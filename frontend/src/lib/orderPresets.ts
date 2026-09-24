// Four brackets, one number.
//
// A ticket has five independent distances on it — stop, target, and the
// ladder's back/step/breakeven — and setting them one at a time is how you end
// up trading a shape nobody chose: a 1R target left over from yesterday sitting
// under a trail meant for a trend day. These are the shapes worth having, each
// stated once, each derived from a single reading of the tape.
//
// THE READING. Every preset's stop is one number: the median range of the
// 30-second bars this session has printed since the 09:30 open (`PresetRuler`), and
// nothing when it has not printed three of them yet. Deliberately not the ATR
// the Σ sizer uses: the median is the number that cannot be yanked by the last
// stretch, and a bracket that changes shape because the open was wild is one you
// would have to re-check every time you looked at it. Deliberately not the drawn
// timeframe's ruler either — see `PresetRuler` for why a rule hung off the bars
// you happen to be looking at is eight rules wearing one name. And deliberately
// with no fallback behind it, which is a change: the pre-bell and yesterday legs
// were fitted for a print-counted bar and do not survive the move to a clock
// one. Before the bell — and for the first ninety seconds after it — the
// presets have nothing to say and say it.
//
// WHAT THE FOUR ARE. Not appetites: four answers to "what is this trade
// supposed to do".
//
//   A  the trend one. No target at all — a target is a claim about where the
//      move stops, and on a trending day that claim is the whole cost. The stop
//      rides 5 ticks tighter than the initial one and starts at the entry.
//   B  the 1.5R one. A target that pays, and a breakeven jump — not a trail:
//      the first rung and no other, 3 ticks past the fill, which is a scratch
//      that actually covers the round trip rather than one that books −$14.
//   C  the 1.33R one. B's target pulled in, and **nothing behind it** — no rung,
//      so a move that gets most of the way there and turns is a full stop and
//      not a scratch. B and C are the same trade priced two ways, and the choice
//      between them is the only one on this list that is about the breakeven
//      rather than about the target.
//   D  the 1R one. A stop and a target and nothing else — the two brackets are
//      placed at the fill and neither of them moves again. The trade you take
//      when you expect one push and no more: the target is close enough that a
//      rung jumping in front of it mostly converts a paid 1R into a scratch.
//
// Then every distance leg gets `LEG_ROOM` on top (see there for why it lands on
// the finished bracket rather than on the reading). One consequence worth
// naming: B, C and D are named for the ratio their *shape* has, and five ticks
// on both legs moves it — B is 1.46R off a 45t reading, not 1.50R. The names
// stay because they are what the shapes are, and the panel prints the real
// distances under each of them rather than asking anyone to trust the label.
//
// Nothing here sets size. That is the Σ sizer's question and it is a different
// one (dollars, an account, a contract), so the two stay apart: pick the shape
// here, pick the money there.

export type PresetId = "A" | "B" | "C" | "D";

/** Every distance a preset decides, in ticks. Zero means the leg is off, on
 *  every one of them — which is how A says "no target" and how any of them
 *  could say "no trail". */
export interface PresetBracket {
  stopTicks: number;
  targetTicks: number;
  /** How far behind the best price the stop rides. 0 is the trail off. */
  trailTicks: number;
  /** The grid the stop may rest on. 0 = one rung per `trailTicks`. */
  trailStepTicks: number;
  /** How far past the fill the first rung lands. */
  trailBeTicks: number;
  /** Take the first rung and no other — a breakeven stop, not a trail. */
  trailBeOnly: boolean;
}

export interface OrderPreset {
  id: PresetId;
  /** What the trade is, in a word. */
  name: string;
  /** The shape in one line, for the row that has to be read at a glance. */
  says: string;
  /** The whole bracket, from the one reading. */
  bracket: (stopTicks: number) => PresetBracket;
}

/** Ticks past the fill B's breakeven rung lands on. An NQ round
 *  trip costs about 4 ticks at 1 lot, so 3 is a scratch that is nearly a real
 *  one; 0 would be breakeven gross and a small loss net. */
const BE_TICKS = 3;

/** How much tighter than the initial stop A's trail rides. */
const TREND_TRAIL_INSET = 5;

/**
 * Ticks of room added to every *distance* leg — the stop, the target, and the
 * trail's ride — after the shape is worked out.
 *
 * Applied to the finished bracket rather than to the reading it was built from,
 * and the difference is not cosmetic: adding it to the stop first would put 1.5
 * of it into B's target and half of it into C's trail, so "+5" would quietly
 * mean +7 in one place and +2 in another. Every leg moves by the same five.
 *
 * The two rung knobs are untouched. `trailStepTicks` is a grid rather than a
 * distance, and `trailBeTicks` is measured from the fill — five more ticks of
 * "breakeven" is not breakeven.
 *
 * **A hand-chosen number**, and now the only rule in this file that is not
 * simply "the ruler's reading": nothing measured says a bracket five ticks
 * wider fills better or loses less.
 */
export const LEG_ROOM = 5;

/** The stop a preset actually places, for a ruler reading: the reading plus the
 *  room. Exported because the panel has to show both — the number measured and
 *  the number sent — and they are no longer the same. */
export const stopForReading = (readingTicks: number): number => readingTicks + LEG_ROOM;

/** Add the room to every leg that is switched on.
 *
 *  A leg at zero is a leg that is *off* — that is how A says "no target" — and
 *  off plus five is a target nobody asked for. So the room lands on live legs
 *  only, which is the one rule that keeps "+5 on everything" from turning a
 *  preset into a different preset. */
function withRoom(b: PresetBracket): PresetBracket {
  return {
    ...b,
    stopTicks: b.stopTicks > 0 ? stopForReading(b.stopTicks) : 0,
    targetTicks: b.targetTicks > 0 ? b.targetTicks + LEG_ROOM : 0,
    trailTicks: b.trailTicks > 0 ? b.trailTicks + LEG_ROOM : 0,
  };
}

/** The shapes, before the room — each one stated in terms of the reading alone,
 *  which is the form the rules are actually written in ("1.5R", "half a stop").
 *  `ORDER_PRESETS` below is these with `withRoom` in front, so a preset added
 *  here cannot forget the cushion.
 *
 *  **Order is the order they are offered in**, and it is targets descending from
 *  A's absent one — so the list reads as one axis rather than four labels. A
 *  shape inserted in the middle renumbers the ones under it, which is what
 *  happened to the 1R when 1.33R went in above it. */
const SHAPES: readonly (Omit<OrderPreset, "bracket"> & { shape: OrderPreset["bracket"] })[] = [
  {
    id: "A",
    name: "trending",
    says: "no target, trail from the entry",
    shape: (s) => ({
      stopTicks: s,
      targetTicks: 0,
      trailTicks: Math.max(1, s - TREND_TRAIL_INSET),
      trailStepTicks: 0,
      trailBeTicks: 0,
      trailBeOnly: false,
    }),
  },
  {
    id: "B",
    name: "1.5R",
    says: "target at 1.5R, breakeven at one stop's distance",
    shape: (s) => ({
      stopTicks: s,
      targetTicks: Math.round(s * 1.5),
      trailTicks: s,
      trailStepTicks: 0,
      trailBeTicks: BE_TICKS,
      trailBeOnly: true,
    }),
  },
  {
    id: "C",
    name: "1.33R",
    says: "target at 1.33R, no breakeven",
    shape: (s) => ({
      stopTicks: s,
      // Deliberately B's shape with the rung taken out and the target pulled
      // in, not D's with the target pushed out: the two are the same five
      // numbers either way, but the reason the target moved is that nothing is
      // going to protect the trade on the way there.
      targetTicks: Math.round(s * (4 / 3)),
      trailTicks: 0,
      trailStepTicks: 0,
      trailBeTicks: 0,
      trailBeOnly: false,
    }),
  },
  {
    id: "D",
    name: "1R",
    says: "target at 1R, nothing behind it",
    shape: (s) => ({
      stopTicks: s,
      targetTicks: s,
      trailTicks: 0,
      trailStepTicks: 0,
      trailBeTicks: 0,
      trailBeOnly: false,
    }),
  },
];

export const ORDER_PRESETS: readonly OrderPreset[] = SHAPES.map(({ shape, ...p }) => ({
  ...p,
  bracket: (s) => withRoom(shape(s)),
}));

/**
 * Are these the same bracket? All six legs, and no tolerance.
 *
 * This is what "the chosen preset" means in the card: not a letter remembered
 * from the last click, but the shape the ticket is *carrying right now* matching
 * one on the list. The difference shows the moment a knob is nudged by hand —
 * a remembered letter goes on claiming a shape the ticket no longer has, and
 * the whole reason to light a row is to answer "what am I about to send".
 *
 * Exact, because every leg here is a whole number of ticks and every preset
 * derives from one reading: two shapes that differ by a tick are two shapes.
 */
export function sameBracket(a: PresetBracket | null, b: PresetBracket | null): boolean {
  if (!a || !b) return false;
  return (
    a.stopTicks === b.stopTicks &&
    a.targetTicks === b.targetTicks &&
    a.trailTicks === b.trailTicks &&
    a.trailStepTicks === b.trailStepTicks &&
    a.trailBeTicks === b.trailBeTicks &&
    a.trailBeOnly === b.trailBeOnly
  );
}

/**
 * The stop every preset is built from: the ruler's reading, in whole ticks.
 *
 * A rounding rule and a validity rule, in the one place both panels and the
 * fixture generator can share. Null where the ruler has nothing — before the
 * bell,
 * on a tape too short to have closed three bars, or on a reading of zero, which
 * is a ruler that has spoken and said nothing usable. That null is a real
 * answer and the panels render it as one: the alternative is inventing a
 * distance, and a bracket nobody measured is worse than one you had to set by
 * hand.
 *
 * There is no fallback chain here any more. Before the move to 30-second bars
 * this fell through to a pre-bell median ÷ 1.06 and then to yesterday's settled
 * one — both fitted over 601 sessions for a 500-print bar
 * (docs/research/preset-stop-fallback.md), and both measuring something a clock
 * bar does not. The correction in particular inverted: an overnight print-bar
 * spans more clock time and reads *wide*, an overnight 30-second bar holds
 * fewer prints and reads *narrow*, so the same 1.06 that used to pull a stop in
 * would now push an already-too-tight one tighter.
 */
export function presetStop(readingTicks: number | null): number | null {
  if (readingTicks == null || !(readingTicks > 0)) return null;
  return Math.max(1, Math.round(readingTicks));
}
