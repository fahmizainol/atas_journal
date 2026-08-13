// Talking to the order plant.
//
// Every mutation here is a real action at a broker, so none of them are wrapped
// in react-query mutations with retry: a retried submit is a second order. They
// are plain calls that throw, and the panel shows what they threw.
//
// The status poll is a query and is deliberately *slower* than the tape's:
// working orders and the position arrive by push on the server side, so this is
// only how often the page collects them, and a request per second for the whole
// time a chart is open would buy nothing.

import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "../lib/api";
import type {
  BrokerState,
  GuardLevels,
  OrderDraft,
  OrderPreview,
  OrderSent,
  RoutingStatus,
} from "../lib/routingTypes";

/** Fast enough that a fill shows up while you are still looking at the chart,
 *  slow enough that it is not a request per frame. Working orders and the
 *  position both arrive by push on the server side, so this is only how often
 *  the page collects them. */
const ROUTING_POLL_MS = 2000;

export function useRoutingStatus(enabled = true) {
  return useQuery({
    queryKey: ["live", "routing"],
    queryFn: () => apiGet<RoutingStatus>("/live/routing"),
    refetchInterval: ROUTING_POLL_MS,
    enabled,
  });
}

/** The guardrail levels alone, plus whether the replay is to enforce them.
 *
 *  For pages that want the rules but not the broker — the replay applies the
 *  same bracket and daily stop, and has no order plant to watch. Polling every
 *  two seconds for a set of numbers that changes when somebody edits a form
 *  would be a request per second per open tab for nothing, so this one goes
 *  stale slowly and never refetches on its own.
 *
 *  `enforced` is `REPLAY_GUARDRAILS`, not `LIVE_GUARDRAILS`: switching the live
 *  layer off does not switch practice off, or the other way about. A failed
 *  request leaves the caller with no data at all, and every caller falls back to
 *  enforcing — the rules surviving an offline API is the same reason the flag
 *  defaults on server-side. */
export function useGuardLevels() {
  return useQuery({
    queryKey: ["live", "routing", "guards"],
    queryFn: async () => {
      const s = await apiGet<RoutingStatus>("/live/routing");
      // `?? true` for an older API that has no such field: a missing answer
      // means enforced, the same direction the server's own default fails.
      return { guards: s.guards, enforced: s.replay_guardrails ?? true };
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** Point routing at another account on this login. Drops anything staged and
 *  re-reads the new account's working orders and position — a reviewed order
 *  named the old account, and the working list belonged to it. */
export function setBrokerAccount(account_id: string) {
  return apiSend<BrokerState>("POST", "/live/routing/account", { account_id });
}

/** Point routing at another contract — the mini's micro, or back.
 *
 *  The chart does not follow and cannot: the tape's subscription was made at
 *  connect. This changes where orders go, so a plan sized for micros can be
 *  practised against the mini's tape at a tenth of the money.
 *
 *  Rejects with 409 unless the book is flat. That is not tidiness: the daily
 *  loss stop flattens whichever contract routing points at, so switching with
 *  something open would aim it at the wrong one. */
export function setBrokerInstrument(instrument: string) {
  return apiSend<BrokerState>("POST", "/live/routing/instrument", { instrument });
}

/** Step one of two. Renders the order in words and mints a single-use token;
 *  nothing leaves the API process. */
export function previewOrder(body: OrderDraft) {
  return apiSend<OrderPreview>("POST", "/live/routing/preview", body);
}

/** Where the gesture happened and which one it was.
 *
 *  `at` is a `performance.now()` reading taken **in the event handler**, not at
 *  the fetch — the gap between the two is React getting round to the work, and
 *  it is part of what the press cost even though no network is involved in it. */
export interface Press {
  at: number;
  gesture: string;
}

/** Time an order's round trip and report it back afterwards.
 *
 *  ONE CLOCK. Both readings are `performance.now()` in this tab, so the number
 *  is a duration and never a difference between two machines' wall clocks — the
 *  browser is on Windows and the API is in WSL, and subtracting one from the
 *  other would report the skew between them as latency, plausibly and silently.
 *  The server measures its own legs the same way, and `net_ms` is the difference
 *  of the two durations rather than of any two instants.
 *
 *  The report is a separate request, sent after the order is already gone and
 *  awaited by nobody: measuring an order must not cost the order anything. A
 *  failed report is swallowed for the same reason — there is nothing useful to
 *  say to somebody who has just sent an order about a statistic that went
 *  missing. */
async function timed(
  press: Press | undefined,
  send: () => Promise<OrderSent>,
): Promise<OrderSent> {
  const t0 = press?.at ?? performance.now();
  const r = await send();
  const client_ms = Math.round((performance.now() - t0) * 10) / 10;
  void apiSend("POST", "/live/routing/latency", {
    tag: r.tag,
    client_ms,
    gesture: press?.gesture ?? "",
  }).catch(() => {});
  return { ...r, latency: { ...(r.latency ?? { tag: r.tag, how: r.how }), client_ms } };
}

/** Step two of the reviewed path: spend the token. There is no field on this
 *  request that describes an order, which is what makes the review impossible
 *  to skip on an account that confirms. */
export function sendOrder(token: string, press?: Press) {
  return timed(press, () =>
    apiSend<OrderSent>("POST", "/live/routing/orders", { token }),
  );
}

/** The one-click path: the order goes outright, no review. The server refuses
 *  unless *this account* has one-click switched on, so this is not a way around
 *  the confirm — it is the confirm having been turned off, per account, on
 *  purpose. */
export function sendOrderNow(draft: OrderDraft, press?: Press) {
  return timed(press, () =>
    apiSend<OrderSent>("POST", "/live/routing/orders", {
      ...draft,
      one_click: true,
    }),
  );
}

/** Label an account demo or live. `confirm` must repeat the kind — the one
 *  typed confirmation left on this path, and it is once per account rather than
 *  once per session. */
export function tagAccount(accountId: string, kind: "demo" | "live") {
  return apiSend<BrokerState>("PUT", `/live/routing/accounts/${accountId}`, {
    kind,
    confirm: kind,
  });
}

/** Turn the confirm popup off (or on) for one account. Tagging an account live
 *  clears this server-side, so it cannot be enabled on practice and inherited. */
export function setOneClick(accountId: string, on: boolean) {
  return apiSend<BrokerState>(
    "PUT",
    `/live/routing/accounts/${accountId}/one_click`,
    { on },
  );
}

/** The knobs that used to be env vars, plus the guardrail levels.
 *
 *  Everything here takes effect on the next order: an order is checked against
 *  the rules as they stand when it is sent.
 *
 *  `guards` is a partial patch: send the one level being edited and the rest stay
 *  where they were. There is no field for turning the layer off — that is
 *  `LIVE_GUARDRAILS` in the environment, so that switching the rules off means
 *  leaving the chart. */
export function saveRoutingSettings(s: {
  max_qty?: number;
  guards?: Partial<GuardLevels>;
}) {
  return apiSend<{ max_qty: number; guards: GuardLevels }>(
    "PUT",
    "/live/routing/settings",
    s,
  );
}

/** A drag, landed. Omitted fields are left alone.
 *
 *  Deliberately not optimistic: nothing updates the chart from this call's
 *  result. The next poll reads the value back from the broker, so a refused
 *  modify shows up as the line returning to where it was — which is a truer
 *  error report than a message box, and needs no reconciliation logic. */
export function modifyBrokerOrder(body: {
  basket_id: string;
  price?: number | null;
  stop?: number | null;
  target?: number | null;
}) {
  return apiSend<{ basket_id: string; changed: string[] }>(
    "POST",
    "/live/routing/modify",
    body,
  );
}

/** Book paper trades taken on the live chart into the journal.
 *
 *  Posted from here rather than derived on the server, because `replaySim.ts`
 *  is the only thing that knows a paper fill happened — the same arrangement
 *  `/replays` uses, and for the same reason: one engine, so the journal cannot
 *  disagree with the chart that produced the trade.
 *
 *  Safe to re-post: the server dedupes on a content hash, so sending a trade
 *  twice writes one row. That is what lets the caller be careless about
 *  retries. */
export function journalPaperTrades(body: {
  symbol: string;
  exchange?: string;
  date: string;
  trades: {
    side: string;
    size: number;
    entry_price: number;
    entry_ms: number;
    exit_price: number;
    exit_ms: number;
    pnl: number;
    pts?: number;
    reason?: string;
  }[];
}) {
  return apiSend<{ written: number; received: number; source_file: string }>(
    "POST",
    "/live/journal/paper",
    body,
  );
}

export function cancelBrokerOrder(basket_id: string) {
  return apiSend<{ basket_id: string; ok: boolean }>("POST", "/live/routing/cancel", {
    basket_id,
  });
}

/** Cancel everything working, then exit the position. Gated on nothing but the
 *  connection, on purpose: a stop button you have to unlock is not a stop
 *  button. */
export function flattenAll() {
  return apiSend<{ ok: boolean }>("POST", "/live/routing/flatten");
}

/** Re-ask the broker what is working and what is held. Available on any
 *  account — reading the truth is never the dangerous operation. */
export function refreshBroker() {
  return apiSend<BrokerState>("POST", "/live/routing/refresh");
}
