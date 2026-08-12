// The day-type readout: is the session paying for trades right now, and which
// way is it leaning?
//
// Three numbers, all causal — nothing here reads a tick past the clock, so the
// readout is safe on a blind replay:
//
//   - TIDE   net drift over the trailing ten minutes, in signed ticks. Which
//            way the tape is actually going, as opposed to how hard it is
//            thrashing on the way (a day can travel 3,000 ticks a minute and go
//            nowhere — see the study below).
//   - SWING  median zigzag leg over the same window: how far a move typically
//            runs before reversing 25+ ticks. The number a trail distance
//            should be read against — a trail inside it exits on routine
//            wiggles.
//   - EXT    median 3-minute favorable excursion of the last few entries. The
//            trader's own trades used as an instrument: when I get in, does it
//            go anywhere? This is the number that actually separated the
//            sittings in docs/research/replay-trail-whatif.md — box, speed and
//            violence were near identical across days that felt nothing alike.
//
// The verdict is EXT bucketed: a day whose entries keep running 100+ ticks is
// paying (widen the trail, lean with the tide); one where they die inside 60
// is dry (clip tight or stand down). The thresholds were eyeballed on three
// sittings — that is why this ships as a READOUT and not a gate: numbers on
// the rail, nothing wired to them. An entry is scored three minutes after it
// happens, never sooner, so a verdict can lag the feel by a trade or two —
// that lag is the price of never reading the future.

import type { Tape } from "./replayEngine";
import type { Trade } from "./replaySim";

/** Trailing tape window TIDE and SWING are read over. */
export const READ_WINDOW_MS = 600_000;
/** How long after entry an excursion is scored — the same 3-minute horizon the
 *  study measured MFE over. */
export const EXT_HORIZON_MS = 180_000;
/** How many scored entries EXT pools; the freshest read the sample allows. */
const EXT_TRADES = 5;
/** Fewer scored entries than this and the verdict withholds itself. */
const EXT_MIN = 3;
/** Reversal that ends a swing leg, in ticks — the study's zigzag threshold. */
const ZIG_TICKS = 25;
/** EXT at or above this: the day is paying for trades. */
export const PAYING_TICKS = 100;
/** EXT below this: it isn't. Between the two the day is grudging. */
export const DRY_TICKS = 60;

export type DayVerdict = "paying" | "grudging" | "dry";

export interface DayRead {
  /** Signed ticks the tape drifted over the window; null until it spans it. */
  tideTicks: number | null;
  /** Median zigzag leg over the window, ticks; null under three legs. */
  swingTicks: number | null;
  /** Median MFE-3min of the scored entries, ticks; null with none scored. */
  extTicks: number | null;
  /** How many entries EXT is standing on (0..EXT_TRADES). */
  scored: number;
  /** Null while there are fewer than EXT_MIN scored entries. */
  verdict: DayVerdict | null;
}

/** First index in ascending `t` at or after `ms`, searching [0, n). */
function lowerBound(t: Float64Array, n: number, ms: number): number {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (t[mid] < ms) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Zigzag leg lengths (ticks) of prices[from..to), threshold ZIG_TICKS. */
function zigzagLegs(price: Float64Array, from: number, to: number, tickSize: number): number[] {
  const thr = ZIG_TICKS * tickSize;
  const legs: number[] = [];
  if (to - from < 2) return legs;
  let anchor = price[from];
  let extreme = anchor;
  let dir = 0;
  for (let i = from + 1; i < to; i++) {
    const x = price[i];
    if (dir === 0) {
      if (Math.abs(x - anchor) >= thr) {
        dir = x > anchor ? 1 : -1;
        extreme = x;
      }
    } else if (dir === 1) {
      if (x > extreme) extreme = x;
      else if (extreme - x >= thr) {
        legs.push((extreme - anchor) / tickSize);
        anchor = extreme;
        extreme = x;
        dir = -1;
      }
    } else {
      if (x < extreme) extreme = x;
      else if (x - extreme >= thr) {
        legs.push((anchor - extreme) / tickSize);
        anchor = extreme;
        extreme = x;
        dir = 1;
      }
    }
  }
  return legs;
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * Read the day as of `clockMs`.
 *
 * `tape` may be a glued tape (context days in front) — everything here is
 * window-bounded lookback from the clock, so the glue never matters. `trades`
 * are the sitting's closed trades in entry order, exactly as the blotter holds
 * them; open positions aren't scored (their excursion isn't finished being
 * written, and reading it early would make the verdict twitch with the mark).
 */
export function dayRead(
  tape: Tape,
  trades: readonly Trade[],
  clockMs: number,
  tickSize: number,
): DayRead {
  const hi = lowerBound(tape.t, tape.n, clockMs + 1);
  const lo = lowerBound(tape.t, tape.n, clockMs - READ_WINDOW_MS);

  let tideTicks: number | null = null;
  let swingTicks: number | null = null;
  if (hi - lo >= 2) {
    tideTicks = (tape.price[hi - 1] - tape.price[lo]) / tickSize;
    const legs = zigzagLegs(tape.price, lo, hi, tickSize);
    if (legs.length >= 3) swingTicks = median(legs);
  }

  // Newest first, so EXT pools the *last* scored entries; stop once it has
  // enough or the entries are too fresh to have finished their horizon.
  const mfes: number[] = [];
  for (let k = trades.length - 1; k >= 0 && mfes.length < EXT_TRADES; k--) {
    const tr = trades[k];
    if (tr.entryMs + EXT_HORIZON_MS > clockMs) continue;
    const a = lowerBound(tape.t, tape.n, tr.entryMs);
    const b = lowerBound(tape.t, tape.n, tr.entryMs + EXT_HORIZON_MS);
    if (b - a < 1) continue;
    const sgn = tr.side === "long" ? 1 : -1;
    let best = -Infinity;
    for (let i = a; i < b; i++) {
      const fav = sgn * (tape.price[i] - tr.entryPrice);
      if (fav > best) best = fav;
    }
    mfes.push(Math.max(0, best) / tickSize);
  }

  const extTicks = mfes.length ? median(mfes) : null;
  const verdict: DayVerdict | null =
    extTicks == null || mfes.length < EXT_MIN
      ? null
      : extTicks >= PAYING_TICKS
        ? "paying"
        : extTicks < DRY_TICKS
          ? "dry"
          : "grudging";

  return { tideTicks, swingTicks, extTicks, scored: mfes.length, verdict };
}

/** What the verdict asks of you — the strip's one line of advice. */
export const VERDICT_LINE: Record<DayVerdict, string> = {
  paying: "paying — trail wide, lean with the tide",
  grudging: "grudging — one rung then stalls",
  dry: "not paying — clip tight or stand down",
};
