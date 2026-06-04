import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend, toQuery } from "../lib/api";
import { qk, scopeParams, type FilterScope } from "../lib/queryKeys";
import type { ScanResult, SyncResult, VideoBookmark, VideoData } from "../lib/types";

// All video state is keyed by the attempt's source_file (the stable id; the
// "Attempt N" label is positional and shifts when takes are deleted).

export function useVideo(sourceFile: string | null) {
  return useQuery({
    queryKey: qk.video(sourceFile ?? ""),
    queryFn: () => apiGet<VideoData>(`/videos?${toQuery({ source_file: sourceFile })}`),
    enabled: !!sourceFile,
  });
}

export function useSaveVideo(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { path: string; duration_s?: number | null }) =>
      apiSend<{ ok: boolean; playable: boolean }>(
        "PUT",
        `/videos?${toQuery({ source_file: sourceFile })}`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

export function useDeleteVideo(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ ok: boolean }>("DELETE", `/videos?${toQuery({ source_file: sourceFile })}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

export function useAddBookmark(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { offset_s: number; label?: string; trade_key?: string | null }) =>
      apiSend<VideoBookmark>(
        "POST",
        `/videos/bookmarks?${toQuery({ source_file: sourceFile })}`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

export function useUpdateBookmark(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: number; offset_s?: number; label?: string }) =>
      apiSend<VideoBookmark>("PUT", `/videos/bookmarks/${vars.id}`, {
        offset_s: vars.offset_s,
        label: vars.label,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

export function useDeleteBookmark(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiSend<{ ok: boolean }>("DELETE", `/videos/bookmarks/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

// Auto-place a bookmark for every trade from the manual anchor. Scope params go
// along so the backend builds the same trade set (view/tz/filters) the day view
// shows; duration_s comes from the player (the DB value is usually NULL).
export function useSyncTrades(sourceFile: string, scope: FilterScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { duration_s: number }) =>
      apiSend<SyncResult>(
        "POST",
        `/videos/sync?${toQuery({ ...scopeParams(scope), source_file: sourceFile })}`,
        { duration_s: vars.duration_s },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}

// Batch auto-link: scan the configured folder and link every attempt whose
// expected recording is found. Scope params go along so the backend builds the
// same attempt set the calendar shows. On success, refresh the calendar (video
// badges) and any open day's video panel.
export function useScanRecordings(scope: FilterScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<ScanResult>("POST", `/videos/scan?${toQuery(scopeParams(scope))}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calendar"] });
      qc.invalidateQueries({ queryKey: ["video"] });
    },
  });
}

export function useClearSynced(sourceFile: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ ok: boolean; deleted: number }>(
        "DELETE",
        `/videos/synced?${toQuery({ source_file: sourceFile })}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.video(sourceFile) }),
  });
}
