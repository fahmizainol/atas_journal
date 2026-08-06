"""Post-hoc cut: undigested big sweeps vs upper-band-bounce entries.

Digestion study said an >=100-lot sweep's direction keeps paying for ~10-15
minutes. Question: does entering the upper-band long against a fresh SELL
sweep (still digesting) hurt, and with a fresh BUY sweep help?

Post-hoc split on the CURRENT baseline (v13 a348d176), no knob until this
shows something robust. Windows and thresholds swept openly — this is a lead
hunt, and anything found here needs split-half + a second host before belief.

    uv run python data/research/bigprint-digestion/pair_upperband.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = ROOT / "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176"


def bucket_stats(t: pd.DataFrame, label: str) -> str:
    if len(t) == 0:
        return f"  {label:>12}: n=0"
    r = t["r_multiple"]
    return (f"  {label:>12}: n={len(t):>3}  avgR={r.mean():+.3f}  "
            f"win={(r > 0).mean() * 100:4.1f}%  net=${t['net_pnl'].sum():>9,.0f}  "
            f"avg$={t['net_pnl'].mean():>7,.0f}")


def main() -> None:
    trades = pd.read_parquet(RUN / "trades.parquet")
    print(f"host: {RUN.name}  n={len(trades)}  net=${trades['net_pnl'].sum():,.0f}  "
          f"avgR={trades['r_multiple'].mean():+.3f}")

    m = pd.read_parquet(HERE / "minutes.parquet")
    sw = m[(m["seg"] == "rth") & (m["s_size"].fillna(0) >= 100)][
        ["s_ts", "s_side", "s_size"]].dropna().sort_values("s_ts")
    ts = sw["s_ts"].to_numpy("datetime64[ns]")
    side = sw["s_side"].to_numpy()

    entry = trades["entry_ts_utc"].dt.tz_convert("UTC").dt.tz_localize(None) \
        .to_numpy("datetime64[ns]")

    for win_min in (5, 10, 15, 30):
        lo = np.searchsorted(ts, entry - np.timedelta64(win_min * 60, "s"), "left")
        hi = np.searchsorted(ts, entry, "left")
        n_buy = np.array([(side[a:b] == "B").sum() for a, b in zip(lo, hi)])
        n_sell = np.array([(side[a:b] == "A").sum() for a, b in zip(lo, hi)])
        # freshest big sweep in the window decides the bucket
        last_side = np.where(hi > lo, side[np.maximum(hi - 1, 0)], "-")

        t = trades.copy()
        t["bucket"] = np.select(
            [(n_buy == 0) & (n_sell == 0), last_side == "B", last_side == "A"],
            ["none", "fresh-buy", "fresh-sell"], default="none")
        print(f"\n== lookback {win_min} min, sweeps >=100 ==")
        for b in ("none", "fresh-buy", "fresh-sell"):
            print(bucket_stats(t[t["bucket"] == b], b))

    # split-half on the 15-min cut
    print("\n== split-half (15-min window) ==")
    win = np.timedelta64(15 * 60, "s")
    lo = np.searchsorted(ts, entry - win, "left")
    hi = np.searchsorted(ts, entry, "left")
    last_side = np.where(hi > lo, side[np.maximum(hi - 1, 0)], "-")
    t = trades.copy()
    t["bucket"] = np.select([hi == lo, last_side == "B", last_side == "A"],
                            ["none", "fresh-buy", "fresh-sell"], default="none")
    sessions = sorted(t["session"].unique())
    mid = sessions[len(sessions) // 2]
    for name, half in (("first", t[t["session"] < mid]),
                       ("second", t[t["session"] >= mid])):
        print(f" {name} half (split {mid}):")
        for b in ("none", "fresh-buy", "fresh-sell"):
            print(bucket_stats(half[half["bucket"] == b], b))


if __name__ == "__main__":
    main()
