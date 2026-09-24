"""The dealer-gamma regime: the ratio mapping, and the ways it must fail.

The badge on the Live chart and the demo page in ``docs/research`` read the same
books through the same functions, so the risk here is not that the arithmetic is
wrong — it is that the *mapping* is. Three things are guarded:

  - **the curve is in ratio space and interpolates back to the absolute answer**,
    because the whole point of serving ratios is that the client never needs a
    basis constant, and a curve that did not round-trip would put the flip in the
    wrong place while looking perfectly plausible;
  - **the anchor cancels basis**, which is what makes an NQ price readable against
    an NDX book at all — the implied carry has to land in a range that is actually
    carry, not an arbitrary number that happens to render;
  - **every missing input fails soft**, because the collector runs hourly on a box
    that reboots unpredictably and a Live chart that breaks when a cron misses a
    night is worse than one with no badge.

Skips when nothing is banked in ``data/cache/gex`` — the collector's history is
not a build artifact.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import gex as gexmod  # noqa: E402

BOOK = "NDX"


def _banked():
    return gexmod.banked(BOOK)


pytestmark = pytest.mark.skipif(not _banked(), reason="no GEX books banked yet")


def test_regime_curve_is_ratio_space_and_round_trips():
    """`at(curve, 1.0)` must equal the net GEX computed at the book's own spot.

    The client's only way onto this curve is a ratio, so if ratio 1.0 does not
    land on the reference value the entire mapping is off by whatever the error
    is — silently, since every number downstream still renders.
    """
    reg = gexmod.regime(BOOK)
    assert reg is not None
    assert reg["curve"][0][0] == pytest.approx(1 - gexmod.GRID_PCT, abs=1e-4)
    assert reg["curve"][-1][0] == pytest.approx(1 + gexmod.GRID_PCT, abs=1e-4)
    assert len(reg["curve"]) == gexmod.GRID_N

    assert gexmod.at(reg["curve"], 1.0) == pytest.approx(reg["at_ref"], abs=1e-3)


def test_flip_ratio_is_where_the_curve_crosses_zero():
    """The flip is the regime boundary; if it is not a zero crossing it is decor."""
    reg = gexmod.regime(BOOK)
    if reg["flip_r"] is None:
        pytest.skip("no flip inside the grid for this book")
    assert gexmod.at(reg["curve"], reg["flip_r"]) == pytest.approx(0.0, abs=5e-3)
    # And it must round-trip through the absolute level the demo page draws.
    assert reg["flip_r"] * reg["ref"] == pytest.approx(reg["flip"], rel=1e-4)


def test_off_grid_is_none_not_a_clamped_zero():
    """Past ±6% there is no open interest left, and a clamped edge value would
    render as a confident "neutral regime" rather than "off the map"."""
    reg = gexmod.regime(BOOK)
    assert gexmod.at(reg["curve"], 1.20) is None
    assert gexmod.at(reg["curve"], 0.80) is None


def test_expired_contracts_are_dropped():
    """The pre-open snapshot still carries yesterday's expiries with stale gamma.
    Summing them overstated net GEX by 36% the first time round."""
    day = _banked()[-1]
    raw, _ = gexmod.load(BOOK, day)
    rows, spot, note = gexmod.contracts(raw, day)
    assert rows and spot > 0
    assert "expired dropped" in note
    # Nothing that already expired may survive into the rows.
    for _k, dte, _iv, _oi, _sgn in rows:
        assert dte >= 0


def test_missing_book_returns_none_not_an_error():
    """A day with nothing banked must be a soft None all the way up."""
    assert gexmod.regime(BOOK, day=date(1990, 1, 1)) is None
    assert gexmod.load(BOOK, date(1990, 1, 1)) is None
    assert gexmod.regime("NOPE") is None


def test_load_falls_back_to_the_most_recent_earlier_book():
    """The collector misses nights. Asking for a day it missed must answer with
    the last book actually banked, not nothing — that is what `stale_days` on the
    endpoint is then able to report."""
    days = _banked()
    got = gexmod.load(BOOK, date.today())
    assert got is not None
    _raw, day = got
    assert day == max(d for d in days if d <= date.today())


# --- the endpoint ---------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


def test_endpoint_fails_soft_with_no_session_and_no_symbol(client):
    """The Live tab asks before it knows there is a session. Never a 404."""
    r = client.get("/api/live/gex")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_endpoint_fails_soft_when_no_prior_tape(client):
    """A date the tick stores cannot see behind has no anchor, so no badge — and
    still a 200, because the chart must draw regardless."""
    r = client.get("/api/live/gex", params={"symbol": "NQU6", "date": "2020-01-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    # 2020 predates every book as well as the tape; either reason is a soft fail.
    assert body["reason"]


def test_endpoint_rejects_an_unknown_book(client):
    """Only the two chains that are actually collected."""
    assert client.get(
        "/api/live/gex", params={"symbol": "NQU6", "date": "2026-08-14", "book": "SPX"}
    ).status_code == 422


def test_endpoint_anchor_cancels_basis(client):
    """NQ = NDX + carry. The mapping never names that number, but it implies one,
    and it has to be carry-shaped: futures over cash, under a few percent. A
    mis-picked anchor (wrong day, wrong store, a post-hour print) shows up here as
    an absurd basis while every other field still looks perfectly reasonable.
    """
    r = client.get("/api/live/gex", params={"symbol": "NQU6", "date": "2026-08-14"})
    body = r.json()
    if not body.get("available"):
        pytest.skip(body.get("reason", "no data"))
    assert 0.0 < body["implied_basis_pct"] < 3.0
    # And the ratio mapping must put the flip on the same side of price as the
    # book does, in the book's own units.
    if body["flip_px"] and body["book"]["flip"]:
        assert (body["px_ref"] > body["flip_px"]) == (
            body["book"]["ref"] > body["book"]["flip"]
        )


# --- The chart layer: books by session, and the two-book merge ---------------


def test_a_session_never_reads_a_book_stamped_after_it_opened():
    """The file's name is the day it was banked, not the session it describes, so
    the pick has to go by the Cboe stamp — and never past the Globex open, or a
    replay would be drawn with open interest nobody had yet."""
    from datetime import datetime, time, timedelta

    for session in gexmod.banked(BOOK)[1:]:
        got = gexmod.book_for_session(BOOK, session)
        if got is None:
            continue
        cutoff = datetime.combine(session - timedelta(days=1), time(18, 0))
        cutoff = gexmod._utc(session - timedelta(days=1), time(18, 0))
        assert got.stamp < cutoff
        # And it is the latest such book, not merely an early-enough one.
        later = [b for b in gexmod.books(BOOK) if got.stamp < b.stamp < cutoff]
        assert not later


def test_walls_are_one_per_cluster_heaviest_first():
    """Two strikes 25 points apart are one hedge; the list must not spend two of
    its few lines on them, and rank 0 must be the heaviest."""
    day = gexmod.banked(BOOK)[-1]
    lv = gexmod.levels(BOOK, day)
    assert lv is not None
    for side in ("call_walls", "put_walls"):
        ws = lv[side]
        assert [w["rank"] for w in ws] == list(range(len(ws)))
        assert [w["weight"] for w in ws] == sorted((w["weight"] for w in ws), reverse=True)
        for i, a in enumerate(ws):
            for b in ws[i + 1:]:
                assert abs(a["k"] - b["k"]) > gexmod.CLUSTER_PCT * a["k"]
    assert lv["call_walls"] and lv["put_walls"]


def test_steps_never_start_before_their_update_was_published(client):
    """Each intraday step is drawn from its own publish stamp on, and the steps
    tile the session with no gaps — a replay must not see an update early."""
    r = client.get("/api/gex/levels", params={"symbol": "NQZ6", "start": "2026-09-21",
                                              "end": "2026-09-23", "tz": "New York"})
    assert r.status_code == 200
    for s in r.json()["sessions"]:
        steps = s["steps"]
        assert steps and steps[0]["from"] == s["from"] and steps[-1]["to"] == s["to"]
        for a, b in zip(steps, steps[1:]):
            assert a["to"] == b["from"] and a["from"] < b["from"]


def test_merge_folds_agreeing_walls_and_keeps_the_rest():
    from api.routers.gex import merge_walls

    def w(px, rank=0, weight=1.0, strike=0):
        return {"px": px, "kind": "call", "rank": rank, "weight": weight, "strike": strike}

    merged = merge_walls({
        "NDX": {"available": True, "walls": [w(30000.0, 1, 0.5, 29600), w(31000.0)]},
        "QQQ": {"available": True, "walls": [w(30020.0, 0, 0.9, 730), w(30500.0)]},
    })
    by = {m["px"]: m for m in merged}
    assert set(by) == {30000.0, 30500.0, 31000.0}
    assert by[30000.0]["books"] == ["NDX", "QQQ"]
    assert by[30000.0]["weight"] == 0.9 and by[30000.0]["rank"] == 0
    assert by[30500.0]["books"] == ["QQQ"]
    # A call wall and a put wall at the same price are two levels, not one.
    both = merge_walls({
        "NDX": {"available": True, "walls": [w(30000.0)]},
        "QQQ": {"available": True, "walls": [{**w(30010.0), "kind": "put"}]},
    })
    assert len(both) == 2 and all(m["books"] == [b] for m, b in zip(both, ["NDX", "QQQ"]))
    # A book that is unavailable contributes nothing rather than raising.
    assert merge_walls({"NDX": {"available": False, "reason": "x"}}) == []


def test_anchor_is_read_at_the_books_own_quote_time():
    """The Cboe stamp is UTC and the CDN is 15 minutes delayed, so a book banked
    "15:59" is an ~11:44 ET index price. Anchored on the futures *close* instead,
    every level moved by the afternoon's move (~130 pts on 2026-09-22) while the
    implied carry still looked plausible. Read at the quote's own instant, NDX
    carry decays smoothly day to day — so pin both the range and the smoothness.
    """
    from datetime import timedelta

    from api.routers.gex import book_levels

    ratios = []
    d = date(2026, 8, 18)
    while d <= date(2026, 9, 23):
        if d.weekday() < 5:
            r = book_levels("NQZ6", d, "NDX")
            if r.get("available"):
                ratios.append(r["implied_ratio"])
        d += timedelta(days=1)
    if len(ratios) < 5:
        pytest.skip("not enough NQZ6 tape behind the banked books")
    assert all(1.0 < r < 1.02 for r in ratios)
    # Carry moves a few points a day; the close-anchor bug moved it ~0.4%.
    steps = [abs(b - a) for a, b in zip(ratios, ratios[1:])]
    assert max(steps) < 0.0015, steps


def test_expiry_filter_counts_days_from_the_session_not_the_bank_day():
    """A book reused under a later session must not call contracts that expired
    in between "0DTE" — ages are counted from the session being drawn."""
    from datetime import timedelta

    day = gexmod.banked(BOOK)[-1]
    raw, _ = gexmod.load(BOOK, day)
    later = day + timedelta(days=1)
    while later.weekday() >= 5:
        later += timedelta(days=1)
    rows_bank, _, _ = gexmod.contracts(raw, day)
    rows_later, _, _ = gexmod.contracts(raw, later)
    expired_between = {r[1] for r in rows_bank} - {r[1] + (later - day).days for r in rows_later}
    assert all(r[1] >= 0 for r in rows_later)
    # Something that was 0DTE on the bank day is gone by the later session.
    if any(r[1] == 0 for r in rows_bank):
        assert 0 in expired_between

    full = gexmod.levels(BOOK, day, later, "all")
    zero = gexmod.levels(BOOK, day, later, "0dte")
    if zero is None:
        pytest.skip("no contracts expiring on the later session in this book")
    # Near-dated walls sit closer to spot than the all-expiry ones.
    near = lambda lv: min(abs(w["k_r"] - 1) for w in lv["call_walls"] + lv["put_walls"])
    assert near(zero) <= near(full)
    with pytest.raises(ValueError):
        gexmod.levels(BOOK, day, later, "monthly")
