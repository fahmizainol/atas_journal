"""Percentile-window sweep — is the vol-clock label sensitive to its lookback?

The study's regime label is datr_pctl60: daily ATR14's percentile within the
trailing 60 sessions, terciled quiet/mid/hot. 60 was a choice, not a finding.
This re-cuts the label at 30- and 14-session windows and compares:

  A. Label behaviour — coverage, agreement vs the 60 baseline, day-to-day
     flip rate, median same-label run length, hot/quiet ATR ratio (how much
     regime contrast each window actually captures).
  B. The study's stats per window — clock test (ρ(ATR,dur) is label-free so
     only the tercile duration medians move), expectancy by tercile, and the
     headline leans (UB quiet lean, DTF regime split, GB first-fill).

Trade stats are computed on the COMMON session set (labelled by all three
windows) so a shorter window's earlier warm-up doesn't smuggle in extra
sessions. Read-only diagnostic — no engine changes.

    Usage: uv run python data/research/vol-clock/pctl_window_sweep.py
"""
import numpy as np
import pandas as pd

ATR_PATH = "data/research/atr-band/daily_atr.parquet"
WINDOWS = [60, 30, 14]
TERCILES = ["quiet", "mid", "hot"]

RUNS = {
    "UB":       ("upper-band v13 (a348d176)",
                 "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176"),
    "DTF":      ("drift-touch v2 12-15h (523f4000)",
                 "data/sims/drift-touch-fade/20250203-20260630-v2-523f4000"),
    "DTF_FULL": ("drift-touch v1 full-window (7e7a94ea)",
                 "data/sims/drift-touch-fade/20250203-20260630-v1-7e7a94ea"),
    "GPOC":     ("drift-globex-poc v2 (0a20b6a9)",
                 "data/sims/drift-touch-fade-entry-stop/20250203-20260630-v2-0a20b6a9"),
    "GB":       ("globex-bounce v14 invert-on (74e6af45)",
                 "data/sims/vwap-globex-bounce/20240303-20260630-v14-74e6af45"),
}


def pf(net):
    w, l = net[net > 0].sum(), -net[net < 0].sum()
    return w / l if l > 0 else np.inf


def roll_pctl(s, window):
    # same estimator as vol_regime.py / build_features.py: share of the
    # trailing window (excluding today) at or below today's ATR
    return s.rolling(window, min_periods=max(window // 3, 5)).apply(
        lambda w: (w.iloc[:-1] <= w.iloc[-1]).mean(), raw=False)


db = pd.read_parquet(ATR_PATH).sort_values("session").reset_index(drop=True)
db["session"] = db["session"].astype(str).str[:10]

for w in WINDOWS:
    db[f"pctl{w}"] = roll_pctl(db["daily_atr14"], w)
    db[f"terc{w}"] = pd.cut(db[f"pctl{w}"], [-0.01, 1 / 3, 2 / 3, 1.01],
                            labels=TERCILES)

common = db.dropna(subset=[f"terc{w}" for w in WINDOWS]).copy()
print(f"sessions: {len(db)} total, {len(common)} labelled by all windows "
      f"({common.session.min()} .. {common.session.max()})")

# ---------------------------------------------------------------- A labels
print("\n=== A. Label behaviour ===")
print(f"{'window':>6} {'coverage':>8} {'agree w/60':>10} {'flip rate':>9} "
      f"{'med run':>7} {'hot/quiet ATR':>13}")
for w in WINDOWS:
    lab = common[f"terc{w}"].astype(str)
    agree = (lab == common["terc60"].astype(str)).mean()
    flips = (lab != lab.shift()).iloc[1:].mean()
    runs_len = lab.groupby((lab != lab.shift()).cumsum()).size()
    med_atr = common.groupby(f"terc{w}", observed=True)["daily_atr14"].median()
    ratio = med_atr.get("hot", np.nan) / med_atr.get("quiet", np.nan)
    cov = db[f"terc{w}"].notna().mean()
    print(f"{w:>6} {cov:>8.0%} {agree:>10.0%} {flips:>9.0%} "
          f"{runs_len.median():>6.1f}d {ratio:>12.2f}x")

# tercile composition shift: what 60 calls hot, what do 30/14 call it?
for w in [30, 14]:
    ct = pd.crosstab(common["terc60"], common[f"terc{w}"], normalize="index")
    print(f"\n60-window tercile -> {w}-window tercile (row %):")
    print((ct * 100).round(0).astype(int).to_string())

# ---------------------------------------------------------------- B trades
sess_cols = ["session", "daily_atr14"] + [f"terc{w}" for w in WINDOWS]
runs = {}
for key, (label, path) in RUNS.items():
    t = pd.read_parquet(path + "/trades.parquet")
    t["session"] = t["session"].astype(str).str[:10]
    t = t.merge(common[sess_cols], on="session", how="inner")
    t["win"] = t["net_pnl"] > 0
    runs[key] = t
    print(f"\nloaded {key}: {len(t)} trades on common sessions ({label})")

print("\n=== B1. Expectancy by tercile, per window ===")
hdr = f"{'run':8s} {'terc':6s}" + "".join(
    f" | {'W' + str(w):>26}" for w in WINDOWS)
print(hdr)
print(f"{'':8s} {'':6s}" + f" | {'n':>5} {'net':>9} {'avgR':>5} {'PF':>4}" * 3)
for key, t in runs.items():
    for terc in TERCILES:
        line = f"{key:8s} {terc:6s}"
        for w in WINDOWS:
            sel = t[t[f"terc{w}"] == terc]
            if len(sel) == 0:
                line += f" | {'—':>26}"
                continue
            line += (f" | {len(sel):>5} ${sel.net_pnl.sum():>8,.0f} "
                     f"{sel.r_multiple.mean():>+5.2f} {pf(sel.net_pnl):>4.2f}")
        print(line)

print("\n=== B2. Clock test — median winner hold quiet vs hot, per window ===")
print(f"{'run':8s}" + "".join(
    f" | W{w}: q / h (ratio)" for w in WINDOWS))
for key, t in runs.items():
    sel = t[t.win]
    line = f"{key:8s}"
    for w in WINDOWS:
        med = sel.groupby(f"terc{w}", observed=True)["duration_s"].median()
        q, h = med.get("quiet", np.nan), med.get("hot", np.nan)
        r = q / h if h and not pd.isna(h) else np.nan
        line += (f" | {q/60:>4.0f}m /{h/60:>4.0f}m ({r:>4.2f}x)"
                 if not (pd.isna(q) or pd.isna(h)) else " |        —        ")
    print(line)

print("\n=== B3. Headline leans, per window ===")
# UB quiet lean: quiet-tercile share of UB net
t = runs["UB"]
for w in WINDOWS:
    q = t[t[f"terc{w}"] == "quiet"]
    print(f"UB quiet lean  W{w}: n={len(q)} net=${q.net_pnl.sum():,.0f} "
          f"({q.net_pnl.sum() / t.net_pnl.sum():+.0%} of total) "
          f"avgR={q.r_multiple.mean():+.2f}")
# DTF hot-morning loss (full window run): morning trades on hot days
t = runs["DTF_FULL"]
t["entry_hour"] = pd.to_datetime(t["entry_ts_local"], utc=False).dt.hour
for w in WINDOWS:
    m = t[(t.entry_hour < 12) & (t[f"terc{w}"] == "hot")]
    print(f"DTF hot-morning W{w}: n={len(m)} net=${m.net_pnl.sum():,.0f} "
          f"avgR={m.r_multiple.mean():+.2f}" if len(m) else
          f"DTF hot-morning W{w}: n=0")
