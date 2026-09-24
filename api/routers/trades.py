"""Trades table + single-trade detail (with its saved note/model/rule checks)."""

from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from journal import db, level_store, level_tag, review, tick_bars

from .. import deps
from ..scope import Scope, resolve_scope, text_cell
from ..serialize import records, sanitize
from ..session_chart import _epoch_local

router = APIRouter()

TRADE_COLS = [
    "trade_no", "trade_key", "logical_trade_key", "instrument", "direction",
    "max_contracts", "entry_ts_local", "exit_ts_local", "entry_ts_utc", "exit_ts_utc",
    "duration_s", "avg_entry", "avg_exit", "net_pnl", "comment", "source_file",
    "model_id",
    # The owning session's mode. Backtest trades get a chart-first detail layout
    # with no recording, so the trade must carry its own mode — keying off the
    # FilterBar would misrender them whenever the mode filter is "all".
    "session_mode",
    # False when that mode was defaulted rather than read — an imported broker
    # export with no session row. The detail page asks for a thesis only where
    # one could have been recorded, and this is what tells the two apart.
    "session_known",
]


@router.get("/trades")
def list_trades(scope: Scope = Depends(resolve_scope)) -> list[dict]:
    rows = records(scope.filtered, TRADE_COLS)
    # Attach each trade's review answers (and the archived era's setup badges)
    # using the notes frame loaded by resolve_scope; building the lookup only
    # over the in-scope keys keeps the JSON parsing proportional to result
    # size, not whole-table size. Notes are keyed by the logical trade, so the
    # answers show in the ATAS view too.
    #
    # The review rides the LIST because the top-down surfaces (the Review
    # page's ledger/matrix/facets, the Trades table's cut) group and count
    # client-side over the rows they already hold — a per-axis endpoint would
    # be a second copy of this join. ``grade_blind`` says whether the grade was
    # answered at the recall front; a False grade predates blind capture and
    # restates the outcome, so any aggregate over grades must split on it.
    notes_df = scope.notes
    keys = {r["logical_trade_key"] for r in rows}
    note_map: dict[str, dict] = {}
    if not notes_df.empty and keys:
        # text_cell, not truthiness or plain passthrough: read_sql turns an
        # all-NULL TEXT column into float64 NaN, and NaN is truthy AND not
        # valid JSON.
        sub = notes_df[notes_df["trade_key"].isin(keys)]
        for _, r in sub.iterrows():
            picks = [str(p) for p in json.loads(text_cell(r["watched_levels_json"]) or "[]")]
            setup, discipline = text_cell(r["setup"]), text_cell(r["discipline"])
            note_map[r["trade_key"]] = {
                "setups": json.loads(text_cell(r["setups_json"]) or "[]"),
                "setup": setup,
                "discipline": discipline,
                "grade": text_cell(r["grade"]),
                "grade_blind": bool(text_cell(r["graded_at"])),
                # Labelled here for the reason the detail's levels are: the
                # client used to keep its own copy of the name map.
                "watched_labels": [
                    "no level" if p == review.NO_LEVEL
                    else level_tag.label_for(level_tag.FAMILY_OF.get(p, ""), p)
                    for p in picks
                ],
                "reviewed": review.trade_answered({
                    "watched_levels": picks,
                    "setup": setup,
                    "discipline": discipline,
                }),
                "_review_row": {
                    "watched_levels": picks, "setup": setup,
                    "discipline": discipline, "grade": text_cell(r["grade"]),
                },
            }
    for r in rows:
        note = note_map.get(r["logical_trade_key"])
        r.update(note or {
            "setups": [], "setup": None, "discipline": None, "grade": None,
            "grade_blind": False, "watched_labels": [], "reviewed": False,
        })
        # reviewed | owed | history — the debt taxonomy. entry_ts_local is an
        # ISO string here (records() serialized it), so [:10] is the day.
        r["review_state"] = review.state_of(
            (note or {}).get("_review_row") or {},
            str(r.get("entry_ts_local") or "")[:10],
        )
        r.pop("_review_row", None)
    return rows


@router.get("/trades/context")
def trade_context_batch(keys: str = Query(...)) -> dict:
    """Many trades' windows at once, ``{trade_key: row | null}``.

    Declared above ``/trades/{trade_no}`` so the literal path wins the match —
    that route takes an int, so a fall-through would 422 rather than try here.

    For the review panel, which walks a sitting's trades and would otherwise ask
    once per card. **Scope-free on purpose**: a ``trade_key`` is a content hash
    and identifies one trade globally, so there is nothing for a filter to
    disambiguate — and the panel that needs this runs inside /charts, where the
    journal's date/mode filters have no reason to agree with the sitting being
    reviewed. A key with no row comes back null rather than missing, so the
    caller can tell "never measured" from "not asked for".
    """
    wanted = [k for k in (keys or "").split(",") if k]
    if not wanted:
        return {}
    conn = deps.get_conn()
    with deps.db_lock():
        return {k: db.get_trade_context(conn, k) for k in wanted}


@router.get("/trades/{trade_no}")
def trade_detail(trade_no: int, scope: Scope = Depends(resolve_scope)) -> dict:
    # filtered_all so a trade from an archived session (Archive toggle off) can
    # still be reached by direct link from the day explorer or a deep link.
    tf = scope.filtered_all
    match = tf[tf["trade_no"] == trade_no] if not tf.empty else tf
    if match.empty:
        raise HTTPException(404, f"Trade #{trade_no} not in scope")
    row = match.iloc[0]
    trade = sanitize(row[[c for c in TRADE_COLS if c in row.index]].to_dict())
    key = row["logical_trade_key"]

    conn = deps.get_conn()
    with deps.db_lock():
        note = db.get_note(conn, key)
        model_id = db.get_trade_model(conn, key)
        checks = db.get_rule_checks(conn, key)
        levels = [
            # Labelled here rather than in the client, which used to keep its
            # own copy of the name map and had to be kept equal by hand.
            {**r, "label": level_tag.label_for(r["family"], r.get("member"))}
            for r in db.get_trade_levels(conn, key)
        ]
        # The same offer the replay/drill review and Recall's back get — the
        # ordering rule lives in level_store so the surfaces cannot drift. Here
        # because the detail's journal form asks the level question too, now
        # that the trade's replay (chart and all) sits right above it.
        level_candidates = level_store.candidates_for(conn, key)
        context = db.get_trade_context(conn, key)
    return {
        "trade": trade,
        # What price did before the entry and after the exit, in points signed to
        # the trade's direction. None means never measured (no cached tape for
        # that session) rather than "nothing happened" — and `*_avail_s` is how
        # much tape each window got, without which a zero is unreadable.
        "context": context,
        "note": note["note"],
        "tags": json.loads(note["tags_json"] or "[]"),
        "setups": json.loads(note["setups_json"] or "[]"),
        "confluences": json.loads(note["confluences_json"] or "[]"),
        # Measured, not claimed — every family, with the rank that says whether
        # being near it meant anything. Sent whole so the client owns the
        # threshold; an empty list means never computed, not "near nothing".
        "levels": levels,
        "level_candidates": level_candidates,
        "levels_at_rank": level_tag.AT_LEVEL_RANK,
        # The review: the trader's own grade and the levels they say the trade
        # was taken off. Beside ``levels`` on purpose — measured and claimed, the
        # pair being the only reason either is worth keeping.
        "grade": note["grade"],
        "setup": note["setup"],
        "discipline": note["discipline"],
        "watched_levels": note["watched_levels"],
        # The trade's own binding, not the effective model — the form edits this
        # one; a backtest session's model shows through ``trade.model_id``.
        "model_id": model_id,
        "rules_met": sorted(rid for rid, met in checks.items() if met),
    }


#: How much of each window the context strip draws. The stored numbers stop at
#: `trade_context.LONG_MIN`; the picture is allowed to be wider than the
#: measurement, because an eye reading an approach wants a little room around it.
STRIP_PAD_MIN = 30


@router.get("/trades/key/{trade_key}/context")
def trade_context_strip(trade_key: str, bars: bool = Query(True),
                        scope: Scope = Depends(resolve_scope)) -> dict:
    """One trade's windows: the stored numbers, plus the bars to see them on.

    Keyed by ``trade_key`` rather than ``trade_no`` because the callers are given
    keys — the review panel by ``/replays/{id}/journal``, the batch above by its
    own reads — and have no trade number to ask with.

    The numbers are read scope-free, for the reason the batch endpoint gives. The
    bars need the journal row (instrument, fills) and so do go through the scope;
    a trade the current filters exclude gets its numbers and an empty bar list
    rather than a 404, because the measurement is the part that was asked for.

    Bars are re-derived from the tape on every call rather than stored beside the
    numbers. That is this feature's whole storage policy in one place: the tape
    is deterministic, so a picture is always one slice away, and a stored copy
    would be a cache that can go stale against the ticks it came from.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        context = db.get_trade_context(conn, trade_key)

    out: dict = {"trade_key": trade_key, "context": context,
                 "direction": None, "entry": None, "exit": None, "bars": []}
    tf = scope.filtered_all
    match = tf[tf["logical_trade_key"] == trade_key] if not tf.empty else tf
    if match.empty or not bars:
        return out
    row = match.iloc[0]

    pad = pd.Timedelta(minutes=STRIP_PAD_MIN)
    entry, exit_ = pd.Timestamp(row["entry_ts_utc"]), pd.Timestamp(row["exit_ts_utc"])
    # The contract the MEASUREMENT resolved, not the journal's instrument label.
    # They differ for every micro trade: the row says `MNQU6@CME` because that is
    # the product traded, while the tape is cached under the mini's root — so
    # asking for the label's own ticks finds nothing and draws an empty window
    # beside a full set of numbers. `symbol` is stored for exactly this.
    sym = (context or {}).get("symbol") or row["instrument"]
    drawn = tick_bars.get_bars(sym, entry - pad, exit_ + pad)
    tz = scope.tz
    out["direction"] = row["direction"]
    # Marked in the same clock the bars are drawn in, so the client places them
    # without re-deriving an offset it could get wrong.
    out["entry"] = {"time": _epoch_local(entry, tz), "price": float(row["avg_entry"])}
    out["exit"] = {"time": _epoch_local(exit_, tz), "price": float(row["avg_exit"])}
    out["bars"] = [] if drawn is None else [
        {"time": int(t), "open": float(o), "high": float(h),
         "low": float(lo), "close": float(c), "volume": float(v)}
        for t, o, h, lo, c, v in zip(
            _epoch_local(drawn["ts_utc"], tz), drawn["open"],
            drawn["high"], drawn["low"], drawn["close"], drawn["volume"])
    ]
    return out
