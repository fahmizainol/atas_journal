import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { Session, SessionMode } from "../lib/types";

export function useSessions() {
  return useQuery({
    queryKey: qk.sessions,
    queryFn: () => apiGet<{ sessions: Session[] }>("/sessions/list").then((d) => d.sessions),
    staleTime: 30_000,
  });
}

export interface SessionPatch {
  mode?: SessionMode;
  model_id?: number | null;
  archived?: boolean;
}

// Mode and archive decide which aggregates a session's trades reach, so a patch
// invalidates every scoped query, not just the session list.
export function usePatchSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceFile, patch }: { sourceFile: string; patch: SessionPatch }) =>
      apiSend<{ ok: boolean; session: Session }>(
        "PATCH",
        `/sessions/${encodeURIComponent(sourceFile)}`,
        patch,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.sessions });
      qc.invalidateQueries({ queryKey: ["filters"] });
      for (const k of [
        "metrics", "summary-extras", "equity-curve", "daily-pnl", "distribution",
        "edges", "trades", "trade", "calendar", "day", "model-stats",
      ]) {
        qc.invalidateQueries({ queryKey: [k] });
      }
    },
  });
}
