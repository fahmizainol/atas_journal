// The multi-session composite profile: several prior sessions' volume-at-price
// summed into one distribution, and the rule that decides which of them belong
// together.
//
// Two rules, and they are not equivalent. A *fixed* window (every context day
// loaded) is the zero-thought one and the one the demo measured as the worse
// one on NQ: day-to-day value areas overlap at all only 52% of the time, balance
// runs are median 2 days (p75 3, p90 4, max 9), and the value area a fixed
// window reports widens 226 → 466 → 1,154 → 1,753pt across balance / 3-day /
// 10-day / 20-day. A 20-day composite on NQ is describing about eight auctions
// at once, so its "value-area edge" is the whole excursion.
//
// The *balance* rule accumulates only while the auction has not broken: walk
// back from yesterday, keep taking sessions while the next one's value area
// still touches the composite's, stop on a clean break, cap at five. That is
// measured to be one auction rather than several.
//
// Walking backwards from yesterday, rather than forward through history the way
// the demo groups its whole corpus, is the difference between "which run is
// today part of" and "how do 599 sessions partition" — the first is the question
// a chart is asking, and it needs no days beyond the ones already loaded.
//
// What a *day* spans is a setting, not a constant. The study measured its run
// lengths and value-area widths on RTH profiles, so that is the span its numbers
// describe — but the overnight is where a good part of the auction happens, and
// a level built without it is a level the night can have already traded through.
// Both spans are offered (see `CompositeSpan`), and the caller decides which
// stretches of tape to hand over; this file only sums what it is given.
//
// The one thing worth carrying across: including Globex widens every session's
// value area and therefore lengthens balance runs, since the break test asks
// whether two value areas touch and wider areas touch more often. Measured on
// NQU6's last week of June 2026, per-session value areas went 205→300, 243→439,
// 411→362, 240→296pt, and the balance run over those five days went from 1 day
// to the cap of 5. So under the wider span the cap is doing most of the work and
// the rule discriminates less — if what you want is the rule's own reading of
// where the auction broke, that is the span to switch back to.

import {
  computeTapeProfileRanges,
  type TapeRange,
  type VolumeProfile,
} from "./volumeProfile";

/** Runaway guard on the balance rule — the p90 of measured runs, so it binds
 *  rarely and only where the rule has stopped saying anything. */
export const BALANCE_CAP = 5;

/** Rows to aim the composite's histogram at. The demo's, and for the same
 *  reason: the full tick grid over a multi-day NQ composite is thousands of
 *  levels, more than a chart panel can show and more than the node reader needs.
 *  It is a floor on the bin, never a cap on the range. */
const TARGET_ROWS = 320;

export type CompositeRule = "off" | "days" | "balance";

/** Which stretch of each context day is composited.
 *
 *  `rth`    09:30–16:00 only — the span the demo's run-length and value-area
 *           numbers were measured on.
 *  `globex` the overnight session in front of it too (18:00 the evening before
 *           through the 16:00 close), so a level is built from everything the
 *           day traded before the bell rather than only from the day session. */
export type CompositeSpan = "rth" | "globex";

export interface Composite {
  profile: VolumeProfile;
  /** How many prior sessions went in — under the balance rule this is what the
   *  auction turned out to be, not what was asked for. */
  days: number;
}

/** Widest span, in tick levels, across a set of tape ranges. */
function levelSpan(level: Int32Array, ranges: TapeRange[]): number {
  let min = Infinity;
  let max = -Infinity;
  for (const { i0, i1 } of ranges) {
    for (let i = i0; i <= i1; i++) {
      const l = level[i];
      if (l < min) min = l;
      if (l > max) max = l;
    }
  }
  return Number.isFinite(min) ? max - min + 1 : 0;
}

/**
 * Build the composite over `days` (the prior sessions' spans on the glued tape,
 * oldest first — RTH or Globex+RTH, as the caller cut them).
 *
 * Returns null when there is nothing to build from — no context days, or a rule
 * of "off". Never includes the session being replayed: the caller passes prior
 * days only, which is what keeps the levels frozen at the prior close. Letting
 * today feed the composite is circular — the POC drifts toward price, the level
 * can never be violated, and every touch looks like a hold.
 */
export function buildComposite(
  level: Int32Array,
  size: Int32Array,
  days: TapeRange[],
  tickSize: number,
  rule: CompositeRule,
): Composite | null {
  if (rule === "off" || days.length === 0 || !(tickSize > 0)) return null;

  // One bin for every profile computed here, chosen from the widest range any
  // of them can cover: the composite and the day it is being compared against
  // have to be binned the same way, or the break test compares two grids.
  const binFor = (r: TapeRange[]) => {
    const span = levelSpan(level, r);
    return span <= 0 ? 0 : Math.max(tickSize, Math.ceil(span / TARGET_ROWS) * tickSize);
  };
  const over = (r: TapeRange[], bin: number) =>
    computeTapeProfileRanges(level, size, r, tickSize, bin);

  if (rule === "days") {
    const profile = over(days, binFor(days));
    return profile ? { profile, days: days.length } : null;
  }

  // One bin for the whole walk: the composite and the day being tested against
  // it have to be binned the same way, or the break test is comparing two grids.
  const bin = binFor(days);
  if (!bin) return null;
  const n = days.length;
  let profile = over(days.slice(n - 1), bin);
  if (!profile) return null;
  let take = 1;
  while (take < Math.min(BALANCE_CAP, n)) {
    const cand = over([days[n - 1 - take]], bin);
    // A clean break: the older session's value sits entirely clear of the value
    // the composite has built so far. Anything touching is still one auction.
    if (!cand || cand.val > profile.vah || cand.vah < profile.val) break;
    const next = over(days.slice(n - 1 - take), bin);
    if (!next) break;
    profile = next;
    take++;
  }
  // Re-read what was accepted at its own resolution. The walk's shared bin is
  // sized off every candidate, including the ones that turned out to be a
  // different auction — and a run of two quiet days should not be drawn through
  // a bin widened by the day that broke away from them.
  if (take < n) {
    const kept = days.slice(n - take);
    const own = binFor(kept);
    if (own && own < bin) profile = over(kept, own) ?? profile;
  }
  return { profile, days: take };
}
