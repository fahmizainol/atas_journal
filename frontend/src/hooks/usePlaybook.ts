import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { PlaybookStat } from "../lib/types";

export function usePlaybookStats(scope: FilterScope) {
  return useQuery({
    queryKey: qk.playbookStats(scope),
    queryFn: () =>
      apiGet<{ playbooks: PlaybookStat[] }>("/playbooks/stats", scopeParams(scope)),
  });
}
