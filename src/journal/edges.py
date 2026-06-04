"""Behavioral edge breakdowns: time-of-day, weekday, hold-time, direction."""

from __future__ import annotations

import pandas as pd

from .config import ET_TZ

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOLD_BUCKETS = ["<1m", "1-5m", "5-15m", "15m+"]


def _build_session_order() -> list[str]:
    """30-min blocks across the day, but 15-min blocks for the 09:30 open."""
    order: list[str] = []
    for h in range(24):
        for block in (0, 30):
            if h == 9 and block == 30:
                order.extend(["09:30", "09:45"])
            else:
                order.append(f"{h:02d}:{block:02d}")
    return order


SESSION_ORDER = _build_session_order()


def _session_block(ts) -> str:
    """Session start (09:30 ET) splits into 15-min blocks; rest are 30-min."""
    h, m = ts.hour, ts.minute
    if h == 9 and m >= 30:
        return "09:30" if m < 45 else "09:45"
    block = 0 if m < 30 else 30
    return f"{h:02d}:{block:02d}"


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
    """US Eastern session blocks: 15-min around the 09:30 open, 30-min otherwise."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    utc = trades["entry_ts_utc"]
    if utc.dt.tz is None:
        utc = utc.dt.tz_localize("UTC")
    et = utc.dt.tz_convert(ET_TZ)
    blocks = et.apply(_session_block)
    return _by(trades, blocks, order=SESSION_ORDER)


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
