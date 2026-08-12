"""1-minute bars, derived from the tick cache instead of bought as bars.

A drop-in for ``databento_client.get_bars`` — same arguments, same columns, same
"None when there's nothing" — so the modules that only ever wanted *some* minute
bars (``levels``, ``excursion``) change one import and keep their arithmetic.

WHY THIS EXISTS. The journal's charts used to buy ohlcv-1m from Databento and
cache it per continuous-symbol day. That corpus is a second copy of data we
already own: the tick cache holds 601 NQ sessions, and a minute bar is a groupby
away from the ticks that made it (``sim.bars.time_bars``). Keeping both meant
paying twice, and in practice meant the journal's charts drew nothing at all —
the bar cache was empty while the tick cache was 3.6 GB, so every trade chart
was a cache miss that ended in a swallowed 402.

THREE THINGS THIS GETS RIGHT THAT THE BAR PATH DIDN'T.

*Volume is real.* An ohlcv-1m bar carries one volume number, so a chart could
only smear it across the bar's range. The ticks hold the actual distribution, so
the profile and footprint drawn on top are exact rather than modelled.

*The contract is the one that traded.* The bar path asked for a *continuous*
symbol (`NQ.v.0`), which is right for a study and wrong for a trade: a journal
row's `instrument` is what ATAS stamped on the export — the front month at
export time — so a January 2026 trade is labelled `NQU6@CME` and actually
happened on `NQH6`. Here the *day* picks the contract, through the roll map.

*It never spends.* Every read below is cache-only (``cached_rth`` and friends).
A GET that could reach Databento is a GET that can cost money and hang; a
session that was never bought simply has no bars, and the caller says so.

The one deliberate difference from the old bars: ``ts_utc`` stamps the **last**
tick in the minute rather than the minute's open, because that is how
``sim.bars.time_bars`` stamps and the whole point is that the Lab and the journal
now draw the same candles from the same builder.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .config import root_symbol
from .sim import bars as barmod
from .sim import ticks as tickmod

#: An ET session date `d` owns the ticks from 18:00 ET on `d-1` (the Globex open)
#: to 18:00 ET on `d`. Two calendar days of slack either side of a UTC window is
#: enough to catch every session that can overlap it, whatever the DST offset.
_SESSION_SLACK = timedelta(days=2)

#: The grid these bars are built on. Named because the window mask has to know
#: how much time a bar covers, not just where it is stamped.
_BAR = "1min"
_BAR_LEN = pd.Timedelta(minutes=1)


def _as_utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def session_ticks(instrument: str, day: date) -> pd.DataFrame | None:
    """Every cached tick for one ET session date: night, RTH, and the post hour.

    Contiguous by construction — the three segments meet end-exclusive at 09:30
    and 16:00 — so concatenating them is already globally ordered. None when the
    session was never bought; a session with only some segments returns what it
    has, because a chart of a half-session is better than no chart.
    """
    sym = tickmod.contract_for_cached(root_symbol(instrument), day)
    if sym is None:
        return None
    parts = [
        tickmod.cached_overnight(sym, day),
        tickmod.cached_rth(sym, day),
        tickmod.cached_post(sym, day),
    ]
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


def _session_dates(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> list[date]:
    d = (start_utc.tz_convert(tickmod.ET_TZ) - _SESSION_SLACK).date()
    last = (end_utc.tz_convert(tickmod.ET_TZ) + _SESSION_SLACK).date()
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def get_bars(
    instrument: str,
    start_utc,
    end_utc,
    slice_to_window: bool = True,
) -> pd.DataFrame | None:
    """1m bars for an instrument across a UTC window (spans day boundaries).

    Signature-compatible with the ``databento_client.get_bars`` it replaces,
    including ``slice_to_window=False`` meaning "every bar of the session(s) the
    window touches, untrimmed" — which is what lets a chart pan and zoom across
    the whole session from one load.

    Sessions with nothing on disk contribute nothing rather than raising, so a
    window that straddles the edge of the cache returns the part that exists.
    """
    start_utc, end_utc = _as_utc(start_utc), _as_utc(end_utc)
    if end_utc < start_utc:
        return None

    frames = []
    for day in _session_dates(start_utc, end_utc):
        t = session_ticks(instrument, day)
        if t is None or t.empty:
            continue
        b = barmod.time_bars(t, "1min")
        if not b.empty:
            frames.append(b[["ts_utc", "open", "high", "low", "close", "volume"]])
    if not frames:
        return None

    allbars = pd.concat(frames, ignore_index=True)
    allbars["ts_utc"] = pd.to_datetime(allbars["ts_utc"], utc=True)
    # Sessions are read whole, so two adjacent ones can each claim a tick at the
    # seam. Sort then drop the duplicate stamp rather than trusting the order.
    allbars = (allbars.sort_values("ts_utc")
                      .drop_duplicates(subset="ts_utc", keep="last")
                      .reset_index(drop=True))
    if slice_to_window:
        # Keep bars that **overlap** the window, not bars whose stamp falls
        # inside it. The distinction is invisible for a long window and total
        # for a short one: a bar is stamped at its last tick, so a 26-second
        # scalp inside 09:49 asks for [09:49:00.1, 09:49:26.8] and the only bar
        # covering it is stamped 09:49:59.9 — outside its own trade. Masking on
        # the stamp returned nothing for every trade shorter than a minute,
        # which is most scalps. A bar owns the minute [m, m+60), so it overlaps
        # when m <= end and m+60 > start.
        bucket = allbars["ts_utc"].dt.floor(_BAR)
        mask = (bucket <= end_utc) & (bucket + _BAR_LEN > start_utc)
        allbars = allbars[mask].reset_index(drop=True)
    else:
        # Untrimmed still means "the sessions this window touches", not the two
        # days of slack _session_dates casts to find them.
        lo = start_utc - _SESSION_SLACK
        hi = end_utc + _SESSION_SLACK
        allbars = allbars[(allbars["ts_utc"] >= lo)
                          & (allbars["ts_utc"] <= hi)].reset_index(drop=True)
    return allbars if not allbars.empty else None


def is_available() -> bool:
    """Whether anything can be charted at all.

    The old ``databento_client.is_available`` asked whether an API key was
    configured — a question about a wallet. The honest question now is whether
    the tick cache exists, and it is asked once at the top of each payload
    builder so an empty install still renders a friendly notice instead of a
    stack of empty charts.
    """
    return tickmod.TICK_CACHE_DIR.is_dir() and any(
        tickmod.TICK_CACHE_DIR.glob("*.parquet")
    )
