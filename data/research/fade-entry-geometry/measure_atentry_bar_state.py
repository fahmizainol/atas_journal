"""Recompute entry-bar geometry HONESTLY: bar state up to the entry tick only.

The stored eb_* fields use the containing bar's FINAL ohlc (post-entry included)
and are direction-signed — unusable as an entry filter. This measures, per trade:
  body-so-far (raw ticks, + = bar up so far), loc-so-far (entry px in range so
  far), elapsed (clock fraction of the bar at entry). 1m and 30s.
"""
from __future__ import annotations
import pathlib, sys
from datetime import date
from multiprocessing import Pool
import numpy as np, pandas as pd

ROOT = pathlib.Path("/home/afahmi/repos/atas_journal")
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))

from journal import trade_context as tcmod
from journal.context_store import trades_from_logical
from journal.level_store import session_day

RES = {"1m": 60_000_000_000, "30s": 30_000_000_000}

def day_job(args):
    day, rows = args
    out = []
    try:
        trades = trades_from_logical(rows)
        path = tcmod.load_path("NQ", day)
        if path is None or not path:
            return []
        tick = tcmod.tick_size_of(path.symbol or "NQ")
        for tr in trades:
            if not tcmod.trade_aligns(path, tr):
                continue
            entry_ns = tr.entry_ts.value
            i_tick = path.index_at(entry_ns)
            if i_tick < 0:
                continue
            row = dict(trade_key=tr.key)
            for suffix, res_ns in RES.items():
                bars = path.bars({"1m": "1min", "30s": "30s"}[suffix])
                if bars.empty:
                    continue
                ends = bars["end_idx"].to_numpy()
                starts = bars["start_idx"].to_numpy()
                b = int(np.searchsorted(ends, i_tick, "left"))
                if not (0 <= b < len(bars)) or i_tick < int(starts[b]):
                    continue
                s = int(starts[b])
                seg = path.px[s:i_tick + 1]
                if seg.size == 0:
                    continue
                o, hi, lo = float(seg[0]), float(seg.max()), float(seg.min())
                row[f"sofar_body_{suffix}"] = round((float(seg[-1]) - o) / tick, 3)
                row[f"sofar_loc_{suffix}"] = (round((tr.entry_px - lo) / (hi - lo), 4)
                                              if hi > lo else None)
                row[f"sofar_elapsed_{suffix}"] = round(
                    min(1.0, max(0.0, (entry_ns - int(path.ns[s])) / res_ns)), 4)
            out.append(row)
    except Exception as e:
        return [dict(trade_key=f"__err_{day}", err=repr(e))]
    return out

def main():
    from api.scope import default_scope
    import sqlite3
    tf = default_scope().filtered_all
    con = sqlite3.connect(str(ROOT / "data/journal.db"))
    have_ctx = {r[0] for r in con.execute("select trade_key from trade_context")}
    tf = tf[tf.logical_trade_key.isin(have_ctx)]
    rows = tf.rename(columns={"logical_trade_key": "trade_key"}).to_dict("records")
    by_day: dict[date, list] = {}
    for r in rows:
        by_day.setdefault(session_day(r["entry_ts_utc"]), []).append(r)
    jobs = sorted(by_day.items())
    print(f"{len(rows)} trades over {len(jobs)} session days", flush=True)
    with Pool(8) as pool:
        chunks = list(pool.imap_unordered(day_job, jobs))
    flat = [r for ch in chunks for r in ch]
    errs = [r for r in flat if r["trade_key"].startswith("__err")]
    good = [r for r in flat if not r["trade_key"].startswith("__err")]
    print(f"measured {len(good)}, day errors {len(errs)}")
    for e in errs[:5]: print(" ", e)
    pd.DataFrame(good).to_pickle(
        "/tmp/claude-1000/-home-afahmi-repos-atas-journal/b3912760-028a-4e77-b4b4-1afab150e267/scratchpad/honest_eb.pkl")

if __name__ == "__main__":
    main()
