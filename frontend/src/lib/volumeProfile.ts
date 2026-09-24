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
//
// A row also carries `delta` — its signed aggressor volume — wherever the source
// can answer for it: a tape whose aggressor tag was handed over, or a footprint
// whose levels shipped one. That is the same quantity the CVD pane accumulates,
// cut by price instead of by time, and it is what lets a profile be read for
// flow (who lifted, who hit) as well as for structure. The renderers draw it as
// its own histogram beside the volume rows rather than as a colour on them:
// volume-at-price and net-delta-at-price are two distributions, and a lane each
// is what lets you see a price that traded heavily *and* two-sided. Where there
// is no tag there is no delta, and `hasDelta` says so rather than the rows
// quietly reading zero.

import type { Bar } from "./chartTypes";
import { SIDE_BUY, SIDE_SELL } from "./replayEngine";

export interface ProfileRow {
  low: number;
  high: number;
  volume: number;
  /** Signed aggressor volume in this row: buy market orders minus sell ones —
   *  the same quantity the CVD pane accumulates, cut by price instead of by time.
   *
   *  Absent, rather than zero, when the source has no aggressor tag to read: the
   *  estimated profile (bars, no tape), and a tape whose every print came back
   *  untagged. A row of exactly zero is a real reading — the two sides cancelled
   *  at that price — and the two must not be spelled the same. */
  delta?: number;
}

export interface VolumeProfile {
  rows: ProfileRow[];
  /** Largest single-row volume — the width the renderer scales against. */
  maxVolume: number;
  /** Whether the rows carry `delta` at all. What the legend needs to say why a
   *  delta reading isn't there, which is otherwise indistinguishable from a
   *  session that happened to trade perfectly two-sided. */
  hasDelta: boolean;
  /** Largest |delta| on any row — what the delta lane scales against, exactly as
   *  `maxVolume` is what the volume rows scale against. Its own maximum and not
   *  `maxVolume`: net delta is a small fraction of volume, and a lane scaled to
   *  the volume it netted out of would be a row of hairlines. Zero when there is
   *  no delta, or when every row's two sides cancelled exactly. */
  maxAbsDelta: number;
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
 * `entries` is the [price, size, delta] rows of every trade in the selected bars
 * (the `footprint` the sim's chart API ships per bar). Unlike computeVolumeProfile
 * nothing is estimated here — and because rows *are* tick levels, POC/VAH/VAL
 * come back as real tradeable prices rather than arbitrary bucket midpoints.
 *
 * The third element is that level's signed aggressor volume, and is what a
 * chart with no tape of its own reads delta from. It is absent on a payload from
 * a server that predates it, and on a day whose ticks carried no aggressor tag —
 * so its presence is tested for rather than assumed.
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

  // Every row carries delta or none does: the footprint is one shape for the
  // whole payload, so one look at its first row settles it.
  const withDelta = entries[0].length > 2;
  const rows: ProfileRow[] = Array.from({ length: rowCount }, (_, i) => {
    const centre = min + i * step;
    const row: ProfileRow = { low: centre - step / 2, high: centre + step / 2, volume: 0 };
    if (withDelta) row.delta = 0;
    return row;
  });
  for (const e of entries) {
    const row = rows[Math.round((e[0] - min) / step)];
    row.volume += e[1];
    if (withDelta) row.delta! += e[2] ?? 0;
  }

  let total = 0;
  let maxVolume = 0;
  let maxAbsDelta = 0;
  let pocIdx = 0;
  for (let i = 0; i < rows.length; i++) {
    total += rows[i].volume;
    if (rows[i].volume > maxVolume) {
      maxVolume = rows[i].volume;
      pocIdx = i;
    }
    const d = Math.abs(rows[i].delta ?? 0);
    if (d > maxAbsDelta) maxAbsDelta = d;
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
    hasDelta: withDelta,
    maxAbsDelta,
    poc: priceAt(pocIdx),
    vah: priceAt(hi),
    val: priceAt(lo),
    valueArea,
  };
}

/** An inclusive tick-index span of the tape. */
export interface TapeRange {
  i0: number;
  i1: number;
}

/**
 * The exact profile again, but read straight off a slice of the raw tape.
 *
 * Same arithmetic and same output as `computeTickProfile` — the difference is
 * only where the trades come from. The simulator holds the whole session as
 * typed arrays and each bar remembers the tick indices it was built from, so a
 * profile over any bar range is an index range, and binning it needs no
 * intermediate array of [price, size] pairs (a full NQ session is ~1M trades,
 * and materialising that per pan would be the slow part of a pan).
 *
 * `level` is price on the integer tick grid, which is how the tape arrives —
 * binning on it directly costs no division and cannot drift off the grid.
 * `i0`/`i1` are inclusive tick indices.
 *
 * `side` is the tape's aggressor tag (`Tape.side`). Hand it over and the rows
 * come back carrying delta as well as volume; leave it out and they carry
 * volume alone, which is what a caller that only wants the distribution should
 * do — the binning loop then skips the question entirely.
 */
export function computeTapeProfile(
  level: Int32Array,
  size: Int32Array,
  i0: number,
  i1: number,
  tickSize: number,
  binSize = 0,
  side?: Uint8Array,
): VolumeProfile | null {
  return computeTapeProfileRanges(level, size, [{ i0, i1 }], tickSize, binSize, side);
}

/**
 * The same profile over *several* stretches of tape at once.
 *
 * A multi-session composite is this and nothing more: the prior days' RTH
 * stretches are separate index spans on one glued tape (see `concatTapes`), and
 * the composite is their volume-at-price summed. Keeping it one pass over the
 * typed arrays is what makes a five-day composite a few milliseconds rather
 * than five profiles and a merge.
 *
 * Ranges may be given in any order and are not required to be adjacent.
 */
export function computeTapeProfileRanges(
  level: Int32Array,
  size: Int32Array,
  ranges: TapeRange[],
  tickSize: number,
  binSize = 0,
  side?: Uint8Array,
): VolumeProfile | null {
  if (!(tickSize > 0)) return null;
  const spans = ranges.filter((r) => r.i1 >= r.i0 && r.i0 >= 0 && r.i1 < level.length);
  if (spans.length === 0) return null;

  let min = Infinity;
  let max = -Infinity;
  for (const { i0, i1 } of spans) {
    for (let i = i0; i <= i1; i++) {
      const l = level[i];
      if (l < min) min = l;
      if (l > max) max = l;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;

  // Same grouping rule as computeTickProfile: one row per tick level until the
  // rows would be sub-pixel, then fatter rows — plus whatever floor the caller
  // asked for. A tick row is the finest the tape can be read at, but it is not
  // always the most readable: on NQ the interesting shelf is a point wide, and
  // four sub-pixel rows per point is a comb rather than a distribution. The
  // floor never overrides the sub-pixel rule, so a wide window still groups.
  const group = groupFor(max - min + 1, tickSize, binSize);
  // Rows sit on a grid of whole `group`s from price zero, not from the window's
  // own low: a 1-point row then spans a round point whichever bars are on
  // screen, so panning re-bins the same trade into the same row instead of
  // sliding every boundary by a tick.
  min = Math.floor(min / group) * group;
  const rowCount = Math.floor((max - min) / group) + 1;

  const vols = new Float64Array(rowCount);
  const dels = side ? new Float64Array(rowCount) : null;
  let tagged = false;
  for (const { i0, i1 } of spans) {
    for (let i = i0; i <= i1; i++) {
      const r = Math.floor((level[i] - min) / group);
      vols[r] += size[i];
      if (!dels) continue;
      // An untagged print (`side` 0) belongs to neither side, so it adds volume
      // and no delta — the same rule the CVD accumulator applies to it.
      const s = side![i];
      if (s === SIDE_BUY) dels[r] += size[i];
      else if (s === SIDE_SELL) dels[r] -= size[i];
      else continue;
      tagged = true;
    }
  }

  // A span the feed tagged nothing in reports no delta rather than a row of
  // zeros — "nobody was the aggressor" is not a reading.
  return finalizeProfile(vols, tagged ? dels : null, min, group, tickSize);
}

/**
 * Turn a bin array into a profile: rows, POC, value area.
 *
 * Split out because it is the *only* part of a profile that doesn't depend on
 * how the bins were filled — `computeTapeProfileRanges` fills them in one pass
 * and `LiveTapeProfile` grows them a few ticks at a time, and both have to
 * report the same numbers off the same bins.
 *
 * `minLevel` is the level `vols[0]` starts at, on the whole-`group` grid.
 * `dels` is the signed aggressor volume of the same bins, or null where there
 * was no aggressor tag to bin.
 */
function finalizeProfile(
  vols: Float64Array,
  dels: Float64Array | null,
  minLevel: number,
  group: number,
  tickSize: number,
): VolumeProfile | null {
  const rowCount = vols.length;
  const step = tickSize * group;
  const minPrice = minLevel * tickSize;
  // A row spans the `group` levels it bins, so it is drawn from half a tick
  // below its lowest level to half a tick above its highest — which is the
  // one-tick row's ±half-tick box when group is 1.
  const rows: ProfileRow[] = Array.from({ length: rowCount }, (_, i) => {
    const low = minPrice + i * step;
    const row: ProfileRow = {
      low: low - tickSize / 2,
      high: low + step - tickSize / 2,
      volume: vols[i],
    };
    if (dels) row.delta = dels[i];
    return row;
  });

  let total = 0;
  let maxVolume = 0;
  let maxAbsDelta = 0;
  let pocIdx = 0;
  for (let i = 0; i < rowCount; i++) {
    total += vols[i];
    if (vols[i] > maxVolume) {
      maxVolume = vols[i];
      pocIdx = i;
    }
    if (dels) {
      const d = Math.abs(dels[i]);
      if (d > maxAbsDelta) maxAbsDelta = d;
    }
  }
  if (total <= 0) return null;

  const [lo, hi] = valueAreaRange(rows, pocIdx, total);
  const valueArea = new Set<number>();
  for (let i = lo; i <= hi; i++) valueArea.add(i);

  // Levels, never row midpoints: a row's bottom level is the round price that
  // names it (20150.00 for the 20150.00–20150.75 point), and the value area is
  // reported at its outer edges — the top of the highest row, the bottom of the
  // lowest — so a 1-point bin can't quietly shave a point off the area.
  const lowOf = (i: number) => minPrice + i * step;
  return {
    rows,
    maxVolume,
    hasDelta: dels !== null,
    maxAbsDelta,
    poc: lowOf(pocIdx),
    vah: lowOf(hi) + step - tickSize,
    val: lowOf(lo),
    valueArea,
  };
}

/**
 * The same profile as `computeTapeProfile`, kept up to date on a span that grows
 * at its right edge — which is what a replaying session is: `i0` is pinned (the
 * bell, the left edge of the viewport) and `i1` chases the playhead.
 *
 * Recomputing from scratch is a scan of the whole span, so doing it once a frame
 * would mean re-reading half a million ticks 60 times a second to add the twenty
 * that just printed. This holds the bins between calls instead and folds in only
 * the new ticks, so a live profile costs the same as the tape it is reading.
 *
 * It falls back to a full rebuild whenever the span isn't a pure extension of the
 * last one — a pan, a rewind, a new tape, a different bin size — so callers can
 * hand it anything and get the right answer; the fast path is an optimisation,
 * never a constraint on what may be asked for.
 *
 * The returned object is reference-stable while nothing changes: two calls with
 * the same span hand back the same profile, which callers can use to skip work
 * of their own.
 */
export class LiveTapeProfile {
  private level: Int32Array | null = null;
  private size: Int32Array | null = null;
  private side: Uint8Array | null = null;
  private tickSize = 0;
  private binSize = 0;
  private i0 = -1;
  /** Newest tick index folded into `vols`, inclusive. */
  private done = -1;
  private group = 1;
  /** Level of `vols[0]`, on the whole-`group` grid. */
  private base = 0;
  /** Highest level seen, so an extension knows whether it has to regroup. */
  private top = 0;
  private vols: Float64Array | null = null;
  /** Signed aggressor volume of the same bins, kept in step with `vols`. Null
   *  when the caller asked for no delta. */
  private dels: Float64Array | null = null;
  /** Whether any tick folded in so far carried an aggressor tag. Cumulative, so
   *  a quiet stretch of untagged prints can't take a reading away that the
   *  earlier tape already earned. */
  private tagged = false;
  private profile: VolumeProfile | null = null;

  /** `i0`/`i1` are inclusive tick indices, as in `computeTapeProfile`, and
   *  `side` is optional on the same terms. */
  update(
    level: Int32Array,
    size: Int32Array,
    i0: number,
    i1: number,
    tickSize: number,
    binSize = 0,
    side?: Uint8Array,
  ): VolumeProfile | null {
    if (!(tickSize > 0) || i0 < 0 || i1 < i0 || i1 >= level.length) {
      this.reset();
      return null;
    }
    const extends_ =
      this.vols !== null &&
      level === this.level &&
      size === this.size &&
      // A caller that starts (or stops) asking for delta is asking for a
      // different profile, and the bins behind it were never kept — so it is a
      // rebuild, exactly like a new tape or a new bin size.
      (side ?? null) === this.side &&
      tickSize === this.tickSize &&
      binSize === this.binSize &&
      i0 === this.i0 &&
      i1 >= this.done;
    if (!extends_) return this.rebuild(level, size, i0, i1, tickSize, binSize, side);
    if (i1 === this.done) return this.profile;

    // Range first, bins second: the new ticks may widen the profile past the
    // point where rows would go sub-pixel, and that changes every row boundary —
    // so it has to be settled before a single tick is binned.
    let lo = this.base;
    let hi = this.top;
    for (let i = this.done + 1; i <= i1; i++) {
      const l = level[i];
      if (l < lo) lo = l;
      if (l > hi) hi = l;
    }
    if (groupFor(hi - lo + 1, tickSize, binSize) !== this.group)
      return this.rebuild(level, size, i0, i1, tickSize, binSize, side);

    this.extend(lo, hi);
    const vols = this.vols!;
    const dels = this.dels;
    for (let i = this.done + 1; i <= i1; i++) {
      const r = Math.floor((level[i] - this.base) / this.group);
      vols[r] += size[i];
      if (!dels) continue;
      const s = side![i];
      if (s === SIDE_BUY) dels[r] += size[i];
      else if (s === SIDE_SELL) dels[r] -= size[i];
      else continue;
      this.tagged = true;
    }
    this.done = i1;
    this.top = hi;
    this.profile = finalizeProfile(
      vols,
      this.tagged ? dels : null,
      this.base,
      this.group,
      tickSize,
    );
    return this.profile;
  }

  /** Widen the bin arrays to cover `lo`…`hi`, keeping the rows already filled.
   *  Both arrays or neither: they are one binning read two ways, and a delta
   *  array a row short of its volumes would silently mis-attribute the edge. */
  private extend(lo: number, hi: number): void {
    const grow = (a: Float64Array | null, add: number, len: number): Float64Array | null => {
      if (!a) return null;
      const grown = new Float64Array(len);
      grown.set(a, add);
      return grown;
    };
    if (lo < this.base) {
      const base = Math.floor(lo / this.group) * this.group;
      const add = (this.base - base) / this.group;
      const len = this.vols!.length + add;
      this.vols = grow(this.vols, add, len);
      this.dels = grow(this.dels, add, len);
      this.base = base;
    }
    const need = Math.floor((hi - this.base) / this.group) + 1;
    if (need > this.vols!.length) {
      this.vols = grow(this.vols, 0, need);
      this.dels = grow(this.dels, 0, need);
    }
  }

  private rebuild(
    level: Int32Array,
    size: Int32Array,
    i0: number,
    i1: number,
    tickSize: number,
    binSize: number,
    side?: Uint8Array,
  ): VolumeProfile | null {
    let min = Infinity;
    let max = -Infinity;
    for (let i = i0; i <= i1; i++) {
      const l = level[i];
      if (l < min) min = l;
      if (l > max) max = l;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      this.reset();
      return null;
    }
    const group = groupFor(max - min + 1, tickSize, binSize);
    const base = Math.floor(min / group) * group;
    const rowCount = Math.floor((max - base) / group) + 1;
    const vols = new Float64Array(rowCount);
    const dels = side ? new Float64Array(rowCount) : null;
    let tagged = false;
    for (let i = i0; i <= i1; i++) {
      const r = Math.floor((level[i] - base) / group);
      vols[r] += size[i];
      if (!dels) continue;
      const s = side![i];
      if (s === SIDE_BUY) dels[r] += size[i];
      else if (s === SIDE_SELL) dels[r] -= size[i];
      else continue;
      tagged = true;
    }

    this.level = level;
    this.size = size;
    this.side = side ?? null;
    this.tickSize = tickSize;
    this.binSize = binSize;
    this.i0 = i0;
    this.done = i1;
    this.group = group;
    this.base = base;
    this.top = max;
    this.vols = vols;
    this.dels = dels;
    this.tagged = tagged;
    this.profile = finalizeProfile(vols, tagged ? dels : null, base, group, tickSize);
    return this.profile;
  }

  private reset(): void {
    this.level = null;
    this.size = null;
    this.side = null;
    this.i0 = -1;
    this.done = -1;
    this.vols = null;
    this.dels = null;
    this.tagged = false;
    this.profile = null;
  }
}

/** How many tick levels one row bins: what the caller asked for, or fatter when
 *  the rows would otherwise be sub-pixel. The one place the rule lives, so the
 *  incremental path and the full rebuild can't disagree about it. */
function groupFor(levels: number, tickSize: number, binSize: number): number {
  const asked = binSize > 0 ? Math.max(1, Math.round(binSize / tickSize)) : 1;
  return Math.max(asked, Math.ceil(levels / MAX_LEVELS));
}

/** A hump in a profile: a price the auction kept coming back to. `height` and
 *  `prom` are shares of the tallest hump, so both read the same on a one-session
 *  profile and on a five-day composite. */
export interface ProfileNode {
  price: number;
  height: number;
  prom: number;
}

/** A trough *between* two accepted humps — a price the auction passed through.
 *  `depth` is its share of the tallest hump. */
export interface ProfileTrough {
  price: number;
  depth: number;
}

export interface ProfileNodes {
  hvn: ProfileNode[];
  lvn: ProfileTrough[];
}

/** Prominence and smoothing the demo page (demo/composite_profile_demo.py)
 *  reports its summary at, and the range each is worth varying over. Neither is
 *  knowable in advance — which setting reads the distribution you are looking at
 *  is the thing to feel out, so they are settings and not constants. */
export const DEFAULT_NODE_PROM = 0.35;
export const DEFAULT_NODE_SMOOTH = 0.04;

/** Centred rolling mean, partial at the edges — pandas'
 *  `rolling(k, center=True, min_periods=1).mean()`, which is what the demo
 *  smooths with.
 *
 *  Exported because lib/volumeShelf smooths its own curve before thresholding
 *  it, and two rolling means that were meant to be the same one are two things
 *  to keep in step. Note the *partial* window at the edges: the last index
 *  averages over fewer terms, so a curve that is high at the boundary rises
 *  into it. Callers who care pad their input. */
export function smoothed(vol: Float64Array, k: number): Float64Array {
  const n = vol.length;
  const sum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) sum[i + 1] = sum[i] + vol[i];
  const half = (k - 1) >> 1;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.max(0, i - half);
    const b = Math.min(n, i + half + 1);
    out[i] = (sum[b] - sum[a]) / (b - a);
  }
  return out;
}

/**
 * HVN humps and the LVN troughs between them, by prominence — a port of the
 * demo's `nodes()`, so a node here means what it means in the write-up.
 *
 * Prominence is the standard definition: how far a peak stands above the higher
 * of the two saddles separating it from any taller peak. Raw height would count
 * every shoulder on the POC as its own node.
 *
 * `prom` and `smooth` are shares — of the tallest hump, and of the row count.
 * An LVN is only meaningful *between* two accepted humps (so `lvn.length` is at
 * most `hvn.length - 1`): a trough at the edge of the distribution is just where
 * the auction stopped, not a price it rejected.
 */
export function profileNodes(
  p: VolumeProfile,
  prom = DEFAULT_NODE_PROM,
  smooth = DEFAULT_NODE_SMOOTH,
): ProfileNodes {
  const n = p.rows.length;
  if (n < 5) return { hvn: [], lvn: [] };
  const vol = new Float64Array(n);
  for (let i = 0; i < n; i++) vol[i] = p.rows[i].volume;
  const k = Math.max(3, Math.trunc(n * smooth) | 1);
  const sm = smoothed(vol, k);
  let top = 0;
  for (let i = 0; i < n; i++) if (sm[i] > top) top = sm[i];
  if (top <= 0) return { hvn: [], lvn: [] };

  const priceAt = (i: number) => (p.rows[i].low + p.rows[i].high) / 2;
  // `>=` on the left and `>` on the right, as the demo has it: a flat shelf
  // resolves to its right-hand end rather than counting as no peak at all.
  const peaks: { i: number; height: number; prom: number }[] = [];
  for (let i = 1; i < n - 1; i++) {
    if (!(sm[i] >= sm[i - 1] && sm[i] > sm[i + 1])) continue;
    let l = i;
    while (l > 0 && sm[l - 1] <= sm[i]) l--;
    let r = i;
    while (r < n - 1 && sm[r + 1] <= sm[i]) r++;
    let lMin = Infinity;
    for (let j = l; j <= i; j++) if (sm[j] < lMin) lMin = sm[j];
    let rMin = Infinity;
    for (let j = i; j <= r; j++) if (sm[j] < rMin) rMin = sm[j];
    const saddle = Math.max(lMin, rMin);
    peaks.push({ i, height: sm[i] / top, prom: (sm[i] - saddle) / top });
  }

  const kept = peaks.filter((x) => x.prom >= prom);
  const hvn = kept
    .slice()
    .sort((a, b) => b.height - a.height)
    .map((x) => ({ price: priceAt(x.i), height: x.height, prom: x.prom }));

  const lvn: ProfileTrough[] = [];
  const order = kept.slice().sort((a, b) => a.i - b.i);
  for (let q = 0; q + 1 < order.length; q++) {
    const i = order[q].i;
    const j = order[q + 1].i;
    if (j - i < 2) continue;
    let m = i;
    for (let x = i; x < j; x++) if (sm[x] < sm[m]) m = x;
    lvn.push({ price: priceAt(m), depth: sm[m] / top });
  }
  return { hvn, lvn };
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
    // A bar's single volume number says nothing about who was the aggressor, so
    // there is no delta to be had here — not even a spread-out estimate of one.
    hasDelta: false,
    maxAbsDelta: 0,
    poc: (rows[pocIdx].low + rows[pocIdx].high) / 2,
    vah: rows[hi].high,
    val: rows[lo].low,
    valueArea,
  };
}
