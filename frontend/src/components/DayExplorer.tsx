import { type ColumnDef } from "@tanstack/react-table";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDay, useDeleteDay, useDeleteAttempt } from "../hooks/useCalendar";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { DataTable } from "./DataTable";
import { DaySessionChart } from "./charts/DaySessionChart";
import { DayJournalForm } from "./DayJournalForm";
import { EquityCurveChart } from "./charts/EquityCurveChart";
import { PerTradeBarChart } from "./charts/PerTradeBarChart";
import { TradeDetail } from "./TradeDetail";
import { VideoReviewProvider, TradeVideoCell } from "./VideoReview";
import { fmt, fmtDateTime, fmtInt, fmtPct, fmtTime } from "../lib/format";
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
  {
    id: "video",
    header: "Video",
    enableSorting: false,
    cell: (c) => <TradeVideoCell trade={c.row.original} />,
  },
];

export function DayExplorer({ scope, date }: { scope: FilterScope; date: string }) {
  // null = show the latest attempt (server default); switching pins a take.
  const [attempt, setAttempt] = useState<string | null>(null);
  useEffect(() => setAttempt(null), [date]); // back to latest when the day changes
  const { data, isLoading } = useDay(scope, date, attempt);
  const navigate = useNavigate();
  const deleteDay = useDeleteDay();
  const deleteAttempt = useDeleteAttempt();
  const onDelete = () => {
    if (!data) return;
    const msg =
      `Delete all executions and journal trades for ${date}?\n\n` +
      `${data.trades.length} trades will be removed. ` +
      `Notes and AI analyses are kept (they'll reattach if you re-import an identical replay). ` +
      `Statistics rows persist per source file and will be overwritten on re-import.`;
    if (!window.confirm(msg)) return;
    deleteDay.mutate(
      { date },
      { onSuccess: () => navigate("/calendar", { replace: true }) },
    );
  };
  const onDeleteAttempt = () => {
    if (!data) return;
    const cur = data.attempts.find((a) => a.source_file === data.source_file);
    const msg =
      `Delete ${cur?.label ?? "this attempt"} (${data.source_file}) for ${date}?\n\n` +
      `Only this replay take is removed — the day's other attempts stay. ` +
      `You can re-upload this export later.`;
    if (!window.confirm(msg)) return;
    deleteAttempt.mutate(
      { sourceFile: data.source_file },
      { onSuccess: () => setAttempt(null) }, // fall back to the latest remaining take
    );
  };
  if (isLoading || !data) return <div className="notice">Loading day…</div>;

  const m = data.kpis;
  const x = data.extras;
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

  const sideCards: Card[] = [
    {
      label: "Long",
      value: fmt(x.long.net_pnl),
      tone: toneOf(x.long.net_pnl),
      sub: `${x.long.trades} trades · ${fmtPct(x.long.win_rate)} win`,
    },
    {
      label: "Short",
      value: fmt(x.short.net_pnl),
      tone: toneOf(x.short.net_pnl),
      sub: `${x.short.trades} trades · ${fmtPct(x.short.win_rate)} win`,
    },
    { label: "Profit factor", value: fmt(m.profit_factor, false) },
    {
      label: "Total contracts",
      value: fmtInt(x.total_contracts),
      sub: `${(x.total_contracts / Math.max(m.trades, 1)).toFixed(1)} / trade`,
    },
  ];

  const flowCards: Card[] = [
    {
      label: "Avg MFE / MAE",
      value: `${fmt(x.avg_mfe_usd)} / ${fmt(x.avg_mae_usd)}`,
    },
    {
      label: "Avg exit efficiency",
      value: x.avg_exit_efficiency == null ? "—" : fmtPct(x.avg_exit_efficiency),
    },
    {
      label: "Avg ATR (hold)",
      value:
        x.avg_atr_pts == null
          ? "—"
          : `${x.avg_atr_pts.toFixed(2)} pts · ${fmt(x.avg_atr_usd)}`,
    },
    {
      label: "Avg hold",
      value: typeof m.avg_trade_length_s === "number"
        ? `${(m.avg_trade_length_s / 60).toFixed(1)}m`
        : "—",
    },
    {
      label: "Trading window",
      value: `${fmtTime(x.window_start)}–${fmtTime(x.window_end)}`,
    },
    {
      label: "Modified",
      value: fmtDateTime(data.file_modified),
      sub: data.attempts.length > 1 ? `${data.attempts.length} attempts` : undefined,
    },
  ];

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 12,
        }}
      >
        <div>
          <div className="section-title">{pretty}</div>
          <div className="section-cap">
            {data.trades.length} trades
            {data.attempts.length > 1 &&
              ` · ${data.attempts.find((a) => a.source_file === data.source_file)?.label} of ${data.attempts.length}`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {data.attempts.length > 1 && (
            <button
              type="button"
              className="btn-danger"
              onClick={onDeleteAttempt}
              disabled={deleteAttempt.isPending}
              title="Delete only the replay take currently shown; the day's other attempts stay."
            >
              {deleteAttempt.isPending ? "Deleting…" : "Delete this attempt"}
            </button>
          )}
          <button
            type="button"
            className="btn-danger"
            onClick={onDelete}
            disabled={deleteDay.isPending}
            title="Delete all executions and trades for this day (every attempt). Use before re-importing a replayed ATAS export."
          >
            {deleteDay.isPending ? "Deleting…" : "Delete day's data"}
          </button>
        </div>
      </div>
      {data.attempts.length > 1 && (
        <div className="radio-group" style={{ margin: "10px 0" }}>
          {data.attempts.map((a) => (
            <button
              key={a.source_file}
              type="button"
              className={a.source_file === data.source_file ? "active" : ""}
              onClick={() => setAttempt(a.source_file)}
              title={`${a.source_file}${a.file_modified ? ` · modified ${fmtDateTime(a.file_modified)}` : ""}`}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
      <VideoReviewProvider sourceFile={data.source_file} scope={scope}>
        <KpiGrid cards={cards} template="1.5fr 1fr 1fr 1fr" />
        <KpiGrid cards={sideCards} template="1fr 1fr 1fr 1fr" />
        <KpiGrid cards={flowCards} template="repeat(6, 1fr)" />
        <DayJournalForm date={date} />
        <DaySessionChart scope={scope} date={date} sourceFile={data.source_file} />
        <div className="section-title">Trades this day</div>
        <div className="section-cap">Click a row to expand its full detail.</div>
        <div className="panel">
          <DataTable
            data={data.trades}
            columns={dayColumns}
            rowKey={(r) => r.trade_no}
            scrollOnExpand={false}
            renderExpanded={(r) => <TradeDetail scope={scope} tradeNo={r.trade_no} />}
          />
        </div>
        <div className="grid-2">
          {data.equity.length > 0 && <EquityCurveChart data={data.equity} />}
          <PerTradeBarChart data={data.per_trade_bars} />
        </div>
      </VideoReviewProvider>
    </div>
  );
}
