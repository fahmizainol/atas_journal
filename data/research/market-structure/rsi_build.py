"""Build the 1-minute RSI grid for the NY-upper-band × RSI study.

Sibling of nyema_build.py: Wilder RSI (14/5/2) computed over the same
overnight+RTH 1-minute close stream the 9/20 EMA used (so the open isn't
unwarmed), then sampled onto the RTH minute grid. Output joins 1:1 with
nyema_minutes.parquet on (session, ts_utc).

    Usage: .venv/bin/python data/research/market-structure/rsi_build.py

Writes rsi_minutes.parquet next to this file. Downstream:
  - rsi_events.py — RSI state at the +1σ band-touch events
  - rsi_trades.py — join the a348d176 upper-band run entries to RSI state
"""
import sys, time
from datetime import date, timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import regime as regmod

START, END = date(2025, 2, 3), date(2026, 6, 30)
OUTDIR = "data/research/market-structure"
PERIODS = [14, 5, 2]
SLOPE_MIN = 5  # RSI slope over the last 5 emitted minute bars, like the EMA study


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0.0)
    loss = (-d).clip(lower=0.0)
    ag = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    al = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + ag / al)
    return rsi.where(al > 0, 100.0).where(ag + al > 0, np.nan)


def _rsi_grid(full: pd.DataFrame) -> pd.DataFrame:
    mb = regmod.minute_bars(full)
    if mb.empty:
        return pd.DataFrame()
    c = mb["close"].astype("float64")
    out = {}
    for p in PERIODS:
        r = wilder_rsi(c, p)
        out[f"rsi{p}"] = r.to_numpy()
        if p == 14:
            out["rsi14_slope"] = (r - r.shift(SLOPE_MIN)).to_numpy()
    return pd.DataFrame(out, index=pd.DatetimeIndex(mb["ts_utc"]))


def build():
    t0 = time.time()
    frames = []
    day = START
    n_sess = 0
    while day <= END:
        if day.weekday() >= 5:
            day += timedelta(days=1); continue
        sym = tickmod.contract_for_cached("NQ", day)
        if sym is None:
            day += timedelta(days=1); continue
        full = tickmod.get_day_ticks(sym, day, include_overnight=True)
        rth = tickmod.cached_rth(sym, day)
        if full is None or full.empty or rth is None or rth.empty:
            day += timedelta(days=1); continue
        mb_rth = regmod.minute_bars(rth)
        if mb_rth.empty:
            day += timedelta(days=1); continue
        rg = _rsi_grid(full)
        if rg.empty:
            day += timedelta(days=1); continue
        rg = rg.reindex(pd.DatetimeIndex(mb_rth["ts_utc"]))
        rg.insert(0, "session", str(day))
        rg.insert(1, "ts_utc", mb_rth["ts_utc"].to_numpy())
        rg.insert(2, "minute_idx", np.arange(len(mb_rth)))
        frames.append(rg.reset_index(drop=True))
        n_sess += 1
        if n_sess % 40 == 0:
            print(f"  {n_sess} sessions, {time.time()-t0:.0f}s (through {day})", flush=True)
        day += timedelta(days=1)

    df = pd.concat(frames, ignore_index=True)
    out = f"{OUTDIR}/rsi_minutes.parquet"
    df.to_parquet(out, index=False)
    print(f"\nWrote {out}: {len(df)} minute-rows over {n_sess} sessions "
          f"in {time.time()-t0:.0f}s")
    print(df[[f"rsi{p}" for p in PERIODS] + ["rsi14_slope"]].describe().round(2).to_string())


if __name__ == "__main__":
    build()
