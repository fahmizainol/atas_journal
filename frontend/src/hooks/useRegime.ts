import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { RegimeDay, RegimeRange, RegimeStudy, VolRegimeRange } from "../lib/regimeTypes";

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

// The daily-ATR vol clock over the same window — keyed by (symbol, range) like
// the day-type above, and just as run-independent. The server warms the label up
// from ~90 sessions before `start`, so the first day of the range is anchored as
// well as the last; the first call over a window that was never labelled scans a
// parquet per new session, every call after that is served from small per-day
// artifacts.
export function useVolRegimeRange(symbol: string | null, start: string | null, end: string | null) {
  return useQuery({
    queryKey: ["regime", "vol-range", symbol, start, end],
    queryFn: () => apiGet<VolRegimeRange>("/vol-regime", { symbol, start, end }),
    enabled: !!symbol && !!start && !!end,
  });
}

// The regime-vs-P&L study for one run. Unlike the two queries around it this IS
// keyed by the run — it joins the run's own daily P&L against the shared regime —
// and it is the *only* source of those numbers: the browser no longer scores
// anything itself, so the panel and the snapshot on disk can't disagree.
//
// The first call on a run whose snapshot predates a version bump recomputes ~100
// permutation tests server-side (a couple of seconds); every call after that is
// served from the file.
export function useRegimePnl(slug: string | null, runId: string | null) {
  return useQuery({
    queryKey: ["regime", "pnl", slug, runId],
    queryFn: () => apiGet<RegimeStudy>(`/strategies/${slug}/runs/${runId}/regime-pnl`),
    enabled: !!slug && !!runId,
    // A run with no completed artifacts 404s and keeps 404ing.
    retry: false,
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
