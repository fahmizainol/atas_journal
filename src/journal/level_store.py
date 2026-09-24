"""Turning journal trades into scored fills, and putting the scores away.

``level_tag`` measures; this module knows where the trades come from and where
the numbers go. The split is deliberate: the measurement has no idea whether it
is looking at a replay, a backtest or a live session, so adding a fourth source
means writing a new ``fills_from_*`` here and nothing else.

Nothing in this module writes a user-facing answer. See the ``trade_levels``
schema for why that boundary is load-bearing — the rows it stores are offered to
the review as *candidates* for which level a trade was taken off, and the trader
still has to pick one (``journal.review``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import db as dbmod
from . import level_tag
from .config import ET_TZ
from .level_tag import Fill

#: Journal comments are ``mode:reason`` (``replay:stop``, ``live:manual``).
def _reason(comment: str | None) -> str | None:
    if not comment:
        return None
    return str(comment).split(';')[0].split(':')[-1].strip() or None


def fills_from_logical(rows) -> list[Fill]:
    """Logical journal trades -> the two fills each one owes.

    ``avg_entry``/``avg_exit`` are size-weighted, so a scaled trade is scored at
    the price it actually got rather than at whichever leg happened to be first.
    A trade missing either side contributes only the side it has.
    """
    out: list[Fill] = []
    for r in rows:
        key = str(r.get('trade_key') or r.get('logical_trade_key') or '')
        if not key:
            continue
        reason = _reason(r.get('comment'))
        for anchor, ts_col, px_col in (('entry', 'entry_ts_utc', 'avg_entry'),
                                       ('exit', 'exit_ts_utc', 'avg_exit')):
            ts, px = r.get(ts_col), r.get(px_col)
            if ts is None or px is None or pd.isna(ts) or pd.isna(px):
                continue
            out.append(Fill(key=key, anchor=anchor,
                            ts_utc=pd.Timestamp(ts).tz_convert('UTC')
                            if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz='UTC'),
                            price=float(px),
                            reason=reason if anchor == 'exit' else None))
    return out


def session_day(ts_utc) -> date:
    """The trading day a fill belongs to, in the exchange's own clock."""
    return pd.Timestamp(ts_utc).tz_convert(ET_TZ).date()


def tag_trades(conn, rows, *, allow_fetch: bool = False) -> dict:
    """Measure and store level proximity for a set of logical trades.

    Grouped by session because the null is per-session: companions have to come
    from the same tape the fill printed on. Returns a small report rather than
    raising — this runs off the back of a finished session, and a day whose ticks
    are not cached is an ordinary outcome, not an error worth failing a save for.
    """
    fills = fills_from_logical(rows)
    scored = [f for f in fills if not f.mechanical]
    by_day: dict[date, list[Fill]] = {}
    for f in scored:
        by_day.setdefault(session_day(f.ts_utc), []).append(f)

    written, days_ok, days_skipped = 0, [], []
    for day, day_fills in sorted(by_day.items()):
        ranks = level_tag.tag_fills(contract='NQ', day=day, fills=day_fills,
                                    tz=ET_TZ, allow_fetch=allow_fetch)
        if not ranks:
            days_skipped.append(day)
            continue
        written += dbmod.set_trade_levels(conn, ranks, level_tag.METHOD)
        days_ok.append(day)
    return {
        'fills': len(fills), 'scored': len(scored),
        'mechanical_skipped': len(fills) - len(scored),
        'rows_written': written,
        'days': len(days_ok), 'days_skipped': [str(d) for d in days_skipped],
        'method': level_tag.METHOD,
    }


def candidates_for(conn, key: str) -> list[dict]:
    """Every level measured at this trade's entry, as a picker's options.

    The read side of what :func:`tag_trades` stores, and here for the reason
    this module's own docstring gives: these rows are *offered* to the review,
    so shaping them into an offer is this module's job rather than any one
    router's. Two surfaces ask for them — the replay/drill review and Recall's
    back card — and a second copy of the ordering rule below is exactly the
    drift the ``trade_levels`` schema note warns about.

    **Ordered by distance, never by the tagger's rank.** Distance is a fact
    about where the chart was; rank is the machine's opinion about how unusual
    that distance was, and leading the list with it would be the tagger
    answering the human's question for them. The rank rides along so the panel
    can show it *beside* an option, which is a measurement offered rather than a
    suggestion made; it is carried only by the member its family was scored
    through, so most options have a distance and no rank.

    One option per *level*, not per family. A family collapses to its nearest
    member, so a picker built from families could not offer the globex POC on a
    day the session POC happened to be closer — and which one you were watching
    is the entire question being asked.

    Only the entry anchor: the question is what the trade was taken off.
    """
    out = []
    for r in dbmod.get_trade_levels(conn, key):
        if r.get('anchor') != 'entry' or not r.get('member'):
            continue
        dist = r.get('dist_ticks')
        out.append({
            # The level itself — this is what a pick stores.
            'id': r['member'],
            # Named off the member, not the family: the families pool NY, globex
            # and weekly, and "which VAH" is exactly what the pick must record.
            'label': level_tag.label_for(r['family'], r['member']),
            'family': r['family'],
            'dist_ticks': dist,
            'rank': r.get('rank'),
        })
    out.sort(key=lambda c: abs(c['dist_ticks']) if c['dist_ticks'] is not None else 1e9)
    return out
