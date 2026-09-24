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
//     history with empty rows. **Backtest mode is the one exception** — see
//     `open` below, and docs/backtest-mode-plan.md D9 for why a sat-out rep has
//     to be a row there;
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
import { ApiError, apiSend } from "../lib/api";
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
  /** Which kind of sitting, fixed here and refused by every later patch.
   *
   *  A drill is unpriced (no account ever sees it), binds its model
   *  session-wide when it books, and opens at the drop rather than at the first
   *  fill — see docs/backtest-mode-plan.md. `paper` is a plain replay against
   *  the other account: the same rules and the same floor, priced by
   *  `journal.replay_account`'s paper epochs instead of the funded ones. */
  mode?: "replay" | "paper" | "drill";
  /** Which account pays for this sitting. Stamped at create and never movable
   *  afterwards — the same refusal `mode` gets, and for the same reason:
   *  "relabel the losing sitting" must not be a way to move a loss onto an
   *  account that never took it. Null in a drill, which no account prices.
   *
   *  The server resolves it from the mode when it is absent, so an older client
   *  keeps working; sending it is what lets there be more than two accounts. */
  accountId?: string | null;
  modelId?: number | null;
  /** The clock this rep was thrown in at, and the window it was drawn from.
   *  Neither survives derivation, and the drop histogram is what the mode is
   *  for. */
  dropMs?: number | null;
  window?: { from_ms: number; to_ms: number } | null;
}

export interface AttemptRecord {
  id: string;
  status: string;
  repeat_index: number;
  note: string;
  model_id: number | null;
  /** Marked to come back to. Absent on attempts written before 2026-08-25, so
   *  read it as falsy rather than as a tri-state — never having been asked and
   *  having said no are the same answer here. */
  review_later?: boolean;
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
  /** The resumed sitting's equity path.
   *
   *  `peakUsd`/`troughUsd` are folds and the caller re-derives them from the
   *  rebuilt simulation — the adopted log produces them again exactly.
   *  **`minRoomUsd` is not**, and this is where that asymmetry bites: it was
   *  measured against the floors this sitting was under on its *previous*
   *  visit, which the resumed page does not have and cannot recompute. So the
   *  stored figure is carried in and the page keeps the lower of it and whatever
   *  this visit sees. A resumed sitting that silently forgot a breach would be
   *  the escape hatch the whole account exists to close. */
  peakUsd?: number;
  troughUsd?: number;
  minRoomUsd?: number | null;
}

interface Pending {
  log: Log;
  trades: Trade[];
  clockMs: number;
  /** The sitting's equity excursion so far — see `replayStats.Activity`. */
  peakUsd: number;
  troughUsd: number;
  minRoomUsd: number | null;
}

/** How far the equity path may move before it is worth a write.
 *
 *  The excursion is the one part of a sitting that can move with **nothing else
 *  moving**: a runner that goes +800 and comes back flat books no trade,
 *  cancels no order and drags no bracket, so under a signature made only of the
 *  log it was never written at all — and that is precisely the sitting the
 *  figures exist for. Quantised rather than exact because the alternative is a
 *  request per print; the path only ever widens, so the extra writes are bounded
 *  by its range over the quantum, and the most a settle can under-report by is
 *  one quantum plus one debounce. Both errors lower the floor, never raise it. */
const PATH_QUANTUM_USD = 25;

/** Cheap identity of the *trading* in a simulation: the orders, what was done
 *  to them, and the trades they produced. Trades are in it because a resting
 *  order can fill from the tape with no change to the log at all.
 *
 *  This is the half that says whether a sitting was **traded on**, which is the
 *  only thing that reopens one that has ended — see the status logic in
 *  `flush`. Kept apart from the excursion for exactly that reason. */
function work(log: Log, trades: Trade[]): string {
  const edits = log.orders.reduce((a, o) => a + o.edits.length + (o.cancelMs != null ? 1 : 0), 0);
  const net = trades.reduce((a, t) => a + t.pnl, 0);
  return (
    `${log.orders.length}.${edits}.${log.closes.length}.${log.brackets.length}` +
    `|${trades.length}|${net.toFixed(2)}`
  );
}

/** Cheap identity of a simulation — what has to differ for a write to be worth
 *  making. The trading, plus the excursion, which can move with neither the log
 *  nor the trades changing. */
function sig(log: Log, trades: Trade[], peakUsd: number, troughUsd: number): string {
  const band = (x: number) => Math.trunc(x / PATH_QUANTUM_USD);
  return `${work(log, trades)}|${band(peakUsd)}.${band(troughUsd)}`;
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
  // The account's floor ended this sitting (`kill`). Every save from then on
  // writes `finished`, whatever triggered it — see the status logic in `flush`.
  const killedRef = useRef(false);
  // The trading (`work`) this attempt held when it last ended, or null while it
  // is open. It is what lets `flush` tell trading on from every other reason a
  // settled sitting gets written to — a resume re-marking the excursion, a
  // scrub, a bracket leg cancelling after the flatten. Without it any of those
  // reopened the sitting, and an hour later the stale sweep filed the ended
  // sitting as `abandoned` (`journal.replay_account.sweep_stale_actives`).
  const settledRef = useRef<string | null>(null);
  // The account refused to open this sitting (a 409 on the create). One no is
  // the whole answer: without this seal the debounce would retry the create on
  // every later fill and drag — and each retry replays the refusal, and its
  // cue, on a page that was already told. Cleared by `arm`; a new session is a
  // new question.
  const createRefusedRef = useRef(false);

  const [attempt, setAttempt] = useState<AttemptRecord | null>(null);
  const [summary, setSummary] = useState<AttemptSummary | null>(null);
  const [status, setStatus] = useState<"idle" | "active" | "finished">("idle");
  const [error, setError] = useState<string | null>(null);
  // Separate from `error`: this one is the account saying no, not the write
  // going wrong, and the page shows the two in different places.
  const [refusal, setRefusal] = useState<string | null>(null);

  /** The page clears it once it has shown it — a refusal is a reply, not a
   *  state (the same rule `Simulator`'s own `refused` follows). */
  const clearRefusal = useCallback(() => setRefusal(null), []);

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
      if (createRefusedRef.current && !idRef.current) return;
      const gen = genRef.current;
      const rewinds = rewindsRef.current;
      const discarded = discardedRef.current;
      const mine = () => genRef.current === gen;

      const s = summarize(p.trades, p.log, {
        rewinds,
        discarded,
        clockStartMs: ctx.startedMs,
        clockEndMs: p.clockMs,
        peakUsd: p.peakUsd,
        troughUsd: p.troughUsd,
        minRoomUsd: p.minRoomUsd,
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
              mode: ctx.mode ?? "replay",
              account_id: ctx.accountId ?? null,
              model_id: ctx.modelId ?? null,
              drop_ms: ctx.dropMs ?? null,
              window: ctx.window ?? null,
            })
              .then((rec) => {
                if (mine()) {
                  idRef.current = rec.id;
                  setAttempt(rec);
                  // The create is also where a resettable account becomes a live
                  // one — `replay_account.ensure_epoch` mints the next epoch here
                  // and nowhere else. Until this refetch lands the page is holding
                  // the *dead* epoch's equity and floor, so without it a sitting
                  // opened after a blow-up would run to its end unpriced: no
                  // floor to reach, no meters, and the autopsy of the last
                  // account still up over the new one. Cheap because it is one
                  // request and it only fires on the first fill of a sitting.
                  qc.invalidateQueries({ queryKey: ["replays", "account"] });
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

        // Trading on after a sitting ended — a rewind and another go, or coming
        // back to the day across a reload — reopens it. It is one sitting
        // either way, and calling it finished while trades are still being
        // added to it would be a lie the history page has no way to notice.
        //
        // **Only trading on does.** A settled sitting still gets written to for
        // reasons that are not trading: a resume re-marks the excursion, a
        // scrub moves the clock, a bracket leg cancels a step after the
        // flatten. Those used to write `active` too, which un-finished a
        // sitting nobody had touched — and an hour later the stale sweep filed
        // it `abandoned`. `settledRef` is what tells the two apart; `killed` is
        // the stricter case of the same rule, where even trading on is refused.
        const w = work(p.log, p.trades);
        const settled = settledRef.current;
        const done = finishing || killedRef.current || (settled !== null && settled === w);
        // `null` says nothing about the status, which is what a save that is
        // not about the status should say: writing `finished` over a `reviewed`
        // sitting would drop its review for no reason.
        const next = done ? (settled === w ? null : "finished") : "active";
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
        settledRef.current = done ? w : null;
        setStatus(done ? "finished" : "active");
        setSummary(s);
        setError(null);
        if (finishing) qc.invalidateQueries({ queryKey: ["replays"] });
      } catch (e) {
        if (!mine()) return;
        setError(e instanceof Error ? e.message : String(e));
        // A refused *create* is not an error, it is an answer — the account
        // would not let this sitting open (see `replay_account.refusal`). It
        // gets its own channel because the page has to say it loudly, in the
        // same place a refused order is said, rather than as a small red note
        // beside a blotter that will never fill.
        if (e instanceof ApiError && e.status === 409) {
          const d = e.detail as { message?: string } | null;
          setRefusal(typeof d?.message === "string" ? d.message : e.message);
          if (!idRef.current) createRefusedRef.current = true;
        }
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
      killedRef.current = false;
      settledRef.current = null;
      createRefusedRef.current = false;
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
      setRefusal(null);
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
    const peakUsd = st.peakUsd ?? 0;
    const troughUsd = st.troughUsd ?? 0;
    pendingRef.current = {
      log: st.log, trades: st.trades, clockMs: st.clockMs,
      peakUsd, troughUsd, minRoomUsd: st.minRoomUsd ?? null,
    };
    dirtyRef.current = false;
    sigRef.current = sig(st.log, st.trades, peakUsd, troughUsd);
    // Adopting a sitting that already ended adopts the fact that it ended: the
    // next write only reopens it if the trading has grown since. `reviewed`
    // counts as ended for the same reason `setStatus` below folds it into
    // `finished` — the recorder only cares that it is over.
    settledRef.current =
      rec.status === "finished" || rec.status === "reviewed"
        ? work(st.log, st.trades)
        : null;
    setAttempt(rec);
    // `reviewed` is a finished sitting with its flags answered — see
    // journal.replay_account. The recorder only cares that it is over.
    setStatus(rec.status === "finished" || rec.status === "reviewed" ? "finished" : "active");
    setSummary(
      summarize(st.trades, st.log, {
        rewinds: st.rewinds,
        discarded: st.discarded,
        clockStartMs: st.startedMs,
        clockEndMs: st.clockMs,
        peakUsd, troughUsd, minRoomUsd: st.minRoomUsd ?? null,
      }),
    );
    setError(null);
  }, []);

  /**
   * Open the attempt now, without waiting for a fill. **Backtest mode only.**
   *
   * This is the deliberate exception to rule one at the top of this file. A
   * drill rep where you looked and correctly found nothing is the row backtest
   * mode exists to produce — it is what makes "the setup was there in 12 of 40
   * hours" a sentence that can be written — and under the first-fill rule it
   * leaves no trace at all.
   *
   * It writes an empty sitting rather than only creating the record, because
   * the two things that later ask about it both read files: the stale sweep
   * asks whether there were trades (`trades.json`), and `finish` needs
   * something pending to flush. An attempt with a folder and no trades file is
   * a shape nothing else here has.
   *
   * Idempotent: called again on the same armed session it does nothing, so a
   * re-render or a second effect pass cannot mint two reps for one draw.
   */
  const open = useCallback(
    (log: Log, clockMs: number) => {
      if (!ctxRef.current || idRef.current || creatingRef.current) return;
      // A drill is priced by no account, so its path is nobody's business and
      // starts flat like any other untraded sitting.
      pendingRef.current = {
        log, trades: [], clockMs, peakUsd: 0, troughUsd: 0, minRoomUsd: null,
      };
      dirtyRef.current = true;
      sigRef.current = sig(log, [], 0, 0);
      void flush();
    },
    [flush],
  );

  /** Hand over a freshly published simulation. Cheap on every call but the ones
   *  that changed something. */
  const record = useCallback(
    (
      log: Log,
      trades: Trade[],
      open: boolean,
      clockMs: number,
      path?: { peakUsd: number; troughUsd: number; minRoomUsd: number | null },
    ) => {
      if (!ctxRef.current) return;
      // The first fill is what opens an attempt — a position on, or one already
      // closed. Orders resting and never filled are not a sitting worth keeping.
      if (!idRef.current && !creatingRef.current && !trades.length && !open) return;
      const peakUsd = path?.peakUsd ?? 0;
      const troughUsd = path?.troughUsd ?? 0;
      const s = sig(log, trades, peakUsd, troughUsd);
      if (s === sigRef.current) return;
      sigRef.current = s;
      pendingRef.current = {
        log, trades, clockMs, peakUsd, troughUsd,
        minRoomUsd: path?.minRoomUsd ?? null,
      };
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

  /** The floor caught this sitting. From here every save stays `finished` —
   *  there is no trading on after a death, so anything that still writes is
   *  the flatten's own bookkeeping, not a reopening. Cleared by `arm`. */
  const kill = useCallback(() => {
    killedRef.current = true;
  }, []);

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

  /** Mark this sitting to review later, or take the mark off.
   *
   *  The whole of what an unreviewed sitting can owe since 2026-08-25 — nothing
   *  waits on it, so this is a note to yourself and the history page is where it
   *  is read. Written through the same PATCH the note is, and for the same
   *  reason it lives here rather than in a mutation: the record it updates is
   *  this hook's own state, and a react-query cache entry would not be it. */
  const setReviewLater = useCallback(async (flag: boolean) => {
    const id = idRef.current;
    if (!id) return;
    const rec = await apiSend<AttemptRecord>("PATCH", `/replays/${id}`, {
      review_later: flag,
    });
    setAttempt(rec);
    // The history page and the account chip both count these, and neither is
    // looking at this hook. One key does both — the account query lives under
    // `["replays", "account", …]` precisely so it rides on this invalidation.
    void qc.invalidateQueries({ queryKey: ["replays"] });
  }, [qc]);

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

  return {
    attempt, attemptId, summary, status, error, refusal, clearRefusal,
    arm, adopt, open, record, noteRewind, finish, kill, setNote, setReviewLater,
  };
}
