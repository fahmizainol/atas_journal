"""Rebuild a session's VWAP anchors / developing profile / IB from cached ticks.

Purpose: check a *claimed* level against what was actually on the screen. When a
presenter says "I got long at 268 off the point of control", this answers (a) was
30268 tradeable at that minute, and (b) where was the developing POC. Both
questions are needed — a level that decodes to a real price at the right minute
is a genuine read; one that doesn't is hindsight.

Uses the engine's own primitives (journal.sim.vwap / profile / bars), NOT the
chart's, so the numbers match what the sim trades against rather than a second
sigma definition. See the note at the top of journal/sim/vwap.py.

Two VWAP anchors matter for these teardowns:
  rth_*   anchored 09:30 ET, resets daily     -> the "RTH band" they trade
  gvwap   anchored 18:00 ET prior day, KEEPS  -> the "Globex/ETH VWAP", which is
          DEVELOPING through RTH                 usually the one meant by an
                                                 unqualified "VWAP" pre-10:00
Getting that second one wrong is the most common way to mis-verify a call: a
Globex VWAP frozen at the open sits tens of points away from the developing one.

Usage:
    .venv/bin/python data/research/vwap-wave-livestreams/session_levels.py 2026-06-04
    .venv/bin/python data/research/vwap-wave-livestreams/session_levels.py 2026-06-04 09:30 11:20
"""
import sys
from datetime import date, time, timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim.bars import time_bars
from journal.sim.profile import developing_profile
from journal.sim.vwap import vwap_bands

ET = "America/New_York"
TICK = 0.25
CACHE = "data/cache/ticks"


def load_day(day: date, symbol: str = "NQM6") -> pd.DataFrame:
    df = pd.read_parquet(f"{CACHE}/{symbol}_{day}_day.parquet")
    return df.sort_values("ts_utc").reset_index(drop=True)


def enrich(day: date, symbol: str = "NQM6") -> pd.DataFrame:
    """1-min RTH bars carrying every level a VWAP-system trader would have up."""
    d = load_day(day, symbol)
    open_utc, close_utc = tickmod.session_bounds_utc(day)
    glx_utc, _ = tickmod.overnight_bounds_utc(day)

    rth = d[(d.ts_utc >= open_utc) & (d.ts_utc < close_utc)].reset_index(drop=True)
    bars = time_bars(rth, "1min")
    bars["et"] = bars["ts_utc"].dt.tz_convert(ET)
    end = bars["end_idx"].to_numpy()

    rb = vwap_bands(rth)
    for col in ("mid", "std", "upper1", "upper2", "lower1", "lower2"):
        bars["rth_" + col] = rb[col].to_numpy()[end]

    prof = developing_profile(rth, bars, TICK)
    bars["poc"], bars["vah"], bars["val"] = prof.poc, prof.vah, prof.val

    # Globex-anchored VWAP, still accumulating through RTH (see module docstring).
    cont = d[d.ts_utc >= glx_utc].reset_index(drop=True)
    gb = vwap_bands(cont)
    cbars = time_bars(cont, "1min")
    cend = cbars["end_idx"].to_numpy()
    gmap = pd.DataFrame({
        "ts_utc": cbars["ts_utc"],
        "gvwap": gb["mid"].to_numpy()[cend],
        "gu1": gb["upper1"].to_numpy()[cend],
        "gl1": gb["lower1"].to_numpy()[cend],
    })
    bars = bars.merge(gmap, on="ts_utc", how="left")

    ib = bars[bars.et.dt.time < time(10, 30)]
    if len(ib):
        bars["ib_hi"], bars["ib_lo"] = ib["high"].max(), ib["low"].min()
        bars["ib_mid"] = (bars["ib_hi"] + bars["ib_lo"]) / 2
    return bars


def prior_levels(day: date, symbol: str = "NQM6") -> dict:
    """Prior RTH session's high/low/close/POC/VAH/VAL/IB — the 'yesterday's X'
    levels that get quoted constantly and are trivially checkable."""
    d = load_day(day, symbol)
    open_utc, close_utc = tickmod.session_bounds_utc(day)
    rth = d[(d.ts_utc >= open_utc) & (d.ts_utc < close_utc)].reset_index(drop=True)
    bars = time_bars(rth, "1min")
    bars["et"] = bars["ts_utc"].dt.tz_convert(ET)
    prof = developing_profile(rth, bars, TICK)
    ib = bars[bars.et.dt.time < time(10, 30)]
    return dict(high=rth.price.max(), low=rth.price.min(), close=rth.price.iloc[-1],
                poc=prof.poc[-1], vah=prof.vah[-1], val=prof.val[-1],
                ib_hi=ib["high"].max(), ib_lo=ib["low"].min())


def shape(bars: pd.DataFrame) -> dict:
    """Day shape — the control every single-session claim needs.

    A book of longs on a flush-then-trend-up day proves nothing about the setups;
    it proves the day went up. Always print this before believing a trade log.
    """
    lo, hi = bars.loc[bars.low.idxmin()], bars.loc[bars.high.idxmax()]
    return dict(open=bars.open.iloc[0], close=bars.close.iloc[-1],
                low=lo.low, low_at=lo.et.strftime("%H:%M"),
                high=hi.high, high_at=hi.et.strftime("%H:%M"),
                range=bars.high.max() - bars.low.min(),
                open_to_low=lo.low - bars.open.iloc[0],
                low_to_high=hi.high - lo.low,
                net=bars.close.iloc[-1] - bars.open.iloc[0])


COLS = ["et", "open", "high", "low", "close", "volume", "rth_mid", "rth_upper1",
        "rth_lower1", "gvwap", "poc", "vah", "val"]


def window(bars, t0=time(9, 30), t1=time(11, 30), cols=None):
    m = (bars.et.dt.time >= t0) & (bars.et.dt.time <= t1)
    w = bars.loc[m, cols or COLS].copy()
    w["et"] = w["et"].dt.strftime("%H:%M")
    return w.round(2)


if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1])
    t0 = time.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else time(9, 30)
    t1 = time.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else time(11, 30)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)

    bars = enrich(day)
    print(f"==== {day} shape ====")
    for k, v in shape(bars).items():
        print(f"  {k:12} {v if isinstance(v, str) else round(float(v), 2)}")
    print(f"  IB {bars.ib_lo.iloc[0]:.2f} / {bars.ib_hi.iloc[0]:.2f} "
          f"mid {bars.ib_mid.iloc[0]:.2f}")
    try:
        prev = day - timedelta(days=1)
        while prev.weekday() > 4:
            prev -= timedelta(days=1)
        print("  prior:", {k: round(float(v), 2) for k, v in prior_levels(prev).items()})
    except FileNotFoundError:
        print("  prior: not cached")
    print()
    print(window(bars, t0, t1).to_string(index=False))
