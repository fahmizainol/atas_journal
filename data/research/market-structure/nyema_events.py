"""NY-upper-band × 9/20-EMA — the three study angles.

Reads nyema_minutes.parquet. Defines a band-touch event (first fresh tap of the
NY +1σ upper band from below) and measures, per touch:

  Angle 1  Confluence-as-S/R : does the 9/20 EMA sitting AT the band (vs well
           below it) change whether price continues up (to +2σ) or fades back?
  Angle 2  EMA-cross timing   : where do 9/20 crosses happen relative to the band,
           and does a bull cross below the band precede a band break?
  Angle 3  EMA-slope filter   : at the touch, does EMA state (stacked gap, slope,
           price-stretch) separate follow-through from fade?

Forward outcome is signed excursion in TICKS over 15/30/60-minute horizons, plus
whether price tags the +2σ target (continuation) before falling back to the mid
(fail), all measured within the same session on the 1-minute grid.

    Usage: .venv/bin/python data/research/market-structure/nyema_events.py
"""
import sys
sys.path.insert(0, "src")
import numpy as np
import pandas as pd

TICK = 0.25
OUTDIR = "data/research/market-structure"
HORIZONS = [15, 30, 60]


WARMUP_MIN = 15   # NY σ is degenerate in the first minutes; drop for the event study


def load():
    df = pd.read_parquet(f"{OUTDIR}/nyema_minutes.parquet")
    df = df[df["minute_idx"] >= WARMUP_MIN]
    return df.sort_values(["session", "minute_idx"]).reset_index(drop=True)


def with_forward(df):
    """Per-minute forward excursions + target/fail flags, within session."""
    out = []
    for _, g in df.groupby("session", sort=False):
        g = g.reset_index(drop=True)
        close = g["close"].to_numpy(); high = g["high"].to_numpy()
        low = g["low"].to_numpy(); up2 = g["upper2"].to_numpy(); mid = g["mid"].to_numpy()
        n = len(g)
        for h in HORIZONS:
            mu = np.full(n, np.nan); md = np.full(n, np.nan)
            for i in range(n):
                j = min(i + h, n - 1)
                if j <= i:
                    continue
                mu[i] = (high[i+1:j+1].max() - close[i]) / TICK
                md[i] = (low[i+1:j+1].min() - close[i]) / TICK
            g[f"fmax_up_{h}"] = mu
            g[f"fmax_dn_{h}"] = md
        # target (+2σ tag) vs fail (revert to mid) — which comes first, 60m window
        reach = np.full(n, np.nan)
        for i in range(n):
            j = min(i + 60, n - 1)
            hit_t = np.nan; hit_f = np.nan
            for k in range(i+1, j+1):
                if np.isnan(hit_t) and high[k] >= up2[i]:
                    hit_t = k
                if np.isnan(hit_f) and low[k] <= mid[i]:
                    hit_f = k
                if not np.isnan(hit_t) or not np.isnan(hit_f):
                    break
            if not np.isnan(hit_t) and (np.isnan(hit_f) or hit_t <= hit_f):
                reach[i] = 1.0
            elif not np.isnan(hit_f):
                reach[i] = 0.0
        g["cont_to_up2"] = reach   # 1=tagged +2σ first, 0=reverted to mid first, nan=neither
        out.append(g)
    return pd.concat(out, ignore_index=True)


def touch_events(df):
    """Fresh tap of +1σ from below: prev minute closed below the band, this
    minute's high reaches it. One row per touch (dedup consecutive within 5 min)."""
    ev = []
    for _, g in df.groupby("session", sort=False):
        g = g.reset_index(drop=True)
        up1 = g["upper1"].to_numpy(); high = g["high"].to_numpy(); close = g["close"].to_numpy()
        last = -999
        for i in range(1, len(g)):
            if high[i] >= up1[i] and close[i-1] < up1[i-1] and (i - last) >= 5:
                ev.append(g.iloc[i]); last = i
    return pd.DataFrame(ev).reset_index(drop=True)


def summ(label, s):
    s = s.dropna()
    if len(s) == 0:
        return f"  {label:<34} n=0"
    return (f"  {label:<34} n={len(s):>5}  mean={s.mean():>7.1f}  "
            f"med={s.median():>7.1f}  p25={s.quantile(.25):>7.1f}  p75={s.quantile(.75):>7.1f}")


def rate(label, s):
    s = s.dropna()
    if len(s) == 0:
        return f"  {label:<34} n=0"
    return f"  {label:<34} n={len(s):>5}  cont_to_up2={s.mean()*100:>5.1f}%"


def main():
    df = load()
    print(f"loaded {len(df)} minute-rows, {df['session'].nunique()} sessions")
    df = with_forward(df)

    # baseline: every warmed RTH minute
    print("\n=== BASELINE (all RTH minutes, minute_idx>=15) ===")
    for h in HORIZONS:
        print(summ(f"fwd max-up {h}m (ticks)", df[f"fmax_up_{h}"]))
        print(summ(f"fwd max-dn {h}m (ticks)", df[f"fmax_dn_{h}"]))
    print(rate("unconditional cont→+2σ vs mid", df["cont_to_up2"]))

    ev = touch_events(df)
    print(f"\n=== BAND-TOUCH EVENTS: {len(ev)} fresh +1σ taps "
          f"({len(ev)/df['session'].nunique():.1f}/session) ===")
    print(rate("all touches cont→+2σ", ev["cont_to_up2"]))
    for h in HORIZONS:
        print(summ(f"touch fwd max-up {h}m", ev[f"fmax_up_{h}"]))
        print(summ(f"touch fwd max-dn {h}m", ev[f"fmax_dn_{h}"]))

    # ---- Angle 1: confluence as S/R (EMA distance to the band) ----
    print("\n=== ANGLE 1 — EMA-band confluence (d_ema9_up1 = ema9−band, ticks) ===")
    print("  (negative = fast EMA below the band = price stretched above both)")
    q = ev["d_ema9_up1"]
    at = ev[q.abs() <= 8]            # fast EMA hugging the band (±2 pts)
    below = ev[q < -8]              # EMA well below band = price stretched
    for name, sub in [("EMA9 at band (±8t)", at), ("EMA9 >8t below band", below)]:
        print(rate(name, sub["cont_to_up2"]))
        print(summ(f"  {name} max-up 30m", sub["fmax_up_30"]))

    # ---- Angle 3: slope / stacking filter ----
    print("\n=== ANGLE 3 — EMA state at the touch ===")
    stacked = ev[ev["ema_gap"] > 0]
    inv = ev[ev["ema_gap"] <= 0]
    print(rate("stacked bull (ema9>ema20)", stacked["cont_to_up2"]))
    print(rate("inverted   (ema9<=ema20)", inv["cont_to_up2"]))
    rising = ev[ev["ema9_slope"] > 0]
    falling = ev[ev["ema9_slope"] <= 0]
    print(rate("ema9 rising", rising["cont_to_up2"]))
    print(rate("ema9 falling/flat", falling["cont_to_up2"]))
    # combined: stacked & rising vs not
    good = ev[(ev["ema_gap"] > 0) & (ev["ema9_slope"] > 0)]
    bad = ev[(ev["ema_gap"] <= 0) | (ev["ema9_slope"] <= 0)]
    print(rate("stacked & rising", good["cont_to_up2"]))
    print(rate("not (stacked&rising)", bad["cont_to_up2"]))
    print(summ("  stacked&rising max-up 30m", good["fmax_up_30"]))
    print(summ("  not             max-up 30m", bad["fmax_up_30"]))

    # ---- stretch: price extension over fast EMA at the touch ----
    print("\n=== stretch9 (close−ema9, ticks) tertiles at touch ===")
    st = ev.dropna(subset=["stretch9"]).copy()
    if len(st):
        st["bin"] = pd.qcut(st["stretch9"], 3, labels=["low", "mid", "high"], duplicates="drop")
        for b, sub in st.groupby("bin", observed=True):
            print(rate(f"stretch {b} (med {sub['stretch9'].median():.0f}t)", sub["cont_to_up2"]))
            print(summ(f"  stretch {b} max-dn 30m", sub["fmax_dn_30"]))

    # ---- Angle 2: 9/20 cross timing relative to the band ----
    print("\n=== ANGLE 2 — 9/20 bull crosses (ema_gap turns >0) ===")
    cr = []
    for _, g in df.groupby("session", sort=False):
        g = g.reset_index(drop=True)
        gap = g["ema_gap"].to_numpy()
        for i in range(1, len(g)):
            if gap[i] > 0 and gap[i-1] <= 0:
                cr.append(g.iloc[i])
    cr = pd.DataFrame(cr).reset_index(drop=True)
    print(f"  {len(cr)} bull crosses ({len(cr)/df['session'].nunique():.1f}/session)")
    print(summ("  price-to-band at cross (ticks)", cr["d_px_up1"]))
    print(summ("  price-to-band at cross (σ)", cr["d_px_up1_sig"]))
    below = cr[cr["d_px_up1"] < 0]          # cross fires while price still under the band
    above = cr[cr["d_px_up1"] >= 0]
    print(rate("bull cross BELOW band → cont→+2σ", below["cont_to_up2"]))
    print(rate("bull cross ABOVE band → cont→+2σ", above["cont_to_up2"]))
    print(summ("  cross-below fwd max-up 60m", below["fmax_up_60"]))
    print(summ("  cross-above fwd max-up 60m", above["fmax_up_60"]))

    ev.to_parquet(f"{OUTDIR}/nyema_events.parquet", index=False)
    print(f"\nwrote {OUTDIR}/nyema_events.parquet ({len(ev)} events)")


if __name__ == "__main__":
    main()
