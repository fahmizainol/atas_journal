export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Anchored VWAP with both deviation bands. Emitted once per anchor (Globex
// 18:00 ET, NY 09:30 ET) — see api/charts_data.VWAP_ANCHORS.
export interface VwapPoint {
  time: number;
  middle: number;
  upper1: number;
  lower1: number;
  upper2: number;
  lower2: number;
}

export interface ATRPoint {
  time: number;
  atr: number;
}

// Developing volume profile: the value area as it stood at each bar's close,
// accumulated from an anchor. Emitted only by the sim (api/sim_charts), as two
// series — one anchored at the Globex open (18:00 ET), one at the NY open (09:30
// ET) — mirroring the two anchored VWAPs; see src/journal/sim/profile.py.
// Distinct from the chart's own volume-profile overlay, which is a *whole-range*
// profile drawn as a histogram; this one is a time series you can watch move.
export interface ProfilePoint {
  time: number;
  poc: number;
  vah: number;
  val: number;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  color: string;
  text?: string;
}

export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}

// Per-trade numbers behind the hover tooltip. Only the sim's charts send this;
// journal day charts leave it unset and simply get no tooltip.
export interface TradeRectStats {
  trade_no: number;
  entry_hms: string;
  exit_hms: string;
  duration_s: number;
  avg_entry: number;
  avg_exit: number;
  stop_price: number;
  stop_ticks: number;
  exit_reason: string;
  r_multiple: number;
  band_width_ticks: number;
}

export interface TradeRect {
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  net_pnl: number;
  profitable: boolean;
  stats?: TradeRectStats;
}

export interface Excursion {
  available: boolean;
  has_data?: boolean;
  mfe_usd?: number;
  mae_usd?: number;
  exit_efficiency?: number | null;
  avg_atr_pts?: number | null;
  avg_atr_usd?: number | null;
}

// Real volume-at-price per bar — `footprint[i]` is the [price, size] pairs of
// every trade inside `bars[i]`. Only the sim's charts send it (they hold the tape
// the bars were built from); the journal's Databento bars have no such thing, and
// their volume profile falls back to an estimate. See lib/volumeProfile.
export type Footprint = number[][][];

export interface TradeChartData {
  available: boolean;
  bars?: Bar[];
  vwap_globex?: VwapPoint[];
  vwap_ny?: VwapPoint[];
  /** Which anchor the engine actually traded; the other is drawn as context.
   * Only the sim's charts set it — the journal's draw both as reference. */
  vwap_anchor?: "globex" | "ny";
  profile_globex?: ProfilePoint[];
  profile_ny?: ProfilePoint[];
  atr_points?: ATRPoint[];
  markers?: ChartMarker[];
  price_lines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  trade_rect?: TradeRect | null;
  excursion?: Omit<Excursion, "available">;
  footprint?: Footprint;
  tick_size?: number;
  /** Dollars per full point per contract (contract spec) — for the ruler's $/lot. */
  point_value?: number;
}

export interface DayChartData {
  available: boolean;
  instrument?: string;
  bars?: Bar[];
  vwap_globex?: VwapPoint[];
  vwap_ny?: VwapPoint[];
  /** Which anchor the engine actually traded; the other is drawn as context.
   * Only the sim's charts set it — the journal's draw both as reference. */
  vwap_anchor?: "globex" | "ny";
  profile_globex?: ProfilePoint[];
  profile_ny?: ProfilePoint[];
  atr_points?: ATRPoint[];
  markers?: ChartMarker[];
  levels?: PriceLineSpec[];
  trades?: TradeRect[];
  footprint?: Footprint;
  tick_size?: number;
  /** Dollars per full point per contract (contract spec) — for the ruler's $/lot. */
  point_value?: number;
}
