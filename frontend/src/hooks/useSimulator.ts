import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { SessionPayload, TickSource } from "../lib/replayEngine";
import { useTapeHistory, type HistoryTapes } from "./useTapeHistory";

// Re-exported so the Simulator's imports stay where they were: the types belong
// to the shared loader now, but they describe the same thing they always did.
export type { HistDay, HistoryTapes } from "./useTapeHistory";

export interface SimDay {
  date: string;
  symbol: string;
  root: string;
  has_overnight: boolean;
  has_post: boolean;
  /** The tape stops materially before the 16:00 ET close — a holiday half day,
   *  or a recording that could not be finished. The server cannot tell those
   *  apart from timestamps (see `_live_segments`), so neither can this: it marks
   *  the day short and does not call it broken. */
  ends_early: boolean;
  source: TickSource;
}

export type { TickSource };

export function useSimulatorDays(root: string | null) {
  return useQuery({
    queryKey: ["simulator", "days", root],
    queryFn: () => apiGet<{ days: SimDay[]; roots: string[] }>("/simulator/days", { root }),
  });
}

// One session is ~0.5-1M ticks (a few MB). It never changes once cached on disk,
// so hold it in the query cache for the whole visit rather than refetching.
export function useSimulatorSession(symbol: string | null, date: string | null, tz: string) {
  return useQuery({
    queryKey: ["simulator", "session", symbol, date, tz],
    queryFn: () => apiGet<SessionPayload>("/simulator/session", { symbol, date, tz }),
    enabled: !!symbol && !!date,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  });
}

/**
 * Load whole prior sessions as tape, for drawing to the left of the replay.
 *
 * They are fetched from the same endpoint the session comes from, so a context
 * day is the same thing as a replayable one — real ticks, which is what lets the
 * chart bucket them onto any timeframe and profile them off the tape rather than
 * estimating from candles.
 *
 * The loader itself is `useTapeHistory`, shared with the live page: only the
 * endpoint differs, because only the *store* differs.
 */
export function useSimulatorHistory(symbol: string | null, dates: string[], tz: string): HistoryTapes {
  return useTapeHistory("/simulator/session", symbol, dates, tz);
}
