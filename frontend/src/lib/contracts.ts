// The mini and its micro — the one contract fact the front end has to know on
// its own.
//
// Every other instrument fact reaches the client from the server: a session
// payload carries the `tick_size` and `point_value` of the contract whose ticks
// it decoded (api/routers/simulator.py), and live routing carries the same pair
// for the contract orders are actually sent to (`RoutingStatus`). Neither can
// answer the question this module exists for, which is what the *same tape*
// would be worth in a contract nobody has ticks for.
//
// There is only one MNQ tick store and it is empty: the Databento corpus is NQ,
// and Rithmic records the contract the socket subscribed to. Asking the session
// endpoint for MNQU6 404s at `cached_rth`. But MNQ and NQ track the same index
// to within a tick and the book is a tick wide on both, so NQ ticks *are* the
// MNQ tape — everything except the money is identical. That makes trading the
// micro in replay a re-pricing rather than a different data set, and this is
// where the re-pricing is written down.
//
// Mirrors `CONTRACT_SPECS` in src/journal/config.py and the scaling in
// journal/live/broker.py — deliberately as a ratio and a symbol map rather than
// a second copy of the specs, because the ratio is a fact about the contracts
// (which does not change) while the dollars are a fact about the instrument
// (which arrives from the server, already right).

import type { FillCfg } from "./fillModel";

/** A micro is a tenth of its mini. True of NQ/MNQ, ES/MES, YM/MYM and RTY/M2K
 *  alike — the exchange lists micros at 1/10 notional, and it is the same 1/10
 *  `broker.py` scales the commission rate by. */
export const MICRO_RATIO = 10;

/** Which micro belongs to which mini. Roots, not contracts: the month and year
 *  are the same on both legs of every pair, so the caller keeps them.
 *
 *  A map rather than `"M" + root` because RTY's micro is M2K, not MRTY — the
 *  one pair where the obvious rule is wrong is exactly the one a rule would get
 *  silently wrong. Anything not listed has no micro as far as this app knows,
 *  and the caller must handle that rather than invent a symbol. */
const MICROS: Record<string, string> = {
  NQ: "MNQ",
  ES: "MES",
  YM: "MYM",
  RTY: "M2K",
};

/** This root's micro, or null when it has none (or is already one). Null is a
 *  real answer — it is what takes the choice off the screen. */
export const microOf = (root: string): string | null =>
  MICROS[root.toUpperCase()] ?? null;

/** The least a broker charges for a micro round-turn side. Brokers do not
 *  discount a micro to a tenth of a mini; the floor is the measured rate, and
 *  clamping up is the safe direction — a commission estimated too low is the
 *  one that lets a day run past its loss stop. Same constant, same reasoning,
 *  as `MICRO_COMMISSION_FLOOR` in journal/live/broker.py. */
export const MICRO_COMMISSION_FLOOR = 0.5;

/**
 * A mini's per-side commission, charged as a micro.
 *
 * Zero passes through untouched, which the server's version has no need to say:
 * the replay's fill model can be switched off entirely (`PERFECT_FILLS`), and a
 * free fill that started charging fifty cents the moment you picked the micro
 * would be the toggle quietly overruling the choice above it.
 */
export const microCommission = (perSide: number): number =>
  perSide > 0 ? Math.max(MICRO_COMMISSION_FLOOR, perSide / MICRO_RATIO) : 0;

/**
 * The same fill model, charged as the micro of whatever contract it describes.
 *
 * Two of the four numbers move and two do not, which is the whole content of
 * "it is the same trade in a smaller contract": the money per point is a tenth
 * and the commission is billed at the micro rate, while the tick grid and the
 * two tick-denominated costs are identical — a micro's book is a tick wide the
 * same way its mini's is. So a fill resolves to the same *price* either way and
 * only what it is worth changes.
 *
 * Takes and returns a whole `FillCfg` so callers hold one object rather than
 * remembering which of its fields the contract touches.
 */
export const asMicro = (cfg: FillCfg): FillCfg => ({
  ...cfg,
  pointValue: cfg.pointValue / MICRO_RATIO,
  commission: microCommission(cfg.commission),
});
