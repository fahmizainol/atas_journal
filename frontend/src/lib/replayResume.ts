// Where the Simulator was when the tab last went away.
//
// The sibling of lib/simPrefs, and deliberately a separate file, because it is a
// different kind of thing. Prefs are *settings* — the ticket, the speed, the bar
// size — saved because you would otherwise re-enter them. This is a *position*:
// which session was open, how far into it the clock had run, and which stored
// attempt holds what you did there. simPrefs used to say the open session was
// deliberately not remembered; it is now, here, and the 🎲 draw is what you
// press when you want the old behaviour.
//
// Only the bookmark is local. The trading itself is already on the server —
// useReplayAttempt writes the order log to /replays every couple of seconds —
// so the whole job of this file is to name the attempt that log belongs to, plus
// the one number the server has no way to know: how many context ticks were
// glued in front of the session when the log's cursors were written.
// `OrderRec.idx` counts from the start of the *concatenated* tape, and the
// context days are a client-side reading choice that the next visit may have set
// differently. Stored rather than assumed, because getting it wrong would
// restore every resting order a few million prints out of place — and silently,
// since a shifted cursor is still a valid one.
//
// What is deliberately *not* here: the trades, the position, the working orders.
// All three are derived from the log, and a second copy of them in localStorage
// is a second answer that can disagree with the first.

export interface ResumePoint {
  symbol: string;
  date: string;
  /** Replay clock as epoch-ms in the display zone — the same projection the
   *  tape is shipped in, not UTC (see journal.replays). */
  clockMs: number;
  /** The attempt holding the order log, or null when the sitting was watched
   *  and never traded: no attempt exists until the first fill, and there is then
   *  nothing to put back but the day and the clock. */
  attemptId: string | null;
  /** Context ticks glued in front of the session when the log was written — the
   *  re-base for every `OrderRec.idx` in it. */
  contextTicks: number;
}

const KEY = "sim.resume";

const str = (v: unknown): v is string => typeof v === "string" && v.length > 0;

export function loadResume(): ResumePoint | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Record<keyof ResumePoint, unknown>>;
    // A bookmark is all-or-nothing: half of one would put you on the right day
    // at the wrong clock, which is worse than the random draw it displaced.
    if (!str(s.symbol) || !str(s.date)) return null;
    if (typeof s.clockMs !== "number" || !Number.isFinite(s.clockMs)) return null;
    const ctx = s.contextTicks;
    return {
      symbol: s.symbol,
      date: s.date,
      clockMs: s.clockMs,
      attemptId: str(s.attemptId) ? s.attemptId : null,
      contextTicks: typeof ctx === "number" && Number.isFinite(ctx) && ctx >= 0 ? Math.floor(ctx) : 0,
    };
  } catch {
    return null;
  }
}

export function saveResume(p: ResumePoint): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...p, clockMs: Math.round(p.clockMs) }));
  } catch {
    // Private mode / quota. The replay still runs; it just won't be waiting for
    // you next time.
  }
}

/** Forget the bookmark. Pressing 🎲 or picking a day by hand is a decision to
 *  start somewhere else, and leaving the old point behind would resurrect it on
 *  the next reload. */
export function clearResume(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear if the store isn't there */
  }
}
