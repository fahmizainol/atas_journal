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
// Every one is dark, and that is a constraint rather than a shortlist that
// happens to be short: the indicator hues were chosen against a dark surface —
// the Globex VWAP's mid line is literally #ffffff — so a light surface would
// erase levels rather than restyle them. A light chart is a re-cut of the
// indicator palette, not a background swap.
//
// The two axes worth having are brightness (how much contrast the candles get)
// and temperature (which is pure preference). What is NOT here is a surface
// tinted toward a hue an indicator family already owns — no violet, because the
// NY VWAP lives there; no teal, because the ⚓ anchored VWAP does. A surface that
// competes with a level is the one way this knob could cost you a read.
//
// Default first, then darkest-to-lightest, then the warm one. Each carries its
// own grid and axis text: a grid tuned for #0e1117 disappears on black and
// glares on gunmetal.
export const chartSurfaces = {
  charcoal: { label: "Charcoal", bg: palette.bg, grid: palette.grid, text: palette.text },
  black: { label: "Black", bg: "#000000", grid: "#1e1e1e", text: "#dcdfe6" },
  midnight: { label: "Midnight", bg: "#0a0e1a", grid: "#1e2333", text: "#dde1ec" },
  slate: { label: "Slate", bg: "#131722", grid: "#2a2e39", text: "#d1d4dc" },
  graphite: { label: "Graphite", bg: "#181a20", grid: "#31343d", text: "#e6e8ee" },
  // The softest of them — for a bright room, where a near-black chart is a mirror.
  gunmetal: { label: "Gunmetal", bg: "#1c1f26", grid: "#363a44", text: "#eaecf2" },
  // The one warm option. Safe despite the orange/gold families (weekly VWAP, the
  // EMAs) because it is far darker than any of them — it shifts the neutral, it
  // doesn't approach a line's own hue.
  warm: { label: "Warm dark", bg: "#17130f", grid: "#332c24", text: "#ece5db" },
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
// reads as the outer envelope at a glance: Globex in white→grey, NY in purple,
// weekly in orange (context-only — no engine trades it). `anchored` is the
// user-placed ⚓ tool (click any bar to start a VWAP there): teal, the last
// distinct hue family the chart wasn't already using, so a hand-drawn anchor
// never reads as one of the three fixed session anchors.
// `fill` is the shaded region between ±1σ and ±2σ (see VwapBandPrimitive) — an
// "r, g, b" triplet because the renderer composes its own alpha.
export const vwapPalette = {
  globex: { middle: "#ffffff", band1: "#9aa1ad", band2: "#565d6b", fill: "154, 161, 173" },
  ny: { middle: "#c4b5fd", band1: "#8b5cf6", band2: "#5b3fb8", fill: "139, 92, 246" },
  weekly: { middle: "#fb923c", band1: "#f97316", band2: "#c2410c", fill: "249, 115, 22" },
  anchored: { middle: "#2dd4bf", band1: "#14b8a6", band2: "#0f766e", fill: "45, 212, 191" },
} as const;

// Modern VWAP [GBB] — pink, the last hue family none of the four anchors above
// (white / violet / orange / teal) or the mark layers (blue, orange, lime, gold)
// had claimed. It has to be separable from those at a glance: this one is a
// study layer, and the moment it reads as one of the session anchors it is
// telling you something it hasn't earned.
//
// `regime` is his own palette for the two-axis read, kept because the quadrant
// colouring is the only place the regime is visible at all — purple trending,
// yellow ranging, grey not enough history to say. "r, g, b" triplets: the band
// series composes its own alpha per σ ring.
export const modernVwapPalette = {
  middle: "#f472b6",
  band: "#9d5a7c",
  regime: { trending: "184, 77, 255", ranging: "255, 230, 0", undefined: "120, 123, 134" },
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

// Developing value areas — one per VWAP anchor, drawn together. Each borrows its
// anchor's VWAP family but sits a distinct shade off it, because the value-area
// edge and the VWAP band it belongs to often land inches apart and two levels that
// decide a trade must never be mistaken for each other: the NY area on the magenta
// / fuchsia side of the NY indigo-violet, the Globex area a cool icy-cyan off the
// Globex white / grey. `edge` = VAH / VAL (the levels the rules test, solid);
// `poc` = the point of control (dashed, dimmer, but bright enough to read).
export const profilePalette = {
  ny: { edge: "#e879f9", poc: "#d946ef" },
  globex: { edge: "#7dd3fc", poc: "#38bdf8" },
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

export type Tone = "pos" | "neg" | "neutral";

export function toneOf(x: number | null | undefined): Tone {
  if (x == null) return "neutral";
  if (x > 0) return "pos";
  if (x < 0) return "neg";
  return "neutral";
}
