"""The recall deck's arithmetic: scheduling.

Pure functions and a table of constants, deliberately holding no database handle
— the schedule is the part most worth testing and the part least worth mocking a
connection for. The deck-wide state the Arena needs is passed through the same
way a card is, so this module stays a function of its arguments.

**Two schedulers, one of them optional.** :func:`advance` is what the deck calls.
It commits SuperMemo's Algorithm Arena when ``vendor/sm20`` has been built, and
SM-2 when it hasn't — a fresh clone with no Rust toolchain still has a working
deck, which is why the SM-2 arithmetic below is live code and not history. Either
way the card's counters (``ease``, ``reps``, ``lapses``) are SM-2's, because they
are the card's history rather than any scheduler's working state, and because
they are what the UI shows. Only the interval changes hands.

**Why SM-2 at all, over your own charts.** The usual objection to repeating a
chart is that the second look is recognition rather than reading, so the rep
teaches nothing. That objection assumes the goal is to measure your read. It is
not: the goal is to recognise the situations you have already paid for, which is
exactly the thing recognition is good at. The card is not "can you call this
market" — it is "do you remember what you did here, and what it cost".

**Why nothing is graded.** There is no objective answer to "what does price do
next", so the rating is self-assigned the way an Anki card's is. That admits
dishonesty, and it is how Anki is used in practice; the schedule is only ever as
good as the ratings, and a rating you fudge costs you a rep you needed.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import sm20

# Anki's four buttons. SM-2 proper takes a 0-5 quality; the four-button
# collapse is what people actually press, so it is what we store.
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = (AGAIN, HARD, GOOD, EASY)
RATING_LABEL = {AGAIN: "again", HARD: "hard", GOOD: "good", EASY: "easy"}

EASE_START = 2.5
EASE_MIN = 1.3
EASE_STEP_HARD = -0.15
EASE_STEP_AGAIN = -0.20
EASE_STEP_EASY = 0.15

# The two graduating intervals, in days. A card answered Good for the first time
# comes back tomorrow, the second time in a week; only then does the ease factor
# start compounding.
FIRST_INTERVAL_D = 1.0
SECOND_INTERVAL_D = 6.0
HARD_MULT = 1.2
EASY_BONUS = 1.3
MAX_INTERVAL_D = 365.0

# --- Where the front's tape stops --------------------------------------------
# Nowhere, any more: **the front stops at the fill**, and that is not a constant
# this module owns — it is the trade's own entry stamp, read straight off the
# journal row by ``api.routers.recall``. There is nothing here to schedule, store
# or reshuffle.
#
# What used to be here (until 2026-08-22) was a jittered cut: an instant drawn
# from a window straddling the entry, deterministic per trade key and pinned on
# the card row so it never moved. Its purpose was to keep the card from telling
# you *when* the trade was — with a jitter, the right edge might be before the
# fill or after it and you could not say which.
#
# That is the property this deck gave up, deliberately. A cut minutes off the
# fill shows a chart the setup has not formed on yet, or one where the move is
# already over: either way the rep is spent on a situation that was never traded.
# Stopping on the fill costs the timing blindness and buys the only question the
# deck is for — *this* moment, the one you paid for, what happens next. Direction,
# size, price and outcome stay behind the flip, which is where the blindness that
# still matters lives.


def new_card(today: date) -> dict:
    """An unseen card: due now, no history."""
    return {
        "due": today.isoformat(),
        "interval_d": 0.0,
        "ease": EASE_START,
        "reps": 0,
        "lapses": 0,
    }


def schedule(card: dict, rating: int, today: date) -> dict:
    """The card, advanced by one rating. Pure — returns a new dict.

    ``Again`` resets the rep count rather than shortening the interval, so a card
    you have lost comes back through the same two graduating steps a new one
    does. Halving a mature interval instead would keep showing you a card you
    have demonstrably stopped knowing at intervals that assume you know it.
    """
    if rating not in RATINGS:
        raise ValueError(f"{rating!r} is not a rating")

    ease = float(card.get("ease") or EASE_START)
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)
    interval = float(card.get("interval_d") or 0.0)

    if rating == AGAIN:
        ease = max(EASE_MIN, ease + EASE_STEP_AGAIN)
        return {
            "due": (today + timedelta(days=1)).isoformat(),
            "interval_d": FIRST_INTERVAL_D,
            "ease": ease,
            "reps": 0,
            "lapses": lapses + 1,
        }

    if rating == HARD:
        ease = max(EASE_MIN, ease + EASE_STEP_HARD)
        interval = max(FIRST_INTERVAL_D, interval * HARD_MULT)
    else:
        if rating == EASY:
            ease = ease + EASE_STEP_EASY
        if reps == 0:
            interval = FIRST_INTERVAL_D
        elif reps == 1:
            interval = SECOND_INTERVAL_D
        else:
            interval = interval * ease
        if rating == EASY:
            interval *= EASY_BONUS

    interval = min(MAX_INTERVAL_D, interval)
    return {
        "due": (today + timedelta(days=round(interval))).isoformat(),
        "interval_d": interval,
        "ease": ease,
        "reps": reps + 1,
        "lapses": lapses,
    }


# A committed interval below this still means "tomorrow". The Arena is free to
# answer in fractions of a day and SM-20 has intra-day repetitions; this deck
# does not — its cards are days on a calendar, and rounding 0.4 down to today
# would re-show the same card for the rest of the sitting.
MIN_DUE_D = 1


def advance(card: dict, rating: int, today: date,
            collection: dict | None = None) -> tuple[dict, dict | None]:
    """The card, advanced by one rating, plus the deck state that produced it.

    Returns ``(card, collection)``. ``collection`` comes back ``None`` when the
    Arena did not run, which the caller must treat as "leave the stored state
    alone" rather than "store None" — writing a fresh collection over a learned
    one is the one way this deck loses tuning it cannot recompute.

    The card's own SM-2 counters advance either way; when the Arena runs, its
    interval replaces SM-2's and its item state rides along on the card. If the
    binary is missing or refuses, SM-2's answer is already computed and is what
    gets committed — the deck degrades, it does not fail.
    """
    nxt = schedule(card, rating, today)

    if not sm20.available():
        return nxt, None

    now = sm20.epoch_day(today)
    last = card.get("last_review_day")
    # Elapsed is measured against the Arena's own last rating, not against the
    # card's due date or SM-2's interval: the models each keep a copy of that
    # day inside their state, and feeding them an interval measured from a
    # different clock has them scheduling off a history that never happened. A
    # card the Arena has never seen is elapsed 0 — a first review, which is what
    # it is, whatever SM-2 did with it before.
    elapsed = 0.0 if last is None else max(0.0, float(now - int(last)))

    try:
        out = sm20.review(
            grade=sm20.rating_to_grade(rating),
            elapsed_days=elapsed,
            today=now,
            state=card.get("sm20_state"),
            collection=collection,
        )
    except sm20.Sm20Unavailable:
        # Built a moment ago, gone now, or wedged. SM-2's answer stands.
        return nxt, None

    interval = float(out["interval_days"])
    nxt["interval_d"] = interval
    nxt["due"] = (today + timedelta(days=max(MIN_DUE_D, round(interval)))).isoformat()
    nxt["sm20_state"] = out["state"]
    nxt["last_review_day"] = now
    return nxt, out["collection"]


def is_due(card: dict | None, today: date) -> bool:
    """A card with no row has never been shown, which counts as due."""
    return card is None or str(card.get("due") or "") <= today.isoformat()
