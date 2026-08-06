"""Run a registered strategy over a *partial* session.

Shadow mode's job is to answer one question honestly: would this strategy have
signalled, right now, on today's tape? The only answer worth having is the one
the backtest would give, so this module does not reimplement anything. It hands
the day-so-far to the same ``run_session`` the registry already points at, with
``partial=True``, and returns what that returns.

WHY A RE-RUN AND NOT AN INCREMENTAL STEP. An incremental ``step(tick, state)``
per strategy would be cheaper, and it would be a second implementation of the
entry rules. This repo has already ruled twice that a second implementation is
the thing to avoid — ``journal.replays`` keeps what the browser computed rather
than re-deriving it ("a second implementation on this side could disagree with
it about a fill"), and the run/replay API says the same. A re-run cannot
disagree with the backtest, because it *is* the backtest. That matters more than
usual here: the strategies on the shelf are marginal enough that one bar's
difference in an acceptance rule flips the sign of an edge.

THE CONTRACT THIS RELIES ON. A prefix run must reproduce the prefix of the full
run. That holds because every array the engine precomputes is cumulative — the
VWAP bands are three ``cumsum``s, the developing profile accumulates per closed
bar, ``levels_in_force`` reports the last bar to have closed strictly before the
tick, and the bars themselves drop the trailing partial. Nothing reads a session
extreme, a day range or an end-of-day value. It is a real property of the code,
not an aspiration — and ``tests/test_prefix_replay.py`` is what keeps it true.

The one place it did not hold was ``force_i``, the last tick at which a position
may still be held. On a truncated frame that collapsed onto the newest tick and
force-flattened every open position with reason "time". ``engine._force_index``
is the fix; see its docstring for why ``partial`` and not the data is what tells
the two cases apart.

WHAT THIS MODULE DOES NOT DO. It does not fetch. The caller owns the frame —
a live recorder holds the day-so-far in memory, and re-reading a cache file
would only produce the settled vendor copy of a session that has not settled.
It does not decide *when* to re-run either; state changes on bar closes, so a
caller that re-runs on every tick is only burning CPU.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import registry


def shadow_session(
    slug: str, cfg, day: date, frame: pd.DataFrame,
    regime: dict | None = None, partial: bool = True,
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Run ``slug`` over ``frame`` as a session in progress.

    ``frame`` is the day so far, carrying what ``ticks.get_day_ticks`` returns
    and already sliced to the window the strategy's ``session`` declares. That
    slicing is the caller's job and it is load-bearing: the READ CONTRACT in
    ``ticks.py`` applies here unchanged, so a frame that quietly carries the
    overnight in front of an RTH strategy re-phases every bar and moves the VWAP
    anchor to 18:00 — silently, with no error.

    ``regime`` is the session's regime artifact if the caller has one. Left None
    the gates read the cache as they always have, which is right for a settled
    day and wrong for a live one — ``get_regime`` computes from the whole cached
    day, and a session in progress has no such file, so every regime gate would
    blind-fail-closed and the strategy would simply stop signalling after its
    checkpoint. A live caller must supply this or accept that silence.

    ``partial`` is what tells the engine whether more ticks are coming, and it is
    the discriminator ``_force_index`` needs — a complete frame ending at the
    bell and a truncated one both land on the last tick, and only the caller
    knows which it is holding. Live callers leave it True. Phase 6's
    reconciliation is the one place that passes False over a live tape: comparing
    the day's prefix runs against a *settled* run of the same day means the
    settled one has to be told the day is over, or every position still open at
    the last tick would be force-flattened as "time" and the comparison would be
    against a run nobody would ever have made.

    Returns exactly what the strategy's own entry point returns:
    ``(trades, vetoed, bars, bands)``.
    """
    strat = registry.get(slug)
    return strat.run_session(cfg, day, frame=frame, partial=partial, regime=regime)
