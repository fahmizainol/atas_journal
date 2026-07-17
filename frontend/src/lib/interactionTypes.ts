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
  source: string; // "ny" | "globex"
  level_type: string; // VAH | VAL | POC | ±1σ | ±2σ
  label: string; // e.g. "Globex VAL"
  sources: string[]; // every source label in the clustered zone
  n_sources: number;
  nearest_other_source_dist: number | null;
  nth_touch: number;
  approach: "below" | "above";
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
  level_jump_pts: number;
  level_age_min: number;
  excursion_bars_before: number;
  band_at_snap: string | null;
  px: number;
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
  events: { touches: Touch[]; va_snaps: VaSnap[]; band_state: BandState[] };
  aggregates: {
    by_source: AggRow[];
    by_nth_touch: AggRow[];
    confluence_lift: AggRow[];
    by_horizon: AggRow[];
    by_band_context: BandContextRow[];
    band_occupancy: BandOccupancyRow[]; // NY VWAP
    band_occupancy_gx: BandOccupancyRow[]; // Globex (ON) VWAP; empty if no globex source
    upper_band_pullback: BandContextRow[];
    vasnap_reversion: VaSnapAggRow[];
    vasnap_continuation: VaSnapContRow[];
  };
  day_index: Record<string, { n_touches: number; n_snaps: number }>;
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
