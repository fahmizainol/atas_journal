"""Sweep the bracket's R ratio around the vol ruler, on both axes.

**Axis A** (run first) held the target at the ruler's full width and moved the stop
in underneath, which restates the bracket at a higher R:

    stop 0.75 x ruler, target = ruler  ->  1.33R
    stop 0.50 x ruler, target = ruler  ->  2.00R

Both lost, monotonically, and the 2R arm landed exactly on its own break-even win
rate. That said nothing about the other direction, so:

**Axis B** (this run) pins the stop at 1.0 x ruler — the width the ruler is actually
calibrated to — and sweeps the target from **0.50R to 1.50R**. It is the cleaner
experiment: only one leg moves, and the stop stays where the day's own noise put it.

The two axes cross: axis A's 0.75x/ruler and axis B's ruler/1.33R are the same *R*
at different absolute widths, which is what separates "the payoff ratio" from "how
wide the stop is relative to the noise".

Note ``ruler/1.5r`` exists in the main sweep (§12) but was scored there by evaluation
pass rate at mini sizing. This re-measures it at $240 of micro risk against a funded
month, so the column is comparable with everything else here.
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
MONTHS, TRADING_DAYS = 4_000, 21

def arm(name, stop_mult, target_r):
    return dict(key=name, stop_basis="ruler", stop_ticks=None, stop_mult=stop_mult,
                target=("r", round(target_r, 4)), trail=None,
                shape=f"{target_r:.2f}r", sizing=None)

#: Axis B — stop pinned at the ruler, target swept. 1.00R is the shared baseline.
ARMS = [arm("sl ruler, tp 0.50R", 1.00, 0.50),
        arm("sl ruler, tp 0.75R", 1.00, 0.75),
        arm("sl ruler, tp 1.00R  (baseline)", 1.00, 1.00),
        arm("sl ruler, tp 1.33R", 1.00, 4 / 3),
        arm("sl ruler, tp 1.50R", 1.00, 1.50)]

#: Axis A, already measured (doc §16.5) — target at the ruler, stop moved in:
#:   arm("sl 0.75x ruler, tp ruler (1.33R)", 0.75, 4/3)
#:   arm("sl 0.50x ruler, tp ruler (2.00R)", 0.50, 2.0)


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
            for a in ARMS:
                out[(a["key"], p)].append(B.run_day(t, px, ruler, e, a))
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

    print(f"{len(days)} sessions x {SEEDS} draws, ${STOP:.0f} day stop, no goal, "
          f"$240 risk in micros, LucidDaily 50K keep-$1k\n")
    print("PER TRADE")
    print(f"  {'arm':32} {'p':>5} {'med stop':>9} {'micros':>7} {'win%':>7} "
          f"{'mean$':>8} {'avg win':>8} {'avg loss':>9} {'target%':>8}")
    for a in ARMS:
        for p in PS:
            flat = [x for d in trades[(a["key"], p)] for x in d]
            net, wins, sts, ns = [], collections.Counter(), [], []
            for pts, st, _pa, why in flat:
                pv, fees = B.position(SIZING, st)
                net.append(pts * pv - fees); wins[why] += 1; sts.append(st)
                ns.append(pv / B.MICRO_POINT_VALUE)
            net = np.array(net); w = net[net > 0]; l = net[net <= 0]
            print(f"  {a['key']:32} {p:>5} {np.median(sts):8.0f}t {np.median(ns):7.0f} "
                  f"{100*len(w)/len(net):6.1f}% {net.mean():8.2f} {w.mean():8.0f} "
                  f"{l.mean():9.0f} {100*wins['target']/len(net):7.1f}%")

    print("\nPER DAY, then A FUNDED MONTH")
    print(f"  {'arm':32} {'p':>5} {'green%':>7} {'day mean':>9} {'day med':>8} "
          f"{'payouts':>8} {'take-home':>10} {'dead':>7}")
    for a in ARMS:
        for p in PS:
            pl = [B.day_record(tr, SIZING, STOP, GOAL, "mtm", B.MAX_MINIS)
                  for tr in trades[(a["key"], p)]]
            d = np.array([c for c, _ in pl])
            rng = np.random.default_rng([31, len(pl)])
            picks = rng.integers(len(pl), size=(MONTHS, TRADING_DAYS))
            res = [FP.month(pl, picks[i], "lucid_daily", FP.POLICIES["keep"])
                   for i in range(MONTHS)]
            n = np.array([r[1] for r in res], float)
            g = np.array([r[2] for r in res], float) * FP.SPLIT
            bust = collections.Counter(r[0] for r in res)["bust"]
            print(f"  {a['key']:32} {p:>5} {100*(d>0).mean():6.1f}% {d.mean():9.0f} "
                  f"{np.median(d):8.0f} {n.mean():8.2f} {g.mean():10,.0f} "
                  f"{100*bust/MONTHS:6.1f}%")


if __name__ == "__main__":
    main()
