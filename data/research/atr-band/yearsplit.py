import pandas as pd, sys
sys.path.insert(0, "data/research/atr-band")
from analyze import spearman, pf
for run in ["a348d176", "cdc07ca2"]:
    t = pd.read_parquet(f"data/research/atr-band/features_{run}.parquet")
    for yr, g in t.groupby(t["session"].str[:4]):
        r, p = spearman(g["daily_atr14"], g["r_multiple"])
        m = g["daily_atr14"].notna()
        q = pd.qcut(g.loc[m, "daily_atr14"], 3, labels=["low","mid","high"])
        pfs = {lab: round(pf(sub["net_pnl"]), 2) for lab, sub in g[m].groupby(q, observed=True)}
        print(f"{run} {yr}: n={len(g)} rho_R={r:+.3f} (p={p:.3f})  PF by tercile {pfs}")
