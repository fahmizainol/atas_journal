"""How does a fixed 40-tick stop do on days the s30 ruler reads WIDE early?

Conditions each session on the ruler's median reading over 09:35-11:35 and runs a
focused arm set with entries in that same two-hour window, so the condition is live
for every trade it prices. Everything delegates to bracket_survival, which is gated
against the app engine.
"""
import collections
import multiprocessing as mp
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path("data/research/replay-trail").resolve()))
import bracket_survival as B  # noqa: E402

H2_START = B.ENTRY_OPEN_SOD                 # 09:35, the ruler warmup gate
H2_END = 11 * 3600 + 35 * 60                # 11:35
N_ENTRIES = max(1, round(B.ENTRIES_PER_DAY * (H2_END - H2_START) / B.FULL_SPAN_S))
SEEDS = 8
PS = [0.5, 0.7]
HORIZON = 2

STOPS = [("t40", 40), ("t50", 50), ("t75", 75), ("t100", 100), ("ruler", None)]
SHAPES = [("1r/none", ("r", 1.0), None),
          ("1.5r/none", ("r", 1.5), None),
          ("none/trail1r", None, dict(distR=1.0, stepR=0.0, beTicks=B.BE_TICKS, beOnly=False))]
ARMS = [dict(key=f"{sk}/{shk}", stop_basis=sk, stop_ticks=st, target=tg, trail=tr,
             shape=shk, sizing=None)
        for sk, st in STOPS for shk, tg, tr in SHAPES]

BUCKETS = [(0, 40, "<40t"), (40, 60, "40-60t"), (60, 80, "60-80t"),
           (80, 100, "80-100t"), (100, 1e9, ">100t")]


def bucket_of(v):
    for lo, hi, name in BUCKETS:
        if lo <= v < hi:
            return name
    return None


def job(sd):
    sym, day = sd
    try:
        t, px = B.load_rth(sym, day)
    except Exception:
        return None
    if len(px) < 5_000:
        return None
    ruler = B.ruler_series(t, px)
    sod = (t / 1000.0) % 86400.0
    m = (sod >= H2_START) & (sod < H2_END) & np.isfinite(ruler)
    if m.sum() < 100:
        return None
    lvl = float(np.median(ruler[m]))          # the day's early ruler level
    span = B.window_ms(t, "all")
    lo = int(np.searchsorted(sod, H2_START, "left"))
    hi = int(np.searchsorted(sod, H2_END, "left"))
    if hi - lo < 2:
        return None
    span = (float(t[lo]), float(t[hi - 1]))

    out = collections.defaultdict(list)
    for s in range(SEEDS):
        for p in PS:
            rng = np.random.default_rng([day.toordinal(), s, int(p * 100), HORIZON, 99])
            entries = B.draw(rng, t, px, p, HORIZON, span, N_ENTRIES)
            for arm in ARMS:
                for pts, st_, _exc, why in B.run_day(t, px, ruler, entries, arm):
                    out[(arm["key"], p)].append((pts, st_, why))
    return lvl, {k: v for k, v in out.items()}


def main():
    days = B.sessions()
    print(f"{len(days)} sessions, {len(ARMS)} arms, {SEEDS} seeds, "
          f"{N_ENTRIES} entries in 09:35-11:35")
    per_bucket = collections.defaultdict(lambda: collections.defaultdict(list))
    n_days = collections.Counter()
    levels = []
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for i, r in enumerate(pool.imap_unordered(job, days, chunksize=4), 1):
            if r is None:
                continue
            lvl, arms = r
            levels.append(lvl)
            b = bucket_of(lvl)
            n_days[b] += 1
            for k, v in arms.items():
                per_bucket[b][k].extend(v)
            if i % 200 == 0:
                print(f"  {i}/{len(days)}")

    lv = np.array(levels)
    print(f"\nearly-ruler level across {len(lv)} sessions: median {np.median(lv):.0f}t, "
          f"p10 {np.percentile(lv, 10):.0f}t, p90 {np.percentile(lv, 90):.0f}t, max {lv.max():.0f}t")
    print("day counts:", {name: n_days[name] for _, _, name in BUCKETS})

    def stat(rows):
        if not rows:
            return None
        pts = np.array([r[0] for r in rows])
        gross = pts * B.POINT_VALUE
        net = gross - 2 * B.CFG["commission"]
        why = collections.Counter(r[2] for r in rows)
        return dict(n=len(rows), net=net.mean(), gross=gross.mean(),
                    win=100 * (net > 0).mean(),
                    stop=100 * why["stop"] / len(rows),
                    med_stop=float(np.median([r[1] for r in rows])))

    for p in PS:
        print("\n" + "=" * 96)
        print(f"p = {p}   —   net $/trade (win% | stop-out%),  entries 09:35-11:35, 1 NQ")
        print("=" * 96)
        for shk, _, _ in SHAPES:
            print(f"\n  shape {shk}")
            hdr = "  ".join(f"{name:>16}" for _, _, name in BUCKETS)
            print(f"    {'stop':10} {hdr}")
            for sk, _ in STOPS:
                cells = []
                for _, _, name in BUCKETS:
                    s = stat(per_bucket[name].get((f"{sk}/{shk}", p), []))
                    cells.append("               -" if not s else
                                 f"{s['net']:7.1f} ({s['win']:2.0f}|{s['stop']:2.0f})")
                print(f"    {sk:10} " + "  ".join(f"{c:>16}" for c in cells))
            # what the ruler actually armed in each bucket
            med = []
            for _, _, name in BUCKETS:
                s = stat(per_bucket[name].get((f"ruler/{shk}", p), []))
                med.append("-" if not s else f"{s['med_stop']:.0f}t")
            print(f"    {'(ruler armed)':10} " + "  ".join(f"{c:>16}" for c in med))


if __name__ == "__main__":
    main()
