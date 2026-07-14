// Session regime — what kind of day it was, measured from the two anchored VWAPs.
// Mirrors src/journal/sim/regime.py. A regime belongs to the (symbol, date), not
// to any run: the same artifact is joined to whichever run's P&L is on screen.

/** The checkpoints a KPI set is snapshotted at. Anything but `eod` is what was
 * knowable at that clock time — the distinction the artifact exists to preserve,
 * so a KPI read at 09:45 can never be quietly standing in for hindsight. */
export const CHECKPOINTS = ["09:30", "09:45", "10:30", "12:00", "eod"] as const;
export type Checkpoint = (typeof CHECKPOINTS)[number];

/** Provisional — thresholds in regime.classify are a first guess, not a fit. */
export type RegimeClass = "trend_up" | "trend_down" | "balance" | "mixed" | "unknown";

/** Where a close sits relative to the anchors. The `on_*` states are pre-RTH,
 * where only the Globex anchor exists. */
export type RegimeState =
  | "above_both"
  | "below_both"
  | "above_gx_only"
  | "above_ny_only"
  | "on_above_gx"
  | "on_below_gx";

/** Every KPI is nullable: a day with no overnight has no Globex anchor and so no
 * dual-VWAP metrics at all, and a checkpoint before any bar closed has none of
 * them. Null means "not computable here", never zero. */
export interface RegimeKpis {
  // Overnight priors — Globex-anchored, fixed at the bell, so a 09:30 decision
  // can read them without leaking the session it is predicting.
  on_abr: number | null;
  on_band_cross_rate: number | null;
  on_range_pts: number | null;
  open_z: number | null;
  /** Globex VWAP slope over the last 30 min of the overnight (09:00→09:30) —
   * the one slope that is fully formed before the first entry. Same unit
   * conventions as the intraday slopes. */
  on_vwap_slope_ppm: number | null;
  on_vwap_slope_deg: number | null;

  // Band behaviour per anchor: does price respect the σ envelope, or slice it?
  ny_band_cross_rate: number | null;
  ny_upper_channel_occupancy: number | null;
  /** With the channel occupancies these partition the session: above +2σ,
   * +1σ..+2σ, ±1σ, -2σ..-1σ, below -2σ — each close is in exactly one. */
  ny_above_dev2_occupancy: number | null;
  ny_middle_band_occupancy: number | null;
  ny_lower_channel_occupancy: number | null;
  ny_below_dev2_occupancy: number | null;
  ny_touch_hold_ratio: number | null;
  ny_lower_touch_hold_ratio: number | null;
  ny_vwap_cross_rate: number | null;
  /** VWAP slope over the last 30 min. `ppm` is the native unit (points per
   * minute); `deg` is the same slope as an angle under the fixed convention
   * "1 ATR of rise per minute of run = 45°" — an angle is meaningless without
   * such a convention, since it otherwise depends on the chart's aspect ratio. */
  ny_vwap_slope_ppm: number | null;
  ny_vwap_slope_deg: number | null;
  gx_band_cross_rate: number | null;
  gx_upper_channel_occupancy: number | null;
  gx_above_dev2_occupancy: number | null;
  gx_middle_band_occupancy: number | null;
  gx_lower_channel_occupancy: number | null;
  gx_below_dev2_occupancy: number | null;
  gx_touch_hold_ratio: number | null;
  gx_lower_touch_hold_ratio: number | null;
  gx_vwap_cross_rate: number | null;
  gx_vwap_slope_ppm: number | null;
  gx_vwap_slope_deg: number | null;

  // Dual-VWAP: the user's own read — the model works when price holds above both.
  abr: number | null;
  bbr: number | null;
  longest_hold_min: number | null;
  longest_hold_below_min: number | null;
  quadrant_transitions_rate: number | null;
  norm_spread: number | null;
  spread_slope: number | null;
  /** Share of RTH where the Globex upper channel contains the session's — the
   * wrap geometry where a pullback through the session +1σ still has the Globex
   * +1σ underneath it. */
  upper_wrap_occupancy: number | null;
  /** Mean (session +1σ − Globex +1σ) in session-σ units. Positive = the Globex
   * line runs below the session's, a second floor under the band. */
  upper_dev1_gap_sigma: number | null;
  /** Of closes that broke the session +1σ while the Globex +1σ sat below it,
   * the share where the lows held the Globex line and price recovered — the
   * "bounced at Globex's dev1 instead of the session's" event. */
  gx_upper_rescue_ratio: number | null;

  /** Minutes of RTH the snapshot covers. 0 at the bell. */
  bars: number;
}

export type RegimeCheckpoints = Record<Checkpoint, RegimeKpis>;

/** One day, without its ribbon — what the range endpoint serves. */
export interface RegimeDaySummary {
  date: string;
  class: RegimeClass;
  /** The overnight ticks were never bought, so this day has NY-anchored KPIs only. */
  partial: boolean;
  checkpoints: RegimeCheckpoints;
}

export interface RegimeDay extends RegimeDaySummary {
  symbol: string;
  version: number;
  /** Per-minute quadrant state, on the same time axis as that day's candles. */
  ribbon: { time: number; state: RegimeState }[];
}

export interface RegimeRange {
  days: RegimeDaySummary[];
  /** Sessions with no ticks on disk. A hole, not a flat day — and never fetched:
   * charts and KPIs read the tick cache, they never spend at Databento. */
  skipped: string[];
}

// --- the regime-vs-P&L study ------------------------------------------------
//
// All of it is computed server-side (journal.sim.regime_pnl) and snapshotted to
// <run>/regime_pnl.json. The browser used to score this itself, which meant the
// only way to read the answer was to mount the panel — no API, no file, nothing
// an LLM could see. It renders the numbers now; it does not produce them.
//
// Which KPIs exist, and how to print them, arrives in the payload for the same
// reason: a picker with its own copy of the list can offer a KPI the scores were
// never computed for.

/** A day's KPI value and what the run made that day, as the picker labels it. */
export interface KpiSpec {
  key: keyof RegimeKpis;
  label: string;
  pct?: boolean;
}

/** One third of the traded days, by KPI value. No member list — recover it from
 * `days` and this band's [lo, hi]; see regime_pnl._band. */
export interface RegimeBand {
  band: "low" | "mid" | "high";
  days: number;
  net: number;
  avg_net: number | null;
  trades: number;
  win_rate: number | null;
  lo: number | null;
  hi: number | null;
}

/** One KPI, scored against the run's daily P&L at one checkpoint. */
export interface BoardRow extends KpiSpec {
  /** Spearman ρ against the day's net. */
  rho: number;
  /** Avg net of a day in the top third minus one in the bottom third — the money
   * answer: "what does a day in the good band pay over a day in the bad one". */
  edge: number;
  /** Win-rate points, top third minus bottom third. */
  win_edge: number;
  /** Share of shuffled P&Ls that beat this |ρ|. Low = hard to get by luck. */
  luck: number;
  /** Clears the multiple-testing bar. NOT "is true". */
  holds: boolean;
  days: number;
  bands: RegimeBand[];
}

export interface Board {
  /** The Bonferroni line for a family this size — blunt, but it errs the safe way. */
  luck_bar: number;
  /** Roughly how many rows should clear a plain 5% bar by chance alone. */
  expected_false_positives: number;
  holds: number;
  /** Ranked by |edge|. */
  rows: BoardRow[];
}

export interface ClassBucket {
  class: RegimeClass;
  label: string;
  days: number;
  net: number;
  avg_net: number;
  trades: number;
  win_rate: number | null;
  dates: string[];
}

/** One traded session: the day's regime, and what the run made in it. */
export interface StudyDay {
  date: string;
  class: RegimeClass;
  partial: boolean;
  net: number;
  trades: number;
  wins: number;
}

export interface RegimeStudy {
  stats_version: number;
  regime_version: number;
  permutations: number;
  symbol: string;
  start: string;
  end: string;
  checkpoints: Checkpoint[];
  kpis: KpiSpec[];
  sessions_in_range: number;
  /** Sessions the run covered but never traded. Left out of every score: a zero
   * from "no setup armed" is not a zero from "traded flat". */
  untraded_days: number;
  traded_days: number;
  /** Sessions with no cached ticks, and so no regime at all. */
  skipped: string[];
  days: StudyDay[];
  class_buckets: ClassBucket[];
  boards: Record<Checkpoint, Board>;
}

export const CLASS_LABEL: Record<RegimeClass, string> = {
  trend_up: "Trend up",
  trend_down: "Trend down",
  balance: "Balance",
  mixed: "Mixed",
  unknown: "Unknown",
};
