import { useFilters } from "../hooks/useFilters";
import {
  useDailyPnl,
  useDistribution,
  useEquityCurve,
  useMetrics,
  useSummaryExtras,
} from "../hooks/useOverview";
import { KpiGrid } from "../components/KpiGrid";
import { EquityCurveChart } from "../components/charts/EquityCurveChart";
import { DailyPnlChart } from "../components/charts/DailyPnlChart";
import { DistributionChart } from "../components/charts/DistributionChart";
import { fmt, fmtInt, fmtPct, fmtTime, numValue } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "../components/KpiCard";
import type { SummaryExtras } from "../lib/types";

function fmtWindow(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const sDate = start.slice(0, 10);
  const eDate = end.slice(0, 10);
  if (sDate === eDate) return `${fmtTime(start)}–${fmtTime(end)}`;
  const d = (iso: string) =>
    new Date(iso).toLocaleDateString("en-US", { day: "2-digit", month: "short" });
  return `${d(start)} – ${d(end)}`;
}

function extrasCards(x: SummaryExtras, avgHoldS: number | null): Card[] {
  return [
    {
      label: "Long",
      value: fmt(x.long.net_pnl),
      tone: toneOf(x.long.net_pnl),
      sub: `${x.long.trades} trades · ${fmtPct(x.long.win_rate)} win`,
    },
    {
      label: "Short",
      value: fmt(x.short.net_pnl),
      tone: toneOf(x.short.net_pnl),
      sub: `${x.short.trades} trades · ${fmtPct(x.short.win_rate)} win`,
    },
    {
      label: "Total contracts",
      value: fmtInt(x.total_contracts),
    },
    {
      label: "Avg hold",
      value: avgHoldS == null || !Number.isFinite(avgHoldS)
        ? "—"
        : `${(avgHoldS / 60).toFixed(1)}m`,
    },
    {
      label: "Avg MFE / MAE",
      value: `${fmt(x.avg_mfe_usd)} / ${fmt(x.avg_mae_usd)}`,
    },
    {
      label: "Avg exit efficiency",
      value: x.avg_exit_efficiency == null ? "—" : fmtPct(x.avg_exit_efficiency),
    },
    {
      label: "Avg ATR (hold)",
      value:
        x.avg_atr_pts == null
          ? "—"
          : `${x.avg_atr_pts.toFixed(2)} pts · ${fmt(x.avg_atr_usd)}`,
    },
    {
      label: "Trading window",
      value: fmtWindow(x.window_start, x.window_end),
    },
  ];
}

export function Overview() {
  const { scope, setMode, setIncludeArchived } = useFilters();
  const { data: m, isLoading } = useMetrics(scope);
  const { data: eq } = useEquityCurve(scope);
  const { data: daily } = useDailyPnl(scope);
  const { data: dist } = useDistribution(scope);
  const { data: extras } = useSummaryExtras(scope);

  if (isLoading) return <div className="notice">Loading…</div>;
  // Everything before the model cutover is archived, and the default scope is
  // live-only. Without this, a journal made entirely of pre-cutover replays
  // opens on a blank Overview that reads as data loss.
  if (!m || m.trades === 0)
    return (
      <div className="notice">
        <div>No trades match the current filters.</div>
        {(!scope.includeArchived || scope.mode !== "all") && (
          <div className="section-cap" style={{ marginTop: 8 }}>
            Sessions from before the model cutover are archived, and the Session
            filter defaults to <strong>Live</strong>. Nothing was deleted —{" "}
            <button type="button" onClick={() => { setMode("all"); setIncludeArchived(true); }}>
              show every session, archived included
            </button>
            .
          </div>
        )}
      </div>
    );

  const hero: Card[] = [
    {
      label: "Net PnL",
      value: fmt(m.net_pnl),
      tone: toneOf(numValue(m.net_pnl)),
      hero: true,
      sub: `${m.trades} trades · ${m.view} view`,
    },
    { label: "Win rate", value: fmtPct(m.win_rate), sub: `${m.wins}W / ${m.losses}L` },
    { label: "Profit factor", value: fmt(m.profit_factor, false) },
    {
      label: "Expectancy",
      value: fmt(m.expectancy),
      tone: toneOf(numValue(m.expectancy)),
      sub: "per trade",
    },
  ];

  const secondary: Card[] = [
    { label: "Avg win", value: fmt(m.avg_win), tone: "pos" },
    { label: "Avg loss", value: fmt(m.avg_loss), tone: "neg" },
    { label: "Best trade", value: fmt(m.best_trade), tone: "pos" },
    { label: "Worst trade", value: fmt(m.worst_trade), tone: "neg" },
    { label: "Max drawdown", value: fmt(m.max_drawdown), tone: toneOf(numValue(m.max_drawdown)) },
    { label: "Sharpe", value: fmt(m.sharpe, false) },
    { label: "Sortino", value: fmt(m.sortino, false) },
    { label: "Recovery factor", value: fmt(m.recovery_factor, false) },
    {
      label: "Max consec W/L",
      value: `${m.max_consecutive_wins} / ${m.max_consecutive_losses}`,
    },
  ];

  return (
    <div>
      <KpiGrid cards={hero} template="1.5fr 1fr 1fr 1fr" />
      {eq && eq.length > 0 && <EquityCurveChart data={eq} />}
      <KpiGrid cards={secondary} template="repeat(4, 1fr)" />
      {extras && (
        <KpiGrid
          cards={extrasCards(extras, numValue(m.avg_trade_length_s))}
          template="repeat(4, 1fr)"
        />
      )}
      <div className="grid-2">
        {daily && <DailyPnlChart data={daily} />}
        {dist && <DistributionChart values={dist.values} />}
      </div>
    </div>
  );
}
