import { useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useDayChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import { CandlestickChart } from "./CandlestickChart";
import { TimeframeRadio } from "./TimeframeRadio";
import { LevelsToggle } from "./LevelsToggle";

// Full-day session candlestick: every trade's fills + an outcome-tinted holding
// rectangle (reuses the Phase-4 CandlestickChart + TradeRectanglePrimitive).
export function DaySessionChart({ scope, date }: { scope: FilterScope; date: string }) {
  const { data: meta } = useMeta();
  const [tf, setTf] = useState("1m");
  const [showLevels, setShowLevels] = useState(true);
  const { data, isLoading } = useDayChart(scope, date, tf);

  if (meta && !meta.databento_available)
    return <div className="notice">Set DATABENTO_API_KEY in .env to render the day candlestick.</div>;
  if (isLoading) return <div className="notice">Loading session…</div>;
  if (!data || !data.available) return <div className="notice">Databento unavailable.</div>;
  if (!data.bars || data.bars.length === 0)
    return <div className="notice">No market data returned for this day.</div>;

  return (
    <div className="panel">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <TimeframeRadio value={tf} onChange={setTf} />
        <LevelsToggle value={showLevels} onChange={setShowLevels} />
      </div>
      <CandlestickChart
        bars={data.bars}
        vwap={data.vwap}
        markers={data.markers}
        levels={data.levels}
        showLevels={showLevels}
        tradeRects={data.trades}
        height={580}
      />
      <div className="section-cap" style={{ marginTop: 6 }}>
        Current + prior session — each trade shows a holding rectangle + Buy/Sell fills; gold band =
        VWAP ±1σ, dotted lines = session levels (ON/PD high-low, prior close, open), lower pane = volume.
      </div>
    </div>
  );
}
