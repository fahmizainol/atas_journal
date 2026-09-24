import { useCallback, useEffect, useMemo, useState } from "react";
import { NavMenu } from "../components/NavMenu";
import { FullscreenButton } from "../components/charts/FullscreenButton";
import { RecallCard } from "../components/charts/RecallCard";
import {
  answered,
  answersDirty,
  DISCIPLINE_LABEL,
  GRADE_COLOR,
  ReviewCard,
  SETUP_LABEL,
  seedAnswers,
  type ReviewAnswers,
} from "../components/charts/ReviewCard";
import {
  RATINGS,
  useRecallBack,
  useRecallDeck,
  useRecallRate,
  useRecallUndo,
  type RecallBack,
  type RecallOrder,
} from "../hooks/useRecall";
import {
  useReviewVocab,
  useSaveTradeReview,
  useTradeTags,
  type DrillTradeRow,
} from "../hooks/useReplays";
import { fmtCountdown, fmtPts, fmtUsd } from "../lib/simViews";
import { palette } from "../theme";

// Recall: spaced repetition over your own reviewed trades
// (docs/trade-grading-plan.md). The card is one trade's session stopped near its
// entry; you read it, guess what price does next, flip, and rate yourself.
//
// **The front is also where a trade gets its grade** (2026-08-31). A card whose
// trade is ungraded asks for A-D beside the guess box, before the flip — the
// one moment in the app where the question can be answered without the outcome
// on screen. The pick locks at the flip and rides out with the rating; the
// server takes only the first grade a trade is ever given, and Undo takes the
// grade back with the rating that wrote it.
//
// **Nothing here is graded, and that is deliberate.** There is no objective
// answer to "what does price do next", so a score would either be an outcome
// score — which trains outcome-chasing on single-trade noise — or a fiction. The
// rating is self-assigned exactly as an Anki card's is. It admits dishonesty;
// so does every deck anyone has ever learned from, and a rating you fudge only
// costs you the rep you needed.
//
// **What the reps are for.** The usual objection to repeating a chart is that
// the second look is recognition rather than reading. True, and here it is the
// point: the goal is to recognise the situations you have already paid for. The
// back card carries the review — the grade, the level, the tags — so what gets
// rehearsed is the lesson, not just the picture.

const TZ = "America/New_York";

/** What ended the trade, in words rather than the log's token. `reduce` is size
 *  taken off by an order the other way — a scale-out or the closing half of a
 *  flip — and `trail` is a stop the ladder had already moved, which is a
 *  different event from the stop that was placed. */
const EXIT_SAYS: Record<RecallOrder["exit_reason"], string> = {
  manual: "closed by hand",
  stop: "out on the stop",
  target: "out on the target",
  trail: "out on the trail",
  reduce: "scaled out",
};

/** The order behind the fill, as one line: how you got in, where the bracket
 *  sat when the position opened, and what took you out.
 *
 *  The levels are the *opening* ones, so the line has to carry the two things
 *  that can move them afterwards — a trail and a bracket drag — or a stop
 *  quoted below the entry beside "out on the stop" at a profit reads as a bug
 *  rather than as a stop that was moved up. */
function orderLine(o: RecallOrder): string {
  const parts: string[] = [];
  if (o.open_type === "market") {
    parts.push("market in");
  } else {
    const at = o.rest_price != null ? ` @ ${fmtPts(o.rest_price)}` : "";
    const waited = o.rest_ms != null && o.rest_ms >= 1000
      ? `, waited ${fmtCountdown(o.rest_ms)}` : "";
    parts.push(`${o.open_type}${at}${waited}`);
  }
  parts.push(o.stop != null ? `stop ${fmtPts(o.stop)}` : "no stop");
  if (o.target != null) parts.push(`target ${fmtPts(o.target)}`);
  if (o.trail_pts) {
    parts.push(o.trail_be_only
      ? `breakeven at ${o.trail_pts}` : `trailing ${o.trail_pts}`);
  }
  if (o.moved) parts.push("bracket moved");
  parts.push(EXIT_SAYS[o.exit_reason] ?? o.exit_reason);
  return parts.join(" · ");
}

/** The review on the back of a card — read as it stands, or corrected in place.
 *
 *  **Why the review is editable here and not on the trade page.** `TradeDetail`
 *  shows the same three answers read-only, on the stated ground that a review is
 *  written against the tape rather than beside a P&L figure. This card *is* the
 *  tape: the chart above is the trade's own session, stopped at the fill and
 *  played out on the flip. Rereading a trade with the chart in front of you is
 *  the moment you find out the grade was generous or that you were watching the
 *  globex POC and wrote down the session one — so the correction belongs where
 *  it is noticed, and nowhere else on this page can it be made.
 *
 *  It is the same `ReviewCard` the replay and drill reviews use, saving through
 *  the same `PUT /notes`. Three surfaces, one picker, one write path: a second
 *  editor here would be how two of them start disagreeing about what a review is.
 *
 *  **Folded behind ✎.** A card is a card — flipping it should answer a question,
 *  not open a form — and the rating buttons have to stay where the thumb already
 *  is. Editing is the exception, so it costs one click.
 *
 *  The rep's own rating is untouched by any of this: the grade is what you think
 *  of the *trade*, the rating is how well you read the *chart*, and correcting
 *  one says nothing about the other. */
function RecallReview({ back }: { back: RecallBack }) {
  const [editing, setEditing] = useState(false);
  const vocab = useReviewVocab();
  const tags = useTradeTags({ enabled: editing });
  const save = useSaveTradeReview();

  // The shape `ReviewCard` and its two helpers read. The echo fields ride along
  // untouched — see `RecallBack` — so a correction cannot blank the model, its
  // rule checks, or the archived badges.
  const row = useMemo<DrillTradeRow>(
    () => ({
      trade_key: back.trade_key,
      direction: back.direction,
      entry_ts_local: back.entry_ts_local,
      net_pnl: back.net_pnl,
      model_id: back.model_id,
      rules_met: back.rules_met,
      reviewed: true,
      note: back.note,
      tags: back.tags,
      setups: back.setups,
      confluences: back.confluences,
      grade: back.grade,
      setup: back.setup,
      discipline: back.discipline,
      watched_levels: back.watched_levels,
      levels: back.levels,
      context_chips: [],
    }),
    [back],
  );
  // Seeded once per card — the component is keyed on the trade — so a refetch
  // landing mid-edit cannot swallow a half-typed note. After a save the row and
  // the answers agree, which is what closes the Save button rather than a flag.
  const [answers, setAnswers] = useState<ReviewAnswers>(() => seedAnswers(row));
  const patch = useCallback(
    (p: Partial<ReviewAnswers>) => setAnswers((prev) => ({ ...prev, ...p })),
    [],
  );

  const dirty = answersDirty(answers, row);
  // The same gate the replay review files under: a card is in this deck because
  // its trade was answered, and an edit that removed the answer would drop it
  // out of the deck silently, mid-rep. Correcting a review is a move; un-
  // answering one is not.
  const complete = answered(answers);

  if (!editing) {
    return (
      <>
        <div
          style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 12, marginTop: 6 }}
          data-recall-review
        >
          {back.grade && (
            <b style={{ fontSize: 15 }} data-recall-grade={back.grade}>
              {back.grade}
            </b>
          )}
          {back.watched_labels.length > 0 && (
            <span>off the {back.watched_labels.join(" + ")}</span>
          )}
          {back.setup && (
            <span data-recall-setup={back.setup}>{SETUP_LABEL[back.setup] ?? back.setup}</span>
          )}
          {back.discipline && back.discipline !== "clean" && (
            <span data-recall-discipline={back.discipline} style={{ color: palette.orange }}>
              {DISCIPLINE_LABEL[back.discipline] ?? back.discipline}
            </span>
          )}
          <button
            type="button"
            data-recall-review-edit
            onClick={() => setEditing(true)}
            title="Change the levels, the axes, the tags or the note — with the chart it was written against right here. The grade is not editable: it was answered blind, and the blind answer stands."
            style={{ marginLeft: "auto", fontSize: 11, padding: "1px 6px" }}
          >
            ✎
          </button>
        </div>
        {back.tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
            {back.tags.map((t) => (
              <span
                key={t}
                style={{
                  fontSize: 11,
                  padding: "1px 6px",
                  borderRadius: 9,
                  border: `1px solid ${palette.cardBorder}`,
                }}
              >
                {t}
              </span>
            ))}
          </div>
        )}
        {back.note && (
          <div style={{ fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>{back.note}</div>
        )}
      </>
    );
  }

  return (
    <div data-recall-review-editor style={{ marginTop: 6 }}>
      <ReviewCard
        row={row}
        value={answers}
        vocab={vocab.data ?? null}
        tagSuggestions={tags.data?.tags ?? []}
        onChange={patch}
      />
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
        <button
          type="button"
          className="btn-accent"
          data-recall-review-save
          disabled={!dirty || !complete || save.isPending}
          title={
            !complete
              ? "A review needs a level (or “no level”), a setup and a discipline call — this card is in the deck because it has them"
              : undefined
          }
          onClick={() =>
            save.mutate(
              {
                tradeKey: back.trade_key,
                setup: answers.setup,
                discipline: answers.discipline,
                watchedLevels: answers.watchedLevels,
                tags: answers.tags,
                note: answers.note,
                setups: back.setups,
                confluences: back.confluences,
                modelId: back.model_id,
                rulesMet: back.rules_met,
              },
              { onSuccess: () => setEditing(false) },
            )
          }
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          data-recall-review-cancel
          onClick={() => {
            setAnswers(seedAnswers(row));
            setEditing(false);
          }}
        >
          Cancel
        </button>
        {save.isError && (
          <span className="neg" style={{ fontSize: 11 }}>
            {save.error instanceof Error ? save.error.message : "Save failed"}
          </span>
        )}
      </div>
    </div>
  );
}

export function Recall() {
  const deck = useRecallDeck();
  const rate = useRecallRate();
  const undo = useRecallUndo();
  const vocab = useReviewVocab();
  const cards = deck.data?.cards ?? [];

  const [idx, setIdx] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [guess, setGuess] = useState("");
  // The blind grade, when this card's trade still owes one. Held here rather
  // than in the form so the flip can lock it and the rating can send it.
  const [grade, setGrade] = useState<string | null>(null);
  // The card an undo is bringing back, held until the refetched deck contains
  // it. Its restored due date decides where it lands in the order — an undone
  // first showing returns to the *new* cards — so the index cannot be guessed
  // at the moment of the click.
  const [returning, setReturning] = useState<string | null>(null);

  const card = cards[idx] ?? null;
  const undoSlot = deck.data?.undo ?? null;
  // A rating with no matching label is one this build does not know — the button
  // still works, so it says the neutral thing rather than nothing.
  const undoLabel =
    RATINGS.find((r) => r.id === undoSlot?.rating)?.label ?? "last rating";
  // Fetched only once flipped: asking earlier would put the answer in the cache
  // while the front is still on screen, which is what the split route prevents.
  const backQ = useRecallBack(flipped && card ? card.trade_key : null);
  const back = backQ.data ?? null;

  // A deck that reloads under us (a rating invalidates it) must not leave the
  // index pointing past the end.
  useEffect(() => {
    if (idx >= cards.length && cards.length) setIdx(0);
  }, [cards.length, idx]);

  // Land on the card the undo restored, once the deck it rejoined has arrived.
  useEffect(() => {
    if (!returning) return;
    const at = cards.findIndex((c) => c.trade_key === returning);
    if (at >= 0) {
      setIdx(at);
      setReturning(null);
    }
  }, [cards, returning]);

  const answer = useCallback(
    (rating: number) => {
      if (!card) return;
      rate.mutate(
        {
          trade_key: card.trade_key,
          rating,
          guess: guess.trim() || null,
          // Only for a trade the deck said owes one — the server refuses a
          // second grade, and sending one would 422 the whole rating.
          grade: card.needs_grade ? grade : undefined,
        },
        {
          onSuccess: () => {
            setFlipped(false);
            setGuess("");
            setGrade(null);
            // The rated card leaves the deck on the refetch, so staying put
            // lands on the next one. Wrapping is for the case where it was last.
            setIdx((i) => (i + 1 >= cards.length ? 0 : i));
          },
        },
      );
    },
    [card, cards.length, guess, grade, rate],
  );

  // A different card must not inherit the previous card's blind answers.
  useEffect(() => {
    setGrade(null);
  }, [card?.trade_key]);

  if (deck.isLoading) return <div className="page-fallback" />;

  return (
    <div className="recall-page">
      {/* The whole of this page's persistent chrome, in the same ~36px row the
          chart pages use — Recall draws no shell chrome either (see `chrome` in
          lib/workspaces), so this is the way out, what card you are on, and how
          much deck is left. Not sticky: the page is exactly one viewport tall
          and never scrolls, so normal flow is already always visible. */}
      <div className="chart-topbar">
        <NavMenu />
        <span className="chart-topbar-title static">Recall</span>
        {card && (
          <span className="recall-ident" data-recall-card={card.trade_key}>
            <b>{card.root}</b>
            {card.reps > 0 ? (
              <span className="muted">
                seen {card.reps}×{card.lapses > 0 && ` · ${card.lapses} lapse${card.lapses > 1 ? "s" : ""}`}
              </span>
            ) : (
              <span className="muted">new card</span>
            )}
          </span>
        )}
        <div className="chart-topbar-end">
          <span className="recall-deck">
            <span data-recall-stat="due">{cards.length} due</span>
            <span data-recall-stat="total">{deck.data?.total ?? 0} in the deck</span>
            {deck.data?.next_due && (
              <span className="muted" data-recall-stat="next">
                next due {deck.data.next_due}
              </span>
            )}
          </span>
          {/* Takes back the last rating — the schedule, the rep and the
              scheduler's state, all of it. In the chrome rather than beside the
              rating buttons because the moment you want it is *after* the card
              has gone, including on the empty deck you just rated yourself into.
              Only the last rating is revertible, so this is one button and not a
              history: see the `recall_undo` schema. */}
          {undoSlot && (
            <button
              type="button"
              data-recall-undo={undoSlot.trade_key}
              disabled={undo.isPending}
              onClick={() =>
                undo.mutate(undefined, {
                  onSuccess: (res) => {
                    setReturning(res.trade_key);
                    setFlipped(false);
                    setGuess(res.guess ?? "");
                    // The undone rating's grade went back with it; the card
                    // returns asking again, so nothing may be pre-answered.
                    setGrade(null);
                  },
                })
              }
              title={
                `Take back your ${undoLabel} — the card comes back exactly as it was,` +
                " schedule and all. Only the last rating can be undone."
              }
              style={{ fontSize: 11 }}
            >
              ↶ Undo {undoLabel}
            </button>
          )}
          {undo.isError && (
            <span className="neg" style={{ fontSize: 11 }}>
              {undo.error instanceof Error ? undo.error.message : "Undo failed"}
            </span>
          )}
          <FullscreenButton />
        </div>
      </div>

      {!card && (
        <div className="recall-body">
          <div className="panel muted">
            {deck.data?.total
              ? "Nothing due. The deck comes back on its own schedule — that is the whole idea."
              : "No cards yet. A trade joins the deck once its sitting has been reviewed."}
          </div>
        </div>
      )}

      {card && (
        <div className="recall-body">
          <div className="recall-main">
            <RecallCard key={card.trade_key} card={card} back={back} tz={TZ} />
          </div>

          {!flipped ? (
            <div className="recall-side" data-recall-form>
              {card.needs_grade && (
                <div style={{ marginBottom: 6 }} data-recall-grade-ask>
                  <div style={{ fontSize: 11, color: palette.muted, marginBottom: 4 }}>
                    Grade it first — how good was this trade, knowing only what
                    this chart knows? Locked at the flip; only your first answer
                    is ever kept.
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    {(vocab.data?.grades ?? []).map((g) => {
                      const on = grade === g.id;
                      return (
                        <button
                          key={g.id}
                          type="button"
                          data-recall-grade-pick={g.id}
                          onClick={() => setGrade(on ? null : g.id)}
                          title={g.says}
                          style={{
                            flex: 1,
                            fontSize: 12,
                            fontWeight: 600,
                            padding: "3px 0",
                            cursor: "pointer",
                            background: on ? (GRADE_COLOR[g.id] ?? palette.text) : "transparent",
                            color: on ? "#0b0d12" : palette.muted,
                            border: `1px solid ${palette.cardBorder}`,
                            borderRadius: 3,
                          }}
                        >
                          {g.id}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              <input
                data-recall-guess
                value={guess}
                onChange={(e) => setGuess(e.target.value)}
                placeholder="your read, in a few words… (optional, never marked)"
                style={{ width: "100%", fontSize: 12 }}
              />
              <div className="recall-actions">
                <button
                  type="button"
                  data-recall-flip
                  disabled={card.needs_grade && !grade}
                  onClick={() => setFlipped(true)}
                  title={
                    card.needs_grade && !grade
                      ? "This trade has never been graded — grade it blind before the flip shows you how it went"
                      : "Show what was actually traded here, and what you said about it in review"
                  }
                >
                  Flip
                </button>
              </div>
            </div>
          ) : (
            <div className="recall-side" data-recall-back>
              {backQ.isLoading && <div className="muted" style={{ fontSize: 12 }}>…</div>}
              {back && (
                <>
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 12 }}>
                    <span
                      style={{ color: back.direction === "Short" ? palette.red : palette.green }}
                      data-recall-direction={back.direction ?? ""}
                    >
                      {back.direction} ×{back.max_contracts}
                    </span>
                    <span>
                      {fmtPts(back.avg_entry)} → {fmtPts(back.avg_exit)}
                    </span>
                    <b style={{ color: back.net_pnl >= 0 ? palette.green : palette.red }}>
                      {fmtUsd(back.net_pnl)}
                    </b>
                  </div>

                  {/* How the position was actually opened, and what it was
                      bracketed with — the decision behind the fill, which the
                      entry→exit line above says nothing about. Drawn on the
                      chart too (RecallCard), so the numbers here have levels to
                      go with them. */}
                  {back.order && (
                    <div
                      data-recall-order={back.order.open_type}
                      style={{ fontSize: 11, color: palette.muted, marginTop: 4 }}
                      title="The order behind the fill, as the sitting recorded it. The stop and target are the ones the position opened with."
                    >
                      {orderLine(back.order)}
                    </div>
                  )}

                  {card.needs_grade && grade && (
                    <div
                      data-recall-grade-committed={grade}
                      style={{ fontSize: 11, color: palette.muted, marginTop: 4 }}
                      title="Given before the flip, kept with the rating. Undo the rating to take it back."
                    >
                      you graded it{" "}
                      <b style={{ color: GRADE_COLOR[grade] ?? palette.text }}>{grade}</b>
                      {" "}— blind
                    </div>
                  )}
                  <RecallReview key={back.trade_key} back={back} />
                  {/* The day around the trade, under the trade's own note and
                      set apart from it: this one was written about the whole
                      sitting, so it is context for the card rather than an
                      answer about it. Labelled for the same reason — two
                      unmarked paragraphs read as one long note. */}
                  {back.session_note && (
                    <div
                      data-recall-session-note
                      style={{
                        fontSize: 11,
                        color: palette.muted,
                        marginTop: 8,
                        paddingLeft: 8,
                        borderLeft: `2px solid ${palette.cardBorder}`,
                        lineHeight: 1.5,
                      }}
                      title="What you wrote about the whole session when you reviewed it"
                    >
                      the session: {back.session_note}
                    </div>
                  )}

                  {guess.trim() && (
                    <div
                      style={{ fontSize: 11, color: palette.muted, marginTop: 8, lineHeight: 1.5 }}
                      title="Yours, from a moment ago. Kept, never marked."
                    >
                      you said: {guess.trim()}
                    </div>
                  )}
                  {back.reps.filter((r) => r.guess).length > 0 && (
                    <details style={{ fontSize: 11, color: palette.muted, marginTop: 6 }}>
                      <summary style={{ cursor: "pointer" }}>
                        what you said the last {back.reps.filter((r) => r.guess).length} time(s)
                      </summary>
                      <ul style={{ margin: "4px 0 0", paddingLeft: 16, lineHeight: 1.5 }}>
                        {back.reps
                          .filter((r) => r.guess)
                          .map((r, i) => (
                            <li key={i}>
                              {r.shown_at.slice(0, 10)} — {r.guess}
                            </li>
                          ))}
                      </ul>
                    </details>
                  )}

                  <div className="recall-actions" data-recall-ratings>
                    {RATINGS.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        data-recall-rate={r.id}
                        disabled={rate.isPending}
                        onClick={() => answer(r.id)}
                        title={r.says}
                      >
                        {r.label}
                      </button>
                    ))}
                  </div>
                  {rate.isError && (
                    <div className="notice" style={{ marginTop: 6 }}>
                      Couldn't record that
                      {rate.error instanceof Error ? ` — ${rate.error.message}` : ""}.
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
