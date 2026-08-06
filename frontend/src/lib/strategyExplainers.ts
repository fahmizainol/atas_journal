// Visual strategy descriptions — a spec-driven replacement for the prose
// `description` on the Strategies list card and detail header. A strategy that
// has an entry here renders the StrategyExplainer (rule chips + annotated
// diagrams) with the prose demoted behind a toggle; a strategy without one
// falls back to its plain `description` unchanged. The diagrams are idealised,
// hand-authored teaching charts (static SVG) — they show the RULE, not a real
// session — so every value is a round number chosen for legibility, not a real
// print. Adding a strategy is data-only: fill in chips + figures below.

import { palette, vwapPalette } from "../theme";

const NY = vwapPalette.ny.middle; // #c4b5fd — the NY VWAP target hue
const GX_FAINT = vwapPalette.globex.band1; // #9aa1ad — context Globex VWAP

// ---- chart geometry (mirrors the fields the renderer reads) -----------------

export interface ExCandle {
  o: number;
  h: number;
  l: number;
  c: number;
  /** Override the up/down colour (e.g. a neutral context candle). */
  col?: string;
  /** Dashed ring drawn around the whole candle to call it out. */
  ring?: string;
  /** Draw at half opacity — a de-emphasised context candle. */
  dim?: boolean;
}

export interface ExHLine {
  p: number;
  color: string;
  label?: string;
  /** SVG dash pattern, e.g. "5 3". Omit for a solid line. */
  dash?: string;
  width?: number;
  opacity?: number;
  /** Which end the label sits at. Default "right". */
  side?: "left" | "right";
}

export interface ExZone {
  pTop: number;
  pBot: number;
  /** Candle indices the band spans (inclusive), padded by one candle width. */
  i0: number;
  i1: number;
  color: string;
  opacity?: number;
  label?: string;
  labelColor?: string;
}

export interface ExVwap {
  /** One price per candle index. */
  pts: number[];
  color: string;
  width?: number;
  dash?: string;
  opacity?: number;
}

export interface ExMarker {
  /** Candle index (fractional allowed — 6.15 sits just right of candle 6). */
  i: number;
  p: number;
  /** A glyph drawn centred on (i, p): "▲", "✓", "✕". */
  glyph?: string;
  color: string;
  /** Nudge the glyph vertically (px, +down). */
  dy?: number;
  /** Nudge the glyph horizontally (px). */
  gdx?: number;
  size?: number;
  /** A text label, positioned relative to (i, p) by labelDx/labelDy. */
  label?: string;
  labelDx?: number;
  labelDy?: number;
  anchor?: "start" | "middle" | "end";
  /** Draw a leader line from (i, p) to the label. */
  leader?: boolean;
}

export interface ExChart {
  /** viewBox width/height — sets the drawing aspect ratio. */
  aspect: [number, number];
  pMin: number;
  pMax: number;
  pad?: { l?: number; r?: number; t?: number; b?: number };
  candles: ExCandle[];
  hlines?: ExHLine[];
  zones?: ExZone[];
  vwaps?: ExVwap[];
  markers?: ExMarker[];
}

// ---- figure + explainer shells ----------------------------------------------

export interface ExSubPanel {
  verdict: "skip" | "take";
  verdictLabel: string;
  note: string;
  chart: ExChart;
  ariaLabel: string;
}

export interface ExFigure {
  num: string;
  title: string;
  caption: string;
  /** "single" reads `chart`; "split" reads `panels`. */
  layout: "single" | "split";
  chart?: ExChart;
  ariaLabel?: string;
  panels?: ExSubPanel[];
}

export interface ExChip {
  key: string;
  /** The dot colour (hex). */
  dot: string;
  main: string;
  /** A trailing, de-emphasised qualifier ("— …"). */
  em?: string;
}

export interface StrategyExplainer {
  /** One short line for the list card. */
  tagline: string;
  /** The lead sentence above the toggle on the detail header. */
  subtitle: string;
  chips: ExChip[];
  figures: ExFigure[];
}

// ---- drift-touch-fade -------------------------------------------------------

const driftTouchFade: StrategyExplainer = {
  tagline: "Fade a level price drifted into rather than approached.",
  subtitle:
    "Fade a level that price drifted into rather than approached — contact with no impulse behind it has nothing to carry it through.",
  chips: [
    { key: "Setup", dot: palette.gold, main: "Slow drift into a developing level", em: "not a momentum approach" },
    { key: "Side", dot: palette.accent, main: "Fade the side price hugged", em: "long above, short below" },
    { key: "Entry", dot: palette.green, main: "A: touch-bar close · B: confirm close beyond it" },
    { key: "Stop", dot: palette.red, main: "Behind the zone", em: "measured from the level" },
    { key: "Target", dot: NY, main: "NY VWAP", em: "or fixed R / fixed distance" },
  ],
  figures: [
    {
      num: "01",
      title: "Does the touch qualify?",
      layout: "split",
      caption:
        "A drift touch means the level already absorbed minutes of trade next to it without breaking, so price wiggles into contact with no net momentum. A fast approach is a momentum test — nothing has stalled at the level, so there's nothing to fade.",
      panels: [
        {
          verdict: "skip",
          verdictLabel: "✕ Skip",
          note: "fast approach — a momentum test",
          ariaLabel: "Fast momentum candles diving into a level",
          chart: {
            aspect: [340, 250],
            pMin: 20993,
            pMax: 21033,
            pad: { l: 10, r: 12, t: 16, b: 20 },
            candles: [
              { o: 21028, h: 21030, l: 21024, c: 21025 },
              { o: 21025, h: 21026, l: 21016, c: 21017 },
              { o: 21017, h: 21018, l: 21007, c: 21008 },
              { o: 21008, h: 21009, l: 20999, c: 21001, ring: palette.red },
            ],
            hlines: [{ p: 21000, color: palette.gold, dash: "5 3", label: "level", side: "left" }],
            markers: [
              { i: 3, p: 20999, glyph: "✕", color: palette.red, dy: 20, size: 15 },
              { i: 1.5, p: 21023, color: palette.muted, label: "momentum", anchor: "middle" },
            ],
          },
        },
        {
          verdict: "take",
          verdictLabel: "✓ Fade",
          note: "drift touch — loitered, then wiggled in",
          ariaLabel: "Small candles loitering above a level then drifting into a touch",
          chart: {
            aspect: [340, 250],
            pMin: 20993,
            pMax: 21033,
            pad: { l: 10, r: 12, t: 16, b: 20 },
            candles: [
              { o: 21012, h: 21014, l: 21010, c: 21011 },
              { o: 21011, h: 21013, l: 21008, c: 21010 },
              { o: 21010, h: 21012, l: 21007, c: 21009 },
              { o: 21009, h: 21010, l: 21005, c: 21007 },
              { o: 21007, h: 21008, l: 21002, c: 21004 },
              { o: 21004, h: 21005, l: 20999, c: 21003, ring: palette.green },
            ],
            hlines: [{ p: 21000, color: palette.gold, dash: "5 3", label: "level", side: "left" }],
            zones: [{ pTop: 21001.5, pBot: 20998.5, i0: 3, i1: 5, color: palette.gold, opacity: 0.16 }],
            markers: [
              { i: 5, p: 20999, glyph: "✓", color: palette.green, dy: 20, size: 15 },
              { i: 2, p: 21012, color: palette.muted, label: "loiter", anchor: "middle" },
            ],
          },
        },
      ],
    },
    {
      num: "02",
      title: "The trade — support fade (long)",
      layout: "single",
      caption:
        "Price hugs above a developing level, drifts down and touches it. Variant B waits for a bar to close back beyond the touch bar before filling long. The stop sits behind the zone (from the level, not the fill); the target fades toward value at the NY VWAP.",
      ariaLabel:
        "Full drift-touch fade trade: drift touch, variant B confirm, long entry, stop behind the zone, and NY VWAP target",
      chart: {
        aspect: [720, 344],
        pMin: 20988,
        pMax: 21031,
        pad: { l: 14, r: 128, t: 22, b: 22 },
        candles: [
          { o: 21022, h: 21026, l: 21018, c: 21020 },
          { o: 21020, h: 21022, l: 21014, c: 21015 },
          { o: 21015, h: 21017, l: 21009, c: 21011 },
          { o: 21011, h: 21013, l: 21005, c: 21007 },
          { o: 21007, h: 21009, l: 21001, c: 21003 },
          { o: 21003, h: 21004, l: 20999, c: 21001, ring: palette.gold }, // drift touch
          { o: 21001, h: 21008, l: 21000, c: 21007, ring: palette.green }, // variant B confirm
          { o: 21007, h: 21014, l: 21006, c: 21012 },
          { o: 21012, h: 21020, l: 21011, c: 21018 },
          { o: 21018, h: 21026, l: 21016, c: 21024 }, // hits target
        ],
        hlines: [
          { p: 21024, color: NY, width: 1.6, label: "TARGET · NY VWAP", side: "right" },
          { p: 21000, color: palette.gold, dash: "5 3", width: 1.5, label: "developing level", side: "right" },
          { p: 20992, color: palette.red, dash: "5 3", width: 1.5, label: "STOP · behind zone", side: "right" },
        ],
        zones: [
          { pTop: 21001.8, pBot: 20998.2, i0: 5, i1: 6, color: palette.gold, opacity: 0.16, label: "drift touch", labelColor: palette.gold },
        ],
        vwaps: [
          { pts: [21019, 21019.6, 21020.2, 21020.8, 21021.4, 21022, 21022.6, 21023.1, 21023.6, 21024], color: NY, width: 1.7 },
          { pts: [21014, 21013.6, 21013.2, 21012.9, 21012.6, 21012.3, 21012.1, 21012, 21011.9, 21011.8], color: GX_FAINT, width: 1.2, opacity: 0.5 },
        ],
        markers: [
          { i: 6, p: 20998, glyph: "▲", color: palette.green, dy: 6, size: 15, label: "enter long", labelDy: 26, anchor: "middle" },
          { i: 6, p: 21008, color: palette.green, label: "confirm (B)", anchor: "middle", labelDy: -10 },
          { i: 9, p: 21024, glyph: "✓", color: NY, dy: -14, size: 14 },
        ],
      },
    },
  ],
};

export const strategyExplainers: Record<string, StrategyExplainer> = {
  "drift-touch-fade": driftTouchFade,
};
