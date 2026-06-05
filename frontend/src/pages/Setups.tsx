import { useState } from "react";
import { TaxonomyManager } from "../components/TaxonomyManager";
import { useFilters } from "../hooks/useFilters";
import { useSetupStats } from "../hooks/useSetups";
import { useTaxonomyList } from "../hooks/useTaxonomy";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { ConfluenceStat, SetupStat } from "../lib/types";

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

function SetupCard({ p, description }: { p: SetupStat; description?: string }) {
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
    <div className="panel">
      <div className="section-title">
        <span className="badge">{p.name}</span>
      </div>
      {description && <div className="section-cap" style={{ marginBottom: 8 }}>{description}</div>}
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

export function Setups() {
  const { scope } = useFilters();
  const { data, isLoading } = useSetupStats(scope);
  const { data: catalog = [] } = useTaxonomyList("setups");
  const descByName = new Map(catalog.map((s) => [s.name, s.description]));

  const setups = data?.setups ?? [];

  return (
    <div>
      <div className="section-title">Setups</div>
      <div className="section-cap">
        Per-setup performance over the current filter scope. A trade can carry several
        setups, so it counts toward each — totals across setups may exceed your trade count.
      </div>
      <TaxonomyManager kind="setups" noun="setup" />
      {isLoading ? (
        <div className="notice">Loading…</div>
      ) : setups.length === 0 ? (
        <div className="notice">
          No setups tagged yet. Add setup badges to trades from their detail panel.
        </div>
      ) : (
        <div className="card-grid-2">
          {setups.map((p) => (
            <SetupCard key={p.name} p={p} description={descByName.get(p.name)} />
          ))}
        </div>
      )}
    </div>
  );
}
