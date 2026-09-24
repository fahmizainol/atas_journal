import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type {
  DailyPnlPoint,
  EquityPoint,
  Metrics,
  Note,
  SummaryExtras,
  TradeRow,
} from "../lib/types";

export interface CalendarMonth {
  year: number;
  month: number;
  label: string;
}
/** One trading day, read as its **latest attempt alone** — not as a sum.
 *
 *  `net_pnl`, `trades`, `win_rate` and `account` all describe the one take the
 *  day finished on (the day explorer opens that same take by default). `attempts`
 *  counts every in-scope take that exists, so a day showing one of several says
 *  so. Everything outside the calendar — statistics, overview — still sums every
 *  attempt; see the note in `api/routers/calendar.py`. */
export interface CalendarDay {
  date: string;
  net_pnl: number;
  trades: number;
  win_rate: number;
  attempts: number; // distinct in-scope takes on this day; >1 means re-done
  account: string | null; // the account the shown take traded
  file_modified: string | null; // shown attempt's "Date modified", ISO in display tz
}
export interface CalendarData {
  months: CalendarMonth[];
  days: CalendarDay[];
}

export interface DayAttempt {
  source_file: string;
  label: string; // "Attempt 1", "Attempt 2", …
  file_modified: string | null; // export's "Date modified", ISO in display tz
}

/** How often the day's entries pointed the right way, read off the tape at a
 *  fixed clock from each entry and *blind to the exit* — so it scores the entry
 *  itself, not the trade that was managed out of it.
 *
 *  Three denominators, and they are not interchangeable: `trades` is what was
 *  taken, `measured` is what the tape could speak to (a day whose ticks were
 *  never cached measures nothing), and each horizon's `n` is what still had
 *  session left that far out. A 15:57 entry counts in the first two and in
 *  none of the third at five minutes. */
export interface EntryDirection {
  trades: number;
  measured: number;
  horizons: {
    label: string; // "30s", "1m", "5m"
    seconds: number;
    n: number;
    right: number;
    flat: number; // price back at the entry to the tick: neither side
    hit_rate: number | null; // percent, null when nothing was measured
    median_pts: number | null;
  }[];
}

export interface DayDetail {
  kpis: Metrics;
  extras: SummaryExtras;
  entry_direction: EntryDirection;
  equity: EquityPoint[];
  per_trade_bars: { trade_no: number; net_pnl: number; time: string }[];
  trades: TradeRow[];
  instrument: string;
  attempts: DayAttempt[]; // replay takes for this day, oldest-uploaded first
  source_file: string; // the attempt currently shown
  file_modified: string | null; // "Date modified" of the shown attempt's export
}

export function useCalendar(scope: FilterScope) {
  return useQuery({
    queryKey: qk.calendar(scope),
    queryFn: () => apiGet<CalendarData>("/calendar", scopeParams(scope)),
  });
}

export function useDay(
  scope: FilterScope,
  date: string | null,
  sourceFile: string | null = null,
) {
  return useQuery({
    queryKey: qk.day(scope, date ?? "", sourceFile),
    queryFn: () =>
      apiGet<DayDetail>(`/day/${date}`, { ...scopeParams(scope), source_file: sourceFile }),
    enabled: !!date,
  });
}

// daily-pnl reused indirectly; re-export type for convenience.
export type { DailyPnlPoint };

export interface DeleteDayResult {
  journal: number;
  executions: number;
}

export function useDayNote(date: string | null) {
  return useQuery({
    queryKey: qk.dayNote(date ?? ""),
    queryFn: () => apiGet<Note>(`/day-notes/${date}`),
    enabled: !!date,
  });
}

// Every day's note+tags keyed by ISO date, in one request — for views that show
// per-day tags across many days at once (the Interactions Sessions table).
export type DayNoteMap = Record<string, Note>;

export function useAllDayNotes() {
  return useQuery({
    queryKey: qk.dayNotesAll,
    queryFn: () => apiGet<DayNoteMap>("/day-notes"),
    staleTime: 30_000,
  });
}

export function useSaveDayNote(date: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Note) => apiSend<{ ok: boolean }>("PUT", `/day-notes/${date}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dayNote(date) });
      qc.invalidateQueries({ queryKey: qk.dayNotesAll });
      qc.invalidateQueries({ queryKey: ["filters"] });
    },
  });
}

export function useDeleteDay() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { date: string; account?: string; instrument?: string }) => {
      const qs = toQuery({ account: vars.account, instrument: vars.instrument });
      const suffix = qs ? `?${qs}` : "";
      return apiSend<DeleteDayResult>("DELETE", `/day/${vars.date}${suffix}`);
    },
    onSuccess: () => qc.invalidateQueries(),
  });
}

export interface DeleteAttemptResult {
  executions: number;
  atas_journal: number;
  atas_statistics: number;
  imported_files: number;
}

// Drop a single replay take (one source file) without touching the day's
// other attempts.
export function useDeleteAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { sourceFile: string }) => {
      const qs = toQuery({ source_file: vars.sourceFile });
      return apiSend<DeleteAttemptResult>("DELETE", `/attempt?${qs}`);
    },
    onSuccess: () => qc.invalidateQueries(),
  });
}
