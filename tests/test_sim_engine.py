"""Strategy simulator: tick bars, tick VWAP, and the engine's trade invariants.

The unit tests are hand-computable. The engine tests run over the *real* cached
NQZ5 session — a synthetic tick stream can't produce a realistic VWAP band, and
the whole point of these assertions is that the engine obeys its rules against
the same data it will actually be judged on. They skip if the tick cache is cold.

Run directly:  ``.venv/bin/python tests/test_sim_engine.py``
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from journal.sim import bars as barmod  # noqa: E402
from journal.sim import confluences as confmod  # noqa: E402
from journal.sim import engine, ticks  # noqa: E402
from journal.sim import profile as profmod  # noqa: E402
from journal.sim import vwap as vwapmod  # noqa: E402
from journal.sim.rules import (  # noqa: E402
    DriftFadeConfig, FadeConfig, GlobexBounceConfig, ProfilePullbackConfig,
    SimConfig)

TICK = 0.25
DAY = date(2025, 10, 13)


def _synth(prices, sizes=None) -> pd.DataFrame:
    n = len(prices)
    return pd.DataFrame({
        "ts_utc": pd.date_range("2025-10-13 13:30", periods=n, freq="s", tz="UTC"),
        "price": [float(p) for p in prices],
        "size": [1] * n if sizes is None else sizes,
        "side": ["A"] * n,
    })


def test_tick_bars_aggregate_by_count():
    b = barmod.tick_bars(_synth([1, 5, 2, 4, 3, 9, 7, 8]), n=4)
    assert len(b) == 2, b
    assert b.loc[0, "open"] == 1 and b.loc[0, "close"] == 4
    assert b.loc[0, "high"] == 5 and b.loc[0, "low"] == 1
    assert b.loc[0, "volume"] == 4
    assert (b.loc[0, "start_idx"], b.loc[0, "end_idx"]) == (0, 3)
    # ts is the bar's LAST tick — the instant its close became known.
    assert b.loc[0, "ts_utc"] == pd.Timestamp("2025-10-13 13:30:03", tz="UTC")


def test_tick_bars_drop_incomplete_tail():
    # 7 ticks, 4 per bar -> one bar; the 3 leftovers never closed, so no
    # close-based rule may see them.
    assert len(barmod.tick_bars(_synth([1] * 7), n=4)) == 1
    assert barmod.tick_bars(_synth([1, 2]), n=4).empty


def test_vwap_is_volume_weighted():
    # prices 10,20 with sizes 1,3 -> vwap = (10*1 + 20*3)/4 = 17.5
    w = vwapmod.vwap_bands(_synth([10, 20], sizes=[1, 3]))
    assert abs(w["mid"].iloc[-1] - 17.5) < 1e-9
    # var = (100*1 + 400*3)/4 - 17.5^2 = 325 - 306.25 = 18.75
    assert abs(w["std"].iloc[-1] ** 2 - 18.75) < 1e-9
    assert abs(w["upper1"].iloc[-1] - (17.5 + w["std"].iloc[-1])) < 1e-9
    assert abs(w["upper2"].iloc[-1] - (17.5 + 2 * w["std"].iloc[-1])) < 1e-9


def test_vwap_flat_series_has_zero_sigma():
    # Constant price: variance is exactly 0, and float cancellation must not
    # push it negative and NaN the sqrt.
    w = vwapmod.vwap_bands(_synth([100.0] * 50))
    assert (w["std"] >= 0).all()
    assert w["std"].iloc[-1] < 1e-6
    assert not w[["mid", "upper1", "upper2"]].isna().any().any()


def test_overnight_bounds_start_the_previous_evening():
    # Monday session: Globex opened Sunday 18:00 ET (22:00 UTC under EDT). The
    # segment ends exactly at the RTH open — get_range is end-exclusive, so the
    # two segments meet at 09:30 with no gap and no duplicate tick.
    s, e = ticks.overnight_bounds_utc(DAY)
    assert s == pd.Timestamp("2025-10-12 22:00", tz="UTC")
    assert e == ticks.session_bounds_utc(DAY)[0]
    assert e == pd.Timestamp("2025-10-13 13:30", tz="UTC")


def test_overnight_segment_splices_in_front_of_rth():
    import tempfile

    def frame(start, prices):
        n = len(prices)
        return pd.DataFrame({
            "ts_utc": pd.date_range(start, periods=n, freq="s", tz="UTC"),
            "price": [float(p) for p in prices],
            "size": [1] * n,
            "side": ["A"] * n,
        })

    old = ticks.TICK_CACHE_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ticks.TICK_CACHE_DIR = Path(tmp)
            ticks._read_day_parquet.cache_clear()
            ticks.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            frame("2025-10-12 22:00", [1, 2, 3]).to_parquet(
                ticks._cache_path("TEST", DAY, "on"), index=False)
            frame("2025-10-13 13:30", [4, 5]).to_parquet(
                ticks._cache_path("TEST", DAY, "rth"), index=False)

            got = ticks.get_day_ticks("TEST", DAY, include_overnight=True)
            assert list(got["price"]) == [1.0, 2.0, 3.0, 4.0, 5.0]
            assert got["ts_utc"].is_monotonic_increasing
            # The default stays RTH-only — existing strategies read exactly
            # the series they always did.
            assert list(ticks.get_day_ticks("TEST", DAY)["price"]) == [4.0, 5.0]
        finally:
            ticks.TICK_CACHE_DIR = old
            ticks._read_day_parquet.cache_clear()


# --- engine invariants, against the real cached session --------------------

def _have_ticks() -> bool:
    return ticks._cache_path("NQZ5", DAY).exists()


def _same_trades(a: list[dict], b: list[dict]) -> bool:
    """Plain == on trade rows broke the day the excursion timers learned to say
    NaN ("never recovered"): NaN != NaN, so two byte-identical runs compared
    unequal. Same-key NaNs are equal here; everything else is still ==."""
    def _eq(x, y):
        return (x != x and y != y) or x == y
    return len(a) == len(b) and all(
        r.keys() == q.keys() and all(_eq(r[k], q[k]) for k in r)
        for r, q in zip(a, b))


def _session(variant: str):
    cfg = SimConfig(entry_variant=variant)
    trades, _, b, _ = engine.run_session(cfg, DAY)
    t = ticks.get_day_ticks("NQZ5", DAY)
    w = vwapmod.vwap_bands(t)
    return cfg, trades, t, w


def test_engine_stop_is_exactly_config_distance():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, _ = _session("A")
    assert trades
    for tr in trades:
        assert abs((tr["avg_entry"] - tr["stop_price"]) - cfg.stop_ticks * TICK) < 1e-9


def test_engine_variant_a_fills_on_the_dev1_line():
    """A resting buy limit at dev1 gets dev1 — not the (worse) traded price."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, t, w = _session("A")
    for tr in trades:
        i = tr["entry_idx"]
        assert abs(tr["avg_entry"] - w["upper1"].iloc[i]) < 1e-9
        assert t["price"].iloc[i] <= w["upper1"].iloc[i] + 1e-9  # price reached the limit


def test_engine_variant_a_fill_is_a_real_touch_even_behind_a_gate():
    """The dev1 limit may only fill on a tick where the market is actually AT dev1.

    The min_band_width_ticks gate used to suspend the entry check while leaving the
    setup armed, so price could cross dev1 and run to the far side of the session —
    and the check, waking up on the first tick the band was wide enough, booked a
    fill at a dev1 the market had left minutes earlier. On 2025-10-15 that short
    filled at the lower band while the tape was trading the upper 2σ, 300 points
    away, and 'stopped out' for 4x its stop distance.
    """
    day = date(2025, 10, 15)
    if not ticks._cache_path("NQZ5", day).exists():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(min_band_width_ticks=100, target="rr", target_rr=1.0,
                    invalidate_below_mid_bars=0)
    t = ticks.get_day_ticks("NQZ5", day)
    w = vwapmod.vwap_bands(t)
    for side, band in (("long", "upper1"), ("short", "lower1")):
        trades, _, _, _ = engine.run_session(cfg, day, side=side)
        assert trades, side
        for tr in trades:
            i = tr["entry_idx"]
            assert abs(tr["avg_entry"] - w[band].iloc[i]) < 1e-9, (side, tr)
            # The tape was at the limit when it filled — within a tick or two of
            # slippage through it, not on the other side of the VWAP.
            gap = abs(t["price"].iloc[i] - tr["avg_entry"]) / TICK
            assert gap <= 8, (side, tr["trade_no"] if "trade_no" in tr else i, gap)


def test_engine_exits_respect_their_reason():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, t, w = _session("A")
    seen = set()
    for tr in trades:
        seen.add(tr["exit_reason"])
        if tr["exit_reason"] == "stop":
            # Filled at the traded price, which is at or through the stop —
            # never better than reality.
            assert tr["avg_exit"] <= tr["stop_price"] + 1e-9
        elif tr["exit_reason"] == "target":
            i = tr["exit_idx"]
            assert abs(tr["avg_exit"] - w["upper2"].iloc[i]) < 1e-9
            assert tr["avg_exit"] > tr["avg_entry"]
    assert {"stop", "target"} <= seen, seen


def test_engine_acceptance_preceded_every_entry():
    """Acceptance must be a real, prior, green, >30-ticks-above-dev1 close."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, t, w = _session("A")
    b = barmod.tick_bars(t, cfg.ticks_per_bar)
    for tr in trades:
        acc = tr["acceptance_ts"]
        assert acc is not None and acc <= tr["entry_ts_utc"]
        bar = b[b["ts_utc"] == acc]
        assert len(bar) == 1, f"acceptance {acc} is not a bar close"
        row = bar.iloc[0]
        u1 = w["upper1"].iloc[int(row["end_idx"])]
        assert row["close"] > row["open"], "acceptance candle was not green"
        assert (row["close"] - u1) > cfg.acceptance_min_ticks * TICK


def test_engine_never_holds_two_positions_or_trades_past_the_bell():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, _ = _session("A")
    et = "America/New_York"
    for prev, cur in zip(trades, trades[1:]):
        assert cur["entry_ts_utc"] > prev["exit_ts_utc"], "positions overlapped"
    for tr in trades:
        entry = tr["entry_ts_utc"].tz_convert(et).time()
        assert cfg.entry_open <= entry < cfg.entry_close, f"entered at {entry}"
        assert tr["exit_ts_utc"].tz_convert(et).time() <= cfg.flat_by


def test_engine_rearms_only_on_a_fresh_acceptance():
    """After an exit, the next trade must be armed by an acceptance candle that
    formed *after* that exit — otherwise a stop-out could re-enter immediately on
    the same stale signal."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, _, _ = _session("A")
    for prev, cur in zip(trades, trades[1:]):
        assert cur["acceptance_ts"] > prev["exit_ts_utc"], (
            f"trade re-entered on an acceptance from before the previous exit "
            f"({cur['acceptance_ts']} <= {prev['exit_ts_utc']})"
        )


def test_engine_in_trade_ghosts_lie_inside_a_real_trade():
    """The shadow machine only sees what an open position hid: every "in_trade"
    ghost must have filled strictly inside one real trade's ticks, off an
    acceptance that formed at or after that trade's entry — anything else was a
    setup the live machine could have seen for itself."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    for variant in ("A", "B"):
        cfg = SimConfig(entry_variant=variant)
        trades, vetoed, _, _ = engine.run_session(cfg, DAY)
        ghosts = [v for v in vetoed if v["gate"] == "in_trade"]
        for g in ghosts:
            hosts = [tr for tr in trades
                     if tr["entry_idx"] < g["entry_idx"] < tr["exit_idx"]]
            assert hosts, f"in_trade ghost at idx {g['entry_idx']} fell outside every trade"
            assert g["acceptance_ts"] >= hosts[0]["entry_ts_utc"]


def test_engine_variant_b_enters_above_dev1():
    """B is a reclaim: the buy stop sits above dev1, so fills are never below it."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, t, w = _session("B")
    assert trades
    for tr in trades:
        i = tr["entry_idx"]
        level = w["upper1"].iloc[i] + cfg.entry_stop_offset_ticks * TICK
        assert tr["avg_entry"] >= level - 1e-9


# --- variant A's limit offset -------------------------------------------------


def test_limit_offset_off_changes_nothing():
    """The regression guard the version bump is really about: an offset of 0 rests
    the limit on dev1, exactly where it rested before the knob existed."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    base, _, _, _ = engine.run_session(SimConfig(), DAY)
    off, _, _, _ = engine.run_session(SimConfig(entry_limit_offset_ticks=0), DAY)
    assert _same_trades(base, off)


def test_limit_offset_fills_in_front_of_dev1():
    """The limit rests N ticks ABOVE dev1 on a long, so it fills on the way down
    before price reaches the band — and it gets its own price, not dev1's."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    off_ticks = 10
    cfg = SimConfig(entry_limit_offset_ticks=off_ticks)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    assert trades
    t = ticks.get_day_ticks("NQZ5", DAY)
    w = vwapmod.vwap_bands(t)
    for tr in trades:
        i = tr["entry_idx"]
        dev1 = w["upper1"].iloc[i]
        assert abs(tr["avg_entry"] - (dev1 + off_ticks * TICK)) < 1e-9
        assert tr["avg_entry"] > dev1  # in front of the band, never on or under it
        # A resting limit only fills on a genuine touch: the tape came down to it.
        assert t["price"].iloc[i] <= tr["avg_entry"] + 1e-9


def test_limit_offset_mirrors_onto_a_short():
    """'In front of dev1' is below it on a short — the lower band's limit fills on
    the way UP, before price reaches the band."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    off_ticks = 10
    cfg = SimConfig(entry_limit_offset_ticks=off_ticks)
    trades, _, _, _ = engine.run_session(cfg, DAY, side="short")
    t = ticks.get_day_ticks("NQZ5", DAY)
    w = vwapmod.vwap_bands(t)
    for tr in trades:
        i = tr["entry_idx"]
        dev1 = w["lower1"].iloc[i]
        assert abs(tr["avg_entry"] - (dev1 - off_ticks * TICK)) < 1e-9
        assert tr["avg_entry"] < dev1
        assert t["price"].iloc[i] >= tr["avg_entry"] - 1e-9


def test_limit_offset_entries_are_earlier_and_never_cheaper():
    """What the knob is FOR: the pullback is met sooner. Every trade the offset run
    takes must fill at a worse price than the dev1 run would have — you are paying
    up to be filled — and the offset can only find entries the dev1 limit also saw
    or missed entirely, never at a better price."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    at_band, _, _, _ = engine.run_session(SimConfig(), DAY)
    ahead, _, _, _ = engine.run_session(SimConfig(entry_limit_offset_ticks=10), DAY)
    assert ahead
    for tr in ahead:
        assert tr["avg_entry"] > 0
    # The touch of dev1+10 always precedes the touch of dev1 on the same leg down.
    for a, b in zip(ahead, at_band):
        if a["acceptance_ts"] == b["acceptance_ts"]:
            assert a["entry_idx"] <= b["entry_idx"], "the closer limit filled later"
            assert a["avg_entry"] >= b["avg_entry"] - 1e-9, "paid less than at dev1"


# --- averaging down (pyramid_direction="against") ---------------------------

def _avg_down_session(stop_mode: str):
    cfg = SimConfig(contracts=3, stop_ticks=150,
                    pyramid_tranches=3, pyramid_step_ticks=40,
                    pyramid_direction="against", pyramid_stop_mode=stop_mode)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    t = ticks.get_day_ticks("NQZ5", DAY)
    w = vwapmod.vwap_bands(t)
    return cfg, trades, t, w


def test_avg_down_adds_fill_at_their_limit_levels():
    """An against-grid lot is a resting limit: it books at its own level, so the
    average entry is exactly the blend of the grid levels the tape reached — and
    only those. The tape must really have traded at (or through) every level
    that filled, and never at the first level that didn't."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, t, w = _avg_down_session("anchor")
    assert trades
    step = cfg.pyramid_step_ticks * TICK
    lot = cfg.contracts // cfg.pyramid_tranches
    seen_adds = False
    for tr in trades:
        first = w["upper1"].iloc[tr["entry_idx"]]
        k = tr["max_contracts"] // lot
        assert abs(tr["avg_entry"] - (first - step * (k - 1) / 2)) < 1e-9, tr
        # anchor: the stop (and the risk booked) stays the FIRST lot's
        assert abs((first - tr["stop_price"]) - cfg.stop_ticks * TICK) < 1e-9
        lo = t["price"].iloc[tr["entry_idx"] + 1: tr["exit_idx"] + 1].min()
        assert lo <= first - (k - 1) * step + 1e-9, tr
        if k < cfg.pyramid_tranches:
            assert lo > first - k * step - 1e-9, tr
        seen_adds = seen_adds or k > 1
    assert seen_adds, "the session never exercised the grid — assertions were vacuous"


def test_avg_down_blend_restrikes_off_the_average():
    """Blend keeps the stop stop_ticks behind the running average, so every add
    into the dip WIDENS the stop — the martingale, priced as configured."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, _ = _avg_down_session("blend")
    assert trades
    for tr in trades:
        assert abs((tr["avg_entry"] - tr["stop_price"]) - cfg.stop_ticks * TICK) < 1e-9


def test_avg_down_grid_may_not_reach_the_stop():
    """The deepest add resting at or past the stop could only ever fill on the
    print that kills the trade; the schema refuses the config outright."""
    from journal.sim import schema
    ok = dict(contracts=3, stop_ticks=150, pyramid_tranches=3,
              pyramid_direction="against", pyramid_step_ticks=74)
    schema.parse(dict(ok))
    try:
        schema.parse(dict(ok, pyramid_step_ticks=75))
        raise AssertionError("a grid ending on the stop must be refused")
    except ValueError:
        pass


# --- developing volume profile ---------------------------------------------

def _profile(prices, sizes, n):
    t = _synth(prices, sizes)
    b = barmod.tick_bars(t, n=n)
    return t, b, profmod.developing_profile(t, b, tick_size=1.0)


def test_profile_bins_volume_at_the_traded_price():
    # levels 100:2, 101:5, 102:1 -> POC is 101, the level that actually traded most.
    _, _, p = _profile([100, 101, 100, 102], [1, 5, 1, 1], n=4)
    assert p.poc[-1] == 101
    # Value area (70% of 8 = 5.6): start at 101 (5), the pair below carries 2 and
    # the pair above carries 1, so it annexes downward to 100 and stops at 7.
    assert p.val[-1] == 100 and p.vah[-1] == 101


def test_profile_is_developing_not_per_bar():
    # Bar 0 trades 12 lots at 100. Bar 1 trades 4 lots and every one of them at
    # 200 — so a *per-bar* profile would put bar 1's POC at 200. The developing
    # one keeps it at 100: bar 0's volume is still in the book.
    _, _, p = _profile([100] * 4 + [200] * 4, [3] * 4 + [1] * 4, n=4)
    assert p.poc[0] == 100
    assert p.poc[1] == 100, "the profile reset instead of accumulating"


def test_value_area_encloses_at_least_seventy_percent():
    rng = np.random.default_rng(7)
    prices = 100 + rng.integers(0, 40, size=400)
    sizes = rng.integers(1, 9, size=400)
    t, b, p = _profile(list(prices), list(sizes), n=100)
    for k in range(len(b)):
        upto = int(b["end_idx"].iloc[k]) + 1
        px, sz = prices[:upto], sizes[:upto]
        inside = sz[(px >= p.val[k]) & (px <= p.vah[k])].sum()
        assert inside >= 0.70 * sz.sum(), f"bar {k} value area holds only {inside}/{sz.sum()}"
        assert p.val[k] <= p.poc[k] <= p.vah[k]


def test_levels_in_force_lag_by_a_bar():
    """The engine settles fills before it processes the bar closing on that tick,
    so the VAH in force at tick i must come from a bar that closed BEFORE i —
    never from the bar ending on i, whose ticks a fill at i hadn't all seen."""
    t, b, p = _profile([100] * 4 + [200] * 4, [1] * 4 + [3] * 4, n=4)
    vah = profmod.levels_in_force(p, b, len(t))
    assert np.isnan(vah[:4]).all(), "no bar has closed yet — there is no profile"
    # Bar 0 closes on tick 3, so its VAH governs ticks 4..7 — including tick 7,
    # where bar 1 closes. Bar 1's own VAH never applies to any tick in this frame.
    assert (vah[4:8] == p.vah[0]).all()
    assert p.vah[1] != p.vah[0], "the test is vacuous if the two bars agree"


# --- volume-profile confluence + VAH exit, against the real session ----------

def _vah_view(cfg):
    trades, vetoed, b, _ = engine.run_session(cfg, DAY)
    t = ticks.get_day_ticks("NQZ5", DAY)
    p = profmod.developing_profile(t, b, TICK)
    return trades, vetoed, t, b, p, profmod.levels_in_force(p, b, len(t))


def test_gate_takes_only_fills_strictly_above_vah():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(confluences={"volume_profile": {"enabled": True}})
    trades, vetoed, _, _, _, vah = _vah_view(cfg)
    assert trades and vetoed, "the gate must have both passed and blocked entries"
    for tr in trades:
        v = vah[tr["entry_idx"]]
        assert not np.isnan(v), "an entry was taken before any profile existed"
        assert tr["avg_entry"] > v, f"took a fill at {tr['avg_entry']} inside value (VAH {v})"
    for tr in vetoed:
        if tr["gate"] == "in_trade":
            continue  # blocked by an open position, not by the gate
        v = vah[tr["entry_idx"]]
        assert tr["gate"] == "volume_profile"
        assert np.isnan(v) or tr["avg_entry"] <= v, "vetoed an entry that was above VAH"


def test_gate_margin_only_tightens():
    """min_ticks_above_vah is a demand for separation from value: raising it can
    only remove trades, never introduce one the strict gate refused."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    strict, _, _, _ = engine.run_session(
        SimConfig(confluences={"volume_profile": {"enabled": True}}), DAY)
    wide, _, _, _ = engine.run_session(
        SimConfig(confluences={"volume_profile": {"enabled": True,
                                                  "min_ticks_above_vah": 40}}), DAY)
    assert len(wide) < len(strict), "a 40-tick margin should bite on this session"
    kept = {t["entry_idx"] for t in strict}
    assert {t["entry_idx"] for t in wide} <= kept, "margin invented an entry"


def test_vah_exit_fills_on_the_tick_after_a_sub_vah_close():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(exit_below_vah_bars=1)
    trades, _, t, b, p, _ = _vah_view(cfg)
    px = t["price"].to_numpy()
    ends = b["end_idx"].to_numpy()
    outs = [tr for tr in trades if tr["exit_reason"] == "vah"]
    assert outs, "the rule never fired — this test proves nothing"
    for tr in outs:
        x = tr["exit_idx"]
        # A market order sent on a close fills at the next print, at its price.
        assert abs(tr["avg_exit"] - px[x]) < 1e-9
        k = np.flatnonzero(ends == x - 1)
        assert len(k) == 1, "a vah exit landed somewhere other than a post-close tick"
        assert b["close"].iloc[k[0]] < p.vah[k[0]], "fired on a close that was not below VAH"


def test_profile_is_not_built_when_nothing_reads_it():
    """The default config must not pay for a value-area scan per bar."""
    assert not confmod.needs_profile(SimConfig())
    assert not confmod.needs_profile(
        SimConfig(confluences={"volume_profile": {"enabled": False}}))
    assert confmod.needs_profile(SimConfig(exit_below_vah_bars=1))
    assert confmod.needs_profile(SimConfig(confluences={"volume_profile": {"enabled": True}}))


# --- the Globex-anchored variant -------------------------------------------
#
# Synthetic, not the real session: the overnight segment isn't in the cache, and
# these assertions need a stream whose VWAP is known by construction. The
# overnight ticks are a ±30 square wave at size 100, which pins the anchor at
# mid=20000, sigma=30 (so dev1=20030, dev2=20060) and makes the RTH ticks —
# size 1, a few hundred of them — too light to move it more than a hair.

RTH_OPEN_UTC = pd.Timestamp("2025-10-13 13:30", tz="UTC")  # 09:30 ET
GLOBEX_OPEN_UTC = pd.Timestamp("2025-10-12 22:00", tz="UTC")  # 18:00 ET, prev day
ON_SQUARE = [20030.0 if k % 2 == 0 else 19970.0 for k in range(4000)]


def _grid(*legs) -> list[float]:
    """Concatenated linspace legs, snapped to NQ's 0.25 grid."""
    out: list[float] = []
    for lo, hi, n in legs:
        out.extend(np.round(np.linspace(lo, hi, n) * 4) / 4)
    return out


class _globex_cache:
    """Serve one synthetic session's two segments through the real tick cache, so
    the engine reaches them the way it will in production. Both segments are
    always written — an ``on=[]`` writes an *empty* overnight, which is how a
    session with no overnight data actually presents itself (and never a fetch:
    a cached file, even an empty one, is served without touching Databento)."""

    def __init__(self, on, on_size, rth, rth_size):
        self.on, self.on_size, self.rth, self.rth_size = on, on_size, rth, rth_size

    def _frame(self, start, prices, size):
        return pd.DataFrame({
            "ts_utc": pd.date_range(start, periods=len(prices), freq="s", tz="UTC"),
            "price": [float(p) for p in prices],
            "size": [size] * len(prices),
            "side": ["A"] * len(prices),
        })

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._old = ticks.TICK_CACHE_DIR
        ticks.TICK_CACHE_DIR = Path(self._tmp.name)
        ticks._read_day_parquet.cache_clear()
        ticks.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._frame(GLOBEX_OPEN_UTC, self.on, self.on_size).to_parquet(
            ticks._cache_path("TEST", DAY, "on"), index=False)
        self._frame(RTH_OPEN_UTC, self.rth, self.rth_size).to_parquet(
            ticks._cache_path("TEST", DAY, "rth"), index=False)
        # The Globex strategy's config carries `side`; run_session_globex reads it.
        # A GlobexBounceConfig is a SimConfig, so the run_session(side=…) calls that
        # exercise the mirror through this same fixture keep working unchanged.
        return GlobexBounceConfig(contract="TEST", ticks_per_bar=50)

    def __exit__(self, *exc):
        ticks.TICK_CACHE_DIR = self._old
        ticks._read_day_parquet.cache_clear()
        self._tmp.cleanup()


def test_globex_vwap_is_anchored_at_the_overnight_open():
    """The whole point of the variant: at the bell the bands already carry the
    night. An RTH-anchored VWAP would open flat on the session's first print."""
    # Rally off 20000 through acceptance (>dev1+7.5), pull back to dev1, run to dev2.
    rth = _grid((20000, 20045, 50), (20045, 20025, 100), (20025, 20070, 300), (20070, 20070, 150))
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        trades, _, b, bands = engine.run_session_globex(cfg, DAY)

        # Bars are built over the combined stream, so the chart's session starts
        # at 18:00 the previous evening.
        assert (b["ts_utc"] < RTH_OPEN_UTC).any(), "no overnight bars in the frame"

        first_rth = int(b["ts_utc"].searchsorted(RTH_OPEN_UTC, side="left"))
        # Anchored at Globex: mid is the night's 20000, not seg1's ~20022 mean.
        assert abs(bands.loc[first_rth, "mid"] - 20000) < 0.5
        assert abs(bands.loc[first_rth, "upper1"] - 20030) < 0.5
        assert abs(bands.loc[first_rth, "upper2"] - 20060) < 0.5

        # The fixture is only meaningful if it actually trades the setup.
        assert len(trades) == 1, trades
        tr = trades[0]
        assert tr["exit_reason"] == "target"
        assert abs(tr["avg_entry"] - 20030) < 0.5   # filled the dev1 limit
        assert abs(tr["avg_exit"] - 20060) < 0.5    # took dev2


def test_globex_entries_and_acceptance_stay_inside_rth():
    rth = _grid((20000, 20045, 50), (20045, 20025, 100), (20025, 20070, 300), (20070, 20070, 150))
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        trades, _, _, _ = engine.run_session_globex(cfg, DAY)

    assert trades
    entry_open = pd.Timestamp("2025-10-13 13:31", tz="UTC")  # cfg.entry_open, 09:31 ET
    for tr in trades:
        assert tr["entry_ts_utc"] >= entry_open
        assert tr["acceptance_ts"] >= RTH_OPEN_UTC


def test_globex_overnight_bars_feed_the_bands_but_never_arm_the_setup():
    """The overnight is indicator input, not signal. Here the ONLY acceptance
    candle in the stream is an overnight one, and RTH then pulls back through
    dev1 — a state machine that read overnight closes would arm and fill; this
    one must not."""
    on = ON_SQUARE + _grid((20000, 20120, 200))  # a rally that closes well above dev1
    rth = _grid((20080, 20020, 600))             # red bars only: no RTH acceptance
    with _globex_cache(on, 100, rth, 1) as cfg:
        trades, _, b, bands = engine.run_session_globex(cfg, DAY)

        # Guard against a vacuous pass: the fixture must really contain an
        # overnight candle that satisfies the acceptance rule, and RTH must
        # really trade down through dev1 (i.e. a full-session machine WOULD
        # have armed and filled here).
        acc_min = cfg.acceptance_min_ticks * 0.25
        pre = b["ts_utc"] < RTH_OPEN_UTC
        armable = ((b["close"] - bands["upper1"]) > acc_min) & (b["close"] > b["open"])
        assert (armable & pre).any(), "fixture has no overnight acceptance candle"
        assert (b["low"][~pre] <= bands["upper1"][~pre]).any(), "RTH never reached dev1"

        assert trades == [], trades


def test_globex_refuses_a_session_with_no_overnight_ticks():
    """get_day_ticks falls back to RTH-only when the overnight comes back empty.
    Silently anchoring at 09:30 would be a different strategy wearing this one's
    name, so the run must fail loudly instead."""
    with _globex_cache([], 100, _grid((20000, 20050, 600)), 1) as cfg:
        try:
            engine.run_session_globex(cfg, DAY)
        except RuntimeError as exc:
            assert "overnight" in str(exc)
        else:
            raise AssertionError("expected a RuntimeError")

        # The RTH strategy is untouched by any of this — same data, still runs.
        engine.run_session(cfg, DAY)


# --- the inverted band read --------------------------------------------------
#
# invert decouples the band from the trade: a long reads the LOWER band (buy the
# pullback into support), a short the UPPER (sell the rally into resistance). The
# entry is still a resting limit at dev1; only the direction — and, since dev2 is
# now behind the trade, the R target — differ. As with the mirror, one reflection
# test pins the whole signed loop: get any comparison's sign wrong and it breaks.

# ON_SQUARE anchors the VWAP at 20000, so lower1 ~ 19970. A green candle in the
# channel arms; a pull DOWN through the band fills the long; then it rallies to
# the R target (19970 + 1.0 * 75 ticks = 19988.75).
_INVERT_RTH = _grid((19985, 19998, 80), (19998, 19960, 150),
                    (19960, 19992, 300), (19992, 19992, 100))


def test_invert_long_buys_the_pullback_into_the_lower_band():
    with _globex_cache(ON_SQUARE, 100, _INVERT_RTH, 1) as cfg:
        cfg = replace(cfg, side="long", invert=True, target="rr", target_rr=1.0)
        trades, _, _, _ = engine.run_session_globex(cfg, DAY)

    assert len(trades) == 1, trades
    tr = trades[0]
    assert tr["direction"] == "Long"
    assert abs(tr["avg_entry"] - 19970) < 1.0        # filled the lower dev1
    assert tr["stop_price"] < tr["avg_entry"]        # a long's stop sits below it
    assert tr["avg_exit"] > tr["avg_entry"]          # and it profits UP, toward the mid
    assert tr["band_width_ticks"] > 0                # width is band-signed, never negative
    assert tr["exit_reason"] == "target"


def test_invert_short_is_the_invert_long_reflected():
    """The load-bearing test: reflect the tape about a constant and the inverted
    short (upper band) must reproduce the inverted long (lower band) trade for
    trade, reflected. A sign error in any of the band-vs-trade reads breaks it."""
    C = 40000.0
    with _globex_cache(ON_SQUARE, 100, _INVERT_RTH, 1) as cfg:
        longs, _, _, _ = engine.run_session_globex(
            replace(cfg, side="long", invert=True, target="rr", target_rr=1.0), DAY)
    with _globex_cache([C - p for p in ON_SQUARE], 100,
                       [C - p for p in _INVERT_RTH], 1) as cfg:
        shorts, _, _, _ = engine.run_session_globex(
            replace(cfg, side="short", invert=True, target="rr", target_rr=1.0), DAY)

    assert longs, "the fixture must trade for the mirror to mean anything"
    assert len(longs) == len(shorts), (len(longs), len(shorts))
    for lo, sh in zip(longs, shorts):
        assert lo["direction"] == "Long" and sh["direction"] == "Short"
        assert (lo["entry_idx"], lo["exit_idx"]) == (sh["entry_idx"], sh["exit_idx"])
        assert lo["exit_reason"] == sh["exit_reason"]
        assert abs(lo["avg_entry"] + sh["avg_entry"] - C) < 1e-6   # reflected prices
        assert abs(lo["r_multiple"] - sh["r_multiple"]) < 1e-9


def test_invert_off_is_byte_identical_to_the_bounce():
    """The knob must not touch the default path: side=long/invert=False is the
    upper-band bounce, unchanged."""
    rth = _grid((20000, 20045, 50), (20045, 20025, 100),
                (20025, 20070, 300), (20070, 20070, 150))
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        plain, _, _, _ = engine.run_session_globex(cfg, DAY)          # invert defaults off
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        explicit, _, _, _ = engine.run_session_globex(
            replace(cfg, side="long", invert=False), DAY)
    assert _same_trades(plain, explicit)
    assert plain and plain[0]["direction"] == "Long"


# --- the lower-band mirror ---------------------------------------------------
#
# The short is the same idea read upside-down, so these are the long's own
# invariants with every inequality flipped — plus one test that pins the mirror
# itself: reflect the tape about a constant and the short must reproduce the
# long's trades exactly, reflected. That one is what makes the other flips safe
# to trust, since a sign error anywhere in the loop breaks it.

def _short_session(variant: str):
    cfg = SimConfig(entry_variant=variant)
    trades, _, _, _ = engine.run_session(cfg, DAY, side="short")
    t = ticks.get_day_ticks("NQZ5", DAY)
    return cfg, trades, t, vwapmod.vwap_bands(t)


def test_short_stop_sits_above_entry_at_the_config_distance():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, _ = _short_session("A")
    assert trades
    for tr in trades:
        assert tr["direction"] == "Short"
        assert abs((tr["stop_price"] - tr["avg_entry"]) - cfg.stop_ticks * TICK) < 1e-9


def test_short_variant_a_fills_on_the_lower_dev1_line():
    """A resting sell limit at the lower dev1 gets dev1 — not the (worse) traded
    price. Price must have traded UP to it."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, t, w = _short_session("A")
    assert trades
    for tr in trades:
        i = tr["entry_idx"]
        assert abs(tr["avg_entry"] - w["lower1"].iloc[i]) < 1e-9
        assert t["price"].iloc[i] >= w["lower1"].iloc[i] - 1e-9


def test_short_exits_respect_their_reason():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, _, w = _short_session("A")
    seen = set()
    for tr in trades:
        seen.add(tr["exit_reason"])
        if tr["exit_reason"] == "stop":
            # Bought back at or through the stop, which sits above the entry.
            assert tr["avg_exit"] >= tr["stop_price"] - 1e-9
        elif tr["exit_reason"] == "target":
            i = tr["exit_idx"]
            assert abs(tr["avg_exit"] - w["lower2"].iloc[i]) < 1e-9
            assert tr["avg_exit"] < tr["avg_entry"], "a short's target is BELOW its entry"
    assert {"stop", "target"} <= seen, seen


def test_short_pnl_is_signed_the_other_way():
    """The one thing a flipped rule set cannot get away with fudging: selling
    high and buying back low has to *make* money."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, _ = _short_session("A")
    assert trades
    for tr in trades:
        assert abs(tr["points"] - (tr["avg_entry"] - tr["avg_exit"])) < 1e-9
        assert tr["gross_pnl"] > 0 if tr["avg_exit"] < tr["avg_entry"] else True
    winners = [tr for tr in trades if tr["exit_reason"] == "target"]
    assert winners, "no target hits — this test proves nothing"
    for tr in winners:
        assert tr["points"] > 0 and tr["net_pnl"] > 0
        assert tr["r_multiple"] > 0


def test_short_acceptance_was_a_red_close_below_dev1():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, t, w = _short_session("A")
    b = barmod.tick_bars(t, cfg.ticks_per_bar)
    for tr in trades:
        acc = tr["acceptance_ts"]
        assert acc is not None and acc <= tr["entry_ts_utc"]
        bar = b[b["ts_utc"] == acc]
        assert len(bar) == 1, f"acceptance {acc} is not a bar close"
        row = bar.iloc[0]
        l1 = w["lower1"].iloc[int(row["end_idx"])]
        assert row["close"] < row["open"], "acceptance candle was not red"
        assert (l1 - row["close"]) > cfg.acceptance_min_ticks * TICK


def test_short_variant_b_enters_below_dev1():
    """B is a rejection: the sell stop sits below dev1, so fills are never above it."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _, w = _short_session("B")
    assert trades
    for tr in trades:
        level = w["lower1"].iloc[tr["entry_idx"]] - cfg.entry_stop_offset_ticks * TICK
        assert tr["avg_entry"] <= level + 1e-9


def test_short_gate_takes_only_fills_strictly_below_val():
    """The mirrored confluence: a short from below value may only fill below the
    developing VAL. The gate must read VAL, not the VAH it reads for a long."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(confluences={"volume_profile": {"enabled": True}})
    trades, vetoed, b, _ = engine.run_session(cfg, DAY, side="short")
    t = ticks.get_day_ticks("NQZ5", DAY)
    p = profmod.developing_profile(t, b, TICK)
    val = profmod.levels_in_force(p, b, len(t), edge="val")
    assert trades and vetoed, "the gate must have both passed and blocked entries"
    for tr in trades:
        v = val[tr["entry_idx"]]
        assert not np.isnan(v), "an entry was taken before any profile existed"
        assert tr["avg_entry"] < v, f"took a fill at {tr['avg_entry']} inside value (VAL {v})"
    for tr in vetoed:
        if tr["gate"] == "in_trade":
            continue  # blocked by an open position, not by the gate
        v = val[tr["entry_idx"]]
        assert tr["gate"] == "volume_profile"
        assert np.isnan(v) or tr["avg_entry"] >= v, "vetoed an entry that was below VAL"


def test_short_value_exit_fires_on_closes_back_ABOVE_val():
    """exit_below_vah_bars keeps its long-flavoured name on a short and means the
    mirror: price was re-accepted back UP inside value."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(exit_below_vah_bars=1)
    trades, _, b, _ = engine.run_session(cfg, DAY, side="short")
    t = ticks.get_day_ticks("NQZ5", DAY)
    p = profmod.developing_profile(t, b, TICK)
    px = t["price"].to_numpy()
    ends = b["end_idx"].to_numpy()
    outs = [tr for tr in trades if tr["exit_reason"] == "vah"]
    assert outs, "the rule never fired — this test proves nothing"
    for tr in outs:
        x = tr["exit_idx"]
        assert abs(tr["avg_exit"] - px[x]) < 1e-9  # market order, next print
        k = np.flatnonzero(ends == x - 1)
        assert len(k) == 1, "a value exit landed somewhere other than a post-close tick"
        assert b["close"].iloc[k[0]] > p.val[k[0]], "fired on a close that was not above VAL"


def test_short_is_the_long_reflected():
    """Reflect every price about a constant and the short must produce exactly the
    long's trades, reflected: same ticks entered and exited, same reasons, same R.

    This is the mirror's load-bearing test. The band bounce is one signed loop, so
    a single flipped comparison — a stop that checks the wrong direction, a target
    that fills on the wrong side of dev2 — shows up here as a diverged trade,
    which no amount of eyeballing the short's own inequalities would catch.
    """
    C = 40000.0
    rth = _grid((20000, 20045, 50), (20045, 20025, 100), (20025, 20070, 300), (20070, 20070, 150))
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        longs, _, _, _ = engine.run_session(cfg, DAY, overnight=True)
    with _globex_cache([C - p for p in ON_SQUARE], 100, [C - p for p in rth], 1) as cfg:
        shorts, _, _, _ = engine.run_session(cfg, DAY, overnight=True, side="short")

    assert longs, "the fixture must actually trade for the mirror to mean anything"
    assert len(longs) == len(shorts), (len(longs), len(shorts))
    for lo, sh in zip(longs, shorts):
        assert sh["direction"] == "Short" and lo["direction"] == "Long"
        assert (lo["entry_idx"], lo["exit_idx"]) == (sh["entry_idx"], sh["exit_idx"])
        assert lo["exit_reason"] == sh["exit_reason"]
        assert lo["acceptance_ts"] == sh["acceptance_ts"]
        for col in ("avg_entry", "avg_exit", "stop_price", "target_price"):
            assert abs((C - lo[col]) - sh[col]) < 1e-6, col
        for col in ("points", "r_multiple", "net_pnl", "band_width_ticks"):
            assert abs(lo[col] - sh[col]) < 1e-6, col


def test_side_must_be_long_or_short():
    try:
        engine.run_session(SimConfig(), DAY, side="up")
    except ValueError as exc:
        assert "side" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_gate_rejects_typo_knobs():
    """A misspelled knob silently doing nothing would masquerade as an experiment."""
    for bad in ({"enabled": True, "min_ticks_above_vah_": 5},
                {"enabled": True, "min_ticks_above_vah": -1},
                {"enabled": True, "min_ticks_above_vah": 1.5}):
        try:
            confmod.validate(SimConfig(confluences={"volume_profile": bad}),
                             ("volume_profile",))
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass


# --- the step trail ---------------------------------------------------------
#
# The ratchet is a pure function of (entry, current print, step), so these read
# it straight off the real session's trades rather than trying to hand-build a
# tick stream that walks a VWAP band into a trail.


def test_trail_off_changes_nothing():
    """The regression guard the version bump is really about: a config that
    leaves the trail off must simulate exactly as it did before it existed."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    base, _, _, _ = engine.run_session(SimConfig(), DAY)
    off, _, _, _ = engine.run_session(SimConfig(trail_stop_ticks=0), DAY)
    assert len(base) == len(off)
    for a, b in zip(base, off):
        for col in ("avg_entry", "avg_exit", "stop_price", "exit_reason", "points"):
            assert a[col] == b[col], col
    # ...and with it off, the stop never moved.
    for tr in off:
        assert tr["final_stop_price"] == tr["stop_price"]
        assert tr["exit_reason"] != "trail"


def test_trail_stop_never_loosens_and_lands_on_a_step():
    """Every trailed stop sits exactly at entry + (N-1) steps for some N >= 1,
    and never further from entry than the stop actually risked."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(trail_stop_ticks=75)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    assert trades
    step = cfg.trail_stop_ticks * TICK
    moved = 0
    for tr in trades:
        fs, e = tr["final_stop_price"], tr["avg_entry"]
        # Never loosened: the trailed stop is never below the one we entered on.
        assert fs >= tr["stop_price"] - 1e-9
        if abs(fs - tr["stop_price"]) < 1e-9:
            continue
        moved += 1
        # Sits on a step boundary: (fs - entry) is a whole number of steps, and
        # the first one is breakeven (N=1 -> fs == entry).
        n = (fs - e) / step
        assert abs(n - round(n)) < 1e-9, (fs, e, n)
        assert round(n) >= 0
    assert moved, "no trade trailed — the test proves nothing"


def test_trail_breakeven_exit_is_its_own_reason():
    """A trade stopped on a ratcheted stop is reported as 'trail', not 'stop' —
    and it exits at or beyond the stop that was actually in force."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    trades, _, _, _ = engine.run_session(SimConfig(trail_stop_ticks=75), DAY)
    trailed = [tr for tr in trades if tr["exit_reason"] == "trail"]
    assert trailed, "no trailed exits on this session — the test proves nothing"
    for tr in trailed:
        assert tr["final_stop_price"] != tr["stop_price"]
        # Filled at or through the stop in force, never better than reality.
        assert tr["avg_exit"] <= tr["final_stop_price"] + 1e-9
        # The stop in force was breakeven or better...
        assert tr["final_stop_price"] >= tr["avg_entry"] - 1e-9
        # ...so the trade is out for a scratch, not the full risk. Not asserted as
        # points >= 0: the entry is a limit resting at dev1, which is not on the
        # tick grid, so a breakeven stop fills a fraction of a tick below entry.
        # That fill-through is the engine's rule, not a rounding slip.
        assert tr["r_multiple"] > -1.0
    # A 'stop' still means the ORIGINAL stop was hit.
    for tr in trades:
        if tr["exit_reason"] == "stop":
            assert tr["final_stop_price"] == tr["stop_price"]


def test_trail_r_multiple_still_measures_entry_risk():
    """R is what you risked, not where the stop ended up: a breakeven scratch is
    ~0R, not an infinite one."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(trail_stop_ticks=75)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    risk = cfg.stop_ticks * TICK
    for tr in trades:
        assert abs(tr["r_multiple"] - tr["points"] / risk) < 1e-9


def test_trail_mirrors_onto_a_short():
    """The ratchet is read in the trade's direction: a short's stop steps DOWN
    toward entry, and its first step is breakeven too."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(trail_stop_ticks=75)
    trades, _, _, _ = engine.run_session(cfg, DAY, side="short")
    if not trades:
        print("   (skipped: no short trades this session)")
        return
    step = cfg.trail_stop_ticks * TICK
    for tr in trades:
        fs, e = tr["final_stop_price"], tr["avg_entry"]
        # A short's stop sits ABOVE entry and can only come down.
        assert fs <= tr["stop_price"] + 1e-9
        if abs(fs - tr["stop_price"]) < 1e-9:
            continue
        n = (e - fs) / step
        assert abs(n - round(n)) < 1e-9 and round(n) >= 0, (fs, e, n)
        if tr["exit_reason"] == "trail":
            assert tr["avg_exit"] >= fs - 1e-9
            assert fs <= e + 1e-9              # breakeven or better
            assert tr["r_multiple"] > -1.0     # a scratch, not the full risk


def test_trail_applies_to_vetoed_ghosts():
    """A vetoed entry is only a counterfactual if it was exited by exactly the
    rules the real trades were — including the trail."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(
        trail_stop_ticks=75,
        confluences={"volume_profile": {"enabled": True, "min_ticks_above_vah": 40}},
    )
    _, vetoed, _, _ = engine.run_session(cfg, DAY)
    if not vetoed:
        print("   (skipped: the gate vetoed nothing this session)")
        return
    step = cfg.trail_stop_ticks * TICK
    for tr in vetoed:
        assert tr["final_stop_price"] >= tr["stop_price"] - 1e-9
        n = (tr["final_stop_price"] - tr["avg_entry"]) / step
        if abs(tr["final_stop_price"] - tr["stop_price"]) > 1e-9:
            assert abs(n - round(n)) < 1e-9


def test_a_zero_step_is_one_click_per_trail_distance():
    """The step's 0 sentinel: with no step of its own the trail moves in single
    clicks of its own distance, which is the one-knob trail this grew out of. So
    the two configs are the same run, and that is what lets every artifact written
    before the split still replay to the trades it reported."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    implied, _, _, _ = engine.run_session(SimConfig(trail_stop_ticks=75), DAY)
    spelled, _, _, _ = engine.run_session(
        SimConfig(trail_stop_ticks=75, trail_step_ticks=75), DAY)
    assert len(implied) == len(spelled) and implied
    for a, b in zip(implied, spelled):
        for col in ("avg_entry", "avg_exit", "final_stop_price", "exit_reason"):
            assert a[col] == b[col], col


def test_the_scratch_level_lifts_the_trail_off_the_entry():
    """A stop on the entry is breakeven *gross*, so the round trip books its
    commission as a loss. The offset moves the trail's whole grid up by that much
    — the first click lands there, and every step is measured from it."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    be = 4
    cfg = SimConfig(trail_stop_ticks=75, trail_step_ticks=25, trail_breakeven_ticks=be)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    moved = [tr for tr in trades
             if abs(tr["final_stop_price"] - tr["stop_price"]) > 1e-9]
    assert moved, "no trade trailed — the test proves nothing"
    for tr in moved:
        # Never below the scratch level, and on the step grid measured FROM it.
        off = (tr["final_stop_price"] - tr["avg_entry"]) / TICK
        assert off >= be - 1e-6, (off, be)
        n = (off - be) / cfg.trail_step_ticks
        assert abs(n - round(n)) < 1e-6, (off, n)

    # And the point of the whole knob: a trade stopped on the lifted trail is out
    # for a real scratch — commission paid — not for the commission itself.
    comm = 2 * cfg.commission_per_side * cfg.contracts
    scratched = [tr for tr in trades
                 if tr["exit_reason"] == "trail"
                 and abs(tr["final_stop_price"] - tr["avg_entry"] - be * TICK) < 1e-6]
    assert scratched, "no trade stopped on the scratch level — the test proves nothing"
    for tr in scratched:
        # Gross covers the round trip. Not asserted on net: the stop fills at the
        # print that traded THROUGH it, which can be a tick or more beyond, and
        # that fill-through is the engine's rule, not a rounding slip.
        assert tr["gross_pnl"] > 0
        assert tr["net_pnl"] > -comm, (tr["net_pnl"], comm)


def test_a_breakeven_stop_takes_the_first_click_and_no_other():
    """The stop moves to the scratch level once and stays there — it is a breakeven
    stop, not a trail. So every trailed stop in the run sits on exactly that one
    level, whatever the step says and however far the trade ran."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    be = 4
    cfg = SimConfig(trail_stop_ticks=75, trail_step_ticks=25,
                    trail_breakeven_ticks=be, trail_breakeven_only=True)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    moved = [tr for tr in trades
             if abs(tr["final_stop_price"] - tr["stop_price"]) > 1e-9]
    assert moved, "no trade trailed — the test proves nothing"
    for tr in moved:
        off = (tr["final_stop_price"] - tr["avg_entry"]) / TICK
        assert abs(off - be) < 1e-6, (off, be)   # the one level, never a step above


def test_a_breakeven_stop_owes_nothing_to_the_step():
    """Without the flag, 'breakeven only' can only be *simulated* — by a step so
    wide the second click can never come, which is a claim about the target, not
    about the stop, and quietly fails the moment the target moves further out.
    The flag makes the step irrelevant instead: same stops at any step, while the
    trail proper ratchets straight past the scratch level."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    base = SimConfig(trail_stop_ticks=75, trail_breakeven_ticks=4)
    fine, _, _, _ = engine.run_session(
        replace(base, trail_breakeven_only=True, trail_step_ticks=25), DAY)
    coarse, _, _, _ = engine.run_session(
        replace(base, trail_breakeven_only=True, trail_step_ticks=250), DAY)
    assert fine and len(fine) == len(coarse)
    for a, b in zip(fine, coarse):
        assert a["final_stop_price"] == b["final_stop_price"]
        assert a["exit_reason"] == b["exit_reason"]

    # ...and the flag is doing real work: the same trail without it climbs.
    trailed, _, _, _ = engine.run_session(replace(base, trail_step_ticks=25), DAY)
    climbed = [tr for tr in trailed
               if (tr["final_stop_price"] - tr["avg_entry"]) / TICK > 4 + 1e-6]
    assert climbed, "nothing ratcheted past the scratch level — the test proves nothing"


def test_a_zero_scratch_level_still_trails_off_the_entry():
    """The offset's 0 sentinel is the trail this grew out of: first click on the
    entry. Every artifact written before the knob existed lacks the key, and must
    replay to the trades it reported."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    a, _, _, _ = engine.run_session(SimConfig(trail_stop_ticks=75, trail_step_ticks=25), DAY)
    b, _, _, _ = engine.run_session(
        SimConfig(trail_stop_ticks=75, trail_step_ticks=25, trail_breakeven_ticks=0), DAY)
    assert a and len(a) == len(b)
    for x, y in zip(a, b):
        assert x["final_stop_price"] == y["final_stop_price"]
        assert x["exit_reason"] == y["exit_reason"]


def test_the_step_is_the_grid_and_the_trail_is_the_distance():
    """The two knobs do different jobs: the trail distance says when the stop
    starts moving (and how far behind it stays), the step says what levels it may
    rest on. A 50/25 trail therefore sits on 25-tick multiples of the entry —
    levels a 50-tick step could never land on."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = SimConfig(trail_stop_ticks=50, trail_step_ticks=25)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    assert trades
    step = cfg.trail_step_ticks * TICK
    moved = [tr for tr in trades
             if abs(tr["final_stop_price"] - tr["stop_price"]) > 1e-9]
    assert moved, "no trade trailed — the test proves nothing"
    for tr in moved:
        # Breakeven or better, on the step grid measured from the entry: the trail
        # never installs a stop that tightens the loss it was entered with.
        n = (tr["final_stop_price"] - tr["avg_entry"]) / step
        assert abs(n - round(n)) < 1e-9, (tr["final_stop_price"], tr["avg_entry"])
        assert round(n) >= 0


# --- the daily loss stop ------------------------------------------------------


def test_daily_loss_stop_off_changes_nothing():
    """The regression guard the version bump is really about: a config that
    leaves the governor off must simulate exactly as it did before it existed."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    base, _, _, _ = engine.run_session(SimConfig(), DAY)
    off, _, _, _ = engine.run_session(SimConfig(daily_loss_stop=0.0), DAY)
    huge, _, _, _ = engine.run_session(SimConfig(daily_loss_stop=1e9), DAY)
    assert _same_trades(base, off) and _same_trades(off, huge)


def test_daily_loss_stop_halts_the_rest_of_the_session():
    """Once realized net P&L reaches the limit, the session takes no further
    entries — and the trades it did take are exactly the unhalted run's prefix
    through the trade that tripped it: the governor blocks new risk, it never
    rewrites a trade already on. The fixture recovers after the trip, so a halt
    that leaked even one more entry would show up as extra (winning) trades."""
    day = date(2025, 10, 14)
    if not ticks._cache_path("NQZ5", day).exists():
        print("   (skipped: tick cache cold)")
        return
    base, _, _, _ = engine.run_session(SimConfig(), day, side="short")
    cum = np.cumsum([tr["net_pnl"] for tr in base])
    limit = 500.0
    trip = int(np.flatnonzero(cum <= -limit)[0])
    assert trip < len(base) - 1, "the trip must not be the last trade — proves nothing"
    assert cum[-1] > -limit, "the base run must recover — proves nothing"
    halted, _, _, _ = engine.run_session(SimConfig(daily_loss_stop=limit), day, side="short")
    assert _same_trades(halted, base[: trip + 1])


def test_daily_loss_exit_open_off_or_unreachable_changes_nothing():
    """The regression guard the version bump is really about: the flatten arms
    nothing when it can't trip, so a run whose limit is never reached (or the knob
    off) must simulate exactly as it did before the exit existed."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    base, _, _, _ = engine.run_session(SimConfig(daily_loss_stop=1e9), DAY)
    on, _, _, _ = engine.run_session(
        SimConfig(daily_loss_stop=1e9, daily_loss_exit_open=True), DAY)
    assert _same_trades(base, on)


def test_daily_loss_exit_flattens_the_open_trade_before_its_stop():
    """The hole the knob closes: a single trade whose own stop sits wider than the
    whole daily limit. Fill the dev1 limit at 20030, then drift the long down
    against itself past the point where its open loss breaches a small daily stop
    but BEFORE it reaches the 75-tick stop (20011.25). Off, the trade rides to the
    time exit deep in the red; on, it leaves at market under 'daily_loss', a
    smaller loss, earlier, and above its own stop."""
    rth = _grid((20000, 20050, 100), (20050, 20030, 100),
                (20030, 20016, 300), (20016, 20016, 300))
    with _globex_cache(ON_SQUARE, 100, rth, 1) as cfg:
        base = replace(cfg, daily_loss_stop=250.0, daily_loss_exit_open=False)
        on = replace(cfg, daily_loss_stop=250.0, daily_loss_exit_open=True)
        b_tr, _, _, _ = engine.run_session_globex(base, DAY)
        e_tr, _, _, _ = engine.run_session_globex(on, DAY)
    assert len(b_tr) == 1 and len(e_tr) == 1, (b_tr, e_tr)
    b, e = b_tr[0], e_tr[0]
    # The fixture must exercise the hole: off, the trade never stops out — it rides
    # its whole giveback to the forced time exit.
    assert b["exit_reason"] == "time", b
    assert abs(b["avg_entry"] - 20030) < 0.5 and abs(e["avg_entry"] - 20030) < 0.5
    # On, the flatten fires: its own reason, a real loss, and a fill still above
    # the initial stop (so it left on the drawdown, not the stop).
    assert e["exit_reason"] == "daily_loss", e
    assert e["net_pnl"] < 0
    assert e["avg_exit"] > e["stop_price"], "must exit before the hard stop"
    # It caps the loss: less red, and earlier, than riding it out.
    assert e["net_pnl"] > b["net_pnl"]
    assert e["exit_idx"] < b["exit_idx"]
    # And it bites near the limit, not miles past it (one market order, next-print
    # fill, so a little overshoot is expected — but not a whole stop's worth).
    assert -400 < e["net_pnl"] <= -250


# --- the dev1 fade -------------------------------------------------------------
#
# Synthetic, by the same square-wave trick as the Globex tests, but the wave sits
# *inside* RTH because the fade's VWAP is session-anchored: 4000 heavy prints
# alternating 20030/19970 pin mid=20000 and sigma=30 (upper1=20030, upper2=20060),
# and the light size-1 scenario ticks that follow can't move the bands more than
# a few hundredths. The wave also outlasts 09:31, so the scenario plays entirely
# inside the entry window. Default FadeConfig: 50-tick arming stretch (12.5 pts,
# armed above ~20042.5), 50-tick stop, target mid.

SQ_RTH = [20030.0 if k % 2 == 0 else 19970.0 for k in range(4000)]


class _fade_cache:
    """One synthetic RTH session served through the real tick cache."""

    def __init__(self, scenario, base: float = 0.0):
        # ``base`` reflects the whole tape: every price p becomes base - p, for
        # the long/short mirror test.
        self.prices = [float(p) for p in SQ_RTH] + [float(p) for p in scenario]
        if base:
            self.prices = [base - p for p in self.prices]

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._old = ticks.TICK_CACHE_DIR
        ticks.TICK_CACHE_DIR = Path(self._tmp.name)
        ticks._read_day_parquet.cache_clear()
        ticks.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "ts_utc": pd.date_range(RTH_OPEN_UTC, periods=len(self.prices), freq="s", tz="UTC"),
            "price": self.prices,
            "size": [100] * len(SQ_RTH) + [1] * (len(self.prices) - len(SQ_RTH)),
            "side": ["A"] * len(self.prices),
        }).to_parquet(ticks._cache_path("TEST", DAY, "rth"), index=False)
        return FadeConfig(contract="TEST", ticks_per_bar=50)

    def __exit__(self, *exc):
        ticks.TICK_CACHE_DIR = self._old
        ticks._read_day_parquet.cache_clear()
        self._tmp.cleanup()


# Stretch to 20050 (well past the 20042.5 arming line), return through dev1
# (the variant-A fill), keep going through the mid (the target).
FADE_TAPE = _grid((20000, 20050, 100), (20050, 20025, 100), (20025, 19995, 100))


def test_fade_arms_on_the_stretch_and_fades_the_return():
    with _fade_cache(FADE_TAPE) as cfg:
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert len(trades) == 1, trades
    tr = trades[0]
    assert tr["direction"] == "Short"
    assert abs(tr["avg_entry"] - 20030) < 0.5, "the limit rests on dev1"
    assert tr["exit_reason"] == "target"
    assert abs(tr["avg_exit"] - 20000) < 0.5, "the mid target, tracked live"
    assert abs((tr["stop_price"] - tr["avg_entry"]) - cfg.stop_ticks * TICK) < 1e-9
    # The arming stamp is the stretch print — after the rally began, before the fill.
    assert tr["acceptance_ts"] > RTH_OPEN_UTC + pd.Timedelta(seconds=len(SQ_RTH))
    assert tr["acceptance_ts"] < tr["entry_ts_utc"]


def test_fade_long_is_the_short_reflected():
    """Reflect the tape about a constant and the lower-band fade must reproduce
    the upper-band fade's trades exactly, reflected — the one test a sign error
    anywhere in the u/s frame cannot survive."""
    with _fade_cache(FADE_TAPE) as cfg:
        short, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    with _fade_cache(FADE_TAPE, base=40000.0) as cfg:
        long_, _, _, _ = engine.run_session_fade(cfg, DAY, side="long")
    assert len(short) == len(long_) == 1
    for s_, l_ in zip(short, long_):
        assert l_["direction"] == "Long"
        assert (s_["entry_idx"], s_["exit_idx"]) == (l_["entry_idx"], l_["exit_idx"])
        assert s_["exit_reason"] == l_["exit_reason"]
        assert abs(l_["avg_entry"] - (40000 - s_["avg_entry"])) < 1e-6
        assert abs(l_["avg_exit"] - (40000 - s_["avg_exit"])) < 1e-6
        assert abs(l_["net_pnl"] - s_["net_pnl"]) < 1e-6


def test_fade_stretch_short_of_the_extension_never_arms():
    # To 20040 only: 10 points past dev1, under the 12.5 the arming needs. The
    # return then crosses dev1, so an armed machine WOULD have filled.
    tape = _grid((20000, 20040, 100), (20040, 19990, 200))
    with _fade_cache(tape) as cfg:
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert trades == [], trades


def test_fade_variant_b_stops_into_the_continuation():
    # The return stalls at 20028 — inside dev1 (a bar closes there, confirming
    # the rejection) but above the B stop at dev1 - 10 ticks = 20027.5. Only the
    # next leg's break through 20027.5 may fill, at the traded price.
    tape = _grid((20000, 20050, 100), (20050, 20028, 50), (20028, 19995, 100))
    with _fade_cache(tape) as cfg:
        a_trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
        b_trades, _, _, _ = engine.run_session_fade(
            replace(cfg, entry_variant="B"), DAY, side="short")
    assert len(a_trades) == len(b_trades) == 1
    assert abs(a_trades[0]["avg_entry"] - 20030) < 0.5
    assert b_trades[0]["avg_entry"] <= 20027.5 + 0.1, "B fills past the offset, at market"
    assert b_trades[0]["entry_idx"] > a_trades[0]["entry_idx"]
    assert b_trades[0]["exit_reason"] == "target"


def test_fade_mid_cross_requirement_blocks_the_second_setup():
    """Two stretch/return cycles with no mid touch between them: without the
    knob both fade; with it, only the first — its approach came off the square
    wave's mid prints, the second's never went back."""
    tape = _grid((20000, 20050, 100), (20050, 20015, 100),
                 (20015, 20055, 100), (20055, 20010, 100))
    # An R target keeps both exits well above the mid, so the second setup's
    # approach really never touches it.
    with _fade_cache(tape) as base:
        cfg = replace(base, target="rr", target_rr=1.0)
        both, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
        gated, _, _, _ = engine.run_session_fade(
            replace(cfg, arm_require_mid_cross=True), DAY, side="short")
    assert len(both) == 2, both
    assert all(abs(tr["avg_exit"] - (tr["avg_entry"] - 12.5)) < 1e-9 for tr in both)
    assert len(gated) == 1, gated
    assert _same_trades(gated, both[:1]), "the first setup is untouched by the gate"


def test_fade_dev2_cap_stands_the_setup_down():
    # The stretch runs on: a bar closes above dev2 (20060) while the setup is
    # armed and unfilled. With the cap that kills it — and the return through
    # dev1, which fades to the mid without the cap, must fill nothing.
    tape = _grid((20000, 20075, 150), (20075, 19990, 300))
    with _fade_cache(tape) as cfg:
        without, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
        capped, _, _, _ = engine.run_session_fade(
            replace(cfg, arm_cap_at_dev2=True), DAY, side="short")
    assert len(without) == 1 and without[0]["exit_reason"] == "target"
    assert capped == [], capped


def test_fade_invalidates_on_reacceptance_beyond_dev1():
    # Fill at dev1, then price re-accepts above the band: two consecutive bar
    # closes beyond dev1 (but under the stop) must exit at market with the
    # fade's own reason, not ride to the stop.
    tape = _grid((20000, 20050, 100), (20050, 20026, 50), (20035, 20035, 200))
    with _fade_cache(tape) as base:
        cfg = replace(base, invalidate_beyond_dev1_bars=2)
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert len(trades) == 1, trades
    tr = trades[0]
    assert tr["exit_reason"] == "dev1"
    assert abs(tr["avg_exit"] - 20035) < 0.5
    assert tr["net_pnl"] < 0, "the invalidation books its small loss honestly"
    assert tr["avg_exit"] < tr["stop_price"], "and it fired before the stop could"


def test_fade_rearm_needs_a_fresh_stretch():
    """After the first fade exits, the tape returns to dev1 again WITHOUT a new
    stretch — nothing may fill: the old overextension was consumed."""
    tape = _grid((20000, 20050, 100), (20050, 19995, 200),   # trade 1: fill, mid target
                 (19995, 20035, 100), (20035, 19995, 100))   # back past dev1, no stretch
    with _fade_cache(tape) as cfg:
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert len(trades) == 1, trades


# --- the fade armed from inside the band --------------------------------------
#
# arm_stretch_side="inside": the stretch runs DOWN through dev1 into the channel
# and the short sells the retest back UP to the band. The square wave that pins
# the bands is itself a run of inside-stretches (every 19970 print is 60 ticks
# under dev1), so these tapes open the entry window at 10:40 — after the wave
# (4000s from the 09:30 open ends it at 10:36:40) and after the leg that resets
# the arming state. What fills is then only what the scenario does.
#
#   20040 ─ leg 1: up through dev1, but only 10pts over — arms nothing either way
#   20030 ═ dev1 ══════════════════════════ leg 3 crosses back up: the A fill
#   20017.5 ┄ the arming line, 12.5pts under dev1
#   20010 ─ leg 2: the rip down — ARMED (inside)
#   20000 ═ mid — the target, reached by leg 4

INSIDE_TAPE = _grid((20000, 20040, 100),   # up over dev1: no beyond-stretch, resets the state
                    (20040, 20010, 100),   # the rip DOWN through dev1: arms the inside stretch
                    (20010, 20035, 100),   # the retest back up to dev1: variant A's fill
                    (20035, 19995, 100))   # and the reversion through the mid: the target
INSIDE_OPEN = time(10, 40)


def test_fade_inside_stretch_sells_the_retest_of_the_broken_band():
    with _fade_cache(INSIDE_TAPE) as base:
        cfg = replace(base, arm_stretch_side="inside", entry_open=INSIDE_OPEN)
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert len(trades) == 1, trades
    tr = trades[0]
    assert tr["direction"] == "Short"
    assert abs(tr["avg_entry"] - 20030) < 0.5, "the limit rests on dev1, hit from below"
    assert tr["exit_reason"] == "target"
    assert abs(tr["avg_exit"] - 20000) < 0.5, "the mid target, tracked live"
    # The arming stamp is the rip DOWN through the band — leg 2, the second 100
    # ticks of the scenario — and not the retest that filled: the setup was armed
    # by the break, and the fill came back UP to the band from under it.
    leg2 = [RTH_OPEN_UTC + pd.Timedelta(seconds=len(SQ_RTH) + k) for k in (100, 200)]
    assert leg2[0] < tr["acceptance_ts"] < leg2[1]
    assert tr["acceptance_ts"] < tr["entry_ts_utc"]


def test_fade_beyond_stretch_never_arms_on_the_inside_tape():
    """The same tape under the default arming: price never prints 12.5pts ABOVE
    dev1, so the overextension the fade was built on simply never happens. The
    two sides are genuinely different setups, not one setup restated."""
    with _fade_cache(INSIDE_TAPE) as base:
        cfg = replace(base, entry_open=INSIDE_OPEN)   # arm_stretch_side="beyond"
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert trades == [], trades


def test_fade_inside_variant_b_sells_the_failure_of_the_retest():
    """B's confirming close flips with the stretch and its stop does not: the
    retest is confirmed by a bar closing back ABOVE dev1, and the entry is the
    failure back DOWN through dev1 - entry_stop_offset (20027.5), into the
    channel — the same direction B always stops into."""
    with _fade_cache(INSIDE_TAPE) as base:
        cfg = replace(base, arm_stretch_side="inside", entry_open=INSIDE_OPEN)
        a_trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
        b_trades, _, _, _ = engine.run_session_fade(
            replace(cfg, entry_variant="B"), DAY, side="short")
    assert len(a_trades) == len(b_trades) == 1
    assert b_trades[0]["avg_entry"] <= 20027.5 + 0.1, "B fills past the offset, at market"
    assert b_trades[0]["entry_idx"] > a_trades[0]["entry_idx"], (
        "and only after the close above dev1 that A never waits for")
    assert b_trades[0]["exit_reason"] == "target"


def test_fade_inside_long_is_the_inside_short_reflected():
    """The mirror test, run against the third sign: reflect the tape and the
    lower-band fade armed INSIDE dev1 must reproduce the upper-band short's
    trades exactly, reflected. A sign error in the a/u/s frame cannot survive."""
    with _fade_cache(INSIDE_TAPE) as base:
        cfg = replace(base, arm_stretch_side="inside", entry_open=INSIDE_OPEN)
        short, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    with _fade_cache(INSIDE_TAPE, base=40000.0) as base:
        cfg = replace(base, arm_stretch_side="inside", entry_open=INSIDE_OPEN)
        long_, _, _, _ = engine.run_session_fade(cfg, DAY, side="long")
    assert len(short) == len(long_) == 1
    for s_, l_ in zip(short, long_):
        assert l_["direction"] == "Long"
        assert (s_["entry_idx"], s_["exit_idx"]) == (l_["entry_idx"], l_["exit_idx"])
        assert s_["exit_reason"] == l_["exit_reason"]
        assert abs(l_["avg_entry"] - (40000 - s_["avg_entry"])) < 1e-6
        assert abs(l_["avg_exit"] - (40000 - s_["avg_exit"])) < 1e-6
        assert abs(l_["net_pnl"] - s_["net_pnl"]) < 1e-6


def test_fade_inside_limit_offset_rests_below_the_band():
    """The offset is "in front of dev1, toward the stretch" — which under an
    inside arming is BELOW the band. The retest then fills on the way up before
    it reaches dev1, at a worse price than the band's, not a better one."""
    with _fade_cache(INSIDE_TAPE) as base:
        cfg = replace(base, arm_stretch_side="inside", entry_open=INSIDE_OPEN,
                      entry_limit_offset_ticks=20)   # 5 points under dev1
        trades, _, _, _ = engine.run_session_fade(cfg, DAY, side="short")
    assert len(trades) == 1, trades
    assert abs(trades[0]["avg_entry"] - 20025) < 0.5, "dev1 - 20 ticks"


# --- the profile pullback's level-stability gate -----------------------------
#
# Against the real cached session that motivated the knob: 2025-12-09's only
# fill — long the NY VAH at 25630.50 at 09:46:26 ET — sat on a VAH that had
# held within the re-arm distance for ~3.6 minutes, but whose in-force series
# was NaN until the 09:45 warmup. The three runs pin the gate's semantics:
# off takes the fill, 2 minutes keeps it BECAUSE stability reads the level's
# raw path through the warmup mask, and 5 minutes vetoes it.

PB_DAY = date(2025, 12, 9)


def _have_pb_ticks() -> bool:
    return (ticks._cache_path("NQZ5", PB_DAY).exists()
            and ticks._cache_path("NQZ5", PB_DAY, "on").exists())


def _pullback(stability_min: int):
    cfg = ProfilePullbackConfig(use_globex_levels=False, max_touches_per_level=1,
                                min_level_stability_min=stability_min)
    trades, _, _, _ = engine.run_session_profile_pullback(cfg, PB_DAY)
    return trades


def test_pullback_stability_off_takes_the_fill():
    if not _have_pb_ticks():
        print("   (skipped: tick cache cold)")
        return
    trades = _pullback(0)
    assert len(trades) == 1, trades
    tr = trades[0]
    assert tr["level"] == "NY VAH"
    assert abs(tr["avg_entry"] - 25630.50) < 1e-9


def test_pullback_stability_reads_through_the_warmup_mask():
    """The fill's VAH had sat in place since ~09:42:50 — longer than 2 minutes,
    but reaching back past 09:45, where the warmup mask still hides the
    in-force series. Stability must read the raw path, or this fill (and every
    NY fill in the first minutes after warmup) would be wrongly vetoed."""
    if not _have_pb_ticks():
        print("   (skipped: tick cache cold)")
        return
    assert _same_trades(_pullback(2), _pullback(0))


def test_pullback_stability_vetoes_a_level_that_just_relocated():
    if not _have_pb_ticks():
        print("   (skipped: tick cache cold)")
        return
    assert _pullback(5) == []


# --- the value rotation ------------------------------------------------------

class _rotation_cache:
    """One synthetic RTH session with hand-built volume, served through the real
    tick cache, so the developing profile is exactly the histogram the test
    computed by hand. ``base`` reflects the tape (price -> base - price) for the
    long/short mirror test; the volume rides along, so the profile mirrors too."""

    def __init__(self, prices, sizes, base: float = 0.0):
        self.prices = [float(base - p if base else p) for p in prices]
        self.sizes = list(sizes)

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._old = ticks.TICK_CACHE_DIR
        ticks.TICK_CACHE_DIR = Path(self._tmp.name)
        ticks._read_day_parquet.cache_clear()
        ticks.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "ts_utc": pd.date_range(RTH_OPEN_UTC, periods=len(self.prices), freq="s", tz="UTC"),
            "price": self.prices,
            "size": self.sizes,
            "side": ["A"] * len(self.prices),
        }).to_parquet(ticks._cache_path("TEST", DAY, "rth"), index=False)
        from journal.sim.rules import ValueRotationConfig
        return ValueRotationConfig(
            contract="TEST", ticks_per_bar=5, level_warmup_min=0,
            entry_open=time(9, 30), arm_beyond_ticks=2, stop_ticks=8,
            min_room_ticks=4)

    def __exit__(self, *exc):
        ticks.TICK_CACHE_DIR = self._old
        ticks._read_day_parquet.cache_clear()
        self._tmp.cleanup()


# Bar 1 builds the profile by hand: POC 99 (80 lots), VAH 100, VAL 99 — the
# value area annexes 99.75+100 as one pair. Bar 2 is the excursion above the
# VAH (100.75 > 100 + 0.5 arming line) closing back inside at 99.75 — armed,
# then confirmed. Bar 3 is the retest to the failed edge and the rotation down
# through the POC.
ROT_PRICES = ([99, 99.75, 100, 99.75, 99]
              + [100.75, 100.75, 100.75, 99.75, 99.75]
              + [99.75, 100, 99.5, 99.25, 99])
ROT_SIZES = [40, 20, 25, 15, 40] + [1] * 10


def test_rotation_arms_confirms_and_rides_to_the_poc():
    with _rotation_cache(ROT_PRICES, ROT_SIZES) as cfg:
        trades, vetoed, _, _ = engine.run_session_value_rotation(cfg, DAY)
    assert len(trades) == 1 and vetoed == [], trades
    tr = trades[0]
    assert tr["direction"] == "Short"
    assert abs(tr["avg_entry"] - 100.0) < 1e-9, "the limit rests on the failed VAH"
    assert tr["exit_reason"] == "target"
    assert abs(tr["avg_exit"] - 99.0) < 1e-9, "the POC target, tracked live"
    assert abs(tr["band_width_ticks"] - 4) < 1e-9, "POC room at entry, in ticks"
    assert abs((tr["stop_price"] - tr["avg_entry"]) - cfg.stop_ticks * TICK) < 1e-9
    # The arming stamp is the excursion print — before the confirming close.
    assert tr["acceptance_ts"] == RTH_OPEN_UTC + pd.Timedelta(seconds=5)
    assert tr["acceptance_ts"] < tr["entry_ts_utc"]


def test_rotation_without_room_to_the_poc_never_fills():
    """The trivial-rotation guard: the same tape with one more tick of required
    room takes no trade — and books no ghost, a miss is not a veto."""
    with _rotation_cache(ROT_PRICES, ROT_SIZES) as cfg:
        trades, vetoed, _, _ = engine.run_session_value_rotation(
            replace(cfg, min_room_ticks=5), DAY)
    assert trades == [] and vetoed == []


def test_rotation_variant_b_stops_into_the_rotation():
    with _rotation_cache(ROT_PRICES, ROT_SIZES) as cfg:
        a_trades, _, _, _ = engine.run_session_value_rotation(cfg, DAY)
        b_trades, _, _, _ = engine.run_session_value_rotation(
            replace(cfg, entry_variant="B", entry_stop_offset_ticks=2,
                    min_room_ticks=2), DAY)
    assert len(a_trades) == len(b_trades) == 1
    assert abs(b_trades[0]["avg_entry"] - 99.5) < 1e-9, "B fills past the offset, at market"
    assert b_trades[0]["entry_idx"] > a_trades[0]["entry_idx"]
    assert b_trades[0]["exit_reason"] == "target"
    assert abs(b_trades[0]["avg_exit"] - 99.0) < 1e-9


def test_rotation_edge_snapping_across_price_disarms_instead_of_filling():
    """A VA rebuild that relocates the VAH below a standing print must NOT book
    a limit fill at the relocated edge (profile-pullback's v3 phantom): price
    never did the crossing, so the confirmed setup dies instead."""
    prices = ROT_PRICES[:10] + [98] * 5 + [99] * 5
    sizes = ROT_SIZES[:10] + [500] * 5 + [1] * 5
    with _rotation_cache(prices, sizes) as cfg:
        trades, vetoed, _, _ = engine.run_session_value_rotation(cfg, DAY)
    assert trades == [] and vetoed == [], trades


def test_rotation_long_is_the_short_reflected():
    """Reflect the tape (and so the profile) about a constant: the long off the
    VAL must reproduce the short's trade exactly, reflected — the one test a
    sign error anywhere in the u/s frame cannot survive."""
    with _rotation_cache(ROT_PRICES, ROT_SIZES) as cfg:
        short, _, _, _ = engine.run_session_value_rotation(cfg, DAY)
    with _rotation_cache(ROT_PRICES, ROT_SIZES, base=200.0) as cfg:
        long_, _, _, _ = engine.run_session_value_rotation(
            replace(cfg, side="long"), DAY)
    assert len(short) == len(long_) == 1
    s_, l_ = short[0], long_[0]
    assert l_["direction"] == "Long"
    assert (s_["entry_idx"], s_["exit_idx"]) == (l_["entry_idx"], l_["exit_idx"])
    assert s_["exit_reason"] == l_["exit_reason"]
    assert abs(l_["avg_entry"] - (200 - s_["avg_entry"])) < 1e-6
    assert abs(l_["avg_exit"] - (200 - s_["avg_exit"])) < 1e-6
    assert abs(l_["net_pnl"] - s_["net_pnl"]) < 1e-6


# --- drift-touch fade -------------------------------------------------------
#
# The shared gap-closer is hand-computable; the engine invariants run over the
# real cached session, like every other engine test here.

def test_gap_closer_flags_a_drift_touch():
    """A level price wiggled into (net move away over the window, level static)
    is a drift; a level price moved toward is not."""
    lvl = np.full(6, 100.0)                     # a static level, e.g. a session ref
    # Price hugs just above the level and drifts UP away from it: close rises from
    # 100.5 to 102, so over the window price moved AWAY. total <= 0 -> drift.
    away = np.array([100.5, 100.75, 101.0, 101.25, 101.5, 102.0])
    cls, price_closed, _ = profmod.gap_closer(lvl, away, 5)
    assert cls == "drift", (cls, price_closed)
    assert price_closed <= 0
    # Price approaches the level (falls toward it): price closed the gap, not drift.
    toward = np.array([104.0, 103.0, 102.0, 101.0, 100.5, 100.0])
    assert profmod.gap_closer(lvl, toward, 5)[0] == "price"
    # No history yet -> unknown, never a false drift.
    assert profmod.gap_closer(lvl, away, 0)[0] == "unknown"


def _drift(**over):
    """One real-session drift-fade run with test-friendly gates off."""
    cfg = DriftFadeConfig(instrument="NQ", contract="NQ",
                          start_date=DAY, end_date=DAY, **over)
    trades, vetoed, _, _ = engine.run_session_drift_fade(cfg, DAY)
    return cfg, trades, vetoed


def test_drift_stop_is_measured_from_the_level_not_the_fill():
    """The zone is the invalidation: stop_ticks sits behind the LEVEL, so the
    distance from the fill varies and the R booked on a stop-out reflects it."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _ = _drift()
    assert trades, "the fixture must actually trade"
    for tr in trades:
        s = 1.0 if tr["direction"] == "Long" else -1.0
        level = tr["stop_price"] + s * cfg.stop_ticks * TICK  # invert stop = level - s*risk
        # The fill is a market order near the level, never the level itself.
        assert abs(tr["avg_entry"] - level) < cfg.stop_ticks * TICK
        # R is always measured against the nominal stop distance, not the fill's.
        assert abs(tr["r_multiple"] - tr["points"] / (cfg.stop_ticks * TICK)) < 1e-9


def test_drift_entry_stop_variant_anchors_risk_to_the_fill():
    """The entry-stop strategy trades the same signals but every trade risks
    exactly stop_ticks from the entry print — the varying fill-to-level distance
    must not leak into the initial stop."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg = DriftFadeConfig(instrument="NQ", contract="NQ",
                          start_date=DAY, end_date=DAY)
    trades, _, _, _ = engine.run_session_drift_fade_entry_stop(cfg, DAY)
    assert trades, "the fixture must actually trade"
    for tr in trades:
        s = 1.0 if tr["direction"] == "Long" else -1.0
        assert abs((tr["avg_entry"] - tr["stop_price"]) - s * cfg.stop_ticks * TICK) < 1e-9
    # Same detection: the first fill matches the level-stop original (later
    # trades may diverge — different exits re-time the one-position slot).
    base, _, _, _ = engine.run_session_drift_fade(cfg, DAY)
    assert trades[0]["avg_entry"] == base[0]["avg_entry"]
    assert trades[0]["stop_price"] != base[0]["stop_price"]


def test_drift_entries_stay_inside_the_window_and_name_a_side():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, trades, _ = _drift()
    assert trades
    for tr in trades:
        et = tr["entry_ts_utc"].tz_convert("America/New_York")
        assert time(9, 45) <= et.time() < time(15, 0), et
        assert tr["direction"] in ("Long", "Short")


def test_drift_entry_names_the_reference_level_it_faded():
    """Every fill records which candidate zone's drift-touch triggered it, by the
    same human name the engine builds the level under — the entry attribution the
    by_entry_reason edges cut slices on. A blank or off-vocabulary reason means the
    name was dropped somewhere on the best_signal -> pending -> _Pos -> _row path."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    vocab = {"Globex POC", "Globex VAH", "Globex VAL",
             "NY POC", "NY VAH", "NY VAL", "Open", "ONH", "ONL",
             "pd POC", "pd VAH", "pd VAL", "pd Close"}
    _, trades, _ = _drift()
    assert trades
    seen = {tr["entry_reason"] for tr in trades}
    assert seen, "trades must carry an entry_reason"
    assert seen <= vocab, f"off-vocabulary entry reasons: {seen - vocab}"
    assert all(tr["entry_reason"] for tr in trades), "no fill may have a blank reason"


def test_drift_side_filter_partitions_direction():
    """long-only takes only the support fades, short-only only the resistance
    ones — a sign error in the hugged-side read would cross them."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, longs, _ = _drift(side="long")
    _, shorts, _ = _drift(side="short")
    assert all(t["direction"] == "Long" for t in longs)
    assert all(t["direction"] == "Short" for t in shorts)
    assert longs or shorts, "at least one side must set up in the session"


def test_drift_is_deterministic():
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    _, a, _ = _drift()
    _, b, _ = _drift()
    assert _same_trades(a, b)


def test_drift_r_multiple_target_is_a_fixed_distance():
    """With an R target the exit on a 'target' fill is exactly target_rr behind
    the fill in the trade's direction — no live tracking."""
    if not _have_ticks():
        print("   (skipped: tick cache cold)")
        return
    cfg, trades, _ = _drift(target_mode="r_multiple", target_rr=1.5)
    hit = [t for t in trades if t["exit_reason"] == "target"]
    if not hit:
        print("   (no target fills in the fixture — vacuously ok)")
        return
    for tr in hit:
        s = 1.0 if tr["direction"] == "Long" else -1.0
        want = tr["avg_entry"] + s * 1.5 * cfg.stop_ticks * TICK
        assert abs(tr["avg_exit"] - want) < 1e-6, (tr["avg_exit"], want)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
