"""R-multiple bracket what-ifs across all stored replay sittings: no trail,
target capped at R x the placed stop distance (computed at fill, since the
log stores absolute levels). Manual closes and the end-of-sitting flatten are kept.

Companion to exit_whatif.py; see docs/research/replay-exit-whatif.md §5.

The R multiple used to be a module-global monkeypatch over the engine's
`open_position`. It is a field of the scenario spec now, and the ladder itself is
`journal.replay_whatif.PRESETS` — the same rows the app serves per sitting. One
consequence for anyone comparing against the old printout: every counterfactual row
drops recorded bracket drags, because a drag writes an absolute level that would
overwrite the row's own target. The old `r1` row (which kept them) no longer exists;
`r1` here is what that run called `r1sf`.

Usage:
    .venv/bin/python data/research/replay-trail/r_whatif.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
from multiprocessing import Pool

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal.replay_whatif import (  # noqa: E402
    PRESETS,
    apply_scenario,
    contract_spec,
    load_tape,
    pick_cfg,
    run_flat,
    scen_of,
    summarize,
)
from journal.replays import read as read_attempt  # noqa: E402

#: The R half of the shared ladder, against the two baselines it is read against.
SCEN = [r for r in PRESETS
        if r["key"] in ("as-played", "no-trail") or r["spec"].get("targetR") is not None]


def run_one(aid):
    try:
        a = read_attempt(aid)
        log, recorded = a["log"], a["trades"]
        t, px = load_tape(a["symbol"], a["date"], a.get("tz"))
        clock = a["clock_ms"]
        tick_size = float(contract_spec(a["symbol"])["tick_size"])
        cfg, _ = pick_cfg(a, t, px, log, recorded, clock)
        if cfg is None:
            return dict(aid=aid, valid=False)
        out = dict(aid=aid, date=a["date"], valid=True,
                   contracts=sum(tr["size"] for tr in recorded), scen={})
        for row in SCEN:
            drop_drags = row["spec"].get("targetR") is not None
            lg = apply_scenario(log, row["spec"], tick_size, drop_drags=drop_drags)
            st = run_flat(t, px, lg, clock, cfg, scen_of(row["spec"]))
            out["scen"][row["key"]] = summarize(st["trades"])
        return out
    except Exception:
        return dict(aid=aid, valid=False)


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
    names = [row["key"] for row in SCEN]
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
