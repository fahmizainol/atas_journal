// Backtest mode's review: the rep is over, here is what you took — say which
// level each trade was taken off, what it was (setup) and how it was taken
// (discipline), tick which of the model's rules it met. The grade comes later,
// blind, at the recall front.
//
// The answers are **forced**: 🎲 does not draw the next rep while any trade of
// this one is unreviewed, and the server refuses the create the same way — so a
// reload that throws this panel away changes nothing. The rules stay optional: a
// trade with no rule rows is `unscored`, not `broke` ("counting it as broke
// would slander it"), and forcing ticks would manufacture compliance data.
//
// It asks exactly what the replay review asks (`ReviewCard`) — the model column
// was the only thing a drill ever hid, since a rep's model is its binding and
// re-asking it was a question with one answer, and there is no model column now.
//
// It is also the one place worth being honest about what this data is. Every
// answer on this panel is given with the P&L on screen, so it measures partly
// how you remember the trade. That is a training aid, not evidence, and the
// panel says so at the bottom rather than leaving it implied.

import { useEffect, useState } from "react";
import type { ModelRule } from "../../lib/types";
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

/** The clock off an ISO-ish local stamp, without parsing it as a date. The
 *  string is already in the display zone (`entry_ts_local`), and re-reading it
 *  through Date() would project it a second time. */
const clockOf = (ts: string) => (ts.includes(" ") ? ts.split(" ")[1] : ts).slice(0, 5);

export interface DrillCardState extends ReviewAnswers {
  rulesMet: number[];
}

export function DrillReview({
  modelName,
  rules,
  trades,
  vocab,
  tagSuggestions,
  saving,
  error,
  onSave,
  onDraw,
  drawBlocked,
}: {
  modelName: string;
  rules: ModelRule[];
  trades: DrillTradeRow[];
  vocab: ReviewVocab | null;
  tagSuggestions: string[];
  saving: boolean;
  error: string | null;
  onSave: (row: DrillTradeRow, patch: DrillCardState) => void;
  onDraw: () => void;
  drawBlocked: string | null;
}) {
  // Seeded from what is stored, so a rep reviewed on an earlier visit opens
  // showing its answers rather than blank. Keyed by trade so re-fetching the
  // list mid-edit cannot drop what you have typed.
  const seed = (t: DrillTradeRow): DrillCardState => ({
    ...seedAnswers(t),
    rulesMet: t.rules_met,
  });
  const [cards, setCards] = useState<Record<string, DrillCardState>>({});
  useEffect(() => {
    setCards((m) => {
      const next = { ...m };
      for (const t of trades) if (!(t.trade_key in next)) next[t.trade_key] = seed(t);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades]);

  const card = (t: DrillTradeRow): DrillCardState => cards[t.trade_key] ?? seed(t);
  const patch = (t: DrillTradeRow, p: Partial<DrillCardState>) =>
    setCards((m) => ({ ...m, [t.trade_key]: { ...card(t), ...p } }));

  const dirty = (t: DrillTradeRow): boolean => {
    const c = card(t);
    return (
      answersDirty(c, t) ||
      c.rulesMet.length !== t.rules_met.length ||
      c.rulesMet.some((r) => !t.rules_met.includes(r))
    );
  };

  return (
    <div className="sim-sec">
      <div className="sim-sec-t">Rep over · {modelName}</div>

      {trades.length === 0 ? (
        // The valuable outcome, and it says so. A rep where the setup was not
        // there is the row that makes a base rate exist, and a panel that
        // greeted it with "no trades" would read as a wasted draw.
        <div style={{ fontSize: 12, color: palette.muted, lineHeight: 1.5 }}>
          No trades — you looked and passed. That is a rep, and it is the half of
          the sample nothing else here can measure.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {trades.map((t) => {
            const c = card(t);
            const d = dirty(t);
            // Answered *and saved* — the border relaxes only when the server
            // has it, because the server is what 🎲 asks.
            const owed = !answered(c) || d;
            return (
              <div
                key={t.trade_key}
                data-drill-trade={t.trade_key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  border: `1px solid ${owed ? palette.orange : palette.cardBorder}`,
                  borderRadius: 4,
                  padding: 6,
                }}
              >
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 12 }}>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {clockOf(t.entry_ts_local)}
                  </span>
                  <span style={{ color: palette.muted }}>{t.direction ?? "—"}</span>
                  <span className={t.net_pnl >= 0 ? "pos" : "neg"}>{fmtUsd(t.net_pnl)}</span>
                  {!owed && <span style={{ color: palette.muted, fontSize: 11 }}>saved</span>}
                </div>

                <ReviewCard
                  row={t}
                  value={c}
                  vocab={vocab}
                  tagSuggestions={tagSuggestions}
                  onChange={(p) => patch(t, p)}
                />

                {rules.map((r) => (
                  <label
                    key={r.id}
                    style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}
                  >
                    <input
                      type="checkbox"
                      checked={c.rulesMet.includes(r.id)}
                      onChange={() =>
                        patch(t, {
                          rulesMet: c.rulesMet.includes(r.id)
                            ? c.rulesMet.filter((x) => x !== r.id)
                            : [...c.rulesMet, r.id],
                        })
                      }
                      style={{ margin: 0 }}
                    />
                    {r.label}
                  </label>
                ))}

                <button
                  type="button"
                  className="chart-topbar-btn"
                  style={{ alignSelf: "start" }}
                  onClick={() => onSave(t, { ...c, note: c.note.trim() })}
                  disabled={saving || !d}
                  title={
                    answered(c)
                      ? undefined
                      : "It saves, but 🎲 stays locked until every trade carries a level, a setup and a discipline call."
                  }
                >
                  {d ? "Save" : "Saved"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {error && (
        <div className="neg" style={{ fontSize: 11, marginTop: 6 }}>
          {error}
        </div>
      )}

      <button
        type="button"
        className="chart-topbar-btn"
        style={{ marginTop: 10, alignSelf: "start" }}
        onClick={onDraw}
        disabled={!!drawBlocked}
        title={drawBlocked ?? "Draw the next rep — a new day and a new hour of it"}
      >
        🎲 Next rep
      </button>

      {trades.length > 0 && (
        <div style={{ fontSize: 11, color: palette.muted, marginTop: 8, lineHeight: 1.5 }}>
          Answered after the result is on screen, so these are memory prompts
          rather than evidence. The one answer worth keeping blind — the grade —
          is asked at the recall front instead, before the outcome is shown.
        </div>
      )}
    </div>
  );
}
