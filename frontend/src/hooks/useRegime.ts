import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { RegimeDay, RegimeRange } from "../lib/regimeTypes";

// Regime is keyed by (symbol, date) — never by run — so these queries are shared
// across every run that touched the same window, and the cache is hit rather than
// recomputed when you flip between runs.

export function useRegimeRange(symbol: string | null, start: string | null, end: string | null) {
  return useQuery({
    queryKey: ["regime", "range", symbol, start, end],
    queryFn: () => apiGet<RegimeRange>("/regime", { symbol, start, end }),
    enabled: !!symbol && !!start && !!end,
  });
}

// `tz` rides in the key because the ribbon comes back projected onto the display
// zone's axis — the same one the day's candles are drawn on.
export function useRegimeDay(symbol: string | null, day: string | null, tz: string) {
  return useQuery({
    queryKey: ["regime", "day", symbol, day, tz],
    queryFn: () => apiGet<RegimeDay>(`/regime/${day}`, { symbol, tz }),
    enabled: !!symbol && !!day,
    // A day with no cached ticks 404s and stays 404 until a run buys them —
    // retrying just delays the "no data" the caller is about to render.
    retry: false,
  });
}
