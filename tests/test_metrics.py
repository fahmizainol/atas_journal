"""Sharpe and Sortino: daily and annualized, not per trade.

The distinction is the whole reason they were changed. A per-trade ratio is blind
to how often a strategy trades, so a setup that fires 63 times scores the same as
one that fires 527 times for the same per-trade noise — and the second is plainly
the better business. These pin the definition that fixes that, and the flat-day
rule that makes it honest.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import metrics  # noqa: E402


def _frame(days_and_pnl: list[tuple[str, float]]) -> pd.DataFrame:
    """One trade per (day, pnl), with the columns compute_metrics reads."""
    rows = []
    for i, (day, pnl) in enumerate(days_and_pnl):
        ts = pd.Timestamp(f"{day} 10:00:00", tz="America/New_York")
        rows.append({
            "trade_no": i + 1, "direction": "Long",
            "entry_ts_utc": ts.tz_convert("UTC"), "entry_ts_local": ts,
            "net_pnl": pnl, "commission": 14.0, "duration_s": 600.0,
        })
    return pd.DataFrame(rows)


def test_sharpe_is_daily_and_annualized():
    """One trade a day, four days: the ratio is mean/std of the DAILY series,
    times sqrt(252) — not the per-trade ratio it used to be."""
    pnl = [100.0, -50.0, 200.0, -25.0]
    df = _frame([("2026-01-05", pnl[0]), ("2026-01-06", pnl[1]),
                 ("2026-01-07", pnl[2]), ("2026-01-08", pnl[3])])
    d = pd.Series(pnl)
    want = d.mean() / d.std(ddof=1) * math.sqrt(252)
    got = metrics.compute_metrics(df)["sharpe"]
    assert abs(got - want) < 1e-9, (got, want)
    # And it is not the per-trade number, which here is the same ratio unscaled.
    assert abs(got - d.mean() / d.std(ddof=1)) > 1.0


def test_two_trades_on_one_day_are_one_daily_observation():
    """The series is P&L per DAY, so a day's trades net off against each other
    before they are measured — that is what an account actually experiences."""
    split = metrics.compute_metrics(
        _frame([("2026-01-05", 100.0), ("2026-01-05", -50.0), ("2026-01-06", 30.0)]))
    merged = metrics.compute_metrics(
        _frame([("2026-01-05", 50.0), ("2026-01-06", 30.0)]))
    assert abs(split["sharpe"] - merged["sharpe"]) < 1e-9


def test_flat_days_inside_the_span_count_as_zero():
    """A day the strategy sat out earned 0. Dropping those days would flatter a
    rare setup by pretending its idle weeks never happened, so the series is
    reindexed over every weekday between the first trade and the last."""
    # Two winners a fortnight apart: 11 weekdays, 9 of them flat.
    sparse = metrics.compute_metrics(
        _frame([("2026-01-05", 100.0), ("2026-01-19", 100.0)]))
    # The same two winners, back to back, with no idle days between them.
    dense = metrics.compute_metrics(
        _frame([("2026-01-05", 100.0), ("2026-01-06", 100.0)]))
    # Back to back, the daily series has no variance at all -> no ratio to report.
    assert dense["sharpe"] == 0.0
    # Spread out, the flat days ARE the variance, and the Sharpe is finite.
    assert 0.0 < sparse["sharpe"] < 20.0
    # Weekends are not trading days and must not dilute it: Jan 5 and Jan 19 2026
    # are both Mondays, so the span is 11 weekdays, not 15 calendar days.
    d = pd.Series([100.0, 100.0] + [0.0] * 9)
    assert abs(sparse["sharpe"] - d.mean() / d.std(ddof=1) * math.sqrt(252)) < 1e-9


def test_sortino_only_charges_for_losing_days():
    """Upside volatility is not risk: a run whose good days are wildly good must
    not be punished for it, so only losing days sit in the denominator."""
    df = _frame([("2026-01-05", 100.0), ("2026-01-06", -50.0),
                 ("2026-01-07", 900.0), ("2026-01-08", -30.0)])
    m = metrics.compute_metrics(df)
    d = pd.Series([100.0, -50.0, 900.0, -30.0])
    down = pd.Series([-50.0, -30.0])
    want = d.mean() / down.std(ddof=1) * math.sqrt(252)
    assert abs(m["sortino"] - want) < 1e-9
    # The huge up day inflates total volatility but not downside volatility.
    assert m["sortino"] > m["sharpe"]


def test_a_single_day_has_no_ratio_to_report():
    """One observation has no deviation. Report 0 rather than dividing by it."""
    m = metrics.compute_metrics(_frame([("2026-01-05", 100.0)]))
    assert m["sharpe"] == 0.0 and m["sortino"] == 0.0


def test_no_losing_days_reports_no_sortino():
    """A denominator of zero is not an infinite Sortino, it is an unmeasured one."""
    m = metrics.compute_metrics(
        _frame([("2026-01-05", 100.0), ("2026-01-06", 50.0)]))
    assert m["sortino"] == 0.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
