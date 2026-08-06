"""Stop-enforced 5m ORB re-cut by vol regime (datr_pctl60 terciles).

The scouting doc's surviving IB/ORB shape (strategy-scouting-2026-07 §2):
enter the 09:35 close in the first 5m candle's direction, stop at that candle's
opposite extreme ENFORCED intraday on minute bars, exit at the session close.
A stopped day is exactly −1R. The unenforced Zarattini read concentrated its R
on hot days (ib_by_regime.py) — this checks whether the *enforced* shape does
too, which is the actual "entry-time trend proxy" question, plus the 2026-only
vintage check. Read-only; tick cache via the same minute_bars path as ib.py.
"""
from datetime import date, time

import numpy as np
import pandas as pd

from src.journal.sim import ticks as tickmod
from src.journal.sim.regime import minute_bars
from src.journal.config import ET_TZ

ROOT = "/home/afahmi/repos/atas_journal"
W_END = time(9, 35)

rows = []
for day in tickmod.session_dates(date(2025, 2, 1), date(2026, 6, 30)):
    contract = tickmod.contract_for_cached("NQ", day)
    rth = tickmod.cached_rth(contract, day) if contract else None
    if rth is None or rth.empty:
        continue
    b = minute_bars(rth)
    b = b.assign(_et=b["ts_utc"].dt.tz_convert(ET_TZ).dt.time)
    w = b[b["_et"] < W_END]
    post = b[b["_et"] >= W_END]
    if w.empty or post.empty:
        continue
    o, c = float(w["open"].iloc[0]), float(w["close"].iloc[-1])
    hi, lo = float(w["high"].max()), float(w["low"].min())
    d = 1 if c > o else -1 if c < o else 0
    if d == 0:
        continue
    stop = lo if d == 1 else hi
    stop_dist = (c - lo) if d == 1 else (hi - c)
    if stop_dist <= 0:
        continue
    if d == 1:
        stopped = bool((post["low"].to_numpy() <= stop).any())
    else:
        stopped = bool((post["high"].to_numpy() >= stop).any())
    r = -1.0 if stopped else (float(post["close"].iloc[-1]) - c) * d / stop_dist
    rows.append({"day": day.isoformat(), "dir": d, "stop_dist": stop_dist,
                 "stopped": stopped, "r": r})

df = pd.DataFrame(rows)
atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
df = df.merge(atr[["session", "terc"]], left_on="day", right_on="session",
              how="inner").dropna(subset=["terc"])
df["date"] = pd.to_datetime(df["day"])
df["half"] = np.where(df["date"] < df["date"].median(), "H1", "H2")
df["month"] = df["date"].dt.to_period("M")
df["yr"] = df["date"].dt.year

print(f"sessions traded: {len(df)}  pooled avgR {df.r.mean():+.3f}  "
      f"win {(df.r > 0).mean():.1%}  totalR {df.r.sum():+.1f}  "
      f"stopped {(df.stopped).mean():.0%}")
print("(doc replication check: 2025-02→2026-01 subset:",
      f"avgR {df.loc[df.date < '2026-02', 'r'].mean():+.3f},",
      f"n={int((df.date < '2026-02').sum())})")

print("\n== stop-enforced ORB by tercile ==")
for t in ["quiet", "mid", "hot"]:
    s = df[df.terc == t]
    wins = tot = 0
    for m, gg in s.groupby("month"):
        if len(gg) >= 3:
            tot += 1
            wins += gg.r.mean() > 0
    h1, h2 = (s.loc[s.half == hh, "r"].mean() for hh in ["H1", "H2"])
    pos = s.loc[s.r > 0, "r"].sort_values(ascending=False)
    top5 = pos.head(5).sum() / pos.sum() if len(pos) else np.nan
    print(f"  {t:6s} n={len(s):3d}  avgR {s.r.mean():+.3f}  totalR {s.r.sum():+6.1f}  "
          f"win {(s.r > 0).mean():.0%}  months+ {wins}/{tot}  "
          f"halves {h1:+.2f}/{h2:+.2f}  top5-share {top5:.0%}")

print("\n== hot tercile by year (vintage check) ==")
for yr, s in df[df.terc == "hot"].groupby("yr"):
    print(f"  {yr}: n={len(s):3d}  avgR {s.r.mean():+.3f}  totalR {s.r.sum():+.1f}  "
          f"win {(s.r > 0).mean():.0%}")

print("\n== 2026-only, all terciles ==")
for t in ["quiet", "mid", "hot"]:
    s = df[(df.terc == t) & (df.yr == 2026)]
    print(f"  {t:6s} n={len(s):3d}  avgR {s.r.mean():+.3f}  totalR {s.r.sum():+.1f}")

print("\n== monthly avgR, hot tercile ==")
hm = df[df.terc == "hot"].groupby("month").agg(n=("r", "size"), avg=("r", "mean"))
print(hm[hm.n >= 3].to_string(float_format=lambda x: f"{x:+.2f}"))
