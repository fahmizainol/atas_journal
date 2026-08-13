// The replay account, as the browser reads it. Mirrors `journal.replay_account`.
//
// Everything here is derived on the server from the attempts on disk — there is
// no balance to keep in sync and nothing to post back. What this file adds is
// the one thing the server cannot do: count down.
//
// **Every deadline is measured against the view's own `now`, never against the
// browser's.** A machine clock four minutes fast would show a gate as open that
// the server is about to refuse, which is the most annoying possible way for a
// discipline feature to be wrong. So the view carries the server's clock, we
// note the local time we received it, and every countdown is
// `deadline − (server now + how long we have been holding it)`.
//
// A lib rather than a corner of the hook, because `guardRules` reads these
// types and a rule module has no business importing a hook.

/** One thing a finished sitting has to answer for. Raised server-side — see
 *  `replay_account.flags_for` for why it cannot be the browser's job. */
export interface ReplayFlag {
  kind: "trade" | "rewind";
  trade_id: number | null;
  /** Tape wall clock, so it can be seeked to. Never account time — the two
   *  timestamp families are documented in `journal.replay_account`. */
  ms: number;
  pnl: number;
  label: string;
  reasons: string[];
}

export type AccountStatus = "live" | "blown" | "cooldown" | "can_reset";

export interface AccountView {
  /** The server's clock at the moment this was built. Countdowns run off it. */
  now: string;
  equity: number;
  /** The end-of-day trailing floor. Constant for a whole sitting, which is what
   *  makes it safe to compare a live equity against in the browser. */
  floor: number;
  peak_close: number;
  status: AccountStatus;
  day_net: number;
  day_loss_remaining: number;
  target_remaining: number;
  /** When the next sitting may open, or null if it may open now. */
  next_sitting_at: string | null;
  cooldown_until: string | null;
  can_reset: boolean;
  review_block: { attempt_id: string; flags: ReplayFlag[] } | null;
  epoch: { index: number; started_at: string; sittings: number; net: number };
  last_death: {
    at: string;
    attempt_id: string;
    equity: number;
    floor: number;
    epoch: number;
    cause_of_death: string | null;
  } | null;
  caps: { minis: number; micros: number };
}

/** Milliseconds left until `deadline`, on the server's clock rather than this
 *  machine's. `receivedAt` is a `Date.now()` from when the view arrived. */
export function remainingMs(
  view: AccountView | undefined,
  deadline: string | null | undefined,
  receivedAt: number,
): number {
  if (!view || !deadline) return 0;
  const held = Date.now() - receivedAt;
  const serverNow = Date.parse(view.now) + held;
  return Math.max(0, Date.parse(deadline) - serverNow);
}

/** A wait, in the coarsest unit that still says something useful. Hours when
 *  there are hours left (nobody counts out a 24h timeout by the second),
 *  minutes and seconds when it is close enough to sit through.
 *
 *  Not `simViews.fmtCountdown`, which is the bar-close clock and reads m:ss —
 *  "1440:00" is not a way to say a day. */
export function fmtWait(ms: number): string {
  const s = Math.ceil(ms / 1000);
  if (s >= 3600) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s - h * 3600) / 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  if (s >= 60) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
}
