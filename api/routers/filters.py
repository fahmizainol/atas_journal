"""Available filter options for the current view/tz (instruments, dates, tags)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from journal import db

from .. import deps
from ..scope import Scope, resolve_scope

router = APIRouter()


@router.get("/filters")
def filters(scope: Scope = Depends(resolve_scope)) -> dict:
    base = scope.base
    instruments = sorted(base["instrument"].dropna().unique().tolist()) if not base.empty else []
    accounts = sorted(base["account"].dropna().unique().tolist()) if not base.empty else []
    date_min = date_max = None
    if not base.empty:
        date_min = base["entry_ts_local"].min().date().isoformat()
        date_max = base["entry_ts_local"].max().date().isoformat()

    conn = deps.get_conn()
    with deps.db_lock():
        notes_df = db.all_notes(conn)
    all_tags: set[str] = set()
    if not notes_df.empty:
        for tj in notes_df["tags_json"].dropna():
            all_tags.update(json.loads(tj or "[]"))

    return {
        "instruments": instruments,
        "accounts": accounts,
        "date_min": date_min,
        "date_max": date_max,
        "tags": sorted(all_tags),
    }
