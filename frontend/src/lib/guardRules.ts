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
// The stop *floor* — `stop_ticks_min` — is **not enforced here either**, and
// that one is a deliberate reversal rather than a clock problem. The floor
// exists because a 40-tick stop was getting noise-stopped in the flat half of
// the sample, which is a finding about where the stop should sit, not a rule
// the replay should be unable to test. Refusing a 50-tick stop in practice
// means the one place cheap enough to re-measure the finding is the one place
// that will not run it. The ceiling stays, the target floor stays, and the
// dollar-risk ceiling stays: those bound what an account survives, and none of
// them stop a question from being asked. Live is unchanged — `routing.py`
// still refuses a stop under the floor, and this file no longer mirrors it.
//
// The other thing that does not cross: nothing here is enforcement in the sense
// the server's is. This is the browser refusing itself, on a page where the
// money is imaginary. It exists to build the habit, not to hold the line.
//
// THE SWITCH. `REPLAY_GUARDRAILS=0` in `.env` turns the layer off for `/replay`
// — read off `/live/routing` like the levels are, and applied by the caller
// (`pages/Simulator`) rather than in here: these functions answer "what do the
// rules say", which stays a worthwhile question with the layer off, and the
// replay goes on asking it so the strip can report what it would have refused.
// It is a separate flag from `LIVE_GUARDRAILS` and neither implies the other.
// The reason to have it at all is the one in the paragraph above about the stop
// floor, generalised: the replay is where a rule gets *tested*, and a rule that
// forbids its own test can only ever be confirmed.

import type { AccountView } from "./replayAccount";
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
    const stopBound = g.stop_ticks_max ? `a stop no wider than ${g.stop_ticks_max} ticks` : "a stop";
    return `every entry goes out bracketed — ${stopBound}, ${g.min_target_ticks}+ tick target`;
  }
  if (g.min_target_ticks && o.targetTicks && o.targetTicks < g.min_target_ticks) {
    return `a ${o.targetTicks}-tick target is under the ${g.min_target_ticks}-tick floor — every target at or under 80 ticks is net-negative on your own book, at every stop width tried`;
  }
  // No stop floor. See the header: the replay is where a tighter stop gets
  // tested, so `stop_ticks_min` is read for display and never refused on.
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

// --- the account ------------------------------------------------------------
// Everything above is a rule about *this session*: a shape, a day, a running
// total that resets tomorrow. The two below are the only rules here that
// remember last week, and they are the only ones that are **always on**.
//
// `REPLAY_GUARDRAILS` exists because a rule under test has to be testable — the
// stop floor is the worked example in this file's header. The account is not a
// rule under test. It is the stakes, and stakes you can switch off are not
// stakes, so neither function below consults `guardsOn` and neither caller may.

/**
 * Has the account hit its trailing floor, counting the open position? The
 * reason to flatten, or null.
 *
 * The equity join happens here rather than on the server for the reason the
 * floor is end-of-day trailing: the floor does not move during a sitting, so
 * there is exactly one moving part, and it is the one the browser already
 * holds. `view.equity` is settled attempts only — the sitting in progress is
 * still `active` and so is not in it — which is why the two live terms are
 * added rather than replacing anything.
 *
 * Lands a beat late for the same reason `equityStop` does: `openPnl` arrives on
 * the HUD's throttled tick. Fine to rehearse against, not a number to quote.
 */
export function accountStop(
  view: AccountView | undefined,
  day: DayState,
  openPnl: number,
  hasPosition: boolean,
): string | null {
  if (!view || !hasPosition) return null;
  const equity = view.equity + day.realized + openPnl;
  if (equity > view.floor) return null;
  return (
    `the account has reached its trailing floor — $${Math.round(equity).toLocaleString()} ` +
    `against a $${Math.round(view.floor).toLocaleString()} floor, with the open position. ` +
    `Closed automatically, and the sitting is over. This is what a blown account is; ` +
    `there is no version of it you trade back from in the same session.`
  );
}

/** Why the account will not open a **new** sitting, or null.
 *
 *  Ordered by what it would be absurd to be told instead. "Wait 20 minutes" is
 *  the wrong sentence to read at a dead account, so the terminal states come
 *  first and the hour gate last.
 *
 *  Null while a sitting is already open, and that is deliberate: resuming what
 *  you are in the middle of is free. The gate is on *starting*, because
 *  starting again immediately is the behaviour it exists to price. */
export function accountRefusal(
  view: AccountView | undefined,
  sittingOpen: boolean,
): { code: string; message: string; until: string | null } | null {
  if (!view || sittingOpen) return null;
  if (view.status === "blown") {
    return {
      code: "blown",
      message:
        "the account is blown — write what killed it before anything else. " +
        "The timeout runs from the death either way, so this costs you nothing but is not skippable.",
      until: null,
    };
  }
  if (view.status === "cooldown") {
    return {
      code: "cooldown",
      message:
        "the account is blown and the day's timeout has not run out. " +
        "A day is what a blown account costs; that is the whole point of it costing something.",
      until: view.cooldown_until,
    };
  }
  if (view.review_block) {
    return {
      code: "review",
      message:
        "the last sitting has not been reviewed. Every flag on it needs a verdict — " +
        "a leak or justified — before another one starts.",
      until: null,
    };
  }
  if (view.next_sitting_at) {
    return {
      code: "hour",
      message:
        "an hour between sittings. Back-to-back replays are how one bad session becomes six, " +
        "and the hour is the only part of a rep that a compressed clock cannot compress.",
      until: view.next_sitting_at,
    };
  }
  return null;
}

/** Does this order take size off rather than put it on? A flip is not a reduce:
 *  an order bigger than what is held closes the position *and opens a fresh
 *  one*, which is an entry however it is framed — and it is the shape somebody
 *  reaches for once a rule has just refused them. */
export function isReducing(open: Position | null, side: Side, size: number): boolean {
  if (!open || !open.size) return false;
  return side !== open.side && size <= open.size;
}
