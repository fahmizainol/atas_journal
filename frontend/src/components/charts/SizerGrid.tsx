// Size for risk: the grid half of the ticket card.
//
// Headless on purpose — no heading, no ruler toggle, no empty state. It used to
// be a whole panel with all three, and then it sat behind its own Σ chip next to
// the bracket chip, each rendering its own copy of the same bucketing control
// over the same reading. Two controls bound to one setting is one too many, and
// the card (`TicketCard`) now owns the reading, says where it came from once,
// and hands it down here. What is left is the part only this file knows: what a
// stop is worth in contracts, at three appetites, on both the mini and its
// micro.
//
// **The reading is the presets' ruler**, at whichever bucketing the card has
// selected — the same number A–D builds its stop from. It used to be the vol
// ruler's ATR at the *drawn* timeframe, and that was wrong in the way
// `PresetRuler` spells out: a 500-tick chart and a 5-minute chart of the same
// tape hand you stops 4× apart, so the Σ answered a different question on every
// pane and disagreed with the bracket rows sitting above it.
//
// What it does *not* take on is `LEG_ROOM`. A preset places the reading plus 5
// ticks; this sizes the reading itself, so its dollars sit a leg-room short of
// what an A–D bracket really risks (5t × size — $25 on 1 NQ). Deliberate: the
// sizer answers "what is this tape worth in contracts", and the cushion is a
// property of the bracket shape rather than of the tape.

import {
  presetsFor,
  routeLabel,
  stopThatFits,
  type Route,
  type SizerInput,
} from "../../lib/riskSizer";

/** What the sizer needs that the ticket cannot tell it: the account's limits and
 *  which contract the page is pointed at. The ruler is not in here — it arrives
 *  from the card, which reads it once for both halves, so there is no way for a
 *  page to point the two halves of one ticket at different measurements. */
export interface SizerCtx extends Omit<SizerInput, "volTicks"> {
  /** The mini's root and its micro's, for the labels ("1 NQ", "7 MNQ"). */
  root: string;
  microRoot: string | null;
  /** Whether the ticket is currently pointed at the micro. */
  micro: boolean;
  /** May clicking a cell move the ticket to the other contract? False on
   *  `/live`, where the routed instrument belongs to routing rather than to the
   *  ticket, and in a replay holding a position — the mini and its micro do not
   *  net. The cells that would need the switch go dead rather than half-applying,
   *  which is the one outcome worse than doing nothing. */
  canSwitchContract: boolean;
}

/** A row's two routes, as buttons. Clicking one is a decision about the
 *  contract as much as the size, which is why it goes back to the page rather
 *  than into the ticket here — the mini/micro switch is the page's to throw and
 *  it refuses while there is skin in the game. */
function RouteBtn({
  route,
  label,
  current,
  onPick,
}: {
  route: Route | null;
  label: string | null;
  /** This exact size, on the contract the ticket is pointed at — see `SizerGrid`. */
  current: boolean;
  onPick: (() => void) | null;
}) {
  if (!route || !label)
    return (
      <span className="sim-sizer-cell none" title="Not one contract fits this budget">
        —
      </span>
    );
  return (
    <button
      type="button"
      className={`sim-sizer-cell${current ? " current" : ""}`}
      disabled={!onPick}
      onClick={onPick ?? undefined}
      title={
        `${label} — risks $${Math.round(route.riskUsd).toLocaleString()} if the stop is hit, ` +
        `fees $${route.feesUsd.toFixed(2)} round turn. ` +
        `${route.losersToDeath} of these ends the account.` +
        (current ? " This is what the ticket is set to." : "")
      }
      aria-pressed={current}
    >
      <b>{label}</b>
      <i>${Math.round(route.riskUsd).toLocaleString()}</i>
      <u>{route.losersToDeath}</u>
    </button>
  );
}

function Row({
  r,
  ctx,
  onApply,
  stopTicks,
  size,
}: {
  r: ReturnType<typeof presetsFor>[number];
  ctx: SizerCtx;
  onApply: ((a: { stopTicks: number; size: number; micro: boolean }) => void) | null;
  stopTicks: number;
  size: number;
}) {
  const canMicro = ctx.microRoot != null;
  /** A cell may be taken when the page can apply at all, and either it is the
   *  contract already on the ticket or the ticket is free to move to it. */
  const pickable = (wantMicro: boolean) =>
    !!onApply && (wantMicro === ctx.micro || ctx.canSwitchContract);
  /** The cell the ticket is *on*: this contract and this many of them. It used
   *  to be the whole column — "you are on the micro" — which lit three cells at
   *  once and so could never answer the question actually being asked, which is
   *  which of these nine numbers is the one in the size knob. A hand-typed size
   *  matching no cell lights nothing, and that is the honest answer. */
  const on = (wantMicro: boolean, n: number | undefined) =>
    wantMicro === ctx.micro && n === size;
  return (
    <>
      {/* The budget is on the row rather than in a tooltip, because it is now the
          whole of what the row is. It used to read "4×" — how many losers the
          day limit would absorb — over a budget computed from that, and the two
          did not reconcile once a cushion came off ($1,200 ÷ 4 is $300; the row
          spent $250). There is nothing left to reconcile: this is the money. */}
      <span
        className="sim-sizer-lbl"
        title={`$${Math.round(r.budgetUsd).toLocaleString()} of risk on this trade`}
      >
        {r.appetite}
        <i>${Math.round(r.budgetUsd).toLocaleString()}</i>
      </span>
      <RouteBtn
        route={r.mini}
        label={routeLabel(r.mini, ctx.root, ctx.microRoot ?? "")}
        current={on(false, r.mini?.minis)}
        onPick={
          r.mini && pickable(false)
            ? () => onApply!({ stopTicks, size: r.mini!.minis, micro: false })
            : null
        }
      />
      <RouteBtn
        route={canMicro ? r.micro : null}
        label={canMicro ? routeLabel(r.micro, ctx.root, ctx.microRoot ?? "") : null}
        current={canMicro && on(true, r.micro?.micros)}
        onPick={
          canMicro && r.micro && pickable(true)
            ? () => onApply!({ stopTicks, size: r.micro!.micros, micro: true })
            : null
        }
      />
    </>
  );
}

/**
 * The three appetites for a reading, on both contracts. Not a rule and not a
 * refusal — nothing here moves the ticket until a cell is clicked.
 *
 * `volTicks` is the ruler's reading in whole ticks, already known to be usable:
 * the card does not render this half at all until it has one, so there is no
 * "not yet" state in here to keep in step with the presets' one.
 */
export function SizerGrid({
  ctx,
  volTicks,
  size,
  onApply,
}: {
  ctx: SizerCtx;
  volTicks: number;
  /** Contracts on the ticket now, so the cell it came from can be lit. */
  size: number;
  onApply: ((a: { stopTicks: number; size: number; micro: boolean }) => void) | null;
}) {
  const { root, microRoot } = ctx;
  const rows = presetsFor({ ...ctx, volTicks });
  const stopTicks = rows[0].stopTicks;
  const over = rows[0].overStopCeiling;
  return (
    <>
      <div className="sim-sizer-grid">
        <span className="sim-sizer-hd" />
        <span className={`sim-sizer-hd${ctx.micro ? "" : " on"}`}>{root}</span>
        <span className={`sim-sizer-hd${ctx.micro ? " on" : ""}`}>{microRoot ?? "micro"}</span>
        {rows.map((r) => (
          <Row key={r.appetite} r={r} ctx={ctx} onApply={onApply} stopTicks={stopTicks} size={size} />
        ))}
      </div>
      {over && (
        <p className="sim-sizer-note warn">
          The ruler is wider than the {ctx.stopTicksMax}t stop ceiling — the guards will refuse
          this bracket. Sizing shown anyway; the reading is the reading.
        </p>
      )}
      {/* The inverse, and the only thing worth saying under the grid: when the
          minis have dropped out, this is what would let one back in. The budget
          is not repeated — the row label carries it, and every cell carries what
          it actually spends. */}
      <p className="sim-sizer-note">
        One {root} fits the moderate budget at ⊥{" "}
        {stopThatFits(rows[1].budgetUsd, 1, 0, ctx.tickUsd, ctx.commissionPerSide)}t or tighter.
      </p>
    </>
  );
}
