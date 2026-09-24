// What was traded, one row per closed portion — on both clocks.
//
// Replay and Live are one trading surface, and the blotter is the last panel
// that had not noticed. Live had a `3 closed · net $X` line and nothing under
// it; Replay had the card. Two cards would have been two sets of decisions
// about what a row shows and what net means, kept in step by hand, so there is
// one, fed by `simViews.blotterRow` and `brokerViews.blotterRows` — the same
// arrangement `posLine`/`positionLine` and `orderView`/`workingViews` already
// use for the chart's overlays.
//
// The header is the caller's, because it is the one part that genuinely differs:
// the replay has a sitting to end and a save that can fail, Live has an account
// id and a connection. The rows are not.

import type { ReactNode } from "react";
import { openStamps } from "../../lib/replaySim";
import { fmtR, fmtUsd, type BlotterRow } from "../../lib/simViews";
import { palette } from "../../theme";

/**
 * How many trades these rows are — **positions, not rows.**
 *
 * The list is one row per closed portion, and a position closed in two portions
 * is two rows and one trade. Live is where that bites: a Rithmic bracket is
 * attached per *partial fill*, so an ordinary entry-and-exit routinely books
 * two lots, and the page was reporting twelve trades on a day the journal
 * records eight of.
 *
 * Both numbers when they differ, one when they don't — because "8" over a list
 * of twelve rows reads like a panel that cannot count, and "12" is a number the
 * journal will disagree with. The count itself is `replaySim.openStamps`, which
 * is the same grouping `build_logical_trades` walks the ledger into.
 */
export function TradeTally({ rows, noun = "trades" }: { rows: BlotterRow[]; noun?: string }) {
  const n = openStamps(rows).length;
  return (
    <>
      {n} {noun}
      {rows.length > n && (
        <span
          style={{ color: palette.muted }}
          title={
            `${rows.length} closed lots out of ${n} position${n === 1 ? "" : "s"} — ` +
            `a scale-out, or a bracket that filled in parts, books a row each. ` +
            `The journal counts the ${n}.`
          }
        >
          {" "}
          · {rows.length} legs
        </span>
      )}
    </>
  );
}

/** Both R's spelled out, for the row that only has room to show one. */
function rTitle(t: BlotterRow): string {
  if (t.rCash == null && t.r == null) {
    return "No stop was on when this opened, so there is no risk to measure against";
  }
  return (
    `stake ${fmtR(t.rCash)} (of the money risked at open) · ` +
    `excursion ${fmtR(t.r)} (of the distance risked at open)`
  );
}

/** What the entry put up, and where the number came from.
 *
 *  The order pad's sizer quotes this before the order goes — "risks $250 if the
 *  stop is hit" — and until now it was quoted and then forgotten. Printing it
 *  beside what the trade paid is what turns a blotter into a record of the bets
 *  rather than only of the outcomes: two −$180 rows are the same loss and not
 *  the same mistake if one of them staked $250 and the other $900.
 *
 *  **The position's stake, repeated on every scale-out of it**, on exactly the
 *  terms `rCash` divides by it — which is why the column is never totalled. */
function RiskCell({ t }: { t: BlotterRow }) {
  if (t.riskUsd == null) {
    return (
      <span
        style={{ color: palette.muted, opacity: 0.5 }}
        title="Opened with no stop, so nothing was ever at a defined risk"
      >
        —
      </span>
    );
  }
  return (
    <span
      style={{ color: palette.muted }}
      title={
        `Risked ${fmtUsd(t.riskUsd)} — the stop distance in money at the size ` +
        `this opened with, which is the figure the ticket's sizer quoted. ` +
        `Every scale-out of one entry repeats it, so it does not add up down ` +
        `the column.`
      }
    >
      ⌀{fmtUsd(t.riskUsd)}
    </span>
  );
}

/**
 * The card. `head` is everything after the title in the section header —
 * counts, buttons, warnings — and `total` is the figure that sits at its right.
 *
 * Rows come newest first. The list scrolls; the header does not.
 *
 * **A record and nothing else.** The ticket card — the bracket presets and the
 * risk sizer — sat on top of this for a day and moved to the ticket panel, where
 * the fields it fills actually are. What it left behind is the pairing worth
 * keeping in mind rather than in the DOM: `⌀$` per row is what each entry
 * staked, and the card on the ticket is that same question asked forwards.
 */
export function Blotter({
  rows,
  head,
  total,
  empty = "No trades yet.",
}: {
  rows: BlotterRow[];
  head?: ReactNode;
  total: number;
  empty?: string;
}) {
  return (
    <div className="sim-card sim-blotter">
      <div className="sim-sec-t" style={{ flex: "none" }}>
        Blotter
        {head}
        <span
          style={{
            fontFamily: "monospace",
            fontWeight: 700,
            marginLeft: "auto",
            color: total >= 0 ? palette.green : palette.red,
          }}
        >
          {fmtUsd(total)}
        </span>
      </div>
      {/* The layout is the stylesheet's, not inline — a rule cannot raise a floor
          that an inline `minHeight: 0` has already put on the floor. */}
      <div className="sim-blotter-list">
        {rows.length === 0 && <div style={{ color: palette.muted }}>{empty}</div>}
        {rows
          .slice()
          .reverse()
          .map((t) => (
            <div
              key={t.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 6,
                padding: "4px 0",
                borderBottom: `1px solid ${palette.cardBorder}`,
              }}
            >
              <span style={{ color: t.side === "long" ? palette.green : palette.red }}>
                {t.side === "long" ? "L" : "S"}×{t.size}
                {/* Named only when this row is not in the contract orders go to
                    now — the only time the head of the panel doesn't already
                    say it, and the only time two rows of "×1" mean different
                    money. The mapper decides; see `BlotterRow.contract`. */}
                {t.contract && (
                  <span
                    style={{ color: palette.muted, fontSize: 10, marginLeft: 4 }}
                    title={`Traded as ${t.contract} — the contract the order was sent to, not the one selected now.`}
                  >
                    {t.contract}
                  </span>
                )}
              </span>
              <span style={{ color: palette.muted }}>
                {!t.openType || t.openType === "market"
                  ? ""
                  : `${t.openType === "stop" ? "stp" : "lmt"}→`}
                {t.reason}
              </span>
              <RiskCell t={t} />
              {/* Stake R leads — it is the one that says what the account did.
                  Excursion R only earns its own column when the two disagree,
                  which is to say when size changed mid-trade; on an ordinary
                  one-clip trade they are the same number and printing it twice
                  would just be noise. */}
              <span style={{ color: palette.muted }} title={rTitle(t)}>
                {fmtR(t.rCash)}
                {t.r != null && t.rCash != null && Math.abs(t.r - t.rCash) > 0.005 && (
                  <span style={{ opacity: 0.6 }}> · {fmtR(t.r)}e</span>
                )}
              </span>
              {/* Net — the gross and the fee are in the tooltip, where the
                  difference is worth seeing without the column carrying it. */}
              <span
                style={{
                  fontFamily: "monospace",
                  color: t.pnl >= 0 ? palette.green : palette.red,
                }}
                title={
                  t.fees
                    ? `${fmtUsd(t.pnl + t.fees)} gross − ${fmtUsd(t.fees)} commission · in ${t.entryPrice} out ${t.exitPrice}`
                    : `in ${t.entryPrice} out ${t.exitPrice}`
                }
              >
                {fmtUsd(t.pnl)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}
