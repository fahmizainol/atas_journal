import { useCallback, useEffect, useState } from "react";

/** Is native fullscreen actually available for a plain element?
 *
 *  iOS Safari says no — it only ever fullscreens a <video> — and the button has
 *  to be absent there rather than present and inert. Checked once at module
 *  load: the answer cannot change for the life of the document. */
const CAN_FULLSCREEN =
  typeof document !== "undefined" &&
  document.fullscreenEnabled === true &&
  typeof document.documentElement.requestFullscreen === "function";

/**
 * ⛶, for the pages that draw their own bar.
 *
 * Every chrome-less workspace wants this button and wants it to behave
 * identically: the app has already given the tape every pixel it owns, and the
 * only ones left are the browser's own tab and address bars, which only the
 * browser can hide. Shared rather than copied because the state is subtle — Esc
 * leaves fullscreen without going through the button, so the icon has to follow
 * the document rather than a click count.
 *
 * Renders nothing where fullscreen isn't offered.
 */
export function FullscreenButton() {
  const [isFull, setIsFull] = useState(false);

  useEffect(() => {
    if (!CAN_FULLSCREEN) return;
    const sync = () => setIsFull(document.fullscreenElement != null);
    document.addEventListener("fullscreenchange", sync);
    sync();
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  const toggle = useCallback(() => {
    // Whole document, not the chart element: the page already fills the viewport,
    // so the only pixels left to win are the browser's own.
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen().catch(() => {});
  }, []);

  if (!CAN_FULLSCREEN) return null;
  return (
    <button
      type="button"
      className={`chart-topbar-btn${isFull ? " on" : ""}`}
      onClick={toggle}
      aria-pressed={isFull}
      title={isFull ? "Leave fullscreen (Esc)" : "Fullscreen — hides the browser's own chrome"}
    >
      ⛶
    </button>
  );
}
