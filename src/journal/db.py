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
    ts_local      TEXT,   -- source-tz ISO string (tz captured at import)
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

CREATE TABLE IF NOT EXISTS day_notes (
    day           TEXT PRIMARY KEY,   -- ISO date in the display tz it was tagged from
    note          TEXT,
    tags_json     TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS imported_files (
    source_file   TEXT PRIMARY KEY,
    imported_at   TEXT,   -- when we ingested it (UTC)
    file_mtime    TEXT    -- the export's own modified time (Windows "Date modified", UTC)
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

CREATE TABLE IF NOT EXISTS attempt_videos (
    source_file   TEXT PRIMARY KEY,   -- the replay take this video belongs to
    path          TEXT NOT NULL,      -- as entered (Windows or POSIX); resolved on serve
    duration_s    REAL,               -- known once the browser reads metadata; nullable
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS video_bookmarks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file   TEXT NOT NULL,      -- the replay take this bookmark belongs to
    offset_s      REAL NOT NULL,      -- seconds into that take's video
    label         TEXT,
    trade_key     TEXT,               -- bound trade (NULL = free-form bookmark)
    created_at    TEXT,
    origin        TEXT NOT NULL DEFAULT 'manual'  -- 'manual' (hand-placed/anchor) | 'synced' (auto from trade ts)
);

CREATE INDEX IF NOT EXISTS idx_video_bookmarks_sf ON video_bookmarks(source_file);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: Streamlit reruns the script across worker threads
    # but serializes runs per session, so sharing one cached connection is safe.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    _migrate_video_schema(conn)  # must run before SCHEMA recreates the tables
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_ai_schema(conn)
    _migrate_imported_files(conn)
    _migrate_bookmark_origin(conn)


def _migrate_video_schema(conn: sqlite3.Connection) -> None:
    """Drop pre-rekey video tables so SCHEMA can recreate them keyed by
    ``source_file`` instead of ``day``.

    An early build keyed videos/bookmarks by ``day``; we switched to the stable
    ``source_file`` (the attempt id). ``CREATE TABLE IF NOT EXISTS`` would skip
    the old tables, then the new ``source_file`` index would fail against the
    old ``day`` columns. These tables only ever held throwaway pre-release data,
    so dropping is safe — relink the recording to recreate.
    """
    conn.execute("DROP TABLE IF EXISTS day_videos")  # renamed to attempt_videos
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='video_bookmarks'"
    ).fetchone()
    if row is not None and "source_file" not in (row[0] or ""):
        conn.execute("DROP INDEX IF EXISTS idx_video_bookmarks_day")
        conn.execute("DROP TABLE IF EXISTS video_bookmarks")
    conn.commit()


def _migrate_imported_files(conn: sqlite3.Connection) -> None:
    """Add ``file_mtime`` to installs that predate the export-modified-time card.

    Existing rows get NULL (we never captured the original file date), so their
    day shows "—" for Modified until re-uploaded.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(imported_files)")}
    if "file_mtime" not in cols:
        conn.execute("ALTER TABLE imported_files ADD COLUMN file_mtime TEXT")
        conn.commit()


def _migrate_bookmark_origin(conn: sqlite3.Connection) -> None:
    """Add ``origin`` to installs whose video_bookmarks predate auto-sync.

    Existing rows were all hand-placed, so they default to 'manual' — exactly
    right, since "Clear synced" and orphan-pruning must never touch them.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_bookmarks)")}
    if "origin" not in cols:
        conn.execute(
            "ALTER TABLE video_bookmarks ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'"
        )
        conn.commit()


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


def mark_imported(
    conn: sqlite3.Connection, source_file: str, file_mtime: str | None = None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO imported_files (source_file, imported_at, file_mtime) "
        "VALUES (?, datetime('now'), ?)",
        (source_file, file_mtime),
    )
    conn.commit()


def imported_files(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT source_file FROM imported_files ORDER BY 1")]


def imported_at_map(conn: sqlite3.Connection) -> dict[str, str]:
    """{source_file: imported_at} (UTC ISO from ``datetime('now')``).

    Drives replay-attempt ordering: a calendar day can hold several source
    files (re-done takes), and the most recently imported one is the day's
    canonical "latest attempt".
    """
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT source_file, imported_at FROM imported_files")
    }


def file_mtime_map(conn: sqlite3.Connection) -> dict[str, str]:
    """{source_file: file_mtime} (the export's own modified time, UTC ISO).

    Shown on the day's "Modified" card. NULL for files imported before the
    column existed, or imported without a captured mtime.
    """
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT source_file, file_mtime FROM imported_files")
        if r[1] is not None
    }


def delete_attempt(conn: sqlite3.Connection, source_file: str) -> dict[str, int]:
    """Delete every row from one replay attempt (one source file).

    Removes the file's executions, journal trades, per-file statistics, and its
    imported-files entry so the same export can be re-uploaded fresh. Used to
    drop a junk take without touching the other attempts of that day.
    """
    counts: dict[str, int] = {}
    for table in ("executions", "atas_journal", "atas_statistics", "imported_files"):
        cur = conn.execute(f"DELETE FROM {table} WHERE source_file = ?", (source_file,))
        counts[table] = cur.rowcount
    conn.commit()
    return counts


def delete_day(
    conn: sqlite3.Connection,
    day: str,
    account: str | None = None,
    instrument: str | None = None,
) -> dict[str, int]:
    """Delete executions and journal rows whose KL-local date equals ``day``.

    ``atas_statistics`` is left alone — it's keyed by source_file, so a
    replayed re-import overwrites cleanly via INSERT OR REPLACE. ``trade_notes``
    and ``ai_trade_analysis`` are also kept: notes are user content, and a
    bit-identical replay reattaches them via stable trade_key.
    """
    j_where = ["substr(open_ts_local, 1, 10) = ?"]
    e_where = ["substr(ts_local, 1, 10) = ?"]
    params: list[str] = [day]
    if account:
        j_where.append("account = ?")
        e_where.append("account = ?")
        params.append(account)
    if instrument:
        j_where.append("instrument = ?")
        e_where.append("instrument = ?")
        params.append(instrument)
    j_cur = conn.execute(
        f"DELETE FROM atas_journal WHERE {' AND '.join(j_where)}", params
    )
    e_cur = conn.execute(
        f"DELETE FROM executions WHERE {' AND '.join(e_where)}", params
    )
    conn.commit()
    return {"journal": j_cur.rowcount, "executions": e_cur.rowcount}


def delete_all_trades(conn: sqlite3.Connection) -> dict[str, int]:
    """Wipe every trade-derived row: executions, journal, per-file stats, and
    the imported-files log so the same filenames can be re-imported fresh.

    ``trade_notes`` and ``ai_*`` are intentionally kept — they're user/AI
    content keyed by trade_key and reattach automatically if you re-import
    the same data. Call ``delete_user_data`` if you want a total nuke.
    """
    counts: dict[str, int] = {}
    for table in ("executions", "atas_journal", "atas_statistics", "imported_files"):
        cur = conn.execute(f"DELETE FROM {table}")
        counts[table] = cur.rowcount
    conn.commit()
    return counts


# --- Read helpers --------------------------------------------------------
# ts_* columns are stored as ISO strings tagged with the source tz at import
# time. A DB can hold rows from multiple source tzs (e.g. older KL imports
# alongside newer NY ones), which pandas' default ISO8601 parser rejects with
# "Mixed timezones detected". Parsing with utc=True coerces all rows to a
# single tz-aware UTC column; the *_ts_local columns lose their per-row offset
# but that's fine — every downstream view rebuilds them from *_ts_utc via
# ``trades.localize`` in the user's display tz. The one exception is execution
# fills (read by AI), which ``api.scope`` re-projects into the display tz.
def load_executions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM executions", conn)
    if not df.empty:
        df["ts_local"] = pd.to_datetime(df["ts_local"], format="ISO8601", utc=True)
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="ISO8601", utc=True)
    return df


def load_journal(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM atas_journal", conn)
    for c in ("open_ts_local", "close_ts_local", "open_ts_utc", "close_ts_utc"):
        if c in df and not df.empty:
            df[c] = pd.to_datetime(df[c], format="ISO8601", utc=True)
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


def get_day_note(conn: sqlite3.Connection, day: str) -> dict:
    row = conn.execute(
        "SELECT note, tags_json FROM day_notes WHERE day = ?", (day,)
    ).fetchone()
    if row is None:
        return {"note": "", "tags_json": "[]"}
    return {"note": row["note"] or "", "tags_json": row["tags_json"] or "[]"}


def save_day_note(conn: sqlite3.Connection, day: str, note: str, tags_json: str) -> None:
    conn.execute(
        "INSERT INTO day_notes (day, note, tags_json, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(day) DO UPDATE SET "
        "note=excluded.note, tags_json=excluded.tags_json, updated_at=excluded.updated_at",
        (day, note, tags_json),
    )
    conn.commit()


def all_day_notes(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM day_notes", conn)


# --- Per-attempt video link + bookmarks ----------------------------------
# Each replay take (source_file) links to one recorded session video
# (referenced by on-disk path, never copied). The attempt "number" shown in the
# UI is positional and shifts when takes are deleted, so the stable key is the
# source_file. Bookmarks are pure metadata — an offset in seconds plus a label —
# independent of the file's location or format; trade_key binds one to a trade.
def get_attempt_video(conn: sqlite3.Connection, source_file: str) -> dict | None:
    row = conn.execute(
        "SELECT source_file, path, duration_s, updated_at "
        "FROM attempt_videos WHERE source_file = ?",
        (source_file,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def save_attempt_video(
    conn: sqlite3.Connection, source_file: str, path: str, duration_s: float | None = None
) -> None:
    conn.execute(
        "INSERT INTO attempt_videos (source_file, path, duration_s, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(source_file) DO UPDATE SET "
        "path=excluded.path, duration_s=excluded.duration_s, updated_at=excluded.updated_at",
        (source_file, path, duration_s),
    )
    conn.commit()


def delete_attempt_video(conn: sqlite3.Connection, source_file: str) -> None:
    """Unlink the attempt's video and drop its bookmarks (offsets are
    meaningless without the video they point into)."""
    conn.execute("DELETE FROM video_bookmarks WHERE source_file = ?", (source_file,))
    conn.execute("DELETE FROM attempt_videos WHERE source_file = ?", (source_file,))
    conn.commit()


def list_bookmarks(conn: sqlite3.Connection, source_file: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source_file, offset_s, label, trade_key, created_at, origin "
        "FROM video_bookmarks WHERE source_file = ? ORDER BY offset_s",
        (source_file,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_bookmark(
    conn: sqlite3.Connection,
    source_file: str,
    offset_s: float,
    label: str = "",
    trade_key: str | None = None,
    origin: str = "manual",
) -> dict:
    cur = conn.execute(
        "INSERT INTO video_bookmarks "
        "(source_file, offset_s, label, trade_key, created_at, origin) "
        "VALUES (?, ?, ?, ?, datetime('now'), ?)",
        (source_file, offset_s, label, trade_key, origin),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, source_file, offset_s, label, trade_key, created_at, origin "
        "FROM video_bookmarks WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return dict(row)


def update_bookmark(
    conn: sqlite3.Connection,
    bookmark_id: int,
    offset_s: float | None = None,
    label: str | None = None,
) -> dict | None:
    """Patch a bookmark's offset and/or label; unspecified fields are left as-is.

    Any hand edit promotes the row to ``origin='manual'``: once you've nudged a
    synced marker, it's yours, so "Clear synced" won't wipe your change.
    """
    sets: list[str] = []
    params: list[object] = []
    if offset_s is not None:
        sets.append("offset_s = ?")
        params.append(offset_s)
    if label is not None:
        sets.append("label = ?")
        params.append(label)
    if sets:
        sets.append("origin = 'manual'")
        params.append(bookmark_id)
        conn.execute(
            f"UPDATE video_bookmarks SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()
    row = conn.execute(
        "SELECT id, source_file, offset_s, label, trade_key, created_at, origin "
        "FROM video_bookmarks WHERE id = ?",
        (bookmark_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_bookmark(conn: sqlite3.Connection, bookmark_id: int) -> None:
    conn.execute("DELETE FROM video_bookmarks WHERE id = ?", (bookmark_id,))
    conn.commit()


def clear_synced_bookmarks(conn: sqlite3.Connection, source_file: str) -> int:
    """Delete every auto-synced bookmark for an attempt; manual rows survive.
    Returns the number removed."""
    cur = conn.execute(
        "DELETE FROM video_bookmarks WHERE source_file = ? AND origin = 'synced'",
        (source_file,),
    )
    conn.commit()
    return cur.rowcount


def prune_orphan_synced_bookmarks(
    conn: sqlite3.Connection, source_file: str, valid_trade_keys: list[str]
) -> int:
    """Drop synced bookmarks whose trade no longer exists (e.g. after a
    re-import shifted trade_keys). Manual rows are never pruned. Returns count.

    Synced markers are fully regenerable (just re-sync), so removing stale ones
    keeps the scrub bar honest with no user action.
    """
    keys = list(valid_trade_keys)
    placeholders = ",".join("?" for _ in keys)
    # NOT IN () is invalid SQL; with no valid keys, every synced row is orphaned.
    where_keys = f"AND trade_key NOT IN ({placeholders})" if keys else ""
    cur = conn.execute(
        f"DELETE FROM video_bookmarks "
        f"WHERE source_file = ? AND origin = 'synced' {where_keys}",
        (source_file, *keys),
    )
    conn.commit()
    return cur.rowcount


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
