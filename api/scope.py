"""Resolve the shared filter scope for every trade-derived endpoint.

Mirrors ``app.py``: load executions + journal, build the logical/ATAS frame,
``localize`` to the display tz *before* any metrics/edges/daily run (they bucket
on ``entry_ts_local``), then ``apply_filters`` (instruments / date range / tags /
session mode / model / archive).

Every attempt of a re-done day is kept. An earlier build collapsed each calendar
day down to its latest-modified ``source_file``, which silently deleted a day's
live trades whenever a replay of that same day was imported afterwards, and made
replay stats survivorship-biased (only the takes that went well survived). The
archive flag, not collapsing, is what keeps the old era out of the aggregates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import Query

from journal import db, trades
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS

from . import deps

# A trade whose source_file has no ``sessions`` row (an export ingested by an
# older build, or one whose session was deleted) reads as an un-archived replay:
# visible, but never counted as live money.
DEFAULT_SESSION = {"mode": "replay", "account": None, "model_id": None, "archived": False}


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _csv_ints(value: str | None) -> list[int]:
    out: list[int] = []
    for v in _csv(value):
        try:
            out.append(int(v))
        except ValueError:
            continue
    return out


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


@dataclass
class Scope:
    view: str
    tz_label: str
    tz: ZoneInfo
    instruments: list[str]
    accounts: list[str]
    start: date | None
    end: date | None
    tags: list[str]
    base: pd.DataFrame          # localized, unfiltered (for filter-option discovery)
    filtered: pd.DataFrame      # localized + filtered; archived excluded by default
    # filtered_all applies the same filters but always keeps archived sessions, so
    # a deep link to an archived trade still resolves with the Archive toggle off.
    filtered_all: pd.DataFrame = field(default_factory=pd.DataFrame)
    modes: list[str] = field(default_factory=list)
    models: list[int] = field(default_factory=list)
    include_archived: bool = False
    imported_at: dict = field(repr=False, default_factory=dict)  # source_file -> UTC ISO
    file_mtime: dict = field(repr=False, default_factory=dict)    # source_file -> export mtime
    sessions: dict = field(repr=False, default_factory=dict)      # source_file -> session row
    journal: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    # Carry the notes frame loaded during scope resolution so handlers that need
    # setup/confluence/tag badges don't re-scan trade_notes themselves.
    notes: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)

    @property
    def date_range(self):
        if self.start and self.end:
            return (self.start, self.end)
        return None


def _attach_session_columns(
    df: pd.DataFrame, sessions: dict[str, dict], model_by_trade: dict[str, int]
) -> pd.DataFrame:
    """Add ``session_mode`` / ``session_archived`` / ``model_id`` to every row.

    ``model_id`` is the trade's *effective* model: its own ``trade_model`` binding
    if it has one, else the session's model when that session is a backtest (one
    model exercised for the whole session), else NULL for off-model. Exactly one
    model per trade, so per-model PnL partitions the scope total.
    """
    if df.empty:
        return df
    out = df.copy()
    sess = [sessions.get(s, DEFAULT_SESSION) for s in out["source_file"]]
    out["session_mode"] = [s["mode"] for s in sess]
    out["session_archived"] = [s["archived"] for s in sess]
    # Built as an object column, not via ``Series.where``: a NULL model would
    # otherwise coerce the whole column to float and turn model ids into 1.0.
    out["model_id"] = [
        model_by_trade[key] if key in model_by_trade
        else (s["model_id"] if s["mode"] == "backtest" else None)
        for key, s in zip(out["logical_trade_key"], sess)
    ]
    return out


def _apply_filters(
    df: pd.DataFrame, instruments: list[str], accounts: list[str], start: date | None,
    end: date | None, tags: list[str], notes_df: pd.DataFrame,
    day_notes_df: pd.DataFrame, modes: list[str], models: list[int],
) -> pd.DataFrame:
    """Every filter except the archive flag, which ``resolve_scope`` applies last
    so the archive-inclusive and archive-excluded frames share one pass."""
    if df.empty:
        return df
    out = df
    if modes:
        out = out[out["session_mode"].isin(modes)]
    if models:
        out = out[out["model_id"].isin(models)]
    if instruments:
        out = out[out["instrument"].isin(instruments)]
    if accounts:
        out = out[out["account"].isin(accounts)]
    if start and end:
        d = out["entry_ts_local"].dt.date
        out = out[(d >= start) & (d <= end)]
    if tags:
        sel = set(tags)
        trade_tag_map = {
            r["trade_key"]: set(json.loads(r["tags_json"] or "[]"))
            for _, r in notes_df.iterrows()
        } if not notes_df.empty else {}
        day_tag_map = {
            r["day"]: set(json.loads(r["tags_json"] or "[]"))
            for _, r in day_notes_df.iterrows()
        } if not day_notes_df.empty else {}
        day_iso = out["entry_ts_local"].dt.date.map(lambda d: d.isoformat())
        # Notes are keyed by the logical trade, so an ATAS row inherits the tags
        # of the logical trade that owns it.
        mask = out["logical_trade_key"].apply(
            lambda k: bool(trade_tag_map.get(k, set()) & sel)
        ) | day_iso.apply(lambda d: bool(day_tag_map.get(d, set()) & sel))
        out = out[mask]
    return out.reset_index(drop=True)


def resolve_scope(
    view: str = Query("logical"),
    instruments: str | None = Query(None),
    accounts: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    tags: str | None = Query(None),
    tz: str | None = Query(None),
    modes: str | None = Query(None),
    models: str | None = Query(None),
    include_archived: bool = Query(False),
) -> Scope:
    tz_label = tz if tz in DISPLAY_TZS else DEFAULT_DISPLAY_TZ
    disp_tz = DISPLAY_TZS[tz_label]
    instr_list = _csv(instruments)
    account_list = _csv(accounts)
    tag_list = _csv(tags)
    mode_list = _csv(modes)
    model_list = _csv_ints(models)
    d0, d1 = _parse_date(start), _parse_date(end)

    conn = deps.get_conn()
    with deps.db_lock():
        ex = db.load_executions(conn)
        jr = db.load_journal(conn)
        notes_df = db.all_notes(conn)
        day_notes_df = db.all_day_notes(conn)
        imported = db.imported_at_map(conn)
        file_mtimes = db.file_mtime_map(conn)
        sessions = db.sessions_map(conn)
        model_by_trade = db.trade_model_map(conn)

    # load_executions parses ts_local as UTC (rows can come from mixed source
    # tzs). Reproject into the chosen display tz so per-fill timestamps shown
    # to the AI and in chart markers read in the user's clock.
    if not ex.empty:
        ex["ts_local"] = ex["ts_utc"].dt.tz_convert(disp_tz)

    if view == "atas":
        base = trades.atas_trades(jr)
    else:
        base = trades.build_logical_trades(jr, ex)
    base = trades.localize(base, disp_tz)
    if base is None:
        base = pd.DataFrame()

    if not base.empty:
        # Journaling (note, model, rule checks) binds to the logical trade in both
        # views: an ATAS row resolves to whichever logical trade absorbed its lot.
        if view == "atas":
            lot_map = trades.lot_to_logical_map(jr)
            # A lot the grouper couldn't place (it should place all of them)
            # falls back to its own key rather than becoming NaN and silently
            # colliding with every other unmapped row.
            base["logical_trade_key"] = (
                base["dedupe_key"].map(lot_map).fillna(base["trade_key"])
            )
        else:
            base["logical_trade_key"] = base["trade_key"]
        base = _attach_session_columns(base, sessions, model_by_trade)

    # Filter once. ``filtered`` is exactly ``filtered_all`` minus archived rows,
    # so deriving it rather than re-running the (tag-map building, per-row masking)
    # pipeline both halves the work and makes the two frames impossible to desync.
    filtered_all = _apply_filters(
        base, instruments=instr_list, accounts=account_list, start=d0, end=d1,
        tags=tag_list, notes_df=notes_df, day_notes_df=day_notes_df,
        modes=mode_list, models=model_list,
    )
    filtered = filtered_all
    if not include_archived and not filtered_all.empty:
        keep = ~filtered_all["session_archived"].astype(bool)
        filtered = filtered_all[keep].reset_index(drop=True)
    return Scope(
        view="atas" if view == "atas" else "logical",
        tz_label=tz_label, tz=disp_tz, instruments=instr_list, accounts=account_list,
        start=d0, end=d1, tags=tag_list, base=base, filtered=filtered,
        filtered_all=filtered_all, modes=mode_list, models=model_list,
        include_archived=include_archived, imported_at=imported, file_mtime=file_mtimes,
        sessions=sessions, journal=jr, notes=notes_df,
    )
