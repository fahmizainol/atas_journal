"""Forward-fan demo, stage 1: the state pool.

For every cached session and every RTH minute close, record WHERE price sat
relative to the three VWAPs and the three developing profiles, and WHAT the
next 15 minutes of closes did. `render.py` then finds, for a walked session,
the historical minutes that looked like each one and draws their futures.

Every level is read through ``level_tag.DayLevels.at`` — the last bar that
closed STRICTLY BEFORE the instant — so the state never contains the minute
it is describing (the lookahead that made `fade-entry-geometry` a mirage).

Distances are in RULER units (ticks / median closed 1-min bar range over the
trailing 30 min, same window as ``trade_context.vol_med_ticks_1m``), because
20 ticks above VWAP on a hot day and a quiet day are not the same place.

    .venv/bin/python data/research/forward-fan/build_states.py [--workers 8]

Writes ``states.parquet`` beside this file. Cache-only: never fetches.
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
OUT = HERE / "states.parquet"
TICKS = ROOT / "data" / "cache" / "ticks"

ET = ZoneInfo("America/New_York")
TICK = 0.25
FWD_MIN = 15            # forward closes recorded per minute
RULER_WIN_MIN = 30      # trade_context.VOL_WIN_MIN
RULER_MIN_BARS = 3      # trade_context.MIN_BARS

#: The twelve lines the state is measured against, and the label the viewer
#: draws them under. Order matters: it is the feature order in the parquet.
LEVELS = [
    ("vwap",      "NY VWAP"),
    ("gxvwap",    "Globex VWAP"),
    ("wkvwap",    "Weekly VWAP"),
    ("devVP_poc", "NY POC"),
    ("devVP_vah", "NY VAH"),
    ("devVP_val", "NY VAL"),
    ("gxVP_poc",  "Globex POC"),
    ("gxVP_vah",  "Globex VAH"),
    ("gxVP_val",  "Globex VAL"),
    ("wkVP_poc",  "Weekly POC"),
    ("wkVP_vah",  "Weekly VAH"),
    ("wkVP_val",  "Weekly VAL"),
]
#: Band half-widths, in ruler units — how stretched each VWAP's distribution is.
SIGMAS = [("vwap", "vwap_u1"), ("gxvwap", "gxvwap_u1"), ("wkvwap", "wkvwap_u1")]
#: How price ARRIVED, not only where it is: net run over the last 5 and 15 min
#: (ruler units, signed) and where the close sits in the last 15 min's range
#: (0 = its low, 1 = its high). Same vocabulary as ``trade_context.pre_*``.
#: Kept to three numbers on purpose — every feature loosens the match.
ARRIVAL = ["run5", "run15", "loc15"]


def cached_days() -> list[date]:
    """Sessions in the Databento corpus. This is the BACKTEST REFERENCE, and it
    deliberately does not see the live store — see :func:`live_days`."""
    days = set()
    for f in glob.glob(str(TICKS / "*_day.parquet")):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})_day\.parquet$", f)
        if m:
            days.add(date.fromisoformat(m.group(1)))
    return sorted(days)


def live_days() -> list[date]:
    """Sessions in the RECORDED store, ``data/live/ticks/<SYMBOL>/<DATE>/``.

    Kept apart from :func:`cached_days` on purpose. ``live-shadow-plan``
    decision 3 is permanent — nothing recorded live may grow the backtest
    corpus — so a recorded day is never quietly folded into the Databento pool.
    It is tagged instead and the consumer decides: in `render_days.py` a live
    day may be the TARGET a page describes, but never a twin the target is
    matched against.
    """
    from journal.sim import ticks as tickmod
    days = set()
    root = tickmod.LIVE_TICK_DIR
    if not root.is_dir():
        return []
    for sym in (p for p in root.iterdir() if p.is_dir()):
        for d in sym.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"):
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if tickmod.live_chunks(sym.name, day):   # the directory is the truth
                days.add(day)
    return sorted(days)


def all_days() -> list[tuple[date, str]]:
    """Every describable session as ``(day, src)``, src in {databento, live}.

    A day held by both stores is reported once as ``databento``: the corpus copy
    is the one a reference pool is allowed to quote.
    """
    cached = set(cached_days())
    return sorted([(d, "databento") for d in cached]
                  + [(d, "live") for d in live_days() if d not in cached])


def one_day(day: date) -> pd.DataFrame | None:
    from api.session_chart import session_frame
    from journal.level_tag import DayLevels

    try:
        f = session_frame(contract="NQ", day=day, tz=ET, allow_fetch=False, overnight=True)
    except Exception as e:  # noqa: BLE001 — a bad day is skipped, not fatal
        print(f"  {day}: frame failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if f is None or f.ticks is None or f.ticks.empty:
        return None

    t = f.ticks
    px = pd.Series(t["price"].to_numpy(dtype=float), index=pd.DatetimeIndex(t["ts_utc"]))
    # A Globex-anchored VWAP that starts at the bell is a different line. Days
    # without the overnight on disk are kept out of the pool rather than
    # described by the wrong geometry.
    bell = pd.Timestamp(datetime.combine(day, datetime.min.time()), tz=ET).replace(hour=9, minute=30)
    # The overnight begins at 18:00 ET the evening before, so "starts before the
    # bell" is the test — not the hour of the first print.
    has_on = px.index[0] < bell - pd.Timedelta(hours=1)

    m1 = px.resample("1min", label="left", closed="left").ohlc().dropna()
    if m1.empty:
        return None
    et_idx = m1.index.tz_convert(ET)
    close_t = bell.replace(hour=16, minute=0)
    rth = (et_idx >= bell) & (et_idx < close_t)
    if rth.sum() < 300:
        return None

    rng_ticks = ((m1["high"] - m1["low"]) / TICK).to_numpy()
    closes = m1["close"].to_numpy()
    highs = m1["high"].to_numpy()
    lows = m1["low"].to_numpy()
    starts = m1.index  # bar open instants (UTC)
    n = len(m1)
    levels = DayLevels(f)

    rows = []
    rth_pos = np.flatnonzero(rth)
    for i in rth_pos:
        bar_close = starts[i] + pd.Timedelta(minutes=1)
        # Ruler: closed bars only, strictly before this bar's close, last 30 min.
        lo = np.searchsorted(starts, bar_close - pd.Timedelta(minutes=RULER_WIN_MIN), "left")
        win = rng_ticks[lo:i + 1]  # bar i has closed at bar_close
        if win.size < RULER_MIN_BARS:
            continue
        ruler = float(np.median(win))
        if ruler <= 0:
            continue
        lv = levels.at(bar_close)
        c = float(closes[i])
        feat = [(c - lv.get(k, np.nan)) / TICK / ruler for k, _ in LEVELS]
        sig = [(lv.get(u, np.nan) - lv.get(mid, np.nan)) / TICK / ruler for mid, u in SIGMAS]
        raw = [lv.get(k, np.nan) for k, _ in LEVELS]
        # Arrival, off closed bars only (bar i has closed at this instant).
        run5 = (c - closes[i - 5]) / TICK / ruler if i >= 5 else np.nan
        run15 = (c - closes[i - 15]) / TICK / ruler if i >= 15 else np.nan
        if i >= 14:
            hi15, lo15 = float(highs[i - 14:i + 1].max()), float(lows[i - 14:i + 1].min())
            loc15 = (c - lo15) / (hi15 - lo15) if hi15 > lo15 else np.nan
        else:
            loc15 = np.nan
        fwd = [(closes[i + h] - c) / TICK if i + h < n else np.nan for h in range(1, FWD_MIN + 1)]
        tod = int((et_idx[i] - bell).total_seconds() // 60)
        rows.append([day.isoformat(), f.symbol, tod, c, ruler, has_on,
                     float(m1["open"].iat[i]), float(highs[i]), float(lows[i]),
                     *feat, *sig, run5, run15, loc15, *raw, *fwd])

    cols = (["day", "symbol", "tod", "close", "ruler", "has_on", "open", "high", "low"]
            + [f"f_{k}" for k, _ in LEVELS] + [f"s_{m}" for m, _ in SIGMAS]
            + [f"a_{a}" for a in ARRIVAL]
            + [f"lvl_{k}" for k, _ in LEVELS] + [f"fwd_{h}" for h in range(1, FWD_MIN + 1)])
    return pd.DataFrame(rows, columns=cols)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="first N days only (smoke)")
    args = ap.parse_args()

    days = cached_days()
    if args.limit:
        days = days[-args.limit:]
    print(f"{len(days)} cached sessions {days[0]} .. {days[-1]}")
    t0 = time.time()
    frames, skipped = [], 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = {ex.submit(one_day, d): d for d in days}
        for k, fut in enumerate(as_completed(futs), 1):
            df = fut.result()
            if df is None:
                skipped += 1
            else:
                frames.append(df)
            if k % 50 == 0:
                print(f"  {k}/{len(days)}  {time.time() - t0:.0f}s")
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(OUT, index=False)
    no_on = out.loc[~out["has_on"], "day"].nunique()
    print(f"wrote {OUT.name}: {len(out):,} minute-states over {out['day'].nunique()} sessions "
          f"({skipped} skipped, {no_on} without overnight) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
