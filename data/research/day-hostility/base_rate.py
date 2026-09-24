"""Random-entry base rate per session, as a function of how the stop is sized.

The question behind this study is "was 2026-03-25 a hostile day", and the trap in
it is that *hostile* is not a property of a day alone — it is a property of a day
and a stop distance together. So the sweep is over that pair: identical entries
and identical fills, priced once at the session's own volatility ruler and again
at a set of fixed tick distances. A day that collapses at one point on that axis
and thrives at another was never the variable.

Edge-free on purpose. An entry every minute through the window, **both sides**, so
nothing here is a claim about a setup — it is the base rate of the geometry alone.
The stop is the ticket's own ``s30`` vol ruler and the fills are
``replay_whatif``'s, both borrowed from ``bracket_survival.py`` so the arithmetic
is the one the replay UI runs (1t spread, 1t queue, $3.50/side, 250ms round trip).

The reading that carries information is the **R > 1** target share. A symmetric
bracket taken on both sides is ~50% by construction — long's target is short's
stop — so 1R measures almost nothing. At 2R the day has to actually go somewhere
before it comes back, which is exactly the question "could any trade have worked".

Usage:
    .venv/bin/python data/research/day-hostility/base_rate.py
"""
from __future__ import annotations

import json
import multiprocessing as mp
import pathlib
import sys
from datetime import date as date_cls

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "data/research/replay-trail"))
sys.path.insert(0, str(ROOT / "src"))

import bracket_survival as BS  # noqa: E402

OUT = HERE / "base_rate.json"

TICK, TICK_USD, CFG = BS.TICK, BS.TICK_USD, BS.CFG
#: 1 NQ, both sides of the round trip. The study never sizes up — a base rate
#: should not be entangled with a sizing rule.
RT_COST = 2 * CFG["commission"]

#: "am" is the window under complaint; "pm" rides along as the contrast, since
#: "idk how it is afterwards" deserves an answer rather than an assumption.
#: 09:35 rather than 09:30 because the ruler refuses to read under three closed
#: 30-second bars and there is no fallback leg — the ticket would not arm either.
WINDOWS = {"am": (9 * 3600 + 35 * 60, 12 * 3600),
           "pm": (12 * 3600, 15 * 3600 + 30 * 60)}
STEP_S = 60

#: How the stop is sized. ``None`` is the ruler — the ticket's own behaviour;
#: the integers are fixed tick distances, the control that cannot adapt.
#: 56 is the corpus median ruler reading, so it is "a normal day's stop".
STOP_MODES: list[int | None] = [None, 30, 40, 56]
#: Presets D, C and B (`frontend/src/lib/orderPresets.ts`), plus a 2R stretch.
R_MULTS = [1.0, 1.33, 1.5, 2.0]

MIN_PRINTS = 5000
MIN_ENTRIES = 30
#: A stop under this many ticks is inside the spread-plus-queue and would price
#: nonsense rather than a trade.
MIN_STOP_T = 4


def _entry_indices(sod: np.ndarray, n: int, lo: int, hi: int) -> list[int]:
    out = []
    for s in range(lo, hi, STEP_S):
        i = int(np.searchsorted(sod, s, side="left"))
        if 0 < i < n:
            out.append(i)
    return out


def day_stats(job):
    sym, day = job
    try:
        t, px = BS.load_rth(sym, day)
    except FileNotFoundError:
        return None
    if len(px) < MIN_PRINTS:
        return None
    ruler = BS.ruler_series(t, px)
    sod = (t / 1000.0) % 86400.0

    out: dict = {"day": day.isoformat(), "sym": sym}
    for wname, (a, b) in WINDOWS.items():
        idx = _entry_indices(sod, len(px), a, b)
        if len(idx) < MIN_ENTRIES:
            continue
        fin = ruler[idx][np.isfinite(ruler[idx])]
        if len(fin) == 0:
            continue
        rec = {"n_entries": len(idx), "median_ruler_t": round(float(np.median(fin)), 1)}

        for mode in STOP_MODES:
            key = "ruler" if mode is None else f"s{mode}"
            # The ruler mode can only trade where the ruler reads; a fixed stop
            # trades everywhere. Both are honest, and the entry counts differ,
            # so each cell carries its own n.
            if mode is None:
                pairs = [(i, int(round(ruler[i]))) for i in idx if np.isfinite(ruler[i])]
            else:
                pairs = [(i, mode) for i in idx]
            pairs = [(i, s) for i, s in pairs if s >= MIN_STOP_T]
            if len(pairs) < MIN_ENTRIES:
                continue
            for R in R_MULTS:
                hit = miss = 0
                pnl = []
                for i, st in pairs:
                    for side in ("long", "short"):
                        d = 1.0 if side == "long" else -1.0
                        entry = BS.cross(px[i], side == "short", CFG)
                        stop_px = entry - d * st * TICK
                        tgt_px = entry + d * max(1, round(R * st)) * TICK
                        _, xp, why = BS.walk_trade(px, i, side, entry, stop_px,
                                                   tgt_px, None)
                        if why == "target":
                            hit += 1
                        elif why in ("stop", "trail"):
                            miss += 1
                        pnl.append((xp - entry) * d / TICK * TICK_USD - RT_COST)
                n = hit + miss
                rec[f"{key}_R{R}"] = {
                    "n": len(pnl),
                    "target_share": round(hit / n, 4) if n else None,
                    "net_per_trade": round(float(np.mean(pnl)), 2) if pnl else None,
                }
        out[wname] = rec
    return out


def main():
    days = [(s, d) for s, d in BS.sessions() if d >= date_cls(2025, 1, 1)]
    print(f"{len(days)} sessions, {days[0][1]} → {days[-1][1]}", file=sys.stderr)
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        rows = [r for r in pool.imap_unordered(day_stats, days, chunksize=2) if r]
    rows.sort(key=lambda r: r["day"])
    OUT.write_text(json.dumps(rows))
    print(f"wrote {len(rows)} days → {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
