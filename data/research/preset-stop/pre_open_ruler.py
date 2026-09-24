"""What should the bracket presets read before the settled window opens?

THE QUESTION. `lib/orderPresets` sets every preset's stop from the 500-print
median bar range inside 10:00-16:00 ET, and falls back to *yesterday's* settled
median when today has not printed three of those bars yet — which is the whole
09:30-10:00 stretch, i.e. exactly when the bracket gets set. Yesterday is a day
old and the night in between is where the regime changes. So:

  1. Is the fallback needed at all? The 10:00 window is a **time-bar** rule (the
     first half hour runs 2-4x the rest of the day, and would drag a median of
     clock bars). A 500-print bar is volume-clocked — a fast tape makes *more*
     bars, not bigger ones — so the open may not be wider at this bucketing at
     all. If it isn't, the window can open at the bell and the fallback covers
     ~75 seconds instead of thirty minutes.

  2. If it is needed, what beats yesterday? The obvious candidate is on the same
     tape and hours old rather than a day: **tonight's own median**, measured the
     same way over the prints before the bell. Also tested: the last 2.5h and the
     last hour of it (more current, fewer bars), and yesterday scaled by how much
     the night moved relative to yesterday's night (a regime-adjusted carry).

WHAT IS MEASURED. For every cached session: the 500-print blocks of the whole
tape, anchored at the first print exactly as `FixedTickRuler` anchors them,
bucketed by the ET wall clock of each block's first print. The *truth* per day is
that day's settled 10:00-16:00 median — the number the developing median walks
toward. Every predictor is scored against it, and every predictor is causal at
the bell (except `open30`, which is the answer to question 1).

Reads the tick cache only; `cached_*` never fetches, so this cannot spend.

Usage:
    .venv/bin/python data/research/preset-stop/pre_open_ruler.py            # all
    .venv/bin/python data/research/preset-stop/pre_open_ruler.py --limit 40
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import date as date_cls
from multiprocessing import Pool

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal.replay_whatif import load_tape  # noqa: E402

TICK = 0.25
PRINTS = 500  # lib/volRuler PRESET_BAR_PRINTS
MIN_BARS = 3  # lib/volRuler MIN_BARS

CACHE = ROOT / "data/cache/ticks"
OUT = pathlib.Path(__file__).parent

#: ET seconds-of-day. The bell, the ruler's settled window, and the close.
BELL = 9 * 3600 + 1800
WIN_START = 10 * 3600
WIN_END = 16 * 3600

#: The windows each predictor is measured over, as ET seconds-of-day. All but
#: `open30` are complete before the bell, which is what makes them usable when
#: the ticket is being set.
WINDOWS = {
    "rth": (WIN_START, WIN_END),          # the truth
    "open30": (BELL, WIN_START),          # is the open wider at this bucketing?
    "pre_all": (0, BELL),                 # the night, as far back as the tape goes
    "pre_2h": (7 * 3600, BELL),
    "pre_1h": (8 * 3600 + 1800, BELL),
}


def blocks(t: np.ndarray, px: np.ndarray, n: int):
    """500-print bars, anchored at print zero — `FixedTickRuler`'s own rule.
    Returns (first-print ET seconds-of-day, range in ticks) for complete blocks
    only, since a partial block is not a bar the ruler would have measured."""
    starts = np.arange(0, len(t) - n + 1, n)
    ends = starts + n
    hi = np.array([px[a:b].max() for a, b in zip(starts, ends)])
    lo = np.array([px[a:b].min() for a, b in zip(starts, ends)])
    tod = (t[starts] / 1000) % 86400
    return tod, (hi - lo) / TICK


def med(tod: np.ndarray, rng: np.ndarray, lo: float, hi: float):
    """The median block range inside a window, or None where too few bars
    landed in it to be a reading — the same floor the app applies."""
    m = (tod >= lo) & (tod < hi)
    return (float(np.median(rng[m])), int(m.sum())) if m.sum() >= MIN_BARS else (None, int(m.sum()))


def run_one(job):
    symbol, day = job
    try:
        t, px = load_tape(symbol, day, None)
        if len(t) < PRINTS * 4:
            return dict(symbol=symbol, day=day, ok=False, why=f"{len(t)} prints")
        tod, rng = blocks(t, px, PRINTS)
        out = dict(symbol=symbol, day=day, ok=True, prints=int(len(t)))
        for name, (lo, hi) in WINDOWS.items():
            v, n = med(tod, rng, lo, hi)
            out[name] = v
            out[f"n_{name}"] = n
        return out
    except Exception as e:  # a half-written cache entry is not a reason to stop
        return dict(symbol=symbol, day=day, ok=False, why=repr(e))


def sessions():
    """Every cached day, as (symbol, date). Grouped by symbol on purpose: the
    'yesterday' of a session has to be the previous session *of the same
    contract*, or the roll silently pairs two different instruments."""
    seen = set()
    for f in sorted(CACHE.glob("*_*_*.json")) + sorted(CACHE.glob("*_*_day.parquet")):
        m = re.match(r"([A-Z0-9]+)_(\d{4}-\d{2}-\d{2})_", f.name)
        if m:
            seen.add((m.group(1), m.group(2)))
    return sorted(seen)


def ratio_stats(pred: list[float], truth: list[float]) -> dict:
    """How wrong a predictor is, in the units the decision is made in.

    Scored as a *ratio* rather than a difference: a 10-tick miss on a 30-tick day
    is a different mistake from the same miss on a 90-tick one, and the stop is
    set proportionally. `p50_abs_pct` is the typical miss; `within_20` is how
    often it would have put the stop within a fifth of where the day's own median
    ended up; `bias` says whether it leans wide or tight.
    """
    p = np.asarray(pred, dtype=float)
    q = np.asarray(truth, dtype=float)
    r = p / q
    return dict(
        n=int(len(p)),
        bias=round(float(np.median(r)), 3),
        p50_abs_pct=round(float(np.median(np.abs(r - 1))) * 100, 1),
        p90_abs_pct=round(float(np.percentile(np.abs(r - 1), 90)) * 100, 1),
        within_20=round(float(np.mean(np.abs(r - 1) <= 0.20)) * 100, 1),
        within_35=round(float(np.mean(np.abs(r - 1) <= 0.35)) * 100, 1),
        spearman=round(float(spearman(p, q)), 3),
    )


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = rank(a), rank(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    d = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / d) if d else float("nan")


def rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x), dtype=float)
    r[order] = np.arange(len(x), dtype=float)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="most recent N sessions only")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    jobs = sessions()
    if args.limit:
        jobs = jobs[-args.limit :]
    print(f"{len(jobs)} cached sessions")

    with Pool(args.workers) as pool:
        rows = list(pool.imap_unordered(run_one, jobs, chunksize=4))
    ok = sorted([r for r in rows if r["ok"]], key=lambda r: (r["symbol"], r["day"]))
    bad = [r for r in rows if not r["ok"]]
    print(f"{len(ok)} read, {len(bad)} skipped")
    for r in bad[:6]:
        print(f"  !! {r['symbol']} {r['day']}: {r['why']}")

    # Yesterday: the previous cached session of the same contract, and only when
    # it is genuinely the session before (a gap means a day nobody cached, and
    # 'yesterday' would be a week ago).
    by_sym: dict[str, list[dict]] = {}
    for r in ok:
        by_sym.setdefault(r["symbol"], []).append(r)
    for rows_ in by_sym.values():
        for prev, cur in zip(rows_, rows_[1:]):
            gap = (date_cls.fromisoformat(cur["day"]) - date_cls.fromisoformat(prev["day"])).days
            if gap <= 4:
                cur["yday_rth"] = prev["rth"]
                cur["yday_pre_1h"] = prev["pre_1h"]

    # --- question 1: is the open wider at 500 prints a bar? -------------------
    pair = [(r["open30"], r["rth"]) for r in ok if r.get("open30") and r.get("rth")]
    print(f"\n--- the open, at {PRINTS} prints a bar ({len(pair)} sessions) ---")
    if pair:
        r = np.array([a / b for a, b in pair])
        print(f"  09:30-10:00 median / settled median: p10 {np.percentile(r, 10):.2f} "
              f"p50 {np.median(r):.2f} p90 {np.percentile(r, 90):.2f}")
        print(f"  the open is wider on {100 * np.mean(r > 1):.0f}% of sessions")
        print("  (a p50 near 1.0 means the volume clock has already absorbed the open,")
        print("   and the 10:00 window is a clock-bar rule this bucketing does not need)")

    # --- question 2: what predicts today's settled median at the bell? -------
    preds = {
        "yday_rth": lambda r: r.get("yday_rth"),
        "pre_all": lambda r: r.get("pre_all"),
        "pre_2h": lambda r: r.get("pre_2h"),
        "pre_1h": lambda r: r.get("pre_1h"),
        # Yesterday, scaled by how much tonight differs from last night: the
        # carry with a regime term, and the only two-input candidate here.
        "yday_x_night": lambda r: (
            r["yday_rth"] * r["pre_1h"] / r["yday_pre_1h"]
            if r.get("yday_rth") and r.get("pre_1h") and r.get("yday_pre_1h")
            else None
        ),
        # And the one that is not causal at the bell, for scale: the first half
        # hour of the session itself, which is what an earlier window would use.
        "open30 (10:00 only)": lambda r: r.get("open30"),
    }
    # One shared sample so the columns are comparable: every day where the truth
    # and *every* predictor exists. Scoring each on its own sample would let the
    # weakest one look good by quietly dropping the days it had nothing to say
    # about.
    sample = [
        r for r in ok
        if r.get("rth") and all(f(r) for f in preds.values())
    ]
    print(f"\n--- predicting today's settled median, at the bell ({len(sample)} sessions) ---")
    print(f"  {'predictor':<22}{'bias':>7}{'p50 err':>9}{'p90 err':>9}{'±20%':>7}{'±35%':>7}{'rho':>7}")
    table = {}
    truth = [r["rth"] for r in sample]
    for name, f in preds.items():
        s = ratio_stats([f(r) for r in sample], truth)
        table[name] = s
        print(f"  {name:<22}{s['bias']:>7.2f}{s['p50_abs_pct']:>8.1f}%{s['p90_abs_pct']:>8.1f}%"
              f"{s['within_20']:>6.0f}%{s['within_35']:>6.0f}%{s['spearman']:>7.2f}")

    art = OUT / "summary.json"
    art.write_text(json.dumps(
        dict(prints=PRINTS, sessions=len(ok), sample=len(sample), table=table,
             open_ratio=dict(
                 n=len(pair),
                 p50=round(float(np.median([a / b for a, b in pair])), 3) if pair else None,
             ),
             rows=ok), indent=2))
    print(f"\nwrote {art.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
