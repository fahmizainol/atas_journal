"""Lab → Recall: spaced repetition over your own reviewed trades.

A card is one journaled trade's session tape, playing **a minute around the
fill** — half before, half after, with nothing drawn to say where in there it
was. You read it, guess what price does next, flip, and rate yourself. The
schedule is SM-2 (``journal.srs``); the rating is self-assigned because there is
no objective answer to grade against, which is exactly how an Anki card works.
Design: docs/trade-grading-plan.md, decisions G7-G8.

**The front's payload carries no answer.** Direction, size, exit and PnL are
served by ``/recall/back/{key}`` and by nothing else. This is the whole blindness
mechanism: masking a field in the UI leaves it one devtools tab away, and the one
reader who can most cheaply cheat is the person the deck is for.

**"When" is not one of the hidden fields, by construction.** The front's window
is pinned to the fill — the entry sits a fixed half-minute inside the right edge
— so ``cut_ms`` is a stamp the chart's own geometry already gives away, and
withholding it would hide nothing. Until 2026-08-22 the stop was jittered
precisely so the timing stayed blind; that was traded away because a stop minutes
off the fill asks about a chart the setup has not formed on. What remains blind
is what the trade *was*, which is the part a rep can actually be wrong about.

The date and the contract *are* on the front, because the tape cannot be fetched
without them — they are masked in the UI instead (``hideDates``, root-only
symbol). That is a deliberately weaker guarantee for the two fields that have no
alternative, and the reason the answer fields do not get the same treatment.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from journal import config, db, levels as levels_mod, replay_whatif, review, srs
from journal import level_store, level_tag
from journal.live import booking

from .. import deps
from ..scope import Scope, resolve_scope

router = APIRouter()

def _local_ms(stamp: str) -> int | None:
    """A journal local stamp as epoch ms **with the zone offset dropped**.

    The tape clock is display-zone wall time, so the offset is discarded rather
    than applied — the same rule ``DayReplayer.localMs`` follows in the browser,
    and the reason a card built here lines up with the chart that draws it.

    Dropping the offset means reading the wall clock *as if it were UTC*, which
    is what ``Date.parse(s + "Z")`` does on the other side. ``.timestamp()`` on a
    naive datetime does something else entirely — it reads the stamp in the
    **server's** zone — and on any machine that is not itself at UTC that silently
    slides every card by the local offset: the cut lands hours from the trade, in
    a stretch of tape the entry is nowhere near, and the flip jumps to the entry
    the browser drew at the *right* clock. Nothing here may use it.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp)[:19])
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _eligible(scope: Scope) -> pd.DataFrame:
    df = scope.filtered_all
    if df.empty or "logical_trade_key" not in df:
        return df
    return df.drop_duplicates(subset=["logical_trade_key"], keep="first")


def _card_front(row: dict, conn, cards: dict[str, dict], reviews: dict[str, dict]) -> dict | None:
    """One deck entry, or None when this trade cannot be a card.

    Two things disqualify a trade: it has not been reviewed (the deck is *over*
    reviews — a card whose back has nothing to say is a chart with a price on
    it), and it has no measured context row, which is where the tape's contract
    comes from. That contract must never be taken from ``instrument``: ATAS
    stamps every 2026 row with one label across the roll, so the journal's own
    symbol will happily hand back a day the trade never traded.

    ``reviews`` is the whole review table, read once by the caller. The review
    test comes first on purpose: it is the one that rejects almost everything,
    and it is now free, so the per-trade context query below runs only for the
    handful of trades that got this far.
    """
    key = str(row.get("logical_trade_key") or "")
    if not key:
        return None

    if not review.trade_answered(reviews.get(key) or {}):
        return None

    ctx = db.get_trade_context(conn, key)
    symbol = str((ctx or {}).get("symbol") or "")
    if not symbol:
        return None

    entry_ms = _local_ms(str(row.get("entry_ts_local") or ""))
    if entry_ms is None:
        return None
    day = levels_mod.rth_date_for(row.get("entry_ts_utc"))

    card = cards.get(key)
    return {
        "trade_key": key,
        "symbol": symbol,
        "root": config.root_symbol(symbol),
        "date": day.isoformat(),
        # What the front's window is centred on: the fill itself, derived rather
        # than stored. The client puts the window around it and clamps that into
        # the session it actually loaded.
        "cut_ms": int(entry_ms),
        # Whether this trade still owes its grade. The front is where grading
        # happens (docs/trade-grading-plan.md G3 addendum, 2026-08-31): asked at
        # the fill-freeze, before the back is fetched, it is the one review
        # answer given without the outcome on screen. Safe on the front — it
        # says nothing about what the trade was, only that a question is due.
        "needs_grade": not str((reviews.get(key) or {}).get("grade") or "").strip(),
        # Schedule only — nothing here says what the trade was.
        "reps": card["reps"] if card else 0,
        "lapses": card["lapses"] if card else 0,
        "due": card["due"] if card else None,
    }


@router.get("/recall/deck")
def deck(scope: Scope = Depends(resolve_scope)) -> dict:
    """Every card due today, oldest due first, then the ones never seen.

    Never grades and never opens a tape: this is a page-load read, and the two
    expensive things a card needs (its tick session, its back) are fetched when
    a card is actually shown. The one write it can make is the stale-cut repair
    in :func:`_card_front`, which corrects a window rather than a schedule.
    """
    today = date.today()
    conn = deps.get_conn()
    df = _eligible(scope)
    rows = df.to_dict("records") if not df.empty else []
    fronts: list[dict] = []
    with deps.db_lock():
        cards = db.all_recall_cards(conn)
        reviews = db.all_trade_reviews(conn)
        # Deck-wide like the two above, and read in the same held lock so the
        # slot cannot describe a rating that landed halfway through this build.
        undo = db.pending_recall_undo(conn)
        for r in rows:
            front = _card_front(r, conn, cards, reviews)
            if front is not None:
                fronts.append(front)

    due = [c for c in fronts if srs.is_due(cards.get(c["trade_key"]), today)]
    # Seen cards first, most overdue first — one you are about to forget is worth
    # more than one you have never met. New cards follow, most recent first: the
    # trade you took yesterday is the one still worth arguing with.
    seen = sorted((c for c in due if c["due"] is not None), key=lambda c: c["due"])
    fresh = sorted((c for c in due if c["due"] is None),
                   key=lambda c: c["date"], reverse=True)
    ordered = seen + fresh

    upcoming = sorted(
        (c["due"] for c in fronts if c["due"] and c["due"] > today.isoformat())
    )
    return {
        "cards": ordered,
        "total": len(fronts),
        "next_due": upcoming[0] if upcoming else None,
        # The last rating, still revertible. Card and rating only — this is the
        # front's payload, so it carries no guess text and nothing about what any
        # trade was; a rating is the reader's own word about themselves.
        "undo": undo,
    }


@router.get("/recall/back/{trade_key}")
def back(trade_key: str, scope: Scope = Depends(resolve_scope)) -> dict:
    """The answer: what was actually traded here, and what was said about it.

    Its own route rather than a field on the deck, so that the front cannot leak
    it. Fetched on flip.

    It also carries what the card needs to *correct* that review in place — the
    level candidates and the fields a ``PUT /notes`` save must echo. There is no
    write here: the card saves through ``PUT /notes/{trade_key}`` like every
    other review surface, because a second write path to the same three columns
    is how two surfaces start disagreeing about what a review is.
    """
    df = _eligible(scope)
    match = df[df["logical_trade_key"] == trade_key] if not df.empty else df
    if match.empty:
        raise HTTPException(404, f"No trade {trade_key} in scope")
    row = match.iloc[0].to_dict()

    conn = deps.get_conn()
    with deps.db_lock():
        note = db.get_note(conn, trade_key)
        reps = db.recall_reps_for(conn, trade_key)
        # Everything the card's own review editor needs, which is the picker's
        # options plus the fields ``PUT /notes`` overwrites wholesale. The
        # echo set (setups, confluences, model, rule checks) is not shown
        # anywhere on the card: it rides along so a review corrected here cannot
        # blank taxonomy written on the trade page — the same obligation
        # ``_journal_rows`` carries for the replay review, for the same reason.
        checks = db.get_rule_checks(conn, trade_key)
        model_id = db.get_trade_model(conn, trade_key)
        candidates = level_store.candidates_for(conn, trade_key)
        # What was said about the whole sitting this trade was taken in — the
        # review's session box (docs/review-revamp-plan.md V15). It belongs on
        # the back and only the back: it is written with the day finished, so it
        # is the same hindsight class as the grade, and a word of it on the front
        # would be describing the tape the card is asking about.
        sitting = db.session_note(conn, str(row.get("source_file") or ""))

    # How the position was opened and what it was bracketed with — read off the
    # sitting on disk, because the mirror keeps neither (`live.booking`
    # translates a trade and drops the order behind it). None for a trade that
    # was never a sitting, and for one whose attempt has since been deleted.
    #
    # Back-only for the same reason everything else here is: an order type says
    # whether the entry was waited for, and a stop says how much room the trade
    # was given — both of which are the read the card is asking you to make.
    attempt = booking.attempt_id_for_source(str(row.get("source_file") or ""))
    order = replay_whatif.order_behind_fill(
        attempt,
        side="long" if str(row.get("direction")) == "Long" else "short",
        size=float(row.get("max_contracts") or 0),
        entry_price=float(row.get("avg_entry") or 0.0),
        exit_price=float(row.get("avg_exit") or 0.0),
        duration_s=float(row.get("duration_s") or 0.0) or None,
    ) if attempt else None

    # The answer names the levels themselves, so no join is needed to say which:
    # `label_for` reads each member directly.
    picks = note["watched_levels"]
    return {
        "trade_key": trade_key,
        "direction": row.get("direction"),
        "max_contracts": int(row.get("max_contracts") or 0),
        "avg_entry": float(row.get("avg_entry") or 0.0),
        "avg_exit": float(row.get("avg_exit") or 0.0),
        "net_pnl": float(row.get("net_pnl") or 0.0),
        "entry_ts_local": str(row.get("entry_ts_local") or ""),
        "exit_ts_local": str(row.get("exit_ts_local") or ""),
        "trade_no": int(row.get("trade_no") or 0),
        # The order behind the fill: `open_type`/`rest_price`/`rest_ms` are how
        # you got in, `stop`/`target` the bracket the position opened with, and
        # `moved`/`trail_pts` the two reasons an exit can land somewhere that
        # bracket doesn't explain. Null when the trade came from anywhere but a
        # sitting on disk.
        "order": order,
        # The review, which is the half of the back worth reading twice.
        "grade": note["grade"],
        "setup": note["setup"],
        "discipline": note["discipline"],
        "watched_levels": picks,
        "watched_labels": [
            "no level" if p == review.NO_LEVEL
            else level_tag.label_for(level_tag.FAMILY_OF.get(p, ""), p)
            for p in picks
        ],
        "tags": json.loads(note["tags_json"] or "[]"),
        "note": note["note"] or "",
        # The picker's options: every level measured at this entry, nearest
        # first. Back-only like the rest — a list of the levels around the fill
        # is a description of the chart the front is asking you to read.
        "levels": candidates,
        # Echoed on save, never displayed. See the read above.
        "setups": json.loads(note["setups_json"] or "[]"),
        "confluences": json.loads(note["confluences_json"] or "[]"),
        "model_id": model_id,
        "rules_met": sorted(rid for rid, met in checks.items() if met),
        # The sitting's own words, distinct from the trade's: one is about this
        # fill, the other about the day it happened in.
        "session_note": sitting,
        "reps": reps,
    }


class RateIn(BaseModel):
    """One self-rating, and optionally the read that preceded it.

    ``guess`` is stored and never scored — see the ``recall_reps`` schema. It is
    here so the read is committed to before the flip, which is the only thing
    stopping "I knew that" from being decided afterwards.

    ``grade`` rides the same commitment: picked at the front while the outcome
    is still unfetched, locked by the flip, delivered with the rating. The
    server only accepts it for a trade that has no grade yet — first answer
    wins, and the first answer is the blind one. Client-enforced blindness,
    like the guess: the deck is for the person reading it.
    """
    trade_key: str
    rating: int
    guess: str | None = None
    grade: str | None = None


@router.post("/recall/rate")
def rate(body: RateIn, scope: Scope = Depends(resolve_scope)) -> dict:
    if body.rating not in srs.RATINGS:
        raise HTTPException(
            422, f"{body.rating!r} is not a rating "
                 f"({', '.join(f'{k}={v}' for k, v in srs.RATING_LABEL.items())})")

    df = _eligible(scope)
    match = df[df["logical_trade_key"] == body.trade_key] if not df.empty else df
    if match.empty:
        raise HTTPException(404, f"No trade {body.trade_key} in scope")
    row = match.iloc[0].to_dict()

    if body.grade is not None and body.grade not in review.GRADES:
        raise HTTPException(422, f"{body.grade!r} is not a grade")

    today = date.today()
    conn = deps.get_conn()
    with deps.db_lock():
        # First answer wins: the grade the front collected was given blind, and
        # a re-grade from a later rep would be given by someone who has seen
        # this card's back. Refused loudly rather than dropped — a 422 mid-rate
        # means the client showed a picker it should not have.
        wrote_grade = False
        if body.grade is not None:
            if str(db.get_note(conn, body.trade_key)["grade"] or "").strip():
                raise HTTPException(
                    422, f"trade {body.trade_key} is already graded — "
                         "the blind answer stands")
            wrote_grade = True
        card = db.get_recall_card(conn, body.trade_key)
        if card is None:
            card = srs.new_card(today)
        # Read, schedule and write the deck-wide state inside one held lock: the
        # Arena's collection is read-modify-write, so two ratings racing here
        # would have the later write silently discard the earlier one's learning.
        collection = db.get_recall_collection(conn)
        nxt, collection = srs.advance(card, body.rating, today, collection)
        rep_id = db.add_recall_rep(conn, body.trade_key, body.rating, body.guess)
        # Between the rep and the overwrites, so the snapshot is of the rows
        # this rating is about to replace — the grade included. Same held lock
        # as the writes it guards — a snapshot taken outside it could catch
        # another rating's half-finished state and "restore" the deck to
        # something that never was.
        db.stage_recall_undo(conn, body.trade_key, rep_id, wrote_grade=wrote_grade)
        if wrote_grade:
            db.set_trade_review(conn, body.trade_key, body.grade, None, blind=True)
        db.save_recall_card(conn, body.trade_key, nxt)
        if collection is not None:
            db.save_recall_collection(conn, collection)
    # Without the scheduler's item state: the page shows a due date and a rep
    # count, and shipping ~900 bytes of model internals to a browser that has no
    # use for them only invites something to start reading them.
    return {"ok": True, "card": {k: v for k, v in nxt.items() if k != "sm20_state"}}


@router.post("/recall/undo")
def undo() -> dict:
    """Take back the last rating: the rep, the schedule and the Arena's state.

    Only the last one, and only once — see the ``recall_undo`` schema for why an
    undo stack would be a worse feature rather than a deeper one.

    It is a full revert, not a re-rating: the card comes back with the due date,
    interval, ease, rep count and lapse count it had, so a card whose first ever
    showing is undone goes back to being unseen. The guess comes back too, since
    the reason to undo is usually that the wrong button was hit on a read you had
    already typed out.

    Deliberately not scoped: a rating is a fact about the reader rather than
    about a filtered set of trades, and the row being restored was written by
    this same deck. Requiring the card to still be in scope would make an undo
    fail because a filter moved between the two clicks.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        undone = db.undo_last_recall_rating(conn)
    if undone is None:
        raise HTTPException(409, "Nothing to undo — the last rating is already back")
    return {"ok": True, **undone}
