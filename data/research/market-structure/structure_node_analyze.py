"""Analysis for the structure-break × volume-node pre-check.

Reads structure_node_events.parquet and answers ONE question per the study
contract: do breaks through thin profile (LVN) travel farther than breaks
through heavy shelves (HVN), beyond what a shuffled-node null produces?

Guardrails baked in:
  * maturity filter: bar >= 30 (a 5-minute-old "profile" has no nodes)
  * primary stat raced against a WITHIN-SESSION permutation of node
    percentiles (preserves each session's outcome mix — day drift can't
    manufacture a node effect)
  * split-half by calendar (first half of sessions vs second)
  * touches covariate: node effect re-read inside retest-count buckets,
    because HVN-at-break and "level tested many times" are near-collinear

Usage: .venv/bin/python data/research/market-structure/structure_node_analyze.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SRC = "data/research/market-structure/structure_node_events.parquet"
MIN_BAR = 30
PCTL = "node_pctl_s2"     # primary node reading (1pt smoothing)
PCTL_ALT = "node_pctl_s6" # robustness reading (3pt smoothing)
N_PERM = 2000
RNG = np.random.default_rng(7)


def cells(df: pd.DataFrame, col: str) -> pd.DataFrame:
    q = pd.cut(df[col], [0, 20, 40, 60, 80, 100.001],
               labels=["Q1 thin (LVN)", "Q2", "Q3", "Q4", "Q5 heavy (HVN)"],
               include_lowest=True)
    return df.groupby(q, observed=True).agg(
        n=("fwd_net", "size"),
        net=("fwd_net", "mean"),
        win_pct=("fwd_net", lambda s: 100 * (s > 0).mean()),
        mfe=("fwd_mfe", "mean"),
        mae=("fwd_mae", "mean"),
    ).round(2)


def lvn_hvn_stat(df: pd.DataFrame, col: str, out: str = "fwd_net") -> float:
    lvn = df.loc[df[col] <= 20, out]
    hvn = df.loc[df[col] >= 80, out]
    if len(lvn) < 30 or len(hvn) < 30:
        return np.nan
    return float(lvn.mean() - hvn.mean())


def perm_p(df: pd.DataFrame, col: str, out: str = "fwd_net") -> tuple[float, float]:
    """Two-sided p for the LVN-minus-HVN gap vs within-session node shuffles."""
    obs = lvn_hvn_stat(df, col, out)
    if np.isnan(obs):
        return obs, np.nan
    codes = df.groupby("session").ngroup().to_numpy()
    vals = df[col].to_numpy().copy()
    outs = df[out].to_numpy()
    order = np.argsort(codes, kind="stable")
    codes_s, vals_s = codes[order], vals[order]
    bounds = np.flatnonzero(np.diff(codes_s)) + 1
    hits = 0
    for _ in range(N_PERM):
        shuf = vals_s.copy()
        for seg in np.split(np.arange(len(shuf)), bounds):
            shuf[seg] = shuf[RNG.permutation(seg)]
        back = np.empty_like(shuf)
        back[order] = shuf
        lvn = outs[back <= 20]
        hvn = outs[back >= 80]
        if len(lvn) and len(hvn) and abs(lvn.mean() - hvn.mean()) >= abs(obs):
            hits += 1
    return obs, hits / N_PERM


def main():
    raw = pd.read_parquet(SRC)
    print(f"rows={len(raw)}  sessions={raw.session.nunique()}  "
          f"(maturity filter: bar>={MIN_BAR})")

    for thr, df_t in raw[raw.bar >= MIN_BAR].groupby("thr"):
        df = df_t.dropna(subset=["fwd_net", PCTL])
        print(f"\n{'=' * 72}\n=== swing threshold {thr:g} pt   "
              f"(n={len(df)}, {df.session.nunique()} sessions) ===")

        print(f"\n--- forward outcome by node quintile ({PCTL}) ---")
        print(cells(df, PCTL).to_string())

        obs, p = perm_p(df, PCTL)
        print(f"\nLVN-minus-HVN net gap = {obs:+.2f} pts   "
              f"perm p = {p:.3f}  ({N_PERM} within-session shuffles)")
        obs_m, p_m = perm_p(df, PCTL, "fwd_mfe")
        print(f"LVN-minus-HVN MFE gap = {obs_m:+.2f} pts   perm p = {p_m:.3f}")

        # robustness: the coarser smoothing must tell the same story
        obs_a, p_a = perm_p(df, PCTL_ALT)
        print(f"alt smoothing ({PCTL_ALT}): net gap {obs_a:+.2f}, p={p_a:.3f}")

        # split-half by calendar
        sess = sorted(df.session.unique())
        half = set(sess[:len(sess) // 2])
        for tag, sel in [("first half", df.session.isin(half)),
                         ("second half", ~df.session.isin(half))]:
            g = lvn_hvn_stat(df[sel], PCTL)
            print(f"  split-half {tag}: LVN-HVN net gap = {g:+.2f} pts "
                  f"(n={int(sel.sum())})")

        # BOS vs CHoCH
        df2 = df.assign(kind=df["type"].str.replace("_up", "").str.replace("_down", ""))
        for kind, g in df2.groupby("kind"):
            print(f"  {kind}: LVN-HVN net gap = {lvn_hvn_stat(g, PCTL):+.2f} pts "
                  f"(n={len(g)})")

        # touches covariate: does any node effect survive inside retest buckets?
        print("\n--- LVN-HVN net gap inside touch-count buckets ---")
        tb = pd.cut(df["touches"], [-1, 2, 6, 15, 10_000],
                    labels=["0-2", "3-6", "7-15", "16+"])
        for b, g in df.groupby(tb, observed=True):
            print(f"  touches {b}: gap = {lvn_hvn_stat(g, PCTL):+.2f} pts "
                  f"(n={len(g)}, LVN n={int((g[PCTL] <= 20).sum())}, "
                  f"HVN n={int((g[PCTL] >= 80).sum())})")

        # AM/PM (afternoon leads showed up elsewhere; cheap to look)
        et_hr = (pd.to_datetime(df.session) .dt.tz_localize(None))  # noqa: just date
        is_pm = df["bar"] >= 150  # >= ~12:00 ET in RTH minute bars
        for tag, sel in [("AM (bar<150)", ~is_pm), ("PM (bar>=150)", is_pm)]:
            print(f"  {tag}: gap = {lvn_hvn_stat(df[sel], PCTL):+.2f} pts "
                  f"(n={int(sel.sum())})")


if __name__ == "__main__":
    main()
