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

import { DEFAULT_BIG_LOTS } from "./replayEngine";
import type { OrderType } from "./replaySim";
import { DEFAULT_NODE_PROM } from "./volumeProfile";
import type { CompositeRule, CompositeSpan } from "./compositeProfile";
import { DEFAULT_TIMEFRAME_ID, TIMEFRAMES } from "./timeframes";

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

/** Strength floor for the event bands, in units of each kind's own threshold
 *  (150 lots for a burst, 3× the session's own concentration for an
 *  absorption). Zero is the layer off, which is where it starts: ~19 events a
 *  session is a busy chart, and nothing here is a signal. */
export const EVENT_STRENGTH_OPTIONS = [0, 1, 2, 3];

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
  /** Prominence floor for the HVN/LVN node reader (0 = off). Read off the
   *  composite and off the developing NY profile alike — one knob, because it is
   *  one question ("how big does a hump have to be") asked of two profiles. */
  nodeProm: number;
  /** Strength floor for the tape-event bands (0 = off). */
  eventStrength: number;
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
}

const KEY = "sim.prefs";

export const DEFAULT_SIM_PREFS: SimPrefs = {
  root: "NQ",
  startTime: "09:30",
  speed: 30,
  size: 1,
  stopTicks: 40,
  targetTicks: 80,
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
  nodeProm: DEFAULT_NODE_PROM,
  // Off. The events are a proxy that measured negative against the very levels
  // they sit next to, and ~19 a session drawn by default would read as a signal
  // by sheer presence.
  eventStrength: 0,
  indicators: true,
  // Away by default on Replay: the tape is the thing, and the ticket is two
  // keystrokes (w/s) or a click on the chart. Live pins it instead — there the
  // rail carries a signal feed you want in view, not a form you fill in.
  railPinned: false,
};

const ORDER_TYPES: OrderType[] = ["market", "limit", "stop"];

const int = (v: unknown, min: number, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) && v >= min ? Math.floor(v) : fallback;

export function loadSimPrefs(): SimPrefs {
  const d = DEFAULT_SIM_PREFS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...d };
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
      compositeSpan: COMPOSITE_SPANS.includes(s.compositeSpan as CompositeSpan)
        ? (s.compositeSpan as CompositeSpan)
        : d.compositeSpan,
      nodeProm: NODE_PROM_OPTIONS.includes(s.nodeProm as number)
        ? (s.nodeProm as number)
        : d.nodeProm,
      eventStrength: EVENT_STRENGTH_OPTIONS.includes(s.eventStrength as number)
        ? (s.eventStrength as number)
        : d.eventStrength,
      indicators: typeof s.indicators === "boolean" ? s.indicators : d.indicators,
      railPinned: typeof s.railPinned === "boolean" ? s.railPinned : d.railPinned,
    };
  } catch {
    return { ...d };
  }
}

export function saveSimPrefs(p: SimPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the ticket still works, the settings just won't stick.
  }
}
