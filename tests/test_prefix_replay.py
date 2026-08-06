"""The prefix-replay contract: a partial run reproduces the prefix of a full one.

Shadow mode re-invokes the *same* ``run_session`` the backtest uses, handing it
the day so far. That is only honest if the engine is genuinely causal — if
running it over the first four hours of a session yields exactly the trades the
full run reports for those four hours, and nothing else. This file is what keeps
that true.

It is not a formality. The engine precomputes full-length indicator arrays and
then walks them, so "causal" is a property of every one of those arrays being
cumulative, not something the loop structure enforces. One array that peeked at
a session extreme would make shadow mode quietly optimistic, and the strategies
on the shelf are marginal enough that a single flipped signal changes the sign
of an edge.

The known break was ``force_i``, the last tick a position may still be held. On
a truncated frame it collapsed onto the newest tick, so every open position was
force-flattened with reason "time" and entries were blocked there too. See
``engine._force_index``.

Runs over the real cached session — a synthetic tape can't produce a realistic
VWAP band or value area, and those are exactly what a lookahead would hide in.
Skips if the tick cache is cold.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.config import ET_TZ  # noqa: E402
from journal.sim import live_shadow, registry, ticks  # noqa: E402

# Two sessions, because one is not enough: 2025-10-13 is where twelve of the
# thirteen strategies trade, and 2025-10-07 is where the weekly traverse does.
# The traverse is the one with its own bespoke force_i line, so leaving it
# untraded would leave that line untested.
DAYS = (date(2025, 10, 13), date(2025, 10, 7))
CONTRACT = "NQZ5"

# Cut points through the session. 10:30 lands while the morning setups are still
# arming, 15:30 after most have resolved — between them they cover a position
# open at the cut, a position closed before it, and the gates' 09:45/10:30
# checkpoints on both sides.
CUTS = (time(10, 30), time(12, 0), time(14, 0), time(15, 30))


def _have_ticks(day: date) -> bool:
    return ticks.has_rth(CONTRACT, day)


def _frame(strat, cfg, day: date) -> pd.DataFrame:
    """The frame the strategy's own entry point would load.

    ``session`` is the registry's declaration of which segments the idea reads,
    and it is exactly what decides ``include_overnight`` inside the engine — so
    reading it here is reproducing the engine's choice, not guessing at it.
    """
    return ticks.get_day_ticks(
        ticks.contract_for(cfg.contract, day), day,
        include_overnight=(strat.session == "globex"))


def _cut(frame: pd.DataFrame, day: date, at: time) -> pd.DataFrame:
    """Everything strictly before ``at`` ET, re-indexed from 0.

    The reset matters: the engine treats the frame's index positionally (bar
    boundaries, ``entry_idx``), and ``ticks._slice_window`` hands it a 0-based
    frame, so a prefix that kept the parent's index would not be the same input.
    """
    edge = pd.Timestamp(datetime.combine(day, at), tz=ET_TZ)
    keep = (frame["ts_utc"] < edge).to_numpy()
    return frame[keep].reset_index(drop=True)


def _rows_equal(a: list[dict], b: list[dict]) -> bool:
    """Row-wise equality that treats NaN as equal to NaN (excursion fields are
    NaN whenever a trade never crossed back)."""
    def eq(x, y):
        return (x != x and y != y) or x == y
    return len(a) == len(b) and all(
        r.keys() == q.keys() and all(eq(r[k], q[k]) for k in r)
        for r, q in zip(a, b))


def _midtrade_cuts(full_trades: list[dict], limit: int = 3) -> list[int]:
    """Tick indices that land strictly inside a trade.

    Wall-clock cuts alone are weak: whether they catch a lookahead depends on
    whether a position happened to be open at 12:00, and for most strategies on
    most days one isn't. These cuts are derived from the full run, so every
    strategy that trades at all gets its open-position case exercised — which is
    exactly where ``force_i`` used to force a phantom "time" exit.
    """
    cuts = []
    for tr in full_trades[:limit]:
        lo, hi = int(tr["entry_idx"]), int(tr["exit_idx"])
        if hi - lo >= 2:
            cuts.append((lo + hi) // 2)
    return cuts


@pytest.mark.parametrize("day", DAYS, ids=[d.isoformat() for d in DAYS])
@pytest.mark.parametrize("slug", sorted(registry.STRATEGIES))
def test_partial_run_is_a_prefix_of_the_full_run(slug: str, day: date):
    if not _have_ticks(day):
        pytest.skip("tick cache cold")
    strat = registry.get(slug)
    cfg = strat.config_cls(contract=CONTRACT)
    frame = _frame(strat, cfg, day)
    assert frame is not None and not frame.empty

    full_trades, full_vetoed, _, _ = strat.run_session(cfg, day, frame=frame)

    def check(part: pd.DataFrame, label: str):
        trades, vetoed, _, _ = live_shadow.shadow_session(slug, cfg, day, part)

        # A partial run may report FEWER trades — one still open at the cut has
        # no exit yet, and one that hadn't entered yet obviously has nothing to
        # report. What it may never do is report a different trade, or report
        # more of them than the whole session produced.
        assert len(trades) <= len(full_trades), (
            f"{slug} @ {label}: partial run invented trades "
            f"({len(trades)} > {len(full_trades)})")
        assert _rows_equal(trades, full_trades[:len(trades)]), (
            f"{slug} @ {label}: partial trades diverge from the full run's prefix")

        # The ghosts ride the same rules to the same would-be exits, so they owe
        # the same guarantee — and they are where a gate reading post-hoc state
        # would show up first.
        assert len(vetoed) <= len(full_vetoed)
        assert _rows_equal(vetoed, full_vetoed[:len(vetoed)]), (
            f"{slug} @ {label}: partial ghosts diverge from the full run's prefix")
        return trades

    for at in CUTS:
        part = _cut(frame, day, at)
        if not part.empty:
            check(part, str(at))

    # Cut inside a trade: the position is open at the cut, so it must simply not
    # be reported — not closed early at the newest tick. Exactly the trades that
    # had already exited, and no others.
    for k in _midtrade_cuts(full_trades):
        trades = check(frame.iloc[:k].reset_index(drop=True), f"tick {k}")
        closed = sum(1 for tr in full_trades if int(tr["exit_idx"]) < k)
        assert len(trades) == closed, (
            f"{slug} @ tick {k}: reported {len(trades)} trades, but {closed} had "
            f"closed by then — an open position was force-flattened at the cut")


@pytest.mark.parametrize("slug", sorted(registry.STRATEGIES))
def test_supplying_the_frame_changes_nothing(slug: str):
    """The ``frame`` seam must be transparent.

    A frame handed in has to produce byte-identical results to the same frame
    fetched from cache — otherwise every number shadow mode reports is measured
    against a different engine than the backtest's, and the comparison that
    justifies the whole feature is meaningless.
    """
    day = DAYS[0]
    if not _have_ticks(day):
        pytest.skip("tick cache cold")
    strat = registry.get(slug)
    cfg = strat.config_cls(contract=CONTRACT)

    from_cache = strat.run_session(cfg, day)
    injected = strat.run_session(cfg, day, frame=_frame(strat, cfg, day))

    assert _rows_equal(injected[0], from_cache[0]), f"{slug}: trades differ"
    assert _rows_equal(injected[1], from_cache[1]), f"{slug}: ghosts differ"


def test_force_index_holds_its_answer_on_a_settled_frame():
    """``partial`` is the discriminator, not the data.

    A complete RTH frame whose flat_by sits at or past the bell also lands on the
    final tick — and there the bell really is the force point. Only the caller
    knows whether more ticks are coming, so only the caller's flag may change the
    answer.
    """
    import numpy as np

    from journal.sim.engine import _force_index

    # flat_by lands mid-frame: the answer is the same either way, because the
    # frame demonstrably continues past it.
    holdable = np.array([0, 1, 2, 3])
    assert _force_index(holdable, 10, partial=False) == 3
    assert _force_index(holdable, 10, partial=True) == 3

    # flat_by is at or past the end of the data: settled means the bell,
    # partial means "not yet".
    holdable = np.array([0, 1, 2, 3])
    assert _force_index(holdable, 4, partial=False) == 3
    assert _force_index(holdable, 4, partial=True) == float("inf")

    # Nothing holdable at all still degrades to the old fallback.
    assert _force_index(np.array([], dtype=int), 10, partial=False) == 9
