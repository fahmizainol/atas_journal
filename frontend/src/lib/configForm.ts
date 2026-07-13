// The run form's logic, kept out of its rendering: what a field's value means,
// whether it's legal, and how this config differs from the one it was prefilled
// from. Everything here is driven by the descriptors the server sends
// (journal.sim.schema) — nothing knows the name of a single knob.
//
// The client validates for the *feedback*: an inline error the moment you type a
// zero into the stop is worth more than a 400 after a round trip. It is not the
// enforcement. The same descriptors coerce and reject on the server, because a
// stale tab or a curl is no less able to divide by zero.

import type { ConfigField, ConfigSchema, SimConfig } from "./strategyTypes";

/** The form's working value: a SimConfig under construction, so `confluences`
 * is nested exactly as it is on the wire. */
export type DraftConfig = Record<string, unknown>;

type Section = Record<string, unknown>;

/** A knob whose engine only reads it when a sibling holds a given value —
 * `target_rr` when the target is "rr", the gate's distance when the gate is on.
 * `scope` is the object the sibling lives in: the config, or the gate's section. */
export function dependencyMet(f: ConfigField, scope: Section): boolean {
  if (!f.depends_on) return true;
  return scope[f.depends_on.field] === f.depends_on.value;
}

/** Is a 0-means-off knob currently off? */
export function isOff(f: ConfigField, v: unknown): boolean {
  return !!f.zero_means_off && (v === 0 || v == null);
}

/** The value a knob takes when it is switched on — by its checkbox, or by the
 * dependency that gates it. */
export function onValue(f: ConfigField): unknown {
  if (f.on_default != null) return f.on_default;
  if (f.default != null && f.default !== 0) return f.default;
  return f.min ?? 1;
}

export function fieldError(f: ConfigField, v: unknown): string | null {
  if (v == null || v === "") {
    // A null is only ever legal on a knob nothing is reading right now.
    return f.nullable ? null : "required";
  }
  if (f.type === "int" || f.type === "float") {
    const n = typeof v === "number" ? v : Number(v);
    if (!Number.isFinite(n)) return "must be a number";
    if (f.type === "int" && !Number.isInteger(n)) return "must be a whole number";
    // 0 is the sentinel for "off" — it is allowed to sit below the minimum, which
    // describes the value the knob takes when it is on.
    if (isOff(f, n)) return null;
    if (f.min != null && n < f.min) return `must be at least ${f.min}`;
    if (f.max != null && n > f.max) return `must be at most ${f.max}`;
  }
  if (f.type === "enum" && f.choices && !f.choices.some((c) => c.value === v)) {
    return "not a valid choice";
  }
  return null;
}

/** Every error in the draft, keyed by field name (gate knobs as "gate.knob").
 * Includes the rules that read more than one knob — each of those is a config the
 * engine accepts and then quietly does something other than what it says. */
export function validate(schema: ConfigSchema, cfg: DraftConfig): Record<string, string> {
  const errors: Record<string, string> = {};

  for (const f of schema.fields) {
    if (!dependencyMet(f, cfg)) continue; // the engine isn't reading it
    const e = fieldError(f, cfg[f.name]);
    if (e) errors[f.name] = e;
  }

  const conf = (cfg.confluences ?? {}) as Record<string, Section>;
  for (const gate of schema.confluences) {
    const section = conf[gate.name];
    if (!section) continue;
    for (const f of gate.fields) {
      if (!dependencyMet(f, section)) continue;
      const e = fieldError(f, section[f.name]);
      if (e) errors[`${gate.name}.${f.name}`] = e;
    }
  }

  const start = cfg.start_date as string;
  const end = cfg.end_date as string;
  if (start && end && start > end) errors.end_date = "the window ends before it starts";

  return errors;
}

// --- the diff against baseline ------------------------------------------------

export interface ConfigDiff {
  key: string;
  field: ConfigField;
  from: unknown;
  to: unknown;
}

/** A gate that is off and a gate that isn't in the config at all are the same
 * run (the server drops disabled sections before hashing), so the diff must read
 * them as the same thing — otherwise ticking a gate on and back off would show as
 * a change against a baseline it is in fact identical to. */
function gateValue(cfg: DraftConfig, gate: string, f: ConfigField): unknown {
  const section = ((cfg.confluences ?? {}) as Record<string, Section>)[gate];
  if (!section || !section.enabled) return f.name === "enabled" ? false : f.default;
  return section[f.name] ?? f.default;
}

/** Which knobs differ from the config this form was prefilled with. */
export function diffConfig(
  schema: ConfigSchema,
  cfg: DraftConfig,
  ref: DraftConfig,
): ConfigDiff[] {
  const out: ConfigDiff[] = [];
  const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

  for (const f of schema.fields) {
    // A knob the engine isn't reading can't make this a different experiment,
    // even when its (ignored) value differs.
    if (!dependencyMet(f, cfg) && !dependencyMet(f, ref)) continue;
    if (!same(cfg[f.name], ref[f.name])) {
      out.push({ key: f.name, field: f, from: ref[f.name], to: cfg[f.name] });
    }
  }
  for (const gate of schema.confluences) {
    for (const f of gate.fields) {
      const to = gateValue(cfg, gate.name, f);
      const from = gateValue(ref, gate.name, f);
      if (!same(to, from)) {
        out.push({ key: `${gate.name}.${f.name}`, field: f, from, to });
      }
    }
  }
  return out;
}

const displayValue = (f: ConfigField, v: unknown): string => {
  if (isOff(f, v)) return "off";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (v == null) return "—";
  return String(v);
};

/** `stop_ticks 75 → 50 · trail_step_ticks off → 75` — the line above the Run
 * button, which is the honest answer to "what am I actually about to test?". */
export function describeDiff(diffs: ConfigDiff[]): string {
  return diffs
    .map((d) => `${d.key} ${displayValue(d.field, d.from)} → ${displayValue(d.field, d.to)}`)
    .join("  ·  ");
}

/** `stop 50, trail 75` — the same diff, short enough to name a run with. Ten
 * experiments deep, a list of these beats a list of hashes. */
export function suggestLabel(diffs: ConfigDiff[]): string {
  return diffs
    .map((d) => {
      const [section, knob] = d.key.includes(".") ? d.key.split(".") : [null, d.key];
      // A gate's on/off switch is named by its gate — "enabled on" says nothing
      // about *what* was enabled.
      const name = section && knob === "enabled" ? section : [section, knob].filter(Boolean).join(" ");
      return `${name.replace(/_(ticks|bars)$/, "").replace(/_/g, " ")} ${displayValue(d.field, d.to)}`;
    })
    .join(", ");
}

/** The draft the form starts from, and returns to when you hit reset. Gate
 * sections are materialized so their knobs have something to bind to; the server
 * drops the ones left switched off, so an untouched form still resolves to the
 * exact run it was prefilled from. */
export function draftFrom(schema: ConfigSchema, config: SimConfig | null): DraftConfig {
  const base: DraftConfig = {};
  for (const f of schema.fields) {
    base[f.name] = config ? (config as unknown as DraftConfig)[f.name] : f.default;
  }
  const conf = ((config?.confluences ?? {}) as Record<string, Section>) ?? {};
  const sections: Record<string, Section> = {};
  for (const gate of schema.confluences) {
    const existing = conf[gate.name] ?? {};
    const section: Section = {};
    for (const f of gate.fields) {
      section[f.name] = existing[f.name] ?? f.default;
    }
    sections[gate.name] = section;
  }
  base.confluences = sections;
  return base;
}
