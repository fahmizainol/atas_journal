"""Multi-session anchors: the monthly VWAP and the N-day composite volume profile.

Both are things this presenter quotes constantly ("rejected out of monthly again",
"that's the point of control from late May", "I didn't change my composite from
seven to nine") and neither existed in the repo. They are the only two pieces of
his toolkit that the daily/weekly anchors cannot stand in for, so a call that
misses every session level by 50-100 points is not necessarily a bad call — it may
just be measured against a chart we were not drawing.

Both are built ETH (Globex 18:00 open through the RTH close) from the tick cache,
same contract only. Honest absence: a missing session raises rather than silently
shortening the lookback, because a composite quietly built over 6 of 9 days is
worse than no composite at all.

Usage:
    .venv/bin/python data/research/vwap-wave-livestreams/composite_levels.py 2026-06-11 10:20 11:30
"""
import sys
from datetime import date, time, timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim.bars import time_bars
from journal.sim.vwap import vwap_bands

sys.path.insert(0, "data/research/vwap-wave-livestreams")
from session_levels import ET, TICK, load_day

CACHE = "data/cache/ticks"


def _eth(day: date, symbol: str) -> pd.DataFrame:
    """One session's ETH ticks: Globex open (18:00 ET prior day) -> RTH close."""
    d = load_day(day, symbol)
    glx_utc, _ = tickmod.overnight_bounds_utc(day)
    _, close_utc = tickmod.session_bounds_utc(day)
    return d[(d.ts_utc >= glx_utc) & (d.ts_utc < close_utc)].reset_index(drop=True)


def cached_sessions(symbol: str, upto: date, n: int) -> list[date]:
    """The n cached sessions strictly before *upto*, most recent last."""
    out, probe = [], upto - timedelta(days=1)
    while len(out) < n and probe > upto - timedelta(days=n * 3 + 10):
        from pathlib import Path
        if Path(f"{CACHE}/{symbol}_{probe}_day.parquet").exists():
            out.append(probe)
        probe -= timedelta(days=1)
    if len(out) < n:
        raise SystemExit(f"only {len(out)} of {n} sessions cached before {upto}")
    return sorted(out)


def composite_profile(days: list[date], symbol: str) -> pd.Series:
    """Volume-at-price over a closed set of prior sessions, indexed by price."""
    hist: dict[float, float] = {}
    for d in days:
        t = _eth(d, symbol)
        lv = np.rint(t["price"].to_numpy() / TICK).astype("int64")
        agg = pd.Series(t["size"].to_numpy(dtype="float64")).groupby(lv).sum()
        for k, v in agg.items():
            hist[k * TICK] = hist.get(k * TICK, 0.0) + float(v)
    return pd.Series(hist).sort_index()


def composite_nodes(prof: pd.Series, n: int = 6) -> dict:
    """POC plus the low-volume nodes — the shelf edges he calls 'composite LVN'.

    An LVN here is a local minimum of the smoothed volume curve holding less than
    35% of the composite POC's volume: the thin pockets price is said to travel
    through quickly. Smoothing first stops every one-tick hole counting as a node.
    """
    sm = prof.rolling(41, center=True, min_periods=1).mean()
    poc = float(sm.idxmax())
    v = sm.to_numpy()
    px = sm.index.to_numpy()
    lows = [i for i in range(2, len(v) - 2)
            if v[i] == min(v[i - 2:i + 3]) and v[i] < 0.35 * v.max()]
    lows.sort(key=lambda i: v[i])
    return dict(poc=poc, lvns=sorted(float(px[i]) for i in lows[:n]))


def monthly_vwap(day: date, symbol: str, t0: time, t1: time) -> pd.DataFrame:
    """Month-to-date VWAP + bands, anchored at the month's first cached session."""
    from pathlib import Path
    first = date(day.year, day.month, 1)
    prior, probe = [], first
    while probe < day:
        if Path(f"{CACHE}/{symbol}_{probe}_day.parquet").exists():
            prior.append(probe)
        probe += timedelta(days=1)

    # Seed = everything the month accumulated before today's Globex open.
    sv = spv = sp2v = 0.0
    for d in prior:
        t = _eth(d, symbol)
        p, s = t["price"].to_numpy(dtype="float64"), t["size"].to_numpy(dtype="float64")
        sv += s.sum(); spv += (p * s).sum(); sp2v += (p * p * s).sum()

    today = _eth(day, symbol)
    bands = vwap_bands(today, seed=(sv, spv, sp2v))
    bars = time_bars(today, "1min")
    bars["et"] = bars["ts_utc"].dt.tz_convert(ET)
    end = bars["end_idx"].to_numpy()
    for src, dst in (("mid", "m_mid"), ("upper1", "m_u1"), ("lower1", "m_l1"),
                     ("upper2", "m_u2"), ("lower2", "m_l2")):
        bars[dst] = bands[src].to_numpy()[end]
    m = (bars.et.dt.time >= t0) & (bars.et.dt.time <= t1)
    out = bars.loc[m, ["et", "high", "low", "m_mid", "m_l1", "m_l2", "m_u1"]].copy()
    out["et"] = out["et"].dt.strftime("%H:%M")
    return out.round(2)


if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1])
    t0 = time.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else time(9, 30)
    t1 = time.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else time(11, 30)
    sym = sys.argv[4] if len(sys.argv) > 4 else "NQM6"
    pd.set_option("display.width", 200); pd.set_option("display.max_rows", 400)

    days = cached_sessions(sym, day, 9)
    print(f"==== 9-day composite ({days[0]} .. {days[-1]}) ====")
    nodes = composite_nodes(composite_profile(days, sym))
    print(f"  composite POC {nodes['poc']:.2f}")
    print("  composite LVNs " + ", ".join(f"{x:.2f}" for x in nodes["lvns"]))
    print(f"\n==== month-to-date VWAP, {day} ====")
    print(monthly_vwap(day, sym, t0, t1).to_string(index=False))
