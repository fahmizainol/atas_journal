"""Phases 5-6: the recorder, the live store's readers, and the reconciliation.

What these guard is the half of live mode that fails *quietly*. The surface is
watched in a browser and the prefix property has its own suite; what nothing
would otherwise notice is:

  - a recorded day that reads back as different ticks than were written, or that
    slices into the wrong windows — every number downstream moves, with no error;
  - the two tick stores stopping being disjoint. A live day that answered
    ``day_complete`` would short-circuit a Databento backfill; a live day that
    won over a cached one would make the reference Phase 6 reconciles against
    partly made of the thing it is checking;
  - the ten ``gx_*`` gate sites finding nothing behind ``cached_overnight`` on a
    live day, which makes every Globex strategy veto everything and say nothing
    about why — the plan's reason ticks-on-disk could not be cut;
  - a session that never rolls at 18:00 ET, so an always-on host accumulates one
    endless day;
  - and a reconciliation that reports agreement it cannot attribute.

The heavy end (a real tape, real strategies) runs over a cached Databento
session re-recorded into a temporary live store, and skips if the cache is cold.
"""

from __future__ import annotations

import asyncio
import time
import sys
from collections import Counter, deque
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.live import harvest  # noqa: E402
from journal.live import journal as jourmod  # noqa: E402
from journal.live import reconcile as recmod  # noqa: E402
from journal.live import recorder as recorder_mod  # noqa: E402
from journal.live import rithmic as rith  # noqa: E402
from journal.live.recorder import TickRecorder  # noqa: E402
from journal.live.rithmic import RithmicFeed  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

DAY = date(2025, 10, 13)
CONTRACT = "NQZ5"

needs_ticks = pytest.mark.skipif(not tickmod.has_rth(CONTRACT, DAY),
                                 reason="tick cache is cold")


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """The replay waits a second before asking an empty window again.

    Right in production — an empty answer wants to be a different moment before
    it is believed — and pure cost here, where every stub is deterministic. Left
    in, it took this file from 50s to over ten minutes.
    """
    monkeypatch.setattr(rith, "EMPTY_RETRY_S", 0)


@pytest.fixture
def live_store(tmp_path, monkeypatch):
    """Point the live store at a temp dir, and clear the reads keyed on it."""
    monkeypatch.setattr(tickmod, "LIVE_TICK_DIR", tmp_path / "ticks")
    monkeypatch.setattr(jourmod, "LIVE_SIGNAL_DIR", tmp_path / "signals")
    tickmod._clear_tick_caches()
    yield tmp_path
    tickmod._clear_tick_caches()


def _synthetic_day(day: date, freq: str = "1min") -> pd.DataFrame:
    """A tick per interval across the whole session, prev 18:00 -> 18:00 ET."""
    lo = tickmod.overnight_bounds_utc(day)[0]
    hi = tickmod.post_bounds_utc(day)[1]
    ts = pd.date_range(lo, hi, freq=freq, inclusive="left", tz="UTC")
    n = len(ts)
    return pd.DataFrame({
        "ts_utc": ts,
        "price": 20000.0 + np.arange(n) * 0.25,
        "size": np.ones(n, dtype="uint32"),
        "side": np.where(np.arange(n) % 2 == 0, "B", "A"),
        "agg_raw": np.where(np.arange(n) % 2 == 0, 1, 2).astype("int16"),
    })


def _record(symbol: str, day: date, df: pd.DataFrame, batch: int = 37) -> TickRecorder:
    r = TickRecorder(symbol, day)
    for i in range(0, len(df), batch):
        r.append(df.iloc[i:i + batch])
    r.close(df["ts_utc"].iloc[-1])
    tickmod._clear_tick_caches()
    return r


# --- the store --------------------------------------------------------------


def test_a_recorded_day_reads_back_tick_for_tick(live_store):
    df = _synthetic_day(DAY)
    _record("TEST", DAY, df)
    back = tickmod.live_day_ticks("TEST", DAY)
    assert len(back) == len(df)
    assert back["ts_utc"].tolist() == df["ts_utc"].tolist()
    assert back["price"].tolist() == df["price"].tolist()
    assert back["side"].tolist() == df["side"].tolist()


def test_the_windows_are_the_ones_get_day_ticks_returns(live_store):
    """The READ CONTRACT holds for a recorded day exactly as for a bought one.

    A frame that quietly carried the overnight in front of an RTH strategy would
    re-phase every tick bar and move the VWAP anchor to 18:00 — silently.
    """
    _record("TEST", DAY, _synthetic_day(DAY))
    rth_open, rth_close = tickmod.session_bounds_utc(DAY)
    gx_open = tickmod.overnight_bounds_utc(DAY)[0]

    rth = tickmod.cached_rth("TEST", DAY)
    assert rth["ts_utc"].min() >= rth_open and rth["ts_utc"].max() < rth_close

    on = tickmod.cached_overnight("TEST", DAY)
    assert on["ts_utc"].min() >= gx_open and on["ts_utc"].max() < rth_open

    post = tickmod.cached_post("TEST", DAY)
    assert post["ts_utc"].min() >= rth_close

    assert len(rth) + len(on) + len(post) == len(tickmod.live_day_ticks("TEST", DAY))


def test_the_gate_read_finds_the_night_on_a_live_day(live_store):
    """``cached_overnight`` is what ten ``gx_*`` gate sites call.

    They blind-fail-closed on a missing night, so this returning None on a
    recorded day is not a degraded shadow mode — it is one where seven
    strategies never signal and nothing says why.
    """
    _record("TEST", DAY, _synthetic_day(DAY))
    on = tickmod.cached_overnight("TEST", DAY)
    assert on is not None and not on.empty


def test_the_two_stores_stay_disjoint(live_store):
    """A recorded day must be invisible to every Databento-side question.

    ``day_complete`` deciding a live day is settled would short-circuit the
    backfill for that date, and ``has_rth`` is the runner's broken-window guard.
    """
    _record("TEST", DAY, _synthetic_day(DAY))
    assert tickmod.day_complete("TEST", DAY) is False
    assert tickmod.has_rth("TEST", DAY) is False
    assert tickmod.have_segment("TEST", DAY, "rth") is False


@needs_ticks
def test_a_bought_day_wins_over_a_recorded_one(live_store):
    """Databento first, always.

    This direction is what keeps the reference independent: recording a session
    can never change what a backtest over that session says, and Phase 6 is not
    comparing a tape against itself.
    """
    fake = _synthetic_day(DAY)
    _record(CONTRACT, DAY, fake)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    assert len(rth) != len(fake)  # the cached day, not the 1-per-minute forgery
    assert len(rth) == len(tickmod._read_segment_cached(CONTRACT, DAY, "rth"))


def test_a_growing_day_invalidates_its_own_read(live_store):
    """The read is keyed on the chunk set, so nobody has to remember to clear it.

    The failure this replaces is the one the segment LRU and the sums file both
    had to be designed against: a cached read serving a prefix of a file that has
    since grown.
    """
    df = _synthetic_day(DAY)
    r = TickRecorder("TEST", DAY)
    r.append(df.iloc[:500])
    r.flush()
    tickmod._clear_tick_caches()
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 500
    r.append(df.iloc[500:900])
    r.flush()
    # No cache clear here — that is the point.
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 900


def test_a_day_reads_back_in_time_order_however_it_was_written(live_store):
    """Write order stopped being time order when the feed learned to backfill.

    Connect at 07:08 and those prints are the first chunk; reconnect and the
    replayed night from 18:00 lands in a later one. Concatenating the glob then
    gives a tape that jumps backwards — which `LiveSession.append` trusts, the
    engine searchsorts, and every tick bar is phased by. Nothing would raise.
    """
    df = _synthetic_day(DAY)
    late, night = df.iloc[800:900], df.iloc[:800]
    r = TickRecorder("TEST", DAY)
    r.append(late)                      # what an earlier connect recorded
    r.flush()
    r.append(night)                     # the backfill, written afterwards
    r.close(night["ts_utc"].iloc[-1])

    back = tickmod.live_day_ticks("TEST", DAY)
    assert len(back) == 900
    assert back["ts_utc"].is_monotonic_increasing
    assert back["ts_utc"].tolist() == df.iloc[:900]["ts_utc"].tolist()


def test_a_resumed_recorder_continues_rather_than_overwriting(live_store):
    """A restart must not reuse a chunk index — those ticks are gone for good."""
    df = _synthetic_day(DAY)
    r1 = TickRecorder("TEST", DAY)
    r1.append(df.iloc[:400])
    r1.close(df["ts_utc"].iloc[399])
    tickmod._clear_tick_caches()

    r2 = TickRecorder("TEST", DAY)
    assert r2.rows == 400  # counted from the parquet footers, not by re-reading
    r2.append(df.iloc[400:800])
    r2.close(df["ts_utc"].iloc[799])
    tickmod._clear_tick_caches()

    back = tickmod.live_day_ticks("TEST", DAY)
    assert len(back) == 800
    assert back["ts_utc"].is_monotonic_increasing


def test_the_night_is_sealed_before_rth_opens(live_store):
    """A batch crossing a window boundary seals the old window first.

    Without it the night would be short by up to one seal interval at the one
    moment the ``gx_*`` gates ask for it.
    """
    df = _synthetic_day(DAY)
    rth_open = tickmod.session_bounds_utc(DAY)[0]
    night = df[df["ts_utc"] < rth_open]
    r = TickRecorder("TEST", DAY)
    r.append(night)                     # buffered, not necessarily sealed
    r.append(df[df["ts_utc"] >= rth_open].iloc[:5])   # crosses -> seals the night
    tickmod._clear_tick_caches()
    on = tickmod.live_segment("TEST", DAY, "on")
    assert on is not None and len(on) == len(night)


def test_recorded_days_lists_only_what_has_chunks(live_store):
    (tickmod.LIVE_TICK_DIR / "TEST" / "2025-01-01").mkdir(parents=True)
    _record("TEST", DAY, _synthetic_day(DAY))
    assert recorder_mod.recorded_days() == [("TEST", DAY)]
    assert recorder_mod.read_manifest("TEST", DAY)["closed"] is True


# --- the session clock ------------------------------------------------------


@pytest.mark.parametrize("et,expected", [
    ("2025-10-13 09:30", date(2025, 10, 13)),   # inside RTH
    ("2025-10-13 17:30", date(2025, 10, 13)),   # the halt still belongs to today
    ("2025-10-13 18:00", date(2025, 10, 14)),   # the reopen is tomorrow's night
    ("2025-10-17 19:00", date(2025, 10, 20)),   # Friday evening -> Monday
    ("2025-10-19 18:30", date(2025, 10, 20)),   # Sunday evening -> Monday
])
def test_the_session_rolls_at_1800_et(et, expected):
    """The roll is decided by the tick clock, never the wall clock.

    A host whose clock has drifted still cuts the day where the exchange does —
    and this is the same boundary ``overnight_bounds_utc`` already assumes when
    it keys Monday's night to Sunday.
    """
    ts = pd.Timestamp(et, tz="America/New_York").tz_convert("UTC")
    assert tickmod.session_date_for(ts) == expected


# --- the router and the roll ------------------------------------------------


@pytest.fixture
def no_session(live_store, monkeypatch):
    """Run the router against a clean module state and tear it down after.

    ``state`` holds one process-wide session by design; a test that left one
    running would leak a shadow thread into every test after it.
    """
    from journal.live import state as statemod

    monkeypatch.setattr(statemod, "_current", None, raising=False)
    # The shadow runner spawns a thread and re-runs the whole shelf. What is
    # under test here is the routing, so it is stubbed out — the runner has its
    # own suite.
    monkeypatch.setattr(statemod, "ShadowRunner",
                        lambda session, journal=None, enabled=True:
                        _StubRunner(journal, enabled))
    yield statemod
    if statemod.current() is not None:
        statemod.stop()


class _StubRunner:
    def __init__(self, journal=None, enabled=True):
        self.journal = journal
        self.enabled = enabled
        self.started = False

    def start(self):
        if self.enabled:
            self.started = True

    def stop(self):
        self.started = False

    def set_enabled(self, on):
        self.enabled = bool(on)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def set_journal(self, journal):
        self.journal = journal


def test_the_router_opens_a_session_and_records_it(no_session):
    df = _synthetic_day(DAY).iloc[:200]
    no_session._Router("TEST")(df)
    live = no_session.current()
    assert live is not None
    assert live.session.day == DAY and live.session.n == 200
    assert live.recorder is not None
    live.recorder.flush()
    tickmod._clear_tick_caches()
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 200


def test_a_batch_straddling_1800_et_splits_across_two_sessions(no_session):
    """An always-on host crosses this boundary every night.

    Grouping the batch rather than taking its first tick's date is what keeps the
    two sides of 18:00 out of each other's tapes — a session that swallowed the
    next day's opening prints would anchor its Globex VWAP on the wrong night.
    """
    reopen = tickmod.post_bounds_utc(DAY)[1]          # 18:00 ET on the session date
    ts = pd.date_range(reopen - pd.Timedelta(seconds=2), periods=5, freq="1s",
                       tz="UTC")
    frame = pd.DataFrame({"ts_utc": ts, "price": [1.0] * 5,
                          "size": np.ones(5, dtype="uint32"), "side": ["B"] * 5})
    no_session._Router("TEST")(frame)

    live = no_session.current()
    next_day = tickmod.session_date_for(reopen)
    assert live.session.day == next_day
    assert live.session.n == 3          # 18:00:00, :01, :02
    live.recorder.flush()
    tickmod._clear_tick_caches()
    # The two ticks before the reopen stayed on the day they belonged to, and
    # were sealed when that session was closed out by the roll.
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 2
    assert len(tickmod.live_day_ticks("TEST", next_day)) == 3


def test_rows_replayed_off_disk_reach_the_tape_and_not_the_recorder(no_session):
    """The feed republishes a recorded stretch so the tape is assembled in time
    order. The recorder appends whatever it is handed, so writing those rows
    again would duplicate them into a later chunk and the day would read back
    out of order."""
    df = _synthetic_day(DAY).iloc[:200]
    router = no_session._Router("TEST")
    router(df.iloc[:100])
    router(df.iloc[100:], record=False)

    live = no_session.current()
    assert live.session.n == 200          # the tape has all of it
    live.recorder.flush()
    tickmod._clear_tick_caches()
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 100   # disk has half


def test_a_tick_for_a_closed_day_is_dropped_not_misfiled(no_session):
    """Neither home for it is sound.

    Resurrecting the old session hands the client a tape that jumps backwards;
    putting it on the current one breaks the ordering the engine searchsorts the
    RTH boundary with — and nothing raises on an out-of-order tick, it just
    re-phases bars.
    """
    router = no_session._Router("TEST")
    reopen = tickmod.post_bounds_utc(DAY)[1]
    router(pd.DataFrame({"ts_utc": [reopen], "price": [1.0],
                         "size": np.ones(1, dtype="uint32"), "side": ["B"]}))
    rolled = no_session.current().session.day
    router(pd.DataFrame({"ts_utc": [reopen - pd.Timedelta(hours=3)], "price": [1.0],
                         "size": np.ones(1, dtype="uint32"), "side": ["B"]}))
    assert no_session.current().session.day == rolled
    assert no_session.current().session.n == 1
    assert router.dropped == 1


def test_a_restart_resumes_the_day_from_disk(no_session, monkeypatch):
    """Without this a process that came back at eleven would hold a tape that
    began at eleven, and every strategy would be simulating a session that opened
    two hours late — silently, with plausible numbers."""
    df = _synthetic_day(DAY).iloc[:600]
    _record("TEST", DAY, df)
    monkeypatch.setattr(tickmod, "session_date_for", lambda ts: DAY)

    live = no_session.resume()
    assert live is not None
    assert live.session.n == 600
    assert live.session.day == DAY
    assert live.feed is None          # resumed, not reconnected


def test_resume_picks_the_freshest_heartbeat(no_session, monkeypatch):
    """With two contracts recorded today, take the one that was still writing.

    Alphabetical order is not an answer to "which session was running", and a
    host that records a contract deliberately should be able to say so —
    ``LIVE_SYMBOL`` wins outright when it is set.
    """
    df = _synthetic_day(DAY)
    _record("AAA", DAY, df.iloc[:100])
    _record("ZZZ", DAY, df.iloc[:200])
    recorder_mod.TickRecorder("AAA", DAY).heartbeat(None)   # AAA beats more recently
    monkeypatch.setattr(tickmod, "session_date_for", lambda ts: DAY)

    assert no_session.resume().session.symbol == "AAA"
    no_session.stop()
    assert no_session.resume(symbol="ZZZ").session.symbol == "ZZZ"


def test_resume_ignores_a_finished_day(no_session, monkeypatch):
    """A recorded day that is not today is inert (decision 4) — it exists to be
    reconciled, not to be re-opened as a live session."""
    _record("TEST", DAY, _synthetic_day(DAY).iloc[:100])
    monkeypatch.setattr(tickmod, "session_date_for",
                        lambda ts: date(2025, 10, 20))
    assert no_session.resume() is None


# --- the two modes ----------------------------------------------------------
#
# Recording and the shadow shelf are separate switches because they fail
# differently. Recording off is a visible absence; the shelf running with
# nothing on disk behind it is not an absence at all — the `gx_*` gates read the
# session's earlier windows off disk and blind-fail-closed, so seven of the
# thirteen strategies veto everything and say nothing about why. That is a
# plausible wrong answer, which is the one outcome this stack refuses to serve.


def test_the_shelf_may_not_run_over_a_live_feed_that_writes_nothing(no_session):
    df = _synthetic_day(DAY).iloc[:50]
    no_session._Router("TEST")(df)
    with pytest.raises(ValueError) as e:
        no_session.set_modes(record=False)
    assert "off disk" in str(e.value)
    # Refused means unchanged, not half-applied.
    live = no_session.current()
    assert live.recording and live.signals


def test_the_shelf_and_the_recording_may_both_be_switched_off(no_session):
    no_session._Router("TEST")(_synthetic_day(DAY).iloc[:50])
    live = no_session.set_modes(record=False, signals=False)
    assert not live.recording and not live.signals
    assert live.shadow.journal is None


def test_switching_the_shelf_off_alone_is_allowed(no_session):
    """Recording is the half that could not be cut; the runner is the half that
    could (docs/live-shadow-plan.md decision 7)."""
    no_session._Router("TEST")(_synthetic_day(DAY).iloc[:50])
    live = no_session.set_modes(signals=False)
    assert live.recording and not live.signals
    # The journal follows the recording *and* the shelf: nothing is being said,
    # so there is nothing to write down.
    assert live.shadow.journal is None


def test_ticks_that_arrive_unrecorded_are_counted_and_land_in_the_manifest(no_session):
    """A day recorded in two halves has a hole in the middle.

    The tape is in memory and the chunks are what survive the process, so prints
    that arrived while recording was off are gone at the next restart. A manifest
    that read as complete would be the worst possible outcome — it is the file a
    reader consults precisely to find out whether the day is whole.
    """
    df = _synthetic_day(DAY).iloc[:300]
    router = no_session._Router("TEST")
    router(df.iloc[:100])
    no_session.set_modes(record=False, signals=False)
    router(df.iloc[100:200])
    assert no_session.current().unrecorded == 100

    no_session.set_modes(record=True)
    router(df.iloc[200:])
    live = no_session.current()
    live.recorder.close(live.session.last_ts())
    tickmod._clear_tick_caches()

    # 200 on disk, 300 on the tape, and the manifest says which is which.
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 200
    assert live.session.n == 300
    man = recorder_mod.read_manifest("TEST", DAY)
    assert man["stats"]["unrecorded_rows"] == 100
    assert man["shadow"] == "off"


def test_switching_recording_back_on_continues_the_chunk_numbering(no_session):
    """Not a restart: re-using an index would overwrite ticks that are gone."""
    df = _synthetic_day(DAY).iloc[:300]
    router = no_session._Router("TEST")
    router(df.iloc[:100])
    no_session.current().recorder.flush()
    before = set(tickmod.live_chunks("TEST", DAY))
    no_session.set_modes(record=False, signals=False)
    no_session.set_modes(record=True)
    router(df.iloc[200:])
    no_session.current().recorder.flush()
    after = set(tickmod.live_chunks("TEST", DAY))
    assert before < after                    # nothing was replaced
    tickmod._clear_tick_caches()
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 200


def test_the_modes_survive_the_1800_roll(no_session):
    """They are a decision about the run, not about the day.

    A recorder that came back at midnight because the session turned over would
    be the switch undoing itself at the one hour nobody is watching.
    """
    no_session._Router("TEST")(_synthetic_day(DAY).iloc[:50])
    no_session.set_modes(record=False, signals=False)
    router = no_session._Router("TEST")
    reopen = tickmod.post_bounds_utc(DAY)[1]
    router(pd.DataFrame({"ts_utc": [reopen], "price": [1.0],
                         "size": np.ones(1, dtype="uint32"), "side": ["B"]}))
    live = no_session.current()
    assert live.session.day == tickmod.session_date_for(reopen)
    assert not live.recording and not live.signals


def test_a_rithmic_session_with_recording_off_is_not_called_a_fake_feed(no_session):
    """`source` used to be derived from `record`, which was true only while the
    two were the same switch. The banner exists so this surface cannot be
    mistaken for something it is not; it must not be the thing that lies."""
    no_session._Router("TEST")(_synthetic_day(DAY).iloc[:50])
    live = no_session.set_modes(record=False, signals=False)
    assert live.source == "rithmic"


def test_the_simulated_feed_refuses_to_record(no_session):
    """Its source is a cached day: recording it would manufacture a live day out
    of a replayed one, which is the independence Phase 6's reference rests on."""
    with pytest.raises(ValueError) as e:
        no_session.check_modes("fake", record=True, signals=True)
    assert "cached day" in str(e.value)
    # ...and running the shelf over it with nothing recorded is fine, because the
    # windows the gates read are already on disk.
    no_session.check_modes("fake", record=False, signals=True)


def test_a_restart_restores_the_shelf_switch(no_session, monkeypatch):
    """A restart is not a decision. A process that re-armed the runner because it
    happened to bounce would change how the day is watched without anyone
    choosing it."""
    df = _synthetic_day(DAY).iloc[:100]
    rec = _record("TEST", DAY, df)
    rec.marks["shadow"] = "off"
    rec.heartbeat(df["ts_utc"].iloc[-1])
    monkeypatch.setattr(tickmod, "session_date_for", lambda ts: DAY)

    live = no_session.resume()
    assert live is not None and not live.signals


# --- credentials ------------------------------------------------------------


def test_credentials_read_the_dotenv_file(tmp_path, monkeypatch):
    """`.env` is not loaded at import in this repo — `config.load_env()` is.

    A module that went straight to ``os.getenv`` found nothing however carefully
    the file was filled in, and reported the credentials as *missing* rather than
    as unread. That is a bad failure to debug: everything about the message
    points at the file, which is correct.
    """
    from journal import config as cfgmod
    from journal.live import rithmic as rith

    env = tmp_path / ".env"
    env.write_text("RITHMIC_USER=u\nRITHMIC_PASSWORD=p\n"
                   "RITHMIC_SYSTEM_NAME=Sys Name\nRITHMIC_GATEWAY=host:443\n")
    for k in list(rith._ENV_KEYS.values()):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)

    creds = rith.credentials()
    assert creds["user"] == "u" and creds["url"] == "host:443"
    assert creds["system_name"] == "Sys Name"     # spaces survive, verbatim
    assert creds["app_name"] == "atas_journal_shadow"


def test_missing_credentials_name_the_variable_that_is_actually_missing(monkeypatch):
    """The gateway is read from RITHMIC_GATEWAY, not RITHMIC_URL.

    Deriving the variable name from the client's kwarg told anyone who hit this
    to set RITHMIC_URL — which does not exist, and would not have helped.
    """
    from journal import config as cfgmod
    from journal.live import rithmic as rith

    for k in rith._ENV_KEYS.values():
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfgmod, "load_env", lambda: None)

    with pytest.raises(LookupError) as e:
        rith.credentials()
    assert "RITHMIC_GATEWAY" in str(e.value)
    assert "RITHMIC_URL" not in str(e.value)


# --- the feed's tick decoding ----------------------------------------------


def _feed() -> RithmicFeed:
    f = RithmicFeed.__new__(RithmicFeed)   # no socket, no credentials
    f._last_ns = 0
    f.stats = __import__("collections").Counter()
    return f


def test_the_exchange_stamp_is_preferred_over_rithmics():
    """``source_*`` is the same instant Databento stores as ``ts_event``.

    Without it Phase 6's tape comparison would be measuring Rithmic's delivery
    rather than the two feeds' agreement.
    """
    ns, exch, _ = RithmicFeed._stamp({"source_ssboe": 100, "source_nsecs": 123456789,
                                      "ssboe": 101, "usecs": 500})
    assert (ns, exch) == (100_123_456_789, True)


def test_the_host_clock_is_never_a_fallback():
    """No stamp means the tick is dropped, not stamped from local time.

    The WSL2 host measured 1.7-2.8s behind Rithmic and the offset moved between
    runs, so a locally-stamped tick is a tick at the wrong instant.
    """
    assert RithmicFeed._stamp({"trade_price": 1.0}) == (None, False, None)


def test_microseconds_are_used_when_nanoseconds_are_absent():
    ns, exch, _ = RithmicFeed._stamp({"source_ssboe": 7, "source_usecs": 250})
    assert (ns, exch) == (7_000_250_000, True)
    ns, exch, _ = RithmicFeed._stamp({"ssboe": 7, "usecs": 250})
    assert (ns, exch) == (7_000_250_000, False)


def test_the_hop_is_measured_between_two_stamps_in_the_same_message():
    """The one latency figure that needs no local clock.

    Both stamps ride in the message, so their difference is as good on a host
    whose clock is a second wrong as on one that is right — which is the whole
    reason this is the leg that gets reported.
    """
    _, _, hop = RithmicFeed._stamp({"source_ssboe": 100, "source_nsecs": 0,
                                    "ssboe": 100, "usecs": 350})
    assert hop == 350                              # µs, and Rithmic is later


def test_there_is_no_hop_when_only_one_clock_is_present():
    """A fallback row is one where the exchange stamp is what is missing.

    Reported as absent rather than as zero: zero is a claim about the hop, and
    the honest answer is that this message cannot answer.
    """
    assert RithmicFeed._stamp({"ssboe": 7, "usecs": 250})[2] is None
    assert RithmicFeed._stamp({"source_ssboe": 7, "source_nsecs": 0})[2] is None


def _timed_feed() -> RithmicFeed:
    f = _feed()
    f._hop_us = deque(maxlen=rith.TIMING_WINDOW)
    f._lag_us = deque(maxlen=rith.TIMING_WINDOW)
    return f


def test_timing_reports_percentiles_and_omits_a_leg_it_has_no_samples_for():
    """An absent leg is absent, not zero.

    A feed running on rows that carry only Rithmic's stamp can say nothing about
    the hop, and a reported ``hop_p50_us: 0`` would read as a perfect one.
    """
    f = _timed_feed()
    f._lag_us.extend(range(100))                   # 0..99 µs
    t = f.timing()
    assert t["lag_n"] == 100
    assert t["lag_p50_us"] == 50
    assert t["lag_p90_us"] == 90
    assert t["lag_max_us"] == 99
    assert not any(k.startswith("hop") for k in t)


def test_timing_holds_only_the_recent_feed():
    """Bounded on purpose: a session average hides the minute it stalled."""
    f = _timed_feed()
    f._lag_us.extend([9_000_000] * rith.TIMING_WINDOW)   # an old, bad stretch
    f._lag_us.extend([10] * rith.TIMING_WINDOW)          # and it recovers
    assert f.timing()["lag_max_us"] == 10


def test_the_drain_measures_how_long_a_print_waited_to_be_published():
    """Arrival to publish, on the monotonic clock.

    Both stamps come from `monotonic`, so this survives a host clock that is a
    second wrong — and it is the number that says whether this process, rather
    than the network, is what a tick is waiting on.
    """
    f = _timed_feed()
    f._agg = {1: "B", 2: "A"}
    f.route = lambda frame, record=True: None
    now = time.monotonic()
    # Two prints, arrived 50ms and 10ms ago, both carrying a 300µs hop.
    f._pending = [(1_700_000_000_000_000_000, 100.0, 1, 1, now - 0.050, 300),
                  (1_700_000_000_000_000_001, 100.25, 2, 2, now - 0.010, 300)]
    asyncio.run(f._drain())

    assert list(f._hop_us) == [300, 300]
    lags = list(f._lag_us)
    assert len(lags) == 2
    # Generous bounds: what is asserted is that the wait is measured per print
    # and in the right unit, not the machine's scheduling jitter.
    assert 45_000 <= lags[0] <= 200_000
    assert 5_000 <= lags[1] <= 160_000
    assert lags[0] > lags[1]                       # the older print waited longer


def test_a_row_with_no_hop_is_skipped_rather_than_counted_as_zero():
    f = _timed_feed()
    f._agg = {1: "B", 2: "A"}
    f.route = lambda frame, record=True: None
    now = time.monotonic()
    f._pending = [(1_700_000_000_000_000_000, 100.0, 1, 1, now, None),
                  (1_700_000_000_000_000_001, 100.25, 2, 2, now, 400)]
    asyncio.run(f._drain())

    assert list(f._hop_us) == [400]                # one sample, not a zero and a 400
    assert len(f._lag_us) == 2                     # but both prints waited


def test_the_tape_is_forced_monotonic_across_batches():
    """An out-of-order tick would not raise — it would re-phase bars.

    Clamping forward rather than dropping, because a dropped print is a real
    trade missing from the profile and the VWAP.
    """
    f = _feed()
    a = f._clamped(np.array([10, 20, 15, 30], dtype="int64"))
    assert a.tolist() == [10, 20, 20, 30]
    b = f._clamped(np.array([25, 40], dtype="int64"))   # 25 is behind the last
    assert b.tolist() == [30, 40]
    assert f.stats["clamped"] == 2


# --- the backfill -----------------------------------------------------------
#
# What it is for: a live subscription delivers prints from the moment it opens,
# so a feed connected at nine holds a session that began at nine — and the engine
# reads a *frame*, so every strategy would be simulating a day that opened hours
# late, silently and with plausible numbers. Same failure `resume()` exists to
# prevent, from the other side.


# A past instant, deliberately: `_backfill` asks for nothing when the range it
# would request has not happened yet, so a `BASE_NS` in the future would make
# every test here pass by doing nothing at all.
BASE_NS = int(pd.Timestamp("2025-10-13T13:30:00Z").value)


def _bar(ns: int, price: float, size: int, side: str = "bid",
         num_trades: int = 1) -> dict:
    """One historical tick bar, in the shape ``MessageToDict`` produces.

    Faithful to two details that broke the first version of the reader: uint64
    fields arrive as JSON *strings*, and a zero-valued field is left out of the
    dict altogether rather than sent as 0.
    """
    bar = {
        "data_bar_ssboe": [ns // 1_000_000_000],
        "data_bar_usecs": [(ns % 1_000_000_000) // 1_000],
        "close_price": price,
        "volume": str(size),
        "num_trades": str(num_trades),
    }
    if side in ("bid", "both"):
        bar["bid_volume"] = str(size)
    if side in ("ask", "both"):
        bar["ask_volume"] = str(size)
    return bar


def _replay_maps():
    return rith._replay_aggressor_map(), rith._aggressor_map()


def test_a_replay_bar_becomes_the_same_print_a_live_tick_would():
    agg_num, agg_side = _replay_maps()
    f = rith.replay_frame([_bar(BASE_NS, 23000.25, 3, "bid")], agg_num, agg_side)
    assert list(f.columns) == rith.FEED_COLS + ["num_trades"]
    assert f["ts_utc"].iloc[0] == pd.Timestamp(BASE_NS, tz="UTC")
    assert f["price"].iloc[0] == 23000.25
    assert f["size"].iloc[0] == 3 and f["size"].dtype == "uint32"


def test_the_side_mapping_is_the_measured_one_not_the_obvious_one():
    """``bid_volume`` is the BUY aggressor, and that reads backwards.

    A buy lifts the offer, so ``ask_volume`` is the natural guess — and it is
    wrong. Measured against the live aggressor int on matched prints, twice, with
    zero off-diagonal (docs/live-shadow-plan.md § Tick replay). This test is the
    finding, written down where it cannot be reasoned away.
    """
    agg_num, agg_side = _replay_maps()
    f = rith.replay_frame([_bar(BASE_NS, 1.0, 1, "bid"),
                           _bar(BASE_NS + 1000, 1.0, 1, "ask")], agg_num, agg_side)
    assert f["side"].tolist() == ["B", "A"]
    # The evidence travels with the interpretation: `agg_raw` is in the same
    # alphabet a live print uses, so Phase 6's cross-tab reads both.
    assert f["agg_raw"].tolist() == [agg_num["bid_volume"], agg_num["ask_volume"]]


def test_a_bar_with_volume_on_both_sides_has_no_side():
    """Rather than a side picked by whichever field was read last."""
    agg_num, agg_side = _replay_maps()
    f = rith.replay_frame([_bar(BASE_NS, 1.0, 2, "both")], agg_num, agg_side)
    assert f["side"].iloc[0] == "N" and f["agg_raw"].iloc[0] == 0


def test_replayed_bars_are_sorted_before_anything_reads_them():
    """`LiveSession.append` trusts its rows to be in order and the engine
    searchsorts the RTH boundary — an out-of-order tape re-phases every bar
    behind it without raising."""
    agg_num, agg_side = _replay_maps()
    f = rith.replay_frame([_bar(BASE_NS + 5000, 2.0, 1), _bar(BASE_NS, 1.0, 1)],
                          agg_num, agg_side)
    assert f["price"].tolist() == [1.0, 2.0]


def test_the_seam_drops_a_print_rather_than_duplicating_it():
    """The replay is on Rithmic's clock and the tape on the exchange's.

    The *same trade* is stamped a median 287µs later in the replay, so a seam cut
    exactly at the tape's last tick would re-admit it as a new one — volume that
    never traded, at a price that printed once. `SEAM_SLACK_NS` cuts wide instead,
    which can only lose a print at the join.
    """
    agg_num, agg_side = _replay_maps()
    bars = [_bar(BASE_NS + 300_000, 1.0, 1),                    # the hop-delayed twin
            _bar(BASE_NS + rith.SEAM_SLACK_NS + 1, 2.0, 1)]     # genuinely new
    f = rith.trim_seam(rith.replay_frame(bars, agg_num, agg_side),
                       BASE_NS + rith.SEAM_SLACK_NS, None)
    assert f["price"].tolist() == [2.0]


def test_the_backfill_ends_at_the_first_print_that_actually_arrived():
    """Not at the requested end instant.

    The request is made deliberately wide because its end comes from the host
    clock, which measured 1.7-2.8s off with a moving offset. The join is decided
    against a print that arrived instead.
    """
    agg_num, agg_side = _replay_maps()
    bars = [_bar(BASE_NS + i * 1_000_000_000, float(i), 1) for i in range(5)]
    f = rith.trim_seam(rith.replay_frame(bars, agg_num, agg_side),
                       BASE_NS, BASE_NS + 2_500_000_000)
    assert f["price"].tolist() == [0.0, 1.0, 2.0]


class _StubHistory:
    """A client that answers the replay and records what it was asked for.

    Answers each request with only the bars inside the range it was given, the
    way Rithmic does — otherwise a test cannot tell a correctly-scoped request
    from one that asked for the whole day and was saved by the trim.
    """

    def __init__(self, bars, fail: bool = False) -> None:
        self.bars = bars
        self.fail = fail
        self.asked: list[tuple] = []

    async def get_historical_tick_data(self, symbol, exchange, start, end, **kw):
        self.asked.append((symbol, exchange, start, end))
        if self.fail:
            raise RuntimeError("replay unavailable")
        lo, hi = int(pd.Timestamp(start).value), int(pd.Timestamp(end).value)
        return [b for b in self.bars
                if lo <= b["data_bar_ssboe"][0] * 1_000_000_000
                + b["data_bar_usecs"][0] * 1_000 < hi]


def _backfill_feed(routed: list, *, from_ns: int, last_ns: int = 0,
                   pending: list | None = None,
                   resume: pd.DataFrame | None = None) -> RithmicFeed:
    f = RithmicFeed.__new__(RithmicFeed)   # no socket, no credentials
    f.symbol, f.exchange = "NQU6", "CME"
    f.route = lambda frame, record=True: routed.append((frame, record))
    f._from_ns, f._last_ns = from_ns, last_ns
    f._pending = pending or []
    f._resume = resume
    f._agg_replay, f._agg = _replay_maps()
    f._stop = __import__("threading").Event()
    f.stats = Counter()
    f.backfills = []
    return f


def test_the_backfill_publishes_the_session_so_far():
    routed: list = []
    bars = [_bar(BASE_NS + i * 1_000_000_000, 100.0 + i, 1) for i in range(4)]
    feed = _backfill_feed(routed, from_ns=BASE_NS)
    asyncio.run(feed._backfill(_StubHistory(bars)))

    assert len(routed) == 1
    f, record = routed[0]
    assert f["price"].tolist() == [100.0, 101.0, 102.0, 103.0]
    assert record is True
    assert "num_trades" not in f.columns      # never reaches the tape
    assert feed.stats["backfill_rows"] == 4
    # Left where the backfill ends, so the next piece clamps against it rather
    # than against nothing.
    assert feed._last_ns == BASE_NS + 3_000_000_000
    assert feed.backfills[0]["rows"] == 4


def test_the_join_ends_at_the_first_print_that_actually_arrived():
    """Not at the host clock, which measured 1.7-2.8s off with a moving offset.

    A join placed a second late replays prints the subscription is about to
    deliver — the one error direction that puts volume on the tape twice.
    """
    routed: list = []
    bars = [_bar(BASE_NS + i * 1_000_000_000, 100.0 + i, 1) for i in range(4)]
    feed = _backfill_feed(routed, from_ns=BASE_NS,
                          pending=[(BASE_NS + 2_500_000_000, 1.0, 1, 1)])
    asyncio.run(feed._join(_StubHistory(bars)))

    assert routed[0][0]["price"].tolist() == [100.0, 101.0, 102.0]
    assert feed.backfills[0]["range"] == "join"
    assert feed.backfills[0]["covered"] is True
    # Trimmed rather than published: the request deliberately reaches a minute
    # past the join (Rithmic's replay is second-granular at the boundary), each
    # continuation re-asks from the second its last print fell in, and an empty
    # window is asked twice before it is believed.
    assert feed.stats["backfill_dropped_seam"] == 5


def test_a_quiet_market_joins_short_of_now_rather_than_guessing(monkeypatch):
    """No print arrived, and the wait itself is the evidence: nothing traded in
    those seconds, so there is nothing in them to replay."""
    monkeypatch.setattr(rith, "JOIN_WAIT_S", 0.2)
    routed: list = []
    feed = _backfill_feed(routed, from_ns=BASE_NS)
    client = _StubHistory([_bar(BASE_NS, 1.0, 1)])
    asyncio.run(feed._join(client))

    _, _, _, end = client.asked[0]
    # Short of now by the wait, not past it — a range reaching `now` would
    # replay prints the subscription is about to deliver.
    assert pd.Timestamp(end).timestamp() < time.time() + 60
    assert routed[0][0]["price"].tolist() == [1.0]


def test_the_night_in_front_of_an_existing_recording_is_what_gets_filled():
    """The bug a real run found, and the reason the pieces are ordered.

    Somebody connects at 07:08, watches, stops. Reconnecting later, a rule that
    resumed from "the tape's tail" would start at 07:09 and skip the whole night
    in front of it — leaving the ``gx_*`` gates with nothing to read and seven
    strategies vetoing everything without saying why. That is the failure this
    feature exists to remove, reintroduced by the fix for it.
    """
    routed: list = []
    rec_from = BASE_NS + 3_600_000_000_000            # an hour into the session
    recorded = pd.DataFrame({
        "ts_utc": pd.to_datetime([rec_from, rec_from + 1_000_000_000], utc=True),
        "price": [50.0, 51.0], "size": np.ones(2, dtype="uint32"), "side": ["B", "B"],
    })
    bars = ([_bar(BASE_NS + i * 60_000_000_000, float(i), 1) for i in range(3)]
            + [_bar(rec_from + 2_000_000_000, 99.0, 1)])
    feed = _backfill_feed(routed, from_ns=BASE_NS, resume=recorded)
    asyncio.run(feed._backfill(_StubHistory(bars)))

    kinds = [(f["price"].tolist(), record) for f, record in routed]
    assert kinds == [
        ([0.0, 1.0, 2.0], True),     # the night, recorded — the gates read it
        ([50.0, 51.0], False),       # already on disk; writing it twice would
                                     # put the day on disk out of order
        ([99.0], True),              # and on to the live join
    ]
    # One ordered stream, which is the only kind `LiveSession.append` can take.
    stamps = pd.concat([f["ts_utc"] for f, _ in routed], ignore_index=True)
    assert stamps.is_monotonic_increasing
    assert [b["range"] for b in feed.backfills] == ["head", "tail"]


def test_a_reconnect_repairs_the_hole_the_dropped_socket_left():
    """No head and no disk frame — the tape's tail is the last live tick
    published before the socket went, and one range covers the gap."""
    routed: list = []
    tail = BASE_NS + 3_600_000_000_000
    feed = _backfill_feed(routed, from_ns=BASE_NS, last_ns=tail)
    client = _StubHistory([_bar(tail + 1_000_000_000, 5.0, 1)])
    asyncio.run(feed._backfill(client))

    # The first request starts at the tape's tail plus the seam slack; the second
    # is the confirming call that comes back empty and ends the range.
    _, _, start, _ = client.asked[0]
    assert int(pd.Timestamp(start).value) == tail + rith.SEAM_SLACK_NS
    assert routed[0][0]["price"].tolist() == [5.0]
    assert len(routed) == 1


def test_a_failed_backfill_costs_the_stretch_and_not_the_feed():
    """It is an enhancement to the tape, never a precondition for streaming."""
    routed: list = []
    feed = _backfill_feed(routed, from_ns=BASE_NS)
    asyncio.run(feed._backfill(_StubHistory([], fail=True)))

    assert routed == []
    assert feed.stats["backfill_errors"] == 1
    assert "replay unavailable" in feed.backfills[0]["error"]


def test_an_aggregating_replay_is_counted_rather_than_trusted():
    """Every bar of a whole session carried exactly one trade when this was
    probed. If that stops being true the tape is no longer prints."""
    routed: list = []
    feed = _backfill_feed(routed, from_ns=BASE_NS)
    asyncio.run(feed._backfill(_StubHistory([
        _bar(BASE_NS, 1.0, 5, num_trades=3),
        _bar(BASE_NS + 1_000_000_000, 2.0, 1)])))

    assert feed.stats["backfill_aggregated"] == 1
    assert len(routed[0][0]) == 2     # admitted: a real trade at a real price


# --- the harvest ------------------------------------------------------------
#
# The other half of "the tape is whole": the backfill covers the session you
# connected to, this covers the ones nobody was connected for at all.


def test_a_day_with_nothing_recorded_needs_its_whole_session(live_store):
    lo, hi = tickmod.day_bounds_utc(DAY)
    assert harvest.gaps_in("TEST", DAY) == [(int(lo.value), int(hi.value))]


def test_a_partly_recorded_day_needs_the_head_and_the_tail(live_store):
    """The shape a stopped-and-restarted session leaves behind — and the head is
    the half that matters, since that is where the night lives."""
    df = _synthetic_day(DAY)
    _record("TEST", DAY, df.iloc[300:700])
    lo, hi = tickmod.day_bounds_utc(DAY)
    gaps = harvest.gaps_in("TEST", DAY)

    assert len(gaps) == 2
    assert gaps[0][0] == int(lo.value)
    # Both edges held off what is already recorded by the seam slack: a replayed
    # print carries Rithmic's stamp, a median 287µs after the exchange's, so a
    # tail starting exactly at the last recorded tick would re-admit that very
    # print as a new one.
    assert gaps[0][1] == int(df["ts_utc"].iloc[300].value) - rith.SEAM_SLACK_NS
    assert gaps[1][0] == int(df["ts_utc"].iloc[699].value) + rith.SEAM_SLACK_NS
    assert gaps[1][1] == int(hi.value)


def test_a_flagged_day_is_never_looked_at_again(live_store):
    """`market_closed` only knows *full* exchange closures, and only for
    contracts with a roll probe — a pinned raw contract has none, so it answers
    False for every day of the year. A half-day and a day with a hole in it are
    also indistinguishable from the timestamps. So completion is recorded, not
    re-derived; without this a holiday is re-fetched on every startup forever.
    """
    r = TickRecorder("TEST", DAY)
    r.append(_synthetic_day(DAY).iloc[:10])
    r.close(None)
    tickmod._clear_tick_caches()
    assert harvest.gaps_in("TEST", DAY)              # short, so it looks incomplete

    r.heartbeat(None, closed=True, source="harvest", harvest={"complete": True})
    assert harvest.gaps_in("TEST", DAY) == []        # ...and the flag settles it


def test_a_session_still_running_is_left_to_the_feed(live_store, monkeypatch):
    """Marking a day complete while it is still being traded would freeze it
    half-recorded, and the live feed owns the current session anyway."""
    today = tickmod.session_date_for(pd.Timestamp.now(tz="UTC"))
    assert today not in harvest.pending("TEST", today, today)


def test_the_sweep_skips_weekends(live_store):
    """A session runs prev 18:00 → 18:00 ET, so Sunday evening belongs to Monday
    and Saturday is unreachable."""
    days = harvest.sessions_between(date(2025, 10, 10), date(2025, 10, 14))
    assert [d.day for d in days] == [10, 13, 14]     # Fri, Mon, Tue


class _StubSweepClient:
    """Answers a replay with one print per requested range.

    Note a session's range *starts* on the previous calendar day — 18:00 ET —
    so anything keyed on the requested start's date is keyed on the wrong day.
    """

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[tuple] = []
        self._ns: int | None = None

    async def get_historical_tick_data(self, symbol, exchange, start, end, **kw):
        self.asked.append((start, end))
        if self.fail:
            raise RuntimeError("replay unavailable")
        # One print, fixed on the first request and then answered for by range —
        # the fetch loop asks repeatedly until a call comes back empty, so a stub
        # that invents a fresh print per call would never let it finish.
        if self._ns is None:
            self._ns = int(pd.Timestamp(start).value) + 1_000_000_000
        lo, hi = int(pd.Timestamp(start).value), int(pd.Timestamp(end).value)
        return [_bar(self._ns, 100.0, 1)] if lo <= self._ns < hi else []


class _TruncatingClient:
    """Rithmic as it actually behaves: answers with a silent *prefix*.

    Measured, not imagined — a request for the whole of 2026-06-16 came back
    with exactly 50,000 prints ending at 04:29 ET, on a day whose neighbours
    returned 313k and 487k. Nothing raised and nothing was flagged.
    """

    def __init__(self, ns_list: list[int], per_call: int = 2) -> None:
        self.ns = sorted(ns_list)
        self.per_call = per_call
        self.calls = 0

    async def get_historical_tick_data(self, symbol, exchange, start, end, **kw):
        self.calls += 1
        lo = int(pd.Timestamp(start).value)
        hi = int(pd.Timestamp(end).value)
        inside = [n for n in self.ns if lo <= n < hi]
        return [_bar(n, 100.0, 1) for n in inside[:self.per_call]]


def test_a_truncated_replay_is_continued_not_believed():
    """One call is not one range, and this is the loop that finds that out."""
    base = BASE_NS
    want = [base + i * 1_000_000_000 for i in range(7)]
    client = _TruncatingClient(want, per_call=2)
    got: list = []

    async def publish(frame):
        got.append(frame)

    res = asyncio.run(rith.replay_into(
        client, "TEST", "CME", base, base + 10_000_000_000,
        *_replay_maps(), publish))

    assert res["rows"] == 7 and res["covered"] is True
    assert client.calls > 1                    # it did not believe the first call
    stamps = pd.concat([f["ts_utc"] for f in got], ignore_index=True)
    # Every print, once, in order — the re-sent boundary of each continuation is
    # cut by the trim rather than published twice.
    assert stamps.is_monotonic_increasing and len(stamps) == 7
    assert res["dropped"] == client.calls - 1  # one re-sent print per continuation


def test_an_empty_window_is_asked_twice_before_it_is_believed():
    """2026-08-04 was flagged complete ending at 15:48 ET on the strength of one
    empty final window. The same window asked again holds 51,271 prints."""
    seen: list[int] = []

    class _EmptyOnce:
        async def get_historical_tick_data(self, symbol, exchange, start, end, **kw):
            seen.append(len(seen))
            # Nothing the first time, the print every time after.
            return [] if len(seen) == 1 else [_bar(BASE_NS + 1_000_000_000, 7.0, 1)]

    got: list = []

    async def publish(frame):
        got.append(frame)

    res = asyncio.run(rith.replay_into(
        _EmptyOnce(), "TEST", "CME", BASE_NS, BASE_NS + 7_200_000_000_000,
        *_replay_maps(), publish))

    assert res["rows"] == 1 and got[0]["price"].tolist() == [7.0]


def test_a_range_that_cannot_be_finished_is_reported_uncovered():
    """The cap is a backstop, and hitting it is not completion.

    Everything downstream keys off `covered`: a harvested day is flagged — and
    so never looked at again — only when the replay itself said there was
    nothing left.
    """
    want = [BASE_NS + i * 1_000_000_000 for i in range(20)]
    client = _TruncatingClient(want, per_call=2)

    async def publish(frame):
        pass

    res = asyncio.run(rith.replay_into(
        client, "TEST", "CME", BASE_NS, BASE_NS + 60_000_000_000,
        *_replay_maps(), publish, max_calls=3))
    assert res["calls"] == 3 and res["covered"] is False


def test_a_truncated_day_is_not_flagged_complete(live_store):
    """The safety property. A day flagged complete is never looked at again, so
    the flag must mean "the replay said there was nothing left", not "nothing
    threw" — otherwise a silent prefix becomes the permanent record of a
    session."""
    lo, hi = tickmod.day_bounds_utc(DAY)
    want = [int(lo.value) + i * 60_000_000_000 for i in range(20)]
    # Progress, but not enough of it: each continuation re-sends its boundary
    # print and nets one new one, so three calls cannot reach the day's end.
    client = _TruncatingClient(want, per_call=2)
    monkey = rith.REPLAY_MAX_CALLS
    try:
        rith.REPLAY_MAX_CALLS = 3
        res = asyncio.run(harvest.harvest_day(client, "TEST", DAY))
    finally:
        rith.REPLAY_MAX_CALLS = monkey

    assert res["covered"] is False
    assert recorder_mod.read_manifest("TEST", DAY)["harvest"]["complete"] is False
    assert harvest.gaps_in("TEST", DAY)      # so the next sweep picks it up


def test_the_harvest_does_not_re_admit_a_print_it_already_has(live_store):
    """The seam again, from the other side. A day recorded up to 16:00 needs its
    post hour — but the replay of that range is stamped on Rithmic's clock, so a
    range starting at the last recorded tick would fetch that tick back."""
    df = _synthetic_day(DAY)
    _record("TEST", DAY, df.iloc[:900])
    last = int(df["ts_utc"].iloc[899].value)
    gaps = harvest.gaps_in("TEST", DAY)

    assert len(gaps) == 1                     # nothing missing at the head
    assert gaps[0][0] > last                  # and the tail starts after it
    # A replayed twin of that print, hop-delayed, falls outside the range.
    twin = rith.trim_seam(
        rith.replay_frame([_bar(last + 300_000, 1.0, 1)], *_replay_maps()),
        gaps[0][0], gaps[0][1])
    assert twin.empty


def test_a_harvested_day_is_recorded_and_flagged(live_store):
    client = _StubSweepClient()
    res = asyncio.run(harvest.harvest_day(client, "TEST", DAY))

    assert res["rows"] == 1 and res["error"] is None
    assert len(tickmod.live_day_ticks("TEST", DAY)) == 1
    man = recorder_mod.read_manifest("TEST", DAY)
    # `source` is what tells a reader this day was never *watched*: no signal
    # journal exists for it, and it is on Rithmic's clock throughout.
    assert man["source"] == "harvest"
    assert man["harvest"]["complete"] is True
    assert harvest.gaps_in("TEST", DAY) == []        # and so it is not re-fetched


def test_a_day_rithmic_refused_is_left_unflagged(live_store):
    """A day the replay would not answer for must come back on the next sweep."""
    client = _StubSweepClient(fail=True)
    res = asyncio.run(harvest.harvest_day(client, "TEST", DAY))

    assert res["error"] and res["rows"] == 0
    man = recorder_mod.read_manifest("TEST", DAY)
    assert man["harvest"]["complete"] is False
    assert harvest.gaps_in("TEST", DAY)              # still pending


def test_a_day_that_came_back_empty_is_retried_not_believed(live_store):
    """An empty answer is not evidence of an empty session.

    2026-07-06 came back with nothing and was flagged complete; a 60-second probe
    of the same day at 10:00 ET returned 2,142 prints. A holiday and a transient
    miss are indistinguishable from one call, so the cheap error is the one to
    make: retry a real holiday every sweep rather than record a real session as
    having had no trades in it.
    """
    class _Empty:
        async def get_historical_tick_data(self, *a, **kw):
            return []

    res = asyncio.run(harvest.harvest_day(_Empty(), "TEST", DAY))
    assert res["rows"] == 0 and res["error"] is None
    assert recorder_mod.read_manifest("TEST", DAY)["harvest"]["complete"] is False
    assert harvest.gaps_in("TEST", DAY)


def test_a_half_day_is_flagged_even_though_its_tail_is_empty(live_store):
    """Juneteenth and 3 July both close at 13:00 ET.

    The condition is "the day has prints", not "this fetch returned prints" —
    otherwise every half-day stays unflagged and is re-fetched on every sweep
    for the life of the machine.
    """
    class _Empty:
        async def get_historical_tick_data(self, *a, **kw):
            return []

    _record("TEST", DAY, _synthetic_day(DAY).iloc[:400])   # a session that stops early
    res = asyncio.run(harvest.harvest_day(_Empty(), "TEST", DAY))

    assert res["rows"] == 0                                # nothing after the close
    assert recorder_mod.read_manifest("TEST", DAY)["harvest"]["complete"] is True
    assert harvest.gaps_in("TEST", DAY) == []              # and it is left alone


def test_two_sweeps_cannot_run_at_once(live_store):
    """Rithmic allows one session per login: two sweeps racing would force-log-out
    each other, and the live feed with them."""
    async def both():
        client = _StubSweepClient()
        start = DAY

        async def one():
            return await harvest.sweep(client, "TEST", start, start)

        return await asyncio.gather(one(), one())

    a, b = asyncio.run(both())
    assert bool(a) != bool(b)      # exactly one of them did the work


# --- the reconciliation -----------------------------------------------------


@needs_ticks
def test_a_tape_recorded_from_the_cached_day_reconciles_perfectly(live_store):
    """The control: record exactly what Databento holds, expect no residual.

    A comparison that cannot report agreement when the two tapes are literally
    the same ticks cannot report disagreement meaningfully either.
    """
    parts = [tickmod._read_segment_cached(CONTRACT, DAY, s) for s in tickmod.SEGMENTS]
    whole = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    whole = whole.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    whole["agg_raw"] = np.where(whole["side"].to_numpy() == "B", 1,
                                np.where(whole["side"].to_numpy() == "A", 2, 0)
                                ).astype("int16")
    _record(CONTRACT, DAY, whole, batch=20_000)

    res = recmod.tape_fidelity(CONTRACT, DAY)
    assert res["status"] == "ok", res
    assert res["matched_volume_share"] == 1.0
    assert res["live_only_volume"] == 0 and res["ref_only_volume"] == 0
    assert res["aggressor"]["verdict"] == "confirmed"
    # The one residual left is the documented rth/post seam: the cached post
    # file's first tick sits a hair before the 20:00 boundary its own window
    # declares, so a live tape sliced by time files it under RTH. The whole day
    # still matches exactly — which is the distinction the headline draws.
    assert res["window_boundary_volume"] == 4  # two contracts, counted both ways


@needs_ticks
def test_fidelity_notices_a_tape_that_is_missing_prints(live_store):
    """And the negative control — it fails when the bug it guards is present."""
    parts = [tickmod._read_segment_cached(CONTRACT, DAY, s) for s in tickmod.SEGMENTS]
    whole = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    whole = whole.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    _record(CONTRACT, DAY, whole.iloc[::2], batch=20_000)   # every other print

    res = recmod.tape_fidelity(CONTRACT, DAY)
    assert res["status"] == "degraded"
    assert res["matched_volume_share"] < 0.9
    assert res["ref_only_volume"] > 0


def test_fidelity_says_unavailable_rather_than_perfect_with_nothing_to_check(live_store):
    """A day that was never bought must not read as 100% agreement.

    ``cached_rth`` falls through to the live store by design; a reconciliation
    that used that fallthrough would compare a tape against itself.
    """
    _record("TEST", DAY, _synthetic_day(DAY))
    res = recmod.tape_fidelity("TEST", DAY)
    assert res["status"] == "unavailable"


def test_signal_agreement_reports_itself_unattributable(live_store):
    """The flag is the point of the module.

    A difference in stage 3 with stage 1 unresolved could be the feeds, the
    prefix property, or the strategies — and there is no way to tell from the
    number alone.
    """
    _record("TEST", DAY, _synthetic_day(DAY))
    res = recmod.reconcile("TEST", DAY)
    assert res["tape_fidelity"]["status"] == "unavailable"
    assert res["signal_agreement"]["attributable"] is False
    assert "NOT ATTRIBUTABLE" in (res["signal_agreement"].get("note") or "")


def test_prefix_integrity_needs_a_journal_and_says_so(live_store):
    _record("TEST", DAY, _synthetic_day(DAY))
    res = recmod.prefix_integrity("TEST", DAY)
    assert res["status"] == "unavailable"
    assert "no signal journal" in res["reason"]


# --- the signal journal -----------------------------------------------------


def test_the_journal_writes_a_line_only_when_the_answer_changes(live_store):
    """A strategy that has not signalled all morning says the same nothing every
    thirty seconds; writing those would bury the lines that carry the day."""
    j = jourmod.SignalJournal("TEST", DAY)
    t = [{"direction": "Long", "entry_ts_utc": "2025-10-13T13:35:00Z",
          "net_pnl": 100.0}]
    assert j.record("s", 10, None, [], []) is True
    assert j.record("s", 20, None, [], []) is False
    assert j.record("s", 30, None, t, []) is True
    assert j.record("s", 40, None, t, []) is False
    lines = jourmod.read("TEST", DAY, "s")
    assert [len(x["trades"]) for x in lines] == [0, 1]
    assert jourmod.slugs("TEST", DAY) == ["s"]


# --- end to end -------------------------------------------------------------


@needs_ticks
def test_the_day_reconciles_end_to_end(live_store):
    """Record a session, shadow it as it grows, then reconcile all three stages.

    The only test here that exercises the actual shelf, and the one that would
    catch the failure the whole feature is exposed to: a live surface that shows
    trades the settled day does not contain. The tape is the cached session
    re-recorded, so a disagreement in stage 3 cannot be the feeds — which is
    exactly the isolation the three-stage ordering is for.
    """
    from journal.live.journal import SignalJournal
    from journal.live.session import LiveSession
    from journal.live.shadow import ShadowRunner

    parts = [tickmod._read_segment_cached(CONTRACT, DAY, s) for s in tickmod.SEGMENTS]
    whole = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    whole = whole.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    _record(CONTRACT, DAY, whole, batch=50_000)

    # Shadow the day as it grows, journalling what was said at each point. The
    # cadence floor is bypassed deliberately: this is testing the prefix
    # property, not the scheduler.
    session = LiveSession(CONTRACT, DAY, "test-gen")
    runner = ShadowRunner(session, journal=SignalJournal(CONTRACT, DAY))
    for cut in (0.4, 0.7, 1.0):
        upto = int(len(whole) * cut)
        session.append(whole.iloc[session.n:upto])
        for w in runner.watches:
            w.last_at, w.last_rows, w.ran = 0.0, 0, False
        runner.run_due()

    res = recmod.reconcile(CONTRACT, DAY)

    assert res["tape_fidelity"]["status"] == "ok", res["tape_fidelity"]
    prefix = res["prefix_integrity"]
    assert prefix["status"] == "ok", [r for r in prefix["per_strategy"] if not r["ok"]]
    assert prefix["strategies"] > 0
    # Something has to have traded, or "no divergence" is vacuous.
    assert any(r["settled_trades"] > 0 for r in prefix["per_strategy"])

    agree = res["signal_agreement"]
    assert agree["status"] == "ok" and agree["attributable"] is True
    assert agree["pnl_share_live"] == 1.0, [
        r for r in agree["per_strategy"] if r["pnl_share_live"] != 1.0]
    # Every trade matched and every one of them ended for the same money. The
    # exits that differ do so only in their stamp, and only for positions
    # force-flattened on the tape's last tick — the two stores' last ticks differ
    # by the documented rth/post seam, which stage 1 has already reported. This
    # asserts the two failures stay separated: a P&L difference here would be a
    # real disagreement, and there is none.
    assert agree["exit_pnl_delta"] == 0.0
    for row in agree["per_strategy"]:
        for d in row["divergent_exits"]:
            assert set(d) <= {"entry", "exit_ts_utc"}, d


@needs_ticks
def test_prefix_integrity_catches_a_signal_the_day_never_contained(live_store):
    """The negative control: it must fail when the bug it guards is present.

    A journal claiming a trade the settled run does not have is the loudest
    failure live mode can produce — the surface showed something that did not
    happen — and a check that cannot see it is not a check.
    """
    from journal.live.journal import SignalJournal

    parts = [tickmod._read_segment_cached(CONTRACT, DAY, s) for s in tickmod.SEGMENTS]
    whole = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    _record(CONTRACT, DAY, whole.sort_values("ts_utc", kind="stable"), batch=50_000)

    j = SignalJournal(CONTRACT, DAY)
    j.record("vwap-upper-band-bounce", 1000, None, [{
        "direction": "Long", "entry_ts_utc": "2025-10-13T14:00:00+00:00",
        "exit_ts_utc": "2025-10-13T14:30:00+00:00", "avg_entry": 1.0,
        "avg_exit": 2.0, "exit_reason": "invented", "net_pnl": 5000.0}], [])

    res = recmod.prefix_integrity(CONTRACT, DAY)
    assert res["status"] == "failed"
    assert res["diverged"] == 1


def test_a_truncated_journal_line_is_dropped_not_raised_on(live_store):
    """The process writing it may simply have been killed mid-append; the lines
    behind it are still exactly what was said."""
    j = jourmod.SignalJournal("TEST", DAY)
    j.record("s", 10, None, [], [])
    with (jourmod.day_dir("TEST", DAY) / "s.jsonl").open("a") as fh:
        fh.write('{"rows": 20, "trad')
    assert len(jourmod.read("TEST", DAY, "s")) == 1


# --- coverage: what is on disk, and how long the holes stay fillable ---------


def test_a_raw_contract_parses_and_a_root_does_not():
    assert harvest.parse_contract("NQU6") == ("NQ", 9, 2026)
    assert harvest.parse_contract("NQU26") == ("NQ", 9, 2026)  # two digits taken literally
    assert harvest.parse_contract("MNQZ5") == ("MNQ", 12, 2025)
    # A root has no month code, which is the same thing the feed's own guard is
    # about: a root would send `contract_for` to probe Databento.
    assert harvest.parse_contract("NQ") is None
    assert harvest.parse_contract("") is None


def test_the_expiry_is_the_third_friday_and_only_for_roots_that_settle_there():
    assert harvest.contract_expiry("NQU6") == date(2026, 9, 18)
    assert harvest.contract_expiry("NQZ5") == date(2025, 12, 19)
    assert harvest.contract_expiry("ESH7") == date(2027, 3, 19)
    # Crude settles nowhere near the third Friday. No date beats a plausible
    # wrong one on a deadline nobody can re-check after it passes.
    assert harvest.contract_expiry("CLM6") is None


def test_the_replay_window_counts_the_holes_and_the_days_left():
    today = date(2026, 8, 6)
    w = harvest.replay_window("NQU6", {date(2026, 8, 5), date(2026, 8, 4)}, today)

    assert w["floor"] == "2026-04-08"                  # today - 120
    assert w["expiry"] == "2026-09-18" and w["days_to_expiry"] == 43
    assert w["recorded"] == 2
    assert w["missing"] == w["sessions"] - 2
    # Listed as well as counted, so the strip can draw the holes without a second
    # copy of the session calendar living in the client.
    assert len(w["missing_dates"]) == w["missing"]
    # Today's session is the live feed's job, not a hole — counting it would open
    # every morning with a fresh one.
    assert today.isoformat() not in w["missing_dates"]
    assert w["missing_dates"][0] == w["oldest_missing"]


def test_the_window_ends_at_the_contract_and_not_at_the_calendar():
    """The floor slides; the expiry does not. Both are ceilings on repair."""
    near = harvest.replay_window("NQU6", set(), date(2026, 9, 14))
    assert near["days_to_expiry"] == 4
    past = harvest.replay_window("NQU6", set(), date(2026, 10, 1))
    assert past["days_to_expiry"] < 0     # said plainly rather than clamped to 0


def test_a_watched_day_and_a_harvested_one_are_told_apart():
    """The four cases, in the order the evidence is trustworthy.

    Not cosmetic: a harvested day has no signal journal and carries Rithmic's
    clock rather than the exchange's, and a reader of the data has to be able to
    find that out without asking a person.
    """
    from api.routers.live import _kind_of

    assert _kind_of({}, ["vwap-upper-band-bounce"]) == "watched"
    assert _kind_of({"shadow": "off"}, []) == "watched"      # shelf off, still watched
    assert _kind_of({"source": "harvest"}, ["s"]) == "filled"  # watched, then repaired
    assert _kind_of({"source": "harvest"}, []) == "harvest"
    # Nothing on disk says which. Guessing "harvested" here would put a clock
    # claim on a day that has not earned one.
    assert _kind_of({}, []) == "unknown"


def test_a_gap_fill_does_not_erase_that_the_day_was_watched(live_store):
    """`heartbeat` rewrites session.json whole.

    Without carrying the mark, a watched day that the sweep later repaired came
    back looking exactly like a day nobody was ever connected for.
    """
    r = TickRecorder("TEST", DAY)
    r.marks["shadow"] = "on"
    r.append(_synthetic_day(DAY).iloc[:5])
    r.close(None)
    tickmod._clear_tick_caches()

    asyncio.run(harvest.harvest_day(_StubSweepClient(), "TEST", DAY))

    man = recorder_mod.read_manifest("TEST", DAY)
    assert man["source"] == "harvest"       # the sweep did write it...
    assert man["shadow"] == "on"            # ...and the session's mark survived


def test_the_recordings_list_carries_the_provenance_and_the_deadline(live_store, monkeypatch):
    from api.routers.live import live_recordings

    monkeypatch.delenv("LIVE_SYMBOL", raising=False)
    r = TickRecorder(CONTRACT, DAY)
    r.stats["clamped"] = 7
    r.append(_synthetic_day(DAY).iloc[:20])
    r.close(None)
    tickmod._clear_tick_caches()
    jourmod.SignalJournal(CONTRACT, DAY).record("vwap-upper-band-bounce", 20, None, [], [])

    body = live_recordings(None)
    row = body["recordings"][0]

    assert row["symbol"] == CONTRACT and row["date"] == DAY.isoformat()
    assert row["kind"] == "watched"
    assert row["signals"] == ["vwap-upper-band-bounce"]
    # Out of `stats` and into a field of its own: the plan flags a non-tiny
    # figure as a real finding, and it was only readable by opening a JSON file.
    assert row["clamped"] == 7
    assert [c["symbol"] for c in body["contracts"]] == [CONTRACT]


def test_the_contract_being_recorded_appears_before_it_has_a_day(live_store, monkeypatch):
    """Which is exactly the state in which a deadline is worth reading."""
    from api.routers.live import live_recordings

    monkeypatch.setenv("LIVE_SYMBOL", "NQU6")
    body = live_recordings(None)

    assert body["recordings"] == []
    assert [c["symbol"] for c in body["contracts"]] == ["NQU6"]
    assert body["contracts"][0]["recorded"] == 0


# --- the days behind the live one ------------------------------------------
# `/live/history` exists so the live chart is not stranded with only the session
# in progress on it. The interesting part is not the encoding — that is the codec
# the Simulator and the tape poll already share — but *which store answers*, and
# what it says about the days it could not find.


def test_the_cache_answers_before_the_live_store(live_store, monkeypatch):
    """One rule, in one place — `journal.sim.weekly.session_sums`' rule.

    The two stores overlap, and a day held in both has to draw the same bars on
    the live chart as it does in the Simulator. Cache first, or the two surfaces
    quietly disagree about a Tuesday.
    """
    from api.routers.live import _history_source

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: False)
    assert _history_source(CONTRACT, DAY) is None      # neither store has it

    _record(CONTRACT, DAY, _synthetic_day(DAY).iloc[:20])
    tickmod._clear_tick_caches()
    assert _history_source(CONTRACT, DAY) == "live"    # only the recording

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: True)
    assert _history_source(CONTRACT, DAY) == "cache"   # both -> the cache wins


def test_the_walk_back_skips_weekends_and_reports_the_holes(live_store, monkeypatch):
    """A week of calendar is routinely fewer sessions of tape.

    The live store has long contiguous stretches with nothing recorded, so the
    holes have to come back with the answer: gluing across one would draw a
    continuous chart out of a discontinuous week.
    """
    from api.routers.live import live_history_days

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: False)
    # Mon 2025-10-06 .. Fri 2025-10-10, with the Wednesday missing. DAY itself is
    # the Monday after, and is never its own context.
    for d in (date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 9), date(2025, 10, 10)):
        _record(CONTRACT, d, _synthetic_day(d).iloc[:5])
    tickmod._clear_tick_caches()

    body = live_history_days(symbol=CONTRACT, date_=DAY.isoformat(), days=5)

    assert [d["date"] for d in body["days"]] == [
        "2025-10-06", "2025-10-07", "2025-10-09", "2025-10-10",
    ]
    assert all(d["source"] == "live" for d in body["days"])
    # The Wednesday, and only it: the weekend is not a hole, and weekdays older
    # than the oldest day found were never part of the window.
    assert body["missing"] == ["2025-10-08"]


def test_the_walk_stops_at_the_count_it_was_asked_for(live_store, monkeypatch):
    """Oldest-first, and a shorter answer than requested is not an error."""
    from api.routers.live import live_history_days

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: False)
    for d in (date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8), date(2025, 10, 9)):
        _record(CONTRACT, d, _synthetic_day(d).iloc[:5])
    tickmod._clear_tick_caches()

    body = live_history_days(symbol=CONTRACT, date_=DAY.isoformat(), days=2)
    assert [d["date"] for d in body["days"]] == ["2025-10-08", "2025-10-09"]
    # The Friday is still a hole even though the walk had already found what it
    # was asked for: it sits between the newest context day and the session, so
    # what gets drawn is *not* contiguous with the live tape. That is precisely
    # the thing this list exists to say, and staying quiet about it because the
    # count was satisfied would be the chart lying by omission.
    assert body["missing"] == ["2025-10-10"]

    # Nothing behind it at all: every weekday walked is a genuine hole, and the
    # caller gets an empty list rather than a 404 — context is optional.
    empty = live_history_days(symbol=CONTRACT, date_="2025-09-01", days=5)
    assert empty["days"] == []
    assert len(empty["missing"]) > 0


def test_a_recorded_day_comes_back_as_tape(live_store, monkeypatch):
    """The same bytes `/simulator/session` and `/live/tape` ship.

    Decoded by the same function on the client, which is the whole reason a
    finished day, a growing day and a replayed day are one wire format.
    """
    from api.routers.live import live_history_session

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: False)
    df = _synthetic_day(DAY)
    _record(CONTRACT, DAY, df)
    tickmod._clear_tick_caches()

    body = live_history_session(symbol=CONTRACT, date_=DAY.isoformat(), tz="America/New_York")

    assert body["source"] == "live"
    assert body["n"] == len(df)
    # Self-contained, like every other block from this codec.
    assert body["dt"][0] == 0 and body["dp"][0] == 0
    assert body["session_start_ms"] < body["rth_open_ms"] < body["rth_close_ms"]
    assert body["session_end_ms"] >= body["rth_close_ms"]
    # It is drawn, never played: there is no start for a transport to seek to.
    assert "default_start_ms" not in body


def test_a_day_with_no_tape_is_a_404_not_an_empty_chart(live_store, monkeypatch):
    from api.routers.live import live_history_session
    from fastapi import HTTPException

    monkeypatch.setattr(tickmod, "has_rth", lambda s, d: False)
    with pytest.raises(HTTPException) as e:
        live_history_session(symbol=CONTRACT, date_=DAY.isoformat(), tz=None)
    assert e.value.status_code == 404
