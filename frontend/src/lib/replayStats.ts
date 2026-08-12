// What an attempt came to: the aggregates over a replay's trades, and the way
// several attempts pool into a track record.
//
// Computed here rather than on the server on purpose. The fill engine only
// exists in replaySim.ts, so a second implementation in Python could disagree
// with it about a fill; the API stores what this produces and never re-derives
// it. See src/journal/replays.py.
//
// The summary splits in two, and the split is what makes pooling honest:
//
//   - `Totals` are *additive*. Every field is a count or a sum, so pooling a
//     year of attempts is adding them up;
//   - everything else is *derived* from those totals by `derive`. Win rate,
//     profit factor and expectancy are ratios, and averaging ratios across
//     attempts is not the same number as the ratio of the sums — the second is
//     the true one, so the pooled view recomputes rather than averages.
//
// Yardsticks, in the order they mean something:
//
//   - dollars are primary — it is what the HUD shows while you trade;
//   - points are size-blind, and comparable to the strategy sim's NQ numbers;
//   - R is stake R (`Trade.rCash`), the one that sums. It carries its own
//     denominator `n_with_r`, because a trade taken without a stop has no risk
//     to measure against and averaging it in as a zero would quietly flatter
//     every naked entry. Excursion R (`Trade.r`) is deliberately not totalled:
//     every portion of one scaled position reports the same geometry.
//
// Win rate follows journal.metrics: wins over *all* trades, scratches included
// in the denominator, expressed as a percentage — so a replay's number can sit
// next to a real one without a conversion.

import type { ExitReason, Log, Trade } from "./replaySim";

/** Bumped by hand when replaySim's fill semantics change. Stored on every
 *  attempt so a rebuild under new rules is never mistaken for the numbers you
 *  actually traded. */
export const SIM_ENGINE_VERSION = 2;

/** Below this many trades, a win rate is a coin flip with an opinion. The
 *  headline KPIs stay visible but say so — 12 of the last 13 gates this repo
 *  tested died of exactly this. */
export const MIN_SAMPLE = 20;

/** 95% two-sided. */
const Z = 1.959963985;

export interface Totals {
  trades: number;
  wins: number;
  losses: number;
  /** Exactly flat. Counted apart, but still in the win-rate denominator. */
  scratches: number;
  longs: number;
  shorts: number;
  /** Contracts closed, summed over trades — the size the attempt actually did. */
  contracts: number;
  /** Net of commission — the number the account would have printed. */
  net_usd: number;
  /** Commission paid, always ≥ 0. `net_usd + fees_usd` is what the same trades
   *  would have made under perfect fills *minus the spread*, which is not
   *  recoverable from here: the spread is inside the fill prices, so it lives in
   *  `net_points` and cannot be added back. Commission can, which is exactly why
   *  it is the one cost carried separately. */
  fees_usd: number;
  net_points: number;
  /** Sum of stake R over the trades that had risk on. */
  net_r: number;
  /** How many trades that sum is over. Never assume it equals `trades`. */
  n_with_r: number;
  gross_win_usd: number;
  /** Negative, like journal.metrics — the sum of the losing trades. */
  gross_loss_usd: number;
  /** Total time held, over all trades. Averages come out of `derive`. */
  hold_ms: number;
  best_usd: number | null;
  worst_usd: number | null;
  by_reason: Record<ExitReason, number>;
}

export interface Derived {
  /** Percent, over all trades. Null with no trades to divide by. */
  win_rate: number | null;
  /** Wilson score interval on the win rate, in percent. Narrow only when the
   *  sample earns it — which is the whole reason it's here. */
  win_rate_lo: number | null;
  win_rate_hi: number | null;
  profit_factor: number | null;
  expectancy_usd: number | null;
  expectancy_points: number | null;
  /** Per trade *that had risk on*, so it divides by n_with_r. */
  expectancy_r: number | null;
  avg_win_usd: number | null;
  avg_loss_usd: number | null;
  avg_hold_s: number | null;
}

/** What the attempt did that isn't a trade: the orders, the do-overs, the span
 *  of clock it covered. */
export interface Activity {
  orders_placed: number;
  orders_cancelled: number;
  order_edits: number;
  bracket_edits: number;
  rewinds: number;
  discarded_trades: number;
  first_fill_ms: number | null;
  last_exit_ms: number | null;
  clock_start_ms: number | null;
  clock_end_ms: number | null;
}

export type AttemptSummary = Totals & Derived & Activity;

const REASONS: ExitReason[] = ["manual", "stop", "target", "reduce", "trail"];

export function emptyTotals(): Totals {
  return {
    trades: 0,
    wins: 0,
    losses: 0,
    scratches: 0,
    longs: 0,
    shorts: 0,
    contracts: 0,
    net_usd: 0,
    fees_usd: 0,
    net_points: 0,
    net_r: 0,
    n_with_r: 0,
    gross_win_usd: 0,
    gross_loss_usd: 0,
    hold_ms: 0,
    best_usd: null,
    worst_usd: null,
    by_reason: Object.fromEntries(REASONS.map((r) => [r, 0])) as Record<ExitReason, number>,
  };
}

/**
 * The Wilson score interval, in percent.
 *
 * Not the textbook normal approximation: at the sample sizes a practice log
 * actually reaches, that one runs off both ends of [0,1] and reports a lower
 * bound below zero on a good week. Wilson stays inside the interval and stays
 * sane at n = 3, which is where these numbers live for the first month.
 */
export function wilson(wins: number, n: number): [number, number] | null {
  if (n <= 0) return null;
  const p = wins / n;
  const z2 = Z * Z;
  const denom = 1 + z2 / n;
  const centre = (p + z2 / (2 * n)) / denom;
  const half = (Z / denom) * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return [Math.max(0, centre - half) * 100, Math.min(1, centre + half) * 100];
}

export function derive(t: Totals): Derived {
  const n = t.trades;
  const ci = wilson(t.wins, n);
  const grossLoss = Math.abs(t.gross_loss_usd);
  return {
    win_rate: n ? (t.wins / n) * 100 : null,
    win_rate_lo: ci ? ci[0] : null,
    win_rate_hi: ci ? ci[1] : null,
    // Infinity is a real answer here — a set of trades with no losing one — but
    // it isn't a number the UI can rank, so it reads as null and prints as "—".
    profit_factor: n && grossLoss > 0 ? t.gross_win_usd / grossLoss : null,
    expectancy_usd: n ? t.net_usd / n : null,
    expectancy_points: n ? t.net_points / n : null,
    expectancy_r: t.n_with_r ? t.net_r / t.n_with_r : null,
    avg_win_usd: t.wins ? t.gross_win_usd / t.wins : null,
    avg_loss_usd: t.losses ? t.gross_loss_usd / t.losses : null,
    avg_hold_s: n ? t.hold_ms / n / 1000 : null,
  };
}

export function totalsOf(trades: Trade[]): Totals {
  const t = emptyTotals();
  for (const tr of trades) {
    t.trades += 1;
    t.contracts += tr.size;
    if (tr.pnl > 0) t.wins += 1;
    else if (tr.pnl < 0) t.losses += 1;
    else t.scratches += 1;
    if (tr.side === "long") t.longs += 1;
    else t.shorts += 1;
    t.net_usd += tr.pnl;
    // Older stored attempts have no `fees` on their trades (engine version 1
    // charged none), so this reads as zero for them rather than as NaN.
    t.fees_usd += tr.fees ?? 0;
    t.net_points += tr.pts;
    if (tr.rCash != null) {
      t.net_r += tr.rCash;
      t.n_with_r += 1;
    }
    if (tr.pnl > 0) t.gross_win_usd += tr.pnl;
    else t.gross_loss_usd += tr.pnl;
    t.hold_ms += Math.max(0, tr.exitMs - tr.entryMs);
    t.best_usd = t.best_usd == null ? tr.pnl : Math.max(t.best_usd, tr.pnl);
    t.worst_usd = t.worst_usd == null ? tr.pnl : Math.min(t.worst_usd, tr.pnl);
    t.by_reason[tr.reason] = (t.by_reason[tr.reason] ?? 0) + 1;
  }
  return t;
}

/** A seek that erased fills. Kept because the alternative is a track record
 *  that quietly improves every time you rewind out of a bad trade. */
export interface RewindEvent {
  /** Clock it was rewound from, and to. */
  from_ms: number;
  to_ms: number;
  /** How many booked trades that seek un-happened. */
  dropped: number;
}

export function summarize(
  trades: Trade[],
  log: Log,
  ctx: {
    rewinds: RewindEvent[];
    discarded: Trade[];
    clockStartMs: number | null;
    clockEndMs: number | null;
  },
): AttemptSummary {
  const totals = totalsOf(trades);
  return {
    ...totals,
    ...derive(totals),
    orders_placed: log.orders.length,
    orders_cancelled: log.orders.filter((o) => o.cancelMs != null).length,
    order_edits: log.orders.reduce((a, o) => a + o.edits.length, 0),
    bracket_edits: log.brackets.length,
    rewinds: ctx.rewinds.length,
    discarded_trades: ctx.discarded.length,
    first_fill_ms: trades.length ? trades[0].entryMs : null,
    last_exit_ms: trades.length ? trades[trades.length - 1].exitMs : null,
    clock_start_ms: ctx.clockStartMs,
    clock_end_ms: ctx.clockEndMs,
  };
}

/** Pool many attempts into one record. Sums the sums, then re-derives the
 *  ratios from them — see the note at the top about why this is not an average
 *  of the per-attempt numbers. */
export function pool(summaries: Partial<AttemptSummary>[]): Totals & Derived {
  const t = emptyTotals();
  for (const s of summaries) {
    t.trades += s.trades ?? 0;
    t.wins += s.wins ?? 0;
    t.losses += s.losses ?? 0;
    t.scratches += s.scratches ?? 0;
    t.longs += s.longs ?? 0;
    t.shorts += s.shorts ?? 0;
    t.contracts += s.contracts ?? 0;
    t.net_usd += s.net_usd ?? 0;
    t.fees_usd += s.fees_usd ?? 0;
    t.net_points += s.net_points ?? 0;
    t.net_r += s.net_r ?? 0;
    t.n_with_r += s.n_with_r ?? 0;
    t.gross_win_usd += s.gross_win_usd ?? 0;
    t.gross_loss_usd += s.gross_loss_usd ?? 0;
    t.hold_ms += s.hold_ms ?? 0;
    if (s.best_usd != null) t.best_usd = t.best_usd == null ? s.best_usd : Math.max(t.best_usd, s.best_usd);
    if (s.worst_usd != null) t.worst_usd = t.worst_usd == null ? s.worst_usd : Math.min(t.worst_usd, s.worst_usd);
    for (const r of REASONS) t.by_reason[r] += s.by_reason?.[r] ?? 0;
  }
  return { ...t, ...derive(t) };
}
