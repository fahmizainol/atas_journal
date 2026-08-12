import { type ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useFilters } from "../hooks/useFilters";
import { useTrades } from "../hooks/useTrades";
import { useTradeVideoStatuses } from "../hooks/useVideo";
import { DataTable } from "../components/DataTable";
import { TradeDetail } from "../components/TradeDetail";
import { BadgeList } from "../components/BadgeInput";
import { fmt, fmtDateTime, fmtInt, fmtTime } from "../lib/format";
import type { TradeRow, TradeVideoStatus } from "../lib/types";

function fmtOffset(s: number): string {
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

function videoTitle(status: TradeVideoStatus | undefined): string {
  if (!status?.has_video) return "No recording linked for this trade's attempt";
  if (!status.exists) return "Recording is linked, but the file is missing";
  if (!status.playable) return "Recording is linked, but this format cannot play in the browser";
  if (!status.bookmark) return "Recording is linked, but this trade has no bookmark yet";
  return "Open this trade and jump to its bookmarked recording time";
}

export function Trades() {
  const { scope } = useFilters();
  const { data, isLoading } = useTrades(scope);
  const { data: videoStatuses } = useTradeVideoStatuses(scope);
  const [jump, setJump] = useState<{
    tradeKey: string;
    offsetS: number;
    nonce: number;
  } | null>(null);
  const { tradeNo } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.length === 0)
    return <div className="notice">No trades to display.</div>;

  const expanded = tradeNo ? Number(tradeNo) : null;
  const statusByTrade = videoStatuses?.statuses ?? {};
  const columns: ColumnDef<TradeRow, any>[] = [
    { accessorKey: "trade_no", header: "#", cell: (c) => `#${c.getValue()}` },
    { accessorKey: "instrument", header: "Instrument" },
    { accessorKey: "direction", header: "Dir" },
    { accessorKey: "max_contracts", header: "Qty", cell: (c) => fmtInt(c.getValue() as number) },
    {
      accessorKey: "entry_ts_local",
      header: "Entry",
      cell: (c) => fmtDateTime(c.getValue() as string),
    },
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
      cell: (c) => {
        const trade = c.row.original;
        const status = statusByTrade[trade.trade_key];
        const bookmark = status?.bookmark;
        if (!status?.has_video) return <span className="section-cap" title={videoTitle(status)}>—</span>;
        if (!status.exists || !status.playable) {
          return <span className="section-cap" title={videoTitle(status)}>Linked</span>;
        }
        if (!bookmark) return <span className="section-cap" title={videoTitle(status)}>No mark</span>;
        return (
          <button
            type="button"
            className="btn-xs"
            onClick={(e) => {
              e.stopPropagation();
              setJump((prev) => ({
                tradeKey: trade.trade_key,
                offsetS: bookmark.offset_s,
                nonce: (prev?.nonce ?? 0) + 1,
              }));
              navigate({ pathname: `/trades/${trade.trade_no}`, search });
            }}
            title={videoTitle(status)}
          >
            ▶ {fmtOffset(bookmark.offset_s)}
          </button>
        );
      },
    },
    {
      id: "setup",
      header: "Setup",
      enableSorting: false,
      cell: (c) => <BadgeList items={c.row.original.setups ?? []} />,
    },
  ];

  return (
    <div>
      <div className="section-title">Trades</div>
      <div className="section-cap">Click a row to expand its full detail.</div>
      <div className="panel table-scroll-x-narrow">
        <DataTable
          data={data}
          columns={columns}
          rowKey={(r) => r.trade_no}
          initialSort={[{ id: "trade_no", desc: false }]}
          expandedKey={expanded}
          onExpandedChange={(key) =>
            navigate({
              pathname: key == null ? "/trades" : `/trades/${key}`,
              search,
            })
          }
          renderExpanded={(r) => (
            <TradeDetail
              scope={scope}
              tradeNo={r.trade_no}
              jumpToOffset={
                jump?.tradeKey === r.trade_key
                  ? { offsetS: jump.offsetS, nonce: jump.nonce }
                  : null
              }
            />
          )}
        />
      </div>
    </div>
  );
}
