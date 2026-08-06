import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { decodeTape, type SessionPayload, type Tape } from "../lib/replayEngine";

export interface SimDay {
  date: string;
  symbol: string;
  root: string;
  has_overnight: boolean;
  has_post: boolean;
}

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

// The context days, decoded and kept across sessions — scrolling back through a
// week re-reads the same Mondays, and a day is a several-megabyte download and a
// JSON parse of a million numbers.
//
// Deliberately *not* react-query: the cache there would hold the raw payload
// (three arrays of a million JS numbers, ~25MB a day) alongside the typed arrays
// we actually use. Decoding and dropping the payload is the whole point, so the
// decoded tapes are cached here by hand and the JSON is garbage the moment it
// has been read.
/** One context day: its ticks, and the two instants that say which of them are
 *  the RTH session. The bounds are kept because a profile of a *day* means its
 *  RTH profile — the composite the Simulator builds is the prior days' RTH
 *  volume-at-price, which is what the study measured. */
export interface HistDay {
  tape: Tape;
  rthOpenMs: number;
  rthCloseMs: number;
}

const TAPES = new Map<string, HistDay>();
const MAX_TAPES = 8;

function remember(key: string, day: HistDay): void {
  TAPES.set(key, day);
  // Insertion-ordered, so the oldest key is the first one out.
  while (TAPES.size > MAX_TAPES) TAPES.delete(TAPES.keys().next().value!);
}

export interface HistoryTapes {
  /** Oldest first, ready to be glued in front of the session's own tape. Only
   *  published once every requested day is in — a half-loaded history would
   *  rebuild the engine once per day for nothing. */
  days: HistDay[];
  loading: boolean;
  /** The days that couldn't be read, if any. Context is optional: the replay
   *  runs on whatever arrived. */
  failed: string[];
}

/**
 * Load whole prior sessions as tape, for drawing to the left of the replay.
 *
 * They are fetched from the same endpoint the session comes from, so a context
 * day is the same thing as a replayable one — real ticks, which is what lets the
 * chart bucket them onto any timeframe and profile them off the tape rather than
 * estimating from candles.
 *
 * `dates` must be oldest-first, and every day must be the same contract as the
 * session: a roll would splice two price series a hundred points apart.
 */
export function useSimulatorHistory(symbol: string | null, dates: string[], tz: string): HistoryTapes {
  const key = dates.join(",");
  const [state, setState] = useState<HistoryTapes>({ days: [], loading: false, failed: [] });

  useEffect(() => {
    if (!symbol || !key) {
      setState({ days: [], loading: false, failed: [] });
      return;
    }
    let cancelled = false;
    // Cleared, not kept: the days on screen belong to the session that asked for
    // them, and yesterday's yesterday is not context for today.
    setState({ days: [], loading: true, failed: [] });
    void (async () => {
      const days: HistDay[] = [];
      const failed: string[] = [];
      // One at a time: each day is a multi-megabyte parse, and three of them
      // racing each other only makes the first one land later.
      for (const date of key.split(",")) {
        const ck = `${symbol}|${date}|${tz}`;
        let day = TAPES.get(ck);
        if (!day) {
          try {
            const p = await apiGet<SessionPayload>("/simulator/session", { symbol, date, tz });
            day = { tape: decodeTape(p), rthOpenMs: p.rth_open_ms, rthCloseMs: p.rth_close_ms };
            remember(ck, day);
          } catch {
            failed.push(date);
            continue;
          }
        }
        if (cancelled) return;
        days.push(day);
      }
      if (!cancelled) setState({ days, loading: false, failed });
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol, key, tz]);

  return state;
}
