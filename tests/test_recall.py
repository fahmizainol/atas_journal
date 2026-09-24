"""The recall deck: SM-2, where the front stops, and the blindness of the front.

The properties worth pinning are the ones a careless change would quietly
destroy without breaking anything visible:

1. **the front carries no answer** — direction, price, PnL and the exit stamp
   are on ``/recall/back`` and nowhere else, because a masked field is still a
   readable field;
2. **the front's window is centred on the fill, in the tape's own clock** —
   ``cut_ms`` is derived from the trade rather than stored, so it cannot drift
   between reps; and read as wall-time-as-UTC, because a cut in the server's zone
   lands hours from the trade while every self-consistent assertion still passes.
   The ±30s around it is the client's (``RecallCard.WINDOW_PRE_MS`` /
   ``WINDOW_POST_MS``), checked in tools/browser/recallcheck.mjs;
3. **only reviewed trades are cards** — the deck is over reviews;
4. the schedule itself: lapses reset, intervals grow, ease floors at 1.3.

The entry stamp is *not* on the leak list: the tape stops there, so the fill
instant is the right edge and withholding the number would hide nothing.

Run directly:  ``PYTHONPATH=src .venv/bin/python -m pytest tests/test_recall.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import HTTPException  # noqa: E402

from journal import db, sm20, srs  # noqa: E402
from journal.level_tag import LevelRank  # noqa: E402
from api import deps  # noqa: E402
from api.routers import notes, recall  # noqa: E402

KEY = "abc123"
#: The sitting a trade belongs to, which is what the session note hangs off.
SOURCE = "live/2026-02-03.xlsx"
DAY = "2026-02-03"
ENTRY_LOCAL = f"{DAY} 10:42:00-05:00"
ENTRY_UTC = f"{DAY}T15:42:00Z"


@pytest.fixture(autouse=True)
def _keep_replays_dir():
    """``_sitting`` points the attempt store at a scratch directory; every test
    after it would otherwise read from one that has been deleted."""
    from journal import replays
    original = replays.REPLAYS_DIR
    yield
    replays.REPLAYS_DIR = original


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    deps._conn = conn
    return conn


def _scope(**over) -> SimpleNamespace:
    row = {
        "logical_trade_key": KEY, "trade_no": 7, "direction": "Long",
        "max_contracts": 2, "avg_entry": 21500.25, "avg_exit": 21540.75,
        "net_pnl": 810.0, "entry_ts_local": ENTRY_LOCAL,
        "exit_ts_local": f"{DAY} 10:58:00-05:00", "entry_ts_utc": ENTRY_UTC,
        "instrument": "NQU6",
    }
    row.update(over)
    return SimpleNamespace(filtered_all=pd.DataFrame([row]))


def _review(conn, grade="B", family="devVP_val", tags=("held the level",)):
    notes.put_note(KEY, notes.NoteIn(
        grade=grade, watched_levels=[family], tags=list(tags), note="wicked out",
        setup="faded_rally", discipline="clean"))


def _context(conn, symbol="NQH6"):
    """The tape contract, which is the only place the deck may read it from."""
    conn.execute(
        "INSERT INTO trade_context (trade_key, symbol, method) VALUES (?, ?, ?)",
        (KEY, symbol, "v2"),
    )
    conn.commit()


# --- the deck ---------------------------------------------------------------

def test_a_reviewed_trade_becomes_a_card():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        out = recall.deck(_scope())
        assert [c["trade_key"] for c in out["cards"]] == [KEY]
        assert out["total"] == 1


def test_an_unreviewed_trade_is_not_a_card():
    """The deck is over reviews. A card whose back has nothing to say is a chart
    with a price on it."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _context(conn)
        assert recall.deck(_scope())["cards"] == []
        # Half a review is still not a review.
        notes.put_note(KEY, notes.NoteIn(grade="B"))
        assert recall.deck(_scope())["cards"] == []


def test_a_trade_with_no_measured_contract_is_not_a_card():
    """``instrument`` is not a fallback: ATAS stamps every 2026 row with one
    label across the roll, so trusting it would silently draw the wrong day."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        assert recall.deck(_scope())["cards"] == []


def test_the_card_uses_the_measured_contract_not_the_label():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn, symbol="NQH6")
        card = recall.deck(_scope(instrument="NQU6"))["cards"][0]
        assert card["symbol"] == "NQH6"
        assert card["root"] == "NQ"


def test_the_front_carries_no_answer():
    """The blindness mechanism. Masking these in the UI would leave them one
    devtools tab away, and the reader most able to cheat is the deck's own user.

    ``entry_ts_local`` is not on the list: the front's window is pinned to the
    fill, so that instant is a fixed offset from the right edge and ``cut_ms``
    *is* it. Asserting the name is absent while the value is the card's whole
    geometry would be theatre.
    """
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        card = recall.deck(_scope())["cards"][0]
        for leak in ("direction", "avg_entry", "avg_exit", "net_pnl",
                     "exit_ts_local", "max_contracts",
                     "grade", "tags", "note", "watched_levels"):
            assert leak not in card, f"the front leaked {leak}"


def test_a_card_stops_being_due_once_rated():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        out = recall.deck(_scope())
        assert out["cards"] == []
        assert out["total"] == 1
        assert out["next_due"] is not None


# --- where the front stops --------------------------------------------------

def test_the_front_stops_on_the_fill():
    """The whole geometry of the card, in one number.

    Until 2026-08-22 this was a jittered instant near the entry and this test
    asserted the opposite — that the stop was *never* the fill, because a stop
    that always landed there tells you within a few reps that the last bar is the
    trade. That blindness was given up on purpose: a stop minutes off the fill
    asks about a chart the setup has not formed on. What the card still refuses
    to say is which way, how big, and what it did.
    """
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        card = recall.deck(_scope())["cards"][0]
        assert card["cut_ms"] == recall._local_ms(ENTRY_LOCAL)


def test_the_stop_is_measured_in_the_tape_s_clock():
    """The tape does not get a vote: its clock is wall time read as UTC, exactly
    what the browser's ``localMs`` produces from the same stamp. Pinned as an
    absolute epoch, and under a deliberately non-UTC ``TZ``, because reading the
    stamp in the server's own zone is the mistake this guards — it put every
    card's stop hours from its trade on a UTC+8 machine, in tape the entry was
    nowhere near, while every self-consistent test still passed.
    """
    # 10:42 wall on 2026-02-03, offset dropped: Date.parse("2026-02-03T10:42:00Z").
    assert recall._local_ms(ENTRY_LOCAL) == 1770115320000


def test_the_stop_s_clock_ignores_the_server_zone(monkeypatch):
    """The same number on a machine that is not at UTC — which is where this
    broke, and which CI is not."""
    import time

    monkeypatch.setenv("TZ", "Asia/Singapore")
    time.tzset()
    try:
        assert recall._local_ms(ENTRY_LOCAL) == 1770115320000
    finally:
        monkeypatch.undo()
        time.tzset()


def test_the_stop_is_stable_across_reads_and_schedules():
    """It is derived from the trade, so there is nothing to drift — and nothing
    to pin. A card mid-schedule is asked at the same instant a fresh one is."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        first = recall.deck(_scope())["cards"][0]["cut_ms"]
        assert recall.deck(_scope())["cards"][0]["cut_ms"] == first

        learned = srs.new_card(date.today())
        learned.update(reps=3, lapses=1, interval_d=6.0)
        db.save_recall_card(conn, KEY, learned)
        assert recall.deck(_scope())["cards"][0]["cut_ms"] == first


def test_a_card_row_carries_no_stop():
    """The schedule table holds a schedule. Where the tape stops comes from the
    journal row, so a deleted card comes back as the same question."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        assert "cut_ms" not in db.get_recall_card(conn, KEY)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recall_cards)")}
        assert "cut_ms" not in cols


# --- the schedule -----------------------------------------------------------

def test_a_new_card_graduates_one_then_six():
    today = date(2026, 2, 3)
    c = srs.new_card(today)
    c = srs.schedule(c, srs.GOOD, today)
    assert c["interval_d"] == 1.0 and c["reps"] == 1
    c = srs.schedule(c, srs.GOOD, today)
    assert c["interval_d"] == 6.0 and c["reps"] == 2
    c = srs.schedule(c, srs.GOOD, today)
    assert c["interval_d"] == pytest.approx(6.0 * srs.EASE_START)


def test_again_resets_the_reps_and_counts_a_lapse():
    """Not a halved interval: a card you have demonstrably stopped knowing should
    come back through the same two steps a new one does."""
    today = date(2026, 2, 3)
    c = srs.new_card(today)
    for _ in range(3):
        c = srs.schedule(c, srs.GOOD, today)
    assert c["interval_d"] > 6.0
    c = srs.schedule(c, srs.AGAIN, today)
    assert c["reps"] == 0 and c["lapses"] == 1
    assert c["interval_d"] == srs.FIRST_INTERVAL_D
    assert c["due"] == "2026-02-04"


def test_ease_floors_and_easy_lifts():
    today = date(2026, 2, 3)
    c = srs.new_card(today)
    for _ in range(20):
        c = srs.schedule(c, srs.AGAIN, today)
    assert c["ease"] == srs.EASE_MIN
    lifted = srs.schedule(c, srs.EASY, today)
    assert lifted["ease"] > srs.EASE_MIN


def test_the_interval_is_capped():
    today = date(2026, 2, 3)
    c = {"due": today.isoformat(), "interval_d": 400.0,
         "ease": 2.5, "reps": 9, "lapses": 0}
    assert srs.schedule(c, srs.EASY, today)["interval_d"] == srs.MAX_INTERVAL_D


def test_an_unknown_rating_is_refused():
    with pytest.raises(ValueError):
        srs.schedule(srs.new_card(date(2026, 2, 3)), 9, date(2026, 2, 3))


# --- rating -----------------------------------------------------------------

def test_rating_appends_a_rep_and_keeps_the_guess():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(
            trade_key=KEY, rating=srs.HARD, guess="fades back to VWAP"), _scope())
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        reps = db.recall_reps_for(conn, KEY)
        assert [r["rating"] for r in reps] == [srs.HARD, srs.GOOD]
        assert reps[0]["guess"] == "fades back to VWAP"
        assert reps[1]["guess"] is None


def test_a_blank_guess_stores_as_absent():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD, guess="   "),
                    _scope())
        assert db.recall_reps_for(conn, KEY)[0]["guess"] is None


def test_rating_an_unknown_value_is_422():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        with pytest.raises(HTTPException) as e:
            recall.rate(recall.RateIn(trade_key=KEY, rating=0), _scope())
        assert e.value.status_code == 422


def test_rating_a_trade_off_the_journal_is_404():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            recall.rate(recall.RateIn(trade_key="nope", rating=srs.GOOD), _scope())
        assert e.value.status_code == 404


# --- the back ---------------------------------------------------------------

def test_the_back_answers_with_the_trade_and_the_review():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade="D", family="gxVP_val", tags=("oversized",))
        _context(conn)
        out = recall.back(KEY, _scope())
        assert out["direction"] == "Long" and out["net_pnl"] == 810.0
        assert out["grade"] == "D"
        assert out["watched_labels"] == ["GX VAL"]
        assert out["tags"] == ["oversized"]
        assert out["note"] == "wicked out"


def test_the_back_carries_the_sitting_it_was_taken_in():
    """The review's session box, on the answer side.

    It is written with the day finished — the same hindsight class as the grade
    — so the back is the only face it may appear on; the companion test below
    pins that the front does not learn it.
    """
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        db.upsert_session(conn, SOURCE, mode="replay")
        db.update_session(conn, SOURCE, note="chop until 10:30, then it trended")
        out = recall.back(KEY, _scope(source_file=SOURCE))
        assert out["session_note"] == "chop until 10:30, then it trended"
        # And the trade's own note is untouched by it: two fields, two scopes.
        assert out["note"] == "wicked out"


def test_a_sitting_that_said_nothing_reads_as_nothing():
    """No session row at all is the same answer as an empty note — an export
    ingested before the sessions table has neither, and neither is an error."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        assert recall.back(KEY, _scope(source_file=SOURCE))["session_note"] == ""


def test_the_front_never_carries_the_sitting_note():
    """The leak guard. A note about the day is a description of the tape the
    card is asking you to read, so a single word of it on the front would answer
    the card before it was flipped."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        db.upsert_session(conn, SOURCE, mode="replay")
        db.update_session(conn, SOURCE, note="trended all day off the globex VAL")
        card = recall.deck(_scope(source_file=SOURCE))["cards"][0]
        assert "session_note" not in card
        assert not any("globex" in str(v).lower() for v in card.values())


def test_the_back_labels_a_trade_taken_off_no_level():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, family="none")
        _context(conn)
        assert recall.back(KEY, _scope())["watched_labels"] == ["no level"]


def test_the_back_404s_off_the_journal():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            recall.back("nope", _scope())
        assert e.value.status_code == 404


# --- correcting the review from the card -------------------------------------
#
# The card is the tape the review was written against, so it is where a wrong
# grade or a misnamed level is actually noticed. The back carries what that edit
# needs; the write itself is ``PUT /notes`` like every other review surface.

def test_the_back_offers_the_levels_the_picker_needs():
    """Nearest first — the same ordering rule the replay review reads, because
    it is now the same function (``level_store.candidates_for``)."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        db.set_trade_levels(conn, [
            LevelRank(KEY, "entry", "value_high", "wkVP_vah", 0.01, -40.0),
            LevelRank(KEY, "entry", "session_mean", "vwap", 0.60, 3.0),
        ], "t")
        out = recall.back(KEY, _scope())
        assert [c["id"] for c in out["levels"]] == ["vwap", "wkVP_vah"]


def test_the_back_echoes_what_a_save_would_otherwise_blank():
    """``PUT /notes`` overwrites the whole row. The card shows none of these and
    carries all of them, so a grade corrected here cannot take the trade's model,
    its rule checks or its archived badges with it."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note(KEY, notes.NoteIn(
            grade="B", watched_levels=["devVP_val"], tags=["held the level"],
            setups=["fade"], confluences=["poc"]))
        _context(conn)
        out = recall.back(KEY, _scope())
        assert out["setups"] == ["fade"] and out["confluences"] == ["poc"]
        assert out["model_id"] is None and out["rules_met"] == []


def test_the_front_never_carries_the_levels():
    """The leak guard for the picker's own options. A list of what sat around
    the fill is a description of the chart the card is asking you to read."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        db.set_trade_levels(conn, [
            LevelRank(KEY, "entry", "value_low", "gxVP_val", 0.2, 2.0),
        ], "t")
        card = recall.deck(_scope())["cards"][0]
        assert "levels" not in card
        assert not any("gxVP" in str(v) for v in card.values())


# --- the order behind the fill ----------------------------------------------
#
# The mirror keeps the trade and drops the order that opened it, so the back
# reads the sitting on disk to say how the position was entered and what it was
# bracketed with. What is worth pinning is that the bracket is the one struck at
# the *fill* — a market ticket says ticks, and freezing the levels the mark
# implied at the gesture is how a "risk 40 ticks" order comes back as 44 — and
# that a trade which was never a sitting says so rather than inventing one.

def _sitting(tmp: Path, *, entry=21500.25, exit_px=21540.75, order=None,
             trade=None) -> str:
    """One attempt on disk, holding the trade ``_scope()`` describes."""
    from journal import replays

    replays.REPLAYS_DIR = tmp / "replays"
    attempt = replays.create(
        symbol="NQH6", root="NQ", date=DAY, tz="New York", engine_version=1,
        tape={"n": 10}, prefs={}, started_ms=0)
    ms = 1_770_115_320_000
    o = {"id": 1, "type": "market", "side": "long", "size": 2, "ms": ms - 250,
         "idx": 0, "price": None, "stop": 21480.0, "target": 21560.0,
         "trail": None, "edits": [], "cancelMs": None}
    o.update(order or {})
    t = {"id": 1, "side": "long", "size": 2, "entryPrice": entry, "entryMs": ms,
         "openType": o["type"], "exitMs": ms + 960_000, "exitPrice": exit_px,
         "reason": "target", "pts": exit_px - entry, "pnl": 810.0, "fees": 14}
    t.update(trade or {})
    replays.save(attempt["id"], log={"orders": [o], "closes": [], "brackets": []},
                 trades=[t], summary={})
    return attempt["id"]


def test_the_back_carries_the_order_behind_the_fill():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        aid = _sitting(Path(d))
        out = recall.back(KEY, _scope(source_file=f"replay/{aid}",
                                      duration_s=960.0))
        assert out["order"] == {
            "open_type": "market", "exit_reason": "target",
            "rest_price": None, "rest_ms": None,
            "stop": 21480.0, "target": 21560.0,
            "trail_pts": None, "trail_be_only": False, "moved": False,
        }


def test_a_market_bracket_is_struck_from_the_fill_not_from_the_gesture():
    """The ticket promised distances; the fill landed a tick later and paid the
    spread. Freezing the prices the mark implied is what made a ⊥50/⊤50 ticket
    open a 227-tick stop on 2025-01-30 — ``open_position`` is the rule, and this
    is the reader going through it rather than reading the record's own levels."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        aid = _sitting(Path(d), order={
            # Struck from a mark two ticks below where it actually filled.
            "stop": 21470.0, "target": 21550.0,
            "stopTicks": 80, "targetTicks": 160})
        order = recall.back(KEY, _scope(source_file=f"replay/{aid}"))["order"]
        assert order["stop"] == 21500.25 - 80 * 0.25
        assert order["target"] == 21500.25 + 160 * 0.25


def test_a_resting_entry_says_where_it_waited_and_for_how_long():
    """``rest_ms`` is a duration, never a stamp: the log's clock is the tape's
    display-zone epoch and the journal's is Eastern wall time, so a stamp here
    would need the attempt's zone to be readable. A duration needs nothing."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        ms = 1_770_115_320_000
        aid = _sitting(Path(d), order={
            "type": "limit", "price": 21495.0, "ms": ms - 90_000,
            # Dragged up to where it eventually filled, 30s before it did.
            "edits": [{"ms": ms - 30_000, "price": 21500.25,
                       "stop": 21480.0, "target": 21560.0}]})
        order = recall.back(KEY, _scope(source_file=f"replay/{aid}"))["order"]
        assert order["open_type"] == "limit"
        assert order["rest_price"] == 21500.25
        assert order["rest_ms"] == 30_000


def test_a_bracket_moved_while_the_trade_was_open_says_so():
    """The reported levels are the ones the position opened with — the risk that
    was accepted. A drag moves them afterwards, so an exit can land where those
    levels do not explain, and this flag is what stops the two numbers reading as
    a contradiction."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        from journal import replays
        aid = _sitting(Path(d))
        rec = replays.read(aid)
        rec["log"]["brackets"] = [{"ms": 1_770_115_320_000 + 5_000,
                                   "stop": 21495.0, "target": 21530.0}]
        replays.save(aid, log=rec["log"], trades=rec["trades"], summary={})
        order = recall.back(KEY, _scope(source_file=f"replay/{aid}"))["order"]
        assert order["moved"] is True
        assert order["stop"] == 21480.0     # still the opening bracket


def test_a_trade_that_was_never_a_sitting_has_no_order():
    """Most of the journal is an imported broker export. There is no order log
    behind those rows, and a bracket guessed off the wrong fill would be worse
    than none."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        assert recall.back(KEY, _scope(source_file=SOURCE))["order"] is None


def test_a_sitting_that_holds_no_matching_trade_has_no_order():
    """Fails closed: the attempt is there, but nothing in it is this fill."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        aid = _sitting(Path(d), trade={"entryPrice": 20000.0})
        assert recall.back(KEY, _scope(source_file=f"replay/{aid}"))["order"] is None


def test_the_front_never_carries_the_order():
    """The leak guard. An order type says whether the entry was waited for and a
    stop says how much room the trade was given — both are the read the card is
    asking you to make, so neither may reach the front."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        aid = _sitting(Path(d))
        card = recall.deck(_scope(source_file=f"replay/{aid}"))["cards"][0]
        assert "order" not in card
        assert not any("21480" in str(v) for v in card.values())


# --- the Algorithm Arena ----------------------------------------------------
# What is worth pinning here is the boundary, not the scheduler: the numbers are
# upstream's and ``cargo test`` already replays them. These tests ask whether our
# JSON crossing preserves them, whether the deck still works when the binary was
# never built, and whether the deck-wide state survives a round trip through
# SQLite — the three ways this integration can fail silently.

FIXTURE = ROOT / "vendor" / "sm20" / "sm20ArenaParityFixture.json"
needs_binary = pytest.mark.skipif(
    not sm20.available(), reason=f"not built — {sm20.BUILD_HINT}")


@needs_binary
def test_the_shim_reproduces_the_parity_fixture():
    """Every grade, through JSON, against the language-neutral golden.

    The fixture is upstream's and is shared with their TypeScript; matching it
    from Python is what proves the subprocess boundary neither rounds nor
    reorders anything. ``today`` must be the fixture's ``elapsed_days``, because
    its item state was captured with ``m1_state.last_review_day == 0`` and M1
    derives its own interval from that rather than from what we pass.
    """
    fx = json.loads(FIXTURE.read_text())
    for expected in fx["grades"]:
        out = sm20.review(
            grade=expected["grade"], elapsed_days=fx["elapsed_days"],
            today=fx["elapsed_days"], state=fx["state"],
            seed=0, disperse=False,   # a golden has to be reproducible
        )
        assert [round(x) for x in out["model_intervals"]] == expected["candidates"]
        assert round(out["interval_days"]) == expected["recommendation"]


@needs_binary
def test_the_committed_interval_is_a_blend_not_sm20s_answer():
    """The headline caveat, pinned: at default weights SM-20 proper is 25% of it.

    If this ever starts equalling ``model_intervals[3]`` then the Arena has been
    bypassed and the deck is running something other than what it documents.
    """
    fx = json.loads(FIXTURE.read_text())
    out = sm20.review(grade=4, elapsed_days=30, today=30, state=fx["state"],
                      seed=0, disperse=False)
    blend = sum(w / 100 * c
                for w, c in zip(fx["weights"], out["model_intervals"]))
    # 49.51 blended, 49 committed: the Arena truncates to whole days. Asserted as
    # a bound rather than an equality because the rounding rule is upstream's to
    # change and the claim worth pinning is that the number IS the blend.
    assert blend == pytest.approx(49.51, abs=0.01)
    assert out["interval_days"] == 49.0
    assert round(out["model_intervals"][3]) == 53   # SM-20 alone would say 53


def test_an_unknown_rating_has_no_grade():
    assert sm20.rating_to_grade(srs.AGAIN) == 0
    assert sm20.rating_to_grade(srs.EASY) == 5
    with pytest.raises(ValueError):
        sm20.rating_to_grade(9)


def test_the_deck_falls_back_to_sm2_when_the_binary_is_absent(monkeypatch):
    """A fresh clone has no Rust toolchain, and must still have a working deck."""
    monkeypatch.setattr(sm20, "BINARY", Path("/nonexistent/sm20-schedule"))
    today = date(2026, 2, 3)
    card = srs.new_card(today)
    nxt, collection = srs.advance(card, srs.GOOD, today)
    assert nxt == srs.schedule(card, srs.GOOD, today)   # SM-2's answer, unchanged
    assert collection is None                            # nothing to store
    assert "sm20_state" not in nxt


@needs_binary
def test_the_arena_schedules_and_hands_back_both_states():
    today = date(2026, 2, 3)
    nxt, collection = srs.advance(srs.new_card(today), srs.GOOD, today)
    assert nxt["sm20_state"] is not None
    assert nxt["last_review_day"] == sm20.epoch_day(today)
    assert collection is not None and "arena" in collection
    # The counters stay SM-2's: they are the card's history, not the
    # scheduler's, and they are what the page renders.
    assert nxt["reps"] == 1 and nxt["lapses"] == 0


@needs_binary
def test_a_rated_card_never_comes_back_the_same_day():
    """The Arena may answer in fractions of a day; this deck is a calendar."""
    today = date(2026, 2, 3)
    nxt, _ = srs.advance(srs.new_card(today), srs.AGAIN, today)
    assert nxt["due"] > today.isoformat()


@needs_binary
def test_the_collection_learns_across_ratings():
    """The whole reason the deck carries 270 KB: the weights have to move."""
    today = date(2026, 2, 3)
    card, collection = srs.new_card(today), None
    first = None
    for i in range(6):
        card, collection = srs.advance(card, srs.GOOD, today + timedelta(days=i),
                                       collection)
        if first is None:
            first = list(collection["arena"]["weights"])
    assert collection["arena"]["weights"] != first
    assert round(sum(collection["arena"]["weights"])) == 100   # still a blend


def test_a_legacy_deck_migrates_and_the_migration_repeats_safely():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        # The oldest shape on disk: SM-2 only, and a stored jittered cut.
        conn.executescript("""
            DROP TABLE recall_cards;
            CREATE TABLE recall_cards (
                trade_key TEXT PRIMARY KEY, cut_ms INTEGER NOT NULL,
                due TEXT NOT NULL, interval_d REAL NOT NULL, ease REAL NOT NULL,
                reps INTEGER NOT NULL, lapses INTEGER NOT NULL, updated_at TEXT);
            INSERT INTO recall_cards VALUES ('old', 12, '2026-01-01', 6.0, 2.5, 3, 0, NULL);
        """)
        db._migrate_recall_sm20(conn)
        db._migrate_recall_sm20(conn)
        db._migrate_recall_drop_cut(conn)
        db._migrate_recall_drop_cut(conn)
        card = db.get_recall_card(conn, "old")
        # An SM-2 history is not translated — the two schedulers share no state
        # space — but the card keeps its due date and its counters.
        assert card["sm20_state"] is None and card["last_review_day"] is None
        assert card["due"] == "2026-01-01" and card["reps"] == 3
        # And the stop it used to carry is gone, not merely ignored.
        assert "cut_ms" not in {
            r[1] for r in conn.execute("PRAGMA table_info(recall_cards)")}


def test_a_fallback_rating_does_not_blank_a_stored_arena_state():
    """Otherwise a single rating taken on a machine without the binary would
    restart a card the Arena had been learning for months."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.save_recall_card(conn, "k", {
            "due": "2026-03-01", "interval_d": 30.0, "ease": 2.5,
            "reps": 4, "lapses": 0,
            "sm20_state": {"stability": 9.5}, "last_review_day": 20500})
        db.save_recall_card(conn, "k", {   # an SM-2 answer: no state to offer
            "due": "2026-04-01", "interval_d": 40.0, "ease": 2.5,
            "reps": 5, "lapses": 0})
        card = db.get_recall_card(conn, "k")
        assert card["sm20_state"] == {"stability": 9.5}
        assert card["last_review_day"] == 20500
        assert card["due"] == "2026-04-01"  # while the schedule did advance


def test_the_deck_query_does_not_carry_scheduler_state():
    """``deck()`` asks every card whether it is due; pulling each one's model
    state to answer a date comparison would put the deck's whole working set in
    memory to show one card."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.save_recall_card(conn, "k", {
            "due": "2026-03-01", "interval_d": 30.0, "ease": 2.5,
            "reps": 4, "lapses": 0, "sm20_state": {"stability": 9.5},
            "last_review_day": 20500})
        assert "sm20_state" not in db.all_recall_cards(conn)["k"]


@needs_binary
def test_rating_persists_both_states_and_keeps_them_off_the_wire():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        out = recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        assert "sm20_state" not in out["card"]
        assert db.get_recall_card(conn, KEY)["sm20_state"] is not None
        before = db.get_recall_collection(conn)
        assert before is not None
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.AGAIN), _scope())
        assert db.get_recall_collection(conn) != before   # and it kept learning
        assert conn.execute(
            "SELECT count(*) FROM recall_collection").fetchone()[0] == 1


def test_the_back_names_the_level_without_the_measurement():
    """The answer *is* the level, so naming it needs no measured row at all.

    Worth pinning because the measurement is the volatile half: a `METHOD` bump
    rebuilds `trade_levels` from scratch, and for as long as the backfill has not
    run there are no rows. A review answered before that must still read back as
    the level it named, not degrade to a family or to nothing.
    """
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, family="gxVP_val")
        _context(conn)
        assert db.get_trade_levels(conn, KEY) == []
        assert recall.back(KEY, _scope())["watched_labels"] == ["GX VAL"]


# --- undo -------------------------------------------------------------------
# A rating writes three places — the rep log, the card's schedule, and the
# Arena's deck-wide state — and two of those cannot be recomputed afterwards
# (``recall_collection`` is path dependent; ``save_recall_card`` COALESCEs
# ``sm20_state`` so the old value is gone the moment a new one lands). So the
# property under test is not "the card looks unrated again" but **every one of
# the three is byte-for-byte what the rating found**, which is the only version
# of this feature that does not quietly corrupt the scheduler.

def test_undoing_a_first_rating_leaves_the_card_unseen():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        assert db.get_recall_card(conn, KEY) is not None

        out = recall.undo()
        assert out["trade_key"] == KEY and out["rating"] == srs.GOOD
        # No row at all, rather than a row reset to defaults: the card had none.
        assert db.get_recall_card(conn, KEY) is None
        assert db.recall_reps_for(conn, KEY) == []
        assert recall.deck(_scope())["cards"][0]["reps"] == 0


def test_undo_restores_the_schedule_it_found_not_a_default():
    """The undone card had a history; it must come back with that history."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        before = db.get_recall_card(conn, KEY)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.AGAIN), _scope())
        assert db.get_recall_card(conn, KEY) != before

        recall.undo()
        assert db.get_recall_card(conn, KEY) == before
        assert [r["rating"] for r in db.recall_reps_for(conn, KEY)] == [srs.GOOD]


def test_undo_deletes_the_showing_it_undid_and_no_other():
    """Two cards rated in turn: the undo takes the second's rep, not the first's."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.HARD, guess="first"),
                    _scope())
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.EASY, guess="second"),
                    _scope())
        assert recall.undo()["guess"] == "second"
        reps = db.recall_reps_for(conn, KEY)
        assert [r["guess"] for r in reps] == ["first"]


def test_only_the_last_rating_can_be_undone():
    """A second undo has nothing to take back, and says so rather than
    reverting the rating before it — restoring an older snapshot would roll the
    Arena's collection past everything it has learned since."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.HARD), _scope())
        recall.undo()
        with pytest.raises(HTTPException) as e:
            recall.undo()
        assert e.value.status_code == 409
        assert [r["rating"] for r in db.recall_reps_for(conn, KEY)] == [srs.GOOD]


def test_undo_on_an_untouched_deck_is_409():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            recall.undo()
        assert e.value.status_code == 409


def test_the_deck_offers_the_undo_without_the_guess():
    """The slot rides on the front's payload, so it may carry the card and the
    reader's own rating — and no free text about the trade."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        assert recall.deck(_scope())["undo"] is None
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.HARD,
                                  guess="fades back to VWAP"), _scope())
        slot = recall.deck(_scope())["undo"]
        assert slot["trade_key"] == KEY and slot["rating"] == srs.HARD
        assert "guess" not in slot
        recall.undo()
        assert recall.deck(_scope())["undo"] is None


def test_undo_puts_back_a_null_sm20_state():
    """The one restore ``save_recall_card`` cannot do: it COALESCEs, so a card
    whose first rating gave it Arena state would keep that state through an
    undo and resume months of learning that never happened."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.save_recall_card(conn, KEY, {
            "due": "2026-03-01", "interval_d": 30.0, "ease": 2.5,
            "reps": 4, "lapses": 0})
        assert db.get_recall_card(conn, KEY)["sm20_state"] is None
        rep_id = db.add_recall_rep(conn, KEY, srs.GOOD, None)
        db.stage_recall_undo(conn, KEY, rep_id)
        db.save_recall_card(conn, KEY, {
            "due": "2026-04-01", "interval_d": 40.0, "ease": 2.5,
            "reps": 5, "lapses": 0,
            "sm20_state": {"stability": 9.5}, "last_review_day": 20500})

        db.undo_last_recall_rating(conn)
        card = db.get_recall_card(conn, KEY)
        assert card["sm20_state"] is None and card["last_review_day"] is None
        assert card["due"] == "2026-03-01" and card["reps"] == 4


@needs_binary
def test_undo_rewinds_the_arena_to_the_state_the_rating_found():
    """The collection is the half that cannot be rebuilt from the rep log."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        before = db.get_recall_collection(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.AGAIN), _scope())
        assert db.get_recall_collection(conn) != before

        recall.undo()
        assert db.get_recall_collection(conn) == before


@needs_binary
def test_undoing_the_decks_first_ever_rating_leaves_no_collection():
    """Restoring "there was none" means deleting the row, not writing defaults
    over it — otherwise the deck's first rating is unrepeatable."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn)
        _context(conn)
        assert db.get_recall_collection(conn) is None
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        assert db.get_recall_collection(conn) is not None
        recall.undo()
        assert db.get_recall_collection(conn) is None
        assert conn.execute(
            "SELECT count(*) FROM recall_collection").fetchone()[0] == 0


# --- the grade, answered at the front ----------------------------------------
#
# Since 2026-08-31 the grade is captured here — picked at the fill-freeze,
# locked by the flip, delivered with the rating — because this is the one
# surface that can ask it without the outcome on screen. The 132 reviews graded
# in the panel restated the P&L sign (every A/B won, every D lost), which is
# what a grade with the outcome on screen must do.

def test_the_front_says_when_a_grade_is_owed():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade=None)
        _context(conn)
        assert recall.deck(_scope())["cards"][0]["needs_grade"] is True
        notes.put_note(KEY, notes.NoteIn(grade="C"))
        assert recall.deck(_scope())["cards"][0]["needs_grade"] is False


def test_the_rating_carries_the_blind_grade():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade=None)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD, grade="B"),
                    _scope())
        assert db.get_note(conn, KEY)["grade"] == "B"


def test_a_second_grade_is_refused():
    """First answer wins: the first was given blind, and any later one is given
    by someone who has seen the back. A 422 rather than a silent drop — grade
    arriving on a graded trade means the client showed a picker it shouldn't."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade="A")
        _context(conn)
        with pytest.raises(HTTPException) as e:
            recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD, grade="C"),
                        _scope())
        assert e.value.status_code == 422
        assert db.get_note(conn, KEY)["grade"] == "A"


def test_a_grade_that_is_not_a_grade_is_refused():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade=None)
        _context(conn)
        with pytest.raises(HTTPException) as e:
            recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD, grade="E"),
                        _scope())
        assert e.value.status_code == 422
        assert db.get_note(conn, KEY)["grade"] is None


def test_undo_takes_the_grade_back():
    """The misclick path the user asked for: undoing the rating undoes the
    grade it wrote, and the card comes back asking again."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade=None)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD, grade="D"),
                    _scope())
        assert db.get_note(conn, KEY)["grade"] == "D"
        # The rate path is the blind path, and says so on the row.
        assert conn.execute(
            "SELECT graded_at FROM trade_notes WHERE trade_key = ?", (KEY,)
        ).fetchone()[0] is not None
        recall.undo()
        assert db.get_note(conn, KEY)["grade"] is None
        # The blind stamp leaves with the grade it described.
        assert conn.execute(
            "SELECT graded_at FROM trade_notes WHERE trade_key = ?", (KEY,)
        ).fetchone()[0] is None
        assert recall.deck(_scope())["cards"][0]["needs_grade"] is True


def test_undo_without_a_grade_leaves_the_grade_alone():
    """The flag, not a value comparison, guards the restore — a rating that
    wrote no grade must not clobber one written elsewhere in between."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        _review(conn, grade=None)
        _context(conn)
        recall.rate(recall.RateIn(trade_key=KEY, rating=srs.GOOD), _scope())
        notes.put_note(KEY, notes.NoteIn(grade="B"))  # a hand repair meanwhile
        recall.undo()
        assert db.get_note(conn, KEY)["grade"] == "B"
