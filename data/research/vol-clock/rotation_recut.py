"""Rotation re-cut (vol-clock follow-up, 2026-08-03).

The one live lead from the vol-clock study: UB's edge concentrates on quiet
days, DTF's on mid/hot — opposite habitats on the two adopted strategies.
Before any A/B, the discipline (weekly-VWAP lesson) is:

  1. re-cut the lean on the CURRENT pinned baselines (UB v13 a348d176 was
     already current; DTF's pin is v1 63c78056 while the vol-clock cut used the
     v2 entry-reason-adoption config 523f4000 — cut BOTH);
  2. window-length sensitivity on the 60-session percentile (40/90) as a
     ROBUSTNESS CHECK, not a search — the lean was measured at 60 pre-committed;
  3. the rotation portfolio itself, measured EXACTLY: sessions are engine-
     independent (session-parallel runner, no cross-day state), so a day-level
     on/off is a faithful re-simulation, unlike trade-level gates.
"""
import math

import pandas as pd

ROOT = "/home/afahmi/repos/atas_journal"
RUNS = {
    "UB": ("data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176", "quiet"),
    "DTFv1": ("data/sims/drift-touch-fade/20250203-20260630-v1-63c78056", "midhot"),
    "DTFv2": ("data/sims/drift-touch-fade/20250203-20260630-v2-523f4000", "midhot"),
}
WINDOWS = {40: 13, 60: 20, 90: 30}  # window -> min_periods (~1/3)

atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["session"] = pd.to_datetime(atr["session"]).dt.date.astype(str)


def pctl_col(w, mp):
    return atr["daily_atr14"].rolling(w, min_periods=mp).apply(
        lambda x: (x.iloc[:-1] <= x.iloc[-1]).mean(), raw=False)


for w, mp in WINDOWS.items():
    atr[f"p{w}"] = pctl_col(w, mp)
    atr[f"terc{w}"] = pd.cut(atr[f"p{w}"], [-0.01, 1 / 3, 2 / 3, 1.01],
                             labels=["quiet", "mid", "hot"])

trades = {}
for key, (path, _) in RUNS.items():
    t = pd.read_parquet(f"{ROOT}/{path}/trades.parquet")
    t["session"] = t["session"].astype(str)
    t = t.merge(atr[["session"] + [f"terc{w}" for w in WINDOWS]], on="session", how="left")
    trades[key] = t


def pf(x):
    g, l = x[x > 0].sum(), -x[x < 0].sum()
    return g / l if l else float("inf")


def welch_p(a, b):
    ma, mb, va, vb, na, nb = a.mean(), b.mean(), a.var(ddof=1), b.var(ddof=1), len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return 1.0
    from statistics import NormalDist
    return 2 * (1 - NormalDist().cdf(abs(ma - mb) / se))


print("== 1+2. Lean by window (focus vs rest, avgR on net R; monthly sign = months focus wins, min 3 trades/side) ==")
for key, (path, habitat) in RUNS.items():
    t = trades[key]
    for w in WINDOWS:
        tc = f"terc{w}"
        d = t.dropna(subset=[tc])
        focus = d[tc] == "quiet" if habitat == "quiet" else d[tc].isin(["mid", "hot"])
        f, r = d[focus], d[~focus]
        # monthly sign
        d2 = d.assign(month=d["session"].str[:7], focus=focus)
        wins = tot = 0
        for m, g in d2.groupby("month"):
            gf, gr = g[g.focus], g[~g.focus]
            if len(gf) >= 3 and len(gr) >= 3:
                tot += 1
                wins += gf.r_multiple.mean() > gr.r_multiple.mean()
        # split-half of the lean
        halves = []
        d2 = d2.sort_values("session")
        for h in (d2.iloc[:len(d2) // 2], d2.iloc[len(d2) // 2:]):
            hf, hr = h[h.focus], h[~h.focus]
            halves.append(hf.r_multiple.mean() - hr.r_multiple.mean())
        print(f"{key:6} w{w:2}  focus n={len(f):3} avgR {f.r_multiple.mean():+.2f} PF {pf(f.net_pnl):4.2f} | "
              f"rest n={len(r):3} avgR {r.r_multiple.mean():+.2f} PF {pf(r.net_pnl):4.2f} | "
              f"dR {f.r_multiple.mean() - r.r_multiple.mean():+.2f} p={welch_p(f.r_multiple, r.r_multiple):.3f} | "
              f"months {wins}/{tot} | halves dR {halves[0]:+.2f}/{halves[1]:+.2f}")
    print()

print("== 3. Rotation portfolio (exact day-filter; label = w60; DTF arm = v2 adopted config) ==")


def daily(t, mask=None):
    d = t if mask is None else t[mask]
    return d.groupby("session").net_pnl.sum()


def stats(dp, ntr):
    idx = sorted(set().union(*[set(x.index) for x in dp]))
    eq = pd.Series(0.0, index=idx)
    for x in dp:
        eq = eq.add(x, fill_value=0.0)
    cum = eq.cumsum()
    dd = (cum - cum.cummax()).min()
    sharpe = eq.mean() / eq.std() * math.sqrt(252) if eq.std() else 0
    mo = eq.groupby(eq.index.str[:7]).sum()
    return f"net ${cum.iloc[-1]:>9,.0f}  maxDD ${dd:>8,.0f}  Sharpe {sharpe:4.2f}  months+ {(mo > 0).sum():2}/{len(mo)}  trades {ntr}"


ub, dtf = trades["UB"].dropna(subset=["terc60"]), trades["DTFv2"].dropna(subset=["terc60"])
ub_q = ub["terc60"] == "quiet"
dtf_mh = dtf["terc60"].isin(["mid", "hot"])
ports = {
    "A always-both        ": ([daily(ub), daily(dtf)], len(ub) + len(dtf)),
    "B hard rotation      ": ([daily(ub, ub_q), daily(dtf, dtf_mh)], ub_q.sum() + dtf_mh.sum()),
    "C UB always+DTF mh   ": ([daily(ub), daily(dtf, dtf_mh)], len(ub) + dtf_mh.sum()),
    "D UB quiet+DTF always": ([daily(ub, ub_q), daily(dtf)], ub_q.sum() + len(dtf)),
}
for name, (dp, n) in ports.items():
    print(f"{name} {stats(dp, n)}")

print("\n-- same, DTF arm = v1 pinned --")
dtf1 = trades["DTFv1"].dropna(subset=["terc60"])
d1_mh = dtf1["terc60"].isin(["mid", "hot"])
for name, (dp, n) in {
    "A always-both        ": ([daily(ub), daily(dtf1)], len(ub) + len(dtf1)),
    "B hard rotation      ": ([daily(ub, ub_q), daily(dtf1, d1_mh)], ub_q.sum() + d1_mh.sum()),
}.items():
    print(f"{name} {stats(dp, n)}")

print("\n== label agreement across windows (sessions where terc40/60/90 disagree on quiet-vs-not) ==")
lab = atr.dropna(subset=["terc40", "terc60", "terc90"])
q = pd.DataFrame({w: lab[f"terc{w}"] == "quiet" for w in WINDOWS})
print(f"n={len(lab)}  all-agree {(q.nunique(axis=1) == 1).mean():.0%}  "
      f"w40 vs w60 agree {(q[40] == q[60]).mean():.0%}  w90 vs w60 agree {(q[90] == q[60]).mean():.0%}")
