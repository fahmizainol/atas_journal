import { useEffect, useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { useGateAudit } from "../../hooks/useGateAudit";
import { useCreateRun } from "../../hooks/useStrategies";
import { DataTable } from "../DataTable";
import { fmt } from "../../lib/format";
import type {
  GateAudit,
  GateAuditGate,
  GateAuditVariant,
  GateVerdict,
} from "../../lib/strategyTypes";

const VERDICT_LABEL: Record<GateVerdict, string> = {
  real: "REAL",
  partial: "PARTIAL",
  weak: "LUCK-SUSPECT",
  fail: "FAIL",
  unscored: "UNSCORED",
};

const VERDICT_HELP: Record<GateVerdict, string> = {
  real: "Positive marginal that survives every robustness test: tail-cap, window halves, parameter neighborhood, selection quality",
  partial: "Positive marginal, no failed test — but some variant runs are missing, so the ladder is incomplete",
  weak: "Positive marginal that fails a robustness test — indistinguishable from a lucky cutoff on this window",
  fail: "The full-stack A/B says the gate costs money on this config",
  unscored: "No gate-off run exists yet; only the ghost-frame tests could score",
};

const VERDICT_COLOR: Record<GateVerdict, string> = {
  real: "var(--green)",
  partial: "var(--muted)",
  weak: "#d9a03f",
  fail: "var(--red)",
  unscored: "var(--muted)",
};

const sign = (n: number) => `${n >= 0 ? "+" : ""}${fmt(n)}`;
const tone = (n: number) => (n >= 0 ? "pos" : "neg");

/** Compact param summary — the values that define the gate, not the plumbing. */
function paramSummary(gate: string, params: Record<string, unknown>): string {
  const p = params as Record<string, string | number>;
  if (gate === "regime") return `bbr≤${p.bbr_max}@${p.checkpoint}`;
  if (gate === "chop") return `overlap≤${p.max_overlap}`;
  if (gate === "gx_overhang") return `≤${p.max_ticks}t`;
  if (gate === "gx_poc_shape") return `${p.zone_min_ticks}–${p.zone_max_ticks}t ${p.mode}`;
  return Object.entries(params)
    .filter(([k]) => k !== "enabled")
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
}

function VerdictChip({ verdict }: { verdict: GateVerdict }) {
  return (
    <span
      title={VERDICT_HELP[verdict]}
      style={{
        color: VERDICT_COLOR[verdict],
        border: `1px solid ${VERDICT_COLOR[verdict]}`,
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: 11,
        whiteSpace: "nowrap",
      }}
    >
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

function Check({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span className={ok == null ? "muted" : ok ? "pos" : "neg"} title={label}>
      {ok == null ? "·" : ok ? "✓" : "✗"}
    </span>
  );
}

/** Every not-yet-run variant across the audit, deduped (neighbors can collide
 * across gates when two stacks hash to the same config). */
function missingVariants(a: GateAudit): { gate: string; variant: GateAuditVariant }[] {
  const seen = new Set<string>();
  const out: { gate: string; variant: GateAuditVariant }[] = [];
  for (const g of a.gates) {
    for (const v of [g.off, ...g.neighbors]) {
      if ((v.state === "missing" || v.state === "error") && !seen.has(v.run_id)) {
        seen.add(v.run_id);
        out.push({ gate: g.gate, variant: v });
      }
    }
  }
  return out;
}

function anyRunning(a: GateAudit): boolean {
  return a.gates.some(
    (g) => g.off.state === "running" || g.neighbors.some((v) => v.state === "running"),
  );
}

export function GateAuditPanel({ slug, runId }: { slug: string; runId: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: open ? 12 : 0,
        }}
      >
        <button
          type="button"
          className="btn-xs"
          onClick={() => setOpen((v) => !v)}
          title={open ? "Hide the gate audit" : "Show the gate audit"}
        >
          {open ? "▾" : "▸"}
        </button>
        <h3 style={{ margin: 0 }}>Gate audit</h3>
        <span className="muted" style={{ fontSize: 12 }}>
          Which confluences are real edges, which are luck — the scorecard from the
          gate-robustness study
        </span>
      </div>

      {open && <Body slug={slug} runId={runId} />}
    </div>
  );
}

function Body({ slug, runId }: { slug: string; runId: string }) {
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const { data, isLoading, error } = useGateAudit(slug, runId, launching);
  const createRun = useCreateRun(slug);

  const missing = useMemo(() => (data ? missingVariants(data) : []), [data]);
  const running = data ? anyRunning(data) : false;

  // Sequential auto-launcher: one variant at a time (the API serializes runs
  // box-wide anyway — a second POST would just 409), next one fires when the
  // poll shows nothing in flight anymore.
  useEffect(() => {
    if (!launching || !data || running || createRun.isPending) return;
    const next = missing[0];
    if (!next) {
      setLaunching(false);
      return;
    }
    createRun.mutate(
      {
        config: next.variant.config,
        label: `gate-audit: ${next.gate} ${next.variant.label ?? "off"}`,
      },
      {
        onError: (e) => {
          setLaunchError(e instanceof Error ? e.message : String(e));
          setLaunching(false);
        },
      },
    );
  }, [launching, data, running, missing, createRun]);

  if (isLoading) return <div className="notice">Scoring the gates…</div>;
  if (error || !data) return <div className="notice">No completed run to audit.</div>;
  if (data.gates.length === 0)
    return <div className="notice">This config runs no confluence gates.</div>;

  const b = data.baseline;
  const tailShare = b.net !== 0 ? (100 * (b.net - b.net_ex_top20)) / b.net : 0;

  return (
    <>
      <div
        style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}
      >
        <span className="muted" style={{ fontSize: 12 }}>
          {b.trades} trades · <span className={tone(b.net)}>{fmt(b.net)}</span> net ·{" "}
          {fmt(b.net_ex_top20)} ex-top-20 (top 20 = {tailShare.toFixed(0)}% of net) · verdict
          rules in Lab → Research → “Gate Robustness Scorecard”
        </span>
        {(missing.length > 0 || launching || running) && (
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {launching || running ? (
              <span className="muted" style={{ fontSize: 12 }}>
                {running ? "variant running…" : "launching…"}
                {launching && ` (${missing.length} left)`}
              </span>
            ) : (
              <button
                type="button"
                className="btn-xs"
                title="Launch the gate-off / neighbor runs this audit still needs, one at a time (~1 min each, cached ticks only)"
                onClick={() => {
                  setLaunchError(null);
                  setLaunching(true);
                }}
              >
                Run {missing.length} missing variant{missing.length === 1 ? "" : "s"}
              </button>
            )}
            {launching && (
              <button type="button" className="btn-xs" onClick={() => setLaunching(false)}>
                Stop
              </button>
            )}
          </span>
        )}
      </div>
      {launchError && <div className="notice warn">{launchError}</div>}
      {!data.has_ghost_frame && (
        <div className="notice" style={{ marginBottom: 10 }}>
          This run predates the per-veto gate column — selection/cohort tests unavailable.
          Re-run the config to populate them.
        </div>
      )}

      <div className="table-scroll" style={{ overflow: "auto" }}>
        <DataTable
          data={data.gates}
          columns={columns}
          rowKey={(g) => g.gate}
          renderExpanded={(g) => <GateDetail g={g} />}
          scrollOnExpand={false}
        />
      </div>
    </>
  );
}

const columns: ColumnDef<GateAuditGate, any>[] = [
  {
    header: "Gate",
    accessorKey: "gate",
    cell: ({ row }) => (
      <span>
        {row.original.gate}{" "}
        <span className="muted" style={{ fontSize: 11 }}>
          {paramSummary(row.original.gate, row.original.params)}
        </span>
      </span>
    ),
  },
  {
    header: "Verdict",
    accessorKey: "verdict",
    cell: ({ row }) => (
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <VerdictChip verdict={row.original.verdict} />
        {row.original.mirage && (
          <span
            title="This gate's ghost trades net POSITIVE dollars even though removing the gate costs money — the composition-gate mirage. Never judge it by its ghost ledger."
            style={{ cursor: "help" }}
          >
            ⚠
          </span>
        )}
      </span>
    ),
  },
  {
    header: "Δ net",
    id: "d_net",
    accessorFn: (g) => g.marginal?.d_net ?? null,
    cell: ({ row }) => {
      const m = row.original.marginal;
      return m ? <span className={tone(m.d_net)}>{sign(m.d_net)}</span> : <span className="muted">—</span>;
    },
  },
  {
    header: "Δ PF",
    id: "d_pf",
    accessorFn: (g) => g.marginal?.d_pf ?? null,
    cell: ({ row }) => {
      const m = row.original.marginal;
      return m ? (
        <span className={tone(m.d_pf)}>{`${m.d_pf >= 0 ? "+" : ""}${m.d_pf.toFixed(2)}`}</span>
      ) : (
        <span className="muted">—</span>
      );
    },
  },
  {
    header: "Δ maxDD",
    id: "d_maxdd",
    accessorFn: (g) => g.marginal?.d_maxdd ?? null,
    cell: ({ row }) => {
      const m = row.original.marginal;
      // Positive = drawdown got shallower with the gate in.
      return m ? <span className={tone(m.d_maxdd)}>{sign(m.d_maxdd)}</span> : <span className="muted">—</span>;
    },
  },
  {
    header: "Δ ex-top-20",
    id: "d_tail",
    accessorFn: (g) => g.tail?.d_net_ex_top20 ?? null,
    cell: ({ row }) => {
      const t = row.original.tail;
      return t ? (
        <span className={tone(t.d_net_ex_top20)}>{sign(t.d_net_ex_top20)}</span>
      ) : (
        <span className="muted">—</span>
      );
    },
  },
  {
    header: "Ghost AUC",
    id: "auc",
    accessorFn: (g) => g.cohort?.auc ?? null,
    cell: ({ row }) => {
      const c = row.original.cohort;
      return c ? (
        <span title={`Mann-Whitney p=${c.p.toPrecision(2)} · ${c.n_ghost} unique ghosts`}>
          {c.auc.toFixed(2)}
          <span className="muted" style={{ fontSize: 11 }}> (p={c.p < 0.001 ? "<.001" : c.p.toFixed(2)})</span>
        </span>
      ) : (
        <span className="muted">—</span>
      );
    },
  },
  {
    header: "Tests",
    id: "checks",
    enableSorting: false,
    cell: ({ row }) => {
      const c = row.original.checks;
      return (
        <span style={{ display: "flex", gap: 6, fontSize: 12 }}>
          <Check ok={c.tail} label="Tail: marginal survives removing top-20 / winsorizing" />
          <Check ok={c.halves} label="Halves: both window halves positive" />
          <Check ok={c.plateau} label="Neighborhood: all parameter neighbors beat gate-off (plateau, not spike)" />
          <Check ok={c.selection} label="Selection: kept book beats random same-size subsets / ghost cohort separates" />
        </span>
      );
    },
  },
];

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ minWidth: 160 }}>
      <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13 }}>{children}</div>
    </div>
  );
}

function VariantLine({ v, offNet }: { v: GateAuditVariant; offNet?: number }) {
  const label = v.label ?? "off";
  if (v.state !== "done" || v.net == null)
    return (
      <div className="muted" style={{ fontSize: 12 }}>
        {label}: {v.state} <span style={{ fontSize: 11 }}>({v.run_id.slice(-8)})</span>
      </div>
    );
  const belowOff = offNet != null && v.net < offNet;
  return (
    <div style={{ fontSize: 12 }}>
      {label}: <span className={tone(v.net)}>{fmt(v.net)}</span>
      {v.pf != null && <span className="muted"> · PF {v.pf.toFixed(2)}</span>}
      {v.maxdd != null && <span className="muted"> · DD {fmt(v.maxdd)}</span>}
      {v.trades != null && <span className="muted"> · {v.trades} tr</span>}
      {belowOff && (
        <span className="neg" title="This neighbor lands BELOW gate-off — spike, not plateau">
          {" "}▼ below off
        </span>
      )}
    </div>
  );
}

function GateDetail({ g }: { g: GateAuditGate }) {
  const m = g.marginal;
  return (
    <div style={{ display: "flex", gap: 24, flexWrap: "wrap", padding: "8px 4px" }}>
      <Stat label={`Gate-off run (${g.off.state})`}>
        {m ? (
          <>
            {fmt(m.off_net)} · PF {m.off_pf.toFixed(2)} · DD {fmt(m.off_maxdd)} ·{" "}
            {m.off_trades} tr
            <div className="muted" style={{ fontSize: 11 }}>{g.off.run_id}</div>
          </>
        ) : (
          <span className="muted">not run yet — every marginal column waits on it</span>
        )}
      </Stat>
      {g.halves && (
        <Stat label="Marginal by window half">
          <span className={tone(g.halves.d_h1)} title={g.halves.h1_label}>
            {sign(g.halves.d_h1)}
          </span>{" "}
          /{" "}
          <span className={tone(g.halves.d_h2)} title={g.halves.h2_label}>
            {sign(g.halves.d_h2)}
          </span>
        </Stat>
      )}
      {g.months && (
        <Stat label="Months better (sign test)">
          {g.months.better}/{g.months.months}
          <span className="muted"> · p={g.months.p.toFixed(2)}</span>
        </Stat>
      )}
      {g.bootstrap && (
        <Stat label="Month-block bootstrap CI">
          [{fmt(g.bootstrap.ci_lo)}, {fmt(g.bootstrap.ci_hi)}]
        </Stat>
      )}
      {g.cohort && (
        <Stat label={`Unique ghosts (${g.cohort.n_ghost})`}>
          <span className={tone(g.cohort.ghost_net)}>{fmt(g.cohort.ghost_net)}</span>
          {g.mirage && (
            <span title="Positive ghost dollars + positive in-stack marginal = the composition-gate mirage; the value is what freed arm-cycles re-fill into, not these trades."> ⚠</span>
          )}
          <div className="muted" style={{ fontSize: 11 }}>
            stop {Math.round(g.cohort.ghost_stop * 100)}% vs kept{" "}
            {Math.round(g.cohort.kept_stop * 100)}%
          </div>
        </Stat>
      )}
      {g.selection && (
        <Stat label="Kept vs random subsets">
          win p{g.selection.win_pctile.toFixed(0)} · R p{g.selection.mean_r_pctile.toFixed(0)}
          <div className="muted" style={{ fontSize: 11 }}>
            percentile among {g.selection.n_universe}-trade universe draws
          </div>
        </Stat>
      )}
      <Stat label="Parameter neighborhood">
        {g.neighbors.length === 0 ? (
          <span className="muted">no neighborhood defined for this gate</span>
        ) : (
          g.neighbors.map((v) => (
            <VariantLine key={v.run_id} v={v} offNet={g.marginal?.off_net} />
          ))
        )}
      </Stat>
    </div>
  );
}
