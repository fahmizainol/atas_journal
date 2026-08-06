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
import { Link } from "react-router-dom";
import { ReplayChart, type ReplayChartHandle } from "../components/charts/ReplayChart";
import type { IndicatorSettingsMap } from "../components/charts/IndicatorLegend";
import type { SettingField } from "../components/charts/IndicatorSettings";
import { SimIndicators } from "../components/charts/SimIndicators";
import { TimeframeControl } from "../components/charts/TimeframeControl";
import { ChartTopBar } from "../components/charts/ChartTopBar";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import {
  useSimulatorDays,
  useSimulatorHistory,
  useSimulatorSession,
  type HistDay,
} from "../hooks/useSimulator";
import { useReplayAttempt } from "../hooks/useReplayAttempt";
import {
  concatTapes,
  ReplayEngine,
  decodeTape,
  type IbBox,
  type RangeBox,
  type SessionPayload,
  type Tape,
} from "../lib/replayEngine";
import { replaySource, type TapeSource } from "../lib/tapeSource";
import { showsSeconds, timeframeById, TIMEFRAMES } from "../lib/timeframes";
import {
  newLog,
  newSim,
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
  BIG_LOT_OPTIONS,
  DEFAULT_SIM_PREFS,
  EVENT_STRENGTH_OPTIONS,
  HISTORY_DAY_OPTIONS,
  loadSimPrefs,
  NODE_PROM_OPTIONS,
  saveSimPrefs,
  SIM_SPEEDS,
} from "../lib/simPrefs";
import type { CompositeRule, CompositeSpan } from "../lib/compositeProfile";
import type { TapeRange } from "../lib/volumeProfile";
import { MIN_SAMPLE } from "../lib/replayStats";
import { palette } from "../theme";

/** How far the ticket has to be dragged down before letting go puts it away. */
const GRAB_DISMISS_PX = 64;
const RTH_OPEN_MIN = 9 * 60 + 30;

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


export function Simulator() {
  // Read once, on mount: everything below seeds from it, and from then on the
  // React state is the truth and the store just trails it.
  const [prefs] = useState(loadSimPrefs);
  const [root, setRoot] = useState<string>(prefs.root);
  const daysQ = useSimulatorDays(root);
  const [sel, setSel] = useState<{ symbol: string; date: string } | null>(null);
  const [startTime, setStartTime] = useState(prefs.startTime);
  const tz = "New York";

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
  const [eventStrength, setEventStrength] = useState(prefs.eventStrength);
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
  const hidden = blind && !revealed;

  /** Any cached day, at random. */
  const anyDay = useCallback((days: { symbol: string; date: string }[]) => {
    const d = days[Math.floor(Math.random() * days.length)];
    setRevealed(false);
    setSel({ symbol: d.symbol, date: d.date });
  }, []);

  // Which day the picker opens on is a draw, on purpose: the replay is only
  // practice while the tape is one you don't remember, and the newest session is
  // the one you have most likely just been looking at. Pick another with the
  // dropdown, or draw again with 🎲.
  useEffect(() => {
    if (!sel && daysQ.data && daysQ.data.days.length) anyDay(daysQ.data.days);
  }, [anyDay, daysQ.data, sel]);

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
  const [openPos, setOpenPos] = useState<Position | null>(null);
  const [working, setWorking] = useState<WorkingOrderView[]>([]);
  const [size, setSize] = useState(prefs.size);
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

  // The ticket, the transport speed and the session you set up from are settings,
  // not replay state — they follow you to the next visit.
  useEffect(() => {
    saveSimPrefs({
      root,
      startTime,
      speed,
      size,
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
      eventStrength,
      indicators,
      railPinned,
    });
  }, [
    root,
    startTime,
    speed,
    size,
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
    eventStrength,
    indicators,
    railPinned,
  ]);

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
  // whether a position is open and how far its buttons wrapped. Published as
  // --chart-floor and read by the chart's own stylesheet, so the chart never has
  // to know what this page keeps down there.
  const dockRef = useRef<HTMLDivElement>(null);
  const [floor, setFloor] = useState(0);
  useEffect(() => {
    const el = dockRef.current;
    if (!el) {
      setFloor(0);
      return;
    }
    const read = () => setFloor(el.getBoundingClientRect().height);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    read();
    return () => ro.disconnect();
  }, []);

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
  useEffect(() => {
    const el = footRef.current;
    if (!el) return;
    const read = () => setFoot(el.getBoundingClientRect().height);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    read();
    return () => ro.disconnect();
  }, []);

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
  const pointValue = sessionRef.current?.point_value ?? 20;

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
    const views = workingOrders(st).map((o) => orderView(o, clock, st.open));
    // The trades array is appended to in place by the stepper, and so is the
    // position — a scale-in moves the size and the average on the object React
    // is already holding. Hand over copies or it sees the same reference and
    // skips the render.
    setTrades(st.trades.slice());
    setOpenPos(st.open && { ...st.open });
    setWorking(views);
    chartRef.current?.setPosition(st.open ? posLine(st.open, barAt) : null);
    chartRef.current?.setOrders(views);
    chartRef.current?.setTrades(st.trades.map((t) => tradeMark(t, barAt)));
    // Every path that changes the simulation ends here, which makes this the one
    // place the recorder has to be told. It writes nothing until a fill has
    // happened, and nothing again until something changes.
    recordAttempt(logRef.current, st.trades, st.open != null, clock);
  }, [barAt, recordAttempt]);

  // Re-derive everything from the log. Every user action goes through here: an
  // action is rare enough that one pass over the tape costs nothing, and it
  // means there is no second, optimistic code path that could disagree with
  // what a later scrub would produce.
  const rebuild = useCallback(
    (clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      publish(runSim(tape, logRef.current, clock, pointValue), clock);
    },
    [publish, pointValue],
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
      stepSim(tape, logRef.current, st, from, to, clock, pointValue);
      if (simSig(st) !== sigRef.current) publish(st, clock);
    },
    [pointValue, publish],
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
      // A rewind un-develops the day's range along with everything else: the
      // snapshot is re-derived from tick zero, so the extremes are whatever had
      // actually printed by the new clock.
      geoRef.current = { ib: snap.ib, range: snap.range };
      clockRef.current = clamped;
      rebuild(clamped);
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
  }, []);

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
  const indicatorSettings = useMemo<IndicatorSettingsMap>(() => {
    // One prominence for both node readers, and one strength floor for both
    // event kinds — it is one question asked of two layers, and two knobs for it
    // would only ever be set to the same number. Each appears on both of its
    // rows, saying so.
    const nodes: SettingField = {
      key: "nodeProm",
      label: "Prominence floor",
      help: "Mark high- and low-volume nodes on the composite and on the developing NY profile. A hump counts once it stands this far clear of the deeper valley beside it, as a share of the tallest hump — lower finds more. LVNs are only drawn between two accepted humps.",
      value: nodeProm,
      options: NODE_PROM_OPTIONS.map((p) => ({
        value: p,
        label: p === 0 ? "off" : `${Math.round(p * 100)}%`,
      })),
      onChange: (v) => setNodeProm(Number(v)),
      note: "Shared with the other node reader — the composite's and the NY profile's are one setting.",
    };
    const events: SettingField = {
      key: "eventStrength",
      label: "Strength floor",
      help: "Draw tape events as bands: clustered big sweeps (≥150 lots within 60s and 5pt) and absorption (a 15s window whose lots-per-point runs ≥3× the session's own median so far). The floor multiplies both thresholds. Not a signal — measured, both land further from a frozen composite's levels than the session's own volume does.",
      value: eventStrength,
      options: EVENT_STRENGTH_OPTIONS.map((s) => ({ value: s, label: s === 0 ? "off" : `≥${s}×` })),
      onChange: (v) => setEventStrength(Number(v)),
      note: "Shared with the other event kind — one floor multiplies both thresholds.",
    };
    return {
      bigTrades: {
        title: "Big trades",
        fields: [
          {
            key: "bigLots",
            label: "Sweep size",
            help: "Mark sweeps over this many lots. A sweep is consecutive same-side fills within 250ms and 4 ticks — the shape an order gets worked through the book in, which single prints mostly miss.",
            value: bigLots,
            options: BIG_LOT_OPTIONS.map((n) => ({ value: n, label: `>${n} lots` })),
            onChange: (v) => changeBigLots(Number(v)),
          },
        ],
      },
      compositeProfile: {
        title: "Composite VP",
        fields: [
          {
            key: "composite",
            label: "Rule",
            help: "Composite the prior days into one profile. 'Balance run' takes only the days still in the same auction (each one's value area must touch the composite's, cap 5) — measured as the better rule on NQ, where balance runs are median 2 days and a fixed 10-day window merges about eight auctions.",
            value: composite,
            options: [
              { value: "off", label: "off" },
              { value: "balance", label: "balance run" },
              { value: "days", label: "all prior days" },
            ],
            onChange: (v) => setComposite(v as CompositeRule),
            note: `Built from the ${historyDays} prior session${historyDays === 1 ? "" : "s"} loaded — "Prior days" in the setup row, since each one is a whole tape to fetch.`,
          },
          // Only once there is a composite for it to cut: which part of each day
          // goes in is not a question about a profile that isn't being built.
          ...(composite === "off"
            ? []
            : [
                {
                  key: "compositeSpan",
                  label: "Span",
                  help: "Which part of each prior day the composite is built from. 'Globex + RTH' takes the whole day from the 18:00 open to the 16:00 close; 'RTH only' takes the day session, which is the span the balance-run and value-area numbers in the write-up were measured on. Wider spans mean wider value areas, which touch more often — so the balance rule keeps more days under Globex.",
                  value: compositeSpan,
                  options: [
                    { value: "globex", label: "globex + RTH" },
                    { value: "rth", label: "RTH only" },
                  ],
                  onChange: (v: string | number) => setCompositeSpan(v as CompositeSpan),
                } satisfies SettingField,
              ]),
        ],
      },
      compositeNodes: { title: "Composite nodes", fields: [nodes] },
      developingVpNyNodes: { title: "NY nodes", fields: [nodes] },
      sweepBursts: { title: "Sweep bursts", fields: [events] },
      absorption: { title: "Absorption", fields: [events] },
    };
  }, [bigLots, changeBigLots, composite, compositeSpan, eventStrength, historyDays, nodeProm]);

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
    // A context change mid-replay is not a reason to stop watching: the clock
    // doesn't move, so playback picks up where the rebuild left it.
    const wasPlaying = playingRef.current;
    stop();
    // The session's own ticks are decoded once. Re-gluing is an array copy;
    // re-decoding would be a million prints for a picture that didn't change.
    const sessTape = fresh || !sessTapeRef.current ? decodeTape(data) : sessTapeRef.current;
    const tape = contextTapes.length ? concatTapes([...contextTapes, sessTape]) : sessTape;
    const shift =
      contextTapes.reduce((a, t) => a + t.n, 0) -
      histTapesRef.current.reduce((a, t) => a + t.n, 0);
    sessTapeRef.current = sessTape;
    histTapesRef.current = contextTapes;
    tapeRef.current = tape;
    sessionRef.current = data;
    sourceRef.current = replaySource(data.session_end_ms);
    if (fresh) {
      // Before anything reads a clock off this tape: every HUD push from here on
      // belongs to this session, and the clock-keyed effects check the stamp.
      sessGenRef.current += 1;
      endedRef.current = false;
    }
    // The ⚓ is the user's, and it is placed on a bar time — which the context
    // days don't move. Carried across the rebuild rather than re-placed.
    const anchor = fresh ? null : (engineRef.current?.anchor() ?? null);
    engineRef.current = new ReplayEngine(tape, data, tfRef.current);
    engineRef.current.setBigLots(bigLotsRef.current);
    if (anchor != null) engineRef.current.setAnchor(anchor);
    // The chart profiles bar ranges straight off the tape, so it needs the same
    // typed arrays the engine got. On a new day that also resets its hand-drawn
    // tools; on a context change it must not — the same session with more days
    // in front of it is the same chart.
    chartRef.current?.setTape(tape, { keepTools: !fresh, contextRanges });
    if (fresh) {
      logRef.current = newLog();
      simRef.current = newSim();
      sigRef.current = simSig(simRef.current);
      openRef.current = null;
      idRef.current = 1;
      setTrades([]);
      setOpenPos(null);
      setWorking([]);
      // A new tape is a new question — whatever was revealed about the last one
      // doesn't carry.
      setRevealed(false);
    } else if (shift) {
      // The tape grew (or shrank) in front of the session, so every cursor the
      // log recorded points a few million prints off. Nothing else in it moves:
      // the clocks and the prices are what they were.
      logRef.current = shiftLog(logRef.current, shift);
    }
    // Honour the chosen start time (falls back to the RTH bell). A context
    // change is not a move through time, so it keeps the clock it had.
    const [h, m] = startTime.split(":").map((x) => parseInt(x, 10));
    const offMin = (Number.isFinite(h) ? h * 60 + m : RTH_OPEN_MIN) - RTH_OPEN_MIN;
    const clock = fresh
      ? Math.max(
          data.session_start_ms,
          Math.min(data.session_end_ms, data.rth_open_ms + offMin * 60_000),
        )
      : clockRef.current;
    const snap = engineRef.current.snapshotTo(clock);
    chartRef.current?.setSnapshot(snap, fresh ? undefined : { reframe: false });
    geoRef.current = { ib: snap.ib, range: snap.range };
    clockRef.current = clock;
    setReady(true);
    // Re-publish what the log says: the position, the working orders and every
    // mark have to come back on a chart that was just handed a new tape.
    if (!fresh) rebuild(clock);
    pushHud(snap.lastPrice, clock, true);
    if (fresh) {
      // Point the recorder at this session. Nothing is written yet — an attempt
      // opens on the first fill — but the tape it would be measured against is
      // fingerprinted here, while the payload is in hand.
      armAttempt({
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
      });
    } else if (wasPlaying) play();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionQ.data, contextTapes, contextRanges]);

  // A change of span re-cuts the same days without touching the tape, so the
  // rebuild above sees nothing to do and returns early. Push the new spans on
  // their own: the chart no-ops when they are the ones it already has, so the
  // rebuild's own `setTape` is not doubled up on.
  useEffect(() => {
    chartRef.current?.setContextRanges(contextRanges);
  }, [contextRanges]);

  useEffect(() => () => stop(), [stop]);

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
      };
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
    },
    [append, size, stopTicks, targetTicks, tickSize, trailTicks, trailStepTicks, trailBeTicks, trailBeOnly],
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

  // Running out of tape ends the replay, and the answer comes with it. Keyed on
  // the clock rather than wired into the playback loop so it holds however the
  // end was reached — played to, stepped to, or scrubbed to. It ends the
  // attempt on the same terms, for the same reason: the sitting is over.
  useEffect(() => {
    const end = sessionRef.current?.session_end_ms;
    // Only this session's own clock speaks for this session — see `gen`.
    if (end == null || hud.gen !== sessGenRef.current || hud.clockMs < end) return;
    setRevealed(true);
    if (endedRef.current) return;
    endAttempt();
  }, [endAttempt, hud.clockMs, hud.gen]);

  // The three things you do faster than a hand can find a button: get out (q),
  // buy (w), sell (s). Left-hand keys next to each other, because the other hand
  // is on the mouse placing levels on the chart.
  //
  // Deliberately unmodified single keys — a trading key with a chord in front of
  // it is a key you don't press in time — so everything that could be *typing*
  // is let through untouched: a field with the caret in it, a chord, and an
  // autorepeat (a held w must not machine-gun market orders).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      const k = e.key.toLowerCase();
      if (k !== "q" && k !== "w" && k !== "s") return;
      e.preventDefault();
      if (k === "q") closeAll();
      else placeMarket(k === "w" ? "long" : "short");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeAll, placeMarket]);

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
  const scrubMax = sess?.session_end_ms ?? 1;

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
          !sel
            ? "Pick a session"
            : hidden
              ? `${root} · ▨▨▨▨ · ${startTime}`
              : `${sel.symbol} · ${sel.date} · ${startTime}`
        }
        onTitle={() => setSetupOpen((o) => !o)}
        titleOpen={setupOpen}
        right={
          <Link to="/charts/replay/history" className="sim-topbar-link" title="Every attempt you've recorded">
            History →
          </Link>
        }
      >
        <TimeframeControl
          value={tfId}
          onChange={changeTimeframe}
          options={TIMEFRAMES.map((t) => ({ key: t.id, label: t.label }))}
        />
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
                setRevealed(false);
                setSel({ symbol, date });
              }}
            >
              {(daysQ.data?.days ?? []).map((d) => (
                <option key={`${d.symbol}|${d.date}`} value={`${d.symbol}|${d.date}`}>
                  {d.date} · {d.symbol}
                  {d.has_overnight ? "" : " (no overnight)"}
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
          disabled={!daysQ.data?.days.length}
          title="Draw another session at random"
        >
          🎲
        </button>
        <label
          style={{ display: "flex", alignItems: "center", gap: 6, alignSelf: "end", fontSize: 12, color: palette.muted, paddingBottom: 6 }}
          title="Hide which day this is — the date comes off the picker and the chart's time axis until the tape runs out"
        >
          <input type="checkbox" checked={blind} onChange={(e) => setBlind(e.target.checked)} style={{ margin: 0 }} />
          Blind
        </label>
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
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Start time (ET)
          <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
        </label>
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

      <div className="sim-body">
        <div className="sim-chart-card">
          <div className="sim-chart">
            <ReplayChart
              ref={chartRef}
              onAnchorChange={setAnchor}
              onBracketChange={moveBracket}
              onFlatten={closeManual}
              onOrderMove={moveOrder}
              onOrderCancel={cancelOrder}
              onPlaceOrder={placeAt}
              onPlaceTyped={placeTyped}
              ticket={{ size, stopTicks, targetTicks }}
              onTicketChange={(t) => {
                setSize(t.size);
                applyStop(t.stopTicks);
                applyTarget(t.targetTicks);
              }}
              mark={hud.lastPrice}
              canPlaceOrders={ready}
              hideDates={hidden}
              secondsAxis={showsSeconds(tf)}
              bigLots={bigLots}
              composite={historyDays > 0 ? composite : "off"}
              nodeProm={nodeProm}
              eventStrength={eventStrength}
              indicatorSettings={indicatorSettings}
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
              reach. */}
            <div ref={dockRef} className="sim-quick">
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
            </div>
          </div>
          {/* The transport keeps a permanent row — the one deliberate exception
              to this page summoning its chrome. It is not something you
              occasionally want: it is the instrument you drive a replay with,
              and it carries the clock you read continuously. Behind a pill it
              cost a click before every scrub, speed change and step, and hid the
              Play button, which is the control pressed most. ~34px, and only
              Replay pays it — Live has no transport. */}
          <div ref={footRef} className="sim-transport">
              <button type="button" style={btn(playing ? palette.red : palette.green)} onClick={() => (playing ? stop() : play())} disabled={!ready}>
                {playing ? "❚❚ Pause" : "▶ Play"}
              </button>
              <button type="button" style={btn(palette.card)} onClick={stepBar} disabled={!ready || playing}>
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
                min={scrubMin}
                max={scrubMax}
                step={1000}
                value={hud.clockMs}
                onChange={(e) => seekTo(Number(e.target.value))}
                disabled={!ready}
                className="sim-scrub"
              />
              <span className="sim-clock" style={{ fontFamily: "monospace", color: palette.text, minWidth: 78 }}>
                {fmtClock(hud.clockMs)}
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
          <button
            type="button"
            className={`sim-rail-btn${sheetOpen ? " on" : ""}`}
            onClick={() => setSheetOpen((o) => !o)}
            aria-pressed={sheetOpen}
            title={sheetOpen ? "Hide the ticket and blotter" : "Show the ticket and blotter"}
          >
            ▤
          </button>
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
        </div>

        {/* Ticket, working orders, attempt summary and blotter. Laid over the
            tape by default and given its own column when pinned — the rail
            beside it is what opens and pins it. The collapsed "Last / Ticket ▴"
            face it used to carry on a phone is gone: the rail button is the
            opener now, and the market buttons already quote the last price. */}
        <div ref={panelRef} className={`sim-panel${sheetOpen ? " open" : ""}`}>
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
          <div className="sim-card sim-ticket">
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
            <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
              {(["market", "limit", "stop"] as OrderType[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => chooseType(t)}
                  style={{
                    ...btn(orderType === t ? palette.accent : palette.bg2),
                    flex: 1,
                    padding: "6px 0",
                    fontSize: 12,
                    textTransform: "capitalize",
                    color: orderType === t ? "#fff" : palette.muted,
                  }}
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
              Keys: <b>w</b> buy · <b>s</b> sell · <b>q</b> flatten and cancel everything.
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
          </div>

          {/* Working orders. Only there when something is resting — an empty box
              on every flat session would just be furniture. */}
          {working.length > 0 && (
            <div className="sim-card">
              <div style={{ color: palette.muted, fontSize: 12, marginBottom: 6 }}>
                Working · {working.length}
                {working.length > 1 && (
                  <span
                    style={{ opacity: 0.7 }}
                    title="Orders placed while flat are one OCO set. Orders placed while a position is open stand on their own — they scale in, scale out or flip."
                  >
                    {" "}
                    · placed flat, they cancel each other
                  </span>
                )}
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
                    </div>
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
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, flex: "none", gap: 6, alignItems: "center" }}>
              <span style={{ color: palette.muted, fontSize: 12 }}>
                Blotter · {trades.length} trades · {wins}W
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
                    <span style={{ fontFamily: "monospace", color: t.pnl >= 0 ? palette.green : palette.red }}>{fmtUsd(t.pnl)}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
