"""Measured level proximity — the four ways it can lie, and the boundary it must not cross.

The measurement is a claim about *unusualness*, so its failure modes are all
statistical rather than exceptional. It does not raise when it is wrong; it
quietly reports that every trade was taken off a level.

  - **a fill at nothing must score at nothing.** Fills placed at random instants
    on the tape have to rank ~0.5. This is the guard on the whole approach: the
    first two cuts of the study both produced confident level attribution for
    fills that were, by construction, arbitrary — once because a level nobody
    trades near wins a ratio on a dozen coincidences, once because the companion
    instants weren't matched on how far price had run.
  - **a fill on a level must score as such.** The complement; without it the
    first check passes trivially by never firing.
  - **mechanical exits must not be scored at all.** A stop lands at vol-ruler
    distance from entry. Attributing it to a level invents a decision.
  - **a tape that isn't the trade's tape must be refused.** The roll hands back
    the front month at export time, which for one journal day is a contract the
    trade never touched.

And the boundary: measuring must never satisfy the human review gate. If these
numbers reach ``trade_notes``, every sitting reviews itself and the comparison
this was built for — what you said versus where you filled — stops existing.

Run directly:  ``.venv/bin/python tests/test_level_tag.py``
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import level_tag  # noqa: E402
from journal.level_tag import DayLevels, Fill  # noqa: E402

DAY = date(2026, 3, 10)
START = pd.Timestamp("2026-03-10 13:30", tz="UTC")   # 09:30 ET


class _Frame:
    """The smallest thing ``DayLevels`` will accept: a tape, a bar clock, and the
    developing NY value area. Every other slot is absent, which is also a case
    worth exercising — a family with no members must simply not be scored."""

    def __init__(self, ticks, rows, mv_poc_naked=None, mv_poc_revisit=None):
        self.ticks = ticks
        self.profile_ny = rows
        self.mv_poc_naked = mv_poc_naked or []
        self.mv_poc_revisit = mv_poc_revisit or []
        self.bar_time = lambda ts: int(pd.Timestamp(ts).value // 1_000_000_000)


def _session(seed: int = 7, minutes: int = 180):
    """A random-walk session with its value area placed where the walk went.

    A walk, not noise around a mean: the drift-matching this module relies on
    only matters when price actually goes somewhere, and a stationary tape would
    let a broken null pass. The levels are quantiles of the realized path rather
    than fixed constants, so "a fill sitting on the VAL" is always a case this
    tape can express — otherwise the test that proves the measurement can fire
    silently skips itself on an unlucky seed.

    Returns ``(DayLevels, {'val':…, 'poc':…, 'vah':…})``.
    """
    rng = np.random.default_rng(seed)
    n = minutes * 60
    steps = rng.choice([-0.25, 0.0, 0.25], size=n, p=[0.25, 0.5, 0.25])
    price = 20_000.0 + np.cumsum(steps)
    ts = START + pd.to_timedelta(np.arange(n), unit="s")
    ticks = pd.DataFrame({"ts_utc": ts, "price": price})

    q = {k: round(float(np.quantile(price, p)) * 4) / 4
         for k, p in (("val", 0.15), ("poc", 0.5), ("vah", 0.85))}
    rows = [{"time": int(t.value // 1_000_000_000), **q} for t in ts[::60]]
    return DayLevels(_Frame(ticks, rows)), q


def _session_with_poc_anchor(slot: str, seed: int = 7, minutes: int = 180):
    """The same tape, plus a POC-anchored VWAP parked midway between the VAL and
    the POC — a price the value area is nowhere near, so "scored against
    ``mv_poc_*``" cannot be a value family answering under another name."""
    lv, q = _session(seed=seed, minutes=minutes)
    level = round((q["val"] + q["poc"]) / 2 * 4) / 4
    gap = min(abs(level - v) for v in q.values())
    assert gap >= 2.0, "the fixture must separate the POC anchor from the value area"
    secs = np.unique(lv.tick_ns // 1_000_000_000)[::60]
    setattr(lv.frame, slot, [{"time": int(t), "value": level} for t in secs])
    return DayLevels(lv.frame), level, q


def _rank_of(lv, fills):
    return {(r.anchor, r.family): r.rank
            for r in level_tag.rank_fills(lv, fills, seed=1)}


def test_random_fills_rank_at_chance():
    """The guard on the method. Fills taken at arbitrary instants, priced off the
    tape, must not look attracted to anything."""
    lv, _ = _session()
    rng = np.random.default_rng(3)
    idx = rng.choice(len(lv.tick_ns), size=60, replace=False)
    fills = [
        Fill(key=f"t{i}", anchor="entry",
             ts_utc=pd.Timestamp(int(lv.tick_ns[j]), tz="UTC"),
             price=float(lv.tick_px[j]))
        for i, j in enumerate(sorted(idx))
    ]
    ranks = [r.rank for r in level_tag.rank_fills(lv, fills, seed=11)]
    assert len(ranks) >= 100, "the value families should all have scored"
    mean = float(np.mean(ranks))
    assert 0.40 < mean < 0.60, f"arbitrary fills ranked {mean:.3f}, expected ~0.5"


def test_fill_on_the_level_ranks_tight():
    """The complement: a fill placed at the VAL, at a moment the tape was there,
    must rank far below chance for ``value_low`` — and must not drag the family
    it isn't near along with it."""
    lv, q = _session()
    near = np.flatnonzero(np.abs(lv.tick_px - q["val"]) <= 0.25)
    assert near.size >= 20, "the fixture must produce fills that sit on the VAL"
    fills = [
        Fill(key=f"v{i}", anchor="entry",
             ts_utc=pd.Timestamp(int(lv.tick_ns[j]), tz="UTC"), price=q["val"])
        for i, j in enumerate(near[:: max(1, near.size // 20)])
    ]
    got = level_tag.rank_fills(lv, fills, seed=5)
    low = np.mean([r.rank for r in got if r.family == "value_low"])
    high = np.mean([r.rank for r in got if r.family == "value_high"])
    assert low < 0.25, f"fills on the VAL ranked {low:.3f} against value_low"
    assert high > low, "being at the VAL must not also read as being at the VAH"


def test_mechanical_exits_are_never_scored():
    """A stop or a trail exit lands where the bracket put it. Scoring it would
    write a reason the trader never had."""
    lv, _ = _session()
    ts = pd.Timestamp(int(lv.tick_ns[1000]), tz="UTC")
    px = float(lv.tick_px[1000])
    fills = [
        Fill(key="a", anchor="exit", ts_utc=ts, price=px, reason="stop"),
        Fill(key="b", anchor="exit", ts_utc=ts, price=px, reason="trail"),
        Fill(key="c", anchor="exit", ts_utc=ts, price=px, reason="manual"),
        Fill(key="d", anchor="entry", ts_utc=ts, price=px, reason=None),
    ]
    keys = {r.key for r in level_tag.rank_fills(lv, fills, seed=2)}
    assert keys == {"c", "d"}, f"scored the wrong fills: {keys}"


def test_absent_families_are_omitted_not_guessed():
    """Only the NY value area exists on this frame. The band and EMA families
    have no members to be near, and must be silent rather than default."""
    lv, _ = _session()
    fills = [Fill(key="x", anchor="entry",
                  ts_utc=pd.Timestamp(int(lv.tick_ns[500]), tz="UTC"),
                  price=float(lv.tick_px[500]))]
    fams = {r.family for r in level_tag.rank_fills(lv, fills, seed=4)}
    assert fams <= {"value_low", "value_mid", "value_high"}
    assert "low_band" not in fams and "trend_ema" not in fams


@pytest.mark.parametrize("family", ("mv_poc_naked", "mv_poc_revisit"))
def test_poc_anchors_are_measured_on_their_own(family):
    """Each re-arm rule is a family of one, and has to stay one.

    Several claims in one test because they are the same mistake seen from
    different sides: fills sitting on a POC anchor must rank against *that*
    family, must not be reported as "traded off VWAP" (folded into
    ``session_mean`` they would be — the session means are collinear with each
    other and these lines are collinear with none of them), and must not be
    answered by the other re-arm rule, which on a real day draws a different
    line and here is absent entirely.
    """
    other = "mv_poc_revisit" if family == "mv_poc_naked" else "mv_poc_naked"
    lv, level, q = _session_with_poc_anchor(family)
    near = np.flatnonzero(np.abs(lv.tick_px - level) <= 0.25)
    assert near.size >= 20, "the fixture must produce fills that sit on the line"
    fills = [
        Fill(key=f"p{i}", anchor="entry",
             ts_utc=pd.Timestamp(int(lv.tick_ns[j]), tz="UTC"), price=level)
        for i, j in enumerate(near[:: max(1, near.size // 20)])
    ]
    got = level_tag.rank_fills(lv, fills, seed=9)
    mine = [r.rank for r in got if r.family == family]
    assert mine, f"the {family} family scored nothing"
    assert float(np.mean(mine)) < 0.25, (
        f"fills on the line ranked {np.mean(mine):.3f} against {family}"
    )
    assert all(r.member == family for r in got if r.family == family)
    assert not any(r.family == other for r in got), (
        f"{other} has no series on this frame and must not be scored"
    )
    assert family not in level_tag.FAMILIES["session_mean"]
    assert level_tag.FAMILY_LABELS[family]


def test_misaligned_tape_is_refused():
    """The roll trap. Fills from a different contract sit hundreds of points off
    a session that is otherwise perfectly healthy."""
    lv, _ = _session()
    ts = pd.Timestamp(int(lv.tick_ns[900]), tz="UTC")
    ok = [Fill(key="k", anchor="entry", ts_utc=ts, price=float(lv.tick_px[900]))]
    wrong = [Fill(key="k", anchor="entry", ts_utc=ts, price=float(lv.tick_px[900]) + 300)]
    assert level_tag.fills_align(lv, ok)
    assert not level_tag.fills_align(lv, wrong)


def test_measuring_does_not_satisfy_the_review_gate():
    """The boundary. Level rows are machine-owned; the sitting still owes answers.

    Asserted against the gate itself rather than against the schema, because the
    schema staying clean is only half of it — what matters is that a trade with
    a full set of measurements still counts as unreviewed.

    Restated 2026-08-20, when the tagger started *offering* the level options,
    and again 2026-08-31 when the gate became level + setup + discipline. That is
    the closest this has come to the forbidden thing, so it is the version most
    worth pinning: a shortlist is not an answer, and a trade measured down to
    the tick still owes all three.
    """
    from api.routers import replays as router

    measured = {"trade_key": "abc", "levels": [
        {"id": "devVP_val", "family": "value_low", "dist_ticks": 1.0, "rank": 0.01},
    ]}
    assert router._unanswered([measured]) == 1, (
        "a measured trade must still owe a human answer"
    )
    assert router._unanswered([{**measured, "watched_levels": ["devVP_val"]}]) == 1, (
        "picking the level is one third of a review, not all of it"
    )
    assert router._unanswered([
        {**measured, "watched_levels": ["devVP_val"], "setup": "faded_rally"}
    ]) == 1, "a level and a setup still owe the discipline call"
    assert router._unanswered([{
        **measured, "watched_levels": ["devVP_val"],
        "setup": "faded_rally", "discipline": "clean",
    }]) == 0


def test_stored_rows_are_replaced_not_merged(tmp_path):
    """A method bump must not leave the previous null's families beside the new
    ones, where they would read as extra evidence."""
    import sqlite3

    from journal import db as dbmod

    conn = sqlite3.connect(tmp_path / "j.db")
    conn.row_factory = sqlite3.Row
    dbmod.init_db(conn)

    old = [level_tag.LevelRank("k1", "entry", f, "m", 0.1, 1.0)
           for f in ("value_low", "low_band", "trend_ema")]
    dbmod.set_trade_levels(conn, old, "v0")
    new = [level_tag.LevelRank("k1", "entry", "value_low", "m", 0.2, 2.0)]
    dbmod.set_trade_levels(conn, new, "v1")

    rows = dbmod.get_trade_levels(conn, "k1")
    assert [r["family"] for r in rows] == ["value_low"]
    assert dbmod.trade_levels_methods(conn) == {"v1": 1}


def test_a_full_recompute_leaves_no_orphans(tmp_path):
    """The other half of the replace rule, and the one a whole-journal backfill
    actually hit: a trade whose key no longer exists is never re-scored, so its
    rows survive a method bump untouched and the table stays mixed forever.
    They have to be pruned by stamp, not by trade."""
    import sqlite3

    from journal import db as dbmod

    conn = sqlite3.connect(tmp_path / "j.db")
    conn.row_factory = sqlite3.Row
    dbmod.init_db(conn)

    gone = [level_tag.LevelRank("orphan", "entry", "value_low", "m", 0.1, 1.0)]
    dbmod.set_trade_levels(conn, gone, "v0")
    live = [level_tag.LevelRank("k1", "entry", "value_low", "m", 0.2, 2.0)]
    dbmod.set_trade_levels(conn, live, "v1")
    assert dbmod.trade_levels_methods(conn) == {"v0": 1, "v1": 1}

    assert dbmod.prune_trade_levels(conn, "v1") == 1
    assert dbmod.trade_levels_methods(conn) == {"v1": 1}
    assert dbmod.get_trade_levels(conn, "orphan") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
