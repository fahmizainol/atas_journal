import { useEffect, useRef, useState } from "react";
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
import { palette, profilePalette, regimePalette, vwapPalette } from "../../theme";
import { TradeRectanglePrimitive } from "./TradeRectanglePrimitive";
import { RulerPrimitive } from "./RulerPrimitive";
import { MarkerPrimitive } from "./MarkerPrimitive";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import { VolumeProfilePrimitive } from "./VolumeProfilePrimitive";
import { RangeProfilePrimitive } from "./RangeProfilePrimitive";
import { IndicatorLegend, type IndicatorKey, type LegendItem } from "./IndicatorLegend";
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
  Footprint,
  PriceLineSpec,
  ProfilePoint,
  TradeRect,
  VwapPoint,
} from "../../lib/chartTypes";

interface Props {
  bars: Bar[];
  vwapGlobex?: VwapPoint[];
  vwapNy?: VwapPoint[];
  /**
   * Developing value area (POC/VAH/VAL as of each bar's close). Supplied only by
   * the sim, and only for runs that actually traded against it — so a line here
   * is always a level the engine really consulted.
   */
  profile?: ProfilePoint[];
  atrPoints?: ATRPoint[];
  markers?: ChartMarker[];
  priceLines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  tradeRects?: TradeRect[];
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

// Client-side candlestick (+ anchored VWAPs + volume) used by both the
// single-trade reconstruction and the full-day session views. Weekend/overnight
// gaps collapse natively (missing bars aren't drawn).
export function CandlestickChart({
  bars,
  vwapGlobex,
  vwapNy,
  profile,
  atrPoints,
  markers,
  priceLines,
  levels,
  tradeRects,
  footprint,
  regimeStates,
  tickSize,
  pointValue,
  height = 520,
  onTradeClick,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
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
    if (v) armRulerRef.current(false); // one drag tool owns the mouse at a time
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
    if (v && armedRef.current) arm(false);
    rulerArmedRef.current = v;
    setRulerArmed(v);
    rulerApplyRef.current?.(v);
  };
  const armRulerRef = useRef(armRuler);
  armRulerRef.current = armRuler;
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
    const chart: IChartApi = createChart(ref.current, {
      width: ref.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: palette.bg },
        textColor: palette.text,
        fontFamily: "Inter, sans-serif",
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });

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

    // The ribbon is a strip, not a chart: it gets just enough height to read as a
    // colour band. Set after addAtr so both panes are sized from one place.
    const panes0 = chart.panes();
    if (ribbonPane && panes0.length > ribbonPane) {
      panes0[0].setStretchFactor(1000);
      panes0[ribbonPane].setStretchFactor(70);
    }

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
      mid.setData(points.map((v) => ({ time: v.time as Time, value: v.middle })));
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
          line.setData(points.map((v) => ({ time: v.time as Time, value: v[key] })));
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

    // Developing value area: VAH and VAL solid (they are the levels the rules
    // actually test against), POC dashed between them. Deliberately not a shaded
    // band — the VWAP envelope already owns that visual, and stacking two fills
    // makes the one place they overlap, which is the whole setup, unreadable.
    const profileSeries: ISeriesApi<"Line">[] = [];
    if (profile && profile.length > 0) {
      const lines = [
        { key: "vah", color: profilePalette.edge, style: 0, width: 2 },
        { key: "val", color: profilePalette.edge, style: 0, width: 2 },
        { key: "poc", color: profilePalette.poc, style: 2, width: 1 },
      ] as const;
      for (const l of lines) {
        const s_ = chart.addSeries(LineSeries, {
          color: l.color,
          lineWidth: l.width as 1 | 2,
          lineStyle: l.style,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        s_.setData(profile.map((v) => ({ time: v.time as Time, value: v[l.key] })));
        profileSeries.push(s_);
      }
    }

    if (markers && markers.length > 0) {
      const barMap = new Map(bars.map((b) => [b.time, b]));
      const snappedMarkers = markers.map((m) => ({ ...m, time: nearestBar(m.time) }));
      candle.attachPrimitive(new MarkerPrimitive(snappedMarkers, barMap) as any);
    }

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
      setScroll(a || rulerArmedRef.current);
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

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const x = xOf(e);
      const idx = idxAtX(x);
      if (idx == null) return;
      downX = x;
      downY = yOf(e);

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
        if (armedRef.current || rulerArmedRef.current) return;
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
      setScroll(a || armedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
      if (a) ruler.setData(null); // re-arming starts a fresh measurement
      else rulerDrag = null;
    };
    rulerApplyRef.current(rulerArmedRef.current);
    rulerClearRef.current = () => {
      rulerDrag = null;
      ruler.setData(null);
    };

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
      for (const s of profileSeries) s.applyOptions({ visible: v.developingProfile });
      for (const l of levelLines) l.applyOptions({ lineVisible: v.levels, axisLabelVisible: v.levels });
      if (atrPoints && atrPoints.length > 0) {
        if (v.atr && !atrSeries) addAtr();
        else if (!v.atr && atrSeries) {
          chart.removeSeries(atrSeries);
          atrSeries = null;
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
          if (armedRef.current || rulerArmedRef.current || !cb || !param.point) return;
          if (hitTest(param.point.x)) return; // a profile is covering this trade
          const r = hitRect(param.point.x, param.point.y);
          if (r) cb(r);
        });
      }
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      applyRef.current = null;
      armApplyRef.current = null;
      rulerApplyRef.current = null;
      rulerClearRef.current = () => {};
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
    profile,
    atrPoints,
    markers,
    priceLines,
    levels,
    tradeRects,
    footprint,
    regimeStates,
    tickSize,
    pointValue,
    height,
  ]);

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
  if (profile && profile.length > 0)
    legendItems.push({
      key: "developingProfile",
      label: "Developing VA · VAH/POC/VAL",
      color: profilePalette.edge,
    });
  if (atrPoints && atrPoints.length > 0)
    legendItems.push({ key: "atr", label: "ATR 14", color: palette.gold });
  if (levels && levels.length > 0)
    legendItems.push({ key: "levels", label: "Session levels", color: palette.blue });
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
        <button
          className={`chart-tool${armed ? " on" : ""}`}
          onClick={() => arm(!armed)}
          title={
            armed
              ? "Drag across the chart to profile that range (Esc to cancel)"
              : "Fixed-range volume profile — drag across a range to profile it. Drag its edges to resize, its body to move, Del to remove."
          }
        >
          {armed ? "Drag a range…" : "+ Fixed range VP"}
        </button>
        <button
          className={`chart-tool${rulerArmed ? " on" : ""}`}
          onClick={() => armRuler(!rulerArmed)}
          title={
            rulerArmed
              ? "Drag (or click, move, click) between two points to measure (Esc to cancel)"
              : "Ruler — measure between two points: points/ticks/%, $ per lot, bars and time. Click the chart or press Esc to dismiss."
          }
        >
          {rulerArmed ? "Measuring…" : "📏 Measure"}
        </button>
        {selected != null && (
          <button className="chart-tool" onClick={deleteSelected} title="Remove this profile (Del)">
            Delete
          </button>
        )}
        {ranges.length > 1 && (
          <button className="chart-tool" onClick={clearRanges} title="Remove every fixed-range profile">
            Clear all
          </button>
        )}
      </div>
      <div ref={ref} style={{ width: "100%" }} />
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
