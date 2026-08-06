"""Stage 2a: build a leakage-safe entry-time feature matrix for a meta-label.

Every feature is computable strictly from data available AT OR BEFORE the entry
tick — the whole game here is leakage discipline (the market-structure study got
burned by an index-base lookahead). Tape features use the CANONICAL aggressor
sign: A = buy (ask-lift), B = sell (bid-hit), so buy-positive CVD = A - B. (Note:
extract_loser.py used the flipped sign; see the tick-aggressor-side memory.)

Target = the engine's OWN realized outcome (eng_loss = net_pnl <= 0). Stage 1
showed a tight-barrier target is contraindicated, so the meta-label predicts the
strategy's actual losers, to be skipped.

Reuses the exact read-only tick-array rebuild + splice auto-detection from
relabel.py (kept self-contained here so the script runs standalone).

    python data/research/triple-barrier-driftfade/meta_features.py [SLUG] [RUN]

Writes meta_features__<slug>.parquet next to this script.
"""
import sys
from datetime import timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod

SLUG = sys.argv[1] if len(sys.argv) > 1 else "vwap-upper-band-bounce"
RUN = sys.argv[2] if len(sys.argv) > 2 else "20250201-20260630-v13-a348d176"
BASE = f"data/sims/{SLUG}/{RUN}"
OUT = f"data/research/triple-barrier-driftfade/meta_features__{SLUG}.parquet"


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


def tape(prefix, mask, sd, sz, rec, dur_s):
    """Trailing-window tape features. sd is buy-positive signed size (A - B)."""
    n = int(mask.sum())
    vol = int(sz[mask].sum()) if n else 0
    rec[f"{prefix}_vol"] = vol
    rec[f"{prefix}_ntr"] = n
    rec[f"{prefix}_volrate"] = vol / max(dur_s, 1e-9)
    rec[f"{prefix}_cvd"] = float(sd[mask].sum()) if n else 0.0
    rec[f"{prefix}_cvdpv"] = (float(sd[mask].sum()) / vol) if vol else 0.0  # per-vol imbalance
    big = mask & (sz >= 10)
    bv = int(sz[big].sum()) if big.any() else 0
    rec[f"{prefix}_big_part"] = (bv / vol) if vol else 0.0
    rec[f"{prefix}_big_cvd"] = float(sd[big].sum()) if big.any() else 0.0
    rec[f"{prefix}_maxsz"] = int(sz[mask].max()) if n else 0


def main():
    tr = pd.read_parquet(f"{BASE}/trades.parquet").reset_index(drop=True)
    print(f"{SLUG}/{RUN}: {len(tr)} trades", flush=True)
    splice = detect_splice(tr)

    rows = []
    cache = {}
    for _, t in tr.iterrows():
        day = pd.Timestamp(t.session).date()
        if day not in cache:
            walk = _on_plus_rth(day) if splice == "on" else _rth(day)
            vol = walk if splice == "on" else _on_plus_rth(day)
            if vol is None or (hasattr(vol, "empty") and vol.empty):
                vol = walk
            cache[day] = (None if walk is None else walk.reset_index(drop=True),
                          None if vol is None else vol.reset_index(drop=True))
        walk, varr = cache[day]
        if walk is None:
            continue
        ei = int(t.entry_idx)
        entry_px = float(t.avg_entry)
        entry_ts = pd.Timestamp(t.entry_ts_utc)
        rth_open = tickmod.session_bounds_utc(day)[0]

        # --- session context up to entry (leakage-safe), from the aligned array ---
        wts = walk["ts_utc"]
        wpx = walk["price"].to_numpy(dtype="float64")
        in_sess = (wts >= rth_open).to_numpy() & (np.arange(len(wpx)) <= ei)
        seg = wpx[in_sess]
        if len(seg):
            run_hi, run_lo = float(seg.max()), float(seg.min())
        else:
            run_hi = run_lo = entry_px
        rng = max(run_hi - run_lo, 1e-9)

        # --- momentum into entry, from the vol array by timestamp ---
        vts = varr["ts_utc"]
        vpx = varr["price"].to_numpy(dtype="float64")
        vsz = varr["size"].to_numpy(dtype="int64")
        side = varr["side"].to_numpy(dtype="U1")
        sd = np.where(side == "A", vsz, 0) - np.where(side == "B", vsz, 0)  # buy-positive

        def px_at(delta_min):
            j = int(vts.searchsorted(entry_ts - timedelta(minutes=delta_min), side="right")) - 1
            return float(vpx[j]) if j >= 0 else entry_px

        rec = {
            "trade_no": int(t.trade_no), "session": t.session,
            "eng_net": float(t.net_pnl), "eng_r": float(t.r_multiple),
            "eng_loss": float(t.net_pnl) <= 0.0,          # TARGET
            "eng_exit_reason": t.exit_reason,
            # config / structure known at entry
            "entry_hour_et": pd.Timestamp(t.entry_ts_local).hour,
            "dow": pd.Timestamp(t.session).weekday(),
            "band_width_ticks": float(t.get("band_width_ticks")) if pd.notna(t.get("band_width_ticks")) else np.nan,
            "stop_pts": abs(entry_px - float(t.stop_price)) if pd.notna(t.get("stop_price")) else np.nan,
            "tgt_pts": abs(float(t.target_price) - entry_px) if pd.notna(t.get("target_price")) else np.nan,
            # where in the day's range the entry sits, and the trailing pullback
            "pos_in_range": (entry_px - run_lo) / rng,
            "dist_hi_pts": entry_px - run_hi,             # <= 0
            "range_pts": run_hi - run_lo,
            "ret_5m": entry_px - px_at(5),
            "ret_30m": entry_px - px_at(30),
        }
        for pre, dm in (("t60", 1), ("t300", 5)):
            lo = entry_ts - timedelta(minutes=dm)
            m = ((vts >= lo) & (vts < entry_ts)).to_numpy()
            tape(pre, m, sd, vsz, rec, dm * 60)
        rows.append(rec)

    df = pd.DataFrame(rows)
    df.to_parquet(OUT)
    feat_cols = [c for c in df.columns if c not in
                 ("trade_no", "session", "eng_net", "eng_r", "eng_loss", "eng_exit_reason")]
    print(f"wrote {OUT}: {len(df)} rows, {len(feat_cols)} features", flush=True)
    print(f"loss rate: {df.eng_loss.mean():.3f} ({int(df.eng_loss.sum())} losers)", flush=True)


if __name__ == "__main__":
    main()
