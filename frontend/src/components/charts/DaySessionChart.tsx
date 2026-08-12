import { useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useDayChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import { CandlestickChart } from "./CandlestickChart";
import { TimeframeControl, JOURNAL_TFS } from "./TimeframeControl";

// Full-day session candlestick: every trade's fills + an outcome-tinted holding
// rectangle (reuses the Phase-4 CandlestickChart + TradeRectanglePrimitive).
//
// Built from api/session_chart.py off the tick cache, so it carries the same
// layers a Lab session chart does. The session builder already starts at the
// 18:00 ET Globex open, which is why this no longer asks for a day of extra
// history to make the VWAP band honest.
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

  if (meta && !meta.chart_ticks_available)
    return (
      <div className="notice">
        No tick data cached yet — run a strategy over these sessions to fetch them.
      </div>
    );
  if (isLoading) return <div className="notice">Loading session…</div>;
  if (!data || !data.available) return <div className="notice">Chart data unavailable.</div>;
  if (!data.bars || data.bars.length === 0)
    return <div className="notice">{data.reason ?? "No market data for this session."}</div>;

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
