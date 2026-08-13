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

import { useEffect, useState } from "react";
import { fmtWait, remainingMs, type AccountView } from "../../hooks/useReplayAccount";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

/** Re-render once a second so a countdown counts.
 *
 *  A local ticker rather than a refetch: the deadline is already known, and
 *  polling the server every second to watch a clock run down would be a request
 *  per second for a number arithmetic already has. */
function useTick(on: boolean): void {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => bump((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [on]);
}

export interface AccountProps {
  view: AccountView | undefined;
  /** `Date.now()` when the view arrived — see `remainingMs` on why the browser
   *  clock is only ever used for elapsed time and never for a deadline. */
  receivedAt: number;
}

/** The bar chip: equity, and whatever is currently standing in the way.
 *
 *  Deliberately one line and mostly silent. What binds is rare, and a chip that
 *  always has something to say is one you stop reading — so the wait, the
 *  cooldown and the review each appear only while they are real. */
export function AccountChip({ view, receivedAt }: AccountProps) {
  const wait = remainingMs(view, view?.next_sitting_at, receivedAt);
  const cool = remainingMs(view, view?.cooldown_until, receivedAt);
  useTick(!!view && (wait > 0 || (view.status === "cooldown" && cool > 0)));
  if (!view) return null;

  const room = view.equity - view.floor;
  const blocked =
    view.status === "blown"
      ? { text: "account blown", tone: palette.red }
      : view.status === "cooldown" && cool > 0
        ? { text: `cooldown ${fmtWait(cool)}`, tone: palette.red }
        : view.status === "can_reset"
          ? { text: "reset ready", tone: palette.orange }
          : view.review_block
            ? { text: "review owed", tone: palette.orange }
            : wait > 0
              ? { text: `next in ${fmtWait(wait)}`, tone: palette.orange }
              : null;

  return (
    <span
      className="chart-account-chip"
      data-account-status={view.status}
      title={
        `Replay account — epoch #${view.epoch.index + 1}, ${view.epoch.sittings} sitting${
          view.epoch.sittings === 1 ? "" : "s"
        }, ${fmtUsd(view.epoch.net)} net.\n` +
        `Trailing floor ${fmtUsd(view.floor)} — ${fmtUsd(room)} of room, and it only moves on a day close.\n` +
        `Day ${fmtUsd(view.day_net)}, ${fmtUsd(view.day_loss_remaining)} of the daily limit left.\n` +
        `${fmtUsd(view.target_remaining)} to target.`
      }
    >
      <b style={{ fontFamily: "monospace", color: room <= 400 ? palette.orange : palette.text }}>
        {fmtUsd(view.equity)}
      </b>
      {blocked && (
        <span data-account-block style={{ color: blocked.tone }}>
          {blocked.text}
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
 */
export function AccountNotice({ view, receivedAt }: AccountProps) {
  const cool = remainingMs(view, view?.cooldown_until, receivedAt);
  useTick(!!view && cool > 0);
  if (!view) return null;

  if (view.status !== "live") {
    const d = view.last_death;
    return (
      <div className="sim-account-note dead" role="status">
        <b>Account blown.</b>{" "}
        {d && (
          <>
            Closed at {fmtUsd(d.equity)} against a {fmtUsd(d.floor)} floor.{" "}
          </>
        )}
        {view.status === "blown"
          ? "Write what killed it before the clock starts counting — the timeout runs from the death either way, so this costs nothing but is not skippable."
          : view.status === "cooldown"
            ? `Cause recorded. ${fmtWait(cool)} left before a new account can be opened.`
            : "The timeout is up. The next sitting opens a fresh account."}
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
        "Account",
        fmtUsd(view.equity),
        view.epoch.net >= 0 ? palette.green : palette.red,
        `epoch #${view.epoch.index + 1} · ${view.epoch.sittings} sitting${view.epoch.sittings === 1 ? "" : "s"}`,
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
      {cell("To target", fmtUsd(view.target_remaining), palette.text, `epoch ${fmtUsd(view.epoch.net)}`)}
    </div>
  );
}
