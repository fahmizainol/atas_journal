"""Strategies workbench: run identity, store lifecycle, migration, veto gates, API.

The properties pinned here are the ones the research workflow leans on:
  - a run's identity is (config, engine version) and NOTHING else — labels and
    notes are mutable metadata that can never fork or overwrite an artifact;
  - the baseline pin auto-sets once and survives deletes of other runs;
  - legacy demo runs migrate into the vwap-upper-band-bounce strategy intact;
  - a confluence gate can only veto — vetoed entries become ghost rows with a
    would-be P&L instead of silently vanishing.

Engine-with-real-ticks tests skip if the tick cache is cold, same as
test_sim_engine.py.

Run directly:  ``.venv/bin/python tests/test_strategies.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fastapi import BackgroundTasks, HTTPException  # noqa: E402

from journal import edges  # noqa: E402
from journal.sim import confluences as confmod  # noqa: E402
from journal.sim import engine, registry, runner, schema, store, ticks  # noqa: E402
from journal.sim.rules import FadeConfig, GlobexBounceConfig, SimConfig  # noqa: E402
from api.routers import strategies as api  # noqa: E402

DAY = date(2025, 10, 13)
SLUG = "vwap-upper-band-bounce"


class _TmpStore:
    """Point store.SIMS_DIR at a scratch dir for the duration of a test."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = store.SIMS_DIR

    def __enter__(self):
        store.SIMS_DIR = Path(self._tmp.name) / "sims"
        return store.SIMS_DIR

    def __exit__(self, *exc):
        store.SIMS_DIR = self._old
        self._tmp.cleanup()


def _fake_trades() -> pd.DataFrame:
    return pd.DataFrame({"trade_no": [1], "net_pnl": [100.0]})


def _finished_run(cfg: SimConfig, version: str = "1", metrics: dict | None = None) -> str:
    rid = store.init_run(SLUG, cfg, version, sessions_total=5)
    store.finish_run(SLUG, rid, _fake_trades(), pd.DataFrame(),
                     metrics or {"trades": 1, "net_pnl": 100.0})
    store.maybe_autopin_baseline(SLUG, rid)
    return rid


# --- run identity -----------------------------------------------------------

def test_run_id_hashes_config_and_version_only():
    cfg = SimConfig()
    assert store.run_id(cfg, "1") == store.run_id(SimConfig(), "1")
    assert store.run_id(cfg, "1") != store.run_id(cfg, "2"), "version must fork the id"
    assert store.run_id(cfg, "1") != store.run_id(
        SimConfig(stop_ticks=50), "1"), "config must fork the id"


def test_meta_edits_never_change_identity():
    with _TmpStore():
        cfg = SimConfig()
        rid = _finished_run(cfg)
        store.write_meta(SLUG, rid, label="the keeper", notes="looked clean")
        assert store.run_id(cfg, "1") == rid, "labeling must not fork the run"
        m = store.read_meta(SLUG, rid)
        assert (m["label"], m["notes"]) == ("the keeper", "looked clean")


# --- lifecycle & baseline ----------------------------------------------------

def test_lifecycle_running_to_done_and_listing():
    with _TmpStore():
        cfg = SimConfig()
        rid = store.init_run(SLUG, cfg, "1", sessions_total=5)
        assert store.read_run(SLUG, rid) is None, "an unfinished run must not read as done"
        store.update_progress(SLUG, rid, 3)
        assert store.read_state(SLUG, rid)["sessions_done"] == 3

        store.finish_run(SLUG, rid, _fake_trades(), pd.DataFrame(), {"trades": 1})
        got = store.read_run(SLUG, rid)
        assert got is not None and got[2]["trades"] == 1

        runs = store.list_runs(SLUG)
        assert [r["run_id"] for r in runs] == [rid]
        assert runs[0]["state"]["status"] == "done"


def test_baseline_autopin_and_delete_clears_it():
    with _TmpStore():
        rid1 = _finished_run(SimConfig())
        assert store.baseline(SLUG) == rid1, "first completed run auto-pins"
        rid2 = _finished_run(SimConfig(stop_ticks=50))
        assert store.baseline(SLUG) == rid1, "autopin must not steal an existing pin"
        store.set_baseline(SLUG, rid2)
        store.delete_run(SLUG, rid2)
        assert store.baseline(SLUG) is None, "deleting the baseline must clear the pin"
        assert store.read_state(SLUG, rid1) is not None


def test_failed_run_records_the_error():
    with _TmpStore():
        rid = store.init_run(SLUG, SimConfig(), "1", sessions_total=5)
        store.fail_run(SLUG, rid, "RuntimeError: no ticks for 2025-10-15")
        st = store.read_state(SLUG, rid)
        assert st["status"] == "error" and "no ticks" in st["error"]
        assert store.read_run(SLUG, rid) is None


# --- legacy migration ---------------------------------------------------------

def test_legacy_demo_runs_migrate_under_the_vwap_strategy():
    with _TmpStore() as sims:
        old = sims / "A-20251013-20251017-a4b5f723"
        old.mkdir(parents=True)
        legacy_cfg = {**SimConfig().to_json(), "label": "variant A"}
        (old / "config.json").write_text(json.dumps(legacy_cfg))
        (old / "metrics.json").write_text(json.dumps({"trades": 1, "net_pnl": 42.0}))
        _fake_trades().to_parquet(old / "trades.parquet", index=False)

        store.ensure_migrated()

        assert not old.exists(), "legacy folder must be consumed"
        runs = store.list_runs(SLUG)
        assert len(runs) == 1
        r = runs[0]
        assert r["meta"]["label"] == "variant A", "config label becomes meta label"
        assert "label" not in r["config"], "label must leave the config"
        assert r["state"]["status"] == "done"
        assert store.baseline(SLUG) == r["run_id"], "migration pins a baseline"
        assert r["metrics"]["net_pnl"] == 42.0

        store.ensure_migrated()  # idempotent
        assert len(store.list_runs(SLUG)) == 1


# --- confluence gates ---------------------------------------------------------

class _VetoAll:
    name = "test_veto"
    needs_profile = False

    def prepare(self, ctx):
        pass

    def allows(self, i, fill):
        return False


def test_unknown_confluence_is_rejected():
    cfg = SimConfig(confluences={"nope": {"enabled": True}})
    try:
        confmod.validate(cfg, ())
        raise AssertionError("expected ValueError for unknown gate")
    except ValueError as exc:
        assert "nope" in str(exc)


def test_vetoed_entries_become_ghost_trades():
    if not ticks._cache_path("NQZ5", DAY).exists():
        print("   (skipped: tick cache cold)")
        return
    confmod.GATE_FACTORIES["test_veto"] = lambda section: _VetoAll()
    try:
        base_trades, base_vetoed, _, _ = engine.run_session(SimConfig(), DAY)
        assert base_trades and not base_vetoed

        cfg = SimConfig(confluences={"test_veto": {"enabled": True}})
        trades, vetoed, _, _ = engine.run_session(cfg, DAY)
        assert not trades, "a veto-all gate must allow no entries"
        assert len(vetoed) >= len(base_trades), "every base entry must appear as a ghost"
        for v in vetoed:
            assert v["gate"] == "test_veto"
            assert v["exit_reason"] in ("stop", "target", "time")
            assert "net_pnl" in v

        # enabled:false is inert — identical trades to the gateless run.
        off = SimConfig(confluences={"test_veto": {"enabled": False}})
        off_trades, off_vetoed, _, _ = engine.run_session(off, DAY)
        assert len(off_trades) == len(base_trades) and not off_vetoed
    finally:
        confmod.GATE_FACTORIES.pop("test_veto", None)


def _regime_ctx(day=DAY, band=None):
    """A minimal SessionCtx for the regime gate: it reads only cfg, day, and
    tick timestamps. Ticks straddle the 10:30 ET checkpoint.

    ``band`` is the side of the market the setup lives on — None leaves it to
    derive the bounce's way (a long lives on the upper band). Pass "lower" for
    the fade-long, whose caps must read the lower band's KPIs."""
    ts = pd.to_datetime(
        ["2025-10-13 13:35", "2025-10-13 14:29", "2025-10-13 14:30", "2025-10-13 15:00"],
        utc=True)  # 09:35, 10:29, 10:30, 11:00 ET
    t = pd.DataFrame({"ts_utc": ts})
    return confmod.SessionCtx(cfg=SimConfig(), day=day, ticks=t, bars=pd.DataFrame(),
                              value_edge_at_tick=None, profile=None, band=band)


def test_regime_gate_stands_down_after_1030_on_below_vwap_days():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"10:30": {"bbr": 0.8}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.RegimeGate({"enabled": True, "bbr_max": 0.6})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # A qualifying morning leaves the gate inert.
        art = {"checkpoints": {"10:30": {"bbr": 0.2}}}
        g = gatesmod.RegimeGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # Blind — no artifact, or no bbr — must not read as confirmed.
        for blind in (None, {"checkpoints": {}}, {"checkpoints": {"10:30": {"bbr": None}}}):
            art = blind
            g = gatesmod.RegimeGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(1, 0.0) and not g.allows(2, 0.0), f"blind case {blind!r}"

        # checkpoint="09:45" reads the 09:45 bbr and stands down from 09:45 —
        # only the 09:35 tick is still before the read.
        art = {"checkpoints": {"09:45": {"bbr": 0.8}, "10:30": {"bbr": 0.2}}}
        g = gatesmod.RegimeGate({"enabled": True, "checkpoint": "09:45"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "bbr": 0.6}, {"enabled": True, "bbr_max": 1.5},
                {"enabled": True, "bbr_max": True},
                {"enabled": True, "checkpoint": "09:30"},
                {"enabled": True, "checkpoint": "12:00"}):
        try:
            gatesmod.RegimeGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_vwap_slope_gate_stands_down_after_1030_without_upward_grade():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"10:30": {"ny_vwap_slope_ppm": -0.4}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.VwapSlopeGate({"enabled": True, "slope_min": 0.0})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # A slope exactly at the threshold stands down: the knob is "at or below".
        art = {"checkpoints": {"10:30": {"ny_vwap_slope_ppm": 0.0}}}
        g = gatesmod.VwapSlopeGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(2, 0.0)

        # An upward grade leaves the gate inert.
        art = {"checkpoints": {"10:30": {"ny_vwap_slope_ppm": 0.7}}}
        g = gatesmod.VwapSlopeGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # Blind — no artifact, or no slope — must not read as confirmed.
        for blind in (None, {"checkpoints": {}},
                      {"checkpoints": {"10:30": {"ny_vwap_slope_ppm": None}}}):
            art = blind
            g = gatesmod.VwapSlopeGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(1, 0.0) and not g.allows(2, 0.0), f"blind case {blind!r}"

        # checkpoint="09:45" reads the 09:45 slope and stands down from 09:45.
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": -0.4},
                               "10:30": {"ny_vwap_slope_ppm": 0.7}}}
        g = gatesmod.VwapSlopeGate({"enabled": True, "checkpoint": "09:45"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "slope": 0.0}, {"enabled": True, "slope_min": 9.0},
                {"enabled": True, "slope_min": True},
                {"enabled": True, "checkpoint": "11:00"}):
        try:
            gatesmod.VwapSlopeGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_vwap_cross_gate_stands_down_on_churn_mornings():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"09:45": {"ny_vwap_cross_rate": 16.0}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.VwapCrossGate({"enabled": True, "cross_max": 12.0})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # A rate exactly at the threshold stands down: the knob is "at or above".
        art = {"checkpoints": {"09:45": {"ny_vwap_cross_rate": 12.0}}}
        g = gatesmod.VwapCrossGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(1, 0.0)

        # A morning that held its side leaves the gate inert.
        art = {"checkpoints": {"09:45": {"ny_vwap_cross_rate": 2.0}}}
        g = gatesmod.VwapCrossGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # checkpoint="10:30" reads the 10:30 rate and stands down from 10:30.
        art = {"checkpoints": {"09:45": {"ny_vwap_cross_rate": 2.0},
                               "10:30": {"ny_vwap_cross_rate": 16.0}}}
        g = gatesmod.VwapCrossGate({"enabled": True, "checkpoint": "10:30"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # Blind — no artifact, or no rate — must not read as confirmed.
        for blind in (None, {"checkpoints": {}},
                      {"checkpoints": {"09:45": {"ny_vwap_cross_rate": None}}}):
            art = blind
            g = gatesmod.VwapCrossGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(0, 0.0) and not g.allows(1, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "cross": 12.0}, {"enabled": True, "cross_max": -1.0},
                {"enabled": True, "cross_max": True},
                {"enabled": True, "checkpoint": "eod"}):
        try:
            gatesmod.VwapCrossGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_upper_occupancy_gate_stands_down_when_price_never_lived_up_there():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"10:30": {"ny_upper_channel_occupancy": 0.05}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.UpperOccupancyGate({"enabled": True, "occupancy_min": 0.17})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # An occupancy exactly at the threshold stands down: "at or below".
        art = {"checkpoints": {"10:30": {"ny_upper_channel_occupancy": 0.17}}}
        g = gatesmod.UpperOccupancyGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(2, 0.0)

        # A morning lived in the channel leaves the gate inert.
        art = {"checkpoints": {"10:30": {"ny_upper_channel_occupancy": 0.5}}}
        g = gatesmod.UpperOccupancyGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # checkpoint="09:45" reads the 09:45 share and stands down from 09:45.
        art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.05},
                               "10:30": {"ny_upper_channel_occupancy": 0.5}}}
        g = gatesmod.UpperOccupancyGate({"enabled": True, "checkpoint": "09:45"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # Blind — no artifact, or no occupancy — must not read as confirmed.
        for blind in (None, {"checkpoints": {}},
                      {"checkpoints": {"10:30": {"ny_upper_channel_occupancy": None}}}):
            art = blind
            g = gatesmod.UpperOccupancyGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(1, 0.0) and not g.allows(2, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "occupancy": 0.2},
                {"enabled": True, "occupancy_min": 1.5},
                {"enabled": True, "occupancy_min": True},
                {"enabled": True, "checkpoint": "09:30"}):
        try:
            gatesmod.UpperOccupancyGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_gx_rescue_gate_reads_the_0945_ratio_and_knows_its_three_silences():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"partial": False,
           "checkpoints": {"09:45": {"gx_upper_rescue_ratio": 0.0}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.GxRescueGate({"enabled": True, "rescue_min": 0.33})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # Rescues at or above the threshold leave the gate inert.
        art = {"partial": False,
               "checkpoints": {"09:45": {"gx_upper_rescue_ratio": 0.5}}}
        g = gatesmod.GxRescueGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # A described day whose band simply hasn't broken yet is NOT blind:
        # the absence of the event must not stand the day down.
        art = {"partial": False,
               "checkpoints": {"09:45": {"gx_upper_rescue_ratio": None}}}
        g = gatesmod.GxRescueGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # Blind — no artifact, or no Globex anchor — must not read as confirmed.
        for blind in (None,
                      {"partial": True,
                       "checkpoints": {"09:45": {"gx_upper_rescue_ratio": None}}}):
            art = blind
            g = gatesmod.GxRescueGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(0, 0.0) and not g.allows(1, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "rescue": 0.3}, {"enabled": True, "rescue_min": 1.5},
                {"enabled": True, "rescue_min": True}):
        try:
            gatesmod.GxRescueGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_vwap_slope_cap_gate_stands_down_against_a_steep_upward_grade():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": 2.0}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.VwapSlopeCapGate({"enabled": True, "slope_max": 1.1})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # A slope exactly at the threshold stands down: the knob is "at or above".
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": 1.1}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(2, 0.0)

        # A flat or downward grade leaves the gate inert.
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": -0.4}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # checkpoint="10:30" reads the 10:30 slope and stands down from 10:30.
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": 5.0},
                               "10:30": {"ny_vwap_slope_ppm": 2.0}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True, "checkpoint": "10:30"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # Blind — no artifact, or no slope — must not read as confirmed.
        for blind in (None, {"checkpoints": {}},
                      {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": None}}}):
            art = blind
            g = gatesmod.VwapSlopeCapGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(0, 0.0) and not g.allows(1, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "slope": 1.0}, {"enabled": True, "slope_max": 9.0},
                {"enabled": True, "slope_max": True},
                {"enabled": True, "checkpoint": "11:00"}):
        try:
            gatesmod.VwapSlopeCapGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_upper_occupancy_cap_gate_stands_down_when_price_lives_up_there():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.6}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.UpperOccupancyCapGate({"enabled": True, "occupancy_max": 0.33})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # An occupancy exactly at the threshold stands down: "at or above".
        art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.33}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(2, 0.0)

        # A morning that stayed out of the channel leaves the gate inert.
        art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.05}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # checkpoint="10:30" reads the 10:30 share and stands down from 10:30.
        art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.9},
                               "10:30": {"ny_upper_channel_occupancy": 0.6}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True, "checkpoint": "10:30"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # Blind — no artifact, or no occupancy — must not read as confirmed.
        for blind in (None, {"checkpoints": {}},
                      {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": None}}}):
            art = blind
            g = gatesmod.UpperOccupancyCapGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(0, 0.0) and not g.allows(1, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "occupancy": 0.2},
                {"enabled": True, "occupancy_max": 1.5},
                {"enabled": True, "occupancy_max": True},
                {"enabled": True, "checkpoint": "09:30"}):
        try:
            gatesmod.UpperOccupancyCapGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_gx_rescue_cap_gate_stands_down_when_globex_is_catching():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    art = {"partial": False,
           "checkpoints": {"10:30": {"gx_upper_rescue_ratio": 0.5}}}
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art
        g = gatesmod.GxRescueCapGate({"enabled": True, "rescue_max": 0.4})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0) and g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not g.allows(2, 0.0) and not g.allows(3, 0.0), "stood down from 10:30"

        # A ratio exactly at the threshold stands down: "at or above".
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_upper_rescue_ratio": 0.4}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert not g.allows(2, 0.0)

        # Rescues below the threshold leave the gate inert.
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_upper_rescue_ratio": 0.0}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # A described day whose band simply hasn't broken yet is NOT blind:
        # the absence of the event must not stand the day down.
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_upper_rescue_ratio": None}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx())
        assert all(g.allows(i, 0.0) for i in range(4))

        # checkpoint="09:45" reads the 09:45 ratio and stands down from 09:45.
        art = {"partial": False,
               "checkpoints": {"09:45": {"gx_upper_rescue_ratio": 0.5},
                               "10:30": {"gx_upper_rescue_ratio": 0.0}}}
        g = gatesmod.GxRescueCapGate({"enabled": True, "checkpoint": "09:45"})
        g.prepare(_regime_ctx())
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # Blind — no artifact, or no Globex anchor — must not read as confirmed.
        for blind in (None,
                      {"partial": True,
                       "checkpoints": {"10:30": {"gx_upper_rescue_ratio": None}}}):
            art = blind
            g = gatesmod.GxRescueCapGate({"enabled": True})
            g.prepare(_regime_ctx())
            assert g.allows(1, 0.0) and not g.allows(2, 0.0), f"blind case {blind!r}"
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "rescue": 0.3}, {"enabled": True, "rescue_max": 1.5},
                {"enabled": True, "rescue_max": True},
                {"enabled": True, "checkpoint": "12:00"}):
        try:
            gatesmod.GxRescueCapGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


# --- the caps, mirrored onto the lower band (the fade-long) --------------------
#
# The three caps are the fade's regime stand-downs, and each was written in the
# short's flavour: an UPWARD grade, the UPPER channel, a floor UNDERNEATH. On the
# lower band every one of those reverses. These are the tests a sign error would
# have to survive — and the failure they guard against is silent, because a cap
# pointed at the wrong side doesn't error, it just waves through exactly the days
# it was meant to stop (and stands down the ones it should have passed).

def test_vwap_slope_cap_mirrors_onto_the_lower_band():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art

        # A steep DOWNWARD grade is the fade-long's runaway: the tape is
        # trending away from the mean it buys back toward.
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": -2.0}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True, "slope_max": 1.1})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"

        # And the same grade the SHORT stands down for leaves the long inert:
        # an upward tape carries price back to the lower band it is fading.
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": 2.0}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True, "slope_max": 1.1})
        g.prepare(_regime_ctx(band="lower"))
        assert all(g.allows(i, 0.0) for i in range(4)), "an upward grade is not the long's enemy"
        g.prepare(_regime_ctx())  # the short, same day, same artifact
        assert not g.allows(2, 0.0), "the very grade that stands the short down"

        # Exactly at the threshold, in the long's own frame: "at or above".
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": -1.1}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert not g.allows(2, 0.0)

        # Blind stays blind on the mirror too: "no data" is not "confirmed".
        art = {"checkpoints": {"09:45": {"ny_vwap_slope_ppm": None}}}
        g = gatesmod.VwapSlopeCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(0, 0.0) and not g.allows(1, 0.0)
    finally:
        gatesmod.regmod.get_regime = real


def test_upper_occupancy_cap_mirrors_onto_the_lower_channel():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art

        # A morning camped in the NY lower channel is accepting those prices —
        # the residence the fade-long is betting against. The gate must read
        # THAT number, not the upper one it is named after.
        art = {"checkpoints": {"09:45": {"ny_lower_channel_occupancy": 0.6,
                                         "ny_upper_channel_occupancy": 0.0}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True, "occupancy_max": 0.33})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(0, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (1, 2, 3)), "stood down from 09:45"
        g.prepare(_regime_ctx())  # the short reads the upper channel: empty, inert
        assert all(g.allows(i, 0.0) for i in range(4))

        # And the reverse day: upper channel busy, lower empty. The long passes.
        art = {"checkpoints": {"09:45": {"ny_lower_channel_occupancy": 0.0,
                                         "ny_upper_channel_occupancy": 0.6}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True, "occupancy_max": 0.33})
        g.prepare(_regime_ctx(band="lower"))
        assert all(g.allows(i, 0.0) for i in range(4))

        # Blind on the mirror's key — the upper reading must not stand in for it.
        art = {"checkpoints": {"09:45": {"ny_upper_channel_occupancy": 0.0}}}
        g = gatesmod.UpperOccupancyCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(0, 0.0) and not g.allows(1, 0.0)
    finally:
        gatesmod.regmod.get_regime = real


def test_gx_rescue_cap_mirrors_onto_the_lower_band():
    from journal.sim import gates as gatesmod

    real = gatesmod.regmod.get_regime
    try:
        gatesmod.regmod.get_regime = lambda symbol, day: art

        # The mirror event: the Globex −1σ standing ABOVE the session −1σ and
        # catching the rallies that break it — the ceiling the long buys into.
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_lower_rescue_ratio": 0.5,
                                         "gx_upper_rescue_ratio": 0.0}}}
        g = gatesmod.GxRescueCapGate({"enabled": True, "rescue_max": 0.4})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(1, 0.0), "pre-checkpoint entries pass"
        assert not any(g.allows(i, 0.0) for i in (2, 3)), "stood down from 10:30"
        g.prepare(_regime_ctx())  # the short reads its own side: no rescues, inert
        assert all(g.allows(i, 0.0) for i in range(4))

        # A day rescuing on the upper side only says nothing about the long.
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_lower_rescue_ratio": 0.0,
                                         "gx_upper_rescue_ratio": 0.9}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert all(g.allows(i, 0.0) for i in range(4))

        # The lower band simply hasn't broken yet: absence of the event, not
        # blindness. The gate stays inert — same doctrine as the short's.
        art = {"partial": False,
               "checkpoints": {"10:30": {"gx_lower_rescue_ratio": None}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert all(g.allows(i, 0.0) for i in range(4))

        # But a partial day (no Globex anchor) is blind, on this side too.
        art = {"partial": True,
               "checkpoints": {"10:30": {"gx_lower_rescue_ratio": None}}}
        g = gatesmod.GxRescueCapGate({"enabled": True})
        g.prepare(_regime_ctx(band="lower"))
        assert g.allows(1, 0.0) and not g.allows(2, 0.0)
    finally:
        gatesmod.regmod.get_regime = real


def test_gx_floor_gate_wants_the_globex_line_just_beneath_the_fill():
    from journal.sim import gates as gatesmod

    ctx = _regime_ctx()
    n_on, n_rth = 3, len(ctx.ticks)
    on = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-10-13 09:00"] * n_on, utc=True),
                       "price": [100.0] * n_on, "size": [1] * n_on})
    # upper1 per tick, overnight rows first: the RTH segment the gate slices off
    # is [100, 105, nan, 100]; lower1 mirrors it above for the short case.
    bands = pd.DataFrame({
        "upper1": [0.0] * n_on + [100.0, 105.0, float("nan"), 100.0],
        "lower1": [0.0] * n_on + [100.0, 95.0, float("nan"), 100.0],
    })

    real_contract = gatesmod.tickmod.contract_for_cached
    real_on = gatesmod.tickmod.cached_overnight
    real_bands = gatesmod.vwapmod.vwap_bands
    try:
        gatesmod.tickmod.contract_for_cached = lambda symbol, day: "NQZ5"
        gatesmod.tickmod.cached_overnight = lambda symbol, day: on
        gatesmod.vwapmod.vwap_bands = lambda t: bands

        g = gatesmod.GxFloorGate({"enabled": True, "max_ticks_below": 80})  # 20 pts on NQ
        g.prepare(ctx)
        assert g.allows(0, 100.0), "line exactly at the fill is a floor"
        assert g.allows(0, 110.0), "line 10 pts below the fill is within reach"
        assert not g.allows(0, 125.0), "line 25 pts below is past the 20-pt reach"
        assert not g.allows(1, 100.0), "line above the fill is no floor"
        assert not g.allows(2, 100.0), "a NaN line must not read as confirmed"

        # The short mirror: the floor becomes a ceiling within reach ABOVE.
        sctx = confmod.SessionCtx(cfg=ctx.cfg, day=ctx.day, ticks=ctx.ticks,
                                  bars=ctx.bars, value_edge_at_tick=None,
                                  profile=None, side="short")
        g = gatesmod.GxFloorGate({"enabled": True, "max_ticks_below": 80})
        g.prepare(sctx)
        assert g.allows(1, 90.0), "line 5 pts above a short's fill is a ceiling"
        assert not g.allows(1, 100.0), "line below a short's fill is no ceiling"

        # Blind — no cached overnight — vetoes wholesale.
        gatesmod.tickmod.cached_overnight = lambda symbol, day: None
        g = gatesmod.GxFloorGate({"enabled": True})
        g.prepare(ctx)
        assert not any(g.allows(i, 100.0) for i in range(4))
    finally:
        gatesmod.tickmod.contract_for_cached = real_contract
        gatesmod.tickmod.cached_overnight = real_on
        gatesmod.vwapmod.vwap_bands = real_bands

    for bad in ({"enabled": True, "max_ticks": 10}, {"enabled": True, "max_ticks_below": -1},
                {"enabled": True, "max_ticks_below": True}):
        try:
            gatesmod.GxFloorGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_on_high_gate_wants_the_fill_within_reach_of_the_overnight_high():
    from journal.sim import gates as gatesmod

    ctx = _regime_ctx()
    on = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-10-13 09:00"] * 3, utc=True),
                       "price": [95.0, 110.0, 100.0], "size": [1, 1, 1]})

    real_contract = gatesmod.tickmod.contract_for_cached
    real_on = gatesmod.tickmod.cached_overnight
    try:
        gatesmod.tickmod.contract_for_cached = lambda symbol, day: "NQZ5"
        gatesmod.tickmod.cached_overnight = lambda symbol, day: on

        g = gatesmod.OnHighGate({"enabled": True, "max_ticks_below": 100})  # 25 pts on NQ
        g.prepare(ctx)  # wall = overnight high = 110
        assert g.allows(0, 120.0), "a fill above the overnight high always passes"
        assert g.allows(0, 110.0), "a fill on the wall itself passes"
        assert g.allows(0, 85.0), "25 pts beneath is exactly within reach"
        assert not g.allows(0, 84.75), "one tick past the reach is vetoed"

        # The short mirror: the wall is the overnight LOW, reach is above it.
        sctx = confmod.SessionCtx(cfg=ctx.cfg, day=ctx.day, ticks=ctx.ticks,
                                  bars=pd.DataFrame(), value_edge_at_tick=None,
                                  profile=None, side="short")
        g = gatesmod.OnHighGate({"enabled": True, "max_ticks_below": 100})
        g.prepare(sctx)  # wall = overnight low = 95
        assert g.allows(0, 90.0), "a fill below the overnight low always passes"
        assert g.allows(0, 120.0), "25 pts above the low is exactly within reach"
        assert not g.allows(0, 120.25), "one tick past the reach is vetoed"

        # Blind — no cached overnight — vetoes wholesale.
        gatesmod.tickmod.cached_overnight = lambda symbol, day: None
        g = gatesmod.OnHighGate({"enabled": True})
        g.prepare(ctx)
        assert not g.allows(0, 200.0), "no overnight must not read as confirmed"
    finally:
        gatesmod.tickmod.contract_for_cached = real_contract
        gatesmod.tickmod.cached_overnight = real_on

    for bad in ({"enabled": True, "max_ticks": 10}, {"enabled": True, "max_ticks_below": -1},
                {"enabled": True, "max_ticks_below": True}):
        try:
            gatesmod.OnHighGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_gx_value_gate_reads_the_globex_value_area_not_the_sessions():
    from journal.sim import gates as gatesmod
    from journal.sim.rules import SimConfig as _Cfg

    # Overnight tape: 6 of 8 contracts print at 100, one each at 90 and 110.
    # The 70% value area is the single 100 level — VAH = VAL = 100 — and with
    # ticks_per_bar=4 both overnight bars have closed before RTH begins, so the
    # level is in force from the first session tick.
    on = pd.DataFrame({
        "ts_utc": pd.to_datetime(["2025-10-13 09:00"] * 8, utc=True),
        "price": [100.0, 100.0, 90.0, 100.0, 100.0, 110.0, 100.0, 100.0],
        "size": [1] * 8,
    })
    rth = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-10-13 13:35"] * 4, utc=True),
                        "price": [101.0] * 4, "size": [1] * 4})
    cfg = _Cfg(ticks_per_bar=4)
    ctx = confmod.SessionCtx(cfg=cfg, day=DAY, ticks=rth, bars=pd.DataFrame(),
                             value_edge_at_tick=None, profile=None)

    real_contract = gatesmod.tickmod.contract_for_cached
    real_on = gatesmod.tickmod.cached_overnight
    try:
        gatesmod.tickmod.contract_for_cached = lambda symbol, day: "NQZ5"
        gatesmod.tickmod.cached_overnight = lambda symbol, day: on

        g = gatesmod.GxValueGate({"enabled": True})
        g.prepare(ctx)  # Globex VAH in force at every RTH tick = 100
        assert g.allows(0, 101.0), "a fill above the Globex VAH passes"
        assert g.allows(0, 100.0), "a fill on the edge passes at margin 0"
        assert not g.allows(0, 99.0), "a fill back inside overnight value is vetoed"

        g = gatesmod.GxValueGate({"enabled": True, "max_ticks_inside": 8})  # 2 pts
        g.prepare(ctx)
        assert g.allows(0, 98.0), "2 pts inside is within the configured tolerance"
        assert not g.allows(0, 97.75), "one tick past the tolerance is vetoed"

        # The short mirror reads the VAL: beyond means BELOW it.
        sctx = confmod.SessionCtx(cfg=cfg, day=DAY, ticks=rth, bars=pd.DataFrame(),
                                  value_edge_at_tick=None, profile=None, side="short")
        g = gatesmod.GxValueGate({"enabled": True})
        g.prepare(sctx)
        assert g.allows(0, 99.0), "a fill below the Globex VAL passes the short"
        assert not g.allows(0, 101.0), "a fill above it is inside value for a short"

        # Blind — no cached overnight — vetoes wholesale.
        gatesmod.tickmod.cached_overnight = lambda symbol, day: None
        g = gatesmod.GxValueGate({"enabled": True})
        g.prepare(ctx)
        assert not g.allows(0, 200.0), "no overnight must not read as confirmed"
    finally:
        gatesmod.tickmod.contract_for_cached = real_contract
        gatesmod.tickmod.cached_overnight = real_on

    for bad in ({"enabled": True, "min_ticks": 1}, {"enabled": True, "max_ticks_inside": -1},
                {"enabled": True, "max_ticks_inside": True}):
        try:
            gatesmod.GxValueGate(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


# --- preflight spend guard ------------------------------------------------------

def test_preflight_globex_needs_the_overnight_segment_too():
    """A day with only its RTH file cached still costs money for a globex
    strategy — the guard must count it and price the overnight range. Same for an
    RTH strategy under the default fetch scope, which buys the night for the
    charts; only ``fetch_overnight=False`` (the form's "NY session only") counts
    that day as paid for."""
    old_dir, old_est = ticks.TICK_CACHE_DIR, ticks.estimate_cost
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ticks.TICK_CACHE_DIR = Path(tmp) / "ticks"
            ticks.TICK_CACHE_DIR.mkdir(parents=True)
            priced: list[bool] = []

            def fake_cost(symbol, start, end, include_overnight=False):
                priced.append(include_overnight)
                return 1.0

            ticks.estimate_cost = fake_cost
            # Pinned, so the guard is measured against one known symbol: what's under
            # test is which *segments* get priced, not which contract they belong to.
            cfg = SimConfig(contract="NQZ5", start_date=DAY, end_date=DAY)
            ticks._cache_path(cfg.contract, DAY).touch()  # RTH cached, overnight not

            assert runner.preflight(cfg, fetch_overnight=False)["uncached_sessions"] == 0
            assert runner.preflight(cfg)["uncached_sessions"] == 1, \
                "the default scope buys the night for the charts"
            pf = runner.preflight(cfg, "globex")
            assert pf["uncached_sessions"] == 1, "RTH alone must not satisfy globex"
            assert priced == [True, True], "the estimate must span the overnight range"
            assert pf["est_cost_usd"] == 1.0

            ticks._cache_path(cfg.contract, DAY, "on").touch()
            assert runner.preflight(cfg, "globex")["uncached_sessions"] == 0
            assert runner.preflight(cfg)["uncached_sessions"] == 0
        finally:
            ticks.TICK_CACHE_DIR = old_dir
            ticks.estimate_cost = old_est


def test_run_buys_the_overnight_for_the_charts_unless_rth_only():
    """An RTH strategy's run pulls the night too — the charts can't, they only read
    the cache. `rth_only` on the request is what skips it, and neither choice may
    touch the run's identity: it buys data, it doesn't change a rule."""
    strat = registry.get(SLUG)
    asked: list[date] = []
    old = ticks.ensure_overnight
    try:
        ticks.ensure_overnight = lambda symbol, day: asked.append(day) or True

        for body, want in ((api.ConfigIn(), True), (api.ConfigIn(rth_only=True), False)):
            assert api._fetch_overnight(strat, body) is want

        # A globex strategy simulates on the night, so it cannot honour rth_only.
        globex = registry.get("vwap-globex-bounce")
        assert api._fetch_overnight(globex, api.ConfigIn(rth_only=True)) is True

        cfg = SimConfig(start_date=DAY, end_date=DAY)
        rth_only_id = store.run_id(cfg, strat.version)
        assert rth_only_id == store.run_id(cfg, strat.version), "scope is not in the hash"

        with _TmpStore():
            rid = store.init_run(SLUG, cfg, strat.version, 1)
            runner.run_to_completion(strat, cfg, rid, fetch_overnight=False)
            assert asked == [], "rth_only must not buy the night"

            rid2 = store.init_run(SLUG, cfg, strat.version + "x", 1)
            runner.run_to_completion(strat, cfg, rid2)
            assert asked == [DAY], "the default scope buys the night, once per session"
    finally:
        ticks.ensure_overnight = old


def test_a_dead_overnight_fetch_does_not_fail_the_run():
    """The night is context for the charts, not an input to the rules — a run whose
    RTH ticks are all present must not land in 'error' because the garnish failed."""
    if not ticks._cache_path("NQZ5", DAY).exists():
        print("   (skipped: tick cache cold)")
        return
    old = ticks._get_segment
    try:
        def boom(symbol, day, segment, use_cache):
            if segment == "on":
                raise RuntimeError("databento is down")
            return old(symbol, day, segment, use_cache)

        ticks._get_segment = boom
        assert ticks.ensure_overnight("NQZ5", DAY) is False

        with _TmpStore():
            strat = registry.get(SLUG)
            cfg = SimConfig(start_date=DAY, end_date=DAY)
            rid = runner.execute(strat, cfg)
            assert store.read_state(SLUG, rid)["status"] == "done"
    finally:
        ticks._get_segment = old


# --- API ----------------------------------------------------------------------

def _drain(bt: BackgroundTasks) -> None:
    for t in bt.tasks:
        t.func(*t.args, **t.kwargs)


def test_api_create_run_dedupes_and_conflicts():
    if not ticks._cache_path("NQZ5", DAY).exists():
        print("   (skipped: tick cache cold)")
        return
    with _TmpStore():
        body = api.ConfigIn(config={"start_date": "2025-10-13", "end_date": "2025-10-13"})
        bt = BackgroundTasks()
        res = api.create_run(SLUG, body, bt)
        assert res["status"] == "running" and not res["already_existed"]
        # While the (not yet drained) run is in flight, the same config conflicts.
        try:
            api.create_run(SLUG, body, BackgroundTasks())
            raise AssertionError("expected 409 while running")
        except HTTPException as exc:
            assert exc.status_code == 409
        _drain(bt)

        st = store.read_state(SLUG, res["run_id"])
        assert st["status"] == "done", st
        assert store.baseline(SLUG) == res["run_id"]

        again = api.create_run(SLUG, body, BackgroundTasks())
        assert again["already_existed"], "same config+code must resolve to the artifact"

        detail = api.run_detail(SLUG, res["run_id"])
        assert detail["metrics"]["trades"] == len(detail["trades"])
        assert detail["session_days"] == ["2025-10-13"]

        pf = api.preflight(SLUG, body)
        assert pf["exists"] and pf["uncached_sessions"] == 0


def test_run_edges_partition_the_run_and_only_a_sim_knows_why_it_exited():
    """Every cut is a partition: the same trades, sliced. If a bucket goes missing
    (a weekday nobody traded, a session block off the grid) the table silently
    under-reports the run, so the totals are what this pins."""
    if not ticks._cache_path("NQZ5", DAY).exists():
        print("   (skipped: tick cache cold)")
        return
    with _TmpStore():
        body = api.ConfigIn(config={"start_date": "2025-10-13", "end_date": "2025-10-13"})
        bt = BackgroundTasks()
        rid = api.create_run(SLUG, body, bt)["run_id"]
        _drain(bt)

        _, trades, metrics = store.read_run(SLUG, rid)
        if trades.empty:
            print("   (skipped: no trades that session)")
            return

        payload = api.run_edges(SLUG, rid, compare=None)
        traded = payload["scopes"]["traded"]
        assert traded["trades"] == len(trades)
        assert [c["name"] for c in traded["cuts"]] == list(api.TRADED_CUTS)

        for cut in traded["cuts"]:
            rows = cut["rows"]
            assert sum(r["trades"] for r in rows) == len(trades), f"{cut['name']} loses trades"
            assert abs(sum(r["net_pnl"] for r in rows) - metrics["net_pnl"]) < 1e-6, cut["name"]

        # The cut the journal cannot have: the engine is the only thing that knows
        # a trade died on its stop rather than at its target.
        by_name = {c["name"]: c for c in traded["cuts"]}
        exits = by_name["by_exit_reason"]
        assert {r["bucket"] for r in exits["rows"]} <= set(trades["exit_reason"])

        # ...and the one it must never *score*: a stop is a loss by construction, so
        # a permutation test would call it significant every single time.
        assert exits["knowable"] is False and exits["luck"] is None
        assert by_name["by_hold_time"]["luck"] is None, "hold time is an outcome too"

        try:
            api.run_edges(SLUG, "no-such-run", compare=None)
            raise AssertionError("expected 404")
        except HTTPException as exc:
            assert exc.status_code == 404


def test_a_cut_that_separates_nothing_does_not_hold():
    """The luck column earns its place by being hard to satisfy. Trades whose P&L
    has nothing to do with their bucket must not clear the bar — and a cut that
    perfectly predicts P&L must, or the test is decoration."""
    n = 60
    ts = pd.date_range("2025-10-13 14:00", periods=n, freq="5min", tz="UTC")
    noise = [(-1) ** i * (100 + 7 * i) for i in range(n)]  # sign has no weekday pattern
    base = pd.DataFrame({
        "entry_ts_utc": ts,
        "entry_ts_local": ts.tz_convert("America/New_York"),
        "duration_s": [120.0] * n,
        "direction": ["Long"] * n,
        "net_pnl": noise,
        "r_multiple": [v / 400 for v in noise],
    })
    assert edges.luck(base, "by_hour_et") > edges.luck_bar(("by_hour_et",))

    # Now make the bucket *be* the answer: every trade in the first hour wins.
    rigged = base.copy()
    rigged["net_pnl"] = [500.0 if t.hour == 14 else -500.0 for t in ts]
    p = edges.luck(rigged, "by_hour_et")
    assert p <= edges.luck_bar(("by_hour_et",)), p

    # And the R column is the average of the R's, not a re-derivation from dollars.
    row = edges.by_direction(base).iloc[0]
    assert abs(row["avg_r"] - base["r_multiple"].mean()) < 1e-9
    assert row["trades"] == n


def test_api_bad_config_is_a_400_not_a_run():
    with _TmpStore():
        try:
            api.create_run(SLUG, api.ConfigIn(config={"stop_tickss": 50}), BackgroundTasks())
            raise AssertionError("expected 400")
        except HTTPException as exc:
            assert exc.status_code == 400 and "stop_tickss" in str(exc.detail)
        assert store.list_runs(SLUG) == []


# --- config schema: canonicalization & enforcement ----------------------------

def test_int_and_float_spellings_are_the_same_run():
    """The config IS the run's identity (run_id sha1s it), so a form emitting 7.0
    where the artifact on disk holds 7 would re-run the config it meant to reuse.
    Coercion to the declared type is what makes the hash a fact about the rules
    rather than about who typed them."""
    a = store.config_from_json({"commission_per_side": 7, "target": "rr", "target_rr": 2})
    b = store.config_from_json({"commission_per_side": 7.0, "target": "rr", "target_rr": 2.0})
    assert a == b
    assert store.run_id(a, "3") == store.run_id(b, "3")

    # Gate sections are part of the hash too, so they canonicalize as well.
    g1 = store.config_from_json({"confluences": {"volume_profile": {"enabled": True,
                                                                    "min_ticks_above_vah": 4.0}}})
    g2 = store.config_from_json({"confluences": {"volume_profile": {"enabled": True,
                                                                    "min_ticks_above_vah": 4}}})
    assert store.run_id(g1, "3") == store.run_id(g2, "3")


def test_configs_the_engine_would_lie_about_are_400s():
    """Each of these is a config the engine accepts and then quietly does
    something other than what it says — the worst kind, because the run comes back
    green. They belong at the door, not in a background thread."""
    bad = {
        # ZeroDivisionError computing r_multiple, deep inside the worker thread:
        # the run lands in state 'error' instead of the POST failing.
        "stop_ticks": 0,
        # Engine only ever compares entry_variant to "A"/"B" — a typo takes zero
        # entries and reports a clean run of 0 trades.
        "entry_variant": "C",
        # Engine only compares target to "rr" — anything else silently means dev2.
        "target": "dev3",
        "contracts": 0,
        "ticks_per_bar": 0,
        "acceptance_min_ticks": -1,
    }
    for key, value in bad.items():
        with _TmpStore():
            try:
                api.create_run(SLUG, api.ConfigIn(config={key: value}), BackgroundTasks())
                raise AssertionError(f"expected 400 for {key}={value!r}")
            except HTTPException as exc:
                assert exc.status_code == 400 and key in str(exc.detail)
            assert store.list_runs(SLUG) == [], f"{key} left a run behind"


def test_rr_target_without_an_rr_is_a_400():
    """`if cfg.target == "rr" and cfg.target_rr` — a null rr trades the dev2
    target while the config claims an R target."""
    try:
        store.config_from_json({"target": "rr", "target_rr": None})
        raise AssertionError("expected a rejection")
    except ValueError as exc:
        assert "target_rr" in str(exc)
    # ...but it is legal, and stays null, whenever the target isn't rr.
    assert store.config_from_json({"target": "dev2"}).target_rr is None


def test_invert_needs_an_rr_target_and_no_dev2_cap():
    """Inverting reverts toward the mid, so dev2 sits behind the entry. A dev2
    target would fill instantly at a loss and an acceptance capped at dev2 could
    never arm — both must be refused, not quietly mis-simulated."""
    G = GlobexBounceConfig
    try:
        store.config_from_json({"invert": True, "target": "dev2"}, G)
        raise AssertionError("expected a rejection")
    except ValueError as exc:
        assert "target" in str(exc) and "rr" in str(exc)
    try:
        store.config_from_json(
            {"invert": True, "target": "rr", "target_rr": 1.5,
             "acceptance_cap_at_dev2": True}, G)
        raise AssertionError("expected a rejection")
    except ValueError as exc:
        assert "acceptance_cap_at_dev2" in str(exc)
    # invert with an R target is fine; and the whole gate is off when invert is off
    # (dev2 stays a legal target for the plain bounce).
    assert store.config_from_json(
        {"invert": True, "target": "rr", "target_rr": 1.5}, G).invert is True
    assert store.config_from_json({"invert": False, "target": "dev2"}, G).target == "dev2"


def test_a_limit_offset_past_the_acceptance_close_is_rejected():
    """The acceptance candle closes just over acceptance_min_ticks beyond dev1, so a
    limit offset further out than that is already through the market when the setup
    arms. A broker fills that instantly; the engine would rest it and wait for a
    touch that has already happened — a different rule than the one on screen."""
    try:
        store.config_from_json({"acceptance_min_ticks": 30, "entry_limit_offset_ticks": 31})
        raise AssertionError("expected a rejection")
    except ValueError as exc:
        assert "entry_limit_offset_ticks" in str(exc)

    # At the boundary the limit still sits strictly behind the close (acceptance is
    # a strict inequality), so it rests.
    cfg = store.config_from_json({"acceptance_min_ticks": 30, "entry_limit_offset_ticks": 30})
    assert cfg.entry_limit_offset_ticks == 30
    # Variant B never reads the knob, so a value left over from A must not 400.
    assert store.config_from_json(
        {"entry_variant": "B", "entry_limit_offset_ticks": 99}
    ).entry_limit_offset_ticks == 99


def test_zero_still_means_off():
    """The four 0-means-off knobs keep their sentinel — the form renders a
    checkbox over it, but the wire format (and so every existing artifact's hash)
    must not move."""
    cfg = store.config_from_json({"trail_step_ticks": 0, "exit_below_vah_bars": 0,
                                  "min_band_width_ticks": 0, "invalidate_below_mid_bars": 0})
    assert cfg.trail_step_ticks == 0 and cfg.invalidate_below_mid_bars == 0


def test_bools_do_not_leak_into_number_fields():
    """isinstance(True, int) is True in Python, so a stray `true` would otherwise
    sail into an int knob and hash as 1."""
    for bad in ({"stop_ticks": True}, {"acceptance_require_green": 1}):
        try:
            store.config_from_json(bad)
            raise AssertionError(f"expected a rejection for {bad}")
        except ValueError:
            pass


def test_a_stored_config_predating_a_knob_still_loads():
    """Artifacts written before trail_stop_ticks existed simply lack the key.
    Absent means default; only *unknown* keys are an error."""
    cfg = store.config_from_json({"instrument": "NQ", "stop_ticks": 75})
    assert cfg.trail_stop_ticks == 0 and cfg.trail_step_ticks == 0


def test_a_stored_one_knob_trail_still_means_what_it_meant():
    """Before the trail's distance and its step were separate knobs, the single
    trail_step_ticks was both. Every stored run is read back through parse, and a
    run must replay to the trades it reported — so an artifact that carries only
    the old key loads as a trail of that distance, not as a trail switched off."""
    cfg = store.config_from_json({"trail_step_ticks": 75})
    assert cfg.trail_stop_ticks == 75 and cfg.trail_step_ticks == 75

    # ...and the new form, which always writes both, is taken at its word: an
    # explicit 0 distance is the trail off, whatever the step happens to hold.
    off = store.config_from_json({"trail_stop_ticks": 0, "trail_step_ticks": 75})
    assert off.trail_stop_ticks == 0
    both = store.config_from_json({"trail_stop_ticks": 50, "trail_step_ticks": 25})
    assert both.trail_stop_ticks == 50 and both.trail_step_ticks == 25


def test_every_knob_is_reachable_from_the_form():
    """A knob added to SimConfig without a descriptor would be invisible in the UI
    and unvalidated on the way in. schema.py asserts this at import; this is the
    same guard, where a failure names itself."""
    described = {f["name"] for f in schema.config_schema()["fields"]}
    assert set(SimConfig().to_json()) - described == {"confluences"}

    sch = schema.config_schema(registry.get(SLUG).confluences)
    groups = {g["key"] for g in sch["groups"]}
    assert {f["group"] for f in sch["fields"]} <= groups
    vp = next(c for c in sch["confluences"] if c["name"] == "volume_profile")
    assert {f["name"] for f in vp["fields"]} == {"enabled", "min_ticks_above_vah"}


def test_a_new_run_can_be_labelled_at_creation():
    with _TmpStore():
        bt = BackgroundTasks()
        res = api.create_run(SLUG, api.ConfigIn(
            config={"start_date": "2025-10-13", "end_date": "2025-10-13"},
            label="stop 50, trail 75"), bt)
        assert store.read_meta(SLUG, res["run_id"])["label"] == "stop 50, trail 75"


def test_api_delete_guards_and_baseline_pin():
    with _TmpStore():
        rid1 = _finished_run(SimConfig())
        rid2 = _finished_run(SimConfig(stop_ticks=50))
        running = store.init_run(SLUG, SimConfig(stop_ticks=60), "1", sessions_total=5)

        try:
            api.delete_run(SLUG, running)
            raise AssertionError("expected 409 for an in-flight run")
        except HTTPException as exc:
            assert exc.status_code == 409

        try:
            api.pin_baseline(SLUG, api.BaselineIn(run_id=running))
            raise AssertionError("expected 409: only completed runs can be baseline")
        except HTTPException as exc:
            assert exc.status_code == 409

        api.pin_baseline(SLUG, api.BaselineIn(run_id=rid2))
        assert store.baseline(SLUG) == rid2
        api.delete_run(SLUG, rid1)
        assert [r["run_id"] for r in store.list_runs(SLUG) if r["run_id"] == rid1] == []


def test_api_rerun_baseline_uses_current_version():
    if not ticks._cache_path("NQZ5", DAY).exists():
        print("   (skipped: tick cache cold)")
        return
    with _TmpStore():
        cfg = SimConfig(start_date=DAY, end_date=DAY)
        rid_old = store.init_run(SLUG, cfg, "0", sessions_total=1)
        store.finish_run(SLUG, rid_old, _fake_trades(), pd.DataFrame(), {"trades": 1})
        store.set_baseline(SLUG, rid_old)

        bt = BackgroundTasks()
        res = api.rerun_baseline(SLUG, bt)
        _drain(bt)
        assert res["run_id"] != rid_old, "new engine version must be a new artifact"
        st = store.read_state(SLUG, res["run_id"])
        assert st["status"] == "done"
        assert st["engine_version"] == registry.get(SLUG).version
        assert store.read_state(SLUG, rid_old) is not None, "the old artifact survives"


# --- the fade's config class ---------------------------------------------------
#
# The fade is the first strategy with its own config class (registry.config_cls),
# so these pin the seams that make two classes safe to coexist: per-class parsing
# and schema, per-class cross-field rules — and, above all, that adding the fade
# changed NOTHING about how a bounce config serializes, because run identity
# hashes the serialization and every stored bounce run must keep its id.

FADE_SLUG = "vwap-dev1-fade-short"
FADE_LONG_SLUG = "vwap-dev1-fade-long"


def test_fade_registry_entry():
    strat = registry.get(FADE_SLUG)
    assert strat.config_cls is FadeConfig
    assert strat.version == "2"   # v2: arm_stretch_side
    assert strat.session == "rth"
    assert strat.confluences == ("volume_profile", "vwap_slope_cap",
                                 "upper_occupancy_cap", "gx_rescue_cap")


def test_fade_long_registry_entry_is_the_short_reflected():
    """Same config class, same schema, same caps — one strategy, two sides. The
    long is a separate registry entry (and so a separate run history) because
    the band it reads is coded, not configured."""
    long_, short = registry.get(FADE_LONG_SLUG), registry.get(FADE_SLUG)
    assert long_.config_cls is short.config_cls is FadeConfig
    assert long_.confluences == short.confluences
    assert long_.session == short.session
    assert long_.run_session is engine.run_session_fade_long
    assert long_.run_session is not short.run_session
    # Distinct histories: the same config on the two sides is two different runs.
    assert store.run_id(FadeConfig(), long_.version) == store.run_id(
        FadeConfig(), short.version), "identity hashes the config, not the slug"
    assert long_.slug != short.slug


def test_fade_config_is_its_own_identity():
    assert store.run_id(FadeConfig(), "1") != store.run_id(SimConfig(), "1"), \
        "a fade run and a bounce run of the same window must never share an id"
    assert store.run_id(FadeConfig(), "1") != store.run_id(
        FadeConfig(arm_extension_ticks=60), "1"), "config must fork the id"
    # Coercion applies to the fade exactly as to the bounce: one spelling, one id.
    a = store.config_from_json({"daily_loss_stop": 500}, FadeConfig)
    b = store.config_from_json({"daily_loss_stop": 500.0}, FadeConfig)
    assert store.run_id(a, "1") == store.run_id(b, "1")


def test_fade_config_rejects_what_its_engine_cannot_read():
    for bad in ({"acceptance_min_ticks": 30},          # a bounce knob
                {"exit_below_vah_bars": 1},            # ditto
                {"arm_extension_ticks": 0},            # below its minimum
                {"entry_limit_offset_ticks": 60},      # beyond the default 50 stretch
                {"target": "dev2"},                    # the bounce's target
                {"target": "rr", "target_rr": None}):
        try:
            store.config_from_json(bad, FadeConfig)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected a ValueError for {bad}")
    # And the mirror: a fade knob is an unknown key on a bounce config.
    try:
        store.config_from_json({"arm_extension_ticks": 50})
    except ValueError:
        pass
    else:
        raise AssertionError("expected a ValueError for a fade knob on SimConfig")


def test_fade_default_config_roundtrips():
    cfg = FadeConfig()
    assert store.config_from_json(cfg.to_json(), FadeConfig) == cfg


def test_fade_schema_covers_every_field_both_ways():
    described = {f.name for f in schema.FADE_FIELDS}
    assert set(FadeConfig().to_json()) - described == {"confluences"}
    assert described - set(FadeConfig().to_json()) == set()


def test_adding_the_fade_left_bounce_identity_untouched():
    """The reason FadeConfig is a class and not new SimConfig fields: a knob
    added to the shared class would change every stored bounce run's hash. The
    default bounce config must serialize exactly the fields it always had."""
    d = SimConfig().to_json()
    assert "arm_extension_ticks" not in d
    assert store.run_id(store.config_from_json({}), "6") == store.run_id(SimConfig(), "6")


def test_fade_strategy_detail_serves_its_own_schema():
    detail = api.strategy_detail(FADE_SLUG)
    assert detail["default_config"] == FadeConfig().to_json()
    names = {f["name"] for f in detail["config_schema"]["fields"]}
    assert "arm_extension_ticks" in names and "acceptance_min_ticks" not in names
    assert [g["key"] for g in detail["config_schema"]["groups"]].count("arming") == 1
    assert [c["name"] for c in detail["config_schema"]["confluences"]] == [
        "volume_profile", "vwap_slope_cap", "upper_occupancy_cap", "gx_rescue_cap"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
