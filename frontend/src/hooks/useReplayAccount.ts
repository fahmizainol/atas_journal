// The replay account query. The shape it fetches, and the arithmetic over it,
// live in `lib/replayAccount` — `guardRules` reads them too, and a rule module
// has no business importing a hook.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { AccountView } from "../lib/replayAccount";

export type { AccountKey, AccountMode, AccountView, ReplayFlag } from "../lib/replayAccount";
export { modeOfAccount } from "../lib/replayAccount";

/** The account, on the same key family the attempt list already uses.
 *
 *  `["replays", "account"]` rather than a key of its own so that every existing
 *  `invalidateQueries({ queryKey: ["replays"] })` — the one the recorder fires
 *  when a sitting finishes, the ones the history page fires on delete and patch
 *  — refreshes this too. A sitting that just settled is exactly when the
 *  account changed.
 *
 *  The account id is *in* the key. Two accounts answering on one key would mean
 *  one account's floor briefly rendered against another's equity on a switch,
 *  which is the one number on this page nobody should ever see a wrong version
 *  of. `null` (a drill) fetches nothing: an unpriced rep has no account to show.
 *
 *  **`date` is the tape day on the chart, and it is in the key for the same
 *  reason the mode is.** The account counts a day of the *market*, not of the
 *  evening (`replay_account.tape_day`): the day being replayed has not closed,
 *  so its peak is not banked and the floor under it does not move, and the daily
 *  loss limit is that day's. Switching tapes is therefore a different account
 *  view, and one cached under a shared key would draw the previous day's spent
 *  allowance against the new day's trading. Undefined until a day is picked, and
 *  the server then reports no day open — a full allowance and every day closed,
 *  which is the right reading when nothing is being traded. */
export function useReplayAccount(
  accountId: string | null,
  date?: string | null,
) {
  return useQuery({
    queryKey: ["replays", "account", accountId, date ?? null],
    queryFn: () =>
      apiGet<AccountView>(
        `/replays/account?account=${encodeURIComponent(accountId!)}` +
          (date ? `&date=${encodeURIComponent(date)}` : ""),
      ),
    enabled: accountId !== null,
  });
}

/** Record what killed the account. The one write in this feature whose content
 *  comes from a person rather than from the trades — which is why the blown
 *  state waits on it and why the server refuses it when nothing has died.
 *
 *  Only ever reached on the funded account: paper is resettable the second it
 *  dies, so nothing there is waiting on a write-up and the page never offers
 *  the form. It still takes the mode, so the answer lands on the key it was
 *  asked from. */
export function useWriteCause(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cause_of_death: string) =>
      apiSend<AccountView>(
        "POST",
        `/replays/account/cause?account=${encodeURIComponent(accountId)}`,
        { cause_of_death },
      ),
    // Invalidate rather than seed the cache: the view is keyed by account *and*
    // tape day now, and writing the answer under one day's key would leave every
    // other day's still saying the account was blown.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }),
  });
}
