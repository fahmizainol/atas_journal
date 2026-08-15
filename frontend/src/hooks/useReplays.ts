// The stored replay attempts — the practice record behind /simulator/history.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { Log, Trade } from "../lib/replaySim";
import type { ReplayFlag, ReviewItem } from "../lib/replayAccount";
import type { AttemptSummary, RewindEvent } from "../lib/replayStats";

export interface AttemptRow {
  id: string;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  symbol: string;
  root: string;
  date: string;
  tz: string;
  engine_version: number;
  tape: { n: number; t0: number; end: number; rth_open_ms: number };
  prefs: Record<string, unknown>;
  /** `reviewed` is `finished` plus the answer — see `journal.replay_account`.
   *  It is what the next sitting's create gate reads. */
  status: "active" | "finished" | "abandoned" | "reviewed";
  started_ms: number;
  clock_ms: number;
  /** How many attempts on this session came before it. 0 is a cold read. */
  repeat_index: number;
  note: string;
  model_id: number | null;
  /** Which kind of sitting. Absent on every attempt recorded before backtest
   *  mode existed, which is why nothing reads it without the `?? "replay"`. */
  mode?: "replay" | "drill";
  /** Drill only: the clock this rep was thrown in at, and the window it was
   *  drawn from. Both tape wall clocks. */
  drop_ms?: number | null;
  window?: { from_ms: number; to_ms: number } | null;
  rewinds: RewindEvent[];
  discarded_trades: number;
  summary: Partial<AttemptSummary>;
  /** What the account raised over this sitting when it finished, and the
   *  verdicts filed against them. Absent on an attempt that finished before any
   *  of this existed, and on one still running. */
  flags?: ReplayFlag[];
  review?: { items: { flag_idx: number; verdict: string; note?: string }[]; reviewed_at: string };
}

export interface AttemptDetail extends AttemptRow {
  log: Log;
  trades: Trade[];
  discarded: Trade[];
}

/** `enabled` is for the pages that only want this under one condition — the
 *  Simulator draws the autopsy off it and only when the account is dead, and
 *  fetching every attempt on every replay visit to answer a question nobody
 *  asked is a request per page load for nothing. */
export function useReplayAttempts(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["replays", "list"],
    queryFn: () => apiGet<{ attempts: AttemptRow[] }>("/replays"),
    enabled: opts?.enabled ?? true,
  });
}

/** The journal rows one attempt produced, with the keys notes hang off.
 *
 *  Backtest mode's review needs these and cannot derive them: a trade_key is a
 *  hash of the trade's own content, written on the server when the mirror runs.
 *  Enabled only once a rep is over, because that is the only moment the answer
 *  is stable — the mirror rewrites this attempt's rows on every autosave. */
export function useReplayJournal(id: string | null, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["replays", "journal", id],
    queryFn: () =>
      apiGet<{ trades: DrillTradeRow[] }>(`/replays/${id}/journal`, { include_archived: 1 }),
    enabled: !!id && (opts?.enabled ?? true),
  });
}

export interface DrillCampaign {
  model_id: number | null;
  reps: number;
  traded_reps: number;
  sat_out: number;
  /** Traded reps over settled reps — how often the model was there at all. Null
   *  when there are no reps to divide by. */
  base_rate: number | null;
  trades: number;
  net_usd: number;
  expectancy: number | null;
  drawn_by_hour: { hour: number; reps: number }[];
  traded_by_hour: { hour: number; reps: number }[];
}

/** A model's backtest-mode campaign. Read off attempts rather than trades: a
 *  sat-out rep has no trades, and a trades-based aggregate would report a base
 *  rate of 100% forever. */
export function useDrillCampaign(modelId: number | null) {
  return useQuery({
    queryKey: ["replays", "drills", modelId],
    queryFn: () => apiGet<DrillCampaign>("/replays/drills", { model_id: modelId }),
    enabled: modelId != null,
  });
}

/** File one trade's rule checks against the model the rep is bound to.
 *
 *  Straight onto `PUT /notes/{trade_key}`, which already takes a model and a
 *  rules_met list and already sweeps checks belonging to another model's rules.
 *  A review endpoint of its own would be a second way to write the same rows.
 *
 *  Note that this sends the note fields empty. That is correct for a drill —
 *  there is no note being written here — but it means this must never be used
 *  to save a trade that has one, or it would blank it. */
export function useSaveRuleChecks(modelId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tradeKey, rulesMet }: { tradeKey: string; rulesMet: number[] }) =>
      apiSend<{ ok: boolean }>("PUT", `/notes/${tradeKey}`, {
        note: "",
        tags: [],
        setups: [],
        confluences: [],
        model_id: modelId,
        rules_met: rulesMet,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["replays", "journal"] });
      // A rule check moves the trade between compliance buckets.
      qc.invalidateQueries({ queryKey: ["model-stats"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });
}

export interface DrillTradeRow {
  trade_key: string;
  direction: string | null;
  entry_ts_local: string;
  net_pnl: number;
  model_id: number | null;
  rules_met: number[];
  reviewed: boolean;
}

export function useReplayAttemptDetail(id: string | null) {
  return useQuery({
    queryKey: ["replays", "detail", id],
    queryFn: () => apiGet<AttemptDetail>(`/replays/${id}`),
    enabled: !!id,
  });
}

export function useDeleteReplayAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiSend<{ ok: boolean }>("DELETE", `/replays/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}

/** File the review that clears a sitting.
 *
 *  One request rather than a save-then-mark: the verdicts and the status change
 *  are the same act, and the server refuses `reviewed` unless every flag on the
 *  stored attempt has one — so sending them apart would just be a way to get a
 *  409 with the notes already written. Invalidating `["replays"]` refreshes the
 *  account too, which is the thing that was waiting on this. */
export function useFileReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, items }: { id: string; items: ReviewItem[] }) =>
      apiSend<AttemptRow>("PATCH", `/replays/${id}`, { status: "reviewed", review: { items } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}

export function usePatchReplayAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; note?: string; model_id?: number | null; status?: string }) =>
      apiSend<AttemptRow>("PATCH", `/replays/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}
