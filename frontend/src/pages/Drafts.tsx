import { Link } from "react-router-dom";
import { useDraftList } from "../hooks/useDrafts";
import { fmt } from "../lib/format";

// The Lab's sketchbook: study events laid out as trades on real charts, one
// step before a strategy exists. Specs live in data/drafts/ in the repo — a
// draft shows up here by adding a JSON file, same convention as Research.

export function Drafts() {
  const list = useDraftList();
  const drafts = list.data ?? [];

  return (
    <div className="research-index">
      <p className="muted" style={{ fontSize: 13 }}>
        A draft materializes a study's event table into a trade-shaped list and draws it on the
        session charts — no fills, no sizing, no simulation. <b>Drafts are not backtests</b>; they
        exist so the eye can inspect what the aggregate stats can't. Specs live in{" "}
        <code>data/drafts/</code>; promotion goes split-half → monthly consistency → engine A/B.
      </p>
      {list.isLoading && <div className="page-fallback" />}
      {drafts.map((d) => (
        <Link key={d.slug} to={`/drafts/${d.slug}`} className="panel research-card">
          <div className="research-card-title">{d.name}</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
            {d.symbol} · {d.direction}
            {d.summary
              ? ` · ${d.summary.n_trades} trades · ` +
                // Unraced (passthrough) drafts have no win rate and no R;
                // showing "— · 0.00R total" reads as a flat result rather than
                // an absent one, so they report distance instead.
                (d.summary.targets + d.summary.stops > 0
                  ? `${d.summary.win_rate != null ? (d.summary.win_rate * 100).toFixed(1) + "% to target" : "—"} · ` +
                    `${fmt(d.summary.total_r, false)}R total`
                  : `${fmt(d.summary.total_points, false)} points total`)
              : " · not materialized yet — open to build"}
          </div>
          <div style={{ fontSize: 13 }}>{d.hypothesis}</div>
        </Link>
      ))}
      {!list.isLoading && drafts.length === 0 && (
        <div className="panel muted">
          No drafts yet. Add a spec file under <code>data/drafts/</code>.
        </div>
      )}
    </div>
  );
}
