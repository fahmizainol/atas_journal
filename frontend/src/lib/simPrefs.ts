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
import { MAX_PANES, clampRatio, isLayoutId, type LayoutId } from "./paneLayout";

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

/**
 * What both chart pages remember about *reading* a tape.
 *
 * Exactly the fields Live carries, and exactly the subset of the replay's that
 * cannot touch a fill: which bar the chart draws, what counts as a big print,
 * how the profiles are composited, what selects a tape event, and how the panes
 * are arranged. It was two identical field lists and two identical validator
 * chains before, on the reasoning that the two pages read different tapes — true
 * of the *values*, which is why the two stores are still separate keys, and
 * never true of the shape.
 *
 * **The stores are untouched.** `sim.prefs` and `live.chartKnobs` hold what they
 * always held; this is a type and a loader, not a migration.
 */
export interface ChartReadingPrefs {
  /** Lots a sweep must exceed to be marked on the chart. A reading choice: it
   *  changes which prints are drawn, never what fills. */
  bigLots: number;
  /** Prominence floor for the HVN/LVN node reader (0 = off). Read off the
   *  composite and off the developing NY profile alike — one knob, because it is
   *  one question ("how big does a hump have to be") asked of two profiles. */
  nodeProm: number;
  /** How the context days are composited into one profile, if at all. Costs
   *  nothing extra: the composite is built from tape already loaded and drawn. */
  composite: CompositeRule;
  /** How much of each context day goes in — the day session, or the Globex
   *  session in front of it as well. */
  compositeSpan: CompositeSpan;
  /** What selects a tape event. One setting that is really ten, because "is
   *  this size arriving or defending" is not a question with one threshold. The
   *  rows are shown or hidden by the chart's own indicator toggles. */
  eventTuning: EventTuning;
  /** Strength at which a band carries its lot count (0 = never). */
  eventLabelSt: number;
  /** Fill alpha of the band wash at strength 1 (0 = outline only). */
  eventFill: number;
  /** Whether the events also draw as a marginal down the volume profiles'
   *  gutters — the "where did all that size go" reading, which is a different
   *  question from the bands on the candles. */
  eventMarginal: boolean;
  /** Modern VWAP's parameters, stored as one object because the indicator takes
   *  them as one. A drawing choice: it reads bars already on the chart. */
  modernVwap: ModernVwapParams;
  /** Which bar the chart draws (see lib/timeframes). Purely how the tape is
   *  bucketed for display — it can't change a fill, so it's safe to carry. */
  timeframe: string;
  /** Whether the day-scale indicator strip is showing. Chart real estate, so it
   *  collapses to a pill. */
  indicators: boolean;
  /** Whether the ticket/blotter rail reserves layout width instead of opening
   *  over the tape. Carried between visits because it is a statement about how
   *  you work, and that outlives a session. */
  railPinned: boolean;
  /** How the panes are arranged. `one` is the page as it always was; the rest
   *  put two, three or four charts on the same tape, each with its own engine on
   *  its own bucketing. See lib/paneLayout. */
  layout: LayoutId;
  /** Each pane's bucketing, indexed by pane. Held for `MAX_PANES` however many
   *  are on screen, so switching 1 -> 2x2 -> 1 gives every pane back the
   *  bucketing it had. Pane 0's entry is unused — the page's own `timeframe` is
   *  pane 0's, and a second copy would be a second source of truth. */
  paneTfs: string[];
  /** Where the vertical divider sits, as the left column's percentage of the
   *  width, and the horizontal one as the top row's percentage of the height.
   *  Clamped well short of every edge: a pane dragged to nothing is a pane you
   *  cannot get back by dragging. */
  splitPct: number;
  splitPctY: number;
  /** Whether the panes share one crosshair and one right edge (lib/paneLink).
   *  A reading choice like the layout itself — it moves viewports, never a
   *  fill. */
  linkOn: boolean;
  /** Which panes take part, indexed like `paneTfs`. The global switch above is
   *  the one you reach for; this is the per-pane `⇄` badge. */
  paneLinked: boolean[];
  /** Whether the tool rail reserves a column beside the charts or floats over
   *  the tape. Pinned by default — 38px off the width beats covering candles on
   *  four panes — but on one pane the column is a straight loss. */
  toolsPinned: boolean;
}

/** The replay's own settings: the ticket it trades with, the clock it runs on,
 *  and the two pieces of page posture Live has no equivalent of. Everything
 *  here either reaches a fill or drives a clock, which is exactly why none of it
 *  is in `ChartReadingPrefs`. */
export interface SimPrefs extends ChartReadingPrefs {
  root: string;
  /** ET wall clock the replay starts at, "HH:MM". */
  startTime: string;
  speed: number;
  size: number;
  /** Trade the root's micro instead of the mini — MNQ against the NQ tape (see
   *  lib/contracts). Not a reading choice: it re-prices every fill, so the P&L,
   *  the risk chips and what the guardrails refuse all move with it. Remembered
   *  because which contract you practise is an account decision, not something
   *  to re-pick each sitting. */
  micro: boolean;
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
  /** How many prior sessions to draw to the left of the replay. Each one is a
   *  whole tape (a few MB and a million prints), so this is the one setting here
   *  that costs something — hence a short list of choices rather than a box.
   *
   *  Replay-only because Live has no "before this session" to draw: it is
   *  watching the session it is in. */
  historyDays: number;
  /** Whether the transport row is in flow at the foot of the page.
   *
   *  On by default and worth its ~34px: it is the instrument a replay is driven
   *  with, not chrome you occasionally want. But the keys reach all of it — k
   *  play/pause, `,` and `.` step, the speed is a setting you land on once — so
   *  a session spent reading rather than scrubbing can have the pixels back, and
   *  on a laptop that row is a real fraction of the tape. Sticky for the same
   *  reason `railPinned` is: it is a statement about how you work.
   *
   *  The toggle is on the top bar rather than the transport itself — a hide
   *  button that goes away with the thing it hid leaves nothing to press.
   *
   *  Replay-only for the plainest possible reason: there is no clock to
   *  transport on a live tape. */
  transportOpen: boolean;
}

const KEY = "sim.prefs";

export const DEFAULT_SIM_PREFS: SimPrefs = {
  root: "NQ",
  startTime: "09:30",
  speed: 30,
  size: 1,
  // The mini, because that is what the tape is and what the funded account is
  // sized in. The micro is a deliberate choice, never a default.
  micro: false,
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
  // In flow, which is how the page has always opened.
  transportOpen: true,
  // One pane, so nothing about the page changes until it is asked for.
  layout: "one",
  // Pane 0's slot is a placeholder (the page's own `timeframe` is pane 0's).
  // The rest run slower as they go: far enough apart to be a second and third
  // read of the session rather than the same chart at a different zoom.
  paneTfs: ["5m", "5m", "15m", "1h"],
  splitPct: 60,
  splitPctY: 55,
  // On: reading one moment at four bucketings is what the grid is *for*, and a
  // link nobody switched on is a feature nobody finds. It is one click away and
  // the answer sticks — which is the shape "a toggle, not a default" asks for.
  // With one pane it does nothing at all, so the single-chart page is unmoved.
  linkOn: true,
  paneLinked: [true, true, true, true],
  toolsPinned: true,
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

/**
 * The reading knobs, validated. One chain, both stores.
 *
 * Every rule in here was already written twice, identically, once per loader —
 * which is the duplication that actually costs something: a picker gains an
 * option, one list is updated, and the other page quietly falls back to its
 * default on a value the user can see in the first page's dropdown.
 *
 * `legacy` is the one asymmetry, and it stays an argument rather than becoming
 * a shared rule: only `sim.prefs` was ever written by a build that had
 * `panes: 1 | 2` instead of a layout id, so only it has a shape to translate.
 */
function readingPrefs(
  s: Partial<Record<string, unknown>>,
  d: ChartReadingPrefs,
  legacy?: (raw: Record<string, unknown>, fallback: LayoutId) => LayoutId,
): ChartReadingPrefs {
  return {
    // Same reason as the speed below: a threshold that isn't one of the presets
    // would leave the picker blank.
    bigLots: pick(s.bigLots, BIG_LOT_OPTIONS, d.bigLots),
    nodeProm: pick(s.nodeProm, NODE_PROM_OPTIONS, d.nodeProm),
    composite: pick(s.composite, COMPOSITE_RULES, d.composite),
    compositeSpan: pick(s.compositeSpan, COMPOSITE_SPANS, d.compositeSpan),
    eventTuning: eventTuning(s.eventTuning),
    eventLabelSt: pick(s.eventLabelSt, EVENT_LABEL_ST_OPTIONS, d.eventLabelSt),
    eventFill: pick(s.eventFill, EVENT_FILL_OPTIONS, d.eventFill),
    eventMarginal: typeof s.eventMarginal === "boolean" ? s.eventMarginal : d.eventMarginal,
    modernVwap: modernVwapParams(s.modernVwap),
    // A retired timeframe id would leave the picker showing a blank.
    timeframe: TIMEFRAMES.some((t) => t.id === s.timeframe) ? (s.timeframe as string) : d.timeframe,
    indicators: typeof s.indicators === "boolean" ? s.indicators : d.indicators,
    railPinned: typeof s.railPinned === "boolean" ? s.railPinned : d.railPinned,
    // Only the layouts that exist — an unknown id from a later version would
    // otherwise render nothing at all.
    layout: isLayoutId(s.layout)
      ? s.layout
      : (legacy?.(s as Record<string, unknown>, d.layout) ?? d.layout),
    paneTfs: paneTfs(s as Record<string, unknown>, d.paneTfs),
    splitPct: clampRatio(s.splitPct, d.splitPct),
    splitPctY: clampRatio(s.splitPctY, d.splitPctY),
    linkOn: typeof s.linkOn === "boolean" ? s.linkOn : d.linkOn,
    paneLinked: paneFlags(s.paneLinked, d.paneLinked),
    toolsPinned: typeof s.toolsPinned === "boolean" ? s.toolsPinned : d.toolsPinned,
  };
}

/** A defaults object nobody can mutate through. The two objects inside a set of
 *  reading prefs are handed straight to an engine and an indicator, and a store
 *  that returned the module's own DEFAULT_* objects would let a page edit the
 *  defaults for every later load. */
function freshReading<T extends ChartReadingPrefs>(d: T): T {
  return { ...d, eventTuning: { ...d.eventTuning }, modernVwap: { ...d.modernVwap } };
}

export function loadSimPrefs(): SimPrefs {
  const d = DEFAULT_SIM_PREFS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return freshReading(d);
    const s = JSON.parse(raw) as Partial<Record<keyof SimPrefs, unknown>>;
    return {
      ...readingPrefs(s, d, legacyLayout),
      root: typeof s.root === "string" && s.root ? s.root : d.root,
      startTime: typeof s.startTime === "string" && /^\d{1,2}:\d{2}$/.test(s.startTime) ? s.startTime : d.startTime,
      // An unknown speed would leave the transport's <select> showing a blank.
      speed: SIM_SPEEDS.includes(s.speed as number) ? (s.speed as number) : d.speed,
      size: int(s.size, 1, d.size),
      micro: typeof s.micro === "boolean" ? s.micro : d.micro,
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
      historyDays: pick(s.historyDays, HISTORY_DAY_OPTIONS, d.historyDays),
      transportOpen: typeof s.transportOpen === "boolean" ? s.transportOpen : d.transportOpen,
    };
  } catch {
    return freshReading(d);
  }
}

/** Keep the divider away from both edges — see `SimPrefs.splitPct`. */
export function clampSplit(v: unknown, fallback = DEFAULT_SIM_PREFS.splitPct): number {
  return clampRatio(v, fallback);
}

/** What a pref written before there were layouts meant.
 *
 *  The old shape was `panes: 1 | 2` — one chart, or a trading chart beside a
 *  context one, side by side. That is exactly `col2`, so a stored 2 comes back
 *  as the same arrangement rather than as the default; anyone who had the split
 *  on finds it still on. */
function legacyLayout(s: Record<string, unknown>, fallback: LayoutId): LayoutId {
  return s.panes === 2 ? "col2" : fallback;
}

/** Per-pane bucketings, padded and validated to `MAX_PANES`.
 *
 *  The old single `paneTf` was the context pane's, which is pane 1 — so a stored
 *  one lands there and the rest keep their defaults. Anything unrecognised (a
 *  timeframe that has since been removed) falls back per pane rather than
 *  discarding the whole array. */
function paneTfs(s: Record<string, unknown>, d: string[]): string[] {
  const known = (v: unknown): v is string => TIMEFRAMES.some((t) => t.id === v);
  const stored = Array.isArray(s.paneTfs) ? (s.paneTfs as unknown[]) : [];
  const out = Array.from({ length: MAX_PANES }, (_, i) => (known(stored[i]) ? stored[i] : d[i]));
  if (!stored.length && known(s.paneTf)) out[1] = s.paneTf;
  return out;
}

/** A per-pane boolean array, padded and validated to `MAX_PANES` — same rule as
 *  `paneTfs`: a short or hand-edited array fills from the default per slot
 *  rather than being thrown away whole. */
function paneFlags(raw: unknown, d: boolean[]): boolean[] {
  const stored = Array.isArray(raw) ? (raw as unknown[]) : [];
  return Array.from({ length: MAX_PANES }, (_, i) =>
    typeof stored[i] === "boolean" ? (stored[i] as boolean) : (d[i] ?? true),
  );
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

/** Live carries exactly the reading knobs and nothing else — no ticket, no
 *  clock, no context days. It *is* the shared shape, which is the honest way to
 *  say it: an alias rather than a copy that has to be kept equal by hand. */
export type LiveChartKnobs = ChartReadingPrefs;

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
  // One pane, so nothing about the page changes until it is asked for. The
  // bucketings run slower as they go, from the tick bar Live watches on.
  layout: "one",
  paneTfs: ["500t", "1m", "5m", "15m"],
  splitPct: 60,
  splitPctY: 55,
  linkOn: true,
  paneLinked: [true, true, true, true],
  toolsPinned: true,
};

export function loadLiveChartKnobs(): LiveChartKnobs {
  const d = DEFAULT_LIVE_CHART_KNOBS;
  try {
    const raw = localStorage.getItem(LIVE_KNOBS_KEY);
    if (!raw) return freshReading(d);
    // No legacy layout translation: `live.chartKnobs` never existed in a build
    // that stored `panes` instead of a layout id.
    return readingPrefs(JSON.parse(raw) as Record<string, unknown>, d);
  } catch {
    return freshReading(d);
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

const LIVE_CONTRACT_KEY = "live.contract";

/** The front month when this was written, and only ever a starting point: the
 *  first successful connect stores what it connected to, so the quarterly roll
 *  is something you type once rather than a code edit. */
export const DEFAULT_LIVE_CONTRACT = "NQU6";

/**
 * The raw contract Live connects to, uppercase.
 *
 * Validated on the way out rather than trusted, because the Live page now
 * *autostarts* off this value: a stored root ("NQ") or a hand-edited blank would
 * turn every visit into a 422 with nothing on screen saying which stale string
 * caused it. The shape checked is the one the connect button's own guard uses —
 * four or more alphanumerics — and the API rejects roots regardless.
 */
export function loadLiveContract(): string {
  try {
    const raw = (localStorage.getItem(LIVE_CONTRACT_KEY) ?? "").trim().toUpperCase();
    return /^[A-Z0-9]{4,}$/.test(raw) ? raw : DEFAULT_LIVE_CONTRACT;
  } catch {
    return DEFAULT_LIVE_CONTRACT;
  }
}

export function saveLiveContract(symbol: string): void {
  try {
    localStorage.setItem(LIVE_CONTRACT_KEY, symbol.trim().toUpperCase());
  } catch {
    // Private mode / quota — the autostart falls back to the default next visit.
  }
}
