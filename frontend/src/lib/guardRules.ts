// The discipline rules, as the replay can apply them.
//
// A deliberate mirror of `journal.live.routing` rather than a second design.
// The point of putting them here at all is that practice should refuse what the
// funded account refuses — a bracket you rehearse for a month and then cannot
// place is worse than no rehearsal. So the levels come down from
// `/live/routing` (`RoutingStatus.guards`) and only fall back to the constants
// below when there is no API to ask, and the shape rules are the same
// arithmetic in the same units.
//
// WHAT DOES NOT MIRROR, AND WHY. The server's slow-down rule — no entry within
// `min_gap_s` of the last one once the day is far enough down — is **not
// enforced here**, and that is not an omission. Replay runs at 30x. Two minutes
// of market time is four seconds of yours, so enforcing it would train nothing;
// enforcing it against the wall clock instead would make speed-30 replay
// unusable. What the rule is really about is real-time patience, and the only
// honest thing a compressed clock can do with that is *measure* it. Hence
// `DayState.medianGapS` and `fastShare`, which are reported and never enforced.
//
// The other thing that does not cross: nothing here is enforcement in the sense
// the server's is. This is the browser refusing itself, on a page where the
// money is imaginary. It exists to build the habit, not to hold the line.

import type { GuardLevels } from "./routingTypes";
import type { Position, Side, Trade } from "./replaySim";

/** Used until `/live/routing` answers. Matches `routing.Guards`' own defaults,
 *  so an offline replay is not quietly practising against different numbers. */
export const DEFAULT_GUARDS: GuardLevels = {
  daily_loss_stop: 500,
  daily_profit_lock: 0,
  slow_down_at: 300,
  min_gap_s: 120,
  min_target_ticks: 100,
  stop_ticks_min: 40,
  stop_ticks_max: 60,
  require_bracket: true,
  auto_flatten: true,
  max_risk_usd: 250,
  commission_per_side: 3.5,
};

/** A trade that resolved inside this many seconds is the one habit the
 *  behavioural audit found actually costs money — about half of all entries,
 *  winning 26% of the time. It is an *entry* problem (85% of the damage is the
 *  stop firing, not an early manual exit), which is why this is a counter and
 *  never a rule. */
export const FAST_TRADE_MS = 30_000;

export interface OrderShape {
  stopTicks: number;
  targetTicks: number;
  size: number;
  /** What one tick is worth on the contract being replayed: tick size x point
   *  value. Zero disables the dollar-risk check — see `shapeRefusal`. */
  tickUsd: number;
}

/**
 * Why this entry may not be placed, or null. Mirrors `routing._check_shape`.
 *
 * Reducing orders never reach here: closing size has no target to be too tight,
 * and a rule that could refuse an exit is, at the worst moment, a rule that
 * keeps you in a trade.
 */
export function shapeRefusal(g: GuardLevels, o: OrderShape): string | null {
  if (g.require_bracket && !(o.stopTicks && o.targetTicks)) {
    return `every entry goes out bracketed — ${g.stop_ticks_min}–${g.stop_ticks_max} tick stop, ${g.min_target_ticks}+ tick target`;
  }
  if (g.min_target_ticks && o.targetTicks && o.targetTicks < g.min_target_ticks) {
    return `a ${o.targetTicks}-tick target is under the ${g.min_target_ticks}-tick floor — every target at or under 80 ticks is net-negative on your own book, at every stop width tried`;
  }
  if (g.stop_ticks_min && o.stopTicks && o.stopTicks < g.stop_ticks_min) {
    return `a ${o.stopTicks}-tick stop is tighter than the ${g.stop_ticks_min}-tick floor — a 40-tick stop was getting noise-stopped in the flat half of the sample`;
  }
  if (g.stop_ticks_max && o.stopTicks && o.stopTicks > g.stop_ticks_max) {
    return `a ${o.stopTicks}-tick stop is wider than the ${g.stop_ticks_max}-tick ceiling — take fewer contracts instead, the drawdown that ends an account is fixed in dollars`;
  }
  if (g.max_risk_usd && o.stopTicks && o.tickUsd > 0) {
    const risk = o.stopTicks * o.tickUsd * o.size;
    if (risk > g.max_risk_usd) {
      return `this risks $${Math.round(risk).toLocaleString()} — ${o.stopTicks} ticks × ${o.size} at $${o.tickUsd.toFixed(2)} a tick — against a $${Math.round(g.max_risk_usd).toLocaleString()} ceiling`;
    }
  }
  return null;
}

/** Today, as the rules see it — plus the three behavioural numbers the
 *  operating plan says to log after every session. */
export interface DayState {
  /** Running realised P&L. `Trade.pnl` is already net of commission, so unlike
   *  the server this needs no fee arithmetic of its own. */
  realized: number;
  trades: number;
  /** Why the day is over, or null. Latching falls out for free here: the
   *  simulation is re-derived from the log, so "did the running total ever
   *  cross the line" is the natural computation rather than a flag to keep.
   *  A rewind lifts it, which is right — those trades have been un-happened. */
  locked: string | null;
  /** Past the slow-down level and not yet stopped. Reported, never enforced. */
  slow: boolean;
  /** Deepest the day ever got. The lock reads off this, not off the close. */
  low: number;

  // --- the behaviour, measured ------------------------------------------
  /** Trades that resolved inside 30 seconds of market time, and their share. */
  fastCount: number;
  fastShare: number | null;
  /** Median seconds between one entry and the next. 136s on your green days,
   *  76s on your red ones — with the trade *count* identical. */
  medianGapS: number | null;
  /** Whether any trade was opened while the day was already past the
   *  slow-down level. The third number worth logging. */
  tradedInTheHole: boolean;
}

export function dayState(g: GuardLevels, trades: Trade[]): DayState {
  const byExit = trades.slice().sort((a, b) => a.exitMs - b.exitMs);
  let run = 0;
  let low = 0;
  let locked: string | null = null;
  for (const t of byExit) {
    run += t.pnl;
    if (run < low) low = run;
    if (!locked && g.daily_loss_stop && run <= -g.daily_loss_stop) {
      locked = `the daily stop of $${Math.round(g.daily_loss_stop).toLocaleString()} was reached`;
    }
    if (!locked && g.daily_profit_lock && run >= g.daily_profit_lock) {
      locked = `the daily profit lock of $${Math.round(g.daily_profit_lock).toLocaleString()} was reached`;
    }
  }

  const fastCount = trades.filter((t) => t.exitMs - t.entryMs < FAST_TRADE_MS).length;

  // Distinct opens, because a scale-out books several rows against one entry
  // and counting each would report a gap of zero that nobody took.
  const opens = [...new Set(trades.map((t) => t.entryMs))].sort((a, b) => a - b);
  const gaps = opens.slice(1).map((ms, i) => (ms - opens[i]) / 1000);

  // Was anything *opened* while the day was already in the hole? Walks realised
  // P&L as of each open rather than at the close — the question is what was
  // known at the moment the decision was made.
  let tradedInTheHole = false;
  if (g.slow_down_at) {
    for (const open of opens) {
      const realizedBefore = byExit
        .filter((t) => t.exitMs <= open)
        .reduce((s, t) => s + t.pnl, 0);
      if (realizedBefore <= -g.slow_down_at) {
        tradedInTheHole = true;
        break;
      }
    }
  }

  return {
    realized: run,
    trades: trades.length,
    locked,
    low,
    slow: !locked && !!g.slow_down_at && run <= -g.slow_down_at,
    fastCount,
    fastShare: trades.length ? fastCount / trades.length : null,
    medianGapS: gaps.length ? median(gaps) : null,
    tradedInTheHole,
  };
}

function median(xs: number[]): number {
  const s = xs.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Why no new entry may go on right now, or null. Mirrors `routing.day_refusal`
 *  minus the timing rule — see the note at the top of this file. */
export function dayRefusal(day: DayState, reducing: boolean): string | null {
  if (reducing || !day.locked) return null;
  return `${day.locked} — the day is over. It stays over even if the running total comes back; "one more to get back to level" is the trade this refuses. Closing out still works.`;
}

/**
 * Has the day spent its loss limit **on equity**, counting the open position?
 * Returns the reason to close, or null.
 *
 * The rule realised P&L cannot express. A position held at −$800 has already
 * spent the drawdown whether or not it has been booked, and a stop that waited
 * for the booking would sit silent through the loss and then refuse the *next*
 * order — which was never the problem.
 *
 * Replay applies this by closing the position the way you would, appending to
 * the log, so a rewind un-does it like anything else. It lands a beat late: the
 * open P&L arrives on the HUD's throttled tick, which at speed 30 is a couple of
 * seconds of market time. Live it fires on the broker's own PnL update. Close
 * enough to rehearse against, not close enough to quote.
 */
export function equityStop(
  g: GuardLevels,
  day: DayState,
  openPnl: number,
  hasPosition: boolean,
): string | null {
  if (!g.auto_flatten || !g.daily_loss_stop || !hasPosition) return null;
  const equity = day.realized + openPnl;
  if (equity > -g.daily_loss_stop) return null;
  return `the daily stop of $${Math.round(g.daily_loss_stop).toLocaleString()} was reached on equity ($${Math.round(equity).toLocaleString()} with the open position) — closed automatically`;
}

/** Does this order take size off rather than put it on? A flip is not a reduce:
 *  an order bigger than what is held closes the position *and opens a fresh
 *  one*, which is an entry however it is framed — and it is the shape somebody
 *  reaches for once a rule has just refused them. */
export function isReducing(open: Position | null, side: Side, size: number): boolean {
  if (!open || !open.size) return false;
  return side !== open.side && size <= open.size;
}
