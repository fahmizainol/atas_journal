"""How far the bracket moves between the ticket and the fill.

THE OBSERVATION. The ticket previews a stop and a target as money — ticks x size
x $/tick. After the order fills, the position's chips quote something else: the
distance from the *fill* to each leg. They are not the same number, and the
question is how much not.

WHY THEY DIFFER. `Simulator.placeOrder` freezes the bracket as **prices**, taken
from the mark at the instant of the gesture:

    stop   = at - dir * stopTicks   * tickSize
    target = at + dir * targetTicks * tickSize

The fill lands somewhere else — every gesture is resolved `latencyMs` later
against the tape as it stood then, and a market order pays the spread on top. So
with a fill `d` ticks the wrong way for you, the position carries a stop
`stopTicks + d` away and a target `targetTicks - d` away. One leg widens, the
other tightens, by the same amount and in opposite directions.

This measures `d` over every recorded sitting: the signed gap, in ticks, between
the print the ticket was written against (`order.idx`) and the price the trade
actually opened at.

Usage:
    .venv/bin/python data/research/preset-stop/leg_drift.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from journal.replay_whatif import load_tape  # noqa: E402

TICK = 0.25


def rows():
    for d in sorted((ROOT / "data/replays").glob("*/*/")):
        log_f, tr_f, at_f = d / "log.json", d / "trades.json", d / "attempt.json"
        if not (log_f.exists() and tr_f.exists() and at_f.exists()):
            continue
        a = json.loads(at_f.read_text())
        log = json.loads(log_f.read_text())
        trades = json.loads(tr_f.read_text())
        if not trades or not log.get("orders"):
            continue
        try:
            t, px = load_tape(a["symbol"], a["date"], a.get("tz"))
        except Exception:
            continue
        # An order and the trade it opened, paired on the fill clock: the trade
        # records `entryMs`, the order the gesture's own `ms` and the tape index
        # it was written against.
        by_ms = {}
        for o in log["orders"]:
            by_ms.setdefault(round(o["ms"]), []).append(o)
        for tr in trades:
            # The gesture that opened this trade is the newest order at or before
            # the fill. Latency puts them apart by `latencyMs`, never more than a
            # second, so anything further away is a different order.
            best = None
            for o in log["orders"]:
                if o["ms"] <= tr["entryMs"] and tr["entryMs"] - o["ms"] <= 1_000:
                    if best is None or o["ms"] > best["ms"]:
                        best = o
            if best is None or best["side"] != tr["side"]:
                continue
            # The print the gesture was written against, found by **clock** and
            # not by `order.idx`. That index is into the tape the page had glued
            # together — context days and all — so with prior sessions drawn it
            # is offset by millions of prints from this session's own array, and
            # reading it here silently quotes a price from two days earlier. It
            # is what made the first run of this script report 700-tick tickets.
            i = int(np.searchsorted(t, best["ms"], side="right")) - 1
            if i < 0:
                continue
            # `placeOrder`'s own rule: the mark for a market order, the resting
            # price for a limit or a stop. Measuring a resting order against the
            # mark instead would report how far away you placed it, which is a
            # fact about the gesture and not about the fill.
            at = float(px[i]) if best["type"] == "market" else float(best["price"])
            dir_ = 1 if tr["side"] == "long" else -1
            # Positive = filled against you: the stop ends up this many ticks
            # further away than the ticket said, the target this many nearer.
            d = (tr["entryPrice"] - at) * dir_ / TICK
            stop_t = abs(best["stop"] - at) / TICK if best.get("stop") else None
            tgt_t = abs(best["target"] - at) / TICK if best.get("target") else None
            yield dict(
                day=a["date"], type=best["type"], size=tr["size"], drift=d,
                stop_ticks=stop_t, target_ticks=tgt_t,
                risk_ticks=(stop_t + d) if stop_t else None,
                reward_ticks=(tgt_t - d) if tgt_t else None,
                micro=bool(tr.get("micro")),
            )


def pct(x, q):
    return float(np.percentile(x, q))


def main():
    rs = list(rows())
    print(f"{len(rs)} opened trades matched to the gesture that placed them")
    mkt = [r for r in rs if r["type"] == "market"]
    rest = [r for r in rs if r["type"] != "market"]
    print(f"  {len(mkt)} market, {len(rest)} resting (limit/stop)")

    for label, sub in (("market", mkt), ("limit/stop", rest)):
        if not sub:
            continue
        d = np.array([r["drift"] for r in sub])
        print(f"\n--- {label}: ticks between the print you clicked and the fill ---")
        print(f"  signed   p10 {pct(d,10):+.1f}  p50 {pct(d,50):+.1f}  p90 {pct(d,90):+.1f}   "
              f"(+ = filled against you)")
        print(f"  absolute p50 {pct(np.abs(d),50):.1f}  p90 {pct(np.abs(d),90):.1f}  "
              f"max {np.abs(d).max():.0f}")
        print(f"  the fill was NOT the clicked print on {100*np.mean(d != 0):.0f}% of them")

    # What that is worth, which is the form the ticket quotes it in. NQ money,
    # per contract — the only figure the user is actually comparing.
    both = [r for r in mkt if r["stop_ticks"] and r["target_ticks"]]
    if both:
        rd = np.array([abs(r["risk_ticks"] - r["stop_ticks"]) * 5 * r["size"] for r in both])
        print(f"\n--- what the two chips move by, in dollars ({len(both)} trades) ---")
        print(f"  p50 ${pct(rd,50):.0f}   p90 ${pct(rd,90):.0f}   max ${rd.max():.0f}")
        print("  (each leg moves by this much — the stop one way, the target the other)")
        worst = sorted(both, key=lambda r: -abs(r["drift"]))[:5]
        print("\n  the five biggest:")
        for r in worst:
            print(f"    {r['day']}  ticket ⊥{r['stop_ticks']:.0f}/⊤{r['target_ticks']:.0f}"
                  f"  →  filled ⊥{r['risk_ticks']:.0f}/⊤{r['reward_ticks']:.0f}"
                  f"   ({r['drift']:+.0f}t)")


if __name__ == "__main__":
    main()
