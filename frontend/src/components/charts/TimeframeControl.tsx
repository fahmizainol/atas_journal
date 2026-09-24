// The one timeframe selector shared by every chart section. Callers pass the
// option list: the minute set is uniform (MINUTE_TFS), and the leading bar
// option differs by context — the engine's native n-tick bar on Strategies, and
// a fixed 500t on the Interactions/Drafts research benches and now the Journal
// (JOURNAL_TFS). The Journal used to be the one section with no tick option at
// all, because its charts were built from bought 1-minute bars; they read the
// tick cache now, so it can offer what everything else does. Mapping a chosen
// key back to backend params (resolution vs bar_minutes/ticks_per_bar) stays
// with each caller; this is only the button row.
//
// `custom` adds a field to the overflow popup for a bucketing nobody put a
// button on. It is a prop rather than the default because the two kinds of
// caller differ in what they can honour: the tape-bucketed charts build every
// bar in the browser and can draw any rule, while the ones above ask an API for
// bars at a fixed set of resolutions.

import { useEffect, useState, type FormEvent } from "react";
import { addCustomTimeframe, isCustomTimeframe, removeCustomTimeframe } from "../../lib/timeframes";

export type TfOption = { key: string; label: string };

// 1m / 3m / 5m / 15m — the standard minute ladder every section offers.
export const MINUTE_TFS: TfOption[] = [
  { key: "1m", label: "1m" },
  { key: "3m", label: "3m" },
  { key: "5m", label: "5m" },
  { key: "15m", label: "15m" },
];

// What the Journal's charts offer. 500 ticks is the same bar the research
// benches use — there is no engine behind a journal trade to inherit a native
// bar size from, so the shared default is the honest choice.
export const JOURNAL_TFS: TfOption[] = [{ key: "500t", label: "500t" }, ...MINUTE_TFS];

export function TimeframeControl({
  value,
  onChange,
  options,
  primary,
  compact,
  custom,
}: {
  value: string;
  onChange: (tf: string) => void;
  options: readonly TfOption[];
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
  /** Offer a field for a bucketing that isn't on the list, and an × to forget
   *  one you added. Only the charts that bucket the tape *in the browser* can
   *  take one — the Simulator, Live, and the two replays build every bar from
   *  prints, so any rule is drawable. The Journal/Strategies/Drafts pickers ask
   *  a backend for bars at a fixed set of resolutions, and a typed `45s` there
   *  would be a promise the API can't keep. */
  custom?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  /** The field rejected what's in it. Cleared on the next keystroke — an error
   *  that outlives the text it was about is an error about nothing. */
  const [bad, setBad] = useState(false);

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

  // The ⋯ is also where a bucketing is typed, so with `custom` it stays on the
  // row even when nothing has overflowed into it.
  const hasPop = extra.length > 0 || !!custom;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const tf = addCustomTimeframe(draft);
    if (!tf) {
      setBad(true);
      return;
    }
    onChange(tf.id);
    setDraft("");
    setOpen(false);
  };

  const row = (
    <div className="radio-group" style={compact ? undefined : { marginBottom: 10 }}>
      {onRow.map((o) => btn(o))}
      {hasPop && (
        <button
          type="button"
          className={`tf-more${open ? " active" : ""}`}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="menu"
          title={
            extra.length > 0
              ? `More timeframes (${extra.map((o) => o.label).join(", ")})`
              : "Another timeframe"
          }
        >
          ⋯
        </button>
      )}
    </div>
  );

  if (!hasPop) return row;

  return (
    <div className="tf-wrap" data-tf-more>
      {row}
      {open && (
        <div className="tf-pop radio-group" role="menu">
          {extra.map((o) =>
            // One you added, and not the one on screen — dropping the selected
            // bucketing would re-bucket the chart as a side effect of tidying.
            // Option and × share a wrapper so a wrap never orphans the × on the
            // next line, where it would read as belonging to whatever it landed
            // beside. `.radio-group button` is a descendant rule, so the buttons
            // inside it are styled exactly as the flat ones are.
            custom && isCustomTimeframe(o.key) && o.key !== value ? (
              <span className="tf-opt" key={o.key}>
                {btn(o, true)}
                <button
                  type="button"
                  className="tf-forget"
                  title={`Forget ${o.label}`}
                  aria-label={`Forget ${o.label}`}
                  onClick={() => removeCustomTimeframe(o.key)}
                >
                  ×
                </button>
              </span>
            ) : (
              btn(o, true)
            ),
          )}
          {custom && (
            <form className="tf-custom" onSubmit={submit}>
              <input
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setBad(false);
                }}
                className={bad ? "bad" : undefined}
                placeholder="e.g. 90s"
                aria-label="Custom timeframe"
                title="Seconds, minutes, hours or ticks: 45s, 7m, 2h, 1500t. A bare number is minutes."
              />
              <button type="submit" title="Add this timeframe">
                +
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
