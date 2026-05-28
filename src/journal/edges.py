"""Behavioral edge breakdowns: time-of-day, weekday, hold-time, direction."""

from __future__ import annotations

import pandas as pd

from .config import ET_TZ

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOLD_BUCKETS = ["<1m", "1-5m", "5-15m", "15m+"]


def _hold_bucket(seconds: float) -> str:
    if seconds < 60:
        return "<1m"
    if seconds < 300:
        return "1-5m"
    if seconds < 900:
        return "5-15m"
    return "15m+"


def _summarize(grp: pd.DataFrame) -> pd.Series:
    pnl = grp["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n = len(pnl)
    win_rate = len(wins) / n * 100 if n else 0.0
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    expectancy = (len(wins) / n) * avg_win + (len(losses) / n) * avg_loss if n else 0.0
    return pd.Series({
        "trades": n,
        "net_pnl": float(pnl.sum()),
        "win_rate": win_rate,
        "expectancy": float(expectancy),
    })


def _by(trades: pd.DataFrame, key: pd.Series, order: list | None = None) -> pd.DataFrame:
    df = trades.copy()
    df["_k"] = key
    out = df.groupby("_k", group_keys=False).apply(_summarize).reset_index()
    out = out.rename(columns={"_k": "bucket"})
    if order is not None:
        out["bucket"] = pd.Categorical(out["bucket"], categories=order, ordered=True)
        out = out.sort_values("bucket")
    return out.reset_index(drop=True)


def by_hour_kl(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    return _by(trades, trades["entry_ts_local"].dt.hour, order=list(range(24)))


def by_hour_et(trades: pd.DataFrame) -> pd.DataFrame:
    """Hour-of-day in US Eastern (CME session reference)."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    utc = trades["entry_ts_utc"]
    if utc.dt.tz is None:
        utc = utc.dt.tz_localize("UTC")
    et_hour = utc.dt.tz_convert(ET_TZ).dt.hour
    return _by(trades, et_hour, order=list(range(24)))


def by_weekday(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    wd = trades["entry_ts_local"].dt.dayofweek.map(dict(enumerate(WEEKDAYS)))
    return _by(trades, wd, order=WEEKDAYS)


def by_hold_time(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    return _by(trades, trades["duration_s"].apply(_hold_bucket), order=HOLD_BUCKETS)


def by_direction(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    return _by(trades, trades["direction"], order=["Long", "Short"])
