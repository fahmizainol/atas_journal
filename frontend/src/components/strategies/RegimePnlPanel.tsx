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
import { useRegimeRange } from "../../hooks/useRegime";
import { axisProps, gridProps, tooltipStyle } from "../charts/chartTheme";
import { fmt, fmtPct } from "../../lib/format";
import { palette, regimePalette } from "../../theme";
import {
  CHECKPOINTS,
  CLASS_LABEL,
  KPI_OPTIONS,
  type Checkpoint,
  type RegimeClass,
  type RegimeKpis,
} from "../../lib/regimeTypes";
import {
  expectedFalsePositives,
  luckThreshold,
  rankCorr,
  score,
  type DayPoint,
  type Score,
} from "../../lib/regimeStats";
import type { SimTrade } from "../../lib/strategyTypes";

interface Point extends DayPoint {
  klass: RegimeClass;
  partial: boolean;
}

interface Bucket {
  label: string;
  color?: string;
  days: Point[];
}

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const tone = (n: number) => (n >= 0 ? "pos" : "neg");
const th = { cursor: "default" as const };

/** Split into thirds by KPI value. Terciles rather than fixed cutoffs: the KPIs
 * are on incompatible scales (a ratio, a rate, a σ), so a threshold that means
 * something for ABR means nothing for the spread — and ranking can't manufacture
 * a band that isn't in the data. */
function terciles(points: Point[], fmtX: (v: number) => string): Bucket[] {
  const sorted = [...points].sort((a, b) => a.x - b.x);
  const n = sorted.length;
  if (n < 6) return [{ label: "all days", days: sorted }];
  const cuts = [0, Math.floor(n / 3), Math.floor((2 * n) / 3), n];
  return ["low", "mid", "high"].map((name, i) => {
    const days = sorted.slice(cuts[i], cuts[i + 1]);
    return {
      label: `${name} · ${fmtX(days[0].x)} – ${fmtX(days[days.length - 1].x)}`,
      days,
    };
  });
}

function BucketTable({ buckets, head }: { buckets: Bucket[]; head: string }) {
  return (
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
        {buckets.map((b) => {
          const net = sum(b.days.map((d) => d.net));
          const trades = sum(b.days.map((d) => d.trades));
          const wins = sum(b.days.map((d) => d.wins));
          return (
            <tr key={b.label}>
              <td>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {b.color && (
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: 3,
                        background: b.color,
                        flex: "0 0 auto",
                      }}
                    />
                  )}
                  {b.label}
                </span>
              </td>
              <td>{b.days.length}</td>
              <td className={tone(net)} style={{ fontWeight: 600 }}>
                {fmt(net)}
              </td>
              <td className={tone(net)}>{b.days.length ? fmt(net / b.days.length) : "—"}</td>
              <td>{trades}</td>
              <td>{trades ? fmtPct((wins / trades) * 100, 0) : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// Which regime KPIs track this run's P&L — every KPI at one checkpoint, ranked by
// how much a day in its top third pays over a day in its bottom third.
//
// The `luck` column is the point of this table, not the ranking. With fifteen KPIs
// on the board, the best-looking one is worth nothing on its own: something always
// wins. So each row is scored against the same statistic computed on shuffled P&L,
// and the ones that a coin-flip would have produced this often get called out as
// noise rather than quietly sorted to the top and believed.
function Leaderboard({
  rows,
  cp,
  selected,
  onPick,
}: {
  rows: { key: keyof RegimeKpis; label: string; s: Score }[];
  cp: Checkpoint;
  selected: keyof RegimeKpis;
  onPick: (k: keyof RegimeKpis) => void;
}) {
  const bar = luckThreshold(rows.length);
  const survivors = rows.filter((r) => r.s.luck <= bar).length;
  const expected = expectedFalsePositives(rows.length);

  return (
    <>
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
          {rows.map(({ key, label, s }) => {
            const solid = s.luck <= bar;
            return (
              <tr
                key={key}
                onClick={() => onPick(key)}
                className={key === selected ? "selected" : undefined}
                style={{ cursor: "pointer", opacity: solid ? 1 : 0.55 }}
                title="Show this KPI's bands below"
              >
                <td>{label}</td>
                <td>{s.days}</td>
                <td className={tone(s.rho)}>
                  {s.rho >= 0 ? "+" : ""}
                  {s.rho.toFixed(2)}
                </td>
                <td className={tone(s.edge)} style={{ fontWeight: 600 }}>
                  {s.edge >= 0 ? "+" : ""}
                  {fmt(s.edge)}
                </td>
                <td className={tone(s.winEdge)}>
                  {s.winEdge >= 0 ? "+" : ""}
                  {s.winEdge.toFixed(0)} pts
                </td>
                <td>
                  {solid ? (
                    <span className="badge badge-sm" title="Survives the multiple-testing bar">
                      holds
                    </span>
                  ) : (
                    <span className="muted" title="Shuffled P&L produces this often — treat as noise">
                      {s.luck < 0.01 ? "<1%" : `${Math.round(s.luck * 100)}%`}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="section-cap" style={{ marginTop: 6 }}>
        Ranked by what a day in the KPI's top third pays over one in its bottom third — click a row
        to see its bands. <strong>“Luck”</strong> is how often shuffled P&amp;L produces a
        correlation this strong: with {rows.length} KPIs on the board, roughly{" "}
        {expected.toFixed(1)} of them should clear a plain 1-in-20 bar{" "}
        <em>by chance alone</em>, so the “holds” badge uses a stricter bar ({(bar * 100).toFixed(1)}
        %) that accounts for how many were tried. {survivors === 0
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

// Does the day's regime explain the day's P&L?
//
// Tables, not a plot, by default: at ~30 sessions a scatter is a cloud you squint
// at, while "these ten days made 13k and those twenty-one lost 4k" is the sentence
// you want out of it. The scatter is a click away — it's the better tool once
// there are enough days for a shape to exist.
//
// The checkpoint picker is the point of the whole panel. A KPI read at `eod`
// correlating with the day's P&L proves nothing tradeable — both are computed from
// the same session. A KPI read at 09:45 that still separates the winners is a
// signal you could have acted on, because it was knowable before the day resolved.
function Body({
  symbol,
  trades,
  start,
  end,
  onHide,
}: {
  symbol: string;
  trades: SimTrade[];
  start: string;
  end: string;
  onHide: () => void;
}) {
  const [kpi, setKpi] = useState<keyof RegimeKpis>("abr");
  const [cp, setCp] = useState<Checkpoint>("10:30");
  const [chart, setChart] = useState(false);
  const { data, isLoading } = useRegimeRange(symbol, start, end);

  const spec = KPI_OPTIONS.find((k) => k.key === kpi)!;
  const fmtX = (v: number) => (spec.pct ? `${(v * 100).toFixed(0)}%` : v.toFixed(2));

  // Same bucketing as the by-day view's dayStats: a trade belongs to the session
  // it entered in.
  const netByDay = useMemo(() => {
    const by = new Map<string, { net: number; n: number; wins: number }>();
    for (const t of trades) {
      const d = t.session.slice(0, 10);
      const s = by.get(d) ?? { net: 0, n: 0, wins: 0 };
      s.net += t.net_pnl;
      s.n += 1;
      if (t.net_pnl > 0) s.wins += 1;
      by.set(d, s);
    }
    return by;
  }, [trades]);

  // Days the run covered but never traded are deliberately left out: a zero from
  // "no setup armed" and a zero from "traded flat" are different facts, and folding
  // both into a band would dilute whichever regime produces the fewest signals —
  // which is exactly the regime you are trying to detect.
  const pointsFor = useMemo(
    () =>
      (key: keyof RegimeKpis, at: Checkpoint): Point[] => {
        const out: Point[] = [];
        for (const d of data?.days ?? []) {
          const v = d.checkpoints[at]?.[key];
          const s = netByDay.get(d.date);
          if (v == null || !s) continue;
          out.push({
            date: d.date,
            x: v,
            net: s.net,
            trades: s.n,
            wins: s.wins,
            klass: d.class,
            partial: d.partial,
          });
        }
        return out;
      },
    [data, netByDay],
  );

  // Every KPI scored at once — this is the answer to "which of these fifteen do I
  // even bother looking at". Permutation-scored, so it's ~500 shuffles x 15 KPIs
  // over ~30 days: cheap enough to just do, and memoized on the checkpoint anyway.
  const board = useMemo(() => {
    const rows = KPI_OPTIONS.map((o) => ({
      key: o.key,
      label: o.label,
      s: score(pointsFor(o.key, cp)),
    })).filter((r): r is { key: keyof RegimeKpis; label: string; s: Score } => r.s != null);
    return rows.sort((a, b) => Math.abs(b.s.edge) - Math.abs(a.s.edge));
  }, [pointsFor, cp]);

  const points = useMemo(() => pointsFor(kpi, cp), [pointsFor, kpi, cp]);

  const classBuckets = useMemo<Bucket[]>(() => {
    const by = new Map<RegimeClass, Point[]>();
    for (const p of points) by.set(p.klass, [...(by.get(p.klass) ?? []), p]);
    return [...by.entries()]
      .map(([k, days]) => ({ label: CLASS_LABEL[k], color: regimePalette.klass[k], days }))
      .sort((a, b) => sum(b.days.map((d) => d.net)) - sum(a.days.map((d) => d.net)));
  }, [points]);

  const kpiBuckets = useMemo(() => terciles(points, fmtX), [points, fmtX]);
  const rho = useMemo(
    () => (points.length >= 4 ? rankCorr(points.map((p) => p.x), points.map((p) => p.net)) : null),
    [points],
  );

  if (isLoading || !data)
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <Header open onToggle={onHide} />
        <div className="notice" style={{ marginTop: 12 }}>
          {isLoading ? "Loading regime…" : "No regime for this window."}
        </div>
      </div>
    );

  const untraded = data.days.length - new Set(points.map((p) => p.date)).size;
  const scatter = points.map((p) => ({ ...p, x: spec.pct ? p.x * 100 : p.x }));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <Header open onToggle={onHide}>
        <span className="muted" style={{ fontSize: 12 }}>
          knowable at
        </span>
        <span style={{ display: "flex", gap: 2 }}>
          {CHECKPOINTS.map((c) => (
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

      {board.length === 0 ? (
        <div className="notice">
          Not enough traded sessions with a regime at {cp} to rank anything
          {cp === "09:30" && " — at the bell only the overnight priors exist"}.
        </div>
      ) : (
        <>
          <Leaderboard rows={board} cp={cp} selected={kpi} onPick={setKpi} />

          <div style={{ display: "grid", gap: 18, marginTop: 20 }}>
            <div>
              <div className="section-cap" style={{ marginBottom: 4 }}>
                {spec.label} @ {cp} — days split into thirds, weakest first
              </div>
              {chart ? (
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
                        const p = payload[0].payload as Point;
                        return (
                          <div style={tooltipStyle.contentStyle}>
                            <div style={{ padding: "6px 10px" }}>
                              <div>
                                <b>{p.date}</b> · {CLASS_LABEL[p.klass]}
                                {p.partial && " · partial"}
                              </div>
                              <div style={{ color: palette.muted }}>
                                {spec.label}: {spec.pct ? `${p.x.toFixed(0)}%` : p.x.toFixed(2)} @{" "}
                                {cp}
                              </div>
                              <div style={{ color: p.net >= 0 ? palette.green : palette.red }}>
                                {fmt(p.net)} · {p.trades} trade{p.trades === 1 ? "" : "s"}
                              </div>
                            </div>
                          </div>
                        );
                      }}
                    />
                    <Scatter data={scatter} isAnimationActive={false}>
                      {scatter.map((p) => (
                        <Cell key={p.date} fill={p.net >= 0 ? palette.green : palette.red} />
                      ))}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              ) : (
                <BucketTable buckets={kpiBuckets} head={`${spec.label} band`} />
              )}
              {rho != null && !chart && (
                <div className="section-cap" style={{ marginTop: 6 }}>
                  Rank correlation with the day's net:{" "}
                  <strong>
                    {rho >= 0 ? "+" : ""}
                    {rho.toFixed(2)}
                  </strong>{" "}
                  over {points.length} traded days. Read the spread between the bands, not this
                  number — at this sample size it moves a lot on one big day.
                </div>
              )}
            </div>

            <div>
              <div className="section-cap" style={{ marginBottom: 4 }}>
                By regime class — always the end-of-day call, whatever checkpoint is picked above
              </div>
              <BucketTable buckets={classBuckets} head="Regime" />
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
        {untraded > 0 &&
          `${untraded} session${untraded === 1 ? "" : "s"} in range never traded and ${untraded === 1 ? "is" : "are"} left out — a day with no setup is not a flat day. `}
        {data.skipped.length > 0 &&
          `${data.skipped.length} session${data.skipped.length === 1 ? " has" : "s have"} no cached ticks and no regime at all.`}
      </div>
    </div>
  );
}

// Closed by default, and the body is *unmounted* rather than hidden — the regime
// range is a per-session fetch (and ~500 shuffles x 15 KPIs of scoring on top), and
// a study nobody opened should not pay for either. Mounting Body is what asks for
// the data, so opening the panel is what buys it.
export function RegimePnlPanel(props: {
  symbol: string;
  trades: SimTrade[];
  start: string;
  end: string;
}) {
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

  return <Body {...props} onHide={() => setOpen(false)} />;
}
