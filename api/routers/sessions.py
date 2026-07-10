"""Sessions: one row per ATAS export (``source_file``), the attempt id.

Mode drives what a session's trades mean:

* ``live``     — prop firm, real money
* ``replay``   — a simulated re-run of a past session
* ``backtest`` — one model exercised exclusively, so it binds every trade in the
  session (``model_id`` is required, and is what ``scope`` resolves as the
  effective model for a trade with no binding of its own)

``archived`` keeps a session browsable but out of the default statistics; the
pre-cutover era was archived wholesale rather than deleted.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from journal import db

from .. import deps

router = APIRouter()

MODES = ("live", "replay", "backtest")


class SessionPatch(BaseModel):
    mode: str | None = None
    model_id: int | None = None
    archived: bool | None = None


@router.get("/sessions/list")
def list_sessions() -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        return {"sessions": db.list_sessions(conn)}


@router.patch("/sessions/{source_file}")
def patch_session(source_file: str, body: SessionPatch) -> dict:
    if body.mode is not None and body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(MODES)}")

    conn = deps.get_conn()
    with deps.db_lock():
        current = db.sessions_map(conn).get(source_file)
        if current is None:
            raise HTTPException(404, f"No session for {source_file}")

        mode = body.mode or current["mode"]
        model_id = body.model_id if body.model_id is not None else current["model_id"]
        if mode == "backtest" and model_id is None:
            raise HTTPException(400, "a backtest session must bind a model")
        # Only a backtest binds a model session-wide. Rejecting rather than
        # silently dropping a model_id sent with any other mode: an "ok" that
        # discards what you asked for is worse than an error.
        if mode != "backtest" and body.model_id is not None:
            raise HTTPException(400, "only a backtest session can bind a model")
        # Leaving backtest unbinds the model, so a stale id can't invite the next
        # reader to trust it.
        clear_model = mode != "backtest"

        db.update_session(
            conn, source_file,
            mode=body.mode, model_id=None if clear_model else model_id,
            archived=body.archived, clear_model=clear_model,
        )
        return {"ok": True, "session": next(
            s for s in db.list_sessions(conn) if s["source_file"] == source_file
        )}
