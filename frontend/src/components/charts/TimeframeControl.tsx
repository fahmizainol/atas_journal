// The one timeframe selector shared by every chart section. Callers pass the
// option list: the minute set is uniform (MINUTE_TFS), and the leading bar
// option differs by context — the engine's native n-tick bar on Strategies, a
// fixed 500t on the Interactions/Drafts research benches, and none on the
// Journal's databento minute charts. Mapping a chosen key back to backend params
// (resolution vs bar_minutes/ticks_per_bar) stays with each caller; this is only
// the button row.

import { useEffect, useState } from "react";

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
  primary,
  compact,
}: {
  value: string;
  onChange: (tf: string) => void;
  options: TfOption[];
  /** Keys to keep on the row; everything else moves behind a ⋯ button. Omit and
   *  every option is shown, which is what the four-option callers want.
   *
   *  Named rather than "first N" because the order here is meaningful — the list
   *  runs fastest-first — and the ones worth a permanent button are not the
   *  fastest four. The row still draws them in the caller's order. */
  primary?: string[];
  /** Drop the row's bottom margin. It exists because most callers sit this above
   *  a chart in a stacked form; in a vertically-centred bar it only pushes the
   *  buttons off-centre. Kept inline and conditional rather than moved to CSS —
   *  `.radio-group` is shared with six components that space themselves. */
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);

  // Esc and outside-press close, both in the capture phase — the same reasoning
  // as NavMenu and IndicatorLegend: a listener registered on open would
  // otherwise run after the chart's own key handlers, and the tape would act on
  // the Escape first.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest?.("[data-tf-more]")) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [open]);

  // The selected option always keeps its button, even when it lives in the
  // overflow: a picker that hides what you have chosen is a picker that lies.
  const onRow = primary
    ? options.filter((o) => primary.includes(o.key) || o.key === value)
    : options;
  const extra = options.filter((o) => !onRow.includes(o));

  const btn = (o: TfOption, close = false) => (
    <button
      key={o.key}
      type="button"
      className={value === o.key ? "active" : ""}
      onClick={() => {
        onChange(o.key);
        if (close) setOpen(false);
      }}
    >
      {o.label}
    </button>
  );

  const row = (
    <div className="radio-group" style={compact ? undefined : { marginBottom: 10 }}>
      {onRow.map((o) => btn(o))}
      {extra.length > 0 && (
        <button
          type="button"
          className={`tf-more${open ? " active" : ""}`}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="menu"
          title={`More timeframes (${extra.map((o) => o.label).join(", ")})`}
        >
          ⋯
        </button>
      )}
    </div>
  );

  if (extra.length === 0) return row;

  return (
    <div className="tf-wrap" data-tf-more>
      {row}
      {open && (
        <div className="tf-pop radio-group" role="menu">
          {extra.map((o) => btn(o, true))}
        </div>
      )}
    </div>
  );
}
