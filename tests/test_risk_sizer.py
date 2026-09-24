"""The ticket's risk sizer, held to its arithmetic.

The fixture is generated from the real `frontend/src/lib/riskSizer.ts` by
`tools/ticket-sizer/run.sh`. This file re-derives every row from first
principles in Python and asserts the two agree, plus the invariants that make a
recommendation safe to click.

Why bother, when the TypeScript is twelve lines of arithmetic: both ways it can
be wrong are silent. Drop the commission from the denominator and every ticket
comes out slightly too big, forever. Price a micro as a mini and one ticket
comes out ten times too big, once. Neither throws.

Re-run the generator and commit the fixture when the rule changes on purpose.
"""
from __future__ import annotations

import json
import math
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures/risk_sizer.json"

#: contracts.ts — a micro is a tenth of its mini, but its commission is floored
#: rather than divided, so the micro route pays more in fees per unit exposure.
MICRO_RATIO = 10.0
MICRO_COMMISSION_FLOOR = 0.5


@pytest.fixture(scope="module")
def fx():
    assert FIXTURE.exists(), "run tools/ticket-sizer/run.sh"
    return json.loads(FIXTURE.read_text())


def micro_commission(per_side: float) -> float:
    return max(MICRO_COMMISSION_FLOOR, per_side / MICRO_RATIO) if per_side > 0 else 0.0


def per_contract(stop_ticks: float, tick_usd: float, commission: float) -> float:
    """The whole rule: what one contract costs to be wrong about, **net** of
    both sides' fees. `guardRules.shapeRefusal` computes this gross, which is
    the $7 disagreement documented in riskSizer.ts."""
    return stop_ticks * tick_usd + 2 * commission


def test_stop_follows_the_ruler(fx):
    """Stop = ruler x STOP_MULT, rounded, floored at one tick. The multiplier is
    1.0 while the rule is being tested by hand."""
    assert fx["stopMult"] == 1.0
    for r in fx["rows"]:
        assert r["stopTicks"] == max(1, round(r["volTicks"] * fx["stopMult"]))


def test_budgets_are_three_constants(fx):
    """The three presets are three dollar figures, and that is the whole rule.

    They were computed until 2026-09-22 — the daily loss limit over how many
    losers the appetite would spend it on, less a flat $50 cushion — which
    reached these same numbers on LucidPro 50K and re-priced every row on any
    other account. Pinned here as literals rather than read back out of the
    fixture, because a table the test copies from the code cannot notice the
    code changing.

    What is still worth asserting beyond the values: nothing moves them. One
    budget per appetite across every vol reading in the fixture — the tape has
    no say, and since `dayLossUsd` left `SizerInput` the account has none
    either."""
    assert fx["budgets"] == {"safe": 150, "moderate": 250, "aggressive": 350}
    assert "dayLossUsd" not in fx["base"], "the sizer should not be asking for a day limit"
    by_appetite = {}
    for r in fx["rows"]:
        by_appetite.setdefault(r["appetite"], set()).add(r["budget"])
    for appetite, budgets in by_appetite.items():
        assert budgets == {fx["budgets"][appetite]}, (appetite, budgets)


def test_sizes_are_the_most_that_fit(fx):
    """Every route is floor(budget / per-contract) — and, crucially, one more
    contract would NOT fit. That second half is what catches an off-by-one that
    a floor test alone would pass."""
    tick_usd = fx["contract"]["tickUsd"]
    comm = fx["contract"]["commissionPerSide"]
    caps = fx["base"]["caps"]
    for r in fx["rows"]:
        stop = r["stopTicks"]
        per_mini = per_contract(stop, tick_usd, comm)
        per_micro = per_contract(stop, tick_usd / MICRO_RATIO, micro_commission(comm))

        want_minis = min(int(r["budget"] // per_mini), caps["minis"])
        if want_minis >= 1:
            assert r["mini"] is not None, f"{r['volTicks']}t {r['appetite']}: mini route missing"
            assert r["mini"]["minis"] == want_minis
            assert r["mini"]["risk"] == pytest.approx(want_minis * per_mini, abs=0.01)
            # One more would not fit (unless the cap is what stopped us).
            if want_minis < caps["minis"]:
                assert (want_minis + 1) * per_mini > r["budget"]
        else:
            assert r["mini"] is None, f"{r['volTicks']}t {r['appetite']}: mini should not fit"

        want_micros = min(int(r["budget"] // per_micro), caps["micros"])
        if want_micros >= 1:
            assert r["micro"] is not None
            assert r["micro"]["micros"] == want_micros
            assert r["micro"]["risk"] == pytest.approx(want_micros * per_micro, abs=0.01)
            if want_micros < caps["micros"]:
                assert (want_micros + 1) * per_micro > r["budget"]
        else:
            assert r["micro"] is None


def test_no_route_ever_exceeds_its_budget(fx):
    """The one invariant that makes a cell safe to click. A recommendation that
    can hand you more risk than you asked for is worse than no recommendation."""
    for r in fx["rows"]:
        for leg in ("mini", "micro"):
            if r[leg]:
                assert r[leg]["risk"] <= r["budget"] + 1e-9, (
                    f"{r['volTicks']}t {r['appetite']} {leg}: "
                    f"${r[leg]['risk']} over a ${r['budget']} budget"
                )


def test_fees_are_in_the_risk_not_beside_it(fx):
    """Risk is net. Strip the fees and what is left must be exactly the ticks x
    money — which is what `shapeRefusal` computes, and is not the same number."""
    tick_usd = fx["contract"]["tickUsd"]
    for r in fx["rows"]:
        if r["mini"]:
            gross = r["mini"]["risk"] - r["mini"]["fees"]
            assert gross == pytest.approx(r["stopTicks"] * tick_usd * r["mini"]["minis"], abs=0.01)
        if r["micro"]:
            gross = r["micro"]["risk"] - r["micro"]["fees"]
            assert gross == pytest.approx(
                r["stopTicks"] * (tick_usd / MICRO_RATIO) * r["micro"]["micros"], abs=0.01
            )


def test_micro_costs_more_per_unit_of_exposure(fx):
    """The reason the panel shows both routes rather than picking one. A micro
    round turn is $1.00 against the mini's $7.00 for a tenth of the exposure —
    2.0 ticks against 1.4. If this ever inverts, the floor constant moved."""
    for r in fx["rows"]:
        if not (r["mini"] and r["micro"]):
            continue
        mini_fee_per_exposure = r["mini"]["fees"] / r["mini"]["exposure"]
        micro_fee_per_exposure = r["micro"]["fees"] / r["micro"]["exposure"]
        assert micro_fee_per_exposure > mini_fee_per_exposure


def test_hot_tape_drops_the_mini_before_the_micro(fx):
    """The size-down signal, and the reason it needed no rule of its own: as the
    ruler widens, the budget stops reaching a whole mini while micros still fit.
    A row offering a mini but no micro would mean the granular route had become
    the coarse one."""
    for r in fx["rows"]:
        if r["mini"] is not None:
            assert r["micro"] is not None, (
                f"{r['volTicks']}t {r['appetite']}: mini fits but micro does not"
            )


def test_minis_are_monotone_in_vol(fx):
    """Within an appetite, a wider ruler can never buy more minis."""
    for appetite in fx["budgets"]:
        rows = sorted(
            (r for r in fx["rows"] if r["appetite"] == appetite),
            key=lambda r: r["volTicks"],
        )
        counts = [(r["mini"]["minis"] if r["mini"] else 0) for r in rows]
        assert counts == sorted(counts, reverse=True), f"{appetite}: {counts}"


def test_losers_to_death_counts_the_route_not_the_row(fx):
    """The two routes spend the same budget but rarely the same money, so they
    rarely buy the same number of mistakes. This is the only figure on the panel
    that connects a ticket to the account floor."""
    max_loss = fx["base"]["maxLossUsd"]
    for r in fx["rows"]:
        for leg in ("mini", "micro"):
            if r[leg]:
                assert r[leg]["losersToDeath"] == math.floor(max_loss / r[leg]["risk"])


def test_stop_ceiling_is_flagged_never_clamped(fx):
    """The guards refuse a stop over the ceiling. The sizer says so and sizes it
    anyway — a stop quietly shrunk to fit a rule is a stop the user did not
    choose, and the ruler said what it said."""
    ceiling = fx["base"]["stopTicksMax"]
    for r in fx["rows"]:
        assert r["overStopCeiling"] == (r["stopTicks"] > ceiling)
        if r["overStopCeiling"]:
            assert r["stopTicks"] == round(r["volTicks"] * fx["stopMult"])


def test_stop_that_fits_is_the_inverse(fx):
    """`stopThatFits` is what the panel offers when nothing does. It must be the
    genuine inverse: that stop fits, one tick wider does not."""
    tick_usd = fx["contract"]["tickUsd"]
    comm = fx["contract"]["commissionPerSide"]
    for f in fx["fits"]:
        per_tick = f["minis"] * tick_usd + f["micros"] * tick_usd / MICRO_RATIO
        fees = 2 * (f["minis"] * comm + f["micros"] * micro_commission(comm))
        risk = f["stopTicks"] * per_tick + fees
        assert risk <= f["budget"] + 1e-9, f"{f}: ${risk} over ${f['budget']}"
        assert (f["stopTicks"] + 1) * per_tick + fees > f["budget"], f"{f}: not the widest"


def test_one_mini_on_a_fifty_tick_stop_does_not_fit_250(fx):
    """The measured headline from docs/research/vol-sizing.md, pinned so it
    cannot drift back: 50 x $5 + $7 = $257. The 48t answer is what the panel
    offers instead."""
    fits = {(f["budget"], f["minis"], f["micros"]): f["stopTicks"] for f in fx["fits"]}
    assert fits[(250, 1, 0)] == 48
    tick_usd = fx["contract"]["tickUsd"]
    comm = fx["contract"]["commissionPerSide"]
    assert per_contract(50, tick_usd, comm) == pytest.approx(257.0)
