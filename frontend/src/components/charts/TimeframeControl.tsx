// The one timeframe selector shared by every chart section. Callers pass the
// option list: the minute set is uniform (MINUTE_TFS), and the leading bar
// option differs by context — the engine's native n-tick bar on Strategies, a
// fixed 500t on the Interactions/Drafts research benches, and none on the
// Journal's databento minute charts. Mapping a chosen key back to backend params
// (resolution vs bar_minutes/ticks_per_bar) stays with each caller; this is only
// the button row.

export type TfOption = { key: string; label: string };

// 1m / 3m / 5m / 15m — the standard minute ladder every section offers.
export const MINUTE_TFS: TfOption[] = [
  { key: "1m", label: "1m" },
  { key: "3m", label: "3m" },
  { key: "5m", label: "5m" },
  { key: "15m", label: "15m" },
];

export function TimeframeControl({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (tf: string) => void;
  options: TfOption[];
}) {
  return (
    <div className="radio-group" style={{ marginBottom: 10 }}>
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          className={value === o.key ? "active" : ""}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
