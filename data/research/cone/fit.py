"""Cone kill test, stage 2: can anything beat the ruler?

Three predictors of how far price travels, scored at three quantiles and three
horizons, on a chronological hold-out:

    ruler      the baseline — k x the 30-min median bar range, k set on TRAIN
               to hit the quantile. This is what the bracket presets already do.
    vol        gradient-boosted quantile regression on the volatility family
               alone (rulers at three scales, realised vol, expansion, clock).
    vol+geo    the same model plus the level geometry and arrival.

`vol` vs `ruler` asks whether volatility is worth modelling at all.
`vol+geo` vs `vol` is the ablation that matters: does the chart carry anything
about how far price travels that the volatility numbers do not already say?

Scoring is pinball loss (the proper loss for a quantile) plus coverage — a
predicted 80th percentile that is not exceeded ~20% of the time is miscalibrated
whatever its loss. The split is by SESSION and chronological, with an embargo,
because neighbouring minutes share their forward window and a random split
would let the answer leak across it.

    .venv/bin/python data/research/cone/fit.py [--quick]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build import ARR_FEATS, GEO_FEATS, HORIZONS, VOL_FEATS  # noqa: E402

PANEL = HERE / "panel.parquet"
RESULTS = HERE / "results.json"
QUANTILES = (0.5, 0.8, 0.95)
EMBARGO = 5            # sessions dropped at the split so no forward window straddles it
TEST_FRAC = 0.25

FAMILIES = {"vol": VOL_FEATS, "vol+geo": VOL_FEATS + GEO_FEATS + ARR_FEATS}


def pinball(y: np.ndarray, yhat: np.ndarray, q: float) -> float:
    d = y - yhat
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer trees, skip importance")
    args = ap.parse_args()

    p = pd.read_parquet(PANEL)
    days = np.array(sorted(p["day"].unique()))
    cut = int(len(days) * (1 - TEST_FRAC))
    train_days, test_days = set(days[:cut - EMBARGO]), set(days[cut:])
    tr, te = p[p["day"].isin(train_days)], p[p["day"].isin(test_days)]
    print(f"train {len(tr):,} rows / {len(train_days)} sessions ({days[0]} .. {days[cut - EMBARGO - 1]})")
    print(f"test  {len(te):,} rows / {len(test_days)} sessions ({days[cut]} .. {days[-1]})"
          f"   embargo {EMBARGO} sessions\n")

    results, rows = {}, []
    for h in HORIZONS:
        for side in ("dn", "up"):
            col = f"{side}_{h}"
            ytr, yte = tr[col].to_numpy(), te[col].to_numpy()
            for q in QUANTILES:
                # --- baseline: the ruler, scaled to hit the quantile on train.
                k = float(np.quantile(ytr / tr["ruler_30"].to_numpy(), q))
                base = k * te["ruler_30"].to_numpy()
                rec = {"h": h, "side": side, "q": q, "k": round(k, 3),
                       "ruler": {"pinball": pinball(yte, base, q),
                                 "coverage": float((yte <= base).mean()),
                                 "median": float(np.median(base))}}
                for fam, feats in FAMILIES.items():
                    m = HistGradientBoostingRegressor(
                        loss="quantile", quantile=q, max_iter=120 if args.quick else 400,
                        learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200,
                        early_stopping=True, validation_fraction=0.12, random_state=0)
                    m.fit(tr[feats], ytr)
                    pred = m.predict(te[feats])
                    rec[fam] = {"pinball": pinball(yte, pred, q),
                                "coverage": float((yte <= pred).mean()),
                                "median": float(np.median(pred)),
                                "spread": float(np.std(pred) / np.mean(pred))}
                    rec[fam]["gain_vs_ruler"] = 100 * (1 - rec[fam]["pinball"] / rec["ruler"]["pinball"])
                rec["geo_gain_vs_vol"] = 100 * (1 - rec["vol+geo"]["pinball"] / rec["vol"]["pinball"])
                results[f"{col}_q{q}"] = rec
                rows.append(rec)
                print(f"  {col:6} q{q:<5} ruler k={k:5.2f}  "
                      f"pinball ruler {rec['ruler']['pinball']:6.2f} | "
                      f"vol {rec['vol']['pinball']:6.2f} ({rec['vol']['gain_vs_ruler']:+5.1f}%) | "
                      f"vol+geo {rec['vol+geo']['pinball']:6.2f} ({rec['vol+geo']['gain_vs_ruler']:+5.1f}%)"
                      f"   geo adds {rec['geo_gain_vs_vol']:+5.2f}%   "
                      f"cov {rec['ruler']['coverage']:.2f}/{rec['vol']['coverage']:.2f}/{rec['vol+geo']['coverage']:.2f}")
        print()

    # Which features does the full model actually lean on? One representative
    # cell, because permutation importance costs a full re-predict per feature.
    if not args.quick:
        h, q, col = 30, 0.8, "dn_30"
        feats = FAMILIES["vol+geo"]
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=400,
                                          learning_rate=0.06, min_samples_leaf=200,
                                          early_stopping=True, random_state=0).fit(tr[feats], tr[col])
        sub = te.sample(min(20000, len(te)), random_state=0)
        imp = permutation_importance(m, sub[feats], sub[col], n_repeats=3, random_state=0,
                                     scoring=lambda est, X, y: -pinball(y.to_numpy(), est.predict(X), q))
        order = np.argsort(imp.importances_mean)[::-1]
        print(f"permutation importance ({col}, q{q}) — drop in pinball when shuffled:")
        for i in order[:12]:
            fam = "vol" if feats[i] in VOL_FEATS else ("geo" if feats[i] in GEO_FEATS else "arr")
            print(f"   {feats[i]:12} [{fam}] {imp.importances_mean[i]:7.4f} ± {imp.importances_std[i]:.4f}")
        results["importance"] = {feats[i]: float(imp.importances_mean[i]) for i in order}

    RESULTS.write_text(json.dumps(results, indent=1))
    print(f"\nwrote {RESULTS.relative_to(HERE.parents[2])}")


if __name__ == "__main__":
    main()
