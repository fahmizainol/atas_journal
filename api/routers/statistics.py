"""ATAS cross-check: exported Statistics sheet vs our recomputed metrics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from journal import db, metrics, trades

from .. import deps
from ..serialize import records, sanitize

router = APIRouter()


@router.get("/statistics/files")
def statistics_files() -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        stats = db.load_statistics(conn)
    files = sorted(stats["source_file"].unique().tolist()) if not stats.empty else []
    return {"files": files}


@router.get("/statistics/{source_file}")
def statistics_detail(source_file: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        stats = db.load_statistics(conn)
        ex = db.load_executions(conn)
        jr = db.load_journal(conn)

    atas_view = stats[stats["source_file"] == source_file]
    if atas_view.empty:
        raise HTTPException(404, f"No Statistics for {source_file}")

    # ATAS Statistics sheet as exported (metric x scope).
    pivot = atas_view.pivot_table(
        index="metric", columns="scope", values="value", aggfunc="first"
    ).reset_index()
    scopes = [c for c in ("Total", "Long", "Short") if c in pivot.columns]

    # Our recomputed metrics for this file (ATAS-rows view).
    jr_sf = jr[jr["source_file"] == source_file]
    ours = metrics.compute_metrics(trades.atas_trades(jr_sf))

    # Logical-vs-ATAS reconciliation across all imported data.
    logical = trades.build_logical_trades(jr, ex)
    reconcile = trades.reconcile(logical, jr)

    return {
        "pivot": {"scopes": scopes, "rows": records(pivot, ["metric", *scopes])},
        "ours": sanitize(ours),
        "reconcile": sanitize(reconcile),
    }
