"""Vol-conditioned SIZING across every stored replay sitting.

The question this answers is not "does the vol ruler predict the trade" — the
probe already says it does not (rho(vol, ticks/contract) ~ +0.05, noise). It is
the other one: **the same 50-tick bracket is not the same risk on a quiet tape
as on a hot one**, so a fixed contract count silently levers up exactly when the
market is most able to take it. Within the 50t-stop sittings the median loss
runs 0.46x the placed stop when the tape is quiet and 1.04x when it is hot, and
the share of losses that land *past* the stop goes 17% -> 53%.

So the arms hold the stop still — this book's geometry has been found absolute
four times over (docs/research/atr-trail.md and friends) — and move only the
contract count. Scoring is survivability-first, because that is what a $2,000
end-of-day trailing drawdown actually tests: peak-to-trough on the chained
equity path, the worst single trade, and whether the LucidPro floor catches it.

Sizing is applied to the trade list rather than re-run through the engine: this
engine has no market impact, so contract count cannot change which fills happen
or where. Scaling afterwards is exact, and it isolates sizing from everything
else.

Usage:
    .venv/bin/python data/research/replay-trail/vol_size.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]

TICK = 0.25
NQ_TICK_USD = 5.00     # 0.25pt x $20
MICRO_RATIO = 10.0

# The LucidPro 50K table (mirrors src/journal/replay_account.py).
START_EQUITY = 50_000.0
MAX_LOSS = 2_000.0
TRAIL_CAP = 52_100.0
DAY_LOSS = 1_200.0
MAX_MINIS = 4

#: The risk budget every sizing arm is handed, in dollars. `guardRules`'
#: `max_risk_usd`.
RISK_USD = 250.0

#: E|loss| in ticks as a function of the vol read, fitted on the 50t-stop
#: sittings (see the probe). The 500-tick bar is both the stronger fit and the
#: timeframe actually traded, so it is the default reference.
LOSS_FIT = {"atr_k500": (11.6, 0.403), "atr_s30": (21.1, 0.214)}


def expected_loss_ticks(ref: str, vol: float) -> float:
    a, b = LOSS_FIT[ref]
    return a + b * vol


# --- sizing arms ---------------------------------------------------------------
#
# Each returns a contract count for one entry, given the vol read at its fill,
# the stop that was actually placed on it, and whether it is a micro.


def _cap(n, micro):
    lim = MAX_MINIS * MICRO_RATIO if micro else MAX_MINIS
    return int(min(max(n, 1), lim))


def size_asplayed(f, ref, micro):
    return f["size"]


def size_flat1(f, ref, micro):
    return int(MICRO_RATIO) if micro else 1


def size_nominal(f, ref, micro):
    """The rule as usually written: hold *nominal* risk constant — the placed
    stop times the tick value. This is the arm that ignores the finding."""
    tick_usd = NQ_TICK_USD / MICRO_RATIO if micro else NQ_TICK_USD
    per = f["stop"] * tick_usd + 2 * f["comm"]
    return _cap(round(RISK_USD / per), micro)


def size_realized(f, ref, micro):
    """Hold *realized* risk constant: size off what a loss on this tape has
    actually cost, not off where the stop was drawn."""
    tick_usd = NQ_TICK_USD / MICRO_RATIO if micro else NQ_TICK_USD
    vol = f.get(ref)
    if vol is None or not np.isfinite(vol):
        return size_nominal(f, ref, micro)
    per = expected_loss_ticks(ref, vol) * tick_usd + 2 * f["comm"]
    return _cap(round(RISK_USD / per), micro)


def size_realized_down(f, ref, micro):
    """Size down on a hot tape, never up on a quiet one — the asymmetric
    version, which cannot reproduce the failed 'widen on hot days' arms by
    construction and never adds leverage the account has not already survived."""
    return min(size_realized(f, ref, micro), size_nominal(f, ref, micro))


ARMS = [
    ("as-played", size_asplayed),
    ("flat-1", size_flat1),
    ("nominal$", size_nominal),
    ("realized$", size_realized),
    ("realized-dn", size_realized_down),
]


# --- scoring -------------------------------------------------------------------


def repnl(f, n):
    """This trade's P&L at `n` contracts. pts and the fee rate are per
    contract, so both legs scale linearly."""
    pv = 20.0 / MICRO_RATIO if f["micro"] else 20.0
    return f["pts"] * pv * n - 2 * f["comm"] * n


def max_drawdown(path):
    peak = -1e18
    dd = 0.0
    for x in path:
        peak = max(peak, x)
        dd = max(dd, peak - x)
    return dd


def lucid_walk(sitting_nets):
    """Walk the sittings through the LucidPro floor in the order they were
    played. Returns (died_at_index or None, final equity, worst floor gap).

    The floor follows day CLOSES only, so it is constant for a whole sitting —
    which is what makes an intraday dip survivable and a settle below it fatal.
    """
    eq = START_EQUITY
    peak_close = START_EQUITY
    died = None
    worst_gap = 1e18
    for i, net in enumerate(sitting_nets):
        floor = min(peak_close, TRAIL_CAP) - MAX_LOSS
        eq += net
        worst_gap = min(worst_gap, eq - floor)
        if eq <= floor and died is None:
            died = i
            break
        peak_close = max(peak_close, eq)
    return died, eq, worst_gap


def main():
    probe = json.loads((ROOT / "data/research/replay-trail/vol_probe.json").read_text())
    # Played order: the attempt id ends in the recording timestamp, which is
    # the order the account itself counts them in.
    probe.sort(key=lambda r: r["aid"].split("_")[-1])

    fills = []
    for r in probe:
        comm = r["cfg"]["commission"]
        micro = comm < 3.5  # the micro rate; minis are billed 3.5/side
        for f in r["fills"]:
            g = dict(f)
            g["comm"] = comm
            g["micro"] = micro
            g["stop"] = r["prefs"]["stop"]
            g["aid"] = r["aid"]
            fills.append(g)

    ref = "atr_k500"
    print(f"{len(fills)} trades, {len(probe)} sittings, risk budget ${RISK_USD:.0f}, "
          f"reference {ref}\n")

    hdr = (f"{'arm':<12}{'net$':>9}{'ct':>7}{'$/ct':>7}{'maxDD':>8}{'worst':>8}"
           f"{'wSit':>8}{'died':>6}{'gap':>8}")
    print(hdr)
    print("-" * len(hdr))
    rowsout = {}
    for name, fn in ARMS:
        per_sitting = {}
        path = [0.0]
        contracts = 0
        worst_trade = 0.0
        for f in fills:
            n = fn(f, ref, f["micro"])
            contracts += n / (MICRO_RATIO if f["micro"] else 1)  # in mini-equivalents
            p = repnl(f, n)
            worst_trade = min(worst_trade, p)
            per_sitting[f["aid"]] = per_sitting.get(f["aid"], 0.0) + p
            path.append(path[-1] + p)
        nets = [per_sitting.get(r["aid"], 0.0) for r in probe]
        died, eq, gap = lucid_walk(nets)
        net = sum(nets)
        rowsout[name] = dict(net=net, nets=nets, dd=max_drawdown(path),
                             worst=worst_trade, ct=contracts, died=died)
        print(f"{name:<12}{net:>9,.0f}{contracts:>7.0f}{net/contracts:>7.1f}"
              f"{max_drawdown(path):>8,.0f}{worst_trade:>8,.0f}{min(nets):>8,.0f}"
              f"{(died if died is not None else '-'):>6}{gap:>8,.0f}")

    print("\n  net$  total P&L    ct  mini-equivalent contracts traded    $/ct  net per contract")
    print("  maxDD peak-to-trough on the trade-by-trade path")
    print("  worst worst single trade   wSit worst sitting")
    print("  died  index of the sitting the $2,000 floor caught (of "
          f"{len(probe)})   gap  closest the equity ever came to the floor")

    # WHAT VOL ACTUALLY MOVES. The reason the vol-conditioned arms cannot beat
    # the flat one, stated as data rather than as a result: across the 50t-stop
    # trades the *median* loss triples with the vol read while the *p90* barely
    # moves. The stop already caps the tail — that is what a stop is — so vol
    # has nothing left to protect. What it predicts is how OFTEN the full stop
    # gets paid, which is a session-level question, not a per-ticket one.
    print("\nwhat the vol read moves (50t-stop trades, losing trades only):")
    print(f"{'vol bucket':<16}{'n':>5}{'med':>8}{'p90':>8}{'max':>8}{'past stop':>11}")
    sub = [f for f in fills if f["stop"] == 50]
    v = np.array([f.get(ref, np.nan) for f in sub], dtype=float)
    y = np.array([(f["pts"] or 0) / TICK for f in sub])
    m = np.isfinite(v) & (y < 0)
    x, L = v[m], -y[m]
    edges = np.percentile(x, [0, 20, 40, 60, 80, 100])
    for i in range(5):
        s = (x >= edges[i]) & (x <= edges[i + 1])
        if s.sum() < 5:
            continue
        print(f"{f'{edges[i]:.0f}-{edges[i + 1]:.0f}t':<16}{s.sum():>5}"
              f"{np.median(L[s]):>8.0f}{np.percentile(L[s], 90):>8.0f}{L[s].max():>8.0f}"
              f"{100 * (L[s] > 50).mean():>10.0f}%")
    print("  median triples across the range; p90 is flat — the stop is the tail.")

    # The question the user actually asked: "I set a max risk in dollars." So
    # the test that matters is not net P&L — this book is net negative at any
    # size, and shrinking a negative number proves nothing — but how tightly
    # each rule HOLDS realized dollar risk at the number it was handed.
    print(f"\nhow well does each rule hold realized risk at ${RISK_USD:.0f}? "
          "(losing trades only)")
    hdr2 = (f"{'arm':<12}{'n':>5}{'med$':>8}{'p90$':>8}{'p95$':>8}{'max$':>9}"
            f"{'>budget':>9}{'>2x':>7}{'spread':>8}")
    print(hdr2)
    print("-" * len(hdr2))
    for name, fn in ARMS:
        losses = []
        for f in fills:
            n = fn(f, ref, f["micro"])
            p = repnl(f, n)
            if p < 0:
                losses.append(-p)
        L = np.array(losses)
        q = np.percentile(L, [50, 90, 95])
        print(f"{name:<12}{len(L):>5}{q[0]:>8,.0f}{q[1]:>8,.0f}{q[2]:>8,.0f}{L.max():>9,.0f}"
              f"{100 * (L > RISK_USD).mean():>8.0f}%{100 * (L > 2 * RISK_USD).mean():>6.0f}%"
              f"{q[1] / max(q[0], 1):>8.1f}x")
    print("  >budget  share of losses that blew through the $250 ceiling")
    print("  spread   p90/median — how much the worst losses exceed a typical one")

    # Leverage-neutral comparison: rescale every arm to the same total exposure
    # as flat-1, so an arm cannot win by simply trading smaller.
    print("\nrescaled to flat-1 exposure (an arm cannot win by levering down):")
    base_ct = rowsout["flat-1"]["ct"]
    print(f"{'arm':<12}{'net$':>9}{'maxDD':>9}{'worst':>9}{'DD/$100 net':>13}")
    for name, _ in ARMS:
        r = rowsout[name]
        k = base_ct / r["ct"]
        dd = r["dd"] * k
        print(f"{name:<12}{r['net'] * k:>9,.0f}{dd:>9,.0f}{r['worst'] * k:>9,.0f}"
              f"{(dd / abs(r['net'] * k) * 100 if r['net'] else float('nan')):>13,.1f}")

    out = ROOT / "data/research/replay-trail/vol_size.json"
    out.write_text(json.dumps({k: {kk: vv for kk, vv in v.items()}
                               for k, v in rowsout.items()}, default=float))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
