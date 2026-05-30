"""Tier-A session reference levels (overnight + prior-day) from cached 1m bars.

All windows are defined in ET (the CME session reference, DST-aware) and the
values are derived purely from the 1m OHLCV bars the app already fetches, so
nothing is invented. Each level degrades to None when its window has no bars
(e.g. Databento unavailable, or a date with no session). "Prior day" walks back
over weekends/holidays — a non-trading day simply has no RTH bars.

The cash session ("RTH") is 09:30-16:15 ET for CME equity index futures; the
1m bars are stamped at their open, so the 405 RTH bars run 09:30..16:14 and
the 16:14 bar's close is the ~16:15 mark we use as a settlement proxy. The
overnight window is the Globex session before the open: 18:00 ET (prior
evening) .. 09:29 ET.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from . import databento_client as dbn
from .config import ET_TZ

RTH_OPEN = time(9, 30)
RTH_LAST_BAR = time(16, 14)  # open of the final RTH 1m bar (closes ~16:15)
GLOBEX_OPEN = time(18, 0)
ON_LAST_BAR = time(9, 29)  # last overnight bar before the cash open
_MAX_WALKBACK_DAYS = 5

LEVEL_KEYS = ("onh", "onl", "prior_high", "prior_low", "prior_close", "today_open")
LEVEL_LABELS = {
    "onh": "ON high",
    "onl": "ON low",
    "prior_high": "PD high",
    "prior_low": "PD low",
    "prior_close": "prior close ~16:15",
    "today_open": "open",
}


def _et_utc(d: date, t: time) -> datetime:
    """A wall-clock ET time on date `d` as a UTC datetime (DST-aware)."""
    return pd.Timestamp(datetime.combine(d, t), tz=ET_TZ).tz_convert("UTC").to_pydatetime()


def rth_date_for(ts_utc) -> date:
    """The ET calendar date of a UTC instant — the session a NY-open trade belongs to."""
    ts = pd.Timestamp(ts_utc)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.tz_convert(ET_TZ).date()


def _rth_bars(instrument: str, d: date) -> pd.DataFrame | None:
    bars = dbn.get_bars(instrument, _et_utc(d, RTH_OPEN), _et_utc(d, RTH_LAST_BAR))
    if bars is None or bars.empty:
        return None
    return bars.sort_values("ts_utc")


def prior_trading_date(instrument: str, rth_date: date) -> date | None:
    """The most recent date before `rth_date` that actually had a cash session.

    Walks back over weekends/holidays (a non-trading day has no RTH bars), capped
    at _MAX_WALKBACK_DAYS. None if nothing traded in that window.
    """
    cand = rth_date - timedelta(days=1)
    for _ in range(_MAX_WALKBACK_DAYS):
        if _rth_bars(instrument, cand) is not None:
            return cand
        cand -= timedelta(days=1)
    return None


def compute_levels(instrument: str, rth_date: date) -> dict | None:
    """Tier-A levels for the session opening 09:30 ET on `rth_date`.

    Returns {onh, onl, prior_high, prior_low, prior_close, today_open} (float or
    None per key), or None when Databento isn't configured at all.
    """
    if not dbn.is_available():
        return None

    levels: dict[str, float | None] = {k: None for k in LEVEL_KEYS}

    # Overnight: 18:00 ET (prior evening) .. 09:29 ET, before the cash open.
    on_bars = dbn.get_bars(
        instrument, _et_utc(rth_date - timedelta(days=1), GLOBEX_OPEN),
        _et_utc(rth_date, ON_LAST_BAR),
    )
    if on_bars is not None and not on_bars.empty:
        levels["onh"] = float(on_bars["high"].max())
        levels["onl"] = float(on_bars["low"].min())

    # Prior cash session: the last day that actually traded (walk-back).
    prior = prior_trading_date(instrument, rth_date)
    if prior is not None:
        pbars = _rth_bars(instrument, prior)
        if pbars is not None:
            levels["prior_high"] = float(pbars["high"].max())
            levels["prior_low"] = float(pbars["low"].min())
            levels["prior_close"] = float(pbars.iloc[-1]["close"])

    # Today's RTH open.
    today = _rth_bars(instrument, rth_date)
    if today is not None:
        levels["today_open"] = float(today.iloc[0]["open"])

    return levels


def level_relations(
    levels: dict | None, avg_entry: float, avg_exit: float,
    mfe_price: float | None = None, mae_price: float | None = None,
) -> dict | None:
    """Pre-compute entry/exit-relative geometry so the LLM never does arithmetic.

    Signed points are level-minus-reference (positive = level sits above it).
    Returns None when there are no usable levels.
    """
    if not levels:
        return None
    rows = []
    for key in LEVEL_KEYS:
        price = levels.get(key)
        if price is None:
            continue
        rows.append({
            "label": LEVEL_LABELS[key],
            "price": float(price),
            "from_entry": round(float(price) - avg_entry, 2),
            "from_exit": round(float(price) - avg_exit, 2),
        })
    if not rows:
        return None
    rows.sort(key=lambda r: r["price"], reverse=True)

    nearest_entry = min(rows, key=lambda r: abs(r["from_entry"]))

    def _nearest_to(px: float | None) -> dict | None:
        if px is None:
            return None
        near = min(rows, key=lambda r: abs(r["price"] - px))
        return {"label": near["label"], "price": round(px, 2),
                "gap": round(near["price"] - px, 2)}

    return {
        "rows": rows,
        "nearest_entry": {"label": nearest_entry["label"], "dist": nearest_entry["from_entry"]},
        "mfe": _nearest_to(mfe_price),
        "mae": _nearest_to(mae_price),
    }
