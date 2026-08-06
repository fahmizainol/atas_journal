/** Whether the primary pointer is a fingertip rather than a mouse.
 *
 *  Everything touch-related keys off this rather than off viewport width: a
 *  phone held sideways is 844px wide and still a fingertip, and a 1280px tablet
 *  is wider than plenty of laptops. Width tells you how much room there is; it
 *  tells you nothing about how precisely the user can point at it.
 *
 *  Read once at module load. A device does not grow a mouse mid-session, and the
 *  alternative — a live `matchMedia` listener threaded into canvas hit-testing —
 *  buys nothing for the hybrid case that would actually need it (a laptop with a
 *  touchscreen reports `fine`, so it keeps the mouse-sized targets it should).
 */
export const COARSE_POINTER =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(pointer: coarse)").matches;

/** Pick the touch value or the mouse one. Reads better at the use site than a
 *  ternary on a bare boolean, and keeps the two numbers side by side. */
export const byPointer = <T,>(fine: T, coarse: T): T => (COARSE_POINTER ? coarse : fine);
