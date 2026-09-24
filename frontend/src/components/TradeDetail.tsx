import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTradeDetail } from "../hooks/useTrades";
import { useExcursion } from "../hooks/useCharts";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { JournalForm } from "./JournalForm";
import type { TradeLevel } from "../hooks/useTrades";
import { ContextStrip } from "./ContextStrip";
import { TradeReplay } from "./charts/DayReplayer";
import { DISCIPLINE_LABEL, SETUP_LABEL } from "./charts/ReviewCard";
import { TradeAnalysis } from "./ai/TradeAnalysis";
import { fmt, fmtDateTime, fmtInt, fmtPct } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";

export function TradeDetail({
  scope,
  tradeNo,
}: {
  scope: FilterScope;
  tradeNo: number;
}) {
  const { data, isLoading } = useTradeDetail(scope, tradeNo);
  const { search } = useLocation();
  // Open from the start, for every mode: the replay IS the trade's chart, and a
  // review that begins by clicking the chart open begins by not looking at it.
  // (It used to default shut back when this slot held the static reconstruction
  // and backtest was the one chart-first mode.) The toggle stays for the cost —
  // hiding it unmounts the tape and the engine.
  const [showChart, setShowChart] = useState(true);

  const { data: exc } = useExcursion(scope, tradeNo);
  if (isLoading || !data) return <div className="notice">Loading trade…</div>;

  const t = data.trade;
  const row1: Card[] = [
    { label: "Direction", value: t.direction },
    { label: "Contracts", value: fmtInt(t.max_contracts) },
    { label: "Net PnL", value: fmt(t.net_pnl), tone: toneOf(t.net_pnl) },
    { label: "Avg entry", value: fmt(t.avg_entry, false) },
    { label: "Avg exit", value: fmt(t.avg_exit, false) },
  ];
  const row2: Card[] = [
    { label: "Entry", value: fmtDateTime(t.entry_ts_local) },
    { label: "Exit", value: fmtDateTime(t.exit_ts_local) },
    { label: "Hold", value: `${(t.duration_s / 60).toFixed(1)}m` },
  ];
  // How far the trade went either way, and how much of the good half was kept.
  // Header numbers rather than chart numbers: they say how the trade went, not
  // what the chart shows, and behind the chart toggle they were invisible on
  // every trade nobody opened. Rendered as ellipses while the query is in
  // flight and dropped entirely once it comes back empty — a row of dashes for
  // a session with no cached tape reads as "zero excursion", which is a result.
  const excLoading = exc === undefined;
  const hasExc = !!exc?.has_data;
  const excCell = (v: string) => (excLoading ? "…" : v);
  const row3: Card[] = !excLoading && !hasExc ? [] : [
    { label: "MFE", value: excCell(fmt(exc?.mfe_usd)), tone: "pos" },
    { label: "MAE", value: excCell(fmt(exc?.mae_usd)), tone: "neg" },
    {
      label: "Exit efficiency",
      value: excCell(exc?.exit_efficiency != null ? fmtPct(exc.exit_efficiency * 100, 0) : "—"),
    },
    {
      label: "Avg ATR (hold)",
      value: excCell(
        exc?.avg_atr_pts == null
          ? "—"
          : `${exc.avg_atr_pts.toFixed(2)} pts · ${fmt(exc.avg_atr_usd)}`,
      ),
    },
  ];
  const journal = (
    <JournalForm
      // The logical trade owns the journal entry, in either view.
      tradeKey={t.logical_trade_key}
      initialNote={data.note}
      initialTags={data.tags}
      initialSetups={data.setups}
      initialConfluences={data.confluences}
      initialModelId={data.model_id}
      initialRulesMet={data.rules_met}
      initialSetup={data.setup}
      initialDiscipline={data.discipline}
      initialWatchedLevels={data.watched_levels}
      levelCandidates={data.level_candidates}
    />
  );

  const title = (
    <div className="section-title" style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
      <span>
        Trade #{t.trade_no} — {fmtDateTime(t.entry_ts_local)}
      </span>
      {/* The hop to the whole day around this trade — its other trades, the
          day journal, the session chart. The review itself no longer needs the
          jump: the replay and the full journal form are both on this page.
          Keeps the querystring so the FilterBar scope survives. */}
      <Link
        to={{ pathname: `/calendar/${String(t.entry_ts_local).slice(0, 10)}`, search }}
        data-detail-day-link
        style={{ fontSize: 12, fontWeight: 400 }}
      >
        Replay this day ↗
      </Link>
    </div>
  );
  const chartToggle = (
    <div style={{ margin: "10px 0 4px" }}>
      <button
        type="button"
        className={showChart ? "active" : ""}
        onClick={() => setShowChart((v) => !v)}
        title="Show / hide the trade's replay — the day's tape from just before the entry, with the trade armed (hidden = unloaded)"
      >
        {showChart ? "▾ Hide replay" : "▸ Show replay"}
      </button>
    </div>
  );
  // Unbundled from the chart toggle: this is a read of a stored analysis, not a
  // second chart, and hiding it behind the expensive thing hid it for no saving.
  // Still waits for excursion to resolve, so it doesn't flash its "needs
  // excursion data" notice while the numbers are in flight.
  const analysis = exc !== undefined && (
    <TradeAnalysis
      tradeKey={t.trade_key}
      scope={scope}
      hasExcursion={!!exc?.available && !!exc?.has_data}
    />
  );

  return (
    <div>
      {title}
      {/* Machine left, human right. The measurements — levels, context windows,
          the AI read — sit in the column the trader is not typing in, which is
          the prompt-contamination guard the old single column enforced by
          ordering: your own account of the trade should not be written under a
          list of what the machine already concluded about it.

          DOM order is the collapsed order: under ~1100px the columns stack as
          written, trade first, journal last. No CSS `order` — it would leave the
          tab sequence running up the page. */}
      <div className="trade-detail-split">
        <div className="trade-detail-main">
          <KpiGrid cards={row1} template="repeat(5, 1fr)" className="kpi-compact" />
          <KpiGrid cards={row2} template="repeat(3, 1fr)" className="kpi-compact" />
          {row3.length > 0 && (
            <KpiGrid cards={row3} template="repeat(4, 1fr)" className="kpi-compact" />
          )}
          {chartToggle}
          {/* The full replayer on this trade's day, not a static reconstruction:
              same panel, same practice sim as the day view's, already rewound to
              five seconds before the entry with the trade armed. Unmounted while
              hidden — the toggle is how you shed the tape's cost, and reopening
              simply replays the trade again. */}
          {showChart && <TradeReplay scope={scope} trade={t} />}
          <LevelStrip levels={data.levels} atRank={data.levels_at_rank} />
          <ContextStrip context={data.context} scope={scope} tradeKey={t.logical_trade_key} />
          {analysis}
        </div>
        <aside className="trade-detail-rail">
          <ReviewPanel
            grade={data.grade}
            setup={data.setup}
            discipline={data.discipline}
            watchedLevels={data.watched_levels}
            levels={data.levels}
            showAbsence={t.session_known}
          />
          {journal}
        </aside>
      </div>
    </div>
  );
}

/** The review, summarized at the top of the journal rail.
 *
 * Everything but the grade is editable in the journal form below — the levels
 * included, since the trade's replay (the chart a level pick needs in front of
 * it) now sits open on this very page. Only the grade stays read-only here:
 * it is written blind at the recall front, and a grade picked beside the P&L
 * is the outcome restated.
 */
function ReviewPanel({
  grade,
  setup,
  discipline,
  watchedLevels,
  levels,
  showAbsence,
}: {
  grade: string | null;
  setup: string | null;
  discipline: string | null;
  watchedLevels: string[];
  /** The measured rows, only so each pick can be named the way the picker named
   *  it: the answer stores the level id, and its label lives on the entry row. */
  levels: TradeLevel[];
  showAbsence: boolean;
}) {
  // Nothing to say and no gap to point at: an imported broker row never went
  // through a sitting, so it cannot have skipped a review.
  if (!grade && !setup && !watchedLevels.length && !showAbsence) return null;
  const level =
    watchedLevels
      .map((w) =>
        w === "none"
          ? "no level"
          : levels.find((l) => l.anchor === "entry" && l.member === w)?.label ?? w,
      )
      .join(" + ") || null;
  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="section-title" style={{ marginTop: 0 }}>Review</div>
      <div className="section-cap">
        What you said it was, and the levels you say you were trading off.
      </div>
      {grade || setup || level ? (
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 12, flexWrap: "wrap" }}>
          {grade && (
            <b data-detail-grade={grade} style={{ fontSize: 15 }}>
              {grade}
            </b>
          )}
          {setup && (
            <span data-detail-setup={setup}>{SETUP_LABEL[setup] ?? setup}</span>
          )}
          {discipline && discipline !== "clean" && (
            <span data-detail-discipline={discipline} style={{ color: "var(--orange, #e8a13c)" }}>
              {DISCIPLINE_LABEL[discipline] ?? discipline}
            </span>
          )}
          {level && (
            <span data-detail-watched={watchedLevels.join(",")}>off the {level}</span>
          )}
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 12 }}>
          Not reviewed yet — the sitting it belongs to still owes one.
        </div>
      )}
    </div>
  );
}


/** Where the fills actually landed, as opposed to what the journal says about them.
 *
 * Rendered *after* the journal form on purpose. These numbers are deliberately not
 * wired into tagging — the whole reason they live in their own table is so the
 * trader's own answer stays independent of the machine's, and a suggestion sitting
 * above the tag box would quietly end that. Read it after you've said your piece.
 *
 * Two tiers, one honest sentence each. A family that beat the rank threshold was
 * *unusually* close — full badge. A family merely close in ticks but ordinary for
 * the session (a balance day orbits its POC all afternoon) still gets named, dimmed
 * and tagged "common": the geometry is a fact the review needs stated, and hiding
 * it made the strip contradict the chart it sits under. "Nothing qualified" is
 * printed rather than left blank: a fill that was near nothing is a real result and
 * looks identical to a missing measurement if the panel just disappears.
 */

//: Merely-close cutoff for the demoted tier, in ticks. Display-only — the rank
//: threshold (`level_tag.AT_LEVEL_RANK`) stays the measurement's verdict.
const NEAR_TICKS = 10;

function LevelStrip({ levels, atRank }: { levels: TradeLevel[]; atRank: number }) {
  if (!levels.length) return null;
  const byAnchor = (a: "entry" | "exit") =>
    levels.filter((l) => l.anchor === a && l.rank < atRank).sort((x, y) => x.rank - y.rank);
  const nearByAnchor = (a: "entry" | "exit") =>
    levels
      .filter(
        (l) =>
          l.anchor === a &&
          l.rank >= atRank &&
          l.dist_ticks != null &&
          Math.abs(l.dist_ticks) <= NEAR_TICKS,
      )
      .sort((x, y) => Math.abs(x.dist_ticks!) - Math.abs(y.dist_ticks!));

  const badge = (l: TradeLevel, common: boolean) => (
    <span
      // The member, not the family: rows are per-member since the 2026-08-21
      // tagger pass, so one family legitimately shows several badges.
      key={l.member ?? l.family}
      className="badge badge-sm"
      // Neutral, not the accent a tag wears. These are measurements, and a
      // measurement that looks identical to something the trader typed
      // invites exactly the confusion the separate table exists to prevent.
      // The common tier dims further: physically there, statistically routine.
      style={{
        background: "color-mix(in srgb, var(--muted) 14%, transparent)",
        borderColor: "color-mix(in srgb, var(--muted) 35%, transparent)",
        fontWeight: 500,
        ...(common ? { opacity: 0.55 } : {}),
      }}
      title={
        `${l.member ?? l.family} — ${l.dist_ticks?.toFixed(1) ?? "?"} ticks. ` +
        (common
          ? `Close, but ${(l.rank * 100).toFixed(0)}% of comparable moments in this ` +
            `session sat closer — ordinary placement for this day.`
          : `Closer than ${((1 - l.rank) * 100).toFixed(0)}% of comparable moments ` +
            `in this session.`)
      }
    >
      {l.label ?? l.family}
      <span className="muted" style={{ marginLeft: 4 }}>
        {l.dist_ticks == null ? "" : `${l.dist_ticks > 0 ? "+" : ""}${l.dist_ticks.toFixed(0)}t`}
        {common ? " · common" : ""}
      </span>
    </span>
  );

  const line = (a: "entry" | "exit") => {
    const hits = byAnchor(a);
    const near = nearByAnchor(a);
    // An anchor with no rows at all was never measured (a mechanical exit, or a
    // session with no cached tape) — say nothing rather than "near nothing".
    if (!levels.some((l) => l.anchor === a)) return null;
    return (
      <div key={a} style={{ display: "flex", gap: 6, alignItems: "baseline", marginTop: 4, flexWrap: "wrap" }}>
        <span className="section-cap" style={{ minWidth: 44 }}>{a}</span>
        {hits.length === 0 && near.length === 0 ? (
          <span className="muted">no level</span>
        ) : (
          <>
            {hits.map((l) => badge(l, false))}
            {near.map((l) => badge(l, true))}
          </>
        )}
      </div>
    );
  };

  return (
    <div className="panel" style={{ marginTop: 10 }}>
      <div className="section-title">Measured levels</div>
      <div className="muted" style={{ fontSize: "0.85em", marginBottom: 2 }}>
        Where the fills sat relative to the chart's references — scored against other
        moments in the same session. Full badges were unusually close; "common" ones
        were right there too, but so was the whole session.
      </div>
      {line("entry")}
      {line("exit")}
    </div>
  );
}

