"""Participation floor — does low volume predict anything the clock doesn't?

Creamer's one genuinely new knob: *below 20,000 contracts per 5-minute MNQ
candle, don't trade — participation is dying and moves won't carry.*

Two problems porting that here, both handled:

  1. **Wrong instrument.** The tick cache is NQ; his threshold is MNQ, which is
     1/10 the notional and a different (retail-heavy) participant mix. There is
     no fixed divisor that converts one into the other — the ratio drifts with
     time of day and regime. So the constant is discarded and the *concept* is
     ported: a percentile of the volume distribution.

  2. **Volume declines into lunch by construction**, so any raw volume gate is
     partly a clock in disguise — and the clock is already spoken for
     (vol-clock study: ATR sets the clock, confirmed on 5 baselines). A high
     correlation therefore does NOT kill the idea; it means the raw gate is
     untestable and the *residual* is what must be tested:

         resid = log( bar volume / median volume for that time-of-day )

     "Unusually quiet **for this time of day**" — not "quiet". If the residual
     still predicts, there is a volume effect independent of the clock. If it
     does not, the clock explains all of it and the knob is a re-description.

The outcome is his own claim, not a strategy P&L: does the move *carry*?

    eff  = |net move over next 30 min| / (sum of |5-min moves| over that span)

Directional efficiency — 1.0 is a straight run, ~0.2 is chop that ends where it
started. This is model-free, so it screens the premise before any strategy work
is spent on it.

Significance is computed **across sessions**, not across bars: the 30-minute
windows overlap and adjacent bars are heavily autocorrelated, so a pooled t-test
on bars would be badly overstated. Each session contributes one mean per
quintile and the test runs on those.

    .venv/bin/python data/research/participation-floor/screen.py
    .venv/bin/python data/research/participation-floor/screen.py --first90
"""

from __future__ import annotations

import glob
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TICKS = ROOT / "data" / "cache" / "ticks"
ET = "America/New_York"

BAR = "5min"
FWD_BARS = 6          # 30 minutes of follow-through
OPEN_MIN = 9 * 60 + 30
CLOSE_MIN = 16 * 60


def sessions() -> dict[str, str]:
    """date -> front-month file. On roll days the busier contract wins."""
    by: dict[str, list[str]] = defaultdict(list)
    for f in sorted(glob.glob(str(TICKS / "*_day.parquet"))):
        m = re.search(r"/([A-Z0-9]+)_(\d{4}-\d{2}-\d{2})_day", f)
        if m:
            by[m.group(2)].append(f)
    out = {}
    for d, fs in by.items():
        if len(fs) == 1:
            out[d] = fs[0]
        else:                                  # roll day — take the liquid leg
            out[d] = max(fs, key=lambda f: pd.read_parquet(f, columns=["size"])["size"].sum())
    return dict(sorted(out.items()))


def bars(path: str) -> pd.DataFrame | None:
    """RTH 5-minute bars: volume, close, minutes-from-open."""
    d = pd.read_parquet(path, columns=["ts_utc", "price", "size", "seg"])
    d = d[d["seg"] == "rth"]
    if d.empty:
        return None
    ts = pd.to_datetime(d["ts_utc"], utc=True).dt.tz_convert(ET)
    d = d.assign(ts=ts).set_index("ts")
    g = d.resample(BAR, label="left", closed="left")
    b = pd.DataFrame({"vol": g["size"].sum(), "close": g["price"].last()}).dropna()
    if b.empty:
        return None
    b["tod"] = b.index.hour * 60 + b.index.minute
    b = b[(b["tod"] >= OPEN_MIN) & (b["tod"] < CLOSE_MIN)]
    b = b[b["vol"] > 0]
    return b if len(b) > FWD_BARS + 4 else None


def forward(b: pd.DataFrame) -> pd.DataFrame:
    """Follow-through over the next FWD_BARS, scored from the NEXT bar on.

    The anchor bar is excluded from the outcome — the same discipline the
    weekly-VWAP and structure/orderflow studies had to learn the hard way.
    """
    c = b["close"].to_numpy()
    n = len(c)
    net = np.full(n, np.nan)
    path = np.full(n, np.nan)
    for i in range(n - FWD_BARS):
        seg = c[i:i + FWD_BARS + 1]
        net[i] = abs(seg[-1] - seg[0])
        path[i] = np.abs(np.diff(seg)).sum()
    out = b.copy()
    out["net"] = net
    out["path"] = path
    out["eff"] = np.where(path > 0, net / path, np.nan)
    return out.dropna(subset=["eff"])


def main() -> None:
    first90 = "--first90" in sys.argv
    files = sessions()
    print(f"{len(files)} sessions, {min(files)} -> {max(files)}")
    if first90:
        print("restricted to the first 90 minutes (Creamer's own window)")

    frames = []
    for i, (d, f) in enumerate(files.items(), 1):
        b = bars(f)
        if b is None:
            continue
        fw = forward(b)
        fw["date"] = d
        frames.append(fw)
        if i % 100 == 0:
            print(f"  ...{i}/{len(files)}")
    df = pd.concat(frames)
    if first90:
        df = df[df["tod"] < OPEN_MIN + 90]
    print(f"{len(df):,} bar-observations\n")

    df["lvol"] = np.log(df["vol"])
    # time-of-day baseline: median log-volume for each 5-minute slot
    tod_med = df.groupby("tod")["lvol"].transform("median")
    df["resid"] = df["lvol"] - tod_med

    # --- 1. the collinearity number ---------------------------------------
    r_clock = df["lvol"].corr(df["tod"] - OPEN_MIN, method="spearman")
    print("1. IS THE RAW GATE A CLOCK IN DISGUISE?")
    print(f"   rho(log volume, minutes-from-open) = {r_clock:+.3f}  (Spearman)")
    print(f"   -> raw volume is {'largely' if abs(r_clock) > .35 else 'only weakly'} "
          f"explained by time of day; test the residual, not the raw gate.\n")

    # --- 2. raw vs residualised against follow-through ---------------------
    r_raw = df["lvol"].corr(df["eff"], method="spearman")
    r_res = df["resid"].corr(df["eff"], method="spearman")
    print("2. DOES VOLUME PREDICT FOLLOW-THROUGH?")
    print(f"   rho(log volume, efficiency) = {r_raw:+.3f}   <- raw, confounded by the clock")
    print(f"   rho(residual,   efficiency) = {r_res:+.3f}   <- clock removed\n")

    # --- 3. quintiles of the residual -------------------------------------
    df["q"] = pd.qcut(df["resid"], 5, labels=[1, 2, 3, 4, 5])
    print("3. QUINTILES OF THE RESIDUAL  (1 = unusually quiet for the time of day)")
    print(f"   {'q':>2}{'n':>9}{'eff':>9}{'net pts':>10}{'path pts':>10}{'vol':>10}")
    for q, g in df.groupby("q", observed=True):
        print(f"   {q:>2}{len(g):>9,}{g['eff'].mean():>9.3f}{g['net'].mean():>10.2f}"
              f"{g['path'].mean():>10.2f}{g['vol'].mean():>10,.0f}")

    # --- 4. session-clustered test, Q1 vs Q5 ------------------------------
    per = df.groupby(["date", "q"], observed=True)["eff"].mean().unstack()
    pair = per[[1, 5]].dropna()
    diff = pair[1] - pair[5]
    t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))
    print(f"\n4. Q1 vs Q5, CLUSTERED BY SESSION  (n = {len(diff)} sessions)")
    print(f"   mean efficiency  Q1 {pair[1].mean():.4f}   Q5 {pair[5].mean():.4f}"
          f"   diff {diff.mean():+.4f}")
    print(f"   paired t = {t:+.2f}   ({'sessions favour Q5 (busy)' if t < 0 else 'sessions favour Q1 (quiet)'})")

    # --- 5. the gate as Creamer would use it ------------------------------
    print("\n5. AS A GATE  (skip the quietest X% for the time of day)")
    print(f"   {'cut':>6}{'skipped':>10}{'eff kept':>10}{'eff skipped':>13}{'net kept':>10}")
    for pct in (10, 20, 30, 40):
        thr = df["resid"].quantile(pct / 100)
        kept, skip = df[df["resid"] >= thr], df[df["resid"] < thr]
        print(f"   {pct:>5}%{len(skip):>10,}{kept['eff'].mean():>10.3f}"
              f"{skip['eff'].mean():>13.3f}{kept['net'].mean():>10.2f}")


if __name__ == "__main__":
    main()
