// How much viewport is left below an element's top edge.
//
// The chart pages fill the screen instead of scrolling — the tape wants to be as
// tall as it can be — and the chrome above them (topbar, tabs, padding) is not a
// fixed height to subtract. So it is measured, and published as `--sim-fill-h`
// for `.sim-page` to consume. Every decision about how the measurement is *used*
// stays in CSS, which is what lets a viewport too short to fill opt out of
// filling; an inline height could not be talked out of it.
//
// Shared rather than copied because the pages that need it are the ones that
// would each carry their own copy of the rotation workaround below, and a subtle
// timing fix that exists twice is a subtle timing fix that gets fixed once.
//
// Without this the page height falls back to `auto`, `.sim-body`'s `flex: 1` has
// no definite height to take a share of, and the chart collapses to whatever its
// tallest sibling happens to be — which is a bug that looks like "the chart is
// squashed until the side panel loads".

import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from "react";

/** Breathing room under a page that sits inside the shell's padding, so a
 *  viewport-filling layout doesn't butt against the bottom edge (and doesn't
 *  hand the document a scrollbar).
 *
 *  A page that runs edge to edge wants none of it and passes `0` — a chart page
 *  draws no shell chrome and no padding, so 16px of reserved gutter would be
 *  16px of the tape spent on nothing. */
export const PAGE_GUTTER = 16;

/**
 * The pixel height `ref`'s element should take to reach the bottom of the
 * viewport, or null before the first measurement.
 *
 * Re-measured after every render, not once on mount. A page whose `.sim-page`
 * appears only after some state arrives — Live shows a "no session" card until
 * the status poll answers — would otherwise measure against a null ref, find
 * nothing, and never look again. The height then stays `auto`, `.sim-body`'s
 * `flex: 1` has no definite height to take a share of, and the chart collapses to
 * whatever its tallest sibling happens to be.
 *
 * Cheap enough to do unconditionally: one `getBoundingClientRect` per render, and
 * the state only changes when the number does, so it cannot loop. There is no
 * feedback risk either — what is measured is the element's *top*, which is set by
 * the chrome above it and cannot be moved by the height this returns.
 */
export function useFillHeight(ref: RefObject<HTMLElement | null>, gutter: number = PAGE_GUTTER): number | null {
  const [fillH, setFillH] = useState<number | null>(null);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const next = Math.max(0, window.innerHeight - top - gutter);
    setFillH((prev) => (prev === next ? prev : next));
  }, [ref, gutter]);

  // Layout, not passive: the height lands in the same frame the element does, so
  // the chart is never painted at the collapsed size first.
  useLayoutEffect(measure);

  useEffect(() => {
    // A rotate resizes the viewport, but iOS reports the pre-rotation numbers for
    // a frame or two afterwards — so measure again on the far side of a paint.
    const remeasure = () => requestAnimationFrame(() => requestAnimationFrame(measure));
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", remeasure);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", remeasure);
    };
  }, [measure]);

  return fillH;
}
