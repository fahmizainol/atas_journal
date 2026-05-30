"""Parse ATAS xlsx exports (Statistics / Journal / Executions) into DB rows.

Timezone handling: Journal and Executions timestamps are naive — they carry
the clock of whatever timezone ATAS was set to when the file was exported.
The importer tags those naives with ``source_tz`` (default America/New_York;
older exports used Asia/Kuala_Lumpur — override per-import) and stores both
the source-local ISO and the UTC ISO. The Statistics sheet is already UTC and
is stored verbatim.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

from . import db
from .config import ET_TZ, IMPORTS_DIR, UTC_TZ, normalize_instrument

DEFAULT_SOURCE_TZ = ET_TZ


def _utc_iso(dt: datetime | None, source_tz: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=source_tz).astimezone(UTC_TZ).isoformat()


def _local_iso(dt: datetime | None, source_tz: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=source_tz).isoformat()


def _journal_key(row: dict) -> str:
    parts = [
        str(row["account"]), str(row["instrument"]),
        str(row["open_ts_local"]), str(row["close_ts_local"]),
        str(row["open_price"]), str(row["close_price"]), str(row["pnl"]),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _sheet_rows(wb, name: str) -> list[tuple]:
    if name not in wb.sheetnames:
        return []
    return list(wb[name].iter_rows(values_only=True))


def parse_file(
    path: Path, source_tz: ZoneInfo = DEFAULT_SOURCE_TZ
) -> dict[str, list[dict]]:
    """Return normalized {executions, journal, statistics} record lists.

    ``source_tz`` is the timezone the naive Journal/Executions timestamps were
    recorded in (i.e. whatever ATAS was set to at export time).
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    source = path.name

    executions: list[dict] = []
    for r in _sheet_rows(wb, "Executions")[1:]:
        if r[0] is None or r[3] is None:
            continue
        account, instrument, ts, exch_id, direction, price, volume, _route, comm = r[:9]
        executions.append({
            "exchange_id": str(exch_id),
            "account": str(account),
            "instrument": normalize_instrument(str(instrument)),
            "ts_local": _local_iso(ts, source_tz),
            "ts_utc": _utc_iso(ts, source_tz),
            "direction": str(direction),
            "price": float(price),
            "volume": float(volume),
            "commission": float(comm or 0),
            "source_file": source,
        })

    journal: list[dict] = []
    for r in _sheet_rows(wb, "Journal")[1:]:
        if r[0] is None:
            continue
        (account, instrument, open_t, open_p, open_v, close_t, close_p,
         close_v, price_pnl, profit_ticks, pnl, comment) = r[:12]
        rec = {
            "account": str(account),
            "instrument": normalize_instrument(str(instrument)),
            "open_ts_local": _local_iso(open_t, source_tz),
            "close_ts_local": _local_iso(close_t, source_tz),
            "open_ts_utc": _utc_iso(open_t, source_tz),
            "close_ts_utc": _utc_iso(close_t, source_tz),
            "open_price": float(open_p),
            "open_volume": float(open_v),
            "close_price": float(close_p),
            "close_volume": float(close_v),
            "price_pnl": float(price_pnl) if price_pnl is not None else None,
            "profit_ticks": float(profit_ticks) if profit_ticks is not None else None,
            "pnl": float(pnl) if pnl is not None else None,
            "comment": str(comment or ""),
            "source_file": source,
        }
        rec["dedupe_key"] = _journal_key(rec)
        journal.append(rec)

    statistics: list[dict] = []
    stat_rows = _sheet_rows(wb, "Statistics")
    for r in stat_rows[1:]:
        if r[0] is None:
            continue
        metric = str(r[0])
        for scope, idx in (("Total", 1), ("Long", 2), ("Short", 3)):
            if idx < len(r) and r[idx] is not None:
                statistics.append({
                    "source_file": source,
                    "metric": metric,
                    "scope": scope,
                    "value": str(r[idx]),
                })

    return {"executions": executions, "journal": journal, "statistics": statistics}


def import_file(
    conn: sqlite3.Connection,
    path: Path,
    source_tz: ZoneInfo = DEFAULT_SOURCE_TZ,
) -> dict[str, int]:
    parsed = parse_file(path, source_tz=source_tz)
    counts = {
        "executions": db.insert_executions(conn, parsed["executions"]),
        "journal": db.insert_journal(conn, parsed["journal"]),
        "statistics": db.insert_statistics(conn, parsed["statistics"]),
    }
    db.mark_imported(conn, path.name)
    return counts


def import_dir(
    conn: sqlite3.Connection,
    directory: Path = IMPORTS_DIR,
    source_tz: ZoneInfo = DEFAULT_SOURCE_TZ,
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for path in sorted(Path(directory).glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        results[path.name] = import_file(conn, path, source_tz=source_tz)
    return results
