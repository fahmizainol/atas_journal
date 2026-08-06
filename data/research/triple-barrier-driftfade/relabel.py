"""Stage 1 of the triple-barrier study: relabel a strategy's trades under
volatility-scaled first-touch barriers, independent of the engine's own exit.

The journal scores a trade by MFE/MAE over the engine's *actual* holding window
(entry_idx -> exit_idx). That label is path-blind about a counterfactual stop:
a trade that dips 40pt, stops a real position, then recovers to +80 still scores
as a big win. Triple-barrier (Lopez de Prado) relabels by whichever of three
barriers price touches FIRST from entry, forward: a profit barrier, a stop
barrier, and a vertical (time) barrier. Barriers here are symmetric multiples of
a per-trade realized-volatility unit, so they are wide on a fast day and tight at
lunch. One `eng_match` config mirrors the engine's own stop/target distances,
capped at the engine's actual exit, as a sanity anchor (its label should agree
with the engine outcome; the gaps are trailing stops / partial fills).

Pure offline relabel: no engine run. It rebuilds the *exact* tick array the engine
traded, read-only via the cached_* readers (never buys), so `entry_idx` aligns.
The splice mode differs by strategy — drift-fade (session="globex") splices the
overnight in front of RTH; the RTH-only strategies do not — so it is auto-detected
per run by whichever array minimises |price[entry_idx] - avg_entry|.

    python data/research/triple-barrier-driftfade/relabel.py [SLUG] [RUN]

Writes relabeled__<slug>.parquet next to this script.
"""
import sys
from datetime import timedelta

sys.path.insert(0, "src")
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod

SLUG = sys.argv[1] if len(sys.argv) > 1 else "drift-touch-fade-entry-stop"
RUN = sys.argv[2] if len(sys.argv) > 2 else "20250203-20260630-v2-b0c570aa"
BASE = f"data/sims/{SLUG}/{RUN}"
OUT = f"data/research/triple-barrier-driftfade/relabeled__{SLUG}.parquet"

VOL_WINDOW_MIN = 30      # trailing window for the realized-vol unit
VOL_WINDOW_WIDE = 60     # fallback if the 30-min window is too thin
VOL_MIN_RETURNS = 5      # need at least this many 1-min returns to trust sigma

CONFIGS = [
    {"name": "tb_1.0_60", "k": 1.0, "h_min": 60},
    {"name": "tb_1.5_60", "k": 1.5, "h_min": 60},
    {"name": "tb_2.0_60", "k": 2.0, "h_min": 60},
    {"name": "tb_2.0_30", "k": 2.0, "h_min": 30},
    {"name": "tb_2.0_120", "k": 2.0, "h_min": 120},
    {"name": "eng_match", "k": None, "h_min": None},  # engine stop/target, exit cap
]


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


def rebuild(day, splice):
    """The engine's tick array for one session, read-only. `splice` is 'on' (drift
    -fade: overnight ++ RTH) or 'rth' (RTH-only). Returns None if not cached."""
    arr = _on_plus_rth(day) if splice == "on" else _rth(day)
    if arr is None or arr.empty:
        return None
    return arr.reset_index(drop=True)


def detect_splice(tr):
    """Pick the tick-array layout `entry_idx` indexes into, by whichever gives the
    smaller median |price[entry_idx] - avg_entry| over a sample of trades."""
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
    print(f"splice mode: {mode}  (median |price[ei]-avg_entry|: "
          f"rth={med['rth']:.3f} on={med['on']:.3f})", flush=True)
    return mode


def realized_vol_pts(ts, price, entry_ts, entry_px):
    """Points-stdev of 1-min log returns over the trailing window before entry.
    Falls back to a wider window, then returns (nan, 'none')."""
    for win, tag in ((VOL_WINDOW_MIN, "30m"), (VOL_WINDOW_WIDE, "60m")):
        lo = entry_ts - timedelta(minutes=win)
        m = (ts >= lo) & (ts < entry_ts)
        if m.sum() < 2:
            continue
        s = pd.Series(price[m.to_numpy()], index=ts[m])
        closes = s.resample("1min").last().dropna()
        if len(closes) < VOL_MIN_RETURNS + 1:
            continue
        lr = np.diff(np.log(closes.to_numpy()))
        sd = float(np.std(lr, ddof=1))
        if sd > 0:
            return entry_px * sd, tag
    return float("nan"), "none"


def first_touch(seg_px, seg_ts, entry_px, entry_ts, up, dn, is_long):
    """Walk the forward tick path; return (label, secs_to_touch, mfe, mae).

    label: win | loss | time_pos | time_neg | empty. A single tick price cannot
    cross both barriers (up > entry > dn), so first-touch is unambiguous at tick
    granularity. Time-barrier trades are marked to the last tick's directional sign.
    """
    if len(seg_px) == 0:
        return "empty", float("nan"), 0.0, 0.0
    if is_long:
        prof_hit = seg_px >= up
        stop_hit = seg_px <= dn
        mfe = float(seg_px.max() - entry_px)
        mae = float(seg_px.min() - entry_px)
    else:
        prof_hit = seg_px <= dn
        stop_hit = seg_px >= up
        mfe = float(entry_px - seg_px.min())
        mae = float(entry_px - seg_px.max())
    i_prof = int(np.argmax(prof_hit)) if prof_hit.any() else None
    i_stop = int(np.argmax(stop_hit)) if stop_hit.any() else None
    if i_prof is None and i_stop is None:
        last = seg_px[-1]
        signed = (last - entry_px) if is_long else (entry_px - last)
        return ("time_pos" if signed > 0 else "time_neg"), float("nan"), mfe, mae
    if i_stop is None or (i_prof is not None and i_prof <= i_stop):
        return "win", float((seg_ts[i_prof] - entry_ts).total_seconds()), mfe, mae
    return "loss", float((seg_ts[i_stop] - entry_ts).total_seconds()), mfe, mae


def main():
    tr = pd.read_parquet(f"{BASE}/trades.parquet").reset_index(drop=True)
    print(f"{SLUG}/{RUN}: {len(tr)} trades", flush=True)
    splice = detect_splice(tr)

    rows = []
    cache = {}
    for _, t in tr.iterrows():
        day = pd.Timestamp(t.session).date()
        if day not in cache:
            # walk_arr: the index-aligned array for the FORWARD barrier walk.
            # vol_arr: always overnight ++ RTH, for the BACKWARD trailing-vol
            # window — an RTH-only strategy has no pre-09:30 history otherwise, so
            # its morning entries would drop out of the vol estimate entirely.
            walk = rebuild(day, splice)
            if splice == "on":
                vol = walk
            else:
                vol = _on_plus_rth(day)
                if vol is None or vol.empty:
                    vol = walk
            cache[day] = (walk, vol)
        arr, varr = cache[day]
        if arr is None:
            print(f"  SKIP {day}: no cached ticks", flush=True)
            continue
        ts = arr["ts_utc"]
        price = arr["price"].to_numpy(dtype="float64")
        vts = varr["ts_utc"]
        vprice = varr["price"].to_numpy(dtype="float64")
        ei = int(t.entry_idx)
        xi = int(t.exit_idx)
        entry_px = float(t.avg_entry)
        entry_ts = pd.Timestamp(t.entry_ts_utc)
        exit_ts = pd.Timestamp(t.exit_ts_utc)
        is_long = t.direction == "Long"
        rth_close = tickmod.session_bounds_utc(day)[1]

        # Mechanical validation of the walk: recompute MFE/MAE over the engine's
        # own hold [entry_idx, exit_idx] and compare to the stored excursions.
        # (For strategies that pyramid, avg_entry is a blended fill, so a small
        # residual vs the engine's excursion is expected — reported by analyze.)
        hold = price[ei: xi + 1]
        if is_long:
            chk_mfe = float(hold.max() - entry_px)
            chk_mae = float(hold.min() - entry_px)
        else:
            chk_mfe = float(entry_px - hold.min())
            chk_mae = float(entry_px - hold.max())

        sigma, vsrc = realized_vol_pts(vts, vprice, entry_ts, entry_px)
        eng_stop_pts = (abs(entry_px - float(t.stop_price))
                        if pd.notna(t.get("stop_price")) else float("nan"))
        eng_tgt_pts = (abs(float(t.target_price) - entry_px)
                       if pd.notna(t.get("target_price")) else float("nan"))

        rec = {
            "trade_no": int(t.trade_no), "session": t.session,
            "direction": t.direction, "entry_ts_utc": entry_ts,
            "entry_ts_local": t.entry_ts_local,
            "entry_hour_et": pd.Timestamp(t.entry_ts_local).hour,
            "avg_entry": entry_px, "entry_reason": t.get("entry_reason"),
            "band_width_ticks": t.get("band_width_ticks"),
            "eng_exit_reason": t.exit_reason, "eng_r": float(t.r_multiple),
            "eng_net": float(t.net_pnl), "eng_points": float(t.points),
            "eng_mfe": float(t.mfe_points), "eng_mae": float(t.mae_points),
            "sigma_pts": sigma, "sigma_src": vsrc,
            "eng_stop_pts": eng_stop_pts, "eng_tgt_pts": eng_tgt_pts,
            "chk_mfe": chk_mfe, "chk_mae": chk_mae,
        }

        for cfg in CONFIGS:
            if cfg["name"] == "eng_match":
                if not np.isfinite(eng_tgt_pts) or not np.isfinite(eng_stop_pts):
                    rec["eng_match_label"] = "na"
                    rec["eng_match_secs"] = float("nan")
                    continue
                up_d = eng_tgt_pts if is_long else eng_stop_pts
                dn_d = eng_stop_pts if is_long else eng_tgt_pts
                hz_ts = exit_ts  # same barriers, same hold — isolates trailing
            else:
                if not np.isfinite(sigma):
                    rec[f"{cfg['name']}_label"] = "novol"
                    rec[f"{cfg['name']}_secs"] = float("nan")
                    continue
                up_d = dn_d = cfg["k"] * sigma
                hz_ts = min(entry_ts + timedelta(minutes=cfg["h_min"]), rth_close)

            up = entry_px + up_d
            dn = entry_px - dn_d
            hz_idx = int(ts.searchsorted(hz_ts, side="right"))
            seg_px = price[ei + 1: hz_idx]
            seg_ts = ts.iloc[ei + 1: hz_idx].reset_index(drop=True)
            label, secs, mfe, mae = first_touch(
                seg_px, seg_ts, entry_px, entry_ts, up, dn, is_long)
            rec[f"{cfg['name']}_label"] = label
            rec[f"{cfg['name']}_secs"] = secs
            if cfg["name"] == "tb_2.0_60":
                rec["h60_mfe"] = mfe
                rec["h60_mae"] = mae

        rows.append(rec)

    df = pd.DataFrame(rows)
    n_novol = int((df.sigma_src == "none").sum())
    df.to_parquet(OUT)
    print(f"wrote {OUT}: {len(df)} rows, {n_novol} without a vol estimate", flush=True)


if __name__ == "__main__":
    main()
