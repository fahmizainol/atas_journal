import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { DailyPnlPoint, EquityPoint, Metrics } from "../lib/types";

export function useMetrics(scope: FilterScope) {
  return useQuery({
    queryKey: qk.metrics(scope),
    queryFn: () => apiGet<Metrics>("/metrics", scopeParams(scope)),
  });
}

export function useEquityCurve(scope: FilterScope) {
  return useQuery({
    queryKey: qk.equityCurve(scope),
    queryFn: () => apiGet<EquityPoint[]>("/equity-curve", scopeParams(scope)),
  });
}

export function useDailyPnl(scope: FilterScope) {
  return useQuery({
    queryKey: qk.dailyPnl(scope),
    queryFn: () => apiGet<DailyPnlPoint[]>("/daily-pnl", scopeParams(scope)),
  });
}

export function useDistribution(scope: FilterScope) {
  return useQuery({
    queryKey: qk.distribution(scope),
    queryFn: () => apiGet<{ values: number[] }>("/distribution", scopeParams(scope)),
  });
}
