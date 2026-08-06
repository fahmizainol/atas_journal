// Renders one idealised teaching chart from an ExChart spec as static SVG. Pure
// geometry: candles, horizontal reference lines, shaded zones, VWAP polylines,
// and glyph/text markers, mapped from price space into the viewBox. No data
// fetch, no lightweight-charts — these are hand-authored diagrams that show a
// strategy's RULE, so they must render identically every time. See
// lib/strategyExplainers.ts for the specs.
import { palette } from "../../theme";
import type { ExChart } from "../../lib/strategyExplainers";

export function StrategyExplainerChart({ spec, ariaLabel }: { spec: ExChart; ariaLabel?: string }) {
  const [W, H] = spec.aspect;
  const pad = { l: 10, r: 12, t: 14, b: 16, ...spec.pad };
  const x0 = pad.l;
  const x1 = W - pad.r;
  const y0 = pad.t;
  const y1 = H - pad.b;
  const n = spec.candles.length;

  const px = (i: number) => x0 + ((i + 0.5) * (x1 - x0)) / n;
  const py = (p: number) => y1 - ((p - spec.pMin) * (y1 - y0)) / (spec.pMax - spec.pMin);
  const cw = Math.min(16, (0.56 * (x1 - x0)) / n);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="se-svg" role="img" aria-label={ariaLabel}>
      {/* plot baseline */}
      <line x1={x0} y1={y1} x2={x1} y2={y1} stroke={palette.grid} strokeWidth={1} opacity={0.6} />

      {/* shaded zones — behind everything */}
      {(spec.zones ?? []).map((z, k) => {
        const zx0 = px(z.i0) - cw;
        const zx1 = px(z.i1) + cw;
        return (
          <g key={`z${k}`}>
            <rect
              x={zx0}
              y={py(z.pTop)}
              width={zx1 - zx0}
              height={py(z.pBot) - py(z.pTop)}
              fill={z.color}
              opacity={z.opacity ?? 0.14}
              rx={3}
            />
            {z.label && (
              <text
                x={(zx0 + zx1) / 2}
                y={py(z.pBot) + 12}
                fill={z.labelColor ?? palette.gold}
                fontSize={10}
                fontWeight={600}
                textAnchor="middle"
              >
                {z.label}
              </text>
            )}
          </g>
        );
      })}

      {/* horizontal reference lines */}
      {(spec.hlines ?? []).map((h, k) => {
        // Right labels sit out in the reserved right padding (axis-style) so
        // they never collide with candles that reach the plot's right edge;
        // left labels tuck just inside the left edge.
        const anchor = h.side === "left" ? "start" : "start";
        const lx = h.side === "left" ? x0 + 4 : x1 + 6;
        return (
          <g key={`h${k}`}>
            <line
              x1={x0}
              y1={py(h.p)}
              x2={x1}
              y2={py(h.p)}
              stroke={h.color}
              strokeWidth={h.width ?? 1.4}
              strokeDasharray={h.dash}
              opacity={h.opacity ?? 1}
            />
            {h.label && (
              <text
                x={lx}
                y={py(h.p) - 4}
                fill={h.color}
                fontSize={10.5}
                fontWeight={650}
                textAnchor={anchor}
                letterSpacing="0.03em"
              >
                {h.label}
              </text>
            )}
          </g>
        );
      })}

      {/* VWAP polylines */}
      {(spec.vwaps ?? []).map((v, k) => (
        <polyline
          key={`v${k}`}
          points={v.pts.map((p, i) => `${px(i)},${py(p)}`).join(" ")}
          fill="none"
          stroke={v.color}
          strokeWidth={v.width ?? 1.6}
          strokeDasharray={v.dash}
          opacity={v.opacity ?? 1}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}

      {/* candles */}
      {spec.candles.map((c, i) => {
        const up = c.c >= c.o;
        const col = c.col ?? (up ? palette.green : palette.red);
        const cx = px(i);
        const yTop = py(Math.max(c.o, c.c));
        const yBot = py(Math.min(c.o, c.c));
        return (
          <g key={`c${i}`} opacity={c.dim ? 0.5 : 1}>
            <line x1={cx} y1={py(c.h)} x2={cx} y2={py(c.l)} stroke={col} strokeWidth={1.3} />
            <rect x={cx - cw / 2} y={yTop} width={cw} height={Math.max(1.4, yBot - yTop)} fill={col} rx={1} />
            {c.ring && (
              <rect
                x={cx - cw / 2 - 3}
                y={py(c.h) - 3}
                width={cw + 6}
                height={py(c.l) - py(c.h) + 6}
                fill="none"
                stroke={c.ring}
                strokeWidth={1.4}
                rx={3}
                strokeDasharray="3 2"
              />
            )}
          </g>
        );
      })}

      {/* markers: glyph and/or label */}
      {(spec.markers ?? []).map((m, k) => {
        const cx = px(m.i);
        const cy = py(m.p);
        const lx = cx + (m.labelDx ?? 0);
        const ly = cy + (m.labelDy ?? 0);
        return (
          <g key={`m${k}`}>
            {m.leader && m.label && (
              <line
                x1={cx}
                y1={cy}
                x2={lx}
                y2={ly + ((m.labelDy ?? 0) < 0 ? 4 : -4)}
                stroke={m.color}
                strokeWidth={1}
                opacity={0.55}
              />
            )}
            {m.glyph && (
              <text
                x={cx + (m.gdx ?? 0)}
                y={cy + (m.dy ?? 0)}
                fill={m.color}
                fontSize={m.size ?? 15}
                fontWeight={800}
                textAnchor="middle"
                dominantBaseline="middle"
              >
                {m.glyph}
              </text>
            )}
            {m.label && (
              <text x={lx} y={ly} fill={m.color} fontSize={10.5} fontWeight={650} textAnchor={m.anchor ?? "middle"}>
                {m.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
