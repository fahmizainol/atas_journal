// The forced review: every trade the sitting took, answered for against the tape.
//
// It is a panel in the replay's own rail rather than a page of its own, and that
// is the whole design. A review that happens somewhere else is a review done
// from memory and a summary table; done here it is done *against the tape*, with
// the chart at the moment in question and every level that was on it at the
// time. Pressing a trade puts the tape five seconds before its entry **and runs
// it** — you are meant to watch the last breath before the click, not to arrive
// at the fill, and a seek that landed paused short of the entry showed a chart
// with none of your trades on it yet.
//
// It ran at a forced 1× until 2026-08-21. The speed is the transport's now: a
// review is a dozen seeks, and re-setting the speed after each one made a
// setting into something you had to keep saying.
//
// What each trade owes is the level it was taken off, a setup and a discipline
// call (`ReviewCard`, docs/trade-grading-plan.md). The tags and the note are
// optional; the grade is answered later, blind, at the recall front.
//
// Beneath the cards sits the one field that is about the *sitting* rather than
// any trade in it (added 2026-08-22 on request): what the session was, in your
// words. It is optional and it is deliberately last — the thing you can only say
// once you have been back through every trade. It writes the attempt's own
// `note`, the field the history table has always rendered and nothing wrote, and
// the server mirrors it onto the journal session row, so the sentence shows up
// wherever that session does.
//
// **The panel is those four per-trade fields and nothing else.** Two things were removed
// on 2026-08-20 rather than moved: the context strip, whose numbers live on the
// trade detail where they get read deliberately instead of glanced at while
// answering; and the flag verdicts, because leak/justified asks whether an
// account rule was tripped, which is a different question from how good the
// trade was. Flags are still raised onto the attempt as a fact about the
// sitting — they are just no longer something to clear before filing. (The
// server kept its half of that gate until 2026-08-23, which made a flagged
// sitting impossible to file and so blocked its account; it is gone now.)
//
// The answers land on the journal's own rows in one write (`PUT /notes`), so a
// reviewed replay trade is a journaled trade with no copy. **File review saves
// every card itself** and then asks for `reviewed`, which the server grants only
// when every trade carries all three.
//
// Each card keeps its own Save — one card written the moment you are happy with
// it is worth having, and it is what the "Saved" label reports — but pressing it
// is no longer the toll on the way out.

import { useState } from "react";
import type { Trade } from "../../lib/replaySim";
import type { DrillTradeRow } from "../../hooks/useReplays";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";
import {
  ReviewCard,
  answered,
  answersDirty,
  seedAnswers,
  type ReviewAnswers,
  type ReviewVocab,
} from "./ReviewCard";

/** How far before the flagged moment the tape is put. Five seconds — the last
 *  breath before the click, where the decision actually got made. A minute back
 *  meant every review opened on a chart you then had to run forward yourself
 *  before anything you were reviewing was on it. */
export const REVIEW_LEAD_MS = 5_000;

export function ReviewPanel({
  attemptId,
  trades,
  journal,
  vocab,
  tagSuggestions,
  sessionNote,
  onSeek,
  onSaveTrade,
  saving,
  onFile,
  filing,
  error,
}: {
  attemptId: string;
  /** The sitting's booked trades, tape order — the seek targets. */
  trades: Trade[];
  /** The journal mirror's rows for the same trades, entry order. The join to
   *  `trades` is by index: both sides sort by entry, and the mirror writes one
   *  row per booked trade. When the counts disagree (mirror behind), the cards
   *  lose their seek rather than mis-joining. */
  journal: DrillTradeRow[];
  vocab: ReviewVocab | null;
  tagSuggestions: string[];
  /** The attempt's stored note, seeding the session box. Seeded rather than
   *  started empty because filing sends whatever is in the box: a review filed
   *  on a sitting that already carried a note must not erase it. */
  sessionNote: string;
  /** Put the replay `REVIEW_LEAD_MS` before this tape clock and run it, at
   *  whatever speed the transport is set to. */
  onSeek: (ms: number) => void;
  /** Resolves when the row is written. Awaited by File, which saves whatever is
   *  still dirty before it files — the server reads the stored rows. */
  onSaveTrade: (row: DrillTradeRow, patch: ReviewAnswers) => Promise<unknown>;
  saving: boolean;
  onFile: (note: string) => void;
  filing: boolean;
  error: string | null;
}) {
  const [cards, setCards] = useState<Record<string, ReviewAnswers>>({});
  const [at, setAt] = useState<string | null>(null);
  /** The session's own thoughts. It goes out with the file, not on a Save of its
   *  own: the cards have per-card saves because a card is finished one at a time,
   *  and this is one box written once at the end of the same sitting at the desk.
   *  (A reload before filing loses what is typed here, same as an unsaved card.) */
  const [note, setNote] = useState(sessionNote);

  const joined = trades.length === journal.length;
  const card = (row: DrillTradeRow): ReviewAnswers => cards[row.trade_key] ?? seedAnswers(row);
  const setCard = (row: DrillTradeRow, patch: Partial<ReviewAnswers>) =>
    setCards((s) => ({
      ...s,
      [row.trade_key]: { ...(s[row.trade_key] ?? seedAnswers(row)), ...patch },
    }));

  const settled = journal.filter((r) => answered(card(r))).length;
  const complete = settled === journal.length;

  /** Save everything still dirty, then file.
   *
   *  Sequential, not in parallel: the journal mirror rewrites this attempt's
   *  rows on every write, and a fan of concurrent PUTs is a race over the same
   *  rows. Nothing files until the last one lands — the server grants
   *  `reviewed` off what is stored.
   *
   *  A failed write stops the whole thing rather than filing a review the
   *  server would refuse (or worse, grant against half-written rows). What went
   *  wrong is already on screen: `error` carries the mutation's message. */
  const [filingAll, setFilingAll] = useState(false);
  const fileAll = async () => {
    setFilingAll(true);
    try {
      for (const row of journal) {
        const c = card(row);
        if (!answersDirty(c, row)) continue;
        await onSaveTrade(row, { ...c, note: c.note.trim() });
      }
    } catch {
      setFilingAll(false);
      return;
    }
    setFilingAll(false);
    // Nothing to carry but the sentence: the answers are already on the journal
    // rows the loop above wrote, and the server files off those.
    onFile(note.trim());
  };

  const clockOf = (ts: string) => (ts.includes(" ") ? ts.split(" ")[1] : ts).slice(0, 5);

  return (
    <div className="sim-card sim-review" data-review-panel>
      <div className="sim-sec-t" style={{ flex: "none" }}>
        Review
        <span className="r" data-review-progress>
          {settled}/{journal.length} trades
        </span>
      </div>
      {/* The cards are the part that scrolls, and the standing instructions
          scroll away with them — they are read once at the top of a review and
          then they are in the way of the thing they describe. What stays fixed
          is the count and File review, so the way out is never further away than
          the trades made it.
          `flex: 1` + `min-height: 0` only bite once the dock hands its height
          over (`.sim-panel.reviewing`); undocked the panel is short and this is
          the same list it always was. */}
      <div
        data-review-list
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
        }}
      >
        <div style={{ fontSize: 11, color: palette.muted, lineHeight: 1.5 }}>
          Read-only on the tape — nothing here places an order. Press a trade and
          the tape runs from five seconds before its entry; the decision is in
          those seconds, not in the fill, so slow the transport down for it.
          {" "}Every trade owes the level you were trading off, a setup and a discipline call.
          Filing saves every card and the session note at the bottom, so Save per
          card is optional.
        </div>
        {journal.map((row, i) => {
          const t = joined ? trades[i] : null;
          const c = card(row);
          const done = answered(c);
          return (
            <div
              key={row.trade_key}
              data-review-trade={i}
              style={{
                border: `1px solid ${done ? palette.cardBorder : palette.orange}`,
                borderRadius: 4,
                padding: 6,
              }}
            >
              <button
                type="button"
                disabled={!t}
                onClick={() => {
                  if (!t) return;
                  setAt(row.trade_key);
                  onSeek(Math.max(0, t.entryMs - REVIEW_LEAD_MS));
                }}
                style={{
                  display: "flex",
                  width: "100%",
                  gap: 6,
                  alignItems: "baseline",
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: t ? "pointer" : "default",
                  textAlign: "left",
                  color: at === row.trade_key ? palette.text : palette.muted,
                  fontSize: 12,
                }}
                title={t ? "Play it again from five seconds before the entry" : undefined}
              >
                <span style={{ fontVariantNumeric: "tabular-nums" }}>
                  {clockOf(row.entry_ts_local)}
                </span>
                <span>{row.direction ?? "—"}</span>
                <span
                  style={{
                    fontFamily: "monospace",
                    color: row.net_pnl < 0 ? palette.red : palette.text,
                  }}
                >
                  {fmtUsd(row.net_pnl)}
                </span>
                {t && <span style={{ marginLeft: "auto", fontSize: 10 }}>seek →</span>}
              </button>

              <ReviewCard
                row={row}
                value={c}
                vocab={vocab}
                tagSuggestions={tagSuggestions}
                onChange={(patch) => setCard(row, patch)}
              />

              <button
                type="button"
                className="chart-topbar-btn"
                data-review-save
                style={{ marginTop: 6 }}
                disabled={saving || !answersDirty(c, row) || !done}
                onClick={() => onSaveTrade(row, { ...c, note: c.note.trim() })}
                title={
                  done
                    ? undefined
                    : "A trade owes the level it was taken off, a setup and a discipline call."
                }
              >
                {answersDirty(c, row) ? "Save" : "Saved"}
              </button>
            </div>
          );
        })}

        {/* The sitting, not the trades. Last on purpose: it is the one thing you
            can only write once you have been back through all of them, and it is
            the only field here that no gate reads — a session with nothing worth
            saying about it files just as well empty.

            It scrolls with the cards rather than sitting fixed above File review.
            Pinned it was four rows the trades never got, and in a dock squeezed
            short by the blotter the fixed rows overflowed a panel that clips
            (`.sim-panel.reviewing { overflow-y: hidden }`) — the note went out of
            the bottom with no scrollbar to bring it back. Scrolling, the trades
            lead to it the same way they lead to File review. */}
        <div style={{ marginTop: 2 }}>
          <div style={{ fontSize: 11, color: palette.muted, marginBottom: 4 }}>
            The session, in your words <span style={{ opacity: 0.7 }}>(optional)</span>
          </div>
          <textarea
            data-review-session-note
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={4}
            placeholder="what the day was doing, what you were doing about it, what you would want to read before the next one…"
            style={{
              width: "100%",
              fontSize: 11,
              lineHeight: 1.5,
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />
        </div>
      </div>

      {error && (
        <div style={{ color: palette.red, fontSize: 11, marginTop: 8, lineHeight: 1.5 }}>
          {error}
        </div>
      )}
      <button
        type="button"
        data-review-file
        disabled={!complete || filing || filingAll}
        onClick={fileAll}
        style={{
          width: "100%",
          marginTop: 8,
          padding: "6px 0",
          fontSize: 12,
          cursor: complete ? "pointer" : "default",
        }}
        title={
          complete
            ? "Save every card and file the review — the next sitting opens once this lands"
            : "Every trade needs a level, a setup and a discipline call. The server checks this too; a partial review is a gate you clear by scrolling."
        }
      >
        {filingAll
          ? "Saving…"
          : filing
            ? "Filing…"
            : complete
              ? "File review"
              : `${journal.length - settled} still to answer`}
      </button>
      <div style={{ fontSize: 10, color: palette.muted, marginTop: 4, opacity: 0.7 }}>
        {attemptId}
      </div>
    </div>
  );
}
