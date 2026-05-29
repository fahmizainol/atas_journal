"""Persisted app settings (currently the AI-grounding trading profile)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from journal import db

from .. import deps

router = APIRouter()


class ProfileIn(BaseModel):
    profile: str = ""


@router.get("/settings/trading_profile")
def get_profile() -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        profile = db.get_setting(conn, "trading_profile")
    return {"profile": profile}


@router.put("/settings/trading_profile")
def put_profile(body: ProfileIn) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_setting(conn, "trading_profile", body.profile)
    return {"ok": True}
