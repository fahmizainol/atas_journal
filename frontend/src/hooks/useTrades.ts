import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { Note, TradeRow } from "../lib/types";

export interface TradeDetail {
  trade: TradeRow;
  note: string;
  tags: string[];
  setups: string[];
  confluences: string[];
  // The trade's own model binding — not the effective one, which may come from a
  // backtest session and shows through `trade.model_id`.
  model_id: number | null;
  rules_met: number[];
}

export function useTrades(scope: FilterScope) {
  return useQuery({
    queryKey: qk.trades(scope),
    queryFn: () => apiGet<TradeRow[]>("/trades", scopeParams(scope)),
  });
}

export function useTradeDetail(scope: FilterScope, tradeNo: number | null) {
  return useQuery({
    queryKey: qk.trade(scope, tradeNo ?? -1),
    queryFn: () => apiGet<TradeDetail>(`/trades/${tradeNo}`, scopeParams(scope)),
    enabled: tradeNo != null,
  });
}

export function useSaveNote(tradeKey: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Note) => apiSend<{ ok: boolean }>("PUT", `/notes/${tradeKey}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["note"] });
      qc.invalidateQueries({ queryKey: ["trade"] });
      qc.invalidateQueries({ queryKey: ["trades"] });
      qc.invalidateQueries({ queryKey: ["day"] });
      qc.invalidateQueries({ queryKey: ["filters"] });
      // A model or rule-check change moves the trade between per-model buckets.
      qc.invalidateQueries({ queryKey: ["model-stats"] });
    },
  });
}
