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
export interface CalendarDay {
  date: string;
  net_pnl: number;
  trades: number;
  win_rate: number;
  attempts: number; // distinct replay takes on this day; >1 means re-done
  has_video: boolean; // any attempt on this day has a recording linked
  file_modified: string | null; // latest attempt's "Date modified", ISO in display tz
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

export interface DayDetail {
  kpis: Metrics;
  extras: SummaryExtras;
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

export function useSaveDayNote(date: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Note) => apiSend<{ ok: boolean }>("PUT", `/day-notes/${date}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dayNote(date) });
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
