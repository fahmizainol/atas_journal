// Drawing the delta lane — the canvas half of what `lib/deltaFlow` decides.
//
// The viewport histogram and the fixed-range tool both draw this lane, growing in
// opposite directions, and until now each carried its own copy of the bar
// formula. That was survivable while the formula was one line; with a scale mode
// and a flag mark on it, two copies is two lanes that can disagree about what the
// reader is looking at. So the row draw lives here and both call it.
//
// Split from `deltaFlow` rather than folded into it because that module is pure
// arithmetic over a profile and is unit-tested under node, where there is no
// canvas. Keeping the ctx out of it is what keeps it testable.

import { deltaBarFill, deltaFlagInk, deltaUnderFill, type ChartInk } from "../theme";
import type { Verdict } from "./deltaFlow";

/** Width of the solid cap on a flagged row's bar tip. */
const CAP = 2;
/** Half-height of a verdict glyph, and the smallest row it will fit in. A row on
 *  a dense profile is a pixel or two tall; a glyph drawn there is a smear, and
 *  the cap alone still says the row was flagged. */
const GLYPH = 3.5;
const GLYPH_MIN_H = 5;

/**
 * One row's delta bar, plus the cap that marks it flagged.
 *
 * `base` is the lane's baseline and `dir` which way its bars grow from it: -1 for
 * the viewport histogram (leftward, into the right gutter), +1 for the range
 * tool (rightward, out of the selection's left edge). `frac` is the share of
 * `max` this bar spans — `deltaFlow.readLane` owns that number, and no scaling
 * decision is taken here.
 *
 * A flagged row is capped even when its bar has no length. That is not a
 * degenerate case to paper over: a row that netted flat in a session where every
 * other row leaned one way is exactly the kind of row the flag exists to find,
 * and it would otherwise be the one row marked invisibly. The cap grows outward
 * from the tip so it never eats into the bar it belongs to.
 *
 * `under` is the visit lane's prior-visits segment (`LaneReading.under`), laid
 * down washed *before* the main bar so the current visit always wins where they
 * overlap — the standing lean shows as the tail past the visit's tip, or not at
 * all when the visit outran it, and that is the honest ordering: the solid bar
 * is the present tense. When the two disagree the caller flags the row, so a
 * fully covered prior still announces itself through the cap.
 */
export function drawDeltaRow(
  ctx: CanvasRenderingContext2D,
  delta: number,
  frac: number,
  flagged: boolean,
  base: number,
  max: number,
  dir: 1 | -1,
  y: number,
  h: number,
  inkDelta: ChartInk["profileDelta"],
  under?: { frac: number; delta: number },
): void {
  if (under) {
    const uw = Math.max(0, under.frac) * max;
    if (uw > 0) {
      ctx.fillStyle = deltaUnderFill(under.delta, inkDelta);
      ctx.fillRect(dir < 0 ? base - uw : base, y, uw, h);
    }
  }
  const dw = Math.max(0, frac) * max;
  if (dw > 0) {
    ctx.fillStyle = deltaBarFill(delta, inkDelta);
    ctx.fillRect(dir < 0 ? base - dw : base, y, dw, h);
  }
  if (!flagged) return;
  const tip = base + dir * dw;
  ctx.fillStyle = deltaFlagInk(delta, inkDelta);
  ctx.fillRect(dir < 0 ? tip - CAP : tip, y, CAP, h);
}

/**
 * A flagged row's verdict, drawn in a column clear of the lane's longest bar.
 *
 * A column rather than a mark at each bar's own tip: the glyphs are being
 * compared with each other — which of the marked rows held and which ran — and
 * lining them up is what makes that a glance instead of a scan. `x` is the
 * column's centre; the caller places it past the lane's full extent so a
 * full-length bar can't collide with it.
 *
 * Initiative is a triangle pointing the way the row's aggressors pushed, so the
 * direction survives even in a theme where the two hues are close. Absorbed is a
 * diamond — a shape that points nowhere, which is the reading.
 */
export function drawVerdict(
  ctx: CanvasRenderingContext2D,
  verdict: Verdict,
  delta: number,
  x: number,
  y: number,
  h: number,
  inkDelta: ChartInk["profileDelta"],
): void {
  if (h < GLYPH_MIN_H) return;
  const cy = y + h / 2;
  ctx.fillStyle = deltaFlagInk(delta, inkDelta);
  ctx.beginPath();
  if (verdict === "initiative") {
    const up = delta > 0 ? -1 : 1;
    ctx.moveTo(x, cy + up * GLYPH);
    ctx.lineTo(x - GLYPH, cy - up * GLYPH);
    ctx.lineTo(x + GLYPH, cy - up * GLYPH);
  } else {
    ctx.moveTo(x, cy - GLYPH);
    ctx.lineTo(x + GLYPH, cy);
    ctx.lineTo(x, cy + GLYPH);
    ctx.lineTo(x - GLYPH, cy);
  }
  ctx.closePath();
  ctx.fill();
}

/** Gap between the lane's full extent and the verdict column's centre. */
export const VERDICT_GAP = GLYPH + 3;
