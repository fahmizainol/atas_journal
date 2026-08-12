import { type ColumnDef } from "@tanstack/react-table";
import { useNavigate, useLocation } from "react-router-dom";
import { DataTable } from "./DataTable";
import { fmt, fmtDateTime, fmtInt, fmtPct } from "../lib/format";
import type { CalendarDay } from "../hooks/useCalendar";

function dayWeekday(date: string): string {
  return new Date(date + "T00:00:00").toLocaleDateString("en-US", { weekday: "short" });
}

const columns: ColumnDef<CalendarDay, any>[] = [
  {
    accessorKey: "date",
    header: "Date",
    cell: (c) => {
      const d = c.getValue() as string;
      return (
        <span>
          {d} <span className="muted">{dayWeekday(d)}</span>
        </span>
      );
    },
  },
  {
    accessorKey: "net_pnl",
    header: "Net PnL",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
    },
  },
  { accessorKey: "trades", header: "Trades", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "win_rate",
    header: "Win rate",
    cell: (c) => fmtPct(c.getValue() as number),
  },
  {
    accessorKey: "attempts",
    header: "Attempts",
    cell: (c) => fmtInt(c.getValue() as number),
  },
  {
    accessorKey: "has_video",
    header: "Video",
    cell: (c) => ((c.getValue() as boolean) ? "🎥" : ""),
  },
  {
    accessorKey: "file_modified",
    header: "Modified",
    // null sorts last; TanStack handles string compare, "—" only renders.
    cell: (c) => fmtDateTime(c.getValue() as string | null),
  },
];

// Sortable list of trading days — the table alternative to the heatmap grid.
// Clicking a row opens that day's explorer, exactly like clicking a cell.
export function CalendarTable({
  days,
  selected,
}: {
  days: CalendarDay[];
  selected: string | null;
}) {
  const navigate = useNavigate();
  const { search } = useLocation();
  return (
    <div className="panel">
      <div className="section-cap">Click a row to explore the day's trades.</div>
      {/* .table-scroll already scrolls both ways and pins the header; the height
          lives in CSS so a phone can trade some of it back (see the Journal
          mobile block) rather than spending most of a 844px screen on it. */}
      <div className="table-scroll calendar-table-scroll">
        <DataTable
          data={days}
          columns={columns}
          rowKey={(r) => r.date}
          selectedKey={selected}
          onRowClick={(r) => navigate({ pathname: `/calendar/${r.date}`, search })}
          initialSort={[{ id: "file_modified", desc: true }]}
        />
      </div>
    </div>
  );
}
