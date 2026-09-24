// The app is really four products sharing one shell: a retrospective Journal
// (scoped by the FilterBar), a prospective Lab (market-data research that
// ignores the FilterBar), Charts — the market itself, played back or live — and
// Recall, which hands your own reviewed trades back to you on a schedule.
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

export type Tab = {
  to: string;
  label: string;
  end?: boolean;
  /** Overrides the workspace's `filterBar` for this tab alone.
   *
   *  The Journal is scoped by the FilterBar because every tab in it reads the
   *  ATAS journal — except Accounts, which reads the replay store and is scoped
   *  by which account you are looking at. Leaving the bar up there would put a
   *  control above the page that changes nothing on it, which is worse than not
   *  having one. */
  filterBar?: boolean;
};

export type Workspace = {
  id: string;
  label: string;
  filterBar: boolean;
  /** Does the shell draw the topbar, tab strip and data sidebar for this
   *  workspace? False means the page draws its own.
   *
   *  Charts and Recall opt out: the tape is the product on both, and 215px of
   *  topbar/tabs/padding above it was a fifth of a 1080p viewport spent on
   *  navigation you look at once. It is a property of the workspace rather than
   *  a page-level opt-out because the shell has to know before it renders, not
   *  after. The shell stamps a `chromeless` class for these, which is what the
   *  stylesheet keys the zero-padding, full-height rules off. */
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
      // The reviewed trades top-down. Beside Trades because they are one set
      // of rows read two ways — the review cut is shared URL state between
      // them.
      { to: "/review", label: "Review" },
      { to: "/models", label: "Models" },
      // The practice accounts, and what each one's reps came to. In the Journal
      // because it is a retrospective — the same question the Calendar asks, of
      // the ledger you practise on rather than the one you traded.
      { to: "/accounts", label: "Accounts", filterBar: false },
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
    // Backtest sits last because it is the narrowest of the three: one model,
    // one rep, and a day you are not shown. Replay is the page you open to
    // trade a session; this is the one you open to drill something specific.
    tabs: [
      { to: "/charts/replay", label: "Replay" },
      { to: "/charts/live", label: "Live" },
      { to: "/charts/backtest", label: "Backtest" },
    ],
  },
  {
    id: "recall",
    label: "Recall",
    filterBar: false,
    // Chrome-less for the same reason Charts is, and it is the same product
    // argument: a rep is one chart you are asked to read, so the tape is the
    // page. It is not a Charts tab, though — Replay, Live and Backtest are the
    // market, played back or live, and this is your own history handed back to
    // you on a schedule. Different question, different workspace.
    chrome: false,
    tabs: [{ to: "/recall", label: "Recall" }],
  },
];

/** The tab `pathname` belongs to, and the workspace holding it.
 *
 *  Derived from the URL (not stored) so deep links, bookmarks, and back/forward
 *  all land in the right mode. Null for a path no tab claims — a redirect, or a
 *  page reached only from another page.
 *
 *  Matching is by prefix, so a tab owns its detail routes: /trades/412 is
 *  Trades, and /charts/replay/paper is Replay (paper and the live-money ledger
 *  are the same activity on different accounts). */
export function tabForPath(pathname: string): { ws: Workspace; tab: Tab } | null {
  for (const ws of WORKSPACES) {
    for (const tab of ws.tabs) {
      if (tab.to === "/") {
        if (pathname === "/") return { ws, tab };
      } else if (pathname === tab.to || pathname.startsWith(tab.to + "/")) {
        return { ws, tab };
      }
    }
  }
  return null;
}

/** The workspace whose tabs contain `pathname`. Defaults to the first workspace
 *  for unknown paths, so the shell always has chrome to draw. */
export function workspaceForPath(pathname: string): Workspace {
  return tabForPath(pathname)?.ws ?? WORKSPACES[0];
}

/** Does this path get the FilterBar — the tab's answer if it has one, else the
 *  workspace's. Derived here rather than in the shell so the one rule lives with
 *  the list it reads. */
export function filterBarForPath(pathname: string): boolean {
  const hit = tabForPath(pathname);
  return hit?.tab.filterBar ?? hit?.ws.filterBar ?? WORKSPACES[0].filterBar;
}

/** The Charts workspace, which the chart pages need by name for their own
 *  Replay|Live switch. Non-null by construction — it is in the list above. */
export const CHARTS = WORKSPACES.find((w) => w.id === "charts")!;
