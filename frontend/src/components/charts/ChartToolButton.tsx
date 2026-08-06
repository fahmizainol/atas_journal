import type { ReactNode } from "react";

// One button in the on-chart tool row, shared by the strategy chart and the
// replay chart because both rows are the same row.
//
// The icon and the words are separate elements on purpose: on a phone the words
// come off (index.css), and what is left has to still say which tool it is. So
// the icon is never decoration — it is the label at small sizes, and the title /
// aria-label carry the meaning for everything that isn't looking at pixels.
export function ChartToolButton({
  icon,
  label,
  on,
  disabled,
  title,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  /** Present only on the tools that arm; drives the lit state and aria-pressed. */
  on?: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`chart-tool${on ? " on" : ""}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={label}
      aria-pressed={on === undefined ? undefined : on}
    >
      <span className="chart-tool-i" aria-hidden="true">
        {icon}
      </span>
      <span className="chart-tool-t">{label}</span>
    </button>
  );
}
