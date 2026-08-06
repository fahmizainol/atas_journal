export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Candle resolution for the strategy charts. "tick" is the engine's own
// tick-count bars (the candles it actually traded); the minute resolutions
// rebuild them as clock-time bars over the same ticks as a familiar context view.
export type ChartResolution = "tick" | "1m" | "3m" | "5m" | "15m";

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

// A moving-average point (currently the 9/20 EMA on the 1-minute grid). Stamped
// on the minute the value was computed on; on a tick-bar chart the frontend
// samples it onto the drawn bar grid. Only the Interactions day-chart sends it.
export interface EmaPoint {
  time: number;
  value: number;
}

// A Wilder RSI point — the momentum oscillator (0-100) the RSI family is built on.
// Unlike the EMA it tracks the drawn timeframe: the backend computes it on the
// drawn-bar closes (14 min at 1m, 70 min at 5m, 14 tick-bars on tick charts), so
// each point is stamped on its bar. Drawn in its own pane under the candles. Only
// the Interactions day-chart sends it.
export interface RsiPoint {
  time: number;
  value: number;
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

// Cumulative volume delta: the running sum of signed aggressor volume (buy market
// orders minus sell market orders) as of each bar's close, anchored at the first
// drawn bar. Only the sim's charts send it — it needs the tape's aggressor side,
// which the journal's Databento bars don't carry. Drawn in its own pane under the
// candles. See api/sim_charts._cvd.
export interface CvdPoint {
  time: number;
  value: number;
}

// A regular price/CVD divergence: between two swing pivots, price and
// cumulative delta printed opposite-sloping highs (bear) or lows (bull).
// Endpoints are in CVD coordinates — v1/v2 are delta values, t1/t2 bar times —
// so the A→B line is drawn on the CVD pane. See api/sim_charts._cvd_divergences.
export interface CvdDivergence {
  kind: "bear" | "bull";
  t1: number;
  v1: number;
  t2: number;
  v2: number;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  color: string;
  text?: string;
}

// Initial Balance — high/low of the first 60 min of RTH (9:30–10:30 ET), the
// same window the IB/ORB study measures, so what the chart draws is what the
// study's break/extension stats were computed against. `start`/`formed`/`end`
// are drawn-bar times: the lines span start (the bell) → end (last bar), the
// 1×/1.5×/2× extension guides span formed (the IB's completion) → end. Only the
// sim/Lab charts send it; absent when the session's data ends inside the window.
export interface IbOverlay {
  high: number;
  low: number;
  start: number;
  formed: number;
  end: number;
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
  /** Weekly anchor (the week's first Globex open), context only: no engine
   * trades it, so it is never `vwap_anchor`. Absent when the week has a hole. */
  vwap_weekly?: VwapPoint[];
  /** Which anchor the engine actually traded; the other is drawn as context.
   * Only the sim's charts set it — the journal's draw both as reference. */
  vwap_anchor?: "globex" | "ny";
  profile_globex?: ProfilePoint[];
  profile_ny?: ProfilePoint[];
  /** 9/20/50/200 EMA on the 1-minute grid — day-trading convention, context
   * only. 9/20 are the fast pullback pair, 50/200 the slower trend reference. */
  ema9?: EmaPoint[];
  ema20?: EmaPoint[];
  ema50?: EmaPoint[];
  ema200?: EmaPoint[];
  /** Wilder RSI(14) on the drawn timeframe, in its own pane. Context only.
   * Only the sim's trade chart sends it — the journal reconstruction omits it. */
  rsi?: RsiPoint[];
  atr_points?: ATRPoint[];
  markers?: ChartMarker[];
  price_lines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  ib?: IbOverlay | null;
  trade_rect?: TradeRect | null;
  excursion?: Omit<Excursion, "available">;
  footprint?: Footprint;
  cvd?: CvdPoint[];
  cvd_divergences?: CvdDivergence[];
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
  /** Weekly anchor (the week's first Globex open), context only: no engine
   * trades it, so it is never `vwap_anchor`. Absent when the week has a hole. */
  vwap_weekly?: VwapPoint[];
  /** Which anchor the engine actually traded; the other is drawn as context.
   * Only the sim's charts set it — the journal's draw both as reference. */
  vwap_anchor?: "globex" | "ny";
  profile_globex?: ProfilePoint[];
  profile_ny?: ProfilePoint[];
  atr_points?: ATRPoint[];
  /** 9/20/50/200 EMA on the 1-minute grid — day-trading convention, context
   * only. 9/20 are the fast pullback pair, 50/200 the slower trend reference. */
  ema9?: EmaPoint[];
  ema20?: EmaPoint[];
  ema50?: EmaPoint[];
  ema200?: EmaPoint[];
  /** Wilder RSI(14) on the drawn timeframe, in its own pane. Context only. */
  rsi?: RsiPoint[];
  markers?: ChartMarker[];
  levels?: PriceLineSpec[];
  ib?: IbOverlay | null;
  trades?: TradeRect[];
  footprint?: Footprint;
  cvd?: CvdPoint[];
  cvd_divergences?: CvdDivergence[];
  tick_size?: number;
  /** Dollars per full point per contract (contract spec) — for the ruler's $/lot. */
  point_value?: number;
}
