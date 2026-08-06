// The practice record: every Simulator sitting that was ever traded, pooled.
//
// Two things it is careful about, both of them lessons this repo paid for on
// the research side:
//
//   - the headline is an *interval*, not a point. A win rate over 14 trades is
//     a coin flip with an opinion, and the KPI says so rather than printing a
//     confident 64%;
//   - the pooled numbers are recomputed from the summed totals, never averaged
//     from the per-attempt ones (see lib/replayStats). Averaging ratios across
//     sittings of different sizes is a different — and wrong — number.
//
// The default sample is every *finished* attempt, do-overs included. That is
// deliberate and it is flattering: rewinding out of a stop and re-taking the
// setup is recorded, flagged, and still counted here, because the alternative
// (silently dropping half the history) hides more than it fixes. The "clean
// only" toggle is the honest cut, one click away.

import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { KpiGrid } from "../components/KpiGrid";
import {
  useDeleteReplayAttempt,
  useReplayAttemptDetail,
  useReplayAttempts,
  type AttemptRow,
} from "../hooks/useReplays";
import { MIN_SAMPLE, pool } from "../lib/replayStats";
import { palette, toneOf } from "../theme";

const fmtUsd = (v: number | null | undefined) =>
  v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(0)}%`);
const fmtR = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`;

/** When the sitting happened (not the session it replayed) — local, short. */
const fmtWhen = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

const isClean = (a: AttemptRow) => !a.rewinds?.length;

/**
 * Cumulative net across the sample, oldest first.
 *
 * A hand-rolled polyline rather than a charting library: it is one series of a
 * few dozen points with no axes to speak of, and the one library this app has
 * loaded draws price on a time scale — which this is not (the x axis here is
 * attempt order, not time).
 */
function EquityLine({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 640;
  const h = 90;
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - ((v - lo) / span) * h;
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 90 }}>
      <line x1={0} x2={w} y1={y(0)} y2={y(0)} stroke={palette.grid} strokeWidth={1} />
      <polyline
        points={pts}
        fill="none"
        stroke={last >= 0 ? palette.green : palette.red}
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** One attempt's trades, opened from the table. Fetched on demand — the list
 *  endpoint carries summaries only, and a year of blotters is not a payload the
 *  history page should be paying for on mount. */
function AttemptTrades({ id }: { id: string }) {
  const q = useReplayAttemptDetail(id);
  if (q.isLoading) return <div style={{ color: palette.muted, fontSize: 12 }}>loading…</div>;
  const d = q.data;
  if (!d) return null;
  const rows = [
    ...d.trades.map((t) => ({ t, kept: true })),
    ...d.discarded.map((t) => ({ t, kept: false })),
  ];
  if (!rows.length) return <div style={{ color: palette.muted, fontSize: 12 }}>No trades.</div>;
  return (
    <table className="data-table" style={{ fontSize: 12, width: "100%" }}>
      <thead>
        <tr>
          <th>#</th>
          <th>Side</th>
          <th>Entry</th>
          <th>Exit</th>
          <th>Why</th>
          <th style={{ textAlign: "right" }}>Pts</th>
          <th style={{ textAlign: "right" }}>R</th>
          <th style={{ textAlign: "right" }}>P&L</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ t, kept }, i) => (
          <tr key={`${kept ? "k" : "d"}${i}`} style={{ opacity: kept ? 1 : 0.45 }}>
            <td>{kept ? t.id : "—"}</td>
            <td style={{ color: t.side === "long" ? palette.green : palette.red }}>
              {t.side === "long" ? "L" : "S"}×{t.size}
            </td>
            <td style={{ fontFamily: "monospace" }}>{t.entryPrice.toFixed(2)}</td>
            <td style={{ fontFamily: "monospace" }}>{t.exitPrice.toFixed(2)}</td>
            <td style={{ color: palette.muted }}>
              {kept ? t.reason : <span title="A rewind erased this trade — kept, not counted">rewound out</span>}
            </td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{t.pts.toFixed(2)}</td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{fmtR(t.rCash)}</td>
            <td
              style={{
                textAlign: "right",
                fontFamily: "monospace",
                color: t.pnl >= 0 ? palette.green : palette.red,
              }}
            >
              {fmtUsd(t.pnl)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReplayHistory() {
  const q = useReplayAttempts();
  const del = useDeleteReplayAttempt();
  const [cleanOnly, setCleanOnly] = useState(false);
  const [includeUnfinished, setIncludeUnfinished] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const all = useMemo(() => q.data?.attempts ?? [], [q.data]);

  const sample = useMemo(
    () =>
      all.filter((a) => {
        if (!includeUnfinished && a.status !== "finished") return false;
        if (cleanOnly && !isClean(a)) return false;
        // An attempt with no trades in it says nothing about anything.
        return (a.summary?.trades ?? 0) > 0;
      }),
    [all, cleanOnly, includeUnfinished],
  );

  const sampleIds = useMemo(() => new Set(sample.map((a) => a.id)), [sample]);
  const totals = useMemo(() => pool(sample.map((a) => a.summary ?? {})), [sample]);
  // Oldest first: a track record reads forward.
  const equity = useMemo(() => {
    let run = 0;
    return sample
      .slice()
      .reverse()
      .map((a) => (run += a.summary?.net_usd ?? 0));
  }, [sample]);

  const thin = totals.trades < MIN_SAMPLE;
  const cards = [
    {
      label: "Net",
      value: fmtUsd(totals.net_usd),
      tone: toneOf(totals.net_usd),
      sub: `${sample.length} attempt${sample.length === 1 ? "" : "s"} · ${totals.trades} trades`,
      hero: true,
    },
    {
      label: "Win rate",
      value: fmtPct(totals.win_rate),
      tone: "neutral" as const,
      sub:
        totals.win_rate_lo != null
          ? `95% CI ${fmtPct(totals.win_rate_lo)}–${fmtPct(totals.win_rate_hi)}`
          : "no trades yet",
    },
    {
      label: "Expectancy",
      value: fmtUsd(totals.expectancy_usd),
      tone: toneOf(totals.expectancy_usd),
      sub: `${totals.expectancy_points != null ? totals.expectancy_points.toFixed(2) : "—"} pts/trade`,
    },
    {
      label: "Stake R",
      value: fmtR(totals.n_with_r ? totals.net_r : null),
      tone: toneOf(totals.n_with_r ? totals.net_r : null),
      sub:
        totals.n_with_r < totals.trades
          ? `over ${totals.n_with_r}/${totals.trades} with a stop`
          : `${fmtR(totals.expectancy_r)}/trade`,
    },
    {
      label: "Profit factor",
      value: totals.profit_factor != null ? totals.profit_factor.toFixed(2) : "—",
      tone: toneOf(totals.profit_factor != null ? totals.profit_factor - 1 : null),
      sub: `${totals.wins}W / ${totals.losses}L${totals.scratches ? ` / ${totals.scratches}=` : ""}`,
    },
  ];

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          Replay history
        </h2>
        <Link to="/charts/replay" style={{ color: palette.muted, fontSize: 13 }}>
          ← Simulator
        </Link>
        <label style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: palette.muted }}>
          <input type="checkbox" checked={cleanOnly} onChange={(e) => setCleanOnly(e.target.checked)} style={{ margin: 0 }} />
          <span title="Drop attempts where a rewind erased a fill — the sample where you never saw the answer first">
            Clean only
          </span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: palette.muted }}>
          <input
            type="checkbox"
            checked={includeUnfinished}
            onChange={(e) => setIncludeUnfinished(e.target.checked)}
            style={{ margin: 0 }}
          />
          <span title="Attempts you walked away from without ending. Their tail is missing, so they are out of the record by default.">
            Include unfinished
          </span>
        </label>
      </div>

      {q.isLoading && <div style={{ color: palette.muted }}>loading…</div>}
      {!q.isLoading && !all.length && (
        <div style={{ color: palette.muted }}>
          Nothing recorded yet. An attempt is written from your first fill in the{" "}
          <Link to="/charts/replay">Simulator</Link>.
        </div>
      )}

      {!!all.length && (
        <>
          <KpiGrid cards={cards} template="1.4fr 1fr 1fr 1fr 1fr" />
          {thin && (
            <div style={{ color: palette.orange, fontSize: 12, margin: "8px 0" }}>
              {totals.trades} trades — under {MIN_SAMPLE}, so read the interval, not the rate. Nothing
              here is distinguishable from luck yet.
            </div>
          )}

          {equity.length > 1 && (
            <div className="panel" style={{ marginTop: 12 }}>
              <div style={{ color: palette.muted, fontSize: 12, marginBottom: 4 }}>
                Cumulative net, attempt by attempt
              </div>
              <EquityLine values={equity} />
            </div>
          )}

          <table className="data-table" style={{ marginTop: 16, width: "100%" }}>
            <thead>
              <tr>
                <th>Sat</th>
                <th>Session</th>
                <th></th>
                <th style={{ textAlign: "right" }}>Trades</th>
                <th style={{ textAlign: "right" }}>Win%</th>
                <th style={{ textAlign: "right" }}>R</th>
                <th style={{ textAlign: "right" }}>Net</th>
                <th>Note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {all.map((a) => {
                const s = a.summary ?? {};
                const inSample = sampleIds.has(a.id);
                return (
                  <Fragment key={a.id}>
                    <tr
                      onClick={() => setOpen(open === a.id ? null : a.id)}
                      style={{ cursor: "pointer", opacity: inSample ? 1 : 0.5 }}
                    >
                      <td style={{ whiteSpace: "nowrap" }}>{fmtWhen(a.created_at)}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {a.date} · {a.symbol}
                      </td>
                      <td style={{ whiteSpace: "nowrap", fontSize: 11 }}>
                        {a.status !== "finished" && (
                          <span style={{ color: palette.muted }} title="Never ended — the tail is missing">
                            {a.status}{" "}
                          </span>
                        )}
                        {!isClean(a) && (
                          <span
                            style={{ color: palette.orange }}
                            title={`${a.rewinds.length} rewind(s) past a fill · ${a.discarded_trades} trade(s) erased`}
                          >
                            do-over{" "}
                          </span>
                        )}
                        {a.repeat_index > 0 && (
                          <span style={{ color: palette.muted }} title="You had replayed this session before">
                            #{a.repeat_index + 1}
                          </span>
                        )}
                      </td>
                      <td style={{ textAlign: "right" }}>{s.trades ?? 0}</td>
                      <td style={{ textAlign: "right" }}>{fmtPct(s.win_rate)}</td>
                      <td style={{ textAlign: "right", fontFamily: "monospace" }}>
                        {s.n_with_r ? fmtR(s.net_r) : "—"}
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "monospace",
                          color: (s.net_usd ?? 0) >= 0 ? palette.green : palette.red,
                        }}
                      >
                        {fmtUsd(s.net_usd)}
                      </td>
                      <td style={{ color: palette.muted, fontSize: 12, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {a.note}
                      </td>
                      <td>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (confirm(`Delete this attempt (${a.date} · ${a.symbol})? The log and its trades go with it.`))
                              del.mutate(a.id);
                          }}
                          title="Delete this attempt"
                          style={{ background: "none", border: "none", color: palette.muted, cursor: "pointer" }}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                    {open === a.id && (
                      <tr>
                        <td colSpan={9} style={{ background: palette.bg2 }}>
                          <AttemptTrades id={a.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
