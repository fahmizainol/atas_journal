// Reading a session that is still happening.
//
// Three of the four reads are ordinary polls and go through react-query. The
// tape is not — twice over. Its payloads are thousands of JS numbers that exist
// only to be prefix-summed into typed arrays and thrown away, so a query cache
// would keep every block of the day alive alongside the tape they were already
// folded into. And it does not poll at all: the server pushes blocks over SSE
// the moment ticks land (`/live/tape/stream`), so the chart moves on the
// market's own rhythm instead of a timer's.

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { createGrowableTape, type GrowableTape, type TapeBlock } from "../lib/growableTape";
import type {
  LiveHeader,
  LiveHistoryDays,
  LiveRecordings,
  LiveSignals,
  LiveStatus,
} from "../lib/liveTypes";
import type { Tape } from "../lib/replayEngine";

/** The signal cadence is server-side (a bar close, floored at ~5s), so asking
 *  much faster than that only re-reads an unchanged answer. */
const SIGNAL_POLL_MS = 3000;
const STATUS_POLL_MS = 2000;

export function useLiveStatus() {
  return useQuery({
    queryKey: ["live", "status"],
    queryFn: () => apiGet<LiveStatus>("/live/status"),
    refetchInterval: STATUS_POLL_MS,
  });
}

export function useLiveHeader(gen: string | null, tz: string) {
  return useQuery({
    queryKey: ["live", "session", gen, tz],
    queryFn: () => apiGet<LiveHeader>("/live/session", { tz }),
    enabled: !!gen,
    // The header is not constant — `session_start_ms`, `globex_anchor_ms` and the
    // weekly seed are all null until the ticks that answer them arrive — so it is
    // re-read while the session is young rather than cached for the visit.
    refetchInterval: STATUS_POLL_MS,
  });
}

/**
 * What is on disk in the live store, and how long the missing days stay
 * reachable.
 *
 * Polled far more slowly than the rest of this file, and on purpose: a session
 * is written continuously but *appears* here once, and the deadline it carries
 * moves a day at a time. The refetch exists so the panel is not stale after a
 * harvest sweep finishes behind the feed, not so it keeps up with the tape.
 */
export function useLiveRecordings(symbol?: string) {
  return useQuery({
    queryKey: ["live", "recordings", symbol ?? null],
    queryFn: () => apiGet<LiveRecordings>("/live/recordings", symbol ? { symbol } : undefined),
    refetchInterval: 60_000,
  });
}

/**
 * Which prior sessions have tape behind this one, oldest first.
 *
 * The server answers because it is the only side that can see both stores at
 * once (the Databento cache and the live one, resolved cache-first per day) and
 * the only side that can tell a hole from a holiday without opening a file.
 * `missing` comes back with it: the live store has long contiguous stretches
 * with nothing recorded, and a week of calendar is routinely fewer sessions of
 * tape than it looks.
 *
 * Cached for the visit. Which days exist behind a fixed date does not change
 * while you watch — the only thing that could change it is a harvest sweep, and
 * that is what `useLiveRecordings` is for.
 */
export function useLiveHistoryDays(symbol: string | null, date: string | null, days: number) {
  return useQuery({
    queryKey: ["live", "history", "days", symbol, date, days],
    queryFn: () => apiGet<LiveHistoryDays>("/live/history/days", { symbol, date, days }),
    enabled: !!symbol && !!date && days > 0,
    staleTime: Infinity,
  });
}

export function useLiveSignals(gen: string | null) {
  return useQuery({
    queryKey: ["live", "signals", gen],
    queryFn: () => apiGet<LiveSignals>("/live/signals"),
    enabled: !!gen,
    refetchInterval: SIGNAL_POLL_MS,
  });
}

export function startFakeFeed(params: {
  symbol: string;
  date: string;
  speed: number;
  start_at?: string;
}) {
  return apiSend<{ gen: string }>("POST", `/live/feed/start?${toQuery(params)}`);
}

/**
 * Connect the real ticker plant and start recording.
 *
 * `symbol` must be a RAW contract (`NQU6`), never a root — a root would send
 * `contract_for` to probe Databento, which a live path must not do, and the
 * on-disk roll map ends 2026-06-30 anyway. The API rejects roots with a 422
 * rather than resolving one, so the guard is not only in this comment.
 *
 * Resolves as soon as the feed is *started*, not when the session is whole: the
 * backfill replays the day so far behind it and lands a few seconds later, as
 * rows on `/live/tape` like any others. `status.feed_status.backfills` is where
 * it reports what it covered, or why it could not.
 */
export function startRithmicFeed(params: {
  symbol: string;
  exchange?: string;
  backfill?: boolean;
  record?: boolean;
  signals?: boolean;
  /** Also open the ORDER and PnL plants. Unlike `record` and `signals` this is
   *  not a mode: one Rithmic login is one socket, so the order path rides this
   *  connection and the plants are chosen once, here. A session started without
   *  it cannot acquire the ability to trade later. */
  routing?: boolean;
}) {
  return apiSend<{ gen: string; recording: boolean; signals: boolean; routing: boolean }>(
    "POST",
    `/live/feed/rithmic?${toQuery(params)}`,
  );
}

export interface LiveModes {
  gen: string;
  recording: boolean;
  signals: boolean;
  journalling: boolean;
  unrecorded_rows: number;
}

/**
 * Turn recording and the shadow shelf on or off under a running session.
 *
 * Omit a field to leave that mode alone. The API refuses one pair with a 422 —
 * the shelf running over a live feed with nothing being written — because the
 * `gx_*` gates read the session's earlier windows off disk and would veto
 * everything without saying why. The error text is the explanation, so it is
 * shown rather than swallowed.
 */
export function setLiveModes(params: { record?: boolean; signals?: boolean }) {
  return apiSend<LiveModes>("POST", `/live/modes?${toQuery(params)}`);
}

export function stopFeed() {
  return apiSend<{ stopped: boolean }>("POST", "/live/feed/stop");
}

export interface LiveTapeState {
  rows: number;
  closed: boolean;
  error: string | null;
}

/**
 * Keep a growing tape fed from `/live/tape/stream`.
 *
 * Server-sent events, not a poll: the server holds the connection and pushes a
 * block the moment ticks land, so blocks arrive on the market's own irregular
 * rhythm — the thing a fixed poll grid cannot fake, however fine. Each `data:`
 * payload is byte-for-byte a `/live/tape` response, so the decode path is
 * unchanged.
 *
 * The cursor lives on the server. Every event carries `id: {gen}|{next}`, and
 * on a drop the browser reconnects with that id in `Last-Event-ID` — which the
 * server honours over the URL's stale `since` — so a blip costs latency, never
 * a tick, same as the poll's contract. A `reset` block answers a gen the
 * server no longer recognises; `event: gone` says the live state itself is
 * over, and the stream closes for good (the status poll is what tears the page
 * down, as it always was).
 *
 * `onReset` fires with a brand-new tape whenever the session underneath changes
 * — a restart, a different day — because the row indices the caller was holding
 * describe a tape that no longer exists. `onAppend` fires after every block that
 * carried rows, which is the cue to advance the chart.
 *
 * `context` is the prior days drawn to the left, seeded in front of row zero
 * (see `createGrowableTape`). It is read **only when a tape is created**, which
 * is the point: tick indices have to be stable for the life of the session, so
 * context is a precondition of starting rather than something spliced in later.
 * `contextKey` is what the stream restarts on — an array identity changes every
 * render and would tear the connection down with it.
 */
export function useLiveTape(opts: {
  enabled: boolean;
  gen: string | null;
  tz: string;
  tickSize: number;
  pointValue: number;
  context?: readonly Tape[];
  contextKey?: string;
  onReset: (tape: GrowableTape) => void;
  onAppend: (tape: GrowableTape, added: number) => void;
}): LiveTapeState {
  const { enabled, gen, tz, tickSize, pointValue, contextKey = "" } = opts;
  const [state, setState] = useState<LiveTapeState>({ rows: 0, closed: false, error: null });
  // The callbacks are re-made on every render of a page that renders often. Held
  // in a ref so the poll loop is not torn down and restarted for that — the loop's
  // identity belongs to the session, not to a render.
  const cb = useRef(opts);
  cb.current = opts;

  useEffect(() => {
    if (!enabled || !gen) return;
    let tape: GrowableTape | null = null;

    const qs = toQuery({ since: 0, gen, tz });
    const es = new EventSource(`/api/live/tape/stream?${qs}`);

    es.onmessage = (ev) => {
      const block = JSON.parse(ev.data) as TapeBlock;
      if (!tape || block.reset) {
        tape = createGrowableTape(tickSize, pointValue, cb.current.context ?? []);
        cb.current.onReset(tape);
      }
      if (block.n > 0) {
        tape.append(block);
        cb.current.onAppend(tape, block.n);
      }
      // Also what clears a reconnect's error: the stream is speaking again.
      setState({ rows: block.rows, closed: block.closed, error: null });
    };

    es.addEventListener("gone", () => {
      // The live state is over, said out loud. Close for good — reconnecting
      // would only collect another `gone` — and let the status poll tear the
      // page down the way it always has.
      es.close();
    });

    es.onerror = () => {
      // EventSource reconnects on its own, resuming via `Last-Event-ID` — so
      // an error is information, not a task. CLOSED is the one terminal state
      // (a non-200 or wrong content-type: deployment breakage, not a blip).
      setState((s) => ({
        ...s,
        error:
          es.readyState === EventSource.CLOSED
            ? "tape stream closed"
            : "tape stream reconnecting…",
      }));
    };

    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, gen, tz, tickSize, pointValue, contextKey]);

  return state;
}
