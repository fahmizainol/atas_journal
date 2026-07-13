"""The contract roll: which raw contract each session trades.

The thing under test is an invariant, not a mapping. Databento's continuous symbol
rolls at 19:00 ET, an hour *inside* our Globex segment — on 2025-12-16 it flipped
front month mid-overnight and the price gapped 225 points. So the roll date is
taken from Databento but applied at our session boundary, and what must hold is
that one session never spans two contracts. Everything below is that, or the
back-compat that keeps already-stored runs replaying to the trades they reported.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from journal.sim import ticks  # noqa: E402

# The real December 2025 roll, as resolved from NQ.v.0's RTH instrument_id.
ROLL_MAP = {
    "2025-12-15": "NQZ5",
    "2025-12-16": "NQZ5",  # Databento flips to H6 at 19:00 ET *this evening*...
    "2025-12-17": "NQH6",  # ...so the next session is where we roll.
    "2025-12-18": "NQH6",
    "2025-12-19": "NQH6",  # Z5's expiry: it has no RTH at all.
}


@pytest.fixture()
def cache(monkeypatch):
    """A tick cache dir holding a known NQ roll map, and no network."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(ticks, "TICK_CACHE_DIR", Path(tmp))
        # The map is memoised per process; a stale entry here would read the real
        # cache dir's map instead of this one.
        monkeypatch.setattr(ticks, "_ROLL_CACHE", {})
        (Path(tmp) / "roll_NQ.json").write_text(json.dumps(ROLL_MAP))
        yield Path(tmp)


def test_a_session_never_spans_two_contracts(cache):
    """The whole point. Both segments of a session resolve to one contract, so the
    overnight can't be stitched across the roll the way NQ.v.0 would stitch it."""
    for day in ROLL_MAP:
        d = date.fromisoformat(day)
        # There is exactly one answer per session — the overnight is not resolved
        # separately from the RTH, which is what makes the seam impossible.
        assert ticks.contract_for("NQ", d) == ROLL_MAP[day]


def test_the_roll_lands_between_sessions_not_inside_one(cache):
    """16 Dec trades Z5 start to finish; 17 Dec trades H6 start to finish. Databento's
    own switch (19:00 ET on the 16th) falls in the gap between them."""
    assert ticks.contract_for("NQ", date(2025, 12, 16)) == "NQZ5"
    assert ticks.contract_for("NQ", date(2025, 12, 17)) == "NQH6"


def test_expiry_day_resolves_to_the_live_contract(cache):
    """The bug that started this: NQZ5 stops trading at the open on 19 Dec, so a run
    pinned to it found no RTH ticks and died at 49/50 sessions. Rolling, 19 Dec is
    simply an H6 session."""
    assert ticks.contract_for("NQ", date(2025, 12, 19)) == "NQH6"


def test_a_pinned_contract_is_never_rolled(cache):
    """A stored run pinned 'NQZ5' and reported trades against it. It must keep
    replaying to those trades, expiry or not — so a pinned symbol is used verbatim,
    and still runs dry on 19 Dec exactly as it did before."""
    for d in (date(2025, 12, 16), date(2025, 12, 19)):
        assert ticks.contract_for("NQZ5", d) == "NQZ5"
    assert ticks.ensure_roll_map("NQZ5", date(2025, 12, 15), date(2025, 12, 19)) == {}


def test_an_unknown_symbol_is_taken_at_face_value(cache):
    """Only instruments we keep specs for can be rolled — rolling means asking
    Databento for a continuous symbol. Anything else is fetched as written."""
    assert not ticks.rolls("NQZ5")
    assert not ticks.rolls("TEST")
    assert ticks.rolls("NQ")
    assert ticks.contract_for("TEST", date(2025, 12, 19)) == "TEST"


def test_a_holiday_carries_the_previous_session_forward(cache, monkeypatch):
    """A holiday has no RTH bars, so the probe returns nothing for it. It must still
    land in the map: without an entry, every later lookup re-probes Databento for a
    day that will never have one, and the charts call this once per request."""
    monkeypatch.setattr(ticks, "_probe_front_month", lambda *a: {})
    christmas = date(2025, 12, 25)  # a weekday with no session
    assert ticks.contract_for("NQ", christmas) == "NQH6"
    # ...and it was written down, so the next call is a dict hit, not a fetch.
    written = json.loads((cache / "roll_NQ.json").read_text())
    assert written["sessions"]["2025-12-25"] == "NQH6"


def test_a_probe_confirmed_holiday_is_known_closed(cache, monkeypatch):
    """Christmas sits between real sessions: the probe finds bars after it but none
    on it. That is positive evidence the exchange was shut, and it is what lets the
    runner skip the day instead of failing the whole run on an empty tick pull."""
    monkeypatch.setattr(ticks, "_probe_front_month",
                        lambda *a: {"2025-12-24": "NQH6", "2025-12-26": "NQH6"})
    ticks.ensure_roll_map("NQ", date(2025, 12, 24), date(2025, 12, 26))
    assert ticks.market_closed("NQ", date(2025, 12, 25))
    assert not ticks.market_closed("NQ", date(2025, 12, 24))
    # The verdict survives the process: it's in the file, not just the cache.
    written = json.loads((cache / "roll_NQ.json").read_text())
    assert written["closed"] == ["2025-12-25"]


def test_a_quiet_trailing_day_is_never_called_closed(cache, monkeypatch):
    """A day with no bars at the END of the probe is ambiguous: holiday, or data not
    yet published. Calling it closed would let a run silently skip a real session —
    so it carries forward for resolution but stays skippable-by-nobody, and an empty
    tick pull there still fails the run."""
    monkeypatch.setattr(ticks, "_probe_front_month", lambda *a: {})
    ticks.ensure_roll_map("NQ", date(2025, 12, 25), date(2025, 12, 25))
    assert ticks.contract_for("NQ", date(2025, 12, 25)) == "NQH6"
    assert not ticks.market_closed("NQ", date(2025, 12, 25))


def test_a_pinned_contract_never_has_a_closed_day(cache):
    """No probe, no evidence: a pinned run must keep failing on any empty day, as
    every stored run always has."""
    assert not ticks.market_closed("NQZ5", date(2025, 12, 25))
    assert not ticks.market_closed("TEST", date(2025, 12, 25))


def test_preflight_prices_each_contract_separately(cache, monkeypatch):
    """A window spanning the roll must be costed as one range per contract. A single
    range across the seam would be priced against the wrong symbol — and the spend
    guard is the only thing standing between a typo and a real Databento bill."""
    from journal.sim import runner
    from journal.sim.rules import SimConfig

    asked: list[tuple] = []

    def fake_cost(symbol, start, end, include_overnight=False):
        asked.append((symbol, start, end))
        return 1.0

    monkeypatch.setattr(ticks, "estimate_cost", fake_cost)
    cfg = SimConfig(contract="NQ", start_date=date(2025, 12, 15), end_date=date(2025, 12, 19))
    pf = runner.preflight(cfg, fetch_overnight=False)

    assert pf["contracts"] == ["NQH6", "NQZ5"]
    assert sorted(asked) == [
        ("NQH6", date(2025, 12, 17), date(2025, 12, 19)),
        ("NQZ5", date(2025, 12, 15), date(2025, 12, 16)),
    ], "no priced range may straddle the roll"


def test_preflight_does_not_price_a_closed_day(cache, monkeypatch):
    """Christmas has nothing to buy: it must not appear in the uncached count that
    warns the user, nor in the ranges sent for costing."""
    from journal.sim import runner
    from journal.sim.rules import SimConfig

    monkeypatch.setattr(ticks, "_probe_front_month",
                        lambda *a: {"2025-12-24": "NQH6", "2025-12-26": "NQH6"})
    monkeypatch.setattr(ticks, "estimate_cost", lambda *a, **k: 1.0)
    cfg = SimConfig(contract="NQ", start_date=date(2025, 12, 24), end_date=date(2025, 12, 26))
    pf = runner.preflight(cfg, fetch_overnight=False)
    assert pf["sessions_total"] == 3  # the window still spans the holiday
    assert pf["uncached_days"] == ["2025-12-24", "2025-12-26"]


def test_regime_resolves_the_roll_before_reading_the_cache(cache, monkeypatch):
    """The regime router is handed the run's cfg.contract — 'NQ' for a rolling
    run — but tick and regime caches are keyed by the raw contract that traded.
    Without resolving first, every day of a rolling run reads an empty cache and
    the run page shows no regime at all. And it must resolve *offline*: regime
    is a GET, so a day the map doesn't know is a None, never a Databento probe."""
    from journal.sim import regime as regmod

    monkeypatch.setattr(regmod, "REGIME_DIR", cache / "regime")
    monkeypatch.setattr(
        ticks, "_probe_front_month",
        lambda *a: (_ for _ in ()).throw(AssertionError("a GET must not probe")))
    seen: list[str] = []
    monkeypatch.setattr(ticks, "cached_rth", lambda s, d: seen.append(s))
    regmod.get_regime("NQ", date(2025, 12, 16))
    regmod.get_regime("NQ", date(2025, 12, 19))
    assert seen == ["NQZ5", "NQH6"], "the cache is read under what actually traded"
    # Before the map begins there is nothing to carry forward: a quiet None,
    # where contract_for would raise — a missing regime is not an error.
    assert regmod.get_regime("NQ", date(2025, 1, 6)) is None


def test_an_unresolvable_session_raises_rather_than_guessing(cache, monkeypatch):
    """No map entry and nothing before it to carry forward. Guessing a contract here
    would silently simulate the wrong instrument; a run that can't name what it traded
    must not report metrics."""
    monkeypatch.setattr(ticks, "_probe_front_month", lambda *a: {})
    with pytest.raises(RuntimeError, match="cannot resolve the front-month contract"):
        ticks.contract_for("NQ", date(2025, 1, 6))  # before the map starts
