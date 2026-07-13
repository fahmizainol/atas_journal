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

from journal.sim import confluences as confmod  # noqa: E402
from journal.sim import engine, registry, runner, schema, store, ticks  # noqa: E402
from journal.sim.rules import SimConfig  # noqa: E402
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


def _regime_ctx(day=DAY):
    """A minimal SessionCtx for the regime gate: it reads only cfg, day, and
    tick timestamps. Ticks straddle the 10:30 ET checkpoint."""
    ts = pd.to_datetime(
        ["2025-10-13 13:35", "2025-10-13 14:29", "2025-10-13 14:30", "2025-10-13 15:00"],
        utc=True)  # 09:35, 10:29, 10:30, 11:00 ET
    t = pd.DataFrame({"ts_utc": ts})
    return confmod.SessionCtx(cfg=SimConfig(), day=day, ticks=t, bars=pd.DataFrame(),
                              value_edge_at_tick=None, profile=None)


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
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "bbr": 0.6}, {"enabled": True, "bbr_max": 1.5},
                {"enabled": True, "bbr_max": True}):
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
    finally:
        gatesmod.regmod.get_regime = real

    for bad in ({"enabled": True, "slope": 0.0}, {"enabled": True, "slope_min": 9.0},
                {"enabled": True, "slope_min": True}):
        try:
            gatesmod.VwapSlopeGate(bad)
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
    """Artifacts written before trail_step_ticks existed simply lack the key.
    Absent means default; only *unknown* keys are an error."""
    cfg = store.config_from_json({"instrument": "NQ", "stop_ticks": 75})
    assert cfg.trail_step_ticks == 0


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
