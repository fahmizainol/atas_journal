import { createBrowserRouter } from "react-router-dom";
import { Layout } from "./pages/Layout";
import { Overview } from "./pages/Overview";
import { Calendar } from "./pages/Calendar";
import { Edges } from "./pages/Edges";
import { Trades } from "./pages/Trades";
import { AiReview } from "./pages/AiReview";
import { CrossCheck } from "./pages/CrossCheck";

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
      { path: "ai", element: <AiReview /> },
      { path: "cross-check", element: <CrossCheck /> },
    ],
  },
]);
