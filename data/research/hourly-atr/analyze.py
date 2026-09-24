"""Can ATR predict the next bar's range? 60min vs 30min, all-day vs RTH-only.

Every predictor is causal: computed from bars that had *closed* before the bar
opened, so a row's own range can never leak into its own forecast. Each is
scored the same way — fit one multiplier k on a training span, predict k*p,
report error over a held-out later span. A predictor that cannot beat "the
median range for this slot of the day" is not predicting volatility, it is
reading a clock. That control is the whole study.

Three configurations per interval:

  all         every Globex bar; ATR over the continuous tape.
  rth_eval    RTH bars scored, ATR still over the continuous tape — so the
              lookback is diluted by the quiet overnight bars it spans.
  rth_chain   RTH bars only, ATR rebuilt over the RTH-only chain, exactly what
              an RTH-session chart shows. True range is recomputed against the
              prior RTH close, so the overnight gap enters TR once per session
              instead of being smeared over the night.

MdAE in ticks is not comparable across intervals (a 30min bar ranges less than
a 60min one), so MdAPE — median |error| / realized range — is the headline.

    Usage: uv run python data/research/hourly-atr/analyze.py

Writes findings_<interval>.json next to this file.
"""
import json
import sys

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

OUTDIR = "data/research/hourly-atr"
TICK = 0.25
TRAIN_FRAC = 0.6
ATR_PERIODS = (2, 4, 6, 14, 24)


def spearman(a: pd.Series, b: pd.Series) -> float | None:
    m = a.notna() & b.notna()
    if m.sum() < 30:
        return None
    return float(a[m].rank().corr(b[m].rank()))


def recompute_tr(d: pd.DataFrame) -> pd.DataFrame:
    """True range against the prior row of *this* frame.

    Subsetting to RTH changes what "the previous bar" means, so TR has to be
    rebuilt or the RTH chain would carry gaps measured against bars it no
    longer contains. Roll boundaries fall back to high-low for the same reason
    as the builder: the prior close is a different instrument.
    """
    d = d.sort_values("ts_utc").reset_index(drop=True)
    prev_close = d["close"].shift(1)
    same = d["symbol"].eq(d["symbol"].shift(1))
    hl = d["high"] - d["low"]
    tr = pd.concat([hl, (d["high"] - prev_close).abs(),
                    (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    d["tr_pts"] = np.where(same, tr, hl)
    return d


def build_predictors(d: pd.DataFrame, per_day: int) -> pd.DataFrame:
    """Causal forecasts of the bar's range, in ticks, one column per method."""
    d = d.sort_values("ts_utc").reset_index(drop=True).copy()
    for p in ATR_PERIODS:
        a = d["tr_pts"].ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()
        d[f"atr{p}"] = a.shift(1) / TICK
    # One trading day of bars, whatever that means for this frame.
    a = d["tr_pts"].ewm(alpha=1.0 / per_day, adjust=False, min_periods=per_day).mean()
    d["atr_day"] = a.shift(1) / TICK
    d["prev_range"] = d["range_ticks"].shift(1)

    # The clock itself: expanding median range for this slot, shifted so a
    # day's forecast only ever sees earlier days.
    g = d.groupby("slot")["range_ticks"]
    d["slot_med"] = g.transform(lambda s: s.shift(1).expanding(min_periods=20).median())
    # Same-slot ATR: mean of the last 5 ranges at this time of day.
    d["slot5"] = g.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    # Clock shape scaled by a slow, unitless "is the tape hot right now".
    lvl = d["atr_day"] / d["atr_day"].rolling(10 * per_day,
                                              min_periods=5 * per_day).mean()
    d["slot_x_level"] = d["slot_med"] * lvl
    return d


def preds_for() -> list[str]:
    return (["const", "slot_med", "prev_range"]
            + [f"atr{p}" for p in ATR_PERIODS]
            + ["atr_day", "slot5", "slot_x_level"])


def score(d: pd.DataFrame, label: str) -> dict:
    """Out-of-sample horse race. k fit on train, error reported on test."""
    d = d.dropna(subset=["range_ticks"]).copy()
    d["const"] = 1.0
    cut = int(len(d) * TRAIN_FRAC)
    train, test = d.iloc[:cut], d.iloc[cut:]
    # Score every predictor on identical rows, or the race is not a race.
    cols = preds_for()
    ok = test[cols].notna().all(axis=1)
    test = test[ok]
    out = {
        "label": label,
        "n_train": int(len(train)), "n_test": int(len(test)),
        "train_span": [train["session"].iloc[0], train["session"].iloc[-1]],
        "test_span": [test["session"].iloc[0], test["session"].iloc[-1]],
        "test_median_range_ticks": round(float(test["range_ticks"].median()), 1),
        "preds": {},
    }
    for p in cols:
        tr = train.dropna(subset=[p, "range_ticks"])
        if len(tr) < 100 or len(test) < 100:
            continue
        k = float((tr["range_ticks"] / tr[p]).median())
        fc = k * test[p]
        err = (test["range_ticks"] - fc).abs()
        ratio = test["range_ticks"] / fc
        out["preds"][p] = {
            "k": round(k, 4),
            "mdae_ticks": round(float(err.median()), 1),
            "mdape": round(float((err / test["range_ticks"]).median()), 4),
            "spearman": round(spearman(test[p], test["range_ticks"]) or float("nan"), 4),
            "ratio_q": {str(q): round(float(ratio.quantile(q)), 3)
                        for q in (0.1, 0.5, 0.9)},
        }
    return out


def within_slot(d: pd.DataFrame, cols: tuple[str, ...]) -> dict:
    """The honest test: does the predictor rank bars *inside* one clock slot?"""
    rows, wavg = {}, {}
    for slot, g in d.groupby("slot"):
        r = {"n": int(len(g)),
             "label": str(g["slot_label"].iloc[0]),
             "median_range_ticks": round(float(g["range_ticks"].median()), 1)}
        for c in cols:
            v = spearman(g[c], g["range_ticks"])
            r[c] = round(v, 4) if v is not None else None
        rows[int(slot)] = r
    for c in cols:
        num = sum(r["n"] * r[c] for r in rows.values() if r.get(c) is not None)
        den = sum(r["n"] for r in rows.values() if r.get(c) is not None)
        wavg[c] = round(num / den, 4) if den else None
    return {"per_slot": rows, "weighted_mean_spearman": wavg}


def lift(d: pd.DataFrame, col: str) -> list[dict]:
    """Median realized range by quintile of the predictor, ranked *within*
    slot so the clock cannot manufacture the spread."""
    g = d.dropna(subset=[col, "range_ticks"]).copy()
    g["pct"] = g.groupby("slot")[col].rank(pct=True)
    g["norm"] = g["range_ticks"] / g.groupby("slot")["range_ticks"].transform("median")
    q = pd.cut(g["pct"], [0, .2, .4, .6, .8, 1.0],
               labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
    return [{"quintile": str(name), "n": int(len(s)),
             "median_range_ticks": round(float(s["range_ticks"].median()), 1),
             "vs_slot_median": round(float(s["norm"].median()), 3)}
            for name, s in g.groupby(q, observed=True)]


def configs(mins: int) -> dict:
    df = pd.read_parquet(f"{OUTDIR}/bars_{mins}min.parquet")
    per_hour = 60 // mins
    all_pd = 23 * per_hour                      # Globex day, minus the 17:00 halt
    rth_pd = int(round(6.5 * per_hour))         # 09:30-16:00
    if mins == 60:
        rth_pd = 6                              # the 09:00 bar is half overnight

    out = {}
    out["all"] = build_predictors(df, all_pd)
    # RTH scored against the continuous-tape ATR: same rows, diluted lookback.
    out["rth_eval"] = out["all"][out["all"]["rth"]].copy()
    # RTH with the ATR chain rebuilt over RTH bars only.
    out["rth_chain"] = build_predictors(recompute_tr(df[df["rth"]].copy()), rth_pd)
    return out


def report(mins: int) -> dict:
    cfg = configs(mins)
    res = {"interval_min": mins, "configs": {}}
    for name, d in cfg.items():
        s = score(d, f"{mins}min / {name}")
        s["within_slot"] = within_slot(d, ("atr14", "slot5", "slot_x_level"))
        s["lift_atr14"] = lift(d, "atr14")
        s["lift_best"] = None
        res["configs"][name] = s

    with open(f"{OUTDIR}/findings_{mins}min.json", "w") as f:
        json.dump(res, f, indent=2)

    for name, s in res["configs"].items():
        print(f"\n=== {s['label']} ===")
        print(f"test {s['test_span'][0]}..{s['test_span'][1]}  n={s['n_test']}  "
              f"median range={s['test_median_range_ticks']:.0f} ticks")
        print(f"{'predictor':<14}{'k':>8}{'MdAPE':>9}{'MdAE(t)':>10}{'rho':>8}"
              f"{'   ratio p10/p50/p90':>22}")
        for p, r in sorted(s["preds"].items(), key=lambda kv: kv[1]["mdape"]):
            rq = r["ratio_q"]
            print(f"{p:<14}{r['k']:>8.3f}{r['mdape']:>8.1%}{r['mdae_ticks']:>10.1f}"
                  f"{r['spearman']:>8.3f}"
                  f"   {rq['0.1']:.2f} / {rq['0.5']:.2f} / {rq['0.9']:.2f}")
        w = s["within_slot"]["weighted_mean_spearman"]
        print(f"  within-slot rho (clock removed): "
              + "  ".join(f"{k}={v}" for k, v in w.items()))
        lf = s["lift_atr14"]
        print("  atr14 quintile lift vs slot median: "
              + " ".join(f"{r['quintile']}={r['vs_slot_median']:.2f}" for r in lf))
    return res


def main() -> None:
    both = {m: report(m) for m in (60, 30)}

    print("\n\n=== HEADLINE: MdAPE by interval × config (lower better) ===")
    names = ["const", "slot_med", "prev_range", "atr14", "atr_day",
             "slot5", "slot_x_level"]
    print(f"{'predictor':<14}" + "".join(
        f"{f'{m}/{c}':>16}" for m in (60, 30)
        for c in ("all", "rth_eval", "rth_chain")))
    for p in names:
        cells = []
        for m in (60, 30):
            for c in ("all", "rth_eval", "rth_chain"):
                r = both[m]["configs"][c]["preds"].get(p)
                cells.append(f"{r['mdape']:>15.1%}" if r else f"{'-':>15}")
        print(f"{p:<14}" + " ".join(cells))
    print(f"\n→ {OUTDIR}/findings_{{60,30}}min.json")


if __name__ == "__main__":
    main()
