import type { Tone } from "../theme";

export interface Card {
  label: string;
  value: string;
  tone?: Tone;
  sub?: string;
  hero?: boolean;
}

export function KpiCard({ label, value, tone = "neutral", sub, hero }: Card) {
  return (
    <div className={`kpi-card${hero ? " kpi-hero" : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${tone}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}
