import { lazy } from "react";
import { createBrowserRouter, Navigate, redirect, useParams } from "react-router-dom";
import { Layout } from "./pages/Layout";
import { COARSE_POINTER } from "./lib/pointer";

// Every page is lazy so a route's JS (and its chart libraries) loads on first
// visit instead of in the entry bundle. Pages are named exports, hence the map.
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const Calendar = lazy(() => import("./pages/Calendar").then((m) => ({ default: m.Calendar })));
const Edges = lazy(() => import("./pages/Edges").then((m) => ({ default: m.Edges })));
const Interactions = lazy(() =>
  import("./pages/Interactions").then((m) => ({ default: m.Interactions })),
);
const Trades = lazy(() => import("./pages/Trades").then((m) => ({ default: m.Trades })));
const Review = lazy(() => import("./pages/Review").then((m) => ({ default: m.Review })));
const Models = lazy(() => import("./pages/Models").then((m) => ({ default: m.Models })));
const Backtests = lazy(() => import("./pages/Backtests").then((m) => ({ default: m.Backtests })));
const Strategies = lazy(() => import("./pages/Strategies").then((m) => ({ default: m.Strategies })));
const StrategyDetail = lazy(() =>
  import("./pages/StrategyDetail").then((m) => ({ default: m.StrategyDetail })),
);
const AiReview = lazy(() => import("./pages/AiReview").then((m) => ({ default: m.AiReview })));
const Research = lazy(() => import("./pages/Research").then((m) => ({ default: m.Research })));
const Drafts = lazy(() => import("./pages/Drafts").then((m) => ({ default: m.Drafts })));
const Recall = lazy(() => import("./pages/Recall").then((m) => ({ default: m.Recall })));
const DraftDetail = lazy(() =>
  import("./pages/DraftDetail").then((m) => ({ default: m.DraftDetail })),
);
const CrossCheck = lazy(() => import("./pages/CrossCheck").then((m) => ({ default: m.CrossCheck })));
const Accounts = lazy(() => import("./pages/Accounts").then((m) => ({ default: m.Accounts })));
const AccountDetail = lazy(() =>
  import("./pages/AccountDetail").then((m) => ({ default: m.AccountDetail })),
);
const Simulator = lazy(() => import("./pages/Simulator").then((m) => ({ default: m.Simulator })));
const ReplayHistory = lazy(() =>
  import("./pages/ReplayHistory").then((m) => ({ default: m.ReplayHistory })),
);
const LiveChart = lazy(() => import("./pages/LiveChart").then((m) => ({ default: m.LiveChart })));

// A touch device that opens the app lands on Recall rather than Overview.
// Overview is a wall of desktop-width statistics; Recall is a deck of cards,
// which is the one thing here worth doing one-handed — so on a phone or tablet
// it is the reason you opened the app at all.
//
// Keyed off the pointer and NOT off viewport width, for the reason pointer.ts
// spells out: a tablet in landscape is wider than plenty of laptops, and any
// width cutoff that catches it in portrait drops it the moment it is turned
// sideways.
//
// `launchPending` is module scope, so it is armed exactly once per document
// load — that is what makes this a *launch* redirect rather than a permanent
// one. A later client-side navigation to "/" (tapping the Journal workspace
// button, whose first tab is Overview) re-runs the loader but not the module,
// finds the flag spent, and renders Overview.
//
// It is spent in a loader rather than in a component because a component
// cannot reliably spend it: rendering <Navigate> unmounts the component in the
// same commit, and React drops the pending passive effect of a fiber deleted
// before the effect flush — so an effect-based flag survives the very redirect
// it fired, and the next visit to "/" bounces too. A loader runs once per
// navigation, before the element exists, and StrictMode does not double-invoke
// it.
//
// The pathname test is what keeps a deep link honest. Module scope runs before
// the first navigation, so this is the URL the document opened at: arming the
// flag only for a launch at the root means someone who opened /trades and
// later taps Journal gets Overview, not a redirect fired hours after the
// launch it was named for.
let launchPending = COARSE_POINTER && window.location.pathname === "/";

function launchRedirect() {
  if (!launchPending) return null;
  launchPending = false;
  return redirect("/recall");
}

/** The replay page on a named account. A wrapper only because the key has to be
 *  the account id and a route element cannot read its own params. */
function ReplayOnAccount() {
  const { accountId } = useParams();
  const id = accountId ?? "funded";
  return <Simulator key={id} mode="replay" accountId={id} />;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, loader: launchRedirect, element: <Overview /> },
      { path: "calendar", element: <Calendar /> },
      { path: "calendar/:date", element: <Calendar /> },
      { path: "edges", element: <Edges /> },
      { path: "interactions", element: <Interactions /> },
      { path: "trades", element: <Trades /> },
      { path: "trades/:tradeNo", element: <Trades /> },
      // The reviewed trades, top-down — facets, the setup×discipline matrix,
      // and a group-by ledger over the same rows the Trades table shows. Its
      // cut is URL state shared with /trades.
      { path: "review", element: <Review /> },
      { path: "models", element: <Models /> },
      // The practice accounts. In the Journal because it is a retrospective —
      // the sittings are traded in Charts and read here.
      { path: "accounts", element: <Accounts /> },
      { path: "accounts/:accountId", element: <AccountDetail /> },
      { path: "backtests", element: <Backtests /> },
      { path: "strategies", element: <Strategies /> },
      { path: "strategies/:slug", element: <StrategyDetail /> },
      { path: "research", element: <Research /> },
      { path: "research/:slug", element: <Research /> },
      { path: "drafts", element: <Drafts /> },
      { path: "drafts/:slug", element: <DraftDetail /> },
      { path: "recall", element: <Recall /> },
      { path: "charts/live", element: <LiveChart /> },
      // Keyed by mode: both routes render the same component at the same tree
      // position, and without the key React would *reconcile* a tab switch —
      // same engine, same order log, same blotter — so trades placed after
      // switching to Backtest were booked into the still-armed replay attempt.
      // The key makes a mode switch a real unmount/mount; each mode parks its
      // own bookmark (lib/replayResume) and resumes it on the way back.
      { path: "charts/replay", element: <Simulator key="replay" /> },
      { path: "charts/replay/history", element: <ReplayHistory /> },
      // Paper: the same page on the other account. A route rather than a piece
      // of page state for the same reason Backtest is one — the account is fixed
      // for the whole of a sitting, and the key below is what makes the switch a
      // real unmount rather than a reconcile that would leave a still-armed
      // recorder pointing at the other ledger.
      //
      // *Under* `charts/replay` rather than beside it, because it is the Replay
      // tab: `workspaceForPath` and the tab strip both match by prefix, so this
      // keeps the chart chrome and the highlight without a fourth tab appearing
      // for something the account chip already switches.
      { path: "charts/replay/paper", element: <Simulator key="paper" mode="paper" /> },
      // Every other account. The two above keep the paths they have always had
      // — every link, bookmark and browser check still resolves — and anything
      // made from a template lands here, under an `/a/` segment so an account
      // called "history" cannot shadow the route above it.
      //
      // The key is the account id for the same reason the two above are keyed:
      // a switch has to be a real unmount. Reconciling would leave a recorder
      // armed on one ledger writing into another, and an attempt's account can
      // never be moved afterwards.
      { path: "charts/replay/a/:accountId", element: <ReplayOnAccount /> },
      // Backtest mode is the same page with a different clock and a bound
      // model — the same argument that keeps Replay and Live adjacent. It is a
      // route rather than a switch on the Replay page because the two are
      // different intents on the same tape, and because the mode has to be
      // fixed for the whole of a sitting.
      { path: "charts/backtest", element: <Simulator key="drill" mode="drill" /> },
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
