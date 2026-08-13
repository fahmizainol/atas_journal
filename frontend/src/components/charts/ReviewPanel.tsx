// The forced review: the flags a sitting raised, and the verdicts that clear it.
//
// It is a panel in the replay's own rail rather than a page of its own, and that
// is the whole design. A review that happens somewhere else is a review done
// from memory and a summary table; done here it is done *against the tape*, with
// the chart at the moment in question and every level that was on it at the
// time. Pressing a flag seeks a minute before it, at 1x — you are meant to watch
// the sixty seconds you were making the decision in, not to arrive at the fill.
//
// Two verdicts, and they are the point. "Leak" and "justified" are not a grading
// scale; they are the only two answers that change anything afterwards. A flag
// you can neither defend nor call a mistake is a flag you have not looked at.

import { useState } from "react";
import type { ReplayFlag, ReviewItem, Verdict } from "../../lib/replayAccount";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

/** How far before the flagged moment the tape is put. A minute of market time,
 *  which is where the decision was being made — the fill itself is the last
 *  thing that happened, and the least informative. */
export const REVIEW_LEAD_MS = 60_000;

export function ReviewPanel({
  attemptId,
  flags,
  onSeek,
  onFile,
  filing,
  error,
}: {
  attemptId: string;
  flags: ReplayFlag[];
  /** Put the replay a minute before this flag, at 1x. */
  onSeek: (ms: number) => void;
  onFile: (items: ReviewItem[]) => void;
  filing: boolean;
  error: string | null;
}) {
  const [verdicts, setVerdicts] = useState<Record<number, Verdict>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [at, setAt] = useState<number | null>(null);

  const answered = flags.filter((_, i) => verdicts[i]).length;
  const complete = answered === flags.length;

  return (
    <div className="sim-card sim-review" data-review-panel>
      <div className="sim-sec-t" style={{ flex: "none" }}>
        Review
        <span className="r" data-review-progress>
          {answered}/{flags.length} answered
        </span>
      </div>
      <div style={{ fontSize: 11, color: palette.muted, lineHeight: 1.5, marginBottom: 8 }}>
        Read-only — nothing here places an order or writes to the sitting. Press a
        flag to put the tape a minute in front of it at 1×; the decision is in
        that minute, not in the fill.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, overflowY: "auto" }}>
        {flags.map((f, i) => {
          const v = verdicts[i];
          return (
            <div
              key={i}
              data-review-flag={i}
              style={{
                border: `1px solid ${v ? palette.cardBorder : palette.orange}`,
                borderRadius: 4,
                padding: 6,
              }}
            >
              <button
                type="button"
                onClick={() => {
                  setAt(i);
                  onSeek(Math.max(0, f.ms - REVIEW_LEAD_MS));
                }}
                style={{
                  display: "flex",
                  width: "100%",
                  gap: 6,
                  alignItems: "baseline",
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  textAlign: "left",
                  color: at === i ? palette.text : palette.muted,
                  fontSize: 12,
                }}
                title="Seek a minute before this and watch it again at 1×"
              >
                <span style={{ fontFamily: "monospace", color: f.pnl < 0 ? palette.red : palette.text }}>
                  {f.kind === "rewind" ? "↺" : fmtUsd(f.pnl)}
                </span>
                <span>{f.label}</span>
                <span style={{ marginLeft: "auto", fontSize: 10 }}>seek →</span>
              </button>
              <ul style={{ margin: "4px 0 0", paddingLeft: 16, fontSize: 11, color: palette.orange }}>
                {f.reasons.map((r, j) => (
                  <li key={j}>{r}</li>
                ))}
              </ul>
              <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                {(["leak", "justified"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    data-verdict={k}
                    onClick={() => setVerdicts((s) => ({ ...s, [i]: k }))}
                    style={{
                      flex: 1,
                      fontSize: 11,
                      padding: "3px 0",
                      cursor: "pointer",
                      background: v === k ? (k === "leak" ? palette.red : palette.green) : "transparent",
                      color: v === k ? "#0b0d12" : palette.muted,
                      border: `1px solid ${palette.cardBorder}`,
                      borderRadius: 3,
                    }}
                    title={
                      k === "leak"
                        ? "This cost money and would do it again. Naming it is the only thing that makes the next one visible."
                        : "The rule caught something that was actually the right trade. Worth saying — a rule that flags good trades is a rule to re-cut."
                    }
                  >
                    {k}
                  </button>
                ))}
              </div>
              {v && (
                <input
                  value={notes[i] ?? ""}
                  onChange={(e) => setNotes((s) => ({ ...s, [i]: e.target.value }))}
                  placeholder={v === "leak" ? "what was going on…" : "why it was right…"}
                  style={{ width: "100%", marginTop: 4, fontSize: 11 }}
                />
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <div style={{ color: palette.red, fontSize: 11, marginTop: 8, lineHeight: 1.5 }}>{error}</div>
      )}
      <button
        type="button"
        data-review-file
        disabled={!complete || filing}
        onClick={() =>
          onFile(
            flags.map((_, i) => ({
              flag_idx: i,
              verdict: verdicts[i] as Verdict,
              note: (notes[i] ?? "").trim(),
            })),
          )
        }
        style={{ width: "100%", marginTop: 8, padding: "6px 0", fontSize: 12, cursor: complete ? "pointer" : "default" }}
        title={
          complete
            ? "File the review — the next sitting opens once this lands"
            : "Every flag needs a verdict. The server checks this too; a partial review is a gate you clear by scrolling."
        }
      >
        {filing ? "Filing…" : complete ? "File review" : `${flags.length - answered} still to answer`}
      </button>
      <div style={{ fontSize: 10, color: palette.muted, marginTop: 4, opacity: 0.7 }}>{attemptId}</div>
    </div>
  );
}
