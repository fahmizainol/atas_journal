// The chart's own knobs, hung off the legend row each one tunes — built here so
// Replay and Live hand `ReplayChart` the *same* panels for the same layers.
// This used to live inline in the Simulator, which made it a page's furniture;
// it is the chart's, and the day Live grew the layers it had to grow the knobs
// too or be a second, dumber copy of the same surface.
//
// The state stays on the page (it is what the chart's props are fed from);
// what's built here is a presentation of that state, never a second copy of it.
// Two of these knobs feed two rows each — the prominence floor and the shared
// event styling — and the notes on the fields say so.

import type { IndicatorSettingsMap } from "./IndicatorLegend";
import type { SettingField } from "./IndicatorSettings";
import type { EventTuning } from "../../lib/replayEngine";
import type { CompositeRule, CompositeSpan } from "../../lib/compositeProfile";
import {
  SHELF_HOLD_OPTIONS,
  SHELF_WINDOW_OPTIONS,
  SHELF_ZMIN_OPTIONS,
  VWAP_BAND_OPTIONS,
  VWAP_FILL_OPTIONS,
  VWAP_FILL_REGION_OPTIONS,
  type DeltaLaneKnobs,
  type VwapBandChoice,
  type VwapFillRegion,
} from "../../lib/chartPrefs";
import {
  HTF_FRAME_SETS,
  HTF_LENGTHS,
  type HtfTint,
  type HtfTrendParams,
} from "../../lib/htfTrend";
import type { ShelfParams } from "../../lib/volumeShelf";
import {
  EXTERNAL_PERIOD_OPTIONS,
  type ExternalChartParams,
  type ExternalPalette,
} from "../../lib/externalChart";
import {
  RZ_ABSORB_OPTIONS,
  RZ_BREAK_OPTIONS,
  RZ_FILTER_OPTIONS,
  RZ_RANK_OPTIONS,
  RZ_KEEP_BROKEN_OPTIONS,
  RZ_MIN_SWING_OPTIONS,
  RZ_SPAN_OPTIONS,
  RZ_STORED_OPTIONS,
  RZ_TREND_LEN_OPTIONS,
  RZ_VISIBLE_OPTIONS,
  RZ_VOL_LEN_OPTIONS,
  RZ_WIDTH_OPTIONS,
  type RankedZonesParams,
} from "../../lib/rankedZones";
import type { ShelfField } from "./VolumeShelfPrimitive";
import { FORWARD_BARS, type LaneScale, type LaneWindow } from "../../lib/deltaFlow";
import {
  BIG_LOT_OPTIONS,
  EVENT_FILL_OPTIONS,
  EVENT_FLOOR_OPTIONS,
  EVENT_LABEL_ST_OPTIONS,
  EVENT_TUNING_OPTIONS,
  NODE_PROM_OPTIONS,
} from "../../lib/simPrefs";
import {
  MV_ANCHOR_OPTIONS,
  MV_ATR_LEN_OPTIONS,
  MV_BAND_OPTIONS,
  MV_HOLD_OPTIONS,
  MV_KER_LEN_OPTIONS,
  MV_KER_WEIGHT_OPTIONS,
  MV_OCC_WINDOW_OPTIONS,
  MV_PIVOT_OPTIONS,
  MV_POC_SOURCE_OPTIONS,
  MV_REARM_MODE_OPTIONS,
  MV_REARM_OPTIONS,
  MV_REGIME_LEN_OPTIONS,
  MV_SIGNAL_OPTIONS,
  mvOccMinOptions,
  type ModernVwapParams,
} from "../../lib/modernVwap";
import {
  DSV_ANCHOR_OPTIONS,
  DSV_APT_OPTIONS,
  DSV_BAND_OPTIONS,
  DSV_BAND_SCOPE_OPTIONS,
  DSV_BIAS_OPTIONS,
  DSV_FLAG_OPTIONS,
  DSV_PAST_ALPHA_OPTIONS,
  DSV_SWING_OPTIONS,
  DSV_WEIGHTING_OPTIONS,
  type DsvParams,
} from "../../lib/dynamicSwingVwap";
import {
  CVD_OSC_FRACTAL_OPTIONS,
  CVD_OSC_MODE_OPTIONS,
  CVD_OSC_PERIOD_OPTIONS,
  type CvdOscMode,
  type CvdOscParams,
} from "../../lib/cvdOsc";

/** The event layer's knobs, as one bundle: what selects the bands (the tuning,
 *  which re-derives the tape) and how they draw (a repaint). Absent on a page
 *  that doesn't offer the layer, and the two legend rows then get no panels. */
export interface EventKnobs {
  tuning: EventTuning;
  labelSt: number;
  /** The wash and the draw-floor are per kind: with both kinds on, quieting one
   *  without toggling it off is what keeps the overlap readable. */
  fillSweep: number;
  fillAbsorb: number;
  floorSweep: number;
  floorAbsorb: number;
  marginal: boolean;
  onTuning: (patch: Partial<EventTuning>) => void;
  onLabelSt: (v: number) => void;
  onFillSweep: (v: number) => void;
  onFillAbsorb: (v: number) => void;
  onFloorSweep: (v: number) => void;
  onFloorAbsorb: (v: number) => void;
  onMarginal: (v: boolean) => void;
}

export interface ChartKnobsConfig {
  bigLots: number;
  onBigLots: (lots: number) => void;
  nodeProm: number;
  onNodeProm: (p: number) => void;
  /** The composite's rule, span and the line under the picker saying where the
   *  day-count knob lives — the pages load their context differently, so each
   *  writes its own note. Absent on a chart that loads no prior days at all: the
   *  composite is a fact about the days *before* the tape, and where there are
   *  none the chart draws no composite row for these knobs to hang off. */
  composite?: {
    rule: CompositeRule;
    onRule: (r: CompositeRule) => void;
    span: CompositeSpan;
    onSpan: (s: CompositeSpan) => void;
    note: string;
  };
  events?: EventKnobs;
  /** Modern VWAP's parameters, as one object because the indicator takes them as
   *  one. Absent on a page that doesn't offer the layer. */
  modernVwap?: {
    params: ModernVwapParams;
    onChange: (patch: Partial<ModernVwapParams>) => void;
  };
  /** The Zeiierman swing-flip VWAP's parameters, on the same terms as above.
   *  Absent on a page that doesn't offer the layer. */
  dynamicSwingVwap?: {
    params: DsvParams;
    onChange: (patch: Partial<DsvParams>) => void;
  };
  /** The volume shelves' window and thresholds, on the same terms as above.
   *  Absent on a page that doesn't offer the layer. */
  volumeShelf?: {
    params: ShelfParams;
    onChange: (patch: Partial<ShelfParams>) => void;
    field: ShelfField;
    onField: (f: ShelfField) => void;
  };
}

/**
 * The volume profile's knobs: whether a net-delta lane is drawn beside it, and —
 * once it is — what that lane is being asked.
 *
 * A pane knob rather than a page one, like the anchor knobs below and for the
 * same reason — the preferences are the chart's, sticky and global
 * (`chart.profileDelta`, `chart.deltaLane`), and no host page needs to learn
 * about them for the histogram to be adjustable. Both charts that draw a profile
 * build this, so the Lab's and the replay's cannot end up offering it in two
 * different wordings.
 *
 * The three reading knobs are emitted only while the lane is on, the same rule
 * the composite's span follows below: a panel of knobs for a layer that isn't
 * drawn is three questions about nothing.
 */
export function volumeProfileKnobs(
  delta: boolean,
  onDelta: (v: boolean) => void,
  /** Whether this chart has a delta to draw: false on a session with no
   *  aggressor tag (and on the journal's estimated profile, which has no tape at
   *  all). The knob stays — switching it on ahead of a tape that has one is
   *  legitimate — and says so instead of drawing an empty lane. */
  available = true,
  /** How the lane is read, and how to change it. `canClassify` is false where
   *  the chart can offer no time axis to measure a verdict against. */
  lane?: {
    value: DeltaLaneKnobs;
    onChange: (patch: Partial<DeltaLaneKnobs>) => void;
    canClassify?: boolean;
  },
): SettingField[] {
  const fields: SettingField[] = [
    {
      key: "profileDelta",
      label: "Delta lane",
      help: "Draw net delta at price as a second histogram beside the volume one — green where buy market orders outweighed sells at that price, red where sells outweighed buys, and the bar's length is how far net. Its own lane rather than a colour on the volume rows, because the two are different distributions and the prices worth finding are the ones where they disagree: heavy volume that netted out to nothing, or a thin shelf that was bought one-way. The volume profile itself is untouched: same rows, same widths, same value-area fill. Applies to the fixed-range tool too, where the lane sits on the other side because that histogram grows the other way. Switch it on and three more knobs appear, for what the lane is measured against and which of its rows get marked.",
      value: delta ? 1 : 0,
      options: [
        { value: 0, label: "off" },
        { value: 1, label: "on" },
      ],
      onChange: (v) => onDelta(Number(v) === 1),
      note: available
        ? undefined
        : "No aggressor tag on this session's ticks — there is no delta to draw, so the lane stays empty.",
    },
  ];
  if (!delta || !lane) return fields;

  const inVisit = lane.value.window === "visit";
  fields.push({
    key: "deltaLaneWindow",
    label: "Lane window",
    help: "What stretch of tape the lane's delta is read from. 'session' is the lane as it has always been: one cumulative number per row over everything on screen — which by the afternoon is mostly a record of the morning, since a row's biggest hour dominates its total forever after. '30 min' and '15 min' read only the trailing window ending at the newest bar: the volume rows keep the full span (the structure you are trading against), while the lane answers who is aggressing at these prices now — lengths, colours, flags and verdicts all come from the window's own trading, so a row that was bought on the session but is being sold this half hour draws red, at the window's length. 'this visit' cuts by price path instead of by clock: each row splits into the delta of its latest contiguous visit — for rows price is in now, the ongoing one — drawn solid, over everything earlier drawn washed behind it. The read that mode exists for is the retest whose flow disagrees with the standing lean ('bought all morning, being sold on this return'), and rows where the two sides disagree get the solid end-cap. Applies to the viewport lane only: a fixed-range profile's span is the window you drew it over, so the tool keeps reading exactly that.",
    value: lane.value.window,
    options: [
      { value: "session", label: "session" },
      { value: "m30", label: "30 min" },
      { value: "m15", label: "15 min" },
      { value: "visit", label: "this visit" },
    ],
    onChange: (v) => lane.onChange({ window: v as LaneWindow }),
  });

  fields.push({
    key: "deltaLaneScale",
    label: "Lane scale",
    help: "What a delta bar's length is measured against. 'net' is the lane as it has always been drawn: the raw imbalance in contracts, scaled to the biggest one on the chart. It answers how many net lots traded at a price, which means a heavy row nearly always out-draws a thin one even when the thin one is the one that traded one-way — a 5000-lot row that netted +300 draws longer than a 200-lot row that netted +150. 'imbalance' divides each row's delta by its own volume, so the bar is how *one-sided* the row was, from flat to entirely one way, and the thin one-way shelf finally out-draws the heavy two-sided one — at the cost that a row which caught four contracts and happened to catch them all one way now draws at full length. 'z-score' corrects for exactly that: it divides by the square root of the row's volume instead, which is roughly how far a row of that size would swing on chance alone, then standardises across the chart. Its bar is how unusual the row is given how much traded there, and it is the one the flag below agrees with. Changing this changes only the drawing: the rows, the volume histogram and which rows are flagged are identical in all three.",
    value: lane.value.scale,
    options: [
      { value: "net", label: "net" },
      { value: "imbalance", label: "imbalance" },
      { value: "zscore", label: "z-score" },
    ],
    onChange: (v) => lane.onChange({ scale: v as LaneScale }),
    note: inVisit
      ? "Idle while the window is 'this visit': that lane's two segments are net contracts against one shared denominator by construction — a z-score doesn't decompose into per-visit parts."
      : undefined,
  });

  fields.push({
    key: "deltaLaneFlag",
    label: "Flag rows",
    help: "Mark the rows that traded further one-way than the rest of this chart did — a notch on the delta bar, so which rows are worth a second look is a threshold rather than a judgement about bar lengths. The measure is each row's delta over the square root of its volume, standardised across the chart. The square root is load-bearing and not a detail: a row that caught four contracts can be entirely one-sided on a coin toss, while a row that caught four thousand cannot, so ranking rows on the plain imbalance ratio finds thin rows rather than one-sided ones — it was measured doing exactly that, putting every flag on a top-or-bottom tick nobody cares about. Dividing by the square root of volume puts a heavy row and a thin one on the same footing. The test runs on that same measure whichever scale the lane is drawn at, so a row flagged in 'net' view is the same row flagged in 'imbalance' view: the flag is a claim about the row, and it must not change meaning because you changed how long the bars are. The spread it is judged against is a median absolute deviation rather than a standard deviation, so the one row that ran hardest cannot widen the ruler enough to hide the merely-unusual rows behind it. Rows holding less than a hundredth of the busiest row's volume are not candidates at all: six contracts at the last tick of the day are 'entirely one-sided' on every measure and are still six contracts. They keep their bar in the lane; they just never get a mark. ±2σ is the usual starting point and names a handful of rows on a typical session; ±1.5σ for the shoulders, ±2.5σ for only the extremes. A session whose rows all lean about equally has nothing standing out from and flags nothing at any setting, which is itself the reading.",
    value: lane.value.flagSigma,
    options: [
      { value: 0, label: "off" },
      { value: 1.5, label: "±1.5σ" },
      { value: 2, label: "±2σ" },
      { value: 2.5, label: "±2.5σ" },
    ],
    onChange: (v) => lane.onChange({ flagSigma: Number(v) }),
    note: inVisit
      ? "Idle while the window is 'this visit': the cap there marks a flip — a latest visit leaning against the standing delta — not a σ outlier."
      : undefined,
  });

  const canClassify = lane.canClassify !== false;
  fields.push({
    key: "deltaLaneClassify",
    label: "Classify",
    help: `Give each flagged row a verdict: did the aggressors who piled in there get paid, or were they absorbed? The lane alone cannot tell you — heavy one-way buying that lifted price and heavy one-way buying that a passive seller ate look identical as a bar, and which one it was is the whole reading. So this finds the bar that contributed most of the row's imbalance, looks ${FORWARD_BARS} bars on from it, and asks where price went in the direction that row was pushing. Past half the window's median bar range and it is marked initiative (▲); short of that, absorbed (◆) — someone was on the other side. The yardstick is the window's own bar range rather than a fixed number of ticks, so the verdict means the same thing on a quiet morning as on a fast one. Rows too close to the right edge to look forward from get no mark, since measuring against a bar that hasn't happened would answer with the end of the chart instead of with the market. Needs the flag threshold above to be on — it classifies flagged rows, not every row. Read it as context, not as a signal: it is a description of what already happened, and nothing here has been tested as an edge.`,
    value: canClassify && lane.value.classify ? 1 : 0,
    options: [
      { value: 0, label: "off" },
      { value: 1, label: "on" },
    ],
    onChange: (v) => lane.onChange({ classify: Number(v) === 1 }),
    note: !canClassify
      ? "This chart's profile has no per-bar tape behind it, so there is no time axis to measure a verdict against."
      : inVisit
        ? "Idle while the window is 'this visit': a flip is not an outlier, and racing it forward would be a different study."
        : lane.value.flagSigma === 0
          ? "Nothing to classify while 'Flag rows' is off — it marks flagged rows only."
          : undefined,
  });

  return fields;
}

/** A fixed session anchor's two knobs: which σ rings it draws, and how heavily
 *  the region between them is washed in. Built here rather than on either chart
 *  because both draw the same three anchors from the same sticky preferences, and
 *  an envelope cut differently on the Lab's chart than on the replay's is two
 *  charts disagreeing about one band.
 *
 *  The caller supplies the panel title naming which anchor these belong to. */
export function vwapAnchorKnobs(
  value: { bands: VwapBandChoice; fill: number; region: VwapFillRegion },
  set: {
    bands: (v: VwapBandChoice) => void;
    fill: (v: number) => void;
    region: (v: VwapFillRegion) => void;
  },
): SettingField[] {
  const inner = value.region === "inner";
  const fillIdle = inner ? value.bands === "s2" || value.bands === "none" : value.bands !== "both";
  return [
    {
      key: "vwapBands",
      label: "Bands",
      help: "Which σ rings this anchor draws. The mid is not on the list — it is the anchored VWAP, and the row's own eye is how you turn the whole thing off. Both halves of a ring go together: nobody reads +1σ without −1σ, that is one envelope drawn on both sides of the mid. Worth cutting when all three fixed anchors are up, which is twelve dashed lines through the price action for a session most people read off one ring. The ±1σ→±2σ wash needs both rings to be an honest fill, so it is drawn only at 'both' — shade up to a ±2σ line you asked to hide and the wash draws it back in as a colour edge. Per anchor, and sticky across every chart that draws it.",
      value: value.bands,
      options: VWAP_BAND_OPTIONS.map((c) => ({ value: c, label: VWAP_BAND_LABEL[c] })),
      onChange: (v) => set.bands(v as VwapBandChoice),
    },
    {
      key: "vwapFill",
      label: "Band fill",
      help: "How heavily the region between this anchor's ±1σ and ±2σ lines is shaded. The reading is the envelope — where price sits against it — and the fill only says which side of the σ line you are on, so it is the part that can be turned down without losing anything. Worth turning down when all three fixed anchors are up at once: three washes over the same candles compound into a tint the candles have to be read through, and the weekly's is the widest of them. 'Off' keeps the lines and draws no fill at all. The dashed σ lines and the mid are untouched at every setting, and the choice is per anchor and sticky across every chart that draws it. The region it covers is the knob below.",
      value: value.fill,
      options: VWAP_FILL_OPTIONS.map((w) => ({ value: w, label: VWAP_FILL_LABEL[w] })),
      onChange: (v) => set.fill(Number(v)),
      note: fillIdle
        ? inner
          ? "Idle: the inner wash runs −1σ→+1σ and needs the ±1σ ring up."
          : "Idle: the outer wash runs ±1σ→±2σ and needs both rings up."
        : undefined,
    },
    {
      key: "vwapFillRegion",
      label: "Fill region",
      help: "Which part of the envelope the wash covers. 'Outer' is how it has always been drawn: the two ±1σ→±2σ ribbons, with the mid-to-±1σ left clear so the mid stays readable — the fill says price is stretched. 'Inner' flips it: one ribbon from −1σ to +1σ, the anchor's value area, with the ±2σ rings left as plain lines — the fill says price is inside the fair range. The inner wash only needs the ±1σ ring, so it draws at 'both' or '±1σ only'. Weight comes from 'Band fill' either way. Per anchor, and sticky across every chart that draws it.",
      value: value.region,
      options: VWAP_FILL_REGION_OPTIONS.map((r) => ({ value: r, label: VWAP_FILL_REGION_LABEL[r] })),
      onChange: (v) => set.region(v as VwapFillRegion),
    },
  ];
}

const VWAP_FILL_REGION_LABEL: Record<VwapFillRegion, string> = {
  outer: "outer ±1σ→±2σ (default)",
  inner: "inner −1σ→+1σ (value area)",
};

/** Named by what they draw rather than by their key. Keyed off the option values
 *  so a new choice cannot ship without a name. */
const VWAP_BAND_LABEL: Record<VwapBandChoice, string> = {
  both: "±1σ and ±2σ (default)",
  s1: "±1σ only",
  s2: "±2σ only",
  none: "none (mid only)",
};

/** Named by what they look like rather than by their multiplier, which is not a
 *  number anyone reads a chart in. Keyed off the option values so a new step
 *  cannot ship without a name. */
const VWAP_FILL_LABEL: Record<number, string> = {
  1: "full (default)",
  0.6: "dimmed",
  0.3: "faint",
  0: "off (lines only)",
};

/** The Dynamic Swing VWAP's knobs — his four published inputs, plus what the
 *  flags draw. One row rather than Modern VWAP's two, because it names no
 *  triggers: it is a line and the anchors that explain it.
 *
 *  Exported alongside `modernVwapKnobs` and for the same reason: the replay pages
 *  hand their whole knob map to the chart, while CandlestickChart builds its own
 *  legend rows and asks for just this panel. */
/** The CVD oscillator's three knobs — the script's own inputs, and nothing we
 *  invented. Its own builder rather than a branch of `buildChartKnobs` for the
 *  same reason the two VWAPs have theirs: both charts own this state locally
 *  (it is a sticky chart preference, not a page's run config), so both call this
 *  directly with their own setter. */
export function cvdOscKnobs(
  p: CvdOscParams,
  set: (patch: Partial<CvdOscParams>) => void,
): SettingField[] {
  return [
    {
      key: "cvdOscMode",
      label: "Window",
      help: "How the last N bars of delta are accumulated. 'Periodic' is a straight rolling sum — every bar in the window counts the same, and the value is literally 'net contracts lifted minus hit over the last N bars'. 'EMA' weights recent bars more, which reacts faster to a shift in flow at the cost of a number you can no longer read as a contract count. The original offers both and defaults to the sum.",
      value: p.mode,
      options: CVD_OSC_MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => set({ mode: v as CvdOscMode }),
    },
    {
      key: "cvdOscPeriod",
      label: "Period",
      help: "How many bars of delta the window holds. This is what separates the oscillator from the CVD line above it: the line accumulates from the session anchor and drifts, so late in the day a swing's delta rides on hours of history; the window forgets, and zero always means 'the last N bars balanced'. Counts bars, not minutes — 21 is 21 minutes on a 1m chart and 105 on a 5m one.",
      value: p.period,
      options: CVD_OSC_PERIOD_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
      onChange: (v) => set({ period: Number(v) }),
    },
    {
      key: "cvdOscFractal",
      label: "Fractal width",
      help: "How many bars either side a bar must beat to count as a pivot. A pivot is therefore confirmed this many bars late — not lag to be tuned away, but the fact that a high is not a high until the bars after it exist. Wider finds fewer, larger swings and confirms them later. Divergences are only paired between two pivots less than 30 bars apart, and stop being drawn 30 bars after the second one.",
      value: p.fractalN,
      options: CVD_OSC_FRACTAL_OPTIONS.map((n) => ({ value: n, label: `${n} bar${n === 1 ? "" : "s"}` })),
      onChange: (v) => set({ fractalN: Number(v) }),
    },
  ];
}

/** Named by what they look like rather than by their alpha, which is the only
 *  thing about them a reader cares about. Keyed off the option values so a new
 *  step cannot ship without one. */
const PAST_ALPHA_LABEL: Record<number, string> = {
  0.35: "ghosted",
  0.5: "faint (default)",
  0.7: "clear",
  1: "same as live",
};

export function dynamicSwingVwapKnobs(
  p: DsvParams,
  set: (patch: Partial<DsvParams>) => void,
): SettingField[] {
  return [
    {
      key: "dsvSwing",
      label: "Swing period",
      help: "How many bars a bar must be the highest (or lowest) of to *latch* as the swing high (or low). The structure reads bullish while the high latched more recently than the low, bearish the other way, and the line re-anchors when that flips. The latch is the subtle part and it is his: once set it holds until something beats it, so the remembered extreme can be far older than this window — which is why there are so few anchors. Detection needs no bars to the right, so a flip is known on the bar it happens; what lags is the anchor, placed back at the pivot the leg came off, and the segment is redrawn from there. Counts bars, not minutes: 50 is 50 minutes on a 1m chart and 250 on a 5m one.",
      value: p.swingPeriod,
      options: DSV_SWING_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
      onChange: (v) => set({ swingPeriod: Number(v) }),
    },
    {
      key: "dsvAnchorTf",
      label: "Swings on",
      help: "Which bars the swing hunt runs on. Left on the pane's own, every pane counts its own bars — so a 50-bar swing period is 50 minutes on a 1m pane and 250 on a 5m one, the two panes find different swings, and their legs start in different places. Name a timeframe here and the latches, the flips and the pivots all read that bucketing instead, so every pane flags the same swings at the same prices however it is drawn, and 'Swing period' counts those bars. Only the structure moves: each leg still averages the pane's own bars, so the lines agree on where a leg starts rather than on every decimal of where it has got to. A timeframe at or below the pane's own bar changes nothing — there is nothing to group — and the legend says '@' only when it really did.",
      value: p.anchorTf,
      options: DSV_ANCHOR_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => set({ anchorTf: String(v) }),
    },
    {
      key: "dsvWeighting",
      label: "Averaging",
      help: "How each leg averages. 'Decayed' is his and is the indicator: both accumulators forget, so recent price×volume outweighs older and the line tracks price instead of drifting away the way a session VWAP does by lunchtime. 'Plain anchored VWAP' turns that off along with the wick seed, and folds the tape's own prints rather than each bar's hlc3 — so each leg becomes exactly Σ(p×v)/Σv from its pivot bar forward and matches the ⚓ tool anchored on the same candle, value for value, envelope included. The anchors do not move: the latch still decides where legs start and end. It answers one question — whether the decay is telling you anything an ordinary aVWAP off the same swing would not. The three knobs below are unread while it is on.",
      value: p.weighting,
      options: DSV_WEIGHTING_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => set({ weighting: v as DsvParams["weighting"] }),
    },
    // The decay's three knobs exist only while something is decaying.
    ...(p.weighting === "decay"
      ? dsvDecayKnobs(p, set)
      : []),
    {
      key: "dsvBands",
      label: "Envelope",
      help: "Draw σ rings around the live segment. Not part of the published indicator — his draws the line and the swing labels and nothing else — so 'off' is the faithful setting. What is drawn is the second moment of the same accumulator: volume-weighted σ of hlc3 about this leg's own VWAP. Under the decayed averaging it carries the same half-life as the mid line, and two consequences are worth expecting rather than reporting as bugs: the envelope opens pinched at each anchor and flares over the next few bars, because the anchor bar carries only two observations (the wick and its own hlc3); and it re-widens whenever the volatility adjustment shortens the half-life, a shorter memory being a smaller effective sample. Under 'plain anchored VWAP' neither happens — it is the ordinary cumulative band, and it does not pinch at all, because the anchor bar already carries the spread of its own ticks. Which segments get rings is the next knob down.",
      value: p.bands,
      options: DSV_BAND_OPTIONS.map((k) => ({ value: k, label: k === 0 ? "off" : `±${k}σ` })),
      onChange: (v) => set({ bands: Number(v) as 0 | 1 | 2 | 3 }),
    },
    // Only once there is an envelope to place. Which segments get one is not a
    // question about bands that aren't drawn.
    ...(p.bands
      ? [
          {
            key: "dsvBandScope",
            label: "Envelope on",
            help: "Whose σ rings are drawn. 'Live leg only' is the readable setting and answers the usual question — how stretched is price from the leg it is in. 'Every segment' also gives each frozen segment behind it its own rings, which is a different picture rather than more of the same: a hundred overlapping envelopes showing where each past leg's spread stood at the moment it was cut. Dense, and meant for looking rather than reading — the mid lines stay heaviest so they remain findable through it. The ±1σ→±2σ wash is never drawn on the frozen ones; a hundred stacked fills is a smear. This is about segments that have been anchored: the pending shadow, if it is shown, always draws the envelope above whichever setting is chosen here, because it is the live leg you would inherit rather than one of the past ones.",
            value: p.bandScope,
            options: DSV_BAND_SCOPE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
            onChange: (v: string | number) => set({ bandScope: v as DsvParams["bandScope"] }),
          } satisfies SettingField,
        ]
      : []),
    {
      key: "dsvPastLegs",
      label: "Past legs",
      help: "How strongly the superseded segments behind the live line are drawn. They are the point of the indicator rather than a bonus — the live line says where the current leg's average is, the frozen ones are what it peeled away from — but half-lit is the right weight only while they are context. Turn it up when they are what you came to read: at 'same as live' a past leg is exactly as solid as the current one, and only the line weight still says which is which. Their σ rings follow at a fixed share of whatever you pick, so the mids stay findable through them at every setting, and the anchor flags and the pending shadow are untouched.",
      value: p.pastAlpha,
      options: DSV_PAST_ALPHA_OPTIONS.map((a) => ({
        value: a,
        label: PAST_ALPHA_LABEL[a],
      })),
      onChange: (v) => set({ pastAlpha: Number(v) }),
    },
    {
      key: "dsvShadow",
      label: "Pending anchor",
      help: "Ghost in the segment that would appear if the structure flipped on the next bar — dashed, in the colour of the leg it would be, from the anchor it would take. It costs nothing to know: the indicator already tracks two latched extremes, the live segment is anchored at one of them, and the other is by construction where the opposite leg would start. So on a bullish leg the shadow runs from the recent swing high and shows you the bear line you would inherit. Strictly causal — it uses only what has printed — but it is a hypothesis, not a forecast: its anchor can still move forward before any flip arrives, and no flip may come at all. Its flag is drawn hollow for that reason. It carries whatever envelope the live leg has — its own σ measured from its own anchor, which is the other half of what the shadow is for, since a fresh anchor's rings are pinched where the current leg's have long since flared. They keep the dash and stay under the mid; there is no wash on them.",
      value: p.shadow ? 1 : 0,
      options: [
        { value: 1, label: "shown" },
        { value: 0, label: "off" },
      ],
      onChange: (v) => set({ shadow: Number(v) === 1 }),
    },
    {
      key: "dsvFlags",
      label: "Swing flags",
      help: "Mark the bar each anchor was set at, with a stem out to the bar the flip was detected on. Worth leaving on: every segment is drawn as though it always ran from its pivot, so the stem is the only thing on the pane saying that stretch was repainted — that you were not looking at those prices while those bars printed. The labels are the structure read: each pivot against the last one of its own side, so HH/HL come off swing lows and LH/LL off swing highs. A pivot with nothing to compare against, or an exact tie, carries no label, as in his.",
      value: p.flags,
      options: DSV_FLAG_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => set({ flags: v as DsvParams["flags"] }),
    },
  ];
}

/** The half-life and what moves it — read only under the decayed averaging, and
 *  hidden entirely under the plain one, where nothing decays for them to set. */
function dsvDecayKnobs(
  p: DsvParams,
  set: (patch: Partial<DsvParams>) => void,
): SettingField[] {
  return [
    {
      key: "dsvApt",
      label: "Price tracking",
      help: "The half-life, in bars, of the decay on both accumulators — α = 1 − 2^(−1/apt), his formula — and what makes this not a cumulative VWAP. Lower forgets faster and the line rides closer to price; higher smooths and behaves more like an ordinary anchored VWAP within the leg. Pinned to 5–300 and rounded to a whole number before use, as he does, so the tracking speed is a step function and the line kinks slightly where it changes.",
      value: p.apt,
      options: DSV_APT_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
      onChange: (v) => set({ apt: Number(v) }),
    },
    {
      key: "dsvAdapt",
      label: "Adapt tracking",
      help: "Let volatility move the half-life: ATR(50) against its own 50-bar Wilder average, so loud tape shortens the memory and the line hugs price while quiet tape lengthens it and the line smooths. Off in his defaults, which is worth knowing — the indicator most people are looking at is the fixed one, and with this off the line is a plain decayed VWAP re-anchored at flips.",
      value: p.adaptApt ? 1 : 0,
      options: [
        { value: 1, label: "by ATR ratio" },
        { value: 0, label: "fixed" },
      ],
      onChange: (v) => set({ adaptApt: Number(v) === 1 }),
    },
    // The bias only exists when something is being scaled by it.
    ...(p.adaptApt
      ? [
          {
            key: "dsvBias",
            label: "Volatility bias",
            help: "The exponent on the ATR ratio, and his default is 10 — far stronger than it looks. The ratio is a smoothed ATR over its own smoothed average, so it lives near 1, and the tenth power is what turns a few percent of drift into a real change in tracking speed. At 1 it is nearly inert. Either way the result is pinned to 5–300 bars, so the extremes saturate rather than run away.",
            value: p.volBias,
            options: DSV_BIAS_OPTIONS.map((b) => ({ value: b, label: `${b}×` })),
            onChange: (v: string | number) => set({ volBias: Number(v) }),
          } satisfies SettingField,
        ]
      : []),
  ];
}

/** Modern VWAP's knobs — every configurable the demo page carries, minus two
 *  that have no meaning here: its bar-size switch (the chart has its own
 *  timeframe control) and its "compare" line (the chart already draws the
 *  Globex, NY and weekly VWAPs as their own layers, from ticks, better).
 *
 *  Split across the two rows the way the composite's prominence floor is shared:
 *  everything that shapes *the line* is on the line's row, everything that
 *  shapes *the triggers* is on the triggers' row, and both rows carry the anchor
 *  because the anchor is what the triggers are measured against.
 *
 *  Exported as well as used below: the replay pages hand their whole knob map to
 *  the chart, while CandlestickChart builds its own legend rows and asks for
 *  just these two panels. Same knobs either way — that is the point of it being
 *  one function. */
export function modernVwapKnobs(
  p: ModernVwapParams,
  set: (patch: Partial<ModernVwapParams>) => void,
): { line: SettingField[]; signals: SettingField[] } {
  const anchor: SettingField = {
    key: "mvAnchor",
    label: "Anchor",
    help: "Where the accumulator resets. 'Swing pivots' is the construct this indicator exists for — it re-anchors at every confirmed pivot instead of at a clock time, which is the one thing on it we have neither built nor falsified. 'POC touch' is our own candidate under evaluation: it re-anchors where price revisits the developing globex point of control — average price since the market last agreed on value; zero-lag, but in balance raw touches cluster, so mind the re-arm knob. The three clock anchors are here so those two have something to be read against; note they are bar-weighted, while the chart's own Globex/NY/weekly bands accumulate tick by tick and are the better number.",
    value: p.anchor,
    options: MV_ANCHOR_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
    onChange: (v) => set({ anchor: v as ModernVwapParams["anchor"] }),
    note: "Shared with the other Modern VWAP row — the triggers are measured against this line.",
  };
  // Only on a swing anchor: a pivot length is not a question about a clock.
  const pivot: SettingField[] =
    p.anchor !== "swing"
      ? []
      : [
          {
            key: "mvPivot",
            label: "Pivot length",
            help: "How many bars on each side a swing high or low must beat outright to count. Strict on both sides, so the pivot confirms this many bars after it happened and the line steps then — it does not repaint what is already drawn. Longer means fewer, more structural anchors; at 10 on a 5-minute chart a swing wants 50 minutes of confirmation each way.",
            value: p.pivot,
            options: MV_PIVOT_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
            onChange: (v) => set({ pivot: Number(v) }),
          },
        ];
  // And only on a POC anchor: the debounce that keeps balance from shredding
  // the accumulator. On the study page's tape, 'off' anchored every couple of
  // bars whenever price sat at value — the knob is doing all the structural
  // work in this mode, which is exactly the caveat to keep in view.
  const rearm: SettingField[] =
    p.anchor !== "poc"
      ? []
      : [
          {
            key: "mvPocSource",
            label: "POC source",
            help: "Which developing point of control the touch is against. 'Globex session' accumulates from 18:00; 'weekly' carries the profile from Sunday 18:00 across the week (same honest-absence rule as the weekly VWAP — a week with a hole in it anchors nothing, and the legend shows 1⚓). One horizon up the caveats bite harder: a tick re-arm is nearly no debounce against a weekly level, and a weekly POC migration teleports across the week's range.",
            value: p.pocSource,
            options: MV_POC_SOURCE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
            onChange: (v) => set({ pocSource: v as ModernVwapParams["pocSource"] }),
          },
          {
            key: "mvRearmMode",
            label: "Re-arm on",
            help: "What has to happen before a touch counts again. 'Price leaves the POC' is the published shape: the level stays live and each genuine revisit re-anchors. 'The POC moves' asks the opposite question — once anchored at a level, no amount of whipsawing across it anchors again; the accumulator waits for the developing POC to migrate to a price it has not anchored at, and takes the first touch of that one. One anchor per migration rather than one per revisit: price rotating around a level the market has already agreed on is not new information, a level it has just moved to is.",
            value: p.rearmMode,
            options: MV_REARM_MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
            onChange: (v) => set({ rearmMode: v as ModernVwapParams["rearmMode"] }),
          },
          {
            key: "mvRearm",
            label: p.rearmMode === "pocMove" ? "POC must move" : "Re-arm distance",
            help:
              p.rearmMode === "pocMove"
                ? "How far the developing POC must migrate from the price it last anchored at before its next touch counts. 'Any move' takes every migration, which in balance is barely a debounce — the POC wobbles a tick at a time while price sits on it, and each wobble would be a fresh anchor. Note the level is 'naked' in the sense of never having been *anchored at*, not never traded: a migration usually happens because volume built at the new price, so the bar that moves the POC often spans it and fires the same bar. The one that waits is the POC jumping to a shelf price has left — which is the case worth watching."
                : "After a touch re-anchors, the next touch only counts once a close has been at least this far from the developing POC. 'Off' takes every crossing bar — in balance that is an anchor every few bars and the σ envelope never warms. Denominated in ticks, so it is implicitly loosest on the wildest days; a fact about the knob, not a feature.",
            value: p.rearmTicks,
            options: MV_REARM_OPTIONS.map((n) => ({
              value: n,
              label: n === 0 ? (p.rearmMode === "pocMove" ? "any move" : "off") : `${n} ticks`,
            })),
            onChange: (v) => set({ rearmTicks: Number(v) }),
          },
        ];

  return {
    line: [
      anchor,
      ...pivot,
      ...rearm,
      {
        key: "mvBands",
        label: "Envelope",
        help: "How many volume-weighted σ bands are drawn. A drawing knob only — the MR trigger always tests ±2σ whatever this shows.",
        value: p.bands,
        options: MV_BAND_OPTIONS.map((k) => ({ value: k, label: `±${k}σ` })),
        onChange: (v) => set({ bands: Number(v) as 1 | 2 | 3 }),
      },
      {
        key: "mvAdaptive",
        label: "Band scaling",
        help: "Optionally widen the bands in chop by 1 + w×(1−KER), up to 50% at his default weight. Off in his own script, and worth leaving off until measured: σ already widens when price oscillates around the mean, so this scales a dispersion measure by a second one — the shape the ATR × upper-band study resolved as 'intraday ATR is the band renamed'.",
        value: p.adaptive ? 1 : 0,
        options: [
          { value: 0, label: "fixed" },
          { value: 1, label: "KER-adaptive" },
        ],
        onChange: (v) => set({ adaptive: Number(v) === 1 }),
      },
      // The weight only exists when something is being scaled by it.
      ...(p.adaptive
        ? [
            {
              key: "mvKerWeight",
              label: "Scale weight",
              help: "The w in 1 + w×(1−KER). At 0.5 a dead-flat KER widens the bands by half; at 1 it doubles them.",
              value: p.kerWeight,
              options: MV_KER_WEIGHT_OPTIONS.map((w) => ({
                value: w,
                label: `${w}× · up to +${Math.round(w * 100)}%`,
              })),
              onChange: (v: string | number) => set({ kerWeight: Number(v) }),
            } satisfies SettingField,
          ]
        : []),
      {
        key: "mvKerLen",
        label: "KER length",
        help: "Bars the efficiency ratio is measured over: net travel ÷ gross travel, 1 a straight line and 0 pure chop. This counts bars, not minutes — KER(20) is 20 minutes on a 1m chart and 100 on a 5m one, so the read genuinely changes with the chart's timeframe.",
        value: p.kerLen,
        options: MV_KER_LEN_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
        onChange: (v) => set({ kerLen: Number(v) }),
      },
      {
        key: "mvRegimeLen",
        label: "Regime window",
        help: "How far back KER is compared against its own median to decide 'trending'. Because it is a median split, roughly half of every session reads trending by construction, whatever the market did — this can say 'trendier than the last 200 bars', never 'today is a trend day'. Shorter reacts faster and warms up sooner; the indicator needs this many bars plus the KER length before the gate says anything at all.",
        value: p.regimeLen,
        options: MV_REGIME_LEN_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
        onChange: (v) => set({ regimeLen: Number(v) }),
      },
      {
        key: "mvAtrLen",
        label: "ATR length",
        help: "The second axis of his 'two-axis regime': ATR% against its own median. Included for fidelity with the Pine, but it gates nothing — the quadrant is 2×(KER>median) + (ATR%>median) and the gate asks quadrant ≥ 2, which is exactly the KER half. Changing this moves which of two pills a bar gets and nothing else.",
        value: p.atrLen,
        options: MV_ATR_LEN_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
        onChange: (v) => set({ atrLen: Number(v) }),
      },
      {
        key: "mvRegimeColor",
        label: "Regime colour",
        help: "Colour the bands by the quadrant each bar landed in — his palette: purple trending, yellow ranging, grey not enough history to say. Off draws them in one flat hue, which is easier to read next to the chart's other bands but drops the only place the regime is visible.",
        value: p.regimeColor ? 1 : 0,
        options: [
          { value: 1, label: "by quadrant" },
          { value: 0, label: "flat" },
        ],
        onChange: (v) => set({ regimeColor: Number(v) === 1 }),
      },
    ],
    signals: [
      anchor,
      {
        key: "mvSignals",
        label: "Gate",
        help: "MR wants a ranging regime, TC wants a trending one. 'Gate applied' draws only the triggers that fired in their own regime; 'all' draws the blocked ones too, hollow and grey. Keeping the blocked ones is the point — the gate is this indicator's central claim, and you cannot see what a gate is doing by looking only at what survived it.",
        value: p.signals,
        options: MV_SIGNAL_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
        onChange: (v) => set({ signals: v as ModernVwapParams["signals"] }),
      },
      {
        key: "mvOccWindow",
        label: "Occupancy window",
        help: "Trend continuation, part one: how many bars back the rule looks to decide which side of the line price has been living on. Causal — the current bar's close is not in its own window, and the window only opens once the anchor is that far back.",
        value: p.occWindow,
        options: MV_OCC_WINDOW_OPTIONS.map((n) => ({ value: n, label: `${n} bars` })),
        onChange: (v) => {
          // The floor is only meaningful against its window, so move it with the
          // window rather than leaving a pair that can never fire.
          const n = Number(v);
          const opts = mvOccMinOptions(n);
          const keep = opts.reduce((best, o) =>
            Math.abs(o / n - p.occMin / p.occWindow) < Math.abs(best / n - p.occMin / p.occWindow)
              ? o
              : best,
          );
          set({ occWindow: n, occMin: keep });
        },
      },
      {
        key: "mvOccMin",
        label: "Occupancy floor",
        help: "How many of those closes must be on one side before a touch of the line arms anything. His default is 8 of the last 10. Offered as a share of the window above so the pair can never be set to something that fires nothing, silently.",
        value: p.occMin,
        options: mvOccMinOptions(p.occWindow).map((n) => ({
          value: n,
          label: `${n} of ${p.occWindow}`,
        })),
        onChange: (v) => set({ occMin: Number(v) }),
      },
      {
        key: "mvHold",
        label: "Reclaim window",
        help: "After price touches the line, how many bars the rule waits for a close back on side before the episode dies. Overlapping touches merge into one episode, and it also dies on a side flip or an anchor reset — one signal per episode either way.",
        value: p.holdBars,
        options: MV_HOLD_OPTIONS.map((n) => ({ value: n, label: `${n} bar${n === 1 ? "" : "s"}` })),
        onChange: (v) => set({ holdBars: Number(v) }),
      },
      {
        key: "mvAnchorMarks",
        label: "Anchor ticks",
        help: "Mark the bars where the accumulator reset with a hairline under the low. On a swing anchor this is the only way to see the construct working without inferring it from where the line steps.",
        value: p.anchorMarks ? 1 : 0,
        options: [
          { value: 1, label: "on" },
          { value: 0, label: "off" },
        ],
        onChange: (v) => set({ anchorMarks: Number(v) === 1 }),
      },
    ],
  };
}

export function buildChartKnobs(cfg: ChartKnobsConfig): IndicatorSettingsMap {
  // One prominence for both node readers — it is one question asked of two
  // layers, and two knobs for it would only ever be set to the same number. It
  // appears on both of its rows, saying so.
  const nodes: SettingField = {
    key: "nodeProm",
    label: "Prominence floor",
    help: "Mark high- and low-volume nodes on the composite and on the developing NY profile. A hump counts once it stands this far clear of the deeper valley beside it, as a share of the tallest hump — lower finds more. LVNs are only drawn between two accepted humps.",
    value: cfg.nodeProm,
    options: NODE_PROM_OPTIONS.map((p) => ({
      value: p,
      label: p === 0 ? "off" : `${Math.round(p * 100)}%`,
    })),
    onChange: (v) => cfg.onNodeProm(Number(v)),
    note: "Shared with the other node reader — the composite's and the NY profile's are one setting.",
  };

  const map: IndicatorSettingsMap = {
    bigTrades: {
      title: "Big trades",
      fields: [
        {
          key: "bigLots",
          label: "Sweep size",
          help: "Mark sweeps over this many lots. A sweep is consecutive same-side fills within 250ms and 4 ticks — the shape an order gets worked through the book in, which single prints mostly miss.",
          value: cfg.bigLots,
          options: BIG_LOT_OPTIONS.map((n) => ({ value: n, label: `>${n} lots` })),
          onChange: (v) => cfg.onBigLots(Number(v)),
        },
      ],
    },
    compositeNodes: { title: "Composite nodes", fields: [nodes] },
    developingVpNyNodes: { title: "NY nodes", fields: [nodes] },
  };

  // Volume shelves. The knobs are on the raster's row rather than the boxes' —
  // the raster is the layer, and the boxes are a reading drawn on top of it.
  const shelf = cfg.volumeShelf;
  if (shelf) {
    map.volumeShelf = {
      title: "Volume shelves",
      fields: [
        {
          key: "shelfField",
          label: "Field",
          help: "What the raster draws. \u201cSize per visit\u201d is the shelf reading itself \u2014 where volume built, divided by how long price actually traded there. \u201cOrder flow\u201d swaps in how one-sidedly each row traded (delta over the square root of its volume, so a four-lot row can\u2019t shout), on a buy/sell ramp. The boxes don\u2019t move either way, which is the point: a box says size concentrated here, and the flow field under it says which side put it there.",
          value: shelf.field,
          options: [
            { value: "size", label: "size per visit" },
            { value: "flow", label: "order flow" },
          ],
          onChange: (v) => shelf.onField(v === "flow" ? "flow" : "size"),
        },
        {
          key: "shelfWindow",
          label: "Window",
          help: "How far back each reading looks. Size-at-price is divided by how many seconds price actually traded there, so what the raster shows is size per visit — where volume is building, not where price has spent time. A longer window is steadier and slower to notice.",
          value: shelf.params.windowMin,
          options: SHELF_WINDOW_OPTIONS.map((m) => ({ value: m, label: `${m}m` })),
          onChange: (v) => shelf.onChange({ windowMin: Number(v) }),
        },
        {
          key: "shelfZ",
          label: "Box threshold",
          help: "How far above the window's mean a band must stand, in standard deviations, before a box is drawn round it. The raster is never thresholded, so this only changes what gets named — turn it down and watch which new boxes were already visible in the wash.",
          value: shelf.params.zMin,
          options: SHELF_ZMIN_OPTIONS.map((z) => ({ value: z, label: `${z}σ` })),
          onChange: (v) => shelf.onChange({ zMin: Number(v) }),
        },
        {
          key: "shelfHold",
          label: "Must hold for",
          help: "How long a band has to keep qualifying before it counts as a shelf. Without it a session names a few hundred bands and something is lit most of the time; this is the difference between a burst and a level that keeps being defended.",
          value: shelf.params.minHoldMin,
          options: SHELF_HOLD_OPTIONS.map((m) => ({
            value: m,
            label: m === 0 ? "off" : `${m}m`,
          })),
          onChange: (v) => shelf.onChange({ minHoldMin: Number(v) }),
          note: "No claim is made that a shelf holds price — every level-geometry study in this repo has come back null. It marks where size went, and the tagger measures whether your fills land on it.",
        },
      ],
    };
  }

  const comp = cfg.composite;
  if (comp) {
    map.compositeProfile = {
      title: "Composite VP",
      fields: [
        {
          key: "composite",
          label: "Rule",
          help: "Composite the prior days into one profile. 'Balance run' takes only the days still in the same auction (each one's value area must touch the composite's, cap 5) — measured as the better rule on NQ, where balance runs are median 2 days and a fixed 10-day window merges about eight auctions.",
          value: comp.rule,
          options: [
            { value: "off", label: "off" },
            { value: "balance", label: "balance run" },
            { value: "days", label: "all prior days" },
          ],
          onChange: (v) => comp.onRule(v as CompositeRule),
          note: comp.note,
        },
        // Only once there is a composite for it to cut: which part of each day
        // goes in is not a question about a profile that isn't being built.
        ...(comp.rule === "off"
          ? []
          : [
              {
                key: "compositeSpan",
                label: "Span",
                help: "Which part of each prior day the composite is built from. 'Globex + RTH' takes the whole day from the 18:00 open to the 16:00 close; 'RTH only' takes the day session, which is the span the balance-run and value-area numbers in the write-up were measured on. Wider spans mean wider value areas, which touch more often — so the balance rule keeps more days under Globex.",
                value: comp.span,
                options: [
                  { value: "globex", label: "globex + RTH" },
                  { value: "rth", label: "RTH only" },
                ],
                onChange: (v: string | number) => comp.onSpan(v as CompositeSpan),
              } satisfies SettingField,
            ]),
      ],
    };
  }

  if (cfg.modernVwap) {
    const mv = modernVwapKnobs(cfg.modernVwap.params, cfg.modernVwap.onChange);
    map.modernVwap = { title: "Modern VWAP", fields: mv.line };
    map.modernVwapSignals = { title: "Modern VWAP signals", fields: mv.signals };
  }

  if (cfg.dynamicSwingVwap) {
    map.dynamicSwingVwap = {
      title: "Dynamic Swing VWAP",
      fields: dynamicSwingVwapKnobs(cfg.dynamicSwingVwap.params, cfg.dynamicSwingVwap.onChange),
    };
  }

  const ev = cfg.events;
  if (!ev) return map;

  // How the bands draw — a repaint, unlike the thresholds below, which re-derive
  // the tape. The label floor and the marginal are statements about the layer,
  // shared across both rows; the wash and the draw-floor are per kind, because
  // with both kinds on the chart, being able to quiet one without hiding it is
  // most of what keeps the overlap readable.
  const perKindDrawn = (kind: "sweep" | "absorb"): SettingField[] => {
    const K = kind === "sweep" ? "Sweep" : "Absorb";
    return [
      {
        key: `eventFill${K}`,
        label: "Wash",
        help: `How strongly this kind's fill is tinted at strength 1; stronger events tint further, up to 2.5×. ${
          kind === "absorb"
            ? "Absorption's wash is hatched rather than solid — same hue, different cloth — so where a burst overlaps a shelf the eye sees solid with lines through it instead of a muddier third colour."
            : "The burst's wash is the solid one; absorption's is hatched, so the two stay tellable apart where they overlap."
        } The wash draws under the candles, so it never becomes a lid over the price action. Outline-only still says everything the band's shape says.`,
        value: kind === "sweep" ? ev.fillSweep : ev.fillAbsorb,
        options: EVENT_FILL_OPTIONS.map((a) => ({
          value: a,
          label: a === 0 ? "outline only" : `${Math.round(a * 100)}%`,
        })),
        onChange: (v) => (kind === "sweep" ? ev.onFillSweep : ev.onFillAbsorb)(Number(v)),
        note: "This kind's wash only — the other keeps its own, so one can be quieted without hiding it.",
      },
      {
        key: `eventFloor${K}`,
        label: "Draw from",
        help: "Only draw this kind's events at or above this strength — in units of the threshold that selected them, so 2× is twice the burst size or twice the concentration multiple. A repaint, not a re-derivation: the engine still finds everything, this only chooses what reaches the chart, and the legend's count follows it. At 1× every published event draws.",
        value: kind === "sweep" ? ev.floorSweep : ev.floorAbsorb,
        options: EVENT_FLOOR_OPTIONS.map((s) => ({
          value: s,
          label: s === 1 ? "all" : `≥${s}×`,
        })),
        onChange: (v) => (kind === "sweep" ? ev.onFloorSweep : ev.onFloorAbsorb)(Number(v)),
        note: "This kind only — the other keeps its own floor.",
      },
    ];
  };
  const drawn: SettingField[] = [
    {
      key: "eventLabelSt",
      label: "Label from",
      help: "Write an event's lot count on it once it reaches this strength — in units of the threshold that selected it, so 2× is twice the burst size or twice the concentration multiple. At 1 every drawn band carries a number.",
      value: ev.labelSt,
      options: EVENT_LABEL_ST_OPTIONS.map((s) => ({
        value: s,
        label: s === 0 ? "never" : `≥${s}×`,
      })),
      onChange: (v) => ev.onLabelSt(Number(v)),
      note: "Shared with the other event kind — one layer, one way of drawing it.",
    },
    {
      key: "eventMarginal",
      label: "Profile marginal",
      help: "Also draw the events as a distribution down the composite's and the viewport profile's gutters — of all the size that arrived this way, where it went. A different question from the bands: a profile has no time axis. Read it against the histogram's shape, not against its levels; events land where price traded, and price traded where value is. The developing NY gutter is left clean.",
      value: ev.marginal ? 1 : 0,
      options: [
        { value: 1, label: "on" },
        { value: 0, label: "off" },
      ],
      onChange: (v) => ev.onMarginal(Number(v) === 1),
      note: "Shared with the other event kind — one layer, one way of drawing it.",
    },
  ];

  // And what selects them. Every one of these re-derives the tape from tick
  // zero, which is the only honest way to change a clusterer's rules — hence
  // the shared note, and hence their being on the layer's own panels rather
  // than anywhere they could be turned by accident.
  const REDERIVES =
    "Re-derives the session from tick zero — strengths are only comparable within one setting.";
  const opts = EVENT_TUNING_OPTIONS;
  map.sweepBursts = {
    title: "Sweep bursts",
    fields: [
      {
        key: "burstLots",
        label: "Burst size",
        help: "Lots a burst needs before it is drawn — and the unit its strength is quoted in, so 150 means a 300-lot burst reads 2×. Below the threshold the burst still accumulates; it simply hasn't happened yet.",
        value: ev.tuning.burstLots,
        options: opts.burstLots.map((n) => ({ value: n, label: `≥${n} lots` })),
        onChange: (v) => ev.onTuning({ burstLots: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "sweepLots",
        label: "Member sweep",
        help: "How big a single sweep must be to count toward a burst. A sweep is consecutive same-side fills within 250ms and 4 ticks — the shape an order gets worked through the book in. Separate from the big-trade threshold on purpose: that one decides which prints get a bubble, this one decides what the clusterer is allowed to see.",
        value: ev.tuning.sweepLots,
        options: opts.sweepLots.map((n) => ({ value: n, label: `≥${n} lots` })),
        onChange: (v) => ev.onTuning({ sweepLots: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "burstGapS",
        label: "Time gap",
        help: "Big sweeps this close together join one burst. Longer merges a whole working order — or a whole busy hour, past which a burst stops being an event and starts being the session.",
        value: ev.tuning.burstGapS,
        options: opts.burstGapS.map((s) => ({
          value: s,
          label: s < 60 ? `${s}s` : `${s / 60}m`,
        })),
        onChange: (v) => ev.onTuning({ burstGapS: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "burstSpanPts",
        label: "Price span",
        help: "How far price may walk from a member sweep and still be the same burst. Tight keeps a burst to one price — a defended level being run — while wide lets it follow a trend leg.",
        value: ev.tuning.burstSpanPts,
        options: opts.burstSpanPts.map((p) => ({ value: p, label: `${p}pt` })),
        onChange: (v) => ev.onTuning({ burstSpanPts: Number(v) }),
        note: REDERIVES,
      },
      ...perKindDrawn("sweep"),
      ...drawn,
    ],
  };
  map.absorption = {
    title: "Absorption",
    fields: [
      {
        key: "absorbMult",
        label: "Concentration",
        help: "How many times the baseline median a window's lots-per-point must run to be absorption. Never an absolute band: the same 4pt means opposite things in a quiet and a violent regime. This also decides which adjacent hot windows merge, so it shapes the bands as well as choosing them — under 2× about half the session qualifies.",
        value: ev.tuning.absorbMult,
        options: opts.absorbMult.map((m) => ({ value: m, label: `≥${m}×` })),
        onChange: (v) => ev.onTuning({ absorbMult: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "absorbWinMs",
        label: "Window",
        help: "The stretch of tape one concentration is measured over. Short finds the jab a single iceberg refill leaves; long finds the shelf a whole balance sat on. It is the setting that most changes what the layer is about.",
        value: ev.tuning.absorbWinMs,
        options: opts.absorbWinMs.map((ms) => ({
          value: ms,
          label: ms < 60_000 ? `${ms / 1000}s` : `${ms / 60_000}m`,
        })),
        onChange: (v) => ev.onTuning({ absorbWinMs: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "absorbBaseline",
        label: "Baseline",
        help: "What the median is taken over. The whole session so far answers 'concentrated for today' and carries the open around all afternoon; a rolling window answers 'concentrated for right now' and drifts with the regime. Either way it develops causally — nothing is scored against tape that hasn't printed.",
        value: ev.tuning.absorbBaseline,
        options: opts.absorbBaseline.map((n) => ({
          value: n,
          label:
            n === 0
              ? "session so far"
              : `last ${Math.round((n * ev.tuning.absorbWinMs) / 60_000)}m`,
        })),
        onChange: (v) => ev.onTuning({ absorbBaseline: Number(v) }),
        note: `${REDERIVES} A rolling baseline is ${ev.tuning.absorbBaseline || "N"} windows at the window above, so it re-scales when that changes.`,
      },
      {
        key: "absorbMinWindows",
        label: "Warm-up",
        help: "How many windows must have closed before anything is scored — a median off three windows is not a median. Fewer finds absorption near the open, off a thin baseline. Clamped to the rolling baseline when there is one, since the pool stops growing there.",
        value: ev.tuning.absorbMinWindows,
        options: opts.absorbMinWindows.map((n) => ({
          value: n,
          label: `${n} · ${Math.round((n * ev.tuning.absorbWinMs) / 60_000)}m`,
        })),
        onChange: (v) => ev.onTuning({ absorbMinWindows: Number(v) }),
        note: REDERIVES,
      },
      {
        key: "absorbScope",
        label: "Scope",
        help: "Which tape is measured. RTH restarts the baseline at the bell — the overnight trades a fraction of the volume through a fraction of the range, so one median across both has the open firing on every window. Scoring the night too is a different instrument, and worth it only when the night is what you are reading.",
        value: ev.tuning.absorbScope,
        options: [
          { value: "rth", label: "RTH only" },
          { value: "all", label: "globex + RTH" },
        ],
        onChange: (v) => ev.onTuning({ absorbScope: v as "rth" | "all" }),
        note: REDERIVES,
      },
      {
        key: "absorbMerge",
        label: "Merge adjacent",
        help: "Whether consecutive hot windows read as one tall event or as several. Merging is only taken while the merged block still clears the threshold, so a band never claims a concentration it doesn't have. Off is the same tape read as 'three windows agreed' rather than 'one block'.",
        value: ev.tuning.absorbMerge ? 1 : 0,
        options: [
          { value: 1, label: "on" },
          { value: 0, label: "off" },
        ],
        onChange: (v) => ev.onTuning({ absorbMerge: Number(v) === 1 }),
        note: REDERIVES,
      },
      ...perKindDrawn("absorb"),
      ...drawn,
    ],
  };
  return map;
}

/**
 * The External Chart overlay's knobs (lib/externalChart).
 *
 * A pane knob like the volume profile's above, and for the same reason: the
 * parameters are the chart's, sticky and global (`chart.externalChart`), so both
 * surfaces that draw candles can offer the layer without either host page
 * learning what an external period is.
 *
 * The two colour fields are the first knobs of ours that aren't selects, and
 * that is not the convention slipping. The rule this file opens with is about
 * *measured* settings — a shortlist is the honest shape for a number whose
 * useful range came out of a study, and inventing one for a hue would be a lie
 * in the other direction. So the period stays a shortlist (those bucketings
 * floor cleanly on the epoch; the others don't) and the hues are a colour
 * picker, behind a select that keeps the surface's own ink as the default. They
 * appear only once you've asked for them, the same rule the delta lane's
 * reading knobs follow.
 */
export function externalChartKnobs(
  value: ExternalChartParams,
  onChange: (patch: Partial<ExternalChartParams>) => void,
  /** Whether the external period actually grouped the bars on this chart. False
   *  when it is no coarser than the drawn timeframe, which draws nothing — the
   *  note says so rather than leaving an empty layer looking broken. */
  grouped = true,
): SettingField[] {
  const fields: SettingField[] = [
    {
      key: "externalPeriod",
      label: "External period",
      help: "The coarser bar drawn over this chart's own. Each box is one period's worth of the bars underneath it: the outline is that period's open and close (blue when it closed above its open, red below), and the grey box round it is the period's high and low. The read it buys is where the bar you are trading sits inside the bar containing it — whether this push is the first minute of a fresh 15-minute leg or the last gasp of one that has already run. Nothing is fetched: the boxes are the chart's own bars regrouped, so the overlay can never disagree with the candles inside it. Each drawn bar is counted into the period its open falls in, which is exact whenever the drawn timeframe divides the external one. The list stops at 4h because these all floor cleanly on the clock; a daily box would have to anchor on the 18:00 session open rather than on midnight, and one floored on midnight would cut the session in half.",
      value: value.period,
      options: EXTERNAL_PERIOD_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => onChange({ period: Number(v) }),
      note: grouped
        ? undefined
        : "Not coarser than the bars on this chart, so it would draw one box per candle — nothing is drawn until the external period is above the drawn timeframe.",
    },
    {
      key: "externalGrid",
      label: "High/low box",
      help: "Draw the period's full range as a grey box round the open/close outline — ATAS's 'Show Grid'. It is what makes the overlay a candle rather than just a body: without it you can see which way the period went but not what it reached on the way, and the extremes are usually the part the next period trades against. Thinner than the body outline on purpose, so the direction still reads first.",
      value: value.grid ? 1 : 0,
      options: [
        { value: 0, label: "off" },
        { value: 1, label: "on" },
      ],
      onChange: (v) => onChange({ grid: Number(v) === 1 }),
    },
    {
      key: "externalFill",
      label: "Fill body",
      help: "Wash the open/close body instead of leaving it hollow. Off by default, and worth leaving off on a fast chart: the layer exists to frame the candles underneath it, and a filled box over a dense minute chart buries the price action it was drawn to give context to. Useful at the coarse end, where a 4h body spans most of the pane and the outline alone is easy to lose.",
      value: value.fill ? 1 : 0,
      options: [
        { value: 0, label: "off" },
        { value: 1, label: "on" },
      ],
      onChange: (v) => onChange({ fill: Number(v) === 1 }),
    },
    {
      key: "externalAbove",
      label: "Draw above price",
      help: "Which side of the candles the boxes sit on — ATAS's 'Show above chart'. Behind them by default, which is where context belongs: the overlay is the frame, the candles are the subject. Put it in front when the boxes are being lost among a lot of other layers and you want the period's edges readable at a glance.",
      value: value.above ? 1 : 0,
      options: [
        { value: 0, label: "behind candles" },
        { value: 1, label: "above candles" },
      ],
      onChange: (v) => onChange({ above: Number(v) === 1 }),
    },
    {
      key: "externalPalette",
      label: "Colours",
      help: "Where the two hues come from. 'surface' uses the chart appearance's own external-chart ink, which is authored twice — once for the dark surfaces and once for the light ones — so the outlines keep their contrast when you switch to Paper. Pick 'custom' to choose the pair yourself; a custom pair is used as given on every surface, since an explicit choice is not something the theme should be re-cutting behind you.",
      value: value.palette,
      options: [
        { value: "theme", label: "surface" },
        { value: "custom", label: "custom" },
      ],
      onChange: (v) => onChange({ palette: v as ExternalPalette }),
    },
  ];

  if (value.palette !== "custom") return fields;

  fields.push(
    {
      key: "externalBull",
      label: "Up colour",
      kind: "color",
      value: value.bull,
      help: "The outline for a period that closed above its open.",
      onChange: (v) => onChange({ bull: v }),
    },
    {
      key: "externalBear",
      label: "Down colour",
      kind: "color",
      value: value.bear,
      help: "The outline for a period that closed below its open.",
      onChange: (v) => onChange({ bear: v }),
    },
  );
  return fields;
}

/**
 * The HTF trend layer's knobs (lib/htfTrend). Pane-owned and sticky-global,
 * like the External Chart's above.
 */
export function htfTrendKnobs(
  value: HtfTrendParams,
  onChange: (patch: Partial<HtfTrendParams>) => void,
  /** False while no frame has enough closed bars to draw — the note says why the
   *  layer is on and empty. */
  warm = true,
): SettingField[] {
  return [
    {
      key: "htfFrames",
      label: "Frames",
      help: "The higher timeframes read onto this chart. Each frame's EMA is drawn as a stepped line (finest solid, then dashed, then dotted), and the background washes green or red only where EVERY frame agrees: its last closed bar's close is on the trending side of its EMA and the EMA has moved that way over the last 3 closed bars. Built by regrouping this chart's own bars, so nothing is fetched and the frames can never disagree with the candles. Closed frame bars only, so the line steps once per frame bar and never repaints. A frame no coarser than the chart is dropped.",
      value: value.frames,
      options: HTF_FRAME_SETS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => onChange({ frames: String(v) }),
      note: warm
        ? undefined
        : "Not enough bars on the chart yet: a frame needs its EMA length in closed bars before it draws (a 15m EMA20 is five hours). Load more prior context, or wait for the tape.",
    },
    {
      key: "htfLength",
      label: "EMA length",
      help: "The EMA length on every frame. 20 is what the manual-trade audit (data/research/htf-alignment) scored: with-trend trades beat against-trend ones in every cohort, but no interval excluded zero. Treat this as context, not a signal.",
      value: value.length,
      options: HTF_LENGTHS.map((n) => ({ value: n, label: String(n) })),
      onChange: (v) => onChange({ length: Number(v) }),
    },
    {
      key: "htfLines",
      label: "EMA lines",
      help: "Draw each frame's EMA over the candles. This is the level a counter-move on this chart is pulling back to. Over the candles rather than under, because a level hidden behind a wall of bodies is not a level you will see.",
      value: value.lines ? 1 : 0,
      options: [
        { value: 0, label: "off" },
        { value: 1, label: "on" },
      ],
      onChange: (v) => onChange({ lines: Number(v) === 1 }),
    },
    {
      key: "htfTint",
      label: "Trend tint",
      help: "How the frames' agreement is shown. 'wash' tints the whole pane behind the candles; 'ribbon' is a thin strip along the bottom for when the wash is too much; 'off' leaves the lines alone. No tint where the frames disagree or are flat.",
      value: value.tint,
      options: [
        { value: "wash", label: "wash" },
        { value: "ribbon", label: "ribbon" },
        { value: "off", label: "off" },
      ],
      onChange: (v) => onChange({ tint: v as HtfTint }),
    },
  ];
}

/**
 * The ranked S/R zones' knobs (lib/rankedZones).
 *
 * A pane knob like the External Chart's, sticky-global for the same reason, and
 * every field is a select because every one of them *is* a shortlist here: these
 * are the Pine author's defaults with a spread either side, not a measured
 * range. The help text says so where it matters — a knob whose useful value came
 * out of nothing in particular should not pretend otherwise by offering a
 * continuum.
 *
 * Ordered by how often you would touch one: what is drawn, then what counts as a
 * level, then what kills one, then the two scoring lengths nobody changes.
 */
export function rankedZonesKnobs(
  value: RankedZonesParams,
  onChange: (patch: Partial<RankedZonesParams>) => void,
): SettingField[] {
  const num = (
    key: string,
    label: string,
    help: string,
    field: keyof RankedZonesParams,
    options: readonly number[],
    fmt: (n: number) => string = String,
  ): SettingField => ({
    key,
    label,
    help,
    value: value[field] as number,
    options: options.map((o) => ({ value: o, label: fmt(o) })),
    onChange: (v) => onChange({ [field]: Number(v) } as Partial<RankedZonesParams>),
  });
  const bool = (
    key: string,
    label: string,
    help: string,
    field: "strengthBars" | "zoneText",
  ): SettingField => ({
    key,
    label,
    help,
    value: value[field] ? 1 : 0,
    options: [
      { value: 0, label: "off" },
      { value: 1, label: "on" },
    ],
    onChange: (v) => onChange({ [field]: Number(v) === 1 } as Partial<RankedZonesParams>),
  });

  return [
    num(
      "rzVisible",
      "Show top zones",
      "How many of the highest-ranked zones are drawn. The ranking is the whole layer — every pivot that survives the filters becomes a zone, and without a cut you get a chart papered in rectangles. Raising this does not find more levels, it just draws further down a list that is already sorted. What the score is made of: zone width, volume at the pivot, whether the zone agrees with the trend EMA, how cleanly the swing stood out, how often price has come back, and age — the last two of which also subtract. Worth knowing that none of that has been tested here: it is the author's weighting, and a ranked list with percentages on it invites more trust than this chart has earned it.",
      "visibleLimit",
      RZ_VISIBLE_OPTIONS,
    ),
    {
      key: "rzRankBy",
      label: "Rank by",
      help: "What the list is sorted on. Author's score is the port as published: width, bar volume, trend, swing, touches, age. Order flow is a separate score read off the prints, not a blend: volume that traded inside the zone while the pivot formed, the share of that aggression that came at the zone (sells into support, buys into resistance, which held, so it went nowhere: 'Absorbed' from 60% up), and aggression into the zone on later touches that failed to break it; age takes points off. The two can pick different zones. The flow weights are guesses and untested, and every order-flow study here has come back null, so this sorts the screen differently, it does not sort it better. Needs the tape; without one it falls back to the author's score and the legend says so.",
      value: value.rankBy,
      options: RZ_RANK_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => onChange({ rankBy: v as RankedZonesParams["rankBy"] }),
    },
    {
      key: "rzDirection",
      label: "Sides",
      help: "Draw both sides, or only one. A filter on what is *shown*, not on what is computed or ranked — a resistance zone still exists, still scores, still breaks and still occupies a place in the ranking while hidden, so switching to one side does not promote the next zone up into view.",
      value: value.direction,
      options: RZ_FILTER_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      onChange: (v) => onChange({ direction: v as RankedZonesParams["direction"] }),
    },
    num(
      "rzSpan",
      "Pivot length",
      "How many bars either side a swing must beat to count as a pivot, which also sets how late it confirms: a zone appears this many bars after the high or low that made it, never at it. Longer finds fewer and more considered levels; shorter finds more and reacts sooner. Strict on both sides, so an exact double top produces no pivot at all — a tie is not a turn.",
      "pivotSpan",
      RZ_SPAN_OPTIONS,
    ),
    num(
      "rzMinSwing",
      "Min pivot ATR",
      "How far a pivot must stand out from the two bars either side of it, in ATR, before it becomes a zone. Deliberately a three-bar measure rather than a measure over the whole pivot window — it is a displacement filter, not a prominence one, so a pivot that beat twenty bars by a hair is still rejected. Zero draws a zone at every confirmed pivot.",
      "minSwingAtr",
      RZ_MIN_SWING_OPTIONS,
      (n) => `${n} ATR`,
    ),
    num(
      "rzWidth",
      "Zone width",
      "How thick each zone is, in ATR, centred on the pivot price. This is also what 'touched' and 'broken' are measured against, so it is not only a cosmetic: a wider zone is touched sooner, mitigates more slowly as a share of its own width, and needs price to travel further before it breaks.",
      "zoneAtrWidth",
      RZ_WIDTH_OPTIONS,
      (n) => `${n} ATR`,
    ),
    num(
      "rzAbsorb",
      "Absorb similar",
      "How close two same-side zones must be, in ATR between their mids, before a new pivot is folded into the existing zone instead of stacking a second one beside it. Absorbing is not just tidying: the survivor widens to cover both, keeps the better of every score term, gains a touch, and has its mitigation reset to zero — price returning to make a fresh pivot at a level is treated as the level being renewed. Zero stacks everything; the high end merges aggressively and leaves a few wide zones.",
      "absorbAtr",
      RZ_ABSORB_OPTIONS,
      (n) => `${n} ATR`,
    ),
    num(
      "rzBreak",
      "Break buffer",
      "How far past a zone's far edge price must *close*, in ATR, for the zone to count as broken and leave the ranking. Only closes break a zone — a wick through it is a touch, and touches raise the zone's score rather than ending it. Zero breaks on any close beyond the edge, which on a chart that oscillates will break almost everything it creates.",
      "breakAtr",
      RZ_BREAK_OPTIONS,
      (n) => `${n} ATR`,
    ),
    num(
      "rzKeepBroken",
      "Keep broken",
      "How many broken zones stay on the chart behind price, newest first, drawn grey with a dotted line where the level was and stopping at the bar that broke them. They are out of the ranking and score nothing — they are there because a level that just failed is usually the most interesting thing on the screen. Zero removes them on the break.",
      "keepBrokenCount",
      RZ_KEEP_BROKEN_OPTIONS,
    ),
    bool(
      "rzBars",
      "Strength bars",
      "The two meters inside each zone, growing from its left edge: the zone's own side reading its score through a piecewise stretch, and the opposite side reading almost entirely how far price has already eaten into it. They are a restatement of the score and the mitigation, not new information — off is the cleaner chart, on is the one where you can compare two zones without reading the ranking order.",
      "strengthBars",
    ),
    bool(
      "rzText",
      "Zone text",
      "The label at the right edge of each zone — 'Strong Support', 'Mitigated Resistance'. Derived from the strength bar and the mitigation share, thresholded at the author's 70/45 and 75%, so it says nothing the bars do not; it is there for when the bars are off or the zone is too thin to hold them.",
      "zoneText",
    ),
    num(
      "rzStored",
      "Max stored",
      "How many zones are kept alive internally before the lowest-ranked are dropped. Above the draw cap on purpose: a zone outside the top few still ages, still gets touched, still breaks, and can climb back into view. Raising it lengthens the memory rather than the picture, and costs a little per bar.",
      "storedLimit",
      RZ_STORED_OPTIONS,
    ),
    num(
      "rzVolLen",
      "Volume baseline",
      "The lookback the pivot's own volume is scored against — a pivot on twice the average volume scores the term out. Longer makes the baseline steadier and the term rarer to max.",
      "volLen",
      RZ_VOL_LEN_OPTIONS,
    ),
    num(
      "rzTrendLen",
      "Trend EMA",
      "The EMA a zone is scored for agreeing with: support above it, or resistance below it, earns a flat bonus — nothing in between, it is a yes or no. Longer asks about a slower trend.",
      "trendLen",
      RZ_TREND_LEN_OPTIONS,
    ),
  ];
}
