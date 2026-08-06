"""Big-print digestion study — extract per-minute bars + biggest print/sweep.

Null-check of the MatFinOg "liquid gold from MBO data" claims (2026-08-02 video)
on our own cache:

  (a) following the minute's biggest large print has an edge that builds to a
      15-20 min "digestion" plateau (his named cell: >=100 lots, RTH, 15 min);
  (b) size has a ceiling — 70-190 lots informative, >=200 inverts (exhaustion);
  (c) a print in the signal bar's wick carries the edge, mid-body carries none
      (screened here against a price-only rejection-bar control).

One row per (session, ET minute): OHLCV + the largest single print and the
largest sweep that started in that minute (side, price, position in the bar's
range). Sweeps glue consecutive same-side fills within SWEEP_GAP_MS /
SWEEP_SPAN_PTS — a 100-lot order works the book as many prints; the sweep is
the order-shaped unit (same rule as demo/big_trades_demo.py).

Cache-only (reads data/cache/ticks/*_day.parquet, never fetches).

    uv run python data/research/bigprint-digestion/extract.py
"""

from __future__ import annotations

import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal.config import ET_TZ  # noqa: E402

OUT = Path(__file__).resolve().parent / "minutes.parquet"

BUY, SELL = "B", "A"          # measured: B lifts the offer (tick-aggressor-side-encoding)
SWEEP_GAP_MS = 250
SWEEP_SPAN_PTS = 1.00
MIN_KEEP = 20                 # only keep print/sweep info when >= this many lots


def sweeps(df: pd.DataFrame) -> pd.DataFrame:
    """demo/big_trades_demo.py sweep gluing; side 'N' breaks runs and is dropped."""
    ts = df["ts_utc"].to_numpy("datetime64[ns]")
    side = df["side"].to_numpy()
    price = df["price"].to_numpy(dtype=float)

    gap_ms = np.diff(ts).astype("timedelta64[ms]").astype(float)
    new_run = np.empty(len(df), dtype=bool)
    new_run[0] = True
    new_run[1:] = (gap_ms > SWEEP_GAP_MS) | (side[1:] != side[:-1])
    run = np.cumsum(new_run) - 1
    anchor = price[new_run]
    while True:
        far = np.abs(price - anchor[run]) > SWEEP_SPAN_PTS
        if not far.any():
            break
        new_run |= far
        run = np.cumsum(new_run) - 1
        anchor = price[new_run]

    out = pd.DataFrame({"run": run, "ts_utc": df["ts_utc"].to_numpy(),
                        "price": price, "size": df["size"].to_numpy(dtype=float),
                        "side": side})
    g = out.groupby("run", sort=True)
    sw = g.agg(ts_utc=("ts_utc", "first"), price=("price", "last"),
               size=("size", "sum"), side=("side", "first"))
    return sw[sw["side"] != "N"].reset_index(drop=True)


def biggest_per_minute(ev: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Largest event per ET minute -> columns {prefix}_size/side/price/ts."""
    ev = ev[ev["size"] >= MIN_KEEP]
    if ev.empty:
        return pd.DataFrame(columns=["minute", f"{prefix}_size", f"{prefix}_side",
                                     f"{prefix}_price", f"{prefix}_ts"]).set_index("minute")
    minute = ev["ts_utc"].dt.tz_convert(ET_TZ).dt.floor("min").dt.tz_localize(None)
    idx = ev.groupby(minute)["size"].idxmax()
    top = ev.loc[idx].copy()
    top["minute"] = minute.loc[idx].to_numpy()
    top = top.set_index("minute")
    return top.rename(columns={"size": f"{prefix}_size", "side": f"{prefix}_side",
                               "price": f"{prefix}_price", "ts_utc": f"{prefix}_ts"})[
        [f"{prefix}_size", f"{prefix}_side", f"{prefix}_price", f"{prefix}_ts"]]


def one_session(path: str) -> pd.DataFrame | None:
    sym, day_s, _ = Path(path).stem.split("_")
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df = df.sort_values("ts_utc").reset_index(drop=True)
    df["size"] = df["size"].astype("int64")

    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    df["_min"] = et.dt.floor("min").dt.tz_localize(None)

    g = df.groupby("_min", sort=True)
    bars = g.agg(open=("price", "first"), high=("price", "max"),
                 low=("price", "min"), close=("price", "last"),
                 volume=("size", "sum"), seg=("seg", "first"))

    prints = biggest_per_minute(df[df["side"] != "N"][["ts_utc", "price", "size", "side"]],
                                "p")
    sw = biggest_per_minute(sweeps(df), "s")
    bars = bars.join(prints).join(sw)

    bars = bars.reset_index().rename(columns={"_min": "minute"})
    bars.insert(0, "day", datetime.strptime(day_s, "%Y-%m-%d").date())
    bars.insert(1, "symbol", sym)
    return bars


def main() -> None:
    files = sorted(glob.glob(str(ROOT / "data/cache/ticks/*_day.parquet")))
    # one file per calendar day: on roll overlap keep the bigger (front) file
    by_day: dict[str, str] = {}
    for f in files:
        day_s = Path(f).stem.split("_")[1]
        if day_s not in by_day or os.path.getsize(f) > os.path.getsize(by_day[day_s]):
            by_day[day_s] = f
    files = [by_day[d] for d in sorted(by_day)]
    print(f"{len(files)} sessions")

    frames = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        for i, out in enumerate(ex.map(one_session, files, chunksize=4)):
            if out is not None:
                frames.append(out)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(files)}")

    allb = pd.concat(frames, ignore_index=True)
    allb.to_parquet(OUT, index=False)
    n_ev = int((allb["p_size"].fillna(0) >= 100).sum())
    n_sw = int((allb["s_size"].fillna(0) >= 100).sum())
    print(f"wrote {OUT}  ({len(allb):,} minutes, {allb['day'].nunique()} days, "
          f"minutes with >=100-lot print: {n_ev:,}, sweep: {n_sw:,})")


if __name__ == "__main__":
    main()
