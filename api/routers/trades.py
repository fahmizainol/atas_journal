"""Trades table + single-trade detail (with its saved note/tags)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from journal import db

from .. import deps
from ..scope import Scope, resolve_scope
from ..serialize import records, sanitize

router = APIRouter()

TRADE_COLS = [
    "trade_no", "trade_key", "instrument", "direction", "max_contracts",
    "entry_ts_local", "exit_ts_local", "entry_ts_utc", "exit_ts_utc",
    "duration_s", "avg_entry", "avg_exit", "net_pnl", "comment",
]


@router.get("/trades")
def list_trades(scope: Scope = Depends(resolve_scope)) -> list[dict]:
    return records(scope.filtered, TRADE_COLS)


@router.get("/trades/{trade_no}")
def trade_detail(trade_no: int, scope: Scope = Depends(resolve_scope)) -> dict:
    tf = scope.filtered
    match = tf[tf["trade_no"] == trade_no] if not tf.empty else tf
    if match.empty:
        raise HTTPException(404, f"Trade #{trade_no} not in scope")
    row = match.iloc[0]
    trade = sanitize(row[[c for c in TRADE_COLS if c in row.index]].to_dict())

    conn = deps.get_conn()
    with deps.db_lock():
        note = db.get_note(conn, row["trade_key"])
    return {
        "trade": trade,
        "note": note["note"],
        "tags": json.loads(note["tags_json"] or "[]"),
    }
