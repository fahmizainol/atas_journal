"""The SSE tape stream: /live/tape/stream against a real LiveSession.

The stream's promises are the poll's promises — same payload, same cursor
contract — plus three of its own: an event goes out when an append lands (not
when a timer fires), a session swap surfaces as ``reset`` without the client
asking, and a dead live state is said out loud as ``event: gone`` rather than
left to time out.

DRIVEN AS A GENERATOR, NOT OVER HTTP. TestClient cannot serve an unfinished
stream — its transport runs the app coroutine to completion before handing back
so much as the headers, so connecting to an endless SSE endpoint deadlocks the
test at ``client.stream(...)``. The endpoint function is called directly
instead and its ``body_iterator`` consumed one frame at a time, which is
*more* faithful where it matters: every await carries a hard timeout, appends
can be interleaved between reads, and ``aclose()`` is exactly the
GeneratorExit a browser disconnect delivers through starlette's cancellation —
so the cleanup test exercises the real disconnect path, not a stand-in. What
this harness does not cover is HTTP framing itself, which is starlette's
StreamingResponse and is verified in the browser.

Only the module global holding the running session is faked; the session, the
codec and the generator are the real ones.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from api.routers import live as live_router  # noqa: E402
from journal.live import state as statemod  # noqa: E402
from journal.live.session import LiveSession  # noqa: E402

DAY = date(2025, 10, 13)
CONTRACT = "NQZ5"

READ_TIMEOUT_S = 5.0


def _ticks(k: int, start_s: int = 0) -> pd.DataFrame:
    ts = pd.date_range("2025-10-13 14:00", periods=k, freq="s",
                       tz="UTC") + pd.Timedelta(seconds=start_s)
    return pd.DataFrame({"ts_utc": ts, "price": [25000.0 + (i % 7) * 0.25 for i in range(k)],
                         "size": [1 + i % 3 for i in range(k)], "side": ["B"] * k})


@pytest.fixture
def live_state(monkeypatch):
    monkeypatch.setattr(statemod, "_current", None, raising=False)
    return monkeypatch


def _install(monkeypatch, session: LiveSession) -> None:
    monkeypatch.setattr(statemod, "_current",
                        SimpleNamespace(session=session), raising=False)


class _Stream:
    """The endpoint's generator, held open across reads."""

    def __init__(self, since: int = 0, gen: str | None = None,
                 tz: str | None = None, last_id: str | None = None) -> None:
        self._args = (since, gen, tz)
        headers = [(b"last-event-id", last_id.encode())] if last_id else []
        self._request = Request({"type": "http", "method": "GET",
                                 "headers": headers, "query_string": b"",
                                 "path": "/api/live/tape/stream"})
        self.response = None
        self._it = None

    async def open(self):
        since, gen, tz = self._args
        self.response = await live_router.live_tape_stream(
            self._request, since=since, gen=gen, tz=tz)
        self._it = self.response.body_iterator
        return self

    async def next_event(self, timeout: float = READ_TIMEOUT_S) -> dict:
        """The next non-comment frame as {id?, event?, data?}."""
        while True:
            frame = await asyncio.wait_for(self._it.__anext__(), timeout)
            if frame.startswith(":"):
                continue  # heartbeat
            ev: dict = {}
            for line in frame.strip("\n").split("\n"):
                field, _, value = line.partition(":")
                ev[field] = value.lstrip(" ")
            return ev

    async def close(self) -> None:
        # GeneratorExit into the generator — the same thing starlette's task
        # cancellation delivers when a browser hangs up.
        await self._it.aclose()


def _block(ev: dict) -> dict:
    assert "data" in ev, f"event has no data: {ev}"
    return json.loads(ev["data"])


# --- the payload is the poll's payload ---------------------------------------

def test_first_event_is_the_poll_response_for_the_same_slice(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(50))
    _install(live_state, s)
    poll = live_router.live_tape(since=0, gen=None, tz="New York")

    async def run():
        st = await _Stream(tz="New York").open()
        try:
            first = await st.next_event()
            assert first["id"] == f"{s.gen}|50"
            assert _block(first) == poll
            assert st.response.media_type == "text/event-stream"
            assert st.response.headers["cache-control"] == "no-cache"
        finally:
            await st.close()

    asyncio.run(run())


def test_an_append_is_pushed_and_next_advances(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(10))
    _install(live_state, s)

    async def run():
        st = await _Stream().open()
        try:
            first = _block(await st.next_event())
            assert (first["since"], first["next"], first["n"]) == (0, 10, 10)
            # The bridge as deployed: the append lands on another thread, the
            # nudge crosses into this loop, the stream wakes with the block.
            await asyncio.to_thread(s.append, _ticks(4, start_s=100))
            second = _block(await st.next_event())
            assert (second["since"], second["next"], second["n"]) == (10, 14, 4)
            assert second["reset"] is False
        finally:
            await st.close()

    asyncio.run(run())


# --- reconnect ----------------------------------------------------------------

def test_last_event_id_resumes_at_the_right_row(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(30))
    _install(live_state, s)

    async def run():
        # The gen itself contains ':' — exactly why the id separator is '|'.
        # since=0 is what a reconnecting EventSource replays in the URL; the
        # header must beat it.
        st = await _Stream(since=0, last_id=f"{s.gen}|20").open()
        try:
            first = _block(await st.next_event())
            assert (first["since"], first["next"], first["reset"]) == (20, 30, False)
        finally:
            await st.close()

    asyncio.run(run())


def test_a_foreign_gen_resets_from_row_zero(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-2")
    s.append(_ticks(15))
    _install(live_state, s)

    async def run():
        st = await _Stream(last_id="NQZ5:2025-10-13:99-1|8").open()
        try:
            first = _block(await st.next_event())
            assert (first["reset"], first["since"], first["next"]) == (True, 0, 15)
            assert first["gen"] == s.gen
        finally:
            await st.close()

    asyncio.run(run())


# --- the world moving under the stream ---------------------------------------

def test_a_session_swap_surfaces_as_reset_from_the_new_session(live_state):
    old = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    old.append(_ticks(5))
    _install(live_state, old)

    async def run():
        st = await _Stream().open()
        try:
            assert _block(await st.next_event())["gen"] == old.gen
            fresh = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-2")
            fresh.append(_ticks(8, start_s=200))
            _install(live_state, fresh)
            # The roll's own order: new state in place, then the old session
            # closes — and close() notifying is what wakes the stream.
            old.close()
            ev = _block(await st.next_event())
            assert (ev["gen"], ev["reset"], ev["since"], ev["next"]) == (
                fresh.gen, True, 0, 8)
        finally:
            await st.close()

    asyncio.run(run())


def test_a_stopped_live_state_says_gone(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(3))
    _install(live_state, s)

    async def run():
        st = await _Stream().open()
        try:
            _block(await st.next_event())
            began = time.monotonic()
            live_state.setattr(statemod, "_current", None, raising=False)
            ev = await st.next_event()
            assert ev.get("event") == "gone"
            # Detection rides the 1s control-plane wait, not a heartbeat.
            assert time.monotonic() - began < 3.0
        finally:
            await st.close()

    asyncio.run(run())


def test_connecting_with_no_live_state_is_gone_not_an_error(live_state):
    async def run():
        # The endpoint answers 200 with a gone event rather than raising —
        # a non-200 would put EventSource into retry-forever.
        st = await _Stream().open()
        try:
            assert (await st.next_event()).get("event") == "gone"
        finally:
            await st.close()

    asyncio.run(run())


# --- catch-up chunking --------------------------------------------------------

def test_a_giant_catchup_arrives_as_chained_chunks(live_state, monkeypatch):
    # The real chunk size would make this test seed 200k+ rows for no extra
    # coverage; what is under test is the chaining, not the constant.
    monkeypatch.setattr(live_router, "_SSE_CHUNK", 40)
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(100))
    _install(live_state, s)

    async def run():
        st = await _Stream().open()
        try:
            blocks = [_block(await st.next_event()) for _ in range(3)]
            assert [b["n"] for b in blocks] == [40, 40, 20]
            # Each block starts exactly where the last ended: the cursor
            # contract, now driven server-side.
            assert [(b["since"], b["next"]) for b in blocks] == [
                (0, 40), (40, 80), (80, 100)]
        finally:
            await st.close()

    asyncio.run(run())


# --- cleanup ------------------------------------------------------------------

def test_a_disconnect_leaves_no_listener_behind(live_state):
    s = LiveSession(CONTRACT, DAY, "NQZ5:2025-10-13:7-1")
    s.append(_ticks(3))
    _install(live_state, s)

    async def run():
        st = await _Stream().open()
        try:
            _block(await st.next_event())
            assert len(s._listeners) == 1
        finally:
            await st.close()

    asyncio.run(run())
    assert not s._listeners
