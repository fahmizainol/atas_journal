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

/** The KPIs worth plotting against P&L, in the order the picker offers them. */
export const KPI_OPTIONS: { key: keyof RegimeKpis; label: string; pct?: boolean }[] = [
  { key: "abr", label: "Above both VWAPs (ABR)", pct: true },
  { key: "bbr", label: "Below both VWAPs (BBR)", pct: true },
  { key: "quadrant_transitions_rate", label: "Quadrant transitions / hr" },
  { key: "ny_touch_hold_ratio", label: "NY +1σ touch → hold", pct: true },
  { key: "gx_touch_hold_ratio", label: "Globex +1σ touch → hold", pct: true },
  { key: "ny_upper_channel_occupancy", label: "NY upper-channel occupancy", pct: true },
  { key: "gx_upper_channel_occupancy", label: "Globex upper-channel occupancy", pct: true },
  { key: "ny_band_cross_rate", label: "NY +1σ crossings / hr" },
  { key: "ny_vwap_cross_rate", label: "NY VWAP crossings / hr" },
  { key: "longest_hold_min", label: "Longest hold above both (min)" },
  { key: "norm_spread", label: "VWAP spread (σ)" },
  { key: "spread_slope", label: "VWAP spread slope (30m)" },
  { key: "ny_vwap_slope_ppm", label: "NY VWAP slope (pts/min, 30m)" },
  { key: "ny_vwap_slope_deg", label: "NY VWAP slope (°, ATR-norm)" },
  { key: "gx_vwap_slope_ppm", label: "Globex VWAP slope (pts/min, 30m)" },
  { key: "gx_vwap_slope_deg", label: "Globex VWAP slope (°, ATR-norm)" },
  { key: "on_abr", label: "Overnight above Globex VWAP", pct: true },
  { key: "on_vwap_slope_ppm", label: "Overnight VWAP slope (pts/min, 30m)" },
  { key: "on_vwap_slope_deg", label: "Overnight VWAP slope (°, ATR-norm)" },
  { key: "on_range_pts", label: "Overnight range (pts)" },
  { key: "open_z", label: "Open in Globex σ-terms" },
];

export const CLASS_LABEL: Record<RegimeClass, string> = {
  trend_up: "Trend up",
  trend_down: "Trend down",
  balance: "Balance",
  mixed: "Mixed",
  unknown: "Unknown",
};
