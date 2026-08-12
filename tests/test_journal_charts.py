"""The journal's charts, built from the shared session builder.

The point of the migration was that a journal chart and a Lab chart stopped
being different *kinds* of picture — so what's worth asserting is that the
journal payload carries the layers it could never carry off 1-minute bars, and
that the two things that only break for short trades still work.

Run directly:  ``.venv/bin/python tests/test_journal_charts.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from api import charts_data  # noqa: E402
from test_tick_bars import DAY, RTH_OPEN_UTC, _cache  # noqa: E402

ET = "America/New_York"


def _trade(hold_s: float = 300.0, **over) -> pd.Series:
    entry = RTH_OPEN_UTC + pd.Timedelta(minutes=2)
    base = {
        "instrument": "NQU6@CME",          # the stale export label, on purpose
        "direction": "Long",
        "entry_ts_utc": entry,
        "exit_ts_utc": entry + pd.Timedelta(seconds=hold_s),
        "avg_entry": 20000.0,
        "avg_exit": 20010.0,
        "net_pnl": 200.0,
        "gross_pnl": 200.0,
        "max_contracts": 1.0,
        "duration_s": hold_s,
        "fills": None,
    }
    base.update(over)
    return pd.Series(base)


# --- the rectangle ----------------------------------------------------------


def test_a_trade_inside_one_candle_still_draws_a_rectangle():
    """Snapping both corners to the bar grid is right, and collapses a scalp to
    zero width — which draws as nothing at all, so the trade vanishes from its
    own chart."""
    def bar_time(_ts):
        return 1_000
    r = charts_data._trade_rect(_trade(), ET, bar_time)
    assert r["exit_time"] > r["entry_time"]


def test_a_normal_trade_keeps_the_corners_it_was_given():
    times = iter([1_000, 1_600])
    r = charts_data._trade_rect(_trade(), ET, lambda _ts: next(times))
    assert (r["entry_time"], r["exit_time"]) == (1_000, 1_600)


def test_a_trade_with_no_average_has_no_rectangle():
    assert charts_data._trade_rect(_trade(avg_exit=float("nan")), ET, None) is None


# --- the levels -------------------------------------------------------------


def test_far_levels_do_not_squash_the_candles():
    bars = [{"low": 20000.0, "high": 20050.0}]
    rows = [{"price": 20025.0}, {"price": 30000.0}]
    kept = charts_data._near_levels(rows, bars)
    assert [r["price"] for r in kept] == [20025.0]


def test_levels_with_nothing_drawn_are_dropped():
    assert charts_data._near_levels([{"price": 1.0}], []) == []


# --- the payload ------------------------------------------------------------


def test_a_journal_chart_carries_the_layers_a_lab_chart_does():
    """The whole migration in one assertion. None of these could exist off
    ohlcv-1m: a footprint and a CVD line need the tape, and an exact developing
    profile needs to know where inside the bar the volume actually traded."""
    with _cache(minutes=30):
        p = charts_data.trade_chart(_trade(), "1m", ET)

    assert p["available"] and p["bars"]
    for layer in ("vwap_globex", "vwap_ny", "profile_globex", "profile_ny",
                  "ema9", "ema20", "ema50", "ema200", "rsi", "atr_points",
                  "cvd", "footprint"):
        assert layer in p, f"the journal chart lost {layer}"
    assert len(p["footprint"]) == len(p["bars"]), "one volume-at-price map per bar"
    # No engine traded here, so no anchor is claimed as the one that was traded.
    assert p["vwap_anchor"] == "ny"


def test_the_chart_reports_the_contract_that_traded_not_the_label():
    with _cache(minutes=10):
        p = charts_data.trade_chart(_trade(), "1m", ET)
    assert p["instrument"] == "NQH6", "the export's stale NQU6 reached the chart header"


def test_a_session_with_no_ticks_says_which_one_is_missing():
    """The old path returned a bare empty bar list for a swallowed 402, which
    read on screen as 'this session had no trades'."""
    with _cache(minutes=5):
        far = RTH_OPEN_UTC + pd.Timedelta(days=40)
        p = charts_data.trade_chart(_trade(entry_ts_utc=far,
                                           exit_ts_utc=far + pd.Timedelta(minutes=5)),
                                    "1m", ET)
    assert p["available"] and p["bars"] == []
    assert "no ticks cached" in p["reason"]


def test_the_tick_timeframe_the_journal_never_had():
    with _cache(minutes=30):
        ticks_tf = charts_data.trade_chart(_trade(), "500t", ET)
        minutes = charts_data.trade_chart(_trade(), "1m", ET)
    assert ticks_tf["bars"] and minutes["bars"]
    # 30 min of RTH + 5 of night at 600 ticks/min = 21000 ticks -> 42 500t bars,
    # against 35 minute bars. Different grids, same session.
    assert len(ticks_tf["bars"]) != len(minutes["bars"])


def test_a_scalp_gets_an_excursion_off_the_minute_it_traded_in():
    """The sub-minute window bug, end to end: this returned no excursion at all
    for any trade shorter than a minute."""
    with _cache(minutes=10):
        p = charts_data.trade_chart(_trade(hold_s=26.7), "1m", ET)
    assert p.get("excursion"), "a 27-second trade lost its MAE/MFE"
    assert p["excursion"]["mfe_usd"] >= 0 >= p["excursion"]["mae_usd"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
