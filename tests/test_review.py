"""The graded review: what a trade owes, how it is stored, and what it refuses.

Four things worth pinning, all of them load-bearing decisions from
docs/trade-grading-plan.md rather than incidental behaviour:

1. the gate is watched level ∧ setup ∧ discipline — the grade left it for the
   recall front (2026-08-31) and the free tags left with it — and the note is
   *not* in it;
2. ``grade`` / ``watched_levels`` are **partial** on ``PUT /notes`` — a caller
   that has never heard of them cannot blank them by saving a note, which is the
   whole reason they are not part of ``save_note``'s whole-row overwrite;
3. empty and ``['none']`` stay distinguishable, because a trade with no cached
   tape must not read as an unreviewed one;
4. level candidates come back **by distance, not by rank** — the tagger offers a
   shortlist and does not answer with it.

Calls the router functions directly with a temp DB injected into deps._conn
(same pattern as test_taxonomy_api.py).

Run directly:  ``PYTHONPATH=src .venv/bin/python -m pytest tests/test_review.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import HTTPException  # noqa: E402

from journal import db, level_store, review  # noqa: E402
from journal.level_tag import LevelRank  # noqa: E402
from api import deps  # noqa: E402
from api.routers import notes, replays  # noqa: E402


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    deps._conn = conn
    return conn


def _answered(**over) -> dict:
    row = {"trade_key": "k1", "watched_levels": ["devVP_val"],
           "setup": "faded_rally", "discipline": "clean",
           "tags": [], "note": ""}
    row.update(over)
    return row


# --- what a trade owes ------------------------------------------------------

def test_all_three_are_required():
    assert review.trade_answered(_answered())
    assert not review.trade_answered(_answered(watched_levels=[]))
    assert not review.trade_answered(_answered(setup=None))
    assert not review.trade_answered(_answered(discipline=None))


def test_the_grade_and_the_tags_are_not_in_the_gate():
    """The grade is answered at the recall front, blind — a panel gate on it
    would deadlock the deck that asks it. And "≥1 tag" of an open vocabulary
    was answered seven times with the literal tag ``None``: a gate satisfied
    is not a question answered, so the tags are optional colour now."""
    assert review.trade_answered(_answered(grade=None, tags=[]))


def test_an_axis_answer_must_be_in_the_vocabulary():
    """A misspelled setup is an answer no cut can read, and the gate must not
    count it — the door (PUT /notes) refuses these, but a hand-edited row has
    to fail the gate too rather than pass as reviewed."""
    assert not review.trade_answered(_answered(setup="faded the rally, kinda"))
    assert not review.trade_answered(_answered(discipline="mostly clean"))


def test_the_note_is_not_part_of_the_gate():
    """The one field with nothing to say on an ordinary trade. Requiring prose on
    all of them is how a review becomes something you fill in."""
    assert review.trade_answered(_answered(note=""))


def test_whitespace_is_not_an_answer():
    assert not review.trade_answered(_answered(setup="   "))
    assert not review.trade_answered(_answered(watched_levels=["  "]))


def test_no_level_is_a_real_answer():
    """``'none'`` has to pass the gate. If it did not, a trade whose tape was
    never cached would be permanently unreviewable — the gate would be blocking
    on a missing *measurement* rather than a missing answer."""
    assert review.trade_answered(_answered(watched_levels=[review.NO_LEVEL]))


def test_unanswered_counts_rows_not_fields():
    rows = [_answered(), _answered(setup=None), _answered(discipline=None)]
    assert review.unanswered(rows) == 2


# --- storage ----------------------------------------------------------------

def test_null_and_none_are_different_answers():
    """NULL is never-answered; ``'none'`` is answered *no level*. Collapsing them
    would make an unreviewed trade indistinguishable from one taken off nothing."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        assert db.get_note(conn, "k1")["watched_levels"] == []
        db.set_trade_review(conn, "k1", "C", [review.NO_LEVEL])
        assert db.get_note(conn, "k1")["watched_levels"] == ["none"]


def test_the_review_writes_without_a_note_row():
    """A grade must land on a trade that has never had a note saved."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.set_trade_review(conn, "fresh", "A", ["vwap"])
        got = db.get_note(conn, "fresh")
        assert (got["grade"], got["watched_levels"]) == ("A", ["vwap"])
        assert got["note"] == ""


def test_saving_a_note_does_not_blank_the_review():
    """The property the partial fields exist for. The journal form sends a whole
    NoteIn and has never heard of a grade; if omission cleared it, editing a note
    on the Trades page would silently un-review the trade."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(
            grade="D", watched_levels=["vwap_u1"], tags=["oversized"]))
        notes.put_note("k1", notes.NoteIn(note="a later thought", tags=["oversized"]))
        got = db.get_note(conn, "k1")
        assert (got["grade"], got["watched_levels"]) == ("D", ["vwap_u1"])
        assert got["note"] == "a later thought"


def test_the_review_does_not_blank_the_note():
    """And the other direction: grading a trade must not wipe prose written
    earlier, which is what a whole-row upsert would have done."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(note="wicked out", tags=["too tight"]))
        db.set_trade_review(conn, "k1", "C", ["devVP_vah"])
        got = db.get_note(conn, "k1")
        assert got["note"] == "wicked out"
        assert got["grade"] == "C"


def test_several_levels_are_one_answer():
    """Confluence is the ordinary reason a level gets traded — the globex POC on
    the weekly VWAP is the setup, not a tie to be broken. Picked order is kept:
    it is the only thing in the answer that says which one was really being
    traded."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(
            watched_levels=["gxVP_poc", "wkvwap"],
            setup="joined_pullback", discipline="clean"))
        got = db.get_note(conn, "k1")
        assert got["watched_levels"] == ["gxVP_poc", "wkvwap"]
        assert review.trade_answered(got)


def test_deselecting_a_level_persists():
    """The one place the partial fields are not additive. A pick sent as a list
    replaces the stored set — otherwise unpicking one of two would be a click
    that appears to work and silently does nothing."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(watched_levels=["gxVP_poc", "wkvwap"]))
        notes.put_note("k1", notes.NoteIn(watched_levels=["wkvwap"]))
        assert db.get_note(conn, "k1")["watched_levels"] == ["wkvwap"]


def test_get_note_reports_the_review():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(grade="A", watched_levels=["devVP_val"]))
        out = notes.get_note("k1")
        assert out["grade"] == "A" and out["watched_levels"] == ["devVP_val"]


# --- refused at the door ----------------------------------------------------

def test_an_unknown_grade_is_refused():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            notes.put_note("k1", notes.NoteIn(grade="A+"))
        assert e.value.status_code == 422


def test_a_level_that_is_not_a_level_is_refused():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            notes.put_note("k1", notes.NoteIn(watched_levels=["the 20 ema-ish"]))
        assert e.value.status_code == 422


def test_no_level_cannot_be_picked_beside_a_level():
    """The one contradiction in the vocabulary. Stored, it would be an answer
    every downstream cut has to guess about, so it is refused at the door."""
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            notes.put_note("k1", notes.NoteIn(
                watched_levels=[review.NO_LEVEL, "vwap"]))
        assert e.value.status_code == 422


def test_the_vocab_is_served_not_hardcoded():
    out = notes.review_vocab()
    assert [g["id"] for g in out["grades"]] == list(review.GRADES)
    assert [x["id"] for x in out["setups"]] == list(review.SETUPS)
    assert [x["id"] for x in out["disciplines"]] == list(review.DISCIPLINES)
    assert all(g["says"] for g in out["grades"] + out["setups"] + out["disciplines"])
    assert out["no_level"] == review.NO_LEVEL


def test_an_unknown_axis_answer_is_refused_at_the_door():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        with pytest.raises(HTTPException) as e:
            notes.put_note("k1", notes.NoteIn(setup="vibes"))
        assert e.value.status_code == 422
        with pytest.raises(HTTPException) as e:
            notes.put_note("k1", notes.NoteIn(discipline="yolo"))
        assert e.value.status_code == 422


def test_saving_a_note_does_not_blank_the_axes():
    """Same property the grade already has: the journal form has never heard of
    a setup, and omission must mean unchanged."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note("k1", notes.NoteIn(setup="test", discipline="rushed"))
        notes.put_note("k1", notes.NoteIn(note="a later thought"))
        got = db.get_note(conn, "k1")
        assert (got["setup"], got["discipline"]) == ("test", "rushed")


def test_the_blind_stamp_follows_the_write_path():
    """``graded_at`` says the *stored* grade was answered blind at the recall
    front. A ``blind=True`` write stamps it; a hand repair through PUT /notes
    does not — and clears any stamp it overwrites, because a stamp describing a
    grade that is no longer there would launder a hindsight grade into the
    blind era's statistics."""
    def stamp(conn):
        return conn.execute(
            "SELECT graded_at FROM trade_notes WHERE trade_key = 'k1'"
        ).fetchone()[0]

    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.set_trade_review(conn, "k1", "B", ["vwap"], blind=True)
        assert stamp(conn) is not None
        # A later non-grade write leaves the stamp alone.
        db.set_trade_review(conn, "k1", None, None, setup="test")
        assert stamp(conn) is not None
        # A hand repair replaces the grade and takes the stamp with it.
        notes.put_note("k1", notes.NoteIn(grade="C"))
        assert stamp(conn) is None


def test_the_era_boundary_never_hides_a_partial():
    """The debt taxonomy. A touched trade is owed at ANY age — a partial answer
    is a promise to finish, and the era line exists to drop untouched history,
    never to bury started work. Untouched trades split on the era date."""
    old, new = "2024-01-05", "2026-08-25"
    untouched = {"watched_levels": [], "setup": None, "discipline": None}
    partial = {"watched_levels": ["vwap"], "setup": None, "discipline": None}
    full = {"watched_levels": ["vwap"], "setup": "test", "discipline": "clean"}
    assert review.state_of(untouched, old) == "history"
    assert review.state_of(untouched, new) == "owed"
    assert review.state_of(partial, old) == "owed"
    assert review.state_of(partial, new) == "owed"
    assert review.state_of(full, old) == "reviewed"
    # A lone hindsight grade is a touch too — it opted the trade in.
    assert review.state_of({"grade": "B"}, old) == "owed"


def test_the_thesis_endpoints_are_gone():
    """The vocabulary was deleted, not deprecated: a route still accepting a
    thesis would keep collecting claims nothing reads."""
    for name in ("put_intent", "intent_vocab", "IntentIn"):
        assert not hasattr(notes, name), f"notes.{name} survived the deletion"
    assert not hasattr(replays, "_record_intent")


# --- level candidates -------------------------------------------------------

def test_candidates_are_ordered_by_distance_not_rank():
    """The boundary rule. Rank is the tagger's opinion about how unusual a
    distance was; leading the picker with it would be the machine answering the
    human's question. Distance is a fact about where the chart was.
    """
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.set_trade_levels(conn, [
            # The tightest rank is the *farthest* level, so rank order and
            # distance order disagree — which is the only case that proves which
            # one the picker uses.
            LevelRank("k1", "entry", "value_high", "wkVP_vah", 0.01, -40.0),
            LevelRank("k1", "entry", "session_mean", "vwap", 0.60, 3.0),
            LevelRank("k1", "entry", "value_low", "gxVP_val", 0.30, -12.0),
        ], "t")
        got = level_store.candidates_for(conn, "k1")
        assert [c["id"] for c in got] == ["vwap", "gxVP_val", "wkVP_vah"]
        # The rank still rides along — offered beside an option, not as its order.
        assert got[0]["rank"] == 0.60
        # Named off the member, not the family — "which VAH" is the thing the
        # pooled family label cannot say and the pick is trying to record.
        assert got[0]["label"] == "NY VWAP"
        assert [c["label"] for c in got] == ["NY VWAP", "GX VAL", "WK VAH"]


def test_only_the_entry_anchor_is_offered():
    """The question is what the trade was taken *off*. Exit levels would be a
    different question wearing the same picker."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.set_trade_levels(conn, [
            LevelRank("k1", "entry", "value_low", "devVP_val", 0.2, 2.0),
            LevelRank("k1", "exit", "high_band", "vwap_u1", 0.1, 1.0),
        ], "t")
        assert [c["id"] for c in level_store.candidates_for(conn, "k1")] == ["devVP_val"]


def test_a_trade_with_no_measurement_offers_nothing():
    """And that is not an error — it is the case ``'none'`` exists for."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        assert level_store.candidates_for(conn, "nope") == []


def test_the_label_names_the_session_not_the_family():
    """The families pool NY, globex and weekly because they are collinear, which
    is right for ranking and useless on a chip: "value high (VAH)" does not say
    *which* VAH, and the globex one is a different level to have been watching.
    """
    from journal import level_tag

    assert level_tag.label_for("value_high", "gxVP_vah") == "GX VAH"
    assert level_tag.label_for("value_high", "devVP_vah") == "NY VAH"
    assert level_tag.label_for("value_high", "wkVP_vah") == "WK VAH"
    assert level_tag.label_for("session_mean", "wkvwap") == "WK VWAP"
    assert level_tag.label_for("low_band", "gxvwap_l1") == "GX \u22121\u03c3"


def test_every_member_of_every_family_has_a_label():
    """A chip falling back to the pooled name is the failure this guards: adding
    a series to FAMILIES without naming it would silently un-discriminate one
    level, and only on the days that level happened to be nearest."""
    from journal.level_tag import FAMILIES, MEMBER_LABELS

    missing = [m for members in FAMILIES.values() for m in members
               if m not in MEMBER_LABELS]
    assert not missing, f"unnamed level members: {missing}"


def test_an_unknown_member_falls_back_to_the_family():
    """A stored row can carry a null member, and a chip reading "unknown" where
    a level was measured would be worse than the pooled name."""
    from journal import level_tag

    assert level_tag.label_for("value_high", None) == "value high (VAH)"
    assert level_tag.label_for("value_high", "vah_from_the_future") == "value high (VAH)"
