"""SQLite schema, connection, and idempotent upserts.

Re-importing overlapping ATAS files must never double-count, so every insert
uses INSERT OR IGNORE against a stable dedupe key:
  - executions: Exchange ID (unique per fill)
  - atas_journal: hash of account/instrument/open/close/prices/pnl
  - atas_statistics: (source_file, metric, scope)
"""

from __future__ import annotations

import json
import re
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
    pnl           REAL,   -- gross, always
    fees          REAL,   -- commission both sides; NULL = never reported
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

-- The trader's own account of a trade. HUMAN-OWNED, and the counterpart to
-- ``trade_levels`` / ``trade_context`` below, which answer what they can without
-- a self-report.
--
-- ``grade`` and ``watched_levels_json`` are the review
-- (docs/trade-grading-plan.md).
-- Both are written by the review alone and are deliberately NOT part of
-- ``save_note``'s whole-row overwrite — see ``set_trade_review``, which updates
-- them in place so the journal form and the drill save never have to echo two
-- fields they know nothing about.
--
-- ``grade`` is A|B|C|D on the whole trade. Until 2026-08-31 it was assigned in
-- the review panel with the outcome on screen, and the 132 rows written that way
-- restated the P&L sign (every A/B won, every D lost). It is now written from
-- the recall front — the fill-freeze, before the outcome fetch — which is the
-- one surface that can ask the question blind. Rows graded under the old rule
-- keep their letters; cuts that care can split on ``updated_at``.
--
-- ``setup`` and ``discipline`` are the two enumerated axes the free tags held by
-- accident (``journal.review.SETUPS`` / ``DISCIPLINES``): what the trade *was*,
-- faded-vs-joined first, and whether the plan was followed. NULL = never
-- answered. Written by ``set_trade_review`` like the fields above.
--
-- ``watched_levels_json`` is the levels the trade was taken off, chosen from the
-- candidates ``trade_levels`` measured, or the single literal ``'none'``. NULL
-- and ``'[]'`` mean never answered; ``["none"]`` means answered *I was not
-- trading a level*, and the two must stay distinguishable or a trade with no
-- cached tape becomes indistinguishable from an unreviewed one.
--
-- It holds levels (``gxVP_poc``), not families. It briefly held a family, which
-- could not distinguish the globex VAH from the session one — the whole point of
-- asking. It then held exactly one level, which could not say *the globex POC
-- and the weekly VWAP were the same price*: confluence is the ordinary reason a
-- level gets traded, and forcing the pick to one made the answer arbitrary at
-- the moments that mattered most. ``'none'`` stays exclusive — "no level" and "a
-- level" cannot both be true — and that is enforced at the door in
-- ``api.routers.notes``.
--
-- ``watched_level`` and ``watched_family`` are the older columns, migrated
-- across and then left alone rather than dropped.
CREATE TABLE IF NOT EXISTS trade_notes (
    trade_key        TEXT PRIMARY KEY,
    note             TEXT,
    tags_json        TEXT,
    setups_json      TEXT DEFAULT '[]',   -- setup badges (per-trade)
    confluences_json TEXT DEFAULT '[]',   -- evidence/context badges (per-trade)
    grade            TEXT,                -- 'A'|'B'|'C'|'D'; NULL = ungraded
    setup            TEXT,                -- review.SETUPS; NULL = unanswered
    discipline       TEXT,                -- review.DISCIPLINES; NULL = unanswered
    watched_levels_json TEXT DEFAULT '[]',-- level_tag MEMBERS keys, or ['none']
    watched_level    TEXT,                -- superseded by watched_levels_json; unread
    watched_family   TEXT,                -- superseded by watched_level; unread
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

-- ``attempt_videos`` / ``video_bookmarks`` used to live here: a day was reviewed
-- by replaying a screen recording. The tape replayer in the day view replaced
-- that, and this DB held no rows for either table when they were removed. They
-- are deliberately *not* dropped on init — the pre-mode-folders and pre-tz-repair
-- backups still carry 86 links and 723 bookmarks between them, and restoring one
-- of those should not silently destroy the history it was kept for.

-- A session is one ATAS export (one source_file).
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
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT,
    updated_at  TEXT
);

-- A trading model: the fixed approach a trade is executed under. Unlike the
-- name-keyed setups/confluences above this uses an integer surrogate PK, because
-- three tables reference it and a rename must not cascade through them.
-- ``folder`` is the model's export drop-box under data/imports/backtest/ — a
-- stable slug the watcher resolves back to the model, so it survives renames.
-- ``target_sample`` is the backtest sample size the model is working toward.
CREATE TABLE IF NOT EXISTS models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT DEFAULT '',
    archived      INTEGER NOT NULL DEFAULT 0,
    folder        TEXT,
    target_sample INTEGER,
    created_at    TEXT
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

-- Measured level proximity, one row per (trade, fill, family). MACHINE-OWNED:
-- nothing here is a self-report, and the review gate deliberately does not read
-- it. Keeping it out of the human's own tables is what preserves the only
-- comparison that makes it worth measuring — what the trader said, versus what
-- the tape did. Auto-filling the human's side would answer the question with its
-- own guess and satisfy the review gate for free.
--
-- What the human side *is* changed twice. It was the review's confluence tags —
-- a list of the levels the trader remembered seeing, which this table could only
-- agree or disagree with, and which was never wrong in any way worth recording.
-- Then a thesis, deleted 2026-08-20 with its vocabulary. It is now
-- ``trade_notes.watched_levels_json``: the levels the trade was taken off,
-- picked from the levels measured here.
--
-- That pick is offered a shortlist, which is as close to the forbidden thing as
-- this gets, and the boundary holds on two rules kept in
-- ``api/routers/replays._level_candidates``: candidates are ordered by DISTANCE,
-- never by ``rank`` (distance is a fact about the chart; rank is this table's
-- opinion about it), and nothing is pre-selected. A single referent that can
-- disagree with the nearest measured level is falsifiable in the way the
-- confluence list never was.
--
-- ``rank`` is the measurement (fraction of drift-matched companions that sat
-- closer); "at the level" is a threshold applied at READ time, never stored, so
-- retuning it costs nothing. ``method`` stamps the null that produced the row —
-- see journal.level_tag.METHOD — so a mixed table is detectable and recomputable.
--
-- ONE ROW PER LEVEL since 2026-08-21, not per family. The families still own the
-- ranking (that is what stops one collinear signal being split three ways), so
-- ``rank`` is carried only by the member its family was scored through and is
-- NULL on the rest. The rest exist because the review asks which level the trade
-- was taken off, and a family collapsed to its nearest member cannot answer that:
-- on a day the session POC was closer, there was simply no way to say "the
-- globex POC". Distance is a fact, needs no null, and every measured level has one.
CREATE TABLE IF NOT EXISTS trade_levels (
    trade_key   TEXT NOT NULL,
    anchor      TEXT NOT NULL,   -- 'entry' | 'exit'
    family      TEXT NOT NULL,   -- journal.level_tag.FAMILIES
    member      TEXT NOT NULL,   -- journal.level_tag.MEMBERS — the level itself
    rank        REAL,            -- 0..1 on the family's scored member; NULL elsewhere
    dist_ticks  REAL,            -- signed: fill price minus the level
    method      TEXT NOT NULL,
    computed_at TEXT,
    PRIMARY KEY (trade_key, anchor, member)
);

-- What price did BEFORE the entry and AFTER the exit, in index points, signed to
-- the trade's own direction. One row per trade. MACHINE-OWNED, like trade_levels
-- and for the same reason: nothing here is a self-report, which is the whole
-- point of having it beside the trader's own account of the trade.
--
-- Everything is re-derivable from the tape — an attempt pins its symbol and day
-- — so this table is a cache of a measurement, never a second copy of the ticks.
-- ``method`` stamps the definitions (journal.trade_context.METHOD) so a mixed
-- table is detectable and a redefinition is a backfill.
--
-- READ ``pre_avail_s``/``post_avail_s`` FIRST. They are how much tape each window
-- actually got: a trade exited at 15:58 has four minutes of session left, and
-- its 30-minute follow-through of zero is the clock, not the market.
--
-- The hold itself is deliberately absent — MAE/MFE between entry and exit is
-- journal.excursion, on minute bars, and two numbers with one name is how a
-- journal starts lying.
CREATE TABLE IF NOT EXISTS trade_context (
    trade_key    TEXT PRIMARY KEY,
    symbol       TEXT,            -- contract the roll resolved; the audit trail
    tick_size    REAL,            -- points per tick: converts the two units below
    method       TEXT NOT NULL,
    computed_at  TEXT,
    pre_avail_s  REAL,
    post_avail_s REAL,
    fwd_avail_s  REAL,            -- tape left AFTER THE ENTRY (see fwd_pts_*)
    -- The approach. POSITIVE run = price had already gone your way: you chased.
    pre_run_pts_1m   REAL,
    pre_run_pts_5m   REAL,
    pre_run_pts_15m  REAL,
    pre_run_pts_30m  REAL,
    pre_range_pts_15m REAL,
    pre_range_pts_30m REAL,
    pre_loc_15m  REAL,            -- 0 = fill at the window low, 1 = at its high
    pre_loc_30m  REAL,            -- RAW, not direction-signed (see trade_context)
    -- Was the direction right. Net move from the ENTRY at a fixed clock, signed
    -- to the trade. The one measurement here that is BLIND TO THE EXIT: it
    -- scores the claim the entry made, so a scratch and a runner off the same
    -- signal read the same. NULL past the session end, never the closing price.
    fwd_pts_30s  REAL,
    fwd_pts_1m   REAL,
    fwd_pts_5m   REAL,
    -- What was left behind. MFE without MAE is half a claim: holding for the
    -- follow-through means sitting through the drawdown that came with it.
    post_mfe_pts_1m  REAL,
    post_mfe_pts_5m  REAL,
    post_mfe_pts_15m REAL,
    post_mfe_pts_30m REAL,
    post_mae_pts_1m  REAL,
    post_mae_pts_5m  REAL,
    post_mae_pts_15m REAL,
    post_mae_pts_30m REAL,
    post_mfe_close_pts REAL,      -- to the session end, not to a horizon
    post_close_pts     REAL,
    exit_rank          REAL,      -- 1.0 = nothing in the next 30m traded better
    post_ret_entry_s   REAL,      -- NULL = the entry was never offered again
    -- How big the bars were around the fill, at the three resolutions actually
    -- traded on. In TICKS (the points above divide by tick_size), so these read
    -- the same as the chart's vol-ruler pane. ATR(14) is what was on screen;
    -- the median over the 30m approach is the number one bar cannot yank.
    vol_atr_ticks_500t REAL,
    vol_atr_ticks_30s  REAL,
    vol_atr_ticks_1m   REAL,
    vol_med_ticks_500t REAL,
    vol_med_ticks_30s  REAL,
    vol_med_ticks_1m   REAL,
    -- The bar the fill landed in. Body is SIGNED TO THE TRADE (positive = the
    -- bar was going your way), because "green" means opposite things to a long
    -- and a short. `elapsed` is the honest form of "was the candle closed":
    -- off the tape every bar is closed, and what differed live is how much of
    -- it had printed when you fired.
    eb_body_ticks_500t REAL,
    eb_body_ticks_30s  REAL,
    eb_body_ticks_1m   REAL,
    eb_loc_500t        REAL,      -- 0 = filled at the bar's low, 1 = at its high
    eb_loc_30s         REAL,      -- RAW, not direction-signed, and NOT clipped:
                                  -- a scaled entry's average can sit outside the
                                  -- bar its first fill landed in, and that is the
                                  -- fact, not an error to round away
    eb_loc_1m          REAL,
    eb_elapsed_500t    REAL,      -- 0.1 on a 500-tick bar = you saw 50 prints
    eb_elapsed_30s     REAL,
    eb_elapsed_1m      REAL
);

-- Spaced repetition over your own reviewed trades (Lab -> Recall,
-- docs/trade-grading-plan.md). One card per trade; the rep log is separate so a
-- card's schedule can be rebuilt from its history and so a rep is never lost to
-- an upsert.
--
-- **This table holds a schedule and nothing else.** Where the front's tape stops
-- is not stored: it is the trade's own fill, read off the journal row each time
-- (``api.routers.recall``). It used to be a jittered ``cut_ms`` column, pinned so
-- the question could not move between reps — dropped 2026-08-22 along with the
-- jitter itself, because a stop that is derived from the trade cannot drift the
-- way a rolled one could, and a card whose row is deleted still comes back as
-- the same question.
-- ``ease``, ``reps`` and ``lapses`` are SM-2's. They are still written, because
-- SM-2 is what schedules this deck when the Arena binary hasn't been built, and
-- a column that goes stale the moment you clone the repo is worse than one kept
-- current for a fallback that costs nothing.
CREATE TABLE IF NOT EXISTS recall_cards (
    trade_key   TEXT PRIMARY KEY,
    due         TEXT NOT NULL,     -- ISO date; <= today means show it
    interval_d  REAL NOT NULL,     -- days until the next showing
    ease        REAL NOT NULL,     -- SM-2 ease factor, starts at 2.5
    reps        INTEGER NOT NULL,
    lapses      INTEGER NOT NULL,
    -- The Arena's per-card state (~900 bytes of JSON): the five models' item
    -- state, its stability and difficulty. NULL until the card's first rating
    -- under SM-20. Deliberately not read by ``all_recall_cards`` — the deck
    -- needs a due date, not a scheduler's working set.
    sm20_state  TEXT,
    -- Unix epoch days at the last rating, the clock the Arena is given. Ours,
    -- not read out of ``sm20_state``: M1 keeps its own copy inside that blob and
    -- reaching into a vendored struct's internals from Python is how a re-vendor
    -- breaks silently. NULL means "never rated under SM-20".
    last_review_day INTEGER,
    updated_at  TEXT
);

-- One row per showing, append-only. ``guess`` is free text and is NEVER scored:
-- there is no objective answer to "what does price do next", so the card is
-- self-rated the way an Anki card is, and the guess exists to make you commit to
-- a read before flipping rather than to be marked.
CREATE TABLE IF NOT EXISTS recall_reps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_key  TEXT NOT NULL,
    shown_at   TEXT NOT NULL,     -- UTC ISO, stamped server-side at the rating
    rating     INTEGER NOT NULL,  -- 1 again | 2 hard | 3 good | 4 easy
    guess      TEXT
);

-- The Algorithm Arena's deck-wide state: live blend weights, M2's optimizer,
-- and M3's 21x21x21 matrices (src/journal/sm20.py, vendor/sm20/PROVENANCE.md).
--
-- One row, always id 1. SM-2 needed nothing like this — a card's schedule was a
-- function of that card alone — but the Arena learns across the whole deck, so
-- this blob is where every rating's tuning accumulates. It is ~270 KB and it is
-- rewritten on each rating; that is a fixed cost, not one that grows with the
-- deck, because every matrix inside it has fixed dimensions.
--
-- Losing this row is not fatal and not loud: the next rating starts from
-- default weights, and the schedule simply gets quietly worse. It is derived
-- state and cannot be rebuilt from ``recall_reps`` (the models are path
-- dependent), which is the argument for keeping it in the same file as the
-- cards rather than in a cache directory.
CREATE TABLE IF NOT EXISTS recall_collection (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    state_json TEXT NOT NULL,
    updated_at TEXT
);

-- What the **last** rating overwrote, so that one misclick can be taken back.
--
-- One row, always id 1, replaced by every rating: the slot therefore always
-- describes the most recent one, which is the only rating that can be reverted
-- exactly. Restoring an older snapshot would roll ``recall_collection`` back to
-- a state every rating since has learned past, so an undo stack is not a deeper
-- version of this feature — it is a different and worse one.
--
-- Why a snapshot rather than a recomputation: two of the three things a rating
-- writes cannot be reconstructed after the fact. The Arena's collection is path
-- dependent (see above), and ``save_recall_card`` deliberately COALESCEs
-- ``sm20_state`` so a fallback rating cannot blank it — which also means the
-- previous value is gone the moment it is written over. The bytes have to be
-- kept, so they are kept here, once.
--
-- NULL ``card_json`` means the card had no row at all before the rating (its
-- first ever showing), and NULL ``collection_json`` means the deck had no Arena
-- state. Both restore by deleting, not by writing a default: the point is to
-- leave the tables exactly as the rating found them.
CREATE TABLE IF NOT EXISTS recall_undo (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    trade_key       TEXT NOT NULL,
    rep_id          INTEGER NOT NULL,  -- the recall_reps row to delete
    card_json       TEXT,              -- recall_cards row before; NULL = none
    collection_json TEXT,              -- recall_collection blob before; NULL = none
    -- The grade this rating wrote, if it wrote one (the recall front is where a
    -- trade gets graded). ``wrote_grade`` says whether to restore at all;
    -- ``grade_before`` is what to restore to — NULL for a first grading, which
    -- is the ordinary case since the front only asks an ungraded trade.
    wrote_grade     INTEGER DEFAULT 0,
    grade_before    TEXT,
    staged_at       TEXT
);

-- ``trade_intent`` and ``recall_reviews`` are gone (the thesis vocabulary they
-- served was deleted 2026-08-20, docs/trade-grading-plan.md). Installs that have
-- them keep them: dropping a table to reclaim nothing is how a record of what
-- was once asked gets lost, and nothing reads them now.

CREATE INDEX IF NOT EXISTS idx_model_rules_model ON model_rules(model_id);
CREATE INDEX IF NOT EXISTS idx_trade_levels_key ON trade_levels(trade_key);
CREATE INDEX IF NOT EXISTS idx_recall_reps_key ON recall_reps(trade_key);
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
    # WAL keeps the many small import commits cheap and lets a second connection
    # (CLI/Streamlit) read while the API writes. WAL is a persistent DB property
    # and is only safe on a real filesystem — keep the DB on ext4, never /mnt/c.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_ai_schema(conn)
    _migrate_imported_files(conn)
    _migrate_trade_note_tagging(conn)
    _migrate_trade_review_cols(conn)
    _migrate_watched_level(conn)          # reads the OLD trade_levels rows
    _migrate_watched_levels_multi(conn)   # ...and folds its column into a list
    _migrate_trade_levels_per_member(conn)  # ...so it must run after
    _migrate_setup_confluence_master(conn)  # needs trade-note columns above
    _migrate_journal_model(conn)
    _migrate_backtest_journaling(conn)
    _migrate_trade_context_cols(conn)
    _migrate_recall_sm20(conn)
    _migrate_recall_drop_cut(conn)
    _migrate_review_axes(conn)
    _migrate_journal_fees(conn)


def _migrate_imported_files(conn: sqlite3.Connection) -> None:
    """Add ``file_mtime`` to installs that predate the export-modified-time card.

    Existing rows get NULL (we never captured the original file date), so their
    day shows "—" for Modified until re-uploaded.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(imported_files)")}
    if "file_mtime" not in cols:
        conn.execute("ALTER TABLE imported_files ADD COLUMN file_mtime TEXT")
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


def _migrate_trade_review_cols(conn: sqlite3.Connection) -> None:
    """Add ``grade`` / ``watched_family`` to installs whose ``trade_notes``
    predate the graded review (docs/trade-grading-plan.md).

    Both stay NULL on existing rows, which is exactly right: NULL means *never
    answered*, and every trade reviewed under the old thesis-and-note rule is
    unanswered under the new one. They will be asked again the next time each
    sitting is opened.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_notes)")}
    if "grade" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN grade TEXT")
    if "watched_family" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN watched_family TEXT")
    conn.commit()


def _migrate_watched_level(conn: sqlite3.Connection) -> None:
    """Move the review's level answer from a family to the level itself.

    The old column stored a family, which could not say *which* VAH — the thing
    the question exists to capture. Each stored answer is resolved to the member
    that family was scored through on that trade, which is the level the picker
    was naming when it was answered, so nothing is guessed.

    Reads ``trade_levels`` in its OLD one-row-per-family shape, so it has to run
    before that table is rebuilt. An answer whose measurement is gone keeps the
    family id: ``label_for`` falls back to the family name, and a stale id reads
    better than a silently dropped answer.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_notes)")}
    if "watched_level" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN watched_level TEXT")
    if "watched_family" not in cols:
        conn.commit()
        return
    rows = conn.execute(
        "SELECT trade_key, watched_family FROM trade_notes "
        "WHERE watched_family IS NOT NULL AND watched_level IS NULL"
    ).fetchall()
    for r in rows:
        fam = r["watched_family"]
        member = None
        if fam != "none":
            hit = conn.execute(
                "SELECT member FROM trade_levels "
                "WHERE trade_key = ? AND anchor = 'entry' AND family = ?",
                (r["trade_key"], fam),
            ).fetchone()
            member = hit["member"] if hit else None
        conn.execute(
            "UPDATE trade_notes SET watched_level = ? WHERE trade_key = ?",
            (member or fam, r["trade_key"]),
        )
    if rows:
        print(f"[db] carried {len(rows)} review answer(s) from family to level")
    conn.commit()


def _migrate_watched_levels_multi(conn: sqlite3.Connection) -> None:
    """Let the review's level answer name more than one level.

    A trade taken where the globex POC and the weekly VWAP sat on the same price
    was being asked which one it was, and any answer to that was arbitrary.

    Every stored answer becomes a one-element list, which loses nothing: a single
    pick is still a valid multiple pick. The old column keeps its value and stops
    being read, the way ``watched_family`` did before it.

    Idempotent by NULL: the ALTER adds the column with no default, so exactly the
    rows that predate it are NULL, and the backfill leaves none behind. A review
    later cleared back to ``'[]'`` is therefore not resurrected on the next boot.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_notes)")}
    if "watched_levels_json" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN watched_levels_json TEXT")
    rows = conn.execute(
        "SELECT trade_key, watched_level FROM trade_notes "
        "WHERE watched_levels_json IS NULL"
    ).fetchall()
    carried = 0
    for r in rows:
        one = (r["watched_level"] or "").strip()
        conn.execute(
            "UPDATE trade_notes SET watched_levels_json = ? WHERE trade_key = ?",
            (json.dumps([one] if one else []), r["trade_key"]),
        )
        carried += bool(one)
    if carried:
        print(f"[db] carried {carried} review answer(s) from one level to a list")
    conn.commit()


def _migrate_trade_levels_per_member(conn: sqlite3.Connection) -> None:
    """Rebuild ``trade_levels`` keyed by the level rather than by the family.

    The primary key changes, which SQLite cannot alter in place, so the table is
    dropped and recreated. **That discards the stored measurements**, and is only
    acceptable because they are exactly the kind of thing that can be recomputed:
    the row shape moved, which bumps ``level_tag.METHOD``, which already means
    every row is stale and awaiting ``demo/level_tag_backfill.py``.

    Answers written by a human are NOT in this table and are untouched — see
    ``_migrate_watched_level``, which runs first and depends on these rows.
    """
    cols = list(conn.execute("PRAGMA table_info(trade_levels)"))
    if not cols:
        return
    # ``table_info`` states primary-key membership directly, in its ``pk``
    # ordinal (0 = not in the key). Read it from there rather than walking
    # ``index_list`` -> ``index_info``: that walk read the index *name* out of
    # ``index_list``'s ``unique`` slot — the row is (seq, name, unique, origin,
    # partial) — so ``index_info`` was handed a 1, matched nothing, and left
    # ``pk_cols`` empty. The guard below could then never fire, and a migration
    # meant to run once dropped every measurement on *every* connect. The cost
    # was invisible until a review: with no candidates, a card offers only "no
    # level", and a trade that cannot name its level cannot be answered at all.
    pk_cols = {r[1] for r in cols if r[5]}
    # The old shape keys on the family; the new one keys on the member.
    if "member" in pk_cols and "family" not in pk_cols:
        return
    n = conn.execute("SELECT COUNT(*) FROM trade_levels").fetchone()[0]
    conn.execute("DROP TABLE trade_levels")
    conn.executescript(SCHEMA)
    conn.commit()
    if n:
        print(f"[db] trade_levels rebuilt per-level; {n} row(s) dropped — "
              f"re-run demo/level_tag_backfill.py to recompute them")


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
    inferred by :func:`infer_session`, the same rule the ingest path uses. Old
    setup badges are deliberately *not* mapped onto
    ``trade_model``: a trade historically carried 0..n setups and a model is
    exactly 1, so any automatic mapping would be semantically wrong.
    """
    if get_setting(conn, "sessions_cutover_done") != "1":
        by_file: dict[str, list[str]] = {}
        for r in conn.execute("SELECT source_file, account FROM atas_journal"):
            by_file.setdefault(r["source_file"], []).append(r["account"])
        for source_file, accounts in by_file.items():
            mode, modal = infer_session(accounts)
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


def _migrate_backtest_journaling(conn: sqlite3.Connection) -> None:
    """Add the backtest-journaling columns to installs that predate them.

    ``models.folder`` (the watcher's export drop-box slug) is backfilled from
    each model's name so existing models get a folder without a rename;
    ``models.target_sample`` and ``sessions.note`` start empty.
    """
    model_cols = {r[1] for r in conn.execute("PRAGMA table_info(models)")}
    if "folder" not in model_cols:
        conn.execute("ALTER TABLE models ADD COLUMN folder TEXT")
    if "target_sample" not in model_cols:
        conn.execute("ALTER TABLE models ADD COLUMN target_sample INTEGER")
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "note" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    for r in conn.execute("SELECT id, name FROM models WHERE folder IS NULL").fetchall():
        conn.execute(
            "UPDATE models SET folder = ? WHERE id = ?",
            (unique_model_folder(conn, r["name"], exclude_id=r["id"]), r["id"]),
        )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_models_folder ON models(folder)")
    conn.commit()


#: The only non-numeric columns of ``trade_context``. Everything else the
#: measurement defines is a REAL, which is what lets the migration below be
#: written once instead of once per redefinition.
_CONTEXT_TEXT_COLS = frozenset({"trade_key", "symbol", "method", "computed_at"})


def _migrate_trade_context_cols(conn: sqlite3.Connection) -> None:
    """Give an older context table whatever columns the measurement now defines.

    Driven off ``trade_context.COLUMNS`` rather than a hand-written list, because
    that list is already the single definition of the table's shape — the writer
    inserts by it and the reader reads by it — and a migration that restates it
    is a third copy waiting to fall out of step. Adding a horizon is then one
    edit in that module plus a ``METHOD`` bump, with nothing to remember here.

    Existing rows keep their old ``method`` stamp and get NULLs in the new
    columns, which is the honest shape: those numbers were never measured, and
    every reader renders a missing one as absent rather than as a zero. Running
    ``demo/trade_context_backfill.py`` re-measures the table at the current
    METHOD, which is what actually fills them in.
    """
    from .trade_context import COLUMNS as CONTEXT_COLUMNS

    have = {r[1] for r in conn.execute("PRAGMA table_info(trade_context)")}
    for col in CONTEXT_COLUMNS:
        if col not in have and col not in _CONTEXT_TEXT_COLS:
            conn.execute(f"ALTER TABLE trade_context ADD COLUMN {col} REAL")
    conn.commit()


def _migrate_recall_sm20(conn: sqlite3.Connection) -> None:
    """Give an SM-2-era recall deck the two columns the Arena needs.

    Existing cards get NULL in both, which reads as "never rated under SM-20":
    the next rating seeds fresh item state from the grade it is given, exactly as
    a new card would. Their SM-2 history is not translated and cannot be — the
    two schedulers do not share a state space — so a deck carried across this
    migration keeps its due dates and starts learning again from the next rep.

    ``recall_collection`` needs nothing here: it is a ``CREATE TABLE IF NOT
    EXISTS`` in the schema, which every open already runs.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(recall_cards)")}
    if "sm20_state" not in have:
        conn.execute("ALTER TABLE recall_cards ADD COLUMN sm20_state TEXT")
    if "last_review_day" not in have:
        conn.execute("ALTER TABLE recall_cards ADD COLUMN last_review_day INTEGER")
    conn.commit()


def _migrate_recall_drop_cut(conn: sqlite3.Connection) -> None:
    """Drop the jittered ``cut_ms`` from a deck that predates the fixed stop.

    Nothing reads it any more — the front stops at the trade's own fill — and it
    is ``NOT NULL``, so leaving it would make every future insert carry a number
    with no meaning. The schedule in the other columns is untouched: these are
    the same cards, asked at the fill instead of near it.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(recall_cards)")}
    if "cut_ms" in have:
        conn.execute("ALTER TABLE recall_cards DROP COLUMN cut_ms")
        conn.commit()


def _migrate_review_axes(conn: sqlite3.Connection) -> None:
    """Add the review's two enumerated axes, and the undo slot's grade snapshot.

    ``setup`` / ``discipline`` stay NULL on existing rows — never answered —
    which makes every pre-axes review unanswered under the new gate. That is
    deliberate and mirrors what ``_migrate_trade_review_cols`` did when the
    grade arrived: old attempts are already filed and are not re-gated, and any
    row edited from now on owes the current questions. The one-time tag→axis
    carry lives in ``demo/review_axes_backfill.py``, not here: it is a judgment
    call over a closed set of rows, not a schema fact every install must apply.

    The ``recall_undo`` columns let an undone rating take back the grade it
    wrote, now that the recall front is where grading happens.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_notes)")}
    if "setup" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN setup TEXT")
    if "discipline" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN discipline TEXT")
    # When the grade was written — the blind-era marker. Stamped only by
    # ``set_trade_review`` when a grade rides the write, and since 2026-08-31
    # the only caller that sends one is the recall front, so NOT NULL means
    # *answered blind*. Every grade already on disk stays NULL: those were
    # assigned with the P&L on screen and restate the outcome, and any cut by
    # grade must be able to tell the two eras apart. ``updated_at`` cannot do
    # this job — the axis backfill touched it on rows whose grade it never
    # wrote.
    if "graded_at" not in cols:
        conn.execute("ALTER TABLE trade_notes ADD COLUMN graded_at TEXT")
    undo_cols = {r[1] for r in conn.execute("PRAGMA table_info(recall_undo)")}
    if "wrote_grade" not in undo_cols:
        conn.execute("ALTER TABLE recall_undo ADD COLUMN wrote_grade INTEGER DEFAULT 0")
    if "grade_before" not in undo_cols:
        conn.execute("ALTER TABLE recall_undo ADD COLUMN grade_before TEXT")
    conn.commit()


def _migrate_journal_fees(conn: sqlite3.Connection) -> None:
    """Give ``atas_journal`` the commission column the broker already computes.

    Until this column existed the journal stored gross P&L and nothing else:
    ``trades.py`` hardcoded ``commission = 0.0``, so ``net_pnl`` *was*
    ``gross_pnl`` everywhere and a live day could never agree with the broker's
    own statement, which is net. The number was not missing, only homeless —
    ``Broker._emit_trade`` has always put ``fees`` on every round trip and
    written it to ``orders.jsonl``.

    Existing rows get NULL, and NULL rather than 0.0 is the honest value: an
    imported ATAS export never reported a commission, so "not known" is what is
    true of it. Every reader treats NULL as no commission known and renders the
    row exactly as it renders it today.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(atas_journal)")}
    if "fees" not in have:
        conn.execute("ALTER TABLE atas_journal ADD COLUMN fees REAL")
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


JOURNAL_COLS = [
    "dedupe_key", "account", "instrument", "open_ts_local", "close_ts_local",
    "open_ts_utc", "close_ts_utc", "open_price", "open_volume", "close_price",
    "close_volume", "price_pnl", "profit_ticks", "pnl", "fees", "comment",
    "source_file",
]


def insert_journal(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    return _insert_ignore(conn, "atas_journal", JOURNAL_COLS, rows)


def replace_journal(
    conn: sqlite3.Connection, source_file: str, rows: Iterable[dict]
) -> int:
    """Make one source file's journal rows *exactly* ``rows``. Returns how many.

    ``insert_journal`` can only ever add: it is INSERT OR IGNORE on a content
    hash, which is the right shape for an import (re-importing the same export
    is free) and the wrong shape for a source whose trades can be **withdrawn**.
    A Simulator attempt is such a source — rewinding past a fill un-happens it —
    and appending would leave the erased trade in the journal for good, with the
    attempt on disk no longer saying it ever existed.

    Delete-then-insert rather than a diff, because a `dedupe_key` is derived from
    the trade's own content: a trade whose exit price changed is a different key,
    so there is nothing to update in place. Both statements land in one commit,
    so a reader never sees the gap where the file has no trades.

    Notes survive this. ``trade_notes`` is keyed by ``trade_key``, which is the
    lot's ``dedupe_key`` truncated (``trades._trade_key``) — so a trade that
    comes back unchanged comes back under the same key, with its note attached.
    """
    data = [tuple(r[c] for c in JOURNAL_COLS) for r in rows]
    placeholders = ",".join("?" for _ in JOURNAL_COLS)
    conn.execute("DELETE FROM atas_journal WHERE source_file = ?", (source_file,))
    if data:
        conn.executemany(
            f"INSERT OR IGNORE INTO atas_journal ({','.join(JOURNAL_COLS)}) "
            f"VALUES ({placeholders})",
            data,
        )
    conn.commit()
    return len(data)


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


def clear_imported(conn: sqlite3.Connection, source_file: str) -> None:
    """Drop one source file's import stamp, leaving its rows alone.

    ``delete_attempt`` does this as part of clearing a whole ATAS take. A
    Simulator sitting is withdrawn through ``replace_journal`` instead (its
    trades are a mirror, not an import), so it needs the stamp removed on its
    own — otherwise a deleted attempt keeps a Modified time on the calendar day
    it no longer has any trades in.
    """
    conn.execute("DELETE FROM imported_files WHERE source_file = ?", (source_file,))
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


EMPTY_NOTE = {
    "note": "",
    "tags_json": "[]",
    "setups_json": "[]",
    "confluences_json": "[]",
    "grade": None,
    "setup": None,
    "discipline": None,
    # Decoded, unlike the badge arrays beside it: every caller of this wants the
    # list, and four of them re-implementing the same ``json.loads`` around a
    # column that can be NULL is four places for "never answered" to read wrong.
    "watched_levels": [],
}


def _decode_levels(raw: str | None) -> list[str]:
    """``watched_levels_json`` as a list, tolerating anything a hand-edited row
    could hold. An empty list is "never answered" — the same thing NULL is."""
    try:
        arr = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in arr if str(x).strip()] if isinstance(arr, list) else []


def get_note(conn: sqlite3.Connection, trade_key: str) -> dict:
    row = conn.execute(
        "SELECT note, tags_json, setups_json, confluences_json, grade, "
        "setup, discipline, watched_levels_json "
        "FROM trade_notes WHERE trade_key = ?",
        (trade_key,),
    ).fetchone()
    if row is None:
        return dict(EMPTY_NOTE)
    return {
        "note": row["note"] or "",
        "tags_json": row["tags_json"] or "[]",
        "setups_json": row["setups_json"] or "[]",
        "confluences_json": row["confluences_json"] or "[]",
        # No coalescing: NULL is "never answered" and must not read as an answer.
        "grade": row["grade"],
        "setup": row["setup"],
        "discipline": row["discipline"],
        "watched_levels": _decode_levels(row["watched_levels_json"]),
    }


def all_trade_reviews(conn: sqlite3.Connection) -> dict[str, dict]:
    """{trade_key: the three review answers}, for deciding *which* trades have
    been reviewed in one query instead of one per trade.

    Deliberately unfiltered: whether a row counts as a review is
    ``journal.review.trade_answered``'s call and must not be re-stated as a
    WHERE clause here, where it would drift. The table only holds trades that
    have been written about at all, so reading it whole is cheap."""
    return {
        r["trade_key"]: {
            "grade": r["grade"],
            "setup": r["setup"],
            "discipline": r["discipline"],
            "watched_levels": _decode_levels(r["watched_levels_json"]),
            "tags": json.loads(r["tags_json"] or "[]"),
        }
        for r in conn.execute(
            "SELECT trade_key, grade, setup, discipline, watched_levels_json, "
            "tags_json FROM trade_notes"
        )
    }


def all_trade_tags(conn: sqlite3.Connection) -> list[str]:
    """The union of every free-form tag on any trade, sorted by how often it is
    used and then alphabetically — the order an autocomplete wants to offer
    them in. Tags only: setups/confluences have their own curated masters."""
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT tags_json FROM trade_notes"):
        try:
            arr = json.loads(row["tags_json"] or "[]")
        except (TypeError, ValueError):
            continue
        for t in arr:
            if isinstance(t, str) and t.strip():
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t.lower()))


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


def set_trade_review(conn: sqlite3.Connection, trade_key: str,
                     grade: str | None, watched_levels: list[str] | None,
                     setup: str | None = None,
                     discipline: str | None = None, *,
                     blind: bool = False) -> None:
    """Write the review's verdict columns **in place**, leaving the note and
    every badge array alone.

    Deliberately not part of ``save_note``. That one overwrites the whole row,
    which is a documented blanking hazard the journal form and the drill save
    already have to work around by echoing fields they don't own; folding more
    in would widen that obligation to every caller. Here ``None`` means
    *leave it as it was*, so a caller that has never heard of a grade cannot
    erase one.

    A ``watched_levels`` **list** replaces the stored set wholesale — deselecting
    one of two has to persist, so this one field cannot be additive. An empty
    list is therefore a deliberate un-answering, reachable only from a caller
    that sent the field at all; ``None`` remains the way to say nothing.
    """
    if grade is None and watched_levels is None \
            and setup is None and discipline is None:
        return
    sets, vals = [], []
    if grade is not None:
        sets.append("grade = ?")
        vals.append(grade)
        # The blind-era stamp (see _migrate_review_axes). The recall front
        # passes ``blind=True``; a hand repair through PUT /notes does not, and
        # *clears* any stamp it overwrites — the invariant is that a non-NULL
        # ``graded_at`` always describes the grade currently stored.
        sets.append("graded_at = datetime('now')" if blind else "graded_at = NULL")
    if setup is not None:
        sets.append("setup = ?")
        vals.append(setup)
    if discipline is not None:
        sets.append("discipline = ?")
        vals.append(discipline)
    if watched_levels is not None:
        sets.append("watched_levels_json = ?")
        vals.append(json.dumps(list(watched_levels)))
    # INSERT first so a trade whose only answer is a grade still gets a row;
    # DO NOTHING keeps an existing note untouched.
    conn.execute(
        "INSERT INTO trade_notes (trade_key, note, tags_json) VALUES (?, '', '[]') "
        "ON CONFLICT(trade_key) DO NOTHING",
        (trade_key,),
    )
    conn.execute(
        f"UPDATE trade_notes SET {', '.join(sets)}, updated_at = datetime('now') "
        "WHERE trade_key = ?",
        (*vals, trade_key),
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
# ``infer_session`` is the single definition of what an export *is*, shared by
# the cutover backfill and the ingest path. Splitting it would let freshly
# imported sessions classify differently from back-filled historical ones, and
# silently change which trades count as real money.
def infer_session(accounts: Iterable[str]) -> tuple[str, str | None]:
    """(mode, modal account) from the accounts on an export's journal rows.

    A file whose every row is the ``Replay`` account is a replay; anything else
    touched a real account and counts as live. ``backtest`` is never inferred —
    nothing in an export says a session exercised one model exclusively, so it's
    a deliberate choice made in the UI.
    """
    accounts = [a for a in accounts if a]
    if not accounts:
        return "replay", None
    mode = "replay" if all(a == "Replay" for a in accounts) else "live"
    modal = max(set(accounts), key=accounts.count)
    return mode, modal



# One row per ATAS export. The ingest path creates them un-archived; the cutover
# created the historical ones archived. Only ``upsert_session`` is called from
# ingest, and it never overwrites — so a mode/archive choice made in the UI
# survives re-importing the same export.
def upsert_session(
    conn: sqlite3.Connection,
    source_file: str,
    mode: str,
    account: str | None = None,
    model_id: int | None = None,
) -> None:
    """``model_id`` is only meaningful with ``mode='backtest'`` — the watcher
    passes the model a backtest folder resolved to."""
    conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(source_file, mode, account, model_id, archived, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, datetime('now'), datetime('now'))",
        (source_file, mode, account, model_id),
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
        "SELECT s.source_file, s.mode, s.account, s.model_id, s.archived, s.note, "
        "       s.updated_at, m.name AS model_name "
        "FROM sessions s LEFT JOIN models m ON m.id = s.model_id "
        "ORDER BY s.source_file"
    ).fetchall()
    return [{**dict(r), "archived": bool(r["archived"])} for r in rows]


# Every table that identifies its rows by the imports-relative source path.
SOURCE_FILE_TABLES = (
    "executions", "atas_journal", "atas_statistics",
    "sessions", "imported_files",
)


def rekey_source_prefix(conn: sqlite3.Connection, old_prefix: str, new_prefix: str) -> int:
    """Re-point every ``source_file`` under *old_prefix* at *new_prefix*.

    Renaming a model moves its drop-box folder on disk, which would otherwise
    strand every session already imported from it under a path that no longer
    exists: the watcher re-imports the moved file as a *new* session, but its
    fills and lots dedupe on content, so they stay attached to the dead key and
    the new session lands empty. Re-keying in the same breath as the disk rename
    keeps a session's identity following its folder.

    ``OR REPLACE`` so a row already squatting on the target key (from an earlier
    stranded re-import) loses to the original, which is the one carrying the
    user's mode / model / note / archive choices.

    Safe for trade identity: ``dedupe_key`` hashes only the lot's own contents
    and ``trade_key`` hashes the first lot's ``dedupe_key``, so neither moves
    when the path does — notes, model bindings and rule checks all survive.
    """
    moved = 0
    for table in SOURCE_FILE_TABLES:
        cur = conn.execute(
            f"UPDATE OR REPLACE {table} "
            "SET source_file = ? || substr(source_file, ?) "
            "WHERE source_file LIKE ? || '/%'",
            (new_prefix, len(old_prefix) + 1, old_prefix),
        )
        moved += cur.rowcount
    conn.commit()
    return moved


def update_session(
    conn: sqlite3.Connection,
    source_file: str,
    mode: str | None = None,
    model_id: int | None = None,
    archived: bool | None = None,
    clear_model: bool = False,
    note: str | None = None,
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
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(source_file)
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE source_file = ?", params)
    conn.commit()


def session_note(conn: sqlite3.Connection, source_file: str) -> str:
    """What was written about the sitting as a whole, or ``""``.

    One column off one row, because the two readers next to it answer a
    different question: ``session_map`` is the scope filter's hot path and has no
    business carrying prose, and ``list_sessions`` reads the whole table to fill
    a page. A session with no row (an export ingested before the sessions table,
    say) has no note, which is the same answer as an empty one.
    """
    row = conn.execute(
        "SELECT note FROM sessions WHERE source_file = ?", (source_file,)
    ).fetchone()
    return (row["note"] if row else "") or ""


def delete_session(conn: sqlite3.Connection, source_file: str) -> None:
    """Drop the sitting record itself.

    Not part of ``delete_attempt``, which exists to clear a junk *import* so the
    same file can be re-uploaded — there the session row is the thing that must
    survive, because it carries the mode and archive choice made in the UI. A
    source whose sitting is gone for good (a deleted Simulator attempt) has no
    such file to come back, and a session row pointing at nothing would sit in
    the sessions list forever.
    """
    conn.execute("DELETE FROM sessions WHERE source_file = ?", (source_file,))
    conn.commit()


# --- Models + their rule checklists ---------------------------------------
def slugify_folder(name: str) -> str:
    """Model name -> filesystem-safe folder slug: 'Silver Bullet v2' -> 'silver-bullet-v2'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "model"


def unique_model_folder(
    conn: sqlite3.Connection, name: str, exclude_id: int | None = None
) -> str:
    """The name's slug, suffixed -2/-3/… past any other model already holding it."""
    base = slugify_folder(name)
    taken = {
        r["folder"]
        for r in conn.execute(
            "SELECT folder FROM models WHERE folder IS NOT NULL AND id != ?",
            (exclude_id or -1,),
        )
    }
    folder = base
    n = 2
    while folder in taken:
        folder = f"{base}-{n}"
        n += 1
    return folder


def model_folder_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """{folder: {id, name, archived}} — how the watcher resolves a backtest
    subfolder back to the model it declares."""
    return {
        r["folder"]: {"id": r["id"], "name": r["name"], "archived": bool(r["archived"])}
        for r in conn.execute(
            "SELECT id, name, archived, folder FROM models WHERE folder IS NOT NULL"
        )
    }


def get_model(conn: sqlite3.Connection, model_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, name, description, archived, folder, target_sample "
        "FROM models WHERE id = ?",
        (model_id,),
    ).fetchone()
    if row is None:
        return None
    return {**dict(row), "archived": bool(row["archived"])}


def list_models(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    """Models A→Z, each with its rules. Archived rules are excluded from the
    checklist, but stay in the DB so old trades' compliance scores keep meaning."""
    where = "" if include_archived else "WHERE archived = 0"
    models = conn.execute(
        f"SELECT id, name, description, archived, folder, target_sample "
        f"FROM models {where} "
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
            "folder": m["folder"],
            "target_sample": m["target_sample"],
            "rules": by_model.get(m["id"], []),
        }
        for m in models
    ]


def create_model(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    cur = conn.execute(
        "INSERT INTO models (name, description, archived, folder, created_at) "
        "VALUES (?, ?, 0, ?, datetime('now'))",
        (name, description, unique_model_folder(conn, name)),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_model(
    conn: sqlite3.Connection,
    model_id: int,
    name: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
    folder: str | None = None,
    target_sample: int | None = None,
    clear_target: bool = False,
) -> None:
    """``target_sample=None`` means "don't touch" (like the other optionals);
    pass ``clear_target`` to actually null it out."""
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
    if folder is not None:
        sets.append("folder = ?")
        params.append(folder)
    if clear_target:
        sets.append("target_sample = NULL")
    elif target_sample is not None:
        sets.append("target_sample = ?")
        params.append(target_sample)
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


# --- Measured level proximity (machine-owned; see the trade_levels schema) ---
def set_trade_levels(conn: sqlite3.Connection, rows: Iterable, method: str) -> int:
    """Replace a trade's measured proximities with a freshly computed set.

    Whole-trade replace rather than per-row upsert: a recompute under a new
    ``method`` must not leave last method's rows sitting alongside the new ones,
    where they would read as extra evidence that nothing produced.

    ``rows`` are ``level_tag.LevelRank``. Returns how many were written.
    """
    rows = list(rows)
    for key in {r.key for r in rows}:
        conn.execute("DELETE FROM trade_levels WHERE trade_key = ?", (key,))
    conn.executemany(
        "INSERT OR REPLACE INTO trade_levels "
        "(trade_key, anchor, family, member, rank, dist_ticks, method, computed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        [(r.key, r.anchor, r.family, r.member, r.rank, r.dist_ticks, method)
         for r in rows],
    )
    conn.commit()
    return len(rows)


def get_trade_levels(conn: sqlite3.Connection, trade_key: str) -> list[dict]:
    """One trade's measured proximities, tightest first.

    Returned whole — every level, not just the ones that qualify — because the
    "nothing was near" answer is a real result and the caller owns the threshold.

    Ranked rows sort first (they are the families' scored members), then the rest
    by absolute distance. An unranked row is not a badly-ranked one: it is a level
    that was measured but was not the one its family was scored through.
    """
    return [dict(r) for r in conn.execute(
        "SELECT anchor, family, member, rank, dist_ticks, method, computed_at "
        "FROM trade_levels WHERE trade_key = ? "
        "ORDER BY anchor, rank IS NULL, rank, ABS(COALESCE(dist_ticks, 1e9))",
        (trade_key,),
    )]


def trade_levels_map(conn: sqlite3.Connection, max_rank: float) -> dict[str, list[dict]]:
    """{trade_key: [qualifying rows]} for the whole journal — the list view's feed.

    Thresholded here rather than in SQL-per-trade so the ranking stays one query
    no matter how many trades the page shows.
    """
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT trade_key, anchor, family, member, rank, dist_ticks FROM trade_levels "
        "WHERE rank < ? ORDER BY trade_key, anchor, rank", (max_rank,),
    ):
        out.setdefault(r["trade_key"], []).append(dict(r))
    return out


def prune_trade_levels(conn: sqlite3.Connection, method: str) -> int:
    """Drop every row not stamped ``method``. Returns how many went.

    A recompute replaces the rows of the trades it scores, which leaves behind
    the rows of trades it *didn't* — ones whose key no longer exists because a
    re-import re-cut them, or whose fills have since become mechanical. Those
    rows are a measurement of a trade the journal can't show, under a family map
    that may no longer contain their families, and they are what makes
    ``trade_levels_methods`` report a mixed table.

    Only safe after a run that covered the whole journal: a since-date backfill
    would delete perfectly good rows for the trades it never looked at.
    """
    n = conn.execute("DELETE FROM trade_levels WHERE method != ?", (method,)).rowcount
    conn.commit()
    return n


def trade_levels_methods(conn: sqlite3.Connection) -> dict[str, int]:
    """{method stamp: row count}. A table carrying more than one stamp is mid
    -recompute, and any cross-trade comparison drawn from it is comparing nulls."""
    return {r["method"]: r["n"] for r in conn.execute(
        "SELECT method, COUNT(*) AS n FROM trade_levels GROUP BY method"
    )}


# --- Measured pre-entry / post-exit context (see the trade_context schema) ---
def set_trade_context(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Store one row per trade, replacing whatever was there.

    ``rows`` are already flattened onto ``trade_context.COLUMNS`` — the column
    list lives with the measurement rather than here, so adding a horizon is one
    edit in one module plus a migration, not a rewrite of this function.
    """
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0])
    conn.executemany(
        f"INSERT OR REPLACE INTO trade_context ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})",
        [tuple(r[c] for c in cols) for r in rows],
    )
    conn.commit()
    return len(rows)


def clear_trade_context(conn: sqlite3.Connection, keys: Iterable[str]) -> int:
    """Drop the rows for trades the tape has since refused.

    The measurement is a cache, so a row must never outlive the reading that
    produced it: once a guard rejects a trade, leaving its old numbers in place
    is worse than having none, because every reader treats a present row as a
    measured one. INSERT OR REPLACE cannot express that — a refused trade
    produces nothing to insert — so the removal is its own call.
    """
    keys = list(keys)
    if not keys:
        return 0
    cur = conn.executemany("DELETE FROM trade_context WHERE trade_key = ?",
                           [(k,) for k in keys])
    conn.commit()
    return cur.rowcount


def get_trade_context(conn: sqlite3.Connection, trade_key: str) -> dict | None:
    """One trade's windows, or None when the tape could never speak to it."""
    r = conn.execute(
        "SELECT * FROM trade_context WHERE trade_key = ?", (trade_key,)
    ).fetchone()
    return dict(r) if r else None


def trade_context_methods(conn: sqlite3.Connection) -> dict[str, int]:
    """{method stamp: row count}. More than one stamp means the table is mid
    -recompute, and any cross-trade comparison drawn from it mixes definitions."""
    return {r["method"]: r["n"] for r in conn.execute(
        "SELECT method, COUNT(*) AS n FROM trade_context GROUP BY method"
    )}


def trade_context_keys(conn: sqlite3.Connection, method: str) -> set[str]:
    """Which trades already carry context under this method — what lets a backfill
    resume, and what makes a method bump re-measure everything."""
    return {r["trade_key"] for r in conn.execute(
        "SELECT trade_key FROM trade_context WHERE method = ?", (method,)
    )}


# --- The recall deck (see the recall_cards / recall_reps schema) -------------
def all_recall_cards(conn: sqlite3.Connection) -> dict[str, dict]:
    """{trade_key: card}, for building the deck in one query.

    Deliberately without ``sm20_state``: the deck asks each card only whether it
    is due, and pulling ~900 bytes of scheduler state per card to answer a date
    comparison would put the whole deck's working set in memory to show one card.
    :func:`get_recall_card` is where the scheduler's state comes from.
    """
    return {
        r["trade_key"]: {
            "due": r["due"],
            "interval_d": float(r["interval_d"]),
            "ease": float(r["ease"]),
            "reps": int(r["reps"]),
            "lapses": int(r["lapses"]),
        }
        for r in conn.execute(
            "SELECT trade_key, due, interval_d, ease, reps, lapses FROM recall_cards"
        )
    }


def get_recall_card(conn: sqlite3.Connection, trade_key: str) -> dict | None:
    """One card's schedule and scheduler state, or None if it has never been rated.

    ``sm20_state`` comes back parsed, or None on a card that predates SM-20 or
    was last scheduled by the SM-2 fallback. ``last_review_day`` is Unix epoch
    days and is what the elapsed interval must be measured against.
    """
    r = conn.execute(
        "SELECT due, interval_d, ease, reps, lapses, sm20_state, last_review_day "
        "FROM recall_cards WHERE trade_key = ?",
        (trade_key,),
    ).fetchone()
    if r is None:
        return None
    return {
        "due": r["due"],
        "interval_d": float(r["interval_d"]), "ease": float(r["ease"]),
        "reps": int(r["reps"]), "lapses": int(r["lapses"]),
        "sm20_state": json.loads(r["sm20_state"]) if r["sm20_state"] else None,
        "last_review_day": (
            None if r["last_review_day"] is None else int(r["last_review_day"])
        ),
    }


def save_recall_card(conn: sqlite3.Connection, trade_key: str, card: dict) -> None:
    """Upsert one card's schedule.

    ``sm20_state`` and ``last_review_day`` are optional and are only overwritten
    when present: a rating that fell back to SM-2 leaves the Arena's last state
    intact rather than blanking it, so building the binary later resumes the card
    where it was instead of restarting it.
    """
    state = card.get("sm20_state")
    conn.execute(
        "INSERT INTO recall_cards "
        "(trade_key, due, interval_d, ease, reps, lapses, "
        " sm20_state, last_review_day, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(trade_key) DO UPDATE SET "
        "due=excluded.due, interval_d=excluded.interval_d, ease=excluded.ease, "
        "reps=excluded.reps, lapses=excluded.lapses, "
        "sm20_state=COALESCE(excluded.sm20_state, recall_cards.sm20_state), "
        "last_review_day=COALESCE(excluded.last_review_day, recall_cards.last_review_day), "
        "updated_at=excluded.updated_at",
        (trade_key, card["due"], float(card["interval_d"]),
         float(card["ease"]), int(card["reps"]), int(card["lapses"]),
         json.dumps(state) if state is not None else None,
         card.get("last_review_day")),
    )
    conn.commit()


def get_recall_collection(conn: sqlite3.Connection) -> dict | None:
    """The Arena's deck-wide state, or None before the deck's first SM-20 rating."""
    r = conn.execute("SELECT state_json FROM recall_collection WHERE id = 1").fetchone()
    return json.loads(r["state_json"]) if r else None


def save_recall_collection(conn: sqlite3.Connection, state: dict) -> None:
    """Replace the deck-wide state. Whole-blob rewrite on every rating, ~270 KB:
    the models inside it are cross-referential, so there is no smaller unit to
    write, and it does not grow with the deck."""
    conn.execute(
        "INSERT INTO recall_collection (id, state_json, updated_at) "
        "VALUES (1, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "state_json=excluded.state_json, updated_at=excluded.updated_at",
        (json.dumps(state),),
    )
    conn.commit()


def add_recall_rep(conn: sqlite3.Connection, trade_key: str, rating: int,
                   guess: str | None) -> int:
    """Record one showing, returning its row id. Append, never upsert — the
    schedule is derived state and can be rebuilt from these, so losing a rep to
    an update loses the only thing that cannot be recomputed.

    The id is what :func:`stage_recall_undo` holds onto: an undo has to delete
    *this* showing, not "the newest one for this card", which is a different row
    the moment two ratings interleave.
    """
    cur = conn.execute(
        "INSERT INTO recall_reps (trade_key, shown_at, rating, guess) "
        "VALUES (?, datetime('now'), ?, ?)",
        (trade_key, int(rating), (guess or "").strip() or None),
    )
    conn.commit()
    return int(cur.lastrowid)


def stage_recall_undo(conn: sqlite3.Connection, trade_key: str, rep_id: int,
                      *, wrote_grade: bool = False) -> None:
    """Snapshot what a rating is about to overwrite (see the ``recall_undo`` schema).

    Reads the rows as they currently stand, so it must be called **after**
    :func:`add_recall_rep` and **before** the card, collection and grade are
    saved — the whole sequence under one held lock, which is where the rating
    already runs. Replaces any previous snapshot: only the last rating is
    revertible.

    ``wrote_grade`` says this rating is about to write the trade's grade (the
    recall front is where grading happens), so the undo knows to take that back
    too — to whatever ``trade_notes.grade`` holds right now, NULL included.
    """
    row = conn.execute(
        "SELECT due, interval_d, ease, reps, lapses, sm20_state, last_review_day "
        "FROM recall_cards WHERE trade_key = ?",
        (trade_key,),
    ).fetchone()
    coll = conn.execute(
        "SELECT state_json FROM recall_collection WHERE id = 1").fetchone()
    prior = conn.execute(
        "SELECT grade FROM trade_notes WHERE trade_key = ?", (trade_key,)
    ).fetchone()
    conn.execute(
        "INSERT INTO recall_undo "
        "(id, trade_key, rep_id, card_json, collection_json, "
        " wrote_grade, grade_before, staged_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "trade_key=excluded.trade_key, rep_id=excluded.rep_id, "
        "card_json=excluded.card_json, collection_json=excluded.collection_json, "
        "wrote_grade=excluded.wrote_grade, grade_before=excluded.grade_before, "
        "staged_at=excluded.staged_at",
        (trade_key, int(rep_id),
         json.dumps(dict(row)) if row is not None else None,
         coll["state_json"] if coll is not None else None,
         int(wrote_grade),
         prior["grade"] if prior is not None else None),
    )
    conn.commit()


def pending_recall_undo(conn: sqlite3.Connection) -> dict | None:
    """The rating that can still be taken back, or None. Identity and rating
    only — enough to label a button, and no free text: this is read by the deck,
    which is the payload that must never carry anything about a card's answer."""
    r = conn.execute(
        "SELECT u.trade_key, u.staged_at, p.rating FROM recall_undo u "
        "LEFT JOIN recall_reps p ON p.id = u.rep_id WHERE u.id = 1"
    ).fetchone()
    if r is None:
        return None
    return {
        "trade_key": r["trade_key"],
        "rating": None if r["rating"] is None else int(r["rating"]),
        "staged_at": r["staged_at"],
    }


def undo_last_recall_rating(conn: sqlite3.Connection) -> dict | None:
    """Put the deck back exactly as the last rating found it, or None if there
    is nothing staged.

    Returns the showing that was removed — its card, rating and guess — so the
    caller can hand the typed read back to whoever undid the misclick.

    All three restores plus the slot's own clearing share one transaction: a
    half-applied undo would leave a schedule that no rep log explains. The card
    is written column-by-column rather than through :func:`save_recall_card`,
    because that one COALESCEs ``sm20_state`` and an undo must be able to put a
    NULL back — a card rated for the first time had none.
    """
    slot = conn.execute(
        "SELECT trade_key, rep_id, card_json, collection_json, "
        "wrote_grade, grade_before "
        "FROM recall_undo WHERE id = 1").fetchone()
    if slot is None:
        return None
    key = slot["trade_key"]
    rep = conn.execute(
        "SELECT rating, guess FROM recall_reps WHERE id = ?", (slot["rep_id"],)
    ).fetchone()
    card = json.loads(slot["card_json"]) if slot["card_json"] else None

    conn.execute("DELETE FROM recall_reps WHERE id = ?", (slot["rep_id"],))
    if card is None:
        conn.execute("DELETE FROM recall_cards WHERE trade_key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO recall_cards "
            "(trade_key, due, interval_d, ease, reps, lapses, "
            " sm20_state, last_review_day, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(trade_key) DO UPDATE SET "
            "due=excluded.due, interval_d=excluded.interval_d, ease=excluded.ease, "
            "reps=excluded.reps, lapses=excluded.lapses, "
            "sm20_state=excluded.sm20_state, "
            "last_review_day=excluded.last_review_day, "
            "updated_at=excluded.updated_at",
            (key, card["due"], float(card["interval_d"]), float(card["ease"]),
             int(card["reps"]), int(card["lapses"]),
             card["sm20_state"], card["last_review_day"]),
        )
    if slot["collection_json"] is None:
        conn.execute("DELETE FROM recall_collection WHERE id = 1")
    else:
        conn.execute(
            "INSERT INTO recall_collection (id, state_json, updated_at) "
            "VALUES (1, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "state_json=excluded.state_json, updated_at=excluded.updated_at",
            (slot["collection_json"],),
        )
    # The grade this rating wrote goes back too — usually to NULL, since the
    # front only asks an ungraded trade. Guarded by the flag rather than by
    # comparing values, so an undo cannot clobber a grade some other surface
    # wrote in between.
    if slot["wrote_grade"]:
        # ``graded_at`` (the blind-answer stamp) goes with it. NULL is right in
        # every reachable state: the front only asks an ungraded trade, so
        # ``grade_before`` is NULL — and were it ever not, a pre-blind or
        # hand-repaired grade carries no stamp either.
        conn.execute(
            "UPDATE trade_notes SET grade = ?, graded_at = NULL, "
            "updated_at = datetime('now') WHERE trade_key = ?",
            (slot["grade_before"], key),
        )
    conn.execute("DELETE FROM recall_undo WHERE id = 1")
    conn.commit()
    return {
        "trade_key": key,
        "rating": None if rep is None else int(rep["rating"]),
        "guess": None if rep is None else rep["guess"],
    }


def recall_reps_for(conn: sqlite3.Connection, trade_key: str) -> list[dict]:
    """Every showing of one card, oldest first — the card's own history, shown on
    the back so a guess can be read against the ones before it."""
    return [
        {"shown_at": r["shown_at"], "rating": int(r["rating"]), "guess": r["guess"]}
        for r in conn.execute(
            "SELECT shown_at, rating, guess FROM recall_reps "
            "WHERE trade_key = ? ORDER BY id",
            (trade_key,),
        )
    ]


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
    where they'd inflate the new model's compliance denominator. A check against a
    *retired* rule of the same model survives, so re-saving a note doesn't erase
    the score a trade earned under the old checklist.

    Rows are written for all the model's active rules (met 0 or 1), so "unmet" and
    "never reviewed" stay distinguishable: a trade with no rows was never scored.
    """
    if model_id is None:
        conn.execute("DELETE FROM trade_rule_checks WHERE trade_key = ?", (trade_key,))
        conn.commit()
        return

    own = [r["id"] for r in list_rules(conn, model_id, include_inactive=True)]
    placeholders = ",".join("?" for _ in own)
    # NOT IN () is invalid SQL; a model with no rules sweeps every check.
    keep = f"AND rule_id NOT IN ({placeholders})" if own else ""
    conn.execute(
        f"DELETE FROM trade_rule_checks WHERE trade_key = ? {keep}", (trade_key, *own)
    )
    met = set(rules_met)
    for rule in list_rules(conn, model_id):
        conn.execute(
            "INSERT INTO trade_rule_checks (trade_key, rule_id, met) VALUES (?, ?, ?) "
            "ON CONFLICT(trade_key, rule_id) DO UPDATE SET met = excluded.met",
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
