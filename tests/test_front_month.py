"""Which contract the market is actually trading, and when that changes.

The failure this guards is not a wrong number — it is a *right-looking* one. A
live feed subscribes once, at connect, and the symbol it connects to comes out
of the browser's local storage, so it outlives the quarter somebody typed it in.
Nothing about a rolled contract looks wrong on a chart: the candles print, the
volume is real, the levels are self-consistent. It is simply a different market
from the one the orders are going to.

That happened on 2026-09-14. The tape was `NQU6`, the volume had rolled to
`NQZ6` eight days earlier, and the 18:00 ET session opened 290 points away from
where TradingView's continuous symbol opened. The order plant had resolved the
front month on its own and was routing `MNQZ6`. Every test below is a corner of
that day.

The rule under test is a *calendar*, not a lookup, and that is deliberate — see
``front_month``'s docstring for why neither Databento nor Rithmic may be asked
on the live path.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from journal.live.harvest import (  # noqa: E402
    ROLL_LEAD_DAYS,
    contract_expiry,
    front_month,
    is_front_month,
)

# The day it went wrong, and the two contracts involved.
THE_DAY = date(2026, 9, 14)


def test_the_day_it_went_wrong():
    """2026-09-14: the tape said NQU6 and the market said NQZ6."""
    assert front_month("NQ", THE_DAY) == "NQZ6"
    assert is_front_month("NQU6", THE_DAY) is False
    assert is_front_month("NQZ6", THE_DAY) is True


def test_the_roll_is_eight_days_in_front_of_expiry_not_at_it():
    """The gap the feed sat through.

    NQU6 was still *listed* until 2026-09-18 — replayable, priceable, perfectly
    able to fill an order. It stopped being the front month on 2026-09-10. Those
    eight days are the whole hazard: an expiry check would have said the feed was
    fine on all of them.
    """
    expiry = contract_expiry("NQU6")
    assert expiry == date(2026, 9, 18)
    roll = expiry - timedelta(days=ROLL_LEAD_DAYS)
    assert roll == date(2026, 9, 10)
    # Still front the day before the roll, not on it.
    assert front_month("NQ", date(2026, 9, 9)) == "NQU6"
    assert front_month("NQ", date(2026, 9, 10)) == "NQZ6"


def test_a_contract_is_front_for_the_whole_quarter_before_its_roll():
    """No flapping in between — the answer changes once, on the roll date."""
    assert front_month("NQ", date(2026, 6, 12)) == "NQU6"
    assert front_month("NQ", date(2026, 7, 1)) == "NQU6"
    assert front_month("NQ", date(2026, 9, 9)) == "NQU6"


def test_december_rolls_across_the_year_boundary():
    """The one a single-pass loop gets wrong: Z rolls to the *next* year's H."""
    assert front_month("NQ", date(2026, 12, 1)) == "NQZ6"
    assert front_month("NQ", date(2026, 12, 11)) == "NQH7"
    assert front_month("NQ", date(2026, 12, 31)) == "NQH7"


def test_the_micro_rolls_with_its_mini():
    """MNQ is on the same calendar, which is what makes the pairing honest.

    The order path resolves the micro separately, so this is the assertion that
    the two answers agree — the case ``Broker._find_siblings`` warns about is a
    *lookup* disagreeing, not the calendar.
    """
    assert front_month("MNQ", THE_DAY) == "MNQZ6"
    assert is_front_month("MNQU6", THE_DAY) is False


def test_a_root_with_no_known_cycle_gets_no_opinion_rather_than_a_guess():
    """None, not False — the distinction the banner is drawn off.

    CL lists monthly and settles nowhere near a third Friday. Answering "not
    front" for it would put a red warning on a contract nobody can adjudicate,
    and a warning that cries wolf is one that gets ignored when it is right.
    """
    assert front_month("CL") is None
    assert is_front_month("CLZ6") is None
    assert is_front_month("NQ") is None        # a root is not a raw contract
    assert is_front_month("") is None


def test_every_quarterly_contract_says_it_is_front_on_its_own_watch():
    """The round trip: ask for the front month, and it agrees that it is.

    Cheap, and it is the property that actually has to hold — the two functions
    are read together (one names the fix, the other decides whether to warn) and
    a disagreement between them would draw a banner pointing at the contract
    already on screen.
    """
    day = date(2026, 1, 1)
    for _ in range(24):
        sym = front_month("NQ", day)
        assert sym is not None
        assert is_front_month(sym, day) is True, f"{sym} disowned itself on {day}"
        day += timedelta(days=30)
