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
import type { DailyPnlPoint } from "../../lib/types";
import { axisProps, gridProps, tooltipStyle } from "./chartTheme";
import { fmt } from "../../lib/format";

export function DailyPnlChart({ data }: { data: DailyPnlPoint[] }) {
  const rows = data.map((d) => ({ ...d, label: d.date.slice(0, 10) }));
  return (
    <div className="panel">
      <div className="section-cap">Daily net PnL</div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="label" {...axisProps} />
          <YAxis {...axisProps} width={64} tickFormatter={(v) => fmt(v)} />
          <Tooltip {...tooltipStyle} formatter={(v: number) => fmt(v)} />
          <Bar dataKey="net_pnl" isAnimationActive={false}>
            {rows.map((d, i) => (
              <Cell key={i} fill={d.net_pnl >= 0 ? palette.green : palette.red} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
