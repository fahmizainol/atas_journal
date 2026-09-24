"""Second pass: can a *scaled* night beat yesterday, and against which truth?

The first pass (pre_open_ruler.py) said two things. The night's median ranks the
days better than yesterday's does — rho 0.86 against 0.79 — and it is
systematically too wide, 1.29x on the whole night and 1.12x on the last hour of
it. That is a calibration problem wearing an accuracy problem's clothes: a
predictor that is 29% high on every day is a predictor with a constant in front
of it, and the constant is one number.

So this scores the scaled versions — and does it **out of sample**, because a
scale fitted on the days it is then graded on is a scale that cannot lose. The
sessions are split by date: the older half fits every constant, the newer half is
scored, and then the two halves are swapped to show the constant is a property of
the market rather than of the half.

TWO TRUTHS, deliberately. A stop set at the bell is judged here against both:

  `settled`  the day's own 10:00-16:00 median — "how big are this day's bars",
             which is what the preset ruler is trying to be a causal estimate of.
  `open30`   the 09:30-10:00 median — the volatility the bracket will actually
             live in, if the trade is taken at the open and is over inside the
             hour.

They are not the same question and they do not have to have the same answer. The
open runs 1.12x the settled median, so a stop that nails the day's character is
~11% tight for the first half hour by construction.

Usage:
    .venv/bin/python data/research/preset-stop/calibrate.py
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]

#: Candidate predictors, as functions of a day's row. Every one is complete
#: before the bell — nothing here can see the session it is predicting.
RAW = {
    "yday_rth": lambda r: r.get("yday_rth"),
    "pre_all": lambda r: r.get("pre_all"),
    "pre_2h": lambda r: r.get("pre_2h"),
    "pre_1h": lambda r: r.get("pre_1h"),
}

#: Blends, given the already-scaled parts. The geometric mean rather than the
#: arithmetic one: these are multiplicative quantities (a stop is set in
#: proportion), so the average that matters is the one on the log scale.
BLENDS = {
    "½yday ½night": lambda p: (p["yday_rth"] * p["pre_all"]) ** 0.5,
    "⅓yday ⅔night": lambda p: p["yday_rth"] ** (1 / 3) * p["pre_all"] ** (2 / 3),
    "½yday ½pre_1h": lambda p: (p["yday_rth"] * p["pre_1h"]) ** 0.5,
}


def stats(pred: np.ndarray, truth: np.ndarray) -> dict:
    r = pred / truth
    return dict(
        n=int(len(r)),
        bias=round(float(np.median(r)), 3),
        p50=round(float(np.median(np.abs(r - 1))) * 100, 1),
        p90=round(float(np.percentile(np.abs(r - 1), 90)) * 100, 1),
        w20=round(float(np.mean(np.abs(r - 1) <= 0.20)) * 100, 0),
        w35=round(float(np.mean(np.abs(r - 1) <= 0.35)) * 100, 0),
    )


def score(rows: list[dict], truth_key: str, train: list[dict], test: list[dict]) -> dict:
    """Fit every scale on `train`, score every predictor on `test`."""
    # The constant: the median ratio the predictor runs at, on the training days
    # alone. One number per predictor, and the only thing fitted anywhere here.
    scale = {}
    for name, f in RAW.items():
        r = [f(d) / d[truth_key] for d in train]
        scale[name] = float(np.median(r))

    truth = np.array([d[truth_key] for d in test], dtype=float)
    out = {}
    parts = {}
    for name, f in RAW.items():
        p = np.array([f(d) for d in test], dtype=float) / scale[name]
        parts[name] = p
        out[f"{name} ÷{scale[name]:.2f}"] = stats(p, truth)
    for name, f in BLENDS.items():
        out[name] = stats(f(parts), truth)
    # The rule as it stands, unscaled, for the comparison that matters.
    out["yday_rth (as built)"] = stats(
        np.array([d["yday_rth"] for d in test], dtype=float), truth
    )
    return out, scale


def table(title: str, res: dict):
    print(f"\n  {title}")
    print(f"    {'predictor':<24}{'bias':>7}{'p50 err':>9}{'p90 err':>9}{'±20%':>7}{'±35%':>7}")
    for name, s in sorted(res.items(), key=lambda kv: kv[1]["p50"]):
        print(f"    {name:<24}{s['bias']:>7.2f}{s['p50']:>8.1f}%{s['p90']:>8.1f}%"
              f"{s['w20']:>6.0f}%{s['w35']:>6.0f}%")


def main():
    data = json.loads((HERE / "summary.json").read_text())
    rows = [r for r in data["rows"] if r.get("ok")]
    # Every day where the truth and all four predictors exist — one shared
    # sample, so no predictor can look good by skipping the days it was silent.
    keep = [
        r for r in rows
        if r.get("rth") and r.get("open30") and all(f(r) for f in RAW.values())
    ]
    keep.sort(key=lambda r: r["day"])
    half = len(keep) // 2
    early, late = keep[:half], keep[half:]
    print(f"{len(keep)} sessions, {early[0]['day']}..{early[-1]['day']} | "
          f"{late[0]['day']}..{late[-1]['day']}")

    for truth_key, label in (("rth", "the day's settled median"), ("open30", "the first half hour")):
        print(f"\n=== predicting {label} ===")
        res_late, sc_early = score(keep, truth_key, early, late)
        table(f"fit on the older half, scored on the newer ({len(late)} sessions)", res_late)
        res_early, sc_late = score(keep, truth_key, late, early)
        table(f"and the other way round ({len(early)} sessions)", res_early)
        print("\n    scales fitted:  " + "  ".join(
            f"{k} {sc_early[k]:.2f}/{sc_late[k]:.2f}" for k in RAW))
        print("    (the two halves agreeing on a constant is what makes it a constant)")


if __name__ == "__main__":
    main()
