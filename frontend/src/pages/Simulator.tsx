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
import {
  ReplayChart,
  type ReplayChartHandle,
  type TicketDraft,
} from "../components/charts/ReplayChart";
import type { IndicatorSettingsMap } from "../components/charts/IndicatorLegend";
import { buildChartKnobs } from "../components/charts/indicatorKnobs";
import {
  loadShelfParams,
  saveShelfParams,
  loadShelfField,
  saveShelfField,
} from "../lib/chartPrefs";
import type { ShelfParams } from "../lib/volumeShelf";
import type { ShelfField } from "../components/charts/VolumeShelfPrimitive";
import type { ModernVwapParams } from "../lib/modernVwap";
import type { DsvParams } from "../lib/dynamicSwingVwap";
import { SimIndicators } from "../components/charts/SimIndicators";
import { QuickDock } from "../components/charts/QuickDock";
import { TimeframeControl } from "../components/charts/TimeframeControl";
import { StudyPicker } from "../components/charts/StudyPicker";
import { loadStudies, saveStudies } from "../lib/chartPrefs";
import type { StudySpec } from "../lib/studies";
import type { LayerState } from "../components/charts/chartLayers";
import { Blotter, TradeTally } from "../components/charts/Blotter";
import { ChartTopBar } from "../components/charts/ChartTopBar";
import { GuardMeters } from "../components/charts/GuardMeters";
import { PaceChip } from "../components/charts/PaceChip";
import { LayoutPicker } from "../components/charts/LayoutPicker";
import { LAYOUTS, MAX_PANES, clampPaneIndex, gridArea, gridTemplate } from "../lib/paneLayout";
import { setLinkOn as setLinkModuleOn } from "../lib/paneLink";
import { ChartToolRail } from "../components/charts/ChartToolRail";
import { TicketCard, type PresetCtx } from "../components/charts/TicketCard";
import { TicketKnobs } from "../components/charts/TicketKnobs";
import { LegAmount, legEcho } from "../components/charts/LegAmount";
import { legTicks, pinnedLeg, usdForTicks } from "../lib/bracketUsd";
import { TurboChip } from "../components/charts/TurboChip";
import type { SizerCtx } from "../components/charts/SizerGrid";
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
import { TURBO_MULT, useTurbo } from "../hooks/useTurbo";
import { AccountChip, AccountNotice, AccountRecap } from "../components/charts/ReplayAccount";
import { AccountSwitch } from "../components/charts/AccountSwitch";
import { AutopsyCard } from "../components/charts/AutopsyCard";
import {
  useFileReview,
  useReviewVocab,
  useTradeTags,
  useReplayAttemptDetail,
  useReplayAttempts,
  useReplayJournal,
  useSaveTradeReview,
  type AttemptDetail,
  type DrillTradeRow,
} from "../hooks/useReplays";
import { DrillReview } from "../components/charts/DrillReview";
import { clearResume, loadResume, saveResume, type ResumePoint } from "../lib/replayResume";
import { clearReview, loadReview, saveReview } from "../lib/replayReview";
import { ReviewPanel } from "../components/charts/ReviewPanel";
import {
  answered as reviewAnswered,
  seedAnswers,
  type ReviewAnswers,
} from "../components/charts/ReviewCard";
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
import { showsSeconds, timeframeById, useTimeframeOptions } from "../lib/timeframes";
import {
  HISTORY_DAY_OPTIONS,
  defaultHistoryDays,
  governingHistory,
  withHistoryOverride,
} from "../lib/contextDays";
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
} from "../lib/replaySim";
import {
  blotterRow,
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
  accountBreach,
  dayFlatten,
  dayRefusal,
  dayState,
  isReducing,
  paceRefusal,
  shapeRefusal,
} from "../lib/guardRules";
import { liveAccount } from "../lib/replayAccount";
import type { PresetBracket } from "../lib/orderPresets";
import {
  armAction,
  armPurpose,
  limitPlacement,
  DEFAULT_THROUGH_TICKS,
  type ArmAction,
  type ArmShape,
  type ArmableLevel,
  type LevelArm,
} from "../lib/levelArm";
import { DEFAULT_MAX_LOSS } from "../lib/riskSizer";
import type { VolRulerRead } from "../lib/volRuler";
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

/** **TEMPORARY (2026-08-20, user request): backtest reviews are not mandatory.**
 *
 *  The browser copy of `DRILL_REVIEW_REQUIRED` in api/routers/replays.py — flip
 *  both together, and re-read that comment before you do. While it is `false`
 *  the rep-end panel is still offered and every answer still saves; 🎲 simply
 *  stops waiting for them, exactly as the server has stopped refusing. */
const DRILL_REVIEW_REQUIRED = false;

/** How often the resume bookmark is brought up to date with the clock. Bounds
 *  what a crash costs you in tape — a few seconds of scrolling back — against a
 *  localStorage write on a page that is already doing sixty frames a second. */
const RESUME_SAVE_MS = 4_000;

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

/** Which of the replay-clock pages this is.
 *
 *  `replay` is the page you pick a day on. `paper` is the same page against the
 *  paper account — identical rules, identical floor, and a death that costs a
 *  sentence and a day on `replay` costs nothing here. `drill` is backtest mode:
 *  one model bound for the whole sitting, a random RTH clock on a day you are
 *  not shown, and no account at all — see docs/backtest-mode-plan.md.
 *
 *  The mode is also *which ledger the sitting lands on* — it rides on the create
 *  and the server maps it to an account (`replay_account.BY_MODE`). It is a prop
 *  rather than page state because the mode is fixed for the whole of a sitting
 *  and the route is what fixes it. A switch you could flip mid-rep would be a
 *  switch that moved a finished sitting between two ledgers. */
/** The other one. Its own name because the reverse knob has to flip the side in
 *  two places that must never disagree — the order that gets sent, and the label
 *  on the button that sends it — and an inlined ternary in each is how a button
 *  ends up saying BUY over a sell. */
const otherSide = (s: Side): Side => (s === "long" ? "short" : "long");

export type SimMode = "replay" | "paper" | "drill";

export function Simulator({
  mode = "replay",
  /** Which account prices this page's sittings. Defaults from the mode so the
   *  two built-in routes need not name it — `paper` prices the paper account
   *  and everything else the funded one, which is exactly the mapping every
   *  sitting already on disk was written under (`replays.account_id_of`). A
   *  drill has none and passes null the whole way down. */
  accountId,
}: { mode?: SimMode; accountId?: string } = {}) {
  const acctId =
    mode === "drill" ? null : accountId ?? (mode === "paper" ? "paper" : "funded");
  /** What this page's bookmark and review marker are filed under. The account,
   *  because that is what a sitting belongs to — two LucidPro accounts keyed by
   *  mode would fight over one bookmark and land you on the wrong day with the
   *  wrong floor. A drill has no account and keeps its own scope. */
  const scope = acctId ?? "drill";
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
  const [pending, setPending] = useState(() => loadResume(scope));
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
  const [reviewMark, setReviewMark] = useState(() => loadReview(scope));
  // Same query key as the resume fetch above when they name the same attempt,
  // so this is one request, not two.
  const reviewQ = useReplayAttemptDetail(reviewMark?.attemptId ?? null);
  const reviewDetail = reviewQ.data ?? null;
  /** The reviewed sitting's record, for the tape build — which runs outside the
   *  render and needs the *log* off it. Paired with `reviewSettled` below: the
   *  build waits for this the same way it waits for the resume fetch, because a
   *  review built without it is a review with an empty chart. */
  const reviewDetailRef = useRef<AttemptDetail | null>(null);
  reviewDetailRef.current = reviewQ.data ?? null;
  const reviewSettled = !reviewMark?.attemptId || reviewQ.isSuccess || reviewQ.isError;
  const reviewFlags = reviewDetail?.flags ?? [];
  // A sitting owes while it is `finished` with anything to answer for — a flag
  // or, since the review revamp, any booked trade at all (every trade owes a
  // model and tags now; docs/review-revamp-plan.md).
  const reviewing =
    !!reviewMark &&
    reviewDetail?.status === "finished" &&
    (reviewFlags.length > 0 || (reviewDetail?.trades?.length ?? 0) > 0) &&
    sel?.symbol === reviewDetail.symbol &&
    sel?.date === reviewDetail.date;
  // Reviewing a backtest rep uses the same panel with nothing hidden: the model
  // column was the one thing a drill suppressed (the rep's binding already is
  // the model) and the review no longer has one. The way a drill gets here is
  // the history page, the escape hatch for a rep-end panel lost to a reload
  // (plan V9).
  //
  // The journal mirror's rows for the reviewed sitting — where the per-trade
  // answers live and where the server reads them back from.
  const reviewJournalQ = useReplayJournal(reviewing ? (reviewMark?.attemptId ?? null) : null);
  // Read inside the tape build and inside `placeOrder`, both of which run
  // outside the render that decided it.
  const reviewingRef = useRef(reviewing);
  reviewingRef.current = reviewing;
  /** The reviewed sitting's id, for the callbacks that run outside the render.
   *  A ref for the same reason `reviewingRef` is: `writeResume` is held by
   *  effects with their own dependency lists, and re-creating it on every change
   *  of the mark would re-run them. */
  const reviewMarkRef = useRef(reviewMark);
  reviewMarkRef.current = reviewMark;

  const navigate = useNavigate();
  const fileReview = useFileReview();
  /** File the review and leave.
   *
   *  Leaving is not tidiness. The reviewed sitting is `reviewed` now, and
   *  resuming into it would put it back to `active` — which withdraws the review
   *  that was just filed (see `journal.replays.save`). So the bookmark goes with
   *  the marker, and the way on from here is a new sitting. */
  const submitReview = useCallback(
    (note: string) => {
      const id = reviewMark?.attemptId;
      if (!id) return;
      fileReview.mutate(
        // The sitting's own note travels with the status — one PATCH, one act.
        { id, note },
        {
          onSuccess: () => {
            clearReview(scope);
            setReviewMark(null);
            clearResume(scope);
            // A filed drill review is what unlocks 🎲, so the way on is the
            // backtest page itself — reloaded, so the fresh mount draws the
            // next rep. A sitting's review files into the ledger, so the
            // history page.
            if (drill) navigate(0);
            else navigate("/charts/replay/history");
          },
        },
      );
    },
    [drill, fileReview, mode, navigate, reviewMark],
  );

  /** Start somewhere else on purpose — 🎲, or a day picked by hand. The
   *  bookmark is a record of where you were, so a decision to be elsewhere
   *  retires it rather than leaving it to resurface on the next reload. */
  const leaveResume = useCallback(() => {
    setPending(null);
    pendingRef.current = null;
    clearResume(scope);
  }, [mode]);

  const sessionQ = useSimulatorSession(sel?.symbol ?? null, sel?.date ?? null, tz);

  // Context days: whole prior sessions, drawn to the left of the replay so the
  // levels you trade off — yesterday's high, the shelf the week has been sat on
  // — are on the chart instead of in your head. They are the same contract only:
  // a roll would splice two price series a hundred points apart, which is the
  // same rule the weekly anchor follows (journal.sim.weekly).
  // How many of them is a question about the *bar*, not about the page: an
  // hourly with one day behind it has twenty-three candles on it, and a 1m with
  // twenty has fourteen thousand it will never scroll to. So the count follows
  // the bucketing (lib/contextDays) and this state is only what you have
  // overridden that rule to, per bar. Resolved below, once the pane timeframes
  // it also has to answer for have been declared.
  const [histOverrides, setHistOverrides] = useState(prefs.historyDaysByTf);
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
  // The Zeiierman line beside it, held and persisted the same way — a separate
  // indicator, so separate state; see lib/dynamicSwingVwap.
  const [dsvParams, setDsvParams] = useState(prefs.dynamicSwingVwap);
  const patchDsv = useCallback(
    (patch: Partial<DsvParams>) => setDsvParams((p) => ({ ...p, ...patch })),
    [],
  );
  // Volume shelves. Sticky-global rather than part of the page's own prefs, like
  // the surface and the band fills: how you read a shelf is a statement about
  // shelves, not about this sitting — and it is the same setting the journal's
  // charts offer.
  const [shelfParams, setShelfParams] = useState(loadShelfParams);
  // Which quantity the raster draws. Its own state, not a seventh shelf
  // parameter: switching it re-reads nothing, and `ShelfParams` is mirrored
  // by a Python module that draws nothing at all.
  const [shelfField, setShelfField] = useState<ShelfField>(loadShelfField);
  const patchShelfField = useCallback((f: ShelfField) => {
    setShelfField(f);
    saveShelfField(f);
  }, []);
  const patchShelf = useCallback(
    (patch: Partial<ShelfParams>) =>
      setShelfParams((p) => {
        const next = { ...p, ...patch };
        saveShelfParams(next);
        return next;
      }),
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
  const [evFillSweep, setEvFillSweep] = useState(prefs.eventFillSweep);
  const [evFillAbsorb, setEvFillAbsorb] = useState(prefs.eventFillAbsorb);
  const [evFloorSweep, setEvFloorSweep] = useState(prefs.eventFloorSweep);
  const [evFloorAbsorb, setEvFloorAbsorb] = useState(prefs.eventFloorAbsorb);
  const [evMarginal, setEvMarginal] = useState(prefs.eventMarginal);
  // The day-scale indicator strip over the chart's foot (see SimIndicators): the
  // IB-width chip and the range-budget gauge. A reading choice like the bar size
  // and the big-trade threshold — it cannot move the clock or fill an order.
  const [indicators, setIndicators] = useState(prefs.indicators);

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
    kill: killAttempt,
    setNote: setAttemptNote,
    setReviewLater: setAttemptReviewLater,
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

  // Which day the picker opens on is decided further down — see the day-pick
  // effect. It lives there rather than here because what it consults (the
  // bookmark's own attempt fetch, the review marker) is declared between the two.

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
  // Hold Ctrl to run ten times whatever the ladder is set to.
  const turbo = useTurbo();
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
    saveResume(scope, {
      symbol: s.symbol,
      date: s.date,
      clockMs: clockRef.current,
      // Whose sitting this is. Normally the recorder's — but the recorder is
      // deliberately **unarmed in review mode**, so `attemptIdOf()` is null
      // there, and writing that null was quietly fatal: the bookmark is what
      // names the attempt whose log gets replayed onto the tape, and a bookmark
      // with no id fails the `usable` test on the next load. The first visit to
      // a review worked (the auto-open wrote the id itself) and every visit
      // after it opened a review with an empty chart — cards on the right,
      // no trades anywhere, at any clock.
      attemptId: reviewingRef.current
        ? (reviewMarkRef.current?.attemptId ?? null)
        : attemptIdOf(),
      // The cursors in the stored log count from the start of the glued tape, so
      // what is glued in front of it right now is the number that makes them
      // readable again next time.
      contextTicks: histTapesRef.current.reduce((a, t) => a + t.n, 0),
    });
  }, [attemptIdOf, mode]);

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
    /** The mark, and the **only** part of the open position's value that is
     *  sampled. What is *held* comes from `openPos` and the mark is applied to
     *  it at render (`openNow`) — see there for why the two may not be sampled
     *  together. */
    lastPrice: number;
    /** The sitting's equity high-water, off `SimState`'s fold rather than off
     *  this sample — an account's floor must not depend on which animation
     *  frames landed. See `replayAccount.liveAccount`. */
    peakUsd: number;
    gen: number;
    ib: IbBox | null;
    range: RangeBox | null;
  }>({ clockMs: 0, lastPrice: NaN, peakUsd: 0, gen: 0, ib: null, range: null });
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
  /** Which contract this page is *pointing at*, or `null` for "it isn't".
   *
   *  Null while reviewing, and that is the whole point of the distinction: a
   *  review places nothing — the ticket is gone and every order path refuses —
   *  so there is no routed contract for the chips to be priced in or for the
   *  blotter to name a difference from. Left as the plain preference, a review
   *  read with the ticket parked on the micro drew the sitting's own NQ
   *  positions and orders at MNQ's $2 a point and badged the canvas "→ MNQ",
   *  which is a claim about orders that cannot be sent.
   *
   *  Each position and order carries its own stamp (`Position.micro`, see
   *  `posLine`/`orderView`), so with this null the overlays fall back to the
   *  tape's own contract and anything actually traded as a micro still prices
   *  as one. */
  const routedMicro: boolean | null = reviewing ? null : onMicro;
  const routedTo = routedMicro ? (microSym ?? undefined) : undefined;
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
  //
  // Read up here, above the ticket, because a dollar-pinned leg is *derived*
  // from it: the bracket cannot be resolved before the money that prices it is
  // known.
  const pointValue = tapePointValue / (onMicro ? MICRO_RATIO : 1);
  const tickUsd = tickSize * pointValue;
  // Both bracket legs are optional: zero ticks means the leg isn't attached at
  // all, and an order can be placed with neither — the trade is then yours to
  // close by hand, or to bracket afterwards by dragging a level onto it.
  //
  // A leg may also be pinned to a **dollar figure** instead (lib/bracketUsd), and
  // then the tick distance below stops being the setting and becomes the last
  // resolution of it — kept up to date at every pin so that a session with no
  // tape loaded, where there is no tick money to divide by, still has a bracket
  // to place. `stopTicks`/`targetTicks` further down are the resolved distances,
  // and they are what every order path on this page reads.
  const [stopTicksSet, setStopTicks] = useState(prefs.stopTicks);
  const [targetTicksSet, setTargetTicks] = useState(prefs.targetTicks);
  const [stopUsd, setStopUsd] = useState(prefs.stopUsd);
  const [targetUsd, setTargetUsd] = useState(prefs.targetUsd);
  const stopTicks = legTicks(stopTicksSet, stopUsd, tickUsd, size);
  const targetTicks = legTicks(targetTicksSet, targetUsd, tickUsd, size);
  // The ladder. Off by default, and set per ticket rather than per session — it
  // rides on the order, so two trades in one replay can be managed differently.
  const [trailTicks, setTrailTicks] = useState(prefs.trailTicks);
  const [trailStepTicks, setTrailStepTicks] = useState(prefs.trailStepTicks);
  const [trailBeTicks, setTrailBeTicks] = useState(prefs.trailBeTicks);
  const [trailBeOnly, setTrailBeOnly] = useState(prefs.trailBeOnly);
  const [orderType, setOrderType] = useState<OrderType>(prefs.orderType);
  // Trade the model backwards (lib/simPrefs `reverseEntry`). A setting rather
  // than a modifier key: you drill a whole campaign inverted or you don't, and a
  // held key is the wrong shape for something that has to still be true forty
  // reps later.
  const [reverseEntry, setReverseEntry] = useState(prefs.reverseEntry);
  const [limitPx, setLimitPx] = useState("");
  const lastHudRef = useRef(0);
  const [tfId, setTfId] = useState(prefs.timeframe);
  const tf = useMemo(() => timeframeById(tfId), [tfId]);
  // The built-in bar sizes plus any you have typed into the picker. One list for
  // the bar's control and for every pane legend's, so a bucketing added in one
  // is on offer in the others immediately.
  const tfOptions = useTimeframeOptions();
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
    // Whether the distances above were chosen or derived, and from what. A
    // pinned leg is a different decision from the same distance typed — it goes
    // on tracking the size — and the attempt should say which one was traded.
    stopUsd,
    targetUsd,
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
  // The account's floor caught this sitting. Set the moment `accountStop`
  // fires and never unset within the session — not by going flat, not by a
  // rewind — because the one thing a blown account must not have is a next
  // trade in the sitting that killed it. The refetched view catches the same
  // state a beat later (`accountRefusal` outranks an open sitting when the
  // account is dead); this ref is what closes the gap before it lands, and
  // what survives the refetch coming back "live" when the floor was only
  // grazed and the settled net recovered past it.
  const sittingDeadRef = useRef(false);

  // What a leg switched back on goes back to. Turning a leg off is a trading
  // decision, not a reason to forget the distance you were using — so the last
  // live value is kept here and the toggle restores it.
  const lastStopRef = useRef(prefs.stopTicks || DEFAULT_SIM_PREFS.stopTicks);
  const lastTargetRef = useRef(prefs.targetTicks || DEFAULT_SIM_PREFS.targetTicks);
  // Setting a distance **unpins** the leg, everywhere and without exception: a
  // preset, a sizer cell, a knob, a typed box and a dragged level are all
  // statements that this is the distance, and a pin left standing would put the
  // money's answer back over the top of it on the next render. The two are one
  // setting with two units, never two settings.
  const applyStop = useCallback((t: number) => {
    const v = Math.max(0, Math.floor(t) || 0);
    if (v > 0) lastStopRef.current = v;
    setStopUsd(null);
    setStopTicks(v);
  }, []);
  const applyTarget = useCallback((t: number) => {
    const v = Math.max(0, Math.floor(t) || 0);
    if (v > 0) lastTargetRef.current = v;
    setTargetUsd(null);
    setTargetTicks(v);
  }, []);
  /** Pin a leg to a dollar figure, or (`null`) unpin it where it stands.
   *
   *  The distance is written alongside the pin rather than left to re-derive:
   *  see `pinnedLeg` — it is what the leg falls back to when there is no tape
   *  loaded to price it against, and a stale one there is a bracket nobody
   *  chose. */
  //  `atSize` is for the one edit that changes two things at once: the long-press
  //  ticket can move the size and the pin in the same act, and resolving the new
  //  money against the old size would leave the fallback distance describing a
  //  ticket that never existed.
  const pinStop = useCallback(
    (usd: number | null, atSize = size) => {
      const next = pinnedLeg(usd, stopTicks, tickUsd, atSize);
      if (next.ticks > 0) lastStopRef.current = next.ticks;
      setStopUsd(next.usd);
      setStopTicks(next.ticks);
    },
    [size, stopTicks, tickUsd],
  );
  const pinTarget = useCallback(
    (usd: number | null, atSize = size) => {
      const next = pinnedLeg(usd, targetTicks, tickUsd, atSize);
      if (next.ticks > 0) lastTargetRef.current = next.ticks;
      setTargetUsd(next.usd);
      setTargetTicks(next.ticks);
    },
    [size, targetTicks, tickUsd],
  );
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
  const [presetBucket, setPresetBucket] = useState(prefs.presetBucket);
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

  // --- how far back the chart reaches -----------------------------------------
  //
  // Down here rather than beside the other context state because it is the first
  // thing on the page that needs to know what is *drawn*: the answer depends on
  // the bar, and in a split layout on every pane's bar at once.
  //
  // The bars on screen, main first. Pane 0's bucketing is the page's own `tfId`
  // — it drives the engine the fills come out of — so `paneTfIds[0]` is never
  // read, and panes past the layout's count are not drawn at all.
  const drawnTfs = useMemo(
    () => [tfId, ...paneTfIds.slice(1, paneCount)].map(timeframeById),
    [tfId, paneTfIds, paneCount],
  );
  // Whichever of them asks for most history, and which one that was. There is
  // one tape, so this cannot be per pane — an hourly beside a 1m pulls the
  // hourly's days and the 1m draws them too, which costs it nothing.
  const governingHist = useMemo(
    () => governingHistory(drawnTfs, histOverrides),
    [drawnTfs, histOverrides],
  );
  const historyDays = governingHist.days;
  /** Whether the governing bar is on a number you chose rather than the rule's. */
  const histOverridden = histOverrides[governingHist.tf.id] != null;
  /** Set — or with null, forget — the governing bar's own count. Deliberately not
   *  "the current bar's": the select shows the max across panes, so writing
   *  anywhere else would be a control whose number ignored what you typed into
   *  it. */
  const setHistoryDays = useCallback(
    (n: number | null) =>
      setHistOverrides((o) => withHistoryOverride(o, governingHist.tf.id, n)),
    [governingHist.tf.id],
  );

  const histDates = useMemo(() => {
    if (!sel || historyDays <= 0) return [];
    return (daysQ.data?.days ?? [])
      .filter((d) => d.symbol === sel.symbol && d.date < sel.date)
      .map((d) => d.date)
      .sort()
      .slice(-historyDays);
  }, [daysQ.data, historyDays, sel]);
  const histQ = useSimulatorHistory(sel?.symbol ?? null, histDates, tz);

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
      // The distances as stored, not as resolved: a pinned leg's ticks are a
      // reading of this session's tick money, and the next one may be a
      // different contract.
      stopTicks: stopTicksSet,
      targetTicks: targetTicksSet,
      stopUsd,
      targetUsd,
      trailTicks,
      trailStepTicks,
      trailBeTicks,
      trailBeOnly,
      orderType,
      reverseEntry,
      blind,
      timeframe: tfId,
      bigLots,
      historyDaysByTf: histOverrides,
      composite,
      compositeSpan,
      nodeProm,
      modernVwap: mvParams,
      dynamicSwingVwap: dsvParams,
      eventTuning: evTuning,
      eventLabelSt: evLabelSt,
      eventFillSweep: evFillSweep,
      eventFillAbsorb: evFillAbsorb,
      eventFloorSweep: evFloorSweep,
      eventFloorAbsorb: evFloorAbsorb,
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
      presetBucket,
    });
  }, [
    root,
    startTime,
    speed,
    size,
    micro,
    stopTicksSet,
    targetTicksSet,
    stopUsd,
    targetUsd,
    trailTicks,
    trailStepTicks,
    trailBeTicks,
    trailBeOnly,
    orderType,
    reverseEntry,
    blind,
    tfId,
    bigLots,
    histOverrides,
    composite,
    compositeSpan,
    nodeProm,
    mvParams,
    dsvParams,
    evTuning,
    evLabelSt,
    evFillSweep,
    evFillAbsorb,
    evFloorSweep,
    evFloorAbsorb,
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
    presetBucket,
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
  /** Is the transport actually in flow right now? Just the preference.
   *
   *  It used to take itself away whenever a position was on, to keep you from
   *  scrubbing back through your own fill — a seek truncates the log, so a
   *  rewind past the entry un-happens the trade you are in the middle of. But
   *  holding is exactly when you want Play, the speed and the clock, and hiding
   *  the whole row to prevent one illegal drag took four working controls away
   *  to stop the fifth. The rewind is now refused where every backward move
   *  already goes through (`seekTo`), and the scrubber's floor moves up to the
   *  entry so the handle can't be dragged there in the first place.
   */
  const transportShown = transportOpen;

  /** Does the panel get its own column, or lay over the tape?
   *
   *  The preference, **and always while reviewing**. A review forces the panel
   *  open, and unpinned that is 300px of overlay along the right edge — which is
   *  precisely where a seek puts the playhead. The trades were on the chart the
   *  whole time and behind the panel, which reads exactly like a review that
   *  failed to load the sitting.
   *
   *  The preference is not written, like the transport's: this is the layout a
   *  review needs, not a statement about how you want to trade. It comes back
   *  the way you left it when the review is filed. */
  const panelPinned = railPinned || reviewing;

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
  // stakes.
  //
  // Which account is **the page's mode**, and the query is keyed by it: the
  // funded one on `/charts/replay`, the paper one on `/charts/replay/paper`,
  // and none at all in a drill (`null` fetches nothing). Keying it means a
  // switch cannot show one account's equity against the other's floor for the
  // frame before the refetch lands.
  //
  // **And by the tape day**, because that is the day the account counts — the
  // one being replayed has not closed, so the floor under it does not move and
  // the daily limit is its own (`replay_account.tape_day`). The drill passes no
  // date as well as no mode: the query is disabled either way, but the date is
  // the one fact backtest mode exists to withhold and it has no business in a
  // cache key.
  const accountQ = useReplayAccount(
    acctId,
    mode === "drill" ? null : sel?.date ?? null,
  );
  const account = accountQ.data;
  /** The account *as stakes* — undefined in a drill, which has none.
   *
   *  A drill is unpriced by design: the server files the attempt as invisible to
   *  the account (routers/replays: no equity written, no epoch minted), and the
   *  bar says so. Everything on this page that lets the account *act* — the
   *  refusal before an entry, the flatten at the floor, the meters, the autopsy —
   *  reads this rather than `account`, so the two halves cannot disagree about
   *  whether a rep costs anything. A floor a rep never moves is not a floor that
   *  gets to end it.
   *
   *  `account` itself stays for the things that are about the account rather than
   *  about this sitting: the chip (already `!drill`) and the fetch the drill's own
   *  rep counter shares.
   *
   *  **Undefined at `can_reset` too**, and that is not a hole in the gate — it is
   *  the only honest reading. A resettable account is one whose epoch is *over*:
   *  its equity and its floor describe a life that ended, and the sitting about to
   *  open is on the next one, which does not exist until the create mints it
   *  (`replay_account.ensure_epoch`, called from `POST /replays` — "the next
   *  sitting is the reset"). Pricing the new sitting off the dead epoch's numbers
   *  is not caution, it is a wrong number: on paper, where every death lands on
   *  `can_reset` within the second, the first fill of the fresh account would be
   *  measured against the floor the *last* one died on and auto-flattened before
   *  it had a rep. Neither gate refuses this state — not `accountRefusal` here,
   *  not `replay_account.refusal` on the server — so nothing is being got around;
   *  the account simply has no numbers to enforce for the few hundred ms between
   *  the fill that mints it and the refetch that reports it at $50,000.
   *
   *  A **passed** account is in exactly this position and reaches it the same
   *  way, which is why the test is `can_reset` rather than a list of statuses:
   *  the epoch is over, its floor belongs to the life that won, and the next
   *  sitting is the next eval. */
  const stakes = drill || account?.can_reset ? undefined : account;
  /** Whether this sitting may drive its own clock.
   *
   *  An account that prices a real product has to cost what the day cost. A
   *  step, a scrub and a speed ladder are all the same gesture — skip the part
   *  you don't want to sit through — and sitting through it is the rep. So
   *  those sittings run forward at real time or they are paused, and Pause is
   *  the whole transport. Paper keeps the full set — it is the practice
   *  surface — and a backtest is a measurement whose whole point is getting
   *  through a session faster than it happened.
   *
   *  **Which accounts, read off the template and never off the id.** This was
   *  `acctId === "funded"` until 2026-09-10, and the registry had long since
   *  made that the wrong question: every LucidDaily account made from a
   *  template kept the ladder, the scrub and the held-Ctrl turbo, so the one
   *  account shape whose floor moves *under an open position* was the one you
   *  could skip the day on. `rules.real_time` is the same fact `rules.trailing`
   *  is — a property of the shape, stated by the server.
   *
   *  Locked while the view is still loading, which is the safe direction: the
   *  transport appearing a moment late costs a click, and a rep begun at 30×
   *  because a fetch had not landed costs the rep. `account` rather than
   *  `stakes`, because a `can_reset` account's *next* sitting is on the same
   *  template and is every bit as much a rep.
   *
   *  Locked is a property of the *rep*, not of the tape — `seekTo` still works,
   *  and has to: the start-time picker uses it to choose where the rep begins,
   *  and a review uses it to jump to a fill. Which is also why the lock lifts
   *  the moment a review is up. The rep is over by then and the day is already
   *  revealed; replaying your own decision is the point of being there. */
  const clockLocked = mode === "replay" && !reviewing && (account?.rules.real_time ?? true);
  // Read by the frame loop and by the two step callbacks, all of which run
  // outside the render that decided it.
  const clockLockedRef = useRef(clockLocked);
  clockLockedRef.current = clockLocked;
  /** The account as a *record* rather than as stakes — what it has done, as
   *  against what it is currently pricing. Only a drill has neither, and it has
   *  neither for the same reason: there is no account behind the rep at all.
   *
   *  The death band, the autopsy and the end-of-sitting recap all read this. They
   *  are the three places that say what happened, and what happened does not stop
   *  being true when the account becomes resettable — on paper that is a tenth of
   *  a second later, so a death told through `stakes` would be a death told for no
   *  time at all. */
  const ledger = drill ? undefined : account;

  // --- the blown flow -------------------------------------------------------
  // Only fetched when there is something to autopsy. The card is composed
  // entirely from these rows — the epoch's equity curve is a cumulative sum over
  // them and its totals are `replayStats.pool` — so nothing new is stored and
  // the server is not asked to re-derive what the history page already draws.
  //
  // Two questions, and conflating them is what wedged the paper account: *is
  // there a death to look at* is not *can this page trade*. The card stands until
  // the reset actually happens, because a blow-up you are never shown is a blow-up
  // that taught nothing; the ticket comes back the moment the account stops
  // refusing, because the reset **is** the next sitting and hiding the ticket is
  // hiding the only way to take it. On the funded account they part for a day and
  // the difference is easy to miss; on paper they part immediately, and while they
  // were one flag the page offered a fresh $50,000 with no way to open it.
  // Named after what it holds: an autopsy is about a *death*, so a passed
  // account does not get one. Its ending is told by the notice band and counted
  // in the record, and a card asking what killed it would be asking about a life
  // that was not killed.
  const autopsy =
    !!ledger && (ledger.status === "blown" || ledger.status === "can_reset");
  const dead = !!stakes && stakes.status !== "live";
  // The drill needs the same list for its rep counter, so the two conditions
  // share one fetch rather than the mode adding a second query on the same key.
  // `autopsy` is false in a drill whatever the account is doing, which is why the
  // `|| drill` is load-bearing rather than belt-and-braces.
  //
  // There used to be a third condition — an *owed* sitting, which the page had
  // to find before it could open into it. Gone with the auto-review on
  // 2026-08-25: nothing opens by itself now, so nothing needs finding up front.
  const attemptsQ = useReplayAttempts({ enabled: autopsy || drill });
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
  const saveTradeReview = useSaveTradeReview();
  const tradeTagsQ = useTradeTags();
  /** One card's answers: watched level, setup, discipline, tags, note.
   *
   *  `mutateAsync`, so a caller can wait for it: filing a review saves every
   *  card that is still dirty first, and the server checks the *stored* rows —
   *  so the writes have to have landed before the PATCH goes. Every caller
   *  either awaits it or catches; an ignored rejection here would be an
   *  unhandled one.
   *
   *  **One write.** It used to be two, because the thesis lived on its own table
   *  so that `PUT /notes`'s whole-row overwrite could not blank it. The grade
   *  and the watched level are partial fields on that same endpoint instead —
   *  omitted means unchanged — so the hazard is handled at the door and the
   *  review is a single request.
   *
   *  The setups, confluences and model on the notes row are still echoed
   *  untouched. The review does not ask for them, but the Trades page and the
   *  journal read them, and a review that quietly blanked taxonomy written
   *  elsewhere would be destroying records to satisfy a form that stopped
   *  caring about them. */
  const saveTradeAnswers = useCallback(
    async (
      row: DrillTradeRow,
      patch: ReviewAnswers & { modelId?: number | null; rulesMet?: number[] },
    ) => {
      await saveTradeReview.mutateAsync({
        tradeKey: row.trade_key,
        note: patch.note,
        tags: patch.tags,
        setups: row.setups,
        confluences: row.confluences,
        modelId: patch.modelId !== undefined ? patch.modelId : row.model_id,
        rulesMet: patch.rulesMet ?? row.rules_met,
        setup: patch.setup,
        discipline: patch.discipline,
        watchedLevels: patch.watchedLevels,
      });
    },
    [saveTradeReview],
  );
  // Why 🎲 cannot draw *right now*, beyond the standing reasons: the rep on
  // screen still has unreviewed trades. The server refuses the create the same
  // way (409 `drill_review`), so a reload changes nothing — the way back into
  // a lost rep-end panel is the history page's review button.
  const repOwes =
    DRILL_REVIEW_REQUIRED &&
    drill &&
    repOver &&
    (repJournalQ.data?.trades ?? []).some((t) => !reviewAnswered(seedAnswers(t)));
  const drillDrawBlocked =
    drillBlocked ??
    (repOwes
      ? "Review every trade of this rep first — a level, a setup and a discipline call each, and the next draw opens."
      : null);
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
  // Never fires on paper (nothing there waits on a sentence) but it is keyed by
  // mode anyway, so the answer lands on the same cache entry it was asked from.
  const writeCause = useWriteCause(acctId ?? "funded");

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
      saveResume(scope, {
        symbol: a.symbol,
        date: a.date,
        clockMs: a.started_ms,
        attemptId: a.id,
        // Unused in review mode — every cursor is rebased off its own timestamp.
        contextTicks: 0,
      });
      saveReview(scope, { attemptId: a.id });
      navigate(0);
    },
    [attemptsQ.data, mode, navigate],
  );

  /** The other account. What the chip does when you click its name.
   *
   *  A navigation, because the account is the route (see `SimMode` and the two
   *  entries in `router.tsx`): the keyed element makes this a real unmount, so
   *  the recorder cannot survive the switch still pointing at the ledger you
   *  just left. Each side keeps its own bookmark and its own review marker, so
   *  the sitting you were parked on is where you left it when you come back.
   *
   *  Offered only when this account is *clean* — no sitting open, no review
   *  owed. The first is the rule the mode already lives by (fixed for the whole
   *  of a sitting). The second is the same rule the review gate is: a review you
   *  can walk away from by changing accounts is not a gate, it is a suggestion,
   *  and the whole reason paper owes reviews at all is that it is not meant to
   *  be the way out of one. */
  //
  //  It is no longer a toggle between two: `AccountSwitch` navigates, and this
  //  is what it calls first so the bookmark for the account being left is
  //  written before the unmount takes the page with it.
  const switchAccount = useCallback(() => {
    writeResume();
  }, [writeResume]);

  // Which day the picker opens on.
  //
  // **Nothing opens as a review any more.** Until 2026-08-25 an owed review came
  // first: the page found the sitting the account was blocking on and landed in
  // it, because trading a session the server would then refuse to record was
  // worse than being made to answer. Reviewing is a choice now, so the page has
  // no business choosing it — a review is entered from the end-of-sitting
  // prompt or from the history page, both of which write the same marks this
  // used to.
  //
  // What is left is two rules:
  // 1. **A session you were in the middle of wins over a draw** — "carry on"
  //    is what you want far more often than "start again", and the tape you
  //    were halfway through is not one you can practise blind on anyway. Since
  //    the mode split this holds for drills too, with one condition: the rep's
  //    attempt must still be `active`. Resuming a settled one would flip it
  //    back and withdraw the review it may already have filed.
  // 2. **Failing that it is a draw**, on purpose — the replay is only practice
  //    while the tape is one you don't remember. Pick another with the
  //    dropdown, or draw again with 🎲.
  //
  // A bookmarked day that isn't in the list falls through to the draw: the
  // root is a preference saved next to it so the two normally agree, but a
  // contract whose cache has since been cleared out is not there to go back to.
  useEffect(() => {
    const days = daysQ.data?.days;
    if (sel || !days?.length) return;
    const p = pendingRef.current;
    if (p && days.some((d) => d.symbol === p.symbol && d.date === p.date)) {
      // A review entered from the history page arrives as marks plus a
      // bookmark naming the same attempt; it is honoured whatever the
      // attempt's status — review mode is read-only, so "active" is not a
      // condition it needs.
      const reviewingThis = !!reviewMark && p.attemptId === reviewMark.attemptId;
      if (!drill || reviewingThis) {
        setSel({ symbol: p.symbol, date: p.date });
        return;
      }
      if (p.attemptId) {
        if (!resumeSettled) return;
        if (resumeDetailRef.current?.status === "active") {
          setSel({ symbol: p.symbol, date: p.date });
          return;
        }
      }
      // A settled (or attempt-less) rep is not a place to go back to — spend
      // the bookmark and draw.
      leaveResume();
    }
    anyDay(days);
  }, [anyDay, daysQ.data, drill, leaveResume, mode, resumeSettled, reviewMark, sel]);
  // Everything the rules and the behaviour strip need, re-derived whenever the
  // simulation is. Cheap: a couple of passes over a day's trades.
  const day = useMemo(() => dayState(guards, trades), [guards, trades]);
  // Forward-looking, unlike `day` — a statement about the entry not yet placed
  // rather than a median over the ones that were. Same input, so it rides the
  // same re-derivation; `guardRules.paceRefusal` takes no clock, deliberately.
  const pace = useMemo(() => paceRefusal(trades), [trades]);

  /** What a position is worth at a price, in dollars. Zero when there is none.
   *
   *  **The position is an argument rather than a read of `openRef`**, and that
   *  is the whole point of the function's shape. What is held and what has been
   *  booked change together, in `publish`, on the frame a fill lands; the mark
   *  is an 80ms sample. Reading the position from wherever this happens to be
   *  called lets a caller pair a *booked* trade with the same trade still shown
   *  as open — the loss counted twice, once realised and once unrealised.
   *
   *  That is not hypothetical. It ended two sittings on the 25K daily account on
   *  2026-08-25 at −$240.50 and −$247.50 against a $300 daily limit, each within
   *  a frame of the stop that booked them: `day.realized` had the loss, the HUD
   *  sample still had the position, and `accountBreach` added the two together.
   *
   *  So: the caller says which position, and the only stale thing left is the
   *  price — which is the documented "lands a beat late", and can only
   *  under-report a live position, never invent a closed one. */
  const markOpen = useCallback(
    (p: Position | null, lastPrice: number): number => {
      if (!p || !Number.isFinite(lastPrice)) return 0;
      const dir = p.side === "long" ? 1 : -1;
      // The position's contract, not the ticket's. They are the same until the
      // ticket is re-pointed after a fill, and that is precisely the moment this
      // number must not move: what is held is what is held.
      const pv = tapePointValue / (p.micro ? MICRO_RATIO : 1);
      return (lastPrice - p.entryPrice) * dir * pv * p.size;
    },
    [tapePointValue],
  );

  /** The open position's value **at one instant**, and the only figure any rule
   *  or readout on this page may use for it.
   *
   *  `openPos` and `day.realized` are both published by `publish`, so they are
   *  the same instant by construction: the frame a trade books, the position is
   *  already null here and its loss is in `realized` exactly once. Only the
   *  price is sampled — see `markOpen`. */
  const openNow = markOpen(openPos, hud.lastPrice);

  /** Is the *next* market click going to be flipped? The render-time twin of
   *  the test in `placeMarket`, off the state rather than the ref because a
   *  label has to repaint when the position appears — and the moment it does,
   *  the knob stops applying and the buttons go back to saying what they do.
   *  The two readings agree by construction: `openPos` and `openRef` are both
   *  written by `publish`. */
  const reversing = reverseEntry && openPos == null;

  // The account plus the sitting on screen, worked out once. Both things that
  // read a live equity — the floor meter and the auto-flatten — take this, so
  // they cannot disagree about how much room is left, and neither of them
  // assembles it from parts any more: whether this sitting is already in
  // `equity` is a question only the account can answer (see `liveEquity`).
  //
  // `attempt.id` rather than `attemptIdOf()`: this has to re-derive when the
  // recorder opens or adopts an attempt, and a ref does not tell React that.
  //
  // **A pair, not a figure.** On an intraday-trailing account the floor is a
  // live number too and has the same `counted_ids` dependency the equity has;
  // producing them apart would let the meter draw one sitting's equity against
  // another's floor. `hud.peakUsd` is the sitting's own high-water off the
  // simulation *fold*, never off this sampled tick — see `liveAccount`.
  const live = liveAccount(
    stakes,
    attemptRec.attempt?.id ?? null,
    day.realized,
    openNow,
    hud.peakUsd,
  );
  const equityNow = live?.equity ?? null;

  // How close this sitting has come to the floor it was under **at the time**,
  // and the one figure here that is a ratchet rather than a fold.
  //
  // That is the opposite rule to `SimState.peakUsd`, deliberately, and the
  // difference is worth stating where both are in view. The peak is re-derived
  // on every rewind, because an excursion inside a trade that has been
  // un-happened must un-happen with it. This is a record of what the page was
  // *told* at a moment — the account said "you have $40 of room" and the
  // sitting went on — and re-deriving it after a rewind would forget a breach
  // that was announced and acted on. A forgotten breach is the escape hatch the
  // account exists to close, so this one only ever tightens.
  const minRoomRef = useRef<number | null>(null);
  useEffect(() => {
    if (live == null || reviewingRef.current) return;
    const prev = minRoomRef.current;
    if (prev == null || live.room < prev) minRoomRef.current = live.room;
  }, [live]);

  // --- The risk sizer -------------------------------------------------------
  // The vol ruler's current reading, pushed up by the chart on every bar close
  // (it computes whether or not the pane is drawn, so this keeps up with a
  // collapsed pane). Null until enough bars have closed to say anything. The
  // sizer's own number is not in here — it reads `volRead.preset` through
  // `presetCtx` below, so Σ and A–D measure the same tape the same way.
  const [volRead, setVolRead] = useState<VolRulerRead | null>(null);
  // Only one account number reaches the sizer now, and it is the one that
  // moves: room-to-the-floor, which the losers-to-death column divides, because
  // counting down is that figure's entire job. The budgets are constants in the
  // lib (`BUDGETS_USD`) — they were `rules.day_loss ÷ losers` here, and before
  // that `day_loss_remaining ÷ losers`, which halved through a bad day. A drill
  // has no account and falls back to the LucidPro floor it is practising for.
  const sizerCtx = useMemo<SizerCtx | undefined>(() => {
    if (!(tickUsd > 0)) return undefined;
    const roomToFloor = equityNow != null && stakes ? equityNow - stakes.floor : null;
    return {
      // The *mini's* money and the mini's fee rate — the sizer derives the
      // micro's itself, and would double-shrink a ticket already pointed at one.
      tickUsd: onMicro ? tickUsd * MICRO_RATIO : tickUsd,
      commissionPerSide: fills.commission,
      maxLossUsd: roomToFloor != null && roomToFloor > 0 ? roomToFloor : DEFAULT_MAX_LOSS,
      caps: { minis: stakes?.caps.minis ?? 4, micros: stakes?.caps.micros ?? 40 },
      stopTicksMax: guards.stop_ticks_max,
      root,
      microRoot: microSym,
      micro: onMicro,
      canSwitchContract: !contractLocked,
    };
  }, [
    contractLocked, equityNow, fills.commission, guards.stop_ticks_max, microSym,
    onMicro, root, stakes, tickUsd,
  ]);

  // The presets' ruler and the bar it is read at. The reading carries all three
  // bucketings at once (lib/volRuler), so the toggle is a pick out of a record
  // rather than a re-measure — which is what lets it be a comparison you flick
  // through rather than a setting you commit to.
  const presetCtx = useMemo<PresetCtx | null>(
    () =>
      volRead ? { read: volRead.preset, bucket: presetBucket, onBucket: setPresetBucket } : null,
    [volRead, presetBucket],
  );

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
    recordAttempt(logRef.current, st.trades, st.open != null, clock, {
      peakUsd: st.peakUsd,
      troughUsd: st.troughUsd,
      minRoomUsd: minRoomRef.current,
    });
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

  const pushHud = useCallback(
    (lastPrice: number, clockMs: number, force = false) => {
      const now = performance.now();
      if (!force && now - lastHudRef.current < 80) return;
      lastHudRef.current = now;
      const { ib, range } = geoRef.current;
      // `peakUsd` is read off the simulation ref, which `stepSim` has already
      // updated in place for this frame. The asymmetry with the mark is the
      // point: the peak is a *fold* while the price is still an 80ms sample,
      // and that is safe precisely because the peak is monotone within a
      // sitting and the equity is not — a peak that spiked and retreated
      // between two of these ticks is still in the fold, an equity that did the
      // same is gone. Which is why the floor may be derived from one and the
      // "lands a beat late" caveat still only applies to the other.
      setHud({
        clockMs, lastPrice,
        peakUsd: simRef.current.peakUsd,
        gen: sessGenRef.current, ib, range,
      });
    },
    [],
  );

  // --- armed levels ---------------------------------------------------------
  // A level you asked to trade itself (lib/levelArm). `through` and `exit` stand
  // here: a `bid` places its order the moment you ask for it and then has
  // nothing left to wait for, so it never becomes state — cancel it the way you
  // cancel any other resting order.
  const [arms, setArms] = useState<LevelArm[]>([]);
  const armsRef = useRef<LevelArm[]>([]);
  armsRef.current = arms;
  /** Race the standing arms: the first to fire cancels the others *of its own
   *  purpose*. Off by default — arms firing independently is the plainer rule,
   *  and the race is the one that quietly removes something you set up. */
  const [armRace, setArmRace] = useState(false);
  const armRaceRef = useRef(false);
  armRaceRef.current = armRace;
  /** Firing needs `placeAt` and `closeManual`, both declared with the order
   *  plumbing far below — the same reason `mark` reaches its price lines through
   *  a ref. */
  const fireArmRef = useRef<(a: LevelArm, act: ArmAction) => void>(() => {});

  // Fold a played tick range into the running simulation, and only re-render the
  // panel on the step where something actually resolved.
  const advanceSim = useCallback(
    (from: number, to: number, clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      const st = simRef.current;
      stepSim(tape, logRef.current, st, from, to, clock, fillCfg);
      // Armed levels, against the prints that just played.
      //
      // Here rather than in the chart's own `checkAlerts`, which is the other
      // place a crossing is already detected, because that one hangs off `mark`
      // — and `mark` is the funnel *every* path that moves the price goes
      // through, a seek included. An alert firing on a rewind is a stray chime;
      // an order placed on one is a trade you did not make. `advanceSim` has
      // exactly two callers and both of them run the tape forwards.
      //
      // After `stepSim`, so an order an arm places cannot fill on the same
      // prints that triggered it — it becomes fillable from the next range, and
      // pays `latencyMs` from its own stamp like every other order.
      const armed = armsRef.current;
      if (armed.length && from > 0) {
        const prev = tape.price[from - 1];
        for (const a of armed) {
          // Against `armsRef` rather than the `armed` snapshot: firing mutates
          // the ref, and with the race on that is how the arms this one just
          // cancelled are skipped instead of firing off the stale list.
          if (!armsRef.current.includes(a)) continue;
          const act = armAction(a, tape.price, from, to, prev, tape.tickSize);
          if (act) fireArmRef.current(a, act);
        }
      }
      if (simSig(st) !== sigRef.current) publish(st, clock);
      // The equity path moves on prints, not on fills, so it has to be offered
      // on prints too — a runner that goes +800 and comes back flat changes
      // nothing in `simSig` and would otherwise never be written, which is
      // exactly the sitting the figures exist for. Cheap: `record` builds one
      // string and compares it, returning immediately on every frame the
      // quantised path did not cross a step.
      else
        recordAttempt(logRef.current, st.trades, st.open != null, clock, {
          peakUsd: st.peakUsd,
          troughUsd: st.troughUsd,
          minRoomUsd: minRoomRef.current,
        });
    },
    [fillCfg, publish, recordAttempt],
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
      // A locked rep runs at the speed the day ran at, and this one expression
      // is what makes that true — it takes the ladder, the `[`/`]` keys and the
      // held-Ctrl turbo out in one place rather than in three. The stored speed
      // pref is deliberately left alone: it belongs to the pages that still have
      // a ladder, and forcing it to 1 here would reset theirs.
      const rate = clockLockedRef.current ? 1 : speedRef.current * turbo.mult.current;
      const { clock, atEnd } = src.clockFor(clockRef.current, dtReal, rate);
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
      // A drill used to refuse every backward move here (plan D8, superseded
      // 2026-08-21): a rewound rep is a rep that already knew the answer, and
      // the base rate the mode exists to measure cannot survive being pooled
      // with those *unmarked*. Marking turned out to be the cheaper half of
      // that sentence — the rewind record below is written in every mode, so a
      // rewound rep now arrives at the campaign carrying the fact, and
      // `/replays/drills` counts it out loud rather than the transport refusing
      // to let it exist. Everything else about the rep is unchanged.
      //
      // **Not back past your own entry while you are holding.** The transport
      // stays on the page with a position on — you want Play, the speed and the
      // clock most while you are in something — but the one move it cannot
      // honestly carry there is a rewind through the fill: the log truncates,
      // the entry un-happens, and you are handed a do-over of a trade you were
      // in the middle of. Everything else is allowed, including a rewind to
      // somewhere after the entry, which only un-does a scale or a bracket drag.
      //
      // Same choke point as the drill rule above, for the same reason: the
      // scrubber's `min` moves to the entry so the control looks like what it
      // does, and this catches ⏪, `,` and the start-time jump.
      //
      // **Never in a review.** The whole hazard is the truncation, and a review
      // does not truncate (above) — the position on screen is a replayed one,
      // not size you are carrying, and nothing there can un-happen. Refusing
      // would mean the cards stop being reachable the moment the tape plays into
      // a fill: seek to the 09:42 trade, watch it fill, and every earlier card
      // is refused until it closes. Every card, at any time, is the point.
      const held = reviewingRef.current ? null : simRef.current.open;
      if (held && clockMs < held.fillMs) {
        setRefused("Flatten first — a rewind past your entry un-happens the trade you are in.");
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
      //
      // **Never in a review.** That rule is about a sitting you are *in*, where
      // going back means the part you hadn't done yet hasn't been done. A review
      // is a reading of a sitting that already finished: the log is the record,
      // not a draft, and truncating it deleted the very trade the seek was
      // aimed at — press "seek" on a card, watch its five seconds run past the
      // entry, and nothing ever fills, because the order had been thrown away on
      // the way there. (Every later trade with it: one seek to an early card
      // wiped the rest of the sitting off the tape.) Nothing in review mode can
      // write, so leaving the log whole costs nothing and is the only thing that
      // makes "watch it again" mean anything.
      if (!reviewingRef.current) logRef.current = truncateLog(logRef.current, clamped);
      const snap = eng.snapshotTo(clamped);
      // The view follows the clock at the zoom the user set: a seek is a move
      // through time, not a request to be put back at the default bar spacing.
      chartRef.current?.setSnapshot(snap, { reframe: "follow" });
      // The clock must move before the panes resync: `resyncPane` snapshots the
      // extra panes to `clockRef`, and their engines only step forward — a pane
      // resynced to the pre-seek clock is stranded there until playback catches
      // back up, frozen from the user's side.
      clockRef.current = clamped;
      resyncPane("follow");
      // A rewind un-develops the day's range along with everything else: the
      // snapshot is re-derived from tick zero, so the extremes are whatever had
      // actually printed by the new clock.
      geoRef.current = { ib: snap.ib, range: snap.range };
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
      // Not while reviewing: nothing un-happened (the log is left whole above),
      // and a review that recorded rewinds would be flagging the sitting for the
      // act of looking at it.
      if (clamped < from && !reviewingRef.current) {
        const dropped = had.slice(simRef.current.trades.length);
        if (dropped.length || (hadOpen && !simRef.current.open))
          noteRewind(from, clamped, dropped);
      }
    },
    [noteRewind, pushHud, rebuild, stop],
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
  const ticket = useMemo<TicketDraft>(
    () => ({ size, stopTicks, targetTicks, stopUsd, targetUsd }),
    [size, stopTicks, targetTicks, stopUsd, targetUsd],
  );
  /** The ticket, changed from a chart. One handler for every pane: size and the
   *  bracket belong to the page, so the number in a long-press ticket and the
   *  number a dock button sends are the same number whichever chart you are on.
   *
   *  A leg arrives as one of two statements — a distance, or the money it is
   *  pinned to — and each goes to the setter that means it. The distances on a
   *  pinned leg are the page's own resolution coming back, so re-applying them
   *  as distances would silently unpin every leg on every edit. */
  const changeTicket = useCallback(
    (t: TicketDraft) => {
      setSize(t.size);
      if (t.stopUsd != null) pinStop(t.stopUsd, t.size);
      else applyStop(t.stopTicks);
      if (t.targetUsd != null) pinTarget(t.targetUsd, t.size);
      else applyTarget(t.targetTicks);
    },
    [applyStop, applyTarget, pinStop, pinTarget],
  );

  // The ticket asks nothing before an order goes out. It briefly asked for a
  // thesis; that vocabulary is gone (docs/trade-grading-plan.md, G1), and the
  // grade that replaced it is a review-time question by decision G3 — a scale
  // on the fast path is one that gets answered carelessly.
  const vocab = useReviewVocab();

  /**
   * A preset cell on the risk sizer was clicked: take its stop and its size,
   * and point the ticket at the contract the cell was quoting.
   *
   * The contract half is why this is here and not inside `TicketKnobs` — the
   * mini/micro switch is the page's, and it is refused outright while there is
   * a position or a working order (`contractLocked`: the two instruments do not
   * net). All or nothing: a cell that moved the stop but not the size would
   * leave a ticket nobody chose, which is worse than a cell that does nothing.
   * The panel already greys the cells this could not honour (`canSwitchContract`),
   * so reaching the guard here is the belt to that braces.
   */
  const applySizing = useCallback(
    (a: { stopTicks: number; size: number; micro: boolean }) => {
      if (a.micro !== micro) {
        if (contractLocked) return;
        setMicro(a.micro);
      }
      applyStop(a.stopTicks);
      setSize(a.size);
    },
    [applyStop, contractLocked, micro],
  );

  /**
   * A bracket preset was chosen: the whole shape, in one act.
   *
   * All five distances or none — that is what makes it a preset rather than five
   * suggestions. Zero is a real value on every leg here (the target off for A,
   * the first rung on the entry itself), so each one is set outright rather than
   * merged into whatever the ticket was carrying: a preset that left yesterday's
   * target standing would be a shape nobody chose, which is the failure this is
   * for. Size is untouched — that is the Σ sizer's question.
   */
  const applyPreset = useCallback(
    (b: PresetBracket) => {
      applyStop(b.stopTicks);
      applyTarget(b.targetTicks);
      applyTrail(b.trailTicks);
      setTrailStepTicks(b.trailStepTicks);
      setTrailBeTicks(b.trailBeTicks);
      setTrailBeOnly(b.trailBeOnly);
    },
    [applyStop, applyTarget, applyTrail],
  );

  /** The five distances the ticket is carrying now, in the shape a preset is
   *  written in — so the card can light whichever preset equals it, and light
   *  nothing once a knob has been nudged off one. Built here rather than in the
   *  card because three of the six legs are this page's state and not the
   *  ticket's. */
  const activeBracket = useMemo<PresetBracket>(
    () => ({ stopTicks, targetTicks, trailTicks, trailStepTicks, trailBeTicks, trailBeOnly }),
    [stopTicks, targetTicks, trailTicks, trailStepTicks, trailBeTicks, trailBeOnly],
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
    () => ({
      tuning: evTuning,
      style: { labelSt: evLabelSt, fillSweep: evFillSweep, fillAbsorb: evFillAbsorb },
      floorSweep: evFloorSweep,
      floorAbsorb: evFloorAbsorb,
      marginal: evMarginal,
    }),
    [evTuning, evLabelSt, evFillSweep, evFillAbsorb, evFloorSweep, evFloorAbsorb, evMarginal],
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
        dynamicSwingVwap: { params: dsvParams, onChange: patchDsv },
        volumeShelf: {
          params: shelfParams,
          onChange: patchShelf,
          field: shelfField,
          onField: patchShelfField,
        },
        composite: {
          rule: composite,
          onRule: setComposite,
          span: compositeSpan,
          onSpan: setCompositeSpan,
          note: `Built from the ${historyDays} prior session${historyDays === 1 ? "" : "s"} loaded — "Prior days" in the setup row, since each one is a whole tape to fetch.`,
        },
        events: {
          tuning: evTuning,
          labelSt: evLabelSt,
          fillSweep: evFillSweep,
          fillAbsorb: evFillAbsorb,
          floorSweep: evFloorSweep,
          floorAbsorb: evFloorAbsorb,
          marginal: evMarginal,
          onTuning: changeEvTuning,
          onLabelSt: setEvLabelSt,
          onFillSweep: setEvFillSweep,
          onFillAbsorb: setEvFillAbsorb,
          onFloorSweep: setEvFloorSweep,
          onFloorAbsorb: setEvFloorAbsorb,
          onMarginal: setEvMarginal,
        },
      }),
    [
      bigLots,
      changeBigLots,
      changeEvTuning,
      composite,
      compositeSpan,
      evFillSweep,
      evFillAbsorb,
      evFloorSweep,
      evFloorAbsorb,
      evLabelSt,
      evMarginal,
      evTuning,
      historyDays,
      mvParams,
      dsvParams,
      shelfParams,
      shelfField,
      nodeProm,
      patchMv,
      patchDsv,
      patchShelf,
      patchShelfField,
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
    // Same wait for the review's own record. It is a *separate* fetch from the
    // bookmark's whenever the two name different attempts — and one of the ways
    // this page loses a sitting's trades is building the tape before it lands,
    // since `reviewing` cannot even be true until it has.
    if (fresh && reviewMarkRef.current?.attemptId && !reviewSettled) return;
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
      sittingDeadRef.current = false;
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
        // **Which sitting's log this is.** Reviewing, it is the one the review
        // marker names, off the review's own fetch — not whatever the bookmark
        // remembers. The bookmark is a record of where you were sitting, and in
        // review mode there is no sitting: the recorder is unarmed, so what it
        // used to write there was `attemptId: null`, and the id test below then
        // failed on every visit after the first. Reading the id from the marker
        // makes the review independent of it — including for a bookmark already
        // written with the null, which is the state a browser that has been in a
        // review before is in right now.
        const reviewingNow = reviewingRef.current;
        const wantId = reviewingNow
          ? (reviewMarkRef.current?.attemptId ?? null)
          : bookmark.attemptId;
        const d = reviewingNow ? reviewDetailRef.current : resumeDetailRef.current;
        // The stored cursors only mean anything if the session under them is
        // still the tape it was — the tick cache is not immutable, and the
        // 16:00-17:00 gap fix re-fetched 352 sessions and moved every index in
        // them — and if the fills would be resolved by the same rules. Either
        // check failing costs the trades, not the day: the clock still goes back
        // where you left it and you carry on reading from there.
        const usable =
          d != null &&
          d.id === wantId &&
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
        accountId: acctId,
        modelId: drill ? drillRef.current.modelId : null,
        dropMs: drill ? clock : null,
        window: drill ? drillWindowRef.current : null,
      });
      // A resumed sitting continues the attempt it came from rather than opening
      // a second one on the same day — which the history page would read,
      // correctly by its own rules and wrongly in fact, as a re-run of a session
      // you had already seen the end of.
      const d = reviewingRef.current ? null : resumed?.detail;
      // And in a drill, open it here rather than waiting for a fill: a rep you
      // looked at and passed on is the row this mode exists to write. After
      // `arm`, which is what gives it a context to write against — and never
      // over an adoptable record: `open`'s flush fires the create immediately,
      // so a resumed rep opening first would mint a duplicate attempt that the
      // adopt below could not take back.
      if (drill && !reviewingRef.current && !d) openAttempt(logRef.current, clock);
      if (d) {
        // A resumed rep's drop is put back for the title — the bookmark's
        // clock outranks it for where the tape stands, but the drop is what
        // names the question the rep was.
        if (drill && d.drop_ms != null) {
          setDrop(Math.round((d.drop_ms - data.rth_open_ms) / 60_000) + RTH_OPEN_MIN);
        }
        adoptAttempt(d, {
          log: logRef.current,
          trades: d.trades,
          rewinds: d.rewinds ?? [],
          discarded: d.discarded ?? [],
          // The sitting spans from the first fill of the *first* visit, not from
          // the clock this one opened at.
          startedMs: d.started_ms,
          clockMs: clock,
          // The equity path, and the two halves of it behave differently on a
          // resume. `peakUsd`/`troughUsd` are folds: `rebuild` below re-derives
          // them from the adopted log exactly, so seeding them from the stored
          // summary would only be overwritten by the truth a moment later.
          // `minRoomUsd` is not a fold — it was measured against the floors this
          // sitting was under on its *previous* visit, which this page does not
          // have and cannot recompute — so the stored figure is carried in, and
          // `minRoomRef` keeps the lower of it and whatever this visit sees.
          // A resumed sitting that quietly forgot a breach would be the escape
          // hatch the account exists to close.
          minRoomUsd: d.summary?.min_room_usd ?? null,
        });
        minRoomRef.current = d.summary?.min_room_usd ?? null;
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
  }, [sessionQ.data, contextTapes, contextRanges, engineContext, resumeSettled, reviewSettled]);

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
   *  for a market order, the resting price for a limit or a stop.
   *
   *  Returns whether the order was actually logged. Every human path ignores the
   *  answer — the refusal is already on screen, and a click that was refused has
   *  nothing left to do. An *armed level* is the caller that needs it: it has to
   *  disarm itself either way, or a rule it cannot satisfy would be re-attempted
   *  on every print for the rest of the session. */
  const placeOrder = useCallback(
    (type: OrderType, side: Side, price: number | null, at: number): boolean => {
      const eng = engineRef.current;
      if (!eng) return false;
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
        return false;
      }
      const reducing = isReducing(openRef.current, side, size);
      // The account comes first, and it comes *outside* `guardsOn`: the
      // guardrails are rules under test and the switch is what makes them
      // testable, but the account is the stakes. It only speaks when there is
      // no sitting open yet — resuming one you are in the middle of is free,
      // and the thing being priced is starting another. Silent in a drill,
      // which has no stakes to speak for (see `stakes`).
      if (!reducing) {
        // The floor already ended this sitting. Refused here, off the page's
        // own memory of the death, because the server only learns of it when
        // the attempt settles and the view refetches — a beat during which
        // "trade the account back" would otherwise still work. Which it did:
        // the sitting that found this bug dived $109 through the floor,
        // re-entered, and settled $2 alive.
        if (sittingDeadRef.current) {
          setRefused(
            "the account died this sitting — the floor already closed it, and there is " +
              "no version of a blown account you trade back from in the same session.",
          );
          playCue("canceled");
          return false;
        }
        const no = accountRefusal(stakes, attemptIdOf() != null);
        if (no) {
          setRefused(no.message);
          playCue("canceled");
          return false;
        }
      }
      if (!reducing && guardsOn) {
        const why =
          dayRefusal(day, false) ??
          shapeRefusal(guards, { stopTicks, targetTicks, size, tickUsd });
        if (why) {
          setRefused(why);
          playCue("canceled");
          return false;
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
        // ...and the same bracket as the ticket actually said it: distances.
        // A market order's legs hang off the *fill* (see `OrderRec.stopTicks`),
        // so the prices above are only what you were looking at when you
        // clicked. A resting order carries no distances at all — its bracket
        // belongs to the level it was drawn against.
        ...(type === "market" ? { stopTicks, targetTicks } : {}),
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
        // And what you expect it to do — the same stamping rule with one extra
        // reason: a claim is only worth grading if it was made before the
        // outcome. Read off a ref rather than the state so the value is the one
        // showing on the ticket at the instant the gesture fired.
      };
      // A market order is its own fill and the rebuild below will sound as one —
      // the tick and the chime a few milliseconds apart would read as one
      // stuttering noise. Only an order that goes on to *rest* gets the tick.
      if (type !== "market") playCue("placed");
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
      return true;
    },
    [stakes, append, attemptIdOf, day, guards, guardsOn, onMicro, size,
     stopTicks, targetTicks, tickSize, tickUsd, trailTicks, trailStepTicks,
     trailBeTicks, trailBeOnly],
  );

  const placeMarket = useCallback(
    (side: Side) => {
      if (!ready) return;
      const px = markPrice();
      if (!Number.isFinite(px)) return;
      // The reverse knob, applied here and nowhere else.
      //
      // Here because this is the one path where the side is a *choice* — two
      // buttons, either of which is placeable at the mark. The resting paths
      // below it look like they take a side too, but `placeAt` reads one off
      // which side of the mark you clicked and the long-press menu greys out
      // the pairs that can't sit at that price; a flipped bid above the offer
      // is not an order, and `placeResting` would only shove it back across.
      //
      // Read off the ref, not the state: the flip has to be decided against
      // the position as it stands at the instant of the click, and a render
      // behind is a doubled position.
      const flipped = reverseEntry && openRef.current == null;
      placeOrder("market", flipped ? otherSide(side) : side, null, px);
    },
    [markPrice, placeOrder, ready, reverseEntry],
  );

  /** Rest an order at a price, held one tick clear of the mark on the side its
   *  type belongs on — a marketable resting order would fill on the next print
   *  at a price better than the market, which is not a thing the tape can do. */
  const placeResting = useCallback(
    (price: number, side: Side, type: "limit" | "stop"): boolean => {
      if (!ready) return false;
      const mk = markPrice();
      if (!Number.isFinite(mk) || !Number.isFinite(price)) return false;
      const px = Math.round(price / tickSize) * tickSize;
      const above = type === "stop" ? side === "long" : side === "short";
      const rest = above ? Math.max(px, mk + tickSize) : Math.min(px, mk - tickSize);
      return placeOrder(type, side, rest, rest);
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
    (price: number, button: "left" | "right"): boolean => {
      const mk = markPrice();
      if (!Number.isFinite(mk)) return false;
      const below = price < mk;
      const passive = button === "left";
      const side: Side = passive === below ? "long" : "short";
      return placeResting(price, side, passive ? "limit" : "stop");
    },
    [markPrice, placeResting],
  );

  /** Arm a level, or disarm it.
   *
   *  `bid` is not an arm at all — it places its order now and there is nothing
   *  left to stand. `through` and `exit` are, because both are waiting for a
   *  crossing that has not happened.
   *
   *  Deliberately not persisted, unlike the drawings the lines come from: an arm
   *  is a price, and a price means nothing on another day's tape. A `sim.prefs`
   *  arm restored onto tomorrow's random session would be a standing order at a
   *  number that is now the middle of nowhere. */
  const armToggle = useCallback(
    (level: ArmableLevel, shape: ArmShape | null) => {
      if (shape === "limit") {
        // Through `limitPlacement` rather than a literal "left" here: which
        // button a shape means is the module's answer, and a second copy of it
        // in the page is how the two shapes drift apart.
        const p = limitPlacement({
          key: level.key,
          label: level.label,
          shape,
          price: level.price,
          throughTicks: DEFAULT_THROUGH_TICKS,
        });
        placeAt(p.price, p.button);
        return;
      }
      setArms((prev) => {
        const rest = prev.filter((a) => a.key !== level.key);
        return shape == null
          ? rest
          : [
              ...rest,
              {
                key: level.key,
                label: level.label,
                shape,
                // Snapped to the grid here rather than at placement, so the
                // number the panel shows you standing is the number the order
                // will rest at. A VWAP is not on the tick grid and reads as
                // 19527.820770232105 otherwise — which is not a price anything
                // can be bought at, and not the one this arm is going to use.
                price: Math.round(level.price / tickSize) * tickSize,
                throughTicks: DEFAULT_THROUGH_TICKS,
              },
            ];
      });
    },
    [placeAt, tickSize],
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

  /** An arm reached its level: spend it, and do what it said.
   *
   *  Declared down here rather than beside `armToggle` because it needs
   *  `closeManual`, and a `useCallback` naming it in its deps above the line it
   *  is defined on is a temporal-dead-zone throw at render — `fireArmRef` is how
   *  `advanceSim` reaches it from further up.
   *
   *  Spent whether or not anything happened. A refusal is a rule this ticket
   *  cannot satisfy right now — the day's stop, a shape the guards won't take, a
   *  dead account — and re-attempting it on the next print, and the one after,
   *  would turn one refused order into a refusal on every tick for the rest of
   *  the session. The same rule covers an `exit` that fires while flat: it took
   *  nothing off because there was nothing on, and it does not stay standing
   *  waiting for a position to protect. `placeOrder` has already put any reason
   *  on screen, and the arm visibly leaving the panel is the rest of the answer.
   *
   *  With the race on, firing also cancels the other standing arms *of the same
   *  purpose* — see `armPurpose`. Entries race entries, exits race exits, so a
   *  level-based bracket survives a level-based entry firing under it. */
  const fireArm = useCallback(
    (a: LevelArm, act: ArmAction) => {
      const purpose = armPurpose(a.shape);
      const keep = (x: LevelArm) =>
        x.key !== a.key && !(armRaceRef.current && armPurpose(x.shape) === purpose);
      // The ref as well as the state: `advanceSim` reads the ref inside the
      // frame, and React has not re-rendered yet, so an arm left standing here
      // could fire twice inside one range — and with the race on, a cancelled
      // one could still fire once.
      armsRef.current = armsRef.current.filter(keep);
      setArms((prev) => prev.filter(keep));
      if (act.kind === "close") closeManual();
      else placeAt(act.price, act.button);
    },
    [closeManual, placeAt],
  );
  fireArmRef.current = fireArm;

  /** End the sitting by hand. Anything still on comes off at the last print
   *  first: an attempt whose net leaves out what you were carrying is not the
   *  sitting you had. Trading on afterwards simply reopens it.
   *
   *  **Ending is all it does.** Until 2026-08-25 a traded sitting went straight
   *  into review mode on the way out, because every booked trade owed a review
   *  and the account would not open another sitting until it got one. Reviewing
   *  is a choice now, and a page that lands you in the thing you may not want is
   *  the same coercion with a friendlier name. The offer is on the recap card
   *  instead — Review now, or Review later, or neither. */
  const endAttempt = useCallback(() => {
    endedRef.current = true;
    if (openRef.current) closeManual();
    void finishAttempt();
  }, [closeManual, finishAttempt]);

  /** Open the sitting just ended in review mode, against its own tape.
   *
   *  Through a reload, exactly as `openReview` does and for the same reason:
   *  review mode has to come back with the recorder *unarmed*, and a fresh
   *  mount is the only thing that guarantees it. The marks are on disk before
   *  the navigate, and `writeResume` is what puts the bookmark beside them —
   *  the attempt does not have to be looked up because the page is already
   *  standing on it. */
  const reviewThisSitting = useCallback(() => {
    const id = attemptIdOf();
    if (!id) return;
    writeResume();
    saveReview(scope, { attemptId: id });
    navigate(0);
  }, [attemptIdOf, mode, navigate, writeResume]);

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

  // The daily stop, acting rather than refusing — and the account's three
  // limits, which end the sitting rather than the position.
  //
  // The daily stop watches **booked** P&L (`guardRules.dayFlatten` says why),
  // so it can only be reached the moment a trade closes, and the only case it
  // acts on is size still on after that close: a scale-out that took the day
  // past the line and left a runner. It closes that runner the way `closeManual`
  // does, by appending to the log, so a rewind un-does it like any other action
  // and the sim stays a pure fold over what happened.
  //
  // Fires once per crossing: `openRef` going null is the reset, so a position
  // re-opened after the stop can be closed again if it also breaches. Nothing
  // stops that entry being placed, because the day lock already refuses it.
  //
  // The account's limits below it still read equity and still land a beat late:
  // `openNow` marks the position against `hud.lastPrice`, which arrives on the
  // throttled ~80ms tick — at speed 30 a couple of seconds of market time. Fine
  // to rehearse against, not a number to quote. Late is the only error there;
  // what is *held* comes from the same publish as what has been booked, so a
  // trade can never be counted as both (see `markOpen`).
  const autoClosedRef = useRef(false);
  useEffect(() => {
    // The account's floor first, and always — it is not under the `guardsOn`
    // switch, and unlike the daily stop it does not lift tomorrow. Hitting it
    // does not just close the position: the sitting is over, because an account
    // that has reached its floor has no next trade to take. Never in a drill:
    // `stakes` is undefined there and this returns null, because a rep the
    // account never sees is not a rep the account gets to end.
    //
    // Checked *before* the position gate below, because the floor does not
    // need one: a stop-out can book its way through the floor and be flat
    // again before this effect ever runs, and a death that waits for the next
    // position is a death you trade in front of. "A sitting is in progress"
    // is read off the simulation itself — a position on or a trade booked —
    // rather than off the recorder's attempt id, which only exists once the
    // *debounced* first save lands and would leave exactly the stop-out case
    // unwatched. So a page merely looking at an account cannot end a sitting
    // that never opened — and never in a review, which replays a sitting the
    // account has already priced.
    if (
      !sittingDeadRef.current &&
      !reviewingRef.current &&
      (openRef.current != null || day.trades > 0)
    ) {
      // Three limits, not one: the drawdown floor, the daily loss limit and the
      // day's goal once it has been made. All of them end the day and none of
      // them is under `guardsOn` — the account's rules are the ones you do not
      // get to argue with, which is what makes rehearsing against them worth
      // anything. Only the floor ends the *account*; `endAttempt` closing the
      // sitting is what the other two cost.
      const breach = accountBreach(stakes, live, openRef.current != null);
      const dead = breach?.reason;
      if (dead) {
        sittingDeadRef.current = true;
        // Before the flatten inside `endAttempt`, so even the death's own
        // writes go out already sealed.
        killAttempt();
        setRefused(dead);
        playCue("canceled");
        endAttempt();
        return;
      }
    }
    if (!openRef.current) {
      autoClosedRef.current = false;
      return;
    }
    if (autoClosedRef.current) return;
    if (!guardsOn) return;
    const why = dayFlatten(guards, day, true);
    if (!why) return;
    autoClosedRef.current = true;
    setRefused(why);
    playCue("canceled");
    closeManual();
  }, [stakes, closeManual, day, endAttempt, live, guards, guardsOn, openNow, killAttempt]);

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
    // Gated here rather than only on the button, because the button is not the
    // only way in: `.` reaches the same callback. Same for `stepBack` and
    // `nudgeSpeed` below.
    if (clockLockedRef.current) return;
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
    if (clockLockedRef.current) return;
    seekTo(eng.prevBarClockMs());
  }, [seekTo]);

  /** Step the speed along the offered ladder — the keyboard's version of the
   *  transport's <select>. */
  const nudgeSpeed = useCallback((d: 1 | -1) => {
    if (clockLockedRef.current) return;
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
  // and forward, [ and ] walk the speed ladder, 1–8 pick the bar size. All but
  // k and the bar sizes stand down on a locked rep — in the callbacks they
  // reach rather than here, so the button and the key die together. Video
  // keys rather than invented ones — a replay is a video of the tape, and k/,/.
  // are what every scrubbing tool binds. Space is deliberately NOT play/pause:
  // it is the order modifier on the chart, and a focused button's trigger
  // everywhere else.
  //
  // Held Ctrl — ten times the ladder, for the dead stretch between setups — is
  // hooks/useTurbo and not here, because it is a modifier being *read* rather
  // than a key being taken, and because these bindings all stand down while a
  // chord is up (`isTypingTarget`), which is exactly when it applies.
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
  // Per **position**, to agree with the count beside it: a scale-out books a
  // row per portion, and a position that took a partial at +2 and the rest at
  // the stop is one trade with one verdict, not a win and a loss. Summed over
  // the portions of each open, which is what the account actually collected.
  const wins = useMemo(() => {
    const byOpen = new Map<number, number>();
    for (const t of trades) byOpen.set(t.entryMs, (byOpen.get(t.entryMs) ?? 0) + t.pnl);
    return [...byOpen.values()].filter((pnl) => pnl > 0).length;
  }, [trades]);
  // The blotter's rows. The contract badge is decided here rather than in the
  // card because only this page knows what the ticket is pointed at — and in a
  // review it is pointed nowhere (`routedMicro` is null), where badging every
  // row would say each one differs from a selection that isn't being offered.
  const blotterRows = useMemo(
    () =>
      trades.map((t) =>
        blotterRow(
          t,
          microSym && routedMicro !== null && t.micro !== routedMicro
            ? t.micro
              ? microSym
              : root
            : null,
        ),
      ),
    [microSym, root, routedMicro, trades],
  );
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
    pin: number | null,
    apply: (t: number) => void,
    onPin: (usd: number | null) => void,
    remembered: React.RefObject<number>,
  ) => {
    const on = ticks > 0;
    // The bound the guardrails refuse outside of, and — while the leg is priced
    // in money — what this leg currently comes to. Both in the caption, because
    // the box can only ever show one of the two units and the other one is the
    // one being checked against the rule.
    const echo = legEcho(ticks, pin, tickUsd, size);
    return (
      // `minWidth: 0` because this is a grid cell holding a text box: without it
      // the column sizes to the box's intrinsic width, and the three columns
      // (size, stop, target) stop being thirds — the Size box collapsed to 22px
      // and the two legs took 211px each, overflowing a 300px panel.
      <div style={{ fontSize: 11, color: palette.muted, minWidth: 0 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input
            id={`sim-${key}-on`}
            type="checkbox"
            checked={on}
            // Switching a leg back on restores the distance it had, in the unit
            // it is being set in — a pinned leg comes back as money, not as the
            // ticks that money happened to resolve to.
            onChange={(e) => {
              if (!e.target.checked) return pin != null ? onPin(0) : apply(0);
              if (pin != null) onPin(Math.round(usdForTicks(remembered.current, tickUsd, size)));
              else apply(remembered.current);
            }}
            style={{ margin: 0 }}
            title={`Trade with ${key === "stop" ? "a stop" : "a target"}`}
          />
          <label htmlFor={on ? `sim-${key}` : `sim-${key}-on`}>{label}</label>
          {/* The bound the guardrails will refuse outside of, on the control
              that sets it — a rule you only meet by being refused is one you
              read as the app being broken. */}
          {on && (legBound(key) || echo) && (
            <span style={{ fontSize: 9, marginLeft: "auto" }}>
              {[legBound(key), echo].filter(Boolean).join(" · ")}
            </span>
          )}
        </span>
        <LegAmount
          id={`sim-${key}`}
          ticks={ticks}
          pin={pin}
          tickUsd={tickUsd}
          size={size}
          onTicks={apply}
          onPin={onPin}
          disabled={!on}
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
      className={`sim-page${panelPinned ? " pinned" : ""}`}
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
                the worst of both — it would look like stakes.

                Its name is also the switch to the other account, and it is only
                offered while this one is clean: no sitting open, the mode being
                fixed for the whole of one. (It also waited on an owed review
                until 2026-08-25, so the review could not be stepped around by
                changing accounts. Nothing to step around now.) */}
            {!drill && (
              <AccountChip view={account} onSwitch={null} />
            )}
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
            {/* How fast the entries are coming, on the bar for the reason the
                account chip is: `GuardMeters` says it in full, but the panel it
                lives in opens to 0x0 until asked for, and a warning you have to
                go looking for is one that arrives after the trade. */}
            {pace && <PaceChip reason={pace} />}
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
                openPos && !drill
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
          options={tfOptions}
          // The tick bar (unique to a tape-driven chart), the default, and the
          // two the research vocabulary is written in. 30s/2m/3m/1h go behind ⋯.
          primary={["500t", "1m", "5m", "15m"]}
          compact
          // Every bar here is built from prints in the browser, so the eight are
          // a starting point rather than the set: type 45s or 1500t behind the ⋯.
          custom
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
            {drillDrawBlocked && (
              <span
                className="neg"
                style={{ fontSize: 11, alignSelf: "end", paddingBottom: 6, maxWidth: 220 }}
              >
                {drillDrawBlocked}
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
          disabled={!daysQ.data?.days.length || !!drillDrawBlocked}
          title={
            drillDrawBlocked ??
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
        {/* Per bar, which is why it carries the bar's name under it: the number
            you see belongs to `governingHist.tf` and not to the page, and a
            control that silently changed meaning when you pressed 1h would be
            the quiet kind of wrong. ↺ puts that bar back on the rule. */}
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title={`Draw this many prior sessions to the left of the replay. Real ticks, so they candle on any bar size and profile like the session does — but nothing develops over them, and they can't be traded.\n\nSet per bar size: this is the ${governingHist.tf.label}'s, and it opens on ${defaultHistoryDays(governingHist.tf)} because that is roughly what a ${governingHist.tf.label} needs to have a readable chart behind it.`}
        >
          Prior days
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <select value={historyDays} onChange={(e) => setHistoryDays(Number(e.target.value))}>
              {HISTORY_DAY_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n === 0 ? "none" : `${n} day${n === 1 ? "" : "s"}`}
                </option>
              ))}
            </select>
            {histOverridden && (
              <button
                type="button"
                onClick={() => setHistoryDays(null)}
                title={`Back to ${defaultHistoryDays(governingHist.tf)} — what the ${governingHist.tf.label} asks for on its own`}
                aria-label={`Reset the ${governingHist.tf.label}'s prior days`}
                style={{
                  background: "none",
                  border: "none",
                  color: palette.muted,
                  cursor: "pointer",
                  padding: 0,
                  fontSize: 13,
                  lineHeight: 1,
                }}
              >
                ↺
              </button>
            )}
          </span>
          <span style={{ fontSize: 11, opacity: 0.7 }}>
            {governingHist.tf.label}
            {histOverridden ? ` · default ${defaultHistoryDays(governingHist.tf)}` : ""}
          </span>
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
        {/* Not on a locked rep. The start time itself stays — it is where a
            *new* sitting begins, and the tape build honours it on every fresh
            session — but this button re-seeks the session already running, so
            on the funded replay it is the scrubber with an extra step: set
            15:00, press it, and you have skipped the day. Pick the day again to
            start it somewhere else. */}
        {!clockLocked && (
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
        )}
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
      {/* `ledger`, not `stakes`: a resettable account has no stakes to speak of
          and still has a death worth saying out loud — which on paper is every
          death it will ever have. */}
      <AccountNotice view={ledger} />

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
              // The near-price levels panel, on the trading pane only: a context
              // pane exists to be glanced at, and this is a box of rows in the
              // axis gutter of a chart that is already half the width.
              levelPanel
              // Arming, on the same pane and for the same reason. Offered only
              // where the ticket is: a pane that cannot place an order has no
              // business showing a control that places one.
              arms={arms}
              onArmToggle={armToggle}
              armRace={armRace}
              onArmRace={() => setArmRace((v) => !v)}
              // The legend's identity line. Blind replay masks the date, not the
              // instrument — you are told what you are trading, never when.
              symbol={sel ? (hidden ? root : sel.symbol) : root}
              tapeContract={sel && !hidden ? sel.symbol : undefined}
              tfLabel={tf.label}
              // The label is the picker. Every bucketing, not the bar's short
              // list — there is no width to run out of in a popup, and the ⋯ on
              // the bar exists only because a 36px row has an end.
              tfOptions={tfOptions}
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
              // a badge that never turns off — and omitted in a review, which
              // sends nowhere at all (`routedMicro`).
              routedTo={routedTo}
              pointValue={routedMicro ? pointValue : undefined}
              ticket={ticket}
              onTicketChange={changeTicket}
              // Only the main chart feeds the sizer. The extra panes draw the
              // same tape on other timeframes, and a ruler that changed its
              // mind depending on which pane closed a bar last is a ruler that
              // sizes a ticket at random.
              onVolRuler={setVolRead}
              mark={hud.lastPrice}
              canPlaceOrders={ready}
              hideDates={hidden}
              secondsAxis={showsSeconds(tf)}
              bigLots={bigLots}
              composite={historyDays > 0 ? composite : "off"}
              nodeProm={nodeProm}
              shelfParams={shelfParams}
              shelfField={shelfField}
              modernVwap={mvParams}
              dynamicSwingVwap={dsvParams}
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
              <TicketKnobs
                ticket={ticket}
                onChange={changeTicket}
                tickUsd={tickUsd}
                sizer={sizerCtx}
                onApplySizing={applySizing}
                preset={presetCtx ?? undefined}
                onApplyPreset={applyPreset}
                activeBracket={activeBracket}
                reverse={{
                  on: reverseEntry,
                  applying: reversing,
                  onToggle: () => setReverseEntry((v) => !v),
                }}
              />
              {openPos && (
                <>
                  <span className="sim-quick-pos">
                    <span style={{ color: openPos.side === "long" ? palette.green : palette.red }}>
                      {openPos.side === "long" ? "LONG" : "SHORT"} ×{openPos.size}
                    </span>
                    <span style={{ color: palette.muted }}>@ {fmtPts(openPos.entryPrice)}</span>
                    <b style={{ color: openNow >= 0 ? palette.green : palette.red }}>
                      {fmtUsd(openNow)}
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
              {/* Both market buttons off one list, because reversed they are
                  each other and two hand-written copies is how one of them ends
                  up green over a sell. `gesture` is the button you press — the
                  key it answers to and the side it has always meant — and
                  `sent` is what leaves the ticket; everything you can see is
                  the second one, so the label is never a claim the order
                  contradicts. Reversed and flat, that swaps the pair: the green
                  BUY is on the left, which is the signal that the knob is on. */}
              {([
                { gesture: "short" as Side, key: "s" },
                { gesture: "long" as Side, key: "w" },
              ]).map(({ gesture, key }) => {
                const sent = reversing ? otherSide(gesture) : gesture;
                const long = sent === "long";
                const verb = long ? "Buy" : "Sell";
                // Which position this click would eat into, if any — the
                // tooltip the dock has always shown, now asked about the side
                // actually going out.
                const off = openPos && openPos.side !== sent ? openPos.side : null;
                return (
                  <button
                    key={key}
                    type="button"
                    className={`sim-quick-btn ${long ? "buy" : "sell"}`}
                    onClick={() => placeMarket(gesture)}
                    disabled={!ready}
                    title={
                      off
                        ? `${verb} ${size} at market (${key}) — takes size off the ${off}`
                        : reversing
                          ? `${verb} at market (${key}) — reversed: ${key} has always meant ` +
                            `${gesture === "long" ? "buy" : "sell"}, and the knob sends the other one ` +
                            `until you have size on`
                          : `${verb} at market (${key})`
                    }
                  >
                    <span>{long ? "BUY" : "SELL"}</span>
                    <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
                  </button>
                );
              })}
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
                    tapeContract={sel && !hidden ? sel.symbol : undefined}
                    tfLabel={paneTfsRef.current[i].label}
                    tfOptions={tfOptions}
                    onTfChange={(id) => changePaneTimeframe(i, id)}
                    onAnchorChange={(t) => setPaneAnchor(i, t)}
                    onBracketChange={moveBracket}
                    onFlatten={closeManual}
                    onOrderMove={moveOrder}
                    onOrderCancel={cancelOrder}
                    onPlaceOrder={placeAt}
                    onPlaceTyped={placeTyped}
                    routedTo={routedTo}
                    pointValue={routedMicro ? pointValue : undefined}
                    ticket={ticket}
                    onTicketChange={changeTicket}
                    mark={hud.lastPrice}
                    canPlaceOrders={ready}
                    hideDates={hidden}
                    secondsAxis={showsSeconds(paneTfsRef.current[i])}
                    bigLots={bigLots}
                    composite={historyDays > 0 ? composite : "off"}
                    nodeProm={nodeProm}
                    shelfParams={shelfParams}
                    shelfField={shelfField}
                    modernVwap={mvParams}
                    dynamicSwingVwap={dsvParams}
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
              {/* Everything between Pause and the clock is a way to not sit
                  through the tape, so a locked rep has none of it — no dead
                  controls either, because a greyed scrubber is still a scrubber
                  you keep reaching for. See `clockLocked`. */}
              {!clockLocked && (
                <>
                  <button
                    type="button"
                    style={btn(palette.card)}
                    onClick={stepBack}
                    disabled={!ready}
                    title="One bar back (,) — a rewind: anything done inside the un-happened bar un-happens"
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
                  <label
                    style={{ fontSize: 12, color: palette.muted }}
                    title={`Replay speed — [ and ] walk the ladder, hold Ctrl for ${TURBO_MULT}×`}
                  >
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
                  {/* The <select> still reads 30 while Ctrl is held, so the tape
                      running at 300 has to say so somewhere. */}
                  <TurboChip on={turbo.on} speed={speed} />
                  <input
                    type="range"
                    // Holding a position, the floor is the fill that opened it:
                    // back past there the trade un-happens. `seekTo` refuses that
                    // drag anyway; this is so the control looks like what it does
                    // rather than flashing a refusal at every grab of the handle.
                    // A drill carried a second floor here — the high-water mark,
                    // since its clock never went back — and lost it with D8.
                    //
                    // A review has neither floor: the whole session is reachable at
                    // any moment, replayed position on the screen or not.
                    min={openPos && !reviewing ? openPos.fillMs : scrubMin}
                    max={scrubMax}
                    step={1000}
                    value={hud.clockMs}
                    onChange={(e) => seekTo(Number(e.target.value))}
                    disabled={!ready}
                    className="sim-scrub"
                  />
                </>
              )}
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
              see is a setting with no visible effect. Not during a review
              either — the column is forced there (see `panelPinned`), so the
              button would be a toggle that changes nothing while claiming to. */}
          {sheetOpen && !reviewing && (
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
            it hidden behind a button. A drill's rep-end review counts: the tags
            are forced now, and a lock whose key is behind a closed sheet reads
            as a broken page. */}
        <div
          ref={panelRef}
          className={
            `sim-panel${sheetOpen || reviewing || autopsy || (drill && repOver) ? " open" : ""}` +
            // A review owns the whole dock and is the one card that can be taller
            // than it — seventeen trades is seventeen cards. The class hands the
            // scrolling to the card list so the panel's own feet (the session
            // note, File review) stay put; see the rule in index.css.
            `${reviewing ? " reviewing" : ""}`
          }
        >
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
              is up there is nothing else on this page worth reading — but *above*
              the ticket rather than instead of it once the account is resettable,
              since from that moment the thing to do about the death is take the
              next sitting. */}
          {autopsy && ledger && (
            <AutopsyCard
              view={ledger}
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
              trades={reviewDetail?.trades ?? []}
              journal={reviewJournalQ.data?.trades ?? []}
              vocab={vocab.data ?? null}
              tagSuggestions={tradeTagsQ.data?.tags ?? []}
              // What the sitting already carries, so filing re-states it rather
              // than erasing it. `reviewing` is false until this fetch lands, so
              // the panel never mounts against a missing note.
              sessionNote={reviewDetail?.note ?? ""}
              onSeek={(ms) => {
                // **The transport's speed, not 1×.** This used to force 1× on
                // every card press, on the grounds that the seconds in front of
                // a decision are the whole content of looking at it again. True
                // of the first card and wrong by the fourth: a review is a dozen
                // seeks, and re-setting the speed after each one is a control
                // fighting you. The speed is a setting; the transport owns it,
                // and it is one keystroke away when a card does want slowing
                // down.
                seekTo(ms);
                // And run it. `seekTo` stops the tape — every other caller is a
                // scrub, where stopping is the point — but a review seek is a
                // request to *watch* something, and landing paused five seconds
                // short of the entry looks exactly like a chart with none of
                // your trades on it. You press seek, the decision plays.
                play();
              }}
              // Both modes ask the same four things now: the model column was
              // the only thing a drill hid, and there is no model column.
              onSaveTrade={saveTradeAnswers}
              saving={saveTradeReview.isPending}
              onFile={submitReview}
              filing={fileReview.isPending}
              error={
                fileReview.error instanceof Error
                  ? fileReview.error.message
                  : saveTradeReview.error instanceof Error
                    ? saveTradeReview.error.message
                    : null
              }
            />
          )}

          {/* Backtest mode's own review, opened when the rep is over. The
              answers are forced — 🎲 stays locked while any trade owes, here and
              at the server — but the panel still sits above the ticket rather
              than replacing it: the rep is finished, and the ticket is what the
              next draw arrives into. */}
          {drill && repOver && (
            <DrillReview
              modelName={boundModel?.name ?? "this model"}
              rules={boundModel?.rules ?? []}
              trades={repJournalQ.data?.trades ?? []}
              vocab={vocab.data ?? null}
              tagSuggestions={tradeTagsQ.data?.tags ?? []}
              saving={saveTradeReview.isPending}
              error={saveTradeReview.error instanceof Error ? saveTradeReview.error.message : null}
              onSave={(row, patch) => {
                // Caught here rather than awaited — this panel's Save is one
                // card at a time and the failure is already on screen through
                // `error`; what the catch prevents is an unhandled rejection.
                //
                // The rule ticks and the model ride along explicitly: they are
                // the drill's, these rows were journaled under its binding, and
                // `PUT /notes` rewrites the whole row — leaving them out would
                // blank them.
                void saveTradeAnswers(row, {
                  ...patch,
                  modelId: row.model_id ?? drillPrefs.modelId,
                }).catch(() => {});
              }}
              onDraw={() => daysQ.data?.days.length && anyDay(daysQ.data.days)}
              drawBlocked={drillDrawBlocked}
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
                    control — and because it is the rate every box under it is
                    read through, whichever unit that box is in. */}
                <span title="Dollars per tick on the routed contract. It is what turns the stop and target below into risk — and, on a leg pinned to dollars, what turns the money back into a distance.">
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
                the auto-flatten cannot disagree about how much room is left.
                Both read `stakes`, so in a drill both go quiet together: the
                floor meter simply does not draw, which is the case GuardMeters
                was already written for. The day's own rules still show — those
                are rules under test, and a drill is where you test them. */}
            {/* Which account this sitting is priced on, in the same control
                Live uses and in the same place: at the top of the ticket, above
                the meters it decides the numbers of. Refused while a sitting is
                open or a review is on screen — an attempt's account is stamped
                when it opens and can never be moved, so the switch has to be a
                thing you do *between* sittings. (It used to be refused on an
                owed review too, so the review could not be walked around by
                changing accounts; there is nothing to walk around now.) */}
            {!drill && acctId && (
              <div style={{ marginBottom: 8 }}>
                <AccountSwitch
                  accountId={acctId}
                  onSwitch={!attemptRec.attempt && !reviewing ? switchAccount : null}
                />
              </div>
            )}
            <GuardMeters
              feed={{
                on: guardsOn,
                levels: guards,
                realized: day.realized,
                trades: day.trades,
                legs: day.legs,
                locked: day.locked,
                slow: day.slow,
                equity: equityNow,
                // The *live* floor, not the view's — on an intraday-trailing
                // account they differ by whatever this sitting has been up, and
                // the meter must read the same number the auto-flatten fires on.
                floor: live?.floor ?? null,
                floorTotal: stakes?.rules.max_loss ?? null,
                trailing: stakes?.rules.trailing ?? null,
                // The account's own daily limit, counted against the tape day —
                // not the session's realised, and not the switchable personal
                // stop. `dayTotal` counts the open position because that is what
                // the limit is enforced on: a position that would take the day
                // through it is closed rather than noticed afterwards.
                dayLimit: stakes?.rules.day_loss ?? null,
                dayLeft:
                  stakes && live
                    ? Math.max(0, stakes.rules.day_loss + Math.min(0, live.dayTotal))
                    : null,
                // The goal arms on *booked* P&L — a runner through the number
                // has not made the day — so this counts `dayRealized`.
                goal: stakes?.rules.day_goal ?? null,
                goalLeft:
                  stakes?.rules.day_goal && live
                    ? Math.max(0, stakes.rules.day_goal - live.dayRealized)
                    : null,
                goalArmed: !!(
                  stakes?.rules.day_goal &&
                  live &&
                  live.dayRealized >= stakes.rules.day_goal
                ),
                size: openPos?.size ?? 0,
                cap: onMicro ? (stakes?.caps.micros ?? 40) : (stakes?.caps.minis ?? 4),
                fastShare: day.fastShare,
                medianGapS: day.medianGapS,
                tradedInTheHole: day.tradedInTheHole,
                pace,
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
              {legField("stop", "Stop", stopTicks, stopUsd, applyStop, pinStop, lastStopRef)}
              {legField(
                "target",
                "Target",
                targetTicks,
                targetUsd,
                applyTarget,
                pinTarget,
                lastTargetRef,
              )}
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
            {/* The whole card the dock's A–D chip offers — shapes and sizer
                both — open here because this is where the fields they fill are
                sitting: the grid above, the ladder above that, the size at the
                top. Under them rather than over them on purpose: you read what
                the ticket currently is, then the shortcut that would replace it.
                It was on the blotter for a day and belongs here, next to the
                fields, not next to the record of what the fields already did. */}
            {presetCtx && (
              <div className="sim-preset-block">
                <TicketCard
                  preset={presetCtx}
                  size={size}
                  tickUsd={tickUsd}
                  onApply={applyPreset}
                  active={activeBracket}
                  sizer={sizerCtx}
                  onApplySizing={applySizing}
                />
              </div>
            )}
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
            {/* Reverse, beside the buttons it rewrites rather than up with the
                distances: it is not a property of the bracket, and the two
                things it needs to be read against — the type row above and the
                pair below — are both right here. The dock draws the same
                setting as a chip; one state, two surfaces, so they cannot
                disagree. */}
            <label
              style={{
                fontSize: 11,
                color: palette.muted,
                marginTop: 10,
                display: "flex",
                alignItems: "center",
                gap: 4,
                opacity: resting ? 0.5 : 1,
              }}
              title={
                resting
                  ? "Market orders only — a resting order's side is decided by which side of the market it sits on, and there is no bid above the offer to flip it into."
                  : "Send the opposite side of the button you press, so a model can be traded backwards without doing the flip in your head. Opening only: with size on, adds and exits mean what they say."
              }
            >
              <input
                type="checkbox"
                checked={reverseEntry}
                onChange={(e) => setReverseEntry(e.target.checked)}
                style={{ margin: 0 }}
              />
              Reverse entries
              {reverseEntry && !resting && (
                <b style={{ marginLeft: "auto", color: reversing ? palette.gold : palette.muted }}>
                  {reversing ? "swapped" : "stands down — position on"}
                </b>
              )}
            </label>
            {/* The same pair as the dock's, and mapped for the same reason —
                except that here only the market half reverses. A resting
                order's side is the price's to decide, so `sent` collapses to
                the gesture the moment the ticket is on limit or stop, and the
                geometry refusals below go on naming the order you asked for. */}
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              {([
                { gesture: "long" as Side, can: canLong },
                { gesture: "short" as Side, can: canShort },
              ]).map(({ gesture, can }) => {
                const sent = reversing && !resting ? otherSide(gesture) : gesture;
                const long = sent === "long";
                return (
                  <button
                    key={gesture}
                    type="button"
                    style={{ ...btn(long ? palette.green : palette.red), flex: 1, opacity: can ? 1 : 0.4 }}
                    onClick={() => submit(gesture)}
                    disabled={!ready || !can}
                    title={
                      resting && !can
                        ? long
                          ? wantsAbove
                            ? "A buy stop has to sit above the market"
                            : "A bid has to rest below the market"
                          : wantsAbove
                            ? "A sell stop has to sit below the market"
                            : "An offer has to rest above the market"
                        : reversing && !resting
                          ? `Sends a ${long ? "buy" : "sell"} — the reverse knob is on, and it stops ` +
                            `applying once you have size on`
                          : undefined
                    }
                  >
                    {resting
                      ? long
                        ? wantsAbove
                          ? "Buy stop"
                          : "Buy limit"
                        : wantsAbove
                          ? "Sell stop"
                          : "Sell limit"
                      : long
                        ? "Buy"
                        : "Sell"}
                  </button>
                );
              })}
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
              <b>k</b> play/pause ·{" "}
              {/* The transport keys are only advertised where they do anything:
                  a hint listing a key that no-ops is worse than no hint. */}
              {!clockLocked && (
                <>
                  <b>,</b>/<b>.</b> bar back/forward · <b>[</b>/<b>]</b> speed ·{" "}
                </>
              )}
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
                <div style={{ fontSize: 20, fontFamily: "monospace", color: openNow >= 0 ? palette.green : palette.red }}>
                  {fmtUsd(openNow)}
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
                    <AccountRecap view={ledger} />
                    {/* The review, offered. This is the whole of what replaced
                        the mandatory one on 2026-08-25: ending a traded sitting
                        used to drop you into review mode with the account
                        refusing the next sitting until you filed, and what that
                        produced was a form you filled in to get back to
                        trading.

                        Two doors and no wrong answer. **Review now** goes to the
                        tape while the sitting is still in your head, which is
                        where a review is worth anything. **Review later** marks
                        it and lets you carry on — the mark is the only thing an
                        unreviewed sitting carries now, and the history page is
                        where it is read back. Pressing neither is also an
                        answer, and the commonest one.

                        Not for drills: the rep-end panel is the drill's own
                        review and it is already on screen. */}
                    {!drill && s.trades > 0 && (
                      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                        <button
                          type="button"
                          data-review-now
                          onClick={reviewThisSitting}
                          style={{ flex: 1, padding: "5px 0", fontSize: 12, cursor: "pointer" }}
                          title="Reopen this sitting on its own tape, read-only, with the trades to answer for"
                        >
                          Review now
                        </button>
                        <button
                          type="button"
                          data-review-later
                          onClick={() =>
                            void setAttemptReviewLater(!attemptRec.attempt?.review_later)
                          }
                          style={{
                            flex: 1,
                            padding: "5px 0",
                            fontSize: 12,
                            cursor: "pointer",
                            color: attemptRec.attempt?.review_later
                              ? palette.orange
                              : undefined,
                          }}
                          title={
                            attemptRec.attempt?.review_later
                              ? "Marked. It is on the history page under Flagged — press again to unmark it."
                              : "Mark it to come back to. Nothing waits on it; it just stops the sitting getting lost."
                          }
                        >
                          {attemptRec.attempt?.review_later ? "🚩 Flagged" : "Review later"}
                        </button>
                      </div>
                    )}
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

          <Blotter
            rows={blotterRows}
            total={realized}
            head={
              <>
                <span className="r">
                  <TradeTally rows={blotterRows} /> · {wins}W
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
              </>
            }
          />
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
