// How many charts the page draws, and where each one sits.
//
// The Charts workspace used to have exactly two arrangements — one pane, or a
// trading pane beside a read-only context pane — and both were expressible as a
// flex row with a percentage on the first child. Six arrangements are not, so
// the geometry moves here, out of the page, as data.
//
// EVERY LAYOUT IS PLACED ON ONE 3x3 LINE GRID. Two content tracks per axis with
// a divider track between them:
//
//        line 1        line 2   line 3        line 4
//          |             |        |             |
//          |   column A  | DIV_PX |   column B  |
//          v             v        v             v
//
// so a pane is four grid *line* numbers and nothing else. The one-pane case
// falls out for free: a pane spanning lines 1..4 simply swallows the divider
// track, so there is no special case anywhere for "no split" — the same
// template, the same placement code, one pane that happens to cover everything.
//
// The two ratios (`cx`, `ry`) are the only continuous state. Which of them a
// given layout actually uses is a property of the layout, not of the page, so a
// layout with no horizontal divider simply never reads `ry` — and the value
// survives, which is what lets you switch 2-col -> 2x2 -> 2-col and land back
// on the divider positions you had.
//
// NOTHING HERE KNOWS ABOUT CHARTS. It is placement arithmetic, so it can be
// read and checked without a canvas, a tape, or a browser.

/** The arrangements the picker offers, in the order it offers them. */
export const LAYOUT_IDS = ["one", "col2", "row2", "left3", "top3", "quad"] as const;
export type LayoutId = (typeof LAYOUT_IDS)[number];

/** Where one pane sits, as grid line numbers on the 3x3 line grid above. */
export interface PanePlace {
  /** Row lines: [start, end]. */
  row: readonly [number, number];
  /** Column lines: [start, end]. */
  col: readonly [number, number];
}

/** A divider, placed the same way. `axis` is what dragging it changes: a
 *  vertical divider moves `cx`, a horizontal one moves `ry`. */
export interface DividerPlace extends PanePlace {
  axis: "v" | "h";
}

export interface Layout {
  id: LayoutId;
  /** For the picker's button. Short enough to sit under a 30px icon. */
  label: string;
  /** For the title attribute, spelled out. */
  title: string;
  panes: number;
  place: readonly PanePlace[];
  dividers: readonly DividerPlace[];
}

/** The divider track's thickness, in px. Matches `.sim-pane-divider`'s old flex
 *  basis — a hairline with a generous grab area either side of it (the CSS
 *  widens the hit box past the paint). */
export const DIVIDER_PX = 7;

const P = (r1: number, r2: number, c1: number, c2: number): PanePlace => ({
  row: [r1, r2],
  col: [c1, c2],
});
const D = (axis: "v" | "h", r1: number, r2: number, c1: number, c2: number): DividerPlace => ({
  axis,
  row: [r1, r2],
  col: [c1, c2],
});

// Read each `place` as "rows r1..r2, columns c1..c2". A span of 1..4 covers the
// whole axis including the divider track, which is how the panes that are not
// split on that axis are written.
export const LAYOUTS: Record<LayoutId, Layout> = {
  one: {
    id: "one",
    label: "1",
    title: "One chart",
    panes: 1,
    place: [P(1, 4, 1, 4)],
    dividers: [],
  },
  col2: {
    id: "col2",
    label: "2",
    title: "Two side by side",
    panes: 2,
    place: [P(1, 4, 1, 2), P(1, 4, 3, 4)],
    dividers: [D("v", 1, 4, 2, 3)],
  },
  row2: {
    id: "row2",
    label: "2",
    title: "Two stacked",
    panes: 2,
    place: [P(1, 2, 1, 4), P(3, 4, 1, 4)],
    dividers: [D("h", 2, 3, 1, 4)],
  },
  left3: {
    id: "left3",
    label: "3",
    title: "One left, two stacked right",
    panes: 3,
    place: [P(1, 4, 1, 2), P(1, 2, 3, 4), P(3, 4, 3, 4)],
    // The horizontal divider only spans the right-hand column, because the left
    // pane runs past it.
    dividers: [D("v", 1, 4, 2, 3), D("h", 2, 3, 3, 4)],
  },
  top3: {
    id: "top3",
    label: "3",
    title: "One on top, two below",
    panes: 3,
    place: [P(1, 2, 1, 4), P(3, 4, 1, 2), P(3, 4, 3, 4)],
    dividers: [D("h", 2, 3, 1, 4), D("v", 3, 4, 2, 3)],
  },
  quad: {
    id: "quad",
    label: "4",
    title: "Two by two",
    panes: 4,
    place: [P(1, 2, 1, 2), P(1, 2, 3, 4), P(3, 4, 1, 2), P(3, 4, 3, 4)],
    dividers: [D("v", 1, 4, 2, 3), D("h", 2, 3, 1, 4)],
  },
};

/** The most panes any layout draws — how many chart instances the page keeps
 *  state for, so that switching layouts never loses a pane's bucketing. */
export const MAX_PANES = LAYOUT_IDS.reduce((n, id) => Math.max(n, LAYOUTS[id].panes), 0);

export function isLayoutId(v: unknown): v is LayoutId {
  return typeof v === "string" && (LAYOUT_IDS as readonly string[]).includes(v);
}

/** Keep a divider away from both edges. A pane dragged to nothing is a pane you
 *  cannot get back by dragging — the same reason `clampSplit` existed for the
 *  two-pane split, and the same bounds. */
export function clampRatio(v: unknown, fallback: number): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return fallback;
  return Math.min(80, Math.max(20, Math.round(v)));
}

/** The grid this layout's placements are read against.
 *
 *  `fr` rather than `%` so the divider's fixed 7px comes off the total before
 *  the panes divide what is left — with percentages the two panes plus the
 *  divider would add up to more than the box and the last one would overflow.
 */
export function gridTemplate(cx: number, ry: number): {
  gridTemplateColumns: string;
  gridTemplateRows: string;
} {
  return {
    gridTemplateColumns: `${cx}fr ${DIVIDER_PX}px ${100 - cx}fr`,
    gridTemplateRows: `${ry}fr ${DIVIDER_PX}px ${100 - ry}fr`,
  };
}

/** One placement as a `grid-area` value: row-start / column-start / row-end /
 *  column-end, which is the order CSS wants them in. */
export function gridArea(p: PanePlace): string {
  return `${p.row[0]} / ${p.col[0]} / ${p.row[1]} / ${p.col[1]}`;
}

/** Which pane index owns a given slot after a layout change.
 *
 *  Panes keep their identity across a change — pane 2 is pane 2 whether the
 *  layout is 2-col or 2x2 — so this is only interesting at the edges: a pane
 *  that no longer exists hands whatever it owned (focus, the ticket) back to
 *  pane 0 rather than to nothing. */
export function clampPaneIndex(i: number, layout: LayoutId): number {
  return i >= 0 && i < LAYOUTS[layout].panes ? i : 0;
}
