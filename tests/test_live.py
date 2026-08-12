"""The live stack: the tape wire format, the in-memory session, and the freeze.

``tests/test_prefix_replay.py`` already guards the property shadow mode rests on
— that a partial run reproduces the prefix of a full one. This file guards the
machinery that *feeds* it, where the failures are quieter:

  - a delta block that decodes to different ticks than the session it was sliced
    out of would show as a chart that drifts, with no error anywhere;
  - a live frame cut to the wrong window re-phases every bar and moves the VWAP
    anchor to 18:00, silently, changing every number (``ticks.py``'s READ
    CONTRACT);
  - a regime checkpoint that moves as the day grows would let a gate's verdict
    change retroactively — the plan's "most likely way the feature ships broken
    and stays unnoticed";
  - and an artifact returned as None would send ``gates._regime_art`` to the
    *cached whole-day* read, handing every gate the finished day's answer at nine
    in the morning.

Runs over the real cached session where it needs a realistic tape, and skips if
the tick cache is cold.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from api.tape_codec import encode_ticks, zone_for  # noqa: E402
from journal.config import ET_TZ  # noqa: E402
from journal.live.session import LiveSession  # noqa: E402
from journal.live.shadow import ShadowRunner  # noqa: E402
from journal.sim import regime as regmod  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

DAY = date(2025, 10, 13)
CONTRACT = "NQZ5"
ZONE = zone_for("New York")


def _have_ticks() -> bool:
    return tickmod.has_rth(CONTRACT, DAY)


needs_ticks = pytest.mark.skipif(not _have_ticks(), reason="tick cache is cold")


def _decode(block: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """The client's ``decodeTape``, in Python: prefix-sum a block back to ticks.

    Deliberately a re-implementation of lib/growableTape's loop rather than a
    call into it — the point of the test is that the two agree, so sharing code
    would test nothing.
    """
    n = block["n"]
    t = np.cumsum(np.asarray(block["dt"], dtype="int64")) + block["t0"]
    dp = np.cumsum(np.asarray(block["dp"], dtype="int64"))
    px = (round(block["price0"] / 0.25) + dp) * 0.25
    return t[:n], px[:n], np.asarray(block["size"], dtype="int64")[:n], block["side"]


# --- the wire format --------------------------------------------------------

@needs_ticks
def test_a_slice_decodes_to_the_same_ticks_as_the_whole_tape():
    """A block is self-contained: rows [a, b) decode to exactly the rows the
    whole-frame encoding puts at [a, b).

    This is what lets the live poll hand back a slice and the client simply
    append it. If it were false the tape would be *continuous but wrong* — the
    prices would drift by whatever the block's base was off by, and nothing would
    raise.
    """
    frame = tickmod.cached_rth(CONTRACT, DAY)
    whole = _decode(encode_ticks(frame, ZONE, 0.25))
    a, b = 1000, 4000
    part = _decode(encode_ticks(frame.iloc[a:b], ZONE, 0.25))
    for w, p in zip(whole[:3], part[:3]):
        np.testing.assert_array_equal(w[a:b], p)
    assert whole[3][a:b] == part[3]


def test_an_empty_block_encodes_rather_than_raising():
    """The live poll asks for [n, n) every time it wakes and nothing has arrived.
    "Nothing" is the ordinary answer, not an error."""
    empty = pd.DataFrame({"ts_utc": pd.to_datetime([], utc=True), "price": [],
                          "size": [], "side": []})
    assert encode_ticks(empty, ZONE, 0.25)["n"] == 0


# --- the in-memory session --------------------------------------------------

@needs_ticks
def test_live_frames_are_the_windows_get_day_ticks_returns():
    """The READ CONTRACT holds for a live frame exactly as for a cached read.

    This is the load-bearing one. A frame that quietly carries the overnight in
    front of an RTH strategy re-phases every tick bar and moves the NY VWAP
    anchor to 18:00 — silently, with no error, changing the numbers of every RTH
    strategy on the shelf.

    Fed the way a feed delivers: one time-ordered stream, here the night and the
    session as they arrived up to the bell.
    """
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)

    s = LiveSession(CONTRACT, DAY, "test")
    s.append(pd.concat([on, rth], ignore_index=True))

    got = s.frame_for(overnight=False)
    want = tickmod.get_day_ticks(CONTRACT, DAY, include_overnight=False)
    pd.testing.assert_series_equal(got["ts_utc"], want["ts_utc"], check_dtype=False)
    np.testing.assert_allclose(got["price"], want["price"])

    # Globex: the night spliced in front, and the anchor therefore at 18:00.
    got_gx = s.frame_for(overnight=True)
    want_gx = tickmod.get_day_ticks(CONTRACT, DAY, include_overnight=True)
    pd.testing.assert_series_equal(got_gx["ts_utc"], want_gx["ts_utc"], check_dtype=False)

    # The night alone, which is what `compute_regime` is handed separately.
    assert len(s.overnight_frame()) == len(on)


@needs_ticks
def test_the_post_hour_rides_on_the_tape_but_never_reaches_an_engine():
    """The chart draws the post hour and the weekly seed reads it, so it is
    accepted onto the live tape — but no engine window may contain it. A Globex
    strategy is anchored at 18:00 and trades to the bell; appending 16:00-18:00
    would extend every session past the close it was written against.

    Asserted against the documented bounds rather than against
    ``get_day_ticks``, because the two disagree by one print here: the cached
    ``post`` parquet's first tick is at 19:59:59.9995 UTC, a hair *before* the
    20:00 boundary its own window declares, so the cached ``rth`` segment is
    missing a tick that is RTH by the [09:30, 16:00) rule. That is a seam in the
    cache's segmentation, not in this window logic — which is exactly why this
    test measures the rule and not the file.
    """
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    post = tickmod.cached_post(CONTRACT, DAY)

    s = LiveSession(CONTRACT, DAY, "test")
    s.append(pd.concat([on, rth, post], ignore_index=True))

    rth_open, rth_close = tickmod.session_bounds_utc(DAY)
    for overnight in (False, True):
        f = s.frame_for(overnight=overnight)
        assert f["ts_utc"].max() < rth_close
        lo = tickmod.overnight_bounds_utc(DAY)[0] if overnight else rth_open
        assert f["ts_utc"].min() >= lo


@needs_ticks
def test_appending_in_batches_is_the_same_tape_as_appending_at_once():
    """The feed publishes whatever has arrived each wake, so the batch boundaries
    are an artefact of scheduling and must not be visible in the result."""
    rth = tickmod.cached_rth(CONTRACT, DAY).iloc[:5000]
    at_once = LiveSession(CONTRACT, DAY, "a")
    at_once.append(rth)
    in_bits = LiveSession(CONTRACT, DAY, "b")
    for lo in range(0, 5000, 337):  # deliberately not a round number
        in_bits.append(rth.iloc[lo:lo + 337])
    assert at_once.n == in_bits.n == 5000
    np.testing.assert_allclose(at_once.slice()["price"], in_bits.slice()["price"])


def test_a_slice_past_the_end_is_empty_not_an_error():
    """A client polling `since=n` when nothing new has arrived is the common
    case, and it should get an empty block rather than a 400."""
    s = LiveSession(CONTRACT, DAY, "test")
    assert s.slice(99999).empty
    assert s.last_ts() is None


# --- the listener seam ------------------------------------------------------
# What the SSE stream stands on: appends and close() nudge subscribers, cheaply
# and from whatever thread the producer happens to be.

def _tick_frame(k: int = 3) -> pd.DataFrame:
    ts = pd.date_range("2025-10-13 14:00", periods=k, freq="s", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "price": [25000.0 + i for i in range(k)],
                         "size": [1] * k, "side": ["B"] * k})


def test_append_nudges_a_subscriber_and_empty_appends_do_not():
    s = LiveSession(CONTRACT, DAY, "test")
    calls = []
    s.subscribe(lambda: calls.append(s.n))
    assert s.append(_tick_frame()) == 3
    assert calls == [3]
    # The empty append is the poll-idle case: no rows, no wake.
    s.append(pd.DataFrame({"ts_utc": pd.to_datetime([], utc=True), "price": [],
                           "size": [], "side": []}))
    assert calls == [3]


def test_close_nudges_subscribers():
    """Closing is the only signal that the tape will never append again — a
    session is replaced by closing the old one, so a stream that missed this
    would sleep forever on a finished tape."""
    s = LiveSession(CONTRACT, DAY, "test")
    calls = []
    s.subscribe(lambda: calls.append("closed" if s.closed else "open"))
    s.close()
    assert calls == ["closed"]


def test_a_raising_listener_breaks_neither_the_producer_nor_its_peers():
    s = LiveSession(CONTRACT, DAY, "test")
    heard = []

    def bad() -> None:
        raise RuntimeError("Event loop is closed")  # the --reload straggler

    s.subscribe(bad)
    s.subscribe(lambda: heard.append(True))
    assert s.append(_tick_frame()) == 3  # append returns normally
    assert heard == [True]  # and the healthy peer still fired


def test_unsubscribe_is_idempotent_and_stops_the_nudges():
    s = LiveSession(CONTRACT, DAY, "test")
    calls = []
    fn = lambda: calls.append(True)  # noqa: E731
    s.subscribe(fn)
    s.unsubscribe(fn)
    s.unsubscribe(fn)  # dropping twice is a no-op, not an error
    s.append(_tick_frame())
    assert calls == []


def test_the_intended_bridge_wakes_an_event_loop_across_threads():
    """The real subscriber shape, end to end: a producer thread appends, the
    nudge is `loop.call_soon_threadsafe(event.set)`, and a coroutine waiting on
    the event on another thread's loop wakes. This is the whole SSE bridge in
    miniature — if this test cannot hold, the stream cannot either."""
    import asyncio
    import threading

    s = LiveSession(CONTRACT, DAY, "test")

    async def waiter() -> bool:
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        s.subscribe(lambda: loop.call_soon_threadsafe(wake.set))
        t = threading.Thread(target=s.append, args=(_tick_frame(),))
        t.start()
        try:
            await asyncio.wait_for(wake.wait(), timeout=5.0)
        finally:
            t.join()
        return s.n == 3

    assert asyncio.run(waiter())


# --- the regime seam --------------------------------------------------------

@needs_ticks
def test_injected_frames_produce_the_cached_artifact():
    """Frame injection is a seam, not a second implementation: handing
    ``compute_regime`` the very frames it would have read must change nothing."""
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    assert regmod.compute_regime(CONTRACT, DAY, frames=(on, rth)) == \
        regmod.compute_regime(CONTRACT, DAY)


@needs_ticks
def test_a_checkpoint_does_not_move_as_the_day_grows():
    """A checkpoint computed from a short prefix equals the same checkpoint
    computed from the whole day.

    This is what makes the live freeze a guarantee rather than a hope: each
    checkpoint already slices to the bars that had closed by its own cutoff, so
    extending the prefix cannot reach back and change one. If this ever fails, a
    gate's verdict can change retroactively and shadow mode is reporting a
    decision nobody could have made.
    """
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    cut = pd.Timestamp(f"{DAY.isoformat()} 11:00", tz=ET_TZ).tz_convert("UTC")
    prefix = rth[rth["ts_utc"] < cut].reset_index(drop=True)

    full = regmod.compute_regime(CONTRACT, DAY, frames=(on, rth))
    part = regmod.compute_regime(CONTRACT, DAY, frames=(on, prefix))
    # Every checkpoint whose cutoff is behind 11:00 must be identical.
    for name in ("09:30", "09:45", "10:30"):
        assert part["checkpoints"][name] == full["checkpoints"][name], name
    # ...and "12:00" must NOT be, or the prefix was not actually short.
    assert part["checkpoints"]["12:00"] != full["checkpoints"]["12:00"]


# --- the freeze -------------------------------------------------------------

@needs_ticks
def test_the_live_artifact_only_carries_checkpoints_that_have_been_reached():
    """A checkpoint present before its cutoff would be a fabricated verdict — the
    morning wearing noon's label. Absent, the gate blind-fails-closed, and since
    every regime gate only vetoes from its own checkpoint minute onwards, it is
    inert until it can honestly answer."""
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    cut = pd.Timestamp(f"{DAY.isoformat()} 10:00", tz=ET_TZ).tz_convert("UTC")

    s = LiveSession(CONTRACT, DAY, "test")
    s.append(pd.concat([on, rth[rth["ts_utc"] < cut]], ignore_index=True))
    art = ShadowRunner(s)._live_regime(s.overnight_frame(), s.frame_for(overnight=False))

    assert set(art["checkpoints"]) == {"09:30", "09:45"}
    assert "10:30" not in art["checkpoints"]
    assert "eod" not in art["checkpoints"]


@needs_ticks
def test_a_frozen_checkpoint_is_not_recomputed_when_the_day_grows():
    """Freezing is belt-and-braces over the prefix-stability above: once a
    checkpoint is in, later passes must not touch it at all."""
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    ten = pd.Timestamp(f"{DAY.isoformat()} 10:00", tz=ET_TZ).tz_convert("UTC")

    s = LiveSession(CONTRACT, DAY, "test")
    s.append(pd.concat([on, rth[rth["ts_utc"] < ten]], ignore_index=True))
    runner = ShadowRunner(s)
    early = runner._live_regime(s.overnight_frame(), s.frame_for(overnight=False))
    sentinel = {"class": "sentinel"}
    runner._frozen["09:45"] = sentinel

    s.append(rth[rth["ts_utc"] >= ten])
    later = runner._live_regime(s.overnight_frame(), s.frame_for(overnight=False))

    # The sentinel survived, so 09:45 was never recomputed...
    assert later["checkpoints"]["09:45"] is sentinel
    # ...while the checkpoints the day has now reached did get added.
    assert "10:30" in later["checkpoints"] and "10:30" not in early["checkpoints"]


@needs_ticks
def test_at_the_close_the_live_artifact_matches_the_settled_one():
    """The freeze must converge. Every checkpoint is frozen from the prefix that
    had reached it, so by the closing bell the live artifact's checkpoints have
    to be the ones ``compute_regime`` writes for the settled day — otherwise the
    day's live verdicts and its stored verdicts describe different sessions, and
    Phase 6's reconciliation has nothing to reconcile against.
    """
    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)
    post = tickmod.cached_post(CONTRACT, DAY)
    whole = pd.concat([on, rth, post], ignore_index=True)

    s = LiveSession(CONTRACT, DAY, "test")
    runner = ShadowRunner(s)
    # Five bites, so every checkpoint is frozen from a different prefix. The last
    # one runs past the bell, which is what a live feed does and what freezes
    # `eod` — a session in progress at 15:59 genuinely has no closing verdict yet.
    for hh in (10, 11, 13, 16, 18):
        cut = pd.Timestamp(f"{DAY.isoformat()} {hh:02d}:00", tz=ET_TZ).tz_convert("UTC")
        have = s.last_ts()
        seg = whole[whole["ts_utc"] < cut] if have is None else \
            whole[(whole["ts_utc"] > have) & (whole["ts_utc"] < cut)]
        s.append(seg)
        live = runner._live_regime(s.overnight_frame(), s.frame_for(overnight=False))

    # Compared against one whole-day computation over the *same* tape, which is
    # what convergence means. Deliberately not against the cached rth/on
    # segments: those disagree with the live windows by the one-print seam
    # documented above, and this test is about the freeze, not about the cache.
    settled = regmod.compute_regime(CONTRACT, DAY,
                                    frames=(s.overnight_frame(),
                                            s.frame_for(overnight=False)))
    assert set(live["checkpoints"]) == set(settled["checkpoints"])
    assert "eod" in live["checkpoints"]
    for name, kp in settled["checkpoints"].items():
        assert live["checkpoints"][name] == kp, name


@needs_ticks
def test_the_runner_reproduces_the_backtest_over_the_whole_day():
    """The headline claim of Phase 4, end to end.

    A shadow signal must be what the backtest would say — not approximately, and
    not "the same shape". This feeds a whole cached session through the live
    machinery (in-memory tape → window slicing → frozen regime → prefix re-run)
    and checks every strategy against the settled artifact and a direct call. Any
    disagreement means the *plumbing* has changed a number, which is precisely
    what shadow mode cannot be allowed to do.
    """
    from journal.sim import live_shadow

    on = tickmod.cached_overnight(CONTRACT, DAY)
    rth = tickmod.cached_rth(CONTRACT, DAY)

    s = LiveSession(CONTRACT, DAY, "test")
    s.append(pd.concat([on, rth], ignore_index=True))
    runner = ShadowRunner(s)
    if not runner.watches:
        pytest.skip("no strategy has a baseline pinned")
    runner.run_due()

    settled = regmod.compute_regime(CONTRACT, DAY, frames=(on, rth))
    for w in runner.watches:
        assert w.error is None, f"{w.slug}: {w.error}"
        frame = tickmod.get_day_ticks(CONTRACT, DAY,
                                      include_overnight=(w.session == "globex"))
        want, _v, _b, _bd = live_shadow.shadow_session(
            w.slug, w.cfg, DAY, frame, regime=settled)
        assert len(w.trades) == len(want), w.slug
        for got, exp in zip(w.trades, want):
            assert got["entry_ts_utc"] == exp["entry_ts_utc"], w.slug
            assert got["avg_entry"] == exp["avg_entry"], w.slug
            assert got["exit_reason"] == exp["exit_reason"], w.slug
            assert got["net_pnl"] == exp["net_pnl"], w.slug


def test_the_live_artifact_is_never_none():
    """``gates._regime_art`` falls back to ``get_regime`` — a cached read of the
    *whole settled day* — when the injected artifact is None. On a fake feed whose
    source is a cached day that file exists, so a None here would hand every gate
    the finished day's answer at nine in the morning: lookahead, silent, and
    flattering. An empty-checkpoint artifact is the honest empty answer.
    """
    s = LiveSession(CONTRACT, DAY, "test")  # no ticks at all
    art = ShadowRunner(s)._live_regime(None, None)
    assert art is not None
    assert art["checkpoints"] == {}
    assert art["live"] is True


# --- the shelf's own switch -------------------------------------------------


def _tiny_session() -> LiveSession:
    s = LiveSession(CONTRACT, DAY, "test")
    ts = pd.date_range(pd.Timestamp("2025-10-13 14:00", tz="UTC"), periods=10,
                       freq="1s")
    s.append(pd.DataFrame({"ts_utc": ts, "price": np.full(10, 100.0),
                           "size": np.ones(10, dtype="uint32"),
                           "side": ["B"] * 10}))
    return s


def test_a_disabled_runner_runs_nothing_and_says_so():
    """"Off" and "nothing has signalled" render identically without the flag, and
    which one you are looking at changes what the empty list means."""
    runner = ShadowRunner(_tiny_session(), enabled=False)
    runner.run_due()
    assert runner.snapshot()["enabled"] is False
    assert all(not w.ran for w in runner.watches)
    runner.start()
    assert runner._thread is None      # start is a no-op while disabled


def test_a_runner_restarted_after_a_stop_actually_runs():
    """``stop`` sets the event the loop reads; a ``start`` that did not clear it
    would spawn a thread that exits on its first pass — silently, and looking
    exactly like a market with no setups in it."""
    runner = ShadowRunner(_tiny_session(), enabled=False)
    runner.stop()                       # the event is now set
    runner.set_enabled(True)
    try:
        assert runner._thread is not None and runner._thread.is_alive()
        assert not runner._stop.is_set()
    finally:
        runner.set_enabled(False)
    assert runner._thread is None


def test_toggling_the_shelf_keeps_what_it_last_said():
    """The passes stop; the answers stand. A panel that blanked on the switch
    would throw away the morning's readings to report a setting."""
    runner = ShadowRunner(_tiny_session(), enabled=True)
    runner.stop()                       # no thread, so nothing races the assert
    if runner.watches:
        runner.watches[0].ran = True
        runner.watches[0].trades = [{"direction": "long"}]
    runner.set_enabled(False)
    snap = runner.snapshot()
    assert snap["enabled"] is False
    if runner.watches:
        assert snap["strategies"][0]["trades"] == [{"direction": "long"}]
