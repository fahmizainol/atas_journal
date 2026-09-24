// The replay account, as the browser reads it. Mirrors `journal.replay_account`.
//
// Everything here is derived on the server from the attempts on disk — there is
// no balance to keep in sync and nothing to post back.
//
// This file used to carry the one thing the server could not do — count down —
// along with the discipline that made a countdown safe (every deadline measured
// against the view's own `now` plus how long we had been holding it, never
// against a machine clock that might be four minutes fast). Both timed gates are
// gone as of 2026-08-24 and the arithmetic went with them: nothing about this
// account is answered by waiting any more, so nothing here has a deadline to
// subtract. `liveEquity` is what is left, and it was always the more important
// half.
//
// A lib rather than a corner of the hook, because `guardRules` reads these
// types and a rule module has no business importing a hook.

/** One account rule a finished sitting tripped. Raised server-side — see
 *  `replay_account.flags_for` for why it cannot be the browser's job. It is a
 *  record rather than a question: the leak/justified verdict that flags used to
 *  owe was retired 2026-08-20 (`docs/trade-grading-plan.md` G6), and the review
 *  is the per-trade answers. */
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

/** Where the account's current life stands.
 *
 *  A life ends two ways. `blown` is the floor catching it with the write-up
 *  still owed, `can_reset` is that write-up paid (and every paper death, which
 *  owes none), and `passed` is the other end entirely — the profit target
 *  cleared on a settled balance. All three of the last two are replaceable:
 *  read `can_reset` for that rather than comparing the status, since a pass and
 *  a paid-for death both open a fresh account on the next sitting. */
export type AccountStatus = "live" | "blown" | "can_reset" | "passed";

/** Which account this is. An **open** set since accounts became a registry —
 *  `funded` and `paper` are the two built-ins, and any number of others can be
 *  made from a template. Every account is a separate walk over the sittings
 *  stamped with its id; none of them can move another. */
export type AccountKey = string;

/** How the floor follows the equity, straight off the account's template.
 *
 *  `eod` is the LucidPro rule: the peak is taken at day closes, so the floor is
 *  a constant for a whole sitting and the browser can be handed one number.
 *  `intraday` is the LucidDaily rule: the peak follows running equity including
 *  an open position, so the floor moves under the position moving it and the
 *  browser has to keep re-deriving it. Read from the view, **never inferred
 *  from the account's name** — see `AccountView.rules`. */
export type Trailing = "eod" | "intraday";

/** The sitting mode. Two accounts predate the registry and are still selectable
 *  by the mode they priced (`replay` → funded, `paper` → paper), which is what
 *  every sitting already on disk and every browser check speaks. New accounts
 *  are named by id. A drill is neither: it has no account, and asks for none. */
export type AccountMode = "replay" | "paper";

/** Which sittings an account is the account of, for the places holding a view
 *  rather than a page. Only meaningful for the two built-ins — anything else
 *  opens under `replay` and is told apart by its id. */
export const modeOfAccount = (key: AccountKey): AccountMode =>
  key === "paper" ? "paper" : "replay";

/** Which account priced a sitting. Mirrors `replays.account_id_of`, fallback
 *  and all: a sitting written before the field existed was priced by its mode,
 *  and a drill was priced by nobody. Kept in step with the server because the
 *  history page groups by it and would otherwise show one account's sittings
 *  under another's epoch. */
export function accountIdOf(row: {
  account_id?: string | null;
  mode?: string | null;
}): AccountKey | null {
  if ((row.mode ?? "replay") === "drill") return null;
  const stamped = (row.account_id ?? "").trim();
  if (stamped) return stamped;
  return row.mode === "paper" ? "paper" : "funded";
}

export interface AccountView {
  /** The server's clock at the moment this was built — which sittings are in
   *  the epoch and which day `day_net` covers were decided against it. */
  now: string;
  account: AccountKey;
  /** The tape day the day figures below are counted against — the one being
   *  replayed, echoed back. Null when no day is open (the history page), in
   *  which case every day has closed and no allowance is being spent. The
   *  account's day is a day of the *market*, never of the evening you replayed
   *  it in; `journal.replay_account.tape_day` documents what that cost when it
   *  was the other way round. */
  day: string | null;
  /** The account's own name, as shown in the switcher. */
  label: string;
  /** **The rule shape and this account's figures, said outright.**
   *
   *  The client must know whether `floor` is a constant for the sitting or a
   *  number it has to keep re-deriving, and the one thing it must never do is
   *  work that out from the account's name. Same argument as `account` itself:
   *  a payload that cannot name its own rules is a floor enforced under the
   *  wrong ones. The figures ride along because the meters need totals as well
   *  as remainders — `max_loss` is the floor meter's full bar. */
  rules: {
    template: string;
    template_label: string;
    trailing: Trailing;
    /** Whether a sitting on this account must run at the speed the day ran at
     *  — no ladder, no scrub, no step, no held-Ctrl turbo. True on every
     *  template that prices a real product (LucidPro, LucidDaily) and false on
     *  Paper, which is the practice surface.
     *
     *  Read from here and **never from the account's id**. It was an
     *  `acctId === "funded"` test on the page until 2026-09-10, which is why
     *  every LucidDaily account made from the registry could be fast-forwarded
     *  through the rep it exists to make you sit through. */
    real_time: boolean;
    start: number;
    max_loss: number;
    trail_cap: number;
    day_loss: number;
    day_goal: number | null;
    profit_target: number;
    max_minis: number;
    max_micros: number;
  };
  equity: number;
  /** The end-of-day trailing floor. Constant for a whole sitting, which is what
   *  makes it safe to compare a live equity against in the browser. */
  floor: number;
  peak_close: number;
  status: AccountStatus;
  day_net: number;
  day_loss_remaining: number;
  target_remaining: number;
  /** The attempt ids `equity` is the sum of. A sitting in this list is already
   *  priced; adding its running P&L to `equity` counts it twice. See
   *  `replay_account.derive` for why the page cannot infer this from its own
   *  state — the server settles abandoned sittings without telling it. */
  counted_ids: string[];
  can_reset: boolean;
  /** Sittings you marked to review later — the oldest of them, and how many
   *  there are. A reminder and never a refusal: it replaced `review_block` on
   *  2026-08-25, when reviewing stopped being mandatory. */
  review_flagged: { attempt_id: string; flags: ReplayFlag[]; count: number } | null;
  epoch: { index: number; started_at: string; sittings: number; net: number };
  last_death: {
    at: string;
    attempt_id: string;
    equity: number;
    floor: number;
    epoch: number;
    cause_of_death: string | null;
  } | null;
  /** How the life in hand was won, or null while it is still being traded.
   *
   *  Unlike `last_death` this is about *this* epoch only. A death is written up
   *  to be read through the next account, so it has to outlive its own epoch; a
   *  pass leaves nothing pinned to its successor, and what carries it forward is
   *  `record`. */
  passed: { at: string; attempt_id: string; equity: number; target: number } | null;
  /** **The tracker**: how many of this account's lives ended each way.
   *
   *  Derived from the walks like everything else here and never counted up on
   *  disk, so a deleted sitting or an edited limit re-answers it rather than
   *  leaving it claiming an eval the trades no longer support. The life in hand
   *  is included the moment it ends — a tally that waited for the replacement
   *  would sit one behind at exactly the moment you looked at it. */
  record: { passed: number; blown: number };
  caps: { minis: number; micros: number };
}

/** The account's equity *including the sitting on screen* — the one number the
 *  floor is worth comparing against, and the only place it is worked out.
 *
 *  `view.equity` is the sum of the sittings in `counted_ids`. A sitting still
 *  being traded is not among them, so its running P&L has to be added; a
 *  sitting that has settled *is* among them, and adding it again subtracts its
 *  own P&L from the room a second time. That second case is not hypothetical
 *  and it is not rare: it happens the instant a sitting ends, and again to any
 *  tab left open for an hour, because the server settles a stale `active`
 *  attempt without telling the page (`replay_account.sweep_stale`).
 *
 *  `attemptId` is the sitting on screen, or null when nothing has been recorded
 *  yet — an untracked sitting cannot have been counted, so its P&L is added.
 *
 *  Returns null when there is no account, which is every drill: the caller
 *  passes that straight through to a meter that declines to draw and a stop
 *  that declines to fire. */
/** The record in words — one phrasing, everywhere it is shown.
 *
 *  The chip, the recap and the account switcher all draw the same two numbers,
 *  and the same two numbers under two different names read as two different
 *  facts. Empty string for an account that has never finished a life, so the
 *  callers can leave the line out rather than print a row of zeroes. */
export function fmtRecord(record: { passed: number; blown: number }): string {
  if (!record.passed && !record.blown) return "";
  return `${record.passed} passed · ${record.blown} blown`;
}

export function liveEquity(
  view: AccountView | undefined,
  attemptId: string | null,
  realized: number,
  openPnl: number,
): number | null {
  return liveAccount(view, attemptId, realized, openPnl, 0)?.equity ?? null;
}

/** The account as it stands with the sitting on screen counted — **both**
 *  numbers the floor rule needs, and the only place either is worked out.
 *
 *  It returns a pair rather than a figure because on an intraday-trailing
 *  template the floor is a live number too, and it has the *same* dependency
 *  the equity has: a sitting in `counted_ids` is already inside `view.equity`
 *  **and** inside `view.peak_close`, so adding its excursion on top would raise
 *  the floor twice over the same trades. Splitting the two across two calls
 *  would let a caller pair a live equity with a floor from before the sitting —
 *  which is the drawdown, charged once and refunded once.
 *
 *  `peakUsd` is the sitting's own high-water in dollars from where it opened,
 *  off the simulation fold (`replaySim.SimState.peakUsd`) and never off the HUD
 *  sample: which animation frames landed must not move an account's floor. The
 *  asymmetry with `openPnl` — a fold against a sample — is safe precisely
 *  because the peak is monotone within a sitting and the equity is not.
 *
 *  On an `eod` template it hands back `view.floor` untouched, so LucidPro
 *  behaves exactly as it did.
 *
 *  Returns null when there is no account, which is every drill: the caller
 *  passes that straight through to a meter that declines to draw and a stop
 *  that declines to fire. */
export interface LiveAccount {
  equity: number;
  /** The floor as it stands *now*: a constant for the sitting on `eod`, and a
   *  moving number on `intraday`. */
  floor: number;
  /** What is left before the floor. Negative is a breach. */
  room: number;
  /** The tape day's **booked** P&L, this sitting included. What arms the day
   *  goal — a goal reached on an open runner has not been reached. */
  dayRealized: number;
  /** The day's P&L counting the open position. What the day's limits are
   *  *enforced* on: a position that would take the day through a limit is
   *  closed, rather than the limit being noticed after it books. */
  dayTotal: number;
}

export function liveAccount(
  view: AccountView | undefined,
  attemptId: string | null,
  realized: number,
  openPnl: number,
  peakUsd: number,
): LiveAccount | null {
  if (!view) return null;
  // Already priced: its P&L is inside `equity`, inside `peak_close` and inside
  // `day_net`, and adding any of it again charges the same trades twice.
  const counted = !!attemptId && view.counted_ids.includes(attemptId);
  const mine = counted ? 0 : realized;
  const open = counted ? 0 : openPnl;
  const equity = view.equity + mine + open;
  const floor =
    !counted && view.rules.trailing === "intraday"
      ? Math.min(view.peak_close + Math.max(0, peakUsd), view.rules.trail_cap) -
        view.rules.max_loss
      : view.floor;
  return {
    equity,
    floor,
    room: equity - floor,
    dayRealized: view.day_net + mine,
    dayTotal: view.day_net + mine + open,
  };
}
