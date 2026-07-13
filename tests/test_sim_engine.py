"""Strategy simulator: tick bars, tick VWAP, and the engine's trade invariants.

The unit tests are hand-computable. The engine tests run over the *real* cached
NQZ5 session — a synthetic tick stream can't produce a realistic VWAP band, and
the whole point of these assertions is that the engine obeys its rules against
the same data it will actually be judged on. They skip if the tick cache is cold.

Run directly:  ``.venv/bin/python tests/test_sim_engine.py``
"""

from __future__ import annotations

import sys
from datetime import date
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
from journal.sim.rules import SimConfig  # noqa: E402

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
    assert base == off


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
        return SimConfig(contract="TEST", ticks_per_bar=50)

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
    off, _, _, _ = engine.run_session(SimConfig(trail_step_ticks=0), DAY)
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
    cfg = SimConfig(trail_step_ticks=75)
    trades, _, _, _ = engine.run_session(cfg, DAY)
    assert trades
    step = cfg.trail_step_ticks * TICK
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
    trades, _, _, _ = engine.run_session(SimConfig(trail_step_ticks=75), DAY)
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
    cfg = SimConfig(trail_step_ticks=75)
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
    cfg = SimConfig(trail_step_ticks=75)
    trades, _, _, _ = engine.run_session(cfg, DAY, side="short")
    if not trades:
        print("   (skipped: no short trades this session)")
        return
    step = cfg.trail_step_ticks * TICK
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
        trail_step_ticks=75,
        confluences={"volume_profile": {"enabled": True, "min_ticks_above_vah": 40}},
    )
    _, vetoed, _, _ = engine.run_session(cfg, DAY)
    if not vetoed:
        print("   (skipped: the gate vetoed nothing this session)")
        return
    step = cfg.trail_step_ticks * TICK
    for tr in vetoed:
        assert tr["final_stop_price"] >= tr["stop_price"] - 1e-9
        n = (tr["final_stop_price"] - tr["avg_entry"]) / step
        if abs(tr["final_stop_price"] - tr["stop_price"]) > 1e-9:
            assert abs(n - round(n)) < 1e-9


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
    assert base == off == huge


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
    assert halted == base[: trip + 1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
