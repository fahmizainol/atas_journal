"""What the shelf said, while it was saying it.

Phase 6's second comparison is prefix integrity: every signal the live runner
emitted during the session must be a prefix of one final full run over the same
tape. That comparison needs the *during* half to have been written down — a run
after the close can always be redone, but what the runner believed at 10:14 is
gone the moment the process is.

So each strategy gets an append-only ``.jsonl`` under
``data/live/signals/{SYMBOL}/{DATE}/{slug}.jsonl``, one line per pass **whose
answer changed**. A pass that repeats itself is the ordinary case (a strategy
that has not signalled all morning says the same nothing every thirty seconds),
and writing those would bury the handful of lines that carry the day in
thousands that do not.

WHY NOT WRITE THE WHOLE SNAPSHOT. What is journalled is one strategy's trades and
vetoes, keyed by tape position — the smallest thing the prefix comparison
consumes. Anything else (the regime, the cadence state, the config) either
belongs to the run that can be redone, or is already recorded where it is
authoritative.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pandas as pd

from ..config import DATA_DIR

LIVE_SIGNAL_DIR = DATA_DIR / "live" / "signals"


def day_dir(symbol: str, day: date) -> Path:
    return LIVE_SIGNAL_DIR / symbol / day.isoformat()


class SignalJournal:
    """Append-only record of each strategy's answer as the day ran."""

    def __init__(self, symbol: str, day: date) -> None:
        self.symbol = symbol
        self.day = day
        self.dir = day_dir(symbol, day)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # The last answer written per slug, so an unchanged pass writes nothing.
        # Compared as the encoded string: the trades are engine dicts of numpy
        # scalars and Timestamps, and comparing those structurally is a longer
        # way round to the same answer.
        self._last: dict[str, str] = {}

    def record(self, slug: str, rows: int, last_ts: pd.Timestamp | None,
               trades: list[dict], vetoed: list[dict],
               error: str | None = None) -> bool:
        """Write one line if this differs from the strategy's last. True if written."""
        body = json.dumps({"trades": trades, "vetoed": vetoed, "error": error},
                          default=str, sort_keys=True)
        with self._lock:
            if self._last.get(slug) == body:
                return False
            self._last[slug] = body
            line = json.dumps({
                "rows": rows,
                "at": None if last_ts is None else last_ts.isoformat(),
                "trades": trades,
                "vetoed": vetoed,
                "error": error,
            }, default=str)
            try:
                with (self.dir / f"{slug}.jsonl").open("a") as fh:
                    fh.write(line + "\n")
            except OSError:
                # A journal that cannot be written must not stop the shadow pass:
                # the signals on screen are the live half of the feature, and the
                # reconciliation they feed is the deferred half.
                return False
        return True


def read(symbol: str, day: date, slug: str) -> list[dict]:
    """One strategy's journal for a day, oldest first. [] if there is none.

    A truncated final line is dropped rather than raised on — the process that
    was writing it may simply have been killed mid-append, and the lines behind
    it are still exactly what was said.
    """
    p = day_dir(symbol, day) / f"{slug}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def slugs(symbol: str, day: date) -> list[str]:
    """Which strategies were journalled for a day."""
    d = day_dir(symbol, day)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))
