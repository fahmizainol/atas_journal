"""The counterfactual campaign: this account under a bracket it never traded.

Three things are being tested, and they are the three that would be silently
wrong rather than loudly broken:

  - **the emitted equity path is a reduction, not a sample.** ``replay_whatif``
    stores drawdowns as (running peak, deepest trough after it) pairs, and the
    claim is that a floor walk learns exactly as much from those as it would
    from every print. So the pairs must reproduce a death the scalars cannot see
    — peak-then-dip, the case ``min_room_usd`` exists for — and must not invent
    one where there was none.
  - **a real sitting is untouched by any of it.** The path branch is additive:
    every attempt a browser ever wrote has no ``equity_path`` and must walk byte
    for byte as it did before. A test that only exercised the new branch would
    pass while the account's real history quietly moved.
  - **the campaign re-buys.** A life ends and the next rep opens a fresh account
    at ``numbers.start``, so the record is a count of completed evals over the
    same reps — and the rep that ended a life is counted by that life and not
    handed to its successor as well.

Run directly:  ``.venv/bin/python tests/test_account_campaign.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import account_campaign as campaign  # noqa: E402
from journal import replay_account as acct  # noqa: E402
from journal import replay_whatif as whatif  # noqa: E402
from journal import replays  # noqa: E402

TAPE = {"n": 900_000, "t0": 1_770_000_000_000, "end": 1_770_050_000_000,
        "rth_open_ms": 1_770_010_000_000}
PREFS = {"size": 1, "stopTicks": 50, "targetTicks": 120, "orderType": "market"}
LOG = {"orders": [], "closes": [], "brackets": []}

#: A 50K under the real LucidPro numbers: floor opens at 48,000.
NUMS = acct.Numbers()


def _tmp(fn):
    def run():
        with tempfile.TemporaryDirectory() as td:
            original = replays.REPLAYS_DIR
            replays.REPLAYS_DIR = Path(td) / "replays"
            try:
                fn()
            finally:
                replays.REPLAYS_DIR = original

    run.__name__ = fn.__name__
    return run


def _row(date: str, net: float, *, pnls=None, path=None, peak=None, ident="a"):
    """A counterfactual sitting, in the shape ``walk`` reads it."""
    summary = {"trades": len(pnls or [1]), "net_usd": net}
    if peak is not None:
        summary["peak_usd"] = peak
    return {"id": ident, "date": date, "created_at": f"{date}T00:00:00Z",
            "finished_at": f"{date}T01:00:00Z", "status": "finished",
            "summary": summary,
            "pnls": pnls if pnls is not None else [net],
            "equity_path": path}


# --- the path branch ---------------------------------------------------------


def test_a_path_catches_the_peak_then_dip_a_trough_alone_cannot():
    """Up 1,500, down to −600, home at +200 — and dead on the way.

    This is the shape the account really met once: a sitting that went through
    the floor, traded back, and settled alive. The trail banked the 51,500 peak,
    which put the floor at 49,500; equity touched 49,400 and came home at 50,200.

    Every scalar reading says it lived. The trough is 1,400 clear of the floor
    the sitting *opened* on, and the settled balance is 700 above the floor it
    ended on. Only the ordered pairs know the dip came **after** the peak that
    raised the floor — which is exactly what ``min_room_usd`` was for, and what a
    bracket that was never traded can never have.
    """
    row = _row("2026-02-03", 200, pnls=[200], path=[[1500.0, -600.0]], peak=1500)
    w = acct.walk([row], numbers=NUMS, trailing="intraday")
    assert w.death is not None, "the floor rose to 49,500 and the equity fell through it"
    assert round(w.death["floor"]) == 49_500
    assert round(w.equity) == 49_400, "equity freezes at the dip, not the settle"

    # The same sitting with only the two scalars a browser reports and no
    # `min_room_usd`. It reads alive, and settles $700 above its own floor.
    blind = {**row, "equity_path": None}
    blind["summary"] = {**blind["summary"], "trough_usd": -600.0}
    assert acct.walk([blind], numbers=NUMS, trailing="intraday").death is None

    # A shallower dip after the same peak survives: the check is a comparison,
    # not a rule that any peak-then-dip is fatal.
    lived = _row("2026-02-03", 200, pnls=[200], path=[[1500.0, -100.0]], peak=1500)
    assert acct.walk([lived], numbers=NUMS, trailing="intraday").death is None


def test_a_path_does_not_invent_a_death_the_account_survived():
    """A dip that never reached the floor leaves the account alive."""
    row = _row("2026-02-03", 200, pnls=[200], path=[[0.0, -800.0], [200.0, 200.0]], peak=200)
    w = acct.walk([row], numbers=NUMS, trailing="intraday")
    assert w.death is None
    assert round(w.equity) == 50_200


def test_an_eod_account_ignores_the_path_entirely():
    """The eod floor moves on day closes, so an intraday excursion is not its
    business — the same peak-then-dip row that kills an intraday account leaves
    an eod one untouched."""
    row = _row("2026-02-03", -100, pnls=[-100], path=[[1500.0, -100.0]], peak=1500)
    w = acct.walk([row], numbers=NUMS, trailing="eod")
    assert w.death is None
    assert round(w.equity) == 49_900


def test_a_real_sitting_walks_exactly_as_it_did_without_a_path():
    """The additive claim, tested rather than asserted: a row with neither
    ``pnls`` nor ``equity_path`` — every attempt a browser ever wrote — reaches
    the scalar branch and dies where it always did."""
    real = {"id": "b", "date": "2026-02-03", "created_at": "2026-02-03T00:00:00Z",
            "finished_at": "2026-02-03T01:00:00Z", "status": "finished",
            "summary": {"trades": 1, "net_usd": -100.0,
                        "peak_usd": 1500.0, "trough_usd": -100.0,
                        "min_room_usd": -400.0}}
    assert acct.equity_path(real) is None
    w = acct.walk([real], numbers=NUMS, trailing="intraday")
    assert w.death is not None, "min_room_usd is still the browser's verdict"


def test_a_malformed_path_is_no_path_rather_than_a_wrong_one():
    for bad in ([], [[1.0]], [["x", 2.0]], "nope", {"hi": 1}):
        assert acct.equity_path({"equity_path": bad}) is None


# --- pnls off the row --------------------------------------------------------


def test_trade_pnls_prefers_the_row_and_a_real_row_still_reads_disk():
    assert acct.trade_pnls({"id": "x", "pnls": [1.5, -2.5]}) == [1.5, -2.5]
    # No `pnls` key and an id that resolves to nothing on disk: empty, which is
    # the degradation the walk has always made.
    assert acct.trade_pnls({"id": "2026-02-03_NQH5_20260101T000000Z"}) == []


def test_the_booked_path_still_kills_before_the_settled_sum():
    """A sitting that dived through the floor and traded back is dead — the
    property the whole ``pnls`` field exists to preserve."""
    row = _row("2026-02-03", 50, pnls=[-2_500, 2_550])
    w = acct.walk([row], numbers=NUMS, trailing="eod")
    assert w.death is not None
    assert round(w.equity) == 47_500


# --- the campaign ------------------------------------------------------------


def test_the_campaign_re_buys_and_counts_the_killing_rep_once():
    rows = [
        _row("2026-02-03", -2_100, pnls=[-2_100], ident="r1"),   # kills life 1
        _row("2026-02-04", 500, pnls=[500], ident="r2"),         # life 2 opens here
        _row("2026-02-05", 400, pnls=[400], ident="r3"),
    ]
    out = campaign.run(rows, NUMS, "eod", key="k", label="L", column="clicked")
    assert [life.outcome for life in out.lives] == ["blown", "live"]
    assert [life.reps for life in out.lives] == [1, 2]
    assert out.lives[0].killed_by == "r1"
    # Life 2 opened fresh at 50,000 and made 900 — the killing rep is not in it.
    assert round(out.lives[1].end_equity) == 50_900
    assert out.record == {"passed": 0, "blown": 1}


def test_a_pass_ends_a_life_too_and_the_next_rep_starts_a_fresh_one():
    rows = [
        _row("2026-02-03", 3_000, pnls=[3_000], ident="r1"),   # clears +3,000
        _row("2026-02-04", -200, pnls=[-200], ident="r2"),
    ]
    out = campaign.run(rows, NUMS, "eod", key="k", label="L", column="clicked")
    assert [life.outcome for life in out.lives] == ["passed", "live"]
    assert out.lives[0].killed_by == "r1"
    assert out.record == {"passed": 1, "blown": 0}
    assert out.score == 1


def test_score_prefers_surviving_to_passing_at_any_cost():
    survived = campaign.Run("a", "A", "clicked", [], 0.0, 0, 0)
    assert survived.score == 0
    bought_four_cleared_one = campaign.Run(
        "b", "B", "clicked",
        [campaign.Life(i, 1, None, None, 0.0, o, None)
         for i, o in enumerate(["passed", "blown", "blown", "blown"])],
        0.0, 0, 0)
    assert bought_four_cleared_one.score == -2
    assert survived.score > bought_four_cleared_one.score


@_tmp
def test_an_unpriceable_rep_is_dropped_from_every_row_and_counted():
    a = replays.create(symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
                       engine_version=1, tape=TAPE, prefs=PREFS,
                       started_ms=TAPE["rth_open_ms"], account_id="funded")
    replays.save(a["id"], log=LOG, trades=[{"pnl": -100.0}],
                 summary={"trades": 1, "net_usd": -100.0}, status="finished")
    flat = replays.create(symbol="NQH5", root="NQ", date="2026-02-04", tz="New York",
                          engine_version=1, tape=TAPE, prefs=PREFS,
                          started_ms=TAPE["rth_open_ms"], account_id="funded")
    replays.save(flat["id"], log=LOG, trades=[],
                 summary={"trades": 0, "net_usd": 0.0}, status="finished")

    rows = [replays.read(a["id"]), replays.read(flat["id"])]
    cover = campaign.coverage(rows, {})           # nothing priced
    assert cover == {"reps": 2, "traded": 1, "priced": 0, "unpriced": 1, "flat": 1}

    scen = campaign.scenario_rows(rows, {}, "t25", "clicked")
    # The traded rep is dropped; the flat one stays, because it is a *day* and an
    # end-of-day floor banks its peak when the day changes.
    assert [r["id"] for r in scen] == [flat["id"]]
    assert scen[0]["summary"] == {"trades": 0, "net_usd": 0.0}


@_tmp
def test_a_stale_grid_is_missing_rather_than_usable():
    a = replays.create(symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
                       engine_version=1, tape=TAPE, prefs=PREFS,
                       started_ms=TAPE["rth_open_ms"], account_id="funded")
    replays.save(a["id"], log=LOG, trades=[], summary={}, status="finished")
    replays.write_whatif(a["id"], {"engine_version": 1,
                                   "grid_version": whatif.GRID_VERSION,
                                   "valid": True, "rows": []})
    assert replays.read_whatif(a["id"], engine_version=1,
                               grid_version=whatif.GRID_VERSION) is not None
    # Either fingerprint moving makes it unusable, and neither is a soft warning.
    assert replays.read_whatif(a["id"], engine_version=2,
                               grid_version=whatif.GRID_VERSION) is None
    assert replays.read_whatif(a["id"], engine_version=1,
                               grid_version=whatif.GRID_VERSION + 1) is None


# --- the seeded walk ---------------------------------------------------------


def test_a_seeded_walk_opens_where_the_last_one_left_off():
    """What ``rep_verdicts`` is built on: one rep judged from the equity and the
    floor it really opened against, not from a fresh account."""
    first = acct.walk([_row("2026-02-03", -1_800, pnls=[-1_800])],
                      numbers=NUMS, trailing="eod")
    assert first.death is None and round(first.equity) == 48_200

    # −300 more is survivable from 50,000 and fatal from 48,200.
    rep = _row("2026-02-04", -300, pnls=[-300], ident="r2")
    assert acct.walk([rep], numbers=NUMS, trailing="eod").death is None
    seeded = acct.walk([rep], numbers=NUMS, trailing="eod", seed=first)
    assert seeded.death is not None
    assert seeded.death["attempt_id"] == "r2"


# --- the emitted path itself -------------------------------------------------


def test_the_path_records_every_drawdown_and_drops_only_shallow_ones():
    p = whatif._Path()
    for eq in (0, 100, 90, 300, 299, 500, 60, 200):
        p.mark(eq)
    assert p.peak == 500 and p.trough == 0
    dd = p.done()
    # 100→90 and 300→299 are under the quantum and are not rows. 500→60 is, and
    # it is the one that could cost a life.
    assert dd == [[500.0, 60.0]]
    highs = [hi for hi, _ in dd]
    assert highs == sorted(highs), "peaks must increase"
    assert max(highs) == p.peak


def test_a_dropped_drawdown_is_shallower_than_the_quantum_by_construction():
    """The one approximation in the path, stated as a bound rather than trusted.

    A dip is only left out when the peak it fell from is within
    ``PATH_QUANTUM_USD`` of it — so the floor breach that could hide there is one
    the account survived by under $25. Everything deeper is on the record.
    """
    seen = [(0, 100), (100, 90), (90, 300), (300, 299), (299, 500), (500, 60), (60, 200)]
    p = whatif._Path()
    p.mark(0)
    for _, eq in seen:
        p.mark(eq)
    recorded = {(hi, lo) for hi, lo in p.done()}
    # Every local drawdown either made the record or was shallower than the quantum.
    for hi, lo in ((100, 90), (300, 299), (500, 60)):
        assert (hi, lo) in recorded or hi - lo < whatif.PATH_QUANTUM_USD


def test_a_rally_with_no_dip_collapses_to_one_row():
    p = whatif._Path()
    for eq in range(0, 2_000, 5):
        p.mark(eq)
    assert p.done() == [[1_995.0, 1_995.0]]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
