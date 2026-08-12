// The run form: one input per knob, rendered from the descriptors the server
// sends (journal.sim.schema) rather than from a hard-coded list. A knob added to
// SimConfig shows up here on its own — and one that isn't in the schema can't be
// reached from the browser at all, which is the point.
//
// Three things the plain JSON editor this replaced could not do:
//   - `0` is the engine's sentinel for "feature off" on four knobs. Here that is
//     a checkbox, and the 0 goes on the wire where the engine still expects it.
//   - A knob the engine isn't currently reading (target_rr on a dev2 target) is
//     shown disabled rather than hidden, so the shape of the form doesn't depend
//     on its values and "is there an R target?" is answered in one place.
//   - Every field that differs from the config this was prefilled with is marked,
//     including inside a collapsed group — that diff is the experiment.

import { useState, type ReactNode } from "react";
import { dependencyMet, isOff, onValue, type DraftConfig } from "../../lib/configForm";
import type { ConfigField, ConfigGroup, ConfigSchema } from "../../lib/strategyTypes";

type Section = Record<string, unknown>;

function Help({ text }: { text?: string }) {
  if (!text) return null;
  return <div className="cfg-help">{text}</div>;
}

/** One knob. `scope` is the object it lives in — the config, or a gate's section
 * — which is also where its dependency (if any) reads its sibling from. */
function FieldRow({
  field,
  scope,
  onChange,
  error,
  changed,
}: {
  field: ConfigField;
  scope: Section;
  onChange: (name: string, value: unknown) => void;
  error?: string;
  changed: boolean;
}) {
  const value = scope[field.name];
  const enabled = dependencyMet(field, scope);
  const off = isOff(field, value);

  const label = (
    <label>
      {field.label}
      {field.unit && <span className="cfg-unit"> ({field.unit})</span>}
    </label>
  );

  const cls = ["field", "cfg-field", changed ? "cfg-changed" : "", enabled ? "" : "cfg-disabled"]
    .filter(Boolean)
    .join(" ");

  // A bool is its own control — the label belongs beside the box, not above it.
  if (field.type === "bool") {
    return (
      <div className={cls}>
        <label className="cfg-check">
          <input
            type="checkbox"
            checked={!!value}
            disabled={!enabled}
            onChange={(e) => onChange(field.name, e.target.checked)}
          />
          <span>{field.label}</span>
        </label>
        <Help text={field.help} />
      </div>
    );
  }

  // The four 0-means-off knobs: a checkbox over the sentinel. Unticking writes 0
  // (which is what the engine reads as "off"); ticking restores the on-value, so
  // nobody has to know that 0 is magic.
  if (field.zero_means_off) {
    return (
      <div className={cls}>
        <label className="cfg-check">
          <input
            type="checkbox"
            checked={!off}
            disabled={!enabled}
            onChange={(e) => onChange(field.name, e.target.checked ? onValue(field) : 0)}
          />
          <span>
            {field.label}
            {field.unit && <span className="cfg-unit"> ({field.unit})</span>}
          </span>
        </label>
        {!off && (
          <input
            type="number"
            value={value as number}
            min={field.min}
            max={field.max}
            // Same rule as the plain numeric branch below: a float knob left on
            // the default step of 1 reads its own value back as invalid (the
            // daily loss stop is $1995.01; the ATR trail multiplier is 0.05).
            step={field.type === "int" ? 1 : "any"}
            disabled={!enabled}
            onChange={(e) => onChange(field.name, e.target.valueAsNumber)}
          />
        )}
        <Help text={field.help} />
        {error && <div className="cfg-error">{error}</div>}
      </div>
    );
  }

  let input;
  if (field.type === "enum") {
    input = (
      <select
        value={String(value ?? "")}
        disabled={!enabled}
        onChange={(e) => onChange(field.name, e.target.value)}
      >
        {field.choices?.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "int" || field.type === "float") {
    input = (
      <input
        type="number"
        value={value == null ? "" : (value as number)}
        min={field.min}
        max={field.max}
        step={field.type === "int" ? 1 : "any"}
        disabled={!enabled}
        onChange={(e) =>
          onChange(field.name, e.target.value === "" ? null : e.target.valueAsNumber)
        }
      />
    );
  } else if (field.type === "date") {
    input = (
      <input
        type="date"
        value={String(value ?? "")}
        disabled={!enabled}
        onChange={(e) => onChange(field.name, e.target.value)}
      />
    );
  } else if (field.type === "time") {
    input = (
      <input
        type="time"
        step={1}
        value={String(value ?? "")}
        disabled={!enabled}
        onChange={(e) => onChange(field.name, e.target.value)}
      />
    );
  } else {
    input = (
      <input
        type="text"
        value={String(value ?? "")}
        disabled={!enabled}
        onChange={(e) => onChange(field.name, e.target.value)}
      />
    );
  }

  return (
    <div className={cls}>
      {label}
      {input}
      <Help text={field.help} />
      {error && <div className="cfg-error">{error}</div>}
    </div>
  );
}

function Group({
  title,
  collapsed,
  changes,
  summary,
  children,
}: {
  title: string;
  collapsed: boolean;
  changes: number;
  summary?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(!collapsed);
  if (!collapsed)
    return (
      <fieldset className="cfg-group">
        <legend>
          {title}
          {changes > 0 && <span className="cfg-count">{changes}</span>}
        </legend>
        <div className="cfg-grid">{children}</div>
      </fieldset>
    );

  // A collapsed group still declares that it is hiding a change — a knob you
  // tweaked and then closed must not be able to disappear from the experiment.
  return (
    <fieldset className="cfg-group">
      <legend>
        <button type="button" className="cfg-toggle" onClick={() => setOpen((o) => !o)}>
          {open ? "▾" : "▸"} {title}
          {changes > 0 && <span className="cfg-count">{changes}</span>}
        </button>
      </legend>
      {open ? (
        <div className="cfg-grid">{children}</div>
      ) : (
        summary && <div className="cfg-summary">{summary}</div>
      )}
    </fieldset>
  );
}

export function ConfigForm({
  schema,
  config,
  onChange,
  errors,
  changed,
}: {
  schema: ConfigSchema;
  config: DraftConfig;
  onChange: (next: DraftConfig) => void;
  errors: Record<string, string>;
  /** Keys (name, or "gate.knob") that differ from the config this was prefilled with. */
  changed: Set<string>;
}) {
  const setField = (name: string, value: unknown) => onChange({ ...config, [name]: value });

  const setGateField = (gate: string, name: string, value: unknown) => {
    const conf = (config.confluences ?? {}) as Record<string, Section>;
    onChange({
      ...config,
      confluences: { ...conf, [gate]: { ...(conf[gate] ?? {}), [name]: value } },
    });
  };

  // A one-line stand-in for a group you've closed, so the boring knobs are out of
  // the way without being out of sight. Names included: a collapsed "Size & cost"
  // reading `1 · 7` tells you nothing you didn't already have to remember.
  const summaryOf = (g: ConfigGroup) =>
    schema.fields
      .filter((f) => f.group === g.key)
      .map((f) => {
        const v = config[f.name];
        if (isOff(f, v)) return `${f.name} off`;
        if (typeof v === "boolean") return v ? f.name : `no ${f.name}`;
        return `${f.name} ${v ?? "—"}`;
      })
      .join("  ·  ");

  const conf = (config.confluences ?? {}) as Record<string, Section>;

  return (
    <div className="cfg-form">
      {schema.groups.map((g) => {
        const fields = schema.fields.filter((f) => f.group === g.key);
        if (!fields.length) return null;
        return (
          <Group
            key={g.key}
            title={g.title}
            collapsed={g.collapsed}
            summary={summaryOf(g)}
            changes={fields.filter((f) => changed.has(f.name)).length}
          >
            {fields.map((f) => (
              <FieldRow
                key={f.name}
                field={f}
                scope={config}
                onChange={setField}
                error={errors[f.name]}
                changed={changed.has(f.name)}
              />
            ))}
          </Group>
        );
      })}

      {schema.confluences.map((gate) => {
        const section = conf[gate.name] ?? {};
        return (
          <Group
            key={gate.name}
            title={`Confluence — ${gate.name.replace(/_/g, " ")}`}
            collapsed={false}
            changes={gate.fields.filter((f) => changed.has(`${gate.name}.${f.name}`)).length}
          >
            {gate.fields.map((f) => (
              <FieldRow
                key={f.name}
                field={f}
                scope={section}
                onChange={(name, value) => setGateField(gate.name, name, value)}
                error={errors[`${gate.name}.${f.name}`]}
                changed={changed.has(`${gate.name}.${f.name}`)}
              />
            ))}
          </Group>
        );
      })}
    </div>
  );
}
