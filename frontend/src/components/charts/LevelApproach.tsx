// The levels near price — what they are, how far off they are, and whether each
// is armed to place its own order (lib/levelArm).
//
// How price is *arriving* at each is still read and still reported, but on the
// row's hover rather than in a column: arming made the row four columns wide and
// the name was what gave way, and a panel you arm from has to say what you are
// arming before it says anything else about it.
//
// The classification is `gapCloser` (lib/levelApproach), which is a port of the
// arithmetic the engine's drift-touch fade and the Interactions Lab both read —
// so a row reading `drift` here names the same kind of contact the adopted rule
// trades. What it is *not* is a signal: the fade also wants a time of day, a
// side and a stop, and none of that is in a row of this panel.
//
// One caveat worth knowing before it gets reported as a bug: this reads the
// chart's own levels, and the chart reconstructs a developing profile where the
// engine bins the true tape — `src/journal/sim/profile.py` says outright that
// the two "do not agree to the cent". So a row here can differ from the Lab's
// `closed_by` on the same touch. The arithmetic is pinned (tests/
// test_level_approach.py); the inputs are the same shape, not the same numbers.
//
// Presentational only: the rows arrive built, the distances are written straight
// into the DOM by the chart (see `paintLevelDist` in ReplayChart) because they
// move with the tape and React is not in that path.

import type { ApproachRow } from "../../lib/levelApproach";
import { armFor, armPurpose, type ArmShape, type ArmableLevel, type LevelArm } from "../../lib/levelArm";

/** The shapes a row offers, in the order they are read: the two ways in, then
 *  the way out. */
const SHAPES: ArmShape[] = ["limit", "through", "exit"];

/** What each class is called on a row, and the one-line reason behind it. The
 *  wording is the Python's, so the panel, the Lab column and the write-ups all
 *  say the same thing about the same event. */
const CLASS_LABEL: Record<ApproachRow["cls"], { text: string; title: string }> = {
  drift: {
    text: "drift",
    title:
      "Neither side closed the gap over the window — price was already loitering by this level and wiggled into it. The contact the drift-touch fade is built on.",
  },
  price: {
    text: "price-led",
    title: "Price did the closing: it travelled to this level. A momentum test.",
  },
  level: {
    text: "level-led",
    title:
      "The level did the closing — it came to price, which tested nothing. A falling band 'touching' is not a touch.",
  },
  both: { text: "both", title: "They met in the middle." },
  mixed: {
    text: "mixed",
    title:
      "The levels stacked here disagree about who closed the gap — typically a static reference and a moving one. Read the members below.",
  },
  unknown: {
    text: "—",
    title:
      "No window yet: these levels are younger than the lookback, so there is nothing to attribute.",
  },
};

/** What each arm shape does, said once so the row control and the standing list
 *  cannot describe it differently. */
const SHAPE_LABEL: Record<ArmShape, { text: string; title: string }> = {
  limit: {
    text: "bid",
    title:
      "Rest a passive order at this level now — a bid under the market, an offer over it. Filled by price coming to you.",
  },
  through: {
    text: "thru",
    title:
      "Wait for price to cross this level, then rest a stop a few ticks back on the side it came from. Filled only if price reclaims what it just gave up.",
  },
  exit: {
    text: "exit",
    title:
      "Wait for price to reach this level from either side, then take the position off at market. Sized to whatever you are holding; if you are flat when it fires, nothing is placed and the arm is spent.",
  },
};

export interface LevelApproachProps {
  rows: ApproachRow[];
  /** The standing arms. Rendered here rather than derived from `rows` because an
   *  arm outlives the row that made it: it is a frozen price, so it survives the
   *  cluster breaking up and price walking away from it, and it has to stay
   *  cancellable when it does. */
  arms?: LevelArm[];
  /** Arm a row with a shape, or `null` to disarm it. Omitted, the panel is
   *  read-only — which is what the journal's replayer and the Recall card want,
   *  since neither has a ticket to place anything with. */
  onArm?: (level: ArmableLevel, shape: ArmShape | null) => void;
  /** Whether the rows reach past the near window, and the toggle for it. */
  reach?: boolean;
  onReach?: () => void;
  /** Whether the first arm to fire cancels the rest, and the toggle for it.
   *  Within a purpose only — see `armPurpose`. */
  race?: boolean;
  onRace?: () => void;
  /** Handed up so the chart can write the live distances into these rows without
   *  going through React — the same split the crosshair's OHLC line makes. */
  containerRef?: React.Ref<HTMLDivElement>;
  /** What the window is, in this chart's own bars — "5 × 500t". The lookback was
   *  calibrated at the engine's bar sizes, so the reader is told what five bars
   *  currently means rather than being left to assume. */
  window: string;
  open: boolean;
  onToggle: () => void;
}

export function LevelApproach({
  rows,
  window,
  open,
  onToggle,
  containerRef,
  arms = [],
  onArm,
  reach = false,
  onReach,
  race = false,
  onRace,
}: LevelApproachProps) {
  // Nothing near price is not a state worth a box — and before the window fills
  // there is nothing to say either. An arm standing on a level that has drifted
  // out of range is the exception: that one still has to be reachable to cancel.
  if (!rows.length && !arms.length) return null;

  if (!open)
    return (
      <div className="chart-levels collapsed">
        <button
          type="button"
          className="chart-levels-pill"
          onClick={onToggle}
          title={
            arms.length
              ? `Show the levels near price (${rows.length}) — ${arms.length} armed`
              : `Show the levels near price (${rows.length})`
          }
        >
          ⌁ {rows.length}
          {/* An arm places an order without being asked again, so the count of
              standing ones is the thing that must survive the panel being shut. */}
          {arms.length > 0 && <span className="chart-levels-armed-n"> ⦿{arms.length}</span>}
        </button>
      </div>
    );

  return (
    <div className="chart-levels" ref={containerRef}>
      <div className="chart-levels-head">
        <span>levels · {window}</span>
        {onReach && (
          <button
            type="button"
            className={`chart-levels-reach${reach ? " on" : ""}`}
            onClick={onReach}
            title={
              reach
                ? "Back to the levels near price"
                : "Reach further out — the level worth arming is usually one price has not got to yet"
            }
          >
            ⤢
          </button>
        )}
        {/* The race. Only offered where arming is — a panel with no ticket has
            nothing to race — and it says how many arms it currently governs,
            because "first one wins" is a rule about a set and a set of one is
            the same as it being off. */}
        {onRace && onArm && (
          <button
            type="button"
            className={`chart-levels-race${race ? " on" : ""}`}
            onClick={onRace}
            title={
              race
                ? "First touched wins: when an arm fires, the other standing arms of the same kind are cancelled. Entries race entries and exits race exits, so a level-based bracket and a level-based entry can stand at once."
                : "Let every arm fire on its own. Turn on to race them: the first level touched wins and cancels the others of its kind."
            }
          >
            1st
          </button>
        )}
        <button type="button" onClick={onToggle} title="Hide the levels panel" aria-label="Hide the levels panel">
          ×
        </button>
      </div>
      {rows.map((r) => {
        const c = CLASS_LABEL[r.cls];
        // Keyed by what is *in* the row, not by its price: a developing level
        // nudging a tick would otherwise remount every row under it.
        const key = r.members.map((m) => m.label).join("|");
        return (
          <div className="chart-levels-row" key={key}>
            <span
              className="chart-levels-dist"
              data-price={r.price}
              title="Ticks from last price — updates with the tape."
            />
            {/* The approach class is on the hover, not in a column of its own.
                It had one until arming arrived and made the row four columns
                wide, at which point the name — the only column that is a word,
                and the one you need to know *what* you are about to arm — was
                what truncated. Nothing is lost: the class was already in this
                title, member by member, and the row's own reading joins it. */}
            <span
              className="chart-levels-name"
              title={`${r.members
                .map((m) => `${m.label} · ${CLASS_LABEL[m.cls].text}`)
                .join("\n")}\n\n${c.text} — ${c.title}`}
            >
              {r.members[0].label}
              {r.members.length > 1 && (
                <span className="chart-levels-n"> +{r.members.length - 1}</span>
              )}
            </span>
            {onArm && (
              <span className="chart-levels-arm">
                {SHAPES.map((s) => {
                  const on = armFor(arms, r.key)?.shape === s;
                  return (
                    <button
                      key={s}
                      type="button"
                      className={`chart-levels-armbtn${on ? " on" : ""}${
                        armPurpose(s) === "exit" ? " exit" : ""
                      }`}
                      // Clicking the lit shape disarms; clicking the other one
                      // swaps to it. There is no third click to learn.
                      onClick={() =>
                        onArm(
                          { key: r.key, label: r.members[0].label, family: r.members[0].family, price: r.price },
                          on ? null : s,
                        )
                      }
                      title={SHAPE_LABEL[s].title}
                    >
                      {SHAPE_LABEL[s].text}
                    </button>
                  );
                })}
              </span>
            )}
          </div>
        );
      })}
      {arms.length > 0 && (
        <div className="chart-levels-armed">
          {/* The price is printed because it is the arm's, not the level's: an
              arm freezes the price it was made at and never chases the level
              afterwards, so once a developing level has moved these two numbers
              are different and only this one is going to be traded. */}
          {arms.map((a) => (
            <div className="chart-levels-armedrow" key={a.key}>
              <span
                className={`chart-levels-armedmark${armPurpose(a.shape) === "exit" ? " exit" : ""}`}
                title={
                  armPurpose(a.shape) === "exit"
                    ? "Standing arm — this will take the position off by itself."
                    : "Standing arm — this will place an order by itself."
                }
              >
                ⦿
              </span>
              <span className="chart-levels-name" title={`Armed at ${a.price}`}>
                {a.label}
              </span>
              {/* `data-price` for the same reason the distance spans carry one:
                  the label can hold digits of its own ("−2σ", "9/20 EMA"), so
                  the number is not recoverable from the text. */}
              <span className="chart-levels-armedpx" data-price={a.price}>
                {a.price}
              </span>
              <span className="chart-levels-cls" title={SHAPE_LABEL[a.shape].title}>
                {SHAPE_LABEL[a.shape].text}
              </span>
              {onArm && (
                <button
                  type="button"
                  className="chart-levels-armbtn"
                  onClick={() => onArm({ key: a.key, label: a.label, family: "", price: a.price }, null)}
                  title="Disarm"
                  aria-label={`Disarm ${a.label}`}
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="chart-levels-foot" title="The class is settled on the last closed bar; the distances follow the tape.">
        as of last close
      </div>
    </div>
  );
}
