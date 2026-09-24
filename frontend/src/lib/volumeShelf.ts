// Volume shelves: where size is *building*, as opposed to where size has been.
//
// A volume profile is a cumulative integral. A tall row says volume happened at
// that price at some point in the session — not when, and not whether it is
// still happening. "Volume building at a level over the last half hour" is a
// rate question, and the only tool the chart had for it was the fixed-range
// profile, where you drag a box across the bars you meant and read the shape by
// eye. This module is that drag, done on a schedule and thresholded.
//
// THE DENOMINATOR IS THE WHOLE THING. Ask which row has the most volume in the
// last N minutes and the answer is "wherever price sat", every single time —
// twice over:
//
//   the clock        NQ trades ~71 ticks a 30-second bar just after the bell and
//                    ~27 by the close (the vol ruler's own reading), so a raw
//                    volume number means something different at 09:31 than at
//                    15:31 and rows from the two cannot be compared.
//
//   time-at-price    volume accrues at a price mechanically because price is
//                    *there*. A row that price camped on for twenty minutes
//                    outweighs one it crossed twice in size, without anything
//                    having accumulated in the sense anybody means.
//
// Both die to the same fix, and it is the textbook one: divide by time at
// price — how long price actually spent trading each row, in seconds (see
// `timeAtPrice` for why seconds and not a bar count). `volume / time` is size
// per unit of visit. A price a lot traded at in little time stands out; a price
// price merely sat at does not. Z-scoring that across the window's occupied rows
// takes the clock out too, because every row in a window shares its clock.
//
// What this module does NOT claim: that a shelf is support, resistance, or worth
// trading. Every level-geometry study in this repo has come back null — the
// stable-level one measured 13,284 touches and found developing POC/VAH/VAL hold
// 45.5%, with how long the level had been building making no difference at all
// (permutation p = 1.00). Shelves differ from those on one axis (they measure
// concentration of size, not the position of a summary statistic) and that axis
// is untested. Until it is tested, this draws and it measures. It does not
// advise.

import { smoothed, type VolumeProfile } from "./volumeProfile";
import { deviationOf } from "./deltaFlow";

/** The bar fields a shelf reads. Structural on purpose: the journal's charts and
 *  the replay engine each have their own `Bar`, and every field below means the
 *  same thing in both. */
export interface ShelfBar {
  time: number;
  high: number;
  low: number;
}

export interface ShelfParams {
  /** Trailing window, in minutes, each reading is taken over. */
  windowMin: number;
  /** How many standard deviations above the window's mean a row's size-per-visit
   *  must stand to belong to a shelf. The one threshold in the module, and the
   *  raster is unthresholded precisely so it always shows what this is cutting. */
  zMin: number;
  /** Narrowest band worth calling a shelf, in ticks. A single remarkable row is
   *  a print, not a zone, and at tick resolution there are always a few.
   *
   *  Counted on the *smoothed* curve, which costs a band roughly one row at each
   *  shoulder — the rolloff drops the outermost rows under `zMin` before the
   *  band really ends. So the narrowest band that survives is about two ticks
   *  wider than this number says. Left as-is rather than compensated: the
   *  compensation would be a second fudge tuned against the first, and the
   *  raster shows the true width regardless. */
  minTicks: number;
  /** How long a band must keep qualifying before it is a shelf, in minutes.
   *
   *  This is the "for a certain period of time" half of the question, and
   *  leaving it out is not a smaller version of this layer — it is a different
   *  and useless one. Without it a real NQ session yields ~290 bands and
   *  something is "a shelf" on 87% of bars, which is a layer that has told you
   *  nothing. A single window catching a burst is an *event*; a band that keeps
   *  reappearing window after window is the thing the question was about. */
  minHoldMin: number;
  /** Smoothing width as a share of the row count, handed to `smoothed`. Noise
   *  suppression only — it moves no boundary that `zMin` does not. */
  smooth: number;
  /** How often the window is re-read, in seconds of chart time.
   *
   *  A reading is taken over the trailing `windowMin`, so two readings a second
   *  apart are the same thirty minutes with one bar swapped — they cannot differ
   *  by anything worth drawing. Re-reading per bar is therefore pure cost, and on
   *  these charts it is a lot of it: a tick-bucketed session runs to hundreds of
   *  bars, each wanting its own profile, its own TPO pass and its own column of
   *  raster. Sampling on the clock instead ties the work to how fast the reading
   *  can actually change rather than to how fast bars happen to print. */
  stepSec: number;
}

export const DEFAULT_SHELF_PARAMS: ShelfParams = {
  windowMin: 30,
  zMin: 2,
  minTicks: 4,
  minHoldMin: 10,
  smooth: 0.04,
  stepSec: 60,
};

/**
 * Which bars to take a reading at — the first, the last, and one every
 * `stepSec` of chart time between.
 *
 * The last bar is always included whatever the spacing, because it is the live
 * edge: on a replay or a live chart that is the bar the trader is looking at,
 * and a shelf that appears a minute late there is a shelf that appeared after
 * the decision it was meant to inform.
 */
export function evalBars(bars: ShelfBar[], stepSec: number): number[] {
  if (bars.length === 0) return [];
  const out = [0];
  let last = bars[0].time;
  for (let i = 1; i < bars.length - 1; i++) {
    if (bars[i].time - last < stepSec) continue;
    out.push(i);
    last = bars[i].time;
  }
  if (bars.length > 1) out.push(bars.length - 1);
  return out;
}

/**
 * First bar index of the trailing `windowMin` window ending at bar `i`.
 *
 * Walked back by each bar's own timestamp rather than by a bar count, because
 * none of these charts is reliably one bar a minute: they are tick-, volume- and
 * custom-interval bucketed, so a 30-minute window is six bars overnight and
 * dozens through the open. Estimating one bar count for the day off the spacing
 * of the first two puts most of the session on the wrong window — quietly, since
 * every window still produces *a* reading.
 */
export function windowStart(bars: ShelfBar[], i: number, windowMin: number): number {
  const cutoff = bars[i].time - windowMin * 60;
  let j = i;
  while (j > 0 && bars[j - 1].time >= cutoff) j--;
  return j;
}

/** One reading: a hump in the time-normalised distribution at one instant. */
export interface Shelf {
  /** Price span of the hump, from `ProfileNode`'s saddle-bounded footprint. */
  lo: number;
  hi: number;
  /** The peak row's mid price. */
  price: number;
  /** How far the peak row's size-per-visit stood above the window's mean, in
   *  standard deviations across occupied rows. The number `zMin` cut on. */
  z: number;
}

/** A shelf followed across bars: the same hump, the stretch it held for, and
 *  what has happened to it since.
 *
 *  A box outlives its detection. The band stops qualifying once the window has
 *  moved past the size that made it — which with a two-hour window is a couple
 *  of hours after price left — but the *claim* it makes ("a lot traded here in
 *  few visits") stands until price comes back and deals with it. So the drawn
 *  right edge keeps running to the live edge until it is cleared, and a box that
 *  reaches the right-hand side of the chart is exactly the set of zones price has
 *  not returned to. That is the whole reason to draw them past their detection,
 *  and it needs no extra ink to read: an untested box touches the edge, a cleared
 *  one stops where price came back. */
export interface ShelfBox {
  lo: number;
  hi: number;
  from: number;
  /** Right edge as drawn. Advances while the shelf is detected, keeps advancing
   *  while it stands untested, and freezes on the bar price returns. */
  to: number;
  /** Last time the band was actually detected.
   *
   *  The hold gate reads this and never `to`. Reading `to` would let a band that
   *  qualified for two minutes satisfy a five-minute hold by standing untested
   *  for three more — the gate would stop being about how long size took to
   *  build and start being about how long price stayed away. */
  detectedTo: number;
  /** The strongest reading seen while it has been alive — a shelf is worth what
   *  it was at its best, not what it has decayed to on the bar it dies. */
  z: number;
  /** Still detected in the newest window. Drawn with a solid right edge, the
   *  same convention the fixed-range tool uses for a latched profile. */
  live: boolean;
  /** A bar has traded wholly clear of the band, so a return would now mean
   *  something.
   *
   *  Nothing can clear a shelf before this, and the reason is that price is
   *  *inside* the band while it forms — testing for a touch straight away would
   *  clear almost every shelf on the bar after it closed, against price that had
   *  never gone anywhere. The whole bar and not just its close, because near a
   *  band that is still building, closes hop off it constantly while the bar
   *  itself goes on trading in it: arming on a close outside was tried, and the
   *  wick after any bar that happened to settle a few ticks off the band read as
   *  "price came back" — shelves were cleared against the very trading that was
   *  building them. A bar that never touched the band is the statement price
   *  actually left. Same shape as the Modern VWAP anchor's `distance` rearm,
   *  minus its tick margin: that arms off a level with no width, where a margin
   *  is the only available meaning for "away". A band has width already.
   *
   *  Deliberately indifferent to whether the band is still *detected*. Departure
   *  is a fact about price; detection is a fact about the trailing window, and
   *  with a one- or two-hour window a band stays detected for the whole window
   *  after price has left. This first gated on detection lapsing, and the cost
   *  was a blind stretch exactly that long: price could leave and come back
   *  while the window still remembered the size, the revisit counted for
   *  nothing, `to` overran the truth, and the level tagger went on scoring
   *  fills against bands price had already dealt with. */
  armed: boolean;
  /** Price has traded back into the band since arming. `to` is frozen at the bar
   *  that did it, and a box this happens to while still detected is retired on
   *  the spot: the size may go on qualifying in the next window, but the claim
   *  that was tested is this box's, and what re-forms after the test is a new
   *  claim — a new box, owing the hold gate from scratch. */
  cleared: boolean;
}

/**
 * Time at price per profile row: how many *seconds* of these bars traded that
 * price. A bar counts for a row when their price spans overlap at all — the
 * classic Market Profile rule — and contributes its own duration, not 1.
 *
 * Weighted by duration rather than counted, because a bar is only a time slice
 * on a time-bucketed chart, and most charts this layer draws on are tick- or
 * volume-bucketed — activity-clocked. There, counting bars puts activity in the
 * denominator of the very ratio the numerator measures: a burst spawns bars *by*
 * trading, so its rows collect TPO in proportion to their volume and
 * `volume / TPO` collapses toward the bucket size for burst and camp alike. That
 * is the same self-cancellation `detectShelves` refuses estimated profiles over
 * (`exact`), arriving through the denominator instead. Seconds restore the
 * asymmetry the reading is about: a camp of many short bars accrues lots of
 * time, a burst accrues little, and the ratio tells them apart again.
 *
 * On uniformly time-bucketed bars every duration is equal, the weights are one
 * constant factor, and the z-scores — which are scale-invariant — come out
 * identical to the bar-count version. So this changes nothing where counting was
 * already right.
 *
 * A bar's duration is the gap to the *next* bar (the fixture's and the tape's
 * convention: a bar's stamp opens it); the last bar, which has no next, reuses
 * the previous gap — it is the live bar, and any figure for it is provisional
 * until the next bar exists. Read off the bars' high/low rather than off the
 * footprint, deliberately: that keeps it the identical computation on a chart
 * that ships a per-bar footprint and on one that ships only OHLC.
 */
export function timeAtPrice(p: VolumeProfile, bars: ShelfBar[]): Float64Array {
  const n = p.rows.length;
  const out = new Float64Array(n);
  let prev = 1;
  for (let k = 0; k < bars.length; k++) {
    const gap = k + 1 < bars.length ? bars[k + 1].time - bars[k].time : 0;
    const d = gap > 0 ? gap : prev;
    if (gap > 0) prev = gap;
    const b = bars[k];
    for (let i = 0; i < n; i++) {
      if (b.low <= p.rows[i].high && b.high >= p.rows[i].low) out[i] += d;
    }
  }
  return out;
}

/** What one row's size-per-visit was, and how unusual it was for this window. */
export interface ShelfResidual {
  /** `volume / timeAtPrice` per row, smoothed. Zero where nothing traded —
   *  which is a true reading here, unlike elsewhere in this codebase: a row no
   *  bar reached has no size per visit because it has no size. */
  residual: Float64Array;
  /** `residual` in standard deviations from the mean, computed across occupied
   *  rows only so the empty tails of the price axis cannot drag the mean down
   *  and make every traded row look remarkable. NaN where unoccupied. */
  z: Float64Array;
  /** Rows some bar traded at — the population the z-scores were taken over. */
  occupied: number;
}

/**
 * Fewest occupied rows a window may have and still be scored.
 *
 * Not a taste threshold — a z-score over `n` points cannot exceed `(n-1)/√n`,
 * because one point can only carry so much of a spread it is itself part of. At
 * n = 5 the ceiling is 1.79, so a `zMin` of 2 is *unreachable* and the detector
 * reports nothing however concentrated the size was; at n = 10 it is 2.85, which
 * a bimodal window (a camp and a burst, nothing between) still cannot get near.
 * Twenty rows puts the ceiling at 4.25 and leaves the usual thresholds room to
 * mean what they say.
 *
 * A window this thin therefore returns *no reading* rather than a quiet zero —
 * the distinction this codebase draws everywhere, and it matters most here,
 * because "no shelf" and "cannot tell" would otherwise look identical on a
 * chart. Windows are this thin in practice: the first minutes after a gap open,
 * a thin overnight stretch, or any window where price jumped and left the rows
 * between two regions untraded.
 */
export const MIN_OCCUPIED_ROWS = 20;

export function shelfResidual(
  p: VolumeProfile,
  timeAt: Float64Array,
  smooth = DEFAULT_SHELF_PARAMS.smooth,
): ShelfResidual {
  const n = p.rows.length;
  const raw = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    raw[i] = timeAt[i] > 0 ? p.rows[i].volume / timeAt[i] : 0;
  }

  // Smoothed with zero pads on both ends. The pads are not cosmetic: `smoothed`
  // takes a *partial* centred mean at the array edges, dividing by however many
  // terms are in range, so a tall block at the last row averages over fewer and
  // fewer rows and the curve rises into the boundary instead of decaying. Since
  // a profile's rows stop exactly where the session's range does, that boundary
  // is where a spike high lives — the very shape this layer wants to catch. Zero
  // is not a convenient filler there either: above the high nothing traded.
  const k = Math.max(3, Math.trunc(n * smooth) | 1);
  const pad = Math.max(2, (k - 1) >> 1);
  const wide = new Float64Array(n + 2 * pad);
  wide.set(raw, pad);
  const smWide = smoothed(wide, k);
  const residual = smWide.slice(pad, pad + n);

  let sum = 0;
  let occupied = 0;
  for (let i = 0; i < n; i++) {
    if (timeAt[i] > 0) {
      sum += residual[i];
      occupied += 1;
    }
  }
  const z = new Float64Array(n).fill(NaN);
  if (occupied < 2) return { residual, z, occupied };

  const mean = sum / occupied;
  let ss = 0;
  for (let i = 0; i < n; i++) if (timeAt[i] > 0) ss += (residual[i] - mean) ** 2;
  const sd = Math.sqrt(ss / occupied);
  // A window where every occupied row carried the same size per visit has no
  // standout by construction. Leaving z as NaN says that; dividing by zero would
  // say every row was infinitely remarkable.
  if (!(sd > 0)) return { residual, z, occupied };

  for (let i = 0; i < n; i++) if (timeAt[i] > 0) z[i] = (residual[i] - mean) / sd;
  return { residual, z, occupied };
}

/**
 * The shelves in one window.
 *
 * A shelf is a maximal contiguous run of rows whose size-per-visit stands at
 * least `zMin` above the window's mean, at least `minTicks` tall.
 *
 * A run and not a prominence hump, which is what a reader of this codebase would
 * expect given `profileNodes` sits right next door — and the reason is worth
 * recording, because the hump version was written first and thrown away.
 * Prominence exists to find humps in a *cumulative* profile, where there is no
 * natural threshold and the only way to say a bump matters is to compare it with
 * the valleys beside it. Here the normalisation has already produced a quantity
 * with a meaningful zero and a meaningful scale, so the zone is simply where it
 * is high, and its boundary is where it stops being high. Running a peak finder
 * over it instead put the reported price *outside* the burst on narrow spikes —
 * the smoothed maximum drifts toward whichever side has the fatter tail — and
 * needed two more knobs (prominence, and a hump's footprint definition) to say
 * something the threshold already says.
 *
 * `exact` is the caller's statement that this profile came off real
 * volume-at-price (`computeTickProfile` / `computeTapeProfileRanges`) rather than
 * off `computeVolumeProfile`, which spreads a bar's single volume number across
 * the rows its high-low covers. Under that spreading rule `volume / TPO` is
 * near-constant by construction — the estimate divides by the very thing this
 * divides by — so every reading would be a shelf-shaped artefact of the
 * estimator. It is a required argument and not a sniff test because there is no
 * honest structural signal to sniff (`hasDelta` is also false on a real tape
 * that arrived untagged), and a layer that silently draws the estimator's shape
 * is worse than one that draws nothing.
 */
export function detectShelves(
  p: VolumeProfile | null,
  bars: ShelfBar[],
  exact: boolean,
  tickSize: number,
  params: ShelfParams = DEFAULT_SHELF_PARAMS,
): { shelves: Shelf[]; reading: ShelfResidual | null } {
  if (!p || !exact || bars.length === 0 || !(tickSize > 0)) {
    return { shelves: [], reading: null };
  }

  const timeAt = timeAtPrice(p, bars);
  const reading = shelfResidual(p, timeAt, params.smooth);
  // Too thin to score — see MIN_OCCUPIED_ROWS. The reading still goes back so the
  // raster can draw what little there was; only the thresholded read is withheld.
  if (reading.occupied < MIN_OCCUPIED_ROWS) return { shelves: [], reading };

  const n = p.rows.length;
  // A row is not always one tick: the profile groups rows when a session's range
  // is wide (`MAX_LEVELS`), so a width in ticks has to be converted through the
  // pitch these rows actually came out at.
  const ticksPerRow = Math.max((p.rows[0].high - p.rows[0].low) / tickSize, 1e-9);
  const minRows = Math.max(1, Math.round(params.minTicks / ticksPerRow));

  const shelves: Shelf[] = [];
  let start = -1;
  for (let i = 0; i <= n; i++) {
    const over = i < n && Number.isFinite(reading.z[i]) && reading.z[i] >= params.zMin;
    if (over && start < 0) start = i;
    if (over || start < 0) continue;

    // Run [start, i) just ended.
    if (i - start >= minRows) {
      let lo = Infinity;
      let hi = -Infinity;
      let best = start;
      for (let j = start; j < i; j++) {
        if (p.rows[j].low < lo) lo = p.rows[j].low;
        if (p.rows[j].high > hi) hi = p.rows[j].high;
        if (reading.z[j] > reading.z[best]) best = j;
      }
      shelves.push({
        lo,
        hi,
        price: (p.rows[best].low + p.rows[best].high) / 2,
        z: reading.z[best],
      });
    }
    start = -1;
  }
  return { shelves, reading };
}

/**
 * The same window read for *flow* instead of size: per row, how one-sidedly it
 * traded. `null` when the profile carries no aggressor tag at all.
 *
 * `deltaFlow.deviationOf` and not a second statistic, so the raster and the
 * delta lane beside the profile cannot disagree about which way a row leaned —
 * and because that function has already answered the question this would
 * otherwise have to: a row's delta swings by about `sqrt(volume)` on chance
 * alone, so `delta / sqrt(volume)` is the form whose spread is the same at any
 * row size. A plain `delta / volume` ratio would let a four-lot row reach ±1.0
 * on a coin toss and paint the top and bottom ticks of every window.
 *
 * It also carries its own relevance floor (a row must hold 1% of the busiest
 * row's volume), which is why this needs no minimum-volume knob of its own.
 *
 * NaN — not zero — where there is no reading. Zero is a real and different
 * fact here: the two sides cancelled exactly.
 *
 * Absolute, so the raster can keep the fixed ramp the size field uses. Every
 * other reading in `deltaFlow` is rescaled against its own profile's maximum,
 * which is right for a bar length in a lane and wrong here: each raster column
 * is its own profile, so a per-column maximum would give every column a
 * full-strength cell and make a placid window and a violent one paint the same.
 * That comparison is the entire reason the raster exists.
 */
export function shelfFlow(p: VolumeProfile | null): Float64Array | null {
  if (!p || !p.hasDelta) return null;
  const out = new Float64Array(p.rows.length).fill(NaN);
  for (let i = 0; i < p.rows.length; i++) {
    const d = deviationOf(p, i);
    if (d != null) out[i] = d;
  }
  return out;
}

/** Two shelves are the same shelf when their price spans overlap. Bare overlap
 *  rather than a tolerance: the spans already come from the distribution's own
 *  saddles, so a tolerance would be a second threshold doing a job the first one
 *  has done. */
function overlaps(a: { lo: number; hi: number }, b: { lo: number; hi: number }): boolean {
  return a.lo <= b.hi && a.hi >= b.lo;
}

/**
 * Shelves followed across bars.
 *
 * A reading is per-window; a *shelf* is the thing that keeps showing up in
 * consecutive windows, and it is the shelf a box draws. Fed one window's
 * detection per bar close, in bar order.
 */
export class ShelfTracker {
  private active: ShelfBox[] = [];
  private done: ShelfBox[] = [];
  /** `graceBars` is how many consecutive misses are tolerated before a shelf is
   *  closed. One bar of absence is usually the band dipping under the threshold
   *  rather than the size leaving, and closing on it would shatter one shelf
   *  into a stack of boxes.
   *
   *  `minHoldSec` is the duration gate: nothing is *reported* until it has held
   *  that long. Tracked from the first window it appeared in, so a shelf becomes
   *  visible the moment it earns it and never retroactively — which is what
   *  keeps this honest on a replay stepping bar by bar. */
  constructor(
    private readonly graceBars = 2,
    private readonly minHoldSec = DEFAULT_SHELF_PARAMS.minHoldMin * 60,
  ) {}

  private held(b: ShelfBox): boolean {
    return b.detectedTo - b.from >= this.minHoldSec;
  }

  /** Bars this box has gone unseen for, parallel to `active`. */
  private missed: number[] = [];
  /** Time of the last push, so the arm/clear sweep can tell which of the bars it
   *  is handed are new. */
  private lastTime = -Infinity;

  /**
   * One window's detection, plus the bars it was taken over.
   *
   * `window` is the same slice that was handed to `detectShelves`, so no caller
   * has to keep a second one. Only the bars in it newer than the previous push
   * are swept — the slices of consecutive readings overlap almost entirely, and
   * re-walking them would let a bar from before a shelf existed clear it.
   *
   * The whole slice and not just the reading's own bar, because readings are
   * taken on a clock (`stepSec`) and bars are not: a wick that entered a band
   * and left again between two readings is exactly the revisit this is for, and
   * testing only the bar the reading landed on would never see it.
   */
  push(time: number, shelves: Shelf[], window: ShelfBar[] = []): void {
    const matched = new Set<number>();

    for (const s of shelves) {
      const at = this.active.findIndex((b, i) => !matched.has(i) && overlaps(b, s));
      if (at >= 0) {
        const b = this.active[at];
        // The span tracks the latest reading — a shelf that thickens or drifts
        // is still that shelf, and freezing its first span would draw a box the
        // distribution has since moved out of.
        b.lo = s.lo;
        b.hi = s.hi;
        b.to = time;
        b.detectedTo = time;
        b.z = Math.max(b.z, s.z);
        b.live = true;
        this.missed[at] = 0;
        matched.add(at);
        continue;
      }
      this.active.push({
        lo: s.lo,
        hi: s.hi,
        from: time,
        to: time,
        detectedTo: time,
        z: s.z,
        live: true,
        armed: false,
        cleared: false,
      });
      this.missed.push(0);
      matched.add(this.active.length - 1);
    }

    const keep: ShelfBox[] = [];
    const keptMiss: number[] = [];
    for (let i = 0; i < this.active.length; i++) {
      if (matched.has(i)) {
        keep.push(this.active[i]);
        keptMiss.push(0);
        continue;
      }
      const miss = this.missed[i] + 1;
      if (miss > this.graceBars) {
        this.active[i].live = false;
        this.done.push(this.active[i]);
      } else {
        keep.push(this.active[i]);
        keptMiss.push(miss);
      }
    }
    this.active = keep;
    this.missed = keptMiss;

    // Arm and clear, closed and still-detected boxes alike — departure and
    // return are facts about price, and whether the trailing window still
    // remembers the size has no bearing on either (see `armed`). An arming bar
    // trades wholly clear of the band, so it cannot also be the return — the
    // clear always comes from a later bar, by construction rather than by rule.
    const sweep = (box: ShelfBox, b: ShelfBar) => {
      if (box.cleared) return;
      if (!box.armed) {
        if (b.high < box.lo || b.low > box.hi) box.armed = true;
        return;
      }
      if (b.low <= box.hi && b.high >= box.lo) {
        box.cleared = true;
        box.to = b.time;
      }
    };
    for (const b of window) {
      if (b.time <= this.lastTime) continue;
      for (const box of this.done) sweep(box, b);
      for (const box of this.active) sweep(box, b);
    }
    // A still-detected box that just got cleared retires now: what the next
    // window re-detects there is a new claim owing the hold gate from scratch,
    // not this box growing past the bar that tested it.
    for (let i = this.active.length - 1; i >= 0; i--) {
      if (!this.active[i].cleared) continue;
      const [box] = this.active.splice(i, 1);
      this.missed.splice(i, 1);
      box.live = false;
      this.done.push(box);
    }
    this.lastTime = time;
    // Whatever is still standing runs to the live edge.
    for (const box of this.done) if (!box.cleared) box.to = time;
  }

  /** Every shelf that held long enough, closed and still running, oldest first.
   *  Bands that qualified for a window or two and vanished are dropped here
   *  rather than filtered by the renderer — a caller drawing `boxes()` and a
   *  caller reading `strongest()` must not disagree about what a shelf is. */
  boxes(): ShelfBox[] {
    return [...this.done, ...this.active]
      .filter((b) => this.held(b))
      .sort((a, b) => a.from - b.from);
  }

  /**
   * The shelf closest to `price` that has held and that price has not been back
   * to — what the level tagger measures fills against. Null when none qualifies.
   *
   * Standing, not merely detected: a band whose size the window has forgotten is
   * still a band price has not returned to, and "did I fill on a shelf nobody
   * had come back to?" is the question this measurement exists to make askable.
   * Restricting it to actively-detected shelves made that question unposable,
   * because the window forgets a shelf long before price deals with it.
   *
   * Nearest and not strongest, which is what this returned first and which those
   * two changes together made wrong. While only *live* shelves counted, the
   * strongest was also near price by construction — a detected shelf sits inside
   * a window of recent bars. An untested one does not: on a real session the
   * day's strongest untested band stayed named for 72% of the readings, from
   * over a hundred points away, so a fill sitting exactly on a weaker untested
   * shelf was scored against the far one and came back "not at a level". The
   * tagger's whole job is a distance, so the band it is handed has to be the one
   * price was actually near.
   */
  nearest(price: number): ShelfBox | null {
    let best: ShelfBox | null = null;
    let bestGap = Infinity;
    for (const b of [...this.active, ...this.done]) {
      if (b.cleared || !this.held(b)) continue;
      const gap =
        price >= b.lo && price <= b.hi
          ? 0
          : Math.min(Math.abs(price - b.lo), Math.abs(price - b.hi));
      // Ties go to the stronger band — two shelves the same distance away is a
      // coin flip otherwise, and a coin flip is not a reading.
      if (gap < bestGap || (gap === bestGap && best !== null && b.z > best.z)) {
        best = b;
        bestGap = gap;
      }
    }
    return best;
  }

  reset(): void {
    this.active = [];
    this.done = [];
    this.missed = [];
    this.lastTime = -Infinity;
  }
}
