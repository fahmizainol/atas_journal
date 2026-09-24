import { useState } from "react";
import type { TradeContext } from "../hooks/useTrades";
import type { FilterScope } from "../lib/queryKeys";
import { ContextChart } from "./charts/ContextChart";

/** The four horizons the measurement stores, in minutes. Kept in step with
 *  `journal.trade_context.HORIZONS_MIN` — the column names are built from it. */
const HORIZONS = [1, 5, 15, 30] as const;

/** Below this much tape, a window's numbers describe the clock running out
 *  rather than the market. `journal.trade_context.LONG_MIN` in seconds. */
const FULL_WINDOW_S = 30 * 60;

/** The bar sizes the volatility is measured at, in step with
 *  `journal.trade_context.VOL_RES`. Three rather than one because a scalp lives
 *  on the 500-tick chart and a hold lives on the minute, and a move that is two
 *  bars of one is a fifth of a bar of the other. */
const VOL_RES = [
  { key: "500t", label: "500t" },
  { key: "30s", label: "30s" },
  { key: "1m", label: "1m" },
] as const;

/** What price did around a trade — the approach it was taken into, and the move
 *  it left behind.
 *
 * Rendered after the journal form, for the same reason `LevelStrip` is: these are
 * the machine's numbers, and putting them above the box where the trader writes
 * their own account of the trade would quietly turn a record into a prompt.
 *
 * Two things this deliberately refuses to do.
 *
 * It never renders a verdict. "You exited early" is a threshold on the follow
 * -through, and the threshold belongs to whoever is reading, not to a component
 * that would apply the same one to a scalp and a swing. What it shows instead is
 * the approach's own range, so the reader can see that 30 points of follow
 * -through is a lot after a quiet 20-point approach and unremarkable after an
 * 80-point one.
 *
 * And it never hides a truncated window. A trade exited near the close has no
 * half hour to follow through in, and its zero looks exactly like a market that
 * stood still — so a short window says so in words rather than quietly reporting
 * a number that means something else.
 */
export function ContextStrip({
  context,
  variant = "panel",
  scope,
  tradeKey,
}: {
  context: TradeContext | null;
  /** `inline` drops the panel chrome and inherits its colour from the parent, so
   *  the same component reads correctly inside /charts' dark review panel
   *  without this file having to know anything about that palette. */
  variant?: "panel" | "inline";
  /** Supply both to offer the window as a chart. Omitted — and always in the
   *  `inline` variant — the strip stays numbers-only: the review rail is too
   *  narrow for a chart to say anything the numbers don't, and /charts already
   *  has the tape on screen behind it. */
  scope?: FilterScope;
  tradeKey?: string;
}) {
  // Lazy, like every other chart in the detail view: the bars are a tape slice
  // per call, and most readings of this panel never want the picture.
  const [showChart, setShowChart] = useState(false);
  // Points or multiples of a bar. Default points, because that is the unit the
  // account is denominated in; the other one exists because 20 points means two
  // different trades on a quiet morning and a CPI print.
  const [unit, setUnit] = useState<"pts" | "bars">("pts");
  // Absent is not zero: the session's ticks were never cached, so nothing was
  // measured. Saying nothing beats a row of dashes that reads as "nothing moved".
  if (!context) return null;

  const truncated =
    context.post_avail_s != null && context.post_avail_s < FULL_WINDOW_S;
  const inline = variant === "inline";

  // The yardstick is the MEDIAN minute bar of the approach, not the ATR: one
  // spike in the run-up should not shrink every number underneath it. Rows
  // measured in minutes get compared to a minute bar.
  const barPts =
    context.vol_med_ticks_1m != null && context.tick_size
      ? context.vol_med_ticks_1m * context.tick_size
      : null;
  const inBars = unit === "bars" && barPts != null && barPts > 0;
  const conv = (v: number | null) => (v == null ? null : inBars ? v / barPts! : v);
  const digits = inBars ? 2 : 1;
  const suffix = inBars ? " bars" : "";

  return (
    <div
      className={inline ? undefined : "panel"}
      style={inline ? { marginTop: 6, fontSize: 11, opacity: 0.9 } : { marginTop: 10 }}
    >
      {!inline && <div className="section-title">Measured context</div>}
      {!inline && (
        <div className="muted" style={{ fontSize: "0.85em", marginBottom: 6 }}>
          What price did before the entry and after the exit, in points signed to this
          trade's direction — positive is the way the trade wanted to go.
        </div>
      )}

      {barPts != null && barPts > 0 && (
        <div style={{ marginBottom: inline ? 3 : 6 }}>
          <button
            type="button"
            className={inBars ? "active" : ""}
            style={inline ? { fontSize: 10, padding: "1px 5px" } : undefined}
            onClick={() => setUnit(inBars ? "pts" : "bars")}
            title={`Read the moves below as multiples of the approach's median minute bar (${barPts.toFixed(2)} pts). The same 20 points is a stroll on a quiet morning and half a bar on a fast one.`}
          >
            {inBars ? `× ${barPts.toFixed(2)} pt bar` : "in points"}
          </button>
        </div>
      )}

      {/* Capped rather than fluid: at full panel width the 1m column's number
          drifts halfway across the page from its own row label, and the four
          horizons stop reading as one series. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto repeat(4, minmax(52px, 88px))",
          gap: "2px 10px",
          alignItems: "baseline",
          maxWidth: inline ? undefined : 520,
        }}
      >
        <span />
        {HORIZONS.map((m) => (
          <span key={m} className="section-cap" style={{ textAlign: "right" }}>
            {m}m
          </span>
        ))}

        <Row
          label="into entry"
          title="Net move into the fill. Positive means price had already gone the trade's way before it was paid for — you chased."
          values={HORIZONS.map((m) => conv(num(context, `pre_run_pts_${m}m`)))}
          digits={digits}
        />
        <Row
          label="after, best"
          title="The most the trade could have made had it stayed on."
          values={HORIZONS.map((m) => conv(num(context, `post_mfe_pts_${m}m`)))}
          digits={digits}
        />
        <Row
          label="after, worst"
          title="What holding for that would have meant sitting through first. The best case on its own is half a claim."
          values={HORIZONS.map((m) => conv(num(context, `post_mae_pts_${m}m`)))}
          digits={digits}
        />
      </div>

      <VolBlock context={context} inline={inline} />

      <div
        className={inline ? undefined : "muted"}
        style={{
          fontSize: inline ? 10 : "0.85em",
          marginTop: inline ? 4 : 6,
          display: "flex",
          flexWrap: "wrap",
          gap: inline ? 8 : 12,
          opacity: inline ? 0.75 : undefined,
        }}
      >
        {context.pre_range_pts_15m != null && (
          <span title="High-low of the 15 minutes before the fill. The yardstick every other number here should be read against.">
            approach range {conv(context.pre_range_pts_15m)!.toFixed(digits)}
            {suffix}
            {context.pre_loc_15m != null && (
              <> · filled at {(context.pre_loc_15m * 100).toFixed(0)}% of it</>
            )}
          </span>
        )}
        {context.exit_rank != null && (
          <span title="Fraction of the following 30 minutes whose price the exit beat. 100% means nothing traded better; 50% is a coin flip.">
            exit beat {(context.exit_rank * 100).toFixed(0)}% of the next 30m
          </span>
        )}
        <span title="How long until price traded back through the entry. Never means it did not, for the rest of the session.">
          entry offered again{" "}
          {context.post_ret_entry_s == null
            ? "never"
            : `after ${fmtDuration(context.post_ret_entry_s)}`}
        </span>
      </div>

      {truncated && (
        <div
          className={inline ? undefined : "muted"}
          style={{ fontSize: inline ? 10 : "0.85em", marginTop: 4, opacity: inline ? 0.75 : undefined }}
        >
          Only {fmtDuration(context.post_avail_s!)} of tape followed this exit — the
          longer horizons above ran out of session, and their zeros are the clock.
        </div>
      )}

      {!inline && scope && tradeKey && (
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            className={showChart ? "active" : ""}
            onClick={() => setShowChart(!showChart)}
            title="Half an hour either side of the trade, with the entry level and what the next 30 minutes actually traded through"
          >
            {showChart ? "▾ Hide the window" : "▸ Show the window"}
          </button>
          {showChart && (
            <div style={{ marginTop: 6 }}>
              <ContextChart scope={scope} tradeKey={tradeKey} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  title,
  values,
  digits = 1,
  signed = true,
}: {
  label: string;
  title: string;
  values: (number | null)[];
  digits?: number;
  /** Off for readings that have no direction to take — a bar size is never
   *  "negative", and colouring one green would claim something it cannot know. */
  signed?: boolean;
}) {
  return (
    <>
      <span className="section-cap" title={title} style={{ whiteSpace: "nowrap" }}>
        {label}
      </span>
      {values.map((v, i) => (
        <span
          key={i}
          style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}
          className={
            v == null || !signed
              ? v == null
                ? "muted"
                : undefined
              : v > 0
                ? "pos"
                : v < 0
                  ? "neg"
                  : undefined
          }
        >
          {v == null ? "–" : `${signed && v > 0 ? "+" : ""}${v.toFixed(digits)}`}
        </span>
      ))}
    </>
  );
}

/** How big the bars were when the trade was taken, and what the bar the fill
 *  landed in was doing.
 *
 * Three resolutions rather than one because the same tape is a different market
 * depending on what you were watching it on: eight ticks is a big 500-tick bar
 * and a quiet minute. In ticks, so it reads as the chart's vol ruler does.
 *
 * The entry bar's body is signed to the trade, which is the only form of "was it
 * green" that survives having both longs and shorts in one journal. What is
 * deliberately not here is "was the candle closed" — off the tape every bar is
 * closed, and the live question it stands in for is how much of the bar had
 * printed when the order went in, which is what `printed` says.
 */
function VolBlock({ context, inline }: { context: TradeContext; inline: boolean }) {
  const at = (stem: string) => VOL_RES.map((r) => num(context, `${stem}_${r.key}`));
  const vols = at("vol_atr_ticks");
  const meds = at("vol_med_ticks");
  // Nothing measured at any resolution — a pre-v2 row. Say nothing rather than
  // draw a grid of dashes that reads as a flat market.
  if (vols.every((v) => v == null) && meds.every((v) => v == null)) return null;

  const locs = at("eb_loc");
  const elapsed = at("eb_elapsed");

  return (
    <div style={{ marginTop: inline ? 5 : 10 }}>
      {!inline && (
        <div className="section-cap" style={{ marginBottom: 3 }}>
          bar size at the fill, in ticks
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto repeat(3, minmax(52px, 88px))",
          gap: "2px 10px",
          alignItems: "baseline",
          maxWidth: inline ? undefined : 520,
        }}
      >
        <span />
        {VOL_RES.map((r) => (
          <span key={r.key} className="section-cap" style={{ textAlign: "right" }}>
            {r.label}
          </span>
        ))}

        <Row
          label="ATR(14)"
          title="Wilder ATR over the 14 bars before the fill — what the chart's vol ruler was showing when the order went in."
          values={vols}
          signed={false}
        />
        <Row
          label="median bar"
          title="Median bar range over the 30 minutes into the fill. The same reading as the ATR, without one spike being able to move it."
          values={meds}
          signed={false}
        />
        <Row
          label="entry bar"
          title="Body of the bar the fill landed in, SIGNED TO THE TRADE: positive means that bar was going the trade's way when it was entered."
          values={at("eb_body_ticks")}
        />
        <Row
          label="filled at %"
          title="Where in that bar the fill sat: 0% is its low, 100% its high. Raw, not signed — buying a bar's high and selling it are different trades. Outside 0-100% means this was a scaled entry whose average price the entry bar never traded."
          values={locs.map((v) => (v == null ? null : v * 100))}
          digits={0}
          signed={false}
        />
        <Row
          label="bar printed %"
          title="How much of the bar had traded when the fill happened. Low means the bar you reacted to barely existed yet."
          values={elapsed.map((v) => (v == null ? null : v * 100))}
          digits={0}
          signed={false}
        />
      </div>
    </div>
  );
}

function num(ctx: TradeContext, key: string): number | null {
  const v = (ctx as unknown as Record<string, number | null>)[key];
  return typeof v === "number" ? v : null;
}

function fmtDuration(s: number): string {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}
