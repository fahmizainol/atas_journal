// On-the-wire shapes for the interaction-tracking study (GET /api/interactions).
// Mirrors journal.sim.interactions — keep in sync with its result dict.

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
  touch_vol: number;
  signed_delta: number;
  time_bucket: "open" | "midday" | "pm";
  outcome: "reject" | "accept" | "chop" | "unknown";
  mfe: number | null;
  mae: number | null;
  reaction_min: number;
}

export interface VaSnap {
  day: string;
  ts: number;
  hhmm: string;
  source: string;
  level_type: string;
  snap_dir: "up_over_price" | "down_under_price";
  level_jump_pts: number;
  excursion_bars_before: number;
  band_at_snap: string | null;
  px: number;
  reverted?: boolean;
  revert_move?: number | null;
}

export interface BandState {
  day: string;
  ts: number;
  hhmm: string;
  band: string;
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

export interface VaSnapAggRow {
  label: string;
  n: number;
  revert_rate: number | null;
  avg_move: number | null;
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
    vasnap_reversion: VaSnapAggRow[];
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
