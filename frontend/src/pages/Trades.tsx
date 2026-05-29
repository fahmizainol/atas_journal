import { type ColumnDef } from "@tanstack/react-table";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useFilters } from "../hooks/useFilters";
import { useTrades } from "../hooks/useTrades";
import { DataTable } from "../components/DataTable";
import { TradeDetail } from "../components/TradeDetail";
import { fmt, fmtDateTime, fmtInt, fmtTime } from "../lib/format";
import type { TradeRow } from "../lib/types";

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
];

export function Trades() {
  const { scope } = useFilters();
  const { data, isLoading } = useTrades(scope);
  const { tradeNo } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.length === 0)
    return <div className="notice">No trades to display.</div>;

  const selected = tradeNo ? Number(tradeNo) : data[0].trade_no;

  return (
    <div>
      <div className="section-title">Trades</div>
      <div className="section-cap">Select a row to inspect the trade below.</div>
      <div className="panel">
        <DataTable
          data={data}
          columns={columns}
          rowKey={(r) => r.trade_no}
          selectedKey={selected}
          onRowClick={(r) => navigate({ pathname: `/trades/${r.trade_no}`, search })}
          initialSort={[{ id: "trade_no", desc: false }]}
        />
      </div>
      <TradeDetail scope={scope} tradeNo={selected} />
    </div>
  );
}
