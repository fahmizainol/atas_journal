"""The real feed: Rithmic's ticker plant, trades only.

The fake feed proved the live stack against a day that already happened. This is
the same shape with an exchange behind it — a background thread running an
asyncio client, publishing batches of prints into whatever the router hands them
to. It knows nothing about sessions, recorders or strategies; ``state.py`` owns
all three, which is what lets the 18:00 ET session roll be decided in one place.

FIVE THINGS THIS GETS RIGHT ON PURPOSE, each of them measured rather than assumed
during the access probe (docs/live-shadow-plan.md § Rithmic access):

1. **TICKER_PLANT only.** ``client.connect()`` defaults to all four plants and
   would open the ORDER plant. Shadow mode never does — that is decision 2, and
   it is both a lighter conformance scope and a different question to ask a prop
   firm than "may I automate orders".
2. **LAST_TRADE only, no BBO.** Quotes run 12-21x the trade count and the engine's
   tick schema is ``(ts_utc, price, size, side)`` — trades, no bid/ask anywhere.
   Skipping BBO drops ~95% of message volume and loses nothing any strategy reads.
3. **Exchange timestamps, never the host clock.** The WSL2 host measured 1.7-2.8s
   behind Rithmic, and the offset moved between runs; Rithmic's own hop is
   0.3-0.4ms. So a tick is stamped from ``source_ssboe``/``source_nsecs`` (the
   exchange's stamp, the same instant Databento's ``ts_event`` carries, which is
   what makes Phase 6's print-for-print comparison meaningful), falling back to
   ``ssboe``/``usecs`` (Rithmic's send stamp) and never to ``time.time()``.
4. **The password is filtered at the handler.** ``async_rithmic`` logs the whole
   outgoing ``RequestLogin`` — password included — at ERROR on *any* rejected
   login. Handler level, not logger level: records come from ``rithmic.plant.*``
   children and a filter on an ancestor logger is skipped during propagation.
5. **The tape is forced monotonic.** See ``_clamped``.

THE BACKFILL. A live subscription delivers prints from the moment it is opened,
so a feed connected at nine in the morning holds a session that began at nine —
and the engine reads a *frame*, so every strategy would then be simulating a day
that opened hours late, silently and with plausible numbers. That is the same
failure ``state.resume()`` exists to prevent, seen from the other side: resume
recovers what *this* recorder wrote, and nothing recovers a stretch nobody was
connected for. So on connect the feed replays that stretch off Rithmic's history
plant and publishes it before the live prints — see ``_backfill``, and
docs/live-shadow-plan.md § Tick replay for what was measured before it was built.

THE AGGRESSOR MAPPING IS RECORDED, NOT JUST APPLIED. Rithmic sends an int, and
its own protobuf names them (``BUY=1``, ``SELL=2``) — so the enum is read from
the schema rather than hardcoded from the two values the probe happened to see.
What is still an assumption is that Rithmic's *aggressor* and Databento's *side*
mean the same thing; the measurement behind ``'B'`` = buy aggressor was made on
Databento prints (they sit ~0.35pt above the local mid), not on these. So every
recorded tick keeps the raw int in ``agg_raw`` alongside the mapped ``side``, and
Phase 6 cross-tabs the two against a Databento day for the same date. If the
mapping is ever shown backwards, a recorded day can be re-derived instead of
re-recorded — the evidence is kept, not just the interpretation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import Counter, deque

import numpy as np
import pandas as pd

# How often the publisher drains what has arrived. Same figure as the fake feed:
# fine enough that the chart advances smoothly, coarse enough that a busy market
# is one batch rather than one call per print.
PUBLISH_S = 0.1
# Reconnect backoff, seconds — doubling to the cap. The daily maintenance halt
# (17:00-18:00 ET) is not a disconnect, so this only ever runs on a real fault.
RECONNECT_MIN_S = 2.0
RECONNECT_MAX_S = 60.0
# How long the join waits for the subscription's first print before deciding the
# market is quiet. Long enough that a slow tape still gives an exact join, short
# enough that connecting into a halt is not a stall.
JOIN_WAIT_S = 5.0

# How many recent prints the timing readout keeps. Bounded on purpose: what a
# latency figure is worth saying is what the feed is doing *now*, and an average
# taken over a whole session hides the minute it stalled inside thirteen hours of
# healthy tape. At NQ rates this is a window of seconds, which is the point.
TIMING_WINDOW = 2000

# (ts_utc, price, size, side) is the engine's schema; agg_raw is the evidence
# behind `side` and is dropped again by every read that feeds an engine.
FEED_COLS = ["ts_utc", "price", "size", "side", "agg_raw"]

# How wide to hold the backfill off a tick the tape already has.
#
# Measured, not chosen: a replayed print and the same print received live are
# stamped a median 287µs apart, p90 ~0.9ms, max 6.7ms (two runs of
# demo/rithmic_history_probe.py). The replay carries Rithmic's stamp where the
# live path carries the exchange's, so the *same trade* looks slightly later in
# the replay — and a seam cut exactly at the last known tick would therefore
# re-admit it as a new one.
#
# The bias is deliberate and one-directional: cutting wide drops at most a few
# real prints at the join, cutting tight duplicates them. A missing print is a
# trade absent from the profile; a duplicated one is volume that never traded,
# printed at a price twice, in a tape whose whole claim is that it is what
# happened.
SEAM_SLACK_NS = 10_000_000  # 10ms

# A replay bar has no aggressor field — only the bar's volume split by book
# side. Measured against the live aggressor int on matched prints, twice, with
# zero off-diagonal (docs/live-shadow-plan.md § Tick replay): bid_volume is the
# BUY aggressor and ask_volume is the SELL aggressor. That is the opposite of
# the obvious reading — a buy lifts the offer, so surely ask_volume? — which is
# exactly why it is measured here rather than reasoned about.
_REPLAY_SIDE_FIELD = {"bid_volume": "BUY", "ask_volume": "SELL"}


class RedactSecrets(logging.Filter):
    """Scrub a secret out of anything the rithmic logger emits.

    Not paranoia: a rejected login prints the request it sent, password and all.
    Nothing can stop the library logging; filtering is the one place that catches
    every path it might take.
    """

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secret:
            return True
        if isinstance(record.msg, str) and self.secret in record.msg:
            record.msg = record.msg.replace(self.secret, "***")
        if record.args:
            record.args = tuple(
                a.replace(self.secret, "***") if isinstance(a, str) else a
                for a in record.args)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc.args = tuple(a.replace(self.secret, "***") if isinstance(a, str) else a
                             for a in exc.args)
        return True


def install_redaction(secret: str) -> None:
    """Attach the filter to every handler that could carry a login request.

    Both trees: ``async_rithmic`` installs its own handler on ``rithmic``, and
    anything the app configured sits on the root.
    """
    f = RedactSecrets(secret)
    for name in ("", "rithmic"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(f)


# The client's kwarg name for each setting, and the environment variable it comes
# from. Kept as one mapping because the two disagree — `url` is read from
# RITHMIC_GATEWAY — and deriving the variable name from the kwarg (the first
# version of this) told anyone who hit it to go and set RITHMIC_URL, which does
# not exist and would not have helped.
_ENV_KEYS = {
    "user": "RITHMIC_USER",
    "password": "RITHMIC_PASSWORD",
    "system_name": "RITHMIC_SYSTEM_NAME",
    "url": "RITHMIC_GATEWAY",
    "app_name": "RITHMIC_APP_NAME",
    "app_version": "RITHMIC_APP_VERSION",
}
_REQUIRED = ("user", "password", "system_name", "url")
_DEFAULTS = {"app_name": "atas_journal_shadow", "app_version": "0.1"}


def credentials(**overrides) -> dict:
    """Rithmic login from the environment, with the same keys the probe uses.

    ``load_env()`` first, and that is not belt-and-braces: this repo does **not**
    load ``.env`` at import. ``config.load_dotenv`` sits inside ``load_env()`` and
    every consumer calls it before reading its own keys, so a module that went
    straight to ``os.getenv`` would find nothing however carefully the file was
    filled in — which is exactly what happened, and it reported the credentials
    as missing rather than as unread.

    Raises ``LookupError`` naming the variables that are missing, rather than
    letting the client fail at the socket with something less useful.
    """
    from ..config import load_env

    load_env()
    creds = {k: os.getenv(env, _DEFAULTS.get(k)) for k, env in _ENV_KEYS.items()}
    creds.update({k: v for k, v in overrides.items() if v})
    missing = [_ENV_KEYS[k] for k in _REQUIRED if not creds[k]]
    if missing:
        raise LookupError(
            "missing Rithmic credentials: " + ", ".join(missing)
            + " — see .env.example, and `uv run python demo/rithmic_smoke.py "
              "--discover` for the gateway and system names")
    return creds


def _aggressor_map() -> dict[int, str]:
    """Rithmic's aggressor enum -> the engine's side letter, read off the schema.

    ``LastTrade``'s own enum names its values, so this is Rithmic's declaration
    rather than a guess from the two ints the probe observed live. What it maps
    *to* is the assumption — see this module's docstring — and it is the thing
    Phase 6 checks.
    """
    from async_rithmic.protocol_buffers.last_trade_pb2 import LastTrade

    e = LastTrade.DESCRIPTOR.enum_types_by_name["TransactionType"]
    return {e.values_by_name["BUY"].number: "B",
            e.values_by_name["SELL"].number: "A"}


def _replay_aggressor_map() -> dict[str, int]:
    """Replay's volume-split field -> the same aggressor int a live print carries.

    Goes through the enum for the same reason ``_aggressor_map`` does: what was
    measured is that a bar's ``bid_volume`` corresponds to the aggressor Rithmic
    calls BUY, and naming it that way keeps the finding legible if the numbers
    ever move. A backfilled tick therefore lands in ``agg_raw`` in the same
    alphabet as a live one, and Phase 6's cross-tab reads both without knowing
    which is which.
    """
    from async_rithmic.protocol_buffers.last_trade_pb2 import LastTrade

    e = LastTrade.DESCRIPTOR.enum_types_by_name["TransactionType"]
    return {field: e.values_by_name[name].number
            for field, name in _REPLAY_SIDE_FIELD.items()}


def _num(v) -> int:
    """One numeric field off a replay bar.

    ``MessageToDict`` renders uint64 as a JSON *string* and leaves a zero-valued
    field out altogether, so both the type and the absence have to be handled —
    ``int(bar["volume"])`` alone raises on the quiet bars.
    """
    return 0 if v is None else int(v)


def replay_frame(bars: list[dict], agg_num: dict[str, int],
                 agg_side: dict[int, str]) -> pd.DataFrame:
    """Historical tick bars -> the same frame shape the live drain publishes.

    One bar is one print: the request is ``TICK_BAR`` with specifier ``"1"``, and
    the probe found all 84,191 bars of a session carrying ``num_trades == 1``.
    Bars that carry more are still admitted — an aggregate is a real trade at a
    real price and dropping it would be a hole — but they are counted by the
    caller, because a replay that started aggregating would make the tape
    something other than prints.

    Stamps come from ``data_bar_ssboe``/``data_bar_usecs``, which are repeated:
    one entry per constituent print. The first is the bar's own instant, and it
    is the one that matched the live tape print for print in the probe.
    """
    rows = []
    for b in bars or []:
        ss = b.get("data_bar_ssboe") or []
        if not ss:
            continue
        us = b.get("data_bar_usecs") or []
        agg = 0
        for field, num in agg_num.items():
            if _num(b.get(field)):
                # A bar with volume on both sides has no single aggressor; left
                # at 0 it maps to side "N", which is what an unknown side is
                # already called everywhere else.
                agg = 0 if agg else num
        rows.append((
            int(ss[0]) * 1_000_000_000 + int(us[0] if us else 0) * 1_000,
            float(b.get("close_price") or 0.0),
            _num(b.get("volume")),
            agg,
            _num(b.get("num_trades")),
        ))
    if not rows:
        return pd.DataFrame({c: [] for c in FEED_COLS + ["num_trades"]})
    rows.sort(key=lambda r: r[0])
    agg_arr = np.fromiter((r[3] for r in rows), dtype="int16", count=len(rows))
    return pd.DataFrame({
        "ts_utc": pd.to_datetime(np.fromiter((r[0] for r in rows), dtype="int64",
                                             count=len(rows)), utc=True),
        "price": np.fromiter((r[1] for r in rows), dtype="float64", count=len(rows)),
        "size": np.fromiter((r[2] for r in rows), dtype="uint32", count=len(rows)),
        "side": pd.Series(agg_arr).map(agg_side).fillna("N").to_numpy(dtype="U1"),
        "agg_raw": agg_arr,
        "num_trades": np.fromiter((r[4] for r in rows), dtype="int64", count=len(rows)),
    })


# The most one replay request may ask for.
#
# Measured, and it is the difference between a sweep that works in one pass and
# one that takes three. Asking for a whole session is what fails: a 24h request
# came back truncated at exactly 90,000 bars on one attempt and complete at
# 451,212 on the next, and an earlier one at exactly 50,000 — round numbers,
# intermittent, and silent. Every window of 6h and under answered reliably
# (30k bars in 7-10s), and throughput is not the constraint: 40 back-to-back
# requests in 57s all succeeded. So the limit is what a single request asks for,
# and three hours leaves margin on the largest sessions seen (1.1M prints, so
# ~140k to a window, against a 243k single response that came back fine).
REPLAY_WINDOW_NS = 3 * 3600 * 1_000_000_000

# How many replay calls one range may take before it is called uncovered. A
# session is eight windows plus a continuation or two; the cap exists so a replay
# that answers but never advances cannot spin forever.
REPLAY_MAX_CALLS = 200

# How long a replay may go quiet before the client gives up on it. The library
# defaults to 5s, which is a stall timer rather than a total, and it is still too
# tight: harvesting a month of NQ, four of the busiest sessions (500k-1.1M
# prints) died on it while their neighbours came back fine. A day that times out
# is a day left unfetched, so this is a data-loss setting, not a latency one.
REPLAY_IDLE_S = 30.0

# How long to wait before asking an empty window a second time. Long enough to
# be a different moment, short enough that a holiday's worth of empty windows
# costs seconds rather than minutes.
EMPTY_RETRY_S = 1.0


async def replay_into(client, symbol: str, exchange: str, from_ns: int,
                      until_ns: int, agg_num: dict[str, int], agg_side: dict[int, str],
                      publish, max_calls: int | None = None) -> dict:
    """Fetch ``[from_ns, until_ns)`` in as many calls as it takes, publishing each.

    ONE CALL IS NOT ONE RANGE, and finding that out cost a day of data. Rithmic's
    replay can return a **prefix** of what was asked for and say nothing about it:
    a request for the whole of 2026-06-16 came back with exactly 50,000 prints
    ending at 04:29 ET, on a day whose neighbours returned 313k and 487k. Nothing
    raised, nothing was flagged, and the harvest recorded it as the session.

    So the range is driven by a cursor rather than by one request. Each call
    continues from **one nanosecond past the last print published**, and the loop
    ends when a call returns nothing more inside the range — which is the only
    honest evidence that there is nothing more. ``covered`` says whether it ended
    that way or ran out of calls, and a caller that flags a day complete must
    check it: a truncated fetch marked done is never looked at again.

    The +1ns lands in the **trim**, not in the request: a ``datetime`` carries
    microseconds and Rithmic indexes a replay request by whole *seconds*, so each
    continuation necessarily re-asks from the start of the second its last print
    fell in. That is why it is right — the remainder of that second comes back
    rather than being skipped, and the prints already published are cut here
    instead. What it costs is one re-sent page per continuation, counted in
    ``dropped``. What it loses is any print sharing the exact stamp of a page's
    last one, which is the safe direction: the alternative admits it twice.

    ``publish`` is awaited with each frame in turn, so a long range fills the tape
    as it arrives rather than in one lump at the end.
    """
    from datetime import datetime, timedelta, timezone

    # Read at call time, not bound as a default: a default argument freezes the
    # module constant at import and makes the cap untestable.
    cap = max_calls or REPLAY_MAX_CALLS
    out = {"rows": 0, "dropped": 0, "aggregated": 0, "calls": 0, "covered": False}
    cursor = from_ns
    empty_at = None      # the window already seen empty once — see below
    while cursor < until_ns and out["calls"] < cap:
        out["calls"] += 1
        window_end = min(until_ns, cursor + REPLAY_WINDOW_NS)
        start = datetime.fromtimestamp(cursor / 1e9, tz=timezone.utc)
        # Asked a minute past the window and cut back below: Rithmic's replay is
        # second-granular at the request boundary, and a range that ended exactly
        # on the instant wanted would be at the mercy of that rounding.
        end = datetime.fromtimestamp(window_end / 1e9, tz=timezone.utc) + timedelta(minutes=1)
        raw = replay_frame(
            await client.get_historical_tick_data(symbol, exchange, start, end,
                                                  idle_timeout=REPLAY_IDLE_S),
            agg_num, agg_side)
        frame = trim_seam(raw, cursor, window_end)
        out["dropped"] += len(raw) - len(frame)
        if frame.empty:
            # This window came back with nothing. Advancing past it rather than
            # stopping is right — with the range cut into windows, "empty" is a
            # fact about three hours and not about the session — but it is not
            # yet a fact at all: an empty answer is the same shape whether the
            # window was quiet or the replay simply did not serve it. 2026-08-04
            # was flagged complete ending at 15:48 ET on the strength of one
            # empty final window; the same window asked again holds 51,271
            # prints. So a window is asked twice before it is believed, which
            # costs one extra call on the genuinely empty ones (the 17:00-18:00
            # halt, and holidays) and nothing anywhere else.
            if empty_at != cursor:
                empty_at = cursor
                await asyncio.sleep(EMPTY_RETRY_S)
                continue
            cursor = window_end
            empty_at = None
            continue
        out["aggregated"] += int((frame["num_trades"] > 1).sum())
        await publish(frame.drop(columns=["num_trades"]))
        out["rows"] += len(frame)
        nxt = int(frame["ts_utc"].iloc[-1].value) + 1
        if nxt <= cursor:
            return out          # answered, but not advancing — do not spin
        # A window that came back truncated leaves the cursor inside it, and the
        # next call simply carries on from there. Truncation and progress are the
        # same code path.
        cursor = nxt
    out["covered"] = cursor >= until_ns
    return out


def trim_seam(frame: pd.DataFrame, from_ns: int, before_ns: int | None) -> pd.DataFrame:
    """Cut a replayed frame to ``[from_ns, before_ns)``.

    Both edges are seams against a tape stamped on a different clock, and both
    are cut so that the replay can only ever *lose* a print at the join, never
    add one — see ``SEAM_SLACK_NS`` for why that direction is the safe one.

    ``before_ns`` is the first print already received live. Cutting there rather
    than at the requested end instant is what closes the gap that would otherwise
    open between "the replay's last row" and "the first tick the subscription
    delivered": the request is deliberately made wider than needed, and the join
    is decided here, against a print that actually arrived, rather than against
    the host clock — which measured 1.7-2.8s off with a moving offset.
    """
    if frame.empty:
        return frame
    ns = frame["ts_utc"].values.astype("datetime64[ns]").astype("int64")
    keep = ns >= from_ns
    if before_ns is not None:
        keep &= ns < before_ns
    return frame[keep].reset_index(drop=True)


class RithmicFeed:
    """Streams live trades from the ticker plant into ``route``.

    ``route(frame)`` is called from a worker thread with a monotonic, sorted
    frame of ``FEED_COLS``. It is called off the event loop deliberately: it ends
    in a parquet write, and a seal that blocked the loop would stop the socket
    draining while it ran.
    """

    def __init__(self, route, symbol: str, exchange: str = "CME",
                 creds: dict | None = None,
                 backfill_from: pd.Timestamp | None = None,
                 resume_frame: pd.DataFrame | None = None,
                 sweep_days: int | None = None) -> None:
        self.route = route
        self.symbol = symbol
        self.exchange = exchange
        self._creds = creds or credentials()
        # Where the session begins — the instant the backfill reaches back to.
        # None turns the backfill off entirely, which is what the tests and any
        # caller that only wants the live stream pass.
        self._from_ns = None if backfill_from is None else int(
            pd.Timestamp(backfill_from).tz_convert("UTC").value)
        # What is already on disk for this session, held rather than appended.
        #
        # It is handed here instead of being preloaded into the session because
        # of the order things have to be published in. A recording that begins at
        # 07:08 — somebody connected, watched, and stopped — leaves the night in
        # front of it missing, and that is the very hole this feed exists to fill.
        # But `LiveSession.append` only appends: rows replayed for 18:00 cannot go
        # in *behind* rows already sitting at 07:08, and a tape that jumps
        # backwards re-phases every bar in it. So the feed publishes the three
        # pieces in time order — the night, then what was recorded, then the rest
        # — and the session sees one ordered stream. See ``_backfill``.
        self._resume = None if resume_frame is None or resume_frame.empty else resume_frame
        # What each backfill covered, for the status endpoint and the log. The
        # ranges matter to Phase 6: a backfilled stretch is on Rithmic's clock
        # and will show a systematic sub-millisecond offset against Databento's
        # ts_event, which is a finding about the seam and not about the feed.
        self.backfills: list[dict] = []
        # Whether to sweep earlier sessions once the live stream is up, and how
        # far back. None turns it off; the sweep is an addition to the tape, not
        # a part of connecting.
        self._sweep = sweep_days
        self._swept = False
        self.harvested: list[dict] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set from the loop when stop() is called, so a reconnect backoff can be
        # interrupted. `asyncio.sleep` is not cancellable from another thread,
        # and a shutdown that had to wait out a 60s backoff would look like a
        # hung API rather than a stopping feed.
        self._wake: asyncio.Event | None = None
        # Arrival buffer, appended by the tick callback and drained by the
        # publisher. A plain list under the GIL: both sides run on the same
        # thread (the event loop), so there is nothing to lock against.
        self._pending: list[tuple] = []
        self._last_ns = 0  # newest published stamp — see _clamped
        self._agg = None
        self._agg_replay = None
        self.connected = False
        # True while a replay is in flight. A whole session is tens of seconds
        # of it, during which the tape is empty and the chart has nothing to
        # draw — which reads as a broken page unless the page can say otherwise.
        self.backfilling = False
        self.error: str | None = None
        self.stats: Counter[str] = Counter()
        # Recent per-tick timings, in microseconds. Both are chosen because they
        # stay true on a host whose clock is not: the hop is a difference between
        # two stamps carried in the *same message*, and the lag is measured with
        # `monotonic`, which counts elapsed time and holds no opinion about what
        # time it is. The host clock qualifies for neither job — it measured
        # 1.7-2.8s behind Rithmic on one probe and 1.1s ahead on another, an
        # offset that moves and so cannot even be subtracted out. That is why
        # there is no end-to-end figure here: it is not measurable from this box,
        # and a plausible wrong number is worse than an absent one.
        self._hop_us: deque[int] = deque(maxlen=TIMING_WINDOW)
        self._lag_us: deque[int] = deque(maxlen=TIMING_WINDOW)
        self.started_at = time.time()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        install_redaction(self._creds["password"])
        self._thread = threading.Thread(target=self._thread_main,
                                        name="live-rithmic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to unwind and wait briefly.

        Bounded rather than joined outright, for the same reason the fake feed
        bounds its join: this is called from a request handler and from app
        shutdown, and a wedged feed thread must not take the API down with it.
        """
        self._stop.set()
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)
        t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        self._thread = None

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def status(self) -> dict:
        return {
            "source": "rithmic",
            "symbol": self.symbol,
            "exchange": self.exchange,
            "gateway": self._creds["url"],
            "system": self._creds["system_name"],
            "connected": self.connected,
            "running": self.running,
            "backfilling": self.backfilling,
            "error": self.error,
            "stats": dict(self.stats),
            "timing": self.timing(),
            "backfills": list(self.backfills),
            "harvested": list(self.harvested),
        }

    def timing(self) -> dict[str, int]:
        """Recent latency, as far as it is measurable without a trusted clock.

        Two legs, and neither is the one people ask for:

        ``hop_*`` is the exchange's stamp to Rithmic's send stamp — the leg the
        access probe measured at 0.3-0.4ms. Both stamps ride in one message, so
        no local clock enters into it and the figure is as good on this host as
        on any other.

        ``lag_*`` is arrival to publish *inside this process*, and it is bounded
        below by ``PUBLISH_S`` — a p50 near half of it is the cadence, not a
        fault. It earns its place as a starvation signal: a p90 far above 100ms
        means something (a shadow pass, a parquet seal, a sim sweep on the same
        cores) is holding the event loop, which is the one latency this code can
        actually do something about.

        **End-to-end latency is deliberately absent.** It needs the host clock
        against the exchange's, and the host clock is off by a second in a
        direction that changes between runs — three orders of magnitude above the
        quantity, so any number reported would be measuring WSL2's drift while
        looking exactly like a feed measurement.
        """
        out: dict[str, int] = {}
        for name, buf in (("hop", self._hop_us), ("lag", self._lag_us)):
            # One snapshot, then work off the copy: these are appended from the
            # feed thread and read from whichever thread served the request.
            # `list(deque)` is a single C-level call, so no append can land
            # inside it — which is what makes this safe without a lock.
            vals = sorted(list(buf))
            if not vals:
                continue
            out[f"{name}_n"] = len(vals)
            out[f"{name}_p50_us"] = vals[len(vals) // 2]
            out[f"{name}_p90_us"] = vals[min(len(vals) - 1, int(len(vals) * 0.9))]
            out[f"{name}_max_us"] = vals[-1]
        return out

    # --- the loop -----------------------------------------------------------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:  # noqa: BLE001 — the thread's last word
            self.error = f"{type(e).__name__}: {e}"

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._agg = _aggressor_map()
        self._agg_replay = _replay_aggressor_map()
        delay = RECONNECT_MIN_S
        while not self._stop.is_set():
            try:
                await self._connected_session()
                delay = RECONNECT_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a dropped feed reconnects
                self.connected = False
                self.error = f"{type(e).__name__}: {e}"
                self.stats["reconnects"] += 1
            if self._stop.is_set():
                return
            await self._nap(delay)
            delay = min(RECONNECT_MAX_S, delay * 2)

    async def _nap(self, secs: float) -> None:
        """Sleep, but wake early if stop() has been called."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass

    async def _connected_session(self) -> None:
        from async_rithmic import DataType, RithmicClient, SysInfraType

        client = RithmicClient(**self._creds)
        client.on_tick += self._on_tick
        # Market data plants only. The default opens all four, including ORDER.
        # HISTORY is opened only when there is a stretch to backfill, and its
        # absence must never cost the live feed: an account entitled to the
        # ticker plant and not the history plant would otherwise fail to connect
        # at all, trading a whole session for the stretch in front of it.
        plants = [SysInfraType.TICKER_PLANT]
        if self._from_ns:
            plants.append(SysInfraType.HISTORY_PLANT)
        try:
            await client.connect(plants=plants)
        except Exception as e:  # noqa: BLE001
            if len(plants) == 1:
                raise
            self.stats["history_plant_unavailable"] += 1
            print(f"[live-rithmic] history plant refused ({type(e).__name__}: {e}) "
                  "— connecting for live ticks only, without the backfill",
                  flush=True)
            client = RithmicClient(**self._creds)
            client.on_tick += self._on_tick
            await client.connect(plants=[SysInfraType.TICKER_PLANT])
            self._from_ns = None
        self.connected = True
        self.error = None
        try:
            # The bulk of the session is replayed BEFORE subscribing, and that
            # ordering was measured rather than chosen: a 13-hour replay takes
            # 12s on a quiet event loop and **66s** with a LAST_TRADE
            # subscription running beside it, because the tape floods the same
            # process the pagination is waiting in. Subscribing first also looks
            # like the safer order — it fixes the instant the live tape begins —
            # but it costs a minute of blank chart on every connect.
            #
            # What it gives up is closed immediately after: `_join` replays the
            # few seconds between the bulk and the first live print, which is a
            # small enough request that the flood does not matter.
            if self._from_ns:
                await self._backfill(client)
            await client.subscribe_to_market_data(
                self.symbol, self.exchange, DataType.LAST_TRADE)
            if self._from_ns:
                await self._join(client)
            # Days nobody was connected for, filled behind the live stream on
            # this same connection — Rithmic allows one session per login, so a
            # sweep with its own client would log this feed out. Backgrounded
            # because it is minutes of work and the tape must keep draining;
            # once per feed, not once per reconnect.
            if self._sweep and not self._swept:
                self._swept = True
                asyncio.get_running_loop().create_task(self._sweep_missing(client))
            while not self._stop.is_set():
                await self._nap(PUBLISH_S)
                await self._drain()
        finally:
            self.connected = False
            try:
                await client.unsubscribe_from_market_data(
                    self.symbol, self.exchange, DataType.LAST_TRADE)
            except Exception:  # noqa: BLE001 — we are already leaving
                pass
            await self._drain()
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # --- the backfill -------------------------------------------------------

    async def _backfill(self, client) -> None:
        """Assemble the session in front of the live stream, in time order.

        Runs on the same client as the live subscription, and that is a
        constraint rather than a convenience: Rithmic allows **one concurrent
        session per login**, so a second client opened to fetch history gets the
        first one force-logged-out. Measured — it happened while probing.

        Three pieces, published in the order they happened, because the session
        appends and a tape that jumps backwards re-phases every bar in it:

          1. **the head** — the session open up to whatever is already recorded.
             This is the piece that matters and the one a "resume from the tape's
             tail" rule silently skips: a recording that starts at 07:08 leaves
             the night in front of it missing, and a night the ``gx_*`` gates
             cannot read makes seven strategies veto everything without saying
             why. Recorded, because those gates read it off disk.
          2. **what was already recorded**, replayed from disk rather than from
             Rithmic and deliberately *not* re-recorded — it is already there,
             and writing it twice would put the day on disk out of order.
          3. **the tail** — from the recording's end to the first live print. On
             a **reconnect** there is no head and no disk frame, and this is the
             hole the dropped socket left; the same code repairs it.

        What this does not repair is a hole *between* two earlier recordings —
        connect, stop, connect, stop, then connect again. The head reaches the
        first, the tail follows the last, and the gap in the middle stays. Fixing
        it needs the covered intervals, and a gap in a tick tape is not
        distinguishable from a quiet market by looking at the tape.

        A backfill that fails costs the stretch in front of the connection and
        nothing else. It must never cost the live feed, so everything here is
        contained: on any error the feed goes on to stream, having said so.
        """
        now_ns = int(time.time() * 1e9)
        resume = self._resume
        self._resume = None       # published once, however often we reconnect
        if resume is None:
            # Nothing recorded yet, or this is a reconnect and the tape's tail is
            # already `_last_ns`. One range either way.
            await self._replay_range(client, self._from_ns, now_ns, "session")
            return

        head_until = int(resume["ts_utc"].iloc[0].value) - SEAM_SLACK_NS
        await self._replay_range(client, self._from_ns, head_until, "head")

        ns = self._clamped(resume["ts_utc"].values.astype("datetime64[ns]")
                           .astype("int64"))
        resume = resume.assign(ts_utc=pd.to_datetime(ns, utc=True))
        loop = asyncio.get_running_loop()
        # record=False: these rows came off disk, and the recorder appends
        # whatever it is handed. Writing them again would duplicate them into a
        # later chunk, and the day would then read back out of order.
        await loop.run_in_executor(None, self.route, resume, False)
        self.stats["resumed_rows"] += len(resume)

        await self._replay_range(client, self._from_ns, now_ns, "tail")

    async def _join(self, client) -> None:
        """Close the gap between the bulk backfill and the live subscription.

        Cut against a print that actually arrived, never against a clock: the
        host's measured 1.7-2.8s off with a moving offset, and a join placed a
        second late would replay prints the subscription is about to deliver —
        which is the one error direction that puts volume on the tape twice.

        On a quiet market no print arrives, and then the wait itself is the
        evidence: nothing traded in those seconds, so there is nothing in them to
        replay and cutting the range short of `now` loses nothing.
        """
        waited = 0.0
        while not self._pending and not self._stop.is_set() and waited < JOIN_WAIT_S:
            await asyncio.sleep(0.1)
            waited += 0.1
        until = (self._pending[0][0] if self._pending
                 else int((time.time() - waited) * 1e9))
        await self._replay_range(client, self._from_ns, until, "join")

    async def _sweep_missing(self, client) -> None:
        """Harvest earlier sessions behind the live stream. Never raises.

        Slower here than it would be alone — a replay competing with a
        LAST_TRADE subscription measured 5× — but it blocks nothing, and the
        alternative is a second login, which Rithmic answers by logging this one
        out.
        """
        from . import harvest

        try:
            days = await harvest.sweep(
                client, self.symbol, harvest.default_start(self._sweep),
                exchange=self.exchange,
                on_day=lambda r: print(
                    f"[live-harvest] {r['date']}: "
                    + ("already whole" if r["skipped"] else
                       f"{r['rows']:,} prints" + (f" — {r['error']}" if r.get("error") else "")),
                    flush=True))
        except Exception as e:  # noqa: BLE001 — an addition, never a dependency
            self.stats["harvest_errors"] += 1
            print(f"[live-harvest] sweep failed: {type(e).__name__}: {e}", flush=True)
            return
        self.harvested = days
        filled = [d for d in days if not d["skipped"] and d["rows"]]
        if filled:
            print(f"[live-harvest] filled {len(filled)} earlier session(s), "
                  f"{sum(d['rows'] for d in filled):,} prints", flush=True)

    async def _replay_range(self, client, from_ns: int, until_ns: int,
                            label: str) -> None:
        """Replay ``[from_ns, until_ns)`` and publish it. Never raises.

        ``from_ns`` is held off whatever has already been published by
        ``SEAM_SLACK_NS`` — see the constant for why the bias is toward losing a
        print at the join rather than admitting one twice.

        The fetching is ``replay_into``'s, which drives a cursor rather than
        trusting one call to answer for the whole range: Rithmic returns a silent
        prefix often enough that a session came back 50,000 prints long. Each
        page is published as it lands, so a long range fills the chart as it
        arrives instead of in one lump at the end.
        """
        if self._last_ns:
            from_ns = max(from_ns, self._last_ns + SEAM_SLACK_NS)
        if until_ns <= from_ns:
            return

        first_ns = last_ns = None

        async def publish(frame: pd.DataFrame) -> None:
            nonlocal first_ns, last_ns
            # A whole session takes tens of seconds to replay, and `stop()` waits
            # five before letting go of the thread — so a feed stopped mid-replay
            # would come back here with the session already torn down, and
            # `_Router` would open a *new* one to put these rows in.
            if self._stop.is_set():
                return
            # Through the same clamp the live drain uses, so the published stamp
            # is monotonic across the join and `_last_ns` ends where this range
            # does — which is what makes the next piece clamp against it rather
            # than against nothing.
            ns = self._clamped(frame["ts_utc"].values.astype("datetime64[ns]")
                               .astype("int64"))
            frame = frame.assign(ts_utc=pd.to_datetime(ns, utc=True))
            if first_ns is None:
                first_ns = int(ns[0])
            last_ns = int(ns[-1])
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.route, frame)

        t0 = time.perf_counter()
        self.backfilling = True
        try:
            res = await replay_into(client, self.symbol, self.exchange, from_ns,
                                    until_ns, self._agg_replay, self._agg, publish)
        except Exception as e:  # noqa: BLE001 — degraded, never fatal
            self.stats["backfill_errors"] += 1
            self.backfills.append({
                "range": label,
                "from": pd.Timestamp(from_ns, tz="UTC").isoformat(),
                "rows": 0, "error": f"{type(e).__name__}: {e}"})
            print(f"[live-rithmic] {label} backfill failed: {type(e).__name__}: {e}"
                  " — that stretch of the session will be missing", flush=True)
            return
        finally:
            self.backfilling = False

        self.stats["backfill_rows"] += res["rows"]
        self.stats["backfill_dropped_seam"] += res["dropped"]
        if res["aggregated"]:
            # Every bar of a whole session carried exactly one trade when this
            # was probed. If that stops being true the tape is no longer prints,
            # and that is a finding, not a log line to scroll past.
            self.stats["backfill_aggregated"] += res["aggregated"]
        if not res["covered"]:
            self.stats["backfill_truncated"] += 1

        span = {
            "range": label,
            "from": pd.Timestamp(from_ns, tz="UTC").isoformat(),
            "rows": int(res["rows"]),
            "seconds": round(time.perf_counter() - t0, 2),
            "dropped_seam": int(res["dropped"]),
            "aggregated": int(res["aggregated"]),
            "calls": int(res["calls"]),
            "covered": bool(res["covered"]),
        }
        if first_ns is not None:
            span["first"] = pd.Timestamp(first_ns, tz="UTC").isoformat()
            span["last"] = pd.Timestamp(last_ns, tz="UTC").isoformat()
        self.backfills.append(span)
        print(f"[live-rithmic] {label}: {span['rows']:,} prints from "
              f"{span.get('first', span['from'])} in {span['seconds']}s "
              f"({span['calls']} call(s)"
              + ("" if res["covered"] else ", TRUNCATED") + ")", flush=True)

    # --- ticks --------------------------------------------------------------

    async def _on_tick(self, data: dict) -> None:
        """One message off the plant. Kept to a buffer append and nothing else.

        Everything expensive belongs in the publisher: this runs inline on the
        socket's drain, so work done here is backpressure on the feed.
        """
        from async_rithmic import DataType, LastTradePresenceBits

        if data.get("data_type") != DataType.LAST_TRADE:
            self.stats["non_trade"] += 1
            return
        # A LAST_TRADE message can carry only the derived fields (volume, vwap,
        # net change) with no print in it. The presence bit is what says whether
        # there is a trade here at all.
        if not data.get("presence_bits", 0) & LastTradePresenceBits.LAST_TRADE:
            self.stats["no_trade_bit"] += 1
            return
        if data.get("is_snapshot"):
            # The opening snapshot repeats the last print with a stale stamp.
            # Admitting it would put a duplicate tick on the tape at the wrong
            # instant, which is exactly the corruption the monotonic clamp
            # cannot see (it is in-order, just wrong).
            self.stats["snapshot"] += 1
            return
        ns, exch, hop_us = self._stamp(data)
        if ns is None:
            self.stats["no_stamp"] += 1
            return
        price = data.get("trade_price")
        size = data.get("trade_size")
        if price is None or size is None:
            self.stats["incomplete"] += 1
            return
        self.stats["exchange_stamp" if exch else "rithmic_stamp"] += 1
        # `monotonic` rather than `time()`, and not only because the wall clock is
        # wrong: this stamp is subtracted from another taken in `_drain`, so what
        # is wanted is elapsed time, and monotonic is the clock that cannot step
        # sideways under an NTP correction mid-batch. It is a vDSO read, which is
        # what keeps this callback the buffer append its docstring promises.
        self._pending.append((ns, float(price), int(size),
                              int(data.get("aggressor", 0) or 0),
                              time.monotonic(), hop_us))

    @staticmethod
    def _stamp(data: dict) -> tuple[int | None, bool, int | None]:
        """Epoch ns for a print, whether it is the exchange's clock, and the hop.

        The exchange stamp is preferred because it is the same instant Databento
        stores as ``ts_event`` — without it, Phase 6's tape comparison would be
        measuring Rithmic's delivery rather than the two feeds' agreement.
        Rithmic's send stamp is the fallback; the host clock is never used, at
        any resolution, for any field.

        The third value is the **exchange -> Rithmic hop** in microseconds, and it
        is the one latency figure this feed can state honestly, because both
        stamps arrive in the same message and their difference needs no local
        clock. ``None`` when the message carries only one of the two — which is
        also every fallback row, where the exchange stamp is what is missing.
        """
        rith_ns = None
        if data.get("ssboe"):
            rith_ns = (int(data["ssboe"]) * 1_000_000_000
                       + int(data.get("usecs", 0) or 0) * 1_000)
        if data.get("source_ssboe"):
            sub = data.get("source_nsecs")
            if sub is None:
                sub = int(data.get("source_usecs", 0) or 0) * 1_000
            ns = int(data["source_ssboe"]) * 1_000_000_000 + int(sub)
            return ns, True, None if rith_ns is None else (rith_ns - ns) // 1_000
        if rith_ns is not None:
            return rith_ns, False, None
        return None, False, None

    def _clamped(self, ns: np.ndarray) -> np.ndarray:
        """Force the batch non-decreasing, continuing from the last published tick.

        Exchange stamps can arrive very slightly out of order, and the tape is
        not allowed to be: ``LiveSession.append`` documents that rows are trusted
        to be in order, the engine searchsorts the RTH boundary, and every tick
        bar is phased by position. An out-of-order tick would not raise — it
        would quietly re-phase bars, which is the failure class this whole plan
        keeps trying to avoid.

        Clamping forward rather than dropping, because a dropped print is a real
        trade missing from the volume profile and the VWAP, while a clamped one
        is the same trade with its stamp moved by microseconds. How often it
        happens is counted and lands in the recorder's manifest, so a feed where
        it stops being rare is visible rather than assumed away.
        """
        out = np.maximum.accumulate(np.maximum(ns, self._last_ns))
        moved = int((out != ns).sum())
        if moved:
            self.stats["clamped"] += moved
        self._last_ns = int(out[-1])
        return out

    async def _drain(self) -> None:
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        ns = self._clamped(np.fromiter((r[0] for r in rows), dtype="int64",
                                       count=len(rows)))
        agg = np.fromiter((r[3] for r in rows), dtype="int16", count=len(rows))
        frame = pd.DataFrame({
            "ts_utc": pd.to_datetime(ns, utc=True),
            "price": np.fromiter((r[1] for r in rows), dtype="float64",
                                 count=len(rows)),
            "size": np.fromiter((r[2] for r in rows), dtype="uint32",
                                count=len(rows)),
            "side": pd.Series(agg).map(self._agg).fillna("N").to_numpy(dtype="U1"),
            "agg_raw": agg,
        })
        self.stats["ticks"] += len(frame)
        # Timed here rather than after `route` returns: what this measures is how
        # long a print waited to be published, and the recorder's write is the
        # publishing, not a delay before it. Read once for the whole batch so
        # every row in it is measured against the same instant.
        now = time.monotonic()
        self._lag_us.extend(int((now - r[4]) * 1_000_000) for r in rows)
        self._hop_us.extend(r[5] for r in rows if r[5] is not None)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.route, frame)
        except Exception as e:  # noqa: BLE001 — a bad batch is not a dead feed
            self.stats["route_errors"] += 1
            self.error = f"route: {type(e).__name__}: {e}"
