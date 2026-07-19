import type { IndicatorKey } from "../components/charts/IndicatorLegend";

export type IndicatorVisibility = Record<IndicatorKey, boolean>;

const STORAGE_KEY = "chart.indicatorVisibility";

const DEFAULT_VISIBILITY: IndicatorVisibility = {
  vwapGlobex: true,
  vwapNy: true,
  vwapWeekly: true,
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
  touches: true,
  va_snaps: true,
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
