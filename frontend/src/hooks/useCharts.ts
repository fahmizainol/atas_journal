import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { DayChartData, Excursion, TradeChartData } from "../lib/chartTypes";

export function useTradeChart(scope: FilterScope, tradeNo: number | null, tf: string) {
  return useQuery({
    queryKey: qk.tradeChart(scope, tradeNo ?? -1, tf),
    queryFn: () =>
      apiGet<TradeChartData>(`/trade-chart/${tradeNo}`, { ...scopeParams(scope), tf }),
    enabled: tradeNo != null,
  });
}

export function useExcursion(scope: FilterScope, tradeNo: number | null) {
  return useQuery({
    queryKey: qk.excursion(tradeNo ?? -1),
    queryFn: () => apiGet<Excursion>(`/trades/${tradeNo}/excursion`, scopeParams(scope)),
    enabled: tradeNo != null,
  });
}

export function useDayChart(scope: FilterScope, date: string | null, tf: string) {
  return useQuery({
    queryKey: qk.dayChart(scope, date ?? "", tf),
    queryFn: () =>
      apiGet<DayChartData>(`/day-chart/${date}`, { ...scopeParams(scope), tf }),
    enabled: !!date,
  });
}
