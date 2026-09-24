"""Stage 1b: the same windowed path measurement, on MANUAL reviewed trades.

Same measurement as extract.py (shared in windows.py), different source: the
logical trades built from the ATAS journal, filtered to one review tag. There is
no ``entry_idx`` here — the trade knows only its timestamps — so the fill is
located in the day's tick array by timestamp, and the price found there is
checked against ``avg_entry`` (an alignment residual is printed; a bad roll
resolution shows up as a large one).

Manual trades are far shorter than engine trades, so the window set starts at
5s. ``stop_pts`` is unknown for a discretionary trade, so there is no R column —
ticks and dollars only. Journal PnL is ATAS's and is GROSS.

    python data/research/bounce-velocity/extract_manual.py ["Rally fade"]

Writes manual__<tag>.parquet next to this script.
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
import json

import numpy as np
import pandas as pd
from windows import path_features

from journal import db as dbmod
from journal import trades as trmod
from journal.config import ET_TZ, point_value, root_symbol, tick_size
from journal.sim import ticks as tickmod

TAG = sys.argv[1] if len(sys.argv) > 1 else "Rally fade"
OUT = f"data/research/bounce-velocity/manual__{TAG.replace(' ', '-').lower()}.parquet"

WINDOWS = (5, 10, 15, 30, 60)
FIRST_UP = (5, 10, 20)
FIRST_DN = (10, 20, 30)
CENSOR_S = 300.0
SNAP_S = 5.0        # how far either side of the recorded fill to look for it
SNAP_TICKS = 5000   # index half-width searched before the timestamp filter


def tagged_keys(conn, tag):
    out = []
    for key, tags in conn.execute("SELECT trade_key, tags_json FROM trade_notes"):
        try:
            arr = json.loads(tags or "[]")
        except json.JSONDecodeError:
            continue
        if any(str(a).strip().lower() == tag.strip().lower() for a in arr):
            out.append(key)
    return out


def main():
    conn = dbmod.connect()
    keys = tagged_keys(conn, TAG)
    logical = trmod.build_logical_trades(dbmod.load_journal(conn),
                                         dbmod.load_executions(conn))
    tr = logical[logical.trade_key.isin(keys)].reset_index(drop=True)
    print(f"tag {TAG!r}: {len(keys)} notes, {len(tr)} logical trades matched", flush=True)

    rows, resid, day_cache = [], [], (None, None)
    for _, t in tr.iterrows():
        entry_ts = pd.Timestamp(t.entry_ts_utc)
        exit_ts = pd.Timestamp(t.exit_ts_utc)
        day = entry_ts.tz_convert(ET_TZ).date()
        if day_cache[0] != day:
            sym = tickmod.contract_for_cached(root_symbol(t.instrument), day)
            arr = tickmod.cached_rth(sym, day) if sym else None
            day_cache = (day, None if arr is None else arr.reset_index(drop=True))
        arr = day_cache[1]
        if arr is None:
            print(f"  no ticks for {day} — skipped", flush=True)
            continue

        ts_all = arr["ts_utc"]
        ei = int(ts_all.searchsorted(entry_ts, side="left"))
        xi = int(ts_all.searchsorted(exit_ts, side="right")) - 1
        if ei >= len(arr) or xi <= ei:
            print(f"  {t.trade_key} outside the tape — skipped", flush=True)
            continue

        tick = tick_size(root_symbol(t.instrument))
        pval = point_value(root_symbol(t.instrument))
        entry_px = float(t.avg_entry)

        # Snap the read to the tick actually trading at the fill price. The
        # recorded instant is accurate to well under a second, but in a fast
        # tape the print at that instant can sit several ticks off the fill
        # (filled on the other side of the spread, or a limit resting through
        # the move). Reading from there would open the path already underwater
        # and invent a dip the trade never took. If no print matches inside
        # SNAP_S the raw index stands and the residual is reported.
        raw_resid = abs(float(arr["price"].iloc[ei]) - entry_px)
        near = arr.iloc[max(0, ei - SNAP_TICKS):ei + SNAP_TICKS]
        near = near[(near["ts_utc"] >= entry_ts - pd.Timedelta(seconds=SNAP_S))
                    & (near["ts_utc"] <= entry_ts + pd.Timedelta(seconds=SNAP_S))
                    & (np.isclose(near["price"], entry_px))]
        if len(near):
            off = (near["ts_utc"] - entry_ts).dt.total_seconds()
            ei = int(near.index[int(off.abs().to_numpy().argmin())])
            snap_s = float(off.iloc[int(off.abs().to_numpy().argmin())])
        else:
            snap_s = np.nan
        resid.append(raw_resid)
        if xi <= ei:
            print(f"  {t.trade_key} exit precedes snapped entry — skipped", flush=True)
            continue

        s = 1.0 if str(t.direction).lower().startswith("l") else -1.0
        px = arr["price"].to_numpy(dtype="float64")[ei:xi + 1]
        ts = ts_all.iloc[ei:xi + 1]
        sec = (ts - ts.iloc[0]).dt.total_seconds().to_numpy()
        sig = s * (px - entry_px) / tick
        realised = s * (float(t.avg_exit) - entry_px) / tick

        rec = {
            "trade_key": t.trade_key,
            "session": str(day),
            "instrument": t.instrument,
            "direction": t.direction,
            "entry_et": entry_ts.tz_convert(ET_TZ).strftime("%H:%M:%S"),
            "duration_s": float(t.duration_s),
            "align_snap_s": snap_s,
            "align_raw_resid_ticks": raw_resid / tick,
            "net_pnl": float(t.net_pnl),
            "contracts": float(t.max_contracts),
            "realised_ticks": realised,
            "mae_ticks": float(max(-sig.min(), 0.0)),   # off the tape, not the journal
            "mfe_ticks": float(max(sig.max(), 0.0)),
            "n_ticks": int(len(sig)),
        }
        rec.update(path_features(
            sig, sec, windows=WINDOWS, first_up=FIRST_UP, first_dn=FIRST_DN,
            censor_s=CENSOR_S, tick=tick, pval=pval,
            contracts=float(t.max_contracts), commission=0.0,
            duration_s=float(t.duration_s)))
        rows.append(rec)

    df = pd.DataFrame(rows).sort_values(["session", "entry_et"]).reset_index(drop=True)
    df.to_parquet(OUT)
    print(f"wrote {OUT}: {len(df)} rows", flush=True)
    print(f"entry alignment residual: med {np.median(resid):.3f}pt max {max(resid):.3f}pt", flush=True)
    print(f"win {(df.net_pnl > 0).mean():.0%}  net ${df.net_pnl.sum():,.0f}  "
          f"med duration {df.duration_s.median():.0f}s  med MAE {df.mae_ticks.median():.0f}t", flush=True)


if __name__ == "__main__":
    main()
