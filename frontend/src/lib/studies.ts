// The community indicator catalogue — 415 studies out of `lightweight-charts-indicators`,
// riding oakscriptjs (a PineScript-v6 runtime), reachable from the Charts topbar.
//
// Evaluated first on docs/research/lwc-addons.html, which is the page to read
// before trusting a number off one of these: the package claims 446 indicators and
// 415 of them carry the metadata/calculate pair this reads; 317 are machine ports of
// published Pine, and section 3 there diffs the library's ATR/RSI/EMA against our
// own bar for bar (they converge to ~1e-10 once seeded). A transpiled indicator
// that is quietly wrong is the cheapest route to a fake edge, so what ships here
// is a *reading* surface — nothing in the sim or the strategy engine reads it.
//
// The seam is deliberately thin. Nothing outside this file imports the package,
// and the types below are our own structural mirrors of its metadata rather than
// its exported ones: every module is validated at runtime anyway (see
// `isStudyModule`), because "does this export carry the metadata/calculate pair"
// is the same question either way, and asking it once in JS is cheaper than
// dragging 793 exports' worth of declarations through the build.
//
// Loaded on demand. The bundle is ~1.8 MB of ESM, which is not a thing to put in
// front of a chart page that opens on a tape; `loadCatalogue()` is called when
// the picker is first opened, or by a chart that was handed saved studies.

import type { SettingField } from "../components/charts/IndicatorSettings";
import type { Bar } from "./chartTypes";

/** One configurable on a study, as the package describes it. These six types
 *  cover the entire catalogue — asserted on the research page, and the reason one
 *  generated form can serve every study in it. */
export interface StudyInputConfig {
  id: string;
  type: "int" | "float" | "bool" | "string" | "source" | "color";
  defval: unknown;
  title?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

/** One line the study draws, and how. `display: "none"` means the Pine original
 *  computed it for its own use and never plotted it. */
export interface StudyPlotConfig {
  id: string;
  title: string;
  color: string;
  lineWidth?: number;
  style?: "line" | "stepline" | "histogram" | "area" | "circles" | "columns" | "cross" | "areabr" | "steplinebr" | "linebr";
  display?: "all" | "none" | "data_window" | "status_line" | "pane";
}

/** A fixed reference level — RSI's 70/30, an oscillator's zero. */
export interface StudyHLineConfig {
  id: string;
  price: number;
  title?: string;
  color?: string;
  linestyle?: "solid" | "dashed" | "dotted";
  linewidth?: number;
}

/** What `plotshape`/`plotchar`/`plotarrow` and every candlestick pattern come
 *  back as. Already in lightweight-charts' own marker shape — the package
 *  converts on its way out. */
export interface StudyMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text?: string;
  size?: number;
}

export interface StudyResult {
  plots?: Record<string, { time: number; value: number }[]>;
  markers?: StudyMarker[];
}

export interface StudyModule {
  metadata: { title: string; shortTitle?: string; overlay: boolean };
  inputConfig?: StudyInputConfig[];
  plotConfig?: StudyPlotConfig[];
  hlineConfig?: StudyHLineConfig[];
  calculate(bars: Bar[], inputs?: Record<string, unknown>): StudyResult;
}

/** One entry in the catalogue: the module plus the three facts the picker, the
 *  legend and the pane router need without opening it. */
export interface StudyEntry {
  /** What a spec stores. The package's export name (`RSI`, `SqueezeMomentum`)
   *  for a borrowed study; a `pine:`-prefixed name for one of ours, so the two
   *  namespaces cannot collide and a saved spec always resolves to the thing it
   *  meant. */
  key: string;
  title: string;
  short: string;
  /** Drawn over the candles, or in a pane of its own. The library's own call. */
  overlay: boolean;
  /** Whose it is. Borrowed studies are unvalidated ports of other people's Pine;
   *  ours are Pine we transcribed (see lib/pineStudy), which makes them *our*
   *  handwriting rather than trustworthy — the distinction the picker draws is
   *  about provenance, not quality, and neither side is wired to the sim. */
  origin: "mine" | "community";
  mod: StudyModule;
}

/** One study the user has added, as it is persisted. Just the export name and
 *  the inputs they chose — the module itself is looked up at draw time, so a
 *  saved study survives a package upgrade (or disappears cleanly if the upstream
 *  export is renamed). */
export interface StudySpec {
  /** Unique within the list, so the same indicator can be added twice at
   *  different lengths. */
  id: string;
  key: string;
  inputs: Record<string, unknown>;
  /** Line colour per plot, keyed by the plot id the study declares — an override
   *  of the palette the borrowed indicator ships with. Only the plots actually
   *  recoloured are stored, so an untouched study keeps following its own
   *  defaults and a package upgrade that restyles one is still felt.
   *
   *  It exists because the package colours by *export*, not by instance: a 9 and
   *  a 21 EMA are the same `EMA` module twice, so they arrive as two lines in
   *  the identical blue and there is no reading them apart on the canvas. The
   *  inputs alone cannot fix that — nothing in `inputConfig` reaches the plot. */
  colors?: Record<string, string>;
  /** Line width (1–4) and dash per plot, on the same override-only terms as
   *  `colors`: absent means the house default if there is one, else the
   *  package's own. */
  widths?: Record<string, number>;
  styles?: Record<string, PlotLineStyle>;
  /** The legend row's eye. On the spec rather than in the visibility map because
   *  that map is a fixed set of known keys and drops anything it doesn't
   *  recognise on load (see chartPrefs) — an unbounded list of spec ids cannot
   *  live in it. Hidden also means *not computed*: the layer skips `calculate`
   *  entirely, which is a saving the fixed layers mostly don't get. */
  hidden?: boolean;
}

/** How a plot's line is dashed. The subset of lightweight-charts' own styles a
 *  reader tells apart at a glance. */
export const PLOT_LINE_STYLES = ["solid", "dashed", "dotted"] as const;
export type PlotLineStyle = (typeof PLOT_LINE_STYLES)[number];

export const PLOT_WIDTHS = [1, 2, 3, 4] as const;

/** Our own defaults for a borrowed study whose shipped palette fights this
 *  chart, keyed by export name then plot id. Sits between the spec's overrides
 *  and the package's `plotConfig`, so an untouched study picks it up and a
 *  recoloured one keeps what the reader chose.
 *
 *  EMAMulti ("EMA4") ships TradingView's orange/blue/green/pink, and every one of
 *  them is already a layer here: weekly VWAP, Modern VWAP, IB and the NY anchor.
 *  Re-cut as one yellow ramp — the one hue family no anchor, profile or zone
 *  uses — graded by length three ways at once: brighter, thinner and solid for
 *  the fast pair; darker, wider and dashed for the 100/200 trend context. */
const HOUSE_PLOTS: Record<string, Record<string, { color?: string; width?: number; style?: PlotLineStyle }>> = {
  EMAMulti: {
    plot0: { color: "#fde047", width: 1, style: "solid" }, // 20
    plot1: { color: "#eab308", width: 2, style: "solid" }, // 50
    plot2: { color: "#ca8a04", width: 2, style: "dashed" }, // 100
    plot3: { color: "#a16207", width: 3, style: "dashed" }, // 200
  },
};

const house = (entry: StudyEntry, plotId: string) => HOUSE_PLOTS[entry.key]?.[plotId];

/** What a chart made of one spec, for the picker to report back. */
export interface StudyReport {
  id: string;
  /** `calculate` threw, or returned nothing plottable. Null when it drew. */
  error: string | null;
  plots: number;
  markers: number;
}

/** The `source` input's options — which price a study reads. Pine's list, minus
 *  nothing: the package accepts all of them. */
export const SOURCE_OPTIONS = [
  "open",
  "high",
  "low",
  "close",
  "hl2",
  "hlc3",
  "ohlc4",
  "hlcc4",
] as const;

function isStudyModule(v: unknown): v is StudyModule {
  if (!v || typeof v !== "object") return false;
  const m = v as Partial<StudyModule>;
  return typeof m.calculate === "function" && !!m.metadata && typeof m.metadata.title === "string";
}

let cache: StudyEntry[] | null = null;
let pending: Promise<StudyEntry[]> | null = null;

/** The catalogue if it is already in memory, else null. For the draw path, which
 *  cannot await — see `loadCatalogue` for the other half. */
export function catalogue(): StudyEntry[] | null {
  return cache;
}

/** Fetch the catalogue, once. Concurrent callers share the one import.
 *
 *  Two imports, one cache. Ours are a few kilobytes and the borrowed ones are
 *  1.8 MB, so folding them into a single load means a chart holding only one of
 *  ours still waits for the big half — accepted deliberately, because the
 *  alternative is two caches, two loading states and two error states for a
 *  saving most sittings pay once and then have cached. They share the Pine
 *  runtime underneath either way. */
export function loadCatalogue(): Promise<StudyEntry[]> {
  if (cache) return Promise.resolve(cache);
  if (!pending) {
    pending = Promise.all([import("lightweight-charts-indicators"), import("../studies")])
      .then(([pkg, ours]) => {
        // Every export that carries the metadata/calculate pair — the package
        // also exports the bare `calculateX` functions, the input types and a
        // handful of helpers, none of which are studies. Sorted by title,
        // because the picker is a list a human reads.
        const borrowed: StudyEntry[] = Object.entries(pkg as Record<string, unknown>)
          .filter(([, v]) => isStudyModule(v))
          .map(([key, v]) => {
            const mod = v as StudyModule;
            return {
              key,
              title: mod.metadata.title || key,
              short: mod.metadata.shortTitle || mod.metadata.title || key,
              overlay: !!mod.metadata.overlay,
              origin: "community" as const,
              mod,
            };
          });
        // One list, sorted as a whole — `origin` is what separates them, and the
        // picker is the only thing that needs them separated. Everything else
        // (the layer, both legends, a saved spec) only ever asks by key.
        cache = [...ours.MINE, ...borrowed].sort((a, b) => a.title.localeCompare(b.title));
        return cache;
      })
      .catch((e) => {
        // A failed chunk fetch must not poison the picker for the session — the
        // next open tries again.
        pending = null;
        throw e;
      });
  }
  return pending;
}

/** One study by key, or null. Null also means "the catalogue hasn't landed yet"
 *  — the callers that care tell the two apart by asking `catalogue()` whether
 *  there is a catalogue to have missed. */
export function findStudy(key: string): StudyEntry | null {
  return cache?.find((c) => c.key === key) ?? null;
}

export function defaultInputs(entry: StudyEntry): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const cfg of entry.mod.inputConfig ?? []) out[cfg.id] = cfg.defval;
  return out;
}

/** `RSI (14)` — the short name plus its numeric inputs, TradingView's own
 *  convention. Strings, booleans and colours are left off: they are what the
 *  settings panel is for, and a row reading `RSI (14, close, false, None)` is a
 *  row nobody reads. */
export function studyLabel(entry: StudyEntry, inputs: Record<string, unknown>): string {
  const nums = (entry.mod.inputConfig ?? [])
    .filter((c) => c.type === "int" || c.type === "float")
    .map((c) => inputs[c.id])
    .filter((v) => typeof v === "number");
  if (!nums.length) return entry.short;
  const shown = nums.slice(0, 3).join(", ") + (nums.length > 3 ? ", …" : "");
  return `${entry.short} (${shown})`;
}

/** The plots a study actually draws, in the order it declares them. `display:
 *  "none"` is the Pine original's "computed, never plotted", and the layer skips
 *  those too — a colour knob for a line that is not on the chart is a knob that
 *  does nothing. */
const drawnPlots = (entry: StudyEntry): StudyPlotConfig[] =>
  (entry.mod.plotConfig ?? []).filter((c) => c.display !== "none");

/** The colour one of a study's plots is drawn in — the override if the user set
 *  one, else the palette the package ships. Called with no plot id it answers for
 *  the first drawn plot, which is what the legend swatch wants.
 *
 *  Both the canvas and the swatch resolve through here, because the swatch's
 *  whole job is to name the line: the moment they answer differently it is
 *  pointing at a colour that is not on the chart. Empty string when the study
 *  plots nothing at all (the pattern ones, which are marks only) — the callers
 *  have their own fallback for that and it isn't a colour this file knows. */
export function studyColor(entry: StudyEntry, spec: StudySpec, plotId?: string): string {
  const plots = drawnPlots(entry);
  const cfg = plotId ? plots.find((p) => p.id === plotId) : plots[0];
  if (!cfg) return "";
  return spec.colors?.[cfg.id] || house(entry, cfg.id)?.color || cfg.color || "";
}

/** A plot's line width, 1–4 — the spec's override, else the house default, else
 *  the package's. Clamped: lightweight-charts takes nothing else. */
export function studyWidth(entry: StudyEntry, spec: StudySpec, plotId: string): number {
  const cfg = drawnPlots(entry).find((p) => p.id === plotId);
  const w = spec.widths?.[plotId] ?? house(entry, plotId)?.width ?? cfg?.lineWidth ?? 1;
  return Math.min(4, Math.max(1, Math.round(Number(w) || 1)));
}

/** A plot's dash — the spec's override, else the house default, else what the
 *  Pine style implies: `circles`/`cross` are point plots, and dotted is the
 *  nearest honest thing a line series has. */
export function studyLineStyle(entry: StudyEntry, spec: StudySpec, plotId: string): PlotLineStyle {
  const picked = spec.styles?.[plotId] ?? house(entry, plotId)?.style;
  if (picked && PLOT_LINE_STYLES.includes(picked)) return picked;
  const cfg = drawnPlots(entry).find((p) => p.id === plotId);
  return cfg?.style === "circles" || cfg?.style === "cross" ? "dotted" : "solid";
}

/** One study's inputs as the app's own settings panel takes them.
 *
 *  The bridge between a borrowed library's `inputConfig` and the panel every
 *  other knob on this chart is drawn in — so a study's settings open in the same
 *  place, in the same shape, as the composite's prominence floor. Six input
 *  types cover the whole catalogue, which is why this mapping is total.
 *
 *  `onChange` takes the whole new input bag rather than one field: a spec is
 *  persisted as a unit and the caller is replacing it in a list either way. */
export function studyFields(
  entry: StudyEntry,
  spec: StudySpec,
  onChange: (spec: StudySpec) => void,
): SettingField[] {
  const inputs = spec.inputs;
  const fields: SettingField[] = (entry.mod.inputConfig ?? []).map((cfg) => {
    const set = (value: string | number | boolean) =>
      onChange({ ...spec, inputs: { ...inputs, [cfg.id]: value } });
    const base = {
      key: cfg.id,
      label: cfg.title || cfg.id,
      onChange: set,
    };
    if (cfg.type === "bool") return { ...base, kind: "bool" as const, value: !!inputs[cfg.id] };
    if (cfg.type === "color")
      return { ...base, kind: "color" as const, value: String(inputs[cfg.id] ?? "#7e57c2") };
    if (cfg.type === "source")
      return {
        ...base,
        kind: "select" as const,
        value: String(inputs[cfg.id] ?? "close"),
        options: SOURCE_OPTIONS.map((o) => ({ value: o, label: o })),
      };
    // A string input with a declared option list is a select in Pine too; one
    // without is genuinely free text (a session window, a symbol).
    if (cfg.type === "string")
      return cfg.options?.length
        ? {
            ...base,
            kind: "select" as const,
            value: String(inputs[cfg.id] ?? ""),
            options: cfg.options.map((o) => ({ value: o, label: o })),
          }
        : { ...base, kind: "text" as const, value: String(inputs[cfg.id] ?? "") };
    return {
      ...base,
      kind: cfg.type === "int" ? ("int" as const) : ("float" as const),
      value: Number(inputs[cfg.id] ?? 0),
      min: cfg.min,
      max: cfg.max,
      step: cfg.step,
    };
  });

  // Then a colour per line the study draws. Appended rather than interleaved:
  // the inputs are the indicator's own vocabulary and come from upstream, these
  // are ours, and keeping the seam visible in the form is the honest shape.
  //
  // Per plot, not one for the whole study, because a study is rarely one line —
  // recolouring an EMA's Bollinger band to match its average would merge the two
  // things the band exists to separate.
  for (const cfg of drawnPlots(entry)) {
    fields.push({
      kind: "color",
      // Namespaced: a study whose own `inputConfig` carries a colour input would
      // otherwise collide with it here, and React would drop one of the rows.
      key: `plot:${cfg.id}`,
      label: `${cfg.title || cfg.id} colour`,
      value: studyColor(entry, spec, cfg.id),
      onChange: (value) => onChange({ ...spec, colors: { ...spec.colors, [cfg.id]: value } }),
    });
    // Width and dash only mean something on a line — a histogram's bar has
    // neither.
    if (cfg.style === "histogram" || cfg.style === "columns") continue;
    fields.push({
      key: `plotw:${cfg.id}`,
      label: `${cfg.title || cfg.id} width`,
      value: studyWidth(entry, spec, cfg.id),
      options: PLOT_WIDTHS.map((w) => ({ value: w, label: `${w}px` })),
      onChange: (value) => onChange({ ...spec, widths: { ...spec.widths, [cfg.id]: Number(value) } }),
    });
    fields.push({
      key: `plots:${cfg.id}`,
      label: `${cfg.title || cfg.id} line`,
      value: studyLineStyle(entry, spec, cfg.id),
      options: PLOT_LINE_STYLES.map((st) => ({ value: st, label: st })),
      onChange: (value) =>
        onChange({ ...spec, styles: { ...spec.styles, [cfg.id]: value as PlotLineStyle } }),
    });
  }
  return fields;
}

/** A fresh spec id. Unique within the list and stable across a reload — the
 *  list is persisted, so a timestamp or a random string would churn the React
 *  keys of everything already on the chart. */
export function nextStudyId(specs: StudySpec[]): string {
  let max = 0;
  for (const s of specs) {
    const n = Number.parseInt(s.id.replace(/^s/, ""), 10);
    if (Number.isFinite(n) && n > max) max = n;
  }
  return `s${max + 1}`;
}
