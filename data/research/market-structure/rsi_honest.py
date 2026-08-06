"""NY-band × RSI — the honesty pass (per the ny-band-ema920 §5.1/§7 lesson).

Three checks on the first-pass leads:
  1. Trade tie-in measured at acceptance_ts (the decision point) instead of the
     fill minute — does the rsi14 low-tertile drag survive?
  2. Collinearity: is entry-RSI just the band's σ-depth / stretch axis renamed?
  3. Divergence control: among higher-high touch pairs, is the divergent-vs-
     confirming gap anything beyond current RSI level? Plus split-half stability
     of the event-level monotonicity.

    Usage: .venv/bin/python data/research/market-structure/rsi_honest.py
"""
import sys
sys.path.insert(0, "src")
import numpy as np
import pandas as pd

OUTDIR = "data/research/market-structure"
SLUG = "vwap-upper-band-bounce"
RUN = "20250201-20260630-v13-a348d176"


def sp(a, b):
    m = a.notna() & b.notna()
    return a[m].rank().corr(b[m].rank())


def main():
    # ---------- 1+2: trades ----------
    from journal.sim import store
    _c, tr, _m = store.read_run(SLUG, RUN)
    tr = tr.copy()
    tr["ts_fill"] = pd.to_datetime(tr["entry_ts_utc"], utc=True).dt.floor("1min")
    tr["ts_acc"] = pd.to_datetime(tr["acceptance_ts"], utc=True).dt.floor("1min")

    rsi = pd.read_parquet(f"{OUTDIR}/rsi_minutes.parquet")
    rsi["ts_utc"] = pd.to_datetime(rsi["ts_utc"], utc=True)
    ema = pd.read_parquet(f"{OUTDIR}/nyema_minutes.parquet")
    ema["ts_utc"] = pd.to_datetime(ema["ts_utc"], utc=True)

    for tag, key in [("fill", "ts_fill"), ("acc", "ts_acc")]:
        tr = tr.merge(rsi[["ts_utc", "rsi14", "rsi2"]].rename(
            columns={"rsi14": f"rsi14_{tag}", "rsi2": f"rsi2_{tag}"}),
            left_on=key, right_on="ts_utc", how="left").drop(columns="ts_utc")
    tr = tr.merge(ema[["ts_utc", "d_px_up1_sig", "stretch9"]],
                  left_on="ts_fill", right_on="ts_utc", how="left").drop(columns="ts_utc")

    have = tr.dropna(subset=["rsi14_fill"])
    print(f"{len(tr)} trades, rsi@fill joined {len(have)}, "
          f"rsi@acceptance joined {tr['rsi14_acc'].notna().sum()}")

    print("\n=== 1. rsi14 vs R, fill vs acceptance measurement ===")
    for tag in ["fill", "acc"]:
        c = f"rsi14_{tag}"
        sub = tr.dropna(subset=[c])
        print(f"  rsi14@{tag:<4} spearman ρ vs R = {sp(sub[c], sub['r_multiple']):+.3f} (n={len(sub)})")
        t = sub.copy()
        t["bin"] = pd.qcut(t[c], 3, labels=["low", "mid", "high"])
        for b, g in t.groupby("bin", observed=True):
            print(f"    tertile {b:<5} (med {g[c].median():>4.0f})  n={len(g):>3}  "
                  f"win={g['r_multiple'].gt(0).mean()*100:>4.0f}%  "
                  f"R={g['r_multiple'].mean():>6.3f}  net=${g['net_pnl'].sum():>8,.0f}")

    print("\n=== 2. collinearity of entry-RSI with the band axes (fill minute) ===")
    for a, b in [("rsi14_fill", "d_px_up1_sig"), ("rsi14_fill", "stretch9"),
                 ("rsi14_acc", "d_px_up1_sig"), ("rsi2_fill", "d_px_up1_sig")]:
        print(f"  ρ({a}, {b}) = {sp(tr[a], tr[b]):+.3f}")
    # does rsi14 add anything once σ-depth is partialled out? rank-residual check
    sub = tr.dropna(subset=["rsi14_fill", "d_px_up1_sig"])
    rr = sub["rsi14_fill"].rank(); dd = sub["d_px_up1_sig"].rank(); yy = sub["r_multiple"].rank()
    res_r = rr - np.polyval(np.polyfit(dd, rr, 1), dd)
    res_y = yy - np.polyval(np.polyfit(dd, yy, 1), dd)
    print(f"  partial ρ(rsi14, R | σ-depth) = {pd.Series(res_r).corr(pd.Series(res_y)):+.3f} (n={len(sub)})")

    # ---------- 3: events — divergence vs level, split-half ----------
    ev = pd.read_parquet(f"{OUTDIR}/rsi_events.parquet")
    hh = pd.read_parquet(f"{OUTDIR}/rsi_divergence_pairs.parquet")
    print("\n=== 3. divergence gap controlled for current RSI level ===")
    hh = hh.dropna(subset=["rsi14", "prev_rsi14", "cont_to_up2"]).copy()
    hh["div"] = hh["rsi14"] < hh["prev_rsi14"]
    hh["lvl"] = pd.qcut(hh["rsi14"], 3, labels=["low", "mid", "high"])
    for b, g in hh.groupby("lvl", observed=True):
        d = g[g["div"]]; c = g[~g["div"]]
        print(f"  rsi level {b:<5}: divergent {d['cont_to_up2'].mean()*100:>5.1f}% (n={len(d):>3})  "
              f"confirming {c['cont_to_up2'].mean()*100:>5.1f}% (n={len(c):>3})")

    print("\n=== split-half stability (event-level monotonicity) ===")
    sess = sorted(ev["session"].unique())
    halves = {"H1": set(sess[:len(sess)//2]), "H2": set(sess[len(sess)//2:])}
    for hname, hset in halves.items():
        e = ev[ev["session"].isin(hset)].dropna(subset=["rsi14"]).copy()
        e["bin"] = pd.qcut(e["rsi14"], 3, labels=["low", "mid", "high"])
        rates = {b: g["cont_to_up2"].mean() for b, g in e.groupby("bin", observed=True)}
        print(f"  {hname}: " + "  ".join(f"{b}={v*100:.1f}%" for b, v in rates.items()))
        h2 = ev[ev["session"].isin(hset)]
        hp = hh[hh["session"].isin(hset)]
        d = hp[hp["div"]]; c = hp[~hp["div"]]
        print(f"      divergent {d['cont_to_up2'].mean()*100:.1f}% (n={len(d)})  "
              f"vs confirming {c['cont_to_up2'].mean()*100:.1f}% (n={len(c)})")


if __name__ == "__main__":
    main()
