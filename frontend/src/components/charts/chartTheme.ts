import { palette } from "../../theme";

export const axisProps = {
  stroke: palette.muted,
  tick: { fill: palette.muted, fontSize: 11 },
  tickLine: false,
};

export const gridProps = {
  stroke: palette.grid,
  strokeDasharray: "3 3",
  vertical: false,
};

export const tooltipStyle = {
  contentStyle: {
    background: "#1a1d27",
    border: `1px solid ${palette.grid}`,
    borderRadius: 8,
    color: palette.text,
    fontSize: 12,
  },
  labelStyle: { color: palette.muted },
  cursor: { fill: "rgba(255,255,255,0.04)" },
};
