// The replay account query. The shape it fetches, and the arithmetic over it,
// live in `lib/replayAccount` — `guardRules` reads them too, and a rule module
// has no business importing a hook.

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { AccountView } from "../lib/replayAccount";

export type { AccountView, ReplayFlag } from "../lib/replayAccount";
export { fmtWait, remainingMs } from "../lib/replayAccount";

/** The account, on the same key family the attempt list already uses.
 *
 *  `["replays", "account"]` rather than a key of its own so that every existing
 *  `invalidateQueries({ queryKey: ["replays"] })` — the one the recorder fires
 *  when a sitting finishes, the ones the history page fires on delete and patch
 *  — refreshes this too. A sitting that just settled is exactly when the
 *  account changed. */
export function useReplayAccount() {
  return useQuery({
    queryKey: ["replays", "account"],
    queryFn: () => apiGet<AccountView>("/replays/account"),
  });
}
