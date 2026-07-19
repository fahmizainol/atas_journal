import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { DayChartData } from "../lib/chartTypes";
import type {
  Coverage,
  IbParams,
  IbResult,
  InteractionParams,
  InteractionResult,
  SavedRun,
  WeeklyVwapParams,
  WeeklyVwapResult,
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

// The Initial Balance / ORB study — session structure only (minute bars, no
// per-level scan), so a fresh range computes in well under the touch study's
// time. Deferred until the user hits its Run button, cached server-side by the
// same config-hash contract.
export function useIbStudy(params: IbParams | null) {
  return useQuery({
    queryKey: ["interactions", "ib", params],
    queryFn: () => apiGet<IbResult>("/interactions/ib", { ...params }),
    enabled: !!params,
    retry: false,
  });
}

// The weekly VWAP study — where the open prints in the weekly envelope and how
// the day resolves from there. Session structure only (no per-level scan), so a
// fresh range is cheap. Deferred until its own Run button, cached server-side
// by the same config-hash contract.
export function useWeeklyVwapStudy(params: WeeklyVwapParams | null) {
  return useQuery({
    queryKey: ["interactions", "weekly-vwap", params],
    queryFn: () => apiGet<WeeklyVwapResult>("/interactions/weekly-vwap", { ...params }),
    enabled: !!params,
    retry: false,
  });
}

// Every snapshot saved on disk, newest first — the "saved runs" list. Committing
// a saved config re-hits the same cache file, so reopening one is instant.
export function useInteractionRuns() {
  return useQuery({
    queryKey: ["interactions", "saved-runs"],
    queryFn: () => apiGet<SavedRun[]>("/interactions/runs"),
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
  ticksPerBar?: number,
) {
  return useQuery({
    queryKey: [
      "interactions",
      "day-chart",
      symbol,
      day,
      binSize ?? null,
      sources,
      ticksPerBar ?? null,
    ],
    queryFn: () =>
      apiGet<DayChartData>(`/interactions/day-chart/${day}`, {
        symbol,
        bin_size: binSize,
        sources,
        ticks_per_bar: ticksPerBar,
      }),
    enabled: !!symbol && !!day,
    retry: false,
  });
}
