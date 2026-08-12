// Which chart pane the keyboard belongs to.
//
// ReplayChart binds Escape and Delete on `window` rather than on its own canvas,
// and has to: a selection made with the mouse must be dismissable without first
// clicking back onto the chart, and a canvas cannot hold DOM focus to receive the
// key itself. With one chart on the page that is simply correct. With two it is
// not — both instances answer the same keypress, so a Delete aimed at the pane
// you are working in also deletes the selection in the pane you are not.
//
// So the panes elect an owner. A pane claims the keyboard when the pointer enters
// it or a press lands on it, and the first pane to mount claims it unopposed —
// which is what makes a single-pane page behave exactly as it did before this
// file existed. There is no unfocused state while any pane is alive: the last
// pane to be touched keeps the keyboard until another one takes it, so keys still
// work with the pointer parked off the chart entirely.
//
// Deliberately module state and not React state. These are read inside `window`
// event handlers that were installed once and never re-bound (re-binding them per
// render would drop keys mid-press), so what they need is something they can ask
// at event time — not something that re-renders them when it changes.

/** Every mounted pane, in mount order. */
const live = new Set<number>();
let active: number | null = null;
let seq = 0;

/** An identity for one pane, stable for its lifetime. */
export function nextChartId(): number {
  return ++seq;
}

/** Register a pane. The first one alive takes the keyboard with it. */
export function mountChart(id: number): void {
  live.add(id);
  if (active == null) active = id;
}

/** Unregister a pane, handing the keyboard on if it was holding it. */
export function unmountChart(id: number): void {
  live.delete(id);
  if (active === id) active = live.values().next().value ?? null;
}

/** Take the keyboard — the pointer entered this pane, or pressed it. */
export function focusChart(id: number): void {
  if (live.has(id)) active = id;
}

/** Whether a key belongs to this pane. */
export function hasChartFocus(id: number): boolean {
  return active === id;
}
