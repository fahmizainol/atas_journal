import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { DEFAULT_DIV_TICKS } from "../lib/chartPrefs";
import type { ChartResolution, DayChartData, TradeChartData } from "../lib/chartTypes";
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
  resolution: ChartResolution = "tick",
  divTicks: number = DEFAULT_DIV_TICKS,
) {
  return useQuery({
    queryKey: ["strategies", "trade-chart", slug, runId, tradeNo, tz, resolution, divTicks],
    queryFn: () =>
      apiGet<TradeChartData>(`/strategies/${slug}/runs/${runId}/trade-chart/${tradeNo}`, {
        tz,
        resolution,
        div_ticks: divTicks,
      }),
    enabled: !!slug && !!runId && tradeNo != null,
  });
}

export function useRunDayChart(
  slug: string | null,
  runId: string | null,
  day: string | null,
  tz: string,
  resolution: ChartResolution = "tick",
  divTicks: number = DEFAULT_DIV_TICKS,
) {
  return useQuery({
    queryKey: ["strategies", "day-chart", slug, runId, day, tz, resolution, divTicks],
    queryFn: () =>
      apiGet<DayChartData>(`/strategies/${slug}/runs/${runId}/day-chart/${day}`, {
        tz,
        resolution,
        div_ticks: divTicks,
      }),
    enabled: !!slug && !!runId && !!day,
  });
}

function useInvalidateStrategies() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["strategies"] });
}

// A run buys each session whole, so there is nothing to choose here any more —
// the old `rthOnly` flag rode beside the config (it decided what got downloaded,
// not what got simulated, so it never entered the run's identity hash).
export function usePreflight(slug: string) {
  return useMutation({
    mutationFn: (v: { config: SimConfig }) =>
      apiSend<Preflight>("POST", `/strategies/${slug}/preflight`, {
        config: v.config,
      }),
  });
}

export function useCreateRun(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (v: { config: SimConfig; label?: string }) =>
      apiSend<{ run_id: string; status: string; already_existed: boolean }>(
        "POST",
        `/strategies/${slug}/runs`,
        { config: v.config, label: v.label },
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

// Set one trade's tags. Invalidates the whole "strategies" tree, which refetches
// the open run detail so the new tags flow back into the table badges, the tag
// editor, and the filter chips from the single source of truth.
export function usePatchTradeTags(slug: string) {
  const invalidate = useInvalidateStrategies();
  return useMutation({
    mutationFn: (v: { runId: string; tradeNo: number; tags: string[] }) =>
      apiSend("PATCH", `/strategies/${slug}/runs/${v.runId}/trades/${v.tradeNo}`, {
        tags: v.tags,
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
