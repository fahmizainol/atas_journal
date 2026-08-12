"""Writing a live session down, so the day survives the process that watched it.

A recorded tick is not a nice-to-have. Ten ``gx_*`` gate sites and the weekly
seed read the session's earlier windows **off disk**, keyed by (contract, day) —
not from the frame the runner injects. On a live day with nothing behind those
reads every Globex strategy vetoes everything and says nothing about why, which
is a plausible wrong answer rather than an error. So the writes are the half of
Phase 5 that could not be cut; the separate recorder *process* is the half that
could. This is the in-process form: the feed appends here on the same call it
appends to the tape.

THE FORMAT: sealed chunk parquets under ``data/live/ticks/{SYMBOL}/{DATE}/``,
plus a ``session.json`` heartbeat. Three properties, all so that a reader and a
writer can share the directory with no coordination at all:

  - **A chunk is immutable once named.** It is written to a temp file and
    renamed, so a reader sees it whole or not at all. There is no such thing as
    reading a half-written chunk, and therefore no lock between the two sides.
  - **The directory is the truth; the manifest is a heartbeat.** A chunk on disk
    counts whether or not ``session.json`` has caught up with it. That deletes
    the ordering question ("which do I write first") outright — the answer would
    otherwise have to be right on every crash.
  - **Names sort in write order**, so the glob is the whole day. It is not
    already *the tape*: write order stopped being time order when the feed
    learned to backfill — connect at 07:08 and those prints are written first;
    reconnect and the replayed night from 18:00 lands in a later chunk. The day
    is the union of the chunks **in timestamp order**, and ``_read_live_cached``
    is where that sort happens, once per change in the chunk set.

WHY SEAL AT SEGMENT BOUNDARIES. Time and row count are the ordinary triggers,
but the load-bearing one is the third: the buffer is sealed when a batch crosses
into a new window. The overnight is therefore complete on disk at the instant
RTH opens, which is exactly when the ``gx_*`` gates start reading it. Without
that rule the night would be short by up to one seal interval at the one moment
it is asked for.

WHAT IS NOT WRITTEN. Nothing here ever writes into ``data/cache/ticks/``. The
two stores stay disjoint permanently (docs/live-shadow-plan.md decisions 3-4):
the Databento corpus is the independent reference a recorded day is *checked
against*, and a reference partly made of the thing it is checking is not one.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ..sim import ticks as tickmod

# Seal the buffer once it holds this many rows. At NQ rates a session is tens of
# chunks — enough that a crash loses little, few enough that the read is a
# handful of parquet opens rather than thousands.
CHUNK_ROWS = 50_000
# ...or this long since the buffer's first row, whichever comes first. What this
# bounds is how much a crash costs, so it is a data-loss budget, not a tuning
# knob: 30s of prints.
CHUNK_SECONDS = 30.0

MANIFEST = "session.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, write) -> None:
    """Write via a temp file in the same directory, then rename.

    Same directory because rename is only atomic within a filesystem, and same
    reason the chunks use it: a reader must never see a partial file. The temp
    name is process-scoped so two writers cannot collide on it — they should
    never both exist, but a leftover temp from a killed process must not be
    mistaken for a live one either, and the pid in the name makes that visible.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class TickRecorder:
    """Appends a live session's ticks to disk, one sealed chunk at a time.

    Thread-safe, and deliberately never called with the tape's lock held: a
    parquet write is milliseconds and the tape is read by every request thread.
    """

    def __init__(self, symbol: str, day: date) -> None:
        self.symbol = symbol
        self.day = day
        self.dir = tickmod.live_day_dir(symbol, day)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._buf: list[pd.DataFrame] = []
        self._buf_rows = 0
        self._buf_seg: str | None = None
        self._buf_since: float | None = None  # tape seconds of the buffer's first row
        # Resume rather than restart: an existing day keeps its chunks and the
        # next one continues the numbering. Re-using an index would silently
        # overwrite ticks that are gone for good.
        self._next = self._resume_index()
        self.rows = self._recorded_rows()
        # Counters that only a recording can answer, carried in the manifest
        # because they are evidence about the feed rather than about the tape.
        self.stats: dict[str, int] = {}
        # Facts about the *run* that every heartbeat should repeat — how the day
        # was watched, as opposed to how it went. ``harvest`` passes its
        # ``source`` per call because it writes one heartbeat at the end; a
        # session being watched writes hundreds, and a mark that had to be
        # threaded through ``_Router._beat`` would be forgotten by the first
        # caller that did not know about it.
        self.marks: dict[str, object] = {}

    def _resume_index(self) -> int:
        chunks = tickmod.live_chunks(self.symbol, self.day)
        if not chunks:
            return 0
        return max(int(Path(c).stem) for c in chunks) + 1

    def _recorded_rows(self) -> int:
        """How many ticks are already on disk, from the parquet footers.

        Counted from metadata rather than by reading the chunks: this runs on
        every resume, and a session is millions of rows that nothing here needs
        the values of.
        """
        import pyarrow.parquet as pq

        total = 0
        for c in tickmod.live_chunks(self.symbol, self.day):
            try:
                total += pq.ParquetFile(self.dir / c).metadata.num_rows
            except Exception:  # noqa: BLE001 — a count is not worth failing a resume
                pass
        return total

    # --- writing ------------------------------------------------------------

    def append(self, frame: pd.DataFrame) -> None:
        """Buffer a batch, sealing whatever the batch completes.

        Failure here must not take the feed down — a tick that reached the tape
        but not the disk is a smaller problem than a feed that stopped — so the
        caller wraps this. What it must not do is silently swallow: the manifest
        carries an ``errors`` count so a day that recorded badly says so.
        """
        if frame is None or frame.empty:
            return
        seg = self._segment_of(frame["ts_utc"].iloc[0])
        with self._lock:
            # A batch that crosses a window boundary seals the old window first,
            # so each chunk belongs to exactly one segment and the night is
            # complete on disk the moment RTH opens.
            if self._buf and seg != self._buf_seg:
                self._seal_locked()
            self._buf.append(frame)
            self._buf_rows += len(frame)
            self._buf_seg = seg
            if self._buf_since is None:
                self._buf_since = frame["ts_utc"].iloc[0].timestamp()
            span = frame["ts_utc"].iloc[-1].timestamp() - (self._buf_since or 0.0)
            if self._buf_rows >= CHUNK_ROWS or span >= CHUNK_SECONDS:
                self._seal_locked()

    def flush(self) -> None:
        """Seal whatever is buffered. Idempotent."""
        with self._lock:
            self._seal_locked()

    def _segment_of(self, ts: pd.Timestamp) -> str:
        """Which window a tick falls in. Asked of a batch's *first* row only.

        A batch that straddles 09:30 therefore lands whole in the night's chunk,
        and the seal happens one batch (~`PUBLISH_S`) late. That is deliberate and
        costs nothing: ``live_segment`` cuts by timestamp, not by which chunk a
        tick was written into, so the boundary the readers see is exact either
        way. What the rule buys is that the night is sealed and readable within
        a batch of the bell, which is when the ``gx_*`` gates start asking.
        """
        rth_open = tickmod.session_bounds_utc(self.day)[0]
        post_open = tickmod.post_bounds_utc(self.day)[0]
        if ts < rth_open:
            return "on"
        return "rth" if ts < post_open else "post"

    def _seal_locked(self) -> None:
        if not self._buf:
            return
        df = (pd.concat(self._buf, ignore_index=True)
              if len(self._buf) > 1 else self._buf[0].reset_index(drop=True))
        name = f"{self._next:06d}.parquet"
        _write_atomic(self.dir / name, lambda p: df.to_parquet(p, index=False))
        self._next += 1
        self.rows += len(df)
        self._buf, self._buf_rows, self._buf_seg, self._buf_since = [], 0, None, None

    # --- the heartbeat ------------------------------------------------------

    def heartbeat(self, last_ts: pd.Timestamp | None, closed: bool = False,
                  **extra) -> None:
        """Rewrite ``session.json``.

        Nothing reads this to reconstruct the tape — the directory is the truth
        for that. What it answers is the question the files cannot: is anything
        still writing here, and how did the feed behave while it was.
        """
        rec = {
            "symbol": self.symbol,
            "date": self.day.isoformat(),
            "rows": self.rows,
            "buffered": self._buf_rows,
            "chunks": self._next,
            "last_tick_utc": None if last_ts is None else last_ts.isoformat(),
            "updated_at": _utcnow(),
            "closed": bool(closed),
            "stats": dict(self.stats),
            **self.marks,
            **extra,
        }
        try:
            _write_atomic(self.dir / MANIFEST,
                          lambda p: p.write_text(json.dumps(rec, indent=1)))
        except OSError:
            pass  # a heartbeat that cannot be written is not worth a lost tick

    def close(self, last_ts: pd.Timestamp | None = None) -> None:
        self.flush()
        self.heartbeat(last_ts, closed=True)


def read_manifest(symbol: str, day: date) -> dict | None:
    """A recorded day's heartbeat, or None. Never needed to read the ticks."""
    p = tickmod.live_day_dir(symbol, day) / MANIFEST
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def recorded_days(symbol: str | None = None) -> list[tuple[str, date]]:
    """Every (symbol, session date) with at least one sealed chunk, oldest first.

    Globs the live store alone — "what was recorded", with no view of what was
    bought. ``/simulator/days`` calls this for its second pass and then applies
    its own bar (RTH covered end to end); the two questions are different, and
    keeping this one store-shaped is what lets both be asked.
    """
    root = tickmod.LIVE_TICK_DIR
    if not root.is_dir():
        return []
    out: list[tuple[str, date]] = []
    for sym_dir in sorted(root.iterdir()):
        if not sym_dir.is_dir() or (symbol and sym_dir.name != symbol):
            continue
        for day_dir in sorted(sym_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            try:
                d = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if tickmod.live_chunks(sym_dir.name, d):
                out.append((sym_dir.name, d))
    return out
