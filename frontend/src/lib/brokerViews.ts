// Broker state, as the chart already knows how to draw it.
//
// The pleasant discovery behind this file: `WorkingOrderView` carries **no
// x-coordinate**. A working order is a horizontal line, so it needs a price and
// nothing about where it sits in the tape — which means an order that exists at
// Rithmic, with no tick index into our tape at all, draws through exactly the
// primitive the paper blotter uses. No second overlay, no second renderer, and
// the two account kinds look identical on the chart because they *are* the same
// drawing code.
//
// One thing genuinely has to be invented: `id`. The primitives key orders by a
// number (and hand that number back through `onOrderCancel`/`onOrderMove`),
// while Rithmic identifies an order by a `basket_id` string. `BasketIds` is the
// stable two-way map between them, and it has to be stable for the life of the
// session — a number that changed between polls would make the chart's hover,
// drag and cancel act on whichever order happened to inherit it.
//
// WHAT IS NOT DERIVED HERE. Everything below is a rendering of what the broker
// said. Nothing computes a fill, a P&L or a position from our own tape: that is
// the paper simulation's job, and mixing the two is how a chart ends up showing
// a position nobody holds.

import type { PositionLine } from "../components/charts/ReplayChart";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import type { TradeMarkView } from "../components/charts/TradesPrimitive";
import type { BrokerOrder, BrokerPosition, BrokerTrade } from "./routingTypes";

/** Numeric ids for basket ids, stable for the life of a session.
 *
 *  Not a hash: two ids that collided would silently merge two orders on the
 *  chart, and "unlikely" is not the standard for something that decides which
 *  order a click cancels. A counter cannot collide. */
export class BasketIds {
  private toNum = new Map<string, number>();
  private toId = new Map<number, string>();
  private next = 1;

  num(basketId: string): number {
    let n = this.toNum.get(basketId);
    if (n === undefined) {
      n = this.next++;
      this.toNum.set(basketId, n);
      this.toId.set(n, basketId);
    }
    return n;
  }

  /** The basket id a chart callback's number refers to, or null if this map has
   *  never seen it — which happens when the chart is still holding views from
   *  the paper blotter, and must read as "not mine" rather than as an error. */
  id(n: number): string | null {
    return this.toId.get(n) ?? null;
  }

  clear(): void {
    this.toNum.clear();
    this.toId.clear();
    // The counter deliberately does NOT reset: a stale view still on the chart
    // when the account switches must not find its number pointing at one of the
    // new account's orders.
  }
}

const isLong = (side: string) => side === "buy";

/** The price a working order rests at: its trigger if it has one, its limit
 *  otherwise.
 *
 *  Zero counts as absent, and that is the whole point of the helper. Rithmic
 *  sends every price field on every order — the one that does not apply to the
 *  kind comes back as 0.0, so a plain `trigger_price ?? price` reads a limit
 *  order as resting at zero and it silently leaves the chart. That is exactly
 *  what happened on 2026-08-11: a working buy limit was invisible on /live while
 *  it filled. The API normalises the zero away at the seam now (`broker._px`),
 *  and this is the second lock on the same door — a recorded order journal and
 *  any older API still carry the zero. */
const restingPx = (o: BrokerOrder): number | null =>
  o.trigger_price || o.price || null;

/** Which of a working order's legs, if any, is the bracket on an open position.
 *
 *  Rithmic's bracket legs are separate orders, not fields on the entry — so a
 *  position's stop and target arrive as two more working orders on the opposite
 *  side. Inferred by shape rather than by `linked_basket_ids` because the link
 *  is only populated on some notification paths, and a stop that failed to draw
 *  is worse than one drawn from a sound guess:
 *
 *    long position  → a `stop` below is the stop, a `limit` above is the target
 *    short position → a `stop` above is the stop, a `limit` below is the target
 *
 *  A working order that fits neither is drawn as an ordinary resting order,
 *  which is what it is.
 */
export interface Bracket {
  stop: number | null;
  target: number | null;
  /** Which broker order *is* each leg. A drag has to modify the right one —
   *  Rithmic refuses two bracket operations at once, so "whichever leg" is not
   *  good enough. */
  stopId: string | null;
  targetId: string | null;
  /** Is the stop **Rithmic's to move**? True on a trailing bracket, where the
   *  server re-derives the stop from the high water mark and would put a dragged
   *  one back, wider, on the next tick of profit. The drag is refused on this
   *  rather than attempted — the broker refuses it too, and also catches the
   *  breakeven-only case, which leaves no mark on the leg for this to read. */
  stopManaged: boolean;
  /** Both leg ids, for excluding them from the working-order lines. */
  legs: Set<string>;
}

export function bracketOf(orders: BrokerOrder[], pos: BrokerPosition | null): Bracket {
  const legs = new Set<string>();
  let stopId: string | null = null;
  let targetId: string | null = null;
  let stopManaged = false;
  if (!pos || pos.net === 0) {
    return { stop: null, target: null, stopId, targetId, stopManaged, legs };
  }
  const long = pos.net > 0;
  const entry = pos.avg_price;
  let stop: number | null = null;
  let target: number | null = null;
  for (const o of orders) {
    const px = restingPx(o);
    // Same side as the position: an add, not a leg. A bracket closes.
    if (px == null || isLong(o.side) === long) continue;
    const isStop = o.type === "stop" || o.type === "stop_limit";
    // The type decides which leg it is; the price is a sanity check against the
    // entry, so a resting order that merely happens to be on the closing side
    // (a manual scale-out limit placed the wrong side of entry, say) is not
    // adopted as a bracket. Skipped when the entry is unknown rather than
    // guessed at.
    const protective = entry == null || (long ? px < entry : px > entry);
    const profitable = entry == null || (long ? px > entry : px < entry);
    if (isStop && protective && stop == null) {
      stop = px;
      stopId = o.basket_id;
      stopManaged = (o.trail_by_ticks ?? 0) > 0;
      legs.add(o.basket_id);
    } else if (!isStop && profitable && target == null) {
      target = px;
      targetId = o.basket_id;
      legs.add(o.basket_id);
    }
  }
  return { stop, target, stopId, targetId, stopManaged, legs };
}

/** The broker's working orders as chart lines.
 *
 *  `inert` mirrors what the paper view means by it: this order's own bracket
 *  would not take effect because it adds to, or takes size off, a position whose
 *  bracket already stands. A bracket leg is inert by definition — it *is* the
 *  standing bracket, drawn separately by `positionLine`, and drawing it twice
 *  would put two lines at one price.
 *
 *  The legs on a *working* entry are the bracket it was sent with, measured off
 *  its resting price — the same sketch the replay draws behind a resting order,
 *  and for the same reason: an order carrying 50 ticks of stop is not a bare
 *  line, and drawing it as one is the chart under-reporting the risk you have
 *  actually placed. Rithmic itself says nothing about them until the entry
 *  fills, so this comes from the server's memory of the request
 *  (`stop_ticks`/`target_ticks`), and it is 0 — no sketch — for an order this
 *  process did not send. On a stop entry it is where the legs go *if the fill is
 *  at the trigger*; Rithmic measures them from the real fill, so a gap through
 *  the trigger carries both legs with it.
 */
export function workingViews(
  orders: BrokerOrder[],
  pos: BrokerPosition | null,
  ids: BasketIds,
  tickSize: number,
  /** The **routed** contract's $/point, for the same reason `positionLine` takes
   *  it: the leg chips are dollars, and the tape is not always on the contract
   *  the orders were sent to. Omitted where the two are the same. */
  pointValue?: number,
): WorkingOrderView[] {
  const { legs } = bracketOf(orders, pos);
  const out: WorkingOrderView[] = [];
  for (const o of orders) {
    if (legs.has(o.basket_id)) continue;      // drawn as the position's bracket
    const px = restingPx(o);
    if (px == null) continue;                 // a market order is not resting
    const dir = isLong(o.side) ? 1 : -1;
    const leg = (ticks: number, way: number) =>
      ticks > 0 && tickSize > 0 ? px + way * dir * ticks * tickSize : null;
    out.push({
      id: ids.num(o.basket_id),
      type: o.type === "stop" || o.type === "stop_limit" ? "stop" : "limit",
      side: isLong(o.side) ? "long" : "short",
      // What is left to fill, not what was asked for: a part-filled order rests
      // for its remainder, and drawing the original size overstates it.
      size: o.unfilled || o.qty,
      price: px,
      stop: leg(o.stop_ticks, -1),
      target: leg(o.target_ticks, 1),
      inert: !!pos && pos.net !== 0,
      pointValue,
    });
  }
  return out;
}

/** The broker's position as the chart's position line.
 *
 *  `entryTime` is a bar time, so the caller passes `barAt` (the engine's
 *  instant→bar mapper). When the broker never told us when the position opened
 *  — a process that attached to one already running — the caller's fallback is
 *  used instead of a made-up bar.
 */
export function positionLine(
  pos: BrokerPosition | null,
  orders: BrokerOrder[],
  barAt: (ms: number) => number,
  fallbackMs: number,
  /** The **routed** contract's money, which is not always the chart's: routing
   *  can be pointed at the mini's micro while the tape stays on the mini. The
   *  line carries it so the chips price the position that is actually held —
   *  MNQ at $20 a point is a chip reading ten times the money. */
  contract?: { tickSize: number; pointValue: number },
): PositionLine | null {
  if (!pos || pos.net === 0 || pos.avg_price == null) return null;
  const { stop, target } = bracketOf(orders, pos);
  return {
    side: pos.net > 0 ? "long" : "short",
    size: Math.abs(pos.net),
    entry: pos.avg_price,
    stop,
    target,
    entryTime: barAt(pos.opened_ms ?? fallbackMs),
    tickSize: contract?.tickSize,
    pointValue: contract?.pointValue,
  };
}

/** A paired round trip as the chart's trade mark.
 *
 *  The only one of the three views that needs the bar grid, because a mark is
 *  pinned to a candle at each end — so times are snapped through `barAt`,
 *  exactly as `tradeMark` does for paper. `reason` is passed through as the
 *  server's word for it (`stop` / `target` / `reduce` / `manual`), which is the
 *  same vocabulary `replaySim` uses, so the legend reads the same for both. */
export function tradeViews(
  trades: BrokerTrade[],
  barAt: (ms: number) => number,
): TradeMarkView[] {
  return trades.map((t) => ({
    id: t.id,
    side: t.side,
    size: t.size,
    entryTime: barAt(t.entry_ms),
    entryPrice: t.entry_price,
    exitTime: barAt(t.exit_ms),
    exitPrice: t.exit_price,
    pnl: t.pnl,
    r: t.r,
    reason: t.reason as TradeMarkView["reason"],
  }));
}

/** Enough of the broker's state to know whether the chart needs re-feeding.
 *
 *  The routing poll returns a fresh object every time, so a plain reference
 *  check would re-push every order to the chart twice a second. Same trick, and
 *  same reasoning, as `simSig` for the paper simulation. */
export function brokerSig(
  orders: BrokerOrder[],
  pos: BrokerPosition | null,
  trades: BrokerTrade[] = [],
): string {
  const o = orders
    .map((x) => `${x.basket_id}:${x.price ?? ""}:${x.trigger_price ?? ""}:${x.unfilled}`)
    .join("|");
  // Trades only ever grow, so the count is enough to notice a new one — and it
  // keeps this cheap on a poll that runs twice a second all session.
  return `${o}#${pos ? `${pos.net}:${pos.avg_price ?? ""}:${pos.opened_ms ?? ""}` : ""}#${trades.length}`;
}
