"""What one order takes to reach the market, off this desk's own sends.

Reads every ``latency`` event the live broker has written (one line per answer,
each carrying everything known so far — so the last line for a tag is the whole
timeline) and reports the distribution of each leg.

THE LEGS ARE NOT A SUM, and no two of them come from differencing clocks on
different machines: the browser times its own press, the API times its own wire
call. See `OrderLatency` in frontend/src/lib/routingTypes.ts.

    gate_ms    our own checks before the wire — guards, day arithmetic, journal
    plant_ms   our submit -> the order plant answering with a basket id
    exch_ms    the wire -> the exchange's first word on the order
    api_ms     the whole request handler, gate + plant + parsing
    net_ms     client_ms - api_ms: the fetch, the dev proxy, React's handler
    client_ms  the browser's own press -> response in hand

Writes summary.json beside itself. Run from the repo root:

    python data/research/order-latency/measure.py
"""

from __future__ import annotations

import json
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "summary.json"
LEGS = ["client_ms", "net_ms", "api_ms", "gate_ms", "plant_ms", "exch_ms"]


def orders() -> dict[str, dict]:
    """Every order that has a latency record, folded to its last line."""
    recs: dict[str, dict] = defaultdict(dict)
    for path in sorted(ROOT.glob("data/live/orders/*/*/orders.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "latency":
                recs[e["tag"]].update(e)
    return dict(recs)


def pct(v: list[float], p: float) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, round(p * (len(v) - 1)))]


def describe(v: list[float]) -> dict:
    return {
        "n": len(v), "min": round(min(v), 1), "p50": round(pct(v, 0.5), 1),
        "mean": round(st.mean(v), 1), "p90": round(pct(v, 0.9), 1),
        "max": round(max(v), 1),
        "sd": round(st.stdev(v), 1) if len(v) > 1 else 0.0,
    }


def main() -> None:
    recs = orders()
    legs = {k: describe([r[k] for r in recs.values() if isinstance(r.get(k), (int, float))])
            for k in LEGS if any(isinstance(r.get(k), (int, float)) for r in recs.values())}

    # The wire round trip is the stable part and the part that is a fact about
    # the connection rather than about the browser. It is what the replay's lag
    # is set from — see docs/research/order-latency.md for why the *round* trip
    # and not half of it.
    wire = [r["exch_ms"] for r in recs.values() if isinstance(r.get("exch_ms"), (int, float))]
    out = {
        "orders": len(recs),
        "legs": legs,
        "by_gesture": {
            str(g): describe(v) for g, v in sorted(
                _group(recs, "gesture").items(), key=lambda kv: -len(kv[1]))},
        "by_how": {
            str(g): describe(v) for g, v in sorted(
                _group(recs, "how").items(), key=lambda kv: -len(kv[1]))},
        "wire_round_trip_p50": round(pct(wire, 0.5), 1) if wire else None,
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")

    print(f"{len(recs)} orders with a latency record\n")
    print(f"{'leg':>10} {'n':>4} {'min':>7} {'p50':>7} {'mean':>7} {'p90':>7} {'max':>7} {'sd':>6}")
    for k, d in legs.items():
        print(f"{k:>10} {d['n']:>4} {d['min']:>7.1f} {d['p50']:>7.1f} {d['mean']:>7.1f} "
              f"{d['p90']:>7.1f} {d['max']:>7.1f} {d['sd']:>6.1f}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")


def _group(recs: dict[str, dict], field: str) -> dict[object, list[float]]:
    g: dict[object, list[float]] = defaultdict(list)
    for r in recs.values():
        if isinstance(r.get("client_ms"), (int, float)):
            g[r.get(field)].append(r["client_ms"])
    return g


if __name__ == "__main__":
    main()
