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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from journal import db

from .. import deps
from ..scope import Scope, resolve_scope

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


class SyncIn(BaseModel):
    # The player knows the real runtime from <video> metadata; the DB's
    # duration_s is often NULL (the link form never persists it), so the
    # frontend passes it here. Falls back to the stored value if omitted.
    duration_s: float | None = None


def _money(pnl: float) -> str:
    return f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}"


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


@router.post("/videos/sync")
def sync_trades(
    body: SyncIn,
    source_file: str = Query(...),
    scope: Scope = Depends(resolve_scope),
) -> dict:
    """Auto-place a bookmark for every trade of this attempt from one anchor.

    Replay runs at a steady 1×, so video time maps linearly to trade
    timestamps: ``offset = anchor_offset + (entry_ts - anchor_entry_ts)``. The
    anchor is the earliest *manually* marked trade on the attempt. Trades that
    already have any bookmark are skipped (idempotent — re-running fills only
    gaps and never disturbs manual marks/nudges); trades whose computed offset
    falls outside ``[0, duration]`` are skipped (no seekable marker for them).
    """
    df = scope.filtered_all
    day_df = (
        df[df["source_file"] == source_file] if not df.empty else df
    )
    if day_df is None or day_df.empty:
        raise HTTPException(404, "No trades for this attempt in scope")

    # entry_ts_utc by trade_key — the canonical instant for the offset math.
    entry_by_key = dict(zip(day_df["trade_key"], day_df["entry_ts_utc"]))
    valid_keys = list(entry_by_key.keys())

    conn = deps.get_conn()
    with deps.db_lock():
        # Re-import can shift trade_keys, orphaning old synced rows; drop them
        # before filling so a re-synced day ends clean.
        pruned = db.prune_orphan_synced_bookmarks(conn, source_file, valid_keys)
        existing = db.list_bookmarks(conn, source_file)

        # Anchor = earliest (by entry time) manual, trade-bound bookmark whose
        # trade still exists on this attempt.
        anchors = [
            b for b in existing
            if b["origin"] == "manual"
            and b["trade_key"] in entry_by_key
        ]
        if not anchors:
            raise HTTPException(
                400, "Mark a trade on the video first — that's the sync anchor."
            )
        anchor = min(anchors, key=lambda b: entry_by_key[b["trade_key"]])
        anchor_entry = entry_by_key[anchor["trade_key"]]
        anchor_offset = anchor["offset_s"]

        duration = body.duration_s
        if duration is None:
            video = db.get_attempt_video(conn, source_file)
            duration = video["duration_s"] if video else None
        if not duration or duration <= 0:
            raise HTTPException(
                400, "Video duration unknown — play the recording, then sync."
            )

        already = {b["trade_key"] for b in existing if b["trade_key"]}
        created = 0
        out_of_range = 0
        for _, t in day_df.iterrows():
            key = t["trade_key"]
            if key in already:
                continue  # fill gaps only; never touch an existing bookmark
            offset = anchor_offset + (t["entry_ts_utc"] - anchor_entry).total_seconds()
            if offset < 0 or offset > duration:
                out_of_range += 1
                continue
            label = f"#{int(t['trade_no'])} {t['direction']} {_money(float(t['net_pnl']))}"
            db.add_bookmark(conn, source_file, offset, label, key, origin="synced")
            created += 1

    return {
        "created": created,
        "skipped_existing": len(already),
        "skipped_out_of_range": out_of_range,
        "pruned_orphans": pruned,
        "anchor_trade_key": anchor["trade_key"],
    }


@router.delete("/videos/synced")
def clear_synced(source_file: str = Query(...)) -> dict:
    """Remove every auto-synced bookmark for the attempt (manual marks stay)."""
    conn = deps.get_conn()
    with deps.db_lock():
        deleted = db.clear_synced_bookmarks(conn, source_file)
    return {"ok": True, "deleted": deleted}
