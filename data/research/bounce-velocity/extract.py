"""Stage 1: per-trade windowed path features — how FAST the bounce off dev1 was.

The landing-depth study measured how *deep* winners dip below dev1 and never
looked at the clock. The engine's ``recovery_s`` measures the clock but only
from the whole trade's extreme to breakeven, which is outcome-contaminated
(a loser that dies underwater has recovery_s = NaN by construction, so
"fast recovery wins" is a tautology, not a finding).

This extracts a strictly CAUSAL read instead: inside a fixed window of W
seconds after the fill, using only ticks in that window, record the dip, the
climb-out, and the speed of it. The outcome is then measured on what happens
AFTER the window closes. Because the fill sits at dev1 exactly
(entry_variant A, entry_limit_offset_ticks 0), signed distance from
``avg_entry`` IS distance from the level.

Reuses the tick-array rebuild + splice auto-detection from
triple-barrier-driftfade/meta_features.py.

    python data/research/bounce-velocity/extract.py [SLUG] [RUN]

Writes windows__<run>.parquet next to this script.
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
from windows import path_features

from journal.config import point_value, tick_size
from journal.sim import ticks as tickmod

SLUG = sys.argv[1] if len(sys.argv) > 1 else "vwap-upper-band-bounce"
RUN = sys.argv[2] if len(sys.argv) > 2 else "20240303-20260630-v15-95473ae3"
BASE = f"data/sims/{SLUG}/{RUN}"
OUT = f"data/research/bounce-velocity/windows__{RUN}.parquet"

WINDOWS = (30, 60, 120)      # seconds after the fill
FIRST_UP = (5, 10, 20)       # ticks in favour — time-to-reach, censored at CENSOR_S
FIRST_DN = (10, 20, 30)      # ticks against
CENSOR_S = 300.0


def _rth(day):
    sym = tickmod.contract_for_cached("NQ", day)
    return None if sym is None else tickmod.cached_rth(sym, day)


def _on_plus_rth(day):
    sym = tickmod.contract_for_cached("NQ", day)
    if sym is None:
        return None
    rth = tickmod.cached_rth(sym, day)
    if rth is None or rth.empty:
        return None
    on = tickmod.cached_overnight(sym, day)
    if on is None or on.empty:
        return rth.reset_index(drop=True)
    return pd.concat([on, rth], ignore_index=True)


def detect_splice(tr):
    """Which array the run's entry_idx indexes into — RTH-only or overnight+RTH.

    Decided empirically: the right array is the one whose price at entry_idx
    reproduces avg_entry. An off-by-a-segment index silently reads a different
    part of the day, so this is checked, never assumed.
    """
    resid = {"rth": [], "on": []}
    for _, t in tr.head(20).iterrows():
        day = pd.Timestamp(t.session).date()
        ei = int(t.entry_idx)
        for mode, fn in (("rth", _rth), ("on", _on_plus_rth)):
            arr = fn(day)
            if arr is not None and ei < len(arr):
                resid[mode].append(abs(float(arr["price"].iloc[ei]) - float(t.avg_entry)))
    med = {m: (np.median(v) if v else np.inf) for m, v in resid.items()}
    mode = "rth" if med["rth"] <= med["on"] else "on"
    print(f"splice mode: {mode} (rth={med['rth']:.3f} on={med['on']:.3f})", flush=True)
    return mode


def main():
    tr = pd.read_parquet(f"{BASE}/trades.parquet").reset_index(drop=True)
    print(f"{SLUG}/{RUN}: {len(tr)} trades", flush=True)
    splice = detect_splice(tr)
    load = _on_plus_rth if splice == "on" else _rth

    tick = tick_size("NQ")
    pval = point_value("NQ")
    rows, day_cache = [], (None, None)
    for _, t in tr.iterrows():
        day = pd.Timestamp(t.session).date()
        if day_cache[0] != day:
            arr = load(day)
            day_cache = (day, None if arr is None else arr.reset_index(drop=True))
        arr = day_cache[1]
        if arr is None:
            continue
        ei, xi = int(t.entry_idx), int(t.exit_idx)
        if ei >= len(arr):
            continue
        xi = min(xi, len(arr) - 1)
        s = 1.0 if str(t.direction).lower().startswith("l") else -1.0
        entry_px = float(t.avg_entry)
        stop_pts = abs(entry_px - float(t.stop_price)) if pd.notna(t.stop_price) else np.nan

        # The trade's own path, entry tick -> exit tick, in ticks and seconds.
        px = arr["price"].to_numpy(dtype="float64")[ei:xi + 1]
        ts = arr["ts_utc"].iloc[ei:xi + 1]
        sec = (ts - ts.iloc[0]).dt.total_seconds().to_numpy()
        sig = s * (px - entry_px) / tick          # signed ticks from the level

        rec = {
            "trade_no": int(t.trade_no), "session": t.session,
            "direction": t.direction,
            "entry_hour_et": pd.Timestamp(t.entry_ts_local).hour,
            "duration_s": float(t.duration_s),
            "r_multiple": float(t.r_multiple), "net_pnl": float(t.net_pnl),
            "points": float(t.points), "exit_reason": t.exit_reason,
            "band_width_ticks": float(t.band_width_ticks) if pd.notna(t.band_width_ticks) else np.nan,
            "mae_ticks": -float(t.mae_points) / tick,   # depth below dev1, >= 0
            "mfe_ticks": float(t.mfe_points) / tick,
            "recovery_s": float(t.recovery_s) if pd.notna(t.recovery_s) else np.nan,
            "stop_pts": stop_pts,
            "contracts": int(t.max_contracts),
            "commission": float(t.commission),
            "n_ticks": int(len(sig)),
        }

        rec.update(path_features(
            sig, sec, windows=WINDOWS, first_up=FIRST_UP, first_dn=FIRST_DN,
            censor_s=CENSOR_S, tick=tick, pval=pval,
            contracts=int(t.max_contracts), commission=float(t.commission),
            duration_s=float(t.duration_s), stop_pts=stop_pts))
        rows.append(rec)

    df = pd.DataFrame(rows)
    df.to_parquet(OUT)
    print(f"wrote {OUT}: {len(df)} rows, {len(df.columns)} cols", flush=True)
    for w in WINDOWS:
        d, v = df[f"w{w}_depth"], df[f"w{w}_vel"]
        print(f"  w{w}: open at close {df[f'w{w}_open'].mean():.0%} | "
              f"dipped {(d > 0).mean():.0%} | recovered-in-window {v.notna().mean():.0%} | "
              f"med vel {v.median():.2f} t/s", flush=True)


if __name__ == "__main__":
    main()
