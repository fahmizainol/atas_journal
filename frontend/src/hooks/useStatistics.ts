import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { StatisticsDetail } from "../lib/types";

export function useStatisticsFiles() {
  return useQuery({
    queryKey: qk.statisticsFiles,
    queryFn: () => apiGet<{ files: string[] }>("/statistics/files"),
  });
}

export function useStatistics(file: string | null) {
  return useQuery({
    queryKey: qk.statistics(file ?? ""),
    queryFn: () => apiGet<StatisticsDetail>(`/statistics/${encodeURIComponent(file!)}`),
    enabled: !!file,
  });
}
