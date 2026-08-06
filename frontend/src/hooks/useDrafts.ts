import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { DayChartData } from "../lib/chartTypes";
import type { DraftDetailData, DraftListItem } from "../lib/draftTypes";

export function useDraftList() {
  return useQuery({
    queryKey: ["drafts", "list"],
    queryFn: () => apiGet<DraftListItem[]>("/drafts"),
    staleTime: 30_000,
  });
}

// First open of a draft materializes it server-side (a few seconds of
// cache-only reads); every read after that hits the snapshot.
export function useDraftDetail(slug: string | undefined) {
  return useQuery({
    queryKey: ["drafts", "detail", slug],
    queryFn: () => apiGet<DraftDetailData>(`/drafts/${slug}`),
    enabled: !!slug,
    staleTime: 30_000,
  });
}

// The drafts day-chart is the interactions day-chart payload (raw UTC epochs,
// minute candles unless widened/swapped by the bar params) with the draft's
// markers and trade rects layered on, so the tape client shifts everything by
// the same per-day wall-clock offset.
function dayChartOptions(
  slug: string | undefined,
  day: string,
  ticksPerBar?: number,
  barMinutes?: number,
) {
  return {
    queryKey: ["drafts", "day-chart", slug, day, ticksPerBar, barMinutes] as const,
    queryFn: () =>
      apiGet<DayChartData>(`/drafts/${slug}/day-chart/${day}`, {
        ticks_per_bar: ticksPerBar,
        bar_minutes: barMinutes,
      }),
    enabled: !!slug && !!day,
    staleTime: Infinity,
    retry: false,
  };
}

// The day-chart for a *window* of the draft's sessions, fetched in parallel —
// the data behind the continuous session tape (the Interactions pattern). Each
// day is an independent cached query, so recentring the window only fetches
// the newly-entered days. Results come back in `days` order; the caller
// stitches the available ones.
export function useDraftSessionCharts(
  slug: string | undefined,
  days: string[],
  ticksPerBar?: number,
  barMinutes?: number,
): UseQueryResult<DayChartData>[] {
  return useQueries({
    queries: days.map((d) => dayChartOptions(slug, d, ticksPerBar, barMinutes)),
  });
}
