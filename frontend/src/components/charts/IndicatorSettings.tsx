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
// Deliberately only selects. Every setting that has moved here is a choice from
// a measured shortlist (the node prominences, the big-lot thresholds, the two
// composite rules) rather than a free number, and a shortlist is the honest
// shape for a knob whose useful range came out of a study. A free-number field
// is a few lines away if something ever needs one.

export interface SettingOption {
  value: string | number;
  label: string;
}

/** One knob on the panel. `value`/`onChange` stay owned by the page — this is a
 *  presentation of state that lives where the layer is fed from, never a second
 *  copy of it. */
export interface SettingField {
  /** Stable within a panel; only React needs it. */
  key: string;
  label: string;
  /** The tooltip the setup row used to carry. Kept verbatim where a knob moved:
   *  it is usually the measured caveat, which is the part worth not losing. */
  help?: string;
  value: string | number;
  options: readonly SettingOption[];
  onChange: (value: string | number) => void;
  /** A line under the knob for what the label can't hold — most often that the
   *  same state feeds another row too, so it is clear that changing it here
   *  changes it over there. */
  note?: string;
}

export interface IndicatorSettingsSpec {
  /** Short name for the panel's head — the legend label carries a live count and
   *  the layer's parameters, which read as noise once you are inside its own
   *  settings. */
  title: string;
  fields: SettingField[];
}

/**
 * The settings panel itself, drawn under the row it belongs to.
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
        <label key={f.key} className="chart-set-row" title={f.help}>
          <span className="chart-set-label">{f.label}</span>
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
          {f.note && <span className="chart-set-note">{f.note}</span>}
        </label>
      ))}
    </div>
  );
}
