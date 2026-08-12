"""Simulator attempts reaching the journal.

The mirror between `data/replays/` and `journal.db` has three ways to be wrong
and none of them raise:

  - **the clock.** A replayed tape's timestamps are wall clocks projected into
    the display zone with the zone dropped (``api.tape_codec.local_ms``), not
    instants. Read them the way a broker fill is read and every practice trade
    lands four hours off, on the right day and in the wrong session.
  - **withdrawal.** Rewinding past a fill un-happens it. The live path can only
    ever append (INSERT OR IGNORE on a content hash), so an attempt booked that
    way would keep trades its own record says never happened.
  - **the mode tag.** It is the only thing keeping practice out of the
    real-money numbers now that practice is in the journal at all.

Assertions go through ``api.scope.resolve_scope`` rather than a SELECT, for the
reason ``test_live_booking`` gives: the row is not the product, the Trades page
is, and a lot of derivation sits between them.

Run directly:  ``.venv/bin/python tests/test_replay_journal.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from api import deps  # noqa: E402
from api.routers import calendar as cal  # noqa: E402
from api.routers import replays as router  # noqa: E402
from helpers import make_scope  # noqa: E402
from journal import db, replays  # noqa: E402
from journal.live import booking as bk  # noqa: E402

# 2026-08-07 09:31 and 09:41 as the Simulator counts: New York wall clock,
# epoch-ms, no offset applied. `1786109460000` — what the same moment would be
# as a true instant — is deliberately *not* this number.
ENTRY_MS = 1786095060000
EXIT_MS = 1786095660000

TAPE = {"n": 900_000, "t0": ENTRY_MS - 3_600_000, "end": EXIT_MS + 3_600_000,
        "rth_open_ms": ENTRY_MS - 60_000}
LOG = {"orders": [{"id": 1, "type": "market", "side": "long", "ms": 1, "idx": 5}],
       "closes": [], "brackets": []}


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(Path(tmp) / "test.db")
        c.executescript(db.SCHEMA)
        db.init_db(c)
        deps._conn = c
        original = replays.REPLAYS_DIR
        replays.REPLAYS_DIR = Path(tmp) / "replays"
        try:
            yield c
        finally:
            replays.REPLAYS_DIR = original
            deps._conn = None


def _attempt(symbol="NQU6", date="2026-08-07", tz="New York") -> dict:
    return replays.create(symbol=symbol, root="NQ", date=date, tz=tz,
                          engine_version=1, tape=TAPE, prefs={"size": 1},
                          started_ms=TAPE["rth_open_ms"])


def _trade(**over) -> dict:
    """A trade in the browser's shape — what ``replaySim.ts`` emits."""
    base = dict(id=1, side="long", size=2, entryPrice=20000.0, entryMs=ENTRY_MS,
                openType="market", exitPrice=20010.0, exitMs=EXIT_MS,
                reason="target", pts=10.0, pnl=400.0, r=2.0, rCash=2.0)
    base.update(over)
    return base


def _save(attempt: dict, trades: list[dict], **over) -> dict:
    body = dict(log=LOG, trades=trades, summary={}, discarded=[], rewinds=None,
                clock_ms=float(EXIT_MS), status="active")
    body.update(over)
    return router.save_replay(attempt["id"], router.SaveIn(**body))


def _rows(**kw) -> list[dict]:
    df = make_scope(include_archived=True, **kw).filtered
    return [] if df.empty else df.to_dict("records")


# --- the round trip ----------------------------------------------------------


def test_an_attempts_trades_come_back_out_of_the_trades_page(conn):
    a = _attempt()
    assert _save(a, [_trade()])["journaled"] == 1
    (t,) = _rows()
    assert t["account"] == "replay"
    assert t["instrument"] == "NQU6@CME"
    assert t["direction"] == "Long"
    assert t["avg_entry"] == 20000.0 and t["avg_exit"] == 20010.0
    assert t["net_pnl"] == 400.0
    assert t["session_mode"] == "replay"
    assert t["source_file"] == f"replay/{a['id']}"


def test_a_short_attempt_reads_as_a_short(conn):
    """The sign of `open_volume` is the only thing in the row that says which
    way the trade went — see ``test_live_booking`` for the same guard."""
    _save(_attempt(), [_trade(side="short", pnl=-400.0, pts=-10.0)])
    (t,) = _rows()
    assert t["direction"] == "Short" and t["net_pnl"] == -400.0


def test_the_sitting_is_registered_so_practice_is_tagged_replay(conn):
    a = _attempt()
    _save(a, [_trade()])
    sess = db.sessions_map(conn)[f"replay/{a['id']}"]
    assert sess["mode"] == "replay"
    assert sess["account"] == "replay"


# --- the clock ---------------------------------------------------------------


def test_a_wall_clock_tape_is_not_read_as_an_instant(conn):
    """The trap. `local_ms` projects each tick into the display zone and drops
    the zone, so 09:31 in the tape is 09:31 — reading it as a UTC instant would
    book the trade at 05:31, inside the same session date and four hours out."""
    a = _attempt()
    _save(a, [_trade()])
    (t,) = _rows()
    row = conn.execute(
        "SELECT open_ts_local, open_ts_utc FROM atas_journal"
    ).fetchone()
    assert row["open_ts_local"].startswith("2026-08-07T09:31:00-04:00")
    assert row["open_ts_utc"].startswith("2026-08-07T13:31:00+00:00")
    assert str(t["entry_ts_utc"]).startswith("2026-08-07 13:31")
    assert str(t["entry_ts_local"]).startswith("2026-08-07 09:31")


def test_a_tape_projected_into_another_zone_still_lands_in_eastern(conn):
    """The zone is stored on the attempt precisely so the wall clock stays
    invertible. A Kuala Lumpur projection of the same session is the same
    instant, and the journal stores Eastern either way."""
    ny = _attempt(symbol="NQU6")
    _save(ny, [_trade()])
    (ny_utc,) = conn.execute(
        "SELECT open_ts_utc FROM atas_journal WHERE source_file = ?",
        (f"replay/{ny['id']}",),
    ).fetchone()

    kl = _attempt(symbol="NQZ6", tz="Kuala Lumpur")
    # Same instant, written as a Kuala Lumpur wall clock: ET+12 in August.
    shift = 12 * 3_600_000
    _save(kl, [_trade(entryMs=ENTRY_MS + shift, exitMs=EXIT_MS + shift)])
    (kl_utc,) = conn.execute(
        "SELECT open_ts_utc FROM atas_journal WHERE source_file = ?",
        (f"replay/{kl['id']}",),
    ).fetchone()
    assert ny_utc == kl_utc


# --- withdrawal --------------------------------------------------------------


def test_a_rewind_takes_the_journal_row_with_it(conn):
    """The reason this path replaces instead of appending. `seekTo` truncates
    the log and the erased trade stops existing; a journal that only ever adds
    would keep it, and the practice record would be wrong in the one direction
    that flatters it."""
    a = _attempt()
    kept, erased = _trade(id=1), _trade(id=2, entryMs=EXIT_MS + 60_000,
                                        exitMs=EXIT_MS + 120_000, pnl=-250.0)
    assert _save(a, [kept, erased])["journaled"] == 2
    assert len(_rows()) == 2

    # Rewound past the second fill: the client re-sends what is left.
    assert _save(a, [kept])["journaled"] == 1
    (t,) = _rows()
    assert t["net_pnl"] == 400.0


def test_an_edited_trade_replaces_rather_than_doubles(conn):
    """A dedupe_key is derived from the trade's own content, so a trade whose
    exit moved is a *different* key — appending would leave both."""
    a = _attempt()
    _save(a, [_trade()])
    _save(a, [_trade(exitPrice=20020.0, pts=20.0, pnl=800.0)])
    (t,) = _rows()
    assert t["net_pnl"] == 800.0


def test_deleting_an_attempt_withdraws_the_trades_and_the_sitting(conn):
    """"Delete the folder and it is gone" is the promise journal.replays makes."""
    a = _attempt()
    _save(a, [_trade()])
    router.delete_replay(a["id"])
    assert _rows() == []
    assert f"replay/{a['id']}" not in db.sessions_map(conn)


def test_one_attempts_trades_do_not_disturb_another(conn):
    """Replace is scoped to the source file, and a source file is one sitting.
    Two attempts on the same session are two sittings on purpose."""
    first, second = _attempt(), _attempt()
    assert first["id"] != second["id"]
    _save(first, [_trade()])
    _save(second, [_trade(id=9, pnl=-100.0, exitPrice=19995.0, pts=-5.0)])
    assert len(_rows()) == 2
    _save(second, [])
    assert [t["net_pnl"] for t in _rows()] == [400.0]


# --- staying out of the real-money numbers -----------------------------------


def test_practice_is_visible_filterable_and_excluded_from_live(conn):
    from datetime import date

    bk.book_trade(conn, account="DEMO1", instrument="NQU6@CME", mode="live",
                  session_date=date(2026, 8, 7),
                  trade=dict(side="long", size=1, entry_price=20000.0,
                             entry_ms=1786109460000, exit_price=20005.0,
                             exit_ms=1786110060000, pts=5.0, pnl=100.0,
                             reason="target"))
    _save(_attempt(), [_trade(pnl=9999.0)])

    assert {t["account"] for t in _rows()} == {"DEMO1", "replay"}
    assert len(_rows(accounts="replay")) == 1
    live = _rows(modes="live")
    assert [t["account"] for t in live] == ["DEMO1"]
    assert sum(t["net_pnl"] for t in live) == 100.0


def test_replay_and_paper_are_separate_accounts(conn):
    """Both are synthetic and both are tagged replay, but one was taken against
    a tape arriving once and the other against a tape that can be re-run."""
    from datetime import date

    bk.book_trades(conn, account=bk.PAPER_ACCOUNT, instrument="NQU6@CME",
                   mode="replay", session_date=date(2026, 8, 7),
                   trades=[dict(side="long", size=1, entry_price=20000.0,
                                entry_ms=1786109460000, exit_price=20005.0,
                                exit_ms=1786110060000, pts=5.0, pnl=100.0,
                                reason="target")])
    _save(_attempt(), [_trade()])
    assert {t["account"] for t in _rows()} == {"paper", "replay"}
    assert bk.REPLAY_ACCOUNT != bk.PAPER_ACCOUNT


# --- the mirror never costs the attempt --------------------------------------


def test_a_broken_mirror_still_saves_the_sitting(conn, monkeypatch):
    """The attempt on disk is the thing worth keeping — the journal copy can be
    rebuilt from it, and losing a sitting to a mirror write would be the wrong
    trade."""
    a = _attempt()
    monkeypatch.setattr(bk, "book_attempt",
                        lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("nope")))
    body = _save(a, [_trade()])
    assert body["journaled"] is None
    assert replays.read(a["id"])["trades"]           # the sitting survived
    assert _rows() == []


def test_a_malformed_trade_is_dropped_not_raised(conn):
    """An autosave that 500s loses the sitting. A dropped row is visible in the
    count instead."""
    a = _attempt()
    assert _save(a, [_trade(), {"side": "sideways"}, _trade(id=3, size=0),
                     {"side": "long", "size": 1}])["journaled"] == 1
    assert len(_rows()) == 1


def test_a_watched_session_leaves_nothing_in_the_journal(conn):
    """An attempt only opens on the first fill, and one with no trades books
    the sitting without pretending anything happened in it."""
    a = _attempt()
    assert _save(a, [])["journaled"] == 0
    assert _rows() == []
    assert f"replay/{a['id']}" in db.sessions_map(conn)


# --- the sitting record ------------------------------------------------------


def test_a_note_written_on_the_attempt_reaches_the_session(conn):
    a = _attempt()
    _save(a, [_trade()])
    router.patch_replay(a["id"], router.PatchIn(note="chased the open"))
    (row,) = db.list_sessions(conn)
    assert row["note"] == "chased the open"


def test_an_attempt_is_one_sitting_not_one_day(conn):
    """Replaying the same session twice is two attempts on purpose — collapsing
    them here would merge a cold read with a re-run that knew the answer."""
    first, second = _attempt(), _attempt()
    assert second["repeat_index"] == 1
    assert bk.source_file_for_attempt(first["id"]) != \
        bk.source_file_for_attempt(second["id"])
    # Cannot collide with an ATAS export (bare filename, or under `backtest/`)
    # nor with the live account's sittings.
    assert bk.source_file_for_attempt(first["id"]).startswith("replay/")


# --- the backfill ------------------------------------------------------------


def test_the_backfill_picks_up_attempts_recorded_before_the_mirror(conn):
    """Written straight to the store, the way every attempt already on disk
    was — no router, so no mirror."""
    a = _attempt()
    replays.save(a["id"], log=LOG, trades=[_trade()], summary={})
    assert _rows() == []

    assert router.backfill_journal() == {"attempts": 1, "trades": 1, "failed": 0}
    (t,) = _rows()
    assert t["net_pnl"] == 400.0 and t["session_mode"] == "replay"


def test_running_the_backfill_twice_does_not_double_anything(conn):
    a = _attempt()
    replays.save(a["id"], log=LOG, trades=[_trade()], summary={})
    router.backfill_journal()
    router.backfill_journal()
    assert len(_rows()) == 1


# --- the sitting's place in the day ------------------------------------------
#
# A sitting has no export, so nothing was ever written to `imported_files` for
# it — and that table is what every "when was this day last worked on" reader
# consults. The consequence was not a blank cell but a wrong sort: a day you
# practised yesterday sank below every ATAS import in the calendar table,
# indistinguishable from a day nobody has touched since March.


def test_a_sitting_stamps_when_it_happened_so_the_calendar_can_sort_it(conn):
    a = _attempt()
    _save(a, [_trade()])
    (day,) = cal.calendar(scope=make_scope(include_archived=True))["days"]
    assert day["date"] == "2026-08-07"          # the tape's date, not today's
    assert day["file_modified"] is not None
    # The sitting's start, reprojected into the display zone — same instant.
    assert datetime.fromisoformat(day["file_modified"]) == \
        datetime.fromisoformat(a["created_at"])


def test_the_stamp_is_the_start_not_the_last_autosave(conn):
    """An autosave lands every few seconds while you are still trading. Stamping
    each one would walk the sitting up the Modified sort underneath you."""
    a = _attempt()
    src = bk.source_file_for_attempt(a["id"])
    _save(a, [_trade()])
    started = db.file_mtime_map(conn)[src]
    _save(a, [_trade(), _trade(id=2, entryMs=EXIT_MS + 60_000,
                               exitMs=EXIT_MS + 120_000, pnl=-100.0)])
    assert db.file_mtime_map(conn)[src] == started


def test_two_sittings_on_one_day_are_ordered_and_told_apart(conn):
    """Both parse as "Attempt 1" from their ids — there is no `-NN` in an
    attempt id to parse — so the day explorer offered two identical buttons."""
    first, second = _attempt(), _attempt()
    _save(first, [_trade()])
    _save(second, [_trade(id=9, pnl=-100.0, exitPrice=19995.0, pts=-5.0)])
    day = cal.day_detail("2026-08-07", None, make_scope(include_archived=True))
    assert [a["label"] for a in day["attempts"]] == ["Sitting 1", "Sitting 2"]
    assert [a["source_file"] for a in day["attempts"]] == [
        bk.source_file_for_attempt(first["id"]),
        bk.source_file_for_attempt(second["id"]),
    ]


def test_deleting_an_attempt_takes_its_stamp_with_it(conn):
    """Otherwise the day keeps a Modified time it has no trades to justify."""
    a = _attempt()
    _save(a, [_trade()])
    router.delete_replay(a["id"])
    assert bk.source_file_for_attempt(a["id"]) not in db.file_mtime_map(conn)


def test_the_backfill_stamps_attempts_that_predate_the_stamp(conn):
    """The migration path: every sitting already on disk was booked without one."""
    a = _attempt()
    replays.save(a["id"], log=LOG, trades=[_trade()], summary={})
    router.backfill_journal()
    assert db.file_mtime_map(conn)[bk.source_file_for_attempt(a["id"])] is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
