import { useFilters } from "../hooks/useFilters";
import {
  useDailyPnl,
  useDistribution,
  useEquityCurve,
  useMetrics,
} from "../hooks/useOverview";
import { KpiGrid } from "../components/KpiGrid";
import { EquityCurveChart } from "../components/charts/EquityCurveChart";
import { DailyPnlChart } from "../components/charts/DailyPnlChart";
import { DistributionChart } from "../components/charts/DistributionChart";
import { fmt, fmtPct, numValue } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "../components/KpiCard";

export function Overview() {
  const { scope } = useFilters();
  const { data: m, isLoading } = useMetrics(scope);
  const { data: eq } = useEquityCurve(scope);
  const { data: daily } = useDailyPnl(scope);
  const { data: dist } = useDistribution(scope);

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!m || m.trades === 0)
    return <div className="notice">No trades match the current filters.</div>;

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
      <div className="grid-2">
        {daily && <DailyPnlChart data={daily} />}
        {dist && <DistributionChart values={dist.values} />}
      </div>
    </div>
  );
}
