"""Pre-check: do PRIOR-day initial-balance extensions act as levels?

The one level type in the 2026-06-05 / 06-11 streams that the repo does not
already compute. On 06-05 the presenter called "yesterday's IB ... down here at
25" and then "the IB extension was hit to the tick" — and the -200% extension of
06-04's IB (30151.00 - 2 x 213.50 = 29724.00) was the exact printed low at both
11:15 and 11:20. One tick-perfect hit is a coincidence generator, so this asks
the only question that matters: across every cached session, does price DO
anything at these levels that it does not do at an arbitrary horizontal?

Design follows the repo's standing artifact screen. The level is fixed before the
session opens (prior RTH IB), so there is no lookahead. For each first touch we
measure, over the next 30 minutes:

  * penetration  — how far price carried BEYOND the level (a level that holds
    should be penetrated less than a placebo);
  * reversal     — how far price came back the other way.

THE CONTROL IS THE WHOLE EXPERIMENT. The obvious placebo -- the same level shifted
+-25/50 points -- is broken, and broken in the direction that manufactures an
edge: a placebo 50 points BEYOND a down-extension can only be touched on sessions
where the real level already failed, so its penetration is larger by construction.
That artifact alone shows the real levels "holding" by ~10 points at every
multiple and both sides.

The honest control is a SESSION-DRIFT null: express each real level as a distance
from that day's open, then score the same day at `open + delta` using a delta
borrowed from a DIFFERENT session. The placebo is then a horizontal sitting a
realistic distance away on the same day's price action, with no dependence on
where this day actually turned. If the extensions are real, penetration is
smaller and reversal larger than that. If the two match, this is another
arbitrary-horizontal null and there is nothing to build.

Usage:
    .venv/bin/python data/research/vwap-wave-livestreams/ib_extension_check.py
"""
import sys
from collections import defaultdict
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, "src")
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim.bars import time_bars

sys.path.insert(0, "data/research/vwap-wave-livestreams")
from session_levels import ET, load_day

CACHE = Path("data/cache/ticks")
HORIZON = 30          # minutes scored after first touch


def sessions() -> list[tuple[str, date]]:
    out = []
    for p in sorted(CACHE.glob("*_day.parquet")):
        sym, day, _ = p.name.split("_")
        out.append((sym, date.fromisoformat(day)))
    return out


def rth_bars(sym: str, day: date) -> pd.DataFrame | None:
    try:
        d = load_day(day, sym)
    except Exception:
        return None
    op, cl = tickmod.session_bounds_utc(day)
    rth = d[(d.ts_utc >= op) & (d.ts_utc < cl)].reset_index(drop=True)
    if len(rth) < 500:
        return None
    b = time_bars(rth, "1min")
    b["et"] = b["ts_utc"].dt.tz_convert(ET)
    return b


def ib_of(b: pd.DataFrame) -> tuple[float, float] | None:
    ib = b[b.et.dt.time < time(10, 30)]
    if ib.empty:
        return None
    return float(ib.low.min()), float(ib.high.max())


def score(b: pd.DataFrame, level: float, side: str) -> tuple[float, float] | None:
    """First touch of *level* after 10:30, then penetration/reversal over HORIZON.

    side 'down' = an extension below the prior IB, approached from above: price
    holding means it does NOT carry much further down, and bounces back up.
    """
    w = b[b.et.dt.time >= time(10, 30)].reset_index(drop=True)
    if w.empty:
        return None
    hit = np.where((w.low.to_numpy() <= level) & (w.high.to_numpy() >= level))[0]
    if not len(hit):
        return None
    i = int(hit[0])
    fwd = w.iloc[i: i + HORIZON + 1]
    if len(fwd) < HORIZON // 2:
        return None
    if side == "down":
        return level - float(fwd.low.min()), float(fwd.high.max()) - level
    return float(fwd.high.max()) - level, level - float(fwd.low.min())


def main() -> None:
    by_sym: dict[str, list[date]] = defaultdict(list)
    for sym, day in sessions():
        by_sym[sym].append(day)

    # Pass 1: collect each session's real levels, as a distance from that open.
    found = []
    for sym, days in by_sym.items():
        days.sort()
        prev_b = None
        for k, day in enumerate(days):
            b = rth_bars(sym, day)
            if b is None:
                prev_b = None
                continue
            # same contract, and the previous cached session must be the trading
            # day before -- a gap means "yesterday's IB" is not yesterday's.
            if prev_b is not None and (day - days[k - 1]).days <= 4:
                pib = ib_of(prev_b)
                if pib:
                    lo, hi = pib
                    rng = hi - lo
                    if rng > 0:
                        op = float(b.open.iloc[0])
                        for mult in (1.0, 2.0):
                            for level, side in ((lo - mult * rng, "down"),
                                                (hi + mult * rng, "up")):
                                found.append(dict(sym=sym, day=day, bars=b,
                                                  open=op, mult=mult, side=side,
                                                  level=level, delta=level - op))
            prev_b = b

    # Pass 2: score the real level, then the same day at a delta borrowed from
    # another session of the same (mult, side) -- the session-drift null.
    rng_ = np.random.default_rng(20260605)
    pool: dict[tuple[float, str], list[float]] = defaultdict(list)
    for f in found:
        pool[(f["mult"], f["side"])].append(f["delta"])

    rows = []
    for f in found:
        base = score(f["bars"], f["level"], f["side"])
        if base is None:
            continue
        rows.append(dict(day=f["day"], mult=f["mult"], side=f["side"],
                         kind="real", pen=base[0], rev=base[1]))
        # Distance-matched: borrow only deltas within 15% of this level's own
        # distance from the open. Without this the placebo pool is dominated by
        # near-the-open horizontals, which price oscillates across all session
        # and which therefore post huge penetration for reasons that have
        # nothing to do with whether a level holds.
        want = abs(f["delta"])
        deltas = [d for d in pool[(f["mult"], f["side"])]
                  if 0.85 * want <= abs(d) <= 1.15 * want]
        if not deltas:
            continue
        for d in rng_.choice(deltas, size=min(4, len(deltas)), replace=False):
            if abs(d - f["delta"]) < 1e-9:
                continue  # do not borrow this day's own delta
            pl = score(f["bars"], f["open"] + float(d), f["side"])
            if pl:
                rows.append(dict(day=f["day"], mult=f["mult"], side=f["side"],
                                 kind="placebo", pen=pl[0], rev=pl[1]))

    df = pd.DataFrame(rows)
    if df.empty:
        print("no touches scored")
        return
    print(f"scored {len(df)} touches over {df.day.nunique()} sessions "
          f"({df[df.kind=='real'].shape[0]} real)\n")
    g = df.groupby(["mult", "kind"]).agg(n=("pen", "size"),
                                         pen=("pen", "median"),
                                         rev=("rev", "median")).round(2)
    print(g.to_string())
    print("\nby side:")
    g2 = df.groupby(["mult", "side", "kind"]).agg(n=("pen", "size"),
                                                  pen=("pen", "median"),
                                                  rev=("rev", "median")).round(2)
    print(g2.to_string())

    print("\nreal - placebo (negative pen / positive rev = level holds):")
    boot = np.random.default_rng(7)
    for mult in (1.0, 2.0):
        r = df[(df.mult == mult) & (df.kind == "real")]
        p = df[(df.mult == mult) & (df.kind == "placebo")]
        rp, pp = r.pen.to_numpy(), p.pen.to_numpy()
        obs = float(np.median(rp) - np.median(pp))
        # label-shuffle null: how often does a random split of the pooled
        # touches produce a gap this large?
        pooled = np.concatenate([rp, pp])
        hits = 0
        for _ in range(2000):
            s = boot.permutation(pooled)
            if abs(float(np.median(s[:len(rp)]) - np.median(s[len(rp):]))) >= abs(obs):
                hits += 1
        print(f"  {mult:g}x  d_pen {obs:+.2f}  "
              f"d_rev {r.rev.median()-p.rev.median():+.2f}  "
              f"n={len(r)}/{len(p)}  shuffle p={hits/2000:.3f}")


if __name__ == "__main__":
    main()
