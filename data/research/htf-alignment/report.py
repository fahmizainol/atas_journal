"""Report over trades.parquet (see audit.py). Points per contract, gross.

Uncertainty is a day-clustered bootstrap: trades on one day share a tape, so
resampling trades would overstate n.
"""

from pathlib import Path

import numpy as np
import pandas as pd

df = pd.read_parquet(Path(__file__).with_name("trades.parquet"))
rng = np.random.default_rng(7)
COHORTS = ["prop", "live", "atas_replay", "app_replay"]


def cell(g: pd.DataFrame) -> str:
    if len(g) == 0:
        return f"{'—':>28}"
    return f"n={len(g):>4} win={g.win.mean()*100:4.0f}% pts={g.pts.mean():+6.2f}"


def boot_diff(a: pd.DataFrame, b: pd.DataFrame, reps: int = 2000):
    """Day-clustered bootstrap CI for mean(a.pts) - mean(b.pts)."""
    both = pd.concat([a.assign(_g="a"), b.assign(_g="b")])
    days = both["day"].unique()
    by_day = {d: g for d, g in both.groupby("day")}
    out = []
    for _ in range(reps):
        s = pd.concat([by_day[d] for d in rng.choice(days, len(days))])
        ma, mb = s.loc[s._g == "a", "pts"].mean(), s.loc[s._g == "b", "pts"].mean()
        out.append(ma - mb)
    lo, hi = np.nanpercentile(out, [2.5, 97.5])
    return a.pts.mean() - b.pts.mean(), lo, hi


def section(title):
    print(f"\n## {title}")


section("1. Trade direction vs 5m+15m trend")
for c in COHORTS:
    g = df[df.cohort == c]
    print(f"{c:12} days={g.day.nunique():>3}")
    for al in ["with", "mixed", "against"]:
        print(f"   {al:8} {cell(g[g["align"] == al])}  share={np.mean(g["align"] == al)*100:3.0f}%")
    d, lo, hi = boot_diff(g[g["align"] == "with"], g[g["align"] == "against"])
    print(f"   with-against = {d:+.2f} pts  95% CI [{lo:+.2f}, {hi:+.2f}]")

section("2. Your scenario: 30s trend opposite the HTF trend")
print("   'faded'  = traded WITH the 30s, AGAINST the HTF (short a 30s rollover in an HTF uptrend)")
print("   'joined' = traded AGAINST the 30s, WITH the HTF (long the 30s pullback in an HTF uptrend)")
opp = df[df.htf.isin(["up", "down"]) & df.ltf.isin(["up", "down"]) & (df.htf != df.ltf)]
for c in COHORTS:
    g = opp[opp.cohort == c]
    faded, joined = g[g["align"] == "against"], g[g["align"] == "with"]
    print(f"{c:12} faded  {cell(faded)}")
    print(f"{'':12} joined {cell(joined)}")
    if len(faded) > 5 and len(joined) > 5:
        d, lo, hi = boot_diff(joined, faded)
        print(f"{'':12} joined-faded = {d:+.2f} pts  95% CI [{lo:+.2f}, {hi:+.2f}]")

section("3. Same split, 30s AGREES with HTF (the easy case)")
agree = df[df.htf.isin(["up", "down"]) & (df.htf == df.ltf)]
for c in COHORTS:
    g = agree[agree.cohort == c]
    print(f"{c:12} with   {cell(g[g["align"] == 'with'])}")
    print(f"{'':12} against{cell(g[g["align"] == 'against'])}")

section("4. Confound: is 'against' just the sub-30s leak?")
for c in COHORTS:
    g = df[df.cohort == c]
    for fast, lab in [(True, "<30s"), (False, ">=30s")]:
        h = g[(g.duration_s < 30) == fast]
        print(f"{c:12} {lab:5} with {cell(h[h["align"] == 'with'])} | against {cell(h[h["align"] == 'against'])}")

section("5. Split halves (by day) — does the with/against gap replicate?")
for c in COHORTS:
    g = df[df.cohort == c].sort_values("entry_ts_utc")
    days = np.sort(g.day.unique())
    for half, ds in [("H1", days[: len(days) // 2]), ("H2", days[len(days) // 2:])]:
        h = g[g.day.isin(ds)]
        w, a = h[h["align"] == "with"].pts.mean(), h[h["align"] == "against"].pts.mean()
        print(f"{c:12} {half} with={w:+6.2f} against={a:+6.2f} gap={w - a:+6.2f}  (n={len(h)})")

section("6. Which frame carries it? 5m alone vs 15m alone (trade dir vs frame)")
for c in COHORTS:
    g = df[df.cohort == c]
    side = np.where(g.direction == "Long", "up", "down")
    for fr in ["htf5", "htf15", "ltf"]:
        w = g[g[fr] == side]
        a = g[(g[fr] != side) & (g[fr] != "flat")]
        print(f"{c:12} {fr:5} with {cell(w)} | against {cell(a)}")

section("7. With-HTF trades by distance to the 5m EMA20 (ticks, on the trade's side)")
w = df[df["align"] == "with"].copy()
w["dist"] = np.where(w.direction == "Long", w.d5ema_t, -w.d5ema_t)  # + = entry on the trend side of the EMA
bins = [-np.inf, 0, 20, 40, 80, np.inf]
labels = ["through it", "0-20t", "20-40t", "40-80t", ">80t"]
w["band"] = pd.cut(w.dist, bins, labels=labels)
for c in COHORTS:
    g = w[w.cohort == c]
    print(f"{c:12} " + " | ".join(f"{b}: n={len(g[g.band == b])} {g[g.band == b].pts.mean():+.1f}" for b in labels))
