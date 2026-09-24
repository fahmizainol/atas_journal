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
//
// Which is why either can be the one you set. The money on a bracket knob is a
// button: press it and the leg is *pinned* to that figure, the distance becomes
// a reading of it, and it re-derives itself as the size and the routed contract
// move (lib/bracketUsd). The figure itself is a button too — press it and type,
// because stepping four ticks at a time is for nudging and no amount of nudging
// says "$250".

import { useRef, useState } from "react";

import { pinnedLeg, usdForTicks } from "../../lib/bracketUsd";
import {
  LEG_ROOM,
  ORDER_PRESETS,
  presetStop,
  stopForReading,
  type PresetBracket,
  type PresetId,
} from "../../lib/orderPresets";
import { TicketCard, type PresetCtx } from "./TicketCard";
import { type SizerCtx } from "./SizerGrid";
import { PRESET_BUCKETS } from "../../lib/volRuler";
import type { TicketDraft } from "./ReplayChart";

/**
 * The ticket card (bracket shapes and, where the page has one, the risk sizer),
 * as a single chip on the knob row.
 *
 * A chip rather than a button per shape in the row: it is 300px of floating
 * ticket already carrying five controls, and the thing worth seeing before you
 * commit is not the letter but the five distances behind it — which is what the
 * card inside shows. The panel beside the blotter renders that same card open,
 * since there it sits next to the fields it fills and has room to.
 *
 * **One chip, not two.** The sizer had its own Σ beside this one, and the two
 * popovers each drew their own bucketing toggle over the same ruler — so the row
 * carried two buttons, two panels and two copies of one control to ask what is
 * really one question. The Σ's reading now lives in this chip's own tooltip and
 * its grid under the presets.
 */
function TicketCardChip({
  preset,
  size,
  tickUsd,
  onApply,
  active,
  sizer,
  onApplySizing,
}: {
  preset: PresetCtx;
  size: number;
  tickUsd: number;
  onApply: (b: PresetBracket, id: PresetId) => void;
  active?: PresetBracket | null;
  sizer?: SizerCtx;
  onApplySizing?: ((a: { stopTicks: number; size: number; micro: boolean }) => void) | null;
}) {
  const [open, setOpen] = useState(false);
  const bar = PRESET_BUCKETS.find((b) => b.id === preset.bucket)!;
  const stop = presetStop(preset.read[preset.bucket]);
  return (
    <span className="sim-sizer-wrap">
      <button
        type="button"
        className={`sim-knob-rr preset${open ? " open" : ""}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={
          stop
            ? `Bracket${sizer ? " and size" : ""} — every preset off a ` +
              `${stopForReading(stop)}t stop: the ${bar.label} ruler reads ${stop}t, plus ` +
              `${LEG_ROOM} ticks of room${sizer ? `. The sizer inside sizes the ${stop}t itself` : ""}`
            : `Bracket presets — no stop to build one from yet: the ${bar.label} ` +
              `ruler reads from the 09:30 open onwards and has not closed three bars`
        }
      >
        {/* The stop it would *place*, not the number it read — that is what the
            chip is being glanced at for. The panel behind it shows both.

            The letters are the span rather than the roll-call they were at
            three: a fourth shape made `A·B·C·D` seven characters of a chip that
            also has to fit a stop, and a chip naming every preset is a chip
            that goes stale the next time one is added. Read off the list, so it
            cannot. */}
        {ORDER_PRESETS[0].id}–{ORDER_PRESETS[ORDER_PRESETS.length - 1].id}
        {stop ? ` ${stopForReading(stop)}t` : ""}
      </button>
      {open && (
        // `sim-sizer-pop` for the hit-testing reason the sizer spells out — the
        // dock only lifts its clip for a descendant matching that selector.
        <div className="sim-sizer-pop sim-preset-pop" role="dialog">
          <TicketCard
            preset={preset}
            size={size}
            tickUsd={tickUsd}
            active={active}
            sizer={sizer}
            onApplySizing={
              onApplySizing
                ? (a) => {
                    onApplySizing(a);
                    setOpen(false);
                  }
                : null
            }
            onApply={(b, id) => {
              onApply(b, id);
              setOpen(false);
            }}
            onClose={() => setOpen(false)}
          />
        </div>
      )}
    </span>
  );
}

/** One tick, in dollars, for the contract the orders are *routed* to. Zero (no
 *  session, or an unknown contract) and the knobs quote ticks alone rather than
 *  inventing money. */
function money(ticks: number, tickUsd: number, sign: 1 | -1): string | null {
  if (!(tickUsd > 0) || ticks <= 0) return null;
  return dollars(ticks * tickUsd, sign);
}

/** The same figure, when it is already dollars — a pinned leg. */
const dollars = (v: number, sign: 1 | -1): string =>
  `${sign < 0 ? "−" : "+"}$${Math.round(v).toLocaleString()}`;

/**
 * One knob: − value +, where the value can also be typed.
 *
 * Stepping is for nudging and typing is for deciding, and the row needs both —
 * four ticks a click is fine for widening a stop by a bar's worth, and useless
 * for saying "$250". The figure is a button that turns into a box; committing on
 * blur and Enter, abandoning on Escape, so a half-typed number never reaches a
 * ticket.
 *
 * `value` is in whatever unit the knob is currently in, and so is `step` — a
 * dollar-pinned leg steps in dollars. The caller does that conversion because it
 * is the one that knows what the leg is pinned to; this only ever adds `step` to
 * `value`.
 */
function Knob({
  className,
  label,
  value,
  secondary,
  step,
  min,
  onChange,
  title,
  /** The unit this knob is in, and the switch. Absent on the size knob, which is
   *  contracts and nothing else. */
  unit,
}: {
  className: string;
  label: string;
  value: number;
  /** The same leg in the other unit, on the button that switches to it. */
  secondary: string | null;
  step: number;
  min: number;
  onChange: (v: number) => void;
  title: string;
  unit?: { pinned: boolean; canPin: boolean; onToggle: () => void; title: string };
}) {
  const [editing, setEditing] = useState(false);
  // Escape has to beat the blur that follows it, or abandoning would commit.
  const keep = useRef(true);
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
      {editing ? (
        <input
          className="sim-knob-in"
          type="number"
          min={min}
          autoFocus
          defaultValue={value || ""}
          onFocus={(e) => e.currentTarget.select()}
          onBlur={(e) => {
            if (keep.current) onChange(Math.max(min, Number(e.currentTarget.value) || 0));
            keep.current = true;
            setEditing(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            else if (e.key === "Escape") {
              keep.current = false;
              e.currentTarget.blur();
            }
          }}
        />
      ) : (
        <span className="sim-knob-v">
          <button
            type="button"
            className="sim-knob-t"
            onClick={() => setEditing(true)}
            title="Type a figure"
          >
            {label}
          </button>
          {/* The unit rides on the figure in the other unit rather than sitting
              beside the knob: this row is 300px and already carries five
              controls, and the number you press is the one whose unit changes. */}
          {unit ? (
            <button
              type="button"
              className={`sim-knob-u${unit.pinned ? " on" : ""}`}
              disabled={!unit.pinned && !unit.canPin}
              aria-pressed={unit.pinned}
              onClick={unit.onToggle}
              title={unit.title}
            >
              {secondary ?? (unit.pinned ? "$" : "t")}
            </button>
          ) : (
            secondary && <b>{secondary}</b>
          )}
        </span>
      )}
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
  sizer,
  onApplySizing,
  preset,
  onApplyPreset,
  activeBracket,
  reverse,
}: {
  ticket: TicketDraft;
  onChange: (t: TicketDraft) => void;
  tickUsd: number;
  bracketStep?: number;
  /** Send the opposite side of the market button pressed — the page's setting,
   *  drawn here because this row is the only ticket there is under ~1100px.
   *
   *  `applying` is not the same question as `on`: the flip is an *opening* rule
   *  and stands down the moment size is on, so a chip that only knew the setting
   *  would sit there lit over two buttons that had gone back to meaning what
   *  they say. Absent on a surface with no market buttons to reverse, and then
   *  the chip never appears. */
  reverse?: { on: boolean; applying: boolean; onToggle: () => void };
  /** The account's limits and the contract, for the risk sizer. Its ruler comes
   *  off `preset` — the same reading the bracket shapes are built from — so both
   *  are needed for the size half to appear, and on a page with neither the card
   *  is the bracket list alone rather than opening onto a shrug. */
  sizer?: SizerCtx;
  /** A preset cell was clicked. The page applies it, because the mini/micro half
   *  of the answer is the page's switch and it refuses while a position is on. */
  onApplySizing?: (a: { stopTicks: number; size: number; micro: boolean }) => void;
  /** The presets' ruler and the bucketing it is read at — the stop every one of
   *  the brackets is built from, and the toggle that chooses it. Absent on a page
   *  with no ruler, and then the A–D chip never appears. */
  preset?: PresetCtx;
  /** A bracket preset was chosen. The page applies it for the same reason the
   *  sizer's cells go back to it: the trail knobs are not on this ticket. They
   *  are the page's — three fields on the replay's, a whole `trailSource` on
   *  Live's — and a component that reached into either would be describing two
   *  order paths it cannot see. */
  onApplyPreset?: (b: PresetBracket, id: PresetId) => void;
  /** The five distances the page is carrying now, so the card can light the
   *  preset that equals them. From the page rather than from `ticket`, because
   *  the trail legs are the page's and a bracket missing three of its six
   *  numbers would match the wrong row. */
  activeBracket?: PresetBracket | null;
}) {
  const { size, stopTicks, targetTicks } = ticket;
  const risk = stopTicks * size * tickUsd;
  const reward = targetTicks * size * tickUsd;
  const canPin = tickUsd > 0 && size > 0;
  /**
   * A bracket leg's knob, in whichever unit the leg is pinned to.
   *
   * Everything the knob does — stepping, typing, switching unit — comes back
   * through one setter, because they are one edit: a leg is a distance *or* a
   * figure, and the ticket must never be caught holding both answers. Pinned,
   * the distance is re-derived here (`pinnedLeg`) rather than left to the page,
   * so what the knob shows is what an order placed in the same breath carries.
   */
  const leg = (key: "stop" | "target", glyph: string, sign: 1 | -1) => {
    const ticks = key === "stop" ? stopTicks : targetTicks;
    const pin = key === "stop" ? ticket.stopUsd : ticket.targetUsd;
    const set = (next: { usd: number | null; ticks: number }) =>
      onChange({ ...ticket, [`${key}Ticks`]: next.ticks, [`${key}Usd`]: next.usd });
    const cost = money(ticks * size, tickUsd, sign);
    return (
      <Knob
        className={key}
        label={pin != null ? `${glyph} ${dollars(pin, sign)}` : `${glyph} ${ticks}t`}
        value={pin != null ? pin : ticks}
        secondary={pin != null ? (ticks > 0 ? `${ticks}t` : null) : cost}
        // A pinned leg steps by what the same four ticks are worth, so a nudge
        // moves the bracket the same distance whichever unit you are thinking in.
        step={
          pin != null ? Math.max(1, Math.round(usdForTicks(bracketStep, tickUsd, size))) : bracketStep
        }
        min={0}
        onChange={(v) =>
          set(pin != null ? pinnedLeg(v, ticks, tickUsd, size) : { usd: null, ticks: v })
        }
        unit={{
          pinned: pin != null,
          canPin,
          onToggle: () =>
            set(
              pin != null
                ? { usd: null, ticks }
                : pinnedLeg(Math.round(usdForTicks(ticks, tickUsd, size)), ticks, tickUsd, size),
            ),
          title:
            pin != null
              ? `Pinned to ${cost ?? "this figure"} — the distance follows the size and the contract. Press for ticks and it stops at ${ticks}t.`
              : canPin
                ? "Ticks. Press to set this leg in dollars instead, and the distance follows the size and the contract from then on."
                : "Ticks — there is no tick value to price this leg in yet",
        }}
        title={`${key === "stop" ? "Stop" : "Target"} distance — 0 turns the leg off. The figure is typable; the button beside it switches ticks and dollars.`}
      />
    );
  };
  return (
    <div className="sim-quick-knobs">
      <Knob
        className="size"
        label={`×${size}`}
        value={size}
        secondary={null}
        step={1}
        min={1}
        onChange={(v) => onChange({ ...ticket, size: Math.max(1, v) })}
        title="Contracts — every order this ticket sends"
      />
      {/* Zero is a real value on both legs: the leg is off, and the trade is
          managed by hand or by a level dragged on afterwards. */}
      {leg("stop", "⊥", -1)}
      {leg("target", "⊤", 1)}
      {/* The ratio the two knobs above add up to. Its own chip because it is the
          one number here nobody sets and everybody checks. */}
      {risk > 0 && reward > 0 && (
        <span className="sim-knob-rr" title={`Risking ${Math.round(risk)} to make ${Math.round(reward)}`}>
          {(reward / risk).toFixed(1)}R
        </span>
      )}
      {/* Reverse. A chip rather than a checkbox because the row is 300px and
          every other control on it is one, and it earns the width by being the
          one setting here that changes what a *button* does rather than what a
          number is. Lit on, struck through while it stands down — a position is
          on and the next click is an add or an exit, neither of which flips. */}
      {reverse && (
        <button
          type="button"
          className={`sim-knob-rr rev${reverse.on ? " on" : ""}${
            reverse.on && !reverse.applying ? " idle" : ""
          }`}
          aria-pressed={reverse.on}
          onClick={reverse.onToggle}
          title={
            !reverse.on
              ? "Reverse off — the market buttons send the side they name. Press to trade the model backwards."
              : reverse.applying
                ? "Reverse ON — the market buttons are swapped, so the one you press sends the other side. Opening only: once size is on, they mean what they say again."
                : "Reverse on, but standing down — you have a position, and adds and exits are never flipped. It applies again from flat."
          }
        >
          ⇄{reverse.on ? " rev" : ""}
        </button>
      )}
      {/* The bracket's shape and the bracket's size, behind one chip: one says
          how far, the other says how many, and they are not two questions asked
          in the same breath so much as one question asked twice. */}
      {onApplyPreset && preset && (
        <TicketCardChip
          preset={preset}
          size={size}
          tickUsd={tickUsd}
          onApply={onApplyPreset}
          active={activeBracket}
          sizer={sizer}
          onApplySizing={onApplySizing}
        />
      )}
    </div>
  );
}
