// On-the-wire shapes for the interaction-tracking study (GET /api/interactions).
// Mirrors journal.sim.interactions — keep in sync with its result dict.

// One touch rescored at a fixed forward window (10/30/60 min).
export interface HorizonOutcome {
  outcome: "reject" | "accept" | "chop" | "unknown";
  mfe: number | null;
  mae: number | null;
  reaction_min: number;
}

export interface Touch {
  day: string;
  ts: number; // UTC epoch seconds (chart axis)
  hhmm: string; // ET label
  zone_px: number;
  source: string; // "ny" | "globex" | "ref"
  level_type: string; // VAH | VAL | POC | VWAP | ±1σ | ±2σ | a session ref (ONH, pd POC, …)
  label: string; // e.g. "Globex VAL"
  sources: string[]; // every source label in the clustered zone
  n_sources: number;
  nearest_other_source_dist: number | null;
  nth_touch: number;
  approach: "below" | "above";
  // Who closed the gap over the last bars before the touch: price moving to the
  // level, the level moving to price (a falling band chased by price tests
  // nothing), both, or drift (they never actually converged).
  closed_by: "price" | "level" | "both" | "drift" | "unknown";
  price_closed_pts: number | null;
  level_closed_pts: number | null;
  level_slope: "rising" | "flat" | "falling";
  level_age_min: number; // minutes since the level's anchor started
  touch_vol: number;
  signed_delta: number;
  time_bucket: "open" | "midday" | "pm";
  outcome: "reject" | "accept" | "chop" | "unknown";
  mfe: number | null;
  mae: number | null;
  reaction_min: number;
  outcomes: Record<string, HorizonOutcome>; // keyed "10" | "30" | "60"
}

export interface VaSnap {
  day: string;
  ts: number;
  hhmm: string;
  source: string;
  level_type: string;
  snap_dir: "up_over_price" | "down_under_price";
  snap_class: "creep" | "node_flip"; // boundary creep vs the VA re-seating on another node
  level_jump_pts: number;
  level_age_min: number;
  excursion_bars_before: number;
  band_at_snap: string | null;
  px: number;
  co_snaps: number; // other levels that snapped in the same minute
  reverted?: boolean;
  revert_min?: number | null; // minutes until close first reached NY VWAP; null = never
  revert_move?: number | null; // max favorable excursion to session end
  adverse_move?: number | null; // worst excursion against the reversion before it happened
  vwap_dist_pts?: number | null; // room to VWAP in the reversion direction at snap; <=0 = trivial
  cont_move?: Record<string, number>; // max run in the snap direction before the VWAP touch, per horizon
}

export interface BandState {
  day: string;
  ts: number;
  hhmm: string;
  band: string; // NY VWAP band
  gx_band: string | null; // Globex (ON) VWAP band; null when globex isn't a source
  max_band_abs: number;
  bars_since_outer_tag: number;
}

export interface AggRow {
  label: string;
  n: number;
  reject_rate: number | null;
  avg_mfe: number | null;
  avg_mae: number | null;
}

// Touch groups scored at the fixed 30m window with MEDIAN MFE/MAE, read against
// the null-baseline row the server appends (label "null baseline (phantom
// levels)", n=null). ratio = med_mfe / med_mae — ~1.0 means the cut is noise.
export interface BandContextRow {
  label: string;
  n: number | null; // null on the benchmark row
  reject_rate: number | null;
  med_mfe: number | null;
  med_mae: number | null;
  ratio: number | null;
}

// Time price spent in each NY VWAP band, tallied from the per-minute band_state
// stream. minutes = total RTH minutes in the band over the run; avg_min = per
// session (the comparable read, since the total scales with the date range).
export interface BandOccupancyRow {
  label: string; // band, in BAND_LABELS order (">+2σ" … "<-2σ")
  minutes: number;
  pct: number | null; // share of all classified minutes
  avg_min: number | null; // per-session average minutes
}

export interface VaSnapAggRow {
  label: string;
  n: number; // non-trivial snaps (had room to VWAP at entry)
  n_trivial: number; // already at/through VWAP at the snap bar — excluded from rates
  revert_rate: number | null; // by session end (upper bound)
  revert_rate_30: number | null;
  revert_rate_60: number | null;
  avg_move: number | null;
  avg_adverse: number | null;
  avg_dist: number | null; // avg room to VWAP at entry
}

// The flip trade: enter in the snap's direction, stop on a close through VWAP.
export interface VaSnapContRow {
  label: string;
  n: number;
  hold_rate_30: number | null; // never stopped within 30m
  hold_rate_60: number | null;
  avg_run_30: number | null; // mean excursion in the snap direction before the stop
  avg_run_60: number | null;
  avg_stop_dist: number | null; // entry-to-VWAP distance the stop risks
  rr_60: number | null; // avg_run_60 / avg_stop_dist
}

export interface InteractionResult {
  interactions_version: number;
  symbol: string;
  start: string;
  end: string;
  bin_size: number;
  va_pct: number;
  sources: string[];
  outcome_window_min: number;
  zone_cluster_pts: number;
  coverage: { requested_days: number; ran_days: number; skipped: string[] };
  // The lean events view: VA-snaps only. band_state (server-side aggregate
  // input) and the raw touches (the biggest block — chart dots + touches table)
  // are dropped; touch *counts* per day still come from day_index below.
  events: { va_snaps: VaSnap[] };
  day_index: Record<string, { n_touches: number; n_snaps: number }>;
}

// The on-demand detail — fetched by the Stats tab's "Compute stats" button
// rather than shipped with every run, so the Sessions/chart view loads without
// waiting on it. Carries the aggregate tables plus the raw touches (which back
// the chart's touch markers and the per-day touches table).
export interface InteractionStats {
  touches: Touch[];
  aggregates: {
    by_source: AggRow[];
    by_nth_touch: AggRow[];
    confluence_lift: AggRow[];
    by_horizon: AggRow[];
    by_band_context: BandContextRow[];
    band_occupancy: BandOccupancyRow[]; // NY VWAP
    band_occupancy_gx: BandOccupancyRow[]; // Globex (ON) VWAP; empty if no globex source
    upper_band_pullback: BandContextRow[];
    who_closed_gap: BandContextRow[]; // gap-closer attribution vs the null baseline
    acceptance_decay: BandContextRow[]; // nth-touch decay — is the level becoming fair price
    vasnap_reversion: VaSnapAggRow[];
    vasnap_by_class: VaSnapAggRow[]; // creep vs node_flip
    vasnap_confluence: VaSnapAggRow[]; // lone vs same-minute multi-level snaps
    vasnap_continuation: VaSnapContRow[];
  };
}

export interface CoverageDay {
  date: string;
  rth: boolean;
  on: boolean;
}

export interface Coverage {
  symbol: string;
  days: CoverageDay[];
}

// One saved snapshot on disk (GET /api/interactions/runs). `config` carries the
// fully resolved params, so re-committing it verbatim hits the same cache file.
export interface SavedRun {
  run_id: string;
  config: {
    symbol: string;
    start: string;
    end: string;
    bin_size: number;
    va_pct: number;
    sources: string[];
    outcome_window_min: number;
    zone_cluster_pts: number;
  };
  coverage: { requested_days: number; ran_days: number; skipped: string[] };
  n_touches: number;
  n_snaps: number;
  saved_at: number; // file mtime, epoch seconds
}

// --- Initial Balance / ORB study (GET /api/interactions/ib) -----------------
// Mirrors journal.sim.ib — session structure only, no touch events.

export interface IbBreak {
  side: "up" | "down";
  hhmm: string;
  min_after_open: number;
}

export interface IbOrbWindow {
  window: number;
  high: number;
  low: number;
  range: number;
  dir: 1 | -1 | 0;
  follow: boolean | null; // null on a doji window (no trade)
  move_pts: number | null; // signed favourable move, entry = window close
  r_mult: number | null; // move over the stop distance (candle's opposite extreme)
}

export interface IbDay {
  day: string;
  open: number;
  close: number;
  ib_high: number;
  ib_low: number;
  ib_mid: number;
  ib_range: number;
  day_high: number;
  day_low: number;
  day_range: number;
  ib_pct_of_day: number;
  broke_up: boolean;
  broke_down: boolean;
  broke_both: boolean;
  first_break: IbBreak | null;
  second_break: IbBreak | null; // only on double-break days
  ext_up_x: number;
  ext_dn_x: number;
  max_ext_x: number;
  range_x: number; // day range in IB multiples (day-type driver)
  close_pos: number; // close's position in the day range, 0..1
  day_type: "normal" | "normal_variation" | "trend" | "neutral_center" | "neutral_extreme";
  close_beyond_break: boolean | null; // single-break days: did the break hold
  gap_pts: number | null;
  gap_x: number | null; // gap over adr14
  adr14: number | null; // prior-14-session average day range
  ib_vs_adr: number | null;
  on_high: number | null;
  on_low: number | null;
  on_range: number | null;
  open_vs_on: "inside" | "above" | "below" | null;
  ib_vs_on: "inside" | "broke_high" | "broke_low" | "engulfed" | null;
  orb: Record<string, IbOrbWindow | null>; // keyed "5" | "15" | "30"
}

// {label, n, pct} with `of` the denominator when it isn't the whole run.
export interface IbRateRow {
  label: string;
  n: number;
  pct: number | null;
  of?: number;
}

// Extension distribution: quantile rows carry `value` (×IB), milestone rows `pct`.
export interface IbExtRow {
  label: string;
  n: number;
  value: number | null;
  pct: number | null;
}

// A conditioning cut (IB-width tercile, Globex relation, weekday) read through
// how directional its days turned out.
export interface IbCutRow {
  label: string;
  n: number;
  trend_rate: number | null;
  both_rate: number | null;
  med_ext_x: number | null;
  med_range_x: number | null;
}

// The Zarattini-style ORB score per window (and per gap cut).
export interface IbOrbRow {
  label: string;
  n: number;
  follow_rate: number | null;
  avg_r: number | null;
  med_r: number | null;
  med_move_pts: number | null;
}

export interface IbResult {
  ib_version: number;
  symbol: string;
  start: string;
  end: string;
  ib_minutes: number;
  orb_windows: number[];
  coverage: { requested_days: number; ran_days: number; skipped: string[] };
  days: IbDay[];
  aggregates: {
    break_rates: IbRateRow[];
    ext_distribution: IbExtRow[];
    day_types: IbRateRow[];
    break_epilogue: IbRateRow[];
    ib_width_terciles: IbCutRow[];
    globex_cuts: IbCutRow[];
    orb_follow: IbOrbRow[];
    gap_cuts: IbOrbRow[];
    weekday: IbCutRow[];
  };
}

// Per-session IB width for the Sessions table, sliced out of the widest saved IB
// snapshot (never recomputed for the shown window — `adr14` chains through prior
// sessions, so a narrow window would rescale the terciles). Days the snapshot
// doesn't cover are absent from `days`; days inside its ADR warm-up are present
// with a null `ib_vs_adr`/`width`.
export type IbWidthBucket = "narrow" | "mid" | "wide";

export interface IbSessionWidth {
  ib_range: number;
  ib_vs_adr: number | null;
  adr14: number | null;
  width: IbWidthBucket | null;
}

export interface IbSessionWidths {
  symbol: string;
  run_id: string | null; // null when no snapshot exists for the symbol yet
  source: { start: string; end: string; ib_minutes: number } | null;
  tercile_edges: [number, number]; // pinned ADR-unit edges (vol-clock §10c)
  days: Record<string, IbSessionWidth>;
}

export interface IbParams {
  symbol: string;
  start: string;
  end: string;
  ib_minutes?: number;
}

// --- Weekly VWAP study (GET /api/interactions/weekly-vwap) -------------------
// Mirrors journal.sim.weekly_vwap — keep in sync with weekly_vwap.py's result
// dict. Weekly-anchored VWAP envelope: where the open prints in it, which side
// of the mid the day trades, and what first band touches do.

// One weekly level's touch record for a session. The optional fields only
// appear once `touched` is true (band rows carry the fade extras).
export interface WeeklyVwapTouch {
  name: string;
  touched: boolean;
  min_after_open?: number;
  level?: number;
  toward_pts?: number; // excursion back toward the weekly mid after the touch
  beyond_pts?: number; // excursion through the band away from the mid
  hit_mid?: boolean;
}

export interface WeeklyVwapDay {
  day: string;
  first_session: boolean; // week's first session — envelope is still seasoning
  open: number;
  close: number;
  wk_mid_open: number; // weekly VWAP at the bell
  wk_std_open: number;
  wk_mid_close: number;
  open_dist_pts: number;
  open_dist_sigma: number | null; // null while the envelope has no σ yet
  close_dist_sigma: number | null;
  side: "above" | "below";
  drift_pts: number; // open → close, signed
  drift_with_side: boolean | null; // did the day drift away from the mid
  touches: WeeklyVwapTouch[];
}

// Open-position, side and weekday cuts share one row shape: days conditioned on
// the open's place in the envelope, read through how they drifted.
export interface WeeklyVwapPosRow {
  label: string;
  n: number;
  med_drift_pts: number | null;
  with_side_rate: number | null;
  med_close_dist_sigma: number | null;
}

// Touch rate per weekly level, counted only on approaches from the mid's side.
export interface WeeklyVwapTouchRateRow {
  label: string;
  n: number;
  of: number; // eligible days (the denominator)
  touch_rate: number | null;
  med_min_after_open: number | null;
}

// First band touch: fade back to the weekly mid vs break on through.
export interface WeeklyVwapFadeRow {
  label: string;
  n: number;
  hit_mid_rate: number | null;
  med_toward_pts: number | null;
  med_beyond_pts: number | null;
  med_edge_pts: number | null; // toward − beyond
}

export interface WeeklyVwapResult {
  weekly_vwap_version: number;
  symbol: string;
  start: string;
  end: string;
  outcome_window_min: number;
  coverage: {
    requested_days: number;
    ran_days: number;
    seasoned_days: number; // non-first sessions — the rows the cuts trust
    skipped: { day: string; why: string }[];
  };
  days: WeeklyVwapDay[];
  aggregates: {
    open_position: WeeklyVwapPosRow[];
    side: WeeklyVwapPosRow[];
    touch_rates: WeeklyVwapTouchRateRow[];
    band_fades: WeeklyVwapFadeRow[];
    weekday: WeeklyVwapPosRow[];
  };
}

export interface WeeklyVwapParams {
  symbol: string;
  start: string;
  end: string;
  outcome_window_min?: number;
}

// The run config the config-bar commits on "Run". Optional fields fall back to
// server defaults (bin_size -> the instrument tick grid).
export interface InteractionParams {
  symbol: string;
  start: string;
  end: string;
  bin_size?: number;
  va_pct?: number;
  sources?: string[];
  outcome_window_min?: number;
  zone_cluster_pts?: number;
}
