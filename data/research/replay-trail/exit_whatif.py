"""Exit-setting what-if across ALL stored replay sittings.

Companion to whatif.py (the validated engine port): re-runs every sitting's
order log under a grid of exit regimes — no trail, breakeven-only, fixed
trails 25..100t, stepped trails — and prints per-sitting and aggregate nets.
See docs/research/replay-exit-whatif.md for the study this produced.

Two traps beyond the ones whatif.py documents:

  - the browser marks a position still open at the sitting's end clock to
    market and books it into trades.json; the port must flatten the same way
    before comparing (run_flat), or open-ended sittings never validate.
  - attempt.json prefs are a creation-time snapshot, but stored trades
    re-derive under the CURRENT fill model (localStorage sim.fills) — e.g. the
    commission 7→3.5 migration. pick_cfg tries candidates and keeps whichever
    reproduces the stored net to the dollar; engine-v1 sittings (no fill
    model) validate at 0/0/0.

Sittings recorded before an engine change may not validate at all (their
trades.json is frozen from the old build); they are excluded, loudly.

Usage:
    .venv/bin/python data/research/replay-trail/exit_whatif.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
from multiprocessing import Pool

ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "whatif", pathlib.Path(__file__).parent / "whatif.py")
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

T = 0.25

# name -> "asis" | None (no trail) | dict(dist/step in ticks, beOnly)
SCENARIOS = [
    ("as-played", "asis"),
    ("no-trail", None),
    ("be25", dict(dist=25, step=0, beOnly=True)),
    ("be50", dict(dist=50, step=0, beOnly=True)),
    ("t25", dict(dist=25, step=0, beOnly=False)),
    ("t35", dict(dist=35, step=0, beOnly=False)),
    ("t50", dict(dist=50, step=0, beOnly=False)),
    ("t75", dict(dist=75, step=0, beOnly=False)),
    ("t100", dict(dist=100, step=0, beOnly=False)),
    ("t50s10", dict(dist=50, step=10, beOnly=False)),
    ("t50s25", dict(dist=50, step=25, beOnly=False)),
    ("t75s25", dict(dist=75, step=25, beOnly=False)),
    ("t100s25", dict(dist=100, step=25, beOnly=False)),
]


def apply_scenario(log, spec):
    lg = copy.deepcopy(log)
    if spec == "asis":
        return lg
    for o in lg["orders"]:
        if spec is None:
            o["trail"] = None
            continue
        old = o.get("trail")
        be = old["be"] if old and old.get("dist", 0) > 0 else 3 * T
        o["trail"] = dict(dist=spec["dist"] * T, step=spec["step"] * T,
                          be=be, beOnly=spec["beOnly"])
    return lg


def run_flat(t, px, log, clock, cfg):
    """run_sim + flatten anything still open at the end clock, the way the
    browser marks an open position to market into trades.json."""
    st = w.run_sim(t, px, log, clock, cfg)
    if st["open"]:
        p = st["open"]
        last = w.price_at_ms(t, px, clock)
        w.reduce(st, p["size"], clock,
                 w.cross(last, p["side"] == "short", cfg), "open", cfg)
    return st


def pick_cfg(a, t, px, log, recorded, clock):
    """Prefs snapshot is creation-time; stored trades re-derive under the
    current fill model. Keep the first candidate that reproduces the stored
    net to the dollar."""
    cands = []
    p = a["prefs"]
    if "commission" in p:
        cands.append(dict(commission=p["commission"], slipTicks=p["slipTicks"],
                          queueTicks=p["queueTicks"]))
    cands += [dict(commission=3.5, slipTicks=1, queueTicks=1),
              dict(commission=0, slipTicks=0, queueTicks=0),
              dict(commission=7, slipTicks=1, queueTicks=1)]
    want = w.summarize(recorded)
    for cfg in cands:
        got = w.summarize(run_flat(t, px, copy.deepcopy(log), clock, cfg)["trades"])
        if (got["n"], got["net"]) == (want["n"], want["net"]):
            return cfg
    return None


def run_one(aid):
    try:
        a, log, recorded, summ = w.load_attempt(aid)
        t, px = w.load_tape(a["symbol"], a["date"], a["tz"])
        clock = a["clock_ms"]
        cfg = pick_cfg(a, t, px, log, recorded, clock)
        if cfg is None:
            return dict(aid=aid, error="no cfg validates", valid=False)
        out = dict(aid=aid, date=a["date"], cfg=cfg, valid=True,
                   drift=len(t) != a["tape"]["n"],
                   prefs=dict(trail=a["prefs"]["trailTicks"],
                              step=a["prefs"]["trailStepTicks"],
                              stop=a["prefs"]["stopTicks"],
                              tgt=a["prefs"]["targetTicks"]),
                   contracts=sum(tr["size"] for tr in recorded),
                   stored=w.summarize(recorded), scen={})
        for name, spec in SCENARIOS:
            st = run_flat(t, px, apply_scenario(log, spec), clock, cfg)
            out["scen"][name] = w.summarize(st["trades"])
        return out
    except Exception as e:
        return dict(aid=aid, error=repr(e), valid=False)


def main():
    aids = []
    for d in sorted((ROOT / "data/replays").glob("*/*/")):
        if not (d / "log.json").exists() or not (d / "summary.json").exists():
            continue
        if json.loads((d / "summary.json").read_text())["trades"] == 0:
            continue
        aids.append(d.name)
    print(f"{len(aids)} sittings with trades")
    with Pool(8) as pool:
        results = list(pool.imap_unordered(run_one, aids))
    for r in sorted(results, key=lambda r: r["aid"]):
        print(f"  {r['aid']}  {'OK' if r['valid'] else r.get('error')}")
    ok = sorted([r for r in results if r["valid"]], key=lambda r: r["date"])

    names = [n for n, _ in SCENARIOS]
    hdr = f"{'date':<11}{'n':>4}" + "".join(f"{n:>9}" for n in names)
    print()
    print(hdr)
    for r in ok:
        print(f"{r['date']:<11}{r['scen']['as-played']['n']:>4}"
              + "".join(f"{r['scen'][n]['net']:>9}" for n in names))
    print("-" * len(hdr))
    print(f"{'TOTAL':<15}"
          + "".join(f"{sum(r['scen'][n]['net'] for r in ok):>9}" for n in names))

    base = sum(r["scen"]["as-played"]["net"] for r in ok)
    print()
    print(f"{'scenario':<10}{'total$':>9}{'Δtotal':>9}{'better':>8}{'worse':>7}{'Δ/ct':>7}")
    for n in names:
        deltas = [r["scen"][n]["net"] - r["scen"]["as-played"]["net"] for r in ok]
        tot = sum(r["scen"][n]["net"] for r in ok)
        print(f"{n:<10}{tot:>9}{tot - base:>9}"
              f"{sum(1 for d in deltas if d > 0):>8}{sum(1 for d in deltas if d < 0):>7}"
              f"{sum(deltas) / sum(r['contracts'] for r in ok):>7.1f}")


if __name__ == "__main__":
    main()
