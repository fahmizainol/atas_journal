"""Live-chart trades reaching the journal.

What these guard is the half that fails *silently*. A trade booked with a wrong
column does not raise — it becomes a Short that was a Long, or a row that never
appears because its session was never registered. Specifically:

  - **the direction sign.** ``trades.py`` reads `Long if open_volume > 0`, and
    nothing else in the row says which way the trade went. Inverted, every live
    trade in the journal is backwards and nothing complains.
  - **the session row.** ``api.scope.DEFAULT_SESSION`` makes an unregistered
    `source_file` read as ``mode='replay'`` — so a real trade booked without one
    quietly leaves the real-money statistics it belongs in.
  - **paper staying out of live stats.** It is in the journal now, by decision,
    and the only thing keeping it out of the numbers is its mode.
  - **idempotence**, because the paper path re-posts by design.

Every assertion goes through ``api.scope.resolve_scope`` rather than a SELECT.
The row is not the product — what the Trades page shows is, and the two are
separated by a lot of derivation.

Run directly:  ``.venv/bin/python tests/test_live_booking.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "tests"))

from api import deps  # noqa: E402
from helpers import make_scope  # noqa: E402
from journal import db  # noqa: E402
from journal.live import booking as bk  # noqa: E402

DAY = date(2026, 8, 7)
# 2026-08-07 09:31:00 ET and 09:41:00 ET, as epoch ms.
ENTRY_MS = 1786109460000
EXIT_MS = 1786110060000


def _trade(**over) -> dict:
    base = dict(side="long", size=2, entry_price=20000.0, entry_ms=ENTRY_MS,
                exit_price=20010.0, exit_ms=EXIT_MS, pts=10.0, pnl=400.0,
                r=2.0, reason="target")
    base.update(over)
    return base


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(Path(tmp) / "test.db")
        c.executescript(db.SCHEMA)
        db.init_db(c)
        deps._conn = c
        yield c
        deps._conn = None


def _rows(**kw) -> list[dict]:
    """What the Trades page would show, through the real read path.

    `make_scope` is the shared wrapper for calling the FastAPI dependency
    directly (tests/helpers.py) — `resolve_scope`'s defaults are `Query(...)`
    sentinels, so every parameter has to be passed.
    """
    df = make_scope(include_archived=True, **kw).filtered
    return [] if df.empty else df.to_dict("records")


# --- the row -----------------------------------------------------------------


def test_a_booked_trade_comes_back_out_of_the_trades_page(conn):
    assert bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME",
                         mode="live", session_date=DAY, trade=_trade())
    (t,) = _rows()
    assert t["account"] == "DEMO1"
    assert t["instrument"] == "NQU6@CME"
    assert t["direction"] == "Long"
    assert t["avg_entry"] == 20000.0 and t["avg_exit"] == 20010.0
    assert t["net_pnl"] == 400.0
    assert t["session_mode"] == "live"
    assert t["source_file"] == "live/DEMO1/2026-08-07"


def test_the_direction_comes_from_the_sign_of_open_volume(conn):
    """The one column whose mistake is invisible: nothing else in the row says
    which way the trade went, so an inverted sign is a journal full of
    backwards trades and no error anywhere."""
    bk.book_trade(conn, account="A", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade(side="short", pnl=-400.0))
    (t,) = _rows()
    assert t["direction"] == "Short"
    assert t["net_pnl"] == -400.0


def test_a_short_row_does_not_disagree_with_itself(conn):
    r = bk.journal_row(account="A", instrument="B", source_file="c",
                       trade=_trade(side="short"))
    assert r["open_volume"] < 0 and r["close_volume"] > 0
    r = bk.journal_row(account="A", instrument="B", source_file="c",
                       trade=_trade(side="long"))
    assert r["open_volume"] > 0 and r["close_volume"] < 0


def test_every_column_the_inserter_wants_is_present():
    """`db._insert_ignore` does `r[c] for c in cols` — a missing key is a
    KeyError at insert time, not a NULL. This is the guard for that."""
    cols = ["dedupe_key", "account", "instrument", "open_ts_local",
            "close_ts_local", "open_ts_utc", "close_ts_utc", "open_price",
            "open_volume", "close_price", "close_volume", "price_pnl",
            "profit_ticks", "pnl", "comment", "source_file"]
    r = bk.journal_row(account="A", instrument="B", source_file="c",
                       trade=_trade())
    assert set(cols) <= set(r), set(cols) - set(r)


def test_the_timestamps_land_where_the_clock_says(conn):
    """Local is Eastern with its offset, matching the importer's convention.
    A second convention here would put live rows an hour off imported ones for
    half the year."""
    r = bk.journal_row(account="A", instrument="B", source_file="c",
                       trade=_trade())
    assert r["open_ts_local"].startswith("2026-08-07T09:31:00-04:00")
    assert r["open_ts_utc"].startswith("2026-08-07T13:31:00+00:00")


# --- the session row ---------------------------------------------------------


def test_the_sitting_is_registered_before_the_trade_is_inserted(conn):
    """Unregistered, a `source_file` reads as replay (api.scope.DEFAULT_SESSION),
    so a real trade would quietly leave the real-money statistics."""
    bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade())
    sessions = db.sessions_map(conn)
    assert sessions["live/DEMO1/2026-08-07"]["mode"] == "live"
    assert sessions["live/DEMO1/2026-08-07"]["account"] == "DEMO1"


def test_a_trade_with_no_session_row_would_read_as_replay(conn):
    """The failure the ordering above prevents, demonstrated rather than
    asserted about. This is what a bug in `book_trade` would look like."""
    row = bk.journal_row(account="DEMO1", instrument="NQU6@CME",
                         source_file="live/DEMO1/2026-08-07", trade=_trade())
    db.insert_journal(conn, [row])          # no upsert_session — the bug
    (t,) = _rows()
    assert t["session_mode"] == "replay"    # a real trade, hidden from live stats


def test_a_mode_chosen_in_the_ui_survives_later_trades(conn):
    """upsert_session is INSERT OR IGNORE, so booking per trade is safe."""
    bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade())
    conn.execute("UPDATE sessions SET mode='backtest' WHERE source_file=?",
                 ("live/DEMO1/2026-08-07",))
    conn.commit()
    bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade(entry_ms=ENTRY_MS + 60000,
                                                 pnl=100.0))
    assert db.sessions_map(conn)["live/DEMO1/2026-08-07"]["mode"] == "backtest"


# --- paper -------------------------------------------------------------------


def test_paper_is_its_own_account_and_stays_out_of_live_statistics(conn):
    bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade())
    bk.book_trade(conn, account=bk.PAPER_ACCOUNT, instrument="NQU6@CME",
                  mode="replay", session_date=DAY,
                  trade=_trade(pnl=999.0, entry_ms=ENTRY_MS + 120000))

    # Visible, and its own account like any other.
    assert {t["account"] for t in _rows()} == {"DEMO1", bk.PAPER_ACCOUNT}
    # Filterable by account, like any other.
    assert len(_rows(accounts=bk.PAPER_ACCOUNT)) == 1
    # And excluded when you ask only for live money — the whole point of the
    # replay tag, and what stops 999.0 of practice landing in the numbers.
    live = _rows(modes="live")
    assert [t["account"] for t in live] == ["DEMO1"]
    assert sum(t["net_pnl"] for t in live) == 400.0


# --- re-posting --------------------------------------------------------------


def test_booking_the_same_trade_twice_is_a_no_op(conn):
    """The paper path re-posts by design — a page that has been open a while
    catches up on whatever it has. INSERT OR IGNORE on the content hash is what
    makes that free."""
    t = _trade()
    assert bk.book_trade(conn, account="A", instrument="NQU6@CME", mode="live",
                         session_date=DAY, trade=t) is True
    assert bk.book_trade(conn, account="A", instrument="NQU6@CME", mode="live",
                         session_date=DAY, trade=t) is False
    assert len(_rows()) == 1


def test_rounding_happens_once_so_a_repost_still_dedupes(conn):
    """`dedupe_key` hashes the prices as strings, so the same trade arriving
    with more decimal places must still produce one row."""
    a = _trade(entry_price=20000.0000001, pnl=400.000001)
    b = _trade(entry_price=20000.0, pnl=400.0)
    bk.book_trade(conn, account="A", instrument="B", mode="live",
                  session_date=DAY, trade=a)
    bk.book_trade(conn, account="A", instrument="B", mode="live",
                  session_date=DAY, trade=b)
    assert len(_rows()) == 1


def test_a_batch_books_what_is_new_and_ignores_the_rest(conn):
    first = _trade()
    # A genuinely separate trade: opened after the first one closed. See the
    # test below for what happens when they overlap.
    later = _trade(entry_ms=EXIT_MS + 60000, exit_ms=EXIT_MS + 120000, pnl=50.0)
    assert bk.book_trades(conn, account=bk.PAPER_ACCOUNT, instrument="NQU6@CME",
                          mode="replay", session_date=DAY,
                          trades=[first]) == 1
    assert bk.book_trades(conn, account=bk.PAPER_ACCOUNT, instrument="NQU6@CME",
                          mode="replay", session_date=DAY,
                          trades=[first, later]) == 1
    assert len(_rows()) == 2
    assert bk.book_trades(conn, account="x", instrument="y", mode="replay",
                          session_date=DAY, trades=[]) == 0


def test_a_scaled_out_position_reads_as_one_trade_not_two(conn):
    """Not a defect — the property that makes this fit the rest of the journal.

    The broker emits a round trip each time size comes off, so scaling out of
    one position produces two of them, sharing an entry. Written as two lots,
    ``build_logical_trades`` nets them back into a single logical trade of the
    full size — which is what the position actually was, and how an ATAS export
    of the same scale-out would read. The lot rows stay separate underneath, so
    nothing is lost.
    """
    entry = _trade(size=1, pnl=100.0, reason="reduce")
    rest = _trade(size=2, exit_ms=EXIT_MS + 60000, exit_price=20020.0,
                  pnl=800.0, reason="target")
    bk.book_trades(conn, account="A", instrument="NQU6@CME", mode="live",
                   session_date=DAY, trades=[entry, rest])
    (t,) = _rows()
    assert len(t["lot_keys"]) == 2          # two lots underneath
    assert t["direction"] == "Long"
    assert t["net_pnl"] == 900.0            # and one trade over them


def test_a_sitting_is_one_account_one_day(conn):
    assert bk.source_file_for("DEMO1", DAY) == "live/DEMO1/2026-08-07"
    assert bk.source_file_for(bk.PAPER_ACCOUNT, DAY) == "live/paper/2026-08-07"
    # Cannot collide with an ATAS export, which is a bare filename at the root
    # or lives under `backtest/`.
    assert bk.source_file_for("A", DAY).startswith("live/")


# --- the endpoint ------------------------------------------------------------


def _post(**over):
    from api.routers import live_orders

    body = dict(symbol="nqu6", exchange="cme", date=DAY.isoformat(),
                trades=[_paper()])
    body.update(over)
    return live_orders.journal_paper_trades(live_orders.PaperTradesIn(**body))


def _paper(**over) -> dict:
    """A trade in the browser's shape — what `replaySim` hands over."""
    base = dict(side="long", size=1.0, entry_price=20000.0, entry_ms=ENTRY_MS,
                exit_price=20010.0, exit_ms=EXIT_MS, pnl=200.0, pts=10.0,
                reason="target")
    base.update(over)
    return base


def test_the_paper_endpoint_books_and_normalises_the_contract(conn):
    body = _post()
    assert body["written"] == 1 and body["received"] == 1
    assert body["source_file"] == "live/paper/2026-08-07"
    (t,) = _rows()
    # Lower-cased on the way in, stored the way the rest of the journal spells
    # it, so it filters alongside imported rows rather than beside them.
    assert t["instrument"] == "NQU6@CME"
    assert t["account"] == "paper" and t["session_mode"] == "replay"


def test_paper_journaling_does_not_need_routing_switched_on(conn, monkeypatch):
    """Paper reaches no broker, so none of the routing gates are about it. A
    checkout with LIVE_ROUTING unset still practises on the live chart, and
    those trades should still be recorded."""
    from journal.live import routing as rt

    monkeypatch.setattr(rt, "policy",
                        lambda: rt.Policy(enabled=False, max_qty=5))
    assert _post()["written"] == 1


def test_the_endpoint_re_posts_without_duplicating(conn):
    t = _paper()
    assert _post(trades=[t])["written"] == 1
    assert _post(trades=[t])["written"] == 0
    assert len(_rows()) == 1


def test_a_bad_date_or_no_contract_is_a_422(conn):
    from fastapi import HTTPException

    for bad in ({"date": "not-a-date"}, {"symbol": "  "}):
        with pytest.raises(HTTPException) as e:
            _post(**bad)
        assert e.value.status_code == 422


# --- fills -------------------------------------------------------------------


def _fill(**over) -> dict:
    base = dict(fill_id="F1", price=20000.0, size=1, side="buy", ms=ENTRY_MS)
    base.update(over)
    return base


def test_a_fill_becomes_a_marker_on_the_trade(conn):
    """Executions are markers, not money — the trade-detail chart matches them
    into a trade by account, instrument and time window."""
    bk.book_trade(conn, account="A", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade())
    assert bk.book_fill(conn, account="A", instrument="NQU6@CME",
                        session_date=DAY, fill=_fill())
    assert bk.book_fill(conn, account="A", instrument="NQU6@CME",
                        session_date=DAY,
                        fill=_fill(fill_id="F2", side="sell", price=20010.0,
                                   ms=EXIT_MS))
    (t,) = _rows()
    assert [f["direction"] for f in t["fills"]] == ["Buy", "Sell"]
    assert [f["price"] for f in t["fills"]] == [20000.0, 20010.0]


def test_a_fill_that_cannot_be_keyed_is_dropped_not_invented(conn):
    """`exchange_id` is the primary key and the one natural key in the schema.
    A synthetic one would let the same fill land twice under two ids, which is
    worse than no marker."""
    for bad in ({"fill_id": ""}, {"fill_id": None}, {"price": None}, {"size": 0}):
        assert bk.book_fill(conn, account="A", instrument="B",
                            session_date=DAY, fill=_fill(**bad)) is False
    assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_the_same_fill_twice_is_one_execution(conn):
    assert bk.book_fill(conn, account="A", instrument="B", session_date=DAY,
                        fill=_fill()) is True
    assert bk.book_fill(conn, account="A", instrument="B", session_date=DAY,
                        fill=_fill()) is False


def test_a_trade_with_no_fills_recorded_simply_has_no_markers(conn):
    """The whole read path degrades to "no markers" rather than a wrong number —
    which is why P&L never comes from this table."""
    bk.book_trade(conn, account="A", instrument="NQU6@CME", mode="live",
                  session_date=DAY, trade=_trade())
    (t,) = _rows()
    assert t["fills"] is None
    assert t["net_pnl"] == 400.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
