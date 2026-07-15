import { useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../components/DataTable";
import {
  useInteractions,
  useInteractionCoverage,
} from "../hooks/useInteractions";
import type {
  AggRow,
  InteractionParams,
  Touch,
  VaSnap,
  VaSnapAggRow,
} from "../lib/interactionTypes";
import { fmt, fmtInt, fmtPct } from "../lib/format";

// The Interactions Lab — a research bench over the cached tick sessions,
// deliberately separate from the Edges tab (which is trade-derived / live). Here
// nothing depends on trades: you pick a range, run the study, and read how price
// meets the developing NY + Globex profile levels. The output is evidence, not a
// verdict — promoting a cut to a filter/signal stays a manual, downstream step.

const ALL_SOURCES = [
  { key: "ny", label: "NY profile" },
  { key: "globex", label: "Globex profile" },
  { key: "vwap_bands", label: "VWAP bands" },
];

const pct = (v: number | null) => (v == null ? "—" : fmtPct(v));
const num = (v: number | null) => (v == null ? "—" : fmt(v, false));

const aggColumns: ColumnDef<AggRow, any>[] = [
  { accessorKey: "label", header: "Bucket", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "reject_rate", header: "Reject %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_mfe", header: "Avg MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_mae", header: "Avg MAE", cell: (c) => num(c.getValue() as number | null) },
];

const vasnapColumns: ColumnDef<VaSnapAggRow, any>[] = [
  { accessorKey: "label", header: "Snap", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "revert_rate", header: "Reverted to VWAP %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_move", header: "Avg move", cell: (c) => num(c.getValue() as number | null) },
];

function AggTable({ title, data, columns, caption }: {
  title: string;
  data: any[];
  columns: ColumnDef<any, any>[];
  caption?: string;
}) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data.length > 0 ? (
        <DataTable data={data} columns={columns} initialSort={[{ id: "n", desc: true }]} />
      ) : (
        <div className="muted">No data.</div>
      )}
      {caption && <div className="section-cap" style={{ marginTop: 6 }}>{caption}</div>}
    </div>
  );
}

const touchColumns: ColumnDef<Touch, any>[] = [
  { accessorKey: "hhmm", header: "Time", cell: (c) => String(c.getValue()) },
  { accessorKey: "label", header: "Level", cell: (c) => String(c.getValue()) },
  { accessorKey: "zone_px", header: "Price", cell: (c) => fmt(c.getValue() as number, false) },
  { accessorKey: "approach", header: "Appr", cell: (c) => String(c.getValue()) },
  { accessorKey: "nth_touch", header: "Nth", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "outcome",
    header: "Outcome",
    cell: (c) => {
      const v = String(c.getValue());
      const cls = v === "reject" ? "pos" : v === "accept" ? "neg" : "muted";
      return <span className={cls}>{v}</span>;
    },
  },
  { accessorKey: "mfe", header: "MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "mae", header: "MAE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "n_sources", header: "Src", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "signed_delta", header: "Δ", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "level_slope", header: "Slope", cell: (c) => String(c.getValue()) },
];

const snapColumns: ColumnDef<VaSnap, any>[] = [
  { accessorKey: "hhmm", header: "Time", cell: (c) => String(c.getValue()) },
  {
    id: "level",
    header: "Level",
    accessorFn: (r) => `${r.source === "ny" ? "NY" : "Globex"} ${r.level_type}`,
    cell: (c) => String(c.getValue()),
  },
  { accessorKey: "snap_dir", header: "Direction", cell: (c) => String(c.getValue()) },
  { accessorKey: "level_jump_pts", header: "Jump", cell: (c) => fmt(c.getValue() as number, false) },
  { accessorKey: "excursion_bars_before", header: "Excursion (bars)", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "band_at_snap", header: "Band", cell: (c) => String(c.getValue() ?? "—") },
  {
    accessorKey: "reverted",
    header: "Reverted",
    cell: (c) => (c.getValue() ? <span className="pos">yes</span> : <span className="muted">no</span>),
  },
  { accessorKey: "revert_move", header: "Move", cell: (c) => num((c.getValue() as number) ?? null) },
];

export function Interactions() {
  const [symbol, setSymbol] = useState("NQ");
  const [start, setStart] = useState("2025-08-01");
  const [end, setEnd] = useState("2025-08-31");
  const [binSize, setBinSize] = useState("");
  const [sources, setSources] = useState<string[]>(["ny", "globex"]);
  const [windowMin, setWindowMin] = useState("10");
  const [committed, setCommitted] = useState<InteractionParams | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const coverage = useInteractionCoverage(symbol, start, end);
  const run = useInteractions(committed);

  const toggleSource = (key: string) =>
    setSources((prev) => (prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key]));

  const onRun = () => {
    setSelectedDay(null);
    setCommitted({
      symbol,
      start,
      end,
      bin_size: binSize ? Number(binSize) : undefined,
      sources,
      outcome_window_min: windowMin ? Number(windowMin) : undefined,
    });
  };

  const data = run.data;
  const dayList = useMemo(
    () => (data ? Object.keys(data.day_index).sort() : []),
    [data],
  );
  const dayTouches = useMemo(
    () => (data && selectedDay ? data.events.touches.filter((t) => t.day === selectedDay) : []),
    [data, selectedDay],
  );
  const daySnaps = useMemo(
    () => (data && selectedDay ? data.events.va_snaps.filter((s) => s.day === selectedDay) : []),
    [data, selectedDay],
  );

  return (
    <div>
      <div className="section-title">Interactions Lab</div>

      {/* config bar */}
      <div className="panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        <label>Symbol<br /><input value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ width: 70 }} /></label>
        <label>Start<br /><input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>End<br /><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Bin (pts)<br /><input value={binSize} placeholder="tick" onChange={(e) => setBinSize(e.target.value)} style={{ width: 60 }} /></label>
        <label>Outcome window (min)<br /><input value={windowMin} onChange={(e) => setWindowMin(e.target.value)} style={{ width: 60 }} /></label>
        <div>
          Sources<br />
          {ALL_SOURCES.map((s) => (
            <label key={s.key} style={{ marginRight: 10, fontWeight: 400 }}>
              <input type="checkbox" checked={sources.includes(s.key)} onChange={() => toggleSource(s.key)} /> {s.label}
            </label>
          ))}
        </div>
        <button type="button" onClick={onRun} disabled={run.isFetching}>
          {run.isFetching ? "Running…" : "Run tracking"}
        </button>
      </div>

      {/* cache coverage strip */}
      {coverage.data && (
        <div className="panel">
          <div className="section-cap">
            Cache coverage — {coverage.data.days.filter((d) => d.rth).length}/{coverage.data.days.length} sessions have ticks
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
            {coverage.data.days.map((d) => (
              <span
                key={d.date}
                title={`${d.date}${d.rth ? " · rth" : ""}${d.on ? " · overnight" : ""}`}
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 2,
                  background: d.rth ? (d.on ? "var(--pos, #3fb950)" : "#6ea8fe") : "#30363d",
                  opacity: d.rth ? 1 : 0.4,
                  outline: d.date === selectedDay ? "2px solid #f0f6fc" : "none",
                }}
              />
            ))}
          </div>
        </div>
      )}

      {run.isError && <div className="notice">Run failed: {String((run.error as Error)?.message)}</div>}
      {!committed && <div className="notice">Pick a range and hit Run tracking.</div>}
      {run.isFetching && <div className="notice">Computing interactions…</div>}

      {data && !run.isFetching && (
        <>
          <div className="section-cap" style={{ margin: "10px 0" }}>
            {data.coverage.ran_days}/{data.coverage.requested_days} sessions ·{" "}
            {data.events.touches.length} touches · {data.events.va_snaps.length} VA-snaps
            {data.coverage.skipped.length > 0 && ` · skipped ${data.coverage.skipped.length} (no ticks)`}
          </div>

          {/* aggregates */}
          <div className="grid-2">
            <div>
              <AggTable title="By level source" data={data.aggregates.by_source} columns={aggColumns} />
              <AggTable title="By nth-touch" data={data.aggregates.by_nth_touch} columns={aggColumns} />
            </div>
            <div>
              <AggTable
                title="Confluence lift"
                data={data.aggregates.confluence_lift}
                columns={aggColumns}
                caption="Reject rate of a lone level vs. one stacked with another source."
              />
              <AggTable
                title="VA-snap → reversion"
                data={data.aggregates.vasnap_reversion}
                columns={vasnapColumns}
                caption="After a value boundary snapped across price, did price revert to session VWAP."
              />
            </div>
          </div>

          {/* per-day drill-down */}
          <div className="panel">
            <div className="section-cap">Sessions</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {dayList.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={d === selectedDay ? "chip selected" : "chip"}
                  onClick={() => setSelectedDay(d === selectedDay ? null : d)}
                >
                  {d} · {data.day_index[d].n_touches}t/{data.day_index[d].n_snaps}s
                </button>
              ))}
            </div>
          </div>

          {selectedDay && (
            <>
              {/* Phase 6 mounts the DaySessionChart + InteractionPrimitive overlay here. */}
              <div className="panel">
                <div className="section-cap">{selectedDay} — VA-snaps</div>
                {daySnaps.length > 0 ? (
                  <DataTable data={daySnaps} columns={snapColumns} initialSort={[{ id: "hhmm", desc: false }]} />
                ) : (
                  <div className="muted">No VA-snaps this session.</div>
                )}
              </div>
              <div className="panel">
                <div className="section-cap">{selectedDay} — touches</div>
                <DataTable data={dayTouches} columns={touchColumns} initialSort={[{ id: "hhmm", desc: false }]} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
