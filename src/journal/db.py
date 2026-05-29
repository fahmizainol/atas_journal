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

CREATE TABLE IF NOT EXISTS ai_trade_analysis (
    trade_key     TEXT,
    model         TEXT,
    analysis_json TEXT,
    created_at    TEXT,
    PRIMARY KEY (trade_key, model)
);

CREATE TABLE IF NOT EXISTS ai_period_review (
    scope_sig       TEXT,
    model           TEXT,
    filters_json    TEXT,
    review_json     TEXT,
    trade_count     INTEGER,
    latest_trade_ts TEXT,
    created_at      TEXT,
    PRIMARY KEY (scope_sig, model)
);

CREATE TABLE IF NOT EXISTS ai_settings (
    key           TEXT PRIMARY KEY,
    value         TEXT
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
    _migrate_ai_schema(conn)


def _migrate_ai_schema(conn: sqlite3.Connection) -> None:
    """Rebuild AI tables that predate the per-model composite primary keys.

    Earlier installs keyed analyses by trade_key / scope_sig alone, so a new
    model's review would overwrite another's. Rebuild those tables with the
    composite PK, preserving existing rows (NULL model -> 'unknown').
    """
    def stale(table: str, marker: str) -> bool:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None and marker not in (row[0] or "")

    if stale("ai_trade_analysis", "PRIMARY KEY (trade_key, model)"):
        conn.executescript("""
            ALTER TABLE ai_trade_analysis RENAME TO ai_trade_analysis_old;
            CREATE TABLE ai_trade_analysis (
                trade_key TEXT, model TEXT, analysis_json TEXT, created_at TEXT,
                PRIMARY KEY (trade_key, model)
            );
            INSERT OR IGNORE INTO ai_trade_analysis
                (trade_key, model, analysis_json, created_at)
                SELECT trade_key, COALESCE(NULLIF(model,''),'unknown'),
                       analysis_json, created_at FROM ai_trade_analysis_old;
            DROP TABLE ai_trade_analysis_old;
        """)
    if stale("ai_period_review", "PRIMARY KEY (scope_sig, model)"):
        conn.executescript("""
            ALTER TABLE ai_period_review RENAME TO ai_period_review_old;
            CREATE TABLE ai_period_review (
                scope_sig TEXT, model TEXT, filters_json TEXT, review_json TEXT,
                trade_count INTEGER, latest_trade_ts TEXT, created_at TEXT,
                PRIMARY KEY (scope_sig, model)
            );
            INSERT OR IGNORE INTO ai_period_review
                (scope_sig, model, filters_json, review_json, trade_count,
                 latest_trade_ts, created_at)
                SELECT scope_sig, COALESCE(NULLIF(model,''),'unknown'), filters_json,
                       review_json, trade_count, latest_trade_ts, created_at
                FROM ai_period_review_old;
            DROP TABLE ai_period_review_old;
        """)
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


# --- AI analyzer persistence (keyed per model) ---------------------------
def get_trade_analyses(conn: sqlite3.Connection, trade_key: str) -> dict[str, dict]:
    """All saved per-model analyses for a trade, keyed by model name."""
    rows = conn.execute(
        "SELECT model, analysis_json, created_at FROM ai_trade_analysis "
        "WHERE trade_key = ? ORDER BY created_at",
        (trade_key,),
    ).fetchall()
    return {r["model"]: {"analysis_json": r["analysis_json"],
                         "created_at": r["created_at"]} for r in rows}


def save_trade_analysis(
    conn: sqlite3.Connection, trade_key: str, model: str, analysis_json: str
) -> None:
    conn.execute(
        "INSERT INTO ai_trade_analysis (trade_key, model, analysis_json, created_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(trade_key, model) DO UPDATE SET "
        "analysis_json=excluded.analysis_json, created_at=excluded.created_at",
        (trade_key, model, analysis_json),
    )
    conn.commit()


def get_period_reviews(conn: sqlite3.Connection, scope_sig: str) -> dict[str, dict]:
    """All saved per-model reviews for a scope, keyed by model name."""
    rows = conn.execute(
        "SELECT model, filters_json, review_json, trade_count, latest_trade_ts, "
        "created_at FROM ai_period_review WHERE scope_sig = ? ORDER BY created_at",
        (scope_sig,),
    ).fetchall()
    return {
        r["model"]: {
            "filters_json": r["filters_json"], "review_json": r["review_json"],
            "trade_count": r["trade_count"], "latest_trade_ts": r["latest_trade_ts"],
            "created_at": r["created_at"],
        } for r in rows
    }


def save_period_review(
    conn: sqlite3.Connection, scope_sig: str, model: str, filters_json: str,
    review_json: str, trade_count: int, latest_trade_ts: str | None,
) -> None:
    conn.execute(
        "INSERT INTO ai_period_review (scope_sig, model, filters_json, review_json, "
        "trade_count, latest_trade_ts, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(scope_sig, model) DO UPDATE SET "
        "filters_json=excluded.filters_json, review_json=excluded.review_json, "
        "trade_count=excluded.trade_count, latest_trade_ts=excluded.latest_trade_ts, "
        "created_at=excluded.created_at",
        (scope_sig, model, filters_json, review_json, trade_count, latest_trade_ts),
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM ai_settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] is None:
        return default
    return row["value"]


def save_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO ai_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
