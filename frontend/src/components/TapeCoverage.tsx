// What the live store actually holds, and how long the holes stay fillable.
//
// Named for the tape rather than "recordings" because this repo already has a
// `RecordingsCard` and it is about screen-recorded video on the Calendar page.
// These are recorded *sessions* — `data/live/ticks/`, written by the Rithmic
// feed and by the harvest sweep behind it.
//
// THE ONE DEADLINE. Rithmic replays a *listed* contract back about 120 days and
// an **expired** one not at all, at any depth. So there are two ceilings and only
// one of them moves: the 120-day floor slides forward every day, and the
// contract's expiry is a cliff behind which nothing is recoverable. Whatever is
// still missing when NQU6 rolls is missing permanently — which is a date the
// server can compute, so it is shown here rather than left to be remembered.
//
// WATCHED IS NOT HARVESTED, AND THE PANEL SAYS WHICH. A harvested day has no
// signal journal (nothing recorded what the shelf believed, and nothing can
// reconstruct it) and carries Rithmic's clock rather than the exchange's — a
// median 287µs later, which moves no bar but is a systematic offset against
// Databento's `ts_event`. Neither is a defect; both are things a reader of the
// data has to be able to find out without asking a person.
//
// NOT BACKTEST DATA. Nothing in this list is replayable on the Simulator. The
// two stores are disjoint by decision (docs/live-shadow-plan.md decisions 3-4)
// so that the Databento corpus stays an independent reference to check a
// recorded day *against*.

import { useLiveRecordings } from "../hooks/useLive";
import type { LiveContract, LiveRecording } from "../lib/liveTypes";
import { palette } from "../theme";

/** How a day was come by, as a colour and a word. The server decides `kind`
 *  (api/routers/live.py `_kind_of`) — this only has to render it. */
const KINDS = {
  watched: { color: palette.green, label: "watched", hint: "The shelf ran over it live and journalled what it believed." },
  filled: { color: palette.blue, label: "watched · filled", hint: "Watched live, then gap-filled by the sweep. The filled stretches carry Rithmic's clock." },
  harvest: { color: palette.violet, label: "harvested", hint: "Replayed off the history plant after the fact. No signal journal, and Rithmic's clock throughout." },
  unknown: { color: palette.muted, label: "unrecorded provenance", hint: "Nothing on disk says how this day was come by — it predates the manifest carrying the mark through a gap-fill." },
} as const;

const fmtRows = (n: number | null): string => {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${Math.round(n / 1000)}k`;
  return String(n);
};

/** Urgency of the roll, as a colour. Nothing below 30 days is comfortable: a
 *  deep harvest of a quarter is hours of replay on one connection. */
function deadlineColor(days: number | null): string {
  if (days == null) return palette.muted;
  if (days <= 7) return palette.red;
  if (days <= 30) return palette.orange;
  return palette.muted;
}

export function TapeCoverage({ symbol, compact }: { symbol?: string; compact?: boolean }) {
  const q = useLiveRecordings(symbol);
  const recordings = q.data?.recordings ?? [];
  const contracts = q.data?.contracts ?? [];

  if (q.isLoading) {
    return <div style={{ fontSize: 12, color: palette.muted }}>Reading the live store…</div>;
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>Tape coverage</h3>
          <span style={{ fontSize: 11, color: palette.muted }}>
            {recordings.length} recorded session{recordings.length === 1 ? "" : "s"}
          </span>
        </div>
        <p style={{ fontSize: 11, color: palette.muted, margin: "4px 0 0" }}>
          The live store — recorded off Rithmic, kept apart from the Databento
          cache the Simulator replays. Nothing here is backtest data.
        </p>
      </div>

      {contracts.map((c) => (
        <ContractDeadline key={c.symbol} c={c} recordings={recordings} />
      ))}

      {recordings.length > 0 && <DayList rows={recordings} compact={compact} />}
      {recordings.length === 0 && (
        <div style={{ fontSize: 12, color: palette.muted }}>
          Nothing recorded yet. A Rithmic session writes as it runs, and fills in
          the days behind it on the same connection.
        </div>
      )}
    </div>
  );
}

/**
 * One contract's window: the rolling floor, the cliff, and a strip of the days.
 *
 * The strip is drawn over the *reachable* window rather than over what exists,
 * because the holes are the point — a list of what was recorded cannot show you
 * the fortnight nobody was connected for.
 */
function ContractDeadline({ c, recordings }: { c: LiveContract; recordings: LiveRecording[] }) {
  const byDate = new Map(recordings.filter((r) => r.symbol === c.symbol).map((r) => [r.date, r]));
  // Merged rather than recomputed: the weekday/holiday reasoning behind which
  // dates are sessions at all lives in `harvest.sessions_between`, and a second
  // copy of it here would be a calendar to keep in step.
  const days = [...new Set([...c.missing_dates, ...byDate.keys()])].sort();
  const urgent = deadlineColor(c.days_to_expiry);

  return (
    <div
      style={{
        border: `1px solid ${palette.cardBorder}`,
        borderRadius: 4,
        padding: "8px 10px",
        display: "grid",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{c.symbol}</strong>
        {c.expiry ? (
          <span style={{ fontSize: 11, color: urgent }}>
            expires {c.expiry} · {c.days_to_expiry} day
            {c.days_to_expiry === 1 ? "" : "s"} left
          </span>
        ) : (
          // A whitelist of roots settle on the third Friday; the rest do not, and
          // a plausible wrong date on a deadline nobody can re-check afterwards
          // is worse than none.
          <span style={{ fontSize: 11, color: palette.muted }}>
            expiry unknown for this root
          </span>
        )}
      </div>

      <div style={{ fontSize: 11, color: palette.muted, lineHeight: 1.5 }}>
        Reachable back to <b style={{ color: palette.text }}>{c.floor}</b> ({c.replay_days}
        -day replay window). <b style={{ color: palette.text }}>{c.recorded}</b> of{" "}
        {c.sessions} sessions recorded
        {c.missing > 0 && (
          <>
            {" "}
            —{" "}
            <span
              style={{ color: urgent, cursor: "help" }}
              title={
                "Weekday sessions with nothing at all in the store. A few of these are " +
                "holidays: for a pinned raw contract the exchange calendar cannot be " +
                "resolved, so a day the market was shut looks exactly like a day nobody " +
                "recorded. The sweep answers that by fetching and flagging, not by predicting."
              }
            >
              {c.missing} with nothing
            </span>
            {c.oldest_missing ? `, oldest ${c.oldest_missing}` : ""}
          </>
        )}
        .
      </div>

      {c.missing > 0 && c.expiry && (
        <div style={{ fontSize: 11, color: urgent, lineHeight: 1.5 }}>
          ⚠ Deep-harvest {c.symbol} before it rolls. An expired contract replays
          nothing at any depth, so these {c.missing} sessions become unrecoverable
          on {c.expiry} — earlier for any that ages past the {c.replay_days}-day
          floor first.
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
        {days.map((d) => {
          const r = byDate.get(d);
          const k = r ? KINDS[r.kind] ?? KINDS.unknown : null;
          return (
            <span
              key={d}
              title={
                r
                  ? `${d} · ${k!.label} · ${fmtRows(r.rows)} ticks, ${r.chunks} chunks\n${k!.hint}`
                  : `${d} · nothing recorded — still fetchable until ${c.expiry ?? "the contract rolls"} (or until it ages past ${c.floor}), unless the exchange was shut that day`
              }
              style={{
                width: 8,
                height: 14,
                borderRadius: 1,
                cursor: "help",
                background: r ? k!.color : "transparent",
                border: r ? "none" : `1px solid ${palette.grid}`,
                boxSizing: "border-box",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

/** The recorded days themselves, newest first — one line each, so the panel is
 *  readable in a rail column as well as on the setup page. */
function DayList({ rows, compact }: { rows: LiveRecording[]; compact?: boolean }) {
  return (
    <div style={{ display: "grid", gap: 2, maxHeight: compact ? 260 : 420, overflowY: "auto" }}>
      {rows.map((r) => {
        const k = KINDS[r.kind] ?? KINDS.unknown;
        // Three things can make a day less than whole, and they are different
        // claims: the sweep gave up part-way (`harvest.complete` false), the
        // session never reached its close, or prints reached the tape while
        // nothing was recording — the last of which is a permanent hole and is
        // the only one that cannot be repaired by fetching again.
        const partial = r.harvest ? !r.harvest.complete : r.closed === false;
        return (
          <div
            key={`${r.symbol}-${r.date}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 11,
              padding: "3px 0",
              borderBottom: `1px solid ${palette.bg2}`,
            }}
          >
            <span
              title={k.hint}
              style={{
                width: 6,
                height: 6,
                borderRadius: 3,
                background: k.color,
                flex: "0 0 auto",
                cursor: "help",
              }}
            />
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{r.date}</span>
            <span style={{ color: palette.muted, flex: 1, minWidth: 0 }} title={k.hint}>
              {k.label}
              {r.signals.length > 0 && (
                <span title={`Signal journals: ${r.signals.join(", ")}`}>
                  {" "}
                  · {r.signals.length} journalled
                </span>
              )}
              {r.shadow === "off" && (
                <span title="The shelf was switched off for this session, so the absence of signals is a decision rather than a quiet market.">
                  {" "}
                  · shelf off
                </span>
              )}
            </span>
            <span
              style={{ color: palette.muted, fontVariantNumeric: "tabular-nums" }}
              title={`${(r.rows ?? 0).toLocaleString()} ticks across ${r.chunks} chunks`}
            >
              {fmtRows(r.rows)}
            </span>
            {partial && (
              <span
                style={{ color: palette.orange }}
                title={
                  r.harvest?.error
                    ? `The sweep stopped on this day: ${r.harvest.error}. Fetchable again while the contract is listed.`
                    : "Not whole — the sweep did not reach the end of the day, or the session never closed. Still fetchable while the contract is listed."
                }
              >
                partial
              </span>
            )}
            {r.unrecorded_rows > 0 && (
              <span
                style={{ color: palette.orange }}
                title={`${r.unrecorded_rows.toLocaleString()} ticks reached the tape while nothing was recording. A permanent hole — the tape was in memory and the chunks are what survived.`}
              >
                ⚠ hole
              </span>
            )}
            {r.clamped > 0 && (
              <span
                style={{ color: palette.gold, cursor: "help" }}
                title={`${r.clamped.toLocaleString()} exchange stamps arrived out of order and were pushed forward to keep the tape monotonic. Tiny is ordinary; a large figure is a finding about the feed.`}
              >
                {r.clamped.toLocaleString()} clamped
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
