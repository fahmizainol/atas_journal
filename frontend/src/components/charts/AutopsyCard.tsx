// What killed the account, and the form that says so.
//
// It is composed entirely on the client, out of things the page already has:
// `GET /replays` inlines each attempt's summary, so the epoch's equity curve is
// a cumulative sum in `created_at` order and its totals are `replayStats.pool`
// over the same rows. Nothing new is stored and nothing is recomputed — the
// server would only be re-deriving numbers the history page already draws.
//
// The one thing that is written is the sentence. It is the only piece of state
// in this whole feature authored by a person rather than derived from the
// trades, which is exactly why the account stays blown until it exists: the
// 24-hour timeout runs from the death either way, so writing it up promptly
// costs nothing, and a timeout served in silence teaches the timeout rather
// than the lesson.

import { useState } from "react";
import type { AttemptRow } from "../../hooks/useReplays";
import { fmtWait, remainingMs, type AccountView } from "../../lib/replayAccount";
import { pool } from "../../lib/replayStats";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

/** Cumulative account equity at the close of each sitting in the epoch, oldest
 *  first — the shape of the thing that died. */
export function epochCurve(rows: AttemptRow[], start: number): { id: string; equity: number }[] {
  let run = start;
  return rows.map((a) => {
    run += a.summary?.net_usd ?? 0;
    return { id: a.id, equity: run };
  });
}

/** The curve as a row of bars, drawn against the floor it eventually reached.
 *
 *  Not a chart library and deliberately so: it is eight or twelve numbers and
 *  the only question being asked of them is "was this one long slide or one bad
 *  afternoon", which a row of bars answers and an axis does not improve. */
function Curve({ points, floor, start }: { points: { id: string; equity: number }[]; floor: number; start: number }) {
  if (!points.length) return null;
  const lo = Math.min(floor, ...points.map((p) => p.equity));
  const hi = Math.max(start, ...points.map((p) => p.equity));
  const span = Math.max(1, hi - lo);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 44, marginTop: 6 }} data-epoch-curve>
      {points.map((p, i) => (
        <div
          key={p.id}
          title={`sitting ${i + 1} closed at ${fmtUsd(p.equity)}`}
          style={{
            flex: 1,
            minWidth: 3,
            height: `${Math.max(3, ((p.equity - lo) / span) * 100)}%`,
            background:
              p.equity <= floor ? palette.red : p.equity >= start ? palette.green : palette.orange,
          }}
        />
      ))}
    </div>
  );
}

export function AutopsyCard({
  view,
  receivedAt,
  rows,
  onWriteCause,
  writing,
  error,
  onReview,
}: {
  view: AccountView;
  receivedAt: number;
  /** Every attempt the history page knows about. Filtered to the epoch here. */
  rows: AttemptRow[];
  onWriteCause: (text: string) => void;
  writing: boolean;
  error: string | null;
  /** Open the death sitting in review mode, if it still owes one. */
  onReview: ((attemptId: string) => void) | null;
}) {
  const [text, setText] = useState("");
  const cool = remainingMs(view, view.cooldown_until, receivedAt);
  const death = view.last_death;

  const epoch = rows
    .filter((a) => a.created_at >= view.epoch.started_at && a.status !== "active")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const totals = pool(epoch.map((a) => a.summary ?? {}));
  const points = epochCurve(epoch, 50_000);

  return (
    <div className="sim-card sim-autopsy" data-autopsy>
      <div className="sim-sec-t" style={{ flex: "none" }}>
        Autopsy
        <span className="r" style={{ color: palette.red }}>
          account #{view.epoch.index + 1}
        </span>
      </div>

      {death && (
        <div style={{ fontSize: 12, lineHeight: 1.6 }}>
          It closed at{" "}
          <b style={{ fontFamily: "monospace", color: palette.red }}>{fmtUsd(death.equity)}</b> against a{" "}
          <b style={{ fontFamily: "monospace" }}>{fmtUsd(death.floor)}</b> floor, on the sitting of{" "}
          {death.at.slice(0, 10)}.
        </div>
      )}

      <Curve points={points} floor={view.floor} start={50_000} />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 8,
          marginTop: 8,
          fontSize: 11,
          color: palette.muted,
        }}
      >
        <div>
          sittings
          <div style={{ fontFamily: "monospace", fontSize: 14, color: palette.text }}>{epoch.length}</div>
        </div>
        <div>
          trades
          <div style={{ fontFamily: "monospace", fontSize: 14, color: palette.text }}>{totals.trades}</div>
        </div>
        <div>
          worst
          <div style={{ fontFamily: "monospace", fontSize: 14, color: palette.red }}>
            {fmtUsd(totals.worst_usd ?? 0)}
          </div>
        </div>
      </div>

      {/* The one link out. A death sitting that still owes a review is the one
          thing worth looking at before writing the sentence — and the sentence
          written without looking is the one that says "bad luck". */}
      {onReview && view.review_block && death && (
        <button
          type="button"
          data-autopsy-review
          onClick={() => onReview(view.review_block!.attempt_id)}
          style={{ width: "100%", marginTop: 8, padding: "5px 0", fontSize: 12, cursor: "pointer" }}
        >
          Watch it again — {view.review_block.flags.length} flag
          {view.review_block.flags.length === 1 ? "" : "s"} to answer
        </button>
      )}

      {view.status === "blown" ? (
        <div style={{ marginTop: 10 }}>
          <label style={{ fontSize: 11, color: palette.muted }} htmlFor="autopsy-cause">
            Cause of death — one sentence, in your own words. It gets pinned to the
            next account for the whole of its life.
          </label>
          <textarea
            id="autopsy-cause"
            data-autopsy-cause
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder="e.g. sized up to get back to flat after two stops"
            style={{ width: "100%", marginTop: 4, fontSize: 12, resize: "vertical" }}
          />
          {error && <div style={{ color: palette.red, fontSize: 11, marginTop: 4 }}>{error}</div>}
          <button
            type="button"
            data-autopsy-file
            disabled={!text.trim() || writing}
            onClick={() => onWriteCause(text.trim())}
            style={{ width: "100%", marginTop: 6, padding: "6px 0", fontSize: 12 }}
            title="The 24h timeout runs from the death either way — writing this now costs you nothing."
          >
            {writing ? "Recording…" : "Record the cause"}
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.6 }}>
          <div style={{ color: palette.muted, fontSize: 11 }}>Cause of death</div>
          <div>{death?.cause_of_death}</div>
          <div style={{ marginTop: 6, color: view.can_reset ? palette.green : palette.orange }}>
            {view.can_reset
              ? "The timeout is up. The next sitting opens a fresh $50,000 account, with that sentence pinned to it."
              : `${fmtWait(cool)} before a new account can be opened.`}
          </div>
        </div>
      )}
    </div>
  );
}
