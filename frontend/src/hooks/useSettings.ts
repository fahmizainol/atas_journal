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
