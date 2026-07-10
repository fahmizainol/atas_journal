import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { BacktestDetail, BacktestModelCard, ImportFeed } from "../lib/types";

export function useBacktestsOverview(tz: string) {
  return useQuery({
    queryKey: qk.backtestsOverview(tz),
    queryFn: () =>
      apiGet<{ models: BacktestModelCard[] }>("/backtests/overview", { tz }).then(
        (d) => d.models,
      ),
  });
}

export function useBacktestDetail(modelId: number | null, tz: string) {
  return useQuery({
    queryKey: qk.backtestDetail(modelId ?? -1, tz),
    queryFn: () => apiGet<BacktestDetail>(`/backtests/${modelId}`, { tz }),
    enabled: modelId != null,
  });
}

// Poll the watcher feed. Mounted once in Layout: when the seq advances past an
// "imported" event, the dataset changed under us, so invalidate everything —
// the same blanket invalidation a manual import does.
export function useImportFeed(pollMs = 15_000) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.importFeed,
    queryFn: () => apiGet<ImportFeed>("/import/feed"),
    refetchInterval: pollMs,
  });

  const lastSeq = useRef<number | null>(null);
  const seq = query.data?.seq;
  useEffect(() => {
    if (seq == null) return;
    if (lastSeq.current == null) {
      lastSeq.current = seq; // first load: history, not news
      return;
    }
    if (seq > lastSeq.current) {
      const fresh = (query.data?.events ?? []).filter((e) => e.seq > lastSeq.current!);
      lastSeq.current = seq;
      if (fresh.some((e) => e.kind === "imported")) {
        qc.invalidateQueries();
      }
    }
  }, [seq, query.data, qc]);

  return query;
}
