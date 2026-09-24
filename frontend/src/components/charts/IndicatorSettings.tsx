// The panel behind a legend row's "…" — one indicator's settings, where that
// indicator already is.
//
// These knobs used to sit in the Simulator's setup row: a flat line of selects
// above a chart that carried its own list of the very layers they were tuning.
// Nothing said which select belonged to which row, and the setup row folds away
// in fullscreen — so the mode you concentrate in was the one mode where the
// prominence floor and the event threshold couldn't be reached at all. Hanging
// each knob off the row it changes fixes both: the association is the position,
// and the legend is on the chart in every mode.
//
// The app's own knobs are deliberately only selects. Every setting that has
// moved here is a choice from a measured shortlist (the node prominences, the
// big-lot thresholds, the two composite rules) rather than a free number, and a
// shortlist is the honest shape for a knob whose useful range came out of a
// study.
//
// The community studies are the thing that finally needed the other kinds, and
// they are not an exception to that rule so much as outside it: a borrowed
// library's `length` is any integer, and hand-writing a measured shortlist for
// 415 studies we have no opinion about would be a lie in the shape of a select.
// So `kind` exists, it defaults to "select", and nothing of ours sets it.

export interface SettingOption {
  value: string | number;
  label: string;
}

/** What kind of control a field draws. Absent means "select", which is every
 *  knob the app itself puts here — see the note above.
 *
 *  A discriminated union rather than one interface with a widened `onChange`:
 *  a handler that takes `string | number` is not a handler that takes
 *  `string | number | boolean`, so widening the base would have broken every
 *  existing knob in `indicatorKnobs.ts` at once. This way a field with no `kind`
 *  is exactly the select it always was, and each new kind hands its callback the
 *  type that kind actually produces. */
interface BaseField {
  /** Stable within a panel; only React needs it. */
  key: string;
  label: string;
  /** The tooltip the setup row used to carry. Kept verbatim where a knob moved:
   *  it is usually the measured caveat, which is the part worth not losing. */
  help?: string;
  /** A line under the knob for what the label can't hold — most often that the
   *  same state feeds another row too, so it is clear that changing it here
   *  changes it over there. */
  note?: string;
}

/** A choice from a shortlist. The app's own knobs are all of these, and say so
 *  by omitting `kind`. `value`/`onChange` stay owned by the page — this is a
 *  presentation of state that lives where the layer is fed from, never a second
 *  copy of it. */
export interface SelectField extends BaseField {
  kind?: "select";
  value: string | number;
  options: readonly SettingOption[];
  onChange: (value: string | number) => void;
}

/** A free number, with whatever bounds the study that owns the input declares. */
export interface NumberField extends BaseField {
  kind: "int" | "float";
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}

/** Free text, or a colour — both a string to whoever is holding it. */
export interface TextField extends BaseField {
  kind: "text" | "color";
  value: string;
  onChange: (value: string) => void;
}

export interface BoolField extends BaseField {
  kind: "bool";
  value: boolean;
  onChange: (value: boolean) => void;
}

export type SettingField = SelectField | NumberField | TextField | BoolField;

export interface IndicatorSettingsSpec {
  /** Short name for the panel's head — the legend label carries a live count and
   *  the layer's parameters, which read as noise once you are inside its own
   *  settings. */
  title: string;
  fields: SettingField[];
}

/**
 * The settings panel itself, drawn in the middle of the screen.
 *
 * It used to hang under the row it belongs to, which said which layer it was for
 * by being there. On a phone that placement was unreachable for any row far
 * enough down the list — the panel opened below the fold of a legend that has no
 * scroll of its own — so the panel is centred instead and the head below carries
 * the layer's name, which is the part the position was communicating.
 *
 * Purely presentational: Esc, click-away and which row is open are the legend's
 * business, since only the legend knows about the other rows.
 */
export function IndicatorSettings({
  spec,
  onClose,
}: {
  spec: IndicatorSettingsSpec;
  onClose: () => void;
}) {
  return (
    <div className="chart-set" role="dialog" aria-label={`${spec.title} settings`}>
      <div className="chart-set-head">
        <span>{spec.title}</span>
        <button type="button" onClick={onClose} title="Close (Esc)" aria-label="Close settings">
          ×
        </button>
      </div>
      {spec.fields.map((f) => (
        <label key={f.key} className={`chart-set-row${f.kind === "bool" ? " inline" : ""}`} title={f.help}>
          <span className="chart-set-label">{f.label}</span>
          <SettingControl field={f} />
          {f.note && <span className="chart-set-note">{f.note}</span>}
        </label>
      ))}
    </div>
  );
}

/** The control itself, by kind. Split out so the row above stays one shape
 *  whatever it is holding — the label, the control, the note. */
function SettingControl({ field: f }: { field: SettingField }) {
  switch (f.kind) {
    case "bool":
      return <input type="checkbox" checked={f.value} onChange={(e) => f.onChange(e.target.checked)} />;
    case "color":
      return (
        <input
          type="color"
          value={/^#[0-9a-f]{6}$/i.test(f.value) ? f.value : "#7e57c2"}
          onChange={(e) => f.onChange(e.target.value)}
        />
      );
    case "text":
      return <input type="text" value={f.value} onChange={(e) => f.onChange(e.target.value)} />;
    case "int":
    case "float": {
      const int = f.kind === "int";
      return (
        <input
          type="number"
          value={f.value}
          step={int ? 1 : (f.step ?? "any")}
          min={f.min}
          max={f.max}
          onChange={(e) => {
            const v = int ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
            // A half-typed number is not a length. The keystroke is dropped
            // rather than recomputing against NaN, which every recursive
            // indicator in the catalogue turns into a blank pane.
            if (Number.isFinite(v)) f.onChange(v);
          }}
        />
      );
    }
    default:
      return (
        <select
          value={String(f.value)}
          onChange={(e) => {
            // Hand back the option's own value, not the DOM's string of it:
            // half these knobs are numbers, and a page that had to remember
            // which ones would eventually forget.
            const opt = f.options.find((o) => String(o.value) === e.target.value);
            if (opt) f.onChange(opt.value);
          }}
        >
          {f.options.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      );
  }
}
