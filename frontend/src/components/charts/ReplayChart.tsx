// Lean lightweight-charts wrapper for the Trade Simulator. Unlike the shared
// CandlestickChart (which paints a fully-precomputed session once via setData),
// this one is streaming-native: the page's playback loop feeds it `applyStep`
// each frame and it `.update()`s only the changed tail. Imperative by design —
// the ref handle is how the playback loop talks to it without a React render per
// frame.
//
// It carries the same layers and the same tools as a strategy chart, because the
// point of the replay is to practise the read you'd make there: the developing
// anchored VWAP bands (Globex, NY, and the seeded weekly) with the two
// developing value areas, the Initial Balance, a viewport volume profile, and
// the ⚓ / fixed-range-profile / ruler tools. Prior sessions can be drawn to the
// left as context — the same tape, so they profile like any other bar, but
// nothing develops over them. Two layers exist only here, and both are reading
// aids rather than signals: the multi-session composite over those context days
// (see lib/compositeProfile — frozen at the prior close by construction, since
// the engine develops nothing over them), and the tape-event bands the engine
// publishes (see TapeEvent). Everything is fed from the tape (see
// replayEngine), so a level here is the level the engine would have traded —
// nothing is reconstructed from bars. Two consequences worth knowing:
//
//   - the ⚓ VWAP's σ is tick-derived here, where CandlestickChart's is
//     bar-derived; this one matches the sim engine, that one matches the
//     journal's Databento bars;
//   - the IB *develops*. A strategy chart only draws a completed window; a
//     replay is a session in progress and watching the hour form is the point.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { chartInk, chartSurfaces, palette, type BandHue } from "../../theme";
import {
  applyAppearance,
  appearanceSettings,
  candleColors,
  recolorVolume,
  volumeColors,
} from "./chartAppearance";
import type {
  BandPt,
  Bar,
  BigTrade,
  IbBox,
  ProfilePt,
  Snapshot,
  StepResult,
  Tape,
  TapeEvent,
  EventTuning,
} from "../../lib/replayEngine";
import { LevelHist } from "../../lib/replayEngine";
import type { TapeRange } from "../../lib/volumeProfile";
import { clusterLevels, GAP_LOOKBACK_BARS, type ApproachRow } from "../../lib/levelApproach";
import {
  ARM_REACH_TICKS,
  hlineKey,
  levelKey,
  type ArmShape,
  type ArmableLevel,
  type LevelArm,
} from "../../lib/levelArm";
import { LevelApproach } from "./LevelApproach";
import { LegAmount, legEcho } from "./LegAmount";
import type { UsdBracket } from "../../lib/bracketUsd";
import type { VwapPoint } from "../../lib/chartTypes";
import {
  PresetRuler,
  hasPresetRead,
  samePresetRead,
  VR_STOP_TICKS,
  computeVolRuler,
  type VolRulerRead,
} from "../../lib/volRuler";
import type { ModernVwapData, ModernVwapParams } from "../../lib/modernVwap";
import type { DsvParams, DynamicSwingVwapData } from "../../lib/dynamicSwingVwap";
import {
  createDynamicSwingVwapLayer,
  type DsvSource,
  type DynamicSwingVwapLayer,
} from "./dynamicSwingVwapLayer";
import {
  MV_KEYS,
  MV_RING,
  createModernVwapLayer,
  type ModernVwapLayer,
  type MvKey,
  type MvSource,
} from "./modernVwapLayer";
import { StudyLayer } from "./StudyLayer";
import {
  catalogue,
  findStudy,
  loadCatalogue,
  studyColor,
  studyFields,
  studyLabel,
  type StudyReport,
  type StudySpec,
} from "../../lib/studies";
import { LAYER_NAME, type LayerState, type ReplayLayerKey } from "./chartLayers";
import {
  IndicatorLegend,
  type IndicatorKey,
  type StudyRow,
  type IndicatorSettingsMap,
  type LegendItem,
} from "./IndicatorLegend";
import { ChartToolButton, ChartToolSep, ChartTools } from "./ChartToolButton";
import {
  loadChartAppearance,
  loadCvdOsc,
  deltaLaneLabel,
  loadDeltaLane,
  loadDrawings,
  loadIndicatorVisibility,
  loadLevelPanelOpen,
  loadExternalChartParams,
  loadHtfTrendParams,
  loadRankedZonesParams,
  saveRankedZonesParams,
  loadProfileDelta,
  loadVwapBands,
  loadVwapFill,
  loadVwapFillRegion,
  saveChartAppearance,
  saveCvdOsc,
  saveDeltaLane,
  saveDrawings,
  saveIndicatorVisibility,
  saveLevelPanelOpen,
  saveExternalChartParams,
  saveHtfTrendParams,
  saveProfileDelta,
  saveVwapBands,
  saveVwapFill,
  saveVwapFillRegion,
  type ChartAppearance,
  type DeltaLaneKnobs,
  type IndicatorVisibility,
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
  computeCvdOsc,
  cvdOscHist,
  cvdOscStrengthLabel,
  type CvdOscDivergence,
  type CvdOscParams,
} from "../../lib/cvdOsc";
import {
  cvdOscKnobs,
  externalChartKnobs,
  htfTrendKnobs,
  rankedZonesKnobs,
  volumeProfileKnobs,
  vwapAnchorKnobs,
} from "./indicatorKnobs";
import { ExternalChartPrimitive } from "./ExternalChartPrimitive";
import { HtfTrendPrimitive } from "./HtfTrendPrimitive";
import { computeHtfTrend, framesLabel, type HtfTrendParams } from "../../lib/htfTrend";
import { RankedZonePrimitive } from "./RankedZonePrimitive";
import { EconEventPrimitive, type EconEvent } from "./EconEventPrimitive";
import { GexLevelsPrimitive, type GexSession, type GexStep } from "./GexLevelsPrimitive";
import {
  computeRankedZones,
  type RankedZonesData,
  type ZoneTape,
  type RankedZonesParams,
} from "../../lib/rankedZones";
import {
  EXTERNAL_PERIOD_OPTIONS,
  groupExternalBars,
  type ExternalBar,
  type ExternalChartParams,
} from "../../lib/externalChart";
import { CvdDivergencePrimitive } from "./CvdDivergencePrimitive";
import { playCue } from "../../lib/orderSound";
import { focusChart, hasChartFocus, mountChart, nextChartId, unmountChart } from "../../lib/chartFocus";
import { joinLink, publishCrosshair, publishRightEdge, setPaneLinked } from "../../lib/paneLink";
import type { ChartToolId, ChartToolState } from "../../lib/chartTools";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import { VolumeProfilePrimitive } from "./VolumeProfilePrimitive";
import { RangeProfilePrimitive } from "./RangeProfilePrimitive";
import { RulerPrimitive } from "./RulerPrimitive";
import { PositionPrimitive, type PosHit, type PositionData } from "./PositionPrimitive";
import {
  OrdersPrimitive,
  type OrderHit,
  type WorkingOrderView,
} from "./OrdersPrimitive";
import { TradesPrimitive, type TradeMarkView } from "./TradesPrimitive";
import { BigTradePrimitive } from "./BigTradePrimitive";
import { CompositeProfilePrimitive } from "./CompositeProfilePrimitive";
import { DevelopingProfilePrimitive } from "./DevelopingProfilePrimitive";
import { EventBandPrimitive, type EventStyle } from "./EventBandPrimitive";
import { DEFAULT_BIG_LOTS, SIDE_BUY, SIDE_SELL } from "../../lib/replayEngine";
import {
  LiveTapeProfile,
  computeTapeProfile,
  profileNodes,
  type ProfileNodes,
  type VolumeProfile,
} from "../../lib/volumeProfile";
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
  type ShelfParams,
} from "../../lib/volumeShelf";
import { loadShelfParams, loadShelfField } from "../../lib/chartPrefs";
import {
  loadEconEventsParams,
  saveEconEventsParams,
  type EconImpactFloor,
  GEX_EXPIRY_OPTIONS,
  GEX_WALL_OPTIONS,
  loadGexLevelsParams,
  saveGexLevelsParams,
  type GexExpiry,
  type GexLevelsParams,
} from "../../lib/chartPrefs";
import { apiGet } from "../../lib/api";
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
import {
  buildComposite,
  type Composite,
  type CompositeRule,
} from "../../lib/compositeProfile";
import { COARSE_POINTER, byPointer } from "../../lib/pointer";

// (Volume bar colours follow the candle scheme — see chartAppearance.volumeColors.)

/** How tall one row of the volume profile is, in points — one tick on NQ, the
 *  finest the tape can be read at, and the same grid `/charts` and the engine's
 *  own value area sit on. Every profile on this chart uses it: the viewport one
 *  and each fixed-range one. Wide windows still group above `MAX_LEVELS`
 *  (lib/volumeProfile), so this is a floor on the row, not a promise about it. */
const PROFILE_BIN = 0.25;

/** The open position as the page knows it. The chart fills in the rest of what
 *  the overlay needs (tick size, $/point, the mark price) from the tape and the
 *  playback it is already being fed. */
export interface PositionLine {
  side: "long" | "short";
  size: number;
  entry: number;
  stop: number | null;
  target: number | null;
  /** Bar time (epoch seconds) of the entry — where the risk/reward zones start. */
  entryTime: number;
  /** What this position's contract is worth, when that is **not** the contract
   *  the tape is on. Live routing can be pointed at the mini's micro while the
   *  chart stays on the mini (one login is one socket, so the tape cannot
   *  follow) — and the chips on this line are dollars, so a position held in
   *  MNQ priced at NQ's $20 a point reads ten times the money it is. Omitted
   *  everywhere else, including for the paper blotter, which trades the tape's
   *  own contract by construction. */
  pointValue?: number;
  tickSize?: number;
}

/** The bracket a chart-placed order is measured with — the page's ticket, shown
 *  inside the long-press menu so a whole order can be built without opening it.
 *
 *  Either leg may be pinned to a dollar figure instead of a tick distance
 *  (lib/bracketUsd). The **page resolves the pin before handing this down**, so
 *  `stopTicks` here is always the distance an order would actually carry — the
 *  menu below sets the pin and reads the resolution, and never does the
 *  arithmetic itself. */
export type TicketDraft = UsdBracket;

/** An order chosen outright rather than inferred from which side of the market
 *  was clicked: the long-press menu names the type and the side. */
export interface TypedOrder {
  price: number;
  type: "limit" | "stop";
  side: "long" | "short";
}

export interface ReplayChartHandle {
  /** Hand over the decoded session (or null on unload). Clears every hand-drawn
   *  tool — a new day is a new chart, and a profile dragged over yesterday means
   *  nothing today. `keepTools` is for the one swap that isn't a new day: the
   *  same session with more context days glued in front of it. */
  setTape(
    tape: Tape | null,
    opts?: {
      keepTools?: boolean;
      /** The context days' stretches on this tape, oldest first — what the
       *  composite is built over. Empty when no prior days are drawn. */
      contextRanges?: TapeRange[];
    },
  ): void;
  /** Re-cut the context days without re-handing the tape — the composite span
   *  changing (RTH, or Globex too) is the same days measured differently. A
   *  no-op when they are the spans already in hand. */
  setContextRanges(ranges: TapeRange[]): void;
  /** Repaint everything as of a clock. How the viewport is treated:
   *  - `true` (default) — snap to the tail at a fixed zoom. A load: nobody has
   *    chosen a zoom yet, so the replay opens on the last bars.
   *  - `"follow"` — keep the user's zoom and track the playhead. A seek: the
   *    clock moved, so the view goes with it, but at the bar spacing they set.
   *  - `false` — leave the view where it is. A re-anchor or a threshold change:
   *    nothing moved through time, so neither does the viewport. */
  setSnapshot(s: Snapshot, opts?: { reframe?: boolean | "follow" }): void;
  applyStep(r: StepResult): void;
  setPosition(p: PositionLine | null): void;
  /** The limit orders still working, with their levels resolved to prices. */
  setOrders(orders: WorkingOrderView[]): void;
  /** Every trade closed so far this replay — the full list as of the clock, so
   *  a rewind simply hands back the shorter one. */
  setTrades(trades: TradeMarkView[]): void;
  /** Drop any measurement on the chart. The page calls this when the bar grid
   *  changes under it: a ruler reads "n bars", and those bars are gone. */
  clearRuler(): void;
  /** Arm a hand-tool, or `null` to disarm whatever is armed.
   *
   *  This is the whole of the page-level tool rail's power over a pane — see
   *  lib/chartTools. Arming stays in here (so does the pointer, the mutual
   *  exclusion and every drawing); the rail is a remote control over it. */
  armTool(id: ChartToolId | null): void;
  /** Draw a layer on this pane, or stop drawing it. What the topbar catalogue's
   *  add and remove are for an app layer: visibility is per pane and lives in
   *  here, so the catalogue reaches the focused pane through its handle rather
   *  than the page holding a second copy of the state.
   *
   *  A no-op when the layer is already the way you asked for. */
  setLayer(key: IndicatorKey, on: boolean): void;
  /** The rail's "take it away" group, in the order the rail draws them: the
   *  anchored VWAP, whichever drawing is selected, and everything at once. */
  clearAvwap(): void;
  deleteSelected(): void;
  clearDrawings(): void;
}

interface Props {
  /** The ⚓ tool moved (a bar time in epoch seconds) or was cleared. The page
   *  owns the engine, so it relays this to `ReplayEngine.setAnchor` and hands
   *  back a fresh snapshot — the chart never touches the tape itself. */
  onAnchorChange?: (barTime: number | null) => void;
  /** A stop or target was dragged to a new price and released. Fires once, on
   *  release: the page's trade log records *when* a bracket moved, and a stamp
   *  per mouse-move would be noise. */
  onBracketChange?: (b: { stop: number | null; target: number | null }) => void;
  /** The ✕ on the position chip was clicked — close at market. */
  onFlatten?: () => void;
  /** A working order's resting price (or one of its bracket legs) was dragged
   *  and released. Same contract as `onBracketChange`: the chart has already
   *  drawn it where it landed and clamped it somewhere it couldn't fill on the
   *  spot, so the page only has to record *when* it moved. */
  onOrderMove?: (o: {
    id: number;
    price: number;
    stop: number | null;
    target: number | null;
  }) => void;
  /** The ✕ on a working order's chip was clicked. */
  onOrderCancel?: (id: number) => void;
  /** Space was held and a mouse button clicked at a price. Which button, not
   *  which order — what a click *means* is a trading decision and belongs to the
   *  page; the chart only knows where the pointer was and which side it came
   *  down on. */
  onPlaceOrder?: (price: number, button: "left" | "right") => void;
  /** An order built in the long-press menu, where the type and the side were
   *  named rather than derived. Distinct from `onPlaceOrder` on purpose: that
   *  one hands over a gesture for the page to interpret, this one hands over a
   *  decision the user already made. */
  onPlaceTyped?: (o: TypedOrder) => void;
  /** The page's order ticket, so the menu can show and edit it in place. */
  ticket?: TicketDraft;
  onTicketChange?: (t: TicketDraft) => void;
  /** The vol ruler as it stands, on every bar close — the ticket's risk sizer
   *  sets its stop from it. Fires whether or not the pane is drawn, and with
   *  null while there is nothing to read. */
  onVolRuler?: (r: VolRulerRead | null) => void;
  /** What a point is worth in the contract this page's orders are **routed** to,
   *  when that is not the contract the tape is on — the ticket prices its two
   *  bracket boxes in money, and the story is the same one `PositionLine`
   *  carries it for. Omitted everywhere the two agree. */
  pointValue?: number;
  /** The mark, for the menu's own use: which of the four order types a price can
   *  legally be is a question about where the market is. The overlays get theirs
   *  from the playback (see `mark`), which never re-renders — this one has to. */
  mark?: number;
  /** Whether there is anything to place an order into yet. False before the
   *  replay is ready, and then the gesture goes dead rather than swallowing
   *  clicks. An open position is *not* a reason to refuse: the position is
   *  netted, so a further order scales it, takes size off it, or flips it. */
  canPlaceOrders?: boolean;
  /** Strip the calendar off the time axis — the labels and the crosshair read as
   *  a wall clock and nothing else. For the page's blind replay: a session runs
   *  through midnight, so the axis would otherwise name the day at the boundary,
   *  and the crosshair names it wherever you point. */
  hideDates?: boolean;
  /** Name seconds on the time axis and in the crosshair. For bars shorter than a
   *  minute — a 30s bar, or a tick bar, which is sub-minute most of the time —
   *  where an hh:mm axis would label several bars identically. */
  secondsAxis?: boolean;
  /** The lot threshold the big-trade marks were derived at. The engine decides
   *  which sweeps exist; the chart needs the number too, to scale the bubbles
   *  from it and to say on the legend row what "big" currently means. */
  bigLots?: number;
  /** How the context days are grouped into one composite profile — "off" draws
   *  none. Unlike the layers above, this one is computed here rather than in the
   *  engine: it is a fact about the days *before* the replay, which the engine
   *  deliberately develops nothing over. */
  composite?: CompositeRule;
  /** Prominence floor for the composite's HVN/LVN nodes, as a share of its
   *  tallest hump. Zero leaves the node reader off. */
  nodeProm?: number;
  /** The volume shelves' window and thresholds. Absent on a page that offers no
   *  knob panel for them, in which case the stored (sticky-global) setting is
   *  used — the layer is still drawn, it just cannot be adjusted from there. */
  shelfParams?: ShelfParams;
  shelfField?: ShelfField;
  /** The tape-event layer, or absent for a chart that doesn't offer it at all —
   *  which is the layer's off switch in the sense the *page* means it, distinct
   *  from the per-row indicator toggles a reader flicks. The engine is always
   *  detecting; without this the events simply never reach the canvas.
   *
   *  It carries the tuning as well as the style because the legend rows quote the
   *  thresholds an event was selected at, and a row that said "≥150 lots" while
   *  the engine was clustering at 500 would be worse than a row that said
   *  nothing. */
  events?: EventOverlay;
  /** Modern VWAP's parameters. Present offers the layer; absent and the two rows
   *  never appear, the same way `events` gates the band layer. The chart owns
   *  none of it — the page holds the state and hands the knobs back through
   *  `indicatorSettings`. See lib/modernVwap for what the thing is. */
  modernVwap?: ModernVwapParams;
  /** The Zeiierman swing-flip VWAP's parameters, on exactly the same terms as
   *  Modern VWAP above: present offers the layer, absent and its row never
   *  appears. A different indicator, not a mode of that one — see
   *  lib/dynamicSwingVwap for the three places they disagree. */
  dynamicSwingVwap?: DsvParams;
  /** The knobs above, as the page offers them back to the user — hung off the
   *  "…" on the legend row each one belongs to. The chart doesn't own any of
   *  this state (it arrives as the props above and leaves through these
   *  callbacks); it only knows which row each knob goes on. */
  indicatorSettings?: IndicatorSettingsMap;
  /** Which session the hand-drawn tools belong to (`SYMBOL|date`). With it set,
   *  the fixed-range profiles, the ⚓ anchor and the price lines survive a
   *  reload: they are saved as they change and restored by the `setTape` that
   *  reopens the same session. Absent, nothing is kept — the ruler never is
   *  either way, since a measurement is a question, not a level. */
  drawingsKey?: string;
  /**
   * The chart surface has just been built and is ready to be given data.
   *
   * Fires on every build, not only the first: React can remount a component —
   * StrictMode does it to everything in development, and a pane appearing does
   * it for real — and each remount throws the old chart away and makes a new,
   * empty one. A page that pushed its tape in an effect of its own has no way to
   * know that happened, and hands the data to a chart that is about to be
   * destroyed; the pane then comes up blank and stays blank until something
   * unrelated re-pushes. So the chart says when it is ready instead of the page
   * guessing, and the page's push becomes idempotent.
   */
  onReady?: () => void;
  /** The community studies picked off the topbar (see lib/studies), drawn over
   *  *this* pane's bars: an overlay goes on the price, anything else gets a pane
   *  of its own below the chart's own extra panes.
   *
   *  The chart owns none of it. The page holds the list and hands the same one
   *  to every pane, so a split layout is four bucketings of the same studies —
   *  which is how you get an RSI on the 5m and the 1h at once. */
  studies?: StudySpec[];
  /** A study was hidden, re-tuned or removed on this pane's legend. The page owns
   *  the list (it persists it, and it is per pane), so the legend's edits leave
   *  through here the way a bucketing change leaves through `onTfChange`. */
  onStudiesChange?: (specs: StudySpec[]) => void;
  /** What this pane's layers are doing — every one of them, on or off, drawable
   *  or not yet. The topbar catalogue is driven from this: visibility lives in
   *  here (see `vis`), so the page is told rather than asked, the same way
   *  `onToolsChange` reports the hand tools. */
  onLayers?: (layers: LayerState[]) => void;
  /** Which pane's copy of the per-chart display preferences (indicator
   *  visibility, legend open) this chart reads and writes. Omitted on the
   *  primary, which keeps the shared keys every other chart in the app uses —
   *  a secondary pane passes something short and stable like `"b"`, since the
   *  key has to survive a reload and so cannot be a runtime instance id. */
  prefsPane?: string;
  /** Offer the near-price levels panel (see LevelApproach). Opt-in rather than
   *  derived from `prefsPane`: that is undefined on the primary pane *and* in
   *  the journal's day replayer and the Recall card, neither of which passes it
   *  — so "primary" is not a thing this chart can work out for itself. */
  levelPanel?: boolean;
  /** The standing arms (see lib/levelArm), owned by the *page*: firing one
   *  places an order, and orders are the page's — this chart says which levels
   *  exist and which are lit, and hands the toggle up.
   *
   *  The arm carries its own price, snapshotted at the toggle, so the page never
   *  has to ask this chart what a level is worth later. That is what keeps the
   *  seam one callback wide. */
  arms?: LevelArm[];
  /** Arm a level with a shape, or `null` to disarm it. The level is passed whole
   *  because its price at this instant is the price the arm keeps. */
  onArmToggle?: (level: ArmableLevel, shape: ArmShape | null) => void;
  /** Whether the first arm to fire cancels the rest of its kind, and the toggle.
   *  The page owns the flag because the page is what fires arms; the chart only
   *  draws the switch. */
  armRace?: boolean;
  onArmRace?: () => void;
  /** Whether this pane shares the crosshair and the right edge with the others
   *  (see lib/paneLink). Undefined means linked — a lone chart has nobody to
   *  link with, so the default costs nothing and the pages that never split
   *  don't have to say anything. */
  linked?: boolean;
  /** Offered, the pane wears a `⇄` badge that takes it in and out of the link.
   *  Omitted, no badge — which is the one-pane page, where there is nothing to
   *  link to and a control saying so would be noise. */
  onLinkedChange?: (v: boolean) => void;
  /** The contract this pane's orders would actually be *routed* to, when that
   *  is not the contract the tape is on (NQ tape, MNQ orders). Drawn as a badge,
   *  because "where would a click on this chart send" must never be a guess. */
  routedTo?: string;
  /** What to call this chart on its legend. The *page* decides — blind replay
   *  hands over a masked name on purpose — so the chart never reads it off the
   *  tape itself. */
  symbol?: string;
  /** The display zone the bars' wall clock is in ("New York", or an IANA name).
   *  Only the economic-events layer needs it: its releases are true UTC and have
   *  to be put on the same wall clock the tape was. Defaults to New York, which
   *  is what every page in the app plays the tape in. */
  tz?: string;
  /** The futures contract the tape is on (NQZ6), for layers that read a store
   *  by contract — today only the gamma levels, which anchor the options books
   *  on this contract's own close. Unlike `symbol` this is never masked; a page
   *  that must not reveal the tape (blind replay) leaves it out, and the layer
   *  says it is unavailable. */
  tapeContract?: string;
  /** Which bar this pane is drawing ("5m"), for the same line. The chart is
   *  handed its bars already bucketed and has no other way to know. */
  tfLabel?: string;
  /** Offered, that label becomes a picker for this pane's bucketing. The chart
   *  passes both straight to the legend and never reads either — re-bucketing is
   *  an engine re-derivation and belongs to the page. */
  tfOptions?: readonly { key: string; label: string }[];
  onTfChange?: (id: string) => void;
  /** What this pane's hand-tools are doing, whenever it changes — what a
   *  page-level rail lights up from. Offered, the chart takes its own in-canvas
   *  rail down: the two would be the same buttons twice, and only one of them
   *  can be the one that says what is armed. */
  onToolsChange?: (s: ChartToolState) => void;
  /** The pointer arrived on this pane, or pressed it. The keyboard election in
   *  lib/chartFocus already happens on exactly these events; this is the page's
   *  copy of the same fact, for the chrome that has to name which pane it acts
   *  on. */
  onFocus?: () => void;
}

/** Everything the chart needs to draw the tape events, as the page holds it: the
 *  thresholds they were detected at (for the legend rows), how loud the bands
 *  are, and whether they also draw down the profile gutters. */
export interface EventOverlay {
  tuning: EventTuning;
  style: EventStyle;
  /** Strength below which a published event isn't drawn, per kind (1 = every
   *  published event). A repaint-only filter over what the engine found — it
   *  joins the per-kind toggles in `pushEvents`, the one place the event filter
   *  lives, so the bands, the marginals and the legend counts all agree. */
  floorSweep: number;
  floorAbsorb: number;
  /** The marginal over the volume profiles — the same events read against price
   *  instead of against time. Its own switch because it answers its own
   *  question, and because a gutter can only hold so much. */
  marginal: boolean;
}

/** The "no studies picked" list, as one array rather than a fresh `[]` per
 *  render — the layer rebuilds on identity, and a new empty array every render
 *  would be a rebuild every render. */
const EMPTY_STUDIES: StudySpec[] = [];

type BandKey = "mid" | "u1" | "l1" | "u2" | "l2";
const BAND_KEYS: BandKey[] = ["mid", "u1", "l1", "u2", "l2"];
/** Which ring each σ line belongs to, for the per-anchor band knob. The mid is
 *  not on that knob — it is the anchored VWAP — so it isn't in here either, and
 *  the two places that read this ask about the mid first. */
const RING_OF: Record<Exclude<BandKey, "mid">, 1 | 2> = { u1: 1, l1: 1, u2: 2, l2: 2 };
/** What the ⚓ anchor draws: everything. It is not one of the three fixed anchors
 *  the knob governs (chartPrefs.VwapFillAnchor). */
const ALL_BANDS = { s1: true, s2: true, fill: true };
type ProfKey = "vah" | "val" | "poc";
const PROF_KEYS: ProfKey[] = ["vah", "val", "poc"];
/** How a σ line names itself at the pane edge. The mid contributes nothing — the
 *  anchor's own name is the whole label there. */
const SIGMA_LABEL: Record<MvKey, string> = {
  mid: "",
  u1: " +1σ",
  l1: " −1σ",
  u2: " +2σ",
  l2: " −2σ",
  u3: " +3σ",
  l3: " −3σ",
};

/** What the gamma layer's expiry filter is called on its knob and legend row. */
const GEX_EXPIRY_LABEL: Record<GexExpiry, string> = {
  all: "all expiries",
  week: "≤7 days",
  "0dte": "0DTE",
};

/** Which construct a level came from. Carried so the approach panel can collapse
 *  a stack into one row and still say what is in it — three names for one price
 *  is a stack of one, and a confluence count that does not know that is a
 *  confidence trick (the 9/20 EMA measured ρ > 0.7 against the +1σ band). */
type LevelFamily = "vwap" | "devVa" | "modernVwap" | "dsv" | "ib" | "composite" | "hline";

/** One price level this chart is currently drawing. */
interface EnumeratedLevel {
  family: LevelFamily;
  label: string;
  /** Stable identity for arming (lib/levelArm). Family+label for a drawn layer;
   *  a hand-drawn line keys on its id instead, since every one of them is
   *  labelled "your line" and a label key would collapse them into one. */
  key: string;
  price: number;
  /** Whether the price scale fits to it. The edge markers are exactly the levels
   *  where this is false; nothing else reads it. */
  fitted: boolean;
  /** The level's own value at each bar time asked for, `NaN` where it had none —
   *  which is the right answer for a level younger than the window, and the one
   *  `gapCloser` turns into "unknown". Empty when no window was asked for. */
  path: number[];
}

/** Sample a time-keyed series onto `times`, `NaN` where it has no entry there.
 *
 *  Sampled rather than sliced: see `enumerateLevels`. `pts` and `times` are both
 *  ascending, so this is a merge walk from a binary-searched start — the series
 *  can be a whole session long and the window is six bars at its end. */
function sampleAt<T extends { time: number }>(
  pts: readonly T[],
  times: readonly number[],
  pick: (p: T) => number,
): number[] {
  const out = new Array<number>(times.length).fill(NaN);
  if (!pts.length || !times.length) return out;
  let lo = 0;
  let hi = pts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].time < times[0]) lo = mid + 1;
    else hi = mid;
  }
  for (let i = 0; i < times.length; i++) {
    while (lo < pts.length && pts[lo].time < times[i]) lo++;
    if (lo < pts.length && pts[lo].time === times[i]) out[i] = pick(pts[lo]);
  }
  return out;
}

/** A layer that is drawn but deliberately not *fitted* (see `mkBand`), at a price
 *  outside the pane. Demoting a series from the price scale's fit has exactly one
 *  real cost — off screen and non-existent look identical — and this is the thing
 *  that pays it: the edge markers say which way the level went and how far. */
interface EdgeLevel {
  label: string;
  price: number;
  /** Points between the level and the last trade, always positive: the chevron
   *  carries the direction. */
  dist: number;
}

/** The bar a time sits on (or the nearest one). Bars are strictly ascending, so
 *  it is a plain binary search — used to hold a viewport across a `setData` that
 *  changed how many bars come *before* the ones already on screen. */
const idxOfTime = (bars: Bar[], time: number): number => {
  let lo = 0;
  let hi = bars.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
};

/** How many of each kind of event clear the floor — the legend rows say so, and
 *  a layer with nothing on the chart doesn't get a toggle. */
const countEvents = (events: TapeEvent[]): { sweep: number; absorb: number } => {
  let sweep = 0;
  let absorb = 0;
  for (const e of events) {
    if (e.kind === "sweep") sweep++;
    else absorb++;
  }
  return { sweep, absorb };
};

/** A (fractional, possibly out-of-range) logical index, clamped onto a real bar. */
const clampIdx = (bars: Bar[], at: number): number =>
  bars.length ? Math.max(0, Math.min(bars.length - 1, Math.round(at))) : -1;

/** One fixed-range profile the user has drawn, bounded by bar times.
 *
 *  `live` is what a right edge dropped on the last bar means: keep this edge on
 *  the live edge. `to` is still the truth every other reader uses (hit-testing,
 *  drawing, persistence) — `paint` re-resolves it from the bar array first, so a
 *  latched range is correct after new bars print *and* after a rewind takes them
 *  away again, without a second notion of where its right edge is. */
interface RangeSel {
  id: number;
  from: number;
  to: number;
  live: boolean;
}
/** One hand-drawn horizontal price line. Every line is also an alert: `armed`
 *  goes false the moment the tape crosses the price (one chime, and the line
 *  dims rather than disappears — the level is still the level). Dragging it
 *  re-arms it, which is also the honest reading of the gesture: a moved line is
 *  a level the tape hasn't answered yet. */
interface HLine {
  id: number;
  price: number;
  armed: boolean;
}
/** The lines' hue. The violet the developing-NY histogram draws in — a reading
 *  aid's colour, deliberately not the gold/blue the profile levels own. */
const HLINE_COLOR = "#c4b5fd";
const HLINE_DIM = "rgba(196, 181, 253, 0.45)";
/** What a press grabbed: a fresh drag, an edge to resize, or the body to move. */
type DragMode = "new" | "left" | "right" | "move";
/** How close (px) the pointer must be to an edge to grab it rather than the body.
 *  Wider for a fingertip — see lib/pointer. */
const HANDLE_PX = byPointer(6, 20);
/** How far a press has to travel before it counts as a drag rather than a click.
 *  A finger never holds still, so the mouse's 5px would turn most taps into
 *  zero-width drags. */
const DRAG_SLOP = byPointer(5, 12);
/** How long a press has to stay put before it means "put the order ＋ here"
 *  rather than "pan". Long enough not to fire on a flick, short enough that you
 *  don't wonder whether the chart heard you. */
const LONG_PRESS_MS = 420;
/** Room the anchor keeps from the top and bottom edges, so what hangs off it
 *  stays on screen: just the ＋, or the whole menu. */
const ANCHOR_MARGIN = byPointer(16, 20);
const MENU_MARGIN = byPointer(108, 132);
/** Where the ticket goes when it opens. On a mouse it hangs off the ＋, centred
 *  on the price — a small panel on a big screen, next to the level it is for.
 *  On a fingertip it docks to the foot of the chart instead: the same panel on a
 *  phone is a slab sitting on the one price you summoned it to read, and the
 *  hand that opened it is over the rest. The ＋ stays on the axis either way, so
 *  which level the ticket belongs to is still on screen. */
const DOCK_MENU = COARSE_POINTER;

/** The frame the chart chooses for itself, in bars: the tail of the tape plus a
 *  little room past the newest bar to place an order into. Counted in bar
 *  indices rather than in minutes so it means the same thing on every timeframe
 *  — a time-based range would be 90 minutes of 30s bars or 90 hours of hourly
 *  ones. Used twice: when a session opens, and when the ◎ button puts the view
 *  back on the price (see `frameTail`). */
const FRAME_BARS = 90;
const FRAME_ROOM = 12;
/** How little of the price scale the bars in view can own before the chart says
 *  so (the ◎ lights up). A quarter of the pane: below that the candles are a
 *  ribbon and the day cannot be read off them, which on a chart that carries a
 *  weekly VWAP and a multi-session composite is a thing autoscale does on its
 *  own, without anyone touching the scale. */
const RIBBON = 0.25;
/** How often the developing overlays — the four VWAP bands and the three
 *  developing value areas, 29 line series between them — may be redrawn while
 *  the replay plays, in ms.
 *
 *  The same rule, for the same reason, as `PANE_DRAW_MS` in pages/Simulator.tsx:
 *  bar close *or* this, whichever comes first. Bar close alone would freeze the
 *  developing lines for a whole bar, which reads as a broken layer rather than
 *  a cheap one; 200ms redraws them ~5 times a second, and a σ band that moves
 *  five times a second is not a band you can see stepping.
 *
 *  Why they need a gate at all, when the candle does not: lightweight-charts
 *  rebuilds a series' *entire* raw point array on the next paint after any
 *  `update()` (`_fillRawPoints` — it maps over every row, not the visible
 *  range). So a frame costs O(bars x updated series), and 29 of the ~32 series
 *  on this chart are these. Measured: hiding them takes a 15s replay from 10 to
 *  33fps, because a seconds bucketing holds several thousand bars where a 500t
 *  one holds a few hundred. */
const OVERLAY_DRAW_MS = 200;
/** Overwrite-or-append one point at the tail of a time-keyed series.
 *
 *  Every developing layer prints once per bar and keeps re-printing that bar's
 *  entry while it forms, so a tail always either restates the last point or adds
 *  a new one — never lands in the middle. `ReplayEngine.put` is the same rule on
 *  the engine's side of the wire; this is the chart's, shared by the band
 *  primitives' point arrays and by the buffer the draw gate fills. */
function putTail<T extends { time: number }>(into: T[], pt: T): void {
  if (into.length && into[into.length - 1].time === pt.time) into[into.length - 1] = pt;
  else into.push(pt);
}

/** How many bars of a developing level's own path are kept, for the approach
 *  classification to look back over.
 *
 *  Two more than the lookback, not one. The classification spans
 *  `GAP_LOOKBACK_BARS` *gaps* and so touches that many bars plus the one it
 *  stands on — and it stands on the last *closed* bar, while this buffer's
 *  newest entry is always the forming one. Keep only lookback+1 and the window's
 *  oldest bar has already fallen off the front, which reads as a level younger
 *  than the window and classifies every row `unknown`. */
const VA_PATH_BARS = GAP_LOOKBACK_BARS + 2;

/** Fold a developing profile's tail into the bounded window above. */
function keepVaPath(into: ProfilePt[], tail: readonly ProfilePt[]): void {
  for (const p of tail) putTail(into, p);
  if (into.length > VA_PATH_BARS) into.splice(0, into.length - VA_PATH_BARS);
}
/** Put the viewport on the tail of a `last`-indexed bar array. */
function frameTail(chart: IChartApi, last: number) {
  chart
    .timeScale()
    .setVisibleLogicalRange({ from: Math.max(0, last - FRAME_BARS), to: last + FRAME_ROOM });
}
/** What the tape covers across a slice of bars, inclusive — null if the slice is
 *  empty. The tape's own extent, not the price scale's: the difference between
 *  the two is what the ◎ is about. */
function barRange(bars: Bar[], from: number, to: number): { hi: number; lo: number } | null {
  let hi = -Infinity;
  let lo = Infinity;
  for (let i = Math.max(0, from); i <= Math.min(bars.length - 1, to); i++) {
    if (bars[i].high > hi) hi = bars[i].high;
    if (bars[i].low < lo) lo = bars[i].low;
  }
  return hi > -Infinity ? { hi, lo } : null;
}
/** Both scales onto the tail of the tape: the frame above, and the price scale
 *  set by hand to the bars that frame contains. This is what the ◎ does, and it
 *  is also how every session opens — landing on a chart you have to press a
 *  button to be able to read is landing on the wrong chart.
 *
 *  The price half is deliberately not "turn autoscale on". Autoscale holds every
 *  series on the scale at once, so a weekly VWAP anchored 800 points under the
 *  session, or a composite level off a quiet week, crushes the day into a ribbon
 *  at the top of the pane — autoscale is usually what lost the price in the
 *  first place. Fitting the bars we just framed is a different question from
 *  fitting the chart, and it is the one that means "show me where price is".
 *
 *  The scale is manual from here (double-click the axis for autoscale back),
 *  which is why the ◎ keeps watching: when the tape walks out of the window this
 *  set, it lights up again. */
function frameOnPrice(chart: IChartApi, bars: Bar[], last: number, tick: number) {
  frameTail(chart, last);
  const br = barRange(bars, last - FRAME_BARS, last);
  if (!br) return;
  // Room above and below so the newest bar isn't against an edge — and a tick
  // floor, because a range that opens dead flat would otherwise zoom to a line.
  const pad = Math.max((br.hi - br.lo) * 0.12, tick * 8);
  chart.priceScale("right").setVisibleRange({ from: br.lo - pad, to: br.hi + pad });
}

/** The four resting orders, and which side of the mark each one may sit on. A
 *  bid rests under the market and an offer over it; a stop is the other way
 *  round, because it is the order you have to be run through to fill. */
const ORDER_KINDS: {
  type: "limit" | "stop";
  side: "long" | "short";
  label: string;
  above: boolean;
}[] = [
  { type: "limit", side: "long", label: "Buy Limit", above: false },
  { type: "limit", side: "short", label: "Sell Limit", above: true },
  { type: "stop", side: "long", label: "Buy Stop", above: true },
  { type: "stop", side: "short", label: "Sell Stop", above: false },
];

/** The long-press ticket. Everything an order needs — which of the four it is,
 *  how big, and how far its stop and target sit from the fill — in one panel
 *  hanging off the price it was summoned at, so a trade can be built without
 *  the pointer ever leaving the chart. */
function OrderMenu({
  price,
  mark,
  tick,
  tickUsd,
  ticket,
  docked,
  onTicket,
  onPlace,
  onNudge,
  onClose,
}: {
  price: number;
  mark: number;
  tick: number;
  /** What one tick of one contract is worth, so the two bracket boxes can say
   *  what they cost as well as how far they are. 0 when nothing has told us —
   *  then they say ticks and nothing else, rather than a guessed multiplier. */
  tickUsd: number;
  ticket: TicketDraft;
  /** Docked at the foot of the chart rather than hung off the ＋ — the same
   *  controls, laid out wide and shallow because that is the shape of the strip
   *  it now has to fit in. */
  docked: boolean;
  onTicket: (t: TicketDraft) => void;
  onPlace: (o: TypedOrder) => void;
  onNudge: (d: number) => void;
  onClose: () => void;
}) {
  const known = Number.isFinite(mark);
  const sizeField = (
    <label className="replay-omenu-f" title="Contracts">
      <span className="replay-omenu-l">Size</span>
      <input
        type="number"
        min={1}
        value={ticket.size}
        onChange={(e) => onTicket({ ...ticket, size: Math.max(1, Number(e.target.value)) })}
      />
    </label>
  );
  /** A bracket leg: the same box, in ticks or in dollars.
   *
   *  The figure in the caption is the *other* unit — what this many ticks costs,
   *  or what that money came out as once it was rounded onto the grid. It was
   *  read-only money before this box could take money; now it is the second half
   *  of a two-way conversion, and it is the half you check. */
  const leg = (
    key: "stop" | "target",
    label: string,
    title: string,
    color: string,
  ) => {
    const ticks = ticket[`${key}Ticks`];
    const pin = ticket[`${key}Usd`];
    const echo = legEcho(ticks, pin, tickUsd, Math.max(1, ticket.size));
    return (
      <label className="replay-omenu-f" title={title}>
        <span className="replay-omenu-l">
          {label}
          {echo ? <b style={{ color }}>{echo}</b> : null}
        </span>
        <LegAmount
          ticks={ticks}
          pin={pin}
          tickUsd={tickUsd}
          size={Math.max(1, ticket.size)}
          // Typing a distance says the distance is the setting — so the pin goes.
          onTicks={(t) => onTicket({ ...ticket, [`${key}Ticks`]: t, [`${key}Usd`]: null })}
          onPin={(usd) => onTicket({ ...ticket, [`${key}Usd`]: usd })}
        />
      </label>
    );
  };
  return (
    <div className={`replay-omenu${docked ? " docked" : ""}`} role="menu">
      <div className="replay-omenu-kinds">
        {ORDER_KINDS.map((k) => {
          // Greyed rather than hidden: which two are available is the thing this
          // menu is teaching, and a menu that reshuffles under the pointer as the
          // tape crosses the price would be unusable.
          const ok = known && (k.above ? price > mark : price < mark);
          return (
            <button
              key={k.label}
              type="button"
              role="menuitem"
              className={`replay-okind ${k.side}`}
              disabled={!ok}
              onClick={() => onPlace({ price, type: k.type, side: k.side })}
              title={
                ok
                  ? `${k.label} at ${price.toFixed(2)}`
                  : `A ${k.label.toLowerCase()} has to sit ${k.above ? "above" : "below"} the market`
              }
            >
              {k.label}
            </button>
          );
        })}
      </div>
      {/* The sizing and the price: stacked when the panel hangs off the ＋, side
          by side when it is docked. The wrapper is the only thing the wide
          layout needs to turn one into the other. */}
      <div className="replay-omenu-foot">
        <div className="replay-omenu-row">
          {sizeField}
          {leg(
            "stop",
            "SL",
            "Stop from the fill — optional, leave empty for none. The t/$ button sets whether you are choosing a distance or the money it risks; pinned to money, the distance follows the size and the contract.",
            palette.red,
          )}
          {leg(
            "target",
            "TP",
            "Target from the fill — optional, leave empty for none. The t/$ button sets whether you are choosing a distance or the money it makes; pinned to money, the distance follows the size and the contract.",
            palette.green,
          )}
        </div>
        <div className="replay-omenu-px">
          <button type="button" onClick={() => onNudge(-tick)} title={`Down one tick (${tick})`}>
            ▾
          </button>
          <span>{price.toFixed(2)}</span>
          <button type="button" onClick={() => onNudge(tick)} title={`Up one tick (${tick})`}>
            ▴
          </button>
          <button type="button" className="replay-omenu-x" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}

// One anchor's drawn state: the five σ lines, the shaded ±1σ→±2σ fill, and the
// points both are drawn from (kept here so the fill can be re-pointed each step).
interface Anchor {
  lines: Record<BandKey, ISeriesApi<"Line">>;
  band: VwapBandPrimitive;
  pts: VwapPoint[];
}

// The engine's compact band point → the shared VwapPoint the fill primitive and
// the journal charts speak.
const toVwapPoint = (p: BandPt): VwapPoint => ({
  time: p.time,
  middle: p.mid,
  upper1: p.u1,
  lower1: p.l1,
  upper2: p.u2,
  lower2: p.l2,
});

export const ReplayChart = forwardRef<ReplayChartHandle, Props>(function ReplayChart(
  {
    onAnchorChange,
    onBracketChange,
    onFlatten,
    onOrderMove,
    onOrderCancel,
    onPlaceOrder,
    onPlaceTyped,
    ticket,
    onTicketChange,
    onVolRuler,
    pointValue: pointValueProp,
    mark: markProp = NaN,
    canPlaceOrders = true,
    hideDates = false,
    secondsAxis = false,
    bigLots = DEFAULT_BIG_LOTS,
    composite = "off",
    nodeProm = 0,
    shelfParams,
    shelfField,
    events: eventOverlay,
    modernVwap: mvParams,
    dynamicSwingVwap: dsvParams,
    studies,
    onStudiesChange,
    onLayers,
    indicatorSettings,
    drawingsKey,
    prefsPane,
    levelPanel = false,
    arms,
    onArmToggle,
    armRace,
    onArmRace,
    linked = true,
    onLinkedChange,
    routedTo,
    symbol,
    tz = "New York",
    tapeContract,
    tfLabel,
    tfOptions,
    onTfChange,
    onToolsChange,
    onFocus,
    onReady,
  },
  ref,
) {
  const elRef = useRef<HTMLDivElement>(null);
  /** The crosshair readout's element — written imperatively, see the
   *  subscribeCrosshairMove block. */
  const ohlcRef = useRef<HTMLDivElement>(null);
  /** Which bar the readout is currently showing, or -1 for "the newest one".
   *  Read by the playback, which has to keep an idle readout current as the bar
   *  under it forms. */
  const hoverIdxRef = useRef(-1);
  /** The readout painter, published out of the build effect so `applyStep` can
   *  reach it — see `paintOhlc`. */
  const paintOhlcRef = useRef<((i: number) => void) | null>(null);
  // This pane's identity, for the keyboard election in lib/chartFocus. Assigned
  // once per mounted instance — `useRef(nextChartId())` would burn a fresh id on
  // every render, since the argument is evaluated whether or not it is used.
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  // Read from the pointer handlers inside the build effect, which is installed
  // once — so the page's latest callback has to be reachable through a ref.
  const onFocusRef = useRef(onFocus);
  onFocusRef.current = onFocus;

  const paneIdRef = useRef<number | null>(null);
  if (paneIdRef.current == null) paneIdRef.current = nextChartId();
  const paneId = paneIdRef.current;

  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const gRef = useRef<Anchor | null>(null);
  const nRef = useRef<Anchor | null>(null);
  const aRef = useRef<Anchor | null>(null);
  const wkRef = useRef<Anchor | null>(null);
  const gProfRef = useRef<Record<ProfKey, ISeriesApi<"Line">> | null>(null);
  const nProfRef = useRef<Record<ProfKey, ISeriesApi<"Line">> | null>(null);
  const wProfRef = useRef<Record<ProfKey, ISeriesApi<"Line">> | null>(null);

  // The developing overlays' draw gate — see OVERLAY_DRAW_MS.
  //
  // `pendingOverlay` is why the gate is honest rather than lossy: the engine's
  // tails are *deltas since the last advance()*, so a frame that skips the draw
  // has to keep that frame's points or they are gone. They accumulate here under
  // the same overwrite-or-append rule they would have been drawn with, and the
  // next drawing frame applies the whole run at once — which is one `update()`
  // per changed bar, exactly what an ungated frame would have done, minus the
  // repeated restatements of the bar still forming.
  const lastOverlayDrawRef = useRef(0);
  const pendingOverlayRef = useRef<{ band: BandPt[][]; prof: ProfilePt[][] }>({
    band: [[], [], [], []],
    prof: [[], [], []],
  });
  const clearPendingOverlay = () => {
    pendingOverlayRef.current = { band: [[], [], [], []], prof: [[], [], []] };
    lastOverlayDrawRef.current = 0;
  };

  // What a point is worth on the overlays, which is the routed contract's money
  // when the page named one and the tape's otherwise (see `pointValue`). In a
  // ref because every one of them is fed from outside React — the handle, the
  // playback, a drag — and none of those closures re-bind when a prop changes.
  const pvRef = useRef<number | undefined>(pointValueProp);
  pvRef.current = pointValueProp;
  /** The routed contract's $/point, or the tape's. The single answer every chip
   *  on this canvas is priced with. */
  const chipPv = () => pvRef.current ?? tapeRef.current?.pointValue;
  /** The ruler, which reports in the same money and is built inside the chart
   *  effect — reachable from here only through this. */
  const rulerRef = useRef<RulerPrimitive | null>(null);

  // The open position overlay. The primitive is created here rather than in the
  // build effect because the imperative handle (which lives outside it) is what
  // feeds it; attaching to the series still happens in there.
  const posPrimRef = useRef<PositionPrimitive | null>(null);
  if (!posPrimRef.current) posPrimRef.current = new PositionPrimitive();
  const posRef = useRef<PositionData | null>(null);
  // The working orders, same arrangement: the page owns the log, the chart owns
  // what a drag is doing to a price until the pointer is released.
  const ordPrimRef = useRef<OrdersPrimitive | null>(null);
  if (!ordPrimRef.current) ordPrimRef.current = new OrdersPrimitive();
  const workingRef = useRef<WorkingOrderView[]>([]);
  // The closed trades, kept the same way — the page derives them from its log,
  // the chart only draws them.
  const tradesPrimRef = useRef<TradesPrimitive | null>(null);
  if (!tradesPrimRef.current) tradesPrimRef.current = new TradesPrimitive();
  const tradesRef = useRef<TradeMarkView[]>([]);
  const [tradeCount, setTradeCount] = useState(0);
  // The tape's own big trades. Same arrangement again — the engine derives them
  // from the tape, the chart only draws them — except the list is grown by the
  // playback tails rather than re-handed whole on every fill.
  const bigPrimRef = useRef<BigTradePrimitive | null>(null);
  if (!bigPrimRef.current) bigPrimRef.current = new BigTradePrimitive();
  const bigsRef = useRef<BigTrade[]>([]);
  const [bigCount, setBigCount] = useState(0);

  // The tape's events, kept the same way as the big trades — but merged on
  // (kind, idx) rather than by position, because either of the two open events
  // can grow while the other publishes past it.
  const evPrimRef = useRef<EventBandPrimitive | null>(null);
  if (!evPrimRef.current) evPrimRef.current = new EventBandPrimitive();
  const eventsRef = useRef<TapeEvent[]>([]);
  const evPosRef = useRef(new Map<string, number>());
  /** How many events of each kind are actually *drawn* — the list filtered by
   *  the strength floor, which is the number the legend has to quote. It moves
   *  when the list grows, when an open event grows past the floor, and when the
   *  floor itself changes, so it is recomputed rather than counted up. */
  const [evCount, setEvCount] = useState({ sweep: 0, absorb: 0 });

  // The composite over the context days. Frozen at the prior close by
  // construction — it is built from the history stretch, which the engine
  // develops nothing over — so it is rebuilt only when those days change, never
  // on a playback step.
  const compPrimRef = useRef<CompositeProfilePrimitive | null>(null);
  if (!compPrimRef.current) compPrimRef.current = new CompositeProfilePrimitive();
  /** The context days' stretches, as tick spans on the current tape — RTH, or
   *  Globex-and-RTH, as the page cut them. */
  const ctxRangeRef = useRef<TapeRange[]>([]);
  const compRef = useRef<Composite | null>(null);
  /** The viewport profile, which also carries the event marginal. Owned by the
   *  build effect; held here so the filtered list can reach it. */
  const vpRef = useRef<VolumeProfilePrimitive | null>(null);
  /** The fixed-range profiles, held here on the same terms: the row-colour knob
   *  is one setting over both histograms, so it has to reach this one too. */
  const rangePrimRef = useRef<RangeProfilePrimitive | null>(null);
  /** Nodes at the current prominence, cached: the reading changes only when the
   *  composite or the knob does, and both are rare. */
  const compNodesRef = useRef<ProfileNodes | null>(null);
  /** How many of the drawn bars are context — the span the histogram pins to. */
  const histCountRef = useRef(0);

  // The developing NY profile: the same distribution the NY VAH/POC/VAL lines
  // come from, drawn as a histogram in its own gutter. Recomputed off the tape
  // on each bar close rather than carried in the playback tails — a session's
  // histogram is thousands of rows, and shipping it per frame to redraw a gutter
  // would cost more than reading it back does.
  const devPrimRef = useRef<DevelopingProfilePrimitive | null>(null);
  if (!devPrimRef.current) devPrimRef.current = new DevelopingProfilePrimitive();
  /** Bar time of the session's first NY bar — the profile's left edge. NaN
   *  before the bell, and again after a rewind past it. */
  const nyStartRef = useRef(NaN);
  /** The NY value area as of the clock, straight off the engine — what the
   *  histogram shades by, so it and the VAH/POC/VAL lines can't disagree. */
  const nyVaRef = useRef<ProfilePt | null>(null);
  /** Sessions in the composite as drawn, 0 when there is none. The one thing
   *  about it React needs: under the balance rule the count is a *reading* (this
   *  is how long the auction has been running), so the legend says it. */
  const [compDays, setCompDays] = useState(0);
  /** Context days handed in, whatever the composite rule then made of them. The
   *  composite's row is drawn off *this* rather than off `compDays`, because the
   *  rule itself now lives on that row: gating the row on a composite being
   *  drawn would take the "off" switch away with the thing it switched off. */
  const [ctxDays, setCtxDays] = useState(0);
  // Mirrored for the build effect and the imperative handle, neither of which
  // re-runs when the prop changes.
  const bigLotsRef = useRef(bigLots);
  bigLotsRef.current = bigLots;
  const compositeRef = useRef(composite);
  compositeRef.current = composite;
  // Sticky-global, read at mount (lib/chartPrefs). The replay pages do not offer
  // the shelf knob panel yet — the journal charts do, and the setting is shared —
  // so there is nothing here for it to change under.
  const shelfParamsRef = useRef(shelfParams ?? loadShelfParams());
  shelfParamsRef.current = shelfParams ?? shelfParamsRef.current;
  const shelfFieldRef = useRef<ShelfField>(shelfField ?? loadShelfField());
  shelfFieldRef.current = shelfField ?? shelfFieldRef.current;
  const nodePromRef = useRef(nodeProm);
  nodePromRef.current = nodeProm;
  const eventOvRef = useRef(eventOverlay);
  eventOvRef.current = eventOverlay;
  // Mark price, mirrored off the playback so the position chip's open P&L moves
  // with the tape without a React render per frame.
  const lastPriceRef = useRef<number>(NaN);

  // The bars as drawn, mirrored from the snapshots/tails so the tools can hit-test
  // and index into the tape. Bar objects are the engine's own (a forming bar keeps
  // mutating), which is exactly what the profile wants — its `i1` stays current.
  const barsRef = useRef<Bar[]>([]);
  const tapeRef = useRef<Tape | null>(null);
  const ibRef = useRef<IbBox | null>(null);

  // --- back to the price ------------------------------------------------------
  // Roll back through the morning and the live edge ends up off screen to the
  // right; roll too far the other way and it is off to the left. The scale can
  // lose it too, and more quietly: autoscale has to hold every layer at once, so
  // a weekly VWAP or a composite level far under the session leaves the whole
  // day as a ribbon at the top of the pane, and dragging the axis by hand can
  // put the tape anywhere. Getting back is a scroll in a direction you have to
  // guess at a distance nobody knows — so this is the one press that always ends
  // with the session's price in the middle of the chart.
  //
  // The root element carries the chart's own price-axis width as a CSS var: the
  // button sits at the top right *of the tape*, and the axis under it is 50-70px
  // depending on how many digits the scale is printing (see the size-change
  // subscription in the build effect).
  const rootRef = useRef<HTMLDivElement | null>(null);
  // Whether the tape has got away — the button lights up exactly when pressing
  // it would change something.
  const [offTape, setOffTape] = useState(false);
  // The edge markers are recomputed on exactly the occasions the ◎ is: a pan, a
  // seek, a bar close. Through a ref because `syncEdges` is declared further down
  // — next to the layers it reads — and this is called from three places that all
  // already go through here.
  const syncEdgesRef = useRef<() => void>(() => {});
  const syncOffTape = () => {
    syncEdgesRef.current();
    const chart = chartRef.current;
    const last = barsRef.current.length - 1;
    if (!chart || last < 0) {
      setOffTape(false);
      return;
    }
    const lr = chart.timeScale().getVisibleLogicalRange();
    // Off to the right (panned back in time) or off to the left (scrolled past
    // the end into the empty room).
    if (lr != null && (lr.to < last || lr.from > last)) {
      setOffTape(true);
      return;
    }
    const pr = chart.priceScale("right").getVisibleRange();
    if (pr == null || !(pr.to > pr.from)) {
      setOffTape(false);
      return;
    }
    // Off the top or the bottom of a scale someone set by hand, or one the tape
    // has since walked out of.
    const p = lastPriceRef.current;
    if (Number.isFinite(p) && (p < pr.from || p > pr.to)) {
      setOffTape(true);
      return;
    }
    // On screen but unreadable: the bars in view own so little of the pane that
    // the session is a ribbon. That is autoscale holding the scale open for a
    // layer far under the tape, and it is the case the button was asked for.
    const br = barRange(barsRef.current, lr ? Math.round(lr.from) : 0, lr ? Math.round(lr.to) : last);
    setOffTape(br != null && (br.hi - br.lo) / (pr.to - pr.from) < RIBBON);
  };
  const jumpToPrice = () => {
    const chart = chartRef.current;
    const bars = barsRef.current;
    const last = bars.length - 1;
    if (!chart || last < 0) return;
    frameOnPrice(chart, bars, last, tapeRef.current?.tickSize ?? 0.25);
    // The button keeps watching what it just set: when the tape walks out of
    // that window, it lights up again.
    syncOffTape();
  };

  const onAnchorRef = useRef(onAnchorChange);
  onAnchorRef.current = onAnchorChange;
  const onBracketRef = useRef(onBracketChange);
  onBracketRef.current = onBracketChange;
  const onFlattenRef = useRef(onFlatten);
  onFlattenRef.current = onFlatten;
  const onOrderMoveRef = useRef(onOrderMove);
  onOrderMoveRef.current = onOrderMove;
  const onOrderCancelRef = useRef(onOrderCancel);
  onOrderCancelRef.current = onOrderCancel;
  const onPlaceOrderRef = useRef(onPlaceOrder);
  onPlaceOrderRef.current = onPlaceOrder;

  // --- the long-press order ＋ ------------------------------------------------
  // A press held still on the tape puts a ＋ on the price axis at that price;
  // tapping the ＋ opens the ticket. Two steps rather than one because the first
  // is a gesture you can make by accident and the second never is — and because
  // the ＋ sitting on the axis, at the price, is the confirmation that the chart
  // heard the level you meant before you commit to an order at it.
  //
  // The price is the state; the pixel it sits at is not. A replay pans and
  // rescales continuously under it, so where the ＋ is drawn is re-derived from
  // the price every frame (see the effect below) rather than frozen at the
  // coordinate the press landed on.
  const [plusPrice, setPlusPrice] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const plusRef = useRef<number | null>(null);
  plusRef.current = plusPrice;
  const menuOpenRef = useRef(false);
  menuOpenRef.current = menuOpen;
  const anchorRef = useRef<HTMLDivElement>(null);
  const openPlus = (price: number, menu = false) => {
    setPlusPrice(price);
    setMenuOpen(menu);
  };
  const closePlus = () => {
    setPlusPrice(null);
    setMenuOpen(false);
  };
  const openPlusRef = useRef(openPlus);
  openPlusRef.current = openPlus;
  const closePlusRef = useRef(closePlus);
  closePlusRef.current = closePlus;

  // Push whatever `posRef` now holds at the overlay. Called from the handle when
  // the position changes, from the playback when the mark moves, and on every
  // frame of a bracket drag.
  const pushPos = () => posPrimRef.current?.setData(posRef.current);
  const pushOrders = () =>
    ordPrimRef.current?.setOrders(workingRef.current, tapeRef.current?.tickSize, chipPv());
  const pushTrades = () =>
    tradesPrimRef.current?.setTrades(tradesRef.current, tapeRef.current?.tickSize);

  /** Resolve a position line against the contract it should be priced in, and
   *  draw it. The line the page handed over is kept as it was given (`posSrcRef`)
   *  so the resolution can be redone — the routed contract can change under a
   *  position that hasn't moved, and re-reading it off the merged object would
   *  lose which of the three answers the line itself had supplied. */
  const posSrcRef = useRef<PositionLine | null>(null);
  const applyPos = (p: PositionLine | null) => {
    posSrcRef.current = p;
    const tape = tapeRef.current;
    posRef.current = p
      ? {
          ...p,
          last: lastPriceRef.current,
          // The line's own contract wins over the page's routed one, which wins
          // over the tape's — see `PositionLine` and `pointValue`.
          tickSize: p.tickSize ?? tape?.tickSize ?? 0.25,
          pointValue: p.pointValue ?? chipPv() ?? 20,
        }
      : null;
    pushPos();
  };

  // The page pointed its orders at another contract (the Simulator's mini/micro
  // choice). Nothing on the chart moved and no tape changed — but every figure
  // in money on it did, so the three overlays that quote dollars are re-priced
  // where they stand rather than waiting for whatever would next have pushed
  // them. Deps are the prop alone: the three helpers are re-made every render
  // and read everything they need from refs, so listing them would re-run this
  // on every push instead of on the only thing that changes the answer.
  useEffect(() => {
    rulerRef.current?.setContract(tapeRef.current?.tickSize, chipPv());
    applyPos(posSrcRef.current);
    pushOrders();
  }, [pointValueProp]);

  // Hand the composite to its primitive, pinned to the context bars it was
  // measured over. Cheap — the profile is already built and the nodes are cached
  // until the prominence knob moves — so it can be called from anywhere the
  // drawn bars change.
  const paintComposite = () => {
    const c = compRef.current;
    const bars = barsRef.current;
    const n = histCountRef.current;
    if (!c || n <= 0 || bars.length < n) {
      compPrimRef.current?.setData(null);
      setCompDays(0);
      return;
    }
    setCompDays(c.days);
    const prom = nodePromRef.current;
    if (prom > 0 && !compNodesRef.current) compNodesRef.current = profileNodes(c.profile, prom);
    compPrimRef.current?.setData({
      profile: c.profile,
      nodes: prom > 0 ? compNodesRef.current : null,
      from: bars[0].time,
      to: bars[n - 1].time,
      days: c.days,
    });
  };

  // Rebuild it from the tape. The expensive half — a scan of every context day's
  // RTH ticks, plus one more per day the balance rule considers — and the rare
  // one: a new session, a change to how many days are drawn, or a change of
  // rule. Never a playback step; the composite is frozen at the prior close.
  const rebuildComposite = () => {
    const tape = tapeRef.current;
    setCtxDays(ctxRangeRef.current.length);
    compRef.current =
      tape && ctxRangeRef.current.length
        ? buildComposite(
            tape.level,
            tape.size,
            ctxRangeRef.current,
            tape.tickSize,
            compositeRef.current,
          )
        : null;
    compNodesRef.current = null;
    paintComposite();
  };
  // The tape moved: re-price the open position off the new mark, re-measure
  // how far the market still has to come to fill what's working — and ask the
  // price lines whether one was just crossed, since this is the one funnel
  // every path that moves the price goes through (playback, a step, a seek).
  const mark = (v: number) => {
    if (!Number.isFinite(v) || v === lastPriceRef.current) return;
    const prev = lastPriceRef.current;
    lastPriceRef.current = v;
    checkAlertsRef.current(prev, v);
    ordPrimRef.current?.setMark(v);
    if (!posRef.current) return;
    posRef.current = { ...posRef.current, last: v };
    pushPos();
  };
  // Through a ref: `mark` is declared with the playback plumbing, well before
  // the tools section that owns the lines.
  const checkAlertsRef = useRef<(prev: number, v: number) => void>(() => {});

  // Hide/show per indicator, sharing the journal charts' sticky preference: a
  // band hidden on a strategy chart comes up hidden here, and vice versa. A pane
  // given `prefsPane` keeps its own copy instead — see chartPrefs.paneKey.
  const [vis, setVis] = useState<IndicatorVisibility>(() => loadIndicatorVisibility(prefsPane));
  const visRef = useRef(vis);
  visRef.current = vis;
  const applyRef = useRef<((v: IndicatorVisibility) => void) | null>(null);
  // How heavily each fixed anchor's ±1σ→±2σ region is washed in — the journal
  // charts' preference, shared, exactly as the visibility above is. Global even
  // on a pane with its own `prefsPane`: which layers a context pane draws is a
  // per-pane question, how loud a band's fill is is not.
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
  // Which σ rings each fixed anchor draws — the journal charts' preference,
  // shared, on the same terms as the fill above. It rides the visibility apply
  // rather than getting one of its own: the lines it hides are the same series
  // the row's eye hides, and two functions setting `visible` on one series is two
  // answers to one question. The edge pills follow, because a demoted line that
  // isn't drawn is not a level anyone has lost.
  const [vwapBands, setVwapBands] = useState<VwapBandChoices>(loadVwapBands);
  const bandsRef = useRef(vwapBands);
  const setBands = (anchor: VwapFillAnchor, choice: VwapBandChoice) => {
    const next = { ...bandsRef.current, [anchor]: choice };
    bandsRef.current = next;
    saveVwapBands(next);
    setVwapBands(next);
    applyRef.current?.(visRef.current);
    syncEdgesRef.current();
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
  // What the volume profile's rows are coloured by — value area, or the
  // aggressor delta at each price. The journal charts' preference again, shared
  // and sticky, and it reaches the canvas by the shortest road there is: it
  // changes a fill and nothing else, so no series is rebuilt, nothing is
  // re-profiled, and both histograms are told directly. The ref is what the
  // build effect reads, so a chart rebuilt mid-session comes back tinted.
  // The External Chart overlay's parameters (lib/externalChart) — a pane
  // preference like the two above, sticky and global. The ref is what the
  // painter reads, so a chart rebuilt mid-session comes back at the same period;
  // the primitive is told directly, since none of these change a series and a
  // repaint is all any of them costs. Only the period changes what is *grouped*,
  // and the painter re-reads that from the ref on the same pass.
  const [extParams, setExtParams] = useState(loadExternalChartParams);
  const extParamsRef = useRef(extParams);
  const extPrimRef = useRef<ExternalChartPrimitive | null>(null);
  if (!extPrimRef.current) extPrimRef.current = new ExternalChartPrimitive();
  // Declared beside the state it repaints rather than down with the other
  // painters, so the patcher below closes over an initialised ref.
  const paintExtRef = useRef<(() => void) | null>(null);
  const patchExternal = (patch: Partial<ExternalChartParams>) => {
    const next = { ...extParamsRef.current, ...patch };
    extParamsRef.current = next;
    saveExternalChartParams(next);
    setExtParams(next);
    extPrimRef.current?.setParams(next);
    // A new period is a different grouping, and nothing else on the chart moved
    // — so the repaint has to be asked for rather than waited for.
    if (patch.period !== undefined) paintExtRef.current?.();
  };
  /** Whether the current period actually grouped this chart's bars — false on a
   *  chart already at or above it, which draws nothing and says so. */
  const [extGrouped, setExtGrouped] = useState(true);

  // The HTF trend (lib/htfTrend), wired as the External Chart above: sticky
  // params, a ref for the painter, the primitive told directly. Every knob but
  // the tint changes what is computed, so any patch asks for a repaint.
  const [htfParams, setHtfParams] = useState(loadHtfTrendParams);
  const htfParamsRef = useRef(htfParams);
  const htfPrimRef = useRef<HtfTrendPrimitive | null>(null);
  if (!htfPrimRef.current) htfPrimRef.current = new HtfTrendPrimitive();
  const paintHtfRef = useRef<((force?: boolean) => void) | null>(null);
  const patchHtf = (patch: Partial<HtfTrendParams>) => {
    const next = { ...htfParamsRef.current, ...patch };
    htfParamsRef.current = next;
    saveHtfTrendParams(next);
    setHtfParams(next);
    htfPrimRef.current?.setParams(next);
    paintHtfRef.current?.(true);
  };
  /** The legend's readout: whether any frame is warm yet, and the frames'
   *  agreement at the live edge. Only set on a change, never per frame. */
  const [htfNow, setHtfNow] = useState<{ warm: boolean; state: -1 | 0 | 1 }>({ warm: true, state: 0 });

  // The ranked S/R zones (lib/rankedZones), wired exactly as the overlay above:
  // sticky-global params, a ref for the painter, the primitive told directly.
  // The one difference is what a knob costs. The External Chart's period only
  // changes a *grouping*; every knob here changes the zone set itself, and the
  // zone set is path-dependent over the whole tape — so a patch has to re-run
  // the walk rather than repaint what is already computed.
  const [rzParams, setRzParams] = useState(loadRankedZonesParams);
  const rzParamsRef = useRef(rzParams);
  const rzPrimRef = useRef<RankedZonePrimitive | null>(null);
  if (!rzPrimRef.current) rzPrimRef.current = new RankedZonePrimitive();
  const paintRzRef = useRef<((force?: boolean) => void) | null>(null);
  const patchRankedZones = (patch: Partial<RankedZonesParams>) => {
    const next = { ...rzParamsRef.current, ...patch };
    rzParamsRef.current = next;
    saveRankedZonesParams(next);
    setRzParams(next);
    rzPrimRef.current?.setParams(next);
    // Only the two the *primitive* reads are free. `direction` looks like a
    // display filter and is not one: which zones are visible is decided in
    // `computeRankedZones`, because the draw cap counts passing zones only — so
    // the filter and the cap have to be applied together, in the walk. Leaving
    // it out of this list changed the knob and nothing else, which reads as the
    // filter silently not working.
    if (patch.strengthBars === undefined && patch.zoneText === undefined)
      paintRzRef.current?.(true);
  };
  /** What the last walk produced, for the legend's readout. */
  const [rzCount, setRzCount] = useState(0);
  /** Whether the last walk had prints behind it. Without them the flow ranking
   *  falls back to the author's score, and the legend has to say so. */
  const [rzFlow, setRzFlow] = useState(true);

  // USD economic releases (journal.econ_calendar). Fetched, not computed: one GET
  // per span of days the bars cover, keyed so the tape advancing within a day
  // costs nothing. The impact floor is the one knob and sticky-global.
  const [econParams, setEconParams] = useState(loadEconEventsParams);
  const econParamsRef = useRef(econParams);
  const econPrimRef = useRef<EconEventPrimitive | null>(null);
  if (!econPrimRef.current) econPrimRef.current = new EconEventPrimitive();
  const paintEconRef = useRef<((force?: boolean) => void) | null>(null);
  const tzRef = useRef(tz);
  tzRef.current = tz;
  const [econCount, setEconCount] = useState(0);
  const patchEcon = (floor: EconImpactFloor) => {
    const next = { floor };
    econParamsRef.current = next;
    saveEconEventsParams(next);
    setEconParams(next);
    paintEconRef.current?.(true);
  };

  // Dealer-gamma levels (api/routers/gex.py). Fetched like the econ lines: one GET
  // per span of sessions the bars cover, keyed so the tape advancing within a
  // session costs nothing. Needs the real contract — see `tapeContract`.
  const [gexParams, setGexParams] = useState(loadGexLevelsParams);
  const gexParamsRef = useRef(gexParams);
  const gexPrimRef = useRef<GexLevelsPrimitive | null>(null);
  if (!gexPrimRef.current) gexPrimRef.current = new GexLevelsPrimitive();
  const paintGexRef = useRef<((force?: boolean) => void) | null>(null);
  const tapeContractRef = useRef(tapeContract);
  tapeContractRef.current = tapeContract;
  const [gexLast, setGexLast] = useState<GexStep | null>(null);
  const patchGex = (patch: Partial<GexLevelsParams>) => {
    const next = { ...gexParamsRef.current, ...patch };
    gexParamsRef.current = next;
    saveGexLevelsParams(next);
    setGexParams(next);
    gexPrimRef.current?.setParams(next.walls, next.flip);
    // A different expiry filter is a different set of levels — refetch.
    if (patch.expiry) paintGexRef.current?.(true);
  };
  useEffect(() => {
    paintGexRef.current?.(true);
  }, [tapeContract]);

  const [profileDelta, setProfileDelta] = useState(loadProfileDelta);
  const profileDeltaRef = useRef(profileDelta);
  const setProfileTint = (on: boolean) => {
    profileDeltaRef.current = on;
    saveProfileDelta(on);
    setProfileDelta(on);
    vpRef.current?.setShowDelta(on);
    rangePrimRef.current?.setShowDelta(on);
  };
  // And what that lane is asked — the journal charts' preference again, shared
  // and sticky (lib/deltaFlow). Unlike the switch above this one cannot take the
  // short road to the canvas: bar lengths and flags are derived from the profile,
  // and verdicts from the bars behind it, so the recompute lives in the build
  // effect where both are in scope.
  const [deltaLane, setDeltaLane] = useState<DeltaLaneKnobs>(loadDeltaLane);
  const deltaLaneRef = useRef(deltaLane);
  const applyLaneRef = useRef<(() => void) | null>(null);
  const setLaneKnobs = (patch: Partial<DeltaLaneKnobs>) => {
    const next = { ...deltaLaneRef.current, ...patch };
    deltaLaneRef.current = next;
    saveDeltaLane(next);
    setDeltaLane(next);
    applyLaneRef.current?.();
  };
  const toggle = (key: IndicatorKey) =>
    setVis((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      visRef.current = next;
      applyRef.current?.(next);
      saveIndicatorVisibility(next, prefsPane);
      return next;
    });
  /** Set a layer outright, rather than flipping it. What the topbar catalogue
   *  drives through the handle — "add" and "remove" there are this, since an app
   *  layer has nothing to delete: it is drawn on this pane or it isn't. */
  const setLayer = (key: IndicatorKey, on: boolean) => {
    if (visRef.current[key] === on) return;
    const next = { ...visRef.current, [key]: on };
    visRef.current = next;
    applyRef.current?.(next);
    saveIndicatorVisibility(next, prefsPane);
    setVis(next);
  };
  // Force a layer on. Hide/show is a sticky global preference, so a layer hidden
  // on some other chart stays hidden here — fine for the fixed overlays, wrong
  // for one the user just asked for by hand (the ⚓).
  const reveal = (key: IndicatorKey) => setLayer(key, true);
  const revealRef = useRef(reveal);
  revealRef.current = reveal;

  // The chart's colours, sharing the journal charts' preference the same way the
  // toggles above do. Applied through applyOptions (the effect after the build
  // effect), never a rebuild — a replay rebuilt mid-session would lose the range
  // you are watching. The ref is what the build effect reads, so a chart rebuilt
  // for some other reason comes back in the colours you last chose.
  const [appearance, setAppearance] = useState<ChartAppearance>(loadChartAppearance);
  const appearanceRef = useRef(appearance);
  appearanceRef.current = appearance;
  const changeAppearance = (next: ChartAppearance) => {
    setAppearance(next);
    saveChartAppearance(next);
  };

  // Which layers have actually printed at the current clock — the Globex band is
  // absent on a day with no overnight tape, the NY band doesn't exist until the
  // bell, and the IB doesn't exist before it either. Tracked in a ref alongside
  // the state so the per-frame path only re-renders on the step where one comes
  // to life. A toggle for a line that can't draw is a lie.
  const emptyPresent = {
    bars: false,
    g: false,
    n: false,
    wk: false,
    gp: false,
    np: false,
    wp: false,
    ib: false,
    cvd: false,
  };
  const presentRef = useRef(emptyPresent);
  const [present, setPresent] = useState(emptyPresent);
  const syncPresent = (next: typeof emptyPresent) => {
    const p = presentRef.current;
    if ((Object.keys(next) as (keyof typeof next)[]).every((k) => p[k] === next[k])) return;
    presentRef.current = next;
    setPresent(next);
  };

  // --- Community studies --------------------------------------------------------
  // The topbar picker's indicators (see lib/studies and StudyLayer). Same cadence
  // and the same reason as the vol ruler and Modern VWAP below: every value one
  // of these emits is a fact about a closed bar, so they are recomputed on a
  // snapshot and on a bar close, never per tick.
  const studyLayerRef = useRef<StudyLayer | null>(null);
  // Read through refs inside the build effect, which is installed once — the prop
  // itself would be frozen at mount there.
  const studySpecsRef = useRef<StudySpec[]>(studies ?? EMPTY_STUDIES);
  studySpecsRef.current = studies ?? EMPTY_STUDIES;
  /** What each spec actually drew, as the layer last reported it. Kept here
   *  rather than pushed to the page: the legend that shows it lives in this
   *  component, so the report never has to leave. */
  const [studyReport, setStudyReport] = useState<StudyReport[]>([]);
  const onStudiesRef = useRef(onStudiesChange);
  onStudiesRef.current = onStudiesChange;

  /** What this pane's layers are doing, for the topbar catalogue. Written during
   *  render and read by the effect below it — the array is rebuilt every render,
   *  so the effect keys on its contents instead. */
  const layerStatesRef = useRef<LayerState[]>([]);
  const onLayersRef = useRef(onLayers);
  onLayersRef.current = onLayers;

  const refreshStudies = () => studyLayerRef.current?.setBars(barsRef.current);
  /** The panes moved — CVD or the vol ruler came or went, and pane indices are
   *  positional. Puts the studies' panes back at the end. */
  const remountStudies = () => studyLayerRef.current?.remount();

  // --- CVD ------------------------------------------------------------------
  // Cumulative volume delta: the running sum of signed aggressor volume (lifts
  // minus hits) as of each bar's close, read straight off the tape's `side`. The
  // same quantity api/session_chart._cvd_series ships to the journal charts, and
  // signed the same way — so a CVD read here reads the same as it does there.
  //
  // Anchored at the session's *own* first bar, not at the first drawn one. This
  // workspace glues whole prior days in front of the day being traded (Live loads
  // five by default), and accumulating across them would give the line a
  // multi-day drift that swamps the session's read, which is the thing you are
  // actually watching. So the context days get no CVD and the line starts at the
  // Globex open — which is where the server's anchor lands too.
  const CVD_PANE = 1;
  const cvdSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const cvdPtsRef = useRef<{ time: number; value: number }[]>([]);
  // Cumulative through the last *closed* bar, with the forming bar's own sum
  // (`cur`) carried on top and extended over new ticks only. A step restates the
  // bar still forming, so rescanning its whole span every frame is exactly the
  // cost this accumulator exists to avoid — the same shape as `reprofile`'s.
  const cvdBaseRef = useRef(0);
  const cvdCurRef = useRef(0);
  const cvdScanRef = useRef(0);
  // Whether the tape ever carried an aggressor tag. An older cache — or a symbol
  // the vendor never tagged — prints every tick 'N', and there is nothing to
  // accumulate: the pane and its legend row then don't appear at all, rather than
  // drawing a flat zero line that reads as perfect balance.
  const cvdAnyRef = useRef(false);
  // The same walk's *unaccumulated* per-bar delta, kept in step with `cvdPtsRef`
  // — one entry per session bar, restated in place while a bar is still forming.
  // This is what the CVD oscillator windows (see the block after this one), and
  // taking it from here rather than from a second pass over the tape is what
  // keeps the two panes reading the same prints.
  const cvdDeltaRef = useRef<number[]>([]);

  const resetCvd = () => {
    cvdPtsRef.current = [];
    cvdDeltaRef.current = [];
    cvdBaseRef.current = 0;
    cvdCurRef.current = 0;
    cvdScanRef.current = 0;
    cvdAnyRef.current = false;
  };

  /** Fold one bar — new, or the forming one restated — into the running series,
   *  and return its cumulative value. Does not touch the series itself: a
   *  rebuild walks hundreds of bars through here before there is one to write. */
  const stepCvd = (b: Bar): number => {
    const tape = tapeRef.current;
    const pts = cvdPtsRef.current;
    const last = pts.length ? pts[pts.length - 1] : null;
    if (!last || last.time !== b.time) {
      // The bar that was forming has closed at whatever it reached, and becomes
      // the base the next one accumulates from.
      cvdBaseRef.current = last ? last.value : 0;
      cvdCurRef.current = 0;
      cvdScanRef.current = b.i0;
    }
    let sum = cvdCurRef.current;
    if (tape) {
      const end = Math.min(b.i1, tape.n - 1);
      for (let i = cvdScanRef.current; i <= end; i++) {
        const sd = tape.side[i];
        if (sd === SIDE_BUY) {
          sum += tape.size[i];
          cvdAnyRef.current = true;
        } else if (sd === SIDE_SELL) {
          sum -= tape.size[i];
          cvdAnyRef.current = true;
        }
      }
      // Never walks backwards: a frame that added no ticks leaves the mark where
      // it was rather than re-counting the bar's tail into the sum twice.
      cvdScanRef.current = Math.max(cvdScanRef.current, end + 1);
    }
    cvdCurRef.current = sum;
    const value = cvdBaseRef.current + sum;
    const deltas = cvdDeltaRef.current;
    if (last && last.time === b.time) {
      last.value = value;
      // `sum` is this bar's own delta — the accumulator was zeroed when it
      // opened — so the oscillator's input needs no differencing.
      deltas[deltas.length - 1] = sum;
    } else {
      pts.push({ time: b.time, value });
      deltas.push(sum);
    }
    return value;
  };

  const mountCvd = () => {
    const chart = chartRef.current;
    if (!chart || cvdSeriesRef.current) return;
    const s = chart.addSeries(
      LineSeries,
      {
        color: palette.blue,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "volume" },
      },
      CVD_PANE,
    );
    s.setData(cvdPtsRef.current.map((p) => ({ time: p.time as Time, value: p.value })));
    // A zero reference: CVD crosses sign, and which side of zero it sits on is
    // the whole read — net lifting vs net hitting since the anchor.
    s.createPriceLine({
      price: 0,
      color: palette.grid,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
    cvdSeriesRef.current = s;
    // The default is an even share per pane, so both factors are set explicitly:
    // 5:1 ≈ 83% price / 17% CVD, the same split the journal charts give theirs.
    const panes = chart.panes();
    if (panes.length > CVD_PANE) {
      panes[0].setStretchFactor(1000);
      panes[CVD_PANE].setStretchFactor(200);
    }
  };

  const unmountCvd = () => {
    const chart = chartRef.current;
    const s = cvdSeriesRef.current;
    if (!chart || !s) return;
    cvdSeriesRef.current = null;
    chart.removeSeries(s);
  };

  /** The pane exists only when CVD is both switched on and has something true to
   *  draw. Created and removed rather than hidden: hiding the only series in a
   *  pane leaves the empty pane — and its share of the chart's height — behind,
   *  which on a workspace that is meant to be all chart is the whole cost. */
  const syncCvdMount = () => {
    const want = visRef.current.cvd && cvdAnyRef.current;
    if (want === !!cvdSeriesRef.current) return;
    // CVD owns pane 1 by number, and everything below it mounts at whatever the
    // last pane is — so when CVD comes or goes with those up, they are remounted
    // around the change to keep both claims true. Without this the oscillator
    // would already be holding pane 1 and the line would land inside its pane.
    const vrWas = !!vrDevRef.current;
    const oscWas = !!cvdOscSeriesRef.current;
    if (vrWas) unmountVr();
    if (oscWas) unmountCvdOsc();
    if (want) mountCvd();
    else unmountCvd();
    if (oscWas) mountCvdOsc();
    if (vrWas) mountVr();
    // And the studies' panes after both, for the same reason: they claim the
    // last pane indices, and a pane appearing or vanishing below them renumbers
    // every one of them.
    remountStudies();
  };

  /** Rebuild from a snapshot's bars. A seek can move backwards, so the
   *  accumulator is thrown away and re-walked rather than patched. Session bars
   *  only — `history` is the context days, which the anchor sits after. */
  const rebuildCvd = (drawn: Bar[], histCount: number) => {
    resetCvd();
    for (let i = histCount; i < drawn.length; i++) stepCvd(drawn[i]);
    // Mount first, so a tape that turned out to carry no aggressor tags drops the
    // pane before anything is written to it.
    syncCvdMount();
    cvdSeriesRef.current?.setData(
      cvdPtsRef.current.map((p) => ({ time: p.time as Time, value: p.value })),
    );
    syncCvdOscMount();
    drawCvdOsc();
  };

  // --- CVD oscillator ---------------------------------------------------------
  // The same signed volume, *windowed* instead of accumulated (see lib/cvdOsc for
  // the port and its provenance): a histogram of the last N bars' delta, with the
  // fractal divergences against price drawn on this pane and over the candles.
  //
  // Where the pane above answers "who has been in control since the open", this
  // one answers "who is in control now" — a rolling window has no memory of the
  // morning, which is the entire difference and the reason both exist.
  //
  // Every value here is derived from bars and the delta array, never from the
  // tape directly, so this pane cannot drift out of step with the one above it.
  // The heavy pass (fractals, pivots, the divergence pairing) runs on bar close
  // and on snapshot; a frame that only restates the forming bar re-windows the
  // histogram and writes one point.
  const cvdOscSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const cvdOscMarksRef = useRef<CvdDivergencePrimitive | null>(null);
  const cvdOscPriceMarksRef = useRef<CvdDivergencePrimitive | null>(null);
  const [cvdOscParams, setCvdOscParams] = useState<CvdOscParams>(loadCvdOsc);
  const cvdOscRef = useRef(cvdOscParams);
  cvdOscRef.current = cvdOscParams;
  /** What the legend reports: how many divergences the session has printed and
   *  how consecutive the last one was. State, so the row re-renders — hence the
   *  same unchanged-value guard `vrLastRef` exists for. */
  const [cvdOscRead, setCvdOscRead] = useState({ divs: 0, strength: 0 });
  const patchCvdOsc = useCallback((patch: Partial<CvdOscParams>) => {
    setCvdOscParams((prev) => {
      const next = { ...prev, ...patch };
      cvdOscRef.current = next;
      saveCvdOsc(next);
      return next;
    });
  }, []);

  /** The session's own bars — the ones `cvdDeltaRef` is indexed against. The
   *  context days carry no delta here (see the anchor note on CVD above), so
   *  windowing across them would window across a boundary the numbers don't
   *  cross. */
  const cvdOscBars = () => barsRef.current.slice(histCountRef.current);

  /** The full pass: window, fractals, divergences, both sets of marks. */
  const drawCvdOsc = () => {
    const s = cvdOscSeriesRef.current;
    // A dark layer computes nothing. This runs on every bar close, and the pass
    // below is the expensive one — the fractal scan over the whole session — so
    // without this the switch would only be turning off the *drawing*, and the
    // vol ruler's reason for computing while hidden (the ticket reads it) has no
    // equivalent here: nothing else consumes a divergence.
    if (!s) return;
    const bars = cvdOscBars();
    const { hist, divergences } = computeCvdOsc(bars, cvdDeltaRef.current, cvdOscRef.current);
    s.setData(
      bars.map((b, i) => {
        const v = hist[i];
        // Whitespace, not zero, through the seeding window: zero on a windowed
        // delta means "flow balanced", which is a claim the first N bars can't
        // make.
        if (!Number.isFinite(v)) return { time: b.time as Time };
        return {
          time: b.time as Time,
          value: v,
          color: v >= 0 ? palette.green : palette.red,
        };
      }),
    );
    const seg = (d: CvdOscDivergence, v1: number, v2: number) => ({
      kind: d.kind,
      t1: d.t1,
      v1,
      t2: d.t2,
      v2,
      label: d.kind === "bear" ? "-RD" : "+RD",
    });
    // The same divergence in two unit systems: delta on this pane, price over the
    // candles. One pairing, so the two can't disagree about what was found.
    cvdOscMarksRef.current?.setData(divergences.map((d) => seg(d, d.h1, d.h2)));
    cvdOscPriceMarksRef.current?.setData(divergences.map((d) => seg(d, d.p1, d.p2)));
    const last = divergences[divergences.length - 1];
    const strength = last ? last.strength : 0;
    setCvdOscRead((prev) =>
      prev.divs === divergences.length && prev.strength === strength
        ? prev
        : { divs: divergences.length, strength },
    );
  };

  /** The per-frame path: the forming bar's delta moved, so the window it sits at
   *  the end of moved with it. Nothing else can have changed — a fractal needs
   *  `fractalN` closed bars to its right, so no divergence can appear or vanish
   *  inside a bar. */
  const stepCvdOsc = () => {
    const s = cvdOscSeriesRef.current;
    if (!s) return;
    const bars = cvdOscBars();
    if (!bars.length) return;
    // Rebuilds the whole window array to read its last value, once per frame.
    // Measured 2026-08-22 at 0.0065ms a call — 0.1% of a frame across two charts
    // — against a delta pane that costs no measurable fps at all. An
    // allocation-free tail was written and reverted: it bought that 0.1% for a
    // second implementation of the window math to keep in parity with this one.
    const hist = cvdOscHist(cvdDeltaRef.current, cvdOscRef.current);
    const i = bars.length - 1;
    const v = hist[i];
    if (!Number.isFinite(v)) return;
    s.update({
      time: bars[i].time as Time,
      value: v,
      color: v >= 0 ? palette.green : palette.red,
    });
  };

  const mountCvdOsc = () => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle || cvdOscSeriesRef.current) return;
    // Under CVD's pane when that one is up, above the vol ruler's either way —
    // see syncCvdMount and syncCvdOscMount for how the three claims are kept
    // true together.
    const paneIdx = chart.panes().length;
    const s = chart.addSeries(
      HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "volume" },
      },
      paneIdx,
    );
    // The sign is the read here as much as it is on the cumulative pane, and a
    // histogram that autoscales to a one-sided window can put zero off-frame.
    s.createPriceLine({
      price: 0,
      color: palette.grid,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
    cvdOscSeriesRef.current = s;
    const marks = new CvdDivergencePrimitive();
    s.attachPrimitive(marks as any);
    cvdOscMarksRef.current = marks;
    const priceMarks = new CvdDivergencePrimitive();
    candle.attachPrimitive(priceMarks as any);
    cvdOscPriceMarksRef.current = priceMarks;
    drawCvdOsc();
    const panes = chart.panes();
    panes[0]?.setStretchFactor(1000);
    panes[paneIdx]?.setStretchFactor(200);
  };

  const unmountCvdOsc = () => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    // The price-pane marks outlive their own pane, so they have to be detached
    // by hand — the series removal below only takes this pane's copy with it.
    if (candle && cvdOscPriceMarksRef.current)
      candle.detachPrimitive(cvdOscPriceMarksRef.current as any);
    cvdOscPriceMarksRef.current = null;
    cvdOscMarksRef.current = null;
    const s = cvdOscSeriesRef.current;
    if (!chart || !s) return;
    cvdOscSeriesRef.current = null;
    chart.removeSeries(s);
  };

  /** Same rule as CVD's: built and torn down rather than hidden, because the
   *  pane keeps its height either way. Gated on the same `cvdAnyRef` — an
   *  untagged tape has no delta to window any more than it has one to
   *  accumulate. */
  const syncCvdOscMount = () => {
    const want = visRef.current.cvdOsc && cvdAnyRef.current;
    if (want === !!cvdOscSeriesRef.current) return;
    const vrWas = !!vrDevRef.current;
    if (vrWas) unmountVr();
    if (want) mountCvdOsc();
    else unmountCvdOsc();
    if (vrWas) mountVr();
    remountStudies();
  };

  // --- Vol ruler --------------------------------------------------------------
  // The bar-range volatility pane (see lib/volRuler for what the three lines
  // are and why the median exists next to the ATR). Unlike CVD it has no
  // per-tick accumulator: every value is a fact about a *closed* bar, so it is
  // recomputed whole on snapshot and on bar close — a sort over a session of
  // ranges, sub-millisecond against a cadence of once a bar.
  const vrAtrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vrDevRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vrYdayLineRef = useRef<IPriceLine | null>(null);
  // Through a ref for the same reason `mvRef` is: the build effect captures one
  // `refreshVr` for the life of the chart, so a prop read inside it would be
  // frozen at mount.
  const onVolRulerRef = useRef(onVolRuler);
  onVolRulerRef.current = onVolRuler;
  /** The last reading handed up, so an unchanged one is not handed up again.
   *  `refreshVr` also runs on every snapshot — a seek, a timeframe swap, a
   *  re-anchor — and those mostly re-report the same three numbers. The page
   *  puts this in state, so a fresh object each time is a page re-render each
   *  time for no news. */
  const vrLastRef = useRef<VolRulerRead | null>(null);
  /** The same ruler at the order presets' fixed bucketing, which is not the one
   *  being drawn. Stateful (it measures each block of prints once), hence a ref
   *  that lives as long as the chart does. */
  const vrPresetRef = useRef(new PresetRuler());

  const mountVr = () => {
    const chart = chartRef.current;
    if (!chart || vrDevRef.current) return;
    // Always the last pane — after CVD's when that one is up (see syncCvdMount
    // for how the two claims are kept true together).
    const paneIdx = chart.panes().length;
    const fmt = {
      type: "custom" as const,
      formatter: (v: number) => `${Math.round(v)}t`,
      minMove: 1,
    };
    const atrS = chart.addSeries(
      LineSeries,
      {
        color: palette.violet,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: fmt,
        // The pane keeps the 50t rule in view however hot the tape gets — the
        // distance between the lines and that rule is the whole read, and on a
        // 2026 day autoscale would otherwise leave it below the frame.
        autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => {
          const r = orig();
          if (r?.priceRange)
            r.priceRange.minValue = Math.min(r.priceRange.minValue, VR_STOP_TICKS - 10);
          return r;
        },
      },
      paneIdx,
    );
    const devS = chart.addSeries(
      LineSeries,
      {
        color: palette.gold,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: fmt,
      },
      paneIdx,
    );
    devS.createPriceLine({
      price: VR_STOP_TICKS,
      color: palette.muted,
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      title: `${VR_STOP_TICKS}t stop`,
    });
    vrAtrRef.current = atrS;
    vrDevRef.current = devS;
    const panes = chart.panes();
    panes[0]?.setStretchFactor(1000);
    panes[paneIdx]?.setStretchFactor(200);
  };

  const unmountVr = () => {
    const chart = chartRef.current;
    if (!chart) return;
    // The yday line dies with its series; the ref just has to agree.
    vrYdayLineRef.current = null;
    if (vrAtrRef.current) {
      chart.removeSeries(vrAtrRef.current);
      vrAtrRef.current = null;
    }
    if (vrDevRef.current) {
      chart.removeSeries(vrDevRef.current);
      vrDevRef.current = null;
    }
  };

  const syncVrMount = () => {
    const was = !!vrDevRef.current;
    if (visRef.current.volRuler && barsRef.current.length > histCountRef.current) mountVr();
    else unmountVr();
    // Only when the pane actually came or went: this runs on every bar close, and
    // rebuilding the studies once a bar to discover nothing moved is the one way
    // to make a picked indicator flicker.
    if (was !== !!vrDevRef.current) remountStudies();
  };

  /** Recompute and redraw the whole pane from the drawn bars as they stand.
   *
   *  The computation happens whether or not the pane is up, because the reading
   *  now feeds the ticket's risk sizer as well as the drawing — and a sizer that
   *  went blank when you collapsed a pane would be a sizer nobody could rely on.
   *  Only the drawing is gated on the series existing. */
  const refreshVr = () => {
    syncVrMount();
    const d = computeVolRuler(
      barsRef.current,
      histCountRef.current,
      tapeRef.current?.tickSize ?? 0.25,
    );
    // The presets' ruler, off the prints rather than off these bars: the
    // session's own stretch of tape, up to the last print the drawn bars have
    // reached. Cheap to ask on every bar close — it folds each print into its
    // bars once and remembers (see PresetRuler).
    const bars = barsRef.current;
    const hist = histCountRef.current;
    const preset = vrPresetRef.current.read(
      tapeRef.current,
      bars.length > hist ? bars[hist].i0 : -1,
      bars.length ? bars[bars.length - 1].i1 : -1,
      tapeRef.current?.tickSize ?? 0.25,
    );
    // The last *closed* bar's values — the ruler as it stands. Reported before
    // the draw so the ticket keeps up with a pane that is switched off.
    const read: VolRulerRead | null =
      d.atr.length || d.dev.length || d.yday != null || hasPresetRead(preset)
        ? {
            atr: d.atr.length ? d.atr[d.atr.length - 1].value : null,
            dev: d.dev.length ? d.dev[d.dev.length - 1].value : null,
            yday: d.yday,
            preset,
          }
        : null;
    const was = vrLastRef.current;
    if (
      !was !== !read ||
      (was &&
        read &&
        (was.atr !== read.atr ||
          was.dev !== read.dev ||
          was.yday !== read.yday ||
          // By value: the ruler builds a fresh record every read, so identity
          // here would push a re-render on every bar close for ever.
          !samePresetRead(was.preset, read.preset)))
    ) {
      vrLastRef.current = read;
      onVolRulerRef.current?.(read);
    }
    const devS = vrDevRef.current;
    const atrS = vrAtrRef.current;
    if (!devS || !atrS) return;
    atrS.setData(d.atr.map((p) => ({ time: p.time as Time, value: p.value })));
    devS.setData(d.dev.map((p) => ({ time: p.time as Time, value: p.value })));
    if (d.yday != null) {
      const opts = { price: d.yday, title: `yday ${Math.round(d.yday)}t` };
      if (vrYdayLineRef.current) vrYdayLineRef.current.applyOptions(opts);
      else
        vrYdayLineRef.current = devS.createPriceLine({
          ...opts,
          color: palette.green,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
        });
    } else if (vrYdayLineRef.current) {
      devS.removePriceLine(vrYdayLineRef.current);
      vrYdayLineRef.current = null;
    }
  };

  // --- Modern VWAP ------------------------------------------------------------
  // Same cadence and the same reason as the vol ruler: every value here is a
  // fact about a *closed* bar (KER, the trailing medians, a confirmed pivot, a
  // close back inside the envelope), so it is recomputed whole on snapshot and
  // on bar close rather than stepped per tick. One pass is O(bars) plus a
  // rolling median that keeps its window sorted — a couple of milliseconds over
  // a session with three context days behind it, against a cadence of once a
  // bar. Doing it per tick would be the one layer on this chart that made the
  // tape stutter, for numbers that cannot change between closes.
  //
  // The drawing itself is ./modernVwapLayer — shared with the journal charts,
  // which grew the same layer for the Interactions Lab. What stays here is the
  // wiring: when to redraw, what to feed it, and what the legend quotes.
  const mvLayerRef = useRef<ModernVwapLayer | null>(null);
  // Null when the page doesn't offer the layer. Read through the ref rather than
  // off the prop everywhere below: the build effect captures one `refreshMv` for
  // the life of the chart, so a prop read inside it would be frozen at mount.
  const mvRef = useRef<ModernVwapParams | null>(mvParams ?? null);
  mvRef.current = mvParams ?? null;
  /** What the legend rows quote — the gate's own shares, and how many triggers
   *  it let through at the current settings. Fed by the layer on every redraw,
   *  including the ones it does on its own when a row is switched back on. */
  const [mvRead, setMvRead] = useState({ trendPct: 0, undefPct: 0, signals: 0, anchors: 0 });
  const onMvData = useCallback((d: ModernVwapData | null) => {
    // A dark layer keeps its last numbers rather than printing zeros: the rows
    // stop quoting them anyway (see `mvLive` in the legend below), and "0 through
    // the gate" would read as a measurement when it only means nobody asked.
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
  /** Developing globex POC by bar time, for the indicator's `poc` anchor. Kept
   *  as its own map rather than read back off the profile line series, because
   *  those refs are null when the page doesn't offer that layer — and the
   *  anchor should not silently change meaning with an unrelated toggle. */
  const mvPocRef = useRef<Map<number, number>>(new Map());
  const mvWkPocRef = useRef<Map<number, number>>(new Map());
  /** The three developing value areas' newest points, for the reader that has to
   *  know where a VAH *was* and not only where it is (lib/levelApproach).
   *
   *  Kept here rather than read back off the profile line series for the reason
   *  above it and one more: `series.data()` rebuilds a series' whole raw point
   *  array on the next paint, which is the cost the overlay draw gate exists to
   *  avoid. Bounded to the window the classification looks over — this is not a
   *  history, it is the last few bars, and anything longer would be a second
   *  copy of the profile the engine already holds. */
  const vaPathsRef = useRef<{ g: ProfilePt[]; n: ProfilePt[]; w: ProfilePt[] }>({
    g: [],
    n: [],
    w: [],
  });

  // --- Dynamic Swing VWAP [Zeiierman] --------------------------------------
  // The other swing-anchored VWAP, on the same cadence and the same terms as
  // Modern VWAP above: recomputed whole on bar close, drawn by a shared layer
  // (./dynamicSwingVwapLayer), and computed not at all while its row is off.
  const dsvLayerRef = useRef<DynamicSwingVwapLayer | null>(null);
  const dsvRef = useRef<DsvParams | null>(dsvParams ?? null);
  dsvRef.current = dsvParams ?? null;
  /** What the row quotes: how many times the structure flipped, which way it
   *  reads now, and what the volatility adjustment has done to the half-life. */
  const [dsvRead, setDsvRead] = useState({
    pivots: 0,
    bullPct: 0,
    aptNow: 0,
    dropped: 0,
    // Whether the anchor timeframe actually regrouped this pane's bars — a grid
    // at or below its own bucketing merges nothing, and a row claiming '@5m' on
    // a 15m pane would be quoting a knob rather than what was computed.
    anchored: false,
  });
  const onDsvData = useCallback((d: DynamicSwingVwapData | null) => {
    // A dark layer keeps its last numbers rather than printing zeros — same
    // reason as onMvData above.
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
  const dsvSource = (): DsvSource => ({
    bars: barsRef.current,
    histCount: histCountRef.current,
    params: dsvRef.current,
  });
  const dsvSourceRef = useRef(dsvSource);
  dsvSourceRef.current = dsvSource;
  const refreshDsv = () => dsvLayerRef.current?.redraw();
  const refreshDsvRef = useRef(refreshDsv);
  refreshDsvRef.current = refreshDsv;

  /** Everything the layer cannot know, pulled when it redraws: which parameters
   *  this page holds, which bars are context and which are the session, and which
   *  developing POC the anchor is against. */
  const mvSource = (): MvSource => {
    const p = mvRef.current;
    return {
      bars: barsRef.current,
      histCount: histCountRef.current,
      params: p,
      ctx: {
        // The weekly map is empty when the weekly profile couldn't be honestly
        // seeded, and the anchor then degrades to a plain session anchor — the
        // legend's 1⚓ is the tell, same absence rule as the weekly rows.
        poc: p?.pocSource === "weekly" ? mvWkPocRef.current : mvPocRef.current,
        tickSize: tapeRef.current?.tickSize,
      },
    };
  };
  const mvSourceRef = useRef(mvSource);
  mvSourceRef.current = mvSource;
  const refreshMv = () => mvLayerRef.current?.redraw();
  // Through a ref: the knob-change effect below is declared before the build
  // effect that gives `refreshMv` anything to draw into.
  const refreshMvRef = useRef(refreshMv);
  refreshMvRef.current = refreshMv;

  // --- every level this chart is drawing ---------------------------------------
  // One enumerator, two readers. The price-scale edge markers want the levels
  // *excluded from the fit* (below); the approach panel wants all of them. They
  // used to be one function answering only the first question, and a second copy
  // for the second would be two lists that disagree about what the chart is
  // drawing — so the list is one and `fitted` is the axis they differ on.
  //
  // `times` is the window the approach classification reads over: each level is
  // sampled *onto those bar times*, never indexed by offset into its own array.
  // The arrays do not start at the same bar (Globex opens at 18:00, NY at the
  // bell, the weekly arrives history-prefixed), so an offset would pair one
  // bar's level with another bar's close and answer plausibly about nothing.
  // Pass nothing and no paths are built, which is what the edge markers want —
  // they run on every pan and only ever read `price`.
  //
  // Declared here rather than beside `syncOffTape` so that every ref it reads is
  // in scope above it; nothing calls it during the render pass.
  const enumerateLevels = (times: readonly number[] = []): EnumeratedLevel[] => {
    const out: EnumeratedLevel[] = [];
    const v = visRef.current;
    const flat = (price: number) => new Array<number>(times.length).fill(price);
    const push = (
      label: string,
      price: number | undefined,
      fitted: boolean,
      family: LevelFamily,
      path: number[],
    ) => {
      if (price != null && Number.isFinite(price))
        out.push({ label, price, fitted, family, path, key: levelKey(family, label) });
    };
    // A ring the band knob has taken off the chart gets no row and no pill: the
    // marker exists to say "this level is off the pane", and a line you asked not
    // to draw is not a level anyone has lost.
    const anchor = (
      a: Anchor | null,
      on: boolean,
      name: string,
      midDemoted: boolean,
      key?: VwapFillAnchor,
    ) => {
      if (!a || !on) return;
      const p = a.pts[a.pts.length - 1];
      if (!p) return;
      const s = key ? vwapBandsShown(bandsRef.current[key]) : ALL_BANDS;
      const at = (pick: (q: VwapPoint) => number) => sampleAt(a.pts, times, pick);
      // The weekly is the one anchor demoted mid and all (see mkBand), so its mid
      // is the one that can leave the pane without anything else saying so.
      push(name, p.middle, !midDemoted, "vwap", at((q) => q.middle));
      if (s.s1) {
        push(`${name}${SIGMA_LABEL.u1}`, p.upper1, false, "vwap", at((q) => q.upper1));
        push(`${name}${SIGMA_LABEL.l1}`, p.lower1, false, "vwap", at((q) => q.lower1));
      }
      if (s.s2) {
        push(`${name}${SIGMA_LABEL.u2}`, p.upper2, false, "vwap", at((q) => q.upper2));
        push(`${name}${SIGMA_LABEL.l2}`, p.lower2, false, "vwap", at((q) => q.lower2));
      }
    };
    anchor(gRef.current, v.vwapGlobex, "GX VWAP", false, "globex");
    anchor(nRef.current, v.vwapNy, "NY VWAP", false, "ny");
    anchor(aRef.current, v.vwapAnchored, "⚓ VWAP", false);
    anchor(wkRef.current, v.vwapWeekly, "WK VWAP", true, "weekly");

    // The three developing value areas, off the bounded window kept beside the
    // POC maps — the only per-bar path these have on this side of the wire.
    const va = vaPathsRef.current;
    const devVa = (pts: ProfilePt[], on: boolean, name: string) => {
      const p = pts[pts.length - 1];
      if (!on || !p) return;
      for (const k of PROF_KEYS) {
        push(`${name} ${k.toUpperCase()}`, p[k], true, "devVa", sampleAt(pts, times, (q) => q[k]));
      }
    };
    devVa(va.g, v.developingProfileGlobex, "GX");
    devVa(va.n, v.developingProfileNy, "NY");
    devVa(va.w, v.developingProfileWeekly, "WK");

    const mv = mvLayerRef.current?.last();
    if (mv && v.modernVwap) {
      // One past the window, for the same reason `VA_PATH_BARS` is two past the
      // lookback: the newest point here is the forming bar, which the window the
      // caller asked about does not include.
      const tail = mvLayerRef.current?.tail(times.length + 1) ?? [];
      for (const k of MV_KEYS) {
        // A ring above the chosen envelope draws nothing.
        if (MV_RING[k] > mv.bands) continue;
        push(`MV${SIGMA_LABEL[k]}`, mv.pt[k], MV_RING[k] === 0, "modernVwap",
             sampleAt(tail, times, (q) => q[k]));
      }
    }
    const dsv = dsvLayerRef.current?.last();
    if (dsv && v.dynamicSwingVwap) {
      const tail = dsvLayerRef.current?.tail(times.length + 1) ?? [];
      push("DSV", dsv.value, true, "dsv", sampleAt(tail, times, (q) => q.value));
    }

    // The IB, only once its hour is up. A developing IB genuinely moves and no
    // history of it is kept, so treating it as flat would credit price with all
    // of the closing — a directional lie during exactly the hour the read is
    // wanted. Absent is the honest answer; the extensions already worked this way.
    const ib = ibRef.current;
    if (ib && ib.complete) {
      if (v.initialBalance) {
        push("IB high", ib.high, true, "ib", flat(ib.high));
        push("IB low", ib.low, true, "ib", flat(ib.low));
      }
      if (v.ibExtensions) {
        const range = ib.high - ib.low;
        for (const m of [1, 1.5, 2]) {
          push(`IB +${m}×`, ib.high + m * range, false, "ib", flat(ib.high + m * range));
          push(`IB −${m}×`, ib.low - m * range, false, "ib", flat(ib.low - m * range));
        }
      }
    }

    // Frozen at the prior close by construction, so its path is genuinely flat —
    // `level_closed` is 0 here because the level really did not move.
    const comp = compRef.current?.profile;
    if (comp && v.compositeProfile) {
      push("C-POC", comp.poc, true, "composite", flat(comp.poc));
      push("C-VAH", comp.vah, true, "composite", flat(comp.vah));
      push("C-VAL", comp.val, true, "composite", flat(comp.val));
    }
    const nodes = compNodesRef.current;
    if (nodes && v.compositeNodes) {
      for (const h of nodes.hvn) push("C-HVN", h.price, true, "composite", flat(h.price));
      for (const l of nodes.lvn) push("C-LVN", l.price, true, "composite", flat(l.price));
    }

    // Your own lines, which are the levels this panel exists for. Static like the
    // composite, and for a stronger reason: a price you marked cannot chase you.
    // Keyed by id rather than by label, unlike everything above: these all share
    // one name, so `push`'s family+label key would make every line you drew the
    // same level. Arm two of them and one toggle would light both.
    for (const l of hlinesRef.current)
      if (Number.isFinite(l.price))
        out.push({
          label: "your line",
          price: l.price,
          fitted: true,
          family: "hline",
          path: flat(l.price),
          key: hlineKey(l.id),
        });
    return out;
  };

  /** The levels the price scale does not fit to — the four anchors' σ envelopes,
   *  the weekly mid, the Modern VWAP rings and the IB extension guides. */
  const demotedLevels = (): { label: string; price: number }[] =>
    enumerateLevels().filter((l) => !l.fitted);

  // --- the levels near price, and how price is arriving at them ---------------
  const [levelRows, setLevelRows] = useState<ApproachRow[]>([]);
  const [levelOpen, setLevelOpen] = useState(loadLevelPanelOpen);
  const levelBoxRef = useRef<HTMLDivElement | null>(null);
  /** Reach past `NEAR_TICKS`, so a level price has not arrived at can still be
   *  armed. Not sticky: it widens the panel to a dozen-odd rows, which is a
   *  thing you open to do something and then close again. */
  const [levelReach, setLevelReach] = useState(false);
  const levelReachRef = useRef(false);
  levelReachRef.current = levelReach;

  /** Re-read the panel. Bar-close cadence, never per frame: the classification
   *  spans whole bars, so between closes there is nothing for it to say that it
   *  did not already say — and the enumerator walks every drawn layer.
   *
   *  The *forming* bar is deliberately outside the window. Every developing
   *  layer re-prints that bar's entry on every step, so a window ending on it
   *  would re-flip its own answer inside each bar; the panel says "as of last
   *  close" because that is exactly what it is. Distances are the live half and
   *  are painted separately (`paintLevelDist`). */
  const refreshLevels = () => {
    if (!levelPanel) return;
    const bars = barsRef.current;
    const tick = tapeRef.current?.tickSize ?? 0;
    const price = lastPriceRef.current;
    const end = bars.length - 1; // exclusive: the forming bar
    if (end < 2 || !(tick > 0) || !Number.isFinite(price)) {
      setLevelRows((prev) => (prev.length ? [] : prev));
      return;
    }
    const win = bars.slice(Math.max(0, end - VA_PATH_BARS), end);
    const times = win.map((b) => b.time);
    const rows = clusterLevels(
      enumerateLevels(times),
      win.map((b) => b.close),
      win.length - 1,
      price,
      tick,
      // Reaching past `NEAR_TICKS` is what the ⌁ mode is for: a level worth
      // arming is usually one price has *not* got to yet, and at 40 ticks the
      // panel cannot see far enough to offer it. Only the reach changes — the
      // clustering, the classification and the ordering are the panel's own.
      levelReachRef.current ? { nearTicks: ARM_REACH_TICKS } : {},
    );
    // Settle for a no-op render the way the edge markers do: this runs on every
    // bar close, and a row set that says the same thing is the same panel.
    const key = (rs: ApproachRow[]) =>
      rs.map((r) => `${r.cls}:${r.members.map((m) => m.label).join(",")}`).join("|");
    setLevelRows((prev) => (key(prev) === key(rows) ? prev : rows));
  };
  const refreshLevelsRef = useRef(refreshLevels);
  refreshLevelsRef.current = refreshLevels;

  // Widening the reach has to answer now rather than at the next bar close — the
  // gesture is "show me more", and a panel that sat unchanged for thirty seconds
  // would read as a control that does nothing.
  useEffect(() => {
    refreshLevelsRef.current();
  }, [levelReach]);

  /** The distances, which move with the tape rather than with the bars — written
   *  straight at the DOM like the crosshair's OHLC line, and for the same reason:
   *  ten numbers a frame through React would re-render the panel at the tape's
   *  cadence to change two digits.
   *
   *  And the row *order* with them: nearest first, farthest last. Ranking is here
   *  and not in `clusterLevels` because a distance-ranked list built at bar close
   *  is out of order the moment price moves — the rows are rebuilt every thirty
   *  seconds and the distances every frame, and the panel visibly read "−15, −2,
   *  −21, −33" the first time it was tried. The numbers and their ranking come
   *  from the same read of the same price, so they cannot disagree. React never
   *  sees it: source order stays price-descending (which is what a panel with no
   *  painter — the journal replayer, the Recall card — falls back to), and the
   *  ranking is CSS `order` on a flex column. */
  const levelOrderRef = useRef("");
  const paintLevelDist = () => {
    const box = levelBoxRef.current;
    const tick = tapeRef.current?.tickSize ?? 0;
    const price = lastPriceRef.current;
    if (!box || !(tick > 0) || !Number.isFinite(price)) return;
    const ranked: { row: HTMLElement; at: number; away: number }[] = [];
    for (const el of box.querySelectorAll<HTMLElement>(".chart-levels-dist")) {
      const at = Number(el.dataset.price);
      if (!Number.isFinite(at)) continue;
      const ticks = Math.round((at - price) / tick);
      el.textContent = `${ticks > 0 ? "+" : ""}${ticks}t`;
      const row = el.parentElement;
      if (row instanceof HTMLElement) ranked.push({ row, at, away: Math.abs(ticks) });
    }
    // Ties by the order they arrived in, which is price-descending — two levels
    // equidistant either side of price read high-then-low, the way the scale does.
    ranked.sort((a, b) => a.away - b.away);
    // Writing `order` on every row every frame would be a layout invalidation per
    // print for a panel that mostly is not re-ranking. The ranking only changes
    // when price crosses the midpoint between two adjacent levels, so compare
    // first: one string join against a dozen short numbers, versus a reflow.
    //
    // On the prices and not the distances: a re-render hands back fresh row
    // elements with no inline `order` on them, and a new row set that happened to
    // sit at the same distances would otherwise be left unranked at `order: 0` —
    // which is *ahead* of every ranked row, so the panel would fail loudly and
    // rarely. The prices change whenever the set does.
    const sig = ranked.map((r) => r.at).join(",");
    if (sig === levelOrderRef.current) return;
    levelOrderRef.current = sig;
    ranked.forEach((r, i) => {
      r.row.style.order = String(i + 1);
    });
  };
  const paintLevelDistRef = useRef(paintLevelDist);
  paintLevelDistRef.current = paintLevelDist;

  // A freshly rendered row has *no* distance in it: the numbers are written
  // straight at the DOM by the tape, so a row that has just appeared carries
  // whatever the last print left in the row that used to be in that slot —
  // nothing, for a row at the end. Invisible while the chart is playing, because
  // the next print covers it within a frame; plainly wrong on a paused one,
  // which is where widening the reach put it.
  useEffect(() => {
    paintLevelDistRef.current();
  }, [levelRows]);

  /** The nearest demoted level off the top and off the bottom, and how many are
   *  out there each way. `paneH` rides along because the lower marker sits on the
   *  bottom edge of the *price* pane, which is not the bottom of the element when
   *  CVD is mounted under it. */
  const [edges, setEdges] = useState<{
    up: EdgeLevel | null;
    down: EdgeLevel | null;
    nUp: number;
    nDown: number;
    paneH: number;
  }>({ up: null, down: null, nUp: 0, nDown: 0, paneH: 0 });
  const NO_EDGES = { up: null, down: null, nUp: 0, nDown: 0, paneH: 0 };
  const syncEdges = () => {
    const chart = chartRef.current;
    const pr = chart?.priceScale("right").getVisibleRange();
    const price = lastPriceRef.current;
    if (!chart || pr == null || !(pr.to > pr.from) || !Number.isFinite(price)) {
      setEdges((prev) => (prev.up || prev.down ? NO_EDGES : prev));
      return;
    }
    let up: EdgeLevel | null = null;
    let down: EdgeLevel | null = null;
    let nUp = 0;
    let nDown = 0;
    // Only levels that are properly gone. A σ band a few points over the edge is
    // off screen in the literal sense and nobody has lost it — one scroll finds
    // it, and a pill that sits there through most of a session is a pill you stop
    // reading. Half a pane past the edge is the line: on the sitting this was
    // built for that is ~100 points against a weekly mid 665 past the edge, so
    // the case the markers exist for still speaks and the envelope stays quiet.
    const far = (pr.to - pr.from) * 0.5;
    for (const l of demotedLevels()) {
      // Nearest wins: the level about to come back is the one worth naming, and
      // the count carries the rest.
      if (l.price > pr.to + far) {
        nUp++;
        if (!up || l.price < up.price) up = { ...l, dist: l.price - price };
      } else if (l.price < pr.from - far) {
        nDown++;
        if (!down || l.price > down.price) down = { ...l, dist: price - l.price };
      }
    }
    const paneH = chart.panes()[0]?.getHeight() ?? 0;
    // This runs on every pan and every bar close, so it settles for the same
    // no-op render `setOffTape` relies on: same label at the same whole point
    // distance is the same marker.
    const key = (e: EdgeLevel | null) => (e ? `${e.label}@${Math.round(e.dist)}` : "");
    setEdges((prev) =>
      key(prev.up) === key(up) &&
      key(prev.down) === key(down) &&
      prev.nUp === nUp &&
      prev.nDown === nDown &&
      prev.paneH === paneH
        ? prev
        : { up, down, nUp, nDown, paneH },
    );
  };
  syncEdgesRef.current = syncEdges;

  // A knob turned re-derives everything — the anchors, the regime and the
  // triggers all move together, and there is no partial version of that.
  useEffect(() => {
    refreshMvRef.current();
  }, [mvParams]);

  useEffect(() => {
    refreshDsvRef.current();
  }, [dsvParams]);

  // The window length, its shape and the fractal width all change what the
  // histogram *is*, so a turn of any of them is a full re-derive — but a redraw,
  // never a rebuild: the pane keeps its series and the chart keeps its zoom.
  useEffect(() => {
    drawCvdOsc();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cvdOscParams]);

  // --- hand-drawn tools -----------------------------------------------------
  // Everything below is mirrored into refs so the mouse handlers inside the build
  // effect can read it without becoming effect deps: arming a tool or moving a
  // profile must not rebuild the chart (that would lose zoom, scroll, and the
  // streamed history). React state exists only to render the toolbar.

  // Fixed-range profile. Ranges are stored as bar *times*, so a rewind that drops
  // their bars doesn't corrupt them — they simply stop drawing until the replay
  // reaches them again.
  const [armed, setArmed] = useState(false);
  const [ranges, setRanges] = useState<RangeSel[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const armedRef = useRef(false);
  const rangesRef = useRef<RangeSel[]>([]);
  const selectedRef = useRef<number | null>(null);
  const nextIdRef = useRef(1);
  const armApplyRef = useRef<((a: boolean) => void) | null>(null);
  const paintRef = useRef<(() => void) | null>(null);
  const paintDevRef = useRef<(() => void) | null>(null);
  /** Repaint the volume shelves; the flag resets the tracker, for when the tape
   *  underneath them has been replaced or rewound. */
  const paintShelvesRef = useRef<((reset: boolean) => void) | null>(null);

  // Ruler / measure tool.
  const [rulerArmed, setRulerArmed] = useState(false);
  const rulerArmedRef = useRef(false);
  const rulerApplyRef = useRef<((a: boolean) => void) | null>(null);
  // Returns whether it actually dismissed something, so the Escape handler can
  // tell a consumed key from an ignored one.
  const rulerClearRef = useRef<() => boolean>(() => false);

  // Anchored-VWAP tool. Unlike CandlestickChart's, the anchor lives in the engine
  // — so it keeps developing as the replay runs instead of being a fixed picture.
  const [avwapArmed, setAvwapArmed] = useState(false);
  const [avwapAnchor, setAvwapAnchor] = useState<number | null>(null);
  const avwapArmedRef = useRef(false);
  const avwapApplyRef = useRef<((a: boolean) => void) | null>(null);
  // Mirrored so `persistDrawings` (called from pointer handlers, which never
  // see fresh state) reads the anchor as it stands, not as it rendered.
  const avwapAnchorRef = useRef<number | null>(null);

  // Horizontal price lines — the ━ tool. Drawn as native price lines (an axis
  // label and a full-pane rule for free); the ref array is the truth and the
  // primitive map inside the build effect is its rendering. Same ref/state
  // split as the fixed ranges, for the same reason: a drag repaints without a
  // React render, the toolbar only re-renders when a drag settles.
  const [hlineArmed, setHlineArmed] = useState(false);
  const hlineArmedRef = useRef(false);
  const hlineApplyRef = useRef<((a: boolean) => void) | null>(null);
  const [hlines, setHlines] = useState<HLine[]>([]);
  const hlinesRef = useRef<HLine[]>([]);
  const [selectedHline, setSelectedHline] = useState<number | null>(null);
  const selectedHlineRef = useRef<number | null>(null);
  const nextHlineIdRef = useRef(1);
  const paintHlinesRef = useRef<(() => void) | null>(null);
  // A line the tape just crossed, flashed over the chart while the cue plays.
  const [alertFlash, setAlertFlash] = useState<{ price: number; key: number } | null>(null);
  useEffect(() => {
    if (!alertFlash) return;
    const t = window.setTimeout(() => setAlertFlash(null), 4000);
    return () => window.clearTimeout(t);
  }, [alertFlash]);

  // Which session the drawings above belong to, for the store. Mirrored for
  // the imperative handle, which is where a restore happens.
  const drawingsKeyRef = useRef(drawingsKey);
  drawingsKeyRef.current = drawingsKey;
  const persistDrawings = () => {
    const key = drawingsKeyRef.current;
    if (!key) return;
    saveDrawings(key, {
      ranges: rangesRef.current.map((r) => ({ from: r.from, to: r.to, live: r.live })),
      anchor: avwapAnchorRef.current,
      hlines: hlinesRef.current.map((l) => ({ price: l.price, armed: l.armed })),
    });
  };
  const persistRef = useRef(persistDrawings);
  persistRef.current = persistDrawings;

  // Order placement is a held modifier rather than a tool: hold Space and click
  // a price. Nothing to arm and nothing to leave, which is what you want when
  // the decision to place is made in the second the tape gives you — the chart
  // trader's gesture, not a drawing tool's.
  const [spaceHeld, setSpaceHeld] = useState(false);
  const spaceRef = useRef(false);
  const spaceApplyRef = useRef<((on: boolean) => void) | null>(null);
  const canOrderRef = useRef(canPlaceOrders);
  canOrderRef.current = canPlaceOrders;
  const setSpace = (on: boolean) => {
    if (on === spaceRef.current || (on && !canOrderRef.current)) return;
    spaceRef.current = on;
    setSpaceHeld(on);
    spaceApplyRef.current?.(on);
  };
  const setSpaceRef = useRef(setSpace);
  setSpaceRef.current = setSpace;
  // Whether the pointer is over the chart. Space only becomes the placing
  // modifier when it is — otherwise pressing it after clicking Play would just
  // re-trigger that button, which is what a focused button does with Space and
  // is the right thing everywhere except here.
  const overRef = useRef(false);

  // The same gesture for a device with no keyboard and no second mouse button:
  // arm the tool, pick which channel, tap a price. It is a worse gesture than
  // Space+click — two taps of setup instead of none — so it does not replace it,
  // it sits beside it. Whichever hardware you have, one of them works.
  //
  // It disarms itself after placing, which Space deliberately doesn't. On a
  // mouse the modifier is held, so letting go *is* the disarm and there is never
  // a moment where the chart is quietly armed; a tool has no such moment, and a
  // chart left armed on a touchscreen turns the next stray tap into an order.
  const [orderArmed, setOrderArmed] = useState(false);
  const orderArmedRef = useRef(false);
  const orderApplyRef = useRef<((on: boolean) => void) | null>(null);
  // Which of the two channels a tap means. Named for the mouse buttons rather
  // than for limit/stop on purpose — see onPlaceOrder: what a click *means* is
  // the page's decision, and the mapping flips across the market.
  const [orderSide, setOrderSide] = useState<"left" | "right">("left");
  const orderSideRef = useRef<"left" | "right">("left");
  orderSideRef.current = orderSide;

  // One drag/click tool owns the pointer at a time.
  const armRulerRef = useRef<(v: boolean) => void>(() => {});
  const armAvwapRef = useRef<(v: boolean) => void>(() => {});
  const armHlineRef = useRef<(v: boolean) => void>(() => {});
  const armOrderRef = useRef<(v: boolean) => void>(() => {});
  const arm = (v: boolean) => {
    if (v) {
      armRulerRef.current(false);
      armAvwapRef.current(false);
      armHlineRef.current(false);
      armOrderRef.current(false);
    }
    armedRef.current = v;
    setArmed(v);
    armApplyRef.current?.(v);
  };
  const armRuler = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armAvwapRef.current(false);
      armHlineRef.current(false);
      armOrderRef.current(false);
    }
    rulerArmedRef.current = v;
    setRulerArmed(v);
    rulerApplyRef.current?.(v);
  };
  armRulerRef.current = armRuler;
  const armAvwap = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
      armHlineRef.current(false);
      armOrderRef.current(false);
    }
    avwapArmedRef.current = v;
    setAvwapArmed(v);
    avwapApplyRef.current?.(v);
  };
  armAvwapRef.current = armAvwap;
  const armHline = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
      armAvwapRef.current(false);
      armOrderRef.current(false);
    }
    hlineArmedRef.current = v;
    setHlineArmed(v);
    hlineApplyRef.current?.(v);
  };
  armHlineRef.current = armHline;
  const armOrder = (v: boolean) => {
    if (v) {
      if (!canOrderRef.current) return;
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
      armAvwapRef.current(false);
      armHlineRef.current(false);
    }
    orderArmedRef.current = v;
    setOrderArmed(v);
    orderApplyRef.current?.(v);
  };
  armOrderRef.current = armOrder;
  const clearAvwap = () => {
    setAvwapAnchor(null);
    avwapAnchorRef.current = null;
    onAnchorRef.current?.(null);
    persistRef.current();
  };

  // Push whatever the hline refs now hold into both the chart and the toolbar,
  // and remember it — the mirror of `syncRanges` below.
  const syncHlines = () => {
    setHlines([...hlinesRef.current]);
    setSelectedHline(selectedHlineRef.current);
    paintHlinesRef.current?.();
    persistRef.current();
  };
  const syncHlinesRef = useRef(syncHlines);
  syncHlinesRef.current = syncHlines;
  const deleteSelectedHline = () => {
    if (selectedHlineRef.current == null) return;
    hlinesRef.current = hlinesRef.current.filter((l) => l.id !== selectedHlineRef.current);
    selectedHlineRef.current = null;
    syncHlines();
  };
  const clearHlines = () => {
    hlinesRef.current = [];
    selectedHlineRef.current = null;
    syncHlines();
  };

  // The alert half of a price line: the tape moved from one side of it to the
  // other since the last mark. One chime per arming — the line then dims and
  // stays, and dragging it is what re-arms it. The first mark of a session has
  // no "other side" yet, so it arms silently.
  const checkAlerts = (prev: number, v: number) => {
    if (!Number.isFinite(prev)) return;
    let hit: HLine | null = null;
    for (const l of hlinesRef.current) {
      if (!l.armed) continue;
      if ((prev < l.price && v >= l.price) || (prev > l.price && v <= l.price)) {
        l.armed = false;
        hit = l;
      }
    }
    if (hit) {
      playCue("alert");
      setAlertFlash({ price: hit.price, key: hit.id + Math.random() });
      syncHlinesRef.current();
    }
  };
  checkAlertsRef.current = checkAlerts;

  // Push whatever the refs now hold into both the chart and the toolbar. Called
  // once a drag settles, never mid-drag.
  const syncRanges = () => {
    setRanges([...rangesRef.current]);
    setSelected(selectedRef.current);
    paintRef.current?.();
    persistRef.current();
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
  const disarmRef = useRef<() => void>(() => {});
  const syncRef = useRef<() => void>(() => {});
  disarmRef.current = () => arm(false);
  syncRef.current = syncRanges;

  /** Arm one tool by name, or disarm everything (`null`). The five `arm*`
   *  functions above already make themselves mutually exclusive, so this is a
   *  dispatch and not a state machine — and disarming is "turn each of them
   *  off", which is exactly what Escape does. */
  const armTool = (id: ChartToolId | null) => {
    if (id !== "order") armOrder(false);
    if (id !== "vp") arm(false);
    if (id !== "ruler") armRuler(false);
    if (id !== "avwap") armAvwap(false);
    if (id !== "hline") armHline(false);
    if (id === "order") armOrder(true);
    else if (id === "vp") arm(true);
    else if (id === "ruler") armRuler(true);
    else if (id === "avwap") armAvwap(true);
    else if (id === "hline") armHline(true);
  };
  /** Whichever drawing is selected — one Del, whatever it is pointing at. */
  const deleteSelectedAny = () => {
    if (selected != null) deleteSelected();
    if (selectedHline != null) deleteSelectedHline();
  };

  // What this pane's tools are doing, published to whoever draws the rail. An
  // effect rather than a call inside each `arm*` so it cannot go out of step:
  // it is derived from the rendered state, so every path that changes a tool —
  // a rail click, a key, Escape, a drawing being deleted, a session landing —
  // reports the same way.
  const onToolsRef = useRef(onToolsChange);
  onToolsRef.current = onToolsChange;
  const toolsArmed: ChartToolId | null = orderArmed
    ? "order"
    : armed
      ? "vp"
      : rulerArmed
        ? "ruler"
        : avwapArmed
          ? "avwap"
          : hlineArmed
            ? "hline"
            : null;
  const drawingCount = ranges.length + hlines.length;
  useEffect(() => {
    onToolsRef.current?.({
      armed: toolsArmed,
      canOrder: canPlaceOrders,
      hasAvwap: avwapAnchor != null,
      hasRangeSel: selected != null,
      hasHlineSel: selectedHline != null,
      drawings: drawingCount,
    });
  }, [toolsArmed, canPlaceOrders, avwapAnchor, selected, selectedHline, drawingCount]);

  useEffect(() => {
    mountChart(paneId);
    return () => unmountChart(paneId);
  }, [paneId]);

  // Membership of the link, kept in the module the chart handlers read. Not part
  // of `joinLink` because it changes with a click and the registration doesn't.
  useEffect(() => {
    setPaneLinked(paneId, linked);
  }, [paneId, linked]);

  useEffect(() => {
    // Whether a key belongs to whatever the user is typing into rather than to
    // the chart.
    const busy = () => {
      const el = document.activeElement;
      return (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement
      );
    };
    const onKey = (e: KeyboardEvent) => {
      // Not this pane's keypress. Two ways it can be: this pane holds the
      // keyboard (it was the last one pressed), or the pointer is on it right
      // now. The second clause is what makes Space+click work on a pane you have
      // only hovered — and, more importantly, what keeps Escape honest: the rail
      // arms the *pressed* pane, so an Escape that only ever reached the hovered
      // one would leave a tool armed with nothing on screen able to cancel it.
      // With one chart on the page both are always true, so nothing about the
      // single-chart behaviour changed.
      if (!hasChartFocus(paneId) && !overRef.current) return;
      if (e.key === "Escape") {
        const armed =
          armedRef.current ||
          rulerArmedRef.current ||
          avwapArmedRef.current ||
          hlineArmedRef.current ||
          orderArmedRef.current;
        if (armedRef.current) arm(false);
        if (rulerArmedRef.current) armRulerRef.current(false);
        if (avwapArmedRef.current) armAvwapRef.current(false);
        if (hlineArmedRef.current) armHlineRef.current(false);
        if (orderArmedRef.current) armOrderRef.current(false);
        const dismissed = rulerClearRef.current(); // Esc also dismisses a finished measurement
        const hadPlus = plusRef.current != null;
        if (hadPlus) closePlusRef.current();
        // Mark the key spoken for when it actually cancelled something, so a
        // listener further out (the page's fullscreen exit) can stand down —
        // backing out of a tool shouldn't also tear down the whole view.
        if (armed || dismissed || hadPlus) e.preventDefault();
      }
      if (e.code === "Space" && overRef.current && !busy()) {
        // Space is the browser's page-scroll key (and a focused button's
        // trigger); here it is a held modifier, so take it before either. Auto-
        // repeat re-fires this while it's down — setSpace ignores the repeats.
        e.preventDefault();
        setSpaceRef.current(true);
      }
      // Don't hijack Delete while the user is typing somewhere on the page.
      // Whichever kind of object is selected goes — the two selections are
      // mutually exclusive (selecting one clears the other on the way in).
      if ((e.key === "Delete" || e.key === "Backspace") && !busy()) {
        if (selectedRef.current != null) {
          e.preventDefault();
          deleteSelected();
        } else if (selectedHlineRef.current != null) {
          e.preventDefault();
          deleteSelectedHline();
        }
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceRef.current(false);
    };
    // A key held while the window loses focus never delivers its keyup, and a
    // modifier stuck down would silently turn every later click into an order.
    const onBlur = () => setSpaceRef.current(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    applyRef.current?.(vis);
    // The shelf layer is painted from the frame loop rather than by `apply`, so
    // on a paused replay a toggle of either of its two switches would otherwise
    // not land until the tape moved again.
    paintShelvesRef.current?.(false);
  }, [vis]);

  // Keep the ＋ on its price. lightweight-charts publishes no price-scale change
  // event, and the replay moves the scale every frame anyway, so this rides the
  // same clock the chart does. It only runs while something is summoned, and it
  // writes straight at the DOM — a React render per frame for a button that has
  // not changed would be the one expensive thing on the page.
  //
  // Also republishes the price axis' width, which the ＋ sits on and the menu
  // hangs off: it changes with the digits in the scale, not just with layout.
  useEffect(() => {
    if (plusPrice == null) return;
    let raf = 0;
    const paint = () => {
      raf = requestAnimationFrame(paint);
      const el = anchorRef.current;
      const s = candleRef.current;
      const c = chartRef.current;
      const host = elRef.current;
      if (!el || !s || !c || !host) return;
      const y = s.priceToCoordinate(plusPrice);
      // A price scrolled off the scale still has a menu attached to it, so the
      // anchor is clamped into view rather than hidden — the order it is about
      // to place is at the price, not at the pixel.
      // Only the undocked menu rides with the anchor and so needs the taller
      // margin; a docked one is at the foot of the chart and this row is back to
      // being just the ＋.
      const m = menuOpenRef.current && !DOCK_MENU ? MENU_MARGIN : ANCHOR_MARGIN;
      const h = host.clientHeight;
      const lo = Math.min(m, h / 2);
      const v = Math.max(lo, Math.min(h - lo, y ?? h / 2));
      // Centred on the price, not hung below it — with the menu open that centres
      // the menu, which is what the margin above was clamping for.
      el.style.transform = `translateY(calc(${v}px - 50%))`;
      el.style.setProperty("--axis-w", `${c.priceScale("right").width()}px`);
    };
    paint();
    return () => cancelAnimationFrame(raf);
  }, [plusPrice]);

  // Nothing to place into (no tape yet, or the replay hasn't started): put the
  // ticket away and drop any armed modifier, rather than leave a gesture up that
  // the next click would land on nothing.
  useEffect(() => {
    if (!canPlaceOrders) {
      closePlusRef.current();
      setSpaceRef.current(false);
      armOrderRef.current(false);
    }
  }, [canPlaceOrders]);

  // Bridge from the imperative handle into the build effect's closures — the
  // layers it repaints are all created (and destroyed) in there.
  const hooksRef = useRef<{
    reprofile: () => void;
    paint: () => void;
    syncIb: () => void;
    remakeRuler: (tape: Tape | null) => void;
    clearRuler: () => void;
    /** Push a new indicator ink onto every series built in there. The canvas
     *  primitives need no such call — they read the active ink each frame — but
     *  a lightweight-charts series carries its colour in its options, so the
     *  light↔dark crossing has to be handed to them one applyOptions at a time.
     *  Nothing here touches data or ranges: a recolour must not cost the replay
     *  its position (which is also why this effect never re-runs). */
    relight: () => void;
  } | null>(null);

  useEffect(() => {
    if (!elRef.current) return;
    // Read, not subscribed to: appearance is applied live by its own effect
    // below, and a rebuild here would throw away the range being watched.
    const surf = chartSurfaces[appearanceRef.current.surface];
    const sch = candleColors(appearanceRef.current);
    // Every indicator hue on this chart, in the cut this surface wants. Read
    // once here and again in `relight` below — the series carry their colour in
    // options, so unlike the canvas primitives they have to be told when it
    // changes.
    let hues = chartInk(appearanceRef.current.surface);
    const chart = createChart(elRef.current, {
      // The library watches the container itself. Hand-rolling that watch is the
      // obvious thing and it does not work: resizing the chart re-lays-out the
      // element being observed, so Chrome treats it as a ResizeObserver feedback
      // loop and drops the *next* notification — the chart then tracks every
      // other resize and sits at a stale size in between. width/height below are
      // only the fallback the library uses if it can't observe at all.
      autoSize: true,
      width: elRef.current.clientWidth,
      height: elRef.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: surf.bg },
        textColor: surf.text,
        fontFamily: "Inter, sans-serif",
        fontSize: 9,
      },
      grid: { vertLines: { color: surf.grid }, horzLines: { color: surf.grid } },
      rightPriceScale: { borderColor: surf.grid },
      timeScale: {
        borderColor: surf.grid,
        timeVisible: true,
        secondsVisible: false,
        // The default, set explicitly because the replay's follow behaviour now
        // rests entirely on it: the range shifts with new bars only while the
        // last bar is on screen, so panning away parks the view where you left
        // it (see applyStep).
        shiftVisibleRangeOnNewBar: true,
      },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: sch.up,
      downColor: sch.down,
      wickUpColor: sch.up,
      wickDownColor: sch.down,
      borderVisible: false,
    });
    candleRef.current = candle;

    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volRef.current = vol;

    // The picker's studies, on this chart. Built here rather than in an effect of
    // its own so that a remount — StrictMode, or a second pane appearing — comes
    // up with the studies already on it; the specs are read off the ref, which is
    // whatever the page last handed over.
    studyLayerRef.current = new StudyLayer(chart, candle, setStudyReport);
    studyLayerRef.current.setSpecs(studySpecsRef.current);

    // One anchor = 5 lines (mid, ±1σ, ±2σ) plus the shaded ±1σ→±2σ fill, exactly
    // as the journal charts draw them (CandlestickChart.addVwap): same hues, same
    // dashed envelope, same wash — so a band read here reads the same there.
    //
    // The envelope is drawn, not fitted, and `context: true` extends that to the
    // mid as well. Both are the call the Modern VWAP rings already make below,
    // and the measurement behind them is docs/research/chart-price-scale-occupancy.md:
    // with all twenty σ lines and every mid voting, the price scale ran 3–10× the
    // traded range and the candles took a median 24% of the pane — at the chart's
    // own RIBBON threshold, by default, on 5 of 8 random sittings. The envelope
    // was worth about half of that and the weekly mid most of the rest.
    //
    // A demoted line still draws at its true price. It leaves the pane instead of
    // holding the pane open across the gap, and `syncEdges` names it at the edge
    // while it is gone.
    const mkBand = (
      hue: { middle: string; band1: string; band2: string; fill: string },
      opts?: { context?: boolean },
    ): Anchor => {
      const fitted = (key: BandKey) => key === "mid" && !opts?.context;
      const line = (color: string, key: BandKey) =>
        chart.addSeries(LineSeries, {
          color,
          lineWidth: key === "mid" ? 2 : 1,
          lineStyle: key === "mid" ? LineStyle.Solid : LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          ...(fitted(key) ? {} : { autoscaleInfoProvider: () => null }),
        });
      const band = new VwapBandPrimitive([], hue.fill);
      candle.attachPrimitive(band as any);
      return {
        lines: {
          mid: line(hue.middle, "mid"),
          u1: line(hue.band1, "u1"),
          l1: line(hue.band1, "l1"),
          u2: line(hue.band2, "u2"),
          l2: line(hue.band2, "l2"),
        } as Record<BandKey, ISeriesApi<"Line">>,
        band,
        pts: [],
      };
    };
    gRef.current = mkBand(hues.vwap.globex);
    nRef.current = mkBand(hues.vwap.ny);
    // The weekly anchor, in the same orange the strategy charts draw it in. It
    // is a *context* band here as it is there — nothing the replay does is
    // measured against it — and it only ever has points when the session shipped
    // a seed the week could be honestly built from.
    //
    // Which is why it is the one anchor whose mid is demoted too: a week's VWAP
    // can sit a long way under the day, and it was holding the scale open across
    // the whole gap to show a line nothing here reads. On 2026-08-05 its mid was
    // at 29,050 with price at 29,915 and it alone took the scale from 260 points
    // to 1078 — the candles from 69% of the pane down to 17%.
    wkRef.current = mkBand(hues.vwap.weekly, { context: true });
    // The ⚓ band is built empty and stays empty until the user anchors — no
    // create/destroy dance, the streaming path just starts finding points in it.
    aRef.current = mkBand(hues.vwap.anchored);

    // Modern VWAP: seven lines, the ±1σ→±2σ wash under them and the trigger
    // marks, all built by the shared layer (./modernVwapLayer) — the journal
    // charts mount the same one. Built empty and hidden; `apply` below tells it
    // which of its two rows the user has on, and refreshMv feeds it.
    mvLayerRef.current = createModernVwapLayer(
      chart,
      candle,
      () => mvSourceRef.current(),
      onMvData,
    );

    // And the Zeiierman line beside it: one series and its anchor flags. Built
    // empty and hidden, same as the layer above.
    dsvLayerRef.current = createDynamicSwingVwapLayer(
      chart,
      candle,
      () => dsvSourceRef.current(),
      onDsvData,
    );

    // Developing value areas, one per anchor: VAH and VAL solid (they are the
    // levels the rules actually test against), POC dashed between them, each in
    // its anchor's colour. Deliberately not shaded bands — the VWAP envelope
    // already owns that visual, and stacking fills where the two areas overlap
    // (the whole setup) would be unreadable.
    const mkProfile = (pal: { edge: string; poc: string }) => {
      const line = (color: string, key: ProfKey) =>
        chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          lineStyle: key === "poc" ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      return {
        vah: line(pal.edge, "vah"),
        val: line(pal.edge, "val"),
        poc: line(pal.poc, "poc"),
      } as Record<ProfKey, ISeriesApi<"Line">>;
    };
    gProfRef.current = mkProfile(hues.profile.globex);
    nProfRef.current = mkProfile(hues.profile.ny);
    wProfRef.current = mkProfile(hues.profile.weekly);

    // Initial Balance: high/low as flat segments from the bell to the live edge —
    // line series rather than price lines, because an IB doesn't exist over the
    // overnight candles and a full-pane line would draw it there. The extension
    // guides (±1×/1.5×/2× of the IB range) only appear once the hour completes,
    // and are excluded from autoscale: on a narrow-IB day they sit far outside
    // the traded range, and toggling them on must not crush the candles.
    const ibSeg = (color: string, guide: boolean) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: guide ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...(guide ? { autoscaleInfoProvider: () => null } : {}),
      });
    const ibSeries = [ibSeg(hues.ib.line, false), ibSeg(hues.ib.line, false)];
    const ibExtSeries = [1, 1.5, 2].flatMap(() => [
      ibSeg(hues.ib.ext, true),
      ibSeg(hues.ib.ext, true),
    ]);

    // --- bar-grid helpers (the tools all speak bar times) --------------------
    const nearestIdx = (t: number): number => {
      const bars = barsRef.current;
      const last = bars.length - 1;
      if (last < 0) return -1;
      if (t <= bars[0].time) return 0;
      if (t >= bars[last].time) return last;
      let lo = 0;
      let hi = last;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (bars[mid].time === t) return mid;
        if (bars[mid].time < t) lo = mid + 1;
        else hi = mid - 1;
      }
      return t - bars[hi].time <= bars[lo].time - t ? hi : lo;
    };
    const nearestBar = (t: number): number => {
      const i = nearestIdx(t);
      return i < 0 ? t : barsRef.current[i].time;
    };

    // Every profile on this chart — the viewport-following one and each
    // fixed-range one — is a slice of bars, and a bar knows the ticks it was
    // built from, so they all resolve to a scan of the real tape.
    //
    // The aggressor tag goes with them, so the rows carry delta as well as
    // volume — unconditionally, not only while the tint is on. It is two
    // compares per tick on a loop that is already dividing, and buying it
    // outright means the reading is there the moment the knob is turned, and
    // that "this tape has no delta" is a fact the legend can state rather than
    // an artefact of a switch being off.
    const profileFor = (i0: number, i1: number): VolumeProfile | null => {
      const bars = barsRef.current;
      const tape = tapeRef.current;
      if (!tape || i1 < i0 || i0 < 0 || i1 >= bars.length) return null;
      return computeTapeProfile(
        tape.level,
        tape.size,
        bars[i0].i0,
        bars[i1].i1,
        tape.tickSize,
        PROFILE_BIN,
        tape.side,
      );
    };

    // And how the delta lane on those profiles is read (lib/deltaFlow). Scale and
    // flags are arithmetic over the rows and cost nothing; a verdict needs the
    // tape again, cut by bar, which is a scan of the window on the order of the
    // one `profileFor` above already does.
    //
    // So verdicts are cached on the window, the bar count and the knobs, and the
    // viewport's `reprofile` runs every frame while the tape is playing. Without
    // the cache that scan would land on every frame; with it, it lands once a bar
    // — which is also the only honest cadence for it, since a verdict is a
    // statement about bars that have closed and the forming bar has not.
    let verdictKey = "";
    let verdictCache: Map<number, "initiative" | "absorbed"> | null = null;
    // The lane window's own accumulator, and the visit split's cache. The window
    // profile is a sliding span — its left edge advances a bar at a time — so
    // LiveTapeProfile's fast path carries the forming bar's ticks per frame and
    // pays one rebuild per bar when the edge moves. The visit split has no
    // incremental form (a returning bar re-files a row's whole history), so it
    // is computed once per bar window and reused across the frames inside it —
    // the lane lags the forming bar by at most that bar, which is the price of
    // not scanning the whole span sixty times a second.
    const laneWinLive = new LiveTapeProfile();
    let visitKey = "";
    let visitLane: LaneReading | null = null;
    const laneFor = (
      p: VolumeProfile | null,
      i0: number,
      i1: number,
      /** False for the dragged slices — see RangeProfileItem.lane. */
      allowVerdict = true,
      /** True only for the viewport lane. A fixed range's span *is* the window
       *  the reader drew, so the window knob never re-cuts it. */
      follow = false,
    ): LaneReading | null => {
      if (!p || !p.hasDelta) return null;
      const k = deltaLaneRef.current;
      const bars = barsRef.current;
      const tape = tapeRef.current;
      const win = follow ? k.window : "session";
      const spanOk = tape != null && i0 >= 0 && i1 >= i0 && i1 < bars.length;

      if (win === "visit" && spanOk) {
        const key = `${i0}:${i1}:${bars.length}:${p.rows.length}`;
        if (key === visitKey && visitLane) return visitLane;
        const split = splitVisits(p, bars.slice(i0, i1 + 1), (emit) => {
          for (let b = i0; b <= i1; b++) {
            for (let t = bars[b].i0; t <= bars[b].i1; t++) {
              const s = tape!.side[t];
              if (s !== SIDE_BUY && s !== SIDE_SELL) continue;
              emit(
                b - i0,
                tape!.level[t] * tape!.tickSize,
                s === SIDE_BUY ? tape!.size[t] : -tape!.size[t],
              );
            }
          }
        });
        visitKey = key;
        visitLane = readVisitLane(p, split);
        return visitLane;
      }

      // The timed windows re-source the lane; everything after this block reads
      // `src` and the `vi0..vi1` bars behind it, so flags, scales and verdicts
      // are all statements about the same stretch of tape.
      let src: LaneSource = p;
      let vi0 = i0;
      let vi1 = i1;
      const mins = LANE_WINDOW_MINUTES[win];
      if (mins != null && spanOk) {
        const cut = bars[i1].time - mins * 60;
        let wi0 = i0;
        while (wi0 < i1 && bars[wi0].time <= cut) wi0++;
        // A span already inside the window is its own window — the session lane.
        if (wi0 > i0) {
          const wp = laneWinLive.update(
            tape!.level,
            tape!.size,
            bars[wi0].i0,
            bars[i1].i1,
            tape!.tickSize,
            PROFILE_BIN,
            tape!.side,
          );
          const ws = windowedOnto(p, wp);
          if (ws) {
            src = ws;
            vi0 = wi0;
          }
        }
      }

      const lane = readLane(src, k.scale, k.flagSigma);
      if (!allowVerdict || !k.classify || lane.flagged.length === 0 || !tape || vi1 < vi0)
        return lane;
      if (vi0 < 0 || vi1 >= bars.length) return lane;

      const key = `${vi0}:${vi1}:${bars.length}:${k.scale}:${k.flagSigma}:${win}:${lane.flagged.join()}`;
      if (key === verdictKey && verdictCache) {
        lane.verdict = verdictCache;
        return lane;
      }

      const n = vi1 - vi0 + 1;
      const table = new Map<number, Float64Array>();
      for (const r of lane.flagged) table.set(r, new Float64Array(n));
      for (let b = vi0; b <= vi1; b++) {
        const col = b - vi0;
        for (let t = bars[b].i0; t <= bars[b].i1; t++) {
          const price = tape.level[t] * tape.tickSize;
          for (const r of lane.flagged) {
            const row = src.rows[r];
            if (price < row.low || price >= row.high) continue;
            // The same rule the profile's own binning applies: an untagged print
            // is volume that belonged to neither side, so it moves no delta.
            const s = tape.side[t];
            if (s === SIDE_BUY) table.get(r)![col] += tape.size[t];
            else if (s === SIDE_SELL) table.get(r)![col] -= tape.size[t];
            break;
          }
        }
      }
      verdictKey = key;
      verdictCache = classifyFlagged(
        src,
        lane.flagged,
        (r) => table.get(r)!,
        bars.slice(vi0, vi1 + 1),
      );
      lane.verdict = verdictCache;
      return lane;
    };

    // The two profiles that have to keep up with the playhead read through their
    // own accumulator instead: the forming bar grows by a handful of ticks per
    // frame, and re-scanning the whole span for them would be the difference
    // between "redraw once a bar" and "redraw once a frame" (see LiveTapeProfile).
    // Same numbers as profileFor — the fast path only changes what it costs.
    const vpLive = new LiveTapeProfile();
    const devLive = new LiveTapeProfile();
    //
    // `delta` is per accumulator and not per chart: the viewport profile offers
    // the tint and so buys the tag, the developing NY gutter draws its histogram
    // in one colour and so doesn't — and its span is the whole session, folded a
    // few ticks at a time on every frame.
    const liveProfileFor = (
      live: LiveTapeProfile,
      i0: number,
      i1: number,
      delta = false,
    ): VolumeProfile | null => {
      const bars = barsRef.current;
      const tape = tapeRef.current;
      if (!tape || i1 < i0 || i0 < 0 || i1 >= bars.length) return null;
      return live.update(
        tape.level,
        tape.size,
        bars[i0].i0,
        bars[i1].i1,
        tape.tickSize,
        PROFILE_BIN,
        delta ? tape.side : undefined,
      );
    };

    // Volume profile over whatever bars are on screen: the histogram itself is a
    // primitive (nothing native runs along the price axis), while POC/VAH/VAL are
    // price lines so they get axis labels and span the full pane for free.
    const vp = new VolumeProfilePrimitive(null);
    vp.showDelta = profileDeltaRef.current;
    candle.attachPrimitive(vp as any);
    vpRef.current = vp;

    const VP_LINES = [
      { key: "poc", color: palette.gold, style: LineStyle.Solid, title: "POC" },
      { key: "vah", color: palette.blue, style: LineStyle.Dashed, title: "VAH" },
      { key: "val", color: palette.blue, style: LineStyle.Dashed, title: "VAL" },
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

    // Re-profile on pan/zoom (and on the shift a new bar causes) so zooming into
    // a stretch profiles that stretch — and on every playback frame, so the
    // rightmost rows grow with the bar that is forming rather than jumping a
    // bar's worth of volume at its close. Logical range is fractional and can run
    // past the data on both ends, so clamp it back onto real bar indices.
    //
    // No from/to memo here: the span can be unchanged and the profile still
    // stale, because the last bar's tape keeps arriving. The accumulator answers
    // the "nothing new" case with the very profile it handed back last time, so
    // that comparison is the honest one to skip on.
    let lastVp: VolumeProfile | null = null;
    // The bar window `vp.profile` covers, for the lane's verdicts — a profile
    // knows its prices but not which bars made it.
    const vpWin = { i0: 0, i1: -1 };
    const reprofile = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const from = Math.max(0, Math.ceil(range.from));
      const to = Math.min(barsRef.current.length - 1, Math.floor(range.to));
      if (to < from) return;
      const p = liveProfileFor(vpLive, from, to, true);
      if (p === lastVp) return;
      lastVp = p;
      vpWin.i0 = from;
      vpWin.i1 = to;
      vp.setProfile(p);
      vp.setLane(laneFor(p, from, to, true, true));
      syncProfileLines(p);
    };
    // Re-read the lane without re-deriving anything under it: the rows and their
    // volumes are unchanged, only the question asked of the delta on them.
    applyLaneRef.current = () => {
      verdictKey = "";
      visitKey = "";
      vp.setLane(laneFor(vp.profile, vpWin.i0, vpWin.i1, true, true));
      paintRef.current?.();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      reprofile();
      // Has the ◎ got something to bring you back to? Setting the same boolean
      // is a no-op render, so this can ride a pan.
      syncOffTape();
    });


    // The price axis' width, republished for anything positioned against the
    // tape's right edge (the ◎ button). It changes with the digits in the scale,
    // not just with layout — and the time scale's own width is what moves when
    // it does, which is why this rides that event.
    const syncAxisW = () => {
      rootRef.current?.style.setProperty("--axis-w", `${chart.priceScale("right").width()}px`);
    };
    chart.timeScale().subscribeSizeChange(syncAxisW);
    syncAxisW();

    // --- crosshair readout: O H L C, the bar's change, and its volume --------
    // Written straight at the DOM, the way every per-frame path here is — a
    // React render per crosshair move would be a render per pixel. The values
    // come off `barsRef` by logical index rather than off `param.seriesData`,
    // because the volume isn't in the candle series and the forming bar's
    // numbers should be the engine's own.
    /** Print one bar's numbers. `i < 0` (or out of range) means "nothing under
     *  the pointer", and falls back to the newest bar rather than blanking.
     *
     *  THE FALLBACK IS NOT COSMETIC. This readout lives inside the legend now, so
     *  a hidden one is a missing *row* — and hovering a legend row takes the
     *  pointer off the canvas, which is exactly when the crosshair reports
     *  nothing. Blanking made the rows below jump up and down as you moved along
     *  them. Always drawing something also happens to be the better reading: the
     *  bar you are on is what you want when you are not pointing at another one.
     *
     *  A function rather than the body of the subscription because two other
     *  callers need it: the *link* (`setCrosshairPosition` deliberately skips the
     *  crosshair-move event — it passes `skipEvent` all the way down — so a pane
     *  told to follow would move a crosshair with no numbers beside it), and the
     *  playback, which has to keep the idle readout current as the forming bar
     *  moves under it. */
    const paintOhlc = (i: number) => {
      const el = ohlcRef.current;
      if (!el) return;
      const bars = barsRef.current;
      const at = i >= 0 && i < bars.length ? i : bars.length - 1;
      // The only blank state: no bars at all. Nothing has been laid out yet
      // either, so there is no row to shift.
      if (at < 0) {
        el.style.display = "none";
        return;
      }
      const b = bars[at];
      const chg = b.close - b.open;
      const cls = chg >= 0 ? "up" : "down";
      const f = (v: number) => v.toFixed(2);
      el.innerHTML =
        `<span>O <b class="${cls}">${f(b.open)}</b></span>` +
        `<span>H <b class="${cls}">${f(b.high)}</b></span>` +
        `<span>L <b class="${cls}">${f(b.low)}</b></span>` +
        `<span>C <b class="${cls}">${f(b.close)}</b></span>` +
        `<span class="${cls}">${chg >= 0 ? "+" : "−"}${f(Math.abs(chg))}</span>` +
        `<span>V <b>${b.volume.toLocaleString()}</b></span>`;
      el.style.display = "flex";
    };
    chart.subscribeCrosshairMove((param) => {
      const i = param.logical == null ? -1 : Math.round(param.logical);
      // Remembered so the playback can keep repainting the *right* bar: idle, it
      // is the newest one, and it has to keep up as that one forms.
      hoverIdxRef.current = i;
      // Every crosshair that reaches here has a pointer behind it, so every one
      // of them is this pane's to publish (the synthetic ones never fire this).
      if (param.point) {
        const px = candle.coordinateToPrice(param.point.y);
        if (px != null) publishCrosshair(paneId, (param.time as number) ?? null, px);
      } else {
        // The pointer left the tape without leaving the pane — off the end of
        // the data, or onto the axis. Take the followers' crosshairs down with
        // this one rather than leaving three panes reading a price nobody is
        // pointing at.
        publishCrosshair(paneId, null, 0);
      }
      paintOhlc(i);
    });

    // --- the link: one crosshair and one right edge across the grid ----------
    // See lib/paneLink for why it is the right *edge* and not the whole window.
    // Registered here rather than in an effect of its own because both closures
    // hold this chart, and this is the scope that knows when it is destroyed.
    const leaveLink = joinLink(paneId, {
      crosshair(time, price) {
        if (time == null) {
          chart.clearCrosshairPosition();
          hoverIdxRef.current = -1;
          paintOhlc(-1);
          return;
        }
        // Throws when the time isn't on this pane's bucketing at all; paneLink
        // swallows it, and a pane that can't follow simply doesn't.
        chart.setCrosshairPosition(price, time as Time, candle);
        // The source pane's time, snapped onto whichever of this pane's bars
        // holds that moment — which is the whole reading the link is for.
        hoverIdxRef.current = idxOfTime(barsRef.current, time);
        paintOhlc(hoverIdxRef.current);
      },
      rightEdge(to) {
        const cur = chart.timeScale().getVisibleRange();
        if (!cur) return;
        const span = (cur.to as number) - (cur.from as number);
        // Each pane keeps its own span — that is the whole design. A pane with
        // no span yet (one bar, or none) has nothing to keep, so leave it be.
        if (!(span > 0)) return;
        chart.timeScale().setVisibleRange({ from: (to - span) as Time, to: to as Time });
      },
    });
    chart.timeScale().subscribeVisibleTimeRangeChange((r) => {
      if (r) publishRightEdge(paneId, r.to as number);
    });
    paintOhlcRef.current = paintOhlc;

    // --- The composite over the context days, and the events on the tape -----
    // Attached before the fixed-range tool so a profile you drew sits over them:
    // these two are the standing backdrop, that one is the question you are
    // asking right now.
    const compPrim = compPrimRef.current!;
    candle.attachPrimitive(compPrim as any);

    const devPrim = devPrimRef.current!;
    candle.attachPrimitive(devPrim as any);

    // The coarser bar over this one. Attached with the standing backdrop because
    // that is what it is — it owns both z-orders internally, so "above the
    // candles" is a setting rather than an attach order (ExternalChartPrimitive).
    const extPrim = extPrimRef.current!;
    extPrim.setParams(extParamsRef.current);
    candle.attachPrimitive(extPrim as any);

    // The HTF trend: owns both z-orders (wash under, lines over).
    const htfPrim = htfPrimRef.current!;
    htfPrim.setParams(htfParamsRef.current);
    candle.attachPrimitive(htfPrim as any);

    // Under the candles always — the zones are the background a bar is read
    // against, so this one has no z-order setting to own.
    const rzPrim = rzPrimRef.current!;
    rzPrim.setParams(rzParamsRef.current);
    candle.attachPrimitive(rzPrim as any);

    const econPrim = econPrimRef.current!;
    candle.attachPrimitive(econPrim as any);

    const gexPrim = gexPrimRef.current!;
    gexPrim.setParams(gexParamsRef.current.walls, gexParamsRef.current.flip);
    candle.attachPrimitive(gexPrim as any);

    const evPrim = evPrimRef.current!;
    candle.attachPrimitive(evPrim as any);

    // --- Fixed-range profile: drag across the chart to profile just that slice ---
    const rangePrim = new RangeProfilePrimitive();
    rangePrim.setShowDelta(profileDeltaRef.current);
    candle.attachPrimitive(rangePrim as any);
    rangePrimRef.current = rangePrim;

    // --- Closed trades: entry arrow → exit dot, and what the leg paid --------
    // Attached before the two live overlays so a mark from an hour ago can never
    // be drawn over the position you are in now.
    // --- Big trades: the sweeps the tape had to work through the book --------
    // Attached first of the three mark layers, so it draws under them: what the
    // market did is context for what you did, never a cover over it.
    const bigPrim = bigPrimRef.current!;
    candle.attachPrimitive(bigPrim as any);
    bigPrim.setTrades(bigsRef.current, bigLotsRef.current);

    const tradesPrim = tradesPrimRef.current!;
    candle.attachPrimitive(tradesPrim as any);
    tradesPrim.setTrades(tradesRef.current, tapeRef.current?.tickSize);

    // --- Modern VWAP triggers: MR triangles, TC rings, anchor ticks ----------
    // Above the tape's own marks and below the position: a study layer is
    // context, and it is never what you are currently doing.
    mvLayerRef.current?.attachSignals();
    // The Zeiierman line's frozen segments and swing flags, likewise.
    dsvLayerRef.current?.attachOverlays();

    // --- Open position: entry / stop / target, zones, chips, axis labels ---
    // Drawn above everything else, and the only overlay whose lines can be
    // dragged (the mechanics are down in the pointer handlers).
    const posPrim = posPrimRef.current!;
    candle.attachPrimitive(posPrim as any);
    posPrim.setData(posRef.current);

    // --- Working orders: the resting limits, under the position overlay ------
    const ordPrim = ordPrimRef.current!;
    candle.attachPrimitive(ordPrim as any);
    ordPrim.setOrders(workingRef.current, tapeRef.current?.tickSize, chipPv());

    // --- Horizontal price lines: the ━ tool's rendering -----------------------
    // Native price lines rather than a primitive: the axis label and the
    // full-pane rule are exactly what the library's own lines are, and there is
    // nothing bar-shaped about a level. The map is reconciled against the ref
    // array — lines removed, moved, re-armed or selected all land here.
    const hlineObjs = new Map<number, IPriceLine>();
    const paintHlines = () => {
      const list = hlinesRef.current;
      const sel = selectedHlineRef.current;
      for (const [id, line] of hlineObjs) {
        if (!list.some((l) => l.id === id)) {
          candle.removePriceLine(line);
          hlineObjs.delete(id);
        }
      }
      for (const l of list) {
        const opts = {
          price: l.price,
          color: l.armed ? HLINE_COLOR : HLINE_DIM,
          lineWidth: (l.id === sel ? 2 : 1) as 1 | 2,
          lineStyle: l.armed ? LineStyle.Solid : LineStyle.Dashed,
          axisLabelVisible: true,
          title: "",
        };
        const existing = hlineObjs.get(l.id);
        if (existing) existing.applyOptions(opts);
        else hlineObjs.set(l.id, candle.createPriceLine(opts));
      }
    };
    paintHlinesRef.current = paintHlines;
    paintHlines();

    /** The line under the pointer, nearest first — the same hit-slop the other
     *  draggable levels use. */
    const hitHline = (y: number): number | null => {
      let best: number | null = null;
      let bestD = HANDLE_PX + 1;
      for (const l of hlinesRef.current) {
        const ly = candle.priceToCoordinate(l.price);
        if (ly == null) continue;
        const d = Math.abs(y - ly);
        if (d <= HANDLE_PX && d < bestD) {
          best = l.id;
          bestD = d;
        }
      }
      return best;
    };

    // --- Ruler: drag between two points to measure the move between them ---
    // Recreated when the tape changes, because the tick size and $/point it
    // reports in are constructor arguments.
    let ruler = new RulerPrimitive(tapeRef.current?.tickSize, chipPv());
    rulerRef.current = ruler;
    candle.attachPrimitive(ruler as any);
    const remakeRuler = (tape: Tape | null) => {
      candle.detachPrimitive(ruler as any);
      ruler = new RulerPrimitive(tape?.tickSize, chipPv());
      rulerRef.current = ruler;
      candle.attachPrimitive(ruler as any);
    };

    // How one selection's value area *developed* — POC/VAH/VAL at every bar close
    // inside it, which is what the developing VP gives the session and what this
    // gives an arbitrary slice of it.
    //
    // Through the engine's own LevelHist, so these are the same walk (and the
    // same tick grid) as every other value area on the chart rather than a second
    // port of it. Cost is the slice's ticks plus one O(levels) scan per bar, paid
    // on two occasions only:
    //
    //  - not mid-drag. A resize changes the span on every pointer move, and
    //    re-walking a wide selection at pointer rate is the one way this gets
    //    expensive. The histogram still tracks the drag; the trace comes back on
    //    release.
    //  - not when the span is unchanged. `paint` runs on pans, recolours and
    //    selection changes too, and none of those move a level.
    // A latched range grows by one bar at a time and never moves its left edge,
    // so that case extends the histogram it already built instead of re-walking
    // the slice — the difference between paying the new bar's ticks and paying
    // the whole selection's, once a bar, for the rest of the session. Any other
    // change (a resize, a move, a rewind) starts over.
    const traceCache = new Map<
      number,
      { i0: number; end: number; hist: LevelHist; path: ProfilePt[] }
    >();
    const traceFor = (id: number, i0: number, i1: number): ProfilePt[] => {
      const bars = barsRef.current;
      // Stop at the last *closed* bar. Partly because a value area is the state
      // of a closed bar — the cadence journal.sim.profile and the developing VP
      // both keep — and partly because the kept histogram would otherwise absorb
      // a forming bar's first few ticks and never see the rest of them. The
      // forming bar is not missing from the reading: the headline POC/VAH/VAL
      // are built from the whole selection, this bar included.
      const end = Math.min(i1, bars.length - 2);
      const hit = traceCache.get(id);
      if (hit && hit.i0 === i0 && hit.end === end) return hit.path;
      // The span moved under a live drag: leave the trace off until the drop
      // rather than re-walking at pointer rate.
      if (drag) return [];
      const tape = tapeRef.current;
      if (!tape || i0 < 0 || end < i0) return [];
      const grew = hit != null && hit.i0 === i0 && end > hit.end;
      const hist = grew ? hit!.hist : new LevelHist();
      const path = grew ? hit!.path : [];
      const tick = tape.tickSize;
      for (let b = grew ? hit!.end + 1 : i0; b <= end; b++) {
        const bar = bars[b];
        for (let i = bar.i0; i <= bar.i1; i++) hist.add(tape.level[i], tape.size[i]);
        const l = hist.levels();
        if (l) {
          path.push({ time: bar.time, poc: l.poc * tick, vah: l.vah * tick, val: l.val * tick });
        }
      }
      traceCache.set(id, { i0, end, hist, path });
      return path;
    };

    // Repaint every fixed-range profile from the ref. A range whose span isn't
    // fully on the chart draws nothing rather than being clamped onto the loaded
    // edge — on a rewind its bars genuinely haven't happened yet, and a profile
    // silently squashed onto the live edge would be a wrong number, not a missing
    // one. It comes back untouched when the replay reaches it again.
    const paint = () => {
      const bars = barsRef.current;
      const lastT = bars.length ? bars[bars.length - 1].time : -Infinity;
      const firstT = bars.length ? bars[0].time : Infinity;
      // A latched right edge is resolved here rather than written when a bar
      // arrives: this runs on the new bar *and* on a rewind, so one assignment
      // covers both directions, and `r.to` stays the single thing hit-testing
      // and persistence read.
      if (bars.length) for (const r of rangesRef.current) if (r.live) r.to = lastT;
      rangePrim.setData(
        rangesRef.current.map((r) => {
          const loaded = r.from >= firstT && r.to <= lastT;
          if (!loaded) return { id: r.id, from: r.from, to: r.to, live: r.live, profile: null };
          const i0 = nearestIdx(r.from);
          const i1 = nearestIdx(r.to);
          const p = profileFor(i0, i1);
          return {
            id: r.id,
            from: bars[i0].time,
            to: bars[i1].time,
            live: r.live,
            profile: p,
            lane: laneFor(p, i0, i1, false),
            path: traceFor(r.id, i0, i1),
          };
        }),
        selectedRef.current,
      );
    };
    paintRef.current = paint;

    // The developing NY profile, repainted on every playback frame rather than
    // once a bar: it is the distribution the session is building right now, so a
    // row that only thickens at the bar's close reads as the profile trailing
    // the candles. The span is the first NY bar to the live edge, which is
    // exactly the stretch the NY value area is accumulated over — so the
    // histogram and the VAH/POC/VAL lines are two views of one distribution and
    // cannot disagree, on any frame.
    let lastDev: VolumeProfile | null = null;
    let lastNodes: ProfileNodes | null = null;
    let lastProm = -1;
    // Shelf state that outlives a frame — see `paintShelves` below for why it
    // has to. Reset together, by the same call, so a rewound tape cannot leave
    // the tracker holding bands from a future it no longer has.
    const shelfPrim = new VolumeShelfPrimitive();
    candle.attachPrimitive(shelfPrim as any);
    // Rebuilt on every reset rather than once here, because the hold is a
    // *constructor* argument. Built once, the tracker went on enforcing whatever
    // "must hold for" was set to when the chart mounted: `reset()` empties its
    // bands but cannot change the gate they are measured against, so that one
    // knob silently did nothing until the page was reloaded while the other two
    // — read from `params` at paint time — worked. The journal chart never had
    // this because its tracker is built inside the walk.
    let shelfTracker = new ShelfTracker(2, shelfParamsRef.current.minHoldMin * 60);
    const shelfCols: ShelfColumn[] = [];
    let shelfLastEval = -Infinity;
    // What the primitive was last handed, so a frame that changes nothing costs
    // nothing. `paintShelves` runs on every frame of a playing replay, while a
    // reading can only move once every `stepSec` of tape — without this the
    // in-between frames rebuilt the bar list, re-filtered and re-sorted the
    // boxes, and invalidated the chart, all to hand over the picture it already
    // had.
    let shelfDirty = true;
    let shelfOn = false;
    let shelfBoxesOn = false;
    let shelfShownField: ShelfField | null = null;
    const paintDev = () => {
      const bars = barsRef.current;
      const start = nyStartRef.current;
      if (!Number.isFinite(start) || !bars.length) {
        devPrim.setData(null);
        return;
      }
      const i0 = nearestIdx(start);
      const i1 = bars.length - 1;
      const profile = i0 >= 0 && i1 >= i0 ? liveProfileFor(devLive, i0, i1) : null;
      if (!profile) {
        devPrim.setData(null);
        return;
      }
      const va = nyVaRef.current;
      const prom = nodePromRef.current;
      // Re-read whenever the distribution moves rather than cached like the
      // composite's: this one changes with the tape, so the only thing to cache
      // against is the profile itself — which the accumulator hands back
      // unchanged when no tick has arrived (a paused replay, a repaint for some
      // other reason). It is one pass over a few hundred rows either way.
      if (profile !== lastDev || prom !== lastProm) {
        lastDev = profile;
        lastProm = prom;
        lastNodes = prom > 0 ? profileNodes(profile, prom) : null;
      }
      devPrim.setData({
        profile,
        va: va && { poc: va.poc, vah: va.vah, val: va.val },
        nodes: lastNodes,
        from: bars[i0].time,
      });
    };

    // --- The external-period candles ----------------------------------------
    //
    // Regrouping is one pass over the bar list, which on a full session is a
    // few thousand cheap iterations — small, but this runs on every frame the
    // tape advances, and a per-frame walk of every bar is the exact cost the
    // draw gate was added to stop paying. Only the newest bar can change without
    // the count changing, so a signature over the count and that bar is enough
    // to skip the walk on the frames that changed neither.
    let lastExtSig = "";
    const paintExt = () => {
      const bars = barsRef.current;
      const period = extParamsRef.current.period;
      const last = bars[bars.length - 1];
      const sig = last ? `${bars.length}|${period}|${last.time}|${last.high}|${last.low}|${last.close}` : "";
      if (sig === lastExtSig) return;
      lastExtSig = sig;
      const grouped: ExternalBar[] = groupExternalBars(bars, period);
      extPrim.setBars(grouped);
      // The legend's dimmed row: it grouped, or the period is not above the bar.
      // Only ever flipped on a real change, so this is not a per-frame setState.
      setExtGrouped(grouped.length > 0 || bars.length === 0);
    };

    // The zones. Gated harder than anything else on this chart, because it is
    // the most expensive painter here: one pass over every bar, and inside it a
    // pass over every live zone, so a session's worth is tens of thousands of
    // comparisons. It is also, like the studies, a statement about *closed*
    // bars — the pivot that makes a zone confirms `pivotSpan` bars late and can
    // never be about the bar being painted — so recomputing per tick would buy
    // nothing at all. Same signature gate as the overlay above, plus a `force`
    // for a knob change, which moves the zones without moving a bar.
    let lastRzSig = "";
    const paintRz = (force = false) => {
      // Read here rather than watched by an effect, the same rule the shelves
      // follow: visibility decides whether the walk happens at all, so it has to
      // be checked wherever the frame came from. A layer that is off costs
      // nothing — which matters more here than for any other painter, since this
      // one is a full pass over the session and it is off by default.
      if (!visRef.current.rankedZones) {
        lastRzSig = "";
        return;
      }
      const bars = barsRef.current;
      const last = bars[bars.length - 1];
      const sig = last ? `${bars.length}|${last.time}` : "";
      if (!force && sig === lastRzSig) return;
      lastRzSig = sig;
      // The prints go with the bars so the flow terms exist whichever ranking
      // is on: they are what the flow ranking sorts on, and reading them costs
      // a scan of each pivot window and each touching bar, not of the session.
      const tape = tapeRef.current;
      const zoneTape: ZoneTape | null = tape
        ? {
            level: tape.level,
            size: tape.size,
            side: tape.side,
            tickSize: tape.tickSize,
            span: (b) => {
              const bar = bars[b];
              return bar && bar.i0 >= 0 && bar.i1 >= bar.i0 ? [bar.i0, bar.i1] : null;
            },
          }
        : null;
      const data: RankedZonesData | null = last
        ? computeRankedZones(bars, rzParamsRef.current, tape?.tickSize ?? 0.25, zoneTape)
        : null;
      rzPrim.setData(data, last?.time ?? 0);
      setRzCount(data ? data.zones.filter((z) => z.visible).length : 0);
      setRzFlow(data ? data.hasFlow : true);
    };
    paintRzRef.current = paintRz;

    // --- Economic releases ----------------------------------------------------
    // The bars are handed over every frame (a reference, so free); the fetch only
    // when the span of wall-clock days they cover changes. A span reaching today
    // also keys on a 10-minute bucket, so a live chart picks up the actual once
    // FF posts it. A failed fetch keeps its key: retrying per frame would be a
    // request per print against a scraper.
    let econKey = "";
    const paintEcon = (force = false) => {
      const bars = barsRef.current;
      econPrim.setBars(bars);
      if (!visRef.current.econEvents || bars.length === 0) return;
      const d0 = Math.floor(bars[0].time / 86400) - 1;
      const d1 = Math.floor(bars[bars.length - 1].time / 86400) + 1;
      const today = Math.floor(Date.now() / 86400000);
      const floor = econParamsRef.current.floor;
      const key =
        `${d0}|${d1}|${floor}|${tzRef.current}` +
        (d1 >= today ? `|${Math.floor(Date.now() / 600000)}` : "");
      if (!force && key === econKey) return;
      econKey = key;
      const iso = (d: number) => new Date(d * 86400000).toISOString().slice(0, 10);
      const impact = { high: "high", medium: "high,medium", low: "high,medium,low" }[floor];
      apiGet<{ events: EconEvent[] }>("/econ/events", {
        start: iso(d0),
        end: iso(d1),
        impact,
        tz: tzRef.current,
      })
        .then((r) => {
          if (econKey !== key) return;
          econPrim.setEvents(r.events);
          setEconCount(r.events.filter((e) => e.timed).length);
        })
        .catch(() => {});
    };
    paintEconRef.current = paintEcon;

    // --- Gamma levels ---------------------------------------------------------
    // Keyed on the span of *sessions* the bars cover (a bar at 18:00 belongs to
    // the next day's session, hence the +6h) and the contract. Books change once
    // a day, so nothing here needs a clock bucket; a failed fetch keeps its key
    // for the same reason the econ one does.
    let gexKey = "";
    let gexStepSeen: GexStep | null = null;
    // The legend reads the step in force at the last bar; checked every paint
    // (cheap — a handful of steps), set only when it changes.
    const syncGexStep = () => {
      const b = barsRef.current;
      const st = b.length ? gexPrim.stepAt(b[b.length - 1].time) : null;
      if (st !== gexStepSeen) {
        gexStepSeen = st;
        setGexLast(st);
      }
    };
    const paintGex = (force = false) => {
      const bars = barsRef.current;
      gexPrim.setBars(bars);
      syncGexStep();
      const sym = tapeContractRef.current;
      if (!visRef.current.gexLevels || bars.length === 0 || !sym) return;
      const sess = (t: number) => Math.floor((t + 6 * 3600) / 86400);
      const d1 = sess(bars[bars.length - 1].time);
      const d0 = Math.max(sess(bars[0].time), d1 - 30);
      const expiry = gexParamsRef.current.expiry;
      // A span reaching today keys on a 5-minute bucket too, so a live chart
      // picks up each intraday snapshot the collector banks.
      const today = Math.floor(Date.now() / 86400000);
      const key =
        `${d0}|${d1}|${sym}|${tzRef.current}|${expiry}` +
        (d1 >= today ? `|${Math.floor(Date.now() / 300000)}` : "");
      if (!force && key === gexKey) return;
      gexKey = key;
      const iso = (d: number) => new Date(d * 86400000).toISOString().slice(0, 10);
      apiGet<{ sessions: GexSession[] }>("/gex/levels", {
        symbol: sym,
        start: iso(d0),
        end: iso(d1),
        tz: tzRef.current,
        expiry,
      })
        .then((r) => {
          if (gexKey !== key) return;
          gexPrim.setSessions(r.sessions);
          syncGexStep();
        })
        .catch(() => {});
    };
    paintGexRef.current = paintGex;
    paintExtRef.current = paintExt;
    paintExt();

    // --- The HTF trend --------------------------------------------------------
    //
    // A frame's EMA and vote only move when one of its buckets closes, which
    // only happens when a new drawn bar opens — so the same signature gate as
    // the overlay above, keyed on the bar count and the newest bar's time, skips
    // every frame that only moved the forming bar. `force` is for a knob change.
    let lastHtfSig = "";
    let lastHtfNow = "";
    const paintHtf = (force = false) => {
      const bars = barsRef.current;
      const p = htfParamsRef.current;
      const last = bars[bars.length - 1];
      const sig = last ? `${bars.length}|${last.time}|${p.frames}|${p.length}` : "";
      if (!force && sig === lastHtfSig) return;
      lastHtfSig = sig;
      const trend = computeHtfTrend(bars, p);
      htfPrim.setData(bars, trend);
      const now = { warm: trend.warm || bars.length === 0, state: trend.combined[trend.combined.length - 1] ?? 0 };
      const key = `${now.warm}|${now.state}`;
      if (key !== lastHtfNow) {
        lastHtfNow = key;
        setHtfNow(now);
      }
    };
    paintHtfRef.current = paintHtf;
    paintHtf(true);

    // --- Volume shelves -----------------------------------------------------
    //
    // Incremental, unlike the journal chart's, which re-walks its whole session
    // whenever a knob moves. Here the tape advances, and re-walking every frame
    // would be quadratic in the session — so the tracker and the raster's columns
    // live across frames and each new reading is pushed onto them.
    //
    // That is also the honest shape for a replay: a shelf must appear when the
    // tape reaches the bar that earned it and not a moment before, which is what
    // stepping the same tracker the live chart steps gives for free. Rebuilding
    // from the whole session each frame would let a band that is about to form
    // show up early on a re-render.
    const paintShelves = (reset: boolean) => {
      const params = shelfParamsRef.current;
      if (reset) {
        shelfTracker = new ShelfTracker(2, params.minHoldMin * 60);
        shelfCols.length = 0;
        shelfLastEval = -Infinity;
        shelfDirty = true;
      }
      const bars = barsRef.current;
      // Visibility is read here rather than watched by an effect because it is
      // what the primitive is handed, so a change to either flag has to reach
      // `setData` however the frame arrived.
      const on = visRef.current.volumeShelf;
      const boxesOn = visRef.current.volumeShelfBoxes;
      const fld = shelfFieldRef.current;
      if (on !== shelfOn || boxesOn !== shelfBoxesOn || fld !== shelfShownField) {
        shelfDirty = true;
      }
      shelfOn = on;
      shelfBoxesOn = boxesOn;
      shelfShownField = fld;

      if (!on || !bars.length || !tapeRef.current) {
        if (!shelfDirty) return;
        shelfPrim.setData({
          columns: [], boxes: [], showBoxes: false, zMin: 0,
          field: "size", flowAvailable: false,
        });
        shelfDirty = false;
        return;
      }

      // A reset walks the bars already on screen before going incremental.
      //
      // Not an optimisation — without it a replay that opens part-way through a
      // session (a resumed sitting, a seek) shows one sliver of raster and no
      // boxes at all, because a box has to be *held* before it is reported and a
      // single reading has held for nothing. The history is right there in
      // `bars`; refusing to read it would make the layer's emptiness a statement
      // about when the chart mounted rather than about the market.
      // `shelfLastEval` still at its sentinel means nothing has been read yet —
      // the first paint of a chart that already has bars, which wants the same
      // backfill an explicit reset does and never announces itself as one.
      //
      // Bounded to this session: `bars` also holds the context days the composite
      // is built from, and a shelf is a reading about the auction in front of
      // you, not about last Tuesday's. Unbounded it walked ~4,300 minutes of
      // chart on a three-day load — 4,176 windowed profiles at every reset,
      // several megabytes of raster held for days that are off-screen, and boxes
      // drawn across sessions the trader never opened. Every other tape-reading
      // layer here slices on the same count (`cvdOscBars`, the vol ruler).
      const backfill = reset || shelfLastEval === -Infinity;
      const due = backfill || bars[bars.length - 1].time - shelfLastEval >= params.stepSec;
      if (!due && !shelfDirty) return;

      if (due) {
        const shelfBars = bars.map((b) => ({
          time: b.time,
          high: b.high,
          low: b.low,
        }));
        const tick = tapeRef.current.tickSize;
        const readAt = (i: number) => {
          const t = shelfBars[i].time;
          shelfLastEval = t;
          const j = windowStart(shelfBars, i, params.windowMin);
          const prof = profileFor(j, i);
          const win = shelfBars.slice(j, i + 1);
          const { shelves, reading } = detectShelves(prof, win, true, tick, params);
          shelfTracker.push(t, shelves, win);
          if (prof && reading) {
            shelfCols.push({
              time: t, rows: prof.rows, z: reading.z, flow: shelfFlow(prof) ?? undefined,
            });
          }
        };
        if (backfill) {
          const from = Math.min(histCountRef.current, shelfBars.length - 1);
          const own = shelfBars.slice(from);
          for (const k of evalBars(own, params.stepSec)) readAt(from + k);
        } else {
          readAt(bars.length - 1);
        }
      }
      shelfPrim.setData({
        columns: shelfCols,
        boxes: shelfTracker.boxes(),
        showBoxes: boxesOn,
        zMin: params.zMin,
        field: shelfFieldRef.current,
        flowAvailable: shelfCols.some((c) => c.flow != null),
      });
      shelfDirty = false;
    };
    paintDevRef.current = paintDev;
    paintShelvesRef.current = paintShelves;

    // --- IB overlay ---------------------------------------------------------
    // Redrawn whenever the box or the live edge moves (once per bar close, not
    // per frame): the segments have to keep reaching the right-hand edge as the
    // session runs.
    const syncIb = () => {
      const box = ibRef.current;
      const bars = barsRef.current;
      const blank = () => {
        for (const s of [...ibSeries, ...ibExtSeries]) s.setData([]);
      };
      if (!box || bars.length === 0) return blank();
      // Snap both endpoints onto the drawn grid: lightweight-charts unions the
      // time points of every series, so an off-grid stamp would open its own
      // empty column on the time scale.
      const a = nearestBar(box.start);
      const b = bars[bars.length - 1].time;
      const seg = (s: ISeriesApi<"Line">, from: number, price: number) =>
        s.setData(
          from >= b
            ? [{ time: b as Time, value: price }]
            : [
                { time: from as Time, value: price },
                { time: b as Time, value: price },
              ],
        );
      seg(ibSeries[0], a, box.high);
      seg(ibSeries[1], a, box.low);
      const range = box.high - box.low;
      const formed = nearestBar(box.formed);
      [1, 1.5, 2].forEach((m, k) => {
        // Guides are a statement about a *completed* hour: nothing to extend from
        // until the window closes.
        if (!box.complete) {
          ibExtSeries[k * 2].setData([]);
          ibExtSeries[k * 2 + 1].setData([]);
          return;
        }
        seg(ibExtSeries[k * 2], formed, box.high + m * range);
        seg(ibExtSeries[k * 2 + 1], formed, box.low - m * range);
      });
    };

    // --- pointer plumbing ---------------------------------------------------
    const host = elRef.current;
    const xOf = (e: MouseEvent | PointerEvent) => e.clientX - host.getBoundingClientRect().left;
    const yOf = (e: MouseEvent | PointerEvent) => e.clientY - host.getBoundingClientRect().top;
    const idxAtX = (x: number): number | null => {
      const last = barsRef.current.length - 1;
      if (last < 0) return null;
      const logical = chart.timeScale().coordinateToLogical(x);
      if (logical == null) return null;
      return Math.min(last, Math.max(0, Math.round(logical)));
    };
    // Ruler corners snap to the tick grid — a measurement in fractional ticks is
    // never what anyone wants.
    const priceAtY = (y: number): number | null => {
      // `yOf` measures against the whole chart element, but the candles own only
      // the first pane — below it sit the CVD pane and the time axis, and
      // `coordinateToPrice` extrapolates into both perfectly happily rather than
      // returning null. On a chart where a press places a working order (Space +
      // click, or the armed order tool) an extrapolated price is the worst answer
      // available, so a press that lands under the price pane is simply not a
      // price. Costs nothing when there is no second pane: the axis was already
      // the only thing down there.
      const priceH = chart.panes()[0]?.getHeight();
      if (priceH != null && y > priceH) return null;
      const p = candle.coordinateToPrice(y);
      if (p == null) return null;
      const tick = tapeRef.current?.tickSize;
      return tick ? Math.round(p / tick) * tick : p;
    };
    const measureOf = (i1: number, p1: number, i2: number, p2: number) => {
      const bars = barsRef.current;
      return {
        t1: bars[i1].time,
        p1,
        t2: bars[i2].time,
        p2,
        bars: Math.abs(i2 - i1),
        seconds: Math.abs(bars[i2].time - bars[i1].time),
      };
    };

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
    // something else: while a tool is armed, or while the pointer is over a
    // profile the user could grab. Only the *pressed-drag* gesture conflicts —
    // blanket `handleScroll: false` would also deaden the mouse wheel.
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
    const anyArmed = () =>
      armedRef.current ||
      rulerArmedRef.current ||
      avwapArmedRef.current ||
      hlineArmedRef.current ||
      orderArmedRef.current ||
      spaceRef.current;
    const setCursor = (a: boolean) => {
      if (elRef.current) elRef.current.style.cursor = a ? "crosshair" : "";
    };

    // Drag state, in bar-index space rather than time: the bar grid has gaps
    // (weekends, overnight), so shifting a range by a time delta would smear it.
    let drag: { mode: DragMode; id: number; anchorIdx: number; from: number; to: number } | null =
      null;
    let downX = 0;
    let downY = 0;
    // Which pointer owns the gesture. A mouse only ever has one, but a second
    // finger landing mid-drag would otherwise yank the thing being dragged to
    // wherever it touched down.
    let pressId: number | null = null;
    const isPress = (e: PointerEvent) => pressId == null || e.pointerId === pressId;
    // The long-press countdown. Armed on a press that lands on bare tape and
    // dropped the moment that press turns out to be anything else — a pan, a
    // flick, a tap — so the gesture costs the chart's existing ones nothing.
    let pressTimer: number | null = null;
    const clearPress = () => {
      if (pressTimer != null) window.clearTimeout(pressTimer);
      pressTimer = null;
    };
    // Android answers a held finger with a menu of its own — copy / share /
    // select — and it decides *later* than we do: our ＋ lands at 420ms, the
    // platform's callout at ~500ms, and by then the thing under the finger may be
    // the ＋ or the price beside it rather than the canvas. Those are siblings of
    // the chart, so the host's own contextmenu handler never sees the event and
    // the OS answers first.
    //
    // So the guard is on the document, in the capture phase, and armed for the
    // life of any press that started on this chart: inside that window every
    // contextmenu belongs to this gesture, whatever it happens to land on.
    let ctxGuard = false;
    let ctxTimer: number | null = null;
    const guardCtx = (on: boolean) => {
      if (ctxTimer != null) window.clearTimeout(ctxTimer);
      ctxTimer = null;
      if (on) {
        ctxGuard = true;
        return;
      }
      // Disarmed a beat after the finger lifts, not with it — on some builds the
      // callout arrives just after the pointerup that ended the press.
      ctxTimer = window.setTimeout(() => {
        ctxGuard = false;
        ctxTimer = null;
      }, 600);
    };
    const onDocCtx = (e: MouseEvent) => {
      if (ctxGuard) e.preventDefault();
    };
    // The ruler's anchor while a measurement is being drawn. Survives mouseup on
    // a no-move click, so both TV gestures work: press-drag-release and
    // click-move-click.
    let rulerDrag: { i1: number; p1: number } | null = null;
    // Which price line is being dragged, if any.
    let hlineDrag: number | null = null;
    // Which bracket leg is being dragged, if any.
    let posDrag: "stop" | "target" | null = null;
    // A held entry chip, waiting for the pull that decides which leg it becomes.
    // Only the legs the position hasn't got are in it: the gesture is the way to
    // attach a missing stop or target without a line to grab, so pulling toward
    // one that already exists is not a gesture at all.
    let legArm: { stop: boolean; target: boolean } | null = null;
    // Which working-order leg is being dragged. Dragging the resting price
    // carries its bracket along at the distances it was placed at (the stop is
    // a distance from the entry, not a level in its own right), so the offsets
    // are captured at grab time.
    let ordDrag: {
      id: number;
      leg: "price" | "stop" | "target";
      dStop: number | null;
      dTarget: number | null;
    } | null = null;

    // A bracket must stay on the side of the mark where it can still do its job:
    // a long's stop below the market, its target above. Dragging one past the
    // mark would be an order that fills the instant you let go, so it's clamped
    // one tick short instead — the same refusal TV makes.
    const clampBracket = (leg: "stop" | "target", price: number, p: PositionData): number => {
      const tick = tapeRef.current?.tickSize ?? 0.25;
      const mark = Number.isFinite(p.last) ? p.last : p.entry;
      const above = (p.side === "long") === (leg === "target");
      return above ? Math.max(price, mark + tick) : Math.min(price, mark - tick);
    };
    // "entry" gets the resize cursor too: what it advertises is the axis the
    // gesture ends up moving along, once the hold is done.
    const cursorFor = (h: PosHit) => (h === "close" ? "pointer" : "ns-resize");
    const ordCursorFor = (h: OrderHit) => (h.leg === "cancel" ? "pointer" : "ns-resize");

    // Where a working order's legs may sit. The resting price has to stay on the
    // side of the mark its type belongs on — a buy limit above the market would
    // fill instantly at a price better than the market, and a buy stop below it
    // likewise — so an order dragged across the market stops at the mark rather
    // than turning into the other type under your hand. Its bracket has to stay
    // on the side of the *entry* that lets it do its job.
    const clampOrder = (
      o: WorkingOrderView,
      leg: "price" | "stop" | "target",
      price: number,
    ): number => {
      const tick = tapeRef.current?.tickSize ?? 0.25;
      const long = o.side === "long";
      if (leg === "price") {
        const mk = Number.isFinite(lastPriceRef.current) ? lastPriceRef.current : o.price;
        const above = o.type === "stop" ? long : !long;
        return above ? Math.max(price, mk + tick) : Math.min(price, mk - tick);
      }
      const above = long === (leg === "target");
      return above ? Math.max(price, o.price + tick) : Math.min(price, o.price - tick);
    };

    const onDown = (e: PointerEvent) => {
      // Before anything else, and whatever this press turns out to be: from here
      // until it ends, the platform's own press-and-hold menu is not welcome.
      guardCtx(true);
      // A press is the firmest claim on the keyboard there is. `onEnter` has
      // usually made it already, but not on a touchscreen — a finger arrives at
      // pointerdown with no enter before it. The page's focus is claimed by the
      // root's own handler, which covers the overlays this one never sees.
      focusChart(paneId);
      const x = xOf(e);
      const idx = idxAtX(x);
      if (idx == null) return;

      // Any press on the tape puts the order ＋ away. It is a transient thing you
      // summoned at a price, and the next thing you touch is either it or not it
      // — the ＋ and its menu are siblings of the canvas, not children, so a press
      // *on* them never reaches this handler. A press that then stays put re-arms
      // it at the new price, which is how you move it.
      if (plusRef.current != null) closePlusRef.current();

      // Space + click places an order — either button, and Space stays down so
      // you can place a second one without letting go.
      if (spaceRef.current && (e.button === 0 || e.button === 2)) {
        const p = priceAtY(yOf(e));
        if (p != null) onPlaceOrderRef.current?.(p, e.button === 2 ? "right" : "left");
        e.preventDefault();
        return;
      }
      // Same placement, reached by a tool instead of a modifier. Checked before
      // the button test below because a touch pointerdown reports button 0 —
      // which is right, but says nothing about which channel was chosen.
      if (orderArmedRef.current && e.button === 0) {
        const p = priceAtY(yOf(e));
        if (p != null) onPlaceOrderRef.current?.(p, orderSideRef.current);
        armOrderRef.current(false);
        e.preventDefault();
        return;
      }
      if (e.button !== 0) return;
      // A finger has no hover, so the press is the first this handler hears of
      // the pointer at all. Everything below decides whether to take the drag
      // off the chart's panning — on a mouse that decision was already made
      // during the hover that preceded this.
      pressId = e.pointerId;
      downX = x;
      downY = yOf(e);

      if (avwapArmedRef.current) {
        // Anchor the VWAP on the clicked bar and put the tool away. Un-hide the
        // layer first: its legend row only exists once an anchor is placed, so
        // hiding it once would otherwise make every later anchor land invisible,
        // and the tool read as dead.
        revealRef.current("vwapAnchored");
        const t = barsRef.current[idx].time;
        setAvwapAnchor(t);
        avwapAnchorRef.current = t;
        onAnchorRef.current?.(t);
        armAvwapRef.current(false);
        persistRef.current();
        e.preventDefault();
        return;
      }

      if (hlineArmedRef.current) {
        // Drop a line on the clicked price and put the tool away — like the ⚓,
        // one click is the whole gesture.
        const p = priceAtY(downY);
        if (p != null) {
          const id = nextHlineIdRef.current++;
          hlinesRef.current = [...hlinesRef.current, { id, price: p, armed: true }];
          selectedHlineRef.current = id;
          selectedRef.current = null;
          syncHlinesRef.current();
          syncRef.current();
        }
        armHlineRef.current(false);
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
        const t = barsRef.current[idx].time;
        rangesRef.current = [...rangesRef.current, { id, from: t, to: t, live: false }];
        selectedRef.current = id;
        drag = { mode: "new", id, anchorIdx: idx, from: idx, to: idx };
        e.preventDefault();
        paint();
        return;
      }

      // The open position outranks the fixed-range profiles: its lines are what
      // sits under the pointer most often once you're in a trade.
      const ph = posPrim.hit(x, downY);
      if (ph === "close") {
        onFlattenRef.current?.();
        e.preventDefault();
        return;
      }
      if (ph === "entry") {
        // Hold the position chip, then pull: the drag-out gesture for a leg that
        // has no line to grab yet. Armed the same way the order ＋ is — no
        // preventDefault, no taking the drag off panning — so until the countdown
        // finishes this is still an ordinary press, and a flick is still a pan.
        // (The primitive only offers this hit while a leg is missing, so reaching
        // here already means there is one to pull out.)
        clearPress();
        pressTimer = window.setTimeout(() => {
          pressTimer = null;
          const p = posRef.current;
          if (!p) return;
          legArm = { stop: p.stop == null, target: p.target == null };
          setScroll(true);
          host.style.cursor = "ns-resize";
          posPrim.setHover("entry");
          // Same reason as the ＋: a held finger may have selected something on
          // the way here, and its handles would sit over the chart.
          window.getSelection?.()?.removeAllRanges();
        }, LONG_PRESS_MS);
        return;
      }
      if (ph) {
        posDrag = ph;
        setScroll(true);
        host.style.cursor = "ns-resize";
        posPrim.setHover(ph);
        e.preventDefault();
        return;
      }

      const oh = ordPrim.hit(x, downY);
      if (oh) {
        const o = workingRef.current.find((v) => v.id === oh.id);
        if (oh.leg === "cancel") onOrderCancelRef.current?.(oh.id);
        else if (o) {
          ordDrag = {
            id: oh.id,
            leg: oh.leg,
            dStop: o.stop != null ? o.stop - o.price : null,
            dTarget: o.target != null ? o.target - o.price : null,
          };
          setScroll(true);
          host.style.cursor = "ns-resize";
          ordPrim.setHover(oh);
        }
        e.preventDefault();
        return;
      }

      // A price line, grabbed. After the position and the orders (their levels
      // outrank a drawing), before the range profiles (a line is a thinner
      // target, so it wins where they overlap).
      const lh = hitHline(downY);
      if (lh != null) {
        selectedHlineRef.current = lh;
        selectedRef.current = null;
        hlineDrag = lh;
        setScroll(true);
        host.style.cursor = "ns-resize";
        syncHlinesRef.current();
        syncRef.current();
        e.preventDefault();
        return;
      }

      const hit = hitTest(x);
      if (!hit) {
        // Clicking bare chart deselects — but let the chart pan as usual.
        if (selectedRef.current != null) {
          selectedRef.current = null;
          syncRef.current();
        }
        if (selectedHlineRef.current != null) {
          selectedHlineRef.current = null;
          syncHlinesRef.current();
        }
        // Nothing else wanted this press, so it is a candidate for the order
        // gesture. Started rather than acted on, and without preventDefault or
        // taking the drag off panning: until the timer fires this is still an
        // ordinary press, and most of them are pans. If the finger travels or
        // lifts first, the countdown is dropped and nothing happened.
        if (canOrderRef.current) {
          clearPress();
          pressTimer = window.setTimeout(() => {
            pressTimer = null;
            const p = priceAtY(downY);
            if (p != null) openPlusRef.current(p);
            // If the platform got as far as selecting something on the way here,
            // drop it — otherwise the selection handles and their action bar stay
            // up over the chart even with the callout itself suppressed.
            window.getSelection?.()?.removeAllRanges();
          }, LONG_PRESS_MS);
        }
        return;
      }
      const r = rangesRef.current.find((v) => v.id === hit.id)!;
      selectedRef.current = hit.id;
      if (selectedHlineRef.current != null) {
        selectedHlineRef.current = null;
        syncHlinesRef.current();
      }
      drag = { mode: hit.mode, id: hit.id, anchorIdx: idx, from: nearestIdx(r.from), to: nearestIdx(r.to) };
      // A range being dragged is not following anything: unlatch on the grab, and
      // let the drop decide again. Without this `paint` would keep pulling the
      // right edge back to the live edge while the pointer is trying to move it.
      r.live = false;
      e.preventDefault();
      if (hit.mode === "move") host.style.cursor = "grabbing";
      syncRef.current();
    };

    const onMove = (e: PointerEvent) => {
      if (!isPress(e)) return;
      const x = xOf(e);
      // A press that travels is a pan, not a long press. Same slop the ruler
      // uses, so "held still" means the same thing everywhere on this chart.
      if (pressTimer != null && Math.hypot(x - downX, yOf(e) - downY) > DRAG_SLOP) clearPress();
      if (posDrag) {
        // Live, primitive-only: the page hears about it on release, so a drag
        // costs no React render and no trip through the trade log.
        const p = posRef.current;
        const price = priceAtY(yOf(e));
        if (!p || price == null) return;
        const v = clampBracket(posDrag, price, p);
        posRef.current = { ...p, [posDrag]: v };
        pushPos();
        return;
      }
      if (legArm) {
        // Which leg a pull means is a matter of which side of the trade it goes
        // to, not of up and down: a long's stop sits under the market and a
        // short's over it, so "down" means SL on one and TP on the other. Nothing
        // happens until the pointer has committed to a direction — and a pull
        // toward a leg that already exists is left alone, so the held chip stays
        // armed for a pull the other way.
        const p = posRef.current;
        const y = yOf(e);
        const dy = y - downY;
        if (!p || Math.abs(dy) <= DRAG_SLOP) return;
        const leg = (dy < 0) === (p.side === "long") ? "target" : "stop";
        if (!legArm[leg]) return;
        // From here it is an ordinary bracket drag, and it ends like one: the
        // page hears about the new leg on release, through onBracketChange.
        legArm = null;
        posDrag = leg;
        posPrim.setHover(leg);
        const price = priceAtY(y);
        if (price != null) {
          posRef.current = { ...p, [leg]: clampBracket(leg, price, p) };
          pushPos();
        }
        return;
      }
      if (ordDrag) {
        // Same deal as a bracket drag: primitive-only until release.
        const price = priceAtY(yOf(e));
        const o = workingRef.current.find((v) => v.id === ordDrag!.id);
        if (!o || price == null) return;
        const v = clampOrder(o, ordDrag.leg, price);
        const next: WorkingOrderView =
          ordDrag.leg === "price"
            ? {
                ...o,
                price: v,
                stop: ordDrag.dStop != null ? v + ordDrag.dStop : null,
                target: ordDrag.dTarget != null ? v + ordDrag.dTarget : null,
              }
            : { ...o, [ordDrag.leg]: v };
        workingRef.current = workingRef.current.map((w) => (w.id === next.id ? next : w));
        pushOrders();
        return;
      }
      if (hlineDrag != null) {
        // Live, primitive-only, like every other level drag. Moving a line
        // re-arms its alert: a level you just placed somewhere new is a level
        // the tape hasn't answered.
        const price = priceAtY(yOf(e));
        const l = hlinesRef.current.find((v) => v.id === hlineDrag);
        if (!l || price == null) return;
        l.price = price;
        l.armed = true;
        paintHlines();
        return;
      }
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
        //
        // Skipped for touch. A finger cannot hover, so anything that arrives here
        // from one is the tail of a gesture that has already ended; acting on it
        // would leave a leg lit up under nothing, and would flip panning off on
        // the strength of where a finger happened to lift.
        if (e.pointerType === "touch" || anyArmed()) return;
        const ph = posPrim.hit(x, yOf(e));
        posPrim.setHover(ph);
        const oh = ph ? null : ordPrim.hit(x, yOf(e));
        ordPrim.setHover(oh);
        if (ph || oh) {
          setScroll(true);
          host.style.cursor = ph ? cursorFor(ph) : ordCursorFor(oh!);
          return;
        }
        if (hitHline(yOf(e)) != null) {
          setScroll(true);
          host.style.cursor = "ns-resize";
          return;
        }
        const hit = hitTest(x);
        setScroll(hit != null);
        host.style.cursor = !hit ? "" : hit.mode === "move" ? "grab" : "col-resize";
        return;
      }

      const idx = idxAtX(x);
      if (idx == null) return;
      const bars = barsRef.current;
      const last = bars.length - 1;
      const r = rangesRef.current.find((v) => v.id === drag!.id);
      if (!r) return;

      if (drag.mode === "move") {
        // Slide by whole bars, clamped so the range keeps its width at the edges.
        const width = drag.to - drag.from;
        let from = drag.from + (idx - drag.anchorIdx);
        from = Math.min(last - width, Math.max(0, from));
        r.from = bars[from].time;
        r.to = bars[from + width].time;
      } else {
        // Resizing: the grabbed edge follows the pointer, the other stays put, and
        // dragging one past the other just flips which is which.
        const fixed =
          drag.mode === "new" ? drag.anchorIdx : drag.mode === "left" ? drag.to : drag.from;
        r.from = bars[Math.min(idx, fixed)].time;
        r.to = bars[Math.max(idx, fixed)].time;
      }
      paint(); // primitive only — no React re-render mid-drag
    };

    const onUp = (e: PointerEvent) => {
      if (!isPress(e)) return;
      guardCtx(false);
      pressId = null;
      // Lifting before the countdown finishes is a tap, and a tap is not it.
      // (pointercancel lands here too, which is the case that matters: the OS
      // taking the pointer away must not leave a ＋ armed behind it.)
      clearPress();
      if (legArm) {
        // Held long enough to arm, then let go without pulling anywhere: no leg
        // was chosen, so there is nothing to commit.
        legArm = null;
        setScroll(false);
        host.style.cursor = "";
        posPrim.setHover(null);
        return;
      }
      if (posDrag) {
        const p = posRef.current;
        posDrag = null;
        setScroll(false);
        host.style.cursor = "";
        posPrim.setHover(null);
        if (p) onBracketRef.current?.({ stop: p.stop, target: p.target });
        return;
      }
      if (ordDrag) {
        const o = workingRef.current.find((v) => v.id === ordDrag!.id);
        ordDrag = null;
        setScroll(false);
        host.style.cursor = "";
        ordPrim.setHover(null);
        if (o) {
          onOrderMoveRef.current?.({ id: o.id, price: o.price, stop: o.stop, target: o.target });
        }
        return;
      }
      if (hlineDrag != null) {
        hlineDrag = null;
        setScroll(false);
        host.style.cursor = "";
        // The settle is what persists it and re-renders the toolbar.
        syncHlinesRef.current();
        return;
      }
      if (rulerDrag) {
        // A real drag ends the measurement here; a stationary click leaves the
        // anchor live so the pointer keeps stretching it (click-move-click).
        if (Math.hypot(xOf(e) - downX, yOf(e) - downY) >= DRAG_SLOP) {
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

      // Dropped with the right edge on the last bar: latch it there and let it
      // follow the tape from now on. Dropped anywhere else it stays where it was
      // put, which is also how a latched profile is released — drag its edge off
      // the live edge. One predicate for every drag mode, so moving a whole
      // range up against the live edge latches it too.
      const dropped = rangesRef.current.find((r) => r.id === id);
      if (dropped) dropped.live = nearestIdx(dropped.to) === barsRef.current.length - 1;

      // A click with no real drag means "never mind" — don't leave a hairline
      // profile of a single bar behind.
      if (wasNew && moved < DRAG_SLOP) {
        rangesRef.current = rangesRef.current.filter((r) => r.id !== id);
        selectedRef.current = null;
      }
      if (wasNew) disarmRef.current();
      syncRef.current();
    };

    armApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
    };
    // Below the handlers because both close over `rulerDrag`: disarming or
    // clearing must also drop an in-flight anchor, or the measurement would keep
    // chasing the pointer after Esc / toggling the tool off.
    rulerApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
      if (a) ruler.setData(null); // re-arming starts a fresh measurement
      else rulerDrag = null;
    };
    rulerClearRef.current = () => {
      const had = rulerDrag != null || ruler.data() != null;
      rulerDrag = null;
      ruler.setData(null);
      return had;
    };
    // Arming the ⚓ tool just sets the crosshair and takes the pointer off panning
    // so the anchor click lands cleanly; the placement happens in onDown.
    avwapApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
    };
    // Same again for the ━ tool: the click has to land on a price.
    hlineApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
    };
    // Holding Space takes the left button away from panning (the click has to
    // land on a price, not drag the chart out from under it) and puts the
    // crosshair up so the price under the pointer is readable before you commit.
    spaceApplyRef.current = () => {
      setScroll(anyArmed());
      setCursor(anyArmed());
    };
    // The tool form of the same thing.
    orderApplyRef.current = () => {
      setScroll(anyArmed());
      setCursor(anyArmed());
    };

    // The browser's own menu never gets this surface. It would land on top of the
    // Space+click gesture (whose right half *is* a right-click), and on a
    // touchscreen it arrives from exactly the press-and-hold that summons the ＋
    // — so the one gesture would open two menus, ours under theirs.
    //
    // What replaces it: on a mouse, a right-click opens the order ticket outright
    // at the price under the pointer. The two-step ＋ exists because a fingertip
    // needs a confirmation of the level it hit and has no second button; a
    // right-click is already deliberate and already precise, and making it walk to
    // the axis for a second click would be a worse gesture, not a consistent one.
    const onCtx = (e: MouseEvent) => {
      e.preventDefault();
      if (spaceRef.current || orderArmedRef.current) return;
      if (COARSE_POINTER || !canOrderRef.current) return;
      const p = priceAtY(yOf(e));
      if (p != null) openPlusRef.current(p, true);
    };
    // Held state survives leaving and re-entering — the key really is still
    // down — so only entry and exit are tracked, not the modifier itself.
    const onEnter = () => {
      overRef.current = true;
      // Deliberately no focus claim of any kind. Focus — the ring, the rail, the
      // timeframe control, and which pane owns the keyboard — is claimed by a
      // *press*: chrome that re-aims itself at whatever the pointer brushed past
      // on its way somewhere else is chrome you stop trusting.
      //
      // Hovering still lets a pane answer keys (see the key handler's `over`
      // clause). That is what keeps Space+click working on a pane you have only
      // pointed at, without letting a pass-over move anything.
    };
    const onLeave = () => {
      overRef.current = false;
      // Take the linked crosshair down with the pointer. A crosshair left behind
      // on three other panes reads as a live reading of a price nobody is
      // pointing at any more.
      publishCrosshair(paneId, null, 0);
    };

    // Pointer events rather than mouse events: one set of handlers that a finger,
    // a stylus and a mouse all arrive through, instead of a mouse-only chart with
    // touch bolted on beside it.
    //
    // The chart surface is JS-driven — lightweight-charts owns pan, pinch and
    // wheel, and the handlers here own everything drawn on top — so none of the
    // browser's own touch gestures were ever going to be right on it. Declaring
    // that once, up front, is also the only version that works: touch-action is
    // read when a gesture *starts*, so deciding at pointerdown that we want this
    // drag would already be a frame too late.
    host.style.touchAction = "none";
    // A press held on the tape is a gesture of ours now, and holding still on a
    // touchscreen is what the platforms reach for when they want to offer a
    // selection or a callout of their own. There is no text down here to select,
    // so nothing is being taken away — it just stops the OS answering first.
    host.style.userSelect = "none";
    host.style.webkitUserSelect = "none";
    // Safari-only, and not in the DOM typings — set by name rather than pretended
    // into the type.
    host.style.setProperty("-webkit-touch-callout", "none");
    host.addEventListener("pointerdown", onDown);
    host.addEventListener("contextmenu", onCtx);
    // Capture, and on the document rather than the chart: the event this one is
    // for may be aimed at something that only exists because the press summoned
    // it. See guardCtx.
    document.addEventListener("contextmenu", onDocCtx, true);
    host.addEventListener("pointerenter", onEnter);
    host.addEventListener("pointerleave", onLeave);
    // Move/up on the window, so a drag that leaves the chart still tracks and,
    // more importantly, still terminates. pointercancel matters more than it
    // looks: the OS takes the pointer away mid-drag for its own gestures (an
    // edge swipe, a notification pull) and without it the drag would never end.
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    applyRef.current = (v: IndicatorVisibility) => {
      // An anchor's row toggles the whole thing. Inside it, a *fixed* anchor's
      // band knob says which rings survive — the mid is not on that knob, so it
      // is drawn whenever the anchor is, and the wash needs both rings to be an
      // honest fill (chartPrefs.vwapBandsShown owns both rules). The ⚓ band is
      // not one of the three and keeps its whole envelope: it is drawn one at a
      // time, by hand, at the place you asked about.
      const setBand = (a: Anchor | null, on: boolean, anchor?: VwapFillAnchor) => {
        if (!a) return;
        const region = anchor ? regionRef.current[anchor] : "outer";
        const s = anchor ? vwapBandsShown(bandsRef.current[anchor], region) : ALL_BANDS;
        a.band.setRegion(region);
        for (const k of BAND_KEYS)
          a.lines[k].applyOptions({ visible: on && (k === "mid" || (RING_OF[k] === 1 ? s.s1 : s.s2)) });
        a.band.setVisible(on && s.fill);
      };
      setBand(gRef.current, v.vwapGlobex, "globex");
      setBand(nRef.current, v.vwapNy, "ny");
      setBand(aRef.current, v.vwapAnchored);
      setBand(wkRef.current, v.vwapWeekly, "weekly");
      for (const k of PROF_KEYS) {
        gProfRef.current?.[k].applyOptions({ visible: v.developingProfileGlobex });
        nProfRef.current?.[k].applyOptions({ visible: v.developingProfileNy });
        wProfRef.current?.[k].applyOptions({ visible: v.developingProfileWeekly });
      }
      for (const s of ibSeries) s.applyOptions({ visible: v.initialBalance });
      for (const s of ibExtSeries) s.applyOptions({ visible: v.ibExtensions });
      vp.setVisible(v.volumeProfile);
      for (const l of vpLines)
        l.applyOptions({ lineVisible: v.volumeProfile, axisLabelVisible: v.volumeProfile });
      tradesPrim.setVisible(v.replayTrades);
      bigPrim.setVisible(v.bigTrades);
      compPrim.setVisible(v.compositeProfile, v.compositeNodes);
      devPrim.setVisible(v.developingVpNy, v.developingVpNyNodes);
      extPrim.setVisible(v.externalChart);
      htfPrim.setVisible(v.htfTrend);
      rzPrim.setVisible(v.rankedZones);
      // The walk is skipped while it is off, so turning it on has nothing to
      // show until something else moves — forced here, which is the difference
      // between a layer that appears and one that appears at the next tick.
      if (v.rankedZones) paintRz(true);
      econPrim.setVisible(v.econEvents);
      if (v.econEvents) paintEcon();
      gexPrim.setVisible(v.gexLevels);
      if (v.gexLevels) paintGex();
      // The two event layers hide by dropping out of the filtered list, so that
      // the bands and the marginals can never show different sets.
      pushEvents();
      // CVD is the one layer that isn't hidden but built and torn down — it owns
      // a pane, and an empty pane keeps its height. The vol ruler is the same
      // kind of layer; the refresh mounts or drops it and fills it when it came up.
      syncCvdMount();
      syncCvdOscMount();
      refreshVr();
      // The line's seven series hide as a unit and the marks have their own eye.
      // The layer redraws itself when this takes it out of, or into, dark —
      // with both rows off it isn't drawn *or computed*.
      mvLayerRef.current?.setVisible(v.modernVwap, v.modernVwapSignals);
      // One row, line and flags together — the flags are how the line is read,
      // not a separate claim about the tape.
      dsvLayerRef.current?.setVisible(v.dynamicSwingVwap);
    };
    applyRef.current(visRef.current);

    // The three fixed anchors' fills. Seeded from the ref for the same reason the
    // visibility is: the bands were built at the authored weight, and this effect
    // re-runs for reasons that have nothing to do with the reader's choice.
    applyFillRef.current = (w) => {
      gRef.current?.band.setAlphaScale(w.globex);
      nRef.current?.band.setAlphaScale(w.ny);
      wkRef.current?.band.setAlphaScale(w.weekly);
    };
    applyFillRef.current(fillRef.current);

    // Re-cut every series this effect built. `hues` is reassigned so that
    // anything created later off it (there is nothing today, but the streaming
    // path is where a new layer would go) builds in the cut now in force.
    const relight = () => {
      hues = chartInk(appearanceRef.current.surface);
      const band = (a: Anchor | null, h: BandHue) => {
        if (!a) return;
        a.lines.mid.applyOptions({ color: h.middle });
        a.lines.u1.applyOptions({ color: h.band1 });
        a.lines.l1.applyOptions({ color: h.band1 });
        a.lines.u2.applyOptions({ color: h.band2 });
        a.lines.l2.applyOptions({ color: h.band2 });
        a.band.setRgb(h.fill);
      };
      band(gRef.current, hues.vwap.globex);
      band(nRef.current, hues.vwap.ny);
      band(wkRef.current, hues.vwap.weekly);
      band(aRef.current, hues.vwap.anchored);
      // The layer re-cuts its own seven lines and its wash, and redraws — its
      // regime tint rides on per-point colours, which only come back with data.
      mvLayerRef.current?.relight();
      dsvLayerRef.current?.relight();
      const prof = (r: Record<ProfKey, ISeriesApi<"Line">> | null, pal: { edge: string; poc: string }) => {
        if (!r) return;
        r.vah.applyOptions({ color: pal.edge });
        r.val.applyOptions({ color: pal.edge });
        r.poc.applyOptions({ color: pal.poc });
      };
      prof(gProfRef.current, hues.profile.globex);
      prof(nProfRef.current, hues.profile.ny);
      prof(wProfRef.current, hues.profile.weekly);
      for (const l of ibSeries) l.applyOptions({ color: hues.ib.line });
      for (const l of ibExtSeries) l.applyOptions({ color: hues.ib.ext });
      // The primitives want a frame.
      paint();
    };

    // Everything the imperative handle needs that lives inside this effect.
    hooksRef.current = { reprofile, paint, syncIb, remakeRuler, clearRuler: rulerClearRef.current, relight };

    // The surface is live and every hook it needs is in place: whoever owns the
    // data can hand it over now. Through a ref so that supplying the callback
    // never re-runs this effect — rebuilding the chart is the one thing a page
    // re-rendering must not cause.
    onReadyRef.current?.();

    // Sizing is autoSize's job (see createChart) — it covers the window resize
    // and the cases there is no window resize for: the page's controls wrapping,
    // a panel opening, entering fullscreen.
    return () => {
      hooksRef.current = null;
      vpRef.current = null;
      rangePrimRef.current = null;
      applyRef.current = null;
      applyFillRef.current = null;
      armApplyRef.current = null;
      rulerApplyRef.current = null;
      rulerClearRef.current = () => false;
      avwapApplyRef.current = null;
      hlineApplyRef.current = null;
      spaceApplyRef.current = null;
      orderApplyRef.current = null;
      paintRef.current = null;
      applyLaneRef.current = null;
      paintDevRef.current = null;
      paintExtRef.current = null;
      paintHtfRef.current = null;
      paintRzRef.current = null;
      paintEconRef.current = null;
      paintGexRef.current = null;
      paintShelvesRef.current = null;
      paintHlinesRef.current = null;
      clearPress();
      if (ctxTimer != null) window.clearTimeout(ctxTimer);
      host.removeEventListener("pointerdown", onDown);
      host.removeEventListener("contextmenu", onCtx);
      document.removeEventListener("contextmenu", onDocCtx, true);
      host.removeEventListener("pointerenter", onEnter);
      host.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      // Before chart.remove(): the two link callbacks close over this chart, and
      // a pane still in the map after its chart is destroyed is a throw on the
      // next crosshair move anywhere on the page.
      leaveLink();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      // Went with the chart; the points survive, so a remount redraws them.
      cvdSeriesRef.current = null;
      // The extra panes' series went with it too, and their refs have to say so.
      // A ref still holding a series belonging to a chart that no longer exists
      // is not merely stale: `syncCvdMount` reads `vrDevRef` to decide whether
      // the ruler needs remounting around a CVD change, so on the next mount it
      // would call `removeSeries` on the *new* chart with the *old* chart's
      // series, and lightweight-charts throws "Value is undefined" from inside
      // ensureDefined. That is a crash on remount, and remounting is exactly
      // what a second pane appearing does — see lib/chartFocus for the other
      // thing that turned out to assume this component only ever exists once.
      vrAtrRef.current = null;
      vrDevRef.current = null;
      vrYdayLineRef.current = null;
      // Dropped rather than destroyed, for the same reason: its series went with
      // the chart, and taking them off a chart that no longer exists throws from
      // inside the library. A remount builds a fresh layer above.
      studyLayerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Recolour in place. Declared after the build effect so that on mount it runs
  // with the refs already set — and on a colour change it is the only effect
  // that runs at all, which is the whole point: the replay keeps its range and
  // its clock.
  useEffect(() => {
    applyAppearance(chartRef.current, candleRef.current, appearance);
    recolorVolume(candleRef.current, volRef.current, appearance);
    // After applyAppearance, which sets the active ink the relight reads.
    hooksRef.current?.relight();
  }, [appearance]);

  // How the two time labels read: the calendar on or off, and whether the clock
  // runs to seconds. Bar times are the ET wall clock carried on the UTC epoch
  // (see ReplayEngine), so a UTC read of them *is* the local time — which is also
  // why hiding the date is a formatting choice and nothing more: the same bars,
  // without the day written on them.
  //
  // Two labels give the day away, and they come back on by different routes.
  // A tick-mark formatter returning null means "use the default", so switching
  // the calendar back on is just that — but only while the default is the one we
  // want, and a sub-minute bar needs seconds the default axis won't print. So a
  // seconds axis always sets a formatter of its own. The crosshair's has no
  // escape hatch at all — the library only asks whether one is *set* — so turning
  // the calendar off means handing back a formatter that spells out what the
  // default already did (the same `dd MMM 'yy` + clock).
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const at = (t: unknown) => new Date((t as number) * 1000);
    const p2 = (n: number) => String(n).padStart(2, "0");
    const clock = (d: Date) =>
      `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}` +
      (secondsAxis ? `:${p2(d.getUTCSeconds())}` : "");
    const hhmm = (t: unknown) => clock(at(t));
    const dateTime = (t: unknown) => {
      const d = at(t);
      const mon = new Date(d.getUTCFullYear(), d.getUTCMonth(), 1).toLocaleString(undefined, {
        month: "short",
      });
      return `${p2(d.getUTCDate())} ${mon} '${p2(d.getUTCFullYear() % 100)}   ${clock(d)}`;
    };
    chart.applyOptions({
      timeScale: {
        secondsVisible: secondsAxis,
        tickMarkFormatter: hideDates || secondsAxis ? hhmm : () => null,
      },
      localization: { timeFormatter: hideDates ? hhmm : dateTime },
    });
  }, [hideDates, secondsAxis]);

  // A new threshold only reaches the marks through a rebuilt snapshot (the
  // engine decides which sweeps exist); the bubbles are scaled off it here, so
  // the primitive needs it as it changes rather than at build time.
  useEffect(() => {
    bigPrimRef.current?.setMinLots(bigLots);
  }, [bigLots]);

  // A study added, removed or re-tuned. Structural (series are made and
  // destroyed, a pane appears) and then drawn against the bars already in hand,
  // so a study picked mid-replay comes up with the session behind it rather than
  // waiting for the next bar to close. Nothing here touches the clock or the
  // tape — this is a layer, like every other row on the legend.
  useEffect(() => {
    studyLayerRef.current?.setSpecs(studies ?? EMPTY_STUDIES);
    refreshStudies();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studies]);

  // The legend's study rows are named from the catalogue, so a pane that came up
  // holding saved specs has to re-render once the package lands or its rows sit
  // there labelled with bare export names. The layer asks for the catalogue too
  // and would redraw the canvas either way; this is the DOM half of the same
  // wait, and `loadCatalogue` is one shared import however many panes ask.
  const [catReady, setCatReady] = useState(() => catalogue() != null);
  useEffect(() => {
    if (catReady || !studies?.length) return;
    let alive = true;
    loadCatalogue()
      .then(() => alive && setCatReady(true))
      .catch(() => {
        // The row keeps its export name and the legend says the study drew
        // nothing — which is true, and is what the catalogue failing looks like.
      });
    return () => {
      alive = false;
    };
  }, [catReady, studies]);

  // The style is a repaint and the marginal switch is a re-push — neither needs
  // the engine. The *tuning* is not handled here at all: the page rebuilds the
  // snapshot for that, the same path a timeframe change takes, and the new
  // events arrive through setSnapshot like any other rebuild.
  useEffect(() => {
    if (eventOverlay) evPrimRef.current?.setStyle(eventOverlay.style);
    pushEvents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    eventOverlay?.style.labelSt,
    eventOverlay?.style.fillSweep,
    eventOverlay?.style.fillAbsorb,
    eventOverlay?.floorSweep,
    eventOverlay?.floorAbsorb,
    eventOverlay?.marginal,
    !eventOverlay,
  ]);

  // A new rule is a new composite; a new prominence is the same composite read
  // again. Both leave the replay exactly where it stands — nothing here can
  // touch the clock, the tape or a fill.
  useEffect(() => {
    rebuildComposite();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [composite]);
  // One prominence, two readings: the composite's nodes are cached and dropped
  // here, the developing profile's are re-read as part of its repaint.
  useEffect(() => {
    compNodesRef.current = null;
    paintComposite();
    paintDevRef.current?.();
    paintExtRef.current?.();
    paintHtfRef.current?.();
    paintRzRef.current?.();
    paintEconRef.current?.();
    paintGexRef.current?.();
    // Reset rather than step: a changed window or hold makes every reading so
    // far a reading of a different question.
    paintShelvesRef.current?.(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeProm, shelfParams]);

  // The field is a repaint, not a re-reading: every column already carries both
  // quantities, so this must NOT reset the way a parameter change does.
  useEffect(() => {
    paintShelvesRef.current?.(false);
  }, [shelfField]);

  // The one place the event filter lives: whether the page offers the layer at
  // all, the two per-kind toggles and the per-kind draw-floors are applied
  // here, and the same list then feeds the bands on the candles, the marginal
  // over each profile, and the legend's counts. Three copies of one filter
  // would be three places for them to disagree.
  //
  // What is *not* here is any selection threshold: those live in the engine,
  // because a burst is a cluster and an absorption is scored against a median,
  // and neither can be recovered by filtering the events a different setting
  // published. The draw-floor is different in kind — it reads the strength the
  // engine already assigned, so filtering here is exactly what it means.
  const pushEvents = () => {
    const ov = eventOvRef.current;
    const v = visRef.current;
    const list = !ov
      ? []
      : eventsRef.current.filter((e) =>
          e.kind === "sweep"
            ? v.sweepBursts && e.st >= ov.floorSweep
            : v.absorption && e.st >= ov.floorAbsorb,
        );
    evPrimRef.current?.setEvents(list);
    // The marginal is the same list read against price instead of time, and it
    // has its own switch — so the gutters get an empty list rather than a
    // different one. The developing NY gutter is not one of them: it draws its
    // histogram and nothing over it (DevelopingProfilePrimitive).
    const marginal = ov?.marginal ? list : [];
    compPrimRef.current?.setEvents(marginal);
    vpRef.current?.setEvents(marginal);
    const next = countEvents(list);
    setEvCount((c) => (c.sweep === next.sweep && c.absorb === next.absorb ? c : next));
  };

  // Grow the event list from a playback tail. Merged on kind + index rather than
  // by position: a burst and an absorption can both be open at once, so the
  // entry a tail updates is not always the last one.
  const mergeEvents = (tail: TapeEvent[]) => {
    if (!tail.length) return;
    const list = eventsRef.current;
    const pos = evPosRef.current;
    for (const e of tail) {
      const key = `${e.kind}:${e.idx}`;
      const at = pos.get(key);
      if (at == null) {
        pos.set(key, list.length);
        list.push(e);
      } else {
        list[at] = e;
      }
    }
    pushEvents();
  };

  // Grow the big-trade list from a playback tail. The tail's first entry is the
  // sweep that was still taking fills — matched by `idx`, not by time, because
  // a sweep that crosses a bar boundary changes the bar it belongs to.
  const mergeBigs = (tail: BigTrade[]) => {
    const list = bigsRef.current;
    for (const b of tail) {
      if (list.length && list[list.length - 1].idx === b.idx) list[list.length - 1] = b;
      else list.push(b);
    }
    if (tail.length) {
      bigPrimRef.current?.setTrades(list, bigLotsRef.current);
      setBigCount((n) => (n === list.length ? n : list.length));
    }
  };

  const bandData = (pts: BandPt[], key: BandKey) =>
    pts.map((p) => ({ time: p.time as Time, value: p[key] }));

  // Fold a playback tail into an anchor's fill points: the first tail entry
  // re-states the bar still forming (replace it), the rest are new (append).
  const mergeTail = (a: Anchor, tail: BandPt[]) => {
    for (const p of tail) putTail(a.pts, toVwapPoint(p));
    if (tail.length) a.band.setPoints(a.pts);
  };

  useImperativeHandle(ref, () => ({
    setTape(tape: Tape | null, opts?: { keepTools?: boolean; contextRanges?: TapeRange[] }) {
      tapeRef.current = tape;
      barsRef.current = [];
      histCountRef.current = 0;
      clearPendingOverlay();
      nyStartRef.current = NaN;
      nyVaRef.current = null;
      ibRef.current = null;
      lastPriceRef.current = NaN;
      posRef.current = null;
      pushPos();
      workingRef.current = [];
      pushOrders();
      tradesRef.current = [];
      setTradeCount(0);
      pushTrades();
      bigsRef.current = [];
      setBigCount(0);
      bigPrimRef.current?.setTrades([], bigLotsRef.current);
      eventsRef.current = [];
      evPosRef.current = new Map();
      pushEvents();
      // A new tape is a new anchor. The pane goes with it until the snapshot that
      // follows re-walks it — a stale CVD over fresh candles is worse than none.
      resetCvd();
      syncCvdMount();
      // Same for the vol ruler: its bars are the old tape's until the snapshot
      // that follows, so it comes down rather than briefly lying.
      unmountVr();
      // The days in front of the session are what the composite *is*, so a new
      // tape is always a new composite — including the one that is only a
      // context change, which is precisely when it has to be rebuilt.
      ctxRangeRef.current = opts?.contextRanges ?? [];
      rebuildComposite();
      // A new session is a new chart: nothing hand-drawn over the old one still
      // means anything. `keepTools` is the one exception — the same session with
      // more days glued in front of it is the same chart, and a profile drawn
      // over this morning still profiles this morning.
      if (!opts?.keepTools) {
        rangesRef.current = [];
        selectedRef.current = null;
        hlinesRef.current = [];
        selectedHlineRef.current = null;
        setAvwapAnchor(null);
        avwapAnchorRef.current = null;
        hooksRef.current?.clearRuler();
        // …unless this same session was drawn on before: the store keyed by
        // `drawingsKey` gives the profiles, the lines and the ⚓ back. Ranges
        // and lines restore inline (the repaints below draw them); the anchor
        // has to go back through the page — the engine develops the band — and
        // is deferred a microtask, because setTape is called from inside the
        // page's own session build and the anchor path re-snapshots through it.
        const saved = tape && drawingsKeyRef.current ? loadDrawings(drawingsKeyRef.current) : null;
        if (saved) {
          rangesRef.current = saved.ranges.map((r) => ({
            id: nextIdRef.current++,
            from: r.from,
            to: r.to,
            live: r.live === true,
          }));
          hlinesRef.current = saved.hlines.map((l) => ({
            id: nextHlineIdRef.current++,
            price: l.price,
            armed: l.armed,
          }));
          if (saved.anchor != null) {
            const t = saved.anchor;
            setAvwapAnchor(t);
            avwapAnchorRef.current = t;
            queueMicrotask(() => {
              if (avwapAnchorRef.current === t) onAnchorRef.current?.(t);
            });
          }
        }
        setRanges([...rangesRef.current]);
        setSelected(null);
        setHlines([...hlinesRef.current]);
        setSelectedHline(null);
      }
      hooksRef.current?.remakeRuler(tape);
      hooksRef.current?.paint();
      paintDevRef.current?.();
      paintExtRef.current?.();
      paintHtfRef.current?.();
      paintRzRef.current?.();
      paintEconRef.current?.();
      paintGexRef.current?.();
      // The tape itself was swapped — the tracker's bands belong to the old one.
      paintShelvesRef.current?.(true);
      paintHlinesRef.current?.();
    },
    setContextRanges(ranges: TapeRange[]) {
      // Identity, not contents: the page memoises them, so the same object is
      // the same cut of the same days — and re-summing five sessions of ticks to
      // land on the profile already on screen is the one cost worth avoiding
      // here.
      if (ctxRangeRef.current === ranges) return;
      ctxRangeRef.current = ranges;
      rebuildComposite();
    },
    clearRuler() {
      hooksRef.current?.clearRuler();
    },
    setSnapshot(s: Snapshot, opts?: { reframe?: boolean | "follow" }) {
      const candle = candleRef.current;
      const vol = volRef.current;
      if (!candle || !vol || !gRef.current || !nRef.current || !aRef.current || !wkRef.current)
        return;
      // The context days sit in front of the session's own bars and are drawn
      // exactly like them — they are the same tape, so a bar of Tuesday knows
      // the ticks it was built from and profiles like any other. Only the
      // session's bars ever grow, and they are the tail, which is what lets the
      // streaming path keep appending to this same array.
      const drawn = s.history.length ? s.history.concat(s.bars) : s.bars;
      const frame: "fit" | "follow" | "hold" =
        opts?.reframe === false ? "hold" : opts?.reframe === "follow" ? "follow" : "fit";
      // What the viewport is looking at, before the array under it is replaced —
      // both modes that keep the user's zoom have to read it here. Logical
      // ranges are indices into that array, so context days arriving at the
      // front would otherwise slide the view a thousand bars into Tuesday
      // without anyone touching the chart; `held` is remembered as a bar *time*
      // for that reason. A fit reframes anyway and doesn't ask.
      const held = frame === "fit" ? null : chartRef.current?.timeScale().getVisibleLogicalRange();
      const heldIdx = held ? clampIdx(barsRef.current, held.from) : -1;
      const heldAt = heldIdx >= 0 ? barsRef.current[heldIdx]?.time : undefined;
      // Where the playhead sat in the old array — a follow measures the room the
      // user left to the right of it, and puts the new one back at that spot.
      const prevLast = barsRef.current.length - 1;
      barsRef.current = drawn;
      histCountRef.current = s.history.length;
      // The snapshot re-`setData`s every overlay below, so anything the draw gate
      // was still holding is not just stale but would double-apply against the
      // series it is about to be replaced by.
      clearPendingOverlay();
      candle.setData(
        drawn.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })),
      );
      const volc = volumeColors(appearanceRef.current);
      vol.setData(
        drawn.map((b) => ({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? volc.up : volc.down })),
      );
      rebuildCvd(drawn, s.history.length);
      // A seek rebuilds the developing POC from tick zero, so this is a swap —
      // and it must land before refreshMv reads it for the `poc` anchor.
      mvPocRef.current = new Map(s.gProfile.map((p) => [p.time, p.poc]));
      mvWkPocRef.current = new Map(s.wProfile.map((p) => [p.time, p.poc]));
      // A swap, for the same reason the maps above are: a seek can hand back a
      // *shorter* list than the one held, and appending to it would leave the
      // approach reader looking at bars the replay has rewound past.
      vaPathsRef.current = {
        g: s.gProfile.slice(-VA_PATH_BARS),
        n: s.nProfile.slice(-VA_PATH_BARS),
        w: s.wProfile.slice(-VA_PATH_BARS),
      };
      refreshVr();
      refreshMv();
      refreshDsv();
      refreshStudies();
      const anchors: [Anchor, BandPt[]][] = [
        [gRef.current, s.gBand],
        [nRef.current, s.nBand],
        [aRef.current, s.aBand],
        [wkRef.current, s.wkBand],
      ];
      for (const [a, pts] of anchors) {
        for (const k of BAND_KEYS) a.lines[k].setData(bandData(pts, k));
        // A seek rebuilds the bands from scratch (including backwards, where the
        // fill must shrink), so hand the primitive a fresh array rather than
        // patching the one it holds.
        a.pts = pts.map(toVwapPoint);
        a.band.setPoints(a.pts);
      }
      const setProf = (
        lines: Record<ProfKey, ISeriesApi<"Line">> | null,
        pts: ProfilePt[],
      ) => {
        if (!lines) return;
        for (const k of PROF_KEYS)
          lines[k].setData(pts.map((p) => ({ time: p.time as Time, value: p[k] })));
      };
      setProf(gProfRef.current, s.gProfile);
      setProf(nProfRef.current, s.nProfile);
      setProf(wProfRef.current, s.wProfile);
      // A seek rebuilds the NY value area from tick zero, so the bell is
      // wherever this snapshot says it is — including "not yet", after a rewind
      // to before it.
      nyStartRef.current = s.nProfile.length ? s.nProfile[0].time : NaN;
      nyVaRef.current = s.nProfile.length ? s.nProfile[s.nProfile.length - 1] : null;
      // A seek rebuilds the marks from tick zero, so this is a swap and not a
      // merge — a rewind hands back the shorter list.
      bigsRef.current = s.bigs;
      bigPrimRef.current?.setTrades(s.bigs, bigLotsRef.current);
      setBigCount((n) => (n === s.bigs.length ? n : s.bigs.length));
      // Same swap-not-merge rule for the events, and the position index goes
      // with them: after a rewind the entries an open event maps to are gone.
      eventsRef.current = s.events;
      evPosRef.current = new Map(s.events.map((e, i) => [`${e.kind}:${e.idx}`, i]));
      pushEvents();
      ibRef.current = s.ib;
      mark(s.lastPrice);
      hooksRef.current?.syncIb();
      hooksRef.current?.paint();
      paintDevRef.current?.();
      paintExtRef.current?.();
      paintHtfRef.current?.();
      paintRzRef.current?.();
      paintEconRef.current?.();
      paintGexRef.current?.();
      paintShelvesRef.current?.(false);
      // A seek is a bar close as far as the levels panel is concerned: it lands
      // on a different last-closed bar, so the window it classifies over is a
      // different one. Here rather than beside the refreshes above because it
      // reads the anchors' `pts` and the IB, which this block has just replaced.
      refreshLevelsRef.current();
      paintLevelDistRef.current();
      // The context bars only arrive with a snapshot, so this is where the
      // composite finds the stretch to pin its histogram to.
      paintComposite();
      syncPresent({
        bars: s.bars.length > 0,
        g: s.gBand.length > 0,
        n: s.nBand.length > 0,
        wk: s.wkBand.length > 0,
        gp: s.gProfile.length > 0,
        np: s.nProfile.length > 0,
        wp: s.wProfile.length > 0,
        ib: s.ib != null,
        cvd: cvdAnyRef.current,
      });
      // Frame the tail of the loaded history so the replay opens zoomed-in, not
      // fit to the whole (possibly overnight-spanning) session — on both scales,
      // which is the same fit the ◎ performs (see `frameOnPrice`). A session that
      // opens as a ribbon under a far-off weekly band is one you have to press a
      // button before you can read, and the first thing anyone did on landing was
      // press it. Skipped when the snapshot is a side effect of something else (a
      // re-anchor) rather than a move through time — the user's zoom is theirs.
      //
      // Framed in bar indices rather than in times (see FRAME_BARS), so it means
      // the same thing on every timeframe.
      //
      // A seek follows instead (`"follow"`): the clock moved, so the view moves
      // with it — but at the bar spacing the user chose, not back to 90 bars.
      // Zoom out to read the morning, drag the scrubber, and the morning is
      // still the width you left it; only the playhead has moved. It is the same
      // thing playback already does when a new bar arrives at the right edge,
      // and a seek is playback with a bigger step.
      const chart = chartRef.current;
      const last = drawn.length - 1;
      const width = held ? held.to - held.from : 0;
      // A follow with nothing to preserve — the first snapshot of a session, or
      // a viewport the library hasn't laid out yet — is just a fit.
      const keepZoom = frame === "follow" && held && width > 0 && prevLast >= 0;
      if (!chart) {
        // No chart to frame.
      } else if (!keepZoom && frame !== "hold") {
        // Counted off the drawn array, so the context days stay off to the left
        // where they belong: the replay opens on the session, not on Tuesday.
        if (s.bars.length) frameOnPrice(chart, drawn, last, tapeRef.current?.tickSize ?? 0.25);
      } else if (keepZoom && held) {
        // The room past the newest bar is the user's too — an order gets placed
        // into it — so it is carried across rather than re-imposed. Capped at
        // half the viewport so a wide gap can't push the bars off screen, and
        // replaced by the default margin when the playhead was scrolled off to
        // the right entirely (nothing to preserve, and it has to come back).
        const raw = held.to - prevLast;
        const gap = Math.min(raw >= 0 ? raw : 12, width / 2);
        const to = last + gap;
        const from = to - width;
        chart
          .timeScale()
          .setVisibleLogicalRange(from < 0 ? { from: 0, to: width } : { from, to });
      } else if (held && heldAt != null) {
        // Same bars, new indices: put the range back on the bar it was on. The
        // shift is zero when nothing was prepended, which is every other
        // reframe:false caller (a re-anchor, a big-trade threshold).
        const shift = idxOfTime(drawn, heldAt) - heldIdx;
        if (shift)
          chart.timeScale().setVisibleLogicalRange({ from: held.from + shift, to: held.to + shift });
      }
      hooksRef.current?.reprofile();
      // A seek can land the playhead anywhere, including off a scale the user
      // set by hand.
      syncOffTape();
      // And the legend's readout, which otherwise has nothing to draw until the
      // pointer first crosses the chart — a session opens with a whole block of
      // numbers missing, and the rows under them sitting one line too high.
      paintOhlcRef.current?.(hoverIdxRef.current);
      // The other readout the tape moves rather than the bars.
      paintLevelDistRef.current();
    },
    applyStep(r: StepResult) {
      const candle = candleRef.current;
      const vol = volRef.current;
      if (!candle || !vol || !gRef.current || !nRef.current || !aRef.current || !wkRef.current)
        return;
      const volc = volumeColors(appearanceRef.current);
      let vrClosed = false;
      for (const b of r.barsTail) {
        candle.update({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close });
        vol.update({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? volc.up : volc.down });
        // Same overwrite-or-append the series do with update(): a tail starts at
        // the still-forming bar, which is already the last one we hold.
        const bars = barsRef.current;
        if (bars.length && bars[bars.length - 1].time === b.time) bars[bars.length - 1] = b;
        else {
          bars.push(b);
          // A new bar means the one before it closed — which is the vol ruler's
          // whole cadence (its values are facts about closed bars only).
          vrClosed = true;
        }
        // The tail is the session's own bars, which is what CVD is anchored to,
        // so every one of them counts. `update()` is a no-op while the pane is
        // down (off, or nothing tagged yet); the points still accrue, so the
        // series draws the whole session the moment it comes up.
        const v = stepCvd(b);
        cvdSeriesRef.current?.update({ time: b.time as Time, value: v });
      }
      // The first tagged tick of the session is what brings the pane to life.
      if (cvdAnyRef.current && !cvdSeriesRef.current) syncCvdMount();
      if (cvdAnyRef.current && !cvdOscSeriesRef.current) syncCvdOscMount();
      // The window moved with the forming bar. The divergences can't have — they
      // need closed bars to the right — so this writes one point, and the pass
      // that can find a new one runs on the close below.
      stepCvdOsc();
      // Keep the legend's readout on the bar it is meant to be on. Idle that is
      // the newest one, which is precisely the one this step just moved — a
      // readout that froze the moment you stopped pointing at something would be
      // worse than none. Straight at the DOM, like everything else in this
      // method; when the pointer *is* on a bar the index doesn't change and this
      // rewrites the same numbers.
      paintOhlcRef.current?.(hoverIdxRef.current);
      // The other readout the tape moves rather than the bars.
      paintLevelDistRef.current();
      // The developing-POC tail folds in before the indicator refresh below
      // reads it — same overwrite-or-append the engine itself does.
      for (const p of r.gProfTail) mvPocRef.current.set(p.time, p.poc);
      for (const p of r.wProfTail) mvWkPocRef.current.set(p.time, p.poc);
      // Same fold for the approach reader's window, through `putTail` and not a
      // push: a tail restates the forming bar's entry on every step, so pushing
      // would fill the window with six copies of one bar and the classification
      // would read a flat level that is moving.
      keepVaPath(vaPathsRef.current.g, r.gProfTail);
      keepVaPath(vaPathsRef.current.n, r.nProfTail);
      keepVaPath(vaPathsRef.current.w, r.wProfTail);
      if (vrClosed) {
        refreshVr();
        // A bar closed, so a fractal `fractalN` bars back may have just been
        // confirmed — the only moment a divergence can appear.
        drawCvdOsc();
        // Same cadence: the indicator's every value is a fact about a closed bar.
        refreshMv();
        refreshDsv();
        // And the picker's studies, which are the same fact 415 more times.
        // Measured at 1-5ms each over 4000 bars, against a cadence of once a bar.
        refreshStudies();
        // Last, because it reads what the three above have just redrawn: the
        // Modern VWAP's rings and the swing line only have a tail once their
        // redraw has run.
        refreshLevelsRef.current();
      }
      // The developing overlays. Their *bookkeeping* is unconditional (above and
      // below this block); only the draw is gated, which is the same split the
      // extra panes make — see OVERLAY_DRAW_MS for the cadence and the reason.
      // Until a drawing frame comes round, each tail accrues into the buffer, so
      // what finally reaches the series is every point, once.
      const pend = pendingOverlayRef.current;
      const bandTails = [r.gTail, r.nTail, r.aTail, r.wkTail];
      const profTails = [r.gProfTail, r.nProfTail, r.wProfTail];
      for (let i = 0; i < bandTails.length; i++)
        for (const p of bandTails[i]) putTail(pend.band[i], p);
      for (let i = 0; i < profTails.length; i++)
        for (const p of profTails[i]) putTail(pend.prof[i], p);
      const now = performance.now();
      if (vrClosed || now - lastOverlayDrawRef.current >= OVERLAY_DRAW_MS) {
        lastOverlayDrawRef.current = now;
        const anchors: [Anchor, BandPt[]][] = [
          [gRef.current, pend.band[0]],
          [nRef.current, pend.band[1]],
          [aRef.current, pend.band[2]],
          [wkRef.current, pend.band[3]],
        ];
        for (const [a, tail] of anchors) {
          for (const k of BAND_KEYS)
            for (const p of tail) a.lines[k].update({ time: p.time as Time, value: p[k] });
          mergeTail(a, tail);
        }
        const stepProf = (
          lines: Record<ProfKey, ISeriesApi<"Line">> | null,
          tail: ProfilePt[],
        ) => {
          if (!lines) return;
          for (const k of PROF_KEYS)
            for (const p of tail) lines[k].update({ time: p.time as Time, value: p[k] });
        };
        stepProf(gProfRef.current, pend.prof[0]);
        stepProf(nProfRef.current, pend.prof[1]);
        stepProf(wProfRef.current, pend.prof[2]);
        pend.band = [[], [], [], []];
        pend.prof = [[], [], []];
      }
      // The first NY point the playback ever emits is the bell.
      if (!Number.isFinite(nyStartRef.current) && r.nProfTail.length)
        nyStartRef.current = r.nProfTail[0].time;
      if (r.nProfTail.length) nyVaRef.current = r.nProfTail[r.nProfTail.length - 1];
      mergeBigs(r.bigTail);
      mergeEvents(r.evTail);
      mark(r.lastPrice);

      // The IB's right edge is pinned to the bar grid, so it only needs touching
      // when a bar closes — not once a frame.
      const ibChanged =
        r.ib?.high !== ibRef.current?.high ||
        r.ib?.low !== ibRef.current?.low ||
        r.ib?.complete !== ibRef.current?.complete;
      ibRef.current = r.ib;
      if (r.newBar || ibChanged) hooksRef.current?.syncIb();
      // The two live profiles run every frame — they read the tape, not the bar
      // grid, and the ticks that just printed are already in the forming bar's
      // span. Both go through an accumulator, so a frame costs the ticks it
      // added and nothing more.
      hooksRef.current?.reprofile();
      paintDevRef.current?.();
      paintExtRef.current?.();
      paintHtfRef.current?.();
      paintRzRef.current?.();
      paintEconRef.current?.();
      paintGexRef.current?.();
      paintShelvesRef.current?.(false);
      // A fixed-range profile is bounded by two bar times the user dragged out,
      // so only a new bar can change one (the range that ends at the live edge
      // gains the bar that just closed).
      if (r.newBar) hooksRef.current?.paint();

      syncPresent({
        bars: barsRef.current.length > 0,
        g: presentRef.current.g || r.gTail.length > 0,
        n: presentRef.current.n || r.nTail.length > 0,
        wk: presentRef.current.wk || r.wkTail.length > 0,
        gp: presentRef.current.gp || r.gProfTail.length > 0,
        np: presentRef.current.np || r.nProfTail.length > 0,
        wp: presentRef.current.wp || r.wProfTail.length > 0,
        ib: r.ib != null,
        cvd: cvdAnyRef.current,
      });
      // Once a bar, not once a frame: on a hand-set price scale the tape can
      // walk out of the window without the time scale ever moving, and this is
      // the only thing that would notice. A bar close is soon enough for a
      // hint, and cheap enough to be free.
      if (r.newBar) syncOffTape();
      // Deliberately no scrollToRealTime() here. That call is unconditional — it
      // drags the view back to the right edge (at the default offset) on every
      // bar close, discarding whatever you had panned or zoomed to. The time
      // scale's own `shiftVisibleRangeOnNewBar` already follows the tape when
      // the last bar is visible and leaves the range alone when it isn't, which
      // is the behaviour we want: your view is yours.
    },
    setPosition(p: PositionLine | null) {
      applyPos(p);
    },
    setOrders(orders: WorkingOrderView[]) {
      workingRef.current = orders;
      pushOrders();
    },
    setTrades(trades: TradeMarkView[]) {
      tradesRef.current = trades;
      // The count is the only thing React needs from this (the legend row only
      // exists once something has been traded); the marks themselves go straight
      // to the primitive.
      setTradeCount((n) => (n === trades.length ? n : trades.length));
      pushTrades();
    },
    // The page-level rail's four. No deps array on this handle, so these close
    // over the current render's arm functions rather than the first one's.
    armTool,
    // And the topbar catalogue's one, for the same reason it is on the handle at
    // all: this pane owns which layers it draws.
    setLayer,
    clearAvwap,
    deleteSelected: deleteSelectedAny,
    clearDrawings() {
      clearRanges();
      clearHlines();
    },
  }));

  // Only layers that have actually printed get a row.
  // Read during render rather than mirrored into state: both are set once when a
  // session lands, and the page re-renders this component on every HUD push
  // anyway, so there is nothing here for state to buy.
  const tickSize = tapeRef.current?.tickSize ?? 0.25;
  // The legend's swatches have to be the colours actually on the canvas, so they
  // come from the same ink the series were built (and relit) in rather than from
  // the dark palettes directly — otherwise a light chart would list its levels
  // in the hues of a chart it isn't.
  const legendInk = chartInk(appearance.surface);
  const tkt: TicketDraft = ticket ?? {
    size: 1,
    stopTicks: 0,
    targetTicks: 0,
    stopUsd: null,
    targetUsd: null,
  };
  // The routed contract's money if the page named one, else the tape's, else
  // none — and none means the ticket shows ticks alone. See `pointValue`.
  const tickUsd = tickSize * (pointValueProp ?? tapeRef.current?.pointValue ?? 0);

  // Every layer this pane can draw, whether or not it can draw it *yet*.
  //
  // One array carrying an `available` flag, rather than the series of guarded
  // pushes this used to be, because the same list now answers two questions. The
  // legend shows the rows that can draw — a toggle for a layer with no data is a
  // toggle for nothing. The topbar catalogue shows all of them, because switching
  // something on ahead of the session reaching it is legitimate, and it has to be
  // able to say *why* no row appeared. Guarding the push made the second list
  // impossible without writing every condition out a second time.
  //
  // The names come from LAYER_NAME and the readouts are built here, which is the
  // split that keeps the catalogue and the legend calling each layer the same
  // thing. A label is not a name: it quotes the threshold a count was counted at,
  // how many days went into a composite, what share of the session a gate called
  // trending. That is a fact about this pane now, and it belongs where it is
  // computed.
  const rows: (LegendItem & { available: boolean })[] = [];
  const row = (
    key: ReplayLayerKey,
    available: boolean,
    label: string,
    color: string,
    dim?: boolean,
    // Most rows get their knobs from the page (see `indicatorSettings` below,
    // which overwrites this). A layer whose params this pane owns brings its
    // own, so no host page has to learn about it to make it adjustable.
    settings?: LegendItem["settings"],
  ) => {
    rows.push({ key, available, label, color, dim, settings });
  };
  const N = LAYER_NAME;

  // Each fixed anchor carries its own envelope and fill weight — pane knobs, not
  // page ones, because the preference is the chart's and no host page needs to
  // learn about it for the band to be adjustable here. The label quotes the rings
  // the knob is actually drawing: a row reading "±1σ ±2σ" over a chart drawing
  // one of them is the legend lying about the layer it names.
  const anchorSpec = (anchor: VwapFillAnchor, title: string) => ({
    title,
    fields: vwapAnchorKnobs(
      { bands: vwapBands[anchor], fill: vwapFill[anchor], region: vwapRegion[anchor] },
      {
        bands: (c) => setBands(anchor, c),
        fill: (w) => setFill(anchor, w),
        region: (r) => setRegion(anchor, r),
      },
    ),
  });
  const anchorLabel = (anchor: VwapFillAnchor, name: string) =>
    `${name} ${vwapBandLabel(vwapBands[anchor])}`.trimEnd();
  // The coarser bar over this one. Always available — it is the chart's own bars
  // regrouped, so there is no tape it can be waiting on — but dimmed when the
  // period isn't above the drawn timeframe, which is the one way it can be on
  // and draw nothing. Its knobs are the pane's, like the anchors' above.
  row(
    "externalChart",
    true,
    `${N.externalChart} · ${
      EXTERNAL_PERIOD_OPTIONS.find((o) => o.value === extParams.period)?.label ?? extParams.period
    }`,
    extParams.palette === "custom" ? extParams.bull : legendInk.externalChart.bull,
    !extGrouped,
    {
      title: N.externalChart,
      fields: externalChartKnobs(extParams, patchExternal, extGrouped),
    },
  );
  // The higher frames' trend. Always available — it is this chart's bars
  // regrouped — dimmed while no frame is warm, and the label quotes the frames'
  // agreement at the live edge, so the read is in the legend as well as the wash.
  row(
    "htfTrend",
    true,
    `${N.htfTrend} · ${framesLabel(htfParams)}${
      htfNow.warm ? (htfNow.state > 0 ? " · up" : htfNow.state < 0 ? " · down" : " · mixed") : " · warming"
    }`,
    legendInk.htfTrend.line,
    !htfNow.warm,
    {
      title: N.htfTrend,
      fields: htfTrendKnobs(htfParams, patchHtf, htfNow.warm),
    },
  );
  // The ranked levels. Available whenever the tape is long enough to confirm a
  // pivot — it reads the chart's own bars, so there is nothing it waits on — and
  // dimmed when the walk found none, which on a short or a one-way session is a
  // real answer rather than a fault. The readout is how many are *drawn*, not
  // how many exist: the cap is the layer's main knob and a row saying 8 while
  // sixty are stored would hide that.
  // Always available — a calendar has no warm-up — and dimmed on a span with no
  // timed release at the floor, which on most overnight stretches is the answer.
  row(
    "econEvents",
    true,
    `${N.econEvents} · ${econCount}`,
    legendInk.econEvents.high,
    econCount === 0,
    {
      title: N.econEvents,
      fields: [
        {
          kind: "select" as const,
          key: "econFloor",
          label: "Impact",
          help: "Which ForexFactory impact tiers get a line. Every USD row is banked (data/cache/econ), so widening this needs no re-scrape. The actual is withheld until the tape reaches the release. Context only: docs/research/event-day-overlay.md found no event-day effect on any strategy.",
          value: econParams.floor,
          options: [
            { value: "high", label: "high" },
            { value: "medium", label: "high + medium" },
            { value: "low", label: "all" },
          ],
          onChange: (v: string | number) => patchEcon(v as EconImpactFloor),
        },
      ],
    },
  );
  // Needs the real contract, so unavailable wherever the page withholds it
  // (blind replay). The readout is the latest session's regime per book — the
  // two books disagreeing on long vs short gamma is itself the read.
  const gexRegime = (() => {
    if (!gexLast) return "";
    const parts = Object.entries(gexLast.books)
      .filter(([, b]) => b.available && b.at_ref_b != null)
      .map(([k, b]) => `${k} ${b.at_ref_b! >= 0 ? "+γ" : "−γ"}`);
    return parts.length ? ` · ${parts.join(" / ")}` : " · no book";
  })();
  row(
    "gexLevels",
    !!tapeContract,
    `${N.gexLevels}${gexParams.expiry === "all" ? "" : ` · ${GEX_EXPIRY_LABEL[gexParams.expiry]}`}${gexRegime}`,
    legendInk.gex.callBoth,
    !gexLast || !Object.values(gexLast.books).some((b) => b.available),
    {
      title: N.gexLevels,
      fields: [
        {
          kind: "select" as const,
          key: "gexExpiry",
          label: "Expiries",
          help: "Which options the levels are built from, by days to expiry counted from the session on the chart. 'All' sums every expiry — the big monthly and quarterly open interest dominates, so walls sit hundreds of points from price. '≤7 days' and '0DTE' keep only the near-dated contracts, whose gamma is concentrated at the strikes right around price, so the walls come in close: 0DTE is 1–2% of the open interest but ~20% of the gamma within 1% of price. This is only the 0DTE open interest the prior session left behind — the same-day 0DTE flow lives in intraday volume, which a once-a-day book cannot see, so the flip does not come in to price the way a volume-based read's does.",
          value: gexParams.expiry,
          options: GEX_EXPIRY_OPTIONS.map((e) => ({ value: e, label: GEX_EXPIRY_LABEL[e] })),
          onChange: (v: string | number) => patchGex({ expiry: v as GexExpiry }),
        },
        {
          kind: "select" as const,
          key: "gexWalls",
          label: "Walls",
          help: "How many call walls (C1.., cyan — resistance) and put walls (P1.., peach — support) to draw, per book. C1 is the strike with the most call dollar-gamma at the book's spot, P1 the most put dollar-gamma; strikes within 0.15% of each other count as one wall. Solid = both books (NDX and QQQ) carry it, dashed = one book only; thicker = more gamma resting there, a bigger hedge to work through — not a claim that it holds. Each session uses the latest book stamped before its Globex open, so a replay never sees open interest from after the fact (in practice that is the prior day's close snapshot — one day stale). Unvalidated: the gamma is a Black-Scholes recompute 5–9% off Cboe's own greeks, and level-bounce geometry has come out null four times here. Context to look at, not levels to trade.",
          value: gexParams.walls,
          options: GEX_WALL_OPTIONS.map((n) => ({ value: n, label: n === 0 ? "none" : `top ${n}` })),
          onChange: (v: string | number) => patchGex({ walls: Number(v) }),
        },
        {
          kind: "select" as const,
          key: "gexFlip",
          label: "Flip band",
          help: "The zero-gamma flip: above it dealers are net long gamma and damp moves, below it they are short and chase them. Drawn as the band between the NDX book's flip and the QQQ book's, because the two routinely disagree by 0.5–1.5% and a single line would claim a precision the data does not have. The legend row names each book's regime at its own spot (+γ long, −γ short) for the latest session.",
          value: gexParams.flip ? 1 : 0,
          options: [
            { value: 1, label: "on" },
            { value: 0, label: "off" },
          ],
          onChange: (v: string | number) => patchGex({ flip: Number(v) === 1 }),
        },
      ],
    },
  );
  row(
    "rankedZones",
    true,
    `${N.rankedZones} · top ${rzCount}` +
      (rzParams.rankBy === "flow" ? (rzFlow ? " by flow" : " · no tape, by score") : ""),
    legendInk.rankedZones.support,
    rzCount === 0,
    {
      title: N.rankedZones,
      fields: rankedZonesKnobs(rzParams, patchRankedZones),
    },
  );
  row(
    "vwapGlobex",
    present.g,
    anchorLabel("globex", N.vwapGlobex),
    legendInk.vwap.globex.middle,
    undefined,
    anchorSpec("globex", N.vwapGlobex),
  );
  row(
    "vwapNy",
    present.n,
    anchorLabel("ny", N.vwapNy),
    legendInk.vwap.ny.middle,
    undefined,
    anchorSpec("ny", N.vwapNy),
  );
  row(
    "vwapWeekly",
    present.wk,
    anchorLabel("weekly", N.vwapWeekly),
    legendInk.vwap.weekly.middle,
    undefined,
    anchorSpec("weekly", N.vwapWeekly),
  );
  row(
    "vwapAnchored",
    avwapAnchor != null,
    `${N.vwapAnchored} ±1σ ±2σ`,
    legendInk.vwap.anchored.middle,
  );
  // Modern VWAP, offered only where the page holds its parameters. Two rows: the
  // line, and the triggers read off it. Each label quotes what its own row is
  // actually showing at the current settings — an anchor count is how hard the
  // swing rule is working, and a gate that is undefined half the session is a
  // fact about warm-up, not about the market.
  //
  // Nothing is computed while both rows are off (see refreshMv), so neither label
  // quotes a number it doesn't have — "0 through the gate" would read as a
  // measurement when it only means nobody has asked yet.
  const mvLive = vis.modernVwap || vis.modernVwapSignals;
  row(
    "modernVwap",
    !!mvParams,
    !mvParams
      ? N.modernVwap
      : `${N.modernVwap} · ${
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
          ? ` · ${Math.round(mvRead.trendPct)}% trending${mvRead.undefPct >= 1 ? `, ${Math.round(mvRead.undefPct)}% undefined` : ""}`
          : ""),
    legendInk.modernVwap.middle,
  );
  row(
    "modernVwapSignals",
    !!mvParams,
    !mvParams
      ? N.modernVwapSignals
      : mvParams.signals === "none"
        ? `${N.modernVwapSignals} · off`
        : `${N.modernVwapSignals} · MR/TC` +
          (mvLive
            ? ` · ${mvRead.signals}${mvParams.signals === "gated" ? " through the gate" : " raw"}`
            : ""),
    legendInk.modernVwap.middle,
    // The row stays when its own knob switched it off — that knob is the only way
    // back on, and it lives behind this row's "…".
    mvParams?.signals === "none",
  );
  // The Zeiierman line. One row, and it quotes the two things that are facts
  // about the construct rather than about the market: how many times the
  // structure flipped at this swing period (an anchor count is how hard the rule
  // is working) and what the volatility adjustment has done to the half-life —
  // the knob says 20 bars, the tape may be running it at 9.
  row(
    "dynamicSwingVwap",
    !!dsvParams,
    !dsvParams
      ? N.dynamicSwingVwap
      : `${N.dynamicSwingVwap} · swing ${dsvParams.swingPeriod}${
          // The bucketing the swings were actually hunted on, and only when they
          // were: on a pane already at or below it, nothing was regrouped.
          dsvRead.anchored && vis.dynamicSwingVwap ? `@${dsvParams.anchorTf}` : ""
        } · ${
          dsvParams.weighting === "cumulative"
            ? // No half-life to quote, and the word is the point: the legs are
              // ordinary anchored VWAPs off the same swings.
              "cumulative"
            : `APT ${
                dsvParams.adaptApt && vis.dynamicSwingVwap && dsvRead.aptNow
                  ? `${dsvRead.aptNow.toFixed(dsvRead.aptNow < 10 ? 1 : 0)}b`
                  : `${dsvParams.apt}b`
              }${dsvParams.adaptApt ? ` ATR ${dsvParams.volBias}×` : ""}`
        }${
          dsvParams.bands
            ? ` ±${dsvParams.bands}σ${dsvParams.bandScope === "all" ? " all" : ""}`
            : ""
        }` +
        (vis.dynamicSwingVwap
          ? ` · ${dsvRead.pivots}⚑ · ${Math.round(dsvRead.bullPct)}% bull` +
            // His `max_polylines_count`, said out loud: on a long tape the older
            // segments are simply not on the chart, and a silent 100 would read
            // as "that is all there was".
            (dsvRead.dropped ? ` · oldest ${dsvRead.dropped} dropped` : "")
          : ""),
    `rgb(${legendInk.dynamicSwingVwap.bull})`,
  );
  row(
    "developingProfileGlobex",
    present.gp,
    `${N.developingProfileGlobex} VAH/POC/VAL`,
    legendInk.profile.globex.edge,
  );
  row(
    "developingProfileNy",
    present.np,
    `${N.developingProfileNy} VAH/POC/VAL`,
    legendInk.profile.ny.edge,
  );
  // Absent (not just off) when the weekly profile could not be honestly seeded
  // — the same "no line without the whole week" rule the weekly VWAP row obeys.
  row(
    "developingProfileWeekly",
    present.wp,
    `${N.developingProfileWeekly} VAH/POC/VAL`,
    legendInk.profile.weekly.edge,
  );
  // The same distribution as a histogram, in its own gutter. Its own row because
  // the levels and the shape are separately useful — and because this one is
  // where the event marginal lands.
  row("developingVpNy", present.np, `${N.developingVpNy} (${PROFILE_BIN}pt rows)`, "#c4b5fd");
  // The nodes it names, on the same switch as the composite's: the prominence
  // floor behind this row's "…" is what decides they exist at all. The row is
  // here even at zero, dimmed — that knob is the only way back on.
  row(
    "developingVpNyNodes",
    present.np,
    nodeProm > 0
      ? `${N.developingVpNyNodes} · HVN/LVN at ${Math.round(nodeProm * 100)}% prominence`
      : `${N.developingVpNyNodes} · off`,
    "#818cf8",
    nodeProm === 0,
  );
  row("initialBalance", present.ib, `${N.initialBalance} · first 60m H/L`, legendInk.ib.line);
  row("ibExtensions", present.ib, `${N.ibExtensions} · 1×/1.5×/2×`, legendInk.ib.ext);
  // "(tick)" and not "(est.)": the replay profiles the real tape, never a
  // reconstruction spread across bar ranges — so the POC print is a price that
  // actually traded.
  // The label says whether the delta lane is up, because the row's eye hides
  // both lanes and "+ delta" is the only thing on screen that names the second
  // one. `present.cvd` is the availability test the lane needs and not
  // `present.bars`: both are the tape's aggressor tag, read by price here and by
  // time there.
  row(
    "volumeProfile",
    present.bars,
    // The scale is named too: three different readings share this lane, and
    // "+ delta" over one measuring imbalance names the wrong distribution.
    `${N.volumeProfile} · POC/VA${profileDelta ? ` + delta:${deltaLaneLabel(deltaLane)}` : ""} (${PROFILE_BIN}pt rows)`,
    palette.gold,
    false,
    {
      title: N.volumeProfile,
      // The tape that carries the aggressor tag is the same one a verdict reads
      // its bars from, so one test answers for both knobs.
      fields: volumeProfileKnobs(profileDelta, setProfileTint, present.cvd, {
        value: deltaLane,
        onChange: setLaneKnobs,
        canClassify: present.cvd,
      }),
    },
  );
  // The shelves. Two rows, because the raster and the boxes are two readings and
  // one of them is thresholded: the raster is the layer, the boxes are a claim
  // drawn on top of it. Being able to drop the claim and keep the picture is the
  // point — every level-geometry study in this repo has come back null, so the
  // unthresholded view is the one that can be checked.
  //
  // Available on `present.bars` rather than on "a shelf exists": switching the
  // layer on before the window has filled is legitimate, and a row that appeared
  // only once something had been found would be missing at exactly the moment you
  // went looking for it.
  row(
    "volumeShelf",
    present.bars,
    shelfFieldRef.current === "flow"
      ? `${N.volumeShelf} · order flow over ${shelfParamsRef.current.windowMin}m`
      : `${N.volumeShelf} · size per visit over ${shelfParamsRef.current.windowMin}m`,
    palette.orange,
  );
  row(
    "volumeShelfBoxes",
    present.bars,
    `${N.volumeShelfBoxes} · ≥${shelfParamsRef.current.zMin}σ held ${shelfParamsRef.current.minHoldMin}m`,
    palette.orange,
    !vis.volumeShelf,
  );
  // Once the tape has printed, whether or not any sweep has cleared the floor
  // yet: the threshold is on this row now, and a row that waited for a big trade
  // would be missing at exactly the moment you wanted to lower the bar. The label
  // carries the threshold, since that is what "big" currently means.
  row("bigTrades", present.bars, `${N.bigTrades} · >${bigLots} lots · ${bigCount}`, palette.blue);
  // Only once the tape has actually tagged an aggressor — `present.cvd` is that
  // and not "there are bars". The label names the anchor, because a CVD that
  // reset somewhere you didn't expect is a different indicator, and this one
  // starts at the session open rather than at the first context day drawn.
  row("cvd", present.cvd, `${N.cvd} · cumulative delta from the session open`, palette.blue);
  // The windowed one, available on the same condition and for the same reason —
  // both read the tape's aggressor tag. The label names the window, because a
  // 21-bar sum and a 55-bar EMA of the same delta are different indicators; the
  // divergence count and the last one's strength only appear once the pane is up
  // (with it collapsed the pairing still runs, but reporting a find nobody can
  // look at is how a reading aid turns into a signal).
  row(
    "cvdOsc",
    present.cvd,
    `${N.cvdOsc} · ${cvdOscParams.mode === "ema" ? "EMA" : "periodic"} ${
      cvdOscParams.period
    } · fractal ${cvdOscParams.fractalN}` +
      (vis.cvdOsc && cvdOscRead.divs > 0
        ? ` · ${cvdOscRead.divs} divergence${cvdOscRead.divs === 1 ? "" : "s"} · last ${cvdOscStrengthLabel(
            cvdOscRead.strength,
          )}`
        : ""),
    palette.orange,
    false,
    { title: N.cvdOsc, fields: cvdOscKnobs(cvdOscParams, patchCvdOsc) },
  );
  // The bar-range vol pane. The label names the read, because "ATR" alone is
  // exactly the graph-without-a-number this pane exists to replace.
  row(
    "volRuler",
    present.bars,
    `${N.volRuler} · median bar range vs the ${VR_STOP_TICKS}t stop`,
    palette.gold,
  );
  // The composite gets a row once there are days to build it from — the rule
  // itself sits on the row. How many days went in is a reading under the balance
  // rule (this is how long the auction has been running), so the label says it.
  row(
    "compositeProfile",
    ctxDays > 0,
    compDays > 0
      ? `${N.compositeProfile} · ${compDays} prior session${compDays === 1 ? "" : "s"} · VAH/POC/VAL`
      : `${N.compositeProfile} · off · ${ctxDays} prior day${ctxDays === 1 ? "" : "s"} loaded`,
    legendInk.composite.poc,
    compDays === 0,
  );
  // The nodes read off it — only once there is a composite for them to be read
  // off. Their own switch is the prominence floor, which the NY-nodes row above
  // carries too, so nothing is stranded when this row isn't here.
  row(
    "compositeNodes",
    ctxDays > 0 && compDays > 0,
    nodeProm > 0
      ? `${N.compositeNodes} · HVN/LVN at ${Math.round(nodeProm * 100)}% prominence`
      : `${N.compositeNodes} · off`,
    legendInk.composite.hvn,
    nodeProm === 0,
  );
  // Same again for the events: the ten knobs hang off these two rows, so they are
  // here from the first bar. Each count is quoted with the threshold it was
  // counted at — one without the other is a number that means nothing, since a
  // strength is only ever in units of what selected it.
  const evT = eventOverlay?.tuning;
  row(
    "sweepBursts",
    present.bars && !!evT,
    evT ? `${N.sweepBursts} · ≥${evT.burstLots} lots · ${evCount.sweep}` : N.sweepBursts,
    palette.orange,
  );
  row(
    "absorption",
    present.bars && !!evT,
    evT
      ? `${N.absorption} · ≥${evT.absorbMult}× ${evT.absorbBaseline > 0 ? `the last ${evT.absorbBaseline}` : "the session's own"} · ${evCount.absorb}`
      : N.absorption,
    palette.blue,
  );
  // Only once something has been traded — a row for an empty session would be a
  // toggle for nothing.
  row("replayTrades", tradeCount > 0, `${N.replayTrades} · ${tradeCount} closed`, palette.green);

  const legendItems: LegendItem[] = rows.filter((r) => r.available);

  // The community studies, as legend rows. Built here rather than handed down by
  // the page because everything they need is already in this component — the
  // specs, what the layer managed to draw, and the panel the settings open in.
  // The page only hears about an edit (see `onStudiesChange`), the same way it
  // hears about a bucketing change.
  const studyRows: StudyRow[] = (studies ?? EMPTY_STUDIES).map((spec) => {
    const entry = findStudy(spec.key);
    const rep = studyReport.find((r) => r.id === spec.id);
    const edit = (next: StudySpec) =>
      onStudiesRef.current?.(
        (studies ?? EMPTY_STUDIES).map((sp) => (sp.id === spec.id ? next : sp)),
      );
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
      onRemove: () =>
        onStudiesRef.current?.((studies ?? EMPTY_STUDIES).filter((sp) => sp.id !== spec.id)),
    };
  });

  // The same list, as the topbar catalogue needs it: every layer, on or off,
  // drawable or not yet. Published rather than lifted — visibility is this pane's
  // (see `vis`), and a page holding a second copy of it is a page that can
  // disagree with the chart. Same shape as `onToolsChange`: the chart owns the
  // state and says what it is.
  layerStatesRef.current = rows.map((r) => ({
    key: r.key as ReplayLayerKey,
    on: vis[r.key],
    available: r.available,
  }));
  // Keyed on the contents, not the array: this is rebuilt on every render, and
  // the page turns it into state.
  const layerSig = layerStatesRef.current
    .map((l) => `${l.key}${l.on ? 1 : 0}${l.available ? 1 : 0}`)
    .join(",");
  useEffect(() => {
    onLayersRef.current?.(layerStatesRef.current);
  }, [layerSig]);

  // Hang the page's knobs on whichever rows they belong to. Done in one pass at
  // the end rather than at each push: which row a setting goes on is the page's
  // statement, not this list's, and repeating the lookup twelve times above
  // would only make it easy to forget one.
  if (indicatorSettings)
    for (const it of legendItems) {
      const spec = indicatorSettings[it.key];
      if (spec) it.settings = spec;
    }

  // The ticket, built once and hung in one of two places below — off the ＋ on a
  // mouse, at the foot of the chart on a fingertip. Which one is a layout
  // decision (see DOCK_MENU); the panel itself is the same panel.
  const menuEl =
    plusPrice == null || !menuOpen ? null : (
      <OrderMenu
        price={plusPrice}
        mark={markProp}
        tick={tickSize}
        tickUsd={tickUsd}
        ticket={tkt}
        docked={DOCK_MENU}
        onTicket={(t) => onTicketChange?.(t)}
        onNudge={(d) =>
          setPlusPrice((p) => (p == null ? p : Math.round((p + d) / tickSize) * tickSize))
        }
        onPlace={(o) => {
          onPlaceTyped?.(o);
          closePlus();
        }}
        onClose={closePlus}
      />
    );

  return (
    <div
      ref={rootRef}
      // With the tools out on a page rail, the legend and the placement banners
      // get the left edge back — they were only ever clearing a rail that is no
      // longer inside the canvas. A class rather than an inline custom property,
      // because an *unpinned* page rail floats back over this corner and the
      // page has to be able to say so; an inline value would outrank it.
      className={onToolsChange ? "chart-no-rail" : undefined}
      // A press anywhere in the pane claims the page's focus — the canvas, the
      // legend, a badge, the ◎. On the root rather than on the canvas because
      // the overlays are siblings of it, and "I clicked this pane" should not
      // depend on which part of it you happened to hit. Capture phase, so a
      // control that stops propagation still hands focus over first.
      onPointerDownCapture={() => onFocusRef.current?.()}
      style={{ position: "relative", width: "100%", height: "100%", minHeight: 0 }}
    >
      {/* What the two buttons mean while Space is down. The mapping flips across
          the market, so this is worth saying on screen rather than in a tooltip
          you can't read with a modifier held. */}
      {spaceHeld && (
        <div
          style={{
            position: "absolute",
            top: 8,
            // Top-centre — the spot the OHLC readout vacated when it moved into
            // the legend. The top-left corner is the pane's identity block now,
            // and a transient must not cover the thing that says which chart
            // you are about to place an order on.
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 3,
            display: "flex",
            gap: 8,
            padding: "5px 9px",
            borderRadius: 6,
            background: "rgba(14, 17, 23, 0.86)",
            border: `1px solid ${palette.cardBorder}`,
            fontSize: 11,
            lineHeight: 1,
            pointerEvents: "none",
          }}
        >
          <span style={{ color: palette.muted }}>Click a price —</span>
          <span style={{ color: palette.blue }}>left: limit</span>
          <span style={{ color: palette.orange }}>right: stop</span>
        </div>
      )}
      {/* The tool's version of the same banner. The two channels are the two
          mouse buttons a touchscreen doesn't have, so here they are the choice
          itself rather than a caption about one — pick a side, then tap a price.
          Same corner as the Space banner, because it is the same message. */}
      {orderArmed && (
        <div className="replay-order-armed">
          <button
            type="button"
            className={`replay-side${orderSide === "left" ? " on" : ""}`}
            style={{ color: orderSide === "left" ? palette.blue : palette.muted }}
            onClick={() => setOrderSide("left")}
            aria-pressed={orderSide === "left"}
          >
            limit
          </button>
          <button
            type="button"
            className={`replay-side${orderSide === "right" ? " on" : ""}`}
            style={{ color: orderSide === "right" ? palette.orange : palette.muted }}
            onClick={() => setOrderSide("right")}
            aria-pressed={orderSide === "right"}
          >
            stop
          </button>
          <span style={{ color: palette.muted }}>— now tap a price</span>
        </div>
      )}
      {/* The crosshair readout lives inside the legend now (one identity block,
          top-left) — see IndicatorLegend and the `ohlcRef` handed to it. */}
      {/* A price line was just crossed. Centred rather than cornered: it is the
          one transient here that can fire while you are looking anywhere. */}
      {alertFlash && (
        <div key={alertFlash.key} className="chart-alert-flash">
          🔔 {alertFlash.price.toFixed(2)} crossed
        </div>
      )}
      {/* The in-canvas rail. Not rendered at all once the page draws one of its
          own (`onToolsChange`): the same buttons twice would be two places
          claiming to say what is armed, and only one can be right — and a hidden
          copy is still a second `[data-tip^="Horizontal line"]` for anything
          looking the tools up by what they say. The banners that used to clear
          this rail read `--chart-rail`, which the root sets to 0 when it goes. */}
      {!onToolsChange && (
      <ChartTools armed={orderArmed || armed || rulerArmed || avwapArmed || hlineArmed}>
        {/* Only where the modifier isn't available. On a mouse Space+click is
            strictly the better gesture — nothing to arm, nothing left armed —
            and a button that duplicates it would just be a slower way in. */}
        {COARSE_POINTER && (
          <ChartToolButton
            icon="🧾"
            label="＋ Order"
            on={orderArmed}
            onClick={() => armOrder(!orderArmed)}
            disabled={!canPlaceOrders}
            title={
              canPlaceOrders
                ? orderArmed
                  ? "Pick limit or stop, then tap a price (Esc to cancel)"
                  : "Place an order — pick limit or stop, then tap a price. The desktop gesture is Space + click."
                : "Load a session first"
            }
          />
        )}
        <ChartToolButton
          icon="📊"
          label={armed ? "Drag a range…" : "Fixed range VP"}
          on={armed}
          onClick={() => arm(!armed)}
          title={
            armed
              ? "Drag across the chart to profile that range — end on the last bar to keep it following the tape (Esc to cancel)"
              : "Fixed-range volume profile — drag across a range to profile it, and it draws how its POC/VAH/VAL developed across that stretch. End the drag on the last bar and it keeps taking in new bars. Drag its edges to resize, its body to move, Del to remove."
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
              : "Anchored VWAP — click any bar to draw a VWAP + ±1σ/±2σ bands from that point forward. Keeps developing as the replay runs; σ is tick-derived, like the session anchors. Click again to re-anchor."
          }
        />
        <ChartToolButton
          icon="━"
          label={hlineArmed ? "Click a price…" : "Price line"}
          on={hlineArmed}
          onClick={() => armHline(!hlineArmed)}
          title={
            hlineArmed
              ? "Click a price to put a line there (Esc to cancel)"
              : "Horizontal line — click a price to mark it. The tape crossing it chimes once and the line dims; drag it to move and re-arm it, Del to remove."
          }
        />
        {/* Below the hairline: the tools that take things away. They come and go
            with what is on the chart, so they live at the foot of the rail where
            appearing doesn't move anything above them. */}
        {(avwapAnchor != null ||
          selected != null ||
          selectedHline != null ||
          ranges.length + hlines.length > 1) && <ChartToolSep />}
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
        {selectedHline != null && (
          <ChartToolButton
            icon="🗑"
            label="Delete"
            onClick={deleteSelectedHline}
            title="Remove this price line (Del)"
          />
        )}
        {ranges.length + hlines.length > 1 && (
          <ChartToolButton
            icon="🧹"
            label="Clear all"
            onClick={() => {
              clearRanges();
              clearHlines();
            }}
            title="Remove every fixed-range profile and price line"
          />
        )}
      </ChartTools>
      )}
      {/* The pane's badges, top-right: what it is doing that isn't visible in
          the candles. Both of them answer a question you would otherwise have to
          hold in your head across four charts — is this one scrolling with the
          others, and where would an order placed here go. */}
      {(onLinkedChange || routedTo) && (
        <div className="chart-badges">
          {routedTo && (
            <span
              className="chart-badge routed"
              title={`Orders from this chart are routed to ${routedTo}, not to the contract the tape is on`}
            >
              → {routedTo}
            </span>
          )}
          {onLinkedChange && (
            <button
              type="button"
              className={`chart-badge link${linked ? " on" : ""}`}
              onClick={() => onLinkedChange(!linked)}
              aria-pressed={linked}
              title={
                linked
                  ? "Linked — this pane shares the crosshair and the right edge with the others. Click to read it on its own."
                  : "Unlinked — this pane scrolls on its own. Click to rejoin the others."
              }
            >
              ⇄
            </button>
          )}
        </div>
      )}
      {/* Back to the price. The opposite corner from the tool rail and just
          inside the price axis, because that is the corner the newest bar is in
          — the button is where you are already looking when you notice the tape
          has gone. Lit while it has. */}
      <button
        type="button"
        className={`chart-jump${offTape ? " on" : ""}`}
        onClick={jumpToPrice}
        disabled={!present.bars}
        data-tip="Back to the price — frame the newest bars and zoom the scale to them (double-click the axis for autoscale)"
        aria-label="Back to the price"
      >
        ◎
      </button>
      {/* What the scale stopped waiting for. A demoted layer draws at its true
          price, so when it is far from the tape it simply leaves the pane — and
          off screen looks exactly like not-there, which is the one real cost of
          not letting it hold the scale open. These name the nearest one each way
          and how far under or over the last trade it is, so a weekly band 800
          points down is a number you can read instead of a line you have to go
          hunting for. Nothing to press — they hover for the level's actual price
          and do nothing else; the ◎ below them is still the only thing in this
          corner that changes the view. */}
      {edges.up && (
        <div
          className="chart-edge up"
          title={`${edges.up.label} at ${edges.up.price.toFixed(2)} — ${Math.round(
            edges.up.dist,
          )} points above the last trade, drawn but off the top of the pane${
            edges.nUp > 1 ? `. ${edges.nUp - 1} more above it.` : ""
          }`}
        >
          ▲ {edges.up.label} {Math.round(edges.up.dist)}
          {edges.nUp > 1 && <span className="chart-edge-n">+{edges.nUp - 1}</span>}
        </div>
      )}
      {edges.down && edges.paneH > 0 && (
        <div
          className="chart-edge down"
          style={{ top: edges.paneH - 30 }}
          title={`${edges.down.label} at ${edges.down.price.toFixed(2)} — ${Math.round(
            edges.down.dist,
          )} points below the last trade, drawn but off the bottom of the pane${
            edges.nDown > 1 ? `. ${edges.nDown - 1} more below it.` : ""
          }`}
        >
          ▼ {edges.down.label} {Math.round(edges.down.dist)}
          {edges.nDown > 1 && <span className="chart-edge-n">+{edges.nDown - 1}</span>}
        </div>
      )}
      <div ref={elRef} style={{ width: "100%", height: "100%", minHeight: 0 }} />
      {/* The long-press ＋, riding the price axis at the price it was summoned
          at, with the ticket hanging off its left. Deliberately a sibling of the
          canvas rather than something drawn into it: it is a menu with typed
          fields, and the press that dismisses it must not be a press the chart's
          own handlers ever see. Positioned by the effect above, not by React. */}
      {plusPrice != null && (
        <>
          <div
            ref={anchorRef}
            className={`replay-anchor${menuOpen && !DOCK_MENU ? " open" : ""}`}
            // The ＋ and the level beside it are the one thing on this chart the
            // platform can mistake for text: they appear under a finger that is
            // still held down, which is exactly when Android goes looking for
            // something to offer copy/select on.
            onContextMenu={(e) => e.preventDefault()}
          >
            {menuOpen && !DOCK_MENU && menuEl}
            {/* The level, readable before you commit to it — the point of making
                this two steps rather than one. An undocked menu prints it itself
                and takes this row's place; a docked one is at the other end of
                the chart, so the axis keeps saying which price it is for. */}
            {(!menuOpen || DOCK_MENU) && (
              <span className="replay-plus-px">{plusPrice.toFixed(2)}</span>
            )}
            <button
              type="button"
              className="replay-plus"
              onClick={() => setMenuOpen((o) => !o)}
              aria-expanded={menuOpen}
              aria-label={`Order at ${plusPrice.toFixed(2)}`}
              title={menuOpen ? "Close the ticket (Esc)" : `Order at ${plusPrice.toFixed(2)}`}
            >
              ＋
            </button>
          </div>
          {/* The docked home: the foot of the chart, clear of whatever the page
              has already parked down there (--chart-floor). Nothing about it is
              pinned to the price, so unlike the anchor it is positioned by CSS
              alone. */}
          {menuOpen && DOCK_MENU && (
            <div className="replay-omenu-dock" onContextMenu={(e) => e.preventDefault()}>
              {menuEl}
            </div>
          )}
        </>
      )}
      {levelPanel && (
        <LevelApproach
          rows={levelRows}
          window={`${GAP_LOOKBACK_BARS} × ${tfLabel ?? "bars"}`}
          open={levelOpen}
          onToggle={() => {
            const next = !levelOpen;
            setLevelOpen(next);
            saveLevelPanelOpen(next);
          }}
          containerRef={levelBoxRef}
          arms={arms}
          onArm={onArmToggle}
          reach={levelReach}
          onReach={onArmToggle ? () => setLevelReach((v) => !v) : undefined}
          race={armRace}
          onRace={onArmRace}
        />
      )}
      <IndicatorLegend
        items={legendItems}
        studies={studyRows}
        visibility={vis}
        onToggle={toggle}
        appearance={appearanceSettings(appearance, changeAppearance)}
        prefsPane={prefsPane}
        symbol={symbol}
        tfLabel={tfLabel}
        tfOptions={tfOptions}
        onTfChange={onTfChange}
        routedTo={routedTo}
        ohlcRef={ohlcRef}
      />
    </div>
  );
});
