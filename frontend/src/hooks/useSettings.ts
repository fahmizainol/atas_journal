import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import { qk } from "../lib/queryKeys";

export function useTradingProfile() {
  return useQuery({
    queryKey: qk.settings("trading_profile"),
    queryFn: () => apiGet<{ profile: string }>("/settings/trading_profile"),
  });
}

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profile: string) =>
      apiSend<{ ok: boolean }>("PUT", "/settings/trading_profile", { profile }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings("trading_profile") }),
  });
}

// The single folder the auto-link scanner searches for recordings.
export function useRecordingsFolder() {
  return useQuery({
    queryKey: qk.settings("recordings_folder"),
    queryFn: () => apiGet<{ folder: string }>("/settings/recordings_folder"),
  });
}

export function useSaveRecordingsFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (folder: string) =>
      apiSend<{ ok: boolean }>("PUT", "/settings/recordings_folder", { folder }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings("recordings_folder") }),
  });
}
