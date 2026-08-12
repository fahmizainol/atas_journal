import { useState } from "react";
import { useFilters } from "../hooks/useFilters";
import {
  useArchiveModel,
  useCreateModel,
  useCreateRule,
  useModelStats,
  useModels,
  useRetireRule,
  useUpdateModel,
  useUpdateRule,
} from "../hooks/useModels";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import type { Metrics, Model, ModelStat, RuleStat } from "../lib/types";

function Pnl({ v }: { v: number }) {
  return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
}

const BUCKET_LABEL: Record<string, string> = {
  followed: "Followed the model",
  partial: "Partially followed",
  broke: "Broke every rule",
};

// The whole point of the rule checklist: expectancy conditioned on compliance.
function ComplianceTable({ stat }: { stat: ModelStat }) {
  const c = stat.compliance;
  if (c.rules === 0)
    return (
      <div className="section-cap">
        This model declares no entry rules, so there's nothing to score against.
        Add rules below and they'll appear on each trade's journal form.
      </div>
    );
  if (c.buckets.length === 0)
    return (
      <div className="section-cap">
        None of these {fmtInt(stat.metrics.trades)} trades have been checked
        against the rules yet.
      </div>
    );
  return (
    <>
      <div className="table-scroll-x">
        <table className="data-table">
          <thead>
            <tr>
              <th>Compliance</th>
              <th>Trades</th>
              <th>Win rate</th>
              <th>Expectancy</th>
              <th>Net PnL</th>
            </tr>
          </thead>
          <tbody>
            {c.buckets.map((b) => (
              <tr key={b.label}>
                <td>{BUCKET_LABEL[b.label] ?? b.label}</td>
                <td>{fmtInt(b.trades)}</td>
                <td>{fmtPct(b.win_rate)}</td>
                <td>{fmt(b.expectancy)}</td>
                <td><Pnl v={b.net_pnl} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {c.unscored > 0 && (
        <div className="section-cap" style={{ marginTop: 6 }}>
          {fmtInt(c.unscored)} trade{c.unscored === 1 ? "" : "s"} assigned to this
          model but never checked against its rules — excluded from the split
          rather than counted as broken.
        </div>
      )}
    </>
  );
}

function RuleStatsTable({ rows }: { rows: RuleStat[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="table-scroll-x">
      <table className="data-table">
        <thead>
          <tr>
            <th>Rule</th>
            <th>Met</th>
            <th>Expectancy (met)</th>
            <th>Missed</th>
            <th>Expectancy (missed)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.label}</td>
              <td>{fmtInt(r.met_trades)}</td>
              <td>{r.met_trades ? fmt(r.met_expectancy) : "—"}</td>
              <td>{fmtInt(r.missed_trades)}</td>
              <td>{r.missed_trades ? fmt(r.missed_expectancy) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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

function ModelCard({ stat }: { stat: ModelStat }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="panel">
      <div className="section-title">
        <span className="badge">{stat.name}</span>
        {stat.archived && <span className="section-cap"> (archived)</span>}
      </div>
      {stat.description && (
        <div className="section-cap" style={{ marginBottom: 8 }}>{stat.description}</div>
      )}
      <MetricGrid m={stat.metrics} />
      <div style={{ marginTop: 10 }}>
        <button type="button" className={open ? "active" : ""} onClick={() => setOpen((o) => !o)}>
          {open ? "▾ Hide rule compliance" : `▸ Rule compliance (${stat.compliance.rules} rules)`}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <ComplianceTable stat={stat} />
          <div style={{ marginTop: 12 }}>
            <RuleStatsTable rows={stat.rules} />
          </div>
        </div>
      )}
    </div>
  );
}

// --- Management ----------------------------------------------------------
function RuleEditor({ model }: { model: Model }) {
  const [label, setLabel] = useState("");
  const create = useCreateRule(model.id);
  const update = useUpdateRule();
  const retire = useRetireRule();
  const busy = create.isPending || update.isPending || retire.isPending;

  const move = (index: number, delta: number) => {
    const target = model.rules[index + delta];
    const self = model.rules[index];
    if (!target) return;
    // Swap the two sort_orders; the list re-sorts on refetch.
    update.mutate({ id: self.id, sort_order: target.sort_order });
    update.mutate({ id: target.id, sort_order: self.sort_order });
  };

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    if (!label.trim()) return;
    create.mutate(label.trim(), { onSuccess: () => setLabel("") });
  };

  return (
    <div style={{ marginTop: 8 }}>
      {model.rules.length === 0 ? (
        <div className="section-cap">No entry rules yet.</div>
      ) : (
        <div className="table-scroll-x">
          <table className="data-table">
            <tbody>
              {model.rules.map((r, i) => (
                <tr key={r.id}>
                  <td>{r.label}</td>
                  <td style={{ whiteSpace: "nowrap", width: 1 }}>
                    <button
                      type="button"
                      onClick={() => move(i, -1)}
                      disabled={busy || i === 0}
                      title="Move up"
                    >
                      ↑
                    </button>{" "}
                    <button
                      type="button"
                      onClick={() => move(i, 1)}
                      disabled={busy || i === model.rules.length - 1}
                      title="Move down"
                    >
                      ↓
                    </button>{" "}
                    <button
                      type="button"
                      className="btn-danger"
                      disabled={busy}
                      title="Retire this rule. Trades already scored against it keep their score."
                      onClick={() => {
                        if (
                          window.confirm(
                            `Retire the rule “${r.label}”? It leaves the checklist, but ` +
                              `trades already scored against it keep their compliance score.`,
                          )
                        )
                          retire.mutate(r.id);
                      }}
                    >
                      Retire
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form onSubmit={add} style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="new entry rule…"
          style={{ flex: 1 }}
        />
        <button type="submit" className="btn-accent" disabled={busy || !label.trim()}>
          Add rule
        </button>
      </form>
    </div>
  );
}

function ModelRow({ model }: { model: Model }) {
  const [name, setName] = useState(model.name);
  const [desc, setDesc] = useState(model.description);
  const [open, setOpen] = useState(false);
  const update = useUpdateModel();
  const archive = useArchiveModel();
  const busy = update.isPending || archive.isPending;
  const dirty = name.trim() !== model.name || desc !== model.description;

  return (
    <>
      <tr>
        <td style={{ width: "26%" }}>
          <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: "100%" }} />
        </td>
        <td>
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="description…"
            style={{ width: "100%" }}
          />
        </td>
        <td style={{ whiteSpace: "nowrap", width: 1 }}>
          <button type="button" onClick={() => setOpen((o) => !o)} style={{ marginRight: 6 }}>
            {open ? "▾" : "▸"} Rules ({model.rules.length})
          </button>
          <button
            type="button"
            className="btn-accent"
            disabled={busy || !dirty || !name.trim()}
            onClick={() => update.mutate({ id: model.id, name: name.trim(), description: desc })}
            style={{ marginRight: 6 }}
          >
            Save
          </button>
          <button
            type="button"
            className="btn-danger"
            disabled={busy}
            title="Archive this model. Trades assigned to it keep resolving, so its historical stats don't change."
            onClick={() => {
              if (
                window.confirm(
                  `Archive the model “${model.name}”? It leaves the picker, but trades ` +
                    `already assigned to it keep it — their stats don't change.`,
                )
              )
                archive.mutate(model.id);
            }}
          >
            Archive
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3}>
            <RuleEditor model={model} />
          </td>
        </tr>
      )}
    </>
  );
}

function ModelManager() {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const { data: all = [], isLoading } = useModels();
  const create = useCreateModel();
  // Only live models are manageable; archived ones survive for their trades.
  const models = all.filter((m) => !m.archived);
  // The name check spans archived models too — the DB's UNIQUE(name) does.
  const exists = all.some((m) => m.name.toLowerCase() === newName.trim().toLowerCase());

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name || exists) return;
    create.mutate(
      { name, description: newDesc.trim() },
      {
        onSuccess: () => {
          setNewName("");
          setNewDesc("");
        },
      },
    );
  };

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <button type="button" className={open ? "active" : ""} onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Manage models ({models.length})
      </button>
      {open && (
        <div style={{ marginTop: 12 }}>
          <form onSubmit={add} className="model-add-form">
            <input
              className="model-add-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="new model name…"
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="description (optional)…"
              style={{ flex: 1 }}
            />
            <button
              type="submit"
              className="btn-accent"
              disabled={create.isPending || !newName.trim() || exists}
            >
              Add
            </button>
          </form>
          {exists && (
            <div className="section-cap neg" style={{ marginBottom: 8 }}>
              A model named “{newName.trim()}” already exists.
            </div>
          )}
          {isLoading ? (
            <div className="section-cap">Loading…</div>
          ) : models.length === 0 ? (
            <div className="section-cap">No models yet — add one above.</div>
          ) : (
            <div className="table-scroll-x">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Description</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <ModelRow key={m.id} model={m} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="section-cap" style={{ marginTop: 10 }}>
            Archiving a model or retiring a rule is a soft delete: trades already
            assigned or scored keep their history, so these numbers never shift
            under you.
          </div>
        </div>
      )}
    </div>
  );
}

export function Models() {
  const { scope } = useFilters();
  const { data, isLoading } = useModelStats(scope);
  const models = data?.models ?? [];
  const unassigned = data?.unassigned;

  return (
    <div>
      <div className="section-title">Models</div>
      <div className="section-cap">
        Per-model performance over the current filter scope. A trade has exactly
        one model or none, so these groups partition your trades — the models
        below plus the off-model bucket add up to the scope total.
      </div>
      <ModelManager />
      {isLoading ? (
        <div className="notice">Loading…</div>
      ) : (
        <>
          {unassigned && unassigned.trades > 0 && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="section-title">Off-model</div>
              <div className="section-cap" style={{ marginBottom: 8 }}>
                {fmtInt(unassigned.trades)} trades with no model assigned. Everything
                here is a trade you took without a plan on record.
              </div>
              <MetricGrid m={unassigned} />
            </div>
          )}
          {models.length === 0 ? (
            <div className="notice">
              No trades assigned to a model yet. Pick a model on a trade's journal form.
            </div>
          ) : (
            <div className="card-grid-2">
              {models.map((s) => (
                <ModelCard key={s.id} stat={s} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
