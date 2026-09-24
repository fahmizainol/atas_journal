"""Overnight twins, stage 1: one row per minute per session, 18:00 -> 16:00 ET.

Price (1-min close/high/low) and the Globex-anchored VWAP with its +/-1 sigma
band, read at each minute's close through ``level_tag.DayLevels.at`` (the last
bar that closed strictly before). `render_days.py` compares the overnight
CURVES — not a snapshot — and shows what the look-alike days did in RTH.

    .venv/bin/python data/research/forward-fan/build_days.py [--workers 8]

Writes ``days.parquet`` beside this file. Cache-only: never fetches.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_states import all_days  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "days.parquet"
ET = ZoneInfo("America/New_York")

GLOBEX_OPEN = (18, 0)   # ET, the evening before
CLOSE = (16, 0)         # ET
MINUTES = 22 * 60       # 18:00 -> 16:00


def grid_for(day: date) -> pd.DatetimeIndex:
    start = pd.Timestamp(datetime.combine(day - timedelta(days=1), datetime.min.time()), tz=ET)
    start = start.replace(hour=GLOBEX_OPEN[0], minute=GLOBEX_OPEN[1])
    return pd.date_range(start, periods=MINUTES, freq="1min")


def one_day(day: date, src: str = "databento") -> pd.DataFrame | None:
    from api.session_chart import session_frame
    from journal.level_tag import DayLevels

    try:
        f = session_frame(contract="NQ", day=day, tz=ET, allow_fetch=False, overnight=True)
    except Exception as e:  # noqa: BLE001
        print(f"  {day}: frame failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if f is None or f.ticks is None or f.ticks.empty:
        return None
    grid = grid_for(day)
    t = f.ticks
    px = pd.Series(t["price"].to_numpy(dtype=float), index=pd.DatetimeIndex(t["ts_utc"]))
    if px.index[0] > grid[60]:      # no overnight on disk: not a twin candidate
        return None
    m1 = px.resample("1min", label="left", closed="left").ohlc()
    m1 = m1.reindex(grid.tz_convert("UTC"))
    closes = m1["close"].ffill().to_numpy()
    # Traded size per minute. `size` is uint32 on disk — cast to float BEFORE any
    # arithmetic or the usual wrap-around bites (see api/session_chart.py:139).
    vol = (pd.Series(t["size"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(t["ts_utc"]))
           .resample("1min", label="left", closed="left").sum()
           .reindex(grid.tz_convert("UTC")).fillna(0.0))
    lv = DayLevels(f)
    # Globex-anchored and NY-anchored VWAPs with their +/-1 sigma bands. The NY
    # one is NaN before the bell — it does not exist yet.
    cols = {"gxv": "gxvwap", "gxu": "gxvwap_u1", "gxl": "gxvwap_l1",
            "nyv": "vwap", "nyu": "vwap_u1", "nyl": "vwap_l1",
            # Developing volume profiles — the levels in force at each minute.
            "gxpoc": "gxVP_poc", "gxvah": "gxVP_vah", "gxval": "gxVP_val",
            "nypoc": "devVP_poc", "nyvah": "devVP_vah", "nyval": "devVP_val"}
    series = {k: np.full(MINUTES, np.nan) for k in cols}
    for i, t0 in enumerate(grid):
        d = lv.at(t0 + pd.Timedelta(minutes=1))
        for k, level in cols.items():      # NOT `src` — that is the store tag
            series[k][i] = d.get(level, np.nan)
    return pd.DataFrame({
        "day": day.isoformat(), "symbol": f.symbol, "src": src,
        "i": np.arange(MINUTES, dtype=np.int16),
        # Full OHLC + volume: `open` and `volume` are unused by the twins pages but
        # are what an OHLCV model (Kronos) requires as input — the resample already
        # computed them, there is no reason to throw them away.
        "open": m1["open"].to_numpy(), "high": m1["high"].to_numpy(),
        "low": m1["low"].to_numpy(), "close": closes, "volume": vol.to_numpy(),
        **series,
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rebuild", action="store_true",
                    help="recompute every session instead of only the missing ones")
    args = ap.parse_args()

    want = all_days()
    if args.limit:
        want = want[-args.limit:]
    # Incremental by default: a session already described is not recomputed, so
    # adding recorded days costs only the recorded days.
    have = pd.DataFrame()
    if OUT.exists() and not args.rebuild:
        have = pd.read_parquet(OUT)
        if "src" not in have.columns:        # written before the stores were tagged
            have["src"] = "databento"
        done = set(have["day"].astype(str))
        want = [(d, s) for d, s in want if d.isoformat() not in done]
    kept = have["day"].nunique() if len(have) else 0
    print(f"{len(want)} sessions to build ({kept} already described)")
    if not want and kept:
        out = have
    else:
        t0 = time.time()
        frames, skipped = [], 0
        with ProcessPoolExecutor(args.workers) as ex:
            futs = {ex.submit(one_day, d, s): (d, s) for d, s in want}
            for k, fut in enumerate(as_completed(futs), 1):
                df = fut.result()
                if df is None:
                    skipped += 1
                else:
                    frames.append(df)
                if k % 100 == 0:
                    print(f"  {k}/{len(want)}  {time.time() - t0:.0f}s")
        if len(have):
            frames.append(have)
        out = pd.concat(frames, ignore_index=True).sort_values(["day", "i"])
        print(f"  built {len(want) - skipped}, skipped {skipped}, in {time.time() - t0:.0f}s")
    out.to_parquet(OUT, index=False)
    by_src = out.groupby("src")["day"].nunique().to_dict()
    print(f"wrote {OUT.name}: {out['day'].nunique()} sessions x {MINUTES} min  {by_src}")


if __name__ == "__main__":
    main()
