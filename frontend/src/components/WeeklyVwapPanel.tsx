import { useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "./DataTable";
import { useWeeklyVwapStudy } from "../hooks/useInteractions";
import type {
  WeeklyVwapDay,
  WeeklyVwapFadeRow,
  WeeklyVwapParams,
  WeeklyVwapPosRow,
  WeeklyVwapTouchRateRow,
} from "../lib/interactionTypes";
import { fmt, fmtInt, fmtPct } from "../lib/format";

// The weekly VWAP study — the validation bench for the weekly-envelope family
// before any of it becomes a sim gate or strategy. Session structure only:
// where the open prints in the weekly VWAP envelope, which side of the mid the
// day trades, and whether first band touches fade or break. It shares the
// Lab's symbol/date inputs but commits independently — a separate (cheap) run
// over the same cache.

const pct = (v: number | null | undefined) => (v == null ? "—" : fmtPct(v * 100));
const num = (v: number | null | undefined) => (v == null ? "—" : fmt(v, false));

const posColumns: ColumnDef<WeeklyVwapPosRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "med_drift_pts",
    header: "Med drift",
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      return <span className={v > 0 ? "pos" : v < 0 ? "neg" : "muted"}>{num(v)}</span>;
    },
  },
  { accessorKey: "with_side_rate", header: "With-side %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_close_dist_sigma", header: "Med close σ", cell: (c) => num(c.getValue() as number | null) },
];

const touchColumns: ColumnDef<WeeklyVwapTouchRateRow, any>[] = [
  { accessorKey: "label", header: "Level", cell: (c) => String(c.getValue()) },
  {
    id: "n",
    header: "N",
    accessorFn: (r) => r.n,
    cell: (c) => {
      const r = c.row.original;
      return `${fmtInt(r.n)}/${fmtInt(r.of)}`;
    },
  },
  { accessorKey: "touch_rate", header: "Touch %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_min_after_open", header: "Med min to touch", cell: (c) => num(c.getValue() as number | null) },
];

const fadeColumns: ColumnDef<WeeklyVwapFadeRow, any>[] = [
  { accessorKey: "label", header: "Band", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "hit_mid_rate", header: "Hit-mid %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_toward_pts", header: "Med toward", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "med_beyond_pts", header: "Med beyond", cell: (c) => num(c.getValue() as number | null) },
  {
    accessorKey: "med_edge_pts",
    header: "Med edge",
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      return <span className={v > 0 ? "pos" : v < 0 ? "neg" : "muted"}>{num(v)}</span>;
    },
  },
];

const dayColumns: ColumnDef<WeeklyVwapDay, any>[] = [
  { accessorKey: "day", header: "Day", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "first_session",
    header: "Wk open",
    cell: (c) => (c.getValue() ? "✓" : <span className="muted">—</span>),
  },
  { accessorKey: "side", header: "Side", cell: (c) => String(c.getValue()) },
  { accessorKey: "open_dist_sigma", header: "Open σ", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "close_dist_sigma", header: "Close σ", cell: (c) => num(c.getValue() as number | null) },
  {
    accessorKey: "drift_pts",
    header: "Drift",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={v > 0 ? "pos" : v < 0 ? "neg" : "muted"}>{num(v)}</span>;
    },
  },
  {
    accessorKey: "drift_with_side",
    header: "With side",
    cell: (c) => {
      const v = c.getValue() as boolean | null;
      if (v == null) return <span className="muted">—</span>;
      return v ? "✓" : "✗";
    },
  },
  {
    id: "touches",
    header: "Touched",
    accessorFn: (r) => r.touches.filter((t) => t.touched).map((t) => t.name).join(" "),
    cell: (c) => String(c.getValue() || "—"),
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

export function WeeklyVwapPanel({ symbol, start, end }: {
  symbol: string;
  start: string;
  end: string;
}) {
  const [windowMin, setWindowMin] = useState("60");
  const [committed, setCommitted] = useState<WeeklyVwapParams | null>(null);
  const run = useWeeklyVwapStudy(committed);

  const onRun = () =>
    setCommitted({
      symbol,
      start,
      end,
      outcome_window_min: windowMin ? Number(windowMin) : undefined,
    });

  const data = run.data;
  return (
    <>
      <div className="panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        <div>
          <div className="section-cap">Weekly VWAP</div>
          <div className="muted" style={{ fontSize: 12, maxWidth: 640 }}>
            Weekly-anchored VWAP envelope over the same range: where the open
            prints in it, which side of the mid the day trades, and whether
            first band touches fade or break. The validation pass before any of
            it becomes a gate.
          </div>
        </div>
        <label>
          Outcome window (min)
          <br />
          <input value={windowMin} onChange={(e) => setWindowMin(e.target.value)} style={{ width: 60 }} />
        </label>
        <button type="button" onClick={onRun} disabled={run.isFetching}>
          {run.isFetching ? "Running…" : "Run weekly VWAP study"}
        </button>
      </div>

      {run.isError && <div className="notice">Weekly VWAP study failed: {String((run.error as Error)?.message)}</div>}
      {run.isFetching && <div className="notice">Computing weekly VWAP study…</div>}

      {data && !run.isFetching && (
        <>
          <div className="section-cap" style={{ margin: "10px 0" }}>
            {data.coverage.ran_days}/{data.coverage.requested_days} sessions ·{" "}
            {data.coverage.seasoned_days} seasoned · fade window ={" "}
            {data.outcome_window_min} min
            {data.coverage.skipped.length > 0 && ` · skipped ${data.coverage.skipped.length}`}
          </div>

          <div className="grid-2">
            <Section
              title="Open position in the weekly envelope"
              data={data.aggregates.open_position}
              columns={posColumns}
              keepOrder
              caption="Days cut by where the bell printed vs the weekly bands (in σ). Drift = open → close; with-side = the day drifted further from the weekly mid."
            />
            <Section
              title="Side of weekly VWAP at the bell"
              data={data.aggregates.side}
              columns={posColumns}
              keepOrder
              caption="Just above vs below the weekly mid — the coarsest cut. If with-side beats a coin here, the weekly mid is a trend filter."
            />
          </div>

          <div className="grid-2">
            <Section
              title="Touch rates (approaches from the mid's side)"
              data={data.aggregates.touch_rates}
              columns={touchColumns}
              keepOrder
              caption="How often each weekly level gets tagged, over the days that opened on the level's mid side (the denominator). Med min = median time from the bell to the first tag."
            />
            <Section
              title="First band touch: fade vs break (60m window)"
              data={data.aggregates.band_fades}
              columns={fadeColumns}
              keepOrder
              caption="toward = excursion back to the weekly mid after the touch; beyond = through the band; edge = toward − beyond. Positive edge with a decent hit-mid rate is the fade case."
            />
          </div>

          <Section
            title="By weekday (seasoned days only)"
            data={data.aggregates.weekday}
            columns={posColumns}
            keepOrder
            caption="The open-position read cut by weekday, first sessions of the week excluded (the envelope has no history yet)."
          />

          <Section
            title="Sessions"
            data={data.days}
            columns={dayColumns}
            caption="One row per session — sortable. σ columns = distance from the weekly mid in weekly-band units; Wk open = the week's first session (unseasoned envelope)."
          />
        </>
      )}
    </>
  );
}
