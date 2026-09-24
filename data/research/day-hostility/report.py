"""Rank one session against the corpus on every artifact this study writes.

Prints the tables that go into `docs/research/day-hostility.md`. Takes the day to
put under the microscope so the study is re-runnable against the next day that
feels unfair, rather than being welded to 2026-03-25.

Usage:
    .venv/bin/python data/research/day-hostility/report.py [YYYY-MM-DD]
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-03-25"


def pctile(arr: np.ndarray, v: float) -> float:
    return float((arr < v).mean() * 100)


def _load(name: str):
    p = HERE / name
    if not p.exists():
        print(f"  (missing {name} — run its script first)")
        return None
    return json.loads(p.read_text())


def base_rate():
    rows = _load("base_rate.json")
    if not rows:
        return
    me = [r for r in rows if r["day"] == DAY]
    if not me:
        print(f"  {DAY} not in the corpus")
        return
    me = me[0]
    for win in ("am", "pm"):
        have = [r for r in rows if win in r and win in me]
        if not have or win not in me:
            continue
        rul = np.array([r[win]["median_ruler_t"] for r in have], dtype=float)
        print(f"\n  --- {win} ({len(have)} sessions) --- "
              f"ruler {me[win]['median_ruler_t']:.0f}t vs corpus median "
              f"{np.median(rul):.0f}t (pctile {pctile(rul, me[win]['median_ruler_t']):.0f})")
        print(f"    {'stop':>8}{'2R share':>11}{'pctile':>8}{'net/trade':>12}{'pctile':>8}")
        for mode in ("ruler", "s30", "s40", "s56"):
            k = f"{mode}_R2.0"
            if k not in me[win]:
                continue
            cells = [r[win][k] for r in have if k in r[win]]
            ts = np.array([c["target_share"] for c in cells], dtype=float)
            nt = np.array([c["net_per_trade"] for c in cells], dtype=float)
            m = me[win][k]
            label = "ruler" if mode == "ruler" else mode[1:] + "t"
            print(f"    {label:>8}{m['target_share']:11.3f}"
                  f"{pctile(ts, m['target_share']):8.1f}"
                  f"{m['net_per_trade']:12.2f}{pctile(nt, m['net_per_trade']):8.1f}")


def levels():
    rows = _load("level_depth.json")
    if not rows:
        return
    me = [r for r in rows if r["day"] == DAY]
    if not me:
        return
    me = me[0]
    print(f"\n  --- level penetration ({len(rows)} sessions) --- "
          f"{me['n_excursions']} excursions, {me['n_levels_in_play']} levels in play")
    print(f"    {'metric':<22}{'day':>9}{'corpus med':>12}{'pctile':>8}")
    for k in ("median_depth_t", "p75_depth_t", "held_10t",
              "held_quarter_ruler", "depth_over_ruler"):
        a = np.array([r[k] for r in rows], dtype=float)
        print(f"    {k:<22}{me[k]:9.3f}{np.median(a):12.3f}{pctile(a, me[k]):8.1f}")


if __name__ == "__main__":
    print(f"=== {DAY} ===")
    base_rate()
    levels()
