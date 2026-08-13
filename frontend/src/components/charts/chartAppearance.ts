// The two halves of "how the chart is coloured", in one place because both
// candlestick charts (the journal's CandlestickChart and the Simulator/Live
// ReplayChart) have to agree on them — the whole point of the preference is that
// the chart looks the same wherever you opened it from.
//
// The values themselves live in theme.ts and the choice in lib/chartPrefs; this
// is only the seam that hands them to lightweight-charts and to the legend's
// settings panel.
import { ColorType, type IChartApi, type ISeriesApi } from "lightweight-charts";
import type { ChartAppearance } from "../../lib/chartPrefs";
import { candleSchemes, chartInk, chartSurfaces, setActiveInk } from "../../theme";
import type { IndicatorSettingsSpec } from "./IndicatorSettings";

/** The up/down pair for a scheme *on the surface it will be drawn on*.
 *
 *  Two of the six schemes are authored for a dark chart specifically — `mono` is
 *  white-on-dark and would be paper-on-paper — so the light ink carries its own
 *  cut of them (theme.ts). Every read of a candle colour goes through here so
 *  the two charts can't disagree about which cut is in force. */
export function candleColors(a: ChartAppearance): { up: string; down: string } {
  return chartInk(a.surface).candles[a.candles] ?? candleSchemes[a.candles];
}

/** The volume histogram's up/down bars, which are the candles' own distinction
 *  restated underneath them — so they follow the candle scheme rather than
 *  keeping a green/red of their own. Leaving them fixed would quietly defeat the
 *  one scheme with a reason to exist: pick "Blue / orange" for a red-green
 *  deficiency and the volume bars would still be the pair you can't separate.
 *
 *  Half alpha because they sit in the price panel's own gutter and are context,
 *  not a series you read a value off. */
export function volumeColors(a: ChartAppearance): { up: string; down: string } {
  const sch = candleColors(a);
  return { up: withAlpha(sch.up, 0.5), down: withAlpha(sch.down, 0.5) };
}

/** #rrggbb -> rgba(). The schemes are authored as hex because that is how every
 *  other palette in theme.ts is written; only this one consumer needs alpha. */
function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** Recolour a live chart in place.
 *
 *  applyOptions rather than a rebuild, which matters more than it looks: both
 *  charts guard their build effect against re-running because a rebuild throws
 *  away the user's zoom and scroll position — and on the Replay it would throw
 *  away the replay. Changing the background must not cost you the range you had
 *  spent a minute framing.
 *
 *  Sets the active ink first, before it touches a single option. Everything the
 *  canvas primitives draw reads that on their next frame, so the order is what
 *  makes a light↔dark switch land in one pass: ink, then the surface and candles
 *  here, then the caller's own `relight` for the indicator *series* (which carry
 *  their colour in options and have to be told). */
export function applyAppearance(
  chart: IChartApi | null,
  candle: ISeriesApi<"Candlestick"> | null,
  a: ChartAppearance,
): void {
  setActiveInk(chartInk(a.surface));
  const surf = chartSurfaces[a.surface];
  // The overlays drawn *over* the tape as plain text — the identity block, the
  // crosshair readout, the indicator rows — are HTML, and they are light-on-dark
  // by default. On a light surface they would be light-on-light, which is the
  // one part of this that is unreadable rather than merely off-key. Marked on
  // the document rather than on each chart's root: like the active ink, the
  // appearance is one setting for the whole app, so there is no second answer a
  // second chart could need. (Floating *chips* — the badges, the pills, the tool
  // rail — stay dark on purpose: those are chrome laid over the chart, and a
  // dark plate reads on either surface.)
  if (typeof document !== "undefined") {
    document.documentElement.toggleAttribute("data-chart-light", surf.light);
  }
  const sch = candleColors(a);
  chart?.applyOptions({
    layout: { background: { type: ColorType.Solid, color: surf.bg }, textColor: surf.text },
    grid: { vertLines: { color: surf.grid }, horzLines: { color: surf.grid } },
    rightPriceScale: { borderColor: surf.grid },
    timeScale: { borderColor: surf.grid },
  });
  candle?.applyOptions({
    upColor: sch.up,
    downColor: sch.down,
    wickUpColor: sch.up,
    wickDownColor: sch.down,
  });
}

/**
 * Restate the volume bars in the new scheme.
 *
 * Separate from applyAppearance because it is a different kind of operation: a
 * histogram carries its colour per bar, so there is no options call that
 * recolours it — the data has to be handed back.
 *
 * Which bar was up is re-read from the candles rather than inferred from the
 * colour already on the bar. Inferring would mean comparing against the scheme
 * we are leaving, which breaks the moment two changes land back to back; the
 * candle series is the thing that actually knows.
 */
export function recolorVolume(
  candle: ISeriesApi<"Candlestick"> | null,
  vol: ISeriesApi<"Histogram"> | null,
  a: ChartAppearance,
): void {
  if (!candle || !vol) return;
  const { up, down } = volumeColors(a);
  const rose = new Map<unknown, boolean>();
  for (const b of candle.data()) {
    // Whitespace points (gaps) carry no open/close and colour nothing.
    if ("close" in b && "open" in b) rose.set(b.time, b.close >= b.open);
  }
  if (rose.size === 0) return;
  vol.setData(
    vol.data().map((p) => ("value" in p ? { ...p, color: rose.get(p.time) ? up : down } : p)),
  );
}

const SURFACE_OPTIONS = Object.entries(chartSurfaces).map(([value, s]) => ({
  value,
  label: s.label,
}));
const CANDLE_OPTIONS = Object.entries(candleSchemes).map(([value, s]) => ({
  value,
  label: s.label,
}));

/**
 * Named surface + candle pairs.
 *
 * The two knobs below are chosen together — a candle scheme is picked against
 * the surface it will sit on — and the light half made that expensive: landing
 * on a light chart meant setting the background, seeing white candles vanish
 * into it, and then going back for the second knob. A preset is the pair, so
 * the crossing costs one choice instead of two and a mistake in between.
 *
 * Deliberately not exhaustive. These are the pairings worth having a name for,
 * not the 54 the two lists can make; the knobs underneath still reach every one
 * of those, and doing so simply drops the preset to "Custom".
 */
const PRESETS = [
  { value: "dark-classic", label: "Dark · green / red", surface: "charcoal", candles: "classic" },
  { value: "dark-tv", label: "Dark · teal / red", surface: "slate", candles: "tv" },
  { value: "dark-quiet", label: "Dark · quiet", surface: "gunmetal", candles: "muted" },
  { value: "dark-cb", label: "Dark · blue / orange", surface: "midnight", candles: "cb" },
  { value: "light-paper", label: "Light · paper", surface: "paper", candles: "tv" },
  { value: "light-day", label: "Light · daylight", surface: "daylight", candles: "classic" },
  { value: "light-quiet", label: "Light · quiet", surface: "overcast", candles: "muted" },
  { value: "light-cb", label: "Light · blue / orange", surface: "daylight", candles: "cb" },
] as const satisfies readonly {
  value: string;
  label: string;
  surface: ChartAppearance["surface"];
  candles: ChartAppearance["candles"];
}[];

/** "Custom" is an option so the select has something to show when the pair is
 *  one no preset names — but it is not something you can *choose*: picking it
 *  would have to mean "change nothing", and a knob that does nothing is worse
 *  than one that isn't offered. Appended only when it is the current state. */
const CUSTOM = { value: "custom", label: "Custom" };

function presetOf(a: ChartAppearance): string {
  return PRESETS.find((p) => p.surface === a.surface && p.candles === a.candles)?.value ?? CUSTOM.value;
}

/**
 * The appearance panel, in the legend's own settings shape.
 *
 * Selects only, matching the rest of that panel: these are shortlists picked to
 * work against the indicator hues rather than free colours, and a colour well
 * would invite the one change the palette can't absorb (see theme.ts).
 */
export function appearanceSettings(
  a: ChartAppearance,
  onChange: (next: ChartAppearance) => void,
): IndicatorSettingsSpec {
  const preset = presetOf(a);
  return {
    title: "Chart appearance",
    fields: [
      {
        key: "preset",
        label: "Preset",
        help: "Background and candles together — the two are chosen against each other, so crossing between light and dark is one choice, not two.",
        value: preset,
        options: preset === CUSTOM.value ? [...PRESETS, CUSTOM] : PRESETS,
        onChange: (v) => {
          const p = PRESETS.find((x) => x.value === v);
          if (p) onChange({ ...a, surface: p.surface, candles: p.candles });
        },
      },
      {
        key: "surface",
        label: "Background",
        help: "The surface under the chart. The light ones swap in a second cut of every indicator hue — see theme.ts on why that is not a background swap.",
        value: a.surface,
        options: SURFACE_OPTIONS,
        onChange: (v) => onChange({ ...a, surface: v as ChartAppearance["surface"] }),
      },
      {
        key: "candles",
        label: "Candles",
        help: "Up/down bodies and wicks. Blue / orange survives a red-green colour deficiency.",
        value: a.candles,
        options: CANDLE_OPTIONS,
        onChange: (v) => onChange({ ...a, candles: v as ChartAppearance["candles"] }),
        note: "Applies to every chart in the app.",
      },
    ],
  };
}
