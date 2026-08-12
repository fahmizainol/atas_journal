// Panes reading the same moment: one crosshair across the grid, one right edge.
//
// WHY THIS EXISTS. The point of four charts over one tape is reading one moment
// at four bucketings. Without a shared crosshair you are eyeballing which bar on
// the 1h pane corresponds to the one under the pointer on the 5m — which is the
// whole question the grid was supposed to answer.
//
// THE DECISION THAT MATTERS: link means the same RIGHT EDGE, not the same
// visible window. Pushing one pane's range onto the others is the obvious
// implementation and it is wrong for mixed bucketings — an hourly pane forced
// into a 5-minute pane's window shows four candles. So the right edge is pinned
// and each pane keeps its own span: scrolling back an hour anywhere moves every
// pane back an hour, and each still shows the amount of history its bucketing is
// for.
//
// Deliberately module state and not React state, for the same reason
// lib/chartFocus is: these are read inside chart event handlers installed once
// inside a build effect that must never re-bind (re-binding a crosshair handler
// per render is a render per pixel). What they need is something they can ask at
// event time.
//
// NOTHING HERE KNOWS ABOUT lightweight-charts. A pane hands over two functions
// and gets told when another pane moved; what "put the crosshair there" costs is
// the pane's business.

/** What a pane can be asked to do when another pane moves. */
export interface LinkTarget {
  /** Put the crosshair at this bar time and price — or take it away (`null`).
   *  The time is the *source* pane's, which is very often not a bar on this
   *  one; snapping it (or ignoring it) is the receiver's problem. */
  crosshair(time: number | null, price: number): void;
  /** Pin the right edge of the view to this time, keeping this pane's own span. */
  rightEdge(time: number): void;
}

const targets = new Map<number, LinkTarget>();
/** Panes currently opted out — the `⇄` badge off on that pane. */
const muted = new Set<number>();
/** The global switch (the top bar's `⇄`). */
let on = true;
/** Re-entrancy: applying a range to a pane fires that pane's own range handler,
 *  which would publish straight back and ping-pong until the stack runs out. */
let fanning = false;

/** Join the link. Returns the leave function — call it when the chart is torn
 *  down, because the target closes over a chart that will be `remove()`d. */
export function joinLink(id: number, t: LinkTarget): () => void {
  targets.set(id, t);
  return () => {
    targets.delete(id);
  };
}

/** The global switch. */
export function setLinkOn(v: boolean): void {
  on = v;
}

/** Whether this pane follows the others: the global switch, and its own badge. */
export function setPaneLinked(id: number, linked: boolean): void {
  if (linked) muted.delete(id);
  else muted.add(id);
}

const participates = (id: number): boolean => on && !muted.has(id);

/** Hand a crosshair position to every other participating pane. `time` null is
 *  the pointer leaving — the others drop their crosshair with it, rather than
 *  keeping a stale one that reads as a live reading. */
export function publishCrosshair(from: number, time: number | null, price: number): void {
  if (fanning || !participates(from)) return;
  fanning = true;
  try {
    for (const [id, t] of targets) {
      if (id === from || !participates(id)) continue;
      try {
        t.crosshair(time, price);
      } catch {
        // A time that isn't on this pane's bucketing throws out of
        // setCrosshairPosition. One pane failing to follow is not a reason for
        // the others not to.
      }
    }
  } finally {
    fanning = false;
  }
}

/** Hand a right edge to every other participating pane. */
export function publishRightEdge(from: number, time: number): void {
  if (fanning || !participates(from)) return;
  fanning = true;
  try {
    for (const [id, t] of targets) {
      if (id === from || !participates(id)) continue;
      try {
        t.rightEdge(time);
      } catch {
        /* as above */
      }
    }
  } finally {
    fanning = false;
  }
}
