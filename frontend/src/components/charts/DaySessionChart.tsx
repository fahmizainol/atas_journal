import { useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useDayChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import { CandlestickChart } from "./CandlestickChart";
import { TimeframeRadio } from "./TimeframeRadio";

// Full-day session candlestick: every trade's fills + an outcome-tinted holding
// rectangle (reuses the Phase-4 CandlestickChart + TradeRectanglePrimitive).
export function DaySessionChart({
  scope,
  date,
  sourceFile = null,
}: {
  scope: FilterScope;
  date: string;
  sourceFile?: string | null;
}) {
  const { data: meta } = useMeta();
  const [tf, setTf] = useState("1m");
  const { data, isLoading } = useDayChart(scope, date, tf, sourceFile);

  if (meta && !meta.databento_available)
    return <div className="notice">Set DATABENTO_API_KEY in .env to render the day candlestick.</div>;
  if (isLoading) return <div className="notice">Loading session…</div>;
  if (!data || !data.available) return <div className="notice">Databento unavailable.</div>;
  if (!data.bars || data.bars.length === 0)
    return <div className="notice">No market data returned for this day.</div>;

  return (
    <div className="panel">
      <TimeframeRadio value={tf} onChange={setTf} />
      <CandlestickChart
        bars={data.bars}
        vwapGlobex={data.vwap_globex}
        vwapNy={data.vwap_ny}
        vwapWeekly={data.vwap_weekly}
        atrPoints={data.atr_points}
        markers={data.markers}
        levels={data.levels}
        tradeRects={data.trades}
        tickSize={data.tick_size}
        pointValue={data.point_value}
        height={580}
      />
      <div className="section-cap" style={{ marginTop: 6 }}>
        Current + prior session — each trade shows a holding rectangle + Buy/Sell fills; white band =
        Globex VWAP ±1σ/±2σ, purple band = NY VWAP ±1σ/±2σ, dotted lines = session levels (ON/PD
        high-low, prior close, open), lower pane = volume. The right-edge histogram is the volume
        profile of the bars on screen — gold = POC, blue rows = value area (70%), with POC/VAH/VAL
        marked on the price axis; zoom to re-profile just the visible window.
      </div>
    </div>
  );
}
