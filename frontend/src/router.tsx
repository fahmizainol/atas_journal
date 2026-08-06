import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "./pages/Layout";

// Every page is lazy so a route's JS (and its chart libraries) loads on first
// visit instead of in the entry bundle. Pages are named exports, hence the map.
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const Calendar = lazy(() => import("./pages/Calendar").then((m) => ({ default: m.Calendar })));
const Edges = lazy(() => import("./pages/Edges").then((m) => ({ default: m.Edges })));
const Interactions = lazy(() =>
  import("./pages/Interactions").then((m) => ({ default: m.Interactions })),
);
const Trades = lazy(() => import("./pages/Trades").then((m) => ({ default: m.Trades })));
const Models = lazy(() => import("./pages/Models").then((m) => ({ default: m.Models })));
const Backtests = lazy(() => import("./pages/Backtests").then((m) => ({ default: m.Backtests })));
const Strategies = lazy(() => import("./pages/Strategies").then((m) => ({ default: m.Strategies })));
const StrategyDetail = lazy(() =>
  import("./pages/StrategyDetail").then((m) => ({ default: m.StrategyDetail })),
);
const AiReview = lazy(() => import("./pages/AiReview").then((m) => ({ default: m.AiReview })));
const Research = lazy(() => import("./pages/Research").then((m) => ({ default: m.Research })));
const Drafts = lazy(() => import("./pages/Drafts").then((m) => ({ default: m.Drafts })));
const DraftDetail = lazy(() =>
  import("./pages/DraftDetail").then((m) => ({ default: m.DraftDetail })),
);
const CrossCheck = lazy(() => import("./pages/CrossCheck").then((m) => ({ default: m.CrossCheck })));
const Simulator = lazy(() => import("./pages/Simulator").then((m) => ({ default: m.Simulator })));
const ReplayHistory = lazy(() =>
  import("./pages/ReplayHistory").then((m) => ({ default: m.ReplayHistory })),
);
const LiveChart = lazy(() => import("./pages/LiveChart").then((m) => ({ default: m.LiveChart })));

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "calendar", element: <Calendar /> },
      { path: "calendar/:date", element: <Calendar /> },
      { path: "edges", element: <Edges /> },
      { path: "interactions", element: <Interactions /> },
      { path: "trades", element: <Trades /> },
      { path: "trades/:tradeNo", element: <Trades /> },
      { path: "models", element: <Models /> },
      { path: "backtests", element: <Backtests /> },
      { path: "strategies", element: <Strategies /> },
      { path: "strategies/:slug", element: <StrategyDetail /> },
      { path: "research", element: <Research /> },
      { path: "research/:slug", element: <Research /> },
      { path: "drafts", element: <Drafts /> },
      { path: "drafts/:slug", element: <DraftDetail /> },
      { path: "charts/live", element: <LiveChart /> },
      { path: "charts/replay", element: <Simulator /> },
      { path: "charts/replay/history", element: <ReplayHistory /> },
      // The Auto-Backtest Demo grew into Strategies; keep old links working.
      { path: "auto-backtest", element: <Navigate to="/strategies" replace /> },
      // The Simulator moved out of the Lab into its own Charts workspace, where
      // it is the Replay half of one chart with two clocks. BOTH routes have to
      // redirect: workspaceForPath falls back to the first workspace, so a
      // surviving /simulator/history would render inside the Journal shell,
      // FilterBar and all.
      { path: "simulator", element: <Navigate to="/charts/replay" replace /> },
      {
        path: "simulator/history",
        element: <Navigate to="/charts/replay/history" replace />,
      },
      { path: "ai", element: <AiReview /> },
      { path: "cross-check", element: <CrossCheck /> },
    ],
  },
]);
