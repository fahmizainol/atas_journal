// Whole prior sessions, loaded as tape, for drawing to the left of a chart.
//
// Shared by both surfaces because a context day is the same thing on each: real
// ticks, so it candles on any timeframe and profiles off the tape rather than
// being estimated from bars. Only *where it is read from* differs — the
// Simulator's days come from the Databento cache (`/simulator/session`), the
// live page's from whichever store holds them (`/live/history/session`, which
// resolves cache-then-live per day). The two endpoints ship the same delta
// encoding (api/tape_codec), so one decoder is right about both and the seam is
// a string.
//
// Deliberately *not* react-query: the cache there would hold the raw payload
// (three arrays of a million JS numbers, ~25MB a day) alongside the typed arrays
// we actually use. Decoding and dropping the payload is the whole point, so the
// decoded tapes are cached here by hand and the JSON is garbage the moment it
// has been read.

import { useEffect, useState } from "react";
import { apiGet } from "../lib/api";
import { decodeTape, type SessionPayload, type Tape } from "../lib/replayEngine";

/** One context day: its ticks, and the two instants that say which of them are
 *  the RTH session. The bounds are kept because a profile of a *day* means its
 *  RTH profile — the composite built over these days is the prior days' RTH
 *  volume-at-price, which is what the study measured. */
export interface HistDay {
  tape: Tape;
  rthOpenMs: number;
  rthCloseMs: number;
  /** Which store answered, when the endpoint says. Only `/live/history/session`
   *  does — a cached day and a recorded day are different bytes for the same
   *  date, and a reader should not have to infer which from the calendar. */
  source?: string;
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
  /** The days that couldn't be read, if any. Context is optional: the chart
   *  runs on whatever arrived. */
  failed: string[];
  /** True once a load has finished for the current request — including the
   *  trivial one where nothing was asked for. The live page waits on this
   *  before it starts its tape, because context can only be seeded in front of
   *  a tape that has not started growing yet. */
  settled: boolean;
}

const IDLE: HistoryTapes = { days: [], loading: false, failed: [], settled: true };

/**
 * Load whole prior sessions from `endpoint`, oldest first.
 *
 * `dates` must be oldest-first, and every day must be the same contract as the
 * session: a roll would splice two price series a hundred points apart.
 */
export function useTapeHistory(
  endpoint: string,
  symbol: string | null,
  dates: string[],
  tz: string,
): HistoryTapes {
  const key = dates.join(",");
  const [state, setState] = useState<HistoryTapes>(IDLE);

  useEffect(() => {
    if (!symbol || !key) {
      setState(IDLE);
      return;
    }
    let cancelled = false;
    // Cleared, not kept: the days on screen belong to the session that asked for
    // them, and yesterday's yesterday is not context for today.
    setState({ days: [], loading: true, failed: [], settled: false });
    void (async () => {
      const days: HistDay[] = [];
      const failed: string[] = [];
      // One at a time: each day is a multi-megabyte parse, and three of them
      // racing each other only makes the first one land later.
      for (const date of key.split(",")) {
        const ck = `${endpoint}|${symbol}|${date}|${tz}`;
        let day = TAPES.get(ck);
        if (!day) {
          try {
            const p = await apiGet<SessionPayload & { source?: string }>(endpoint, {
              symbol,
              date,
              tz,
            });
            day = {
              tape: decodeTape(p),
              rthOpenMs: p.rth_open_ms,
              rthCloseMs: p.rth_close_ms,
              source: p.source,
            };
            remember(ck, day);
          } catch {
            failed.push(date);
            continue;
          }
        }
        if (cancelled) return;
        days.push(day);
      }
      if (!cancelled) setState({ days, loading: false, failed, settled: true });
    })();
    return () => {
      cancelled = true;
    };
  }, [endpoint, symbol, key, tz]);

  return state;
}
