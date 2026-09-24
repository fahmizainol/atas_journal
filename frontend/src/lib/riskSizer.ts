// Size for risk: you set the dollars, the vol ruler sets the stop, the contract
// count is what falls out.
//
// The ruler is the presets' one (lib/volRuler `PresetRuler`) at whichever bar it
// is toggled to, which is the same reading the A–D brackets are built from —
// deliberately, so that one toggle moves both and the two halves of a ticket
// cannot end up sizing and bracketing off different measurements of the tape.
//
// THE RULE. The stop follows the ruler at 1.0x — a 50-tick read is a 50-tick
// stop — and the size is whatever that stop can carry inside a dollar budget:
//
//     per contract = stopTicks x tickUsd + 2 x commissionPerSide
//     size         = floor(budget / per contract)
//
// Net of fees, not gross. `guardRules.shapeRefusal` computes the same risk
// *gross*, which is a $7 disagreement on a 1 NQ / 50t ticket and the reason
// that ticket reads as exactly $250 there and $257 here. The commission is
// money you lose on the trade; a ceiling that ignores it is a ceiling you are
// already through.
//
// WHY THE STOP FOLLOWS THE RULER, given that this book's geometry has been
// found absolute four times over. Those studies scaled the stop at *fixed
// size*, which changes what you risk — that is a bet on volatility predicting
// the trade, and it does not (docs/research/vol-sizing.md: rho(vol, ticks per
// contract) = +0.05, noise). This is the other thing: the stop moves and the
// size moves against it so the *dollars stay put*. What that buys is not edge
// but consistency of placement — at a fixed 50t stop the median loss ran 0.46x
// the stop on a quiet tape and 1.04x on a hot one, so a fixed distance is too
// far away to ever be reached in one regime and too close to survive in the
// other. A stop that tracks the ruler sits in the same place in the noise every
// day. That is a claim about where the stop *is*, not about what it earns, and
// it is the claim the user is testing by hand.
//
// WHAT THE PRESETS ARE. Three dollar figures — $150, $250, $350 — and nothing
// else. They used to be computed: the daily loss limit divided by how many
// losers the day should absorb (6 / 4 / 3), less a flat $50 cushion. That went
// 2026-09-22, at his call. The arithmetic landed on these same three numbers on
// LucidPro 50K, so no row on the panel moves; what changes is that the numbers
// are now the rule rather than the output of one. A budget derived from an
// account limit travels with the account — a different day limit re-prices every
// row, and a size calibrated by hand over months is not something to re-learn
// per broker. This is what he risks on a trade. It does not follow a firm.
//
// NOTHING IN THE BUDGET COUNTS DOWN, which was already the intent. The divide
// used to read `day_loss_remaining`, so the budgets halved through a bad day and
// the same tape sized smaller the deeper you were in the hole; pinning them to
// the whole limit fixed that in August, and constants finish it. The
// floor-counting half is untouched — `maxLossUsd` is still room-to-the-floor and
// still shrinks, which is what the losers-to-death figure on every cell is for.
// One number counts down, and it is the one whose whole job is to.
//
// Nothing here recommends a target or a trail. The target floor and the
// measured-best 1R exit contradict each other (`guardRules.min_target_ticks`
// refuses under 100; `r_whatif` puts 1R+BE25 as the best cell of the exit
// study), and a recommender that guesses on an open question is worse than one
// that stays quiet.

import { MICRO_RATIO, microCommission } from "./contracts";

/** How far the stop sits from the ruler's reading. One, for now — the user is
 *  testing the multiplier by hand and will report back. */
export const STOP_MULT = 1.0;

export type Appetite = "safe" | "moderate" | "aggressive";

/** What each appetite risks on one trade, in dollars.
 *
 *  This is the whole content of the three presets, and it is what the panel puts
 *  on the row, because there is nothing behind it left to explain — which is the
 *  point of them being constants rather than a derivation.
 *
 *  **Hand-chosen, measured against nothing**, like `LEG_ROOM` and like the $50
 *  cushion they absorbed. They are where the old computed rule landed on
 *  LucidPro 50K ($1,200 ÷ 6 / 4 / 3, less $50), kept at those figures because
 *  that is the size he has actually been trading. */
export const BUDGETS_USD: Record<Appetite, number> = {
  safe: 150,
  moderate: 250,
  aggressive: 350,
};

export const APPETITES: Appetite[] = ["safe", "moderate", "aggressive"];

/** LucidPro 50K's account floor, for the pages with no account view to ask — a
 *  drill, or a replay before the account has answered. Mirrors
 *  `replay_account.MAX_LOSS`. A page *with* an account passes its own
 *  room-to-the-floor, which counts down; this is only the fallback it starts
 *  from. There is no day-loss twin any more: the budgets are constants, and the
 *  sizer has stopped asking what the day limit is. */
export const DEFAULT_MAX_LOSS = 2_000;

/** One way to take the trade: some number of contracts, and what it costs to be
 *  wrong.
 *
 *  The two routes are kept whole rather than blended. Topping a mini up with
 *  the micros its remainder could buy spends the budget more exactly and reads
 *  terribly — "2 NQ + 1" is a stub that adds 5% of the exposure and a whole
 *  extra round turn of fees. The mini route is the clean answer and the micro
 *  route is the granular one; choosing between them is the actual decision. */
export interface Route {
  minis: number;
  micros: number;
  /** Net of both sides' commission, at these contracts, if the stop is hit. */
  riskUsd: number;
  /** Exposure in mini-equivalents, for comparing the two routes. */
  exposure: number;
  /** Round-turn commission — the cost of choosing this route over the other.
   *  Micros are not discounted to a tenth (see contracts.MICRO_COMMISSION_FLOOR),
   *  so the micro route pays ~1.4x the mini's fees per unit of exposure. */
  feesUsd: number;
  /** Losing trades at this size before the account's own limit is gone. A
   *  property of the route rather than of the row: the two routes spend the
   *  same budget but rarely the same money, so they rarely buy the same number
   *  of mistakes. This is the only figure on the panel that connects a ticket
   *  to the floor, which is why it is on every one of them. */
  losersToDeath: number;
}

export interface PresetRow {
  appetite: Appetite;
  /** The budget this row spends, which is also the name of the row. */
  budgetUsd: number;
  stopTicks: number;
  /** Minis first, micros for the remainder. Null when not a single mini fits —
   *  which is the honest way for a hot tape to say "not in this contract". */
  mini: Route | null;
  /** The all-micro route. Null when the account's micro cap cannot reach one. */
  micro: Route | null;
  /** The guards would refuse this stop as too wide. Flagged, never clamped: the
   *  ruler said what it said, and a stop quietly shrunk to fit a rule is a stop
   *  you did not choose. */
  overStopCeiling: boolean;
}

export interface SizerInput {
  /** The presets' ruler reading, in ticks, at whichever bucketing is selected
   *  (lib/volRuler `PresetRuler`) — the same number the A–D brackets are built
   *  from, so the two halves of a ticket cannot measure the tape differently.
   *
   *  Not the drawn timeframe's ATR, which is what this used to read: that answers
   *  "how big are the bars I am looking at", and it moved every time the chart
   *  changed timeframe. A stop rule has no business depending on the zoom. */
  volTicks: number;
  /** $ per tick for one **mini** — tickSize x pointValue. */
  tickUsd: number;
  /** The mini's per-side commission. The micro's is derived, not assumed. */
  commissionPerSide: number;
  /** What the account dies at, for the losers-to-death column. */
  maxLossUsd: number;
  caps: { minis: number; micros: number };
  /** The guards' stop ceiling, for the flag. Zero or absent = no ceiling. */
  stopTicksMax?: number;
}

/** What one contract costs to be wrong about, net of both sides' fees. */
function perContract(stopTicks: number, tickUsd: number, commission: number): number {
  return stopTicks * tickUsd + 2 * commission;
}

function route(minis: number, micros: number, i: SizerInput, stopTicks: number): Route {
  const microComm = microCommission(i.commissionPerSide);
  const microTick = i.tickUsd / MICRO_RATIO;
  const fees = 2 * (minis * i.commissionPerSide + micros * microComm);
  const riskUsd =
    minis * perContract(stopTicks, i.tickUsd, i.commissionPerSide) +
    micros * perContract(stopTicks, microTick, microComm);
  return {
    minis,
    micros,
    riskUsd,
    exposure: minis + micros / MICRO_RATIO,
    feesUsd: fees,
    losersToDeath: riskUsd > 0 ? Math.floor(i.maxLossUsd / riskUsd) : 0,
  };
}

/**
 * The three preset rows for one vol reading.
 *
 * Returns rows even when nothing fits — a row whose two routes are both null is
 * the panel's way of saying the tape has outgrown this budget, and that is a
 * recommendation rather than an error state.
 */
export function presetsFor(i: SizerInput): PresetRow[] {
  const stopTicks = Math.max(1, Math.round(i.volTicks * STOP_MULT));
  const microComm = microCommission(i.commissionPerSide);
  const microTick = i.tickUsd / MICRO_RATIO;
  const perMini = perContract(stopTicks, i.tickUsd, i.commissionPerSide);
  const perMicro = perContract(stopTicks, microTick, microComm);

  return APPETITES.map((appetite) => {
    const budgetUsd = BUDGETS_USD[appetite];

    // The mini route: whole minis, as many as the budget carries. Capped by the
    // account, which is why the cap is read here rather than trusted to the
    // ticket. Null when not one fits — a hot tape saying "not in this contract".
    let mini: Route | null = null;
    if (perMini > 0 && i.caps.minis >= 1) {
      const m = Math.min(Math.floor(budgetUsd / perMini), i.caps.minis);
      if (m >= 1) mini = route(m, 0, i, stopTicks);
    }

    let micro: Route | null = null;
    if (perMicro > 0 && i.caps.micros >= 1) {
      const u = Math.min(Math.floor(budgetUsd / perMicro), i.caps.micros);
      if (u >= 1) micro = route(0, u, i, stopTicks);
    }

    return {
      appetite,
      budgetUsd,
      stopTicks,
      mini,
      micro,
      overStopCeiling: !!i.stopTicksMax && stopTicks > i.stopTicksMax,
    };
  });
}

/**
 * The widest stop a size you have already decided on can carry inside a budget
 * — the inverse, and the thing that makes "no route fits" actionable. Without
 * it the panel can only say no; with it, it can say what would let you in.
 *
 * Returns 0 when even a zero-width stop costs more in commission than the
 * budget allows, which is a real answer on a micro-sized budget.
 */
export function stopThatFits(
  budgetUsd: number,
  minis: number,
  micros: number,
  tickUsd: number,
  commissionPerSide: number,
): number {
  const microComm = microCommission(commissionPerSide);
  const fees = 2 * (minis * commissionPerSide + micros * microComm);
  const perTick = minis * tickUsd + (micros * tickUsd) / MICRO_RATIO;
  if (perTick <= 0) return 0;
  return Math.max(0, Math.floor((budgetUsd - fees) / perTick));
}

/** A route as the panel writes it: "1 NQ", "7 MNQ", "1 NQ + 5", or null. */
export function routeLabel(r: Route | null, root: string, microRoot: string): string | null {
  if (!r) return null;
  if (r.minis === 0) return `${r.micros} ${microRoot}`;
  if (r.micros === 0) return `${r.minis} ${root}`;
  return `${r.minis} ${root} + ${r.micros}`;
}
