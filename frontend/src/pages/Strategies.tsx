import { Link } from "react-router-dom";
import { useStrategyList } from "../hooks/useStrategies";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { StrategySummary } from "../lib/strategyTypes";
import { strategyExplainers } from "../lib/strategyExplainers";

const tone = (n: number | undefined) => ((n ?? 0) >= 0 ? "pos" : "neg");

// Baseline KPIs inline on the card: enough to rank ideas at a glance without
// opening each one. Full stats live on the detail page.
function BaselineStats({ s }: { s: StrategySummary }) {
  const m = s.baseline_metrics;
  if (!m) return <span className="muted">No baseline yet — run the first sim.</span>;
  return (
    <span>
      <span className={tone(m.net_pnl)}>{fmt(m.net_pnl)}</span>
      {" · "}
      {fmtInt(m.trades)} trades · {fmtPct(m.win_rate, 0)} win · PF {fmt(m.profit_factor, false)}
      {" · "}
      <span className={tone(m.r_mean)}>{fmt(m.r_mean, false)}R mean</span>
    </span>
  );
}

export function Strategies() {
  const { data, isLoading } = useStrategyList();

  if (isLoading) return <div className="notice">Loading…</div>;
  const strategies = data?.strategies ?? [];

  return (
    <div className="page">
      <h2 className="section-title">Strategies</h2>
      <div className="section-cap" style={{ marginBottom: 16 }}>
        Coded trading ideas simulated tick-by-tick over Databento data. Each strategy keeps an
        immutable run per config; pin your best run as the baseline and tweak against it. Runs
        live on disk under <code>data/sims/</code> and never touch the journal.
      </div>

      {strategies.map((s) => (
        <Link
          key={s.slug}
          to={`/strategies/${s.slug}`}
          style={{ textDecoration: "none", color: "inherit", display: "block" }}
        >
          <div className="panel" style={{ marginBottom: 12, cursor: "pointer" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <h3 style={{ margin: 0 }}>{s.name}</h3>
              <span className="muted">
                v{s.version} · {fmtInt(s.run_count)} {s.run_count === 1 ? "run" : "runs"}
              </span>
            </div>
            {strategyExplainers[s.slug] ? (
              <div className="se-card-line">
                <span className="se-card-tag">{strategyExplainers[s.slug].tagline}</span>
                <span className="badge badge-sm se-card-session">
                  {s.session === "globex" ? "globex" : "RTH"}
                </span>
              </div>
            ) : (
              <div className="muted" style={{ margin: "6px 0 10px" }}>{s.description}</div>
            )}
            <div>
              <span className="muted">Baseline: </span>
              <BaselineStats s={s} />
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}
