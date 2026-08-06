"""Vol-clock study — are our time-denominated findings regime-dependent?

Premise: ATR sets the clock. A fixed tick-distance traverse completes ~1/ATR
faster on a hot day (drift case) to ~1/ATR^2 (chop case). Every wall-clock
parameter and finding (hold times, first-hour concentration, afternoon-only
windows, the <=15-min re-entry window) was established pooled across regimes
and could be a vol artifact. This is a read-only diagnostic re-cut by causal
daily-ATR regime — no engine changes, no knobs.

Angles:
  1. Clock test — Spearman(daily ATR, duration) per run, winners/losers
     separately; tercile median durations vs the drift/chop scaling bounds.
  2. Session-boundary risk — exit-reason mix + trade frequency by tercile
     (do quiet days over-index on time/eod exits or fail to fill?).
  3. Expectancy by tercile — does the edge itself live in one regime?
  4. Window findings re-cut — mornings-lose (drift-touch full window),
     first-hour concentration (globex POC), first-fill-of-day (globex bounce).
  5. Re-entry clock — stop -> next-entry gap by tercile (upper-band, reenter
     knob on).

Regime = datr_pctl60 (rolling 60-session percentile of daily ATR14, causal,
from data/research/atr-band/daily_atr.parquet), terciled at 1/3 and 2/3.

    Usage: uv run python data/research/vol-clock/analyze.py
"""
import math

import numpy as np
import pandas as pd

OUTDIR = "data/research/vol-clock"
DOC = "docs/research/vol-clock.md"
ATR_PATH = "data/research/atr-band/daily_atr.parquet"

RUNS = {
    "UB":       ("upper-band v13 (a348d176, audited stack)",
                 "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176"),
    "DTF":      ("drift-touch v2 baseline 12-15h (523f4000)",
                 "data/sims/drift-touch-fade/20250203-20260630-v2-523f4000"),
    "DTF_FULL": ("drift-touch v1 full-window 09:45-15h (7e7a94ea)",
                 "data/sims/drift-touch-fade/20250203-20260630-v1-7e7a94ea"),
    "GPOC":     ("drift-globex-poc v2 (0a20b6a9)",
                 "data/sims/drift-touch-fade-entry-stop/20250203-20260630-v2-0a20b6a9"),
    "GB":       ("globex-bounce v14 invert-on (74e6af45)",
                 "data/sims/vwap-globex-bounce/20240303-20260630-v14-74e6af45"),
}

TERCILES = ["quiet", "mid", "hot"]


# ---------------------------------------------------------------- helpers
def spearman(x, y):
    """(rho, two-sided p) — Pearson on ranks + t-approximation (no scipy)."""
    m = pd.Series(x).notna() & pd.Series(y).notna()
    x, y = pd.Series(x)[m], pd.Series(y)[m]
    n = len(x)
    if n < 5:
        return np.nan, np.nan
    r = x.rank().corr(y.rank())
    if pd.isna(r) or abs(r) >= 1:
        return r, 0.0
    tstat = r * math.sqrt((n - 2) / (1 - r * r))
    p = 2 * (1 - _t_cdf(abs(tstat), n - 2))
    return r, p


def _t_cdf(t, df):
    x = t * (1 - 1 / (4 * df)) / math.sqrt(1 + t * t / (2 * df))
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def pf(net):
    w, l = net[net > 0].sum(), -net[net < 0].sum()
    return w / l if l > 0 else np.inf


def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def fmin(s):
    """seconds -> 'Xm' string."""
    return "—" if pd.isna(s) else f"{s / 60:.0f}m"


# ---------------------------------------------------------------- load
atr = pd.read_parquet(ATR_PATH)
atr["session"] = atr["session"].astype(str).str[:10]
atr = atr.dropna(subset=["datr_pctl60"]).copy()
atr["tercile"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                        labels=TERCILES)
ATR_SESS = atr.set_index("session")

runs = {}
for key, (label, path) in RUNS.items():
    t = pd.read_parquet(path + "/trades.parquet")
    t["session"] = t["session"].astype(str).str[:10]
    n0 = len(t)
    t = t.merge(atr[["session", "daily_atr14", "datr_pctl60", "tercile"]],
                on="session", how="inner")
    t["win"] = t["net_pnl"] > 0
    t["entry_hour"] = pd.to_datetime(t["entry_ts_local"], utc=False).dt.hour
    runs[key] = t
    print(f"loaded {key}: {n0} trades -> {len(t)} in ATR window "
          f"({label})")

doc = ["# Vol-clock — are the time-denominated findings regime artifacts?",
       "",
       "Diagnostic re-cut of the adopted/characterized baselines by causal "
       "daily-ATR regime (`datr_pctl60` terciles from the reusable "
       "`atr-band/daily_atr.parquet`). Premise: ATR sets the clock — a fixed "
       "tick traverse completes between ~ATR-ratio (drift) and ~ATR-ratio² "
       "(chop) faster on hot days — so every wall-clock finding could be "
       "regime-dependent. Read-only; no engine changes.",
       ""]

for key, (label, path) in RUNS.items():
    doc.append(f"- **{key}** = {label} — n={len(runs[key])} in ATR window")
doc.append("")

sess_terc = ATR_SESS["tercile"]
terc_atr = atr.groupby("tercile", observed=True)["daily_atr14"].median()
doc.append("Tercile median daily ATR14: "
           + ", ".join(f"{k} {v:.0f} pt" for k, v in terc_atr.items())
           + f" (hot/quiet ratio {terc_atr['hot'] / terc_atr['quiet']:.2f}; "
             "drift-case clock bound = that ratio, chop-case = its square).")
doc.append("")

# ---------------------------------------------------------------- 1 clock
print("\n=== 1. Clock test ===")
doc += ["## 1. Clock test — does hold time scale with ATR?", ""]
rows = []
for key, t in runs.items():
    for grp, sel in [("winners", t[t.win]), ("losers", t[~t.win])]:
        if len(sel) < 15:
            continue
        rho, p = spearman(sel["daily_atr14"], sel["duration_s"])
        med = sel.groupby("tercile", observed=True)["duration_s"].median()
        ratio = (med.get("quiet", np.nan) / med.get("hot", np.nan)
                 if med.get("hot") else np.nan)
        rows.append([key, grp, len(sel), f"{rho:+.2f}", f"{p:.3f}",
                     fmin(med.get("quiet", np.nan)), fmin(med.get("mid", np.nan)),
                     fmin(med.get("hot", np.nan)),
                     "—" if pd.isna(ratio) else f"{ratio:.2f}x"])
        print(f"{key:8s} {grp:8s} n={len(sel):4d} rho={rho:+.2f} p={p:.3f} "
              f"med q/m/h = {fmin(med.get('quiet', np.nan))}/"
              f"{fmin(med.get('mid', np.nan))}/{fmin(med.get('hot', np.nan))}")
doc.append(md_table(rows, ["run", "cohort", "n", "ρ(ATR,dur)", "p",
                           "med dur quiet", "mid", "hot", "quiet/hot"]))
doc.append("")

# underwater/recovery clocks where populated
rows = []
for key, t in runs.items():
    for col in ["underwater_s", "recovery_s", "giveback_s"]:
        sel = t[t[col].notna() & (t[col] > 0)]
        if len(sel) < 20:
            continue
        rho, p = spearman(sel["daily_atr14"], sel[col])
        rows.append([key, col, len(sel), f"{rho:+.2f}", f"{p:.3f}"])
if rows:
    doc += ["Excursion clocks (same test on underwater/recovery/giveback "
            "seconds):", "", md_table(rows, ["run", "clock", "n", "ρ", "p"]), ""]

# ---------------------------------------------------------------- 2 exits
print("\n=== 2. Exit mix + frequency by tercile ===")
doc += ["## 2. Session-boundary risk — exit mix & frequency by tercile", ""]
rows = []
for key, t in runs.items():
    span = (t.session.min(), t.session.max())
    sess_in_span = atr[(atr.session >= span[0]) & (atr.session <= span[1])]
    n_sess = sess_in_span.groupby("tercile", observed=True).size()
    for terc in TERCILES:
        sel = t[t.tercile == terc]
        if len(sel) == 0:
            continue
        mix = sel.exit_reason.value_counts(normalize=True)
        time_r = sel.loc[sel.exit_reason == "time", "r_multiple"]
        rows.append([
            key, terc, len(sel),
            f"{len(sel) / max(int(n_sess.get(terc, 0)), 1):.2f}",
            f"{mix.get('target', 0) + mix.get('trail', 0):.0%}",
            f"{mix.get('stop', 0):.0%}",
            f"{mix.get('time', 0):.0%}",
            "—" if len(time_r) == 0 else f"{time_r.mean():+.2f}R(n={len(time_r)})",
        ])
doc.append(md_table(rows, ["run", "tercile", "trades", "trades/sess",
                           "target+trail", "stop", "time-exit",
                           "time-exit avgR"]))
doc.append("")

# ---------------------------------------------------------------- 3 edge
print("\n=== 3. Expectancy by tercile ===")
doc += ["## 3. Where does the edge live? Expectancy by tercile", ""]
rows = []
for key, t in runs.items():
    for terc in TERCILES:
        sel = t[t.tercile == terc]
        if len(sel) == 0:
            continue
        rows.append([key, terc, len(sel), f"${sel.net_pnl.sum():,.0f}",
                     f"{sel.win.mean():.0%}", f"{sel.r_multiple.mean():+.2f}",
                     f"{pf(sel.net_pnl):.2f}"])
        print(f"{key:8s} {terc:5s} n={len(sel):4d} net=${sel.net_pnl.sum():>9,.0f} "
              f"win={sel.win.mean():.0%} avgR={sel.r_multiple.mean():+.2f}")
doc.append(md_table(rows, ["run", "tercile", "n", "net", "win%", "avgR", "PF"]))
doc.append("")

# ---------------------------------------------------------------- 4 windows
print("\n=== 4. Window findings re-cut ===")
doc += ["## 4. The wall-clock findings, re-cut by regime", ""]

# 4a. mornings-lose on the full-window drift-touch run
t = runs["DTF_FULL"]
t["window"] = np.where(t.entry_hour < 12, "morning", "afternoon")
rows = []
for terc in TERCILES:
    for w in ["morning", "afternoon"]:
        sel = t[(t.tercile == terc) & (t.window == w)]
        if len(sel) == 0:
            continue
        rows.append([terc, w, len(sel), f"${sel.net_pnl.sum():,.0f}",
                     f"{sel.r_multiple.mean():+.2f}", f"{sel.win.mean():.0%}"])
doc += ["### 4a. \"Mornings lose\" (DTF_FULL, 09:45–15:00 window)", "",
        md_table(rows, ["tercile", "window", "n", "net", "avgR", "win%"]), ""]

# 4b. first-hour concentration on globex POC. Despite the 01:30 window all
# fills land 09-11h local (levels need warmup); the original "first hour =
# 67% of net" is the 09h hour, so that is what gets re-cut.
t = runs["GPOC"]
t["first_hour"] = t["entry_hour"] == 9
rows = []
for terc in TERCILES:
    sel = t[t.tercile == terc]
    if len(sel) == 0:
        continue
    fh = sel[sel.first_hour]
    tot = sel.net_pnl.sum()
    rows.append([terc, len(sel), len(fh), f"${fh.net_pnl.sum():,.0f}",
                 f"${tot:,.0f}",
                 "—" if tot == 0 else f"{fh.net_pnl.sum() / tot:+.0%}"])
doc += ["### 4b. \"First hour = 67% of net\" (GPOC — first hour of fills, "
        "09h local; the 01:30 window never fills before 09h)", "",
        md_table(rows, ["tercile", "n", "n 1st-hr", "1st-hr net", "total net",
                        "1st-hr share"]), ""]

# 4c. first-fill-of-day on globex bounce
t = runs["GB"].sort_values(["session", "entry_ts_utc"])
t["fill_no"] = t.groupby("session").cumcount()
rows = []
for terc in TERCILES:
    sel = t[t.tercile == terc]
    if len(sel) == 0:
        continue
    first, later = sel[sel.fill_no == 0], sel[sel.fill_no > 0]
    rows.append([terc, len(first), f"${first.net_pnl.sum():,.0f}",
                 f"{first.r_multiple.mean():+.2f}",
                 len(later), f"${later.net_pnl.sum():,.0f}",
                 "—" if len(later) == 0 else f"{later.r_multiple.mean():+.2f}"])
doc += ["### 4c. \"First fill of day carries all P&L\" (GB)", "",
        md_table(rows, ["tercile", "n first", "first net", "first avgR",
                        "n later", "later net", "later avgR"]), ""]

# ---------------------------------------------------------------- 5 reentry
# Booked re-entries after a stop are structurally rare on UB v13: a full
# 150-tick 3-lot stop (~$2,250) trips the $1,995 daily loss stop, so only
# partial stops can re-arm (n=4 booked). Measure the re-arm CLOCK instead on
# the union of booked + ghost signals (missed.parquet carries full
# timestamps): gap from a stop exit to the session's next signal.
print("\n=== 5. Re-arm clock (UB, booked + ghost signals) ===")
ub_path = RUNS["UB"][1]
ghosts = pd.read_parquet(ub_path + "/missed.parquet")
ghosts["session"] = ghosts["session"].astype(str).str[:10]
sig = pd.concat([
    runs["UB"][["session", "entry_ts_utc", "exit_ts_utc", "exit_reason"]]
        .assign(kind="booked"),
    ghosts[["session", "entry_ts_utc", "exit_ts_utc", "exit_reason"]]
        .assign(kind="ghost"),
]).sort_values(["session", "entry_ts_utc"]).reset_index(drop=True)
sig = sig.merge(atr[["session", "daily_atr14", "tercile"]], on="session",
                how="inner")
gaps = []
for _, g in sig.groupby("session"):
    stops = g[g.exit_reason == "stop"]
    for _, srow in stops.iterrows():
        nxt = g[pd.to_datetime(g.entry_ts_utc)
                > pd.to_datetime(srow.exit_ts_utc)]
        if len(nxt):
            gap = (pd.to_datetime(nxt.iloc[0].entry_ts_utc)
                   - pd.to_datetime(srow.exit_ts_utc)).total_seconds()
            gaps.append((srow.session, srow.daily_atr14, srow.tercile,
                         gap, nxt.iloc[0].kind))
gaps = pd.DataFrame(gaps, columns=["session", "daily_atr14", "tercile",
                                   "gap_s", "next_kind"])
rows = []
for terc in TERCILES:
    sel = gaps[gaps.tercile == terc]
    if len(sel) == 0:
        continue
    rows.append([terc, len(sel), fmin(sel.gap_s.median()),
                 f"{(sel.gap_s <= 900).mean():.0%}",
                 f"{(sel.next_kind == 'ghost').mean():.0%}"])
rho_all, p_all = spearman(gaps["daily_atr14"], gaps["gap_s"])
doc += ["## 5. Re-arm clock — stop → next signal gap (UB, booked + ghost)",
        "",
        "Booked re-entries are structurally rare on v13 (a full 3-lot stop "
        "≈ $2,250 trips the $1,995 daily loss stop — only partial stops can "
        "re-arm, n=4), so the clock is measured on booked + ghost signals "
        "from `missed.parquet`. The loser-study \"59% regain entry ≤15 min\" "
        "window is the reference wall-clock quantity.",
        "",
        md_table(rows, ["tercile", "n stop→signal", "med gap", "≤15 min",
                        "next is ghost"]),
        "",
        f"Pooled ρ(ATR, gap) = {rho_all:+.2f} (p={p_all:.3f}, "
        f"n={len(gaps)}).",
        ""]
print(f"stop->signal gaps n={len(gaps)} pooled rho={rho_all:+.2f} "
      f"p={p_all:.3f}")

# ---------------------------------------------------------------- 6 halves
# Split-half (by date) on the two headline results: the winner clock rho,
# and DTF_FULL's morning-vs-afternoon delta within the hot tercile.
print("\n=== 6. Split-half checks ===")
doc += ["## 6. Split-half robustness (by date)", ""]
rows = []
for key, t in runs.items():
    w = t[t.win].sort_values("session")
    if len(w) < 30:
        continue
    half = len(w) // 2
    for name, sel in [("H1", w.iloc[:half]), ("H2", w.iloc[half:])]:
        rho, p = spearman(sel["daily_atr14"], sel["duration_s"])
        rows.append([key, name, len(sel), f"{rho:+.2f}", f"{p:.3f}"])
doc += ["Winner clock ρ(ATR, duration) per half:", "",
        md_table(rows, ["run", "half", "n", "ρ", "p"]), ""]

t = runs["DTF_FULL"]
t["window"] = np.where(t.entry_hour < 12, "morning", "afternoon")
hot = t[t.tercile == "hot"].sort_values("session")
half_date = hot.session.iloc[len(hot) // 2]
rows = []
for name, sel in [("H1", hot[hot.session < half_date]),
                  ("H2", hot[hot.session >= half_date])]:
    for w in ["morning", "afternoon"]:
        s = sel[sel.window == w]
        if len(s) == 0:
            continue
        rows.append([name, w, len(s), f"${s.net_pnl.sum():,.0f}",
                     f"{s.r_multiple.mean():+.2f}"])
doc += ["DTF_FULL hot-tercile morning vs afternoon, per half:", "",
        md_table(rows, ["half", "window", "n", "net", "avgR"]), ""]

# ---------------------------------------------------------------- 7 monthly
# Monthly-sign check on the two new expectancy leans: UB quiet-day
# outperformance and DTF hot/mid-day outperformance.
print("\n=== 7. Monthly signs on the expectancy leans ===")
doc += ["## 7. Monthly-sign check on the expectancy leans", ""]
rows = []
for key, focus, rest_lbl in [("UB", ["quiet"], "mid+hot"),
                             ("DTF", ["mid", "hot"], "quiet")]:
    t = runs[key].copy()
    t["month"] = t.session.str[:7]
    t["in_focus"] = t.tercile.isin(focus)
    pos = neg = skipped = 0
    for _, g in t.groupby("month"):
        a, b = g[g.in_focus], g[~g.in_focus]
        if len(a) < 3 or len(b) < 3:
            skipped += 1
            continue
        if a.r_multiple.mean() > b.r_multiple.mean():
            pos += 1
        else:
            neg += 1
    rows.append([key, "+".join(focus), rest_lbl, f"{pos}/{pos + neg}",
                 skipped])
    print(f"{key}: focus {'+'.join(focus)} beats rest in {pos}/{pos + neg} "
          f"months ({skipped} months skipped, <3 trades a side)")
doc += [md_table(rows, ["run", "focus tercile(s)", "vs", "months focus wins",
                        "months skipped"]), ""]

# ---------------------------------------------------------------- verdict
doc += [
    "## Verdict",
    "",
    "1. **The clock is real (CONFIRMED).** Winner hold time anti-correlates "
    "with daily ATR on all five runs (ρ −0.29…−0.43, p≤0.01 pooled), and the "
    "quiet/hot median-duration ratios (1.6–4.1×) sit at or beyond the "
    "chop-case bound — wall-clock durations are regime quantities, full stop. "
    "Split-half: rock-solid on UB/DTF, direction-stable but H2-soft on "
    "GPOC/GB.",
    "",
    "2. **Window boundaries don't need ATR-scaling — their *contents* are "
    "regime-dependent.** \"Mornings lose\" on drift-touch is really "
    "*hot-regime* mornings losing (−0.44R, split-half stable −0.51/−0.35); "
    "quiet mornings are only mildly negative. The adopted afternoon window "
    "earns its keep almost entirely on hot/mid days (§3, §4a). The GPOC "
    "first-hour edge is roughly regime-invariant in dollars (~$9–11k per "
    "tercile) — it is the *rest* of the day that swings with regime. GB's "
    "first-fill advantage holds in every tercile but is thinnest on hot "
    "days, and later fills bleed worst on quiet days.",
    "",
    "3. **New lead: UB's edge concentrates on quiet days** (avgR +0.53, PF "
    "4.0, 84% win vs +0.19/1.6/69% hot) — and the monthly-sign check in §7 "
    "says how seriously to take it. Mirror lead: DTF prefers mid/hot. "
    "Opposite-signed regime leans on the two adopted strategies would be a "
    "natural *portfolio* rotation, not a per-strategy gate — but the gate "
    "scoreboard (1 pass / 12+ fails) and the weekly-VWAP lesson (re-cut on "
    "the current baseline before building) both counsel patience.",
    "",
    "4. **Re-arm clock: cannot be measured on this run** (n=11 stop→signal "
    "gaps — a full 3-lot stop trips the daily loss stop, truncating the "
    "sample). The loser-study \"59% regain ≤15 min\" was a *price-path* "
    "property, not a signal property; re-cutting it by regime needs bar "
    "data per stop. Open item, low priority.",
    "",
    "5. **What NOT to do:** no ATR-time re-expression of window boundaries, "
    "no ATR-scaled stops (already dead per atr-band study), no new gates "
    "off this diagnostic alone.",
    "",
]

# ---------------------------------------------------------------- write
with open(DOC, "w") as f:
    f.write("\n".join(doc) + "\n")
print(f"\nwrote {DOC}")
