import { type ReactNode } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "./DataTable";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { EdgeRow } from "../lib/types";

// One breakdown of a book — bucket, what it traded, what it paid. Shared by the
// journal's Edges tab (the real book) and a strategy run's edges panel (the
// simulated one), so the two read identically and a cut added to one is a cut
// added to both.
export const edgeColumns: ColumnDef<EdgeRow, any>[] = [
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

// `columns` overrides the default set — a strategy run carries an R column and,
// when it is being read against another run, a delta one. `caption` is where a
// cut says what it is worth (its luck, or that it is an outcome and therefore
// isn't a filter at all).
export function EdgeTable<T extends EdgeRow>({
  title,
  data,
  columns,
  caption,
}: {
  title: ReactNode;
  data?: T[];
  columns?: ColumnDef<T, any>[];
  caption?: ReactNode;
}) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data && data.length > 0 ? (
        <div className="table-scroll-x">
          <DataTable data={data} columns={columns ?? (edgeColumns as ColumnDef<T, any>[])} />
        </div>
      ) : (
        <div className="muted">No data.</div>
      )}
      {caption && (
        <div className="section-cap" style={{ marginTop: 6 }}>
          {caption}
        </div>
      )}
    </div>
  );
}
