"""App-level metadata: capability flags, model list, display timezones."""

from __future__ import annotations

from fastapi import APIRouter

from journal import ai
from journal import databento_client as dbn
from journal import db
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS

from .. import deps

router = APIRouter()


@router.get("/meta")
def meta() -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        ex = db.load_executions(conn)
        jr = db.load_journal(conn)
    return {
        "has_data": not (ex.empty and jr.empty),
        "databento_available": dbn.is_available(),
        "ai_available": ai.is_available(),
        "models": ai.config.llm_models(),
        "display_tzs": list(DISPLAY_TZS),
        "default_tz": DEFAULT_DISPLAY_TZ,
    }
