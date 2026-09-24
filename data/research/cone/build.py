"""Cone kill test, stage 1: features and labels for "how far does price travel?"

The retrieval work (forward-fan, twins, motifs) asked whether look-alike states
have look-alike futures, and answered no four times. This asks the supervised
version of the only question that survived: not WHICH WAY price goes, but HOW
FAR it travels — the quantity a stop is placed against.

One row per RTH minute. Everything is read at the minute's close and uses only
closed bars; every label is strictly in the future.

    features   vol family : the ruler (median 1-min range) at 5/15/30 min,
                            realised vol, range expansion, time of day
               geometry    : signed distance to NY/Globex/weekly VWAP, their
                            band half-widths, NY+Globex POC/VAH/VAL, and where
                            price sits inside band and value area — all in
                            RULER UNITS, so the geometry carries no volatility
                            information of its own
               arrival     : net run over 5/15 min, position in the 15-min range

    labels     up_h  = (max high over t+1..t+h) - close_t      [ticks]
               dn_h  = close_t - (min low  over t+1..t+h)      [ticks]
               for h in 5, 15, 30 minutes

The split between families is the whole point: `fit.py` trains on vol alone and
on vol+geometry, so "does the chart tell you anything the ruler doesn't" is an
ablation rather than an opinion.

    .venv/bin/python data/research/cone/build.py

Writes ``panel.parquet`` beside this file.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DAYS = ROOT / "data" / "research" / "forward-fan" / "days.parquet"
OUT = HERE / "panel.parquet"

TICK = 0.25
BELL = 15 * 60 + 30          # 09:30 as minutes from 18:00
CLOSE = BELL + 390           # 16:00
HORIZONS = (5, 15, 30)
RULER_WINDOWS = (5, 15, 30)
MIN_BARS = 3

#: (column, label) for the levels the geometry family measures distance to.
LEVELS = [("nyv", "nyvwap"), ("gxv", "gxvwap"),
          ("nypoc", "nypoc"), ("nyvah", "nyvah"), ("nyval", "nyval"),
          ("gxpoc", "gxpoc"), ("gxvah", "gxvah"), ("gxval", "gxval")]

VOL_FEATS = (["ruler_5", "ruler_15", "ruler_30", "rv_15", "rv_30", "expand", "tod"])
GEO_FEATS = ([f"d_{lab}" for _, lab in LEVELS]
             + ["nyband", "gxband", "z_nyband", "z_nyva"])
ARR_FEATS = ["run5", "run15", "loc15"]


def fwd_extremes(arr: np.ndarray, h: int, how: str) -> np.ndarray:
    """max/min of ``arr[t+1 : t+h+1]`` for every t; NaN where it runs off."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n <= h:
        return out
    win = np.lib.stride_tricks.sliding_window_view(arr, h)   # win[i] = arr[i:i+h]
    vals = win.max(axis=1) if how == "max" else win.min(axis=1)
    out[:len(vals) - 1] = vals[1:]                            # t -> window at t+1
    return out


def one_day(g: pd.DataFrame) -> pd.DataFrame | None:
    s = g.iloc[BELL:CLOSE].reset_index(drop=True)
    if len(s) < 300 or s["high"].isna().mean() > 0.02 or s["nyv"].isna().mean() > 0.5:
        return None
    close = s["close"].to_numpy(float)
    high, low = s["high"].to_numpy(float), s["low"].to_numpy(float)
    rng = (high - low) / TICK
    n = len(s)

    out = {"day": g["day"].iloc[0], "tod": np.arange(n, dtype=np.int16)}

    # --- vol family. Closed bars only: bar t has closed at t's close, so a
    # trailing window ending at t is settled.
    r = pd.Series(rng)
    for w in RULER_WINDOWS:
        out[f"ruler_{w}"] = r.rolling(w, min_periods=MIN_BARS).median().to_numpy()
    ret = pd.Series(np.diff(close, prepend=close[0]) / TICK)
    for w in (15, 30):
        out[f"rv_{w}"] = ret.rolling(w, min_periods=MIN_BARS).std().to_numpy()
    # Is the tape speeding up or settling down? A ratio, so it is unitless.
    out["expand"] = out["ruler_5"] / np.where(out["ruler_30"] > 0, out["ruler_30"], np.nan)

    ruler = out["ruler_30"]
    safe = np.where(ruler > 0, ruler, np.nan)

    # --- geometry family, in ruler units so it carries no volatility of its own.
    for col, lab in LEVELS:
        out[f"d_{lab}"] = (close - s[col].ffill().to_numpy(float)) / TICK / safe
    nyhalf = (s["nyu"].ffill().to_numpy(float) - s["nyv"].ffill().to_numpy(float))
    gxhalf = (s["gxu"].ffill().to_numpy(float) - s["gxv"].ffill().to_numpy(float))
    vahalf = (s["nyvah"].ffill().to_numpy(float) - s["nyval"].ffill().to_numpy(float)) / 2
    out["nyband"] = nyhalf / TICK / safe
    out["gxband"] = gxhalf / TICK / safe
    out["z_nyband"] = np.clip(np.where(nyhalf > 0, (close - s["nyv"].ffill().to_numpy(float)) / np.where(nyhalf > 0, nyhalf, 1), 0), -4, 4)
    out["z_nyva"] = np.clip(np.where(vahalf > 0, (close - s["nypoc"].ffill().to_numpy(float)) / np.where(vahalf > 0, vahalf, 1), 0), -4, 4)

    # --- arrival
    for w in (5, 15):
        prev = np.concatenate([np.full(w, np.nan), close[:-w]])
        out[f"run{w}"] = (close - prev) / TICK / safe
    hi15 = pd.Series(high).rolling(15, min_periods=15).max().to_numpy()
    lo15 = pd.Series(low).rolling(15, min_periods=15).min().to_numpy()
    span = hi15 - lo15
    out["loc15"] = np.where(span > 0, (close - lo15) / np.where(span > 0, span, 1), np.nan)

    # --- labels: how far price actually travelled, using intrabar extremes.
    for h in HORIZONS:
        out[f"up_{h}"] = (fwd_extremes(high, h, "max") - close) / TICK
        out[f"dn_{h}"] = (close - fwd_extremes(low, h, "min")) / TICK

    return pd.DataFrame(out)


def main() -> None:
    t0 = time.time()
    df = pd.read_parquet(DAYS)
    frames = []
    for d, g in df.groupby("day"):
        r = one_day(g.sort_values("i").reset_index(drop=True))
        if r is not None:
            frames.append(r)
    panel = pd.concat(frames, ignore_index=True)
    feats = VOL_FEATS + GEO_FEATS + ARR_FEATS
    labels = [f"{s}_{h}" for h in HORIZONS for s in ("up", "dn")]
    before = len(panel)
    panel = panel.dropna(subset=feats + labels).reset_index(drop=True)
    panel.to_parquet(OUT, index=False)
    print(f"{len(panel):,} rows over {panel['day'].nunique()} sessions "
          f"({before - len(panel):,} dropped for warm-up / truncated forward) "
          f"in {time.time() - t0:.0f}s")
    print(f"  features: {len(VOL_FEATS)} vol + {len(GEO_FEATS)} geometry + {len(ARR_FEATS)} arrival")
    print(panel[["ruler_30"] + [f"dn_{h}" for h in HORIZONS]].describe().round(1).to_string())


if __name__ == "__main__":
    main()
