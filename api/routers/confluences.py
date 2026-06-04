"""Per-confluence performance aggregation.

The mirror image of :mod:`api.routers.playbook`: groups the in-scope trades by
their saved *confluence* badges (``trade_notes``) instead of playbooks, and adds
two analyses confluences invite that setups don't —

* **lift** — how each confluence performs *with* vs *without* it, so a 60% win
  rate can be read against the baseline rather than in a vacuum; and
* **stacking** — how outcomes change as more confluences pile onto one trade.

A trade may carry several confluences, so it contributes to each group — the
groups are not a strict partition. Reuses :func:`journal.metrics.compute_metrics`
unchanged.
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


def _playbook_stats(group: pd.DataFrame, pb_map: dict[str, list[str]]) -> list[dict]:
    """For one confluence's trades, summarise each co-occurring playbook.

    The exact inverse of ``playbook._confluence_stats`` — same shape, swapped map.
    """
    buckets: dict[str, list[float]] = {}
    for _, r in group.iterrows():
        for p in pb_map.get(r["trade_key"], []):
            buckets.setdefault(p, []).append(float(r["net_pnl"]))
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


@router.get("/confluences/stats")
def confluence_stats(scope: Scope = Depends(resolve_scope)) -> dict:
    df = scope.filtered
    if df is None or df.empty:
        return {"baseline": {"trades": 0}, "confluences": [], "stacking": []}

    conn = deps.get_conn()
    with deps.db_lock():
        notes_df = db.all_notes(conn)

    pb_map: dict[str, list[str]] = {}
    conf_map: dict[str, list[str]] = {}
    if not notes_df.empty:
        for _, r in notes_df.iterrows():
            pb_map[r["trade_key"]] = json.loads(r["playbooks_json"] or "[]")
            conf_map[r["trade_key"]] = json.loads(r["confluences_json"] or "[]")

    # Baseline: every in-scope trade, the yardstick that lift is measured against.
    baseline = metrics.compute_metrics(df)

    # --- Leaderboard + lift -------------------------------------------------
    names: set[str] = set()
    for k in df["trade_key"]:
        names.update(conf_map.get(k, []))

    confluences = []
    for name in sorted(names):
        mask = df["trade_key"].apply(lambda k: name in conf_map.get(k, []))
        group = df[mask]
        if group.empty:
            continue
        rest = df[~mask]
        with_m = metrics.compute_metrics(group)
        without_m = metrics.compute_metrics(rest)
        with_wr = with_m.get("win_rate", 0.0) or 0.0
        with_exp = with_m.get("expectancy", 0.0) or 0.0
        without_wr = without_m.get("win_rate", 0.0) or 0.0
        without_exp = without_m.get("expectancy", 0.0) or 0.0
        confluences.append({
            "name": name,
            "metrics": with_m,
            "lift": {
                "win_rate_delta": with_wr - without_wr,
                "expectancy_delta": with_exp - without_exp,
                "without_win_rate": without_wr,
                "without_expectancy": without_exp,
                "without_trades": without_m.get("trades", 0),
            },
            "playbooks": _playbook_stats(group, pb_map),
        })

    # Most active / profitable first.
    confluences.sort(key=lambda c: c["metrics"].get("net_pnl", 0) or 0, reverse=True)

    # --- Stacking -----------------------------------------------------------
    # Bucket each trade by how many confluences it carries; lump 4+ together.
    counts = df["trade_key"].apply(lambda k: len(conf_map.get(k, [])))
    stacking = []
    for c in (1, 2, 3, 4):
        bucket = df[counts == c] if c < 4 else df[counts >= 4]
        if bucket.empty:
            continue
        m = metrics.compute_metrics(bucket)
        stacking.append({
            "count": c,
            "label": "4+" if c == 4 else str(c),
            "trades": m.get("trades", 0),
            "win_rate": m.get("win_rate", 0.0),
            "expectancy": m.get("expectancy", 0.0),
            "net_pnl": m.get("net_pnl", 0.0),
        })

    return sanitize({
        "baseline": baseline,
        "confluences": confluences,
        "stacking": stacking,
    })
