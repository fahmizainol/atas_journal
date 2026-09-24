// The volume-weighted accumulator, and what a bar contributes to one.
//
// The frontend half of `src/journal/sim/vwap.py`, and the same rule applies on
// this side of the wire: **there is one VWAP in this app and it accumulates
// trade prices.** Accumulating bar typical prices instead is a different
// statistic, not a cheaper spelling of the same one — the mids differ by a tick
// or three, and near an anchor the sigmas differ by far more, because a tick
// sigma carries the spread *inside* each bar as well as the spread between them.
// Drawing both under one name is what let a hand-placed ⚓ read differently on
// two charts, which is the bug this module exists to make unrepeatable.
//
// Two ways in, one arithmetic:
//
//   * **Tick by tick** — `add(price, size)`, which is what the replay engine
//     does as the tape plays.
//   * **Bar by bar** — `addBar(bar)`, for a chart that was never shipped the
//     tape. Each bar carries its own tick VWAP and tick variance (`tvwap`,
//     `tvar`, from `journal.sim.vwap.bar_moments`), and because sums are
//     associative, folding those forward from any anchor reproduces the tick
//     accumulation over the same range *exactly* — verified bit-exact on the
//     mid and to 8e-8 on sigma against a real 47k-tick session. Two floats per
//     bar buys every anchor the user can place, at bar cost rather than tick
//     cost.
//
// A bar with no moments falls back to `hlc3 × volume`, which is the old
// bar-domain approximation and is *not* interchangeable with the rest. Only one
// payload still lacks them (`api/charts_data.bars_window`, the timeframe-radio
// refetch, which is resampled from the bar store and has no tape behind it), and
// callers that can tell the user which arithmetic they got should.

/** The shape `addBar` reads. Structural, so both charts' `Bar` types satisfy it
 *  without either importing the other — same reason lib/modernVwap declares its
 *  own input shape. */
export interface VwapBar {
  high: number;
  low: number;
  close: number;
  volume: number;
  /** This bar's own tick VWAP, `Σ(p·v)/Σv` over its ticks. Absent where no tape
   *  stood behind the bar, or where the bar caught no volume at all. */
  tvwap?: number;
  /** This bar's own tick variance about `tvwap`. Absent alongside it. */
  tvar?: number;
}

/** Whether a bar can contribute tick-accurate moments, or only the hlc3
 *  approximation. Charts quote this so the two are never silently mixed. */
export function hasTickMoments(b: VwapBar): boolean {
  return Number.isFinite(b.tvwap) && Number.isFinite(b.tvar);
}

/** One bar's `(v, pv, p²v)`, tick-exact where the moments are there and hlc3 as
 *  the honest fallback where they are not. */
export function barMoments(b: VwapBar): { v: number; pv: number; p2v: number } {
  const v = b.volume > 0 ? b.volume : 0;
  if (!v) return { v: 0, pv: 0, p2v: 0 };
  if (hasTickMoments(b)) {
    const m = b.tvwap as number;
    // `p²v` back out of the variance: E[x²] = Var + E[x]². Shipping the variance
    // rather than E[x²] is what keeps the wire numbers small and the precision
    // good — see bar_moments' note on centring.
    return { v, pv: m * v, p2v: ((b.tvar as number) + m * m) * v };
  }
  const tp = (b.high + b.low + b.close) / 3;
  return { v, pv: tp * v, p2v: tp * tp * v };
}

/** A bar being built tick by tick, for `openBar`/`foldTick` below. */
interface FormingBar {
  volume: number;
  tvwap: number;
  tvar: number;
}

/** The moment fields a bar opens with: one observation, so the bar's VWAP is
 *  that print and its spread is nothing. */
export function openBar(price: number): { tvwap: number; tvar: number } {
  return { tvwap: price, tvar: 0 };
}

/**
 * Fold one print into a forming bar — volume and both moments together.
 *
 * One function rather than a `volume +=` beside two more lines at each of the
 * three places a bar grows: a bar whose volume and moments disagree would draw a
 * VWAP that is wrong by an amount nothing else on the chart could explain.
 *
 * `E[x²] = Var + E[x]²` on the way in and back out again. Within a single bar
 * the round trip is harmless — the sums are one bar's worth, so the subtraction
 * keeps ~8 significant digits on a variance of single digits. It is only across
 * a whole session that this form collapses, which is exactly why the *shipped*
 * per-bar variance is centred instead (see `journal.sim.vwap.bar_moments`).
 */
export function foldTick(bar: FormingBar, price: number, size: number): void {
  const v0 = bar.volume;
  const v1 = v0 + size;
  if (!(v1 > 0)) {
    bar.volume = v1;
    return;
  }
  const pv = bar.tvwap * v0 + price * size;
  const p2v = (bar.tvar + bar.tvwap * bar.tvwap) * v0 + price * price * size;
  const m = pv / v1;
  bar.volume = v1;
  bar.tvwap = m;
  bar.tvar = Math.max(p2v / v1 - m * m, 0);
}

/** A point on an anchored VWAP: the mid and both deviation bands. */
export interface VwapBandPoint {
  mid: number;
  sd: number;
  u1: number;
  l1: number;
  u2: number;
  l2: number;
}

/**
 * Volume-weighted accumulator for one anchored VWAP.
 *
 * `seed` is what was already behind the anchor before the first observation this
 * accumulator will ever see — the weekly anchor, which starts days before the
 * tape (see `journal.sim.vwap`'s `seed`). Zero for an anchor that starts inside
 * the range, which is every other one.
 */
export class Vwap {
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

  /** Fold in a whole bar. A bar with no volume moves nothing — it must not be
   *  allowed to drag the average, which is what a zeroed moment would do. */
  addBar(b: VwapBar) {
    const m = barMoments(b);
    if (!m.v) return;
    this.v += m.v;
    this.pv += m.pv;
    this.p2v += m.p2v;
  }

  /** Mid and sigma as they stand. NaN before anything has traded under the
   *  anchor — a caller draws a gap there rather than inventing a price. */
  read(): { mid: number; sd: number } {
    if (!(this.v > 0)) return { mid: NaN, sd: NaN };
    const mid = this.pv / this.v;
    // Clamped: the two moments accumulate independently, so rounding can put
    // E[x²] a hair under E[x]² on a range that has barely moved, and a negative
    // variance would come back NaN from the sqrt. Same guard as vwap.py's.
    return { mid, sd: Math.sqrt(Math.max(this.p2v / this.v - mid * mid, 0)) };
  }

  /** The full band, for the callers that draw all of it. */
  band(): VwapBandPoint {
    const { mid, sd } = this.read();
    return { mid, sd, u1: mid + sd, l1: mid - sd, u2: mid + 2 * sd, l2: mid - 2 * sd };
  }

  get active() {
    return this.v > 0;
  }
}
