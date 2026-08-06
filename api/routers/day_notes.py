"""Per-day journal note + tags (keyed by ISO date)."""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

from journal import db

from .. import deps

router = APIRouter()


class DayNoteIn(BaseModel):
    note: str = ""
    tags: list[str] = []


@router.get("/day-notes")
def list_day_notes() -> dict:
    """Every day's note+tags in one shot, keyed by ISO date — lets a table (the
    Interactions Sessions grid) show per-day tags without a request per row."""
    conn = deps.get_conn()
    with deps.db_lock():
        df = db.all_day_notes(conn).fillna("")
    out: dict[str, dict] = {}
    for r in df.itertuples(index=False):
        out[str(r.day)] = {
            "note": r.note or "",
            "tags": json.loads(r.tags_json or "[]"),
        }
    return out


@router.get("/day-notes/{day}")
def get_day_note(day: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        n = db.get_day_note(conn, day)
    return {"note": n["note"], "tags": json.loads(n["tags_json"] or "[]")}


@router.put("/day-notes/{day}")
def put_day_note(day: str, body: DayNoteIn) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_day_note(conn, day, body.note, json.dumps(body.tags))
    return {"ok": True}
