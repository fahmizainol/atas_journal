import { Suspense, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "../components/Sidebar";
import { FilterBar } from "../components/FilterBar";
import { useImportFeed } from "../hooks/useBacktests";

const TABS = [
  { to: "/", label: "Overview", end: true },
  { to: "/calendar", label: "Calendar", end: false },
  { to: "/edges", label: "Edges", end: false },
  { to: "/trades", label: "Trades", end: false },
  { to: "/models", label: "Models", end: false },
  { to: "/backtests", label: "Backtests", end: false },
  { to: "/strategies", label: "Strategies", end: false },
  { to: "/ai", label: "AI Review", end: false },
  { to: "/cross-check", label: "ATAS Cross-check", end: false },
];

export function Layout() {
  const { search } = useLocation();
  // Poll the auto-import watcher from the shell so any page refreshes when a
  // new export lands, not just the Backtests tab.
  useImportFeed();
  // The data/timezone sidebar is hidden by default to maximise content width;
  // toggle it open with the ☰ button in the tab bar.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  return (
    <div className="app-shell">
      {sidebarOpen && <Sidebar />}
      <main className="app-main">
        <nav className="tabs">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarOpen((o) => !o)}
            title={sidebarOpen ? "Hide the data panel" : "Show the data panel"}
            aria-label={sidebarOpen ? "Hide the data panel" : "Show the data panel"}
          >
            ☰
          </button>
          {TABS.map((t) => (
            <NavLink key={t.to} to={{ pathname: t.to, search }} end={t.end}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        <FilterBar />
        <Suspense fallback={<div className="page-fallback" />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
