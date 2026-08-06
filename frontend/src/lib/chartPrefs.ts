import type { IndicatorKey } from "../components/charts/IndicatorLegend";
import type { ChartResolution } from "./chartTypes";
import { COARSE_POINTER } from "./pointer";

export type IndicatorVisibility = Record<IndicatorKey, boolean>;

const STORAGE_KEY = "chart.indicatorVisibility";

const DEFAULT_VISIBILITY: IndicatorVisibility = {
  vwapGlobex: true,
  vwapNy: true,
  vwapWeekly: true,
  vwapAnchored: true,
  atr: true,
  cvd: true,
  levels: true,
  initialBalance: true,
  // Platform-convention guide lines with no measured edge (the study says 1×+
  // extensions rarely print) — off until asked for.
  ibExtensions: false,
  volumeProfile: true,
  developingProfileGlobex: true,
  developingProfileNy: true,
  // The four 1-minute EMAs each toggle independently (9/20 the fast pullback
  // pair, 50/200 the slower trend reference), all on by default.
  ema9: true,
  ema20: true,
  ema50: true,
  ema200: true,
  // A new oscillator pane — off by default so it doesn't claim chart height until
  // asked for; toggling it on sticks like every other indicator choice.
  rsi: false,
  touches: true,
  va_snaps: true,
  // Your own replay fills. On by default: seeing where you traded is most of
  // the point of replaying the session back.
  replayTrades: true,
  // The tape's big trades. On by default too — a handful of marks a session,
  // and the point of watching a replay is seeing what arrived when.
  bigTrades: true,
  // The composite over the context days, and the nodes read off it. Both on:
  // the layer only exists at all when the setup bar's Composite rule is on and
  // there are prior days loaded, so this toggle is the second switch, not the
  // first one.
  compositeProfile: true,
  compositeNodes: true,
  // The developing NY histogram. On: it is the session you are trading, and it
  // sits in its own gutter rather than over the price action.
  developingVpNy: true,
  // The nodes read off it, which *do* draw over the price action — on all the
  // same, since the setup bar's prominence knob is the first switch and starts
  // at a setting that names only a handful of levels.
  developingVpNyNodes: true,
  // The event bands. On, for the same reason — the strength floor in the setup
  // bar starts at "off", so nothing draws until it is asked for.
  sweepBursts: true,
  absorption: true,
};

// Indicator hide/show is a per-user chart preference, not per-trade state: a
// toggle on one chart carries over to every chart opened afterwards, including
// across reloads. Unknown/absent keys fall back to visible.
export function loadIndicatorVisibility(): IndicatorVisibility {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_VISIBILITY };
    const saved = JSON.parse(raw) as Partial<Record<IndicatorKey, unknown>>;
    const out = { ...DEFAULT_VISIBILITY };
    for (const key of Object.keys(out) as IndicatorKey[]) {
      if (typeof saved[key] === "boolean") out[key] = saved[key];
    }
    return out;
  } catch {
    return { ...DEFAULT_VISIBILITY };
  }
}

export function saveIndicatorVisibility(vis: IndicatorVisibility): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(vis));
  } catch {
    // Private mode / quota — the chart still works, the choice just won't stick.
  }
}

const LEGEND_OPEN_KEY = "chart.legendOpen";

// Whether the on-chart indicator list is expanded. Like the toggles themselves,
// it's a per-user viewing preference that carries across charts and reloads.
//
// Defaults to open on a mouse, so the list stays discoverable — but closed on a
// touchscreen, where the expanded list is ~250px of the same top edge the tool
// buttons live on and a phone has no room to give both. It is a settings list,
// not something you watch, so it is the one that yields. An explicit choice
// still sticks either way.
export function loadLegendOpen(): boolean {
  const stored = localStorage.getItem(LEGEND_OPEN_KEY);
  if (stored != null) return stored !== "0";
  return !COARSE_POINTER;
}

export function saveLegendOpen(open: boolean): void {
  try {
    localStorage.setItem(LEGEND_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}

const RESOLUTION_KEY = "chart.resolution";

// Candle resolution is a viewing preference like the indicator toggles: a choice
// on one strategy chart carries to the next and across reloads. Default is the
// engine's own tick bars — the candles it actually traded.
const RESOLUTIONS: ChartResolution[] = ["tick", "1m", "3m", "5m", "15m"];

export function loadChartResolution(): ChartResolution {
  const v = localStorage.getItem(RESOLUTION_KEY);
  return (RESOLUTIONS as string[]).includes(v ?? "") ? (v as ChartResolution) : "tick";
}

export function saveChartResolution(res: ChartResolution): void {
  try {
    localStorage.setItem(RESOLUTION_KEY, res);
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}

const DIV_TICKS_KEY = "chart.cvdDivTicks";

// CVD-divergence swing size in ticks — how far price must retrace to count a
// swing pivot. Matches the server's DIV_ZZ_TICKS default. The presets span the
// useful range (noisy → sparse); the query param takes any value in [1, 2000].
export const DEFAULT_DIV_TICKS = 120;
export const DIV_TICKS_OPTIONS = [80, 120, 200] as const;

// Like resolution, the swing size is a per-user viewing preference that carries
// across strategy charts and reloads.
export function loadDivTicks(): number {
  const raw = Number(localStorage.getItem(DIV_TICKS_KEY));
  return Number.isFinite(raw) && raw >= 1 ? raw : DEFAULT_DIV_TICKS;
}

export function saveDivTicks(n: number): void {
  try {
    localStorage.setItem(DIV_TICKS_KEY, String(n));
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}
