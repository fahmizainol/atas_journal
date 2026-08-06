"""Tie the NY-band × 9/20-EMA state to the actual upper-band-bounce trade run.

Joins every entry in the a348d176 run (the gate-robustness scorecard baseline,
262 trades, v13 stack) to the EMA state on the minute of entry, then asks the
one question that matters: does 9/20-EMA state at entry separate winners from
losers on trades the engine actually took?

    Usage: .venv/bin/python data/research/market-structure/nyema_trades.py [run_id]
"""
import sys
sys.path.insert(0, "src")
import numpy as np
import pandas as pd

OUTDIR = "data/research/market-structure"
SLUG = "vwap-upper-band-bounce"
DEFAULT_RUN = "20250201-20260630-v13-a348d176"


def main():
    rid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN
    from journal.sim import store
    res = store.read_run(SLUG, rid)
    if res is None:
        print(f"run {rid} not found"); return
    _cfg, tr, _met = res
    tr = tr.copy()
    tr["session"] = tr["session"].astype(str)
    tr["ts_min"] = pd.to_datetime(tr["entry_ts_utc"], utc=True).dt.floor("1min")

    feat = pd.read_parquet(f"{OUTDIR}/nyema_minutes.parquet")
    feat["ts_utc"] = pd.to_datetime(feat["ts_utc"], utc=True)
    cols = ["ema_gap", "ema9_slope", "ema20_slope", "d_ema9_up1", "d_px_up1",
            "d_px_up1_sig", "stretch9", "minute_idx"]
    j = tr.merge(feat[["ts_utc"] + cols], left_on="ts_min", right_on="ts_utc", how="left")

    have = j.dropna(subset=["ema_gap"])
    print(f"run {rid}: {len(tr)} trades, {len(have)} joined to EMA state "
          f"({len(tr)-len(have)} unmatched — entry before warmup / off-grid)")
    if have.empty:
        return

    win = have["r_multiple"] > 0
    print(f"\noverall: win-rate {win.mean()*100:.1f}%  "
          f"mean R {have['r_multiple'].mean():.3f}  net ${have['net_pnl'].sum():,.0f}")

    def cut(name, mask):
        a = have[mask]; b = have[~mask]
        if len(a) == 0 or len(b) == 0:
            print(f"  {name:<26} degenerate split"); return
        print(f"  {name:<26} | TRUE  n={len(a):>3} win={a['r_multiple'].gt(0).mean()*100:>4.0f}% "
              f"R={a['r_multiple'].mean():>6.3f} net=${a['net_pnl'].sum():>8,.0f}"
              f"  || FALSE n={len(b):>3} win={b['r_multiple'].gt(0).mean()*100:>4.0f}% "
              f"R={b['r_multiple'].mean():>6.3f} net=${b['net_pnl'].sum():>8,.0f}")

    print("\n=== EMA state at entry — winner/loser separation ===")
    cut("stacked bull (gap>0)", have["ema_gap"] > 0)
    cut("ema9 rising", have["ema9_slope"] > 0)
    cut("ema20 rising", have["ema20_slope"] > 0)
    cut("stacked & rising", (have["ema_gap"] > 0) & (have["ema9_slope"] > 0))
    cut("ema9 below band (<0)", have["d_ema9_up1"] < 0)
    cut("stretched >median", have["stretch9"] > have["stretch9"].median())

    # correlations with realized R
    print("\n=== rank-corr of EMA features vs realized R (Spearman) ===")
    for c in ["ema_gap", "ema9_slope", "ema20_slope", "d_ema9_up1", "stretch9", "d_px_up1_sig"]:
        sub = have.dropna(subset=[c])
        # Spearman = Pearson on ranks (scipy not installed)
        rho = sub[c].rank().corr(sub["r_multiple"].rank())
        print(f"  {c:<16} spearman ρ vs R = {rho:+.3f}  (n={len(sub)})")

    j.to_parquet(f"{OUTDIR}/nyema_trades.parquet", index=False)
    print(f"\nwrote {OUTDIR}/nyema_trades.parquet")


if __name__ == "__main__":
    main()
