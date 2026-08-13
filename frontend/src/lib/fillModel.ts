// What a fill costs — the difference between practising a read and practising an
// account.
//
// The fill engine (lib/replaySim) resolves *where* an order fills from the tape.
// This is the second half of that question: what the account is charged for
// getting there. Three costs, and they are the three a prop-firm sim account
// charges that a perfect replay does not:
//
//   - commission, per contract and per side. Flat money off every round turn,
//     which is what turns a scratch into a loss and a +1-tick scalp into a
//     losing strategy;
//   - the spread. A market order, and a stop once it triggers, buys the offer or
//     sells the bid — it never trades at the last print. On NQ the book is one
//     tick wide almost all of the time, so crossing it costs exactly one tick,
//     which is why this is a constant and not a draw from a distribution;
//   - the queue. A resting limit is behind everyone who was already there, so a
//     print *at* your price is not your fill — it is the fill of the orders in
//     front of you. Modelled as the tape having to trade past the level before
//     the order is considered done.
//
// And a fourth, which is not a charge but a delay: the gesture does not reach
// the market at the instant you made it. On the replay page the price you click
// is the price at that millisecond; live, it is neither — the print on your
// screen already travelled to you, and your order has to travel back. Modelled
// as one lag applied to every gesture, so an order fills off the print that was
// current when it *landed* rather than the one you were looking at.
//
// Everything else a real fill suffers — partial fills, a book that thins out
// under you — is deliberately not here. The tape is trades, not quotes: there is
// no book to model a queue position against and no way to know what the offer
// was between two prints. What the tape *can* say is where price actually was
// when a level was crossed, and the engine reads the fill off that print rather
// than off the level, so a stop that was jumped through books what it was jumped
// through at. That is where the rest of the slippage comes from, and it costs
// nothing to be honest about.
//
// The model is a setting rather than a constant because none of it is a fact
// about the code: commission is what your firm charges, the two tick knobs are
// what your instrument's book looks like, and the lag is how far you sit from
// the exchange. Set them all to zero and the replay fills exactly as it did
// before any of this existed, which is the right thing for reading the tape and
// the wrong thing for reading your equity curve.

/** The four costs, in the units they are quoted in. */
export interface FillModel {
  /** Dollars per contract, per side. A round turn on one contract is twice this,
   *  and it is charged at the exit — the whole trip is booked when the portion
   *  comes off, so a scale-out pays for the contracts it closed and no more. */
  commission: number;
  /** Ticks a crossing order pays. Applies to a market order and to a stop once
   *  it has triggered, both of which take whatever is offered; never to a limit,
   *  which is the one thing that fills at its own price or not at all. */
  slipTicks: number;
  /** Ticks the tape must trade *past* a resting limit before it fills. One tick
   *  is the conservative reading of "the print at your price was not yours" —
   *  and it is the reading that stops a replay from filling every wick that
   *  kissed a target and turned around. */
  queueTicks: number;
  /**
   * Milliseconds between a gesture and the market acting on it. Charged on
   * everything you do by hand — an order, a drag, a cancel, a flatten — and on
   * nothing the exchange does for you: a bracket's stop and target are resting
   * there already, and the trail that moves them is the plant's, so those
   * trigger on the print that reached them and pay nothing.
   *
   * **It is the round trip, not the one-way hop, and that is not a mistake.**
   * Two delays sit between the price you react to and the price you get, and
   * the replay has neither: the print on your screen left the exchange one hop
   * ago, and your order takes another hop to get back. The distance from the
   * print you clicked to the print that fills you is therefore both hops — the
   * whole round trip — even though the order itself only travels one of them.
   *
   * See docs/research/order-latency.md for the measurement this is set from.
   */
  latencyMs: number;
}

/** The model plus what the instrument is worth — everything a fill needs to be
 *  priced. Assembled by the page from the session header and the saved model. */
export interface FillCfg extends FillModel {
  pointValue: number;
  tickSize: number;
}

/** The account taken out of it: nothing charged, nothing queued. Kept as a named
 *  thing rather than three zeroes at a call site, because it is a deliberate
 *  choice — the practice of reading a tape rather than of running a book.
 *
 *  Not quite the replay as it was before any of this existed, and the difference
 *  is worth knowing: a stop still books the print that crossed it rather than
 *  the level it was set at. That gap is not a cost model, it is the tape saying
 *  where price actually was — waiving it would be inventing a fill at a price
 *  the market leapt over. What zeroes here are the four charges. */
export const PERFECT_FILLS: FillModel = {
  commission: 0, slipTicks: 0, queueTicks: 0, latencyMs: 0,
};

/** What a funded-evaluation NQ account actually charges: $3.50 a side ($7 the
 *  round turn — verified against the archived prop-firm executions, four of
 *  five firms to the cent; see docs/research/fill-model-verification.md), one
 *  tick to cross a one-tick-wide book, one tick of queue.
 *
 *  And a quarter of a second to reach Chicago and back, measured off this
 *  account's own sends rather than assumed: 24 live orders put the wire round
 *  trip at 239 ms median and the browser's own outbound leg at ~10 ms more
 *  (docs/research/order-latency.md). It is the one number here that is a fact
 *  about *this desk* rather than about the instrument or the firm — trade from
 *  a different connection and it is a different number. */
export const DEFAULT_FILL_MODEL: FillModel = {
  commission: 3.5, slipTicks: 1, queueTicks: 1, latencyMs: 250,
};

/** Is this the free ride? Worth saying out loud in the UI — a summary written
 *  under perfect fills is not comparable with one that paid. */
export const isPerfect = (m: FillModel): boolean =>
  m.commission === 0 && m.slipTicks === 0 && m.queueTicks === 0 && m.latencyMs === 0;

/** The whole round trip, in dollars, for `size` contracts. */
export const roundTurn = (m: FillModel, size: number): number => 2 * m.commission * size;

/**
 * What crossing the spread does to a price.
 *
 * `buying` is the direction of the *fill*, not of the position: exiting a long
 * is a sell and pays the same tick the entry paid. Always adverse, never
 * rounded — `slipTicks` is a whole number of ticks, so the result stays on the
 * instrument's grid.
 */
export const cross = (px: number, buying: boolean, cfg: FillCfg): number =>
  cfg.slipTicks > 0 ? px + (buying ? 1 : -1) * cfg.slipTicks * cfg.tickSize : px;

/** How far past a resting limit the tape has to print, in price. */
export const queueGap = (cfg: FillCfg): number => cfg.queueTicks * cfg.tickSize;

// --- persistence ------------------------------------------------------------
//
// Its own key rather than a corner of the Simulator's ticket prefs, because it
// is not a ticket setting: Replay and Live run the same fill engine, and an
// account that charges you $7 a round turn on one page and nothing on the other is not a
// practice account, it is two.

const KEY = "sim.fills";

const num = (v: unknown, min: number, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) && v >= min ? v : fallback;

export function loadFillModel(): FillModel {
  const d = DEFAULT_FILL_MODEL;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...d };
    const s = JSON.parse(raw) as Partial<Record<keyof FillModel, unknown>>;
    // A stored 7 is the default that shipped for the model's first two days —
    // before the prop-firm journal proved $7 was the round turn, not the side
    // (docs/research/fill-model-verification.md) — not a number anyone chose.
    const commission = num(s.commission, 0, d.commission);
    // A model saved before the lag existed has no opinion about it, so it takes
    // the measured default — with one exception, which is the whole reason this
    // is not just a `num` call. A stored *perfect* model was a deliberate choice
    // to take the account out of it, and silently handing it a quarter-second of
    // lag would leave the button reading "Charge fills" over a model that had
    // just started charging one. Missing plus perfect means perfect.
    const latencyMs =
      s.latencyMs === undefined &&
      num(s.commission, 0, 1) === 0 &&
      num(s.slipTicks, 0, 1) === 0 &&
      num(s.queueTicks, 0, 1) === 0
        ? 0
        : num(s.latencyMs, 0, d.latencyMs);
    return {
      commission: commission === 7 ? d.commission : commission,
      // Ticks are counted, so a fractional one would put a fill off the grid.
      slipTicks: Math.floor(num(s.slipTicks, 0, d.slipTicks)),
      queueTicks: Math.floor(num(s.queueTicks, 0, d.queueTicks)),
      // Milliseconds, and whole ones — the tape's own stamps are integers.
      latencyMs: Math.round(latencyMs),
    };
  } catch {
    return { ...d };
  }
}

export function saveFillModel(m: FillModel): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(m));
  } catch {
    // Private mode / quota — the fills still cost what they cost this session.
  }
}
