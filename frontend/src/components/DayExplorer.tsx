import { type ColumnDef } from "@tanstack/react-table";
import { useEffect, useState } from "react";
import { useDay } from "../hooks/useCalendar";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { DataTable } from "./DataTable";
import { DaySessionChart } from "./charts/DaySessionChart";
import { DayJournalForm } from "./DayJournalForm";
import { WhatIfExits } from "./WhatIfExits";
import { EquityCurveChart } from "./charts/EquityCurveChart";
import { PerTradeBarChart } from "./charts/PerTradeBarChart";
import { TradeDetail } from "./TradeDetail";
import { SessionControl } from "./SessionControl";
import { DayReplayProvider, DayReplaySlot, TradeReplayCell } from "./charts/DayReplayer";
import { BadgeList } from "./BadgeInput";
import { fmt, fmtDateTime, fmtInt, fmtPct, fmtTime } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";
import type { TradeRow } from "../lib/types";

// "narrow-hide" columns drop below 640px (see DataTable's ColumnMeta): what a
// phone keeps is the trade, when, what it made, and the ▶ — everything else is
// one tap away in the expanded detail.
const dayColumns: ColumnDef<TradeRow, any>[] = [
  { accessorKey: "trade_no", header: "#", cell: (c) => `#${c.getValue()}` },
  { accessorKey: "direction", header: "Dir", meta: { className: "narrow-hide" } },
  {
    accessorKey: "max_contracts",
    header: "Qty",
    meta: { className: "narrow-hide" },
    cell: (c) => fmtInt(c.getValue() as number),
  },
  { accessorKey: "entry_ts_local", header: "Entry", cell: (c) => fmtTime(c.getValue() as string) },
  {
    accessorKey: "exit_ts_local",
    header: "Exit",
    meta: { className: "narrow-hide" },
    cell: (c) => fmtTime(c.getValue() as string),
  },
  {
    id: "hold",
    header: "Hold",
    accessorFn: (r) => r.duration_s,
    meta: { className: "narrow-hide" },
    cell: (c) => `${((c.getValue() as number) / 60).toFixed(1)}m`,
  },
  {
    accessorKey: "net_pnl",
    header: "Net PnL",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
    },
  },
  {
    id: "setup",
    header: "Setup",
    enableSorting: false,
    meta: { className: "narrow-hide" },
    cell: (c) => <BadgeList items={c.row.original.setups ?? []} />,
  },
  {
    id: "replay",
    header: "Replay",
    enableSorting: false,
    cell: (c) => <TradeReplayCell trade={c.row.original} />,
  },
];

export function DayExplorer({ scope, date }: { scope: FilterScope; date: string }) {
  // null = show the latest attempt (server default); switching pins a take.
  const [attempt, setAttempt] = useState<string | null>(null);
  useEffect(() => setAttempt(null), [date]); // back to latest when the day changes
  const { data, isLoading } = useDay(scope, date, attempt);
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

  // Was the direction right, per horizon. The card is tinted by the SIGN OF THE
  // MEDIAN MOVE and never by the hit rate: a signed median is a fact about the
  // day, whereas colouring "58% right" green or red would be this component
  // picking a pass mark and applying it to a scalper and a swing alike.
  const ed = data.entry_direction;
  const entryCards: Card[] = ed.horizons.map((h) => ({
    label: `Right at ${h.label}`,
    value: h.hit_rate == null ? "—" : fmtPct(h.hit_rate),
    tone: h.median_pts == null ? "neutral" : toneOf(h.median_pts),
    sub:
      h.n === 0
        ? "no tape this far out"
        : `${h.right}/${h.n} right${h.flat ? ` · ${h.flat} flat` : ""} · median ${
            h.median_pts! > 0 ? "+" : ""
          }${h.median_pts!.toFixed(2)} pts`,
  }));

  return (
    <div>
      <div className="day-head">
        <div>
          <div className="section-title">{pretty}</div>
          <div className="section-cap">
            {data.trades.length} trades
            {data.attempts.length > 1 &&
              ` · ${data.attempts.find((a) => a.source_file === data.source_file)?.label} of ${data.attempts.length}`}
          </div>
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
      <SessionControl sourceFile={data.source_file} />
      <DayReplayProvider
        scope={scope}
        date={date}
        trades={data.trades}
        instrument={data.instrument}
      >
        <KpiGrid cards={cards} template="1.5fr 1fr 1fr 1fr" />
        <KpiGrid cards={sideCards} template="1fr 1fr 1fr 1fr" />
        <KpiGrid cards={flowCards} template="repeat(6, 1fr)" />
        {ed.measured > 0 && (
          <>
            <div className="section-title">Entry direction</div>
            <div className="section-cap">
              Where price was 30 seconds, 1 minute and 5 minutes after each entry,
              signed to its direction — measured off the tape and ignoring what you
              did with the trade, so this grades the entry rather than the exit.
              {ed.measured < ed.trades &&
                ` Measured on ${ed.measured} of ${ed.trades} trades; the rest have no cached tape.`}
            </div>
            <KpiGrid cards={entryCards} template="repeat(3, 1fr)" />
          </>
        )}
        <DayReplaySlot />
        <div className="section-title">Trades this day</div>
        <div className="section-cap">Click a row to expand its full detail.</div>
        <div className="panel compact-table table-scroll-x-narrow">
          <DataTable
            data={data.trades}
            columns={dayColumns}
            rowKey={(r) => r.trade_no}
            scrollOnExpand={false}
            renderExpanded={(r) => (
              <TradeDetail scope={scope} tradeNo={r.trade_no} />
            )}
          />
        </div>
        <DayJournalForm date={date} />
        <WhatIfExits sourceFile={data.source_file} />
        <DaySessionChart scope={scope} date={date} sourceFile={data.source_file} />
        <div className="grid-2">
          {data.equity.length > 0 && <EquityCurveChart data={data.equity} />}
          <PerTradeBarChart data={data.per_trade_bars} />
        </div>
      </DayReplayProvider>
    </div>
  );
}
