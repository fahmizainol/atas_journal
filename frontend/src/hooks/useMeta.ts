import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { Filters, Meta } from "../lib/types";
import type { FilterScope } from "../lib/queryKeys";

export function useMeta() {
  return useQuery({
    queryKey: qk.meta,
    queryFn: () => apiGet<Meta>("/meta"),
    staleTime: 60_000,
  });
}

export function useFiltersData(scope: FilterScope) {
  return useQuery({
    queryKey: qk.filters(scope),
    queryFn: () =>
      apiGet<Filters>("/filters", { view: scope.view, tz: scope.tz }),
  });
}
