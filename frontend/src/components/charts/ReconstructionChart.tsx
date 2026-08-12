import { useMemo, useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useTradeChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import { CandlestickChart } from "./CandlestickChart";
import { TimeframeControl, JOURNAL_TFS } from "./TimeframeControl";
import { KpiGrid } from "../KpiGrid";
import { fmt, fmtPct } from "../../lib/format";
import type { Card } from "../KpiCard";

// Single-trade candlestick reconstruction + MAE/MFE KPIs.
//
// Draws the same layers as the Lab's charts because it is now built from the
// same session builder off the same tick cache (api/session_chart.py). The
// overlays that used to be missing here weren't withheld — 1-minute OHLCV bars
// simply couldn't support them: a footprint, an exact volume profile and a CVD
// line all need the tape, not one volume number per minute.
export function ReconstructionChart({ scope, tradeNo }: { scope: FilterScope; tradeNo: number }) {
  const { data: meta } = useMeta();
  const [tf, setTf] = useState("1m");
  const { data, isLoading } = useTradeChart(scope, tradeNo, tf);
  // Stable array reference so CandlestickChart's effect doesn't rebuild every render.
  const tradeRects = useMemo(
    () => (data?.trade_rect ? [data.trade_rect] : []),
    [data?.trade_rect],
  );

  if (meta && !meta.chart_ticks_available)
    return (
      <div className="notice">
        No tick data cached yet — run a strategy over these sessions to fetch them, and the
        chart will draw with fills, MAE/MFE and exit efficiency.
      </div>
    );
  if (isLoading) return <div className="notice">Loading chart…</div>;
  if (!data) return null;
  if (!data.available) return <div className="notice">Chart data unavailable.</div>;
  // `reason` names the session and contract that are missing. The old path
  // returned a bare empty list for a swallowed 402, which read as "nothing
  // traded this day" — the one thing it definitely did not mean.
  if (!data.bars || data.bars.length === 0)
    return <div className="notice">{data.reason ?? "No market data for this session."}</div>;

  const exc = data.excursion;
  const excCards: Card[] = exc
    ? [
        { label: "MFE", value: fmt(exc.mfe_usd), tone: "pos" },
        { label: "MAE", value: fmt(exc.mae_usd), tone: "neg" },
        {
          label: "Exit efficiency",
          value: exc.exit_efficiency != null ? fmtPct(exc.exit_efficiency * 100, 0) : "—",
        },
        {
          label: "Avg ATR (hold)",
          value:
            exc.avg_atr_pts == null
              ? "—"
              : `${exc.avg_atr_pts.toFixed(2)} pts · ${fmt(exc.avg_atr_usd)}`,
        },
      ]
    : [];

  return (
    <div className="panel">
      <TimeframeControl value={tf} onChange={setTf} options={JOURNAL_TFS} />
      <CandlestickChart
        bars={data.bars}
        vwapGlobex={data.vwap_globex}
        vwapNy={data.vwap_ny}
        vwapWeekly={data.vwap_weekly}
        profileGlobex={data.profile_globex}
        profileNy={data.profile_ny}
        ema9={data.ema9}
        ema20={data.ema20}
        ema50={data.ema50}
        ema200={data.ema200}
        rsi={data.rsi}
        atrPoints={data.atr_points}
        cvd={data.cvd}
        cvdDivergences={data.cvd_divergences}
        footprint={data.footprint}
        ib={data.ib}
        markers={data.markers}
        priceLines={data.price_lines}
        levels={data.levels}
        tradeRects={tradeRects}
        tickSize={data.tick_size}
        pointValue={data.point_value}
        height={560}
      />
      <div className="section-cap" style={{ marginTop: 6 }}>
        Drag = pan · wheel = zoom. Buy/Sell arrows = fills, dashed lines = avg entry/exit,
        dotted lines = session levels (ON/PD high-low, prior close, open), circles = MAE/MFE,
        white band = Globex VWAP ±1σ/±2σ, purple band = NY VWAP ±1σ/±2σ, lower pane = volume.
        Hover the trade for its PnL. Every other layer — both developing value areas, the
        9/20/50/200 EMA, RSI, CVD and the IB — is off by default; toggle it in the legend.
      </div>
      {excCards.length > 0 && <KpiGrid cards={excCards} template="repeat(4, 1fr)" />}
    </div>
  );
}
