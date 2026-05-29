import { useTradeDetail } from "../hooks/useTrades";
import { useExcursion } from "../hooks/useCharts";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { JournalForm } from "./JournalForm";
import { ReconstructionChart } from "./charts/ReconstructionChart";
import { TradeAnalysis } from "./ai/TradeAnalysis";
import { fmt, fmtDateTime, fmtInt } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";

export function TradeDetail({ scope, tradeNo }: { scope: FilterScope; tradeNo: number }) {
  const { data, isLoading } = useTradeDetail(scope, tradeNo);
  const { data: exc } = useExcursion(scope, tradeNo);
  if (isLoading || !data) return <div className="notice">Loading trade…</div>;

  const t = data.trade;
  const row1: Card[] = [
    { label: "Direction", value: t.direction },
    { label: "Contracts", value: fmtInt(t.max_contracts) },
    { label: "Net PnL", value: fmt(t.net_pnl), tone: toneOf(t.net_pnl) },
    { label: "Avg entry", value: fmt(t.avg_entry, false) },
    { label: "Avg exit", value: fmt(t.avg_exit, false) },
  ];
  const row2: Card[] = [
    { label: "Entry", value: fmtDateTime(t.entry_ts_local) },
    { label: "Exit", value: fmtDateTime(t.exit_ts_local) },
    { label: "Hold", value: `${(t.duration_s / 60).toFixed(1)}m` },
  ];

  return (
    <div>
      <div className="section-title">
        Trade #{t.trade_no} — {fmtDateTime(t.entry_ts_local)}
      </div>
      <KpiGrid cards={row1} template="repeat(5, 1fr)" />
      <KpiGrid cards={row2} template="repeat(3, 1fr)" />
      <ReconstructionChart scope={scope} tradeNo={tradeNo} />
      <TradeAnalysis
        tradeKey={t.trade_key}
        scope={scope}
        hasExcursion={!!exc?.available && !!exc?.has_data}
      />
      <JournalForm
        tradeKey={t.trade_key}
        initialNote={data.note}
        initialTags={data.tags}
      />
    </div>
  );
}
