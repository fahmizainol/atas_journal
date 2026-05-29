import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiSend } from "../lib/api";

// After an import, the whole dataset changes — invalidate everything.
function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries();
}

export function useImportDir() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ files: number; total_fills: number }>("POST", "/import/dir"),
    onSuccess: () => invalidateAll(qc),
  });
}

export function useUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: FileList) => {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f));
      return apiSend<{ results: Record<string, unknown> }>("POST", "/import/upload", fd);
    },
    onSuccess: () => invalidateAll(qc),
  });
}
