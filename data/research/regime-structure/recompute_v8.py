"""Force-build the v8 regime artifacts for every cached session, then print the
distributions the texture threshold gets calibrated on.

Usage: .venv/bin/python data/research/regime-structure/recompute_v8.py
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from journal.sim import regime as regmod  # noqa: E402


def sessions():
    out = []
    for p in sorted(Path("data/cache/ticks").glob("*_rth.parquet")):
        sym, d, _ = p.stem.split("_")
        out.append((sym, date.fromisoformat(d)))
    out.sort(key=lambda x: x[1])
    return out


def main():
    rows = []
    t0 = time.time()
    sess = sessions()
    for i, (sym, d) in enumerate(sess):
        r = regmod.get_regime(sym, d)
        if r is None:
            continue
        eod = r["checkpoints"]["eod"]
        rows.append({
            "session": d.isoformat(), "sym": sym, "partial": r["partial"],
            "class": r["class"], "texture": r["texture"],
            **{k: eod.get(k) for k in (
                "st_bias", "st_bias_age_min", "st_bias_share", "st_break_rate",
                "st_bos_share", "st_choch_rate", "chop_occ_30m", "chop_occ_rth")},
        })
        if i % 40 == 0:
            print(f"{i + 1}/{len(sess)}  {time.time() - t0:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet("data/research/regime-structure/eod_structure_v8.parquet")
    print(f"\nWROTE eod_structure_v8.parquet  {df.shape}  {time.time() - t0:.0f}s")

    full = df[~df["partial"]]
    print(f"\nsessions={len(df)}  full={len(full)}")
    print("\n=== chop_occ_rth (eod) distribution — texture calibration ===")
    print(full["chop_occ_rth"].describe(percentiles=[.1, .25, .33, .5, .66, .75, .9])
          .round(4).to_string())
    print("\n=== structure KPI coverage (non-null, full days) ===")
    for k in ("st_bias", "st_bias_share", "st_break_rate", "st_bos_share",
              "st_choch_rate", "chop_occ_30m", "chop_occ_rth"):
        print(f"  {k:16s} {full[k].notna().mean():.2%}")
    print("\n=== eod KPI describe (full days) ===")
    print(full[["st_bias_age_min", "st_bias_share", "st_break_rate",
                "st_bos_share", "st_choch_rate", "chop_occ_rth"]]
          .describe().round(3).to_string())
    print("\n=== class x texture day counts ===")
    print(full.groupby(["class", "texture"]).size().unstack(fill_value=0).to_string())
    print("\n=== chop_occ_rth by class (does texture just re-read the class?) ===")
    print(full.groupby("class")["chop_occ_rth"].describe()[["count", "mean", "std"]]
          .round(4).to_string())
    print("\n=== spearman: chop_occ_rth vs the other structure KPIs ===")
    sub = full[["chop_occ_rth", "st_choch_rate", "st_break_rate", "st_bias_share",
                "st_bias_age_min"]].dropna()
    print(sub.corr(method="spearman").round(3)["chop_occ_rth"].to_string())


if __name__ == "__main__":
    main()
