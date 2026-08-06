// Calibration instruments for the Replay Simulator — the two reads that are
// about the *day's scale* rather than about a trade.
//
// Both answer questions a chart cannot: "is this a wide morning or a narrow
// one?" and "how much range is realistically left?" — questions whose answers
// are only meaningful against the fortnight before this session, which is the
// one thing the replay's tape does not contain. The denominator (`adr14`) comes
// down with the session payload; everything else here develops from the tape as
// the clock runs.
//
// The design rule for this suite is **context, not signals** (lab-backlog,
// "Simulator: indicator suite"). Every signal-shaped indicator idea in this repo
// has failed its A/B, so neither of these tells you to do anything: one names
// the day's character once its first hour is in, the other says how much of a
// typical day's remaining range has already been spent. They are calibration for
// the eye — the point of the fuel gauge in particular is to make the habit of
// projecting targets the day cannot reach visibly wrong.
//
// Both are causal by construction. `adr14` is the mean range of the fourteen
// sessions *before* this one (knowable at the open); the IB range and the day
// range are read off the tape already played. Nothing here can see forward, which
// matters more in a practice replay than anywhere else in the app.

import { IB_MINUTES, type IbBox, type RangeBox, type SessionContext } from "../../lib/replayEngine";
import { ibPalette, palette } from "../../theme";

export type WidthBucket = "narrow" | "mid" | "wide";

/** narrow / mid / wide against the pinned ADR-unit edges — the same cut
 *  `journal.sim.ib.width_bucket` makes, on edges that arrive from the same
 *  constant, so this chip and the Lab's sessions table can never disagree about
 *  a day. */
export function widthBucket(ibVsAdr: number, [lo, hi]: [number, number]): WidthBucket {
  return ibVsAdr < lo ? "narrow" : ibVsAdr > hi ? "wide" : "mid";
}

/** What the width tercile says about the day's character, from vol-clock §10c.
 *  Recognition, not prediction — the same numbers the sessions-table chip quotes,
 *  and the same caveat travels with them. */
const WIDTH_NOTE: Record<WidthBucket, string> = {
  narrow: "narrow first hour — leans balance/churn (29% balance vs 12% on wide days, 38% churny texture)",
  mid: "middling first hour — sits between the two leans, and the mid tercile is where this study's artifacts kept turning up",
  wide: "wide first hour — leans trend day (60% trend-class vs 47% on narrow days); the wide IB is the trend announcing itself",
};

export interface FuelRead {
  /** New range on offer after the IB, in points: `post_ib_add_x × adr14`. */
  budget: number;
  /** Range the session has added since its IB completed. */
  spent: number;
  /** What's left of the budget — negative once the day has run further than a
   *  typical one does after 10:30. */
  remaining: number;
  /** The day-range projection the budget implies: the morning plus the constant. */
  projected: number;
}

/**
 * The range budget as it stands.
 *
 * vol-clock §10c: after the IB completes a session adds ≈`post_ib_add_x`×ADR14
 * of *new* range, and how wide the morning ran says nothing about it (corr with
 * IB width = +0.01; expansion is 0.39–0.44 in ADR units in every width tercile).
 * So the budget is a flat constant set once the IB is in, and the day spends it.
 *
 * A base rate, and it says nothing about direction — it is how much room is
 * plausibly left, not where the room is.
 */
export function fuelRead(ibRange: number, dayRange: number, adr14: number, addX: number): FuelRead {
  const budget = adr14 * addX;
  // The day range contains the IB range by construction (same window, extended),
  // so this is non-negative; the max is belt-and-braces against a rounding edge.
  const spent = Math.max(0, dayRange - ibRange);
  return { budget, spent, remaining: budget - spent, projected: ibRange + budget };
}

const pts = (n: number) => `${n.toFixed(0)} pts`;

export interface SimIndicatorsProps {
  /** The session's prior-days context. Absent on a payload from an older server. */
  context: SessionContext | null | undefined;
  /** The developing IB and the developing day range, as of the replay clock. */
  ib: IbBox | null;
  range: RangeBox | null;
  open: boolean;
  onToggle: () => void;
}

/**
 * The indicator strip, drawn over the foot of the replay chart.
 *
 * On the chart rather than in a panel because the Simulator's fullscreen mode is
 * the chart and nothing else — an indicator that vanishes exactly when you are
 * concentrating hardest is not one you will ever calibrate against. Collapsible
 * for the same reason the legend is: it is still chart real estate.
 */
export function SimIndicators({ context, ib, range, open, onToggle }: SimIndicatorsProps) {
  // Nothing to say before the bell — both reads start at the open.
  if (!ib || !range) return null;

  if (!open)
    return (
      <div className="sim-ind">
        <button type="button" className="sim-ind-pill" onClick={onToggle} title="Show the day-scale indicators">
          ⛽
        </button>
      </div>
    );

  const adr14 = context?.adr14 ?? null;
  const edges = context?.ib_width_edges;
  const ibRange = ib.high - ib.low;
  const dayRange = range.high - range.low;
  // The pinned edges were measured on a 60-minute IB. If the study's window and
  // the engine's ever part company the ratio is still true but the buckets are
  // not, so the chip drops back to the bare number rather than mislabelling it.
  const sameWindow = (context?.ib_minutes ?? IB_MINUTES) === IB_MINUTES;
  const ibVsAdr = adr14 ? ibRange / adr14 : null;
  const bucket = ib.complete && ibVsAdr != null && edges && sameWindow ? widthBucket(ibVsAdr, edges) : null;

  const adrNote = adr14
    ? `${ibVsAdr!.toFixed(2)}× ADR(14) of ${pts(adr14)}` +
      (edges ? ` · terciles pinned at ${edges[0]}/${edges[1]}× ADR` : "")
    : "no ADR(14) for this session — it sits outside the saved IB study, or inside its first fortnight, so there is no denominator to measure against";

  const ibTitle = ib.complete
    ? `Initial Balance (first ${IB_MINUTES} min of RTH): ${pts(ibRange)} · ${adrNote}` +
      (bucket ? ` · ${WIDTH_NOTE[bucket]}` : sameWindow ? "" : " · the study's IB window differs from this chart's, so no bucket") +
      " · day character at a glance, not a forecast: post-IB expansion is width-flat and narrow days still close outside their IB 62% of the time (vol-clock §10c). The ib_width gate this came from stays off."
    : `The Initial Balance is still forming — first ${IB_MINUTES} min of RTH. Its width is read once, when the window closes.`;

  const fuel = ib.complete && adr14 ? fuelRead(ibRange, dayRange, adr14, context!.post_ib_add_x) : null;
  // Clamped for the bar only: once the day is over budget the bar is simply
  // full, and the overflow is said in words instead of drawn off the end.
  const frac = fuel ? Math.min(1, fuel.spent / fuel.budget) : 0;
  const over = fuel ? fuel.remaining < 0 : false;

  const fuelTitle = !adr14
    ? `No range budget: ${adrNote}.`
    : !ib.complete
      ? `The range budget is set when the IB completes: a session adds ≈${context!.post_ib_add_x}× ADR(14) of new range after that, regardless of how wide the morning was (vol-clock §10c).`
      : `Range budget — after the IB, a session adds ≈${context!.post_ib_add_x}× ADR(14) = ${pts(fuel!.budget)} of new range, and how wide the morning ran says nothing about it (corr +0.01).` +
        ` Spent ${pts(fuel!.spent)} of it so far; the day has run ${pts(dayRange)} against a projection of ${pts(fuel!.projected)}.` +
        (over
          ? " Already past the budget — this day has expanded more than a typical one does after 10:30, so a target further out is asking for range that usually isn't there."
          : " A base rate for how much room is plausibly left, not where it is: this says nothing about direction.");

  return (
    <div className="sim-ind">
      <span className="sim-ind-chip" title={ibTitle}>
        {bucket ? (
          <>
            <span className="sim-ind-bucket" style={{ background: ibPalette.width[bucket] }}>
              {bucket}
            </span>
            <span>IB {pts(ibRange)}</span>
            <span className="sim-ind-dim">{ibVsAdr!.toFixed(2)}×</span>
          </>
        ) : ib.complete ? (
          <>
            <span>IB {pts(ibRange)}</span>
            <span className="sim-ind-dim">{adr14 ? `${ibVsAdr!.toFixed(2)}×` : "no ADR"}</span>
          </>
        ) : (
          <span className="sim-ind-dim">IB forming · {pts(ibRange)}</span>
        )}
      </span>

      <span className="sim-ind-chip" title={fuelTitle}>
        <span aria-hidden>⛽</span>
        {fuel ? (
          <>
            <span className="sim-ind-bar">
              <span
                className="sim-ind-fill"
                style={{ width: `${frac * 100}%`, background: over ? palette.orange : ibPalette.line }}
              />
            </span>
            <span style={over ? { color: palette.orange } : undefined}>
              {over ? `over by ${pts(-fuel.remaining)}` : `${pts(fuel.remaining)} left`}
            </span>
          </>
        ) : (
          <span className="sim-ind-dim">{adr14 ? "budget at IB close" : "no ADR(14)"}</span>
        )}
      </span>

      <button type="button" className="sim-ind-x" onClick={onToggle} title="Hide the day-scale indicators">
        ×
      </button>
    </div>
  );
}
