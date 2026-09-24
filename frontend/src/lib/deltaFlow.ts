// How the delta lane is *read*, as opposed to what is in it.
//
// The lane itself is volumeProfile's `ProfileRow.delta` — signed aggressor volume
// per price row — and VolumeProfilePrimitive has always drawn it at
// |delta| / maxAbsDelta. That is an honest length, but it leaves the reader doing
// an unstated comparison: a 5000-lot row that netted +300 draws a longer bar than
// a 200-lot row that netted +150, even though the second is the one that traded
// one-way. Which of the two you care about is the whole question, and the lane
// as drawn can't put it.
//
// So this module turns the three judgments a reader was making by eye into
// numbers:
//
//   scale     — what the bar's length is measured against (`LaneScale`)
//   flagging  — which rows are far enough out to be worth naming (`deviationOf`)
//   verdict   — whether a flagged row's aggressors *moved* price or were absorbed
//
// The flag's statistic is the part that was hardest to get right and the part a
// later reader is most likely to "simplify" back into a bug: it is delta over
// *sqrt* volume, spread by a median absolute deviation, over rows above a
// relevance floor. Each of those three is there because the obvious version was
// measured doing the wrong thing — see `deviationOf` and `deviationStats`.
//
// It lives here and not in volumeProfile because `VolumeProfile` is a price-binned
// distribution with no time axis — `finalizeProfile` exists precisely so that its
// output doesn't depend on how the bins were filled — and a verdict is a statement
// about what happened *after* a row filled. Keeping the two apart is what lets the
// profile stay the same object whether it came off a footprint, a tape slice, or
// a live growing span.
//
// Nothing here is validated as an edge. It is a reading aid: it makes an existing
// lane legible, and deliberately emits no level family, no gate, and no size.

import type { Bar } from "./chartTypes";
import type { VolumeProfile } from "./volumeProfile";

/** What a lane reading actually consumes of a profile: the rows and their two
 *  maxima. Named so a *derived* source — the windowed re-binning below — doesn't
 *  have to fabricate a POC and a value area it never computed just to satisfy a
 *  type, and so a reader of these signatures can see that nothing here ever
 *  looks at one. */
export type LaneSource = Pick<VolumeProfile, "rows" | "maxVolume" | "maxAbsDelta" | "hasDelta">;

/**
 * What the delta bar's length is measured against.
 *
 *   net       — |delta| / maxAbsDelta. The original lane, and the default: the
 *               raw size of the imbalance, in contracts.
 *   imbalance — |delta| / row volume, rescaled by the largest such ratio on the
 *               chart. How *one-sided* the row traded, independent of how much
 *               traded there. This is the one that surfaces a thin one-way
 *               shelf — and, for the same reason, the one that makes a two-lot
 *               row look decisive. It is a display, not the flag's statistic.
 *   zscore    — |delta| / sqrt(volume), standardised across the chart's rows.
 *               How unusual the row is *for this session*, corrected for the
 *               fact that a thin row swings harder by chance (`deviationOf`).
 *               This is the one the flag agrees with.
 */
export type LaneScale = "net" | "imbalance" | "zscore";

export const LANE_SCALES: readonly LaneScale[] = ["net", "imbalance", "zscore"];

/**
 * What stretch of tape the lane reads its delta from.
 *
 *   session — the whole span the profile covers. The lane as it has always been:
 *             one cumulative number per row, which by the afternoon is mostly a
 *             record of the morning.
 *   m30/m15 — only the trailing N minutes, ending at the newest bar in the span.
 *             The volume rows keep the full span (the structure being traded
 *             against), while the lane answers "who is aggressing at these
 *             prices *now*" — lengths, colours, flags and verdicts all read from
 *             the window's own profile, so a row bought on the session but sold
 *             this half hour draws red.
 *   visit   — per-row rather than per-clock: each row splits into the delta of
 *             its *latest* contiguous visit (solid) and everything before it
 *             (washed behind). The read this exists for is the retest whose flow
 *             disagrees with the standing lean, and those rows are capped. Scale
 *             and flags don't apply here — see `readVisitLane`.
 */
export type LaneWindow = "session" | "m30" | "m15" | "visit";

export const LANE_WINDOWS: readonly LaneWindow[] = ["session", "m30", "m15", "visit"];

/** The timed windows' lengths. `session` and `visit` are absent on purpose —
 *  neither is a number of minutes, and a caller iterating this map gets exactly
 *  the windows that are. */
export const LANE_WINDOW_MINUTES: Partial<Record<LaneWindow, number>> = { m30: 30, m15: 15 };

/**
 * Flag thresholds, in spreads of the row deviation (`deviationStats`). 0 is off.
 *
 * Offered rather than free-typed because the number is not a fine adjustment:
 * these are "a bit unusual", "unusual", "rare", and a reader who wants 1.87
 * wants a different statistic, not a finer knob.
 */
export const FLAG_SIGMAS: readonly number[] = [0, 1.5, 2, 2.5];

/** How far ahead of a flagged row's own bar price is measured. */
export const FORWARD_BARS = 10;

/**
 * A flagged row's verdict.
 *
 *   initiative — the aggressors that piled in here got paid: price left in the
 *                direction they pushed.
 *   absorbed   — they didn't: someone passive took the other side and price was
 *                still here N bars later.
 *
 * Both come off the *same* delta reading, which is exactly why the lane alone
 * cannot tell them apart and why this needs a time axis to answer.
 */
export type Verdict = "initiative" | "absorbed";

export interface LaneReading {
  /**
   * Per row, the fraction of the lane's full width to draw, in [0, 1]. The
   * renderer's only input — it multiplies by whatever the lane is wide and does
   * no arithmetic of its own, so a scale mode can never mean one thing on the
   * viewport histogram and another on the fixed-range tool.
   *
   * Zero for a row with no delta, and for a row whose two sides cancelled
   * exactly. Those are different facts but the same drawing: nothing.
   */
  frac: Float64Array;
  /** The signed delta behind each `frac` — what colours the bar. `readLane`
   *  fills it from the source it read, which matters precisely when that source
   *  is not the viewport profile: a windowed lane's row can lean the *other way*
   *  from the session's cumulative number, and a renderer colouring off the
   *  session row would draw the window's length in the session's direction.
   *  Optional only because a reading built before this field existed may still
   *  be held somewhere; renderers fall back to the row's own delta. */
  delta?: Float64Array;
  /** Row indices past the flag threshold, low price to high. Empty when
   *  flagging is off, when the profile carries no delta, or when the session was
   *  uniform enough that nothing stood out.
   *
   *  A *visit* lane (`readVisitLane`) reuses this for the rows whose latest
   *  visit flipped against their standing lean — the same "worth a second look,
   *  capped in the lane" meaning, reached by a different test. Such a lane never
   *  carries verdicts, so the two claims cannot be confused downstream. */
  flagged: number[];
  /** The visit lane's second segment: the delta of every visit *before* the
   *  latest one, drawn washed behind `frac` so the standing lean stays visible
   *  under the current one. Absent on session and timed lanes. */
  under?: { frac: Float64Array; delta: Float64Array };
  /** Verdicts for flagged rows, when classification was asked for *and* the
   *  caller had a time axis to offer. Absent — not empty — when it wasn't, so a
   *  renderer can tell "not classified" from "classified as nothing". */
  verdict?: Map<number, Verdict>;
}

/** The signed imbalance of a row: its delta as a share of its own volume, in
 *  [-1, 1]. Zero-volume rows have no ratio (not a zero one) and are excluded
 *  everywhere below rather than divided by. */
function ratioOf(profile: LaneSource, i: number): number | null {
  const row = profile.rows[i];
  if (!(row.volume > 0) || row.delta == null) return null;
  return row.delta / row.volume;
}

/**
 * The row's delta measured against how much delta its volume could plausibly
 * have produced by chance: `delta / sqrt(volume)`.
 *
 * This, and not the ratio above, is what the flag is computed on — and the
 * difference is not a refinement, it is the difference between the flag working
 * and being actively misleading.
 *
 * The ratio's spread depends on how much traded in the row. Split `v` contracts
 * randomly between two sides and the delta lands around `sqrt(v)`, so the ratio
 * lands around `1/sqrt(v)`: a four-lot row reaches ±1.0 on a coin toss, while a
 * four-thousand-lot row physically cannot get past a few percent. Standardising
 * the ratio across rows therefore doesn't find one-sided rows, it finds *thin*
 * ones — and a profile's thin rows are its top and bottom ticks, which is the
 * noise a reader was already discarding by eye. Measured, every flag it produced
 * on a realistic profile sat on a row too small to care about.
 *
 * Dividing by `sqrt(volume)` instead gives a quantity whose spread is the same
 * at any row size, so heavy rows and thin ones compete on how one-sided they
 * were rather than on how little traded.
 */
export function deviationOf(profile: LaneSource, i: number): number | null {
  const row = profile.rows[i];
  if (!(row.volume > 0) || row.delta == null) return null;
  if (row.volume < profile.maxVolume * MIN_FLAG_SHARE) return null;
  return row.delta / Math.sqrt(row.volume);
}

/**
 * A row must hold at least this share of the busiest row's volume to be a flag
 * candidate at all.
 *
 * Six contracts that landed together are "entirely one-sided" on every measure
 * — ratio 1.0, and a couple of deviations past the coin toss — and they are
 * still six contracts at the last tick of the day. No amount of standardising
 * fixes that, because the row is not noisy, it is *irrelevant*: nothing happened
 * there for a reading to be about.
 *
 * So the floor is a statement about relevance rather than about statistics, and
 * it is deliberately far below anything a reader would call a shelf — a hundredth
 * of the POC. Sub-floor rows still draw in the lane; they are simply not
 * candidates for a mark, and they do not set the spread the others are judged
 * against.
 */
const MIN_FLAG_SHARE = 0.01;

/**
 * Where the middle of the deviations sits and how far they usually stray from
 * it — the median and the (normal-scaled) median absolute deviation.
 *
 * Median and MAD rather than mean and standard deviation, which is not a
 * refinement either. A profile reliably contains a couple of rows that ran hard
 * one way; squaring their distance lets them set the very spread they are being
 * compared against, and the result is that one extreme row hides every moderate
 * one behind it. Measured on a realistic profile, a row that netted 300 of 5000
 * — clearly worth a look — went unflagged purely because a more extreme row
 * elsewhere had widened the ruler. MAD does not square, so an outlier moves it
 * barely at all and the rest of the distribution keeps its scale.
 *
 * Standardised across the chart's own rows rather than against the coin-toss
 * null directly, even though the null would give a threshold with an absolute
 * meaning. Real flow is not independent tosses — one participant working an
 * order puts hundreds of contracts through one side — so the true spread is
 * several times the null's, and a fixed ±2 against it would flag most of a
 * trending session. Standardising against the session keeps the threshold
 * meaning "unusual *here*", which is the only version of unusual a reader can
 * act on.
 *
 * `spread` is 0 when too few rows qualify or when they agree closely enough that
 * the MAD collapses — both of which mean nothing stands out, and the callers
 * read it that way rather than dividing.
 */
function deviationStats(profile: LaneSource): { mid: number; spread: number; n: number } {
  const ds: number[] = [];
  for (let i = 0; i < profile.rows.length; i++) {
    const d = deviationOf(profile, i);
    if (d != null) ds.push(d);
  }
  if (ds.length === 0) return { mid: 0, spread: 0, n: 0 };
  if (ds.length < 2) return { mid: ds[0], spread: 0, n: ds.length };
  const mid = median(ds);
  // 1.4826 puts the MAD on the same footing as a standard deviation for
  // normal-ish data, so "±2σ" keeps the meaning a reader expects from the label.
  let spread = 1.4826 * median(ds.map((d) => Math.abs(d - mid)));
  if (spread === 0) {
    // MAD is exactly zero whenever more than half the rows share one deviation —
    // a quiet stretch where most rows netted flat, which is a real profile and
    // not a contrived one. Left at zero it would silently disable flagging on
    // the very session where the one row that *did* lean is most worth marking,
    // so the spread falls back to the standard deviation there. It gives up the
    // outlier resistance, which is the lesser loss: a distribution this
    // concentrated has no outlier crowd to resist.
    let sum = 0;
    for (const d of ds) sum += d;
    const mean = sum / ds.length;
    let ss = 0;
    for (const d of ds) ss += (d - mean) * (d - mean);
    return { mid: mean, spread: Math.sqrt(ss / ds.length), n: ds.length };
  }
  return { mid, spread, n: ds.length };
}

/** Median of a list. Sorts a copy — the caller's order is the row order, which
 *  everything else here indexes by. */
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * The lane's lengths and flags for one profile.
 *
 * Cheap — one pass per statistic over the rows, no tape — so callers may run it
 * wherever they rebuild the profile, including on every pan.
 *
 * `sigma` flags on the deviation z-score whatever `scale` is drawing. That is
 * deliberate: the flag is a claim about the row ("this traded unusually
 * one-sided"), and it must not change meaning because the reader switched how
 * long the bars are. A row flagged in `net` view is the same row flagged in
 * `zscore` view, at the same threshold.
 */
export function readLane(profile: LaneSource, scale: LaneScale, sigma: number): LaneReading {
  const n = profile.rows.length;
  const frac = new Float64Array(n);
  const delta = new Float64Array(n);
  const flagged: number[] = [];
  if (!profile.hasDelta) return { frac, delta, flagged };
  for (let i = 0; i < n; i++) delta[i] = profile.rows[i].delta ?? 0;

  const { mid, spread } = deviationStats(profile);

  // Each mode's denominator, found once. `net` keeps the original maxAbsDelta so
  // that switching a chart to this mode reproduces the lane it had before this
  // module existed, to the pixel.
  let maxRatio = 0;
  let maxZ = 0;
  if (scale !== "net") {
    for (let i = 0; i < n; i++) {
      const r = ratioOf(profile, i);
      if (r != null) {
        const a = Math.abs(r);
        if (a > maxRatio) maxRatio = a;
      }
      const d = deviationOf(profile, i);
      if (d != null && spread > 0) {
        const z = Math.abs((d - mid) / spread);
        if (z > maxZ) maxZ = z;
      }
    }
  }

  for (let i = 0; i < n; i++) {
    const row = profile.rows[i];

    if (scale === "net") {
      // The row may carry delta without carrying a volume-backed ratio (it
      // can't, in practice — delta comes from trades — but the two guards are
      // independent and this one answers for itself).
      frac[i] =
        profile.maxAbsDelta > 0 && row.delta != null
          ? Math.abs(row.delta) / profile.maxAbsDelta
          : 0;
    } else if (scale === "imbalance") {
      const r = ratioOf(profile, i);
      frac[i] = r != null && maxRatio > 0 ? Math.abs(r) / maxRatio : 0;
    } else {
      // A session whose rows all deviate equally has no spread to standardise
      // against; the honest z-lane there is empty, not full. A row under the
      // relevance floor draws nothing here either — not because it was flat, but
      // because too little traded there to say.
      const d = deviationOf(profile, i);
      frac[i] = d != null && spread > 0 && maxZ > 0 ? Math.abs((d - mid) / spread) / maxZ : 0;
    }

    if (sigma > 0 && spread > 0) {
      const d = deviationOf(profile, i);
      if (d != null && Math.abs((d - mid) / spread) >= sigma) flagged.push(i);
    }
  }

  return { frac, delta, flagged };
}

/** The centre price of a row. */
function centreOf(profile: LaneSource, i: number): number {
  const row = profile.rows[i];
  return (row.low + row.high) / 2;
}

/** Median high-low range of the bars in the window — the yardstick a
 *  displacement is judged against. Scale-free by construction, so the verdict
 *  needs no tick count baked into it and means the same thing on NQ as it would
 *  on a $4 instrument. */
function medianRange(bars: Bar[]): number {
  const rs: number[] = [];
  for (const b of bars) {
    const r = b.high - b.low;
    if (r > 0) rs.push(r);
  }
  if (rs.length === 0) return 0;
  rs.sort((a, b) => a - b);
  const m = rs.length >> 1;
  return rs.length % 2 ? rs[m] : (rs[m - 1] + rs[m]) / 2;
}

/**
 * Split flagged rows into aggressors who got paid and aggressors who got
 * absorbed.
 *
 * For each flagged row: find the bar that contributed the most |delta| to it —
 * the moment that row's imbalance actually happened, which for a row visited
 * twice is the visit that made it — then ask where price was `forwardBars`
 * later, in the direction the row's net delta was pushing.
 *
 * A row that pushed up and left up is `initiative`. A row that pushed up and was
 * still there is `absorbed`: someone passive was on the other side of all that
 * buying. The threshold between them is half the window's median bar range, so
 * "price didn't go anywhere" means what it means on this chart rather than a
 * fixed number of ticks.
 *
 * `deltaByBar` is the caller's time axis: the delta this row received in each bar
 * of the window, one entry per bar. The two charts fill it from different sources
 * (a per-bar footprint; a tape slice indexed by tick) and this doesn't care
 * which.
 *
 * Rows whose peak bar is too close to the right edge to look forward from get no
 * verdict — measuring against a bar that doesn't exist yet would answer with the
 * end of the window rather than with the market.
 */
export function classifyFlagged(
  profile: LaneSource,
  flagged: number[],
  deltaByBar: (rowIdx: number) => Float64Array,
  bars: Bar[],
  forwardBars: number = FORWARD_BARS,
): Map<number, Verdict> {
  const out = new Map<number, Verdict>();
  const tol = medianRange(bars) / 2;
  if (bars.length === 0 || !(tol > 0)) return out;

  for (const i of flagged) {
    const row = profile.rows[i];
    if (row.delta == null || row.delta === 0) continue;

    const contrib = deltaByBar(i);
    let peak = -1;
    let best = 0;
    for (let b = 0; b < contrib.length && b < bars.length; b++) {
      const a = Math.abs(contrib[b]);
      if (a > best) {
        best = a;
        peak = b;
      }
    }
    if (peak < 0) continue;

    const ahead = peak + forwardBars;
    if (ahead >= bars.length) continue;

    // Signed by the row's own direction: buying that was followed by a fall is
    // as absorbed as buying that went nowhere — more so — and both are the
    // opposite of initiative.
    const moved = (bars[ahead].close - centreOf(profile, i)) * Math.sign(row.delta);
    out.set(i, moved > tol ? "initiative" : "absorbed");
  }

  return out;
}

/**
 * A window's profile re-binned onto the viewport profile's rows, so a lane read
 * from it indexes — and draws — against the rows already on screen.
 *
 * The two profiles come off the same binning rules but not necessarily the same
 * grid: the tape profiles anchor rows at whole groups from price zero, the
 * footprint one at its own span's low, and the window's span can group finer
 * than the viewport's. So each window row is assigned to the viewport row whose
 * bounds contain its centre, by binary search rather than by re-deriving either
 * grid's arithmetic — a second copy of that rounding would be a second thing to
 * keep in step. A window row that straddles two viewport rows (coarser window
 * grouping — a wide window inside a narrow viewport, which the callers never
 * produce) goes wholly to the row holding its centre.
 *
 * The result carries the *window's* volume as well as its delta, and that is the
 * point rather than a convenience: the lane's statistics divide delta by the
 * volume it netted out of, and a 30-minute delta judged against a whole
 * session's volume would read every row as eerily quiet. Reading the lane off
 * this source is what makes "unusual" mean unusual *for the window*.
 *
 * Null when there is no window or it carries no delta — the callers fall back
 * to the session lane rather than drawing an empty one.
 */
export function windowedOnto(viewport: LaneSource, win: LaneSource | null): LaneSource | null {
  if (!win || !win.hasDelta) return null;
  const rowsV = viewport.rows;
  const n = rowsV.length;
  if (n === 0) return null;

  const vols = new Float64Array(n);
  const dels = new Float64Array(n);
  for (const row of win.rows) {
    if (row.volume <= 0) continue;
    const c = (row.low + row.high) / 2;
    // Largest row whose low is at or under the centre — which is the containing
    // row when the centre is in range, and the nearest edge row (a clamp, not a
    // miss) when a half-tick of rounding puts it just past either end.
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
      const m = (lo + hi + 1) >> 1;
      if (rowsV[m].low <= c) lo = m;
      else hi = m - 1;
    }
    vols[lo] += row.volume;
    dels[lo] += row.delta ?? 0;
  }

  let maxVolume = 0;
  let maxAbsDelta = 0;
  const rows = rowsV.map((r, i) => {
    if (vols[i] > maxVolume) maxVolume = vols[i];
    const a = Math.abs(dels[i]);
    if (a > maxAbsDelta) maxAbsDelta = a;
    return { low: r.low, high: r.high, volume: vols[i], delta: dels[i] };
  });
  if (maxVolume <= 0) return null;
  return { rows, maxVolume, maxAbsDelta, hasDelta: true };
}

/** Per row, the signed delta of its latest contiguous visit and of everything
 *  before it. Two arrays rather than a difference because both get drawn. */
export interface VisitSplit {
  prior: Float64Array;
  visit: Float64Array;
}

/**
 * Split each row's delta into its latest visit and all the visits before.
 *
 * A visit is a maximal run of *consecutive bars* whose high-low range reaches
 * the row — bar ranges rather than trades, so a bar that traversed the row
 * without printing there still keeps the run alive, and a run isn't broken by a
 * quiet bar sitting right on top of the price. For the rows price is in now the
 * latest visit is the ongoing one, which is what makes this a present-tense
 * reading; for rows price has left it is the most recent test, which is the
 * next best answer to the same question.
 *
 * `eachTrade` is the caller's tape: it must call `emit` once per trade with the
 * index of the bar it printed in (into `bars`), its price, and its signed size —
 * positive for a buy aggressor, negative for a sell, zero (or simply skipped)
 * for an untagged print. The two charts fill it from different sources (a
 * per-bar footprint; a tape slice cut by bar) and this doesn't care which,
 * exactly as `classifyFlagged` doesn't.
 *
 * One pass over the bars to find each row's final run, one pass over the trades
 * to split them against it. Nothing here rescales — that is `readVisitLane`'s
 * job, and keeping the split raw is what makes it testable as arithmetic.
 */
export function splitVisits(
  profile: LaneSource,
  bars: Bar[],
  eachTrade: (emit: (barIdx: number, price: number, delta: number) => void) => void,
): VisitSplit {
  const rows = profile.rows;
  const n = rows.length;
  const prior = new Float64Array(n);
  const visit = new Float64Array(n);
  if (n === 0 || bars.length === 0) return { prior, visit };

  // Containing row by binary search on the row bounds, same clamp as
  // `windowedOnto` — one rule for "which row is this price in" per module.
  const rowOf = (price: number): number => {
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
      const m = (lo + hi + 1) >> 1;
      if (rows[m].low <= price) lo = m;
      else hi = m - 1;
    }
    return lo;
  };

  // Each row's final run of touching bars. `lastBar` is the previous bar that
  // reached the row; a bar that arrives non-adjacent starts a new run.
  const runStart = new Int32Array(n).fill(-1);
  const lastBar = new Int32Array(n).fill(-2);
  for (let b = 0; b < bars.length; b++) {
    const rLo = rowOf(bars[b].low);
    const rHi = rowOf(bars[b].high);
    for (let r = rLo; r <= rHi; r++) {
      if (lastBar[r] !== b - 1) runStart[r] = b;
      lastBar[r] = b;
    }
  }

  eachTrade((b, price, d) => {
    if (d === 0) return;
    const r = rowOf(price);
    if (runStart[r] >= 0 && b >= runStart[r]) visit[r] += d;
    else prior[r] += d;
  });

  return { prior, visit };
}

/**
 * A flip must be at least this share of the biggest segment on the chart, on
 * *both* sides, to earn a cap. Signs alone flip constantly — a two-lot lean
 * against a two-lot lean is a coin toss twice — and a lane whose every third
 * row is capped marks nothing. A relevance floor, not a statistic, on the same
 * grounds as `MIN_FLAG_SHARE`.
 */
const FLIP_MIN_SHARE = 0.1;

/**
 * The visit lane: latest-visit delta as the bar, prior delta washed behind it,
 * and a cap on the rows where the two disagree.
 *
 * Both segments scale against one denominator — the largest single segment
 * anywhere on the chart — so a visit bar and the prior bar behind it are
 * comparable by length, which is the entire reading. That is `net` semantics by
 * construction; the scale modes don't apply, because splitting a z-score into
 * "this visit's z" and "prior z" isn't a decomposition of anything (the
 * statistic isn't additive over visits). For the same reason there are no
 * verdicts: the flag here is a *flip*, not an outlier, and classifying it
 * forward would be a different study.
 */
export function readVisitLane(profile: LaneSource, split: VisitSplit): LaneReading {
  const n = profile.rows.length;
  const frac = new Float64Array(n);
  const delta = new Float64Array(n);
  const under = { frac: new Float64Array(n), delta: new Float64Array(n) };
  const flagged: number[] = [];
  const lane: LaneReading = { frac, delta, flagged, under };
  if (!profile.hasDelta) return lane;

  let maxComp = 0;
  for (let i = 0; i < n; i++) {
    const a = Math.max(Math.abs(split.prior[i]), Math.abs(split.visit[i]));
    if (a > maxComp) maxComp = a;
  }
  if (maxComp <= 0) return lane;

  for (let i = 0; i < n; i++) {
    const p = split.prior[i];
    const v = split.visit[i];
    frac[i] = Math.abs(v) / maxComp;
    delta[i] = v;
    under.frac[i] = Math.abs(p) / maxComp;
    under.delta[i] = p;
    if (p * v < 0 && Math.min(Math.abs(p), Math.abs(v)) >= FLIP_MIN_SHARE * maxComp)
      flagged.push(i);
  }
  return lane;
}
