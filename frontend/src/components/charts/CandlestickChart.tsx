import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { palette } from "../../theme";
import { TradeRectanglePrimitive } from "./TradeRectanglePrimitive";
import { MarkerPrimitive } from "./MarkerPrimitive";
import type { ATRPoint, Bar, ChartMarker, PriceLineSpec, TradeRect, VwapPoint } from "../../lib/chartTypes";

interface Props {
  bars: Bar[];
  vwap?: VwapPoint[];
  atrPoints?: ATRPoint[];
  markers?: ChartMarker[];
  priceLines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  showLevels?: boolean;
  tradeRects?: TradeRect[];
  height?: number;
}

const VOL_UP = "rgba(33,192,122,0.5)";
const VOL_DOWN = "rgba(245,69,95,0.5)";

// Client-side candlestick (+ VWAP band + volume) used by both the single-trade
// reconstruction and the full-day session views. Weekend/overnight gaps
// collapse natively (missing bars aren't drawn).
export function CandlestickChart({
  bars,
  vwap,
  atrPoints,
  markers,
  priceLines,
  levels,
  showLevels = true,
  tradeRects,
  height = 520,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

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
    const nearestBar = (t: number): number => {
      if (t <= barTimes[0]) return barTimes[0];
      if (t >= barTimes[last]) return barTimes[last];
      let lo = 0;
      let hi = last;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] === t) return t;
        if (barTimes[mid] < t) lo = mid + 1;
        else hi = mid - 1;
      }
      const after = barTimes[lo];
      const before = barTimes[hi];
      return t - before <= after - t ? before : after;
    };
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

    if (atrPoints && atrPoints.length > 0) {
      const atr = chart.addSeries(
        LineSeries,
        {
          color: palette.gold,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        },
        1,
      );
      atr.setData(atrPoints.map((p) => ({ time: p.time as Time, value: p.atr })));
      // Force a price-dominant split — default is 1000:1000 (50/50) for two panes,
      // so set both factors explicitly. Ratio 5:1 ≈ 83% price / 17% ATR.
      const panes = chart.panes();
      if (panes.length > 1) {
        panes[0].setStretchFactor(1000);
        panes[1].setStretchFactor(200);
      }
    }

    if (vwap && vwap.length > 0) {
      const mid = chart.addSeries(LineSeries, { color: palette.gold, lineWidth: 1, priceLineVisible: false });
      mid.setData(vwap.map((v) => ({ time: v.time as Time, value: v.middle })));
      for (const key of ["upper", "lower"] as const) {
        const line = chart.addSeries(LineSeries, {
          color: palette.muted,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        line.setData(vwap.map((v) => ({ time: v.time as Time, value: v[key] })));
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

    if (showLevels) {
      for (const lv of levels ?? []) {
        candle.createPriceLine({
          price: lv.price,
          color: lv.color,
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: true,
          title: lv.title,
        });
      }
    }

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
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [bars, vwap, atrPoints, markers, priceLines, levels, showLevels, tradeRects, height]);

  return <div ref={ref} style={{ width: "100%" }} />;
}
