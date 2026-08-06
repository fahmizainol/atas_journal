"""NY-upper-band × RSI — RSI state at the +1σ band-touch events.

Reads nyema_minutes.parquet (bands, forward outcomes) + rsi_minutes.parquet and
reuses the nyema_events machinery (same touch definition, same cont→+2σ-vs-mid
outcome). Angles:

  Angle 1  Level    : classic RSI bins at the touch (<50 … ≥80) — does an
           "overbought" tap fade (textbook read) or continue (house day-with prior)?
  Angle 2  Slope    : RSI14 rising vs falling into the touch.
  Angle 3  Divergence: consecutive touch pairs where price made a HIGHER high —
           RSI lower (bearish divergence) vs RSI confirming. Higher-high held
           constant so divergence isn't conflated with "made a higher high".
  Extra    RSI(2) Connors extreme (≥90) — the classic short-term mean-reversion cut.

    Usage: .venv/bin/python data/research/market-structure/rsi_events.py
"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "data/research/market-structure")
import numpy as np
import pandas as pd

import nyema_events as ne

OUTDIR = "data/research/market-structure"
PAIR_MAX_GAP_MIN = 90   # touch pairs further apart than this aren't one structure


def main():
    df = ne.load()
    rsi = pd.read_parquet(f"{OUTDIR}/rsi_minutes.parquet")
    df = df.merge(
        rsi[["session", "ts_utc", "rsi14", "rsi5", "rsi2", "rsi14_slope"]],
        on=["session", "ts_utc"], how="left")
    print(f"loaded {len(df)} minute-rows, {df['session'].nunique()} sessions, "
          f"rsi14 non-null {df['rsi14'].notna().mean()*100:.1f}%")
    df = ne.with_forward(df)

    print("\n=== unconditional RSI14 distribution (warmed RTH minutes) ===")
    print(df["rsi14"].describe().round(1).to_string())
    print(ne.rate("unconditional cont→+2σ vs mid", df["cont_to_up2"]))

    ev = ne.touch_events(df)
    print(f"\n=== BAND-TOUCH EVENTS: {len(ev)} fresh +1σ taps ===")
    print(ne.rate("all touches cont→+2σ", ev["cont_to_up2"]))
    print(ne.summ("rsi14 at touch", ev["rsi14"]))

    # ---- Angle 1: classic RSI level bins ----
    print("\n=== ANGLE 1 — RSI14 level at the touch ===")
    bins = [(-1, 50, "rsi14 < 50"), (50, 60, "rsi14 50-60"), (60, 70, "rsi14 60-70"),
            (70, 80, "rsi14 70-80"), (80, 101, "rsi14 >= 80")]
    for lo, hi, name in bins:
        sub = ev[(ev["rsi14"] > lo) & (ev["rsi14"] <= hi)] if lo >= 0 else ev[ev["rsi14"] <= hi]
        print(ne.rate(name, sub["cont_to_up2"]))
        print(ne.summ(f"  {name} max-up 30m", sub["fmax_up_30"]))
        print(ne.summ(f"  {name} max-dn 30m", sub["fmax_dn_30"]))
    ob = ev[ev["rsi14"] >= 70]
    nob = ev[ev["rsi14"] < 70]
    print(ne.rate("overbought (>=70)", ob["cont_to_up2"]))
    print(ne.rate("not overbought (<70)", nob["cont_to_up2"]))
    t = ev.dropna(subset=["rsi14"]).copy()
    t["bin"] = pd.qcut(t["rsi14"], 3, labels=["low", "mid", "high"], duplicates="drop")
    for b, sub in t.groupby("bin", observed=True):
        print(ne.rate(f"rsi14 tertile {b} (med {sub['rsi14'].median():.0f})", sub["cont_to_up2"]))

    # ---- Angle 2: RSI slope into the touch ----
    print("\n=== ANGLE 2 — RSI14 slope (5-min delta) at the touch ===")
    print(ne.rate("rsi14 rising", ev[ev["rsi14_slope"] > 0]["cont_to_up2"]))
    print(ne.rate("rsi14 falling/flat", ev[ev["rsi14_slope"] <= 0]["cont_to_up2"]))

    # ---- Angle 3: touch-to-touch bearish divergence ----
    print("\n=== ANGLE 3 — divergence on consecutive higher-high touch pairs ===")
    pairs = []
    for _, g in ev.groupby("session", sort=False):
        g = g.sort_values("minute_idx").reset_index(drop=True)
        for i in range(1, len(g)):
            gap = g["minute_idx"].iloc[i] - g["minute_idx"].iloc[i - 1]
            if gap > PAIR_MAX_GAP_MIN:
                continue
            row = g.iloc[i].copy()
            row["prev_high"] = g["high"].iloc[i - 1]
            row["prev_rsi14"] = g["rsi14"].iloc[i - 1]
            row["pair_gap_min"] = gap
            pairs.append(row)
    pr = pd.DataFrame(pairs).reset_index(drop=True)
    pr = pr.dropna(subset=["rsi14", "prev_rsi14"])
    hh = pr[pr["high"] > pr["prev_high"]].copy()
    print(f"  {len(pr)} consecutive touch pairs (gap<={PAIR_MAX_GAP_MIN}m), "
          f"{len(hh)} with a higher high")
    div = hh[hh["rsi14"] < hh["prev_rsi14"]]
    conf = hh[hh["rsi14"] >= hh["prev_rsi14"]]
    print(ne.rate("HH + RSI divergent (bearish)", div["cont_to_up2"]))
    print(ne.rate("HH + RSI confirming", conf["cont_to_up2"]))
    for name, sub in [("divergent", div), ("confirming", conf)]:
        print(ne.summ(f"  {name} max-up 30m", sub["fmax_up_30"]))
        print(ne.summ(f"  {name} max-dn 30m", sub["fmax_dn_30"]))
    # deep divergence (RSI drop > 5 pts) — the chart-pattern version
    deep = hh[hh["rsi14"] < hh["prev_rsi14"] - 5]
    print(ne.rate("HH + deep divergence (>5pt drop)", deep["cont_to_up2"]))
    # lower-high control: pairs where price did NOT make a higher high
    lh = pr[pr["high"] <= pr["prev_high"]]
    print(ne.rate("control: lower-high pairs", lh["cont_to_up2"]))

    # ---- RSI(2) Connors extreme ----
    print("\n=== RSI(2) extreme at the touch ===")
    print(ne.rate("rsi2 >= 90", ev[ev["rsi2"] >= 90]["cont_to_up2"]))
    print(ne.rate("rsi2 < 90", ev[ev["rsi2"] < 90]["cont_to_up2"]))
    print(ne.rate("rsi2 >= 98 (pinned)", ev[ev["rsi2"] >= 98]["cont_to_up2"]))
    print(ne.summ("rsi2>=90 max-dn 30m", ev[ev["rsi2"] >= 90]["fmax_dn_30"]))
    print(ne.summ("rsi2<90  max-dn 30m", ev[ev["rsi2"] < 90]["fmax_dn_30"]))

    ev.to_parquet(f"{OUTDIR}/rsi_events.parquet", index=False)
    hh.to_parquet(f"{OUTDIR}/rsi_divergence_pairs.parquet", index=False)
    print(f"\nwrote {OUTDIR}/rsi_events.parquet ({len(ev)} events) + "
          f"rsi_divergence_pairs.parquet ({len(hh)} HH pairs)")


if __name__ == "__main__":
    main()
