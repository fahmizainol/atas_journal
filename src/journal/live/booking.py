"""Live-chart trades, written into the journal.

Until this existed, the one surface in the app where you actually trade was the
one surface whose trades never appeared in it. The blotter on `/charts/live`
lived in the browser and died on reload; the broker's round trips lived in
memory and in an append-only `orders.jsonl` that nothing read back. This module
is the seam between "a trade happened on the live chart" and "the journal knows
about it".

**THERE IS NO `trades` TABLE, AND THAT IS WHY THIS IS SHORT.** A journaled trade
is *derived at read time* from `atas_journal` — a table of matched lots — by
``journal.trades.build_logical_trades``, funnelled through ``api.scope``. So a
row inserted here reaches the Trades page, the Calendar, Statistics, the AI
review, notes, setups, models and video bookmarks with **nothing else to change**.
The whole feature is: build the row correctly, and register the session it
belongs to.

THREE CALLERS, AND THE ASYMMETRY IS FORCED. The fill engines are in different
places. A real trade is netted by ``journal.live.broker.Broker`` on the server,
so it books itself. A **paper** trade is computed by ``replaySim.ts`` in the
browser — and ``api.routers.replays`` is explicit about why the server must not
recompute one ("one engine, so a stored attempt can't disagree with the replay
that produced it") — so paper trades arrive by POST and are booked from there.
A **Simulator attempt** is the same browser engine over a tape that already
happened, and arrives the same way, on the autosave it was already making.

PAPER AND REPLAY ARE ACCOUNTS, AND BOTH ARE TAGGED `replay`. ``sim/store.py``
and ``replays.py`` both say synthetic fills must never reach `journal.db` and
"contaminate live stats". They now do reach it — deliberately, so that they show
up in the account list like any other — and the stated harm is prevented by the
mechanism the codebase already uses for ATAS's own `Replay` account:
``sessions.mode='replay'``, which every default statistics query filters on.
Visible and filterable; never in the real-money numbers unless asked for.

THE LIVE HALF APPENDS; THE REPLAY HALF MIRRORS. Everything above the fold here
is INSERT OR IGNORE on a content hash, because a live trade is final the moment
it happens and re-posting one must be free. An attempt's trades are not final —
rewinding past a fill un-happens it — so ``book_attempt`` replaces the source
file's rows outright on every save (``db.replace_journal``). Same row builder,
opposite write semantics, and the difference is the whole reason both are here.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import db
from ..config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS, ET_TZ
from ..ingest import _journal_key, _local_iso, _utc_iso

#: The account name paper trades are booked under. Matches the reserved id in
#: ``journal.live.routing.PAPER`` — the selector, the broker and the journal all
#: call it the same thing, so filtering by account in the UI finds it.
PAPER_ACCOUNT = "paper"

#: Prefix for every `source_file` this module writes. `source_file` is an
#: imports-relative path string and doubles as the sitting id across seven
#: tables, so the one hard requirement is that it cannot collide with a real
#: export — those are bare filenames at the root, or under `backtest/`.
LIVE_PREFIX = "live"

#: The account Simulator attempts book under. Deliberately *not* `paper`: both
#: are synthetic and both are tagged `replay`, but one was taken against a tape
#: arriving once and the other against a tape that can be re-run — and the
#: account filter is the only place that difference is visible at a glance.
REPLAY_ACCOUNT = "replay"

#: Prefix for a Simulator attempt's `source_file`. The attempt id is already
#: `<date>_<SYMBOL>_<UTC stamp>` and unique per sitting, so `replay/<id>` needs
#: nothing added to be one sitting, one source file.
REPLAY_PREFIX = "replay"

#: A replayed contract carries no venue — the Simulator addresses a day by
#: symbol alone. CME because every instrument the tick cache holds is a CME
#: future, and `NQM6` alone would not match `NQM6@CME` from an ATAS export of
#: the same contract.
REPLAY_EXCHANGE = "CME"

#: Prices and P&L are rounded before they reach the row, because `dedupe_key`
#: hashes them as strings: the same trade booked twice from differently-rounded
#: floats would produce two different keys and two rows. Rounded once, here.
PRICE_DP = 6
MONEY_DP = 2


def source_file_for(account: str, session_date: date) -> str:
    """The sitting a trade belongs to: one per account per session date.

    Mirrors what an ATAS export is — one file, one sitting — so everything keyed
    on `source_file` (session mode, notes, videos, `db.delete_attempt`) works on
    a live day without knowing it is one.
    """
    return f"{LIVE_PREFIX}/{account}/{session_date.isoformat()}"


_EPOCH = datetime(1970, 1, 1)


def _iso(
    ms: int | float | None, wall_zone: ZoneInfo | None = None
) -> tuple[str | None, str | None]:
    """Epoch ms -> (local ISO, UTC ISO), the pair `atas_journal` stores.

    Local means **Eastern**, matching ``ingest.DEFAULT_SOURCE_TZ``: the column is
    parsed with ``utc=True`` on read, so what actually has to be right is the
    offset, and using a second convention here would put live rows an hour off
    imported ones every March and November.

    **Two different things call themselves epoch-ms upstream, and confusing them
    costs four hours.** The broker's fills carry a true instant. A replayed tape
    does not: ``api.tape_codec.local_ms`` projects each tick into the display
    zone and *drops the zone*, so the number is a wall clock that happens to be
    counted from 1970 — the client wants `09:30` to be 09:30 without knowing what
    day's offset was. Pass ``wall_zone`` for the second kind and the wall clock
    is read back in the zone it was written in; leave it None for the first.
    """
    if ms is None:
        return None, None
    if wall_zone is None:
        naive = datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc) \
            .astimezone(ET_TZ).replace(tzinfo=None)
    else:
        naive = _EPOCH + timedelta(milliseconds=float(ms))
        if wall_zone is not ET_TZ:
            naive = naive.replace(tzinfo=wall_zone) \
                .astimezone(ET_TZ).replace(tzinfo=None)
    return _local_iso(naive, ET_TZ), _utc_iso(naive, ET_TZ)


def journal_row(*, account: str, instrument: str, source_file: str,
                trade: dict, wall_zone: ZoneInfo | None = None,
                tag: str = "live") -> dict:
    """One closed round trip as an ``atas_journal`` row — all sixteen columns.

    Every column is supplied even where the value is None, and that is not
    defensive: ``db._insert_ignore`` does ``r[c] for c in cols``, so a missing
    key is a ``KeyError`` rather than a NULL. This function existing is what
    stops two callers each getting that wrong differently.

    **The sign of ``open_volume`` carries the direction** — ``trades.py`` reads
    `Long if open_volume > 0 else Short` and nothing else says which way the
    trade went. Getting this backwards would silently invert every live trade in
    the journal, which is why it is the one line with a test of its own.
    """
    size = abs(float(trade["size"]))
    long = trade["side"] == "long"
    entry = round(float(trade["entry_price"]), PRICE_DP)
    exit_px = round(float(trade["exit_price"]), PRICE_DP)
    pnl = round(float(trade["pnl"]), MONEY_DP)
    open_local, open_utc = _iso(trade.get("entry_ms"), wall_zone)
    close_local, close_utc = _iso(trade.get("exit_ms"), wall_zone)

    row = {
        "account": account,
        "instrument": instrument,
        "open_ts_local": open_local,
        "close_ts_local": close_local,
        "open_ts_utc": open_utc,
        "close_ts_utc": close_utc,
        "open_price": entry,
        # Signed: + long, - short. See the docstring.
        "open_volume": size if long else -size,
        "close_price": exit_px,
        # The closing side, mirroring the open. ATAS exports it the same way and
        # `_finalize` takes its absolute value, so only the open's sign is read —
        # but a row that disagreed with itself would be a trap for a later reader.
        "close_volume": -size if long else size,
        # Points, not currency — `price_pnl` is the raw price difference.
        "price_pnl": round(float(trade.get("pts", exit_px - entry)), PRICE_DP),
        # Ticks are instrument-specific and the broker does not send them; None
        # is honest and the column is nullable. `pnl` is what statistics use.
        "profit_ticks": None,
        "pnl": pnl,
        # Where the trade came from and why it closed, in the one free-text
        # column the schema has. Read by nobody, but it is the difference
        # between a mystery row and an explained one a year from now.
        "comment": f"{tag}:{trade.get('reason', 'manual')}",
        "source_file": source_file,
    }
    # Verbatim from the importer, so a live row and an imported row that describe
    # the same trade collapse to one. Not expected to happen (the two sources are
    # disjoint by decision) but the alternative — a second hash recipe — would
    # guarantee they never could.
    row["dedupe_key"] = _journal_key(row)
    return row


def book_trade(conn: sqlite3.Connection, *, account: str, instrument: str,
               mode: str, session_date: date, trade: dict) -> bool:
    """Write one closed round trip to the journal. True if it was new.

    **The session row goes first, and that ordering is load-bearing.**
    ``api.scope.DEFAULT_SESSION`` makes a `source_file` with no `sessions` row
    read as ``mode='replay'`` — so a real trade booked without one would quietly
    drop out of the real-money statistics it belongs in, with nothing on screen
    to say so. Registering the sitting before inserting into it removes the
    window in which that can be true.

    ``upsert_session`` is INSERT OR IGNORE, so this is safe to call per trade:
    the first booking of a sitting fixes its mode, and a mode later changed in
    the UI is never overwritten by the next trade.

    The caller holds ``deps.db_lock()``.
    """
    src = source_file_for(account, session_date)
    db.upsert_session(conn, src, mode, account)
    row = journal_row(account=account, instrument=instrument,
                      source_file=src, trade=trade)
    return db.insert_journal(conn, [row]) > 0


def execution_row(*, account: str, instrument: str, source_file: str,
                  fill: dict) -> dict | None:
    """One fill as an ``executions`` row, or None if it cannot be identified.

    Executions are **markers, not money** — ``trades._window_fills`` matches them
    into a trade by account, instrument and time window, and the trade-detail
    chart draws them. P&L never comes from here (ATAS ships truncated Executions
    sheets, so the whole read path is written to degrade to "no markers" rather
    than to a wrong number), which is why a fill we cannot key is dropped rather
    than given a synthetic id.

    ``exchange_id`` is the table's primary key and the one genuine natural key in
    the schema. Rithmic's ``fill_id`` is the candidate: ``exchange_order_id``
    identifies an *order*, and a part-filled order produces several fills that
    would then collapse into one. **Unverified against a real plant** — if
    ``fill_id`` turns out to be blank or non-unique, this returns None and the
    only cost is missing markers.
    """
    fid = str(fill.get("fill_id") or "").strip()
    if not fid or fill.get("price") is None or not fill.get("size"):
        return None
    local, utc = _iso(fill.get("ms"))
    return {
        "exchange_id": f"rithmic:{fid}",
        "account": account,
        "instrument": instrument,
        "ts_local": local,
        "ts_utc": utc,
        "direction": "Buy" if fill.get("side") == "buy" else "Sell",
        "price": round(float(fill["price"]), PRICE_DP),
        "volume": abs(float(fill["size"])),
        # Rithmic does not report commission on a fill notification. 0.0 rather
        # than None because the column is summed downstream, and the journal
        # already treats gross and net as equal (`trades.py` hardcodes 0.0 too).
        "commission": 0.0,
        "source_file": source_file,
    }


def book_fill(conn: sqlite3.Connection, *, account: str, instrument: str,
              session_date: date, fill: dict) -> bool:
    """Record one fill, for the trade-detail chart's markers. True if new.

    Best-effort by design: an unkeyable fill is skipped, and a missing execution
    costs a marker rather than a number.
    """
    row = execution_row(account=account, instrument=instrument,
                        source_file=source_file_for(account, session_date),
                        fill=fill)
    if row is None:
        return False
    return db.insert_executions(conn, [row]) > 0


def book_trades(conn: sqlite3.Connection, *, account: str, instrument: str,
                mode: str, session_date: date, trades: list[dict]) -> int:
    """``book_trade`` over a list. Returns how many were new.

    The paper path posts in batches (a page that has been open a while may have
    several to catch up on), and re-posting is expected rather than exceptional —
    ``INSERT OR IGNORE`` on the content hash is what makes that free.
    """
    if not trades:
        return 0
    src = source_file_for(account, session_date)
    db.upsert_session(conn, src, mode, account)
    rows = [journal_row(account=account, instrument=instrument,
                        source_file=src, trade=t) for t in trades]
    return db.insert_journal(conn, rows)


def day_trades(conn: sqlite3.Connection, *, account: str, session_date: date,
               symbol: str | None = None) -> list[dict]:
    """Everything this account booked on a session date, oldest close first.

    **The read-back this module's docstring says nothing did.** The broker's
    day — its running total, and therefore the daily loss stop — is folded out
    of a fill stream that only exists while the process does, so a restart used
    to hand a trader a fresh $500 of rope on a day they had already spent it.
    The rows were never lost; nothing read them. This is what reads them.

    Shaped as ``Broker._emit_trade`` shapes a round trip, because the two feed
    the same places: the day record, the panel's blotter and the chart's trade
    marks. Two fields cannot come back and are None rather than guessed —
    ``r`` (the journal has no risk column, and inventing a denominator would
    put a number on the panel that no stop ever justified) and the fill-level
    detail behind a scale-out, which was already one row per closed lot.

    ``symbol`` filters to one contract, matched against the instrument's symbol
    half — the blotter is per contract, while a day's money is not.
    """
    src = source_file_for(account, session_date)
    rows = conn.execute(
        """SELECT instrument, open_ts_utc, close_ts_utc, open_price,
                  close_price, open_volume, price_pnl, pnl, comment
             FROM atas_journal
            WHERE source_file = ?
            ORDER BY close_ts_utc, open_ts_utc""",
        (src,)).fetchall()

    out: list[dict] = []
    for r in rows:
        instrument = str(r[0] or "")
        sym = instrument.split("@", 1)[0]
        if symbol is not None and sym != symbol:
            continue
        size = abs(float(r[5] or 0.0))
        if not size:
            continue
        comment = str(r[8] or "")
        out.append({
            "id": len(out) + 1,
            "side": "long" if float(r[5]) > 0 else "short",
            "size": int(size) if float(size).is_integer() else size,
            "entry_price": float(r[3]),
            "entry_ms": _ms(r[1]),
            "exit_price": float(r[4]),
            "exit_ms": _ms(r[2]),
            "pts": float(r[6] or 0.0),
            "pnl": float(r[7] or 0.0),
            "r": None,
            "reason": comment.split(":", 1)[1] if ":" in comment else "manual",
            "symbol": sym,
            "instrument": instrument,
            # Says out loud that this trade came back off disk rather than out
            # of a fill this process saw. The panel reads it; so does anyone
            # debugging a total that disagrees with the broker's own.
            "restored": True,
        })
    return out


def _ms(iso: str | None) -> int | None:
    """A stored UTC timestamp back to epoch ms. None on anything unparseable.

    The inverse of ``_iso``'s first return path, and only that one: the wall
    clock kind (``wall_zone``) belongs to replayed tapes, which never come back
    through here — a live row was written from a true instant.
    """
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except ValueError:
        return None


# --- Simulator attempts -----------------------------------------------------
#
# The third caller, and the one that had to wait for `replace_journal`. A live
# trade is final the moment it books; an attempt's trades are not, because
# rewinding past a fill un-happens it. So this pair mirrors an attempt rather
# than appending to it: `book_attempt` makes the journal say exactly what
# `trades.json` says, every autosave, and `unbook_attempt` withdraws the lot.
#
# Executions are deliberately not written. `journal.replays` will not re-derive
# what the browser's engine computed, and a `Trade` carries the position's
# *average* entry and the moment the position opened — so a scale-out would
# produce two fill markers at one price that no fill ever happened at. A missing
# marker is honest; an invented one is not.


def source_file_for_attempt(attempt_id: str) -> str:
    """The sitting one Simulator attempt is: ``replay/<attempt id>``.

    One attempt, one source file — not one per *day*, the way the live account
    groups. Replaying the same session twice is two sittings on purpose
    (``replays.create`` counts them as ``repeat_index``), and collapsing them
    here would merge a cold read with a re-run that already knew the answer.
    """
    return f"{REPLAY_PREFIX}/{attempt_id}"


def _sitting_stamp(created_at: str | None) -> str | None:
    """An attempt's ``created_at`` in the form ``file_mtime`` is written in.

    ``replays`` stamps UTC as ``…Z``; ``ingest._disk_mtime_iso`` writes
    ``…+00:00``. Both parse, but ``api.routers.calendar`` picks a day's latest
    take with a plain ``max()`` over the raw strings, and ``Z`` sorts above
    ``+`` — so a sitting and an export stamped in the same second would order by
    spelling. Normalised to one shape here rather than teaching every reader
    both.
    """
    if not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _attempt_trade(t: dict) -> dict | None:
    """One ``replaySim.Trade`` as the dict ``journal_row`` takes, or None.

    The browser's shape is camelCase and the row builder's is snake_case; this
    is the whole of the translation, kept in one place so the field names live
    next to the engine that emits them (``frontend/src/lib/replaySim.ts``).

    Returns None rather than raising for a trade missing a price or a side: an
    autosave that 500s loses the sitting's record, and the sitting is the thing
    this feature exists to keep. A dropped row is visible in the count the
    router returns.
    """
    side = t.get("side")
    if side not in ("long", "short"):
        return None
    try:
        entry = float(t["entryPrice"])
        exit_px = float(t["exitPrice"])
        size = float(t["size"])
        pnl = float(t["pnl"])
    except (KeyError, TypeError, ValueError):
        return None
    if not size:
        return None
    return {
        "side": side,
        "size": size,
        "entry_price": entry,
        "exit_price": exit_px,
        "entry_ms": t.get("entryMs"),
        "exit_ms": t.get("exitMs"),
        "pnl": pnl,
        # Signed by direction already, straight from the engine — unlike
        # `journal_row`'s fallback, which cannot know the side it is subtracting.
        "pts": t.get("pts", (exit_px - entry) * (1 if side == "long" else -1)),
        "reason": t.get("reason", "manual"),
    }


def book_attempt(conn: sqlite3.Connection, *, attempt: dict,
                 trades: list[dict]) -> int:
    """Mirror one attempt's trades into the journal. Returns how many rows.

    **Replaces rather than appends**, so a rewind that erased a fill erases the
    journal row too — see ``db.replace_journal``. Called on every autosave, which
    makes it a mirror rather than an event: whatever `trades.json` holds is what
    the journal holds, and the two cannot drift.

    Tagged ``mode='replay'`` on the session, which is what keeps a practice fill
    out of the real-money statistics while leaving it visible everywhere a trade
    is listed. The session row goes first for the reason ``book_trade`` gives.

    The caller holds ``deps.db_lock()``.
    """
    attempt_id = str(attempt.get("id") or "")
    symbol = str(attempt.get("symbol") or "").strip().upper()
    if not attempt_id or not symbol:
        return 0
    src = source_file_for_attempt(attempt_id)
    # The zone the tape's wall clocks were projected into, stored on the attempt
    # so they stay invertible. Unknown zones fall back to the default rather than
    # to UTC — a wrong-but-plausible offset beats a four-hour shift.
    zone = DISPLAY_TZS.get(
        attempt.get("tz") or DEFAULT_DISPLAY_TZ, DISPLAY_TZS[DEFAULT_DISPLAY_TZ]
    )
    instrument = f"{symbol}@{REPLAY_EXCHANGE}"

    db.upsert_session(conn, src, "replay", REPLAY_ACCOUNT)
    # When the sitting happened, in the slot an ATAS export's "Date modified"
    # occupies. Everything that orders a day's takes reads
    # ``imported_files.file_mtime`` — the calendar table's Modified column, the
    # day explorer's attempt list — so a practice day without a row here sorts
    # as though it had never been traded, which is the one thing a practice
    # record must not do.
    #
    # ``created_at`` (the first fill, which is what opens an attempt) and not
    # ``updated_at``: a sitting's place in the day is where it *started*, and a
    # stamp that moved on every autosave would reshuffle the list underneath you
    # while you were still trading it.
    db.mark_imported(conn, src, file_mtime=_sitting_stamp(attempt.get("created_at")))
    rows = []
    for t in trades:
        norm = _attempt_trade(t)
        if norm is None:
            continue
        rows.append(journal_row(
            account=REPLAY_ACCOUNT, instrument=instrument, source_file=src,
            trade=norm, wall_zone=zone, tag="replay",
        ))
    return db.replace_journal(conn, src, rows)


def unbook_attempt(conn: sqlite3.Connection, attempt_id: str) -> None:
    """Withdraw a deleted attempt from the journal, sitting record and all.

    ``replays.delete`` is documented as "delete the folder and it is gone"; that
    promise is what this keeps. All three halves are needed: the trades, the
    ``sessions`` row that would otherwise outlive them in the sessions list, and
    the ``imported_files`` stamp that would otherwise keep a deleted sitting's
    start time in the day's Modified column.
    """
    src = source_file_for_attempt(attempt_id)
    db.replace_journal(conn, src, [])
    db.delete_session(conn, src)
    db.clear_imported(conn, src)
