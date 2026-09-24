"""Per-trade journal entry: the review, plus notes, tags, model and rule checks.

``trade_key`` here is always the **logical** trade key (``logical_trade_key`` on
the scope frame), so a note written in the logical view still resolves when the
same trade is read as ATAS rows.

The **review** is three of these fields — the levels the trade was taken off,
a setup, and a discipline call (``journal.review``) — and it is what the replay
and drill gates read. The grade rides beside them but is captured at the recall
front, not here. The review fields (and ``grade``) are *partial* on ``NoteIn``:
omitted means unchanged. Every other field here is a whole-row overwrite, which
is a documented blanking hazard that callers work around by echoing fields they
do not own, and widening that obligation to more fields for every caller is
worse than one asymmetry documented in one place.

Setup/confluence badges are still accepted for the archived pre-cutover era, but
saving one no longer registers it in the master list — that auto-registration is
what let any typo become a permanent taxonomy entry. Models are the live
vocabulary now; they're created deliberately, on the Models tab.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from journal import db, review
from journal.level_tag import MEMBERS

from .. import deps

router = APIRouter()


class NoteIn(BaseModel):
    note: str = ""
    tags: list[str] = []
    setups: list[str] = []
    confluences: list[str] = []
    model_id: int | None = None   # None = off-model
    rules_met: list[int] = []     # ids of the model's rules this trade satisfied
    #: The review. ``None`` means *leave it alone* — not "clear it" — so the
    #: journal form and the drill save, neither of which knows these exist,
    #: cannot wipe a review by saving a note. There is deliberately no way to
    #: clear any of them: the gate requires them, so un-answering is not a move.
    #:
    #: ``grade`` is still accepted here for the backfill and for hand repairs,
    #: but no panel sends it any more — since 2026-08-31 the grade is captured
    #: at the recall front (``POST /recall/rate``), which is the one surface
    #: that can ask it blind.
    grade: str | None = None           # journal.review.GRADES
    setup: str | None = None           # journal.review.SETUPS
    discipline: str | None = None      # journal.review.DISCIPLINES
    #: level_tag members, or the single review.NO_LEVEL. A list *replaces* the
    #: stored set — deselecting one of several has to persist — so it is the one
    #: partial field where sending ``[]`` clears rather than skips.
    watched_levels: list[str] | None = None


@router.get("/notes/tags")
def all_tags() -> dict:
    """Every free-form tag ever used on a trade, for autocomplete.

    One shared vocabulary across the journal, the replay review and the drill
    review — tags are the taxonomy that grows by being typed, which is exactly
    what setups/confluences (curated, created on their own tabs) are not.
    Declared above ``/notes/{trade_key}`` so the literal path wins the match.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        tags = db.all_trade_tags(conn)
    return {"tags": tags}


@router.get("/review/vocab")
def review_vocab() -> dict:
    """The review's vocabularies, and the answer that means *no level*.

    Served rather than duplicated in TSX because each scale is a domain fact: a
    picker offering a fifth grade or a seventh setup would collect a value the
    gate, the cuts and the deck have never heard of. The level *options* are not
    here — they are per-trade and ride on the journal row, since which levels
    were nearby is a fact about one fill and not a vocabulary.
    """
    return {
        "grades": [{"id": g, "says": review.GRADE_SAYS[g]} for g in review.GRADES],
        "setups": [{"id": s, "says": review.SETUP_SAYS[s]} for s in review.SETUPS],
        "disciplines": [{"id": d, "says": review.DISCIPLINE_SAYS[d]}
                        for d in review.DISCIPLINES],
        "no_level": review.NO_LEVEL,
    }


@router.get("/notes/{trade_key}")
def get_note(trade_key: str) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        n = db.get_note(conn, trade_key)
        model_id = db.get_trade_model(conn, trade_key)
        checks = db.get_rule_checks(conn, trade_key)
    return {
        "note": n["note"],
        "tags": json.loads(n["tags_json"] or "[]"),
        "setups": json.loads(n["setups_json"] or "[]"),
        "confluences": json.loads(n["confluences_json"] or "[]"),
        "model_id": model_id,
        "rules_met": sorted(rid for rid, met in checks.items() if met),
        "grade": n["grade"],
        "setup": n["setup"],
        "discipline": n["discipline"],
        "watched_levels": n["watched_levels"],
    }


@router.put("/notes/{trade_key}")
def put_note(trade_key: str, body: NoteIn) -> dict:
    """Save a trade's journal entry, and its review if this caller has one.

    Validated here rather than at the picker: an unknown grade or a family that
    is not a family is an answer nothing downstream can read, and it should be
    refused at the door instead of stored and silently ignored by every cut.
    """
    if body.grade is not None and body.grade not in review.GRADES:
        raise HTTPException(422, f"{body.grade!r} is not a grade")
    if body.setup is not None and body.setup not in review.SETUPS:
        raise HTTPException(422, f"{body.setup!r} is not a setup")
    if body.discipline is not None and body.discipline not in review.DISCIPLINES:
        raise HTTPException(422, f"{body.discipline!r} is not a discipline call")
    levels = (None if body.watched_levels is None
              else review.normalize_levels(body.watched_levels))
    if levels is not None:
        for lv in levels:
            if lv != review.NO_LEVEL and lv not in MEMBERS:
                raise HTTPException(
                    422,
                    f"{lv!r} is not a level "
                    f"(or {review.NO_LEVEL!r} for a trade taken off no level)")
        # "No level" and "these levels" cannot both be true, and a row holding
        # both is an answer every cut downstream would have to guess about.
        if review.NO_LEVEL in levels and len(levels) > 1:
            raise HTTPException(
                422,
                f"{review.NO_LEVEL!r} is the answer for a trade taken off no "
                f"level — it cannot be picked alongside one")
    conn = deps.get_conn()
    with deps.db_lock():
        db.save_note(
            conn,
            trade_key,
            body.note,
            json.dumps(body.tags),
            json.dumps(body.setups),
            json.dumps(body.confluences),
        )
        db.set_trade_model(conn, trade_key, body.model_id)
        # Sweeps any check belonging to a rule outside the chosen model, so
        # switching a trade's model can't leave the old model's checks behind.
        db.set_rule_checks(conn, trade_key, body.model_id, body.rules_met)
        # After ``save_note``, which writes the whole row: this one updates two
        # columns in place, so it must not be the write that gets overwritten.
        db.set_trade_review(conn, trade_key, body.grade, levels,
                            setup=body.setup, discipline=body.discipline)
    return {"ok": True}
