// Arming a level to trade itself.
//
// An *arm* is a standing instruction attached to one price: when the tape gets
// there, place an order. Two shapes, and the difference between them is the
// whole point of the feature:
//
//   - `limit`   — rest a bid under the level (or an offer over it) straight
//                 away. The passive trade: you are trying to be filled *at* the
//                 level by price coming to you.
//   - `through` — wait for price to cross the level, then rest a stop a few
//                 ticks back on the side it came from. The trade only happens
//                 if price reclaims what it just gave up.
//   - `exit`    — wait for price to reach the level, then take the position
//                 off. The only shape that closes rather than opens, and the
//                 only one whose crossing direction does not matter: an exit at
//                 the NY VAH is an exit whether price arrived from under it or
//                 fell back onto it.
//
// Deliberately no velocity test on `through`. "Wait for a *fast* rejection" is
// the obvious knob and it is one this journal has already priced: the
// bounce-velocity study measured log-speed against R, residualised on position
// at 60s, at rho -0.137 / -0.118 over two independent runs — consistent in sign
// and *negative*, i.e. a faster snap-back was marginally worse, and no
// bail-on-speed rule replicated across the two. A speed gate here would be a
// setting that costs frames to compute and is known to sort nothing.
//
// This module is the geometry only: which price, and which of the two buttons
// the page's own `placeAt` would have been clicked with. It builds no orders and
// touches no state, so it is also the half a `src/journal/live/level_arm.py`
// would have to match tick-for-tick if this ever reaches a real account — the
// same split, and for the same reason, as lib/orderPresets.ts and the
// replaySim/ladder pair. Nothing here may import React or the chart.

/** What firing an arm does.
 *
 *  Names, not letters, because unlike the bracket presets these are not points
 *  on one scale — they are different trades. `limit` is filled by price
 *  arriving; `through` is filled by price leaving and coming back; `exit` is
 *  not a trade at all but the end of one. */
export type ArmShape = "limit" | "through" | "exit";

/** Whether an arm opens a position or closes one.
 *
 *  The distinction exists for the race (`⥃` in the panel): the first arm to fire
 *  cancels the others *of its own purpose only*, so that a level-based bracket
 *  — exit at the VAH or at the VAL, whichever comes first — can stand at the
 *  same time as a level-based entry pick without either wiping the other. One
 *  race over everything would make an exit firing cancel a pending entry, which
 *  is two unrelated decisions sharing a switch. */
export function armPurpose(shape: ArmShape): "entry" | "exit" {
  return shape === "exit" ? "exit" : "entry";
}

/** How far past the level a `through` order rests, in ticks.
 *
 *  Four ticks is one NQ point: far enough that a single print poking back over
 *  the line does not fill you, near enough that the reclaim is still the move
 *  you were watching for. **Chosen, not measured** — there is no study behind
 *  this number, the way there is none behind `COLLINEAR_TICKS`. If it ever gets
 *  swept, this comment is the thing to come back and delete. */
export const DEFAULT_THROUGH_TICKS = 4;

/** How far the levels panel reaches when it is being used to arm, in ticks.
 *
 *  `NEAR_TICKS` is 40 — ten NQ points — which is the right window for "what is
 *  price about to interact with" and the wrong one for "what do I want to be
 *  filled at later", since the second question is mostly about levels price has
 *  not reached. 240 ticks is 60 points, about a session's range. */
export const ARM_REACH_TICKS = 240;

/** A level the page can arm: whatever the chart is drawing, plus the lines you
 *  drew yourself.
 *
 *  `key` is the arm's identity across bar closes, and it is not the price — a
 *  developing POC that moves a tick is the same level, and an arm keyed on its
 *  value would silently become an arm on nothing. Hand-drawn lines key on their
 *  id (they all share the label "your line"); everything else on family+label,
 *  which the enumerator already guarantees is unique per drawn layer. */
export interface ArmableLevel {
  key: string;
  label: string;
  family: string;
  /** The level's value as of the last closed bar. */
  price: number;
}

/** A standing instruction on one level.
 *
 *  `price` is snapshotted when the arm is made and never chases the level
 *  afterwards. That is a real decision and not an oversight — see
 *  `armedPrice` below for why. */
export interface LevelArm {
  key: string;
  label: string;
  shape: ArmShape;
  /** The level's price at the moment it was armed, snapped to the tick grid —
   *  frozen, and it never chases the level afterwards.
   *
   *  That is a decision, not an omission. A resting order that followed a
   *  developing level would have to be re-priced as the level moved, and
   *  re-pricing means editing an order already in the log. `SimLadder` keys its
   *  checkpoints on log *identity*, so appending a new order is free — the
   *  prefix is untouched — while editing an existing one invalidates every
   *  checkpoint after it and forces a re-fold from that point. An order placed
   *  at 10:00 and followed every bar close to 15:00 would re-fold five hours of
   *  tape every thirty seconds, at whatever turbo multiple is running.
   *
   *  It is also the better trade on its own terms. An order chasing a falling
   *  band down is filled by the level arriving at *it* — `gapCloser`'s `level`
   *  class, the one the panel's own tooltip calls "a falling band 'touching' is
   *  not a touch". The event worth trading is price coming to the level, and
   *  only a frozen price can express it.
   *
   *  The cost is real and belongs on screen: arm the NY VAH at 10:00 and you
   *  have armed the price it was at 10:00. Re-arm to move it. */
  price: number;
  throughTicks: number;
}

/** Where an order goes and which of `placeAt`'s two buttons puts it there.
 *
 *  Expressed as a button rather than as (type, side) so that arming lands in the
 *  page through exactly the gesture a space+click already does: `placeAt` reads
 *  the side off the geometry, snaps to the tick grid, and holds the order one
 *  tick clear of the mark. Re-deriving any of that here would be a second copy
 *  of rules that already exist and already have the edge cases in them. */
export interface ArmPlacement {
  price: number;
  /** `left` = the passive order at that price (a bid under, an offer over);
   *  `right` = the one price has to be run through (a buy stop over, a sell
   *  stop under). The same two the chart's own click already means. */
  button: "left" | "right";
}

/** What firing an arm asks the page to do.
 *
 *  A union rather than a placement with a nullable price, because the two are
 *  not the same kind of answer: `place` names a price and a button and the page
 *  routes it through `placeAt`; `close` names nothing at all and goes through
 *  the flatten every manual exit already uses. Keeping them one type with an
 *  optional price would let a `close` carry a price nothing reads.
 *
 *  `close` is at market on the touch, and that is the honest shape: the position
 *  comes off at the next print and pays the crossing and the latency like any
 *  other market exit. Resting a reduce-only order *at* the level would fill
 *  better, but an order sized to a position is not something `OrderRec` can
 *  express today — it carries the ticket's size — and one resting while flat
 *  would open the opposite trade rather than close anything. */
export type ArmAction = ({ kind: "place" } & ArmPlacement) | { kind: "close" };

/**
 * Did the tape cross `level` over the prints in `[from, to)`, and which way?
 *
 * `-1` for a downward crossing, `+1` upward, `0` for neither. `prev` is the last
 * price before the range — the crossing is a transition, so the price the range
 * *starts* from is part of it.
 *
 * Per print rather than endpoint-to-endpoint, which is the difference between a
 * chime and an order: at 300x a single frame can carry seconds of tape, and a
 * poke through a level that is already back by the end of the frame is exactly
 * the event a `through` arm exists for. The predicate itself is the alert's —
 * `>=` and `<=` on the far side, so touching the level counts as reaching it.
 *
 * The first crossing in the range wins. A range that crosses down and back up
 * reports the down, because that is the one that happened first and an arm is
 * spent when it fires.
 */
export function crossDirection(
  price: Readonly<Float64Array>,
  from: number,
  to: number,
  prev: number,
  level: number,
): -1 | 1 | 0 {
  if (!Number.isFinite(prev) || !Number.isFinite(level)) return 0;
  let p = prev;
  for (let i = from; i < to; i++) {
    const v = price[i];
    if (p > level && v <= level) return -1;
    if (p < level && v >= level) return 1;
    p = v;
  }
  return 0;
}

/**
 * The passive order at the level: a bid under the market, an offer over it.
 *
 * Placed the moment the arm is made, because a limit order that waited for price
 * to arrive would be placed at the instant it was already too late. There is
 * nothing to trigger.
 */
export function limitPlacement(arm: LevelArm): ArmPlacement {
  return { price: arm.price, button: "left" };
}

/**
 * The reclaim order, once price has crossed the level.
 *
 * `dir` is `crossDirection`'s answer. Price crossed *down* through the level, so
 * the trade is a long back over it: the stop rests `throughTicks` **above**, and
 * price has to run through it to fill. Crossed up, the mirror image.
 *
 * Both are the right button — an order price has to be run through — which is
 * what makes this a reclaim rather than a fade. Resting a bid under the level
 * instead would be the `limit` shape, and it fills on the poke itself.
 */
export function throughPlacement(
  arm: LevelArm,
  dir: -1 | 1,
  tickSize: number,
): ArmPlacement {
  return { price: arm.price - dir * arm.throughTicks * tickSize, button: "right" };
}

/**
 * What an arm wants doing, given a tick range that just played.
 *
 * `null` means "not yet" — which for a `through` or `exit` arm is most frames,
 * and for a `limit` arm is never: a limit is placed by arming it, so it is the
 * caller's job to fire that one at the moment of the toggle rather than to poll
 * for it here. Kept in one function anyway so the shapes stay side by side and a
 * fourth can only be added by answering this question for it.
 *
 * `exit` reads the same crossing as `through` and then throws the direction
 * away. That is not a shortcut: a reclaim is a *side*, so which way price went
 * decides which way you are buying, whereas a flatten has no side to pick — the
 * position already has one. Touching the level from either side is the event.
 */
export function armAction(
  arm: LevelArm,
  price: Readonly<Float64Array>,
  from: number,
  to: number,
  prev: number,
  tickSize: number,
): ArmAction | null {
  if (arm.shape === "limit") return null;
  const dir = crossDirection(price, from, to, prev, arm.price);
  if (dir === 0) return null;
  if (arm.shape === "exit") return { kind: "close" };
  return { kind: "place", ...throughPlacement(arm, dir, tickSize) };
}

/** The arm on a level, or undefined. A plain lookup, named because the key
 *  scheme is the thing worth having in one place. */
export function armFor(arms: readonly LevelArm[], key: string): LevelArm | undefined {
  return arms.find((a) => a.key === key);
}

/** The key a hand-drawn line arms under. By id, not label: every line the
 *  enumerator hands over is called "your line", so a label key would collapse
 *  all of them into one arm. */
export function hlineKey(id: number): string {
  return `hline:${id}`;
}

/** The key any other drawn level arms under. */
export function levelKey(family: string, label: string): string {
  return `lvl:${family}|${label}`;
}
