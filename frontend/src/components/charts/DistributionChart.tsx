import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { palette } from "../../theme";
import { axisProps, gridProps, tooltipStyle } from "./chartTheme";
import { fmt } from "../../lib/format";

// Bin the raw net_pnl values client-side (Recharts has no histogram), matching
// charts.distribution_fig's 30 bins.
function bin(values: number[], bins = 30) {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [{ center: min, count: values.length }];
  const width = (max - min) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    let idx = Math.floor((v - min) / width);
    if (idx >= bins) idx = bins - 1;
    counts[idx]++;
  }
  return counts.map((count, i) => ({
    center: min + width * (i + 0.5),
    count,
  }));
}

export function DistributionChart({ values }: { values: number[] }) {
  const rows = bin(values);
  return (
    <div className="panel">
      <div className="section-cap">Trade PnL distribution</div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis
            dataKey="center"
            {...axisProps}
            tickFormatter={(v) => fmt(v)}
          />
          <YAxis {...axisProps} width={40} allowDecimals={false} />
          <Tooltip
            {...tooltipStyle}
            labelFormatter={(v: number) => fmt(v)}
            formatter={(v: number) => [v, "trades"]}
          />
          <Bar dataKey="count" isAnimationActive={false}>
            {rows.map((d, i) => (
              <Cell key={i} fill={d.center >= 0 ? palette.accent : palette.red} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
