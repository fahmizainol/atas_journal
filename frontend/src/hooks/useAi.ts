import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";

export interface TradeAnalysis {
  verdict?: string;
  went_well?: string[];
  went_wrong?: string[];
  suggestion?: string;
  grade?: string;
  error?: string;
}
export interface PeriodReview {
  summary?: string;
  strengths?: string[];
  leaks?: string[];
  recommendations?: string[];
  error?: string;
}
export interface TradeAiResp {
  analyses: Record<string, { analysis: TradeAnalysis; created_at: string }>;
  error?: string;
}
export interface PeriodAiResp {
  reviews: Record<
    string,
    { review: PeriodReview; created_at: string; trade_count: number; stale: boolean }
  >;
  error?: string;
}

export function useTradeAi(tradeKey: string) {
  return useQuery({
    queryKey: qk.aiTrade(tradeKey),
    queryFn: () => apiGet<TradeAiResp>(`/ai/trade/${tradeKey}`),
  });
}

export function useGenTradeAi(tradeKey: string, scope: FilterScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string) =>
      apiSend<TradeAiResp>(
        "POST",
        `/ai/trade/${tradeKey}?${toQuery({ ...scopeParams(scope), model })}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.aiTrade(tradeKey) }),
  });
}

export function usePeriodAi(scope: FilterScope) {
  return useQuery({
    queryKey: qk.aiPeriod(scope),
    queryFn: () => apiGet<PeriodAiResp>("/ai/period", scopeParams(scope)),
  });
}

export function useGenPeriodAi(scope: FilterScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string) =>
      apiSend<PeriodAiResp>("POST", `/ai/period?${toQuery({ ...scopeParams(scope), model })}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.aiPeriod(scope) }),
  });
}
