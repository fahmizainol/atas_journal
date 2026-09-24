"""Cone vs Kronos: does a pretrained OHLCV foundation model beat k x ruler?

The kill test (`fit.py`) trained a model on this repo's own data and lost to a
one-parameter baseline — but it was handicapped by sample size. Kronos is
PRETRAINED on 12B candlesticks from 45 exchanges, so it carries knowledge our
599 sessions could never supply. This is the honest re-run of that question.

Method, deliberately identical to the baseline's:

    anchor      an RTH minute t. Everything is read at t's close.
    lookback    the LOOK minutes ending at t (may include the overnight).
    forecast    Kronos generates 30 bars, `paths` times. Each path gives one
                (up, dn) excursion; the quantiles ACROSS paths are the band.
    baseline    k x ruler_30, k fitted in fit.py on sessions ending 2025-11-18.
    label       up_30 / dn_30 from real intrabar highs and lows.
    score       pinball loss at 0.5 / 0.8 / 0.95, plus coverage.

Only held-out Databento sessions are used (2025-11-26 onward), so the k's never
saw them and the pool stays the backtest corpus (live-shadow-plan decision 3).

Runs under the ISOLATED venv, which is the only one with torch:

    .venv-kronos/bin/python data/research/cone/kronos_test.py --pilot
    .venv-kronos/bin/python data/research/cone/kronos_test.py --model mini \
        --paths 50 --sessions 0 --anchors 150

Writes ``kronos_results.json`` beside this file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / ".kronos"))          # `from model import ...`

DAYS = ROOT / "data" / "research" / "forward-fan" / "days.parquet"
KFILE = HERE / "results.json"
OUT = HERE / "kronos_results.json"

TICK = 0.25
BELL = 15 * 60 + 30          # 09:30 as minutes from 18:00
RTH = 390
HORIZON = 30
QUANTILES = (0.5, 0.8, 0.95)
TEST_FROM = "2025-11-26"     # fit.py's held-out span
RULER_W, RULER_MIN = 30, 5

VARIANTS = {
    "mini":  ("NeoQuasar/Kronos-Tokenizer-2k",   "NeoQuasar/Kronos-mini",  2048),
    "small": ("NeoQuasar/Kronos-Tokenizer-base", "NeoQuasar/Kronos-small",  512),
    "base":  ("NeoQuasar/Kronos-Tokenizer-base", "NeoQuasar/Kronos-base",   512),
}


def pinball(y: np.ndarray, yhat: np.ndarray, q: float) -> float:
    d = y - yhat
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def sessions(df: pd.DataFrame, limit: int) -> list[str]:
    """Held-out Databento sessions only — never a live recording (decision 3)."""
    d = df[(df["src"] == "databento") & (df["day"].astype(str) >= TEST_FROM)]
    days = sorted(d["day"].astype(str).unique())
    return days[:limit] if limit else days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mini", choices=list(VARIANTS))
    ap.add_argument("--lookback", type=int, default=200)
    ap.add_argument("--paths", type=int, default=50, help="Monte Carlo draws = the band")
    ap.add_argument("--sessions", type=int, default=0, help="0 = every held-out session")
    ap.add_argument("--anchors", default="150", help="comma-separated RTH minute offsets")
    ap.add_argument("--pilot", action="store_true",
                    help="tiny head-to-head: both models, 4 sessions, 16 paths")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    import torch  # noqa: E402 — only the isolated venv has it
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

    df = pd.read_parquet(DAYS)
    if "src" not in df.columns:
        df["src"] = "databento"
    kj = json.loads(KFILE.read_text())
    K = {f"{s}_{HORIZON}_{q}": kj[f"{s}_{HORIZON}_q{q}"]["k"]
         for s in ("up", "dn") for q in QUANTILES}

    models = [args.model] if not args.pilot else ["mini", "small"]
    n_sess = args.sessions or (4 if args.pilot else 0)
    paths = 16 if args.pilot else args.paths
    anchors = [int(a) for a in args.anchors.split(",")]
    days = sessions(df, n_sess)
    if not days:
        raise SystemExit("no held-out sessions")
    print(f"  {len(days)} sessions x {len(anchors)} anchors x {paths} paths "
          f"| lookback {args.lookback} | models {models}")
    print(f"  threads {torch.get_num_threads()}\n")

    # --- gather the anchors once; every model scores the SAME windows.
    work = []
    for day in days:
        g = df[df["day"].astype(str) == day].sort_values("i").reset_index(drop=True)
        s = g.iloc[BELL:BELL + RTH].reset_index(drop=True)
        if len(s) < RTH or s["high"].isna().mean() > 0.02:
            continue
        hi, lo, cl = (s[c].to_numpy(float) for c in ("high", "low", "close"))
        ruler = pd.Series((hi - lo) / TICK).rolling(RULER_W, min_periods=RULER_MIN).median().to_numpy()
        ts0 = pd.Timestamp(f"{day} 18:00", tz="America/New_York") - pd.Timedelta(days=1)
        for a in anchors:
            if a + HORIZON >= RTH or not np.isfinite(ruler[a]):
                continue
            j = BELL + a                       # global minute index of the anchor
            w = g.iloc[j - args.lookback + 1:j + 1]
            if w[["open", "high", "low", "close"]].isna().any().any():
                continue
            work.append({
                "day": day, "anchor": a, "close": cl[a], "ruler": ruler[a],
                "df": w[["open", "high", "low", "close", "volume"]].reset_index(drop=True),
                "xt": pd.Series(ts0 + pd.to_timedelta(np.arange(j - args.lookback + 1, j + 1), "min")),
                "yt": pd.Series(ts0 + pd.to_timedelta(np.arange(j + 1, j + 1 + HORIZON), "min")),
                "up": (hi[a + 1:a + 1 + HORIZON].max() - cl[a]) / TICK,
                "dn": (cl[a] - lo[a + 1:a + 1 + HORIZON].min()) / TICK,
            })
    print(f"  {len(work)} usable anchors\n")

    results = {}
    for name in models:
        tok_id, mdl_id, ctx = VARIANTS[name]
        tok = KronosTokenizer.from_pretrained(tok_id)
        mdl = Kronos.from_pretrained(mdl_id)
        predictor = KronosPredictor(mdl, tok, device="cpu", max_context=ctx)

        t0 = time.time()
        pred_up = np.full((len(work), len(QUANTILES)), np.nan)
        pred_dn = np.full((len(work), len(QUANTILES)), np.nan)
        for i, w in enumerate(work):
            ups, dns = [], []
            for _ in range(paths):
                out = predictor.predict(df=w["df"], x_timestamp=w["xt"], y_timestamp=w["yt"],
                                        pred_len=HORIZON, T=1.0, top_p=0.9,
                                        sample_count=1, verbose=False)
                ups.append((out["high"].max() - w["close"]) / TICK)
                dns.append((w["close"] - out["low"].min()) / TICK)
            pred_up[i] = np.quantile(ups, QUANTILES)
            pred_dn[i] = np.quantile(dns, QUANTILES)
            if (i + 1) % 10 == 0:
                el = time.time() - t0
                print(f"    {i+1}/{len(work)}  {el:.0f}s  (eta {el/(i+1)*(len(work)-i-1):.0f}s)")
        elapsed = time.time() - t0

        rec = {"elapsed_s": round(elapsed, 1), "anchors": len(work), "paths": paths,
               "lookback": args.lookback, "cells": {}}
        print(f"\n  === {name} ({elapsed:.0f}s, {elapsed/max(1,len(work)*paths):.2f}s/path) ===")
        for side, pred_q in (("up", pred_up), ("dn", pred_dn)):
            y = np.array([w[side] for w in work], float)
            ruler = np.array([w["ruler"] for w in work], float)
            for qi, q in enumerate(QUANTILES):
                base = K[f"{side}_{HORIZON}_{q}"] * ruler
                pk, pb = pinball(y, pred_q[:, qi], q), pinball(y, base, q)
                cell = {"kronos_pinball": round(pk, 3), "ruler_pinball": round(pb, 3),
                        "gain_pct": round(100 * (1 - pk / pb), 2),
                        "kronos_cov": round(float((y <= pred_q[:, qi]).mean()), 3),
                        "ruler_cov": round(float((y <= base).mean()), 3)}
                rec["cells"][f"{side}_{q}"] = cell
                flag = "KRONOS" if cell["gain_pct"] > 0 else "ruler "
                print(f"    {side} q{q:<5} pinball kronos {pk:8.2f} | ruler {pb:8.2f}  "
                      f"{cell['gain_pct']:+6.2f}%  -> {flag}   "
                      f"cov {cell['kronos_cov']:.2f} vs {cell['ruler_cov']:.2f} (target {q})")
        results[name] = rec

    payload = {"tag": args.tag, "pilot": args.pilot, "sessions": len(days),
               "test_from": TEST_FROM, "results": results}
    prev = json.loads(OUT.read_text()) if OUT.exists() else []
    prev = prev if isinstance(prev, list) else [prev]
    prev.append(payload)
    OUT.write_text(json.dumps(prev, indent=1))
    print(f"\n  wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
