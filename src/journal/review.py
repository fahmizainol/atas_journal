"""What a trade owes its review, and the vocabulary it owes it in.

The predicate lives here rather than in the router because three places have to
agree on it — the sitting's ``reviewed`` PATCH, the next-drill create, and the
panel that mirrors both — and a gate that disagrees with the form in front of you
is the specific failure that makes a review feel broken.

The grades are A-D on the whole trade. Until 2026-08-31 they were part of this
gate, assigned in the review panel with the P&L on screen — and the 132 reviews
written that way showed exactly what docs/trade-grading-plan.md decision G3
predicted, only worse: every A and B won, every D lost, so the grade was the
P&L sign wearing a letter. The grade now belongs to the recall front — answered
at the fill-freeze, before the outcome fetch — and is no longer part of what the
*panel* gate asks. The scale itself is unchanged.

What replaced it in the gate are the two enumerated axes the free-tag box was
holding by accident. ``SETUPS`` is the faded-vs-joined question — the one axis
worth keeping from the deleted thesis review — crossed with the shape of the
trade. ``DISCIPLINES`` is whether the plan was followed, which the old tags
("Revenge", "FOMO", "Under risk") were already answering, one trade in three.
Both are small on purpose: an axis you can query is an axis with few values,
and anything they cannot say still has the free tags and the note.
"""

from __future__ import annotations

GRADES = ("A", "B", "C", "D")

# Shown on the picker, served rather than duplicated in TSX for the same reason
# the thesis vocabulary was: the scale is a domain fact, and a fifth grade
# appearing in a component would be a value nothing else in the system knows.
GRADE_SAYS = {
    "A": "everything lined up — the best trade available",
    "B": "bread and butter, the trade taken hundreds of times",
    "C": "a real setup with most of it missing — a feeler",
    "D": "should not have been taken",
}

# What the trade *was*, faded-vs-joined first. Direction-neutral on purpose:
# "faded_rally" is against whatever run brought price here — a rally sold or a
# selloff bought — because the trade's own side already says which way, and a
# vocabulary that split them would be counting direction twice.
SETUPS = ("faded_rally", "faded_breakout", "faded_extension", "joined_rally",
          "joined_breakout", "joined_pullback", "joined_extension", "test",
          "range_play")

SETUP_SAYS = {
    "faded_rally": "against the run that brought price here — a rally sold or a selloff bought",
    "faded_breakout": "against a break — sold back into the range it tried to leave",
    "faded_extension": "into a working trend, betting its pullback was due — the trend itself not doubted",
    "joined_rally": "with the run already underway — joined mid-move, no pullback waited for",
    "joined_breakout": "with a break, out of the range it left",
    "joined_pullback": "with the trend, entered on its pullback",
    "joined_extension": "with a trend already stretched — added into the extension rather than waiting for it to come back",
    "test": "a feeler — small, early, most of the setup missing",
    "range_play": "inside a range, traded edge to edge",
}

# Whether the plan was followed. "clean" is a real answer and the common one —
# the axis exists for the exceptions, which the P&L join says are expensive
# (the old Revenge/FOMO/oversize tags rode almost entirely on losers).
#
# Ordered by where the sin lives — why you entered (revenge/fomo/rushed), the
# size (over/under), the stop (tight/wide at placement, moved after), the exit
# (cut_early) — so the picker's row reads as a walk through the trade. Still
# single-pick: the answer is the sin that DECIDED the trade, and free tags
# carry any second one.
DISCIPLINES = ("clean", "revenge", "fomo", "rushed", "oversized", "undersized",
               "tight_stop", "wide_stop", "moved_stop", "cut_early")

DISCIPLINE_SAYS = {
    "clean": "took the plan's trade at the plan's size",
    "revenge": "taken to win a loss back",
    "fomo": "chased — the move left without you and you followed it",
    "rushed": "entered before the setup finished forming",
    "oversized": "bigger than the plan allowed",
    "undersized": "smaller than the setup deserved",
    "tight_stop": "the stop was inside the market's noise — it never had room to work",
    "wide_stop": "the stop was farther than the plan allowed — too much risk per contract",
    "moved_stop": "the stop was widened or pulled mid-trade",
    "cut_early": "bailed before the plan resolved — the exit, not the setup, decided the trade",
}

# The answer for a trade that was not taken off any level at all. A real choice,
# not an absence: it is what keeps a trade whose tape was never cached
# answerable, so the gate can only ever block on a missing *answer* and never on
# a missing measurement. Exclusive by definition — it is the only member of the
# vocabulary that contradicts the others, and the door refuses the mix.
NO_LEVEL = "none"


def normalize_levels(levels: list[str]) -> list[str]:
    """A level answer as it should be stored: trimmed, de-duplicated, in the
    order it was picked. Order is kept because it is the only thing in the answer
    that says which level the trader thinks they were *actually* trading."""
    seen: dict[str, None] = {}
    for x in levels:
        s = str(x).strip()
        if s:
            seen.setdefault(s, None)
    return list(seen)


def trade_answered(row: dict) -> bool:
    """Whether one journal row has been reviewed.

    At least one watched level, a setup, and a discipline call. The note is
    deliberately not in here — it is the one field with nothing to say on an
    ordinary trade, and requiring prose on all of them is how a review becomes
    something you fill in rather than something you answer. The free tags left
    the gate on 2026-08-31 for the same reason the grade did: requiring "at
    least one tag" of an open vocabulary was answered seven times with the tag
    ``None``, which is a gate being satisfied rather than a question.

    None of the three has the tri-state problem that killed the model-id gate:
    each is either present or empty, and ``['none']`` is an explicit answer
    rather than a blank.
    """
    if not any(str(x).strip() for x in (row.get("watched_levels") or [])):
        return False
    if str(row.get("setup") or "").strip() not in SETUPS:
        return False
    return str(row.get("discipline") or "").strip() in DISCIPLINES


def unanswered(rows: list[dict]) -> int:
    """How many of an attempt's trades still owe a review."""
    return sum(1 for r in rows if not trade_answered(r))


#: When "every trade owes its review" became the workflow — the day
#: docs/trade-grading-plan.md shipped. Trades entered before this and never
#: touched by a review are *history*: not owed, not faked, still queryable.
#: A calendar line alone would be wrong for this journal — replay sittings
#: draw RANDOM tape days, so reviews land on trades years old — which is why
#: the debt predicate below is touched-OR-in-era, never the date alone.
ERA_START = "2026-08-20"


def touched(row: dict) -> bool:
    """Whether a review was ever *started* on this row — any answer at all.

    Touching a trade opts it into the review debt regardless of its age: a
    partial answer is a promise to finish, and the era boundary must never
    hide it.
    """
    return (
        any(str(x).strip() for x in (row.get("watched_levels") or []))
        or bool(str(row.get("setup") or "").strip())
        or bool(str(row.get("discipline") or "").strip())
        or bool(str(row.get("grade") or "").strip())
    )


def state_of(row: dict, entry_day: str) -> str:
    """One journal row's place in the debt: reviewed | owed | history.

    ``entry_day`` is the trade's entry date as ISO ``YYYY-MM-DD`` (string
    compare is date compare in that form). "owed" is the actionable set —
    what a finishing sweep walks; "history" is the pre-era backlog nobody is
    pretending will be answered.
    """
    if trade_answered(row):
        return "reviewed"
    if touched(row) or entry_day >= ERA_START:
        return "owed"
    return "history"
