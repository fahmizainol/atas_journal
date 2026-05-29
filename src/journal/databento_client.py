"""Databento historical 1m bars with on-disk parquet caching.

Degrades gracefully: if no API key is configured (or a fetch fails), every
function returns None and the caller shows a friendly notice. All API-cost
paths go through a per-symbol, per-UTC-day parquet cache so repeated chart /
excursion calls never re-hit the API.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pandas as pd

from .config import CACHE_DIR, DATABENTO_DATASET, UTC_TZ, continuous_symbol, databento_key


class DatabentoUnavailable(Exception):
    pass


def is_available() -> bool:
    return databento_key() is not None


def _as_utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _cache_path(symbol: str, day: date):
    return CACHE_DIR / f"{symbol}_{day.isoformat()}.parquet"


def _fetch_day(symbol: str, day: date) -> pd.DataFrame:
    """Fetch one full UTC day of ohlcv-1m bars for a continuous symbol."""
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")

    client = dbn.Historical(key)
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    # Clamp to "now" so we never ask for future minutes.
    now = datetime.now(UTC_TZ).replace(tzinfo=None)
    if end > now:
        end = now
    if end <= start:
        return pd.DataFrame()

    def _query(e: datetime):
        return client.timeseries.get_range(
            dataset=DATABENTO_DATASET, schema="ohlcv-1m", stype_in="continuous",
            symbols=[symbol], start=start, end=e,
        )

    try:
        data = _query(end)
    except dbn.common.error.BentoClientError as exc:
        # Subscription/license boundary: the API tells us the latest allowed end.
        m = re.search(r"end time before (\S+)", str(exc))
        if not m:
            raise
        allowed = pd.Timestamp(m.group(1)).tz_localize(None)
        if allowed <= pd.Timestamp(start):
            return pd.DataFrame()
        data = _query(allowed.to_pydatetime())

    df = data.to_df(price_type="float", pretty_ts=True)
    if df.empty:
        return df
    df = df.reset_index()  # ts_event becomes a column
    ts_col = "ts_event" if "ts_event" in df.columns else df.columns[0]
    df = df.rename(columns={ts_col: "ts_utc"})
    keep = ["ts_utc", "open", "high", "low", "close", "volume"]
    df = df[[c for c in keep if c in df.columns]]
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


def get_day_bars(symbol: str, day: date, use_cache: bool = True) -> pd.DataFrame | None:
    cache = _cache_path(symbol, day)
    if use_cache and cache.exists():
        return pd.read_parquet(cache)
    try:
        df = _fetch_day(symbol, day)
    except DatabentoUnavailable:
        return None
    except Exception:
        return None
    if df is not None and not df.empty:
        df.to_parquet(cache, index=False)
    return df


def get_bars(
    instrument: str,
    start_utc: datetime,
    end_utc: datetime,
    slice_to_window: bool = True,
) -> pd.DataFrame | None:
    """1m bars for an instrument across a UTC window (spans day boundaries).

    With slice_to_window=False, returns every bar for the full UTC day(s) the
    window touches (no trimming) — used so the chart can pan/zoom across the
    whole session from already-cached data.
    """
    symbol = continuous_symbol(instrument)
    start_utc = _as_utc(start_utc)
    end_utc = _as_utc(end_utc)

    days: list[date] = []
    d = start_utc.date()
    while d <= end_utc.date():
        days.append(d)
        d = d + timedelta(days=1)

    frames = []
    for day in days:
        bars = get_day_bars(symbol, day)
        if bars is not None and not bars.empty:
            frames.append(bars)
    if not frames:
        return None
    allbars = pd.concat(frames, ignore_index=True)
    allbars["ts_utc"] = pd.to_datetime(allbars["ts_utc"], utc=True)
    allbars = allbars.sort_values("ts_utc").reset_index(drop=True)
    if not slice_to_window:
        return allbars
    mask = (allbars["ts_utc"] >= start_utc) & (allbars["ts_utc"] <= end_utc)
    return allbars[mask].reset_index(drop=True)
