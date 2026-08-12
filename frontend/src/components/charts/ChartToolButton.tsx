import type { ReactNode } from "react";

// One button in the on-chart tool rail, shared by the strategy chart and the
// replay chart because both rails are the same rail.
//
// The rail is icons only — it sits on the chart's left edge and the words would
// cost more width than the tape can spare. So the icon is the label, and the
// meaning is carried three ways for the three ways it gets read: `data-tip`
// paints the hover tooltip (index.css), `aria-label` names the button for
// assistive tech, and the label element stays in the markup for anyone who
// re-widens the rail later.
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
  /** The long description. Falls back to the label when a tool has none. */
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`chart-tool${on ? " on" : ""}`}
      onClick={onClick}
      disabled={disabled}
      // Not the `title` attribute: the CSS tooltip below would double up with the
      // platform's own, at a different delay and a different corner.
      data-tip={title ?? label}
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

// The hairline between the tools that make things and the tools that remove
// them. Only worth drawing when something removable exists — see both charts.
export function ChartToolSep() {
  return <div className="chart-tool-sep" aria-hidden="true" />;
}
