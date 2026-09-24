import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { TradeRow } from "../lib/types";

/** The review cut — which slice of the reviewed trades the Trades table and
 * the Review page are showing.
 *
 * URL state like the FilterBar scope (refresh-safe, shareable, and it survives
 * the workspace hop because every nav keeps the querystring) but applied
 * CLIENT-side, not sent to the server: `/trades` already returns every in-scope
 * row with its review attached, and the Review page needs the un-narrowed rows
 * anyway to put honest counts on its facets — a facet's count is computed with
 * every axis applied *except its own*, which no single server response could
 * carry. Param names are `r`-prefixed so they can never collide with a scope
 * param (`setups` is already the archived-era taxonomy filter's name).
 */
export interface ReviewCut {
  setups: string[];
  disciplines: string[];
  grades: string[];
  /** Watched-level display labels ("GX VAH", "no level") — labels rather than
   * member ids because that is what the rows carry, and none contain a comma. */
  levels: string[];
  /** The debt taxonomy, mirroring `TradeRow.review_state`. */
  state: "" | "reviewed" | "owed" | "history";
}

const CSV = (v: string | null): string[] => (v ? v.split(",").filter(Boolean) : []);

export const EMPTY_CUT: ReviewCut = {
  setups: [], disciplines: [], grades: [], levels: [], state: "",
};

export function cutIsEmpty(cut: ReviewCut): boolean {
  return (
    !cut.state && !cut.setups.length && !cut.disciplines.length &&
    !cut.grades.length && !cut.levels.length
  );
}

/** Does one row survive the cut? `omit` drops one axis from the test — how a
 * facet counts what clicking it would show rather than what is already shown. */
export function matchesCut(
  row: TradeRow, cut: ReviewCut, omit?: keyof ReviewCut,
): boolean {
  if (omit !== "state" && cut.state && row.review_state !== cut.state) return false;
  if (omit !== "setups" && cut.setups.length &&
      !cut.setups.includes(row.setup ?? "")) return false;
  if (omit !== "disciplines" && cut.disciplines.length &&
      !cut.disciplines.includes(row.discipline ?? "")) return false;
  if (omit !== "grades" && cut.grades.length &&
      !cut.grades.includes(row.grade ?? "")) return false;
  if (omit !== "levels" && cut.levels.length) {
    const labels = row.watched_labels ?? [];
    if (!cut.levels.some((l) => labels.includes(l))) return false;
  }
  return true;
}

export function useReviewCut() {
  const [params, setParams] = useSearchParams();

  const cut: ReviewCut = useMemo(() => {
    const state = params.get("rstate");
    return {
      setups: CSV(params.get("rsetup")),
      disciplines: CSV(params.get("rdiscipline")),
      grades: CSV(params.get("rgrade")),
      levels: CSV(params.get("rlevel")),
      state:
        state === "reviewed" || state === "owed" || state === "history"
          ? state
          : "",
    };
  }, [params]);

  const patch = useCallback(
    (next: Partial<ReviewCut>) => {
      setParams(
        (prev) => {
          const sp = new URLSearchParams(prev);
          const put = (k: string, v: string[] | string | undefined) => {
            if (v === undefined) return;
            const val = Array.isArray(v) ? v.join(",") : v;
            if (!val) sp.delete(k);
            else sp.set(k, val);
          };
          put("rsetup", next.setups);
          put("rdiscipline", next.disciplines);
          put("rgrade", next.grades);
          put("rlevel", next.levels);
          put("rstate", next.state);
          return sp;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const toggle = useCallback(
    (axis: "setups" | "disciplines" | "grades" | "levels", id: string) => {
      const have = cut[axis];
      patch({
        [axis]: have.includes(id) ? have.filter((x) => x !== id) : [...have, id],
      });
    },
    [cut, patch],
  );

  return {
    cut,
    patch,
    toggle,
    setState: (state: ReviewCut["state"]) => patch({ state }),
    clear: () => patch(EMPTY_CUT),
  };
}
