// How price is closing the gap to a level — the chart's side of an arithmetic
// that already exists and is already adopted.
//
// `gapCloser` below is a transcription of ``gap_closer`` in
// src/journal/sim/profile.py:43. That function is what the engine's drift-touch
// fade (src/journal/sim/rules.py) and the Interactions Lab's `closed_by` column
// both read, and its own comment says the definition "lives in exactly one
// place". This file makes that untrue in the letter unless it is fenced, so it
// is fenced: tests/test_level_approach.py bundles this module with the
// frontend's own esbuild, runs it under node, and compares it to the Python at
// every bar index — including the constant below.
//
// So: do not improve this function. If the arithmetic is wrong it is wrong in
// profile.py first, and this follows.
//
// One deliberate departure. The Python also returns the two closing distances,
// rounded — and Python rounds halves to even where JavaScript does not. Only the
// class is returned here, so there is no rounding to disagree about.

/** Bars of history the classification looks back over. Pinned against
 *  ``profile.GAP_LOOKBACK_BARS`` by the parity test: bump one and the other
 *  fails. Calibrated at the engine's bar sizes — five bars of a 500-tick chart
 *  is a real window, five bars of a 1-second chart is noise, which is why the
 *  panel labels its window with the actual bucketing rather than "5 bars". */
export const GAP_LOOKBACK_BARS = 5;

/** Who closed the distance between price and a level.
 *
 *  - `drift`   — they never converged over the window. Price was already
 *                loitering by the level and wiggled into contact.
 *  - `level`   — the level did the closing (share >= 0.6): a falling band
 *                chased by price, which tests nothing.
 *  - `price`   — price did the closing (share <= 0.4): a momentum test.
 *  - `both`    — they met in the middle.
 *  - `unknown` — not enough history yet, or the level did not exist across the
 *                window (a NaN in it). */
export type GapClass = "drift" | "level" | "price" | "both" | "unknown";

/**
 * Attribute the closing distance at bar `i`: price's move toward the level vs
 * the level's move toward price, over the last `lookback` bars.
 *
 * `values` is the level's own path and `close` the matching closes — **sampled
 * onto the same bar times**, one entry each per bar, `NaN` where the level had
 * no value. Callers must not index these by "bars back" into a level's native
 * array: the level arrays on the chart start at different bars (the Globex
 * anchor, the bell, a history-prefixed weekly), so an offset would pair one
 * bar's level with another bar's close and answer plausibly about nothing.
 */
export function gapCloser(
  values: readonly number[],
  close: readonly number[],
  i: number,
  lookback: number = GAP_LOOKBACK_BARS,
): GapClass {
  const j = i - Math.min(lookback, i);
  if (j >= i || Number.isNaN(values[j]) || Number.isNaN(values[i])) return "unknown";
  const toward = Math.sign(values[j] - close[j]); // +1: level overhead, -1: level below
  const priceClosed = (close[i] - close[j]) * toward;
  const levelClosed = -(values[i] - values[j]) * toward;
  const total = priceClosed + levelClosed;
  if (total <= 0) return "drift";
  const share = levelClosed / total;
  return share >= 0.6 ? "level" : share <= 0.4 ? "price" : "both";
}

// --- reading a chart's levels as a short list --------------------------------
//
// Below the line the arithmetic stops and the presentation starts. Nothing here
// is ported and nothing here is measured; it decides which levels are worth a
// row and how a stack of them collapses into one.

/** A level as the chart hands it over — see `EnumeratedLevel` in ReplayChart. */
export interface ApproachInput {
  family: string;
  label: string;
  /** Stable identity, carried through so a row can be armed (see lib/levelArm).
   *  Not derivable from family+label here: every hand-drawn line is called "your
   *  line", so the chart keys those by id and only the chart knows the id. */
  key: string;
  price: number;
  /** The level's own value at each bar of the window, `NaN` where it had none. */
  path: readonly number[];
}

/** One row of the panel: a price, and everything the chart draws at it. */
export interface ApproachRow {
  /** Mid of the members' prices — what the row is a row *about*. */
  price: number;
  /** Ticks from last price, signed: positive above, negative below. */
  dist: number;
  /** The members' shared class, `mixed` where they disagree, `unknown` where
   *  none of them could answer. */
  cls: GapClass | "mixed";
  members: { label: string; family: string; key: string; cls: GapClass }[];
  /** The row's identity for arming: its members' keys, in price order. A row is
   *  what you point at, so a row is what you arm — and a cluster that later
   *  breaks apart simply stops matching, which is right: the arm it made is a
   *  standing order at a frozen price and outlives the row that started it. */
  key: string;
}

/** How close two levels must be to be one level, in ticks. Four ticks is a point
 *  on NQ, which is the width at which two references stop being two things you
 *  could trade separately. Not measured — there is nothing to measure it
 *  against — so it is a default, not a finding. */
export const COLLINEAR_TICKS = 4;

/** How far from price a level still earns a row, in ticks. */
export const NEAR_TICKS = 40;

/**
 * Collapse the levels near `price` into rows, classifying each member's approach.
 *
 * Two levels join a row when they are within `COLLINEAR_TICKS` of each other —
 * but a row may not grow wider than twice that, or a ladder of near-levels
 * chains into one row spanning far more than "the same price". Chaining is the
 * classic failure of single-linkage clustering and it is what would turn this
 * from a confluence reader into a confluence launderer.
 *
 * A row's class is its members', when they agree. When they don't it is `mixed`,
 * which is a reading in itself: a static reference and a moving one disagreeing
 * about who closed the gap is exactly the case where a naive confluence count
 * would have told you the level was twice as strong. Members that cannot answer
 * (`unknown` — younger than the window) are left out of that agreement rather
 * than counted as a disagreement; a row of nothing but those stays `unknown`.
 */
export function clusterLevels(
  levels: readonly ApproachInput[],
  close: readonly number[],
  i: number,
  price: number,
  tickSize: number,
  opts: { collinearTicks?: number; nearTicks?: number } = {},
): ApproachRow[] {
  if (!(tickSize > 0) || !Number.isFinite(price)) return [];
  const tol = (opts.collinearTicks ?? COLLINEAR_TICKS) * tickSize;
  const near = (opts.nearTicks ?? NEAR_TICKS) * tickSize;

  const inRange = levels
    .filter((l) => Number.isFinite(l.price) && Math.abs(l.price - price) <= near)
    .sort((a, b) => a.price - b.price);

  const groups: ApproachInput[][] = [];
  for (const l of inRange) {
    const g = groups[groups.length - 1];
    const prev = g?.[g.length - 1];
    if (g && prev && l.price - prev.price <= tol && l.price - g[0].price <= tol * 2) g.push(l);
    else groups.push([l]);
  }

  return groups
    .map((g) => {
      const members = g.map((m) => ({
        label: m.label,
        family: m.family,
        key: m.key,
        cls: gapCloser(m.path, close, i),
      }));
      const answered = members.filter((m) => m.cls !== "unknown");
      const cls: GapClass | "mixed" = !answered.length
        ? "unknown"
        : answered.every((m) => m.cls === answered[0].cls)
          ? answered[0].cls
          : "mixed";
      const mid = (g[0].price + g[g.length - 1].price) / 2;
      const key = members.map((m) => m.key).join("+");
      return { price: mid, dist: (mid - price) / tickSize, cls, members, key };
    })
    // Highest price first, which is the order the rows are *built* in and the
    // one a reader falls back to. It is not what the panel shows: the chart
    // ranks the rows nearest-first, in `paintLevelDist`, by writing flex `order`
    // straight at the DOM.
    //
    // That ranking cannot be done here, and the reason is the one thing to
    // remember about this sort. Rows are rebuilt on bar close; distances are
    // repainted every frame. A distance-ranked list built at bar close is out of
    // order the moment price moves — it read "−15, −2, −21, −33" on the first
    // session it was pointed at. Price order cannot go stale, because the prices
    // don't move, so it is the right thing to hand over; ranking belongs
    // wherever the live price already is.
    .sort((a, b) => b.price - a.price);
}
