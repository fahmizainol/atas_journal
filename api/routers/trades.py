"""Trades table + single-trade detail (with its saved note/model/rule checks)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from journal import db

from .. import deps
from ..scope import Scope, resolve_scope
from ..serialize import records, sanitize

router = APIRouter()

TRADE_COLS = [
    "trade_no", "trade_key", "logical_trade_key", "instrument", "direction",
    "max_contracts", "entry_ts_local", "exit_ts_local", "entry_ts_utc", "exit_ts_utc",
    "duration_s", "avg_entry", "avg_exit", "net_pnl", "comment", "source_file",
    "model_id",
]


@router.get("/trades")
def list_trades(scope: Scope = Depends(resolve_scope)) -> list[dict]:
    rows = records(scope.filtered, TRADE_COLS)
    # Attach each trade's setup badges using the notes frame loaded by
    # resolve_scope; building the lookup only over the in-scope keys keeps the
    # JSON parsing proportional to result size, not whole-table size. Notes are
    # keyed by the logical trade, so the badges show in the ATAS view too.
    notes_df = scope.notes
    keys = {r["logical_trade_key"] for r in rows}
    setup_map: dict[str, list] = {}
    if not notes_df.empty and keys:
        sub = notes_df[notes_df["trade_key"].isin(keys)]
        for _, r in sub.iterrows():
            setup_map[r["trade_key"]] = json.loads(r["setups_json"] or "[]")
    for r in rows:
        r["setups"] = setup_map.get(r["logical_trade_key"], [])
    return rows


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
    return {
        "trade": trade,
        "note": note["note"],
        "tags": json.loads(note["tags_json"] or "[]"),
        "setups": json.loads(note["setups_json"] or "[]"),
        "confluences": json.loads(note["confluences_json"] or "[]"),
        # The trade's own binding, not the effective model — the form edits this
        # one; a backtest session's model shows through ``trade.model_id``.
        "model_id": model_id,
        "rules_met": sorted(rid for rid, met in checks.items() if met),
    }
