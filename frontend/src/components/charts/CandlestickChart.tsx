import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { chartInk, chartSurfaces, palette, regimePalette } from "../../theme";
import {
  applyAppearance,
  appearanceSettings,
  candleColors,
  recolorVolume,
  volumeColors,
} from "./chartAppearance";
import { TradeRectanglePrimitive } from "./TradeRectanglePrimitive";
import { CompositeProfilePrimitive } from "./CompositeProfilePrimitive";
import { DevelopingProfilePrimitive } from "./DevelopingProfilePrimitive";
import { ExternalChartPrimitive } from "./ExternalChartPrimitive";
import {
  EXTERNAL_PERIOD_OPTIONS,
  groupExternalBars,
  type ExternalChartParams,
} from "../../lib/externalChart";
import { RulerPrimitive } from "./RulerPrimitive";
import { MarkerPrimitive } from "./MarkerPrimitive";
import { CvdDivergencePrimitive } from "./CvdDivergencePrimitive";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import { VolumeProfilePrimitive } from "./VolumeProfilePrimitive";
import { RangeProfilePrimitive } from "./RangeProfilePrimitive";
import {
  VolumeShelfPrimitive,
  type ShelfColumn,
  type ShelfField,
} from "./VolumeShelfPrimitive";
import {
  detectShelves,
  evalBars,
  windowStart,
  ShelfTracker,
  shelfFlow,
  type ShelfBox,
  type ShelfParams,
} from "../../lib/volumeShelf";
import {
  loadShelfParams,
  saveShelfParams,
  loadShelfField,
  saveShelfField,
} from "../../lib/chartPrefs";
import { InteractionPrimitive } from "./InteractionPrimitive";
import {
  IndicatorLegend,
  type IndicatorKey,
  type IndicatorSettingsMap,
  type LegendItem,
  type StudyRow,
} from "./IndicatorLegend";
import { StudyLayer } from "./StudyLayer";
import { StudyPicker } from "./StudyPicker";
import type { LayerState, ReplayLayerKey } from "./chartLayers";
import {
  findStudy,
  studyColor,
  studyFields,
  studyLabel,
  type StudyReport,
  type StudySpec,
} from "../../lib/studies";
import { createModernVwapLayer, type ModernVwapLayer } from "./modernVwapLayer";
import {
  buildChartKnobs,
  cvdOscKnobs,
  dynamicSwingVwapKnobs,
  externalChartKnobs,
  modernVwapKnobs,
  volumeProfileKnobs,
  vwapAnchorKnobs,
} from "./indicatorKnobs";
import {
  computeCvdOsc,
  cvdOscStrengthLabel,
  type CvdOscDivergence,
  type CvdOscParams,
} from "../../lib/cvdOsc";
import type { ModernVwapData, ModernVwapParams } from "../../lib/modernVwap";
import type { DsvParams, DynamicSwingVwapData } from "../../lib/dynamicSwingVwap";
import {
  createDynamicSwingVwapLayer,
  type DynamicSwingVwapLayer,
} from "./dynamicSwingVwapLayer";
import { ChartToolButton, ChartToolSep, ChartTools } from "./ChartToolButton";
import {
  loadChartAppearance,
  loadCvdOsc,
  deltaLaneLabel,
  loadDeltaLane,
  loadExternalChartParams,
  loadIndicatorVisibility,
  loadProfileDelta,
  loadProfileKnobs,
  loadStudies,
  loadVwapBands,
  loadVwapFill,
  loadVwapFillRegion,
  saveChartAppearance,
  saveCvdOsc,
  saveDeltaLane,
  saveExternalChartParams,
  saveIndicatorVisibility,
  saveProfileDelta,
  saveProfileKnobs,
  saveStudies,
  saveVwapBands,
  saveVwapFill,
  saveVwapFillRegion,
  type ChartAppearance,
  type DeltaLaneKnobs,
  type IndicatorVisibility,
  type ProfileKnobs,
  vwapBandLabel,
  vwapBandsShown,
  type VwapBandChoice,
  type VwapBandChoices,
  type VwapFillAnchor,
  type VwapFillRegion,
  type VwapFillRegions,
  type VwapFillWeights,
} from "../../lib/chartPrefs";
import {
  computeTickProfile,
  profileNodes,
  type TapeRange,
  type VolumeProfile,
  computeVolumeProfile,
} from "../../lib/volumeProfile";
import {
  LANE_WINDOW_MINUTES,
  classifyFlagged,
  readLane,
  readVisitLane,
  splitVisits,
  windowedOnto,
  type LaneReading,
  type LaneSource,
} from "../../lib/deltaFlow";
import { BALANCE_CAP, buildComposite, type Composite } from "../../lib/compositeProfile";
import { VR_STOP_TICKS, computeVolRuler } from "../../lib/volRuler";
import type {
  ATRPoint,
  Bar,
  ChartMarker,
  ContextProfile,
  CvdPoint,
  CvdDivergence,
  EmaPoint,
  Footprint,
  IbOverlay,
  PriceLineSpec,
  ProfilePoint,
  RsiPoint,
  TradeRect,
  VwapPoint,
} from "../../lib/chartTypes";
import { Vwap, hasTickMoments } from "../../lib/vwap";
import type { Touch, VaSnap } from "../../lib/interactionTypes";

interface Props {
  bars: Bar[];
  vwapGlobex?: VwapPoint[];
  vwapNy?: VwapPoint[];
  /** Weekly anchor (the week's first Globex open) — context only, no engine
   * trades it. Absent when the week's prior sessions aren't all on disk. */
  vwapWeekly?: VwapPoint[];
  /**
   * Developing value areas (POC/VAH/VAL as of each bar's close), one per VWAP
   * anchor: `profileGlobex` accumulates from the 18:00 open, `profileNy` from the
   * 09:30 bell. Both are supplied by the sim on every chart and drawn together —
   * which anchor a rule actually read is the run's config, not the picture.
   */
  profileGlobex?: ProfilePoint[];
  profileNy?: ProfilePoint[];
  /** The developing *weekly* value area — the globex profile carrying the week
   *  behind it. Present only when the week could be honestly built. */
  profileWeekly?: ProfilePoint[];
  /**
   * The prior sessions' volume-at-price, oldest first — what the multi-session
   * composite is built from, by the same balance walk the replay chart runs
   * (lib/compositeProfile). Absent on a chart whose payload doesn't carry them,
   * and the composite's two rows then aren't drawn at all.
   *
   * They arrive as collapsed histograms rather than as tape because a journal
   * chart holds one session: five days of ticks to draw five frozen levels is a
   * cost with no second use. See lib/chartTypes.ContextProfile.
   */
  contextProfiles?: ContextProfile[];
  /**
   * 9/20/50/200 EMA on the 1-minute grid — the day-trading convention, drawn as
   * context lines over the candles (9/20 the fast pullback pair, 50/200 the
   * slower trend reference). Stamped on the minute they were computed on, so on a
   * tick-bar chart they are sampled onto the drawn bar grid (a bar takes the
   * latest EMA at or before its close). Each has its own legend toggle.
   */
  ema9?: EmaPoint[];
  ema20?: EmaPoint[];
  ema50?: EmaPoint[];
  ema200?: EmaPoint[];
  /**
   * Wilder RSI(14), drawn as a line in its own oscillator pane under the candles
   * (0-100, with 30/50/70 guides). Unlike the EMA it tracks the *drawn* timeframe
   * — the backend computes it on the drawn-bar closes, so it arrives already
   * stamped on the bar grid. Only the Interactions Lab supplies it. See RsiPoint.
   */
  rsi?: RsiPoint[];
  atrPoints?: ATRPoint[];
  /**
   * Cumulative volume delta (signed aggressor volume, running sum) per bar, drawn
   * as a line in its own pane under the candles. Only the sim's charts supply it —
   * it needs the tape's aggressor side. See lib/chartTypes.CvdPoint.
   */
  cvd?: CvdPoint[];
  /**
   * Price/CVD divergences, drawn as A→B lines on the CVD pane (not the price
   * candles). Rides with the CVD series — shown only when CVD is on, since the
   * divergence is a statement about the delta line. See CvdDivergencePrimitive.
   */
  cvdDivergences?: CvdDivergence[];
  /**
   * Signed aggressor volume *per bar* — the same tape pass as `cvd`, unaccumulated.
   * Feeds the CVD oscillator (lib/cvdOsc): its own pane under the cumulative one,
   * plus divergence marks over the candles. Ships from the same charts `cvd` does;
   * absent, the layer is not offered at all.
   */
  delta?: CvdPoint[];
  markers?: ChartMarker[];
  /**
   * Level-interaction overlay from the Interactions Lab: touch dots (coloured by
   * outcome) and VA-snap markers. Optional — only the Interactions page sends them.
   */
  touches?: Touch[];
  vaSnaps?: VaSnap[];
  priceLines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  /**
   * Initial Balance (first 60 min of RTH): high/low drawn as flat segments from
   * the bell to the close, plus faint 1×/1.5×/2× extension guides from where the
   * IB completes. A single overlay for the single-session sim charts, or one per
   * session for the multi-session Interactions/Drafts tapes (each drawn in its
   * own span). Only the sim/Lab charts send it. See lib/chartTypes.IbOverlay.
   */
  ib?: IbOverlay | IbOverlay[] | null;
  tradeRects?: TradeRect[];
  /**
   * Open zoomed in on the (first) trade rectangle rather than fitting the whole
   * session — the by-trade view wants the trade filling the chart, with a little
   * context on either side. Ignored when there's no rect. The user can still zoom
   * back out; this only sets the initial range.
   */
  focusOnTrade?: boolean;
  /**
   * Overlay a live readout of the current zoom (padding bars / ratio relative to
   * the trade span) — a scratchpad for tuning the `focusOnTrade` framing. No
   * effect without `focusOnTrade`.
   */
  debugZoom?: boolean;
  /**
   * Frame this time span (bar times) on open instead of fitting the whole loaded
   * tape — used by the continuous session chart, whose `bars` span many sessions
   * but which should open on the one the user selected. Changing it re-frames the
   * chart in place (no rebuild), so dragging into adjacent days keeps its zoom.
   * Ignored while `focusOnTrade` is set (that owns the initial viewport).
   */
  initialTimeRange?: { from: number; to: number } | null;
  /**
   * Real volume-at-price per bar. When supplied (the sim's charts), the volume
   * profile is computed from the actual tape; without it (the journal's Databento
   * bars) it falls back to spreading each bar's volume across its range.
   */
  footprint?: Footprint;
  /**
   * Modern VWAP [GBB] — the swing- (or POC-, or clock-) anchored VWAP, its σ
   * envelope, and the MR/TC triggers read off it. Absent on a page that doesn't
   * offer the layer, and its two legend rows then aren't drawn at all.
   *
   * The parameters live on the page (which is what persists them) and arrive as
   * one object because the indicator takes them as one; `onChange` patches, so
   * the knobs behind the two rows' "…" can each set their own field. An
   * unfalsified indicator — see lib/modernVwap and docs/research/modern-vwap.html
   * before reading anything off it.
   */
  modernVwap?: {
    params: ModernVwapParams;
    onChange: (patch: Partial<ModernVwapParams>) => void;
  };
  /**
   * Dynamic Swing Anchored VWAP [Zeiierman] — the other swing-anchored VWAP, on
   * exactly the same terms as the one above and for the same reasons. A separate
   * indicator rather than a mode of that one: see lib/dynamicSwingVwap for the
   * three places the two constructs disagree. Also unfalsified.
   */
  dynamicSwingVwap?: {
    params: DsvParams;
    onChange: (patch: Partial<DsvParams>) => void;
  };
  /**
   * Per-minute session regime (which side of the two anchored VWAPs price is on),
   * drawn as a colour strip in its own pane under the candles. Supplied by the
   * sim's day chart; see lib/regimeTypes.
   */
  regimeStates?: { time: number; state: string }[];
  tickSize?: number;
  /** Dollars per full point per contract — powers the ruler's $/lot readout. */
  pointValue?: number;
  height?: number;
  /** Called when the user clicks inside a trade rectangle. */
  onTradeClick?: (rect: TradeRect) => void;
}

type Visibility = IndicatorVisibility;

/** One fixed-range profile the user has drawn, bounded by bar times. */
interface RangeSel {
  id: number;
  from: number;
  to: number;
}

/** What a mousedown grabbed: a fresh drag, an edge to resize, or the body to move. */
type DragMode = "new" | "left" | "right" | "move";
/** How close (px) the pointer must be to an edge to grab it rather than the body. */
const HANDLE_PX = 6;

// (Volume bar colours follow the candle scheme — see chartAppearance.volumeColors.)

// --- `focusOnTrade` framing (the by-trade view's default zoom) ---
// A fixed window centred on the trade: FOCUS_BARS wide and FOCUS_TICKS tall. If
// the trade itself is larger than the window, the window expands to fit it plus a
// small margin so it's never clipped. Tune the two window sizes to taste.
const FOCUS_BARS = 508;
const FOCUS_TICKS = 549;
const FOCUS_MARGIN_BARS = 12;
const FOCUS_MARGIN_TICKS = 8;

const fmtDur = (s: number): string => {
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.round(s)}s`;
};

// Hover tooltip for a trade rectangle. Only rects that carry `stats` (the sim's
// charts) get one; the journal day chart sends bare rects and is unaffected.
function tradeTooltipHtml(r: TradeRect): string {
  const s = r.stats!;
  const pnlColor = r.profitable ? palette.green : palette.red;
  const usd = Math.abs(r.net_pnl).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const tight = s.band_width_ticks < s.stop_ticks;
  const muted = `color:${palette.muted}`;
  return [
    `<div><b>#${s.trade_no}</b> · ${s.exit_reason} · ` +
      `<b style="color:${pnlColor}">${r.net_pnl >= 0 ? "+" : "−"}$${usd}</b></div>`,
    `<div style="${muted}">${s.entry_hms} → ${s.exit_hms} · ${fmtDur(s.duration_s)}</div>`,
    `<div style="${muted}">in ${s.avg_entry.toFixed(2)} · out ${s.avg_exit.toFixed(2)} · ` +
      `stop ${s.stop_ticks.toFixed(0)}t</div>`,
    `<div>R <b style="color:${s.r_multiple >= 0 ? palette.green : palette.red}">` +
      `${s.r_multiple.toFixed(2)}</b> · band ${s.band_width_ticks.toFixed(0)}t` +
      (tight ? ` <span style="color:${palette.red}">— narrower than the stop</span>` : "") +
      `</div>`,
  ].join("");
}

// How far off the loaded tape a ⚓ anchor may sit and still be kept when the
// bars change under it: one bar at the coarsest resolution any chart draws, so
// re-gridding 1m → 15m never drops an anchor sitting on the first/last bar.
const GRID_SLACK_S = 15 * 60;

// The two halves of the picker this chart doesn't use, as module constants
// rather than fresh `[]`s per render — a new array identity on every render is a
// dependency change to everything downstream of it.
const EMPTY_LAYERS: LayerState[] = [];
const EMPTY_APP_LAYERS: ReplayLayerKey[] = [];

/**
 * Which slot these charts keep their picked studies in (chartPrefs.paneKey).
 *
 * Their own, not the replay chart's. Hide/show is deliberately one map across
 * every chart — it is a preference about a fixed set of layers, and a band you
 * hid because you never read it stays hidden wherever you meet it. A study is
 * not that: it is an instance you added for a purpose, and it brings a pane with
 * it. Four studies picked to read a replay session, silently mounting four panes
 * on a 560px trade chart you opened to look at one fill, is a surprise in the
 * one direction that costs you the thing you came for.
 *
 * One slot for every chart built on this component — the journal's day and trade
 * charts, and the Lab's — because they are all the same chart of a finished
 * session with different marks on it. The replay panes keep theirs.
 */
const STUDY_PANE = "session";

// Client-side candlestick (+ anchored VWAPs + volume) used by both the
// single-trade reconstruction and the full-day session views. Weekend/overnight
// gaps collapse natively (missing bars aren't drawn).
export function CandlestickChart({
  bars,
  vwapGlobex,
  vwapNy,
  vwapWeekly,
  profileGlobex,
  profileNy,
  profileWeekly,
  contextProfiles,
  ema9,
  ema20,
  ema50,
  ema200,
  rsi,
  atrPoints,
  cvd,
  cvdDivergences,
  delta,
  markers,
  touches,
  vaSnaps,
  priceLines,
  levels,
  ib,
  tradeRects,
  focusOnTrade,
  debugZoom,
  initialTimeRange,
  footprint,
  modernVwap,
  dynamicSwingVwap,
  regimeStates,
  tickSize,
  pointValue,
  height = 520,
  onTradeClick,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const debugRef = useRef<HTMLDivElement>(null);
  // Normalise the IB prop to a list up front: the sim charts pass one overlay,
  // the multi-session Interactions/Drafts tapes pass one per session. Memoised so
  // the build effect's dependency stays reference-stable across renders.
  const ibList = useMemo<IbOverlay[]>(
    () => (ib ? (Array.isArray(ib) ? ib : [ib]) : []),
    [ib],
  );
  // The chart instance and the desired initial frame, both in refs: the frame is
  // read (not deps'd) by the build effect so a new selected day re-frames through
  // the effect below rather than rebuilding the chart, and the instance lets that
  // effect reach the timeScale without capturing it in a closure.
  const chartApiRef = useRef<IChartApi | null>(null);
  const initialRangeRef = useRef(initialTimeRange);
  initialRangeRef.current = initialTimeRange;
  // Ref, not an effect dep: a new callback identity must not rebuild the chart
  // (that would lose the user's zoom/scroll), same reason as applyRef above.
  const onTradeClickRef = useRef(onTradeClick);
  onTradeClickRef.current = onTradeClick;

  // TV-style hide/show per indicator, seeded from the user's last choice and
  // persisted on every toggle. Toggling applies to the live chart via applyRef —
  // it must NOT re-run the build effect (that would rebuild the chart and lose
  // the user's zoom/scroll position).
  const [vis, setVis] = useState<Visibility>(loadIndicatorVisibility);
  const visRef = useRef(vis);
  const applyRef = useRef<((v: Visibility) => void) | null>(null);
  // How heavily each fixed anchor's ±1σ→±2σ region is washed in, on the same
  // sticky-global terms as the visibility above and applied the same way — the
  // fill is a repaint of a primitive already on the pane, and rebuilding the
  // chart to change one alpha would cost the reader their zoom.
  const [vwapFill, setVwapFill] = useState<VwapFillWeights>(loadVwapFill);
  const fillRef = useRef(vwapFill);
  const applyFillRef = useRef<((w: VwapFillWeights) => void) | null>(null);
  const setFill = (anchor: VwapFillAnchor, weight: number) => {
    const next = { ...fillRef.current, [anchor]: weight };
    fillRef.current = next;
    applyFillRef.current?.(next);
    saveVwapFill(next);
    setVwapFill(next);
  };
  // Which σ rings each fixed anchor draws, on the same sticky-global terms. Unlike
  // the fill this is a *visibility* question, so it rides the visibility apply
  // rather than getting a second one: the σ lines it hides are the same series
  // the row's own eye hides, and two functions racing to set `visible` on one
  // series is two answers to one question.
  const [vwapBands, setVwapBands] = useState<VwapBandChoices>(loadVwapBands);
  const bandsRef = useRef(vwapBands);
  const setBands = (anchor: VwapFillAnchor, choice: VwapBandChoice) => {
    const next = { ...bandsRef.current, [anchor]: choice };
    bandsRef.current = next;
    saveVwapBands(next);
    setVwapBands(next);
    applyRef.current?.(visRef.current);
  };
  // Which region each fixed anchor's wash covers — outer ±1σ→±2σ, or the inner
  // −1σ→+1σ value area. Rides the visibility apply like the rings: which region
  // is drawn decides whether the wash has both of its edges at all.
  const [vwapRegion, setVwapRegion] = useState<VwapFillRegions>(loadVwapFillRegion);
  const regionRef = useRef(vwapRegion);
  const setRegion = (anchor: VwapFillAnchor, region: VwapFillRegion) => {
    const next = { ...regionRef.current, [anchor]: region };
    regionRef.current = next;
    saveVwapFillRegion(next);
    setVwapRegion(next);
    applyRef.current?.(visRef.current);
  };
  // What the volume profile's rows are painted with — the value area, or the
  // aggressor delta at each price. Sticky-global like the two above and applied
  // the same way, for the same reason: it is a fill on primitives already on the
  // pane, and rebuilding the chart to recolour them would cost the reader their
  // zoom. Both histograms take it, the viewport one and the fixed-range tool.
  // The External Chart overlay (lib/externalChart) — the same sticky-global
  // preference the replay's copy of this layer reads, so a period picked over
  // there is the period a trade's chart comes up at. Applied straight to the
  // primitive like the two below: none of these knobs touches a series, and
  // rebuilding the chart to change an outline colour would cost the reader
  // their zoom.
  const [extParams, setExtParams] = useState(loadExternalChartParams);
  const extParamsRef = useRef(extParams);
  const extPrimRef = useRef<ExternalChartPrimitive | null>(null);
  const patchExternal = (patch: Partial<ExternalChartParams>) => {
    const next = { ...extParamsRef.current, ...patch };
    extParamsRef.current = next;
    saveExternalChartParams(next);
    setExtParams(next);
    extPrimRef.current?.setParams(next);
  };
  // Grouped here rather than in the build effect because this chart's bars are a
  // payload, not a tape: they change when the day changes and not otherwise, so
  // one memo covers both the period moving and the chart being handed a new day.
  // The ref is what the build effect seeds a freshly-built primitive from — a
  // rebuild doesn't change the grouping, so the effect below would not re-fire.
  const extBars = useMemo(() => groupExternalBars(bars, extParams.period), [bars, extParams.period]);
  const extBarsRef = useRef(extBars);
  extBarsRef.current = extBars;
  useEffect(() => {
    extPrimRef.current?.setBars(extBars);
  }, [extBars]);

  const [profileDelta, setProfileDelta] = useState(loadProfileDelta);
  const profileDeltaRef = useRef(profileDelta);
  const applyTintRef = useRef<((on: boolean) => void) | null>(null);
  const setProfileTint = (on: boolean) => {
    profileDeltaRef.current = on;
    applyTintRef.current?.(on);
    saveProfileDelta(on);
    setProfileDelta(on);
  };
  // What that lane is being *asked* — its scale, which rows it marks, and whether
  // marked rows get a verdict (lib/deltaFlow). Sticky-global and applied without
  // a rebuild, exactly like the switch above: these change a reading drawn on
  // primitives already on the pane, and none of them touches the distribution
  // underneath, so re-deriving the profile would be work done to reach the same
  // rows.
  const [deltaLane, setDeltaLane] = useState<DeltaLaneKnobs>(loadDeltaLane);
  const deltaLaneRef = useRef(deltaLane);
  const applyLaneRef = useRef<(() => void) | null>(null);
  const setLaneKnobs = (patch: Partial<DeltaLaneKnobs>) => {
    const next = { ...deltaLaneRef.current, ...patch };
    deltaLaneRef.current = next;
    applyLaneRef.current?.();
    saveDeltaLane(next);
    setDeltaLane(next);
  };
  // Push a new indicator ink onto the series the build effect made. A series
  // carries its colour in its options, so unlike the canvas primitives (which
  // read the active ink each frame) it has to be told when the chart crosses
  // between a light and a dark surface.
  const relightRef = useRef<(() => void) | null>(null);
  const toggle = (key: IndicatorKey) =>
    setVis((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      visRef.current = next;
      applyRef.current?.(next);
      saveIndicatorVisibility(next);
      return next;
    });
  // Force an indicator on. Hide/show is a sticky global preference, so a layer
  // hidden on some other chart stays hidden here — fine for the fixed overlays,
  // wrong for one the user just asked for by hand. Applied through the ref
  // first so a draw in the same tick already sees it.
  const reveal = (key: IndicatorKey) => {
    if (visRef.current[key]) return;
    const next = { ...visRef.current, [key]: true };
    visRef.current = next;
    applyRef.current?.(next);
    saveIndicatorVisibility(next);
    setVis(next);
  };
  const revealRef = useRef(reveal);
  revealRef.current = reveal;

  // --- Community studies -----------------------------------------------------
  // The ƒ picker's indicators (see lib/studies and StudyLayer), the same
  // catalogue the Charts workspace offers. Their specs live here rather than on
  // the page: unlike the replay chart — where the topbar picker aims at whichever
  // pane has focus, so the page has to hold them — these charts are one chart on
  // a page, and the picker sits in this one's own tool rail.
  const studyLayerRef = useRef<StudyLayer | null>(null);
  const [studies, setStudies] = useState<StudySpec[]>(() => loadStudies(STUDY_PANE));
  const studiesRef = useRef(studies);
  studiesRef.current = studies;
  const [studyReport, setStudyReport] = useState<StudyReport[]>([]);
  const changeStudies = useCallback((specs: StudySpec[]) => {
    studiesRef.current = specs;
    saveStudies(specs, STUDY_PANE);
    setStudies(specs);
  }, []);
  // A spec added, removed or re-tuned is structural — series made and destroyed,
  // panes appearing. Never a rebuild of the chart: that would cost the zoom.
  useEffect(() => {
    studyLayerRef.current?.setSpecs(studies);
  }, [studies]);

  // --- The histogram layers' knobs -------------------------------------------
  // How the prior sessions are composited, how much of each goes in, and how
  // prominent a hump must be to be named. Sticky-global like the toggles above,
  // and read through refs for the same reason: turning one of these re-derives a
  // profile, which is a repaint — never a rebuild.
  const [knobs, setKnobs] = useState<ProfileKnobs>(loadProfileKnobs);
  const knobsRef = useRef(knobs);
  knobsRef.current = knobs;
  /** Prior sessions the rule actually accepted, 0 when there is no composite.
   *  The composite's row is drawn off the days *handed in* rather than off this,
   *  because the rule that rejected them lives on that row: gating the row on a
   *  composite existing would hide the only way to switch one back on. */
  const [compDays, setCompDays] = useState(0);
  /** Rebuild the composite and repaint both node readers. Set by the build
   *  effect, which is where the primitives are. */
  const profilesRef = useRef<(() => void) | null>(null);
  /** Repaint the volume shelves in place. Set by the build effect, like the
   *  profiles above and for the same reason — the primitive lives there. The
   *  argument is whether the session has to be re-walked: a parameter change
   *  asks a different question of the tape, a field change only draws a
   *  different one of the two answers each column already carries. */
  const shelvesRef = useRef<((rebuild: boolean) => void) | null>(null);
  /** The shelf reading's parameters, sticky-global like the profile knobs.
   *  Through a ref because changing one re-walks the whole session. */
  const [shelfParams, setShelfParams] = useState<ShelfParams>(loadShelfParams);
  const shelfParamsRef = useRef(shelfParams);
  shelfParamsRef.current = shelfParams;
  const patchShelf = useCallback((patch: Partial<ShelfParams>) => {
    setShelfParams((prev) => {
      const next = { ...prev, ...patch };
      shelfParamsRef.current = next;
      saveShelfParams(next);
      return next;
    });
  }, []);
  /** Which field the raster draws. Separate from the params because switching it
   *  is a repaint, not a re-reading: the walk is unchanged and every column
   *  already carries both quantities. */
  const [shelfField, setShelfField] = useState<ShelfField>(loadShelfField);
  const shelfFieldRef = useRef(shelfField);
  shelfFieldRef.current = shelfField;
  const patchShelfField = useCallback((f: ShelfField) => {
    setShelfField(f);
    shelfFieldRef.current = f;
    saveShelfField(f);
  }, []);
  useEffect(() => {
    shelvesRef.current?.(true);
  }, [shelfParams]);
  useEffect(() => {
    shelvesRef.current?.(false);
  }, [shelfField]);
  const patchKnobs = useCallback((patch: Partial<ProfileKnobs>) => {
    setKnobs((prev) => {
      const next = { ...prev, ...patch };
      knobsRef.current = next;
      saveProfileKnobs(next);
      return next;
    });
  }, []);
  // A knob turned re-derives the profiles in place. Declared as an effect rather
  // than done inside `patchKnobs` so it also runs on the first paint after a
  // rebuild, when the primitives are new and the knobs are whatever was stored.
  useEffect(() => {
    profilesRef.current?.();
  }, [knobs]);

  // --- The CVD oscillator ----------------------------------------------------
  // Its window and its fractal width, sticky-global like the profile knobs above
  // and read through a ref for the same reason: changing either re-runs the
  // window and the pivots off it, which is a redraw, not a rebuild.
  //
  // The parameters live here rather than on the page (as Modern VWAP's do)
  // because nothing off-chart reads them — no strategy, no sim, no export. They
  // are how this chart is being looked at, and every chart should be looked at
  // the same way until the user says otherwise.
  const [cvdOscParams, setCvdOscParams] = useState<CvdOscParams>(loadCvdOsc);
  const cvdOscRef = useRef(cvdOscParams);
  cvdOscRef.current = cvdOscParams;
  const cvdOscDrawRef = useRef<(() => void) | null>(null);
  const [cvdOscRead, setCvdOscRead] = useState({ divs: 0, strength: 0 });
  const patchCvdOsc = useCallback((patch: Partial<CvdOscParams>) => {
    setCvdOscParams((prev) => {
      const next = { ...prev, ...patch };
      cvdOscRef.current = next;
      saveCvdOsc(next);
      return next;
    });
  }, []);
  useEffect(() => {
    cvdOscDrawRef.current?.();
  }, [cvdOscParams]);

  /** Per-bar delta on the *drawn* bar grid, or null when this chart's payload
   *  carried none. Matched by time rather than by index: the delta rows and the
   *  bars come off the same backend pass, but a chart that ever drew a subset of
   *  what it was sent would silently window-shift the whole indicator, and a
   *  missing bar honestly reads as zero flow. */
  const deltaByBar = useMemo(() => {
    if (!delta || delta.length === 0 || bars.length === 0) return null;
    const m = new Map<number, number>();
    for (const p of delta) m.set(p.time, p.value);
    return bars.map((b) => m.get(b.time) ?? 0);
  }, [delta, bars]);

  /**
   * The context days as one glued "tape", in the shape `buildComposite` reads.
   *
   * The prior sessions arrive as dense per-window histograms, and a profile only
   * ever asks its input two things — what level, how much. So each window
   * expands to its non-empty (level, size) pairs, the windows of one day are laid
   * down adjacently, and a day becomes an index span: the whole day for a Globex
   * composite, its second window alone for an RTH one. Aggregated input is
   * lossless here — `computeTapeProfileRanges` sums by level and never looks at
   * order — so the balance walk this feeds returns exactly what it would have
   * returned over five days of raw prints.
   */
  /**
   * Where the drawn tape's last RTH session starts and ends, as bar indices
   * (`start` is -1 when there is no RTH on the chart at all).
   *
   * Three layers need it — the vol ruler (everything before the bell is context
   * it warms *through* rather than measures), the session's own volume profile,
   * and the nodes read off that — and the legend needs to know whether those
   * rows exist, so it lives out here rather than inside the build effect.
   *
   * Read off the bar clock, since bar times carry the ET wall clock on a UTC
   * epoch, rather than off `profileNy`: the layer is then there on a chart whose
   * payload has no developing value area. The *last* bell, because a
   * multi-session tape (the Lab's) is developing the session it ended in.
   */
  const nySpan = useMemo(() => {
    const NY_OPEN_S = 9.5 * 3600;
    const NY_CLOSE_S = 16 * 3600;
    const tod = (t: number) => ((t % 86400) + 86400) % 86400;
    let start = -1;
    for (let i = 0; i < bars.length; i++) {
      const s = tod(bars[i].time);
      if (s >= NY_OPEN_S && s < NY_CLOSE_S && (i === 0 || tod(bars[i - 1].time) < NY_OPEN_S))
        start = i;
    }
    let end = -1;
    if (start >= 0) {
      end = start;
      while (end + 1 < bars.length && tod(bars[end + 1].time) < NY_CLOSE_S) end++;
    }
    return { start, end };
  }, [bars]);

  const ctxTape = useMemo(() => {
    const days = contextProfiles ?? [];
    if (days.length === 0) return null;
    const level: number[] = [];
    const size: number[] = [];
    const globex: TapeRange[] = [];
    const rth: TapeRange[] = [];
    const push = (h?: { min: number; counts: number[] }) => {
      if (!h) return;
      for (let i = 0; i < h.counts.length; i++) {
        const v = h.counts[i];
        if (v > 0) {
          level.push(h.min + i);
          size.push(v);
        }
      }
    };
    for (const d of days) {
      const i0 = level.length;
      push(d.on);
      const rthStart = level.length;
      push(d.rth);
      const i1 = level.length - 1;
      if (i1 < i0) continue; // a day with neither window on disk
      globex.push({ i0, i1 });
      if (i1 >= rthStart) rth.push({ i0: rthStart, i1 });
    }
    if (globex.length === 0) return null;
    return { level: Int32Array.from(level), size: Int32Array.from(size), globex, rth };
  }, [contextProfiles]);

  // --- Modern VWAP ----------------------------------------------------------
  // The layer itself is ./modernVwapLayer, shared with the replay chart. What
  // lives here is the wiring: the parameters read through a ref (a knob turn
  // must redraw, never rebuild — a rebuild costs the user their zoom), and what
  // the two legend rows quote.
  const mvLayerRef = useRef<ModernVwapLayer | null>(null);
  const mvParams = modernVwap?.params ?? null;
  const mvRef = useRef<ModernVwapParams | null>(mvParams);
  mvRef.current = mvParams;

  // --- Dynamic Swing VWAP ---------------------------------------------------
  // The Zeiierman line, wired exactly as Modern VWAP is above and sharing its
  // draw effect below — one indicator, one layer, two charts.
  const dsvLayerRef = useRef<DynamicSwingVwapLayer | null>(null);
  const dsvParams = dynamicSwingVwap?.params ?? null;
  const dsvRef = useRef<DsvParams | null>(dsvParams);
  dsvRef.current = dsvParams;
  const dsvDrawRef = useRef<(() => void) | null>(null);
  const [dsvRead, setDsvRead] = useState({
    pivots: 0,
    bullPct: 0,
    aptNow: 0,
    dropped: 0,
    // Whether the anchor timeframe regrouped these bars at all — see the row
    // below, which says '@' only when it did.
    anchored: false,
  });
  const onDsvData = useCallback((d: DynamicSwingVwapData | null) => {
    if (!d) return;
    setDsvRead((prev) =>
      prev.pivots === d.pivots.length &&
      prev.bullPct === d.bullPct &&
      prev.aptNow === d.aptNow &&
      prev.dropped === d.dropped &&
      prev.anchored === d.anchored
        ? prev
        : {
            pivots: d.pivots.length,
            bullPct: d.bullPct,
            aptNow: d.aptNow,
            dropped: d.dropped,
            anchored: d.anchored,
          },
    );
  }, []);
  /** Redraw with the current parameters, set by the build effect (which is where
   *  the bars and the POC maps are). Null before the chart exists. */
  const mvDrawRef = useRef<(() => void) | null>(null);
  const [mvRead, setMvRead] = useState({ trendPct: 0, undefPct: 0, signals: 0, anchors: 0 });
  const onMvData = useCallback((d: ModernVwapData | null) => {
    // A dark layer keeps its last numbers rather than printing zeros — the rows
    // stop quoting them anyway, and "0 through the gate" would read as a
    // measurement when it only means nobody has asked yet.
    if (!d) return;
    setMvRead((prev) =>
      prev.trendPct === d.trendPct &&
      prev.undefPct === d.undefPct &&
      prev.signals === d.signals.length &&
      prev.anchors === d.anchors.length
        ? prev
        : {
            trendPct: d.trendPct,
            undefPct: d.undefPct,
            signals: d.signals.length,
            anchors: d.anchors.length,
          },
    );
  }, []);

  // The chart's own colours — same sticky-global shape as the toggles above, and
  // the same rule about the build effect: recolouring goes through applyOptions
  // (see the effect below), never through a rebuild, so it can't cost you your
  // zoom. The ref is what the build effect reads, so a chart rebuilt for some
  // other reason comes back in the colours you last chose.
  const [appearance, setAppearance] = useState<ChartAppearance>(loadChartAppearance);
  const appearanceRef = useRef(appearance);
  appearanceRef.current = appearance;
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const changeAppearance = (next: ChartAppearance) => {
    setAppearance(next);
    saveChartAppearance(next);
  };

  // Fixed-range profile tool. `armed` = waiting for the drag that defines a new
  // profile; `ranges` are the ones already on the chart, any of which can be
  // re-dragged. Everything is mirrored into refs so the mouse handlers inside the
  // build effect can read it without becoming effect deps — arming the tool or
  // moving a profile must not rebuild the chart (that would lose zoom/scroll).
  //
  // React state exists only to render the toolbar; a drag in progress repaints
  // through the primitive alone, so panning a profile doesn't re-render per frame.
  // Ranges are stored as bar *times*, not indices, so they survive a timeframe
  // switch (which swaps the whole bar array out).
  const [armed, setArmed] = useState(false);
  const [ranges, setRanges] = useState<RangeSel[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const armedRef = useRef(false);
  const rangesRef = useRef<RangeSel[]>([]);
  const selectedRef = useRef<number | null>(null);
  const nextIdRef = useRef(1);
  const armApplyRef = useRef<((a: boolean) => void) | null>(null);
  const paintRef = useRef<(() => void) | null>(null);

  const arm = (v: boolean) => {
    // one drag/click tool owns the mouse at a time
    if (v) {
      armRulerRef.current(false);
      armAvwapRef.current(false);
    }
    armedRef.current = v;
    setArmed(v);
    armApplyRef.current?.(v);
  };

  // Ruler / measure tool (TV's ruler): arm it, drag (or click-move-click) across
  // the chart, and read off the move — points/ticks/%, $ per lot, bars and time.
  // One measurement at a time; it lives in the primitive only (no React state
  // beyond the button), and a plain click or Esc dismisses it. Same ref dance as
  // the range tool: arming must not rebuild the chart.
  const [rulerArmed, setRulerArmed] = useState(false);
  const rulerArmedRef = useRef(false);
  const rulerApplyRef = useRef<((a: boolean) => void) | null>(null);
  const rulerClearRef = useRef<() => void>(() => {});
  const armRuler = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armAvwapRef.current(false);
    }
    rulerArmedRef.current = v;
    setRulerArmed(v);
    rulerApplyRef.current?.(v);
  };
  const armRulerRef = useRef(armRuler);
  armRulerRef.current = armRuler;

  // Anchored-VWAP tool (TV's ⚓): arm it, click any bar, and a VWAP + ±1σ/±2σ
  // bands draw from that bar forward — computed client-side from the bar tape, so
  // its σ is bar-derived (typical price × volume), not the engine's tick-derived
  // σ. One anchor at a time; re-clicking moves it, the Clear button removes it.
  // `avwapAnchor` is a bar *time* (survives a timeframe switch, same as the
  // ranges); the draw itself lives in the build effect and is triggered through
  // avwapDrawRef so re-anchoring never rebuilds the chart (which would lose the
  // user's zoom/scroll). Same three-way exclusion as the range and ruler tools.
  const [avwapArmed, setAvwapArmed] = useState(false);
  const [avwapAnchor, setAvwapAnchor] = useState<number | null>(null);
  const avwapArmedRef = useRef(false);
  const avwapAnchorRef = useRef<number | null>(null);
  const avwapApplyRef = useRef<((a: boolean) => void) | null>(null);
  const avwapDrawRef = useRef<(() => void) | null>(null);
  const avwapSyncRef = useRef<() => void>(() => {});
  avwapSyncRef.current = () => setAvwapAnchor(avwapAnchorRef.current);
  const armAvwap = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
    }
    avwapArmedRef.current = v;
    setAvwapArmed(v);
    avwapApplyRef.current?.(v);
  };
  const armAvwapRef = useRef(armAvwap);
  armAvwapRef.current = armAvwap;
  const clearAvwap = () => {
    avwapAnchorRef.current = null;
    avwapDrawRef.current?.(); // anchor null -> draw removes the series
    setAvwapAnchor(null);
  };
  // Push whatever the refs now hold into both the chart and the toolbar. Called
  // once a drag settles, never mid-drag.
  const syncRanges = () => {
    setRanges([...rangesRef.current]);
    setSelected(selectedRef.current);
    paintRef.current?.();
  };
  const clearRanges = () => {
    rangesRef.current = [];
    selectedRef.current = null;
    syncRanges();
  };
  const deleteSelected = () => {
    if (selectedRef.current == null) return;
    rangesRef.current = rangesRef.current.filter((r) => r.id !== selectedRef.current);
    selectedRef.current = null;
    syncRanges();
  };
  // Held in refs so the effect's mouse handlers can call them without capturing
  // stale closures.
  const disarmRef = useRef<() => void>(() => {});
  const syncRef = useRef<() => void>(() => {});
  disarmRef.current = () => arm(false);
  syncRef.current = syncRanges;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (armedRef.current) arm(false);
        if (rulerArmedRef.current) armRulerRef.current(false);
        if (avwapArmedRef.current) armAvwapRef.current(false);
        rulerClearRef.current(); // Esc also dismisses a finished measurement
      }
      // Don't hijack Delete while the user is typing somewhere on the page.
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if ((e.key === "Delete" || e.key === "Backspace") && !typing && selectedRef.current != null) {
        e.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    // Handle for the debug-readout animation loop (focusOnTrade + debugZoom only),
    // cancelled on teardown.
    let debugRaf = 0;
    // Read, not subscribed to: appearance is applied live by its own effect
    // below, and a rebuild here would throw away zoom and scroll.
    const surf = chartSurfaces[appearanceRef.current.surface];
    const sch = candleColors(appearanceRef.current);
    // Every indicator hue, in the cut this surface wants (theme.ts). Reassigned
    // by `relight` at the foot of this effect, so anything built later off it —
    // the ⚓ anchor is drawn on demand — gets the cut now in force.
    let hues = chartInk(appearanceRef.current.surface);
    const chart: IChartApi = createChart(ref.current, {
      width: ref.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: surf.bg },
        textColor: surf.text,
        fontFamily: "Inter, sans-serif",
        // Axis labels are smaller than the default 12px so the right price scale
        // (a wide 5-digit NQ price + ".25") takes a narrower gutter — the scale
        // auto-sizes to the widest label, and the library has no max-width knob.
        fontSize: 9,
      },
      grid: {
        vertLines: { color: surf.grid },
        horzLines: { color: surf.grid },
      },
      rightPriceScale: { borderColor: surf.grid },
      timeScale: { borderColor: surf.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartApiRef.current = chart;

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: sch.up,
      downColor: sch.down,
      wickUpColor: sch.up,
      wickDownColor: sch.down,
      borderVisible: false,
    });
    candleRef.current = candle;
    candle.setData(
      bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    // Overlay times (fills, MAE/MFE, trade rect) carry second precision, but
    // lightweight-charts only renders markers on an exact bar time and
    // timeToCoordinate() returns null for any off-grid time — so snap every
    // overlay time onto the actual (resampled) bar grid.
    const barTimes = bars.map((b) => b.time);
    const last = barTimes.length - 1;

    const { start: nyStart, end: nyEnd } = nySpan;
    const nearestIdx = (t: number): number => {
      if (t <= barTimes[0]) return 0;
      if (t >= barTimes[last]) return last;
      let lo = 0;
      let hi = last;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] === t) return mid;
        if (barTimes[mid] < t) lo = mid + 1;
        else hi = mid - 1;
      }
      return t - barTimes[hi] <= barTimes[lo] - t ? hi : lo;
    };
    const nearestBar = (t: number): number => barTimes[nearestIdx(t)];
    const floorBar = (t: number): number => {
      if (t <= barTimes[0]) return barTimes[0];
      let lo = 0;
      let hi = last;
      let res = barTimes[0];
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] <= t) {
          res = barTimes[mid];
          lo = mid + 1;
        } else hi = mid - 1;
      }
      return res;
    };
    const ceilBar = (t: number): number => {
      if (t >= barTimes[last]) return barTimes[last];
      let lo = 0;
      let hi = last;
      let res = barTimes[last];
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] >= t) {
          res = barTimes[mid];
          hi = mid - 1;
        } else lo = mid + 1;
      }
      return res;
    };

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeRef.current = volume;
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const volc = volumeColors(appearanceRef.current);
    volume.setData(
      bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? volc.up : volc.down,
      })),
    );

    // The regime ribbon: one bar per candle, full height, coloured by which side
    // of the two anchored VWAPs price was on. It reads as a strip rather than a
    // series — the value is always 1 and only the colour carries meaning.
    //
    // The states arrive on a 1-minute grid while the candles are tick bars, so
    // each candle takes the state in force at its close rather than the ribbon's
    // own times being fed to the chart: lightweight-charts unions the time points
    // of every series, and minute stamps that no candle sits on would open an
    // empty column each. Snapping to the candle grid also means the strip stays
    // glued to the candles under zoom and pan, for free.
    const ribbonPane = regimeStates && regimeStates.length > 0 ? 1 : 0;
    if (ribbonPane) {
      const ribbon = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: false, base: 0 },
        ribbonPane,
      );
      let j = 0;
      ribbon.setData(
        bars.map((b) => {
          while (j + 1 < regimeStates!.length && regimeStates![j + 1].time <= b.time) j++;
          const s = regimeStates![j];
          // A candle before the first state (a night the regime engine had no
          // ticks for) gets no bar rather than the first state back-projected.
          if (!s || s.time > b.time) return { time: b.time as Time };
          return {
            time: b.time as Time,
            value: 1,
            color:
              regimePalette.state[s.state as keyof typeof regimePalette.state] ??
              regimePalette.klass.unknown,
          };
        }),
      );
      ribbon.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
    }

    // ATR gets created/removed (not just hidden) on toggle: hiding the series
    // would leave its empty sub-pane behind, while removing the last series of
    // a pane drops the pane too.
    let atrSeries: ISeriesApi<"Line"> | null = null;
    const atrPane = ribbonPane + 1;
    const addAtr = () => {
      atrSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.gold,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        },
        atrPane,
      );
      atrSeries.setData(atrPoints!.map((p) => ({ time: p.time as Time, value: p.atr })));
      // Force a price-dominant split — default is an even share per pane, so set
      // every factor explicitly. Ratio 5:1 ≈ 83% price / 17% ATR.
      const panes = chart.panes();
      if (panes.length > atrPane) {
        panes[0].setStretchFactor(1000);
        panes[atrPane].setStretchFactor(200);
      }
    };

    // CVD gets the same create/remove-on-toggle treatment as ATR and its own pane,
    // stacked under it. The offset keeps it right on a chart with no ATR (the
    // journal's own charts carry no CVD, the Lab's carry both).
    let cvdSeries: ISeriesApi<"Line"> | null = null;
    const cvdPane = atrPane + (atrPoints && atrPoints.length > 0 ? 1 : 0);
    const addCvd = () => {
      cvdSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.blue,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "volume" },
        },
        cvdPane,
      );
      cvdSeries.setData(cvd!.map((p) => ({ time: p.time as Time, value: p.value })));
      // A zero reference: CVD crosses sign, and which side of zero it sits on is
      // the whole read (net buying vs net selling since the anchor).
      cvdSeries.createPriceLine({
        price: 0,
        color: palette.grid,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      // Divergence A→B lines live on this pane, attached to the CVD series so
      // they resolve in delta units and vanish with CVD when it's toggled off.
      if (cvdDivergences && cvdDivergences.length > 0) {
        cvdSeries.attachPrimitive(
          new CvdDivergencePrimitive(
            // This pane names the read outright; the oscillator's marks below use
            // its source script's ±RD, which only means anything next to its own
            // histogram.
            cvdDivergences.map((d) => ({ ...d, label: d.kind })),
          ) as any,
        );
      }
      const panes = chart.panes();
      if (panes.length > cvdPane) {
        panes[0].setStretchFactor(1000);
        panes[cvdPane].setStretchFactor(200);
      }
    };

    // RSI gets the same create/remove-on-toggle treatment as ATR/CVD and its own
    // pane, stacked after them. It's computed on the drawn bars (so it tracks the
    // chart's timeframe), arriving already stamped on the bar grid; the forward-walk
    // is a straight copy, but kept identical to the EMA's so it's robust to any
    // stamp that lands off-grid. The pane is pinned to 0-100 with 30/50/70 guides —
    // the oscillator's whole read is where it sits between those bands.
    // The CVD oscillator: the windowed delta as a histogram, under the cumulative
    // line it is the alternative to, and the divergences it finds drawn twice —
    // on this pane in delta units, and over the candles at the pivot prices. The
    // second attachment is the point of the indicator (a divergence you have to
    // eyeball across two panes isn't one), and it is why this owns a primitive on
    // the price series that has to be detached by hand when the row goes dark.
    let cvdOscSeries: ISeriesApi<"Histogram"> | null = null;
    let cvdOscMarks: CvdDivergencePrimitive | null = null;
    let cvdOscPriceMarks: CvdDivergencePrimitive | null = null;
    const cvdOscPane = cvdPane + (cvd && cvd.length > 0 ? 1 : 0);
    /** Re-run the window and the pivots at the current knobs and repaint. Cheap
     *  enough to call on every knob turn: one pass over the drawn bars. */
    const drawCvdOsc = () => {
      if (!cvdOscSeries || !deltaByBar) return;
      const { hist, divergences } = computeCvdOsc(bars, deltaByBar, cvdOscRef.current);
      cvdOscSeries.setData(
        bars.map((b, i) => {
          const v = hist[i];
          // Whitespace through the seeding window rather than a zero: the window
          // isn't full yet, and a zero there would read as "flow balanced".
          if (!Number.isFinite(v)) return { time: b.time as Time };
          return {
            time: b.time as Time,
            value: v,
            color: v >= 0 ? palette.green : palette.red,
          };
        }),
      );
      // `+RD`/`-RD` is the source script's notation: the sign is the *trade*
      // implied (bullish divergence is +), not the sign of the reading.
      const seg = (d: CvdOscDivergence, v1: number, v2: number) => ({
        kind: d.kind,
        t1: d.t1,
        v1,
        t2: d.t2,
        v2,
        label: d.kind === "bear" ? "-RD" : "+RD",
      });
      cvdOscMarks?.setData(divergences.map((d) => seg(d, d.h1, d.h2)));
      cvdOscPriceMarks?.setData(divergences.map((d) => seg(d, d.p1, d.p2)));
      const last = divergences[divergences.length - 1];
      const strength = last ? last.strength : 0;
      setCvdOscRead((prev) =>
        prev.divs === divergences.length && prev.strength === strength
          ? prev
          : { divs: divergences.length, strength },
      );
    };
    const addCvdOsc = () => {
      cvdOscSeries = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "volume" } },
        cvdOscPane,
      );
      // Zero is the whole read here — unlike the cumulative pane, where it means
      // "back to the anchor", on a window it means the last `period` bars
      // balanced.
      cvdOscSeries.createPriceLine({
        price: 0,
        color: palette.grid,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      cvdOscMarks = new CvdDivergencePrimitive();
      cvdOscSeries.attachPrimitive(cvdOscMarks as any);
      cvdOscPriceMarks = new CvdDivergencePrimitive();
      candle.attachPrimitive(cvdOscPriceMarks as any);
      drawCvdOsc();
      const panes = chart.panes();
      if (panes.length > cvdOscPane) {
        panes[0].setStretchFactor(1000);
        panes[cvdOscPane].setStretchFactor(200);
      }
    };
    const removeCvdOsc = () => {
      if (cvdOscPriceMarks) candle.detachPrimitive(cvdOscPriceMarks as any);
      cvdOscPriceMarks = null;
      cvdOscMarks = null;
      if (cvdOscSeries) chart.removeSeries(cvdOscSeries);
      cvdOscSeries = null;
    };

    let rsiSeries: ISeriesApi<"Line"> | null = null;
    const rsiPane = cvdOscPane + (deltaByBar ? 1 : 0);
    const addRsi = () => {
      rsiSeries = chart.addSeries(
        LineSeries,
        {
          color: palette.violet,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 1, minMove: 0.1 },
          // Pin the pane to the oscillator's fixed 0-100 range so the guide lines
          // mean the same thing every session, whatever the day's RSI swing.
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        },
        rsiPane,
      );
      const data: { time: Time; value: number }[] = [];
      let j = 0;
      for (const b of bars) {
        while (j + 1 < rsi!.length && rsi![j + 1].time <= b.time) j++;
        const p = rsi![j];
        if (!p || p.time > b.time) continue; // a bar before the first RSI point
        data.push({ time: b.time as Time, value: p.value });
      }
      rsiSeries.setData(data);
      // Overbought / midline / oversold guides — the levels the RSI read is against.
      for (const lvl of [70, 50, 30]) {
        rsiSeries.createPriceLine({
          price: lvl,
          color: lvl === 50 ? palette.grid : palette.muted,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
        });
      }
      const panes = chart.panes();
      if (panes.length > rsiPane) {
        panes[0].setStretchFactor(1000);
        panes[rsiPane].setStretchFactor(200);
      }
    };

    // The vol ruler: the bar-range volatility pane, read three ways at once —
    // an ATR(14) line, the session's developing median bar range, and (when the
    // tape carries a prior session) yesterday's settled median, all in ticks
    // against the 50-tick stop. See lib/volRuler for why the median exists next
    // to the ATR. Same create/remove-on-toggle treatment and its own pane, last
    // in the stack.
    //
    // Everything before the bell is context it warms *through* rather than
    // measures — an overnight bar's range says nothing about the day's character
    // — which is exactly what `nyStart` means to computeVolRuler.
    let vrAtr: ISeriesApi<"Line"> | null = null;
    let vrDev: ISeriesApi<"Line"> | null = null;
    const vrPane = rsiPane + (rsi && rsi.length > 0 ? 1 : 0);
    const addVr = () => {
      const fmt = {
        type: "custom" as const,
        formatter: (v: number) => `${Math.round(v)}t`,
        minMove: 1,
      };
      vrAtr = chart.addSeries(
        LineSeries,
        {
          color: palette.violet,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: fmt,
          // Keep the 50t rule in view however hot the tape got: the distance
          // between the lines and that rule is the whole read, and autoscale
          // would otherwise leave it below the frame on a wild session.
          autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => {
            const r = orig();
            if (r?.priceRange)
              r.priceRange.minValue = Math.min(r.priceRange.minValue, VR_STOP_TICKS - 10);
            return r;
          },
        },
        vrPane,
      );
      vrDev = chart.addSeries(
        LineSeries,
        {
          color: palette.gold,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: fmt,
        },
        vrPane,
      );
      const d = computeVolRuler(bars, Math.max(0, nyStart), tickSize ?? 0.25);
      vrAtr.setData(d.atr.map((p) => ({ time: p.time as Time, value: p.value })));
      vrDev.setData(d.dev.map((p) => ({ time: p.time as Time, value: p.value })));
      vrDev.createPriceLine({
        price: VR_STOP_TICKS,
        color: palette.muted,
        lineWidth: 1,
        lineStyle: 2,
        title: `${VR_STOP_TICKS}t stop`,
      });
      // Only when the tape actually carries a settled prior session — on a
      // one-session chart it doesn't, and a made-up reference is worse than none.
      if (d.yday != null) {
        vrDev.createPriceLine({
          price: d.yday,
          color: palette.green,
          lineWidth: 1,
          lineStyle: 2,
          title: `yday ${Math.round(d.yday)}t`,
        });
      }
      const panes = chart.panes();
      if (panes.length > vrPane) {
        panes[0].setStretchFactor(1000);
        panes[vrPane].setStretchFactor(200);
      }
    };

    // The ribbon is a strip, not a chart: it gets just enough height to read as a
    // colour band. Set after addAtr so both panes are sized from one place.
    const panes0 = chart.panes();
    if (ribbonPane && panes0.length > ribbonPane) {
      panes0[0].setStretchFactor(1000);
      panes0[ribbonPane].setStretchFactor(70);
    }

    // A non-finite value is a session-boundary break sentinel (see
    // Interactions.tsx). lightweight-charts has no native line gaps: a
    // whitespace item only reserves a time-scale slot — the series drops it
    // from its own rows and the line renderer connects the surviving
    // neighbours straight across. Each drawn segment takes the colour of the
    // point it *leaves*, so the break is enforced by painting the last real
    // point before the sentinel fully transparent: the stroke arriving there
    // keeps the series colour, the bridge to the next session doesn't render.
    const GAP_COLOR = "rgba(0,0,0,0)";
    const gappedLineData = <K extends string>(
      pts: ({ time: number } & Record<K, number>)[],
      key: K,
    ): { time: Time; value?: number; color?: string }[] => {
      const out: { time: Time; value?: number; color?: string }[] = [];
      for (const v of pts) {
        if (Number.isFinite(v[key])) {
          out.push({ time: v.time as Time, value: v[key] });
        } else {
          const prev = out.length > 0 ? out[out.length - 1] : null;
          if (prev && prev.value !== undefined) prev.color = GAP_COLOR;
          out.push({ time: v.time as Time });
        }
      }
      return out;
    };

    // One anchored VWAP = 5 lines (mid, ±1σ, ±2σ) plus a shaded fill between the
    // σ bands. The σ lines are dashed and fade outward so they read as an
    // envelope rather than competing with the mid line. Each anchor — lines and
    // fill together — is toggled as a unit.
    const addVwap = (
      points: VwapPoint[],
      colors: { middle: string; band1: string; band2: string; fill: string },
    ): {
      series: ISeriesApi<"Line">[];
      sigma1: ISeriesApi<"Line">[];
      sigma2: ISeriesApi<"Line">[];
      band: VwapBandPrimitive;
    } => {
      const series: ISeriesApi<"Line">[] = [];
      const sigma1: ISeriesApi<"Line">[] = [];
      const sigma2: ISeriesApi<"Line">[] = [];
      const mid = chart.addSeries(LineSeries, {
        color: colors.middle,
        lineWidth: 2,
        priceLineVisible: false,
      });
      mid.setData(gappedLineData(points, "middle"));
      series.push(mid);
      const bands = [
        { keys: ["upper1", "lower1"], color: colors.band1, ring: sigma1 },
        { keys: ["upper2", "lower2"], color: colors.band2, ring: sigma2 },
      ] as const;
      for (const band of bands) {
        for (const key of band.keys) {
          const line = chart.addSeries(LineSeries, {
            color: band.color,
            lineWidth: 1,
            lineStyle: 2,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          line.setData(gappedLineData(points, key));
          series.push(line);
          // The same series in two lists: `series` is the anchor whole — what the
          // relight recolours and what the ⚓ band toggles as a unit — and the two
          // rings are what the per-anchor band knob hides. One array, two ways of
          // asking about it, so neither can drift from what was actually built.
          band.ring.push(line);
        }
      }
      const band = new VwapBandPrimitive(points, colors.fill);
      candle.attachPrimitive(band as any);
      return { series, sigma1, sigma2, band };
    };

    const globex =
      vwapGlobex && vwapGlobex.length > 0 ? addVwap(vwapGlobex, hues.vwap.globex) : null;
    const ny = vwapNy && vwapNy.length > 0 ? addVwap(vwapNy, hues.vwap.ny) : null;
    const weekly =
      vwapWeekly && vwapWeekly.length > 0 ? addVwap(vwapWeekly, hues.vwap.weekly) : null;

    // Modern VWAP — the same layer the replay chart mounts (see
    // ./modernVwapLayer), fed here from the drawn bars and the developing
    // profiles this chart was already handed. Built whatever the page passes, so
    // it is one thing to switch on rather than a rebuild; with no parameters it
    // computes nothing and its rows aren't in the legend.
    //
    // The marks go on the pane here rather than later in the stack: unlike the
    // replay there is no open position or working order for a study layer to be
    // careful about, and the Lab's own touch/VA-snap marks are the reading this
    // chart exists for, so they stay on top.
    // The developing POC by bar time, for the indicator's `poc` anchor: whichever
    // profile the knob asks for, read off the series this chart was given. An
    // absent profile leaves an empty map and that anchor degrades to a plain
    // session anchor — the legend's ⚓ count is the tell.
    const mvPocMap = (pts: ProfilePoint[] | undefined): Map<number, number> =>
      new Map((pts ?? []).map((p) => [p.time, p.poc]));
    const mvGlobexPoc = mvPocMap(profileGlobex);
    const mvWeeklyPoc = mvPocMap(profileWeekly);
    // Where the context ends and the session begins. On a multi-session tape the
    // frame the chart opens on *is* the session being read (the Lab hands over
    // the focused day's span), so the prior days behind it warm the medians, the
    // KER and the anchors without being counted in what the gate says about the
    // day. Without a frame there is nothing to call context and the whole tape
    // is the session.
    const mvFrom = initialRangeRef.current?.from;
    const mvFirst = mvFrom == null ? -1 : bars.findIndex((b) => b.time >= mvFrom);
    const mvHist = mvFirst < 0 ? 0 : mvFirst;
    const mvLayer = createModernVwapLayer(
      chart,
      candle,
      () => {
        // The parameters through the ref, not the prop: this closure lives as
        // long as the chart does, and a knob turn must reach it without a
        // rebuild (which would cost the user their zoom).
        const p = mvRef.current;
        return {
          bars,
          histCount: mvHist,
          params: p,
          ctx: { poc: p?.pocSource === "weekly" ? mvWeeklyPoc : mvGlobexPoc, tickSize },
        };
      },
      onMvData,
    );
    mvLayer.attachSignals();
    mvLayerRef.current = mvLayer;
    mvDrawRef.current = () => mvLayer.redraw();

    // The Zeiierman line beside it, off the same bars and the same context
    // boundary. It reads no profile, so there is nothing else to hand it.
    const dsvLayer = createDynamicSwingVwapLayer(
      chart,
      candle,
      () => ({ bars, histCount: mvHist, params: dsvRef.current }),
      onDsvData,
    );
    dsvLayer.attachOverlays();
    dsvLayerRef.current = dsvLayer;
    dsvDrawRef.current = () => dsvLayer.redraw();
    // A no-op while the oscillator's row is off — its series doesn't exist, and
    // turning it on is what runs the first pass.
    cvdOscDrawRef.current = drawCvdOsc;

    // User-anchored VWAP (the ⚓ tool). Computed here from the bars in the browser
    // — running Σv, Σpv, Σp²v over each bar's typical price (H+L+C)/3 from the
    // anchor bar forward, the same bar-derived formula the journal charts use
    // (api/charts_data._vwap_rows), NOT the engine's tick-derived σ. Redrawn
    // imperatively whenever the anchor moves; the anchor is a bar *time*, so it
    // re-snaps onto the current grid after a timeframe switch, exactly like the
    // fixed-range profiles.
    let avwap: { series: ISeriesApi<"Line">[]; band: VwapBandPrimitive } | null = null;
    // Tick-accumulated, via each bar's shipped moments — the same arithmetic the
    // engine runs on the replay chart and the same the session bands beside it
    // are drawn with. It used to accumulate hlc3 × volume, which made this one
    // tool mean two different things depending on which chart you dropped it on:
    // a mid a tick or three away and, near the anchor, a sigma tens of times
    // narrower, because a bar-domain sigma is zero at its own anchor bar while a
    // tick one already carries that bar's internal spread. See lib/vwap.
    const computeAvwap = (i0: number): VwapPoint[] => {
      const out: VwapPoint[] = [];
      const acc = new Vwap();
      for (let i = i0; i <= last; i++) {
        const b = bars[i];
        acc.addBar(b);
        if (!acc.active) continue; // no volume yet -> no defined VWAP
        const { mid, sd } = acc.read();
        out.push({
          time: b.time,
          middle: mid,
          upper1: mid + sd,
          lower1: mid - sd,
          upper2: mid + 2 * sd,
          lower2: mid - 2 * sd,
        });
      }
      return out;
    };
    const drawAvwap = () => {
      if (avwap) {
        for (const s of avwap.series) chart.removeSeries(s);
        candle.detachPrimitive(avwap.band as any);
        avwap = null;
      }
      const t = avwapAnchorRef.current;
      if (t == null) return;
      // The tape can change under a live anchor: a timeframe switch re-grids it
      // (re-snapping is the point), but a new session window can also slide the
      // anchored bar out of the loaded range entirely — and nearestIdx would
      // then clamp to the first bar and draw a VWAP nobody asked for. Drop the
      // anchor instead. The slack covers a re-grid moving an endpoint by up to
      // one bar at the coarsest resolution the charts draw.
      if (t < barTimes[0] - GRID_SLACK_S || t > barTimes[last] + GRID_SLACK_S) {
        avwapAnchorRef.current = null;
        avwapSyncRef.current();
        return;
      }
      const pts = computeAvwap(nearestIdx(t));
      if (pts.length === 0) return;
      avwap = addVwap(pts, hues.vwap.anchored);
      const on = visRef.current.vwapAnchored;
      for (const s of avwap.series) s.applyOptions({ visible: on });
      avwap.band.setVisible(on);
    };
    avwapDrawRef.current = drawAvwap;
    drawAvwap(); // restore the anchor across a rebuild (data / timeframe change)

    // Developing value areas, one per anchor: VAH and VAL solid (they are the
    // levels the rules actually test against), POC dashed between them, each in its
    // anchor's colour. Deliberately not shaded bands — the VWAP envelope already
    // owns that visual, and stacking fills where the two areas overlap (the whole
    // setup) would be unreadable.
    const addProfile = (
      pts: ProfilePoint[] | undefined,
      pal: { edge: string; poc: string },
    ): ISeriesApi<"Line">[] => {
      if (!pts || pts.length === 0) return [];
      const lines = [
        { key: "vah", color: pal.edge, style: 0, width: 2 },
        { key: "val", color: pal.edge, style: 0, width: 2 },
        { key: "poc", color: pal.poc, style: 2, width: 2 },
      ] as const;
      return lines.map((l) => {
        const s_ = chart.addSeries(LineSeries, {
          color: l.color,
          lineWidth: l.width as 1 | 2,
          lineStyle: l.style,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        s_.setData(gappedLineData(pts, l.key));
        return s_;
      });
    };
    const profileGlobexSeries = addProfile(profileGlobex, hues.profile.globex);
    const profileNySeries = addProfile(profileNy, hues.profile.ny);
    const profileWeeklySeries = addProfile(profileWeekly, hues.profile.weekly);

    // 9/20 EMA (1-minute). The values arrive stamped on the minute they were
    // computed on, but the candles may be tick bars — and an off-grid time has no
    // coordinate — so each drawn bar takes the latest EMA at or before its close,
    // exactly the forward-walk the regime ribbon uses. On the 1-minute chart the
    // stamps already match the bars, so this is a straight copy; on tick bars it
    // samples the minute line onto the tick grid (flat within a minute, stepping
    // at each new one), keeping the line a true 1-minute EMA either way.
    const addEma = (pts: EmaPoint[] | undefined, color: string): ISeriesApi<"Line"> | null => {
      if (!pts || pts.length === 0) return null;
      const s_ = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const data: { time: Time; value: number }[] = [];
      let j = 0;
      for (const b of bars) {
        while (j + 1 < pts.length && pts[j + 1].time <= b.time) j++;
        const p = pts[j];
        if (!p || p.time > b.time) continue; // a bar before the first EMA point
        data.push({ time: b.time as Time, value: p.value });
      }
      s_.setData(data);
      return s_;
    };
    // Each EMA carries its own toggle key so the four lines hide/show
    // independently (see the visibility pass below and the legend rows).
    const emaSpecs: { key: IndicatorKey; pts: EmaPoint[] | undefined; color: string }[] = [
      { key: "ema9", pts: ema9, color: hues.ema.fast },
      { key: "ema20", pts: ema20, color: hues.ema.slow },
      { key: "ema50", pts: ema50, color: hues.ema.trend50 },
      { key: "ema200", pts: ema200, color: hues.ema.trend200 },
    ];
    const emaSeries: { key: IndicatorKey; series: ISeriesApi<"Line"> }[] = [];
    for (const spec of emaSpecs) {
      const s = addEma(spec.pts, spec.color);
      if (s) emaSeries.push({ key: spec.key, series: s });
    }

    if (markers && markers.length > 0) {
      const barMap = new Map(bars.map((b) => [b.time, b]));
      const snappedMarkers = markers.map((m) => ({ ...m, time: nearestBar(m.time) }));
      candle.attachPrimitive(new MarkerPrimitive(snappedMarkers, barMap) as any);
    }

    // Interaction overlay (touch dots + VA-snap markers). Attached unconditionally
    // so its toggles exist even before the arrays fill; empty arrays draw nothing.
    // Events are stamped on the minute grid, but the candles may be tick bars —
    // and timeToCoordinate returns null for any off-grid time — so snap each mark
    // onto the actual bar grid, exactly as the native markers above are snapped.
    const snappedTouches = (touches ?? []).map((t) => ({ ...t, ts: nearestBar(t.ts) }));
    const snappedSnaps = (vaSnaps ?? []).map((s) => ({ ...s, ts: nearestBar(s.ts) }));
    const interactionPrim = new InteractionPrimitive(snappedTouches, snappedSnaps);
    candle.attachPrimitive(interactionPrim as any);

    for (const pl of priceLines ?? []) {
      candle.createPriceLine({
        price: pl.price,
        color: pl.color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: pl.title,
      });
    }

    const levelLines: IPriceLine[] = (levels ?? []).map((lv) =>
      candle.createPriceLine({
        price: lv.price,
        color: lv.color,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: lv.title,
      }),
    );

    // Initial Balance: high/low as flat segments spanning the bell → the close —
    // line series rather than price lines, because an IB doesn't exist over the
    // overnight candles and a full-pane line would draw it there. The extension
    // guides (±1×/1.5×/2× of the IB range beyond each edge, the study's ext_x
    // units) start where the IB completes, and are excluded from autoscale: on a
    // narrow-IB day they sit far outside the traded range, and toggling them on
    // must not crush the candles.
    const ibSeries: ISeriesApi<"Line">[] = [];
    const ibExtSeries: ISeriesApi<"Line">[] = [];
    // One overlay (single-session sim charts) or many (a session per day on the
    // Interactions/Drafts tapes). Each is drawn bell → its own session's close,
    // so the segments never bleed across the overnight into the next session.
    for (const one of ibList) {
      const ibSeg = (
        price: number,
        from: number,
        into: ISeriesApi<"Line">[],
        opts: { color: string; style: 0 | 2; guide?: boolean },
      ) => {
        if (one.end <= from) return; // degenerate session: nothing to span
        const s_ = chart.addSeries(LineSeries, {
          color: opts.color,
          lineWidth: 1,
          lineStyle: opts.style,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          ...(opts.guide ? { autoscaleInfoProvider: () => null } : {}),
        });
        s_.setData([
          { time: from as Time, value: price },
          { time: one.end as Time, value: price },
        ]);
        into.push(s_);
      };
      ibSeg(one.high, one.start, ibSeries, { color: hues.ib.line, style: 0 });
      ibSeg(one.low, one.start, ibSeries, { color: hues.ib.line, style: 0 });
      const ibRange = one.high - one.low;
      for (const m of [1, 1.5, 2]) {
        for (const p of [one.high + m * ibRange, one.low - m * ibRange]) {
          ibSeg(p, one.formed, ibExtSeries, { color: hues.ib.ext, style: 2, guide: true });
        }
      }
    }

    // Every profile on this chart — the viewport-following one and each
    // fixed-range one — is a slice of bars, so they all resolve through here.
    // With a footprint we sum the real tape over those bars; without one we fall
    // back to spreading each bar's volume across its range.
    const exact = footprint != null && footprint.length === bars.length && tickSize != null;
    const profileFor = (i0: number, i1: number): VolumeProfile | null => {
      if (i1 < i0) return null;
      if (exact) {
        const entries: number[][] = [];
        for (let i = i0; i <= i1; i++) entries.push(...footprint![i]);
        return computeTickProfile(entries, tickSize!);
      }
      return computeVolumeProfile(bars.slice(i0, i1 + 1));
    };

    // And how that profile's delta lane is read — resolved here for the same
    // reason the profile is: every lane on this chart is a slice of these bars,
    // and one function is what stops the viewport histogram and the dragged one
    // from answering the same question differently.
    //
    // Classification is the only expensive part and it is gated three ways: the
    // knob has to be on, some row has to have been flagged, and the chart needs a
    // footprint to look at. When all three hold it costs one pass over the
    // window's trades — for *every* flagged row at once, not one pass each,
    // which is what keeps a pan from paying per mark. `reprofile` below only
    // calls this when the visible bar range actually moved, so it is once per pan
    // step rather than once per frame.
    const laneFor = (
      p: VolumeProfile | null,
      i0: number,
      i1: number,
      /** False for the dragged slices, whose right edge is the reader's rather
       *  than the market's — see RangeProfileItem.lane. Also spares a drag the
       *  pass over its trades on every mousemove. */
      allowVerdict = true,
      /** True only for the viewport lane. A fixed range's span *is* the window
       *  the reader drew, so the window knob never re-cuts it. */
      follow = false,
    ): LaneReading | null => {
      if (!p || !p.hasDelta) return null;
      const k = deltaLaneRef.current;
      // The timed and visit windows both need the trades cut by bar, which on
      // this chart is the footprint — without one the lane holds the session
      // reading whatever the knob says, the same degradation classify has.
      const win = follow && exact ? k.window : "session";

      if (win === "visit" && i0 >= 0 && i1 >= i0) {
        const split = splitVisits(p, bars.slice(i0, i1 + 1), (emit) => {
          for (let b = i0; b <= i1; b++)
            for (const e of footprint![b]) emit(b - i0, e[0], e[2] ?? 0);
        });
        return readVisitLane(p, split);
      }

      // The timed windows re-source the lane; everything after this block reads
      // `src` and the `vi0..vi1` bars behind it, so flags, scales and verdicts
      // are all statements about the same stretch of tape.
      let src: LaneSource = p;
      let vi0 = i0;
      let vi1 = i1;
      const mins = LANE_WINDOW_MINUTES[win];
      if (mins != null && i0 >= 0 && i1 >= i0) {
        const cut = bars[i1].time - mins * 60;
        let wi0 = i0;
        while (wi0 < i1 && bars[wi0].time <= cut) wi0++;
        // A span already inside the window is its own window — the session lane.
        if (wi0 > i0) {
          const entries: number[][] = [];
          for (let i = wi0; i <= i1; i++) entries.push(...footprint![i]);
          const ws = windowedOnto(p, computeTickProfile(entries, tickSize!));
          if (ws) {
            src = ws;
            vi0 = wi0;
          }
        }
      }

      const lane = readLane(src, k.scale, k.flagSigma);
      if (!allowVerdict || !k.classify || lane.flagged.length === 0 || !exact || vi1 < vi0)
        return lane;

      const n = vi1 - vi0 + 1;
      const table = new Map<number, Float64Array>();
      for (const r of lane.flagged) table.set(r, new Float64Array(n));
      for (let b = vi0; b <= vi1; b++) {
        for (const e of footprint![b]) {
          // Row membership off the row's own bounds rather than by repeating
          // computeTickProfile's binning arithmetic: a second copy of that
          // rounding is a second thing to keep in step, and a row that means
          // something slightly different here would misattribute the delta it is
          // being judged on.
          for (const r of lane.flagged) {
            const row = src.rows[r];
            if (e[0] >= row.low && e[0] < row.high) {
              table.get(r)![b - vi0] += e[2] ?? 0;
              break;
            }
          }
        }
      }
      lane.verdict = classifyFlagged(
        src,
        lane.flagged,
        (r) => table.get(r)!,
        bars.slice(vi0, vi1 + 1),
      );
      return lane;
    };

    // Volume profile over whatever bars are on screen: the histogram itself is a
    // primitive (nothing native runs along the price axis), while POC/VAH/VAL are
    // price lines so they get axis labels and span the full pane for free.
    const vp = new VolumeProfilePrimitive(profileFor(0, last));
    candle.attachPrimitive(vp as any);
    // The bar window `vp.profile` currently covers. Kept beside the primitive
    // rather than derived from it because a lane's verdicts are measured against
    // those bars, and a profile knows its prices but not which slice made it.
    const vpWin = { i0: 0, i1: last };
    vp.setLane(laneFor(vp.profile, vpWin.i0, vpWin.i1, true, true));

    const VP_LINES = [
      { key: "poc", color: palette.gold, style: 0, title: "POC" },
      { key: "vah", color: palette.blue, style: 2, title: "VAH" },
      { key: "val", color: palette.blue, style: 2, title: "VAL" },
    ] as const;
    let vpLines: IPriceLine[] = [];

    const syncProfileLines = (p: VolumeProfile | null) => {
      const on = visRef.current.volumeProfile && p != null;
      // A window with no traded range (every bar flat) has no profile to label.
      if (p && vpLines.length === 0) {
        vpLines = VP_LINES.map((spec) =>
          candle.createPriceLine({
            price: p[spec.key],
            color: spec.color,
            lineWidth: 1,
            lineStyle: spec.style,
            axisLabelVisible: true,
            title: spec.title,
          }),
        );
      }
      vpLines.forEach((line, i) =>
        line.applyOptions({
          ...(p ? { price: p[VP_LINES[i].key] } : {}),
          lineVisible: on,
          axisLabelVisible: on,
        }),
      );
    };

    syncProfileLines(vp.profile);

    // Re-profile on pan/zoom so zooming into one session profiles that session
    // rather than the whole loaded window. Logical range is fractional and can
    // run past the data on both ends, so clamp it back onto real bar indices.
    let lastFrom = -1;
    let lastTo = -1;
    const reprofile = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const from = Math.max(0, Math.ceil(range.from));
      const to = Math.min(bars.length - 1, Math.floor(range.to));
      if (to < from || (from === lastFrom && to === lastTo)) return;
      lastFrom = from;
      lastTo = to;
      const p = profileFor(from, to);
      vpWin.i0 = from;
      vpWin.i1 = to;
      vp.setProfile(p);
      vp.setLane(laneFor(p, from, to, true, true));
      syncProfileLines(p);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(reprofile);

    // --- The session's own distribution, and the days behind it ---------------
    //
    // Two histograms in two gutters, answering two questions the value-area
    // *lines* above cannot: what shape the session has built getting where it is,
    // and what shape the auction in front of it left behind. Both are read at the
    // knobs held above, and both name their HVN/LVN nodes at one shared
    // prominence — see lib/volumeProfile.profileNodes.
    //
    // The composite is frozen by construction: it is built from the prior
    // sessions' volume alone, never this one's. Letting today feed it is circular
    // — the POC drifts toward price, the level can never be violated, and every
    // touch looks like a hold.
    const compPrim = new CompositeProfilePrimitive();
    candle.attachPrimitive(compPrim as any);
    const devPrim = new DevelopingProfilePrimitive();
    candle.attachPrimitive(devPrim as any);

    // The coarser bar over this one. Owns both z-orders internally, so where it
    // sits relative to the candles is a setting rather than an attach order.
    const extPrim = new ExternalChartPrimitive();
    extPrim.setParams(extParamsRef.current);
    extPrim.setBars(extBarsRef.current);
    candle.attachPrimitive(extPrim as any);
    extPrimRef.current = extPrim;

    // The session's histogram, computed once: it is frozen too. A journal chart
    // draws a session that has already happened, so "developing" here means the
    // whole of it — there is no clock left to grow it.
    const nyProfile = nyStart >= 0 ? profileFor(nyStart, nyEnd) : null;
    // The value area as the *server* has it, at tick resolution — the numbers the
    // developing NY lines are drawn from. Binning to readable rows moves VAH by
    // points, and shading rows by a number the lines beside them disagree with
    // would be a bug you could see. Shape from the histogram, levels from the
    // engine. (See DevelopingProfilePrimitive.DevelopingVa.)
    const nyVa = profileNy && profileNy.length > 0 ? profileNy[profileNy.length - 1] : null;

    /** The composite as the current rule reads the context days, and the nodes
     *  both layers name at the current prominence. Recomputed on a knob turn and
     *  on a rebuild; the profiles themselves never change under it. */
    let comp: Composite | null = null;
    let compRule: string | null = null;
    const paintProfiles = () => {
      const k = knobsRef.current;
      if (ctxTape && tickSize) {
        // Only re-walk the days when the question changed — the balance rule is
        // a profile per candidate day, and a prominence turn is not a new
        // composite, only a new reading of one.
        const sig = `${k.composite}/${k.compositeSpan}`;
        if (sig !== compRule) {
          compRule = sig;
          comp = buildComposite(
            ctxTape.level,
            ctxTape.size,
            k.compositeSpan === "rth" ? ctxTape.rth : ctxTape.globex,
            tickSize,
            k.composite,
          );
        }
      } else {
        comp = null;
      }
      setCompDays(comp?.days ?? 0);
      compPrim.setData(
        comp
          ? {
              profile: comp.profile,
              nodes: k.nodeProm > 0 ? profileNodes(comp.profile, k.nodeProm) : null,
              // No stretch of tape to pin it to: the days it was measured over
              // are not drawn on a journal chart, so the shape goes in a gutter
              // and the levels run the full width. See CompositeData.from.
              from: null,
              to: null,
              days: comp.days,
            }
          : null,
      );
      devPrim.setData(
        nyProfile
          ? {
              profile: nyProfile,
              va: nyVa ? { poc: nyVa.poc, vah: nyVa.vah, val: nyVa.val } : null,
              nodes: k.nodeProm > 0 ? profileNodes(nyProfile, k.nodeProm) : null,
              from: bars[nyStart].time,
            }
          : null,
      );
      compPrim.setVisible(visRef.current.compositeProfile, visRef.current.compositeNodes);
      devPrim.setVisible(visRef.current.developingVpNy, visRef.current.developingVpNyNodes);
      extPrim.setVisible(visRef.current.externalChart);
    };
    profilesRef.current = paintProfiles;
    paintProfiles();

    // --- Volume shelves: where size is *building*, not where it has been ---
    //
    // Computed off the same `profileFor` every other profile on this chart uses,
    // so a shelf is read from the identical distribution the histogram draws.
    // `exact` is handed straight through: on a chart with no footprint the
    // profile is the bar-spreading estimate, under which size-per-visit is
    // near-constant by construction, and every band would be an artefact of the
    // estimator rather than a reading (see lib/volumeShelf.detectShelves).
    const shelfPrim = new VolumeShelfPrimitive();
    candle.attachPrimitive(shelfPrim as any);

    // The walked session, kept so switching a layer on and off does not re-walk
    // it. Everything the walk reads — the bars, the tick size, the parameters —
    // is fixed for the life of this effect except the parameters, and those
    // arrive through `rebuild`. Visibility is not an input to the reading at all,
    // only to what is drawn of it, so a legend click must not cost a session.
    let built: { columns: ShelfColumn[]; boxes: ShelfBox[] } | null = null;

    const paintShelves = (rebuild = false) => {
      if (rebuild) built = null;
      const v = visRef.current;
      if (!v.volumeShelf || tickSize == null) {
        shelfPrim.setData({
          columns: [], boxes: [], showBoxes: false, zMin: 0,
          field: "size", flowAvailable: false,
        });
        return;
      }
      const params = shelfParamsRef.current;
      if (!built) {
        const shelfBars = bars.map((b) => ({
          time: b.time,
          high: b.high,
          low: b.low,
        }));
        const tracker = new ShelfTracker(2, params.minHoldMin * 60);
        const columns: ShelfColumn[] = [];
        for (const i of evalBars(shelfBars, params.stepSec)) {
          const j = windowStart(shelfBars, i, params.windowMin);
          const prof = profileFor(j, i);
          const win = shelfBars.slice(j, i + 1);
          const { shelves, reading } = detectShelves(prof, win, exact, tickSize, params);
          tracker.push(shelfBars[i].time, shelves, win);
          if (prof && reading) {
            const flow = shelfFlow(prof) ?? undefined;
            columns.push({ time: shelfBars[i].time, rows: prof.rows, z: reading.z, flow });
          }
        }
        built = { columns, boxes: tracker.boxes() };
      }
      shelfPrim.setData({
        columns: built.columns,
        boxes: built.boxes,
        showBoxes: v.volumeShelfBoxes,
        zMin: params.zMin,
        field: shelfFieldRef.current,
        flowAvailable: built.columns.some((c) => c.flow != null),
      });
    };
    shelvesRef.current = paintShelves;
    paintShelves();

    // --- Fixed-range profile: drag across the chart to profile just that slice ---
    const rangePrim = new RangeProfilePrimitive();
    candle.attachPrimitive(rangePrim as any);

    // --- Ruler: drag between two points to measure the move between them ---
    const ruler = new RulerPrimitive(tickSize, pointValue);
    candle.attachPrimitive(ruler as any);

    // Repaint every profile from the ref. Each range's edges are re-snapped onto
    // the *current* bar grid: ranges are stored as the times the user dragged
    // over, which after a timeframe switch are no longer bar times — and an
    // off-grid time has no coordinate, so the profile would vanish.
    const paint = () => {
      rangePrim.setData(
        rangesRef.current.map((r) => {
          const i0 = nearestIdx(r.from);
          const i1 = nearestIdx(r.to);
          const p = profileFor(i0, i1);
          return {
            id: r.id,
            from: barTimes[i0],
            to: barTimes[i1],
            profile: p,
            // Scale and flags only — RangeProfileItem.lane says why a dragged
            // slice gets no verdicts.
            lane: laneFor(p, i0, i1, false),
          };
        }),
        selectedRef.current,
      );
    };
    paintRef.current = paint;
    paint(); // restore existing profiles across a rebuild

    const host = ref.current;
    const xOf = (e: MouseEvent) => e.clientX - host.getBoundingClientRect().left;
    const yOf = (e: MouseEvent) => e.clientY - host.getBoundingClientRect().top;
    const idxAtX = (x: number): number | null => {
      const logical = chart.timeScale().coordinateToLogical(x);
      if (logical == null) return null;
      return Math.min(last, Math.max(0, Math.round(logical)));
    };
    // Ruler corners snap to the tick grid (when known) — a measurement in
    // fractional ticks is never what anyone wants.
    const priceAtY = (y: number): number | null => {
      const p = candle.coordinateToPrice(y);
      if (p == null) return null;
      return tickSize ? Math.round(p / tickSize) * tickSize : p;
    };
    const measureOf = (i1: number, p1: number, i2: number, p2: number) => ({
      t1: barTimes[i1],
      p1,
      t2: barTimes[i2],
      p2,
      bars: Math.abs(i2 - i1),
      seconds: Math.abs(barTimes[i2] - barTimes[i1]),
    });

    // What's under the pointer, topmost (most recently drawn) first. Edges win
    // over bodies so a narrow profile is still resizable.
    const hitTest = (x: number): { id: number; mode: DragMode } | null => {
      const ts = chart.timeScale();
      for (let i = rangesRef.current.length - 1; i >= 0; i--) {
        const r = rangesRef.current[i];
        const a = ts.timeToCoordinate(nearestBar(r.from) as Time);
        const b = ts.timeToCoordinate(nearestBar(r.to) as Time);
        if (a == null || b == null) continue;
        const x1 = Math.min(a, b);
        const x2 = Math.max(a, b);
        if (Math.abs(x - x1) <= HANDLE_PX) return { id: r.id, mode: "left" };
        if (Math.abs(x - x2) <= HANDLE_PX) return { id: r.id, mode: "right" };
        if (x > x1 && x < x2) return { id: r.id, mode: "move" };
      }
      return null;
    };

    // Panning is a left-drag too, so it must be off whenever a left-drag means
    // something else: while the tool is armed, or while the pointer is over a
    // profile the user could grab. Only the *pressed-drag* gesture conflicts —
    // blanket `handleScroll: false` would also deaden the mouse wheel, so the
    // wheel (and zooming) keep working while a profile is under the cursor.
    // Toggled only on change: applyOptions on every mousemove would be wasteful.
    let scrollOff = false;
    const setScroll = (off: boolean) => {
      if (off === scrollOff) return;
      scrollOff = off;
      chart.applyOptions({
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: !off,
          horzTouchDrag: !off,
          vertTouchDrag: !off,
        },
      });
    };
    armApplyRef.current = (a: boolean) => {
      setScroll(a || rulerArmedRef.current || avwapArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
    };
    armApplyRef.current(armedRef.current);

    // Drag state, in bar-index space rather than time: the bar grid has gaps
    // (weekends, overnight), so shifting a range by a time delta would smear it.
    // Indices move it by whole bars, which is what the user sees.
    let drag: { mode: DragMode; id: number; anchorIdx: number; from: number; to: number } | null =
      null;
    let downX = 0;
    let downY = 0;
    // The ruler's anchor while a measurement is being drawn. Survives mouseup on
    // a no-move click, so both TV gestures work: press-drag-release and
    // click-move-click.
    let rulerDrag: { i1: number; p1: number } | null = null;
    // Set when a mousedown placed a VWAP anchor, so the trade-click that
    // lightweight-charts fires from the same click doesn't also open a trade
    // (the anchor may land on top of a trade rectangle).
    let avwapConsumedClick = false;

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const x = xOf(e);
      const idx = idxAtX(x);
      if (idx == null) return;
      downX = x;
      downY = yOf(e);

      if (avwapArmedRef.current) {
        // Anchor the VWAP on the clicked bar, draw it, and put the tool away.
        // Un-hide the layer first: its legend row only exists once an anchor is
        // placed, so hiding it once (easy — it's the row that just appeared)
        // would otherwise make every later anchor land invisible, and the tool
        // read as dead.
        revealRef.current("vwapAnchored");
        avwapAnchorRef.current = barTimes[idx];
        avwapConsumedClick = true;
        drawAvwap();
        armAvwapRef.current(false);
        avwapSyncRef.current(); // reflect the anchor in the toolbar + legend
        e.preventDefault();
        return;
      }

      if (rulerArmedRef.current) {
        if (rulerDrag) {
          // Second click of click-move-click: the measurement is done.
          rulerDrag = null;
          armRulerRef.current(false);
          e.preventDefault();
          return;
        }
        const p = priceAtY(downY);
        if (p == null) return;
        rulerDrag = { i1: idx, p1: p };
        ruler.setData(measureOf(idx, p, idx, p));
        e.preventDefault();
        return;
      }
      // Any plain press dismisses a finished measurement, like TV's ruler.
      if (ruler.data()) ruler.setData(null);

      if (armedRef.current) {
        const id = nextIdRef.current++;
        rangesRef.current = [...rangesRef.current, { id, from: barTimes[idx], to: barTimes[idx] }];
        selectedRef.current = id;
        drag = { mode: "new", id, anchorIdx: idx, from: idx, to: idx };
        e.preventDefault();
        paint();
        return;
      }

      const hit = hitTest(x);
      if (!hit) {
        // Clicking bare chart deselects — but let the chart pan as usual.
        if (selectedRef.current != null) {
          selectedRef.current = null;
          syncRef.current();
        }
        return;
      }
      const r = rangesRef.current.find((v) => v.id === hit.id)!;
      selectedRef.current = hit.id;
      drag = {
        mode: hit.mode,
        id: hit.id,
        anchorIdx: idx,
        from: nearestIdx(r.from),
        to: nearestIdx(r.to),
      };
      e.preventDefault();
      if (hit.mode === "move") host.style.cursor = "grabbing";
      syncRef.current();
    };

    const onMove = (e: MouseEvent) => {
      const x = xOf(e);
      if (rulerDrag) {
        const idx = idxAtX(x);
        const p = priceAtY(yOf(e));
        if (idx == null || p == null) return;
        ruler.setData(measureOf(rulerDrag.i1, rulerDrag.p1, idx, p));
        return;
      }
      if (!drag) {
        // Idle hover: advertise what a grab here would do, and take the mouse away
        // from the chart's panning so the grab actually lands.
        if (armedRef.current || rulerArmedRef.current || avwapArmedRef.current) return;
        const hit = hitTest(x);
        setScroll(hit != null);
        host.style.cursor = !hit ? "" : hit.mode === "move" ? "grab" : "col-resize";
        return;
      }

      const idx = idxAtX(x);
      if (idx == null) return;
      const r = rangesRef.current.find((v) => v.id === drag!.id);
      if (!r) return;

      if (drag.mode === "move") {
        // Slide by whole bars, clamped so the range keeps its width at the edges.
        const width = drag.to - drag.from;
        let from = drag.from + (idx - drag.anchorIdx);
        from = Math.min(last - width, Math.max(0, from));
        r.from = barTimes[from];
        r.to = barTimes[from + width];
      } else {
        // Resizing: the grabbed edge follows the pointer, the other stays put, and
        // dragging one past the other just flips which is which.
        const fixed = drag.mode === "new" ? drag.anchorIdx : drag.mode === "left" ? drag.to : drag.from;
        r.from = barTimes[Math.min(idx, fixed)];
        r.to = barTimes[Math.max(idx, fixed)];
      }
      paint(); // primitive only — no React re-render mid-drag
    };

    const onUp = (e: MouseEvent) => {
      if (rulerDrag) {
        // A real drag ends the measurement here; a stationary click leaves the
        // anchor live so the pointer keeps stretching it (click-move-click).
        if (Math.hypot(xOf(e) - downX, yOf(e) - downY) >= 5) {
          rulerDrag = null;
          armRulerRef.current(false);
        }
        return;
      }
      if (!drag) return;
      const wasNew = drag.mode === "new";
      const id = drag.id;
      const moved = Math.abs(xOf(e) - downX);
      drag = null;

      // A click with no real drag means "never mind" — don't leave a hairline
      // profile of a single bar behind.
      if (wasNew && moved < 5) {
        rangesRef.current = rangesRef.current.filter((r) => r.id !== id);
        selectedRef.current = null;
      }
      if (wasNew) disarmRef.current();
      syncRef.current();
    };

    // Below the handlers because both close over `rulerDrag`: disarming or
    // clearing must also drop an in-flight anchor, or the measurement would keep
    // chasing the pointer after Esc / toggling the tool off.
    rulerApplyRef.current = (a: boolean) => {
      setScroll(a || armedRef.current || avwapArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
      if (a) ruler.setData(null); // re-arming starts a fresh measurement
      else rulerDrag = null;
    };
    rulerApplyRef.current(rulerArmedRef.current);
    rulerClearRef.current = () => {
      rulerDrag = null;
      ruler.setData(null);
    };

    // Arming the ⚓ tool just sets the crosshair and takes the pointer off panning
    // so the anchor click lands cleanly; the actual placement happens in onDown.
    avwapApplyRef.current = (a: boolean) => {
      setScroll(a || armedRef.current || rulerArmedRef.current);
      if (ref.current) ref.current.style.cursor = a ? "crosshair" : "";
    };
    avwapApplyRef.current(avwapArmedRef.current);

    host.addEventListener("mousedown", onDown);
    // Move/up on the window, so a drag that leaves the chart still tracks and,
    // more importantly, still terminates.
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);

    /** Which of the optional panes are up, as one string — the thing the study
     *  panes' indices depend on. */
    const panesSig = () =>
      `${atrSeries ? 1 : 0}${cvdSeries ? 1 : 0}${cvdOscSeries ? 1 : 0}${rsiSeries ? 1 : 0}${
        vrDev ? 1 : 0
      }`;
    let lastPanes = panesSig();

    applyRef.current = (v: Visibility) => {
      vp.setVisible(v.volumeProfile);
      for (const l of vpLines)
        l.applyOptions({ lineVisible: v.volumeProfile, axisLabelVisible: v.volumeProfile });
      // A fixed anchor's row toggles the whole thing; inside it, the band knob
      // says which rings survive. The mid is not on that knob, so it is on
      // whenever the anchor is — see chartPrefs.vwapBandsShown, which also owns
      // the rule that the wash needs both of its edges to be honest.
      const setAnchor = (
        a: { series: ISeriesApi<"Line">[]; sigma1: ISeriesApi<"Line">[]; sigma2: ISeriesApi<"Line">[]; band: VwapBandPrimitive } | null,
        on: boolean,
        key: VwapFillAnchor,
      ) => {
        if (!a) return;
        const region = regionRef.current[key];
        const s = vwapBandsShown(bandsRef.current[key], region);
        a.band.setRegion(region);
        for (const line of a.series) line.applyOptions({ visible: on });
        for (const line of a.sigma1) line.applyOptions({ visible: on && s.s1 });
        for (const line of a.sigma2) line.applyOptions({ visible: on && s.s2 });
        a.band.setVisible(on && s.fill);
      };
      setAnchor(globex, v.vwapGlobex, "globex");
      setAnchor(ny, v.vwapNy, "ny");
      setAnchor(weekly, v.vwapWeekly, "weekly");
      // The ⚓ band is not one of the three: it is drawn one at a time, by hand,
      // at the place you asked about, and it keeps its whole envelope.
      for (const s of avwap?.series ?? []) s.applyOptions({ visible: v.vwapAnchored });
      avwap?.band.setVisible(v.vwapAnchored);
      for (const s of profileGlobexSeries) s.applyOptions({ visible: v.developingProfileGlobex });
      for (const s of profileNySeries) s.applyOptions({ visible: v.developingProfileNy });
      for (const s of profileWeeklySeries) s.applyOptions({ visible: v.developingProfileWeekly });
      for (const { key, series } of emaSeries) series.applyOptions({ visible: v[key] });
      for (const l of levelLines) l.applyOptions({ lineVisible: v.levels, axisLabelVisible: v.levels });
      for (const s of ibSeries) s.applyOptions({ visible: v.initialBalance });
      for (const s of ibExtSeries) s.applyOptions({ visible: v.ibExtensions });
      interactionPrim.setVisibility(v.touches, v.va_snaps);
      // The line's seven series hide as a unit and the marks have their own eye.
      // The layer redraws itself when this takes it out of, or into, dark — with
      // both rows off it isn't drawn *or computed*, which is why it can be built
      // on every chart without costing the ones that never show it.
      mvLayer.setVisible(v.modernVwap, v.modernVwapSignals);
      // One row: the flags are how the line is read, not a separate claim.
      dsvLayer.setVisible(v.dynamicSwingVwap);
      if (atrPoints && atrPoints.length > 0) {
        if (v.atr && !atrSeries) addAtr();
        else if (!v.atr && atrSeries) {
          chart.removeSeries(atrSeries);
          atrSeries = null;
        }
      }
      if (cvd && cvd.length > 0) {
        if (v.cvd && !cvdSeries) addCvd();
        else if (!v.cvd && cvdSeries) {
          chart.removeSeries(cvdSeries);
          cvdSeries = null;
        }
      }
      if (deltaByBar) {
        if (v.cvdOsc && !cvdOscSeries) addCvdOsc();
        else if (!v.cvdOsc && cvdOscSeries) removeCvdOsc();
      }
      if (rsi && rsi.length > 0) {
        if (v.rsi && !rsiSeries) addRsi();
        else if (!v.rsi && rsiSeries) {
          chart.removeSeries(rsiSeries);
          rsiSeries = null;
        }
      }
      // The vol ruler's two lines are one pane, so they come and go together —
      // removing the last series of a pane is what drops the pane.
      if (v.volRuler && !vrDev) addVr();
      else if (!v.volRuler && vrDev) {
        chart.removeSeries(vrDev);
        if (vrAtr) chart.removeSeries(vrAtr);
        vrDev = null;
        vrAtr = null;
      }
      // Any of the five panes above coming or going renumbers everything after
      // it, and the studies' panes have to stay last. Only when the set actually
      // changed: rebuilding them to discover nothing moved is the one way to make
      // a picked indicator flicker.
      if (panesSig() !== lastPanes) {
        lastPanes = panesSig();
        studyLayerRef.current?.remount();
      }
      // Both histograms hide as a unit with their nodes' own eye beside them —
      // the shape in its gutter and the levels it names over the price action are
      // separately useful, and the second is the one that draws on the tape.
      compPrim.setVisible(v.compositeProfile, v.compositeNodes);
      devPrim.setVisible(v.developingVpNy, v.developingVpNyNodes);
      extPrim.setVisible(v.externalChart);
      // The shelves have no `setVisible`: both of their switches change what the
      // primitive is *given*, not whether it draws, so they go back through the
      // paint. It is the cached walk unless the reading itself changed.
      paintShelves();
    };
    applyFillRef.current = (w) => {
      globex?.band.setAlphaScale(w.globex);
      ny?.band.setAlphaScale(w.ny);
      weekly?.band.setAlphaScale(w.weekly);
    };
    // One switch over both histograms: a chart reading its viewport profile for
    // flow is reading the slice you dragged for it too.
    applyTintRef.current = (on) => {
      vp.setShowDelta(on);
      rangePrim.setShowDelta(on);
    };
    applyTintRef.current(profileDeltaRef.current);
    // Re-reading the lane needs no new profile — same rows, same volumes, a
    // different question asked of the delta already on them. `paint` re-derives
    // the dragged slices, which is where their own readings are attached.
    applyLaneRef.current = () => {
      vp.setLane(laneFor(vp.profile, vpWin.i0, vpWin.i1, true, true));
      paintRef.current?.();
    };
    // Seeded here for the same reason the visibility is below: the bands were
    // built at the authored weight, and a chart rebuilt for any other reason has
    // to come back carrying the reader's.
    applyFillRef.current(fillRef.current);
    // Which also gives the Modern VWAP its first draw, if either of its rows is
    // on: the layer starts dark, so switching it on there is the crossing that
    // makes it pull from its source.
    applyRef.current(visRef.current);

    // The picker's studies, built after every pane above has claimed its index:
    // pane indices are positional, and a study pane has to be the last one. The
    // specs come off the ref rather than the render's copy so a chart rebuilt for
    // some other reason comes back with what was on it.
    const studyLayer = new StudyLayer(chart, candle, setStudyReport);
    studyLayerRef.current = studyLayer;
    studyLayer.setSpecs(studiesRef.current);
    studyLayer.setBars(bars);

    if (tradeRects && tradeRects.length > 0) {
      // Snap entry down / exit up to bar boundaries so the rectangle spans the
      // whole holding period and its corners resolve to real coordinates.
      const snapped = tradeRects.map((r) => {
        let entry = floorBar(r.entry_time);
        let exit = ceilBar(r.exit_time);
        if (exit <= entry) {
          const idx = barTimes.indexOf(entry);
          exit = idx >= 0 && idx < last ? barTimes[idx + 1] : exit;
        }
        return { ...r, entry_time: entry, exit_time: exit };
      });
      candle.attachPrimitive(new TradeRectanglePrimitive(snapped) as any);

      // Hover a rect -> stats tooltip; click -> onTradeClick. Hit-testing runs
      // in pixel space against the snapped rects, padded so a near-flat rect
      // (scratch trade) is still hoverable. The rect is the hover target — the
      // 10px markers are too fiddly to aim at.
      const withStats = snapped.filter((r) => r.stats);
      if (withStats.length > 0 || onTradeClickRef.current) {
        const PAD_X = 3;
        const PAD_Y = 8;
        const hitRect = (x: number, y: number): TradeRect | null => {
          const ts = chart.timeScale();
          for (const r of snapped) {
            const x1 = ts.timeToCoordinate(r.entry_time as Time);
            const x2 = ts.timeToCoordinate(r.exit_time as Time);
            const y1 = candle.priceToCoordinate(r.entry_price);
            const y2 = candle.priceToCoordinate(r.exit_price);
            if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
            if (
              x >= Math.min(x1, x2) - PAD_X &&
              x <= Math.max(x1, x2) + PAD_X &&
              y >= Math.min(y1, y2) - PAD_Y &&
              y <= Math.max(y1, y2) + PAD_Y
            )
              return r;
          }
          return null;
        };

        chart.subscribeCrosshairMove((param) => {
          const tip = tipRef.current;
          const host = ref.current;
          if (!tip || !host) return;
          // The range tool owns the pointer while armed, and a profile owns it
          // wherever one is drawn: no rect tooltip, and don't stomp the cursor
          // those set. Deleting the profile gives the trade back.
          if (
            armedRef.current ||
            rulerArmedRef.current ||
            avwapArmedRef.current ||
            (param.point && hitTest(param.point.x))
          ) {
            tip.style.display = "none";
            return;
          }
          const r = param.point ? hitRect(param.point.x, param.point.y) : null;
          if (!r || !r.stats) {
            tip.style.display = "none";
            host.style.cursor = "";
            return;
          }
          tip.innerHTML = tradeTooltipHtml(r);
          tip.style.display = "block";
          const tw = tip.offsetWidth;
          const th = tip.offsetHeight;
          let left = param.point!.x + 14;
          if (left + tw > host.clientWidth - 8) left = param.point!.x - tw - 14;
          let top = param.point!.y - th - 12;
          if (top < 4) top = param.point!.y + 16;
          tip.style.left = `${Math.max(4, left)}px`;
          tip.style.top = `${top}px`;
          host.style.cursor = onTradeClickRef.current ? "pointer" : "";
        });

        chart.subscribeClick((param) => {
          const cb = onTradeClickRef.current;
          // Swallow the click that just placed a VWAP anchor.
          if (avwapConsumedClick) {
            avwapConsumedClick = false;
            return;
          }
          if (armedRef.current || rulerArmedRef.current || avwapArmedRef.current || !cb || !param.point)
            return;
          if (hitTest(param.point.x)) return; // a profile is covering this trade
          const r = hitRect(param.point.x, param.point.y);
          if (r) cb(r);
        });
      }
    }

    // The by-trade view opens zoomed onto the trade: frame the entry→exit span
    // with roughly its own width of context on each side (a floor so a scratch
    // trade of a couple of bars still gets breathing room). Logical range takes
    // fractional / past-the-end values, so the padding needs no clamping — it
    // just shows empty gutter at the ends of the session. Everything else fits
    // the whole loaded window as before.
    if (focusOnTrade && tradeRects && tradeRects.length > 0) {
      const r = tradeRects[0];
      const i0 = nearestIdx(r.entry_time);
      const i1 = nearestIdx(r.exit_time);
      const lo = Math.min(i0, i1);
      const hi = Math.max(i0, i1);
      const tk = tickSize ?? 0.25;

      // Horizontal (time): a FOCUS_BARS-wide window centred on the trade, widened
      // to the trade + margin if the trade is wider than the window.
      const cx = (lo + hi) / 2;
      const halfB = Math.max(FOCUS_BARS / 2, (hi - lo) / 2 + FOCUS_MARGIN_BARS);
      chart.timeScale().setVisibleLogicalRange({ from: cx - halfB, to: cx + halfB });

      // Vertical (price): a FOCUS_TICKS-tall window centred on the trade's price
      // action — the high/low of its own bars plus its entry / exit / stop levels,
      // same expand-to-fit rule. Uses the price scale's own range setter (v5),
      // which pins the vertical zoom; the user can still drag the price axis, and
      // double-click resets it to autoscale.
      let pMin = Infinity;
      let pMax = -Infinity;
      for (let i = lo; i <= hi; i++) {
        if (bars[i].low < pMin) pMin = bars[i].low;
        if (bars[i].high > pMax) pMax = bars[i].high;
      }
      for (const p of [r.entry_price, r.exit_price, r.stats?.stop_price]) {
        if (p == null) continue;
        if (p < pMin) pMin = p;
        if (p > pMax) pMax = p;
      }
      if (pMin <= pMax) {
        const cy = (pMin + pMax) / 2;
        const halfP = Math.max((FOCUS_TICKS * tk) / 2, (pMax - pMin) / 2 + FOCUS_MARGIN_TICKS * tk);
        chart.priceScale("right").setVisibleRange({ from: cy - halfP, to: cy + halfP });
      }

      // Zoom-tuning readout: the visible width (bars) and height (points/ticks) —
      // the two numbers that map straight to FOCUS_BARS / FOCUS_TICKS. Driven by
      // requestAnimationFrame so dragging the price axis updates it too (there's no
      // price-scale change event). Only runs when `debugZoom` is on.
      if (debugZoom) {
        const priceScale = chart.priceScale("right");
        const showDebug = () => {
          const el = debugRef.current;
          if (el) {
            const vr = chart.timeScale().getVisibleLogicalRange();
            const pr = priceScale.getVisibleRange();
            const xPart = vr ? `x: ${Math.round(vr.to - vr.from)} bars` : "x: —";
            const yPart = pr
              ? `y: ${(pr.to - pr.from).toFixed(1)}pt / ${Math.round((pr.to - pr.from) / tk)}t`
              : "y: —";
            el.textContent = `${xPart}  |  ${yPart}`;
          }
          debugRaf = requestAnimationFrame(showDebug);
        };
        debugRaf = requestAnimationFrame(showDebug);
      }
    } else if (initialRangeRef.current) {
      // A continuous multi-session tape: open on the selected day's span rather
      // than fitting every loaded session. Off-grid endpoints are fine — the
      // scale snaps them onto the nearest bars.
      chart.timeScale().setVisibleRange({
        from: initialRangeRef.current.from as Time,
        to: initialRangeRef.current.to as Time,
      });
    } else {
      chart.timeScale().fitContent();
    }

    // Re-cut every series this effect built, without rebuilding it — a rebuild
    // would throw away the zoom and scroll the appearance effect exists to
    // preserve. The canvas primitives need no call: they read the active ink
    // each frame. `avwap` is read through the closure rather than captured,
    // since the ⚓ tool replaces it whenever the anchor moves.
    relightRef.current = () => {
      hues = chartInk(appearanceRef.current.surface);
      const anchor = (
        a: { series: ISeriesApi<"Line">[]; band: VwapBandPrimitive } | null,
        h: { middle: string; band1: string; band2: string; fill: string },
      ) => {
        if (!a) return;
        // addVwap pushes mid first, then the ±1σ pair, then the ±2σ pair.
        const order = [h.middle, h.band1, h.band1, h.band2, h.band2];
        a.series.forEach((ser, i) => ser.applyOptions({ color: order[i] ?? h.band2 }));
        a.band.setRgb(h.fill);
      };
      anchor(globex, hues.vwap.globex);
      anchor(ny, hues.vwap.ny);
      anchor(weekly, hues.vwap.weekly);
      anchor(avwap, hues.vwap.anchored);
      // addProfile draws VAH, VAL, then the POC.
      const prof = (list: ISeriesApi<"Line">[], pal: { edge: string; poc: string }) => {
        list.forEach((ser, i) => ser.applyOptions({ color: i < 2 ? pal.edge : pal.poc }));
      };
      prof(profileGlobexSeries, hues.profile.globex);
      prof(profileNySeries, hues.profile.ny);
      const emaHue: Record<string, string> = {
        ema9: hues.ema.fast,
        ema20: hues.ema.slow,
        ema50: hues.ema.trend50,
        ema200: hues.ema.trend200,
      };
      for (const e of emaSeries) e.series.applyOptions({ color: emaHue[e.key] });
      for (const l of ibSeries) l.applyOptions({ color: hues.ib.line });
      for (const l of ibExtSeries) l.applyOptions({ color: hues.ib.ext });
      // The layer re-cuts its own lines and wash, and redraws — its regime tint
      // rides on per-point colours, which only come back with the data.
      mvLayer.relight();
      dsvLayer.relight();
      paintRef.current?.();
    };

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      if (debugRaf) cancelAnimationFrame(debugRaf);
      chartApiRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      applyRef.current = null;
      extPrimRef.current = null;
      applyFillRef.current = null;
      applyTintRef.current = null;
      applyLaneRef.current = null;
      relightRef.current = null;
      mvLayerRef.current = null;
      mvDrawRef.current = null;
      dsvLayerRef.current = null;
      dsvDrawRef.current = null;
      cvdOscDrawRef.current = null;
      armApplyRef.current = null;
      rulerApplyRef.current = null;
      rulerClearRef.current = () => {};
      avwapApplyRef.current = null;
      avwapDrawRef.current = null;
      paintRef.current = null;
      profilesRef.current = null;
      shelvesRef.current = null;
      // Before `chart.remove()` below: taking series off a chart that no longer
      // exists throws from inside the library.
      studyLayer.destroy();
      studyLayerRef.current = null;
      host.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (tipRef.current) tipRef.current.style.display = "none";
      if (ref.current) ref.current.style.cursor = "";
      ro.disconnect();
      chart.remove();
    };
  }, [
    bars,
    vwapGlobex,
    vwapNy,
    vwapWeekly,
    profileGlobex,
    profileNy,
    profileWeekly,
    ctxTape,
    ema9,
    ema20,
    ema50,
    ema200,
    rsi,
    atrPoints,
    cvd,
    cvdDivergences,
    deltaByBar,
    markers,
    touches,
    vaSnaps,
    priceLines,
    levels,
    ibList,
    tradeRects,
    focusOnTrade,
    debugZoom,
    footprint,
    regimeStates,
    tickSize,
    pointValue,
    height,
  ]);

  // Recolour in place. Declared after the build effect so that on mount it runs
  // with the refs already set — and on a colour change it is the only effect
  // that runs at all, which is the whole point: the chart keeps its range.
  useEffect(() => {
    applyAppearance(chartApiRef.current, candleRef.current, appearance);
    recolorVolume(candleRef.current, volumeRef.current, appearance);
    // After applyAppearance, which sets the active ink the relight reads.
    relightRef.current?.();
  }, [appearance]);

  // A Modern VWAP knob turned re-derives everything — the anchors, the regime and
  // the triggers all move together, and there is no partial version of that. A
  // redraw, never a rebuild: the parameters are deliberately not a dependency of
  // the build effect, which would cost the user their zoom on every knob turn.
  useEffect(() => {
    mvDrawRef.current?.();
  }, [mvParams]);

  useEffect(() => {
    dsvDrawRef.current?.();
  }, [dsvParams]);

  // Re-frame on a new selected day without rebuilding: when the tape spans many
  // sessions and only the focus span changes (same bars), scroll to it in place
  // so the user's zoom into a neighbour isn't thrown away. Skipped under
  // `focusOnTrade`, which owns the viewport; the initial frame is set in the
  // build effect above, so this only fires on subsequent changes.
  useEffect(() => {
    if (focusOnTrade || !initialTimeRange) return;
    const chart = chartApiRef.current;
    if (!chart) return;
    chart.timeScale().setVisibleRange({
      from: initialTimeRange.from as Time,
      to: initialTimeRange.to as Time,
    });
  }, [initialTimeRange, focusOnTrade]);

  // The legend's swatches have to be the colours actually on the canvas, so they
  // come from the same ink the series were built (and relit) in rather than from
  // the dark palettes directly — otherwise a light chart would list its levels
  // in the hues of a chart it isn't.
  const legendInk = chartInk(appearance.surface);

  /** Prior sessions handed to the composite, whatever the rule then made of
   *  them — see `compDays` for what it kept. */
  const ctxDays = ctxTape?.globex.length ?? 0;
  /** Is there an RTH session on this tape to profile? */
  const sessionVpOn = nySpan.start >= 0;
  // The panels behind the two histogram layers' "…" — the same builder the
  // replay pages hand their whole knob map to, asked for the subset this chart
  // draws. The Modern VWAP rows keep building their own below, since those
  // parameters belong to the page rather than to this chart.
  const profileKnobPanels: IndicatorSettingsMap = buildChartKnobs({
    // A layer this chart doesn't draw: the builder wants the fields, and a knob
    // for a layer with no row is never reached.
    bigLots: 0,
    onBigLots: () => {},
    nodeProm: knobs.nodeProm,
    onNodeProm: (nodeProm) => patchKnobs({ nodeProm }),
    volumeShelf: {
      params: shelfParams,
      onChange: patchShelf,
      field: shelfField,
      onField: patchShelfField,
    },
    composite: {
      rule: knobs.composite,
      onRule: (composite) => patchKnobs({ composite }),
      span: knobs.compositeSpan,
      onSpan: (compositeSpan) => patchKnobs({ compositeSpan }),
      // The replay pages point at their context-day knob here. A journal chart
      // has none: the days arrive with the session, fixed at the rule's own cap,
      // so what the line has to say is why there aren't more of them.
      note:
        `Up to ${BALANCE_CAP} prior sessions arrive with the chart — the balance rule's own cap. ` +
        "The run ends early at a contract roll, or at the first session whose ticks were never bought.",
    },
  });

  // Whether this chart's profile can be read for flow at all: a footprint whose
  // levels ship a signed delta beside their size. Absent on a day the feed never
  // tagged an aggressor on (the server drops the third element rather than
  // shipping zeros), and on the estimated profile, which has no tape to tag.
  // The first bar that traded settles it — the footprint is one shape for the
  // whole payload.
  const profileHasDelta = !!footprint?.some((rows) => rows.length > 0 && rows[0].length > 2);

  const legendItems: LegendItem[] = [];
  // The row says which rings it is drawing, because the knob inside can now take
  // them away — a label reading "±1σ ±2σ" over a chart drawing one of them is the
  // legend lying about the layer it names.
  const anchorRow = (anchor: VwapFillAnchor, name: string, title: string) => ({
    label: `${name} ${vwapBandLabel(vwapBands[anchor])}`.trimEnd(),
    settings: {
      title,
      fields: vwapAnchorKnobs(
        { bands: vwapBands[anchor], fill: vwapFill[anchor], region: vwapRegion[anchor] },
        {
          bands: (c) => setBands(anchor, c),
          fill: (w) => setFill(anchor, w),
          region: (r) => setRegion(anchor, r),
        },
      ),
    },
  });
  // The coarser bar over this one — the chart's own bars regrouped, so the row
  // exists whenever there are bars at all. Dimmed when the period isn't above
  // the drawn timeframe, which is the one way it can be on and draw nothing.
  if (bars.length > 0)
    legendItems.push({
      key: "externalChart",
      label: `External chart · ${
        EXTERNAL_PERIOD_OPTIONS.find((o) => o.value === extParams.period)?.label ?? extParams.period
      }`,
      color: extParams.palette === "custom" ? extParams.bull : legendInk.externalChart.bull,
      dim: extBars.length === 0,
      settings: {
        title: "External chart",
        fields: externalChartKnobs(extParams, patchExternal, extBars.length > 0),
      },
    });
  if (vwapGlobex && vwapGlobex.length > 0)
    legendItems.push({
      key: "vwapGlobex",
      color: legendInk.vwap.globex.middle,
      ...anchorRow("globex", "VWAP · Globex", "Globex VWAP"),
    });
  if (vwapNy && vwapNy.length > 0)
    legendItems.push({
      key: "vwapNy",
      color: legendInk.vwap.ny.middle,
      ...anchorRow("ny", "VWAP · NY", "NY VWAP"),
    });
  if (vwapWeekly && vwapWeekly.length > 0)
    legendItems.push({
      key: "vwapWeekly",
      color: legendInk.vwap.weekly.middle,
      ...anchorRow("weekly", "VWAP · Weekly", "Weekly VWAP"),
    });
  if (avwapAnchor != null)
    legendItems.push({
      key: "vwapAnchored",
      // Says which arithmetic drew it. Normally this line accumulates the tape's
      // own prints, exactly as the session bands beside it do — but a payload
      // with no tape behind it (the timeframe-radio refetch, resampled from the
      // bar store) leaves the bars without moments and the line falls back to
      // hlc3. That is a *different statistic*, widest apart in the first bars
      // after the anchor, and one anchored VWAP silently meaning two things is
      // the bug this whole seam exists to prevent. So it is said out loud.
      label: `VWAP · Anchored ±1σ ±2σ${
        bars.length && !hasTickMoments(bars[bars.length - 1]) ? " · hlc3 (no tape)" : ""
      }`,
      color: legendInk.vwap.anchored.middle,
    });
  // Modern VWAP, offered only where the page holds its parameters. Two rows: the
  // line, and the triggers read off it. Each label quotes what its own row is
  // actually showing at the current settings — an anchor count is how hard the
  // swing rule is working, and a gate that is undefined half the session is a
  // fact about warm-up, not about the market. Nothing is computed while both
  // rows are off, so neither label quotes a number it doesn't have.
  if (mvParams && modernVwap) {
    const mvKnobs = modernVwapKnobs(mvParams, modernVwap.onChange);
    const mvLive = vis.modernVwap || vis.modernVwapSignals;
    legendItems.push({
      key: "modernVwap",
      label:
        `Modern VWAP · ${
          mvParams.anchor === "swing"
            ? `swing ${mvParams.pivot}`
            : mvParams.anchor === "poc"
              ? `${mvParams.pocSource === "weekly" ? "wk " : ""}${
                  mvParams.rearmMode === "pocMove" ? "naked " : ""
                }POC${mvParams.rearmTicks ? ` ${mvParams.rearmTicks}t` : ""} · ${mvRead.anchors}⚓`
              : mvParams.anchor
        } ±${mvParams.bands}σ` +
        (mvParams.adaptive ? " · KER-adaptive" : "") +
        (mvLive
          ? ` · ${Math.round(mvRead.trendPct)}% trending${
              mvRead.undefPct >= 1 ? `, ${Math.round(mvRead.undefPct)}% undefined` : ""
            }`
          : ""),
      color: legendInk.modernVwap.middle,
      settings: { title: "Modern VWAP", fields: mvKnobs.line },
    });
    legendItems.push({
      key: "modernVwapSignals",
      label:
        mvParams.signals === "none"
          ? "Modern VWAP signals · off"
          : "Modern VWAP signals · MR/TC" +
            (mvLive
              ? ` · ${mvRead.signals}${mvParams.signals === "gated" ? " through the gate" : " raw"}`
              : ""),
      color: legendInk.modernVwap.middle,
      settings: { title: "Modern VWAP signals", fields: mvKnobs.signals },
      // The row stays when its own knob switched it off — that knob is the only
      // way back on, and it lives behind this row's "…".
      dim: mvParams.signals === "none",
    });
  }
  // The Zeiierman line, on the same terms: offered only where the page holds its
  // parameters, and quoting the two numbers that are facts about the construct —
  // how often the structure flipped at this swing period, and what the volatility
  // adjustment has actually done to the half-life the knob asks for.
  if (dsvParams && dynamicSwingVwap) {
    const live = vis.dynamicSwingVwap;
    legendItems.push({
      key: "dynamicSwingVwap",
      label:
        `Dynamic Swing VWAP · swing ${dsvParams.swingPeriod}${
          // Named only when the swings really were hunted somewhere else: a grid
          // at or below this chart's own bar regroups nothing.
          dsvRead.anchored && live ? `@${dsvParams.anchorTf}` : ""
        } · ${
          dsvParams.weighting === "cumulative"
            ? // No half-life to quote, and the word is the point: the legs are
              // ordinary anchored VWAPs off the same swings.
              "cumulative"
            : `APT ${
                dsvParams.adaptApt && live && dsvRead.aptNow
                  ? `${dsvRead.aptNow.toFixed(dsvRead.aptNow < 10 ? 1 : 0)}b`
                  : `${dsvParams.apt}b`
              }${dsvParams.adaptApt ? ` ATR ${dsvParams.volBias}×` : ""}`
        }${
          dsvParams.bands
            ? ` ±${dsvParams.bands}σ${dsvParams.bandScope === "all" ? " all" : ""}`
            : ""
        }` +
        (live
          ? ` · ${dsvRead.pivots}⚑ · ${Math.round(dsvRead.bullPct)}% bull` +
            (dsvRead.dropped ? ` · oldest ${dsvRead.dropped} dropped` : "")
          : ""),
      color: `rgb(${legendInk.dynamicSwingVwap.bull})`,
      settings: {
        title: "Dynamic Swing VWAP",
        fields: dynamicSwingVwapKnobs(dsvParams, dynamicSwingVwap.onChange),
      },
    });
  }
  if (profileGlobex && profileGlobex.length > 0)
    legendItems.push({
      key: "developingProfileGlobex",
      label: "Developing VA · Globex VAH/POC/VAL",
      color: legendInk.profile.globex.edge,
    });
  if (profileNy && profileNy.length > 0)
    legendItems.push({
      key: "developingProfileNy",
      label: "Developing VA · NY VAH/POC/VAL",
      color: legendInk.profile.ny.edge,
    });
  if (profileWeekly && profileWeekly.length > 0)
    legendItems.push({
      key: "developingProfileWeekly",
      label: "Developing VA · Weekly VAH/POC/VAL",
      color: legendInk.profile.weekly.edge,
    });
  // One legend row per EMA so each hides/shows on its own (see emaSeries above).
  const emaLegend: { key: IndicatorKey; pts?: EmaPoint[]; label: string; color: string }[] = [
    { key: "ema9", pts: ema9, label: "EMA 9 · 1-minute", color: legendInk.ema.fast },
    { key: "ema20", pts: ema20, label: "EMA 20 · 1-minute", color: legendInk.ema.slow },
    { key: "ema50", pts: ema50, label: "EMA 50 · 1-minute", color: legendInk.ema.trend50 },
    { key: "ema200", pts: ema200, label: "EMA 200 · 1-minute", color: legendInk.ema.trend200 },
  ];
  for (const e of emaLegend)
    if (e.pts && e.pts.length > 0)
      legendItems.push({ key: e.key, label: e.label, color: e.color });
  if (atrPoints && atrPoints.length > 0)
    legendItems.push({ key: "atr", label: "ATR 14", color: palette.gold });
  if (cvd && cvd.length > 0)
    legendItems.push({ key: "cvd", label: "CVD · cumulative delta", color: palette.blue });
  // The windowed delta beside the cumulative one. The label quotes the window it
  // is actually running — a divergence count means nothing without the fractal
  // width that produced it — and the swatch is a row marker only: the histogram
  // itself is green above zero and red below.
  if (deltaByBar)
    legendItems.push({
      key: "cvdOsc",
      label:
        `CVD oscillator · ${cvdOscParams.mode === "ema" ? "EMA" : "periodic"} ${
          cvdOscParams.period
        } · fractal ${cvdOscParams.fractalN}` +
        (vis.cvdOsc && cvdOscRead.divs > 0
          ? ` · ${cvdOscRead.divs} divergence${cvdOscRead.divs === 1 ? "" : "s"} · last ${
              cvdOscStrengthLabel(cvdOscRead.strength)
            }`
          : ""),
      color: palette.orange,
      settings: { title: "CVD oscillator", fields: cvdOscKnobs(cvdOscParams, patchCvdOsc) },
    });
  if (rsi && rsi.length > 0)
    legendItems.push({ key: "rsi", label: "RSI 14", color: palette.violet });
  if (levels && levels.length > 0)
    legendItems.push({ key: "levels", label: "Session levels", color: palette.blue });
  if (ibList.length > 0) {
    legendItems.push({
      key: "initialBalance",
      label: "Initial Balance · first 60m H/L",
      color: legendInk.ib.line,
    });
    legendItems.push({
      key: "ibExtensions",
      label: "IB extensions · 1×/1.5×/2×",
      color: legendInk.ib.ext,
    });
  }
  if (touches && touches.length > 0)
    legendItems.push({ key: "touches", label: "Interactions · touches", color: palette.green });
  if (vaSnaps && vaSnaps.length > 0)
    legendItems.push({ key: "va_snaps", label: "Interactions · VA-snaps", color: palette.red });
  // The shelves. Only where the profile is exact: without a footprint the rows
  // come from spreading each bar's volume across its range, and size-per-visit is
  // then near-constant by construction — every band would be an artefact of the
  // estimator. `detectShelves` refuses to draw it; offering a row the layer
  // cannot honour would make that refusal look like a bug.
  if (bars.length > 0 && footprint != null && footprint.length === bars.length) {
    legendItems.push({
      key: "volumeShelf",
      label:
        shelfField === "flow"
          ? `Volume shelves · order flow over ${shelfParams.windowMin}m`
          : `Volume shelves · size per visit over ${shelfParams.windowMin}m`,
      color: palette.orange,
    });
    legendItems.push({
      key: "volumeShelfBoxes",
      label: `Shelf boxes · ≥${shelfParams.zMin}σ held ${shelfParams.minHoldMin}m`,
      color: palette.orange,
      dim: !vis.volumeShelf,
    });
  }
  if (bars.length > 0)
    legendItems.push({
      key: "volumeProfile",
      // Say which kind it is: on the sim's charts it's the real tape, on the
      // journal's it's reconstructed from bars, and that changes how much you
      // should trust the exact POC print. And say whether the delta lane is up,
      // since the row's own eye hides both lanes together.
      // Name the scale the lane is drawn at, not just that it is up: "delta" over
      // a lane measuring imbalance is the legend naming the wrong distribution,
      // and the three modes are different enough readings that which one you are
      // looking at cannot be left to memory.
      label: `Volume profile · POC/VA${profileDelta ? ` + delta:${deltaLaneLabel(deltaLane)}` : ""} (${footprint ? "tick" : "est."})`,
      color: palette.gold,
      settings: {
        title: "Volume profile",
        // A reconstructed profile has no aggressor tag anywhere in it — a bar's
        // one volume number cannot say who lifted — so the knob says so rather
        // than offering a lane that would draw empty. The same footprint is what
        // a verdict needs a time axis from, so it gates that knob too.
        fields: volumeProfileKnobs(profileDelta, setProfileTint, profileHasDelta, {
          value: deltaLane,
          onChange: setLaneKnobs,
          canClassify: profileHasDelta,
        }),
      },
    });
  // The session's own distribution, and the days behind it. Each label quotes
  // what its row is actually showing at the current settings — how many days a
  // rule kept, what prominence named the nodes — because that is a fact about
  // this chart now, and a number quoted without the threshold that produced it
  // means nothing.
  if (bars.length > 0 && sessionVpOn) {
    legendItems.push({
      key: "developingVpNy",
      label: footprint
        ? "Session VP · NY volume at price (tick)"
        : "Session VP · NY volume at price (est.)",
      color: "#c4b5fd",
    });
    legendItems.push({
      key: "developingVpNyNodes",
      label:
        knobs.nodeProm > 0
          ? `Session VP nodes · HVN/LVN at ${Math.round(knobs.nodeProm * 100)}% prominence`
          : "Session VP nodes · off",
      color: "#818cf8",
      // The row stays when its own knob switched it off — that knob is the only
      // way back on, and it lives behind this row's "…".
      dim: knobs.nodeProm === 0,
    });
  }
  if (ctxDays > 0) {
    legendItems.push({
      key: "compositeProfile",
      label:
        compDays > 0
          ? `Composite VP · ${compDays} prior session${compDays === 1 ? "" : "s"} · VAH/POC/VAL`
          : `Composite VP · off · ${ctxDays} prior day${ctxDays === 1 ? "" : "s"} loaded`,
      color: legendInk.composite.poc,
      dim: compDays === 0,
    });
    if (compDays > 0)
      legendItems.push({
        key: "compositeNodes",
        label:
          knobs.nodeProm > 0
            ? `Composite nodes · HVN/LVN at ${Math.round(knobs.nodeProm * 100)}% prominence`
            : "Composite nodes · off",
        color: legendInk.composite.hvn,
        dim: knobs.nodeProm === 0,
      });
  }
  if (bars.length > 0)
    legendItems.push({
      key: "volRuler",
      label: `Vol ruler · median bar range vs the ${VR_STOP_TICKS}t stop`,
      color: palette.gold,
    });

  // Hang the histogram layers' knobs on the rows they tune, in one pass at the
  // end — the same panels the replay chart's rows carry, from the same builder,
  // so a composite rule means the same thing on either chart.
  for (const it of legendItems) {
    const spec = profileKnobPanels[it.key];
    if (spec) it.settings = spec;
  }

  // The community studies, as legend rows — same three gestures as a layer
  // above plus an ×, because unlike a layer a study is an instance you added and
  // can take away. Built here rather than handed down: everything they need is
  // already in this component.
  const studyRows: StudyRow[] = studies.map((spec) => {
    const entry = findStudy(spec.key);
    const rep = studyReport.find((r) => r.id === spec.id);
    const edit = (next: StudySpec) =>
      changeStudies(studiesRef.current.map((sp) => (sp.id === spec.id ? next : sp)));
    // The panel's rows: the indicator's own inputs, then a colour per line it
    // draws. A study with neither — a pattern that only ever emits marks — gets
    // no "…" on its row rather than an empty panel.
    const fields = entry ? studyFields(entry, spec, edit) : [];
    return {
      id: spec.id,
      label: entry ? studyLabel(entry, spec.inputs) : spec.key,
      // The colour of the study's first line, so the swatch is the line you are
      // looking for — the user's override once they have set one, which is the
      // whole point of being able to set it. A study with no plots at all (the
      // pattern ones) falls back to the muted grey its marks are drawn in.
      color: (entry && studyColor(entry, spec)) || palette.muted,
      on: !spec.hidden,
      error: rep?.error ?? null,
      marks: rep?.plots === 0 ? rep.markers : 0,
      settings: entry && fields.length ? { title: entry.title, fields } : undefined,
      onToggle: () => edit({ ...spec, hidden: !spec.hidden }),
      onRemove: () => changeStudies(studiesRef.current.filter((sp) => sp.id !== spec.id)),
    };
  });

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <ChartTools armed={armed || rulerArmed || avwapArmed}>
        <ChartToolButton
          icon="📊"
          label={armed ? "Drag a range…" : "Fixed range VP"}
          on={armed}
          onClick={() => arm(!armed)}
          title={
            armed
              ? "Drag across the chart to profile that range (Esc to cancel)"
              : "Fixed-range volume profile — drag across a range to profile it. Drag its edges to resize, its body to move, Del to remove."
          }
        />
        <ChartToolButton
          icon="📏"
          label={rulerArmed ? "Measuring…" : "Measure"}
          on={rulerArmed}
          onClick={() => armRuler(!rulerArmed)}
          title={
            rulerArmed
              ? "Drag (or click, move, click) between two points to measure (Esc to cancel)"
              : "Ruler — measure between two points: points/ticks/%, $ per lot, bars and time. Click the chart or press Esc to dismiss."
          }
        />
        <ChartToolButton
          icon="⚓"
          label={avwapArmed ? "Click a bar…" : "Anchored VWAP"}
          on={avwapArmed}
          onClick={() => armAvwap(!avwapArmed)}
          title={
            avwapArmed
              ? "Click a bar to anchor the VWAP there (Esc to cancel)"
              : "Anchored VWAP — click any bar to draw a VWAP + ±1σ/±2σ bands from that point forward. σ is bar-derived (not tick-exact). Click again to re-anchor."
          }
        />
        {/* The community catalogue. On the Charts workspace this lives on the
            topbar, because there it aims at whichever pane has focus; here there
            is one chart and one rail, so it sits in it. Its app-layers half is
            switched off: this chart draws a session that has already happened, so
            every layer that has anything to draw already has a legend row, and a
            second list of the same switches is a second place for them to
            disagree. */}
        <StudyPicker
          layers={EMPTY_LAYERS}
          onLayer={() => {}}
          appLayers={EMPTY_APP_LAYERS}
          specs={studies}
          onSpecs={changeStudies}
        />
        {/* Below the hairline: the tools that take things away. They come and go
            with what is on the chart, so they live at the foot of the rail where
            appearing doesn't move anything above them. */}
        {(avwapAnchor != null || selected != null || ranges.length > 1) && <ChartToolSep />}
        {avwapAnchor != null && (
          <ChartToolButton
            icon={<span className="chart-tool-pair">⚓✕</span>}
            label="Clear VWAP"
            onClick={clearAvwap}
            title="Remove the anchored VWAP"
          />
        )}
        {selected != null && (
          <ChartToolButton
            icon="🗑"
            label="Delete"
            onClick={deleteSelected}
            title="Remove this profile (Del)"
          />
        )}
        {ranges.length > 1 && (
          <ChartToolButton
            icon="🧹"
            label="Clear all"
            onClick={clearRanges}
            title="Remove every fixed-range profile"
          />
        )}
      </ChartTools>
      <div ref={ref} style={{ width: "100%" }} />
      {debugZoom && (
        <div
          ref={debugRef}
          style={{
            position: "absolute",
            top: 8,
            // Clear of the tool rail, same gutter the legend uses.
            left: "calc(14px + var(--chart-rail, 36px))",
            zIndex: 10,
            pointerEvents: "none",
            background: palette.card,
            border: `1px solid ${palette.cardBorder}`,
            borderRadius: 6,
            padding: "4px 8px",
            font: "11px/1.4 ui-monospace, monospace",
            color: palette.text,
            whiteSpace: "nowrap",
          }}
        />
      )}
      <div
        ref={tipRef}
        style={{
          position: "absolute",
          display: "none",
          zIndex: 10,
          pointerEvents: "none",
          background: palette.card,
          border: `1px solid ${palette.cardBorder}`,
          borderRadius: 6,
          padding: "6px 10px",
          font: "12px/1.5 Inter, sans-serif",
          color: palette.text,
          whiteSpace: "nowrap",
          boxShadow: "0 4px 16px rgba(0,0,0,0.45)",
        }}
      />
      <IndicatorLegend
        items={legendItems}
        studies={studyRows}
        visibility={vis}
        onToggle={toggle}
        appearance={appearanceSettings(appearance, changeAppearance)}
      />
    </div>
  );
}
