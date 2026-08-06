import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { emaPalette, ibPalette, palette, profilePalette, regimePalette, vwapPalette } from "../../theme";
import { TradeRectanglePrimitive } from "./TradeRectanglePrimitive";
import { RulerPrimitive } from "./RulerPrimitive";
import { MarkerPrimitive } from "./MarkerPrimitive";
import { CvdDivergencePrimitive } from "./CvdDivergencePrimitive";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import { VolumeProfilePrimitive } from "./VolumeProfilePrimitive";
import { RangeProfilePrimitive } from "./RangeProfilePrimitive";
import { InteractionPrimitive } from "./InteractionPrimitive";
import { IndicatorLegend, type IndicatorKey, type LegendItem } from "./IndicatorLegend";
import { ChartToolButton } from "./ChartToolButton";
import {
  loadIndicatorVisibility,
  saveIndicatorVisibility,
  type IndicatorVisibility,
} from "../../lib/chartPrefs";
import {
  computeTickProfile,
  computeVolumeProfile,
  type VolumeProfile,
} from "../../lib/volumeProfile";
import type {
  ATRPoint,
  Bar,
  ChartMarker,
  CvdPoint,
  CvdDivergence,
  EmaPoint,
  Footprint,
  IbOverlay,
  PriceLineSpec,
  ProfilePoint,
  RsiPoint,
  TradeRect,
  VwapPoint,
} from "../../lib/chartTypes";
import type { Touch, VaSnap } from "../../lib/interactionTypes";

interface Props {
  bars: Bar[];
  vwapGlobex?: VwapPoint[];
  vwapNy?: VwapPoint[];
  /** Weekly anchor (the week's first Globex open) — context only, no engine
   * trades it. Absent when the week's prior sessions aren't all on disk. */
  vwapWeekly?: VwapPoint[];
  /**
   * Developing value areas (POC/VAH/VAL as of each bar's close), one per VWAP
   * anchor: `profileGlobex` accumulates from the 18:00 open, `profileNy` from the
   * 09:30 bell. Both are supplied by the sim on every chart and drawn together —
   * which anchor a rule actually read is the run's config, not the picture.
   */
  profileGlobex?: ProfilePoint[];
  profileNy?: ProfilePoint[];
  /**
   * 9/20/50/200 EMA on the 1-minute grid — the day-trading convention, drawn as
   * context lines over the candles (9/20 the fast pullback pair, 50/200 the
   * slower trend reference). Stamped on the minute they were computed on, so on a
   * tick-bar chart they are sampled onto the drawn bar grid (a bar takes the
   * latest EMA at or before its close). Each has its own legend toggle.
   */
  ema9?: EmaPoint[];
  ema20?: EmaPoint[];
  ema50?: EmaPoint[];
  ema200?: EmaPoint[];
  /**
   * Wilder RSI(14), drawn as a line in its own oscillator pane under the candles
   * (0-100, with 30/50/70 guides). Unlike the EMA it tracks the *drawn* timeframe
   * — the backend computes it on the drawn-bar closes, so it arrives already
   * stamped on the bar grid. Only the Interactions Lab supplies it. See RsiPoint.
   */
  rsi?: RsiPoint[];
  atrPoints?: ATRPoint[];
  /**
   * Cumulative volume delta (signed aggressor volume, running sum) per bar, drawn
   * as a line in its own pane under the candles. Only the sim's charts supply it —
   * it needs the tape's aggressor side. See lib/chartTypes.CvdPoint.
   */
  cvd?: CvdPoint[];
  /**
   * Price/CVD divergences, drawn as A→B lines on the CVD pane (not the price
   * candles). Rides with the CVD series — shown only when CVD is on, since the
   * divergence is a statement about the delta line. See CvdDivergencePrimitive.
   */
  cvdDivergences?: CvdDivergence[];
  markers?: ChartMarker[];
  /**
   * Level-interaction overlay from the Interactions Lab: touch dots (coloured by
   * outcome) and VA-snap markers. Optional — only the Interactions page sends them.
   */
  touches?: Touch[];
  vaSnaps?: VaSnap[];
  priceLines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  /**
   * Initial Balance (first 60 min of RTH): high/low drawn as flat segments from
   * the bell to the close, plus faint 1×/1.5×/2× extension guides from where the
   * IB completes. A single overlay for the single-session sim charts, or one per
   * session for the multi-session Interactions/Drafts tapes (each drawn in its
   * own span). Only the sim/Lab charts send it. See lib/chartTypes.IbOverlay.
   */
  ib?: IbOverlay | IbOverlay[] | null;
  tradeRects?: TradeRect[];
  /**
   * Open zoomed in on the (first) trade rectangle rather than fitting the whole
   * session — the by-trade view wants the trade filling the chart, with a little
   * context on either side. Ignored when there's no rect. The user can still zoom
   * back out; this only sets the initial range.
   */
  focusOnTrade?: boolean;
  /**
   * Overlay a live readout of the current zoom (padding bars / ratio relative to
   * the trade span) — a scratchpad for tuning the `focusOnTrade` framing. No
   * effect without `focusOnTrade`.
   */
  debugZoom?: boolean;
  /**
   * Frame this time span (bar times) on open instead of fitting the whole loaded
   * tape — used by the continuous session chart, whose `bars` span many sessions
   * but which should open on the one the user selected. Changing it re-frames the
   * chart in place (no rebuild), so dragging into adjacent days keeps its zoom.
   * Ignored while `focusOnTrade` is set (that owns the initial viewport).
   */
  initialTimeRange?: { from: number; to: number } | null;
  /**
   * Real volume-at-price per bar. When supplied (the sim's charts), the volume
   * profile is computed from the actual tape; without it (the journal's Databento
   * bars) it falls back to spreading each bar's volume across its range.
   */
  footprint?: Footprint;
  /**
   * Per-minute session regime (which side of the two anchored VWAPs price is on),
   * drawn as a colour strip in its own pane under the candles. Supplied by the
   * sim's day chart; see lib/regimeTypes.
   */
  regimeStates?: { time: number; state: string }[];
  tickSize?: number;
  /** Dollars per full point per contract — powers the ruler's $/lot readout. */
  pointValue?: number;
  height?: number;
  /** Called when the user clicks inside a trade rectangle. */
  onTradeClick?: (rect: TradeRect) => void;
}

type Visibility = IndicatorVisibility;

/** One fixed-range profile the user has drawn, bounded by bar times. */
interface RangeSel {
  id: number;
  from: number;
  to: number;
}

/** What a mousedown grabbed: a fresh drag, an edge to resize, or the body to move. */
type DragMode = "new" | "left" | "right" | "move";
/** How close (px) the pointer must be to an edge to grab it rather than the body. */
const HANDLE_PX = 6;

const VOL_UP = "rgba(33,192,122,0.5)";
const VOL_DOWN = "rgba(245,69,95,0.5)";

// --- `focusOnTrade` framing (the by-trade view's default zoom) ---
// A fixed window centred on the trade: FOCUS_BARS wide and FOCUS_TICKS tall. If
// the trade itself is larger than the window, the window expands to fit it plus a
// small margin so it's never clipped. Tune the two window sizes to taste.
const FOCUS_BARS = 508;
const FOCUS_TICKS = 549;
const FOCUS_MARGIN_BARS = 12;
const FOCUS_MARGIN_TICKS = 8;

const fmtDur = (s: number): string => {
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.round(s)}s`;
};

// Hover tooltip for a trade rectangle. Only rects that carry `stats` (the sim's
// charts) get one; the journal day chart sends bare rects and is unaffected.
function tradeTooltipHtml(r: TradeRect): string {
  const s = r.stats!;
  const pnlColor = r.profitable ? palette.green : palette.red;
  const usd = Math.abs(r.net_pnl).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const tight = s.band_width_ticks < s.stop_ticks;
  const muted = `color:${palette.muted}`;
  return [
    `<div><b>#${s.trade_no}</b> · ${s.exit_reason} · ` +
      `<b style="color:${pnlColor}">${r.net_pnl >= 0 ? "+" : "−"}$${usd}</b></div>`,
    `<div style="${muted}">${s.entry_hms} → ${s.exit_hms} · ${fmtDur(s.duration_s)}</div>`,
    `<div style="${muted}">in ${s.avg_entry.toFixed(2)} · out ${s.avg_exit.toFixed(2)} · ` +
      `stop ${s.stop_ticks.toFixed(0)}t</div>`,
    `<div>R <b style="color:${s.r_multiple >= 0 ? palette.green : palette.red}">` +
      `${s.r_multiple.toFixed(2)}</b> · band ${s.band_width_ticks.toFixed(0)}t` +
      (tight ? ` <span style="color:${palette.red}">— narrower than the stop</span>` : "") +
      `</div>`,
  ].join("");
}

// How far off the loaded tape a ⚓ anchor may sit and still be kept when the
// bars change under it: one bar at the coarsest resolution any chart draws, so
// re-gridding 1m → 15m never drops an anchor sitting on the first/last bar.
const GRID_SLACK_S = 15 * 60;

// Client-side candlestick (+ anchored VWAPs + volume) used by both the
// single-trade reconstruction and the full-day session views. Weekend/overnight
// gaps collapse natively (missing bars aren't drawn).
export function CandlestickChart({
  bars,
  vwapGlobex,
  vwapNy,
  vwapWeekly,
  profileGlobex,
  profileNy,
  ema9,
  ema20,
  ema50,
  ema200,
  rsi,
  atrPoints,
  cvd,
  cvdDivergences,
  markers,
  touches,
  vaSnaps,
  priceLines,
  levels,
  ib,
  tradeRects,
  focusOnTrade,
  debugZoom,
  initialTimeRange,
  footprint,
  regimeStates,
  tickSize,
  pointValue,
  height = 520,
  onTradeClick,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const debugRef = useRef<HTMLDivElement>(null);
  // Normalise the IB prop to a list up front: the sim charts pass one overlay,
  // the multi-session Interactions/Drafts tapes pass one per session. Memoised so
  // the build effect's dependency stays reference-stable across renders.
  const ibList = useMemo<IbOverlay[]>(
    () => (ib ? (Array.isArray(ib) ? ib : [ib]) : []),
    [ib],
  );
  // The chart instance and the desired initial frame, both in refs: the frame is
  // read (not deps'd) by the build effect so a new selected day re-frames through
  // the effect below rather than rebuilding the chart, and the instance lets that
  // effect reach the timeScale without capturing it in a closure.
  const chartApiRef = useRef<IChartApi | null>(null);
  const initialRangeRef = useRef(initialTimeRange);
  initialRangeRef.current = initialTimeRange;
  // Ref, not an effect dep: a new callback identity must not rebuild the chart
  // (that would lose the user's zoom/scroll), same reason as applyRef above.
  const onTradeClickRef = useRef(onTradeClick);
  onTradeClickRef.current = onTradeClick;

  // TV-style hide/show per indicator, seeded from the user's last choice and
  // persisted on every toggle. Toggling applies to the live chart via applyRef —
  // it must NOT re-run the build effect (that would rebuild the chart and lose
  // the user's zoom/scroll position).
  const [vis, setVis] = useState<Visibility>(loadIndicatorVisibility);
  const visRef = useRef(vis);
  const applyRef = useRef<((v: Visibility) => void) | null>(null);
  const toggle = (key: IndicatorKey) =>
    setVis((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      visRef.current = next;
      applyRef.current?.(next);
      saveIndicatorVisibility(next);
      return next;
    });
  // Force an indicator on. Hide/show is a sticky global preference, so a layer
  // hidden on some other chart stays hidden here — fine for the fixed overlays,
  // wrong for one the user just asked for by hand. Applied through the ref
  // first so a draw in the same tick already sees it.
  const reveal = (key: IndicatorKey) => {
    if (visRef.current[key]) return;
    const next = { ...visRef.current, [key]: true };
    visRef.current = next;
    applyRef.current?.(next);
    saveIndicatorVisibility(next);
    setVis(next);
  };
  const revealRef = useRef(reveal);
  revealRef.current = reveal;

  // Fixed-range profile tool. `armed` = waiting for the drag that defines a new
  // profile; `ranges` are the ones already on the chart, any of which can be
  // re-dragged. Everything is mirrored into refs so the mouse handlers inside the
  // build effect can read it without becoming effect deps — arming the tool or
  // moving a profile must not rebuild the chart (that would lose zoom/scroll).
  //
  // React state exists only to render the toolbar; a drag in progress repaints
  // through the primitive alone, so panning a profile doesn't re-render per frame.
  // Ranges are stored as bar *times*, not indices, so they survive a timeframe
  // switch (which swaps the whole bar array out).
  const [armed, setArmed] = useState(false);
  const [ranges, setRanges] = useState<RangeSel[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const armedRef = useRef(false);
  const rangesRef = useRef<RangeSel[]>([]);
  const selectedRef = useRef<number | null>(null);
  const nextIdRef = useRef(1);
  const armApplyRef = useRef<((a: boolean) => void) | null>(null);
  const paintRef = useRef<(() => void) | null>(null);

  const arm = (v: boolean) => {
    // one drag/click tool owns the mouse at a time
    if (v) {
      armRulerRef.current(false);
      armAvwapRef.current(false);
    }
    armedRef.current = v;
    setArmed(v);
    armApplyRef.current?.(v);
  };

  // Ruler / measure tool (TV's ruler): arm it, drag (or click-move-click) across
  // the chart, and read off the move — points/ticks/%, $ per lot, bars and time.
  // One measurement at a time; it lives in the primitive only (no React state
  // beyond the button), and a plain click or Esc dismisses it. Same ref dance as
  // the range tool: arming must not rebuild the chart.
  const [rulerArmed, setRulerArmed] = useState(false);
  const rulerArmedRef = useRef(false);
  const rulerApplyRef = useRef<((a: boolean) => void) | null>(null);
  const rulerClearRef = useRef<() => void>(() => {});
  const armRuler = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armAvwapRef.current(false);
    }
    rulerArmedRef.current = v;
    setRulerArmed(v);
    rulerApplyRef.current?.(v);
  };
  const armRulerRef = useRef(armRuler);
  armRulerRef.current = armRuler;

  // Anchored-VWAP tool (TV's ⚓): arm it, click any bar, and a VWAP + ±1σ/±2σ
  // bands draw from that bar forward — computed client-side from the bar tape, so
  // its σ is bar-derived (typical price × volume), not the engine's tick-derived
  // σ. One anchor at a time; re-clicking moves it, the Clear button removes it.
  // `avwapAnchor` is a bar *time* (survives a timeframe switch, same as the
  // ranges); the draw itself lives in the build effect and is triggered through
  // avwapDrawRef so re-anchoring never rebuilds the chart (which would lose the
  // user's zoom/scroll). Same three-way exclusion as the range and ruler tools.
  const [avwapArmed, setAvwapArmed] = useState(false);
  const [avwapAnchor, setAvwapAnchor] = useState<number | null>(null);
  const avwapArmedRef = useRef(false);
  const avwapAnchorRef = useRef<number | null>(null);
  const avwapApplyRef = useRef<((a: boolean) => void) | null>(null);
  const avwapDrawRef = useRef<(() => void) | null>(null);
  const avwapSyncRef = useRef<() => void>(() => {});
  avwapSyncRef.current = () => setAvwapAnchor(avwapAnchorRef.current);
  const armAvwap = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
    }
    avwapArmedRef.current = v;
    setAvwapArmed(v);
    avwapApplyRef.current?.(v);
  };
  const armAvwapRef = useRef(armAvwap);
  armAvwapRef.current = armAvwap;
  const clearAvwap = () => {
    avwapAnchorRef.current = null;
    avwapDrawRef.current?.(); // anchor null -> draw removes the series
    setAvwapAnchor(null);
  };
  // Push whatever the refs now hold into both the chart and the toolbar. Called
  // once a drag settles, never mid-drag.
  const syncRanges = () => {
    setRanges([...rangesRef.current]);
    setSelected(selectedRef.current);
    paintRef.current?.();
  };
  const clearRanges = () => {
    rangesRef.current = [];
    selectedRef.current = null;
    syncRanges();
  };
  const deleteSelected = () => {
    if (selectedRef.current == null) return;
    rangesRef.current = rangesRef.current.filter((r) => r.id !== selectedRef.current);
    selectedRef.current = null;
    syncRanges();
  };
  // Held in refs so the effect's mouse handlers can call them without capturing
  // stale closures.
  const disarmRef = useRef<() => void>(() => {});
  const syncRef = useRef<() => void>(() => {});
  disarmRef.current = () => arm(false);
  syncRef.current = syncRanges;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (armedRef.current) arm(false);
        if (rulerArmedRef.current) armRulerRef.current(false);
        if (avwapArmedRef.current) armAvwapRef.current(false);
        rulerClearRef.current(); // Esc also dismisses a finished measurement
      }
      // Don't hijack Delete while the user is typing somewhere on the page.
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if ((e.key === "Delete" || e.key === "Backspace") && !typing && selectedRef.current != null) {
        e.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    // Handle for the debug-readout animation loop (focusOnTrade + debugZoom only),
    // cancelled on teardown.
    let debugRaf = 0;
    const chart: IChartApi = createChart(ref.current, {
      width: ref.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: palette.bg },
        textColor: palette.text,
        fontFamily: "Inter, sans-serif",
        // Axis labels are smaller than the default 12px so the right price scale
        // (a wide 5-digit NQ price + ".25") takes a narrower gutter — the scale
        // auto-sizes to the widest label, and the library has no max-width knob.
        fontSize: 9,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartApiRef.current = chart;

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: palette.green,
      downColor: palette.red,
      wickUpColor: palette.green,
      wickDownColor: palette.red,
      borderVisible: false,
    });
    candle.setData(
      bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    // Overlay times (fills, MAE/MFE, trade rect) carry second precision, but
    // lightweight-charts only renders markers on an exact bar time and
    // timeToCoordinate() returns null for any off-grid time — so snap every
    // overlay time onto the actual (resampled) bar grid.
    const barTimes = bars.map((b) => b.time);
    const last = barTimes.length - 1;
    const nearestIdx = (t: number): number => {
      if (t <= barTimes[0]) return 0;
      if (t >= barTimes[last]) return last;
      let lo = 0;
      let hi = last;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] === t) return mid;
        if (barTimes[mid] < t) lo = mid + 1;
        else hi = mid - 1;
      }
      return t - barTimes[hi] <= barTimes[lo] - t ? hi : lo;
    };
    const nearestBar = (t: number): number => barTimes[nearestIdx(t)];
    const floorBar = (t: number): number => {
      if (t <= barTimes[0]) return barTimes[0];
      let lo = 0;
      let hi = last;
      let res = barTimes[0];
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] <= t) {
          res = barTimes[mid];
          lo = mid + 1;
        } else hi = mid - 1;
      }
      return res;
    };
    const ceilBar = (t: number): number => {
      if (t >= barTimes[last]) return barTimes[last];
      let lo = 0;
      let hi = last;
      let res = barTimes[last];
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] >= t) {
          res = barTimes[mid];
          hi = mid - 1;
        } else lo = mid + 1;
      }
      return res;
    };

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? VOL_UP : VOL_DOWN,
      })),
    );

    // The regime ribbon: one bar per candle, full height, coloured by which side
    // of the two anchored VWAPs price was on. It reads as a strip rather than a
    // series — the value is always 1 and only the colour carries meaning.
    //
    // The states arrive on a 1-minute grid while the candles are tick bars, so
    // each candle takes the state in force at its close rather than the ribbon's
    // own times being fed to the chart: lightweight-charts unions the time points
    // of every series, and minute stamps that no candle sits on would open an
    // empty column each. Snapping to the candle grid also means the strip stays
    // glued to the candles under zoom and pan, for free.
    const ribbonPane = regimeStates && regimeStates.length > 0 ? 1 : 0;
    if (ribbonPane) {
      const ribbon = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: false, base: 0 },
        ribbonPane,
      );
      let j = 0;
      ribbon.setData(
        bars.map((b) => {
          while (j + 1 < regimeStates!.length && regimeStates![j + 1].time <= b.time) j++;
          const s = regimeStates![j];
          // A candle before the first state (a night the regime engine had no
          // ticks for) gets no bar rather than the first state back-projected.
          if (!s || s.time > b.time) return { time: b.time as Time };
          return {
            time: b.time as Time,
            value: 1,
            color:
              regimePalette.state[s.state as keyof typeof regimePalette.state] ??
              regimePalette.klass.unknown,
          };
        }),
      );
      ribbon.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
    }

    // ATR gets created/removed (not just hidden) on toggle: hiding the series
    // would leave its empty sub-pane behind, while removing the last series of
    // a pane drops the pane too.
    let atrSeries: ISeriesApi<"Line"> | null = null;
    const atrPane = ribbonPane + 1;
    const addAtr = () => {
      atrSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.gold,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        },
        atrPane,
      );
      atrSeries.setData(atrPoints!.map((p) => ({ time: p.time as Time, value: p.atr })));
      // Force a price-dominant split — default is an even share per pane, so set
      // every factor explicitly. Ratio 5:1 ≈ 83% price / 17% ATR.
      const panes = chart.panes();
      if (panes.length > atrPane) {
        panes[0].setStretchFactor(1000);
        panes[atrPane].setStretchFactor(200);
      }
    };

    // CVD gets the same create/remove-on-toggle treatment as ATR and its own pane,
    // stacked under it. The offset keeps it right on a chart with no ATR (the
    // journal's own charts carry no CVD, the Lab's carry both).
    let cvdSeries: ISeriesApi<"Line"> | null = null;
    const cvdPane = atrPane + (atrPoints && atrPoints.length > 0 ? 1 : 0);
    const addCvd = () => {
      cvdSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.blue,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "volume" },
        },
        cvdPane,
      );
      cvdSeries.setData(cvd!.map((p) => ({ time: p.time as Time, value: p.value })));
      // A zero reference: CVD crosses sign, and which side of zero it sits on is
      // the whole read (net buying vs net selling since the anchor).
      cvdSeries.createPriceLine({
        price: 0,
        color: palette.grid,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      // Divergence A→B lines live on this pane, attached to the CVD series so
      // they resolve in delta units and vanish with CVD when it's toggled off.
      if (cvdDivergences && cvdDivergences.length > 0) {
        cvdSeries.attachPrimitive(new CvdDivergencePrimitive(cvdDivergences) as any);
      }
      const panes = chart.panes();
      if (panes.length > cvdPane) {
        panes[0].setStretchFactor(1000);
        panes[cvdPane].setStretchFactor(200);
      }
    };

    // RSI gets the same create/remove-on-toggle treatment as ATR/CVD and its own
    // pane, stacked after them. It's computed on the drawn bars (so it tracks the
    // chart's timeframe), arriving already stamped on the bar grid; the forward-walk
    // is a straight copy, but kept identical to the EMA's so it's robust to any
    // stamp that lands off-grid. The pane is pinned to 0-100 with 30/50/70 guides —
    // the oscillator's whole read is where it sits between those bands.
    let rsiSeries: ISeriesApi<"Line"> | null = null;
    const rsiPane = cvdPane + (cvd && cvd.length > 0 ? 1 : 0);
    const addRsi = () => {
      rsiSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.violet,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 1, minMove: 0.1 },
          // Pin the pane to the oscillator's fixed 0-100 range so the guide lines
          // mean the same thing every session, whatever the day's RSI swing.
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        },
        rsiPane,
      );
      const data: { time: Time; value: number }[] = [];
      let j = 0;
      for (const b of bars) {
        while (j + 1 < rsi!.length && rsi![j + 1].time <= b.time) j++;
        const p = rsi![j];
        if (!p || p.time > b.time) continue; // a bar before the first RSI point
        data.push({ time: b.time as Time, value: p.value });
      }
      rsiSeries.setData(data);
      // Overbought / midline / oversold guides — the levels the RSI read is against.
      for (const lvl of [70, 50, 30]) {
        rsiSeries.createPriceLine({
          price: lvl,
          color: lvl === 50 ? palette.grid : palette.muted,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
        });
      }
      const panes = chart.panes();
      if (panes.length > rsiPane) {
        panes[0].setStretchFactor(1000);
        panes[rsiPane].setStretchFactor(200);
      }
    };

    // The ribbon is a strip, not a chart: it gets just enough height to read as a
    // colour band. Set after addAtr so both panes are sized from one place.
    const panes0 = chart.panes();
    if (ribbonPane && panes0.length > ribbonPane) {
      panes0[0].setStretchFactor(1000);
      panes0[ribbonPane].setStretchFactor(70);
    }

    // A non-finite value is a session-boundary break sentinel (see
    // Interactions.tsx). lightweight-charts has no native line gaps: a
    // whitespace item only reserves a time-scale slot — the series drops it
    // from its own rows and the line renderer connects the surviving
    // neighbours straight across. Each drawn segment takes the colour of the
    // point it *leaves*, so the break is enforced by painting the last real
    // point before the sentinel fully transparent: the stroke arriving there
    // keeps the series colour, the bridge to the next session doesn't render.
    const GAP_COLOR = "rgba(0,0,0,0)";
    const gappedLineData = <K extends string>(
      pts: ({ time: number } & Record<K, number>)[],
      key: K,
    ): { time: Time; value?: number; color?: string }[] => {
      const out: { time: Time; value?: number; color?: string }[] = [];
      for (const v of pts) {
        if (Number.isFinite(v[key])) {
          out.push({ time: v.time as Time, value: v[key] });
        } else {
          const prev = out.length > 0 ? out[out.length - 1] : null;
          if (prev && prev.value !== undefined) prev.color = GAP_COLOR;
          out.push({ time: v.time as Time });
        }
      }
      return out;
    };

    // One anchored VWAP = 5 lines (mid, ±1σ, ±2σ) plus a shaded fill between the
    // σ bands. The σ lines are dashed and fade outward so they read as an
    // envelope rather than competing with the mid line. Each anchor — lines and
    // fill together — is toggled as a unit.
    const addVwap = (
      points: VwapPoint[],
      colors: { middle: string; band1: string; band2: string; fill: string },
    ): { series: ISeriesApi<"Line">[]; band: VwapBandPrimitive } => {
      const series: ISeriesApi<"Line">[] = [];
      const mid = chart.addSeries(LineSeries, {
        color: colors.middle,
        lineWidth: 2,
        priceLineVisible: false,
      });
      mid.setData(gappedLineData(points, "middle"));
      series.push(mid);
      const bands = [
        { keys: ["upper1", "lower1"], color: colors.band1 },
        { keys: ["upper2", "lower2"], color: colors.band2 },
      ] as const;
      for (const band of bands) {
        for (const key of band.keys) {
          const line = chart.addSeries(LineSeries, {
            color: band.color,
            lineWidth: 1,
            lineStyle: 2,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          line.setData(gappedLineData(points, key));
          series.push(line);
        }
      }
      const band = new VwapBandPrimitive(points, colors.fill);
      candle.attachPrimitive(band as any);
      return { series, band };
    };

    const globex =
      vwapGlobex && vwapGlobex.length > 0 ? addVwap(vwapGlobex, vwapPalette.globex) : null;
    const ny = vwapNy && vwapNy.length > 0 ? addVwap(vwapNy, vwapPalette.ny) : null;
    const weekly =
      vwapWeekly && vwapWeekly.length > 0 ? addVwap(vwapWeekly, vwapPalette.weekly) : null;

    // User-anchored VWAP (the ⚓ tool). Computed here from the bars in the browser
    // — running Σv, Σpv, Σp²v over each bar's typical price (H+L+C)/3 from the
    // anchor bar forward, the same bar-derived formula the journal charts use
    // (api/charts_data._vwap_rows), NOT the engine's tick-derived σ. Redrawn
    // imperatively whenever the anchor moves; the anchor is a bar *time*, so it
    // re-snaps onto the current grid after a timeframe switch, exactly like the
    // fixed-range profiles.
    let avwap: { series: ISeriesApi<"Line">[]; band: VwapBandPrimitive } | null = null;
    const computeAvwap = (i0: number): VwapPoint[] => {
      const out: VwapPoint[] = [];
      let sumV = 0;
      let sumPV = 0;
      let sumP2V = 0;
      for (let i = i0; i <= last; i++) {
        const b = bars[i];
        const typ = (b.high + b.low + b.close) / 3;
        sumV += b.volume;
        sumPV += typ * b.volume;
        sumP2V += typ * typ * b.volume;
        if (sumV <= 0) continue; // no volume yet -> no defined VWAP
        const mid = sumPV / sumV;
        const sd = Math.sqrt(Math.max(0, sumP2V / sumV - mid * mid));
        out.push({
          time: b.time,
          middle: mid,
          upper1: mid + sd,
          lower1: mid - sd,
          upper2: mid + 2 * sd,
          lower2: mid - 2 * sd,
        });
      }
      return out;
    };
    const drawAvwap = () => {
      if (avwap) {
        for (const s of avwap.series) chart.removeSeries(s);
        candle.detachPrimitive(avwap.band as any);
        avwap = null;
      }
      const t = avwapAnchorRef.current;
      if (t == null) return;
      // The tape can change under a live anchor: a timeframe switch re-grids it
      // (re-snapping is the point), but a new session window can also slide the
      // anchored bar out of the loaded range entirely — and nearestIdx would
      // then clamp to the first bar and draw a VWAP nobody asked for. Drop the
      // anchor instead. The slack covers a re-grid moving an endpoint by up to
      // one bar at the coarsest resolution the charts draw.
      if (t < barTimes[0] - GRID_SLACK_S || t > barTimes[last] + GRID_SLACK_S) {
        avwapAnchorRef.current = null;
        avwapSyncRef.current();
        return;
      }
      const pts = computeAvwap(nearestIdx(t));
      if (pts.length === 0) return;
      avwap = addVwap(pts, vwapPalette.anchored);
      const on = visRef.current.vwapAnchored;
      for (const s of avwap.series) s.applyOptions({ visible: on });
      avwap.band.setVisible(on);
    };
    avwapDrawRef.current = drawAvwap;
    drawAvwap(); // restore the anchor across a rebuild (data / timeframe change)

    // Developing value areas, one per anchor: VAH and VAL solid (they are the
    // levels the rules actually test against), POC dashed between them, each in its
    // anchor's colour. Deliberately not shaded bands — the VWAP envelope already
    // owns that visual, and stacking fills where the two areas overlap (the whole
    // setup) would be unreadable.
    const addProfile = (
      pts: ProfilePoint[] | undefined,
      pal: { edge: string; poc: string },
    ): ISeriesApi<"Line">[] => {
      if (!pts || pts.length === 0) return [];
      const lines = [
        { key: "vah", color: pal.edge, style: 0, width: 2 },
        { key: "val", color: pal.edge, style: 0, width: 2 },
        { key: "poc", color: pal.poc, style: 2, width: 2 },
      ] as const;
      return lines.map((l) => {
        const s_ = chart.addSeries(LineSeries, {
          color: l.color,
          lineWidth: l.width as 1 | 2,
          lineStyle: l.style,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        s_.setData(gappedLineData(pts, l.key));
        return s_;
      });
    };
    const profileGlobexSeries = addProfile(profileGlobex, profilePalette.globex);
    const profileNySeries = addProfile(profileNy, profilePalette.ny);

    // 9/20 EMA (1-minute). The values arrive stamped on the minute they were
    // computed on, but the candles may be tick bars — and an off-grid time has no
    // coordinate — so each drawn bar takes the latest EMA at or before its close,
    // exactly the forward-walk the regime ribbon uses. On the 1-minute chart the
    // stamps already match the bars, so this is a straight copy; on tick bars it
    // samples the minute line onto the tick grid (flat within a minute, stepping
    // at each new one), keeping the line a true 1-minute EMA either way.
    const addEma = (pts: EmaPoint[] | undefined, color: string): ISeriesApi<"Line"> | null => {
      if (!pts || pts.length === 0) return null;
      const s_ = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const data: { time: Time; value: number }[] = [];
      let j = 0;
      for (const b of bars) {
        while (j + 1 < pts.length && pts[j + 1].time <= b.time) j++;
        const p = pts[j];
        if (!p || p.time > b.time) continue; // a bar before the first EMA point
        data.push({ time: b.time as Time, value: p.value });
      }
      s_.setData(data);
      return s_;
    };
    // Each EMA carries its own toggle key so the four lines hide/show
    // independently (see the visibility pass below and the legend rows).
    const emaSpecs: { key: IndicatorKey; pts: EmaPoint[] | undefined; color: string }[] = [
      { key: "ema9", pts: ema9, color: emaPalette.fast },
      { key: "ema20", pts: ema20, color: emaPalette.slow },
      { key: "ema50", pts: ema50, color: emaPalette.trend50 },
      { key: "ema200", pts: ema200, color: emaPalette.trend200 },
    ];
    const emaSeries: { key: IndicatorKey; series: ISeriesApi<"Line"> }[] = [];
    for (const spec of emaSpecs) {
      const s = addEma(spec.pts, spec.color);
      if (s) emaSeries.push({ key: spec.key, series: s });
    }

    if (markers && markers.length > 0) {
      const barMap = new Map(bars.map((b) => [b.time, b]));
      const snappedMarkers = markers.map((m) => ({ ...m, time: nearestBar(m.time) }));
      candle.attachPrimitive(new MarkerPrimitive(snappedMarkers, barMap) as any);
    }

    // Interaction overlay (touch dots + VA-snap markers). Attached unconditionally
    // so its toggles exist even before the arrays fill; empty arrays draw nothing.
    // Events are stamped on the minute grid, but the candles may be tick bars —
    // and timeToCoordinate returns null for any off-grid time — so snap each mark
    // onto the actual bar grid, exactly as the native markers above are snapped.
    const snappedTouches = (touches ?? []).map((t) => ({ ...t, ts: nearestBar(t.ts) }));
    const snappedSnaps = (vaSnaps ?? []).map((s) => ({ ...s, ts: nearestBar(s.ts) }));
    const interactionPrim = new InteractionPrimitive(snappedTouches, snappedSnaps);
    candle.attachPrimitive(interactionPrim as any);

    for (const pl of priceLines ?? []) {
      candle.createPriceLine({
        price: pl.price,
        color: pl.color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: pl.title,
      });
    }

    const levelLines: IPriceLine[] = (levels ?? []).map((lv) =>
      candle.createPriceLine({
        price: lv.price,
        color: lv.color,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: lv.title,
      }),
    );

    // Initial Balance: high/low as flat segments spanning the bell → the close —
    // line series rather than price lines, because an IB doesn't exist over the
    // overnight candles and a full-pane line would draw it there. The extension
    // guides (±1×/1.5×/2× of the IB range beyond each edge, the study's ext_x
    // units) start where the IB completes, and are excluded from autoscale: on a
    // narrow-IB day they sit far outside the traded range, and toggling them on
    // must not crush the candles.
    const ibSeries: ISeriesApi<"Line">[] = [];
    const ibExtSeries: ISeriesApi<"Line">[] = [];
    // One overlay (single-session sim charts) or many (a session per day on the
    // Interactions/Drafts tapes). Each is drawn bell → its own session's close,
    // so the segments never bleed across the overnight into the next session.
    for (const one of ibList) {
      const ibSeg = (
        price: number,
        from: number,
        into: ISeriesApi<"Line">[],
        opts: { color: string; style: 0 | 2; guide?: boolean },
      ) => {
        if (one.end <= from) return; // degenerate session: nothing to span
        const s_ = chart.addSeries(LineSeries, {
          color: opts.color,
          lineWidth: 1,
          lineStyle: opts.style,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          ...(opts.guide ? { autoscaleInfoProvider: () => null } : {}),
        });
        s_.setData([
          { time: from as Time, value: price },
          { time: one.end as Time, value: price },
        ]);
        into.push(s_);
      };
      ibSeg(one.high, one.start, ibSeries, { color: ibPalette.line, style: 0 });
      ibSeg(one.low, one.start, ibSeries, { color: ibPalette.line, style: 0 });
      const ibRange = one.high - one.low;
      for (const m of [1, 1.5, 2]) {
        for (const p of [one.high + m * ibRange, one.low - m * ibRange]) {
          ibSeg(p, one.formed, ibExtSeries, { color: ibPalette.ext, style: 2, guide: true });
        }
      }
    }

    // Every profile on this chart — the viewport-following one and each
    // fixed-range one — is a slice of bars, so they all resolve through here.
    // With a footprint we sum the real tape over those bars; without one we fall
    // back to spreading each bar's volume across its range.
    const exact = footprint != null && footprint.length === bars.length && tickSize != null;
    const profileFor = (i0: number, i1: number): VolumeProfile | null => {
      if (i1 < i0) return null;
      if (exact) {
        const entries: number[][] = [];
        for (let i = i0; i <= i1; i++) entries.push(...footprint![i]);
        return computeTickProfile(entries, tickSize!);
      }
      return computeVolumeProfile(bars.slice(i0, i1 + 1));
    };

    // Volume profile over whatever bars are on screen: the histogram itself is a
    // primitive (nothing native runs along the price axis), while POC/VAH/VAL are
    // price lines so they get axis labels and span the full pane for free.
    const vp = new VolumeProfilePrimitive(profileFor(0, last));
    candle.attachPrimitive(vp as any);

    const VP_LINES = [
      { key: "poc", color: palette.gold, style: 0, title: "POC" },
      { key: "vah", color: palette.blue, style: 2, title: "VAH" },
      { key: "val", color: palette.blue, style: 2, title: "VAL" },
    ] as const;
    let vpLines: IPriceLine[] = [];

    const syncProfileLines = (p: VolumeProfile | null) => {
      const on = visRef.current.volumeProfile && p != null;
      // A window with no traded range (every bar flat) has no profile to label.
      if (p && vpLines.length === 0) {
        vpLines = VP_LINES.map((spec) =>
          candle.createPriceLine({
            price: p[spec.key],
            color: spec.color,
            lineWidth: 1,
            lineStyle: spec.style,
            axisLabelVisible: true,
            title: spec.title,
          }),
        );
      }
      vpLines.forEach((line, i) =>
        line.applyOptions({
          ...(p ? { price: p[VP_LINES[i].key] } : {}),
          lineVisible: on,
          axisLabelVisible: on,
        }),
      );
    };

    syncProfileLines(vp.profile);

    // Re-profile on pan/zoom so zooming into one session profiles that session
    // rather than the whole loaded window. Logical range is fractional and can
    // run past the data on both ends, so clamp it back onto real bar indices.
    let lastFrom = -1;
    let lastTo = -1;
    const reprofile = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const from = Math.max(0, Math.ceil(range.from));
      const to = Math.min(bars.length - 1, Math.floor(range.to));
      if (to < from || (from === lastFrom && to === lastTo)) return;
      lastFrom = from;
      lastTo = to;
      const p = profileFor(from, to);
      vp.setProfile(p);
      syncProfileLines(p);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(reprofile);

    // --- Fixed-range profile: drag across the chart to profile just that slice ---
    const rangePrim = new RangeProfilePrimitive();
    candle.attachPrimitive(rangePrim as any);

    // --- Ruler: drag between two points to measure the move between them ---
    const ruler = new RulerPrimitive(tickSize, pointValue);
    candle.attachPrimitive(ruler as any);

    // Repaint every profile from the ref. Each range's edges are re-snapped onto
    // the *current* bar grid: ranges are stored as the times the user dragged
    // over, which after a timeframe switch are no longer bar times — and an
    // off-grid time has no coordinate, so the profile would vanish.
    const paint = () => {
      rangePrim.setData(
        rangesRef.current.map((r) => {
          const i0 = nearestIdx(r.from);
          const i1 = nearestIdx(r.to);
          return {
            id: r.id,
            from: barTimes[i0],
            to: barTimes[i1],
            profile: profileFor(i0, i1),
          };
        }),
        selectedRef.current,
      );
    };
    paintRef.current = paint;
    paint(); // restore existing profiles across a rebuild

    const host = ref.current;
    const xOf = (e: MouseEvent) => e.clientX - host.getBoundingClientRect().left;
    const yOf = (e: MouseEvent) => e.clientY - host.getBoundingClientRect().top;
    const idxAtX = (x: number): number | null => {
      const logical = chart.timeScale().coordinateToLogical(x);
      if (logical == null) return null;
      return Math.min(last, Math.max(0, Math.round(logical)));
    };
    // Ruler corners snap to the tick grid (when known) — a measurement in
    // fractional ticks is never what anyone wants.
    const priceAtY = (y: number): number | null => {
      const p = candle.coordinateToPrice(y);
      if (p == null) return null;
      return tickSize ? Math.round(p / tickSize) * tickSize : p;
    };
    const measureOf = (i1: number, p1: number, i2: number, p2: number) => ({
      t1: barTimes[i1],
      p1,
      t2: barTimes[i2],
      p2,
      bars: Math.abs(i2 - i1),
      seconds: Math.abs(barTimes[i2] - barTimes[i1]),
    });

    // What's under the pointer, topmost (most recently drawn) first. Edges win
    // over bodies so a narrow profile is still resizable.
    const hitTest = (x: number): { id: number; mode: DragMode } | null => {
      const ts = chart.timeScale();
      for (let i = rangesRef.current.length - 1; i >= 0; i--) {
        const r = rangesRef.current[i];
        const a = ts.timeToCoordinate(nearestBar(r.from) as Time);
        const b = ts.timeToCoordinate(nearestBar(r.to) as Time);
        if (a == null || b == null) continue;
        const x1 = Math.min(a, b);
        const x2 = Math.max(a, b);
        if (Math.abs(x - x1) <= HANDLE_PX) return { id: r.id, mode: "left" };
        if (Math.abs(x - x2) <= HANDLE_PX) return { id: r.id, mode: "right" };
        if (x > x1 && x < x2) return { id: r.id, mode: "move" };
      }
      return null;
    };

    // Panning is a left-drag too, so it must be off whenever a left-drag means
    // something else: while the tool is armed, or while the pointer is over a
    // profile the user could grab. Only the *pressed-drag* gesture conflicts —
    // blanket `handleScroll: false` would also deaden the mouse wheel, so the
    // wheel (and zooming) keep working while a profile is under the cursor.
    // Toggled only on change: applyOptions on every mousemove would be wasteful.
    let scrollOff = false;
    const setScroll = (off: boolean) => {
      if (off === scrollOff) return;
      scrollOff = off;
      chart.applyOptions({
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: !off,
          horzTouchDrag: !off,
          vertTouchDrag: !off,
        },
      });
    };
    armApplyRef.current = (a: boolean) => {
      setScroll(a || rulerArmedRef.current || avwapArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
    };
    armApplyRef.current(armedRef.current);

    // Drag state, in bar-index space rather than time: the bar grid has gaps
    // (weekends, overnight), so shifting a range by a time delta would smear it.
    // Indices move it by whole bars, which is what the user sees.
    let drag: { mode: DragMode; id: number; anchorIdx: number; from: number; to: number } | null =
      null;
    let downX = 0;
    let downY = 0;
    // The ruler's anchor while a measurement is being drawn. Survives mouseup on
    // a no-move click, so both TV gestures work: press-drag-release and
    // click-move-click.
    let rulerDrag: { i1: number; p1: number } | null = null;
    // Set when a mousedown placed a VWAP anchor, so the trade-click that
    // lightweight-charts fires from the same click doesn't also open a trade
    // (the anchor may land on top of a trade rectangle).
    let avwapConsumedClick = false;

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const x = xOf(e);
      const idx = idxAtX(x);
      if (idx == null) return;
      downX = x;
      downY = yOf(e);

      if (avwapArmedRef.current) {
        // Anchor the VWAP on the clicked bar, draw it, and put the tool away.
        // Un-hide the layer first: its legend row only exists once an anchor is
        // placed, so hiding it once (easy — it's the row that just appeared)
        // would otherwise make every later anchor land invisible, and the tool
        // read as dead.
        revealRef.current("vwapAnchored");
        avwapAnchorRef.current = barTimes[idx];
        avwapConsumedClick = true;
        drawAvwap();
        armAvwapRef.current(false);
        avwapSyncRef.current(); // reflect the anchor in the toolbar + legend
        e.preventDefault();
        return;
      }

      if (rulerArmedRef.current) {
        if (rulerDrag) {
          // Second click of click-move-click: the measurement is done.
          rulerDrag = null;
          armRulerRef.current(false);
          e.preventDefault();
          return;
        }
        const p = priceAtY(downY);
        if (p == null) return;
        rulerDrag = { i1: idx, p1: p };
        ruler.setData(measureOf(idx, p, idx, p));
        e.preventDefault();
        return;
      }
      // Any plain press dismisses a finished measurement, like TV's ruler.
      if (ruler.data()) ruler.setData(null);

      if (armedRef.current) {
        const id = nextIdRef.current++;
        rangesRef.current = [...rangesRef.current, { id, from: barTimes[idx], to: barTimes[idx] }];
        selectedRef.current = id;
        drag = { mode: "new", id, anchorIdx: idx, from: idx, to: idx };
        e.preventDefault();
        paint();
        return;
      }

      const hit = hitTest(x);
      if (!hit) {
        // Clicking bare chart deselects — but let the chart pan as usual.
        if (selectedRef.current != null) {
          selectedRef.current = null;
          syncRef.current();
        }
        return;
      }
      const r = rangesRef.current.find((v) => v.id === hit.id)!;
      selectedRef.current = hit.id;
      drag = {
        mode: hit.mode,
        id: hit.id,
        anchorIdx: idx,
        from: nearestIdx(r.from),
        to: nearestIdx(r.to),
      };
      e.preventDefault();
      if (hit.mode === "move") host.style.cursor = "grabbing";
      syncRef.current();
    };

    const onMove = (e: MouseEvent) => {
      const x = xOf(e);
      if (rulerDrag) {
        const idx = idxAtX(x);
        const p = priceAtY(yOf(e));
        if (idx == null || p == null) return;
        ruler.setData(measureOf(rulerDrag.i1, rulerDrag.p1, idx, p));
        return;
      }
      if (!drag) {
        // Idle hover: advertise what a grab here would do, and take the mouse away
        // from the chart's panning so the grab actually lands.
        if (armedRef.current || rulerArmedRef.current || avwapArmedRef.current) return;
        const hit = hitTest(x);
        setScroll(hit != null);
        host.style.cursor = !hit ? "" : hit.mode === "move" ? "grab" : "col-resize";
        return;
      }

      const idx = idxAtX(x);
      if (idx == null) return;
      const r = rangesRef.current.find((v) => v.id === drag!.id);
      if (!r) return;

      if (drag.mode === "move") {
        // Slide by whole bars, clamped so the range keeps its width at the edges.
        const width = drag.to - drag.from;
        let from = drag.from + (idx - drag.anchorIdx);
        from = Math.min(last - width, Math.max(0, from));
        r.from = barTimes[from];
        r.to = barTimes[from + width];
      } else {
        // Resizing: the grabbed edge follows the pointer, the other stays put, and
        // dragging one past the other just flips which is which.
        const fixed = drag.mode === "new" ? drag.anchorIdx : drag.mode === "left" ? drag.to : drag.from;
        r.from = barTimes[Math.min(idx, fixed)];
        r.to = barTimes[Math.max(idx, fixed)];
      }
      paint(); // primitive only — no React re-render mid-drag
    };

    const onUp = (e: MouseEvent) => {
      if (rulerDrag) {
        // A real drag ends the measurement here; a stationary click leaves the
        // anchor live so the pointer keeps stretching it (click-move-click).
        if (Math.hypot(xOf(e) - downX, yOf(e) - downY) >= 5) {
          rulerDrag = null;
          armRulerRef.current(false);
        }
        return;
      }
      if (!drag) return;
      const wasNew = drag.mode === "new";
      const id = drag.id;
      const moved = Math.abs(xOf(e) - downX);
      drag = null;

      // A click with no real drag means "never mind" — don't leave a hairline
      // profile of a single bar behind.
      if (wasNew && moved < 5) {
        rangesRef.current = rangesRef.current.filter((r) => r.id !== id);
        selectedRef.current = null;
      }
      if (wasNew) disarmRef.current();
      syncRef.current();
    };

    // Below the handlers because both close over `rulerDrag`: disarming or
    // clearing must also drop an in-flight anchor, or the measurement would keep
    // chasing the pointer after Esc / toggling the tool off.
    rulerApplyRef.current = (a: boolean) => {
      setScroll(a || armedRef.current || avwapArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
      if (a) ruler.setData(null); // re-arming starts a fresh measurement
      else rulerDrag = null;
    };
    rulerApplyRef.current(rulerArmedRef.current);
    rulerClearRef.current = () => {
      rulerDrag = null;
      ruler.setData(null);
    };

    // Arming the ⚓ tool just sets the crosshair and takes the pointer off panning
    // so the anchor click lands cleanly; the actual placement happens in onDown.
    avwapApplyRef.current = (a: boolean) => {
      setScroll(a || armedRef.current || rulerArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
    };
    avwapApplyRef.current(avwapArmedRef.current);

    host.addEventListener("mousedown", onDown);
    // Move/up on the window, so a drag that leaves the chart still tracks and,
    // more importantly, still terminates.
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);

    applyRef.current = (v: Visibility) => {
      vp.setVisible(v.volumeProfile);
      for (const l of vpLines)
        l.applyOptions({ lineVisible: v.volumeProfile, axisLabelVisible: v.volumeProfile });
      for (const s of globex?.series ?? []) s.applyOptions({ visible: v.vwapGlobex });
      globex?.band.setVisible(v.vwapGlobex);
      for (const s of ny?.series ?? []) s.applyOptions({ visible: v.vwapNy });
      ny?.band.setVisible(v.vwapNy);
      for (const s of weekly?.series ?? []) s.applyOptions({ visible: v.vwapWeekly });
      weekly?.band.setVisible(v.vwapWeekly);
      for (const s of avwap?.series ?? []) s.applyOptions({ visible: v.vwapAnchored });
      avwap?.band.setVisible(v.vwapAnchored);
      for (const s of profileGlobexSeries) s.applyOptions({ visible: v.developingProfileGlobex });
      for (const s of profileNySeries) s.applyOptions({ visible: v.developingProfileNy });
      for (const { key, series } of emaSeries) series.applyOptions({ visible: v[key] });
      for (const l of levelLines) l.applyOptions({ lineVisible: v.levels, axisLabelVisible: v.levels });
      for (const s of ibSeries) s.applyOptions({ visible: v.initialBalance });
      for (const s of ibExtSeries) s.applyOptions({ visible: v.ibExtensions });
      interactionPrim.setVisibility(v.touches, v.va_snaps);
      if (atrPoints && atrPoints.length > 0) {
        if (v.atr && !atrSeries) addAtr();
        else if (!v.atr && atrSeries) {
          chart.removeSeries(atrSeries);
          atrSeries = null;
        }
      }
      if (cvd && cvd.length > 0) {
        if (v.cvd && !cvdSeries) addCvd();
        else if (!v.cvd && cvdSeries) {
          chart.removeSeries(cvdSeries);
          cvdSeries = null;
        }
      }
      if (rsi && rsi.length > 0) {
        if (v.rsi && !rsiSeries) addRsi();
        else if (!v.rsi && rsiSeries) {
          chart.removeSeries(rsiSeries);
          rsiSeries = null;
        }
      }
    };
    applyRef.current(visRef.current);

    if (tradeRects && tradeRects.length > 0) {
      // Snap entry down / exit up to bar boundaries so the rectangle spans the
      // whole holding period and its corners resolve to real coordinates.
      const snapped = tradeRects.map((r) => {
        let entry = floorBar(r.entry_time);
        let exit = ceilBar(r.exit_time);
        if (exit <= entry) {
          const idx = barTimes.indexOf(entry);
          exit = idx >= 0 && idx < last ? barTimes[idx + 1] : exit;
        }
        return { ...r, entry_time: entry, exit_time: exit };
      });
      candle.attachPrimitive(new TradeRectanglePrimitive(snapped) as any);

      // Hover a rect -> stats tooltip; click -> onTradeClick. Hit-testing runs
      // in pixel space against the snapped rects, padded so a near-flat rect
      // (scratch trade) is still hoverable. The rect is the hover target — the
      // 10px markers are too fiddly to aim at.
      const withStats = snapped.filter((r) => r.stats);
      if (withStats.length > 0 || onTradeClickRef.current) {
        const PAD_X = 3;
        const PAD_Y = 8;
        const hitRect = (x: number, y: number): TradeRect | null => {
          const ts = chart.timeScale();
          for (const r of snapped) {
            const x1 = ts.timeToCoordinate(r.entry_time as Time);
            const x2 = ts.timeToCoordinate(r.exit_time as Time);
            const y1 = candle.priceToCoordinate(r.entry_price);
            const y2 = candle.priceToCoordinate(r.exit_price);
            if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
            if (
              x >= Math.min(x1, x2) - PAD_X &&
              x <= Math.max(x1, x2) + PAD_X &&
              y >= Math.min(y1, y2) - PAD_Y &&
              y <= Math.max(y1, y2) + PAD_Y
            )
              return r;
          }
          return null;
        };

        chart.subscribeCrosshairMove((param) => {
          const tip = tipRef.current;
          const host = ref.current;
          if (!tip || !host) return;
          // The range tool owns the pointer while armed, and a profile owns it
          // wherever one is drawn: no rect tooltip, and don't stomp the cursor
          // those set. Deleting the profile gives the trade back.
          if (
            armedRef.current ||
            rulerArmedRef.current ||
            avwapArmedRef.current ||
            (param.point && hitTest(param.point.x))
          ) {
            tip.style.display = "none";
            return;
          }
          const r = param.point ? hitRect(param.point.x, param.point.y) : null;
          if (!r || !r.stats) {
            tip.style.display = "none";
            host.style.cursor = "";
            return;
          }
          tip.innerHTML = tradeTooltipHtml(r);
          tip.style.display = "block";
          const tw = tip.offsetWidth;
          const th = tip.offsetHeight;
          let left = param.point!.x + 14;
          if (left + tw > host.clientWidth - 8) left = param.point!.x - tw - 14;
          let top = param.point!.y - th - 12;
          if (top < 4) top = param.point!.y + 16;
          tip.style.left = `${Math.max(4, left)}px`;
          tip.style.top = `${top}px`;
          host.style.cursor = onTradeClickRef.current ? "pointer" : "";
        });

        chart.subscribeClick((param) => {
          const cb = onTradeClickRef.current;
          // Swallow the click that just placed a VWAP anchor.
          if (avwapConsumedClick) {
            avwapConsumedClick = false;
            return;
          }
          if (armedRef.current || rulerArmedRef.current || avwapArmedRef.current || !cb || !param.point)
            return;
          if (hitTest(param.point.x)) return; // a profile is covering this trade
          const r = hitRect(param.point.x, param.point.y);
          if (r) cb(r);
        });
      }
    }

    // The by-trade view opens zoomed onto the trade: frame the entry→exit span
    // with roughly its own width of context on each side (a floor so a scratch
    // trade of a couple of bars still gets breathing room). Logical range takes
    // fractional / past-the-end values, so the padding needs no clamping — it
    // just shows empty gutter at the ends of the session. Everything else fits
    // the whole loaded window as before.
    if (focusOnTrade && tradeRects && tradeRects.length > 0) {
      const r = tradeRects[0];
      const i0 = nearestIdx(r.entry_time);
      const i1 = nearestIdx(r.exit_time);
      const lo = Math.min(i0, i1);
      const hi = Math.max(i0, i1);
      const tk = tickSize ?? 0.25;

      // Horizontal (time): a FOCUS_BARS-wide window centred on the trade, widened
      // to the trade + margin if the trade is wider than the window.
      const cx = (lo + hi) / 2;
      const halfB = Math.max(FOCUS_BARS / 2, (hi - lo) / 2 + FOCUS_MARGIN_BARS);
      chart.timeScale().setVisibleLogicalRange({ from: cx - halfB, to: cx + halfB });

      // Vertical (price): a FOCUS_TICKS-tall window centred on the trade's price
      // action — the high/low of its own bars plus its entry / exit / stop levels,
      // same expand-to-fit rule. Uses the price scale's own range setter (v5),
      // which pins the vertical zoom; the user can still drag the price axis, and
      // double-click resets it to autoscale.
      let pMin = Infinity;
      let pMax = -Infinity;
      for (let i = lo; i <= hi; i++) {
        if (bars[i].low < pMin) pMin = bars[i].low;
        if (bars[i].high > pMax) pMax = bars[i].high;
      }
      for (const p of [r.entry_price, r.exit_price, r.stats?.stop_price]) {
        if (p == null) continue;
        if (p < pMin) pMin = p;
        if (p > pMax) pMax = p;
      }
      if (pMin <= pMax) {
        const cy = (pMin + pMax) / 2;
        const halfP = Math.max((FOCUS_TICKS * tk) / 2, (pMax - pMin) / 2 + FOCUS_MARGIN_TICKS * tk);
        chart.priceScale("right").setVisibleRange({ from: cy - halfP, to: cy + halfP });
      }

      // Zoom-tuning readout: the visible width (bars) and height (points/ticks) —
      // the two numbers that map straight to FOCUS_BARS / FOCUS_TICKS. Driven by
      // requestAnimationFrame so dragging the price axis updates it too (there's no
      // price-scale change event). Only runs when `debugZoom` is on.
      if (debugZoom) {
        const priceScale = chart.priceScale("right");
        const showDebug = () => {
          const el = debugRef.current;
          if (el) {
            const vr = chart.timeScale().getVisibleLogicalRange();
            const pr = priceScale.getVisibleRange();
            const xPart = vr ? `x: ${Math.round(vr.to - vr.from)} bars` : "x: —";
            const yPart = pr
              ? `y: ${(pr.to - pr.from).toFixed(1)}pt / ${Math.round((pr.to - pr.from) / tk)}t`
              : "y: —";
            el.textContent = `${xPart}  |  ${yPart}`;
          }
          debugRaf = requestAnimationFrame(showDebug);
        };
        debugRaf = requestAnimationFrame(showDebug);
      }
    } else if (initialRangeRef.current) {
      // A continuous multi-session tape: open on the selected day's span rather
      // than fitting every loaded session. Off-grid endpoints are fine — the
      // scale snaps them onto the nearest bars.
      chart.timeScale().setVisibleRange({
        from: initialRangeRef.current.from as Time,
        to: initialRangeRef.current.to as Time,
      });
    } else {
      chart.timeScale().fitContent();
    }

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      if (debugRaf) cancelAnimationFrame(debugRaf);
      chartApiRef.current = null;
      applyRef.current = null;
      armApplyRef.current = null;
      rulerApplyRef.current = null;
      rulerClearRef.current = () => {};
      avwapApplyRef.current = null;
      avwapDrawRef.current = null;
      paintRef.current = null;
      host.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (tipRef.current) tipRef.current.style.display = "none";
      if (ref.current) ref.current.style.cursor = "";
      ro.disconnect();
      chart.remove();
    };
  }, [
    bars,
    vwapGlobex,
    vwapNy,
    vwapWeekly,
    profileGlobex,
    profileNy,
    ema9,
    ema20,
    ema50,
    ema200,
    rsi,
    atrPoints,
    cvd,
    cvdDivergences,
    markers,
    touches,
    vaSnaps,
    priceLines,
    levels,
    ibList,
    tradeRects,
    focusOnTrade,
    debugZoom,
    footprint,
    regimeStates,
    tickSize,
    pointValue,
    height,
  ]);

  // Re-frame on a new selected day without rebuilding: when the tape spans many
  // sessions and only the focus span changes (same bars), scroll to it in place
  // so the user's zoom into a neighbour isn't thrown away. Skipped under
  // `focusOnTrade`, which owns the viewport; the initial frame is set in the
  // build effect above, so this only fires on subsequent changes.
  useEffect(() => {
    if (focusOnTrade || !initialTimeRange) return;
    const chart = chartApiRef.current;
    if (!chart) return;
    chart.timeScale().setVisibleRange({
      from: initialTimeRange.from as Time,
      to: initialTimeRange.to as Time,
    });
  }, [initialTimeRange, focusOnTrade]);

  const legendItems: LegendItem[] = [];
  if (vwapGlobex && vwapGlobex.length > 0)
    legendItems.push({
      key: "vwapGlobex",
      label: "VWAP · Globex ±1σ ±2σ",
      color: vwapPalette.globex.middle,
    });
  if (vwapNy && vwapNy.length > 0)
    legendItems.push({
      key: "vwapNy",
      label: "VWAP · NY ±1σ ±2σ",
      color: vwapPalette.ny.middle,
    });
  if (vwapWeekly && vwapWeekly.length > 0)
    legendItems.push({
      key: "vwapWeekly",
      label: "VWAP · Weekly ±1σ ±2σ",
      color: vwapPalette.weekly.middle,
    });
  if (avwapAnchor != null)
    legendItems.push({
      key: "vwapAnchored",
      label: "VWAP · Anchored ±1σ ±2σ",
      color: vwapPalette.anchored.middle,
    });
  if (profileGlobex && profileGlobex.length > 0)
    legendItems.push({
      key: "developingProfileGlobex",
      label: "Developing VA · Globex VAH/POC/VAL",
      color: profilePalette.globex.edge,
    });
  if (profileNy && profileNy.length > 0)
    legendItems.push({
      key: "developingProfileNy",
      label: "Developing VA · NY VAH/POC/VAL",
      color: profilePalette.ny.edge,
    });
  // One legend row per EMA so each hides/shows on its own (see emaSeries above).
  const emaLegend: { key: IndicatorKey; pts?: EmaPoint[]; label: string; color: string }[] = [
    { key: "ema9", pts: ema9, label: "EMA 9 · 1-minute", color: emaPalette.fast },
    { key: "ema20", pts: ema20, label: "EMA 20 · 1-minute", color: emaPalette.slow },
    { key: "ema50", pts: ema50, label: "EMA 50 · 1-minute", color: emaPalette.trend50 },
    { key: "ema200", pts: ema200, label: "EMA 200 · 1-minute", color: emaPalette.trend200 },
  ];
  for (const e of emaLegend)
    if (e.pts && e.pts.length > 0)
      legendItems.push({ key: e.key, label: e.label, color: e.color });
  if (atrPoints && atrPoints.length > 0)
    legendItems.push({ key: "atr", label: "ATR 14", color: palette.gold });
  if (cvd && cvd.length > 0)
    legendItems.push({ key: "cvd", label: "CVD · cumulative delta", color: palette.blue });
  if (rsi && rsi.length > 0)
    legendItems.push({ key: "rsi", label: "RSI 14", color: palette.violet });
  if (levels && levels.length > 0)
    legendItems.push({ key: "levels", label: "Session levels", color: palette.blue });
  if (ibList.length > 0) {
    legendItems.push({
      key: "initialBalance",
      label: "Initial Balance · first 60m H/L",
      color: ibPalette.line,
    });
    legendItems.push({
      key: "ibExtensions",
      label: "IB extensions · 1×/1.5×/2×",
      color: ibPalette.ext,
    });
  }
  if (touches && touches.length > 0)
    legendItems.push({ key: "touches", label: "Interactions · touches", color: palette.green });
  if (vaSnaps && vaSnaps.length > 0)
    legendItems.push({ key: "va_snaps", label: "Interactions · VA-snaps", color: palette.red });
  if (bars.length > 0)
    legendItems.push({
      key: "volumeProfile",
      // Say which kind it is: on the sim's charts it's the real tape, on the
      // journal's it's reconstructed from bars, and that changes how much you
      // should trust the exact POC print.
      label: footprint ? "Volume profile · POC/VA (tick)" : "Volume profile · POC/VA (est.)",
      color: palette.gold,
    });

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <div className="chart-tools">
        <ChartToolButton
          icon="📊"
          label={armed ? "Drag a range…" : "Fixed range VP"}
          on={armed}
          onClick={() => arm(!armed)}
          title={
            armed
              ? "Drag across the chart to profile that range (Esc to cancel)"
              : "Fixed-range volume profile — drag across a range to profile it. Drag its edges to resize, its body to move, Del to remove."
          }
        />
        <ChartToolButton
          icon="📏"
          label={rulerArmed ? "Measuring…" : "Measure"}
          on={rulerArmed}
          onClick={() => armRuler(!rulerArmed)}
          title={
            rulerArmed
              ? "Drag (or click, move, click) between two points to measure (Esc to cancel)"
              : "Ruler — measure between two points: points/ticks/%, $ per lot, bars and time. Click the chart or press Esc to dismiss."
          }
        />
        <ChartToolButton
          icon="⚓"
          label={avwapArmed ? "Click a bar…" : "Anchored VWAP"}
          on={avwapArmed}
          onClick={() => armAvwap(!avwapArmed)}
          title={
            avwapArmed
              ? "Click a bar to anchor the VWAP there (Esc to cancel)"
              : "Anchored VWAP — click any bar to draw a VWAP + ±1σ/±2σ bands from that point forward. σ is bar-derived (not tick-exact). Click again to re-anchor."
          }
        />
        {avwapAnchor != null && (
          <ChartToolButton
            icon="⚓✕"
            label="Clear VWAP"
            onClick={clearAvwap}
            title="Remove the anchored VWAP"
          />
        )}
        {selected != null && (
          <ChartToolButton
            icon="🗑"
            label="Delete"
            onClick={deleteSelected}
            title="Remove this profile (Del)"
          />
        )}
        {ranges.length > 1 && (
          <ChartToolButton
            icon="🧹"
            label="Clear all"
            onClick={clearRanges}
            title="Remove every fixed-range profile"
          />
        )}
      </div>
      <div ref={ref} style={{ width: "100%" }} />
      {debugZoom && (
        <div
          ref={debugRef}
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            zIndex: 10,
            pointerEvents: "none",
            background: palette.card,
            border: `1px solid ${palette.cardBorder}`,
            borderRadius: 6,
            padding: "4px 8px",
            font: "11px/1.4 ui-monospace, monospace",
            color: palette.text,
            whiteSpace: "nowrap",
          }}
        />
      )}
      <div
        ref={tipRef}
        style={{
          position: "absolute",
          display: "none",
          zIndex: 10,
          pointerEvents: "none",
          background: palette.card,
          border: `1px solid ${palette.cardBorder}`,
          borderRadius: 6,
          padding: "6px 10px",
          font: "12px/1.5 Inter, sans-serif",
          color: palette.text,
          whiteSpace: "nowrap",
          boxShadow: "0 4px 16px rgba(0,0,0,0.45)",
        }}
      />
      <IndicatorLegend items={legendItems} visibility={vis} onToggle={toggle} />
    </div>
  );
}
