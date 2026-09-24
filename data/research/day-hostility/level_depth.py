"""How far price pokes through a watched level before turning back.

The second complaint — "it doesn't respect any of my watched levels" — measured
the same way as the first, and against the same suspect. A level is "respected"
only relative to how far past it you were willing to let price go, so the honest
statistic is the *penetration depth*: each time price crosses a Tier-A level,
how far beyond does it travel before returning to the level.

Reported twice: in raw ticks, and divided by the session's own vol-ruler reading.
If the raw depth is an outlier and the normalised depth is not, the level was
respected exactly as much as usual — the tolerance was the thing that was wrong.

Levels are the journal's own Tier-A set (``journal.levels``), rebuilt here from
the tick cache only so this never reaches Databento.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import pathlib
import sys
from datetime import date as date_cls, timedelta

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "data/research/replay-trail"))
sys.path.insert(0, str(ROOT / "src"))

import bracket_survival as BS  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

TICK = BS.TICK
AM_END = 12 * 3600
#: A crossing has to clear the level by this much to count as an excursion at
#: all, so a print sitting exactly on the level doesn't manufacture events.
MIN_CLEAR_T = 2


def _levels(sym: str, day: date_cls) -> dict:
    """Tier-A levels from cached ticks only — onh/onl, prior day's high/low/close."""
    out: dict[str, float] = {}
    on = tickmod.cached_overnight(sym, day)
    if on is not None and not on.empty:
        out["onh"] = float(on["price"].max())
        out["onl"] = float(on["price"].min())
    prev = day - timedelta(days=1)
    for _ in range(5):
        if prev.weekday() < 5:
            psym = tickmod.contract_for_cached("NQ", prev)
            p = tickmod.cached_rth(psym, prev) if psym else None
            if p is not None and not p.empty:
                out["prior_high"] = float(p["price"].max())
                out["prior_low"] = float(p["price"].min())
                out["prior_close"] = float(p["price"].iloc[-1])
                break
        prev -= timedelta(days=1)
    return out


def _penetrations(px: np.ndarray, level: float) -> list[float]:
    """Max distance past *level*, in ticks, for each excursion beyond it."""
    rel = px - level
    side = np.sign(rel)
    side[side == 0] = 0
    keep = side != 0
    if keep.sum() < 2:
        return []
    idx = np.flatnonzero(keep)
    s = side[idx]
    # Run boundaries: where the sign flips between consecutive non-zero prints.
    brk = np.flatnonzero(np.diff(s) != 0) + 1
    starts = np.concatenate(([0], brk))
    ends = np.concatenate((brk, [len(idx)]))
    depths = []
    for a, b in zip(starts, ends):
        seg = np.abs(rel[idx[a]:idx[b - 1] + 1])
        d = float(seg.max()) / TICK
        if d >= MIN_CLEAR_T:
            depths.append(d)
    return depths


def day_stats(job):
    sym, day = job
    try:
        t, px = BS.load_rth(sym, day)
    except FileNotFoundError:
        return None
    if len(px) < 5000:
        return None
    sod = (t / 1000.0) % 86400.0
    am = sod < AM_END
    if am.sum() < 1000:
        return None
    px_am = px[am]

    ruler = BS.ruler_series(t, px)[am]
    fin = ruler[np.isfinite(ruler)]
    if len(fin) == 0:
        return None
    med_ruler = float(np.median(fin))

    lv = _levels(sym, day)
    depths: list[float] = []
    for name, L in lv.items():
        if not (px_am.min() <= L <= px_am.max()):
            continue        # never in play this morning
        depths.extend(_penetrations(px_am, L))
    if len(depths) < 5:
        return None
    d = np.array(depths)
    return {
        "day": day.isoformat(), "n_levels_in_play": len(lv), "n_excursions": len(d),
        "median_ruler_t": round(med_ruler, 1),
        # Raw: how far past, in ticks.
        "median_depth_t": round(float(np.median(d)), 1),
        "p75_depth_t": round(float(np.percentile(d, 75)), 1),
        # Held within a fixed 10-tick tolerance — the "did it respect it" reading.
        "held_10t": round(float((d <= 10).mean()), 4),
        # ...and within a tolerance that scales with the day.
        "held_quarter_ruler": round(float((d <= med_ruler / 4).mean()), 4),
        "depth_over_ruler": round(float(np.median(d)) / med_ruler, 4),
    }


def main():
    days = [(s, d) for s, d in BS.sessions() if d >= date_cls(2025, 1, 1)]
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        rows = [r for r in pool.imap_unordered(day_stats, days, chunksize=2) if r]
    rows.sort(key=lambda r: r["day"])
    (HERE / "level_depth.json").write_text(json.dumps(rows))
    print(f"wrote {len(rows)} days", file=sys.stderr)


if __name__ == "__main__":
    main()
