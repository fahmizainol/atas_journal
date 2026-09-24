"""Pre-entry / post-exit context — the ways a window measurement lies quietly.

None of these failures raise. Every one of them produces a full table of
plausible numbers that point the wrong way, which is why they are worth pinning:

  - **the sign must follow the trade, not the chart.** A short that kept falling
    after the exit left money on the table exactly as a long that kept rising
    did. Get this wrong and every "I exit too early" finding is computed with
    half the sample inverted — and the totals still look reasonable.
  - **a truncated window is not a quiet window.** A trade exited four minutes
    before the close has no 30-minute follow-through to measure, and a zero there
    means the clock ran out, not that price stood still. ``post_avail_s`` is the
    only thing that separates the two.
  - **"came back to the entry" must not be a tautology.** A long stopped out sits
    *below* its entry already; asking when price next traded below it answers "at
    the next tick" for every loser in the journal.
  - **the location of a fill in its approach is raw, not direction-signed.**
    Buying the high of the last 15 minutes and selling it are different trades
    that must not collapse onto the same number.
  - **the entry bar's body is signed to the trade, its location is not.** A
    rising bar was going a long's way and against a short's, so a stored "green"
    would mean opposite things in one column — while filling at the top of that
    bar means the same thing to both, and must stay raw.
  - **a tape that isn't the trade's tape must be refused.** Same roll trap as
    ``level_tag``: the resolved contract can be one the trade never touched, and
    the windows come back healthy-looking and meaningless.

And the boundary: this module measures strictly *outside* the hold. MAE/MFE
between entry and exit belongs to ``journal.excursion``, on minute bars, and two
numbers with one name is how a journal starts lying.

Run directly:  ``.venv/bin/python tests/test_trade_context.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import trade_context as tc  # noqa: E402
from journal.trade_context import PricePath, Trade  # noqa: E402

START = pd.Timestamp("2026-03-10 13:30", tz="UTC")   # 09:30 ET


def _path(prices, *, step_s: int = 1) -> PricePath:
    """A tape of one price per ``step_s`` seconds from START."""
    ts = START + pd.to_timedelta(np.arange(len(prices)) * step_s, unit="s")
    return PricePath(pd.DataFrame({"ts_utc": ts, "price": np.asarray(prices, float)}),
                     symbol="NQTEST")


def _at(seconds: int) -> pd.Timestamp:
    return START + pd.Timedelta(seconds=seconds)


def _flat_then(pre: float, n_pre: int, tail: list[float]) -> list[float]:
    return [pre] * n_pre + list(tail)


# -- the sign convention ----------------------------------------------------

def test_short_that_kept_falling_shows_positive_follow_through():
    """The one that inverts half the journal if it is wrong."""
    # 30m flat at 100, exit at 95, then price slides to 85 over the next 10m.
    prices = _flat_then(100.0, 1800, list(np.linspace(95.0, 85.0, 600)))
    path = _path(prices)
    t = Trade(key="s", direction="Short", entry_ts=_at(1700), entry_px=100.0,
              exit_ts=_at(1800), exit_px=95.0)
    c = tc.measure(path, t)
    # The slide takes the full 10 minutes, so the 15m horizon sees all of it and
    # the 5m horizon sees half — which is the point of keeping four horizons.
    assert c.post_mfe_pts[15] == pytest.approx(10.0, abs=0.1), \
        "a short that kept falling made money it did not take"
    assert c.post_mfe_pts[5] == pytest.approx(5.0, abs=0.1)
    assert c.post_mae_pts[15] <= 0.0


def test_long_and_short_on_the_same_tape_mirror_each_other():
    prices = _flat_then(100.0, 1800, list(np.linspace(100.0, 110.0, 600)))
    path = _path(prices)
    common = dict(entry_ts=_at(1700), entry_px=100.0, exit_ts=_at(1800),
                  exit_px=100.0)
    long_c = tc.measure(path, Trade(key="l", direction="Long", **common))
    short_c = tc.measure(path, Trade(key="s", direction="Short", **common))
    assert long_c.post_mfe_pts[5] == pytest.approx(-short_c.post_mae_pts[5], abs=1e-6)
    # The two ranks partition the window except for the prints that tied with the
    # exit, which count for neither side — so they sum to just under 1.
    total = long_c.exit_rank + short_c.exit_rank
    assert total <= 1.0 and total == pytest.approx(1.0, abs=0.01)


# -- was the direction right ------------------------------------------------

def test_forward_window_ignores_the_exit_entirely():
    """A scratch and a runner off the same entry must grade identically.

    The whole reason the forward window exists: every other number in the
    journal is contaminated by the management, so none of them can answer
    whether the *entry* was pointing the right way.
    """
    # 301 samples, not 300: the tail runs t=1800..2100 inclusive, so the last
    # horizon lands exactly ON the final tick rather than one past it.
    prices = _flat_then(100.0, 1800, list(np.linspace(100.0, 106.0, 301)))
    path = _path(prices)
    common = dict(direction="Long", entry_ts=_at(1800), entry_px=100.0)
    scratched = tc.measure(path, Trade(key="a", exit_ts=_at(1805),
                                       exit_px=100.0, **common))
    held = tc.measure(path, Trade(key="b", exit_ts=_at(2090),
                                  exit_px=106.0, **common))
    assert scratched.fwd_pts == held.fwd_pts, "the exit must not enter this"
    # Price climbs 6 points over 5 minutes, so each horizon reads its share.
    assert scratched.fwd_pts[30] == pytest.approx(0.6, abs=0.05)
    assert scratched.fwd_pts[60] == pytest.approx(1.2, abs=0.05)
    assert scratched.fwd_pts[300] == pytest.approx(6.0, abs=0.05)


def test_forward_window_is_signed_to_the_trade():
    """A short that got it right reads positive, exactly as a long would."""
    prices = _flat_then(100.0, 1800, list(np.linspace(100.0, 94.0, 301)))
    path = _path(prices)
    common = dict(entry_ts=_at(1800), entry_px=100.0, exit_ts=_at(1810),
                  exit_px=100.0)
    short_c = tc.measure(path, Trade(key="s", direction="Short", **common))
    long_c = tc.measure(path, Trade(key="l", direction="Long", **common))
    assert short_c.fwd_pts[300] == pytest.approx(6.0, abs=0.05)
    assert long_c.fwd_pts[300] == pytest.approx(-6.0, abs=0.05)


def test_forward_horizon_past_the_bell_is_none_not_the_close():
    """The trap this measurement would otherwise walk into every afternoon.

    ``price_at`` walks back to the last tick at or before an instant, so asking
    it for a horizon past the end of the tape hands back the session's closing
    price — a truncated window wearing a perfectly plausible number, and one
    that would quietly score every late entry against the same tick.
    """
    # Entry with only 90 seconds of tape after it.
    prices = _flat_then(100.0, 1800, list(np.linspace(100.0, 103.0, 90)))
    path = _path(prices)
    c = tc.measure(path, Trade(key="late", direction="Long", entry_ts=_at(1800),
                               entry_px=100.0, exit_ts=_at(1820), exit_px=100.5))
    assert c.fwd_avail_s == pytest.approx(89.0, abs=1.5)
    assert c.fwd_pts[30] is not None
    assert c.fwd_pts[60] is not None
    assert c.fwd_pts[300] is None, "five minutes of tape that never existed"


def test_direction_edge_keeps_every_denominator():
    """Unmeasured trades and expired horizons must not vanish into the rate."""
    def row(**kw):
        return {"fwd_avail_s": 1800.0,
                **{f"fwd_pts_{k}": v for k, v in kw.items()}}

    rows = [
        row(**{"30s": 2.0, "1m": 3.0, "5m": 5.0}),    # right throughout
        row(**{"30s": -1.0, "1m": -2.0, "5m": 4.0}),  # wrong early, right later
        row(**{"30s": 0.0, "1m": 1.0, "5m": None}),   # flat, then out of tape
        None,                                          # session never cached
        # A row from BEFORE the forward window existed. The table is mixed
        # whenever a METHOD bump is mid-backfill, and counting this as measured
        # would report a denominator the horizons cannot fill.
        {"pre_avail_s": 1800.0, "fwd_avail_s": None},
    ]
    edge = tc.direction_edge(rows)
    assert edge["trades"] == 5, "the day took five trades"
    assert edge["measured"] == 3, "only three carry a forward window"
    by_label = {h["label"]: h for h in edge["horizons"]}
    assert by_label["5m"]["n"] == 2, "the third trade ran out of session"
    assert by_label["5m"]["hit_rate"] == pytest.approx(100.0)
    # The exact zero is neither a win nor a loss, and is counted where it can be
    # seen rather than folded into either side.
    assert by_label["30s"]["right"] == 1
    assert by_label["30s"]["flat"] == 1
    assert by_label["30s"]["n"] == 3
    assert by_label["30s"]["hit_rate"] == pytest.approx(100 / 3, abs=0.1)
    assert by_label["1m"]["median_pts"] == pytest.approx(1.0)


def test_direction_edge_over_nothing_is_empty_not_zero():
    """No measured trade means no hit rate — 0% would read as "always wrong"."""
    edge = tc.direction_edge([None, None])
    assert edge["trades"] == 2 and edge["measured"] == 0
    assert all(h["hit_rate"] is None and h["n"] == 0 for h in edge["horizons"])


# -- the clock running out --------------------------------------------------

def test_truncated_post_window_is_reported_not_hidden():
    """A zero over two minutes of tape must be distinguishable from a real zero."""
    prices = _flat_then(100.0, 1800, [100.0] * 120)      # tape ends 2m after exit
    path = _path(prices)
    c = tc.measure(path, Trade(key="t", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=100.0))
    assert c.post_avail_s == pytest.approx(119.0, abs=1.5), \
        "the window has to say how much tape it actually got"
    assert c.post_avail_s < 30 * 60
    # The number itself is still zero — that is fine, and exactly why the caller
    # is required to read post_avail_s before believing it.
    assert c.post_mfe_pts[30] == pytest.approx(0.0)


def test_window_with_no_tape_at_all_is_none_not_zero():
    prices = [100.0] * 600
    path = _path(prices)
    # Exit on the last tick: nothing follows it.
    c = tc.measure(path, Trade(key="t", direction="Long", entry_ts=_at(300),
                               entry_px=100.0, exit_ts=_at(599), exit_px=100.0))
    assert c.post_mfe_pts[5] is not None      # the exit tick itself is in-window
    # ...but a window that starts past the end of the tape has nothing to report.
    beyond = tc.measure(path, Trade(key="b", direction="Long", entry_ts=_at(0),
                                    entry_px=100.0, exit_ts=_at(0), exit_px=100.0))
    assert beyond.pre_run_pts[30] is None, "no tape before the open is not a zero run"


# -- the entry revisit ------------------------------------------------------

def test_stopped_long_is_not_credited_with_an_instant_revisit():
    """The tautology guard. A stopped-out long is already below its entry."""
    # Entry 100, stopped at 90, price stays at 90 for 5m, then climbs back to 100.
    tail = [90.0] * 300 + list(np.linspace(90.0, 100.0, 300))
    path = _path(_flat_then(100.0, 1800, tail))
    c = tc.measure(path, Trade(key="stop", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=90.0))
    assert c.post_ret_entry_s is not None
    assert c.post_ret_entry_s > 300, \
        "price was below the entry the whole time — the revisit is the climb back"


def test_winner_revisit_measures_the_giveback():
    tail = list(np.linspace(110.0, 100.0, 600))
    path = _path(_flat_then(100.0, 1800, tail))
    c = tc.measure(path, Trade(key="win", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=110.0))
    assert c.post_ret_entry_s == pytest.approx(599, abs=5)


def test_scratch_gets_no_revisit_answer():
    path = _path(_flat_then(100.0, 1800, [100.0] * 600))
    c = tc.measure(path, Trade(key="flat", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=100.0))
    assert c.post_ret_entry_s is None, \
        "an exit at the entry left price on neither side; 0s would be a fiction"


# -- the approach -----------------------------------------------------------

def test_pre_run_is_positive_when_you_chased():
    # Price climbs 100 -> 110 over 10m; a long buys the top of the climb.
    path = _path(list(np.linspace(100.0, 110.0, 600)) + [110.0] * 600)
    c = tc.measure(path, Trade(key="chase", direction="Long", entry_ts=_at(599),
                               entry_px=110.0, exit_ts=_at(700), exit_px=110.0))
    assert c.pre_run_pts[5] > 0, "price had already gone the trade's way"
    # The same approach, sold instead: the run was against the trade.
    s = tc.measure(path, Trade(key="fade", direction="Short", entry_ts=_at(599),
                               entry_px=110.0, exit_ts=_at(700), exit_px=110.0))
    assert s.pre_run_pts[5] < 0


def test_pre_loc_is_raw_so_buying_and_selling_the_high_differ():
    path = _path(list(np.linspace(100.0, 110.0, 900)) + [110.0] * 300)
    common = dict(entry_ts=_at(899), entry_px=110.0, exit_ts=_at(1000),
                  exit_px=110.0)
    long_c = tc.measure(path, Trade(key="l", direction="Long", **common))
    short_c = tc.measure(path, Trade(key="s", direction="Short", **common))
    assert long_c.pre_loc_15m == pytest.approx(1.0, abs=0.01)
    assert short_c.pre_loc_15m == pytest.approx(1.0, abs=0.01), \
        "location is where the fill sat in the range, not whether that was good"


def test_flat_approach_has_no_location():
    path = _path([100.0] * 2400)
    c = tc.measure(path, Trade(key="f", direction="Long", entry_ts=_at(1800),
                               entry_px=100.0, exit_ts=_at(1900), exit_px=100.0))
    assert c.pre_range_pts_15m == pytest.approx(0.0)
    assert c.pre_loc_15m is None, "a range of zero has no inside to sit in"


# -- the tape has to be the trade's tape ------------------------------------

def test_fills_off_the_tape_are_refused():
    path = _path([100.0] * 2400)
    good = Trade(key="g", direction="Long", entry_ts=_at(1800), entry_px=100.0,
                 exit_ts=_at(1900), exit_px=100.0)
    # Same instants, a contract hundreds of points away — the roll trap.
    wrong = Trade(key="w", direction="Long", entry_ts=_at(1800), entry_px=17756.0,
                  exit_ts=_at(1900), exit_px=17760.0)
    assert tc.trades_align(path, [good])
    assert not tc.trades_align(path, [wrong])
    assert tc.measure_day(contract="NQ", day=START.date(), trades=[wrong],
                          path=path) == []


def test_one_stray_trade_cannot_hide_behind_a_healthy_day():
    """The failure a day-level median is blind to, and it is not hypothetical.

    A paper account quoting its own feed, or a replay sitting of another era's
    contract booked onto a calendar day, puts ONE trade on a market the rest of
    the day never touched. Ten healthy trades carry the median straight through
    the roll guard, and the eleventh gets a full row of plausible numbers
    measured against the wrong tape.
    """
    path = _path([100.0] * 2400)
    healthy = [Trade(key=f"h{i}", direction="Long", entry_ts=_at(1000 + i * 10),
                     entry_px=100.0, exit_ts=_at(1100 + i * 10), exit_px=100.0)
               for i in range(10)]
    stray = Trade(key="stray", direction="Long", entry_ts=_at(1800),
                  entry_px=234.75, exit_ts=_at(1900), exit_px=236.0)
    day = healthy + [stray]

    assert tc.trades_align(path, day), "the day's median is healthy — that is the trap"
    assert not tc.trade_aligns(path, stray)
    keys = {c.key for c in tc.measure_day(contract="NQ", day=START.date(),
                                          trades=day, path=path)}
    assert keys == {t.key for t in healthy}, "the stray must not be measured"


def test_an_averaged_fill_a_few_points_off_is_still_measured():
    """The guard's other half: it must not refuse honest trades.

    A logical trade's price is a size-weighted average of fills, so it sits off
    any single instant's tick by construction — this journal's real gaps reach
    ~12 points at p99.5. A guard tight enough to catch those would throw away
    every scaled entry in the book.
    """
    path = _path([100.0] * 2400)
    scaled = Trade(key="scaled", direction="Long", entry_ts=_at(1800),
                   entry_px=112.5, exit_ts=_at(1900), exit_px=113.0)
    assert tc.trade_aligns(path, scaled)
    assert tc.measure(path, scaled) is not None


# -- the boundary -----------------------------------------------------------

def test_nothing_here_describes_the_hold():
    """``journal.excursion`` owns entry-to-exit. Two names for one number is how
    a journal starts disagreeing with itself."""
    path = _path(_flat_then(100.0, 1800, [100.0] * 600))
    c = tc.measure(path, Trade(key="h", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=100.0))
    fields = set(tc.as_row(c, method="m", computed_at="now"))
    assert not {f for f in fields if "hold" in f or "in_trade" in f}
    # `vol_`/`eb_` are readings AT the entry — the bar sizes on screen and the bar
    # the fill landed in — so they stay on the entry side of the same boundary.
    #
    # `fwd_` is the one prefix here whose minutes can overlap the hold, and it is
    # admitted on a strict condition: it is shaped by the CLOCK and never by the
    # exit, so it cannot become a second answer to "how far did this trade run".
    # A `fwd_` field that took the exit as an argument would be excursion again
    # under a new name, and would belong on the other side of this line.
    assert all(f.startswith(("pre_", "post_", "fwd_", "exit_", "vol_", "eb_"))
               or f in {"trade_key", "symbol", "tick_size", "method", "computed_at"}
               for f in fields)


# -- how big the bars were, and which one the fill landed in ----------------

def _sawtooth(n: int) -> list[float]:
    """One tick per second, rising 0.05 a second and resetting every minute.

    Every 1-minute bar therefore has the same range (59 x 0.05 = 2.95 points =
    11.8 ticks) and every 30-second bar exactly half of it, which is what makes
    the resolutions separable: a single number would hide that difference.
    """
    return [100.0 + (i % 60) * 0.05 for i in range(n)]


def test_bar_size_is_measured_per_resolution_in_ticks():
    path = _path(_sawtooth(2400))
    c = tc.measure(path, Trade(key="v", direction="Long", entry_ts=_at(1830),
                               entry_px=101.5, exit_ts=_at(1900), exit_px=102.0))
    assert c.tick_size == 0.25
    assert c.vol_med_ticks["1m"] == pytest.approx(11.8, abs=0.1)
    # The gap back to the minute's start makes each true range the bar's own
    # range here, so Wilder agrees with the median on a tape this regular.
    assert c.vol_atr_ticks["1m"] == pytest.approx(11.8, abs=0.2)
    assert c.vol_med_ticks["30s"] == pytest.approx(5.8, abs=0.1), \
        "half the bar, half the range — a scalp is not living in the 1m number"
    assert c.vol_med_ticks["30s"] < c.vol_med_ticks["1m"] <= c.vol_med_ticks["500t"]


def test_entry_bar_body_follows_the_trade_and_the_location_does_not():
    """The whole reason raw green/red is not stored: it means opposite things to
    a long and a short, while *where in the bar you filled* means the same."""
    path = _path(_sawtooth(2400))
    kw = dict(entry_ts=_at(1830), entry_px=101.5, exit_ts=_at(1900), exit_px=102.0)
    long_ = tc.measure(path, Trade(key="l", direction="Long", **kw))
    short = tc.measure(path, Trade(key="s", direction="Short", **kw))
    # The bar rose, so it was going the long's way and against the short's.
    assert long_.eb_body_ticks["1m"] == pytest.approx(11.8, abs=0.1)
    assert short.eb_body_ticks["1m"] == pytest.approx(-11.8, abs=0.1)
    # Location is raw: both filled at the same place in the same bar.
    assert long_.eb_loc["1m"] == pytest.approx(short.eb_loc["1m"])
    assert long_.eb_loc["1m"] == pytest.approx(1.5 / 2.95, abs=0.02)


def test_how_much_of_the_entry_bar_had_printed():
    """The honest form of "was the candle closed" — off the tape they all are."""
    path = _path(_sawtooth(2400))
    early = tc.measure(path, Trade(key="e", direction="Long", entry_ts=_at(1803),
                                   entry_px=100.15, exit_ts=_at(1900), exit_px=102.0))
    late = tc.measure(path, Trade(key="t", direction="Long", entry_ts=_at(1856),
                                  entry_px=102.8, exit_ts=_at(1900), exit_px=103.0))
    assert early.eb_elapsed["1m"] < 0.1, "the bar reacted to barely existed yet"
    assert late.eb_elapsed["1m"] > 0.9


# -- the row shape the table stores -----------------------------------------

def test_as_row_covers_every_column_and_widens_the_horizons():
    path = _path(_flat_then(100.0, 1800, [100.0] * 600))
    c = tc.measure(path, Trade(key="k", direction="Long", entry_ts=_at(1700),
                               entry_px=100.0, exit_ts=_at(1800), exit_px=100.0))
    row = tc.as_row(c, method=tc.METHOD, computed_at="2026-03-10 00:00:00")
    assert tuple(row) == tc.COLUMNS, "the writer inserts by this order"
    for m in tc.HORIZONS_MIN:
        assert f"post_mfe_pts_{m}m" in row
        assert f"pre_run_pts_{m}m" in row
    assert row["trade_key"] == "k"
    assert row["symbol"] == "NQTEST"


def test_exit_after_entry_is_required():
    path = _path([100.0] * 2400)
    assert tc.measure(path, Trade(key="x", direction="Long", entry_ts=_at(1900),
                                  entry_px=100.0, exit_ts=_at(1800),
                                  exit_px=100.0)) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
