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
  /** The micro lookup was attempted and failed, as opposed to this login simply
   *  not having one. Both leave `instruments` one entry long and neither draws
   *  a switch — but only this one is worth reconnecting to retry. */
  instrument_lookup_failed: boolean;
  /** What Rithmic said when the lookup failed, or null. Drawn in the tooltip
   *  rather than the line: it is the difference between "no permission" and
   *  "try again later", which is not visible anywhere else without a second
   *  login — and a second login logs the running feed out. */
  instrument_lookup_error: string | null;
  /** The root this login last chose to route to, when the session could **not**
   *  honour it — otherwise null. Non-null means the stored plan is in micros
   *  and the orders are going to the mini at ten times the money, which is the
   *  one thing the switch exists to prevent and so must be said out loud. */
  instrument_want: string | null;
  /** Both follow `symbol`, so the panel's risk arithmetic is read rather than
   *  assumed: the same 50 ticks is $250 of NQ and $25 of MNQ. */
  tick_size: number;
  point_value: number;
  /** The configured rate scaled to the routed contract — a micro round turn is
   *  not charged at the mini rate the setting was measured at. */
  commission_per_side: number;
  /** Epoch seconds of the last reconciliation, or null for "never asked". */
  reconciled_at: number | null;
  /** The trailing ladder this API is running on the open position, or null. */
  ladder: LadderState | null;
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
  /** What the last order took. Read off the poll rather than only off the send's
   *  own reply, because the exchange's word lands after that reply has gone. */
  last_latency: OrderLatency | null;
  error: string | null;
}

/** How long one order took, leg by leg. Every field is a **duration** measured
 *  start-to-finish on a single clock, and no two of them come from differencing
 *  timestamps taken on different machines — the browser times its own press, the
 *  API times its own wire call. A press stamp shipped to the server and
 *  subtracted from its wall clock would report the browser/WSL skew as latency.
 *
 *  Milliseconds, one decimal. The full record is in `orders.jsonl` as `latency`
 *  events, one line per answer, each carrying everything known so far. */
export interface OrderLatency {
  tag: string;
  /** "review" or "one_click" — the two are not comparable, one has a dialog in
   *  the middle of it. */
  how: string;
  basket_id?: string;
  /** The API's own checks before the wire: the guards, the day's arithmetic,
   *  the journal write. A bad number here is a bug on this side. */
  gate_ms?: number;
  /** The wire: our submit to Rithmic's order plant answering with a basket. */
  plant_ms?: number;
  /** The whole request handler, so `api_ms - gate_ms - plant_ms` is what the
   *  session lookup and the parsing cost. */
  api_ms?: number;
  /** Wire to the exchange's *first* word on the order — working, or rejected.
   *  Arrives after the response, so it is null on the send's own reply and
   *  filled in by the time the panel next polls. The honest end of "placed". */
  exch_ms?: number;
  exch_status?: string | null;
  /** The browser's own: gesture handler to response in hand. */
  client_ms?: number;
  /** `client_ms - api_ms` — fetch, proxy, JSON, and React getting round to it. */
  net_ms?: number | null;
  /** Which button it was. Only the browser knows. */
  gesture?: string | null;
  /** Set when the send threw; the timing is kept because a 20-second wedged
   *  plant is the measurement most worth having. */
  failed?: string;
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
  /** Close what is *still* open when the day crosses `daily_loss_stop`, rather
   *  than only refusing the next entry. The stop is measured on **booked** P&L,
   *  so the only case this acts on is a close that took the day past the line
   *  and left size on — a scale-out with a runner behind it. Off, the day still
   *  locks; it just leaves the runner to you. */
  auto_flatten: boolean;
  /** The most one entry may risk: stop x size x the contract's dollars-per-tick.
   *  The whole of how large an order may be — there is no quantity ceiling
   *  beside it, and there was one. 5 on a 50-tick stop is $125 of micros or
   *  $1,250 of minis, and the order goes out on whatever the chart is on, so a
   *  ceiling that passed both was never the rule protecting the account. */
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
  /** Realised plus what the open position is currently down. What the *firm's*
   *  floor is marked against — **not** what the daily stop fires on, which is
   *  `realized` (see `Guards.auto_flatten` on the server for why). */
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
  /** The contract it was actually taken on. Not always the one routing points
   *  at now — an instrument switch mid-session leaves two rows of `×1` meaning
   *  very different money, which is the case the blotter badges. */
  symbol?: string;
  entry_price: number;
  entry_ms: number;
  exit_price: number;
  exit_ms: number;
  pts: number;
  /** **Gross**, unlike the paper simulation's. `fees` is the commission the
   *  day's `realized` charges for it; anything showing net subtracts it. */
  pnl: number;
  /** Commission on this portion, both sides, at the rate for `symbol`. Null on
   *  a row restored from the journal, which has no commission column. */
  fees?: number | null;
  /** Excursion R against the stop the position opened with: points made over
   *  points risked, size-blind. **Null** when it carried no stop — there was no
   *  risk to divide by. */
  r: number | null;
  /** Stake R: net dollars over `risk_usd`. Null on the same terms. */
  r_cash?: number | null;
  /** The dollars staked at open — the figure the order pad's sizer quoted.
   *  The *position's*, repeated on every scale-out of it, so it must never be
   *  totalled; see `replaySim.Trade.riskUsd`, the same field on the same terms. */
  risk_usd?: number | null;
  /** How the position was opened — `limit`, `stop`, `market`. Null when it
   *  cannot be known (a restored row). */
  open_type?: string | null;
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
  /** `LIVE_GUARDRAILS` is not switched off. Readable with no session running,
   *  like `enabled` — "are the rules on" is a property of the deployment. */
  guardrails: boolean;
  /** `REPLAY_GUARDRAILS` is not switched off — whether `/replay` applies its
   *  mirror of the rules (lib/guardRules). Same polarity as `guardrails` and a
   *  separate switch: one protects the account, the other protects the habit,
   *  and a session spent measuring a tighter stop should not have to disarm
   *  the live layer to run. */
  replay_guardrails: boolean;
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
  /** What it took. `client_ms` is filled in on this side once the response is
   *  in hand — the server cannot know it. */
  latency?: OrderLatency | null;
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
  /** --- the ladder: the same trail, run by this app instead ----------------
   *
   *  None of these four reach the wire. The order goes out as a plain static
   *  bracket and the server's own ladder moves the stop leg with `modify` — the
   *  Python port of the rule `replaySim` runs on paper. That buys the grid, the
   *  breakeven rung and a stop that can still be dragged, and costs the ratchet
   *  if the API stops running.
   *
   *  **Mutually exclusive with the two Rithmic-managed fields above**, and the
   *  server refuses the pair rather than picking one: a managed stop is
   *  re-derived absolutely on every new extreme, so the two would overwrite each
   *  other for as long as the trade lasted. */
  ladder_dist_ticks: number;
  /** The grid the stop may rest on. 0 = one rung per `ladder_dist_ticks`. */
  ladder_step_ticks: number;
  /** How far past the fill the first rung lands. 0 is breakeven *gross*. */
  ladder_be_ticks: number;
  /** Take the first rung and no other: a breakeven stop rather than a trail. */
  ladder_be_only: boolean;
}

/** The ladder the API is running on the open position, off the routing poll.
 *
 *  Worth drawing rather than inferring: a stop that moves on its own is the one
 *  line on the chart nobody put there by hand, and a ladder that has quietly
 *  given up (three refused modifies) looks exactly like one that has not earned
 *  a rung yet. `failures` is what tells those apart. */
export interface LadderState {
  side: "long" | "short";
  entry: number;
  /** The best price the trade has seen — what the next rung is measured from. */
  hwm: number;
  stop: number | null;
  /** Has the ladder moved this stop yet? False on a stop still sitting where it
   *  was placed, or on one just dragged by hand. */
  armed: boolean;
  /** Where the grid is pinned, once a drag has re-pinned it. Null while the
   *  ladder still owns the origin. */
  ladder: number | null;
  rungs: number;
  dist: number;
  step: number;
  be: number;
  be_only: boolean;
  /** Consecutive failed modifies. At 3 the ladder has stopped trying. */
  failures: number;
  stats: { rungs: number; failed: number; given_up: number };
}
