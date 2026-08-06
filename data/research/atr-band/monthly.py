"""Monthly robustness of the daily-ATR lean + where the H1 signal lives."""
import pandas as pd, numpy as np, math, sys
sys.path.insert(0, "data/research/atr-band")
from analyze import spearman, pf

for run in ["a348d176", "cdc07ca2"]:
    t = pd.read_parquet(f"data/research/atr-band/features_{run}.parquet")
    t["month"] = t["session"].str[:7]
    med = t["daily_atr14"].median()
    print("=" * 70, f"\nRUN {run}: monthly avgR low-vs-high daily_atr14 (split at pooled median {med:.0f})")
    rows = []
    for m, g in t.groupby("month"):
        lo, hi = g[g["daily_atr14"] <= med], g[g["daily_atr14"] > med]
        if len(lo) >= 3 and len(hi) >= 3:
            rows.append({"month": m, "n_lo": len(lo), "n_hi": len(hi),
                         "avgR_lo": lo["r_multiple"].mean(), "avgR_hi": hi["r_multiple"].mean(),
                         "lo_better": lo["r_multiple"].mean() > hi["r_multiple"].mean()})
    mt = pd.DataFrame(rows)
    print(mt.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"lo_better in {mt['lo_better'].sum()}/{len(mt)} comparable months")
    # within-month rho (controls for regime drift across the window)
    rs = [spearman(g["daily_atr14"], g["r_multiple"])[0] for _, g in t.groupby("month") if len(g) >= 8]
    rs = [r for r in rs if not pd.isna(r)]
    print(f"within-month rho_R: median {np.median(rs):+.3f}, negative in {sum(r<0 for r in rs)}/{len(rs)} months (n>=8)")
    # drop the 2025 Feb-May vol shock and re-test pooled rho
    calm = t[t["session"] >= "2025-06-01"]
    r, p = spearman(calm["daily_atr14"], calm["r_multiple"])
    print(f"pooled rho_R excluding Feb-May 2025 shock: {r:+.3f} (p={p:.3f}, n={len(calm)})")
