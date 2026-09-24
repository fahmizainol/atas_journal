// A bracket leg, priced in money instead of ticks.
//
// A stop is set in ticks because that is what an order carries, but it is
// *decided* in dollars — "I am willing to be wrong about this for $250" — and
// the translation between the two moves under you: the same 50 ticks is $250 of
// NQ at one lot, $500 at two, and $50 of the micro. Typing the tick count means
// redoing that arithmetic every time the size or the routed contract changes,
// and the failure is silent: a ticket carried over from a one-lot sitting risks
// double on the next one and looks identical.
//
// So a leg may be **pinned to a dollar figure**. Pinned, the tick distance stops
// being a setting and becomes a reading — re-derived from the money whenever the
// size or the contract moves. Unpinned (`null`), the ticks are the setting and
// nothing here happens. Both legs pin independently: a dollar stop under a
// tick target is a perfectly ordinary ticket.
//
// **Ticks stay the truth on the wire.** Nothing downstream of `resolveBracketUsd`
// knows a leg was pinned — orders, guardrails, the ladder and the fill model all
// see the same `stopTicks` they always did. The pin is a rule for what that
// number is, not a second number racing it.

/** A ticket's two distance legs and the money they may be pinned to.
 *
 *  Both `TicketDraft` (the replay's) and `LiveTicket` extend this, which is what
 *  lets one resolver serve both pages — the pin means the same thing on a paper
 *  fill as on a real one, and a second implementation is how the two would come
 *  to disagree by a tick. */
export interface UsdBracket {
  size: number;
  stopTicks: number;
  targetTicks: number;
  /** Dollars this leg is pinned to, or `null` for "the ticks are the setting".
   *  Zero is not a pin at zero — a leg at 0 is a leg that is *off*, and it says
   *  so in `stopTicks`. */
  stopUsd: number | null;
  targetUsd: number | null;
}

/** What a leg costs to be wrong about, at this size. */
export function usdForTicks(ticks: number, tickUsd: number, size: number): number {
  if (!(ticks > 0) || !(tickUsd > 0) || !(size > 0)) return 0;
  return ticks * tickUsd * size;
}

/**
 * The tick distance that costs `usd`, at this tick money and size.
 *
 * Rounded to a whole tick, because that is the only distance an order can be
 * placed at — so a pinned leg is nearly always worth a dollar or two either side
 * of the figure typed, and every surface prints what it actually came to rather
 * than the figure it was asked for.
 *
 * Floored at one tick for any positive figure: a $1 stop on NQ is not a stop at
 * zero distance (which is the leg *off*, and a different statement), it is the
 * smallest stop there is. Zero in, zero out — the leg is off.
 *
 * Money the caller doesn't have — no session, an unknown contract — gives 0 as
 * well, which is why `legTicks` guards this rather than calling it blind: a
 * pinned leg silently turning itself off is exactly the failure the pin is for.
 */
export function ticksForUsd(usd: number, tickUsd: number, size: number): number {
  if (!(usd > 0) || !(tickUsd > 0) || !(size > 0)) return 0;
  return Math.max(1, Math.round(usd / (tickUsd * size)));
}

/**
 * The distance a leg is actually placed at: the pin's, or the stored ticks.
 *
 * The stored ticks are not dead while a pin is live — they are what the leg
 * falls back to when the money is unknown, and they are kept up to date at every
 * pin (see `pinnedBracket`). That is the difference between a chart that hasn't
 * said what a tick is worth yet leaving the bracket alone, and it quietly
 * sending an order with no stop on it.
 */
export function legTicks(
  ticks: number,
  usd: number | null,
  tickUsd: number,
  size: number,
): number {
  if (usd == null) return ticks;
  const t = ticksForUsd(usd, tickUsd, size);
  // usd <= 0 is the leg off, and that is an answer; only unknown money falls back.
  return t > 0 || !(usd > 0) ? t : ticks;
}

/**
 * A whole ticket with both legs resolved — the shape every order path reads.
 *
 * Returns the ticket itself when nothing moved, so the identity checks the pages
 * are built on (`useMemo` deps, `t[key] === v` before a `setState`) keep holding
 * and a resolve on every render costs nothing.
 */
export function resolveBracketUsd<T extends UsdBracket>(t: T, tickUsd: number): T {
  const stopTicks = legTicks(t.stopTicks, t.stopUsd, tickUsd, t.size);
  const targetTicks = legTicks(t.targetTicks, t.targetUsd, tickUsd, t.size);
  if (stopTicks === t.stopTicks && targetTicks === t.targetTicks) return t;
  return { ...t, stopTicks, targetTicks };
}

/**
 * Pin a leg to a figure — the money **and** the ticks it comes to right now.
 *
 * Both, always: writing the pin alone would leave the fallback distance behind
 * at whatever the leg was last set to by hand, which is the number a ticket
 * reverts to the moment the contract goes unknown. Passing `null` unpins and
 * leaves the distance exactly where the pin had it, which is what makes the
 * `$`/`t` toggle a change of unit rather than a change of bracket.
 */
export function pinnedLeg(
  usd: number | null,
  ticks: number,
  tickUsd: number,
  size: number,
): { usd: number | null; ticks: number } {
  if (usd == null) return { usd: null, ticks };
  const t = ticksForUsd(usd, tickUsd, size);
  return { usd, ticks: t > 0 || !(usd > 0) ? t : ticks };
}
