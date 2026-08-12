"""App-level metadata: capability flags, model list, display timezones."""

from __future__ import annotations

from fastapi import APIRouter

from journal import ai
from journal import databento_client as dbn
from journal import tick_bars
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
        # Whether a *run* can buy data it hasn't got. Still a question about the
        # wallet, and still the right one for the sidebar's status line.
        "databento_available": dbn.is_available(),
        # Whether anything can be *charted*. Since 2026-08-08 the charts read the
        # tick cache and never fetch, so an API key says nothing about them: a
        # checkout with no key still draws every session on disk, and one with a
        # key but an empty cache draws nothing. The two questions came apart, so
        # they are two fields.
        "chart_ticks_available": tick_bars.is_available(),
        "ai_available": ai.is_available(),
        "models": ai.config.llm_models(),
        "display_tzs": list(DISPLAY_TZS),
        "default_tz": DEFAULT_DISPLAY_TZ,
    }
