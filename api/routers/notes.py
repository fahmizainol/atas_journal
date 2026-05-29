"""Per-trade journal note + tags (keyed by trade_key)."""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

from journal import db

from .. import deps

router = APIRouter()


class NoteIn(BaseModel):
    note: str = ""
    tags: list[str] = []


@router.get("/notes/{trade_key}")
def get_note(trade_key: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        n = db.get_note(conn, trade_key)
    return {"note": n["note"], "tags": json.loads(n["tags_json"] or "[]")}


@router.put("/notes/{trade_key}")
def put_note(trade_key: str, body: NoteIn) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_note(conn, trade_key, body.note, json.dumps(body.tags))
    return {"ok": True}
