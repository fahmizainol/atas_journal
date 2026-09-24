"""Does cutting the position when *unrealised* P&L touches −$500 beat the app's
between-trades stop?

The app disarms the day on **realised** P&L, so a trade opened at −$490 still takes
its whole stop and the measured worst day is −$790 rather than −$500. The obvious
alternative is a hard equity stop: out at market the moment the marked path touches
the level. That buys a tighter tail and pays for it in expectancy, because a trade
that was down $500 and would have come back is now closed. This prices both, and
then asks the only question that matters — what it does to a funded month.
"""
import collections, multiprocessing as mp, pathlib, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bracket_survival as B
import funded_payouts as FP

B.RISK_USD["mnq240"] = 240.0
PS = [0.60, 0.65, 0.70]
H, SEEDS = 2, 4
SIZING, STOP, GOAL = "mnq240", 500.0, 0.0
ARM = dict(key="ruler/1r/none", stop_basis="ruler", stop_ticks=None,
           target=("r", 1.0), trail=None, shape="1r/none", sizing=None)
MONTHS, TRADING_DAYS = 4_000, 21


def job(sd):
    sym, day = sd
    try:
        t, px = B.load_rth(sym, day)
    except Exception:
        return None
    if len(px) < 5_000:
        return None
    ruler = B.ruler_series(t, px)
    span = B.window_ms(t, "all")
    if span is None:
        return None
    out = collections.defaultdict(list)
    for s in range(SEEDS):
        for p in PS:
            rng = np.random.default_rng([day.toordinal(), s, int(p * 100), H, 55])
            e = B.draw(rng, t, px, p, H, span, B.ENTRIES_PER_DAY)
            out[p].append(B.run_day(t, px, ruler, e, ARM))
    return dict(out)


def main():
    days = B.sessions()
    trades = collections.defaultdict(list)
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for r in pool.imap(job, days, chunksize=4):
            if r:
                for k, v in r.items():
                    trades[k].extend(v)
    FP.configure(50_000)
    FP.TRADING_DAYS = TRADING_DAYS
    FP.CAL = FP.calendar_days(TRADING_DAYS)

    print(f"{len(days)} sessions x {SEEDS} draws, {ARM['key']}, ${STOP:.0f} day stop, "
          f"$240 risk in micros, LucidDaily 50K, keep-$1k\n")
    print("PER DAY")
    print(f"  {'p':>5} {'stop':>6} {'green%':>7} {'mean':>8} {'median':>8} {'p10':>8} "
          f"{'p90':>8} {'worst':>8} {'>-500 days':>11}")
    pools = {}
    for p in PS:
        for hard in (False, True):
            pl = [B.day_record(tr, SIZING, STOP, GOAL, "mtm", B.MAX_MINIS, hard)
                  for tr in trades[p]]
            pools[(p, hard)] = pl
            d = np.array([c for c, _ in pl])
            q = np.percentile(d, [10, 50, 90])
            worse = 100 * (d < -STOP).mean()
            print(f"  {p:>5} {'hard' if hard else 'soft':>6} {100*(d>0).mean():6.1f}% "
                  f"{d.mean():8.0f} {q[1]:8.0f} {q[0]:8.0f} {q[2]:8.0f} {d.min():8.0f} "
                  f"{worse:10.1f}%")

    print("\nA FUNDED MONTH (21 days, 4,000 bootstraps)")
    print(f"  {'p':>5} {'stop':>6} {'payouts':>8} {'take-home':>10} {'p(any)':>7} "
          f"{'dead':>7} {'p10':>8}")
    for p in PS:
        for hard in (False, True):
            pl = pools[(p, hard)]
            rng = np.random.default_rng([31, len(pl)])
            picks = rng.integers(len(pl), size=(MONTHS, TRADING_DAYS))
            res = [FP.month(pl, picks[i], "lucid_daily", FP.POLICIES["keep"])
                   for i in range(MONTHS)]
            n = np.array([r[1] for r in res], float)
            g = np.array([r[2] for r in res], float) * FP.SPLIT
            fates = collections.Counter(r[0] for r in res)
            print(f"  {p:>5} {'hard' if hard else 'soft':>6} {n.mean():8.2f} "
                  f"{g.mean():10,.0f} {100*(n>0).mean():6.1f}% "
                  f"{100*fates['bust']/MONTHS:6.1f}% {np.percentile(g,10):8,.0f}")


if __name__ == "__main__":
    main()
