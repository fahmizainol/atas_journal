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
// trades, which is exactly why the account stays blown until it exists — and
// since 2026-08-24 it is the *whole* of what a funded death costs. There used to
// be a 24-hour timeout beside it; a timeout served in silence teaches the
// timeout rather than the lesson, so what is left is the lesson.

import { useState } from "react";
import type { AttemptRow } from "../../hooks/useReplays";
import { modeOfAccount, type AccountView } from "../../lib/replayAccount";
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
  rows,
  onWriteCause,
  writing,
  error,
  onReview,
}: {
  view: AccountView;
  /** Every attempt the history page knows about. Filtered to the epoch here. */
  rows: AttemptRow[];
  onWriteCause: (text: string) => void;
  writing: boolean;
  error: string | null;
  /** Open the death sitting in review mode. */
  onReview: ((attemptId: string) => void) | null;
}) {
  const [text, setText] = useState("");
  const death = view.last_death;
  const paper = view.account === "paper";

  // This account's own sittings, and only those. `rows` is every attempt the
  // history page knows about — funded, paper and drill together — so the epoch
  // window has to be closed on the mode as well as on the clock, exactly as
  // `replay_account.epoch_attempts` does server-side. Without it the curve here
  // would draw the other account's sittings into this one's death.
  const wantMode = modeOfAccount(view.account);
  const epoch = rows
    .filter(
      (a) =>
        (a.mode ?? "replay") === wantMode &&
        a.created_at >= view.epoch.started_at &&
        a.status !== "active",
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const totals = pool(epoch.map((a) => a.summary ?? {}));
  const points = epochCurve(epoch, 50_000);

  return (
    <div className="sim-card sim-autopsy" data-autopsy>
      <div className="sim-sec-t" style={{ flex: "none" }}>
        Autopsy
        <span className="r" style={{ color: palette.red }}>
          {paper ? "paper" : ""} account #{view.epoch.index + 1}
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

      {/* The one link out. The sitting that killed the account is the one thing
          worth looking at before writing the sentence — and the sentence written
          without looking is the one that says "bad luck".

          Offered on every death since 2026-08-25, where it used to be offered
          only while the sitting still *owed* a review. Reviewing is optional
          now, so gating the link on a debt would have hidden it exactly when
          the review had been skipped — which is the case it is most for. */}
      {onReview && death && (
        <button
          type="button"
          data-autopsy-review
          onClick={() => onReview(death.attempt_id)}
          style={{ width: "100%", marginTop: 8, padding: "5px 0", fontSize: 12, cursor: "pointer" }}
        >
          Watch it again — the sitting that killed it
        </button>
      )}

      {/* Paper never asks for the sentence. It is not that the death does not
          matter — the curve above is the same curve — it is that nothing is
          waiting on the write-up: the account is already resettable, so a form
          here would be a form with no gate behind it, and a gate you can walk
          around teaches you to walk around gates. Since the review stopped
          being mandatory (2026-08-25) a paper death costs nothing at all — the
          link above is the whole of what it offers, and taking it is a choice. */}
      {paper ? (
        <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.6 }}>
          <div style={{ color: palette.green }}>
            The next paper sitting opens a fresh $50,000 — same rules, same floor, nothing owed.
          </div>
        </div>
      ) : view.status === "blown" ? (
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
            title="This is the whole of what a blown account costs — nothing else is waiting on it."
          >
            {writing ? "Recording…" : "Record the cause"}
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.6 }}>
          <div style={{ color: palette.muted, fontSize: 11 }}>Cause of death</div>
          <div>{death?.cause_of_death}</div>
          <div style={{ marginTop: 6, color: palette.green }}>
            The next sitting opens a fresh $50,000 account, with that sentence pinned to it.
          </div>
        </div>
      )}
    </div>
  );
}
