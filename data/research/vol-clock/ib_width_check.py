"""IB range size: does the width of the first hour predict anything?

External lore: narrow IB → range-expansion/trend day. Local prior: the claim
REVERSED on the stopped ORB (narrow = worst width tercile, scouting §2).
Cuts, all read-only over the full-window snapshot + daily_atr + two pinned
baselines' trades:
  A. ib_vs_adr terciles → day outcomes (trend rate, both-break, extension,
     post-IB expansion vs ADR) — the compression→expansion claim directly.
  B. width × vol-regime: is IB width even orthogonal to datr_pctl60?
  C. UB (a348d176) and DTF v2 (523f4000) net/avgR by the day's width tercile —
     re-cut on the current baselines BEFORE anyone proposes an ib_width gate
     A/B (weekly-VWAP lesson).
"""
import json

import numpy as np
import pandas as pd

ROOT = "/home/afahmi/repos/atas_journal"
SNAP = f"{ROOT}/data/cache/ib/NQ_20250201-20260630_v1-aada9fa2b02c.json"

days = pd.DataFrame(json.load(open(SNAP))["days"])
days = days[days.ib_vs_adr.notna()].copy()
atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
df = days.merge(atr[["session", "terc", "datr_pctl60"]], left_on="day",
                right_on="session", how="inner").dropna(subset=["terc"])
df["date"] = pd.to_datetime(df["day"])
df["half"] = np.where(df["date"] < df["date"].median(), "H1", "H2")
lo, hi = df.ib_vs_adr.quantile([1 / 3, 2 / 3])
df["wid"] = pd.cut(df.ib_vs_adr, [-1, lo, hi, 99],
                   labels=["narrow", "midw", "wide"])
df["expansion_adr"] = (df.day_range - df.ib_range) / df.adr14  # post-IB new range
df["day_vs_adr"] = df.day_range / df.adr14
df["trend"] = df.day_type == "trend"
print(f"n={len(df)}  width tercile edges: {lo:.2f} / {hi:.2f} ×ADR14")

print("\n== A. day outcomes by IB-width tercile ==")
g = df.groupby("wid", observed=True)
out = g.agg(n=("day", "size"), trend_rate=("trend", "mean"),
            both_break=("broke_both", "mean"), med_ext=("max_ext_x", "median"),
            expansion=("expansion_adr", "mean"), day_range=("day_vs_adr", "mean"),
            close_dir=("close_pos", "mean"))
print(out.to_string(float_format=lambda x: f"{x:.3f}"))
print("corr(ib_vs_adr, expansion_adr):",
      f"{df.ib_vs_adr.corr(df.expansion_adr):+.3f}",
      " | corr(ib_vs_adr, day_vs_adr):",
      f"{df.ib_vs_adr.corr(df.day_vs_adr):+.3f}")
for c in ["expansion_adr", "day_vs_adr"]:
    print(f"  split-half corr ib_vs_adr↔{c}: " + "  ".join(
        f"{h}: {df[df.half==h].ib_vs_adr.corr(df[df.half==h][c]):+.3f}"
        for h in ["H1", "H2"]))

print("\n== B. width × vol regime ==")
print("corr(ib_vs_adr, datr_pctl60):", f"{df.ib_vs_adr.corr(df.datr_pctl60):+.3f}")
print(pd.crosstab(df.terc, df.wid, normalize="index")
      .to_string(float_format=lambda x: f"{x:.0%}"))
print("expansion_adr by regime×width (mean):")
print(df.pivot_table(index="terc", columns="wid", values="expansion_adr",
                     observed=True).to_string(float_format=lambda x: f"{x:.2f}"))

print("\n== C. live-strategy trades by day's IB-width tercile ==")
wid_map = df.set_index("day")["wid"]
RUNS = {"UB": "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176",
        "DTF": "data/sims/drift-touch-fade/20250203-20260630-v2-523f4000"}
for name, path in RUNS.items():
    t = pd.read_parquet(f"{ROOT}/{path}/trades.parquet")
    t["session"] = t["session"].astype(str)
    t["wid"] = t["session"].map(wid_map)
    t = t.dropna(subset=["wid"])
    t["date"] = pd.to_datetime(t["session"])
    t["half"] = np.where(t["date"] < t["date"].median(), "H1", "H2")
    print(f"  {name}:")
    for w in ["narrow", "midw", "wide"]:
        s = t[t.wid == w]
        if not len(s):
            continue
        halves = [s.loc[s.half == h, "r_multiple"].mean() for h in ["H1", "H2"]]
        print(f"    {w:6s} n={len(s):3d}  net ${s.net_pnl.sum():>9,.0f}  "
              f"avgR {s.r_multiple.mean():+.2f}  win {(s.net_pnl > 0).mean():.0%}  "
              f"halves {halves[0]:+.2f}/{halves[1]:+.2f}")
