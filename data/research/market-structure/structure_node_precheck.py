"""Pre-check: does the VOLUME NODE a structure break passes through condition
its follow-through?  (market structure × HVN/LVN combo — scan, not a build.)

Hypothesis cell (the one untested combo): a BOS/CHoCH whose break LEVEL sits in
a thin (LVN) region of the developing intraday profile should travel farther
before pulling back ("no resistance") than one breaking through a heavy (HVN)
shelf.  Both parents are individually null (market-structure-events.md,
lvn-retrace-continuation.md, stable-level-sr.md) — this races the interaction.

Causality: for a break confirmed at the close of 1-min bar t, the profile is the
cumulative tick histogram up to the END OF BAR t-1 (strictly prior — the break
bar's own volume never colours its node).  Everything is RTH-only, same tick
cache + minute_bars as structure_events.py, whose event machine is imported and
re-run per swing threshold so bars and events agree bit-for-bit.

Per break row: node percentile of the break level inside the visited range
(two smoothings), ratio to median shelf volume, prior touch count (retest
covariate), elapsed bars, and the parquet's forward MFE/MAE/net convention
(scored over bars t+1..t+20 — the break bar is already excluded).

Usage: .venv/bin/python data/research/market-structure/structure_node_precheck.py
Output: data/research/market-structure/structure_node_events.parquet
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "data/research/market-structure")
from journal.sim.ticks import cached_rth  # noqa: E402
from journal.sim.regime import minute_bars  # noqa: E402
from structure_events import structure_events, sessions  # noqa: E402

TICK = 0.25
SWEEP_PTS = [5.0, 10.0, 20.0]
SMOOTHS = [2, 6]  # half-widths in ticks for the node reading (1pt / 3pt windows)
OUT = Path("data/research/market-structure/structure_node_events.parquet")


def node_stats(hist: np.ndarray, base: int, level: float):
    """Percentile of the level's (smoothed) volume among all levels in the
    visited span, plus its ratio to the median shelf.  hist is the cumulative
    tick histogram (index = price/TICK - base) as of the end of bar t-1."""
    nz = np.flatnonzero(hist)
    if nz.size < 20:  # profile too immature to call anything a node
        return None
    lo, hi = int(nz[0]), int(nz[-1])
    span = hist[lo:hi + 1]
    idx = int(round(level / TICK)) - base - lo
    if idx < 0 or idx >= len(span):
        return None  # level outside the visited range (shouldn't happen: it's a pivot)
    out = {}
    for s in SMOOTHS:
        k = 2 * s + 1
        sm = np.convolve(span, np.ones(k) / k, mode="same")
        v = sm[idx]
        out[f"node_pctl_s{s}"] = float((sm < v).mean() * 100.0)
        med = np.median(sm)
        out[f"rel_med_s{s}"] = float(v / med) if med > 0 else np.nan
    out["span_levels"] = hi - lo + 1
    return out


def main():
    rows = []
    t0 = time.time()
    sess_list = sessions()
    done = 0
    for sym, d in sess_list:
        t = cached_rth(sym, d)
        if t is None or len(t) < 2000:
            continue
        bars = minute_bars(t, "1min")
        if len(bars) < 30:
            continue
        lv = np.rint(t["price"].to_numpy(dtype="float64") / TICK).astype("int64")
        sz = t["size"].to_numpy(dtype="float64")
        base = int(lv.min())
        hist = np.zeros(int(lv.max()) - base + 1)
        end_idx = bars["end_idx"].to_numpy()
        bhi = bars["high"].to_numpy()
        blo = bars["low"].to_numpy()

        # break events at every swing threshold, keyed by bar
        by_bar: dict[int, list] = {}
        for thr in SWEEP_PTS:
            _, ev = structure_events(bars, thr_pts=thr)
            br = ev[ev["type"].str.contains("BOS|CHoCH")]
            for _, r in br.iterrows():
                by_bar.setdefault(int(r["bar"]), []).append((thr, r))

        start = 0
        for bi in range(len(bars)):
            for thr, r in by_bar.get(bi, []):
                if bi == 0:
                    continue  # no prior profile to read
                ns = node_stats(hist, base, r["level"])
                if ns is None:
                    continue
                touches = int(((blo[:bi] <= r["level"]) & (bhi[:bi] >= r["level"])).sum())
                rows.append({
                    "session": d.isoformat(), "sym": sym, "thr": thr,
                    "type": r["type"], "bar": bi, "level": r["level"],
                    "touches": touches,
                    "fwd_net": r["fwd_net"], "fwd_mfe": r["fwd_mfe"],
                    "fwd_mae": r["fwd_mae"], **ns,
                })
            # only NOW does bar bi's volume enter the profile (causal)
            e = int(end_idx[bi])
            np.add.at(hist, lv[start:e + 1] - base, sz[start:e + 1])
            start = e + 1
        done += 1
        if done % 50 == 0:
            print(f"{done} sessions, {len(rows)} break rows, "
                  f"{time.time() - t0:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(OUT)
    print(f"\nDONE {done} sessions -> {len(df)} rows  ({time.time() - t0:.0f}s)")
    print(df.groupby("thr")["fwd_net"].agg(["size", "mean"]).round(2).to_string())


if __name__ == "__main__":
    main()
