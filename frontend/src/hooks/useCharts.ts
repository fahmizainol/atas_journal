import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { DayChartData, Excursion } from "../lib/chartTypes";

/** MAE/MFE and the grade, off the cached tape.
 *
 * Unconditional once a trade is open. This used to be deferred behind the chart
 * toggle, back when the numbers came from Databento and a cold cache made
 * expanding a row wait on a download; it reads local ticks now, and its numbers
 * belong in the header rather than inside a panel that may never be opened. */
export function useExcursion(scope: FilterScope, tradeNo: number | null) {
  return useQuery({
    queryKey: qk.excursion(tradeNo ?? -1),
    queryFn: () => apiGet<Excursion>(`/trades/${tradeNo}/excursion`, scopeParams(scope)),
    enabled: tradeNo != null,
  });
}

export function useDayChart(
  scope: FilterScope,
  date: string | null,
  tf: string,
  sourceFile: string | null = null,
) {
  return useQuery({
    queryKey: qk.dayChart(scope, date ?? "", tf, sourceFile),
    queryFn: () =>
      apiGet<DayChartData>(`/day-chart/${date}`, {
        ...scopeParams(scope),
        tf,
        source_file: sourceFile,
      }),
    enabled: !!date,
  });
}
