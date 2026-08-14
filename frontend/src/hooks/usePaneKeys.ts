// The pane keys, once, for both terminals.
//
// Not the *trading* keys — q/w/s stay on each page, because what they do
// differs (one appends to a log, the other reaches a broker) and a hook that
// tried to own both would end up taking a dozen callbacks. These are the keys
// that are purely about which chart you are looking at and how it is bucketed,
// which is the same question on both pages and had two answers only because
// Live got its panes later.
//
//   1–8         set the focused pane's bar size (TIMEFRAMES, in order)
//   Shift+1–4   focus a pane
//
// **Shift for focus, bare digits for the bar size**, and not the other way
// round: the bare digits have picked the bar size since before there were
// panes, and taking a binding away from a page you drive by keyboard is worse
// than spending a modifier on the newer thing.
//
// The focus keys read `e.code`, not `e.key`. A shifted digit is punctuation, and
// *which* punctuation depends on the keyboard layout — `!` on US, `"` on UK for
// Shift+2. `Digit2` is the physical key either way.

import { useEffect } from "react";
import { TIMEFRAMES } from "../lib/timeframes";

/**
 * Is this key event really somebody typing?
 *
 * The one guard every single-key binding in this app needs, and it was written
 * out by hand in three places. A field with the caret in it owns its own keys; a
 * chord belongs to the browser or the OS; and an autorepeat must never reach a
 * binding that acts (a held `w` machine-gunning market orders is the worked
 * example this rule exists for).
 */
export function isTypingTarget(e: KeyboardEvent): boolean {
  if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return true;
  const el = e.target as HTMLElement | null;
  const tag = el?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!el?.isContentEditable;
}

export interface PaneKeys {
  /** How many panes are on screen. A function, not a number: the handler is
   *  installed once and would otherwise close over the count at mount. */
  paneCount: () => number;
  /** Which pane the chrome currently acts on. */
  focused: () => number;
  setFocus: (pane: number) => void;
  /** Re-bucket one pane. The focused pane's, never the page's — the same rule
   *  the top bar's picker follows, so the key and the button cannot disagree
   *  about which chart they just changed. */
  setPaneTimeframe: (pane: number, id: string) => void;
  /** Stand down entirely — a confirm dialog is up, the page is read-only, or
   *  there is no tape yet. */
  disabled?: () => boolean;
}

export function usePaneKeys({ paneCount, focused, setFocus, setPaneTimeframe, disabled }: PaneKeys): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e) || disabled?.()) return;
      if (e.shiftKey && /^Digit[1-4]$/.test(e.code)) {
        const i = Number(e.code.slice(5)) - 1;
        if (i < paneCount()) {
          e.preventDefault();
          setFocus(i);
        }
        return;
      }
      if (e.shiftKey) return;
      if (!/^[1-8]$/.test(e.key)) return;
      const tf = TIMEFRAMES[Number(e.key) - 1];
      if (!tf) return;
      e.preventDefault();
      setPaneTimeframe(focused(), tf.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [disabled, focused, paneCount, setFocus, setPaneTimeframe]);
}
