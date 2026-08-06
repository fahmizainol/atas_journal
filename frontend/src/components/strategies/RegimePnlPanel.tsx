import { useMemo, useState, type ReactNode } from "react";
import {
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useRegimePnl, useRegimeRange } from "../../hooks/useRegime";
import { axisProps, gridProps, tooltipStyle } from "../charts/chartTheme";
import { fmt, fmtPct } from "../../lib/format";
import { palette, regimePalette } from "../../theme";
import {
  CLASS_LABEL,
  type Board,
  type BoardRow,
  type Checkpoint,
  type KpiSpec,
  type RegimeClass,
  type RegimeKpis,
  type RegimeStudy,
} from "../../lib/regimeTypes";

const tone = (n: number) => (n >= 0 ? "pos" : "neg");
const th = { cursor: "default" as const };
const sign = (n: number, digits = 0) => `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;

/** A row of the two summary tables. Both are computed server-side; this is only
 * how they are printed. */
interface Row {
  label: string;
  color?: string;
  days: number;
  net: number;
  avgNet: number | null;
  trades: number;
  winRate: number | null;
}

function SummaryTable({ rows, head }: { rows: Row[]; head: string }) {
  return (
    <div className="table-scroll-x">
      <table className="data-table">
        <thead>
          <tr>
            <th style={th}>{head}</th>
            <th style={th}>Days</th>
            <th style={th}>Net</th>
            <th style={th}>Avg / day</th>
            <th style={th}>Trades</th>
            <th style={th}>Win rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {r.color && (
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: 3,
                        background: r.color,
                        flex: "0 0 auto",
                      }}
                    />
                  )}
                  {r.label}
                </span>
              </td>
              <td>{r.days}</td>
              <td className={tone(r.net)} style={{ fontWeight: 600 }}>
                {fmt(r.net)}
              </td>
              <td className={tone(r.net)}>{r.avgNet == null ? "—" : fmt(r.avgNet)}</td>
              <td>{r.trades}</td>
              <td>{r.winRate == null ? "—" : fmtPct(r.winRate, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Which regime KPIs track this run's P&L — every KPI at one checkpoint, ranked by
// how much a day in its top third pays over a day in its bottom third.
//
// The `luck` column is the point of this table, not the ranking. With ~20 KPIs on
// the board the best-looking one is worth nothing on its own: something always
// wins. So each row is scored against the same statistic computed on shuffled P&L,
// and the ones a coin-flip would have produced this often are called out as noise
// rather than quietly sorted to the top and believed.
function Leaderboard({
  board,
  cp,
  selected,
  onPick,
}: {
  board: Board;
  cp: Checkpoint;
  selected: keyof RegimeKpis;
  onPick: (k: keyof RegimeKpis) => void;
}) {
  const { rows, luck_bar: bar, holds: survivors, expected_false_positives: expected } = board;

  return (
    <>
      <div className="table-scroll-x">
        <table className="data-table">
          <thead>
            <tr>
              <th style={th}>KPI @ {cp}</th>
              <th style={th}>Days</th>
              <th style={th} title="Spearman rank correlation with the day's net">
                ρ
              </th>
              <th style={th} title="Avg net of a day in the KPI's top third, minus one in its bottom third">
                Top ⅓ − bottom ⅓ / day
              </th>
              <th style={th} title="Win-rate points, top third minus bottom third">
                Win rate Δ
              </th>
              <th style={th} title="How often shuffled P&L produces a correlation this strong">
                Luck
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.key}
                onClick={() => onPick(r.key)}
                className={r.key === selected ? "selected" : undefined}
                style={{ cursor: "pointer", opacity: r.holds ? 1 : 0.55 }}
                title="Show this KPI's bands below"
              >
                <td>{r.label}</td>
                <td>{r.days}</td>
                <td className={tone(r.rho)}>{sign(r.rho, 2)}</td>
                <td className={tone(r.edge)} style={{ fontWeight: 600 }}>
                  {r.edge >= 0 ? "+" : ""}
                  {fmt(r.edge)}
                </td>
                <td className={tone(r.win_edge)}>{sign(r.win_edge)} pts</td>
                <td>
                  {r.holds ? (
                    <span className="badge badge-sm" title="Survives the multiple-testing bar">
                      holds
                    </span>
                  ) : (
                    <span className="muted" title="Shuffled P&L produces this often — treat as noise">
                      {r.luck < 0.01 ? "<1%" : `${Math.round(r.luck * 100)}%`}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="section-cap" style={{ marginTop: 6 }}>
        Ranked by what a day in the KPI's top third pays over one in its bottom third — click a row
        to see its bands. <strong>“Luck”</strong> is how often shuffled P&amp;L produces a
        correlation this strong: with {rows.length} KPIs on the board, roughly{" "}
        {expected.toFixed(1)} of them should clear a plain 1-in-20 bar <em>by chance alone</em>, so
        the “holds” badge uses a stricter bar ({(bar * 100).toFixed(1)}%) that accounts for how many
        were tried.{" "}
        {survivors === 0
          ? "Nothing clears it here — which means every ordering below is within what noise produces, and none of it should be traded on yet."
          : `${survivors} clear${survivors === 1 ? "s" : ""} it. Dimmed rows are the ones that don't.`}
      </div>
    </>
  );
}

function Header({
  open,
  onToggle,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  children?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        alignItems: "center",
        flexWrap: "wrap",
        marginBottom: open ? 12 : 0,
      }}
    >
      <button
        type="button"
        className="btn-xs"
        onClick={onToggle}
        title={open ? "Hide the regime study" : "Show the regime study"}
      >
        {open ? "▾" : "▸"}
      </button>
      <h3 style={{ margin: 0 }}>Regime vs P&amp;L</h3>
      {children}
    </div>
  );
}

// The scatter's one job is to show the shape the tables summarise, so it needs a
// point per day — which the study payload deliberately does not carry: 21 KPIs x 5
// checkpoints x every session would duplicate the whole regime cache into every
// run's snapshot. It comes from the shared /regime range query instead (already
// cached, keyed by symbol and window rather than by run), joined to the day's net
// from the study. A join for *plotting*; every number is still the server's.
function Chart({
  study,
  spec,
  cp,
}: {
  study: RegimeStudy;
  spec: KpiSpec;
  cp: Checkpoint;
}) {
  const { data } = useRegimeRange(study.symbol, study.start, study.end);

  const points = useMemo(() => {
    const byDate = new Map(study.days.map((d) => [d.date, d]));
    const out = [];
    for (const r of data?.days ?? []) {
      const v = r.checkpoints[cp]?.[spec.key];
      const d = byDate.get(r.date);
      if (v == null || !d) continue;
      out.push({ ...d, x: spec.pct ? v * 100 : v, raw: v });
    }
    return out;
  }, [data, study.days, spec, cp]);

  if (!data) return <div className="notice">Loading the day-by-day values…</div>;

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid {...gridProps} />
        <XAxis
          type="number"
          dataKey="x"
          name={spec.label}
          {...axisProps}
          tickFormatter={(v: number) => (spec.pct ? `${v.toFixed(0)}%` : v.toFixed(2))}
        />
        <YAxis
          type="number"
          dataKey="net"
          name="Net"
          {...axisProps}
          width={64}
          tickFormatter={(v: number) => fmt(v)}
        />
        <ZAxis range={[70, 70]} />
        {/* Breakeven: dots above it are the days the model worked. */}
        <ReferenceLine y={0} stroke={palette.muted} strokeDasharray="3 3" />
        <Tooltip
          {...tooltipStyle}
          cursor={{ strokeDasharray: "3 3" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const p = payload[0].payload as (typeof points)[number];
            return (
              <div style={tooltipStyle.contentStyle}>
                <div style={{ padding: "6px 10px" }}>
                  <div>
                    <b>{p.date}</b> · {CLASS_LABEL[p.class]}
                    {p.partial && " · partial"}
                  </div>
                  <div style={{ color: palette.muted }}>
                    {spec.label}: {spec.pct ? `${p.x.toFixed(0)}%` : p.raw.toFixed(2)} @ {cp}
                  </div>
                  <div style={{ color: p.net >= 0 ? palette.green : palette.red }}>
                    {fmt(p.net)} · {p.trades} trade{p.trades === 1 ? "" : "s"}
                  </div>
                </div>
              </div>
            );
          }}
        />
        <Scatter data={points} isAnimationActive={false}>
          {points.map((p) => (
            <Cell key={p.date} fill={p.net >= 0 ? palette.green : palette.red} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

// Does the day's regime explain the day's P&L?
//
// Every number here is computed server-side (journal.sim.regime_pnl) and read back
// from <run>/regime_pnl.json — the browser scores nothing. That is deliberate: the
// study used to exist only for as long as this component was mounted, which meant
// the one thing that could ever read the answer was a human looking at this table.
//
// Tables, not a plot, by default: at ~100 sessions a scatter is a cloud you squint
// at, while "these ten days made 13k and those twenty-one lost 4k" is the sentence
// you want out of it. The scatter is a click away — it's the better tool once
// there are enough days for a shape to exist.
//
// The checkpoint picker is the point of the whole panel. A KPI read at `eod`
// correlating with the day's P&L proves nothing tradeable — both are computed from
// the same session. A KPI read at 09:45 that still separates the winners is a
// signal you could have acted on, because it was knowable before the day resolved.
function Body({ slug, runId, onHide }: { slug: string; runId: string; onHide: () => void }) {
  const [kpi, setKpi] = useState<keyof RegimeKpis>("abr");
  const [cp, setCp] = useState<Checkpoint>("10:30");
  const [chart, setChart] = useState(false);
  const { data: study, isLoading, error } = useRegimePnl(slug, runId);

  if (isLoading || !study)
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <Header open onToggle={onHide} />
        <div className="notice" style={{ marginTop: 12 }}>
          {isLoading ? "Scoring the regime study…" : error ? "No regime study for this run." : ""}
        </div>
      </div>
    );

  const board = study.boards[cp];
  const rows: BoardRow[] = board?.rows ?? [];
  // The picker can only offer a KPI the board actually scored — and at 09:30 only
  // the overnight priors exist, so most of them aren't there.
  const row = rows.find((r) => r.key === kpi) ?? rows[0];
  const fmtX = (v: number | null) =>
    v == null ? "—" : row.pct ? `${(v * 100).toFixed(0)}%` : v.toFixed(2);

  const bandRows: Row[] = (row?.bands ?? []).map((b) => ({
    label: `${b.band} · ${fmtX(b.lo)} – ${fmtX(b.hi)}`,
    days: b.days,
    net: b.net,
    avgNet: b.avg_net,
    trades: b.trades,
    winRate: b.win_rate,
  }));

  const classRows: Row[] = study.class_buckets.map((b) => ({
    label: b.label,
    color: regimePalette.klass[b.class as RegimeClass],
    days: b.days,
    net: b.net,
    avgNet: b.avg_net,
    trades: b.trades,
    winRate: b.win_rate,
  }));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <Header open onToggle={onHide}>
        <span className="muted" style={{ fontSize: 12 }}>
          knowable at
        </span>
        <span style={{ display: "flex", gap: 2 }}>
          {study.checkpoints.map((c) => (
            <button
              key={c}
              type="button"
              className={`btn-xs${c === cp ? " btn-accent" : ""}`}
              title={
                c === "eod"
                  ? "The whole session — hindsight. Nothing here was knowable in time to trade it."
                  : `Only what had happened by ${c} ET`
              }
              onClick={() => setCp(c)}
            >
              {c}
            </button>
          ))}
        </span>
        <button
          type="button"
          className="btn-xs"
          style={{ marginLeft: "auto" }}
          onClick={() => setChart((v) => !v)}
        >
          {chart ? "Show tables" : "Show scatter"}
        </button>
      </Header>

      {rows.length === 0 ? (
        <div className="notice">
          Not enough traded sessions with a regime at {cp} to rank anything
          {cp === "09:30" && " — at the bell only the overnight priors exist"}.
        </div>
      ) : (
        <>
          <Leaderboard board={board} cp={cp} selected={row.key} onPick={setKpi} />

          <div style={{ display: "grid", gap: 18, marginTop: 20 }}>
            <div>
              <div className="section-cap" style={{ marginBottom: 4 }}>
                {row.label} @ {cp} — days split into thirds, weakest first
              </div>
              {chart ? (
                <Chart study={study} spec={row} cp={cp} />
              ) : (
                <SummaryTable rows={bandRows} head={`${row.label} band`} />
              )}
              {!chart && (
                <div className="section-cap" style={{ marginTop: 6 }}>
                  Rank correlation with the day's net: <strong>{sign(row.rho, 2)}</strong> over{" "}
                  {row.days} traded days. Read the spread between the bands, not this number — at
                  this sample size it moves a lot on one big day.
                </div>
              )}
            </div>

            <div>
              <div className="section-cap" style={{ marginBottom: 4 }}>
                By regime class — always the end-of-day call, whatever checkpoint is picked above
              </div>
              <SummaryTable rows={classRows} head="Regime" />
            </div>
          </div>
        </>
      )}

      <div className="section-cap" style={{ marginTop: 10 }}>
        Every traded session in this run's window, grouped by what kind of day it was.{" "}
        {cp === "eod" ? (
          <strong>
            At “eod” the KPI is measured over the whole session, so any split you see is hindsight —
            it describes the day, it does not predict it. Pick an intraday checkpoint to ask whether
            the regime was readable in time to act on.
          </strong>
        ) : (
          <>
            At <strong>{cp}</strong> the KPI only sees what had already happened by then — so a split
            here is one you could have traded on.
          </>
        )}{" "}
        {study.untraded_days > 0 &&
          `${study.untraded_days} session${study.untraded_days === 1 ? "" : "s"} in range never traded and ${study.untraded_days === 1 ? "is" : "are"} left out — a day with no setup is not a flat day. `}
        {study.skipped.length > 0 &&
          `${study.skipped.length} session${study.skipped.length === 1 ? " has" : "s have"} no cached ticks and no regime at all.`}
      </div>
    </div>
  );
}

// Closed by default, and the body is *unmounted* rather than hidden — opening the
// panel is what asks for the study, and a run whose snapshot predates a version
// bump pays a couple of seconds to rescore on that first open. A study nobody
// opened should not pay for it. (Unlike before, the second open is free: the
// answer is a file now, not a computation.)
export function RegimePnlPanel({ slug, runId }: { slug: string; runId: string }) {
  const [open, setOpen] = useState(false);

  if (!open)
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <Header open={false} onToggle={() => setOpen(true)}>
          <span className="muted" style={{ fontSize: 12 }}>
            Does the kind of day explain the day's P&amp;L?
          </span>
        </Header>
      </div>
    );

  return <Body slug={slug} runId={runId} onHide={() => setOpen(false)} />;
}
