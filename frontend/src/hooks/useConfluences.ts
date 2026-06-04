import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { ConfluencesResponse } from "../lib/types";

export function useConfluenceStats(scope: FilterScope) {
  return useQuery({
    queryKey: qk.confluenceStats(scope),
    queryFn: () =>
      apiGet<ConfluencesResponse>("/confluences/stats", scopeParams(scope)),
  });
}
