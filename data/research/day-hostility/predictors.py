"""Does any knowable-by-10:30 KPI predict a bad morning for a ruler-scaled stop?

The two measurements above say 2026-03-25 was only bad for a stop that ignored
the day. That answers "why did it feel like that" but not "is there a day worth
skipping" — so this asks the second question properly: rank every session by the
vol-scaled random-entry base rate, and see which causal KPI separates the tail.

The outcome is the AM 2R target share with the ruler stop — deliberately the
vol-normalised one, because the raw one is mostly a restatement of bar width and
would rediscover volatility rather than day quality.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date as date_cls

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from journal.sim import regime as rgm  # noqa: E402
from journal.sim import vol_regime as vr  # noqa: E402

CANDIDATES = [
    "chop_occ_30m", "chop_occ_rth", "deep_flip_rate", "shallow_flip_rate",
    "ny_vwap_cross_rate", "ny_band_cross_rate", "ny_touch_hold_ratio",
    "gx_touch_hold_ratio", "st_break_rate", "st_choch_rate", "st_bos_share",
    "norm_spread", "longest_hold_min", "quadrant_transitions_rate",
    "net_conviction", "ny_middle_band_occupancy",
]


def main():
    base = json.loads((HERE / "base_rate.json").read_text())
    rows = []
    labels = vr.range_labels("NQ", date_cls(2025, 1, 1), date_cls(2026, 6, 30))
    pctl = {r["date"]: r["pctl"] for r in labels["days"]}

    for b in base:
        if "am" not in b:
            continue
        d = date_cls.fromisoformat(b["day"])
        r = rgm.get_regime("NQ", d)
        if not r:
            continue
        rec = {"day": b["day"],
               "y": b["am"]["ruler_R2.0"]["target_share"],
               "net": b["am"]["ruler_R2.0"]["net_per_trade"],
               "ruler": b["am"]["median_ruler_t"],
               "atr_pctl": pctl.get(b["day"])}
        for cp in ("09:45", "10:30"):
            k = r["checkpoints"].get(cp) or {}
            for c in CANDIDATES:
                rec[f"{cp}:{c}"] = k.get(c)
        rows.append(rec)

    print(f"{len(rows)} sessions joined\n")
    y = np.array([r["y"] for r in rows], dtype=float)

    print(f"{'predictor':34}{'n':>5}{'spearman':>10}{'  bottom-decile mean y':>24}")
    print(f"{'(outcome: AM 2R target share)':34}{'':5}{'':10}{f'  corpus mean y = {y.mean():.3f}':>24}")
    feats = ["ruler", "atr_pctl"] + [f"{cp}:{c}" for cp in ("09:45", "10:30") for c in CANDIDATES]
    scored = []
    for f in feats:
        v = np.array([r.get(f) if r.get(f) is not None else np.nan for r in rows], dtype=float)
        ok = np.isfinite(v) & np.isfinite(y)
        if ok.sum() < 100:
            continue
        vv, yy = v[ok], y[ok]
        rv = np.argsort(np.argsort(vv))
        ry = np.argsort(np.argsort(yy))
        rho = float(np.corrcoef(rv, ry)[0, 1])
        # What the tail actually looks like: worst decile by this predictor.
        cut = int(len(vv) * 0.1)
        lo = yy[np.argsort(vv)[:cut]].mean()
        hi = yy[np.argsort(vv)[-cut:]].mean()
        scored.append((abs(rho), f, ok.sum(), rho, lo, hi))
    for _, f, n, rho, lo, hi in sorted(scored, reverse=True)[:14]:
        print(f"{f:34}{n:5}{rho:10.3f}   low-decile {lo:.3f} / high-decile {hi:.3f}")

    tgt = [r for r in rows if r["day"] == "2026-03-25"]
    if tgt:
        t = tgt[0]
        print(f"\n2026-03-25: y={t['y']:.3f} (corpus mean {y.mean():.3f}), "
              f"pctile {(y < t['y']).mean()*100:.1f}, ruler {t['ruler']}t, atr_pctl {t['atr_pctl']}")


if __name__ == "__main__":
    main()
