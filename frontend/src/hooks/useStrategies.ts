import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { DayChartData, TradeChartData } from "../lib/chartTypes";
import type {
  Preflight,
  RunDetail,
  SimConfig,
  StrategyDetail,
  StrategySummary,
} from "../lib/strategyTypes";

export function useStrategyList() {
  return useQuery({
    queryKey: ["strategies", "list"],
    queryFn: () => apiGet<{ strategies: StrategySummary[] }>("/strategies"),
  });
}

// Polls while any run is in flight so progress bars advance and the run list
// flips to "done" without a manual refresh.
export function useStrategyDetail(slug: string | null) {
  return useQuery({
    queryKey: ["strategies", "detail", slug],
    queryFn: () => apiGet<StrategyDetail>(`/strategies/${slug}`),
    enabled: !!slug,
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.state.status === "running") ? 1500 : false,
  });
}

export function useRunDetail(slug: string | null, runId: string | null) {
  return useQuery({
    queryKey: ["strategies", "run", slug, runId],
    queryFn: () => apiGet<RunDetail>(`/strategies/${slug}/runs/${runId}`),
    enabled: !!slug && !!runId,
  });
}

export function useRunTradeChart(
  slug: string | null,
  runId: string | null,
  tradeNo: number | null,
  tz: string,
) {
  return useQuery({
    queryKey: ["strategies", "trade-chart", slug, runId, tradeNo, tz],
    queryFn: () =>
      apiGet<TradeChartData>(`/strategies/${slug}/runs/${runId}/trade-chart/${tradeNo}`, { tz }),
    enabled: !!slug && !!runId && tradeNo != null,
  });
}

export function useRunDayChart(
  slug: string | null,
  runId: string | null,
  day: string | null,
  tz: string,
) {
  return useQuery({
    queryKey: ["strategies", "day-chart", slug, runId, day, tz],
    queryFn: () =>
      apiGet<DayChartData>(`/strategies/${slug}/runs/${runId}/day-chart/${day}`, { tz }),
    enabled: !!slug && !!runId && !!day,
  });
}

function useInvalidateStrategies() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["strategies"] });
}

// `rthOnly` skips the overnight segment a run otherwise buys for its charts. It
// rides beside the config rather than inside it: it decides what gets downloaded,
// not what gets simulated, so it must not change the run's identity hash.
export function usePreflight(slug: string) {
  return useMutation({
    mutationFn: (v: { config: SimConfig; rthOnly: boolean }) =>
      apiSend<Preflight>("POST", `/strategies/${slug}/preflight`, {
        config: v.config,
        rth_only: v.rthOnly,
      }),
  });
}

export function useCreateRun(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (v: { config: SimConfig; label?: string; rthOnly?: boolean }) =>
      apiSend<{ run_id: string; status: string; already_existed: boolean }>(
        "POST",
        `/strategies/${slug}/runs`,
        { config: v.config, label: v.label, rth_only: v.rthOnly ?? false },
      ),
    onSuccess: invalidate,
  });
}

export function usePatchRunMeta(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (v: { runId: string; label?: string; notes?: string }) =>
      apiSend("PATCH", `/strategies/${slug}/runs/${v.runId}`, {
        label: v.label,
        notes: v.notes,
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteRun(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (runId: string) => apiSend("DELETE", `/strategies/${slug}/runs/${runId}`),
    onSuccess: invalidate,
  });
}

export function usePinBaseline(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (runId: string) =>
      apiSend("PUT", `/strategies/${slug}/baseline`, { run_id: runId }),
    onSuccess: invalidate,
  });
}

export function useRerunBaseline(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: () =>
      apiSend<{ run_id: string; status: string }>("POST", `/strategies/${slug}/rerun-baseline`),
    onSuccess: invalidate,
  });
}
