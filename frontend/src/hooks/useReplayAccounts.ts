// The account registry: which accounts exist, and the rule shapes one can be
// made from. Separate from `useReplayAccount`, which is one account's *state* —
// this is the list you pick from and the form you edit.
//
// The split matters for caching. A view goes stale every time a sitting settles
// and is keyed by account and tape day; the registry changes only when you make
// or edit an account, and is one query for the whole page.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type { Trailing } from "../lib/replayAccount";

/** The editable figures. Every one of them is a number an account of a given
 *  shape may differ on — a 25K and a 50K of one product are the same rules at
 *  different sizes. None of them changes what the walk does. */
export interface AccountNumbers {
  start: number;
  max_loss: number;
  trail_cap: number;
  day_loss: number;
  day_goal: number | null;
  profit_target: number;
  max_minis: number;
  max_micros: number;
}

export interface AccountRow {
  id: string;
  label: string;
  template: string;
  trailing: Trailing;
  needs_cause: boolean;
  archived: boolean;
  numbers: AccountNumbers;
  /** How many lives this account has had. Zero means it has never traded, which
   *  is the only state it can be deleted from. */
  epochs: number;
  /** How those lives ended — the record, walked server-side. It is the one thing
   *  in this row the client could not work out for itself: an outcome is a
   *  property of the sittings, and the switcher holds none of them. */
  record: { passed: number; blown: number };
}

/** A rule *shape*. Ships in code and cannot be authored here — a wrong shape is
 *  not a wrong number, it is an account that teaches the wrong game. */
export interface AccountTemplate {
  key: string;
  label: string;
  trailing: Trailing;
  needs_cause: boolean;
  note: string;
  defaults: AccountNumbers;
}

export function useReplayAccounts() {
  return useQuery({
    queryKey: ["replays", "accounts"],
    queryFn: () =>
      apiGet<{ accounts: AccountRow[]; templates: AccountTemplate[] }>("/replays/accounts"),
  });
}

/** Every write to the registry, sharing one invalidation.
 *
 *  `["replays"]` rather than `["replays","accounts"]`: editing a daily limit
 *  changes what the *view* says is left of the day, so the account state has to
 *  refetch too — and that key family is the one every other write here already
 *  fires (see `useReplayAccount`).
 */
export function useAccountEdits() {
  const qc = useQueryClient();
  const done = { onSuccess: () => qc.invalidateQueries({ queryKey: ["replays"] }) };

  return {
    create: useMutation({
      mutationFn: (body: { label: string; template: string; numbers?: Partial<AccountNumbers> }) =>
        apiSend<AccountRow>("POST", "/replays/accounts", body),
      ...done,
    }),
    patch: useMutation({
      mutationFn: ({ id, ...body }: {
        id: string;
        label: string;
        numbers?: Partial<AccountNumbers>;
        archived?: boolean;
      }) => apiSend<AccountRow>("PATCH", `/replays/accounts/${id}`, body),
      ...done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => apiSend<{ deleted: string }>("DELETE", `/replays/accounts/${id}`, {}),
      ...done,
    }),
  };
}
