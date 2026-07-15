import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { DayChartData } from "../lib/chartTypes";
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

// A single session's candles + both anchored VWAPs + both developing profiles,
// built from the same tick engine as the events so the overlay lines up. Keyed by
// (symbol, day, bin, sources) to match the run the touches came from.
export function useInteractionDayChart(
  symbol: string | null,
  day: string | null,
  binSize?: number,
  sources?: string[],
) {
  return useQuery({
    queryKey: ["interactions", "day-chart", symbol, day, binSize ?? null, sources],
    queryFn: () =>
      apiGet<DayChartData>(`/interactions/day-chart/${day}`, {
        symbol,
        bin_size: binSize,
        sources,
      }),
    enabled: !!symbol && !!day,
    retry: false,
  });
}
