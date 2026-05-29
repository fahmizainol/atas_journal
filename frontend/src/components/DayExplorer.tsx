import { type ColumnDef } from "@tanstack/react-table";
import { useDay } from "../hooks/useCalendar";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { DataTable } from "./DataTable";
import { DaySessionChart } from "./charts/DaySessionChart";
import { EquityCurveChart } from "./charts/EquityCurveChart";
import { PerTradeBarChart } from "./charts/PerTradeBarChart";
import { TradeDetail } from "./TradeDetail";
import { fmt, fmtInt, fmtPct, fmtTime } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";
import type { TradeRow } from "../lib/types";

const dayColumns: ColumnDef<TradeRow, any>[] = [
  { accessorKey: "trade_no", header: "#", cell: (c) => `#${c.getValue()}` },
  { accessorKey: "direction", header: "Dir" },
  { accessorKey: "max_contracts", header: "Qty", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "entry_ts_local", header: "Entry", cell: (c) => fmtTime(c.getValue() as string) },
  { accessorKey: "exit_ts_local", header: "Exit", cell: (c) => fmtTime(c.getValue() as string) },
  {
    id: "hold",
    header: "Hold",
    accessorFn: (r) => r.duration_s,
    cell: (c) => `${((c.getValue() as number) / 60).toFixed(1)}m`,
  },
  { accessorKey: "avg_entry", header: "Avg entry", cell: (c) => fmt(c.getValue() as any, false) },
  { accessorKey: "avg_exit", header: "Avg exit", cell: (c) => fmt(c.getValue() as any, false) },
  {
    accessorKey: "net_pnl",
    header: "Net PnL",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
    },
  },
];

export function DayExplorer({ scope, date }: { scope: FilterScope; date: string }) {
  const { data, isLoading } = useDay(scope, date);
  if (isLoading || !data) return <div className="notice">Loading day…</div>;

  const m = data.kpis;
  const pretty = new Date(date + "T00:00:00").toLocaleDateString("en-US", {
    weekday: "long",
    day: "2-digit",
    month: "long",
    year: "numeric",
  });
  const cards: Card[] = [
    {
      label: "Net PnL",
      value: fmt(m.net_pnl),
      tone: toneOf(typeof m.net_pnl === "number" ? m.net_pnl : 0),
      hero: true,
      sub: `${m.trades} trades`,
    },
    { label: "Win rate", value: fmtPct(m.win_rate), sub: `${m.wins}W / ${m.losses}L` },
    { label: "Best trade", value: fmt(m.best_trade), tone: "pos" },
    { label: "Worst trade", value: fmt(m.worst_trade), tone: "neg" },
  ];

  return (
    <div>
      <div className="section-title">{pretty}</div>
      <div className="section-cap">{data.trades.length} trades</div>
      <KpiGrid cards={cards} template="1.5fr 1fr 1fr 1fr" />
      <DaySessionChart scope={scope} date={date} />
      <div className="section-title">Trades this day</div>
      <div className="section-cap">Click a row to expand its full detail.</div>
      <div className="panel">
        <DataTable
          data={data.trades}
          columns={dayColumns}
          rowKey={(r) => r.trade_no}
          renderExpanded={(r) => <TradeDetail scope={scope} tradeNo={r.trade_no} />}
        />
      </div>
      <div className="grid-2">
        {data.equity.length > 0 && <EquityCurveChart data={data.equity} />}
        <PerTradeBarChart data={data.per_trade_bars} />
      </div>
    </div>
  );
}
