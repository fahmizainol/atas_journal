"""Calendar tab: monthly PnL grid + single-day explorer."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from journal import db, metrics
from journal.recordings import parse_attempt_no

from .. import deps
from ..scope import Scope, resolve_scope
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


def _attempts_for_day(
    day_all: pd.DataFrame, imported_at: dict, file_mtime: dict, tz
) -> list[dict]:
    """Replay attempts that touched this day, oldest-modified first.

    Each ATAS export is one attempt; we order by the export's "Date modified"
    (upload time, then filename, break ties) so the latest take sorts last (the
    day's default view). The "Attempt N" label is the number **parsed from the
    export filename** (first take → 1, ``…-02.xlsx`` → 2), not a positional
    index — so it stays fixed when a take is deleted and lines up with the
    ``-NN`` recording the auto-link scanner matches. ``file_modified`` is that
    same modified time (NULL for files imported before we captured it).
    """
    files = day_all["source_file"].dropna().unique().tolist()
    files.sort(key=lambda s: (file_mtime.get(s) or "", imported_at.get(s, ""), s))
    return [
        {
            "source_file": sf,
            "label": f"Attempt {parse_attempt_no(sf)}",
            "file_modified": _to_display_iso(file_mtime.get(sf), tz),
        }
        for sf in files
    ]


@router.get("/calendar")
def calendar(scope: Scope = Depends(resolve_scope)) -> dict:
    tf = scope.filtered
    if tf.empty:
        return {"months": [], "days": []}

    # tf is already latest-attempt-per-day, so the cell PnL never blends takes.
    # Count distinct attempts from the unreduced frame to badge re-done days,
    # and collect each day's source files so a day can be badged when *any* of
    # its attempts has a recording linked.
    attempts_by_day: dict = {}
    files_by_day: dict = {}
    allf = scope.filtered_all
    if not allf.empty:
        ac = allf.copy()
        ac["date"] = ac["entry_ts_local"].dt.date
        attempts_by_day = ac.groupby("date")["source_file"].nunique().to_dict()
        files_by_day = (
            ac.groupby("date")["source_file"].apply(lambda s: set(s.dropna())).to_dict()
        )

    conn = deps.get_conn()
    with deps.db_lock():
        linked = db.linked_video_source_files(conn)

    t = tf.copy()
    t["date"] = t["entry_ts_local"].dt.date
    days = []
    for d, g in t.groupby("date"):
        pnl = g["net_pnl"].astype(float)
        n = len(pnl)
        # Latest export "Date modified" across the day's attempts, so the table
        # view can sort by when a day was last re-imported (NULL for days whose
        # files predate mtime capture). UTC ISO strings sort lexicographically.
        mtimes = [scope.file_mtime.get(sf) for sf in files_by_day.get(d, set())]
        latest_mtime = max((m for m in mtimes if m), default=None)
        days.append({
            "date": d.isoformat(),
            "net_pnl": float(pnl.sum()),
            "trades": n,
            "win_rate": float((pnl > 0).sum() / n * 100) if n else 0.0,
            "attempts": int(attempts_by_day.get(d, 1)),
            "has_video": bool(files_by_day.get(d, set()) & linked),
            "file_modified": _to_display_iso(latest_mtime, scope.tz),
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

    cols = ["trade_no", "trade_key", "instrument", "direction", "max_contracts",
            "entry_ts_local", "exit_ts_local", "duration_s",
            "avg_entry", "avg_exit", "net_pnl"]
    trade_rows = records(day_df, cols)
    # Attach each trade's setup badges from the notes frame already loaded by
    # resolve_scope (avoids a second full SELECT * FROM trade_notes per request).
    # Build the map only over this day's trade_keys instead of the whole table.
    notes_df = scope.notes
    day_keys = {r["trade_key"] for r in trade_rows}
    setup_map: dict[str, list] = {}
    if not notes_df.empty and day_keys:
        sub = notes_df[notes_df["trade_key"].isin(day_keys)]
        for _, r in sub.iterrows():
            setup_map[r["trade_key"]] = json.loads(r["setups_json"] or "[]")
    for r in trade_rows:
        r["setups"] = setup_map.get(r["trade_key"], [])
    file_modified = next(
        (a["file_modified"] for a in attempts if a["source_file"] == selected), None
    )
    return {
        "kpis": sanitize(kpis),
        "extras": sanitize(summary_extras(day_df)),
        "equity": records(equity, ["ts", "trade_no", "pnl", "equity", "drawdown"]),
        "per_trade_bars": per_trade_bars,
        "trades": trade_rows,
        "instrument": instrument,
        "attempts": attempts,
        "source_file": selected,
        "file_modified": file_modified,
    }
