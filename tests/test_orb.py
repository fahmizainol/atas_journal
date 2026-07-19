"""ORB strategy + IB gates: schema, gate verdicts, and engine invariants.

Schema and gate-knob tests are synthetic. The engine tests run over the real
cached NQZ5 session, like test_sim_engine — they skip if the tick cache is cold.

Run directly:  ``.venv/bin/python tests/test_orb.py``
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import bars as barmod  # noqa: E402
from journal.sim import engine, registry, schema  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402
from journal.sim.confluences import GATE_FACTORIES, SessionCtx, validate  # noqa: E402
from journal.sim.rules import OrbConfig, SimConfig  # noqa: E402

DAY = date(2025, 10, 13)


def _cached_session():
    contract = tickmod.contract_for_cached("NQ", DAY)
    if contract is None:
        return None
    t = tickmod.cached_rth(contract, DAY)
    return None if t is None or t.empty else t


# --- schema -----------------------------------------------------------------


def test_schema_parse_and_rejects():
    cfg = schema.parse({"entry_mode": "second_break", "window_minutes": 60}, OrbConfig)
    assert cfg.entry_mode == "second_break" and cfg.window_minutes == 60
    assert cfg.stop_mode == "range" and cfg.target == "eod"
    for bad in [
        {"target": "rr"},                                    # rr without target_rr
        {"min_range_ticks": 500, "max_range_ticks": 100},    # empty band
        {"entry_mode": "nope"},
        {"window_minutes": 0},
        {"stop_tickss": 50},                                 # typo'd knob
    ]:
        try:
            schema.parse(bad, OrbConfig)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    print("schema ok")


def test_registry_entry():
    s = registry.get("orb-breakout")
    assert s.config_cls is OrbConfig
    assert s.session == "rth"
    assert set(s.confluences) == {"ib_in_on", "ib_width", "vwap_flat"}
    validate(OrbConfig(confluences={"ib_in_on": {"enabled": True}}), s.confluences)
    print("registry ok")


# --- gates ------------------------------------------------------------------


def test_gate_knob_validation():
    for name, bad in [
        ("ib_in_on", {"mode": "sideways"}),
        ("ib_in_on", {"ib_minutes": 5}),
        ("ib_width", {"min_ticks": -1}),
        ("ib_width", {"min_ticks": 500, "max_ticks": 100}),
        ("ib_width", {"typo": 1}),
    ]:
        try:
            GATE_FACTORIES[name](bad)
            raise AssertionError(f"{name} accepted {bad}")
        except ValueError:
            pass
    print("gate knobs ok")


def test_gates_on_cached_session():
    t = _cached_session()
    if t is None:
        print("gates on session SKIPPED (tick cache cold)")
        return
    ctx = SessionCtx(cfg=SimConfig(contract="NQ"), day=DAY, ticks=t,
                     bars=barmod.tick_bars(t, 500), value_edge_at_tick=None,
                     profile=None)
    pre, post = 100, len(t) - 100
    # The two modes are complements after the IB completes; pre-IB always passes.
    veto = GATE_FACTORIES["ib_in_on"]({"enabled": True, "mode": "veto_inside"})
    req = GATE_FACTORIES["ib_in_on"]({"enabled": True, "mode": "require_inside"})
    veto.prepare(ctx)
    req.prepare(ctx)
    assert veto.allows(pre, 0) and req.allows(pre, 0)
    assert veto.allows(post, 0) != req.allows(post, 0)
    # Width: mutually exclusive bounds around the actual IB width.
    lo = GATE_FACTORIES["ib_width"]({"enabled": True, "max_ticks": 1})
    hi = GATE_FACTORIES["ib_width"]({"enabled": True, "min_ticks": 1})
    lo.prepare(ctx)
    hi.prepare(ctx)
    assert not lo.allows(post, 0)   # every real IB is wider than 1 tick
    assert hi.allows(post, 0)
    assert lo.allows(pre, 0)        # pre-IB the width isn't knowable
    print("gates on session ok")


# --- engine -----------------------------------------------------------------


def test_engine_invariants():
    t = _cached_session()
    if t is None:
        print("engine SKIPPED (tick cache cold)")
        return
    for raw in [
        {"entry_mode": "candle", "window_minutes": 5},
        {"entry_mode": "break", "window_minutes": 30, "entry_offset_ticks": 4},
        {"entry_mode": "second_break", "window_minutes": 60},
        {"entry_mode": "candle", "window_minutes": 5, "target": "rr", "target_rr": 2.0},
        {"entry_mode": "candle", "window_minutes": 5, "stop_mode": "ticks",
         "stop_ticks": 80},
    ]:
        cfg = schema.parse(raw, OrbConfig)
        trades, vetoed, b, bands = engine.run_session_orb(cfg, DAY)
        assert len(trades) + len(vetoed) <= 1, "more than one attempt"
        assert not b.empty and len(bands) == len(b)
        win_end = pd.Timestamp(f"{DAY} 09:30", tz="America/New_York") \
            + pd.Timedelta(minutes=cfg.window_minutes)
        for tr in trades:
            s = 1 if tr["direction"] == "Long" else -1
            # causality: no entry before the window closes
            assert tr["entry_ts_utc"].tz_convert("America/New_York") >= win_end
            # the stop is on the loss side, and its distance is the R unit
            assert s * (tr["avg_entry"] - tr["stop_price"]) > 0
            risk = s * (tr["avg_entry"] - tr["stop_price"])
            got = s * (tr["avg_exit"] - tr["avg_entry"])
            assert abs(tr["r_multiple"] - got / risk) < 1e-9
            if cfg.stop_mode == "ticks":
                assert abs(risk - cfg.stop_ticks * 0.25) < 1e-9
            if tr["exit_reason"] == "stop":
                assert abs(tr["r_multiple"] + 1.0) < 0.2  # slippage-free ~ -1R
    print("engine invariants ok")


def test_gate_produces_ghost():
    t = _cached_session()
    if t is None:
        print("ghost SKIPPED (tick cache cold)")
        return
    # 2025-10-13's IB sat inside the overnight range (verified in the gate smoke
    # test), so veto_inside must ghost the 60m candle entry, not book it.
    cfg = schema.parse({
        "entry_mode": "candle", "window_minutes": 60,
        "confluences": {"ib_in_on": {"enabled": True, "mode": "veto_inside"}},
    }, OrbConfig)
    trades, vetoed, _, _ = engine.run_session_orb(cfg, DAY)
    assert len(trades) == 0 and len(vetoed) == 1
    assert vetoed[0]["gate"] == "ib_in_on"
    print("ghost ok")


if __name__ == "__main__":
    test_schema_parse_and_rejects()
    test_registry_entry()
    test_gate_knob_validation()
    test_gates_on_cached_session()
    test_engine_invariants()
    test_gate_produces_ghost()
    print("all orb tests passed")
