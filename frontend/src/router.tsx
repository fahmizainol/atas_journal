import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "./pages/Layout";

// Every page is lazy so a route's JS (and its chart libraries) loads on first
// visit instead of in the entry bundle. Pages are named exports, hence the map.
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const Calendar = lazy(() => import("./pages/Calendar").then((m) => ({ default: m.Calendar })));
const Edges = lazy(() => import("./pages/Edges").then((m) => ({ default: m.Edges })));
const Trades = lazy(() => import("./pages/Trades").then((m) => ({ default: m.Trades })));
const Models = lazy(() => import("./pages/Models").then((m) => ({ default: m.Models })));
const Backtests = lazy(() => import("./pages/Backtests").then((m) => ({ default: m.Backtests })));
const Strategies = lazy(() => import("./pages/Strategies").then((m) => ({ default: m.Strategies })));
const StrategyDetail = lazy(() =>
  import("./pages/StrategyDetail").then((m) => ({ default: m.StrategyDetail })),
);
const AiReview = lazy(() => import("./pages/AiReview").then((m) => ({ default: m.AiReview })));
const CrossCheck = lazy(() => import("./pages/CrossCheck").then((m) => ({ default: m.CrossCheck })));

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "calendar", element: <Calendar /> },
      { path: "calendar/:date", element: <Calendar /> },
      { path: "edges", element: <Edges /> },
      { path: "trades", element: <Trades /> },
      { path: "trades/:tradeNo", element: <Trades /> },
      { path: "models", element: <Models /> },
      { path: "backtests", element: <Backtests /> },
      { path: "strategies", element: <Strategies /> },
      { path: "strategies/:slug", element: <StrategyDetail /> },
      // The Auto-Backtest Demo grew into Strategies; keep old links working.
      { path: "auto-backtest", element: <Navigate to="/strategies" replace /> },
      { path: "ai", element: <AiReview /> },
      { path: "cross-check", element: <CrossCheck /> },
    ],
  },
]);
