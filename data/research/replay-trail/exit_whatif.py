"""Exit-setting what-if across ALL stored replay sittings.

Re-runs every sitting's order log under a grid of exit regimes — no trail,
breakeven-only, fixed trails 25..100t, stepped trails — and prints per-sitting and
aggregate nets. See docs/research/replay-exit-whatif.md for the study this produced.

The engine, the scenario ladder and the fill-model search all live in
`journal.replay_whatif` now; the app serves the same rows per sitting from a day
view. This script is the corpus-wide report over them, and nothing more.

Sittings recorded before an engine change may not validate at all (their trades.json
is frozen from the old build); they are excluded, loudly.

Usage:
    .venv/bin/python data/research/replay-trail/exit_whatif.py
"""
from __future__ import annotations

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

#: The trail half of the shared ladder. The R rows are r_whatif.py's report, and
#: the reversed rows change the entry rather than the exit — neither belongs in a
#: study of exit settings.
SCENARIOS = [r for r in PRESETS
             if r["spec"].get("targetR") is None and not r["spec"].get("flip")]


def run_one(aid):
    try:
        a = read_attempt(aid)
        log, recorded = a["log"], a["trades"]
        t, px = load_tape(a["symbol"], a["date"], a.get("tz"))
        clock = a["clock_ms"]
        tick_size = float(contract_spec(a["symbol"])["tick_size"])
        cfg, _ = pick_cfg(a, t, px, log, recorded, clock)
        if cfg is None:
            return dict(aid=aid, error="no cfg validates", valid=False)
        out = dict(aid=aid, date=a["date"], cfg=cfg, valid=True,
                   drift=len(t) != a["tape"]["n"],
                   prefs=dict(trail=a["prefs"]["trailTicks"],
                              step=a["prefs"]["trailStepTicks"],
                              stop=a["prefs"]["stopTicks"],
                              tgt=a["prefs"]["targetTicks"]),
                   contracts=sum(tr["size"] for tr in recorded),
                   stored=summarize(recorded), scen={})
        for row in SCENARIOS:
            lg = apply_scenario(log, row["spec"], tick_size)
            st = run_flat(t, px, lg, clock, cfg, scen_of(row["spec"]))
            out["scen"][row["key"]] = summarize(st["trades"])
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

    names = [row["key"] for row in SCENARIOS]
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
