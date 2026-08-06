// Reading a session that is still happening.
//
// Three of the four reads are ordinary polls and go through react-query. The
// tape is not: one poll's payload is thousands of JS numbers that exist only to
// be prefix-summed into typed arrays and thrown away, and holding them in a query
// cache would keep every block of the day alive alongside the tape they were
// already folded into. Same reasoning as `useSimulatorHistory` — decode and drop
// is the whole point, so the tape poll is a hand-rolled loop.

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { createGrowableTape, type GrowableTape, type TapeBlock } from "../lib/growableTape";
import type { LiveHeader, LiveSignals, LiveStatus } from "../lib/liveTypes";

/** How often the tape is asked for what has arrived. Fast enough that the chart
 *  moves like a feed, slow enough that a session is a few thousand requests and
 *  not a few hundred thousand. The chart's own clock comes from the last print
 *  received, so a slower poll shows as a slightly staler chart, never a wrong one. */
const TAPE_POLL_MS = 500;
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
}) {
  return apiSend<{ gen: string; recording: boolean; signals: boolean }>(
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
 * Keep a growing tape fed from `/live/tape`.
 *
 * `onReset` fires with a brand-new tape whenever the session underneath changes
 * — a restart, a different day — because the row indices the caller was holding
 * describe a tape that no longer exists. `onAppend` fires after every block that
 * carried rows, which is the cue to advance the chart.
 *
 * Polling is a `setTimeout` chain rather than `setInterval`: a slow reply must
 * delay the next request, not stack a second one behind it.
 */
export function useLiveTape(opts: {
  enabled: boolean;
  gen: string | null;
  tz: string;
  tickSize: number;
  pointValue: number;
  onReset: (tape: GrowableTape) => void;
  onAppend: (tape: GrowableTape, added: number) => void;
}): LiveTapeState {
  const { enabled, gen, tz, tickSize, pointValue } = opts;
  const [state, setState] = useState<LiveTapeState>({ rows: 0, closed: false, error: null });
  // The callbacks are re-made on every render of a page that renders often. Held
  // in a ref so the poll loop is not torn down and restarted for that — the loop's
  // identity belongs to the session, not to a render.
  const cb = useRef(opts);
  cb.current = opts;

  useEffect(() => {
    if (!enabled || !gen) return;
    let cancelled = false;
    let timer: number | undefined;
    let tape: GrowableTape | null = null;
    let cursor = 0;

    const tick = async () => {
      try {
        const block = await apiGet<TapeBlock>("/live/tape", { since: cursor, gen, tz });
        if (cancelled) return;
        if (!tape || block.reset) {
          tape = createGrowableTape(tickSize, pointValue);
          cursor = 0;
          cb.current.onReset(tape);
        }
        if (block.n > 0) {
          tape.append(block);
          // Advance on `next`, never on `rows`: the tape kept growing while the
          // request was being served, so `rows` is ahead of the block and using
          // it as the cursor would skip every tick in between.
          cursor = block.next;
          cb.current.onAppend(tape, block.n);
        }
        setState({ rows: block.rows, closed: block.closed, error: null });
      } catch (e) {
        if (cancelled) return;
        // A failed poll is not a failed session: the next one asks from the same
        // cursor, so a blip costs latency and never a tick.
        setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
      }
      if (!cancelled) timer = window.setTimeout(tick, TAPE_POLL_MS);
    };
    void tick();

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, gen, tz, tickSize, pointValue]);

  return state;
}
