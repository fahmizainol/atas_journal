"""What p actually means, and what a day looks like under the moderate config."""
import collections, multiprocessing as mp, pathlib, sys
import numpy as np
sys.path.insert(0, "data/research/replay-trail")
import bracket_survival as B

B.RISK_USD["mnq240"] = 240.0
PS = [0.50, 0.60, 0.65, 0.70]
H, SEEDS = 2, 4
ARM = dict(key="ruler/1r/none", stop_basis="ruler", stop_ticks=None,
           target=("r", 1.0), trail=None, shape="1r/none", sizing=None)
STOP, GOAL = 500.0, 0.0          # the moderate route: -$500 day stop, no goal

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
    out = {}
    for p in PS:
        trades, days = [], []
        for s in range(SEEDS):
            rng = np.random.default_rng([day.toordinal(), s, int(p*100), H, 55])
            e = B.draw(rng, t, px, p, H, span, B.ENTRIES_PER_DAY)
            tr = B.run_day(t, px, ruler, e, ARM)
            for pts, st, _path, why in tr:
                pv, fees = B.position("mnq240", st)
                trades.append((pts * pv - fees, why, st))
            close, _sk = B.day_record(tr, "mnq240", STOP, GOAL, "mtm", B.MAX_MINIS)
            days.append(close)
        out[p] = (trades, days)
    return out

if __name__ == "__main__":
    days = B.sessions()
    per = {p: ([], []) for p in PS}
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for r in pool.imap(job, days, chunksize=4):
            if r:
                for p, (tr, dy) in r.items():
                    per[p][0].extend(tr); per[p][1].extend(dy)
    print(f"{len(days)} sessions x {SEEDS} draws, arm {ARM['key']}, "
          f"$240 risk in micros, -$500 day stop, no goal\n")
    print("PER TRADE")
    print(f"  {'p':>5} {'n':>8} {'win%':>7} {'mean$':>8} {'median$':>8} "
          f"{'avg win':>8} {'avg loss':>9} {'stop%':>6} {'target%':>8}")
    for p in PS:
        tr = per[p][0]
        net = np.array([x[0] for x in tr]); why = collections.Counter(x[1] for x in tr)
        w = net[net > 0]; l = net[net <= 0]
        print(f"  {p:>5} {len(net):8,} {100*len(w)/len(net):6.1f}% {net.mean():8.2f} "
              f"{np.median(net):8.2f} {w.mean():8.2f} {l.mean():9.2f} "
              f"{100*why['stop']/len(net):5.1f}% {100*why['target']/len(net):7.1f}%")
    print("\nPER DAY (one funded trading day)")
    print(f"  {'p':>5} {'green%':>7} {'mean$':>8} {'p10':>8} {'p25':>8} {'median':>8} "
          f"{'p75':>8} {'p90':>8} {'worst':>8} {'best':>8}")
    for p in PS:
        d = np.array(per[p][1])
        q = np.percentile(d, [10, 25, 50, 75, 90])
        print(f"  {p:>5} {100*(d>0).mean():6.1f}% {d.mean():8.0f} {q[0]:8.0f} {q[1]:8.0f} "
              f"{q[2]:8.0f} {q[3]:8.0f} {q[4]:8.0f} {d.min():8.0f} {d.max():8.0f}")
    print("\n  trades a day (after the -$500 stop truncates):")
    for p in PS:
        print(f"    p={p}: {len(per[p][0])/len(per[p][1]):.1f}")
