import { Suspense, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Sidebar } from "../components/Sidebar";
import { FilterBar } from "../components/FilterBar";
import { useImportFeed } from "../hooks/useBacktests";
import { WORKSPACES, workspaceForPath } from "../lib/workspaces";

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
    // The workspace id rides on the shell so mobile styling can target one
    // product at a time — the Lab is tuned for phones; the Journal isn't (yet).
    <div className={`app-shell ws-${active.id}`}>
      {/* The data sidebar is import/timezone plumbing over ATAS exports — a
          chart page reads the tick cache and has no use for it, and both of them
          hardcode their timezone. Gated on `chrome` rather than left mounted so
          an open drawer doesn't follow you onto a chart. */}
      {active.chrome && sidebarOpen && <Sidebar />}
      {active.chrome && sidebarOpen && (
        // On phones the drawer overlays the content; this scrim closes it on a
        // tap outside. Hidden on desktop, where the sidebar sits in the flow.
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close the data panel"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <main className="app-main">
        {active.chrome && (
          <>
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
          </>
        )}
        {active.filterBar && <FilterBar />}
        <Suspense fallback={<div className="page-fallback" />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
