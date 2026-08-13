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

export function useReplayAttempts() {
  return useQuery({
    queryKey: ["replays", "list"],
    queryFn: () => apiGet<{ attempts: AttemptRow[] }>("/replays"),
  });
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
