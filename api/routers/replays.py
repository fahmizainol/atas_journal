"""Replay attempts — the Simulator's practice record.

Thin by design. The fill engine lives in the browser (frontend/src/lib/
replaySim.ts) and so does every number derived from it, so this router
validates the shape of what it is handed and writes it to disk; it never
recomputes a trade or a statistic. One engine, so a stored attempt can't
disagree with the replay that produced it.

The attempt itself — log, trades, summary, rewinds — lives in data/replays/ and
still does. What changed (2026-08-08) is that its **trades are also mirrored
into journal.db**, under the `replay` account with the sitting tagged
``mode='replay'``, exactly as `/charts/live`'s paper trades are. The reason is
the one journal.live.booking gives: there is no `trades` table, so a row in
`atas_journal` reaches the Trades page, the Calendar, statistics, notes and
setups with nothing else to build, and the mode tag is what keeps
practice out of the real-money numbers.

The mirror is a *projection of the stored attempt*, not a second record: every
autosave replaces the source file's rows, so a rewind that erased a fill erases
its journal row too, and deleting an attempt withdraws both. Nothing here is
recomputed — the row builder is handed the same trades that go to trades.json.

A booking failure never fails the save. The attempt on disk is the thing worth
keeping; the journal copy can be rebuilt from it, and losing a sitting because a
mirror write went wrong would be the wrong trade to make.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from journal import (
    account_campaign as campaign,
    context_store,
    db,
    level_store,
    replay_account,
    replay_whatif as whatif,
    replays,
    review,
    trade_context,
    trades as trmod,
)
from journal.live import booking as bookmod

from .. import deps
from ..scope import Scope, default_scope, resolve_scope

router = APIRouter()

#: **TEMPORARY (2026-08-20, user request): backtest reviews are not mandatory.**
#:
#: Flip back to ``True`` to restore decision V6 of docs/review-revamp-plan.md —
#: a drill that traded owes a watched level, a setup and a discipline call on every trade
#: rep draws, and before it counts as ``reviewed``. While it is ``False`` the
#: panel is still offered at the end of a rep and every answer still stores; it
#: is only the *refusals* that stand down. Drill-only: replay and paper sittings
#: keep their gate either way.
#:
#: Two mirrors to flip with it: ``DRILL_REVIEW_REQUIRED`` in
#: frontend/src/pages/Simulator.tsx (the copy that locks 🎲 before the press),
#: and tools/browser/drillcheck.mjs, which carries the same constant and skips
#: its three lock assertions (saying so) while the gate is off.
DRILL_REVIEW_REQUIRED = False


def _mirror(attempt: dict, trades: list) -> int | None:
    """Mirror an attempt's trades into the journal. None if it could not be.

    Best-effort by contract — see the module docstring. The count is returned so
    the client can see that the two records agree, and a None says the write
    failed rather than that there was nothing to write.
    """
    try:
        conn = deps.get_conn()
        with deps.db_lock():
            return bookmod.book_attempt(conn, attempt=attempt, trades=trades)
    except Exception as e:  # noqa: BLE001 — never fail the save over the mirror
        print(f"[replays] journal mirror failed for {attempt.get('id')}: {e}")
        return None


def _tag_levels(attempt_id: str) -> None:
    """Measure where this sitting's fills landed, once it has finished.

    Deliberately NOT on the mirror: ``book_attempt`` runs on every autosave, and
    this reads ~600 companion instants per fill — a cost that belongs at the end
    of a sitting, not every few seconds during one.

    Reads the journal back rather than the browser's ``trades`` so it scores the
    same logical trades the review and the journal show, in the same shape the
    backfill uses (``demo/level_tag_backfill.py``). Best-effort, like the mirror
    it follows: a day whose ticks aren't cached simply has no measurement, and
    that must never be the reason a finished sitting fails to save.

    Nothing here writes a human answer — that's the whole point of measuring
    separately (see the ``trade_levels`` schema). What these rows became on
    2026-08-20 is the review's *candidate list*: the trader picks which of the
    measured families the trade was actually taken off. Offering the options is
    not answering the question, and ``level_store.candidates_for`` orders them by
    distance rather than by rank so the tagger's opinion is not the first read.
    """
    try:
        src = bookmod.source_file_for_attempt(attempt_id)
        conn = deps.get_conn()
        with deps.db_lock():
            logical = trmod.build_logical_trades(db.load_journal(conn),
                                                 db.load_executions(conn))
            if logical.empty:
                return
            mine = logical[logical["source_file"] == src]
            if mine.empty:
                return
            report = level_store.tag_trades(conn, mine.to_dict("records"))
        print(f"[replays] level tags for {attempt_id}: {report}")
    except Exception as e:  # noqa: BLE001 — never fail a save over a measurement
        print(f"[replays] level tagging failed for {attempt_id}: {e}")


def _measure_context(attempt_id: str) -> None:
    """Measure what price did before each entry and after each exit.

    Alongside ``_tag_levels`` rather than inside it: the two answer different
    questions (*where* a fill landed vs *what price did around it*), they carry
    their own ``method`` stamps and backfills, and either can be recomputed
    without the other. Loading the same rows twice is the price of that
    independence, and a background task is where it belongs.

    Same three disciplines as its neighbour. Reads the journal back, so it
    measures the logical trades the review shows rather than the browser's own.
    Cache-only, so a session that was never bought simply has no context. Best
    -effort, so a measurement is never why a finished sitting fails to save.
    """
    try:
        src = bookmod.source_file_for_attempt(attempt_id)
        conn = deps.get_conn()
        with deps.db_lock():
            logical = trmod.build_logical_trades(db.load_journal(conn),
                                                 db.load_executions(conn))
            if logical.empty:
                return
            mine = logical[logical["source_file"] == src]
            if mine.empty:
                return
            report = context_store.context_trades(conn, mine.to_dict("records"))
        print(f"[replays] trade context for {attempt_id}: {report}")
    except Exception as e:  # noqa: BLE001 — never fail a save over a measurement
        print(f"[replays] context measurement failed for {attempt_id}: {e}")


def _price_whatif(attempt_id: str) -> None:
    """Re-price this sitting under the exit ladder, once it has finished.

    The third measurement to ride on a finish, and the same three disciplines as
    its neighbours: at the end of a sitting rather than every autosave (it reads
    the whole tick tape and runs the engine twenty-six times, about a second);
    cache-only, so a day whose ticks are gone simply has no grid; best-effort, so
    a counterfactual is never why a finished sitting fails to save.

    Unlike the two above it this writes no journal row — it stores a file beside
    the sitting (``replays.write_whatif``). Its reader is the account view, which
    pools thirty of these into one answer and cannot afford to measure them at
    the moment it is asked.
    """
    try:
        out = whatif.price(attempt_id)
        n = len(out.get("rows") or [])
        print(f"[replays] what-if grid for {attempt_id}: "
              f"{'valid, ' + str(n) + ' rows' if out.get('valid') else 'refused'}")
    except Exception as e:  # noqa: BLE001 — never fail a save over a measurement
        print(f"[replays] what-if pricing failed for {attempt_id}: {e}")


class CreateIn(BaseModel):
    symbol: str
    root: str = ""
    date: str
    tz: str
    engine_version: int
    # Enough of the tape to know later whether it is still the same tape:
    # {n, t0, end, rth_open_ms}. Stored opaquely — the rebuild that reads it
    # lives in the client.
    tape: dict = Field(default_factory=dict)
    # The ticket the attempt was traded with, plus speed/start/blind/timeframe.
    prefs: dict = Field(default_factory=dict)
    # Same replay clock as SaveIn.clock_ms — integral today because an attempt
    # is armed on a fresh session, fractional the moment it isn't.
    started_ms: float
    model_id: int | None = None
    # Backtest mode. `drill` opens at the drop rather than the first fill, is
    # invisible to the account, and binds `model_id` session-wide when it books
    # — see docs/backtest-mode-plan.md. Defaulting to `replay` keeps every
    # existing client (and every browser check) sending exactly what it did.
    mode: str = "replay"
    # Which account pays for this sitting. Omitted by a client that still names
    # its account by mode, which every existing one does — the server resolves
    # it below and stamps the answer, so nothing has to be sent before it can be.
    account_id: str | None = None
    drop_ms: float | None = None
    window: dict | None = None


class SaveIn(BaseModel):
    log: dict
    trades: list = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    discarded: list = Field(default_factory=list)
    rewinds: list | None = None
    # The replay clock advances by `elapsed × speed` inside an animation frame,
    # so it arrives fractional. Accept it as sent and let the store round it —
    # sub-millisecond precision is meaningless here, but rejecting it fails the
    # save.
    clock_ms: float | None = None
    status: str | None = None


class PatchIn(BaseModel):
    note: str | None = None
    model_id: int | None = None
    status: str | None = None
    #: Marked to come back to. Since 2026-08-25 this is the whole of what an
    #: unreviewed sitting can owe — see ``replay_account.review_flagged``.
    review_later: bool | None = None


@router.post("/replays")
def create_replay(body: CreateIn) -> dict:
    """Open an attempt. The client calls this on the first fill — a session you
    only watched leaves no record. **In `mode="drill"` it calls it at the drop
    instead**, because a rep you looked at and passed on is the row backtest
    mode exists to produce, and that mode is not gated below.

    **This is the gate.** The browser refuses the gesture before it becomes a
    fill, which is the only way a refusal can be useful; this refuses the
    *record*, which is the only way one can be true. A stale page, a second tab
    and an edited client all get past the first and none of them gets past this.

    409 rather than 403: the sitting is refused because of the state the account
    is in, and that state is one you can leave — by writing up the death, which
    since 2026-08-25 is the only thing left to refuse on. It is never one you
    leave by waiting, which is what the refusal losing its `until` field means
    (`replay_account.refusal`).
    """
    drill = body.mode == "drill"
    # A drill with no model is a drill that measures nothing — the binding is
    # the entire difference between this and a blind replay, and it cannot be
    # added afterwards (`upsert_session` writes it once).
    if drill and body.model_id is None:
        raise HTTPException(400, "a drill must bind a model")
    # The sweep runs for both — it is what deletes the empty rep the last
    # abandoned draw left behind, and a drill session is the one most likely to
    # be leaving them.
    replay_account.sweep_stale_actives()
    # Which account is being opened on — the funded one for a replay, the paper
    # one for a paper sitting, neither for a drill. The gate below is the same
    # gate for both accounts; what differs is only what their own states can be
    # (a paper account is never `blown`, so what it can refuse is the review and
    # nothing else).
    # Named outright when the client knows its account, and resolved from the
    # mode when it does not — which is every client written before there could
    # be more than two. A drill names none and is priced by none.
    acct = None if drill else (
        replay_account.account_by_id(body.account_id) or replay_account.for_mode(body.mode)
    )
    if body.account_id and acct is None:
        raise HTTPException(404, f"no account {body.account_id!r}")
    if acct is not None and acct.archived:
        raise HTTPException(409, {
            "code": "archived",
            "message": (
                f"{acct.label} is archived. Its history is intact and it still prices "
                "the sittings it took, but nothing new opens on it — un-archive it "
                "first, or pick another account."
            ),
        })
    if acct is not None:
        # Gated as of the day being opened: that day has not closed, so its peak
        # is not banked and the floor this sitting is judged against is the one
        # the page has been showing.
        no = replay_account.refusal(account=acct, day=body.date)
        if no:
            raise HTTPException(409, no)
        # Once a death has been paid for, the create is what starts the next
        # account. It happens here rather than on a button because the account
        # is not a thing you open, it is the thing you are trading — the next
        # sitting is the reset. On paper that is the whole lifecycle: the sitting
        # after the blow-up mints the replacement, with nothing owed in between.
        replay_account.ensure_epoch(account=acct, day=body.date)
    # A drill passes both: it is unpriced by design, so there is no gate to
    # clear and no epoch to mint. **It must not call `ensure_epoch` either** —
    # minting epoch 0 off a drill would start the real account's life at a
    # moment nothing was at stake, and every sitting before it would fall
    # outside every epoch for good.
    #
    # It has a gate of its own instead (plan decision V6): the next drill does
    # not open while the last one's trades are untagged. Checked here rather
    # than only in the browser because a reload throws the rep-end panel away
    # — this refusal is the one that survives it, and its message is what the
    # page shows. Drill-on-drill only; the account's world stays separate.
    if drill and DRILL_REVIEW_REQUIRED:
        last = next(
            (r for r in replays.list_attempts(limit=5000) if replays.is_drill(r)),
            None,
        )
        # Only a drill still parked at ``finished`` owes: filing its review is
        # what moves it to ``reviewed`` (and the PATCH gate makes sure the tags
        # were given first), and ``abandoned`` never owes anywhere else either.
        # Without the status check a rep whose status was settled out-of-band
        # would block every future drill forever.
        if (
            last is not None
            and last.get("status") == "finished"
            and replay_account.trade_count(last) > 0
        ):
            owing = _unanswered(_review_rows(last["id"]))
            if owing:
                raise HTTPException(409, {
                    "code": "drill_review",
                    "message": (
                        f"the last rep has {owing} unreviewed trade"
                        f"{'s' if owing != 1 else ''} — every trade owes a watched "
                        "level, a setup and a discipline call before the next rep "
                        "is drawn"
                    ),
                    "attempt_id": last["id"],
                    "until": None,
                })
    try:
        return replays.create(
            symbol=body.symbol,
            root=body.root,
            date=body.date,
            tz=body.tz,
            engine_version=body.engine_version,
            tape=body.tape,
            prefs=body.prefs,
            started_ms=body.started_ms,
            model_id=body.model_id,
            mode=body.mode,
            account_id=acct.id if acct else None,
            drop_ms=body.drop_ms,
            window=body.window,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/replays/journal/backfill")
def backfill_journal() -> dict:
    """Mirror every attempt already on disk into the journal.

    The mirror rides on ``save``, so attempts recorded before it existed have no
    journal rows and would never get any — a practice history that starts the
    day the feature shipped. This walks the store once and books them.

    Safe to run repeatedly: each attempt replaces its own source file's rows, so
    a second run is a no-op rather than a doubling. Declared above the
    ``/replays/{attempt_id}`` routes to keep it that way if one ever takes a
    POST — a path parameter would otherwise swallow `journal`.
    """
    written = attempts = failed = 0
    for row in replays.list_attempts(limit=5000):
        try:
            trades = replays.read(row["id"]).get("trades") or []
        except (ValueError, FileNotFoundError):
            failed += 1
            continue
        n = _mirror(row, trades)
        if n is None:
            failed += 1
            continue
        attempts += 1
        written += n
    return {"attempts": attempts, "trades": written, "failed": failed}


def _account(mode: str, account: str | None = None) -> replay_account.Account:
    """The account being asked about, by id or — failing that — by mode.

    ``account`` is the id and is what a client that knows about the registry
    sends. ``mode`` is the older vocabulary and could only ever name two
    accounts; it is still accepted because every sitting on disk and every
    browser check speaks it, and dropping it would break them for no gain.

    The routes below declare these as bare defaulted scalars rather than
    ``Query(...)``. FastAPI reads a defaulted scalar as a query parameter either
    way, and the bare form is also callable as a plain Python function — which is
    how the tests reach these routes.
    """
    if account:
        found = replay_account.account_by_id(account)
        if found is None:
            raise HTTPException(404, f"no account {account!r}")
        return found
    acct = replay_account.for_mode(mode)
    if acct is None:
        raise HTTPException(400, f"no account prices mode {mode!r}")
    return acct


# --- the registry -----------------------------------------------------------
# Above `/replays/{attempt_id}` like every other named route here — a path
# parameter would otherwise swallow `accounts`.


class AccountIn(BaseModel):
    """A new account, or an edit to one.

    ``template`` is write-once and only on create: it is the rule *shape*, and a
    shape that could be swapped afterwards would re-price every sitting the
    account has already taken under rules they were not traded under. The
    numbers may be edited freely — they are the same rules at a different size.
    """

    label: str
    template: str = replay_account.DEFAULT_TEMPLATE
    numbers: dict | None = None
    archived: bool | None = None


@router.get("/replays/accounts")
def list_accounts(include_archived: bool = True) -> dict:
    """Every account, its record, and the rule shapes one can be made from.

    ``epochs`` is how many lives an account has had; ``record`` is how they
    ended. Both are walked here rather than left to the client, because an
    outcome is a property of the sittings and the switcher holds none of them.

    **The store is scanned once for the whole list.** Every account's tally is a
    walk per life, and going back to disk per window would make opening the
    switcher read a hundred summaries per account — so the sittings are fetched
    and split once (``attempts_by_account``) and each account walks its own.
    """
    state = replay_account.load_state()
    rows = replay_account.attempts_by_account()
    return {
        "accounts": [
            {
                **a.as_dict(),
                "epochs": len(replay_account.epochs_of(state, a)),
                "record": replay_account.record(
                    replay_account.lives(account=a, state=state, rows=rows.get(a.id, []))
                ),
            }
            for a in replay_account.accounts(state, include_archived=include_archived)
        ],
        "templates": [
            {
                "key": t.key, "label": t.label, "trailing": t.trailing,
                "needs_cause": t.needs_cause, "real_time": t.real_time,
                "note": t.note,
                "defaults": t.defaults.as_dict(),
            }
            for t in replay_account.TEMPLATES.values()
        ],
    }


@router.post("/replays/accounts")
def create_account(body: AccountIn) -> dict:
    """Open a new account from a template.

    The id is derived from the label and is permanent: it is stamped on every
    sitting the account prices, so a renamed account keeps its history and a
    re-used id would inherit somebody else's.
    """
    tpl = replay_account.TEMPLATES.get(body.template)
    if tpl is None:
        raise HTTPException(400, f"no template {body.template!r}")
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "an account needs a name")

    state = replay_account.load_state()
    taken = {a.id for a in replay_account.accounts(state)}
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "account"
    acct_id, n = base, 1
    while acct_id in taken:
        n += 1
        acct_id = f"{base}-{n}"

    acct = replay_account.Account(
        id=acct_id, label=label, template=tpl,
        numbers=replay_account.Numbers.from_dict(body.numbers, base=tpl.defaults),
    )
    replay_account.put_account(state, acct, epochs=[])
    replay_account.save_state(state)
    return acct.as_dict()


@router.patch("/replays/accounts/{account_id}")
def patch_account(account_id: str, body: AccountIn) -> dict:
    """Rename, re-number, archive or un-archive an account.

    **The template is not editable and neither are the epochs.** The first would
    re-price sittings under a shape they were never traded under; the second is
    the account's life, and an edit to a daily limit must not be able to reset
    one. ``put_account`` keeps the epochs by construction rather than by this
    route remembering to.
    """
    state = replay_account.load_state()
    current = replay_account.account_by_id(account_id, state)
    if current is None:
        raise HTTPException(404, f"no account {account_id!r}")
    if body.template and body.template != current.template.key:
        raise HTTPException(409, (
            "an account's rule shape is fixed when it opens — its sittings were "
            "traded under it. Make a new account instead; this one keeps its history."
        ))
    updated = replay_account.Account(
        id=current.id,
        label=(body.label or "").strip() or current.label,
        template=current.template,
        numbers=(
            replay_account.Numbers.from_dict(body.numbers, base=current.numbers)
            if body.numbers is not None else current.numbers
        ),
        archived=current.archived if body.archived is None else bool(body.archived),
    )
    replay_account.put_account(state, updated)
    replay_account.save_state(state)
    return updated.as_dict()


@router.delete("/replays/accounts/{account_id}")
def delete_account(account_id: str) -> dict:
    """Remove an account that never traded. Anything else is archived instead.

    A deleted account with sittings on disk would leave them priced by nobody —
    the same hole ``account_id`` immutability exists to close, reopened from the
    other end. The built-ins are never deletable: the paper account is where a
    rehearsal goes when the funded one is dead.
    """
    state = replay_account.load_state()
    acct = replay_account.account_by_id(account_id, state)
    if acct is None:
        raise HTTPException(404, f"no account {account_id!r}")
    if account_id in (replay_account.FUNDED.id, replay_account.PAPER.id):
        raise HTTPException(409, f"{acct.label} is built in — archive it instead")
    if replay_account.epoch_attempts(account=acct):
        raise HTTPException(409, {
            "code": "has_history",
            "message": (
                f"{acct.label} has sittings on it. Deleting it would leave them "
                "priced by no account at all — archive it instead, which hides it "
                "from the switcher and keeps every number it produced."
            ),
        })
    state["accounts"] = [r for r in state["accounts"] if r.get("id") != account_id]
    replay_account.save_state(state)
    return {"deleted": account_id}


@router.get("/replays/accounts/{account_id}/review")
def account_review_state(
    account_id: str, scope: Scope = Depends(resolve_scope)
) -> dict:
    """What this account's reps were graded, and what they still owe.

    **Its own route because its source is its own.** A grade lives in the
    *journal*, not the replay store, and reaching it means building the scoped
    trade frame — 8 seconds against the campaign walk's 0.1. Bolted onto the
    campaign it would have made the page eighty times slower to answer the
    question it exists for; separate, the campaign paints at once and this fills
    in behind it.

    The scope arrives from the dependency and is handed to every rep.
    ``_review_rows`` builds its own, which is right for a gate asking about one
    attempt and would be thirty builds here.

    Best-effort like every other journal read on this path: a process with no DB
    reports nothing owed rather than failing the page.
    """
    acct = replay_account.account_by_id(account_id)
    if acct is None:
        raise HTTPException(404, f"no account {account_id!r}")
    grades: dict[str, int] = {}
    owed = graded = 0
    try:
        for rep in campaign.reps(acct):
            if replay_account.trade_count(rep) == 0:
                continue
            rows = _journal_rows(rep["id"], scope)
            owed += _unanswered(rows)
            for row in rows:
                g = str(row.get("grade") or "").strip().upper()
                if g:
                    grades[g] = grades.get(g, 0) + 1
                    graded += 1
    except Exception as e:  # noqa: BLE001 — the panel degrades, never 500s
        print(f"[replays] account review state unavailable: {e}")
        return {"grades": {}, "owed": None, "graded": 0}
    return {"grades": grades, "owed": owed, "graded": graded}


@router.get("/replays/accounts/{account_id}/campaign")
def account_campaign_report(account_id: str) -> dict:
    """Every bracket in the exit ladder, re-walked over this account.

    The day view asks what a bracket *netted* on one sitting. This asks what it
    would have done to the account — passed, blown, or still alive — because an
    account has a floor and a net does not know about it. See
    ``journal.account_campaign`` for the three things this is not.

    **Reads the cached grids and never prices one.** A page being looked at must
    not spend thirty seconds of tape replay; ``POST .../price`` is how a rep gets
    measured, and ``coverage`` is how this route says which ones have been.
    """
    acct = replay_account.account_by_id(account_id)
    if acct is None:
        raise HTTPException(404, f"no account {account_id!r}")
    state = replay_account.load_state()
    history = replay_account.lives(account=acct, state=state)
    return {
        **campaign.report(acct, state=state),
        # The account's real history, beside the reconstruction of it. They will
        # not agree exactly and the page says why: this walk re-derives the
        # equity path, where the real one reads the browser's live verdict.
        "real": {
            "record": replay_account.record(history),
            "lives": [
                {
                    "index": life.index,
                    "started_at": life.started_at,
                    "outcome": life.outcome,
                    "reps": len(life.walk.counted),
                    "equity": round(life.walk.equity, 2),
                    "floor": round(life.walk.floor, 2),
                    "cause": life.cause,
                    "ended_by": (life.walk.death or life.walk.passed or {}).get("attempt_id"),
                }
                for life in history
            ],
        },
    }


@router.get("/replays/accounts/{account_id}/reps/{attempt_id}")
def account_rep_verdicts(account_id: str, attempt_id: str) -> dict:
    """Which brackets would have survived one rep — the one that ended a life.

    Every row starts from the equity and the floor that rep really opened
    against, so the rows differ only in the bracket.
    """
    acct = replay_account.account_by_id(account_id)
    if acct is None:
        raise HTTPException(404, f"no account {account_id!r}")
    try:
        return campaign.rep_verdicts(acct, attempt_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/replays/accounts/{account_id}/price")
def price_account(account_id: str, background: BackgroundTasks) -> dict:
    """Measure the what-if grid for this account's reps that have none.

    Returns at once and works in the background — a sweep is about a second a
    rep. Poll the campaign route: its ``coverage`` is the progress bar, and it is
    the same number the sweep is closing.
    """
    acct = replay_account.account_by_id(account_id)
    if acct is None:
        raise HTTPException(404, f"no account {account_id!r}")
    rows = campaign.reps(acct)
    todo = [r["id"] for r in rows
            if replay_account.trade_count(r) > 0 and whatif.cached(r["id"], r) is None]
    for attempt_id in todo:
        background.add_task(_price_whatif, attempt_id)
    return {"account": acct.id, "pricing": len(todo)}


@router.get("/replays/account")
def get_account(
    mode: str = "replay", date: str | None = None, account: str | None = None
) -> dict:
    """A replay account — equity, the trailing floor, and what it forbids.

    ``date`` is the **tape day** being replayed, and it is what makes the day
    figures mean anything: the floor does not bank the peak of a day still being
    traded, and the daily loss limit is that day's rather than the evening's.
    A page sitting at a chart passes it; the history page does not, and every
    day closes for it. See ``replay_account.tape_day`` for what went wrong while
    the day was taken from the wall clock.

    Two of them, one per traded mode: ``replay`` is the funded rehearsal,
    ``paper`` the same rules with a free reset (``journal.replay_account``).
    They are separate walks over separate rows and neither can move the other.

    Derived on every call from the attempts on disk (there is no stored
    balance; see ``journal.replay_account``), which is also why the stale sweep
    runs first: an ``active`` attempt nobody has written to in an hour is a
    sitting whose result has not been counted yet, and the account would read
    high by exactly the amount that sitting lost. The sweep is account-blind on
    purpose — it settles whatever has gone stale, and each account then walks
    the part of it that is its own.

    Declared above ``/replays/{attempt_id}`` — a path parameter would otherwise
    swallow `account`, the same trap ``backfill_journal`` documents.
    """
    acct = _account(mode, account)
    replay_account.sweep_stale_actives()
    return replay_account.derive(account=acct, day=date)


class CauseIn(BaseModel):
    cause_of_death: str


@router.post("/replays/account/cause")
def write_cause(body: CauseIn, mode: str = "replay", account: str | None = None) -> dict:
    """Record what killed the account, and start its clock running.

    The 24h timeout runs from the death rather than from this, so writing it up
    promptly costs nothing — but it is not skippable, and that is the point: a
    timeout served in silence teaches the timeout rather than the lesson.

    The paper account asks for none of this (``needs_cause=False``): it is
    already resettable the moment it dies, so nothing here is standing in the
    way and the page never offers the form. Writing one anyway is accepted
    rather than refused — an epitaph on a free account is a note about a rep,
    which is a fine thing to keep and gates nothing.

    Above `/replays/{attempt_id}` like every other named route here.
    """
    acct = _account(mode, account)
    try:
        return replay_account.write_cause(body.cause_of_death, account=acct)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/replays")
def list_replays(
    limit: int = Query(500, ge=1, le=5000),
    status: str | None = Query(None),
    symbol: str | None = Query(None),
    date: str | None = Query(None),
) -> dict:
    return {
        "attempts": replays.list_attempts(
            limit=limit, status=status, symbol=symbol, date=date
        )
    }


@router.get("/replays/drills")
def drill_campaign(model_id: int | None = Query(None)) -> dict:
    """What a backtest-mode campaign has actually measured.

    **Read off the attempts, not off the journal trades**, and that is the whole
    reason this endpoint exists rather than a filter on the trades table. A rep
    where you looked and passed on has no trades at all; an aggregate built from
    trades would therefore see only the reps you traded and report a base rate of
    100% forever — plausibly, which is the worst way for a number to be wrong.

    ``base_rate`` is traded reps over settled reps: how often, drawn blind into
    an hour of RTH, the model was there to be traded. The two histograms are
    what make it readable — hours drawn against hours traded — because a low
    base rate on a window you narrowed to the afternoon says something quite
    different from the same number drawn across the whole session.

    ``rewound_reps`` is the caveat that came with letting the drill transport go
    backwards (plan D8, superseded). A rep that was rewound had a look at tape
    it then traded through, so it is not a cold read — but it is still a rep
    that was drawn, and dropping it would quietly shrink a campaign for a reason
    the card never showed. It is counted in ``reps`` like any other and reported
    beside it, so the base rate can be read with the hindsight in it named.

    Above ``/replays/{attempt_id}``, like every other named route here.
    """
    rows = [r for r in replays.list_attempts(limit=5000) if replays.is_drill(r)]
    if model_id is not None:
        rows = [r for r in rows if r.get("model_id") == model_id]
    # An `active` rep is the one being sat right now. Counting it would have
    # every campaign open claiming a rep it has not finished.
    settled = [r for r in rows if r.get("status") in replay_account.SETTLED]

    drawn: dict[int, int] = {}
    traded_by_hour: dict[int, int] = {}
    traded = net = 0.0
    traded_reps = rewound_reps = 0
    for r in settled:
        if r.get("rewinds"):
            rewound_reps += 1
        n = replay_account.trade_count(r)
        hour = _drop_hour(r)
        if hour is not None:
            drawn[hour] = drawn.get(hour, 0) + 1
            if n:
                traded_by_hour[hour] = traded_by_hour.get(hour, 0) + 1
        if n:
            traded_reps += 1
            traded += n
            net += replay_account.net_usd(r)

    return {
        "model_id": model_id,
        "reps": len(settled),
        "traded_reps": traded_reps,
        "sat_out": len(settled) - traded_reps,
        "rewound_reps": rewound_reps,
        "base_rate": (traded_reps / len(settled)) if settled else None,
        "trades": int(traded),
        "net_usd": round(net, 2),
        "expectancy": round(net / traded, 2) if traded else None,
        "drawn_by_hour": [{"hour": h, "reps": drawn[h]} for h in sorted(drawn)],
        "traded_by_hour": [
            {"hour": h, "reps": traded_by_hour.get(h, 0)} for h in sorted(drawn)
        ],
    }


def _drop_hour(row: dict) -> int | None:
    """The ET hour a rep was thrown in at, from its stored drop.

    ``drop_ms`` is a *display-zone wall clock with the zone dropped* — see the
    boxed rule in journal.replay_account — so the hour is arithmetic on the
    stamp itself and never a timezone conversion. Reading it as UTC and
    converting would shift every rep by four or five hours and still look like
    a plausible histogram.
    """
    drop = row.get("drop_ms")
    if not isinstance(drop, (int, float)):
        return None
    return int((drop % 86_400_000) // 3_600_000)


@router.get("/replays/{attempt_id}/journal")
def replay_journal(attempt_id: str, scope: Scope = Depends(resolve_scope)) -> dict:
    """The journal rows one attempt produced, each with the key notes hang off.

    Backtest mode's review needs this and cannot compute it: a `trade_key` is a
    truncated SHA-1 of the trade's own content (``journal.trades._trade_key``),
    so the browser has no way to name a trade the mirror has just written. It
    asks instead.

    The saved model and rule checks come back on each row too, so the panel
    opens showing what has already been answered rather than blank over trades
    that were reviewed on a previous visit.

    ``include_archived`` is forced: a review is about the trades this sitting
    made, and whether their session is archived has nothing to do with it.
    """
    return {"trades": _journal_rows(attempt_id, scope, with_levels=True)}


def _journal_rows(attempt_id: str, scope: Scope, *, with_levels: bool = False) -> list[dict]:
    """One attempt's journal rows with everything the review reads and echoes.

    Shared by the journal endpoint and both review gates (``reviewed`` on a
    sitting, the next-drill create): the gate has to look at the same rows the
    panel showed, or a review could look complete and still be refused.

    ``setups``/``confluences`` ride along not for display but so the per-trade
    save can echo them — ``PUT /notes`` overwrites the whole row, and a save
    that sent them empty would blank taxonomy written elsewhere. The grade and
    the watched level do *not* need echoing: they are written in place by
    ``db.set_trade_review``, which is what lets every other caller stay ignorant
    of them.

    ``with_levels`` is off for the gate. The gate only asks whether an answer
    exists, and the candidate list is per-trade work a gate should not grow by.
    """
    src = bookmod.source_file_for_attempt(attempt_id)
    df = scope.filtered_all
    rows: list[dict] = []
    if not df.empty:
        mine = df[df["source_file"] == src]
        conn = deps.get_conn()
        with deps.db_lock():
            for r in mine.to_dict("records"):
                key = str(r.get("logical_trade_key") or "")
                if not key:
                    continue
                checks = db.get_rule_checks(conn, key)
                note = db.get_note(conn, key)
                rows.append({
                    "trade_key": key,
                    "direction": r.get("direction"),
                    "entry_ts_local": str(r.get("entry_ts_local") or ""),
                    "net_pnl": float(r.get("net_pnl") or 0.0),
                    "model_id": db.get_trade_model(conn, key),
                    "rules_met": sorted(rid for rid, met in checks.items() if met),
                    "reviewed": bool(checks),
                    "note": note["note"] or "",
                    "tags": json.loads(note["tags_json"] or "[]"),
                    "setups": json.loads(note["setups_json"] or "[]"),
                    "confluences": json.loads(note["confluences_json"] or "[]"),
                    # The review's own answers ride on every row, gate
                    # included: whether they exist is the whole of what the gate
                    # asks.
                    "grade": note["grade"],
                    "setup": note["setup"],
                    "discipline": note["discipline"],
                    "watched_levels": note["watched_levels"],
                    "levels": level_store.candidates_for(conn, key) if with_levels else [],
                    # What the tape says about the moment, so the reviewer stops
                    # transcribing it into tags. Panel-only like the candidates:
                    # the gate doesn't read descriptions.
                    "context_chips": (trade_context.chips(ctx)
                                      if with_levels and
                                      (ctx := db.get_trade_context(conn, key))
                                      else []),
                })
    # Entry order, which is the order they happened and the order the review
    # walks them in. The scope's own sort is by trade number and is not it.
    rows.sort(key=lambda r: r["entry_ts_local"])
    return rows


def _review_rows(attempt_id: str) -> list[dict]:
    """One attempt's journal rows for a *gate*, resolved without a request.

    Best-effort like the mirror it reads from, and for the same reason: the
    mirror is documented as "never fail the save", so a gate cannot owe more
    than the mirror managed to write. When the journal cannot be read at all
    (no DB in a bare test process), there are no rows and nothing to owe.
    """
    try:
        return _journal_rows(attempt_id, default_scope())
    except Exception as e:  # noqa: BLE001 — the gate degrades, never 500s
        print(f"[replays] review gate could not read the journal for {attempt_id}: {e}")
        return []


def _unanswered(rows: list[dict]) -> int:
    """How many of an attempt's trades still owe a review.

    The predicate itself is ``journal.review.trade_answered`` — a watched
    level, a setup and a discipline call — kept out of this router because the
    panel mirrors it and the two must not drift.
    """
    return review.unanswered(rows)


@router.get("/replays/{attempt_id}")
def get_replay(attempt_id: str) -> dict:
    try:
        return replays.read(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e


class TrailIn(BaseModel):
    dist: int = Field(ge=1, le=4000)
    step: int = Field(0, ge=0, le=4000)
    beOnly: bool = False


class ScenarioIn(BaseModel):
    """One what-if row's exits, in ticks from the entry.

    ``trail`` is three-valued and the three values are all meaningful: omitted
    keeps whatever trail was on the ticket, ``null`` runs without one, and an
    object replaces it.

    ``flip`` is the one field that is not an exit: it takes the other side of every
    entry at the same moment, mirroring the placed legs across the fill.
    """

    stop: int | None = Field(None, ge=1, le=4000)
    target: int | None = Field(None, ge=1, le=4000)
    targetR: float | None = Field(None, gt=0, le=20)
    trail: TrailIn | Literal["as-placed"] | None = "as-placed"
    flip: bool = False

    @model_validator(mode="after")
    def _one_target(self):
        if self.target is not None and self.targetR is not None:
            raise ValueError("Give a target in ticks or in R, not both.")
        return self


class WhatIfIn(BaseModel):
    #: Rows the user built. The preset ladder is server-owned and always priced.
    custom: list[ScenarioIn] = Field(default_factory=list, max_length=20)


@router.post("/replays/{attempt_id}/whatif")
def replay_whatif(attempt_id: str, body: WhatIfIn) -> dict:
    """This sitting's fills re-priced under other exits — the one exception to
    the no-recompute rule this module opens with.

    It recomputes counterfactuals only, never the record: the stored trades are
    read, not written, and the first thing it does is reproduce them exactly. If
    no fill model does, it answers ``valid: false`` with no rows rather than
    show numbers that cannot be compared against the sitting they came from.

    Entry times are held fixed, so a row is *the same button presses under a
    different bracket*, not a strategy result — those clicks were conditioned on
    what actually happened next. The reversed rows hold the times and take the
    other side, which is a readout on the direction read and less of a base rate
    still.
    """
    custom = [{"spec": s.model_dump()} for s in body.custom]
    try:
        return whatif.grid(attempt_id, custom)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e


@router.put("/replays/{attempt_id}")
def save_replay(attempt_id: str, body: SaveIn, background: BackgroundTasks) -> dict:
    """Autosave. Debounced by the client, so this lands every few seconds while
    a replay is being traded rather than once at the end.

    The journal mirror rides on this rather than on `finish`, for the same
    reason the autosave exists at all: a sitting that ends by closing the tab
    never finishes, and it should still be in the journal.
    """
    try:
        attempt = replays.save(
            attempt_id,
            log=body.log,
            trades=body.trades,
            summary=body.summary,
            discarded=body.discarded,
            rewinds=body.rewinds,
            clock_ms=body.clock_ms,
            status=body.status,
        )
        if body.status == "finished":
            attempt = _raise_flags(attempt, body.trades)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e
    journaled = _mirror(attempt, body.trades)
    # After the mirror, so the logical trades they read back are this sitting's;
    # in the background, so the measurement never sits between the trader and a
    # saved sitting. Nothing the review gates on is written here — the review's
    # three answers are typed by a person, so there is nothing to race.
    if body.status == "finished":
        background.add_task(_tag_levels, attempt_id)
        background.add_task(_measure_context, attempt_id)
        background.add_task(_price_whatif, attempt_id)
    return {**attempt, "journaled": journaled}


def _raise_flags(attempt: dict, trades: list) -> dict:
    """Ask the account what this sitting has to answer for, and store the answer-list.

    Server-side because the browser is the thing being reviewed: a flag the page
    could decline to send is not a gate. It is still not a second fill engine —
    ``flags_for`` reads the trades the browser just stored and never re-derives
    one.

    **A zero-trade sitting passes straight through**, flagged or not. The review
    is the trades: a rep where you looked and passed owes nothing, and a ceremony
    you always have to click through is one you stop reading. A flag on such a
    sitting can only be a rewind, which is a fact recorded about it and not a
    question put to you — see ``docs/trade-grading-plan.md`` G6, where the
    leak/justified verdict was retired.

    Anything that traded parks at ``finished`` and that is now where it may stay.
    Between 2026-08-17 and 2026-08-25 ``finished`` was a debt — the account
    refused the next sitting until every booked trade carried a grade, a watched
    level and a tag. It is a terminal state again: reviewing is a choice, and a
    sitting you want to come back to is marked ``review_later`` rather than left
    blocking the ones after it.
    """
    flags = replay_account.flags_for(attempt, trades)
    booked = [t for t in trades if isinstance(t, dict)]
    # Through `patch`, not a second `save`: the trades have just been written
    # and must not be written again — `save` takes `discarded` as an argument
    # and would drop the rewind record on a call that omitted it.
    return replays.patch(
        attempt["id"],
        flags=flags,
        **({} if booked else {"status": "reviewed"}),
    )


@router.patch("/replays/{attempt_id}")
def patch_replay(attempt_id: str, body: PatchIn) -> dict:
    fields = body.model_dump(exclude_unset=True)

    # `reviewed` means reviewed. It is only accepted when every journaled trade
    # carries its three answers — read off stored rows rather than taken from
    # the request, for the same reason the flags are computed server-side: the
    # page being reviewed does not get to say how much reviewing it needs.
    #
    # This survived the 2026-08-25 change that made reviewing optional, and it
    # is not the same gate. That one refused the *next sitting* until this one
    # was answered, which is what made the review a toll. This one refuses only
    # the claim: a sitting you did not want to review is left `finished`, or
    # marked `review_later` and come back to, and neither needs to lie about
    # having been reviewed to get there.
    #
    # The flags used to be the other half of this, each owing a leak/justified
    # verdict. That went with the grading revamp (2026-08-20,
    # docs/trade-grading-plan.md G6): a flag asks whether an account rule was
    # tripped, which is a different question from how the trade was, and the
    # panel stopped offering the buttons. Only the server half was left behind —
    # which made every flagged sitting unfileable, and so blocked its account.
    if fields.get("status") == "reviewed":
        try:
            stored = replays.read(attempt_id)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, f"No attempt {attempt_id}") from e
        # The gate stands down for drills while the switch is off, so a
        # partly-answered rep can still be filed and put down.
        if DRILL_REVIEW_REQUIRED or not replays.is_drill(stored):
            owing = _unanswered(_review_rows(attempt_id))
            if owing:
                raise HTTPException(
                    409,
                    f"{owing} trade{'s' if owing != 1 else ''} still unreviewed — every "
                    "trade needs a watched level, a setup and a discipline call "
                    "before this sitting counts as reviewed",
                )

    try:
        attempt = replays.patch(attempt_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e

    # The note and the model are the two fields a sitting carries in both
    # records. Pushed across so the Trades page shows what the history page
    # shows; the session row may not exist yet (an attempt can be annotated
    # before it has journalled a trade), and update_session is a no-op then.
    if "note" in fields or "model_id" in fields:
        try:
            conn = deps.get_conn()
            with deps.db_lock():
                db.update_session(
                    conn,
                    bookmod.source_file_for_attempt(attempt_id),
                    note=attempt.get("note"),
                    model_id=attempt.get("model_id"),
                )
        except Exception as e:  # noqa: BLE001 — the attempt is already patched
            print(f"[replays] session patch failed for {attempt_id}: {e}")
    return attempt


@router.delete("/replays/{attempt_id}")
def delete_replay(attempt_id: str) -> dict:
    try:
        replays.delete(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # Withdraw the mirror too. "Delete the folder and it is gone" is the promise
    # journal.replays makes, and a journal row that outlived its attempt would
    # be a trade with no record of how it was taken.
    try:
        conn = deps.get_conn()
        with deps.db_lock():
            bookmod.unbook_attempt(conn, attempt_id)
    except Exception as e:  # noqa: BLE001
        print(f"[replays] journal withdraw failed for {attempt_id}: {e}")
    return {"ok": True}
