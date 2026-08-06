// Where the clock comes from, and what you're allowed to do to it.
//
// Replay and Live are one chart surface. The engine folds ticks forward, the
// chart applies the step, the sim folds the same tick range into the blotter —
// none of that cares whether the tape was decoded from a finished session or is
// still arriving. Exactly three things differ, and they all live here:
//
//   1. the clock         — a playback rate you can scale, vs. the last print
//   2. whether it ends   — a replay stops at session_end_ms; a live day doesn't
//   3. what you may do   — seek, rewind, change speed, step one bar
//
// Cutting the seam here rather than inside ReplayEngine is deliberate. The
// engine is already append-tolerant: `advance()` re-reads `tape.n` on every
// iteration and holds a live reference to the tape object, so a growing tape
// needs no engine change at all. What actually forks is the caller's frame loop.
//
// The capability flags are not cosmetic — they are what stops the live surface
// from reaching a code path that cannot be correct there. `truncateLog` un-does
// actions you took after a point in time, which is coherent only when the tape
// can be re-taken; a live log is append-only and a rewind would be a lie about
// what happened. Gate on `canRewind`, not on `mode`, so the reason travels with
// the flag.

export type TapeMode = "replay" | "live";

/** What the frame loop needs to know to advance one step. */
export interface ClockStep {
  /** The clock to advance the engine to. */
  clock: number;
  /** True when the tape has no more to give and the loop should settle. */
  atEnd: boolean;
}

export interface TapeSource {
  readonly mode: TapeMode;

  /**
   * The clock for this frame.
   *
   * `prev` is the current clock, `dtRealMs` the wall-clock milliseconds since
   * the previous frame, `speed` the playback multiplier (ignored when
   * `canSetSpeed` is false).
   */
  clockFor(prev: number, dtRealMs: number, speed: number): ClockStep;

  /** Stop the rAF loop when `atEnd` goes true. False for a live tape, which is
   *  only ever caught up, never finished. */
  readonly stopAtEnd: boolean;

  /** Jump the clock to an arbitrary point (scrubber, start-time picker). */
  readonly canSeek: boolean;
  /** Move the clock BACKWARD, which un-does actions taken after it. */
  readonly canRewind: boolean;
  /** Run faster or slower than real time. */
  readonly canSetSpeed: boolean;
  /** Advance exactly one bar. Needs to know when the next bar completes, which
   *  on a tick timeframe means reading ticks that haven't printed yet. */
  readonly canStepBar: boolean;
}

/**
 * A finished session, played back on a scalable clock.
 *
 * `endMs` is the session's last instant. The clock is clamped to it and the
 * loop is told to stop — which is also what ends the attempt, so the clamp has
 * to be exact rather than approximate.
 */
export function replaySource(endMs: number): TapeSource {
  return {
    mode: "replay",
    clockFor(prev, dtRealMs, speed) {
      const clock = prev + dtRealMs * speed;
      return clock >= endMs ? { clock: endMs, atEnd: true } : { clock, atEnd: false };
    },
    stopAtEnd: true,
    canSeek: true,
    canRewind: true,
    canSetSpeed: true,
    canStepBar: true,
  };
}

/**
 * A session in progress, clocked by its own most recent print.
 *
 * Deliberately NOT the wall clock. On a quiet market a wall clock runs the chart
 * past the data and hands `advance()` a clock it has nothing to fill with, so
 * the playhead claims a currency the tape doesn't have. Anchoring on the last
 * received tick means the chart is exactly as current as the data behind it —
 * and when the feed stalls, it visibly stops, which is the honest failure.
 *
 * `lastTickMs` reads the tape's newest timestamp; it returns null before the
 * first tick arrives, where there is no clock to have.
 */
export function liveSource(lastTickMs: () => number | null): TapeSource {
  return {
    mode: "live",
    clockFor(prev) {
      const t = lastTickMs();
      // Never go backwards: a re-bootstrap after a dropped connection can hand
      // back a shorter tape for a moment, and rewinding the clock would replay
      // fills that already happened.
      return { clock: t == null ? prev : Math.max(prev, t), atEnd: false };
    },
    stopAtEnd: false,
    canSeek: false,
    canRewind: false,
    canSetSpeed: false,
    canStepBar: false,
  };
}
