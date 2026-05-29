import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { DailyPnlPoint, EquityPoint, Metrics, TradeRow } from "../lib/types";

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
