// Volume profile: POC / VAH / VAL, computed two ways depending on what the chart
// actually has.
//
//   computeTickProfile   — exact. Real volume-at-price off the tape, one row per
//                          tick level. The sim's charts have the trades that made
//                          every bar, so they ship a per-bar footprint and get this.
//   computeVolumeProfile — estimated. The journal's charts come from Databento
//                          ohlcv-1m, which carries one volume number per bar and
//                          no distribution, so a bar's volume is spread across the
//                          rows its high-low range covers. The POC lands on the
//                          right shelf, but it's the profile of the bars, not of
//                          the tape.
//
// Both return the same shape, so the renderers don't care which one produced it.

import type { Bar } from "./chartTypes";

export interface ProfileRow {
  low: number;
  high: number;
  volume: number;
}

export interface VolumeProfile {
  rows: ProfileRow[];
  /** Largest single-row volume — the width the renderer scales against. */
  maxVolume: number;
  /** Point of control: mid price of the heaviest row. */
  poc: number;
  /** Value-area high/low: the outer edges of the rows holding `valueAreaPct`. */
  vah: number;
  val: number;
  /** Row indices inside the value area, for row-by-row shading. */
  valueArea: Set<number>;
}

export const PROFILE_ROWS = 60;
const VALUE_AREA_PCT = 0.7;

// Classic Market Profile value area: start at the POC and keep annexing
// whichever neighbouring *pair* of rows carries more volume until 70% of the
// session's volume is enclosed. Comparing pairs (rather than single rows) is
// what keeps the area from creeping up one thin row at a time on a lopsided
// distribution.
function valueAreaRange(rows: ProfileRow[], pocIdx: number, total: number): [number, number] {
  const target = total * VALUE_AREA_PCT;
  let acc = rows[pocIdx].volume;
  let lo = pocIdx;
  let hi = pocIdx;

  // Volume of the next `n` rows beyond the current edge, in the given direction.
  const pairAbove = () => (rows[hi + 1]?.volume ?? 0) + (rows[hi + 2]?.volume ?? 0);
  const pairBelow = () => (rows[lo - 1]?.volume ?? 0) + (rows[lo - 2]?.volume ?? 0);

  while (acc < target && (lo > 0 || hi < rows.length - 1)) {
    const up = hi < rows.length - 1 ? pairAbove() : -1;
    const down = lo > 0 ? pairBelow() : -1;
    if (up < 0 && down < 0) break;
    if (up >= down) {
      for (let i = 0; i < 2 && hi < rows.length - 1; i++) acc += rows[++hi].volume;
    } else {
      for (let i = 0; i < 2 && lo > 0; i++) acc += rows[--lo].volume;
    }
  }
  return [lo, hi];
}

/**
 * Above this many price levels the rows would be sub-pixel, so ticks get grouped
 * into fatter rows for drawing. Only bites on a very wide window (2000 levels is
 * a 500-point range on a 0.25 tick).
 */
const MAX_LEVELS = 2000;

/**
 * The exact profile: real volume-at-price off the tape, one row per tick level.
 *
 * `entries` is the [price, size] pairs of every trade in the selected bars (the
 * `footprint` the sim's chart API ships per bar). Unlike computeVolumeProfile
 * nothing is estimated here — and because rows *are* tick levels, POC/VAH/VAL
 * come back as real tradeable prices rather than arbitrary bucket midpoints.
 */
export function computeTickProfile(entries: number[][], tickSize: number): VolumeProfile | null {
  if (entries.length === 0 || !(tickSize > 0)) return null;

  let min = Infinity;
  let max = -Infinity;
  for (const [price] of entries) {
    if (price < min) min = price;
    if (price > max) max = price;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;

  const levels = Math.round((max - min) / tickSize) + 1;
  const group = Math.max(1, Math.ceil(levels / MAX_LEVELS));
  const step = tickSize * group;
  // Round, not floor: a row is a *centre* price, and the loop below finds a
  // price's row by rounding to the nearest centre. Flooring here would size the
  // array one row short whenever (max - min) / step lands on a half — an even
  // level count with group 2 — and the top price would then index off the end.
  // Both must be the same rounding, or the widest bar on the chart has no row.
  const rowCount = Math.round((max - min) / step) + 1;

  const rows: ProfileRow[] = Array.from({ length: rowCount }, (_, i) => {
    const centre = min + i * step;
    return { low: centre - step / 2, high: centre + step / 2, volume: 0 };
  });
  for (const [price, size] of entries) {
    rows[Math.round((price - min) / step)].volume += size;
  }

  let total = 0;
  let maxVolume = 0;
  let pocIdx = 0;
  for (let i = 0; i < rows.length; i++) {
    total += rows[i].volume;
    if (rows[i].volume > maxVolume) {
      maxVolume = rows[i].volume;
      pocIdx = i;
    }
  }
  if (total <= 0) return null;

  const [lo, hi] = valueAreaRange(rows, pocIdx, total);
  const valueArea = new Set<number>();
  for (let i = lo; i <= hi; i++) valueArea.add(i);

  // Report the levels themselves, not the row edges: these are prices you could
  // actually have traded at.
  const priceAt = (i: number) => min + i * step;
  return {
    rows,
    maxVolume,
    poc: priceAt(pocIdx),
    vah: priceAt(hi),
    val: priceAt(lo),
    valueArea,
  };
}

/**
 * The estimated profile, for charts with no tape: each bar's single volume number
 * is spread across the price rows its high-low range covers, in proportion to the
 * overlap. Used by the journal's charts, whose bars come from Databento ohlcv-1m.
 * The sim's charts ship a real footprint and use computeTickProfile instead.
 */
export function computeVolumeProfile(bars: Bar[], rowCount = PROFILE_ROWS): VolumeProfile | null {
  if (bars.length === 0) return null;

  let min = Infinity;
  let max = -Infinity;
  for (const b of bars) {
    if (b.low < min) min = b.low;
    if (b.high > max) max = b.high;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null;

  const step = (max - min) / rowCount;
  const rows: ProfileRow[] = Array.from({ length: rowCount }, (_, i) => ({
    low: min + i * step,
    high: min + (i + 1) * step,
    volume: 0,
  }));
  const rowOf = (price: number) =>
    Math.min(rowCount - 1, Math.max(0, Math.floor((price - min) / step)));

  for (const b of bars) {
    const vol = b.volume;
    if (!(vol > 0)) continue;
    const loIdx = rowOf(b.low);
    const hiIdx = rowOf(b.high);
    // A bar that opens and closes inside one row (or has no range at all) can't
    // be apportioned — it all belongs to that row.
    if (loIdx === hiIdx) {
      rows[loIdx].volume += vol;
      continue;
    }
    const span = b.high - b.low;
    for (let i = loIdx; i <= hiIdx; i++) {
      const overlap = Math.min(b.high, rows[i].high) - Math.max(b.low, rows[i].low);
      if (overlap > 0) rows[i].volume += vol * (overlap / span);
    }
  }

  let total = 0;
  let maxVolume = 0;
  let pocIdx = 0;
  for (let i = 0; i < rows.length; i++) {
    total += rows[i].volume;
    if (rows[i].volume > maxVolume) {
      maxVolume = rows[i].volume;
      pocIdx = i;
    }
  }
  if (total <= 0) return null;

  const [lo, hi] = valueAreaRange(rows, pocIdx, total);
  const valueArea = new Set<number>();
  for (let i = lo; i <= hi; i++) valueArea.add(i);

  return {
    rows,
    maxVolume,
    poc: (rows[pocIdx].low + rows[pocIdx].high) / 2,
    vah: rows[hi].high,
    val: rows[lo].low,
    valueArea,
  };
}
