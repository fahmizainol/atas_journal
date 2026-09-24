"""What an INTRADAY trailing drawdown costs, against an end-of-day one.

LucidDaily's funded account trails the max loss limit off the intraday high
water mark; LucidPro trails it off the daily close. Same $2,000, same $52,100
trail cap, same $50,100 lock — only the moment the floor moves differs. This
book is built on a wide target with tolerated giveback, which is exactly the
shape a peak-equity floor charges for, so the question is not rhetorical.

The equity path is rebuilt tick by tick from the stored replay sittings, so the
peak includes OPEN trades, not just closed ones — that is the whole cost. Both
regimes breach the same way (equity touching the floor at any moment, per
Lucid's "intraday spikes included"); they differ only in when the floor moves.

Sizing is flat 1 NQ per `lucidpro-operating-plan.md`, applied to the trades as
played — the engine has no market impact, so rescaling afterwards is exact.

Usage:
    .venv/bin/python data/research/replay-trail/intraday_dd.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent

# The LucidPro / LucidDaily 50K table (mirrors src/journal/replay_account.py).
START_EQUITY = 50_000.0
MAX_LOSS = 2_000.0
TRAIL_CAP = 52_100.0

POINT_VALUE = 20.0     # $/point, 1 NQ
COMMISSION = 3.50      # per side, verified against real fills
DAILY_STOP = 500.0     # the operating plan's own stop, realized $ on the day


def _whatif():
    spec = importlib.util.spec_from_file_location("whatif", HERE / "whatif.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- one sitting -> a tick-level equity path ---------------------------------


def day_path(t, px, trades, daily_stop=None, mtm=True):
    """Return (path, close) for one sitting at flat 1 NQ.

    `path` is the running account delta sampled at every tick: realized P&L of
    the trades already closed, plus the mark-to-market of anything still open.
    The round turn is charged at entry, so an open trade never looks richer
    than it is.

    `mtm=False` drops the open-trade mark and walks closed balance only — the
    reading in which the intraday floor never watches a live position. Which of
    the two Lucid means is the whole question, so both get priced.

    `daily_stop` drops every trade entered after realized P&L has reached
    -daily_stop, which is what the app does when it disarms the day.
    """
    realized = np.zeros(len(t))     # step function, added at each exit
    open_mtm = np.zeros(len(t))
    running = 0.0
    for tr in sorted(trades, key=lambda r: r["entryMs"]):
        if daily_stop is not None and running <= -daily_stop:
            break
        i0 = int(np.searchsorted(t, tr["entryMs"]))
        i1 = int(np.searchsorted(t, tr["exitMs"]))
        sgn = 1.0 if tr["side"] == "long" else -1.0
        net = tr["pts"] * POINT_VALUE - 2 * COMMISSION
        if i0 < len(t):
            if mtm:
                open_mtm[i0:i1] += sgn * (px[i0:i1] - tr["entryPrice"]) * POINT_VALUE
                open_mtm[i0:i1] -= 2 * COMMISSION
            realized[min(i1, len(t) - 1):] += net
        running += net
    path = realized + open_mtm
    return path, running


def day_tax(path):
    """The extra floor room an intraday trail demands on this day, in dollars.

    A day that starts at the account's peak close sits MAX_LOSS above a fixed
    floor under an EOD trail, so what it has to survive is its deepest drop
    BELOW ITS OWN START. Under an intraday trail the floor climbs with every
    new high, so what it has to survive is its deepest drop below its own
    RUNNING PEAK. The difference between the two is what the intraday floor
    costs — and it is zero on a day that only ever went down, which is why
    peak-minus-close overstates it.
    """
    from_peak = float(np.max(np.maximum.accumulate(path) - path)) if len(path) else 0.0
    from_start = max(0.0, -float(path.min())) if len(path) else 0.0
    return from_peak - from_start


# --- chaining the sittings through the two floors ----------------------------


def walk(days, intraday: bool):
    """Walk the sittings in played order. `days` is [(path, close)].

    intraday=False — the floor follows day closes (LucidPro / LucidFlex).
    intraday=True  — the floor follows the running high water mark, open
                     trades included (LucidDaily funded).

    Both breach on equity touching the floor at any tick.
    """
    eq = START_EQUITY
    peak = START_EQUITY
    died = None
    worst_gap = 1e18
    locked = None       # sitting index at which the floor reached $50,100
    for i, (path, close) in enumerate(days):
        curve = eq + path
        if intraday:
            hwm = np.maximum.accumulate(np.maximum(curve, peak))
            floor = np.minimum(hwm, TRAIL_CAP) - MAX_LOSS
        else:
            floor = np.full(len(curve), min(peak, TRAIL_CAP) - MAX_LOSS)
        gap = curve - floor
        worst_gap = min(worst_gap, float(gap.min()) if len(gap) else 0.0)
        if locked is None and float(np.max(floor)) >= TRAIL_CAP - MAX_LOSS:
            locked = i
        if len(gap) and gap.min() <= 0 and died is None:
            died = i
            break
        eq += close
        peak = max(peak, float(curve.max()) if intraday else eq)
    return dict(died=died, eq=eq, worst_gap=worst_gap, locked=locked)


def main():
    w = _whatif()
    probe = json.loads((HERE / "vol_probe.json").read_text())
    probe.sort(key=lambda r: r["aid"].split("_")[-1])   # the order they were played

    ARMS = [("as played", None, True), ("as played", None, False),
            (f"-${DAILY_STOP:.0f} daily stop", DAILY_STOP, True),
            (f"-${DAILY_STOP:.0f} daily stop", DAILY_STOP, False)]
    days = {i: [] for i in range(len(ARMS))}

    for r in probe:
        aid = r["aid"]
        d = ROOT / "data/replays" / aid[:10] / aid
        a = json.loads((d / "attempt.json").read_text())
        trades = json.loads((d / "trades.json").read_text())
        t, px = w.load_tape(a["symbol"], a["date"], a["tz"])
        if len(t) != a["tape"]["n"]:
            print(f"!! {aid}: tape drifted ({len(t)} vs {a['tape']['n']}) — skipped")
            continue
        for i, (_, stop, mtm) in enumerate(ARMS):
            days[i].append(day_path(t, px, trades, stop, mtm))

    for i, (stop_label, _, mtm) in enumerate(ARMS):
        d = days[i]
        over = np.array([day_tax(p) for p, _ in d])
        net = sum(c for _, c in d)
        eod, itd = walk(d, intraday=False), walk(d, intraday=True)
        mark = "peak watches OPEN trades" if mtm else "peak watches CLOSED balance only"

        print("=" * 78)
        print(f"{len(d)} sittings, flat 1 NQ, {stop_label} — {mark}")
        print(f"  net ${net:,.0f} (${net/len(d):,.0f}/day)")
        print(f"  extra room the intraday floor demands: median ${np.median(over):,.0f}  "
              f"mean ${over.mean():,.0f}  p90 ${np.percentile(over, 90):,.0f}  "
              f"max ${over.max():,.0f}")
        print(f"  -> effective MLL under an intraday floor: median "
              f"${MAX_LOSS - np.median(over):,.0f}, p90 day ${MAX_LOSS - np.percentile(over, 90):,.0f} "
              f"(of $2,000)")
        print(f"  days where that extra room exceeds $500: "
              f"{int((over > 500).sum())}/{len(over)} ({100*(over > 500).mean():.0f}%)")
        for name, res in (("EOD trail  (LucidPro) ", eod), ("intraday   (LucidDaily)", itd)):
            fate = f"DIED on sitting {res['died']+1}" if res["died"] is not None \
                   else f"survived, ended ${res['eq']:,.0f}"
            print(f"  {name}: {fate} | worst gap to floor ${res['worst_gap']:,.0f}")
        print()


if __name__ == "__main__":
    main()
