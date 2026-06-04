"""Persisted app settings: the AI-grounding trading profile and the
auto-link recordings folder."""

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


class RecordingsFolderIn(BaseModel):
    folder: str = ""


@router.get("/settings/recordings_folder")
def get_recordings_folder() -> dict:
    """The single folder the auto-link scanner looks in (as entered; a Windows
    ``C:\\…`` path is translated to its WSL mount when the scan runs)."""
    conn = deps.get_conn()
    with deps.db_lock():
        folder = db.get_setting(conn, "recordings_folder")
    return {"folder": folder}


@router.put("/settings/recordings_folder")
def put_recordings_folder(body: RecordingsFolderIn) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_setting(conn, "recordings_folder", body.folder.strip())
    return {"ok": True}
