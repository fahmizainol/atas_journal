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

interface Row {
  trade_no: number;
  net_pnl: number;
  time: string;
}

export function PerTradeBarChart({ data }: { data: Row[] }) {
  const rows = data.map((d) => ({ ...d, label: `#${d.trade_no}` }));
  return (
    <div className="panel">
      <div className="section-cap">Per-trade net PnL</div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="label" {...axisProps} />
          <YAxis {...axisProps} width={64} tickFormatter={(v) => fmt(v)} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number) => fmt(v)}
            labelFormatter={(label: string, payload: any) =>
              payload?.[0] ? `${label} @ ${payload[0].payload.time}` : label
            }
          />
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
