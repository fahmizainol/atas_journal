"""Do quiet/mid/hot ATR-regime days differ in price action / market structure?

Joins the regime-artifact structure KPIs (eod_structure_v8.parquet — BOS/CHoCH
rates, chop occupancy, texture/class labels from the swing-pivot event stream)
with the causal vol-regime label (datr_pctl60 terciles from daily_atr.parquet)
and asks whether SHAPE differs across terciles, beyond the scale difference the
label is built from. Read-only diagnostic; split-half by date for the headline
contrasts. No scipy — Welch normal approx + 2-prop z, house style.
"""
import numpy as np
import pandas as pd

ROOT = "/home/afahmi/repos/atas_journal"

atr = pd.read_parquet(f"{ROOT}/data/research/atr-band/daily_atr.parquet")
atr["terc"] = pd.cut(atr["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                     labels=["quiet", "mid", "hot"])
atr["ret_pts"] = atr["close"] - atr["open"]
atr["range_pts"] = atr["high"] - atr["low"]
# shape-normalised day geometry (scale removed by dividing by the day's own range)
atr["close_pos"] = (atr["close"] - atr["low"]) / atr["range_pts"]          # 0=low, 1=high
atr["directionality"] = (atr["close"] - atr["open"]).abs() / atr["range_pts"]  # |net|/range
atr["up_day"] = (atr["ret_pts"] > 0).astype(float)

st = pd.read_parquet(f"{ROOT}/data/research/regime-structure/eod_structure_v8.parquet")
df = st.merge(atr[["session", "terc", "close_pos", "directionality", "up_day",
                   "range_pts", "ret_pts"]], on="session", how="inner")
df = df[df["terc"].notna() & ~df["partial"]].copy()
df["date"] = pd.to_datetime(df["session"])
df["half"] = np.where(df["date"] < df["date"].median(), "H1", "H2")
print(f"joined sessions: {len(df)}  (quiet/mid/hot = "
      f"{(df.terc=='quiet').sum()}/{(df.terc=='mid').sum()}/{(df.terc=='hot').sum()})")


def welch_p(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return np.nan
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    if se == 0:
        return np.nan
    z = (a.mean() - b.mean()) / se
    from math import erf
    return 2 * (1 - 0.5 * (1 + erf(abs(z) / np.sqrt(2))))


NUM = ["chop_occ_rth", "chop_occ_30m", "st_break_rate", "st_bos_share",
       "st_choch_rate", "st_bias_share", "st_bias_age_min",
       "directionality", "close_pos", "up_day"]

print("\n== numeric KPIs by tercile (mean; quiet-vs-hot Welch p) ==")
g = df.groupby("terc", observed=True)
rows = []
for c in NUM:
    m = g[c].mean()
    p = welch_p(df.loc[df.terc == "quiet", c], df.loc[df.terc == "hot", c])
    rows.append([c, m.get("quiet", np.nan), m.get("mid", np.nan),
                 m.get("hot", np.nan), p])
out = pd.DataFrame(rows, columns=["kpi", "quiet", "mid", "hot", "p_q_vs_h"])
print(out.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

print("\n== day-class mix by tercile (row %) ==")
mix = pd.crosstab(df["terc"], df["class"], normalize="index") * 100
print(mix.to_string(float_format=lambda x: f"{x:.0f}%"))
print("\n== texture mix by tercile (row %) ==")
tex = pd.crosstab(df["terc"], df["texture"], normalize="index") * 100
print(tex.to_string(float_format=lambda x: f"{x:.0f}%"))


def prop_z(k1, n1, k2, n2):
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return np.nan
    from math import erf
    z = (p1 - p2) / se
    return 2 * (1 - 0.5 * (1 + erf(abs(z) / np.sqrt(2))))


for lab, col, val in [("trend-day share", "class", None), ("churny share", "texture", "churny")]:
    if val is None:
        ind = df["class"].str.startswith("trend")
    else:
        ind = df[col] == val
    q, h = df.terc == "quiet", df.terc == "hot"
    p = prop_z(ind[q].sum(), q.sum(), ind[h].sum(), h.sum())
    print(f"\n{lab}: quiet {ind[q].mean():.0%} vs hot {ind[h].mean():.0%}  (2-prop p={p:.3f})")

print("\n== split-half stability of the headline contrasts (quiet mean / hot mean per half) ==")
for c in ["chop_occ_rth", "st_choch_rate", "directionality", "st_bias_share"]:
    line = []
    for hh in ["H1", "H2"]:
        s = df[df.half == hh]
        line.append(f"{hh}: {s.loc[s.terc=='quiet', c].mean():.3f}/"
                    f"{s.loc[s.terc=='hot', c].mean():.3f}")
    print(f"  {c:18s} {'  '.join(line)}")

print("\n== trend-day direction split by tercile ==")
td = df[df["class"].str.startswith("trend")]
print(pd.crosstab(td["terc"], td["class"]).to_string())
