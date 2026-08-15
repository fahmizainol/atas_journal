// Trade Simulator (Lab) — fxreplay-style tick-by-tick replay of one cached
// session. Pick a day + start time, press play, and practise entering/closing
// against the tape. Everything is client-side and throwaway: nothing here
// touches the journal or the database.
//
// Two clocks of state live side by side. The *engine* + *chart* are driven
// imperatively from a requestAnimationFrame loop (no React render per frame).
// The *trading* state is the small ground-truth log of what you did — the orders
// you placed, the drags, the cancels, the manual closes — from which the trades,
// the open position and the still-working orders are all derived by replaying
// the tape (see lib/replaySim). Forward play folds each frame's ticks into the
// running simulation (cheap); any scrub or any action re-runs it from the log
// (correct), which is why rewinding to re-take a setup is coherent: whatever you
// hadn't done yet at the new clock simply un-happens.
//
// Two order types. A market order is its own fill, at the last print. A limit
// order rests until the tape reaches it — bid below the market, offer above.
// The position is netted the way a futures account is: orders can be placed
// while one is open, and they add to it, scale out of it, or flip it. The fill
// rules, the netting and why they're drawn the conservative way live with the
// simulation, not here.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ReplayChart, type ReplayChartHandle } from "../components/charts/ReplayChart";
import type { IndicatorSettingsMap } from "../components/charts/IndicatorLegend";
import { buildChartKnobs } from "../components/charts/indicatorKnobs";
import type { ModernVwapParams } from "../lib/modernVwap";
import { SimIndicators } from "../components/charts/SimIndicators";
import { QuickDock } from "../components/charts/QuickDock";
import { TimeframeControl } from "../components/charts/TimeframeControl";
import { StudyPicker } from "../components/charts/StudyPicker";
import { loadStudies, saveStudies } from "../lib/chartPrefs";
import type { StudySpec } from "../lib/studies";
import type { LayerState } from "../components/charts/chartLayers";
import { ChartTopBar } from "../components/charts/ChartTopBar";
import { GuardMeters } from "../components/charts/GuardMeters";
import { LayoutPicker } from "../components/charts/LayoutPicker";
import { LAYOUTS, MAX_PANES, clampPaneIndex, gridArea, gridTemplate } from "../lib/paneLayout";
import { setLinkOn as setLinkModuleOn } from "../lib/paneLink";
import { ChartToolRail } from "../components/charts/ChartToolRail";
import { TicketKnobs } from "../components/charts/TicketKnobs";
import { EMPTY_TOOL_STATE, type ChartToolId, type ChartToolState } from "../lib/chartTools";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import {
  useSimulatorDays,
  useSimulatorHistory,
  useSimulatorSession,
  type HistDay,
  type SimDay,
} from "../hooks/useSimulator";
import { useReplayAttempt } from "../hooks/useReplayAttempt";
import { selectableModels, useModels } from "../hooks/useModels";
import { useReplayAccount, useWriteCause } from "../hooks/useReplayAccount";
import { isTypingTarget, usePaneKeys } from "../hooks/usePaneKeys";
import { AccountChip, AccountNotice, AccountRecap } from "../components/charts/ReplayAccount";
import { AutopsyCard } from "../components/charts/AutopsyCard";
import {
  useFileReview,
  useReplayAttemptDetail,
  useReplayAttempts,
  useReplayJournal,
  useSaveRuleChecks,
  type AttemptDetail,
} from "../hooks/useReplays";
import { DrillReview } from "../components/charts/DrillReview";
import { clearResume, loadResume, saveResume, type ResumePoint } from "../lib/replayResume";
import { clearReview, loadReview, saveReview } from "../lib/replayReview";
import { ReviewPanel } from "../components/charts/ReviewPanel";
import {
  concatTapes,
  ReplayEngine,
  decodeTape,
  type EventTuning,
  type IbBox,
  type RangeBox,
  type SessionPayload,
  type Tape,
} from "../lib/replayEngine";
import { replaySource, type TapeSource } from "../lib/tapeSource";
import { showsSeconds, timeframeById, TIMEFRAMES, TF_OPTIONS } from "../lib/timeframes";
import {
  newLog,
  newSim,
  rebaseLog,
  runSim,
  shiftLog,
  stepSim,
  truncateLog,
  workingOrders,
  type Log,
  type OrderRec,
  type OrderType,
  type Position,
  type Side,
  type SimState,
  type Trade,
} from "../lib/replaySim";
import {
  fmtClock,
  fmtCountdown,
  fmtPts,
  fmtR,
  fmtUsd,
  orderView,
  posLine,
  simSig,
  tradeMark,
  type BarAt,
} from "../lib/simViews";
import {
  DEFAULT_SIM_PREFS,
  HISTORY_DAY_OPTIONS,
  loadSimPrefs,
  saveSimPrefs,
  clampSplit,
  SIM_SPEEDS,
} from "../lib/simPrefs";
import {
  hhmm,
  loadDrillPrefs,
  minutesOf,
  saveDrillPrefs,
  windowOk,
  type DrillPrefs,
} from "../lib/drillPrefs";
import type { TapeRange } from "../lib/volumeProfile";
import { MIN_SAMPLE, SIM_ENGINE_VERSION } from "../lib/replayStats";
import {
  DEFAULT_FILL_MODEL,
  isPerfect,
  loadFillModel,
  PERFECT_FILLS,
  saveFillModel,
  type FillCfg,
  type FillModel,
} from "../lib/fillModel";
import { MICRO_RATIO, microCommission, microOf } from "../lib/contracts";
import { FillCues, playCue, simMark } from "../lib/orderSound";
import { dayRead, VERDICT_LINE, type DayRead, type DayVerdict } from "../lib/dayRead";
import {
  DEFAULT_GUARDS,
  accountRefusal,
  accountStop,
  dayRefusal,
  dayState,
  equityStop,
  isReducing,
  shapeRefusal,
} from "../lib/guardRules";
import { fmtWait, remainingMs, type ReviewItem } from "../lib/replayAccount";
import { useGuardLevels } from "../hooks/useRouting";
import { palette } from "../theme";

/**
 * How often the context pane may repaint while the replay plays, in ms.
 *
 * Bar close alone is not enough, and shipping it that way was wrong: a 5m pane
 * closes a bar every five minutes of session time, so at 1× speed the pane sat
 * frozen for five real minutes at a stretch. Nothing was broken — a seek moved
 * it, because a seek re-snapshots — but a chart whose last price never moves
 * reads as a dead chart, and being told "that is the gate working" is no comfort
 * while you are watching it.
 *
 * So: bar close *or* this, whichever comes first. At 200ms the pane redraws ~5
 * times a second against the trading pane's 60, which is 8% of a pane's draw
 * cost — still inside the noise the split was measured at — and the forming bar
 * visibly moves, which is the whole reason to have the pane up.
 */
const PANE_DRAW_MS = 200;

/** The caveats on a replayable day, as one suffix on its picker entry.
 *
 *  An `<option>` is text and nothing else, so these cannot be badges — and they
 *  are better as text anyway. Every one of them is a way the tape is not the
 *  ordinary bell-to-bell session with a whole night behind it, which is a thing
 *  to read *before* choosing rather than discover at the open.
 *
 *  `rithmic` is not a warning. It says the day came from the live recordings
 *  instead of the Databento corpus, which is what every session after
 *  2026-06-30 is — the corpus is pinned at the data budget. The tape is real
 *  either way; the label is there because the two stores are stamped by
 *  different clocks (~287µs apart, far below a bar) and because a recorded day
 *  is the one kind that can have been backfilled short. */
function dayNotes(d: SimDay): string {
  const notes: string[] = [];
  if (d.source === "live") notes.push("rithmic");
  if (!d.has_overnight) notes.push("no overnight");
  if (d.ends_early) notes.push("ends early");
  return notes.length ? ` (${notes.join(", ")})` : "";
}

/** How far the ticket has to be dragged down before letting go puts it away. */
const GRAB_DISMISS_PX = 64;
const RTH_OPEN_MIN = 9 * 60 + 30;

/** How often the resume bookmark is brought up to date with the clock. Bounds
 *  what a crash costs you in tape — a few seconds of scrolling back — against a
 *  localStorage write on a page that is already doing sixty frames a second. */
const RESUME_SAVE_MS = 4_000;

/** Both R's spelled out, for the row that only has room to show one. */
const rTitle = (t: Trade) =>
  t.rCash == null && t.r == null
    ? "No stop was on when this opened, so there is no risk to measure against"
    : `stake ${fmtR(t.rCash)} (of the money risked at open) · excursion ${fmtR(t.r)} (of the distance risked at open)`;
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(0)}%`);
/** First index in a tape's (ascending) times at or after `ms`, or `n` if there
 *  is none. */
const firstAt = (t: Float64Array, n: number, ms: number): number => {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (t[mid] < ms) lo = mid + 1;
    else hi = mid;
  }
  return lo;
};


/** Stable empties for a pane the layout doesn't currently have — a fresh `[]`
 *  per render would make the picker's memos churn and the chart rebuild its
 *  studies every frame. */
const EMPTY_SPECS: StudySpec[] = [];
const EMPTY_LAYERS: LayerState[] = [];

/** Where the clock stops.
 *
 *  The replay plays to the end of the tape, which includes the hour past the
 *  close. A drill stops at the bell: the rep is a question about the session,
 *  and the settlement hour is a different market that no model here is about.
 *  `min` rather than the close outright, because an early-close day's tape ends
 *  before 16:00 and the clock cannot run past what printed. */
const endOf = (s: { session_end_ms: number; rth_close_ms: number }, drill: boolean) =>
  drill ? Math.min(s.session_end_ms, s.rth_close_ms) : s.session_end_ms;

/** Which of the two replay-clock pages this is.
 *
 *  `replay` is the page you pick a day on. `drill` is backtest mode: one model
 *  bound for the whole sitting, a random RTH clock on a day you are not shown,
 *  and no account — see docs/backtest-mode-plan.md.
 *
 *  It is a prop rather than page state because the mode is fixed for the whole
 *  of a sitting and the route is what fixes it. A switch you could flip mid-rep
 *  would be a switch that moved a finished sitting between two ledgers. */
export type SimMode = "replay" | "drill";

export function Simulator({ mode = "replay" }: { mode?: SimMode } = {}) {
  const drill = mode === "drill";
  // Read once, on mount: everything below seeds from it, and from then on the
  // React state is the truth and the store just trails it.
  const [prefs] = useState(loadSimPrefs);
  // What a fill costs, and it is not a Simulator setting — Live runs the same
  // engine and reads the same model (see lib/fillModel).
  const [fills, setFills] = useState(loadFillModel);
  const [root, setRoot] = useState<string>(prefs.root);
  const daysQ = useSimulatorDays(root);
  const [sel, setSel] = useState<{ symbol: string; date: string } | null>(null);
  const [startTime, setStartTime] = useState(prefs.startTime);
  const tz = "New York";

  // Where this browser was when the page last closed (see lib/replayResume).
  // Read once and consumed once, by the first session build below — a bookmark
  // that turns out not to fit the tape is spent rather than retried, so a bad
  // one can't follow you around. From then on it is write-only: `sel` and the
  // clock are the truth and the store trails them, the same shape as `prefs`.
  const [pending, setPending] = useState(loadResume);
  const pendingRef = useRef<ResumePoint | null>(pending);
  pendingRef.current = pending;
  // The order log the bookmark names, fetched in parallel with the session it
  // belongs to rather than after it — the two are independent requests and the
  // resume needs both.
  const resumeQ = useReplayAttemptDetail(pending?.attemptId ?? null);
  // Nothing to wait for, or the fetch has come back one way or the other. A
  // failure settles it too: a missing attempt (deleted from the history page,
  // say) costs the trades, never the day.
  const resumeSettled = !pending?.attemptId || resumeQ.isSuccess || resumeQ.isError;
  const resumeDetailRef = useRef<AttemptDetail | null>(null);
  resumeDetailRef.current = resumeQ.data ?? null;

  // --- review mode ----------------------------------------------------------
  // The page opened to *look at* a sitting rather than to trade one: the tape,
  // the log and every level that was on the chart at the time, with the recorder
  // off and the order paths refusing. Entered from the history page, which
  // leaves a marker (lib/replayReview) beside the ordinary resume point.
  //
  // The marker is not trusted on its own. Review mode holds only while the
  // attempt it names still *owes* one and the session on screen is the session
  // it happened on — so a stale marker, a sitting reviewed in another tab, or a
  // day picked by hand all drop the page back to an ordinary replay rather than
  // leaving it inertly read-only.
  const [reviewMark, setReviewMark] = useState(loadReview);
  // Same query key as the resume fetch above when they name the same attempt,
  // so this is one request, not two.
  const reviewQ = useReplayAttemptDetail(reviewMark?.attemptId ?? null);
  const reviewDetail = reviewQ.data ?? null;
  const reviewFlags = reviewDetail?.flags ?? [];
  const reviewing =
    !!reviewMark &&
    reviewDetail?.status === "finished" &&
    reviewFlags.length > 0 &&
    sel?.symbol === reviewDetail.symbol &&
    sel?.date === reviewDetail.date;
  // Read inside the tape build and inside `placeOrder`, both of which run
  // outside the render that decided it.
  const reviewingRef = useRef(reviewing);
  reviewingRef.current = reviewing;

  const navigate = useNavigate();
  const fileReview = useFileReview();
  /** File the verdicts and leave.
   *
   *  Leaving is not tidiness. The reviewed sitting is `reviewed` now, and
   *  resuming into it would put it back to `active` — which withdraws the review
   *  that was just filed (see `journal.replays.save`). So the bookmark goes with
   *  the marker, and the way on from here is a new sitting. */
  const submitReview = useCallback(
    (items: ReviewItem[]) => {
      const id = reviewMark?.attemptId;
      if (!id) return;
      fileReview.mutate(
        { id, items },
        {
          onSuccess: () => {
            clearReview();
            setReviewMark(null);
            clearResume();
            navigate("/charts/replay/history");
          },
        },
      );
    },
    [fileReview, navigate, reviewMark],
  );

  /** Start somewhere else on purpose — 🎲, or a day picked by hand. The
   *  bookmark is a record of where you were, so a decision to be elsewhere
   *  retires it rather than leaving it to resurface on the next reload. */
  const leaveResume = useCallback(() => {
    setPending(null);
    pendingRef.current = null;
    clearResume();
  }, []);

  const sessionQ = useSimulatorSession(sel?.symbol ?? null, sel?.date ?? null, tz);

  // Context days: whole prior sessions, drawn to the left of the replay so the
  // levels you trade off — yesterday's high, the shelf the week has been sat on
  // — are on the chart instead of in your head. They are the same contract only:
  // a roll would splice two price series a hundred points apart, which is the
  // same rule the weekly anchor follows (journal.sim.weekly).
  const [historyDays, setHistoryDays] = useState(prefs.historyDays);
  // What is made *of* those days: one composite profile over the auction they
  // belong to, frozen at the prior close (see lib/compositeProfile), the nodes
  // read off it, and the tape-event bands. All three are reading choices in the
  // same sense as the bar size — they are drawn from tape that is already loaded
  // and none of them can move the clock or fill an order.
  const [composite, setComposite] = useState(prefs.composite);
  const [compositeSpan, setCompositeSpan] = useState(prefs.compositeSpan);
  const [nodeProm, setNodeProm] = useState(prefs.nodeProm);
  // Modern VWAP's parameters. One object, patched rather than replaced, because
  // the indicator takes them as one and half the knobs are only meaningful
  // beside another (a pivot length without a swing anchor, an occupancy floor
  // without its window).
  const [mvParams, setMvParams] = useState(prefs.modernVwap);
  const patchMv = useCallback(
    (patch: Partial<ModernVwapParams>) => setMvParams((p) => ({ ...p, ...patch })),
    [],
  );
  // The tape events, in two halves: what selects them (ten knobs the engine
  // detects by, so a change re-derives the tape) and how they draw (three that
  // are a repaint). Kept apart because the cost is different and the panels say
  // so — nothing on a chart should quietly rebuild a million ticks.
  const [evTuning, setEvTuning] = useState(prefs.eventTuning);
  // Mirrored for the session loader, which builds the engine and deliberately
  // doesn't re-run on anything but a new tape — the same reason the timeframe and
  // the big-trade threshold are mirrored.
  const evTuningRef = useRef(evTuning);
  evTuningRef.current = evTuning;
  const [evLabelSt, setEvLabelSt] = useState(prefs.eventLabelSt);
  const [evFill, setEvFill] = useState(prefs.eventFill);
  const [evMarginal, setEvMarginal] = useState(prefs.eventMarginal);
  // The day-scale indicator strip over the chart's foot (see SimIndicators): the
  // IB-width chip and the range-budget gauge. A reading choice like the bar size
  // and the big-trade threshold — it cannot move the clock or fill an order.
  const [indicators, setIndicators] = useState(prefs.indicators);
  const histDates = useMemo(() => {
    if (!sel || historyDays <= 0) return [];
    return (daysQ.data?.days ?? [])
      .filter((d) => d.symbol === sel.symbol && d.date < sel.date)
      .map((d) => d.date)
      .sort()
      .slice(-historyDays);
  }, [daysQ.data, historyDays, sel]);
  const histQ = useSimulatorHistory(sel?.symbol ?? null, histDates, tz);

  // What this sitting is being recorded as. The recorder watches the published
  // simulation and writes it; nothing about trading goes through it, so a
  // failed save costs the record and never the replay.
  const attemptRec = useReplayAttempt();
  const {
    arm: armAttempt,
    adopt: adoptAttempt,
    open: openAttempt,
    attemptId: attemptIdOf,
    record: recordAttempt,
    noteRewind,
    finish: finishAttempt,
    setNote: setAttemptNote,
  } = attemptRec;

  // Blind replay: which day this is stays hidden until the replay runs out, or
  // until you ask. It only means anything with a random draw — a day you chose
  // is a day you already know — but the two are kept apart because giving up on
  // one shouldn't give up on the other.
  //
  // `revealed` is per-session and starts false on every new day; the preference
  // is what persists.
  const [blind, setBlind] = useState(prefs.blind);
  const [revealed, setRevealed] = useState(false);
  // Backtest mode is blind by definition — a rep on a day you were shown is not
  // a rep. `Reveal` still works, because giving up on one draw should not need
  // the mode switched off.
  const hidden = (blind || drill) && !revealed;

  // Backtest mode's own three settings (lib/drillPrefs). Held whether or not
  // this page is the drill, because a hook cannot be conditional — the drill
  // reads them and the replay ignores them.
  const [drillPrefs, setDrillPrefs] = useState(loadDrillPrefs);
  useEffect(() => {
    if (drill) saveDrillPrefs(drillPrefs);
  }, [drill, drillPrefs]);
  const patchDrill = useCallback(
    (p: Partial<DrillPrefs>) => setDrillPrefs((d) => ({ ...d, ...p })),
    [],
  );
  // Read inside the session-build effect, which is installed once and must not
  // be re-bound when a setting changes — the same reason `ticketRef` exists.
  const drillRef = useRef(drillPrefs);
  drillRef.current = drillPrefs;
  // The drawn-from window as tape wall clocks, resolved when a day is drawn
  // (the bounds are ET times and the tape is a wall clock, so they only become
  // milliseconds once there is a session to measure them against).
  const drillWindowRef = useRef<{ from_ms: number; to_ms: number } | null>(null);
  const modelsQ = useModels();
  // Archived models are off the list but not unbound: a campaign whose model was
  // archived mid-way keeps booking against it, and the picker says so rather
  // than silently re-pointing the reps at something else.
  const models = useMemo(
    () => selectableModels(modelsQ.data ?? [], drillPrefs.modelId),
    [modelsQ.data, drillPrefs.modelId],
  );
  const boundModel = models.find((m) => m.id === drillPrefs.modelId) ?? null;
  // Why the drill cannot draw, or null. The server refuses both of these too
  // (400 on an unbound drill); this is the copy that can say so before you press
  // anything.
  const drillBlocked = !drill
    ? null
    : drillPrefs.modelId == null
      ? "Bind a model first — a drill with nothing bound measures nothing."
      : !windowOk(drillPrefs)
        ? "The drop window ends before it starts."
        : null;

  // Whether this rep has ended — by hand or at the bell — and the review is
  // therefore worth offering. Its own flag rather than reading the recorder's
  // status, because the review must not appear over a rep that merely paused,
  // and must survive the next autosave reopening the attempt.
  const [repOver, setRepOver] = useState(false);

  // Where in RTH this rep was thrown in, as ET minutes past midnight. Null on
  // the replay page and until the first draw.
  //
  // Drawn *with the day* rather than recomputed where the clock is set: that
  // code runs again on every context change (a history day added, a corrected
  // header), and a drop that re-rolled itself under you would move the tape
  // mid-rep. One draw, one drop.
  const [drop, setDrop] = useState<number | null>(null);

  /** Any cached day, at random — and in a drill, an hour of it too.
   *
   *  Uniform over all 601 cached days, with replacement and no memory of what
   *  has been sat. At that size a chance repeat is rare, and drawing from a set
   *  difference would be bookkeeping for very little. */
  const anyDay = useCallback(
    (days: { symbol: string; date: string }[]) => {
      const d = days[Math.floor(Math.random() * days.length)];
      // Drawing is a decision to be somewhere else, so the bookmark goes.
      leaveResume();
      setRevealed(false);
      setRepOver(false);
      if (drillRef.current && drill) {
        const lo = minutesOf(drillRef.current.dropFrom);
        const hi = minutesOf(drillRef.current.dropTo);
        // Inclusive of both bounds: a window with equal ends is a fixed-time
        // drill, which is a reasonable thing to ask for.
        setDrop(lo + Math.floor(Math.random() * (hi - lo + 1)));
      }
      setSel({ symbol: d.symbol, date: d.date });
    },
    [drill, leaveResume],
  );

  // Which day the picker opens on. A sitting you were in the middle of wins:
  // "carry on" is what you want far more often than "start again", and the tape
  // you were half way through is by definition not one you can practise blind on
  // any more anyway.
  //
  // Failing that it is a draw, on purpose — the replay is only practice while
  // the tape is one you don't remember, and the newest session is the one you
  // have most likely just been looking at. Pick another with the dropdown, or
  // draw again with 🎲.
  //
  // A bookmarked day that isn't in the list falls through to the draw: the root
  // is a preference saved next to it so the two normally agree, but a contract
  // whose cache has since been cleared out is simply not there to go back to.
  useEffect(() => {
    const days = daysQ.data?.days;
    if (sel || !days?.length) return;
    // A drill always draws. Two reasons, and the second is a bug the first
    // would have hidden: a rep you walked away from is not a cold read any
    // more, so resuming one is not the favour it is on the replay page — and a
    // resumed day arrives with no drop drawn, which would quietly fall back to
    // the start time and land every such rep at 09:30.
    if (!drill) {
      const p = pendingRef.current;
      if (p && days.some((d) => d.symbol === p.symbol && d.date === p.date)) {
        setSel({ symbol: p.symbol, date: p.date });
        return;
      }
    }
    anyDay(days);
  }, [anyDay, daysQ.data, drill, sel]);

  // --- imperative refs (not React state — the frame loop reads these) -------
  const chartRef = useRef<ReplayChartHandle>(null);
  const engineRef = useRef<ReplayEngine | null>(null);
  // Three views of the same ticks. `tapeRef` is what the engine plays and what
  // every fill is resolved against; `sessTapeRef` is the session's own prints,
  // kept apart so re-gluing context is an array copy rather than a re-decode;
  // `histTapesRef` is the context currently in front of it, which is what a
  // change to the day count is diffed against.
  const tapeRef = useRef<Tape | null>(null);
  const sessTapeRef = useRef<Tape | null>(null);
  const histTapesRef = useRef<Tape[]>([]);
  const sessionRef = useRef<SessionPayload | null>(null);
  // Which clock this surface runs on, and what it will let you do to it. This
  // page is always the replay half; the Live tab supplies liveSource() instead
  // and shares everything downstream of engine.advance(). Re-made whenever the
  // session changes, because the replay clock is defined by that session's end.
  const sourceRef = useRef<TapeSource>(replaySource(0));
  const clockRef = useRef<number>(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const playingRef = useRef(false);
  const speedRef = useRef(prefs.speed);
  const idRef = useRef(1);

  // The action log, and the simulation currently derived from it.
  const logRef = useRef<Log>(newLog());
  const simRef = useRef<SimState>(newSim());
  const sigRef = useRef("");
  const openRef = useRef<Position | null>(null);
  // What the blotter looked like last time it was published, so a fill can be
  // heard as the difference. Re-synced — and so adopted in silence — by every
  // path that re-derives a *different* sitting rather than advancing this one: a
  // resume, a seek, a session swap. See lib/orderSound: that is the whole rule.
  const cuesRef = useRef(new FillCues());

  /** Note where the replay currently stands, for the next visit.
   *
   *  Reads the refs rather than React state for the same reason the frame loop
   *  does: the clock never renders. What goes in is a *position* and nothing
   *  else — the trades are already on the server under the attempt named here,
   *  and a second copy of them in localStorage would be a second answer that
   *  could disagree with the first. */
  const writeResume = useCallback(() => {
    const s = sessionRef.current;
    if (!s) return;
    saveResume({
      symbol: s.symbol,
      date: s.date,
      clockMs: clockRef.current,
      attemptId: attemptIdOf(),
      // The cursors in the stored log count from the start of the glued tape, so
      // what is glued in front of it right now is the number that makes them
      // readable again next time.
      contextTicks: histTapesRef.current.reduce((a, t) => a + t.n, 0),
    });
  }, [attemptIdOf]);

  // --- display state --------------------------------------------------------
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(prefs.speed);
  // `gen` stamps the HUD with the session it was read from. Everything keyed on
  // the clock — revealing a blind day, ending an attempt — runs an effect, and
  // an effect sees the clock from the render it was queued in. On the commit
  // where a new tape lands that clock is still the last session's, and a day
  // earlier in the calendar has smaller wall-clock epoch-ms: the old clock is
  // then already "past the end" of the new tape, which would reveal a blind
  // session on sight and close an attempt that hasn't started. Comparing the
  // stamp is how those effects tell whose clock they are looking at.
  const [hud, setHud] = useState<{
    clockMs: number;
    lastPrice: number;
    openPnl: number;
    gen: number;
    ib: IbBox | null;
    range: RangeBox | null;
  }>({ clockMs: 0, lastPrice: NaN, openPnl: 0, gen: 0, ib: null, range: null });
  const sessGenRef = useRef(0);
  // The session geometry the day-scale indicators read, as of the last step or
  // snapshot the engine produced. A ref rather than a fourth argument to
  // `pushHud`, because it is the engine's to know and half the HUD pushes come
  // from actions that never moved the clock (placing an order re-reads the mark;
  // it cannot have changed the day's range).
  const geoRef = useRef<{ ib: IbBox | null; range: RangeBox | null }>({ ib: null, range: null });
  const [trades, setTrades] = useState<SimState["trades"]>([]);
  // Why the last gesture was refused, if it was. Transient — it clears itself,
  // because it is a reply to something you just did rather than a state of the
  // page.
  const [refused, setRefused] = useState<string | null>(null);
  const [openPos, setOpenPos] = useState<Position | null>(null);
  const [working, setWorking] = useState<WorkingOrderView[]>([]);
  const [size, setSize] = useState(prefs.size);
  // Which contract this replay's orders are *sent to*. The tape is the mini's
  // either way — there are no micro ticks to load — so this is a re-pricing of
  // the same session, not a different one (see lib/contracts).
  const [micro, setMicro] = useState(prefs.micro);
  // The root's micro, or null where it has none — which is what decides the
  // choice is offered at all, and what the chart's routing badge is called.
  const microSym = useMemo(() => microOf(root), [root]);
  // Trading the micro is only really on when there is one to trade. A stored
  // `micro: true` carried onto a root without a micro would otherwise price the
  // session at a tenth of itself and name no contract for it.
  const onMicro = micro && microSym != null;
  // What the account is charged, in the contract actually being traded: a micro
  // round turn is not billed at the mini's rate the setting was measured at
  // (lib/contracts — the same scaling the live broker applies).
  const charged = useMemo<FillModel>(
    () => (onMicro ? { ...fills, commission: microCommission(fills.commission) } : fills),
    [fills, onMicro],
  );
  // The contract cannot be changed with skin in the game. The mini and its micro
  // are two instruments that do not net, so a position opened in one can only be
  // traded in that one — and a resting order belongs to the contract it was sent
  // to. The live panel refuses the same switch for the same reason
  // (components/RoutingPanel, `InstrumentSwitch`); here it also keeps the
  // netting in `replaySim` from ever having to arbitrate between two contracts
  // in one position.
  const contractLocked = openPos != null || working.length > 0;
  // Both bracket legs are optional: zero ticks means the leg isn't attached at
  // all, and an order can be placed with neither — the trade is then yours to
  // close by hand, or to bracket afterwards by dragging a level onto it.
  const [stopTicks, setStopTicks] = useState(prefs.stopTicks);
  const [targetTicks, setTargetTicks] = useState(prefs.targetTicks);
  // The ladder. Off by default, and set per ticket rather than per session — it
  // rides on the order, so two trades in one replay can be managed differently.
  const [trailTicks, setTrailTicks] = useState(prefs.trailTicks);
  const [trailStepTicks, setTrailStepTicks] = useState(prefs.trailStepTicks);
  const [trailBeTicks, setTrailBeTicks] = useState(prefs.trailBeTicks);
  const [trailBeOnly, setTrailBeOnly] = useState(prefs.trailBeOnly);
  const [orderType, setOrderType] = useState<OrderType>(prefs.orderType);
  const [limitPx, setLimitPx] = useState("");
  const lastHudRef = useRef(0);
  const [tfId, setTfId] = useState(prefs.timeframe);
  const tf = useMemo(() => timeframeById(tfId), [tfId]);
  // The session loader builds the engine and deliberately doesn't re-run on
  // anything but a new tape, so it reads the timeframe from here.
  const tfRef = useRef(tf);
  tfRef.current = tf;
  // What counts as a big trade. Read by the session loader for the same reason
  // the timeframe is — the engine derives the marks, and it is built there.
  const [bigLots, setBigLots] = useState(prefs.bigLots);
  const bigLotsRef = useRef(bigLots);
  bigLotsRef.current = bigLots;

  // The ticket as it stands, for the recorder to stamp on an attempt when the
  // first fill opens one. Held in a ref rather than passed as a value: an
  // attempt opens from inside the publish path, which must not re-bind every
  // time a distance box changes — and the ticket that matters is the one the
  // first trade was taken with, not the one the session loaded under.
  const ticketRef = useRef<Record<string, unknown>>({});
  ticketRef.current = {
    size,
    stopTicks,
    targetTicks,
    trailTicks,
    trailStepTicks,
    trailBeTicks,
    trailBeOnly,
    orderType,
    speed,
    startTime,
    blind,
    timeframe: tfId,
    // What the fills were charged. Stamped with the ticket because it is the
    // other half of the same question: a net figure only means something
    // alongside the rules that priced it. The rate is the one actually charged,
    // not the stored mini rate — which is why the contract is stamped beside it:
    // "$0.50 a side" only reads as right next to the MNQ it was charged on.
    contract: onMicro ? microSym : root,
    commission: charged.commission,
    slipTicks: charged.slipTicks,
    queueTicks: charged.queueTicks,
    // Stamped beside `speed` on purpose. The lag is wall-clock milliseconds
    // applied to tape milliseconds, so a sitting watched at 4× spent a quarter
    // of the market movement in flight that the same click would have spent
    // live — the two numbers only read as one story together.
    latencyMs: charged.latencyMs,
  };
  // Whether the tape running out has already closed this attempt. One shot per
  // session: reaching the end again after a rewind is not a second ending.
  const endedRef = useRef(false);

  // What a leg switched back on goes back to. Turning a leg off is a trading
  // decision, not a reason to forget the distance you were using — so the last
  // live value is kept here and the toggle restores it.
  const lastStopRef = useRef(prefs.stopTicks || DEFAULT_SIM_PREFS.stopTicks);
  const lastTargetRef = useRef(prefs.targetTicks || DEFAULT_SIM_PREFS.targetTicks);
  const applyStop = useCallback((t: number) => {
    const v = Math.max(0, Math.floor(t) || 0);
    if (v > 0) lastStopRef.current = v;
    setStopTicks(v);
  }, []);
  const applyTarget = useCallback((t: number) => {
    const v = Math.max(0, Math.floor(t) || 0);
    if (v > 0) lastTargetRef.current = v;
    setTargetTicks(v);
  }, []);
  // Same idea for the ladder. A trail switched off and back on is the same trail.
  const lastTrailRef = useRef(prefs.trailTicks || prefs.stopTicks || DEFAULT_SIM_PREFS.stopTicks);
  const applyTrail = useCallback((t: number) => {
    const v = Math.max(0, Math.floor(t) || 0);
    if (v > 0) lastTrailRef.current = v;
    setTrailTicks(v);
  }, []);

  // Whether the ticket/blotter rail reserves a column or opens over the tape.
  // Declared up here with the other carried settings so the save effect below can
  // see it — see the rail section further down for what it does.
  const [railPinned, setRailPinned] = useState(prefs.railPinned);
  // Whether the transport row is in flow. See simPrefs.transportOpen for why it
  // is a setting at all: every control on it has a key, so a reading session can
  // have the ~34px back.
  const [transportOpen, setTransportOpen] = useState(prefs.transportOpen);

  // The extra panes' own carried settings, up here for the same reason. What a
  // pane *is* lives further down, with the engines that feed them.
  const [layout, setLayout] = useState(prefs.layout);
  const [paneTfIds, setPaneTfIds] = useState(prefs.paneTfs);
  const [splitPct, setSplitPct] = useState(prefs.splitPct);
  const [splitPctY, setSplitPctY] = useState(prefs.splitPctY);
  const [linkOn, setLinkOn] = useState(prefs.linkOn);
  const [toolsPinned, setToolsPinned] = useState(prefs.toolsPinned);
  const [paneLinked, setPaneLinked] = useState(prefs.paneLinked);
  const paneCount = LAYOUTS[layout].panes;

  // The community studies and this chart's own layers, both per pane and both
  // driven from the topbar ƒ (see StudyPicker). Per pane because a pane is a
  // question: the study you want on the 5m is usually not the one you want on
  // the hourly beside it, which is the same reason the visibility map splits.
  //
  // The specs are the page's because the page persists them; the *layers* are
  // each chart's own and only reported here, so the catalogue can show what the
  // focused pane is drawing without anyone holding a second copy of that state.
  const [studies, setStudies] = useState<StudySpec[][]>(() =>
    Array.from({ length: MAX_PANES }, (_, i) => loadStudies(i === 0 ? undefined : `p${i}`)),
  );
  const [paneLayers, setPaneLayers] = useState<LayerState[][]>(() =>
    Array.from({ length: MAX_PANES }, () => []),
  );
  const setPaneStudies = useCallback((pane: number, specs: StudySpec[]) => {
    setStudies((prev) => prev.map((s, i) => (i === pane ? specs : s)));
    saveStudies(specs, pane === 0 ? undefined : `p${pane}`);
  }, []);
  const reportLayers = useCallback((pane: number, next: LayerState[]) => {
    setPaneLayers((prev) => prev.map((l, i) => (i === pane ? next : l)));
  }, []);
  /** Which pane the chrome acts on. Claimed by the pointer arriving (the same
   *  event lib/chartFocus already elects the keyboard owner on), so it is
   *  usually the pane you are looking at without anyone having clicked. A pane
   *  that stops existing hands it back to pane 0 rather than to nothing. */
  const [focus, setFocus] = useState(0);
  const focusedPane = clampPaneIndex(focus, layout);
  // For the key handler, which is installed once and must read the current pane
  // at event time rather than whichever one it closed over.
  const focusRef = useRef(focusedPane);
  focusRef.current = focusedPane;

  // What each pane's hand-tools are doing, for the one rail that drives them
  // (see lib/chartTools). Page state because the rail is drawn by the page; one
  // entry per pane rather than one for the focused pane, so switching focus
  // shows what that pane already had armed rather than a stale reading of the
  // pane you left.
  const [toolStates, setToolStates] = useState<ChartToolState[]>(() =>
    Array.from({ length: MAX_PANES }, () => EMPTY_TOOL_STATE),
  );
  const reportTools = useCallback((i: number, s: ChartToolState) => {
    setToolStates((prev) => {
      const cur = prev[i];
      // The chart reports on a render; most of those say the same thing, and a
      // fresh array per report would re-render the whole page for nothing.
      if (
        cur &&
        cur.armed === s.armed &&
        cur.canOrder === s.canOrder &&
        cur.hasAvwap === s.hasAvwap &&
        cur.hasRangeSel === s.hasRangeSel &&
        cur.hasHlineSel === s.hasHlineSel &&
        cur.drawings === s.drawings
      ) {
        return prev;
      }
      const next = prev.slice();
      next[i] = s;
      return next;
    });
  }, []);

  // Panes 1..n-1 — every chart on the page except the page's own. Arrays indexed
  // by pane rather than a second named ref per pane: with six layouts the count
  // is data, and `chart2Ref`/`chart3Ref`/`chart4Ref` would put that count into
  // the source four times over. Index 0 is deliberately never used, so a pane
  // index means the same thing here as it does everywhere else on the page.
  const extraCharts = useRef<(ReplayChartHandle | null)[]>([]);
  const extraEngines = useRef<(ReplayEngine | null)[]>([]);
  /** rAF timestamp of each extra pane's last repaint — see PANE_DRAW_MS. */
  const extraDrawn = useRef<number[]>([]);
  // Identity-stable, so an effect can depend on "which bucketings" without
  // re-running because `.map` handed back a fresh array this render.
  const paneTfKey = paneTfIds.join(",");
  const paneTfsRef = useRef(paneTfIds.map(timeframeById));
  paneTfsRef.current = paneTfIds.map(timeframeById);
  const paneCountRef = useRef(paneCount);
  paneCountRef.current = paneCount;

  /** Any pane's chart handle by index — pane 0's lives in its own ref, since it
   *  is the page's own chart and everything else on the page reaches it that
   *  way. The tool rail is the one caller that genuinely doesn't care which. */
  const paneChart = useCallback(
    (i: number): ReplayChartHandle | null => (i === 0 ? chartRef.current : extraCharts.current[i]),
    [],
  );

  /** Do something to every extra pane that currently exists. The guard is the
   *  point: a pane's chart handle outlives the layout change that removed it by
   *  one render, and driving a chart that is on its way out is how the old
   *  two-pane code learned to check `panes > 1` in five places. */
  const eachExtra = useCallback(
    (fn: (chart: ReplayChartHandle, i: number) => void) => {
      for (let i = 1; i < paneCountRef.current; i++) {
        const c = extraCharts.current[i];
        if (c) fn(c, i);
      }
    },
    [],
  );

  // The fill model follows you to the next visit too, and to the Live page —
  // hence its own store rather than a corner of this page's prefs.
  useEffect(() => {
    saveFillModel(fills);
  }, [fills]);

  // The ticket, the transport speed and the session you set up from are settings,
  // not replay state — they follow you to the next visit.
  useEffect(() => {
    saveSimPrefs({
      root,
      startTime,
      speed,
      size,
      micro,
      stopTicks,
      targetTicks,
      trailTicks,
      trailStepTicks,
      trailBeTicks,
      trailBeOnly,
      orderType,
      blind,
      timeframe: tfId,
      bigLots,
      historyDays,
      composite,
      compositeSpan,
      nodeProm,
      modernVwap: mvParams,
      eventTuning: evTuning,
      eventLabelSt: evLabelSt,
      eventFill: evFill,
      eventMarginal: evMarginal,
      indicators,
      railPinned,
      transportOpen,
      layout,
      paneTfs: paneTfIds,
      splitPct,
      splitPctY,
      linkOn,
      paneLinked,
      toolsPinned,
    });
  }, [
    root,
    startTime,
    speed,
    size,
    micro,
    stopTicks,
    targetTicks,
    trailTicks,
    trailStepTicks,
    trailBeTicks,
    trailBeOnly,
    orderType,
    blind,
    tfId,
    bigLots,
    historyDays,
    composite,
    compositeSpan,
    nodeProm,
    mvParams,
    evTuning,
    evLabelSt,
    evFill,
    evMarginal,
    indicators,
    railPinned,
    transportOpen,
    layout,
    paneTfIds,
    splitPct,
    splitPctY,
    linkOn,
    paneLinked,
    toolsPinned,
  ]);

  // The global switch, into the module the chart handlers read at event time.
  // The per-pane half rides the `linked` prop, since that one is already a
  // per-chart fact.
  useEffect(() => {
    setLinkModuleOn(linkOn);
  }, [linkOn]);

  // The app shell scrolls in normal document flow, so there is no ancestor
  // height for the chart to be a percentage of. Measure where the page starts
  // and claim everything below it: the replay wants the tape as tall as the
  // screen allows, and the chrome above (topbar, tabs, padding) is not fixed.
  //
  // Published as a custom property rather than an inline height, so the
  // stylesheet stays in charge of whether to fill at all — a viewport too short
  // to be worth filling drops it and scrolls instead. There is deliberately no
  // floor here: a floor taller than the viewport is exactly what pushes the
  // transport below the fold on a phone held sideways.


  // Two pieces of chrome that only exist where the viewport can't afford them.
  // The setup row folds away on a short screen (it is pre-run configuration, not
  // something you touch mid-replay) and the ticket collapses to a strip on a
  // narrow one. Both are inert anywhere the CSS leaves the originals visible, so
  // there is no desktop behaviour riding on this state.
  const [setupOpen, setSetupOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  // --- the rail ---------------------------------------------------------------
  // The page has one layout, and it is the chart: the bar above is 36px, the
  // transport below is a row, and everything else — setup, ticket, blotter — is
  // summoned. There used to be a second layout (`.sim-page.full`, entered on
  // mount) that said the same thing by covering the shell with position:fixed;
  // the shell now simply declines to draw chrome for this workspace, so the mode
  // and its z-index contract are gone. ⛶ still exists, on ChartTopBar, and means
  // only what the app cannot do for itself: hide the *browser's* chrome.
  //
  // Pinned, the rail reserves a column. Unpinned, opening it lays the panel over
  // the tape and the tape keeps its full width.

  // How much of the foot of the chart is already spoken for: the market buttons
  // float over the bottom of the tape so they stay under a thumb, and anything
  // the chart wants to park down there (the order ticket, which docks to the
  // bottom on a fingertip) has to sit above them.
  //
  // Measured rather than declared — the row is one height or two depending on
  // whether a position is open and how far its buttons wrapped — and reported by
  // the window itself, which is the only thing that knows whether it is still
  // parked down there or has been dragged off onto the chart. Published as
  // --chart-floor and read by the chart's own stylesheet, so the chart never has
  // to know what this page keeps down there.
  const [floor, setFloor] = useState(0);

  // The transport's height, for the one thing that has to clear it: the ticket
  // panel is positioned against `.sim-body`, whose bottom edge is under the
  // transport, so without this it would lay over the clock. The chart's own
  // floor (above) can't answer this — that one is measured inside the chart and
  // is about what floats over the *tape*.
  //
  // Measured rather than declared for the usual reason: the bar is one row or
  // two depending on width, and a guess is wrong on one of them.
  const footRef = useRef<HTMLDivElement>(null);
  const [foot, setFoot] = useState(0);
  /** Is the transport actually in flow right now?
   *
   *  Not simply `transportOpen`. **A position on takes it away**, whatever the
   *  preference says, and that is the one piece of chrome on this page that
   *  hides itself rather than being hidden.
   *
   *  The transport is for scrubbing, and scrubbing with size on is the single
   *  gesture the replay cannot honestly support: a seek truncates the log, so
   *  rewinding past your own entry un-happens the trade you are in the middle
   *  of. The recorder counts that as a do-over and flags it, which is the right
   *  bookkeeping and the wrong moment to be doing bookkeeping — the honest fix
   *  is that the control is not there to reach for while you are holding
   *  something.
   *
   *  It comes straight back when the position comes off; the preference is never
   *  written, so the ▶▌ toggle still means what it always meant. The keys are
   *  unaffected — k still plays and pauses, `,` and `.` still step. Nothing
   *  about *running* the tape is being taken away, only the row you scrub on.
   */
  const transportShown = transportOpen && !openPos;

  useEffect(() => {
    const el = footRef.current;
    if (!el) return;
    const read = () => setFoot(el.getBoundingClientRect().height);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    read();
    return () => ro.disconnect();
    // Re-read when the row is hidden: it stays mounted (so this ref never goes
    // null) but collapses to nothing, and the ticket must stop clearing a bar
    // that isn't there.
  }, [transportShown]);

  // Drag the ticket away. The panel is anchored to the bottom edge in fullscreen,
  // so down is the direction it came from and down is the way it goes back — on a
  // phone that gesture is the whole affordance, since there is no comfortable
  // button to reach for with a thumb already on the glass.
  //
  // Driven straight at the DOM rather than through state: a pointermove lands
  // ~60×/second and none of them change anything React needs to know about until
  // the drag ends.
  const panelRef = useRef<HTMLDivElement>(null);
  const grabRef = useRef<{ id: number; y0: number; dy: number } | null>(null);

  const onGrabDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    grabRef.current = { id: e.pointerId, y0: e.clientY, dy: 0 };
    // Capture keeps the drag alive when the finger leaves the bar, which it will
    // — the bar moves out from under it. Throws if the pointer is already gone.
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* not capturable; the drag still tracks via the move handler */
    }
    panelRef.current?.classList.add("dragging");
  }, []);

  const onGrabMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const g = grabRef.current;
    if (!g || g.id !== e.pointerId) return;
    // Downward only — dragging up would just peel the sheet off its own edge.
    g.dy = Math.max(0, e.clientY - g.y0);
    if (panelRef.current) panelRef.current.style.transform = `translateY(${g.dy}px)`;
  }, []);

  const onGrabEnd = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const g = grabRef.current;
    if (!g || g.id !== e.pointerId) return;
    grabRef.current = null;
    const el = panelRef.current;
    if (el) {
      el.classList.remove("dragging");
      el.style.transform = "";
    }
    if (g.dy >= GRAB_DISMISS_PX) setSheetOpen(false);
  }, []);

  // Escape puts away whatever this page currently has out. There is no layout
  // mode left for it to exit — the page has one layout — so it closes the setup
  // panel and the ticket instead, innermost thing first is not a distinction
  // worth making when only two things can be open.
  //
  // Native fullscreen is not handled here: the browser owns that Escape, and
  // ChartTopBar follows `fullscreenchange` rather than trying to predict it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Only when the chart didn't already spend this Escape on a tool of its
      // own, and not while the browser is using it to leave fullscreen.
      if (e.key !== "Escape" || e.defaultPrevented || document.fullscreenElement) return;
      setSetupOpen(false);
      setSheetOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const tickSize = sessionRef.current?.tick_size ?? 0.25;
  /** What a point is worth on the **tape's** contract — the mini, because they
   *  are the mini's ticks. */
  const tapePointValue = sessionRef.current?.point_value ?? 20;
  // What a point is worth in the contract orders are going to *now*: the tape's
  // figure, or a tenth of it while the ticket is pointed at the micro
  // (lib/contracts). This prices the ticket and what the guardrails will accept
  // — the order you are about to place — and nothing that has already happened.
  // The tick *grid* is untouched either way: MNQ trades the same quarter point,
  // so every price on this page is where it always was and only the money moves.
  const pointValue = tapePointValue / (onMicro ? MICRO_RATIO : 1);
  // Everything a fill needs to be priced: what the instrument is worth, and what
  // the account is charged. Rebuilt when either changes, which is what makes
  // turning commission on re-derive the whole log at the new rules rather than
  // leaving the trades already booked under the old ones.
  //
  // Deliberately the **tape's** contract and the **stored** rate, not the routed
  // ones: which contract a trade was in is stamped on the order that opened it
  // and `replaySim` prices each position from its own stamp. Feeding the routed
  // figure in here instead is what made switching the ticket to NQ re-price a
  // position that had been sent, filled and closed as MNQ.
  const fillCfg = useMemo<FillCfg>(
    () => ({ ...fills, pointValue: tapePointValue, tickSize }),
    [fills, tapePointValue, tickSize],
  );

  // The guardrail levels, from the one place that owns them. Practice has to
  // refuse what the funded account refuses, so these come down from
  // `/live/routing` rather than being a second set of constants — and fall back
  // to `DEFAULT_GUARDS` (the same numbers the server defaults to) when there is
  // no session to ask, which is the ordinary case for a replay.
  const guardQ = useGuardLevels().data;
  const guards = guardQ?.guards ?? DEFAULT_GUARDS;
  // `REPLAY_GUARDRAILS=0` in .env. Off, the rules are still *computed* and still
  // shown — the day still locks in the strip, the fast-trade share still counts
  // — and nothing is refused or auto-closed. That split is the point: the reason
  // to switch this off is to run a session the rules would not allow (a 30-tick
  // stop, trading through the daily stop to see what the rest of the day did),
  // and such a session is worth nothing if the readout goes quiet too. Falls
  // back to enforced whenever the API did not answer.
  const guardsOn = guardQ?.enforced ?? true;
  // The account, beside the levels — and unlike them, **not** subject to
  // `guardsOn`. The guardrails are rules under test, which is what the switch is
  // for; the account is the stakes, and stakes you can switch off are not
  // stakes. `dataUpdatedAt` is when this arrived, which is all the countdowns
  // use the local clock for (see `remainingMs`).
  const accountQ = useReplayAccount();
  const account = accountQ.data;
  const accountAt = accountQ.dataUpdatedAt;

  // --- the blown flow -------------------------------------------------------
  // Only fetched when there is something to autopsy. The card is composed
  // entirely from these rows — the epoch's equity curve is a cumulative sum over
  // them and its totals are `replayStats.pool` — so nothing new is stored and
  // the server is not asked to re-derive what the history page already draws.
  const dead = !!account && account.status !== "live";
  // The drill needs the same list for its rep counter, so the two conditions
  // share one fetch rather than the mode adding a second query on the same key.
  const attemptsQ = useReplayAttempts({ enabled: dead || drill });
  /** Reps finished today, for the counter on the bar.
   *
   *  Counted off the settled ones only: an `active` row is the rep you are in
   *  the middle of, and a counter that included it would open every draw
   *  claiming you had already done it. Local midnight, because the number is
   *  about your day rather than about the account's (which counts in New York,
   *  see replay_account._et_day). */
  // The rep's journal rows, and the keys its rule checks hang off. Fetched only
  // once the rep is over: the mirror rewrites this attempt's rows on every
  // autosave, so mid-rep the keys are a moving target and any answer filed
  // against one would be filed against a row about to be replaced.
  const repJournalQ = useReplayJournal(drill && repOver ? attemptRec.attempt?.id ?? null : null);
  const saveRules = useSaveRuleChecks(drillPrefs.modelId);
  const repsToday = useMemo(() => {
    if (!drill) return 0;
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    return (attemptsQ.data?.attempts ?? []).filter(
      (a) =>
        (a.mode ?? "replay") === "drill" &&
        a.status !== "active" &&
        new Date(a.created_at).getTime() >= midnight.getTime(),
    ).length;
  }, [attemptsQ.data, drill]);
  const writeCause = useWriteCause();

  /** Open the death sitting in review mode.
   *
   *  Through a reload rather than by swapping `sel`, and for a reason that is
   *  not laziness: entering review mode has to leave the recorder *unarmed*, and
   *  it is armed right now for whatever session is on screen. A fresh mount is
   *  the only way to guarantee that without threading a disarm through the tape
   *  build. The marks are already on disk when the page comes back. */
  const openReview = useCallback(
    (attemptId: string) => {
      const a = attemptsQ.data?.attempts.find((r) => r.id === attemptId);
      if (!a) return;
      saveResume({
        symbol: a.symbol,
        date: a.date,
        clockMs: a.started_ms,
        attemptId: a.id,
        // Unused in review mode — every cursor is rebased off its own timestamp.
        contextTicks: 0,
      });
      saveReview({ attemptId: a.id });
      navigate(0);
    },
    [attemptsQ.data, navigate],
  );
  const tickUsd = tickSize * pointValue;
  // Everything the rules and the behaviour strip need, re-derived whenever the
  // simulation is. Cheap: a couple of passes over a day's trades.
  const day = useMemo(() => dayState(guards, trades), [guards, trades]);

  // The day-type readout (lib/dayRead): TIDE and SWING off the trailing tape,
  // EXT off the last few entries. Quantised to 15s of tape so the HUD's ~80ms
  // pushes don't recompute a window scan sixty times a second, and gen-guarded
  // like every other clock reading so a session swap can't read the old day.
  const readClockMs =
    hud.gen === sessGenRef.current ? Math.floor(hud.clockMs / 15_000) * 15_000 : 0;
  const read = useMemo<DayRead | null>(() => {
    const tape = tapeRef.current;
    if (!tape || readClockMs <= 0) return null;
    return dayRead(tape, trades, readClockMs, tickSize);
  }, [readClockMs, trades, tickSize]);
  // The verdict the UI wears flips only on two consecutive agreeing reads. A
  // 5-entry median twitches at one trade a minute; 30 seconds of agreement is
  // the difference between a reading and a mood. Going dark is immediate —
  // withholding a verdict needs no second opinion.
  const prevVerdictRef = useRef<DayVerdict | null>(null);
  const [dayVerdict, setDayVerdict] = useState<DayVerdict | null>(null);
  useEffect(() => {
    const raw = read?.verdict ?? null;
    if (raw === null || raw === prevVerdictRef.current) setDayVerdict(raw);
    prevVerdictRef.current = raw;
  }, [read]);

  // --- helpers --------------------------------------------------------------
  /** The mark: the last print the replay has reached. */
  const markPrice = useCallback((): number => {
    const v = engineRef.current?.lastPriceValue() ?? NaN;
    return Number.isFinite(v) ? v : NaN;
  }, []);

  // Where a fill sits on the grid as it stands now. Read through the ref rather
  // than closed over, so a timeframe switch re-snaps every mark by re-publishing
  // rather than by rebuilding the callback chain.
  const barAt = useCallback<BarAt>(
    (ms) => engineRef.current?.barTimeAt(ms) ?? Math.floor(ms / 1000),
    [],
  );

  // Hand a fresh simulation to the panel and the chart.
  const publish = useCallback((st: SimState, clock: number) => {
    simRef.current = st;
    sigRef.current = simSig(st);
    openRef.current = st.open;
    // Each view is priced in the contract its own order went to, which is what
    // keeps a chip on a micro order reading as micro money after the ticket has
    // been pointed back at the mini.
    const views = workingOrders(st).map((o) => orderView(o, clock, st.open, tapePointValue));
    // The trades array is appended to in place by the stepper, and so is the
    // position — a scale-in moves the size and the average on the object React
    // is already holding. Hand over copies or it sees the same reference and
    // skips the render.
    setTrades(st.trades.slice());
    setOpenPos(st.open && { ...st.open });
    // Every change to the simulation passes through here, which makes this the
    // one place a fill can be noticed — the engine has no incremental fill
    // callback to hook, by design (see replaySim), so a fill *is* a difference
    // between two published states.
    cuesRef.current.observe(simMark(st));
    setWorking(views);
    chartRef.current?.setPosition(st.open ? posLine(st.open, barAt, tapePointValue) : null);
    chartRef.current?.setOrders(views);
    const marks = st.trades.map((t) => tradeMark(t, barAt));
    chartRef.current?.setTrades(marks);
    // Every other pane gets exactly the same three things. It costs nothing to
    // draw them there: a position and a working order are horizontal lines, so
    // they carry a price and nothing about where they sit in the tape, and the
    // same view renders on a 1-minute pane and an hourly one unchanged.
    const pos = st.open ? posLine(st.open, barAt, tapePointValue) : null;
    eachExtra((c) => {
      c.setPosition(pos);
      c.setOrders(views);
      c.setTrades(marks);
    });
    // Every path that changes the simulation ends here, which makes this the one
    // place the recorder has to be told. It writes nothing until a fill has
    // happened, and nothing again until something changes.
    recordAttempt(logRef.current, st.trades, st.open != null, clock);
    // And the one place the bookmark has to be moved. The timer alone would not
    // do: it samples the clock every few seconds, and a fill inside that window
    // would leave the bookmark sitting *before* a booked trade — so coming back
    // would rewind past it and un-happen it, which is the one thing this page
    // refuses to do silently (see `seekTo`'s rewind record). Pinning the
    // bookmark to the moment something resolved makes that impossible, and it
    // follows a rewind straight back down for the same reason.
    //
    // Cheap enough to sit here: this runs when a fill resolves or you do
    // something, not per frame.
    writeResume();
  }, [barAt, eachExtra, recordAttempt, tapePointValue, writeResume]);

  // Re-derive everything from the log. Every user action goes through here: an
  // action is rare enough that one pass over the tape costs nothing, and it
  // means there is no second, optimistic code path that could disagree with
  // what a later scrub would produce.
  const rebuild = useCallback(
    (clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      publish(runSim(tape, logRef.current, clock, fillCfg), clock);
    },
    [publish, fillCfg],
  );

  // A refusal is a reply, not a state. Left up it would read as a property of
  // the ticket rather than as an answer to the last click.
  useEffect(() => {
    if (!refused) return;
    const t = window.setTimeout(() => setRefused(null), 7000);
    return () => window.clearTimeout(t);
  }, [refused]);

  // The server refusing to open the sitting, said in the same place the client's
  // own refusals are said. The client gate above fires first and this should
  // never be reached — but "should never be reached" is exactly the condition
  // under which a silent failure costs a session: the fills would go on
  // happening on a page that had quietly stopped recording them.
  const { refusal: attemptRefusal, clearRefusal } = attemptRec;
  useEffect(() => {
    if (!attemptRefusal) return;
    setRefused(attemptRefusal);
    playCue("canceled");
    clearRefusal();
  }, [attemptRefusal, clearRefusal]);

  const openPnl = useCallback(
    (lastPrice: number): number => {
      const o = openRef.current;
      if (!o || !Number.isFinite(lastPrice)) return 0;
      const dir = o.side === "long" ? 1 : -1;
      // The position's contract, not the ticket's. They are the same until the
      // ticket is re-pointed after a fill, and that is precisely the moment this
      // number must not move: what is held is what is held.
      const pv = tapePointValue / (o.micro ? MICRO_RATIO : 1);
      return (lastPrice - o.entryPrice) * dir * pv * o.size;
    },
    [tapePointValue],
  );

  const pushHud = useCallback(
    (lastPrice: number, clockMs: number, force = false) => {
      const now = performance.now();
      if (!force && now - lastHudRef.current < 80) return;
      lastHudRef.current = now;
      const { ib, range } = geoRef.current;
      setHud({ clockMs, lastPrice, openPnl: openPnl(lastPrice), gen: sessGenRef.current, ib, range });
    },
    [openPnl],
  );

  // Fold a played tick range into the running simulation, and only re-render the
  // panel on the step where something actually resolved.
  const advanceSim = useCallback(
    (from: number, to: number, clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      const st = simRef.current;
      stepSim(tape, logRef.current, st, from, to, clock, fillCfg);
      if (simSig(st) !== sigRef.current) publish(st, clock);
    },
    [fillCfg, publish],
  );

  // --- the context pane -----------------------------------------------------
  // A second chart beside the trading one: its own ReplayEngine on its own
  // bucketing over the same tape, read-only, and repainting only when a bar
  // closes on *it*.
  //
  // That gate is the whole reason the pane is affordable, and it was measured
  // rather than assumed (tools/browser/README): a second pane repainting every
  // frame costs about a quarter of the frame rate, and one gated on bar close
  // costs nothing measurable. A 5m pane closing a bar every five minutes of
  // session time simply has nothing to redraw in between — the forming bar's
  // last tick is not a reading you take off a context chart.
  //
  // Deliberately a second *engine* rather than a second bucketing threaded
  // through the first. An engine over a tape it shares by reference costs ~1MB
  // and re-derives in ~60ms, so here the cheap thing and the simple thing are
  // the same thing, and the first one stays a class about one grid.

  // --- the divider ----------------------------------------------------------
  // Dragged against the split container's own box rather than against the
  // window: the chart card is inset by the rail and whatever chrome the viewport
  // wrapped, so a percentage taken off clientX would drift from the pointer by
  // exactly that inset. Pointer capture is what keeps the drag alive when the
  // pointer outruns a 7px target, which at speed it always does.
  const splitRef = useRef<HTMLDivElement>(null);
  /** Which divider is being dragged, or null. The axis rather than a boolean:
   *  a layout can have one of each, and only the one under the pointer should
   *  light up. */
  const [dragging, setDragging] = useState<"v" | "h" | null>(null);
  const startSplitDrag = useCallback((e: React.PointerEvent<HTMLDivElement>, axis: "v" | "h") => {
    const host = splitRef.current;
    if (!host) return;
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    setDragging(axis);
    const onMove = (ev: PointerEvent) => {
      const box = host.getBoundingClientRect();
      const span = axis === "v" ? box.width : box.height;
      if (span <= 0) return;
      const at = axis === "v" ? ev.clientX - box.left : ev.clientY - box.top;
      const pct = clampSplit((at / span) * 100);
      if (axis === "v") setSplitPct(pct);
      else setSplitPctY(pct);
    };
    const onUp = () => {
      setDragging(null);
      el.releasePointerCapture?.(e.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
    };
    // On the captured element, not the window: capture routes every move here
    // until it is released, and a pointercancel (the OS taking the pointer for a
    // gesture of its own) is what stops a drag that would otherwise never end.
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
  }, []);

  /** Repaint every extra pane from its own engine, as of the clock the page's
   *  chart is at. Every path that re-derives the primary calls this too — the
   *  panes are one session, and a context chart showing a different session than
   *  the one under it is worse than no context chart. */
  const resyncPane = useCallback(
    (reframe?: boolean | "follow") => {
      eachExtra((c, i) => {
        const e = extraEngines.current[i];
        if (e) c.setSnapshot(e.snapshotTo(clockRef.current), { reframe });
      });
    },
    [eachExtra],
  );

  // --- playback loop --------------------------------------------------------
  const stop = useCallback(() => {
    playingRef.current = false;
    setPlaying(false);
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    lastTsRef.current = null;
  }, []);

  const frame = useCallback(
    (ts: number) => {
      if (!playingRef.current || !engineRef.current || !sessionRef.current) return;
      const last = lastTsRef.current ?? ts;
      lastTsRef.current = ts;
      const dtReal = ts - last;
      // The only clock-specific step in the loop. Everything below this line is
      // the same work whether the tape is a finished session or one still
      // arriving — which is why the seam is here and not inside ReplayEngine.
      const src = sourceRef.current;
      const { clock, atEnd } = src.clockFor(
        clockRef.current, dtReal, speedRef.current,
      );
      const r = engineRef.current.advance(clock);
      chartRef.current?.applyStep(r);
      // Every extra pane advances every frame regardless — the engine step is
      // 0.006ms, and skipping it would leave its bars behind the clock. Only the
      // *draw* is gated, because the draw is the part that costs anything: a
      // pane repainting per frame costs about 65% of a core, and four of them
      // would spend the frame budget on charts nobody is trading off.
      for (let i = 1; i < paneCountRef.current; i++) {
        const e = extraEngines.current[i];
        if (!e) continue;
        const step = e.advance(clock);
        if (step.newBar || ts - (extraDrawn.current[i] ?? 0) >= PANE_DRAW_MS) {
          extraDrawn.current[i] = ts;
          extraCharts.current[i]?.applyStep(step);
        }
      }
      geoRef.current = { ib: r.ib, range: r.range };
      clockRef.current = clock;
      advanceSim(r.fromIdx, r.toIdx, clock);
      pushHud(r.lastPrice, clock, atEnd);
      if (atEnd && src.stopAtEnd) {
        stop();
        return;
      }
      rafRef.current = requestAnimationFrame(frame);
    },
    [advanceSim, pushHud, stop],
  );

  const play = useCallback(() => {
    if (!ready || playingRef.current) return;
    if (clockRef.current >= (sessionRef.current?.session_end_ms ?? 0)) return;
    playingRef.current = true;
    setPlaying(true);
    lastTsRef.current = null;
    rafRef.current = requestAnimationFrame(frame);
  }, [frame, ready]);

  // --- load / seek ----------------------------------------------------------
  const seekTo = useCallback(
    (clockMs: number) => {
      const s = sessionRef.current;
      const eng = engineRef.current;
      if (!s || !eng) return;
      // A source that can't seek must not reach this path at all. Seeking
      // re-takes the tape from tick 0 and truncates the order log at the new
      // clock — coherent only when the tape is finished and can be replayed.
      // On a live tape the log is append-only, and "un-happening" a fill that
      // really occurred would be a lie about what the session did.
      if (!sourceRef.current.canSeek) return;
      // **Forward only in a drill.** A rewound rep is a rep that already knew
      // the answer, and the base rate this mode exists to measure cannot
      // survive being pooled with those. Pausing, stepping forward and changing
      // speed are all still yours — the refusal is specifically about going
      // back over tape you have already traded through.
      //
      // Here rather than on the buttons because this is the one choke point
      // every backward move goes through: ⏪, the `,` key, the transport's
      // scrubber and the start-time jump. A guard on each of them is a guard
      // that the next one added will not have.
      if (drill && clockMs < clockRef.current) {
        setRefused("Not in a drill — a rep only runs forwards.");
        return;
      }
      stop();
      const clamped = Math.max(s.session_start_ms, Math.min(s.session_end_ms, clockMs));
      // What the seek is about to erase, read before it is erased.
      const from = clockRef.current;
      const had = simRef.current.trades.slice();
      const hadOpen = simRef.current.open != null;
      // Truncate the action log — everything after the new clock un-happens:
      // an order you hadn't placed is gone, a bracket you hadn't dragged is back
      // where it was, an order you hadn't cancelled is working again.
      logRef.current = truncateLog(logRef.current, clamped);
      const snap = eng.snapshotTo(clamped);
      // The view follows the clock at the zoom the user set: a seek is a move
      // through time, not a request to be put back at the default bar spacing.
      chartRef.current?.setSnapshot(snap, { reframe: "follow" });
      resyncPane("follow");
      // A rewind un-develops the day's range along with everything else: the
      // snapshot is re-derived from tick zero, so the extremes are whatever had
      // actually printed by the new clock.
      geoRef.current = { ib: snap.ib, range: snap.range };
      clockRef.current = clamped;
      rebuild(clamped);
      // A seek is not something happening, it is a move to somewhere it already
      // happened — so the blotter on the other side of it is adopted in silence,
      // in both directions. Forward past a resting order does fill it, and
      // announcing that would announce a fill you jumped over rather than
      // watched.
      cuesRef.current.sync(simMark(simRef.current));
      pushHud(snap.lastPrice, clamped, true);
      // Going back past a fill is a do-over, and the record says so. The
      // surviving trades are a prefix of what there was — the walk is
      // deterministic — so whatever is past that prefix is what un-happened. A
      // position rewound out of before it ever closed books no trade, and
      // counts just the same.
      if (clamped < from) {
        const dropped = had.slice(simRef.current.trades.length);
        if (dropped.length || (hadOpen && !simRef.current.open))
          noteRewind(from, clamped, dropped);
      }
    },
    [drill, noteRewind, pushHud, rebuild, stop],
  );

  // The chart's ⚓ tool moved. The anchored band develops from the tape like the
  // two session anchors do, so the engine owns it and the picture is rebuilt
  // through the one path that already exists for that — without re-framing the
  // viewport, since placing an anchor isn't a move through time.
  const setAnchor = useCallback((barTime: number | null) => {
    const eng = engineRef.current;
    if (!eng) return;
    eng.setAnchor(barTime);
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
  }, []);

  /** The same, for a pane that is not pane 0. The anchored VWAP is a property
   *  of the engine that draws it, so a ⚓ dropped on the 15-minute pane anchors
   *  the 15-minute VWAP and leaves every other pane alone — which is the only
   *  reading of the gesture that makes sense once there are four charts. */
  const ticket = useMemo(
    () => ({ size, stopTicks, targetTicks }),
    [size, stopTicks, targetTicks],
  );
  /** The ticket, changed from a chart. One handler for every pane: size and the
   *  bracket belong to the page, so the number in a long-press ticket and the
   *  number a dock button sends are the same number whichever chart you are on. */
  const changeTicket = useCallback(
    (t: { size: number; stopTicks: number; targetTicks: number }) => {
      setSize(t.size);
      applyStop(t.stopTicks);
      applyTarget(t.targetTicks);
    },
    [applyStop, applyTarget],
  );

  const setPaneAnchor = useCallback((i: number, barTime: number | null) => {
    const eng = extraEngines.current[i];
    if (!eng) return;
    eng.setAnchor(barTime);
    extraCharts.current[i]?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
  }, []);

  /**
   * Draw the same tape as different bars.
   *
   * A timeframe is a bucketing rule, so this is a re-derivation and not a
   * reload: the engine re-runs the tape from tick zero onto the new grid, and the
   * simulation is re-published so every fill mark re-snaps to the bar it now
   * belongs to. The clock, the log and therefore every trade are untouched —
   * fills come off tick indices, so what you made on this session is the same
   * number on 30s as on 1h.
   *
   * Reframed on purpose. Bars are a different width now, so the viewport is
   * showing a different amount of session than the user chose; snapping back to
   * the tail is less surprising than landing somewhere arbitrary. The ruler goes
   * with it — a measurement reads "n bars" and those bars no longer exist.
   *
   * A running replay keeps running. The rebuild has to pause the frame loop
   * while it re-derives (it is swapping the picture out from under it), but
   * changing how the tape is drawn is not a decision to stop watching it — and a
   * tape that stopped every time you looked at another bar size would have you
   * pressing Play instead of reading the market.
   */
  const changeTimeframe = useCallback(
    (id: string) => {
      setTfId(id);
      const eng = engineRef.current;
      if (!eng) return;
      const wasPlaying = playingRef.current;
      stop();
      eng.setTimeframe(timeframeById(id));
      chartRef.current?.clearRuler();
      chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current));
      rebuild(clockRef.current);
      if (wasPlaying) play();
    },
    [play, rebuild, stop],
  );

  /** The bucketing of whichever pane the chrome is acting on.
   *
   *  This is what makes one timeframe control enough for four charts, and it is
   *  why the per-pane pickers that phase 2 left sitting on each canvas are gone:
   *  the bar could not reach past pane 0, so every extra pane had to carry a
   *  second copy of the same control. Pane 0's bucketing is the page's own
   *  `timeframe` — it drives the engine the fills come out of — so the two
   *  halves are genuinely different operations, not one with an index. */
  const changePaneTimeframe = useCallback(
    (i: number, id: string) => {
      if (i === 0) {
        changeTimeframe(id);
        return;
      }
      setPaneTfIds((prev) => prev.map((t, j) => (j === i ? id : t)));
    },
    [changeTimeframe],
  );

  /**
   * Change what counts as a big trade.
   *
   * A re-derivation like the timeframe, and for the same reason: which sweeps
   * clear the threshold is a question about the tape, so the engine re-runs it
   * from tick zero rather than the chart filtering marks it was already given.
   * The clock, the log and the bars are untouched — so unlike a timeframe change
   * this one leaves the viewport alone, and there is no ruler to clear.
   */
  const changeBigLots = useCallback((lots: number) => {
    setBigLots(lots);
    const eng = engineRef.current;
    if (!eng) return;
    eng.setBigLots(lots);
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
    extraEngines.current.forEach((e) => e?.setBigLots(lots));
    resyncPane(false);
  }, [resyncPane]);

  /**
   * Change what selects a tape event — one knob at a time, the whole tuning
   * re-derived.
   *
   * Exactly the path above, for a stronger version of the same reason: a burst is
   * a cluster of sweeps and an absorption is scored against a running median, so
   * neither can be recovered by filtering what a different setting published. The
   * clock and the log are untouched; only the bands change.
   */
  const changeEvTuning = useCallback((patch: Partial<EventTuning>) => {
    setEvTuning((t) => ({ ...t, ...patch }));
    const eng = engineRef.current;
    if (!eng) return;
    eng.setEventTuning(patch);
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
    extraEngines.current.forEach((e) => e?.setEventTuning(patch));
    resyncPane(false);
  }, [resyncPane]);

  // The event layer as the chart takes it: what selected the bands (for the
  // legend rows, which quote the thresholds their counts were counted at) and
  // how they draw. Its presence is what offers the layer at all — the Live chart
  // passes nothing and has no event rows.
  const eventOverlay = useMemo(
    () => ({ tuning: evTuning, style: { labelSt: evLabelSt, fill: evFill }, marginal: evMarginal }),
    [evTuning, evLabelSt, evFill, evMarginal],
  );

  // The chart's own knobs, hung off the legend row each one tunes (rendered by
  // IndicatorSettings, behind the "…" on the row). They used to be a line of
  // selects in the setup row above the chart, with nothing but reading order
  // saying which select belonged to which layer — and the setup row folds away
  // in fullscreen, so the mode this page is most for was the one mode where the
  // prominence floor and the event threshold couldn't be reached at all.
  //
  // The state stays here. It is what the chart's props are fed from, and two of
  // these knobs feed two rows each, so what goes down is a presentation of the
  // state rather than a second copy of it.
  //
  // "Prior days" is deliberately not among them: it fetches whole tapes instead
  // of re-reading ones already in memory, and it is the switch that decides the
  // composite row exists at all — a knob that can delete its own panel has to
  // live somewhere else. It stays in the setup row with the rest of the pre-run
  // configuration.
  const indicatorSettings = useMemo<IndicatorSettingsMap>(
    () =>
      buildChartKnobs({
        bigLots,
        onBigLots: changeBigLots,
        nodeProm,
        onNodeProm: setNodeProm,
        modernVwap: { params: mvParams, onChange: patchMv },
        composite,
        onComposite: setComposite,
        compositeSpan,
        onCompositeSpan: setCompositeSpan,
        compositeNote: `Built from the ${historyDays} prior session${historyDays === 1 ? "" : "s"} loaded — "Prior days" in the setup row, since each one is a whole tape to fetch.`,
        events: {
          tuning: evTuning,
          labelSt: evLabelSt,
          fill: evFill,
          marginal: evMarginal,
          onTuning: changeEvTuning,
          onLabelSt: setEvLabelSt,
          onFill: setEvFill,
          onMarginal: setEvMarginal,
        },
      }),
    [
      bigLots,
      changeBigLots,
      changeEvTuning,
      composite,
      compositeSpan,
      evFill,
      evLabelSt,
      evMarginal,
      evTuning,
      historyDays,
      mvParams,
      nodeProm,
      patchMv,
    ],
  );

  // The context days that may actually be glued on: in wall-clock order, no
  // overlaps, and all of them wholly before the session. A day that fails the
  // test is dropped rather than drawn — bars out of order are a chart the
  // library refuses, and one missing Tuesday is a cheaper failure than that.
  const contextDays = useMemo(() => {
    const start = sessionQ.data?.session_start_ms;
    if (start == null) return [];
    const out: HistDay[] = [];
    let prevEnd = -Infinity;
    for (const d of histQ.days) {
      const t = d.tape;
      if (t.n === 0 || t.t[0] <= prevEnd || t.t[t.n - 1] > start) continue;
      out.push(d);
      prevEnd = t.t[t.n - 1];
    }
    return out;
  }, [histQ.days, sessionQ.data]);
  const contextTapes = useMemo(() => contextDays.map((d) => d.tape), [contextDays]);

  // Where each context day sits on the glued tape — the spans the composite is
  // built over, and the only thing it is built over. The session being replayed
  // is deliberately not among them: a composite today fed would be a level today
  // could never violate.
  //
  // How much of a day counts is the span setting. Under "globex" it starts at
  // the day's first tick, which is the 18:00 Globex open the evening before (the
  // session payload glues on | rth | post in that order); under "rth" it starts
  // at the bell. Either way it ends at the 16:00 close — the post-close hour
  // belongs to the *next* day's overnight, and counting it here would put the
  // same ticks in two days. A day whose overnight was never cached simply starts
  // at its bell under both spans, which is the honest thing for it to do.
  const contextRanges = useMemo(() => {
    const out: TapeRange[] = [];
    let off = 0;
    for (const d of contextDays) {
      const t = d.tape;
      const i0 = compositeSpan === "globex" ? 0 : firstAt(t.t, t.n, d.rthOpenMs);
      const i1 = firstAt(t.t, t.n, d.rthCloseMs) - 1;
      if (i1 >= i0) out.push({ i0: off + i0, i1: off + i1 });
      off += t.n;
    }
    return out;
  }, [contextDays, compositeSpan]);

  const contextRangesRef = useRef(contextRanges);
  contextRangesRef.current = contextRanges;

  /** What the engine needs to run the weekly anchor back over the context days:
   *  each day's share of the glued tape and its own seed. Same list, same order
   *  as the tapes that were glued — that is what makes the tick counts index
   *  bounds. */
  const engineContext = useMemo(
    () => contextDays.map((d) => ({ ticks: d.tape.n, weeklySeed: d.weeklySeed })),
    [contextDays],
  );
  const engineContextRef = useRef(engineContext);
  engineContextRef.current = engineContext;

  /**
   * Stand the context pane up: its own engine over the tape in hand, handed to
   * its chart.
   *
   * Deliberately idempotent and deliberately called from both sides, because
   * neither side can be relied on to be last. The page learns about a tape in an
   * effect; the pane's chart is built in an effect of the chart's own; and React
   * is free to throw that chart away and build another (StrictMode does it to
   * every mount in development, and a pane appearing mid-replay does it for
   * real). The first version pushed once, from the page, and lost the race —
   * the pane came up drawn on a chart that no longer existed, which reads as a
   * blank pane and was caught only by measuring the ink on it.
   *
   * So: this runs when the pane's settings change *and* whenever the chart says
   * it has just been (re)built, and re-running it costs one ~60ms re-derivation
   * over a tape that is already in memory.
   */
  const primePane = useCallback((only?: number) => {
    const tape = tapeRef.current;
    const data = sessionRef.current;
    if (!tape || !data) return;
    for (let i = 1; i < paneCountRef.current; i++) {
      if (only != null && only !== i) continue;
      const chart = extraCharts.current[i];
      if (!chart) continue;
      const e = new ReplayEngine(tape, data, paneTfsRef.current[i], engineContextRef.current);
      e.setBigLots(bigLotsRef.current);
      e.setEventTuning(evTuningRef.current);
      extraEngines.current[i] = e;
      chart.setTape(tape, { contextRanges: contextRangesRef.current });
      chart.setSnapshot(e.snapshotTo(clockRef.current));
      extraDrawn.current[i] = 0;
    }
  }, []);

  useEffect(() => {
    // Engines belonging to panes the layout no longer draws go now rather than
    // when their chart unmounts: an engine is a whole tape's worth of derived
    // bars, and one kept alive for a pane nobody can see is a leak that only
    // shows up as memory.
    for (let i = paneCount; i < extraEngines.current.length; i++) {
      extraEngines.current[i] = null;
    }
    primePane();
  }, [paneCount, paneTfKey, contextRanges, primePane]);

  // Decode + build the engine whenever a new session lands — or whenever the
  // context days in front of it change, which is the same construction with the
  // replay left where it stands.
  useEffect(() => {
    const data = sessionQ.data;
    if (!data) return;
    const fresh = sessionRef.current !== data;
    const same =
      contextTapes.length === histTapesRef.current.length &&
      contextTapes.every((t, i) => t === histTapesRef.current[i]);
    if (!fresh && same) return;
    // A bookmark in hand has a log to put back, and it is still in flight.
    // Building the tape without it would show a fresh, empty session for as long
    // as the fetch takes and then yank the trades onto it — and would leave a
    // gap in which a fill could open a *second* attempt on a day that already
    // has one. Hold off instead: the query settles either way, so this is a wait
    // and never a deadlock.
    if (fresh && pendingRef.current && !resumeSettled) return;
    // A context change mid-replay is not a reason to stop watching: the clock
    // doesn't move, so playback picks up where the rebuild left it.
    const wasPlaying = playingRef.current;
    stop();
    // The session's own ticks are decoded once. Re-gluing is an array copy;
    // re-decoding would be a million prints for a picture that didn't change.
    const sessTape = fresh || !sessTapeRef.current ? decodeTape(data) : sessTapeRef.current;
    const tape = contextTapes.length ? concatTapes([...contextTapes, sessTape]) : sessTape;
    const ctxTicks = contextTapes.reduce((a, t) => a + t.n, 0);
    const shift = ctxTicks - histTapesRef.current.reduce((a, t) => a + t.n, 0);
    sessTapeRef.current = sessTape;
    histTapesRef.current = contextTapes;
    tapeRef.current = tape;
    sessionRef.current = data;
    // A rep runs to the RTH close, not to the end of the tape. The tape carries
    // the post-close hour too (the `post` segment), and a drill that kept
    // playing into it would be measuring a market the rep is not about. One
    // number, set on the source, so the frame loop stops there on its own
    // rather than every reader of the clock needing to know.
    sourceRef.current = replaySource(endOf(data, drill));
    if (fresh) {
      // Before anything reads a clock off this tape: every HUD push from here on
      // belongs to this session, and the clock-keyed effects check the stamp.
      sessGenRef.current += 1;
      endedRef.current = false;
    }
    // The ⚓ is the user's, and it is placed on a bar time — which the context
    // days don't move. Carried across the rebuild rather than re-placed.
    const anchor = fresh ? null : (engineRef.current?.anchor() ?? null);
    engineRef.current = new ReplayEngine(tape, data, tfRef.current, engineContext);
    engineRef.current.setBigLots(bigLotsRef.current);
    engineRef.current.setEventTuning(evTuningRef.current);
    if (anchor != null) engineRef.current.setAnchor(anchor);
    // The chart profiles bar ranges straight off the tape, so it needs the same
    // typed arrays the engine got. On a new day that also resets its hand-drawn
    // tools; on a context change it must not — the same session with more days
    // in front of it is the same chart.
    chartRef.current?.setTape(tape, { keepTools: !fresh, contextRanges });
    // What the bookmark asks to be put back. Read — and spent — inside the same
    // build that makes the tape, rather than by an effect afterwards, so the
    // session is never briefly playable with the wrong log under it.
    let resumed: { clock: number; log: Log; detail: AttemptDetail | null } | null = null;
    const bookmark = fresh ? pendingRef.current : null;
    if (bookmark) {
      // Spent whether or not it fits: a bookmark that doesn't match this tape is
      // one to let go of, not one to keep trying.
      leaveResume();
      if (bookmark.symbol === data.symbol && bookmark.date === data.date) {
        const d = resumeDetailRef.current;
        // The stored cursors only mean anything if the session under them is
        // still the tape it was — the tick cache is not immutable, and the
        // 16:00-17:00 gap fix re-fetched 352 sessions and moved every index in
        // them — and if the fills would be resolved by the same rules. Either
        // check failing costs the trades, not the day: the clock still goes back
        // where you left it and you carry on reading from there.
        const usable =
          d != null &&
          d.id === bookmark.attemptId &&
          d.symbol === data.symbol &&
          d.date === data.date &&
          d.engine_version === SIM_ENGINE_VERSION &&
          d.tape?.n === data.n &&
          d.tape?.t0 === data.session_start_ms &&
          d.tape?.end === data.session_end_ms;
        resumed = {
          clock: Math.max(
            data.session_start_ms,
            Math.min(data.session_end_ms, bookmark.clockMs),
          ),
          // `idx` counts from the start of the *glued* tape, so a visit carrying
          // a different number of context days has to re-base every cursor in
          // the log — the same correction the `shift` branch below makes when
          // the day count changes mid-replay.
          // Reviewing: rebase every cursor off its own timestamp instead.
          // Nothing recorded how many context days were glued in front of the
          // log when it was written, and the history page — which is where a
          // review is started from — has no way to know. `ms` is the primary
          // record and `idx` is an index into a reading choice, so the search
          // is exact where the arithmetic would be a guess.
          log: !usable
            ? newLog()
            : reviewingRef.current
              ? rebaseLog(d.log, tape)
              : shiftLog(d.log, ctxTicks - bookmark.contextTicks),
          detail: usable ? d : null,
        };
      }
    }
    if (fresh) {
      logRef.current = resumed?.log ?? newLog();
      simRef.current = newSim();
      sigRef.current = simSig(simRef.current);
      openRef.current = null;
      // Past every id a restored log already used, or the next order placed
      // would answer to the same number as one that is still working.
      idRef.current = logRef.current.orders.reduce((a, o) => Math.max(a, o.id + 1), 1);
      setTrades([]);
      setOpenPos(null);
      setWorking([]);
      // A new tape is a new question — whatever was revealed about the last one
      // doesn't carry. A resumed tape starts unrevealed too, and deliberately: a
      // blind sitting you hadn't unmasked is one you are still in the middle of.
      setRevealed(false);
    } else if (shift) {
      // The tape grew (or shrank) in front of the session, so every cursor the
      // log recorded points a few million prints off. Nothing else in it moves:
      // the clocks and the prices are what they were.
      logRef.current = shiftLog(logRef.current, shift);
    }
    // Honour the chosen start time (falls back to the RTH bell) — unless this
    // session was resumed, in which case the clock you left it at outranks it: a
    // start time is where a *new* sitting begins. A context change is not a move
    // through time either way, so it keeps the clock it had.
    const [h, m] = startTime.split(":").map((x) => parseInt(x, 10));
    // A drill is thrown in at the drawn hour instead. The start time is a
    // setting about where a session begins; a drop is the question the rep is,
    // so it outranks it — and the drill's setup panel doesn't offer the start
    // time at all.
    const startMin = drill && drop != null
      ? drop
      : (Number.isFinite(h) ? h * 60 + m : RTH_OPEN_MIN);
    const offMin = startMin - RTH_OPEN_MIN;
    const clock = !fresh
      ? clockRef.current
      : (resumed?.clock ??
        Math.max(
          data.session_start_ms,
          Math.min(data.session_end_ms, data.rth_open_ms + offMin * 60_000),
        ));
    // The drawn-from bounds, now that there is a session to measure them
    // against. Stored rather than derived later because the histogram has to be
    // able to say a narrow campaign was narrow.
    if (drill) {
      const at = (min: number) => data.rth_open_ms + (min - RTH_OPEN_MIN) * 60_000;
      drillWindowRef.current = {
        from_ms: at(minutesOf(drillRef.current.dropFrom)),
        to_ms: at(minutesOf(drillRef.current.dropTo)),
      };
    }
    const snap = engineRef.current.snapshotTo(clock);
    chartRef.current?.setSnapshot(snap, fresh ? undefined : { reframe: false });
    // The context pane's own engine over the same tape — the same one path a
    // pane switched on mid-replay takes, so there is only one way it is ever
    // built. It reads the refs this effect has just written.
    primePane();
    geoRef.current = { ib: snap.ib, range: snap.range };
    clockRef.current = clock;
    // A sitting resumed at the end of its tape has already had its ending — the
    // attempt was finished when the tape ran out — and the clock-keyed effect
    // below must not close it a second time.
    if (resumed && clock >= data.session_end_ms) endedRef.current = true;
    setReady(true);
    if (fresh) {
      // Point the recorder at this session. Nothing is written yet — an attempt
      // opens on the first fill — but the tape it would be measured against is
      // fingerprinted here, while the payload is in hand.
      //
      // Before the rebuild below, not after: `rebuild` publishes, and publishing
      // is what tells the recorder. Until this call the recorder is still aimed
      // at the session before this one, and a resumed log arriving there would
      // be written against the wrong day.
      // Never in review mode. Not "armed but refusing" — unarmed, so the
      // recorder's own flush returns early on a missing context and there is no
      // path at all from this page to a write. A read-only mode whose safety
      // depends on every caller remembering is one write away from overwriting
      // the sitting it was opened to examine.
      if (!reviewingRef.current) armAttempt({
        symbol: data.symbol,
        root: data.root,
        date: data.date,
        tz: data.tz,
        tape: {
          n: data.n,
          t0: data.session_start_ms,
          end: data.session_end_ms,
          rth_open_ms: data.rth_open_ms,
        },
        prefs: () => ticketRef.current,
        startedMs: clock,
        mode,
        modelId: drill ? drillRef.current.modelId : null,
        dropMs: drill ? clock : null,
        window: drill ? drillWindowRef.current : null,
      });
      // And in a drill, open it here rather than waiting for a fill: a rep you
      // looked at and passed on is the row this mode exists to write. After
      // `arm`, which is what gives it a context to write against.
      if (drill && !reviewingRef.current) openAttempt(logRef.current, clock);
      // A resumed sitting continues the attempt it came from rather than opening
      // a second one on the same day — which the history page would read,
      // correctly by its own rules and wrongly in fact, as a re-run of a session
      // you had already seen the end of.
      const d = reviewingRef.current ? null : resumed?.detail;
      if (d) {
        adoptAttempt(d, {
          log: logRef.current,
          trades: d.trades,
          rewinds: d.rewinds ?? [],
          discarded: d.discarded ?? [],
          // The sitting spans from the first fill of the *first* visit, not from
          // the clock this one opened at.
          startedMs: d.started_ms,
          clockMs: clock,
        });
      }
    }
    // Re-publish what the log says: the position, the working orders and every
    // mark have to come back on a chart that was just handed a new tape — and on
    // a resumed session that log is not empty, so this is also what puts the
    // open position and the resting orders back on it.
    if (!fresh || resumed) rebuild(clock);
    // Silently, in the sound sense: a resumed sitting arrives with its trades
    // already booked and possibly a position on, and none of that is happening
    // now. Whatever the blotter is once it has been re-derived — a restored one
    // or an empty one — is the baseline the first real fill will be heard
    // against.
    cuesRef.current.sync(simMark(simRef.current));
    pushHud(snap.lastPrice, clock, true);
    // Note where this session now stands, without waiting for the timer below:
    // switching day and closing the tab in the same breath should still come
    // back to the day you switched to.
    writeResume();
    if (!fresh && wasPlaying) play();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // `engineContext` is memoed off the same `contextDays` as `contextTapes`, so
    // it changes in lockstep and never adds a rebuild of its own.
  }, [sessionQ.data, contextTapes, contextRanges, engineContext, resumeSettled]);

  // A change of span re-cuts the same days without touching the tape, so the
  // rebuild above sees nothing to do and returns early. Push the new spans on
  // their own: the chart no-ops when they are the ones it already has, so the
  // rebuild's own `setTape` is not doubled up on.
  useEffect(() => {
    chartRef.current?.setContextRanges(contextRanges);
    eachExtra((c) => c.setContextRanges(contextRanges));
  }, [contextRanges, eachExtra]);

  useEffect(() => () => stop(), [stop]);

  // Keep the bookmark on the moving clock.
  //
  // On a timer rather than off the HUD: the clock advances ~12×/second while
  // playing, and the point of this is to survive a tab closing, not to be exact.
  // What a crash costs is bounded by the interval and it costs it to the clock
  // alone — the trades are on the server inside the recorder's own debounce,
  // which is shorter.
  useEffect(() => {
    if (!ready) return;
    const id = window.setInterval(writeResume, RESUME_SAVE_MS);
    // The ordinary ways a sitting ends — switching apps, locking the screen,
    // closing the tab — give nothing else a chance to run, so spend the interval
    // early on the way out. Same hook the recorder flushes its debounce on.
    const onHide = () => {
      if (document.visibilityState === "hidden") writeResume();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onHide);
      // Navigating off the page is leaving off somewhere, and this is the last
      // chance to say where.
      writeResume();
    };
  }, [ready, writeResume]);

  // --- trading actions ------------------------------------------------------
  // All of them do the same two things: append to the log, re-derive. Nothing
  // mutates the simulation directly.
  const append = useCallback(
    (next: Log) => {
      logRef.current = next;
      rebuild(clockRef.current);
    },
    [rebuild],
  );

  // Changing what a fill costs re-derives the sitting under the new rules, the
  // same way every other action does. The log is untouched — what you did is
  // what you did — but each fill it implies is priced again, so switching
  // commission on doesn't leave the trades already booked reading at the old
  // numbers while the next one pays. Waits for the tape: there is nothing to
  // re-derive against until the session is loaded.
  useEffect(() => {
    if (ready) rebuild(clockRef.current);
  }, [fillCfg, ready, rebuild]);

  /** Place an order. `at` is the price its bracket is measured from — the mark
   *  for a market order, the resting price for a limit or a stop. */
  const placeOrder = useCallback(
    (type: OrderType, side: Side, price: number | null, at: number) => {
      const eng = engineRef.current;
      if (!eng) return;
      // The discipline layer, at the one gesture every order path funnels
      // through — the dock, q/w/s, space+click, the long-press ticket and the
      // ＋Order tool all land here, so there is no route around it and no
      // second copy of the rules to fall out of step.
      //
      // Skipped entirely for an order that *reduces*: closing size has no
      // target to be too tight, and a practice rule that could refuse an exit
      // would teach the one habit nobody wants. Skipped wholesale when
      // `REPLAY_GUARDRAILS` is off — the strip still says what the rules would
      // have said, it just does not stand in the way.
      // Review mode. First, above even the account: this sitting already
      // happened, and the one thing a review must not be able to do is change
      // what it is reviewing.
      if (reviewingRef.current) {
        setRefused(
          "this is a review, not a sitting — the trades below already happened and nothing " +
            "here can add to them. File the verdicts and the next sitting opens.",
        );
        playCue("canceled");
        return;
      }
      const reducing = isReducing(openRef.current, side, size);
      // The account comes first, and it comes *outside* `guardsOn`: the
      // guardrails are rules under test and the switch is what makes them
      // testable, but the account is the stakes. It only speaks when there is
      // no sitting open yet — resuming one you are in the middle of is free,
      // and the thing being priced is starting another.
      if (!reducing) {
        const no = accountRefusal(account, attemptIdOf() != null);
        if (no) {
          setRefused(
            no.until
              ? `${no.message} (${fmtWait(remainingMs(account, no.until, accountAt))} to go)`
              : no.message,
          );
          playCue("canceled");
          return;
        }
      }
      if (!reducing && guardsOn) {
        const why =
          dayRefusal(day, false) ??
          shapeRefusal(guards, { stopTicks, targetTicks, size, tickUsd });
        if (why) {
          setRefused(why);
          playCue("canceled");
          return;
        }
      }
      const dir = side === "long" ? 1 : -1;
      const rec: OrderRec = {
        id: idRef.current++,
        type,
        side,
        size,
        ms: clockRef.current,
        idx: eng.cursorIndex(),
        price,
        stop: stopTicks > 0 ? at - dir * stopTicks * tickSize : null,
        target: targetTicks > 0 ? at + dir * targetTicks * tickSize : null,
        // Snapshotted, and resolved to prices here — the ticket is the last
        // place that thinks in ticks. Stamping it on the order is what lets a
        // rebuild reproduce the ladder: settings read live from React state
        // would re-derive the whole session under whatever the ticket says
        // *now*, so changing the trail mid-replay would silently rewrite the
        // stops on trades you already took.
        trail:
          trailTicks > 0
            ? {
                dist: trailTicks * tickSize,
                step: trailStepTicks * tickSize,
                be: trailBeTicks * tickSize,
                beOnly: trailBeOnly,
              }
            : null,
        edits: [],
        cancelMs: null,
        // Which contract this one is being sent to, stamped for exactly the
        // reason the trail above is: the ticket can be pointed elsewhere later,
        // and an order that filled as a micro is worth what a micro is worth
        // whatever the picker says afterwards.
        micro: onMicro,
      };
      // A market order is its own fill and the rebuild below will sound as one —
      // the tick and the chime a few milliseconds apart would read as one
      // stuttering noise. Only an order that goes on to *rest* gets the tick.
      if (type !== "market") playCue("placed");
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
    },
    [account, accountAt, append, attemptIdOf, day, guards, guardsOn, onMicro, size,
     stopTicks, targetTicks, tickSize, tickUsd, trailTicks, trailStepTicks,
     trailBeTicks, trailBeOnly],
  );

  const placeMarket = useCallback(
    (side: Side) => {
      if (!ready) return;
      const px = markPrice();
      if (!Number.isFinite(px)) return;
      placeOrder("market", side, null, px);
    },
    [markPrice, placeOrder, ready],
  );

  /** Rest an order at a price, held one tick clear of the mark on the side its
   *  type belongs on — a marketable resting order would fill on the next print
   *  at a price better than the market, which is not a thing the tape can do. */
  const placeResting = useCallback(
    (price: number, side: Side, type: "limit" | "stop") => {
      if (!ready) return;
      const mk = markPrice();
      if (!Number.isFinite(mk) || !Number.isFinite(price)) return;
      const px = Math.round(price / tickSize) * tickSize;
      const above = type === "stop" ? side === "long" : side === "short";
      const rest = above ? Math.max(px, mk + tickSize) : Math.min(px, mk - tickSize);
      placeOrder(type, side, rest, rest);
    },
    [markPrice, placeOrder, ready, tickSize],
  );

  /**
   * Space + click on the chart, at a price.
   *
   * Which order that is falls out of the geometry, so there is nothing to choose
   * before you click: the left button always places the *passive* order at that
   * price — a bid under the market, an offer over it — and the right button the
   * one that has to be run through, a sell stop under and a buy stop over. So
   * the side you get flips as you cross the market, which is the point: you
   * click the level you want to trade at and the platform works out what kind of
   * order that has to be.
   */
  const placeAt = useCallback(
    (price: number, button: "left" | "right") => {
      const mk = markPrice();
      if (!Number.isFinite(mk)) return;
      const below = price < mk;
      const passive = button === "left";
      const side: Side = passive === below ? "long" : "short";
      placeResting(price, side, passive ? "limit" : "stop");
    },
    [markPrice, placeResting],
  );

  /**
   * The chart's long-press ticket, where the type and the side were named
   * outright. The mirror image of `placeAt`: there the geometry decides what the
   * order has to be, here the user already decided and the geometry only has to
   * agree — the menu greys out the two of the four that can't sit at that price,
   * so anything that arrives here is placeable as asked.
   */
  const placeTyped = useCallback(
    (o: { price: number; type: "limit" | "stop"; side: Side }) =>
      placeResting(o.price, o.side, o.type),
    [placeResting],
  );

  const cancelOrder = useCallback(
    (id: number) => {
      const log = logRef.current;
      if (log.orders.some((o) => o.id === id && o.cancelMs == null)) playCue("canceled");
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id && o.cancelMs == null ? { ...o, cancelMs: clockRef.current } : o,
        ),
      });
    },
    [append],
  );

  // A level was dragged on the chart — a working order's resting price or either
  // bracket leg, before or after the fill. The chart has already drawn it where
  // it landed (and clamped it somewhere it couldn't fill on the spot), so this
  // only has to record *when* it moved: the log is what a rewind, and the next
  // pass over the tape, read back.
  const editOrder = useCallback(
    (id: number, next: { price: number | null; stop: number | null; target: number | null }) => {
      const log = logRef.current;
      // Once per landed drag, not per pixel: the chart reports a move on release
      // (see ReplayChart's pointer-up), which is what makes this survivable as a
      // sound at all.
      playCue("changed");
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id ? { ...o, edits: [...o.edits, { ms: clockRef.current, ...next }] } : o,
        ),
      });
    },
    [append],
  );

  // The open position's bracket was dragged. Its own channel in the log, not an
  // edit on the order that opened the position: with several fills making up one
  // position, "the stop" belongs to the position rather than to any of them.
  const moveBracket = useCallback(
    (b: { stop: number | null; target: number | null }) => {
      if (!openRef.current) return;
      playCue("changed");
      const log = logRef.current;
      append({ ...log, brackets: [...log.brackets, { ms: clockRef.current, ...b }] });
    },
    [append],
  );

  const moveOrder = useCallback(
    (o: { id: number; price: number; stop: number | null; target: number | null }) =>
      editOrder(o.id, { price: o.price, stop: o.stop, target: o.target }),
    [editOrder],
  );

  const closeManual = useCallback(() => {
    if (!openRef.current) return;
    const log = logRef.current;
    append({ ...log, closes: [...log.closes, { ms: clockRef.current }] });
    pushHud(markPrice(), clockRef.current, true);
  }, [append, markPrice, pushHud]);


  /**
   * Everything off: the position at the last print, and every order still
   * working with it.
   *
   * One append rather than a close followed by n cancels, so the whole thing is
   * a single point in the log — a rewind either lands before it and nothing came
   * off, or after it and everything did. Half a flatten is not a state the
   * replay should be able to sit in.
   */
  const closeAll = useCallback(() => {
    const live = new Set(workingOrders(simRef.current).map((o) => o.id));
    const hadPos = openRef.current != null;
    if (!hadPos && live.size === 0) return;
    // One flatten is one event, and if it took a position off, *that* is the
    // event — the exit cue follows from the rebuild below. Only a flatten that
    // did nothing but pull orders announces itself as a cancel; announcing both
    // would make the more important half the one you hear second, or not at all
    // (see the anti-stack guard in playCue).
    if (!hadPos) playCue("canceled");
    const log = logRef.current;
    const ms = clockRef.current;
    append({
      ...log,
      orders: live.size
        ? log.orders.map((o) => (live.has(o.id) ? { ...o, cancelMs: ms } : o))
        : log.orders,
      closes: hadPos ? [...log.closes, { ms }] : log.closes,
    });
    pushHud(markPrice(), ms, true);
  }, [append, markPrice, pushHud]);

  /** End the sitting by hand. Anything still on comes off at the last print
   *  first: an attempt whose net leaves out what you were carrying is not the
   *  sitting you had. Trading on afterwards simply reopens it. */
  const endAttempt = useCallback(() => {
    endedRef.current = true;
    if (openRef.current) closeManual();
    void finishAttempt();
  }, [closeManual, finishAttempt]);

  /** End the rep by hand — the drill's version of the same thing.
   *
   *  It stops the tape and reveals the day, which `endAttempt` on its own does
   *  not: reaching the bell does both through the effect below, and pressing
   *  the button has to leave you in the same state as sitting there until it
   *  would have. */
  const endRep = useCallback(() => {
    stop();
    endAttempt();
    setRevealed(true);
    setRepOver(true);
  }, [endAttempt, stop]);

  // The daily stop, acting rather than refusing.
  //
  // Watches equity — realised plus what the open position is currently down —
  // because the account's drawdown does not wait for a loss to be booked. It
  // closes the position the way `closeManual` does, by appending to the log, so
  // a rewind un-does it like any other action and the sim stays a pure fold over
  // what happened.
  //
  // Fires once per crossing: `openRef` going null is the reset, so a position
  // re-opened after the stop can be closed again if it also breaches. Nothing
  // stops that entry being placed, because the day lock already refuses it.
  //
  // It lands a beat late. `hud.openPnl` arrives on the throttled ~80ms tick,
  // which at speed 30 is a couple of seconds of market time — fine to rehearse
  // against, not a number to quote.
  const autoClosedRef = useRef(false);
  useEffect(() => {
    if (!openRef.current) {
      autoClosedRef.current = false;
      return;
    }
    if (autoClosedRef.current) return;
    // The account's floor first, and always — it is not under the `guardsOn`
    // switch, and unlike the daily stop it does not lift tomorrow. Hitting it
    // does not just close the position: the sitting is over, because an account
    // that has reached its floor has no next trade to take.
    const dead = accountStop(account, day, hud.openPnl, true);
    if (dead) {
      autoClosedRef.current = true;
      setRefused(dead);
      playCue("canceled");
      endAttempt();
      return;
    }
    if (!guardsOn) return;
    const why = equityStop(guards, day, hud.openPnl, true);
    if (!why) return;
    autoClosedRef.current = true;
    setRefused(why);
    playCue("canceled");
    closeManual();
  }, [account, closeManual, day, endAttempt, guards, guardsOn, hud.openPnl]);

  // Running out of tape ends the replay, and the answer comes with it. Keyed on
  // the clock rather than wired into the playback loop so it holds however the
  // end was reached — played to, stepped to, or scrubbed to. It ends the
  // attempt on the same terms, for the same reason: the sitting is over.
  useEffect(() => {
    const s = sessionRef.current;
    const end = s ? endOf(s, drill) : null;
    // Only this session's own clock speaks for this session — see `gen`.
    if (end == null || hud.gen !== sessGenRef.current || hud.clockMs < end) return;
    setRevealed(true);
    // Sitting there until the bell is the same ending as pressing End rep, and
    // has to leave the page in the same state — including the review being on
    // offer. Outside the `endedRef` guard because that one is about not
    // finishing an attempt twice, not about what the page shows.
    if (drill) setRepOver(true);
    if (endedRef.current) return;
    endAttempt();
  }, [drill, endAttempt, hud.clockMs, hud.gen]);

  const stepBar = useCallback(() => {
    const s = sessionRef.current;
    const eng = engineRef.current;
    if (!s || !eng) return;
    // nextBarClockMs() reads forward on a tick timeframe — it needs the print
    // that will complete the bar, which on a live tape has not happened yet.
    if (!sourceRef.current.canStepBar) return;
    stop();
    const clock = Math.min(s.session_end_ms, eng.nextBarClockMs());
    const r = eng.advance(clock);
    chartRef.current?.applyStep(r);
    geoRef.current = { ib: r.ib, range: r.range };
    clockRef.current = clock;
    advanceSim(r.fromIdx, r.toIdx, clock);
    pushHud(r.lastPrice, clock, true);
  }, [advanceSim, pushHud, stop]);

  /** One bar back. A seek, not a step: the tape can't run in reverse, so the
   *  engine re-derives to the previous bar's close — which also means anything
   *  done inside the un-happened bar un-happens, exactly as the scrubber's
   *  rewind does, rewind record and all. */
  const stepBack = useCallback(() => {
    const eng = engineRef.current;
    if (!eng || !sourceRef.current.canSeek) return;
    seekTo(eng.prevBarClockMs());
  }, [seekTo]);

  /** Step the speed along the offered ladder — the keyboard's version of the
   *  transport's <select>. */
  const nudgeSpeed = useCallback((d: 1 | -1) => {
    const i = SIM_SPEEDS.indexOf(speedRef.current);
    const j = Math.max(0, Math.min(SIM_SPEEDS.length - 1, (i < 0 ? 0 : i) + d));
    speedRef.current = SIM_SPEEDS[j];
    setSpeed(SIM_SPEEDS[j]);
  }, []);

  // The three things you do faster than a hand can find a button: get out (q),
  // buy (w), sell (s). Left-hand keys next to each other, because the other hand
  // is on the mouse placing levels on the chart.
  //
  // Deliberately unmodified single keys — a trading key with a chord in front of
  // it is a key you don't press in time — so everything that could be *typing*
  // is let through untouched: a field with the caret in it, a chord, and an
  // autorepeat (a held w must not machine-gun market orders).
  //
  // The transport rides the same guards: k play/pause, , and . step a bar back
  // and forward, [ and ] walk the speed ladder, 1–8 pick the bar size. Video
  // keys rather than invented ones — a replay is a video of the tape, and k/,/.
  // are what every scrubbing tool binds. Space is deliberately NOT play/pause:
  // it is the order modifier on the chart, and a focused button's trigger
  // everywhere else.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      const k = e.key.toLowerCase();
      if (k === "q" || k === "w" || k === "s") {
        e.preventDefault();
        if (k === "q") closeAll();
        else placeMarket(k === "w" ? "long" : "short");
        return;
      }
      if (k === "k") {
        e.preventDefault();
        if (playingRef.current) stop();
        else play();
        return;
      }
      if (k === ",") {
        e.preventDefault();
        stepBack();
        return;
      }
      if (k === ".") {
        e.preventDefault();
        stepBar();
        return;
      }
      if (k === "[" || k === "]") {
        e.preventDefault();
        nudgeSpeed(k === "]" ? 1 : -1);
        return;
      }
      // 1–8 and Shift+1–4 are hooks/usePaneKeys — the same bindings Live has.
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeAll, nudgeSpeed, placeMarket, play, stepBack, stepBar, stop]);

  usePaneKeys({
    paneCount: useCallback(() => paneCountRef.current, []),
    focused: useCallback(() => focusRef.current, []),
    setFocus,
    setPaneTimeframe: changePaneTimeframe,
  });

  const onSpeed = (v: number) => {
    speedRef.current = v;
    setSpeed(v);
  };

  // Switching to a resting ticket seeds the price with the mark, so the input is
  // never a blank you have to look up the market to fill in.
  const chooseType = (t: OrderType) => {
    setOrderType(t);
    if (t !== "market" && !limitPx && Number.isFinite(hud.lastPrice)) {
      setLimitPx(fmtPts(hud.lastPrice));
    }
  };

  const submit = (side: Side) => {
    if (orderType === "market") return placeMarket(side);
    const p = Number(limitPx);
    if (Number.isFinite(p) && p > 0) placeResting(p, side, orderType);
  };

  // --- derived display values ----------------------------------------------
  const realized = useMemo(() => trades.reduce((a, t) => a + t.pnl, 0), [trades]);
  const wins = trades.filter((t) => t.pnl > 0).length;
  const sess = sessionRef.current;
  const scrubMin = sess?.session_start_ms ?? 0;
  const scrubMax = sess ? endOf(sess, drill) : 1;
  // Time left in the bar now forming, for the clock to carry. Time bars only:
  // a tick bar closes on a count of prints, which the wall clock knows nothing
  // about, so showing it a countdown would be showing it a guess. Anchored at
  // the bell like the engine's own boundaries (see nextBarClockMs), so the two
  // can't disagree about where a bar ends.
  const countdownMs =
    ready && tf.kind === "time" && sess
      ? tf.ms - ((((hud.clockMs - sess.rth_open_ms) % tf.ms) + tf.ms) % tf.ms)
      : null;

  const btn = (bg: string): React.CSSProperties => ({
    background: bg,
    color: "#fff",
    border: "none",
    borderRadius: 6,
    padding: "8px 14px",
    fontWeight: 600,
    cursor: "pointer",
  });
  const resting = orderType !== "market";
  // Which way a typed price can go. A bid rests below the market and an offer
  // above it; a stop is the other way round. The button that would contradict
  // the price goes dead rather than quietly having the price clamped across the
  // mark — that is the one place the type is a choice, so it has to be honest.
  const restVal = Number(limitPx);
  const restOk = Number.isFinite(restVal) && restVal > 0 && Number.isFinite(hud.lastPrice);
  const wantsAbove = orderType === "stop";
  const canLong = !resting || (restOk && (wantsAbove ? restVal > hud.lastPrice : restVal < hud.lastPrice));
  const canShort = !resting || (restOk && (wantsAbove ? restVal < hud.lastPrice : restVal > hud.lastPrice));

  /** One optional bracket leg. The tickbox says whether the leg is attached at
   *  all; unticked, the distance box empties and reads "none" — which is also
   *  what clearing the box by hand does, so the two say the same thing. */
  /** What the guardrails will accept for this leg, as a short label. Empty when
   *  the rule is switched off — a level of 0, or the whole layer off via
   *  `REPLAY_GUARDRAILS`, since a bound nothing refuses is not a bound. The stop
   *  reads as a ceiling only: the replay does not enforce `stop_ticks_min`
   *  (lib/guardRules), so quoting a range would name one of those. */
  const legBound = (key: "stop" | "target"): string =>
    !guardsOn
      ? ""
      : key === "stop"
      ? guards.stop_ticks_max > 0
        ? `≤${guards.stop_ticks_max}`
        : ""
      : guards.min_target_ticks > 0
        ? `≥${guards.min_target_ticks}`
        : "";

  const legField = (
    key: "stop" | "target",
    label: string,
    ticks: number,
    apply: (t: number) => void,
    remembered: React.RefObject<number>,
  ) => {
    const on = ticks > 0;
    return (
      <div style={{ fontSize: 11, color: palette.muted }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input
            id={`sim-${key}-on`}
            type="checkbox"
            checked={on}
            onChange={(e) => apply(e.target.checked ? remembered.current : 0)}
            style={{ margin: 0 }}
            title={`Trade with ${key === "stop" ? "a stop" : "a target"}`}
          />
          <label htmlFor={on ? `sim-${key}` : `sim-${key}-on`}>{label}</label>
          {/* The bound the guardrails will refuse outside of, on the control
              that sets it — a rule you only meet by being refused is one you
              read as the app being broken. */}
          {on && legBound(key) && (
            <span style={{ fontSize: 9, marginLeft: "auto" }}>{legBound(key)}</span>
          )}
        </span>
        <input
          id={`sim-${key}`}
          type="number"
          min={1}
          value={on ? ticks : ""}
          placeholder="none"
          disabled={!on}
          onChange={(e) => apply(Number(e.target.value))}
          style={{ width: "100%" }}
        />
      </div>
    );
  };

  /** Attach a leg to an already-open position, at the ticket's distance from the
   *  entry — the way back for a trade taken without one. Held clear of the mark
   *  on the side it belongs on, like every other level the page places: a stop
   *  the tape has already run through would otherwise exit at a price the market
   *  left behind. */
  const addLeg = (leg: "stop" | "target") => {
    const p = openRef.current;
    const mk = markPrice();
    if (!p || !Number.isFinite(mk)) return;
    const dir = p.side === "long" ? 1 : -1;
    const ticks = leg === "stop" ? lastStopRef.current : lastTargetRef.current;
    // Measured from the average, which is what the position is carried at once
    // it has been scaled into.
    const raw = p.entryPrice + (leg === "stop" ? -dir : dir) * ticks * tickSize;
    // A long's stop sits under the market and its target over it; a short's the
    // other way round.
    const below = leg === "stop" ? p.side === "long" : p.side === "short";
    const px = below ? Math.min(raw, mk - tickSize) : Math.max(raw, mk + tickSize);
    moveBracket({ stop: leg === "stop" ? px : p.stop, target: leg === "target" ? px : p.target });
  };

  return (
    <div
      className={`sim-page${railPinned ? " pinned" : ""}`}
      style={
        {
          "--chart-floor": `${floor}px`,
          "--sim-foot-h": `${foot}px`,
        } as React.CSSProperties
      }
    >
      {/* The page's whole persistent chrome. The title is the setup panel's
          trigger — what you press to configure the session is the line that says
          which session it is — and blind replay keeps working because that line
          is a node, not a date the bar formats for itself. */}
      <ChartTopBar
        title={
          // In a drill the bound model replaces the date the tape is hiding
          // anyway — it is the one thing about this rep worth reading, and it
          // is where the eye already goes for "what am I looking at".
          // `hidden`, not a hardcoded mask: the rep ends by revealing the day,
          // and a title that masked it unconditionally would make the reveal
          // silently do nothing — which is exactly what it did until
          // drillcheck asked.
          drill
            ? `${boundModel?.name ?? "No model"} · ${
                hidden ? `${root} · ▨▨▨▨` : `${sel?.symbol ?? root} · ${sel?.date ?? ""}`
              } · ${drop == null ? "—" : hhmm(drop)}`
            : !sel
              ? "Pick a session"
              : hidden
                ? `${root} · ▨▨▨▨ · ${startTime}`
                : `${sel.symbol} · ${sel.date} · ${startTime}`
        }
        onTitle={() => setSetupOpen((o) => !o)}
        titleOpen={setupOpen}
        right={
          <>
            {/* The account, first on the bar, because it is the only thing here
                that can end the session before the tape does.

                Not in a drill: reps are unpriced, so there is no equity, no
                floor and nothing that could end one early. A chip reading
                $50,000 all session on a page it has no authority over would be
                the worst of both — it would look like stakes. */}
            {!drill && <AccountChip view={account} receivedAt={accountAt} />}
            {drill && (
              <>
                <span
                  className="sim-topbar-note"
                  title="Reps you have finished today. Unpriced: no account, no floor, no cooldown — what a drill costs is the tape it takes."
                >
                  rep {repsToday + 1}
                </span>
                <button
                  type="button"
                  className="chart-topbar-btn"
                  onClick={endRep}
                  disabled={!ready || endedRef.current}
                  title="End this rep now and reveal the day (the tape ends it at the bell either way)"
                >
                  End rep
                </button>
              </>
            )}
            <Link to="/charts/replay/history" className="sim-topbar-link" title="Every attempt you've recorded">
              History →
            </Link>
            {/* The dock's opener, on the bar. It used to be the ▤ on the side
                rail, which meant the control that summons the panel lived on a
                strip you had to know was there — and the bar is the one piece of
                chrome that is always on screen. The rail keeps the pin, the
                working count and the day-type dot: those *report*, this
                summons. */}
            <button
              type="button"
              className={`chart-topbar-btn${sheetOpen ? " on" : ""}`}
              onClick={() => setSheetOpen((o) => !o)}
              aria-pressed={sheetOpen}
              title={sheetOpen ? "Hide the ticket and blotter" : "Show the ticket and blotter"}
            >
              ▤▎
            </button>
            {/* The transport's own switch. On the bar rather than on the row it
                hides, because a button that leaves with the thing it hid gives
                you no way back — and this is the one piece of chrome that is
                always on screen. Nothing is lost by hiding it: k plays and
                pauses, `,` and `.` step, and the clock is on the chart. */}
            <button
              type="button"
              className={`chart-topbar-btn${transportOpen ? " on" : ""}`}
              onClick={() => setTransportOpen((o) => !o)}
              aria-pressed={transportOpen}
              title={
                openPos
                  ? "Away while a position is on — scrubbing with size on would rewind past your own entry. k still plays and pauses, , and . still step."
                  : transportOpen
                    ? "Hide the transport — k still plays and pauses, , and . still step"
                    : "Show the transport"
              }
            >
              ▶▌
            </button>
          </>
        }
      >
        <TimeframeControl
          // The focused pane's bucketing. With one pane that is the page's own
          // and nothing has changed; with four it is the one control that used
          // to need a copy on every canvas.
          value={focusedPane === 0 ? tfId : paneTfIds[focusedPane]}
          onChange={(id) => changePaneTimeframe(focusedPane, id)}
          options={TIMEFRAMES.map((t) => ({ key: t.id, label: t.label }))}
          // The tick bar (unique to a tape-driven chart), the default, and the
          // two the research vocabulary is written in. 30s/2m/3m/1h go behind ⋯.
          primary={["500t", "1m", "5m", "15m"]}
          compact
        />
        {/* The community indicator catalogue, next to the bucketing because both
            answer "what am I reading this tape through". Page-level on purpose:
            every pane draws the same studies over its own bars, which is how one
            pick becomes an RSI on the 5m and the 1h at once. */}
        <StudyPicker
          layers={paneLayers[focusedPane] ?? EMPTY_LAYERS}
          onLayer={(key, on) => paneChart(focusedPane)?.setLayer(key, on)}
          specs={studies[focusedPane] ?? EMPTY_SPECS}
          onSpecs={(next) => setPaneStudies(focusedPane, next)}
          paneLabel={paneCount > 1 ? String(focusedPane + 1) : undefined}
        />
        {/* Which pane the control to the left just changed. Spelled out rather
            than left to the focus ring alone: with two 15m panes side by side
            the ring is the only difference between them, and a bar that silently
            re-buckets whichever chart you last brushed past is a bar you stop
            trusting. Only with more than one pane — on a single chart it would
            be a label for a choice that doesn't exist. */}
        {paneCount > 1 && (
          <span className="chart-focus-note" title="Shift+1…4 to change it, or point at a pane">
            pane <b>{focusedPane + 1}</b>
          </span>
        )}
        {/* The layout, next to the bucketing it arranges: both answer "what am I
            looking at", and this is the other thing you change mid-read. Hidden
            on a narrow viewport — four half-width charts on a phone is four
            charts you cannot read, and the mobile pass stops at the Lab. */}
        <LayoutPicker value={layout} onChange={setLayout} />
        {/* The link. Next to the layout because it is a property of the
            arrangement: two views of one tape at different bucketings should
            scroll together, four charts used as four different questions should
            not. Same reason as the note above — with one pane there is nothing
            to link to. */}
        {paneCount > 1 && (
          <button
            type="button"
            className={`chart-topbar-btn link${linkOn ? " on" : ""}`}
            onClick={() => setLinkOn((v) => !v)}
            aria-pressed={linkOn}
            title={
              linkOn
                ? "Linked — the panes share one crosshair, and scrolling one moves the right edge of all of them. Each keeps its own span, so an hourly pane stays hourly."
                : "Unlinked — each pane scrolls on its own"
            }
          >
            ⇄
          </button>
        )}
      </ChartTopBar>
      {/* A press anywhere else puts the setup panel away — the touch screen's
          replacement for Escape, which a phone does not have. Under the bar, so
          the title that opened it is still the thing that closes it too. */}
      {setupOpen && (
        <button
          type="button"
          className="sim-setup-backdrop"
          aria-label="Close session setup"
          onClick={() => setSetupOpen(false)}
        />
      )}
      {/* Pre-run configuration: instrument, session, how much context to draw,
          where to start. Touched once before a replay and then not again, which
          is what makes it a panel rather than a row — it was costing ~48px of
          every session to show settings you had already finished with. */}
      <div className={`sim-setup${setupOpen ? " open" : ""}`}>
        {/* The binding comes first because nothing else in this panel means
            anything without it: a drill is one model exercised exclusively, and
            every trade in the rep books against whatever is chosen here. It is
            deliberately not on the top bar — it is pre-run configuration you
            touch once a campaign, which is what this panel is for. */}
        {drill && (
          <>
            <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
              Model
              <select
                value={drillPrefs.modelId == null ? "" : String(drillPrefs.modelId)}
                onChange={(e) =>
                  patchDrill({ modelId: e.target.value ? Number(e.target.value) : null })
                }
              >
                <option value="">— pick a model —</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                    {m.archived ? " (archived)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label
              style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
              title="The ET window a rep is thrown in at, drawn uniformly. The default stops at 15:00 so every rep has an hour of runway to the close — narrow it to drill a model where it lives, and remember that narrowing it is you telling yourself where the setup is."
            >
              Drop between
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="time"
                  value={drillPrefs.dropFrom}
                  onChange={(e) => patchDrill({ dropFrom: e.target.value })}
                  style={{ width: "100%" }}
                />
                <span aria-hidden>→</span>
                <input
                  type="time"
                  value={drillPrefs.dropTo}
                  onChange={(e) => patchDrill({ dropTo: e.target.value })}
                  style={{ width: "100%" }}
                />
              </span>
            </label>
            {drillBlocked && (
              <span
                className="neg"
                style={{ fontSize: 11, alignSelf: "end", paddingBottom: 6, maxWidth: 220 }}
              >
                {drillBlocked}
              </span>
            )}
          </>
        )}
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Instrument
          <select value={root} onChange={(e) => setRoot(e.target.value)}>
            {(daysQ.data?.roots ?? [root]).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Session
          {/* Blind: the dropdown goes away entirely rather than being disabled —
              a closed <select> shows the day it is sitting on, which is the one
              thing being kept back. Drawing another is still allowed, because
              that tells you nothing. */}
          {hidden ? (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 8px",
                border: `1px solid ${palette.cardBorder}`,
                borderRadius: 6,
                color: palette.text,
              }}
              title="Blind replay — the day is revealed when the tape runs out"
            >
              <span style={{ letterSpacing: 2 }}>▨▨▨▨</span>
              <button
                type="button"
                style={{ ...btn(palette.bg2), color: palette.muted, padding: "2px 8px", fontSize: 11, fontWeight: 500 }}
                onClick={() => setRevealed(true)}
                title="Show which session this is"
              >
                Reveal
              </button>
            </span>
          ) : (
            <select
              value={sel ? `${sel.symbol}|${sel.date}` : ""}
              onChange={(e) => {
                const [symbol, date] = e.target.value.split("|");
                // Picking a day by hand retires the bookmark for the same reason
                // 🎲 does: it says where you want to be now.
                leaveResume();
                setRevealed(false);
                setSel({ symbol, date });
              }}
            >
              {(daysQ.data?.days ?? []).map((d) => (
                <option key={`${d.symbol}|${d.date}`} value={`${d.symbol}|${d.date}`}>
                  {d.date} · {d.symbol}
                  {dayNotes(d)}
                </option>
              ))}
            </select>
          )}
        </label>
        {/* Another draw, without reloading the page. Sits with the picker
            because it is the same choice made the other way. */}
        <button
          type="button"
          style={{ ...btn(palette.bg2), alignSelf: "end", padding: "6px 10px" }}
          onClick={() => daysQ.data?.days.length && anyDay(daysQ.data.days)}
          disabled={!daysQ.data?.days.length || !!drillBlocked}
          title={
            drillBlocked ??
            (drill
              ? "Draw the next rep — a new day and a new hour of it"
              : "Draw another session at random")
          }
        >
          🎲
        </button>
        {/* Not offered in a drill: blind is what the mode *is*, and a checkbox
            you cannot meaningfully clear is chrome that teaches you the setting
            doesn't work. Reveal is still there on the session line, because
            giving up on one draw shouldn't need the mode switched off. */}
        {!drill && (
          <label
            style={{ display: "flex", alignItems: "center", gap: 6, alignSelf: "end", fontSize: 12, color: palette.muted, paddingBottom: 6 }}
            title="Hide which day this is — the date comes off the picker and the chart's time axis until the tape runs out"
          >
            <input type="checkbox" checked={blind} onChange={(e) => setBlind(e.target.checked)} style={{ margin: 0 }} />
            Blind
          </label>
        )}
        {/* The bar lives in the top bar now, not here: it is the one setting on
            this page you change while reading rather than before starting, and
            burying it behind a panel would have been the change most likely to
            annoy daily. Everything left in this panel is pre-run configuration.
            It still can't move the clock, and still can't change a fill. */}
        {/* The days behind this one. Sits with the bar because it is the second
            question about the picture rather than about the replay — the context
            days are drawn, never played, and nothing on them can fill an order.
            Unlike the bar it costs something: each day is a whole tape, fetched
            once and then kept, which is why this one knob stayed here when the
            rest of the chart's settings moved onto the legend rows they tune.
            It is also what decides the composite row exists at all, and a knob
            that can delete its own panel can't live inside it. */}
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title="Draw this many prior sessions to the left of the replay. Real ticks, so they candle on any bar size and profile like the session does — but nothing develops over them, and they can't be traded."
        >
          Prior days
          <select value={historyDays} onChange={(e) => setHistoryDays(Number(e.target.value))}>
            {HISTORY_DAY_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "none" : `${n} day${n === 1 ? "" : "s"}`}
              </option>
            ))}
          </select>
        </label>
        {/* What is made of those days, how big a hump has to be to be a node,
            and how much size an event band needs — all three used to sit here,
            and all three now hang off the legend row they tune (the "…" on it).
            They were only ever questions about one layer each, and this row had
            no way of saying which. */}
        {/* The drop replaces it in a drill — "where does this session begin"
            is the question backtest mode answers with a die, so offering a
            second answer beside it would be two controls fighting over one
            clock. The drop window above is the drill's version of this. */}
        {!drill && (
          <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
            Start time (ET)
            <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </label>
        )}
        {/* Which contract the orders go to is *not* here: it sits at the head of
            the ticket, where Live keeps the same choice (components/RoutingPanel,
            `InstrumentSwitch`). It is not pre-run configuration — it is what the
            account is, and it can be changed with a position open. The rate
            below follows it, which is why the two still read together. */}
        {/* What a fill costs. Pre-run configuration like everything else left in
            this panel — except that unlike the rest of it, these four *can*
            change a fill, which is why editing one re-derives the sitting on the
            spot instead of applying to the next trade only. */}
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title={
            onMicro
              ? `Commission per contract per side, at the mini's rate. ${microSym} is charged a tenth of it — $${charged.commission.toFixed(2)} a side, floored at $0.50, the way the broker bills it — and that is what the log is priced at.`
              : "Commission per contract per side. A round turn on one contract costs twice this, and it is charged when the trade closes."
          }
        >
          {/* The box holds the mini's rate — it is the account's rate, shared
              with the Live page — so when the micro is being charged a tenth of
              it the figure actually taken is said out loud beside it. A box
              reading 3.50 while the log is priced at 0.50 is the sort of quiet
              disagreement that makes a P&L untrustworthy. */}
          Commission $/side{onMicro && ` · ${microSym} $${charged.commission.toFixed(2)}`}
          <input
            type="number"
            min={0}
            step={0.05}
            value={fills.commission}
            onChange={(e) =>
              setFills((f) => ({ ...f, commission: Math.max(0, Number(e.target.value) || 0) }))
            }
            style={{ width: 76 }}
          />
        </label>
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title="Ticks paid for crossing the book. Charged on market orders and on stops once they trigger — never on a limit, which fills at its own price or not at all. On NQ the book is a tick wide, so 1 is the honest number."
        >
          Spread (ticks)
          <input
            type="number"
            min={0}
            step={1}
            value={fills.slipTicks}
            onChange={(e) =>
              setFills((f) => ({ ...f, slipTicks: Math.max(0, Math.floor(Number(e.target.value) || 0)) }))
            }
            style={{ width: 60 }}
          />
        </label>
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title="How far the tape has to trade past a resting limit — an entry limit or a target — before it counts as filled. A print at your price was the queue's; 1 tick stops the replay filling every wick that kissed a level and turned around."
        >
          Queue (ticks)
          <input
            type="number"
            min={0}
            step={1}
            value={fills.queueTicks}
            onChange={(e) =>
              setFills((f) => ({ ...f, queueTicks: Math.max(0, Math.floor(Number(e.target.value) || 0)) }))
            }
            style={{ width: 60 }}
          />
        </label>
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title="How long anything you do takes to reach the market — an order, a drag, a cancel, a flatten. It fills off the print that was current when it landed, not the one you clicked. A bracket's stop and target are already resting at the exchange, so they pay none of it. 250 ms is this desk's measured round trip (docs/research/order-latency.md); it is the round trip and not half of it because the price you reacted to had already travelled to you."
        >
          Lag (ms)
          <input
            type="number"
            min={0}
            step={25}
            value={fills.latencyMs}
            onChange={(e) =>
              setFills((f) => ({ ...f, latencyMs: Math.max(0, Math.round(Number(e.target.value) || 0)) }))
            }
            style={{ width: 68 }}
          />
        </label>
        <button
          type="button"
          style={{ ...btn(palette.bg2), color: palette.muted, alignSelf: "end", padding: "6px 10px", fontSize: 11, fontWeight: 500 }}
          onClick={() => setFills(isPerfect(fills) ? { ...DEFAULT_FILL_MODEL } : { ...PERFECT_FILLS })}
          title={
            isPerfect(fills)
              ? "Charge fills the way a funded account does: $3.50 a side, a tick of spread, a tick of queue, a quarter-second to reach the market"
              : "Fill at the level, for free — the tape read with the account taken out of it"
          }
        >
          {isPerfect(fills) ? "Charge fills" : "Perfect fills"}
        </button>
        <button
          type="button"
          style={btn(palette.accent)}
          onClick={() => {
            const [h, m] = startTime.split(":").map((x) => parseInt(x, 10));
            const off = (Number.isFinite(h) ? h * 60 + m : RTH_OPEN_MIN) - RTH_OPEN_MIN;
            if (sess) seekTo(sess.rth_open_ms + off * 60_000);
          }}
          disabled={!ready}
        >
          Go to start
        </button>
        {sessionQ.isFetching && <span style={{ color: palette.muted, fontSize: 12 }}>loading tape…</span>}
        {/* The replay is playable while these land — they are context, so they
            simply appear to the left when they arrive. */}
        {histQ.loading && (
          <span style={{ color: palette.muted, fontSize: 12 }}>
            loading {histDates.length} prior day{histDates.length === 1 ? "" : "s"}…
          </span>
        )}
        {histQ.failed.length > 0 && (
          <span
            style={{ color: palette.orange, fontSize: 12 }}
            title={`Not drawn: ${histQ.failed.join(", ")}`}
          >
            ⚠ {histQ.failed.length} day{histQ.failed.length === 1 ? "" : "s"} unread
          </span>
        )}
      </div>

      {/* Above the tape and in flow, not over it. A blown account and the last
          account's cause of death are both things you are meant to be unable to
          work around, and a floating badge is exactly the shape of a thing you
          learn to look past. Renders nothing at all on a healthy first account. */}
      <AccountNotice view={account} receivedAt={accountAt} />

      <div className="sim-body">
        <div className="sim-chart-card">
          {/* The rail and the grid, side by side. One rail for however many
              charts: a tool is a mode of the terminal, not a property of one
              canvas — see ChartToolRail. It arms the focused pane, which is why
              phase 5 (the focus model) had to come first. */}
          <div className="sim-chart-wrap">
          <ChartToolRail
            state={toolStates[focusedPane] ?? EMPTY_TOOL_STATE}
            paneLabel={paneCount > 1 ? String(focusedPane + 1) : undefined}
            pinned={toolsPinned}
            onPinnedChange={setToolsPinned}
            onArm={(id: ChartToolId | null) => paneChart(focusedPane)?.armTool(id)}
            onClearAvwap={() => paneChart(focusedPane)?.clearAvwap()}
            onDeleteSelected={() => paneChart(focusedPane)?.deleteSelected()}
            onClearDrawings={() => paneChart(focusedPane)?.clearDrawings()}
          />
          <div className="sim-chart" ref={splitRef} style={gridTemplate(splitPct, splitPctY)}>
            {/* Pane 0 — the page's own chart. Everything the page floats over a
                chart (the indicator strip, the order dock) is inside it rather
                than beside it, so a layout change moves them with the chart they
                belong to. Placed like every other pane; with one pane on screen
                the placement spans the divider tracks too, so nothing about the
                single-chart page moved. */}
            <div
              className={`sim-pane${paneCount > 1 && focusedPane === 0 ? " focused" : ""}`}
              data-pane="0"
              style={{ gridArea: gridArea(LAYOUTS[layout].place[0]) }}
            >
            <ReplayChart
              ref={chartRef}
              linked={linkOn && paneLinked[0]}
              onLinkedChange={
                paneCount > 1
                  ? (v) => setPaneLinked((p) => p.map((b, j) => (j === 0 ? v : b)))
                  : undefined
              }
              onFocus={() => setFocus(0)}
              onToolsChange={(s) => reportTools(0, s)}
              // The legend's identity line. Blind replay masks the date, not the
              // instrument — you are told what you are trading, never when.
              symbol={sel ? (hidden ? root : sel.symbol) : root}
              tfLabel={tf.label}
              // The label is the picker. Every bucketing, not the bar's short
              // list — there is no width to run out of in a popup, and the ⋯ on
              // the bar exists only because a 36px row has an end.
              tfOptions={TF_OPTIONS}
              onTfChange={(id) => changePaneTimeframe(0, id)}
              onAnchorChange={setAnchor}
              onBracketChange={moveBracket}
              onFlatten={closeManual}
              onOrderMove={moveOrder}
              onOrderCancel={cancelOrder}
              onPlaceOrder={placeAt}
              onPlaceTyped={placeTyped}
              // Where a click on this canvas actually sends, and what it is
              // worth there. Both omitted on the mini, where the tape's own
              // contract is the one being traded and saying so twice would be
              // a badge that never turns off.
              routedTo={onMicro ? microSym : undefined}
              pointValue={onMicro ? pointValue : undefined}
              ticket={ticket}
              onTicketChange={changeTicket}
              mark={hud.lastPrice}
              canPlaceOrders={ready}
              hideDates={hidden}
              secondsAxis={showsSeconds(tf)}
              bigLots={bigLots}
              composite={historyDays > 0 ? composite : "off"}
              nodeProm={nodeProm}
              modernVwap={mvParams}
              studies={studies[0] ?? EMPTY_SPECS}
              onStudiesChange={(next) => setPaneStudies(0, next)}
              onLayers={(l) => reportLayers(0, l)}
              events={eventOverlay}
              indicatorSettings={indicatorSettings}
              drawingsKey={sel ? `${sel.symbol}|${sel.date}` : undefined}
            />
            {/* Over the chart rather than beside it: fullscreen is the chart and
                nothing else, and an instrument you calibrate against has to be
                there in the mode you concentrate in. Fed from the HUD, so it
                moves on the same throttled ~80ms tick as the clock — never a
                render per frame. */}
            <SimIndicators
              context={sess?.context}
              ib={hud.gen === sessGenRef.current ? hud.ib : null}
              range={hud.gen === sessGenRef.current ? hud.range : null}
              open={indicators}
              onToggle={() => setIndicators((v) => !v)}
            />
          {/* Market orders, under the thumb, always. The ticket is an overlay
              you have to call up, and the buttons that get you in — and out —
              are the ones you can least afford to go looking for. Floated over
              the foot of the tape rather than parked in the transport, so they
              stay in the same place whatever the transport wrapped to, and so
              a phone reaches them without stretching.

              Both sides quote the same number, and that is not a placeholder
              for a bid/ask: the replay has one price, the last print, and that
              is where a market order fills. A cosmetic spread here would be a
              claim about the fill that the simulation doesn't make.

              Both stay up while a position is open — with a netted position
              they are how you scale in and out, so hiding them behind Close
              would put the two things you most want to do mid-trade out of
              reach.

              One small window rather than a row, and a window that moves: it
              starts at the foot of the tape and can be dragged anywhere on the
              chart, remembering where it was left (see QuickDock). */}
            <QuickDock onFloorChange={setFloor}>
              {/* Size and the bracket, on the window that actually fires. The
                  numbers were only editable in a panel you had to summon, which
                  is a ticket split across two surfaces — and under 1100px the
                  panel gets no column at all, so this is the only order entry
                  there is. See TicketKnobs. */}
              <TicketKnobs ticket={ticket} onChange={changeTicket} tickUsd={tickUsd} />
              {openPos && (
                <>
                  <span className="sim-quick-pos">
                    <span style={{ color: openPos.side === "long" ? palette.green : palette.red }}>
                      {openPos.side === "long" ? "LONG" : "SHORT"} ×{openPos.size}
                    </span>
                    <span style={{ color: palette.muted }}>@ {fmtPts(openPos.entryPrice)}</span>
                    <b style={{ color: hud.openPnl >= 0 ? palette.green : palette.red }}>
                      {fmtUsd(hud.openPnl)}
                    </b>
                  </span>
                  <button
                    type="button"
                    className="sim-quick-btn flat"
                    onClick={closeAll}
                    title="Flatten at market and cancel everything still working (q)"
                  >
                    Close
                  </button>
                </>
              )}
              <button
                type="button"
                className="sim-quick-btn sell"
                onClick={() => placeMarket("short")}
                disabled={!ready}
                title={
                  openPos?.side === "long"
                    ? `Sell ${size} at market (s) — takes size off the long`
                    : "Sell at market (s)"
                }
              >
                <span>SELL</span>
                <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
              </button>
              <button
                type="button"
                className="sim-quick-btn buy"
                onClick={() => placeMarket("long")}
                disabled={!ready}
                title={
                  openPos?.side === "short"
                    ? `Buy ${size} at market (w) — takes size off the short`
                    : "Buy at market (w)"
                }
              >
                <span>BUY</span>
                <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
              </button>
            </QuickDock>
            </div>
            {/* The dividers. Read off the layout rather than counted here:
                which axes a layout splits on is a property of the layout, and
                the drag handler is told which ratio it moves. */}
            {LAYOUTS[layout].dividers.map((d) => (
              <div
                key={d.axis}
                className="sim-pane-divider"
                data-axis={d.axis}
                data-dragging={dragging === d.axis ? "1" : undefined}
                style={{ gridArea: gridArea(d) }}
                onPointerDown={(e) => startSplitDrag(e, d.axis)}
                role="separator"
                aria-orientation={d.axis === "v" ? "vertical" : "horizontal"}
                aria-label={d.axis === "v" ? "Resize the columns" : "Resize the rows"}
                title="Drag to resize — double-click to even them up"
                onDoubleClick={() =>
                  d.axis === "v"
                    ? setSplitPct(DEFAULT_SIM_PREFS.splitPct)
                    : setSplitPctY(DEFAULT_SIM_PREFS.splitPctY)
                }
              />
            ))}
            {/* The extra panes. Each one is its own engine on its own bucketing
                over the same tape, each draws the position, the working orders
                and the fills, and each takes the same order gestures pane 0
                does — space+click, the ＋Order tool, the long-press ticket, and
                a drag on any level already drawn.

                They are handed the *same* callbacks rather than pane-aware
                copies, and that is the point: every one of them names a price
                and nothing else, so there is exactly one order path however many
                charts are pointing at it. The one thing that is per pane is the
                ⚓ anchor, because that draws on the chart you dropped it on. */}
            {LAYOUTS[layout].place.slice(1).map((place, k) => {
              const i = k + 1;
              return (
                <div
                  className={`sim-pane${focusedPane === i ? " focused" : ""}`}
                  data-pane={i}
                  key={i}
                  style={{ gridArea: gridArea(place) }}
                >
                  <ReplayChart
                    ref={(h) => {
                      extraCharts.current[i] = h;
                    }}
                    linked={linkOn && paneLinked[i]}
                    onLinkedChange={(v) =>
                      setPaneLinked((p) => p.map((b, j) => (j === i ? v : b)))
                    }
                    onFocus={() => setFocus(i)}
                    onToolsChange={(s) => reportTools(i, s)}
                    symbol={sel ? (hidden ? root : sel.symbol) : root}
                    tfLabel={paneTfsRef.current[i].label}
                    tfOptions={TF_OPTIONS}
                    onTfChange={(id) => changePaneTimeframe(i, id)}
                    onAnchorChange={(t) => setPaneAnchor(i, t)}
                    onBracketChange={moveBracket}
                    onFlatten={closeManual}
                    onOrderMove={moveOrder}
                    onOrderCancel={cancelOrder}
                    onPlaceOrder={placeAt}
                    onPlaceTyped={placeTyped}
                    routedTo={onMicro ? microSym : undefined}
                    pointValue={onMicro ? pointValue : undefined}
                    ticket={ticket}
                    onTicketChange={changeTicket}
                    mark={hud.lastPrice}
                    canPlaceOrders={ready}
                    hideDates={hidden}
                    secondsAxis={showsSeconds(paneTfsRef.current[i])}
                    bigLots={bigLots}
                    composite={historyDays > 0 ? composite : "off"}
                    nodeProm={nodeProm}
                    modernVwap={mvParams}
                    studies={studies[i] ?? EMPTY_SPECS}
                    onStudiesChange={(next) => setPaneStudies(i, next)}
                    onLayers={(l) => reportLayers(i, l)}
                    events={eventOverlay}
                    indicatorSettings={indicatorSettings}
                    drawingsKey={sel ? `${sel.symbol}|${sel.date}` : undefined}
                    // Each pane keeps its own indicator visibility and legend
                    // state. Keyed by index, so pane 2's answers are pane 2's
                    // whichever layout put it there.
                    prefsPane={`p${i}`}
                    onReady={() => primePane(i)}
                  />
                  {/* No per-pane timeframe picker any more: the top bar's
                      reaches this pane the moment it is focused, and a second
                      copy of the same control on every canvas was both chart
                      pixels and a second place for the answer to live. */}
                </div>
              );
            })}
          </div>
          </div>
          {/* The transport keeps a permanent row — the one deliberate exception
              to this page summoning its chrome. It is not something you
              occasionally want: it is the instrument you drive a replay with,
              and it carries the clock you read continuously. Behind a pill it
              cost a click before every scrub, speed change and step, and hid the
              Play button, which is the control pressed most. ~34px, and only
              Replay pays it — Live has no transport. */}
          <div ref={footRef} className={`sim-transport${transportShown ? "" : " away"}`}>
              <button
                type="button"
                style={btn(playing ? palette.red : palette.green)}
                onClick={() => (playing ? stop() : play())}
                disabled={!ready}
                title={playing ? "Pause (k)" : "Play (k)"}
              >
                {playing ? "❚❚ Pause" : "▶ Play"}
              </button>
              <button
                type="button"
                style={btn(palette.card)}
                onClick={stepBack}
                disabled={!ready || drill}
                title={
                  drill
                    ? "Not in a drill — a rep only runs forwards, or its base rate is measuring your hindsight"
                    : "One bar back (,) — a rewind: anything done inside the un-happened bar un-happens"
                }
              >
                ⏮
              </button>
              <button
                type="button"
                style={btn(palette.card)}
                onClick={stepBar}
                disabled={!ready || playing}
                title="One bar forward (.)"
              >
                ⏭ Step {tf.label}
              </button>
              <label style={{ fontSize: 12, color: palette.muted }} title="Replay speed">
                {/* The word goes on a short viewport; "30×" says what it is. */}
                <span className="sim-lbl">Speed</span>
                <select value={speed} onChange={(e) => onSpeed(Number(e.target.value))} style={{ marginLeft: 6 }}>
                  {SIM_SPEEDS.map((s) => (
                    <option key={s} value={s}>
                      {s}×
                    </option>
                  ))}
                </select>
              </label>
              <input
                type="range"
                // In a drill the floor is where you already are — which is the
                // high-water mark, since the clock never goes back. `seekTo`
                // refuses a backward drag anyway; this is so the control looks
                // like what it does rather than flashing a refusal at every
                // grab of the handle.
                min={drill ? hud.clockMs : scrubMin}
                max={scrubMax}
                step={1000}
                value={hud.clockMs}
                onChange={(e) => seekTo(Number(e.target.value))}
                disabled={!ready}
                className="sim-scrub"
              />
              <span className="sim-clock" style={{ fontFamily: "monospace", color: palette.text, minWidth: 78 }}>
                {fmtClock(hud.clockMs)}
                {countdownMs != null && (
                  <span className="sim-countdown" title={`This ${tf.label} bar closes in ${fmtCountdown(countdownMs)}`}>
                    −{fmtCountdown(countdownMs)}
                  </span>
                )}
              </span>
              {/* ⛶ lives on ChartTopBar now, where it means only what the app
                  cannot do for itself: hide the browser's own chrome. */}
            </div>
        </div>

        {/* The rail. Always present and ~34px wide, which is the honest price of
            a control that has to be findable: the panel behind it used to be a
            300px column reserved whether or not you were trading, and before
            that an edge tab that only existed in fullscreen.

            Pinned, the panel reserves a column beside the tape. Unpinned, it
            lays over the tape and the chart keeps its full width — which is the
            default here, because a replay is mostly reading and the ticket is
            two keystrokes away (w/s) or a click on the chart itself. */}
        <div className="sim-rail">
          {/* The ▤ opener moved to the top bar — see ChartTopBar's `right` slot.
              What is left here reports rather than summons. */}
          {/* Only offered once the panel is out: pinning something you cannot
              see is a setting with no visible effect. */}
          {sheetOpen && (
            <button
              type="button"
              className={`sim-rail-btn${railPinned ? " on" : ""}`}
              onClick={() => setRailPinned((p) => !p)}
              aria-pressed={railPinned}
              title={
                railPinned
                  ? "Unpin — let the panel lay over the tape"
                  : "Pin — give the panel its own column beside the tape"
              }
            >
              📌
            </button>
          )}
          {/* Working orders are worth a count on the rail: with the panel away
              this is the only thing saying something is resting out there. */}
          {working.length > 0 && (
            <span className="sim-rail-badge" title={`${working.length} working`}>
              {working.length}
            </span>
          )}
          {/* The day-type readout, distilled to a dot — the one thing the rail
              can say with the panel away. The numbers behind it are on the
              strip in the panel; the hover carries them here. */}
          {read && (
            <span
              className="sim-rail-read"
              style={{ background: dayVerdict ? VERDICT_COLOR[dayVerdict] : palette.muted }}
              title={readTitle(read, dayVerdict)}
            />
          )}
        </div>

        {/* Ticket, working orders, attempt summary and blotter. Laid over the
            tape by default and given its own column when pinned — the rail
            beside it is what opens and pins it. The collapsed "Last / Ticket ▴"
            face it used to carry on a phone is gone: the rail button is the
            opener now, and the market buttons already quote the last price. */}
        {/* Open by force while reviewing — the panel *is* the review, and a
            page that opened to make you look at something should not open with
            it hidden behind a button. */}
        <div ref={panelRef} className={`sim-panel${sheetOpen || reviewing || dead ? " open" : ""}`}>
          {/* Sticky, so it stays grabbable however far the ticket below it has
              been scrolled. Hidden when pinned — a column in normal flow has
              nowhere to be dragged to. */}
          <div
            className="sim-grab"
            onPointerDown={onGrabDown}
            onPointerMove={onGrabMove}
            onPointerUp={onGrabEnd}
            onPointerCancel={onGrabEnd}
            role="button"
            tabIndex={0}
            aria-label="Hide the order ticket (or drag down)"
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setSheetOpen(false);
              }
            }}
          >
            <span />
          </div>
          {/* The autopsy. Ahead of everything else in the panel, because while it
              is up there is nothing else on this page worth reading. */}
          {dead && account && (
            <AutopsyCard
              view={account}
              receivedAt={accountAt}
              rows={attemptsQ.data?.attempts ?? []}
              onWriteCause={(t) => writeCause.mutate(t)}
              writing={writeCause.isPending}
              error={writeCause.error instanceof Error ? writeCause.error.message : null}
              onReview={openReview}
            />
          )}

          {/* The forced review, in the panel the ticket would be in — the point of
              reviewing here rather than on a page of its own is that it happens
              *against the tape*, with the chart on the moment in question and
              every level that was on it at the time. */}
          {reviewing && reviewMark && (
            <ReviewPanel
              attemptId={reviewMark.attemptId}
              flags={reviewFlags}
              onSeek={(ms) => {
                // 1x, because the sixty seconds in front of a flagged decision
                // are the whole content of looking at it again. At 30x they are
                // two seconds and there is nothing to see.
                setSpeed(1);
                seekTo(ms);
              }}
              onFile={submitReview}
              filing={fileReview.isPending}
              error={fileReview.error instanceof Error ? fileReview.error.message : null}
            />
          )}

          {/* Backtest mode's own review, offered when the rep is over and never
              forced — 🎲 is live either side of it. Above the ticket rather than
              replacing it: the rep is finished, but the ticket is what the next
              draw arrives into, and a panel that hid it would make "next rep"
              feel like leaving the page. */}
          {drill && repOver && (
            <DrillReview
              modelName={boundModel?.name ?? "this model"}
              rules={boundModel?.rules ?? []}
              trades={repJournalQ.data?.trades ?? []}
              saving={saveRules.isPending}
              error={saveRules.error instanceof Error ? saveRules.error.message : null}
              onSave={(key, rulesMet) =>
                saveRules.mutate({ tradeKey: key, rulesMet })
              }
              onDraw={() => daysQ.data?.days.length && anyDay(daysQ.data.days)}
              drawBlocked={drillBlocked}
            />
          )}

          {/* The ticket goes away entirely while reviewing. A BUY/SELL pad that
              refuses every press is the shape of a broken page, and the refusal
              on the order paths is a backstop rather than the explanation. */}
          {!reviewing && !dead && (
          <div className="sim-card sim-ticket">
            {/* Which contract these gestures are sent to, at the head of the
                ticket — the row Live carries in the same place, laid out the
                same way (components/RoutingPanel, `InstrumentSwitch`): the
                choice, the money it makes a tick worth, and the mismatch with
                the tape drawn rather than left to be remembered.

                Live's version cannot let the tape follow, because one Rithmic
                login is one socket. This one cannot either, for a different
                reason with the same consequence: the micro has no tick store to
                load (lib/contracts). So the warning is the honest one in both
                places — a limit price read off this chart is the mini's price,
                and it is sent as typed.

                Absent entirely on a root with no micro: a select with one option
                is a question with one answer. */}
            {microSym && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  flexWrap: "wrap",
                  fontSize: 11,
                  color: palette.muted,
                  margin: "0 0 8px",
                }}
              >
                <select
                  value={micro ? "micro" : "mini"}
                  disabled={contractLocked}
                  onChange={(e) => setMicro(e.target.value === "micro")}
                  title={
                    contractLocked
                      ? `Refused while something is held or working — ${root} and ${microSym} are two ` +
                        "instruments and do not net against each other, so the position you are " +
                        "carrying can only be traded in the contract it was opened in. Close it, " +
                        "or cancel what is resting, and this opens up."
                      : `Which contract this replay's orders are sent to. The tape stays ${root} ` +
                        `either way — ${microSym} tracks the same index to within a tick — so ` +
                        "only the money changes: the P&L, the risk chips and what the guardrails " +
                        "will accept. It applies to what you place next; trades already booked " +
                        "keep the contract they were sent in."
                  }
                  style={{ fontSize: 11, maxWidth: 120, ...(onMicro ? { borderColor: palette.orange } : null) }}
                >
                  <option value="mini">{root}</option>
                  <option value="micro">{microSym}</option>
                </select>
                {/* The money, because that is the only reason to touch this
                    control — and because every box under it is in ticks. */}
                <span title="Dollars per tick on the routed contract. The stop and target below are distances in ticks, so this is what turns the geometry into risk.">
                  ${tickUsd % 1 === 0 ? tickUsd.toFixed(0) : tickUsd.toFixed(2)}/tick
                </span>
                {onMicro && (
                  <span
                    style={{ color: palette.orange }}
                    title={
                      `Orders are priced as ${microSym}; the tape, the chart and every level on ` +
                      `it are ${root}. The two track within a tick, so the geometry carries ` +
                      "over — but a limit price read off this chart is the mini's price, sent " +
                      "as typed."
                    }
                  >
                    ⚠ tape is {root}
                  </span>
                )}
              </div>
            )}
            {/* One guard readout for both terminals — the replay's adapter.
                It replaced a local `Discipline` strip: see components/charts/
                GuardMeters for why replacing rather than joining was the point.

                The floor comes off the account and the day off the simulation,
                which is exactly the join phase 10 made possible — `equity` here
                is the same live figure `accountStop` fires on, so the meter and
                the auto-flatten cannot disagree about how much room is left. */}
            <GuardMeters
              feed={{
                on: guardsOn,
                levels: guards,
                realized: day.realized,
                trades: day.trades,
                locked: day.locked,
                slow: day.slow,
                equity: account ? account.equity + day.realized + hud.openPnl : null,
                floor: account?.floor ?? null,
                size: openPos?.size ?? 0,
                cap: onMicro ? (account?.caps.micros ?? 40) : (account?.caps.minis ?? 4),
                fastShare: day.fastShare,
                medianGapS: day.medianGapS,
                tradedInTheHole: day.tradedInTheHole,
                refused,
              }}
            />
            <DayReadStrip read={read} verdict={dayVerdict} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 22, fontFamily: "monospace" }}>
              <span style={{ color: palette.muted, fontSize: 12, alignSelf: "center" }}>Last</span>
              <span style={{ color: palette.text }}>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                gap: 8,
                marginTop: 10,
                alignItems: "end",
              }}
            >
              <label style={{ fontSize: 11, color: palette.muted }}>
                Size
                <input type="number" min={1} value={size} onChange={(e) => setSize(Math.max(1, Number(e.target.value)))} style={{ width: "100%" }} />
              </label>
              {legField("stop", "Stop (t)", stopTicks, applyStop, lastStopRef)}
              {legField("target", "Target (t)", targetTicks, applyTarget, lastTargetRef)}
            </div>
            {/* The ladder. Collapsed to its switch until it's on: three more
                distance boxes are a lot of ticket for something most replays
                don't use, and the ones that do set it once. */}
            <div style={{ fontSize: 11, color: palette.muted, marginTop: 8 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  id="sim-trail-on"
                  type="checkbox"
                  checked={trailTicks > 0}
                  onChange={(e) => applyTrail(e.target.checked ? lastTrailRef.current : 0)}
                  style={{ margin: 0 }}
                  title="Ratchet the stop up behind the best price the trade has seen"
                />
                <label htmlFor="sim-trail-on">
                  {trailBeOnly && trailTicks > 0 ? "Auto breakeven" : "Trail"}
                </label>
                {trailTicks > 0 && (
                  <label style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }}>
                    <input
                      type="checkbox"
                      checked={trailBeOnly}
                      onChange={(e) => setTrailBeOnly(e.target.checked)}
                      style={{ margin: 0 }}
                      title="Take the first rung and no other — a breakeven stop, not a trail"
                    />
                    BE only
                  </label>
                )}
              </span>
              {trailTicks > 0 && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 4 }}>
                  <label>
                    Back (t)
                    <input
                      type="number"
                      min={1}
                      value={trailTicks}
                      onChange={(e) => applyTrail(Number(e.target.value))}
                      style={{ width: "100%" }}
                      title="How far in front the trade has to be before the stop moves, and how far behind the high it then rides"
                    />
                  </label>
                  <label style={{ opacity: trailBeOnly ? 0.4 : 1 }}>
                    Step (t)
                    <input
                      type="number"
                      min={0}
                      value={trailStepTicks}
                      disabled={trailBeOnly}
                      placeholder="= back"
                      onChange={(e) => setTrailStepTicks(Math.max(0, Math.floor(Number(e.target.value)) || 0))}
                      style={{ width: "100%" }}
                      title="The grid the stop rests on. 0 = one rung per trail distance"
                    />
                  </label>
                  <label>
                    BE (t)
                    <input
                      type="number"
                      min={0}
                      value={trailBeTicks}
                      onChange={(e) => setTrailBeTicks(Math.max(0, Math.floor(Number(e.target.value)) || 0))}
                      style={{ width: "100%" }}
                      title="How far past the entry the first rung lands. 0 is breakeven gross — the round trip still owes commission"
                    />
                  </label>
                </div>
              )}
            </div>
            <div className="sim-kinds" style={{ marginTop: 10 }}>
              {(["market", "limit", "stop"] as OrderType[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => chooseType(t)}
                  className={orderType === t ? "on" : ""}
                  aria-pressed={orderType === t}
                >
                  {t}
                </button>
              ))}
            </div>
            {resting && (
              <label style={{ fontSize: 11, color: palette.muted, display: "block", marginTop: 8 }}>
                {wantsAbove ? "Trigger price" : "Limit price"}
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    type="number"
                    step={tickSize}
                    value={limitPx}
                    onChange={(e) => setLimitPx(e.target.value)}
                    style={{ flex: 1, minWidth: 0 }}
                  />
                  <button
                    type="button"
                    style={{ ...btn(palette.bg2), padding: "4px 8px", fontSize: 11, fontWeight: 500 }}
                    onClick={() => Number.isFinite(hud.lastPrice) && setLimitPx(fmtPts(hud.lastPrice))}
                    title="Fill in the last price"
                  >
                    Last
                  </button>
                </div>
              </label>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button
                type="button"
                style={{ ...btn(palette.green), flex: 1, opacity: canLong ? 1 : 0.4 }}
                onClick={() => submit("long")}
                disabled={!ready || !canLong}
                title={
                  resting && !canLong
                    ? wantsAbove
                      ? "A buy stop has to sit above the market"
                      : "A bid has to rest below the market"
                    : undefined
                }
              >
                {resting ? (wantsAbove ? "Buy stop" : "Buy limit") : "Buy"}
              </button>
              <button
                type="button"
                style={{ ...btn(palette.red), flex: 1, opacity: canShort ? 1 : 0.4 }}
                onClick={() => submit("short")}
                disabled={!ready || !canShort}
                title={
                  resting && !canShort
                    ? wantsAbove
                      ? "A sell stop has to sit below the market"
                      : "An offer has to rest above the market"
                    : undefined
                }
              >
                {resting ? (wantsAbove ? "Sell stop" : "Sell limit") : "Sell"}
              </button>
            </div>
            {/* The gesture that does all of this without the ticket. Said here
                because it is the only place a modifier can be advertised — you
                cannot read a tooltip with Space held down. */}
            <div className="sim-hint" style={{ color: palette.muted, fontSize: 11, marginTop: 8, opacity: 0.8 }}>
              On the chart: hold <b>Space</b> and click a price —{" "}
              <span style={{ color: palette.blue }}>left</span> rests a limit,{" "}
              <span style={{ color: palette.orange }}>right</span> a stop. Or{" "}
              <b>press and hold</b> (right-click) a price for the full ticket.
              <br />
              Keys: <b>w</b> buy · <b>s</b> sell · <b>q</b> flatten and cancel everything ·{" "}
              <b>k</b> play/pause · <b>,</b>/<b>.</b> bar back/forward · <b>[</b>/<b>]</b> speed ·{" "}
              <b>1–8</b> bar size.
            </div>
            {openPos && (
              <div style={{ marginTop: 10, fontSize: 13 }}>
                <div style={{ color: palette.muted }}>
                  Open {openPos.side} ×{openPos.size} @ {fmtPts(openPos.entryPrice)}
                  {openPos.scaled && (
                    <span style={{ opacity: 0.7 }} title="Average of the fills that built it">
                      {" "}
                      avg
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 20, fontFamily: "monospace", color: hud.openPnl >= 0 ? palette.green : palette.red }}>
                  {fmtUsd(hud.openPnl)}
                </div>
                {/* The live bracket, not the one placed at entry — dragging the
                    chart's SL/TP lines is what moves it. One stop and one target
                    for the whole position, whatever it was scaled into. A leg
                    that isn't there has no line to drag, so it gets a button
                    instead: that is the only way back once a trade is taken
                    without one. */}
                <div
                  style={{
                    color: palette.muted,
                    fontSize: 11,
                    marginTop: 4,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    flexWrap: "wrap",
                  }}
                >
                  {(() => {
                    const b = openPos;
                    const add = (leg: "stop" | "target", label: string) => (
                      <button
                        type="button"
                        onClick={() => addLeg(leg)}
                        title={`Attach ${leg === "stop" ? "a stop" : "a target"} at the ticket's distance from the average entry`}
                        style={{
                          ...btn(palette.bg2),
                          color: palette.muted,
                          padding: "1px 6px",
                          fontSize: 11,
                          fontWeight: 500,
                        }}
                      >
                        + {label}
                      </button>
                    );
                    return (
                      <>
                        <span>
                          SL {b.stop != null ? fmtPts(b.stop) : "none"} · TP{" "}
                          {b.target != null ? fmtPts(b.target) : "none"}
                        </span>
                        {b.stop == null && add("stop", "SL")}
                        {b.target == null && add("target", "TP")}
                        {(b.stop != null || b.target != null) && (
                          <span style={{ opacity: 0.7 }}>· drag on the chart</span>
                        )}
                        {(b.stop == null || b.target == null) && (
                          <span style={{ opacity: 0.7 }}>· or hold the position chip and pull</span>
                        )}
                      </>
                    );
                  })()}
                </div>
                {/* The position and nothing else — anything you left resting is
                    still resting. `q` (and the dock's Close, which is the same
                    button under a thumb) is the one that takes everything off. */}
                <button
                  type="button"
                  style={{ ...btn(palette.accent), width: "100%", marginTop: 6 }}
                  onClick={closeManual}
                  title="Close the position at the last print. Working orders stay working — q takes those too"
                >
                  Close @ market
                </button>
              </div>
            )}
          </div>)}

          {/* Working orders. Only there when something is resting — an empty box
              on every flat session would just be furniture. */}
          {working.length > 0 && (
            <div className="sim-card">
              <div className="sim-sec-t">
                Working
                <span className="r">
                  {working.length}
                  {working.length > 1 && (
                    <span
                      style={{ opacity: 0.7 }}
                      title="Orders placed while flat are one OCO set. Orders placed while a position is open stand on their own — they scale in, scale out or flip."
                    >
                      {" "}
                      · one OCO set
                    </span>
                  )}
                </span>
              </div>
              {working.map((o) => (
                <div
                  key={o.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 12,
                    padding: "3px 0",
                  }}
                >
                  <span style={{ color: o.side === "long" ? palette.green : palette.red, minWidth: 46 }}>
                    {o.side === "long" ? "BUY" : "SELL"}
                  </span>
                  <span style={{ color: o.type === "stop" ? palette.orange : palette.blue }}>
                    {o.type === "stop" ? "STP" : "LMT"}
                  </span>
                  <span style={{ fontFamily: "monospace", color: palette.text }}>{fmtPts(o.price)}</span>
                  <span style={{ color: palette.muted, marginLeft: "auto" }}>
                    {Number.isFinite(hud.lastPrice) ? `${Math.abs(o.price - hud.lastPrice).toFixed(2)} away` : "—"}
                  </span>
                  <button
                    type="button"
                    onClick={() => cancelOrder(o.id)}
                    title="Cancel this order"
                    className="sim-cancel"
                    style={{
                      background: "none",
                      border: "none",
                      color: palette.muted,
                      cursor: "pointer",
                      padding: "0 2px",
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* The recap. Only after the sitting is over — mid-replay the blotter
              is the readout, and a second scoreboard next to it would just be
              two numbers to reconcile. */}
          {attemptRec.status === "finished" && attemptRec.summary && (
            <div className="sim-card">
              {(() => {
                const s = attemptRec.summary!;
                const thin = s.trades < MIN_SAMPLE;
                const cell = (label: string, value: string, tone?: string, sub?: string) => (
                  <div>
                    <div style={{ color: palette.muted, fontSize: 11 }}>{label}</div>
                    <div style={{ fontFamily: "monospace", fontSize: 16, color: tone ?? palette.text }}>{value}</div>
                    {sub && <div style={{ color: palette.muted, fontSize: 10, opacity: 0.8 }}>{sub}</div>}
                  </div>
                );
                return (
                  <>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                      <span style={{ color: palette.muted, fontSize: 12 }}>
                        Attempt recorded
                        {attemptRec.attempt && attemptRec.attempt.repeat_index > 0 && (
                          <span
                            style={{ opacity: 0.7 }}
                            title="You have replayed this session before — you knew how it ended"
                          >
                            {" "}
                            · repeat #{attemptRec.attempt.repeat_index + 1}
                          </span>
                        )}
                        {s.rewinds > 0 && (
                          <span
                            style={{ color: palette.orange }}
                            title={`${s.rewinds} rewind(s) past a fill erased ${s.discarded_trades} trade(s). They are kept, and this attempt is flagged.`}
                          >
                            {" "}
                            · {s.rewinds} do-over{s.rewinds > 1 ? "s" : ""}
                          </span>
                        )}
                      </span>
                      <Link to="/charts/replay/history" style={{ color: palette.muted, fontSize: 12 }}>
                        History →
                      </Link>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                      {cell("Net", fmtUsd(s.net_usd), s.net_usd >= 0 ? palette.green : palette.red,
                        `${s.trades} trade${s.trades === 1 ? "" : "s"} · ${s.net_points.toFixed(2)} pts`)}
                      {cell(
                        "Win rate",
                        fmtPct(s.win_rate),
                        thin ? palette.muted : palette.text,
                        // The interval, not the point estimate, is the honest
                        // read of a handful of trades — and a practice log lives
                        // in handfuls for its first few months.
                        s.win_rate_lo != null
                          ? `95% CI ${fmtPct(s.win_rate_lo)}–${fmtPct(s.win_rate_hi)}${thin ? " · thin" : ""}`
                          : undefined,
                      )}
                      {cell(
                        "Stake R",
                        s.n_with_r ? fmtR(s.net_r) : "—",
                        s.net_r >= 0 ? palette.green : palette.red,
                        s.n_with_r < s.trades
                          ? `over ${s.n_with_r}/${s.trades} with a stop`
                          : `${s.expectancy_r != null ? fmtR(s.expectancy_r) : "—"}/trade`,
                      )}
                    </div>
                    <div style={{ color: palette.muted, fontSize: 11, marginTop: 8 }}>
                      PF {s.profit_factor != null ? s.profit_factor.toFixed(2) : "—"} · best{" "}
                      {s.best_usd != null ? fmtUsd(s.best_usd) : "—"} · worst{" "}
                      {s.worst_usd != null ? fmtUsd(s.worst_usd) : "—"}
                      {s.avg_hold_s != null && ` · held ${Math.round(s.avg_hold_s / 60)}m avg`}
                      {/* The broker's share, spelled out. Net is already after
                          it — this says how much of the distance between a good
                          day and a flat one was commission. The spread is not in
                          here and cannot be: it is inside the fill prices.

                          The rate in the tooltip is read back out of the fees
                          rather than off the ticket: a sitting may have changed
                          contract between positions, and the average actually
                          charged is the only rate that multiplies out to this
                          total. */}
                      {s.fees_usd > 0 && (
                        <span title={`${s.contracts} contract(s) × 2 sides × $${(s.fees_usd / (2 * s.contracts)).toFixed(2)}. Net is already after this.`}>
                          {" "}
                          · fees {fmtUsd(s.fees_usd)}
                        </span>
                      )}
                    </div>
                    {/* What the sitting did to the account, under what it did on
                        its own terms. The two are different questions — a
                        +$400 session that took the floor from $900 away to $500
                        away is a good sitting and a worse account. */}
                    <AccountRecap view={account} />
                    <textarea
                      // Keyed by attempt so a new sitting never inherits the
                      // last one's note into an uncontrolled box.
                      key={attemptRec.attempt?.id ?? "none"}
                      defaultValue={attemptRec.attempt?.note ?? ""}
                      onBlur={(e) => void setAttemptNote(e.target.value)}
                      placeholder="What happened here…"
                      rows={2}
                      style={{ width: "100%", marginTop: 8, fontSize: 12, resize: "vertical" }}
                    />
                  </>
                );
              })()}
            </div>
          )}

          <div className="sim-card sim-blotter">
            <div className="sim-sec-t" style={{ flex: "none" }}>
              Blotter
              <span className="r">
                {trades.length} trades · {wins}W
              </span>
              {/* Only once there is something to end. Ending is explicit here
                  and automatic at the end of the tape — both close the sitting
                  the same way, position and all. */}
              {trades.length > 0 && attemptRec.status !== "finished" && (
                <button
                  type="button"
                  onClick={endAttempt}
                  className="sim-cancel"
                  style={{ ...btn(palette.bg2), color: palette.muted, padding: "2px 8px", fontSize: 11, fontWeight: 500 }}
                  title="Close anything still open at the last print and file this attempt"
                >
                  End attempt
                </button>
              )}
              {attemptRec.error && (
                <span style={{ color: palette.red, fontSize: 11 }} title={attemptRec.error}>
                  ⚠ not saved
                </span>
              )}
              <span style={{ fontFamily: "monospace", fontWeight: 700, marginLeft: "auto", color: realized >= 0 ? palette.green : palette.red }}>
                {fmtUsd(realized)}
              </span>
            </div>
            <div className="sim-blotter-list" style={{ flex: 1, minHeight: 0, overflowY: "auto", fontSize: 12 }}>
              {trades.length === 0 && <div style={{ color: palette.muted }}>No trades yet.</div>}
              {trades
                .slice()
                .reverse()
                .map((t) => (
                  <div
                    key={t.id}
                    style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: `1px solid ${palette.cardBorder}` }}
                  >
                    <span style={{ color: t.side === "long" ? palette.green : palette.red }}>
                      {t.side === "long" ? "L" : "S"}×{t.size}
                      {/* Named only when this row is not in the contract the
                          ticket is pointed at now — which is the only time the
                          head of the panel doesn't already say it, and the only
                          time two rows of "×1" mean different money. */}
                      {microSym && t.micro !== onMicro && (
                        <span
                          style={{ color: palette.muted, fontSize: 10, marginLeft: 4 }}
                          title={`Traded as ${t.micro ? microSym : root} — the contract the order was sent to, not the one selected now.`}
                        >
                          {t.micro ? microSym : root}
                        </span>
                      )}
                    </span>
                    <span style={{ color: palette.muted }}>
                      {t.openType === "market" ? "" : `${t.openType === "stop" ? "stp" : "lmt"}→`}
                      {t.reason}
                    </span>
                    {/* Stake R leads — it is the one that says what the account
                        did. Excursion R only earns its own column when the two
                        disagree, which is to say when size changed mid-trade;
                        on an ordinary one-clip trade they are the same number
                        and printing it twice would just be noise. */}
                    <span style={{ color: palette.muted }} title={rTitle(t)}>
                      {fmtR(t.rCash)}
                      {t.r != null && t.rCash != null && Math.abs(t.r - t.rCash) > 0.005 && (
                        <span style={{ opacity: 0.6 }}> · {fmtR(t.r)}e</span>
                      )}
                    </span>
                    {/* Net, like everything else on this page — the gross and
                        the fee are in the tooltip, where the difference is
                        worth seeing without the column having to carry it. */}
                    <span
                      style={{ fontFamily: "monospace", color: t.pnl >= 0 ? palette.green : palette.red }}
                      title={
                        t.fees > 0
                          ? `${fmtUsd(t.pnl + t.fees)} gross − ${fmtUsd(t.fees)} commission · in ${t.entryPrice} out ${t.exitPrice}`
                          : `in ${t.entryPrice} out ${t.exitPrice}`
                      }
                    >
                      {fmtUsd(t.pnl)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The day, against the rules — and the three numbers worth logging after it.
 *
 * Two halves, and they are different kinds of thing on purpose.
 *
 * **Above the line: what is enforced.** The running total and the daily stop,
 * which refuse a new entry once crossed. Closing out is never refused, here or
 * on the live side, so a locked day is a day you can still get out of.
 *
 * **Below it: what is only measured.** Share of trades resolving inside 30
 * seconds, median gap between entries, and whether anything was opened while
 * already in the hole. These are the operating plan's after-session three, and
 * they are counters rather than rules for two separate reasons. The 30-second
 * habit is an *entry* problem — 85% of its damage is the stop firing, not an
 * early manual exit — so there is no exit rule that could catch it. And pace
 * cannot be enforced against a compressed clock at all: at speed 30 a two-minute
 * gap is four seconds of yours, so a rule would train nothing. Measuring is what
 * a replay can honestly do with a real-time habit.
 */
const VERDICT_COLOR: Record<DayVerdict, string> = {
  paying: palette.green,
  grudging: palette.orange,
  dry: palette.red,
};

/** The rail dot's hover: the whole strip in a sentence, for when the panel is
 *  away. */
function readTitle(read: DayRead, verdict: DayVerdict | null): string {
  const t = (v: number | null) => (v == null ? "—" : `${Math.round(v)}t`);
  const tide =
    read.tideTicks == null
      ? "—"
      : `${read.tideTicks < 0 ? "▼" : "▲"}${Math.abs(Math.round(read.tideTicks))}t`;
  const head = `Tide ${tide} · swing ${t(read.swingTicks)} · ext ${t(read.extTicks)} (${read.scored} scored)`;
  return verdict ? `${head} — ${VERDICT_LINE[verdict]}` : `${head} — reading the day`;
}

/**
 * The day-type readout: TIDE (10-min net drift), SWING (median leg before a
 * 25t reversal), EXT (median 3-min excursion of the last few entries), and the
 * verdict EXT buckets into. A readout and not a gate — the thresholds stand on
 * three sittings (docs/research/replay-trail-whatif.md), so it says what it
 * sees and refuses nothing. Causal throughout, so safe on a blind replay.
 */
function DayReadStrip({ read, verdict }: { read: DayRead | null; verdict: DayVerdict | null }) {
  if (!read) return null;
  const num = (v: number | null) => (v == null ? "—" : `${Math.round(v)}`);
  const stat = (label: string, value: string, title: string) => (
    <span title={title} style={{ whiteSpace: "nowrap" }}>
      {label} <b style={{ color: palette.text, fontFamily: "monospace" }}>{value}</b>
    </span>
  );
  return (
    <div style={{ borderBottom: `1px solid ${palette.cardBorder}`, paddingBottom: 8, marginBottom: 8 }}>
      <div style={{ display: "flex", gap: 10, fontSize: 10, color: palette.muted }}>
        <span style={{ letterSpacing: 0.4 }}>READ</span>
        {stat(
          "tide",
          read.tideTicks == null
            ? "—"
            : `${read.tideTicks < 0 ? "▼" : "▲"}${Math.abs(Math.round(read.tideTicks))}`,
          "Net drift over the last 10 minutes of tape, in ticks. Which way the session is actually going — thrash cancels out, tide doesn't.",
        )}
        {stat(
          "swing",
          num(read.swingTicks),
          "Median run before a 25-tick reversal, over the same window. A trail distance inside this number exits on routine wiggles.",
        )}
        {stat(
          "ext",
          read.extTicks == null ? "—" : `${num(read.extTicks)}·${read.scored}`,
          "Median 3-minute favorable excursion of your last few entries (scored 3 minutes after entry, so it lags by a trade or two). The number that separated paying days from dry ones in the trail study.",
        )}
      </div>
      <div
        style={{
          fontSize: 11,
          marginTop: 4,
          color: verdict ? VERDICT_COLOR[verdict] : palette.muted,
          lineHeight: 1.4,
        }}
      >
        {verdict ? VERDICT_LINE[verdict] : `reading the day — ${read.scored}/3 entries scored`}
      </div>
    </div>
  );
}
