"""Per-trade journal entry: note, tags, model, and rule compliance.

``trade_key`` here is always the **logical** trade key (``logical_trade_key`` on
the scope frame), so a note written in the logical view still resolves when the
same trade is read as ATAS rows.

Setup/confluence badges are still accepted for the archived pre-cutover era, but
saving one no longer registers it in the master list — that auto-registration is
what let any typo become a permanent taxonomy entry. Models are the live
vocabulary now; they're created deliberately, on the Models tab.
"""

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
    setups: list[str] = []
    confluences: list[str] = []
    model_id: int | None = None   # None = off-model
    rules_met: list[int] = []     # ids of the model's rules this trade satisfied


@router.get("/notes/{trade_key}")
def get_note(trade_key: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        n = db.get_note(conn, trade_key)
        model_id = db.get_trade_model(conn, trade_key)
        checks = db.get_rule_checks(conn, trade_key)
    return {
        "note": n["note"],
        "tags": json.loads(n["tags_json"] or "[]"),
        "setups": json.loads(n["setups_json"] or "[]"),
        "confluences": json.loads(n["confluences_json"] or "[]"),
        "model_id": model_id,
        "rules_met": sorted(rid for rid, met in checks.items() if met),
    }


@router.put("/notes/{trade_key}")
def put_note(trade_key: str, body: NoteIn) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_note(
            conn,
            trade_key,
            body.note,
            json.dumps(body.tags),
            json.dumps(body.setups),
            json.dumps(body.confluences),
        )
        db.set_trade_model(conn, trade_key, body.model_id)
        # Sweeps any check belonging to a rule outside the chosen model, so
        # switching a trade's model can't leave the old model's checks behind.
        db.set_rule_checks(conn, trade_key, body.model_id, body.rules_met)
    return {"ok": True}
