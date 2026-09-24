// One bracket leg's distance box, in whichever unit you think in.
//
// The stop and the target are set in five places on this app — the replay's
// long-press ticket, its setup panel, the floating ticket's knobs, Live's setup
// drawer and the routing order pad — and every one of them used to be a tick
// box with the money printed beside it, read-only. That is the wrong way round
// for the number that is actually being chosen: nobody decides to be wrong by
// 50 ticks, they decide to be wrong by $250, and the tick count is the
// translation.
//
// So the box takes either, and the button on its end says which. Pinned to
// dollars the tick distance re-derives itself as the size and the routed
// contract move (lib/bracketUsd) — which is the whole point, because that
// arithmetic is the part that silently goes wrong when a one-lot ticket is
// carried into a two-lot sitting.
//
// One component rather than five copies for the reason the ticket card gives at
// length: a second implementation of the same control is how two surfaces come
// to disagree about what the ticket says, and here they would disagree about
// money. Each host keeps its own caption, its own on/off checkbox and its own
// guard bounds — the parts that are genuinely different — and prints the other
// unit with `legEcho` in whatever slot it already had for it.

import { usdForTicks } from "../../lib/bracketUsd";

/** Dollars, the way every other money chip on the ticket writes them. */
const usd = (v: number): string => `$${Math.round(v).toLocaleString("en-US")}`;

/**
 * What this leg reads in the unit the box is *not* in — the caption every host
 * puts beside its label.
 *
 * Pinned, this is the distance the money came to, and it is the honest number:
 * a bracket lands on whole ticks, so $250 of a $5 tick at one lot is 50t and
 * $252 is 50t as well. Printing what was asked for rather than what was placed
 * is how a ticket comes to claim a figure it isn't carrying.
 */
export function legEcho(
  ticks: number,
  pin: number | null,
  tickUsd: number,
  size: number,
): string {
  if (pin != null) return ticks > 0 ? `${ticks}t · ${usd(usdForTicks(ticks, tickUsd, size))}` : "";
  const v = usdForTicks(ticks, tickUsd, size);
  return v > 0 ? usd(v) : "";
}

export function LegAmount({
  ticks,
  pin,
  tickUsd,
  size,
  onTicks,
  onPin,
  id,
  disabled,
  style,
  title,
}: {
  /** The distance the leg is **placed** at — already resolved through the pin
   *  by the page (`legTicks`), never the raw stored one. A box showing a stale
   *  distance beside a live one is the failure this whole seam exists to stop. */
  ticks: number;
  /** The dollar figure the leg is pinned to, or null for ticks. */
  pin: number | null;
  /** One tick of one contract, on the instrument the order will actually reach.
   *  0 where the page has no money to quote — then the box stays in ticks and
   *  the `$` is refused rather than converting against a guessed multiplier. */
  tickUsd: number;
  size: number;
  /** A tick distance was typed. **Hosts must clear the pin here** — typing a
   *  distance is the plainest possible statement that the distance is the
   *  setting, and a pin left standing would overwrite it on the next render. */
  onTicks: (t: number) => void;
  /** A dollar figure was typed, or the unit was switched. `null` unpins and
   *  leaves the distance where the pin had it. */
  onPin: (usd: number | null) => void;
  id?: string;
  disabled?: boolean;
  style?: React.CSSProperties;
  title?: string;
}) {
  const on = pin != null;
  const canPin = tickUsd > 0 && size > 0;
  return (
    <span className="leg-amt" style={style}>
      <input
        id={id}
        type="number"
        min={0}
        // A leg at zero is a leg that is off, and an empty box says that better
        // than a 0 does — which would read as a stop sitting on the fill.
        value={(on ? pin : ticks) || ""}
        placeholder="none"
        disabled={disabled}
        onChange={(e) => {
          const v = Math.max(0, Number(e.target.value) || 0);
          if (on) onPin(v);
          else onTicks(v);
        }}
        title={title}
      />
      <button
        type="button"
        className={`leg-amt-u${on ? " on" : ""}`}
        // Switching *to* dollars needs a rate; switching back never does, so a
        // pinned leg can always be unpinned even after the contract goes quiet.
        disabled={disabled || (!on && !canPin)}
        aria-pressed={on}
        onClick={() =>
          onPin(on ? null : Math.round(usdForTicks(ticks, tickUsd, size)))
        }
        title={
          on
            ? `Pinned to ${usd(pin)} — the distance follows the size and the ` +
              `contract. Press for ticks, and it stops at ${ticks}t.`
            : canPin
              ? `Ticks. Press to set this leg in dollars instead — ${
                  ticks > 0 ? `${usd(usdForTicks(ticks, tickUsd, size))} at this size` : "at this size"
                } — and the distance follows the size and the contract from then on.`
              : "Ticks — there is no tick value to price this leg in yet"
        }
      >
        {on ? "$" : "t"}
      </button>
    </span>
  );
}
