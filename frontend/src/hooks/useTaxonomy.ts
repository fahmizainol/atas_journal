import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { TaxonomyItem } from "../lib/types";

// Setup/confluence master-list management. One set of hooks parameterised by
// `kind`, mirroring the shared backend CRUD. `kind` is constant per page, so
// calling these at the top level never violates the rules of hooks.
export type TaxonomyKind = "setups" | "confluences";

const listKey = (kind: TaxonomyKind) =>
  kind === "setups" ? qk.setupList : qk.confluenceList;

export function useTaxonomyList(kind: TaxonomyKind) {
  return useQuery({
    queryKey: listKey(kind),
    queryFn: () =>
      apiGet<Record<string, TaxonomyItem[]>>(`/${kind}/list`).then((d) => d[kind] ?? []),
    staleTime: 30_000,
  });
}

// A rename/delete cascades into trade tag arrays, so refresh everything that
// reads those: this list, both stats pivots (each nests the other dimension),
// the trade tables/detail/notes, day view, and the autocomplete filters.
function useInvalidateTaxonomy(kind: TaxonomyKind) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: listKey(kind) });
    qc.invalidateQueries({ queryKey: ["setup-stats"] });
    qc.invalidateQueries({ queryKey: ["confluence-stats"] });
    qc.invalidateQueries({ queryKey: ["filters"] });
    qc.invalidateQueries({ queryKey: ["trades"] });
    qc.invalidateQueries({ queryKey: ["trade"] });
    qc.invalidateQueries({ queryKey: ["note"] });
    qc.invalidateQueries({ queryKey: ["day"] });
  };
}

export function useCreateTaxonomy(kind: TaxonomyKind) {
  const invalidate = useInvalidateTaxonomy(kind);
  return useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      apiSend<{ ok: boolean }>("POST", `/${kind}/create`, body),
    onSuccess: invalidate,
  });
}

export function useUpdateTaxonomy(kind: TaxonomyKind) {
  const invalidate = useInvalidateTaxonomy(kind);
  return useMutation({
    mutationFn: (body: { name: string; new_name?: string; description?: string }) =>
      apiSend<{ ok: boolean }>("POST", `/${kind}/update`, body),
    onSuccess: invalidate,
  });
}

export function useDeleteTaxonomy(kind: TaxonomyKind) {
  const invalidate = useInvalidateTaxonomy(kind);
  return useMutation({
    mutationFn: (name: string) =>
      apiSend<{ ok: boolean }>("POST", `/${kind}/delete`, { name }),
    onSuccess: invalidate,
  });
}
