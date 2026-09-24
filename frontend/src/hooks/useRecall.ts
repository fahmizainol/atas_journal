import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { LevelCandidate } from "./useReplays";

// Lab → Recall. Two reads, deliberately split: the deck is the *front* of every
// due card and carries nothing that would answer it, and the back is fetched on
// flip. See api/routers/recall.py — the split is the blindness mechanism, not a
// payload-size optimisation.

/** SM-2's four buttons, in the order they are shown. */
export const RATINGS = [
  { id: 1, label: "Again", says: "no idea — start this one over" },
  { id: 2, label: "Hard", says: "got there, but it was a struggle" },
  { id: 3, label: "Good", says: "read it" },
  { id: 4, label: "Easy", says: "instant — push it out further" },
] as const;

export interface RecallCardFront {
  trade_key: string;
  /** The tape's contract, for loading the session. Masked in the UI: the front
   *  shows `root` only, because a month letter dates the chart. */
  symbol: string;
  root: string;
  /** Masked too (`hideDates`), but unavoidable on the front — the tape cannot be
   *  fetched without it. */
  date: string;
  /** Where the front's tape stops. Jittered near the entry and stored, so it is
   *  neither a tell nor a moving target. */
  cut_ms: number;
  /** Whether this trade still owes its grade. The front is where grading
   *  happens — picked at the fill-freeze, locked by the flip, sent with the
   *  rating. Says nothing about what the trade was, only that a question is
   *  due. */
  needs_grade: boolean;
  reps: number;
  lapses: number;
  due: string | null;
}

export interface RecallRep {
  shown_at: string;
  rating: number;
  guess: string | null;
}

/** The order behind the fill: how the position was opened and what it was
 *  bracketed with, read off the sitting on disk (api/routers/recall.py). Back
 *  only — an order type says whether the entry was waited for and a stop says
 *  how much room the trade was given, which is the read the card is asking for. */
export interface RecallOrder {
  open_type: "market" | "limit" | "stop";
  exit_reason: "manual" | "stop" | "target" | "reduce" | "trail";
  /** Where a resting entry waited, and for how long before it filled (ms). Null
   *  on a market order, which is its own fill. */
  rest_price: number | null;
  rest_ms: number | null;
  /** The bracket the position **opened** with — the risk that was accepted, not
   *  necessarily the levels the exit fired against. */
  stop: number | null;
  target: number | null;
  /** The two reasons an exit can land where the opening bracket doesn't explain:
   *  a trail riding the stop, and a drag that moved it while the trade was on. */
  trail_pts: number | null;
  trail_be_only: boolean;
  moved: boolean;
}

export interface RecallBack {
  trade_key: string;
  direction: string | null;
  max_contracts: number;
  avg_entry: number;
  avg_exit: number;
  net_pnl: number;
  entry_ts_local: string;
  exit_ts_local: string;
  trade_no: number;
  /** Null for a trade that was never a sitting — most of the journal is an
   *  imported broker export, and there is no order log behind those rows. */
  order: RecallOrder | null;
  grade: string | null;
  setup: string | null;
  discipline: string | null;
  watched_levels: string[];
  watched_labels: string[];
  tags: string[];
  note: string;
  /** The review's picker options — every level measured at this entry, nearest
   *  first. Back-only: a list of what sat around the fill describes the chart
   *  the front is asking about. */
  levels: LevelCandidate[];
  /** Never shown. Echoed straight back on save because `PUT /notes` overwrites
   *  the whole row, so a review corrected here would otherwise blank taxonomy
   *  and rule checks written on the trade page. */
  setups: string[];
  confluences: string[];
  model_id: number | null;
  rules_met: number[];
  /** The review's session box — what the whole sitting was, in your words.
   *  Empty when none was written. Back-only, like everything else here. */
  session_note: string;
  reps: RecallRep[];
}

/** The last rating, while it can still be taken back. Card and rating only —
 *  the deck is the front's payload and carries nothing else. */
export interface RecallUndo {
  trade_key: string;
  rating: number | null;
  staged_at: string | null;
}

export function useRecallDeck() {
  return useQuery({
    queryKey: qk.recallDeck,
    queryFn: () =>
      apiGet<{
        cards: RecallCardFront[];
        total: number;
        next_due: string | null;
        undo: RecallUndo | null;
      }>("/recall/deck"),
  });
}

/** The answer. Enabled only once the card is flipped — asking for it earlier
 *  would put it in the cache while the front is still on screen, which is the
 *  one thing the split endpoint exists to prevent. */
export function useRecallBack(tradeKey: string | null) {
  return useQuery({
    queryKey: qk.recallBack(tradeKey ?? ""),
    queryFn: () => apiGet<RecallBack>(`/recall/back/${tradeKey}`),
    enabled: !!tradeKey,
  });
}

export function useRecallRate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      trade_key: string;
      rating: number;
      guess?: string | null;
      /** The blind grade, when the front collected one. Only sent for a trade
       *  the deck said `needs_grade` — the server refuses a second grade. */
      grade?: string | null;
    }) =>
      apiSend<{ ok: boolean; card: { due: string; interval_d: number; reps: number } }>(
        "POST",
        "/recall/rate",
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.recallDeck }),
  });
}

/** Take back the last rating. Restores the schedule, the rep log and the
 *  Arena's state to what they were, and hands the guess back so the read typed
 *  before the misclick is not lost with it.
 *
 *  The back is invalidated as well as the deck: it carries the card's own rep
 *  history, and the showing that was just deleted must not still be listed
 *  under "what you said the last time(s)". */
export function useRecallUndo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ ok: boolean; trade_key: string; rating: number | null; guess: string | null }>(
        "POST",
        "/recall/undo",
      ),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: qk.recallDeck });
      qc.invalidateQueries({ queryKey: qk.recallBack(res.trade_key) });
    },
  });
}
