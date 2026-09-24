// The same clicks under a different bracket.
//
// A sitting records exactly one set of exits — the stop, target and trail that
// were on the ticket that day. This prices its fills under others: entry times,
// sizes and timestamps held fixed, only the bracket changing. The server owns
// the ladder and the engine (`journal.replay_whatif`), which is a port of the
// same fill engine the replay itself ran on and refuses to answer at all unless
// it first reproduces the stored sitting to the dollar.
//
// Three rows at the foot of the ladder — and a checkbox in the builder — change
// one thing that is not an exit: they take the other *side* of every entry, with
// the placed legs mirrored so the risk in ticks is unchanged. It stays in this
// section rather than getting one of its own because it is a modifier on these
// same rows, not a different question: reversed-with-a-trail and
// reversed-set-and-forget are cells of the one grid.
//
// Two things this is not, and the footnote says both:
//
//   * It is not a base rate. Those entries were taken knowing what the tape had
//     just done, so a row is a readout on this sitting, not a strategy result.
//   * It is not live. Only replay and backtest sittings have an order log to
//     re-run; an imported prop day renders nothing here.
//
// It sits *below* the journal form on purpose — the same rule ReviewPanel
// states for its own numbers. Counterfactual PnL above the place where the day
// gets written up would turn a record of what you thought into a prompt for
// what to think.

import { useState } from "react";
import {
  attemptIdOf,
  useWhatIf,
  type WhatIfCell,
  type WhatIfGrid,
  type WhatIfRow,
} from "../hooks/useReplays";
import {
  EMPTY_SPEC,
  loadWhatIfRows,
  saveWhatIfRows,
  type WhatIfSpec,
} from "../lib/whatIfPrefs";
import { fmtInt } from "../lib/format";
import { KpiGrid } from "./KpiGrid";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";

const usd = (n: number) => `${n < 0 ? "-" : ""}$${fmtInt(Math.abs(n))}`;

const tone = (d: number) => (d > 0 ? "pos" : d < 0 ? "neg" : "muted");

/** n / win rate / how the trades ended — the detail behind one net. */
const cellTitle = (c: WhatIfCell) =>
  `${c.n} trades · ${c.wr}% won · avg win ${usd(c.avg_win)} / avg loss ${usd(c.avg_loss)}\n` +
  Object.entries(c.reasons)
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");

/** One leg of the bracket the row actually ran, measured at each fill.
 *
 *  A row that leaves a leg as it was placed rarely runs one distance: the stop is
 *  sized off the volatility ruler at every fill, and the target is stored as an
 *  absolute price, so how far it sits depends on where the fill landed. A row that
 *  overrides that leg is one number. Both columns run the same fills, so either
 *  cell answers — `clicked` unless it has no trades. */
function legCell(row: WhatIfRow, leg: "stop_ticks" | "target_ticks") {
  const s = row.clicked[leg] ?? row.forget[leg];
  if (!s) return <td className="num muted">—</td>;
  const flat = s.lo === s.hi;
  return (
    <td className="num" title={flat ? "every trade" : `median ${s.med}t across the sitting`}>
      {flat ? `${s.lo}t` : `${s.lo}–${s.hi}t`}
    </td>
  );
}

function Cell({ cell, base, anchor }: { cell: WhatIfCell; base: number; anchor: boolean }) {
  const d = cell.net - base;
  return (
    <td className="num" title={cellTitle(cell)}>
      <span className={tone(cell.net)}>{usd(cell.net)}</span>
      {!anchor && (
        <span className={tone(d)} style={{ marginLeft: 6, fontSize: 11 }}>
          {d > 0 ? "+" : ""}
          {usd(d)}
        </span>
      )}
    </td>
  );
}

function Row({ row, base, anchor, onRemove }: {
  row: WhatIfRow;
  base: { clicked: number; forget: number };
  anchor: boolean;
  onRemove?: () => void;
}) {
  return (
    <tr style={anchor ? { fontWeight: 600 } : undefined}>
      <td>
        {row.label}
        {anchor && <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>anchor</span>}
      </td>
      {legCell(row, "stop_ticks")}
      {legCell(row, "target_ticks")}
      <Cell cell={row.clicked} base={base.clicked} anchor={anchor} />
      <Cell cell={row.forget} base={base.forget} anchor={anchor} />
      <td>
        {onRemove && (
          <button type="button" className="btn-xs" onClick={onRemove} aria-label="Remove this row">
            ×
          </button>
        )}
      </td>
    </tr>
  );
}

/** The builder for a row of your own. Deliberately the same four fields the
 *  ticket has, in ticks, because that is the unit the bracket is set in — plus
 *  the one switch the ticket does not have, which takes the other side. */
function AddRow({ onAdd }: { onAdd: (spec: WhatIfSpec) => void }) {
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [unit, setUnit] = useState<"ticks" | "R">("ticks");
  const [trailMode, setTrailMode] = useState<"as-placed" | "none" | "set">("as-placed");
  const [dist, setDist] = useState("25");
  const [step, setStep] = useState("0");
  const [beOnly, setBeOnly] = useState(false);
  const [flip, setFlip] = useState(false);

  // A blank field means "leave it as it was placed"; so does a field with
  // nonsense in it, rather than sending a NaN the server has to reject.
  const num = (s: string) => {
    const n = Number(s.trim());
    return s.trim() === "" || !Number.isFinite(n) || n <= 0 ? null : n;
  };
  const tgt = num(target);

  const submit = () => {
    // A row that changes nothing is the as-played row again. Reversing it is a
    // change on its own, so it is enough by itself.
    if (!flip && num(stop) === null && tgt === null && trailMode === "as-placed") return;
    const spec: WhatIfSpec = {
      ...EMPTY_SPEC,
      flip,
      stop: num(stop),
      target: unit === "ticks" ? tgt : null,
      targetR: unit === "R" ? tgt : null,
      trail:
        trailMode === "as-placed"
          ? "as-placed"
          : trailMode === "none"
            ? null
            : { dist: Number(dist) || 25, step: Number(step) || 0, beOnly },
    };
    onAdd(spec);
    setStop("");
    setTarget("");
  };

  return (
    <div className="panel" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
      <label>
        Stop (ticks)
        <br />
        <input value={stop} onChange={(e) => setStop(e.target.value)} placeholder="as placed"
               style={{ width: 84 }} />
      </label>
      <label>
        Target
        <br />
        <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="as placed"
               style={{ width: 84 }} />
      </label>
      <label>
        in
        <br />
        <select value={unit} onChange={(e) => setUnit(e.target.value as "ticks" | "R")}>
          <option value="ticks">ticks</option>
          <option value="R">R</option>
        </select>
      </label>
      <label>
        Trail
        <br />
        <select value={trailMode}
                onChange={(e) => setTrailMode(e.target.value as "as-placed" | "none" | "set")}>
          <option value="as-placed">as placed</option>
          <option value="none">none</option>
          <option value="set">set…</option>
        </select>
      </label>
      {trailMode === "set" && (
        <>
          <label>
            Dist
            <br />
            <input value={dist} onChange={(e) => setDist(e.target.value)} style={{ width: 60 }} />
          </label>
          <label>
            Step
            <br />
            <input value={step} onChange={(e) => setStep(e.target.value)} style={{ width: 60 }} />
          </label>
          <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <input type="checkbox" checked={beOnly} onChange={(e) => setBeOnly(e.target.checked)} />
            BE only
          </label>
        </>
      )}
      <label style={{ display: "flex", gap: 4, alignItems: "center" }}
             title="Take the other side of every entry, at the same moments and sizes, with the placed stop and target mirrored across the fill">
        <input type="checkbox" checked={flip} onChange={(e) => setFlip(e.target.checked)} />
        Reversed
      </label>
      <button type="button" onClick={submit}>Add row</button>
    </div>
  );
}

/** The two winners, as cards, so the ladder's answer is readable without opening it.
 *
 *  One card per column because they are different questions and usually different
 *  rows: "As clicked" holds your hand flattens fixed and asks which bracket was
 *  best around them, "Set and forget" drops them and asks which bracket you should
 *  have placed and walked away from. The gap between the two cards is what your
 *  hand did to the day.
 *
 *  Reversed rows are eligible to win. A day you were simply wrong on direction
 *  should say so rather than crown the least-bad bracket for a losing side — but a
 *  reversed win is not a bracket you can place tomorrow, so the card says that
 *  where a normal winner would be showing its edge over As played.
 */
function WinnerCards({ data }: { data: WhatIfGrid | undefined }) {
  if (!data?.valid || data.rows.length === 0) return null;
  const anchor = data.rows[0];

  const card = (col: "clicked" | "forget", label: string): Card => {
    const best = bestRow(data.rows, col);
    const delta = best[col].net - anchor[col].net;
    return {
      label,
      value: usd(best[col].net),
      tone: toneOf(best[col].net),
      sub: best.spec.flip
        ? `${best.label} — the other side of every entry, so not a bracket you can place`
        : best.key === anchor.key
          ? "As played — nothing in the ladder beat it"
          : `${best.label} · ${delta > 0 ? "+" : ""}${usd(delta)} vs as played`,
    };
  };

  return (
    <>
      <div className="section-title">Best what-if exit</div>
      <div className="section-cap">
        The top row of the ladder below, in each of its two columns — read on this
        sitting's own entries, which were taken knowing what the tape had just done.
        A readout on the day, not a base rate for the setup.
      </div>
      <KpiGrid
        cards={[card("clicked", "Best · as clicked"), card("forget", "Best · set and forget")]}
        template="1fr 1fr"
      />
    </>
  );
}

export function WhatIfExits({ sourceFile }: { sourceFile: string | null | undefined }) {
  const attemptId = attemptIdOf(sourceFile);
  const [custom, setCustom] = useState<WhatIfSpec[]>(() => loadWhatIfRows());
  // Always fetched, not gated on the fold being open: the winner cards are shown
  // whether or not the ladder is unfolded. A sitting costs one re-pricing per
  // session — finished sittings do not move, so `staleTime: Infinity` holds it.
  const q = useWhatIf(attemptId, custom);

  // An imported day has no order log behind it, so there is nothing to re-run.
  if (!attemptId) return null;

  const setRows = (rows: WhatIfSpec[]) => {
    setCustom(rows);
    saveWhatIfRows(rows);
  };

  const data = q.data;
  const anchor = data?.rows[0];
  const base = {
    clicked: anchor?.clicked.net ?? 0,
    forget: anchor?.forget.net ?? 0,
  };
  const nCustom = custom.length;

  return (
    <>
      {/* Every state renders here, outside the fold, because the fold is closed by
          default and the grid is now fetched regardless — a failure or a refusal
          hidden behind a closed summary would look like a sitting with no what-ifs. */}
      {q.isFetching && <div className="notice">Re-pricing this sitting…</div>}
      {q.isError && (
        <div className="notice">What-if failed: {String((q.error as Error)?.message)}</div>
      )}
      {data && !data.valid && <div className="notice">{data.reason}</div>}

      <WinnerCards data={data} />

      {/* Nothing to unfold when the grid refused, so the summary is not offered. */}
      {data?.valid && (
        <details className="journal-fold">
          <summary title="The same fills under other exit settings — and under a reversed entry">
            What-if exits
          </summary>

          <div className="panel compact-table table-scroll-x-narrow">
            <table>
              <thead>
                <tr>
                  <th title="What this row changed about the sitting">Scenario</th>
                  <th className="num" title="The initial stop the row ran, in ticks, measured at the fill — a range where the ticket sized it per trade">
                    SL
                  </th>
                  <th className="num" title="The initial target the row ran, in ticks from the fill — a range where the target was left as it was placed, since the log stores it as an absolute price">
                    TP
                  </th>
                  <th className="num" title="Your hand flattens are kept — the sitting as you actually traded it">
                    As clicked
                  </th>
                  <th className="num" title="Hand flattens dropped — the bracket alone decides every exit">
                    Set and forget
                  </th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <Row
                    key={r.key}
                    row={r}
                    base={base}
                    anchor={i === 0}
                    onRemove={
                      r.custom
                        ? () => setRows(custom.filter((_, j) => j !== i - (data.rows.length - nCustom)))
                        : undefined
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="section-cap" style={{ margin: "8px 0" }}>
            Same entry times, same sizes, same clock. On most rows only the bracket changes;
            the <b>Reversed</b> rows also take the other side of every entry, with the placed
            stop and target mirrored across the fill so the risk in ticks is unchanged — and
            they keep your hand flattens in <b>As clicked</b>, which means they cut the
            reversed trade at the moments you bailed on the real one, so <b>Set and forget</b>
            is the cleaner read of them. Those entries were taken knowing what the tape had
            just done, so this reads the sitting; it is not a base rate for the setup, and the
            reversed rows are less of one still — a contrarian would not have entered at these
            times either. Bracket drags you made by hand belong to the
            <b> As played</b> row alone: on every other row they would overwrite that row's
            own stop and target. <b>SL</b> and <b>TP</b> are the bracket each row opened its
            trades on, measured from the fill: a range where the leg was left as it was
            placed — the ticket sizes the stop off the volatility ruler, and the target is
            stored as an absolute price, so both move from trade to trade — and a single
            number where the row set it.
            {data.open_marked &&
              " This sitting ended holding a position; every row books it at the close, so" +
              " As played reads that much away from the stored net."}
            {data.tape_drift &&
              " The cached tape for this day has changed since the sitting was recorded — fills may not line up."}
          </div>

          <AddRow onAdd={(spec) => setRows([...custom, spec])} />
        </details>
      )}
    </>
  );
}

/** The highest-netting row in one column — the one definition the cards read.
 *
 *  Ties go to the earlier row, which puts As played ahead of anything that only
 *  matched it: a ladder row has to actually beat the day to be named. */
function bestRow(rows: WhatIfRow[], col: "clicked" | "forget"): WhatIfRow {
  return rows.reduce((a, b) => (b[col].net > a[col].net ? b : a));
}
