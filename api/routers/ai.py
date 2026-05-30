"""AI per-trade critique and aggregate period review (synchronous / slow).

LiteLLM calls run in FastAPI's threadpool with a frontend loading state — no
streaming or job queue (deferred). Reviews are cached per (scope, model) and a
staleness flag is computed server-side so the React warning matches Streamlit.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from journal import ai, db, edges, levels as levels_mod, metrics
from journal import databento_client as dbn
from journal import excursion as excursion_mod

from .. import deps
from ..scope import Scope, resolve_scope

router = APIRouter()


def _parse_analyses(saved: dict[str, dict]) -> dict:
    return {
        model: {"analysis": json.loads(rec["analysis_json"]), "created_at": rec["created_at"]}
        for model, rec in saved.items()
    }


# --- Per-trade critique --------------------------------------------------
@router.get("/ai/trade/{trade_key}")
def get_trade_ai(trade_key: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        saved = db.get_trade_analyses(conn, trade_key)
    return {"analyses": _parse_analyses(saved)}


@router.post("/ai/trade/{trade_key}")
def gen_trade_ai(
    trade_key: str, model: str = Query(...), scope: Scope = Depends(resolve_scope)
) -> dict:
    if not ai.is_available():
        raise HTTPException(400, "AI not configured")
    if not dbn.is_available():
        raise HTTPException(400, "Excursion data unavailable (Databento not configured)")

    tf = scope.filtered
    match = tf[tf["trade_key"] == trade_key] if not tf.empty else tf
    if match.empty:
        raise HTTPException(404, "Trade not in scope")
    trade = match.iloc[0]

    exc = excursion_mod.trade_excursion(trade)
    if exc is None:
        raise HTTPException(400, "No excursion data for this trade")

    conn = deps.get_conn()
    with deps.db_lock():
        note = db.get_note(conn, trade_key)["note"]
        profile = db.get_setting(conn, "trading_profile")
    comment = trade.get("comment", "") or ""
    lv = levels_mod.compute_levels(trade["instrument"], levels_mod.rth_date_for(trade["entry_ts_utc"]))
    rel = levels_mod.level_relations(
        lv, float(trade["avg_entry"]), float(trade["avg_exit"]),
        exc.get("mfe_price"), exc.get("mae_price"),
    )
    data = ai.analyze_trade(trade, exc, note, comment, profile, model, levels=rel)
    if "error" in data:
        return {"error": data["error"]}

    with deps.db_lock():
        db.save_trade_analysis(conn, trade_key, model, json.dumps(data))
        saved = db.get_trade_analyses(conn, trade_key)
    return {"analyses": _parse_analyses(saved)}


# --- Aggregate period review ---------------------------------------------
def _review_payload(saved: dict[str, dict], count: int, latest: str | None) -> dict:
    reviews = {}
    for model, rec in saved.items():
        stale = rec["trade_count"] != count or rec["latest_trade_ts"] != latest
        reviews[model] = {
            "review": json.loads(rec["review_json"]),
            "created_at": rec["created_at"],
            "trade_count": rec["trade_count"],
            "stale": stale,
        }
    return {"reviews": reviews}


@router.get("/ai/period")
def get_period_ai(scope: Scope = Depends(resolve_scope)) -> dict:
    sig = ai.scope_signature(scope.instruments, scope.date_range, scope.tags)
    count, latest = ai.trade_fingerprint(scope.filtered)
    conn = deps.get_conn()
    with deps.db_lock():
        saved = db.get_period_reviews(conn, sig)
    return _review_payload(saved, count, latest)


@router.post("/ai/period")
def gen_period_ai(model: str = Query(...), scope: Scope = Depends(resolve_scope)) -> dict:
    if not ai.is_available():
        raise HTTPException(400, "AI not configured")
    tf = scope.filtered
    if tf.empty:
        raise HTTPException(400, "No trades in scope")

    sig = ai.scope_signature(scope.instruments, scope.date_range, scope.tags)
    count, latest = ai.trade_fingerprint(tf)
    conn = deps.get_conn()
    with deps.db_lock():
        profile = db.get_setting(conn, "trading_profile")

    m = metrics.compute_metrics(tf)
    edge_tables = {
        "By weekday": edges.by_weekday(tf),
        f"By hour ({scope.tz_label})": edges.by_hour_kl(tf),
        "By hour (US Eastern / session)": edges.by_hour_et(tf),
        "By hold time": edges.by_hold_time(tf),
        "Long vs Short": edges.by_direction(tf),
    }
    data = ai.analyze_period(m, edge_tables, metrics.daily_pnl(tf), profile, model)
    if "error" in data:
        return {"error": data["error"]}

    filters_json = json.dumps({
        "instruments": scope.instruments,
        "date_range": [str(scope.start), str(scope.end)] if scope.date_range else None,
        "tags": scope.tags,
    })
    with deps.db_lock():
        db.save_period_review(conn, sig, model, filters_json, json.dumps(data), count, latest)
        saved = db.get_period_reviews(conn, sig)
    return _review_payload(saved, count, latest)
