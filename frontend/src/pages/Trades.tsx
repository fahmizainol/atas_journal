import { type ColumnDef } from "@tanstack/react-table";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useFilters } from "../hooks/useFilters";
import { useTrades } from "../hooks/useTrades";
import { cutIsEmpty, matchesCut, useReviewCut } from "../hooks/useReviewCut";
import { DataTable } from "../components/DataTable";
import { TradeDetail } from "../components/TradeDetail";
import { BadgeList } from "../components/BadgeInput";
import {
  DISCIPLINE_LABEL,
  GRADE_COLOR,
  SETUP_LABEL,
} from "../components/charts/ReviewCard";
import { fmt, fmtDateTime, fmtInt, fmtTime } from "../lib/format";
import type { TradeRow } from "../lib/types";

const REVIEW_STATES = [
  ["", "All"],
  ["reviewed", "Reviewed"],
  ["owed", "Owes review"],
  ["history", "History"],
] as const;

/** The review cut, right where the table it narrows is. The state gets a
 * standing control (finding the trades that owe their review is a first-class
 * reason to open this page — each one's day replay, indicators and all, is
 * where the review gets written); the other axes render as removable chips,
 * since they are usually set from the Review page's facets and only need to be
 * visible and droppable here. Not folded into the FilterBar: the bar's fields
 * are the server scope, this is the client-side slice. */
function CutBar() {
  const { cut, toggle, setState, clear } = useReviewCut();
  const chip = (label: string, onRemove: () => void) => (
    <button
      key={label}
      type="button"
      className="review-cut-chip"
      onClick={onRemove}
      title="Remove this from the cut"
    >
      {label} ×
    </button>
  );
  const axisChips =
    cut.setups.length + cut.disciplines.length + cut.grades.length + cut.levels.length > 0;
  return (
    <div className="review-cut-bar" data-review-cut-bar>
      <span className="review-cut-label">Review:</span>
      <div className="radio-group">
        {REVIEW_STATES.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={cut.state === id ? "active" : ""}
            data-review-state-pick={id || "all"}
            onClick={() => setState(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {cut.setups.map((s) => chip(SETUP_LABEL[s] ?? s, () => toggle("setups", s)))}
      {cut.disciplines.map((d) =>
        chip(DISCIPLINE_LABEL[d] ?? d, () => toggle("disciplines", d)),
      )}
      {cut.grades.map((g) => chip(`grade ${g}`, () => toggle("grades", g)))}
      {cut.levels.map((l) => chip(l, () => toggle("levels", l)))}
      {(axisChips || cut.state) && (
        <button type="button" className="review-cut-clear" onClick={clear}>
          clear
        </button>
      )}
    </div>
  );
}

/** One trade's review, table-cell sized: setup · discipline · grade. Falls back
 * to the archived era's setup badges on rows that predate the review, so the
 * old sessions keep saying what they always said. */
function ReviewCell({ r }: { r: TradeRow }) {
  const parts: React.ReactNode[] = [];
  if (r.setup) parts.push(<span key="s">{SETUP_LABEL[r.setup] ?? r.setup}</span>);
  if (r.discipline)
    parts.push(
      <span key="d" className="muted">
        {DISCIPLINE_LABEL[r.discipline] ?? r.discipline}
      </span>,
    );
  if (r.grade)
    parts.push(
      <b
        key="g"
        style={{ color: GRADE_COLOR[r.grade], opacity: r.grade_blind ? 1 : 0.55 }}
        title={
          r.grade_blind
            ? "Graded blind at the recall front"
            : "Hindsight grade (pre-blind era) — it restates the outcome"
        }
      >
        {r.grade}
        {!r.grade_blind && "†"}
      </b>,
    );
  if (parts.length === 0) {
    if (r.setups?.length) return <BadgeList items={r.setups} />;
    return <span className="muted">—</span>;
  }
  return <span className="review-cell">{parts}</span>;
}

export function Trades() {
  const { scope } = useFilters();
  const { data, isLoading } = useTrades(scope);
  const { cut } = useReviewCut();
  const { tradeNo } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.length === 0)
    return <div className="notice">No trades to display.</div>;

  const rows = cutIsEmpty(cut) ? data : data.filter((r) => matchesCut(r, cut));

  const expanded = tradeNo ? Number(tradeNo) : null;
  // "narrow-hide" drops a column below 640px, where eleven nowrap columns are
  // three screens of sideways scroll. What survives is what identifies a trade
  // and what this page is for: #, when, what it made, and its review — the
  // hidden columns all reappear in the expanded detail's header cards.
  const columns: ColumnDef<TradeRow, any>[] = [
    { accessorKey: "trade_no", header: "#", cell: (c) => `#${c.getValue()}` },
    { accessorKey: "instrument", header: "Instrument", meta: { className: "narrow-hide" } },
    { accessorKey: "direction", header: "Dir", meta: { className: "narrow-hide" } },
    {
      accessorKey: "max_contracts",
      header: "Qty",
      meta: { className: "narrow-hide" },
      cell: (c) => fmtInt(c.getValue() as number),
    },
    {
      accessorKey: "entry_ts_local",
      header: "Entry",
      // cell-wrap so the phone stacks the date over the time instead of the
      // one full-width column forcing the whole table sideways. The date and
      // the time are each pinned nowrap, or the break lands on a hyphen
      // ("2024-03-" / "25") instead of on the space between them.
      meta: { className: "cell-wrap" },
      cell: (c) => {
        const [d, t] = fmtDateTime(c.getValue() as string).split(" ");
        return (
          <>
            <span style={{ whiteSpace: "nowrap" }}>{d}</span>{" "}
            <span style={{ whiteSpace: "nowrap" }}>{t}</span>
          </>
        );
      },
    },
    {
      accessorKey: "exit_ts_local",
      header: "Exit",
      meta: { className: "narrow-hide" },
      cell: (c) => fmtTime(c.getValue() as string),
    },
    {
      id: "hold",
      header: "Hold",
      accessorFn: (r) => r.duration_s,
      meta: { className: "narrow-hide" },
      cell: (c) => `${((c.getValue() as number) / 60).toFixed(1)}m`,
    },
    {
      accessorKey: "avg_entry",
      header: "Avg entry",
      meta: { className: "narrow-hide" },
      cell: (c) => fmt(c.getValue() as any, false),
    },
    {
      accessorKey: "avg_exit",
      header: "Avg exit",
      meta: { className: "narrow-hide" },
      cell: (c) => fmt(c.getValue() as any, false),
    },
    {
      accessorKey: "net_pnl",
      header: "Net PnL",
      cell: (c) => {
        const v = c.getValue() as number;
        return <span className={v >= 0 ? "pos" : "neg"}>{fmt(v)}</span>;
      },
    },
    {
      id: "review",
      header: "Review",
      enableSorting: false,
      // Wraps on a phone: the one cell whose content is words, and hiding it
      // would hide the axis this table is cut by.
      meta: { className: "cell-wrap" },
      cell: (c) => <ReviewCell r={c.row.original} />,
    },
  ];

  return (
    <div>
      <div className="section-title">Trades</div>
      <div className="section-cap">
        Click a row to expand its full detail. The Review page cuts this table
        by setup, discipline, grade and level; a trade that owes its review is
        finished from its day replay (open the trade → Replay this day).
      </div>
      <CutBar />
      {rows.length === 0 ? (
        <div className="notice">No trades survive the current cut.</div>
      ) : (
        <div className="panel table-scroll-x-narrow">
          <DataTable
            data={rows}
            columns={columns}
            rowKey={(r) => r.trade_no}
            initialSort={[{ id: "trade_no", desc: false }]}
            expandedKey={expanded}
            onExpandedChange={(key) =>
              navigate({
                pathname: key == null ? "/trades" : `/trades/${key}`,
                search,
              })
            }
            renderExpanded={(r) => <TradeDetail scope={scope} tradeNo={r.trade_no} />}
          />
        </div>
      )}
    </div>
  );
}
