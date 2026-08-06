"""ATR × vwap-upper-band-bounce — analysis over the built feature frames.

Angles:
  1. Collinearity — is ATR just band_width_ticks renamed? (house pre-screen:
     |rho| > 0.7 → don't build)
  2. Outcome — AUC vs stop-out, Spearman vs r_multiple, tercile expectancy.
  3. Geometry — do MAE/MFE scale with ATR (would an ATR-scaled stop even make
     sense) vs the winner-landing-depth "absolute not sigma-scaled" precedent?
  4. Session regime — trade frequency + per-session net by daily-ATR tercile.
  5. Robustness — both runs, split-half by date, monthly signs on any lead.

    Usage: uv run python data/research/atr-band/analyze.py
"""
import math

import numpy as np
import pandas as pd

OUTDIR = "data/research/atr-band"
RUNS = ["a348d176", "cdc07ca2"]
ATRS = ["daily_atr14", "datr_pctl60", "tr_prev_pts",
        "atr1m14", "atr5m14", "atr1m14_0930",
        "range_sofar_pts", "rth_range_sofar_pts"]


def auc(x, y):
    """AUC of x predicting boolean y — rank-sum Mann-Whitney, no scipy."""
    m = x.notna() & y.notna()
    x, y = x[m], y[m].astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = x.rank()
    u = ranks[y].sum() - n1 * (n1 + 1) / 2
    return u / (n1 * n0)


def spearman(x, y):
    """(rho, two-sided p) — Pearson on ranks + t-approximation."""
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
    """Student-t CDF via the normal approximation with Cornish-Fisher-ish
    correction — plenty for reporting p at the precision used here."""
    x = t * (1 - 1 / (4 * df)) / math.sqrt(1 + t * t / (2 * df))
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def pf(net):
    w = net[net > 0].sum()
    l = -net[net < 0].sum()
    return w / l if l else np.inf


def tercile_table(t, col):
    m = t[col].notna()
    q = pd.qcut(t.loc[m, col], 3, labels=["low", "mid", "high"], duplicates="drop")
    rows = []
    for lab, g in t[m].groupby(q, observed=True):
        rows.append({
            "bucket": lab, "n": len(g),
            "win%": (g["net_pnl"] > 0).mean() * 100,
            "stop%": (g["exit_reason"] == "stop").mean() * 100,
            "avgR": g["r_multiple"].mean(),
            "net": g["net_pnl"].sum(),
            "PF": pf(g["net_pnl"]),
            f"{col}_range": f"{g[col].min():.0f}–{g[col].max():.0f}",
        })
    return pd.DataFrame(rows)


def main():
    pd.set_option("display.width", 200)
    daily = pd.read_parquet(f"{OUTDIR}/daily_atr.parquet")

    for run in RUNS:
        t = pd.read_parquet(f"{OUTDIR}/features_{run}.parquet")
        t["is_stop"] = t["exit_reason"] == "stop"
        t["is_win"] = t["net_pnl"] > 0
        t["bw_pts"] = t["band_width_ticks"] * 0.25
        print("=" * 88)
        print(f"RUN {run}: {len(t)} trades, net ${t['net_pnl'].sum():,.0f}, "
              f"win {t['is_win'].mean():.0%}, PF {pf(t['net_pnl']):.2f}")

        # ---- 1. collinearity with band width ------------------------------
        print("\n[1] collinearity vs band_width_ticks (pearson / spearman):")
        for c in ATRS:
            m = t[c].notna()
            pr = t.loc[m, c].corr(t.loc[m, "bw_pts"])
            sp, _ = spearman(t.loc[m, c], t.loc[m, "bw_pts"])
            flag = "  << redundant (>0.7)" if abs(sp) > 0.7 else ""
            print(f"    {c:20s} rho={pr:+.3f} / {sp:+.3f}{flag}")

        # ---- 2. outcome ---------------------------------------------------
        print("\n[2] outcome: AUC(stop) [0.5=null, >0.5 = higher ATR -> more stops], "
              "spearman vs r_multiple (p):")
        for c in ATRS + ["bw_pts"]:
            m = t[c].notna()
            a = auc(t[c], t["is_stop"])
            r, p = spearman(t.loc[m, c], t.loc[m, "r_multiple"])
            print(f"    {c:20s} AUC_stop={a:.3f}  rho_R={r:+.3f} (p={p:.3f})")

        # ---- 3. geometry scaling -----------------------------------------
        w = t[t["is_win"] & t["atr1m14"].notna()]
        print(f"\n[3] geometry scaling (winners n={len(w)}): spearman of |MAE| vs vol measure")
        for c in ["daily_atr14", "atr1m14", "bw_pts"]:
            r, p = spearman(w[c], w["mae_points"].abs())
            r2, p2 = spearman(t[c], t["mfe_points"])
            print(f"    {c:20s} winnerMAE rho={r:+.3f} (p={p:.3f})   allMFE rho={r2:+.3f} (p={p2:.3f})")

        # ---- 4. terciles on the most interpretable measures ---------------
        for c in ["daily_atr14", "atr1m14", "rth_range_sofar_pts"]:
            print(f"\n[4] terciles by {c}:")
            print(tercile_table(t, c).to_string(index=False,
                  float_format=lambda v: f"{v:,.2f}"))

        # ---- 5. session-level: frequency + per-session net by daily ATR ---
        win = daily[(daily["session"] >= t["session"].min())
                    & (daily["session"] <= t["session"].max())].copy()
        per = t.groupby("session")["net_pnl"].sum()
        win["net"] = win["session"].map(per).fillna(0.0)
        win["traded"] = win["session"].isin(per.index)
        m = win["daily_atr14"].notna()
        q = pd.qcut(win.loc[m, "daily_atr14"], 3, labels=["low", "mid", "high"])
        g = win[m].groupby(q, observed=True).agg(
            sessions=("session", "count"), traded=("traded", "sum"),
            trade_rate=("traded", "mean"), net=("net", "sum"),
            net_per_traded=("net", lambda s: s.sum() / max((s != 0).sum(), 1)))
        print(f"\n[5] sessions by daily ATR tercile ({run}):")
        print(g.to_string(float_format=lambda v: f"{v:,.2f}"))

        # ---- 6. split-half stability of the headline measures -------------
        t = t.sort_values("session").reset_index(drop=True)
        h = len(t) // 2
        print("\n[6] split-half (first vs second half of trades): rho_R per half")
        for c in ["daily_atr14", "atr1m14", "rth_range_sofar_pts"]:
            r1, p1 = spearman(t[c][:h], t["r_multiple"][:h])
            r2, p2 = spearman(t[c][h:], t["r_multiple"][h:])
            print(f"    {c:20s} H1 {r1:+.3f} (p={p1:.2f})   H2 {r2:+.3f} (p={p2:.2f})")


if __name__ == "__main__":
    main()
