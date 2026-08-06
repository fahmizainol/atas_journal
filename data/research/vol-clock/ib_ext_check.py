"""IB extension levels (0.5×/1×/1.5× IB beyond the break side): tradeable?

Three reads over the full-window IB snapshot + minute bars, by vol tercile:
  1. Continuation ladder — P(reach the next milestone | reached this one).
  2. Hold rate — of days that touch m×, how many CLOSE beyond m× (does an
     extension act as a magnet-then-hold, or is it given back)?
  3. Intraday stall — signed forward move 30m after the FIRST touch of the m×
     level (touch bar excluded per the anchor-bar artifact screen), vs the
     same-day all-post-IB-minute null. If the level is resistance, the touch
     cohort should undershoot the null.
Platform lore says these are "targets"; VAH-snap and stable-level S/R both
died as resistance claims here, so the prior is low. Read-only.
"""
from datetime import date, time

import numpy as np
import pandas as pd

from src.journal.sim import ticks as tickmod
from src.journal.sim.regime import minute_bars
from src.journal.config import ET_TZ

import json

ROOT = "/home/afahmi/repos/atas_journal"
SNAP = f"{ROOT}/data/cache/ib/NQ_20250201-20260630_v1-aada9fa2b02c.json"
MS = [0.5, 1.0, 1.5]
FWD = 30  # minutes

days = pd.DataFrame(json.load(open(SNAP))["days"])
atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
df = days.merge(atr[["session", "terc"]], left_on="day", right_on="session",
                how="inner").dropna(subset=["terc"])

# side-resolved: the break side's extension and the close's extension on it
df["ext"] = df[["ext_up_x", "ext_dn_x"]].max(axis=1)
df["up_side"] = df["ext_up_x"] >= df["ext_dn_x"]
df["close_ext"] = np.where(df.up_side, (df.close - df.ib_high) / df.ib_range,
                           (df.ib_low - df.close) / df.ib_range)

print("== 1. continuation ladder  P(next | this), pooled and by tercile ==")
steps = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
for a, b in steps:
    r = df[df.ext >= a]
    line = f"  {a}→{b}×: pooled {(r.ext >= b).mean():.0%} (n={len(r)})"
    for t in ["quiet", "mid", "hot"]:
        s = r[r.terc == t]
        line += f"   {t} {(s.ext >= b).mean():.0%}({len(s)})"
    print(line)

print("\n== 2. hold rate — touched m×, closed beyond m× ==")
for m in MS:
    r = df[df.ext >= m]
    line = f"  {m}×: pooled {(r.close_ext >= m).mean():.0%} (n={len(r)})"
    for t in ["quiet", "mid", "hot"]:
        s = r[r.terc == t]
        if len(s) >= 8:
            line += f"   {t} {(s.close_ext >= m).mean():.0%}({len(s)})"
    print(line)
print("  give-back: median close_ext / max ext on ≥0.5× days:",
      f"{(df[df.ext >= 0.5].close_ext / df[df.ext >= 0.5].ext).median():.2f}")

print(f"\n== 3. intraday stall — {FWD}m signed move after FIRST m× touch vs day null ==")
lab = df.set_index("day")[["terc", "ib_high", "ib_low", "ib_range", "up_side"]]
res = {m: [] for m in MS}
nulls = []
W_END = time(10, 30)
for d in tickmod.session_dates(date(2025, 2, 1), date(2026, 6, 30)):
    key = d.isoformat()
    if key not in lab.index:
        continue
    row = lab.loc[key]
    contract = tickmod.contract_for_cached("NQ", d)
    rth = tickmod.cached_rth(contract, d) if contract else None
    if rth is None or rth.empty:
        continue
    b = minute_bars(rth)
    b = b.assign(_et=b["ts_utc"].dt.tz_convert(ET_TZ).dt.time).reset_index(drop=True)
    post = b[b["_et"] >= W_END].reset_index(drop=True)
    if len(post) < FWD + 2:
        continue
    sgn = 1.0 if row.up_side else -1.0
    closes = post["close"].to_numpy()
    # day null: mean signed FWD-min move from every eligible post-IB minute
    fw = (closes[FWD:] - closes[:-FWD]) * sgn / row.ib_range
    nulls.append({"day": key, "terc": row.terc, "null": float(np.mean(fw))})
    for m in MS:
        lvl = (row.ib_high + m * row.ib_range if row.up_side
               else row.ib_low - m * row.ib_range)
        hit = (post["high"].to_numpy() >= lvl if row.up_side
               else post["low"].to_numpy() <= lvl)
        idx = np.flatnonzero(hit)
        if not len(idx) or idx[0] + 1 + FWD >= len(post):
            continue
        i = idx[0] + 1  # touch bar excluded — start from the next bar's open path
        mv = (closes[i + FWD] - float(post["open"].iloc[i])) * sgn / row.ib_range
        res[m].append({"day": key, "terc": row.terc, "mv": float(mv)})

nul = pd.DataFrame(nulls).set_index("day")
for m in MS:
    r = pd.DataFrame(res[m])
    if r.empty:
        continue
    r = r.join(nul["null"], on="day")
    d30 = r.mv - r["null"]
    line = (f"  {m}×: n={len(r):3d}  post-touch {r.mv.mean():+.3f}×IB  "
            f"day-null {r['null'].mean():+.3f}  delta {d30.mean():+.3f}  "
            f"(continue if >0)")
    print(line)
    for t in ["quiet", "mid", "hot"]:
        s = r[r.terc == t]
        if len(s) >= 10:
            print(f"       {t:6s} n={len(s):3d}  delta {(s.mv - s['null']).mean():+.3f}")
