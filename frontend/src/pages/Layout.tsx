import { Suspense, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Sidebar } from "../components/Sidebar";
import { FilterBar } from "../components/FilterBar";
import { useImportFeed } from "../hooks/useBacktests";

// The app is really two products sharing one shell: a retrospective Journal
// (scoped by the FilterBar) and a prospective Lab (market-data research that
// ignores the FilterBar). Group the tabs by workspace so the two modes stop
// interleaving, and only show the FilterBar where it actually drives the page.
type Tab = { to: string; label: string; end?: boolean };
type Workspace = { id: string; label: string; filterBar: boolean; tabs: Tab[] };

const WORKSPACES: Workspace[] = [
  {
    id: "journal",
    label: "Journal",
    filterBar: true,
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
    tabs: [
      { to: "/strategies", label: "Strategies" },
      { to: "/interactions", label: "Interactions" },
      { to: "/backtests", label: "Backtests" },
    ],
  },
];

// Which workspace owns a given path? Derived from the URL (not stored) so deep
// links, bookmarks, and back/forward all land in the right mode. Defaults to
// the first workspace for unknown paths.
function workspaceForPath(pathname: string): Workspace {
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

export function Layout() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  // Poll the auto-import watcher from the shell so any page refreshes when a
  // new export lands, not just the Backtests tab.
  useImportFeed();
  // The data/timezone sidebar is hidden by default to maximise content width;
  // toggle it open with the ☰ button in the tab bar.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const active = workspaceForPath(pathname);
  return (
    <div className="app-shell">
      {sidebarOpen && <Sidebar />}
      <main className="app-main">
        <div className="app-topbar">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarOpen((o) => !o)}
            title={sidebarOpen ? "Hide the data panel" : "Show the data panel"}
            aria-label={sidebarOpen ? "Hide the data panel" : "Show the data panel"}
          >
            ☰
          </button>
          <div className="workspace-switch" role="tablist" aria-label="Workspace">
            {WORKSPACES.map((ws) => (
              <button
                key={ws.id}
                type="button"
                role="tab"
                aria-selected={ws.id === active.id}
                className={ws.id === active.id ? "active" : ""}
                // Switching workspace lands on its first tab; keep the current
                // querystring so Journal's FilterBar scope survives the hop.
                onClick={() => navigate({ pathname: ws.tabs[0].to, search })}
              >
                {ws.label}
              </button>
            ))}
          </div>
        </div>
        <nav className="tabs">
          {active.tabs.map((t) => (
            <NavLink key={t.to} to={{ pathname: t.to, search }} end={t.end}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        {active.filterBar && <FilterBar />}
        <Suspense fallback={<div className="page-fallback" />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
