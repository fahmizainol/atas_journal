// One guard readout, for both terminals.
//
// This is the open item the plan doc deferred, resolved in the direction that
// item argued for. The objection to "the dock's guard meters" was that three
// meters *beside* the replay's `Discipline` strip would be a second rendering of
// the same facts. Correct — so this **replaces** it rather than joining it, and
// there is one rendering on both pages instead of two on one.
//
// What made it possible was phase 10. The numbers used to be Live-only because
// they come off `routing.Guards` and an account, and the replay had neither an
// account nor a drawdown floor; it does now, and the two pages are finally
// answering the same questions:
//
//   - how much of today is left?           (the daily loss limit)
//   - how much of the *account* is left?   (the trailing floor)
//   - how much size may go on?             (the contract cap)
//   - is anything currently refusing?      (the lock, and the last refusal)
//
// One feed interface with an adapter per page, rather than two components that
// look alike. The adapters are the only place that knows a `DayState` from a
// `GuardState`; everything below this line is the same on both.

import { RefusalFlash } from "./RefusalFlash";
import type { GuardLevels } from "../../lib/routingTypes";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

export interface GuardFeed {
  /** The restraint layer is enforced. Off, every number here is still measured
   *  and shown — a safety layer that is silently off is worse than one nobody
   *  built — and the readout says so, loudly. */
  on: boolean;
  levels: GuardLevels;
  /** Realised on the day, net of commission. */
  realized: number;
  /** How many **positions** were opened and closed today, which is what the
   *  journal calls a trade. Never the row count of the blotter beside it: a
   *  scale-out, or a bracket that filled in parts, books a lot each. */
  trades: number;
  /** Those lots, when there are more of them than there are trades. Null, or
   *  equal to `trades`, says nothing was split and the meter shows one number. */
  legs: number | null;
  /** Why the day is over, or null. Latched. */
  locked: string | null;
  /** Past the slow-down level and not yet stopped. */
  slow: boolean;
  /** Live equity and the trailing floor under it, or null on a page with no
   *  account behind it. The floor meter simply does not draw then — an empty
   *  bar would read as "no room left". */
  equity: number | null;
  floor: number | null;
  /** The full bar of the floor meter — the account's own max loss, not a
   *  constant. A 25K and a 50K of one product have different drawdowns and the
   *  meter has to read as a fraction of the right one. Null falls back to the
   *  distance the floor currently sits below equity, which draws a full bar
   *  rather than a wrong one. */
  floorTotal: number | null;
  /** The day's allowance, and what is left of it. Supplied by the adapter
   *  rather than derived here, because the two pages mean different things by
   *  a day: Live's is the switchable personal stop off `routing.Guards`, and
   *  the replay's is the prop firm's own daily loss limit, which is not
   *  switchable and ends the day by itself. */
  dayLimit: number | null;
  dayLeft: number | null;
  /** The day's profit goal, what is left to reach it, and whether it has been
   *  made. Once armed the goal *is* the day's floor: reaching it is the end of
   *  a good day, and the meter says so by filling rather than depleting. Null
   *  on an account that has no goal set, and the meter simply does not draw. */
  goal: number | null;
  goalLeft: number | null;
  goalArmed: boolean;
  /** How the floor moves, so the tooltip can say. Null on a page with no
   *  account. Read from the account's template — never guessed. */
  trailing: "eod" | "intraday" | null;
  /** Contracts currently on, and the most that may be. */
  size: number;
  cap: number;
  /** The three behavioural numbers the operating plan says to log, or nulls on
   *  a page that cannot measure them. Reported, never enforced — the audit
   *  found them to be entry problems, and a rule that refuses an entry on its
   *  own speed is a rule that fires at the wrong moment. */
  fastShare: number | null;
  medianGapS: number | null;
  tradedInTheHole: boolean | null;
  /** Why the next entry is coming too fast, or null — `guardRules.paceRefusal`,
   *  already rendered as a sentence. Unlike the three numbers above this one is
   *  *forward*-looking: they are medians over a day that has happened, this is
   *  a statement about the entry not yet placed. Still reported and never
   *  enforced, and the copy says so, so that promoting it later reads as a
   *  change rather than as a line nobody had noticed.
   *
   *  Honest about where it holds: validated in backtest, inverted in replay and
   *  on Live. `paceRefusal` carries the numbers. */
  pace: string | null;
  /** The last thing that was refused, or null. */
  refused: string | null;
}

/** A left-to-right bar reading "how much of this allowance is still yours".
 *
 *  Depleting rather than filling, and that is the whole reason it is a bar and
 *  not a number: a limit is a thing you spend, and $400 of $1,200 left is
 *  legible as a third of a bar in a way it is not as a figure you have to
 *  divide. Amber under a third, red under a sixth. */
function Meter({
  label,
  left,
  total,
  title,
  /** A goal fills instead of depleting: it is the one bar here where a full one
   *  is the good outcome, so it must not read as red at the start of the day. */
  filling = false,
}: {
  label: string;
  left: number;
  total: number;
  title: string;
  filling?: boolean;
}) {
  const frac = total > 0 ? Math.max(0, Math.min(1, left / total)) : 0;
  const tone = filling
    ? frac >= 1 ? palette.green : palette.muted
    : frac <= 1 / 6 ? palette.red : frac <= 1 / 3 ? palette.orange : palette.green;
  return (
    <div style={{ flex: 1, minWidth: 0 }} title={title} data-meter={label}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: palette.muted }}>
        <span>{label}</span>
        <span style={{ fontFamily: "monospace", color: tone }}>{fmtUsd(left)}</span>
      </div>
      <div style={{ height: 4, marginTop: 2, background: palette.cardBorder, borderRadius: 2 }}>
        <div style={{ height: 4, width: `${frac * 100}%`, background: tone, borderRadius: 2 }} />
      </div>
    </div>
  );
}

export function GuardMeters({ feed }: { feed: GuardFeed }) {
  const g = feed.levels;
  const tone = feed.locked ? palette.red : feed.slow ? palette.orange : palette.muted;
  const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);
  const secs = (x: number | null) => (x == null ? "—" : `${Math.round(x)}s`);

  // What is left of the day. The adapter's figures when it has them — the prop
  // firm's own limit, counted against the tape day — and otherwise the personal
  // stop against this session's realised, which is what Live has. A green day
  // banks no extra room either way: the limit is a limit, not a budget that rolls.
  const dayLimit = feed.dayLimit ?? g.daily_loss_stop;
  const dayLeft =
    feed.dayLeft ?? Math.max(0, g.daily_loss_stop + Math.min(0, feed.realized));
  const room = feed.equity != null && feed.floor != null ? feed.equity - feed.floor : null;

  return (
    <div
      className="guard-meters"
      data-guard-meters
      style={{ borderBottom: `1px solid ${palette.cardBorder}`, paddingBottom: 8, marginBottom: 8 }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, fontSize: 12 }}>
        <span style={{ fontSize: 10, color: palette.muted, letterSpacing: 0.4 }}>DAY</span>
        <strong
          style={{
            color: feed.realized > 0 ? palette.green : feed.realized < 0 ? tone : palette.muted,
            fontFamily: "monospace",
          }}
        >
          {fmtUsd(feed.realized)}
        </strong>
        <span
          style={{ color: palette.muted, fontSize: 11 }}
          title={
            feed.legs != null && feed.legs > feed.trades
              ? `${feed.legs} closed lots out of ${feed.trades} positions — a scale-out, ` +
                `or a bracket that filled in parts, books a row each. The journal counts ` +
                `the ${feed.trades}.`
              : undefined
          }
        >
          {feed.trades} trade{feed.trades === 1 ? "" : "s"}
          {feed.legs != null && feed.legs > feed.trades && ` · ${feed.legs} legs`}
        </span>
        {/* Size against the cap, on the same row as the money it is risking.
            4 minis / 40 micros is the prop firm's own number, and it is the one
            rule here that is about the order rather than about the day. */}
        <span
          style={{ fontSize: 10, color: feed.size > feed.cap ? palette.red : palette.muted }}
          title={`Contracts on, against the ${feed.cap} the account allows.`}
          data-guard-cap
        >
          {feed.size}/{feed.cap}
        </span>
        <span
          style={{ marginLeft: "auto", fontSize: 10, color: feed.on ? palette.muted : palette.red }}
          title={`Stop ${fmtUsd(-g.daily_loss_stop)} · slow ${fmtUsd(-g.slow_down_at)} · target ≥${g.min_target_ticks}tk · stop ≤${g.stop_ticks_max}tk · risk ≤${fmtUsd(g.max_risk_usd)}${feed.on ? "" : " — measured, not enforced"}`}
        >
          {feed.on ? `stop ${fmtUsd(-g.daily_loss_stop)}` : "rules off"}
        </span>
      </div>

      {/* The two allowances, side by side, because they are two different
          clocks: the day resets tomorrow and the floor never does. Reading them
          together is the whole point — a day with $900 left on an account with
          $200 of room is not a day with $900 left. */}
      <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
        {dayLimit > 0 && (
          <Meter
            label="day"
            left={dayLeft}
            total={dayLimit}
            title={`Of the ${fmtUsd(dayLimit)} daily loss limit${
              feed.dayLimit != null
                ? ", counted against the day being replayed. Reaching it closes what is open and ends the day; the account survives it."
                : " you set. A green day banks no extra room."
            }`}
          />
        )}
        {/* The one bar that fills rather than depletes, because it is the one
            allowance you are trying to *spend*. Once it is full the goal is the
            day's floor and giving it back ends the day — which is the whole
            device: a good day you did not hand back. */}
        {feed.goal != null && feed.goal > 0 && (
          <Meter
            label={feed.goalArmed ? "goal ✓" : "goal"}
            left={feed.goal - Math.max(0, feed.goalLeft ?? feed.goal)}
            total={feed.goal}
            filling
            title={
              feed.goalArmed
                ? `The ${fmtUsd(feed.goal)} goal is made, and is now the day's floor. Giving any of it back closes what is open and ends the day.`
                : `${fmtUsd(feed.goalLeft ?? feed.goal)} of booked P&L to the day's ${fmtUsd(feed.goal)} goal. It arms on realised, never on an open runner — and once armed it becomes the day's floor.`
            }
          />
        )}
        {room != null && feed.floor != null && (
          <Meter
            label="floor"
            left={room}
            total={feed.floorTotal ?? room}
            title={`Equity ${fmtUsd(feed.equity ?? 0)} against a ${fmtUsd(feed.floor)} floor.${
              feed.trailing === "intraday"
                ? " It follows the running peak including your open position, so money you were up and gave back is room you do not get again."
                : " It only moves on a day close, so this number is constant for a whole sitting."
            }`}
          />
        )}
      </div>

      {!feed.on && (
        <div style={{ fontSize: 11, color: palette.red, marginTop: 4, lineHeight: 1.5 }}>
          <b>The guardrails are switched off.</b> Nothing here is enforced — the
          bracket rules, the daily stop and the auto-flatten are measured and
          reported, and no order is refused. The account's own floor is a separate
          matter and is never off.
        </div>
      )}

      {feed.locked ? (
        <div style={{ fontSize: 11, color: palette.red, marginTop: 4, lineHeight: 1.5 }}>
          <b>Day over</b> — {feed.locked}.{" "}
          {feed.on
            ? "New entries refused; closing out still works."
            : "Not enforced while the rules are off — this is what would have been refused."}
        </div>
      ) : feed.slow ? (
        <div style={{ fontSize: 11, color: palette.orange, marginTop: 4, lineHeight: 1.5 }}>
          <b>In the hole.</b> Past {fmtUsd(-g.slow_down_at)} your measured
          expectancy flips sign. A bad start slowed down on costs $147/day; the
          same start sped up on costs $803.
        </div>
      ) : null}

      {feed.pace && (
        <div
          data-pace
          style={{ fontSize: 11, color: palette.orange, marginTop: 4, lineHeight: 1.5 }}
        >
          <b>Coming in fast.</b> On the tape clock, {feed.pace}. Nothing is
          refused.
        </div>
      )}

      <RefusalFlash reason={feed.refused} />

      {/* Measured, never enforced. See `GuardFeed.fastShare`. */}
      {(feed.fastShare != null || feed.medianGapS != null || feed.tradedInTheHole != null) && (
        <div style={{ display: "flex", gap: 10, marginTop: 6, fontSize: 10, color: palette.muted }}>
          <span title="Trades that resolved inside 30 seconds. About half your real entries, winning 26% of the time — the one habit the audit found that actually costs money. An entry problem, so it is counted and never blocked.">
            &lt;30s{" "}
            <b style={{ color: (feed.fastShare ?? 0) > 0.5 ? palette.orange : palette.muted }}>
              {pct(feed.fastShare)}
            </b>
          </span>
          <span title="Median seconds between one entry and the next. Your green days ran 136s and your red days 76s — with the trade COUNT identical. Volume is not the problem; speed is.">
            gap{" "}
            <b style={{ color: (feed.medianGapS ?? 999) < 76 ? palette.orange : palette.muted }}>
              {secs(feed.medianGapS)}
            </b>
          </span>
          <span title="Was anything opened while the day was already past the slow-down level? A bad start slowed down on costs $147/day; the same start sped up on costs $803.">
            in the hole{" "}
            <b style={{ color: feed.tradedInTheHole ? palette.orange : palette.muted }}>
              {feed.tradedInTheHole == null ? "—" : feed.tradedInTheHole ? "yes" : "no"}
            </b>
          </span>
        </div>
      )}
    </div>
  );
}
