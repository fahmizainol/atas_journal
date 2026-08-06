// The stored replay attempts — the practice record behind /simulator/history.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { Log, Trade } from "../lib/replaySim";
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
  status: "active" | "finished" | "abandoned";
  started_ms: number;
  clock_ms: number;
  /** How many attempts on this session came before it. 0 is a cold read. */
  repeat_index: number;
  note: string;
  model_id: number | null;
  rewinds: RewindEvent[];
  discarded_trades: number;
  summary: Partial<AttemptSummary>;
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

export function usePatchReplayAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; note?: string; model_id?: number | null; status?: string }) =>
      apiSend<AttemptRow>("PATCH", `/replays/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}
