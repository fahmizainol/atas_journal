import { useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { useFilters } from "../hooks/useFilters";
import { useTrades } from "../hooks/useTrades";
import {
  type ReviewCut,
  cutIsEmpty,
  matchesCut,
  useReviewCut,
} from "../hooks/useReviewCut";
import { DataTable } from "../components/DataTable";
import { edgeColumns } from "../components/EdgeTable";
import {
  DISCIPLINE_LABEL,
  GRADE_COLOR,
  SETUP_LABEL,
} from "../components/charts/ReviewCard";
import { fmt, fmtInt } from "../lib/format";
import type { EdgeRow, TradeRow } from "../lib/types";

// The top-down read of the reviewed trades — the page the 2026-08-31 axis
// redesign exists for. The axes made per-axis questions *answerable*
// ("how do my fades do?"); this page is where they get asked. Three surfaces
// over one set of rows:
//
//   facet rail — every answer with a live count; clicking narrows the cut
//   matrix     — setup × discipline, the two enumerated axes crossed
//   ledger     — the cut's trades grouped by any one axis, EdgeTable-style
//
// All of it is computed CLIENT-side from the `/trades` rows the FilterBar
// scope already returns (review attached per row): a facet's count needs the
// rows with every axis applied except its own, which no single server
// aggregate could carry — and at this journal's size the rows are already
// here. The cut lives in the URL (useReviewCut) and is honored by the Trades
// table too, so any group on this page is one hop from its individual trades.
//
// GRADES ARE SPLIT BY ERA, always. A grade picked with the P&L on screen
// restates the outcome (every pre-blind A and B won, every D lost — the join
// that forced the redesign), so hindsight grades never enter a grade
// aggregate by default; they are shown counted, marked †, behind a toggle.

interface Agg {
  n: number;
  wins: number;
  losses: number;
  net: number;
  grossWin: number;
  grossLoss: number;
}

const AGG0: Agg = { n: 0, wins: 0, losses: 0, net: 0, grossWin: 0, grossLoss: 0 };

function accumulate(agg: Agg, r: TradeRow): Agg {
  const pnl = r.net_pnl;
  return {
    n: agg.n + 1,
    wins: agg.wins + (pnl > 0 ? 1 : 0),
    losses: agg.losses + (pnl < 0 ? 1 : 0),
    net: agg.net + pnl,
    grossWin: agg.grossWin + (pnl > 0 ? pnl : 0),
    grossLoss: agg.grossLoss + (pnl < 0 ? pnl : 0),
  };
}

/** An EdgeRow plus the averages the journal's Edges tab doesn't carry — on a
 * review cut the asymmetry (small winners, one huge loser) is often the whole
 * finding. */
interface LedgerRow extends EdgeRow {
  avg_win: number | null;
  avg_loss: number | null;
}

function toLedgerRow(bucket: string, agg: Agg): LedgerRow {
  return {
    bucket,
    trades: agg.n,
    net_pnl: agg.net,
    // As a percent, not a fraction — edgeColumns' fmtPct prints the number
    // as-is, matching the server's own EdgeRows.
    win_rate: agg.n ? (agg.wins / agg.n) * 100 : 0,
    expectancy: agg.n ? agg.net / agg.n : 0,
    avg_win: agg.wins ? agg.grossWin / agg.wins : null,
    avg_loss: agg.losses ? agg.grossLoss / agg.losses : null,
  };
}

const ledgerColumns: ColumnDef<LedgerRow, any>[] = [
  ...(edgeColumns as ColumnDef<LedgerRow, any>[]),
  {
    accessorKey: "avg_win",
    header: "Avg win",
    cell: (c) => {
      const v = c.getValue() as number | null;
      return v == null ? <span className="muted">—</span> : <span className="pos">{fmt(v)}</span>;
    },
  },
  {
    accessorKey: "avg_loss",
    header: "Avg loss",
    cell: (c) => {
      const v = c.getValue() as number | null;
      return v == null ? <span className="muted">—</span> : <span className="neg">{fmt(v)}</span>;
    },
  },
];

/** Group `rows` by `key`, in `order` when given (vocab order for the enumerated
 * axes), by net descending otherwise. Rows the key doesn't reach land in an
 * "(unanswered)" bucket at the bottom — the review debt is part of the
 * top-down read, not something to silently drop. */
function groupBy(
  rows: TradeRow[],
  key: (r: TradeRow) => string | null,
  label: (id: string) => string,
  order?: readonly string[],
): LedgerRow[] {
  const aggs = new Map<string, Agg>();
  let missing = AGG0;
  for (const r of rows) {
    const k = key(r);
    if (k == null) {
      missing = accumulate(missing, r);
      continue;
    }
    aggs.set(k, accumulate(aggs.get(k) ?? AGG0, r));
  }
  const ids = order
    ? order.filter((id) => aggs.has(id))
    : [...aggs.keys()].sort((a, b) => (aggs.get(b)!.net - aggs.get(a)!.net));
  const out = ids.map((id) => toLedgerRow(label(id), aggs.get(id)!));
  if (missing.n) out.push(toLedgerRow("(unanswered)", missing));
  return out;
}

const SETUP_IDS = Object.keys(SETUP_LABEL);
const DISCIPLINE_IDS = Object.keys(DISCIPLINE_LABEL);
const GRADE_IDS = ["A", "B", "C", "D"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type CutBy = "setup" | "discipline" | "grade" | "level" | "hour" | "weekday";

const CUT_BY: { id: CutBy; label: string }[] = [
  { id: "setup", label: "Setup" },
  { id: "discipline", label: "Discipline" },
  { id: "grade", label: "Grade" },
  { id: "level", label: "Watched level" },
  { id: "hour", label: "Hour" },
  { id: "weekday", label: "Weekday" },
];

function hourOf(r: TradeRow): string | null {
  const m = /[T ](\d{2}):/.exec(r.entry_ts_local ?? "");
  return m ? `${m[1]}:00` : null;
}

function weekdayOf(r: TradeRow): string | null {
  const d = new Date(r.entry_ts_local);
  return isNaN(d.getTime()) ? null : WEEKDAYS[(d.getDay() + 6) % 7];
}

/** The ledger's rows for one grouping axis. Levels fan out — a trade watching
 * two levels counts under both, which the caption owns up to. */
function ledgerFor(rows: TradeRow[], by: CutBy): LedgerRow[] {
  switch (by) {
    case "setup":
      return groupBy(rows, (r) => r.setup ?? null, (id) => SETUP_LABEL[id] ?? id, SETUP_IDS);
    case "discipline":
      return groupBy(
        rows, (r) => r.discipline ?? null,
        (id) => DISCIPLINE_LABEL[id] ?? id, DISCIPLINE_IDS,
      );
    case "grade":
      return groupBy(rows, (r) => r.grade ?? null, (id) => id, GRADE_IDS);
    case "level": {
      const aggs = new Map<string, Agg>();
      let missing = AGG0;
      for (const r of rows) {
        const labels = r.watched_labels ?? [];
        if (!labels.length) {
          missing = accumulate(missing, r);
          continue;
        }
        for (const l of labels) aggs.set(l, accumulate(aggs.get(l) ?? AGG0, r));
      }
      const out = [...aggs.entries()]
        .sort((a, b) => b[1].net - a[1].net)
        .map(([l, agg]) => toLedgerRow(l, agg));
      if (missing.n) out.push(toLedgerRow("(unanswered)", missing));
      return out;
    }
    case "hour":
      return groupBy(rows, hourOf, (id) => id).sort((a, b) =>
        a.bucket.localeCompare(b.bucket),
      );
    case "weekday":
      return groupBy(rows, weekdayOf, (id) => id, WEEKDAYS);
  }
}

// --- facet rail -------------------------------------------------------------

function FacetButton({
  active, label, count, color, onClick, title,
}: {
  active: boolean;
  label: React.ReactNode;
  count: number;
  color?: string;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      className={`facet${active ? " active" : ""}`}
      onClick={onClick}
      disabled={!active && count === 0}
      title={title}
      data-review-facet
    >
      <span style={color ? { color } : undefined}>{label}</span>
      <span className="facet-n">{fmtInt(count)}</span>
    </button>
  );
}

function FacetRail({
  rows, cut, toggle, setState,
}: {
  rows: TradeRow[];
  cut: ReviewCut;
  toggle: ReturnType<typeof useReviewCut>["toggle"];
  setState: (s: ReviewCut["state"]) => void;
}) {
  // Each facet counts what clicking it would show: every axis applied except
  // its own. That is the whole reason the rows are aggregated client-side.
  const forAxis = (omit: keyof ReviewCut) => rows.filter((r) => matchesCut(r, cut, omit));

  const stateRows = forAxis("state");
  const setupRows = forAxis("setups");
  const discRows = forAxis("disciplines");
  const gradeRows = forAxis("grades");
  const levelRows = forAxis("levels");

  const count = (xs: TradeRow[], p: (r: TradeRow) => boolean) => xs.filter(p).length;

  const levelCounts = new Map<string, number>();
  for (const r of levelRows)
    for (const l of r.watched_labels ?? [])
      levelCounts.set(l, (levelCounts.get(l) ?? 0) + 1);
  const levelIds = [...levelCounts.entries()].sort((a, b) => b[1] - a[1]);

  return (
    <div className="facet-rail" data-review-rail>
      <div className="facet-group">
        <div className="facet-head">Reviewed</div>
        {(
          [
            ["reviewed", "reviewed", "Passes the full gate: level + setup + discipline"],
            ["owed", "owes review",
             "Started at any age, or from the grading era (2026-08-20) onward"],
            ["history", "history",
             "Untouched and pre-era — not owed, not faked, still here"],
          ] as const
        ).map(([id, label, title]) => (
          <FacetButton
            key={id}
            active={cut.state === id}
            label={label}
            count={count(stateRows, (r) => r.review_state === id)}
            title={title}
            onClick={() => setState(cut.state === id ? "" : id)}
          />
        ))}
      </div>
      <div className="facet-group">
        <div className="facet-head">Setup</div>
        {SETUP_IDS.map((s) => (
          <FacetButton
            key={s}
            active={cut.setups.includes(s)}
            label={SETUP_LABEL[s]}
            count={count(setupRows, (r) => r.setup === s)}
            onClick={() => toggle("setups", s)}
          />
        ))}
      </div>
      <div className="facet-group">
        <div className="facet-head">Discipline</div>
        {DISCIPLINE_IDS.map((d) => (
          <FacetButton
            key={d}
            active={cut.disciplines.includes(d)}
            label={DISCIPLINE_LABEL[d]}
            count={count(discRows, (r) => r.discipline === d)}
            onClick={() => toggle("disciplines", d)}
          />
        ))}
      </div>
      <div className="facet-group">
        <div className="facet-head">Grade</div>
        {GRADE_IDS.map((g) => {
          const blind = count(gradeRows, (r) => r.grade === g && !!r.grade_blind);
          const hind = count(gradeRows, (r) => r.grade === g && !r.grade_blind);
          return (
            <FacetButton
              key={g}
              active={cut.grades.includes(g)}
              label={
                <>
                  {g}
                  {hind > 0 && <span className="muted"> · {hind}†</span>}
                </>
              }
              count={blind}
              color={GRADE_COLOR[g]}
              title={hind > 0 ? `${hind} hindsight grade${hind === 1 ? "" : "s"} (†) not in the count` : undefined}
              onClick={() => toggle("grades", g)}
            />
          );
        })}
      </div>
      <div className="facet-group">
        <div className="facet-head">Watched level</div>
        {levelIds.map(([l, n]) => (
          <FacetButton
            key={l}
            active={cut.levels.includes(l)}
            label={l}
            count={n}
            onClick={() => toggle("levels", l)}
          />
        ))}
        {levelIds.length === 0 && <div className="muted">none answered</div>}
      </div>
    </div>
  );
}

// --- setup × discipline matrix ----------------------------------------------

function Matrix({
  rows, cut, patch,
}: {
  rows: TradeRow[];
  cut: ReviewCut;
  patch: ReturnType<typeof useReviewCut>["patch"];
}) {
  const cells = new Map<string, Agg>();
  for (const r of rows) {
    if (!r.setup || !r.discipline) continue;
    const k = `${r.setup}|${r.discipline}`;
    cells.set(k, accumulate(cells.get(k) ?? AGG0, r));
  }
  if (cells.size === 0)
    return <div className="muted">No trade in the cut has both axes answered yet.</div>;
  // Only the columns/rows that occur — six by six of mostly-empty cells is a
  // wall, and the empty ones say nothing "0" doesn't.
  const usedD = DISCIPLINE_IDS.filter((d) =>
    SETUP_IDS.some((s) => cells.has(`${s}|${d}`)));
  const usedS = SETUP_IDS.filter((s) =>
    usedD.some((d) => cells.has(`${s}|${d}`)));
  return (
    <div className="table-scroll-x">
      <table className="review-matrix" data-review-matrix>
        <thead>
          <tr>
            <th />
            {usedD.map((d) => <th key={d}>{DISCIPLINE_LABEL[d]}</th>)}
          </tr>
        </thead>
        <tbody>
          {usedS.map((s) => (
            <tr key={s}>
              <th>{SETUP_LABEL[s]}</th>
              {usedD.map((d) => {
                const agg = cells.get(`${s}|${d}`);
                const on = cut.setups.includes(s) && cut.disciplines.includes(d);
                return (
                  <td key={d}>
                    {agg ? (
                      <button
                        type="button"
                        className={`matrix-cell${on ? " active" : ""}`}
                        title={`${SETUP_LABEL[s]} · ${DISCIPLINE_LABEL[d]} — click to cut to these trades`}
                        onClick={() =>
                          patch(on ? { setups: [], disciplines: [] }
                                   : { setups: [s], disciplines: [d] })
                        }
                      >
                        <span className={agg.net >= 0 ? "pos" : "neg"}>{fmt(agg.net)}</span>
                        <span className="matrix-n">{agg.n}×</span>
                      </button>
                    ) : (
                      <span className="muted">·</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- the page ---------------------------------------------------------------

export function Review() {
  const { scope } = useFilters();
  // Force the logical view: the review binds to the logical trade, and an ATAS
  // scope would count one review once per lot.
  const logicalScope = useMemo(() => ({ ...scope, view: "logical" }), [scope]);
  const { data, isLoading } = useTrades(logicalScope);
  const { cut, patch, toggle, setState, clear } = useReviewCut();
  const [cutBy, setCutBy] = useState<CutBy>("setup");
  const [withHindsight, setWithHindsight] = useState(false);

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.length === 0)
    return <div className="notice">No trades to display.</div>;

  const inCut = data.filter((r) => matchesCut(r, cut));

  // The grade axis never blends the eras: hindsight grades leave before any
  // grade aggregate unless explicitly invited, and even then they are the
  // reader's own risk — the toggle label says why.
  const hindsightGraded = inCut.filter((r) => r.grade && !r.grade_blind).length;
  const ledgerRows =
    cutBy === "grade" && !withHindsight
      ? inCut.filter((r) => !r.grade || r.grade_blind)
      : inCut;
  const ledger = ledgerFor(ledgerRows, cutBy);

  const reviewed = inCut.filter((r) => r.review_state === "reviewed").length;
  const owedInCut = inCut.filter((r) => r.review_state === "owed").length;
  const history = inCut.filter((r) => r.review_state === "history").length;

  return (
    <div className="review-page" data-review-page>
      <div className="section-title">Review</div>
      <div className="section-cap">
        The reviewed trades, top-down. Facets narrow the cut (URL-shared with
        the Trades table); the matrix crosses the two answered axes; the ledger
        groups whatever survives. In this cut: {fmtInt(reviewed)} reviewed ·{" "}
        {fmtInt(owedInCut)} owe review · {fmtInt(history)} pre-era history.
      </div>
      <div className="review-layout">
        <FacetRail rows={data} cut={cut} toggle={toggle} setState={setState} />
        <div className="review-main">
          {!cutIsEmpty(cut) && (
            <div className="review-cut-bar">
              <span className="review-cut-label">
                Cut to {fmtInt(inCut.length)} trade{inCut.length === 1 ? "" : "s"}
              </span>
              <button type="button" className="review-cut-clear" onClick={clear}>
                clear
              </button>
            </div>
          )}
          <div className="panel">
            <div className="section-cap">Setup × discipline</div>
            <Matrix rows={inCut} cut={cut} patch={patch} />
          </div>
          <div className="panel">
            <div className="review-ledger-head">
              <div className="section-cap">Ledger</div>
              <div className="radio-group">
                {CUT_BY.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className={cutBy === c.id ? "active" : ""}
                    onClick={() => setCutBy(c.id)}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>
            {cutBy === "grade" && (
              <div className="section-cap" data-review-grade-era>
                {hindsightGraded > 0 ? (
                  <label title="Pre-2026-08-31 grades were picked with the P&L on screen — they restate the outcome, not the decision.">
                    <input
                      type="checkbox"
                      checked={withHindsight}
                      onChange={(e) => setWithHindsight(e.target.checked)}
                    />{" "}
                    include {fmtInt(hindsightGraded)} hindsight grade
                    {hindsightGraded === 1 ? "" : "s"}† — they restate the
                    outcome, not the decision
                  </label>
                ) : (
                  <span>Blind grades only — answered at the recall front.</span>
                )}
              </div>
            )}
            {ledger.length === 0 ? (
              <div className="muted">Nothing to group.</div>
            ) : (
              <div className="table-scroll-x">
                <DataTable data={ledger} columns={ledgerColumns} />
              </div>
            )}
            {cutBy === "level" && (
              <div className="section-cap" style={{ marginTop: 6 }}>
                A trade watching two levels counts under both — the rows overlap
                and do not sum to the cut.
              </div>
            )}
            <div className="section-cap" style={{ marginTop: 6 }}>
              Buckets describe this cut; small n proves nothing. Win rate is
              net&gt;0; expectancy is net ÷ trades.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
