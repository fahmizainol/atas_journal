import { useState } from "react";
import { useFilters } from "../hooks/useFilters";
import { useConfluenceStats } from "../hooks/useConfluences";
import { fmt, fmtInt, fmtPct, numValue } from "../lib/format";
import type { Num } from "../lib/format";
import type { ConfluenceLeaderStat, ConfluenceStat, StackBucket } from "../lib/types";

function Pnl({ v }: { v: number }) {
  return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
}

// Signed delta with explicit +/- and red/green, for the lift cells.
function Delta({ v, kind }: { v: Num; kind: "pp" | "money" }) {
  const n = numValue(v);
  const cls = !Number.isFinite(n) ? "" : n >= 0 ? "pos" : "neg";
  const sign = Number.isFinite(n) && n >= 0 ? "+" : "";
  const body = kind === "pp" ? `${n.toFixed(1)}pp` : fmt(v);
  return <span className={cls}>{sign}{body}</span>;
}

// Setup breakdown nested in each confluence card (inverse of the Setups table).
function SetupTable({ rows }: { rows: ConfluenceStat[] }) {
  if (rows.length === 0)
    return <div className="section-cap">No setups tagged on these trades yet.</div>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Setup</th>
          <th>Trades</th>
          <th>Win rate</th>
          <th>Net PnL</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.name}>
            <td><span className="badge badge-sm">{p.name}</span></td>
            <td>{fmtInt(p.trades)}</td>
            <td>{fmtPct(p.win_rate)}</td>
            <td><Pnl v={p.net_pnl} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ConfluenceCard({ c }: { c: ConfluenceLeaderStat }) {
  const [open, setOpen] = useState(false);
  const m = c.metrics;
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
    ["Δ win rate vs rest", <Delta v={c.lift.win_rate_delta} kind="pp" />],
    ["Δ expectancy vs rest", <Delta v={c.lift.expectancy_delta} kind="money" />],
  ];
  return (
    <div className="panel">
      <div className="section-title">
        <span className="badge">{c.name}</span>
      </div>
      <div className="kpi-grid kpi-compact" style={{ gridTemplateColumns: "repeat(6, 1fr)" }}>
        {cells.map(([label, value]) => (
          <div key={label} className="kpi-card">
            <div className="kpi-label">{label}</div>
            <div className="kpi-value">{value}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <button type="button" className={open ? "active" : ""} onClick={() => setOpen((o) => !o)}>
          {open ? "▾ Hide setups" : `▸ Setups (${c.setups.length})`}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <SetupTable rows={c.setups} />
        </div>
      )}
    </div>
  );
}

function StackingTable({ rows }: { rows: StackBucket[] }) {
  if (rows.length === 0)
    return <div className="section-cap">No trades carry a confluence yet.</div>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Confluences present</th>
          <th>Trades</th>
          <th>Win rate</th>
          <th>Expectancy</th>
          <th>Net PnL</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => (
          <tr key={b.count}>
            <td>{b.label}</td>
            <td>{fmtInt(b.trades)}</td>
            <td>{fmtPct(b.win_rate)}</td>
            <td>{fmt(b.expectancy)}</td>
            <td><Pnl v={b.net_pnl} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Confluences() {
  const { scope } = useFilters();
  const { data, isLoading } = useConfluenceStats(scope);

  if (isLoading) return <div className="notice">Loading…</div>;
  const confluences = data?.confluences ?? [];
  const stacking = data?.stacking ?? [];

  return (
    <div>
      <div className="section-title">Confluences</div>
      <div className="section-cap">
        Per-confluence performance over the current filter scope. A trade can carry several
        confluences, so it counts toward each — totals may exceed your trade count. "Δ vs rest"
        compares trades that have a confluence against those that don't, so you can read its win
        rate against your baseline rather than in a vacuum.
      </div>
      {confluences.length === 0 ? (
        <div className="notice">
          No confluences tagged yet. Add confluence badges to trades from their detail panel.
        </div>
      ) : (
        <>
          <div className="card-grid-2">
            {confluences.map((c) => <ConfluenceCard key={c.name} c={c} />)}
          </div>
          <div className="section-title" style={{ marginTop: 20 }}>Stacking</div>
          <div className="section-cap">
            Outcomes bucketed by how many confluences were present on the trade — does stacking
            reads actually improve results, or are you confirmation-biasing into trades?
          </div>
          <div className="panel">
            <StackingTable rows={stacking} />
          </div>
        </>
      )}
    </div>
  );
}
