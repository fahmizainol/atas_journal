"""Behavioral edge breakdowns: time-of-day, weekday, hold-time, direction.

Two callers, one set of definitions. The journal's Edges tab cuts the *real* book
(``by_weekday`` and friends, one DataFrame each); a strategy run cuts its
*simulated* one and wants more of it — an R column, the sim-only cuts (which exit
rule closed the trade, how wide the band was), and a read on whether any of the
splits are worth believing. ``cuts()`` is that richer call, and it is built from
the same key functions the plain ones use, so a bucket can never mean one thing
on one page and something else on the other.

The luck column exists for the same reason it does in regime_pnl: a run's trades
split six ways is a lot of buckets over not many trades, and the best-looking one
is the one that got lucky. Every cut is scored against the same statistic computed
on shuffled P&L, so "this weekday split is what noise produces anyway" is
something the table says rather than something you have to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .config import ET_TZ

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOLD_BUCKETS = ["<1m", "1-5m", "5-15m", "15m+"]

# Shuffles behind every luck figure, and the seed that makes it reproducible: the
# same run must not produce a different p-value each time the panel is opened.
PERMUTATIONS = 500
SEED = 0x5EED

# Under this many trades, or with only one bucket populated, a permutation test is
# theatre — there is nothing for a shuffle to disturb.
MIN_TRADES_FOR_LUCK = 12


def _build_session_order() -> list[str]:
    """30-min blocks across the day, but 15-min blocks for the 09:30 open."""
    order: list[str] = []
    for h in range(24):
        for block in (0, 30):
            if h == 9 and block == 30:
                order.extend(["09:30", "09:45"])
            else:
                order.append(f"{h:02d}:{block:02d}")
    return order


SESSION_ORDER = _build_session_order()


def _session_block(ts) -> str:
    """Session start (09:30 ET) splits into 15-min blocks; rest are 30-min."""
    h, m = ts.hour, ts.minute
    if h == 9 and m >= 30:
        return "09:30" if m < 45 else "09:45"
    block = 0 if m < 30 else 30
    return f"{h:02d}:{block:02d}"


def _hold_bucket(seconds: float) -> str:
    if seconds < 60:
        return "<1m"
    if seconds < 300:
        return "1-5m"
    if seconds < 900:
        return "5-15m"
    return "15m+"


def _summarize(grp: pd.DataFrame) -> pd.Series:
    pnl = grp["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n = len(pnl)
    win_rate = len(wins) / n * 100 if n else 0.0
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    expectancy = (len(wins) / n) * avg_win + (len(losses) / n) * avg_loss if n else 0.0
    out = {
        "trades": n,
        "net_pnl": float(pnl.sum()),
        "win_rate": win_rate,
        "expectancy": expectancy,
    }
    # What the bucket paid per trade in units of the risk it took. Dollars can't be
    # compared across configs — a 30-tick stop and a 60-tick stop book different
    # money for the same idea — and R is the whole point of a fixed-stop engine.
    # Only a simulated trade carries it; the journal's book has no r_multiple.
    if "r_multiple" in grp.columns:
        out["avg_r"] = float(grp["r_multiple"].astype(float).mean())
    return pd.Series(out)


def _by(trades: pd.DataFrame, key: pd.Series, order: list | None = None) -> pd.DataFrame:
    df = trades.copy()
    df["_k"] = key
    out = df.groupby("_k", group_keys=False).apply(_summarize).reset_index()
    out = out.rename(columns={"_k": "bucket"})
    if order is not None:
        out["bucket"] = pd.Categorical(out["bucket"], categories=order, ordered=True)
        out = out.sort_values("bucket")
    return out.reset_index(drop=True)


# --- the key functions: what bucket does a trade fall in ---------------------

def _k_hour_local(t: pd.DataFrame) -> pd.Series:
    return t["entry_ts_local"].dt.hour


def _k_hour_et(t: pd.DataFrame) -> pd.Series:
    """US Eastern session blocks: 15-min around the 09:30 open, 30-min otherwise."""
    utc = t["entry_ts_utc"]
    if utc.dt.tz is None:
        utc = utc.dt.tz_localize("UTC")
    return utc.dt.tz_convert(ET_TZ).apply(_session_block)


def _k_weekday(t: pd.DataFrame) -> pd.Series:
    return t["entry_ts_local"].dt.dayofweek.map(dict(enumerate(WEEKDAYS)))


def _k_hold(t: pd.DataFrame) -> pd.Series:
    return t["duration_s"].apply(_hold_bucket)


def _k_direction(t: pd.DataFrame) -> pd.Series:
    return t["direction"]


def _k_exit_reason(t: pd.DataFrame) -> pd.Series:
    return t["exit_reason"]


def _k_gate(t: pd.DataFrame) -> pd.Series:
    return t["gate"]


def _k_band_width(t: pd.DataFrame) -> pd.Series:
    """Quartiles of dev2−dev1 at the entry, labelled with the ticks they hold.

    Quartiles rather than fixed cutoffs because the band is an instrument-and-
    session-dependent width — σ is small right after the open and widens through
    the day, and a threshold that splits NQ sensibly means nothing on ES. The
    printed range is what makes it actionable anyway: the bottom bucket's upper
    edge IS the ``min_band_width_ticks`` you would set to cut it.
    """
    w = t["band_width_ticks"].astype(float)
    q = pd.qcut(w, 4, duplicates="drop")
    # Label with the widths actually in the bucket, not qcut's interval edges: an
    # edge is a number no trade had, and "76–140 ticks" is a filter you can type in.
    lo = w.groupby(q, observed=True).min()
    hi = w.groupby(q, observed=True).max()
    labels = {iv: f"{lo[iv]:.0f}–{hi[iv]:.0f}" for iv in lo.index}
    return q.map(labels)


@dataclass(frozen=True)
class Cut:
    name: str
    label: str
    keys: Callable[[pd.DataFrame], pd.Series]
    # Columns the key function reads. A frame without them can't be cut this way —
    # the journal's book has no exit_reason, a traded frame has no gate.
    needs: tuple[str, ...]
    order: list | None = None
    # None keeps the buckets in the cut's own order; "net" ranks them by what they
    # paid, which is the only sane order for a set of names (exit reasons, gates).
    sort: str | None = None
    # Was the bucket knowable when the trade was entered?
    #
    # This is the same distinction the regime study's checkpoint picker makes, and
    # it decides whether the cut gets a luck column at all. A trade's session block
    # and its band width were facts before the fill: a split across them is one you
    # could have traded, so asking whether it beats a shuffle is a real question.
    # Its exit reason and its hold time were not — they are what the trade DID. A
    # stop is a loss by construction and a stopped trade dies fast, so those cuts
    # separate P&L perfectly no matter what, and a permutation test on them would
    # print "holds" every time while proving nothing at all. They are read as a
    # description of the exits (is the target leaving money behind?), never as a
    # filter, and the table says so instead of scoring them.
    knowable: bool = True


CUTS: tuple[Cut, ...] = (
    Cut("by_hour_et", "Session block (US Eastern)", _k_hour_et,
        ("entry_ts_utc",), order=SESSION_ORDER),
    Cut("by_weekday", "Weekday", _k_weekday, ("entry_ts_local",), order=WEEKDAYS),
    Cut("by_direction", "Long vs Short", _k_direction, ("direction",),
        order=["Long", "Short"]),
    Cut("by_band_width", "Band width at entry (ticks)", _k_band_width,
        ("band_width_ticks",)),
    Cut("by_hold_time", "Hold time", _k_hold, ("duration_s",), order=HOLD_BUCKETS,
        knowable=False),
    Cut("by_exit_reason", "Exit reason", _k_exit_reason, ("exit_reason",), sort="net",
        knowable=False),
    Cut("by_gate", "Which gate vetoed it", _k_gate, ("gate",), sort="net"),
)

BY_NAME: dict[str, Cut] = {c.name: c for c in CUTS}


# --- the plain per-cut frames (the journal's Edges tab) -----------------------

def _frame(trades: pd.DataFrame, name: str) -> pd.DataFrame:
    cut = BY_NAME[name]
    if trades is None or trades.empty or any(c not in trades.columns for c in cut.needs):
        return pd.DataFrame()
    out = _by(trades, cut.keys(trades), order=cut.order)
    if cut.sort == "net":
        out = out.sort_values("net_pnl", ascending=False).reset_index(drop=True)
    return out


def by_hour_kl(trades: pd.DataFrame) -> pd.DataFrame:
    """Hour of the day in the *display* zone — journal-only. A sim's timestamps are
    always ET, so for a run this would just restate the session blocks."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    return _by(trades, _k_hour_local(trades), order=list(range(24)))


def by_hour_et(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_hour_et")


def by_weekday(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_weekday")


def by_hold_time(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_hold_time")


def by_direction(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_direction")


def by_exit_reason(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_exit_reason")


def by_band_width(trades: pd.DataFrame) -> pd.DataFrame:
    return _frame(trades, "by_band_width")


# --- is the split worth believing? -------------------------------------------

def _between_group_ss(codes: np.ndarray, y: np.ndarray, g: int) -> float:
    """How much of the P&L's spread sits *between* the buckets rather than inside
    them — the one-way ANOVA numerator, and the natural "does this cut separate
    anything at all" statistic for a categorical split.

    Written as sums rather than means so a permutation costs one bincount.
    """
    sums = np.bincount(codes, weights=y, minlength=g)
    ns = np.bincount(codes, minlength=g).astype("float64")
    ns[ns == 0] = np.inf  # an empty bucket contributes nothing, and never divides by 0
    return float((sums ** 2 / ns).sum() - y.sum() ** 2 / len(y))


def luck(trades: pd.DataFrame, name: str) -> float | None:
    """How often shuffling the P&L across the trades separates the buckets this
    well. Low means the split is more than the cut's own shape hands you for free.

    Not a p-value to quote in a paper: no correction (``luck_bar`` is the caller's
    job), and trades within a session are not independent draws. It is the honest
    floor — a cut that a coin-flip reproduces half the time is not a finding, and
    without this column the biggest number in the table always looks like one.

    None when there is nothing to test: an outcome cut (see Cut.knowable), too few
    trades, or one bucket holding everything.
    """
    cut = BY_NAME[name]
    if (trades is None or trades.empty
            or not cut.knowable
            or any(c not in trades.columns for c in cut.needs)
            or len(trades) < MIN_TRADES_FOR_LUCK):
        return None

    codes, uniques = pd.factorize(cut.keys(trades), use_na_sentinel=False)
    g = len(uniques)
    if g < 2:
        return None

    y = trades["net_pnl"].astype("float64").to_numpy()
    if y.std() == 0:
        return None
    obs = _between_group_ss(codes, y, g)

    rng = np.random.default_rng(SEED)
    beat = sum(_between_group_ss(codes, rng.permutation(y), g) >= obs
               for _ in range(PERMUTATIONS))
    # (beat + 1) / (n + 1): the observation is itself one of the arrangements, so a
    # test that never beats it reports 1/501 rather than a flat zero it can't know.
    return (beat + 1) / (PERMUTATIONS + 1)


def luck_bar(names: tuple[str, ...]) -> float:
    """With this many cuts tested at once, one of them clearing a 1-in-20 bar is
    what noise hands you for free. Bonferroni — blunt, and it errs the safe way.
    Same bar, same reasoning as the regime leaderboard's.

    Counts only the cuts that are actually tested: the outcome cuts are never
    scored, so charging the family for them would tighten the bar for nothing.
    """
    tested = sum(1 for n in names if BY_NAME[n].knowable)
    return 0.05 / max(1, tested)


def cuts(trades: pd.DataFrame, names: tuple[str, ...],
         with_luck: bool = True) -> dict[str, dict]:
    """Every named cut of one book, each with its own read on whether it means
    anything. The rows are the same records the plain frames produce."""
    bar = luck_bar(names)
    out: dict[str, dict] = {}
    for name in names:
        cut = BY_NAME[name]
        p = luck(trades, name) if with_luck else None
        out[name] = {
            "name": name,
            "label": cut.label,
            "knowable": cut.knowable,
            "frame": _frame(trades, name),
            "luck": p,
            "holds": p is not None and p <= bar,
        }
    return out
