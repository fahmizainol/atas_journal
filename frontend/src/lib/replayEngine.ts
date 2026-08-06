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
  globex_anchor_ms: number | null;
  /** (Σv, Σpv, Σp²v) already behind the weekly anchor when this session's Globex
   *  open arrives — the week's earlier sessions, collapsed to the three sums the
   *  accumulation needs (journal.sim.weekly). Null when there is no honest
   *  weekly line to draw: no overnight tape, or a hole in the week. */
  weekly_seed: number[] | null;
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
  /** Sweeps in the burst / 15s windows in the absorption. */
  n: number;
}

// The demo page's constants, unchanged so that an event means the same thing in
// the replay as it does in the write-up. Deliberately *not* wired to the
// big-trade threshold the setup bar exposes: that one is a reading choice about
// which prints to mark, these are the numbers the proxies were measured at.
const SWEEP_LOTS = 50; // a sweep this size counts toward a burst
const BURST_GAP_S = 60; // big sweeps this close in time join one burst …
const BURST_SPAN_PTS = 5.0; // … if they also stay this close in price
const BURST_LOTS = 150; // a burst needs this much size (strength 1.0)

// Absorption is scored *relative to the session*, never in absolute points: the
// same band means opposite things in a quiet and a violent regime (measured:
// median 15s RTH range 4.75-6.00pt on 2025-26 NQ, and an absolute
// 60s/≤4pt/≥900-lot rule fired zero times in three sessions). Concentration is
// lots per point traversed, scored against the median of the windows that have
// already closed — the demo's whole-session median is lookahead in a replay, so
// here it develops, exactly like every other layer on this chart.
const ABSORB_WIN_MS = 15_000;
const ABSORB_MULT = 3.0; // concentration this many × the median = strength 1.0
/** Windows that must have closed before the median means anything (five
 *  minutes). The demo's own floor, and until it is met nothing is scored. */
const ABSORB_MIN_WINDOWS = 20;

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
  /** The weekly anchor's band — empty when the session shipped no weekly seed. */
  wkBand: BandPt[];
  gProfile: ProfilePt[];
  nProfile: ProfilePt[];
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

export interface StepResult {
  barsTail: Bar[]; // bars to candle.update() (previously-forming + any new)
  gTail: BandPt[];
  nTail: BandPt[];
  aTail: BandPt[];
  wkTail: BandPt[];
  gProfTail: ProfilePt[];
  nProfTail: ProfilePt[];
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

// Volume-weighted accumulator for one anchored VWAP.
//
// `seed` is what was already behind the anchor before the first tick this
// accumulator will ever see — the weekly anchor, which starts days before the
// tape (see journal.sim.vwap's `seed`). Zero for an anchor that starts inside
// the tape, which is every other one here.
class Vwap {
  v = 0;
  pv = 0;
  p2v = 0;
  constructor(seed?: readonly number[] | null) {
    if (seed && seed.length === 3) {
      this.v = seed[0];
      this.pv = seed[1];
      this.p2v = seed[2];
    }
  }
  add(price: number, size: number) {
    this.v += size;
    this.pv += price * size;
    this.p2v += price * price * size;
  }
  point(time: number): BandPt {
    const mid = this.pv / this.v;
    const varr = Math.max(0, this.p2v / this.v - mid * mid);
    const sd = Math.sqrt(varr);
    return { time, mid, u1: mid + sd, l1: mid - sd, u2: mid + 2 * sd, l2: mid - 2 * sd };
  }
  get active() {
    return this.v > 0;
  }
}

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
 */
class LevelHist {
  private h = new Float64Array(2048);
  private base = 0; // tick level of h[0]
  private lo = 0;
  private hi = -1; // hi < lo -> nothing binned yet
  private total = 0;

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

  private ibEndMs: number;

  /** First tick of the session being replayed. Non-zero when prior days were
   *  glued on in front (see `concatTapes`): everything the engine develops
   *  starts here, so the context days are drawn and nothing more. */
  private i0 = 0;
  /** The context days as bars, on the current bucketing. Built on demand and
   *  thrown away whenever the bucketing changes — it is the same derivation as
   *  the session's bars, just over a stretch that never grows. */
  private hist: Bar[] | null = null;

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
  private gHist = new LevelHist();
  private nHist = new LevelHist();
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
  // published), what it has accumulated, and the member lows the span test runs
  // against.
  private bIdx = -1; // tick index the burst began at, -1 = no burst open
  private bPos = -1;
  private bFrom = 0;
  private bTo = 0;
  private bLo = 0;
  private bHi = 0;
  private bMinLo = 0;
  private bMaxLo = 0;
  private bLots = 0;
  private bBuy = 0;
  private bN = 0;
  private bEndMs = 0;

  // The 15-second window now accumulating, the concentrations of the windows
  // that have already closed (kept sorted, for the running median), and the
  // absorption event adjacent hot windows are merging into.
  private absKey = -1;
  private absIdx = 0;
  private absLo = 0;
  private absHi = 0;
  private absVol = 0;
  private absBuy = 0;
  private absFrom = 0;
  private absTo = 0;
  private absConc: number[] = [];
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
  private emitBig = 0;
  private emitEv = 0;

  constructor(tape: Tape, session: SessionPayload, tf: Timeframe) {
    this.tape = tape;
    this.rthOpenMs = session.rth_open_ms;
    this.rthCloseMs = session.rth_close_ms;
    this.globexAnchorMs = session.has_overnight ? session.globex_anchor_ms : null;
    this.weeklySeed = session.weekly_seed ?? null;
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
    // The context days are bars like any other, so they are re-bucketed too.
    this.hist = null;
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
          bar = { time, open: price, high: price, low: price, close: price, volume: size, i0: i, i1: i };
          out.push(bar);
          left = per;
        } else {
          bar.high = Math.max(bar.high, price);
          bar.low = Math.min(bar.low, price);
          bar.close = price;
          bar.volume += size;
          bar.i1 = i;
        }
        left--;
        continue;
      }
      const bt = this.timeBucket(t.t[i]);
      if (!bar || bar.time !== bt) {
        bar = { time: bt, open: price, high: price, low: price, close: price, volume: size, i0: i, i1: i };
        out.push(bar);
      } else {
        bar.high = Math.max(bar.high, price);
        bar.low = Math.min(bar.low, price);
        bar.close = price;
        bar.volume += size;
        bar.i1 = i;
      }
    }
    const seam = per ? Math.floor(t.t[end] / 1000) : this.timeBucket(t.t[end]);
    while (out.length && out[out.length - 1].time >= seam) out.pop();
    return (this.hist = out);
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
    this.gHist = new LevelHist();
    this.nHist = new LevelHist();
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
   * from a member's low — and it is applied to the *finished* sweep, so the same
   * tape clusters here exactly as it does in the write-up. That costs a few
   * hundred milliseconds of latency (a run only ends when a print arrives that
   * doesn't continue it) and buys numbers that don't have to be re-measured;
   * the burst itself still grows live as further sweeps join it.
   *
   * No cooldown between bursts, also as the demo has it — repeated hits on one
   * price are exactly what is worth seeing.
   */
  private endRun(): void {
    if (this.runLots < SWEEP_LOTS || this.runIdx < 0) return;
    // Consumed: the same run must not be folded in twice (an untagged print and
    // then the next run's break would both end it).
    const lots = this.runLots;
    this.runLots = 0;
    if (this.bIdx >= 0) {
      const gap = (this.runStartMs - this.bEndMs) / 1000;
      const span = Math.max(Math.abs(this.runHi - this.bMinLo), Math.abs(this.runHi - this.bMaxLo));
      if (gap > BURST_GAP_S || span > BURST_SPAN_PTS) this.bIdx = -1;
    }
    if (this.bIdx < 0) {
      this.bIdx = this.runIdx;
      this.bPos = -1;
      this.bFrom = this.runBt;
      this.bLo = this.runLo;
      this.bHi = this.runHi;
      this.bMinLo = this.runLo;
      this.bMaxLo = this.runLo;
      this.bLots = 0;
      this.bBuy = 0;
      this.bN = 0;
    } else {
      this.bMinLo = Math.min(this.bMinLo, this.runLo);
      this.bMaxLo = Math.max(this.bMaxLo, this.runLo);
    }
    this.bN++;
    this.bLots += lots;
    if (this.runSide === SIDE_BUY) this.bBuy += lots;
    if (this.runLo < this.bLo) this.bLo = this.runLo;
    if (this.runHi > this.bHi) this.bHi = this.runHi;
    this.bTo = this.runBtEnd;
    this.bEndMs = this.runMs;
    if (this.bLots < BURST_LOTS) return;
    const ev: TapeEvent = {
      kind: "sweep",
      idx: this.bIdx,
      from: this.bFrom,
      to: this.bTo,
      lo: this.bLo,
      hi: this.bHi,
      lots: this.bLots,
      st: this.bLots / BURST_LOTS,
      buy: this.bBuy >= this.bLots / 2,
      n: this.bN,
    };
    if (this.bPos >= 0) this.events[this.bPos] = ev;
    else {
      this.bPos = this.events.length;
      this.events.push(ev);
    }
  }

  /**
   * Accumulate one RTH tick into the 15-second window absorption is measured on.
   *
   * RTH only, and the baseline restarts at the bell: the overnight trades a
   * fraction of the volume through a fraction of the range, so a median taken
   * across both would have the open firing absorption on every window. That is
   * also the window the demo measured — its concentrations are RTH.
   */
  private applyAbsorb(i: number, ms: number, price: number, size: number, bt: number): void {
    const key = Math.floor(ms / ABSORB_WIN_MS);
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

  /** Median of the closed windows' concentrations. Kept sorted on insert, which
   *  is cheaper than sorting on read: a session closes ~1,500 windows and every
   *  one of them asks for the median. */
  private absMedian(): number {
    const a = this.absConc;
    const n = a.length;
    const m = n >> 1;
    return n % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  /**
   * Score the window that has just closed, and publish it if it is hot.
   *
   * Adjacent hot windows are one absorption, not three — but a merge widens the
   * band as well as adding volume, so it is only taken while the *merged* block
   * still clears the bar. Otherwise the reported concentration could fall below
   * the threshold that selected the event, and a strength floored at 1.0 would
   * be a lie.
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
    let lo = 0;
    let hi = a.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (a[mid] < conc) lo = mid + 1;
      else hi = mid;
    }
    a.splice(lo, 0, conc);
    const key = this.absKey;
    if (a.length < ABSORB_MIN_WINDOWS) return;
    const floor = ABSORB_MULT * this.absMedian();
    if (!(floor > 0)) return;
    if (conc < floor) {
      this.absPos = -1; // a cold window ends the run
      return;
    }
    if (this.absPos >= 0 && this.absPrevKey === key - 1) {
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
          n: ev.n + 1,
        };
        this.absPrevKey = key;
        return;
      }
      // Merging would drop the block below the bar that selected it, so this
      // window starts an event of its own instead.
    }
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
      newBar = !bar || bar.time !== bt;
    }
    if (newBar) {
      // The bar that was forming has just closed: settle its value areas from the
      // histogram as it stands, before this tick joins the next bar.
      this.refreshProfiles();
      bar = { time: bt, open: price, high: price, low: price, close: price, volume: size, i0: i, i1: i };
      this.bars.push(bar);
      this.curBarTime = bt;
      this.barTicks = 1;
    } else {
      this.barTicks++;
      bar!.high = Math.max(bar!.high, price);
      bar!.low = Math.min(bar!.low, price);
      bar!.close = price;
      bar!.volume += size;
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
    this.nyOpen = ms >= this.rthOpenMs && ms < this.rthCloseMs;
    if (this.nyOpen) {
      this.nyv.add(price, size);
      this.nHist.add(t.level[i], size);
      this.applyAbsorb(i, ms, price, size, bt);
      // The day's range, on the same window the NY anchor runs on — NaN-safe on
      // the first tick, like the IB below.
      if (!(price <= this.rthHigh)) this.rthHigh = price;
      if (!(price >= this.rthLow)) this.rthLow = price;
    }
    if (this.anchorMs != null && ms >= this.anchorMs) this.a.add(price, size);
    this.applyBig(i, ms, price, size, bt);
    // Refresh (or append) the band point for the current bar.
    if (this.g.active) ReplayEngine.put(this.gBand, this.g.point(bt));
    if (this.nyOpen && this.nyv.active) ReplayEngine.put(this.nBand, this.nyv.point(bt));
    if (this.a.active) ReplayEngine.put(this.aBand, this.a.point(bt));
    if (this.weeklySeed != null && this.wk.active) ReplayEngine.put(this.wkBand, this.wk.point(bt));
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
    this.emitBig = this.bigs.length;
    this.emitEv = this.events.length;
    return {
      bars: this.bars.slice(),
      history: this.historyBars(),
      gBand: this.gBand.slice(),
      nBand: this.nBand.slice(),
      aBand: this.aBand.slice(),
      wkBand: this.wkBand.slice(),
      gProfile: this.gProfile.slice(),
      nProfile: this.nProfile.slice(),
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
    this.refreshProfiles(); // the still-forming bar, same as in snapshotTo
    const barsTail = this.bars.slice(barStart);
    const gTail = this.gBand.slice(gStart);
    const nTail = this.nBand.slice(nStart);
    const aTail = this.aBand.slice(aStart);
    const wkTail = this.wkBand.slice(wkStart);
    const gProfTail = this.gProfile.slice(gpStart);
    const nProfTail = this.nProfile.slice(npStart);
    const bigTail = this.bigs.slice(bigStart);
    const evTail = this.events.slice(evStart);
    this.emitBars = this.bars.length;
    this.emitG = this.gBand.length;
    this.emitN = this.nBand.length;
    this.emitA = this.aBand.length;
    this.emitWk = this.wkBand.length;
    this.emitGP = this.gProfile.length;
    this.emitNP = this.nProfile.length;
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
