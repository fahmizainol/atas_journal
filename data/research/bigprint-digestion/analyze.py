"""Big-print digestion study — race the video's claims on minutes.parquet.

Measurement (his): signal on minute i -> enter open[i+1], exit open[i+1+N],
direction = side of the minute's biggest print/sweep (B long, A short).
Gross of costs, $20/pt. NQ spread reference: 1 tick = 0.25 pt = $5.

Named cells (pre-registered, everything else is context):
  A. >=100 lots, RTH, horizon curve 1..30 — does the edge build to a 15-20 min
     plateau ("digestion") instead of dying after minute 1?
  B. size bands at h=15 — 70-190 informative, >=200 inverted ("exhaustion")?
  C. wick vs body at h=15 — and does a price-only rejection-bar control
     (big wick, NO big print) carry the same edge? (anchor-bar artifact screen)

    uv run python data/research/bigprint-digestion/analyze.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PT = 20.0                       # $ per NQ point
HORIZONS = [1, 2, 3, 5, 10, 15, 20, 25, 30]
THRESHOLDS = [50, 70, 100, 150, 200, 300]
BANDS = [(20, 50), (50, 70), (70, 100), (100, 150), (150, 200), (200, 300),
         (300, 10_000)]


def load() -> pd.DataFrame:
    df = pd.read_parquet(HERE / "minutes.parquet")
    df["minute"] = pd.to_datetime(df["minute"])
    return df.sort_values(["day", "minute"]).reset_index(drop=True)


def open_lookup(df: pd.DataFrame, seg: str) -> pd.Series:
    sub = df[df["seg"] == seg]
    return pd.Series(sub["open"].to_numpy(),
                     index=pd.MultiIndex.from_arrays([sub["day"], sub["minute"]]))


def race(ev: pd.DataFrame, opens: pd.Series, horizon: int,
         dir_col: str = "dir") -> pd.DataFrame:
    """Attach entry/exit/pnl for one horizon; drops events without both prices."""
    ent_key = pd.MultiIndex.from_arrays(
        [ev["day"], ev["minute"] + pd.Timedelta(minutes=1)])
    ext_key = pd.MultiIndex.from_arrays(
        [ev["day"], ev["minute"] + pd.Timedelta(minutes=1 + horizon)])
    out = ev.copy()
    out["entry"] = opens.reindex(ent_key).to_numpy()
    out["exit"] = opens.reindex(ext_key).to_numpy()
    out = out.dropna(subset=["entry", "exit"])
    out["pnl"] = out[dir_col] * (out["exit"] - out["entry"]) * PT
    return out


def stats(pnl: pd.Series) -> dict:
    n = len(pnl)
    if n == 0:
        return {"n": 0, "avg": np.nan, "t": np.nan, "win": np.nan}
    m, s = pnl.mean(), pnl.std()
    return {"n": n, "avg": round(m, 2),
            "t": round(m / (s / np.sqrt(n)), 2) if s > 0 else np.nan,
            "win": round((pnl > 0).mean() * 100, 1)}


def day_cluster_t(ev: pd.DataFrame) -> tuple[float, int]:
    """t across day-means — the honest t when holds overlap within a day."""
    dm = ev.groupby("day")["pnl"].mean()
    if len(dm) < 3 or dm.std() == 0:
        return np.nan, len(dm)
    return round(dm.mean() / (dm.std() / np.sqrt(len(dm))), 2), len(dm)


def events(df: pd.DataFrame, unit: str, lo: float, hi: float = np.inf,
           seg: str = "rth") -> pd.DataFrame:
    sz, sd = f"{unit}_size", f"{unit}_side"
    ev = df[(df["seg"] == seg) & (df[sz] >= lo) & (df[sz] < hi)].copy()
    ev["dir"] = np.where(ev[sd] == "B", 1.0, -1.0)
    return ev


def main() -> None:
    df = load()
    days = sorted(df["day"].unique())
    print(f"{len(days)} sessions  {days[0]} .. {days[-1]}")
    opens_rth = open_lookup(df, "rth")
    opens_on = open_lookup(df, "on")
    rth = df[df["seg"] == "rth"]

    # ---- 0. baseline: candle direction, all RTH minutes -------------------
    base = rth.copy()
    base["dir"] = np.sign(base["close"] - base["open"])
    base = base[base["dir"] != 0]
    print("\n== baseline: candle-direction race (all RTH minutes) ==")
    for h in [1, 5, 15, 30]:
        r = race(base, opens_rth, h)
        print(f"  h={h:>2}  {stats(r['pnl'])}")

    # ---- grid: unit x threshold x horizon ---------------------------------
    rows = []
    for unit in ("p", "s"):
        for thr in THRESHOLDS:
            ev = events(df, unit, thr)
            for h in HORIZONS:
                r = race(ev, opens_rth, h)
                rows.append({"unit": unit, "thr": thr, "h": h, **stats(r["pnl"])})
    grid = pd.DataFrame(rows)
    grid.to_csv(HERE / "grid.csv", index=False)

    # ---- A. digestion curve ------------------------------------------------
    print("\n== A. horizon curve, >=100 lots, RTH (his: plateau 13-23, peak ~19) ==")
    for unit, name in (("p", "print"), ("s", "sweep")):
        sub = grid[(grid.unit == unit) & (grid.thr == 100)]
        line = "  ".join(f"h{int(r.h)}:{r.avg:+.0f}$" for r in sub.itertuples())
        print(f"  {name:>5} >=100: {line}")
        ev15 = race(events(df, unit, 100), opens_rth, 15)
        ct, nd = day_cluster_t(ev15)
        s15 = stats(ev15["pnl"])
        print(f"         h=15 detail: n={s15['n']}  avg=${s15['avg']}  t={s15['t']}"
              f"  day-cluster t={ct} over {nd} days  win={s15['win']}%")

    # ---- B. size bands ------------------------------------------------------
    print("\n== B. size bands (his: <50 nothing, 70-190 edge, >=200 inverts) ==")
    for h in (1, 15):
        print(f"  h={h}:")
        for unit, name in (("p", "print"), ("s", "sweep")):
            parts = []
            for lo, hi in BANDS:
                r = race(events(df, unit, lo, hi), opens_rth, h)
                st = stats(r["pnl"])
                parts.append(f"[{lo},{hi if hi < 10_000 else '∞'}) "
                             f"n={st['n']} ${st['avg']}")
            print(f"    {name:>5}: " + "  ".join(parts))

    # ---- split-half on the named cell --------------------------------------
    print("\n== split-half (sessions), >=100 @ h=15 ==")
    mid = days[len(days) // 2]
    for unit, name in (("p", "print"), ("s", "sweep")):
        ev = race(events(df, unit, 100), opens_rth, 15)
        a, b = ev[ev["day"] < mid], ev[ev["day"] >= mid]
        print(f"  {name:>5}: first-half {stats(a['pnl'])}   "
              f"second-half {stats(b['pnl'])}   (split at {mid})")

    # ---- C. wick vs body + price-only control ------------------------------
    print("\n== C. wick vs body, sweeps >=100 @ h=15 (his: wick $34 vs body $8) ==")
    ev = events(df, "s", 100)
    body_hi = np.maximum(ev["open"], ev["close"])
    body_lo = np.minimum(ev["open"], ev["close"])
    up_w = ev["s_price"] > body_hi
    dn_w = ev["s_price"] < body_lo
    for label, mask in (("wick", up_w | dn_w), ("body", ~(up_w | dn_w))):
        r = race(ev[mask], opens_rth, 15)
        print(f"  whale {label:>4}: {stats(r['pnl'])}")
    # rejection-agreement split inside the wick cohort
    rej_dir = np.where(dn_w, 1.0, np.where(up_w, -1.0, 0.0))
    agree = ev["dir"] == rej_dir
    for label, mask in (("wick+agree", (up_w | dn_w) & agree),
                        ("wick+fight", (up_w | dn_w) & ~agree)):
        r = race(ev[mask], opens_rth, 15)
        print(f"  {label:>10}: {stats(r['pnl'])}")

    print("\n-- price-only control: dominant-wick bar, NO big print (<50), h=15,")
    print("   direction = rejection (lower wick -> long) --")
    c = rth.copy()
    rng = (c["high"] - c["low"]).replace(0, np.nan)
    ubody, lbody = np.maximum(c["open"], c["close"]), np.minimum(c["open"], c["close"])
    upfrac, dnfrac = (c["high"] - ubody) / rng, (lbody - c["low"]) / rng
    quiet = (c["p_size"].fillna(0) < 50) & (c["s_size"].fillna(0) < 50)
    ctl = c[quiet & ((upfrac >= 0.4) | (dnfrac >= 0.4))].copy()
    ctl["dir"] = np.where(dnfrac[ctl.index] >= 0.4, 1.0, -1.0)
    r = race(ctl, opens_rth, 15)
    ct, nd = day_cluster_t(r)
    print(f"  control: {stats(r['pnl'])}  day-cluster t={ct} over {nd} days")

    # ---- overnight sanity cut ----------------------------------------------
    print("\n== overnight (his: predicts nothing), >=100 @ h=15 ==")
    for unit, name in (("p", "print"), ("s", "sweep")):
        r = race(events(df, unit, 100, seg="on"), opens_on, 15)
        print(f"  {name:>5} ON: {stats(r['pnl'])}")


if __name__ == "__main__":
    main()
