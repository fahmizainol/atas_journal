// One account: the lives it has had, and the brackets that would have changed
// them.
//
// Five panels, and the order is the argument. What happened (the ledger, the
// lives, the sentences written at each death) comes before what might have —
// because the counterfactual is only readable against the record it deviates
// from, and because the record is the part that is simply true.
//
// **The as-played campaign row will not equal the real record**, and the page
// says so rather than reconciling them. The real one reads the browser's live
// `min_room_usd` verdict; the campaign re-derives the equity path from the tape.
// The real record is this account's history. The as-played row is the
// like-for-like baseline every other bracket's delta is measured against, and
// the two are labelled as the different things they are.

import { Fragment, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useAccountCampaign,
  useAccountReview,
  usePriceAccount,
  useRepVerdicts,
  type CampaignRun,
  type RealLife,
} from "../hooks/useAccountCampaign";
import { EquityCurve } from "../components/accounts/EquityCurve";
import { outcomeTone } from "./Accounts";
import { palette } from "../theme";

const money = (n: number | null | undefined) =>
  n == null ? "—" : `${n < 0 ? "-$" : "$"}${Math.abs(n).toFixed(2)}`;
const signed = (n: number) => `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(0)}`;

const COLUMN_HINT: Record<string, string> = {
  clicked: "The manual flattens you made are kept — the bracket changed, your hand did not.",
  forget: "Manual flattens and bracket drags both dropped: the bracket does all the work.",
};

export function AccountDetail() {
  const { accountId } = useParams();
  const q = useAccountCampaign(accountId);
  const review = useAccountReview(accountId);
  const price = usePriceAccount(accountId);
  const [column, setColumn] = useState<"clicked" | "forget">("clicked");
  const [openRep, setOpenRep] = useState<string | null>(null);

  const d = q.data;
  const runs = useMemo(
    () => (d?.runs ?? []).filter((r) => r.column === column),
    [d, column],
  );
  const baseline = useMemo(
    () => (d?.runs ?? []).find((r) => r.key === "as-played" && r.column === column),
    [d, column],
  );
  // The gap between one bracket's two columns, in lives. The most literal answer
  // the page has to "how does this deviate from what I actually trade".
  const hand = useMemo(() => {
    const c = (d?.runs ?? []).find((r) => r.key === "as-played" && r.column === "clicked");
    const f = (d?.runs ?? []).find((r) => r.key === "as-played" && r.column === "forget");
    return c && f ? c.score - f.score : null;
  }, [d]);

  // The lives that ended, each with the rep that ended it. Built as a pair so
  // the id is non-null by construction rather than by a filter TypeScript
  // cannot see through.
  const ended = useMemo(
    () =>
      (d?.real.lives ?? []).flatMap((life) =>
        life.outcome !== "live" && life.ended_by ? [{ life, repId: life.ended_by }] : [],
      ),
    [d],
  );
  const byId = useMemo(
    () => new Map((d?.reps ?? []).map((r) => [r.id, r])),
    [d],
  );

  if (q.isLoading) return <div className="page" style={{ color: palette.muted }}>loading…</div>;
  if (!d) return <div className="page">No such account.</div>;

  const a = d.account;
  const cov = d.coverage;

  return (
    <div className="page" data-account-detail={a.id}>
      <div className="page-head">
        <h1>
          <Link to="/accounts" style={{ color: palette.muted }}>Accounts</Link> / {a.label}
        </h1>
        <p className="page-sub">
          {a.trailing === "intraday" ? "Intraday trail" : "End-of-day trail"} · start{" "}
          {money(a.numbers.start)} · max loss {money(a.numbers.max_loss)} · target{" "}
          {money(a.numbers.profit_target)}
          {!a.needs_cause && " · free reset"}
        </p>
      </div>

      {/* 1 — the record. What actually happened, before any counterfactual. */}
      <section className="panel" style={{ padding: 10, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 13 }}>The record</h2>
        <p style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
          Completed evals, not high-water marks. A life ends two ways: the floor catches it,
          or a settled balance clears the target.
        </p>
        <div style={{ fontSize: 20, marginBottom: 8 }}>
          <span style={{ color: palette.green }}>{d.real.record.passed} passed</span>
          <span style={{ color: palette.muted }}> · </span>
          <span style={{ color: palette.red }}>{d.real.record.blown} blown</span>
          <span style={{ color: palette.muted, fontSize: 12 }}>
            {" "}over {d.real.lives.length} {d.real.lives.length === 1 ? "life" : "lives"}
          </span>
        </div>
        {d.real.lives.length > 0 && (
          <EquityCurve
            reps={d.reps}
            lives={d.real.lives}
            start={a.numbers.start}
            floor={d.real.lives[d.real.lives.length - 1].floor}
          />
        )}
        <LivesTable lives={d.real.lives} />
      </section>

      {/* 2 — the counterfactual campaign. */}
      <section className="panel" style={{ padding: 10, marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 13 }}>Under another bracket</h2>
          <div role="tablist" style={{ display: "flex", gap: 4 }}>
            {(["clicked", "forget"] as const).map((c) => (
              <button
                key={c}
                type="button"
                role="tab"
                aria-selected={c === column}
                className={`btn${c === column ? " active" : ""}`}
                style={{ fontSize: 11, padding: "2px 8px" }}
                title={COLUMN_HINT[c]}
                onClick={() => setColumn(c)}
                data-column={c}
              >
                {c === "clicked" ? "as clicked" : "set & forget"}
              </button>
            ))}
          </div>
          {hand != null && (
            <span
              style={{ color: palette.muted, fontSize: 11 }}
              title="The as-played row walked with your manual flattens, against the same row without them. Positive means your hand saved lives; negative means it cost them."
            >
              your hand on the exit:{" "}
              <b style={{ color: hand > 0 ? palette.green : hand < 0 ? palette.red : palette.muted }}>
                {hand === 0 ? "no change" : `${hand > 0 ? "+" : ""}${hand} ${Math.abs(hand) === 1 ? "life" : "lives"}`}
              </b>
            </span>
          )}
        </div>

        <Coverage
          cov={cov}
          pricing={price.isPending}
          onPrice={() => price.mutate()}
        />

        <p style={{ color: palette.muted, fontSize: 11, lineHeight: 1.6 }}>
          Every rep re-priced under this bracket and walked through the account's own numbers,
          re-buying each time it dies or passes. <b>Entry times are held fixed</b> — a row is
          the same button presses under a different bracket, and those presses were conditioned
          on what happened next. It is a readout on exits, not a strategy result.
          {cov.unpriced > 0 && (
            <>
              {" "}
              <b style={{ color: palette.orange }}>
                {cov.unpriced} of {cov.traded} traded reps could not be re-priced and are dropped
                from every row
              </b>
              , so this campaign does not reproduce the record above it.
            </>
          )}
        </p>

        {runs.length === 0 ? (
          <div style={{ color: palette.muted, fontSize: 12 }}>
            Nothing priced yet — measure the reps and this fills in.
          </div>
        ) : (
          <RunTable runs={runs} baseline={baseline} />
        )}
      </section>

      {/* 3 — the rep that ended each life, and what would have survived it. */}
      <section className="panel" style={{ padding: 10, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 13 }}>What killed each life</h2>
        <p style={{ color: palette.muted, fontSize: 11, marginTop: 4, lineHeight: 1.6 }}>
          The aggregate above says which bracket wins over every rep. This says what happened on
          the ones that mattered — each row starts from the equity and the floor that rep really
          opened against, so the answers differ only in the bracket.
        </p>
        {ended.length === 0 ? (
          <div style={{ color: palette.muted, fontSize: 12 }}>No life has ended yet.</div>
        ) : (
          ended.map(({ life, repId }) => (
            <div key={life.index} style={{ marginTop: 8 }}>
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11 }}
                onClick={() => setOpenRep(openRep === repId ? null : repId)}
                data-killer={repId}
              >
                {openRep === repId ? "▾" : "▸"} Life {life.index + 1} —{" "}
                <span style={{ color: outcomeTone(life.outcome) }}>{life.outcome}</span> at{" "}
                {money(life.equity)}
                <span style={{ color: palette.muted }}>
                  {" "}
                  on the {byId.get(repId)?.date ?? "?"} rep
                </span>
              </button>
              {openRep === repId && <RepVerdicts accountId={a.id} attemptId={repId} />}
            </div>
          ))
        )}
      </section>

      {/* 4 — the consistency read. */}
      <section className="panel" style={{ padding: 10 }}>
        <h2 style={{ margin: 0, fontSize: 13 }}>How you actually traded it</h2>
        <p style={{ color: palette.muted, fontSize: 11, marginTop: 4, lineHeight: 1.6 }}>
          The stop is sized off the volatility ruler at every fill, so a sitting runs a{" "}
          <i>range</i> rather than a number. That inconsistency is what the fixed-stop rows above
          price against.
        </p>
        <Consistency
          reps={d.reps}
          review={review.data}
          loading={review.isLoading}
        />
      </section>
    </div>
  );
}

function Coverage({
  cov,
  pricing,
  onPrice,
}: {
  cov: { traded: number; priced: number; unpriced: number; flat: number; reps: number };
  pricing: boolean;
  onPrice: () => void;
}) {
  return (
    <div
      style={{ display: "flex", gap: 8, alignItems: "center", margin: "6px 0", fontSize: 11 }}
      data-coverage
    >
      <span style={{ color: palette.muted }}>
        priced {cov.priced} of {cov.traded} traded reps
        {cov.flat > 0 && ` · ${cov.flat} with no trade`}
      </span>
      {cov.unpriced > 0 && (
        <button
          type="button"
          className="btn"
          style={{ fontSize: 11 }}
          disabled={pricing}
          onClick={onPrice}
          data-price-account
          title="Re-prices each rep under the whole exit ladder. About a second a rep; the count above updates as it goes."
        >
          {pricing ? "pricing…" : `Price ${cov.unpriced} rep${cov.unpriced === 1 ? "" : "s"} →`}
        </button>
      )}
    </div>
  );
}

function LivesTable({ lives }: { lives: RealLife[] }) {
  if (!lives.length) return <div style={{ color: palette.muted, fontSize: 12 }}>Never traded.</div>;
  return (
    <table className="data-table" style={{ width: "100%", fontSize: 12 }} data-lives-table>
      <thead>
        <tr>
          <th>Life</th>
          <th>Opened</th>
          <th style={{ textAlign: "right" }}>Reps</th>
          <th style={{ textAlign: "right" }}>Ended at</th>
          <th style={{ textAlign: "right" }}>Floor</th>
          <th>Outcome</th>
          <th>What you wrote</th>
        </tr>
      </thead>
      <tbody>
        {lives.map((l) => (
          <tr key={l.index}>
            <td>{l.index + 1}</td>
            <td style={{ color: palette.muted }}>{l.started_at.slice(0, 10)}</td>
            <td style={{ textAlign: "right" }}>{l.reps}</td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(l.equity)}</td>
            <td style={{ textAlign: "right", fontFamily: "monospace", color: palette.muted }}>
              {money(l.floor)}
            </td>
            <td style={{ color: outcomeTone(l.outcome) }}>{l.outcome}</td>
            {/* The sentences were written at each death and have never been read
                back anywhere. That is most of the reason this table exists. */}
            <td style={{ color: palette.muted, fontStyle: l.cause ? "normal" : "italic" }}>
              {l.cause ?? (l.outcome === "blown" ? "not written up yet" : "—")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RunTable({ runs, baseline }: { runs: CampaignRun[]; baseline: CampaignRun | undefined }) {
  const base = baseline?.end_equity ?? null;
  return (
    <table className="data-table" style={{ width: "100%", fontSize: 12 }} data-campaign-table>
      <thead>
        <tr>
          <th>Bracket</th>
          <th style={{ textAlign: "right" }}>Passed</th>
          <th style={{ textAlign: "right" }}>Blown</th>
          <th style={{ textAlign: "right" }}>Lives</th>
          <th style={{ textAlign: "right" }}>Ended at</th>
          <th style={{ textAlign: "right" }}>vs played</th>
          <th style={{ textAlign: "right" }}>Net</th>
          <th style={{ textAlign: "right" }}>Win rate</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r, i) => {
          const isBase = r.key === "as-played";
          const delta = base != null && r.end_equity != null && !isBase ? r.end_equity - base : null;
          // Where the exits stop and the direction read begins. Reversed rows
          // are ranked apart because they are not brackets — see `Run.flip`.
          const firstFlip = r.flip && !runs[i - 1]?.flip;
          return (
            <Fragment key={`${r.key}:${r.column}`}>
              {firstFlip && (
                <tr>
                  <td colSpan={8} style={{ color: palette.muted, fontSize: 11, paddingTop: 10 }}>
                    Below: the same entries taken the <b>other way</b>. Not brackets — these change
                    the direction, not the exit, so they are ranked apart however well they score.
                    On an account that lost money they will score very well, and that is arithmetic
                    rather than a finding.
                  </td>
                </tr>
              )}
            <tr
              style={isBase ? { background: "rgba(255,255,255,0.05)" } : undefined}
              data-run={r.key}
              data-flip={r.flip ? "1" : undefined}
            >
              <td>
                {r.label}
                {isBase && (
                  <span
                    style={{ color: palette.muted, fontSize: 10 }}
                    title="A reconstruction of what you did, not the record itself — it re-derives the equity path where the record reads the browser's live verdict. It is here as the baseline the other rows are measured against."
                  >
                    {" "}· baseline
                  </span>
                )}
              </td>
              <td style={{ textAlign: "right", color: r.record.passed ? palette.green : palette.muted }}>
                {r.record.passed}
              </td>
              <td style={{ textAlign: "right", color: r.record.blown ? palette.red : palette.muted }}>
                {r.record.blown}
              </td>
              <td style={{ textAlign: "right", color: palette.muted }}>{r.lives.length}</td>
              <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(r.end_equity)}</td>
              <td
                style={{
                  textAlign: "right",
                  fontFamily: "monospace",
                  color: delta == null ? palette.muted : delta >= 0 ? palette.green : palette.red,
                }}
              >
                {delta == null ? "—" : signed(delta)}
              </td>
              <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(r.net_usd)}</td>
              <td style={{ textAlign: "right", color: palette.muted }}>
                {r.win_rate == null ? "—" : `${r.win_rate}%`}
              </td>
            </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function RepVerdicts({ accountId, attemptId }: { accountId: string; attemptId: string }) {
  const q = useRepVerdicts(accountId, attemptId);
  if (q.isLoading) return <div style={{ color: palette.muted, fontSize: 11 }}>loading…</div>;
  const d = q.data;
  if (!d) return null;
  if (!d.priced) {
    return (
      <div style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
        This rep has no priced grid, so no bracket can be compared on it.
      </div>
    );
  }
  const survivors = d.verdicts.filter((v) => v.survived);
  return (
    <div style={{ marginTop: 6, fontSize: 11 }} data-rep-verdicts={attemptId}>
      <div style={{ color: palette.muted, marginBottom: 4 }}>
        {d.date} · opened at {money(d.opened_at)} against a floor of {money(d.floor)} ·{" "}
        <b style={{ color: survivors.length ? palette.green : palette.red }}>
          {survivors.length} of {d.verdicts.length} brackets survive it
        </b>
      </div>
      {survivors.length > 0 && (
        <table className="data-table" style={{ width: "100%", fontSize: 11 }}>
          <thead>
            <tr>
              <th>Bracket</th>
              <th>Column</th>
              <th style={{ textAlign: "right" }}>Net</th>
              <th style={{ textAlign: "right" }}>Left it at</th>
            </tr>
          </thead>
          <tbody>
            {survivors
              .slice()
              .sort((x, y) => y.end_equity - x.end_equity)
              .slice(0, 8)
              .map((v) => (
                <tr key={`${v.key}:${v.column}`}>
                  <td>{v.label}</td>
                  <td style={{ color: palette.muted }}>{v.column === "clicked" ? "as clicked" : "set & forget"}</td>
                  <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(v.net_usd)}</td>
                  <td style={{ textAlign: "right", fontFamily: "monospace" }}>{money(v.end_equity)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Consistency({
  reps,
  review,
  loading,
}: {
  reps: {
    id: string;
    date: string | null;
    trades: number;
    stop_ticks: { lo: number; med: number; hi: number } | null;
    fast_trades: number;
    held: number;
    status: string;
  }[];
  review: { grades: Record<string, number>; owed: number | null; graded: number } | undefined;
  loading: boolean;
}) {
  const traded = reps.filter((r) => r.trades > 0);
  const spans = traded.map((r) => r.stop_ticks).filter(Boolean) as {
    lo: number; med: number; hi: number;
  }[];
  const varying = spans.filter((s) => s.hi > s.lo).length;
  const meds = spans.map((s) => s.med).sort((a, b) => a - b);
  const fast = traded.reduce((n, r) => n + r.fast_trades, 0);
  const held = traded.reduce((n, r) => n + r.held, 0);
  const unreviewed = traded.filter((r) => r.status !== "reviewed").length;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 10 }}>
      <Fact
        label="Stop, across reps"
        value={meds.length ? `${meds[0]}–${meds[meds.length - 1]}t` : "—"}
        sub={meds.length ? `median of medians ${meds[Math.floor(meds.length / 2)]}t` : undefined}
        hint="Each rep's median initial stop, low to high across the account."
      />
      <Fact
        label="Reps that varied it inside"
        value={spans.length ? `${varying} of ${spans.length}` : "—"}
        sub="the stop moved between fills"
        hint="The ticket re-sizes the stop off the volatility ruler at every fill, so most sittings run more than one bracket."
      />
      <Fact
        label="Trades under 30s"
        value={held ? `${fast} of ${held}` : "—"}
        sub={held ? `${Math.round((100 * fast) / held)}% of fills` : undefined}
        hint="The one behavioural leak the manual-trade audit actually found, at the same threshold the account's own flags use."
        tone={held && fast / held > 0.2 ? palette.red : undefined}
      />
      <Fact
        label="Reps owing review"
        value={`${unreviewed} of ${traded.length}`}
        sub={review?.owed != null ? `${review.owed} trades unanswered` : loading ? "counting…" : undefined}
        tone={unreviewed ? palette.orange : undefined}
        hint="Every booked trade owes a grade, a watched level and a tag."
      />
      <Fact
        label="Grades"
        value={
          loading
            ? "…"
            : review && review.graded
              ? ["A", "B", "C", "D"]
                  .filter((g) => review.grades[g])
                  .map((g) => `${g}×${review.grades[g]}`)
                  .join("  ")
              : "—"
        }
        sub={review?.graded ? `${review.graded} graded` : "hindsight only — it cannot size a trade"}
        hint="Assigned after the fact, so it reads the outcome as much as the decision."
      />
    </div>
  );
}

function Fact({
  label,
  value,
  sub,
  hint,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div title={hint}>
      <div style={{ color: palette.muted, fontSize: 10 }}>{label}</div>
      <div style={{ fontSize: 16, fontFamily: "monospace", color: tone }}>{value}</div>
      {sub && <div style={{ color: palette.muted, fontSize: 10 }}>{sub}</div>}
    </div>
  );
}
