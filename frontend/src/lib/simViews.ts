// Turning the simulation into what the chart and the panels draw.
//
// Pure mappers, no React and no chart handles: a `SimState` in, the view types
// the overlays take out. They live here because Replay and Live are one trading
// surface with two clocks — the same position, the same working orders and the
// same closed trades get drawn the same way whether the tape is finished or
// still arriving. A second copy of `orderView` for the live page would be a
// second set of netting rules to keep in step with replaySim.
//
// (This is the first of lib/simViews.ts's targets in docs/live-shadow-plan.md
// § 1b — the pure half, which `tsc -b` verifies outright. The rAF loop and the
// bootstrap effect are the half that needs someone at a keyboard, and they are
// deliberately not here.)

import { MICRO_RATIO } from "./contracts";
import type { PositionLine } from "../components/charts/ReplayChart";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import type { TradeMarkView } from "../components/charts/TradesPrimitive";
import {
  orderStateAt,
  workingOrders,
  type OrderRec,
  type Position,
  type Side,
  type SimState,
  type Trade,
} from "./replaySim";

/** Snap an instant onto the bar it happened in. Asked of the engine rather than
 *  computed, because the grid is whatever the current timeframe made it — and on
 *  tick bars there is no grid to compute against at all. */
export type BarAt = (ms: number) => number;

/** What a position or order in the micro is worth a point, given the tape's own
 *  figure. Undefined for anything in the tape's contract — which is what the
 *  overlays read as "price this with the tape", and is the ordinary case.
 *
 *  Here rather than at the call sites because it is the same question `posLine`
 *  and `orderView` both have to answer, and answering it twice is how the chips
 *  on a position come to disagree with the chips on the order that opened it. */
const chipPv = (micro: boolean, tapePv?: number): number | undefined =>
  micro && tapePv != null ? tapePv / MICRO_RATIO : undefined;

/** What the chart draws for an open position. The bracket is the position's own,
 *  so unlike a working order there is no log to resolve it through.
 *
 *  `tapePointValue` is the tape contract's $/point. Needed only to price a
 *  position held in something else — the micro of it (see lib/contracts) — and
 *  omitting it simply leaves that to the chart, which knows the tape's. */
export const posLine = (p: Position, barAt: BarAt, tapePointValue?: number): PositionLine => ({
  side: p.side,
  size: p.size,
  entry: p.entryPrice,
  entryTime: barAt(p.fillMs),
  stop: p.stop,
  target: p.target,
  pointValue: chipPv(p.micro, tapePointValue),
});

/** A closed trade as the chart wants it: on the bar grid, prices only. Times are
 *  snapped to the bar they happened in — a mark is pinned to a candle, so the bar
 *  is the finest the chart can honestly place it at, whatever the bar is. */
export const tradeMark = (t: Trade, barAt: BarAt): TradeMarkView => ({
  id: t.id,
  side: t.side,
  size: t.size,
  entryTime: barAt(t.entryMs),
  entryPrice: t.entryPrice,
  exitTime: barAt(t.exitMs),
  exitPrice: t.exitPrice,
  pnl: t.pnl,
  // Stake R on the tape, to go with the dollars already printed there. The
  // blotter is where the two R's get compared; a chart label has room for one.
  r: t.rCash,
  reason: t.reason,
});

/** One row of the blotter, whichever clock filled it.
 *
 *  The paper simulation and the broker record a closed round trip differently —
 *  different casing, different nullability, and the broker's `pnl` is gross
 *  where the simulation's is net. This is what they are both mapped *to*, so
 *  that Replay and Live can draw the same card (`components/charts/Blotter`)
 *  rather than keeping two row layouts in step by hand. `brokerViews.blotterRows`
 *  is the other mapper.
 *
 *  Nullable wherever a source can honestly not know: a trade opened bare has no
 *  risk to divide by, and a row restored from the journal has neither that nor
 *  its commission (see `booking.day_trades`). Null prints as "—", never as 0. */
export interface BlotterRow {
  id: number;
  /** When the **position** this row came off opened — the same instant on every
   *  scale-out of one entry, which is what makes `replaySim.openStamps` able to
   *  count the day's trades off a list of rows. Not shown; carried so the two
   *  sources cannot be counted by two different rules. */
  entryMs: number;
  side: Side;
  size: number;
  /** The badge to draw beside the size, or null for the ordinary case where the
   *  row is in the contract orders go to now. Resolved by the mapper because
   *  only the caller knows what "now" is — and in a review it is nothing, so
   *  every row would otherwise be badged as differing from a selection that is
   *  not being offered. */
  contract: string | null;
  /** `market`, `limit`, `stop` — how the position was opened. Null when the
   *  source cannot say. */
  openType: string | null;
  reason: string;
  entryPrice: number;
  exitPrice: number;
  /** **Net**, both sources. `fees` is what is already out of it. */
  pnl: number;
  fees: number | null;
  /** Excursion R (size-blind) and stake R (dollars over dollars). They part
   *  company only when size changed mid-trade, which is when both are worth
   *  printing. */
  r: number | null;
  rCash: number | null;
  /** The dollars the entry staked — `rCash`'s denominator, and the figure the
   *  order pad's sizer quoted before it went on. The *position's*: every
   *  scale-out of one entry repeats it, so it is never totalled. */
  riskUsd: number | null;
}

/** A closed paper trade as a blotter row. `contract` is decided by the caller
 *  and passed in, for the reason `BlotterRow.contract` gives. */
export const blotterRow = (t: Trade, contract: string | null = null): BlotterRow => ({
  id: t.id,
  entryMs: t.entryMs,
  side: t.side,
  size: t.size,
  contract,
  openType: t.openType,
  reason: t.reason,
  entryPrice: t.entryPrice,
  exitPrice: t.exitPrice,
  pnl: t.pnl,
  fees: t.fees,
  r: t.r,
  rCash: t.rCash,
  riskUsd: t.riskUsd,
});

/** A working order as the chart and the panel want it: levels resolved to the
 *  prices they stand at now, not the log they were reached through.
 *
 *  Whether the bracket it carries would actually take effect is settled against
 *  the position as it stands: an order's legs open a position or they do
 *  nothing, so one that would add to a position, or take size off it, is
 *  carrying a bracket that is going nowhere. Marked rather than removed — it
 *  becomes live again the moment the position it was measured against is gone.
 *  Mirrors the netting rules in replaySim. */
export const orderView = (
  o: OrderRec,
  ms: number,
  open: Position | null,
  /** The tape contract's $/point — see `posLine`. */
  tapePointValue?: number,
): WorkingOrderView => {
  const s = orderStateAt(o, ms);
  return {
    id: o.id,
    // Only resting orders ever work; a market order is its own fill.
    type: o.type === "stop" ? "stop" : "limit",
    side: o.side,
    size: o.size,
    price: s.price ?? 0,
    stop: s.stop,
    target: s.target,
    // Flat, or big enough to run the position through and out the other side:
    // either way the fill opens a position, and these are its legs.
    inert: !!open && !(o.side !== open.side && o.size > open.size),
    pointValue: chipPv(!!o.micro, tapePointValue),
  };
};

/** Enough of the simulation to know whether the panel needs re-rendering. The
 *  frame loop runs 60× a second and almost every frame changes nothing. The
 *  position's size and average are in it because a fill that scales one in or
 *  out changes neither the trade count nor the working set. */
export const simSig = (st: SimState): string => {
  const p = st.open;
  // The bracket is part of it, not just the size and the average. Everything the
  // *user* does to a level arrives through the log and republishes anyway, so
  // this used to be redundant — but the trail moves the stop from inside the
  // walk, and a ratchet that doesn't show up here is a ratchet the chart never
  // redraws. The line then sits at the old level until the trade closes on a
  // stop that appears to be somewhere else entirely.
  return `${st.trades.length}|${p ? `${p.side}${p.size}@${p.entryPrice}/${p.stop}/${p.target}` : "-"}|${workingOrders(
    st,
  )
    .map((o) => o.id)
    .join(".")}`;
};

// --- formatting -------------------------------------------------------------

export const fmtPts = (p: number): string => p.toFixed(2);
export const fmtUsd = (v: number): string =>
  (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
export const fmtR = (r: number | null): string =>
  r == null ? "—" : `${r >= 0 ? "+" : ""}${r.toFixed(2)}R`;
/** Wall clock. Tape times are the epoch-ms of the display zone's own clock, so
 *  the UTC fields of a Date built from one *are* that wall clock. */
export const fmtClock = (ms: number): string =>
  Number.isFinite(ms) && ms > 0 ? new Date(ms).toISOString().slice(11, 19) : "—";
/** A short duration, m:ss — what the bar-close countdown reads in. Floors the
 *  seconds so the display counts …3, 2, 1, 0 and never shows the next bar's
 *  full span a frame early. */
export const fmtCountdown = (ms: number): string => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
