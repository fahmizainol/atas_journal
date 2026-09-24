import type { IndicatorKey } from "../components/charts/IndicatorLegend";
import { candleSchemes, chartSurfaces } from "../theme";
import type { ChartResolution } from "./chartTypes";
import { modernVwapParams, type ModernVwapParams } from "./modernVwap";
import { dynamicSwingVwapParams, type DsvParams } from "./dynamicSwingVwap";
import { clampExternalChart, type ExternalChartParams } from "./externalChart";
import { clampHtfTrend, type HtfTrendParams } from "./htfTrend";
import { rankedZonesParams, type RankedZonesParams } from "./rankedZones";
import { DEFAULT_SHELF_PARAMS, type ShelfParams } from "./volumeShelf";
import type { ShelfField } from "../components/charts/VolumeShelfPrimitive";
import {
  CVD_OSC_FRACTAL_OPTIONS,
  CVD_OSC_MODE_OPTIONS,
  CVD_OSC_PERIOD_OPTIONS,
  DEFAULT_CVD_OSC,
  type CvdOscMode,
  type CvdOscParams,
} from "./cvdOsc";
import { COARSE_POINTER } from "./pointer";
import type { CompositeRule, CompositeSpan } from "./compositeProfile";
import {
  BIG_LOT_OPTIONS,
  COMPOSITE_RULES,
  COMPOSITE_SPANS,
  EVENT_FILL_OPTIONS,
  EVENT_FLOOR_OPTIONS,
  EVENT_LABEL_ST_OPTIONS,
  NODE_PROM_OPTIONS,
  SIM_SPEEDS,
  eventTuning,
} from "./simPrefs";
import { DEFAULT_BIG_LOTS, DEFAULT_EVENT_TUNING, type EventTuning } from "./replayEngine";
import type { StudySpec } from "./studies";
import { DEFAULT_TIMEFRAME_ID, isTimeframeId } from "./timeframes";
import { sanitizeHistoryDayOverrides, type HistoryDayOverrides } from "./contextDays";
import { DEFAULT_NODE_PROM } from "./volumeProfile";
import {
  FLAG_SIGMAS,
  LANE_SCALES,
  LANE_WINDOW_MINUTES,
  LANE_WINDOWS,
  type LaneScale,
  type LaneWindow,
} from "./deltaFlow";

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
  // The windowed-delta oscillator and the divergences read off it. Off, on the
  // same grounds as Modern VWAP below: it is a borrowed, unfalsified construct,
  // and a divergence indicator drawn by default reads as a signal by sheer
  // presence — it is also a third sub-pane, which is real chart height.
  cvdOsc: false,
  levels: true,
  initialBalance: true,
  // Platform-convention guide lines with no measured edge (the study says 1×+
  // extensions rarely print) — off until asked for.
  ibExtensions: false,
  volumeProfile: true,
  developingProfileGlobex: true,
  developingProfileNy: true,
  // The weekly value area. Off: a third VAH/POC/VAL trio drawn by default is
  // real price-scale clutter, and unlike the session areas nothing adopted
  // reads it yet — switch it on to look at it (and the Modern VWAP poc anchor
  // can anchor on it whether or not it is drawn).
  developingProfileWeekly: false,
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
  volumeShelf: false,
  volumeShelfBoxes: true,
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
  // The Zeiierman swing-flip VWAP, off for the same reason: a second unfalsified
  // swing construct, and two of them drawn by default would read as a consensus.
  dynamicSwingVwap: false,
  // The coarser bar drawn over this one. Off, on different grounds from the two
  // above — it makes no claim to falsify, it is the same bars regrouped — but it
  // draws a rectangle round every fifteen minutes of the chart, and a layer that
  // covers the price action wholesale is one you should have asked for.
  externalChart: false,
  // Off like the External Chart: it washes the whole pane, and a layer that
  // covers the price action wholesale is one you should have asked for. Sticky
  // once switched on.
  htfTrend: false,
  // Off, and for the strongest version of the reason the three above are: it is
  // a *ranking*, and a ranked list with percentages on it reads as a claim about
  // which level holds. Nothing here has established that. Switch it on to look
  // at where price has turned before, not to size off the top row.
  rankedZones: false,
  // On: a line at 08:30 costs nothing to read past, and a trade taken into CPI
  // without knowing is the one thing this layer exists to stop.
  econEvents: true,
  // Off: unvalidated, and the walls are a Black-Scholes recompute 5–9% off
  // Cboe's own greeks — context to look at, not levels to trade.
  gexLevels: false,
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

const LEVEL_PANEL_KEY = "chart.levelPanel";

// Whether the near-price levels panel is unfolded (see LevelApproach). The same
// kind of preference as the legend and the tool rail, and it defaults the same
// way and for the same reason: on a mouse the rows are cheap and discoverable,
// on a touchscreen they are a box in the axis gutter of a chart that has little
// enough of one. An explicit choice sticks either way.
//
// Not per pane, unlike the legend: only the primary pane offers this panel at
// all (the host passes `levelPanel`), so there is nothing to key.
export function loadLevelPanelOpen(): boolean {
  const stored = localStorage.getItem(LEVEL_PANEL_KEY);
  if (stored != null) return stored !== "0";
  return !COARSE_POINTER;
}

export function saveLevelPanelOpen(open: boolean): void {
  try {
    localStorage.setItem(LEVEL_PANEL_KEY, open ? "1" : "0");
  } catch {
    // Private mode / quota — the fold still works, the choice just won't stick.
  }
}

const TOOLS_OPEN_KEY = "chart.toolsOpen";

// Whether the in-canvas tool rail is unfolded. The same kind of preference as
// the legend above, and it defaults the same way and for the same reason: on a
// mouse the column of icons is discoverable and cheap, on a touchscreen it is
// ~44px × nine buttons of the chart's left edge — over a third of a phone's
// tape, covering the one thing the page exists to show. So the phone starts
// folded to a single button and an explicit choice sticks either way.
//
// Not per pane: a page that draws one chart draws one of these rails (the
// four-pane terminal uses ChartToolRail instead), so there is nothing to key.
export function loadToolsOpen(): boolean {
  const stored = localStorage.getItem(TOOLS_OPEN_KEY);
  if (stored != null) return stored !== "0";
  return !COARSE_POINTER;
}

export function saveToolsOpen(open: boolean): void {
  try {
    localStorage.setItem(TOOLS_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Private mode / quota — the fold still works, the choice just won't stick.
  }
}

const STUDIES_KEY = "chart.studies";

/**
 * The community studies picked off the topbar (see lib/studies).
 *
 * Per pane, like the visibility map and for the same reason: a pane is a
 * question, and the study you want on the 5m is usually not the one you want on
 * the hourly beside it. The primary pane passes nothing and so writes the key it
 * always wrote, which is what stops this split resetting the list anyone already
 * has. Shared across both chart pages, again like the visibility map — an RSI
 * added on the replay is an RSI you want on the live chart.
 *
 * Stored as the specs, not as anything computed: an export name, the inputs the
 * user chose and whether its eye is shut. A study whose upstream export is
 * renamed comes back as a row the legend marks and the chart skips, rather than
 * as a crash.
 */
export function loadStudies(pane?: string): StudySpec[] {
  try {
    const raw = localStorage.getItem(paneKey(STUDIES_KEY, pane));
    if (!raw) return [];
    const saved = JSON.parse(raw) as unknown;
    if (!Array.isArray(saved)) return [];
    return saved.filter(
      (s): s is StudySpec =>
        !!s &&
        typeof s === "object" &&
        typeof (s as StudySpec).id === "string" &&
        typeof (s as StudySpec).key === "string",
    );
  } catch {
    return [];
  }
}

export function saveStudies(specs: StudySpec[], pane?: string): void {
  try {
    localStorage.setItem(paneKey(STUDIES_KEY, pane), JSON.stringify(specs));
  } catch {
    // Private mode / quota — the studies still draw, the choice just won't stick.
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

const TIMEFRAME_KEY = "chart.timeframe";

// Which bar the chart is bucketed at, for the charts with no run config to keep
// it in — currently Recall's card. Sticky-global on the same grounds as the tape
// and profile knobs above: the bucketing you read a trade at is a statement about
// how you read a chart, not about the card in front of you, and a deck is a
// sequence of cards — re-picking the timeframe on every rep is the whole cost.
//
// The Simulator and the Charts workspace keep theirs in their own prefs blob
// (lib/simPrefs), where it sits beside the tape and layers of one saved setup,
// and per pane besides. Nothing here touches `sim.prefs`.
//
// Clamped on read, like every loader here: a hand-edited id must not leave the
// picker naming a bar that cannot be drawn. "Can be drawn" rather than "is one
// of the eight" — a bucketing you typed yourself is as real as a built-in, and
// clamping to the list would have thrown it away on the next reload.
export function loadTimeframeId(): string {
  try {
    const raw = localStorage.getItem(TIMEFRAME_KEY);
    return isTimeframeId(raw) ? raw : DEFAULT_TIMEFRAME_ID;
  } catch {
    return DEFAULT_TIMEFRAME_ID;
  }
}

export function saveTimeframeId(id: string): void {
  try {
    localStorage.setItem(TIMEFRAME_KEY, id);
  } catch {
    // Private mode / quota — the picker still works, the choice just won't stick.
  }
}

const SPEED_KEY = "chart.replaySpeed";

// How fast the tape runs, for the charts with no run config to keep it in —
// currently Recall's card. Sticky-global for exactly the reason the bucketing
// above is: a deck is a sequence of cards, and a speed re-picked on every rep is
// re-picked forever. The Simulator keeps its own in `sim.prefs` beside the
// ticket it trades with; nothing here touches that store.
//
// Clamped to the offered list on read, like the timeframe: a stored number that
// isn't one of the presets would leave the transport's <select> blank.
export function loadReplaySpeed(): number {
  try {
    const raw = Number(localStorage.getItem(SPEED_KEY));
    return SIM_SPEEDS.includes(raw) ? raw : 1;
  } catch {
    return 1;
  }
}

export function saveReplaySpeed(speed: number): void {
  try {
    localStorage.setItem(SPEED_KEY, String(speed));
  } catch {
    // Private mode / quota — the picker still works, the choice just won't stick.
  }
}

const VWAP_FILL_KEY = "chart.vwapFill";

/** The session anchors whose ±1σ→±2σ wash is tunable, by their key in
 *  `theme.vwapPalette`. The ⚓ tool is not one of them: it is drawn one at a time,
 *  by hand, at the place you asked about — the crowding these weights exist to
 *  relieve is what the three *fixed* anchors do to each other when all three are
 *  up on the same candles. */
export type VwapFillAnchor = "globex" | "ny" | "weekly";

/** Multipliers on the surface's own `bandAlpha` (theme.ts), which already
 *  differs between the light and dark charts. A weight, not an alpha, so the
 *  choice survives crossing surfaces — "half as loud as this chart's normal" is
 *  a statement that still means something over there. */
export const VWAP_FILL_OPTIONS = [1, 0.6, 0.3, 0] as const;

export type VwapFillWeights = Record<VwapFillAnchor, number>;

/** As they have always been drawn — the knob starts where the chart already was. */
const DEFAULT_VWAP_FILL: VwapFillWeights = { globex: 1, ny: 1, weekly: 1 };

/** Sticky and global, on the same argument as the surface and the candle scheme
 *  above: how heavy a band fill is, is a statement about how you read a band, not
 *  about the session you happened to set it on. Clamped to the offered list on
 *  read, so a hand-edited value can't put a wash somewhere the panel can't
 *  retrieve it from. */
export function loadVwapFill(): VwapFillWeights {
  try {
    const raw = localStorage.getItem(VWAP_FILL_KEY);
    const s = raw ? (JSON.parse(raw) as Partial<VwapFillWeights>) : null;
    const pick = (v: unknown, d: number) =>
      VWAP_FILL_OPTIONS.includes(v as (typeof VWAP_FILL_OPTIONS)[number]) ? (v as number) : d;
    if (!s || typeof s !== "object") return { ...DEFAULT_VWAP_FILL };
    return {
      globex: pick(s.globex, DEFAULT_VWAP_FILL.globex),
      ny: pick(s.ny, DEFAULT_VWAP_FILL.ny),
      weekly: pick(s.weekly, DEFAULT_VWAP_FILL.weekly),
    };
  } catch {
    return { ...DEFAULT_VWAP_FILL };
  }
}

export function saveVwapFill(w: VwapFillWeights): void {
  try {
    localStorage.setItem(VWAP_FILL_KEY, JSON.stringify(w));
  } catch {
    // Private mode / quota — the knob still works, the choice just won't stick.
  }
}

const VWAP_BANDS_KEY = "chart.vwapBands";

/** Which σ rings a fixed anchor draws.
 *
 *  The mid is not on this list and never will be: it *is* the anchored VWAP, and
 *  an anchor without it is not a quieter anchor, it is the anchor gone — which is
 *  what the legend's own eye already does. What is on the list is the envelope,
 *  and the reason to cut it is the same measurement the ReplayChart's demoted
 *  lines came out of (docs/research/chart-price-scale-occupancy.md): three
 *  anchors up is twelve dashed lines through the price action, and most sessions
 *  are read off one ring, not two.
 *
 *  Both rings of a σ are drawn or neither. Nobody reads +1σ without −1σ; that is
 *  one envelope, drawn twice because price can be on either side of it. */
export const VWAP_BAND_OPTIONS = ["both", "s1", "s2", "none"] as const;

export type VwapBandChoice = (typeof VWAP_BAND_OPTIONS)[number];

export type VwapBandChoices = Record<VwapFillAnchor, VwapBandChoice>;

/** As they have always been drawn — the knob starts where the chart already was. */
const DEFAULT_VWAP_BANDS: VwapBandChoices = { globex: "both", ny: "both", weekly: "both" };

/** What a choice actually draws: the two rings, and whether the ±1σ→±2σ wash has
 *  both of its edges.
 *
 *  The wash is spread between the rings, so it is only honest when both are up.
 *  Shade to a ±2σ line you asked to hide and the wash's own edge draws that line
 *  back in, as a colour boundary instead of a dash — the band you switched off is
 *  still on the chart, and now it is on it in a way the legend doesn't name. So
 *  the wash goes with the pair, and the fill weight beside it says how loud it is
 *  on the setting that has one. */
export function vwapBandsShown(
  choice: VwapBandChoice,
  region: VwapFillRegion = "outer",
): {
  s1: boolean;
  s2: boolean;
  fill: boolean;
} {
  const s1 = choice === "both" || choice === "s1";
  return {
    s1,
    s2: choice === "both" || choice === "s2",
    // The inner wash runs −1σ→+1σ, so it has both of its edges whenever the ±1σ
    // ring is up — the ±2σ ring is no part of it.
    fill: region === "inner" ? s1 : choice === "both",
  };
}

/** What the legend row calls this anchor's envelope — the label has always
 *  carried "±1σ ±2σ", and a row that says it draws a ring it isn't drawing is
 *  worse than a row that says nothing. Empty when the mid is all that's left. */
export function vwapBandLabel(choice: VwapBandChoice): string {
  const s = vwapBandsShown(choice);
  return [s.s1 ? "±1σ" : null, s.s2 ? "±2σ" : null].filter(Boolean).join(" ");
}

/** Sticky and global, on the same argument as the fill weights above: which rings
 *  of an envelope you read is a statement about how you read a band, not about
 *  the session you happened to set it on. Clamped to the offered list on read. */
export function loadVwapBands(): VwapBandChoices {
  try {
    const raw = localStorage.getItem(VWAP_BANDS_KEY);
    const s = raw ? (JSON.parse(raw) as Partial<VwapBandChoices>) : null;
    const pick = (v: unknown, d: VwapBandChoice) =>
      VWAP_BAND_OPTIONS.includes(v as VwapBandChoice) ? (v as VwapBandChoice) : d;
    if (!s || typeof s !== "object") return { ...DEFAULT_VWAP_BANDS };
    return {
      globex: pick(s.globex, DEFAULT_VWAP_BANDS.globex),
      ny: pick(s.ny, DEFAULT_VWAP_BANDS.ny),
      weekly: pick(s.weekly, DEFAULT_VWAP_BANDS.weekly),
    };
  } catch {
    return { ...DEFAULT_VWAP_BANDS };
  }
}

export function saveVwapBands(c: VwapBandChoices): void {
  try {
    localStorage.setItem(VWAP_BANDS_KEY, JSON.stringify(c));
  } catch {
    // Private mode / quota — the knob still works, the choice just won't stick.
  }
}

const VWAP_FILL_REGION_KEY = "chart.vwapFillRegion";

/** Which region of a fixed anchor's envelope the wash covers. "outer" is the
 *  two ±1σ→±2σ ribbons it has always drawn, leaving the mid-to-±1σ clear;
 *  "inner" is the one −1σ→+1σ ribbon — the anchor's value area — with the outer
 *  rings left as lines. */
export const VWAP_FILL_REGION_OPTIONS = ["outer", "inner"] as const;

export type VwapFillRegion = (typeof VWAP_FILL_REGION_OPTIONS)[number];

export type VwapFillRegions = Record<VwapFillAnchor, VwapFillRegion>;

/** As they have always been drawn — the knob starts where the chart already was. */
const DEFAULT_VWAP_FILL_REGION: VwapFillRegions = { globex: "outer", ny: "outer", weekly: "outer" };

/** Sticky and global, on the same argument as the fill weights and the rings.
 *  Clamped to the offered list on read. */
export function loadVwapFillRegion(): VwapFillRegions {
  try {
    const raw = localStorage.getItem(VWAP_FILL_REGION_KEY);
    const s = raw ? (JSON.parse(raw) as Partial<VwapFillRegions>) : null;
    const pick = (v: unknown, d: VwapFillRegion) =>
      VWAP_FILL_REGION_OPTIONS.includes(v as VwapFillRegion) ? (v as VwapFillRegion) : d;
    if (!s || typeof s !== "object") return { ...DEFAULT_VWAP_FILL_REGION };
    return {
      globex: pick(s.globex, DEFAULT_VWAP_FILL_REGION.globex),
      ny: pick(s.ny, DEFAULT_VWAP_FILL_REGION.ny),
      weekly: pick(s.weekly, DEFAULT_VWAP_FILL_REGION.weekly),
    };
  } catch {
    return { ...DEFAULT_VWAP_FILL_REGION };
  }
}

export function saveVwapFillRegion(r: VwapFillRegions): void {
  try {
    localStorage.setItem(VWAP_FILL_REGION_KEY, JSON.stringify(r));
  } catch {
    // Private mode / quota — the knob still works, the choice just won't stick.
  }
}

const MODERN_VWAP_KEY = "chart.modernVwap";

// Modern VWAP's parameters for the charts that have no run config to keep them
// in. The Simulator and the Charts workspace store theirs in their own prefs
// blob (lib/simPrefs), because there they sit beside the tape and the layers of
// one saved setup; the Interactions Lab has no such blob and its chart is the
// only thing on the page with settings, so the sticky-global shape the
// visibility toggles and the appearance already use is the right one.
//
// Read back through `modernVwapParams`, which clamps every field to the options
// the knobs actually offer — a stored value from an older build (or a hand-edited
// one) must not put the indicator in a state the panel can't get it out of.
export function loadModernVwapParams(): ModernVwapParams {
  try {
    const raw = localStorage.getItem(MODERN_VWAP_KEY);
    return modernVwapParams(raw ? JSON.parse(raw) : null);
  } catch {
    return modernVwapParams(null);
  }
}

export function saveModernVwapParams(p: ModernVwapParams): void {
  try {
    localStorage.setItem(MODERN_VWAP_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const DYNAMIC_SWING_VWAP_KEY = "chart.dynamicSwingVwap";

/** The Zeiierman VWAP's parameters, stored exactly as Modern VWAP's above and
 *  for the same reasons — including the clamp on read. */
export function loadDynamicSwingVwapParams(): DsvParams {
  try {
    const raw = localStorage.getItem(DYNAMIC_SWING_VWAP_KEY);
    return dynamicSwingVwapParams(raw ? JSON.parse(raw) : null);
  } catch {
    return dynamicSwingVwapParams(null);
  }
}

export function saveDynamicSwingVwapParams(p: DsvParams): void {
  try {
    localStorage.setItem(DYNAMIC_SWING_VWAP_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const EXTERNAL_CHART_KEY = "chart.externalChart";

/** The External Chart overlay's parameters — the external period, whether the
 *  high/low box and the wash are drawn, which side of the candles it sits on,
 *  and the two hues when they aren't the surface's. Stored and clamped exactly
 *  as the two VWAPs above, and sticky-global for the same reason: it is one
 *  answer to "what is the coarser bar I want framing this one", not a fact about
 *  a particular session. */
export function loadExternalChartParams(): ExternalChartParams {
  try {
    const raw = localStorage.getItem(EXTERNAL_CHART_KEY);
    return clampExternalChart(raw ? JSON.parse(raw) : null);
  } catch {
    return clampExternalChart(null);
  }
}

export function saveExternalChartParams(p: ExternalChartParams): void {
  try {
    localStorage.setItem(EXTERNAL_CHART_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const HTF_TREND_KEY = "chart.htfTrend";

/** The HTF trend layer's parameters (lib/htfTrend) — which frames, the EMA
 *  length, lines on/off and the tint style. Sticky-global like the External
 *  Chart's: it is one answer to "which higher frames do I lean on". */
export function loadHtfTrendParams(): HtfTrendParams {
  try {
    const raw = localStorage.getItem(HTF_TREND_KEY);
    return clampHtfTrend(raw ? JSON.parse(raw) : null);
  } catch {
    return clampHtfTrend(null);
  }
}

export function saveHtfTrendParams(p: HtfTrendParams): void {
  try {
    localStorage.setItem(HTF_TREND_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const RANKED_ZONES_KEY = "chart.rankedZones";

/** The ranked S/R zones' parameters. Sticky-global like the two VWAPs and the
 *  External Chart above, and for the same reason: "how long a pivot, how wide a
 *  zone, how many of them" is one answer about how you read levels, not a fact
 *  about a particular session. */
export function loadRankedZonesParams(): RankedZonesParams {
  try {
    const raw = localStorage.getItem(RANKED_ZONES_KEY);
    return rankedZonesParams(raw ? JSON.parse(raw) : null);
  } catch {
    return rankedZonesParams(null);
  }
}

export function saveRankedZonesParams(p: RankedZonesParams): void {
  try {
    localStorage.setItem(RANKED_ZONES_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const PROFILE_KNOBS_KEY = "chart.profileKnobs";

/** The three knobs the two histogram layers are read at: how the prior sessions
 *  are composited, how much of each one goes in, and how prominent a hump must
 *  be to be named. Kept together because they are one question — what the
 *  volume distributions on this chart are being asked. */
export interface ProfileKnobs {
  composite: CompositeRule;
  compositeSpan: CompositeSpan;
  /** Shared by the composite's node reader and the session's, exactly as it is
   *  on the replay chart: one question asked of two layers. */
  nodeProm: number;
}

const DEFAULT_PROFILE_KNOBS: ProfileKnobs = {
  // The measured rule on NQ: only the days still in one auction. See
  // lib/compositeProfile and the composite-profile study.
  composite: "balance",
  // The whole session, open to close — a shelf built overnight is a price the
  // market has already agreed on.
  compositeSpan: "globex",
  nodeProm: DEFAULT_NODE_PROM,
};

// The same sticky-global shape as the visibility map and the Modern VWAP
// parameters above, and for the same reason: the journal's charts have no run
// config to hang settings off, and a composite rule set on one session's chart
// is a statement about how you read a composite, not about that session.
//
// The Simulator keeps its own copy in lib/simPrefs — deliberately, since there
// the rule sits beside the context-day count it is applied to, and one setup's
// number of days is not another's.
export function loadProfileKnobs(): ProfileKnobs {
  const d = { ...DEFAULT_PROFILE_KNOBS };
  try {
    const raw = localStorage.getItem(PROFILE_KNOBS_KEY);
    if (!raw) return d;
    const s = JSON.parse(raw) as Partial<Record<keyof ProfileKnobs, unknown>>;
    // Clamped to what the knobs actually offer, like the Modern VWAP params: a
    // stored value from an older build must not put a layer in a state its
    // panel can't get it out of.
    if (COMPOSITE_RULES.includes(s.composite as CompositeRule))
      d.composite = s.composite as CompositeRule;
    if (COMPOSITE_SPANS.includes(s.compositeSpan as CompositeSpan))
      d.compositeSpan = s.compositeSpan as CompositeSpan;
    if (NODE_PROM_OPTIONS.includes(s.nodeProm as number)) d.nodeProm = s.nodeProm as number;
    return d;
  } catch {
    return d;
  }
}

export function saveProfileKnobs(k: ProfileKnobs): void {
  try {
    localStorage.setItem(PROFILE_KNOBS_KEY, JSON.stringify(k));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const SHELF_PARAMS_KEY = "chart.shelfParams";

/** How wide a window the shelf reading covers, and how remarkable a band has to
 *  be. Offered as a short list rather than a free number for the same reason the
 *  node prominence is: these are settings to *feel out* against a chart, and a
 *  slider with 400 positions is a worse instrument for that than five. */
export const SHELF_WINDOW_OPTIONS = [15, 30, 60, 120] as const;
export const SHELF_ZMIN_OPTIONS = [1.5, 2, 2.5, 3, 4] as const;
export const SHELF_HOLD_OPTIONS = [0, 5, 10, 20, 30] as const;

/** Sticky-global, like the profile knobs above: how you read a shelf is a
 *  statement about how you read shelves, not about one session. */
export function loadShelfParams(): ShelfParams {
  const d = { ...DEFAULT_SHELF_PARAMS };
  try {
    const raw = localStorage.getItem(SHELF_PARAMS_KEY);
    if (!raw) return d;
    const s = JSON.parse(raw) as Partial<Record<keyof ShelfParams, unknown>>;
    // Clamped to what the panel offers, like the profile knobs: a value stored
    // by an older build must not put the layer in a state its panel cannot get
    // it out of.
    if (SHELF_WINDOW_OPTIONS.includes(s.windowMin as never)) d.windowMin = s.windowMin as number;
    if (SHELF_ZMIN_OPTIONS.includes(s.zMin as never)) d.zMin = s.zMin as number;
    if (SHELF_HOLD_OPTIONS.includes(s.minHoldMin as never)) d.minHoldMin = s.minHoldMin as number;
    return d;
  } catch {
    return d;
  }
}

export function saveShelfParams(p: ShelfParams): void {
  try {
    localStorage.setItem(SHELF_PARAMS_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const SHELF_FIELD_KEY = "chart.shelfField";

/** Which quantity the shelf raster draws.
 *
 *  Its own key rather than a seventh member of `ShelfParams`, and not a tidiness
 *  choice: `ShelfParams` is mirrored field-for-field by `journal.sim.vol_shelf`
 *  and pinned by a fixture, and that module draws nothing. A display mode does
 *  not belong in the parameters of a detector that has no display. */
export function loadShelfField(): ShelfField {
  try {
    return localStorage.getItem(SHELF_FIELD_KEY) === "flow" ? "flow" : "size";
  } catch {
    return "size";
  }
}

export function saveShelfField(f: ShelfField): void {
  try {
    localStorage.setItem(SHELF_FIELD_KEY, f);
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const PROFILE_DELTA_KEY = "chart.profileDelta";

/** Whether the volume profile's rows are coloured by their aggressor delta
 *  instead of by value area — the viewport histogram's fill and the fixed-range
 *  tool's, which are one reading offered in two places.
 *
 *  Its own key rather than a field on `ProfileKnobs` above, because it is not
 *  the same kind of setting: those three change what the distributions *are*
 *  (which days, which span, which humps get named) and each chart family keeps
 *  its own copy — the Simulator's beside the setup it belongs to. This one only
 *  changes what the rows are painted with, so it is sticky and global like the
 *  surface and the band fills: which question you read a profile for is a
 *  statement about how you read a profile.
 *
 *  Off by default. Structure is what a profile is for, the tint takes the slot
 *  that says so, and a chart that arrived already recoloured would be answering
 *  a question nobody asked. */
export function loadProfileDelta(): boolean {
  try {
    return localStorage.getItem(PROFILE_DELTA_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveProfileDelta(on: boolean): void {
  try {
    if (on) localStorage.setItem(PROFILE_DELTA_KEY, "1");
    else localStorage.removeItem(PROFILE_DELTA_KEY);
  } catch {
    // Private mode / quota — the tint still works, the choice just won't stick.
  }
}

const DELTA_LANE_KEY = "chart.deltaLane";

/** The three knobs the delta lane is *read* at, once it is switched on: what its
 *  bars are measured against, how far out a row has to be to get marked, and
 *  whether marked rows are given a verdict.
 *
 *  Kept together because they are one question — not "is the lane drawn"
 *  (`profileDelta`, above) but "what am I asking it" — and sticky-global on the
 *  same grounds as that flag: they change how a reading is presented, never what
 *  the distribution underneath is. Both charts that draw a lane share them, so a
 *  row marked on the Lab's chart is the same row marked on the replay's.
 *
 *  The defaults are today's chart exactly: `net` is the lane as it has always
 *  been drawn, nothing is flagged, and nothing is classified. Switching the lane
 *  on must not also switch on three readings nobody asked for. */
export interface DeltaLaneKnobs {
  scale: LaneScale;
  /** Standard deviations of row imbalance; 0 is off. */
  flagSigma: number;
  classify: boolean;
  /** What stretch of tape the lane reads: the whole span, a trailing clock
   *  window, or each row's own latest visit (`lib/deltaFlow.LaneWindow`).
   *  Viewport lane only — a fixed-range profile's span *is* the reader's
   *  window, so the tool ignores this. */
  window: LaneWindow;
}

const DEFAULT_DELTA_LANE: DeltaLaneKnobs = {
  scale: "net",
  flagSigma: 0,
  classify: false,
  window: "session",
};

export function loadDeltaLane(): DeltaLaneKnobs {
  const d = { ...DEFAULT_DELTA_LANE };
  try {
    const raw = localStorage.getItem(DELTA_LANE_KEY);
    if (!raw) return d;
    const s = JSON.parse(raw) as Partial<Record<keyof DeltaLaneKnobs, unknown>>;
    // Clamped to what the knobs offer, exactly as `loadProfileKnobs` is: a value
    // written by a build that offered a mode this one doesn't must not leave the
    // lane in a state its panel can't get it out of.
    if (LANE_SCALES.includes(s.scale as LaneScale)) d.scale = s.scale as LaneScale;
    if (FLAG_SIGMAS.includes(s.flagSigma as number)) d.flagSigma = s.flagSigma as number;
    if (typeof s.classify === "boolean") d.classify = s.classify;
    if (LANE_WINDOWS.includes(s.window as LaneWindow)) d.window = s.window as LaneWindow;
    return d;
  } catch {
    return d;
  }
}

/** How the legend names the lane's reading — `net`, `z@15m`, `visit`. One
 *  formatter for both charts, so the Lab's legend and the replay's cannot
 *  describe one sticky-global setting two ways. The visit window swallows the
 *  scale because the scale is inert there (see `readVisitLane`); a legend
 *  saying `z·visit` would name a statistic the lane is not drawing. */
export function deltaLaneLabel(k: DeltaLaneKnobs): string {
  if (k.window === "visit") return "visit";
  const scale = k.scale === "zscore" ? "z" : k.scale;
  const mins = LANE_WINDOW_MINUTES[k.window];
  return mins != null ? `${scale}@${mins}m` : scale;
}

export function saveDeltaLane(k: DeltaLaneKnobs): void {
  try {
    localStorage.setItem(DELTA_LANE_KEY, JSON.stringify(k));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const CVD_OSC_KEY = "chart.cvdOsc";

/** The CVD oscillator's three knobs — how the delta window accumulates, how long
 *  it is, and how wide a fractal has to be to count as a pivot. Sticky-global on
 *  the same grounds as `ProfileKnobs`: how many bars of flow you read at once is
 *  a statement about the instrument, not about the session in front of you. */
export function loadCvdOsc(): CvdOscParams {
  const d = { ...DEFAULT_CVD_OSC };
  try {
    const raw = localStorage.getItem(CVD_OSC_KEY);
    if (!raw) return d;
    const s = JSON.parse(raw) as Partial<Record<keyof CvdOscParams, unknown>>;
    // Clamped to what the panel offers, like every loader here: a value from an
    // older build must not leave a picker blank.
    if (CVD_OSC_MODE_OPTIONS.some((o) => o.value === s.mode)) d.mode = s.mode as CvdOscMode;
    if (CVD_OSC_PERIOD_OPTIONS.includes(s.period as number)) d.period = s.period as number;
    if (CVD_OSC_FRACTAL_OPTIONS.includes(s.fractalN as number)) d.fractalN = s.fractalN as number;
    return d;
  } catch {
    return d;
  }
}

export function saveCvdOsc(p: CvdOscParams): void {
  try {
    localStorage.setItem(CVD_OSC_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
  }
}

const TAPE_KNOBS_KEY = "chart.tapeKnobs";

/** The knobs the two *tape* layers are read at: what counts as a big print, what
 *  selects an event, and how the bands are drawn once selected.
 *
 *  `ProfileKnobs` above's counterpart — that one is the question the histograms
 *  are asked, this one is the question the tape is. Kept apart because they are
 *  answered at different times: the profile rule is a way of reading a market,
 *  the sweep threshold is a way of reading an instrument. */
export interface TapeKnobs {
  bigLots: number;
  /** Handed to the engine as one object, so stored as one. */
  eventTuning: EventTuning;
  eventLabelSt: number;
  /** The wash and the draw-floor, per kind — see `simPrefs`' twin fields. */
  eventFillSweep: number;
  eventFillAbsorb: number;
  eventFloorSweep: number;
  eventFloorAbsorb: number;
  eventMarginal: boolean;
}

/** The measured numbers, so an untouched chart is the write-up's chart — the
 *  same values `DEFAULT_SIM_PREFS` opens on. The band layers themselves start
 *  hidden either way (see `sweepBursts` above). */
const DEFAULT_TAPE_KNOBS: TapeKnobs = {
  bigLots: DEFAULT_BIG_LOTS,
  eventTuning: { ...DEFAULT_EVENT_TUNING },
  eventLabelSt: 1.5,
  eventFillSweep: 0.2,
  eventFillAbsorb: 0.2,
  eventFloorSweep: 1,
  eventFloorAbsorb: 1,
  eventMarginal: true,
};

// Sticky-global on the same grounds as the profile knobs above: what counts as a
// big print is a statement about the instrument, not about the session you
// happen to be replaying. The Simulator and Live keep their own copies in their
// prefs blobs, where these sit beside the tape and the layers of one saved setup.
export function loadTapeKnobs(): TapeKnobs {
  const d: TapeKnobs = { ...DEFAULT_TAPE_KNOBS, eventTuning: { ...DEFAULT_EVENT_TUNING } };
  try {
    const raw = localStorage.getItem(TAPE_KNOBS_KEY);
    if (!raw) return d;
    const s = JSON.parse(raw) as Partial<Record<keyof TapeKnobs, unknown>>;
    // Clamped to the options the knobs offer, like every loader here: a value
    // from an older build must not leave a picker blank.
    if (BIG_LOT_OPTIONS.includes(s.bigLots as number)) d.bigLots = s.bigLots as number;
    d.eventTuning = eventTuning(s.eventTuning);
    if (EVENT_LABEL_ST_OPTIONS.includes(s.eventLabelSt as number))
      d.eventLabelSt = s.eventLabelSt as number;
    // A blob from before the wash split carries one `eventFill` — it seeds both
    // kinds, so an old setting keeps meaning what it meant.
    const legacyFill = (s as Record<string, unknown>).eventFill;
    const fillSweep = s.eventFillSweep ?? legacyFill;
    const fillAbsorb = s.eventFillAbsorb ?? legacyFill;
    if (EVENT_FILL_OPTIONS.includes(fillSweep as number)) d.eventFillSweep = fillSweep as number;
    if (EVENT_FILL_OPTIONS.includes(fillAbsorb as number)) d.eventFillAbsorb = fillAbsorb as number;
    if (EVENT_FLOOR_OPTIONS.includes(s.eventFloorSweep as number))
      d.eventFloorSweep = s.eventFloorSweep as number;
    if (EVENT_FLOOR_OPTIONS.includes(s.eventFloorAbsorb as number))
      d.eventFloorAbsorb = s.eventFloorAbsorb as number;
    if (typeof s.eventMarginal === "boolean") d.eventMarginal = s.eventMarginal;
    return d;
  } catch {
    return d;
  }
}

export function saveTapeKnobs(k: TapeKnobs): void {
  try {
    localStorage.setItem(TAPE_KNOBS_KEY, JSON.stringify(k));
  } catch {
    // Private mode / quota — the knobs still work, the choice just won't stick.
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

const QUICK_DOCK_MIN_KEY = "chart.quickDockMin";

/** Whether the market-order window is minimised to its badge.
 *
 *  Its own key rather than a field on the position, because the two are
 *  independent: minimising must not forget where the window was, and putting it
 *  back at the foot of the tape must not un-minimise it. Shared by both charts,
 *  like the position — it is the same instrument on Replay and Live. */
export function loadDockMin(): boolean {
  try {
    return localStorage.getItem(QUICK_DOCK_MIN_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveDockMin(v: boolean): void {
  try {
    if (v) localStorage.setItem(QUICK_DOCK_MIN_KEY, "1");
    else localStorage.removeItem(QUICK_DOCK_MIN_KEY);
  } catch {
    // As above.
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
  /** `live` marks a profile whose right edge was left on the live edge and so
   *  follows the tape. Optional because payloads written before it exist, and a
   *  missing flag is the honest reading of them: a fixed range. */
  ranges: { from: number; to: number; live?: boolean }[];
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

const LIVE_HISTORY_DAYS_KEY = "live.historyDaysByTf";

// How many prior sessions the Live page glues in front of the day, per bar size,
// remembered.
//
// It sticks because of what it costs rather than because it is a taste: the
// context days are whole tick tapes, they are fetched and decoded *before* the
// live tape may start (rows seeded in front of it shift every index behind them,
// and an order's `idx` is a position in that array), and each one is a
// multi-megabyte parse.
//
// Only the overrides are stored; `lib/contextDays` holds the rule a bar follows
// when there is no entry for it. The page's own copy rather than the Simulator's
// for the same reason every other reading knob here is: the two pages are
// allowed to be looking at different settings.
//
// The old key held a single number for every bar at once. It is not read: it was
// a flat 5, which on a 1m is four days of prints nothing will scroll to and on a
// 4h is a third of the candles it needs.

export function loadLiveHistoryDays(): HistoryDayOverrides {
  try {
    return sanitizeHistoryDayOverrides(JSON.parse(localStorage.getItem(LIVE_HISTORY_DAYS_KEY) ?? "{}"));
  } catch {
    return {};
  }
}

export function saveLiveHistoryDays(over: HistoryDayOverrides): void {
  try {
    localStorage.setItem(LIVE_HISTORY_DAYS_KEY, JSON.stringify(over));
  } catch {
    // Private mode / quota — the toggle still works, the choice just won't stick.
  }
}

const GEX_LEVELS_KEY = "chart.gexLevels";

/** The gamma-levels layer's knobs. Sticky-global, like the econ floor. */
export interface GexLevelsParams {
  /** Heaviest walls drawn each side of spot, per book, 0–3. */
  walls: number;
  /** Draw the zero-gamma flip band. */
  flip: boolean;
  /** Which expiries the levels are built from: all, ≤7 days, or the session's
   *  own 0DTE (api/routers/gex.py `expiry`). */
  expiry: GexExpiry;
}

export const GEX_WALL_OPTIONS = [0, 1, 2, 3] as const;
export const GEX_EXPIRY_OPTIONS = ["all", "week", "0dte"] as const;
export type GexExpiry = (typeof GEX_EXPIRY_OPTIONS)[number];

export function loadGexLevelsParams(): GexLevelsParams {
  try {
    const raw = JSON.parse(localStorage.getItem(GEX_LEVELS_KEY) ?? "null");
    const walls = GEX_WALL_OPTIONS.includes(raw?.walls) ? raw.walls : 2;
    const expiry = GEX_EXPIRY_OPTIONS.includes(raw?.expiry) ? raw.expiry : "all";
    return { walls, flip: raw?.flip !== false, expiry };
  } catch {
    return { walls: 2, flip: true, expiry: "all" };
  }
}

export function saveGexLevelsParams(p: GexLevelsParams): void {
  try {
    localStorage.setItem(GEX_LEVELS_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knob still works, the choice just won't stick.
  }
}

const ECON_EVENTS_KEY = "chart.econEvents";

/** How loud a release has to be to get a line: ForexFactory's impact tiers,
 *  cumulative. Sticky-global — which prints you care about is not a per-session
 *  answer. */
export type EconImpactFloor = "high" | "medium" | "low";
export interface EconEventsParams {
  floor: EconImpactFloor;
}

export function loadEconEventsParams(): EconEventsParams {
  try {
    const raw = JSON.parse(localStorage.getItem(ECON_EVENTS_KEY) ?? "null");
    const floor = raw?.floor;
    return { floor: floor === "high" || floor === "low" ? floor : "medium" };
  } catch {
    return { floor: "medium" };
  }
}

export function saveEconEventsParams(p: EconEventsParams): void {
  try {
    localStorage.setItem(ECON_EVENTS_KEY, JSON.stringify(p));
  } catch {
    // Private mode / quota — the knob still works, the choice just won't stick.
  }
}
