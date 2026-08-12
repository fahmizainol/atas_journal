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
  BIG_LOT_OPTIONS,
  EVENT_FILL_OPTIONS,
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
  MV_REGIME_LEN_OPTIONS,
  MV_SIGNAL_OPTIONS,
  mvOccMinOptions,
  type ModernVwapParams,
} from "../../lib/modernVwap";

/** The event layer's knobs, as one bundle: what selects the bands (the tuning,
 *  which re-derives the tape) and how they draw (a repaint). Absent on a page
 *  that doesn't offer the layer, and the two legend rows then get no panels. */
export interface EventKnobs {
  tuning: EventTuning;
  labelSt: number;
  fill: number;
  marginal: boolean;
  onTuning: (patch: Partial<EventTuning>) => void;
  onLabelSt: (v: number) => void;
  onFill: (v: number) => void;
  onMarginal: (v: boolean) => void;
}

export interface ChartKnobsConfig {
  bigLots: number;
  onBigLots: (lots: number) => void;
  nodeProm: number;
  onNodeProm: (p: number) => void;
  composite: CompositeRule;
  onComposite: (r: CompositeRule) => void;
  compositeSpan: CompositeSpan;
  onCompositeSpan: (s: CompositeSpan) => void;
  /** The line under the rule picker saying where the day-count knob lives — the
   *  two pages load their context differently, so each writes its own. */
  compositeNote: string;
  events?: EventKnobs;
  /** Modern VWAP's parameters, as one object because the indicator takes them as
   *  one. Absent on a page that doesn't offer the layer. */
  modernVwap?: {
    params: ModernVwapParams;
    onChange: (patch: Partial<ModernVwapParams>) => void;
  };
}

/** Modern VWAP's knobs — every configurable the demo page carries, minus two
 *  that have no meaning here: its bar-size switch (the chart has its own
 *  timeframe control) and its "compare" line (the chart already draws the
 *  Globex, NY and weekly VWAPs as their own layers, from ticks, better).
 *
 *  Split across the two rows the way the composite's prominence floor is shared:
 *  everything that shapes *the line* is on the line's row, everything that
 *  shapes *the triggers* is on the triggers' row, and both rows carry the anchor
 *  because the anchor is what the triggers are measured against. */
function modernVwapKnobs(
  p: ModernVwapParams,
  set: (patch: Partial<ModernVwapParams>) => void,
): { line: SettingField[]; signals: SettingField[] } {
  const anchor: SettingField = {
    key: "mvAnchor",
    label: "Anchor",
    help: "Where the accumulator resets. 'Swing pivots' is the construct this indicator exists for — it re-anchors at every confirmed pivot instead of at a clock time, which is the one thing on it we have neither built nor falsified. The three clock anchors are here so the swing one has something to be read against; note they are bar-weighted, while the chart's own Globex/NY/weekly bands accumulate tick by tick and are the better number.",
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

  return {
    line: [
      anchor,
      ...pivot,
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
    compositeProfile: {
      title: "Composite VP",
      fields: [
        {
          key: "composite",
          label: "Rule",
          help: "Composite the prior days into one profile. 'Balance run' takes only the days still in the same auction (each one's value area must touch the composite's, cap 5) — measured as the better rule on NQ, where balance runs are median 2 days and a fixed 10-day window merges about eight auctions.",
          value: cfg.composite,
          options: [
            { value: "off", label: "off" },
            { value: "balance", label: "balance run" },
            { value: "days", label: "all prior days" },
          ],
          onChange: (v) => cfg.onComposite(v as CompositeRule),
          note: cfg.compositeNote,
        },
        // Only once there is a composite for it to cut: which part of each day
        // goes in is not a question about a profile that isn't being built.
        ...(cfg.composite === "off"
          ? []
          : [
              {
                key: "compositeSpan",
                label: "Span",
                help: "Which part of each prior day the composite is built from. 'Globex + RTH' takes the whole day from the 18:00 open to the 16:00 close; 'RTH only' takes the day session, which is the span the balance-run and value-area numbers in the write-up were measured on. Wider spans mean wider value areas, which touch more often — so the balance rule keeps more days under Globex.",
                value: cfg.compositeSpan,
                options: [
                  { value: "globex", label: "globex + RTH" },
                  { value: "rth", label: "RTH only" },
                ],
                onChange: (v: string | number) => cfg.onCompositeSpan(v as CompositeSpan),
              } satisfies SettingField,
            ]),
      ],
    },
    compositeNodes: { title: "Composite nodes", fields: [nodes] },
    developingVpNyNodes: { title: "NY nodes", fields: [nodes] },
  };

  if (cfg.modernVwap) {
    const mv = modernVwapKnobs(cfg.modernVwap.params, cfg.modernVwap.onChange);
    map.modernVwap = { title: "Modern VWAP", fields: mv.line };
    map.modernVwapSignals = { title: "Modern VWAP signals", fields: mv.signals };
  }

  const ev = cfg.events;
  if (!ev) return map;

  // How the bands draw. One set of three, on both event rows, because it is a
  // statement about the layer rather than about either kind — and unlike the
  // thresholds below, none of it touches the tape: it is a repaint.
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
      key: "eventFill",
      label: "Wash",
      help: "How strongly the band's fill is tinted at strength 1; stronger events tint further, up to 2.5×. The wash draws under the candles, so it never becomes a lid over the price action — but with a composite and two profiles already on the chart it is a lot of colour. Outline-only still says everything the band's shape says.",
      value: ev.fill,
      options: EVENT_FILL_OPTIONS.map((a) => ({
        value: a,
        label: a === 0 ? "outline only" : `${Math.round(a * 100)}%`,
      })),
      onChange: (v) => ev.onFill(Number(v)),
      note: "Shared with the other event kind — one layer, one way of drawing it.",
    },
    {
      key: "eventMarginal",
      label: "Profile marginal",
      help: "Also draw the events as a distribution down the volume profiles' gutters — of all the size that arrived this way, where it went. A different question from the bands: a profile has no time axis. Read it against the histogram's shape, not against its levels; events land where price traded, and price traded where value is.",
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
        help: "How far price may walk from a member sweep's low and still be the same burst. Tight keeps a burst to one price — a defended level being run — while wide lets it follow a trend leg.",
        value: ev.tuning.burstSpanPts,
        options: opts.burstSpanPts.map((p) => ({ value: p, label: `${p}pt` })),
        onChange: (v) => ev.onTuning({ burstSpanPts: Number(v) }),
        note: REDERIVES,
      },
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
      ...drawn,
    ],
  };
  return map;
}
