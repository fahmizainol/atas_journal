"""Import ATAS .xlsx exports: auto-watched, from the watched dir, or via upload."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from journal import db, ingest
from journal.config import IMPORTS_DIR, WATCH_INTERVAL_S

from .. import deps, watcher

router = APIRouter()


@router.get("/import/feed")
def import_feed() -> dict:
    """What the watcher has done lately, newest first. The UI polls this to
    surface auto-imports (and mis-filed exports) within a minute."""
    return {
        "seq": watcher.state.seq,
        "last_scan_at": watcher.state.last_scan_at,
        "interval_s": WATCH_INTERVAL_S,
        "events": list(reversed(watcher.state.events)),
    }


@router.post("/import/scan")
async def import_scan() -> dict:
    """Run one watcher pass right now instead of waiting for the next tick."""
    imported = await asyncio.to_thread(watcher.scan_now)
    return {"ok": True, "imported": imported, "seq": watcher.state.seq}


def _resolve_tz(source_tz: str | None) -> ZoneInfo:
    """Validate a tz name from the client; fall back to the importer default."""
    if not source_tz:
        return ingest.DEFAULT_SOURCE_TZ
    try:
        return ZoneInfo(source_tz)
    except ZoneInfoNotFoundError as err:
        raise HTTPException(400, f"Unknown timezone: {source_tz}") from err


@router.post("/import/dir")
def import_dir(source_tz: str | None = None) -> dict:
    """Import every .xlsx in ``data/imports/``.

    ``source_tz`` is the timezone ATAS was set to when the file was exported
    (e.g. ``America/New_York``, ``Asia/Kuala_Lumpur``). When omitted, each
    file's tz is chosen automatically from its "Date modified" — KL before the
    configured switch date, NY from it on.
    """
    tz = _resolve_tz(source_tz) if source_tz else None
    conn = deps.get_conn()
    with deps.db_lock():
        res = ingest.import_dir(conn, source_tz=tz)
    total_fills = sum(c["executions"] for c in res.values())
    return {
        "files": len(res),
        "total_fills": total_fills,
        "source_tz": str(tz) if tz else "auto (by file date)",
        "detail": res,
    }


def _mtime_iso(epoch_ms: str) -> str | None:
    """Browser ``File.lastModified`` (epoch ms) -> UTC ISO.

    This is the export's own "Date modified" as the OS sees it; saving the
    upload would otherwise stamp it with the upload time, so we carry the
    client's value instead.
    """
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


@router.post("/import/upload")
async def import_upload(
    files: list[UploadFile] = File(...),
    source_tz: str | None = Form(None),
    mtimes: str | None = Form(None),
) -> dict:
    # When no tz is forced, pick per file from its mtime — same switch as the
    # directory import (KL before the switch date, NY from it on).
    forced_tz = _resolve_tz(source_tz) if source_tz else None
    # mtimes: comma-separated File.lastModified (epoch ms), aligned with files.
    mtime_list = mtimes.split(",") if mtimes else []
    conn = deps.get_conn()
    results: dict[str, dict] = {}
    for i, uf in enumerate(files):
        dest = IMPORTS_DIR / uf.filename
        dest.write_bytes(await uf.read())
        mtime = _mtime_iso(mtime_list[i]) if i < len(mtime_list) else None
        tz = forced_tz or (
            ingest.auto_source_tz_for_date(datetime.fromisoformat(mtime).astimezone().date())
            if mtime
            else ingest.DEFAULT_SOURCE_TZ
        )
        with deps.db_lock():
            results[uf.filename] = ingest.import_file(
                conn, dest, source_tz=tz, file_mtime=mtime
            )
    return {"results": results, "source_tz": str(tz)}


@router.delete("/day/{day}")
def delete_day(
    day: str,
    account: str | None = None,
    instrument: str | None = None,
) -> dict:
    """Delete executions and journal rows for a source-tz-local date.

    Intended for the "I replayed this date in ATAS, wipe and re-import" flow.
    Stats and notes are intentionally preserved — see ``journal.db.delete_day``.
    """
    try:
        date.fromisoformat(day)
    except ValueError as err:
        raise HTTPException(400, f"Invalid date: {day}") from err
    conn = deps.get_conn()
    with deps.db_lock():
        return db.delete_day(conn, day, account=account, instrument=instrument)


@router.delete("/attempt")
def delete_attempt(source_file: str) -> dict:
    """Delete one replay attempt (one source file) across all tables.

    For dropping a junk take from a re-done day without disturbing the other
    attempts. Removes that file's executions, journal, statistics, and its
    imported-files entry so it can be re-uploaded clean.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        return db.delete_attempt(conn, source_file)


@router.delete("/data")
def delete_all(confirm: str | None = None) -> dict:
    """Wipe all trade data so the project can be re-imported from scratch.

    Requires ``?confirm=DELETE`` so a stray call can't nuke the DB. Removes
    executions, journal, per-file statistics, and the imported-files log.
    Notes and AI analyses are preserved.
    """
    if confirm != "DELETE":
        raise HTTPException(400, "Pass ?confirm=DELETE to confirm.")
    conn = deps.get_conn()
    with deps.db_lock():
        return db.delete_all_trades(conn)
