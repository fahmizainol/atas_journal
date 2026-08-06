"""Does the Initial Balance behave differently across quiet/mid/hot ATR regimes?

Cuts the 60-min IB snapshot (NQ 2025-02→2026-06, src/journal/sim/ib.py) by the
causal datr_pctl60 tercile. Scale questions (IB range in points) will differ by
construction — the interesting axes are SHAPE: IB as a fraction of the day,
break rates/timing, extension multiples, CBOT day-type mix, break epilogue
(held vs failed), and ib_vs_adr (already self-normalized). Split-half on the
headline contrasts. House-style stats, no scipy.
"""
import json
from math import erf

import numpy as np
import pandas as pd

ROOT = "/home/afahmi/repos/atas_journal"
SNAP = f"{ROOT}/data/cache/ib/NQ_20250201-20260630_v1-aada9fa2b02c.json"

days = pd.DataFrame(json.load(open(SNAP))["days"])
days["first_break_min"] = days["first_break"].map(
    lambda b: b["min_after_open"] if isinstance(b, dict) else np.nan)
days["first_break_up"] = days["first_break"].map(
    lambda b: b["side"] == "up" if isinstance(b, dict) else np.nan)

atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
df = days.merge(atr[["session", "terc"]], left_on="day", right_on="session",
                how="inner")
df = df[df["terc"].notna()].copy()
df["date"] = pd.to_datetime(df["day"])
df["half"] = np.where(df["date"] < df["date"].median(), "H1", "H2")
print(f"sessions: {len(df)}  (q/m/h = {(df.terc=='quiet').sum()}/"
      f"{(df.terc=='mid').sum()}/{(df.terc=='hot').sum()})")


def welch_p(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return np.nan
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    if se == 0:
        return np.nan
    return 2 * (1 - 0.5 * (1 + erf(abs((a.mean() - b.mean()) / se) / np.sqrt(2))))


SCALE = ["ib_range", "day_range", "adr14"]
SHAPE = ["ib_pct_of_day", "ib_vs_adr", "range_x", "max_ext_x",
         "first_break_min", "close_pos"]
BOOLS = ["broke_both", "first_break_up", "close_beyond_break"]

g = df.groupby("terc", observed=True)
print("\n== scale (points — differs by construction, shown for context) ==")
for c in SCALE:
    m = g[c].median()
    print(f"  {c:14s} q/m/h median: {m['quiet']:.0f} / {m['mid']:.0f} / {m['hot']:.0f}")

print("\n== shape KPIs by tercile (mean; quiet-vs-hot Welch p) ==")
rows = []
for c in SHAPE + BOOLS:
    v = df[c].astype(float)
    m = v.groupby(df["terc"], observed=True).mean()
    p = welch_p(v[df.terc == "quiet"], v[df.terc == "hot"])
    rows.append([c, m.get("quiet"), m.get("mid"), m.get("hot"), p])
print(pd.DataFrame(rows, columns=["kpi", "quiet", "mid", "hot", "p_q_vs_h"])
      .to_string(index=False, float_format=lambda x: f"{x:.3f}"))

print("\n== CBOT day-type mix by tercile (row %) ==")
print((pd.crosstab(df["terc"], df["day_type"], normalize="index") * 100)
      .to_string(float_format=lambda x: f"{x:.0f}%"))

print("\n== extension milestones by tercile (share of days reaching ≥X × IB) ==")
for m in (0.5, 1.0, 1.5, 2.0):
    r = (df["max_ext_x"] >= m).groupby(df["terc"], observed=True).mean()
    print(f"  ≥{m}×: quiet {r['quiet']:.0%}  mid {r['mid']:.0%}  hot {r['hot']:.0%}")

print("\n== split-half of any headline contrast (quiet/hot per half) ==")
for c in ["ib_pct_of_day", "range_x", "max_ext_x", "broke_both", "close_beyond_break"]:
    line = []
    for hh in ["H1", "H2"]:
        s = df[df.half == hh]
        line.append(f"{hh}: {s.loc[s.terc=='quiet', c].astype(float).mean():.3f}/"
                    f"{s.loc[s.terc=='hot', c].astype(float).mean():.3f}")
    print(f"  {c:20s} {'  '.join(line)}")

print("\n== ORB 5m Zarattini read by tercile (the surviving IB/ORB shape) ==")
orb = df["orb"].map(lambda o: o.get("5") if isinstance(o, dict) else None)
sub = df[orb.notna()].copy()
sub["orb_dir"] = orb[orb.notna()].map(lambda t: t["dir"])
sub["orb_follow"] = orb[orb.notna()].map(lambda t: t["follow"])
sub["orb_r"] = orb[orb.notna()].map(lambda t: t["r_mult"])
sub = sub[sub["orb_dir"] != 0]
for t in ["quiet", "mid", "hot"]:
    s = sub[sub.terc == t]
    rs = s["orb_r"].dropna().astype(float)
    print(f"  {t:6s} n={len(s):3d}  follow {s['orb_follow'].mean():.0%}  "
          f"avgR {rs.mean():+.2f}  medR {rs.median():+.2f}")
    for hh in ["H1", "H2"]:
        rr = s.loc[s.half == hh, "orb_r"].dropna().astype(float)
        print(f"         {hh}: n={len(rr):3d} avgR {rr.mean():+.2f}")
