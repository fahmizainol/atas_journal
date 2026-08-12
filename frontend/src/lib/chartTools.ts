// The chart's hand-tools, named once so a rail outside the canvas can talk about
// them.
//
// A tool is a mode of the *terminal*, not a property of one chart. That was true
// with one pane and merely academic; with four it is the difference between one
// rail and four, each eating chart pixels and each acting only on its own canvas.
// So the vocabulary moves here, out of the component, and the rail arms the
// focused pane through the chart's imperative handle.
//
// NOTE the deliberate asymmetry: arming is a page-level decision, but everything
// a tool *does* stays inside ReplayChart. It owns the pointer, the drawings and
// the mutual exclusion between tools; the rail is a remote control over that, not
// a second owner of it. (See docs/terminal-redesign-plan.md — this is option B,
// which is the cheaper first move and reversible into the other one.)

export type ChartToolId = "order" | "vp" | "ruler" | "avwap" | "hline";

/** What a pane's tools are doing, as the pane reports it to the page. Everything
 *  here is drawn on the rail: the armed tool lights up, and the "take it away"
 *  buttons only exist while there is something to take. */
export interface ChartToolState {
  armed: ChartToolId | null;
  /** Whether ＋Order can be armed at all — false before a session is loaded, and
   *  the button then says so rather than going quietly dead. */
  canOrder: boolean;
  /** An anchored VWAP is drawn on this pane. */
  hasAvwap: boolean;
  /** A fixed-range profile is selected (Del removes it). */
  hasRangeSel: boolean;
  /** A price line is selected. */
  hasHlineSel: boolean;
  /** Fixed-range profiles + price lines on the pane, for the 🧹 that clears the
   *  lot. */
  drawings: number;
}

export const EMPTY_TOOL_STATE: ChartToolState = {
  armed: null,
  canOrder: false,
  hasAvwap: false,
  hasRangeSel: false,
  hasHlineSel: false,
  drawings: 0,
};

/** The rail's own order, with the icons and the sentences the in-canvas rail
 *  already used — this is a move, not a rewrite, and the tooltips are the ones
 *  that were written for these tools. */
export interface ChartToolSpec {
  id: ChartToolId;
  icon: string;
  label: string;
  /** What the button says when the tool is waiting for its click. */
  armedLabel: string;
  tip: string;
  armedTip: string;
}

export const CHART_TOOLS: readonly ChartToolSpec[] = [
  {
    id: "order",
    icon: "🧾",
    label: "＋ Order",
    armedLabel: "Pick a side…",
    tip: "Place an order — pick limit or stop on the chart, then click a price. The mouse gesture is Space + click, which needs nothing armed.",
    armedTip: "Pick limit or stop, then click a price (Esc to cancel)",
  },
  {
    id: "vp",
    icon: "📊",
    label: "Fixed range VP",
    armedLabel: "Drag a range…",
    tip: "Fixed-range volume profile — drag across a range to profile it. Drag its edges to resize, its body to move, Del to remove.",
    armedTip: "Drag across the chart to profile that range (Esc to cancel)",
  },
  {
    id: "ruler",
    icon: "📏",
    label: "Measure",
    armedLabel: "Measuring…",
    tip: "Ruler — measure between two points: points/ticks/%, $ per lot, bars and time. Click the chart or press Esc to dismiss.",
    armedTip: "Drag (or click, move, click) between two points to measure (Esc to cancel)",
  },
  {
    id: "avwap",
    icon: "⚓",
    label: "Anchored VWAP",
    armedLabel: "Click a bar…",
    tip: "Anchored VWAP — click any bar to draw a VWAP + ±1σ/±2σ bands from that point forward. Keeps developing as the replay runs. Click again to re-anchor.",
    armedTip: "Click a bar to anchor the VWAP there (Esc to cancel)",
  },
  {
    id: "hline",
    icon: "━",
    label: "Price line",
    armedLabel: "Click a price…",
    tip: "Horizontal line — click a price to mark it. The tape crossing it chimes once and the line dims; drag it to move and re-arm it, Del to remove.",
    armedTip: "Click a price to put a line there (Esc to cancel)",
  },
];
