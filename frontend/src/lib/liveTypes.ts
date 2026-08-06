// What the /live endpoints say. Mirrors api/routers/live.py.

import type { SessionContext, SessionPayload } from "./replayEngine";

export interface LiveStatus {
  running: boolean;
  gen?: string;
  symbol?: string;
  date?: string;
  rows?: number;
  closed?: boolean;
  /** "fake" for a cached day replayed at wall-clock speed, "rithmic" for the
   *  real ticker plant, "resumed" for a session rebuilt from disk after a
   *  restart with no feed attached — that last one has a tape and is not
   *  growing, which must not be left to look like a quiet market. */
  source?: string;
  feed_running?: boolean;
  speed?: number | null;
  last_tick_utc?: string | null;
  /** True only on a real feed: the fake one records nothing, deliberately. */
  recording?: boolean;
  recorded_rows?: number | null;
  /** Whether the shelf is being re-run over the day. Off is a state, not a
   *  quiet market — `SignalPanel` has to say which one it is showing. */
  signals?: boolean;
  /** Whether each pass is being written down for Phase 6's prefix check. Follows
   *  the recording: a journal with no tape behind it has nothing to check. */
  journalling?: boolean;
  /** Ticks that reached the tape while nothing was recording. A permanent hole —
   *  the tape is in memory, the chunks are what survives the process. */
  unrecorded_rows?: number;
  /** False for the simulated feed, which records nothing by design. */
  can_record?: boolean;
  feed_status?: RithmicStatus | null;
}

/** What `RithmicFeed.status()` reports. Absent for the fake feed. */
export interface RithmicStatus {
  source: string;
  symbol: string;
  exchange: string;
  gateway: string;
  system: string;
  connected: boolean;
  running: boolean;
  /** A replay is in flight. A whole session is tens of seconds of it, during
   *  which the tape is empty — which reads as a broken page unless it is said. */
  backfilling?: boolean;
  error: string | null;
  /** Counters that only a recording can answer — `clamped` is how often
   *  exchange stamps arrived out of order and had to be pushed forward. */
  stats: Record<string, number>;
  /** Recent latency in microseconds, over the last couple of thousand prints:
   *  `hop_*` is the exchange's stamp to Rithmic's send stamp (both carried in one
   *  message, so no local clock is involved), `lag_*` is arrival to publish
   *  inside the API process. There is deliberately no end-to-end figure — that
   *  needs the host clock, which measured a second off in a direction that moves
   *  between runs, so any number would be reporting WSL2's drift as latency. A
   *  leg with no samples is absent rather than zero. */
  timing?: Record<string, number>;
  /** One entry per backfill: the connect, plus one per reconnect, since the same
   *  replay repairs the hole a dropped socket leaves. `error` is set instead of
   *  the row counts when a backfill failed — which costs the stretch in front of
   *  the connection and nothing else, so it is reported rather than thrown. */
  backfills?: LiveBackfill[];
  /** Earlier sessions this feed filled in behind the live stream — the days
   *  nobody was connected for. Runs on the feed's own connection, because
   *  Rithmic allows one session per login. */
  harvested?: { date: string; skipped: boolean; rows: number; error?: string }[];
}

export interface LiveBackfill {
  from: string;
  rows: number;
  seconds?: number;
  dropped_seam?: number;
  aggregated?: number;
  first?: string;
  last?: string;
  error?: string;
}

/**
 * One session in the live store, as `/live/recordings` lists it.
 *
 * This is the *recorded* store (`data/live/ticks/`, Rithmic), which is not the
 * research cache (`data/cache/ticks/`, Databento) and is deliberately never
 * mixed with it: the Databento corpus is the independent reference a recorded
 * day gets checked against. A day in this list is not replayable on the
 * Simulator, and its absence from there is not a bug.
 */
export interface LiveRecording {
  symbol: string;
  date: string;
  chunks: number;
  rows: number | null;
  closed: boolean | null;
  last_tick_utc: string | null;
  updated_at: string | null;
  stats: Record<string, number>;
  /** How the day was come by, derived server-side from what survives on disk —
   *  see `_kind_of` in api/routers/live.py, which is where the evidence order is
   *  argued. The difference is not cosmetic: a harvested day has no signal
   *  journal and carries Rithmic's clock rather than the exchange's. */
  kind: "watched" | "filled" | "harvest" | "unknown";
  /** The shelf's mode as the session ran it, when a session wrote this manifest. */
  shadow: "on" | "off" | null;
  /** Strategies with a signal journal for the day. Empty on a harvested day is
   *  an honest absence — nothing recorded what the shelf believed, and nothing
   *  can reconstruct it. */
  signals: string[];
  harvest: { complete: boolean; covered: boolean; error: string | null; rows: number } | null;
  /** Exchange stamps that arrived out of order and had to be pushed forward. */
  clamped: number;
  /** Ticks that reached the tape with no recorder attached — a permanent hole. */
  unrecorded_rows: number;
}

/**
 * What is still reachable for one contract, and how long that lasts.
 *
 * Two ceilings, and only the first moves: `floor` is the trailing 120 days
 * Rithmic will replay for a *listed* contract, so an un-harvested session ages
 * out on a rolling basis. `expiry` is the cliff — an expired contract serves
 * nothing at any depth, so whatever is still missing on that date is missing
 * permanently.
 */
export interface LiveContract {
  symbol: string;
  replay_days: number;
  floor: string;
  expiry: string | null;
  days_to_expiry: number | null;
  sessions: number;
  recorded: number;
  missing: number;
  oldest_missing: string | null;
  /** Weekday sessions in the window with nothing at all in the store. Holidays
   *  are in here: they cannot be told from the calendar for a pinned contract,
   *  so a few of these are days the exchange did not trade. */
  missing_dates: string[];
}

export interface LiveRecordings {
  recordings: LiveRecording[];
  contracts: LiveContract[];
}

/** One prior session with tape behind it, and which store answered. */
export interface LiveHistoryDay {
  date: string;
  /** `"cache"` (Databento) or `"live"` (recorded). Resolved server-side,
   *  cache-first, so a day held in both draws the same bars here as it does in
   *  the Simulator. */
  source: string;
}

/** What `/live/history/days` found behind a session. */
export interface LiveHistoryDays {
  symbol: string;
  date: string;
  requested: number;
  /** Oldest first. Fewer than `requested` means the store ran out, not an error. */
  days: LiveHistoryDay[];
  /** Weekdays inside the returned span with no tape in either store. Reported
   *  rather than skipped: gluing across a gap would draw a continuous chart out
   *  of a discontinuous week. */
  missing: string[];
}

/** The day's header: everything about the session that is not a tick.
 *
 *  Field-for-field a `SessionPayload` minus the tape and minus `session_end_ms`,
 *  which a session in progress does not have. `sessionPayloadFor` below closes
 *  that gap so the same `ReplayEngine` constructor takes both. */
export interface LiveHeader {
  gen: string;
  symbol: string;
  root: string;
  date: string;
  tz: string;
  tick_size: number;
  point_value: number;
  rows: number;
  closed: boolean;
  /** Null until the first tick lands — there is genuinely no answer before then,
   *  and it is what stops the client building an engine over an empty tape. */
  session_start_ms: number | null;
  rth_open_ms: number;
  rth_close_ms: number;
  globex_anchor_ms: number | null;
  weekly_seed: number[] | null;
  has_overnight: boolean;
  context: SessionContext | null;
}

/** One engine trade, as `run_session` returns it. The live surface reads a
 *  handful of these fields; the rest ride along because a signal is the whole
 *  row the backtest would have written, not a summary of it. */
export interface ShadowTrade {
  session: string;
  direction: string;
  entry_ts_utc: string;
  exit_ts_utc: string | null;
  entry_idx: number;
  exit_idx: number | null;
  avg_entry: number;
  avg_exit: number | null;
  stop_price: number | null;
  target_price: number | null;
  exit_reason: string | null;
  points: number | null;
  r_multiple: number | null;
  net_pnl: number | null;
}

export interface ShadowStrategy {
  slug: string;
  name: string;
  version: string;
  /** "rth" | "globex" — which window its frame is cut to. */
  session: string;
  /** The run whose config this is being shadowed under. */
  baseline_run_id: string;
  ran: boolean;
  rows_at_last_run: number;
  error: string | null;
  trades: ShadowTrade[];
  /** Entries a gate blocked — the ghosts. Counted, not listed, on this surface. */
  vetoed: ShadowTrade[];
}

export interface LiveSignals {
  gen: string;
  rows: number;
  /** Whether the shelf is running. When false the strategy rows are the last
   *  thing it said before being switched off, not a live reading. */
  enabled: boolean;
  journalling: boolean;
  strategies: ShadowStrategy[];
  skipped: { slug: string; reason: string }[];
  regime: {
    /** Checkpoints whose cutoff has passed and whose KPIs are therefore frozen.
     *  A checkpoint not in here is one no gate can honestly answer at yet. */
    frozen: string[];
    class: string | null;
    texture: string | null;
  };
}

/**
 * Dress a live header as the payload `ReplayEngine` and `decodeTape` expect.
 *
 * The engine reads six things off a session — the two bell instants, the Globex
 * anchor, the weekly seed, whether there is an overnight, and where the session
 * starts in the tape it was handed — and a live header carries all six. The tape
 * fields are zeroed because the ticks arrive through `/live/tape` instead, and
 * `session_end_ms` is set to the start: a live day has no end, and `liveSource`
 * never asks for one (only `replaySource` clamps against it).
 */
export function sessionPayloadFor(h: LiveHeader, startMs: number): SessionPayload {
  return {
    symbol: h.symbol,
    root: h.root,
    date: h.date,
    tz: h.tz,
    tick_size: h.tick_size,
    point_value: h.point_value,
    n: 0,
    t0: startMs,
    dt: [],
    price0: 0,
    dp: [],
    size: [],
    side: "",
    session_start_ms: startMs,
    session_end_ms: startMs,
    rth_open_ms: h.rth_open_ms,
    rth_close_ms: h.rth_close_ms,
    default_start_ms: startMs,
    globex_anchor_ms: h.globex_anchor_ms,
    weekly_seed: h.weekly_seed,
    has_overnight: h.has_overnight,
    has_post: false,
    context: h.context,
  };
}
