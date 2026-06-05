import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { SetupStat } from "../lib/types";

export function useSetupStats(scope: FilterScope) {
  return useQuery({
    queryKey: qk.setupStats(scope),
    queryFn: () =>
      apiGet<{ setups: SetupStat[] }>("/setups/stats", scopeParams(scope)),
  });
}
