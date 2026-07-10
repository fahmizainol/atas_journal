import { Fragment, useEffect, useState } from "react";
import { useFilters } from "../hooks/useFilters";
import {
  useBacktestDetail,
  useBacktestsOverview,
  useImportFeed,
} from "../hooks/useBacktests";
import { useUpdateModel } from "../hooks/useModels";
import { usePatchSession } from "../hooks/useSessions";
import { EquityCurveChart } from "../components/charts/EquityCurveChart";
import { DistributionChart } from "../components/charts/DistributionChart";
import { fmt, fmtDateTime, fmtInt, fmtPct } from "../lib/format";
import type {
  BacktestModelCard,
  BacktestSessionRow,
  ImportFeedEvent,
  Metrics,
  SlimMetrics,
} from "../lib/types";

function Pnl({ v }: { v: number | null | undefined }) {
  if (v == null) return <>—</>;
  return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
}

// --- Watcher feed ----------------------------------------------------------
const KIND_LABEL: Record<ImportFeedEvent["kind"], string> = {
  imported: "imported",
  unknown_folder: "skipped",
  error: "failed",
};

function FeedLine({ e }: { e: ImportFeedEvent }) {
  const cls = e.kind === "imported" ? "pos" : "neg";
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline", lineHeight: 1.7 }}>
      <span className="muted" style={{ fontSize: 11, whiteSpace: "nowrap" }}>
        {fmtDateTime(e.ts).slice(0, 16)}
      </span>
      <span className={cls} style={{ fontSize: 12 }}>{KIND_LABEL[e.kind]}</span>
      <span style={{ fontSize: 12, wordBreak: "break-all" }}>{e.file}</span>
      {e.kind === "imported" && (
        <span className="badge badge-sm">
          {e.mode}
          {e.model_name ? ` · ${e.model_name}` : ""}
        </span>
      )}
      {e.message && (
        <span className="muted" style={{ fontSize: 11 }}>{e.message}</span>
      )}
    </div>
  );
}

function WatcherFeed() {
  const { data } = useImportFeed();
  const [open, setOpen] = useState(false);
  const events = data?.events ?? [];
  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <button type="button" className={open ? "active" : ""} onClick={() => setOpen((o) => !o)}>
          {open ? "▾" : "▸"} Auto-import feed
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          watching data/imports/ every {data ? Math.round(data.interval_s) : 60}s
          {data?.last_scan_at ? ` — last scan ${fmtDateTime(data.last_scan_at).slice(11, 16)} UTC` : ""}
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {events.length === 0 ? (
            <div className="section-cap">
              Nothing yet. Export a backtest into its model's folder under
              data/imports/backtest/ (or any live/replay export into data/imports/)
              and it shows up here within a minute.
            </div>
          ) : (
            events.slice(0, 30).map((e) => <FeedLine key={e.seq} e={e} />)
          )}
        </div>
      )}
    </div>
  );
}

// --- Model picker ------------------------------------------------------------
function SampleProgress({ trades, target }: { trades: number; target: number | null }) {
  if (!target) return null;
  const pct = Math.min(100, (trades / target) * 100);
  return (
    <div
      title={`${fmtInt(trades)} / ${fmtInt(target)} trades`}
      style={{ height: 6, borderRadius: 3, background: "var(--grid)", overflow: "hidden" }}
    >
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: pct >= 100 ? "var(--green)" : "var(--accent)",
        }}
      />
    </div>
  );
}

function ModelCard({
  card, selected, onSelect,
}: {
  card: BacktestModelCard;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      className="kpi-card"
      onClick={onSelect}
      style={{
        cursor: "pointer",
        borderColor: selected ? "var(--accent)" : undefined,
        minWidth: 180,
      }}
    >
      <div className="kpi-label">
        {card.name}
        {card.archived ? " (archived)" : ""}
      </div>
      <div className="kpi-value" style={{ fontSize: 16 }}>
        {fmtInt(card.metrics.trades)}
        {card.target_sample ? ` / ${fmtInt(card.target_sample)}` : ""} trades
      </div>
      <div className="kpi-sub">
        {card.sessions} session{card.sessions === 1 ? "" : "s"} ·{" "}
        <Pnl v={card.metrics.net_pnl as number | null} />
      </div>
      <div style={{ marginTop: 6 }}>
        <SampleProgress trades={card.metrics.trades} target={card.target_sample} />
      </div>
    </div>
  );
}

// --- Detail ------------------------------------------------------------------
function MetricGrid({ m }: { m: Metrics }) {
  const cells: [string, React.ReactNode][] = [
    ["Trades", `${fmtInt(m.trades)} (${fmtInt(m.longs)}L / ${fmtInt(m.shorts)}S)`],
    ["Win rate", fmtPct(m.win_rate)],
    ["Net PnL", <Pnl v={m.net_pnl as number} />],
    ["Profit factor", fmt(m.profit_factor, false)],
    ["Expectancy", fmt(m.expectancy)],
    ["Avg win", fmt(m.avg_win)],
    ["Avg loss", fmt(m.avg_loss)],
    ["Best", fmt(m.best_trade)],
    ["Worst", fmt(m.worst_trade)],
    ["Max drawdown", fmt(m.max_drawdown)],
  ];
  return (
    <div className="kpi-grid kpi-compact" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
      {cells.map(([label, value]) => (
        <div key={label} className="kpi-card">
          <div className="kpi-label">{label}</div>
          <div className="kpi-value">{value}</div>
        </div>
      ))}
    </div>
  );
}

function TargetEditor({ modelId, target }: { modelId: number; target: number | null }) {
  const [value, setValue] = useState(target == null ? "" : String(target));
  useEffect(() => setValue(target == null ? "" : String(target)), [modelId, target]);
  const update = useUpdateModel();
  const parsed = value.trim() === "" ? 0 : Number(value);
  const valid = Number.isInteger(parsed) && parsed >= 0;
  const dirty = (target ?? 0) !== parsed;
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      <label className="muted" style={{ fontSize: 12 }}>Target sample</label>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="e.g. 100"
        style={{ width: 70 }}
        inputMode="numeric"
      />
      <button
        type="button"
        className="btn-accent"
        disabled={update.isPending || !valid || !dirty}
        onClick={() => update.mutate({ id: modelId, target_sample: parsed })}
        title="How many backtest trades this model needs before you trust its numbers. Empty clears the target."
      >
        Save
      </button>
    </span>
  );
}

const MODE_LABEL: Record<string, string> = {
  backtest: "Backtest",
  replay: "Replay",
  live: "Live",
};

function ComparisonTable({ comparison }: { comparison: Record<string, SlimMetrics> }) {
  const rows = (["backtest", "replay", "live"] as const).map((mode) => ({
    mode,
    m: comparison[mode] ?? { trades: 0 },
  }));
  return (
    <div className="panel">
      <div className="section-cap">
        Same model, three arenas. The whole point of a backtest: does replay/live
        performance track what the sample promised?
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Mode</th>
            <th>Trades</th>
            <th>Win rate</th>
            <th>Expectancy</th>
            <th>Profit factor</th>
            <th>Avg win</th>
            <th>Avg loss</th>
            <th>Max DD</th>
            <th>Net PnL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ mode, m }) => (
            <tr key={mode}>
              <td>{MODE_LABEL[mode]}</td>
              <td>{fmtInt(m.trades)}</td>
              <td>{m.trades ? fmtPct(m.win_rate) : "—"}</td>
              <td>{m.trades ? fmt(m.expectancy) : "—"}</td>
              <td>{m.trades ? fmt(m.profit_factor, false) : "—"}</td>
              <td>{m.trades ? fmt(m.avg_win) : "—"}</td>
              <td>{m.trades ? fmt(m.avg_loss) : "—"}</td>
              <td>{m.trades ? fmt(m.max_drawdown) : "—"}</td>
              <td>{m.trades ? <Pnl v={m.net_pnl as number} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SessionNote({ row }: { row: BacktestSessionRow }) {
  const [text, setText] = useState(row.note);
  useEffect(() => setText(row.note), [row.source_file, row.note]);
  const patch = usePatchSession();
  const dirty = text !== row.note;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="session journal — hypothesis, market conditions, verdict…"
        rows={2}
        style={{ flex: 1, resize: "vertical" }}
      />
      <button
        type="button"
        className="btn-accent"
        disabled={patch.isPending || !dirty}
        onClick={() => patch.mutate({ sourceFile: row.source_file, patch: { note: text } })}
      >
        Save
      </button>
    </div>
  );
}

function SessionsTable({ rows }: { rows: BacktestSessionRow[] }) {
  const [openNote, setOpenNote] = useState<string | null>(null);
  if (rows.length === 0)
    return (
      <div className="section-cap">
        No backtest sessions for this model yet — export one into its folder and
        the watcher will pick it up.
      </div>
    );
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Export</th>
          <th>Days</th>
          <th>Trades</th>
          <th>Win rate</th>
          <th>Expectancy</th>
          <th>Net PnL</th>
          <th>Imported</th>
          <th>Note</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s) => (
          <Fragment key={s.source_file}>
            <tr style={s.archived ? { opacity: 0.55 } : undefined}>
              <td style={{ wordBreak: "break-all" }}>
                {s.source_file}
                {s.archived && <span className="muted"> (archived)</span>}
              </td>
              <td style={{ whiteSpace: "nowrap" }}>
                {s.first_day
                  ? s.first_day === s.last_day
                    ? s.first_day
                    : `${s.first_day} → ${s.last_day}`
                  : "—"}
              </td>
              <td>{fmtInt(s.metrics.trades)}</td>
              <td>{s.metrics.trades ? fmtPct(s.metrics.win_rate) : "—"}</td>
              <td>{s.metrics.trades ? fmt(s.metrics.expectancy) : "—"}</td>
              <td>{s.metrics.trades ? <Pnl v={s.metrics.net_pnl as number} /> : "—"}</td>
              <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(s.imported_at).slice(0, 16)}</td>
              <td style={{ width: 1, whiteSpace: "nowrap" }}>
                <button
                  type="button"
                  className={openNote === s.source_file ? "active" : ""}
                  onClick={() =>
                    setOpenNote((o) => (o === s.source_file ? null : s.source_file))
                  }
                >
                  {s.note ? "▸ edit" : "▸ add"}
                </button>
              </td>
            </tr>
            {openNote === s.source_file && (
              <tr>
                <td colSpan={8}>
                  <SessionNote row={s} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

// --- Page ---------------------------------------------------------------------
export function Backtests() {
  const { scope } = useFilters();
  const { data: cards = [], isLoading } = useBacktestsOverview(scope.tz);
  const [selected, setSelected] = useState<number | null>(null);
  // Default to the model with the most backtest trades — the one being worked.
  const modelId =
    selected ??
    [...cards].sort((a, b) => b.metrics.trades - a.metrics.trades)[0]?.id ??
    null;
  const { data: detail } = useBacktestDetail(modelId, scope.tz);

  return (
    <div>
      <div className="section-title">Backtests</div>
      <div className="section-cap">
        Pick a model, run it in ATAS, export into{" "}
        <code>data/imports/backtest/&lt;model-folder&gt;/</code> — the watcher
        imports it as a backtest of that model within a minute. Live/replay
        exports still go to <code>data/imports/</code> and classify themselves.
      </div>

      <WatcherFeed />

      {isLoading ? (
        <div className="notice">Loading…</div>
      ) : cards.length === 0 ? (
        <div className="notice">No models yet — create one on the Models tab.</div>
      ) : (
        <>
          <div
            className="kpi-grid kpi-compact"
            style={{
              gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
              marginBottom: 16,
            }}
          >
            {cards.map((c) => (
              <ModelCard
                key={c.id}
                card={c}
                selected={c.id === modelId}
                onSelect={() => setSelected(c.id)}
              />
            ))}
          </div>

          {detail && (
            <>
              <div className="panel" style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
                  <div className="section-title" style={{ margin: 0 }}>
                    <span className="badge">{detail.model.name}</span>
                  </div>
                  {detail.model.folder && (
                    <span className="muted" style={{ fontSize: 12 }}>
                      export to <code>data/imports/backtest/{detail.model.folder}/</code>
                    </span>
                  )}
                  <TargetEditor modelId={detail.model.id} target={detail.model.target_sample} />
                </div>
                {detail.model.description && (
                  <div className="section-cap" style={{ marginTop: 6 }}>
                    {detail.model.description}
                  </div>
                )}
                <div style={{ marginTop: 12 }}>
                  <MetricGrid m={detail.metrics} />
                </div>
              </div>

              <div className="card-grid-2" style={{ marginBottom: 16 }}>
                <EquityCurveChart data={detail.equity} />
                <DistributionChart values={detail.distribution} />
              </div>

              <div style={{ marginBottom: 16 }}>
                <ComparisonTable comparison={detail.comparison} />
              </div>

              <div className="panel">
                <div className="section-title">Backtest sessions</div>
                <SessionsTable rows={detail.sessions} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
