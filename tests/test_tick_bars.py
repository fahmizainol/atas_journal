"""Minute bars off the tick cache — the journal's chart source since 2026-08-08.

What these guard is the three things that made the Databento bar path wrong, and
that a naive replacement would get wrong the same way:

  - **the window.** A bar is stamped at its *last* tick, so a trade that opened
    and closed inside one minute asks for a window containing no stamp at all.
    Masking on the stamp returned nothing for every sub-minute trade — which is
    most scalps, and exactly the trades whose charts you most want.
  - **the contract.** A journal row's `instrument` is the front month at *export*
    time, so every 2026 export says `NQU6` including the January trades that were
    `NQH6`. The day has to pick the contract, through the roll map.
  - **the wallet.** These are GETs. A chart that can reach Databento is a chart
    that can cost money and hang, so every read here is cache-only.

Run directly:  ``.venv/bin/python tests/test_tick_bars.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import tick_bars  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

DAY = date(2026, 1, 8)
RTH_OPEN_UTC = pd.Timestamp("2026-01-08 14:30", tz="UTC")   # 09:30 ET, winter
# The contract the roll map says traded that session — deliberately NOT the
# `NQU6` an ATAS export of the same trade would carry.
TRADED = "NQH6"


def _frame(start, n, seed=0, freq="100ms"):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "ts_utc": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
        "price": np.round((20000 + rng.normal(0, 5, n)) * 4) / 4,
        "size": rng.integers(1, 20, n).astype("float64"),
        "side": "A",
    })


class _cache:
    """Serve one synthetic session through the real tick cache and roll map."""

    def __init__(self, minutes=10, with_night=True):
        self.minutes, self.with_night = minutes, with_night

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = tickmod.TICK_CACHE_DIR
        self._old_roll = dict(tickmod._ROLL_CACHE)
        tickmod.TICK_CACHE_DIR = Path(self._tmp.name)
        tickmod.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tickmod._read_day_parquet.cache_clear()

        # 10 ticks/second so a minute is 600 ticks — enough that every minute
        # bucket is populated and bar boundaries are unambiguous.
        n = self.minutes * 600
        _frame(RTH_OPEN_UTC, n, seed=2).to_parquet(
            tickmod._cache_path(TRADED, DAY, "rth"), index=False)
        if self.with_night:
            on_start = RTH_OPEN_UTC - pd.Timedelta(minutes=5)
            _frame(on_start, 5 * 600, seed=1).to_parquet(
                tickmod._cache_path(TRADED, DAY, "on"), index=False)

        # The roll map is what turns a stale export label into the real contract.
        tickmod._ROLL_CACHE["NQ"] = {"sessions": {DAY.isoformat(): TRADED}, "closed": []}
        return self

    def __exit__(self, *exc):
        tickmod.TICK_CACHE_DIR = self._old_dir
        tickmod._ROLL_CACHE.clear()
        tickmod._ROLL_CACHE.update(self._old_roll)
        tickmod._read_day_parquet.cache_clear()
        self._tmp.cleanup()


# --- the window -------------------------------------------------------------


def test_a_sub_minute_trade_still_gets_the_bar_it_happened_in():
    """The bug that made every scalp's chart and excursion empty.

    A bar is stamped at its last tick (09:31:59.9), so a trade from 09:31:00.1
    to 09:31:26.8 contains no bar stamp — the bar covering it is stamped after
    the trade ended. Overlap, not containment, is the question.
    """
    with _cache():
        start = RTH_OPEN_UTC + pd.Timedelta(minutes=1, milliseconds=100)
        end = RTH_OPEN_UTC + pd.Timedelta(minutes=1, seconds=26)
        b = tick_bars.get_bars("NQU6@CME", start, end)

    assert b is not None and len(b) == 1, "the minute the trade happened in went missing"
    # Stamped at the minute's last tick, which is after the trade closed — the
    # exact condition a containment mask got wrong.
    assert b["ts_utc"].iloc[0] > end


def test_a_window_spanning_minutes_gets_each_of_them():
    with _cache(minutes=10):
        b = tick_bars.get_bars(
            "NQU6@CME", RTH_OPEN_UTC, RTH_OPEN_UTC + pd.Timedelta(minutes=3))
    # Minutes 0,1,2,3 all overlap the window.
    assert b is not None and len(b) == 4


def test_untrimmed_returns_the_whole_session_not_just_the_window():
    """`slice_to_window=False` is what lets a chart pan and zoom across the
    session from one load."""
    with _cache(minutes=10):
        w = RTH_OPEN_UTC + pd.Timedelta(minutes=2)
        narrow = tick_bars.get_bars("NQU6@CME", w, w + pd.Timedelta(seconds=30))
        wide = tick_bars.get_bars("NQU6@CME", w, w + pd.Timedelta(seconds=30),
                                  slice_to_window=False)
    assert len(narrow) == 1
    assert len(wide) == 15, "night (5) + RTH (10) minutes"


# --- the contract -----------------------------------------------------------


def test_the_day_picks_the_contract_not_the_export_label():
    """ATAS stamps the front month at export time, so a January trade is
    labelled NQU6 and actually happened on NQH6. Trusting the label finds no
    ticks at all."""
    with _cache():
        assert tick_bars.session_ticks("NQU6@CME", DAY) is not None
        # ...and it really is the other contract's file being read.
        assert tickmod.contract_for_cached("NQ", DAY) == TRADED


def test_a_session_the_roll_map_never_saw_is_absent_not_guessed():
    with _cache():
        assert tick_bars.session_ticks("NQU6@CME", date(2020, 1, 2)) is None


# --- the wallet -------------------------------------------------------------


def test_nothing_here_ever_reaches_databento(monkeypatch):
    """A chart is a GET. Runs are where ticks get paid for."""
    def boom(*a, **k):
        raise AssertionError("a chart tried to buy data")

    monkeypatch.setattr(tickmod, "get_day_ticks", boom)
    monkeypatch.setattr(tickmod, "contract_for", boom)
    monkeypatch.setattr(tickmod, "ensure_day", boom)
    with _cache():
        assert tick_bars.get_bars("NQU6@CME", RTH_OPEN_UTC,
                                  RTH_OPEN_UTC + pd.Timedelta(minutes=5)) is not None
        # A day with nothing cached is empty, not bought.
        assert tick_bars.get_bars("NQU6@CME",
                                  pd.Timestamp("2026-01-09 14:30", tz="UTC"),
                                  pd.Timestamp("2026-01-09 15:30", tz="UTC")) is None


# --- the session ------------------------------------------------------------


def test_a_session_runs_from_the_night_into_the_bell():
    with _cache(with_night=True):
        t = tick_bars.session_ticks("NQU6@CME", DAY)
    assert t is not None
    assert t["ts_utc"].min() == RTH_OPEN_UTC - pd.Timedelta(minutes=5)
    assert t["ts_utc"].is_monotonic_increasing, "segments must concatenate in order"


def test_a_session_with_no_night_is_still_a_session():
    """The night is context: a window whose overnight was never bought draws
    the RTH it has rather than nothing."""
    with _cache(with_night=False):
        t = tick_bars.session_ticks("NQU6@CME", DAY)
    assert t is not None and t["ts_utc"].min() == RTH_OPEN_UTC


def test_bars_carry_the_columns_the_old_bar_path_did():
    """Drop-in for databento_client.get_bars — `levels` and `excursion` do
    arithmetic on these names and were not otherwise changed."""
    with _cache():
        b = tick_bars.get_bars("NQU6@CME", RTH_OPEN_UTC,
                               RTH_OPEN_UTC + pd.Timedelta(minutes=5))
    assert set(["ts_utc", "open", "high", "low", "close", "volume"]) <= set(b.columns)
    assert b["high"].ge(b["low"]).all()
    assert b["volume"].gt(0).all()


def test_an_empty_cache_reports_nothing_to_chart():
    with tempfile.TemporaryDirectory() as tmp:
        old = tickmod.TICK_CACHE_DIR
        tickmod.TICK_CACHE_DIR = Path(tmp)
        try:
            assert tick_bars.is_available() is False
        finally:
            tickmod.TICK_CACHE_DIR = old


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
