"""Does the day's true range spike on event days, and does the ATR/label notice?"""
import pandas as pd

atr = pd.read_parquet("/home/afahmi/repos/atas_journal/data/research/atr-band/daily_atr.parquet")
atr["session"] = pd.to_datetime(atr["session"]).dt.date.astype(str)

FOMC = "2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10 2026-01-28 2026-03-18 2026-04-29 2026-06-17".split()
CPI = "2025-02-12 2025-03-12 2025-04-10 2025-05-13 2025-06-11 2025-07-15 2025-08-12 2025-09-11 2025-10-24 2025-12-18 2026-01-13 2026-02-13 2026-03-11 2026-04-10 2026-05-12 2026-06-10".split()
NFP = "2025-02-07 2025-03-07 2025-04-04 2025-05-02 2025-06-06 2025-07-03 2025-08-01 2025-09-05 2025-11-20 2025-12-16 2026-01-09 2026-02-11 2026-03-06 2026-04-03 2026-05-08 2026-06-05".split()
PCE = "2025-02-28 2025-03-28 2025-04-30 2025-05-30 2025-06-27 2025-07-31 2025-08-29 2025-09-26 2025-12-05 2026-01-22 2026-02-20 2026-03-13 2026-04-09 2026-04-30 2026-05-28 2026-06-25".split()
GDP = "2025-02-27 2025-03-27 2025-04-30 2025-05-29 2025-06-26 2025-07-30 2025-08-28 2025-09-25 2025-12-23 2026-01-22 2026-02-20 2026-03-13 2026-04-09 2026-04-30 2026-05-28 2026-06-25".split()
# earnings are AMC -> affected session is next trading day; approximate by shifting to next session present
EARN_AMC = "2025-02-26 2025-05-28 2025-08-27 2025-11-19 2026-02-25 2026-05-20 2025-05-01 2025-07-31 2025-10-30 2026-01-29 2026-04-30 2025-04-30 2025-07-30 2025-10-29 2026-01-28 2026-04-29 2025-02-06 2026-02-05 2025-02-04 2025-04-24 2025-07-23 2026-02-04".split()

sessions = sorted(atr["session"].tolist())
def next_session(d):
    for s in sessions:
        if s > d:
            return s
    return None
EARN = sorted({next_session(d) for d in EARN_AMC} - {None})

tags = {"FOMC": set(FOMC), "CPI": set(CPI), "NFP": set(NFP), "PCE": set(PCE), "GDP": set(GDP), "EARN": set(EARN)}
macro = set(FOMC) | set(CPI) | set(NFP) | set(PCE) | set(GDP)
any_event = macro | set(EARN)

atr["is_event"] = atr["session"].isin(any_event)
atr["is_macro"] = atr["session"].isin(macro)
# day BEFORE a macro event (pre-announcement compression test)
pre = {sessions[i] for i in range(len(sessions) - 1) if sessions[i + 1] in macro}
atr["is_pre"] = atr["session"].isin(pre)
clean = atr[~atr["is_event"] & ~atr["is_pre"]]

print(f"sessions {len(atr)}, event days {atr.is_event.sum()}, macro {atr.is_macro.sum()}, pre-macro {atr.is_pre.sum()}, clean {len(clean)}")
print(f"\n{'cohort':12} {'n':>4} {'med TR':>8} {'mean TR':>8} {'med ATR14':>10}")
def row(name, df):
    print(f"{name:12} {len(df):4d} {df.tr_pts.median():8.0f} {df.tr_pts.mean():8.0f} {df.daily_atr14.median():10.0f}")
row("clean", clean)
row("pre-macro", atr[atr.is_pre])
for k, v in tags.items():
    row(k, atr[atr.session.isin(v)])
row("all-macro", atr[atr.is_macro])

# ratio of day's TR to its own (lagged) ATR — "did the day exceed what the label expected"
atr["tr_vs_atr"] = atr["tr_pts"] / atr["daily_atr14"]
clean = atr[~atr["is_event"] & ~atr["is_pre"]]
print(f"\nTR / lagged ATR14 (1.0 = as expected):")
for name, df in [("clean", clean), ("pre-macro", atr[atr.is_pre]), ("macro", atr[atr.is_macro]),
                 ("FOMC", atr[atr.session.isin(tags['FOMC'])]), ("CPI", atr[atr.session.isin(tags['CPI'])]),
                 ("NFP", atr[atr.session.isin(tags['NFP'])]), ("EARN", atr[atr.session.isin(tags['EARN'])])]:
    d = df.dropna(subset=["tr_vs_atr"])
    print(f"  {name:10} n={len(d):3d} median {d.tr_vs_atr.median():.2f}  mean {d.tr_vs_atr.mean():.2f}  frac>1.25: {(d.tr_vs_atr>1.25).mean():.0%}")

# how much does one event day move the ATR14 itself? Wilder: new = old + (TR - old)/14
atr["atr_move_pct"] = (atr["tr_pts"] - atr["daily_atr14"]) / atr["daily_atr14"] / 14 * 100
d = atr[atr.is_macro].dropna(subset=["atr_move_pct"])
print(f"\nATR14 shift caused by one macro day: median {d.atr_move_pct.median():+.1f}%, p90 {d.atr_move_pct.quantile(.9):+.1f}%")

# do quiet->hot label flips cluster on event days?
atr["tercile"] = pd.cut(atr["datr_pctl60"], [-0.01, 1/3, 2/3, 1.01], labels=["quiet", "mid", "hot"])
# realized tercile of the DAY's TR within same trailing-60 dist (approx: percentile of tr_pts vs prior 60 tr)
atr["tr_pctl60"] = atr["tr_pts"].rolling(61, min_periods=21).apply(lambda w: (w.iloc[:-1] <= w.iloc[-1]).mean(), raw=False)
atr["real_terc"] = pd.cut(atr["tr_pctl60"], [-0.01, 1/3, 2/3, 1.01], labels=["quiet", "mid", "hot"])
flips = atr.dropna(subset=["tercile", "real_terc"])
flips = flips[((flips.tercile == "quiet") & (flips.real_terc == "hot")) | ((flips.tercile == "hot") & (flips.real_terc == "quiet"))]
print(f"\nquiet<->hot label-vs-realized flips: {len(flips)} days, {flips.is_event.mean():.0%} on event days "
      f"(base rate {atr.is_event.mean():.0%}); label-quiet->realized-hot on macro days: "
      f"{len(flips[(flips.tercile=='quiet') & flips.is_macro])}")
