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
to a trade (NULL = free-form). Beyond the manual per-attempt link, the
``/videos/scan`` endpoint batch-auto-links every attempt whose expected
recording (``DD-MON-YYYY-NN.mp4``) is found in the configured recordings folder.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from journal import db
from journal.recordings import expected_recording_name, parse_attempt_no

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


@router.get("/videos/trade-status")
def trade_video_status(scope: Scope = Depends(resolve_scope)) -> dict:
    """Batch video/bookmark status for the visible Trades table rows."""
    df = scope.filtered
    if df is None or df.empty:
        return {"statuses": {}}

    trade_keys = [str(k) for k in df["trade_key"].dropna().unique().tolist()]
    source_files = [str(s) for s in df["source_file"].dropna().unique().tolist()]
    if not trade_keys or not source_files:
        return {"statuses": {}}

    conn = deps.get_conn()
    with deps.db_lock():
        sf_ph = ",".join("?" for _ in source_files)
        video_rows = conn.execute(
            "SELECT source_file, path, duration_s "
            f"FROM attempt_videos WHERE source_file IN ({sf_ph})",
            source_files,
        ).fetchall()

        key_ph = ",".join("?" for _ in trade_keys)
        bookmark_rows = conn.execute(
            "SELECT source_file, offset_s, label, trade_key, origin "
            f"FROM video_bookmarks WHERE trade_key IN ({key_ph}) ORDER BY offset_s",
            trade_keys,
        ).fetchall()

    videos_by_source: dict[str, dict] = {}
    for row in video_rows:
        video = dict(row)
        resolved = _resolve_path(video["path"])
        videos_by_source[video["source_file"]] = {
            "path": video["path"],
            "duration_s": video["duration_s"],
            "exists": resolved.is_file(),
            "playable": resolved.suffix.lower() in PLAYABLE_EXTS,
        }

    bookmark_by_trade: dict[str, dict] = {}
    for row in bookmark_rows:
        bm = dict(row)
        # Match the Day view's first-bookmark behavior: bookmarks are ordered by
        # offset, and a duplicate trade mark should not make the table unstable.
        bookmark_by_trade.setdefault(
            bm["trade_key"],
            {
                "source_file": bm["source_file"],
                "offset_s": bm["offset_s"],
                "label": bm["label"],
                "origin": bm["origin"],
            },
        )

    statuses: dict[str, dict] = {}
    for _, trade in df.iterrows():
        trade_key = str(trade["trade_key"])
        source_file = str(trade["source_file"])
        video = videos_by_source.get(source_file)
        bookmark = bookmark_by_trade.get(trade_key)
        statuses[trade_key] = {
            "source_file": source_file,
            "has_video": video is not None,
            "exists": bool(video and video["exists"]),
            "playable": bool(video and video["playable"]),
            "bookmark": bookmark if bookmark and bookmark["source_file"] == source_file else None,
        }

    return {"statuses": statuses}


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


@router.post("/videos/scan")
def scan_recordings(scope: Scope = Depends(resolve_scope)) -> dict:
    """Auto-link every attempt whose recording is in the configured folder.

    For each replay attempt (``source_file``) not already linked, compute the
    expected recording name from its replayed day (the trades' own entry date)
    and its parsed attempt number, and link it if that ``.mp4`` exists in the
    folder. Idempotent: already-linked attempts are skipped, so manual links
    are never disturbed and a re-scan only fills new matches.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        folder_raw = db.get_setting(conn, "recordings_folder")
    if not folder_raw.strip():
        raise HTTPException(400, "Set a recordings folder first.")
    folder = _resolve_path(folder_raw)
    if not folder.is_dir():
        raise HTTPException(400, f"Not a folder: {folder} (from '{folder_raw}')")

    df = scope.filtered_all
    if df is None or df.empty:
        return {"linked": [], "count": 0}

    # One day per export (a replay session = one trading day); if an export's
    # trades ever straddle midnight, the earliest entry date wins.
    day_by_sf = (
        df.assign(_d=df["entry_ts_local"].dt.date)
        .groupby("source_file")["_d"]
        .min()
        .to_dict()
    )

    conn = deps.get_conn()
    linked: list[dict] = []
    with deps.db_lock():
        already_linked = db.linked_video_source_files(conn)
        for sf, day in day_by_sf.items():
            if sf in already_linked:
                continue  # skip-if-linked is the whole override rule
            attempt_no = parse_attempt_no(sf)
            name = expected_recording_name(day, attempt_no)
            candidate = folder / name
            if candidate.is_file():
                db.save_attempt_video(conn, sf, str(candidate))
                linked.append({
                    "source_file": sf,
                    "day": day.isoformat(),
                    "attempt_no": attempt_no,
                    "filename": name,
                })

    linked.sort(key=lambda r: (r["day"], r["attempt_no"]))
    return {"linked": linked, "count": len(linked)}


@router.delete("/videos/synced")
def clear_synced(source_file: str = Query(...)) -> dict:
    """Remove every auto-synced bookmark for the attempt (manual marks stay)."""
    conn = deps.get_conn()
    with deps.db_lock():
        deleted = db.clear_synced_bookmarks(conn, source_file)
    return {"ok": True, "deleted": deleted}
