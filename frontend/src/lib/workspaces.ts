// The app is really three products sharing one shell: a retrospective Journal
// (scoped by the FilterBar), a prospective Lab (market-data research that
// ignores the FilterBar), and Charts — the market itself, played back or live.
// Group the tabs by workspace so the modes stop interleaving, and only show the
// FilterBar where it actually drives the page.
//
// Charts exists because Replay and Live are one chart with two clocks, not two
// products: the same tape, the same engine, the same indicators, differing only
// in where the clock comes from and whether you may seek. Keeping them adjacent
// is what stops the second one being written as a copy of the first.
//
// This lives in lib/ rather than in Layout because Layout is no longer the only
// thing that renders navigation: the chart pages draw no shell chrome and carry
// their own bar, whose ☰ menu has to offer the same destinations. Two copies of
// this list is two places for a new page to be missing from.

export type Tab = { to: string; label: string; end?: boolean };

export type Workspace = {
  id: string;
  label: string;
  filterBar: boolean;
  /** Does the shell draw the topbar, tab strip and data sidebar for this
   *  workspace? False means the page draws its own.
   *
   *  Charts is the one that opts out: the tape is the product there, and 215px
   *  of topbar/tabs/padding above it was a fifth of a 1080p viewport spent on
   *  navigation you look at once. It is a property of the workspace rather than
   *  a page-level opt-out because the shell has to know before it renders, not
   *  after. */
  chrome: boolean;
  tabs: Tab[];
};

export const WORKSPACES: Workspace[] = [
  {
    id: "journal",
    label: "Journal",
    filterBar: true,
    chrome: true,
    tabs: [
      { to: "/", label: "Overview", end: true },
      { to: "/calendar", label: "Calendar" },
      { to: "/edges", label: "Edges" },
      { to: "/trades", label: "Trades" },
      { to: "/models", label: "Models" },
      { to: "/ai", label: "AI Review" },
      { to: "/cross-check", label: "ATAS Cross-check" },
    ],
  },
  {
    id: "lab",
    label: "Lab",
    filterBar: false,
    chrome: true,
    tabs: [
      { to: "/strategies", label: "Strategies" },
      { to: "/interactions", label: "Interactions" },
      { to: "/backtests", label: "Backtests" },
      { to: "/research", label: "Research" },
      { to: "/drafts", label: "Drafts" },
    ],
  },
  {
    id: "charts",
    label: "Charts",
    filterBar: false,
    chrome: false,
    // Replay leads because switching workspace lands on tabs[0]. Live is a full
    // chart now, so this is a choice, not a necessity: Replay is the page you
    // can open at any hour, Live is only alive while the feed is.
    tabs: [
      { to: "/charts/replay", label: "Replay" },
      { to: "/charts/live", label: "Live" },
    ],
  },
];

/** The workspace whose tabs contain `pathname`.
 *
 *  Derived from the URL (not stored) so deep links, bookmarks, and back/forward
 *  all land in the right mode. Defaults to the first workspace for unknown
 *  paths. */
export function workspaceForPath(pathname: string): Workspace {
  for (const ws of WORKSPACES) {
    for (const tab of ws.tabs) {
      if (tab.to === "/") {
        if (pathname === "/") return ws;
      } else if (pathname === tab.to || pathname.startsWith(tab.to + "/")) {
        return ws;
      }
    }
  }
  return WORKSPACES[0];
}

/** The Charts workspace, which the chart pages need by name for their own
 *  Replay|Live switch. Non-null by construction — it is in the list above. */
export const CHARTS = WORKSPACES.find((w) => w.id === "charts")!;
