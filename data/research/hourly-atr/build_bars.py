"""Time bars over the cached NQ tape — the substrate for "does ATR predict the
next bar's range?".

One row per ET clock slot of the Globex day (18:00 ET → 17:00 ET next day),
built from the on+rth+post tick segments, read-only: every load goes through
``tickmod.cached_*`` so a session that was never bought is skipped rather than
purchased. Sessions come from the tick cache itself, so the span is whatever
is on disk.

True range needs the prior bar's close, which is only meaningful when the
prior bar is the same instrument. On a roll the previous close belongs to a
different contract, so TR falls back to high-low there — otherwise every roll
injects a fake ~50-point spike into ATR.

``slot`` is the bar's index within the ET day (hour * bars_per_hour + ...), so
"the 09:30 bar" is one comparable population across sessions regardless of
interval. UTC and ET boundaries coincide at every interval that divides an
hour, so flooring in UTC and labelling in ET is exact.

    Usage: uv run python data/research/hourly-atr/build_bars.py [--minutes 60]

Writes bars_<interval>.parquet next to this file.
"""
import argparse
import sys
from datetime import date

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod

OUTDIR = "data/research/hourly-atr"
TICK = 0.25


def cached_sessions() -> list[date]:
    """Every session with tick data on disk, in either cache layout."""
    import glob
    import os

    days = set()
    for pat in ("*_day.parquet", "*_rth.parquet"):
        for p in glob.glob(str(tickmod.TICK_CACHE_DIR / pat)):
            base = os.path.basename(p)
            try:
                days.add(date.fromisoformat(base.split("_")[1]))
            except (IndexError, ValueError):
                continue
    return sorted(days)


def day_bars(sym: str, d: date, freq: str) -> pd.DataFrame | None:
    """OHLC at ``freq`` over one Globex day's on+rth+post ticks."""
    segs = [f(sym, d) for f in (tickmod.cached_overnight,
                                tickmod.cached_rth,
                                tickmod.cached_post)]
    segs = [s for s in segs if s is not None and not s.empty]
    if not segs:
        return None
    t = pd.concat(segs, ignore_index=True).sort_values("ts_utc")
    g = t.assign(_b=t["ts_utc"].dt.floor(freq)).groupby("_b", sort=True)
    b = g.agg(
        open=("price", "first"), high=("price", "max"),
        low=("price", "min"), close=("price", "last"),
        volume=("size", "sum"), ticks=("price", "size"),
    ).reset_index().rename(columns={"_b": "ts_utc"})
    b["session"] = d.isoformat()
    b["symbol"] = sym
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60,
                    help="bar interval in minutes; must divide 60")
    args = ap.parse_args()
    mins = args.minutes
    if 60 % mins:
        raise SystemExit("--minutes must divide 60 so ET and UTC slots align")
    freq = f"{mins}min"

    days = cached_sessions()
    print(f"{len(days)} cached sessions {days[0]} → {days[-1]}  @ {freq}")
    frames = []
    for i, d in enumerate(days):
        sym = tickmod.contract_for_cached("NQ", d)
        if sym is None:
            continue
        b = day_bars(sym, d, freq)
        if b is not None:
            frames.append(b)
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(days)}")

    df = pd.concat(frames, ignore_index=True).sort_values("ts_utc")
    # A Globex day's post hour and the next day's overnight both live in the
    # calendar day they belong to, so a slot can appear once only; drop any
    # overlap defensively and keep the first sighting.
    df = df.drop_duplicates(subset="ts_utc", keep="first").reset_index(drop=True)

    et = df["ts_utc"].dt.tz_convert("America/New_York")
    per_hour = 60 // mins
    df["slot"] = et.dt.hour * per_hour + et.dt.minute // mins
    df["slot_label"] = et.dt.strftime("%H:%M")
    df["dow"] = et.dt.dayofweek
    # RTH by bar *start*: exact at 30min and finer; at 60min the 09:00 bar is
    # half overnight and is excluded rather than silently half-counted.
    df["rth"] = (et.dt.time >= pd.Timestamp("09:30").time()) & \
                (et.dt.time < pd.Timestamp("16:00").time())

    prev_close = df["close"].shift(1)
    same_contract = df["symbol"].eq(df["symbol"].shift(1))
    hl = df["high"] - df["low"]
    tr = pd.concat([hl, (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["tr_pts"] = np.where(same_contract, tr, hl)
    df["range_pts"] = hl
    df["range_ticks"] = hl / TICK

    out = f"{OUTDIR}/bars_{mins}min.parquet"
    df.to_parquet(out, index=False)
    print(f"\n{len(df)} bars → {out}")
    print(f"RTH bars: {int(df['rth'].sum())} over {df['session'].nunique()} sessions")
    med = df.groupby("slot_label")["range_ticks"].agg(["size", "median"])
    print(med.to_string())


if __name__ == "__main__":
    main()
