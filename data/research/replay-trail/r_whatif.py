"""R-multiple bracket what-ifs across all stored replay sittings: no trail,
target capped at R x the placed stop distance (computed at fill, since the
log stores absolute levels). 'sf' variants drop recorded bracket drags
(set-and-forget); manual closes and the end-of-sitting flatten are kept.

Companion to exit_whatif.py; see docs/research/replay-exit-whatif.md §5.

Usage:
    .venv/bin/python data/research/replay-trail/r_whatif.py
"""
import collections
import importlib.util
import json
import pathlib
from multiprocessing import Pool

ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "exit_whatif", pathlib.Path(__file__).parent / "exit_whatif.py")
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)
w = X.w

R_MULT = None
_orig_open = w.open_position


def open_r(o, legs, ms, idx, price, size):
    p = _orig_open(o, legs, ms, idx, price, size)
    if R_MULT is not None and p["stop"] is not None:
        d = 1 if p["side"] == "long" else -1
        p["target"] = price + d * R_MULT * abs(price - p["stop"])
    return p


w.open_position = open_r

# name -> (R, trail_spec as in exit_whatif, keep_drags)
SCEN = [
    ("as-played", (None, "asis", True)),
    ("no-trail", (None, None, True)),
    ("r1", (1.0, None, True)),
    ("r1sf", (1.0, None, False)),
    ("r15sf", (1.5, None, False)),
    ("r2sf", (2.0, None, False)),
    ("r1be25sf", (1.0, dict(dist=25, step=0, beOnly=True), False)),
]


def run_one(aid):
    global R_MULT
    a, log, recorded, summ = w.load_attempt(aid)
    t, px = w.load_tape(a["symbol"], a["date"], a["tz"])
    clock = a["clock_ms"]
    R_MULT = None
    cfg = X.pick_cfg(a, t, px, log, recorded, clock)
    if cfg is None:
        return dict(aid=aid, valid=False)
    out = dict(aid=aid, date=a["date"], valid=True,
               contracts=sum(tr["size"] for tr in recorded), scen={})
    for name, (r, tspec, drags) in SCEN:
        lg = X.apply_scenario(log, tspec)
        if not drags:
            lg["brackets"] = []
        R_MULT = r
        st = X.run_flat(t, px, lg, clock, cfg)
        R_MULT = None
        out["scen"][name] = w.summarize(st["trades"])
    return out


def main():
    aids = []
    for d in sorted((ROOT / "data/replays").glob("*/*/")):
        if not (d / "log.json").exists() or not (d / "summary.json").exists():
            continue
        if json.loads((d / "summary.json").read_text())["trades"] == 0:
            continue
        aids.append(d.name)
    with Pool(8) as pool:
        results = [r for r in pool.imap_unordered(run_one, aids) if r["valid"]]
    results.sort(key=lambda r: r["date"])
    print(f"{len(results)}/{len(aids)} sittings validate")
    names = [n for n, _ in SCEN]
    hdr = f"{'date':<11}{'n':>4}" + "".join(f"{n:>10}" for n in names)
    print(hdr)
    for r in results:
        print(f"{r['date']:<11}{r['scen']['as-played']['n']:>4}"
              + "".join(f"{r['scen'][n]['net']:>10}" for n in names))
    print("-" * len(hdr))
    print(f"{'TOTAL':<15}"
          + "".join(f"{sum(r['scen'][n]['net'] for r in results):>10}" for n in names))
    base = sum(r["scen"]["as-played"]["net"] for r in results)
    print()
    print(f"{'scenario':<10}{'total$':>9}{'Δtotal':>9}{'better':>8}{'worse':>7}{'wr%':>5}  exits")
    for n in names:
        deltas = [r["scen"][n]["net"] - r["scen"]["as-played"]["net"] for r in results]
        tot = sum(r["scen"][n]["net"] for r in results)
        c = collections.Counter()
        wins = tr = 0
        for r in results:
            s = r["scen"][n]
            for k, v in s["reasons"].items():
                c[k] += v
            wins += round(s["wr"] * s["n"] / 100)
            tr += s["n"]
        print(f"{n:<10}{tot:>9}{tot - base:>9}"
              f"{sum(1 for d in deltas if d > 0):>8}{sum(1 for d in deltas if d < 0):>7}"
              f"{100 * wins / tr:>5.0f}  {dict(c)}")


if __name__ == "__main__":
    main()
