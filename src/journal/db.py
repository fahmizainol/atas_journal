"""SQLite schema, connection, and idempotent upserts.

Re-importing overlapping ATAS files must never double-count, so every insert
uses INSERT OR IGNORE against a stable dedupe key:
  - executions: Exchange ID (unique per fill)
  - atas_journal: hash of account/instrument/open/close/prices/pnl
  - atas_statistics: (source_file, metric, scope)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    exchange_id   TEXT PRIMARY KEY,
    account       TEXT,
    instrument    TEXT,
    ts_local      TEXT,   -- KL (UTC+8) ISO string
    ts_utc        TEXT,   -- UTC ISO string
    direction     TEXT,   -- Buy / Sell
    price         REAL,
    volume        REAL,
    commission    REAL,
    source_file   TEXT
);

CREATE TABLE IF NOT EXISTS atas_journal (
    dedupe_key    TEXT PRIMARY KEY,
    account       TEXT,
    instrument    TEXT,
    open_ts_local TEXT,
    close_ts_local TEXT,
    open_ts_utc   TEXT,
    close_ts_utc  TEXT,
    open_price    REAL,
    open_volume   REAL,
    close_price   REAL,
    close_volume  REAL,
    price_pnl     REAL,
    profit_ticks  REAL,
    pnl           REAL,
    comment       TEXT,
    source_file   TEXT
);

CREATE TABLE IF NOT EXISTS atas_statistics (
    source_file   TEXT,
    metric        TEXT,
    scope         TEXT,   -- Total / Long / Short
    value         TEXT,
    PRIMARY KEY (source_file, metric, scope)
);

CREATE TABLE IF NOT EXISTS trade_notes (
    trade_key     TEXT PRIMARY KEY,
    note          TEXT,
    tags_json     TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS imported_files (
    source_file   TEXT PRIMARY KEY,
    imported_at   TEXT
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: Streamlit reruns the script across worker threads
    # but serializes runs per session, so sharing one cached connection is safe.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_executions(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = [
        "exchange_id", "account", "instrument", "ts_local", "ts_utc",
        "direction", "price", "volume", "commission", "source_file",
    ]
    return _insert_ignore(conn, "executions", cols, rows)


def insert_journal(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = [
        "dedupe_key", "account", "instrument", "open_ts_local", "close_ts_local",
        "open_ts_utc", "close_ts_utc", "open_price", "open_volume", "close_price",
        "close_volume", "price_pnl", "profit_ticks", "pnl", "comment", "source_file",
    ]
    return _insert_ignore(conn, "atas_journal", cols, rows)


def insert_statistics(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    # Statistics are per-source-file; replace so re-imports refresh values.
    cols = ["source_file", "metric", "scope", "value"]
    data = [tuple(r[c] for c in cols) for r in rows]
    if not data:
        return 0
    placeholders = ",".join("?" for _ in cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO atas_statistics ({','.join(cols)}) VALUES ({placeholders})",
        data,
    )
    conn.commit()
    return len(data)


def _insert_ignore(
    conn: sqlite3.Connection, table: str, cols: list[str], rows: Iterable[dict]
) -> int:
    data = [tuple(r[c] for c in cols) for r in rows]
    if not data:
        return 0
    placeholders = ",".join("?" for _ in cols)
    cur = conn.executemany(
        f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        data,
    )
    conn.commit()
    return cur.rowcount


def mark_imported(conn: sqlite3.Connection, source_file: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO imported_files (source_file, imported_at) "
        "VALUES (?, datetime('now'))",
        (source_file,),
    )
    conn.commit()


def imported_files(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT source_file FROM imported_files ORDER BY 1")]


# --- Read helpers --------------------------------------------------------
def load_executions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM executions", conn)
    if not df.empty:
        df["ts_local"] = pd.to_datetime(df["ts_local"], format="ISO8601")
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="ISO8601")
    return df


def load_journal(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM atas_journal", conn)
    for c in ("open_ts_local", "close_ts_local", "open_ts_utc", "close_ts_utc"):
        if c in df and not df.empty:
            df[c] = pd.to_datetime(df[c], format="ISO8601")
    return df


def load_statistics(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM atas_statistics", conn)


def get_note(conn: sqlite3.Connection, trade_key: str) -> dict:
    row = conn.execute(
        "SELECT note, tags_json FROM trade_notes WHERE trade_key = ?", (trade_key,)
    ).fetchone()
    if row is None:
        return {"note": "", "tags_json": "[]"}
    return {"note": row["note"] or "", "tags_json": row["tags_json"] or "[]"}


def save_note(conn: sqlite3.Connection, trade_key: str, note: str, tags_json: str) -> None:
    conn.execute(
        "INSERT INTO trade_notes (trade_key, note, tags_json, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(trade_key) DO UPDATE SET "
        "note=excluded.note, tags_json=excluded.tags_json, updated_at=excluded.updated_at",
        (trade_key, note, tags_json),
    )
    conn.commit()


def all_notes(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM trade_notes", conn)
