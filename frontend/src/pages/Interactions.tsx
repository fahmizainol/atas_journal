import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../components/DataTable";
import { InitialBalancePanel } from "../components/InitialBalancePanel";
import { WeeklyVwapPanel } from "../components/WeeklyVwapPanel";
import { CandlestickChart } from "../components/charts/CandlestickChart";
import {
  useInteractions,
  useInteractionCoverage,
  useInteractionDayChart,
  useInteractionRuns,
} from "../hooks/useInteractions";
import { useRegimeRange } from "../hooks/useRegime";
import { CLASS_LABEL, type RegimeClass } from "../lib/regimeTypes";
import { regimePalette } from "../theme";
import type {
  AggRow,
  BandContextRow,
  BandOccupancyRow,
  InteractionParams,
  SavedRun,
  Touch,
  VaSnap,
  VaSnapAggRow,
  VaSnapContRow,
} from "../lib/interactionTypes";
import { fmt, fmtInt, fmtPct } from "../lib/format";

// One session's candles with the developing NY + Globex levels and the touch /
// VA-snap overlay, built from the same tick engine as the events.
function DayChart({
  symbol,
  day,
  binSize,
  sources,
  ticksPerBar,
  touches,
  vaSnaps,
}: {
  symbol: string;
  day: string;
  binSize?: number;
  sources?: string[];
  ticksPerBar?: number;
  touches: Touch[];
  vaSnaps: VaSnap[];
}) {
  const { data, isLoading } = useInteractionDayChart(symbol, day, binSize, sources, ticksPerBar);
  if (isLoading) return <div className="notice">Loading session…</div>;
  if (!data || !data.available) return <div className="notice">No cached ticks for this session.</div>;
  if (!data.bars || data.bars.length === 0) return <div className="notice">No bars for this session.</div>;
  return (
    <CandlestickChart
      bars={data.bars}
      vwapGlobex={data.vwap_globex}
      vwapNy={data.vwap_ny}
      vwapWeekly={data.vwap_weekly}
      profileGlobex={data.profile_globex}
      profileNy={data.profile_ny}
      ib={data.ib}
      touches={touches}
      vaSnaps={vaSnaps}
      tickSize={data.tick_size}
      pointValue={data.point_value}
      height={560}
    />
  );
}

// The Interactions Lab — a research bench over the cached tick sessions,
// deliberately separate from the Edges tab (which is trade-derived / live). Here
// nothing depends on trades: you pick a range, run the study, and read how price
// meets the developing NY + Globex profile levels. The output is evidence, not a
// verdict — promoting a cut to a filter/signal stays a manual, downstream step.

const ALL_SOURCES = [
  { key: "ny", label: "NY profile" },
  { key: "globex", label: "Globex profile" },
  { key: "vwap_bands", label: "VWAP bands" },
  { key: "session_refs", label: "Session refs" },
];

// The interactions API sends rates as fractions (0.714), but fmtPct expects an
// already-scaled percentage (71.4) like the rest of the API sends — scale here.
const pct = (v: number | null) => (v == null ? "—" : fmtPct(v * 100));
const num = (v: number | null) => (v == null ? "—" : fmt(v, false));

// The per-session day-type from the (shared, per-day) regime cache — the same
// class the Strategies tab colours its calendar by. Shown here so a session's
// touch/band behaviour can be read against the regime it happened in. `dot` is
// the compact swatch for the Sessions chips; the full pill labels the drill-down.
function RegimeBadge({ klass, dot }: { klass: RegimeClass; dot?: boolean }) {
  const bg = regimePalette.klass[klass];
  const label = CLASS_LABEL[klass];
  if (dot) {
    return (
      <span
        title={`Regime: ${label}`}
        style={{ width: 8, height: 8, borderRadius: 2, background: bg, flexShrink: 0 }}
      />
    );
  }
  return (
    <span
      style={{
        background: bg,
        borderRadius: 4,
        padding: "1px 8px",
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

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
  { accessorKey: "n_trivial", header: "Triv", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "revert_rate_30", header: "≤30m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "revert_rate_60", header: "≤60m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "revert_rate", header: "By close %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_move", header: "Avg move", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_adverse", header: "Avg adverse", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_dist", header: "Avg dist", cell: (c) => num(c.getValue() as number | null) },
];

const bandContextColumns: ColumnDef<BandContextRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "n",
    header: "N",
    cell: (c) => {
      const v = c.getValue() as number | null;
      return v == null ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "reject_rate", header: "Reject % (30m)", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_mfe", header: "Med MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "med_mae", header: "Med MAE", cell: (c) => num(c.getValue() as number | null) },
  {
    accessorKey: "ratio",
    header: "MFE/MAE",
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      const cls = v >= 1.3 ? "pos" : v <= 0.8 ? "neg" : "muted";
      return <span className={cls}>{num(v)}</span>;
    },
  },
];

const bandOccupancyColumns: ColumnDef<BandOccupancyRow, any>[] = [
  {
    accessorKey: "label",
    header: "Band",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "Total" ? <strong>{v}</strong> : v;
    },
  },
  { accessorKey: "avg_min", header: "Avg min/session", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "pct", header: "% of session", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "minutes", header: "Total min", cell: (c) => fmtInt(c.getValue() as number) },
];

// Append a summary row so the table foots to the whole session: minutes and
// avg/session sum across the bands, and pct is 100% by construction (the bands
// partition every classified minute). Empty in → empty out (no phantom total).
function withBandTotals(rows: BandOccupancyRow[]): BandOccupancyRow[] {
  if (rows.length === 0) return rows;
  const minutes = rows.reduce((s, r) => s + r.minutes, 0);
  const avg_min = rows.reduce((s, r) => s + (r.avg_min ?? 0), 0);
  return [...rows, { label: "Total", minutes, pct: 1, avg_min: Math.round(avg_min * 10) / 10 }];
}

// The regime-class mix across the run's sessions — how many trend-up / balance /
// … days the window held. `klass: null` marks the grand-total row.
interface RegimeMixRow {
  klass: RegimeClass | null;
  days: number;
  pct: number | null;
}

const REGIME_ORDER: RegimeClass[] = [
  "trend_up", "trend_down", "balance", "parked", "mixed", "unknown",
];

const regimeMixColumns: ColumnDef<RegimeMixRow, any>[] = [
  {
    accessorKey: "klass",
    header: "Regime",
    cell: (c) => {
      const k = c.getValue() as RegimeClass | null;
      if (k == null) return <strong>Total</strong>;
      return (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <RegimeBadge klass={k} dot />
          {CLASS_LABEL[k]}
        </span>
      );
    },
  },
  { accessorKey: "days", header: "Days", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "pct", header: "% of sessions", cell: (c) => pct(c.getValue() as number | null) },
];

// Regime mix cross-tabbed by weekday: rows Mon..Fri (+ any weekend session that
// slipped in), one column per regime class present in the run, plus a total.
// `weekday: null` marks the grand-total row.
interface WeekdayRow {
  weekday: string | null;
  counts: Partial<Record<RegimeClass, number>>;
  total: number;
}

const WEEKDAY_LABEL: Record<number, string> = {
  1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 0: "Sun",
};
// Mon-first ordering; weekend last if it ever appears.
const WEEKDAY_ORDER = [1, 2, 3, 4, 5, 6, 0];

// Parse "YYYY-MM-DD" as UTC midnight so the weekday can't drift with the local
// zone, then read the UTC day-of-week.
function weekdayIdx(day: string): number {
  return new Date(`${day}T00:00:00Z`).getUTCDay();
}

function weekdayColumns(classes: RegimeClass[]): ColumnDef<WeekdayRow, any>[] {
  const cols: ColumnDef<WeekdayRow, any>[] = [
    {
      id: "weekday",
      header: "Day",
      accessorFn: (r) => r.weekday,
      cell: (c) => {
        const v = c.getValue() as string | null;
        return v == null ? <strong>Total</strong> : v;
      },
    },
  ];
  for (const k of classes) {
    cols.push({
      id: k,
      header: CLASS_LABEL[k],
      accessorFn: (r) => r.counts[k] ?? 0,
      cell: (c) => {
        const n = c.getValue() as number;
        return n === 0 ? <span className="muted">—</span> : fmtInt(n);
      },
    });
  }
  cols.push({
    id: "total",
    header: "Total",
    accessorFn: (r) => r.total,
    cell: (c) => <strong>{fmtInt(c.getValue() as number)}</strong>,
  });
  return cols;
}

const vasnapContColumns: ColumnDef<VaSnapContRow, any>[] = [
  { accessorKey: "label", header: "Snap", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "hold_rate_30", header: "Hold ≤30m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "hold_rate_60", header: "Hold ≤60m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_run_30", header: "Run 30m", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_run_60", header: "Run 60m", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_stop_dist", header: "Stop dist", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "rr_60", header: "R:R 60m", cell: (c) => num(c.getValue() as number | null) },
];

function AggTable({ title, data, columns, caption, keepOrder }: {
  title: string;
  data: any[];
  columns: ColumnDef<any, any>[];
  caption?: string;
  // Preserve the server's row order (band position, cut → benchmark) instead
  // of sorting by N — the band-context tables read top-to-bottom as a story.
  keepOrder?: boolean;
}) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data.length > 0 ? (
        <DataTable
          data={data}
          columns={columns}
          initialSort={keepOrder ? [] : [{ id: "n", desc: true }]}
        />
      ) : (
        <div className="muted">No data.</div>
      )}
      {caption && <div className="section-cap" style={{ marginTop: 6 }}>{caption}</div>}
    </div>
  );
}

const outcomeSpan = (v: string) => {
  const cls = v === "reject" ? "pos" : v === "accept" ? "neg" : "muted";
  return <span className={cls}>{v}</span>;
};

const touchColumns: ColumnDef<Touch, any>[] = [
  { accessorKey: "hhmm", header: "Time", cell: (c) => String(c.getValue()) },
  { accessorKey: "label", header: "Level", cell: (c) => String(c.getValue()) },
  { accessorKey: "zone_px", header: "Price", cell: (c) => fmt(c.getValue() as number, false) },
  { accessorKey: "approach", header: "Appr", cell: (c) => String(c.getValue()) },
  { accessorKey: "nth_touch", header: "Nth", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "closed_by",
    header: "Gap by",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "level" ? <span className="neg">{v}</span> : v === "price" ? v : <span className="muted">{v}</span>;
    },
  },
  { accessorKey: "level_age_min", header: "Age", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "outcome", header: "Outcome", cell: (c) => outcomeSpan(String(c.getValue())) },
  {
    id: "o30",
    header: "30m",
    accessorFn: (r) => r.outcomes?.["30"]?.outcome ?? "—",
    cell: (c) => outcomeSpan(String(c.getValue())),
  },
  {
    id: "o60",
    header: "60m",
    accessorFn: (r) => r.outcomes?.["60"]?.outcome ?? "—",
    cell: (c) => outcomeSpan(String(c.getValue())),
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
  {
    accessorKey: "snap_class",
    header: "Class",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "node_flip" ? <span className="neg">{v}</span> : <span className="muted">{v}</span>;
    },
  },
  { accessorKey: "level_jump_pts", header: "Jump", cell: (c) => fmt(c.getValue() as number, false) },
  {
    accessorKey: "co_snaps",
    header: "Co",
    cell: (c) => {
      const v = (c.getValue() as number) ?? 0;
      return v === 0 ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "excursion_bars_before", header: "Excursion (bars)", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "band_at_snap", header: "Band", cell: (c) => String(c.getValue() ?? "—") },
  {
    accessorKey: "reverted",
    header: "Reverted",
    cell: (c) => (c.getValue() ? <span className="pos">yes</span> : <span className="muted">no</span>),
  },
  {
    accessorKey: "revert_min",
    header: "→VWAP min",
    cell: (c) => {
      const v = c.getValue() as number | null | undefined;
      return v == null ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "vwap_dist_pts", header: "Dist", cell: (c) => num((c.getValue() as number) ?? null) },
  { accessorKey: "revert_move", header: "Move", cell: (c) => num((c.getValue() as number) ?? null) },
  { accessorKey: "adverse_move", header: "Adverse", cell: (c) => num((c.getValue() as number) ?? null) },
];

export function Interactions() {
  const [symbol, setSymbol] = useState("NQ");
  const [start, setStart] = useState("2025-08-01");
  const [end, setEnd] = useState("2025-08-31");
  const [binSize, setBinSize] = useState("");
  const [sources, setSources] = useState<string[]>(["ny", "globex", "session_refs"]);
  const [windowMin, setWindowMin] = useState("10");
  const [committed, setCommitted] = useState<InteractionParams | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  // Chart timeframe for the session drill-down: null = 1-minute candles, a number
  // = that many ticks per bar (the strategies' native tick bars). The events are
  // unchanged; only the candle grid they overlay swaps.
  const [chartTicks, setChartTicks] = useState<number | null>(null);

  const coverage = useInteractionCoverage(symbol, start, end);
  const run = useInteractions(committed);
  const savedRuns = useInteractionRuns();
  const queryClient = useQueryClient();

  // The shared per-day regime, over the committed run's window — same cache the
  // Strategies tab reads, so no recompute. Range payload carries the class per
  // day (no ribbon). day → class lookup for the Sessions chips and drill-down.
  const regimeRange = useRegimeRange(
    committed?.symbol ?? null,
    committed?.start ?? null,
    committed?.end ?? null,
  );
  const regimeByDay = useMemo(() => {
    const m = new Map<string, RegimeClass>();
    for (const d of regimeRange.data?.days ?? []) m.set(d.date, d.class);
    return m;
  }, [regimeRange.data]);

  // A fresh compute writes a new snapshot server-side — refresh the list so it
  // shows up without a reload.
  useEffect(() => {
    if (run.isSuccess) {
      queryClient.invalidateQueries({ queryKey: ["interactions", "saved-runs"] });
    }
  }, [run.isSuccess, run.dataUpdatedAt, queryClient]);

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

  // Reopen a snapshot: sync the config bar to its (fully resolved) params and
  // commit them verbatim — same config hash, so the server answers from disk.
  const loadRun = (r: SavedRun) => {
    const c = r.config;
    setSymbol(c.symbol);
    setStart(c.start);
    setEnd(c.end);
    setBinSize(String(c.bin_size));
    setWindowMin(String(c.outcome_window_min));
    setSources(c.sources);
    setSelectedDay(null);
    setCommitted({
      symbol: c.symbol,
      start: c.start,
      end: c.end,
      bin_size: c.bin_size,
      va_pct: c.va_pct,
      sources: c.sources,
      outcome_window_min: c.outcome_window_min,
      zone_cluster_pts: c.zone_cluster_pts,
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
  // Tally the regime class over the sessions this run actually covered (dayList),
  // so the mix matches the Sessions chips rather than the raw calendar range.
  const regimeMix = useMemo<RegimeMixRow[]>(() => {
    const counts = new Map<RegimeClass, number>();
    for (const d of dayList) {
      const k = regimeByDay.get(d);
      if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    const rows: RegimeMixRow[] = REGIME_ORDER.filter((k) => counts.has(k)).map((k) => ({
      klass: k,
      days: counts.get(k)!,
      pct: counts.get(k)! / total,
    }));
    rows.push({ klass: null, days: total, pct: 1 });
    return rows;
  }, [dayList, regimeByDay]);
  // The same tally cross-cut by weekday. `classes` are the regime classes that
  // actually appear (drives the columns); `rows` are Mon..Fri with per-class
  // counts and a grand-total row.
  const weekdayMix = useMemo<{ rows: WeekdayRow[]; classes: RegimeClass[] }>(() => {
    // grid[weekdayIdx][class] = count
    const grid = new Map<number, Map<RegimeClass, number>>();
    const seenClasses = new Set<RegimeClass>();
    for (const d of dayList) {
      const k = regimeByDay.get(d);
      if (!k) continue;
      const wd = weekdayIdx(d);
      if (!grid.has(wd)) grid.set(wd, new Map());
      const row = grid.get(wd)!;
      row.set(k, (row.get(k) ?? 0) + 1);
      seenClasses.add(k);
    }
    if (seenClasses.size === 0) return { rows: [], classes: [] };
    const classes = REGIME_ORDER.filter((k) => seenClasses.has(k));
    const totals = new Map<RegimeClass, number>();
    let grand = 0;
    const rows: WeekdayRow[] = [];
    for (const wd of WEEKDAY_ORDER) {
      const row = grid.get(wd);
      if (!row) continue;
      const counts: Partial<Record<RegimeClass, number>> = {};
      let total = 0;
      for (const k of classes) {
        const n = row.get(k) ?? 0;
        if (n) counts[k] = n;
        total += n;
        totals.set(k, (totals.get(k) ?? 0) + n);
      }
      grand += total;
      rows.push({ weekday: WEEKDAY_LABEL[wd], counts, total });
    }
    rows.push({ weekday: null, counts: Object.fromEntries(totals) as WeekdayRow["counts"], total: grand });
    return { rows, classes };
  }, [dayList, regimeByDay]);
  const weekdayCols = useMemo(() => weekdayColumns(weekdayMix.classes), [weekdayMix.classes]);

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

      {/* saved snapshots — reopening one is a disk read, not a recompute */}
      {savedRuns.data && savedRuns.data.length > 0 && (
        <div className="panel">
          <div className="section-cap">Saved runs</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {savedRuns.data.map((r) => (
              <button
                key={r.run_id}
                type="button"
                className="chip"
                title={`bin ${r.config.bin_size} · VA ${r.config.va_pct} · window ${r.config.outcome_window_min}m · cluster ${r.config.zone_cluster_pts} pts · ${r.coverage.ran_days}/${r.coverage.requested_days} sessions`}
                onClick={() => loadRun(r)}
                disabled={run.isFetching}
              >
                {r.config.symbol} · {r.config.start} → {r.config.end} ·{" "}
                {r.config.sources.join("+")} · {r.n_touches}t/{r.n_snaps}s
              </button>
            ))}
          </div>
        </div>
      )}

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

      {/* Initial Balance & ORB — independent of the touch study; shares the
          symbol/date inputs above but commits its own (much cheaper) run. */}
      <InitialBalancePanel symbol={symbol} start={start} end={end} />

      {/* Weekly VWAP envelope — same contract as the IB panel: shares the
          symbol/date inputs above, commits its own (cheap) run. */}
      <WeeklyVwapPanel symbol={symbol} start={start} end={end} />

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
          <AggTable
            title="Outcome by horizon"
            data={data.aggregates.by_horizon ?? []}
            columns={aggColumns}
            caption="The same touches rescored at fixed 10/30/60-minute windows — how much of the outcome is just the clock."
          />
          <div className="grid-2">
            <AggTable
              title="Upper-band pullback (the cut)"
              data={data.aggregates.upper_band_pullback ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="Pullback-from-above onto a developing POC/VAH while price holds NY VWAP +1σ..+2σ, scored at 30m with medians. Judge every row against the null baseline: beat its reject rate AND show MFE/MAE asymmetry, or it's noise."
            />
            <AggTable
              title="By band context"
              data={data.aggregates.by_band_context ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="All touches grouped by where price sat in the NY VWAP bands, per approach side — the same level means different things in different bands."
            />
          </div>
          <div className="grid-2">
            <AggTable
              title="Who closed the gap"
              data={data.aggregates.who_closed_gap ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="Over the last 5 bars before each touch: did price move to the level, or the level to price? A falling band chased by price scores as a fresh 1st touch while price tested nothing — if level-led rows sit at the null, they dilute every touch table."
            />
            <AggTable
              title="Acceptance decay"
              data={data.aggregates.acceptance_decay ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="The same touches by how many times the zone was already tested today. Read Med MFE down the rows — a level touched again and again with shrinking excursion has become fair price, even while it still 'rejects' by the 3-pt threshold."
            />
          </div>
          <div className="grid-2">
            <AggTable
              title="Time in band · NY VWAP"
              data={withBandTotals(data.aggregates.band_occupancy ?? [])}
              columns={bandOccupancyColumns}
              keepOrder
              caption="How long price spent in each NY VWAP band, tallied per RTH minute. Rows read top-to-bottom like the chart (>+2σ down to <-2σ). Avg min/session is the comparable read — the total scales with the date range."
            />
            <AggTable
              title="Time in band · Globex (ON) VWAP"
              data={withBandTotals(data.aggregates.band_occupancy_gx ?? [])}
              columns={bandOccupancyColumns}
              keepOrder
              caption="The same per-minute tally against the overnight-anchored Globex VWAP bands. Empty unless 'globex' is among the sources."
            />
          </div>
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
                title="VA-snap → reversion (fade)"
                data={data.aggregates.vasnap_reversion}
                columns={vasnapColumns}
                caption="After a level snapped across price, did price revert to NY VWAP. Rates exclude trivial snaps (already at/through VWAP at the snap bar)."
              />
              <AggTable
                title="VA-snap → continuation (flip)"
                data={data.aggregates.vasnap_continuation ?? []}
                columns={vasnapContColumns}
                caption="The same snaps traded with the snap direction, stop = a close through NY VWAP. Hold % = never stopped within the window; run = excursion before the stop."
              />
            </div>
          </div>
          <div className="grid-2">
            <AggTable
              title="VA-snap by class"
              data={data.aggregates.vasnap_by_class ?? []}
              columns={vasnapColumns}
              caption="The reversion trade cut by snap class: boundary creep (jump < 20 pts) vs node-flip (the value area re-seating on a different volume node). A 4-pt creep and a 195-pt flip are different events."
            />
            <AggTable
              title="VA-snap confluence"
              data={data.aggregates.vasnap_confluence ?? []}
              columns={vasnapColumns}
              caption="Lone snaps vs same-minute multi-level snaps. Two boundaries re-seating in the same minute is one value-migration event, not two independent signals."
            />
          </div>

          <div className="grid-2">
            <AggTable
              title="Regime mix"
              data={regimeMix}
              columns={regimeMixColumns}
              keepOrder
              caption="Day-type breakdown across the run's sessions — the same per-day regime class the Strategies tab colours its calendar by."
            />
            <AggTable
              title="Regime by weekday"
              data={weekdayMix.rows}
              columns={weekdayCols}
              keepOrder
              caption="The same day-types cross-cut by weekday (Mon–Fri) — does the window lean trendy early in the week, balanced on Fridays, etc."
            />
          </div>

          {/* per-day drill-down */}
          <div className="panel">
            <div className="section-cap">Sessions</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {dayList.map((d) => {
                const klass = regimeByDay.get(d);
                return (
                  <button
                    key={d}
                    type="button"
                    className={d === selectedDay ? "chip selected" : "chip"}
                    onClick={() => setSelectedDay(d === selectedDay ? null : d)}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                  >
                    {klass && <RegimeBadge klass={klass} dot />}
                    {d} · {data.day_index[d].n_touches}t/{data.day_index[d].n_snaps}s
                  </button>
                );
              })}
            </div>
          </div>

          {selectedDay && (
            <>
              <div className="panel">
                <div
                  className="section-cap"
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    {regimeByDay.get(selectedDay) && (
                      <RegimeBadge klass={regimeByDay.get(selectedDay)!} />
                    )}
                    <span>
                      {selectedDay} — session · green/red dots = touch reject/accept (ringed = 2+
                      sources stacked), triangles = VA-snaps
                    </span>
                  </span>
                  <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                    {([["1m", null], ["500t", 500]] as const).map(([label, n]) => (
                      <button
                        key={label}
                        type="button"
                        className={chartTicks === n ? "chip selected" : "chip"}
                        onClick={() => setChartTicks(n)}
                        title={n == null ? "1-minute candles" : `${n}-tick bars`}
                      >
                        {label}
                      </button>
                    ))}
                  </span>
                </div>
                <DayChart
                  symbol={data.symbol}
                  day={selectedDay}
                  binSize={committed?.bin_size}
                  sources={committed?.sources}
                  ticksPerBar={chartTicks ?? undefined}
                  touches={dayTouches}
                  vaSnaps={daySnaps}
                />
              </div>
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
