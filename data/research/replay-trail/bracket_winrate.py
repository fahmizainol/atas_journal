"""Per-trade win rate for the arms that matter. Net of costs is what counts."""
import sys, numpy as np, multiprocessing as mp
sys.path.insert(0,'data/research/replay-trail')
import bracket_survival as B

KEYS = ["t75/3r/none","t75/2r/none","t75/1.5r/none","t75/none/be1r","t75/t120/none",
        "ruler/none/be1r","ruler/none/trail1r","ruler/1.5r/be1r","ruler/1r/none",
        "t50/1r/none","t50/1.5r/be1r","t35/1r/trail.5r","t75/3r/trail1r"]
ARMS = [a for a in B.ARMS if a["key"] in KEYS and not a["sizing"]]
COM  = 2 * B.CFG["commission"]          # $7 round turn, 1 NQ

def job(sd):
    sym, day, seeds = sd
    try: t, px = B.load_rth(sym, day)
    except Exception: return None
    if len(px) < 5000: return None
    rl = B.ruler_series(t, px)
    out = {a["key"]: [] for a in ARMS}
    for s in range(seeds):
        ent = B.draw(np.random.default_rng([day.toordinal(), s]), t)
        for a in ARMS:
            for pts, st, path, why in B.run_day(t, px, rl, ent, a):
                out[a["key"]].append((pts * B.POINT_VALUE - COM, why))
    return out

if __name__ == "__main__":
    days = B.sessions()
    days = days[::max(1, len(days)//150)][:150]
    jobs = [(s, d, 2) for s, d in days]
    acc = {a["key"]: [] for a in ARMS}
    with mp.Pool(6) as pool:
        for r in pool.imap_unordered(job, jobs):
            if r:
                for k, v in r.items(): acc[k].extend(v)
    print(f"{len(days)} sessions x 2 draws, 1 NQ, net of $7 round turn + 1t spread\n")
    print(f"{'bracket':<22}{'trades':>7}{'win%':>7}{'avg win':>9}{'avg loss':>9}"
          f"{'exp/trade':>10}{'/day':>8}  exit mix")
    for a in ARMS:
        v = acc[a["key"]]
        if not v: continue
        p = np.array([x[0] for x in v])
        w, l = p[p > 0], p[p <= 0]
        why = {}
        for _, r in v: why[r] = why.get(r, 0) + 1
        mix = " ".join(f"{k}{100*n/len(v):.0f}%" for k, n in sorted(why.items(), key=lambda kv:-kv[1]))
        print(f"{a['key']:<22}{len(v):>7}{100*len(w)/len(v):>6.1f}%{w.mean():>9.0f}{l.mean():>9.0f}"
              f"{p.mean():>10.1f}{p.mean()*len(v)/len(days)/2:>8.0f}  {mix}")
