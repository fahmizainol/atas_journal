import { useState } from "react";
import { useFilters } from "../hooks/useFilters";
import { usePlaybookStats } from "../hooks/usePlaybook";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { ConfluenceStat, PlaybookStat } from "../lib/types";

function Pnl({ v }: { v: number }) {
  return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
}

function ConfluenceTable({ rows }: { rows: ConfluenceStat[] }) {
  if (rows.length === 0)
    return <div className="section-cap">No confluences tagged on these trades yet.</div>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Confluence</th>
          <th>Trades</th>
          <th>Win rate</th>
          <th>Net PnL</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.name}>
            <td><span className="badge badge-sm">{c.name}</span></td>
            <td>{fmtInt(c.trades)}</td>
            <td>{fmtPct(c.win_rate)}</td>
            <td><Pnl v={c.net_pnl} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PlaybookCard({ p }: { p: PlaybookStat }) {
  const [open, setOpen] = useState(false);
  const m = p.metrics;
  const cells: [string, React.ReactNode][] = [
    ["Trades", fmtInt(m.trades)],
    ["Win rate", fmtPct(m.win_rate)],
    ["Net PnL", <Pnl v={m.net_pnl as number} />],
    ["Profit factor", fmt(m.profit_factor, false)],
    ["Expectancy", fmt(m.expectancy)],
    ["Avg win", fmt(m.avg_win)],
    ["Avg loss", fmt(m.avg_loss)],
    ["Best", fmt(m.best_trade)],
    ["Worst", fmt(m.worst_trade)],
    ["Avg hold", `${(((m.avg_trade_length_s as number) ?? 0) / 60).toFixed(1)}m`],
  ];
  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <div className="section-title">
        <span className="badge">{p.name}</span>
      </div>
      <div className="kpi-grid kpi-compact" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        {cells.map(([label, value]) => (
          <div key={label} className="kpi-card">
            <div className="kpi-label">{label}</div>
            <div className="kpi-value">{value}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <button type="button" className={open ? "active" : ""} onClick={() => setOpen((o) => !o)}>
          {open ? "▾ Hide confluences" : `▸ Confluences (${p.confluences.length})`}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <ConfluenceTable rows={p.confluences} />
        </div>
      )}
    </div>
  );
}

export function Playbook() {
  const { scope } = useFilters();
  const { data, isLoading } = usePlaybookStats(scope);

  if (isLoading) return <div className="notice">Loading…</div>;
  const playbooks = data?.playbooks ?? [];

  return (
    <div>
      <div className="section-title">Playbook</div>
      <div className="section-cap">
        Per-playbook performance over the current filter scope. A trade can carry several
        playbooks, so it counts toward each — totals across playbooks may exceed your trade count.
      </div>
      {playbooks.length === 0 ? (
        <div className="notice">
          No playbooks tagged yet. Add playbook badges to trades from their detail panel.
        </div>
      ) : (
        playbooks.map((p) => <PlaybookCard key={p.name} p={p} />)
      )}
    </div>
  );
}
