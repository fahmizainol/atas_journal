// Charts → Live. The same chart surface as Replay, over a tape still arriving.
//
// Almost nothing here is new. The engine, the chart, the view mappers and the
// trade simulation are the ones Replay uses; what differs is the three things
// lib/tapeSource named — where the clock comes from, whether the tape ends, and
// what you may do to it — plus how the ticks get in: a poll that appends into a
// GrowableTape, rather than one decode of a finished session.
//
// WHAT THIS PAGE MAY NOT DO. `liveSource` reports canSeek / canRewind /
// canSetSpeed / canStepBar false, and every one of those is load-bearing:
//
//   - No seek and no rewind, so `truncateLog` is never imported here at all. A
//     rewind un-does actions taken after a point in time, which is coherent only
//     when the tape can be re-taken. A live fill really happened, and un-happening
//     it would be a lie about what the session did. The blotter is append-only,
//     and that is the whole difference between practising and keeping a record.
//   - No speed: the clock is the last print, not a playback rate.
//   - No step-bar: `nextBarClockMs()` reads forward on a tick timeframe, and the
//     print that would complete the forming bar has not happened yet.
//
// SHADOW SIGNALS ARE NOT ORDERS. The signals rail says where each registered
// strategy *would have* signalled — prefix re-runs of the same `run_session` the
// backtest calls, so live cannot disagree with the backtest. Nothing in it can
// route anything, and the shelf has no path to the broker at all: `shadow.py`
// imports nothing from `broker.py`, so strategy-routed entry is absent by
// construction rather than by omission (docs/live-shadow-plan.md § Phase 7).
//
// EVERY GESTURE ROUTES, AND ALL OF THEM THROUGH ONE FUNNEL. `placeMarket`,
// `placeAt`, `placeResting`, the long-press ticket, the BUY/SELL dock and the
// q/w/s keys each end at `useOrderIntent.submit`, which is the single place that
// decides paper-or-broker — see the note at the top of that file for why the
// alternative (each gesture checking for itself) fails silently rather than
// loudly. On paper `submit` declines and the caller folds the order into
// `logRef` over the tape; on a real account it goes to the exchange, behind the
// confirm unless one-click is on for that account.
//
// AND THEY ALL MEASURE THE SAME ORDER. Size and the bracket are one piece of
// state on this page (`ticket`), handed to the chart *and* to the routing
// panel's pad. There were two once — the panel kept its own — and setting the
// stop in one while placing from the other sent a bracket nobody had typed.
//
// THE FEED IS SIMULATED, AND SAYS SO. Until Phase 5 there is no live tick source
// in this project, so the source is a cached Databento day replayed at wall-clock
// speed. The banner names the day and the multiplier for as long as it runs: a
// surface that looked live while showing last February would be the single most
// expensive thing this page could get wrong.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ReplayChart, type ReplayChartHandle } from "../components/charts/ReplayChart";
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
import { TimeframeControl } from "../components/charts/TimeframeControl";
import { StudyPicker } from "../components/charts/StudyPicker";
import type { StudySpec } from "../lib/studies";
import type { LayerState } from "../components/charts/chartLayers";
import { ChartTopBar } from "../components/charts/ChartTopBar";
import { SimIndicators } from "../components/charts/SimIndicators";
import { Blotter, TradeTally } from "../components/charts/Blotter";
import { QuickDock } from "../components/charts/QuickDock";
import { LayoutPicker } from "../components/charts/LayoutPicker";
import { ChartToolRail } from "../components/charts/ChartToolRail";
import { TicketCard, type PresetCtx } from "../components/charts/TicketCard";
import { TicketKnobs } from "../components/charts/TicketKnobs";
import { LegAmount, legEcho } from "../components/charts/LegAmount";
import type { SizerCtx } from "../components/charts/SizerGrid";
import { microOf, rootOf } from "../lib/contracts";
import { DEFAULT_GUARDS, paceRefusal } from "../lib/guardRules";
import { PaceChip } from "../components/charts/PaceChip";
import type { PresetBracket } from "../lib/orderPresets";
import { DEFAULT_MAX_LOSS } from "../lib/riskSizer";
import type { VolRulerRead } from "../lib/volRuler";
import {
  LAYOUTS,
  MAX_PANES,
  clampPaneIndex,
  clampRatio,
  gridArea,
  gridTemplate,
} from "../lib/paneLayout";
import { setLinkOn as setLinkModuleOn } from "../lib/paneLink";
import { EMPTY_TOOL_STATE, type ChartToolId, type ChartToolState } from "../lib/chartTools";
import { TapeCoverage } from "../components/TapeCoverage";
import { RoutingPanel } from "../components/RoutingPanel";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import { useSimulatorDays } from "../hooks/useSimulator";
import {
  cancelBrokerOrder,
  flattenAll,
  journalPaperTrades,
  modifyBrokerOrder,
  useRoutingStatus,
} from "../hooks/useRouting";
import {
  BasketIds,
  blotterRows as brokerBlotterRows,
  bracketOf,
  brokerSig,
  positionLine,
  tradeViews,
  workingViews,
} from "../lib/brokerViews";
import { useOrderIntent } from "../hooks/useOrderIntent";
import { isTypingTarget, usePaneKeys } from "../hooks/usePaneKeys";
import { OrderConfirm, OrderFlash } from "../components/OrderConfirm";
import { brokerMark, FillCues, playCue, simMark } from "../lib/orderSound";
import { loadLiveHistoryDays, loadStudies, saveLiveHistoryDays, saveStudies } from "../lib/chartPrefs";
import type {
  BrokerOrder,
  BrokerPosition,
  BrokerState,
  GuardState,
  OrderDraft,
} from "../lib/routingTypes";
import {
  setLiveModes,
  startFakeFeed,
  startRithmicFeed,
  stopFeed,
  useLiveHeader,
  useLiveHistoryDays,
  useLiveRecordings,
  useLiveSignals,
  useLiveStatus,
  useLiveTape,
} from "../hooks/useLive";
import { useTapeHistory, type HistDay } from "../hooks/useTapeHistory";
import type { TapeRange } from "../lib/volumeProfile";
import type { CompositeRule } from "../lib/compositeProfile";
import { legTicks, pinnedLeg, resolveBracketUsd, type UsdBracket } from "../lib/bracketUsd";
import type { LiveTicket, TrailSource } from "../lib/simPrefs";
import {
  loadLiveChartKnobs,
  loadLiveContract,
  loadLiveTicket,
  saveLiveChartKnobs,
  saveLiveContract,
  saveLiveTicket,
} from "../lib/simPrefs";
import type { GrowableTape } from "../lib/growableTape";
import {
  sessionPayloadFor,
  type LiveBackfill,
  type LiveHeader,
  type LiveSignals,
  type ShadowStrategy,
} from "../lib/liveTypes";
import { ReplayEngine, type EventTuning, type IbBox, type RangeBox, type Tape } from "../lib/replayEngine";
import { loadFillModel, type FillCfg } from "../lib/fillModel";
import { liveSource } from "../lib/tapeSource";
import { showsSeconds, timeframeById, useTimeframeOptions } from "../lib/timeframes";
import {
  HISTORY_DAY_OPTIONS,
  defaultHistoryDays,
  governingHistory,
  withHistoryOverride,
  type HistoryDayOverrides,
} from "../lib/contextDays";
import {
  newLog,
  newSim,
  SimLadder,
  stepSim,
  workingOrders,
  type Log,
  type OrderRec,
  type OrderType,
  type Position,
  type Side,
  type SimState,
  type Trade,
  type TrailCfg,
} from "../lib/replaySim";
import {
  blotterRow,
  fmtClock,
  fmtCountdown,
  fmtPts,
  fmtUsd,
  orderView,
  posLine,
  simSig,
  tradeMark,
} from "../lib/simViews";
import { palette } from "../theme";
import { gexAt, useGexRegime, type GexRegime } from "../hooks/useGex";

const TZ = "New York";
const SPEEDS = [1, 5, 15, 60, 300, 900];

// The prior-session counts offered behind the live one, the rule that picks one
// per bar size, and where your overrides of it are kept: `lib/contextDays` and
// `lib/chartPrefs` respectively. Both shared with the Simulator, which draws the
// same context off the same store.
//
// It costs something, which is why it is a control and not a constant: each day
// is a whole tape to fetch, ~0.5M prints and a few megabytes, and they are
// fetched before the session's own tape starts (see `useLiveTape`). Cold, that
// is seconds per day; warm, nothing at all.

/** How often an extra pane may repaint, in ms. The Simulator's number and the
 *  same argument: the engine step is ~0.006ms so every pane advances every
 *  frame, but a *repaint* costs about 65% of a core, and four panes repainting
 *  at 60fps would spend the frame budget on charts nobody is trading off. Bar
 *  close or this, whichever comes first — so the forming bar visibly moves,
 *  which is the whole reason to have the pane up on a live tape. */
const PANE_DRAW_MS = 200;

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

/**
 * Everything the engine reads off the header that a session in progress can
 * still learn, as one comparable string.
 *
 * A live header is not constant. `has_overnight` is "are there overnight rows on
 * the tape *yet*", and the Globex anchor and the weekly seed are both null until
 * it is true — so a connect whose backfill lands after the first live print
 * hands the engine a session with no night in it, and the Globex VWAP, its band
 * and the weekly line are then absent for the rest of the sitting. That was the
 * bug you fixed by reloading the page: the reload built the engine from a header
 * that had since been answered.
 */
const geoSig = (h: LiveHeader): string =>
  [
    h.has_overnight ? 1 : 0,
    h.globex_anchor_ms ?? "-",
    h.rth_open_ms,
    h.rth_close_ms,
    h.weekly_seed ? h.weekly_seed.join(",") : "-",
  ].join("|");

/** The context a running blotter is pinned to: how many prior days are drawn,
 *  and what each recorded day was at when those orders were indexed against it.
 *  Both can move on their own — the day count when you change the bar, a day's
 *  own bytes when the harvest sweep fills in the hours you were disconnected
 *  for — and either one re-seeds the tape from row zero, which renumbers the
 *  tick indices a paper order is. So they are held, and released, together. */
interface ContextPin {
  days: number;
  versions: Record<string, string | null>;
}

/** Stable empties for a pane the layout doesn't currently have — a fresh `[]`
 *  per render would make the picker's memos churn and the chart rebuild its
 *  studies every frame. */
const EMPTY_SPECS: StudySpec[] = [];
const EMPTY_LAYERS: LayerState[] = [];

export function LiveChart() {
  const statusQ = useLiveStatus();
  const status = statusQ.data;
  const gen = status?.running ? (status.gen ?? null) : null;
  const headerQ = useLiveHeader(gen, TZ);
  const header = headerQ.data ?? null;
  const signalsQ = useLiveSignals(gen);

  // --- the days behind this one ---------------------------------------------
  // Whole prior sessions, drawn to the left of the live one. Real ticks, so they
  // candle on any timeframe and profile off the tape exactly as the session
  // does — but nothing develops over them and nothing on them can be traded.
  //
  // Which days those are is a server question: it needs both stores in view (the
  // Databento cache and the recorded one, resolved cache-first per day) and it
  // needs to tell a hole from a holiday, which the client cannot do without
  // opening a file. `missing` comes back with the answer because the live store
  // has long stretches with nothing recorded, and gluing across one would draw a
  // continuous chart out of a discontinuous week.
  // Remembered, because it is the page's biggest opening cost and not a taste —
  // see `loadLiveHistoryDays`. A reload that went back to a fixed default was
  // that many tapes to fetch and decode before the live one could start,
  // however few you asked for last time.
  //
  // How many is a question about the *bar*: an hourly with one day behind it has
  // twenty-three candles on it. So the count follows the bucketing
  // (lib/contextDays), the stored map is only what you have overridden that rule
  // to, and the bar has to be decided before the fetch — which is why the
  // timeframe and the pane grid are declared here, well above the chrome that
  // draws them.
  const [knobs] = useState(loadLiveChartKnobs);
  const [tfId, setTfId] = useState(knobs.timeframe);
  const tf = useMemo(() => timeframeById(tfId), [tfId]);
  const [layout, setLayout] = useState(knobs.layout);
  const [paneTfIds, setPaneTfIds] = useState(knobs.paneTfs);
  const paneCount = LAYOUTS[layout].panes;
  // The bars on screen, main first — pane 0's bucketing is the page's own `tfId`,
  // so `paneTfIds[0]` is never read, and panes past the layout's count are not
  // drawn at all.
  const drawnTfs = useMemo(
    () => [tfId, ...paneTfIds.slice(1, paneCount)].map(timeframeById),
    [tfId, paneTfIds, paneCount],
  );
  const [histOverrides, setHistOverridesState] = useState(loadLiveHistoryDays);
  const setHistOverrides = useCallback((next: HistoryDayOverrides) => {
    setHistOverridesState(next);
    saveLiveHistoryDays(next);
  }, []);
  const governingHist = useMemo(
    () => governingHistory(drawnTfs, histOverrides),
    [drawnTfs, histOverrides],
  );

  // What the bar *asks* for, and what it actually gets.
  //
  // The two differ only while the blotter has something in it, and they have to:
  // changing the context re-seeds the tape from row zero, and every order on the
  // blotter is a tick index into the tape that would stop existing. The bar is
  // still free to change — it is the one setting you change while watching — so
  // a switch to 1h with a position open moves the candles and leaves the context
  // where it is, and says so rather than silently dropping the difference.
  //
  // Frozen at the value in force when the blotter filled, not recomputed while
  // locked: the point is that the tape underneath does not move.
  // What each recorded day is *at* right now, by date. A finished day is not
  // immutable: the harvest sweep fills in the hours you were disconnected for on
  // your next connect, which lands a minute or so after this page loads. That is
  // half a session's volume on a bad day, and the day's whole share of the
  // weekly anchor with it — so the context has to re-read itself when the
  // manifest moves rather than draw the truncated tape it fetched first.
  // `/live/recordings` is already polled once a minute; this is its other use.
  const recordingsQ = useLiveRecordings(header?.symbol ?? undefined);
  // Whether the contract on screen is still the one the market trades.
  //
  // A feed does not notice a roll: the subscription was made at connect and the
  // stored symbol outlives the quarter it was typed in. Meanwhile the *order*
  // path resolves the front month every time it attaches, so the two drift
  // apart silently — on 2026-09-14 the tape was NQU6 and the orders were going
  // to MNQZ6, 290 points away, with the chart reading normally the whole time.
  // `is_front` is `false` only when the server is sure; `null` (no opinion)
  // draws nothing.
  const staleContract = useMemo(() => {
    const want = header?.symbol;
    const row = (recordingsQ.data?.contracts ?? []).find((c) => c.symbol === want);
    return row && row.is_front === false && row.front_month ? row : null;
  }, [recordingsQ.data, header?.symbol]);
  const dayVersions = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const r of recordingsQ.data?.recordings ?? []) out[r.date] = r.updated_at;
    return out;
  }, [recordingsQ.data]);

  const wantedDays = governingHist.days;
  const wantedRef = useRef({ days: wantedDays, versions: dayVersions });
  wantedRef.current = { days: wantedDays, versions: dayVersions };
  // The context the blotter's orders were indexed against — both halves of it,
  // because a re-harvested day re-seeds the tape exactly as a changed day count
  // does, and an order's `idx` would stop meaning what it meant.
  const [pinned, setPinned] = useState<ContextPin | null>(null);
  const historyDays = pinned?.days ?? wantedDays;
  const histVersions = pinned?.versions ?? dayVersions;
  /** The bar is asking for a context the open blotter won't let it have. */
  const histFrozen = pinned != null && pinned.days !== wantedDays;

  const histDaysQ = useLiveHistoryDays(header?.symbol ?? null, header?.date ?? null, historyDays);
  const histDates = useMemo(
    () => (histDaysQ.data?.days ?? []).map((d) => d.date),
    [histDaysQ.data],
  );
  const histQ = useTapeHistory(
    "/live/history/session", header?.symbol ?? null, histDates, TZ, histVersions);

  // The chart's reading knobs — the same set the Simulator hangs off its legend
  // rows, persisted in their own store (see lib/simPrefs, loadLiveChartKnobs).
  // All of them are reading choices: none can move the clock, fill an order, or
  // reach a broker. Declared up here because the composite span cuts the
  // context ranges below. (`knobs` itself is read at the top of the component,
  // with the timeframe the context count depends on.)
  const [bigLots, setBigLotsState] = useState(knobs.bigLots);
  const bigLotsRef = useRef(bigLots);
  bigLotsRef.current = bigLots;
  const [nodeProm, setNodeProm] = useState(knobs.nodeProm);
  // Modern VWAP's parameters — see the Simulator's copy for why they are patched
  // as one object. Kept in this page's own store, so the two pages can be
  // looking at different settings of a study layer.
  const [mvParams, setMvParams] = useState(knobs.modernVwap);
  const patchMv = useCallback(
    (patch: Partial<ModernVwapParams>) => setMvParams((p) => ({ ...p, ...patch })),
    [],
  );
  // The Zeiierman line beside it, held and persisted the same way — a separate
  // indicator, so separate state; see lib/dynamicSwingVwap.
  const [dsvParams, setDsvParams] = useState(knobs.dynamicSwingVwap);
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
  const [compositeRule, setCompositeRule] = useState(knobs.composite);
  const [compositeSpan, setCompositeSpan] = useState(knobs.compositeSpan);
  const [evTuning, setEvTuning] = useState(knobs.eventTuning);
  const evTuningRef = useRef(evTuning);
  evTuningRef.current = evTuning;
  const [evLabelSt, setEvLabelSt] = useState(knobs.eventLabelSt);
  const [evFillSweep, setEvFillSweep] = useState(knobs.eventFillSweep);
  const [evFillAbsorb, setEvFillAbsorb] = useState(knobs.eventFillAbsorb);
  const [evFloorSweep, setEvFloorSweep] = useState(knobs.eventFloorSweep);
  const [evFloorAbsorb, setEvFloorAbsorb] = useState(knobs.eventFloorAbsorb);
  const [evMarginal, setEvMarginal] = useState(knobs.eventMarginal);
  // The store is written wholesale further down, once the page posture it also
  // carries (timeframe, indicator strip, rail pin) has been declared.

  // The days that may actually be seeded: in wall-clock order and not
  // overlapping. A day that fails the test is dropped rather than drawn — bars
  // out of order are a chart the library refuses, and one missing Tuesday is a
  // cheaper failure than that.
  //
  // No comparison against the session's own start is needed, unlike the replay's
  // version of this: every day here is a strictly earlier *date* than the one
  // running, and a session's tape begins at the 18:00 open of its own eve. The
  // ordering guard is the whole check.
  // Kept as days rather than bare tapes: the weekly anchor reaches back over
  // this stretch and needs each day's own seed to do it (see
  // `ReplayEngine.historyWeeklyBand`), and the seed only travels with the day.
  const contextDays = useMemo(() => {
    const out: HistDay[] = [];
    let prevEnd = -Infinity;
    for (const d of histQ.days) {
      const t = d.tape;
      if (t.n === 0 || t.t[0] <= prevEnd) continue;
      out.push(d);
      prevEnd = t.t[t.n - 1];
    }
    return out;
  }, [histQ.days]);
  const contextTapes = useMemo(() => contextDays.map((d) => d.tape), [contextDays]);
  const engineContext = useMemo(
    () => contextDays.map((d) => ({ ticks: d.tape.n, weeklySeed: d.weeklySeed })),
    [contextDays],
  );
  const engineContextRef = useRef(engineContext);
  engineContextRef.current = engineContext;

  // Where each context day sits on the seeded tape — the spans the composite is
  // built over, and the only thing it is built over. The live session is
  // deliberately not among them: a composite fed by the day in progress is a
  // level that day could never violate.
  //
  // How much of a day counts is the span knob, exactly as on Replay: under
  // "globex" it starts at the day's first tick (the 18:00 open the evening
  // before), under "rth" at the bell. Ends at the 16:00 close either way. The
  // post-close hour belongs to the *next* day's overnight, and counting it here
  // would put the same ticks in two days.
  const contextRanges = useMemo(() => {
    const out: TapeRange[] = [];
    let off = 0;
    for (const d of histQ.days) {
      const t = d.tape;
      if (!contextTapes.includes(t)) continue;
      const i0 = compositeSpan === "globex" ? 0 : firstAt(t.t, t.n, d.rthOpenMs);
      const i1 = firstAt(t.t, t.n, d.rthCloseMs) - 1;
      if (i1 >= i0) out.push({ i0: off + i0, i1: off + i1 });
      off += t.n;
    }
    return out;
  }, [compositeSpan, contextTapes, histQ.days]);
  const ctxRangeRef = useRef<TapeRange[]>([]);
  ctxRangeRef.current = contextRanges;

  // The multi-session composite, reachable at last: it is built over context
  // days, so on this page it has been *unavailable* rather than declined. Frozen
  // at the prior close by construction (lib/compositeProfile) — the session in
  // progress is never in it, which is the rule that stops it becoming a level
  // today could not violate. "off" with no days behind it, since there would be
  // nothing to build it from.
  const composite: CompositeRule = contextTapes.length > 0 ? compositeRule : "off";

  // The tape cannot start until the context is decided: rows seeded in front of
  // it shift every index behind them, and an order's `idx` is a position in that
  // array. So this is a precondition, not a later splice — see
  // `createGrowableTape`. Asking for none settles immediately.
  const historyReady =
    historyDays === 0 || (!histDaysQ.isPending && (histDates.length === 0 || histQ.settled));
  // What the tape is keyed on. Dates, not tape identities: the array is re-made
  // every render and would otherwise tear down the poll loop with it.
  const contextKey = useMemo(
    () => (historyReady ? contextTapes.map((t) => `${t.t[0]}:${t.n}`).join(",") : "pending"),
    [contextTapes, historyReady],
  );


  // --- where an order goes --------------------------------------------------
  // The account selector lives in the routing panel, but *which account is
  // active* is a page-level fact: it decides whether a chart gesture fills the
  // paper blotter or reaches the exchange. So the status is read here and the
  // panel shares the query (react-query dedupes on the key), rather than the
  // page having to ask a panel that may not be mounted.
  // Asked before there is a session too, and for a different reason: the
  // autostart below has to know whether this deployment permits routing *before*
  // it connects, because the order plant is chosen at connect and can never be
  // added to a session afterwards. Same query key as the startup screen's own
  // copy, so react-query asks once either way.
  const routingQ = useRoutingStatus(!!status?.routing || status?.running === false);
  const brokerState = routingQ.data?.broker ?? null;
  /** Can the ladder run at all? `use_instrument` lets orders be routed to MNQ
   *  while the tape stays on NQ, and the ladder measures the high off the tape
   *  this app can see — so on a split it would be trailing the wrong contract.
   *  The server refuses that pair; this greys the option rather than letting the
   *  refusal arrive on the send. */
  const ladderBlocked =
    !!brokerState && !!brokerState.feed_symbol
    && brokerState.symbol !== brokerState.feed_symbol;
  const intent = useOrderIntent(brokerState, () => void routingQ.refetch());
  // Once per session — the curve is fixed for the day (OI is frozen pre-open),
  // so the per-tick half is an interpolation, not a request.
  const gexQ = useGexRegime(status?.symbol ?? null, status?.date ?? null);

  // --- opening the page opens the session -----------------------------------
  // There is no landing screen any more. Arriving at /charts/live with nothing
  // running connects the Rithmic ticker plant *with the order plant open*, on the
  // contract last connected to, recording, shelf off. The screen below still
  // exists and is still the only way to the simulated feed or another contract —
  // it is the fallback now rather than the front door.
  //
  // ONCE PER MOUNT, NEVER AS A RETRY. `firedRef` latches before the POST rather
  // than after it, so a connect that throws lands on the startup screen carrying
  // its error instead of hammering the plant, and StrictMode's double-invoke
  // sends one order-plant login rather than two. It also means a session stopped
  // from the banner stays stopped: a page that reconnected what you just stopped
  // would be one you cannot get out of.
  //
  // AND IT WAITS FOR `/live/routing`. Connecting before that answers would give
  // a session that either cannot trade or cannot say why. When routing is not
  // permitted at all — or the endpoint is unreachable — this declines to connect
  // and hands over to the startup screen rather than quietly opening a data-only
  // session that looks like it worked and has no path to order entry.
  const [autoPhase, setAutoPhase] = useState<"waiting" | "connecting" | "manual">("waiting");
  const [autoErr, setAutoErr] = useState<string | null>(null);
  const [autoSymbol] = useState(loadLiveContract);
  const firedRef = useRef(false);
  const statusOk = statusQ.isSuccess;
  const refetchStatus = statusQ.refetch;
  const rs = routingQ.data;
  const rsFailed = routingQ.isError;
  useEffect(() => {
    if (firedRef.current || !statusOk) return;
    // Attached to a session that was already going. Latched all the same, and
    // that is the point: without it, stopping the feed from the banner would
    // drop `running` and this effect would immediately reconnect the thing you
    // just stopped.
    if (status?.running) {
      firedRef.current = true;
      setAutoPhase("manual");
      return;
    }
    if (!rs && !rsFailed) return;                       // routing not answered yet
    firedRef.current = true;
    if (rsFailed || !rs?.enabled || rs.refusal) {
      setAutoPhase("manual");
      return;
    }
    setAutoPhase("connecting");
    void (async () => {
      try {
        // The shelf stays off (it is a study surface, and this page is opened to
        // trade), the recording stays on — see `RithmicStart` for why that one is
        // not a taste. Both can be thrown while the session runs; routing cannot,
        // which is the whole reason this is here.
        await startRithmicFeed({
          symbol: autoSymbol,
          record: true,
          signals: false,
          routing: true,
        });
        saveLiveContract(autoSymbol);
        await refetchStatus();
      } catch (e) {
        setAutoErr(e instanceof Error ? e.message : String(e));
      } finally {
        setAutoPhase("manual");
      }
    })();
  }, [autoSymbol, refetchStatus, rs, rsFailed, status?.running, statusOk]);

  // The built-in bar sizes plus any you have typed into the picker — the same
  // list the Simulator's bar reads, and the same store behind it. (`tfId` and
  // the pane grid it belongs to are declared at the top of the component: how
  // many days of context to fetch depends on what is drawn, and that decision
  // has to be made before the tape can start.)
  const tfOptions = useTimeframeOptions();
  const tfRef = useRef(tf);
  tfRef.current = tf;

  // --- imperative refs (the frame loop reads these, never React state) ------
  const chartRef = useRef<ReplayChartHandle>(null);
  const engineRef = useRef<ReplayEngine | null>(null);
  // The extra panes: one chart and one engine each, over the *same* growing
  // tape at their own bucketing. Arrays indexed by pane, index 0 unused — the
  // page's own chart is pane 0 and lives in the two refs above. Same shape as
  // the Simulator's, deliberately: the two pages draw the same grid.
  const extraCharts = useRef<(ReplayChartHandle | null)[]>([]);
  const extraEngines = useRef<(ReplayEngine | null)[]>([]);
  /** rAF timestamp of each extra pane's last repaint — see PANE_DRAW_MS. */
  const extraDrawn = useRef<number[]>([]);
  /** Any pane's chart handle by index. */
  const paneChart = useCallback(
    (i: number): ReplayChartHandle | null => (i === 0 ? chartRef.current : extraCharts.current[i]),
    [],
  );
  /** Do something to every extra pane that currently exists. The guard is the
   *  point: a pane's handle outlives by one render the layout change that
   *  removed it. */
  const eachExtra = useCallback((fn: (chart: ReplayChartHandle, i: number) => void) => {
    for (let i = 1; i < paneCountRef.current; i++) {
      const c = extraCharts.current[i];
      if (c) fn(c, i);
    }
  }, []);
  /** The grid, for the divider drag — the ratio is a fraction of this box. */
  const splitRef = useRef<HTMLDivElement>(null);
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
      const pct = clampRatio((at / span) * 100, 50);
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

  // What each pane's hand-tools are doing, for the one rail that drives them.
  const [toolStates, setToolStates] = useState<ChartToolState[]>(() =>
    Array.from({ length: MAX_PANES }, () => EMPTY_TOOL_STATE),
  );
  const reportTools = useCallback((i: number, s: ChartToolState) => {
    setToolStates((prev) => {
      const cur = prev[i];
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
  const tapeRef = useRef<GrowableTape | null>(null);
  const clockRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const idRef = useRef(1);
  const sessionStartRef = useRef(0);
  // The header the running engine was built from, as `geoSig` renders it. The
  // header is not constant — see below — so this is what says whether the engine
  // is still built on the truth.
  const geoSigRef = useRef("");
  // basket_id <-> the numeric id the chart primitives key orders by, stable for
  // the life of the session (see lib/brokerViews).
  const basketsRef = useRef(new BasketIds());
  const brokerSigRef = useRef("");
  // The broker's last word, for the drag handlers. Refs rather than state
  // because those handlers are passed to the chart and would otherwise be
  // re-made on every poll, tearing down the chart's own bindings twice a second.
  const brokerOrdersRef = useRef<BrokerOrder[] | null>(null);
  const brokerPosRef = useRef<BrokerPosition | null>(null);
  // Made once, and never re-made: it closes over the tape *ref*, not over a tape,
  // so a session swap needs no new source. The clock is the newest print — never
  // the wall clock, which on a quiet market would run the chart past its data.
  const sourceRef = useRef(
    liveSource(() => {
      const t = tapeRef.current;
      return t && t.n > 0 ? t.t[t.n - 1] : null;
    }),
  );

  // The action log and the simulation derived from it. Append-only: there is no
  // truncate path on this page at all — by construction, not by discipline.
  const logRef = useRef<Log>(newLog());
  const simRef = useRef<SimState>(newSim());
  const sigRef = useRef("");
  const openRef = useRef<Position | null>(null);
  // Snapshots of the rebuild fold, so a click in the afternoon doesn't re-walk
  // the morning. A cache over `runSim`, checked rather than trusted — see
  // `SimLadder`. Built once and kept: a new one per render would throw the
  // session's snapshots away every time the HUD ticked.
  const ladderRef = useRef<SimLadder | null>(null);
  if (ladderRef.current === null) ladderRef.current = new SimLadder();
  const ladder = ladderRef.current;

  // Two fill watchers because there are two blotters, and only one of them is on
  // screen at a time (see `drawBrokerRef`). Keeping both fed — the hidden one
  // silently — is what stops switching accounts from announcing everything the
  // other side did while you were not looking at it. See lib/orderSound.
  const paperCuesRef = useRef(new FillCues());
  const brokerCuesRef = useRef(new FillCues());

  // --- display state --------------------------------------------------------
  const [ready, setReady] = useState(false);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [openPos, setOpenPos] = useState<Position | null>(null);
  const [working, setWorking] = useState<WorkingOrderView[]>([]);
  const [setupOpen, setSetupOpen] = useState(false);
  // Closed on mount, like the replay's ticket: the panel lays over the tape it is
  // about, and the chart is what you came to the page for. The rail button opens
  // it in one click, and the feed it holds is a log — what happened while it was
  // shut is still there when you open it.
  //
  // Unpinned by default — the feed lays over the tape rather than taking a
  // column off it. Pinning is there when you want to read the two side by side,
  // and it sticks (live.chartKnobs): it is a statement about how you work, not
  // a fact about the session.
  //
  // One panel, two views, rather than a second panel for coverage: they would
  // otherwise stack in the unpinned overlay and fight over the same column when
  // pinned, and the layout rules for that are a second set to keep in step for
  // no gain. Coverage is something you consult, not something you watch.
  const [railView, setRailView] = useState<
    "signals" | "coverage" | "routing" | "blotter" | null
  >(null);
  const signalsOpen = railView === "signals";
  const [railPinned, setRailPinned] = useState(knobs.railPinned);
  const [indicators, setIndicators] = useState(knobs.indicators);
  // The rest of the pane grid, exactly as the Simulator carries it — same layout
  // model, same link, same per-pane bucketings, its own store. See lib/paneLayout.
  // `layout` and `paneTfIds` themselves are up with the timeframe, for the reason
  // given there.
  const [splitPct, setSplitPct] = useState(knobs.splitPct);
  const [splitPctY, setSplitPctY] = useState(knobs.splitPctY);
  const [linkOn, setLinkOn] = useState(knobs.linkOn);
  const [toolsPinned, setToolsPinned] = useState(knobs.toolsPinned);
  const [presetBucket, setPresetBucket] = useState(knobs.presetBucket);
  const [paneLinked, setPaneLinked] = useState(knobs.paneLinked);
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
  const paneCountRef = useRef(paneCount);
  paneCountRef.current = paneCount;
  const paneTfKey = paneTfIds.join(",");
  const paneTfsRef = useRef(paneTfIds.map(timeframeById));
  paneTfsRef.current = paneTfIds.map(timeframeById);
  const [focus, setFocus] = useState(0);
  const focusedPane = clampPaneIndex(focus, layout);
  const focusRef = useRef(focusedPane);
  focusRef.current = focusedPane;
  useEffect(() => {
    setLinkModuleOn(linkOn);
  }, [linkOn]);
  // Everything the store carries, written wholesale — same shape as the load.
  useEffect(() => {
    saveLiveChartKnobs({
      bigLots,
      nodeProm,
      composite: compositeRule,
      compositeSpan,
      eventTuning: evTuning,
      eventLabelSt: evLabelSt,
      eventFillSweep: evFillSweep,
      eventFillAbsorb: evFillAbsorb,
      eventFloorSweep: evFloorSweep,
      eventFloorAbsorb: evFloorAbsorb,
      eventMarginal: evMarginal,
      modernVwap: mvParams,
      dynamicSwingVwap: dsvParams,
      timeframe: tfId,
      indicators,
      railPinned,
      layout,
      paneTfs: paneTfIds,
      splitPct,
      splitPctY,
      linkOn,
      paneLinked,
      toolsPinned,
      presetBucket,
    });
  }, [bigLots, nodeProm, compositeRule, compositeSpan, evTuning, evLabelSt, evFillSweep, evFillAbsorb, evFloorSweep, evFloorAbsorb, evMarginal, mvParams, dsvParams, tfId, indicators, railPinned, layout, paneTfIds, splitPct, splitPctY, linkOn, paneLinked, toolsPinned, presetBucket]);
  // THE ticket — one object, and the page owns it. Size and the bracket that
  // every origination point on this page measures its order with: the setup
  // drawer below, the chart's own long-press ticket, and the routing panel's
  // order pad, which is handed this rather than keeping a second copy of it.
  // Two tickets is how an order goes out carrying a bracket nobody typed.
  //
  // Defaults and the validators are in `lib/simPrefs` (DEFAULT_LIVE_TICKET), and
  // it is loaded from there rather than hard-coded: what you set last time is
  // what the page opens on. `trailTriggerTicks`/`beTicks` are Rithmic's fields
  // and reach real accounts only; the `ladder*` four are this app's own and run
  // on paper too, because it is the same rule either way — see `draftFor`.
  const [rawTicket, setTicket] = useState(loadLiveTicket);
  const tickSize = header?.tick_size ?? 0.25;
  const pointValue = header?.point_value ?? 20;
  // **The contract orders actually go to**, which is not always the one on
  // screen: routing can be pointed at the mini's micro while the tape stays on
  // the mini, because one login is one socket and the subscription was made at
  // connect. Everything drawn from broker state is measured with this rather
  // than with the tape's, or the chips price an MNQ position at NQ's $20 a
  // point and read ten times the money that is on. Both numbers come down on
  // the routing poll and follow `broker.symbol` (see routingTypes).
  const routedTick = brokerState?.tick_size ?? tickSize;
  const routedPoint = brokerState?.point_value ?? pointValue;
  /** What one tick of one contract costs on the instrument this ticket's orders
   *  would actually reach — the routed one where they reach an exchange, the
   *  tape's on paper, which trades what it draws. This is what a dollar-pinned
   *  leg is divided by, and it is the reason the pin is worth having: pointing
   *  routing at the micro re-derives a $250 stop into the micro's distance
   *  instead of leaving ten times the risk on a bracket that looks unchanged. */
  const orderTickUsd = intent.real ? routedTick * routedPoint : tickSize * pointValue;
  /** The ticket as every order path must read it: with a dollar-pinned leg
   *  resolved to the distance it comes to right now (lib/bracketUsd). Resolved
   *  here, once, at the top — the pin is a rule about what `stopTicks` *is*, and
   *  a surface reading the stored value instead would be showing a bracket that
   *  is not the one about to be sent. */
  const ticket = useMemo(() => resolveBracketUsd(rawTicket, orderTickUsd), [rawTicket, orderTickUsd]);
  const {
    size, stopTicks, targetTicks, trailSource, trailTriggerTicks, beTicks,
    beLock, ladderTicks, ladderStepTicks, ladderBeTicks, ladderBeOnly,
  } = ticket;
  // Stored raw: what a leg falls back to is the distance somebody set, not this
  // session's resolution of a pin.
  useEffect(() => saveLiveTicket(rawTicket), [rawTicket]);
  /** One field of the ticket, changed. Everything that edits it comes here —
   *  including the two `*Usd` pins, because a leg's unit and its distance are
   *  one setting and the two must never be written apart.
   *
   *  Setting a **distance** unpins that leg: typing 60 into the stop box is a
   *  statement that 60 is the stop, and a pin left standing would re-derive over
   *  the top of it on the next render, so the box would appear to reject what
   *  was typed.
   *
   *  Setting a **pin** writes the distance it comes to as well (`pinnedLeg`) —
   *  that stored figure is what the leg falls back to when there is no routed
   *  contract to price it in, and a stale one there is a bracket nobody chose. */
  const setTicketField = useCallback(
    <K extends keyof LiveTicket>(key: K, v: LiveTicket[K]) =>
      setTicket((t) => {
        if (key === "stopUsd" || key === "targetUsd") {
          const leg = key === "stopUsd" ? "stop" : "target";
          const at = legTicks(t[`${leg}Ticks`], t[`${leg}Usd`], orderTickUsd, t.size);
          const next = pinnedLeg(v as number | null, at, orderTickUsd, t.size);
          return { ...t, [`${leg}Usd`]: next.usd, [`${leg}Ticks`]: next.ticks };
        }
        const unpin =
          key === "stopTicks" ? { stopUsd: null } : key === "targetTicks" ? { targetUsd: null } : null;
        if (t[key] === v && !unpin) return t;
        return { ...t, [key]: v, ...unpin };
      }),
    [orderTickUsd],
  );
  /** A whole bracket edited somewhere that holds one — a chart's long-press
   *  ticket, the floating knobs. Both legs arrive as either a distance or the
   *  money they are pinned to, and the pinned ones are re-resolved against the
   *  size in the same edit rather than the one that was on screen when it
   *  started. */
  const changeTicket = useCallback(
    (t: UsdBracket) =>
      setTicket((p) => {
        const stop = pinnedLeg(t.stopUsd, t.stopTicks, orderTickUsd, t.size);
        const target = pinnedLeg(t.targetUsd, t.targetTicks, orderTickUsd, t.size);
        return {
          ...p,
          size: t.size,
          stopTicks: stop.ticks,
          stopUsd: stop.usd,
          targetTicks: target.ticks,
          targetUsd: target.usd,
        };
      }),
    [orderTickUsd],
  );
  // The IB/range boxes ride along so the day-scale indicator strip can read
  // them: they are already tracked in geoRef for the chart's own overlays, and
  // going through the throttled HUD is what keeps the strip off a render per
  // frame — the same arrangement the Simulator uses.
  const [hud, setHud] = useState<{
    clockMs: number;
    lastPrice: number;
    openPnl: number;
    ib: IbBox | null;
    range: RangeBox | null;
  }>({ clockMs: 0, lastPrice: NaN, openPnl: 0, ib: null, range: null });
  const lastHudRef = useRef(0);
  const geoRef = useRef<{ ib: IbBox | null; range: RangeBox | null }>({ ib: null, range: null });

  // How much of the foot of the chart the market-order window is using, published
  // as --chart-floor so anything the chart parks down there (the order ticket, on
  // a fingertip) sits above it rather than under it. Reported by the window,
  // which knows whether it is still parked at the foot or has been dragged onto
  // the chart — floated, it claims no floor at all.
  const [floor, setFloor] = useState(0);

  // What the paper side charges a fill, read from the same store the Simulator
  // writes (lib/fillModel) — the shadow account and the practice account are one
  // engine, so they are one set of costs. Read once, on mount: the model is set
  // on the Replay page, and a live sitting that re-priced its own fills
  // mid-session would be re-writing trades that have already been shadowed.
  const fillsRef = useRef(loadFillModel());
  const fillCfg = useMemo<FillCfg>(
    () => ({ ...fillsRef.current, pointValue, tickSize }),
    [pointValue, tickSize],
  );

  /** The ladder the ticket is set to, as prices, or null when it is off.
   *
   *  Built once here and used by both halves of the page: the paper blotter runs
   *  it locally through `replaySim`, and a real order hands the same four
   *  numbers to the server, which runs the Python port of the same rule
   *  (`journal/live/ladder.py`, held to this one by a fixture). Ticks become
   *  prices at exactly one place, which is this one.
   *
   *  Null without a stop, because the ladder has no leg to move without one —
   *  the server refuses that pair, and a draft that reliably 422s is a bug in
   *  here rather than a message worth showing. */
  const ladderCfg = useMemo<TrailCfg | null>(
    () =>
      trailSource === "ladder" && ladderTicks > 0 && stopTicks > 0
        ? {
            dist: ladderTicks * tickSize,
            step: ladderStepTicks * tickSize,
            be: ladderBeTicks * tickSize,
            beOnly: ladderBeOnly,
          }
        : null,
    [trailSource, ladderTicks, ladderStepTicks, ladderBeTicks, ladderBeOnly, stopTicks, tickSize],
  );

  // --- helpers --------------------------------------------------------------
  const markPrice = useCallback((): number => {
    const v = engineRef.current?.lastPriceValue() ?? NaN;
    return Number.isFinite(v) ? v : NaN;
  }, []);

  const barAt = useCallback(
    (ms: number) => engineRef.current?.barTimeAt(ms) ?? Math.floor(ms / 1000),
    [],
  );

  const openPnl = useCallback(
    (lastPrice: number): number => {
      const o = openRef.current;
      if (!o || !Number.isFinite(lastPrice)) return 0;
      const dir = o.side === "long" ? 1 : -1;
      return (lastPrice - o.entryPrice) * dir * pointValue * o.size;
    },
    [pointValue],
  );

  const pushHud = useCallback(
    (lastPrice: number, clockMs: number, force = false) => {
      const now = performance.now();
      if (!force && now - lastHudRef.current < 80) return;
      lastHudRef.current = now;
      setHud({ clockMs, lastPrice, openPnl: openPnl(lastPrice), ...geoRef.current });
    },
    [openPnl],
  );

  // Whose orders the chart is currently drawing. The paper simulation keeps
  // running underneath a real account — it costs nothing and it means switching
  // back finds the blotter where you left it — but it must not be on screen,
  // because a paper order drawn while a real account is selected is the single
  // most expensive confusion this page could offer.
  const drawBrokerRef = useRef(false);
  drawBrokerRef.current = intent.real;

  const publish = useCallback(
    (st: SimState, clock: number) => {
      simRef.current = st;
      sigRef.current = simSig(st);
      openRef.current = st.open;
      const views = workingOrders(st).map((o) => orderView(o, clock, st.open));
      // Copies: the stepper appends to `trades` and mutates the position object
      // in place, so React would see the same reference and skip the render.
      setTrades(st.trades.slice());
      setOpenPos(st.open && { ...st.open });
      if (drawBrokerRef.current) {
        // The paper simulation is still running under the real account, and it
        // must not be heard any more than it is seen — but its baseline is kept
        // current so that switching back doesn't sound the fills it took while
        // it was off screen.
        paperCuesRef.current.sync(simMark(st));
        return;                            // the broker owns the chart layers
      }
      paperCuesRef.current.observe(simMark(st));
      setWorking(views);
      const pos = st.open ? posLine(st.open, barAt) : null;
      const marks = st.trades.map((t) => tradeMark(t, barAt));
      chartRef.current?.setPosition(pos);
      chartRef.current?.setOrders(views);
      chartRef.current?.setTrades(marks);
      // Every pane, not just pane 0. A `WorkingOrderView` carries no
      // x-coordinate — a resting order is a price and a horizontal line — so
      // drawing it on four bucketings costs the same as drawing it on one.
      eachExtra((c) => {
        c.setPosition(pos);
        c.setOrders(views);
        c.setTrades(marks);
      });
    },
    [barAt, eachExtra],
  );

  /**
   * Re-derive the blotter from the log. Every user action goes through here,
   * exactly as on the replay page: one pass over the tape per action, so there
   * is no second, optimistic code path that a later pass could disagree with.
   *
   * The pass resumes from the ladder's newest sound snapshot rather than from
   * the first order, which is what keeps it flat as the session grows — a live
   * tape would otherwise have every afternoon drag re-walking the morning. The
   * contract above is untouched: a snapshot is the same fold, paused (see
   * `SimLadder`), and it is dropped rather than trusted whenever the tape or the
   * consumed part of the log has moved under it.
   */
  const rebuild = useCallback(
    (clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      publish(ladder.run(tape, logRef.current, clock, fillCfg), clock);
    },
    [ladder, publish, fillCfg],
  );

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

  // --- the frame loop -------------------------------------------------------
  // Runs for as long as the page is mounted. `atEnd` is never true on a live
  // source, so nothing stops it — when the feed is quiet the clock doesn't move
  // and `advance()` has nothing to apply, which costs one comparison a frame.
  const frame = useCallback((ts: number) => {
    const eng = engineRef.current;
    if (eng) {
      const { clock } = sourceRef.current.clockFor(clockRef.current, 0, 1);
      if (clock > clockRef.current) {
        const r = eng.advance(clock);
        chartRef.current?.applyStep(r);
        geoRef.current = { ib: r.ib, range: r.range };
        clockRef.current = clock;
        // Every extra pane advances every frame regardless — the engine step is
        // ~0.006ms, and skipping it would leave its bars behind the clock. Only
        // the *draw* is gated; see PANE_DRAW_MS.
        for (let i = 1; i < paneCountRef.current; i++) {
          const e = extraEngines.current[i];
          if (!e) continue;
          const step = e.advance(clock);
          if (step.newBar || ts - (extraDrawn.current[i] ?? 0) >= PANE_DRAW_MS) {
            extraDrawn.current[i] = ts;
            extraCharts.current[i]?.applyStep(step);
          }
        }
        advanceSim(r.fromIdx, r.toIdx, clock);
        pushHud(r.lastPrice, clock);
      }
    }
    rafRef.current = requestAnimationFrame(frame);
  }, [advanceSim, pushHud]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(frame);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [frame]);

  // --- the tape -------------------------------------------------------------
  // A new accumulation: every row index the page was holding described a tape
  // that no longer exists, so the engine, the log and the blotter all go with it.
  const onReset = useCallback((tape: GrowableTape) => {
    tapeRef.current = tape;
    engineRef.current = null;
    // The extra panes' engines described the old tape too. Dropped rather than
    // re-primed here: the tape has no session in it yet, and `onAppend` is what
    // knows when it does.
    extraEngines.current = [];
    geoSigRef.current = "";
    clockRef.current = 0;
    logRef.current = newLog();
    // Belt and braces: the ladder already drops everything when the tape it was
    // handed is not the one it snapshotted, and an empty log fails the prefix
    // check regardless. Said out loud because the snapshots are the one thing
    // here that outlives a session without being visible on the page.
    ladder.reset();
    simRef.current = newSim();
    sigRef.current = simSig(simRef.current);
    // The blotter that made the last noises no longer exists. Without this the
    // fresh one's first booked trade would be compared against a count it can't
    // beat, and would pass unheard.
    paperCuesRef.current.sync(simMark(simRef.current));
    openRef.current = null;
    idRef.current = 1;
    setTrades([]);
    setOpenPos(null);
    setWorking([]);
    setReady(false);
  }, [ladder]);

  const primePaneRef = useRef<(only?: number) => void>(() => {});
  const resyncPanesRef = useRef<(reframe?: boolean | "follow") => void>(() => {});

  const onAppend = useCallback(
    (tape: GrowableTape) => {
      // `tape.n > tape.ctx`, not `tape.n > 0`: with prior days seeded in front,
      // the tape has rows before a single live print has landed, and building the
      // engine off them would put the session's start inside yesterday.
      if (!header || tape.n <= tape.ctx || engineRef.current) return;
      // The session's first print is where everything the engine develops
      // starts, and it is only knowable once that print has landed — which is
      // why the header ships `session_start_ms` as null until then. With context
      // seeded it is the first row *after* it; the engine binary-searches this
      // back into an index and draws everything before it as context bars.
      const payload = sessionPayloadFor(header, tape.t[tape.ctx]);
      // Kept for the broker's position line: when the broker never said when a
      // position opened — this process attached to one already running — the
      // honest fallback is the session's own start rather than an invented bar.
      sessionStartRef.current = tape.t[tape.ctx];
      const eng = new ReplayEngine(tape as Tape, payload, tfRef.current, engineContextRef.current);
      eng.setBigLots(bigLotsRef.current);
      eng.setEventTuning(evTuningRef.current);
      engineRef.current = eng;
      geoSigRef.current = geoSig(header);
      chartRef.current?.setTape(tape as Tape, { contextRanges: ctxRangeRef.current });
      const clock = tape.t[tape.n - 1];
      const snap = eng.snapshotTo(clock);
      chartRef.current?.setSnapshot(snap);
      geoRef.current = { ib: snap.ib, range: snap.range };
      clockRef.current = clock;
      // The extra panes, now that there is a session to build them over. Through
      // a ref because this callback is made before `primePane` is — and it has
      // to be here: a pane's chart mounted before the first print reports itself
      // ready to a page with no tape, and nothing else would ever come back to
      // it.
      primePaneRef.current();
      setReady(true);
      pushHud(snap.lastPrice, clock, true);
      // Every later block is picked up by the frame loop, which reads the tape's
      // newest print through `liveSource` and folds the new ticks in.
    },
    [header, pushHud],
  );

  // --- the extra panes ------------------------------------------------------
  const headerRef = useRef(header);
  headerRef.current = header;

  /**
   * Stand an extra pane up: its own engine over the tape in hand, handed to its
   * chart.
   *
   * Idempotent and called from both sides, for the reason the Simulator's copy
   * spells out — the page learns about a tape in an effect, the pane's chart is
   * built in an effect of its own, and React is free to throw that chart away
   * and build another. Whoever is last wins, and neither can be relied on to be.
   *
   * The one thing that is different here: the tape is still growing. Priming
   * mid-session is not a special case — the engine folds from row zero to the
   * clock, and the frame loop carries it on from there.
   */
  const primePane = useCallback((only?: number) => {
    const tape = tapeRef.current;
    const hdr = headerRef.current;
    if (!tape || !hdr || tape.n <= tape.ctx) return;
    const payload = sessionPayloadFor(hdr, tape.t[tape.ctx]);
    for (let i = 1; i < paneCountRef.current; i++) {
      if (only != null && only !== i) continue;
      const chart = extraCharts.current[i];
      if (!chart) continue;
      const e = new ReplayEngine(tape as Tape, payload, paneTfsRef.current[i], engineContextRef.current);
      e.setBigLots(bigLotsRef.current);
      e.setEventTuning(evTuningRef.current);
      extraEngines.current[i] = e;
      chart.setTape(tape as Tape, { contextRanges: ctxRangeRef.current });
      chart.setSnapshot(e.snapshotTo(clockRef.current));
      extraDrawn.current[i] = 0;
    }
  }, []);
  primePaneRef.current = primePane;

  /** Repaint every extra pane from its own engine, as of the page's clock. Every
   *  path that re-derives the primary calls this too: the panes are one session,
   *  and a pane showing a different session than the one under it is worse than
   *  no pane. */
  const resyncPanes = useCallback(
    (reframe?: boolean | "follow") => {
      eachExtra((c, i) => {
        const e = extraEngines.current[i];
        if (e) c.setSnapshot(e.snapshotTo(clockRef.current), { reframe });
      });
    },
    [eachExtra],
  );
  resyncPanesRef.current = resyncPanes;

  // Panes appearing, disappearing, or changing bucketing. Engines belonging to
  // panes the layout no longer draws go now rather than being advanced forever
  // by the frame loop over a chart that isn't on screen.
  useEffect(() => {
    for (let i = paneCount; i < extraEngines.current.length; i++) {
      extraEngines.current[i] = null;
      extraCharts.current[i] = null;
    }
    primePane();
    // paneTfKey rather than the array: `.map` hands back a fresh one per render.
  }, [paneCount, paneTfKey, primePane]);

  // A context-span change re-cuts the same days without touching the tape; the
  // chart no-ops when they are the ranges it already holds.
  useEffect(() => {
    eachExtra((c) => c.setContextRanges(contextRanges));
  }, [contextRanges, eachExtra]);

  const tapeState = useLiveTape({
    enabled: !!gen && !!header && historyReady,
    gen,
    tz: TZ,
    tickSize,
    pointValue,
    context: contextTapes,
    contextKey,
    onReset,
    onAppend,
  });

  // --- the chart, when the broker owns it -----------------------------------
  // Working orders and the open position come off the routing poll and are
  // drawn through the *same* primitives the paper blotter uses — `WorkingOrderView`
  // carries no x-coordinate, so an order that exists only at Rithmic needs
  // nothing this tape could not give it (see lib/brokerViews).
  //
  // Deliberately not optimistic. Nothing is drawn until the broker has said it,
  // which is what makes a rejected order or a rejected drag self-reporting: the
  // line simply never moves, or moves back. The cost is up to one poll of lag,
  // and that is the honest price of "the chart shows what is true".
  const brokerOrders = brokerState?.working;
  const brokerPos = brokerState?.position ?? null;
  const brokerTrades = brokerState?.trades;
  // Orders that are no longer working, newest first. Not drawn — it is read only
  // to name a fill (see `brokerMark`).
  const brokerRecent = brokerState?.recent;
  brokerOrdersRef.current = brokerOrders ?? null;
  brokerPosRef.current = brokerPos;
  // --- The risk sizer -------------------------------------------------------
  // The levels the live router itself enforces, falling back to the same
  // constants `guardRules` does when routing has not answered.
  const guards = routingQ.data?.guards ?? DEFAULT_GUARDS;
  // The vol ruler's reading, pushed up by the main chart on every bar close.
  const [volRead, setVolRead] = useState<VolRulerRead | null>(null);
  /** Whether orders are actually going to the micro. Measured off the money
   *  rather than off the symbol: routing carries the point value it resolved,
   *  and a tenth of the tape's is what "micro" means here. */
  const routedIsMicro = intent.real && routedPoint > 0 && routedPoint < pointValue;
  // The presets' ruler and the bar it is read at. All three bucketings arrive on
  // one reading (lib/volRuler), so the toggle picks out of a record rather than
  // re-measuring the tape.
  const presetCtx = useMemo<PresetCtx | null>(
    () =>
      volRead ? { read: volRead.preset, bucket: presetBucket, onBucket: setPresetBucket } : null,
    [volRead, presetBucket],
  );

  const sizerCtx = useMemo<SizerCtx | undefined>(() => {
    const mini = tickSize * pointValue;
    if (!(mini > 0)) return undefined;
    const root = rootOf(brokerState?.feed_symbol ?? status?.symbol ?? "");
    if (!root) return undefined;
    return {
      // The tape is always the mini here — there is no MNQ tick store (see
      // lib/contracts) — so the tape's own tick money is the mini's, whatever
      // routing is pointed at. That is exactly what the sizer wants.
      tickUsd: mini,
      commissionPerSide: guards.commission_per_side,
      // The budgets are constants in the lib and this page has no account view
      // to offer a floor, so the only limit passed is the fallback one the
      // losers-to-death column divides. `guards.daily_loss_stop` used to anchor
      // the budgets here; it stops the day's trading, it no longer sizes it.
      maxLossUsd: DEFAULT_MAX_LOSS,
      caps: { minis: 4, micros: 40 },
      stopTicksMax: guards.stop_ticks_max,
      root,
      microRoot: microOf(root),
      micro: routedIsMicro,
      // The routed instrument belongs to routing, not to this ticket. Cells for
      // the other contract go dead rather than silently re-routing an order.
      canSwitchContract: false,
    };
  }, [
    brokerState?.feed_symbol, guards.commission_per_side,
    guards.stop_ticks_max, pointValue, routedIsMicro, status?.symbol, tickSize,
  ]);
  /** Stop and size only — see `canSwitchContract`. */
  const applySizing = useCallback(
    (a: { stopTicks: number; size: number; micro: boolean }) => {
      if (a.micro !== routedIsMicro) return;
      setTicket((p) => ({ ...p, stopTicks: a.stopTicks, size: a.size }));
    },
    [routedIsMicro, setTicket],
  );
  /**
   * A bracket preset (lib/orderPresets), applied to this page's ticket.
   *
   * It lands on the **ladder** and switches `trailSource` to it, because that is
   * the only one of the two trails that can hold the shape: a preset says how
   * far behind the stop rides, on what grid, and where its first rung goes, and
   * Rithmic's native bracket has no vocabulary for any of that — it takes a wake
   * -up trigger and a lock, rides at `stopTicks` by force, and refuses to be
   * dragged. Translating a preset into it would be sending a bracket nobody
   * described.
   *
   * The cost is stated rather than hidden: the ladder is run by this app off the
   * live tape, so it stops ratcheting if the app does. That is the trade the
   * user made deliberately — the ladder is the rule the replay practises and the
   * backtest trades, and a preset that meant something different on this page
   * than in practice would be worth less than no preset. The panel's own
   * `trailSource` control still says which one is live, and switching back to
   * Rithmic keeps the ladder's numbers stored.
   */
  const applyPreset = useCallback(
    (b: PresetBracket) => {
      setTicket((p) => ({
        ...p,
        stopTicks: b.stopTicks,
        targetTicks: b.targetTicks,
        trailSource: "ladder",
        ladderTicks: b.trailTicks,
        ladderStepTicks: b.trailStepTicks,
        ladderBeTicks: b.trailBeTicks,
        ladderBeOnly: b.trailBeOnly,
      }));
    },
    [setTicket],
  );
  /** The five distances the ticket is carrying now, in the shape a preset is
   *  written in, so the card can light whichever preset equals it.
   *
   *  Null unless the **ladder** is the live trail: `trailSource` picks between
   *  this app's ladder and Rithmic's own native trail, and a preset always throws
   *  it to the ladder (see `applyPreset`). On Rithmic's the ladder fields are
   *  still sitting there holding their last values, and lighting a preset off
   *  numbers that are not managing the trade would be the card claiming a shape
   *  the account is not carrying. Nothing lit is the honest answer.  */
  const activeBracket = useMemo<PresetBracket | null>(
    () =>
      ticket.trailSource === "ladder"
        ? {
            stopTicks: ticket.stopTicks,
            targetTicks: ticket.targetTicks,
            trailTicks: ticket.ladderTicks,
            trailStepTicks: ticket.ladderStepTicks,
            trailBeTicks: ticket.ladderBeTicks,
            trailBeOnly: ticket.ladderBeOnly,
          }
        : null,
    [
      ticket.trailSource,
      ticket.stopTicks,
      ticket.targetTicks,
      ticket.ladderTicks,
      ticket.ladderStepTicks,
      ticket.ladderBeTicks,
      ticket.ladderBeOnly,
    ],
  );

  /** The contract a click on any pane would actually reach, when that is not the
   *  one the tape is drawing — the panes wear it as a badge. Undefined when the
   *  two agree, which is the ordinary case and wants no chrome at all: a badge
   *  that is always there stops being read. Only while orders really route
   *  (paper fills off the tape in front of you, whatever routing says). */
  const routedSymbol =
    intent.real && brokerState && brokerState.symbol !== brokerState.feed_symbol
      ? brokerState.symbol
      : undefined;
  useEffect(() => {
    if (!intent.real || !brokerOrders) return;
    const sig = brokerSig(brokerOrders, brokerPos, brokerTrades ?? []);
    if (sig === brokerSigRef.current) return;
    // The first word this account has said — the account was just selected, or
    // the page was just opened. Whatever is held and working at that point is
    // the state we found, not something that happened, and this process may well
    // have attached to a position that was opened before it started.
    const attaching = brokerSigRef.current === "";
    brokerSigRef.current = sig;
    // `recent` rides along so a fill on a resting order can be named as one: it
    // is the only place a real account says what type of order just filled.
    const mark = brokerMark(brokerPos, brokerTrades ?? [], brokerRecent ?? []);
    if (attaching) brokerCuesRef.current.sync(mark);
    else brokerCuesRef.current.observe(mark);
    // The routed contract's tick, not the tape's: `stop_ticks` on a working
    // entry is a distance in the contract it was sent to, and NQ and MNQ only
    // happen to share a tick size.
    const views = workingViews(
      brokerOrders,
      brokerPos,
      basketsRef.current,
      routedTick,
      routedPoint,
    );
    setWorking(views);
    const pos = positionLine(brokerPos, brokerOrders, barAt, sessionStartRef.current, {
      tickSize: routedTick,
      pointValue: routedPoint,
    });
    // Round trips the server paired out of the fill stream, drawn with the same
    // marks the paper blotter uses — same primitive, same vocabulary for the
    // exit reason, so the two kinds of trade read identically on one chart.
    const marks = tradeViews(brokerTrades ?? [], barAt);
    chartRef.current?.setOrders(views);
    chartRef.current?.setPosition(pos);
    chartRef.current?.setTrades(marks);
    // …and on every other pane. What is live at the broker belongs on all of
    // them: a stop you cannot see on the chart you happen to be reading is a
    // stop you will forget is there.
    eachExtra((c) => {
      c.setOrders(views);
      c.setPosition(pos);
      c.setTrades(marks);
    });
  }, [barAt, brokerOrders, brokerPos, brokerRecent, brokerTrades, eachExtra, intent.real,
      routedPoint, routedTick]);

  // Switching account swaps whose orders are on the chart. Cleared first and
  // rebuilt from scratch rather than diffed: the two sets are different kinds of
  // thing keyed in different ways, and a leftover line from the other one is
  // exactly the confusion this page must not offer.
  const activeAccount = brokerState?.account_id ?? null;
  useEffect(() => {
    basketsRef.current.clear();
    brokerSigRef.current = "";
    chartRef.current?.setOrders([]);
    chartRef.current?.setPosition(null);
    chartRef.current?.setTrades([]);
    eachExtra((c) => {
      c.setOrders([]);
      c.setPosition(null);
      c.setTrades([]);
    });
    setWorking([]);
    // Back on paper: repaint the blotter, which has been running underneath all
    // along and is exactly where it was left.
    if (!drawBrokerRef.current) rebuild(clockRef.current);
  }, [activeAccount, eachExtra, rebuild]);

  // --- paper trades reach the journal ---------------------------------------
  // The one surface in this app where you actually trade used to be the one
  // surface whose trades it never saw: this blotter lived in the browser and
  // died on a reload. Each closed paper trade is now posted and booked under the
  // account `paper`, tagged replay so it shows up in Trades and the Calendar
  // without inflating real-money statistics.
  //
  // A high-water mark rather than a diff, because on this page the log is
  // append-only — there is no rewind, by construction — so `trades` only ever
  // grows and everything past the mark is new. Re-posting is harmless anyway
  // (the server dedupes on a content hash), which is what lets this stay a
  // fire-and-forget effect with no retry logic.
  const journaledRef = useRef(0);
  useEffect(() => {
    if (intent.real || !header) return;      // a real account books server-side
    const fresh = trades.slice(journaledRef.current);
    if (fresh.length === 0) return;
    journaledRef.current = trades.length;
    void journalPaperTrades({
      symbol: header.symbol,
      date: header.date,
      trades: fresh.map((t) => ({
        side: t.side,
        size: t.size,
        entry_price: t.entryPrice,
        entry_ms: t.entryMs,
        exit_price: t.exitPrice,
        exit_ms: t.exitMs,
        pnl: t.pnl,
        pts: t.pts,
        reason: t.reason,
      })),
    }).catch((e) => {
      // Put the mark back so the next closed trade carries these along. The
      // blotter is still on screen and correct; only the journal is behind.
      journaledRef.current -= fresh.length;
      intent.fail(e);
    });
  }, [header, intent, trades]);

  // --- the feed dropping ----------------------------------------------------
  // The one cue on this page that is not about an order, and the one worth the
  // most: a chart that has stopped updating looks exactly like a quiet market.
  //
  // Only a real feed can lose a connection — the fake one has no `feed_status`
  // at all — and only a feed that *was* connected can lose one, which is what
  // the ref is for: the flag starts false, and announcing that would greet you
  // with "connection lost" every time you opened the page before connecting.
  // Silent on the way back up, deliberately: the tape resuming is visible on the
  // chart, and a reconnect chime on a flapping socket is a chime every minute.
  const connected = status?.feed_status?.connected;
  const wasConnectedRef = useRef(false);
  useEffect(() => {
    if (connected === undefined) return;      // not a feed that reports one
    if (wasConnectedRef.current && !connected) playCue("connectionLost");
    wasConnectedRef.current = connected;
  }, [connected]);

  // A re-seed clears the blotter (every order's `idx` is a tick index into a
  // tape that stopped existing), so the mark has to go back to zero with it —
  // otherwise the next paper trade of the new sitting would be treated as
  // already booked. What was journalled stays journalled: those trades happened.
  useEffect(() => {
    journaledRef.current = 0;
  }, [contextKey]);

  // The header answering something it could not answer when the engine was
  // built. See `geoSig`: the night, the Globex anchor and the weekly seed all
  // arrive late on a connect whose backfill lands behind the first live print,
  // and an engine built in that window develops no Globex VWAP, no Globex band
  // and no weekly line for the rest of the sitting.
  //
  // A whole new engine rather than a setter, because these are constructor
  // facts: the anchors decide what every accumulator has been fed since tick
  // zero, so repairing them *is* a re-derivation from the start of the tape —
  // the same one a timeframe change does, on the same path. The view is held
  // rather than reframed: nothing about what you are looking at has changed.
  useEffect(() => {
    const tape = tapeRef.current;
    if (!header || !engineRef.current || !tape || tape.n <= tape.ctx) return;
    const sig = geoSig(header);
    if (sig === geoSigRef.current) return;
    geoSigRef.current = sig;
    // The ⚓ is the user's, and it is placed on a bar time — which a repaired
    // header doesn't move. Carried across the rebuild, as on Replay.
    const anchor = engineRef.current.anchor();
    const eng = new ReplayEngine(
      tape as Tape,
      sessionPayloadFor(header, tape.t[tape.ctx]),
      tfRef.current,
      engineContextRef.current,
    );
    eng.setBigLots(bigLotsRef.current);
    eng.setEventTuning(evTuningRef.current);
    if (anchor != null) eng.setAnchor(anchor);
    engineRef.current = eng;
    const snap = eng.snapshotTo(clockRef.current);
    chartRef.current?.setSnapshot(snap, { reframe: false });
    geoRef.current = { ib: snap.ib, range: snap.range };
    // The extra panes' engines were built on the same header this just found to
    // be wrong, so they are rebuilt too rather than left developing a session
    // whose geometry has moved under them.
    primePaneRef.current();
    rebuild(clockRef.current);
  }, [header, rebuild]);

  // A timeframe change is a re-derivation of the same tape, exactly as on the
  // replay page: the log and therefore every fill are untouched, because fills
  // come off tick indices and those don't know what a bar is.
  useEffect(() => {
    const eng = engineRef.current;
    if (!eng) return;
    eng.setTimeframe(tf);
    chartRef.current?.clearRuler();
    const snap = eng.snapshotTo(clockRef.current);
    chartRef.current?.setSnapshot(snap);
    geoRef.current = { ib: snap.ib, range: snap.range };
    rebuild(clockRef.current);
  }, [rebuild, tf]);

  // A change of span re-cuts the same days without touching the tape. The chart
  // no-ops when they are the ranges it already holds, so the seed path's own
  // setTape is not doubled up on — same arrangement as Replay.
  useEffect(() => {
    chartRef.current?.setContextRanges(contextRanges);
  }, [contextRanges]);

  // The chart's ⚓ tool moved. The anchored band develops from the tape like the
  // session anchors do, so the engine owns it and the picture is rebuilt through
  // the one path that already exists for that — without re-framing the viewport,
  // since placing an anchor isn't a move through time. The frame loop carries on
  // from the same clock and keeps extending the band as prints arrive.
  const setAnchor = useCallback((barTime: number | null) => {
    const eng = engineRef.current;
    if (!eng) return;
    eng.setAnchor(barTime);
    const snap = eng.snapshotTo(clockRef.current);
    chartRef.current?.setSnapshot(snap, { reframe: false });
    geoRef.current = { ib: snap.ib, range: snap.range };
  }, []);

  /** The same, for an extra pane. The ⚓ is the one gesture that is genuinely
   *  per pane — it draws on the chart you dropped it on — so each pane's engine
   *  keeps its own anchor. */
  const setPaneAnchor = useCallback((i: number, barTime: number | null) => {
    const eng = extraEngines.current[i];
    if (!eng) return;
    eng.setAnchor(barTime);
    extraCharts.current[i]?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
  }, []);

  /** Change what counts as a big trade — a re-derivation, exactly as on Replay:
   *  which sweeps clear the threshold is a question about the tape, so the
   *  engine re-runs it rather than the chart filtering marks it was given. */
  const changeBigLots = useCallback((lots: number) => {
    setBigLotsState(lots);
    const eng = engineRef.current;
    if (!eng) return;
    eng.setBigLots(lots);
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
    extraEngines.current.forEach((e) => e?.setBigLots(lots));
    resyncPanesRef.current(false);
  }, []);

  /** Change what selects a tape event — the same path, for a stronger version
   *  of the same reason: a burst is a cluster and an absorption is scored
   *  against a median, so neither can be recovered by filtering what a
   *  different setting published. */
  const changeEvTuning = useCallback((patch: Partial<EventTuning>) => {
    setEvTuning((t) => ({ ...t, ...patch }));
    const eng = engineRef.current;
    if (!eng) return;
    eng.setEventTuning(patch);
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
    extraEngines.current.forEach((e) => e?.setEventTuning(patch));
    resyncPanesRef.current(false);
  }, []);

  // The event layer as the chart takes it — its presence is what offers the
  // layer at all, and this page now offers it: the engine has always been
  // detecting, the bands just never reached the canvas here.
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
          rule: compositeRule,
          onRule: setCompositeRule,
          span: compositeSpan,
          onSpan: setCompositeSpan,
          note: `Built from the ${contextTapes.length} prior session${contextTapes.length === 1 ? "" : "s"} drawn — "Prior days" in the ticket panel, since each one is a whole tape to fetch.`,
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
      compositeRule,
      compositeSpan,
      contextTapes.length,
      evFillSweep,
      evFillAbsorb,
      evFloorSweep,
      evFloorAbsorb,
      evLabelSt,
      evMarginal,
      evTuning,
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

  // --- trading --------------------------------------------------------------
  const append = useCallback(
    (next: Log) => {
      logRef.current = next;
      rebuild(clockRef.current);
    },
    [rebuild],
  );

  /** Place an order. `at` is the price its bracket is measured from — the mark
   *  for a market order, the resting price for a limit or a stop. */
  const placeOrder = useCallback(
    (type: OrderType, side: Side, price: number | null, at: number) => {
      const eng = engineRef.current;
      if (!eng) return;
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
        // ...and the distances, which is what a market order's bracket really
        // is (see `OrderRec.stopTicks`). Paper matters more than the replay
        // here, not less: a real order on this same page sends `stop_ticks` and
        // Rithmic hangs the legs off the fill, so a paper blotter measuring from
        // the click would be the one place the two halves of this page disagree.
        ...(type === "market" ? { stopTicks, targetTicks } : {}),
        // The ladder the ticket is set to, and the same one a real order would
        // get. This used to be a hard `null` on the argument that paper must not
        // imitate Rithmic's server-side ratchet — true while Rithmic owned the
        // trail, and dead now that the ladder is ours: paper runs the rule here,
        // a real account runs the Python port of it, and a fixture holds the two
        // together. Practising the exit you actually trade is the point.
        trail: ladderCfg,
        edits: [],
        cancelMs: null,
      };
      // Resting orders only: a market order is its own fill and the rebuild
      // sounds it as one a moment later. The real-account path makes its own
      // noise where the order actually goes out (see useOrderIntent) — this
      // function is never reached on that side.
      if (type !== "market") playCue("placed");
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
    },
    [append, size, stopTicks, targetTicks, tickSize, ladderCfg],
  );

  /** The bracket the ticket is set to, as the broker wants it: ticks, not
   *  prices. The paper path measures its own from the fill; a real order carries
   *  the distances and Rithmic attaches the legs.
   *
   *  **One trail source or the other, never both.** The two blocks below are
   *  mutually exclusive at the wire and the server refuses the pair outright —
   *  Rithmic re-derives a managed stop absolutely on every new extreme, so a
   *  ladder running against it would spend the session being overwritten. The
   *  ticket keeps both sets of numbers because switching sources should not throw
   *  away settings; this is where only one of them becomes an order.
   *
   *  The asymmetry that used to live here is gone. `placeOrder` now gives paper
   *  the same ladder, because the rule is ours on both sides: the blotter runs
   *  `replaySim`'s and the server runs the Python port, held together by
   *  `tests/test_live_ladder.py`. Rithmic's own trail is still the one thing
   *  paper cannot imitate, and still the one that survives this app dying. */
  const draftFor = useCallback(
    (side: Side, type: "market" | "limit" | "stop", price: number | null): OrderDraft => {
      const ladder = trailSource === "ladder" && stopTicks > 0;
      return {
        // The chart speaks long/short (a position), the broker speaks buy/sell
        // (an instruction). Translated here rather than anywhere else, so there
        // is one place where the two vocabularies meet.
        side: side === "long" ? "buy" : "sell",
        qty: size,
        type,
        price,
        stop_ticks: stopTicks,
        target_ticks: targetTicks,
        trail_trigger_ticks: !ladder && stopTicks ? trailTriggerTicks : 0,
        be_trigger_ticks: !ladder && stopTicks ? beTicks : 0,
        // Never sent without its trigger, and never sent as 0 with one: the
        // server refuses both, and a draft that reliably 422s is a bug in here.
        be_ticks: !ladder && stopTicks && beTicks ? Math.max(1, beLock) : 0,
        ladder_dist_ticks: ladder ? ladderTicks : 0,
        ladder_step_ticks: ladder ? ladderStepTicks : 0,
        ladder_be_ticks: ladder ? ladderBeTicks : 0,
        ladder_be_only: ladder ? ladderBeOnly : false,
      };
    },
    [size, stopTicks, targetTicks, trailSource, trailTriggerTicks, beTicks, beLock,
     ladderTicks, ladderStepTicks, ladderBeTicks, ladderBeOnly],
  );

  const placeMarket = useCallback(
    (side: Side, gesture = "key") => {
      if (!ready) return;
      const px = markPrice();
      if (!Number.isFinite(px)) return;
      // `submit` returns false only when the active account is paper — then,
      // and only then, the gesture falls through to the blotter. Which way it
      // went is never decided here.
      if (intent.submit(draftFor(side, "market", null), gesture)) return;
      placeOrder("market", side, null, px);
    },
    [draftFor, intent, markPrice, placeOrder, ready],
  );

  /** Rest an order at a price, held one tick clear of the mark on the side its
   *  type belongs on — a marketable resting order would fill on the next print
   *  at a price better than the market, which the tape cannot do. */
  const placeResting = useCallback(
    (price: number, side: Side, type: "limit" | "stop", gesture = "click") => {
      if (!ready) return;
      const mk = markPrice();
      if (!Number.isFinite(mk) || !Number.isFinite(price)) return;
      const px = Math.round(price / tickSize) * tickSize;
      const above = type === "stop" ? side === "long" : side === "short";
      const rest = above ? Math.max(px, mk + tickSize) : Math.min(px, mk - tickSize);
      // The clamp applies to both paths: an order resting on the wrong side of
      // the market is a fill at a price the tape cannot give you on paper, and a
      // rejection at the exchange on a real account. Same gesture, same rule.
      if (intent.submit(draftFor(side, type, rest), gesture)) return;
      placeOrder(type, side, rest, rest);
    },
    [draftFor, intent, markPrice, placeOrder, ready, tickSize],
  );

  /** Space + click at a price: the left button places the passive order there (a
   *  bid under the market, an offer over it), the right button the one that has
   *  to be run through. The side flips as you cross the market, which is the
   *  point — you click the level and the platform works out the order type. */
  const placeAt = useCallback(
    (price: number, button: "left" | "right") => {
      const mk = markPrice();
      if (!Number.isFinite(mk)) return;
      const below = price < mk;
      const passive = button === "left";
      const side: Side = passive === below ? "long" : "short";
      placeResting(price, side, passive ? "limit" : "stop", "click");
    },
    [markPrice, placeResting],
  );

  const cancelOrder = useCallback(
    (id: number) => {
      // On a real account the ✕ cancels at the broker. No confirm, deliberately
      // and consistently with `closeAll`: taking risk off is never the thing
      // that needs slowing down, and a cancel you have to confirm is a cancel
      // you sometimes don't make.
      if (drawBrokerRef.current) {
        const basket = basketsRef.current.id(id);
        if (!basket) return;   // a stale paper view — not this account's order
        void cancelBrokerOrder(basket)
          .then(() => {
            // On the acknowledgement, like `placed` on this side: the sound
            // means the broker took it, not that you asked.
            playCue("canceled");
            void routingQ.refetch();
          })
          .catch((e) => intent.fail(e));
        return;
      }
      const log = logRef.current;
      if (log.orders.some((o) => o.id === id && o.cancelMs == null)) playCue("canceled");
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id && o.cancelMs == null ? { ...o, cancelMs: clockRef.current } : o,
        ),
      });
    },
    [append, intent, routingQ],
  );

  const editOrder = useCallback(
    (id: number, next: { price: number | null; stop: number | null; target: number | null }) => {
      // A working order's resting price, dragged. On a real account it is a
      // modify at the broker; the chart has already drawn it where it landed,
      // and the next poll either confirms that or moves it back — which is the
      // whole error-reporting mechanism, so there is nothing optimistic to undo.
      if (drawBrokerRef.current) {
        const basket = basketsRef.current.id(id);
        if (!basket || next.price == null) return;
        // Only the resting price is a modify. The legs sketched behind a working
        // entry are the bracket Rithmic will attach when it fills — they do not
        // exist as orders yet, so there is nothing there to move, and sending
        // the unchanged entry price on a leg drag would put a pointless modify
        // on the wire. The leg snaps back on the next poll, which is this
        // page's standing way of saying "that didn't take".
        const held = brokerOrdersRef.current?.find((o) => o.basket_id === basket);
        const at = held ? held.trigger_price || held.price : null;
        // The routed contract's tick — this order rests at the broker, on
        // whatever routing is pointed at, not on whatever the tape is drawing.
        if (at != null && Math.abs(at - next.price) < routedTick / 2) return;
        void modifyBrokerOrder({ basket_id: basket, price: next.price })
          .then(() => {
            playCue("changed");
            void routingQ.refetch();
          })
          .catch((e) => {
            intent.fail(e);
            void routingQ.refetch();
          });
        return;
      }
      const log = logRef.current;
      // Once per landed drag — the chart reports a move on release, not per
      // pixel (see ReplayChart's pointer-up).
      playCue("changed");
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id ? { ...o, edits: [...o.edits, { ms: clockRef.current, ...next }] } : o,
        ),
      });
    },
    [append, intent, routedTick, routingQ],
  );

  // The open position's bracket, dragged. Its own channel in the log rather than
  // an edit on the order that opened the position: with several fills making up
  // one position, "the stop" belongs to the position, not to any of them.
  const moveBracket = useCallback(
    (b: { stop: number | null; target: number | null }) => {
      // The open position's stop or target, dragged. On a real account those
      // legs are separate orders at Rithmic, so the drag becomes a modify on
      // whichever leg moved — and only that leg, never both at once: Rithmic
      // refuses two bracket operations on one order ('Atomic order operation in
      // progress'), and sending an unchanged leg alongside the changed one would
      // trip it. One *basket* is addressed here even when the leg is several
      // orders — a partially-filled bracket entry gets a leg pair per fill — and
      // the broker fans the move out to the siblings (`_sibling_legs`), because
      // which orders make up this position's stop is its question, not the
      // chart's.
      if (drawBrokerRef.current) {
        const pos = brokerPosRef.current;
        const orders = brokerOrdersRef.current;
        if (!pos || pos.net === 0 || !orders) return;
        const cur = bracketOf(orders, pos);
        // Exactly one leg per drag, and it has to be the right one: the legs are
        // separate orders at Rithmic, and it refuses two bracket operations at
        // once. Which one moved is decided by comparing against what the broker
        // last said, not by what the chart is drawing.
        const moved =
          b.stop !== cur.stop
            ? ({ id: cur.stopId, body: { stop: b.stop }, leg: "stop" } as const)
            : b.target !== cur.target
              ? ({ id: cur.targetId, body: { target: b.target }, leg: "target" } as const)
              : null;
        if (!moved) return;
        if (!moved.id) {
          // That leg is not working, so there is nothing to move — and Rithmic
          // cannot attach one after the fact. Said rather than ignored: a drag
          // that appeared to do nothing would read as a UI bug when it is a
          // property of the order you placed.
          intent.fail(
            new Error(
              `this position has no working ${moved.leg} — Rithmic cannot attach ` +
                "one after the fact. Place it as its own order, or cancel and " +
                "re-enter with a bracket.",
            ),
          );
          return;
        }
        if (moved.leg === "stop" && cur.stopManaged) {
          // Rithmic owns this stop. It rides at a fixed distance behind the
          // extreme and recomputes from it — so a drag would hold only until the
          // next tick of profit and then be put back *wider*, which is the
          // direction that costs money and the one the chart would go on
          // drawing wrong. Refused here to save the round trip; the broker
          // refuses it too, and catches the breakeven-only bracket that leaves
          // no mark on the leg for this to read.
          intent.fail(
            new Error(
              "Rithmic is trailing this stop — it re-derives it from the high " +
                "water mark, so a drag here would be silently put back, wider. " +
                "Flatten, or re-enter without a trail.",
            ),
          );
          return;
        }
        void modifyBrokerOrder({ basket_id: moved.id, ...moved.body })
          .then(() => {
            playCue("changed");
            void routingQ.refetch();
          })
          .catch((e) => {
            intent.fail(e);
            void routingQ.refetch();
          });
        return;
      }
      if (!openRef.current) return;
      playCue("changed");
      const log = logRef.current;
      append({ ...log, brackets: [...log.brackets, { ms: clockRef.current, ...b }] });
    },
    [append, intent, routingQ],
  );

  /** Everything off: the position at the last print, and every order working
   *  with it — one append, so half a flatten is not a state this can sit in. */
  const closeAll = useCallback(() => {
    // On a real account this is the kill switch, and it is deliberately gated
    // on nothing but the connection and put behind no confirm — the moment you
    // most want it is the one where something is misbehaving. It cancels
    // everything working and exits the position, at the broker.
    if (brokerState && !brokerState.paper && brokerState.attached) {
      void flattenAll()
        .then(() => void routingQ.refetch())
        .catch(() => void routingQ.refetch());
      return;
    }
    const live = new Set(workingOrders(simRef.current).map((o) => o.id));
    const hadPos = openRef.current != null;
    if (!hadPos && live.size === 0) return;
    // A flatten that took a position off is announced by the exit cue the
    // rebuild produces; only one that just pulled orders says "canceled". Same
    // rule as the replay page. On the broker path above neither is said here —
    // both the cancels and the exit come back through the poll.
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
    // `brokerState` and `routingQ` are read above and MUST be listed, or this
    // closes over the state as it was on first render — which is `null`, before
    // the routing poll has answered. That is not a stale number, it is the
    // wrong branch: q takes the paper path forever and silently flattens a
    // blotter while the real position stays open. Every other broker-aware
    // callback on this page reaches through a ref for exactly this reason;
    // this one read the state directly and did not declare it.
  }, [append, brokerState, markPrice, pushHud, routingQ]);

  // q / w / s, the same three keys as the replay page — the muscle memory is the
  // point, so it does not get its own bindings here.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      const k = e.key.toLowerCase();
      if (k !== "q" && k !== "w" && k !== "s") return;
      // Stand down while a confirm is open. The dialog owns Enter and Esc, and
      // a second w behind an unanswered one is the fastest way to send two
      // orders when you meant one — a stuck key would do it on its own.
      if (intent.pending) return;
      e.preventDefault();
      if (k === "q") closeAll();
      else placeMarket(k === "w" ? "long" : "short");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeAll, intent.pending, placeMarket]);

  // And the pane keys, which Live did not have at all: 1–8 for the focused
  // pane's bar size, Shift+1–4 to focus one. Phase 7 gave this page the same
  // layouts and left it driving them by mouse only, which is the kind of gap
  // that reads as "the replay is the real one and this is the copy".
  //
  // The transport keys stay replay-only for the plainest possible reason: there
  // is no clock to scrub on a live tape.
  usePaneKeys({
    paneCount: useCallback(() => paneCountRef.current, []),
    focused: useCallback(() => focusRef.current, []),
    setFocus,
    setPaneTimeframe: useCallback(
      (pane: number, id: string) =>
        pane === 0
          ? setTfId(id)
          : setPaneTfIds((prev) => prev.map((t, j) => (j === pane ? id : t))),
      [],
    ),
    // The same stand-down the trading keys take: a confirm dialog owns the
    // keyboard while it is up, and re-bucketing a chart underneath an order you
    // have not answered for yet is the wrong thing to be doing.
    disabled: useCallback(() => !!intent.pending, [intent.pending]),
  });

  // The blotter's rows, off whichever record this page is showing — the same
  // card the replay draws, from the same view type (lib/simViews, BlotterRow).
  //
  // Everything the header says about the day is counted off these, including
  // the running total and the trade count behind the title: the paper
  // simulation keeps running underneath a real account, so this has to *pick*
  // rather than sum — paper's figures while a real account is live would be
  // numbers that mean nothing about the money at risk.
  //
  // Paper rows carry no contract badge: unlike the replay, nothing on this page
  // can point the *simulation* at the micro (routing can, but that is the other
  // branch), so every paper row is in the tape's own contract and a badge would
  // be noise on all of them.
  const blotterRows = useMemo(
    () =>
      intent.real
        ? brokerBlotterRows(brokerTrades ?? [], brokerState?.symbol)
        : trades.map((t) => blotterRow(t)),
    [brokerState?.symbol, brokerTrades, intent.real, trades],
  );
  // Summed off the rows rather than off the raw trades, because the broker
  // records `pnl` gross and charges the commission into the day's total
  // separately — so this used to be a gross figure sitting under a guard panel
  // reading net. `blotterRows` is where the subtraction happens.
  const net = blotterRows.reduce((a, r) => a + r.pnl, 0);
  // Off the same rows, and off `openStamps` inside `paceRefusal` rather than the
  // row count — a Rithmic bracket is attached per *partial fill*, so a single
  // entry that filled in three parts is three rows seconds apart and would fire
  // this on every entry of the day. The blotter has been bitten by that once
  // already; see `TradeTally`.
  const pace = useMemo(() => paceRefusal(blotterRows), [blotterRows]);
  // Is there anything on the blotter that re-seeding the tape would take with
  // it? A closed trade counts: the session's record is the point of the page,
  // and "net" above is read off it.
  // Only ever about the *paper* blotter: re-seeding the tape renumbers tick
  // indices, and those are what a paper order's `idx` is. A broker order does
  // not have one, so a real account's working list is no reason to lock this.
  const blotterBusy = trades.length > 0 || openPos != null;
  // Pin the context while it is. Set on the way in rather than read on the way
  // out, so what gets held is the context the blotter's orders were indexed
  // against — recomputing it here would defeat the whole point. Cleared as soon
  // as there is nothing left to lose, at which point the bar's own answer takes
  // over and the tape re-seeds once.
  useEffect(() => {
    setPinned(blotterBusy ? (f) => f ?? wantedRef.current : null);
  }, [blotterBusy]);
  // Time left in the bar now forming, for the top-bar clock to carry. Time bars
  // only — a tick bar closes on a count the wall clock knows nothing about.
  // Anchored at the bell like the engine's own boundaries.
  const countdownMs =
    ready && tf.kind === "time" && header
      ? tf.ms - ((((hud.clockMs - header.rth_open_ms) % tf.ms) + tf.ms) % tf.ms)
      : null;

  if (!status?.running) {
    // Still deciding, or connecting: the autostart owns the screen until it is
    // done, so a visit that is about to become a live session does not flash the
    // startup form on its way there.
    if (autoPhase !== "manual") {
      return <Connecting symbol={autoSymbol} onManual={() => setAutoPhase("manual")} />;
    }
    return <NoSession onStarted={() => void statusQ.refetch()} autoError={autoErr} />;
  }

  return (
    // `.sim-page` gets a definite height from CSS (`100dvh` — there is no chrome
    // above it left to subtract, so nothing is measured in JS any more). That
    // height is load-bearing: without it the page is content-sized, `.sim-body`'s
    // `flex: 1` has no height to take a share of, and the chart collapses to
    // whatever the signal rail happens to be tall — growing as the rail fills,
    // which is not a chart, it's a symptom.
    <div
      className={`sim-page${railPinned ? " pinned" : ""}`}
      style={{ "--chart-floor": `${floor}px` } as React.CSSProperties}
    >
      <ChartTopBar
        title={
          status.symbol
            ? `${status.symbol} · ${status.closed ? status.date ?? "closed" : "live"}`
            : "No feed"
        }
        onTitle={() => setSetupOpen((o) => !o)}
        titleOpen={setupOpen}
        right={
          <>
            {/* The contract rolled and this feed did not. First in the row and
                loud, because every other number on this page is measured off a
                tape that is no longer the market's — and unlike a stale price,
                a stale *contract* reads as perfectly normal. */}
            {staleContract && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: palette.red,
                  border: `1px solid ${palette.red}`,
                  borderRadius: 4,
                  padding: "1px 6px",
                  whiteSpace: "nowrap",
                }}
                title={
                  `This tape is ${staleContract.symbol}, but ${staleContract.front_month} ` +
                  "is the contract this root is trading in now — the volume rolled " +
                  "eight days before expiry and the feed's subscription was made at " +
                  "connect, so it cannot follow.\n\n" +
                  "Orders resolve the front month independently, so they may already " +
                  "be going somewhere this chart cannot see. Reconnect the feed to " +
                  `${staleContract.front_month} before reading another level off it.`
                }
              >
                ⚠ {staleContract.symbol} rolled → {staleContract.front_month}
              </span>
            )}
            {/* Which account the chart's gestures go to, in the one place that
                is always on screen. The routing panel can be closed, pinned or
                unmounted; "am I about to trade real money" must not depend on
                which of those it happens to be. */}
            {status.routing && brokerState && (
              <AccountChip broker={brokerState} routes={intent.routes} />
            )}
            {/* And whether the rules are on, in the same always-on-screen
                place and for the same reason. The routing panel can be closed
                or unmounted; "is the daily stop being enforced" must not
                depend on which. */}
            {status.routing && brokerState && !brokerState.paper && (
              <GuardChip guard={brokerState.guard} />
            )}
            {/* And how fast the entries are coming, in the same always-on-screen
                place and for the same reason — `GuardMeters` says it too, but
                that lives in the routing panel, which can be closed. Not gated
                on `paper`: this is about the habit, and a paper entry is one. */}
            {pace && <PaceChip reason={pace} />}
            <span className="sim-topbar-num" title="Session clock">
              {fmtClock(hud.clockMs)}
              {countdownMs != null && (
                <span
                  className="sim-countdown"
                  title={`This ${tf.label} bar closes in ${fmtCountdown(countdownMs)}`}
                >
                  −{fmtCountdown(countdownMs)}
                </span>
              )}
            </span>
            <span className="sim-topbar-num" title="Last print">
              {Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}
            </span>
            {/* Which gamma regime the day is in, beside the price it is read
                against. Draws nothing at all when no book is banked, when the
                collector missed the day badly enough to be useless, or when
                price is off the book's ±6% grid — a gamma badge that guesses is
                worse than no gamma badge. */}
            {gexQ.data && <GexChip gex={gexQ.data} price={hud.lastPrice} />}
          </>
        }
      >
        <TimeframeControl
          // The focused pane's bucketing — the same arrangement as Replay's, so
          // one control drives however many charts are up.
          value={focusedPane === 0 ? tfId : paneTfIds[focusedPane]}
          onChange={(id) =>
            focusedPane === 0
              ? setTfId(id)
              : setPaneTfIds((prev) => prev.map((t, j) => (j === focusedPane ? id : t)))
          }
          options={tfOptions}
          // The tick bar (unique to a tape-driven chart), the default, and the
          // two the research vocabulary is written in. 30s/2m/3m/1h go behind ⋯.
          primary={["500t", "1m", "5m", "15m"]}
          compact
          // Same as Replay's: the live tape is bucketed here too, so a typed
          // bucketing is drawable and the list they share holds it.
          custom
        />
        {/* The community indicator catalogue, in the same place the replay keeps
            it — one bar, one picker, whichever clock you are on. */}
        <StudyPicker
          layers={paneLayers[focusedPane] ?? EMPTY_LAYERS}
          onLayer={(key, on) => paneChart(focusedPane)?.setLayer(key, on)}
          specs={studies[focusedPane] ?? EMPTY_SPECS}
          onSpecs={(next) => setPaneStudies(focusedPane, next)}
          paneLabel={paneCount > 1 ? String(focusedPane + 1) : undefined}
        />
        {paneCount > 1 && (
          <span className="chart-focus-note" title="Point at a pane to act on it">
            pane <b>{focusedPane + 1}</b>
          </span>
        )}
        <LayoutPicker value={layout} onChange={setLayout} />
        {paneCount > 1 && (
          <button
            type="button"
            className={`chart-topbar-btn link${linkOn ? " on" : ""}`}
            onClick={() => setLinkOn((v) => !v)}
            aria-pressed={linkOn}
            title={
              linkOn
                ? "Linked — the panes share one crosshair, and scrolling one moves the right edge of all of them. Each keeps its own span."
                : "Unlinked — each pane scrolls on its own"
            }
          >
            ⇄
          </button>
        )}
      </ChartTopBar>

      {setupOpen && (
        <button
          type="button"
          className="sim-setup-backdrop"
          aria-label="Close ticket settings"
          onClick={() => setSetupOpen(false)}
        />
      )}

      {/* Ticket sizing — the pre-trade configuration, behind the title exactly
          as the Simulator's session setup is. Everything you touch while
          watching (timeframe, clock, mark) is up in the bar; everything you set
          once is in here. */}
      <div className={`sim-setup${setupOpen ? " open" : ""}`}>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Size
          <input
            type="number"
            min={1}
            value={size}
            onChange={(e) => setTicketField("size", Math.max(1, Number(e.target.value) || 1))}
            style={{ width: 72 }}
          />
        </label>
        {/* The two distances, in ticks or in the money they are worth — the
            button on the end of each box says which, and pinned to money the
            distance follows the size and the routed contract (lib/bracketUsd).
            The caption carries whichever unit the box isn't in. */}
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Stop <i style={{ fontSize: 10 }}>{legEcho(stopTicks, ticket.stopUsd, orderTickUsd, size)}</i>
          <LegAmount
            ticks={stopTicks}
            pin={ticket.stopUsd}
            tickUsd={orderTickUsd}
            size={size}
            onTicks={(t) => setTicketField("stopTicks", t)}
            onPin={(usd) => setTicketField("stopUsd", usd)}
            style={{ width: 96 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Target{" "}
          <i style={{ fontSize: 10 }}>{legEcho(targetTicks, ticket.targetUsd, orderTickUsd, size)}</i>
          <LegAmount
            ticks={targetTicks}
            pin={ticket.targetUsd}
            tickUsd={orderTickUsd}
            size={size}
            onTicks={(t) => setTicketField("targetTicks", t)}
            onPin={(usd) => setTicketField("targetUsd", usd)}
            style={{ width: 96 }}
          />
        </label>
        {/* Who moves the stop. The two blocks below are mutually exclusive at
            the wire — a managed Rithmic stop is re-derived absolutely on every
            new extreme, so a ladder running against it would spend the trade
            being overwritten — and the server refuses the pair rather than
            picking one. Both sets of numbers stay in the ticket either way:
            switching source should not throw away settings.

            Shown on paper too, because the ladder is the one of the two paper
            can actually run. */}
        {stopTicks > 0 && (
          <label
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 12,
              // Loud when the ticket is asking for a ladder that cannot run.
              // The send would refuse with this same reason; saying it here is
              // the difference between a setting and a surprise.
              color:
                ladderBlocked && trailSource === "ladder" ? palette.red : palette.muted,
            }}
            title={
              ladderBlocked
                ? `Orders are routed to ${brokerState?.symbol} but the tape is ` +
                  `${brokerState?.feed_symbol}. The ladder measures the high off ` +
                  `the tape this app can see, so it cannot trail a position in ` +
                  `another contract — Rithmic's own trail rides its own feed and can.`
                : "Rithmic's trail survives this app closing and cannot be dragged. " +
                  "The ladder is the Simulator's own rule — a grid, a breakeven " +
                  "rung, and a stop you can still drag — run here off the live " +
                  "tape, and it stops ratcheting if this app does."
            }
          >
            Trail by
            <select
              value={trailSource}
              onChange={(e) => setTicketField("trailSource", e.target.value as TrailSource)}
              style={{ width: 110 }}
            >
              <option value="rithmic">Rithmic</option>
              <option value="ladder" disabled={ladderBlocked}>
                Ladder
              </option>
            </select>
          </label>
        )}
        {/* Real accounts only, and shown only there — on paper it would be a
            control with no effect, since Rithmic is not holding a paper
            position. One number because Rithmic's trail has one free variable:
            it rides at the stop above, so the choice is when it wakes up, not
            how far back it sits. */}
        {trailSource === "rithmic" && status.routing && brokerState && !brokerState.paper && (
          <label
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 12,
              color: trailTriggerTicks && stopTicks ? palette.orange : palette.muted,
              opacity: stopTicks ? 1 : 0.5,
            }}
            title={
              stopTicks
                ? `Rithmic ratchets the stop up behind the high once the trade is ` +
                  `this far in profit, riding ${stopTicks} ticks back. Rithmic moves ` +
                  `it, not this app — so it keeps working through a reload or ` +
                  `this page being closed. 0 is off.`
                : "Needs a stop: the trail rides at the stop's own distance behind the high."
            }
          >
            Trail after (t)
            <input
              type="number"
              min={0}
              disabled={!stopTicks}
              value={trailTriggerTicks}
              onChange={(e) =>
                setTicketField("trailTriggerTicks", Math.max(0, Number(e.target.value) || 0))
              }
              style={{ width: 72 }}
            />
          </label>
        )}
        {/* The breakeven jump. A separate mechanism from the trail, not a mode
            of it: this fires once and stops, the trail keeps going. They can be
            armed together — though that combination has not been measured
            against Rithmic, only each alone. */}
        {trailSource === "rithmic" && status.routing && brokerState && !brokerState.paper && (
          <label
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 12,
              color: beTicks && stopTicks ? palette.orange : palette.muted,
              opacity: stopTicks ? 1 : 0.5,
            }}
            title={
              stopTicks
                ? "Once the trade is this far in profit, Rithmic jumps the stop " +
                  "to lock the amount beside it in. Fires once. 0 is off."
                : "Needs a stop: there is no leg to jump without one."
            }
          >
            Breakeven after (t)
            <input
              type="number"
              min={0}
              disabled={!stopTicks}
              value={beTicks}
              onChange={(e) => setTicketField("beTicks", Math.max(0, Number(e.target.value) || 0))}
              style={{ width: 72 }}
            />
          </label>
        )}
        {/* Revealed only once breakeven is on — a lock with no trigger never
            fires, and the server says so rather than sending it. Minimum 1:
            "exactly at the fill" is a proto3 zero and never reaches Rithmic. */}
        {trailSource === "rithmic" && status.routing && brokerState && !brokerState.paper
          && beTicks > 0 && stopTicks > 0 && (
          <label
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 12,
              color: palette.orange,
            }}
            title={
              "How much profit that jump locks in, always in your favour on " +
              "either side. At least 1 tick: Rithmic cannot be told 'exactly at " +
              "the fill' — a zero is a protobuf default and never reaches it. " +
              "A 1-tick lock still owes the round turn."
            }
          >
            …locking (t)
            <input
              type="number"
              min={1}
              value={beLock}
              onChange={(e) => setTicketField("beLock", Math.max(1, Number(e.target.value) || 1))}
              style={{ width: 72 }}
            />
          </label>
        )}
        {/* The ladder — the same four knobs the Simulator trails by and the
            backtest engine trades, which is the whole reason it is here: what
            you practise and what you trade were two different rules until now.
            Shown on paper as well as on a real account, because the rule is the
            same on both — the blotter runs it locally, the API runs the Python
            port, and a fixture holds the two together. */}
        {trailSource === "ladder" && stopTicks > 0 && (
          <>
            <label
              style={{
                display: "flex",
                flexDirection: "column",
                fontSize: 12,
                color: ladderTicks ? palette.orange : palette.muted,
              }}
              title={
                "How far behind the best price the stop rides. A real distance, " +
                "unlike Rithmic's trail — it does not have to match the stop " +
                "above. 0 is off, and it is the master switch for the three " +
                "below. THIS APP moves the stop: if it stops running, the stop " +
                "stays where it last got to."
              }
            >
              Ladder (t)
              <input
                type="number"
                min={0}
                value={ladderTicks}
                onChange={(e) =>
                  setTicketField("ladderTicks", Math.max(0, Number(e.target.value) || 0))
                }
                style={{ width: 72 }}
              />
            </label>
            {ladderTicks > 0 && (
              <label
                style={{
                  display: "flex",
                  flexDirection: "column",
                  fontSize: 12,
                  color: palette.orange,
                  opacity: ladderBeOnly ? 0.5 : 1,
                }}
                title={
                  "The grid the stop is allowed to rest on. 0 means one rung per " +
                  "ladder width. A step wider than the ladder is refused: the " +
                  "stop would never reach a second rung."
                }
              >
                …step (t)
                <input
                  type="number"
                  min={0}
                  max={ladderTicks}
                  disabled={ladderBeOnly}
                  value={ladderStepTicks}
                  onChange={(e) =>
                    setTicketField("ladderStepTicks", Math.max(0, Number(e.target.value) || 0))
                  }
                  style={{ width: 72 }}
                />
              </label>
            )}
            {ladderTicks > 0 && (
              <label
                style={{
                  display: "flex",
                  flexDirection: "column",
                  fontSize: 12,
                  color: palette.orange,
                }}
                title={
                  "How far past the fill the first rung lands. Zero is breakeven " +
                  "gross — the round turn still owes commission, so a few ticks " +
                  "here is what makes a scratch really a scratch."
                }
              >
                …from (t)
                <input
                  type="number"
                  min={0}
                  max={Math.max(0, ladderTicks - 1)}
                  value={ladderBeTicks}
                  onChange={(e) =>
                    setTicketField("ladderBeTicks", Math.max(0, Number(e.target.value) || 0))
                  }
                  style={{ width: 72 }}
                />
              </label>
            )}
            {ladderTicks > 0 && (
              <label
                style={{
                  display: "flex",
                  gap: 6,
                  alignItems: "center",
                  fontSize: 12,
                  color: ladderBeOnly ? palette.orange : palette.muted,
                }}
                title={
                  "Take the first rung and no other — a breakeven stop rather " +
                  "than a trail. Its own rule, not a mode: a trail hands back " +
                  "open profit on every pullback, which is exactly what this " +
                  "refuses to do."
                }
              >
                <input
                  type="checkbox"
                  checked={ladderBeOnly}
                  onChange={(e) => setTicketField("ladderBeOnly", e.target.checked)}
                />
                First rung only
              </label>
            )}
          </>
        )}
        {/* The shapes the dock's A–D chip offers, open here beside the
            fields they fill — this drawer is the "set once" surface, which is
            exactly what a preset is. Applying one throws `trailSource` to the
            ladder (see `applyPreset`), so it lands on the block above rather
            than on Rithmic's, whichever was showing. */}
        {presetCtx && (
          <div className="sim-preset-block">
            <TicketCard
              preset={presetCtx}
              size={size}
              tickUsd={intent.real ? routedTick * routedPoint : tickSize * pointValue}
              onApply={applyPreset}
              active={activeBracket}
              sizer={sizerCtx}
              onApplySizing={applySizing}
            />
          </div>
        )}
        {/* The days behind this one. Here rather than in the bar because it is
            set once and because it costs something — each day is a whole tape,
            and changing it restarts the session's own tape from row zero (the
            context has to be seeded in front, so it cannot be added to a tape
            already growing).
            The tape itself is re-read from /live/tape and loses nothing. The
            *blotter* is a different matter: `onReset` clears it, because every
            order it holds is a tick index into the tape that just stopped
            existing. Rather than discard paper trades as a side effect of a
            reading choice — which is exactly the kind of quiet wrongness this
            surface exists not to do — the control locks once there is anything
            to lose. Choose the context before you start, or flatten and clear. */}
        {/* Per bar, which is why it carries the bar's name under it: the number
            you see belongs to `governingHist.tf`, not to the page. ↺ puts that
            bar back on the rule. While the blotter is busy the whole control is
            read-only and shows what is actually seeded — which, if you have
            since changed the bar, is not what the bar is asking for. */}
        <label
          style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}
          title={
            blotterBusy
              ? "Locked while the blotter has something in it: changing the context re-seeds the tape from row zero, and every order on it is a tick index into the tape that would stop existing."
              : `Draw this many prior sessions to the left of the live one. Real ticks, so they candle on any bar size and profile like the session does — but nothing develops over them, and they can't be traded.\n\nSet per bar size: this is the ${governingHist.tf.label}'s, and it opens on ${defaultHistoryDays(governingHist.tf)} because that is roughly what a ${governingHist.tf.label} needs to have a readable chart behind it.`
          }
        >
          Prior days
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <select
              value={historyDays}
              onChange={(e) =>
                setHistOverrides(
                  withHistoryOverride(histOverrides, governingHist.tf.id, Number(e.target.value)),
                )
              }
              disabled={blotterBusy}
              style={{ width: 96 }}
            >
              {HISTORY_DAY_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n === 0 ? "none" : `${n} day${n === 1 ? "" : "s"}`}
                </option>
              ))}
            </select>
            {!blotterBusy && histOverrides[governingHist.tf.id] != null && (
              <button
                type="button"
                onClick={() =>
                  setHistOverrides(withHistoryOverride(histOverrides, governingHist.tf.id, null))
                }
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
          {/* The mismatch, named. Without this a 1h chart sitting on one day of
              context looks like the rule failing, when it is the lock holding. */}
          <span style={{ fontSize: 11, opacity: 0.7, color: histFrozen ? palette.orange : undefined }}>
            {histFrozen
              ? `${governingHist.tf.label} wants ${wantedDays} — flatten and clear`
              : `${governingHist.tf.label}${
                  histOverrides[governingHist.tf.id] != null
                    ? ` · default ${defaultHistoryDays(governingHist.tf)}`
                    : ""
                }`}
          </span>
        </label>
        {historyDays > 0 && (
          <span style={{ alignSelf: "flex-end", paddingBottom: 6, fontSize: 12, color: palette.muted }}>
            {!historyReady
              ? "loading context…"
              : contextTapes.length === 0
                ? "no prior tape"
                : `${contextTapes.length} drawn`}
            {/* Both of these are absences worth naming rather than hiding. A day
                with nothing recorded and a day that failed to load look
                identical on the chart — a shorter chart — and the difference
                matters: one is a hole in the store, the other is a request to
                retry. */}
            {histDaysQ.data && histDaysQ.data.missing.length > 0 && (
              <span
                style={{ color: palette.orange }}
                title={`No tape in either store: ${histDaysQ.data.missing.join(", ")}`}
              >
                {" "}· {histDaysQ.data.missing.length} unrecorded
              </span>
            )}
            {histQ.failed.length > 0 && (
              <span style={{ color: palette.red }} title={`Not drawn: ${histQ.failed.join(", ")}`}>
                {" "}· {histQ.failed.length} unread
              </span>
            )}
          </span>
        )}
        <span style={{ alignSelf: "flex-end", paddingBottom: 6, fontSize: 12, color: palette.muted }}>
          {intent.real ? `${brokerState?.account_id} · ` : "paper · "}
          <TradeTally rows={blotterRows} /> · net {fmtUsd(net)}
          {intent.real
            ? brokerPos && brokerPos.net !== 0 && brokerPos.open_pnl != null
              ? ` · open ${fmtUsd(brokerPos.open_pnl)}`
              : ""
            : openPos
              ? ` · open ${fmtUsd(hud.openPnl)}`
              : ""}
        </span>
      </div>

      <div className="sim-body">
        <div className="sim-chart-card">
          {/* The rail and the pane grid, as on Replay: one tool rail outside every
              canvas, acting on the focused pane. */}
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
              // Trading pane only — see the Simulator's.
              levelPanel
              routedTo={routedSymbol}
              symbol={header?.symbol ?? status.symbol ?? undefined}
              tapeContract={header?.symbol ?? status.symbol ?? undefined}
              tfLabel={tf.label}
              tfOptions={tfOptions}
              onTfChange={setTfId}
              onAnchorChange={setAnchor}
              onBracketChange={moveBracket}
              onFlatten={closeAll}
              onOrderMove={(o) =>
                editOrder(o.id, { price: o.price, stop: o.stop, target: o.target })
              }
              onOrderCancel={cancelOrder}
              onPlaceOrder={placeAt}
              onPlaceTyped={(o) => placeResting(o.price, o.side, o.type, "ticket")}
              ticket={ticket}
              onTicketChange={changeTicket}
              mark={hud.lastPrice}
              // Only the main chart feeds the sizer — the extra panes draw the
              // same tape on other timeframes.
              onVolRuler={setVolRead}
              // The ticket prices its SL/TP boxes in money, so it needs the
              // contract the order would actually go to — the routed one while
              // this is going to the broker, the tape's while it is paper.
              pointValue={intent.real ? routedPoint : pointValue}
              canPlaceOrders={ready}
              secondsAxis={showsSeconds(tf)}
              bigLots={bigLots}
              composite={composite}
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
              drawingsKey={header ? `${header.symbol}|${header.date}` : undefined}
            />
            {/* The same day-scale strip the replay carries. It was written for a
                tape that grows print by print, which is what a live session is —
                the IB reads "forming" until the window closes and the range
                budget says "budget at IB close" until there is a denominator, so
                a partial day degrades honestly rather than guessing. The context
                it needs (ADR(14), the tercile edges) already comes down on
                /live/session; nothing here read it before. */}
            <SimIndicators
              context={header?.context}
              ib={hud.ib}
              range={hud.range}
              open={indicators}
              onToggle={() => setIndicators((v) => !v)}
            />
            {/* A receipt for a one-click order, or the reason one failed. Over
                the chart because that is where you were looking when you sent
                it — a fill you have to go and find in a panel is a fill you
                assume happened. */}
            <OrderFlash flash={intent.flash} error={intent.error} onDismiss={intent.clearError} />
            {/* Market orders under the thumb, as on the replay. On paper both
                sides quote the last print and fill off the same tape the chart
                draws; on a real account the same two buttons go to the exchange,
                which is what the colour change says. The same small window too,
                dragged anywhere on the chart and remembered there — one saved
                spot across both clocks. */}
            <QuickDock onFloorChange={setFloor}>
              {/* Size and the bracket on the window that fires them, priced in
                  the contract the order would actually reach — see TicketKnobs.
                  On a real account that is the routed one, which is the whole
                  reason the money is worth printing here: an MNQ stop read at
                  NQ's $20 a point is ten times the risk that is on. */}
              <TicketKnobs
                ticket={ticket}
                onChange={changeTicket}
                tickUsd={orderTickUsd}
                sizer={sizerCtx}
                onApplySizing={applySizing}
                preset={presetCtx ?? undefined}
                onApplyPreset={applyPreset}
                activeBracket={activeBracket}
              />
              {openPos && (
                <button type="button" className="sim-quick-btn flat" onClick={closeAll} title="Flatten (q)">
                  Close
                </button>
              )}
              {(["short", "long"] as const).map((side) => (
                <button
                  key={side}
                  type="button"
                  className={`sim-quick-btn ${side === "long" ? "buy" : "sell"}`}
                  onClick={() => placeMarket(side, "dock")}
                  disabled={!ready}
                  // The outline is the whole tell. These two buttons mean
                  // different things on different accounts and look otherwise
                  // identical, so the one state worth drawing is "this reaches
                  // an exchange" — and on a live account, in red.
                  style={
                    intent.routes
                      ? {
                          outline: `2px solid ${brokerState?.kind === "live" ? palette.red : palette.orange}`,
                          outlineOffset: -2,
                        }
                      : undefined
                  }
                  title={
                    intent.routes
                      ? `${side === "long" ? "Buy" : "Sell"} at market on ${brokerState?.account_id}` +
                        (brokerState?.one_click ? " — one-click, no confirm" : " — with confirm")
                      : `${side === "long" ? "Buy" : "Sell"} at market (${side === "long" ? "w" : "s"}) — paper`
                  }
                >
                  <span>{side === "long" ? "BUY" : "SELL"}</span>
                  <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
                </button>
              ))}
            </QuickDock>
            </div>
            {/* The dividers, read off the layout — see lib/paneLayout. */}
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
                title="Drag to resize"
              />
            ))}
            {/* The extra panes. Each is its own engine on its own bucketing over
                the *same growing tape*, each draws the broker's position, its
                working orders and its fills, and each takes the same order
                gestures pane 0 does — the callbacks are handed over unchanged,
                because every one of them names a price and nothing else. The ⚓
                is the exception: it draws on the chart you dropped it on. */}
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
                    routedTo={routedSymbol}
                    symbol={header?.symbol ?? status.symbol ?? undefined}
                    tapeContract={header?.symbol ?? status.symbol ?? undefined}
                    tfLabel={paneTfsRef.current[i].label}
                    tfOptions={tfOptions}
                    onTfChange={(id) =>
                      setPaneTfIds((prev) => prev.map((t, j) => (j === i ? id : t)))
                    }
                    onAnchorChange={(t) => setPaneAnchor(i, t)}
                    onBracketChange={moveBracket}
                    onFlatten={closeAll}
                    onOrderMove={(o) =>
                      editOrder(o.id, { price: o.price, stop: o.stop, target: o.target })
                    }
                    onOrderCancel={cancelOrder}
                    onPlaceOrder={placeAt}
                    onPlaceTyped={(o) => placeResting(o.price, o.side, o.type, "ticket")}
                    ticket={ticket}
                    onTicketChange={changeTicket}
                    mark={hud.lastPrice}
                    pointValue={intent.real ? routedPoint : pointValue}
                    canPlaceOrders={ready}
                    secondsAxis={showsSeconds(paneTfsRef.current[i])}
                    bigLots={bigLots}
                    composite={composite}
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
                    drawingsKey={header ? `${header.symbol}|${header.date}` : undefined}
                    prefsPane={`p${i}`}
                    onReady={() => primePane(i)}
                  />
                </div>
              );
            })}
          </div>
          </div>
        </div>

        {/* The same rail the replay carries, doing the same job: open the panel,
            and pin it to a column when you want the width spent on it. */}
        <div className="sim-rail">
          {/* What has been traded today, the card the replay carries. Unlike
              the two below it this is not mounted-while-open — the trades are
              already in hand off the routing poll (or, on paper, out of the
              simulation), so there is nothing behind it that would run. */}
          <button
            type="button"
            className={`sim-rail-btn${railView === "blotter" ? " on" : ""}`}
            onClick={() => setRailView((v) => (v === "blotter" ? null : "blotter"))}
            aria-pressed={railView === "blotter"}
            title="Blotter — every closed round trip today, what it staked and what it paid"
          >
            ▥
          </button>
          <button
            type="button"
            className={`sim-rail-btn${signalsOpen ? " on" : ""}`}
            onClick={() => setRailView((v) => (v === "signals" ? null : "signals"))}
            aria-pressed={signalsOpen}
            title={signalsOpen ? "Hide the shadow signals" : "Show the shadow signals"}
          >
            ▤
          </button>
          <button
            type="button"
            className={`sim-rail-btn${railView === "coverage" ? " on" : ""}`}
            onClick={() => setRailView((v) => (v === "coverage" ? null : "coverage"))}
            aria-pressed={railView === "coverage"}
            title="Tape coverage — what is recorded, and how long the gaps stay fillable"
          >
            ▦
          </button>
          {/* Only on a session that opened the ORDER plant. Hidden rather than
              disabled, and this is the one place in the suite where hiding is
              right: a permanently greyed order button on a shadow session is an
              invitation to look for the way to enable it, and there isn't one —
              routing is decided at connect. The panel itself explains that when
              you reach it from a session that has it. */}
          {status.routing && (
            <button
              type="button"
              className={`sim-rail-btn${railView === "routing" ? " on" : ""}`}
              onClick={() => setRailView((v) => (v === "routing" ? null : "routing"))}
              aria-pressed={railView === "routing"}
              title="Order entry — the only thing on this page that can reach an exchange"
            >
              ⌁
            </button>
          )}
          {railView && (
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
          {working.length > 0 && (
            <span className="sim-rail-badge" title={`${working.length} working`}>
              {working.length}
            </span>
          )}
        </div>
        {/* `.sim-panel` is the box; which of the two it holds is `railView`. The
            coverage read is mounted only while it is showing — it is a directory
            walk on the server, and there is no reason for it to run behind a
            panel nobody has opened. */}
        {railView === "blotter" ? (
          <div className="sim-panel open">
            <Blotter
              rows={blotterRows}
              total={net}
              head={
                <span className="r">
                  <TradeTally rows={blotterRows} />
                  {intent.real ? ` · ${brokerState?.account_id}` : " · paper"}
                </span>
              }
              empty={
                intent.real
                  ? "Nothing closed on this account today."
                  : "No paper trades yet."
              }
            />
          </div>
        ) : railView === "coverage" ? (
          <div className="sim-panel open">
            <div className="panel" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <TapeCoverage symbol={status.symbol} compact />
            </div>
          </div>
        ) : railView === "routing" ? (
          // Mounted only while it is showing, like the coverage read — it polls
          // the broker, and there is no reason for that to run behind a panel
          // nobody has opened.
          <div className="sim-panel open">
            {/* The page's ticket, not a second one. The pad in there sends
                through the same endpoints the chart gestures do, so a bracket
                set in either place has to be the bracket both of them send —
                see `lib/simPrefs`, DEFAULT_LIVE_TICKET. */}
            <RoutingPanel
              mark={hud.lastPrice}
              tickSize={tickSize}
              ticket={ticket}
              onTicket={setTicketField}
              preset={presetCtx}
              onPreset={applyPreset}
              pace={pace}
            />
          </div>
        ) : (
          <SignalPanel data={signalsQ.data} working={working.length} open={signalsOpen} />
        )}
      </div>

      {/* The status strip is a footer: it is state you glance at, not something
          you act on continuously, and above the chart it was pushing the tape
          down by a row you were not reading. */}
      {/* The confirm. Rendered at the page root rather than inside the chart
          card so nothing can clip it, and only while there is something to
          answer — `useOrderIntent` owns Enter and Esc for exactly that long. */}
      {intent.pending && (
        <OrderConfirm
          pending={intent.pending}
          kind={brokerState?.kind ?? null}
          busy={intent.busy}
          onConfirm={intent.confirm}
          onCancel={intent.cancel}
        />
      )}

      <FeedBanner
        source={status.source ?? "fake"}
        symbol={status.symbol ?? "—"}
        date={status.date ?? "—"}
        speed={status.speed ?? 1}
        feedRunning={!!status.feed_running}
        closed={!!status.closed}
        rows={tapeState.rows || (status.rows ?? 0)}
        recording={!!status.recording}
        signals={status.signals !== false}
        canRecord={status.can_record !== false}
        unrecorded={status.unrecorded_rows ?? 0}
        onModes={async (m) => {
          await setLiveModes(m);
          void statusQ.refetch();
          void signalsQ.refetch();
        }}
        backfilling={!!status.feed_status?.backfilling}
        timing={status.feed_status?.timing}
        backfills={status.feed_status?.backfills}
        harvested={status.feed_status?.harvested}
        feedError={status.feed_status?.error}
        error={tapeState.error}
        onStop={async () => {
          await stopFeed();
          void statusQ.refetch();
        }}
      />
    </div>
  );
}

/**
 * Where the chart's gestures land, always on screen.
 *
 * Three states worth telling apart, and the middle one is why this is not just
 * a name: an account can be *selected* without gestures reaching it, because
 * nobody has labelled it demo or live yet or the broker has not been read back.
 * In that window q/w/s refuses rather than filling paper, and reading the chip
 * as "I'm on the live account" would have somebody expect an order that never
 * went.
 */
function AccountChip({ broker, routes }: { broker: BrokerState; routes: boolean }) {
  const paper = broker.paper;
  const live = broker.kind === "live";
  const color = paper
    ? palette.blue
    : !routes
      ? palette.muted
      : live
        ? palette.red
        : palette.orange;
  return (
    <span
      title={
        paper
          ? "Paper. Every order gesture fills the blotter and nothing reaches a broker."
          : routes
            ? `Live on ${broker.account_id} (${broker.kind}). q/w/s, the dock and space+click send real orders` +
              (broker.one_click ? ", with no confirmation." : ", each behind a confirm.")
            : `${broker.account_id} is selected but cannot send yet — label it, or wait for the broker to be read back.`
      }
      style={{
        fontSize: 10,
        letterSpacing: 0.4,
        padding: "1px 6px",
        borderRadius: 10,
        border: `1px solid ${color}`,
        color,
        whiteSpace: "nowrap",
      }}
    >
      {paper ? "📝 PAPER" : `${broker.account_id}${routes ? " ●" : " ○"}`}
      {/* The contract, but only when it is not the one on screen. "Am I about
          to trade real money" acquired a second half the moment routing could
          be pointed somewhere else, and this chip is the one thing on the page
          that is always visible — the routing panel can be closed. Silent while
          they agree, so the common case stays uncluttered. */}
      {!paper && broker.symbol !== broker.feed_symbol && (
        <b style={{ marginLeft: 4 }}> {broker.symbol}</b>
      )}
    </span>
  );
}

/**
 * Are the rules on, and what is the day doing against them.
 *
 * Four states and the first is the one this chip exists for: **off**. Turning
 * the guardrails off is a `.env` edit and a restart, so it is easy to have done
 * last week and be trading on today — and a safety layer that is silently off is
 * worse than one that was never built, because it gets traded as though it were
 * there. So "off" is red, permanent, and sits beside the account name rather
 * than inside a panel that can be closed.
 *
 * The other three are the day: locked (red), slowed (orange), and running
 * (muted, with the number). The number is the total the rules are actually
 * enforced on, which is not always the broker's own day P&L — the panel shows
 * both and flags the gap.
 */
/**
 * Dealer gamma regime, read against the last print.
 *
 * Deliberately a regime and not a level. The Black-Scholes recompute behind this
 * lands 4.6% (QQQ) / 8.8% (NDX) off Cboe's own greeks, and the two books disagree
 * by ~0.5% on where the flip sits — fine for "which side of the flip are we on",
 * not fine for trading the walls. So the chip shows the side and the distance,
 * and the walls stay in the tooltip where they read as context.
 *
 * NOTHING HERE IS VALIDATED. No historical GEX exists in this repo, so no A/B has
 * been run and none can be until the collector has banked ~6 months. The tooltip
 * says so, because a number on a trading screen that looks like every other
 * number on that screen will be read as one that earned its place.
 */
function GexChip({ gex, price }: { gex: GexRegime; price: number }) {
  if (!gex.available || !Number.isFinite(price)) return null;
  const g = gexAt(gex.book.curve, price, gex.px_ref);
  const flip = gex.flip_px;
  const stale = gex.stale_days;

  // Off the ±6% grid, or the anchor is unusable. Say nothing rather than guess.
  if (g == null) return null;

  const long = g > 0;
  const c = stale > 3 ? palette.muted : long ? palette.green : palette.orange;
  const dist = flip ? (price / flip - 1) * 100 : null;

  const lines = [
    `Dealer gamma: ${long ? "LONG (dampening — moves get sold into, ranges hold)" : "SHORT (amplifying — moves get chased, trends extend)"}.`,
    `Net GEX ${g >= 0 ? "+" : ""}${g.toFixed(2)}B at ${fmtPts(price)}, from the ${gex.book.sym} book of ${gex.book.book_date}.`,
    flip ? `Gamma flip ~${fmtPts(flip)} (${dist! >= 0 ? "+" : ""}${dist!.toFixed(2)}% away) — below it the regime inverts.` : "No flip inside the ±6% grid.",
    gex.call_wall_px && gex.put_wall_px
      ? `Call wall ~${fmtPts(gex.call_wall_px)}, put wall ~${fmtPts(gex.put_wall_px)}. Context only — do NOT trade these levels; the recompute is ~5-9% off the feed's own greeks and level-bounce geometry is nulled four times over here.`
      : "",
    `Mapped from ${gex.book.sym} via prior closes (${gex.px_ref_date}, ${gex.px_ref_source} store): NQ ${fmtPts(gex.px_ref)} vs ${gex.book.sym} ${gex.book.ref.toLocaleString()}, implied carry ${gex.implied_basis >= 0 ? "+" : ""}${gex.implied_basis.toFixed(0)}pt (${gex.implied_basis_pct >= 0 ? "+" : ""}${gex.implied_basis_pct.toFixed(2)}%).`,
    stale > 0
      ? `⚠ The book is ${stale} session${stale === 1 ? "" : "s"} old — the collector did not run for this date. This is last-known positioning, not today's.`
      : "",
    !gex.book.is_pre_open
      ? "⚠ Book was banked after the cash open, so its reference is a moving quote rather than a close and the price mapping is approximate."
      : "",
    "UNVALIDATED — no historical GEX here, so this has never been A/B'd against anything. Context read, not a signal.",
  ].filter(Boolean);

  return (
    <span
      title={lines.join("\n\n")}
      style={{
        fontSize: 10,
        letterSpacing: 0.4,
        padding: "1px 6px",
        borderRadius: 10,
        border: `1px solid ${c}`,
        color: c,
        whiteSpace: "nowrap",
      }}
    >
      {long ? "⊕" : "⊖"} {g >= 0 ? "+" : ""}
      {g.toFixed(1)}B
      {dist != null && (
        <span style={{ opacity: 0.7 }}>
          {" "}
          flip {dist >= 0 ? "+" : ""}
          {dist.toFixed(1)}%
        </span>
      )}
      {stale > 0 && <span style={{ opacity: 0.7 }}> · {stale}d old</span>}
    </span>
  );
}

/** Equity, for the chip's tooltip. Blank while flat, where it is just the booked
 *  figure said twice. Named separately because it is no longer what any rule
 *  fires on — only what the account's own floor would be marked against. */
function openLine(guard: GuardState): string {
  if (!guard.open_pnl) return "";
  return ` Open ${fmtUsd(guard.open_pnl)}, so equity is ${fmtUsd(guard.equity)} — what the firm's floor marks, not what the stop reads.`;
}

function GuardChip({ guard }: { guard: GuardState }) {
  const lv = guard.levels;
  const spec = !guard.on
    ? {
        c: palette.red,
        t: "GUARDS OFF",
        title:
          "LIVE_GUARDRAILS is switched off in .env. The daily stop, the slow-down threshold, the minimum target and the stop-width clamp are NOT being enforced.",
      }
    : guard.locked
      ? {
          c: palette.red,
          t: "DAY OVER",
          title: `${guard.locked}. New entries are refused; closing orders and Flatten still work. It stays over even if the running total comes back.`,
        }
      : guard.slow
        ? {
            c: palette.orange,
            t: `SLOW ${fmtUsd(guard.realized)}`,
            title: `Past ${fmtUsd(-lv.slow_down_at)} booked down — entries go no closer than ${Math.round(lv.min_gap_s)}s apart. Daily stop at ${fmtUsd(-lv.daily_loss_stop)}.${openLine(guard)}`,
          }
        : {
            c: palette.muted,
            t: `🛡 ${fmtUsd(guard.realized)}`,
            // The figure the rules are actually enforced on, which since the
            // daily stop moved off equity is the booked one at every level.
            // Equity is still worth reading — it is what the *firm's* floor
            // marks — so it goes in the tooltip rather than on the chip.
            title: `Guarded. Booked today, net of commission — the figure the daily stop fires on. Slow-down at ${fmtUsd(-lv.slow_down_at)}, daily stop at ${fmtUsd(-lv.daily_loss_stop)}${lv.daily_profit_lock > 0 ? `, profit lock at ${fmtUsd(lv.daily_profit_lock)}` : ""}.${openLine(guard)}`,
          };
  return (
    <span
      title={spec.title}
      style={{
        fontSize: 10,
        letterSpacing: 0.4,
        padding: "1px 6px",
        borderRadius: 10,
        border: `1px solid ${spec.c}`,
        color: spec.c,
        whiteSpace: "nowrap",
      }}
    >
      {spec.t}
    </span>
  );
}

/**
 * Microseconds as something readable at a glance.
 *
 * Sub-millisecond keeps two decimals because that is the entire range the hop
 * lives in — 0.3ms rounded to "0ms" would say the leg is free, which is a
 * different claim from the one the measurement makes.
 */
function fmtUs(us: number): string {
  return us < 1000 ? `${(us / 1000).toFixed(2)}ms` : `${Math.round(us / 1000)}ms`;
}

/**
 * The banner that says what this surface is, so it can never be mistaken for
 * something it is not.
 *
 * Three states, and the third is why the source is read rather than assumed. A
 * **resumed** session has a whole tape and no feed behind it: the API restarted
 * mid-day and rebuilt what was recorded. It is not growing, and left labelled
 * "arriving" that reads as a quiet market rather than as a feed that needs
 * reconnecting.
 */
function FeedBanner(props: {
  source: string;
  symbol: string;
  date: string;
  speed: number;
  feedRunning: boolean;
  closed: boolean;
  rows: number;
  recording: boolean;
  signals: boolean;
  canRecord: boolean;
  unrecorded: number;
  onModes: (m: { record?: boolean; signals?: boolean }) => Promise<void>;
  backfilling?: boolean;
  timing?: Record<string, number>;
  backfills?: LiveBackfill[];
  harvested?: { date: string; skipped: boolean; rows: number; error?: string }[];
  feedError?: string | null;
  error: string | null;
  onStop: () => void;
}) {
  // Summed rather than listed: a long-running feed reconnects, and each
  // reconnect backfills the hole it left. What the banner owes the reader is
  // "how much of this tape was replayed rather than watched" and "did any of it
  // fail" — the per-range detail lives on /live/status.
  const filled = (props.backfills ?? []).reduce((a, b) => a + (b.rows || 0), 0);
  const backfillError = (props.backfills ?? []).find((b) => b.error)?.error;
  const caughtUp = (props.harvested ?? []).filter((h) => !h.skipped && h.rows).length;
  // Whichever legs have samples, rather than both or neither: a feed running on
  // rows that carry only Rithmic's stamp can say nothing about the hop, and
  // rendering a zero there would read as a perfect one.
  const t = props.timing ?? {};
  const timingParts: string[] = [];
  if (t.hop_p50_us !== undefined) timingParts.push(`hop ${fmtUs(t.hop_p50_us)}`);
  if (t.lag_p50_us !== undefined) timingParts.push(`lag ${fmtUs(t.lag_p50_us)}`);
  const live = props.source === "rithmic";
  const resumed = props.source === "resumed";
  const accent = live ? palette.green : palette.orange;
  const label = live ? "LIVE · RITHMIC" : resumed ? "RESUMED — NO FEED" : "SIMULATED FEED";
  return (
    <div
      className="live-status"
      style={{
        // The status colour, on the edge the strip now sits against.
        borderTop: `2px solid ${accent}`,
      }}
    >
      <strong style={{ letterSpacing: 0.4, color: accent }}>
        {label}
      </strong>
      <span >
        {props.symbol} · {props.date}
        {live ? "" : ` · ${props.speed}×`} · {props.rows.toLocaleString()} ticks
      </span>
      <span style={{ color: palette.muted }}>
        {props.backfilling
          ? // Said out loud because the tape is empty while this runs — a whole
            // session takes tens of seconds to replay, and a blank chart with a
            // green LIVE banner over it reads as a bug.
            "replaying the session so far…"
          : props.closed
            ? "session complete"
            : resumed
              ? "rebuilt from disk — reconnect to keep recording"
              : props.feedRunning
                ? "arriving"
                : "feed stopped"}
      </span>
      <ModeSwitches
        recording={props.recording}
        signals={props.signals}
        canRecord={props.canRecord}
        unrecorded={props.unrecorded}
        onModes={props.onModes}
      />
      {props.unrecorded > 0 && (
        // The hole, not the switch. Said in the banner rather than left to the
        // manifest because it is a property of the *day* now: the tape is in
        // memory and the chunks are what survives, so these prints are gone at
        // the next restart whatever happens next.
        <span style={{ color: palette.orange }}>
          ⚠ {props.unrecorded.toLocaleString()} tick
          {props.unrecorded === 1 ? "" : "s"} not written
        </span>
      )}
      {timingParts.length > 0 && (
        <span
          style={{ color: palette.muted, cursor: "help" }}
          title={
            "hop — the exchange's stamp to Rithmic's send stamp. Both ride in the " +
            "same message, so no local clock enters into it.\n\n" +
            "lag — arrival to publish inside the API, on the monotonic clock. It is " +
            "bounded below by the 20ms publish cadence, so a p50 near 10ms is the " +
            "cadence rather than a fault; a p90 far above 20ms means something (a " +
            "shadow pass, a sim sweep on the same cores) is holding the event loop.\n\n" +
            "There is no end-to-end figure, deliberately: it would need the host clock " +
            "against the exchange's, and this host measured a second off in a direction " +
            "that changes between runs — so the number would be reporting clock drift " +
            "while looking exactly like a feed measurement."
          }
        >
          {timingParts.join(" · ")}
        </span>
      )}
      {filled > 0 && (
        <span style={{ color: palette.muted }}>
          {filled.toLocaleString()} backfilled
        </span>
      )}
      {caughtUp > 0 && (
        // Earlier sessions, not this one — worth saying because it is what makes
        // the weekly anchor drawable after a week away, and it is otherwise
        // invisible work happening on a connection you opened to watch today.
        <span style={{ color: palette.muted }}>
          +{caughtUp} earlier session{caughtUp === 1 ? "" : "s"} filled
        </span>
      )}
      {backfillError && (
        <span style={{ color: palette.orange }}>
          ⚠ backfill: {backfillError} — the session begins where the feed
          connected
        </span>
      )}
      {props.feedError && (
        <span style={{ color: palette.red }}>⚠ feed: {props.feedError}</span>
      )}
      {props.error && (
        <span style={{ color: palette.red }}>⚠ {props.error}</span>
      )}
      <button style={{ marginLeft: "auto" }} onClick={props.onStop}>
        Stop
      </button>
    </div>
  );
}

/**
 * The two switches, and the one order they may be thrown in.
 *
 * They are separate because they fail differently. Recording off is an absence
 * you can see — the tape stops being written and the banner counts what was
 * lost. The shelf running with nothing recorded is not an absence at all: ten
 * `gx_*` gate sites and the weekly seed read the session's earlier windows off
 * *disk*, not from the frame the runner injects, so they blind-fail-closed and
 * seven of the thirteen strategies veto everything without a word. On screen
 * that is indistinguishable from a morning where no setup formed.
 *
 * So recording cannot be switched off underneath a running shelf. The API
 * refuses it (422) and this disables the control and says why — turning the two
 * off in one click would be the page deciding to stop the shelf because you
 * asked about the disk, which is a bigger action than the one requested.
 *
 * The simulated feed is the exception, and not one: it records nothing by
 * design, but the day it replays is a *cached* day, so the windows the gates
 * read are already on disk and the shelf can honestly run over it.
 */
function ModeSwitches(props: {
  recording: boolean;
  signals: boolean;
  canRecord: boolean;
  unrecorded: number;
  onModes: (m: { record?: boolean; signals?: boolean }) => Promise<void>;
}) {
  const [busy, setBusy] = useState<"record" | "signals" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const send = async (which: "record" | "signals", m: { record?: boolean; signals?: boolean }) => {
    setBusy(which);
    setErr(null);
    try {
      await props.onModes(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // Blocked rather than hidden: the reason is the interesting part, and a
  // control that vanished would read as "this feed cannot do that".
  const recordLocked = props.canRecord && props.recording && props.signals;

  return (
    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {props.canRecord ? (
        <ModeChip
          on={props.recording}
          label="recording"
          busy={busy === "record"}
          disabled={recordLocked}
          title={
            recordLocked
              ? "Switch the shadow shelf off first. Its Globex gates read this " +
                "session's earlier windows off disk — with nothing being written " +
                "they veto everything and say nothing about why, which looks " +
                "exactly like a morning with no setups in it."
              : props.recording
                ? "Writing every print to data/live/. Stopping leaves a permanent " +
                  "hole: the tape is in memory, the chunks are what survive."
                : props.unrecorded > 0
                  ? "Resume writing. The prints that arrived while this was off " +
                    "are not recoverable, and the day's manifest will say so."
                  : "Write every print to data/live/."
          }
          onClick={() => void send("record", { record: !props.recording })}
        />
      ) : (
        <span
          style={{ fontSize: 12, color: palette.muted, cursor: "help" }}
          title={
            "The simulated feed records nothing by design — its source is a cached " +
            "day, and recording it would manufacture a live day out of a replayed one."
          }
        >
          not recorded
        </span>
      )}
      <ModeChip
        on={props.signals}
        label="shadow shelf"
        busy={busy === "signals"}
        title={
          props.signals
            ? "Re-running every registered strategy over the day so far. Switching " +
              "off keeps what it last said on screen and stops the passes."
            : "Re-run the shelf over the day so far."
        }
        onClick={() => void send("signals", { signals: !props.signals })}
      />
      {err && <span style={{ fontSize: 12, color: palette.red }}>⚠ {err}</span>}
    </span>
  );
}

function ModeChip(props: {
  on: boolean;
  label: string;
  busy: boolean;
  disabled?: boolean;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={props.onClick}
      disabled={props.busy || props.disabled}
      title={props.title}
      style={{
        fontSize: 11,
        padding: "1px 7px",
        borderRadius: 10,
        color: props.on ? palette.green : palette.muted,
        borderColor: props.on ? palette.green : undefined,
        opacity: props.disabled ? 0.55 : 1,
      }}
    >
      {props.on ? "●" : "○"} {props.label}
    </button>
  );
}

/** Where the shelf would have signalled, on the day so far. */
function SignalPanel({
  data,
  working,
  open,
}: {
  data?: LiveSignals;
  working: number;
  open: boolean;
}) {
  const ran = data?.strategies.filter((s) => s.ran) ?? [];
  const fired = ran.filter((s) => s.trades.length > 0);
  // `.sim-panel` — the same box the replay's ticket and blotter live in, opened
  // and pinned from the same rail. It was briefly its own class on the theory
  // that a feed you keep in view is a different kind of thing from a form you
  // fill in; in practice that just meant a second set of rules to keep in step,
  // and the panel already does overlay-or-column.
  //
  // `min-height: 0` on the flex column and the scroll on the card are what stop a
  // long signal list from stretching the row and squashing the chart — the row's
  // height belongs to the page, not to this.
  return (
    <div className={`sim-panel${open ? " open" : ""}`}>
      <div className="panel" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {!data ? (
          <div style={{ fontSize: 12, color: palette.muted }}>Loading signals…</div>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>Shadow signals</h3>
              <span style={{ fontSize: 11, color: palette.muted }}>
                {data.enabled ? `${ran.length}/${data.strategies.length} run` : "off"}
              </span>
            </div>
            <p style={{ fontSize: 11, color: palette.muted, margin: "4px 0 10px" }}>
              Prefix re-runs of the same engine the backtest uses, each under its
              own baseline's config. Nothing here places an order.
            </p>

            {!data.enabled && (
              // The whole point of the flag. Without it a switched-off shelf and
              // a market that has offered nothing render identically, and the
              // one you are looking at changes what the blank list means.
              <div
                style={{
                  fontSize: 11,
                  color: palette.orange,
                  border: `1px solid ${palette.cardBorder}`,
                  borderRadius: 4,
                  padding: "6px 8px",
                  marginBottom: 10,
                }}
              >
                The shelf is switched off. Anything below is what it last said
                before that, not a reading of the day as it stands.
              </div>
            )}
            {data.enabled && !data.journalling && (
              <div style={{ fontSize: 11, color: palette.muted, marginBottom: 10 }}>
                Not journalled — these passes are on screen only, so there is
                nothing for the after-the-close prefix check to run against.
              </div>
            )}

            <div style={{ fontSize: 11, color: palette.muted, marginBottom: 10 }}>
              Regime {data.regime.class ?? "—"}
              {data.regime.texture ? ` · ${data.regime.texture}` : ""} · frozen{" "}
              {data.regime.frozen.length ? data.regime.frozen.join(", ") : "none yet"}
            </div>

            {fired.length === 0 && data.enabled && (
              <div style={{ fontSize: 12, color: palette.muted }}>
                No strategy has signalled yet today.
              </div>
            )}
            {fired.map((s) => (
              <StrategyRow key={s.slug} s={s} />
            ))}

            {data.skipped.length > 0 && (
              <details style={{ marginTop: 12, fontSize: 11, color: palette.muted }}>
                <summary>{data.skipped.length} not watched</summary>
                {data.skipped.map((k) => (
                  <div key={k.slug}>
                    {k.slug} — {k.reason}
                  </div>
                ))}
              </details>
            )}
            {working > 0 && (
              <div style={{ marginTop: 12, fontSize: 11, color: palette.muted }}>
                {working} of your own order{working === 1 ? "" : "s"} working
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function StrategyRow({ s }: { s: ShadowStrategy }) {
  const last = s.trades[s.trades.length - 1];
  const open = last && !last.exit_ts_utc;
  return (
    <div style={{ borderTop: `1px solid ${palette.cardBorder}`, padding: "8px 0" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
        <strong style={{ fontSize: 12 }}>{s.name}</strong>
        <span style={{ fontSize: 10, color: palette.muted }}>{s.version}</span>
        {open && <span style={{ fontSize: 10, color: palette.green }}>OPEN</span>}
      </div>
      <div style={{ fontSize: 11, color: palette.muted }}>
        {s.trades.length} signal{s.trades.length === 1 ? "" : "s"}
        {s.vetoed.length > 0 && ` · ${s.vetoed.length} vetoed`}
        {last && ` · last ${last.direction} @ ${fmtPts(last.avg_entry)}`}
        {last?.exit_reason && ` → ${last.exit_reason}`}
      </div>
      {s.error && <div style={{ fontSize: 11, color: palette.red }}>⚠ {s.error}</div>}
    </div>
  );
}

/**
 * The autostart, while it is happening.
 *
 * Its own screen rather than a spinner on the form below, because the two say
 * opposite things: one is "choose how to open this", the other is "this is
 * already opening, with the order plant, on this contract". Showing the form
 * with a disabled button would invite a second connect on a page that is one
 * request away from a live session.
 */
function Connecting({ symbol, onManual }: { symbol: string; onManual: () => void }) {
  return (
    <div className="page">
      <div className="panel" style={{ maxWidth: 720 }}>
        <h2 style={{ marginTop: 0 }}>Connecting {symbol}</h2>
        <p className="muted">
          The ticker plant with the order plant alongside it, recording, shelf
          off. The session so far is replayed from its 18:00 ET open behind the
          live stream, so the night the Globex gates read is there whatever time
          you arrive.
        </p>
        {/* The one way out of a connect that hangs. Not an invitation — the POST
            returns as soon as the feed is *started*, well before the backfill
            lands — but a page whose only state is "connecting…" is a page you
            would have to reload to leave. */}
        <button type="button" onClick={onManual} style={{ marginTop: 4 }}>
          Set it up by hand instead
        </button>
      </div>
    </div>
  );
}

/**
 * No session running — start one, and say plainly what it is.
 *
 * NOT THE FRONT DOOR ANY MORE. The page autostarts a routed Rithmic session on
 * arrival; this is where that lands when it declines to fire (routing not
 * permitted here, `/live/routing` unreachable) or when it threw, and where an
 * explicit stop from the banner returns you. It is also still the only way to
 * the simulated feed and to a contract other than the stored one.
 *
 * The day list is the Simulator's: a fake feed's source is a cached Databento
 * session, so anything replayable is something the feed can present as today.
 */
function NoSession({
  onStarted,
  autoError,
}: {
  onStarted: () => void;
  /** What the autostart threw, if it got as far as throwing. Shown here because
   *  this screen is the only evidence the user gets that it tried at all. */
  autoError?: string | null;
}) {
  const daysQ = useSimulatorDays("NQ");
  const days = daysQ.data?.days ?? [];
  const [pick, setPick] = useState("");
  const [speed, setSpeed] = useState(60);
  const [startAt, setStartAt] = useState("09:25");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const chosen = days.find((d) => d.date === pick) ?? days[days.length - 1];

  return (
    <div className="page">
      <div className="panel" style={{ maxWidth: 720 }}>
        <h2 style={{ marginTop: 0 }}>Live</h2>
        <p className="muted">
          A strategy run over a session in progress reproduces exactly what the
          backtest reports for that stretch — that is the property the whole
          surface rests on, and it holds for either feed below.
        </p>

        {autoError && (
          <p style={{ color: palette.red, fontSize: 12 }}>
            ⚠ Connecting on arrival failed: {autoError}
          </p>
        )}

        <RithmicStart onStarted={onStarted} />

        <h3 style={{ marginBottom: 4 }}>Simulated feed</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          A cached Databento session replayed at wall-clock speed. It is not a
          market and records nothing, and the banner says so for as long as it
          runs. Use it to work on the surface when the market is shut.
        </p>

        <div style={{ display: "flex", gap: 10, alignItems: "end", marginTop: 12, flexWrap: "wrap" }}>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Session
            <select value={chosen?.date ?? ""} onChange={(e) => setPick(e.target.value)}>
              {days.map((d) => (
                <option key={`${d.symbol}-${d.date}`} value={d.date}>
                  {d.date} · {d.symbol}
                  {d.has_overnight ? "" : " (no overnight)"}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Speed
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => (
                <option key={s} value={s}>
                  {s}×
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Open at (ET)
            <input
              value={startAt}
              onChange={(e) => setStartAt(e.target.value)}
              style={{ width: 80 }}
            />
          </label>
          <button
            disabled={!chosen || busy}
            onClick={async () => {
              if (!chosen) return;
              setBusy(true);
              setErr(null);
              try {
                await startFakeFeed({
                  symbol: chosen.symbol,
                  date: chosen.date,
                  speed,
                  start_at: startAt,
                });
                onStarted();
              } catch (e) {
                setErr(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Starting…" : "Start simulated feed"}
          </button>
        </div>
        {err && <p style={{ color: palette.red, fontSize: 12 }}>{err}</p>}

        <p className="muted" style={{ fontSize: 13, marginTop: 24 }}>
          To practise against a finished session instead — with seek, rewind and
          speed — use <Link to="/charts/replay">Replay</Link>.
        </p>
      </div>

      {/* Coverage belongs on this screen as much as on the running one, and
          arguably more: "what have I got, and what is about to become
          unfetchable" is a question you ask *before* connecting, and this is the
          only page with the room to answer it properly. */}
      <div className="panel" style={{ maxWidth: 720, marginTop: 16 }}>
        <TapeCoverage />
      </div>
    </div>
  );
}

/**
 * Connect the real ticker plant.
 *
 * The contract field takes a RAW symbol and nothing else. That is not a UI
 * preference: a root sends `contract_for` to probe Databento — which a live path
 * must never do — and the on-disk roll map ends 2026-06-30 regardless. The API
 * rejects roots with a 422, so this is a hint rather than the guard.
 *
 * CONNECTING LATE IS NO LONGER A TRAP, AND IT USED TO BE. A session is 18:00 →
 * 18:00 ET, seven of the thirteen strategies declare `session="globex"`, and
 * their gates read the night off disk and blind-fail-closed when it is not
 * there — so connecting at nine in the morning produced a shadow day where those
 * seven vetoed everything and nothing said why, which looks exactly like "no
 * setup formed". The feed now replays the session from its open off Rithmic's
 * history plant before streaming, and records it, so the night is both on the
 * chart and on disk (`docs/live-shadow-plan.md § Tick replay`).
 *
 * What it does not cover: a contract that has already rolled off, which returns
 * nothing at any depth. Recording is still the only thing that makes a day
 * recoverable after that.
 */
function RithmicStart({ onStarted }: { onStarted: () => void }) {
  // The contract last connected to, which is also what the autostart used. Typed
  // once per roll rather than edited in the source — and typed *here*, since
  // reaching this screen at all is now the deliberate act.
  const [symbol, setSymbol] = useState(loadLiveContract);
  // The shelf off, the recording on, and the asymmetry is about what each one
  // costs rather than about which is more useful. The shelf is a study surface:
  // this page is opened to trade far more often than to watch thirteen
  // strategies re-run, so it is asked for rather than assumed.
  //
  // The recording is the opposite — leaving it off is what costs, and the bill
  // arrives on the *next* connect rather than this one. The backfill's own rows
  // are written (only the disk-resume frame is not), so a session recorded from
  // 18:00 is a session a restart reads back off parquet in a moment; one that
  // wrote nothing re-fetches the whole night off the history plant, measured at
  // ~38s for a 16-hour head. And it has to be decided *here*: `set_modes`
  // resumes writing from the tape's tail and never goes back for what already
  // arrived, so switching it on mid-session leaves two disjoint stretches on
  // disk — the one hole `RithmicFeed._backfill` documents it cannot repair.
  const [record, setRecord] = useState(true);
  const [signals, setSignals] = useState(false);
  // On, and it used to be off-always with a note under it saying that a
  // connection able to trade because of something you clicked on a previous
  // visit is the accident. What changed is the page above: it now opens a routed
  // session on arrival without asking, so this checkbox is no longer what stands
  // between a visit and the order plant, and leaving it off here would only mean
  // the fallback screen opens a session *less* capable than the front door's —
  // and one that cannot be upgraded without stopping and reconnecting.
  //
  // Still not persisted, because it is no longer a choice worth remembering: it
  // is the default, and unchecking it is a thing you do for this connect only.
  const [routing, setRouting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Whether this deployment permits routing at all, and which kind of account it
  // says these credentials are. Read before connecting because the answer
  // decides whether the switch below is even offered — and because "which
  // account is this" is a question worth answering before the socket, not after.
  const routingQ = useRoutingStatus();
  const rs = routingQ.data;

  // Same rule as the running session's switches, applied before there is a
  // session to apply it to: the shelf reads this day's earlier windows off disk,
  // so it cannot honestly run over a feed that writes none of them. Unchecking
  // the recording therefore unchecks the shelf here, where nothing has started
  // and the pair is still a single choice about how to open the connection.
  const setRecording = (on: boolean) => {
    setRecord(on);
    if (!on) setSignals(false);
  };

  // What the connect will actually ask for. The switch above defaults on and is
  // *hidden* where the deployment forbids routing, so sending the raw state
  // there would be asking for a plant this build refuses — a 422 on a screen
  // that never showed a checkbox to explain it.
  const willRoute = routing && !!rs?.enabled && !rs.refusal;

  return (
    <div style={{ marginBottom: 28 }}>
      <h3 style={{ marginBottom: 4 }}>Rithmic feed</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        The ticker plant, and the order plant alongside it unless you uncheck it
        below — the same pair the page opens on arrival, which is how you got
        here only if that declined or failed.
        Recorded, every print goes to <code>data/live/</code>,
        including the backfilled night: that is what the Globex gates and the
        weekly seed read off disk, and it is what a reconnect resumes from
        instead of re-fetching sixteen hours off the history plant. The shelf
        starts off — it is a study surface, and it needs the recording anyway.
        Live ticks never join the Databento corpus. The first two switches can be
        thrown again while the session runs; the third cannot.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap" }}>
        <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
          Contract (raw)
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="NQU6"
            style={{ width: 100 }}
          />
        </label>
        <label
          style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 12 }}
          title={
            "Every print to data/live/, the replayed night included. It is what " +
            "a reconnect resumes from: with this off, the next connect re-fetches " +
            "the whole night off the history plant (~38s for a 16-hour head) " +
            "instead of reading it back. Decide it here — switched on later it " +
            "writes only from that moment, and the gap is not repairable."
          }
        >
          <input
            type="checkbox"
            checked={record}
            onChange={(e) => setRecording(e.target.checked)}
          />
          Record the tape
        </label>
        <label
          style={{
            display: "flex",
            gap: 5,
            alignItems: "center",
            fontSize: 12,
            opacity: record ? 1 : 0.55,
          }}
          title={
            record
              ? "Re-run every registered strategy over the day as it arrives."
              : "Needs the recording: the Globex gates read this session's earlier " +
                "windows off disk, and with nothing written they veto everything " +
                "without saying why."
          }
        >
          <input
            type="checkbox"
            checked={signals}
            disabled={!record}
            onChange={(e) => setSignals(e.target.checked)}
          />
          Run shadow signals
        </label>
        {/* The third switch, and the only one that is not a mode. It decides
            whether the connection opens Rithmic's ORDER plant — one login is one
            socket, so the order path has to ride this one, which is why it is
            settled here and not toggleable later. Absent entirely when the
            deployment does not permit routing: there is nothing to explain on a
            page whose job is to start a feed, and the refusal (with the env var
            to set) is one click away in the panel. */}
        {rs?.enabled && !rs.refusal && (
          <label
            style={{
              display: "flex",
              gap: 5,
              alignItems: "center",
              fontSize: 12,
              color: routing ? palette.orange : undefined,
            }}
            title={
              "Opens the ORDER and PnL plants alongside the tape, so the real " +
              "accounts appear in the order-entry selector. The session still " +
              "starts on the paper account: sending needs a real account and a " +
              "label on it. Cannot be switched on later — stop and reconnect."
            }
          >
            <input
              type="checkbox"
              checked={routing}
              onChange={(e) => setRouting(e.target.checked)}
            />
            Enable order entry
          </label>
        )}
        <button
          disabled={busy || symbol.trim().length < 4}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              const raw = symbol.trim();
              await startRithmicFeed({ symbol: raw, record, signals, routing: willRoute });
              // What the next visit autostarts on. Written after the connect
              // rather than as you type: a contract the plant accepted is the
              // only one worth arriving on by itself.
              saveLiveContract(raw);
              onStarted();
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy
            ? "Connecting…"
            : willRoute
              ? "Connect with order entry"
              : record
                ? "Connect & record"
                : "Connect (no recording)"}
        </button>
      </div>
      {err && <p style={{ color: palette.red, fontSize: 12 }}>{err}</p>}
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Connecting mid-session is fine: the day is replayed from its 18:00 ET
        open before the live stream starts, so the night the Globex gates read is
        there whatever time you arrive. Needs the <code>RITHMIC_*</code> keys in{" "}
        <code>.env</code> — <code>uv run python demo/rithmic_smoke.py</code> is
        what proves them, and{" "}
        <code>demo/rithmic_history_probe.py</code> the replay behind them.
      </p>
    </div>
  );
}
