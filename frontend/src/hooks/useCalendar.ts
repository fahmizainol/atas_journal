import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type {
  DailyPnlPoint,
  EquityPoint,
  Metrics,
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
}
export interface CalendarData {
  months: CalendarMonth[];
  days: CalendarDay[];
}

export interface DayDetail {
  kpis: Metrics;
  extras: SummaryExtras;
  equity: EquityPoint[];
  per_trade_bars: { trade_no: number; net_pnl: number; time: string }[];
  trades: TradeRow[];
  instrument: string;
}

export function useCalendar(scope: FilterScope) {
  return useQuery({
    queryKey: qk.calendar(scope),
    queryFn: () => apiGet<CalendarData>("/calendar", scopeParams(scope)),
  });
}

export function useDay(scope: FilterScope, date: string | null) {
  return useQuery({
    queryKey: qk.day(scope, date ?? ""),
    queryFn: () => apiGet<DayDetail>(`/day/${date}`, scopeParams(scope)),
    enabled: !!date,
  });
}

// daily-pnl reused indirectly; re-export type for convenience.
export type { DailyPnlPoint };

export interface DeleteDayResult {
  journal: number;
  executions: number;
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
