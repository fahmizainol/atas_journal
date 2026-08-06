"""Structure x S/R x order flow — the statistics pass over sof_events.parquet.

For every (anchor group x tape feature): AUC of the feature vs follow-through
(fwd_net > 0), within-session permutation p, odd/even split-half AUCs, and the
comparison that decides the study — does the feature discriminate BETTER at
structure/level events than at the time-matched null anchors? If flow->outcome
is the same everywhere, the cross is just short-horizon flow momentum wearing a
price-action costume.

Touch-specific second pass: accept (level breaks) vs reject (level holds) as
the target, plus quartile tables for the practitioner hypotheses (absorption at
a level -> reject; big-lot participation with the approach -> acceptance).

    .venv/bin/python data/research/structure-orderflow/analyze_sof.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("data/research/structure-orderflow")
N_PERM = 1000
FEATURES = [
    "w60_volrate", "w60_cvdpv_al", "w60_big_part", "w60_bigcvd_al", "w60_maxsz",
    "w300_volrate", "w300_cvdpv_al", "w300_big_part", "w300_bigcvd_al", "w300_maxsz",
    "absorp", "exh_ratio", "sess_cvdpv_al",
]


def auc(x: np.ndarray, y: np.ndarray) -> float:
    """Mann-Whitney AUC of x separating y (True vs False), ties midranked."""
    m = np.isfinite(x)
    x, y = x[m], y[m]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(x).rank().to_numpy()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _session_perms(sess: np.ndarray, y: np.ndarray, n_perm: int, seed: int = 7):
    """(n, n_perm) matrix of y shuffled WITHIN each session — preserves every
    session's outcome mix (the structure-node convention)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    out = np.empty((n, n_perm), dtype=bool)
    idx_by_sess = [np.flatnonzero(sess == s) for s in np.unique(sess)]
    for p in range(n_perm):
        col = y.copy()
        for ix in idx_by_sess:
            col[ix] = col[rng.permutation(ix)]
        out[:, p] = col
    return out


def perm_p(x: np.ndarray, y: np.ndarray, yperm: np.ndarray) -> float:
    """Two-sided permutation p for AUC(x,y) against within-session shuffles."""
    m = np.isfinite(x)
    x, y, yp = x[m], y[m], yperm[m]
    n = len(x)
    r = pd.Series(x).rank().to_numpy()
    n1 = int(y.sum())
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    a_obs = (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    n1p = yp.sum(axis=0).astype(float)
    n0p = n - n1p
    ap = (r @ yp - n1p * (n1p + 1) / 2) / np.maximum(n1p * n0p, 1)
    return float((np.abs(ap - 0.5) >= abs(a_obs - 0.5)).mean())


def _halves(df: pd.DataFrame):
    order = {s: k for k, s in enumerate(sorted(df["session"].unique()))}
    ix = df["session"].map(order).to_numpy()
    return ix % 2 == 0, ix % 2 == 1


def group_table(df: pd.DataFrame, target: str, label: str) -> pd.DataFrame:
    """AUC / perm p / split-half for every feature on one anchor group."""
    y = df[target].to_numpy(dtype=bool)
    sess = df["session"].to_numpy()
    yperm = _session_perms(sess, y, N_PERM)
    even, odd = _halves(df)
    rows = []
    for f in FEATURES:
        x = df[f].to_numpy(dtype="float64")
        a = auc(x, y)
        rows.append({
            "group": label, "feature": f, "n": int(np.isfinite(x).sum()),
            "auc": round(a, 3),
            "p": round(perm_p(x, y, yperm), 4) if np.isfinite(a) else np.nan,
            "auc_h1": round(auc(x[even], y[even]), 3),
            "auc_h2": round(auc(x[odd], y[odd]), 3),
        })
    return pd.DataFrame(rows)


def _qcut(s: pd.Series) -> pd.Series:
    """Quartile labels robust to duplicate bin edges."""
    q = pd.qcut(s, 4, labels=False, duplicates="drop")
    return q.map(lambda v: f"q{int(v) + 1}" if pd.notna(v) else np.nan)


def quartile_table(df: pd.DataFrame, feat: str, val: str) -> pd.DataFrame:
    d = df[np.isfinite(df[feat])].copy()
    d["q"] = _qcut(d[feat])
    return d.groupby("q", observed=True).agg(
        n=(val, "size"), mean=(val, "mean"),
        win=(val, lambda s: float((s > 0).mean())),
    ).round(3)


def main():
    df = pd.read_parquet(BASE / "sof_events.parquet")
    df["y"] = df["fwd_net"] > 0
    print(f"rows={len(df)}  sessions={df['session'].nunique()}")

    groups = [
        (df[df["cls"] == "null"], "null(momentum)"),
        (df[(df["cls"] == "break") & (df["thr"] == 5.0)], "break thr5"),
        (df[(df["cls"] == "break") & (df["thr"] == 10.0)], "break thr10"),
        (df[(df["cls"] == "break") & (df["thr"] == 20.0)], "break thr20"),
        (df[(df["cls"] == "break") & (df["kind"] == "BOS")], "BOS (all thr)"),
        (df[(df["cls"] == "break") & (df["kind"] == "CHoCH")], "CHoCH (all thr)"),
        (df[(df["cls"] == "touch") & (df["family"] == "static")], "touch static"),
        (df[(df["cls"] == "touch") & (df["family"] == "pivot")], "touch pivot"),
    ]
    tables = [group_table(g, "y", lbl) for g, lbl in groups if len(g) >= 200]
    allt = pd.concat(tables, ignore_index=True)
    allt.to_csv(BASE / "aucs_sof.csv", index=False)

    print("\n=== AUC of tape feature vs follow-through (fwd_net>0), by group ===")
    print("(p = within-session permutation; h1/h2 = odd/even session halves)")
    for lbl in allt["group"].unique():
        print(f"\n--- {lbl} ---")
        print(allt[allt["group"] == lbl].drop(columns="group").to_string(index=False))

    # the decisive read: event AUC minus null AUC, per feature
    piv = allt.pivot_table(index="feature", columns="group", values="auc")
    if "null(momentum)" in piv.columns:
        delta = piv.sub(piv["null(momentum)"], axis=0).drop(columns="null(momentum)")
        print("\n=== event AUC minus null AUC (does price-action context add anything?) ===")
        print(delta.round(3).to_string())

    # --- touch pass: accept vs reject as the target ---
    tt = df[(df["cls"] == "touch") & (df["outcome"].isin(["accept", "reject"]))].copy()
    print(f"\n=== touches, accept-vs-reject target (n={len(tt)}, chop excluded) ===")
    for fam in ("static", "pivot"):
        g = tt[tt["family"] == fam]
        if len(g) < 200:
            continue
        print(f"\n--- {fam} levels: AUC vs ACCEPT ---")
        print(group_table(g, "accept", f"touch {fam} accept")
              .drop(columns="group").to_string(index=False))

    print("\n=== practitioner hypotheses, quartile tables ===")
    for fam in ("static", "pivot"):
        g = tt[tt["family"] == fam]
        if len(g) < 200:
            continue
        print(f"\n{fam}: reject rate by absorp quartile (hypothesis: high absorp -> reject)")
        d = g[np.isfinite(g["absorp"])].copy()
        d["q"] = pd.qcut(d["absorp"], 4, labels=["q1", "q2", "q3", "q4"], duplicates="drop")
        print(d.groupby("q", observed=True).agg(n=("reject", "size"),
                                                reject=("reject", "mean"),
                                                accept=("accept", "mean")).round(3).to_string())
        print(f"\n{fam}: accept rate by w60_bigcvd_al quartile (big lots with approach -> acceptance)")
        d["q2"] = _qcut(d["w60_bigcvd_al"])
        print(d.groupby("q2", observed=True).agg(n=("accept", "size"),
                                                 accept=("accept", "mean")).round(3).to_string())

    # --- artifact screen: is the "flow" signal just the touch bar's own price
    # action wearing a costume? (the RSI@fill == stretch9 lesson) ---
    CONTROLS = ["close_al", "prevclose_al", "bar_ret_al", "prog_ticks"]
    SUSPECTS = ["absorp", "appr_vol60", "w60_cvdpv_al", "w60_bigcvd_al",
                "w60_maxsz", "exh_ratio"]
    print("\n=== artifact screen (touches): price-only controls vs ACCEPT ===")
    for fam in ("static", "pivot"):
        g = tt[tt["family"] == fam]
        if len(g) < 200:
            continue
        y = g["accept"].to_numpy(dtype=bool)
        print(f"\n--- {fam} ---")
        for f in CONTROLS:
            print(f"  {f:14s} auc={auc(g[f].to_numpy('float64'), y):.3f}")
        print("  spearman with controls:")
        sub = g[SUSPECTS + CONTROLS].corr(method="spearman").loc[SUSPECTS, CONTROLS]
        print(sub.round(2).to_string())

    print("\n=== stratified: flow features WITHIN |close_al| <= 1.0 pt "
          "(anchor still pinned at the level) ===")
    for fam in ("static", "pivot"):
        g = tt[(tt["family"] == fam) & (tt["close_al"].abs() <= 1.0)]
        if len(g) < 300:
            continue
        y = g["accept"].to_numpy(dtype=bool)
        sess = g["session"].to_numpy()
        yperm = _session_perms(sess, y, N_PERM)
        even, odd = _halves(g)
        print(f"\n--- {fam}, n={len(g)}, accept rate={y.mean():.3f} ---")
        for f in SUSPECTS:
            x = g[f].to_numpy("float64")
            print(f"  {f:14s} auc={auc(x, y):.3f}  p={perm_p(x, y, yperm):.4f}  "
                  f"h1={auc(x[even], y[even]):.3f}  h2={auc(x[odd], y[odd]):.3f}")

    print("\n=== breaks: fwd_net by w60_cvdpv_al quartile (flow-confirmed breaks) ===")
    for lbl, g in [("thr5", df[(df["cls"] == "break") & (df["thr"] == 5.0)]),
                   ("thr10", df[(df["cls"] == "break") & (df["thr"] == 10.0)]),
                   ("thr20", df[(df["cls"] == "break") & (df["thr"] == 20.0)]),
                   ("null", df[df["cls"] == "null"])]:
        if len(g) < 200:
            continue
        print(f"\n{lbl}:")
        print(quartile_table(g, "w60_cvdpv_al", "fwd_net").to_string())

    # AM/PM robustness on whatever the headline features turn out to be is a
    # follow-up cut; the CSV has everything needed.


if __name__ == "__main__":
    main()
