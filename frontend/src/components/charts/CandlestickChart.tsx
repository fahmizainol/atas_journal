import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { palette } from "../../theme";
import { TradeRectanglePrimitive } from "./TradeRectanglePrimitive";
import type { Bar, ChartMarker, PriceLineSpec, TradeRect, VwapPoint } from "../../lib/chartTypes";

interface Props {
  bars: Bar[];
  vwap?: VwapPoint[];
  markers?: ChartMarker[];
  priceLines?: PriceLineSpec[];
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
  markers,
  priceLines,
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

    const candle = chart.addCandlestickSeries({
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

    const volume = chart.addHistogramSeries({
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

    if (vwap && vwap.length > 0) {
      const mid = chart.addLineSeries({ color: palette.gold, lineWidth: 1, priceLineVisible: false });
      mid.setData(vwap.map((v) => ({ time: v.time as Time, value: v.middle })));
      for (const key of ["upper", "lower"] as const) {
        const line = chart.addLineSeries({
          color: palette.muted,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        line.setData(vwap.map((v) => ({ time: v.time as Time, value: v[key] })));
      }
    }

    if (markers && markers.length > 0) {
      candle.setMarkers(
        markers
          .map((m) => ({
            time: nearestBar(m.time) as Time,
            position: m.position,
            shape: m.shape,
            color: m.color,
            text: m.text,
          }))
          .sort((a, b) => (a.time as number) - (b.time as number)),
      );
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
  }, [bars, vwap, markers, priceLines, tradeRects, height]);

  return <div ref={ref} style={{ width: "100%" }} />;
}
