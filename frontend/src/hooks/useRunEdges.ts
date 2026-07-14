import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { RunEdges } from "../lib/types";

// The journal's /edges cuts, but over one run's simulated trades. Keyed by the
// run and nothing else: the artifact is immutable, so the answer never changes
// once it has been fetched.
export function useRunEdges(slug: string | null, runId: string | null) {
  return useQuery({
    queryKey: ["strategies", "edges", slug, runId],
    queryFn: () => apiGet<RunEdges>(`/strategies/${slug}/runs/${runId}/edges`),
    enabled: !!slug && !!runId,
    // A run with no completed artifact 404s and keeps 404ing.
    retry: false,
  });
}
