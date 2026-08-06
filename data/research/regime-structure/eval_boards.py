"""Do the v8 structure/chop KPIs separate P&L on the current baselines?

Runs regime_pnl.study() (the same boards + luck machinery the UI uses) over the
adopted baseline runs and prints (a) where the new KPIs rank at every
checkpoint, (b) the class x texture expectancy grid. Nothing here is an A/B —
it is the "re-cut the lead on the current baseline" step that has to come
before anyone dreams about knobs.

Usage: .venv/bin/python data/research/regime-structure/eval_boards.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from journal.sim import regime_pnl  # noqa: E402

RUNS = [
    ("vwap-upper-band-bounce", "20250201-20260630-v13-a348d176"),
    ("drift-touch-fade-entry-stop", "20250203-20260630-v2-95580b82"),
    ("profile-pullback-long", "20250601-20260131-v4-5092c2f1"),
    ("ema-pullback-long", "20250203-20251231-v1-73dbb43a"),
    ("value-rotation", "20250801-20260130-v1-c71aefcb"),
]
NEW = {"st_bias", "st_bias_age_min", "st_bias_share", "st_break_rate",
       "st_bos_share", "st_choch_rate", "chop_occ_30m", "chop_occ_rth"}


def eval_run(slug: str, run: str) -> None:
    base = Path("data/sims") / slug / run
    cfg = json.loads((base / "config.json").read_text())
    trades = pd.read_parquet(base / "trades.parquet")
    s = regime_pnl.study(cfg["contract"], date.fromisoformat(cfg["start_date"]),
                         date.fromisoformat(cfg["end_date"]), trades)

    print(f"\n{'=' * 78}\n{slug} / {run}  ({s['traded_days']} traded days)\n{'=' * 78}")
    for cp in s["checkpoints"]:
        b = s["boards"].get(cp)
        if not b:
            continue
        rows = b["rows"]
        print(f"\n--- {cp}  (luck bar {b['luck_bar']:.4f}, "
              f"{b['holds']} hold of {len(rows)}) ---")
        print(f"{'rank':>4} {'kpi':32s} {'rho':>6} {'edge $/day':>11} "
              f"{'luck':>6} {'holds':>5}")
        for i, r in enumerate(rows):
            if r["key"] not in NEW and not r["holds"]:
                continue
            tag = " *NEW*" if r["key"] in NEW else ""
            print(f"{i + 1:>4} {r['key']:32s} {r['rho']:>6.3f} {r['edge']:>11.0f} "
                  f"{r['luck']:>6.3f} {str(r['holds']):>5}{tag}")

    print("\n--- class x texture grid (eod labels, traded days) ---")
    print(f"{'class':12s} {'texture':8s} {'days':>5} {'trades':>7} "
          f"{'net':>10} {'avg/day':>9} {'win%':>6}")
    for g in s["class_texture_grid"]:
        wr = f"{g['win_rate']:.0f}" if g["win_rate"] is not None else "-"
        print(f"{g['class']:12s} {g['texture']:8s} {g['days']:>5} {g['trades']:>7} "
              f"{g['net']:>10.0f} {g['avg_net']:>9.0f} {wr:>6}")

    print("\n--- class buckets (for reference) ---")
    for g in s["class_buckets"]:
        print(f"{g['class']:12s} {'':8s} {g['days']:>5} {g['trades']:>7} "
              f"{g['net']:>10.0f} {g['avg_net']:>9.0f}")


for slug, run in RUNS:
    eval_run(slug, run)
