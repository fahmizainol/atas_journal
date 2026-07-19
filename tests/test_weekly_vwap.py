"""The weekly VWAP anchor: seeded accumulation and the honesty rules.

The seed collapses each prior session to (Σv, Σpv, Σp²v), so the one identity
that makes the whole module correct is: seeded bands over today's ticks ==
plain bands over the week's concatenated ticks, row for row. The rest is the
two honesty rules — a week with a hole is not drawn, a roll restarts the
anchor — which mirror how the Globex anchor treats a missing night.

Run directly:  ``.venv/bin/python -m pytest tests/test_weekly_vwap.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import ticks as tickmod  # noqa: E402
from journal.sim import vwap as vwapmod  # noqa: E402
from journal.sim import weekly  # noqa: E402

MONDAY = date(2025, 10, 13)


def _ticks(day: date, seed: int, n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(f"{day} 13:30", tz="UTC")
    return pd.DataFrame({
        "ts_utc": pd.date_range(start, periods=n, freq="1s"),
        "price": np.round((20000 + rng.normal(0, 5, n)) * 4) / 4,
        "size": rng.integers(1, 20, n).astype("float64"),
        "side": "A",
    })


class _week:
    """A synthetic week in a temp tick cache: both segments for each of the
    given session dates, under one symbol."""

    def __init__(self, symbol: str, days: list[date], skip_on: set[date] = frozenset()):
        self.symbol, self.days, self.skip_on = symbol, days, skip_on

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = tickmod.TICK_CACHE_DIR
        tickmod.TICK_CACHE_DIR = Path(self._tmp.name)
        tickmod._read_day_parquet.cache_clear()
        tickmod.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.frames: dict[date, pd.DataFrame] = {}
        for i, d in enumerate(self.days):
            on = _ticks(d, seed=100 + i)
            rth = _ticks(d, seed=200 + i)
            if d not in self.skip_on:
                on.to_parquet(tickmod._cache_path(self.symbol, d, "on"), index=False)
            rth.to_parquet(tickmod._cache_path(self.symbol, d, "rth"), index=False)
            self.frames[d] = pd.concat(
                [on, rth] if d not in self.skip_on else [rth], ignore_index=True)
        return self

    def __exit__(self, *exc):
        tickmod.TICK_CACHE_DIR = self._old
        tickmod._read_day_parquet.cache_clear()
        self._tmp.cleanup()


def test_seeded_bands_equal_bands_over_the_concatenated_week():
    """The identity the seed exists for: collapsing prior sessions to three
    sums must reproduce, exactly, the accumulation over all their ticks."""
    days = [MONDAY, MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)]
    with _week("TEST", days) as wk:
        wed = wk.frames[days[2]]
        seed = weekly.weekly_seed("TEST", days[2])
        assert seed is not None
        seeded = vwapmod.vwap_bands(wed, seed=seed)

        whole = pd.concat([wk.frames[days[0]], wk.frames[days[1]], wed],
                          ignore_index=True)
        plain = vwapmod.vwap_bands(whole).iloc[-len(wed):].reset_index(drop=True)

    for col in vwapmod.BAND_COLS:
        np.testing.assert_allclose(seeded[col].to_numpy(), plain[col].to_numpy(),
                                   rtol=1e-12, err_msg=col)


def test_weekly_seed_is_zero_on_the_weeks_first_session():
    with _week("TEST", [MONDAY]):
        assert weekly.weekly_seed("TEST", MONDAY) == (0.0, 0.0, 0.0)


def test_weekly_seed_refuses_a_week_with_a_hole():
    """Tuesday's ticks were never bought: a 'weekly' VWAP missing a day would
    be a different line pretending to be this one, so there is none."""
    days = [MONDAY, MONDAY + timedelta(days=2)]  # Monday and Wednesday only
    with _week("TEST", days):
        assert weekly.weekly_seed("TEST", days[1]) is None


def test_weekly_seed_skips_the_weekend():
    """Monday's seed walks back to week_start = itself; a mid-week day never
    counts Saturday/Sunday as expected sessions."""
    days = [MONDAY, MONDAY + timedelta(days=1)]
    with _week("TEST", days):
        # No weekend parquets exist, yet Tuesday's week is whole.
        assert weekly.weekly_seed("TEST", days[1]) is not None


def test_session_sums_invalidate_when_the_night_arrives_later():
    """The cached scalars are keyed by the segments they summed: buying the
    overnight after the sums were first taken must recompute, not serve the
    RTH-only total under a now-fuller cache."""
    with _week("TEST", [MONDAY], skip_on={MONDAY}) as wk:
        rth_only = weekly.session_sums("TEST", MONDAY)
        assert rth_only is not None
        _ticks(MONDAY, seed=100).to_parquet(
            tickmod._cache_path("TEST", MONDAY, "on"), index=False)
        both = weekly.session_sums("TEST", MONDAY)

    assert both is not None and both[0] > rth_only[0], \
        "the night's volume never made it into the recomputed sums"
    expect = vwapmod.frame_sums(pd.concat(
        [_ticks(MONDAY, seed=100), _ticks(MONDAY, seed=200)], ignore_index=True))
    np.testing.assert_allclose(both, expect, rtol=1e-12)


def test_a_roll_restarts_the_weekly_anchor():
    """Mon/Tue trade the old contract, Wed rolls: Thursday's seed must contain
    Wednesday alone — never an average across a ~100-point contract seam."""
    days = [MONDAY + timedelta(days=i) for i in range(4)]
    old_roll = tickmod._ROLL_CACHE.pop("NQ", None)
    tickmod._ROLL_CACHE["NQ"] = {"sessions": {
        days[0].isoformat(): "NQZ9", days[1].isoformat(): "NQZ9",
        days[2].isoformat(): "NQH0", days[3].isoformat(): "NQH0",
    }, "closed": []}
    try:
        with _week("NQH0", days[2:3]):  # only Wednesday's ticks, on the new contract
            seed = weekly.weekly_seed("NQ", days[3])
            assert seed is not None
            wed_sums = weekly.session_sums("NQH0", days[2])
        np.testing.assert_allclose(seed, wed_sums, rtol=1e-12)
    finally:
        del tickmod._ROLL_CACHE["NQ"]
        if old_roll is not None:
            tickmod._ROLL_CACHE["NQ"] = old_roll


def test_exchange_closed_days_are_not_holes():
    """A holiday has no ticks to buy; the week around it is still whole."""
    days = [MONDAY, MONDAY + timedelta(days=2)]  # Tuesday closed
    tue = (MONDAY + timedelta(days=1)).isoformat()
    old_roll = tickmod._ROLL_CACHE.pop("NQ", None)
    tickmod._ROLL_CACHE["NQ"] = {"sessions": {
        days[0].isoformat(): "NQZ9", days[1].isoformat(): "NQZ9",
    }, "closed": [tue]}
    try:
        with _week("NQZ9", days):
            seed = weekly.weekly_seed("NQ", days[1])
            assert seed is not None
            mon_sums = weekly.session_sums("NQZ9", days[0])
        np.testing.assert_allclose(seed, mon_sums, rtol=1e-12)
    finally:
        del tickmod._ROLL_CACHE["NQ"]
        if old_roll is not None:
            tickmod._ROLL_CACHE["NQ"] = old_roll
