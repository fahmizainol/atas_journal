// The confirm popup — the ATAS model, and the thing one-click trading skips.
//
// It shows the order as an English sentence, rendered by the server, because
// the accident it guards against is not misreading a number. It is not reading
// at all: a form full of fields you have filled in a hundred times gets skimmed,
// and a sentence naming the account and its kind does not.
//
// FAST TO DISMISS, ON PURPOSE. Enter sends, Esc cancels (bound in
// `useOrderIntent`, at the window, in capture — so they beat the page's own
// q/w/s handler). Send takes focus on open. A dialog you have to aim the mouse
// at would cost exactly what the chart gestures exist to give you, and the
// honest response to that would be to turn one-click on, which is the more
// dangerous of the two settings. Making the safe path fast is what keeps it
// the one people leave switched on.

import { useEffect, useRef, useState } from "react";
import type { PendingOrder } from "../hooks/useOrderIntent";
import { palette } from "../theme";

export function OrderConfirm({
  pending,
  kind,
  busy,
  onConfirm,
  onCancel,
}: {
  pending: PendingOrder;
  /** The account's label — "demo" or "live". Colours the whole dialog. */
  kind: string | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const sendRef = useRef<HTMLButtonElement>(null);
  const live = kind === "live";
  const accent = live ? palette.red : palette.orange;

  useEffect(() => {
    sendRef.current?.focus();
  }, []);

  // Seconds left on the review. The server forgets the token on its own clock;
  // this is what that deadline looks like. Ticking to zero closes the dialog
  // rather than leaving a button that can only fail.
  const [left, setLeft] = useState(pending.expires_in_s);
  useEffect(() => {
    const t = window.setInterval(() => {
      const s = pending.expires_in_s - (Date.now() - pending.at) / 1000;
      setLeft(s);
      if (s <= 0) onCancel();
    }, 250);
    return () => window.clearInterval(t);
  }, [onCancel, pending]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirm order"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        display: "grid",
        placeItems: "center",
        background: "rgba(0,0,0,0.55)",
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          minWidth: 300,
          maxWidth: 420,
          background: palette.card,
          border: `2px solid ${accent}`,
          borderRadius: 6,
          padding: 14,
        }}
      >
        <div style={{ fontSize: 10, letterSpacing: 0.6, color: accent }}>
          {live ? "⚠ LIVE ACCOUNT — REAL MONEY" : "CONFIRM ORDER"}
        </div>
        <p style={{ fontSize: 14, lineHeight: 1.55, margin: "8px 0 12px" }}>
          {pending.sentence}
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel <span style={{ opacity: 0.6 }}>(Esc)</span>
          </button>
          <button
            ref={sendRef}
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={{ borderColor: accent, color: accent, fontWeight: 600 }}
          >
            {busy ? "Sending…" : "Send"} <span style={{ opacity: 0.6 }}>(↵)</span>
          </button>
          <span style={{ marginLeft: "auto", fontSize: 11, color: palette.muted }}>
            {Math.max(0, Math.round(left))}s
          </span>
        </div>
      </div>
    </div>
  );
}

/** The receipt for an order that went out without a dialog, and the complaint
 *  when one failed. Both are transient and neither is the account's state —
 *  the panel is where you look for that. */
export function OrderFlash({
  flash,
  error,
  onDismiss,
}: {
  flash: string | null;
  error: string | null;
  onDismiss: () => void;
}) {
  if (!flash && !error) return null;
  return (
    <div
      style={{
        position: "absolute",
        top: 8,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 40,
        padding: "4px 10px",
        borderRadius: 4,
        fontSize: 12,
        background: palette.card,
        border: `1px solid ${error ? palette.red : palette.green}`,
        color: error ? palette.red : palette.green,
        cursor: error ? "pointer" : "default",
      }}
      onClick={error ? onDismiss : undefined}
      title={error ? "Dismiss" : undefined}
    >
      {error ? `⚠ ${error}` : flash}
    </div>
  );
}
