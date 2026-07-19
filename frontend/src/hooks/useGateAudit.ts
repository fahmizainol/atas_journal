import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { GateAudit } from "../lib/strategyTypes";

function anyRunning(a: GateAudit | undefined): boolean {
  return !!a?.gates.some(
    (g) => g.off.state === "running" || g.neighbors.some((v) => v.state === "running"),
  );
}

// The gate-robustness scorecard for one run. Unlike edges, the answer is NOT
// immutable: it changes as variant runs complete, so the query re-polls while
// any variant is in flight (or while the panel is auto-launching the ladder).
export function useGateAudit(slug: string | null, runId: string | null, launching = false) {
  return useQuery({
    queryKey: ["strategies", "gate-audit", slug, runId],
    queryFn: () => apiGet<GateAudit>(`/strategies/${slug}/runs/${runId}/gate-audit`),
    enabled: !!slug && !!runId,
    retry: false,
    refetchInterval: (q) => (launching || anyRunning(q.state.data) ? 5000 : false),
  });
}
