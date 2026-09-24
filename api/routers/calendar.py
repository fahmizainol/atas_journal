"""Calendar tab: monthly PnL grid + single-day explorer."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from journal import db, metrics
from journal import trade_context as tcmod
from journal.live.booking import REPLAY_PREFIX
from journal.ingest import parse_attempt_no

from .. import deps
from ..scope import Scope, resolve_scope, text_cell
from ..serialize import records, sanitize
from ..summary import summary_extras

router = APIRouter()


def _to_display_iso(raw: str | None, tz) -> str | None:
    """Reproject an ``imported_at`` (UTC ISO from ``datetime('now')``) to tz.

    The upload time is stored in UTC; showing it raw could land a late-night
    upload on the wrong day, so the "Uploaded" card always reads in the user's
    display zone.
    """
    if not raw:
        return None
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(tz).isoformat()


def _attempt_order(imported_at: dict, file_mtime: dict):
    """Sort key placing the oldest-worked-on attempt first.

    The export's "Date modified" leads, then the upload time, with the filename
    breaking ties. One definition because two readers must agree on it: the day
    explorer orders and defaults its attempt buttons by it, and the calendar
    picks each day's *latest* attempt with it. If the two drifted, a cell would
    report a take the explorer does not open when you click through to it.
    """
    return lambda sf: (file_mtime.get(sf) or "", imported_at.get(sf, ""), sf)


def _attempts_for_day(
    day_all: pd.DataFrame, imported_at: dict, file_mtime: dict, tz
) -> list[dict]:
    """Takes that touched this day, oldest-modified first.

    Each ATAS export is one attempt; we order by the export's "Date modified"
    (upload time, then filename, break ties) so the latest take sorts last (the
    day's default view). The "Attempt N" label is the number **parsed from the
    export filename** (first take → 1, ``…-02.xlsx`` → 2), not a positional
    index — so it stays fixed when a take is deleted and lines up with the
    ``-NN`` recording the auto-link scanner matches. ``file_modified`` is that
    same modified time (NULL for files imported before we captured it).

    A Simulator sitting joins the same list, ordered by the same key: it has no
    export, so ``booking.book_attempt`` writes its start time into ``file_mtime``
    directly. It gets a *positional* label, which is the opposite of the rule
    above and for the reason behind that rule: the numbering is pinned to the
    filename so a deletion can't renumber a take out of step with its recording,
    and a sitting has no ``-NN`` in its id and no recording to fall out of step
    with. Parsing one would read every sitting as "Attempt 1".
    """
    files = day_all["source_file"].dropna().unique().tolist()
    files.sort(key=_attempt_order(imported_at, file_mtime))
    out, sittings = [], 0
    for sf in files:
        if str(sf).startswith(REPLAY_PREFIX + "/"):
            sittings += 1
            label = f"Sitting {sittings}"
        else:
            label = f"Attempt {parse_attempt_no(sf)}"
        out.append({
            "source_file": sf,
            "label": label,
            "file_modified": _to_display_iso(file_mtime.get(sf), tz),
        })
    return out


@router.get("/calendar")
def calendar(scope: Scope = Depends(resolve_scope)) -> dict:
    tf = scope.filtered
    if tf.empty:
        return {"months": [], "days": []}

    # A day cell reads its **single latest attempt** — never a sum across takes.
    # Re-doing a day and reading the takes added together answers a question
    # nobody asks; what the calendar is for is "where did this day finish", and
    # that is the take you finished on. ``attempts`` still counts every in-scope
    # take of the day, so a cell showing one of several says so, and the day
    # explorer opens that same take by default and lets you switch.
    #
    # This collapses by *file*, not by account. On the handful of days where
    # separate books traded — a prop account and a paper twin, say — only the
    # book whose file was touched last is in the cell; the others are reachable
    # through the day explorer's attempt buttons but are not in the day's PnL,
    # and so are not in the month total either. Filter by account to read one
    # book across the month.
    #
    # The aggregates elsewhere (statistics, overview, ``scope.filtered``) still
    # sum every attempt: collapsing *those* to the latest take is what makes
    # replay stats survivorship-biased, and that has not changed.
    t = tf.copy()
    t["date"] = t["entry_ts_local"].dt.date
    order = _attempt_order(scope.imported_at, scope.file_mtime)

    days = []
    for d, g in t.groupby("date"):
        files = sorted(g["source_file"].dropna().unique().tolist(), key=order)
        # The day's latest take, and the rows belonging to it alone. A day with
        # no source file at all is not something the importer produces, but if
        # one existed its rows would still be worth showing over nothing.
        latest = files[-1] if files else None
        shown = g[g["source_file"] == latest] if latest else g
        pnl = shown["net_pnl"].astype(float)
        n = len(pnl)
        # One file carries one account by construction (``db.infer_session``
        # assigns a single account per source file), so this is a scalar.
        accounts = shown["account"].dropna().unique().tolist()
        days.append({
            "date": d.isoformat(),
            "net_pnl": float(pnl.sum()),
            "trades": n,
            "win_rate": float((pnl > 0).sum() / n * 100) if n else 0.0,
            "attempts": max(len(files), 1),
            "account": ", ".join(sorted(accounts)) or None,
            # The shown take's "Date modified" — which is the latest of the day,
            # since that is the key it was picked on. It lets the table sort by
            # when a day was last worked on: re-imported, for an export; sat down
            # with, for a Simulator attempt, which has no file and stamps its
            # start time here instead (see ``booking.book_attempt``). NULL for
            # files predating mtime capture, and for practice recorded before
            # that stamp existed until the replay backfill has been run.
            "file_modified": _to_display_iso(scope.file_mtime.get(latest), scope.tz),
        })
    months = sorted({(d.year, d.month) for d in t["date"]}, reverse=True)
    month_objs = [{"year": y, "month": m,
                   "label": f"{date(y, m, 1):%B %Y}"} for y, m in months]
    return {"months": month_objs, "days": days}


@router.get("/day/{day}")
def day_detail(
    day: str,
    source_file: str | None = Query(None),
    scope: Scope = Depends(resolve_scope),
) -> dict:
    d = date.fromisoformat(day)
    allf = scope.filtered_all
    day_all = allf[allf["entry_ts_local"].dt.date == d] if not allf.empty else allf
    if day_all.empty:
        raise HTTPException(404, f"No trades on {day} in scope")

    attempts = _attempts_for_day(day_all, scope.imported_at, scope.file_mtime, scope.tz)
    # Default to the latest attempt (last in the oldest-first list); honour an
    # explicit pick only if it actually touched this day.
    valid = {a["source_file"] for a in attempts}
    selected = source_file if source_file in valid else attempts[-1]["source_file"]
    day_df = (
        day_all[day_all["source_file"] == selected]
        .sort_values("entry_ts_utc")
        .reset_index(drop=True)
    )

    kpis = metrics.compute_metrics(day_df)
    equity = metrics.equity_curve(day_df)
    instrument = day_df["instrument"].value_counts().idxmax()

    per_trade_bars = [
        {
            "trade_no": int(r["trade_no"]),
            "net_pnl": float(r["net_pnl"]),
            "time": r["entry_ts_local"].strftime("%H:%M:%S"),
        }
        for _, r in day_df.iterrows()
    ]

    cols = ["trade_no", "trade_key", "logical_trade_key", "instrument", "direction",
            "max_contracts", "entry_ts_local", "exit_ts_local", "duration_s",
            "avg_entry", "avg_exit", "net_pnl", "source_file", "model_id"]
    trade_rows = records(day_df, cols)
    # Attach each trade's setup badges from the notes frame already loaded by
    # resolve_scope (avoids a second full SELECT * FROM trade_notes per request).
    # Build the map only over this day's keys instead of the whole table. Notes
    # are keyed by the logical trade, so this resolves in either view.
    notes_df = scope.notes
    day_keys = {r["logical_trade_key"] for r in trade_rows}
    setup_map: dict[str, list] = {}
    if not notes_df.empty and day_keys:
        sub = notes_df[notes_df["trade_key"].isin(day_keys)]
        for _, r in sub.iterrows():
            setup_map[r["trade_key"]] = json.loads(text_cell(r["setups_json"]) or "[]")
    for r in trade_rows:
        r["setups"] = setup_map.get(r["logical_trade_key"], [])
    file_modified = next(
        (a["file_modified"] for a in attempts if a["source_file"] == selected), None
    )

    # Was the direction right, over this attempt's entries. Read by LOGICAL key
    # and through a set, because in the ATAS view a scaled trade is several rows
    # sharing one key — counting it once per fill would let one entry outvote
    # three. Keys with no measured row are kept as None on purpose: they are the
    # gap between "trades taken" and "trades the tape could speak to", and
    # ``direction_edge`` reports both denominators.
    ctx_keys = sorted({r["logical_trade_key"] for r in trade_rows})
    conn = deps.get_conn()
    with deps.db_lock():
        ctx_rows = [db.get_trade_context(conn, k) for k in ctx_keys]

    return {
        "kpis": sanitize(kpis),
        "extras": sanitize(summary_extras(day_df)),
        "entry_direction": sanitize(tcmod.direction_edge(ctx_rows)),
        "equity": records(equity, ["ts", "trade_no", "pnl", "equity", "drawdown"]),
        "per_trade_bars": per_trade_bars,
        "trades": trade_rows,
        "instrument": instrument,
        "attempts": attempts,
        "source_file": selected,
        "file_modified": file_modified,
    }
