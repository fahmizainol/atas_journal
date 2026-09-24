"""Stage 2: does the SPEED of the bounce off dev1 predict anything?

Reads the windowed path features from extract.py and asks, in order:

  1. Descriptive — when is the heat actually taken, and how fast is the
     climb-out when it happens.
  2. Raw read — velocity of winners vs losers. Contaminated by construction
     (a trade that never climbs out has no climb-out speed), reported only to
     show the size of the contamination.
  3. The causal read — control for POSITION at the window's close, then ask
     whether speed adds anything. Strata + a residualised (partial) Spearman,
     the same screen the vol-clock study used.
  4. The only tradeable shape — a cut rule at the window ("not back at the
     level by W" / "not +N ticks by W"), priced against holding.

Every read is repeated on a second run and on a date split-half.

    python data/research/bounce-velocity/analyze.py
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import numpy as np
import pandas as pd
from scipy import stats

HERE = Path("data/research/bounce-velocity")
RUNS = {
    "A (v15 baseline 2024-03→2026-06)": "20240303-20260630-v15-95473ae3",
    "B (v13 2025-02→2026-06)": "20250201-20260630-v13-9760707f",
}
W = 60                      # the decision mark, seconds after the fill
POS_BINS = [-1e9, -40, -20, -5, 5, 1e9]
POS_LABS = ["<-40", "-40..-20", "-20..-5", "-5..+5", ">+5"]


def load(run):
    d = pd.read_parquet(HERE / f"windows__{run}.parquet")
    d["win"] = d.net_pnl > 0
    d["pos"] = pd.cut(d[f"w{W}_end"], bins=POS_BINS, labels=POS_LABS)
    d["logvel"] = np.log10(d[f"w{W}_vel"].replace(0, np.nan))
    return d


def partial_spearman(x, y, z):
    """Spearman(x, y) after residualising both on z — the vol-clock screen.

    Ranks everything, regresses x and y on rank(z) linearly, correlates the
    residuals. Answers "does x still move with y at a fixed z".
    """
    m = x.notna() & y.notna() & z.notna()
    if m.sum() < 20:
        return np.nan, np.nan, int(m.sum())
    rx, ry, rz = (stats.rankdata(v[m]) for v in (x, y, z))
    ex = rx - np.polyval(np.polyfit(rz, rx, 1), rz)
    ey = ry - np.polyval(np.polyfit(rz, ry, 1), rz)
    rho, p = stats.spearmanr(ex, ey)
    return rho, p, int(m.sum())


def cut_pnl(g):
    """Dollars if every trade in g were flattened at the window's close."""
    return float(g[f"w{W}_end_$"].sum())


def section(title):
    print(f"\n{'='*78}\n{title}\n{'='*78}", flush=True)


def descriptive(name, d):
    print(f"\n--- {name}  n={len(d)}  win={d.win.mean():.0%}  net=${d.net_pnl.sum():,.0f}")
    print("| window | share of final MAE made by W | trade still open | got back to level |")
    print("|---|---|---|---|")
    for w in (30, 60, 120):
        share = (d[f"w{w}_depth"] / d.mae_ticks.clip(lower=0.25)).median()
        print(f"| {w}s | {share:.2f} | {d[f'w{w}_open'].mean():.0%} | "
              f"{d[f'w{w}_trec'].notna().mean():.0%} |")
    o = d[d[f"w{W}_open"]]
    print(f"\nclimb-out at {W}s (dipped >=10t): "
          f"med depth {o.loc[o[f'w{W}_depth'] >= 10, f'w{W}_depth'].median():.0f}t, "
          f"med time-back {o[f'w{W}_trec'].median():.1f}s, "
          f"med speed {o[f'w{W}_vel'].median():.1f} t/s "
          f"(p10 {o[f'w{W}_vel'].quantile(.1):.1f}, p90 {o[f'w{W}_vel'].quantile(.9):.1f})")


def raw_read(name, d):
    c = d[(d[f"w{W}_open"]) & (d[f"w{W}_depth"] >= 10)]
    print(f"\n--- {name}")
    print("| cohort | n | got back inside 60s | med depth | med time-back | med speed |")
    print("|---|---|---|---|---|---|")
    for lab, g in (("winners", c[c.win]), ("losers", c[~c.win])):
        print(f"| {lab} | {len(g)} | {g[f'w{W}_trec'].notna().mean():.0%} | "
              f"{g[f'w{W}_depth'].median():.0f}t | "
              f"{g[f'w{W}_trec'].median():.1f}s | {g[f'w{W}_vel'].median():.1f} t/s |")


def controlled(name, d):
    o = d[d[f"w{W}_open"]]
    print(f"\n--- {name}: outcome by POSITION at {W}s (the control)")
    print("| position at 60s | n | win% | avgR | med depth |")
    print("|---|---|---|---|---|")
    for l, g in o.groupby("pos", observed=True):
        print(f"| {l} | {len(g)} | {g.win.mean():.0%} | {g.r_multiple.mean():+.2f} | "
              f"{g[f'w{W}_depth'].median():.0f}t |")

    c = o[(o[f"w{W}_depth"] >= 10) & (o[f"w{W}_trec"].notna())]
    print(f"\n--- {name}: fast vs slow climb-out WITHIN each position stratum")
    print("| position at 60s | n | fast n | fast win% | fast avgR | slow n | slow win% | slow avgR |")
    print("|---|---|---|---|---|---|---|---|")
    for l, g in c.groupby("pos", observed=True):
        if len(g) < 25:
            print(f"| {l} | {len(g)} | — | — | — | — | — | — |")
            continue
        m = g[f"w{W}_vel"].median()
        f, s = g[g[f"w{W}_vel"] >= m], g[g[f"w{W}_vel"] < m]
        print(f"| {l} | {len(g)} | {len(f)} | {f.win.mean():.0%} | {f.r_multiple.mean():+.2f} | "
              f"{len(s)} | {s.win.mean():.0%} | {s.r_multiple.mean():+.2f} |")

    print(f"\n--- {name}: partial Spearman (speed vs outcome, holding position at {W}s fixed)")
    print("| x | y | control | n | rho | p |")
    print("|---|---|---|---|---|---|")
    for xn, x in (("log speed", c["logvel"]), ("time-back", c[f"w{W}_trec"]),
                  ("time to +10t", c["t_up10"])):
        for yn, y in (("r_multiple", c.r_multiple),):
            rho, p, n = partial_spearman(x, y, c[f"w{W}_end"])
            print(f"| {xn} | {yn} | pos@{W}s | {n} | {rho:+.3f} | {p:.3f} |")
    # uncontrolled, for contrast
    rho, p = stats.spearmanr(c["logvel"], c.r_multiple, nan_policy="omit")
    print(f"| log speed | r_multiple | (none) | {c.logvel.notna().sum()} | {rho:+.3f} | {p:.3f} |")


def cut_rules(name, d):
    print(f"\n--- {name}: cut rules at the {W}s mark, priced against holding")
    print("| rule fires when | n | realised net | realised avgR | net if cut at 60s | delta |")
    print("|---|---|---|---|---|---|")
    o = d[d[f"w{W}_open"]]
    rules = {
        "never back at the level": o[f"w{W}_trec"].isna(),
        "below the level right now": o[f"w{W}_end"] < 0,
        "more than 20t below": o[f"w{W}_end"] < -20,
        "not +10t within 60s": ~(o["t_up10"] <= W),
        "climb-out slower than median": o[f"w{W}_vel"] < o[f"w{W}_vel"].median(),
    }
    for lab, m in rules.items():
        g = o[m.fillna(False)]
        if g.empty:
            continue
        held, cut = g.net_pnl.sum(), cut_pnl(g)
        print(f"| {lab} | {len(g)} | ${held:,.0f} | {g.r_multiple.mean():+.2f} | "
              f"${cut:,.0f} | **${cut - held:+,.0f}** |")


def split_half(name, d):
    print(f"\n--- {name}: date split-half, the >+5 stratum fast/slow read")
    o = d[(d[f"w{W}_open"]) & (d[f"w{W}_depth"] >= 10) & (d[f"w{W}_trec"].notna())]
    mid = pd.Timestamp(pd.to_datetime(o.session).median())
    print("| half | n | fast win% | fast avgR | slow win% | slow avgR |")
    print("|---|---|---|---|---|---|")
    for lab, g in (("first", o[pd.to_datetime(o.session) <= mid]),
                   ("second", o[pd.to_datetime(o.session) > mid])):
        if len(g) < 30:
            continue
        m = g[f"w{W}_vel"].median()
        f, s = g[g[f"w{W}_vel"] >= m], g[g[f"w{W}_vel"] < m]
        print(f"| {lab} | {len(g)} | {f.win.mean():.0%} | {f.r_multiple.mean():+.2f} | "
              f"{s.win.mean():.0%} | {s.r_multiple.mean():+.2f} |")


def main():
    books = {k: load(v) for k, v in RUNS.items()}
    section("1. Descriptive — when the heat is taken, how fast the climb-out is")
    for k, d in books.items():
        descriptive(k, d)
    section("2. Raw read — winners vs losers (CONTAMINATED, shown for size)")
    for k, d in books.items():
        raw_read(k, d)
    section("3. Controlled — does speed survive holding position fixed?")
    for k, d in books.items():
        controlled(k, d)
    section("4. Cut rules at the 60s mark")
    for k, d in books.items():
        cut_rules(k, d)
    section("5. Split-half")
    for k, d in books.items():
        split_half(k, d)


if __name__ == "__main__":
    main()
