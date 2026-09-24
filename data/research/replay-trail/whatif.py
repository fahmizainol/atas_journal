"""Replay-sitting what-if harness: re-run a stored order log under different
trail settings, plus tape-texture metrics for the traded window.

The fill-engine port that used to live here is now `journal.replay_whatif` — the
app serves the same grid from a day view, and two copies of a port that has to
reproduce the browser to the dollar is one copy too many. This file keeps the
research-only half (tape texture, the trail grid report) and re-exports the
engine so the sibling scripts that import `whatif` keep working.

See docs/research/replay-trail-whatif.md for the study this was built for.

Usage:
    .venv/bin/python data/research/replay-trail/whatif.py [attempt_id ...]

No arguments runs the three sittings of the 2026-08-10 study.
"""

from __future__ import annotations

import copy
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal import replays  # noqa: E402
from journal.replay_whatif import (  # noqa: E402,F401  (re-exported for siblings)
    apply_scenario,
    cross,
    load_tape,
    mark_to_market,
    money,
    pick_cfg,
    price_at_ms,
    reduce,
    run_flat,
    run_sim,
    summarize,
)

TICK = 0.25  # texture metrics below are reported in NQ ticks

STUDY_ATTEMPTS = [
    "2025-03-13_NQH5_20260810T050644Z",
    "2025-12-04_NQZ5_20260810T055423Z",
    "2026-02-10_NQH6_20260810T063621Z",
]

# (dist_ticks, step_ticks); step 0 = rungs a full dist apart (the UI default)
GRID = [(25, 0), (25, 5), (35, 0), (35, 5), (50, 0), (50, 5), (50, 10),
        (60, 0), (75, 0), (75, 10), (100, 0)]


def load_attempt(aid: str):
    """The four pieces of a sitting, in the order the scripts here expect them."""
    rec = replays.read(aid)
    return rec, rec["log"], rec["trades"], rec["summary"]


# --- tape texture ------------------------------------------------------------


def zigzag_legs(p, thr_ticks):
    """Leg lengths (ticks) of a zigzag with reversal threshold thr_ticks."""
    thr = thr_ticks * TICK
    legs = []
    anchor = p[0]
    extreme = p[0]
    direction = 0
    for x in p[1:]:
        if direction == 0:
            if abs(x - anchor) >= thr:
                direction = 1 if x > anchor else -1
                extreme = x
        elif direction == 1:
            if x > extreme:
                extreme = x
            elif extreme - x >= thr:
                legs.append((extreme - anchor) / TICK)
                anchor, extreme, direction = extreme, x, -1
        else:
            if x < extreme:
                extreme = x
            elif x - extreme >= thr:
                legs.append((anchor - extreme) / TICK)
                anchor, extreme, direction = extreme, x, 1
    return np.array(legs)


def texture(t, px, w0, w1):
    m = (t >= w0) & (t <= w1)
    pw = px[m]
    minutes = (w1 - w0) / 60000
    path = np.abs(np.diff(pw)).sum() / TICK
    net = abs(pw[-1] - pw[0]) / TICK
    legs = zigzag_legs(pw, 25)
    return dict(minutes=minutes, box=(pw.max() - pw.min()) / TICK,
                path_per_min=path / minutes, drift_per_min=net / minutes,
                churn=path / max(net, 1.0),
                swings_per_min=len(legs) / minutes,
                leg_med=float(np.median(legs)) if len(legs) else float("nan"))


def mfe_ticks(t, px, trades, horizon_ms=180_000):
    out = []
    for tr in trades:
        i0 = np.searchsorted(t, tr["entryMs"])
        i1 = np.searchsorted(t, tr["entryMs"] + horizon_ms)
        seg = px[i0:i1]
        if not len(seg):
            continue
        sgn = 1 if tr["side"] == "long" else -1
        out.append((sgn * (seg - tr["entryPrice"])).max() / TICK)
    return np.array(out)


# --- report ------------------------------------------------------------------


def main(attempt_ids):
    for aid in attempt_ids:
        a, log, recorded, summ = load_attempt(aid)
        t, px = load_tape(a["symbol"], a["date"], a.get("tz"))
        if len(t) != a["tape"]["n"]:
            print(f"!! {aid}: tape drifted ({len(t)} vs {a['tape']['n']}) — "
                  f"fills may not reproduce")
        clock = a["clock_ms"]

        print("=" * 96)
        print(f"{aid}  prefs: trail={a['prefs']['trailTicks']}t "
              f"step={a['prefs']['trailStepTicks']}t target={a['prefs']['targetTicks']}t "
              f"stop={a['prefs']['stopTicks']}t")

        cfg, anchor = pick_cfg(a, t, px, log, recorded, clock)
        if cfg is None:
            print("  validate [MISMATCH]  no fill model reproduces the stored trades — "
                  "do not trust the grid")
            continue
        mine, theirs = summarize(anchor["trades"]), summarize(recorded)
        print(f"  validate [OK]  port n={mine['n']} net={mine['net']}  "
              f"stored n={theirs['n']} net={theirs['net']}  cfg={cfg}")

        tx = texture(t, px, summ["first_fill_ms"], summ["last_exit_ms"])
        mfe = mfe_ticks(t, px, recorded)
        print(f"  window {tx['minutes']:.0f}m | box {tx['box']:.0f}t | "
              f"path {tx['path_per_min']:,.0f} t/min | drift {tx['drift_per_min']:.0f} t/min | "
              f"churn {tx['churn']:.0f}x | 25t-swings {tx['swings_per_min']:.1f}/min "
              f"(median leg {tx['leg_med']:.0f}t) | MFE-3min med {np.median(mfe):.0f}t")

        print(f"  {'dist':>5} {'step':>5} {'n':>3} {'net$':>6} {'wr%':>4} {'avgW':>6} {'avgL':>6}  exits")
        for dist, step in GRID:
            lg = copy.deepcopy(log)
            for o in lg["orders"]:
                if o.get("trail"):
                    o["trail"]["dist"] = dist * TICK
                    o["trail"]["step"] = step * TICK
            s = summarize(run_sim(t, px, lg, clock, cfg)["trades"])
            print(f"  {dist:>4}t {step:>4}t {s['n']:>3} {s['net']:>6} {s['wr']:>4} "
                  f"{s['avg_win']:>6} {s['avg_loss']:>6}  {s['reasons']}")


if __name__ == "__main__":
    main(sys.argv[1:] or STUDY_ATTEMPTS)
