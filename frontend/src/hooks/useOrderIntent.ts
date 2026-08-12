// The one place a chart gesture becomes an order.
//
// Every origination point on the Live page — q/w/s, the BUY/SELL dock,
// space+click, the long-press ticket, and the routing panel's own pad — ends
// here, and this decides which of three things happens:
//
//   paper account          → the caller's own fallback: append to the log and
//                            fold it over the tape, exactly as before
//   real, one-click on     → straight to the broker, no dialog
//   real, one-click off    → a confirm naming the order in words, then send
//
// WHY THE FUNNEL IS ONE FUNCTION. The alternative is each gesture deciding for
// itself, and the failure mode of that is not a crash — it is one gesture that
// forgot to check, sending live orders while the others confirm. There is no
// arrangement of this page in which some gestures route and others do not,
// because they all ask the same object.
//
// ORDERS DO NOT QUEUE. While a confirm is open, `submit` refuses. A second
// gesture behind an unanswered dialog is the fastest way to send two orders
// when you meant one — and the keyboard handler that fires q/w/s is exactly the
// kind of thing that repeats under a stuck key.

import { useCallback, useEffect, useRef, useState } from "react";
import { previewOrder, sendOrder, sendOrderNow } from "./useRouting";
import { playCue } from "../lib/orderSound";
import type { BrokerState, OrderDraft } from "../lib/routingTypes";

/** A reviewed order waiting for a yes. `at` is the client's stamp and only
 *  drives the countdown — the server holds the real deadline and refuses on its
 *  own clock, so a drifting browser fails safe. */
export interface PendingOrder {
  draft: OrderDraft;
  token: string;
  sentence: string;
  expires_in_s: number;
  at: number;
}

export interface OrderIntent {
  /** Will a gesture actually send? The server's own `routable`, which is the
   *  whole of `Broker.check_routable` — a real account, labelled, read back.
   *  This is what the chart draws its "this is live" outline from, so it must
   *  mean *will send*, not *might*. */
  routes: boolean;
  /** Is a real account selected at all, sendable or not? The two differ while
   *  the account is untagged or the broker has not been read back yet, and in
   *  that window the gesture **refuses** rather than quietly filling the paper
   *  blotter. Selecting a live account and getting a paper fill is a surprise in
   *  both directions — you either think you are in a trade you are not, or you
   *  have made a practice trade you did not ask for. Paper is one click away in
   *  the selector if that is what you wanted. */
  real: boolean;
  /** The order awaiting confirmation, or null. */
  pending: PendingOrder | null;
  busy: boolean;
  error: string | null;
  /** Last thing that went out, for a brief on-screen acknowledgement. */
  flash: string | null;
  /** Send an order, or stage it for confirmation. Returns true if it took the
   *  order — false means "not routing, do your paper thing". */
  submit: (draft: OrderDraft) => boolean;
  confirm: () => void;
  cancel: () => void;
  clearError: () => void;
  /** Report a failure from something this hook did not send — a cancel, a drag.
   *  They share the page's one error surface so there is a single place a
   *  refusal shows up, whatever refused. */
  fail: (e: unknown) => void;
}

export function useOrderIntent(
  broker: BrokerState | null,
  onDone: () => void,
): OrderIntent {
  const [pending, setPending] = useState<PendingOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  // Read by the keyboard handler, which must not fire a second order behind an
  // open dialog. A ref rather than the state value because that handler is
  // installed once and would otherwise close over a stale `pending`.
  const pendingRef = useRef<PendingOrder | null>(null);
  pendingRef.current = pending;
  const busyRef = useRef(false);
  busyRef.current = busy;

  const real = !!broker && !broker.paper;
  const routes = real && !!broker?.routable;
  const oneClick = !!broker?.one_click;

  // The acknowledgement clears itself. It exists so a one-click order that went
  // out silently leaves *some* trace on screen; leaving it up would then have it
  // read as the state of the account rather than as a receipt.
  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => setFlash(null), 4000);
    return () => window.clearTimeout(t);
  }, [flash]);

  const err = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const submit = useCallback(
    (draft: OrderDraft): boolean => {
      if (!real) return false;          // paper: the caller does its own thing
      if (!routes) {
        // A real account is selected and something about it still refuses —
        // it is untagged, or the broker has not been read back. Taking the
        // gesture and saying so beats letting it fall through to paper.
        setError(
          "this account cannot send yet — label it demo or live in the ⌁ panel " +
            "and let the broker read back, or switch to Paper to practise",
        );
        return true;
      }
      // Refused rather than queued — see the note at the top of this file.
      if (pendingRef.current || busyRef.current) return true;
      setError(null);
      setBusy(true);
      void (async () => {
        try {
          if (oneClick) {
            const r = await sendOrderNow(draft);
            // Sounded on the acknowledgement, not on the gesture: on this side
            // "placed" means the broker took it. A tick that fired when you
            // pressed the key would be a sound for an order that might have been
            // refused — and the fill chime that follows comes from the poll, so
            // even a market order gets two distinct, well-separated noises.
            playCue("placed");
            setFlash(`${draft.side.toUpperCase()} ${draft.qty} sent · ${r.basket_id || r.tag}`);
            onDone();
          } else {
            const p = await previewOrder(draft);
            setPending({ draft, ...p, at: Date.now() });
          }
        } catch (e) {
          err(e);
        } finally {
          setBusy(false);
        }
      })();
      return true;
    },
    [oneClick, onDone, real, routes],
  );

  const confirm = useCallback(() => {
    const p = pendingRef.current;
    if (!p || busyRef.current) return;
    setBusy(true);
    setError(null);
    // Cleared before the round trip, not after: the token is spent either way,
    // so leaving the dialog up invites a second Enter that can only fail.
    setPending(null);
    void (async () => {
      try {
        const r = await sendOrder(p.token);
        playCue("placed");
        setFlash(`${p.draft.side.toUpperCase()} ${p.draft.qty} sent · ${r.basket_id || r.tag}`);
        onDone();
      } catch (e) {
        err(e);
      } finally {
        setBusy(false);
      }
    })();
  }, [onDone]);

  const cancel = useCallback(() => setPending(null), []);

  // Enter sends, Esc cancels. Captured at the window so it beats the page's own
  // q/w/s binding, and installed only while something is pending so it costs
  // nothing the rest of the time. A confirm you have to aim the mouse at would
  // destroy the muscle memory it is protecting.
  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        confirm();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        cancel();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [cancel, confirm, pending]);

  return {
    routes,
    real,
    pending,
    busy,
    error,
    flash,
    submit,
    confirm,
    cancel,
    clearError: () => setError(null),
    fail: err,
  };
}
