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
import { Link, useNavigate } from "react-router-dom";
import { KpiGrid } from "../components/KpiGrid";
import {
  useDeleteReplayAttempt,
  usePatchReplayAttempt,
  useReplayAttemptDetail,
  useReplayAttempts,
  type AttemptRow,
} from "../hooks/useReplays";
import { MIN_SAMPLE, pool } from "../lib/replayStats";
import { saveResume } from "../lib/replayResume";
import { accountIdOf } from "../lib/replayAccount";
import { pathOfAccount } from "../components/charts/AccountSwitch";

/** The history page's tabs. Still the three *worlds* rather than the account
 *  list: a drill belongs to none of them and the two built-ins are what the
 *  stored `mode` says. Kept local now that a resume scope is an account id. */
type HistoryTab = "replay" | "paper" | "drill";
import { saveReview } from "../lib/replayReview";
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

const isDrill = (a: AttemptRow) => (a.mode ?? "replay") === "drill";

/** A row's mode, narrowed to the three the app keys on. Anything else — an
 *  attempt written by a future version, say — reads as a plain replay, which is
 *  the one that owns the unmarked page. */
const modeOf = (a: AttemptRow): HistoryTab => {
  const m = a.mode ?? "replay";
  return m === "drill" || m === "paper" ? m : "replay";
};

/** The page each mode is traded on. Paper is a route under Replay rather than
 *  beside it; see the comment on it in `router.tsx`. */
const PAGE_OF: Record<HistoryTab, string> = {
  replay: "/charts/replay",
  paper: "/charts/replay/paper",
  drill: "/charts/backtest",
};

const TAB_LABEL: Record<HistoryTab, string> = {
  replay: "Replay",
  paper: "Paper",
  drill: "Backtest",
};

export function ReplayHistory() {
  const q = useReplayAttempts();
  const del = useDeleteReplayAttempt();
  const patch = usePatchReplayAttempt();
  const navigate = useNavigate();
  const [cleanOnly, setCleanOnly] = useState(false);
  const [includeUnfinished, setIncludeUnfinished] = useState(false);
  // Show only the sittings marked to come back to. Unlike the two above it is a
  // filter on the *table* and not on the KPI sample: the numbers on this page
  // are a track record, and a track record of the sittings you happened to flag
  // is a number about nothing. This one answers "where did I put that one".
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  // Which world the page shows. Replay sittings and backtest reps are records
  // of different kinds — one is priced against the account, the other is
  // deliberately unpriced — and pooling them made every number on this page a
  // number about neither. One list at a time; the KPIs, the equity line and
  // the empty state all follow the tab.
  //
  // Paper is a third for the same reason rather than a variant of the first:
  // it *is* priced, and identically, but against an account whose deaths cost
  // nothing. A track record that averages the sittings you can afford to lose
  // with the ones you cannot is a flattering number about neither.
  const [tab, setTab] = useState<HistoryTab>("replay");
  const drillTab = tab === "drill";

  const all = useMemo(
    () => (q.data?.attempts ?? []).filter((a) => modeOf(a) === tab),
    [tab, q.data],
  );

  const sample = useMemo(
    () =>
      all.filter((a) => {
        // `reviewed` is a finished sitting that has also been answered for —
        // dropping it here would empty the sample of exactly the sittings that
        // went through the account's own process.
        if (!includeUnfinished && a.status !== "finished" && a.status !== "reviewed") return false;
        if (cleanOnly && !isClean(a)) return false;
        // An attempt with no trades in it says nothing about anything.
        return (a.summary?.trades ?? 0) > 0;
      }),
    [all, cleanOnly, includeUnfinished],
  );

  const sampleIds = useMemo(() => new Set(sample.map((a) => a.id)), [sample]);
  /** What the table draws. `all` unless you asked for the flagged ones only. */
  const rows = useMemo(
    () => (flaggedOnly ? all.filter((a) => a.review_later) : all),
    [all, flaggedOnly],
  );
  const flaggedCount = useMemo(() => all.filter((a) => a.review_later).length, [all]);
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
      // Net is already after commission; the fee line says how much of the
      // distance to it the broker took. Only shown once something has paid —
      // attempts recorded under a zeroed fill model, and every attempt from
      // before there was one, total nothing here and shouldn't claim a row.
      sub:
        `${sample.length} ${drillTab ? "rep" : "attempt"}${sample.length === 1 ? "" : "s"} · ${totals.trades} trades` +
        (totals.fees_usd > 0 ? ` · ${fmtUsd(totals.fees_usd)} fees` : ""),
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
          Practice history
        </h2>
        {(["replay", "paper", "drill"] as const).map((t) => (
          <button
            key={t}
            type="button"
            data-history-tab={t}
            onClick={() => {
              setTab(t);
              setOpen(null);
            }}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              fontSize: 13,
              cursor: "pointer",
              color: tab === t ? palette.text : palette.muted,
              textDecoration: tab === t ? "underline" : "none",
            }}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
        <Link to={PAGE_OF[tab]} style={{ color: palette.muted, fontSize: 13 }}>
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
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: palette.muted }}>
          <input
            type="checkbox"
            data-flagged-only
            checked={flaggedOnly}
            onChange={(e) => setFlaggedOnly(e.target.checked)}
            style={{ margin: 0 }}
          />
          <span title="Only the sittings you marked to review later. This hides table rows and leaves the numbers above alone — they are the whole track record either way.">
            🚩 Flagged{flaggedCount > 0 ? ` (${flaggedCount})` : ""}
          </span>
        </label>
      </div>

      {q.isLoading && <div style={{ color: palette.muted }}>loading…</div>}
      {!q.isLoading && !all.length && (
        <div style={{ color: palette.muted }}>
          {drillTab ? (
            <>
              No reps yet. A rep is written the moment 🎲 drops you into a day on the{" "}
              <Link to="/charts/backtest">Backtest page</Link>.
            </>
          ) : tab === "paper" ? (
            <>
              Nothing on paper yet. Switch the account chip in the{" "}
              <Link to="/charts/replay/paper">Simulator</Link> and trade — same rules, same
              floor, and a death that costs nothing but the sitting.
            </>
          ) : (
            <>
              Nothing recorded yet. An attempt is written from your first fill in the{" "}
              <Link to="/charts/replay">Simulator</Link>.
            </>
          )}
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

          {/* The curve over sittings lives on the account now, where it is a
              curve of an *account's* equity against its own floor rather than a
              running net over whatever this tab happens to be showing. A drill
              still has none by design — it is unpriced, and a curve over reps
              would read as money that was never at stake. */}
          {!drillTab && equity.length > 1 && (
            <div style={{ color: palette.muted, fontSize: 12, margin: "8px 0" }}>
              The lives this pool bought, and the brackets that would have changed them, are on{" "}
              <Link to="/accounts">Accounts</Link>.
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
              {rows.map((a) => {
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
                        {isDrill(a) && (
                          <span style={{ color: palette.muted }} title="A backtest rep — unpriced, blind drop">
                            🎲{" "}
                          </span>
                        )}
                        {/* You marked this one to come back to. It replaced the
                            orange "review owed" on 2026-08-25, and the swap is
                            the whole change in miniature: that badge appeared on
                            every traded sitting and meant the account was
                            refusing the next one, so it was on almost every row
                            and said nothing. This one is on a row because you
                            put it there. */}
                        {a.review_later && (
                          <span
                            style={{ color: palette.orange }}
                            title={
                              "Marked to review later. Nothing is waiting on it — press review to go back in." +
                              ((a.flags?.length ?? 0) > 0
                                ? ` It also tripped ${a.flags!.length} account rule(s).`
                                : "")
                            }
                          >
                            🚩{" "}
                          </span>
                        )}
                        {a.status !== "finished" && a.status !== "reviewed" && (
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
                      <td style={{ whiteSpace: "nowrap" }}>
                        {/* Mark it, or take the mark off, without going in. The
                            page you flag a sitting from is usually the one you
                            ended it on; this is the other door, and it is the
                            only way to *clear* a flag short of filing the
                            review (which clears it server-side — see
                            `journal.replays.patch`). */}
                        {a.status === "finished" && (a.summary?.trades ?? 0) > 0 && (
                          <button
                            type="button"
                            data-review-flag={a.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              patch.mutate({ id: a.id, review_later: !a.review_later });
                            }}
                            title={
                              a.review_later
                                ? "Marked to review later — press to unmark"
                                : "Mark to review later"
                            }
                            style={{
                              fontSize: 11,
                              marginRight: 6,
                              cursor: "pointer",
                              opacity: a.review_later ? 1 : 0.45,
                            }}
                          >
                            🚩
                          </button>
                        )}
                        {/* The way into the review. It hands the Simulator a
                            bookmark and a marker (lib/replayReview) and gets out
                            of the way — the review itself happens against the
                            tape, which is the only place the levels that were on
                            the chart at the time still exist. */}
                        {/* Drills come through here too — this button is the
                            escape hatch for a rep-end panel lost to a reload
                            (plan V9): the drill gate reads the same tags the
                            in-tape review writes. */}
                        {a.status === "finished" &&
                          ((a.flags?.length ?? 0) > 0 || (a.summary?.trades ?? 0) > 0) && (
                          <button
                            type="button"
                            data-review-open={a.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              // Marks scoped to — and the page routed by — the
                              // attempt's own mode: a drill's review belongs on
                              // the backtest page, where the recorder, the
                              // panel and the 🎲 gate all live, and a paper
                              // sitting's on the paper account, whose chip is
                              // the only place its equity means anything.
                              // Scoped to — and routed by — the attempt's own
                              // **account**, not its mode. A drill has none and
                              // belongs on the backtest page, where the
                              // recorder, the panel and the 🎲 gate all live;
                              // everything else opens on the account that
                              // priced it, because a review of a sitting whose
                              // equity means nothing on the page you land on is
                              // a review of somebody else's day.
                              const m = modeOf(a);
                              const scope = accountIdOf(a) ?? "drill";
                              saveResume(scope, {
                                symbol: a.symbol,
                                date: a.date,
                                // From the top of the sitting. The panel seeks to
                                // each flag from there; arriving at one with no
                                // idea what led to it is the review this replaces.
                                clockMs: a.started_ms,
                                attemptId: a.id,
                                // Unknown, and unused: review mode rebases every
                                // cursor off its own timestamp instead (see
                                // replaySim.rebaseLog).
                                contextTicks: 0,
                              });
                              saveReview(scope, { attemptId: a.id });
                              navigate(m === "drill" ? PAGE_OF.drill : pathOfAccount(scope));
                            }}
                            title="Review this sitting — every trade to answer for, against the tape"
                            style={{ fontSize: 11, marginRight: 6, cursor: "pointer" }}
                          >
                            review
                          </button>
                        )}
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
