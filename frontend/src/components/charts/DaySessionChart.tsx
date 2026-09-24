import { useCallback, useMemo, useState } from "react";
import { useMeta } from "../../hooks/useMeta";
import { useDayChart } from "../../hooks/useCharts";
import type { FilterScope } from "../../lib/queryKeys";
import {
  loadDynamicSwingVwapParams,
  loadModernVwapParams,
  saveDynamicSwingVwapParams,
  saveModernVwapParams,
} from "../../lib/chartPrefs";
import type { ModernVwapParams } from "../../lib/modernVwap";
import type { DsvParams } from "../../lib/dynamicSwingVwap";
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

  // Modern VWAP's parameters. Sticky-global (lib/chartPrefs), the same store the
  // Interactions Lab's chart reads — this page has no run config to keep them
  // in, and how you have the indicator set is a statement about the indicator
  // rather than about this session.
  const [mvParams, setMvParams] = useState(loadModernVwapParams);
  const patchMv = useCallback((patch: Partial<ModernVwapParams>) => {
    setMvParams((prev) => {
      const next = { ...prev, ...patch };
      saveModernVwapParams(next);
      return next;
    });
  }, []);
  const mv = useMemo(() => ({ params: mvParams, onChange: patchMv }), [mvParams, patchMv]);
  // The Zeiierman line, held the same way — a separate indicator, so separate
  // state; see lib/dynamicSwingVwap for why it isn't a mode of the one above.
  const [dsvParams, setDsvParams] = useState(loadDynamicSwingVwapParams);
  const patchDsv = useCallback((patch: Partial<DsvParams>) => {
    setDsvParams((prev) => {
      const next = { ...prev, ...patch };
      saveDynamicSwingVwapParams(next);
      return next;
    });
  }, []);
  const dsv = useMemo(
    () => ({ params: dsvParams, onChange: patchDsv }),
    [dsvParams, patchDsv],
  );

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
        profileWeekly={data.profile_weekly}
        contextProfiles={data.context_profiles}
        modernVwap={mv}
        dynamicSwingVwap={dsv}
        ema9={data.ema9}
        ema20={data.ema20}
        ema50={data.ema50}
        ema200={data.ema200}
        rsi={data.rsi}
        atrPoints={data.atr_points}
        cvd={data.cvd}
        cvdDivergences={data.cvd_divergences}
        delta={data.delta}
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
        marked on the price axis; zoom to re-profile just the visible window. Its "…" adds a second
        lane beside it for net delta at price — green where buyers lifted, red where sellers hit,
        length = how far net — so a heavy price that netted to nothing reads differently from a thin
        one that was bought one-way. Beside it, in their own
        gutters: the RTH session's own volume at price, and the frozen composite over the sessions
        in front of it. Every layer has its own eye in the list top-left.
      </div>
    </div>
  );
}
