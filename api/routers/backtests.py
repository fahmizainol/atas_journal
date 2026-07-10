"""Backtest monitoring: per-model sample progress, stats, and mode comparison.

A backtest session binds one model to every trade it holds, so "the model's
backtest record" is just the un-archived backtest trades whose effective model
is that model. The comparison block answers the question backtesting exists to
ask: does the model's replay/live performance track what the backtest promised?

This page is model-centric, not FilterBar-centric, so these endpoints take only
a display tz — the shared scope's mode/model filters would fight the slicing
done here.
"""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from journal import db, metrics, trades
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS

from .. import deps
from ..scope import _attach_session_columns
from ..serialize import records, sanitize

router = APIRouter()

MODES = ("backtest", "replay", "live")


def _frames(tz_label: str | None) -> tuple[pd.DataFrame, list[dict], list[dict], dict]:
    """(trades frame with session columns, session rows, model catalog, imported_at)."""
    label = tz_label if tz_label in DISPLAY_TZS else DEFAULT_DISPLAY_TZ
    tz = DISPLAY_TZS[label]
    conn = deps.get_conn()
    with deps.db_lock():
        ex = db.load_executions(conn)
        jr = db.load_journal(conn)
        sessions = db.sessions_map(conn)
        model_by_trade = db.trade_model_map(conn)
        session_rows = db.list_sessions(conn)
        catalog = db.list_models(conn, include_archived=True)
        imported = db.imported_at_map(conn)

    base = trades.build_logical_trades(jr, ex)
    base = trades.localize(base, tz)
    if base is None or base.empty:
        return pd.DataFrame(), session_rows, catalog, imported
    base["logical_trade_key"] = base["trade_key"]
    base = _attach_session_columns(base, sessions, model_by_trade)
    return base, session_rows, catalog, imported


def _slim(df: pd.DataFrame) -> dict:
    """The headline numbers shown per mode / per session / per model card."""
    m = metrics.compute_metrics(df)
    keys = (
        "trades", "net_pnl", "win_rate", "expectancy", "profit_factor",
        "avg_win", "avg_loss", "max_drawdown",
    )
    return {k: m.get(k) for k in keys if k in m} | {"trades": m["trades"]}


def _backtest_slice(df: pd.DataFrame, model_id: int) -> pd.DataFrame:
    if df.empty:
        return df
    return df[
        (df["session_mode"] == "backtest")
        & (df["model_id"] == model_id)
        & ~df["session_archived"].astype(bool)
    ]


@router.get("/backtests/overview")
def backtests_overview(tz: str | None = Query(None)) -> dict:
    """One card per model: its backtest sample so far vs its target."""
    df, session_rows, catalog, imported = _frames(tz)
    out = []
    for model in catalog:
        bt = _backtest_slice(df, model["id"])
        bt_sessions = [
            s for s in session_rows
            if s["mode"] == "backtest" and s["model_id"] == model["id"]
        ]
        # An archived model with no backtest history is pure noise here.
        if model["archived"] and bt.empty and not bt_sessions:
            continue
        last = max((imported.get(s["source_file"], "") for s in bt_sessions), default="")
        out.append({
            "id": model["id"],
            "name": model["name"],
            "description": model["description"],
            "archived": model["archived"],
            "folder": model["folder"],
            "target_sample": model["target_sample"],
            "sessions": len(bt_sessions),
            "last_import": last or None,
            "metrics": _slim(bt),
        })
    return sanitize({"models": out})


@router.get("/backtests/{model_id}")
def backtest_detail(model_id: int, tz: str | None = Query(None)) -> dict:
    df, session_rows, catalog, imported = _frames(tz)
    model = next((m for m in catalog if m["id"] == model_id), None)
    if model is None:
        raise HTTPException(404, f"no model {model_id}")

    bt = _backtest_slice(df, model_id)
    eq = metrics.equity_curve(bt)
    if not eq.empty:
        # trade_no restarts per source file; the aggregate curve spans many
        # files, so renumber sequentially for a monotonic x-axis.
        eq["trade_no"] = range(1, len(eq) + 1)

    # Backtest vs replay vs live for the same effective model, side by side.
    comparison = {}
    for mode in MODES:
        if df.empty:
            comparison[mode] = _slim(df)
            continue
        sub = df[
            (df["session_mode"] == mode)
            & (df["model_id"] == model_id)
            & ~df["session_archived"].astype(bool)
        ]
        comparison[mode] = _slim(sub)

    sessions = []
    for s in session_rows:
        if s["mode"] != "backtest" or s["model_id"] != model_id:
            continue
        sub = df[df["source_file"] == s["source_file"]] if not df.empty else df
        days = (
            sorted(sub["entry_ts_local"].dt.date.unique()) if not sub.empty else []
        )
        sessions.append({
            "source_file": s["source_file"],
            "archived": s["archived"],
            "note": s["note"] or "",
            "imported_at": imported.get(s["source_file"]),
            "first_day": days[0] if days else None,
            "last_day": days[-1] if days else None,
            "metrics": _slim(sub),
        })
    sessions.sort(key=lambda s: s["imported_at"] or "", reverse=True)

    return sanitize({
        "model": {
            "id": model["id"],
            "name": model["name"],
            "description": model["description"],
            "archived": model["archived"],
            "folder": model["folder"],
            "target_sample": model["target_sample"],
        },
        "metrics": metrics.compute_metrics(bt),
        "equity": records(eq, ["ts", "trade_no", "pnl", "equity", "drawdown"]),
        "distribution": bt["net_pnl"].astype(float).tolist() if not bt.empty else [],
        "comparison": comparison,
        "sessions": sessions,
    })
