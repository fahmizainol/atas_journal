import { useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { useRunEdges } from "../../hooks/useRunEdges";
import { DataTable } from "../DataTable";
import { EdgeTable, edgeColumns } from "../EdgeTable";
import { fmt, fmtInt, fmtPct } from "../../lib/format";
import type {
  ConfluenceRow,
  DailyConcentration,
  Discriminator,
  DiscriminatorRow,
  ExcursionRow,
  RHistBin,
  RunCut,
  RunEdgeRow,
  RunEdgeScope,
  RunEdgeScopeName,
  RunEdges,
  WinLossProfile,
  WinLossSide,
} from "../../lib/types";

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

      {shown.win_loss?.sides && shown.win_loss.sides.length > 0 && (
        <WinLossBreakdown profile={shown.win_loss} />
      )}

      {shown.r_hist && shown.r_hist.length > 0 && <RHistogram bins={shown.r_hist} />}

      {shown.discriminator && (
        <DiscriminatorBreakdown disc={shown.discriminator} />
      )}

      {shown.daily && shown.daily.days > 0 && <DailyBreakdown daily={shown.daily} />}

      {shown.excursions && shown.excursions.length > 0 ? (
        <ExcursionBreakdown rows={shown.excursions} />
      ) : (
        <div className="notice" style={{ marginBottom: 12 }}>
          This run predates MFE/MAE tracking — re-run it (same config, no logic change) to populate
          the excursion profile.
        </div>
      )}

      {scope === "vetoed" && shown.confluences && shown.confluences.length > 0 && (
        <ConfluenceBreakdown rows={shown.confluences} />
      )}

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

// Seconds -> a compact hold like "45s", "3m 20s", "1h 5m". The bucket cuts hide
// this behind a median-per-bucket; here it is the median per side.
function fmtHold(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

// The winners-vs-losers distribution the bucket cuts average away. Two rows —
// what won, what lost — read across the shape of each side, not its middle:
// the fattest single trade (Biggest), how much of the side its top-3 carried
// (Top-3), the payoff geometry (Avg/Med/Std R), and the hold each side was given.
// Below, the book-level numbers a win rate hides: profit factor, payoff, streaks,
// drawdown. All facts after the fill — read as description, never a filter.
const winLossColumns: ColumnDef<WinLossSide, any>[] = [
  { accessorKey: "bucket", header: "Outcome", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "trades",
    header: "Trades",
    cell: (c) => {
      const row = c.row.original;
      return (
        <span>
          {fmtInt(row.trades)}{" "}
          <span className="muted">({fmtPct(row.share * 100, 0)})</span>
        </span>
      );
    },
  },
  {
    accessorKey: "net_pnl",
    header: "Net",
    cell: (c) => <span className={tone(c.getValue() as number)}>{fmt(c.getValue() as number)}</span>,
  },
  { accessorKey: "avg_pnl", header: "Avg", cell: (c) => fmt(c.getValue() as number) },
  {
    accessorKey: "avg_r",
    header: "Avg R",
    cell: (c) => <span className={tone(c.getValue() as number)}>{sign(c.getValue() as number)}R</span>,
  },
  {
    accessorKey: "med_r",
    header: "Med R",
    cell: (c) => <span className={tone(c.getValue() as number)}>{sign(c.getValue() as number)}R</span>,
  },
  {
    accessorKey: "std_r",
    header: "Std R",
    // Dispersion of the side — a tight loser cluster next to a fat winner tail is a
    // different book than symmetric noise. Not signed: it is a spread, not a return.
    cell: (c) => `${(c.getValue() as number).toFixed(2)}R`,
  },
  {
    accessorKey: "best_r",
    header: "Biggest",
    // The single most extreme trade on the side, in R and dollars — the tail the
    // medians don't show.
    cell: (c) => {
      const row = c.row.original;
      return (
        <span className={tone(row.best_r)}>
          {sign(row.best_r)}R <span className="muted">/ {fmt(row.best_pnl)}</span>
        </span>
      );
    },
  },
  {
    accessorKey: "top3_share",
    header: "Top-3 share",
    // What fraction of the side's P&L its three most extreme trades carried. Near
    // 100% on Winners means the green is a handful of trades — one outlier from flat.
    cell: (c) => pctFrac(c.getValue() as number | null),
  },
  {
    accessorKey: "med_hold_s",
    header: "Med hold",
    cell: (c) => fmtHold(c.getValue() as number),
  },
];

function WinLossBreakdown({ profile }: { profile: WinLossProfile }) {
  const s = profile.summary;
  return (
    <div className="panel">
      <div className="section-cap">Winners vs losers (distribution)</div>
      <DataTable data={profile.sides ?? []} columns={winLossColumns} />
      {s && (
        <div
          style={{
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
            margin: "8px 0 2px",
            fontSize: 12,
          }}
        >
          <Stat label="Profit factor" value={fmt(s.profit_factor, false)} />
          <Stat label="Payoff ratio" value={fmt(s.payoff_ratio, false)} />
          <Stat label="Expectancy" value={`${sign(s.expectancy_r)}R`} tone={s.expectancy_r} />
          <Stat label="Max win streak" value={fmtInt(s.max_win_streak)} />
          <Stat label="Max loss streak" value={fmtInt(s.max_loss_streak)} />
          <Stat label="Worst drawdown" value={fmt(s.max_drawdown)} tone={s.max_drawdown} />
        </div>
      )}
      <div className="section-cap" style={{ marginTop: 6 }}>
        The one split the bucket cuts never make — what <strong>won</strong> against what{" "}
        <strong>lost</strong>, by shape not middle. <strong>Biggest</strong> is the single fattest
        trade on the side; <strong>Top-3 share</strong> is the fraction of the side's P&amp;L its three
        most extreme trades carried — near 100% on winners means a handful of trades are the whole
        edge. <strong>Profit factor</strong> is gross won ÷ gross lost, <strong>payoff ratio</strong>{" "}
        avg-win-R ÷ avg-loss-R — the two numbers a win rate hides.{" "}
        <strong>Worst drawdown</strong> is the deepest the cumulative net ever sank from its peak, in
        the order the trades were booked. A description of the book, not a filter.
      </div>
    </div>
  );
}

function Stat({ label, value, tone: t }: { label: string; value: string; tone?: number }) {
  return (
    <span>
      <span className="muted">{label}:</span>{" "}
      <strong className={t == null ? undefined : tone(t)}>{value}</strong>
    </span>
  );
}

// The R-outcome distribution as a bar strip — the shape the win/loss summary
// flattens into two means. On a fixed-stop/target engine the picture is bimodal:
// a wall of stops at ≤ -1R and a spike at the target bucket. Bar length is the
// share of trades; the money each bucket holds is printed beside it (a fat count
// of small-R winners can carry less than a thin count of target hits).
function RHistogram({ bins }: { bins: RHistBin[] }) {
  const maxShare = Math.max(...bins.map((b) => b.share), 0.0001);
  return (
    <div className="panel">
      <div className="section-cap">R-outcome distribution</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "4px 0" }}>
        {bins.map((b) => (
          <div key={b.bucket} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ width: 64, textAlign: "right", flexShrink: 0 }}>{b.bucket}</span>
            <div style={{ flex: 1, background: "var(--panel-2, #1a1a1a)", borderRadius: 3, height: 16 }}>
              <div
                style={{
                  width: `${(b.share / maxShare) * 100}%`,
                  height: "100%",
                  borderRadius: 3,
                  background: b.net_pnl >= 0 ? "var(--pos, #3fb950)" : "var(--neg, #f85149)",
                  opacity: 0.7,
                  minWidth: b.trades > 0 ? 2 : 0,
                }}
              />
            </div>
            <span style={{ width: 92, flexShrink: 0 }}>
              {fmtInt(b.trades)} <span className="muted">({fmtPct(b.share * 100, 0)})</span>
            </span>
            <span style={{ width: 88, textAlign: "right", flexShrink: 0 }} className={tone(b.net_pnl)}>
              {fmt(b.net_pnl)}
            </span>
          </div>
        ))}
      </div>
      <div className="section-cap" style={{ marginTop: 6 }}>
        Every trade's booked R, bucketed. The wall at <strong>≤ -1R</strong> is the stops; the spike
        at the target bucket is the wins that ran to it. Bar length is the share of trades, colored by
        whether the bucket made or lost money; the net beside it is where the P&amp;L actually sits — a
        wide band of small winners can hold less than a narrow one of full target hits.
      </div>
    </div>
  );
}

// What separated winners from losers at the fill — the one framing the outcome
// cuts can't give, because these fields were all facts before it. Winner-mean vs
// loser-mean per feature, with AUC (P a random winner's value tops a random
// loser's; 50% = no separation) and the same permutation floor the cuts carry.
// Zero-variance fields (a fixed stop, a fixed size) are dropped server-side.
const fmtMean = (v: number, unit: string) =>
  `${v.toFixed(1)}${unit === "contracts" ? "" : " " + unit}`;

function DiscriminatorBreakdown({ disc }: { disc: Discriminator }) {
  if (disc.rows.length === 0)
    return (
      <div className="panel">
        <div className="section-cap">What separated winners from losers (at entry)</div>
        <div className="notice">
          No entry feature varied enough to sort the outcomes — this run's stop and size are fixed, so
          geometry alone can't tell a winner from a loser here. That's the honest read, not a gap in
          the data: the difference is in the tape, not the setup.
        </div>
      </div>
    );

  const columns: ColumnDef<DiscriminatorRow, any>[] = [
    {
      accessorKey: "feature",
      header: "Entry feature",
      cell: (c) => {
        const row = c.row.original;
        return (
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {row.feature}
            {row.holds && (
              <span className="badge badge-sm" title="Separation survives the multiple-testing bar">
                holds
              </span>
            )}
          </span>
        );
      },
    },
    {
      accessorKey: "win_mean",
      header: "Winners",
      cell: (c) => <span className="pos">{fmtMean(c.getValue() as number, c.row.original.unit)}</span>,
    },
    {
      accessorKey: "loss_mean",
      header: "Losers",
      cell: (c) => <span className="neg">{fmtMean(c.getValue() as number, c.row.original.unit)}</span>,
    },
    {
      accessorKey: "auc",
      header: "Separation",
      // Distance from 50% is the whole signal; dim the rows sitting near it so a
      // real gap (if any) stands out. >50% = winners ran larger on this field.
      cell: (c) => {
        const auc = c.getValue() as number;
        const strong = Math.abs(auc - 0.5) >= 0.1;
        return (
          <span className={strong ? undefined : "muted"} title="P(a random winner's value > a random loser's)">
            {(auc * 100).toFixed(0)}%
          </span>
        );
      },
    },
  ];

  return (
    <div className="panel">
      <div className="section-cap">What separated winners from losers (at entry)</div>
      <DataTable data={disc.rows} columns={columns} />
      <div className="section-cap" style={{ marginTop: 6 }}>
        Each field was a fact <strong>before the fill</strong>, so a gap between what winners and
        losers carried is a filter you could have traded — the point the outcome cuts can't make.{" "}
        <strong>Separation</strong> is the chance a random winner's value tops a random loser's: 50%
        is nothing, and the further from it the harder the field sorts the two. A{" "}
        <span className="badge badge-sm">holds</span> badge means the gap clears the{" "}
        {(disc.luck_bar * 100).toFixed(1)}% permutation bar over {disc.n_win} winners and {disc.n_loss}{" "}
        losers — a shortlist for the next experiment, not a filter to apply to this table.
      </div>
    </div>
  );
}

// The book rolled up to sessions — the risk view a trade-level table can't show.
// A negative median day next to a positive average is the signature of a book
// carried by a few big sessions; top-3 share and the worst day put numbers on it,
// and the strip shows the clustering trade-for-trade.
function DailyBreakdown({ daily }: { daily: DailyConcentration }) {
  const maxAbs = Math.max(...daily.series.map((d) => Math.abs(d.net)), 1);
  return (
    <div className="panel">
      <div className="section-cap">Daily concentration (session-level risk)</div>
      <div
        style={{ display: "flex", gap: 16, flexWrap: "wrap", margin: "4px 0 8px", fontSize: 12 }}
      >
        <Stat label="Sessions" value={fmtInt(daily.days)} />
        <Stat label="Green days" value={fmtPct(daily.green_share * 100, 0)} />
        <Stat label="Avg day" value={fmt(daily.avg_day)} tone={daily.avg_day} />
        <Stat label="Median day" value={fmt(daily.med_day)} tone={daily.med_day} />
        <Stat label={`Best (${daily.best_date})`} value={fmt(daily.best_day)} tone={daily.best_day} />
        <Stat label={`Worst (${daily.worst_date})`} value={fmt(daily.worst_day)} tone={daily.worst_day} />
        <Stat label="Top-3 days' share" value={pctFrac(daily.top3_share)} />
      </div>
      {/* One bar per session, height ∝ |net|, colored by sign — the clustering the
          summary numbers describe, shown directly. Scrolls if the run is long. */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 1, height: 44, overflowX: "auto" }}>
        {daily.series.map((d) => (
          <div
            key={d.date}
            title={`${d.date}: ${fmt(d.net)}`}
            style={{
              width: 3,
              flexShrink: 0,
              height: `${Math.max((Math.abs(d.net) / maxAbs) * 100, 3)}%`,
              background: d.net >= 0 ? "var(--pos, #3fb950)" : "var(--neg, #f85149)",
              opacity: 0.75,
            }}
          />
        ))}
      </div>
      <div className="section-cap" style={{ marginTop: 6 }}>
        The book by session. <strong>Top-3 days' share</strong> is how much of the net the three best
        days carried — the daily twin of the winners' tail concentration above. A{" "}
        <strong>median day</strong> below the <strong>average day</strong> means a few big sessions are
        doing the work while the typical day bleeds; the <strong>worst day</strong> is the number a
        dollar drawdown is built from. Each bar is one session, sized by its net and colored by sign.
      </div>
    </div>
  );
}

// The MFE/MAE profile — what each trade was ever worth against what it booked,
// split by outcome. Read as a check on the exit, never a filter: MFE and MAE are
// facts only after the fill. Winners that peak far above what they keep, and
// losers that never peak at all, are two different exit problems.
const pctFrac = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : fmtPct(v * 100, digits);

const excursionColumns: ColumnDef<ExcursionRow, any>[] = [
  { accessorKey: "bucket", header: "Outcome", cell: (c) => String(c.getValue()) },
  { accessorKey: "trades", header: "Trades", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "mfe_r",
    header: "MFE (med)",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={tone(v)}>{sign(v)}R</span>;
    },
  },
  {
    accessorKey: "mae_r",
    header: "MAE (med)",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={tone(v)}>{sign(v)}R</span>;
    },
  },
  {
    accessorKey: "capture",
    header: "Capture",
    // Of the peak the trade showed, how much the exit kept. Dim it where no trade
    // in the group was ever in profit (nothing to capture a fraction of).
    cell: (c) => {
      const v = c.getValue() as number | null;
      return v == null ? <span className="muted">—</span> : pctFrac(v);
    },
  },
  { accessorKey: "reach_1r", header: "Reached +1R", cell: (c) => pctFrac(c.getValue() as number) },
  { accessorKey: "heat_1r", header: "Took −1R heat", cell: (c) => pctFrac(c.getValue() as number) },
];

function ExcursionBreakdown({ rows }: { rows: ExcursionRow[] }) {
  // Rendered with DataTable directly rather than through EdgeTable: an excursion
  // row is not an EdgeRow (it has no net_pnl/win_rate/expectancy), and loosening the
  // shared table's contract to admit it would weaken every other caller's typing.
  return (
    <div className="panel">
      <div className="section-cap">MFE / MAE profile (exit efficiency)</div>
      <DataTable data={rows} columns={excursionColumns} />
      <div className="section-cap" style={{ marginTop: 6 }}>
        How far each trade ever ran <strong>in favor</strong> before it was booked (MFE) against the
        worst heat it sat through (MAE), in R, split by outcome — measured off the ticks over each
        trade's own life. <strong>Capture</strong> is the median fraction of the peak the exit
        actually kept: low means a trail or target is handing open profit back.{" "}
        <strong>Reached +1R</strong> is the share that ever got that far in favor — losers that
        rarely do were never really working, so no breakeven rule saves them.{" "}
        <strong>Took −1R heat</strong> is the share that sat through a full stop's worth against;
        winners doing so are surviving on luck the stop should have cut. Read it as a description of
        the exits, not a filter — MFE and MAE are known only after the fill.
      </div>
    </div>
  );
}

// The per-confluence veto breakdown — the answer to "what is each gate actually
// doing" when several are stacked. Every gate that would have rejected a ghost
// entry is scored on it (not just the first to fire, as by_gate does), so the
// rows overlap: a trade two gates both blocked counts under each, and the Vetoed
// column sums to more than the ghost total. Unique = caught alone.
const confluenceColumns: ColumnDef<ConfluenceRow, any>[] = [
  { accessorKey: "bucket", header: "Confluence", cell: (c) => String(c.getValue()) },
  { accessorKey: "trades", header: "Vetoed", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "unique",
    header: "Unique",
    cell: (c) => {
      const v = c.getValue() as number;
      // A gate that never catches anything alone is redundant with the rest of the
      // stack — dim the zero so the gates pulling their own weight stand out.
      return v === 0 ? <span className="muted">0</span> : fmtInt(v);
    },
  },
  {
    accessorKey: "net_pnl",
    header: "Ghost net",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={tone(v)}>{fmt(v)}</span>;
    },
  },
  { accessorKey: "win_rate", header: "Win rate", cell: (c) => fmtPct(c.getValue() as number) },
  {
    accessorKey: "avg_r",
    header: "Avg R",
    cell: (c) => {
      const v = c.getValue() as number;
      return <span className={tone(v)}>{sign(v)}R</span>;
    },
  },
];

function ConfluenceBreakdown({ rows }: { rows: ConfluenceRow[] }) {
  return (
    <EdgeTable
      title="Per-confluence veto breakdown (independent)"
      data={rows}
      columns={confluenceColumns}
      caption={
        <span>
          What each gate would veto <strong>on its own</strong>, not just the entries earlier gates
          let through (that's the <em>Which gate vetoed it</em> cut below). Rows overlap — a trade
          two gates both blocked counts under each, so <strong>Vetoed</strong> sums past the ghost
          total. <strong>Unique</strong> is how many a gate caught alone: high means it earns its
          slot, low means it's redundant with the stack. <strong>Ghost net</strong> is the would-be
          P&amp;L of those cut trades — <span className="pos">positive</span> means the gate filtered
          winners, <span className="neg">negative</span> means it saved you the loss.
        </span>
      }
    />
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
