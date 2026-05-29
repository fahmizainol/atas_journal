import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { palette } from "../../theme";
import type { EquityPoint } from "../../lib/types";
import { axisProps, gridProps, tooltipStyle } from "./chartTheme";
import { fmt } from "../../lib/format";

// Equity curve (top) + drawdown (bottom), both vs trade #. Mirrors
// charts.equity_curve_fig's stacked subplots.
export function EquityCurveChart({ data }: { data: EquityPoint[] }) {
  return (
    <div className="panel">
      <div className="section-cap">Equity curve</div>
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="trade_no" {...axisProps} />
          <YAxis {...axisProps} width={64} tickFormatter={(v) => fmt(v)} />
          <Tooltip {...tooltipStyle} formatter={(v: number) => fmt(v)} />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={palette.accent}
            strokeWidth={2.5}
            fill="rgba(108,92,231,0.16)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
      <div className="section-cap" style={{ marginTop: 4 }}>
        Drawdown
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <AreaChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="trade_no" {...axisProps} />
          <YAxis {...axisProps} width={64} tickFormatter={(v) => fmt(v)} />
          <Tooltip {...tooltipStyle} formatter={(v: number) => fmt(v)} />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke={palette.red}
            strokeWidth={1}
            fill="rgba(245,69,95,0.18)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
