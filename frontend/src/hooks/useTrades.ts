import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { Note, TradeRow } from "../lib/types";
import type { Bar } from "../lib/chartTypes";
import type { LevelCandidate } from "./useReplays";

/** One fill's measured relationship to one family of chart levels.
 *
 * `rank` is the whole point: the fraction of comparable moments in that session
 * that sat closer to the family than the fill did. 0 means tighter than every one
 * of them, 0.5 is what chance produces. Raw distance is *not* evidence — with two
 * dozen levels drawn, every price is near something. */
export interface TradeLevel {
  anchor: "entry" | "exit";
  family: string;
  member: string | null;
  /** What to call it — off the *member*, so "GX VAH" rather than the pooled
   *  "value high (VAH)". Server-side (`level_tag.label_for`) because the client
   *  used to keep its own copy of the map and had to be kept equal by hand. */
  label: string;
  rank: number;
  dist_ticks: number | null;
}

/** What price did before the entry and after the exit, in index points signed to
 * the trade's own direction: positive always means "price went the way the trade
 * wanted", whichever side it was on.
 *
 * READ `post_avail_s` BEFORE BELIEVING A ZERO. It is how many seconds of tape the
 * window actually got — a trade exited near the end of the session has no 30
 * minutes to follow through in, and its zero is the clock running out rather than
 * a market standing still.
 *
 * Strictly outside the hold: entry-to-exit MAE/MFE is the excursion payload, on
 * minute bars, and two numbers with one name is how a journal starts lying. */
export interface TradeContext {
  symbol: string | null;
  /** Points per tick of the contract that was measured. Sent rather than assumed
   * so the strip can turn the points above into ticks — and into multiples of a
   * bar — without hardcoding an instrument. */
  tick_size: number | null;
  method: string;
  computed_at: string | null;
  pre_avail_s: number | null;
  post_avail_s: number | null;
  /** Net move into the fill. POSITIVE MEANS YOU CHASED. */
  pre_run_pts_1m: number | null;
  pre_run_pts_5m: number | null;
  pre_run_pts_15m: number | null;
  pre_run_pts_30m: number | null;
  pre_range_pts_15m: number | null;
  pre_range_pts_30m: number | null;
  /** Where the fill sat in the approach's range, 0 = its low, 1 = its high. RAW,
   * not direction-signed — buying the high and selling it are different trades. */
  pre_loc_15m: number | null;
  pre_loc_30m: number | null;
  /** The best the trade could have done had it stayed on... */
  post_mfe_pts_1m: number | null;
  post_mfe_pts_5m: number | null;
  post_mfe_pts_15m: number | null;
  post_mfe_pts_30m: number | null;
  /** ...and what holding for it would have meant sitting through first. MFE
   * without MAE is half a claim. */
  post_mae_pts_1m: number | null;
  post_mae_pts_5m: number | null;
  post_mae_pts_15m: number | null;
  post_mae_pts_30m: number | null;
  post_mfe_close_pts: number | null;
  post_close_pts: number | null;
  /** Fraction of the next 30 minutes whose price the exit beat. 1 = nothing
   * traded better; 0 = it only ever got better without you. */
  exit_rank: number | null;
  /** Seconds until price offered the entry again. Null = it never did. */
  post_ret_entry_s: number | null;
  /** How big the bars were around the fill, in TICKS, at the three resolutions
   * actually traded on. ATR(14) is what the chart's vol ruler showed at the
   * entry; the median range over the 30-minute approach is the same reading
   * without a single bar able to yank it. Null = too few bars to say. */
  vol_atr_ticks_500t: number | null;
  vol_atr_ticks_30s: number | null;
  vol_atr_ticks_1m: number | null;
  vol_med_ticks_500t: number | null;
  vol_med_ticks_30s: number | null;
  vol_med_ticks_1m: number | null;
  /** The bar the fill landed in. The body is SIGNED TO THE TRADE — positive
   * means the bar was going the trade's way — because "green" means opposite
   * things to a long and a short. */
  eb_body_ticks_500t: number | null;
  eb_body_ticks_30s: number | null;
  eb_body_ticks_1m: number | null;
  /** Where in that bar the fill sat, 0 = its low, 1 = its high. RAW, like the
   * approach locations above. */
  eb_loc_500t: number | null;
  eb_loc_30s: number | null;
  eb_loc_1m: number | null;
  /** How much of the bar had printed when the order filled, 0 to 1. The honest
   * form of "was the candle closed": off the tape every bar is closed, and what
   * differed live is how much of it you had seen. */
  eb_elapsed_500t: number | null;
  eb_elapsed_30s: number | null;
  eb_elapsed_1m: number | null;
}

export interface TradeDetail {
  trade: TradeRow;
  note: string;
  tags: string[];
  setups: string[];
  confluences: string[];
  /** Measured, never claimed — and deliberately not the same thing as `tags`.
   * Sent whole (every family, qualifying or not), so an empty array means the
   * measurement never ran, not that the fill was near nothing. */
  levels: TradeLevel[];
  /** The same offer the replay/drill review gets (level_store.candidates_for):
   * entry-anchor levels as picker options, nearest first. What the journal
   * form's watched-level chips render. */
  level_candidates: LevelCandidate[];
  levels_at_rank: number;
  /** Null means the windows were never measured (no cached tape for that
   * session), which is not the same as nothing having happened in them. */
  context: TradeContext | null;
  /** The review: the grade the trader gave this trade, and the levels they say
   * it was taken off (`level_tag` members, or the single literal "none"). Empty
   * until reviewed. Read-only on this payload — the review is written where the
   * tape is, not beside the P&L. */
  grade: string | null;
  setup: string | null;
  discipline: string | null;
  watched_levels: string[];
  // The trade's own model binding — not the effective one, which may come from a
  // backtest session and shows through `trade.model_id`.
  model_id: number | null;
  rules_met: number[];
}

export function useTrades(scope: FilterScope) {
  return useQuery({
    queryKey: qk.trades(scope),
    queryFn: () => apiGet<TradeRow[]>("/trades", scopeParams(scope)),
  });
}

export function useTradeDetail(scope: FilterScope, tradeNo: number | null) {
  return useQuery({
    queryKey: qk.trade(scope, tradeNo ?? -1),
    queryFn: () => apiGet<TradeDetail>(`/trades/${tradeNo}`, scopeParams(scope)),
    enabled: tradeNo != null,
  });
}

/** Measured windows for a set of trades, `{trade_key: context | null}`.
 *
 * One request for a whole sitting rather than one per card: a busy replay books
 * dozens of trades, and the review panel renders all of them at once.
 *
 * Deliberately takes no scope. The keys are content hashes, so there is nothing
 * for a filter to disambiguate — and this runs inside /charts, where the
 * journal's date and mode filters have no reason to agree with the sitting being
 * reviewed. Asking with a scope there would return 404s for trades that plainly
 * exist. */
export function useTradeContexts(keys: string[]) {
  return useQuery({
    queryKey: qk.tradeContext(keys),
    queryFn: () =>
      apiGet<Record<string, TradeContext | null>>("/trades/context", {
        keys: keys.join(","),
      }),
    enabled: keys.length > 0,
    // The measurement runs as a background task off the sitting's finish, so the
    // review panel can open a second or two before the rows land. Poll while
    // anything is still missing — but briefly, and then stop: a session whose
    // ticks were never cached will never produce a row, and polling for it
    // forever would turn "nothing to measure" into a permanent spinner.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const missing = Object.values(data).some((v) => v === null);
      return missing && query.state.dataUpdateCount < 6 ? 4000 : false;
    },
  });
}

/** One trade's windows plus the bars to draw them on.
 *
 * Separate from `useTradeContexts` because the bars are the expensive half — a
 * tape slice per call — and most readers only ever want the numbers. Fetched
 * lazily, when someone actually opens the picture. */
export interface TradeContextChart {
  trade_key: string;
  direction: string | null;
  context: TradeContext | null;
  entry: { time: number; price: number } | null;
  exit: { time: number; price: number } | null;
  bars: Bar[];
}

export function useTradeContextChart(
  scope: FilterScope,
  tradeKey: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: qk.tradeContextChart(scope, tradeKey ?? ""),
    queryFn: () =>
      apiGet<TradeContextChart>(
        `/trades/key/${tradeKey}/context`,
        scopeParams(scope),
      ),
    enabled: enabled && !!tradeKey,
  });
}

export function useSaveNote(tradeKey: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Note) => apiSend<{ ok: boolean }>("PUT", `/notes/${tradeKey}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["note"] });
      qc.invalidateQueries({ queryKey: ["trade"] });
      qc.invalidateQueries({ queryKey: ["trades"] });
      qc.invalidateQueries({ queryKey: ["day"] });
      qc.invalidateQueries({ queryKey: ["filters"] });
      // A model or rule-check change moves the trade between per-model buckets.
      qc.invalidateQueries({ queryKey: ["model-stats"] });
    },
  });
}
