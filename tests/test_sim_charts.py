"""Per-bar footprint (volume-at-price) behind the sim charts' volume profile.

The profile is only trustworthy if the footprint conserves volume: every tick
lands in exactly one bar, at exactly one price level. These assert that, since a
mis-mapped tick would silently shift the POC rather than fail loudly.

Each level also carries its signed aggressor delta, which the chart colours the
profile's rows by — so the same conservation has to hold for the sign, and
against the per-bar delta the CVD pane is built from: two readings of one tape
that disagree are worse than one reading.

Run directly:  ``.venv/bin/python tests/test_sim_charts.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from api.session_chart import _per_bar_delta  # noqa: E402
from api.sim_charts import _footprint  # noqa: E402
from journal.sim import bars as barmod  # noqa: E402

TICK = 0.25
PER_BAR = 500


def _synth(n: int, seed: int = 7, sides: str | None = "A") -> pd.DataFrame:
    """Ticks on the real 0.25 grid, deliberately not a whole number of bars.

    ``sides`` is the aggressor tag: one letter for a one-sided tape, None for a
    two-sided one, and ``"N"`` for a feed that tagged nothing.
    """
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "ts_utc": pd.date_range("2025-10-13 13:30", periods=n, freq="100ms", tz="UTC"),
        "price": np.round((20000 + rng.normal(0, 5, n)) * 4) / 4,
        "size": rng.integers(1, 20, n).astype("float64"),
        "side": sides if sides else rng.choice(["B", "A", "N"], n, p=[0.45, 0.45, 0.1]),
    })


def test_footprint_conserves_bar_volume():
    t = _synth(2537)
    b = barmod.tick_bars(t, PER_BAR)
    fp = _footprint(t, b, TICK)

    assert len(fp) == len(b)
    for i in range(len(b)):
        assert sum(row[1] for row in fp[i]) == float(b["volume"].iloc[i])


def test_footprint_prices_stay_inside_their_bar():
    t = _synth(2537)
    b = barmod.tick_bars(t, PER_BAR)
    fp = _footprint(t, b, TICK)

    for i in range(len(b)):
        lo, hi = float(b["low"].iloc[i]), float(b["high"].iloc[i])
        for row in fp[i]:
            assert lo <= row[0] <= hi


def test_footprint_levels_are_unique_and_on_the_tick_grid():
    t = _synth(2537)
    fp = _footprint(t, barmod.tick_bars(t, PER_BAR), TICK)

    for rows in fp:
        prices = [r[0] for r in rows]
        assert len(prices) == len(set(prices))  # one entry per level, not per tick
        for p in prices:
            assert abs(round(p / TICK) - p / TICK) < 1e-9


def test_footprint_delta_agrees_with_the_per_bar_delta():
    """The rows the profile is tinted by and the series the CVD pane accumulates
    are one quantity cut two ways. Summing a bar's levels has to give back that
    bar's delta exactly — a sign convention that drifted between the two would
    paint a profile that argues with the pane under it."""
    t = _synth(2537, sides=None)
    b = barmod.tick_bars(t, PER_BAR)
    fp = _footprint(t, b, TICK)
    per_bar = _per_bar_delta(t, b)

    assert per_bar is not None
    for i in range(len(b)):
        assert sum(row[2] for row in fp[i]) == per_bar[i]
    # And the sign is the app's, not its opposite: a tape of nothing but lifted
    # offers can only come back positive.
    lifted = _synth(600, sides="B")
    buys = _footprint(lifted, barmod.tick_bars(lifted, PER_BAR), TICK)
    assert all(row[2] > 0 for rows in buys for row in rows)


def test_footprint_levels_never_claim_more_delta_than_they_traded():
    """|delta| <= volume per level, which is what makes the tint's saturation a
    share of the row rather than an unbounded ratio."""
    t = _synth(2537, sides=None)
    fp = _footprint(t, barmod.tick_bars(t, PER_BAR), TICK)

    for rows in fp:
        for price, size, delta in rows:
            assert abs(delta) <= size, f"level {price} signed more than it traded"


def test_footprint_omits_delta_when_the_feed_tagged_nothing():
    """An untagged tape has no flow reading, and zeros would be a lie: a level of
    zero delta means the two sides cancelled there, which is a fact this tape
    cannot support. The row is two long instead, and the chart's knob reads the
    length to know the tint has nothing to draw."""
    t = _synth(2537, sides="N")
    fp = _footprint(t, barmod.tick_bars(t, PER_BAR), TICK)

    assert any(rows for rows in fp), "the synth traded nothing"
    for rows in fp:
        for row in rows:
            assert len(row) == 2


def test_footprint_drops_ticks_past_the_last_full_bar():
    """tick_bars emits only complete bars, so the trailing partial bar's ticks
    belong to no drawn candle and must not be folded into the last one."""
    n = 2537
    t = _synth(n)
    b = barmod.tick_bars(t, PER_BAR)
    fp = _footprint(t, b, TICK)

    covered = sum(row[1] for rows in fp for row in rows)
    last_tick = int(b["end_idx"].iloc[-1])
    assert last_tick == len(b) * PER_BAR - 1 < n  # there IS a trailing remainder
    assert covered == float(t["size"].iloc[: last_tick + 1].sum())


# --- the session frame: overnight context without moving the engine's candles ---
#
# Every strategy's chart shows the same layers, including the night. The rule the
# uniformity may not break: the candles an engine traded are the candles drawn.

import tempfile  # noqa: E402
from datetime import date  # noqa: E402

from api.sim_charts import _lead_bars, _session_frame  # noqa: E402
from journal.config import ET_TZ  # noqa: E402
from journal.sim import confluences as confmod  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402
from journal.sim.rules import SimConfig  # noqa: E402

DAY = date(2025, 10, 13)
RTH_OPEN_UTC = pd.Timestamp("2025-10-13 13:30", tz="UTC")
GLOBEX_OPEN_UTC = pd.Timestamp("2025-10-12 22:00", tz="UTC")


class _cache:
    """Serve one synthetic session's two segments through the real tick cache."""

    def __init__(self, n_on: int, n_rth: int):
        self.n_on, self.n_rth = n_on, n_rth

    def _frame(self, start, n, seed):
        rng = np.random.default_rng(seed)
        return pd.DataFrame({
            "ts_utc": pd.date_range(start, periods=n, freq="100ms", tz="UTC"),
            "price": np.round((20000 + rng.normal(0, 5, n)) * 4) / 4,
            "size": rng.integers(1, 20, n).astype("float64"),
            "side": "A",
        })

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = tickmod.TICK_CACHE_DIR
        tickmod.TICK_CACHE_DIR = Path(self._tmp.name)
        tickmod._read_day_parquet.cache_clear()
        tickmod.TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # The overnight segment must END at the bell, so it is stamped backwards
        # from 09:30 — that is what makes "the last night candle runs into the
        # open" a real assertion rather than an artifact of the fixture.
        on_start = RTH_OPEN_UTC - pd.Timedelta(milliseconds=100 * self.n_on)
        self._frame(on_start, self.n_on, 1).to_parquet(
            tickmod._cache_path("TEST", DAY, "on"), index=False)
        self._frame(RTH_OPEN_UTC, self.n_rth, 2).to_parquet(
            tickmod._cache_path("TEST", DAY, "rth"), index=False)
        return SimConfig(contract="TEST", instrument="NQ", ticks_per_bar=PER_BAR)

    def __exit__(self, *exc):
        tickmod.TICK_CACHE_DIR = self._old
        tickmod._read_day_parquet.cache_clear()
        self._tmp.cleanup()


def test_lead_bars_chunk_backwards_into_the_bell():
    """Counting forward from 18:00 would leave up to n-1 ticks in an unclosed
    tail — a hole in the chart right before the open. The remainder is dropped at
    the start of the night instead, so the last overnight candle ends on the tick
    immediately before RTH."""
    with _cache(n_on=1100, n_rth=2000) as cfg:
        on = tickmod.cached_overnight(cfg.contract, DAY)
        lead = _lead_bars(on, PER_BAR)

    assert len(lead) == 2  # 1100 // 500, the 100-tick remainder dropped at 18:00
    assert int(lead["end_idx"].iloc[-1]) == len(on) - 1, "the night does not reach the bell"
    assert int(lead["start_idx"].iloc[0]) == 1100 % PER_BAR


def test_session_chart_draws_the_night_without_moving_the_engines_candles():
    """The whole point of building the two legs separately: a session strategy's
    RTH candles must stay bit-for-bit the ones its engine closed on. One
    continuous 18:00 stream would shift every boundary by the night's remainder.
    """
    with _cache(n_on=1100, n_rth=2000) as cfg:
        t = tickmod.get_day_ticks(cfg.contract, DAY)   # the engine's own ticks
        eng = barmod.tick_bars(t, PER_BAR)             # the engine's own bars
        f = _session_frame(cfg, DAY, ET_TZ, overnight=False)
    bars, gx, ny, wk = f.bars, f.vwap_globex, f.vwap_ny, f.vwap_weekly
    prof_gx, prof_ny, ib, fp = f.profile_globex, f.profile_ny, f.ib, f.footprint
    divs = f.cvd_divergences

    lead = len(bars) - len(eng)
    assert lead == 2 and len(eng) == 4, (lead, len(eng))
    for k in range(len(eng)):
        drawn = bars[lead + k]
        assert (drawn["open"], drawn["high"], drawn["low"], drawn["close"]) == (
            eng["open"][k], eng["high"][k], eng["low"][k], eng["close"][k]
        ), f"engine candle {k} was redrawn with different boundaries"

    # Each anchor starting where it is anchored.
    assert len(gx) == len(bars), "the Globex VWAP must span the night too"
    assert len(ny) == len(eng), "the NY VWAP cannot start before the bell"
    # DAY is a Monday: the weekly anchor IS this session's Globex open, so the
    # weekly line exists, spans the night, and coincides with the Globex one.
    assert wk == gx, "on the week's first session the weekly anchor is the Globex anchor"
    # Two developing value areas, mirroring the two VWAP anchors: the Globex one
    # spans the night, the NY one cannot start before the bell.
    assert len(prof_gx) == len(bars), "the Globex value area must span the night too"
    assert len(prof_ny) == len(eng), "the NY value area cannot start before the bell"
    # The footprint is binned over every drawn candle, night included.
    assert len(fp) == len(bars)
    # The fixture's RTH is ~3 minutes of ticks — the IB window (60 min) never
    # completes, so the overlay is absent rather than a made-up IB.
    assert ib is None, "an IB was drawn for a session whose data ends inside the window"
    # CVD divergences ride the same frame as marks to fold into: always a list,
    # never draws a mark on a session the CVD series couldn't be built for.
    assert isinstance(divs, list)
    assert f.cvd or not divs, "divergences without a CVD series to diverge from"


def test_profile_is_drawn_even_when_no_rule_read_it():
    """Uniform layers: the value area is on every chart. Whether a rule was
    looking at it is the run's config, not the picture."""
    with _cache(n_on=1000, n_rth=2000) as cfg:
        assert not confmod.needs_profile(cfg), "the fixture must be a run that ignores the profile"
        f = _session_frame(cfg, DAY, ET_TZ, overnight=False)
    assert f.profile_globex and f.profile_ny, \
        "a value area was withheld from a run that did not read it"


def test_session_chart_survives_a_missing_night():
    """An RTH window whose overnight was never bought: the night is absent, not
    fetched, and the chart is still the engine's own session."""
    with _cache(n_on=1100, n_rth=2000) as cfg:
        (tickmod.TICK_CACHE_DIR / f"TEST_{DAY.isoformat()}_on.parquet").unlink()
        tickmod._read_day_parquet.cache_clear()
        t = tickmod.get_day_ticks(cfg.contract, DAY)
        eng = barmod.tick_bars(t, PER_BAR)
        f = _session_frame(cfg, DAY, ET_TZ, overnight=False)
    bars, gx, ny, wk = f.bars, f.vwap_globex, f.vwap_ny, f.vwap_weekly
    prof_gx, prof_ny = f.profile_globex, f.profile_ny

    assert len(bars) == len(eng)
    assert gx == [], "no night on disk, so there is no Globex anchor to draw"
    assert wk == [], "no night on disk, so no weekly anchor either — absent, not approximated"
    assert prof_gx == [], "no night on disk, so there is no Globex value area either"
    assert len(ny) == len(eng)
    assert len(prof_ny) == len(eng)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
