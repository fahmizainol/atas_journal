"""Per-confluence performance aggregation.

The mirror image of :mod:`api.routers.setups`: groups the in-scope trades by
their saved *confluence* badges (``trade_notes``) instead of setups, and adds
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
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from journal import db, metrics

from .. import deps
from ..scope import Scope, resolve_scope
from ..serialize import sanitize

router = APIRouter()


# --- Master-list CRUD ----------------------------------------------------
# Mirror of the setups router CRUD; see there for why names travel in the body
# (they can contain "/") rather than the path.
class ConfluenceIn(BaseModel):
    name: str
    description: str = ""


class ConfluenceUpdate(BaseModel):
    name: str                     # current name (key)
    new_name: str | None = None   # omit to edit description only
    description: str | None = None


class ConfluenceDelete(BaseModel):
    name: str


@router.get("/confluences/list")
def list_confluences() -> dict:
    """The canonical confluence names + descriptions, independent of any trade."""
    conn = deps.get_conn()
    with deps.db_lock():
        return {"confluences": db.list_taxonomy(conn, "confluences")}


@router.post("/confluences/create")
def create_confluence(body: ConfluenceIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    conn = deps.get_conn()
    with deps.db_lock():
        db.create_taxonomy(conn, "confluences", body.name, body.description)
    return {"ok": True}


@router.post("/confluences/update")
def update_confluence(body: ConfluenceUpdate) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.update_taxonomy(conn, "confluences", body.name, body.new_name, body.description)
    return {"ok": True}


@router.post("/confluences/delete")
def delete_confluence(body: ConfluenceDelete) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.delete_taxonomy(conn, "confluences", body.name)
    return {"ok": True}


def _setup_stats(group: pd.DataFrame, setup_map: dict[str, list[str]]) -> list[dict]:
    """For one confluence's trades, summarise each co-occurring setup.

    The exact inverse of ``setups._confluence_stats`` — same shape, swapped map.
    """
    buckets: dict[str, list[tuple[float, str]]] = {}
    for _, r in group.iterrows():
        for p in setup_map.get(r["trade_key"], []):
            buckets.setdefault(p, []).append((float(r["net_pnl"]), str(r.get("direction", ""))))
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


@router.get("/confluences/stats")
def confluence_stats(scope: Scope = Depends(resolve_scope)) -> dict:
    df = scope.filtered
    if df is None or df.empty:
        return {"baseline": {"trades": 0}, "confluences": [], "stacking": []}

    # Reuse the notes frame already loaded by resolve_scope.
    notes_df = scope.notes
    setup_map: dict[str, list[str]] = {}
    conf_map: dict[str, list[str]] = {}
    if not notes_df.empty:
        for _, r in notes_df.iterrows():
            setup_map[r["trade_key"]] = json.loads(r["setups_json"] or "[]")
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
            "setups": _setup_stats(group, setup_map),
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
            "longs": m.get("longs", 0),
            "shorts": m.get("shorts", 0),
            "win_rate": m.get("win_rate", 0.0),
            "expectancy": m.get("expectancy", 0.0),
            "net_pnl": m.get("net_pnl", 0.0),
        })

    return sanitize({
        "baseline": baseline,
        "confluences": confluences,
        "stacking": stacking,
    })
