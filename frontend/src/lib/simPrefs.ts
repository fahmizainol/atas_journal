// What the Simulator remembers between visits.
//
// The replay itself is throwaway — a session, a log of what you did, gone on
// reload. The *ticket* isn't: size, the bracket you trade with, the order type
// you reach for, the speed you watch at and the day you start from are the
// settings you'd otherwise re-enter every single time. They're a per-user
// preference like the chart's indicator toggles, so they live in the same place
// and load the same way: anything missing or malformed falls back to the
// default rather than breaking the page.
//
// Deliberately *not* remembered: which session was open. The picker defaults to
// the most recent cached day, and that's the one you almost always want after
// new data lands.

import { DEFAULT_BIG_LOTS, DEFAULT_EVENT_TUNING, type EventTuning } from "./replayEngine";
import type { OrderType } from "./replaySim";
import { DEFAULT_NODE_PROM } from "./volumeProfile";
import type { CompositeRule, CompositeSpan } from "./compositeProfile";
import { DEFAULT_TIMEFRAME_ID, TIMEFRAMES } from "./timeframes";
import { DEFAULT_MODERN_VWAP, modernVwapParams, type ModernVwapParams } from "./modernVwap";

/** Replay speeds, as multiples of real time. */
export const SIM_SPEEDS = [1, 5, 30, 120, 300];

/** Big-trade thresholds offered in the setup bar, in lots. 50 is the default
 *  and the one the write-up is cut at; 25 shows the ordinary flow around it, 100
 *  and 200 keep only what a session has a handful of. */
export const BIG_LOT_OPTIONS = [25, 50, 100, 200];

/** Prior sessions the chart can carry as context. Three is a working week's
 *  worth of levels behind you and about three million extra prints in memory;
 *  past that it is a deliberate choice, so the steps get coarse. */
export const HISTORY_DAY_OPTIONS = [0, 1, 3, 5, 10];

/** How the context days are grouped into one composite profile.
 *
 *  `days`    every prior session loaded, as one profile — the zero-thought rule,
 *            and the one the demo measured as the *worse* rule on NQ: balance
 *            runs are median 2 days (p90 4), so a fixed 10-day window merges
 *            about eight auctions and its value area is 1,154pt wide.
 *  `balance` accumulate back from yesterday while each further session's value
 *            area still touches the composite's, stop on a clean break, cap 5.
 *            One auction, whatever that took. */
export const COMPOSITE_RULES: readonly CompositeRule[] = ["off", "days", "balance"];

/** How much of each context day the composite is built from.
 *
 *  `globex` the overnight in front of the session too — the default, because a
 *           level that ignores the night is a level the night may already have
 *           traded through, and Globex is where a good part of an NQ auction
 *           happens.
 *  `rth`    the day session alone, which is the span the demo's balance-run and
 *           value-area numbers were measured on. Keep it when comparing against
 *           the write-up, or when the night is genuinely a different auction. */
export const COMPOSITE_SPANS: readonly CompositeSpan[] = ["globex", "rth"];

/** Prominence a hump must clear to be read as a node, as a share of the tallest.
 *  Zero is the node reader off. The right setting is not knowable in advance —
 *  that is exactly what makes it a setting. */
export const NODE_PROM_OPTIONS = [0, 0.15, 0.25, 0.35, 0.45, 0.6];

/** What selects a tape event, offered as short lists rather than boxes — every
 *  one of these is a question with a measured answer and a few interesting
 *  neighbours, and a free-typed number invites a search for a setting that fits
 *  the day you happen to be reading.
 *
 *  The starred value in each list is `DEFAULT_EVENT_TUNING`'s, which is the
 *  demo's; see `EventTuning` for what each one asks. Changing any of them
 *  re-derives the tape, and strengths are only comparable within one setting. */
export const EVENT_TUNING_OPTIONS = {
  /** Below 25 a "sweep" is ordinary two-lot flow and every minute has a burst. */
  sweepLots: [10, 25, 50, 100, 200],
  /** A minute is the demo's. Five is a whole rotation — bursts stop being events
   *  and start being "the hour was busy". */
  burstGapS: [15, 30, 60, 120, 300],
  /** 5pt is about an NQ rotation's noise. 20 lets a burst follow a trend leg. */
  burstSpanPts: [2, 5, 10, 20],
  /** 150 = strength 1.0. 50 draws the ordinary flow, 1000 only the days it
   *  happened on. */
  burstLots: [50, 100, 150, 300, 500, 1000],
  /** 15s is the demo's. 5s finds the jab a single iceberg refill leaves; 60s
   *  finds the shelf a whole balance sat on. */
  absorbWinMs: [5_000, 10_000, 15_000, 30_000, 60_000],
  /** 3× the session's own median. Under 2× half the session qualifies. */
  absorbMult: [1.5, 2, 3, 4, 6],
  /** Windows before anything is scored — 20 is five minutes at the default
   *  window. Fewer means absorption near the open, off a thin median. */
  absorbMinWindows: [4, 8, 20, 40, 80],
  /** 0 = every window so far today. Otherwise the last N, so the baseline drifts
   *  with the regime instead of carrying the open around all afternoon. */
  absorbBaseline: [0, 40, 120, 240],
} as const;

/** Strength at which a band carries its lot count (0 = never). At 1 every drawn
 *  band is labelled, which on a busy setting is a number per band. */
export const EVENT_LABEL_ST_OPTIONS = [0, 1, 1.5, 2, 3];

/** Fill alpha at strength 1 (0 = outline only). The bands draw under the
 *  candles, over whatever profile gutters are already on. */
export const EVENT_FILL_OPTIONS = [0, 0.1, 0.2, 0.35];

export interface SimPrefs {
  root: string;
  /** ET wall clock the replay starts at, "HH:MM". */
  startTime: string;
  speed: number;
  size: number;
  /** Bracket distance in ticks. Zero means the leg is off: both the stop and the
   *  target are optional, and an order placed without either is managed by hand
   *  (a manual close, or a level dragged on afterwards). */
  stopTicks: number;
  targetTicks: number;
  /** The ladder, in ticks — the same four knobs the backtest engine trails by
   *  (`trail_stop_ticks` and friends). `trailTicks` is the master switch: zero
   *  and the stop is yours to move. The page resolves these to prices at
   *  placement, which is where they stop being ticks. */
  trailTicks: number;
  trailStepTicks: number;
  trailBeTicks: number;
  trailBeOnly: boolean;
  orderType: OrderType;
  /** Hide which day you're trading until the replay ends. */
  blind: boolean;
  /** Which bar the chart draws (see lib/timeframes). Purely how the tape is
   *  bucketed for display — it can't change a fill, so it's safe to carry. */
  timeframe: string;
  /** Lots a sweep must exceed to be marked on the chart. Like the timeframe, a
   *  reading choice: it changes which prints are drawn, never what fills. */
  bigLots: number;
  /** How many prior sessions to draw to the left of the replay. Each one is a
   *  whole tape (a few MB and a million prints), so this is the one setting here
   *  that costs something — hence a short list of choices rather than a box. */
  historyDays: number;
  /** How the context days are composited into one profile, if at all. A reading
   *  choice like the two above, and one that costs nothing extra: the composite
   *  is built from tape that is already loaded and already drawn. */
  composite: CompositeRule;
  /** How much of each context day goes in — the day session, or the Globex
   *  session in front of it as well. */
  compositeSpan: CompositeSpan;
  /** Modern VWAP's parameters, stored as one object because the indicator takes
   *  them as one. A drawing choice like the composite: it reads bars that are
   *  already on the chart and cannot touch a fill. */
  modernVwap: ModernVwapParams;
  /** Prominence floor for the HVN/LVN node reader (0 = off). Read off the
   *  composite and off the developing NY profile alike — one knob, because it is
   *  one question ("how big does a hump have to be") asked of two profiles. */
  nodeProm: number;
  /** What selects a tape event. A reading choice like the ones above — it
   *  re-derives which bands are drawn, never a fill — and the one setting here
   *  that is really ten, because "is this size arriving or defending" is not a
   *  question with one threshold. The rows themselves are shown or hidden by the
   *  chart's own indicator toggles, which is where every other layer's on/off
   *  lives. */
  eventTuning: EventTuning;
  /** Strength at which a band carries its lot count (0 = never). */
  eventLabelSt: number;
  /** Fill alpha of the band wash at strength 1 (0 = outline only). */
  eventFill: number;
  /** Whether the events also draw as a marginal down the volume profiles'
   *  gutters — the "where did all that size go" reading, which is a different
   *  question from the bands on the candles. */
  eventMarginal: boolean;
  /** Whether the day-scale indicator strip is showing. Chart real estate, so it
   *  collapses to a pill — and like the chart's other reading choices it can't
   *  touch a fill, which is what makes it safe to carry between visits. */
  indicators: boolean;
  /** Whether the ticket/blotter rail reserves layout width instead of opening
   *  over the tape. Carried between visits because it is a statement about how
   *  you work — a Replay session where you read more than you trade wants the
   *  rail away, and that preference outlives the session. Like the other reading
   *  choices here it cannot touch a fill. */
  railPinned: boolean;
  /** How many chart panes the replay draws. One is the page as it always was.
   *  Two puts a context chart beside the trading one — its own engine on its own
   *  bucketing over the same tape, read-only, repainting when a bar closes on it
   *  rather than every frame (measured: a pane that repaints per frame costs a
   *  quarter of the frame rate, one gated on bar close costs nothing). Stored as
   *  a count rather than a boolean because the layout is built to grow. */
  panes: number;
  /** The context pane's bucketing. Its own preference — the point of the pane is
   *  that it is *not* the timeframe you are trading. */
  paneTf: string;
  /** Where the divider sits, as the trading pane's percentage of the width.
   *  Clamped well short of either edge: a pane dragged to nothing is a pane you
   *  cannot get back by dragging. */
  splitPct: number;
}

const KEY = "sim.prefs";

export const DEFAULT_SIM_PREFS: SimPrefs = {
  root: "NQ",
  startTime: "09:30",
  speed: 30,
  size: 1,
  // The bracket the operating plan trades and the guardrails accept — a 40-60
  // tick stop and a 100+ tick target (lib/guardRules). Practice defaults to what
  // the funded account will actually let you place: rehearsing an 80-tick target
  // for a month and then being refused it is worse than not rehearsing.
  stopTicks: 50,
  targetTicks: 120,
  // Off by default: an auto-stop you didn't ask for is one that moves your
  // levels while you're reading the tape. The breakeven offset is pre-set to the
  // 4 ticks an NQ round trip actually costs, so switching the trail on gives you
  // a scratch that is really a scratch rather than one that books −$14.
  trailTicks: 0,
  trailStepTicks: 0,
  trailBeTicks: 4,
  trailBeOnly: false,
  orderType: "market",
  blind: false,
  timeframe: DEFAULT_TIMEFRAME_ID,
  bigLots: DEFAULT_BIG_LOTS,
  historyDays: 3,
  // The measured rule, not the convenient one. Costs one profile call over tape
  // that is already in memory, so the only reason to turn it off is that you
  // don't want the levels on the chart.
  composite: "balance",
  // The whole day in front of the bell, not just the day session: an overnight
  // shelf is a price the market has already agreed on, and a composite that
  // skips it draws levels through volume it pretends didn't trade.
  compositeSpan: "globex",
  // His own defaults, and off by default in the legend — see chartPrefs.
  modernVwap: { ...DEFAULT_MODERN_VWAP },
  nodeProm: DEFAULT_NODE_PROM,
  // The measured numbers, so an untouched chart is the write-up's chart. The
  // layer itself starts hidden (the indicator toggles' own default), because the
  // events are a proxy that measured negative against the very levels they sit
  // next to and ~19 a session drawn by default would read as a signal by sheer
  // presence.
  eventTuning: { ...DEFAULT_EVENT_TUNING },
  eventLabelSt: 1.5,
  eventFill: 0.2,
  // On: it costs no chart room (it draws inside gutters that are already there)
  // and it is the reading the bands can't give.
  eventMarginal: true,
  indicators: true,
  // Away by default: the tape is the thing, and the ticket is two keystrokes
  // (w/s) or a click on the chart. Live starts unpinned too, and remembers its
  // own answer (live.chartKnobs).
  railPinned: false,
  // One pane, so nothing about the page changes until it is asked for.
  panes: 1,
  // Five minutes against a one-minute default: far enough apart to be a second
  // read of the session rather than the same chart at a different zoom.
  paneTf: "5m",
  splitPct: 60,
};

const ORDER_TYPES: OrderType[] = ["market", "limit", "stop"];

const int = (v: unknown, min: number, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) && v >= min ? Math.floor(v) : fallback;

/** One of the offered values, or the default. Same rule as the speed and the
 *  timeframe: a saved number that isn't on the list would leave its picker
 *  showing a blank, and a hand-edited localStorage is not a reason to draw a
 *  chart nobody can describe. */
const pick = <T>(v: unknown, options: readonly T[], fallback: T): T =>
  options.includes(v as T) ? (v as T) : fallback;

/** The ten event knobs, each validated against its own list. Stored as one
 *  object because it is handed to the engine as one. */
function eventTuning(raw: unknown): EventTuning {
  const d = DEFAULT_EVENT_TUNING;
  if (!raw || typeof raw !== "object") return { ...d };
  const s = raw as Partial<Record<keyof EventTuning, unknown>>;
  const o = EVENT_TUNING_OPTIONS;
  return {
    sweepLots: pick(s.sweepLots, o.sweepLots, d.sweepLots),
    burstGapS: pick(s.burstGapS, o.burstGapS, d.burstGapS),
    burstSpanPts: pick(s.burstSpanPts, o.burstSpanPts, d.burstSpanPts),
    burstLots: pick(s.burstLots, o.burstLots, d.burstLots),
    absorbWinMs: pick(s.absorbWinMs, o.absorbWinMs, d.absorbWinMs),
    absorbMult: pick(s.absorbMult, o.absorbMult, d.absorbMult),
    absorbMinWindows: pick(s.absorbMinWindows, o.absorbMinWindows, d.absorbMinWindows),
    absorbScope: pick(s.absorbScope, ["rth", "all"] as const, d.absorbScope),
    absorbBaseline: pick(s.absorbBaseline, o.absorbBaseline, d.absorbBaseline),
    absorbMerge: typeof s.absorbMerge === "boolean" ? s.absorbMerge : d.absorbMerge,
  };
}

export function loadSimPrefs(): SimPrefs {
  const d = DEFAULT_SIM_PREFS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...d, modernVwap: { ...d.modernVwap } };
    const s = JSON.parse(raw) as Partial<Record<keyof SimPrefs, unknown>>;
    return {
      root: typeof s.root === "string" && s.root ? s.root : d.root,
      startTime: typeof s.startTime === "string" && /^\d{1,2}:\d{2}$/.test(s.startTime) ? s.startTime : d.startTime,
      // An unknown speed would leave the transport's <select> showing a blank.
      speed: SIM_SPEEDS.includes(s.speed as number) ? (s.speed as number) : d.speed,
      size: int(s.size, 1, d.size),
      // Zero is a real value here — the leg is off — so the floor is 0, not 1.
      stopTicks: int(s.stopTicks, 0, d.stopTicks),
      targetTicks: int(s.targetTicks, 0, d.targetTicks),
      // Zero is meaningful for all three: the trail off, the step defaulting to
      // one rung per trail distance, the first rung on the entry itself.
      trailTicks: int(s.trailTicks, 0, d.trailTicks),
      trailStepTicks: int(s.trailStepTicks, 0, d.trailStepTicks),
      trailBeTicks: int(s.trailBeTicks, 0, d.trailBeTicks),
      trailBeOnly: typeof s.trailBeOnly === "boolean" ? s.trailBeOnly : d.trailBeOnly,
      orderType: ORDER_TYPES.includes(s.orderType as OrderType) ? (s.orderType as OrderType) : d.orderType,
      blind: typeof s.blind === "boolean" ? s.blind : d.blind,
      // A retired timeframe id would leave the picker showing a blank, same as
      // an unknown speed.
      timeframe: TIMEFRAMES.some((t) => t.id === s.timeframe) ? (s.timeframe as string) : d.timeframe,
      // Same reason as the speed: a threshold that isn't one of the presets
      // would leave the picker blank.
      bigLots: BIG_LOT_OPTIONS.includes(s.bigLots as number) ? (s.bigLots as number) : d.bigLots,
      historyDays: HISTORY_DAY_OPTIONS.includes(s.historyDays as number)
        ? (s.historyDays as number)
        : d.historyDays,
      composite: COMPOSITE_RULES.includes(s.composite as CompositeRule)
        ? (s.composite as CompositeRule)
        : d.composite,
      modernVwap: modernVwapParams(s.modernVwap),
      compositeSpan: COMPOSITE_SPANS.includes(s.compositeSpan as CompositeSpan)
        ? (s.compositeSpan as CompositeSpan)
        : d.compositeSpan,
      nodeProm: NODE_PROM_OPTIONS.includes(s.nodeProm as number)
        ? (s.nodeProm as number)
        : d.nodeProm,
      eventTuning: eventTuning(s.eventTuning),
      eventLabelSt: pick(s.eventLabelSt, EVENT_LABEL_ST_OPTIONS, d.eventLabelSt),
      eventFill: pick(s.eventFill, EVENT_FILL_OPTIONS, d.eventFill),
      eventMarginal: typeof s.eventMarginal === "boolean" ? s.eventMarginal : d.eventMarginal,
      indicators: typeof s.indicators === "boolean" ? s.indicators : d.indicators,
      railPinned: typeof s.railPinned === "boolean" ? s.railPinned : d.railPinned,
      // Only the layouts that exist. A stored 3 from a later version would
      // otherwise render one pane and silently drop the rest of the setting.
      panes: s.panes === 2 ? 2 : d.panes,
      paneTf: TIMEFRAMES.some((t) => t.id === s.paneTf) ? (s.paneTf as string) : d.paneTf,
      splitPct: clampSplit(s.splitPct, d.splitPct),
    };
  } catch {
    return { ...d, modernVwap: { ...d.modernVwap } };
  }
}

/** Keep the divider away from both edges — see `SimPrefs.splitPct`. */
export function clampSplit(v: unknown, fallback = DEFAULT_SIM_PREFS.splitPct): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return fallback;
  return Math.min(80, Math.max(20, Math.round(v)));
}

export function saveSimPrefs(p: SimPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the ticket still works, the settings just won't stick.
  }
}

// --- the Live chart's reading knobs ----------------------------------------
// The same knobs the Simulator hangs off its legend rows, as the Live page
// carries them — plus the page posture `sim.prefs` keeps for the replay (the
// timeframe, the indicator strip, the rail pin), which Live would otherwise
// forget on every reload. Its own store rather than a corner of `sim.prefs`
// because the two pages read different tapes and a threshold tuned for a
// replay study is not automatically the one you watch a live session at — but
// the option lists and the validators are one set, which is what keeps the two
// pickers from drifting. All of these are reading choices: none can move a
// clock, fill an order, or reach a broker.

export interface LiveChartKnobs {
  bigLots: number;
  nodeProm: number;
  composite: CompositeRule;
  compositeSpan: CompositeSpan;
  eventTuning: EventTuning;
  eventLabelSt: number;
  eventFill: number;
  eventMarginal: boolean;
  /** Modern VWAP's parameters — the replay's field, kept separately so the two
   *  pages can be looking at different settings of a study layer. */
  modernVwap: ModernVwapParams;
  /** Which bar the chart draws (lib/timeframes) — a bucketing rule over the
   *  tape, same as the replay's, and like it unable to touch a fill. */
  timeframe: string;
  /** Whether the day-scale indicator strip is showing. */
  indicators: boolean;
  /** Whether the rail panel reserves layout width instead of opening over the
   *  tape. */
  railPinned: boolean;
}

const LIVE_KNOBS_KEY = "live.chartKnobs";

export const DEFAULT_LIVE_CHART_KNOBS: LiveChartKnobs = {
  bigLots: DEFAULT_BIG_LOTS,
  nodeProm: DEFAULT_NODE_PROM,
  // The measured rule, same as the replay's default — "off" is what the page
  // renders anyway when no prior days are drawn.
  composite: "balance",
  compositeSpan: "globex",
  modernVwap: { ...DEFAULT_MODERN_VWAP },
  eventTuning: { ...DEFAULT_EVENT_TUNING },
  eventLabelSt: 1.5,
  eventFill: 0.2,
  eventMarginal: true,
  // Tick bars, not the replay's 1m: live is watched print by print, and a tick
  // bar keeps moving on a quiet market where a minute bar would sit still.
  timeframe: "500t",
  indicators: true,
  // Away by default, same as the replay's: the feed lays over the tape rather
  // than taking a column off it — see the page's own comment on `railView`.
  railPinned: false,
};

export function loadLiveChartKnobs(): LiveChartKnobs {
  const d = DEFAULT_LIVE_CHART_KNOBS;
  try {
    const raw = localStorage.getItem(LIVE_KNOBS_KEY);
    if (!raw) return { ...d, eventTuning: { ...d.eventTuning }, modernVwap: { ...d.modernVwap } };
    const s = JSON.parse(raw) as Partial<Record<keyof LiveChartKnobs, unknown>>;
    return {
      bigLots: pick(s.bigLots, BIG_LOT_OPTIONS, d.bigLots),
      nodeProm: pick(s.nodeProm, NODE_PROM_OPTIONS, d.nodeProm),
      composite: pick(s.composite, COMPOSITE_RULES, d.composite),
      compositeSpan: pick(s.compositeSpan, COMPOSITE_SPANS, d.compositeSpan),
      modernVwap: modernVwapParams(s.modernVwap),
      eventTuning: eventTuning(s.eventTuning),
      eventLabelSt: pick(s.eventLabelSt, EVENT_LABEL_ST_OPTIONS, d.eventLabelSt),
      eventFill: pick(s.eventFill, EVENT_FILL_OPTIONS, d.eventFill),
      eventMarginal: typeof s.eventMarginal === "boolean" ? s.eventMarginal : d.eventMarginal,
      // A retired id would leave the picker blank — the sim.prefs rule.
      timeframe: TIMEFRAMES.some((t) => t.id === s.timeframe) ? (s.timeframe as string) : d.timeframe,
      indicators: typeof s.indicators === "boolean" ? s.indicators : d.indicators,
      railPinned: typeof s.railPinned === "boolean" ? s.railPinned : d.railPinned,
    };
  } catch {
    return { ...d, eventTuning: { ...d.eventTuning }, modernVwap: { ...d.modernVwap } };
  }
}

export function saveLiveChartKnobs(k: LiveChartKnobs): void {
  try {
    localStorage.setItem(LIVE_KNOBS_KEY, JSON.stringify(k));
  } catch {
    // Private mode / quota — the knobs still work, they just won't stick.
  }
}

// --- the Live page's order ticket ------------------------------------------
// Size and the bracket, as one object, in one store.
//
// **There used to be two of these** and that was the bug: the page held its own
// size/stop/target for every chart gesture (space+click, q/w/s, the dock, the
// long-press ticket) while the routing panel's order pad held a second,
// independent copy. Setting the bracket on one and placing from the other sent
// an order nobody had described — which is the one thing an order path must
// never do. There is one ticket now, owned by the page and handed to the panel.
//
// Persisted for the same reason the reading knobs are: 50/120 is a default, not
// a decision, and re-typing a decision every reload is how it ends up wrong on
// the reload you didn't check.

export interface LiveTicket {
  size: number;
  /** 0 means no stop. Both legs are optional, and both mean "not sent". */
  stopTicks: number;
  targetTicks: number;
  /** Ticks of profit before Rithmic starts ratcheting the stop. 0 is off. Real
   *  accounts only — the paper blotter does not imitate the ratchet. */
  trailTicks: number;
  /** Ticks of profit before the breakeven jump fires. 0 is off. */
  beTicks: number;
  /** How much profit that jump locks in, always in the trade's favour. */
  beLock: number;
}

const LIVE_TICKET_KEY = "live.ticket";

/** The bracket the operating plan trades, and the one the guardrails accept
 *  (routing.Guards: a 40–60 tick stop, a target of 100+). Paper gets the same
 *  defaults on purpose — practising an 80-tick target the funded account
 *  refuses is practising the wrong thing.
 *
 *  The two exit automatics are off: an exit that moves on its own is a decision,
 *  not something to inherit from a default. Once *made* it sticks, like the
 *  rest of the ticket. */
export const DEFAULT_LIVE_TICKET: LiveTicket = {
  size: 1,
  stopTicks: 50,
  targetTicks: 120,
  trailTicks: 0,
  beTicks: 0,
  beLock: 1,
};

/** A stored number, or the default. Non-finite, negative and NaN all fall back
 *  rather than reaching a draft: every one of these ends up in an order. */
const tick = (v: unknown, d: number, min = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) && n >= min ? Math.floor(n) : d;
};

export function loadLiveTicket(): LiveTicket {
  const d = DEFAULT_LIVE_TICKET;
  try {
    const raw = localStorage.getItem(LIVE_TICKET_KEY);
    if (!raw) return { ...d };
    const s = JSON.parse(raw) as Partial<Record<keyof LiveTicket, unknown>>;
    return {
      size: tick(s.size, d.size, 1),
      stopTicks: tick(s.stopTicks, d.stopTicks),
      targetTicks: tick(s.targetTicks, d.targetTicks),
      trailTicks: tick(s.trailTicks, d.trailTicks),
      beTicks: tick(s.beTicks, d.beTicks),
      // Never 0 with a trigger set: a 0 is a proto3 default and never reaches
      // the wire, so the server refuses the pair. See `OrderDraft.be_ticks`.
      beLock: tick(s.beLock, d.beLock, 1),
    };
  } catch {
    return { ...d };
  }
}

export function saveLiveTicket(t: LiveTicket): void {
  try {
    localStorage.setItem(LIVE_TICKET_KEY, JSON.stringify(t));
  } catch {
    // Private mode / quota — the ticket still works, it just won't stick.
  }
}
