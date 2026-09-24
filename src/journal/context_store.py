"""Turning journal trades into measured windows, and putting them away.

``trade_context`` measures; this module knows where the trades come from and
where the numbers go. The same split — and the same reason — as
``level_tag``/``level_store``: the measurement has no idea whether it is looking
at a replay, a backtest or a live session, so a fourth source means a new
``trades_from_*`` here and nothing else.

Grouped by session because the tape is per-session. A day whose ticks were never
cached is an ordinary outcome and not an error: this runs off the back of a
finished sitting, and there is no version of "your practice is unreviewable
because a 2024 session isn't on disk" worth failing a save for.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import db as dbmod
from . import trade_context as tcmod
from .level_store import session_day
from .trade_context import Trade


def trades_from_logical(rows) -> list[Trade]:
    """Logical journal trades -> the windows each one owes.

    ``avg_entry``/``avg_exit`` are size-weighted, so a scaled trade is measured
    around the price it actually got. A trade missing either side is skipped
    entirely rather than half-measured: without both ends there is no hold, and
    "before" and "after" have nothing to be before and after.
    """
    out: list[Trade] = []
    for r in rows:
        key = str(r.get("trade_key") or r.get("logical_trade_key") or "")
        if not key:
            continue
        vals = [r.get(c) for c in
                ("entry_ts_utc", "avg_entry", "exit_ts_utc", "avg_exit")]
        if any(v is None or pd.isna(v) for v in vals):
            continue
        ets, epx, xts, xpx = vals
        out.append(Trade(
            key=key,
            direction=str(r.get("direction") or "Long"),
            entry_ts=_utc(ets), entry_px=float(epx),
            exit_ts=_utc(xts), exit_px=float(xpx),
        ))
    return out


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def context_trades(conn, rows) -> dict:
    """Measure and store pre-entry / post-exit context for a set of logical trades.

    Returns a small report rather than raising, for the reason in the module
    docstring. ``days_skipped`` is the honest half of it: those trades have no
    context and the report says which, rather than a count that looks complete.
    """
    trades = trades_from_logical(rows)
    by_day: dict[date, list[Trade]] = {}
    for t in trades:
        by_day.setdefault(session_day(t.entry_ts), []).append(t)

    written, cleared, days_ok, days_skipped = 0, 0, [], []
    for day, day_trades in sorted(by_day.items()):
        ctxs = tcmod.measure_day(contract="NQ", day=day, trades=day_trades)
        if not ctxs:
            days_skipped.append(day)
            continue
        stamp = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        written += dbmod.set_trade_context(conn, [
            tcmod.as_row(c, method=tcmod.METHOD, computed_at=stamp) for c in ctxs
        ])
        # A trade the tape priced somewhere else entirely is refused per-trade,
        # and must not keep numbers from a run that predates the guard: a stale
        # row reads as a measured one to every caller. Only ever on a day that
        # DID measure — a day skipped for want of a cached tape says nothing
        # about the trades on it, and clearing there would throw away good rows
        # because a parquet has not been bought yet.
        measured = {c.key for c in ctxs}
        cleared += dbmod.clear_trade_context(
            conn, [t.key for t in day_trades if t.key not in measured])
        days_ok.append(day)
    return {
        "trades": len(trades),
        "rows_written": written,
        "rows_cleared": cleared,
        "days": len(days_ok),
        "days_skipped": [str(d) for d in days_skipped],
        "method": tcmod.METHOD,
    }
