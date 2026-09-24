// Palette — mirrors src/journal/ui.py and .streamlit/config.toml.
export const palette = {
  bg: "#0e1117",
  bg2: "#15171f",
  card: "#1a1d27",
  cardBorder: "#262a36",
  accent: "#6c5ce7",
  green: "#21c07a",
  red: "#f5455f",
  blue: "#3b82f6",
  orange: "#f97316",
  gold: "#e0a52a",
  violet: "#a78bfa",
  text: "#e6e8ee",
  muted: "#8a8f9c",
  grid: "#2a2e38",
} as const;

// The candlestick chart's surface — what everything else is drawn *on*. The one
// part of the chart's colour that is taste rather than measurement, so it is the
// one part the user picks (see lib/chartPrefs). Every indicator family below
// encodes a distinction some study leans on and stays fixed.
//
// This used to be a dark-only list, and the comment here said why: the indicator
// hues were chosen against a dark surface — the Globex VWAP's mid line is
// literally #ffffff — so a light background would erase levels rather than
// restyle them. That was correct, and it is the reason the light surfaces below
// arrive with `light: true` rather than on their own: the flag selects a second
// authored ink for every level on the chart (see `chartInk`). A light chart is a
// re-cut of the indicator palette, and that is what the flag buys.
//
// The two axes worth having are brightness (how much contrast the candles get)
// and temperature (which is pure preference). What is NOT here is a surface
// tinted toward a hue an indicator family already owns — no violet, because the
// NY VWAP lives there; no teal, because the ⚓ anchored VWAP does. A surface that
// competes with a level is the one way this knob could cost you a read.
//
// Default first, then darkest-to-lightest, then the warm one, then the lights.
// Each carries its own grid and axis text: a grid tuned for #0e1117 disappears
// on black and glares on gunmetal.
export const chartSurfaces = {
  charcoal: { label: "Charcoal", bg: palette.bg, grid: palette.grid, text: palette.text, light: false },
  black: { label: "Black", bg: "#000000", grid: "#1e1e1e", text: "#dcdfe6", light: false },
  midnight: { label: "Midnight", bg: "#0a0e1a", grid: "#1e2333", text: "#dde1ec", light: false },
  slate: { label: "Slate", bg: "#131722", grid: "#2a2e39", text: "#d1d4dc", light: false },
  graphite: { label: "Graphite", bg: "#181a20", grid: "#31343d", text: "#e6e8ee", light: false },
  // The softest of them — for a bright room, where a near-black chart is a mirror.
  gunmetal: { label: "Gunmetal", bg: "#1c1f26", grid: "#363a44", text: "#eaecf2", light: false },
  // The one warm option. Safe despite the orange/gold families (weekly VWAP, the
  // EMAs) because it is far darker than any of them — it shifts the neutral, it
  // doesn't approach a line's own hue.
  warm: { label: "Warm dark", bg: "#17130f", grid: "#332c24", text: "#ece5db", light: false },
  // The lights. Off-white rather than #ffffff on purpose: a pure white pane at
  // full screen is its own glare source, and the levels drawn on it are already
  // dark enough that the last few points of contrast buy nothing. Same three
  // axes as above — warm (paper), neutral-cool (daylight), and a dimmer one
  // (overcast) for the light equivalent of the reason `gunmetal` exists.
  paper: { label: "Paper · light", bg: "#f7f4ec", grid: "#e3ddd0", text: "#3b352c", light: true },
  daylight: { label: "Daylight · light", bg: "#f5f7fa", grid: "#dfe4ec", text: "#333a45", light: true },
  overcast: { label: "Overcast · light", bg: "#e7eaf0", grid: "#d0d5df", text: "#2b313b", light: true },
} as const;

// Up/down candle bodies and wicks. Also taste — with one exception worth the
// row: `cb` is there because green/red is the one pair on this chart that a
// red-green colour deficiency collapses, and the candles are the layer you read
// without looking at.
//
// Bodies and wicks share a colour and borders stay off, which is why each scheme
// is two hues rather than six: the candles are the backdrop the levels are read
// against, and a bordered candle draws its own outline into that reading.
//
// The pairs run loud to quiet. That ordering is the actual choice being made
// here: this chart can carry a dozen overlays at once, and how loud the candles
// are decides whether they or the levels win the eye. `muted` and `mono` exist
// for sessions spent reading levels; `neon` for ones spent reading price.
export const candleSchemes = {
  classic: { label: "Green / red", up: palette.green, down: palette.red },
  neon: { label: "Neon green / pink", up: "#00e676", down: "#ff2d55" },
  tv: { label: "Teal / red", up: "#26a69a", down: "#ef5350" },
  cb: { label: "Blue / orange", up: "#3b82f6", down: "#f59e0b" },
  // Desaturated on purpose: still a green/red pair, but one that stops shouting
  // over the bands and profiles drawn on top of it.
  muted: { label: "Muted green / red", up: "#4f9d76", down: "#b8566b" },
  mono: { label: "White / grey", up: "#d1d4dc", down: "#5d6473" },
} as const;

// The anchored VWAPs. Each fades outward from its mid line so the ±2σ band
// reads as the outer envelope at a glance: Globex in white→grey, NY in magenta,
// weekly in orange (context-only — no engine trades it). `anchored` is the
// user-placed ⚓ tool (click any bar to start a VWAP there): teal, the last
// distinct hue family the chart wasn't already using, so a hand-drawn anchor
// never reads as one of the three fixed session anchors.
//
// NY and the Modern VWAP have traded hues: NY takes the electric magenta the
// study layer used to own, and the study layer takes NY's blue. The separation
// argument that put them 100° apart is unchanged — only which side of the gap
// each sits on. NY's magenta is now the near neighbour of the NY *value area's*
// fuchsia (#e879f9), but those are the same anchor's two layers, and one is a
// dashed profile edge against the other's solid band.
//
// `fill` is the shaded region between ±1σ and ±2σ (see VwapBandPrimitive) — an
// "r, g, b" triplet because the renderer composes its own alpha.
export const vwapPalette = {
  globex: { middle: "#ffffff", band1: "#9aa1ad", band2: "#565d6b", fill: "154, 161, 173" },
  // The magenta ramps outward by lightness the way every other anchor here does
  // — the study layer's flat pink is a dashed hairline and can afford to sit
  // above its mid, a solid five-line envelope cannot.
  ny: { middle: "#ff3ec8", band1: "#e0219f", band2: "#8c1163", fill: "255, 62, 200" },
  weekly: { middle: "#fb923c", band1: "#f97316", band2: "#c2410c", fill: "249, 115, 22" },
  anchored: { middle: "#2dd4bf", band1: "#14b8a6", band2: "#0f766e", fill: "45, 212, 191" },
} as const;

// Modern VWAP [GBB] — blue, swapped with the NY anchor above, which used to
// carry it. Blue is still the hue that keeps this layer clear of the composite's
// rose (#fb7185) and the NY value area's fuchsia (#e879f9); it is now also clear
// of the NY *anchor*, since that one has moved into the magenta this indicator
// used to glow in. What separates the study layer from the session anchors is no
// longer chroma but hue family outright.
//
// `regime` was his palette — purple trending, yellow ranging — and it is the
// part that collided worst: the purple is a shade of the NY anchor and the
// yellow is the 9 EMA's lemon, so with `regimeColor` on (the default) this
// indicator drew itself in two other layers' colours. It is re-cut here as an
// ordered ramp in the indicator's own blue, which is what the axis actually is —
// KER above or below its trailing median, loud to quiet — with grey kept for the
// state that isn't a reading at all. The same call the IB width chip makes above.
// "r, g, b" triplets: the band series composes its own alpha per σ ring.
export const modernVwapPalette = {
  middle: "#60a5fa",
  // The flat band colour, used only when `regimeColor` is off. Lighter than the
  // mid rather than darker — on a near-black surface a dashed hairline loses far
  // more than a solid 2px line does, so it needs the lift to stay a band and not
  // a rumour. The dash and the weight carry the hierarchy instead.
  band: "#93c5fd",
  // The quiet step is quiet by chroma, not by lightness: dropped in brightness
  // instead, the ±2σ ring came out near-white on a dark surface and started
  // reading as the Globex anchor's grey. It has to stay unmistakably blue.
  regime: { trending: "96, 165, 250", ranging: "147, 197, 253", undefined: "128, 132, 145" },
  // The ±1σ→±2σ wash, as every other anchor draws it. Used flat only when
  // `regimeColor` is off; with it on the wash takes the same per-bar regime
  // triplet the bands above it do, so the two channels agree instead of arguing.
  fill: "147, 197, 253",
} as const;

// The app's own directional pair — the green and red the regime ribbon and the
// calendar already agree on — as "r, g, b" triplets rather than finished
// colours, for the layers that mix their own alpha per element: the Zeiierman
// VWAP's per-point line and flags, and the volume profile's delta lane.
//
// Two cuts and not one. Both hues are lighter than white-surface text, so on
// paper the pair reads as a highlighter stroke; the light cut is the same
// opposition darkened until it doesn't. Hoisted rather than written out at each
// use so a green that moves moves everywhere at once.
export const directionalTriplets = {
  dark: {
    up: "33, 192, 122",
    down: "245, 69, 95",
    /** No direction to report — an unflipped swing, a row whose sides cancel. */
    flat: "138, 143, 156",
  },
  light: { up: "13, 124, 74", down: "185, 28, 55", flat: "110, 114, 126" },
} as const;

// Dynamic Swing Anchored VWAP [Zeiierman] — the *other* swing-anchored VWAP (see
// lib/dynamicSwingVwap for why there are two). Its one axis is direction, which
// is a binary opposition rather than the ordered loud→quiet ramp Modern VWAP's
// regime is, so it cannot be re-cut into a single hue the way that one was: it
// takes the directional pair above instead.
export const dynamicSwingVwapPalette = {
  bull: directionalTriplets.dark.up,
  bear: directionalTriplets.dark.down,
  /** Before the first flip, when there is no direction to report. */
  flat: directionalTriplets.dark.flat,
} as const;

// Initial Balance (first 60 min of RTH). Session structure rather than an
// anchor family, so it gets its own hue — lime, which nothing else on the chart
// uses — instead of a shade of an existing one. `ext` is the faint dashed
// 1×/1.5×/2× extension guides: platform convention with no efficacy claim
// (docs/research/initial-balance-orb.md), so they must read as reference marks,
// not levels.
export const ibPalette = {
  line: "#a3e635",
  ext: "rgba(163, 230, 53, 0.45)",
  // Width terciles for the Sessions table chip. An ordered ramp in the IB's own
  // lime — narrow→wide is a scale, not a set of kinds — which also keeps it
  // from being read as the vol-clock chip beside it (that one runs cool→warm,
  // and the two axes are orthogonal: vol-clock §10c).
  width: {
    narrow: "rgba(163, 230, 53, 0.14)",
    mid: "rgba(163, 230, 53, 0.3)",
    wide: "rgba(163, 230, 53, 0.55)",
  },
} as const;

// Developing value areas — one per VWAP anchor, drawn together. The value-area
// edge and the VWAP band it belongs to often land inches apart, and two levels
// that decide a trade must never be mistaken for each other: the Globex area is
// a cool icy-cyan off the Globex white / grey, near its anchor without being it.
// The NY area's fuchsia was the same move against the NY anchor's old violet,
// and with the anchor now in magenta the pair is back to reading by shade and by
// dash weight rather than by hue. `edge` = VAH / VAL (the
// levels the rules test, solid); `poc` = the point of control (dashed, dimmer,
// but bright enough to read).
export const profilePalette = {
  ny: { edge: "#e879f9", poc: "#d946ef" },
  globex: { edge: "#7dd3fc", poc: "#38bdf8" },
  // The weekly value area rides the weekly VWAP's orange, one family per
  // horizon — a weekly level and the weekly band it belongs to read as kin,
  // and nothing else on the chart owns orange.
  weekly: { edge: "#f97316", poc: "#fb923c" },
} as const;

// The multi-session composite (Simulator only): the value the days *behind* this
// one built, frozen at the prior close. Rose, which nothing else on this chart
// uses — the two developing value areas own fuchsia and sky, the viewport
// profile gold and blue, and a composite level has to be tellable from all four
// at a glance, since the whole point of drawing it is that it came from
// somewhere else. HVN warm and LVN cool: one is a price the auction kept coming
// back to, the other one it passed through.
export const compositePalette = {
  poc: "#fb7185",
  edge: "#e11d48",
  fill: "251, 113, 133",
  hvn: "#fda4af",
  lvn: "#94a3b8",
} as const;

// EMAs on the 1-minute grid — the institutional day-trading convention, drawn
// together as one family so they read as a set: the shorter the span, the
// brighter, ramping from lemon (9) through gold (20) into amber (50) and bronze
// (200). The 9/20 are the fast pullback pair; the 50/200 are the slower trend
// reference. Yellow→amber is otherwise unused on the chart (weekly VWAP owns the
// brighter, more-saturated orange, the volume-profile POC a muted gold), so the
// family stands apart from every anchor and value-area line it overlays.
export const emaPalette = {
  fast: "#fde047", // 9 EMA
  slow: "#d4a72c", // 20 EMA
  trend50: "#c58a1e", // 50 EMA
  trend200: "#a06a10", // 200 EMA
} as const;

// Session regime. The ribbon (a per-minute quadrant strip under the candles) and
// the calendar are two views of the same states, so they must agree on colour or
// the eye can't carry a day from one to the other. Deliberately dimmer than the
// price colours: the regime is the backdrop a trade happened in, and it must not
// out-shout the trade drawn on top of it.
export const regimePalette = {
  state: {
    above_both: "rgba(33,192,122,0.75)", // holding above both anchors — the model's day
    below_both: "rgba(245,69,95,0.75)",
    above_gx_only: "rgba(224,165,42,0.6)", // torn between the anchors: churn
    above_ny_only: "rgba(59,130,246,0.6)",
    on_above_gx: "rgba(33,192,122,0.3)", // pre-RTH: one anchor only, so drawn faint
    on_below_gx: "rgba(245,69,95,0.3)",
  },
  klass: {
    trend_up: "rgba(33,192,122,0.5)",
    trend_down: "rgba(245,69,95,0.5)",
    balance: "rgba(59,130,246,0.45)",
    parked: "rgba(144,133,233,0.5)", // one-sided but went nowhere: gap-and-flat
    mixed: "rgba(224,165,42,0.45)",
    unknown: "rgba(138,143,156,0.25)",
  },
  // The daily-ATR vol clock — an ordered scale, not a set of kinds, so it reads
  // cool→warm rather than borrowing the direction colours above (a "hot" day is
  // fast, not bullish or bearish).
  vol: {
    quiet: "rgba(59,130,246,0.35)",
    mid: "rgba(138,143,156,0.35)",
    hot: "rgba(245,110,60,0.45)",
  },
} as const;

// ---------------------------------------------------------------------------
// Chart ink — every palette above, re-cut for the surface it is drawn on.
//
// The palettes above are the dark ink, and they stay exported under their own
// names because the app's own chrome (the Lab pages, the strategy explainers,
// the indicator strip) is dark whatever the *chart* is set to. What is new here
// is the second cut, for the light surfaces.
//
// A light chart is not a background swap. Read against #f7f4ec, the dark ink
// fails in three distinct ways and each needed its own answer:
//
//   erased    — the Globex VWAP mid line is #ffffff and the `mono` candles are
//               #d1d4dc. On paper they are the paper.
//   thin      — the pale ends of every family (blue #60a5fa, sky #7dd3fc,
//               lemon #fde047, lime #a3e635, rose #fda4af) carry plenty of
//               contrast against near-black and almost none against near-white.
//   inverted  — inside a family, the dark ink ramps *brighter* toward the thing
//               it wants read first (the mid line, the POC, the fast EMA). On a
//               light surface prominence runs the other way, so each family's
//               ramp is flipped rather than merely darkened — otherwise the ±2σ
//               ring would out-shout the mid line it belongs to.
//
// What is deliberately NOT re-cut: the direction colours (green/red), the
// long/short blue/orange, and the gold — those are mid-tone already and carry
// meaning that a shifted hue would blur. They read on both.
/** One VWAP anchor's five lines and its wash. `fill` is an "r, g, b" triplet —
 *  the band renderer composes its own alpha. */
export interface BandHue {
  middle: string;
  band1: string;
  band2: string;
  fill: string;
}

export type VwapAnchor = keyof typeof vwapPalette;
export type ProfileAnchor = keyof typeof profilePalette;

export interface ChartInk {
  vwap: Record<VwapAnchor, BandHue>;
  modernVwap: {
    middle: string;
    band: string;
    regime: Record<"trending" | "ranging" | "undefined", string>;
    fill: string;
  };
  /** The Zeiierman swing-flip VWAP. Direction triplets, not hex — the line is
   *  coloured per point and the flags mix their own alpha. */
  dynamicSwingVwap: Record<"bull" | "bear" | "flat", string>;
  /** The higher-timeframe candles drawn over this chart's own (the ATAS
   *  "External Chart" read — see lib/externalChart).
   *
   *  Deliberately *not* the candle scheme's up/down pair. This layer's whole job
   *  is to be read as a different bar from the ones underneath it, and giving it
   *  the same green/red would make a 15-minute box and the 1-minute candles
   *  inside it one visual family — exactly the confusion the outline is there to
   *  avoid. Blue/red is ATAS's own pair and is unclaimed on this chart at these
   *  weights. `range` is the high/low box, which is chrome around the body and
   *  follows the surface's grid rather than the direction. */
  externalChart: { bull: string; bear: string; fillBull: string; fillBear: string; range: string };
  /** The higher-timeframe trend (lib/htfTrend): one gold for every frame's EMA
   *  line — the frames are told apart by dash — and a faint green/red wash for
   *  the frames' agreement. The wash is a background, so it is kept well under
   *  the weight at which it would start competing with the candles' own
   *  green/red; the ribbon is a 5px strip and can afford to be solid. */
  htfTrend: { line: string; washUp: string; washDown: string; ribbonUp: string; ribbonDown: string };
  /** The ranked S/R zones (lib/rankedZones).
   *
   *  Green/red, which the External Chart layer above deliberately avoided — and
   *  the reason that argument does not carry here is shape, not hue. That layer
   *  draws bar-shaped outlines, so the candle scheme's pair would have made a
   *  15-minute box and the candles inside it one family. These are wide
   *  horizontal bands *under* price, which nothing on this chart could mistake
   *  for a candle, and support/resistance is the one place a bull/bear pair is
   *  the reading rather than a decoration.
   *
   *  Earthier and darker than any of the four candle schemes on purpose: this is
   *  a background, and a wash that competes with the bars drawn on top of it has
   *  defeated itself. `bar` is the strength meter inside the zone, which is the
   *  one part that must stay legible over the fill, so it carries most of the
   *  chroma the border and fill gave up. */
  /** USD economic-calendar releases (journal.econ_calendar): a vertical line per
   *  print, coloured by ForexFactory's impact tier. Fuchsia/amber, never the
   *  candle pair: FF's own red is the default down candle, and a release is a
   *  clock event, not a direction. */
  econEvents: { high: string; medium: string; low: string; label: string };
  /** Gamma levels: walls one book carries (dashed), walls both carry (solid),
   *  the flip band's edges and wash, and the tag text. */
  gex: {
    /** Call resistance (C1..) and put support (P1..): one book / both books. */
    call: string;
    callBoth: string;
    put: string;
    putBoth: string;
    flip: string;
    flipFill: string;
    label: string;
  };
  rankedZones: {
    support: string;
    resistance: string;
    supportFill: string;
    resistanceFill: string;
    supportBar: string;
    resistanceBar: string;
    broken: string;
    brokenFill: string;
    text: string;
    barText: string;
  };
  profile: Record<ProfileAnchor, { edge: string; poc: string }>;
  composite: { poc: string; edge: string; fill: string; hvn: string; lvn: string };
  ema: Record<keyof typeof emaPalette, string>;
  ib: { line: string; ext: string; width: Record<"narrow" | "mid" | "wide", string> };
  /** The viewport volume profile drawn in the right gutter (VolumeProfilePrimitive)
   *  and the range tool's own copy of it. Washes, so rgba strings. `axis` is the
   *  hairline baseline, which follows the surface's grid. */
  viewportProfile: { va: string; out: string; poc: string; axis: string };
  /** The net-delta lane drawn beside those rows when the profile is being read
   *  for flow as well as for structure (`deltaBarFill`). Triplets rather than
   *  finished colours, so the lane composes its own alpha. */
  profileDelta: Record<"up" | "down" | "flat", string>;
  /** The developing (session-to-here) profile and its node reading. */
  developingProfile: { va: string; out: string; poc: string; hvn: string; lvn: string };
  /** The range tool's own box, which is chrome around the profile above. */
  rangeBox: { shade: string; shadeSel: string; edge: string; edgeSel: string };
  /** Volume shelves (lib/volumeShelf): the price x time raster of size-per-visit,
   *  and the boxes drawn round the bands that cleared the threshold.
   *
   *  `ramp` is a triplet rather than a finished colour because the raster's whole
   *  content is in its alpha — one hue, opacity carrying the z-score — so the
   *  cell composes it per pixel. Its own hue, not the profile families': a shelf
   *  is a different reading from a value area (size per visit, not size), and
   *  drawing it in the same blue or violet would say they were the same claim. */
  volShelf: { ramp: string; box: string; boxLive: string };
  /** The label chips the canvas primitives draw. The text on them is the layer's
   *  own colour, so only the plate flips — but it has to, or a darkened level
   *  writes its price in dark ink on a near-black chip. */
  chip: { bg: string; strong: string; outline: string };
  /** The stroke round a burst∩absorption overlap (EventBandPrimitive). A
   *  neutral brighter than either event hue, because the collision is a fact
   *  about *both* — colouring it either side's would take sides, and blending
   *  blue with orange makes mud. */
  eventCollide: string;
  /** How heavily the ±1σ→±2σ VWAP wash is laid down (VwapBandPrimitive).
   *
   *  Not a colour but part of the same failure: a wash is a fraction of the
   *  distance between its hue and the surface, and the light ink's hues are far
   *  darker than the dark ink's are light. At the dark chart's 0.3 the weekly
   *  band on paper is a solid orange block across half the pane — the envelope
   *  stops being an envelope and becomes the loudest thing on the chart. */
  bandAlpha: number;
  /** Candle schemes the authored pair doesn't survive on this surface. Anything
   *  absent is used as authored — `tv` and `muted` were already mid-tone pairs
   *  and want no help. */
  candles: Partial<Record<keyof typeof candleSchemes, { up: string; down: string }>>;
}

const darkInk: ChartInk = {
  vwap: vwapPalette,
  modernVwap: modernVwapPalette,
  dynamicSwingVwap: dynamicSwingVwapPalette,
  externalChart: {
    bull: "#4d8bff",
    bear: "#f5455f",
    fillBull: "rgba(77, 139, 255, 0.10)",
    fillBear: "rgba(245, 69, 95, 0.10)",
    range: "rgba(138, 143, 156, 0.55)",
  },
  htfTrend: {
    line: "#facc15",
    washUp: "rgba(34, 197, 94, 0.07)",
    washDown: "rgba(239, 68, 68, 0.07)",
    ribbonUp: "rgba(34, 197, 94, 0.75)",
    ribbonDown: "rgba(239, 68, 68, 0.75)",
  },
  // Cyan calls, the one hue no anchor, profile, zone or candle is drawn in (the
  // ⚓ VWAP's teal is the nearest, and it is one hand-placed line); pale peach
  // puts, far enough off the weekly's saturated orange and the composite's rose
  // to read as their own family, and never as a bear candle.
  gex: {
    call: "rgba(34, 211, 238, 0.55)",
    callBoth: "rgba(34, 211, 238, 0.95)",
    put: "rgba(253, 186, 140, 0.55)",
    putBoth: "rgba(253, 186, 140, 0.95)",
    flip: "rgba(165, 243, 252, 0.7)",
    flipFill: "rgba(34, 211, 238, 0.09)",
    label: "#a5f3fc",
  },
  econEvents: {
    high: "rgba(217, 70, 239, 0.85)",
    medium: "rgba(245, 158, 11, 0.75)",
    low: "rgba(148, 163, 184, 0.55)",
    label: "#e5e7eb",
  },
  rankedZones: {
    support: "#15a06b",
    resistance: "#c2453a",
    supportFill: "rgba(21, 160, 107, 0.11)",
    resistanceFill: "rgba(194, 69, 58, 0.11)",
    supportBar: "rgba(21, 160, 107, 0.5)",
    resistanceBar: "rgba(194, 69, 58, 0.5)",
    broken: "rgba(148, 163, 184, 0.75)",
    brokenFill: "rgba(148, 163, 184, 0.06)",
    text: "rgba(214, 219, 228, 0.75)",
    barText: "rgba(255, 255, 255, 0.92)",
  },
  profile: profilePalette,
  composite: compositePalette,
  ema: emaPalette,
  ib: ibPalette,
  viewportProfile: {
    va: "rgba(59, 130, 246, 0.42)",
    out: "rgba(138, 143, 156, 0.22)",
    poc: "rgba(224, 165, 42, 0.72)",
    axis: palette.grid,
  },
  profileDelta: directionalTriplets.dark,
  developingProfile: {
    va: "rgba(139, 92, 246, 0.46)",
    out: "rgba(139, 92, 246, 0.16)",
    poc: "rgba(196, 181, 253, 0.80)",
    hvn: "#c4b5fd",
    lvn: "#818cf8",
  },
  rangeBox: {
    shade: "rgba(108, 92, 231, 0.10)",
    shadeSel: "rgba(108, 92, 231, 0.16)",
    edge: "rgba(108, 92, 231, 0.55)",
    edgeSel: "rgba(147, 130, 255, 0.95)",
  },
  volShelf: {
    ramp: "249, 115, 22",
    box: "rgba(249, 115, 22, 0.55)",
    boxLive: "rgba(251, 146, 60, 0.95)",
  },
  chip: { bg: "rgba(14, 17, 23, 0.82)", strong: "rgba(14, 17, 23, 0.92)", outline: "rgba(14, 17, 23, 0.85)" },
  eventCollide: "rgba(230, 232, 238, 0.85)",
  bandAlpha: 0.3,
  candles: {},
};

const lightInk: ChartInk = {
  vwap: {
    // Globex is the neutral anchor — white→grey on dark, so near-black→grey
    // here. Same role, same ramp, opposite end of the scale.
    globex: { middle: "#1f2430", band1: "#6b7280", band2: "#a8aeba", fill: "107, 114, 128" },
    ny: { middle: "#9d0876", band1: "#d10a9e", band2: "#e79ad0", fill: "209, 10, 158" },
    weekly: { middle: "#b45309", band1: "#ea580c", band2: "#f5b183", fill: "234, 88, 12" },
    anchored: { middle: "#0f766e", band1: "#14b8a6", band2: "#79cfc6", fill: "15, 118, 110" },
  },
  modernVwap: {
    middle: "#1d4ed8",
    // Darker than the mid, not lighter — the dark ink lifts its bands off a
    // near-black surface, and on paper the same move is downward.
    band: "#1e3a8a",
    // The same ordered ramp as the dark ink, shifted so the quiet step still has
    // somewhere to be: on white a pale blue at 0.45 alpha is white, so the quiet
    // step drops chroma and holds its lightness instead.
    regime: { trending: "29, 78, 216", ranging: "90, 110, 160", undefined: "110, 114, 126" },
    fill: "30, 58, 138",
  },
  // The directional pair, darkened for paper — see `directionalTriplets`.
  dynamicSwingVwap: {
    bull: directionalTriplets.light.up,
    bear: directionalTriplets.light.down,
    flat: directionalTriplets.light.flat,
  },
  profile: {
    ny: { edge: "#a21caf", poc: "#c026d3" },
    globex: { edge: "#075985", poc: "#0284c7" },
    weekly: { edge: "#c2410c", poc: "#ea580c" },
  },
  composite: {
    // Dark ink runs poc pale / edge deep; light ink runs poc deep / edge deeper
    // still, so the POC stays the one you find first.
    poc: "#e11d48",
    edge: "#9f1239",
    fill: "190, 18, 60",
    hvn: "#be123c",
    lvn: "#475569",
  },
  // Lemon→bronze became amber→dark-brown: the same warm ramp, shifted down far
  // enough that even the 9 reads on paper. Ordered so the fast pair still sits
  // apart from the slow one at a glance.
  ema: { fast: "#b45309", slow: "#92400e", trend50: "#78350f", trend200: "#4a2409" },
  ib: {
    line: "#4d7c0f",
    ext: "rgba(77, 124, 15, 0.4)",
    width: {
      narrow: "rgba(77, 124, 15, 0.16)",
      mid: "rgba(77, 124, 15, 0.32)",
      wide: "rgba(77, 124, 15, 0.6)",
    },
  },
  viewportProfile: {
    va: "rgba(37, 99, 235, 0.38)",
    out: "rgba(100, 116, 139, 0.28)",
    poc: "rgba(180, 83, 9, 0.72)",
    axis: "#cfd4dd",
  },
  profileDelta: directionalTriplets.light,
  developingProfile: {
    va: "rgba(109, 40, 217, 0.34)",
    out: "rgba(109, 40, 217, 0.13)",
    poc: "rgba(76, 29, 149, 0.78)",
    hvn: "#6d28d9",
    lvn: "#4338ca",
  },
  rangeBox: {
    shade: "rgba(79, 70, 229, 0.08)",
    shadeSel: "rgba(79, 70, 229, 0.14)",
    edge: "rgba(79, 70, 229, 0.5)",
    edgeSel: "rgba(55, 48, 163, 0.95)",
  },
  // Darker and more saturated than the dark surface's: the raster is carried by
  // alpha, and a light background swallows a pale wash that reads fine on black.
  volShelf: {
    ramp: "194, 65, 12",
    box: "rgba(194, 65, 12, 0.55)",
    boxLive: "rgba(154, 52, 18, 0.95)",
  },
  // The plate flips with the surface; the text stays the layer's own colour.
  chip: { bg: "rgba(255, 255, 255, 0.86)", strong: "rgba(255, 255, 255, 0.94)", outline: "rgba(255, 255, 255, 0.9)" },
  eventCollide: "rgba(43, 49, 59, 0.85)",
  // Both outlines a step darker, and the washes a step lighter: on paper the
  // dark cut's blue reads fine but its 10% fill is invisible against white,
  // while the same fill at the dark chart's weight over a light surface is what
  // turns a hollow box into a solid block.
  externalChart: {
    bull: "#1d4ed8",
    bear: "#be123c",
    fillBull: "rgba(29, 78, 216, 0.09)",
    fillBear: "rgba(190, 18, 60, 0.09)",
    range: "rgba(85, 91, 104, 0.5)",
  },
  // Gold darkened to stay a line on paper; the washes a touch heavier, since a
  // 7% tint over white is close to invisible.
  htfTrend: {
    line: "#a16207",
    washUp: "rgba(22, 163, 74, 0.09)",
    washDown: "rgba(220, 38, 38, 0.09)",
    ribbonUp: "rgba(22, 163, 74, 0.8)",
    ribbonDown: "rgba(220, 38, 38, 0.8)",
  },
  // Same treatment as the External Chart pair above: outlines a step darker and
  // the washes a step lighter, because the dark cut's 11% fill disappears
  // against white while its bar weight turns into a slab.
  gex: {
    call: "rgba(14, 116, 144, 0.55)",
    callBoth: "rgba(14, 116, 144, 0.95)",
    put: "rgba(194, 65, 12, 0.5)",
    putBoth: "rgba(194, 65, 12, 0.9)",
    flip: "rgba(8, 145, 178, 0.75)",
    flipFill: "rgba(8, 145, 178, 0.10)",
    label: "#155e75",
  },
  econEvents: {
    high: "rgba(162, 28, 175, 0.8)",
    medium: "rgba(217, 119, 6, 0.75)",
    low: "rgba(100, 110, 125, 0.5)",
    label: "#1f2937",
  },
  rankedZones: {
    support: "#0f766e",
    resistance: "#9f3a2e",
    supportFill: "rgba(15, 118, 110, 0.09)",
    resistanceFill: "rgba(159, 58, 46, 0.09)",
    supportBar: "rgba(15, 118, 110, 0.42)",
    resistanceBar: "rgba(159, 58, 46, 0.42)",
    broken: "rgba(100, 110, 125, 0.7)",
    brokenFill: "rgba(100, 110, 125, 0.06)",
    text: "rgba(52, 58, 68, 0.8)",
    barText: "rgba(255, 255, 255, 0.95)",
  },
  bandAlpha: 0.13,
  candles: {
    // White/grey inverts wholesale — this scheme is "the candles are the least
    // coloured thing on the chart", and on paper that means dark bodies.
    mono: { up: "#333a45", down: "#a7aeba" },
    // Neon and the default green/red both sit a step too bright for white; the
    // colour-deficient pair loses its amber. `tv` and `muted` are unchanged.
    neon: { up: "#00a651", down: "#e60039" },
    classic: { up: "#12a05f", down: "#d92142" },
    cb: { up: "#2563eb", down: "#d97706" },
  },
};

/** Which ink a surface is drawn in. The only question asked of the surface is
 *  whether it is light — two authored cuts, not a per-surface palette, because
 *  the failure the second cut answers is a contrast direction and every light
 *  surface fails it the same way. */
export function chartInk(surface: keyof typeof chartSurfaces): ChartInk {
  return chartSurfaces[surface].light ? lightInk : darkInk;
}

/** The ink the canvas primitives are drawing in right now.
 *
 *  A module global rather than a prop threaded through a dozen primitives, and
 *  that is faithful rather than lazy: the appearance is one setting for the whole
 *  app (`chart.appearance` in lib/chartPrefs — "Applies to every chart in the
 *  app"), so there is no second answer for a second chart to hold. The primitives
 *  redraw from scratch every frame, so reading it at draw time is all a recolour
 *  costs — no series to walk, no data to hand back. */
let active: ChartInk = darkInk;

export function setActiveInk(next: ChartInk): void {
  active = next;
}

export function ink(): ChartInk {
  return active;
}

/** What a delta bar is filled with, for the two histograms that draw a delta
 *  lane beside their rows (the viewport profile and the fixed-range tool).
 *
 *  Hue is the sign and that is all it carries — which side was the aggressor at
 *  that price. How *much* is the bar's length, which is the channel a histogram
 *  already has and the one an eye reads lengths off. One flat alpha, so a run of
 *  rows leaning the same way reads as one block of that colour rather than as a
 *  gradient nobody can decode. */
export function deltaBarFill(delta: number, ink: ChartInk["profileDelta"]): string {
  return `rgba(${delta > 0 ? ink.up : delta < 0 ? ink.down : ink.flat}, 0.72)`;
}

/** The visit lane's prior-visits segment (`lib/deltaFlow.readVisitLane`),
 *  drawn behind the latest visit's bar.
 *
 *  Same hues, less than half the weight: the two segments are one quantity cut
 *  at one moment, so a third colour would say more than is true, and the wash
 *  is what keeps the standing lean legible as *background* to the visit rather
 *  than as a second bar competing with it. */
export function deltaUnderFill(delta: number, ink: ChartInk["profileDelta"]): string {
  return `rgba(${delta > 0 ? ink.up : delta < 0 ? ink.down : ink.flat}, 0.3)`;
}

/** The same hue at full strength, for the cap and verdict glyph on a *flagged*
 *  delta row (`lib/deltaFlow`).
 *
 *  The mark is deliberately not a fourth colour: a flagged row is one of these
 *  rows, singled out, and giving it its own hue would say it is a different kind
 *  of thing. Opacity is the whole difference — the flagged row is the one in
 *  focus, the rest of the lane stays washed behind it. */
export function deltaFlagInk(delta: number, ink: ChartInk["profileDelta"]): string {
  return `rgb(${delta > 0 ? ink.up : delta < 0 ? ink.down : ink.flat})`;
}

export type Tone = "pos" | "neg" | "neutral";

export function toneOf(x: number | null | undefined): Tone {
  if (x == null) return "neutral";
  if (x > 0) return "pos";
  if (x < 0) return "neg";
  return "neutral";
}
