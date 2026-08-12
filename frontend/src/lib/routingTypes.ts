// What the /live/routing endpoints say. Mirrors api/routers/live_orders.py.
//
// This is the only part of the client that describes real orders. Everything
// else on the Live page — the blotter, the position line, the working-order
// primitives — is a fold over the tape (`lib/replaySim`), re-derivable and
// owned by this process. Nothing here is: every field below is the broker's
// word, and the client's job is to show it rather than to keep it.
//
// Which is why `reconciled_at` is nullable and why that matters more than it
// looks. `null` means "we have not asked" — not "nothing is working". A panel
// that rendered the two the same way would show an empty order list to somebody
// who has a live position on, which is the single worst thing this surface
// could do.

/** The reserved id of the account that cannot trade. */
export const PAPER = "paper";

/** One row of the account selector. Paper is one of these, deliberately — the
 *  design is one selector and one mental model, with the thing that cannot
 *  reach a broker sitting first in it. */
export interface BrokerAccount {
  id: string;
  /** "paper", "demo", "live", or **null** for an account nobody has labelled.
   *  Null is not a loading state: nothing Rithmic sends says whether an account
   *  is funded, so an untagged account stays untagged until a person says, and
   *  cannot send an order in the meantime. */
  kind: string | null;
  /** This account skips the confirm popup. Always true for paper. */
  one_click: boolean;
  label: string;
  tagged: boolean;
}

/** What the broker last said. `null` on `RoutingStatus.broker` when the running
 *  session is not a routing session, or when nothing is running at all. */
export interface BrokerState {
  attached: boolean;
  /** Connected to a *real* account whose state has been read back. False on
   *  paper — paper has no broker state to be ready about. */
  ready: boolean;
  /** The active account. `"paper"` at the start of every session, including
   *  after a restart or the 18:00 roll. */
  account_id: string;
  paper: boolean;
  /** The active account's label, or null while it is untagged. */
  kind: string | null;
  /** Whether the active account skips the confirm popup. */
  one_click: boolean;
  accounts: BrokerAccount[];
  max_qty: number;
  /** The contract orders go to — which is not necessarily the one on screen.
   *  See `instruments`. */
  symbol: string;
  exchange: string;
  /** What routing may be pointed at: the feed's contract, and its micro when
   *  the login has one. The tape does not follow a switch — one login is one
   *  socket and the subscription was made at connect — so this is a list of
   *  things you can *send to* while watching the one thing you are watching. */
  instruments: string[];
  /** The contract the tape is actually on. Equal to `symbol` until routing is
   *  pointed elsewhere, and the pair is what the panel draws when they differ. */
  feed_symbol: string;
  /** Both follow `symbol`, so the panel's risk arithmetic is read rather than
   *  assumed: the same 50 ticks is $250 of NQ and $25 of MNQ. */
  tick_size: number;
  point_value: number;
  /** The configured rate scaled to the routed contract — a micro round turn is
   *  not charged at the mini rate the setting was measured at. */
  commission_per_side: number;
  /** Epoch seconds of the last reconciliation, or null for "never asked". */
  reconciled_at: number | null;
  /** Will a gesture actually reach the exchange? The server's own answer, not a
   *  re-derivation: routing is switched on, this is a real account, a person has
   *  labelled it, and the broker has been read back. False means the order path
   *  refuses, and the chart draws its "this is live" outline from exactly this. */
  routable: boolean;
  working: BrokerOrder[];
  recent: BrokerOrder[];
  trades: BrokerTrade[];
  position: BrokerPosition | null;
  /** The discipline layer's state for today. Always present — including when
   *  the layer is switched off, which is precisely when it has to be drawn. */
  guard: GuardState;
  error: string | null;
}

/** The guardrail levels. **Zero disables that one rule**, everywhere.
 *
 *  Fitted to this trader's own book (docs/research/lucidpro-operating-plan.md),
 *  not to generic prop advice — which is why the panel shows the number beside
 *  every rule rather than just its name. */
export interface GuardLevels {
  /** Realised dollars down at which the day is over. Latching. */
  daily_loss_stop: number;
  /** Realised dollars up at which the day is over. 0 in evaluation. */
  daily_profit_lock: number;
  /** Dollars down at which entries have to slow to `min_gap_s` apart. */
  slow_down_at: number;
  min_gap_s: number;
  min_target_ticks: number;
  stop_ticks_min: number;
  stop_ticks_max: number;
  require_bracket: boolean;
  /** Close what is open when the day crosses `daily_loss_stop`, rather than
   *  only refusing the next entry. The stop is measured on **equity** —
   *  realised plus the open position — because the account's own drawdown does
   *  not wait for a loss to be booked. */
  auto_flatten: boolean;
  /** The most one entry may risk: stop x size x the contract's dollars-per-tick.
   *  The rule `max_qty` cannot be — 5 on a 50-tick stop is $125 of micros or
   *  $1,250 of minis, and the order goes out on whatever the chart is on. */
  max_risk_usd: number;
  commission_per_side: number;
}

/** What the rules currently say about today. */
export interface GuardState {
  /** `LIVE_GUARDRAILS` is not switched off. Defaults to **true** — the opposite
   *  polarity to `RoutingStatus.enabled`, deliberately: the safe default for a
   *  permission is "denied" and for a restraint is "enforced". */
  on: boolean;
  /** Realised dollars today, net of commission, as the server paired them.
   *  This is what the rules are enforced on. */
  realized: number;
  trades: number;
  /** How many of those trades came back off the journal instead of out of a
   *  fill this server watched — non-zero after a restart, which is exactly when
   *  the day used to come back at zero and hand the loss stop a fresh $500. The
   *  rebuilt figure is the trustworthy one; this says where it came from. */
  restored: number;
  /** Why the day is over, or null. Latched: a later winner does not clear it. */
  locked: string | null;
  /** Past the slow-down threshold but not yet stopped. */
  slow: boolean;
  since_entry_s: number | null;
  /** What the broker's PnL plant says the account did today. Shown beside
   *  `realized` rather than instead of it — they measure different things and a
   *  gap between them is worth seeing. */
  /** Realised plus what the open position is currently down. What the daily
   *  stop actually fires on. */
  equity: number;
  open_pnl: number | null;
  /** The automatic flatten has already fired today. Latches until the roll. */
  auto_flattened: boolean;
  broker_day_pnl: number | null;
  divergence: number | null;
  levels: GuardLevels;
}

export interface BrokerOrder {
  basket_id: string;
  user_tag: string;
  symbol: string;
  account_id: string;
  side: string;
  type: string;
  qty: number;
  price: number | null;
  trigger_price: number | null;
  filled: number;
  unfilled: number;
  avg_fill_price: number | null;
  /** The bracket this order was sent with, in ticks from the fill — 0 when it
   *  carries none, or when this process did not send it. Rithmic attaches the
   *  legs on the fill and says nothing about them before it, so this is the
   *  server's own memory of the request rather than the broker's word. */
  stop_ticks: number;
  target_ticks: number;
  /** Non-zero on a stop leg **Rithmic is trailing**: the distance it rides
   *  behind the extreme. It re-derives that stop on every new tick of profit,
   *  so this leg is the server's to move and not ours — a drag on it is refused
   *  here and at the broker, because one that went through would be walked back
   *  out to a wider stop than the chart was showing. */
  trail_by_ticks: number;
  status: string;
  notify: number;
  text: string;
  working: boolean;
  at: number;
}

/** A round trip, paired out of the broker's fill stream.
 *
 *  The broker never sends this — a fill stream reports executions, not trades —
 *  so the server folds one using the same netting rules `replaySim` uses for
 *  paper. That parity is the point: a paper trade and a real one on the same
 *  chart have to mean the same thing. */
export interface BrokerTrade {
  id: number;
  side: "long" | "short";
  size: number;
  entry_price: number;
  entry_ms: number;
  exit_price: number;
  exit_ms: number;
  pts: number;
  pnl: number;
  /** Stake R against the stop the position opened with. **Null** when it
   *  carried none — there was no risk to divide by. */
  r: number | null;
  reason: string;
}

export interface BrokerPosition {
  symbol: string;
  /** Signed: positive long, negative short. Read off the PnL plant's
   *  `net_quantity` — never derived from fills this process happened to see. */
  net: number;
  avg_price: number | null;
  open_pnl: number | null;
  day_pnl: number | null;
  /** Epoch ms of when this position came off flat, where the process saw it
   *  happen. **Null** when it did not — a process that attached to an already
   *  open position never sees the transition, and the PnL plant reports a state
   *  rather than an event. The chart falls back rather than inventing a bar. */
  opened_ms: number | null;
  at: number;
}

export interface RoutingStatus {
  /** LIVE_ROUTING is set — the one env var left, and the deployment-level
   *  "this machine must never trade". False means no amount of clicking helps. */
  enabled: boolean;
  max_qty: number;
  /** `LIVE_GUARDRAILS` is not switched off. Readable with no session running,
   *  like `enabled` — "are the rules on" is a property of the deployment. */
  guardrails: boolean;
  guards: GuardLevels;
  /** Why routing is unavailable, in words, or null if it is available. */
  refusal: string | null;
  session: boolean;
  /** A session is running *and* it opened the ORDER plant. A session running
   *  without one is the ordinary case and a different thing from routing being
   *  unavailable. */
  routing_session: boolean;
  broker: BrokerState | null;
}

/** A reviewed order, waiting to be sent. The token is the only handle on it:
 *  `/send` takes nothing else, so an order that was never rendered as a
 *  sentence cannot be sent. */
export interface OrderPreview {
  token: string;
  expires_in_s: number;
  /** The order in English, including the account and the environment. This is
   *  the confirm step's entire content — not a summary of it. */
  sentence: string;
  intent: {
    side: string;
    qty: number;
    type: string;
    price: number | null;
    stop_ticks: number;
    target_ticks: number;
    symbol: string;
    exchange: string;
    account_id: string;
  };
}

export interface OrderSent {
  tag: string;
  basket_id: string;
  /** Which door it went through — "review" or "one_click". Recorded in the
   *  order journal too: "was this one-click" is the first question anyone asks
   *  about a fill they did not expect. */
  how: string;
  sentence: string;
}

/** An order as the page builds it, before it is either reviewed or fired. */
export interface OrderDraft {
  side: "buy" | "sell";
  qty: number;
  type: "market" | "limit" | "stop";
  price: number | null;
  stop_ticks: number;
  target_ticks: number;
  /** Ticks of profit before Rithmic starts ratcheting the stop up behind the
   *  high. 0 is off, and there is deliberately no second number: the trail
   *  rides at `stop_ticks`, so the distance is the stop you already chose and
   *  the only free variable is when it wakes up. Refused without a stop. */
  trail_trigger_ticks: number;
  /** Ticks of profit before Rithmic jumps the stop to a breakeven-plus level.
   *  0 is off. Fires once, unlike the trail. Refused without a stop. */
  be_trigger_ticks: number;
  /** How many ticks of profit that jump locks in — **always positive, always in
   *  the trade's favour**. Rithmic's own field is raw price arithmetic and gets
   *  negated for a sell inside the API; that sign never appears up here, because
   *  a convention that leaks into a form is one that ends up wrong on one side.
   *  Must be ≥ 1 whenever `be_trigger_ticks` is set: a 0 is a proto3 default and
   *  never reaches the wire. */
  be_ticks: number;
}
