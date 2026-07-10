"""SQLite schema, connection, and idempotent upserts.

Re-importing overlapping ATAS files must never double-count, so every insert
uses INSERT OR IGNORE against a stable dedupe key:
  - executions: Exchange ID (unique per fill)
  - atas_journal: hash of account/instrument/open/close/prices/pnl
  - atas_statistics: (source_file, metric, scope)
"""

from __future__ import annotations

import json
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
    trade_key        TEXT PRIMARY KEY,
    note             TEXT,
    tags_json        TEXT,
    setups_json      TEXT DEFAULT '[]',   -- setup badges (per-trade)
    confluences_json TEXT DEFAULT '[]',   -- evidence/context badges (per-trade)
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS day_notes (
    day           TEXT PRIMARY KEY,   -- ISO date in the display tz it was tagged from
    note          TEXT,
    tags_json     TEXT,
    updated_at    TEXT
);

-- Canonical setup/confluence names, independent of any trade. Lets a name
-- exist (pre-seeded or created in the management UI) before it's ever tagged,
-- and gives each an editable description. Trades still carry their own tag
-- arrays in trade_notes.{setups_json,confluences_json}; these tables are the
-- master list those badges are picked from.
CREATE TABLE IF NOT EXISTS setups (
    name          TEXT PRIMARY KEY,
    description   TEXT DEFAULT '',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS confluences (
    name          TEXT PRIMARY KEY,
    description   TEXT DEFAULT '',
    created_at    TEXT
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

-- A session is one ATAS export (one source_file) — the same key attempt_videos
-- and video_bookmarks already use, so linking sessions costs no migration.
--   live     — prop firm / real money
--   replay   — a simulated re-run of a past session
--   backtest — one model exercised exclusively for the whole session
-- ``model_id`` only carries meaning for backtests, where it binds every trade in
-- the session; otherwise a trade's model comes from ``trade_model``.
CREATE TABLE IF NOT EXISTS sessions (
    source_file TEXT PRIMARY KEY,
    mode        TEXT NOT NULL DEFAULT 'replay',
    account     TEXT,
    model_id    INTEGER,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);

-- A trading model: the fixed approach a trade is executed under. Unlike the
-- name-keyed setups/confluences above this uses an integer surrogate PK, because
-- three tables reference it and a rename must not cascade through them.
CREATE TABLE IF NOT EXISTS models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT
);

-- The entry rules a model declares. ``active`` is a soft delete: retiring a rule
-- must not rewrite the compliance score of trades already checked against it.
CREATE TABLE IF NOT EXISTS model_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   INTEGER NOT NULL,
    label      TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT
);

-- Exactly one model per trade, or none (NULL = off-model). A strict partition,
-- so per-model PnL plus the unassigned bucket sums to the scope total. Keyed by
-- the LOGICAL trade key so the badge survives a logical<->ATAS view switch.
CREATE TABLE IF NOT EXISTS trade_model (
    trade_key  TEXT PRIMARY KEY,
    model_id   INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS trade_rule_checks (
    trade_key TEXT NOT NULL,
    rule_id   INTEGER NOT NULL,
    met       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trade_key, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_model_rules_model ON model_rules(model_id);
"""

# One-time curated seed for the setup/confluence master lists (option (b):
# name + description). Applied once, guarded by the ``setup_confluence_seeded``
# setting so a user who deletes a seeded entry doesn't have it resurrected on
# the next startup. INSERT OR IGNORE means it never clobbers a name a user (or
# the backfill) already created.
SEED_SETUPS: list[tuple[str, str]] = [
    ("Failed Breakdown / Bear Trap",
     "Price breaks below a range/level, fails to follow through, sellers get "
     "absorbed and price reclaims. A fade by direction (against the breakdown), "
     "bounce-like by discipline. Reversal thesis. Invalidation: back below the "
     "absorption zone / the low."),
    ("Spring / Range Reclaim",
     "Wyckoff spring: a false breakdown below a range low that reclaims back "
     "inside the range, trapping sellers. Typically an uptrend pullback -> "
     "continuation/breakout thesis. Same trap family as Failed Breakdown but "
     "with a range + continuation context."),
    ("Value-Area Fade",
     "Mean-reversion range play: long on absorption at/below VAL, short at/above "
     "VAH, target POC. A bounce (bet the edge holds). Regime-dependent - only "
     "valid in balance/range, not trend. Invalidation: acceptance (volume "
     "building) outside the value area."),
    ("Level Bounce",
     "Bet a level holds and trade the rejection. Works on pre-existing S/R, "
     "dynamic levels (VWAP/EMA/trendline), or a freshly-flipped breakout level "
     "(a breakout-retest is a Level Bounce on a new S-R flip). Strength comes "
     "from stacked confluences at the touch."),
    ("Breakout (Break-Entry)",
     "Initiative/momentum entry with the move - e.g. a resting buy-stop above "
     "the prior local high, filled on the thrust. The only setup needing no "
     "level-hold. Confirmed by acceptance, stacked imbalances/delta expansion, "
     "volume expansion. Often paired with a tight trailing stop."),
]

SEED_CONFLUENCES: list[tuple[str, str]] = [
    ("Absorption",
     "Large passive orders absorbing aggressive market orders without price "
     "moving - the side being hit is defending. Confirms responsive setups."),
    ("Footprint Rejection",
     "Footprint shows aggressive orders failing at a level (rejection wick / "
     "drying delta) - confirms a level holding."),
    ("Footprint Reclaim",
     "Footprint shows the opposite side stepping in past the prior aggression "
     "zone (e.g. greens above the selling area), confirming a reclaim/reversal."),
    ("Liquidity Grab / Sweep",
     "Price sweeps a pool of resting stops beyond a level, fills size, then "
     "reverses (stop hunt). The mechanism behind springs/traps."),
    ("Aggressive Initiative Buying",
     "Aggressive market buyers lifting offers, initiating an up-move (vs passive "
     "absorption). Confirms momentum/initiative setups."),
    ("Stacked Imbalances / Delta Expansion",
     "Consecutive footprint imbalances / expanding delta in the move's "
     "direction - confirms genuine momentum vs a fake."),
    ("Range Reclaim",
     "Price returns back inside a prior range after breaking out of it (a false "
     "break)."),
    ("Acceptance / Follow-Through",
     "Price trades and stays beyond a level with volume building there - "
     "confirms a breakout is real (opposite of a failed break)."),
    ("Volume Expansion",
     "A surge in volume on the move/break vs the prior drift - supports genuine "
     "momentum."),
    ("HTF Trend Alignment",
     "The higher-timeframe trend agrees with the trade direction (with-trend > "
     "counter-trend)."),
    ("Balanced / Range Regime",
     "Market is balancing/rotating (no trend) - the regime in which value-area "
     "fades are valid."),
    ("Break From Tight Base",
     "Breakout originates from a tight, coiled base/range rather than mid-chop."),
    ("VAL Sesh", "Value Area Low of the current/regular session volume profile."),
    ("VAH Sesh", "Value Area High of the current/regular session volume profile."),
    ("POC Sesh", "Point of Control (highest-volume price) of the current/regular session."),
    ("VAL ON", "Value Area Low of the overnight (ON) session volume profile."),
    ("VAH ON", "Value Area High of the overnight (ON) session volume profile."),
    ("POC ON", "Point of Control of the overnight (ON) session profile."),
    ("Big Buys", "Notably large buy orders/prints hitting the tape."),
    ("Big Sells", "Notably large sell orders/prints hitting the tape."),
    ("Uptrend", "Price in a higher-highs / higher-lows uptrend on the working timeframe."),
    ("Downtrend", "Price in a lower-highs / lower-lows downtrend on the working timeframe."),
    ("VWAP Middle", "Price interacting with the VWAP line itself."),
    ("VWAP Upper", "Price at the upper VWAP band / standard-deviation level."),
    ("VWAP Lower", "Price at the lower VWAP band / standard-deviation level."),
    ("PDH", "Prior Day High."),
    ("PDL", "Prior Day Low."),
    ("PDC", "Prior Day Close."),
]

# Table -> the trade_notes JSON column that mirrors it. Used to validate the
# (internally-supplied, never user-supplied) table name before f-stringing it
# into SQL, and to know which per-trade column a rename/delete must sweep.
_TAXONOMY: dict[str, str] = {"setups": "setups_json", "confluences": "confluences_json"}


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
    _migrate_trade_note_tagging(conn)
    _migrate_setup_confluence_master(conn)  # needs trade-note columns above
    _migrate_journal_model(conn)


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


def _migrate_trade_note_tagging(conn: sqlite3.Connection) -> None:
    """Add ``setups_json`` / ``confluences_json`` to installs whose
    ``trade_notes`` predate the setup/confluence tagging dimensions.

    Both are per-trade JSON arrays (like ``tags_json``); existing rows get the
    column default of '[]' so they read as "untagged" until edited. The setup
    dimension shipped first as ``playbooks_json``; rename it in place (data
    preserved) so the column matches the "Setup" vocabulary.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_notes)")}
    if "confluences_json" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN confluences_json TEXT DEFAULT '[]'")
    if "setups_json" not in cols:
        if "playbooks_json" in cols:
            conn.execute("ALTER TABLE trade_notes RENAME COLUMN playbooks_json TO setups_json")
        else:
            conn.execute("ALTER TABLE trade_notes ADD COLUMN setups_json TEXT DEFAULT '[]'")
    conn.commit()


def _migrate_setup_confluence_master(conn: sqlite3.Connection) -> None:
    """Populate the ``setups`` / ``confluences`` master tables.

    Two parts, both idempotent:

    1. **Backfill** — every distinct setup/confluence name already tagged on a
       trade is inserted (INSERT OR IGNORE, empty description) so existing data
       shows up in the management UI. Safe to run every startup: it only ever
       adds names that are already in use.
    2. **Seed** — the curated :data:`SEED_SETUPS` / :data:`SEED_CONFLUENCES`
       list (name + description) is inserted once, guarded by the
       ``setup_confluence_seeded`` setting. The guard means deleting a seeded
       entry sticks; INSERT OR IGNORE means seeding never overwrites a name the
       backfill or user already created.
    """
    # 1. Backfill from names already tagged on trades.
    notes = conn.execute(
        "SELECT setups_json, confluences_json FROM trade_notes"
    ).fetchall()
    seen_setups: set[str] = set()
    seen_confs: set[str] = set()
    for row in notes:
        seen_setups.update(json.loads(row["setups_json"] or "[]"))
        seen_confs.update(json.loads(row["confluences_json"] or "[]"))
    for name in seen_setups:
        conn.execute(
            "INSERT OR IGNORE INTO setups (name, description, created_at) "
            "VALUES (?, '', datetime('now'))",
            (name,),
        )
    for name in seen_confs:
        conn.execute(
            "INSERT OR IGNORE INTO confluences (name, description, created_at) "
            "VALUES (?, '', datetime('now'))",
            (name,),
        )

    # 2. One-time curated seed.
    if get_setting(conn, "setup_confluence_seeded") != "1":
        for name, desc in SEED_SETUPS:
            conn.execute(
                "INSERT OR IGNORE INTO setups (name, description, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (name, desc),
            )
        for name, desc in SEED_CONFLUENCES:
            conn.execute(
                "INSERT OR IGNORE INTO confluences (name, description, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (name, desc),
            )
        save_setting(conn, "setup_confluence_seeded", "1")
    conn.commit()


def _migrate_journal_model(conn: sqlite3.Connection) -> None:
    """Back-fill ``sessions`` for already-imported exports and seed ``models``.

    Both halves are one-time, each guarded by its own settings key (the
    ``setup_confluence_seeded`` pattern), and both use INSERT OR IGNORE so a
    re-run can never re-archive a session the user un-archived, clobber a manual
    ``backtest`` binding, or resurrect a deleted model.

    The cutover archives every pre-existing session: the trading approach changed,
    so the old era is browsable but out of the default statistics. ``mode`` is
    inferred from the accounts on the export's rows — a file whose every row is
    the ``Replay`` account is a replay, anything else touched a real account and
    counts as live. Old setup badges are deliberately *not* mapped onto
    ``trade_model``: a trade historically carried 0..n setups and a model is
    exactly 1, so any automatic mapping would be semantically wrong.
    """
    if get_setting(conn, "sessions_cutover_done") != "1":
        rows = conn.execute(
            "SELECT source_file, account, COUNT(*) AS n FROM atas_journal "
            "GROUP BY source_file, account"
        ).fetchall()
        by_file: dict[str, list[tuple[str, int]]] = {}
        for r in rows:
            by_file.setdefault(r["source_file"], []).append((r["account"], r["n"]))
        for source_file, accounts in by_file.items():
            mode = "replay" if all(a == "Replay" for a, _ in accounts) else "live"
            modal = max(accounts, key=lambda p: p[1])[0]
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(source_file, mode, account, model_id, archived, created_at, updated_at) "
                "VALUES (?, ?, ?, NULL, 1, datetime('now'), datetime('now'))",
                (source_file, mode, modal),
            )
        save_setting(conn, "sessions_cutover_done", "1")

    if get_setting(conn, "models_seeded") != "1":
        for name, desc in SEED_SETUPS:
            conn.execute(
                "INSERT OR IGNORE INTO models (name, description, archived, created_at) "
                "VALUES (?, ?, 0, datetime('now'))",
                (name, desc),
            )
        save_setting(conn, "models_seeded", "1")
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
        "SELECT note, tags_json, setups_json, confluences_json "
        "FROM trade_notes WHERE trade_key = ?",
        (trade_key,),
    ).fetchone()
    if row is None:
        return {"note": "", "tags_json": "[]", "setups_json": "[]", "confluences_json": "[]"}
    return {
        "note": row["note"] or "",
        "tags_json": row["tags_json"] or "[]",
        "setups_json": row["setups_json"] or "[]",
        "confluences_json": row["confluences_json"] or "[]",
    }


def save_note(
    conn: sqlite3.Connection,
    trade_key: str,
    note: str,
    tags_json: str,
    setups_json: str = "[]",
    confluences_json: str = "[]",
) -> None:
    conn.execute(
        "INSERT INTO trade_notes "
        "(trade_key, note, tags_json, setups_json, confluences_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(trade_key) DO UPDATE SET "
        "note=excluded.note, tags_json=excluded.tags_json, "
        "setups_json=excluded.setups_json, "
        "confluences_json=excluded.confluences_json, "
        "updated_at=excluded.updated_at",
        (trade_key, note, tags_json, setups_json, confluences_json),
    )
    conn.commit()


def all_notes(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM trade_notes", conn)


# --- Setup / confluence master lists -------------------------------------
# Two near-identical taxonomies (setups, confluences) share one set of helpers
# parameterised by table name. The table is always one of the internal
# ``_TAXONOMY`` keys (never user input), so f-stringing it into SQL is safe; we
# assert it anyway to make that contract explicit.
def _taxonomy_col(table: str) -> str:
    col = _TAXONOMY.get(table)
    if col is None:
        raise ValueError(f"unknown taxonomy table: {table!r}")
    return col


def _sweep_trade_tags(
    conn: sqlite3.Connection, json_col: str, old: str, new: str | None
) -> None:
    """Rewrite ``old`` -> ``new`` (or drop it when ``new`` is None) in every
    trade's tag array for ``json_col``. The trade row itself always survives —
    only the one badge is renamed/removed. Dedupes on rename so a trade already
    carrying ``new`` doesn't end up with it twice."""
    rows = conn.execute(
        f"SELECT trade_key, {json_col} AS j FROM trade_notes"
    ).fetchall()
    for r in rows:
        arr = json.loads(r["j"] or "[]")
        if old not in arr:
            continue
        if new is None:
            arr = [x for x in arr if x != old]
        else:
            arr = [new if x == old else x for x in arr]
            seen: set[str] = set()
            arr = [x for x in arr if not (x in seen or seen.add(x))]
        conn.execute(
            f"UPDATE trade_notes SET {json_col} = ?, updated_at = datetime('now') "
            "WHERE trade_key = ?",
            (json.dumps(arr), r["trade_key"]),
        )


def list_taxonomy(conn: sqlite3.Connection, table: str) -> list[dict]:
    """All names + descriptions in a master list, A→Z (case-insensitive)."""
    _taxonomy_col(table)
    rows = conn.execute(
        f"SELECT name, description FROM {table} ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [{"name": r["name"], "description": r["description"] or ""} for r in rows]


def create_taxonomy(
    conn: sqlite3.Connection, table: str, name: str, description: str = ""
) -> None:
    """Add a name to a master list. No-op if it already exists (INSERT OR
    IGNORE) so this is safe both for the management UI and for auto-registering
    inline-typed badges."""
    _taxonomy_col(table)
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    conn.execute(
        f"INSERT OR IGNORE INTO {table} (name, description, created_at) "
        "VALUES (?, ?, datetime('now'))",
        (name, description),
    )
    conn.commit()


def register_taxonomy(conn: sqlite3.Connection, table: str, names: Iterable[str]) -> None:
    """Bulk INSERT OR IGNORE — used when saving a note to fold any newly typed
    badge names into the master list (description left blank)."""
    _taxonomy_col(table)
    for raw in names:
        name = (raw or "").strip()
        if name:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} (name, description, created_at) "
                "VALUES (?, '', datetime('now'))",
                (name,),
            )
    conn.commit()


def update_taxonomy(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    new_name: str | None = None,
    description: str | None = None,
) -> None:
    """Rename and/or re-describe a master-list entry.

    A rename **cascades**: the badge is rewritten on every trade carrying it, so
    no trade silently loses its tag. If the target name already exists the two
    merge (the old master row is dropped and its trades fold onto the survivor).
    Passing only ``description`` edits the blurb in place.
    """
    json_col = _taxonomy_col(table)
    target = (new_name or name).strip()
    if not target:
        raise ValueError("name is required")
    desc = description if description is not None else None

    if target == name:
        if desc is not None:
            conn.execute(
                f"UPDATE {table} SET description = ? WHERE name = ?", (desc, name)
            )
        conn.commit()
        return

    # Renaming to a different name.
    exists = conn.execute(
        f"SELECT 1 FROM {table} WHERE name = ?", (target,)
    ).fetchone()
    if exists:
        conn.execute(f"DELETE FROM {table} WHERE name = ?", (name,))
        if desc is not None:
            conn.execute(
                f"UPDATE {table} SET description = ? WHERE name = ?", (desc, target)
            )
    else:
        if desc is not None:
            conn.execute(
                f"UPDATE {table} SET name = ?, description = ? WHERE name = ?",
                (target, desc, name),
            )
        else:
            conn.execute(
                f"UPDATE {table} SET name = ? WHERE name = ?", (target, name)
            )
    _sweep_trade_tags(conn, json_col, name, target)
    conn.commit()


def delete_taxonomy(conn: sqlite3.Connection, table: str, name: str) -> None:
    """Remove a name from the master list and strip the badge from every trade
    that carried it. The trades themselves are untouched — they just lose this
    one tag."""
    json_col = _taxonomy_col(table)
    conn.execute(f"DELETE FROM {table} WHERE name = ?", (name,))
    _sweep_trade_tags(conn, json_col, name, None)
    conn.commit()


# --- Sessions ------------------------------------------------------------
# One row per ATAS export. The ingest path creates them un-archived; the cutover
# created the historical ones archived. Only ``upsert_session`` is called from
# ingest, and it never overwrites — so a mode/archive choice made in the UI
# survives re-importing the same export.
def upsert_session(
    conn: sqlite3.Connection, source_file: str, mode: str, account: str | None = None
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(source_file, mode, account, model_id, archived, created_at, updated_at) "
        "VALUES (?, ?, ?, NULL, 0, datetime('now'), datetime('now'))",
        (source_file, mode, account),
    )
    conn.commit()


def sessions_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """{source_file: {mode, account, model_id, archived}} for scope resolution."""
    return {
        r["source_file"]: {
            "mode": r["mode"],
            "account": r["account"],
            "model_id": r["model_id"],
            "archived": bool(r["archived"]),
        }
        for r in conn.execute(
            "SELECT source_file, mode, account, model_id, archived FROM sessions"
        )
    }


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT s.source_file, s.mode, s.account, s.model_id, s.archived, s.updated_at, "
        "       m.name AS model_name "
        "FROM sessions s LEFT JOIN models m ON m.id = s.model_id "
        "ORDER BY s.source_file"
    ).fetchall()
    return [{**dict(r), "archived": bool(r["archived"])} for r in rows]


def update_session(
    conn: sqlite3.Connection,
    source_file: str,
    mode: str | None = None,
    model_id: int | None = None,
    archived: bool | None = None,
    clear_model: bool = False,
) -> None:
    """Patch a session; unspecified fields are left as-is.

    ``model_id`` only binds trades when ``mode='backtest'``; pass ``clear_model``
    to unbind (a plain ``model_id=None`` means "don't touch", matching the other
    optional fields).
    """
    sets: list[str] = []
    params: list[object] = []
    if mode is not None:
        sets.append("mode = ?")
        params.append(mode)
    if clear_model:
        sets.append("model_id = NULL")
    elif model_id is not None:
        sets.append("model_id = ?")
        params.append(model_id)
    if archived is not None:
        sets.append("archived = ?")
        params.append(1 if archived else 0)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(source_file)
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE source_file = ?", params)
    conn.commit()


# --- Models + their rule checklists ---------------------------------------
def list_models(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    """Models A→Z, each with its rules. Archived rules are excluded from the
    checklist, but stay in the DB so old trades' compliance scores keep meaning."""
    where = "" if include_archived else "WHERE archived = 0"
    models = conn.execute(
        f"SELECT id, name, description, archived FROM models {where} "
        "ORDER BY name COLLATE NOCASE"
    ).fetchall()
    rules = conn.execute(
        "SELECT id, model_id, label, sort_order FROM model_rules "
        "WHERE active = 1 ORDER BY sort_order, id"
    ).fetchall()
    by_model: dict[int, list[dict]] = {}
    for r in rules:
        by_model.setdefault(r["model_id"], []).append(dict(r))
    return [
        {
            "id": m["id"],
            "name": m["name"],
            "description": m["description"] or "",
            "archived": bool(m["archived"]),
            "rules": by_model.get(m["id"], []),
        }
        for m in models
    ]


def create_model(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    cur = conn.execute(
        "INSERT INTO models (name, description, archived, created_at) "
        "VALUES (?, ?, 0, datetime('now'))",
        (name, description),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_model(
    conn: sqlite3.Connection,
    model_id: int,
    name: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
) -> None:
    sets: list[str] = []
    params: list[object] = []
    if name is not None:
        if not name.strip():
            raise ValueError("name is required")
        sets.append("name = ?")
        params.append(name.strip())
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if archived is not None:
        sets.append("archived = ?")
        params.append(1 if archived else 0)
    if not sets:
        return
    params.append(model_id)
    conn.execute(f"UPDATE models SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def archive_model(conn: sqlite3.Connection, model_id: int) -> None:
    """Soft-delete: the model leaves the picker but trades tagged with it keep
    resolving, so historical per-model stats never silently reshuffle."""
    update_model(conn, model_id, archived=True)


def list_rules(
    conn: sqlite3.Connection, model_id: int, include_inactive: bool = False
) -> list[dict]:
    where = "" if include_inactive else "AND active = 1"
    rows = conn.execute(
        f"SELECT id, model_id, label, sort_order, active FROM model_rules "
        f"WHERE model_id = ? {where} ORDER BY sort_order, id",
        (model_id,),
    ).fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]


def create_rule(
    conn: sqlite3.Connection, model_id: int, label: str, sort_order: int | None = None
) -> int:
    label = label.strip()
    if not label:
        raise ValueError("label is required")
    if sort_order is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM model_rules "
            "WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        sort_order = int(row["n"])
    cur = conn.execute(
        "INSERT INTO model_rules (model_id, label, sort_order, active, created_at) "
        "VALUES (?, ?, ?, 1, datetime('now'))",
        (model_id, label, sort_order),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    label: str | None = None,
    sort_order: int | None = None,
    active: bool | None = None,
) -> None:
    sets: list[str] = []
    params: list[object] = []
    if label is not None:
        if not label.strip():
            raise ValueError("label is required")
        sets.append("label = ?")
        params.append(label.strip())
    if sort_order is not None:
        sets.append("sort_order = ?")
        params.append(sort_order)
    if active is not None:
        sets.append("active = ?")
        params.append(1 if active else 0)
    if not sets:
        return
    params.append(rule_id)
    conn.execute(f"UPDATE model_rules SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def retire_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    """Soft-delete a rule. Its ``trade_rule_checks`` rows stay, so a trade
    scored 3/4 against the old checklist still reads 3/4."""
    update_rule(conn, rule_id, active=False)


# --- Per-trade model assignment + rule compliance -------------------------
# All keyed by the LOGICAL trade key (see ``trades.lot_to_logical_map``), so a
# trade journaled in logical view keeps its model when viewed as ATAS rows.
def get_trade_model(conn: sqlite3.Connection, trade_key: str) -> int | None:
    row = conn.execute(
        "SELECT model_id FROM trade_model WHERE trade_key = ?", (trade_key,)
    ).fetchone()
    return None if row is None else row["model_id"]


def trade_model_map(conn: sqlite3.Connection) -> dict[str, int]:
    """{logical trade_key: model_id} for rows that actually name a model.

    Rows with a NULL ``model_id`` (explicitly marked off-model) are omitted —
    they resolve the same as an absent row, and leaving them out keeps the
    caller's ``.get(key)`` returning None either way.
    """
    return {
        r["trade_key"]: r["model_id"]
        for r in conn.execute(
            "SELECT trade_key, model_id FROM trade_model WHERE model_id IS NOT NULL"
        )
    }


def set_trade_model(conn: sqlite3.Connection, trade_key: str, model_id: int | None) -> None:
    conn.execute(
        "INSERT INTO trade_model (trade_key, model_id, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(trade_key) DO UPDATE SET "
        "model_id=excluded.model_id, updated_at=excluded.updated_at",
        (trade_key, model_id),
    )
    conn.commit()


def get_rule_checks(conn: sqlite3.Connection, trade_key: str) -> dict[int, bool]:
    return {
        r["rule_id"]: bool(r["met"])
        for r in conn.execute(
            "SELECT rule_id, met FROM trade_rule_checks WHERE trade_key = ?", (trade_key,)
        )
    }


def set_rule_checks(
    conn: sqlite3.Connection, trade_key: str, model_id: int | None, rules_met: Iterable[int]
) -> None:
    """Record which of ``model_id``'s active rules this trade met.

    Every check whose rule doesn't belong to the trade's current model is swept —
    changing a trade's model must not leave the previous model's checks behind,
    where they'd inflate the new model's compliance denominator. Rows are written
    for *all* the model's active rules (met 0 or 1), so "unmet" and "never
    reviewed" stay distinguishable: a trade with no rows was never scored.
    """
    conn.execute("DELETE FROM trade_rule_checks WHERE trade_key = ?", (trade_key,))
    if model_id is not None:
        met = set(rules_met)
        for rule in list_rules(conn, model_id):
            conn.execute(
                "INSERT INTO trade_rule_checks (trade_key, rule_id, met) VALUES (?, ?, ?)",
                (trade_key, rule["id"], 1 if rule["id"] in met else 0),
            )
    conn.commit()


def all_rule_checks(conn: sqlite3.Connection) -> dict[str, dict[int, bool]]:
    """{trade_key: {rule_id: met}} — one scan, for the /models/stats aggregation."""
    out: dict[str, dict[int, bool]] = {}
    for r in conn.execute("SELECT trade_key, rule_id, met FROM trade_rule_checks"):
        out.setdefault(r["trade_key"], {})[r["rule_id"]] = bool(r["met"])
    return out


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


def linked_video_source_files(conn: sqlite3.Connection) -> set[str]:
    """Every ``source_file`` that has a recording linked.

    Used by the calendar to badge days whose attempts carry a video, without a
    per-day round-trip. "Linked" only — disk existence isn't checked here (that
    would mean resolving every path on each calendar render)."""
    return {
        r[0] for r in conn.execute("SELECT source_file FROM attempt_videos")
    }


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
