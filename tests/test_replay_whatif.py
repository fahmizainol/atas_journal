"""What-if exits: the four places a counterfactual row can quietly lie.

The engine itself is proved elsewhere — it reproduces 88 of 93 stored sittings to
the dollar, and refuses the rest. What that corpus run cannot prove is the parts
that only exist *because* the rows are counterfactual, since the anchor row
exercises none of them:

  * a stop or target override must be measured from the fill price, not read off
    the absolute level the log happens to store;
  * dropping ``closes`` must actually change how a trade ended, otherwise the
    "set and forget" column is the "as clicked" column with a different header;
  * dropping ``brackets`` must ignore a recorded drag, otherwise every row
    collapses back toward the bracket that was really traded;
  * a micro must price at a tenth, or a sitting traded in MNQ reads ten times its
    size;
  * a reversed row must mirror the placed legs across the fill — left alone, the
    stop lands on the profit side and takes the trade out at its own entry — and
    must flip a resting order's *type* with its side, or it enters somewhere else
    entirely;
  * and a sitting the engine cannot reproduce must come back with no rows at all.

The tape is synthetic and shaped for exactly this: it rises fifty points, then
falls a hundred, so a near stop is hit and a far one never is.

Run directly:  ``.venv/bin/python tests/test_replay_whatif.py``
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import replay_whatif as w  # noqa: E402

T0 = 1_770_000_000_000
TICK = 0.25
ENTRY_I = 5
PEAK_I = 200
N = 600
CLOCK = T0 + (N - 1) * 1000

#: Rises to 21050 at tick 200, then falls to 20950 at tick 600.
def _tape():
    t = np.array([T0 + i * 1000 for i in range(N)], dtype=np.float64)
    px = np.array([21000.0 + i * TICK if i < PEAK_I else 21050.0 - (i - PEAK_I) * TICK
                   for i in range(N)], dtype=np.float64)
    return t, px


#: Free fills, so every assertion below is about levels rather than the fill model.
CFG = dict(commission=0, slipTicks=0, queueTicks=0, latencyMs=0,
           tick_size=TICK, point_value=20.0)

#: The price the entry lands at, given a market order at ``ENTRY_I``.
FILL = 21000.0 + ENTRY_I * TICK


def _log(*, stop=20900.0, target=None, micro=False, closes=(), brackets=(), trail=None):
    """One market buy with a bracket far enough away to never be reached."""
    return {
        "orders": [{"id": 1, "ms": T0 + ENTRY_I * 1000, "idx": ENTRY_I, "type": "market",
                    "side": "long", "size": 1, "stop": stop, "target": target,
                    "trail": trail, "micro": micro}],
        "closes": [dict(c) for c in closes],
        "brackets": [dict(b) for b in brackets],
    }


def _run(log, spec=None, *, drop_closes=False, drop_drags=False, cfg=None):
    cfg = cfg or CFG
    spec = spec or {"stop": None, "target": None, "targetR": None, "trail": "as-placed"}
    t, px = _tape()
    lg = w.apply_scenario(log, spec, TICK, drop_closes=drop_closes, drop_drags=drop_drags)
    return w.run_flat(t, px, lg, CLOCK, cfg, w.scen_of(spec))["trades"]


def test_stop_override_measures_from_the_fill():
    """A 10-tick stop is 10 ticks under *this fill*, not the log's 20900."""
    log = _log(stop=20900.0)

    # The placed stop is a hundred points away and the tape never reaches it, so
    # the sitting ends holding the position.
    base = _run(log)
    assert [tr["reason"] for tr in base] == ["open"], base

    tight = _run(log, {"stop": 10, "target": None, "targetR": None, "trail": "as-placed"})
    assert [tr["reason"] for tr in tight] == ["stop"], tight
    assert tight[0]["exitPrice"] == FILL - 10 * TICK, tight[0]["exitPrice"]


def test_target_r_follows_the_overridden_stop():
    """``targetR`` is a multiple of whichever stop is in force, not of the placed one."""
    log = _log(stop=20900.0)
    spec = {"stop": 20, "target": None, "targetR": 2.0, "trail": None}
    trades = _run(log, spec)
    assert [tr["reason"] for tr in trades] == ["target"], trades
    assert trades[0]["exitPrice"] == FILL + 2 * 20 * TICK, trades[0]["exitPrice"]


def test_dropping_closes_changes_how_the_trade_ended():
    """The two columns differ, and they differ in the exit reason."""
    log = _log(stop=20900.0, closes=[{"ms": T0 + 100 * 1000}])

    clicked = _run(log)
    assert [tr["reason"] for tr in clicked] == ["manual"], clicked
    assert clicked[0]["exitPrice"] == 21000.0 + 100 * TICK

    forget = _run(log, drop_closes=True)
    assert [tr["reason"] for tr in forget] == ["open"], forget


def test_dropping_drags_ignores_a_recorded_drag():
    """A drag writes an absolute level; a counterfactual row must not inherit it."""
    log = _log(stop=20900.0, brackets=[{"ms": T0 + 50 * 1000, "stop": 21005.0}])

    kept = _run(log)
    assert [tr["reason"] for tr in kept] == ["stop"], kept
    assert kept[0]["exitPrice"] == 21005.0

    dropped = _run(log, drop_drags=True)
    assert [tr["reason"] for tr in dropped] == ["open"], dropped


def test_the_stop_column_reports_the_initial_stop():
    """``tick_span`` is the stop at the *fill*, and a drag must not move it."""
    log = _log(stop=20900.0, brackets=[{"ms": T0 + 50 * 1000, "stop": 21005.0}])
    # The drag pulls the stop up to 21005 and takes the trade out there, but the
    # trade opened on a stop 400 ticks below the fill and that is what it ran.
    trades = _run(log)
    assert [tr["reason"] for tr in trades] == ["stop"]
    placed = round((FILL - 20900.0) / TICK)
    assert w.tick_span(trades, "stopTicks") == {"lo": placed, "med": placed, "hi": placed}

    # An override collapses to exactly the number that was asked for.
    tight = _run(log, {"stop": 10, "target": None, "targetR": None, "trail": "as-placed"})
    assert w.tick_span(tight, "stopTicks") == {"lo": 10, "med": 10, "hi": 10}


def test_the_stop_column_spans_trades_that_ran_different_stops():
    """Two trades, two stops — the sitting reports the range, not the last one."""
    log = _log(stop=20900.0, closes=[{"ms": T0 + 250 * 1000}])
    log["orders"].append({"id": 2, "ms": T0 + 300 * 1000, "idx": 300, "type": "market",
                          "side": "long", "size": 1, "stop": 21000.0, "target": None,
                          "trail": None, "micro": False})
    trades = _run(log)
    assert len(trades) == 2, trades
    got = w.tick_span(trades, "stopTicks")
    assert got["lo"] < got["hi"] and got["lo"] <= got["med"] <= got["hi"]

    # And an override flattens both of them onto one stop.
    flat = _run(log, {"stop": 40, "target": None, "targetR": None, "trail": "as-placed"})
    assert w.tick_span(flat, "stopTicks") == {"lo": 40, "med": 40, "hi": 40}


def test_the_target_column_reports_the_initial_target():
    """The target is stored absolute, so its distance is read at the fill too."""
    log = _log(stop=20900.0, target=21100.0,
               brackets=[{"ms": T0 + 50 * 1000, "stop": 20900.0, "target": 21005.0}])
    # The drag pulls the target down to 21005 and the trade ends there, but it
    # opened on a target 400-odd ticks above the fill.
    trades = _run(log)
    assert [tr["reason"] for tr in trades] == ["target"], trades
    placed = round((21100.0 - FILL) / TICK)
    assert w.tick_span(trades, "targetTicks") == {"lo": placed, "med": placed, "hi": placed}

    near = _run(log, {"stop": None, "target": 30, "targetR": None, "trail": "as-placed"})
    assert w.tick_span(near, "targetTicks") == {"lo": 30, "med": 30, "hi": 30}


def test_a_micro_prices_at_a_tenth():
    cfg = {**CFG, "commission": 3.5}
    spec = {"stop": None, "target": 40, "targetR": None, "trail": None}

    mini = _run(_log(), spec, cfg=cfg)[0]
    micro = _run(_log(micro=True), spec, cfg=cfg)[0]

    assert mini["pts"] == micro["pts"] == 40 * TICK
    assert mini["pnl"] == 40 * TICK * 20.0 - 2 * 3.5
    # A tenth of the money per point, and the micro commission floor rather than a
    # tenth of 3.50 — see ``money``.
    assert micro["pnl"] == 40 * TICK * 2.0 - 2 * 0.5


def test_micro_commission_floor_does_not_invent_a_fee():
    """Switching the fill model off must stay off when the order is a micro."""
    trades = _run(_log(micro=True),
                  {"stop": None, "target": 40, "targetR": None, "trail": None})
    assert trades[0]["fees"] == 0.0


#: Reverse everything, leave the exits as they were placed.
REV = {"stop": None, "target": None, "targetR": None, "trail": "as-placed", "flip": True}


def test_reversing_takes_the_other_side_for_the_same_money():
    """Both ends open at the close, so the reversed row is the exact negative."""
    log = _log(stop=20900.0)  # far enough that neither side's stop is reached

    base, rev = _run(log)[0], _run(log, REV)[0]
    assert (base["side"], rev["side"]) == ("long", "short")
    assert base["entryPrice"] == rev["entryPrice"] == FILL
    assert base["entryMs"] == rev["entryMs"]
    assert rev["pnl"] == -base["pnl"] != 0, (base["pnl"], rev["pnl"])


def test_a_reversed_row_mirrors_the_placed_stop_across_the_fill():
    """The placed stop points the old way; unmirrored it would sit on the profit
    side and stop the reversed trade out at its own entry price."""
    log = _log(stop=FILL - 20 * TICK)

    base = _run(log)[0]
    assert base["reason"] == "stop" and base["exitPrice"] == FILL - 20 * TICK

    rev = _run(log, REV)[0]
    assert rev["side"] == "short"
    assert rev["reason"] == "stop"
    assert rev["exitPrice"] == FILL + 20 * TICK, rev["exitPrice"]
    # Same risk, other side — which is the point of mirroring rather than dropping.
    assert rev["stopTicks"] == base["stopTicks"] == 20


def test_a_reversed_resting_order_triggers_where_it_did():
    """A buy limit becomes a sell stop at the same price: same tick, same fill."""
    rest = 20990.0  # under the entry; the tape reaches it only on the way back down
    log = {"orders": [{"id": 1, "ms": T0 + ENTRY_I * 1000, "idx": ENTRY_I, "type": "limit",
                       "side": "long", "size": 1, "price": rest,
                       "stop": None, "target": None, "trail": None}],
           "closes": [], "brackets": []}

    base, rev = _run(log)[0], _run(log, REV)[0]
    assert (base["side"], rev["side"]) == ("long", "short")
    assert base["entryPrice"] == rev["entryPrice"] == rest
    assert base["entryMs"] == rev["entryMs"]
    assert rev["pnl"] == -base["pnl"] != 0


def test_a_reversed_label_says_so():
    assert w.describe({"stop": 40, "targetR": None, "trail": None, "flip": True}) == (
        "Reversed, SL 40t, no trail")
    assert w.describe({**REV}) == "Reversed"


def test_an_unreproducible_sitting_gets_no_rows():
    """No fill model matches the record, so the grid refuses instead of guessing."""
    t, px = _tape()
    log = _log(stop=20900.0)
    # A stored trade the tape cannot produce at any fill model.
    recorded = [{"pnl": 12_345.0, "reason": "target"}]

    cfg, state = w.pick_cfg({"symbol": "NQH5", "prefs": {}}, t, px, log, recorded, CLOCK)
    assert cfg is None and state is None

    rec = dict(symbol="NQH5", date="2026-02-03", tz="New York", clock_ms=CLOCK,
               tape={"n": N}, prefs={}, log=log, trades=recorded)
    real_replays, real_load = w.replays, w.load_tape
    w.replays = types.SimpleNamespace(read=lambda _id: rec)
    w.load_tape = lambda *a, **k: (t, px)
    try:
        out = w.grid("2026-02-03_NQH5_20260101T000000Z")
    finally:
        w.replays, w.load_tape = real_replays, real_load

    assert out["valid"] is False
    assert out["rows"] == []
    assert "no fill model" in out["reason"].lower()
    assert out["stored"]["net"] == 12_345


def test_a_reproducible_sitting_prices_every_row():
    """The happy path end to end: the anchor is the record, the ladder is priced."""
    t, px = _tape()
    log = _log(stop=20900.0, closes=[{"ms": T0 + 100 * 1000}])
    recorded = _run(log, cfg={**CFG, "commission": 0})

    rec = dict(symbol="NQH5", date="2026-02-03", tz="New York", clock_ms=CLOCK,
               tape={"n": N}, prefs={"commission": 0, "slipTicks": 0, "queueTicks": 0,
                                     "latencyMs": 0},
               log=log, trades=recorded)
    real_replays, real_load = w.replays, w.load_tape
    w.replays = types.SimpleNamespace(read=lambda _id: rec)
    w.load_tape = lambda *a, **k: (t, px)
    try:
        out = w.grid("2026-02-03_NQH5_20260101T000000Z",
                     [{"spec": {"stop": 10, "target": 30, "targetR": None, "trail": None}}])
    finally:
        w.replays, w.load_tape = real_replays, real_load

    assert out["valid"] is True
    assert len(out["rows"]) == len(w.PRESETS) + 1

    anchor = out["rows"][0]
    assert anchor["key"] == "as-played" and anchor["drags_kept"] is True
    # The anchor's as-clicked cell *is* the stored sitting.
    assert anchor["clicked"]["net"] == out["stored"]["net"]
    # Every other row disowns the hand on the bracket.
    assert all(r["drags_kept"] is False for r in out["rows"][1:])

    # The manual flatten is what the two columns disagree about.
    assert anchor["clicked"]["reasons"] == {"manual": 1}
    assert anchor["forget"]["reasons"] == {"open": 1}

    # The anchor ran the stop that was placed; the fixed-stop rows ran the one
    # they name, and that difference is what the SL column is for.
    placed = round((FILL - 20900.0) / TICK)
    assert anchor["clicked"]["stop_ticks"] == {"lo": placed, "med": placed, "hi": placed}
    sl = {r["key"]: r for r in out["rows"] if r["key"].startswith("sl")}
    assert set(sl) == {"sl30", "sl50", "sl75", "sl30t25", "sl50t25", "sl75t25"}
    assert sl["sl30"]["clicked"]["stop_ticks"] == {"lo": 30, "med": 30, "hi": 30}
    assert sl["sl75t25"]["clicked"]["stop_ticks"] == {"lo": 75, "med": 75, "hi": 75}

    # No target was placed, so there is nothing for the TP column to report — except
    # on the R rows, which put the target at a multiple of the stop in force.
    assert anchor["clicked"]["target_ticks"] is None
    assert sl["sl30"]["clicked"]["target_ticks"] is None
    r1 = next(r for r in out["rows"] if r["key"] == "r1")
    assert r1["clicked"]["target_ticks"] == r1["clicked"]["stop_ticks"] == {
        "lo": placed, "med": placed, "hi": placed}

    # The reversed block is priced like any other row, and disowns the drags too.
    rev = {r["key"]: r for r in out["rows"] if r["key"].startswith("rev")}
    assert set(rev) == {"rev", "rev-nt", "rev-t25"}
    assert all(r["clicked"]["n"] >= 1 and r["forget"]["n"] >= 1 for r in rev.values())
    # Reversed-as-placed is not the anchor's cache entry under another name.
    assert rev["rev"]["forget"]["net"] == -anchor["forget"]["net"] != 0

    mine = out["rows"][-1]
    assert mine["custom"] is True
    assert mine["label"] == "SL 10t, TP 30t, no trail"
    assert mine["clicked"]["reasons"] == {"target": 1}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
