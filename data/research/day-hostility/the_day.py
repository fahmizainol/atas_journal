"""The two trades of 2026-03-25, against the tape they were taken on.

Everything above is corpus statistics; this is the day itself. For each of the
two fills: the vol ruler at that instant (what the ticket would have sized the
stop to), how far the trade actually ran against, and what price did afterwards
— so "would a wider stop have saved it" is answered rather than assumed.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date as date_cls

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "data/research/replay-trail"))
sys.path.insert(0, str(ROOT / "src"))

import bracket_survival as BS  # noqa: E402

TICK = BS.TICK
DAY = date_cls(2026, 3, 25)

# (label, entry sod ET, side, entry px, exit px, exit sod ET)
TRADES = [
    ("A 09:35:31", 9 * 3600 + 35 * 60 + 31.98, "short", 24466.0, 24479.0, 9 * 3600 + 35 * 60 + 59.68),
    ("B 09:38:30", 9 * 3600 + 38 * 60 + 30.90, "short", 24506.0, 24518.5, 9 * 3600 + 38 * 60 + 43.14),
]


def main():
    sym = "NQM6"
    t, px = BS.load_rth(sym, DAY)
    ruler = BS.ruler_series(t, px)
    sod = (t / 1000.0) % 86400.0

    open_px = px[0]
    print(f"{DAY}  RTH open {open_px:.2f}   session {px.min():.2f}–{px.max():.2f} "
          f"({(px.max()-px.min()):.1f} pts)\n")

    for label, e_sod, side, e_px, x_px, x_sod in TRADES:
        i = int(np.searchsorted(sod, e_sod, side="left"))
        r = ruler[i]
        d = -1.0                                    # both shorts
        adverse = (x_px - e_px) / TICK              # ticks against, both shorts
        hold = x_sod - e_sod

        # What the tape did after the entry, in ticks against the position.
        out = []
        for mins in (5, 15, 30, 60):
            j = int(np.searchsorted(sod, e_sod + mins * 60, side="left"))
            j = min(j, len(px) - 1)
            seg = px[i:j + 1]
            mae = (seg.max() - e_px) / TICK         # worst adverse for a short
            mfe = (e_px - seg.min()) / TICK
            out.append((mins, mae, mfe, (e_px - px[j]) / TICK))

        print(f"{label}  SHORT @ {e_px:.2f} -> {x_px:.2f}   held {hold:.0f}s   "
              f"{adverse:.0f}t against   ${-adverse * 5:.0f}")
        print(f"    vol ruler at entry: {r:.0f}t"
              f"   ->  the ticket's stop would have been ~{r:.0f}t, "
              f"you took {adverse:.0f}t")
        for mins, mae, mfe, net in out:
            print(f"    +{mins:3}m   worst against {mae:6.0f}t   best for {mfe:6.0f}t"
                  f"   at the mark {net:+6.0f}t")
        print()

    # The one question that decides it: was any stop wide enough to survive?
    print("Would a wider stop have helped? (short held to the +N-minute mark)")
    print(f"    {'stop':>8}{'A survives 30m?':>18}{'B survives 30m?':>18}")
    for st in (50, 68, 100, 150, 200):
        row = f"    {st:>6}t"
        for label, e_sod, side, e_px, x_px, x_sod in TRADES:
            i = int(np.searchsorted(sod, e_sod, side="left"))
            j = min(int(np.searchsorted(sod, e_sod + 30 * 60, side="left")), len(px) - 1)
            mae = (px[i:j + 1].max() - e_px) / TICK
            row += f"{('yes' if mae < st else 'no') + f' (mae {mae:.0f}t)':>18}"
        print(row)


if __name__ == "__main__":
    main()
