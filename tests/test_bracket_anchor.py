"""Where a position's bracket ends up: the fill, or the level.

The fixture is generated from the real `frontend/src/lib/replaySim.ts` by
`tools/bracket-anchor/run.sh`, over a synthetic tape this file rebuilds and
re-prices itself.

WHY IT EXISTS. The ticket promises a distance — "risk 40 ticks" — but the
bracket used to be frozen as *prices* struck from the mark at the gesture, while
the fill lands 250ms later having paid the spread. The position then carried a
stop and a target that were neither what the ticket said nor symmetric about it:
one leg wider by the drift, the other tighter by the same amount. Over the
recorded sittings the fill was not the clicked print on 90% of orders
(`data/research/preset-stop/leg_drift.py`), a median of one tick and a p90 of
six — and once, on 2025-01-30, by 177.

Three rules have to hold at the same time, and the third is the one with real
money behind it: 136 stored sittings must keep re-deriving exactly as before.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures/bracket_anchor.json"


@pytest.fixture(scope="module")
def fx():
    assert FIXTURE.exists(), "run tools/bracket-anchor/run.sh"
    return json.loads(FIXTURE.read_text())


def test_the_fill_really_did_drift(fx):
    """The premise. If the synthetic tape ever stops moving the fill away from
    the clicked print, every assertion below passes vacuously — a bracket
    anchored to either end would agree."""
    for name in ("market_ticks", "market_legacy"):
        c = fx["cases"][name]
        drift = (c["entry"] - c["at"]) / fx["tick"]
        assert drift > 0, f"{name} filled at the price it was clicked at"
        # 250ms of latency on a tape stepping a tick every 50ms, plus a tick of
        # spread paid by a buy: five and one.
        assert drift == fx["latencyMs"] / fx["spec"]["stepMs"] + fx["slipTicks"]


def test_a_market_order_keeps_the_distance_the_ticket_promised(fx):
    """The fix. Both legs sit exactly the ticket's distance from the fill, not
    from the print under the cursor — which is what `stop_ticks` does on a real
    account, and what the ticket's own money preview has always claimed."""
    c = fx["cases"]["market_ticks"]
    assert c["riskTicks"] == fx["stopTicks"]
    assert c["rewardTicks"] == fx["targetTicks"]
    # And as prices: measured off the entry, in the right directions for a long.
    assert c["stop"] == pytest.approx(c["entry"] - fx["stopTicks"] * fx["tick"])
    assert c["target"] == pytest.approx(c["entry"] + fx["targetTicks"] * fx["tick"])


def test_an_order_logged_before_the_distances_existed_is_untouched(fx):
    """The regression that matters. Every stored sitting holds orders with prices
    and no ticks; those must re-derive exactly as they always did — legs on the
    levels struck from the mark, and therefore off by the drift. If this ever
    starts agreeing with the case above, 136 recorded sittings have silently
    changed their P&L."""
    c = fx["cases"]["market_legacy"]
    drift = (c["entry"] - c["at"]) / fx["tick"]
    assert c["riskTicks"] == fx["stopTicks"] + drift
    assert c["rewardTicks"] == fx["targetTicks"] - drift
    # The legs are where the *mark* put them, not where the fill would.
    assert c["stop"] == pytest.approx(c["at"] - fx["stopTicks"] * fx["tick"])
    assert c["target"] == pytest.approx(c["at"] + fx["targetTicks"] * fx["tick"])


def test_the_two_legs_move_in_opposite_directions(fx):
    """Why "both my numbers got smaller" cannot be this bug on its own: the drift
    is one number applied to two legs on opposite sides of the fill. Whatever it
    takes from one it gives to the other."""
    c = fx["cases"]["market_legacy"]
    assert (c["riskTicks"] - fx["stopTicks"]) == -(c["rewardTicks"] - fx["targetTicks"])


def test_a_leg_switched_off_stays_off(fx):
    """Zero is a real value on both legs — it is how the ticket says "no stop" —
    and it must not be read as "nothing was said". Read that way it would fall
    back to the price struck from the mark and hang a stop on a trade that asked
    for none, which is the one failure here that costs money in the direction
    nobody is watching."""
    c = fx["cases"]["market_no_stop"]
    assert c["stop"] is None and c["riskTicks"] is None
    # The other leg is unaffected: still the ticket's distance from the fill.
    assert c["rewardTicks"] == fx["targetTicks"]


def test_a_resting_order_keeps_its_level(fx):
    """A limit or a stop is placed *against* something — a level you drew it on.
    Those brackets do not follow the fill, and the stop order is the case that
    shows it: it triggers at its level and fills a tick past it, so a
    fill-anchored bracket would read 40/60 and a level-anchored one reads 41/59."""
    c = fx["cases"]["resting_stop"]
    drift = (c["entry"] - c["price"]) / fx["tick"] if "price" in c else None
    assert c["riskTicks"] == fx["stopTicks"] + 1
    assert c["rewardTicks"] == fx["targetTicks"] - 1
    assert drift is None or drift == 1
    # The limit fills at its own price, so both anchorings agree there — stated
    # rather than asserted as a discriminating case, because it is not one.
    lim = fx["cases"]["resting"]
    assert lim["riskTicks"] == fx["stopTicks"]
    assert lim["rewardTicks"] == fx["targetTicks"]
