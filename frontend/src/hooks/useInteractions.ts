import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type {
  Coverage,
  InteractionParams,
  InteractionResult,
} from "../lib/interactionTypes";

// The interaction study is market structure over the tick cache, not trades — so
// it is keyed by its own config, never by a run or the journal filter scope. The
// server caches the result by the same config hash, so re-running a range you have
// already run is a file read: react-query's cache and the disk snapshot agree.
//
// `params` is null until the user hits Run; the query is deferred until then. A
// month's first compute is a few seconds server-side; every call after is instant.
export function useInteractions(params: InteractionParams | null) {
  return useQuery({
    queryKey: ["interactions", "run", params],
    queryFn: () => apiGet<InteractionResult>("/interactions", { ...params }),
    enabled: !!params,
    retry: false,
  });
}

// Which sessions in the range have cached ticks — drives the coverage strip and
// bounds the range picker so a run can't silently skip days it has no data for.
export function useInteractionCoverage(
  symbol: string | null,
  start: string | null,
  end: string | null,
) {
  return useQuery({
    queryKey: ["interactions", "coverage", symbol, start, end],
    queryFn: () => apiGet<Coverage>("/interactions/coverage", { symbol, start, end }),
    enabled: !!symbol && !!start && !!end,
  });
}
