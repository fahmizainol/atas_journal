"""Per-attempt session video link + manual bookmarks.

Each replay take (``source_file``) links to one recorded session video,
*referenced* by its on-disk path (an OBS recording, etc.) and never copied. The
attempt number shown in the UI is positional and shifts when takes are deleted,
so the stable key is ``source_file`` — passed as a query param because ATAS
filenames contain dots that would break a path segment.

Because the backend runs under WSL while OBS records to a Windows folder, a
pasted Windows path (``C:\\Users\\…``) is translated to its WSL mount
(``/mnt/c/Users/…``). The file is streamed via ``FileResponse``, whose built-in
HTTP Range support lets the browser ``<video>`` seek instantly.

Bookmarks are pure metadata (offset in seconds + label); ``trade_key`` binds one
to a trade (NULL = free-form). The folder/filename auto-link is a future
feature — for now videos are linked manually per attempt.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from journal import db

from .. import deps

router = APIRouter()

# Extensions a browser <video> element can actually play. .mkv / .mov are
# common OBS outputs but won't play — surfaced as a hint, not a hard block.
PLAYABLE_EXTS = {".mp4", ".m4v", ".webm", ".ogg", ".ogv"}

_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def _resolve_path(raw: str) -> Path:
    """Translate a pasted path to one the WSL backend can open.

    ``C:\\Users\\me\\clip.mp4`` and ``C:/Users/me/clip.mp4`` both become
    ``/mnt/c/Users/me/clip.mp4``. POSIX paths are returned unchanged.
    """
    p = raw.strip().strip('"').strip("'")
    m = _WIN_DRIVE.match(p)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(p)


class VideoIn(BaseModel):
    path: str
    duration_s: float | None = None


class BookmarkIn(BaseModel):
    offset_s: float
    label: str = ""
    trade_key: str | None = None


class BookmarkPatch(BaseModel):
    offset_s: float | None = None
    label: str | None = None


@router.get("/videos")
def get_video(source_file: str = Query(...)) -> dict:
    """Linked video (if any) + its bookmarks for one attempt."""
    conn = deps.get_conn()
    with deps.db_lock():
        video = db.get_attempt_video(conn, source_file)
        bookmarks = db.list_bookmarks(conn, source_file)
    if video is None:
        return {"video": None, "bookmarks": bookmarks}
    resolved = _resolve_path(video["path"])
    return {
        "video": {
            "path": video["path"],
            "duration_s": video["duration_s"],
            "exists": resolved.is_file(),
            "playable": resolved.suffix.lower() in PLAYABLE_EXTS,
        },
        "bookmarks": bookmarks,
    }


@router.put("/videos")
def put_video(body: VideoIn, source_file: str = Query(...)) -> dict:
    """Link (or relink) a recording to the attempt. Rejects a path that isn't a
    readable file so a typo surfaces immediately rather than at playback."""
    resolved = _resolve_path(body.path)
    if not resolved.is_file():
        raise HTTPException(404, f"No file at: {resolved} (from '{body.path}')")
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_attempt_video(conn, source_file, body.path, body.duration_s)
    return {"ok": True, "playable": resolved.suffix.lower() in PLAYABLE_EXTS}


@router.delete("/videos")
def delete_video(source_file: str = Query(...)) -> dict:
    """Unlink the attempt's video and drop its bookmarks."""
    conn = deps.get_conn()
    with deps.db_lock():
        db.delete_attempt_video(conn, source_file)
    return {"ok": True}


@router.get("/videos/stream")
def stream_video(source_file: str = Query(...)) -> FileResponse:
    """Serve the linked file. ``FileResponse`` answers Range requests with 206
    Partial Content, which is what lets the <video> element seek."""
    conn = deps.get_conn()
    with deps.db_lock():
        video = db.get_attempt_video(conn, source_file)
    if video is None:
        raise HTTPException(404, "No video linked for this attempt")
    resolved = _resolve_path(video["path"])
    if not resolved.is_file():
        raise HTTPException(404, f"Linked file is missing: {resolved}")
    return FileResponse(resolved)


@router.post("/videos/bookmarks")
def create_bookmark(body: BookmarkIn, source_file: str = Query(...)) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        return db.add_bookmark(conn, source_file, body.offset_s, body.label, body.trade_key)


@router.put("/videos/bookmarks/{bookmark_id}")
def patch_bookmark(bookmark_id: int, body: BookmarkPatch) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        updated = db.update_bookmark(conn, bookmark_id, body.offset_s, body.label)
    if updated is None:
        raise HTTPException(404, f"No bookmark {bookmark_id}")
    return updated


@router.delete("/videos/bookmarks/{bookmark_id}")
def remove_bookmark(bookmark_id: int) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.delete_bookmark(conn, bookmark_id)
    return {"ok": True}
