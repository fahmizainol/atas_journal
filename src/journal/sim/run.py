"""CLI: run a strategy config and write the artifact.

    .venv/bin/python -m journal.sim.run --variant A
    .venv/bin/python -m journal.sim.run --variant B --min-band-width 150
    .venv/bin/python -m journal.sim.run --both --label "seed"

Same artifact the API produces (data/sims/<strategy>/<run_id>/); the UI picks
it up on the next refresh.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from . import registry, runner, store
from .rules import SimConfig


def _report(rid: str, cfg: SimConfig, df: pd.DataFrame, m: dict) -> None:
    print(f"\n=== variant {cfg.entry_variant}  ({rid}) ===")
    if df.empty:
        print("no trades")
        return
    print(f"trades={m['trades']}  net=${m['net_pnl']:,.0f}  win={m['win_rate']:.0f}%  "
          f"PF={m['profit_factor']:.2f}  expectancy=${m['expectancy']:,.0f}  "
          f"maxDD=${m['max_drawdown']:,.0f}")
    print(f"exits={m['exit_reasons']}  R: mean={m['r_mean']:.2f} median={m['r_median']:.2f} "
          f"best={m['r_best']:.2f}")
    print(f"band width at entry: median={m['band_width_median_ticks']:.0f}t  "
          f"min={m['band_width_min_ticks']:.0f}t  (stop is {cfg.stop_ticks}t)")
    if m["band_width_min_ticks"] < cfg.stop_ticks:
        n = int((df["band_width_ticks"] < cfg.stop_ticks).sum())
        print(f"  ! {n}/{len(df)} entries had dev2-dev1 NARROWER than the stop — "
              f"those risked more than the target was worth.")
    if "vetoed" in m:
        v = m["vetoed"]
        print(f"vetoed by confluences: {v['count']} entries, net ${v['net_pnl']:,.0f} "
              f"({v['by_gate']})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="vwap-upper-band-bounce",
                    choices=sorted(registry.STRATEGIES))
    ap.add_argument("--variant", choices=["A", "B"], default="A")
    ap.add_argument("--both", action="store_true", help="run A and B")
    ap.add_argument("--min-band-width", type=int, default=0)
    ap.add_argument("--stop-ticks", type=int, default=75)
    ap.add_argument("--acceptance-ticks", type=int, default=30)
    ap.add_argument("--ticks-per-bar", type=int, default=500)
    ap.add_argument("--label", default="", help="run label (mutable metadata, not identity)")
    args = ap.parse_args()

    strat = registry.get(args.strategy)
    base = SimConfig(
        min_band_width_ticks=args.min_band_width,
        stop_ticks=args.stop_ticks,
        acceptance_min_ticks=args.acceptance_ticks,
        ticks_per_bar=args.ticks_per_bar,
    )
    variants = ["A", "B"] if args.both else [args.variant]
    for v in variants:
        cfg = replace(base, entry_variant=v)
        rid = runner.execute(strat, cfg)
        state = store.read_state(strat.slug, rid) or {}
        if state.get("status") == "error":
            print(f"\n=== variant {v}  ({rid}) ===\nFAILED: {state.get('error')}")
            continue
        label = args.label or f"variant {v}"
        store.write_meta(strat.slug, rid, label=label)
        _, df, m = store.read_run(strat.slug, rid)
        _report(rid, cfg, df, m)


if __name__ == "__main__":
    main()
