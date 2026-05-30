import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { FilterScope } from "../lib/queryKeys";

const CSV = (v: string | null): string[] => (v ? v.split(",").filter(Boolean) : []);

// URL <-> filter state. The full scope lives in the query string so views are
// refresh-safe and shareable (?view=&instruments=&start=&end=&tags=&tz=).
export function useFilters() {
  const [params, setParams] = useSearchParams();

  const scope: FilterScope = useMemo(
    () => ({
      view: params.get("view") || "logical",
      instruments: CSV(params.get("instruments")),
      accounts: CSV(params.get("accounts")),
      start: params.get("start"),
      end: params.get("end"),
      tags: CSV(params.get("tags")),
      tz: params.get("tz") || "",
    }),
    [params],
  );

  const patch = useCallback(
    (next: Partial<Record<string, string | string[] | null>>) => {
      setParams(
        (prev) => {
          const sp = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(next)) {
            const val = Array.isArray(v) ? v.join(",") : v;
            if (!val) sp.delete(k);
            else sp.set(k, val);
          }
          return sp;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return {
    scope,
    setView: (view: string) => patch({ view }),
    setInstruments: (instruments: string[]) => patch({ instruments }),
    setAccounts: (accounts: string[]) => patch({ accounts }),
    setDates: (start: string | null, end: string | null) => patch({ start, end }),
    setTags: (tags: string[]) => patch({ tags }),
    setTz: (tz: string) => patch({ tz }),
  };
}
