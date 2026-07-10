import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk, scopeParams } from "../lib/queryKeys";
import type { FilterScope } from "../lib/queryKeys";
import type { Model, ModelStatsResponse } from "../lib/types";

export function useModels() {
  return useQuery({
    queryKey: qk.modelList,
    queryFn: () => apiGet<{ models: Model[] }>("/models/list").then((d) => d.models),
    staleTime: 30_000,
  });
}

export function useModelStats(scope: FilterScope) {
  return useQuery({
    queryKey: qk.modelStats(scope),
    queryFn: () => apiGet<ModelStatsResponse>("/models/stats", scopeParams(scope)),
  });
}

// A model or rule edit reaches the picker, the checklist rendered on every trade
// form, the per-model stats, and the model multiselect in the filter bar.
function useInvalidateModels() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: qk.modelList });
    qc.invalidateQueries({ queryKey: ["model-stats"] });
    qc.invalidateQueries({ queryKey: ["filters"] });
    qc.invalidateQueries({ queryKey: ["trades"] });
    qc.invalidateQueries({ queryKey: ["trade"] });
    qc.invalidateQueries({ queryKey: ["day"] });
  };
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      apiSend<{ ok: boolean; id: number }>("POST", "/models/create", body),
    onSuccess: invalidate,
  });
}

export function useUpdateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (body: { id: number; name?: string; description?: string; archived?: boolean }) =>
      apiSend<{ ok: boolean }>("POST", "/models/update", body),
    onSuccess: invalidate,
  });
}

// Soft-archive, not a delete: trades already assigned keep resolving to it, so
// historical per-model stats don't reshuffle when a model leaves the picker.
export function useArchiveModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (id: number) => apiSend<{ ok: boolean }>("POST", "/models/delete", { id }),
    onSuccess: invalidate,
  });
}

export function useCreateRule(modelId: number) {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (label: string) =>
      apiSend<{ ok: boolean; id: number }>("POST", `/models/${modelId}/rules`, { label }),
    onSuccess: invalidate,
  });
}

export function useUpdateRule() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (body: { id: number; label?: string; sort_order?: number; active?: boolean }) =>
      apiSend<{ ok: boolean }>("POST", "/models/rules/update", body),
    onSuccess: invalidate,
  });
}

// Also a soft delete: a trade scored 3/4 against the old checklist keeps reading 3/4.
export function useRetireRule() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (id: number) => apiSend<{ ok: boolean }>("POST", "/models/rules/delete", { id }),
    onSuccess: invalidate,
  });
}
