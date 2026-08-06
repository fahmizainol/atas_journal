import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { DayChartData } from "../lib/chartTypes";
import type {
  Coverage,
  IbParams,
  IbResult,
  IbSessionWidths,
  InteractionParams,
  InteractionResult,
  InteractionStats,
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
//
// This is the *events* view: touches, VA-snaps, day index — what the Sessions
// grid and chart drill-down need. The heavy per-minute band_state and the
// aggregate tables are left off the payload (the aggregates come from
// `useInteractionStats` on demand), so the auto-loaded run renders the chart
// without shipping the full snapshot every refresh.
export function useInteractions(params: InteractionParams | null) {
  return useQuery({
    queryKey: ["interactions", "run", params],
    queryFn: () => apiGet<InteractionResult>("/interactions", { ...params }),
    enabled: !!params,
    retry: false,
  });
}

// The aggregate tables for a committed run, fetched on demand: deferred until
// `enabled` (the Stats tab's "Compute stats" button) so the Sessions view never
// waits on them. Same config as the events query, so the server answers from the
// one cached snapshot — the stats are a few-KB slice of a file already on disk.
export function useInteractionStats(params: InteractionParams | null, enabled: boolean) {
  return useQuery({
    queryKey: ["interactions", "stats", params],
    queryFn: () => apiGet<InteractionStats>("/interactions", { ...params, stats: true }),
    enabled: !!params && enabled,
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

// Per-session IB width for the Sessions table. Unlike `useIbStudy` this needs no
// Run button and no committed config: it is a read of the widest saved snapshot,
// keyed by (symbol, range) like the regime queries, so it is shared across every
// run over the same window and costs a file slice. Absent days render as a dash.
export function useIbSessionWidths(
  symbol: string | null,
  start: string | null,
  end: string | null,
) {
  return useQuery({
    queryKey: ["interactions", "ib-sessions", symbol, start, end],
    queryFn: () => apiGet<IbSessionWidths>("/interactions/ib/sessions", { symbol, start, end }),
    enabled: !!symbol && !!start && !!end,
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

// One session's day-chart query — factored out so the single-day hook and the
// multi-day run-chart hook below share the *exact same* key (and therefore the
// same cache entry): opening a day the continuous chart already loaded is a cache
// hit, and vice-versa.
function dayChartQueryOptions(
  symbol: string | null,
  day: string | null,
  binSize?: number,
  sources?: string[],
  ticksPerBar?: number,
  barMinutes?: number,
) {
  return {
    queryKey: [
      "interactions",
      "day-chart",
      symbol,
      day,
      binSize ?? null,
      sources,
      ticksPerBar ?? null,
      barMinutes ?? null,
    ],
    queryFn: () =>
      apiGet<DayChartData>(`/interactions/day-chart/${day}`, {
        symbol,
        bin_size: binSize,
        sources,
        ticks_per_bar: ticksPerBar,
        bar_minutes: barMinutes,
      }),
    enabled: !!symbol && !!day,
    retry: false,
  };
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
  barMinutes?: number,
) {
  return useQuery(dayChartQueryOptions(symbol, day, binSize, sources, ticksPerBar, barMinutes));
}

// The same day-chart for a *window* of sessions, fetched in parallel — the data
// behind the continuous session chart. Each day is an independent query sharing
// the single-day cache above, so a fresh window is one burst of (cached-forever)
// requests and re-opening one is instant. Returns the raw results in `days`
// order; the caller stitches the available ones into one tape.
export function useInteractionRunChart(
  symbol: string | null,
  days: string[],
  binSize?: number,
  sources?: string[],
  ticksPerBar?: number,
  barMinutes?: number,
): UseQueryResult<DayChartData>[] {
  return useQueries({
    queries: days.map((d) =>
      dayChartQueryOptions(symbol, d, binSize, sources, ticksPerBar, barMinutes),
    ),
  });
}
