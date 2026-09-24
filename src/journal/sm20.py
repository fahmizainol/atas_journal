"""SuperMemo's Algorithm Arena, reached by spawning the vendored Rust binary.

The scheduler itself lives in ``vendor/sm20`` and is not Python: it is ~3,000
lines of decompiled numerics, and re-deriving Delphi's ``Real48`` rounding and
80-bit x87 constants by hand is exactly the kind of transcription work that
fails silently. Compiling the original and talking to it over a pipe cannot
drift from it. See ``vendor/sm20/PROVENANCE.md`` for where the code came from
and what is and isn't verified about it.

**What the Arena actually commits.** Not SM-20's answer — a weighted blend of
five schedulers' answers, in slot order SM-2 / SM-15 / SM-19 / SM-20 / FSRS at
starting weights ``[6, 14, 45, 25, 10]``. SM-20 proper is 25% of the committed
interval and SM-19 is 45%. The weights then adapt from recall outcomes, but
slowly: the learning rate is 0.0317, and one review moves them by about
0.006 out of 100. Two of the five models (M2's optimizer, M3's matrices) only
start learning after 200 reps. Early on this is close to a fixed blend.

**One clock, or the models disagree with each other.** ``today`` is Unix epoch
days and each per-model state stores its own ``last_review_day``, from which M1
derives its used interval independently of the ``elapsed_days`` we pass. Feed
those two from different clocks and the models silently schedule off different
histories. :func:`epoch_day` is the only thing that should produce ``today``,
and ``elapsed_days`` must be measured against the same scale.

**The collection is not optional.** Unlike SM-2, where a card's schedule is a
function of that card alone, the Arena keeps deck-wide state: the live blend
weights, M2's optimizer, and M3's 21x21x21 matrices. It is ~270 KB of JSON and
it round-trips on every rating. Lose it and the deck's accumulated tuning
silently resets to defaults.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

from . import config

# The vendored crate's release artifact. Overridable so a test can point at a
# build somewhere else, and so a packaged install can ship the binary apart
# from the source tree.
BINARY = Path(
    os.environ.get("SM20_BINARY")
    or config.ROOT / "vendor" / "sm20" / "target" / "release"
    / ("sm20-schedule.exe" if os.name == "nt" else "sm20-schedule")
)

BUILD_HINT = "cargo build --release --manifest-path vendor/sm20/Cargo.toml"

# SuperMemo's default requested forgetting index, percent. At 10 the retention
# multiplier ``ln(1 - fi/100) / ln(0.9)`` is exactly 1, so a model's candidate
# stability reads directly as days.
DEFAULT_FI = 10

# The deck's four buttons onto SuperMemo's 0-5 grade scale. Mirrors
# ``rating_to_grade`` in the Rust; kept here too so the caller can map without
# paying for a process spawn.
_GRADE_OF_RATING = {1: 0, 2: 3, 3: 4, 4: 5}

# How long to wait on one review. The measured cost is ~2.5ms, so anything
# near this bound means the binary is wedged rather than slow, and a recall
# page that hangs is worse than one that falls back to SM-2.
TIMEOUT_S = 10.0

_EPOCH = date(1970, 1, 1)


class Sm20Unavailable(RuntimeError):
    """The scheduler could not be reached: unbuilt, wedged, or it refused.

    Separate from every other error on purpose — it is the one failure the
    caller is expected to absorb by falling back to SM-2 rather than surfacing.
    """


def epoch_day(day: date) -> int:
    """``day`` as Unix epoch days, the only clock the Arena is given."""
    return (day - _EPOCH).days


def rating_to_grade(rating: int) -> int:
    """A four-button rating as a SuperMemo 0-5 grade.

    The gap between Again (0) and Hard (3) is SuperMemo's, not ours: 1 and 2
    are grades a self-rater never volunteers, so collapsing them into the
    failure case is what the four-button layout already means.
    """
    if rating not in _GRADE_OF_RATING:
        raise ValueError(f"{rating!r} is not a rating")
    return _GRADE_OF_RATING[rating]


def available() -> bool:
    """Whether the binary has been built. Cheap enough to call per request."""
    return BINARY.is_file() and os.access(BINARY, os.X_OK)


def review(
    *,
    grade: int,
    elapsed_days: float,
    today: int,
    state: dict | None = None,
    collection: dict | None = None,
    fi: int = DEFAULT_FI,
    seed: int | None = None,
    disperse: bool = True,
) -> dict:
    """Schedule one review. Pure with respect to this process; nothing is stored.

    ``state`` is the card's, ``collection`` the deck's; both come back in the
    result and both are the caller's to persist. Passing ``None`` for either
    means "fresh", which is right for a card's first rating but is a silent
    reset of the deck's learned weights if you do it by accident.

    ``seed`` fixes interval dispersal. Leave it ``None`` in production so cards
    landing on the same day get spread; set it in tests so a review reproduces.
    """
    if not available():
        raise Sm20Unavailable(
            f"{BINARY} is not built. Run: {BUILD_HINT}")

    request = {
        "grade": int(grade),
        "elapsed_days": float(elapsed_days),
        "today": int(today),
        "fi": int(fi),
        "disperse": bool(disperse),
    }
    if state is not None:
        request["state"] = state
    if collection is not None:
        request["collection"] = collection
    if seed is not None:
        request["seed"] = int(seed)

    try:
        proc = subprocess.run(
            [str(BINARY)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise Sm20Unavailable(f"{BINARY} timed out after {TIMEOUT_S}s") from exc
    except OSError as exc:
        raise Sm20Unavailable(f"could not run {BINARY}: {exc}") from exc

    if proc.returncode != 0:
        # The binary writes its diagnosis to stderr and exits non-zero, so an
        # error string can never be mistaken for a schedule.
        raise Sm20Unavailable(
            f"{BINARY} exited {proc.returncode}: {proc.stderr.strip()}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Sm20Unavailable(
            f"{BINARY} wrote {proc.stdout[:200]!r}, which is not JSON") from exc
