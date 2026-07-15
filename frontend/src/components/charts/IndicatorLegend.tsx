export type IndicatorKey =
  | "vwapGlobex"
  | "vwapNy"
  | "atr"
  | "levels"
  | "volumeProfile"
  | "developingProfileGlobex"
  | "developingProfileNy"
  | "touches"
  | "va_snaps";

export interface LegendItem {
  key: IndicatorKey;
  label: string;
  color: string;
}

function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
      {!open && <line x1="2" y1="2" x2="22" y2="22" />}
    </svg>
  );
}

// TV-style on-chart indicator list: one row per indicator, click to hide/show.
export function IndicatorLegend({
  items,
  visibility,
  onToggle,
}: {
  items: LegendItem[];
  visibility: Record<IndicatorKey, boolean>;
  onToggle: (key: IndicatorKey) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="chart-legend">
      {items.map((it) => {
        const on = visibility[it.key];
        return (
          <button
            key={it.key}
            className={`chart-legend-row${on ? "" : " off"}`}
            onClick={() => onToggle(it.key)}
            title={on ? `Hide ${it.label}` : `Show ${it.label}`}
          >
            <span className="chart-legend-swatch" style={{ background: it.color }} />
            <span>{it.label}</span>
            <EyeIcon open={on} />
          </button>
        );
      })}
    </div>
  );
}
