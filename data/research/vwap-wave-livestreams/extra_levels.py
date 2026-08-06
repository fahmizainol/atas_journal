"""Extra anchors the base session_levels.py does not carry.

Written for the 2026-06-05 / 2026-06-11 pass, where the presenter's "point of
control" and "weekly lower band" calls do not line up with the RTH-only
developing profile. Two candidate explanations, both testable here:

  * his profile is the **ETH session** (Globex 18:00 + RTH) rather than RTH-only,
    which moves the POC a long way on a gap session;
  * "the band" is the **weekly** VWAP anchor (Sunday 18:00 ET), not the daily one.

Usage:
    .venv/bin/python data/research/vwap-wave-livestreams/extra_levels.py 2026-06-11 10:00 11:30
"""
import sys
from datetime import date, time

sys.path.insert(0, "src")
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import weekly as weeklymod
from journal.sim.bars import time_bars
from journal.sim.profile import developing_profile
from journal.sim.vwap import vwap_bands

sys.path.insert(0, "data/research/vwap-wave-livestreams")
from session_levels import ET, TICK, load_day


def eth_and_weekly(day: date, symbol: str = "NQM6") -> pd.DataFrame:
    """1-min RTH bars carrying the ETH-session profile and the weekly VWAP bands."""
    d = load_day(day, symbol)
    open_utc, close_utc = tickmod.session_bounds_utc(day)
    glx_utc, _ = tickmod.overnight_bounds_utc(day)

    # ETH session = Globex open through the RTH close, one continuous profile.
    eth = d[(d.ts_utc >= glx_utc) & (d.ts_utc < close_utc)].reset_index(drop=True)
    ebars = time_bars(eth, "1min")
    ebars["et"] = ebars["ts_utc"].dt.tz_convert(ET)
    eprof = developing_profile(eth, ebars, TICK)
    ebars["eth_poc"], ebars["eth_vah"], ebars["eth_val"] = eprof.poc, eprof.vah, eprof.val

    # Weekly VWAP: seed the frame with the accumulation already behind the anchor.
    seed = weeklymod.weekly_seed(symbol, day)
    if seed is None:
        ebars["wk_mid"] = ebars["wk_l1"] = ebars["wk_l2"] = float("nan")
        ebars["wk_u1"] = ebars["wk_u2"] = float("nan")
    else:
        wb = vwap_bands(eth, seed=seed)
        end = ebars["end_idx"].to_numpy()
        for src, dst in (("mid", "wk_mid"), ("upper1", "wk_u1"), ("lower1", "wk_l1"),
                         ("upper2", "wk_u2"), ("lower2", "wk_l2")):
            ebars[dst] = wb[src].to_numpy()[end]

    return ebars[ebars.ts_utc >= open_utc].reset_index(drop=True)


COLS = ["et", "high", "low", "close", "eth_poc", "eth_vah", "eth_val",
        "wk_mid", "wk_l1", "wk_l2"]

if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1])
    t0 = time.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else time(9, 30)
    t1 = time.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else time(11, 30)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 500)
    b = eth_and_weekly(day)
    m = (b.et.dt.time >= t0) & (b.et.dt.time <= t1)
    w = b.loc[m, COLS].copy()
    w["et"] = w["et"].dt.strftime("%H:%M")
    print(w.round(2).to_string(index=False))
