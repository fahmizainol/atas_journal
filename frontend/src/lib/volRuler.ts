// The vol ruler: the chart's bar-range volatility, read three ways at once —
// an ATR(14) line (the indicator as every platform draws it), the session's
// developing median bar range (the same information as one number that cannot
// be yanked by the last hour), and yesterday's settled median as the flat
// causal reference. All in ticks, drawn in their own pane under the candles,
// with the 50-tick stop as a fixed rule to read them against.
//
// Why the median exists next to the ATR: the ATR remembers ~14 bars, so
// mid-session it reports the last hour, not the day — it spikes on the open,
// sinks in the lunch lull, and hands you a range instead of a number. The
// expanding median since 10:00 ET converges on the day's character an hour or
// two in and one wild stretch cannot drag it. See docs/research/atr-vs-r5.html
// for the study this pane is the live version of.
//
// Everything is computed from the drawn bars at whatever resolution the chart
// is showing — a 1-minute bar is about half a 5-minute bar, so the numbers
// shrink when the timeframe does. That is a property of the question ("how big
// are the bars I am looking at"), not a bug in the answer.

/** All this reads of a bar: its clock and its range. Stated structurally rather
 *  than as the replay engine's `Bar` so the journal's charts — whose bars come
 *  off a payload and carry no tick indices — can be measured by the same ruler.
 *  It is a question about candles, not about a tape. */
type RangeBar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

/** The stop the ruler is read against, in ticks — the manual bracket this
 *  whole pane exists to sanity-check before the open. */
export const VR_STOP_TICKS = 50;

const ATR_PERIOD = 14;
/** 10:00–16:00 ET, as seconds-of-day on the ET-shifted bar clock. The first
 *  half hour runs 2–4× the rest of the day everywhere and would drag the
 *  median of a short session; the settled window is what the demo's ladder was
 *  calibrated on. */
const WIN_START = 10 * 3600;
const WIN_END = 16 * 3600;
/** An expanding median over fewer bars than this says nothing yet. */
const MIN_BARS = 3;

export interface VolRulerPoint {
  time: number;
  value: number;
}

export interface VolRulerData {
  /** Wilder ATR(ATR_PERIOD) over the drawn bars, in ticks — session bars only,
   *  but warmed through the context days so it has a value from the first bar. */
  atr: VolRulerPoint[];
  /** The expanding median of session bar ranges since 10:00 ET, in ticks. */
  dev: VolRulerPoint[];
  /** The prior session's settled (10:00–16:00) median bar range in ticks, from
   *  the context days when any are loaded — null without them. */
  yday: number | null;
}

/** The ruler as it stands right now — the last closed bar's value on each of
 *  the three lines, for readers that want the number rather than the pane. The
 *  ticket's risk sizer is one: it sets the stop from `atr` and has no use for
 *  the series. Each is null where that line has nothing to say yet — `dev`
 *  before 10:00 ET, `yday` without context days loaded. */
export interface VolRulerRead {
  atr: number | null;
  dev: number | null;
  yday: number | null;
  /** The order presets' reading, in ticks, **at every bucketing at once** — the
   *  same "how big is a bar" question asked at the presets' own window, which is
   *  not the drawn one (see `PresetRuler`). Rides along on this reading rather
   *  than arriving on a second callback because it is one measurement of one
   *  tape, taken at the same moment, and two channels for it would be two things
   *  a page could be holding different vintages of. */
  preset: PresetRulerRead;
}

/** The bucketings a preset's stop can be read at, in the order they are offered.
 *
 *  Three answers to "how much tape is one bar", and they are not the same
 *  question asked three ways — a 500-print bar is a **volume clock** (a fast
 *  tape makes more bars, not bigger ones, so it flattens the open by
 *  construction), and the two clock bars are **wall clock** (the open's 30
 *  seconds hold several times the afternoon's prints, so they report it). At NQ
 *  volumes 500 prints is roughly 25 seconds, which is why it sits next to the
 *  30s: the pair is the closest thing to a controlled comparison of the two
 *  clocks, and 15s is the same clock read finer. */
export const PRESET_BUCKETS = [
  {
    id: "t500",
    label: "500t",
    says: "500 prints a bar — a volume clock, so the open is flattened",
    prints: 500,
    seconds: null,
  },
  {
    id: "s30",
    label: "30s",
    says: "30 seconds a bar — a wall clock, so the open reads wide",
    prints: null,
    seconds: 30,
  },
  {
    id: "s15",
    label: "15s",
    says: "15 seconds a bar — the same clock, read finer and tighter",
    prints: null,
    seconds: 15,
  },
] as const satisfies readonly {
  id: string;
  label: string;
  says: string;
  prints: number | null;
  seconds: number | null;
}[];

export type PresetBucket = (typeof PRESET_BUCKETS)[number]["id"];

export const PRESET_BUCKET_IDS: readonly PresetBucket[] = PRESET_BUCKETS.map((b) => b.id);

/** The bucketing a page starts on. 30 seconds: the resolution a stop is actually
 *  chosen at, and the one whose reading moves with the open the way the market
 *  does. */
export const DEFAULT_PRESET_BUCKET: PresetBucket = "s30";

/** Every bucketing's reading in ticks, null where it has not closed `MIN_BARS`
 *  bars inside the window yet.
 *
 *  All three, always, rather than the one a page happens to be showing. They
 *  cost one shared pass over the prints (see `PresetRuler`), and measuring only
 *  the selected one would mean a rewind and a full re-walk of the session on
 *  every toggle — which is exactly the work this class was written to avoid, and
 *  it would make the comparison the toggle exists for a laggy one. */
export type PresetRulerRead = Record<PresetBucket, number | null>;

export const emptyPresetRead = (): PresetRulerRead =>
  Object.fromEntries(PRESET_BUCKET_IDS.map((id) => [id, null])) as PresetRulerRead;

/** Whether any bucketing has spoken. */
export const hasPresetRead = (r: PresetRulerRead): boolean =>
  PRESET_BUCKET_IDS.some((id) => r[id] != null);

/** Value equality, for the change guard that decides whether to tell the page.
 *  Object identity is useless here — the ruler builds a fresh record per read,
 *  which on every bar close would be a re-render nobody asked for. */
export const samePresetRead = (a: PresetRulerRead, b: PresetRulerRead): boolean =>
  PRESET_BUCKET_IDS.every((id) => a[id] === b[id]);

/** The window the presets' median develops in: the bell to the close.
 *
 *  The opening bars are *in*, which is the whole difference from the settled
 *  window above. That window starts at 10:00 to keep the open from dragging a
 *  median it is not representative of — the right call when the question is
 *  "what is this day's character", and the wrong one here, because a bracket
 *  gets set in the first half hour and a ruler that has not measured the tape
 *  it is being set against has nothing to say about it.
 *
 *  What that costs: the open is the widest tape of the day (1.12x the settled
 *  median even volume-clocked, docs/research/preset-stop-fallback.md), so the
 *  first reading is the widest one and it narrows through the morning as the
 *  median fills in. That is the measurement being honest, not drift — but a
 *  bracket set at 09:32 is a wider bracket than the same preset at 11:00, and
 *  the panel quotes the number so the difference is never silent. */
const PRESET_WIN_START = 9 * 3600 + 30 * 60;

/** The prints, as this reads them: a clock and a price per index. Structural
 *  for the same reason `RangeBar` is — and it means a `Tape` and a
 *  `GrowableTape` are both readable without this module knowing either. */
export interface PrintTape {
  t: Float64Array;
  price: Float64Array;
}

/** Seconds-of-day for a bar time (ET wall clock carried on the UTC epoch). */
const tod = (t: number) => ((t % 86400) + 86400) % 86400;

/** Median of a sorted array. */
const medianOf = (sorted: number[]): number => {
  const n = sorted.length;
  const mid = n >> 1;
  return n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/** Insert `v` into `sorted` in place, keeping order — O(n), which over a
 *  session of appends is the same total work as one sort. */
const insertSorted = (sorted: number[], v: number) => {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  sorted.splice(lo, 0, v);
};

/** Recompute the whole ruler from the drawn bars. Called on snapshot and on
 *  bar close, never per tick — the last (still-forming) bar is excluded so
 *  every value is a fact about a closed bar. */
export function computeVolRuler(
  bars: readonly RangeBar[],
  histCount: number,
  tickSize: number,
): VolRulerData {
  const closed = bars.length > 0 ? bars.length - 1 : 0;
  const atr: VolRulerPoint[] = [];
  const dev: VolRulerPoint[] = [];
  if (closed === 0 || tickSize <= 0) return { atr, dev, yday: null };

  // --- ATR, Wilder's smoothing, seeded with the simple mean of the first
  // period's true ranges. Computed across the context days too so the line is
  // already warm at the session's first bar; only session points are emitted.
  let atrVal = 0;
  let seedSum = 0;
  for (let i = 0; i < closed; i++) {
    const b = bars[i];
    const pc = i > 0 ? bars[i - 1].close : b.open;
    const tr =
      Math.max(b.high, pc) - Math.min(b.low, pc);
    if (i < ATR_PERIOD) {
      seedSum += tr;
      atrVal = seedSum / (i + 1);
    } else {
      atrVal += (tr - atrVal) / ATR_PERIOD;
    }
    if (i >= histCount && i + 1 >= ATR_PERIOD)
      atr.push({ time: b.time, value: atrVal / tickSize });
  }

  // --- The developing median: session bars inside the settled window, each
  // point the median of every qualifying range up to and including its own bar.
  const sorted: number[] = [];
  for (let i = histCount; i < closed; i++) {
    const b = bars[i];
    const s = tod(b.time);
    if (s < WIN_START || s >= WIN_END) continue;
    insertSorted(sorted, (b.high - b.low) / tickSize);
    if (sorted.length >= MIN_BARS) dev.push({ time: b.time, value: medianOf(sorted) });
  }

  // --- Yesterday: the last calendar day among the context bars with a settled
  // window to read. Context days are whole sessions, so when any are loaded the
  // most recent one has one; a handful of bars is not a day.
  let yday: number | null = null;
  if (histCount > 0) {
    const byDay = new Map<number, number[]>();
    for (let i = 0; i < histCount; i++) {
      const b = bars[i];
      const s = tod(b.time);
      if (s < WIN_START || s >= WIN_END) continue;
      const day = Math.floor(b.time / 86400);
      let arr = byDay.get(day);
      if (!arr) byDay.set(day, (arr = []));
      arr.push((b.high - b.low) / tickSize);
    }
    const days = [...byDay.keys()].sort((a, b) => a - b);
    for (let d = days.length - 1; d >= 0; d--) {
      const arr = byDay.get(days[d])!;
      if (arr.length >= MIN_BARS * 2) {
        arr.sort((a, b) => a - b);
        yday = medianOf(arr);
        break;
      }
    }
  }

  return { atr, dev, yday };
}

// --- the ruler the presets read ----------------------------------------------
//
// Everything above measures the bars the chart happens to be drawing, which is
// the right answer to "how big are the bars I am looking at" and the wrong one
// to "how far away does my stop go". The same market reads ~12t on a 30s chart
// and ~90t on an hourly one, so a bracket rule hung off the drawn bars would be
// eight rules wearing one name — and it would change under you when you glanced
// at another pane. The order presets (lib/orderPresets) therefore read their own
// ruler at a bucketing of its own off the prints, and a preset means the same
// distance on every timeframe and every /charts tab. *Which* bucketing is a
// setting (`PRESET_BUCKETS`), and the ruler reports all of them so the choice
// costs nothing to change.
//
// It answers with one number per bucketing and has no fallback behind it: the developing
// median of 30-second bar ranges since `PRESET_WIN_START`, or nothing. Before
// the bell there is no reading, and the panels say so rather than quoting a
// distance from somewhere else. The two legs that used to sit behind it — the
// pre-bell median off the same tape, and the last settled session's — are gone
// deliberately. Both were fitted for a 500-print bar, where an overnight bar
// spans *more* clock time than a session one and therefore reads wider; at a
// fixed 30 seconds the relationship inverts (a thin night bar holds a fraction
// of the prints), so the measured 1.06 correction pointed the wrong way and
// yesterday's window was no longer the window being asked about. A stop nobody
// measured is worse than no preset, which is why nothing replaced them.

/**
 * One bucketing's running median: the prints folded into bars, the closed bars'
 * ranges kept sorted.
 *
 * The two kinds of bar differ only in what makes a bucket — a count of prints
 * since the session opened, or a slice of the wall clock — so they are one
 * class with one branch rather than two that would drift apart at the window
 * edge. The bar's clock is its **first print's** second either way, which for a
 * clock bar is the same answer as the bucket's own start (the window's edges are
 * multiples of every bar size offered) and for a print bar is the only answer
 * there is.
 */
class BucketMedian {
  /** The bucket being accumulated, or -1 for none. */
  private key = -1;
  /** Epoch second of that bucket's first print — what the window is tested on. */
  private startSec = 0;
  private hi = 0;
  private lo = 0;
  private sorted: number[] = [];

  constructor(
    private readonly prints: number | null,
    private readonly seconds: number | null,
  ) {}

  reset(): void {
    this.key = -1;
    this.sorted = [];
  }

  push(i: number, sec: number, price: number, tickSize: number, sessionStart: number): void {
    const key =
      this.prints != null
        ? Math.floor((i - sessionStart) / this.prints)
        : Math.floor(sec / this.seconds!);
    if (key !== this.key) {
      this.close(tickSize);
      this.key = key;
      this.startSec = sec;
      this.hi = price;
      this.lo = price;
    } else if (price > this.hi) this.hi = price;
    else if (price < this.lo) this.lo = price;
  }

  /** Retire the open bucket into the median, if it opened inside the window. A
   *  bar is in or out whole — the alternative is one bar straddling the bell and
   *  counting for both sides of it. */
  private close(tickSize: number): void {
    if (this.key < 0) return;
    const s = tod(this.startSec);
    if (s >= PRESET_WIN_START && s < WIN_END)
      insertSorted(this.sorted, (this.hi - this.lo) / tickSize);
  }

  read(): number | null {
    return this.sorted.length >= MIN_BARS ? medianOf(this.sorted) : null;
  }
}

/**
 * The presets' ruler, kept up to date print by print, at every bucketing at
 * once.
 *
 * Stateful on purpose. A recompute from the session's start costs a pass over
 * every print in it, and the chart asks for a reading on every bar close — so
 * each print is folded into its bars exactly once, the closed ranges are kept
 * sorted, and a session costs one pass over its prints however often it is
 * read. Only two things re-measure: a new tape, and a seek backwards, where the
 * bars after the playhead have not happened yet and a ruler that remembered
 * them would be reading the future.
 *
 * **Why all three and not the selected one.** The bucketing is a setting, and
 * measuring only what is selected would mean a rewind and a full re-walk of the
 * session on every toggle — the one expensive thing this class exists to avoid,
 * paid at exactly the moment a comparison is being made. Three accumulators
 * share the single pass instead: three compares per print rather than one, which
 * against the cost of the pass itself is noise, and the toggle is then free and
 * instant.
 *
 * The bar in hand is deliberately *not* in any median. A bucket enters when a
 * print belonging to a later one arrives, so every range the medians are taken
 * over is a closed bar — the same promise `computeVolRuler` makes by dropping
 * the last bar, kept here without a bar count to drop from.
 */
export class PresetRuler {
  private tape: PrintTape | null = null;
  private sessionStart = -1;
  private tickSize = 0;
  /** First print not yet folded into its bars. */
  private cursor = 0;
  private readonly accs = PRESET_BUCKETS.map((b) => new BucketMedian(b.prints, b.seconds));

  /**
   * Every bucketing's reading as of `playhead` — the last print applied,
   * inclusive — in ticks, each null while fewer than `MIN_BARS` of its bars have
   * closed in the window.
   *
   * `sessionStart` is a print index into the same tape. Only the session is
   * measured: the context days drawn to its left are a different day's open and
   * were never what a bracket set today is hung off. It is also what a print
   * bucket counts from, which is the one place the two kinds of bar disagree
   * about where a bar can begin — a count of prints has to be anchored at the
   * seam or it leaves a runt bar straddling it.
   */
  read(
    tape: PrintTape | null,
    sessionStart: number,
    playhead: number,
    tickSize: number,
  ): PresetRulerRead {
    if (!tape || sessionStart < 0 || playhead < sessionStart || !(tickSize > 0))
      return emptyPresetRead();
    if (tape !== this.tape || sessionStart !== this.sessionStart || tickSize !== this.tickSize) {
      this.tape = tape;
      this.sessionStart = sessionStart;
      this.tickSize = tickSize;
      this.rewind();
    } else if (playhead + 1 < this.cursor) {
      this.rewind();
    }
    for (; this.cursor <= playhead; this.cursor++) {
      const sec = Math.floor(tape.t[this.cursor] / 1000);
      const price = tape.price[this.cursor];
      for (const a of this.accs) a.push(this.cursor, sec, price, tickSize, this.sessionStart);
    }
    const out = emptyPresetRead();
    PRESET_BUCKETS.forEach((b, i) => {
      out[b.id] = this.accs[i].read();
    });
    return out;
  }

  private rewind(): void {
    this.cursor = this.sessionStart;
    for (const a of this.accs) a.reset();
  }
}
