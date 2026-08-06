// The Simulator's order keeper: turns the log of what you *did* into the trades,
// the open position, and the orders still working — by replaying the tape.
//
// Everything the page records is an order, stamped with the
// clock it was placed at. Nothing else is stored: the fills, the bracket exits
// and the working set are all *derived* by walking the ticks in order. That is
// what makes rewinding coherent — truncate the log at the new clock and re-run,
// and a trade you hadn't taken yet simply hasn't happened, a stop you hadn't
// dragged is back where it was, an order you hadn't cancelled is working again.
//
// The same walk serves both clocks the page runs on: `stepSim` folds one tick
// range into a live state (what the playback loop does, once a frame), and
// `runSim` rebuilds from the log (what a seek or any user action does). One
// code path, so forward play and a rebuild can never disagree about a fill.
//
// Three order types, and which one you meant follows from where the price sits.
// Below the market, a buy is a limit and a sell is a stop; above it, the other
// way round. So the *type* is never really a choice — it's a consequence of the
// side and the price, which is why the chart can place either from one gesture.
//
// The position is *netted*, the way a futures account is: one net position per
// session, carried at its average price. A fill on the side you're already on
// adds to it and moves the average; a fill against it takes size off and books
// that portion as a trade; a fill bigger than the position closes it and leaves
// you the other way round. There is no such thing here as being long and short
// at once — that is a hedging-account idea, and NQ is not one.
//
// House rules, all of them the conservative reading:
//
//   - a resting order fills when a print reaches its price, at that price — no
//     queue model, no slippage. Practising the read, not the microstructure.
//     Flattering to a stop, which in life slips through its trigger; the ticks
//     lost there are not something a replay can teach you to avoid;
//   - an order can only be placed (or dragged) on the side of the market its
//     type belongs on. A buy limit above the market would fill at the limit and
//     mint free ticks; a buy stop below it would do the same in reverse;
//   - the bracket belongs to the *position*, not to the order that opened it —
//     one stop and one target covering whatever size you're carrying, so the
//     fills you scaled in with don't each bring their own. An order's own legs
//     are the bracket it *proposes*: they take effect only where its fill opens
//     a position, which means from flat or through a flip. A fill that adds to
//     a position, or takes size off one, leaves the bracket exactly as it was;
//   - orders you place while flat are one OCO set — the first of them to fill
//     cancels the others, so bracketing a range with a bid and an offer still
//     does what it looks like it does. Orders placed while a position is open
//     stand alone: you placed those knowing what you were in, and cancelling
//     the scale-out you just set because a scale-in filled would be wrong;
//   - a stop and a target that a single print spans both resolve as the stop,
//     and a print that resolves the bracket does so before it fills any working
//     order — the exit you had on beats the entry you were waiting for;
//   - a flip cancels the bracket it was carrying. Inheriting a long's stop into
//     the short it just became is how a practice account teaches a bad habit;
//   - the trail is derived like everything else. Its settings ride on the order
//     that opened the position, so the ladder is a pure function of the log and
//     a rewind puts the stop back exactly where it stood — the ratchet never
//     writes to the log itself, which would make rewinding it incoherent;
//   - the ladder never loosens the stop, but *you* may, in either direction and
//     at any point. A drag re-pins the grid and walks the high-water mark back
//     to match, so it holds; the trail then resumes from your level on the next
//     high that beats it. The stop is yours, and the trail is a tool for moving
//     it — a practice account that refused the drag would be teaching the habit
//     of arguing with your own platform;
//   - risk is frozen at the fill that opened the position. Both R's measure
//     against that, never against the stop as it stands at the exit, so moving a
//     stop shows up in the numbers instead of hiding in them: widen it and the
//     loss books past 1R, tighten it and it books under.

import type { Tape } from "./replayEngine";

export type Side = "long" | "short";
/** How a portion of a position came off: by hand, on its bracket, or because an
 *  order on the other side filled and netted it down. `trail` is a stop the
 *  ladder had moved — kept apart from `stop` because they are different events,
 *  the same way the backtest engine keeps them apart. */
export type ExitReason = "manual" | "stop" | "target" | "reduce" | "trail";

/** Auto-stop management: a ratchet that walks the stop up behind the best price
 *  the trade has seen, and never gives ground back.
 *
 *  The rule and its four knobs are the backtest engine's, tick for tick — see
 *  `trail_stop_ticks` and friends in `src/journal/sim/rules.py`. That parity is
 *  the point: what you practise here is what the engine trades.
 *
 *  Distances are *prices*, not ticks. The ticket does the tick→price conversion
 *  once, at placement, the same way it already resolves a stop distance to an
 *  absolute level — so nothing downstream has to carry a tick size around. */
export interface TrailCfg {
  /** How far behind the high-water price the stop rides. 0 = the trail is off,
   *  and the master switch for all of it. */
  dist: number;
  /** The grid the stop is allowed to rest on. 0 = one rung per `dist`. */
  step: number;
  /** How far past the entry the first rung lands. Zero is breakeven *gross* —
   *  the round trip still owes commission — so a few ticks here is what makes a
   *  scratch really a scratch. */
  be: number;
  /** Take the first rung and no other. That is a breakeven stop rather than a
   *  trail, and it is its own rule: a trail hands back open profit on every
   *  pullback, which is exactly what a breakeven stop refuses to do. */
  beOnly: boolean;
}
/** `limit` rests on the passive side of the market, `stop` triggers through it. */
export type OrderType = "market" | "limit" | "stop";

/** A drag, stamped with the clock. Carries the whole level set rather than the
 *  one leg that moved, so replaying the log never has to merge edits. */
export interface OrderEdit {
  ms: number;
  price: number | null;
  stop: number | null;
  target: number | null;
}

export interface OrderRec {
  id: number;
  type: OrderType;
  side: Side;
  size: number;
  /** Clock the order was placed at. */
  ms: number;
  /** Tape cursor at placement — the first tick that may fill it. A print that
   *  happened at the same millisecond is a print you placed *after*. */
  idx: number;
  /** Where it rests: the limit for a limit order, the trigger for a stop. A
   *  market order has none — it is its own fill. */
  price: number | null;
  /** The bracket it proposes, as absolute prices. Set from the ticket at
   *  placement and moved by dragging. Becomes the *position's* bracket if this
   *  order's fill opens one. */
  stop: number | null;
  target: number | null;
  /** The trail it proposes, snapshotted off the ticket at placement — like the
   *  bracket, and for the same reason. Settings that live only in React state
   *  would make a rebuild disagree with the forward play the moment you touched
   *  the ticket mid-replay; stamped on the order, the ladder is a pure function
   *  of the log and a rewind reproduces it exactly. */
  trail: TrailCfg | null;
  edits: OrderEdit[];
  /** When it was cancelled, or null while it stands. */
  cancelMs: number | null;
}

export interface OrderState {
  price: number | null;
  stop: number | null;
  target: number | null;
}

/** A drag of the *position's* bracket, stamped with the clock. Its own channel
 *  in the log rather than an edit on the order that opened the position: once
 *  several fills can make up one position, "the stop" is not any one order's. */
export interface BracketEdit {
  ms: number;
  stop: number | null;
  target: number | null;
}

/** The order's levels as they stood at an instant: the last edit at or before it.
 *
 *  Returns the record (or the edit) itself rather than a fresh object — both are
 *  already an `OrderState`, and this is called once per tick per open order, so
 *  a rebuild over a million-tick session would otherwise allocate a million
 *  throwaway objects. Read-only, like everything else on the log. */
export function orderStateAt(o: OrderRec, ms: number): OrderState {
  let s: OrderState = o;
  for (const e of o.edits) {
    if (e.ms > ms) break;
    s = e;
  }
  return s;
}

/** The net position: which way, how much, and at what average. Mutated in place
 *  as fills land on it — it is a running total, not a record of an order. */
export interface Position {
  side: Side;
  /** Net size, always positive. A position that reaches zero is no position. */
  size: number;
  /** Volume-weighted average of the fills that built it. Adds move it; taking
   *  size off does not — the average of what's left is the average it had. */
  entryPrice: number;
  /** When the position opened — the fill that took it off flat, not the last one
   *  that added to it. Where the chart starts drawing it from. */
  fillMs: number;
  /** First tick index the bracket is live from — the print that filled the order
   *  is not also a print that can stop it out. */
  fillIdx: number;
  /** How the position was opened. Kept for the blotter, which says what kind of
   *  order got you in. */
  openType: OrderType;
  /** Whether more than one fill built it — i.e. whether `entryPrice` is an
   *  average rather than a price you actually traded at. Worth saying out loud
   *  in the readouts: it is the number every other one is measured from. */
  scaled: boolean;
  /** The live bracket, covering the whole position. */
  stop: number | null;
  target: number | null;
  /** The ladder this position is managed by, inherited from the order that
   *  opened it. Null when the trail was off on that ticket. */
  trail: TrailCfg | null;
  /** Best price seen since the position opened, in its favour. What the ladder
   *  measures from. */
  hwm: number;
  /** Where the step grid is pinned, once a manual drag has moved it. Null while
   *  the grid still sits on the breakeven rung. */
  ladder: number | null;
  /** Whether the level the stop currently sits on was put there by the ladder
   *  rather than by hand. Decides which of `trail` and `stop` a stop-out books
   *  as — being taken out on a level you dragged is not the ladder's doing. */
  trailArmed: boolean;
  /** Risk as it stood when the position opened, frozen. Dragging the stop
   *  changes what you *lose*; it does not retroactively change what you
   *  *risked*, so neither a drag nor a scale-in ever re-bases these — which is
   *  exactly what makes a widened stop show up as a worse-than-1R loss.
   *
   *  `riskPts` is the distance, `riskCash` the money that distance was worth at
   *  the size it opened with. Null when the position opened bare; the first stop
   *  attached *by hand* fills them in, but the trail's own first rung never does
   *  — a level the ladder chose is not a risk you ever set, and measuring R off
   *  a breakeven rung would read as a huge multiple of nothing. */
  riskPts: number | null;
  riskCash: number | null;
}

/** One closed portion of a position. A trade in the blotter sense: size that was
 *  on and came off, with what it paid. Scaling out books several of these
 *  against one entry, which is the point of scaling out. */
export interface Trade {
  id: number;
  side: Side;
  size: number;
  /** The position's average at the time this portion came off. */
  entryPrice: number;
  entryMs: number;
  openType: OrderType;
  exitMs: number;
  exitPrice: number;
  reason: ExitReason;
  pts: number;
  pnl: number;
  /** Excursion R: points moved ÷ points risked at open. Size-blind — it asks
   *  whether the *read* was good, i.e. whether price travelled further than the
   *  distance you'd allowed against you. Null when the position carried no risk
   *  to measure by. Don't total it: every portion of one position reports the
   *  same geometry. */
  r: number | null;
  /** Stake R: dollars made ÷ dollars staked at open. Asks whether the *bet*
   *  paid. Identical to `r` for a single clip in and out; they part company the
   *  moment size changes mid-trade, which is the case worth seeing. Unlike `r`
   *  this one sums — every portion divides by the same opening stake, so the
   *  scale-outs of one position add up to what it did. */
  rCash: number | null;
}

/** Everything the user did, in the order they did it. */
export interface Log {
  orders: OrderRec[];
  closes: { ms: number }[];
  /** Drags of the open position's bracket. */
  brackets: BracketEdit[];
}

export function newLog(): Log {
  return { orders: [], closes: [], brackets: [] };
}

/** A working order and the company it keeps. `oco` marks the ones placed while
 *  flat, which stand or fall together — see the house rules. */
interface Working {
  o: OrderRec;
  oco: boolean;
}

export interface SimState {
  trades: Trade[];
  open: Position | null;
  working: Working[];
  /** How far the log pointers have been consumed, so an incremental step picks
   *  up where the last one stopped. */
  oi: number;
  ci: number;
  bi: number;
}

export function newSim(): SimState {
  return { trades: [], open: null, working: [], oi: 0, ci: 0, bi: 0 };
}

/** The orders still working, in the order they were placed. */
export function workingOrders(st: SimState): OrderRec[] {
  return st.working.map((w) => w.o);
}

/** The last print at or before an instant — the mark a market order fills at. */
export function priceAtMs(tape: Tape, ms: number): number {
  let lo = 0;
  let hi = tape.n - 1;
  let res = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (tape.t[mid] <= ms) {
      res = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return tape.price[res];
}

/** A position, freshly opened by `o` filling at `price` for `size`. Risk is
 *  frozen here and nowhere else. */
function openPosition(
  o: OrderRec,
  legs: OrderState,
  ms: number,
  idx: number,
  price: number,
  size: number,
  pointValue: number,
): Position {
  const riskPts = legs.stop != null ? Math.abs(price - legs.stop) : null;
  return {
    side: o.side,
    size,
    entryPrice: price,
    fillMs: ms,
    fillIdx: idx,
    openType: o.type,
    scaled: false,
    stop: legs.stop,
    target: legs.target,
    trail: o.trail && o.trail.dist > 0 ? o.trail : null,
    hwm: price,
    ladder: null,
    trailArmed: false,
    riskPts,
    riskCash: riskPts != null ? riskPts * pointValue * size : null,
  };
}

/** Where the ladder wants the stop, or null while it hasn't reached far enough
 *  in front to take its first rung.
 *
 *  The grid is pinned at the breakeven rung and rungs sit `step` apart from
 *  there; the stop rides `dist − be` behind the high-water price, dropping onto
 *  the highest rung that distance clears. So with a 40-tick trail, a 20-tick
 *  step and a 4-tick breakeven, a long from 21000 puts its stop on 21001 once
 *  the trade has printed 21010, on 21006 at 21015, and so on — always 36 to 56
 *  ticks behind the best the trade has seen.
 *
 *  A manual drag re-pins the grid wherever you put it, in whichever direction
 *  you moved it — `ladder` simply replaces the breakeven rung as the origin. The
 *  high-water mark is walked back to match at the same moment (see the bracket
 *  branch of `admin`), because re-pinning alone would not make a loosened stop
 *  hold: the ladder is a function of the high, so the very next print would
 *  recompute the level you just moved away from. Between them the drag sticks,
 *  and the trail resumes only on a high that beats what your level justifies. */
function trailStop(p: Position): number | null {
  const tc = p.trail;
  if (!tc || tc.dist <= 0) return null;
  const dir = p.side === "long" ? 1 : -1;
  const step = tc.step > 0 ? tc.step : tc.dist;
  const back = Math.max(0, tc.dist - tc.be);
  const origin = p.ladder ?? p.entryPrice + dir * tc.be;
  // How many whole rungs past the origin the high-water price has carried it.
  const k = Math.floor(((p.hwm - origin) * dir - back) / step);
  if (k < 0) return null;
  return tc.beOnly ? origin : origin + dir * k * step;
}

/** Move the stop onto `lvl`, but only ever toward the trade. Everything the
 *  ladder does goes through here, so "never loosens" is one line rather than an
 *  invariant spread across the call sites. */
function tighten(p: Position, lvl: number): void {
  const dir = p.side === "long" ? 1 : -1;
  if (p.stop != null && (lvl - p.stop) * dir <= 0) return;
  p.stop = lvl;
  p.trailArmed = true;
}

/** Book `size` of the open position off at `price`. Assumes the caller has
 *  already checked there is that much to take off. */
function reduce(
  st: SimState,
  size: number,
  ms: number,
  price: number,
  reason: ExitReason,
  pointValue: number,
): void {
  const p = st.open!;
  const dir = p.side === "long" ? 1 : -1;
  const pts = (price - p.entryPrice) * dir;
  const pnl = pts * pointValue * size;
  // Both R's measure against the risk frozen at open, never the stop as it
  // stands now. Reading the live stop makes every stop exit book exactly ±1.00R
  // by construction — the exit price *is* the stop, so the numerator and the
  // denominator are the same distance. Correct for a stop that never moved,
  // nonsense for one that ratcheted into profit: a trade that trailed out for
  // +$120 on $200 of risk would book +1.00R, and so would one that trailed out
  // for +$520. The column stops carrying information exactly when it gets
  // interesting.
  st.trades.push({
    id: st.trades.length + 1,
    side: p.side,
    size,
    entryPrice: p.entryPrice,
    entryMs: p.fillMs,
    openType: p.openType,
    exitMs: ms,
    exitPrice: price,
    reason,
    pts,
    pnl,
    r: p.riskPts && p.riskPts > 0 ? pts / p.riskPts : null,
    rCash: p.riskCash && p.riskCash > 0 ? pnl / p.riskCash : null,
  });
  // The average of what's left is the average it had: taking size off never
  // moves it, only adding does.
  p.size -= size;
  if (p.size <= 0) st.open = null;
}

/** Land a fill on the net position: open it, add to it, take size off it, or run
 *  it through and out the other side. */
function applyFill(
  st: SimState,
  o: OrderRec,
  legs: OrderState,
  ms: number,
  idx: number,
  price: number,
  pointValue: number,
): void {
  const p = st.open;
  if (!p) {
    st.open = openPosition(o, legs, ms, idx, price, o.size, pointValue);
    return;
  }
  if (p.side === o.side) {
    // Scaling in: the average moves to the weighted mean of what you now hold.
    p.entryPrice = (p.entryPrice * p.size + price * o.size) / (p.size + o.size);
    p.size += o.size;
    p.fillIdx = idx;
    p.scaled = true;
    // The bracket is untouched: it came from the order that opened the position
    // and it belongs to the position, not to whatever ticket you happened to
    // scale in with. Attaching one afterwards is a deliberate act — the +SL/+TP
    // buttons, or a drag on the chart.
    return;
  }
  // Against the position: net it down, and if the order is bigger than what was
  // on, the remainder opens a new one the other way.
  const closed = Math.min(o.size, p.size);
  reduce(st, closed, ms, price, "reduce", pointValue);
  const rest = o.size - closed;
  // The flip carries this order's own legs and its own ladder, never the ones it
  // just closed out from under — a fresh position, armed from scratch.
  if (rest > 0) st.open = openPosition(o, legs, ms, idx, price, rest, pointValue);
}

/** Take a filled order out of the working set, along with the rest of its OCO
 *  set if it was in one. `w` is null for a market order, which fills without
 *  ever having rested — but which still speaks for the set it was placed into. */
function retire(st: SimState, w: Working | null, oco: boolean): void {
  if (!w && !oco) return;
  st.working = st.working.filter((x) => x !== w && !(oco && x.oco));
}

/** Would this print resolve the bracket, and as which leg? */
function bracketHit(p: Position, px: number): ExitReason | null {
  if (p.side === "long") {
    if (p.stop != null && px <= p.stop) return "stop";
    if (p.target != null && px >= p.target) return "target";
  } else {
    if (p.stop != null && px >= p.stop) return "stop";
    if (p.target != null && px <= p.target) return "target";
  }
  return null;
}

/**
 * Fold the ticks in `[from, to)` into `st`, stopping at `clock`.
 *
 * Mutates `st` — that's the point: the playback loop keeps one state across
 * frames and hands it each frame's tick range, so a session costs one pass over
 * the tape however many frames it took to watch.
 */
export function stepSim(
  tape: Tape,
  log: Log,
  st: SimState,
  from: number,
  to: number,
  clock: number,
  pointValue: number,
): void {
  const { orders, closes, brackets } = log;

  // Everything that happens *between* prints: orders coming on, cancels taking
  // effect, a bracket drag landing, a manual close. Run before each tick against
  // that tick's stamp, and once more at the clock so an action taken since the
  // last print still counts.
  const admin = (ms: number) => {
    while (st.oi < orders.length && orders[st.oi].ms <= ms) {
      const o = orders[st.oi++];
      // Whether an order keeps company with the others is decided here, as it
      // comes on, rather than recorded when it was placed: the walk is
      // deterministic, so the state it is admitted into is the state it was
      // placed into.
      if (o.type === "market") {
        // A market order is its own fill, at the last print before it.
        const oco = st.open == null;
        applyFill(st, o, o, o.ms, o.idx, priceAtMs(tape, o.ms), pointValue);
        retire(st, null, oco);
      } else st.working.push({ o, oco: st.open == null });
    }
    if (st.working.length) {
      const live = st.working.filter((w) => w.o.cancelMs == null || w.o.cancelMs > ms);
      if (live.length !== st.working.length) st.working = live;
    }
    while (st.bi < brackets.length && brackets[st.bi].ms <= ms) {
      const b = brackets[st.bi++];
      // A drag recorded against a position that no longer exists at this point
      // in the walk has nothing to move — which is what a rewind past the trade
      // it belonged to should do.
      if (st.open) {
        const p = st.open;
        const s = b.stop;
        // A stop attached by hand to a position that opened bare is the first
        // risk you ever set on it, so it is the one the R's measure from. Only
        // by hand: see `riskPts`.
        if (p.riskPts == null && s != null) {
          p.riskPts = Math.abs(p.entryPrice - s);
          p.riskCash = p.riskPts * pointValue * p.size;
        }
        p.stop = s;
        p.target = b.target;
        // The drag wins outright, whichever way it went — the ladder is a tool
        // for managing the stop, not a lock on it. Two things have to happen for
        // that to hold, because a level alone would not survive the next print:
        //
        //   - the grid re-pins on where you put it, so the rungs from here are
        //     spaced off your level rather than off the entry;
        //   - the high-water mark walks back to the last high your level is
        //     consistent with. The ladder reads the high, so leaving a high that
        //     already justifies a tighter stop would have it snap straight back
        //     — the drag would appear to take, then undo itself a tick later.
        //
        // Only ever walked *back*: a drag can make the trail forget a high it
        // has been given, never invent one it hasn't seen.
        if (s != null && p.trail) {
          const dir = p.side === "long" ? 1 : -1;
          p.ladder = s;
          const cap = s + dir * Math.max(0, p.trail.dist - p.trail.be);
          if ((cap - p.hwm) * dir < 0) p.hwm = cap;
          // The level is yours now, so being taken out on it is a stop rather
          // than a trail. The next rung the ladder takes claims it back.
          p.trailArmed = false;
        }
      }
    }
    while (st.ci < closes.length && closes[st.ci].ms <= ms) {
      const c = closes[st.ci++];
      if (st.open) reduce(st, st.open.size, c.ms, priceAtMs(tape, c.ms), "manual", pointValue);
    }
  };

  const end = Math.min(to, tape.n);
  for (let i = Math.max(0, from); i < end; i++) {
    const ms = tape.t[i];
    if (ms > clock) break;
    // Guarded rather than called blind: on a full rebuild this loop runs over
    // the whole session, and for almost all of it there is nothing to admit,
    // cancel or close.
    if (st.oi < orders.length || st.ci < closes.length || st.bi < brackets.length || st.working.length)
      admin(ms);
    const px = tape.price[i];
    // The bracket first: an exit you already had on resolves before a print
    // fills anything you were waiting on.
    if (st.open && i >= st.open.fillIdx) {
      const p = st.open;
      const hit = bracketHit(p, px);
      if (hit) {
        // A stop the ladder had moved is not the stop you placed. Kept apart so
        // the blotter can say which one got you out — they are different events,
        // and only one of them is a loss you planned for.
        const why = hit === "stop" && p.trailArmed ? "trail" : hit;
        reduce(st, p.size, ms, hit === "stop" ? p.stop! : p.target!, why, pointValue);
      } else if (p.trail) {
        // The high-water mark moves *after* the bracket has been read, so a
        // print can never both set a new best and ratchet a stop into its own
        // path on the same tick.
        const dir = p.side === "long" ? 1 : -1;
        if ((px - p.hwm) * dir > 0) p.hwm = px;
        const lvl = trailStop(p);
        if (lvl != null) tighten(p, lvl);
      }
    }
    if (st.working.length) {
      // Over a snapshot: a fill can take orders out of the set (its own OCO
      // company), and more than one order can be reached by the same print.
      for (const w of [...st.working]) {
        if (i < w.o.idx || !st.working.includes(w)) continue;
        const s = orderStateAt(w.o, ms);
        if (s.price == null) continue;
        // A limit is reached from the passive side, a stop from the active one —
        // the same comparison, mirrored.
        const wantsUp = w.o.type === "stop" ? w.o.side === "long" : w.o.side === "short";
        const filled = wantsUp ? px >= s.price : px <= s.price;
        if (filled) {
          applyFill(st, w.o, s, ms, i + 1, s.price, pointValue);
          retire(st, w, w.oco);
        }
      }
    }
  }
  admin(clock);
}

/** Rebuild the whole picture from the log, as of `clock`. */
export function runSim(tape: Tape, log: Log, clock: number, pointValue: number): SimState {
  const st = newSim();
  // Nothing can happen before the first order was placed, so start the walk
  // there rather than at the session's first tick.
  const from = log.orders.length ? log.orders[0].idx : tape.n;
  stepSim(tape, log, st, from, tape.n, clock, pointValue);
  return st;
}

// --- the checkpoint ladder --------------------------------------------------
//
// `runSim` walks from the first order to the clock on every user action. On a
// replay tape that is a bounded cost paid on a click. On a *live* tape it is
// O(ticks-since-the-first-order), and it grows all session: place an order at
// the open and by the afternoon every drag re-walks a million prints.
//
// The ladder makes that amortised without introducing a second answer. A
// checkpoint is a snapshot of the *same fold* — `stepSim`, run in chunks, with
// its state cloned at each boundary — so a rebuild that resumes from one
// produces what a rebuild from scratch would have, tick for tick. There is no
// optimistic path here to disagree with the authoritative one; there is one
// path, entered further along.
//
// WHAT MAKES A CHECKPOINT REUSABLE. The state is a fold of (tape prefix, log
// prefix), so it survives exactly as long as neither of those has been rewritten
// underneath it. Both are checked rather than assumed, because both *can* change:
//
//   - the tape, if the session was replaced or context days were glued in front.
//     Object identity is not enough (the typed arrays are reallocated on growth
//     and the object survives), so the last folded tick's stamp is stored and
//     re-read. If `tape.t[i-1]` is no longer what it was, the tape is not the one
//     this state was folded from;
//   - the log, if anything already consumed was edited, cancelled, or truncated.
//     Every mutation on the page rebuilds the `Log` and the `OrderRec` it
//     touches, leaving the untouched ones reference-identical — so comparing the
//     consumed prefix element by element catches a cancel or a drag on an order
//     this state has already admitted, and a rewind's `truncateLog` shortens the
//     arrays. The unconsumed entries are checked too, on stamp: one appended
//     *behind* the checkpoint's clock would be folded in a tick late.
//
// Any of those fails and the checkpoint is dropped, along with every later one
// (they folded a superset of the same history), and the walk falls back to as far
// as the ladder is still sound — a full rebuild in the worst case. Wrong answers
// are therefore not on the menu; the only thing at stake is how much work is
// saved.

/** A snapshot of the fold: ticks `[start, i)` are in, and nothing beyond them. */
interface Checkpoint {
  /** The next tick to fold. */
  i: number;
  /** `tape.t[i - 1]` — the last tick folded, and the proof of which tape it was. */
  ms: number;
  st: SimState;
  /** The consumed prefix of each channel, held by reference for the identity
   *  check. Small: these are the orders placed so far, not the ticks. */
  orders: OrderRec[];
  closes: { ms: number }[];
  brackets: BracketEdit[];
}

/** A deep-enough copy to fold forward from without touching the original.
 *
 *  Only `open` is copied by value, because it is the only thing `stepSim`
 *  mutates in place — trades are pushed and never revised, working entries are
 *  replaced rather than edited, and the `OrderRec` they point at belongs to the
 *  log. The two arrays still need their own containers, which is what `slice`
 *  is for here. */
function cloneSim(st: SimState): SimState {
  return {
    trades: st.trades.slice(),
    open: st.open ? { ...st.open } : null,
    working: st.working.slice(),
    oi: st.oi,
    ci: st.ci,
    bi: st.bi,
  };
}

/** First index whose stamp is past `clock`, i.e. one past the last foldable tick. */
function upperBound(tape: Tape, clock: number): number {
  let lo = 0;
  let hi = tape.n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tape.t[mid] <= clock) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function samePrefix<T>(cur: readonly T[], was: readonly T[]): boolean {
  if (cur.length < was.length) return false;
  for (let i = 0; i < was.length; i++) if (cur[i] !== was[i]) return false;
  return true;
}

/** A drop-in for `runSim` that remembers where it has already been.
 *
 *  Hold one per tape surface and call `run` wherever `runSim` was called. The
 *  ladder is a cache and nothing else: `run` returns what `runSim` returns, and
 *  `reset()` (or simply handing it a different tape) only ever costs time. */
export class SimLadder {
  private tape: Tape | null = null;
  private cps: Checkpoint[] = [];

  /** `every` is the tick spacing between snapshots. The default trades ~20
   *  snapshots over a busy NQ session against a worst-case walk of 50k ticks on
   *  any one rebuild — measured at 0.46ms over a synthetic million-print tape,
   *  against 10.3ms for the same rebuild from scratch.
   *
   *  The worst case is also the rare one. A run lays snapshots up to its own
   *  clock, so the next rebuild resumes from where the last one stopped and
   *  folds only what has arrived since — which on a live tape is the handful of
   *  prints between two clicks. A full chunk is only ever walked once, right
   *  after the ladder is first laid down. */
  constructor(private readonly every = 50_000) {}

  reset(): void {
    this.tape = null;
    this.cps = [];
  }

  /** How many snapshots are live. For tests and diagnostics. */
  get depth(): number {
    return this.cps.length;
  }

  run(tape: Tape, log: Log, clock: number, pointValue: number): SimState {
    if (tape !== this.tape) {
      this.tape = tape;
      this.cps = [];
    }
    const base = this.pick(tape, log, clock);
    const st = base ? cloneSim(base.st) : newSim();
    // Without a checkpoint this is `runSim`'s own start: nothing can happen
    // before the first order was placed.
    const start = base ? base.i : log.orders.length ? log.orders[0].idx : tape.n;

    // Bounding the walk by the clock up front is what lets every chunk run to
    // its own last tick: inside the range there is no early break to lose the
    // cursor to, so a chunk boundary is always a tick boundary.
    const hi = upperBound(tape, clock);
    let i = Math.max(0, start);
    while (i < hi) {
      const to = Math.min(i + this.every, hi);
      // The chunk's own clock, not the caller's. `stepSim` finishes with
      // `admin(clock)`, and handing it the caller's would apply orders placed
      // long after this chunk's last print at this chunk's position.
      stepSim(tape, log, st, i, to, tape.t[to - 1], pointValue);
      i = to;
      this.push(tape, log, st, i);
    }
    // What has happened since the last print: an order placed a moment ago, on a
    // tape that has not printed since, is working. Deliberately after the last
    // snapshot — this admin runs at a clock ahead of the ticks, and folding it
    // into a checkpoint would consume log entries at the wrong tick.
    stepSim(tape, log, st, i, i, clock, pointValue);
    return st;
  }

  /** The newest snapshot still sound for this tape, log and clock. */
  private pick(tape: Tape, log: Log, clock: number): Checkpoint | null {
    for (let k = this.cps.length - 1; k >= 0; k--) {
      const cp = this.cps[k];
      if (!this.sound(tape, log, cp)) {
        // Every later snapshot folded a superset of this history, so they are
        // gone with it.
        this.cps.length = k;
        continue;
      }
      // Sound but ahead of where we are being asked to rebuild to — a rewind, or
      // a seek back. Keep it; it is still the right answer for a later clock.
      if (cp.ms > clock) continue;
      return cp;
    }
    return null;
  }

  private sound(tape: Tape, log: Log, cp: Checkpoint): boolean {
    if (cp.i > tape.n || tape.t[cp.i - 1] !== cp.ms) return false;
    if (!samePrefix(log.orders, cp.orders)) return false;
    if (!samePrefix(log.closes, cp.closes)) return false;
    if (!samePrefix(log.brackets, cp.brackets)) return false;
    // The arrays are stamped in the order they were appended, so the first
    // unconsumed entry is the earliest one — if it is not past the snapshot, an
    // action has been recorded behind the clock this state was folded to, and
    // resuming would apply it a tick late.
    return (
      after(log.orders[cp.st.oi], cp.ms) &&
      after(log.closes[cp.st.ci], cp.ms) &&
      after(log.brackets[cp.st.bi], cp.ms)
    );
  }

  private push(tape: Tape, log: Log, st: SimState, i: number): void {
    this.cps.push({
      i,
      ms: tape.t[i - 1],
      st: cloneSim(st),
      orders: log.orders.slice(0, st.oi),
      closes: log.closes.slice(0, st.ci),
      brackets: log.brackets.slice(0, st.bi),
    });
  }
}

/** Is this entry — if there is one — stamped after `ms`? */
function after(e: { ms: number } | undefined, ms: number): boolean {
  return e === undefined || e.ms > ms;
}

/**
 * Move every tick index in the log by `delta`.
 *
 * The one thing in the log that isn't wall-clock is an order's cursor position,
 * so it is the one thing that stops meaning what it meant when the tape it
 * indexes into changes shape. That happens exactly once: when context days are
 * glued in front of the session (or taken off again) while a replay is running.
 * The clocks, the prices and the levels are all untouched, so a rebuild after
 * this shift reproduces the same fills at the same prints.
 */
export function shiftLog(log: Log, delta: number): Log {
  if (!delta || !log.orders.length) return log;
  return { ...log, orders: log.orders.map((o) => ({ ...o, idx: o.idx + delta })) };
}

/** Drop from the log everything that hadn't happened yet at `clock`. */
export function truncateLog(log: Log, clock: number): Log {
  return {
    orders: log.orders
      .filter((o) => o.ms <= clock)
      .map((o) => {
        const edits = o.edits.filter((e) => e.ms <= clock);
        const cancelMs = o.cancelMs != null && o.cancelMs <= clock ? o.cancelMs : null;
        return edits.length === o.edits.length && cancelMs === o.cancelMs
          ? o
          : { ...o, edits, cancelMs };
      }),
    closes: log.closes.filter((c) => c.ms <= clock),
    brackets: log.brackets.filter((b) => b.ms <= clock),
  };
}
