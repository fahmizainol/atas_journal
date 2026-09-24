// The replay engine: decodes a session's delta-encoded tape into typed arrays
// and plays it back tick-by-tick into developing candles + developing anchored
// VWAP bands. Everything here is pure client-side compute — no chart, no React.
// The chart (ReplayChart) and the page drive it.
//
// The bar is a bucketing rule over the tape (lib/timeframes), which is why it
// can be changed while watching: `setTimeframe` then `snapshotTo` re-derives
// every layer from tick zero, exactly as a seek already does. Bar times are the
// engine's own currency — anything that has to line up with a bar asks
// `barTimeAt`, because with tick bars there is no formula from an instant to one.
//
// Design: a *forming* bar wiggles as ticks land, so the loop is bounded by the
// playback clock, not the tick rate. `advance(clock)` applies every tick up to
// the clock and returns just the changed tail (forming bar + any bars that
// closed this step) so the chart can `.update()` a handful of points per frame.
// `snapshotTo(clock)` rebuilds from scratch — used on load and on any seek
// (including rewinds), which is why seeking backward is coherent: the whole
// picture, bands included, is recomputed as of the new clock.
//
// VWAP σ matches the sim engine's tick-derived bands (journal.sim.vwap): a
// volume-weighted mean and a volume-weighted price stddev, developing from the
// anchor (Globex 18:00, NY 09:30) to each bar's close. The weekly anchor sits
// days before the tape, so it arrives as the three sums already behind it
// (journal.sim.weekly) and develops from there — the same seeding the strategy
// charts draw it with.
//
// The tape may carry more than the session: prior days can be glued in front of
// it (`concatTapes`) so the chart has context to the left. They are bars and
// nothing else — the playback, every anchor and every value area start at the
// session's first tick, which is what the engine's session index marks.
//
// Everything the chart draws that can only be known from the tape is computed
// here, so a replay and a strategy chart of the same session agree: the two
// developing value areas (journal.sim.profile), the Initial Balance
// (journal.sim.ib), the big trades (see `BigTrade`), the sweep-burst and
// absorption events (see `TapeEvent`), and the user-placed ⚓ anchor — all
// tick-exact, none of them reconstructed from bars.

import type { Timeframe } from "./timeframes";
import { Vwap, foldTick, openBar } from "./vwap";

/** Which tick store a session came out of.
 *
 *  `"cache"` is the Databento corpus the backtests read; `"live"` is a session
 *  recorded off Rithmic. They are separate stores on purpose (decision 3 of
 *  docs/live-shadow-plan.md) and the corpus is pinned at the data budget, so in
 *  practice this splits the calendar: bought up to 2026-06-30, recorded after. */
export type TickSource = "cache" | "live";

export interface SessionPayload {
  symbol: string;
  root: string;
  date: string;
  tz: string;
  tick_size: number;
  point_value: number;
  n: number;
  t0: number;
  dt: number[];
  price0: number;
  dp: number[];
  size: number[];
  side: string;
  session_start_ms: number;
  session_end_ms: number;
  rth_open_ms: number;
  rth_close_ms: number;
  default_start_ms: number;
  /** The 18:00 ET Globex open, where the night's VWAP *should* be anchored. */
  globex_open_ms: number;
  /** Where it actually is: the first overnight print the tape carries. Equal to
   *  `globex_open_ms` on a bought day and on a recorded one the feed backfilled
   *  whole; later than it when a Rithmic replay came back short, which it can do
   *  without erroring. The gap is the only thing that says the band is anchored
   *  mid-night, since a wrongly-anchored VWAP draws like a right one. */
  globex_anchor_ms: number | null;
  /** Which store served this session — see `SimDay.source`. Optional so a
   *  payload from an older server still decodes. */
  source?: TickSource;
  /** (Σv, Σpv, Σp²v) already behind the weekly anchor when this session's Globex
   *  open arrives — the week's earlier sessions, collapsed to the three sums the
   *  accumulation needs (journal.sim.weekly). Null when there is no honest
   *  weekly line to draw: no overnight tape, or a hole in the week. */
  weekly_seed: number[] | null;
  /** The weekly volume-at-price behind this session's Globex open, on the tick
   *  grid — seeds the weekly LevelHist the way `weekly_seed` seeds the weekly
   *  Vwap. Null on the same honesty rules (no night / a hole in the week), and
   *  optional so older payload shapes stay valid. */
  weekly_hist_seed?: { min: number; counts: number[] } | null;
  has_overnight: boolean;
  has_post: boolean;
  /** The prior-days context the tape cannot contain. Optional so a payload from
   *  an older server still decodes. */
  context?: SessionContext | null;
}

/** What the session knows about the sessions *before* it, plus the constants the
 *  Simulator's calibration indicators are cut at.
 *
 *  Everything here is knowable at the open — `adr14` is a mean of the fourteen
 *  days that already closed — so none of it is lookahead inside a replay. The
 *  constants come down with it rather than being written out again in TypeScript
 *  because they are measurements (vol-clock §10c), and a second copy of a
 *  measurement is a copy that can go stale. */
export interface SessionContext {
  /** Mean RTH day range (points) of the 14 sessions before this one. Null for a
   *  day outside the saved IB study, and for the first fortnight of it — there
   *  is no denominator then, and the indicators draw nothing rather than guess. */
  adr14: number | null;
  adr_source: { run_id: string; start: string; end: string; ib_minutes: number } | null;
  /** IB window the study measured, in minutes. The engine develops its own IB at
   *  `IB_MINUTES`; the width buckets are only meaningful where the two agree. */
  ib_minutes: number;
  /** Pinned narrow/mid/wide edges in ADR units. */
  ib_width_edges: [number, number];
  /** New range a session adds after the IB completes, in ADR units. */
  post_ib_add_x: number;
}

export interface Bar {
  time: number; // epoch seconds of the local wall clock (gap-collapsing)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  /** This bar's own tick VWAP and tick variance, kept in step with `volume` by
   *  `vwap.foldTick`. Why they are here rather than derived on demand: Modern
   *  VWAP and the Dynamic Swing VWAP re-anchor at arbitrary bars and recompute
   *  whole on every bar close, so re-walking the tape for each would be O(ticks)
   *  per redraw. These make it O(bars), and make the replay chart's VWAPs the
   *  same arithmetic as the journal charts', which get the identical pair over
   *  the wire from `journal.sim.vwap.bar_moments`. */
  tvwap: number;
  tvar: number;
  /** Tick indices this bar was built from, inclusive. `i1` keeps growing while
   *  the bar is still forming. The chart profiles an arbitrary bar range by
   *  scanning the tape between two bars' indices, so it never needs a per-bar
   *  footprint of its own. */
  i0: number;
  i1: number;
}

export interface BandPt {
  time: number;
  mid: number;
  u1: number;
  l1: number;
  u2: number;
  l2: number;
}

/** Developing value area as of a bar's close — the tape's own levels, not row
 *  midpoints (there are no rows: the histogram's bins *are* tick levels). */
export interface ProfilePt {
  time: number;
  poc: number;
  vah: number;
  val: number;
}

/** The Initial Balance as it stands at the current clock. Unlike the strategy
 *  charts' overlay (which only exists for a completed window) this one develops:
 *  a replay is a session in progress, and watching the IB form is the point.
 *  `complete` says whether the clock has passed the end of the window. */
export interface IbBox {
  high: number;
  low: number;
  start: number; // bar time of the bell
  formed: number; // bar time the window completes at
  complete: boolean;
}

/** The session's own high/low so far, RTH only.
 *
 *  RTH only because that is what it gets compared against: `adr14` is a mean of
 *  RTH day ranges, so a range that counted the overnight would be measured with
 *  the wrong ruler. Null before the bell. */
export interface RangeBox {
  high: number;
  low: number;
}

export interface Tape {
  n: number;
  t: Float64Array; // ms, absolute local-wall epoch
  price: Float64Array;
  /** Price as an integer tick-grid index (price / tickSize). The profile bins on
   *  this directly — it's what the tape was delta-encoded in to begin with, so
   *  binning costs no division and no rounding drift. */
  level: Int32Array;
  size: Int32Array;
  /** Aggressor: 1='A' (sell — hit the bid), 2='B' (buy — lifted the offer), 0='N'.
   *  Measured off the cache rather than read off the vendor doc: 'B' prints sit
   *  ~0.35pt *above* the local mid and 'A' the same distance below, so 'B' is the
   *  buyer paying up. (api/sim_charts.py signs its CVD the same way.) */
  side: Uint8Array;
  tickSize: number;
  pointValue: number;
}

/** Aggressor codes, as the tape carries them. */
export const SIDE_SELL = 1; // 'A'
export const SIDE_BUY = 2; // 'B'

/**
 * A trade big enough to mark — one print, or the burst of fills one order was
 * worked through the book as.
 *
 * Size arrives in bursts: over the last week of cached NQ sessions there are
 * 5-59 single prints over 50 lots in a session but 29-96 *sweeps* over 50, so a
 * per-print threshold shows a fraction of the size that actually traded. A
 * sweep is consecutive same-side fills inside `SWEEP_GAP_MS` that stay within
 * `SWEEP_SPAN_TICKS` of where the run began.
 */
export interface BigTrade {
  /** Tick index the run started at — the event's identity across re-emissions,
   *  since a run keeps growing while its fills land. */
  idx: number;
  /** Bar time (epoch seconds) of the bar the latest fill landed in. */
  time: number;
  /** Price of the latest fill — where the order has worked to. */
  price: number;
  lots: number;
  fills: number;
  buy: boolean;
}

/** Same aggregation the demo page uses (demo/big_trades_demo.py), so a sweep
 *  means the same thing in the replay as it does in the write-up. */
const SWEEP_GAP_MS = 250;
const SWEEP_SPAN_TICKS = 4;
/** Lots a sweep has to *exceed* to be marked. The Simulator makes this a
 *  setting; this is what it starts at. */
export const DEFAULT_BIG_LOTS = 50;

/**
 * An event on the tape big enough to be worth seeing as a *shape* rather than a
 * print — the two proxies demo/composite_profile_demo.py stands in with for the
 * MBO step this data can't reach (docs/research/pulcini-scalper-podcast-2026-08.md).
 *
 *   sweep    aggressive size arriving at once: big sweeps clustered in time and
 *            price. The stop-run / initiative half.
 *   absorb   size trading with nowhere to go: a window whose volume per point
 *            traversed runs far above the session's own. The iceberg half.
 *
 * Neither is the MBO label, and neither is a signal — measured over 40 and 120
 * sessions both land *further* from a frozen composite's levels than the
 * session's own volume-weighted tape does, and the sign never flips. They are
 * here to be read as shape: the band spans the prices the event printed across,
 * so a tall band is size that walked and a flat one is size that went nowhere.
 */
export interface TapeEvent {
  kind: "sweep" | "absorb";
  /** Tick index the event began at. With `kind`, the event's identity across
   *  re-emissions — like a sweep, an event keeps growing while it happens. */
  idx: number;
  /** Bar times (epoch seconds) of the first and last print in it. */
  from: number;
  to: number;
  /** The price band it printed across. */
  lo: number;
  hi: number;
  lots: number;
  /** Strength in units of the threshold that selected it, so it is ≥ 1 and
   *  comparable across the two kinds. */
  st: number;
  /** Which side carried the majority of the lots. */
  buy: boolean;
  /** The buy side's share of `lots` — `buy` is the majority's sign, this is its
   *  degree. A 52/48 fight over one price and a clean one-sided run are very
   *  different reads carrying the same `buy`, so the split travels with the
   *  event and the band desaturates as it approaches even. */
  buyLots: number;
  /** Still growing: more of the tape may yet join it. Cleared the moment the
   *  clusterer lets the event go — a break, or a clock far enough past its last
   *  member that nothing still to come could join. */
  open: boolean;
  /** Sweeps in the burst / 15s windows in the absorption. */
  n: number;
}

/**
 * What selects an event, as settings rather than constants.
 *
 * They *start* at the demo pages' numbers, so an untouched chart means the same
 * thing as the write-up — but which sweeps are one order being worked, and how
 * concentrated is concentrated, are facts about the instrument and the day, not
 * about the code. The same argument the big-trade threshold has always had.
 *
 * A change here re-derives the whole tape (`setEventTuning` then `snapshotTo`,
 * the path a timeframe change takes): none of this is a filter over what was
 * already published — a wider gap clusters sweeps that were separate bursts, a
 * different window re-medians the session. Compare only within one setting: an
 * event's strength is in units of the threshold that selected it.
 */
export interface EventTuning {
  /** Lots a single sweep must reach to count toward a burst. Deliberately not
   *  the big-trade threshold: that one is a reading choice about which prints to
   *  mark, this one is what the clusterer is allowed to see. */
  sweepLots: number;
  /** Big sweeps this close in time join one burst … */
  burstGapS: number;
  /** … if they also stay this close in price. */
  burstSpanPts: number;
  /** Lots a burst needs before it is published — strength 1.0. Below it the
   *  burst still accumulates; it simply hasn't happened yet. */
  burstLots: number;
  /** The window absorption is measured over. What "went nowhere" means in time:
   *  short finds jabs, long finds shelves. */
  absorbWinMs: number;
  /** Concentration this many × the baseline median = strength 1.0. Selects the
   *  window *and* decides which adjacent hot windows merge, so it shapes the
   *  bands as well as choosing them. */
  absorbMult: number;
  /** Windows that must have closed before the median means anything. Until it is
   *  met nothing is scored at all. */
  absorbMinWindows: number;
  /** Which tape the windows are measured over. `rth` restarts the baseline at
   *  the bell — the overnight trades a fraction of the volume through a fraction
   *  of the range, so a median across both has the open firing on every window.
   *  `all` scores the night too, on one baseline, and is a different instrument. */
  absorbScope: "rth" | "all";
  /** The baseline: 0 = every window that has closed so far, N = the last N only.
   *  Session-to-date answers "concentrated for today"; a rolling window answers
   *  "concentrated for right now", which drifts with the regime. */
  absorbBaseline: number;
  /** Whether adjacent hot windows read as one tall event or as several. */
  absorbMerge: boolean;
}

/**
 * The measured defaults: `demo/big_trades_demo.py` for the burst half and
 * `demo/composite_profile_demo.py` for the absorption half.
 *
 * Absorption is scored *relative to the session*, never in absolute points: the
 * same band means opposite things in a quiet and a violent regime (measured:
 * median 15s RTH range 4.75-6.00pt on 2025-26 NQ, and an absolute
 * 60s/≤4pt/≥900-lot rule fired zero times in three sessions). Concentration is
 * lots per point traversed, scored against the median of the windows that have
 * already closed — the demo's whole-session median is lookahead in a replay, so
 * here it develops, exactly like every other layer on this chart. The 20-window
 * warm-up is five minutes at the default window, and is the demo's own floor.
 */
export const DEFAULT_EVENT_TUNING: EventTuning = {
  sweepLots: 50,
  burstGapS: 60,
  burstSpanPts: 5.0,
  burstLots: 150,
  absorbWinMs: 15_000,
  absorbMult: 3.0,
  absorbMinWindows: 20,
  absorbScope: "rth",
  absorbBaseline: 0,
  absorbMerge: true,
};

export interface Snapshot {
  bars: Bar[];
  /** The prior sessions on the same tape, bucketed by the same rule — context
   *  drawn to the left of the session, never played. Empty unless prior days
   *  were loaded. Re-derived on a timeframe change like everything else, so it
   *  is handed over whole with each snapshot rather than set once. */
  history: Bar[];
  gBand: BandPt[];
  nBand: BandPt[];
  /** The ⚓ anchor's band — empty when no anchor is set, or when the clock sits
   *  before it (a rewind past the anchor un-draws it and playing forward brings
   *  it back, which is the same "un-happen" rule the trade log follows). */
  aBand: BandPt[];
  /** The weekly anchor's band — empty when the session shipped no weekly seed.
   *  Unlike the other three this one reaches back over the context days, because
   *  the week it measures started before the session did (`historyWeeklyBand`). */
  wkBand: BandPt[];
  gProfile: ProfilePt[];
  nProfile: ProfilePt[];
  /** The developing *weekly* value area — empty when the weekly profile could
   *  not be honestly seeded (same absence rule as the weekly band). */
  wProfile: ProfilePt[];
  /** Every big trade that has printed by the clock — the whole list, so a rewind
   *  simply hands back the shorter one. */
  bigs: BigTrade[];
  /** Every tape event that has printed by the clock, same rule as `bigs`. */
  events: TapeEvent[];
  ib: IbBox | null;
  range: RangeBox | null;
  lastPrice: number;
  clockMs: number;
}

/**
 * One prior session on the glued tape, in the order it was glued in.
 *
 * `ticks` is how much of the tape that day owns, which is what turns the list
 * into index bounds — the engine is handed one flat tape and would otherwise
 * have no idea where Tuesday stops and Wednesday starts.
 *
 * `weeklySeed` is that day's own seed, the same (Σv, Σpv, Σp²v) the session
 * payload carries and from the same source (`journal.sim.weekly`): the week
 * behind *that* day's Globex open. Per day rather than one seed for the whole
 * stretch because the anchor resets — at a week boundary and at a roll — and a
 * single accumulation run across the seam would draw straight through a reset
 * that really happened. Null when the week behind that day has a hole in it,
 * which drops that day's stretch of line rather than approximating it.
 */
export interface ContextDay {
  ticks: number;
  weeklySeed: number[] | null;
}

export interface StepResult {
  barsTail: Bar[]; // bars to candle.update() (previously-forming + any new)
  gTail: BandPt[];
  nTail: BandPt[];
  aTail: BandPt[];
  wkTail: BandPt[];
  gProfTail: ProfilePt[];
  nProfTail: ProfilePt[];
  wProfTail: ProfilePt[];
  /** Big trades that appeared or grew this step, newest-run-last. Merged by
   *  `idx`, not appended: the run at the tail is still taking fills. */
  bigTail: BigTrade[];
  /** Tape events that appeared or grew this step. Merged by `kind` + `idx` for
   *  the same reason, and re-emitted from whichever of the two open events sits
   *  furthest back — a burst and an absorption can both still be growing. */
  evTail: TapeEvent[];
  ib: IbBox | null;
  range: RangeBox | null;
  /** A bar closed during this step — the cue to repaint anything pinned to the
   *  bar grid (fixed-range profiles, the IB's right edge). */
  newBar: boolean;
  fromIdx: number; // tick range applied this step, for bracket-fill checks
  toIdx: number;
  lastPrice: number;
  clockMs: number;
  atEnd: boolean;
}

export function decodeTape(p: SessionPayload): Tape {
  const n = p.n;
  const t = new Float64Array(n);
  const price = new Float64Array(n);
  const level = new Int32Array(n);
  const size = new Int32Array(n);
  const side = new Uint8Array(n);
  let accT = p.t0;
  let accTk = Math.round(p.price0 / p.tick_size);
  const dt = p.dt;
  const dp = p.dp;
  const sz = p.size;
  const sd = p.side;
  for (let i = 0; i < n; i++) {
    accT += dt[i]; // dt[0] === 0
    accTk += dp[i]; // dp[0] === 0
    t[i] = accT;
    price[i] = accTk * p.tick_size;
    level[i] = accTk;
    size[i] = sz[i];
    const c = sd.charCodeAt(i);
    side[i] = c === 65 ? 1 : c === 66 ? 2 : 0; // 'A' : 'B' : else
  }
  return { n, t, price, level, size, side, tickSize: p.tick_size, pointValue: p.point_value };
}

/**
 * Glue several days' tapes into one, oldest first.
 *
 * The Simulator draws prior sessions as context, and the cheapest way to have
 * them behave exactly like the session — real candles on any timeframe, real
 * volume-at-price under a fixed-range profile — is for them to *be* the same
 * tape. The engine then plays only the stretch that belongs to the session it
 * was handed (see `ReplayEngine`'s session index), and everything before it is
 * bars and nothing else: no VWAP, no value area, no IB, no big trades.
 *
 * Callers hold the pieces in wall-clock order and never overlapping — one
 * cached session runs 18:00→18:00, so consecutive days butt up against each
 * other. Tick size and point value come from the last (the session's own).
 */
export function concatTapes(tapes: Tape[]): Tape {
  if (tapes.length === 1) return tapes[0];
  const n = tapes.reduce((a, x) => a + x.n, 0);
  const out: Tape = {
    n,
    t: new Float64Array(n),
    price: new Float64Array(n),
    level: new Int32Array(n),
    size: new Int32Array(n),
    side: new Uint8Array(n),
    tickSize: tapes[tapes.length - 1].tickSize,
    pointValue: tapes[tapes.length - 1].pointValue,
  };
  let at = 0;
  for (const p of tapes) {
    out.t.set(p.t.subarray(0, p.n), at);
    out.price.set(p.price.subarray(0, p.n), at);
    out.level.set(p.level.subarray(0, p.n), at);
    out.size.set(p.size.subarray(0, p.n), at);
    out.side.set(p.side.subarray(0, p.n), at);
    at += p.n;
  }
  return out;
}

// The accumulator lives in lib/vwap now — one definition of "volume-weighted
// anchored VWAP" on this side of the wire, shared with the charts that draw one
// from bars instead of from a tape. `point` stays here because stamping a band
// with a bar time is this engine's concern, not the accumulator's.
const bandAt = (a: Vwap, time: number): BandPt => ({ time, ...a.band() });

const VALUE_AREA_PCT = 0.7;

/**
 * Volume-at-price for one anchor, as a dense histogram over the instrument's
 * tick grid. Ticks go in one at a time (O(1)); the POC / value-area scan is run
 * once per bar close, which is exactly the cadence journal.sim.profile uses —
 * the levels a rule reads are the last *closed* bar's, so a per-tick scan would
 * burn time producing numbers nothing may look at.
 *
 * The array grows on demand rather than being sized from the session's range up
 * front: the range isn't known until the session is over, and a replay is by
 * definition watching it happen.
 *
 * Exported because the fixed-range profile tool develops its own value area over
 * whatever slice was dragged out, and that has to be the *same* walk the session
 * profiles use — two ports of the value-area expansion would be two things to
 * keep in step, and the tool's levels would drift from the ones beside them.
 */
export class LevelHist {
  private h = new Float64Array(2048);
  private base = 0; // tick level of h[0]
  private lo = 0;
  private hi = -1; // hi < lo -> nothing binned yet
  private total = 0;

  /** Start from a server-collapsed histogram — the weekly profile's seed. Goes
   *  through `add` so the growth/bounds bookkeeping has one owner. */
  constructor(seed?: { min: number; counts: number[] } | null) {
    if (seed) {
      for (let i = 0; i < seed.counts.length; i++) {
        if (seed.counts[i] > 0) this.add(seed.min + i, seed.counts[i]);
      }
    }
  }

  add(level: number, size: number): void {
    if (this.hi < this.lo) {
      this.base = level - (this.h.length >> 1);
      this.lo = this.hi = level;
    } else if (level < this.base || level >= this.base + this.h.length) {
      this.grow(level);
    }
    this.h[level - this.base] += size;
    if (level < this.lo) this.lo = level;
    if (level > this.hi) this.hi = level;
    this.total += size;
  }

  private grow(level: number): void {
    const lo = Math.min(this.lo, level) - 1024;
    const hi = Math.max(this.hi, level) + 1024;
    const next = new Float64Array(hi - lo + 1);
    next.set(this.h.subarray(this.lo - this.base, this.hi - this.base + 1), this.lo - lo);
    this.h = next;
    this.base = lo;
  }

  /**
   * POC and the value-area edges as tick levels, or null while nothing has
   * traded. The expansion is the classic Market Profile one and a straight port
   * of journal.sim.profile._value_area: from the POC, annex whichever
   * neighbouring *pair* of levels carries more volume until 70% is enclosed —
   * pairs rather than single levels, so a lopsided distribution can't let the
   * area creep up one thin level at a time.
   */
  levels(): { poc: number; vah: number; val: number } | null {
    if (this.total <= 0 || this.hi < this.lo) return null;
    const h = this.h;
    const b = this.base;
    let poc = this.lo;
    let best = -1;
    for (let l = this.lo; l <= this.hi; l++) {
      const v = h[l - b];
      if (v > best) {
        best = v;
        poc = l;
      }
    }
    const target = this.total * VALUE_AREA_PCT;
    let acc = h[poc - b];
    let lo = poc;
    let hi = poc;
    while (acc < target && (lo > this.lo || hi < this.hi)) {
      // -1 marks an edge that has run out of levels, so the other side always
      // wins the comparison.
      const up =
        hi < this.hi ? h[hi + 1 - b] + (hi + 2 <= this.hi ? h[hi + 2 - b] : 0) : -1;
      const down =
        lo > this.lo ? h[lo - 1 - b] + (lo - 2 >= this.lo ? h[lo - 2 - b] : 0) : -1;
      if (up < 0 && down < 0) break;
      if (up >= down) {
        for (let k = 0; k < 2 && hi < this.hi; k++) acc += h[++hi - b];
      } else {
        for (let k = 0; k < 2 && lo > this.lo; k++) acc += h[--lo - b];
      }
    }
    return { poc, vah: hi, val: lo };
  }
}

/** The two-TPO convention, same window journal.sim.ib measures against. Exported
 *  so a reader of the IB can check it is the window the study's numbers were
 *  measured on before quoting them at it. */
export const IB_MINUTES = 60;

export class ReplayEngine {
  readonly tape: Tape;
  private rthOpenMs: number;
  private rthCloseMs: number;
  private globexAnchorMs: number | null;
  private weeklySeed: number[] | null;
  private weeklyHistSeed: { min: number; counts: number[] } | null;

  private ibEndMs: number;

  /** First tick of the session being replayed. Non-zero when prior days were
   *  glued on in front (see `concatTapes`): everything the engine develops
   *  starts here, so the context days are drawn and nothing more. */
  private i0 = 0;
  /** The context days as bars, on the current bucketing. Built on demand and
   *  thrown away whenever the bucketing changes — it is the same derivation as
   *  the session's bars, just over a stretch that never grows. */
  private hist: Bar[] | null = null;
  /** The context days, oldest first — empty unless prior days were glued on. */
  private ctx: ContextDay[];
  /** The weekly band over those days, cached beside `hist` and thrown away with
   *  it: it is bucketed on the same bars, so a timeframe change re-derives both. */
  private histWk: BandPt[] | null = null;

  private tf: Timeframe;
  /** Prints folded into the bar still forming. Only a tick timeframe reads it,
   *  but it is kept either way so switching mid-replay has nothing to restore. */
  private barTicks = 0;

  private cursor = 0;
  private clockMs: number;
  private bars: Bar[] = [];
  private gBand: BandPt[] = [];
  private nBand: BandPt[] = [];
  private aBand: BandPt[] = [];
  private g = new Vwap();
  private nyv = new Vwap();
  private a = new Vwap();
  private wkBand: BandPt[] = [];
  private wk = new Vwap();
  private gProfile: ProfilePt[] = [];
  private nProfile: ProfilePt[] = [];
  private wProfile: ProfilePt[] = [];
  private gHist = new LevelHist();
  private nHist = new LevelHist();
  // Null when the weekly profile cannot be honestly seeded — mirrors `wk`'s
  // weeklySeed gate, and nothing weekly is emitted then.
  private wHist: LevelHist | null = null;
  private ibHigh = NaN;
  private ibLow = NaN;
  // The session's running extremes, over exactly the ticks the IB's window is a
  // prefix of — so `range` always contains `ib`, which is what makes the
  // difference between them "what the day has added since 10:30".
  private rthHigh = NaN;
  private rthLow = NaN;

  // --- big trades ----------------------------------------------------------
  // The threshold is a setting rather than a constant because what counts as
  // size is a fact about the instrument and the day, not about the code.
  private bigMin = DEFAULT_BIG_LOTS;
  private bigs: BigTrade[] = [];
  // The sweep still taking fills. It is published as soon as it crosses the
  // threshold and then keeps being re-published as it grows, rather than being
  // held back until it closes — a replay is watching the order arrive, and a
  // bubble that only appears once the sweep is over would always be late.
  private runSide = 0;
  private runLots = 0;
  private runFills = 0;
  private runAnchor = NaN; // price the run began at, for the span test
  private runMs = -Infinity;
  private runIdx = -1;
  // The rest of the run, which only the burst clusterer needs: the band it has
  // worked across, and the bar and instant it starts and ends at.
  private runLo = NaN;
  private runHi = NaN;
  private runStartMs = 0;
  private runBt = 0;
  private runBtEnd = 0;

  // --- tape events (bursts + absorption) -----------------------------------
  // Published the moment they cross their threshold and then re-published as
  // they grow, exactly like the sweeps: a replay is watching the event happen,
  // and a band that only appeared once it was over would always be late.
  private events: TapeEvent[] = [];
  // The open burst: where in `events` it sits (-1 until it is big enough to be
  // published), what it has accumulated, and the member edges the span test
  // runs against.
  private bIdx = -1; // tick index the burst began at, -1 = no burst open
  private bPos = -1;
  private bFrom = 0;
  private bTo = 0;
  private bLo = 0;
  private bHi = 0;
  private bMinLo = 0;
  private bMaxLo = 0;
  private bMinHi = 0;
  private bMaxHi = 0;
  private bLots = 0;
  private bBuy = 0;
  private bN = 0;
  private bEndMs = 0;

  // What selects an event. Settings, for the reason `EventTuning` gives, and
  // like the big-trade threshold they are recorded here and re-derived through
  // `snapshotTo` rather than applied to what is already on the chart.
  private ev: EventTuning = { ...DEFAULT_EVENT_TUNING };

  // The window now accumulating, the concentrations of the windows that have
  // already closed (kept sorted, for the running median), and the absorption
  // event adjacent hot windows are merging into.
  private absKey = -1;
  private absIdx = 0;
  private absLo = 0;
  private absHi = 0;
  private absVol = 0;
  private absBuy = 0;
  private absFrom = 0;
  private absTo = 0;
  private absConc: number[] = [];
  // The same concentrations in arrival order, kept only when the baseline is a
  // rolling one — a median over "the last N" has to know which is the oldest,
  // which the sorted copy can't say.
  private absQ: number[] = [];
  private absPos = -1;
  private absPrevKey = -1;
  private absBuyAcc = 0;

  // Whether the last tick applied still belongs to the NY anchor's window. The
  // anchor stops at the bell's close, exactly as api/sim_charts bounds it — the
  // post hour belongs to the Globex anchor only. Without this the band would go
  // flat (and the value area frozen) across the post bars instead of ending.
  private nyOpen = false;
  private lastPrice = NaN;
  // The bar the tail of every developing series currently belongs to, so a
  // profile refresh knows which entry it is overwriting.
  private curBarTime = NaN;

  // The ⚓ anchor, as epoch-ms of the anchored bar's *open*. Survives a rebuild:
  // reset() clears what the anchor produced, never the anchor itself.
  private anchorMs: number | null = null;

  // Emission watermarks so advance() only returns what changed.
  private emitBars = 0;
  private emitG = 0;
  private emitN = 0;
  private emitA = 0;
  private emitWk = 0;
  private emitGP = 0;
  private emitNP = 0;
  private emitWP = 0;
  private emitBig = 0;
  private emitEv = 0;

  constructor(tape: Tape, session: SessionPayload, tf: Timeframe, context: ContextDay[] = []) {
    this.tape = tape;
    this.ctx = context;
    this.rthOpenMs = session.rth_open_ms;
    this.rthCloseMs = session.rth_close_ms;
    this.globexAnchorMs = session.has_overnight ? session.globex_anchor_ms : null;
    this.weeklySeed = session.weekly_seed ?? null;
    this.weeklyHistSeed = session.weekly_hist_seed ?? null;
    this.ibEndMs = session.rth_open_ms + IB_MINUTES * 60_000;
    this.clockMs = session.session_start_ms;
    this.tf = tf;
    // Where the session starts in the tape it was handed. Zero unless context
    // days were glued in front of it — a session's own tape begins at its first
    // tick, which is what `session_start_ms` is.
    this.i0 = 0;
    const t = tape.t;
    let lo = 0;
    let hi = tape.n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (t[mid] < session.session_start_ms) lo = mid + 1;
      else hi = mid;
    }
    this.i0 = lo;
    this.cursor = lo;
  }

  get position(): number {
    return this.clockMs;
  }

  /**
   * Re-bucket the tape. Only records the choice — the caller drives the rebuild
   * through `snapshotTo`, because a timeframe change *is* a re-derivation of
   * everything from tick zero, which is the one path that already exists for
   * that. Nothing about the trade log is touched: the log is in wall-clock ms
   * and tick indices, neither of which knows what a bar is.
   */
  setTimeframe(tf: Timeframe): void {
    this.tf = tf;
    // The context days are bars like any other, so they are re-bucketed too —
    // and the weekly band over them is bucketed on those same bars.
    this.hist = null;
    this.histWk = null;
  }

  /**
   * The context days as bars: everything on the tape before the session, on the
   * current bucketing.
   *
   * Built the same way the session's bars are, with one extra rule at the seam.
   * A time bucket that straddles the session's first tick belongs to the
   * session — that is the bar the replay will grow — so any context bar landing
   * on it is dropped rather than drawn twice at the same time (the chart
   * requires strictly ascending times, and half a bucket of context is not
   * worth a duplicate). Tick bars are counted backwards from the seam instead,
   * so the last context bar ends exactly where the session begins.
   */
  historyBars(): Bar[] {
    if (this.hist) return this.hist;
    const t = this.tape;
    const end = this.i0;
    const out: Bar[] = [];
    if (end <= 0 || end > t.n) return (this.hist = out);
    const per = this.tf.kind === "tick" ? this.tf.ticks : 0;
    // The first bar carries the remainder, so every boundary after it — and the
    // last one — lands on a multiple of `per` counted back from the seam.
    let left = per ? end % per || per : 0;
    let bar: Bar | null = null;
    for (let i = 0; i < end; i++) {
      const price = t.price[i];
      const size = t.size[i];
      if (per) {
        if (!bar || left === 0) {
          const sec = Math.floor(t.t[i] / 1000);
          const time: number = bar ? Math.max(sec, bar.time + 1) : sec;
          bar = { time, open: price, high: price, low: price, close: price, volume: size, ...openBar(price), i0: i, i1: i };
          out.push(bar);
          left = per;
        } else {
          bar.high = Math.max(bar.high, price);
          bar.low = Math.min(bar.low, price);
          bar.close = price;
          foldTick(bar, price, size);
          bar.i1 = i;
        }
        left--;
        continue;
      }
      const bt = this.timeBucket(t.t[i]);
      // `bt > bar.time` rather than `!==`, for the reason `applyTick` gives: a
      // stamp that steps backwards folds into the open bar instead of opening
      // one behind it, because the chart asserts on a descending series.
      if (!bar || bt > bar.time) {
        bar = { time: bt, open: price, high: price, low: price, close: price, volume: size, ...openBar(price), i0: i, i1: i };
        out.push(bar);
      } else {
        bar.high = Math.max(bar.high, price);
        bar.low = Math.min(bar.low, price);
        bar.close = price;
        foldTick(bar, price, size);
        bar.i1 = i;
      }
    }
    const seam = per ? Math.floor(t.t[end] / 1000) : this.timeBucket(t.t[end]);
    while (out.length && out[out.length - 1].time >= seam) out.pop();
    return (this.hist = out);
  }

  /**
   * The weekly anchor over the context days.
   *
   * The other three anchors have nothing to say before the session — Globex and
   * NY are anchored inside it by definition, and the ⚓ is placed by hand — so
   * the accumulators all start at the seam and the context stretch is left bare.
   * The weekly anchor is the one that genuinely predates the session: its line
   * runs from the week's open, and stopping it at the seam drew the week's VWAP
   * over one day of the week.
   *
   * Built the same way the session's own is, once per context day: seed a fresh
   * accumulator from that day's `weeklySeed` — the week behind *it* — and run
   * that day's ticks through it. Which is why the seed is per day and not one
   * for the stretch: the seed already contains every day before its own, so
   * carrying an accumulator across a day boundary would count those days twice.
   * A bar straddling the boundary is left with the older day's point and its
   * remaining ticks dropped, for the same reason — they are in the next day's
   * seed already.
   */
  historyWeeklyBand(): BandPt[] {
    if (this.histWk) return this.histWk;
    const out: BandPt[] = [];
    // No line at all without the session's own seed: a weekly band that covers
    // the context days and then stops at the seam is worse than none, and
    // `applyTick` draws nothing when the session could not be honestly seeded.
    if (this.weeklySeed == null || !this.ctx.length) return (this.histWk = out);
    const bars = this.historyBars();
    const t = this.tape;
    let bi = 0;
    let from = 0;
    for (const day of this.ctx) {
      const to = Math.min(from + day.ticks, this.i0);
      if (day.weeklySeed == null) {
        // A hole in that day's week: skip its bars rather than draw a line off a
        // seed that isn't one. The days after it still draw — each carries its
        // own seed, so one bad Tuesday doesn't cost Wednesday.
        while (bi < bars.length && bars[bi].i0 < to) bi++;
        from = to;
        continue;
      }
      const wk = new Vwap(day.weeklySeed);
      let i = from;
      while (bi < bars.length && bars[bi].i0 < to) {
        const bar = bars[bi];
        const end = Math.min(bar.i1, to - 1);
        for (; i <= end; i++) wk.add(t.price[i], t.size[i]);
        if (wk.active) out.push(bandAt(wk, bar.time));
        bi++;
      }
      from = to;
    }
    return (this.histWk = out);
  }

  timeframe(): Timeframe {
    return this.tf;
  }

  /**
   * How many lots a sweep has to beat to be marked. Records the choice only —
   * the caller rebuilds through `snapshotTo`, the same path a timeframe change
   * takes, because the marks that survive a new threshold are a re-derivation of
   * the whole tape and not a filter over what was already published.
   */
  setBigLots(lots: number): void {
    this.bigMin = Math.max(1, Math.floor(lots));
  }

  bigLots(): number {
    return this.bigMin;
  }

  /**
   * What selects a tape event. Records the choice only, exactly like
   * `setBigLots` — the caller rebuilds through `snapshotTo`, because a burst is
   * a cluster and an absorption is scored against a median, and neither can be
   * recovered from the events a different setting published.
   *
   * Partial: one knob moves at a time, and the panel that moves it has no reason
   * to hold the other nine.
   */
  setEventTuning(t: Partial<EventTuning>): void {
    this.ev = { ...this.ev, ...t };
  }

  eventTuning(): EventTuning {
    return this.ev;
  }

  /**
   * The bar a wall-clock instant falls in, as the epoch seconds the chart draws
   * on. Time buckets are measured off the RTH bell rather than off the epoch, so
   * the bell is always a bar boundary: the NY VWAP, the NY value area and the
   * Initial Balance all start there, and an epoch-aligned hour would otherwise
   * open the session's first bar at 09:00 with half of it overnight tape.
   */
  private timeBucket(ms: number): number {
    const step = (this.tf as { ms: number }).ms;
    return Math.floor((Math.floor((ms - this.rthOpenMs) / step) * step + this.rthOpenMs) / 1000);
  }

  /**
   * The drawn bar an instant belongs to, as a bar time in epoch seconds.
   *
   * Anything pinned to the grid — a fill mark, the IB's edges — has to land on a
   * bar that exists, and with tick bars there is no formula from an instant to
   * one: the bars fall wherever the tape decided. So it is a lookup over what has
   * actually been built, clamped at both ends. An instant the replay hasn't
   * reached yet belongs to the live edge, which is the only honest place to draw
   * it — and it moves to its real bar as soon as the tape gets there.
   */
  barTimeAt(ms: number): number {
    const bars = this.bars;
    const sec = Math.floor(ms / 1000);
    if (!bars.length) return sec;
    if (sec <= bars[0].time) return bars[0].time;
    let lo = 0;
    let hi = bars.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (bars[mid].time <= sec) lo = mid;
      else hi = mid - 1;
    }
    return bars[lo].time;
  }

  /**
   * The clock that completes the bar now forming — what "step one bar" advances
   * to, so that a step always reveals exactly one more finished candle.
   *
   * A time bar is finished once the clock reaches its boundary. A tick bar is
   * finished on a count, which is a fact about the tape and not about the clock,
   * so the target is read off the print that fills it. Landing on that print
   * rather than on the one *after* it matters: it leaves the bar complete instead
   * of opening a one-print stub, which is what the time branch does too. A bar
   * already full (a second step from the same spot) reveals a whole new one.
   */
  nextBarClockMs(): number {
    const t = this.tape;
    if (this.tf.kind === "tick") {
      if (t.n === 0) return this.clockMs;
      const left = this.tf.ticks - this.barTicks;
      const i = this.cursor + (left > 0 ? left : this.tf.ticks) - 1;
      return t.t[Math.min(i, t.n - 1)];
    }
    const step = this.tf.ms;
    return (Math.floor((this.clockMs - this.rthOpenMs) / step) + 1) * step + this.rthOpenMs;
  }

  /**
   * The clock at which the bar before the live edge had just completed — what
   * "step one bar back" seeks to, the mirror of `nextBarClockMs`.
   *
   * Read off the bars actually built rather than off a boundary formula, so it
   * means the same thing on a tick bar (whose edges fall wherever the tape
   * decided) as on a time bar: the last print of the previous closed bar.
   * Whatever was forming at the live edge un-happens, which is exactly what a
   * seek to this clock produces. With less than two bars there is nowhere
   * earlier to stand, so it answers the session's first print and the caller's
   * clamp does the rest.
   */
  prevBarClockMs(): number {
    const bars = this.bars;
    if (bars.length < 2) return this.tape.n ? this.tape.t[this.i0] : this.clockMs;
    return this.tape.t[Math.max(this.i0, bars[bars.length - 2].i1)];
  }

  // The last applied tick's price (the fill price at the current clock), and the
  // index of the next tick to apply (where a new position's bracket scan begins).
  lastPriceValue(): number {
    return this.lastPrice;
  }
  cursorIndex(): number {
    return this.cursor;
  }

  /**
   * Place (or clear) the ⚓ anchored VWAP, as a bar time in epoch seconds.
   *
   * Only records the anchor — the band it produces is built by the next
   * snapshot, which the caller drives. That keeps one rebuild path for every way
   * the picture can change (load, seek, re-anchor) instead of a second one that
   * would have to re-scan the tape itself.
   */
  setAnchor(barTimeSec: number | null): void {
    this.anchorMs = barTimeSec == null ? null : barTimeSec * 1000;
  }

  anchor(): number | null {
    return this.anchorMs == null ? null : this.anchorMs / 1000;
  }

  private reset(): void {
    this.cursor = this.i0;
    this.barTicks = 0;
    this.bars = [];
    this.gBand = [];
    this.nBand = [];
    this.aBand = [];
    this.wkBand = [];
    this.g = new Vwap();
    this.nyv = new Vwap();
    this.a = new Vwap();
    // The week behind the anchor is not re-derived from the tape — it is the
    // seed, so a rebuild starts from it exactly as the first load did.
    this.wk = new Vwap(this.weeklySeed);
    this.gProfile = [];
    this.nProfile = [];
    this.wProfile = [];
    this.gHist = new LevelHist();
    this.nHist = new LevelHist();
    // Like `wk`: a rebuild starts from the seed, not from the tape — the week
    // behind the session is not on it. No seed, no weekly profile at all.
    this.wHist = this.weeklyHistSeed ? new LevelHist(this.weeklyHistSeed) : null;
    this.ibHigh = NaN;
    this.ibLow = NaN;
    this.rthHigh = NaN;
    this.rthLow = NaN;
    this.bigs = [];
    this.runSide = 0;
    this.runLots = 0;
    this.runFills = 0;
    this.runAnchor = NaN;
    this.runMs = -Infinity;
    this.runIdx = -1;
    this.runLo = NaN;
    this.runHi = NaN;
    this.runStartMs = 0;
    this.runBt = 0;
    this.runBtEnd = 0;
    this.events = [];
    this.bIdx = -1;
    this.bPos = -1;
    this.bLots = 0;
    this.bBuy = 0;
    this.bN = 0;
    this.absKey = -1;
    this.absConc = [];
    this.absQ = [];
    this.absPos = -1;
    this.absPrevKey = -1;
    this.absBuyAcc = 0;
    this.nyOpen = false;
    this.lastPrice = NaN;
    this.curBarTime = NaN;
    this.emitBars = 0;
    this.emitG = 0;
    this.emitN = 0;
    this.emitA = 0;
    this.emitWk = 0;
    this.emitGP = 0;
    this.emitNP = 0;
    this.emitBig = 0;
  }

  /**
   * Fold one tick into the running sweep, publishing it once it is big enough.
   *
   * The run breaks on a side change, a gap, or a price that has walked further
   * than the span from where it began — the same three rules the demo page
   * aggregates by. A print with no aggressor tag ('N') can't belong to either
   * side, so it ends the run rather than joining it.
   */
  private applyBig(i: number, ms: number, price: number, size: number, bt: number): void {
    const sd = this.tape.side[i];
    if (sd === 0) {
      // A print with no aggressor tag can't belong to either side, so it ends
      // the run rather than joining it.
      this.endRun();
      this.runSide = 0;
      return;
    }
    const span = SWEEP_SPAN_TICKS * this.tape.tickSize;
    const goes_on =
      sd === this.runSide && ms - this.runMs <= SWEEP_GAP_MS && Math.abs(price - this.runAnchor) <= span;
    if (!goes_on) {
      this.endRun();
      this.runSide = sd;
      this.runLots = 0;
      this.runFills = 0;
      this.runAnchor = price;
      this.runIdx = i;
      this.runLo = price;
      this.runHi = price;
      this.runStartMs = ms;
      this.runBt = bt;
    }
    this.runLots += size;
    this.runFills++;
    this.runMs = ms;
    this.runBtEnd = bt;
    if (price < this.runLo) this.runLo = price;
    if (price > this.runHi) this.runHi = price;
    if (this.runLots <= this.bigMin) return;
    const pt: BigTrade = {
      idx: this.runIdx,
      time: bt,
      price,
      lots: this.runLots,
      fills: this.runFills,
      buy: sd === SIDE_BUY,
    };
    const last = this.bigs.length ? this.bigs[this.bigs.length - 1] : null;
    if (last && last.idx === pt.idx) this.bigs[this.bigs.length - 1] = pt;
    else this.bigs.push(pt);
  }

  /**
   * The run just ended — fold it into the burst it belongs to, if it was big
   * enough to count as one.
   *
   * A burst is big sweeps close to each other in *both* time and price: the
   * shape a worked stop-run leaves. The break test is the demo's — too long a
   * gap since the last member's final fill, or a price that has walked too far
   * from a member — and it is applied to the *finished* sweep, so the same
   * tape clusters here exactly as it does in the write-up. A run usually ends
   * when a print arrives that doesn't continue it; when the tape goes quiet
   * instead, `settleByClock` ends it the moment no print still to come could
   * have continued it, which is the same clustering published earlier. The
   * burst itself still grows live as further sweeps join it.
   *
   * No cooldown between bursts, also as the demo has it — repeated hits on one
   * price are exactly what is worth seeing.
   */
  private endRun(): void {
    if (this.runLots < this.ev.sweepLots || this.runIdx < 0) return;
    // Consumed: the same run must not be folded in twice (an untagged print and
    // then the next run's break would both end it).
    const lots = this.runLots;
    this.runLots = 0;
    if (this.bIdx >= 0) {
      const gap = (this.runStartMs - this.bEndMs) / 1000;
      // Like edge to like edge — the new high against member highs, the new low
      // against member lows — so an up-walk and a down-walk break at the same
      // distance. (Measuring the new high against member *lows*, as the demo
      // first had it, folded each sweep's own range into an upward walk and
      // subtracted it from a downward one.)
      const span = Math.max(
        Math.abs(this.runHi - this.bMinHi),
        Math.abs(this.runHi - this.bMaxHi),
        Math.abs(this.runLo - this.bMinLo),
        Math.abs(this.runLo - this.bMaxLo),
      );
      if (gap > this.ev.burstGapS || span > this.ev.burstSpanPts) this.closeBurst();
    }
    if (this.bIdx < 0) {
      this.bIdx = this.runIdx;
      this.bPos = -1;
      this.bFrom = this.runBt;
      this.bLo = this.runLo;
      this.bHi = this.runHi;
      this.bMinLo = this.runLo;
      this.bMaxLo = this.runLo;
      this.bMinHi = this.runHi;
      this.bMaxHi = this.runHi;
      this.bLots = 0;
      this.bBuy = 0;
      this.bN = 0;
    } else {
      this.bMinLo = Math.min(this.bMinLo, this.runLo);
      this.bMaxLo = Math.max(this.bMaxLo, this.runLo);
      this.bMinHi = Math.min(this.bMinHi, this.runHi);
      this.bMaxHi = Math.max(this.bMaxHi, this.runHi);
    }
    this.bN++;
    this.bLots += lots;
    if (this.runSide === SIDE_BUY) this.bBuy += lots;
    if (this.runLo < this.bLo) this.bLo = this.runLo;
    if (this.runHi > this.bHi) this.bHi = this.runHi;
    this.bTo = this.runBtEnd;
    this.bEndMs = this.runMs;
    if (this.bLots < this.ev.burstLots) return;
    const ev: TapeEvent = {
      kind: "sweep",
      idx: this.bIdx,
      from: this.bFrom,
      to: this.bTo,
      lo: this.bLo,
      hi: this.bHi,
      lots: this.bLots,
      st: this.bLots / this.ev.burstLots,
      buy: this.bBuy >= this.bLots / 2,
      buyLots: this.bBuy,
      open: true,
      n: this.bN,
    };
    if (this.bPos >= 0) this.events[this.bPos] = ev;
    else {
      this.bPos = this.events.length;
      this.events.push(ev);
    }
  }

  /** The open burst is over — nothing further may join it. Its published band,
   *  if it has one, settles closed; the accumulator is forgotten either way. */
  private closeBurst(): void {
    if (this.bPos >= 0) this.events[this.bPos] = { ...this.events[this.bPos], open: false };
    this.bIdx = -1;
    this.bPos = -1;
  }

  /** Same for the absorption block adjacent hot windows were merging into. */
  private closeAbsorb(): void {
    if (this.absPos >= 0) this.events[this.absPos] = { ...this.events[this.absPos], open: false };
    this.absPos = -1;
  }

  /**
   * Settle what the clock alone can settle, once every tick up to it is in.
   *
   * Ticks are applied in time order, so when the clock stands more than the
   * sweep gap past a run's last fill, every print still to come is guaranteed
   * to break that run — folding it now clusters identically to waiting for the
   * print, just earlier. Without this a lull held the last sweep open
   * indefinitely, and the final run of the tape was never folded in at all.
   *
   * The same argument closes a stale burst (clock past its last member by more
   * than the burst gap, and no live run that began inside it), scores an
   * absorption window the clock has left, and closes a stale absorption block
   * (the clock has left the window that could still have merged into it).
   * Nothing about what is selected changes — every fold here produces exactly
   * what the next print would have, just without waiting for it.
   */
  private settleByClock(): void {
    if (this.runIdx >= 0 && this.runLots > 0 && this.clockMs - this.runMs > SWEEP_GAP_MS) {
      this.endRun();
      // The run is over either way — even a backwards-stamped print (which the
      // bar logic tolerates as "one print in the wrong second") must start a
      // run of its own rather than rejoin one the clock already ended.
      this.runSide = 0;
      this.runIdx = -1;
      this.runLots = 0;
    }
    if (this.bIdx >= 0 && this.clockMs - this.bEndMs > this.ev.burstGapS * 1000) {
      // A run still live — last fill within the sweep gap of the clock — that
      // began inside the burst gap may yet finish, qualify, and join.
      const live = this.runIdx >= 0 && this.runLots > 0 && this.clockMs - this.runMs <= SWEEP_GAP_MS;
      if (!(live && (this.runStartMs - this.bEndMs) / 1000 <= this.ev.burstGapS)) this.closeBurst();
    }
    // A window the clock has left is complete — every print still to come lands
    // in a later one — so it is scored now rather than when the next tick
    // happens to arrive: a shelf followed by quiet displays as the clock passes
    // the boundary, and the tape's final window is scored at all (the demo,
    // batching the whole session, always scored it; waiting on a print dropped
    // it). Before the stale-block check below, because scoring this window may
    // be exactly the merge that check would otherwise conclude can no longer
    // happen.
    if (this.absKey >= 0 && Math.floor(this.clockMs / this.ev.absorbWinMs) > this.absKey) {
      this.closeAbsorbWindow();
      // As with the folded run: a backwards-stamped print starts a window of
      // its own rather than resurrecting one the clock already scored.
      this.absKey = -1;
    }
    // The block stays open while the window adjacent to it could still close
    // hot and merge; once the clock has moved past that window, nothing can.
    if (
      this.absPos >= 0 &&
      Math.floor(this.clockMs / this.ev.absorbWinMs) > this.absPrevKey + 1 &&
      this.absKey !== this.absPrevKey + 1
    ) {
      this.closeAbsorb();
    }
  }

  /**
   * Accumulate one tick into the window absorption is measured on.
   *
   * Which ticks reach here is `absorbScope`'s call, and the default is the
   * demo's: RTH only, baseline restarting at the bell, because the overnight
   * trades a fraction of the volume through a fraction of the range and a median
   * taken across both has the open firing absorption on every window.
   */
  private applyAbsorb(i: number, ms: number, price: number, size: number, bt: number): void {
    const key = Math.floor(ms / this.ev.absorbWinMs);
    if (key !== this.absKey) {
      if (this.absKey >= 0) this.closeAbsorbWindow();
      this.absKey = key;
      this.absIdx = i;
      this.absLo = price;
      this.absHi = price;
      this.absVol = 0;
      this.absBuy = 0;
      this.absFrom = bt;
    }
    if (price < this.absLo) this.absLo = price;
    if (price > this.absHi) this.absHi = price;
    this.absVol += size;
    if (this.tape.side[i] === SIDE_BUY) this.absBuy += size;
    this.absTo = bt;
  }

  /** Median of the baseline's concentrations. Kept sorted on insert, which is
   *  cheaper than sorting on read: a session closes ~1,500 windows and every one
   *  of them asks for the median. */
  private absMedian(): number {
    const a = this.absConc;
    const n = a.length;
    const m = n >> 1;
    return n % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  /** Index of the leftmost entry ≥ `v` in the sorted concentrations — the insert
   *  point, and (when `v` is known to be present) the entry to drop. */
  private absAt(v: number): number {
    const a = this.absConc;
    let lo = 0;
    let hi = a.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (a[mid] < v) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /**
   * Score the window that has just closed, and publish it if it is hot.
   *
   * Adjacent hot windows are one absorption, not three — but a merge widens the
   * band as well as adding volume, so it is only taken while the *merged* block
   * still clears the bar. Otherwise the reported concentration could fall below
   * the threshold that selected the event, and a strength floored at 1.0 would
   * be a lie. With `absorbMerge` off each hot window stands alone, which is the
   * same tape read as "three windows agreed" rather than "one block".
   *
   * The floor moves as the median develops, and an event keeps the strength it
   * was published at. That is the honest causal version of the demo's fixed
   * whole-session floor: what was three times the day's own concentration when
   * it printed doesn't stop having been that later in the day.
   */
  private closeAbsorbWindow(): void {
    const tick = this.tape.tickSize;
    const conc = this.absVol / Math.max(this.absHi - this.absLo, tick);
    const a = this.absConc;
    a.splice(this.absAt(conc), 0, conc);
    // A rolling baseline drops the oldest window as the newest lands, so the
    // median answers "concentrated for right now" instead of "for today".
    const roll = this.ev.absorbBaseline;
    if (roll > 0) {
      this.absQ.push(conc);
      while (this.absQ.length > roll) {
        const old = this.absQ.shift() as number;
        const at = this.absAt(old);
        if (at < a.length) a.splice(at, 1);
      }
    }
    const key = this.absKey;
    // A warm-up longer than the rolling baseline would never be met — the pool
    // stops growing at N — so it is clamped rather than left to silently switch
    // the layer off on a combination of two reasonable-looking settings.
    if (a.length < (roll > 0 ? Math.min(this.ev.absorbMinWindows, roll) : this.ev.absorbMinWindows))
      return;
    const floor = this.ev.absorbMult * this.absMedian();
    if (!(floor > 0)) return;
    if (conc < floor) {
      this.closeAbsorb(); // a cold window ends the run
      return;
    }
    if (this.ev.absorbMerge && this.absPos >= 0 && this.absPrevKey === key - 1) {
      const ev = this.events[this.absPos];
      const bLo = Math.min(ev.lo, this.absLo);
      const bHi = Math.max(ev.hi, this.absHi);
      const vol = ev.lots + this.absVol;
      const merged = vol / Math.max(bHi - bLo, tick);
      if (merged >= floor) {
        this.absBuyAcc += this.absBuy;
        this.events[this.absPos] = {
          ...ev,
          to: this.absTo,
          lo: bLo,
          hi: bHi,
          lots: vol,
          st: merged / floor,
          buy: this.absBuyAcc >= vol / 2,
          buyLots: this.absBuyAcc,
          n: ev.n + 1,
        };
        this.absPrevKey = key;
        return;
      }
      // Merging would drop the block below the bar that selected it, so this
      // window starts an event of its own instead.
    }
    this.closeAbsorb(); // whatever block was open, this window isn't joining it
    this.absBuyAcc = this.absBuy;
    this.absPos = this.events.length;
    this.absPrevKey = key;
    this.events.push({
      kind: "absorb",
      idx: this.absIdx,
      from: this.absFrom,
      to: this.absTo,
      lo: this.absLo,
      hi: this.absHi,
      lots: this.absVol,
      st: conc / floor,
      buy: this.absBuy >= this.absVol / 2,
      buyLots: this.absBuy,
      // With merging off each hot window stands alone, so it is born settled.
      open: this.ev.absorbMerge,
      n: 1,
    });
  }

  // Overwrite-or-append one developing point at the tail of a series. Every
  // developing layer prints once per bar and keeps re-printing that bar's entry
  // while it forms, so they all fold in through here.
  private static put<T extends { time: number }>(into: T[], pt: T): void {
    if (into.length && into[into.length - 1].time === pt.time) into[into.length - 1] = pt;
    else into.push(pt);
  }

  /**
   * Recompute both developing value areas for whichever bar is at the tail.
   *
   * Called at each bar close (where the histogram holds exactly that bar's
   * session-to-close volume, since the tick that opens the next bar hasn't
   * landed yet) and once more at the end of a step, so the forming bar's levels
   * track the tape rather than lagging a minute behind.
   */
  private refreshProfiles(): void {
    const bt = this.curBarTime;
    if (!Number.isFinite(bt)) return;
    const tick = this.tape.tickSize;
    const gl = this.gHist.levels();
    if (gl) {
      ReplayEngine.put(this.gProfile, {
        time: bt, poc: gl.poc * tick, vah: gl.vah * tick, val: gl.val * tick,
      });
    }
    const nl = this.nyOpen ? this.nHist.levels() : null;
    if (nl) {
      ReplayEngine.put(this.nProfile, {
        time: bt, poc: nl.poc * tick, vah: nl.vah * tick, val: nl.val * tick,
      });
    }
    const wl = this.wHist ? this.wHist.levels() : null;
    if (wl) {
      ReplayEngine.put(this.wProfile, {
        time: bt, poc: wl.poc * tick, vah: wl.vah * tick, val: wl.val * tick,
      });
    }
  }

  private ibBox(): IbBox | null {
    if (!Number.isFinite(this.ibHigh)) return null;
    return {
      high: this.ibHigh,
      low: this.ibLow,
      start: this.barTimeAt(this.rthOpenMs),
      formed: this.barTimeAt(this.ibEndMs),
      complete: this.clockMs >= this.ibEndMs,
    };
  }

  private rangeBox(): RangeBox | null {
    return Number.isFinite(this.rthHigh) ? { high: this.rthHigh, low: this.rthLow } : null;
  }

  // Apply one tick into the developing bars + VWAP accumulators.
  private applyTick(i: number): void {
    const t = this.tape;
    const ms = t.t[i];
    const price = t.price[i];
    const size = t.size[i];
    let bar = this.bars.length ? this.bars[this.bars.length - 1] : undefined;
    let bt: number;
    let newBar: boolean;
    if (this.tf.kind === "tick") {
      newBar = !bar || this.barTicks >= this.tf.ticks;
      // A tick bar has no grid to land on, so it is stamped with the second its
      // first print landed in. Two bars can fall in the same second when the tape
      // bursts, and the chart requires strictly ascending times — so a collision
      // borrows the next second. The stamp is a max and never a running offset,
      // so the borrow is repaid by the first bar that opens in a second of its
      // own: on a measured NQ session 0.2% of 500-print bars collide and the axis
      // runs at most 1-2 seconds ahead of the tape at any point in the day.
      bt = !newBar
        ? bar!.time
        : bar
          ? Math.max(Math.floor(ms / 1000), bar.time + 1)
          : Math.floor(ms / 1000);
    } else {
      bt = this.timeBucket(ms);
      // `>`, not `!==`: a tape whose stamps step backwards would otherwise open
      // a bar *behind* the one forming, and the chart asserts on a series that
      // is not strictly ascending — which takes the whole page down with it.
      // Tick bars have been immune all along (the stamp above is a max), which
      // is why this only ever showed on a timeframe switch. The out-of-order
      // print is folded into the bar that is open instead: it is one print in
      // the wrong second, and a bar it cannot phase is the honest place for it.
      newBar = !bar || bt > bar.time;
      if (bar && !newBar) bt = bar.time;
    }
    if (newBar) {
      // The bar that was forming has just closed: settle its value areas from the
      // histogram as it stands, before this tick joins the next bar.
      this.refreshProfiles();
      bar = { time: bt, open: price, high: price, low: price, close: price, volume: size, ...openBar(price), i0: i, i1: i };
      this.bars.push(bar);
      this.curBarTime = bt;
      this.barTicks = 1;
    } else {
      this.barTicks++;
      bar!.high = Math.max(bar!.high, price);
      bar!.low = Math.min(bar!.low, price);
      bar!.close = price;
      foldTick(bar!, price, size);
      bar!.i1 = i;
    }
    // Globex band develops from the first overnight tick; NY from the bell. The
    // two value areas are anchored at exactly the same two points, so a level and
    // the band it belongs to always describe the same stretch of tape.
    if (this.globexAnchorMs != null) {
      this.g.add(price, size);
      this.gHist.add(t.level[i], size);
    }
    // The weekly anchor is the Globex one carrying the week behind it, so it
    // accumulates over exactly the same ticks — the difference is the seed it
    // started from. On the week's first session that seed is zero and the two
    // lines coincide, which is what a weekly anchor genuinely looks like on a
    // Monday.
    if (this.weeklySeed != null) this.wk.add(price, size);
    // Same sentence again for the weekly value area: the Globex histogram
    // carrying the week behind it.
    if (this.wHist) this.wHist.add(t.level[i], size);
    this.nyOpen = ms >= this.rthOpenMs && ms < this.rthCloseMs;
    if (this.nyOpen) {
      this.nyv.add(price, size);
      this.nHist.add(t.level[i], size);
      // The day's range, on the same window the NY anchor runs on — NaN-safe on
      // the first tick, like the IB below.
      if (!(price <= this.rthHigh)) this.rthHigh = price;
      if (!(price >= this.rthLow)) this.rthLow = price;
    }
    // Absorption's own window, which is not the NY one unless it is asked to be:
    // scoring the night means one baseline across two very different tapes.
    if (this.nyOpen || this.ev.absorbScope === "all") this.applyAbsorb(i, ms, price, size, bt);
    if (this.anchorMs != null && ms >= this.anchorMs) this.a.add(price, size);
    this.applyBig(i, ms, price, size, bt);
    // Refresh (or append) the band point for the current bar.
    if (this.g.active) ReplayEngine.put(this.gBand, bandAt(this.g, bt));
    if (this.nyOpen && this.nyv.active) ReplayEngine.put(this.nBand, bandAt(this.nyv, bt));
    if (this.a.active) ReplayEngine.put(this.aBand, bandAt(this.a, bt));
    if (this.weeklySeed != null && this.wk.active) ReplayEngine.put(this.wkBand, bandAt(this.wk, bt));
    // Initial Balance: the running high/low of the first hour of RTH.
    if (ms >= this.rthOpenMs && ms < this.ibEndMs) {
      if (!(price <= this.ibHigh)) this.ibHigh = price; // NaN-safe on the first tick
      if (!(price >= this.ibLow)) this.ibLow = price;
    }
    this.lastPrice = price;
  }

  // Full rebuild up to `clockMs`. Used on load and on every seek (incl. rewind).
  snapshotTo(clockMs: number): Snapshot {
    this.reset();
    this.clockMs = clockMs;
    const t = this.tape;
    while (this.cursor < t.n && t.t[this.cursor] <= clockMs) {
      this.applyTick(this.cursor);
      this.cursor++;
    }
    this.settleByClock();
    // The forming bar's value areas are only settled at its close, so print them
    // once here too — otherwise a paused replay shows levels a bar out of date.
    this.refreshProfiles();
    // Everything is now "emitted" — the chart will setData the whole snapshot.
    this.emitBars = this.bars.length;
    this.emitG = this.gBand.length;
    this.emitN = this.nBand.length;
    this.emitA = this.aBand.length;
    this.emitWk = this.wkBand.length;
    this.emitGP = this.gProfile.length;
    this.emitNP = this.nProfile.length;
    this.emitWP = this.wProfile.length;
    this.emitBig = this.bigs.length;
    this.emitEv = this.events.length;
    return {
      bars: this.bars.slice(),
      history: this.historyBars(),
      gBand: this.gBand.slice(),
      nBand: this.nBand.slice(),
      aBand: this.aBand.slice(),
      // The context stretch in front of the session's own points. Static, so it
      // is only ever handed over here — `advance` appends by time and never
      // touches the front of the series.
      wkBand: [...this.historyWeeklyBand(), ...this.wkBand],
      gProfile: this.gProfile.slice(),
      nProfile: this.nProfile.slice(),
      wProfile: this.wProfile.slice(),
      bigs: this.bigs.slice(),
      events: this.events.slice(),
      ib: this.ibBox(),
      range: this.rangeBox(),
      lastPrice: this.lastPrice,
      clockMs,
    };
  }

  // Incremental forward step. `clockMs` must be >= current position.
  advance(clockMs: number): StepResult {
    const t = this.tape;
    const fromIdx = this.cursor;
    // The previously-forming bar/points changed, so re-emit from one before the
    // watermark; update() overwrites an existing time and appends a newer one.
    const barStart = Math.max(0, this.emitBars - 1);
    const gStart = Math.max(0, this.emitG - 1);
    const nStart = Math.max(0, this.emitN - 1);
    const aStart = Math.max(0, this.emitA - 1);
    const wkStart = Math.max(0, this.emitWk - 1);
    const gpStart = Math.max(0, this.emitGP - 1);
    const npStart = Math.max(0, this.emitNP - 1);
    const wpStart = Math.max(0, this.emitWP - 1);
    // One back, like the bars: the newest sweep may have taken more fills.
    const bigStart = Math.max(0, this.emitBig - 1);
    // Both open events may still be growing, and either can sit behind the tail
    // (the other kind may have published after it), so re-emit from whichever is
    // furthest back. Re-emitting an event that didn't change is free — the chart
    // merges on identity, not on position.
    let evStart = Math.max(0, this.emitEv - 1);
    if (this.bPos >= 0) evStart = Math.min(evStart, this.bPos);
    if (this.absPos >= 0) evStart = Math.min(evStart, this.absPos);
    const barsBefore = this.bars.length;
    this.clockMs = clockMs;
    while (this.cursor < t.n && t.t[this.cursor] <= clockMs) {
      this.applyTick(this.cursor);
      this.cursor++;
    }
    this.settleByClock(); // fold a run / settle events the clock alone can end
    this.refreshProfiles(); // the still-forming bar, same as in snapshotTo
    const barsTail = this.bars.slice(barStart);
    const gTail = this.gBand.slice(gStart);
    const nTail = this.nBand.slice(nStart);
    const aTail = this.aBand.slice(aStart);
    const wkTail = this.wkBand.slice(wkStart);
    const gProfTail = this.gProfile.slice(gpStart);
    const nProfTail = this.nProfile.slice(npStart);
    const wProfTail = this.wProfile.slice(wpStart);
    const bigTail = this.bigs.slice(bigStart);
    const evTail = this.events.slice(evStart);
    this.emitBars = this.bars.length;
    this.emitG = this.gBand.length;
    this.emitN = this.nBand.length;
    this.emitA = this.aBand.length;
    this.emitWk = this.wkBand.length;
    this.emitGP = this.gProfile.length;
    this.emitNP = this.nProfile.length;
    this.emitWP = this.wProfile.length;
    this.emitBig = this.bigs.length;
    this.emitEv = this.events.length;
    return {
      barsTail,
      gTail,
      nTail,
      aTail,
      wkTail,
      gProfTail,
      nProfTail,
      wProfTail,
      bigTail,
      evTail,
      ib: this.ibBox(),
      range: this.rangeBox(),
      newBar: this.bars.length > barsBefore,
      fromIdx,
      toIdx: this.cursor,
      lastPrice: this.lastPrice,
      clockMs,
      atEnd: this.cursor >= t.n,
    };
  }
}
