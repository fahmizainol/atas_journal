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
  text: "#e6e8ee",
  muted: "#8a8f9c",
  grid: "#2a2e38",
} as const;

// The two anchored VWAPs. Each fades outward from its mid line so the ±2σ band
// reads as the outer envelope at a glance: Globex in white→grey, NY in purple.
// `fill` is the shaded region between ±1σ and ±2σ (see VwapBandPrimitive) — an
// "r, g, b" triplet because the renderer composes its own alpha.
export const vwapPalette = {
  globex: { middle: "#ffffff", band1: "#9aa1ad", band2: "#565d6b", fill: "154, 161, 173" },
  ny: { middle: "#c4b5fd", band1: "#8b5cf6", band2: "#5b3fb8", fill: "139, 92, 246" },
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
} as const;

export type Tone = "pos" | "neg" | "neutral";

export function toneOf(x: number | null | undefined): Tone {
  if (x == null) return "neutral";
  if (x > 0) return "pos";
  if (x < 0) return "neg";
  return "neutral";
}
