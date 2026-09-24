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
  /** The week already behind *this* day's Globex open, as the payload shipped
   *  it. Kept because the weekly anchor is the one indicator that reaches back
   *  across these days, and it needs each day's own seed to do it without
   *  double-counting (see `ReplayEngine.historyWeeklyBand`). Null when the week
   *  behind that day has a hole in it. */
  weeklySeed: number[] | null;
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
 *
 * `versions` is optional and is how a *finished* day gets to change underneath a
 * running page. A recorded day is not immutable: you disconnect an hour after
 * the bell, and the rest of that session only lands when the harvest sweep fills
 * it on your next connect — which is routinely a minute after this page loaded
 * and cached the truncated version. Half a day's volume can arrive that way, and
 * with it that day's whole stretch of the weekly anchor, so drawing the tape
 * fetched a minute earlier is not a stale pixel but a wrong number. Pass the
 * manifest's `updated_at` per date (`/live/recordings`, which is already polled)
 * and the day re-reads itself when the sweep rewrites it. Days with no entry key
 * as `"-"` and behave exactly as they did before — the Databento cache only ever
 * changes when somebody buys more of it, so the Simulator passes nothing.
 */
export function useTapeHistory(
  endpoint: string,
  symbol: string | null,
  dates: string[],
  tz: string,
  versions?: Record<string, string | null>,
): HistoryTapes {
  const key = dates.join(",");
  // The version of each requested day, in the same order — the cache key's
  // second half. Built from the requested dates and not from `versions` itself
  // so that a map which churns for an unrelated day (the one being recorded
  // updates its manifest every chunk) does not read as a change here.
  const vkey = dates.map((d) => versions?.[d] ?? "-").join(",");
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
      const dates = key.split(",");
      const days: HistDay[] = [];
      const failed: string[] = [];
      // Paired with `dates` by position — `vkey` was built from the same list.
      const vlist = vkey.split(",");
      const vers = new Map(dates.map((d, i) => [d, vlist[i] ?? "-"]));
      const ckOf = (date: string) =>
        `${endpoint}|${symbol}|${date}|${tz}|${vers.get(date) ?? "-"}`;
      type Payload = SessionPayload & { source?: string };
      // A day is asked for while the one before it is being decoded, and never
      // more than that. Not the whole week at once: the payload is the expensive
      // thing — three arrays of half a million numbers, ~25MB of JSON that
      // exists only to be prefix-summed into typed arrays and dropped — so
      // five in flight would hold five of those alive to save time on a parse
      // that is single-threaded anyway. One ahead overlaps the server and the
      // wire with the decode, which is where the wait actually is, and doubles
      // the peak instead of quintupling it.
      //
      // Resolves to null rather than rejecting: the next day's request is
      // started before this one is awaited, so a rejection could otherwise
      // surface with nobody attached to it.
      const fetchDay = (date: string): Promise<Payload | null> | null =>
        TAPES.has(ckOf(date))
          ? null
          : apiGet<Payload>(endpoint, { symbol, date, tz }).catch(() => null);
      let ahead = dates.length ? fetchDay(dates[0]) : null;
      for (let i = 0; i < dates.length; i++) {
        const date = dates[i];
        const pending = ahead;
        // Started before this one is awaited — that is the whole overlap.
        ahead = i + 1 < dates.length ? fetchDay(dates[i + 1]) : null;
        const ck = ckOf(date);
        let day = TAPES.get(ck);
        if (!day) {
          // `pending` is null when the day was cached at prefetch time; the
          // fallback covers the one case where it was evicted since (the cache
          // holds fewer days than the longest span offered).
          const p = await (pending ?? fetchDay(date) ?? Promise.resolve(null));
          if (cancelled) return;
          if (!p) {
            failed.push(date);
            continue;
          }
          try {
            day = {
              tape: decodeTape(p),
              rthOpenMs: p.rth_open_ms,
              rthCloseMs: p.rth_close_ms,
              source: p.source,
              weeklySeed: p.weekly_seed ?? null,
            };
          } catch {
            failed.push(date);
            continue;
          }
          remember(ck, day);
        }
        if (cancelled) return;
        days.push(day);
      }
      if (!cancelled) setState({ days, loading: false, failed, settled: true });
    })();
    return () => {
      cancelled = true;
    };
  }, [endpoint, symbol, key, tz, vkey]);

  return state;
}
