import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { Edges } from "../lib/types";

export function useEdges(scope: FilterScope) {
  return useQuery({
    queryKey: qk.edges(scope),
    queryFn: () => apiGet<Edges>("/edges", scopeParams(scope)),
  });
}
