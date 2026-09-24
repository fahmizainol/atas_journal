// The ticket card: what shape the bracket is, and how many contracts to send.
//
// Its own component because the same two questions get asked in more than one
// place and they must not answer them differently: the chip on the floating
// ticket (the only order entry there is under ~1100px), the ticket in the panel
// beside the blotter, the blotter card itself, and on Live the setup drawer and
// the routing panel's order pad. A second copy typed into a panel is how one of
// them ends up a preset behind the other.
//
// **One card, not two.** The bracket list and the Σ sizer were separate panels
// behind separate chips, and both drew their own copy of the bucketing toggle
// over the same ruler. Two controls bound to one setting is a way to be shown
// two answers and believe them; worse, the two halves are one decision — how far
// the stop goes and how many contracts sit behind it are the same sentence about
// the same money, and asking them from different popovers is what let a 62-tick
// stop get sized as if it were 40. The reading is read once, at the top, with
// where it came from in the tooltip, and everything below is derived from it.
//
// Presentational and stateless on purpose. It reads a ruler reading and calls
// back with a bracket or a size — where the reading comes from, and what
// applying one means, both belong to the page (`applyPreset`/`applySizing` in
// Simulator and LiveChart), because the trail fields are the page's and on Live
// they decide which of two trail engines is live. The bucketing toggle is the
// same deal: this draws it and reports the click, the page owns the setting.

import {
  LEG_ROOM,
  ORDER_PRESETS,
  presetStop,
  sameBracket,
  stopForReading,
  type PresetBracket,
  type PresetId,
} from "../../lib/orderPresets";
import { SizerGrid, type SizerCtx } from "./SizerGrid";
import { PRESET_BUCKETS, type PresetBucket, type PresetRulerRead } from "../../lib/volRuler";

/**
 * The presets' ruler, and which of its bucketings is being read.
 *
 * One object rather than three props for the same reason `SizerCtx` is one: the
 * reading and the selection are meaningless apart — a page holding one without
 * the other can only render a number with no idea what it measured — and this
 * has to be threaded through `TicketKnobs` and `RoutingPanel` to reach both
 * surfaces.
 */
export interface PresetCtx {
  /** Every bucketing's reading in ticks (lib/volRuler `PresetRuler`). */
  read: PresetRulerRead;
  /** Which one the stop is taken from. */
  bucket: PresetBucket;
  /** The toggle was clicked. A stored setting, so it goes back to the page. */
  onBucket: (b: PresetBucket) => void;
}

/** Where the stop came from, for the header's tooltip — the bar being measured
 *  and the window it is measured in. Spelled out rather than merely quoted:
 *  "where did 42t come from" has to be answerable without opening the source,
 *  and with a toggle in the panel the bar is now something you can have changed
 *  and forgotten. */
export const rulerWhy = (bucket: PresetBucket): string => {
  const b = PRESET_BUCKETS.find((x) => x.id === bucket)!;
  return `The median ${b.label} bar range so far today, from the 09:30 open onwards — ${b.says}`;
};

/** What one preset would cost you to be wrong about, at the ticket's size. Null
 *  where the page has no money to quote (no session, unknown contract) — then
 *  the header says ticks alone rather than inventing a figure. */
function money(ticks: number, tickUsd: number): string | null {
  if (!(tickUsd > 0) || ticks <= 0) return null;
  return `−$${Math.round(ticks * tickUsd).toLocaleString()}`;
}

/**
 * The bucketing toggle: three buttons, one selected.
 *
 * Every option carries **its own reading**, not just its name, and that is the
 * point of the control rather than a decoration on it. The three are genuinely
 * different measurements of the same tape — a volume clock flattens the open, a
 * wall clock reports it — so the question "which bar" is really "which of these
 * numbers", and a toggle that made you click through to find out would be
 * asking you to guess. A bucketing with nothing to say yet still shows, greyed,
 * because its absence is also an answer.
 *
 * Exported because it is the one control in this card that a surface might want
 * without the rest of it.
 */
export function BucketToggle({ read, bucket, onBucket }: PresetCtx) {
  return (
    <div className="sim-preset-buckets" role="group" aria-label="Ruler bar">
      {PRESET_BUCKETS.map((b) => {
        const r = presetStop(read[b.id]);
        return (
          <button
            key={b.id}
            type="button"
            className={`sim-preset-bucket${b.id === bucket ? " on" : ""}`}
            aria-pressed={b.id === bucket}
            onClick={() => onBucket(b.id)}
            title={`${b.says}${r ? ` — reads ${r}t` : " — nothing to read yet"}`}
          >
            {b.label}
            <i>{r ? `${r}t` : "—"}</i>
          </button>
        );
      })}
    </div>
  );
}

export function TicketCard({
  preset,
  size,
  tickUsd,
  onApply,
  active,
  sizer,
  onApplySizing,
  onClose,
}: {
  preset: PresetCtx;
  /** Contracts on the ticket, for pricing the stop in the header and for lighting
   *  the size cell the ticket is on. */
  size: number;
  tickUsd: number;
  onApply: (b: PresetBracket, id: PresetId) => void;
  /** The five distances the ticket is carrying **right now** — the page's, since
   *  the trail legs are the page's. Whichever preset equals it is lit.
   *
   *  Derived rather than remembered on purpose: a stored letter goes on claiming
   *  a shape after a knob has been nudged past it, and the row is lit to answer
   *  "what am I about to send", not "what did I last press". Null where the page
   *  has no bracket to compare (Live with a trail engine other than the ladder,
   *  say), and then nothing is lit, which is also the truth. */
  active?: PresetBracket | null;
  /** The account's limits and the contract, for the size half. Absent and the
   *  card is the bracket list alone — which is the order pad, where the size
   *  lives in routing rather than on the ticket. */
  sizer?: SizerCtx;
  onApplySizing?: ((a: { stopTicks: number; size: number; micro: boolean }) => void) | null;
  /** Rendered as a ✕ in the header. For the popover; the panel and the blotter
   *  card have nothing to close. */
  onClose?: () => void;
}) {
  const stop = presetStop(preset.read[preset.bucket]);
  // What the ticket will carry, which is the reading plus the room — the two
  // are different numbers now and the header shows both. Quoting only the
  // reading would put a 45 above four rows that all say 50.
  const placed = stop && stopForReading(stop);
  const risk = placed && money(placed * size, tickUsd);
  return (
    <>
      <div className="sim-sizer-head">
        <span>{sizer ? "Bracket & size" : "Bracket presets"}</span>
        {stop && placed && (
          <b title={`${rulerWhy(preset.bucket)}, plus ${LEG_ROOM} ticks of room on every leg`}>
            ⊥ {placed}t <i>{stop}+{LEG_ROOM}</i>
            {risk && <i> {risk}</i>}
          </b>
        )}
        {onClose && (
          <button type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        )}
      </div>
      {/* Above everything, because it changes every number under it — both the
          distances and the contracts. */}
      <BucketToggle {...preset} />
      {!stop ? (
        <p className="sim-sizer-empty">
          This ruler has nothing to say yet — it reads closed bars from the 09:30 open onwards, and
          there is nothing behind it: before the first three have printed, the bracket and the size
          are yours to set.
        </p>
      ) : (
        <>
          {/* Two up. Four shapes stacked full-width was a list you scrolled in a
              300px popover; two columns puts the whole set in one glance, which
              is what a set of alternatives is for. The sentence each one used to
              carry moved into the row's tooltip — at this width it wrapped to
              three lines and pushed the distances, the part actually being
              compared, off the bottom. */}
          <div className="sim-preset-grid">
            {ORDER_PRESETS.map((p) => {
              const b = p.bracket(stop);
              const on = sameBracket(active ?? null, b);
              return (
                <button
                  key={p.id}
                  type="button"
                  className={`sim-preset-opt${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() => onApply(b, p.id)}
                  title={`${p.name} — ${p.says}${on ? " · this is what the ticket is carrying" : ""}`}
                >
                  <b>{p.id}</b>
                  <span>{p.name}</span>
                  {/* The whole shape, spelled out from the one reading: nothing
                      here should have to be pressed to be understood. */}
                  <u>
                    ⊥{b.stopTicks} {b.targetTicks > 0 ? `⊤${b.targetTicks}` : "no ⊤"}
                    {b.trailTicks > 0 &&
                      ` · ${b.trailBeOnly ? "BE" : "trail"} ${b.trailTicks}/${b.trailStepTicks}/${b.trailBeTicks}`}
                  </u>
                </button>
              );
            })}
          </div>
          {sizer && (
            <>
              {/* Ruled off and named, because it is a different question with the
                  same answer behind it: the rows above spend the reading on
                  distance, the grid below spends it on contracts. */}
              <div className="sim-card-sep">
                <span>Size for risk</span>
              </div>
              <SizerGrid
                ctx={sizer}
                volTicks={stop}
                size={size}
                onApply={onApplySizing ?? null}
              />
            </>
          )}
        </>
      )}
    </>
  );
}
