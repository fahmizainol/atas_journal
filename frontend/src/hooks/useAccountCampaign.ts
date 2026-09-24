// The counterfactual campaign: this account under a bracket it never traded.
//
// A third query family beside `useReplayAccounts` (the list you pick from) and
// `useReplayAccount` (one account's live state while you trade it). This one is
// the retrospective, and it is the only one that is *expensive* — which is why
// the server reads cached grids and never prices one to answer a GET.
//
// `coverage` is the progress bar. `price` starts a background sweep and returns
// at once; the campaign query then polls itself until every rep is priced, so
// the number closing is the same number the sweep is closing.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { AccountRow } from "./useReplayAccounts";

export interface CampaignLife {
  index: number;
  reps: number;
  first: string | null;
  last: string | null;
  end_equity: number;
  outcome: "blown" | "passed" | "live";
  killed_by: string | null;
}

export interface CampaignRun {
  key: string;
  label: string;
  /** `clicked` keeps the manual flattens that were made; `forget` drops them and
   *  the bracket drags with them. The gap between one bracket's two rows is what
   *  the hand on the exit was worth — in lives, not dollars. */
  column: "clicked" | "forget";
  /** This row took the other side of every entry. Not a bracket — it is a
   *  readout on the direction read — so it sorts below every real one however
   *  well it scored. On a losing account it scores very well indeed. */
  flip: boolean;
  record: { passed: number; blown: number };
  /** Lives won less lives lost, and what the rows are ordered by. Not `passed`:
   *  a bracket that passed once and blew three times bought four accounts to
   *  clear one. */
  score: number;
  lives: CampaignLife[];
  end_equity: number | null;
  net_usd: number;
  trades: number;
  win_rate: number | null;
}

export interface Coverage {
  reps: number;
  traded: number;
  priced: number;
  unpriced: number;
  /** Reps that booked no trade. They move no equity but they are still *days*,
   *  so they stay in the walk — an end-of-day floor banks its peak when the day
   *  changes. */
  flat: number;
}

/** One real life of the account, as it actually happened. Beside the campaign
 *  rather than replaced by them: this is the history, those are counterfactuals,
 *  and they will not exactly agree (see `AccountDetail`). */
export interface RealLife {
  index: number;
  started_at: string;
  outcome: "blown" | "passed" | "live";
  reps: number;
  equity: number;
  floor: number;
  cause: string | null;
  ended_by: string | null;
}

/** One rep, as the replay store knows it. What the consistency panel reads, and
 *  what maps a life's `killed_by` back to a date. */
export interface RepFact {
  id: string;
  date: string | null;
  created_at: string;
  status: string;
  trades: number;
  net_usd: number;
  rewinds: number;
  priced: boolean;
  /** The initial stop each position actually opened on, low/median/high — a
   *  *range*, because the ticket re-sizes it off the volatility ruler at every
   *  fill. Null on a rep with no priced grid. */
  stop_ticks: { lo: number; med: number; hi: number } | null;
  fast_trades: number;
  held: number;
}

export interface CampaignReport {
  account: AccountRow;
  coverage: Coverage;
  reps: RepFact[];
  runs: CampaignRun[];
  grid_version: number;
  real: { record: { passed: number; blown: number }; lives: RealLife[] };
}

export function useAccountCampaign(accountId: string | undefined) {
  return useQuery({
    queryKey: ["replays", "campaign", accountId],
    enabled: !!accountId,
    queryFn: () => apiGet<CampaignReport>(`/replays/accounts/${accountId}/campaign`),
    // While a sweep is running the answer is incomplete but not wrong, so it is
    // shown and refreshed rather than hidden behind a spinner. A rep takes about
    // a second; five is often enough to see the count move and never so often
    // that a large account is re-walked for nothing.
    refetchInterval: (q) => {
      const d = q.state.data as CampaignReport | undefined;
      return d && d.coverage.unpriced > 0 ? 5_000 : false;
    },
  });
}

/** Grades and review debt — a separate query because it has a separate source.
 *  A grade lives in the journal, and reaching it builds the scoped trade frame:
 *  eight seconds against the campaign's tenth of one. Fetched alongside so the
 *  campaign paints at once and this fills in behind it. */
export function useAccountReview(accountId: string | undefined) {
  return useQuery({
    queryKey: ["replays", "campaign", accountId, "review"],
    enabled: !!accountId,
    queryFn: () =>
      apiGet<{ grades: Record<string, number>; owed: number | null; graded: number }>(
        `/replays/accounts/${accountId}/review`,
      ),
  });
}

export interface RepVerdict {
  key: string;
  label: string;
  column: "clicked" | "forget";
  survived: boolean;
  net_usd: number;
  end_equity: number;
  floor: number;
}

/** Which brackets would have survived one rep — the one that ended a life.
 *  Every row starts from the equity and floor that rep really opened against. */
export function useRepVerdicts(accountId: string | undefined, attemptId: string | null) {
  return useQuery({
    queryKey: ["replays", "campaign", accountId, "rep", attemptId],
    enabled: !!accountId && !!attemptId,
    queryFn: () =>
      apiGet<{
        attempt_id: string;
        date: string | null;
        opened_at: number;
        floor: number;
        priced: boolean;
        verdicts: RepVerdict[];
      }>(`/replays/accounts/${accountId}/reps/${attemptId}`),
  });
}

/** Price the reps that have no cached grid. Returns as soon as the sweep is
 *  queued — the campaign query's own poll is what shows it finishing. */
export function usePriceAccount(accountId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ account: string; pricing: number }>(
        "POST",
        `/replays/accounts/${accountId}/price`,
        {},
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays", "campaign", accountId] }),
  });
}
