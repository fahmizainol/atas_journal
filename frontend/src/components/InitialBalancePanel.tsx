import { useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "./DataTable";
import { useIbStudy } from "../hooks/useInteractions";
import type {
  IbCutRow,
  IbDay,
  IbExtRow,
  IbOrbRow,
  IbParams,
  IbRateRow,
} from "../lib/interactionTypes";
import { fmt, fmtInt, fmtPct } from "../lib/format";

// The Initial Balance & ORB study — the validation bench for the IB/ORB family
// before any of it becomes a sim gate or strategy (research doc:
// docs/research/initial-balance-orb.md). Session structure only: no touch
// events, so a fresh range runs in seconds. It shares the Lab's symbol/date
// inputs but commits independently — the touch study and this one are separate
// runs over the same cache.

const pct = (v: number | null | undefined) => (v == null ? "—" : fmtPct(v * 100));
const num = (v: number | null | undefined) => (v == null ? "—" : fmt(v, false));

const DAY_TYPE_LABEL: Record<string, string> = {
  normal: "normal",
  normal_variation: "normal variation",
  trend: "trend",
  neutral_center: "neutral · center",
  neutral_extreme: "neutral · extreme",
};

const rateColumns: ColumnDef<IbRateRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  {
    id: "n",
    header: "N",
    accessorFn: (r) => r.n,
    cell: (c) => {
      const r = c.row.original;
      return r.of != null ? `${fmtInt(r.n)}/${fmtInt(r.of)}` : fmtInt(r.n);
    },
  },
  { accessorKey: "pct", header: "%", cell: (c) => pct(c.getValue() as number | null) },
];

const extColumns: ColumnDef<IbExtRow, any>[] = [
  { accessorKey: "label", header: "Extension", cell: (c) => String(c.getValue()) },
  { accessorKey: "value", header: "×IB", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "pct", header: "% of days", cell: (c) => pct(c.getValue() as number | null) },
];

const cutColumns: ColumnDef<IbCutRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "trend_rate", header: "Trend %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "both_rate", header: "Both-break %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_ext_x", header: "Med ext ×IB", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "med_range_x", header: "Med range ×IB", cell: (c) => num(c.getValue() as number | null) },
];

const orbColumns: ColumnDef<IbOrbRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "follow_rate", header: "Follow %", cell: (c) => pct(c.getValue() as number | null) },
  {
    accessorKey: "avg_r",
    header: "Avg R",
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      const cls = v >= 0.2 ? "pos" : v <= -0.2 ? "neg" : "muted";
      return <span className={cls}>{num(v)}</span>;
    },
  },
  { accessorKey: "med_r", header: "Med R", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "med_move_pts", header: "Med move", cell: (c) => num(c.getValue() as number | null) },
];

const dayColumns: ColumnDef<IbDay, any>[] = [
  { accessorKey: "day", header: "Day", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "day_type",
    header: "Day type",
    cell: (c) => DAY_TYPE_LABEL[String(c.getValue())] ?? String(c.getValue()),
  },
  { accessorKey: "ib_range", header: "IB range", cell: (c) => num(c.getValue() as number) },
  { accessorKey: "ib_vs_adr", header: "IB ÷ ADR", cell: (c) => num(c.getValue() as number | null) },
  {
    id: "first_break",
    header: "1st break",
    accessorFn: (r) => (r.first_break ? `${r.first_break.side} ${r.first_break.hhmm}` : "—"),
    cell: (c) => String(c.getValue()),
  },
  {
    accessorKey: "broke_both",
    header: "Both",
    cell: (c) => (c.getValue() ? "✓" : <span className="muted">—</span>),
  },
  { accessorKey: "max_ext_x", header: "Ext ×IB", cell: (c) => num(c.getValue() as number) },
  { accessorKey: "range_x", header: "Range ×IB", cell: (c) => num(c.getValue() as number) },
  { accessorKey: "close_pos", header: "Close pos", cell: (c) => num(c.getValue() as number) },
  {
    id: "or5",
    header: "5m ORB R",
    accessorFn: (r) => r.orb?.["5"]?.r_mult ?? null,
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      return <span className={v > 0 ? "pos" : v < 0 ? "neg" : "muted"}>{num(v)}</span>;
    },
  },
  { accessorKey: "gap_pts", header: "Gap", cell: (c) => num(c.getValue() as number | null) },
  {
    accessorKey: "ib_vs_on",
    header: "IB vs ON",
    cell: (c) => String(c.getValue() ?? "—"),
  },
];

function Section({ title, data, columns, caption, keepOrder }: {
  title: string;
  data: any[];
  columns: ColumnDef<any, any>[];
  caption?: string;
  keepOrder?: boolean;
}) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data.length > 0 ? (
        <DataTable data={data} columns={columns} initialSort={keepOrder ? [] : undefined} />
      ) : (
        <div className="muted">No data.</div>
      )}
      {caption && <div className="section-cap" style={{ marginTop: 6 }}>{caption}</div>}
    </div>
  );
}

export function InitialBalancePanel({ symbol, start, end }: {
  symbol: string;
  start: string;
  end: string;
}) {
  const [ibMinutes, setIbMinutes] = useState("60");
  const [committed, setCommitted] = useState<IbParams | null>(null);
  const run = useIbStudy(committed);

  const onRun = () =>
    setCommitted({
      symbol,
      start,
      end,
      ib_minutes: ibMinutes ? Number(ibMinutes) : undefined,
    });

  const data = run.data;
  return (
    <>
      <div className="panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        <div>
          <div className="section-cap">Initial Balance &amp; ORB</div>
          <div className="muted" style={{ fontSize: 12, maxWidth: 640 }}>
            Session-structure study over the same range: IB break rates, extension
            distribution, CBOT day types, opening-range follow-through. The
            validation pass before any of it becomes a gate.
          </div>
        </div>
        <label>
          IB window (min)
          <br />
          <input value={ibMinutes} onChange={(e) => setIbMinutes(e.target.value)} style={{ width: 60 }} />
        </label>
        <button type="button" onClick={onRun} disabled={run.isFetching}>
          {run.isFetching ? "Running…" : "Run IB study"}
        </button>
      </div>

      {run.isError && <div className="notice">IB study failed: {String((run.error as Error)?.message)}</div>}
      {run.isFetching && <div className="notice">Computing IB study…</div>}

      {data && !run.isFetching && (
        <>
          <div className="section-cap" style={{ margin: "10px 0" }}>
            {data.coverage.ran_days}/{data.coverage.requested_days} sessions · IB ={" "}
            first {data.ib_minutes} min of RTH
            {data.coverage.skipped.length > 0 && ` · skipped ${data.coverage.skipped.length} (no ticks)`}
          </div>

          <div className="grid-2">
            <Section
              title="IB break rates"
              data={data.aggregates.break_rates}
              columns={rateColumns}
              keepOrder
              caption="How often, which side, and when price leaves the IB. The lore says 70–80% of days break; published NQ data says ~96% — this is our number."
            />
            <Section
              title="Day types (CBOT classifier)"
              data={data.aggregates.day_types}
              columns={rateColumns}
              keepOrder
              caption="From IB-extension multiples: normal = IB ≥85% of the day's range; normal variation ≤2× IB; trend >2× with a directional close; neutral = both sides broken. Base rates for these were never published — this is the first measured set on our data."
            />
          </div>

          <div className="grid-2">
            <Section
              title="Extension distribution"
              data={data.aggregates.ext_distribution}
              columns={extColumns}
              keepOrder
              caption="Max one-side extension beyond the IB, in IB-range multiples. The 1×/1.5×/2× rows are the platform-drawn 'targets' — note how rarely they print."
            />
            <Section
              title="Break epilogue"
              data={data.aggregates.break_epilogue}
              columns={rateColumns}
              keepOrder
              caption="Single-break days: did the break hold into the close, or fail back inside the IB. Double-break days: does the close land on the second break's side ('second break wins', claimed ~72% — single-source)."
            />
          </div>

          <div className="grid-2">
            <Section
              title="ORB follow-through"
              data={data.aggregates.orb_follow}
              columns={orbColumns}
              keepOrder
              caption="Zarattini read per window: enter at the window candle's close in its direction, exit at the session close. R is normalized by the candle-extreme stop distance but the stop is NOT enforced intraday — that's the sim engine's job if this graduates. The paper's edge was +0.13R at a 24% win rate (with the stop)."
            />
            <Section
              title="Gap alignment (primary ORB window)"
              data={data.aggregates.gap_cuts}
              columns={orbColumns}
              keepOrder
              caption="The same ORB trade cut by opening gap (vs prior RTH close, in ADR multiples; flat = |gap| ≤ 0.15× ADR). Research verdict: gap direction is a documented dud — check it locally anyway."
            />
          </div>

          <div className="grid-2">
            <Section
              title="IB width vs ADR"
              data={data.aggregates.ib_width_terciles}
              columns={cutColumns}
              keepOrder
              caption="IB range over the prior-14-session average day range, in terciles — the strongest documented conditioner: narrow IBs extend, wide IBs hold."
            />
            <Section
              title="Globex range vs IB"
              data={data.aggregates.globex_cuts}
              columns={cutColumns}
              keepOrder
              caption="Where the open printed in the overnight range, and whether the IB stayed inside it. No public study quantifies this — a genuinely novel cut for us."
            />
          </div>

          <Section
            title="By weekday"
            data={data.aggregates.weekday}
            columns={cutColumns}
            keepOrder
            caption="Directionality by weekday (claimed: Monday clean, Wednesday choppy — single-source)."
          />

          <Section
            title="Sessions"
            data={data.days}
            columns={dayColumns}
            caption="One row per session — sortable. Close pos = where the close sits in the day's range (0 = low, 1 = high)."
          />
        </>
      )}
    </>
  );
}
