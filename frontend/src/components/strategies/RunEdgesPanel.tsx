import { useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { useRunEdges } from "../../hooks/useRunEdges";
import { EdgeTable, edgeColumns } from "../EdgeTable";
import { fmt } from "../../lib/format";
import type { RunCut, RunEdgeRow, RunEdgeScope, RunEdgeScopeName, RunEdges } from "../../lib/types";

const SCOPE_LABEL: Record<RunEdgeScopeName, string> = {
  traded: "Traded",
  vetoed: "Vetoed",
  all: "Both",
};

const SCOPE_HELP: Record<RunEdgeScopeName, string> = {
  traded: "The trades this run actually took",
  vetoed: "The ghost trades its confluence gates cut, and what they would have paid",
  all: "The run the gates were never in — traded and vetoed together",
};

const sign = (n: number, digits = 2) => `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
const tone = (n: number) => (n >= 0 ? "pos" : "neg");

// Ranked-by-nothing on purpose: the cuts render in the order the server lists
// them, which is the knowable ones first (a filter you could have traded) and the
// outcome ones after (a description of what the exits did).
export function RunEdgesPanel({ slug, runId }: { slug: string; runId: string }) {
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
          title={open ? "Hide the edge breakdowns" : "Show the edge breakdowns"}
        >
          {open ? "▾" : "▸"}
        </button>
        <h3 style={{ margin: 0 }}>Behavioral edges</h3>
        <span className="muted" style={{ fontSize: 12 }}>
          Which buckets carried this run — and which ones only look like they did
        </span>
      </div>

      {open && <Body slug={slug} runId={runId} />}
    </div>
  );
}

function Body({ slug, runId }: { slug: string; runId: string }) {
  const [scope, setScope] = useState<RunEdgeScopeName>("traded");
  const [compare, setCompare] = useState(false);
  const { data, isLoading, error } = useRunEdges(slug, runId);

  if (isLoading) return <div className="notice">Scoring the breakdowns…</div>;
  if (error || !data) return <div className="notice">No trades to break down for this run.</div>;

  const scopes = (Object.keys(SCOPE_LABEL) as RunEdgeScopeName[]).filter(
    (s) => data.scopes[s] != null,
  );
  // A run whose gates vetoed nothing has one book, so there is nothing to toggle.
  const shown = data.scopes[scope] ?? data.scopes.traded;
  if (!shown) return <div className="notice">This run took no trades.</div>;

  const ref = data.reference;
  const refScope = compare ? (ref?.scopes[scope] ?? null) : null;

  return (
    <>
      <div
        style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}
      >
        {scopes.length > 1 && (
          <span style={{ display: "flex", gap: 2 }}>
            {scopes.map((s) => (
              <button
                key={s}
                type="button"
                className={`btn-xs${s === scope ? " btn-accent" : ""}`}
                title={SCOPE_HELP[s]}
                onClick={() => setScope(s)}
              >
                {SCOPE_LABEL[s]}
              </button>
            ))}
          </span>
        )}
        <span className="muted" style={{ fontSize: 12 }}>
          {shown.trades} trade{shown.trades === 1 ? "" : "s"} ·{" "}
          <span className={tone(shown.net_pnl)}>{fmt(shown.net_pnl)}</span> net
        </span>
        {ref && (
          <button
            type="button"
            className={`btn-xs${compare ? " btn-accent" : ""}`}
            style={{ marginLeft: "auto" }}
            title={`Show each bucket's change against ${ref.run_id}${
              ref.is_baseline ? " (the pinned baseline)" : ""
            }`}
            onClick={() => setCompare((v) => !v)}
          >
            {compare ? "Hide" : "Show"} Δ vs {ref.is_baseline ? "baseline" : "reference"}
          </button>
        )}
      </div>

      {compare && ref && (
        <div className="notice" style={{ marginBottom: 12 }}>
          Δ is this run minus{" "}
          <strong>{ref.label || ref.run_id}</strong>
          {ref.is_baseline && " (the pinned baseline)"}, bucket by bucket — a bucket the reference
          never traded shows the whole of this run's number.{" "}
          {ref.same_window ? (
            <>Both runs cover the same window, so the Δ is the knob change and nothing else.</>
          ) : (
            <strong>
              The reference covers {ref.start} → {ref.end}, a different window from this run — so a
              Δ in net is mostly a count of sessions the two runs didn't share. Read Δ R, which is
              per-trade and doesn't care how many days each run saw.
            </strong>
          )}
        </div>
      )}

      {/* The columns are the distinction, not a layout convenience: on the left,
          buckets that were facts before the fill — a split there is one you could
          have traded, and it is scored. On the right, what the trade went on to
          do, which is a description of the exits and can never be a filter. */}
      <div className="grid-2">
        <div>
          {shown.cuts.filter((c) => c.knowable).map((c) => (
            <Cut key={c.name} cut={c} refScope={refScope} data={data} />
          ))}
        </div>
        <div>
          {shown.cuts.filter((c) => !c.knowable).map((c) => (
            <Cut key={c.name} cut={c} refScope={refScope} data={data} />
          ))}
        </div>
      </div>

      <div className="section-cap" style={{ marginTop: 6 }}>
        Every trade in this book, cut {shown.cuts.length} ways. The <strong>luck</strong> line under
        each table is how often shuffling the P&amp;L across these same trades separates the buckets
        this well — with {data.permutations} shuffles, and a bar of{" "}
        {(data.luck_bar * 100).toFixed(1)}% that already accounts for how many cuts are on the page.
        A cut that doesn't clear it is not a finding, however large its biggest bucket looks: split
        {" "}{shown.trades} trades enough ways and one of them always pays. What clears the bar is
        somewhere to point the next experiment — turn it into a config and re-run it, rather than a
        filter you apply to this table afterwards.
      </div>
    </>
  );
}

function Cut({
  cut,
  refScope,
  data,
}: {
  cut: RunCut;
  refScope: RunEdgeScope | null;
  data: RunEdges;
}) {
  const refRows = useMemo(() => {
    const rows = refScope?.cuts.find((c) => c.name === cut.name)?.rows ?? [];
    return new Map(rows.map((r) => [String(r.bucket), r]));
  }, [refScope, cut.name]);

  const columns = useMemo<ColumnDef<RunEdgeRow, any>[]>(() => {
    const cols: ColumnDef<RunEdgeRow, any>[] = [
      ...(edgeColumns as ColumnDef<RunEdgeRow, any>[]),
      {
        accessorKey: "avg_r",
        header: "Avg R",
        // The one column that survives a change of stop size: dollars can't be read
        // across two configs that risked different amounts per trade.
        cell: (c) => {
          const v = c.getValue() as number;
          return <span className={tone(v)}>{sign(v)}R</span>;
        },
      },
    ];
    if (!refScope) return cols;
    return [
      ...cols,
      {
        id: "d_net",
        header: "Δ Net",
        accessorFn: (row) => row.net_pnl - (refRows.get(String(row.bucket))?.net_pnl ?? 0),
        cell: (c) => {
          const v = c.getValue() as number;
          return <span className={tone(v)}>{v >= 0 ? "+" : ""}{fmt(v)}</span>;
        },
      },
      {
        id: "d_r",
        header: "Δ R",
        accessorFn: (row) => {
          const r = refRows.get(String(row.bucket));
          // No reference bucket means the reference never traded here — a Δ against
          // a per-trade average that doesn't exist is not zero, it's undefined.
          return r ? row.avg_r - r.avg_r : null;
        },
        cell: (c) => {
          const v = c.getValue() as number | null;
          if (v == null) return <span className="muted">new</span>;
          return <span className={tone(v)}>{sign(v)}R</span>;
        },
      },
    ];
  }, [refScope, refRows]);

  return (
    <EdgeTable
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {cut.label}
          {cut.holds && (
            <span className="badge badge-sm" title="Survives the multiple-testing bar">
              holds
            </span>
          )}
        </span>
      }
      data={cut.rows}
      columns={columns}
      caption={<LuckLine cut={cut} bar={data.luck_bar} />}
    />
  );
}

function LuckLine({ cut, bar }: { cut: RunCut; bar: number }) {
  if (!cut.knowable)
    return (
      <span className="muted">
        An outcome, not a filter — you can't decide at the fill which bucket a trade will land in,
        and a stop is a loss by construction. Unscored on purpose: a shuffle test here would print
        “holds” every time and mean nothing. Read it as what the exits did.
      </span>
    );

  if (cut.luck == null)
    return (
      <span className="muted">
        Not scored — too few trades, or every one of them in the same bucket.
      </span>
    );

  const pct = cut.luck < 0.01 ? "<1%" : `${Math.round(cut.luck * 100)}%`;
  return cut.holds ? (
    <span>
      Shuffled P&amp;L separates these buckets this well only <strong>{pct}</strong> of the time —
      clears the {(bar * 100).toFixed(1)}% bar. Worth turning into a config and re-running.
    </span>
  ) : (
    <span className="muted">
      Shuffled P&amp;L separates these buckets this well <strong>{pct}</strong> of the time. That is
      what noise hands you — this split is not evidence of anything yet.
    </span>
  );
}
