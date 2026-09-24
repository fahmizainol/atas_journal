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
// unusable. `min_gap_s` is a wall-clock rule and stays Live's alone. Hence
// `DayState.medianGapS` and `fastShare`, which are reported and never enforced.
//
// WHAT CHANGED (2026-09-23). The paragraph above used to end "what the rule is
// really about is real-time patience, and the only honest thing a compressed
// clock can do with that is *measure* it". That has since been tested, and the
// premise is wrong. Pace measured on the **tape** clock predicts the next
// trade; pace measured on the wall clock predicts nothing — in any corpus, at
// any threshold, every confidence interval spanning zero. So a compressed clock
// is not the handicap that sentence assumed: it is the clock carrying the
// signal, and a tape-time pace rule is a meaningful thing for a replay to
// apply. That rule is `paceRefusal` below. The wall-clock conclusion about
// `min_gap_s` is untouched — two different rules on two different clocks, and
// only one of them turned out to predict anything.
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

import { type AccountView, type LiveAccount } from "./replayAccount";
import type { GuardLevels } from "./routingTypes";
import { openStamps, type Position, type Side, type Trade } from "./replaySim";

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

/** The pace window: **three entries opened inside three minutes of market
 *  time** (a rate of 1.0 trades per tape-minute).
 *
 *  WHERE IT CAME FROM. A prospective test — trailing pace over the last three
 *  entries, scored against the *next* trade, clustered by sitting/day so trades
 *  inside one sitting are not counted as independent observations:
 *
 *    drill   fires on 21.3% — warned −0.183R against +0.005R quiet.
 *            −0.187R, 95% CI [−0.306, −0.055], p=0.002, with a clean
 *            dose-response: 0.4 → 0.6 → 1.0 gives −0.118 → −0.143 → −0.187R.
 *    replay  fires on 46.0% — warned −0.019R against −0.113R quiet. INVERTED.
 *    live    fires on 15.4% — warned +$58.85/1NQ against +$18.91. INVERTED.
 *            (203 de-duplicated pre-era trades across 16 days.)
 *
 *  So this is validated in **drill only**. It is shown in replay and on Live at
 *  the operator's explicit direction, knowing it points the wrong way in both —
 *  in replay it fires on nearly half of all entries and the ones it flags are
 *  the better half. A warning on those two surfaces is not evidence of anything
 *  and must not be quoted as if it were.
 *
 *  Two further honesties. The threshold was chosen on the same data that
 *  produced the effect, so −0.187R is an upper bound on what a forward test
 *  should find, not a forecast. And the neighbouring sub-30s number is partly
 *  reverse-causal — `FAST_TRADE_MS` already records that 85% of that damage is
 *  the stop firing rather than an early manual exit, and a trade running into
 *  its stop is short by construction.
 *
 *  It does corroborate something already measured here: `medianGapS` is
 *  documented at 136s on green days and 76s on red ones. Three entries in three
 *  minutes is a 90s average gap — between the two. */
export const PACE_WINDOW = 3;
export const PACE_SPAN_MS = 180_000;

/**
 * Why the next entry is coming too fast, or null. **Tape time, not wall time.**
 *
 * Deliberately the same contract as `shapeRefusal`: a sentence and never a
 * code, null when there is nothing to say. That is what makes promoting this
 * from a banner to a refusal a one-line move at the call site — nothing in here
 * changes for it, so no enforcement scaffolding has to be carried meanwhile.
 *
 * Positions, not lots, via `openStamps`. A scale-out books a row per portion
 * against one entry, and on Live a Rithmic bracket is attached per *partial
 * fill* — so counting rows would read one bracketed entry that filled in three
 * parts as three entries seconds apart, and fire on every one of them. The
 * blotter has already been bitten by exactly this; see `Blotter.tsx`.
 *
 * It takes no clock. The measurement it mirrors classified a trade purely by
 * the span of the three entries *before* it, however long after them it came —
 * so a burst stays flagged until the next unhurried entry rolls it out of the
 * window, and passing a clock in would be a decay the evidence does not have.
 */
export function paceRefusal(rows: readonly { entryMs: number }[]): string | null {
  const opens = openStamps(rows);
  if (opens.length < PACE_WINDOW) return null;
  const recent = opens.slice(-PACE_WINDOW);
  const span = recent[recent.length - 1] - recent[0];
  if (span > PACE_SPAN_MS) return null;
  const mins = (span / 60_000).toFixed(1);
  return `your last ${PACE_WINDOW} entries opened inside ${mins} minutes of market time — faster than one every ${PACE_SPAN_MS / 60_000} minutes, the band that measured worst in backtest`;
}

/** Today, as the rules see it — plus the three behavioural numbers the
 *  operating plan says to log after every session. */
export interface DayState {
  /** Running realised P&L. `Trade.pnl` is already net of commission, so unlike
   *  the server this needs no fee arithmetic of its own. */
  realized: number;
  /** How many **positions** were opened and closed today — one per decision,
   *  however many portions it came off in. The count the journal reports for
   *  the same day (`replaySim.openStamps`), so a page and the ledger behind it
   *  cannot say different numbers about it. */
  trades: number;
  /** The closed lots behind those, which is what the blotter lists a row for.
   *  Equal to `trades` on a day nothing was scaled out of, and the reason the
   *  blotter can say "8 trades · 12 legs" instead of looking like it cannot
   *  count its own rows. */
  legs: number;
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

  // Per *lot*, deliberately, unlike `trades` below. The 30-second habit is a
  // finding about exits that resolve too fast (docs/research — the manual-trade
  // behaviour audit), measured on the rows it was measured on; re-basing it on
  // positions here would move a research number as a side effect of relabelling
  // a count on a panel.
  const fastCount = trades.filter((t) => t.exitMs - t.entryMs < FAST_TRADE_MS).length;

  // Distinct opens, because a scale-out books several rows against one entry
  // and counting each would report a gap of zero that nobody took. The same
  // grouping now answers "how many trades today" — see `openStamps`.
  const opens = openStamps(trades);
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
    trades: opens.length,
    legs: trades.length,
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
 * Has the day spent its loss limit on **booked** P&L, with size still on?
 * Returns the reason to close, or null.
 *
 * **This used to read equity**, on the argument that a position held at −$800
 * has already spent the drawdown whether or not it has been booked. That is
 * true of the account's floor — `accountBreach` still reads it, and the firm
 * marks it continuously — and it is not true of this rule. The daily stop is a
 * rule about how much a losing day is allowed to have *done*, and a trade the
 * market has not yet taken anything for has not done any of it. Marking a
 * runner and ending it on that mark closes trades at their worst moment, which
 * is the opposite of what a discipline rail is for.
 *
 * So the only moment the stop can be reached is the moment a trade books, and
 * the only thing left for this to decide is what happens when one books with
 * size still on — a scale-out that took the day past the line and left a
 * runner. `dayState.locked` has already latched by then; this is the half that
 * acts on it. The loss half only: the profit lock refuses the next entry and
 * has never had a reason to end a trade that is winning.
 *
 * Replay applies it by closing the position the way you would, appending to the
 * log, so a rewind un-does it like anything else. Nothing here lands late any
 * more — a booked figure is exact at the instant it is booked, which is the
 * other thing the move off equity bought.
 */
export function dayFlatten(
  g: GuardLevels,
  day: DayState,
  hasPosition: boolean,
): string | null {
  if (!g.auto_flatten || !g.daily_loss_stop || !hasPosition) return null;
  if (day.realized > -g.daily_loss_stop) return null;
  return `the daily stop of $${Math.round(g.daily_loss_stop).toLocaleString()} was reached ($${Math.round(day.realized).toLocaleString()} booked) and size was still on — closed automatically`;
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
 * reason the sitting is over, or null.
 *
 * **It does not wait for a position.** A breach is a breach whether the money
 * is still on or already booked — the sitting that proved it stopped out
 * through the floor and was flat again before the check ever ran, and a floor
 * that only fires mid-position is a floor you can realize your way through.
 * `hasPosition` only shapes the sentence (whether something was closed).
 *
 * The comparison happens here rather than on the server for the reason the
 * floor is end-of-day trailing: the floor does not move during a sitting, so
 * there is exactly one moving part, and it is the one the browser already
 * holds.
 *
 * `equity` is that moving part, and it is **passed in rather than assembled
 * here**. It used to be `view.equity + day.realized + openPnl`, which is right
 * only while the sitting on screen is unsettled — and the page is not always in
 * a position to know that (`replayAccount.counted_ids` says why). Whoever knows
 * what the account has already priced is the only one who can add the rest, so
 * this asks for the answer instead of guessing at it. `replayAccount.liveEquity`
 * is that answer; nothing else should be computing it.
 *
 * Lands a beat late, and unavoidably: `openPnl` arrives on the HUD's throttled
 * tick, which at speed 30 is a couple of seconds of market time. Fine to
 * rehearse against, not a number to quote. `dayFlatten` used to share the
 * problem and no longer does — it reads booked P&L, which is exact.
 */
export function accountStop(
  view: AccountView | undefined,
  live: LiveAccount | null,
  hasPosition: boolean,
): string | null {
  return accountBreach(view, live, hasPosition)?.reason ?? null;
}

/** Which of the account's three limits has been reached, or null.
 *
 *  All three are **always on**. The header above says stakes you can switch off
 *  are not stakes, and that is as true of the prop firm's daily limit as of its
 *  drawdown floor — the point of rehearsing on an account is that its rules are
 *  the ones you cannot argue with.
 *
 *  Ordered by what it would be absurd to be told instead. A blown account is
 *  blown whatever the day did, so the floor comes first; the day goal is a good
 *  day ending and the daily loss is a bad one, and reaching the loss limit while
 *  the goal is armed means the goal was given back, which is the sentence worth
 *  reading.
 *
 *  **The floor arrives as a `LiveAccount` rather than as a number**, because on
 *  an intraday-trailing template it is a live figure with the same `counted_ids`
 *  dependency the equity has. `replayAccount.liveAccount` is what produces the
 *  pair; nothing else should be assembling either half.
 *
 *  Lands a beat late for the same reason `accountStop` does: the open P&L is
 *  marked against a price off the HUD's throttled tick. Fine to rehearse
 *  against, not a number to quote.
 *
 *  Late is the only error the caller may hand it. **The open half and the
 *  realised half must come from the same instant** — this adds them, so a
 *  position sampled a beat after the trade that closed it books the same loss
 *  twice and ends a day that never reached its limit. It did, twice, on
 *  2026-08-25; `Simulator.markOpen` is where that is now prevented, and it is
 *  the reason the position is an argument there rather than a ref read. */
export function accountBreach(
  view: AccountView | undefined,
  live: LiveAccount | null,
  hasPosition: boolean,
): { kind: "floor" | "day" | "goal"; reason: string } | null {
  if (!view || !live) return null;
  const usd = (x: number) => `$${Math.round(x).toLocaleString()}`;
  const closed = hasPosition
    ? ", with the open position. Closed automatically"
    : "";

  if (live.room <= 0) {
    return {
      kind: "floor",
      reason:
        `the account has reached its ${view.rules.trailing === "intraday" ? "" : "trailing "}floor — ` +
        `${usd(live.equity)} against a ${usd(live.floor)} floor${closed}. ` +
        `The sitting is over. This is what a blown account is; there is no ` +
        `version of it you trade back from in the same session.`,
    };
  }

  const goal = view.rules.day_goal;
  // Armed on **booked** P&L: a runner that spikes through the goal and comes
  // back has not made the day, and a goal that armed on unrealised would end
  // the day on a wick.
  const armed = goal != null && goal > 0 && live.dayRealized >= goal;
  if (armed && live.dayTotal < goal) {
    return {
      kind: "goal",
      reason:
        `the day's goal of ${usd(goal)} was reached and is now the day's floor — ` +
        `${usd(live.dayTotal)} is under it${closed}. The day is over: you made ` +
        `the number, and giving it back is the one way to turn a good day into a bad one.`,
    };
  }

  const limit = view.rules.day_loss;
  if (limit > 0 && live.dayTotal <= -limit) {
    return {
      kind: "day",
      reason:
        `the daily loss limit of ${usd(limit)} was reached — ${usd(live.dayTotal)} on the day` +
        `${closed}. The day is over; the account survives it, which is the whole ` +
        `point of a limit that is not the floor.`,
    };
  }
  return null;
}

/** Why the account will not open a **new** sitting, or null.
 *
 *  One clause, since the review stopped being mandatory on 2026-08-25 (see
 *  `replay_account.review_flagged`). It is still written as an ordered list
 *  because the thing that made the order matter — at a dead account the only
 *  sentence worth reading is that it is dead — is what decides where the next
 *  clause goes.
 *
 *  Null while a sitting is already open, and that is deliberate: resuming what
 *  you are in the middle of is free. The gate is on *starting*, because
 *  starting again immediately is the behaviour it exists to price. **Except
 *  when the account is blown** — that state outranks the open sitting, because
 *  the sitting a blown account is "in the middle of" is the one that killed it,
 *  and resuming it to trade on is exactly the trading-back that a blown account
 *  must not have (the reload path of the same rule `Simulator`'s own
 *  `sittingDeadRef` enforces within a session).
 *
 *  **The refusal is not a deadline, and there is no longer one that is.** This
 *  function used to take `receivedAt` and compare `cooldown_until` against the
 *  server's clock, because a gate whose answer is "wait" goes stale in an open
 *  tab: nothing refetches while you are waiting out a timer, so reading the
 *  deadline as a boolean would leave the gate shut long after it had lifted.
 *  Both timed gates are gone (the hour between sittings with the review revamp,
 *  the day after a death on 2026-08-24), and what is left cannot go stale the
 *  same way — a blown account is a state you leave by writing the sentence, and
 *  writing it invalidates the query. */
export function accountRefusal(
  view: AccountView | undefined,
  sittingOpen: boolean,
): { code: string; message: string } | null {
  if (!view) return null;
  if (sittingOpen && view.status !== "blown") return null;
  if (view.status === "blown") {
    return {
      code: "blown",
      message:
        "the account is blown — write what killed it before anything else. " +
        "One sentence is the whole of what a dead account costs now, which is exactly why it is not skippable.",
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
