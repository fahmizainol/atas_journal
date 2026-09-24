// "Open the Simulator to review this sitting, not to trade it."
//
// A hand-off between two pages, and deliberately the smallest one that works:
// the id of the attempt being reviewed, and nothing else. Everything else the
// review needs — the tape, the log, the trades, the flags — is already fetchable
// from that id, and a second copy of any of it here would be a second answer.
//
// It rides alongside `lib/replayResume` rather than inside it because the two
// say different things. A resume point says *where you were*; this says *what
// you are here to do*. A sitting can be resumed without being reviewed (the
// ordinary case) and reviewed without being resumed — the review is read-only,
// and picking up trading where the reviewed sitting left off is exactly what it
// must not do.
//
// Consumed once, by the Simulator, on the build that loads the tape. Leaving it
// in the store would make every later visit to the page a review.

export interface ReviewMark {
  attemptId: string;
}

/** Scoped per mode for the same reason the resume bookmark is (see
 *  lib/replayResume): a drill review owed must open on the backtest page and a
 *  sitting's on the replay page, and a shared marker would send whichever page
 *  loaded first into the other world's review. */
export type ReviewScope = string;

/** Keyed by **account id** rather than by mode, and `funded` keeps the legacy
 *  `replay` spelling so no existing marker is orphaned. See
 *  `lib/replayResume.ResumeScope` for why the mode stopped being enough. */
const keyOf = (scope: ReviewScope) =>
  `sim.review.${scope === "funded" ? "replay" : scope}`;
/** Pre-split shared key, read as a replay fallback and removed on clear. */
const LEGACY_KEY = "sim.review";

export function loadReview(mode: ReviewScope): ReviewMark | null {
  try {
    const raw =
      localStorage.getItem(keyOf(mode)) ??
      (mode === "replay" ? localStorage.getItem(LEGACY_KEY) : null);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Record<keyof ReviewMark, unknown>>;
    return typeof s.attemptId === "string" && s.attemptId ? { attemptId: s.attemptId } : null;
  } catch {
    return null;
  }
}

export function saveReview(mode: ReviewScope, m: ReviewMark): void {
  try {
    localStorage.setItem(keyOf(mode), JSON.stringify(m));
  } catch {
    // Private mode / quota. The review page would open as an ordinary replay,
    // which is why the Simulator also checks that what it loaded is reviewable
    // rather than trusting this alone.
  }
}

export function clearReview(mode: ReviewScope): void {
  try {
    localStorage.removeItem(keyOf(mode));
    if (mode === "replay") localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* nothing to clear if the store isn't there */
  }
}
