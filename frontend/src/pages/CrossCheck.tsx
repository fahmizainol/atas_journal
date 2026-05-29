import { useState } from "react";
import { useStatistics, useStatisticsFiles } from "../hooks/useStatistics";
import { fmt, fmtPct } from "../lib/format";
import type { Metrics } from "../lib/types";

function oursRows(m: Metrics): [string, string][] {
  return [
    ["Total trades", String(m.trades)],
    ["Net PnL", fmt(m.net_pnl)],
    ["Win Rate", fmtPct(m.win_rate, 2)],
    ["Profit factor", fmt(m.profit_factor, false)],
    ["Profitable trades", String(m.wins)],
    ["Losing trades", String(m.losses)],
    ["Best Trade", fmt(m.best_trade)],
    ["Worst Trade", fmt(m.worst_trade)],
  ];
}

export function CrossCheck() {
  const { data: files } = useStatisticsFiles();
  const [file, setFile] = useState<string | null>(null);
  const selected = file ?? files?.files[0] ?? null;
  const { data } = useStatistics(selected);

  if (!files || files.files.length === 0)
    return <div className="notice">No Statistics sheets imported.</div>;

  return (
    <div>
      <div className="section-title">Our metrics vs ATAS Statistics sheet</div>
      <div className="field" style={{ maxWidth: 480, marginBottom: 12 }}>
        <label>Source file</label>
        <select value={selected ?? ""} onChange={(e) => setFile(e.target.value)}>
          {files.files.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>

      {data && (
        <>
          <div className="panel">
            <div className="section-cap">ATAS Statistics (as exported)</div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  {data.pivot.scopes.map((s) => (
                    <th key={s}>{s}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.pivot.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.metric}</td>
                    {data.pivot.scopes.map((s) => (
                      <td key={s}>{r[s] ?? "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <div className="section-cap">Our recomputed metrics (ATAS-rows view, this file)</div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Ours</th>
                </tr>
              </thead>
              <tbody>
                {oursRows(data.ours).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <div className="section-cap">Logical-vs-ATAS PnL reconciliation (all imported data)</div>
            <table className="data-table">
              <tbody>
                <tr>
                  <td>Logical net PnL</td>
                  <td>{fmt(data.reconcile.logical_net_pnl)}</td>
                </tr>
                <tr>
                  <td>ATAS journal PnL</td>
                  <td>{fmt(data.reconcile.atas_journal_pnl)}</td>
                </tr>
                <tr>
                  <td>Difference</td>
                  <td className={data.reconcile.difference === 0 ? "pos" : "neg"}>
                    {fmt(data.reconcile.difference)}
                  </td>
                </tr>
                <tr>
                  <td>Logical trades</td>
                  <td>{data.reconcile.logical_trades}</td>
                </tr>
                <tr>
                  <td>ATAS rows</td>
                  <td>{data.reconcile.atas_rows}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
