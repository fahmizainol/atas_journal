// Tape events as a distribution against the price axis — the *marginal* — drawn
// as an outline over a volume profile so a hump and the event mass that landed
// in it can be compared directly.
//
// A port of the demo page's `drawMarginal` (demo/_composite_profile_template.html),
// and it answers a different question from the bands on the candles. A band says
// "this happened here, then". The marginal says "of all the size that arrived in
// bursts, this is where it went" — which is the only form in which an event
// overlay can be laid against a profile at all, since a profile has no time axis.
//
// Its whole point is the comparison, including when the comparison says nothing.
// Events land where price traded and price traded most where value is, so an
// event peak sitting on the POC is true of *any* subset of the tape, a random
// one included. Read the outline against the histogram's shape, not against the
// levels: the interesting picture is the one where the two disagree.

import type { TapeEvent } from "./replayEngine";

/** Bins across the profile's price range. The demo's, and about the resolution
 *  a 200px-tall gutter can actually show. */
export const MARGINAL_BINS = 46;

/** The demo page's own two hues, kept so the overlay reads the same here as it
 *  does there. Both are drawn only inside a profile's gutter, where nothing else
 *  on this chart draws — the bands on the candles stay coloured by aggressor. */
export const MARGINAL_COLORS: Record<TapeEvent["kind"], string> = {
  sweep: "#ff7a45",
  absorb: "#e879f9",
};

/**
 * Event lots binned onto the price axis, normalised to the heaviest bin.
 *
 * A zone spans prices, so its lots are spread across the bins it covered rather
 * than dropped on its midpoint — an absorption 40pt tall is not an event at one
 * price. Returns null when nothing of this kind is in range.
 */
export function eventMass(
  events: TapeEvent[],
  kind: TapeEvent["kind"],
  lo: number,
  hi: number,
  bins = MARGINAL_BINS,
): Float64Array | null {
  const span = hi - lo || 1;
  const w = new Float64Array(bins);
  let any = false;
  for (const e of events) {
    if (e.kind !== kind || e.hi < lo || e.lo > hi) continue;
    const a = Math.max(0, Math.floor(((e.lo - lo) / span) * bins));
    const b = Math.min(bins - 1, Math.floor(((e.hi - lo) / span) * bins));
    if (b < a) continue;
    const share = e.lots / (b - a + 1);
    for (let i = a; i <= b; i++) w[i] += share;
    any = true;
  }
  if (!any) return null;
  let top = 0;
  for (let i = 0; i < bins; i++) if (w[i] > top) top = w[i];
  if (top <= 0) return null;
  for (let i = 0; i < bins; i++) w[i] /= top;
  return w;
}

/**
 * Draw both kinds' marginals over a profile.
 *
 * `xBase` is the histogram's baseline and `dir` the direction its rows grow in
 * (+1 rightward, −1 leftward), so the outline is measured off exactly the same
 * edge and width as the bars it is being compared against — pass the profile's
 * own geometry and the two can never drift apart.
 */
export function drawEventMarginal(
  ctx: CanvasRenderingContext2D,
  y: (price: number) => number | null,
  events: TapeEvent[],
  lo: number,
  hi: number,
  xBase: number,
  width: number,
  dir: 1 | -1,
): void {
  if (!events.length || !(hi > lo) || width <= 0) return;
  const span = hi - lo;
  for (const kind of ["sweep", "absorb"] as const) {
    const m = eventMass(events, kind, lo, hi);
    if (!m) continue;
    ctx.save();
    ctx.strokeStyle = MARGINAL_COLORS[kind];
    ctx.lineWidth = 1.3;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let started = false;
    // Bins march up in price, and each one is emitted low edge first: the other
    // order sends every segment back down the screen across the bin below it,
    // and the crossings read as a row of triangles rather than a staircase.
    for (let i = 0; i < m.length; i++) {
      const y0 = y(lo + (span * i) / m.length);
      const y1 = y(lo + (span * (i + 1)) / m.length);
      if (y0 == null || y1 == null) continue;
      const x = xBase + dir * m[i] * width;
      if (!started) {
        ctx.moveTo(x, y0);
        started = true;
      } else {
        ctx.lineTo(x, y0);
      }
      ctx.lineTo(x, y1);
    }
    if (started) ctx.stroke();
    ctx.restore();
  }
}
