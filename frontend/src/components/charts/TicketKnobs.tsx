// Size and the bracket, on the floating ticket itself.
//
// WHY HERE AND NOT ONLY IN THE PANEL. The market buttons are the one thing on
// this page you can least afford to go looking for, so they float over the tape
// in their own small window — but the numbers they send were only editable in a
// side panel you had to summon. That is a ticket split across two surfaces, and
// the half you can see is the half that fires. Below ~1100px the panel doesn't
// even get a column, and then the floating ticket is the *only* order entry
// there is, which is what makes this not a convenience.
//
// Every knob quotes money as well as ticks, because ticks are not the decision.
// "50t" is a number; "−$250" is the thing you are agreeing to lose, and it is
// what the discipline layer refuses on.

import type { TicketDraft } from "./ReplayChart";

/** One tick, in dollars, for the contract the orders are *routed* to. Zero (no
 *  session, or an unknown contract) and the knobs quote ticks alone rather than
 *  inventing money. */
function money(ticks: number, tickUsd: number, sign: 1 | -1): string | null {
  if (!(tickUsd > 0) || ticks <= 0) return null;
  const v = ticks * tickUsd;
  return `${sign < 0 ? "−" : "+"}$${Math.round(v).toLocaleString()}`;
}

function Knob({
  className,
  label,
  value,
  money: usd,
  step,
  min,
  onChange,
  title,
}: {
  className: string;
  label: string;
  value: number;
  money: string | null;
  step: number;
  min: number;
  onChange: (v: number) => void;
  title: string;
}) {
  return (
    <div className={`sim-knob ${className}`} title={title}>
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - step))}
        aria-label={`${label} down`}
        disabled={value <= min}
      >
        −
      </button>
      <span className="sim-knob-v">
        {label}
        {usd && <b>{usd}</b>}
      </span>
      <button type="button" onClick={() => onChange(value + step)} aria-label={`${label} up`}>
        +
      </button>
    </div>
  );
}

export function TicketKnobs({
  ticket,
  onChange,
  tickUsd,
  /** How many ticks a bracket step moves. Four is a point on NQ — a tick at a
   *  time is thirty clicks to a sensible stop. */
  bracketStep = 4,
}: {
  ticket: TicketDraft;
  onChange: (t: TicketDraft) => void;
  tickUsd: number;
  bracketStep?: number;
}) {
  const { size, stopTicks, targetTicks } = ticket;
  const risk = stopTicks * size * tickUsd;
  const reward = targetTicks * size * tickUsd;
  return (
    <div className="sim-quick-knobs">
      <Knob
        className="size"
        label={`×${size}`}
        value={size}
        money={null}
        step={1}
        min={1}
        onChange={(v) => onChange({ ...ticket, size: v })}
        title="Contracts — every order this ticket sends"
      />
      {/* Zero is a real value on both legs: the leg is off, and the trade is
          managed by hand or by a level dragged on afterwards. */}
      <Knob
        className="stop"
        label={`⊥ ${stopTicks}t`}
        value={stopTicks}
        money={money(stopTicks * size, tickUsd, -1)}
        step={bracketStep}
        min={0}
        onChange={(v) => onChange({ ...ticket, stopTicks: v })}
        title="Stop distance — 0 turns the leg off"
      />
      <Knob
        className="target"
        label={`⊤ ${targetTicks}t`}
        value={targetTicks}
        money={money(targetTicks * size, tickUsd, 1)}
        step={bracketStep}
        min={0}
        onChange={(v) => onChange({ ...ticket, targetTicks: v })}
        title="Target distance — 0 turns the leg off"
      />
      {/* The ratio the two knobs above add up to. Its own chip because it is the
          one number here nobody sets and everybody checks. */}
      {risk > 0 && reward > 0 && (
        <span className="sim-knob-rr" title={`Risking ${Math.round(risk)} to make ${Math.round(reward)}`}>
          {(reward / risk).toFixed(1)}R
        </span>
      )}
    </div>
  );
}
