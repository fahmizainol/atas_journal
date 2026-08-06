"""Tie RSI state at entry to the actual upper-band-bounce trade run.

Sibling of nyema_trades.py: joins every a348d176 entry to the RSI grid on the
minute of entry and asks whether RSI state separates winners from losers on
trades the engine actually took.

    Usage: .venv/bin/python data/research/market-structure/rsi_trades.py [run_id]
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

    feat = pd.read_parquet(f"{OUTDIR}/rsi_minutes.parquet")
    feat["ts_utc"] = pd.to_datetime(feat["ts_utc"], utc=True)
    cols = ["rsi14", "rsi5", "rsi2", "rsi14_slope"]
    j = tr.merge(feat[["ts_utc"] + cols], left_on="ts_min", right_on="ts_utc", how="left")

    have = j.dropna(subset=["rsi14"])
    print(f"run {rid}: {len(tr)} trades, {len(have)} joined to RSI state "
          f"({len(tr)-len(have)} unmatched)")
    if have.empty:
        return

    win = have["r_multiple"] > 0
    print(f"\noverall: win-rate {win.mean()*100:.1f}%  "
          f"mean R {have['r_multiple'].mean():.3f}  net ${have['net_pnl'].sum():,.0f}")
    print(have[cols].describe().round(1).to_string())

    def cut(name, mask):
        a = have[mask]; b = have[~mask]
        if len(a) == 0 or len(b) == 0:
            print(f"  {name:<26} degenerate split"); return
        print(f"  {name:<26} | TRUE  n={len(a):>3} win={a['r_multiple'].gt(0).mean()*100:>4.0f}% "
              f"R={a['r_multiple'].mean():>6.3f} net=${a['net_pnl'].sum():>8,.0f}"
              f"  || FALSE n={len(b):>3} win={b['r_multiple'].gt(0).mean()*100:>4.0f}% "
              f"R={b['r_multiple'].mean():>6.3f} net=${b['net_pnl'].sum():>8,.0f}")

    print("\n=== RSI state at entry — winner/loser separation ===")
    cut("rsi14 >= 70 (overbought)", have["rsi14"] >= 70)
    cut("rsi14 >= 60", have["rsi14"] >= 60)
    cut("rsi14 < 50", have["rsi14"] < 50)
    cut("rsi14 > median", have["rsi14"] > have["rsi14"].median())
    cut("rsi14 rising (5m slope>0)", have["rsi14_slope"] > 0)
    cut("rsi2 >= 90", have["rsi2"] >= 90)
    cut("rsi5 >= 80", have["rsi5"] >= 80)

    print("\n=== rsi14 tertiles at entry ===")
    t = have.copy()
    t["bin"] = pd.qcut(t["rsi14"], 3, labels=["low", "mid", "high"], duplicates="drop")
    for b, sub in t.groupby("bin", observed=True):
        print(f"  tertile {b:<5} (med {sub['rsi14'].median():>4.0f})  n={len(sub):>3}  "
              f"win={sub['r_multiple'].gt(0).mean()*100:>4.0f}%  "
              f"R={sub['r_multiple'].mean():>6.3f}  net=${sub['net_pnl'].sum():>8,.0f}")

    print("\n=== rank-corr of RSI features vs realized R (Spearman) ===")
    for c in cols:
        sub = have.dropna(subset=[c])
        rho = sub[c].rank().corr(sub["r_multiple"].rank())
        print(f"  {c:<12} spearman ρ vs R = {rho:+.3f}  (n={len(sub)})")

    j.to_parquet(f"{OUTDIR}/rsi_trades.parquet", index=False)
    print(f"\nwrote {OUTDIR}/rsi_trades.parquet")


if __name__ == "__main__":
    main()
