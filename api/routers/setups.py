"""Per-setup performance aggregation.

Groups the in-scope trades by their saved setup badges (``trade_notes``),
runs the shared :func:`journal.metrics.compute_metrics` over each group, and
returns a confluence breakdown per setup. A trade may carry several setups,
so it contributes to each — the groups are not a strict partition.
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
# Names can contain "/" (e.g. "Failed Breakdown / Bear Trap"), so the target
# name travels in the JSON body, not the path — POST action endpoints sidestep
# path-encoding entirely. The management UI and the inline badge field both
# feed the same ``setups`` table.
class SetupIn(BaseModel):
    name: str
    description: str = ""


class SetupUpdate(BaseModel):
    name: str                     # current name (key)
    new_name: str | None = None   # omit to edit description only
    description: str | None = None


class SetupDelete(BaseModel):
    name: str


@router.get("/setups/list")
def list_setups() -> dict:
    """The canonical setup names + descriptions, independent of any trade."""
    conn = deps.get_conn()
    with deps.db_lock():
        return {"setups": db.list_taxonomy(conn, "setups")}


@router.post("/setups/create")
def create_setup(body: SetupIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    conn = deps.get_conn()
    with deps.db_lock():
        db.create_taxonomy(conn, "setups", body.name, body.description)
    return {"ok": True}


@router.post("/setups/update")
def update_setup(body: SetupUpdate) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.update_taxonomy(conn, "setups", body.name, body.new_name, body.description)
    return {"ok": True}


@router.post("/setups/delete")
def delete_setup(body: SetupDelete) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.delete_taxonomy(conn, "setups", body.name)
    return {"ok": True}


def _confluence_stats(group: pd.DataFrame, conf_map: dict[str, list[str]]) -> list[dict]:
    """For one setup's trades, summarise each co-occurring confluence."""
    buckets: dict[str, list[tuple[float, str]]] = {}
    for _, r in group.iterrows():
        for c in conf_map.get(r["trade_key"], []):
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
    for k in df["trade_key"]:
        names.update(setup_map.get(k, []))

    setups = []
    for name in sorted(names):
        mask = df["trade_key"].apply(lambda k: name in setup_map.get(k, []))
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
