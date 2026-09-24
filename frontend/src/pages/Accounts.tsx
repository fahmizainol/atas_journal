// Every practice account, and what its reps came to.
//
// The registry has been real since accounts stopped being two hardcoded rows,
// but the only place any of it surfaced was a popover on the chart and a chip.
// The record — how many lives an account has had and how each one ended — was
// already walked server-side and shown nowhere.
//
// Scope is the practice ledgers. Nothing live is modelled here: a drill belongs
// to no account by design (backtest reps are unpriced), and the reps that
// predate the registry sit outside every epoch on purpose. Both are still
// listed on `/charts/replay/history`, which is the flat record of every sitting.

import { Link } from "react-router-dom";
import { useState } from "react";
import { AccountManager } from "../components/accounts/AccountManager";
import { useReplayAccounts, type AccountRow } from "../hooks/useReplayAccounts";
import { pathOfAccount } from "../components/charts/AccountSwitch";
import { palette } from "../theme";

const money = (n: number) => `$${Math.round(n).toLocaleString()}`;

/** How a life ended, in the colour it ended in. */
export function outcomeTone(outcome: string): string {
  if (outcome === "passed") return palette.green;
  if (outcome === "blown") return palette.red;
  return palette.muted;
}

function Record({ record }: { record: { passed: number; blown: number } }) {
  if (!record.passed && !record.blown) return <span style={{ color: palette.muted }}>—</span>;
  return (
    <>
      {record.passed > 0 && (
        <span style={{ color: palette.green }}>{record.passed} passed</span>
      )}
      {record.passed > 0 && record.blown > 0 && <span style={{ color: palette.muted }}> · </span>}
      {record.blown > 0 && <span style={{ color: palette.red }}>{record.blown} blown</span>}
    </>
  );
}

export function Accounts() {
  const q = useReplayAccounts();
  const [managing, setManaging] = useState(false);
  const rows: AccountRow[] = q.data?.accounts ?? [];
  const live = rows.filter((a) => !a.archived);
  const retired = rows.filter((a) => a.archived);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Accounts</h1>
        <p className="page-sub">
          The practice ledgers. Each one is a separate walk over the sittings stamped with
          it — none of them can move another, and a backtest rep is priced by none.
        </p>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <button
          type="button"
          className="btn"
          onClick={() => setManaging((v) => !v)}
          data-accounts-manage
        >
          {managing ? "close" : "New account / edit numbers…"}
        </button>
        <Link to="/charts/replay/history" style={{ color: palette.muted, fontSize: 12 }}>
          Every sitting, including drills →
        </Link>
      </div>
      {managing && (
        <AccountManager
          accounts={rows}
          templates={q.data?.templates ?? []}
          current={live[0]?.id ?? ""}
          onDone={() => setManaging(false)}
        />
      )}

      {q.isLoading && <div style={{ color: palette.muted }}>loading…</div>}

      <AccountTable rows={live} />
      {retired.length > 0 && (
        <>
          <h2 style={{ marginTop: 18, fontSize: 13, color: palette.muted }}>Archived</h2>
          <p style={{ color: palette.muted, fontSize: 11, marginTop: 0 }}>
            Not something you pick into any more. Still prices every sitting it took, which
            is why an account that has traded is archived rather than deleted.
          </p>
          <AccountTable rows={retired} />
        </>
      )}
    </div>
  );
}

function AccountTable({ rows }: { rows: AccountRow[] }) {
  if (!rows.length) return null;
  return (
    <table className="data-table" data-accounts-table style={{ width: "100%" }}>
      <thead>
        <tr>
          <th>Account</th>
          <th>Rules</th>
          <th style={{ textAlign: "right" }}>Start</th>
          <th style={{ textAlign: "right" }}>Max loss</th>
          <th style={{ textAlign: "right" }}>Target</th>
          <th style={{ textAlign: "right" }}>Lives</th>
          <th>Record</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.id}>
            <td>
              <Link to={`/accounts/${encodeURIComponent(a.id)}`} data-account-link={a.id}>
                {a.id === "paper" ? "📝 " : ""}
                {a.label}
              </Link>
            </td>
            <td
              style={{ color: palette.muted }}
              title={
                a.trailing === "intraday"
                  ? "The floor follows running equity, including an open position — it moves under the position moving it."
                  : "The floor follows day closes only, so it is a constant for a whole sitting."
              }
            >
              {a.trailing === "intraday" ? "intraday trail" : "eod trail"}
              {!a.needs_cause && " · free reset"}
            </td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(a.numbers.start)}</td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(a.numbers.max_loss)}</td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>
              {money(a.numbers.profit_target)}
            </td>
            <td style={{ textAlign: "right" }}>{a.epochs || "—"}</td>
            <td>
              <Record record={a.record} />
            </td>
            <td>
              <Link to={pathOfAccount(a.id)} style={{ color: palette.muted, fontSize: 11 }}>
                trade →
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
