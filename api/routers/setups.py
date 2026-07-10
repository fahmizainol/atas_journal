"""Per-setup performance aggregation — **legacy, read-only**.

Setups were superseded by models + rule compliance (see ``api.routers.models``):
a trade carried 0..n setups, whereas it has exactly one model, so only models
partition the scope. These reads survive to render the archived pre-cutover era's
badges; the create/update/delete endpoints are gone, and nothing registers new
names any more.

Groups the in-scope trades by their saved setup badges (``trade_notes``), runs
the shared :func:`journal.metrics.compute_metrics` over each group, and returns a
confluence breakdown per setup.
"""

from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends

from journal import db, metrics

from .. import deps
from ..scope import Scope, resolve_scope
from ..serialize import sanitize

router = APIRouter()


@router.get("/setups/list")
def list_setups() -> dict:
    """The canonical setup names + descriptions, independent of any trade."""
    conn = deps.get_conn()
    with deps.db_lock():
        return {"setups": db.list_taxonomy(conn, "setups")}


def _confluence_stats(group: pd.DataFrame, conf_map: dict[str, list[str]]) -> list[dict]:
    """For one setup's trades, summarise each co-occurring confluence."""
    buckets: dict[str, list[tuple[float, str]]] = {}
    for _, r in group.iterrows():
        for c in conf_map.get(r["logical_trade_key"], []):
            buckets.setdefault(c, []).append((float(r["net_pnl"]), str(r.get("direction", ""))))
    out = []
    for name, rows in buckets.items():
        n = len(rows)
        wins = sum(1 for p, _ in rows if p > 0)
        longs = sum(1 for _, d in rows if d == "Long")
        shorts = sum(1 for _, d in rows if d == "Short")
        out.append({
            "name": name,
            "trades": n,
            "longs": longs,
            "shorts": shorts,
            "win_rate": (wins / n * 100) if n else 0.0,
            "net_pnl": sum(p for p, _ in rows),
        })
    out.sort(key=lambda d: d["net_pnl"], reverse=True)
    return out


@router.get("/setups/stats")
def setup_stats(scope: Scope = Depends(resolve_scope)) -> dict:
    df = scope.filtered
    if df is None or df.empty:
        return {"setups": []}

    # Reuse the notes frame already loaded by resolve_scope.
    notes_df = scope.notes
    setup_map: dict[str, list[str]] = {}
    conf_map: dict[str, list[str]] = {}
    if not notes_df.empty:
        for _, r in notes_df.iterrows():
            setup_map[r["trade_key"]] = json.loads(r["setups_json"] or "[]")
            conf_map[r["trade_key"]] = json.loads(r["confluences_json"] or "[]")

    # Build the set of setups present on the in-scope trades.
    names: set[str] = set()
    for k in df["logical_trade_key"]:
        names.update(setup_map.get(k, []))

    setups = []
    for name in sorted(names):
        mask = df["logical_trade_key"].apply(lambda k: name in setup_map.get(k, []))
        group = df[mask]
        if group.empty:
            continue
        setups.append({
            "name": name,
            "metrics": metrics.compute_metrics(group),
            "confluences": _confluence_stats(group, conf_map),
        })

    # Most active / profitable first.
    setups.sort(key=lambda p: p["metrics"].get("net_pnl", 0) or 0, reverse=True)
    return sanitize({"setups": setups})
