import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiSend, toQuery } from "../lib/api";

// After an import, the whole dataset changes — invalidate everything.
function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries();
}

export function useImportDir() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars?: { sourceTz?: string }) => {
      const qs = toQuery({ source_tz: vars?.sourceTz });
      const suffix = qs ? `?${qs}` : "";
      return apiSend<{ files: number; total_fills: number; source_tz: string }>(
        "POST",
        `/import/dir${suffix}`,
      );
    },
    onSuccess: () => invalidateAll(qc),
  });
}

export function useUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { files: FileList; sourceTz?: string }) => {
      const fd = new FormData();
      Array.from(vars.files).forEach((f) => fd.append("files", f));
      if (vars.sourceTz) fd.append("source_tz", vars.sourceTz);
      return apiSend<{ results: Record<string, unknown>; source_tz: string }>(
        "POST",
        "/import/upload",
        fd,
      );
    },
    onSuccess: () => invalidateAll(qc),
  });
}

export interface DeleteAllResult {
  executions: number;
  atas_journal: number;
  atas_statistics: number;
  imported_files: number;
}

export function useDeleteAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<DeleteAllResult>("DELETE", "/data?confirm=DELETE"),
    onSuccess: () => invalidateAll(qc),
  });
}
