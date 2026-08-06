"""Build the NY-upper-band × 9/20-EMA minute feature frame.

One row per (session, RTH minute). Carries the NY-anchored VWAP bands (09:30-ET
anchor) and the 9/20 EMA (1-minute, adjust=False, warmed over the overnight+RTH
minute stream exactly like ``interactions._ema_rows``) sampled onto the same
1-minute grid, plus the derived distance/stretch/slope features the three study
angles read.

    Usage: .venv/bin/python data/research/market-structure/nyema_build.py

Writes nyema_minutes.parquet next to this file. Downstream:
  - nyema_events.py   — band-touch events, forward outcomes, the 3 angles
  - nyema_trades.py   — join the a348d176 upper-band run entries to EMA state
"""
import sys, time
from datetime import date, timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import regime as regmod
from journal.sim import vwap as vwapmod

TICK = 0.25
START, END = date(2025, 2, 3), date(2026, 6, 30)
OUTDIR = "data/research/market-structure"
SLOPE_MIN = 5          # EMA slope measured over the last 5 emitted minute bars
# Keep every RTH minute in the frame — the young-σ warmup is filtered downstream
# (event study only), so the trade join can still see early-session entries.


def _ema_grid(full: pd.DataFrame) -> pd.DataFrame:
    """9/20 EMA + short slopes on 1-minute bars over the ON+RTH stream, indexed
    by floored-minute ts_utc. Mirrors interactions._ema_rows (ewm adjust=False)."""
    mb = regmod.minute_bars(full)
    if mb.empty:
        return pd.DataFrame()
    c = mb["close"].astype("float64")
    e9 = c.ewm(span=9, adjust=False).mean()
    e20 = c.ewm(span=20, adjust=False).mean()
    out = pd.DataFrame({
        "ema9": e9.to_numpy(),
        "ema20": e20.to_numpy(),
        "ema9_slope": (e9 - e9.shift(SLOPE_MIN)).to_numpy(),
        "ema20_slope": (e20 - e20.shift(SLOPE_MIN)).to_numpy(),
    }, index=pd.DatetimeIndex(mb["ts_utc"]))
    return out


def build():
    t0 = time.time()
    rows = []
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

        # NY bands: anchored at the 09:30 open == the first RTH tick.
        bands = vwapmod.vwap_bands(rth)
        mb_rth = regmod.minute_bars(rth)
        if mb_rth.empty:
            day += timedelta(days=1); continue
        eidx = mb_rth["end_idx"].to_numpy()
        up1 = bands["upper1"].to_numpy()[eidx]
        up2 = bands["upper2"].to_numpy()[eidx]
        mid = bands["mid"].to_numpy()[eidx]
        std = bands["std"].to_numpy()[eidx]

        # 9/20 EMA warmed over ON+RTH, aligned to the RTH minute grid.
        eg = _ema_grid(full)
        if eg.empty:
            day += timedelta(days=1); continue
        eg = eg.reindex(pd.DatetimeIndex(mb_rth["ts_utc"]))

        close = mb_rth["close"].to_numpy(dtype="float64")
        high = mb_rth["high"].to_numpy(dtype="float64")
        low = mb_rth["low"].to_numpy(dtype="float64")
        ema9 = eg["ema9"].to_numpy()
        ema20 = eg["ema20"].to_numpy()

        with np.errstate(divide="ignore", invalid="ignore"):
            sig = np.where(std > 0, (close - up1) / std, np.nan)

        n = len(mb_rth)
        for i in range(n):
            rows.append({
                "session": str(day),
                "ts_utc": mb_rth["ts_utc"].iloc[i],
                "minute_idx": i,                      # minutes since 09:30
                "close": close[i], "high": high[i], "low": low[i],
                "mid": mid[i], "std": std[i], "upper1": up1[i], "upper2": up2[i],
                "ema9": ema9[i], "ema20": ema20[i],
                "ema9_slope": eg["ema9_slope"].to_numpy()[i],
                "ema20_slope": eg["ema20_slope"].to_numpy()[i],
                # --- derived, all distances in TICKS unless _sig ---
                "ema_gap": (ema9[i] - ema20[i]) / TICK,          # >0 = stacked bull
                "d_ema9_up1": (ema9[i] - up1[i]) / TICK,         # ema9 vs the band
                "d_ema20_up1": (ema20[i] - up1[i]) / TICK,
                "d_px_up1": (close[i] - up1[i]) / TICK,          # >0 = price above band
                "d_px_up1_sig": sig[i],                          # price-to-band in σ
                "stretch9": (close[i] - ema9[i]) / TICK,         # price extension over fast EMA
                "bandw": (up2[i] - up1[i]) / TICK,               # 1σ band width in ticks
            })
        n_sess += 1
        if n_sess % 40 == 0:
            print(f"  {n_sess} sessions, {len(rows)} minutes, "
                  f"{time.time()-t0:.0f}s  (through {day})", flush=True)
        day += timedelta(days=1)

    df = pd.DataFrame(rows)
    out = f"{OUTDIR}/nyema_minutes.parquet"
    df.to_parquet(out, index=False)
    print(f"\nWrote {out}: {len(df)} minute-rows over {n_sess} sessions "
          f"in {time.time()-t0:.0f}s")
    print(df[["d_px_up1", "d_ema9_up1", "ema_gap", "stretch9"]].describe().round(2).to_string())


if __name__ == "__main__":
    build()
