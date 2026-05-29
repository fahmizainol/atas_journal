import { type ColumnDef } from "@tanstack/react-table";
import { useFilters } from "../hooks/useFilters";
import { useEdges } from "../hooks/useEdges";
import { DataTable } from "../components/DataTable";
import { useMeta } from "../hooks/useMeta";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { EdgeRow } from "../lib/types";

const columns: ColumnDef<EdgeRow, any>[] = [
  { accessorKey: "bucket", header: "Bucket", cell: (c) => String(c.getValue()) },
  { accessorKey: "trades", header: "Trades", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "net_pnl",
    header: "Net PnL",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
    },
  },
  { accessorKey: "win_rate", header: "Win rate", cell: (c) => fmtPct(c.getValue() as number) },
  {
    accessorKey: "expectancy",
    header: "Expectancy",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
    },
  },
];

function EdgeTable({ title, data }: { title: string; data?: EdgeRow[] }) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data && data.length > 0 ? (
        <DataTable data={data} columns={columns} />
      ) : (
        <div className="muted">No data.</div>
      )}
    </div>
  );
}

export function Edges() {
  const { scope } = useFilters();
  const { data: meta } = useMeta();
  const { data, isLoading } = useEdges(scope);
  const tzLabel = scope.tz || meta?.default_tz || "local";

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data) return <div className="notice">No trades to display.</div>;

  return (
    <div>
      <div className="section-title">Behavioral edges</div>
      <div className="grid-2">
        <div>
          <EdgeTable title="By weekday" data={data.by_weekday} />
          <EdgeTable title="By hold time" data={data.by_hold_time} />
          <EdgeTable title="Long vs Short" data={data.by_direction} />
        </div>
        <div>
          <EdgeTable title={`By hour (${tzLabel})`} data={data.by_hour_kl} />
          <EdgeTable title="By hour (US Eastern / session)" data={data.by_hour_et} />
        </div>
      </div>
    </div>
  );
}
