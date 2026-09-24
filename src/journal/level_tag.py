"""Which reference a fill actually landed on — measured, not self-reported.

The journal already records what you *said* about a trade (model, setup,
confluences). This module answers the same question with no self-report in it:
given where the fill printed, which of the levels on the chart was it unusually
close to?

The word doing the work is *unusually*. With two dozen levels drawn, every price
in the session range is a few ticks from something, so raw proximity tags
everything and means nothing. Every distance here is therefore scored against a
null built from the fill's own session:

  companions   ~N_POOL instants are drawn from +/-NULL_WINDOW_MIN around the fill
               and the N_NULL whose *drift* is closest to the fill's are kept.

  drift        signed displacement from a trailing median. This is the part that
               cannot be skipped. A fill is never a random moment — it happens
               when price is stretched, usually against the trade — so comparing
               it to unmatched moments makes every lower band and every VAL look
               attractive and every EMA look repelled. That is a property of
               *when* you trade, not of *what you trade off*.

  rank         the fraction of companions that sat closer to the family than the
               real fill did. 0.0 = tighter than every comparable moment, 0.5 =
               exactly chance. This is the stored quantity.

Levels are scored in FAMILIES, not individually, because they run collinear —
the session -1 sigma band, the developing VAL and the Globex VAL are three names
for "the low side" on most days, and a per-level score just splits one signal
across three columns and calls the split a ranking.

A row is nevertheless stored per *level*, because the review asks a human which
one they were trading off and a family cannot answer that ("which VAH?"). Those
extra rows carry ``dist_ticks`` only; ``rank`` stays on the one member its family
was scored through. Distance is a fact and needs no null — it is the ranking that
had to stay pooled.

What is stored is the measurement (the rank), never the judgement (the tag).
Whether a rank of 0.07 counts as "at the level" is a threshold the caller
applies at read time — so retuning it costs nothing and never needs a migration.

The entry point takes a contract, a day and a list of fills. It knows nothing
about replays, backtests or the live session, which is what lets the same call
serve all three.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

TICK = 0.25

#: Bumped whenever the null or the family map changes in a way that moves ranks.
#: Stored beside every row so a mixed table is detectable and recomputable.
METHOD = "v7-volshelf-timewt-fullbararm-permember-drift20-pool600-n25"

N_NULL = 25              # companions kept per fill (the closest-drift ones)
N_POOL = 600             # candidates drawn before drift-matching
DRIFT_MIN = 20           # trailing window the displacement is measured against
NULL_WINDOW_MIN = 45     # companions come from +/- this many minutes

#: A rank below this reads as "at the level" in the UI. Advisory only — nothing
#: in this module filters on it, because the stored rank must outlive the choice.
AT_LEVEL_RANK = 0.05

#: Where each chart series lives on the SessionFrame, and what to call it.
SERIES: dict[str, dict[str, str]] = {
    'profile_ny':     {'poc': 'devVP_poc', 'vah': 'devVP_vah', 'val': 'devVP_val'},
    'profile_globex': {'poc': 'gxVP_poc', 'vah': 'gxVP_vah', 'val': 'gxVP_val'},
    'profile_weekly': {'poc': 'wkVP_poc', 'vah': 'wkVP_vah', 'val': 'wkVP_val'},
    'vwap_ny':     {'middle': 'vwap', 'upper1': 'vwap_u1', 'lower1': 'vwap_l1',
                    'upper2': 'vwap_u2', 'lower2': 'vwap_l2'},
    'vwap_globex': {'middle': 'gxvwap', 'upper1': 'gxvwap_u1', 'lower1': 'gxvwap_l1'},
    'vwap_weekly': {'middle': 'wkvwap', 'upper1': 'wkvwap_u1', 'lower1': 'wkvwap_l1'},
    'ema9':   {'value': 'ema9'},
    'ema20':  {'value': 'ema20'},
    'ema50':  {'value': 'ema50'},
    'ema200': {'value': 'ema200'},
    'mv_poc_naked':   {'value': 'mv_poc_naked'},
    'mv_poc_revisit': {'value': 'mv_poc_revisit'},
    #: The strongest live volume shelf's two edges — see ``journal.sim.vol_shelf``.
    #: A *zone* rather than a line, which is why both edges are carried: a family
    #: collapses to its nearest member, so "distance to the shelf" comes out as
    #: distance to whichever edge price is on, which is the reading that means
    #: something. Its centre would put a fill mid-zone at a distance of zero from
    #: nothing anybody was watching.
    'vol_shelf': {'hi': 'vol_shelf_hi', 'lo': 'vol_shelf_lo'},
}

#: The collinear groups. A family's distance at an instant is the distance to its
#: NEAREST member — "how close to the low side", not "how close to the session
#: -1 sigma specifically". Companions are collapsed the same way, so a family
#: with more members gains no advantage from having them.
FAMILIES: dict[str, tuple[str, ...]] = {
    'low_band':    ('vwap_l1', 'vwap_l2', 'gxvwap_l1', 'wkvwap_l1'),
    'high_band':   ('vwap_u1', 'vwap_u2', 'gxvwap_u1', 'wkvwap_u1'),
    'value_low':   ('devVP_val', 'gxVP_val', 'wkVP_val'),
    'value_high':  ('devVP_vah', 'gxVP_vah', 'wkVP_vah'),
    'value_mid':   ('devVP_poc', 'gxVP_poc', 'wkVP_poc'),
    'session_mean': ('vwap', 'gxvwap', 'wkvwap'),
    'trend_ema':   ('ema9', 'ema20', 'ema50', 'ema200'),
    # Own families rather than members of `session_mean`, because they are not
    # the same claim. The three session means are one line asked from three start
    # times; a POC anchor restarts wherever the day's agreed price last moved, so
    # on a trending day it sits nowhere near them. Folding one in would let it win
    # `session_mean` on days the session VWAPs were far away and read afterwards
    # as "traded off VWAP" — the collinearity that justifies a family is exactly
    # what these lines do not have. `trend_ema`, which nothing claims is a level
    # and which ranks at chance, stays the built-in null control.
    #
    # And two families rather than one with two members, for the same reason: the
    # re-arm rules disagree about when a level is worth re-anchoring, which is the
    # whole question being asked of them. Pooled, a fill near either would score
    # as "near the POC anchor" and neither rule could ever be shown to be the
    # better one.
    'mv_poc_naked':   ('mv_poc_naked',),
    'mv_poc_revisit': ('mv_poc_revisit',),
    # Its own family, and not a member of `value_mid`, because it is not the same
    # claim and it is not collinear with one. A POC is where the *most* volume
    # sat, which on a long quiet session is wherever price sat longest; a shelf is
    # where the most volume traded *per visit*, which is the opposite reading and
    # by construction lands somewhere else on exactly the days the two would
    # disagree. Pooling them would let a fill near either score as "traded off
    # value" and neither could ever be shown to be the one being traded.
    'volume_shelf': ('vol_shelf_hi', 'vol_shelf_lo'),
}

FAMILY_LABELS = {
    'low_band': 'low band', 'high_band': 'high band',
    'value_low': 'value low (VAL)', 'value_high': 'value high (VAH)',
    'value_mid': 'value mid (POC)', 'session_mean': 'VWAP',
    'trend_ema': 'EMA',
    'mv_poc_naked': 'naked-POC VWAP', 'mv_poc_revisit': 'revisited-POC VWAP',
    'volume_shelf': 'volume shelf',
}

#: What to call a level when the *member* is known, which it always is on a
#: stored row — ``family_distance`` resolves each family to its nearest member.
#:
#: The families pool NY, globex and weekly on purpose (they are collinear, and
#: splitting them would divide one signal across three columns before ranking
#: it). That pooling is right for the measurement and wrong for a human reading a
#: chip: "value high (VAH)" does not say *which* VAH, and the globex one is a
#: different level to have been watching than the session one. So the label comes
#: off the member, and the session prefix is the first thing in it.
MEMBER_LABELS = {
    # Volume profiles — NY is the developing RTH profile (`profile_ny`).
    'devVP_poc': 'NY POC', 'devVP_vah': 'NY VAH', 'devVP_val': 'NY VAL',
    'gxVP_poc': 'GX POC', 'gxVP_vah': 'GX VAH', 'gxVP_val': 'GX VAL',
    'wkVP_poc': 'WK POC', 'wkVP_vah': 'WK VAH', 'wkVP_val': 'WK VAL',
    # VWAPs and their bands.
    'vwap': 'NY VWAP', 'vwap_u1': 'NY +1σ', 'vwap_l1': 'NY −1σ',
    'vwap_u2': 'NY +2σ', 'vwap_l2': 'NY −2σ',
    'gxvwap': 'GX VWAP', 'gxvwap_u1': 'GX +1σ', 'gxvwap_l1': 'GX −1σ',
    'wkvwap': 'WK VWAP', 'wkvwap_u1': 'WK +1σ', 'wkvwap_l1': 'WK −1σ',
    # No session prefix: an EMA is read off whatever bars are on the chart, and
    # these are the null control rather than a level anybody claims to trade.
    'ema9': 'EMA 9', 'ema20': 'EMA 20', 'ema50': 'EMA 50', 'ema200': 'EMA 200',
    'mv_poc_naked': 'naked-POC VWAP', 'mv_poc_revisit': 'revisited-POC VWAP',
    # No session prefix: a shelf is read off the trailing window, not off an
    # anchor. The words are the ones a trader uses out loud for the two edges.
    'vol_shelf_hi': 'shelf top', 'vol_shelf_lo': 'shelf base',
}


#: Every level that belongs to a family, and the family it belongs to. The flat
#: view is what a picker offers and what a stored answer is validated against —
#: the pick names a *level*, because "which VAH" is the thing a family cannot say.
FAMILY_OF: dict[str, str] = {m: fam for fam, ms in FAMILIES.items() for m in ms}
MEMBERS: tuple[str, ...] = tuple(FAMILY_OF)


def label_for(family: str, member: str | None = None) -> str:
    """What to call one measured level.

    The member's name when there is one, the family's when there is not — a row
    can carry a null member, and a chip reading "unknown" where a level was
    measured would be worse than the pooled name.
    """
    if member and member in MEMBER_LABELS:
        return MEMBER_LABELS[member]
    return FAMILY_LABELS.get(family, family)

#: Exits that land where a bracket put them, not where the trader chose. Tagging
#: these would fill the journal with attributions nobody made: a stop sits at
#: vol-ruler distance from entry, and calling that "traded off the VAL" is a
#: fabricated reason that later reads as evidence.
MECHANICAL_REASONS = frozenset({'stop', 'trail'})


@dataclass(frozen=True)
class Fill:
    """One priced moment to be scored. ``reason`` is the journal's exit reason
    (``stop`` / ``target`` / ``manual`` / ``trail``); ``None`` for entries."""
    key: str
    anchor: str          # 'entry' | 'exit'
    ts_utc: pd.Timestamp
    price: float
    reason: str | None = None

    @property
    def mechanical(self) -> bool:
        return self.anchor == 'exit' and (self.reason or '') in MECHANICAL_REASONS


@dataclass(frozen=True)
class LevelRank:
    """One fill's relationship to ONE level.

    ``dist_ticks`` is a fact and every measured level gets one. ``rank`` is the
    measurement and is scoped to the *family*, so it is carried only by the
    member that produced it — the one nearest its family at that instant. On the
    rest it is ``None``, which reads as "not the level this family was scored on"
    rather than "ranked badly". Putting the family's rank on all three would say
    a level 400 ticks away was unusually close.
    """
    key: str
    anchor: str
    family: str
    member: str
    rank: float | None
    dist_ticks: float    # signed: fill price minus the level, in ticks

    @property
    def at_level(self) -> bool:
        return self.rank is not None and self.rank < AT_LEVEL_RANK

    @property
    def ranked(self) -> bool:
        """Whether this row is its family's scored member."""
        return self.rank is not None


def _ema_key(rows: list[dict]) -> str | None:
    """EMA rows name their value column something other than 'value'."""
    return next((k for k in rows[0] if k != 'time'), None) if rows else None


def _ns(values) -> np.ndarray:
    """Datetimes as int64 NANOSECONDS, whatever resolution they arrive in.

    ``.astype('int64')`` on a datetime column returns the underlying integer in
    *that column's* unit — microseconds for a datetime64[us] frame — while
    ``Timestamp.value`` is always nanoseconds. Mixing the two is a silent
    factor-of-1000 that puts every companion at the end of the tape and every
    level out of reach, with no exception raised anywhere. Cached tapes happen
    to be datetime64[ns]; nothing guarantees the next source will be.
    """
    return pd.DatetimeIndex(pd.to_datetime(values, utc=True)).as_unit('ns').asi8


class DayLevels:
    """One session's level series, queryable at an arbitrary instant.

    Each series keeps its own time axis because the rows are sparse: a developing
    value area does not exist before its anchor starts, and an absent level has to
    read as absent rather than as the last value that happened to exist.
    """

    def __init__(self, frame):
        self.frame = frame
        self.axes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for slot, cols in SERIES.items():
            rows = getattr(frame, slot, None) or []
            if not rows:
                continue
            if slot.startswith('ema'):
                src = _ema_key(rows)
                if src is None:
                    continue
                cols = {src: next(iter(cols.values()))}
            times = np.array([r['time'] for r in rows], dtype='int64')
            for src, name in cols.items():
                if src not in rows[0]:
                    continue
                self.axes[name] = (times, np.array([r[src] for r in rows], dtype=float))

        t = frame.ticks
        self.tick_ns = _ns(t['ts_utc'])
        self.tick_px = t['price'].to_numpy(dtype=float)
        self._drift_ns: np.ndarray | None = None
        self._drift: np.ndarray | None = None

    # -- reads -------------------------------------------------------------

    def at(self, ts_utc) -> dict[str, float]:
        """Every level as of the last bar STRICTLY BEFORE this instant's own bar.

        Strictly-before is the whole lookahead discipline: the bar containing the
        fill is still forming, and its developing value area already contains the
        print being explained.
        """
        bt = self.frame.bar_time(ts_utc)
        out = {}
        for name, (times, vals) in self.axes.items():
            i = int(np.searchsorted(times, bt, 'left')) - 1
            out[name] = float(vals[i]) if i >= 0 else math.nan
        return out

    def price_at(self, ts_utc) -> float:
        """Last traded price at an instant — the companion's stand-in for a fill."""
        i = int(np.searchsorted(self.tick_ns, pd.Timestamp(ts_utc).value, 'right')) - 1
        return float(self.tick_px[i]) if i >= 0 else math.nan

    def drift_at(self, ts_utc) -> float:
        """How far, and which way, price had run by this instant (ticks).

        A rolling median rather than any charted average, deliberately: matching
        companions on an EMA would quietly condition away one of the very
        families being scored.
        """
        if self._drift_ns is None:
            s = pd.Series(self.tick_px,
                          index=pd.to_datetime(self.tick_ns, utc=True))
            s = s.resample('1min').last().ffill()
            self._drift_ns = _ns(s.index)
            self._drift = ((s - s.rolling(DRIFT_MIN, min_periods=5).median())
                           / TICK).to_numpy()
        i = int(np.searchsorted(self._drift_ns, pd.Timestamp(ts_utc).value, 'right')) - 1
        return float(self._drift[i]) if i >= 0 else math.nan

    # -- families ----------------------------------------------------------

    def member_distance(self, price: float, levels: dict[str, float]) -> dict[str, float]:
        """{member: signed ticks} at one priced instant, for every level that had
        a finite value.

        Plain arithmetic, and deliberately not ranked: a distance is a fact about
        where the chart was, and it is the thing a trader can be asked to confirm.
        The ranking stays at family level (see ``family_distance``) because that
        is the measurement, and per-level ranking would split one collinear
        signal across three columns — the thing FAMILIES exists to prevent.
        """
        out = {}
        for m in FAMILY_OF:
            v = levels.get(m, math.nan)
            if math.isfinite(v):
                out[m] = (price - v) / TICK
        return out

    def family_distance(self, price: float, levels: dict[str, float]) -> dict[str, tuple[str, float]]:
        """{family: (nearest member, signed ticks)} at one priced instant."""
        out = {}
        for fam, members in FAMILIES.items():
            best, best_d = None, math.inf
            for m in members:
                v = levels.get(m, math.nan)
                if not math.isfinite(v):
                    continue
                d = (price - v) / TICK
                if abs(d) < abs(best_d):
                    best, best_d = m, d
            if best is not None:
                out[fam] = (best, best_d)
        return out


def _companions(lv: DayLevels, ts_utc, rng) -> list[tuple[pd.Timestamp, float]]:
    """Instants from the same session, matched on drift. See module docstring."""
    span = NULL_WINDOW_MIN * 60 * 1_000_000_000
    centre = pd.Timestamp(ts_utc).value
    a, b = max(int(lv.tick_ns[0]), centre - span), min(int(lv.tick_ns[-1]), centre + span)
    if b <= a:
        return []
    drift0 = lv.drift_at(ts_utc)
    if not math.isfinite(drift0):
        return []
    cand = []
    for ns in rng.integers(a, b + 1, size=N_POOL):
        nts = pd.Timestamp(int(ns), tz='UTC')
        px, dr = lv.price_at(nts), lv.drift_at(nts)
        if math.isfinite(px) and math.isfinite(dr):
            cand.append((abs(dr - drift0), nts, px))
    cand.sort(key=lambda x: x[0])
    return [(t, p) for _, t, p in cand[:N_NULL]]


def rank_fills(lv: DayLevels, fills: list[Fill], *, seed: int) -> list[LevelRank]:
    """Score already-loaded fills against an already-built session.

    Split out from :func:`tag_fills` so callers that already hold a SessionFrame
    — a replay finishing, a chart that just drew the day — do not pay to build it
    twice, and so tests can drive the scoring off a synthetic frame.
    """
    rng = np.random.default_rng(seed)
    out: list[LevelRank] = []
    for f in fills:
        if f.mechanical:
            continue
        levels = lv.at(f.ts_utc)
        mine = lv.family_distance(float(f.price), levels)
        if not mine:
            continue
        comps = _companions(lv, f.ts_utc, rng)
        if len(comps) < N_NULL // 2:      # too thin to be a null; say nothing
            continue
        comp_d: dict[str, list[float]] = {fam: [] for fam in mine}
        for cts, cpx in comps:
            cl = lv.at(cts)
            for fam, (_, d) in lv.family_distance(cpx, cl).items():
                if fam in comp_d:
                    comp_d[fam].append(abs(d))
        # The family's rank belongs to the member it was measured on.
        ranked: dict[str, float] = {}
        for fam, (member, d) in mine.items():
            pool = comp_d.get(fam) or []
            if pool:
                ranked[member] = float(np.mean([c < abs(d) for c in pool]))

        # One row per *level*, not per family. The extra rows carry distance
        # only, and they exist so the review can be asked which level the trade
        # was taken off: a family collapses to its nearest member, so without
        # them there is no way to say "the globex POC" on a day the session POC
        # was closer.
        for member, d in lv.member_distance(float(f.price), levels).items():
            out.append(LevelRank(key=f.key, anchor=f.anchor,
                                 family=FAMILY_OF[member], member=member,
                                 rank=ranked.get(member), dist_ticks=float(d)))
    return out


def tag_fills(*, contract: str, day: date, fills: list[Fill], tz,
              allow_fetch: bool = False) -> list[LevelRank]:
    """Score a day's fills. Cache-only by default — this runs after a session,
    not as a reason to buy tick data.

    ``contract`` is a root or a journal instrument; the roll is resolved by
    ``session_chart``. Returns [] when the session cannot be built, when its tape
    disagrees with the fills (see :func:`fills_align`), or when every fill was
    mechanical — all three are "nothing to say", never a guess.
    """
    from api import session_chart as sc      # deferred: api imports journal

    frame = sc.session_frame(contract=contract, day=day, tz=tz, overnight=True,
                             allow_fetch=allow_fetch, context_days=0)
    if frame is None or frame.ticks.empty:
        return []
    lv = DayLevels(frame)
    if not lv.axes or not fills_align(lv, fills):
        return []
    return rank_fills(lv, fills, seed=int(day.strftime('%Y%m%d')))


#: A day whose fills sit further than this from its own tape is not the day those
#: fills happened on.
MAX_ALIGN_PTS = 2.0


def fills_align(lv: DayLevels, fills: list[Fill]) -> bool:
    """Does the resolved tape actually contain these fills?

    A journal row carries the front month at *export* time, so the roll can hand
    back a contract the trade never touched — and the fills then sit hundreds of
    points off a session that looks perfectly healthy. Checked by the only thing
    that settles it: the fill price versus what traded at the fill's own instant.
    """
    gaps = [abs(f.price - lv.price_at(f.ts_utc)) for f in fills]
    gaps = [g for g in gaps if math.isfinite(g)]
    return bool(gaps) and float(np.median(gaps)) <= MAX_ALIGN_PTS
