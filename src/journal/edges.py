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


def confluence_breakdown(vetoed: pd.DataFrame) -> pd.DataFrame:
    """Per-confluence veto stats over the ghost ledger, counting overlaps.

    The ``by_gate`` cut partitions the vetoed book by the *first* gate that
    rejected each entry — so a gate late in the check order only gets credit for
    the entries the earlier ones let through, and you can't read what any single
    stacked confluence is actually doing. This does not partition: each vetoed
    entry carries the FULL set of gates that rejected it (``gates``, pipe-joined),
    and here every gate in that set is scored on the entry. A trade two gates both
    blocked counts once under EACH, so the ``trades`` column sums to more than the
    ghost total — that overlap is the answer to "what does each gate independently
    veto".

    Columns mirror the cut frames (trades/net_pnl/win_rate/expectancy/avg_r; the
    bucket is the confluence name) with one addition: ``unique`` — how many of a
    gate's vetoes it caught *alone*, i.e. entries that would have traded had only
    that gate been switched off. A high ``unique`` is a gate pulling its own
    weight; a low one is redundant with the rest of the stack.

    A vetoed row's ``net_pnl`` is the would-be ghost P&L: positive means the gate
    cut a *winner*, negative means it saved the loss. Empty in -> empty out. Older
    ledgers predate the full set and only carry the first-match ``gate``; the
    breakdown falls back to it, which is exact whenever one gate was enabled and a
    first-match approximation when several were.
    """
    if vetoed is None or vetoed.empty:
        return pd.DataFrame()
    col = "gates" if "gates" in vetoed.columns else "gate"
    sets = [
        [g for g in str(s or "").split("|") if g]
        for s in vetoed[col].tolist()
    ]
    # Explode positionally: one row per (vetoed entry × gate that rejected it). A
    # fresh RangeIndex on both the repeated frame and the confluence key keeps the
    # groupby from aligning on duplicate labels.
    reps = [len(gs) for gs in sets]
    exploded = vetoed.iloc[np.repeat(np.arange(len(vetoed)), reps)].reset_index(drop=True)
    if exploded.empty:
        return pd.DataFrame()
    conf = pd.Series([g for gs in sets for g in gs], name="_conf")
    out = _by(exploded, conf)
    # Entries a gate caught alone: it, and nothing else, stood between them and a
    # real fill.
    unique: dict[str, int] = {}
    for gs in sets:
        if len(gs) == 1:
            unique[gs[0]] = unique.get(gs[0], 0) + 1
    out["unique"] = out["bucket"].map(lambda b: unique.get(b, 0)).astype(int)
    return out.sort_values("trades", ascending=False).reset_index(drop=True)


# --- MFE / MAE: what the trade was worth vs. what it booked --------------------

EXCURSION_GROUPS = ("All", "Winners", "Losers")


def excursions(trades: pd.DataFrame) -> pd.DataFrame:
    """The maximum-favorable / maximum-adverse profile of a book, split by outcome.

    Every simulated trade carries how far it ever ran in its own favor before it
    was booked (``mfe_r``, in R) and the deepest it went against (``mae_r``, <= 0) —
    both measured off the ticks over the trade's own life, by the engine. This rolls
    them up three ways: the whole book, the winners, and the losers. What the split
    answers is not "which bucket to trade" (MFE/MAE are outcomes, unknowable at the
    fill) but "is the exit fit for the trade it's exiting":

      - ``mfe_r`` / ``mae_r`` — the median peak and trough in R. Winners that peak
        far above what they book, and losers that never peak at all, are two
        different exit problems.
      - ``capture`` — of the peak a trade showed, the median fraction the exit
        actually kept (booked R / peak R), over trades that were ever in profit. Low
        capture is a trail or target giving open profit back.
      - ``reach_1r`` — share of the group that ever reached +1R MFE. Losers that
        never reach it were never really working; a breakeven rule can't save them.
      - ``heat_1r`` — share that sat through <= -1R MAE. Winners taking that heat
        are surviving on luck the stop should have cut.

    Empty (no trades, or a pre-MFE/MAE run whose frame lacks the columns) -> empty
    out, and the panel says the run predates the tracking rather than drawing zeros.
    """
    need = ("mfe_r", "mae_r", "net_pnl", "points", "mfe_points")
    if trades is None or trades.empty or any(c not in trades.columns for c in need):
        return pd.DataFrame()
    pnl = trades["net_pnl"].astype(float)
    groups = {"All": trades, "Winners": trades[pnl > 0], "Losers": trades[pnl <= 0]}
    rows = []
    for name in EXCURSION_GROUPS:
        g = groups[name]
        if g.empty:
            continue
        mfe_r = g["mfe_r"].astype(float)
        mae_r = g["mae_r"].astype(float)
        # Capture — booked R as a fraction of peak R — only reads on trades that
        # booked a profit: a loser's "fraction of its peak kept" is a negative over a
        # near-zero peak, a meaningless blow-up, not a 0.4-of-peak the way a winner's
        # is. So it is the median over the group's *winning* trades (and is therefore
        # null for the Losers row, which has none) — the honest question is "when this
        # group won, how much of the run did the exit keep".
        won = g[(g["points"].astype(float) > 0) & (g["mfe_points"].astype(float) > 0)]
        capture = (float((won["points"].astype(float)
                          / won["mfe_points"].astype(float)).median())
                   if not won.empty else float("nan"))
        rows.append({
            "bucket": name,
            "trades": int(len(g)),
            "mfe_r": float(mfe_r.median()),
            "mae_r": float(mae_r.median()),
            "capture": capture,
            # Ever in profit at all — mfe_r > 0. The weaker sibling of reach_1r, and
            # the one that keeps reach_1r honest: a Losers row can read 0% reached
            # +1R while nearly all of them were briefly green and gave it back, which
            # is a give-back problem, not a "never worked" one.
            "ever_green": float((mfe_r > 0).mean()),
            "reach_1r": float((mfe_r >= 1.0).mean()),
            "heat_1r": float((mae_r <= -1.0).mean()),
        })
    return pd.DataFrame(rows)


# --- how far the losers ever got: give-back vs never-worked -------------------

# Half-open (lo, hi] on peak MFE in R. "Never green" is mfe_r <= 0 — a trade that
# was never once above its entry, a loss from the tick. The rest were green and
# turned; how far they got says whether an earlier exit could have saved them.
LOSER_GIVEBACK_BINS: tuple[tuple[str, float | None, float | None], ...] = (
    ("Never green", None, 0.0),
    ("0 to 0.5R", 0.0, 0.5),
    ("0.5 to 1R", 0.5, 1.0),
    ("1R+", 1.0, None),
)


def loser_giveback(trades: pd.DataFrame) -> dict:
    """The losers split by the best they ever showed — the answer to "how many of
    these were ever in profit, and by how much".

    A loser that was never green (``mfe_r <= 0``) was wrong from the fill and no
    exit rule reaches it; a loser that ran to +0.8R and came back is a give-back the
    exit could in principle have caught. The split is the evidence for or against a
    breakeven/partial rule: if the losers cluster in "never green" there is nothing
    to protect, and if they cluster past +0.5R there is. ``net_pnl`` per bucket is
    the P&L those losers cost, so the give-back buckets show what the leak is worth.

    Losers is ``net <= 0`` to match ``excursions``. Empty (no trades, no losers, or
    a run predating ``mfe_r``) -> empty dict / zero-loser dict."""
    need = ("mfe_r", "net_pnl")
    if trades is None or trades.empty or any(c not in trades.columns for c in need):
        return {}
    pnl = trades["net_pnl"].astype(float)
    losers = trades[pnl <= 0]
    if losers.empty:
        return {"losers": 0, "ever_green": float("nan"), "buckets": []}
    mfe = losers["mfe_r"].astype(float).to_numpy()
    lp = losers["net_pnl"].astype(float).to_numpy()
    buckets = []
    for label, lo, hi in LOSER_GIVEBACK_BINS:
        if lo is None:
            m = mfe <= hi
        elif hi is None:
            m = mfe > lo
        else:
            m = (mfe > lo) & (mfe <= hi)
        buckets.append({
            "bucket": label,
            "trades": int(m.sum()),
            "share": float(m.mean()),
            "net_pnl": float(lp[m].sum()),
        })
    return {
        "losers": int(len(losers)),
        "ever_green": float((mfe > 0).mean()),
        "buckets": buckets,
    }


# --- how much heat the winners took: clean entry vs survived-on-luck ----------

# Half-open (lo, hi] on heat = -mae_r (>= 0, in R). "No heat" is a winner that never
# went underwater — a clean entry that worked from the fill. The rest took heat
# before they turned; how deep says whether the stop was doing real work or the
# trade was one tick from being cut. Bin bounds are the heat magnitude; the labels
# carry the minus sign because MAE is adverse (matching the signed MAE column), so
# these read negative where the losers' favorable give-back buckets read positive.
WINNER_HEAT_BINS: tuple[tuple[str, float | None, float | None], ...] = (
    ("No heat", None, 0.0),
    ("0 to −0.5R", 0.0, 0.5),
    ("−0.5 to −1R", 0.5, 1.0),
    ("< −1R", 1.0, None),
)


def winner_heat(trades: pd.DataFrame) -> dict:
    """The winners split by the worst heat they ever sat through before booking —
    the mirror of :func:`loser_giveback`.

    A winner that took no heat (``mae_r == 0``) worked from the fill; one that ran to
    -0.8R and came back won on an entry the stop nearly cut. The split reads on stop
    placement and entry timing: winners bunched in "No heat" are clean entries, while
    a wall out at "0.5 to 1R" says the green is being made by trades a tighter stop
    would have killed — an edge surviving on room, not timing. ``net_pnl`` per bucket
    is the P&L that slice of winners made, so the deep-heat buckets show how much of
    the book is riding on that room. (The stop caps heat near -1R, so "1R+" is
    normally empty — its mirror is losers' empty "1R+" favorable under the target.)

    Winners is ``net > 0`` to match :func:`excursions`. Empty (no trades, no winners,
    or a run predating ``mae_r``) -> empty dict / zero-winner dict."""
    need = ("mae_r", "net_pnl")
    if trades is None or trades.empty or any(c not in trades.columns for c in need):
        return {}
    pnl = trades["net_pnl"].astype(float)
    winners = trades[pnl > 0]
    if winners.empty:
        return {"winners": 0, "took_heat": float("nan"), "buckets": []}
    heat = (-winners["mae_r"].astype(float)).to_numpy()  # adverse is <= 0, so heat >= 0
    wp = winners["net_pnl"].astype(float).to_numpy()
    buckets = []
    for label, lo, hi in WINNER_HEAT_BINS:
        if lo is None:
            m = heat <= hi
        elif hi is None:
            m = heat > lo
        else:
            m = (heat > lo) & (heat <= hi)
        buckets.append({
            "bucket": label,
            "trades": int(m.sum()),
            "share": float(m.mean()),
            "net_pnl": float(wp[m].sum()),
        })
    return {
        "winners": int(len(winners)),
        "took_heat": float((heat > 0).mean()),
        "buckets": buckets,
    }


# --- how fast the underwater winners climbed back ----------------------------

# Half-open (lo, hi] on recovery seconds — the time from a winner's deepest heat
# back to breakeven. Buckets span seconds to minutes because a bounce that recovers
# in ten seconds and one that sits red for five minutes are different trades even
# when both end green.
RECOVERY_BINS: tuple[tuple[str, float | None, float | None], ...] = (
    ("< 30s", None, 30.0),
    ("30s to 2m", 30.0, 120.0),
    ("2 to 5m", 120.0, 300.0),
    ("5m+", 300.0, None),
)


def winner_recovery(trades: pd.DataFrame) -> dict:
    """Of the winners that went underwater, how long they took to climb back.

    ``recovery_s`` is measured by the engine: the seconds from a trade's deepest
    adverse tick to the first tick back at breakeven. This reads it over the winners
    that actually took heat (``mae_r < 0``) — a winner that recovered in ten seconds
    barely wobbled, while one that sat red for minutes was a genuine drawdown that
    happened to come back, and only the second is the kind a tighter time-stop or a
    nervier hand would have bailed out of early. ``net_pnl`` per bucket is the P&L in
    the slow-recovery buckets — the green that only exists because the trade was held
    through the red.

    Needs the engine's ``recovery_s`` (runs predating it -> empty dict). Zero-winner
    or no-underwater-winner books -> zero dict."""
    need = ("recovery_s", "mae_r", "net_pnl")
    if trades is None or trades.empty or any(c not in trades.columns for c in need):
        return {}
    pnl = trades["net_pnl"].astype(float)
    mae = trades["mae_r"].astype(float)
    w = trades[(pnl > 0) & (mae < 0) & trades["recovery_s"].notna()]
    if w.empty:
        return {"winners": 0, "median_recovery_s": float("nan"), "buckets": []}
    rec = w["recovery_s"].astype(float).to_numpy()
    wp = w["net_pnl"].astype(float).to_numpy()
    buckets = []
    for label, lo, hi in RECOVERY_BINS:
        if lo is None:
            m = rec <= hi
        elif hi is None:
            m = rec > lo
        else:
            m = (rec > lo) & (rec <= hi)
        buckets.append({
            "bucket": label,
            "trades": int(m.sum()),
            "share": float(m.mean()),
            "net_pnl": float(wp[m].sum()),
        })
    return {
        "winners": int(len(w)),
        "median_recovery_s": float(np.median(rec)),
        "buckets": buckets,
    }


# --- winners vs losers: the distribution the bucket cuts average away ----------

WIN_LOSS_GROUPS = ("Winners", "Losers")


def _max_streaks(won: np.ndarray) -> tuple[int, int]:
    """Longest run of wins and longest run of losses, read over the frame's own
    order — which is the order the engine booked them, i.e. chronological. A
    single pass; a win resets the loss counter and vice versa."""
    max_w = max_l = cur_w = cur_l = 0
    for w in won:
        if w:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        else:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
    return max_w, max_l


def win_loss_profile(trades: pd.DataFrame) -> dict:
    """The winner/loser distribution the per-bucket cuts blend together.

    Every cut in ``CUTS`` mixes winners and losers inside each bucket and reports
    the net — so a bucket that made money by winning big and a bucket that made the
    same money by rarely losing read identically. This splits the book the one way
    those cuts never do, into what won and what lost, and reports the shape of each
    side rather than its middle:

      - the tails (``best_r``/``best_pnl`` — the single biggest trade on that side,
        and ``top3_share`` — the fraction of the side's P&L its three most extreme
        trades carried). A book whose winners' top-3 carry most of the green is one
        outlier away from flat, and no median shows that.
      - the payoff geometry: ``avg_r``/``med_r``/``std_r`` per side, and at the book
        level ``payoff_ratio`` (avg win R over avg loss R) and ``profit_factor``
        (gross won over gross lost) — the two numbers a win rate hides.
      - ``med_hold_s`` per side: whether winners are given room to run while losers
        are cut fast, or the reverse (the signature of exiting winners early).
      - the sequence: ``max_win_streak``/``max_loss_streak`` and ``max_drawdown``
        (deepest peak-to-trough of cumulative net over the booked order) — the run
        of red a win rate averages out but an account has to sit through.

    Losers is ``net <= 0`` to match ``excursions``: a scratch is not a win. Empty
    (no trades, or a run predating ``r_multiple``) -> empty dict, and the panel
    hides the table rather than drawing zeros.
    """
    need = ("net_pnl", "r_multiple", "duration_s")
    if trades is None or trades.empty or any(c not in trades.columns for c in need):
        return {}
    pnl = trades["net_pnl"].astype(float)
    r = trades["r_multiple"].astype(float)
    hold = trades["duration_s"].astype(float)
    n = len(trades)

    sides = []
    for name in WIN_LOSS_GROUPS:
        mask = pnl > 0 if name == "Winners" else pnl <= 0
        gp, gr, gh = pnl[mask], r[mask], hold[mask]
        k = len(gp)
        if k == 0:
            continue
        # "Biggest" reads in the side's own direction — the fattest win, the deepest
        # loss — so a Losers row shows -$250 not the smallest scratch near zero.
        extreme = float(gr.max() if name == "Winners" else gr.min())
        extreme_pnl = float(gp.max() if name == "Winners" else gp.min())
        # Concentration: the three most extreme trades' P&L over the side's total.
        # Both numerator and total share the side's sign, so the fraction is positive
        # on either row; near 1.0 means three trades are the whole side.
        total = gp.sum()
        top3 = float(gp.loc[gp.abs().nlargest(3).index].sum() / total) if total else float("nan")
        sides.append({
            "bucket": name,
            "trades": k,
            "share": k / n,
            "net_pnl": float(gp.sum()),
            "avg_pnl": float(gp.mean()),
            "avg_r": float(gr.mean()),
            "med_r": float(gr.median()),
            "std_r": float(gr.std(ddof=0)),
            "best_r": extreme,
            "best_pnl": extreme_pnl,
            "top3_share": top3,
            "med_hold_s": float(gh.median()),
        })

    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())  # a positive magnitude
    avg_win_r = float(r[pnl > 0].mean()) if len(wins) else 0.0
    avg_loss_r = float(r[pnl <= 0].mean()) if len(losses) else 0.0
    equity = pnl.to_numpy().cumsum()
    drawdown = equity - np.maximum.accumulate(equity)  # <= 0 at every point
    max_w, max_l = _max_streaks((pnl > 0).to_numpy())
    summary = {
        # inf when a side is empty — sanitize() sends "inf", the panel renders ∞,
        # which is the honest reading of "won with nothing lost against it".
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "payoff_ratio": avg_win_r / abs(avg_loss_r) if avg_loss_r else float("inf"),
        "expectancy_r": float(r.mean()),
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
        "max_drawdown": float(drawdown.min()) if n else 0.0,
    }
    return {"sides": sides, "summary": summary}


# --- the R-outcome distribution: the shape a mean/median can't show ------------

# Half-open (lo, hi] so a stop at exactly -1R lands in the first bucket and a
# target at exactly +2R in "1 to 2R" — the two spikes a fixed-stop engine makes.
R_BINS: tuple[tuple[str, float | None, float | None], ...] = (
    ("≤ -1R", None, -1.0),
    ("-1 to 0R", -1.0, 0.0),
    ("0 to 1R", 0.0, 1.0),
    ("1 to 2R", 1.0, 2.0),
    ("2 to 3R", 2.0, 3.0),
    ("> 3R", 3.0, None),
)


def r_histogram(trades: pd.DataFrame) -> list[dict]:
    """Every trade's booked R dropped into fixed R-buckets — the distribution the
    win/loss summary averages into two numbers. On a fixed-stop, fixed-target
    engine this is the most honest single picture of the book: a wall at ``≤ -1R``
    (the stops), a spike at the target bucket, and whatever tail there is past it.
    ``net_pnl`` per bucket says which part of the shape actually holds the money.

    Empty (no trades, or a run with no ``r_multiple``) -> empty list."""
    if trades is None or trades.empty or "r_multiple" not in trades.columns:
        return []
    r = trades["r_multiple"].astype(float).to_numpy()
    pnl = trades["net_pnl"].astype(float).to_numpy()
    rows = []
    for label, lo, hi in R_BINS:
        if lo is None:
            m = r <= hi
        elif hi is None:
            m = r > lo
        else:
            m = (r > lo) & (r <= hi)
        rows.append({
            "bucket": label,
            "trades": int(m.sum()),
            "share": float(m.mean()),
            "net_pnl": float(pnl[m].sum()),
        })
    return rows


# --- what separated winners from losers at entry ------------------------------

@dataclass(frozen=True)
class Discriminator:
    key: str
    label: str
    unit: str
    # Columns the value reads — a frame without them just drops the row.
    needs: tuple[str, ...]
    value: Callable[[pd.DataFrame], pd.Series]


# Every field here was a fact *before* the fill, so a gap between what winners and
# losers carried is a filter you could have traded — the point the outcome cuts
# can't make. Stop distance and time-at-band are the two nothing else on the page
# measures; band width restates the by_band_width cut as a W/L contrast, and size
# asks whether the engine bet bigger into worse.
DISCRIMINATORS: tuple[Discriminator, ...] = (
    Discriminator("band_width", "Band width", "ticks", ("band_width_ticks",),
                  lambda t: t["band_width_ticks"].astype(float)),
    Discriminator("stop_distance", "Stop distance", "pts", ("avg_entry", "stop_price"),
                  lambda t: (t["avg_entry"].astype(float) - t["stop_price"].astype(float)).abs()),
    Discriminator("time_at_band", "Time at band pre-entry", "s",
                  ("entry_ts_utc", "acceptance_ts"),
                  lambda t: (t["entry_ts_utc"] - t["acceptance_ts"]).dt.total_seconds()),
    Discriminator("position_size", "Position size", "contracts", ("max_contracts",),
                  lambda t: t["max_contracts"].astype(float)),
)


def _auc(ranks: np.ndarray, win: np.ndarray) -> float:
    """P(a random winner's value exceeds a random loser's), ties at 0.5 — the
    common-language effect size, read off pre-computed average ranks. 0.5 is no
    separation; the distance from it, in either direction, is the whole signal.
    Scale-free and outlier-robust, which a difference-of-means is not."""
    nw = int(win.sum())
    nl = len(win) - nw
    if nw == 0 or nl == 0:
        return float("nan")
    u = float(ranks[win].sum()) - nw * (nw + 1) / 2.0
    return u / (nw * nl)


def _sep_luck(vals: np.ndarray, win: np.ndarray) -> float | None:
    """How often relabelling the same values, at the same win/loss ratio, separates
    them at least this hard. The AUC analogue of :func:`luck` — same seed, same
    permutation count, same reason: with 344 trades and a handful of features, one
    feature clearing 0.5 by a bit is what shuffling hands you for free.

    None when there is nothing to test (too few trades, or one side empty)."""
    n = len(vals)
    nw = int(win.sum())
    if n < MIN_TRADES_FOR_LUCK or nw == 0 or nw == n:
        return None
    ranks = pd.Series(vals).rank().to_numpy()
    obs = abs(_auc(ranks, win) - 0.5)
    rng = np.random.default_rng(SEED)
    idx = np.arange(n)
    beat = 0
    for _ in range(PERMUTATIONS):
        sel = rng.choice(idx, nw, replace=False)
        u = float(ranks[sel].sum()) - nw * (nw + 1) / 2.0
        if abs(u / (nw * (n - nw)) - 0.5) >= obs:
            beat += 1
    return (beat + 1) / (PERMUTATIONS + 1)


def entry_discriminator(trades: pd.DataFrame, with_luck: bool = True) -> dict:
    """For each entry-knowable field, what winners carried vs what losers did — and
    whether the gap is more than the split's own shape hands you.

    The cuts test one bucket of one feature at a time; this reads every feature at
    once as a winner-mean/loser-mean contrast with a separation score (AUC) and the
    same permutation floor the cuts carry, so the rows that *hold* are the honest
    shortlist of "there might be a filter here". Winners is ``net > 0``.

    Empty (no trades, or none of the features' columns present) -> empty dict."""
    if trades is None or trades.empty or "net_pnl" not in trades.columns:
        return {}
    pnl = trades["net_pnl"].astype(float)
    win_all = (pnl > 0).to_numpy()

    scored = []
    for d in DISCRIMINATORS:
        if any(c not in trades.columns for c in d.needs):
            continue
        v = d.value(trades).astype(float).to_numpy()
        m = ~np.isnan(v)  # time-at-band is NaT-then-NaN when acceptance wasn't logged
        vv, ww = v[m], win_all[m]
        # A feature the engine holds constant (a fixed stop, a fixed size) can't
        # separate anything — AUC is 0.5 by construction. Drop it rather than print
        # a row that only ever says "no", which for a fixed-risk engine is most of them.
        if len(vv) == 0 or ww.all() or not ww.any() or vv.std() == 0:
            continue
        ranks = pd.Series(vv).rank().to_numpy()
        scored.append((d, vv, ww,
                       _auc(ranks, ww),
                       _sep_luck(vv, ww) if with_luck else None))

    # Bonferroni over the features actually tested — same bar, same reasoning as
    # luck_bar; a feature we couldn't score (too sparse) doesn't tighten it.
    tested = sum(1 for *_, p in scored if p is not None)
    bar = 0.05 / max(1, tested)
    rows = [{
        "feature": d.label,
        "unit": d.unit,
        "win_mean": float(vv[ww].mean()),
        "loss_mean": float(vv[~ww].mean()),
        "auc": auc,
        "luck": p,
        "holds": p is not None and p <= bar,
    } for d, vv, ww, auc, p in scored]
    return {
        "rows": rows,
        "luck_bar": bar,
        "n_win": int(win_all.sum()),
        "n_loss": int((~win_all).sum()),
    }


# --- daily concentration: did a few sessions make the book? -------------------

def daily_concentration(trades: pd.DataFrame) -> dict:
    """The book rolled up to sessions — the risk view a trade-level table hides.

    A win rate says nothing about how the green arrived: a book that grinds a little
    every day and one that is three huge days over a flat year read identically at
    the trade level and could not be more different to sit through. ``top3_share``
    is the daily analogue of the win/loss table's tail column — the fraction of net
    the three best days carried — and ``worst_day`` is the number a max-drawdown in
    dollars is built out of. ``series`` is every session's net in order, for a strip
    that shows the clustering directly.

    Grouped by the engine's ``session`` key (falling back to the local entry date).
    Empty -> empty dict."""
    if trades is None or trades.empty or "net_pnl" not in trades.columns:
        return {}
    if "session" in trades.columns:
        key = trades["session"].astype(str)
    elif "entry_ts_local" in trades.columns:
        key = trades["entry_ts_local"].dt.date.astype(str)
    else:
        return {}
    daily = trades.groupby(key)["net_pnl"].sum().astype(float).sort_index()
    nets = daily.to_numpy()
    idx = [str(i) for i in daily.index]
    total = float(nets.sum())
    # Of all the net made, how much the three best days were. Only meaningful over a
    # profitable book — a share of a negative total is not a concentration.
    top3 = float(np.sort(nets)[::-1][:3].sum() / total) if total > 0 else float("nan")
    best_i, worst_i = int(np.argmax(nets)), int(np.argmin(nets))
    return {
        "days": int(len(daily)),
        "green_share": float((nets > 0).mean()),
        "avg_day": float(nets.mean()),
        "med_day": float(np.median(nets)),
        "best_day": float(nets[best_i]),
        "best_date": idx[best_i],
        "worst_day": float(nets[worst_i]),
        "worst_date": idx[worst_i],
        "top3_share": top3,
        "series": [{"date": idx[i], "net": float(nets[i])} for i in range(len(nets))],
    }
