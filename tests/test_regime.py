"""Regime KPIs: the shape of a day, read off synthetic tape.

Unlike the engine tests (which need the real cached session to produce a
realistic band), these assert on *definitions* — a day that only goes up must
classify as a trend, a day that oscillates around its own VWAP must not — so a
hand-built tick stream is exactly the right instrument. The tick cache is
stubbed and the artifacts go to a temp dir, so nothing here reads the real cache
or reaches Databento.

Run directly:  ``.venv/bin/python tests/test_regime.py``
"""

from __future__ import annotations

import contextlib
import math
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.config import ET_TZ  # noqa: E402
from journal.sim import regime as regmod  # noqa: E402

DAY = date(2025, 10, 13)  # a Monday
SYMBOL = "NQZ5"

UP = lambda p: 20020 + 100 * p  # noqa: E731 — a clean one-way session
ON_UP = lambda p: 20000 + 20 * p  # noqa: E731 — a quiet drifting night
OSC = lambda p: 20000 + 30 * math.sin(p * 26 * math.pi)  # noqa: E731 — ~13 cycles
# Up 80 and all the way back: the closes sit above the (lagging) VWAPs for most
# of the session, yet close − open is a sliver of the range. A gap-and-fade day.
ROUND_TRIP = lambda p: 20000 + 80 * math.sin(p * math.pi)  # noqa: E731


def _seg(d0: date, t0: time, d1: date, t1: time, fn) -> pd.DataFrame:
    """Ticks every 10s over [t0, t1) ET, priced by fn(progress in [0, 1))."""
    start = pd.Timestamp(datetime.combine(d0, t0), tz=ET_TZ)
    end = pd.Timestamp(datetime.combine(d1, t1), tz=ET_TZ)
    ts = pd.date_range(start, end, freq="10s", inclusive="left").tz_convert("UTC")
    n = len(ts)
    return pd.DataFrame({
        "ts_utc": ts,
        "price": [float(fn(i / n)) for i in range(n)],
        "size": [1] * n,
        "side": ["A"] * n,
    })


def _overnight(fn) -> pd.DataFrame:
    return _seg(DAY - timedelta(days=1), time(18, 0), DAY, time(9, 30), fn)


def _rth(fn) -> pd.DataFrame:
    return _seg(DAY, time(9, 30), DAY, time(16, 0), fn)


@contextlib.contextmanager
def cache(rth: pd.DataFrame | None, on: pd.DataFrame | None):
    """Stand in for the tick cache, and send the artifacts to a temp dir."""
    real = (regmod.tickmod.cached_rth, regmod.tickmod.cached_overnight, regmod.REGIME_DIR)
    with tempfile.TemporaryDirectory() as td:
        state = {"rth": rth, "on": on}
        regmod.tickmod.cached_rth = lambda s, d: state["rth"]
        regmod.tickmod.cached_overnight = lambda s, d: state["on"]
        regmod.REGIME_DIR = Path(td) / "regime"
        try:
            yield state
        finally:
            (regmod.tickmod.cached_rth, regmod.tickmod.cached_overnight,
             regmod.REGIME_DIR) = real


def test_up_drift_day_is_a_trend():
    on, rth = _overnight(ON_UP), _rth(UP)
    with cache(rth, on):
        r = regmod.compute_regime(SYMBOL, DAY)
    eod = r["checkpoints"]["eod"]

    assert r["partial"] is False
    # Price is above both anchors all session and never leaves that quadrant.
    assert eod["abr"] > 0.9, eod["abr"]
    assert eod["bbr"] == 0.0
    assert eod["net_conviction"] > 0.9
    assert eod["quadrant_transitions_rate"] < 1
    # It never changes sides, and a one-way ramp closes at its extreme.
    assert eod["deep_flip_rate"] == 0.0
    assert eod["net_travel"] > 0.9, eod["net_travel"]
    assert eod["longest_hold_min"] > 300
    assert r["class"] == "trend_up"
    # The ribbon spans the whole session, one state per minute.
    full = pd.concat([on, rth], ignore_index=True)
    assert len(r["ribbon"]) == len(regmod.minute_bars(full))
    assert r["ribbon"][-1]["state"] == regmod.ABOVE_BOTH


def test_oscillating_day_is_balance():
    with cache(_rth(OSC), _overnight(OSC)):
        r = regmod.compute_regime(SYMBOL, DAY)
    eod = r["checkpoints"]["eod"]

    # Price keeps returning through its own VWAP — that is what a churn day is,
    # and it must not be able to read as a trend.
    assert abs(eod["net_conviction"]) < 0.3, eod["net_conviction"]
    assert eod["ny_vwap_cross_rate"] >= 2, eod["ny_vwap_cross_rate"]
    assert r["class"] == "balance"


def test_round_trip_day_is_parked():
    with cache(_rth(ROUND_TRIP), _overnight(ON_UP)):
        r = regmod.compute_regime(SYMBOL, DAY)
    eod = r["checkpoints"]["eod"]

    # The lagging VWAPs keep the closes one-sided — under v5 this day could only
    # read as a trend or be dumped into mixed. But it went nowhere: the whole
    # point of the class is that "held above both anchors" and "trended" are
    # different facts.
    assert eod["net_conviction"] >= 0.5, eod["net_conviction"]
    assert abs(eod["net_travel"]) < 0.3, eod["net_travel"]
    assert eod["deep_flip_rate"] <= 1.5, eod["deep_flip_rate"]
    assert r["class"] == "parked"


def test_checkpoints_only_see_their_own_past():
    with cache(_rth(UP), _overnight(ON_UP)):
        cps = regmod.compute_regime(SYMBOL, DAY)["checkpoints"]

    # 09:30 is the bell: no RTH bar has closed, so nothing but the overnight
    # priors is knowable. This is the anti-leakage guarantee the artifact exists
    # for — a model reading the 09:30 snapshot cannot see the session it is
    # about to predict.
    assert cps["09:30"]["bars"] == 0
    assert cps["09:30"]["abr"] is None
    assert cps["09:30"]["on_abr"] is not None
    # Every checkpoint carries the class its own KPIs support: nothing is
    # classifiable at the bell, and only eod's label is the day's verdict.
    assert cps["09:30"]["class"] == "unknown"
    assert cps["eod"]["class"] == "trend_up"
    # Bars accumulate monotonically and the cutoffs land where they say they do.
    assert [cps[k]["bars"] for k in ("09:30", "09:45", "10:30", "12:00", "eod")] == [
        0, 15, 60, 150, 390
    ]
    # The overnight priors are fixed at the bell — identical in every snapshot.
    assert cps["09:45"]["on_abr"] == cps["eod"]["on_abr"]


def test_missing_overnight_is_partial():
    with cache(_rth(UP), None):
        r = regmod.compute_regime(SYMBOL, DAY)
    eod = r["checkpoints"]["eod"]

    assert r["partial"] is True
    # Without the night there is no Globex anchor: the dual metrics and the
    # Globex band metrics are absent, not zero. The NY-anchored ones still stand.
    assert eod["abr"] is None and eod["gx_band_cross_rate"] is None
    assert eod["ny_upper_channel_occupancy"] is not None
    assert r["class"] == "unknown"
    # The ribbon degrades to the one anchor it has rather than inventing a quadrant.
    assert {b["state"] for b in r["ribbon"]} <= {regmod.ON_ABOVE, regmod.ON_BELOW}


def test_no_ticks_is_no_regime():
    with cache(None, None):
        assert regmod.compute_regime(SYMBOL, DAY) is None
        assert regmod.get_regime(SYMBOL, DAY) is None


def test_cache_round_trip_and_version_bump():
    with cache(_rth(UP), _overnight(ON_UP)) as state:
        first = regmod.get_regime(SYMBOL, DAY)
        path = regmod._path(SYMBOL, DAY)
        assert path.exists()

        # The second read is served from the file. Proved by making a recompute
        # impossible: the artifact must still come back.
        state["rth"] = None
        assert regmod.get_regime(SYMBOL, DAY) == first

        # A version bump orphans the old file rather than reinterpreting numbers
        # that now mean something else.
        regmod.REGIME_VERSION += 1
        try:
            assert regmod.get_regime(SYMBOL, DAY) is None  # no ticks -> no recompute
            assert not regmod._path(SYMBOL, DAY).exists()
            assert path.exists()  # the v1 artifact survives, it is just never read
        finally:
            regmod.REGIME_VERSION -= 1


def test_vwap_slope_units_agree():
    # The two units are the same measurement under different conventions — pts/min
    # is the plain derivative, degrees is atan(pts-per-min / ATR-per-min) — so
    # they must always carry the same sign, and the angle must stay a real angle.
    with cache(_rth(UP), _overnight(ON_UP)):
        r = regmod.compute_regime(SYMBOL, DAY)
    eod = r["checkpoints"]["eod"]

    # A one-way up session: the VWAP is climbing in both anchors, in both units.
    for a in ("ny", "gx"):
        ppm, deg = eod[f"{a}_vwap_slope_ppm"], eod[f"{a}_vwap_slope_deg"]
        assert ppm > 0 and deg > 0, (a, ppm, deg)
        assert -90 < deg < 90
    # UP climbs 100 pts over 390 min; the VWAP of a ramp climbs at half that rate,
    # and the 30-min window sees the tail of it.
    assert 0.1 < eod["ny_vwap_slope_ppm"] < 0.26, eod["ny_vwap_slope_ppm"]

    # At the bell no bar has closed: no slope, not a zero slope.
    bell = r["checkpoints"]["09:30"]
    assert bell["ny_vwap_slope_ppm"] is None and bell["ny_vwap_slope_deg"] is None

    # The overnight slope IS knowable at the bell — it's a prior, so it exists
    # at 09:30, climbs on a drifting-up night, and is fixed in every snapshot.
    assert bell["on_vwap_slope_ppm"] > 0 and bell["on_vwap_slope_deg"] > 0
    assert bell["on_vwap_slope_ppm"] == eod["on_vwap_slope_ppm"]


def test_vwap_slope_missing_without_its_anchor():
    with cache(_rth(UP), None):
        eod = regmod.compute_regime(SYMBOL, DAY)["checkpoints"]["eod"]
    # No overnight -> no Globex anchor -> no Globex slope, in either unit,
    # and no overnight prior slope either.
    assert eod["gx_vwap_slope_ppm"] is None and eod["gx_vwap_slope_deg"] is None
    assert eod["on_vwap_slope_ppm"] is None and eod["on_vwap_slope_deg"] is None
    assert eod["ny_vwap_slope_ppm"] is not None


def test_band_metrics_are_well_formed():
    # A trending day that keeps poking through +1σ: the ratios it produces have
    # to stay ratios whatever the tape does.
    with cache(_rth(lambda p: UP(p) + 6 * math.sin(p * 40 * math.pi)), _overnight(ON_UP)):
        eod = regmod.compute_regime(SYMBOL, DAY)["checkpoints"]["eod"]

    thr = eod["ny_touch_hold_ratio"]
    assert thr is None or 0.0 <= thr <= 1.0
    assert 0.0 <= eod["ny_upper_channel_occupancy"] <= 1.0
    assert eod["ny_band_cross_rate"] >= 0
    assert eod["longest_hold_min"] <= eod["bars"]


def test_gx_rescue_counts_the_catch_on_either_band():
    """The rescue event, pinned on both branches and both sides.

    _gx_rescue reads a signed frame (u = +1 upper, −1 lower) so one function
    serves the fade-short's floor and the fade-long's ceiling. Reflecting every
    price about zero and flipping u must reproduce the ratio exactly — the one
    check a sign error cannot survive.
    """
    import numpy as np

    ny = np.array([100.0, 100.0, 100.0])
    gx = np.array([95.0, 95.0, 95.0])          # the Globex floor, just underneath
    close = np.array([101.0, 99.0, 102.0])     # poked above, broke, closed back above

    # The catch: the dip held above the Globex line, and price reclaimed the
    # session band within the window.
    held = np.array([100.0, 96.0, 100.0])      # bar lows
    assert regmod._gx_rescue(close, held, ny, gx, u=1.0) == 1.0

    # The failure: the dip sliced the Globex line too — both bands gone, no rescue.
    sliced = np.array([100.0, 90.0, 100.0])
    assert regmod._gx_rescue(close, sliced, ny, gx, u=1.0) == 0.0

    # No break at all is no event, and no event is None — not zero. "It never
    # happened" must not read as "it happened and failed".
    assert regmod._gx_rescue(np.array([101.0, 102.0, 103.0]), held, ny, gx, u=1.0) is None

    # A Globex line on the NEAR side of the session band is no floor: the geometry
    # the event is defined on isn't there, so there is nothing to be rescued by.
    assert regmod._gx_rescue(close, held, ny, np.array([105.0] * 3), u=1.0) is None

    # And the mirror: negate everything (lows become highs), flip u, same answers.
    for pierce, expect in ((held, 1.0), (sliced, 0.0)):
        assert regmod._gx_rescue(-close, -pierce, -ny, -gx, u=-1.0) == expect


def test_dual_kpis_mirror_under_reflection():
    """Reflect the tape about a constant and every upper-band KPI must come back
    as its lower-band twin. This is what makes the fade-long's caps trustworthy:
    they read the lower keys, and the lower keys are the upper ones seen in a
    mirror — not a second, subtly different definition."""
    C = 20000.0
    session = lambda p: C + 45 * math.sin(p * 12 * math.pi)  # noqa: E731
    night = lambda p: C + 20 * math.sin(p * 4 * math.pi)     # noqa: E731

    with cache(_rth(session), _overnight(night)):
        up = regmod.compute_regime(SYMBOL, DAY)["checkpoints"]["eod"]
    with cache(_rth(lambda p: 2 * C - session(p)),
               _overnight(lambda p: 2 * C - night(p))):
        down = regmod.compute_regime(SYMBOL, DAY)["checkpoints"]["eod"]

    for upper, lower in (("upper_wrap_occupancy", "lower_wrap_occupancy"),
                         ("upper_dev1_gap_sigma", "lower_dev1_gap_sigma"),
                         ("gx_upper_rescue_ratio", "gx_lower_rescue_ratio")):
        assert up[upper] == down[lower], f"{upper} did not mirror into {lower}"
        assert up[lower] == down[upper], f"{lower} did not mirror into {upper}"

    # The tape has to actually be asymmetric, or the assertions above are vacuous.
    assert up["upper_wrap_occupancy"] != up["lower_wrap_occupancy"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
