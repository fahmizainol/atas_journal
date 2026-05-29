import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "../components/Sidebar";
import { FilterBar } from "../components/FilterBar";

const TABS = [
  { to: "/", label: "Overview", end: true },
  { to: "/calendar", label: "Calendar", end: false },
  { to: "/edges", label: "Edges", end: false },
  { to: "/trades", label: "Trades", end: false },
  { to: "/ai", label: "AI Review", end: false },
  { to: "/cross-check", label: "ATAS Cross-check", end: false },
];

export function Layout() {
  const { search } = useLocation();
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <nav className="tabs">
          {TABS.map((t) => (
            <NavLink key={t.to} to={{ pathname: t.to, search }} end={t.end}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        <FilterBar />
        <Outlet />
      </main>
    </div>
  );
}
