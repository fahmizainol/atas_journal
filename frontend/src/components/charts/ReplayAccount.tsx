// The replay account, on screen: the chip on the bar, the strip above the tape,
// and the row on the recap.
//
// Three renderings of one fact rather than one, because the account has to be
// legible at three different moments. The chip is glanceable while you trade and
// says only what is about to bind. The strip is unmissable and only appears when
// something *is* binding — a blown account, or the cause of death the last one
// left behind. The recap row is the reckoning, and it is the only one that shows
// the whole picture, because that is the moment to look at the whole picture.
//
// None of them enforces anything. That is `guardRules.accountStop` and
// `accountRefusal`, on the Simulator's own paths.

import { type AccountView } from "../../hooks/useReplayAccount";
import { fmtRecord } from "../../lib/replayAccount";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

export interface AccountProps {
  view: AccountView | undefined;
}

/** The bar chip: which account, its equity, and whatever is standing in the way.
 *
 *  Deliberately one line and mostly silent. What binds is rare, and a chip that
 *  always has something to say is one you stop reading — so the death and the
 *  review each appear only while they are real.
 *
 *  It is also the switch between the two accounts, because the switch belongs
 *  on the thing being switched: there is no second page and no menu, the name of
 *  the account you are on is the button that leaves it. `onSwitch` is null while
 *  a sitting is open — the mode is fixed when a sitting opens, so a switch you
 *  could flip mid-rep would be one that moved a finished sitting between two
 *  ledgers — and while a review is owed, which is the Simulator's call to make
 *  and its comment to explain. */
export function AccountChip({
  view,
  onSwitch,
}: AccountProps & { onSwitch?: (() => void) | null }) {
  if (!view) return null;

  const paper = view.account === "paper";
  const room = view.equity - view.floor;
  const record = fmtRecord(view.record);
  // What the chip says beyond the numbers. Only the first two are states the
  // account is *in*; the last is a reminder you left yourself, and it is last
  // because a flagged sitting is never the most important thing on the chip.
  const badge: { text: string; tone: string; hint?: string } | null =
    view.status === "blown"
      ? { text: "account blown", tone: palette.red }
      : view.status === "passed"
        ? // The other way a life ends, and the only one worth a green badge. It
          // is a state rather than a cheer: the epoch is over, so the next
          // sitting is the next eval and nothing is owed in between.
          {
            text: "target hit — passed",
            tone: palette.green,
            hint:
              `Cleared ${fmtUsd(view.passed?.target ?? 0)} on a settled balance. ` +
              "The eval is over — the next sitting opens a fresh account.",
          }
        : view.status === "can_reset"
        ? // Where every death lands once it has been paid for — instantly on
          // paper, and on the funded account the moment the cause is written.
          // It reads differently on paper because nothing was owed there: the
          // account is simply dead and the next sitting is the new one.
          { text: paper ? "blown — next resets" : "reset ready", tone: palette.orange }
        : view.review_flagged
          ? {
              text: `${view.review_flagged.count} flagged`,
              tone: palette.muted,
              hint:
                "Sittings you marked to review later. Nothing is waiting on them — " +
                "the history page is where you go back in.",
            }
          : null;

  return (
    <span
      className="chart-account-chip"
      data-account-status={view.status}
      data-account={view.account}
      title={
        `${paper ? "Paper" : "Funded"} replay account — epoch #${view.epoch.index + 1}, ${
          view.epoch.sittings
        } sitting${view.epoch.sittings === 1 ? "" : "s"}, ${fmtUsd(view.epoch.net)} net.\n` +
        `Trailing floor ${fmtUsd(view.floor)} — ${fmtUsd(room)} of room, and it only moves on a day close.\n` +
        `Day ${fmtUsd(view.day_net)}, ${fmtUsd(view.day_loss_remaining)} of the daily limit left.\n` +
        `${fmtUsd(view.target_remaining)} to target.` +
        (record ? `\nThis account's record: ${record}.` : "") +
        (paper ? "\nBreaching the floor costs nothing here — the next sitting is a fresh account." : "")
      }
    >
      {/* The account's name, and since the registry that is *all* it is.
          Switching used to be this button — it could only ever toggle between
          two — and now lives in `AccountSwitch` down in the dock, beside the
          meters whose numbers it decides. Kept as a button only while a caller
          still hands it something to do, so the two never both claim to be the
          switch: the harness looks the control up by `data-account-switch` and
          two of them is an ambiguity, not a redundancy. */}
      {onSwitch ? (
        <button
          type="button"
          data-account-switch
          onClick={() => onSwitch()}
          className="chart-account-name"
          style={{ color: paper ? palette.orange : palette.muted }}
          title="Switch account."
        >
          {view.label ?? (paper ? "PAPER" : "50K")}
        </button>
      ) : (
        <span
          data-account-name
          className="chart-account-name"
          style={{ color: paper ? palette.orange : palette.muted }}
          title="Which account this sitting is priced on. The switch is in the ticket, beside the meters it decides."
        >
          {view.label ?? (paper ? "PAPER" : "50K")}
        </span>
      )}
      <b style={{ fontFamily: "monospace", color: room <= 400 ? palette.orange : palette.text }}>
        {fmtUsd(view.equity)}
      </b>
      {badge && (
        <span data-account-block style={{ color: badge.tone }} title={badge.hint}>
          {badge.text}
        </span>
      )}
    </span>
  );
}

/**
 * The strip above the tape. Nothing at all most of the time.
 *
 * Two things earn a permanent band across the page. A **blown account**, because
 * nothing else on the page is worth reading until that is dealt with. And the
 * **cause of death of the account before this one**, for the whole of the epoch
 * that replaced it — that is what "pinned into the next epoch" means, and a
 * write-up you have to go and find is one written for nobody.
 *
 * A blown paper account still gets the band. There is nothing to deal with, but
 * "you are trading a corpse until you start the next sitting" is worth one line —
 * and a death that passed in silence would be a death you could stop noticing.
 *
 * A **passed** account gets one for the same reason and not as a reward: the
 * epoch is over either way, and trading on believing you are still in it is the
 * same mistake in the opposite direction.
 */
export function AccountNotice({ view }: AccountProps) {
  if (!view) return null;

  if (view.status === "passed") {
    const record = fmtRecord(view.record);
    return (
      <div className="sim-account-note passed" role="status">
        <b>Account passed.</b> Cleared {fmtUsd(view.passed?.target ?? 0)} and closed at{" "}
        {fmtUsd(view.equity)}. The next sitting opens a fresh account — nothing is owed
        in between, which is the whole difference between this ending and the other one.
        {record && <span style={{ color: palette.muted }}> This account: {record}.</span>}
      </div>
    );
  }

  if (view.status !== "live") {
    const d = view.last_death;
    const paper = view.account === "paper";
    return (
      <div className="sim-account-note dead" role="status">
        <b>{paper ? "Paper account blown." : "Account blown."}</b>{" "}
        {d && (
          <>
            Closed at {fmtUsd(d.equity)} against a {fmtUsd(d.floor)} floor.{" "}
          </>
        )}
        {paper
          ? "Nothing is owed — the next sitting opens a fresh $50,000. Which is the whole difference between this account and the one that counts."
          : view.status === "blown"
            ? "Write what killed it before anything else. One sentence is the whole of what this costs, and it is not skippable."
            : "Cause recorded. The next sitting opens a fresh account."}
      </div>
    );
  }

  // A live account carrying the last one's epitaph.
  const d = view.last_death;
  if (!d || !d.cause_of_death || d.epoch >= view.epoch.index) return null;
  return (
    <div className="sim-account-note pinned" role="note">
      <span style={{ color: palette.muted }}>The last account died of</span> {d.cause_of_death}
    </div>
  );
}

/** The recap row: the whole account, at the one moment there is time to read it.
 *
 *  Four numbers, and the pair of them that matters most is `equity` against
 *  `floor` — the drawdown that ends an account is fixed in dollars, so the
 *  distance to the floor is the only number here that is a countdown. */
export function AccountRecap({ view }: { view: AccountView | undefined }) {
  if (!view) return null;
  const room = view.equity - view.floor;
  const cell = (label: string, value: string, tone?: string, sub?: string) => (
    <div>
      <div style={{ color: palette.muted, fontSize: 11 }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 14, color: tone ?? palette.text }}>{value}</div>
      {sub && <div style={{ color: palette.muted, fontSize: 10, opacity: 0.8 }}>{sub}</div>}
    </div>
  );
  return (
    <div
      data-account-recap
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 1fr 1fr",
        gap: 8,
        marginTop: 10,
        paddingTop: 8,
        borderTop: `1px solid ${palette.cardBorder}`,
      }}
    >
      {cell(
        view.account === "paper" ? "Paper account" : "Account",
        fmtUsd(view.equity),
        view.epoch.net >= 0 ? palette.green : palette.red,
        `epoch #${view.epoch.index + 1} · ${view.epoch.sittings} sitting${view.epoch.sittings === 1 ? "" : "s"} · ${fmtUsd(view.epoch.net)}`,
      )}
      {cell(
        "To the floor",
        fmtUsd(room),
        room <= 400 ? palette.red : room <= 800 ? palette.orange : palette.text,
        `floor ${fmtUsd(view.floor)} · moves on a day close`,
      )}
      {cell(
        "Day left",
        fmtUsd(view.day_loss_remaining),
        view.day_loss_remaining <= 300 ? palette.orange : palette.text,
        `day ${fmtUsd(view.day_net)}`,
      )}
      {/* The eval's win condition, and underneath it how often it has been met.
          The record belongs *here* rather than beside the equity: it is the
          same question asked over the account's whole history, and the recap is
          the one moment there is time to read the whole history. */}
      {cell(
        "To target",
        fmtUsd(view.target_remaining),
        view.status === "passed" ? palette.green : palette.text,
        fmtRecord(view.record) || "no life finished yet",
      )}
    </div>
  );
}
