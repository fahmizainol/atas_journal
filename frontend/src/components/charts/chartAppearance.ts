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
import { candleSchemes, chartSurfaces } from "../../theme";
import type { IndicatorSettingsSpec } from "./IndicatorSettings";

/** The volume histogram's up/down bars, which are the candles' own distinction
 *  restated underneath them — so they follow the candle scheme rather than
 *  keeping a green/red of their own. Leaving them fixed would quietly defeat the
 *  one scheme with a reason to exist: pick "Blue / orange" for a red-green
 *  deficiency and the volume bars would still be the pair you can't separate.
 *
 *  Half alpha because they sit in the price panel's own gutter and are context,
 *  not a series you read a value off. */
export function volumeColors(a: ChartAppearance): { up: string; down: string } {
  const sch = candleSchemes[a.candles];
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
 *  away the user's zoom and scroll position. Changing the background must not
 *  cost you the range you had spent a minute framing. */
export function applyAppearance(
  chart: IChartApi | null,
  candle: ISeriesApi<"Candlestick"> | null,
  a: ChartAppearance,
): void {
  const surf = chartSurfaces[a.surface];
  const sch = candleSchemes[a.candles];
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
 * The appearance panel, in the legend's own settings shape.
 *
 * Two selects, matching the rest of that panel: these are shortlists picked to
 * work against the indicator hues rather than free colours, and a colour well
 * would invite the one change the palette can't absorb (see theme.ts on why
 * every surface here is dark).
 */
export function appearanceSettings(
  a: ChartAppearance,
  onChange: (next: ChartAppearance) => void,
): IndicatorSettingsSpec {
  return {
    title: "Chart appearance",
    fields: [
      {
        key: "surface",
        label: "Background",
        help: "The surface under the chart. All dark — the indicator hues are picked against one (theme.ts).",
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
