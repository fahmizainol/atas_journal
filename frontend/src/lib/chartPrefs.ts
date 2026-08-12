import type { IndicatorKey } from "../components/charts/IndicatorLegend";
import { candleSchemes, chartSurfaces } from "../theme";
import type { ChartResolution } from "./chartTypes";
import { COARSE_POINTER } from "./pointer";

export type IndicatorVisibility = Record<IndicatorKey, boolean>;

const STORAGE_KEY = "chart.indicatorVisibility";

/**
 * Where one pane's copy of a per-chart display preference lives.
 *
 * Only the *display* preferences are split this way — which layers are drawn and
 * whether the legend is expanded. A context pane exists to be read at a glance
 * and wants a fraction of the primary's layers, so sharing one blob between them
 * makes the two panes fight over the same switch. The rest stay global on
 * purpose: appearance (surface + candle scheme) because a split showing two
 * colour schemes is a worse chart, not a more configurable one, and the drawings
 * because a price you marked is a price, whatever bucketing the pane draws it on.
 *
 * The primary pane passes nothing and so writes the key it always wrote — which
 * is what stops this change resetting every existing user's indicator choices.
 */
function paneKey(base: string, pane?: string): string {
  return pane ? `${base}.${pane}` : base;
}

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
  // The event bands. Off, and the only layers here that are: they are a proxy
  // that measured *negative* against the very levels they sit next to (both land
  // further from a frozen composite's than the session's own volume does, over
  // 40 and 120 sessions, sign never flipping), and ~19 a session drawn by default
  // would read as a signal by sheer presence. These rows used to be gated by a
  // strength floor that started at zero; the floor is gone — the thresholds live
  // in the engine now — so the switch is here, where every other layer's is.
  sweepBursts: false,
  absorption: false,
  // The bar-range vol pane (ATR + developing median vs the 50t stop). On by
  // default: it exists to be read *before* the first trade, which is exactly
  // when nobody remembers to switch an indicator on.
  volRuler: true,
  // Modern VWAP and its triggers. Off, on the same grounds as the event bands
  // above: the swing anchor is the one construct on our shelf that is neither
  // built nor falsified, and an unfalsified layer drawn by default reads as a
  // signal by sheer presence. Its own study page says the author's six-year test
  // came back at zero. Switch it on to look at it, not to trade off it.
  modernVwap: false,
  modernVwapSignals: false,
};

// Indicator hide/show is a per-user chart preference, not per-trade state: a
// toggle on one chart carries over to every chart opened afterwards, including
// across reloads. Unknown/absent keys fall back to visible.
export function loadIndicatorVisibility(pane?: string): IndicatorVisibility {
  try {
    const raw = localStorage.getItem(paneKey(STORAGE_KEY, pane));
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

export function saveIndicatorVisibility(vis: IndicatorVisibility, pane?: string): void {
  try {
    localStorage.setItem(paneKey(STORAGE_KEY, pane), JSON.stringify(vis));
  } catch {
    // Private mode / quota — the chart still works, the choice just won't stick.
  }
}

const APPEARANCE_KEY = "chart.appearance";

export type ChartSurfaceKey = keyof typeof chartSurfaces;
export type CandleSchemeKey = keyof typeof candleSchemes;

/** How the chart itself is coloured: the surface under everything, and the
 *  candles on it. Deliberately only these two — the indicator hues carry
 *  measured distinctions and aren't the user's to move (see theme.ts). */
export interface ChartAppearance {
  surface: ChartSurfaceKey;
  candles: CandleSchemeKey;
}

// The chart as it has always looked, so an existing user sees no change until
// they ask for one.
export const DEFAULT_APPEARANCE: ChartAppearance = { surface: "charcoal", candles: "classic" };

// Sticky and global like the indicator toggles: the surface you picked on a
// replay is the surface a trade review opens in. One key holding both halves
// rather than two, because they are chosen together — a candle scheme is picked
// against the surface it will sit on.
export function loadChartAppearance(): ChartAppearance {
  try {
    const raw = localStorage.getItem(APPEARANCE_KEY);
    if (!raw) return { ...DEFAULT_APPEARANCE };
    const saved = JSON.parse(raw) as Partial<Record<keyof ChartAppearance, unknown>>;
    return {
      // A key that no longer exists (a scheme renamed, a hand-edited value) falls
      // back rather than colouring the chart `undefined`.
      surface:
        typeof saved.surface === "string" && saved.surface in chartSurfaces
          ? (saved.surface as ChartSurfaceKey)
          : DEFAULT_APPEARANCE.surface,
      candles:
        typeof saved.candles === "string" && saved.candles in candleSchemes
          ? (saved.candles as CandleSchemeKey)
          : DEFAULT_APPEARANCE.candles,
    };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

export function saveChartAppearance(a: ChartAppearance): void {
  try {
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(a));
  } catch {
    // Private mode / quota — the chart still recolours, the choice just won't stick.
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
export function loadLegendOpen(pane?: string): boolean {
  const stored = localStorage.getItem(paneKey(LEGEND_OPEN_KEY, pane));
  if (stored != null) return stored !== "0";
  // A secondary pane starts collapsed whatever the pointer is: it is there to be
  // glanced at, and 250px of layer list over a half-width chart is most of it.
  return !COARSE_POINTER && !pane;
}

export function saveLegendOpen(open: boolean, pane?: string): void {
  try {
    localStorage.setItem(paneKey(LEGEND_OPEN_KEY, pane), open ? "1" : "0");
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}

const SOUND_KEY = "chart.sound";

// Whether the order cues are audible (see lib/orderSound). On by default: the
// cues exist because a fill is the one thing on these pages you should not have
// to be looking at the blotter to notice, and a confirmation sound nobody asked
// for is one keypress away from off.
export function loadSoundOn(): boolean {
  return localStorage.getItem(SOUND_KEY) !== "0";
}

export function saveSoundOn(on: boolean): void {
  try {
    localStorage.setItem(SOUND_KEY, on ? "1" : "0");
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}

const PACK_KEY = "chart.soundPack";

/** Which recorded set the order cues play: short platform tones, or a voice
 *  saying what happened. See lib/orderSound. */
export type SoundPack = "tones" | "voice";

// Tones by default — they are shorter than the gesture that triggers them,
// which a spoken cue is not.
export function loadSoundPack(): SoundPack {
  return localStorage.getItem(PACK_KEY) === "voice" ? "voice" : "tones";
}

export function saveSoundPack(p: SoundPack): void {
  try {
    localStorage.setItem(PACK_KEY, p);
  } catch {
    // Private mode / quota — the switch still works, the choice just won't stick.
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

const QUICK_DOCK_KEY = "chart.quickDockPos";

/** Where the market-order window has been dragged to, in px from the top-left of
 *  the chart it floats over. `null` means it has never been moved (or has been
 *  put back), and it parks itself at the foot of the tape. */
export interface DockPos {
  x: number;
  y: number;
}

// One key for both charts. The window is the same instrument on Replay and Live
// — the whole reason to move it is to get it out of the way of where *you* look,
// and that doesn't change when the clock does. Positions are re-clamped into the
// chart on load, so a spot saved on a wide monitor doesn't strand the buttons
// off-screen on a laptop.
export function loadDockPos(): DockPos | null {
  try {
    const raw = localStorage.getItem(QUICK_DOCK_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<Record<keyof DockPos, unknown>>;
    if (typeof saved.x !== "number" || typeof saved.y !== "number") return null;
    if (!Number.isFinite(saved.x) || !Number.isFinite(saved.y)) return null;
    return { x: saved.x, y: saved.y };
  } catch {
    return null;
  }
}

export function saveDockPos(pos: DockPos | null): void {
  try {
    if (pos) localStorage.setItem(QUICK_DOCK_KEY, JSON.stringify(pos));
    else localStorage.removeItem(QUICK_DOCK_KEY);
  } catch {
    // Private mode / quota — the window still moves, it just won't remember.
  }
}

const DRAWINGS_KEY = "chart.drawings";

/** What a session's hand-drawn tools boil down to, for coming back to the same
 *  day: fixed-range profiles as the bar times that bound them, the ⚓ anchor as
 *  a bar time, and the horizontal price lines (with whether each alert is still
 *  live — a line the tape already crossed comes back dimmed, not re-armed). The
 *  ruler is deliberately not here: a measurement is a question you were asking
 *  at the time, not a level you keep. */
export interface SessionDrawings {
  ranges: { from: number; to: number }[];
  anchor: number | null;
  hlines: { price: number; armed: boolean }[];
}

/** How many sessions' drawings are kept. Enough that every day you might come
 *  back to still has its levels; bounded so a year of replays doesn't grow a
 *  localStorage entry nobody can see. Oldest-touched go first. */
const DRAWINGS_CAP = 40;

type DrawingsStore = Record<string, SessionDrawings & { at: number }>;

function readDrawingsStore(): DrawingsStore {
  try {
    const raw = localStorage.getItem(DRAWINGS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as DrawingsStore) : {};
  } catch {
    return {};
  }
}

/** The drawings last saved for a session key (`SYMBOL|date`), or null. */
export function loadDrawings(key: string): SessionDrawings | null {
  const store = readDrawingsStore();
  const d = store[key];
  if (!d || !Array.isArray(d.ranges) || !Array.isArray(d.hlines)) return null;
  return { ranges: d.ranges, anchor: typeof d.anchor === "number" ? d.anchor : null, hlines: d.hlines };
}

/** Save (or, when everything was erased, forget) a session's drawings. */
export function saveDrawings(key: string, d: SessionDrawings): void {
  try {
    const store = readDrawingsStore();
    if (d.ranges.length === 0 && d.anchor == null && d.hlines.length === 0) {
      if (!(key in store)) return;
      delete store[key];
    } else {
      store[key] = { ...d, at: Date.now() };
      const keys = Object.keys(store);
      if (keys.length > DRAWINGS_CAP) {
        keys
          .sort((a, b) => (store[a].at ?? 0) - (store[b].at ?? 0))
          .slice(0, keys.length - DRAWINGS_CAP)
          .forEach((k) => delete store[k]);
      }
    }
    localStorage.setItem(DRAWINGS_KEY, JSON.stringify(store));
  } catch {
    // Private mode / quota — the drawings still work, they just won't come back.
  }
}

const LIVE_HISTORY_DAYS_KEY = "live.historyDays";

// How many prior sessions the Live page glues in front of the day, remembered.
//
// It sticks because of what it costs rather than because it is a taste: the
// context days are whole tick tapes, they are fetched and decoded *before* the
// live tape may start (rows seeded in front of it shift every index behind them,
// and an order's `idx` is a position in that array), and each one is a
// multi-megabyte parse. Five is a rich chart and a slow open; one is a fast one.
// Resetting to five on every reload made that choice unusable for anyone who had
// made it.
export const DEFAULT_LIVE_HISTORY_DAYS = 5;

export function loadLiveHistoryDays(): number {
  const raw = localStorage.getItem(LIVE_HISTORY_DAYS_KEY);
  if (raw == null) return DEFAULT_LIVE_HISTORY_DAYS;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n <= 10 ? n : DEFAULT_LIVE_HISTORY_DAYS;
}

export function saveLiveHistoryDays(n: number): void {
  try {
    localStorage.setItem(LIVE_HISTORY_DAYS_KEY, String(n));
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}
