// The stored replay attempts — the practice record behind /simulator/history.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { Log, Trade } from "../lib/replaySim";
import type { ReplayFlag } from "../lib/replayAccount";
import type { AttemptSummary, RewindEvent } from "../lib/replayStats";
import type { WhatIfSpec } from "../lib/whatIfPrefs";

export interface AttemptRow {
  id: string;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  symbol: string;
  root: string;
  date: string;
  tz: string;
  engine_version: number;
  tape: { n: number; t0: number; end: number; rth_open_ms: number };
  prefs: Record<string, unknown>;
  /** `reviewed` is `finished` plus the answer — see `journal.replay_account`.
   *  It used to be what the next sitting's create gate read; since 2026-08-25
   *  nothing gates on it and `finished` is a terminal state in its own right. */
  status: "active" | "finished" | "abandoned" | "reviewed";
  /** Marked to come back to, by you, when the sitting ended or from the history
   *  page. The only thing an unreviewed sitting carries — nothing waits on it.
   *  Absent on everything written before 2026-08-25, so read it as falsy. */
  review_later?: boolean;
  started_ms: number;
  clock_ms: number;
  /** How many attempts on this session came before it. 0 is a cold read. */
  repeat_index: number;
  note: string;
  model_id: number | null;
  /** Which kind of sitting — and, for the two priced ones, which account it
   *  cost. Absent on every attempt recorded before backtest mode existed, which
   *  is why nothing reads it without the `?? "replay"`. */
  mode?: "replay" | "paper" | "drill";
  /** Which account priced this sitting. Absent on everything recorded before
   *  accounts were a registry — `replayAccount.accountIdOf` reads that absence
   *  the same way the server does, so no row on disk had to be touched. */
  account_id?: string | null;
  /** Drill only: the clock this rep was thrown in at, and the window it was
   *  drawn from. Both tape wall clocks. */
  drop_ms?: number | null;
  window?: { from_ms: number; to_ms: number } | null;
  rewinds: RewindEvent[];
  discarded_trades: number;
  summary: Partial<AttemptSummary>;
  /** Which account rules this sitting tripped, recorded when it finished.
   *  Absent on an attempt that finished before any of this existed, and on one
   *  still running. (Attempts filed before 2026-08-20 also carry a `review` of
   *  retired flag verdicts; nothing reads it — see `journal.replays.patch`.) */
  flags?: ReplayFlag[];
}

export interface AttemptDetail extends AttemptRow {
  log: Log;
  trades: Trade[];
  discarded: Trade[];
}

/** The attempt a journal row came out of, or null for a row that came from
 *  anywhere else. A sitting's mirrored trades carry `replay/<attempt id>` as
 *  their source file (`journal/live/booking.py`); an imported broker export
 *  carries the export's own name and has no sitting behind it. */
export function attemptIdOf(sourceFile: string | null | undefined): string | null {
  const prefix = "replay/";
  return sourceFile?.startsWith(prefix) ? sourceFile.slice(prefix.length) : null;
}

/** Fetch one attempt through the same cache `useReplayAttemptDetail` fills, for
 *  the callers that need it inside an event rather than across a render — the
 *  day replayer arms a trade with the bracket that trade was placed with, and
 *  which trade that is only becomes known when the ▶ is pressed. A finished
 *  sitting never moves, so this is one request per attempt per session. */
export function fetchReplayAttemptDetail(
  qc: ReturnType<typeof useQueryClient>,
  id: string,
): Promise<AttemptDetail> {
  return qc.fetchQuery({
    queryKey: ["replays", "detail", id],
    queryFn: () => apiGet<AttemptDetail>(`/replays/${id}`),
    staleTime: Infinity,
  });
}

/** `enabled` is for the pages that only want this under one condition — the
 *  Simulator draws the autopsy off it and only when the account is dead, and
 *  fetching every attempt on every replay visit to answer a question nobody
 *  asked is a request per page load for nothing. */
export function useReplayAttempts(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["replays", "list"],
    queryFn: () => apiGet<{ attempts: AttemptRow[] }>("/replays"),
    enabled: opts?.enabled ?? true,
  });
}

/** The journal rows one attempt produced, with the keys notes hang off.
 *
 *  Backtest mode's review needs these and cannot derive them: a trade_key is a
 *  hash of the trade's own content, written on the server when the mirror runs.
 *  Enabled only once a rep is over, because that is the only moment the answer
 *  is stable — the mirror rewrites this attempt's rows on every autosave. */
export function useReplayJournal(id: string | null, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["replays", "journal", id],
    queryFn: () =>
      apiGet<{ trades: DrillTradeRow[] }>(`/replays/${id}/journal`, { include_archived: 1 }),
    enabled: !!id && (opts?.enabled ?? true),
  });
}

export interface DrillCampaign {
  model_id: number | null;
  reps: number;
  traded_reps: number;
  sat_out: number;
  /** Reps that were seeked backwards at some point, and so are not cold reads.
   *  Counted inside `reps` like any other — reported separately so the base
   *  rate can be read with the hindsight in it named. */
  rewound_reps: number;
  /** Traded reps over settled reps — how often the model was there at all. Null
   *  when there are no reps to divide by. */
  base_rate: number | null;
  trades: number;
  net_usd: number;
  expectancy: number | null;
  drawn_by_hour: { hour: number; reps: number }[];
  traded_by_hour: { hour: number; reps: number }[];
}

/** A model's backtest-mode campaign. Read off attempts rather than trades: a
 *  sat-out rep has no trades, and a trades-based aggregate would report a base
 *  rate of 100% forever. */
export function useDrillCampaign(modelId: number | null) {
  return useQuery({
    queryKey: ["replays", "drills", modelId],
    queryFn: () => apiGet<DrillCampaign>("/replays/drills", { model_id: modelId }),
    enabled: modelId != null,
  });
}

/** File one trade's whole review in one write — the grade, the level it was
 *  taken off, the tags, the note, and the model's rule checks.
 *
 *  Straight onto `PUT /notes/{trade_key}`, which takes all of it. It used to be
 *  two writes because the thesis lived on its own table to keep either from
 *  blanking the other; the grade and the watched level are *partial* fields on
 *  this endpoint instead (omitted means unchanged), so one call does it.
 *
 *  Everything else the PUT overwrites wholesale, which is why the payload takes
 *  every field rather than a delta: the caller echoes what the journal rows
 *  carried (`setups`/`confluences` ride on them for exactly this) so a review
 *  save cannot blank taxonomy written elsewhere. */
export function useSaveTradeReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tradeKey, ...body }: {
      tradeKey: string;
      note: string;
      tags: string[];
      setups: string[];
      confluences: string[];
      modelId: number | null;
      rulesMet: number[];
      /** The grade is deliberately absent: it is written by the recall
       *  front (`POST /recall/rate`), the one surface that asks it blind. */
      setup?: string | null;
      discipline?: string | null;
      /** Omitted leaves the stored set alone; a list replaces it, `[]` included
       *  — deselecting the last level has to be a thing that persists. */
      watchedLevels?: string[];
    }) =>
      apiSend<{ ok: boolean }>("PUT", `/notes/${tradeKey}`, {
        note: body.note,
        tags: body.tags,
        setups: body.setups,
        confluences: body.confluences,
        model_id: body.modelId,
        rules_met: body.rulesMet,
        setup: body.setup ?? null,
        discipline: body.discipline ?? null,
        watched_levels: body.watchedLevels ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["replays", "journal"] });
      // A rule check moves the trade between compliance buckets; a tag joins
      // the shared vocabulary the autocomplete offers.
      qc.invalidateQueries({ queryKey: ["model-stats"] });
      qc.invalidateQueries({ queryKey: ["note"] });
      qc.invalidateQueries({ queryKey: ["trade-tags"] });
      // Recall's back card reads the same three answers and is the third
      // surface that writes them, so a correction made there has to land on
      // the panel that is still on screen showing the old one.
      qc.invalidateQueries({ queryKey: ["recall-back"] });
      // The Trades table and the Review page render the axes per row now.
      qc.invalidateQueries({ queryKey: ["trades"] });
    },
  });
}

/** Every tag ever used on any trade — the shared autocomplete vocabulary.
 *  Most-used first; the server orders it. The taxonomy that grows by being
 *  typed, which is what makes it the review's diagnosis field. */
export function useTradeTags(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["trade-tags"],
    queryFn: () => apiGet<{ tags: string[] }>("/notes/tags"),
    enabled: opts?.enabled ?? true,
  });
}

/** The grading scale, served rather than hardcoded: a picker offering a fifth
 *  grade would collect a value the gate, the cuts and the deck have never heard
 *  of (`GET /review/vocab`). The level *options* are not here — they are a fact
 *  about one fill and ride on the journal row. */
export function useReviewVocab() {
  return useQuery({
    queryKey: ["review-vocab"],
    queryFn: () =>
      apiGet<{
        grades: { id: string; says: string }[];
        setups: { id: string; says: string }[];
        disciplines: { id: string; says: string }[];
        no_level: string;
      }>("/review/vocab"),
    staleTime: Infinity,
  });
}

/** One level measured near a fill, as a picker option.
 *
 *  Ordered by distance by the server, never by `rank` — see
 *  `_level_candidates`. `rank` rides along to be shown *beside* an option (0 is
 *  tighter than every drift-matched companion instant), which is a measurement
 *  offered rather than a suggestion made; it is present only on the one member
 *  its family was scored through, so most options carry a distance and no rank. */
export interface LevelCandidate {
  /** The level id — `gxVP_poc` — and what a pick stores (one of several). */
  id: string;
  label: string;
  /** Which collinear group it belongs to. Not the answer any more: a family
   *  collapses to its nearest member, so it could never say *which* VAH. */
  family: string;
  dist_ticks: number | null;
  rank: number | null;
}

export interface DrillTradeRow {
  trade_key: string;
  direction: string | null;
  entry_ts_local: string;
  net_pnl: number;
  model_id: number | null;
  rules_met: number[];
  reviewed: boolean;
  note: string;
  tags: string[];
  /** Curated taxonomy echoed back on save, never edited by the review. */
  setups: string[];
  confluences: string[];
  /** The review. `setup`/`discipline` are the two enumerated axes;
   *  `watched_levels` holds level ids, or the single literal "none". Null/empty
   *  until answered. `grade` rides along read-only — it is captured at the
   *  recall front, not in any panel. */
  grade: string | null;
  setup: string | null;
  discipline: string | null;
  watched_levels: string[];
  /** What the tagger measured near this entry, for the picker. Empty when the
   *  gate asked (it does not need them) or when the day was never cached —
   *  which is a real state, and what "none" is the answer to. */
  levels: LevelCandidate[];
  /** What the tape says about the moment — read-only chips beside the form, so
   *  the reviewer stops transcribing "Chopping" into tags. Empty when the day
   *  was never measured, or when the gate asked. */
  context_chips: ContextChip[];
}

export interface ContextChip {
  key: string;
  label: string;
  /** Where the derivation is admitted. */
  title: string;
}

export function useReplayAttemptDetail(id: string | null) {
  return useQuery({
    queryKey: ["replays", "detail", id],
    queryFn: () => apiGet<AttemptDetail>(`/replays/${id}`),
    enabled: !!id,
  });
}

export function useDeleteReplayAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiSend<{ ok: boolean }>("DELETE", `/replays/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}

/** File the review that clears a sitting.
 *
 *  The answers themselves are already stored — each card writes its own journal
 *  row (`PUT /notes`), and the panel flushes whatever is still dirty before it
 *  calls this. So this request only asks for the status, which the server grants
 *  by reading those rows. Invalidating `["replays"]` refreshes the account too,
 *  which is the thing that was waiting on this.
 *
 *  `note` is the sitting's own — what the session was, in your words, as against
 *  the per-trade answers that went to the journal rows. It rides in the same
 *  PATCH because filing it and filing the review are one act, and lands on the
 *  attempt's existing `note` field, which the server also pushes onto the
 *  journal session row — so the history table and the Trades page read the same
 *  sentence. */
export function useFileReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      apiSend<AttemptRow>("PATCH", `/replays/${id}`, {
        status: "reviewed",
        // Sent only when the panel offered the field, so a caller that does not
        // ask for a note cannot blank one already stored (the server treats a
        // present `note` as the new value, empty string included).
        ...(note === undefined ? {} : { note }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}

/** Lowest, median and highest across a sitting's trades. */
export interface WhatIfSpan {
  lo: number;
  med: number;
  hi: number;
}

export interface WhatIfCell {
  n: number;
  net: number;
  wr: number;
  avg_win: number;
  avg_loss: number;
  /** The initial bracket these trades ran, in ticks — ranges because the ticket
   *  sizes the stop per fill and stores the target as an absolute price, so most
   *  sittings run neither leg at one distance. A row that overrides a leg
   *  collapses that leg to a single value. */
  stop_ticks: WhatIfSpan | null;
  target_ticks: WhatIfSpan | null;
  reasons: Record<string, number>;
}

export interface WhatIfRow {
  key: string;
  label: string;
  spec: WhatIfSpec;
  /** Yours rather than the server's ladder — the only rows that can be removed. */
  custom: boolean;
  /** Whether recorded bracket drags were kept. True on the As played row alone. */
  drags_kept: boolean;
  /** Hand flattens honoured, and the same row with the bracket left to decide. */
  clicked: WhatIfCell;
  forget: WhatIfCell;
}

export interface WhatIfGrid {
  valid: boolean;
  reason: string | null;
  stored: { n: number; net: number };
  cfg: Record<string, number> | null;
  /** The sitting ended holding something, so every row books it at the close and
   *  the As played row reads that much away from the stored net. */
  open_marked: boolean;
  tape_drift: boolean;
  rows: WhatIfRow[];
}

/** This sitting's fills re-priced under other exits.
 *
 *  A POST because the custom rows are the body, and `enabled` because the answer
 *  costs a re-simulation per row on the server — the section fetches when it is
 *  opened and not when a day is. Finished sittings do not move, so what comes
 *  back is cached for the session. */
export function useWhatIf(
  attemptId: string | null,
  custom: WhatIfSpec[],
  opts?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["replays", "whatif", attemptId, custom],
    queryFn: () => apiSend<WhatIfGrid>("POST", `/replays/${attemptId}/whatif`, { custom }),
    enabled: !!attemptId && (opts?.enabled ?? true),
    staleTime: Infinity,
  });
}

export function usePatchReplayAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      note?: string;
      model_id?: number | null;
      status?: string;
      review_later?: boolean;
    }) => apiSend<AttemptRow>("PATCH", `/replays/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}
