// The Simulator's recorder: turns a sitting at the replay into a stored attempt.
//
// It watches, it doesn't drive. The page keeps trading exactly as it did — the
// log is still the ground truth, the simulation is still derived from it — and
// this hook is handed each fresh simulation as it is published, decides whether
// anything is worth writing, and writes it.
//
// Three rules, all of them consequences of what the page already does:
//
//   - an attempt opens on the *first fill*, not on page load. A session you
//     watched and didn't trade leaves no record, so idle poking can't fill the
//     history with empty rows;
//   - writes are debounced and skipped when nothing changed, so playing an hour
//     of tape without touching anything costs zero requests;
//   - a rewind past a fill is recorded rather than silently absorbed. `seekTo`
//     truncates the log — that is what makes re-taking a setup coherent — but it
//     also means a stop you rewound out of never happened. The attempt keeps the
//     erased trades and the seek that erased them, so a track record can say
//     which of its numbers were written with the answer already in hand.
//
// A fourth rule follows from the first three once the page can resume: a sitting
// picked back up is the *same* sitting. `adopt` points the recorder at an
// attempt that already exists rather than at a blank one, so a reload writes
// back into the record it came from instead of minting a second attempt on the
// same day — which the history page would read, correctly by its own rules and
// wrongly in fact, as a re-run of a session you had already seen the end of.

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiSend } from "../lib/api";
import type { Log, Trade } from "../lib/replaySim";
import {
  SIM_ENGINE_VERSION,
  summarize,
  type AttemptSummary,
  type RewindEvent,
} from "../lib/replayStats";

/** How long a change sits before it is written. Long enough to coalesce a burst
 *  of drags into one request, short enough that a crash costs a couple of
 *  seconds of a session rather than the session. */
const SAVE_DEBOUNCE_MS = 1500;

/** Everything about the tape and the ticket that an attempt is stamped with.
 *  Read once, when the attempt opens. */
export interface AttemptContext {
  symbol: string;
  root: string;
  date: string;
  tz: string;
  /** Cheap tape fingerprint: enough to notice later that the day was re-fetched
   *  and the stored tick indices no longer point where they did. */
  tape: { n: number; t0: number; end: number; rth_open_ms: number };
  /** Read at the moment the attempt opens, not when the session loaded: the
   *  ticket you took the first trade with is the one worth stamping. */
  prefs: () => Record<string, unknown>;
  startedMs: number;
}

export interface AttemptRecord {
  id: string;
  status: string;
  repeat_index: number;
  note: string;
  model_id: number | null;
}

/** What `adopt` needs about the attempt it is taking over, beyond the record
 *  itself: the state the log was last saved in, so the recorder can pick up
 *  from it rather than treat it as a change to write back. */
export interface AdoptState {
  log: Log;
  trades: Trade[];
  rewinds: RewindEvent[];
  discarded: Trade[];
  /** The clock the attempt opened at, which is not this visit's start time — a
   *  resumed sitting spans from the first fill of the *first* visit. */
  startedMs: number;
  clockMs: number;
}

interface Pending {
  log: Log;
  trades: Trade[];
  clockMs: number;
}

/** Cheap identity of a simulation — what has to differ for a write to be worth
 *  making. Trades are in it because a resting order can fill from the tape with
 *  no change to the log at all. */
function sig(log: Log, trades: Trade[]): string {
  const edits = log.orders.reduce((a, o) => a + o.edits.length + (o.cancelMs != null ? 1 : 0), 0);
  const net = trades.reduce((a, t) => a + t.pnl, 0);
  return `${log.orders.length}.${edits}.${log.closes.length}.${log.brackets.length}|${trades.length}|${net.toFixed(2)}`;
}

export function useReplayAttempt() {
  const qc = useQueryClient();

  const ctxRef = useRef<AttemptContext | null>(null);
  const idRef = useRef<string | null>(null);
  // The create request in flight, so a burst of fills opens one attempt.
  const creatingRef = useRef<Promise<string | null> | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  // Whether what's pending differs from what's on disk. Kept apart from
  // `pendingRef`, which holds the latest state whether or not it was written:
  // ending an attempt has to write even when nothing changed since the last
  // autosave, and an unmount must not write when nothing did.
  const dirtyRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const sigRef = useRef("");
  const rewindsRef = useRef<RewindEvent[]>([]);
  const discardedRef = useRef<Trade[]>([]);
  // Bumped every time the recorder is pointed at a new session. A save that was
  // in flight across that moment still finishes — it belongs to the attempt it
  // started for, and its id was read before the switch — but it must not report
  // back, or the new session would inherit the old one's status and summary.
  const genRef = useRef(0);

  const [attempt, setAttempt] = useState<AttemptRecord | null>(null);
  const [summary, setSummary] = useState<AttemptSummary | null>(null);
  const [status, setStatus] = useState<"idle" | "active" | "finished">("idle");
  const [error, setError] = useState<string | null>(null);

  const clearTimer = () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  /** Write whatever is pending, now. Returns once the request has landed, so
   *  finishing an attempt can wait for it. */
  const flush = useCallback(
    async (finishing = false) => {
      clearTimer();
      // Everything this write is about, read *before* the first await: the
      // attempt it belongs to has to survive the recorder being pointed
      // somewhere else halfway through.
      const p = pendingRef.current;
      const ctx = ctxRef.current;
      if (!p || !ctx || (!dirtyRef.current && !finishing)) return;
      const gen = genRef.current;
      const rewinds = rewindsRef.current;
      const discarded = discardedRef.current;
      const mine = () => genRef.current === gen;

      const s = summarize(p.trades, p.log, {
        rewinds,
        discarded,
        clockStartMs: ctx.startedMs,
        clockEndMs: p.clockMs,
      });

      try {
        let id = idRef.current;
        if (!id) {
          if (!creatingRef.current) {
            creatingRef.current = apiSend<AttemptRecord>("POST", "/replays", {
              symbol: ctx.symbol,
              root: ctx.root,
              date: ctx.date,
              tz: ctx.tz,
              engine_version: SIM_ENGINE_VERSION,
              tape: ctx.tape,
              prefs: ctx.prefs(),
              started_ms: ctx.startedMs,
            })
              .then((rec) => {
                if (mine()) {
                  idRef.current = rec.id;
                  setAttempt(rec);
                }
                return rec.id;
              })
              .finally(() => {
                if (mine()) creatingRef.current = null;
              });
          }
          id = await creatingRef.current;
        }
        if (!id) return;

        // Trading on after the tape ran out — a rewind and another go — reopens
        // the attempt. It is one sitting either way, and calling it finished
        // while trades are still being added to it would be a lie the history
        // page has no way to notice.
        const next = finishing ? "finished" : "active";
        await apiSend("PUT", `/replays/${id}`, {
          log: p.log,
          trades: p.trades,
          summary: s,
          discarded,
          rewinds,
          clock_ms: p.clockMs,
          status: next,
        });
        if (!mine()) return;
        dirtyRef.current = false;
        setStatus(next);
        setSummary(s);
        setError(null);
        if (finishing) qc.invalidateQueries({ queryKey: ["replays"] });
      } catch (e) {
        if (mine()) setError(e instanceof Error ? e.message : String(e));
      }
    },
    [qc],
  );

  /** Point the recorder at a session. Called whenever a new tape loads: whatever
   *  was being recorded is let go of here, not finalized — an attempt you walked
   *  away from is settled when you come back to it, not behind your back. */
  const arm = useCallback(
    (ctx: AttemptContext) => {
      // Whatever the last session still owed, pay it before letting go — it
      // keeps its own id and context, and the generation bump below stops it
      // reporting back into this one.
      if (dirtyRef.current) void flush();
      clearTimer();
      genRef.current += 1;
      ctxRef.current = ctx;
      idRef.current = null;
      creatingRef.current = null;
      pendingRef.current = null;
      dirtyRef.current = false;
      sigRef.current = "";
      rewindsRef.current = [];
      discardedRef.current = [];
      setAttempt(null);
      setSummary(null);
      setStatus("idle");
      setError(null);
    },
    [flush],
  );

  /**
   * Take over an attempt that already exists, instead of waiting for a fill to
   * open a new one.
   *
   * Called straight after `arm` when a session is resumed from a bookmark: the
   * log the page just restored is the one this attempt already holds, so the
   * continuation belongs in the same record. Nothing is written here — the state
   * being adopted is by definition what is on disk — and the signature is primed
   * with it, so the first `record` after a resume is the no-op it should be and
   * a reload costs zero requests until you actually do something.
   *
   * Adopting a *finished* attempt is allowed and is not a contradiction: the
   * tape ran out, and trading on after a rewind already reopens it (see the
   * status logic in `flush`). Coming back to the same day is the same move made
   * across a page load.
   */
  const adopt = useCallback((rec: AttemptRecord, st: AdoptState) => {
    // Only ever into a session `arm` has already pointed us at — the context
    // carries the tape fingerprint this attempt's cursors are measured against.
    if (!ctxRef.current) return;
    ctxRef.current = { ...ctxRef.current, startedMs: st.startedMs };
    idRef.current = rec.id;
    rewindsRef.current = st.rewinds;
    discardedRef.current = st.discarded;
    pendingRef.current = { log: st.log, trades: st.trades, clockMs: st.clockMs };
    dirtyRef.current = false;
    sigRef.current = sig(st.log, st.trades);
    setAttempt(rec);
    setStatus(rec.status === "finished" ? "finished" : "active");
    setSummary(
      summarize(st.trades, st.log, {
        rewinds: st.rewinds,
        discarded: st.discarded,
        clockStartMs: st.startedMs,
        clockEndMs: st.clockMs,
      }),
    );
    setError(null);
  }, []);

  /** Hand over a freshly published simulation. Cheap on every call but the ones
   *  that changed something. */
  const record = useCallback(
    (log: Log, trades: Trade[], open: boolean, clockMs: number) => {
      if (!ctxRef.current) return;
      // The first fill is what opens an attempt — a position on, or one already
      // closed. Orders resting and never filled are not a sitting worth keeping.
      if (!idRef.current && !creatingRef.current && !trades.length && !open) return;
      const s = sig(log, trades);
      if (s === sigRef.current) return;
      sigRef.current = s;
      pendingRef.current = { log, trades, clockMs };
      dirtyRef.current = true;
      clearTimer();
      timerRef.current = window.setTimeout(() => void flush(), SAVE_DEBOUNCE_MS);
    },
    [flush],
  );

  /** A seek went back past a fill. `dropped` are the trades it un-happened. */
  const noteRewind = useCallback((from_ms: number, to_ms: number, dropped: Trade[]) => {
    if (!ctxRef.current) return;
    rewindsRef.current = [...rewindsRef.current, { from_ms, to_ms, dropped: dropped.length }];
    discardedRef.current = [...discardedRef.current, ...dropped];
  }, []);

  /** End the attempt: flush and stamp it finished. No-op when nothing was ever
   *  traded, so pressing it on an untouched session doesn't mint a row. */
  const finish = useCallback(async () => {
    if (!idRef.current && !dirtyRef.current) return;
    await flush(true);
  }, [flush]);

  /** The attempt currently open, if one is, read straight off the ref rather
   *  than out of React state. The id arrives from a POST and reaches `attempt`
   *  only on the render after — which is one render too late for anything that
   *  has to name the attempt from inside an effect (the resume bookmark does). */
  const attemptId = useCallback(() => idRef.current, []);

  const setNote = useCallback(async (note: string) => {
    const id = idRef.current;
    if (!id) return;
    const rec = await apiSend<AttemptRecord>("PATCH", `/replays/${id}`, { note });
    setAttempt(rec);
  }, []);

  // A tab going away takes the debounce with it, so spend it first. Covers the
  // ordinary ways a session ends — switching apps, locking the screen, closing
  // the tab — where nothing else gets a chance to run.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden" && dirtyRef.current) void flush();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, [flush]);

  useEffect(
    () => () => {
      // Navigating away mid-attempt: write what's pending and let it fly. The
      // attempt stays `active` — it is unfinished, and saying so is the point.
      if (dirtyRef.current) void flush();
      clearTimer();
    },
    [flush],
  );

  return { attempt, attemptId, summary, status, error, arm, adopt, record, noteRewind, finish, setNote };
}
