// What one trade owes its review: the level it was taken off, a setup, a
// discipline call — and optionally tags and a note. Shared by the replay
// review, the drill review and the recall back's editor, which ask exactly the
// same things.
//
// Design: docs/trade-grading-plan.md, and the 2026-08-31 revision below.
//
// **The grade is not here.** It was, until the 132 reviews written this way
// showed what a grade picked beside the P&L must show: every A and B won,
// every D lost — the letter was the outcome restated. The grade is now
// answered at the recall front, at the fill-freeze, before the back is
// fetched; this card only ever *displays* one. What replaced it in the gate
// are the two axes the free tags were carrying by accident: what the trade
// was (faded-vs-joined first), and whether the plan was followed.
//
// **The context chips answer nothing.** They are the tape's own description of
// the moment — chop, drive, range location, the vol ruler — served read-only
// so "Chopping" never has to be typed again. They describe and never gate:
// chop KPIs failed as gates (market-structure study), and a chip that turned
// red-means-don't would be that failed gate wearing a label.
//
// **The level picker offers candidates and does not answer.** `trade_levels`
// measured every level this fill landed near; the options are ordered by
// distance rather than by the tagger's rank, because rank is the machine's
// opinion about how unusual a distance was and putting it first would be the
// measurement filling in the human's side. Nothing is pre-selected, and "no
// level" is always offered — a trade whose day was never cached still has an
// answer, so the gate can only block on a missing answer. The same
// nothing-pre-selected rule holds for the two axes: "clean" is one tap, and a
// discipline call that defaulted on would be manufactured compliance data.
//
// The pick names *levels* ("GX POC"), not families, and it is a set — see the
// git history of this header for how it got here. "no level" stays exclusive:
// it is the only option that contradicts the rest.

import { useState } from "react";
import { TagInput } from "./TagInput";
import type { ContextChip, DrillTradeRow, LevelCandidate } from "../../hooks/useReplays";
import { palette } from "../../theme";

export interface ReviewVocab {
  grades: { id: string; says: string }[];
  setups: { id: string; says: string }[];
  disciplines: { id: string; says: string }[];
  no_level: string;
}

/** The answers, as the panels hold them mid-edit. No grade — see the header. */
export interface ReviewAnswers {
  setup: string | null;
  discipline: string | null;
  watchedLevels: string[];
  tags: string[];
  note: string;
}

export const seedAnswers = (row: DrillTradeRow): ReviewAnswers => ({
  setup: row.setup ?? null,
  discipline: row.discipline ?? null,
  // Defaulted, not trusted: journal rows reach this from the panel fetch, the
  // gate fetch (which sends no candidates) and any stub in between, and a card
  // that threw on an absent array would take the whole page down with it.
  watchedLevels: row.watched_levels ?? [],
  tags: row.tags ?? [],
  note: row.note ?? "",
});

/** Mirrors `journal.review.trade_answered` — the server gates on the same three,
 *  and a panel that disagreed with it would be a File button that stays dark for
 *  no visible reason. The note and the tags are deliberately absent: prose is
 *  optional, and "≥1 tag" of an open vocabulary was a gate people satisfied
 *  with the literal tag "None". */
export const answered = (a: ReviewAnswers): boolean =>
  a.watchedLevels.some((l) => l.trim()) &&
  !!a.setup?.trim() &&
  !!a.discipline?.trim();

/** Set equality, not array equality: both fields it compares are picked by
 *  clicking, so the order they came out in is not something a Save button
 *  should light up over. */
const sameSet = (a: string[], b: string[]): boolean =>
  a.length === b.length && a.every((x) => b.includes(x));

export const answersDirty = (a: ReviewAnswers, row: DrillTradeRow): boolean =>
  a.setup !== (row.setup ?? null) ||
  a.discipline !== (row.discipline ?? null) ||
  a.note !== (row.note ?? "") ||
  !sameSet(a.watchedLevels, row.watched_levels ?? []) ||
  !sameSet(a.tags, row.tags ?? []);

/** How many level chips show before the fold. */
const SHOWN_LEVELS = 8;

export const GRADE_COLOR: Record<string, string> = {
  A: palette.green,
  B: palette.text,
  C: palette.orange,
  D: palette.red,
};

/** Short labels for the axis ids, shared by every surface that displays a
 *  stored answer ("faded_rally" is an id, not a thing to print). The ids and
 *  the says-strings stay served (`/review/vocab`); these are display only. */
export const SETUP_LABEL: Record<string, string> = {
  faded_rally: "faded rally",
  faded_breakout: "faded breakout",
  faded_extension: "faded extension",
  joined_rally: "joined rally",
  joined_breakout: "joined breakout",
  joined_pullback: "joined pullback",
  joined_extension: "joined extension",
  test: "test",
  range_play: "range play",
};

export const DISCIPLINE_LABEL: Record<string, string> = {
  clean: "clean",
  revenge: "revenge",
  fomo: "FOMO",
  rushed: "rushed",
  oversized: "oversized",
  undersized: "undersized",
  tight_stop: "tight stop",
  wide_stop: "wide stop",
  moved_stop: "moved stop",
  cut_early: "cut early",
};

/** A candidate's distance, in ticks, with the side it sat on. Signed as the
 *  tagger signs it — fill price minus the level — so "+4" reads as *the level
 *  was 4 ticks below where you got in*. */
function distanceLabel(c: LevelCandidate): string {
  if (c.dist_ticks == null) return "";
  const t = Math.round(c.dist_ticks);
  return t === 0 ? "on it" : `${t > 0 ? "+" : ""}${t}t`;
}

/** One row of pickable chips — the shape both axes share. */
/** One enumerated axis as a row of pickable chips. Exported for the journal
 *  form on the trade detail, which asks the same two axes with the same chips —
 *  a second picker there would be how the two surfaces start disagreeing about
 *  what a setup is. */
export function AxisRow({
  options,
  labels,
  value,
  attr,
  onPick,
}: {
  options: { id: string; says: string }[];
  labels: Record<string, string>;
  value: string | null;
  attr: string;
  onPick: (id: string | null) => void;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} {...{ [`data-review-${attr}s`]: true }}>
      {options.map((o) => {
        const on = value === o.id;
        return (
          <button
            key={o.id}
            type="button"
            {...{ [`data-review-${attr}-pick`]: o.id }}
            onClick={() => onPick(on ? null : o.id)}
            title={o.says}
            style={{
              fontSize: 11,
              padding: "2px 7px",
              borderRadius: 3,
              cursor: "pointer",
              background: on ? palette.text : "transparent",
              color: on ? "#0b0d12" : palette.muted,
              border: `1px solid ${on ? palette.text : palette.cardBorder}`,
            }}
          >
            {labels[o.id] ?? o.id}
          </button>
        );
      })}
    </div>
  );
}

/** The tape's description of the moment, read-only. */
function ContextChips({ chips }: { chips: ContextChip[] }) {
  if (!chips.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} data-review-context>
      {chips.map((c) => (
        <span
          key={c.key}
          data-review-context-chip={c.key}
          title={c.title}
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 9,
            color: palette.muted,
            border: `1px dotted ${palette.cardBorder}`,
            cursor: "help",
          }}
        >
          {c.label}
        </span>
      ))}
    </div>
  );
}

/** The watched-level chips: every measured candidate, nearest first, folded
 *  past the first handful, with the exclusive "no level" at the end. Exported
 *  for the journal form on the trade detail, which asks the same question over
 *  the same candidates now that the trade's replay sits right above it — the
 *  AxisRow argument again: a second picker is how two surfaces start
 *  disagreeing about what a pick is. */
export function LevelRow({
  levels,
  picked,
  noLevel,
  onChange,
}: {
  levels: LevelCandidate[];
  picked: string[];
  noLevel: string;
  onChange: (next: string[]) => void;
}) {
  const [allLevels, setAllLevels] = useState(false);
  // The nearest handful answer almost every trade; the fold keeps the rest
  // reachable without making a seventeen-trade review a wall of chips. A pick
  // already sitting in the tail forces it open, or it would look unanswered.
  const tailHasPick = levels.slice(SHOWN_LEVELS).some((c) => picked.includes(c.id));
  const shown = allLevels || tailHasPick ? levels : levels.slice(0, SHOWN_LEVELS);
  const hidden = levels.length - shown.length;
  const noneOn = picked.includes(noLevel);
  /** Toggling a real level drops "no level", which cannot be true beside it. */
  const toggleLevel = (id: string) =>
    onChange(
      picked.includes(id)
        ? picked.filter((p) => p !== id)
        : [...picked.filter((p) => p !== noLevel), id],
    );

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} data-review-levels>
      {shown.map((c) => {
        const on = picked.includes(c.id);
        return (
          <button
            key={c.id}
            type="button"
            data-review-level-pick={c.id}
            data-review-level-on={on ? "1" : undefined}
            onClick={() => toggleLevel(c.id)}
            title={
              `${c.id}` +
              (c.dist_ticks == null ? "" : ` · ${distanceLabel(c)} from your fill`) +
              (c.rank == null
                ? " · measured, but not the level its group was scored on"
                : ` · closer than ${Math.round((1 - c.rank) * 100)}% of comparable moments`)
            }
            style={{
              fontSize: 11,
              padding: "2px 7px",
              borderRadius: 9,
              cursor: "pointer",
              background: on ? palette.text : "transparent",
              color: on ? "#0b0d12" : palette.muted,
              border: `1px solid ${on ? palette.text : palette.cardBorder}`,
            }}
          >
            {c.label}
            {c.dist_ticks != null && (
              <span style={{ opacity: 0.6, marginLeft: 4 }}>{distanceLabel(c)}</span>
            )}
          </button>
        );
      })}
      {hidden > 0 && (
        <button
          type="button"
          data-review-level-more
          onClick={() => setAllLevels(true)}
          title="Every level measured at this entry, furthest last"
          style={{
            fontSize: 11,
            padding: "2px 7px",
            borderRadius: 9,
            cursor: "pointer",
            background: "transparent",
            color: palette.muted,
            border: `1px solid ${palette.cardBorder}`,
          }}
        >
          +{hidden} more
        </button>
      )}
      <button
        type="button"
        data-review-level-pick={noLevel}
        data-review-level-on={noneOn ? "1" : undefined}
        // Exclusive, and it clears rather than merges: "no level" and "these
        // levels" cannot both be true, and the server refuses the mix.
        onClick={() => onChange(noneOn ? [] : [noLevel])}
        title={
          levels.length
            ? "This trade was not taken off any of them"
            : "Nothing was measured for this trade — no cached tape, or nothing near. Still a real answer."
        }
        style={{
          fontSize: 11,
          padding: "2px 7px",
          borderRadius: 9,
          cursor: "pointer",
          background: noneOn ? palette.text : "transparent",
          color: noneOn ? "#0b0d12" : palette.muted,
          border: `1px dashed ${palette.cardBorder}`,
        }}
      >
        no level
      </button>
    </div>
  );
}

export function ReviewCard({
  row,
  value,
  vocab,
  tagSuggestions,
  onChange,
}: {
  row: DrillTradeRow;
  value: ReviewAnswers;
  vocab: ReviewVocab | null;
  tagSuggestions: string[];
  onChange: (patch: Partial<ReviewAnswers>) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 4 }}>
      {/* What the tape says — read first, answers nothing. */}
      <ContextChips chips={row.context_chips ?? []} />

      {/* Watched level */}
      <LevelRow
        levels={row.levels ?? []}
        picked={value.watchedLevels}
        noLevel={vocab?.no_level ?? "none"}
        onChange={(watchedLevels) => onChange({ watchedLevels })}
      />

      {/* Setup — what the trade was, faded-vs-joined first */}
      <AxisRow
        options={vocab?.setups ?? []}
        labels={SETUP_LABEL}
        value={value.setup}
        attr="setup"
        onPick={(setup) => onChange({ setup })}
      />

      {/* Discipline — whether the plan was followed */}
      <AxisRow
        options={vocab?.disciplines ?? []}
        labels={DISCIPLINE_LABEL}
        value={value.discipline}
        attr="discipline"
        onPick={(discipline) => onChange({ discipline })}
      />

      {/* Tags — optional colour for whatever the axes could not say */}
      <TagInput
        value={value.tags}
        onChange={(tags) => onChange({ tags })}
        suggestions={tagSuggestions}
        placeholder={value.tags.length ? "" : "anything else — wicked out, lucky… (optional)"}
      />

      <input
        data-review-note
        value={value.note}
        onChange={(e) => onChange({ note: e.target.value })}
        placeholder="anything the tags could not say… (optional)"
        style={{ width: "100%", fontSize: 11 }}
      />
    </div>
  );
}
