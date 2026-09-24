import { useState, type ReactNode } from "react";
import { loadToolsOpen, saveToolsOpen } from "../../lib/chartPrefs";

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

// The in-canvas rail itself: the panel the buttons sit in, and the one control
// that belongs to the rail rather than to any tool — the fold.
//
// WHY IT FOLDS. This rail floats *over* the tape on the chart's left edge, which
// is affordable on a desktop pane and not on a phone: nine 44px buttons is a
// third of the width of the thing you are trying to read. Folded it is a single
// button in the same corner, so the tools are one tap away and the chart gets
// its edge back. The state is sticky and shared across every page that draws one
// of these (lib/chartPrefs) — it is a statement about how much chrome you want
// over a chart, not about which chart.
//
// The fold button is at the *top* because it is the only stable seat: the
// removal tools at the foot come and go with what is drawn, and a control that
// moves under your thumb as you draw is a control you mis-tap.
export function ChartTools({
  children,
  armed,
}: {
  children: ReactNode;
  /** Whether any tool inside is armed. Lights the fold button, so a rail folded
   *  with the ruler live still says a click is going somewhere unusual — folded,
   *  the lit tool that would otherwise say it is out of sight. */
  armed?: boolean;
}) {
  const [open, setOpen] = useState(loadToolsOpen);
  return (
    <div className={`chart-tools${open ? "" : " folded"}`}>
      <ChartToolButton
        icon={open ? "▴" : "🛠"}
        label={open ? "Fold the tools away" : "Show the tools"}
        on={!open && armed}
        onClick={() => {
          const v = !open;
          setOpen(v);
          saveToolsOpen(v);
        }}
        title={
          open
            ? "Fold the tool rail away — it comes back from this same button."
            : armed
              ? "Show the tools — one of them is armed"
              : "Show the tools"
        }
      />
      {open && children}
    </div>
  );
}
