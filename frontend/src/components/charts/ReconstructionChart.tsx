import { useMemo, useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useTradeChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import { CandlestickChart } from "./CandlestickChart";
import { TimeframeRadio } from "./TimeframeRadio";
import { LevelsToggle } from "./LevelsToggle";
import { KpiGrid } from "../KpiGrid";
import { fmt, fmtPct } from "../../lib/format";
import type { Card } from "../KpiCard";

// Single-trade candlestick reconstruction + MAE/MFE KPIs. Mirrors
// app.render_trade_detail's chart block, incl. graceful Databento degradation.
export function ReconstructionChart({ scope, tradeNo }: { scope: FilterScope; tradeNo: number }) {
  const { data: meta } = useMeta();
  const [tf, setTf] = useState("1m");
  const [showLevels, setShowLevels] = useState(true);
  const { data, isLoading } = useTradeChart(scope, tradeNo, tf);
  // Stable array reference so CandlestickChart's effect doesn't rebuild every render.
  const tradeRects = useMemo(
    () => (data?.trade_rect ? [data.trade_rect] : []),
    [data?.trade_rect],
  );

  if (meta && !meta.databento_available)
    return (
      <div className="notice">
        Set DATABENTO_API_KEY in .env to render the candlestick chart with fills, MAE/MFE
        and exit efficiency.
      </div>
    );
  if (isLoading) return <div className="notice">Loading chart…</div>;
  if (!data) return null;
  if (!data.available) return <div className="notice">Databento unavailable.</div>;
  if (!data.bars || data.bars.length === 0)
    return <div className="notice">No market data returned for this window.</div>;

  const exc = data.excursion;
  const excCards: Card[] = exc
    ? [
        { label: "MFE", value: fmt(exc.mfe_usd), tone: "pos" },
        { label: "MAE", value: fmt(exc.mae_usd), tone: "neg" },
        {
          label: "Exit efficiency",
          value: exc.exit_efficiency != null ? fmtPct(exc.exit_efficiency * 100, 0) : "—",
        },
      ]
    : [];

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
        priceLines={data.price_lines}
        levels={data.levels}
        showLevels={showLevels}
        tradeRects={tradeRects}
        height={560}
      />
      <div className="section-cap" style={{ marginTop: 6 }}>
        Drag = pan · wheel = zoom. Buy/Sell arrows = fills, dashed lines = avg entry/exit,
        dotted lines = session levels (ON/PD high-low, prior close, open), circles = MAE/MFE,
        gold band = VWAP ±1σ, lower pane = volume. Hover the trade for its PnL.
      </div>
      {excCards.length > 0 && <KpiGrid cards={excCards} template="repeat(3, 1fr)" />}
    </div>
  );
}
