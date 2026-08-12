"""The replay day list, once it spans both tick stores.

Decision 4 of docs/live-shadow-plan.md used to keep recorded days out of
``/simulator/days`` entirely, which made the endpoint's correctness a matter of
one glob. Now that both stores are listed, three things can go wrong quietly and
none of them raises:

  - **the wrong store wins.** A live day that beat a cached one would make the
    Simulator and the live chart draw different bars for the same Tuesday, and
    would put a Rithmic clock (~287µs off Databento's) under a session the
    backtests read from the corpus.
  - **a fragment lists as a session.** A day still being recorded, or one whose
    tape starts after the bell, plays a tape that stops without saying so.
  - **a half day lists as a fragment.** The inverse, and the more tempting bug:
    2026-06-19 and 2026-07-03 close at 13:00 ET, and any rule derived from "does
    the tape reach 16:00" throws away real sessions. ``journal.live.harvest``
    documents why the timestamps cannot answer this and the manifest must.

The session endpoint is exercised too, but only to pin the source tag to the
frame it describes — the tape encoding has its own suites.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from api.routers import simulator as simmod  # noqa: E402
from journal.live import journal as jourmod  # noqa: E402
from journal.live.recorder import TickRecorder  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

DAY = date(2025, 10, 13)
SYM = "NQZ5"


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Both tick stores, empty, in a temp dir."""
    monkeypatch.setattr(tickmod, "LIVE_TICK_DIR", tmp_path / "live")
    monkeypatch.setattr(tickmod, "TICK_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(jourmod, "LIVE_SIGNAL_DIR", tmp_path / "signals")
    (tmp_path / "cache").mkdir()
    tickmod._clear_tick_caches()
    yield tmp_path
    tickmod._clear_tick_caches()


def _ticks(lo: pd.Timestamp, hi: pd.Timestamp, freq: str = "1min") -> pd.DataFrame:
    ts = pd.date_range(lo, hi, freq=freq, inclusive="left", tz="UTC")
    n = len(ts)
    return pd.DataFrame({
        "ts_utc": ts,
        "price": 20000.0 + np.arange(n) * 0.25,
        "size": np.ones(n, dtype="uint32"),
        "side": np.where(np.arange(n) % 2 == 0, "B", "A"),
        "agg_raw": np.where(np.arange(n) % 2 == 0, 1, 2).astype("int16"),
    })


def _whole_day(day: date) -> pd.DataFrame:
    """Prev 18:00 ET -> 18:00 ET: night, RTH and the post hour."""
    return _ticks(tickmod.overnight_bounds_utc(day)[0], tickmod.post_bounds_utc(day)[1])


def _record(symbol: str, day: date, df: pd.DataFrame, close: bool = True) -> None:
    r = TickRecorder(symbol, day)
    for i in range(0, len(df), 37):
        r.append(df.iloc[i:i + 37])
    if close:
        r.close(df["ts_utc"].iloc[-1])
    else:
        r.heartbeat(df["ts_utc"].iloc[-1])
    tickmod._clear_tick_caches()


def _cache_day(symbol: str, day: date, df: pd.DataFrame) -> None:
    """Write the whole-day layout the fetcher writes now."""
    df.to_parquet(tickmod.TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_day.parquet",
                  index=False)
    tickmod._clear_tick_caches()


def _entry(days: list[dict], symbol: str, day: date) -> dict | None:
    hits = [d for d in days if d["symbol"] == symbol and d["date"] == day.isoformat()]
    assert len(hits) <= 1, f"{symbol} {day} listed {len(hits)} times"
    return hits[0] if hits else None


# --- what gets listed -------------------------------------------------------


def test_a_recorded_day_is_replayable_and_says_which_store_it_came_from(stores):
    _record(SYM, DAY, _whole_day(DAY))
    d = _entry(simmod.simulator_days(root=None)["days"], SYM, DAY)
    assert d is not None, "a whole recorded session should be replayable"
    assert d["source"] == "live"
    assert d["has_overnight"] and d["has_post"]
    assert not d["ends_early"]


def test_a_cached_day_still_says_cache(stores):
    _cache_day(SYM, DAY, _whole_day(DAY))
    d = _entry(simmod.simulator_days(root=None)["days"], SYM, DAY)
    assert d["source"] == "cache"
    assert d["has_overnight"] and d["has_post"]


def test_a_day_in_both_stores_is_listed_once_and_the_cache_wins(stores):
    """The precedence ``cached_rth`` reads in, visible in the listing.

    If this ever inverts, the corpus the live day is reconciled *against* starts
    being partly made of the live day.
    """
    _cache_day(SYM, DAY, _whole_day(DAY))
    _record(SYM, DAY, _whole_day(DAY))
    days = simmod.simulator_days(root=None)["days"]
    assert len([x for x in days if x["date"] == DAY.isoformat()]) == 1
    assert _entry(days, SYM, DAY)["source"] == "cache"


def test_the_same_date_under_two_contracts_is_two_days(stores):
    """A roll straddle is not a collision. The live store pins one contract and
    the cache follows the front month, so around a roll the two hold genuinely
    different series for one date — and both are replayable."""
    _cache_day("NQM6", DAY, _whole_day(DAY))
    _record("NQU6", DAY, _whole_day(DAY))
    days = simmod.simulator_days(root=None)["days"]
    assert _entry(days, "NQM6", DAY)["source"] == "cache"
    assert _entry(days, "NQU6", DAY)["source"] == "live"


# --- what does not ----------------------------------------------------------


def test_a_session_still_being_recorded_is_not_offered(stores):
    """The tape covers the bell, but the day is not settled — replaying it would
    stop wherever the recorder has got to, with nothing saying so."""
    df = _whole_day(DAY)
    cut = df[df["ts_utc"] < tickmod.session_bounds_utc(DAY)[1]]
    _record(SYM, DAY, cut, close=False)
    assert _entry(simmod.simulator_days(root=None)["days"], SYM, DAY) is None


def test_a_tape_that_starts_after_the_bell_is_not_a_session(stores):
    late = tickmod.session_bounds_utc(DAY)[0] + pd.Timedelta(hours=1)
    _record(SYM, DAY, _ticks(late, tickmod.post_bounds_utc(DAY)[1]))
    assert _entry(simmod.simulator_days(root=None)["days"], SYM, DAY) is None


def test_a_night_only_recording_is_not_a_session(stores):
    _record(SYM, DAY, _ticks(*tickmod.overnight_bounds_utc(DAY)))
    assert _entry(simmod.simulator_days(root=None)["days"], SYM, DAY) is None


# --- the half-day trap ------------------------------------------------------


def test_a_settled_half_day_is_replayable_and_marked_short(stores):
    """The bug this is here to stop: 13:00 ET closes are real sessions, and a
    tape-derived "did it reach 16:00" rule discards them. Settled says keep it;
    the span says say so."""
    early_close = tickmod.session_bounds_utc(DAY)[0] + pd.Timedelta(hours=3, minutes=30)
    _record(SYM, DAY, _ticks(tickmod.overnight_bounds_utc(DAY)[0], early_close))
    d = _entry(simmod.simulator_days(root=None)["days"], SYM, DAY)
    assert d is not None, "a settled half day is a session, not a fragment"
    assert d["ends_early"] is True
    assert d["has_post"] is False


def test_an_ordinary_rth_only_tape_is_not_called_early(stores):
    """The last RTH print lands microseconds before 16:00, so a bare
    ``hi < close`` marks every ordinary day short. The slack is what stops it."""
    rth_open, rth_close = tickmod.session_bounds_utc(DAY)
    _record(SYM, DAY, _ticks(rth_open, rth_close, freq="1s"))
    d = _entry(simmod.simulator_days(root=None)["days"], SYM, DAY)
    assert d is not None
    assert d["ends_early"] is False
    assert d["has_overnight"] is False


# --- the span -------------------------------------------------------------


def test_the_span_folds_over_every_chunk_not_the_first_and_last(stores):
    """Chunk names sort in *write* order, and backfill breaks the match with
    time order: connect at 07:00, then a reconnect replays the night behind it.
    Reading only the endpoints would report a day starting at 07:00."""
    late = tickmod.session_bounds_utc(DAY)[0] - pd.Timedelta(hours=2)
    r = TickRecorder(SYM, DAY)
    r.append(_ticks(late, tickmod.post_bounds_utc(DAY)[1]))       # written first
    r.append(_ticks(*tickmod.overnight_bounds_utc(DAY)))          # the night, after
    r.close(tickmod.post_bounds_utc(DAY)[1])
    tickmod._clear_tick_caches()

    lo, hi = tickmod.live_day_span(SYM, DAY)
    assert lo == tickmod.overnight_bounds_utc(DAY)[0]
    assert _entry(simmod.simulator_days(root=None)["days"], SYM, DAY)["has_overnight"] is True


def test_the_span_reflects_a_day_that_grew(stores):
    """Cached on the chunk set, so an appended chunk invalidates its own entry
    — the failure the segment LRU and the sums file were both designed against."""
    rth_open, rth_close = tickmod.session_bounds_utc(DAY)
    r = TickRecorder(SYM, DAY)
    r.append(_ticks(tickmod.overnight_bounds_utc(DAY)[0], rth_close))
    assert tickmod.live_day_span(SYM, DAY)[1] < rth_close
    r.append(_ticks(rth_close, tickmod.post_bounds_utc(DAY)[1]))
    r.close(tickmod.post_bounds_utc(DAY)[1])
    assert tickmod.live_day_span(SYM, DAY)[1] >= tickmod.post_bounds_utc(DAY)[0]


def test_no_recording_has_no_span(stores):
    assert tickmod.live_day_span(SYM, DAY) is None


# --- the session endpoint ---------------------------------------------------


def test_a_recorded_session_serves_and_is_tagged_live(stores):
    _record(SYM, DAY, _whole_day(DAY))
    s = simmod.simulator_session(symbol=SYM, date_=DAY.isoformat(), tz="UTC")
    assert s["source"] == "live"
    assert s["n"] == len(_whole_day(DAY))
    assert s["has_overnight"] and s["has_post"]


def test_the_source_tag_follows_the_frame_not_the_calendar(stores):
    """Cached day, recorded day, same request shape — the tag is whichever store
    ``cached_rth`` actually read, so it cannot drift from the ticks shipped."""
    other = DAY + timedelta(days=1)
    _cache_day(SYM, DAY, _whole_day(DAY))
    _record(SYM, other, _whole_day(other))
    assert simmod.simulator_session(symbol=SYM, date_=DAY.isoformat(), tz="UTC")["source"] == "cache"
    assert simmod.simulator_session(symbol=SYM, date_=other.isoformat(), tz="UTC")["source"] == "live"


def test_the_globex_open_ships_beside_the_anchor(stores):
    """A night the backfill did not reach in full anchors the band late and
    draws exactly like a correct one. Both numbers, or it is invisible."""
    _record(SYM, DAY, _whole_day(DAY))
    whole = simmod.simulator_session(symbol=SYM, date_=DAY.isoformat(), tz="UTC")
    assert whole["globex_anchor_ms"] == whole["globex_open_ms"]

    tickmod._clear_tick_caches()
    short_night = tickmod.overnight_bounds_utc(DAY)[0] + pd.Timedelta(hours=4)
    _record(SYM, DAY + timedelta(days=1), _ticks(
        short_night + pd.Timedelta(days=1), tickmod.post_bounds_utc(DAY + timedelta(days=1))[1]))
    late = simmod.simulator_session(
        symbol=SYM, date_=(DAY + timedelta(days=1)).isoformat(), tz="UTC")
    assert late["globex_anchor_ms"] > late["globex_open_ms"]
