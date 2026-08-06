"""ATR × vwap-upper-band-bounce — feature builder.

One row per trade, for two runs (v13 gate-stack a348d176 + v10 pre-gate
cdc07ca2), carrying causal ATR measures next to the engine's own outcomes:

  - daily_atr14      Wilder ATR(14) of globex-day true range, through the
                     *prior* session (shifted; the trade day never sees its
                     own range).
  - datr_pctl60      percentile of daily_atr14 within the trailing 60 sessions.
  - tr_prev_pts      prior session's raw true range.
  - atr1m14 / atr5m14   intraday Wilder ATR at entry, from 1-min/5-min bars
                     built over the ON+RTH tick stream; sampled at the last
                     bar that CLOSED strictly before the entry minute.
  - atr1m14_0930     the same 1-min ATR frozen at 09:30 ET (pre-RTH, so it
                     cannot contain the entry move — leak-resistant anchor).
  - range_sofar_pts / rth_range_sofar_pts   globex/RTH high-low up to (and
                     excluding) the entry minute.

    Usage: uv run python data/research/atr-band/build_features.py

Writes daily_atr.parquet + features_<run>.parquet next to this file.
"""
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal import atr as atrmod
from journal.sim import regime as regmod
from journal.sim import ticks as tickmod

OUTDIR = "data/research/atr-band"
RUNS = {
    "a348d176": "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176/trades.parquet",
    "cdc07ca2": "data/sims/vwap-upper-band-bounce/20250201-20260630-v10-cdc07ca2/trades.parquet",
}
START, END = date(2025, 2, 3), date(2026, 6, 30)


def all_sessions() -> list[date]:
    import glob, os
    days = set()
    for p in glob.glob("data/cache/ticks/*_rth.parquet"):
        base = os.path.basename(p)
        try:
            d = date.fromisoformat(base.split("_")[1])
        except (IndexError, ValueError):
            continue
        if START <= d <= END:
            days.add(d)
    return sorted(days)


def daily_bars(days: list[date]) -> pd.DataFrame:
    """Globex-day OHLC (on+rth+post segments) per session date."""
    rows = []
    for d in days:
        sym = tickmod.contract_for_cached("NQ", d)
        if sym is None:
            continue
        segs = []
        for seg in ("on", "rth", "post"):
            try:
                t = tickmod._get_segment(sym, d, seg, use_cache=True)
            except Exception:
                t = None
            if t is not None and not t.empty:
                segs.append(t)
        if not segs:
            continue
        px = pd.concat(segs, ignore_index=True)["price"].astype(float)
        rows.append({"session": d.isoformat(), "open": px.iloc[0],
                     "high": px.max(), "low": px.min(), "close": px.iloc[-1]})
    db = pd.DataFrame(rows).sort_values("session").reset_index(drop=True)
    atr = atrmod.atr_series(db, period=14)
    db["daily_atr14_raw"] = atr
    # Shift: the value a trader knows entering session d is ATR through d-1.
    db["daily_atr14"] = atr.shift(1)
    prev_close = db["close"].shift(1)
    tr = pd.concat([db["high"] - db["low"],
                    (db["high"] - prev_close).abs(),
                    (db["low"] - prev_close).abs()], axis=1).max(axis=1)
    db["tr_pts"] = tr
    db["tr_prev_pts"] = tr.shift(1)
    db["datr_pctl60"] = (
        db["daily_atr14"].rolling(60, min_periods=20)
        .apply(lambda w: (w.iloc[:-1] <= w.iloc[-1]).mean(), raw=False)
    )
    return db


def entry_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Intraday ATR / range-so-far at each entry, one session's ticks at a time."""
    out = []
    for sess, grp in trades.groupby("session"):
        d = date.fromisoformat(str(sess)[:10])
        sym = tickmod.contract_for_cached("NQ", d)
        full = tickmod.get_day_ticks(sym, d, include_overnight=True) if sym else None
        if full is None or full.empty:
            for _ in range(len(grp)):
                out.append({})
            continue
        mb1 = regmod.minute_bars(full)                 # 1-min, ts = bar open
        mb5 = regmod.minute_bars(full, freq="5min")
        a1 = atrmod.atr_series(mb1, 14)
        a5 = atrmod.atr_series(mb5, 14)
        ts1 = pd.to_datetime(mb1["ts_utc"])
        ts5 = pd.to_datetime(mb5["ts_utc"])
        rth_open = tickmod.session_bounds_utc(d)[0]
        # ATR state at 09:30: last 1-min bar that closed at/before the open.
        pre = ts1 + pd.Timedelta(minutes=1) <= rth_open
        atr_0930 = a1[pre].iloc[-1] if pre.any() else np.nan
        for _, tr in grp.iterrows():
            ets = pd.Timestamp(tr["entry_ts_utc"])
            if ets.tz is None:
                ets = ets.tz_localize("UTC")
            m1 = ts1 + pd.Timedelta(minutes=1) <= ets.floor("min")
            m5 = ts5 + pd.Timedelta(minutes=5) <= ets.floor("min")
            closed1 = mb1[m1]
            rowd = {
                "atr1m14": a1[m1].iloc[-1] if m1.any() else np.nan,
                "atr5m14": a5[m5].iloc[-1] if m5.any() else np.nan,
                "atr1m14_0930": atr_0930,
                "range_sofar_pts": (closed1["high"].max() - closed1["low"].min())
                                   if m1.any() else np.nan,
            }
            rth1 = closed1[ts1[m1] >= rth_open]
            rowd["rth_range_sofar_pts"] = (
                rth1["high"].max() - rth1["low"].min()) if len(rth1) else np.nan
            out.append(rowd)
    return pd.DataFrame(out, index=trades.index)


def main():
    days = all_sessions()
    print(f"{len(days)} sessions {days[0]} → {days[-1]}")
    db = daily_bars(days)
    db.to_parquet(f"{OUTDIR}/daily_atr.parquet")
    print(f"daily bars: {len(db)} rows, ATR14 median {db['daily_atr14'].median():.1f} pts")

    for run, path in RUNS.items():
        t = pd.read_parquet(path).reset_index(drop=True)
        t["session"] = t["session"].astype(str).str[:10]
        t = t.merge(db[["session", "daily_atr14", "datr_pctl60", "tr_prev_pts"]],
                    on="session", how="left")
        feats = entry_features(t)
        t = pd.concat([t, feats], axis=1)
        t.to_parquet(f"{OUTDIR}/features_{run}.parquet")
        print(f"{run}: {len(t)} trades, "
              f"atr1m14 non-null {t['atr1m14'].notna().mean():.0%}, "
              f"daily_atr14 non-null {t['daily_atr14'].notna().mean():.0%}")


if __name__ == "__main__":
    main()
