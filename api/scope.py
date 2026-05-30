"""Resolve the shared filter scope for every trade-derived endpoint.

Mirrors ``app.py``: load executions + journal, build the logical/ATAS frame,
``localize`` to the display tz *before* any metrics/edges/daily run (they bucket
on ``entry_ts_local``), then ``apply_filters`` (instruments / date range / tags).
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


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


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
    filtered: pd.DataFrame      # localized + filtered (the working frame)
    journal: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)

    @property
    def date_range(self):
        if self.start and self.end:
            return (self.start, self.end)
        return None


def _apply_filters(
    df: pd.DataFrame, instruments: list[str], accounts: list[str], start: date | None,
    end: date | None, tags: list[str], notes_df: pd.DataFrame,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if instruments:
        out = out[out["instrument"].isin(instruments)]
    if accounts:
        out = out[out["account"].isin(accounts)]
    if start and end:
        d = out["entry_ts_local"].dt.date
        out = out[(d >= start) & (d <= end)]
    if tags and not notes_df.empty:
        keymap = {r["trade_key"]: set(json.loads(r["tags_json"] or "[]"))
                  for _, r in notes_df.iterrows()}
        sel = set(tags)
        out = out[out["trade_key"].apply(lambda k: bool(keymap.get(k, set()) & sel))]
    return out.reset_index(drop=True)


def resolve_scope(
    view: str = Query("logical"),
    instruments: str | None = Query(None),
    accounts: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    tags: str | None = Query(None),
    tz: str | None = Query(None),
) -> Scope:
    tz_label = tz if tz in DISPLAY_TZS else DEFAULT_DISPLAY_TZ
    disp_tz = DISPLAY_TZS[tz_label]
    instr_list = _csv(instruments)
    account_list = _csv(accounts)
    tag_list = _csv(tags)
    d0, d1 = _parse_date(start), _parse_date(end)

    conn = deps.get_conn()
    with deps.db_lock():
        ex = db.load_executions(conn)
        jr = db.load_journal(conn)
        notes_df = db.all_notes(conn)

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

    filtered = _apply_filters(base, instr_list, account_list, d0, d1, tag_list, notes_df)
    return Scope(
        view="atas" if view == "atas" else "logical",
        tz_label=tz_label, tz=disp_tz, instruments=instr_list, accounts=account_list,
        start=d0, end=d1, tags=tag_list, base=base, filtered=filtered, journal=jr,
    )
