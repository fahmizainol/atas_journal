// Backtest mode's review: the rep is over, here is what you took, which of the
// model's rules did each one meet?
//
// Deliberately **not forced**. 🎲 stays live whether this is filled in or not,
// and skipping costs the data point and nothing else — `_compliance_split` puts
// a trade with no recorded checks in `unscored` rather than `broke`, "because
// counting it as broke would slander it". That is what makes an optional review
// safe: an unanswered rep does not quietly become evidence against the model.
//
// It is also the one place worth being honest about what this data is. The
// boxes are ticked *after* the P&L is on screen, so what they measure is partly
// how you remember the trade. It is a training aid and a memory prompt; it is
// not evidence, and pooling it into anything that gets A/B'd would be pooling
// hindsight. The panel says so once, at the bottom, where it cannot be missed
// and does not nag.

import { useEffect, useState } from "react";
import type { ModelRule } from "../../lib/types";
import { fmtUsd } from "../../lib/simViews";
import { palette } from "../../theme";

export interface DrillTrade {
  trade_key: string;
  direction: string | null;
  entry_ts_local: string;
  net_pnl: number;
  model_id: number | null;
  rules_met: number[];
  /** Whether this trade has any rule rows at all. Distinct from "met nothing":
   *  no rows is unscored, which is what an un-reviewed trade should stay. */
  reviewed: boolean;
}

/** The clock off an ISO-ish local stamp, without parsing it as a date. The
 *  string is already in the display zone (`entry_ts_local`), and re-reading it
 *  through Date() would project it a second time. */
const clockOf = (ts: string) => (ts.includes(" ") ? ts.split(" ")[1] : ts).slice(0, 5);

export function DrillReview({
  modelName,
  rules,
  trades,
  saving,
  error,
  onSave,
  onDraw,
  drawBlocked,
}: {
  modelName: string;
  rules: ModelRule[];
  trades: DrillTrade[];
  saving: boolean;
  error: string | null;
  onSave: (tradeKey: string, rulesMet: number[]) => void;
  onDraw: () => void;
  drawBlocked: string | null;
}) {
  // Seeded from what is stored, so a rep reviewed on an earlier visit opens
  // showing its answers rather than blank. Keyed by trade so re-fetching the
  // list mid-edit cannot drop what you have ticked.
  const [met, setMet] = useState<Record<string, number[]>>({});
  useEffect(() => {
    setMet((m) => {
      const next = { ...m };
      for (const t of trades) if (!(t.trade_key in next)) next[t.trade_key] = t.rules_met;
      return next;
    });
  }, [trades]);

  const toggle = (key: string, rid: number) =>
    setMet((m) => {
      const cur = m[key] ?? [];
      return { ...m, [key]: cur.includes(rid) ? cur.filter((r) => r !== rid) : [...cur, rid] };
    });

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
      ) : rules.length === 0 ? (
        <div style={{ fontSize: 12, color: palette.muted, lineHeight: 1.5 }}>
          {modelName} declares no rules, so there is nothing to score these{" "}
          {trades.length} trade{trades.length === 1 ? "" : "s"} against. Add them
          on the Models page and they will appear here next rep.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {trades.map((t) => {
            const mine = met[t.trade_key] ?? [];
            const dirty =
              mine.length !== t.rules_met.length ||
              mine.some((r) => !t.rules_met.includes(r));
            return (
              <div key={t.trade_key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 12 }}>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {clockOf(t.entry_ts_local)}
                  </span>
                  <span style={{ color: palette.muted }}>{t.direction ?? "—"}</span>
                  <span className={t.net_pnl >= 0 ? "pos" : "neg"}>{fmtUsd(t.net_pnl)}</span>
                  {t.reviewed && !dirty && (
                    <span style={{ color: palette.muted, fontSize: 11 }}>saved</span>
                  )}
                </div>
                {rules.map((r) => (
                  <label
                    key={r.id}
                    style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}
                  >
                    <input
                      type="checkbox"
                      checked={mine.includes(r.id)}
                      onChange={() => toggle(t.trade_key, r.id)}
                      style={{ margin: 0 }}
                    />
                    {r.label}
                  </label>
                ))}
                <button
                  type="button"
                  className="chart-topbar-btn"
                  style={{ alignSelf: "start" }}
                  onClick={() => onSave(t.trade_key, mine)}
                  disabled={saving || !dirty}
                >
                  {dirty ? "Save" : "Saved"}
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

      {trades.length > 0 && rules.length > 0 && (
        <div style={{ fontSize: 11, color: palette.muted, marginTop: 8, lineHeight: 1.5 }}>
          Ticked after the result is on screen, so this is a memory prompt rather
          than evidence. Don't A/B on it.
        </div>
      )}
    </div>
  );
}
