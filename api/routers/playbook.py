"""Per-playbook performance aggregation.

Groups the in-scope trades by their saved playbook badges (``trade_notes``),
runs the shared :func:`journal.metrics.compute_metrics` over each group, and
returns a confluence breakdown per playbook. A trade may carry several
playbooks, so it contributes to each — the groups are not a strict partition.
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


def _confluence_stats(group: pd.DataFrame, conf_map: dict[str, list[str]]) -> list[dict]:
    """For one playbook's trades, summarise each co-occurring confluence."""
    buckets: dict[str, list[float]] = {}
    for _, r in group.iterrows():
        for c in conf_map.get(r["trade_key"], []):
            buckets.setdefault(c, []).append(float(r["net_pnl"]))
    out = []
    for name, pnls in buckets.items():
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        out.append({
            "name": name,
            "trades": n,
            "win_rate": (wins / n * 100) if n else 0.0,
            "net_pnl": sum(pnls),
        })
    out.sort(key=lambda d: d["net_pnl"], reverse=True)
    return out


@router.get("/playbooks/stats")
def playbook_stats(scope: Scope = Depends(resolve_scope)) -> dict:
    df = scope.filtered
    if df is None or df.empty:
        return {"playbooks": []}

    conn = deps.get_conn()
    with deps.db_lock():
        notes_df = db.all_notes(conn)

    pb_map: dict[str, list[str]] = {}
    conf_map: dict[str, list[str]] = {}
    if not notes_df.empty:
        for _, r in notes_df.iterrows():
            pb_map[r["trade_key"]] = json.loads(r["playbooks_json"] or "[]")
            conf_map[r["trade_key"]] = json.loads(r["confluences_json"] or "[]")

    # Build the set of playbooks present on the in-scope trades.
    names: set[str] = set()
    for k in df["trade_key"]:
        names.update(pb_map.get(k, []))

    playbooks = []
    for name in sorted(names):
        mask = df["trade_key"].apply(lambda k: name in pb_map.get(k, []))
        group = df[mask]
        if group.empty:
            continue
        playbooks.append({
            "name": name,
            "metrics": metrics.compute_metrics(group),
            "confluences": _confluence_stats(group, conf_map),
        })

    # Most active / profitable first.
    playbooks.sort(key=lambda p: p["metrics"].get("net_pnl", 0) or 0, reverse=True)
    return sanitize({"playbooks": playbooks})
