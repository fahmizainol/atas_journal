"""Import ATAS .xlsx exports: from the watched dir, or via upload."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from journal import db, ingest
from journal.config import IMPORTS_DIR

from .. import deps

router = APIRouter()


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
    (e.g. ``America/New_York``, ``Asia/Kuala_Lumpur``). Defaults to NY.
    """
    tz = _resolve_tz(source_tz)
    conn = deps.get_conn()
    with deps.db_lock():
        res = ingest.import_dir(conn, source_tz=tz)
    total_fills = sum(c["executions"] for c in res.values())
    return {
        "files": len(res),
        "total_fills": total_fills,
        "source_tz": str(tz),
        "detail": res,
    }


@router.post("/import/upload")
async def import_upload(
    files: list[UploadFile] = File(...),
    source_tz: str | None = Form(None),
) -> dict:
    tz = _resolve_tz(source_tz)
    conn = deps.get_conn()
    results: dict[str, dict] = {}
    for uf in files:
        dest = IMPORTS_DIR / uf.filename
        dest.write_bytes(await uf.read())
        with deps.db_lock():
            results[uf.filename] = ingest.import_file(conn, dest, source_tz=tz)
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
