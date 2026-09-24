"""What price did before the entry and after the exit — measured off the tape.

The journal records the trade: where you got in, where you got out, what it
paid. It says nothing about the two windows that decide whether the trade was
well taken — the approach you bought into, and the move you left behind. This
module measures both, per trade, in points.

WHY NOTHING IS CAPTURED LIVE. The tape is deterministic and already on disk: an
attempt pins its symbol and day, and ``sim.ticks`` holds the session. So a
window around any fill can be re-sliced years later and come back identical.
What gets stored is therefore the *summary*, never a copy of the ticks — and
``method`` stamps the definitions that produced it, so a redefinition is a
backfill rather than lost data.

WHAT THE FORWARD WINDOW IS, AND WHY IT IS ALLOWED TO OVERLAP THE HOLD. The two
windows above are both measured around what you *did* — they start at a fill and
end at a fill. ``fwd_pts`` is the one measurement here that ignores the exit
entirely: where price was a fixed number of seconds after the entry, whatever
happened to the trade in between. It answers the one question the rest of the
journal cannot ask, because every other number is contaminated by the management
— **was the direction right at all**. A trade scratched in eight seconds and a
trade held an hour get the same three readings, which is the whole point: the
entry is a claim about direction, and a claim is scored on the clock, not on the
trader's nerve.

WHAT IS DELIBERATELY NOT HERE.

*The hold.* MAE/MFE between entry and exit is ``journal.excursion``, on minute
bars, feeding the Trades page. Measuring it a second time here — on ticks, to a
different definition — would put two numbers with one name in the same journal.
Note what keeps ``fwd_pts`` on the right side of that line despite spanning the
same minutes: it is shaped by the *clock*, never by the hold, so it can never
become a second answer to "how far did this trade run".

*Money.* Everything is points, so a 1-lot and a 5-lot practice trade on the same
setup are comparable. The journal already knows the size.

WHAT THE VOL COLUMNS ARE FOR. Points alone cannot be read: twelve points of
follow-through is a lot on a quiet afternoon and nothing on a CPI open. The vol
ruler measures how big the bars were around the fill — at three resolutions,
because a scalp lives on the 500-tick chart and its 1-minute bars say almost
nothing about it — so every other number on the row can be divided by one and
compared across regimes. Same three readings as the chart's ruler pane
(`frontend/src/lib/volRuler.ts`), which is deliberate: the journal should agree
with what was on screen.

*Judgement.* "Exited early" is a threshold on ``post_mfe_pts_15m``, applied at
read time. Same discipline as ``level_tag``: store the measurement, never the
verdict.

THE ONE TRAP WORTH NAMING. A window truncated by the end of the session reads
exactly like a window where nothing happened — a trade exited at 15:58 has four
minutes of tape, and its 30-minute follow-through of zero is an artifact, not a
finding. ``pre_avail_s``/``post_avail_s`` say how much tape each window actually
got, and no analysis may skip them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from .config import tick_size as tick_size_of
from .sim import bars as barmod

#: Bumped whenever a definition below changes in a way that moves a number.
#: Stored beside every row so a mixed table is detectable and recomputable.
METHOD = "v4-pts-vol-1_5_15_30-range5-fwd-30s_1m_5m"

#: Minutes before the entry and after the exit that every windowed quantity is
#: measured over. Four horizons rather than one because the question "was there
#: more in it" has a different answer at one minute and at half an hour, and
#: which one matters is a finding, not an assumption.
HORIZONS_MIN: tuple[int, ...] = (1, 5, 15, 30)

#: The longest horizon, reused for the two whole-window measures (`exit_rank`,
#: the range/location pair) so they describe the same span the runs do.
LONG_MIN = 30

#: Seconds after the ENTRY that the forward window is read at. Seconds and not
#: minutes because the shortest of them has to be shorter than the trades being
#: scored: a scalp is out inside a minute, and a first horizon of one minute
#: would only ever grade it after the fact.
FWD_HORIZONS_S: tuple[int, ...] = (30, 60, 300)


def fwd_label(sec: int) -> str:
    """``30 -> '30s'``, ``60 -> '1m'``. The column suffix and the UI's label are
    the same string, derived rather than written twice."""
    return f"{sec}s" if sec % 60 else f"{sec // 60}m"


#: The suffixes `FWD_HORIZONS_S` widens onto, in the same order.
FWD_LABELS: tuple[str, ...] = tuple(fwd_label(s) for s in FWD_HORIZONS_S)

#: The resolutions the vol ruler reads at, as ``suffix -> builder argument``. An
#: int means a tick-count bar, a string a pandas frequency — the two builders in
#: ``sim.bars``, which is where bars are defined in this codebase and where the
#: charts get theirs. 500 ticks / 30s / 1m because those are the charts actually
#: traded on; a 1-minute number alone cannot describe a 40-second scalp.
VOL_RES: tuple[tuple[str, int | str], ...] = (("500t", 500), ("30s", "30s"), ("1m", "1min"))

#: Minutes before the entry the median bar range is taken over. The same span as
#: `LONG_MIN`, so the ruler describes the approach the rest of the row measures.
VOL_WIN_MIN = LONG_MIN
#: Wilder's period, as every platform draws it — and as `volRuler.ts` computes it.
ATR_PERIOD = 14
#: Bars fed to the ATR before the entry bar. More than the period so the seed is
#: forgotten by the time the line reaches the fill, bounded so the work per trade
#: does not grow with the session's length.
ATR_LOOKBACK_BARS = 100
#: Fewer closed bars than this in the window and a median is noise, not a level.
MIN_BARS = 3

_NS_MIN = 60 * 1_000_000_000

#: A trade whose fills sit further than this from the tape at their own instant
#: did not happen on this tape. Same threshold, and the same reason, as
#: ``level_tag.MAX_ALIGN_PTS`` — the roll hands back a contract the trade never
#: touched and the session still looks perfectly healthy.
#:
#: Applied to the MEDIAN over a whole day, so it catches the day-sized failure:
#: every trade on the wrong contract at once.
MAX_ALIGN_PTS = 2.0

#: The same question asked of ONE trade, which the median above cannot answer:
#: a single fill off the tape while the rest of the day sits on it. That happens
#: — a paper account quoting its own feed, a replay sitting of another era's
#: contract booked onto a calendar day — and a day-level median of ten healthy
#: trades sails through with the eleventh measured against a market it never
#: touched.
#:
#: An order of magnitude looser than the day guard, deliberately, because a
#: logical trade's price is a size-weighted AVERAGE of fills and legitimately
#: sits off any single instant's tick. There is no judgement in the gap between
#: the two: across this journal's 1075 measured trades the honest gaps reach
#: 8.8 points at p99 and 12.5 at p99.5, while the fills that belong to another
#: tape miss by 134 and 4400. Anything landing between those is a data fault to
#: be found, not a trade to be scored.
MAX_TRADE_ALIGN_PTS = 50.0


@dataclass(frozen=True)
class Trade:
    """One logical trade, reduced to the six things a window needs. ``direction``
    is the journal's own ``Long``/``Short``."""
    key: str
    direction: str
    entry_ts: pd.Timestamp
    entry_px: float
    exit_ts: pd.Timestamp
    exit_px: float

    @property
    def sign(self) -> int:
        """+1 when price rising is in your favour, -1 when it is against."""
        return -1 if str(self.direction).lower().startswith("s") else 1


@dataclass(frozen=True)
class Context:
    """One trade's two windows. Every ``_pts`` field is in index points and is
    signed *to the trade's direction*: positive always means "price went the way
    the trade wanted", whichever side it was.

    Read the two ``avail_s`` fields first. They are the denominator for every
    other number in the row.
    """

    key: str
    #: The contract the roll resolved for the session these windows came off —
    #: the audit trail for the trap in the module docstring, not decoration.
    symbol: str
    #: Points per tick for that contract. Stored rather than assumed, so a reader
    #: can convert between the points every ``_pts`` field is in and the ticks the
    #: vol ruler is in without knowing which instrument the row came from.
    tick_size: float

    #: Seconds of tape the windows actually got. Short of `LONG_MIN * 60`
    #: whenever the session ran out — at the open for `pre`, at the close for
    #: `post`. A zero follow-through over two seconds of tape is not a zero.
    pre_avail_s: float
    post_avail_s: float
    #: The same guard for the forward window: seconds of tape after the ENTRY.
    #: Its own field because it is measured from a different instant — a trade
    #: entered at 15:57 and held to the bell has plenty of `pre` and no `fwd`.
    fwd_avail_s: float

    # -- the approach ------------------------------------------------------
    #: Net move into the fill over each horizon. POSITIVE MEANS YOU CHASED:
    #: price had already gone your way before you paid for it. Negative means
    #: you bought weakness / sold strength.
    pre_run_pts: dict[int, float | None]
    #: High-low of the approach, over the long horizon, a quarter of it, and
    #: the last five minutes (the 5m pair arrived 2026-09-01, for the review
    #: chips' short straightness read). The yardstick every other number on the
    #: row is read against — 8 points of follow-through is a lot after a
    #: 10-point approach and nothing after 80.
    pre_range_pts_5m: float | None
    pre_range_pts_15m: float | None
    pre_range_pts_30m: float | None
    #: Where the fill sat inside that range, 0 = the low, 1 = the high. RAW, not
    #: direction-signed: "bought the top of the range" is `direction=Long` and
    #: `pre_loc` near 1, and flipping the sign here would hide which it was.
    pre_loc_5m: float | None
    pre_loc_15m: float | None
    pre_loc_30m: float | None

    # -- was the direction right -------------------------------------------
    #: Net move from the ENTRY price at each of `FWD_HORIZONS_S`, signed to the
    #: trade's direction. Positive = the entry was pointing the right way at that
    #: instant. BLIND TO THE EXIT by construction: this scores the claim the
    #: entry made, not the trade that was managed out of it, so a scratch and a
    #: runner on the same signal read identically here.
    #:
    #: None past the end of the session, never the last price: a 15:58 entry that
    #: reads 0 at five minutes is the bell, not a market that stood still.
    fwd_pts: dict[int, float | None]

    # -- what was left behind ----------------------------------------------
    #: The best the trade could have done had it stayed on, per horizon. The
    #: number behind "I exit too early" — on its own, though, it is only half
    #: the claim; see `post_mae_pts`.
    post_mfe_pts: dict[int, float | None]
    #: The worst it would have been through the same window. The other half:
    #: holding for the follow-through means sitting through this first, and a
    #: trade whose MAE would have taken the stop out did not leave money on the
    #: table, whatever the MFE says.
    post_mae_pts: dict[int, float | None]
    #: Follow-through and terminal move measured to the end of the session
    #: rather than to a horizon — the whole of what was left.
    post_mfe_close_pts: float | None
    post_close_pts: float | None
    #: Fraction of the following `LONG_MIN` minutes whose price the exit beat.
    #: 1.0 = nothing traded better; 0.0 = it only ever got better without you.
    exit_rank: float | None
    #: Seconds until price traded back through the entry after the exit — the
    #: stop-and-reverse tell. None when it never did before the session ended,
    #: which is itself the informative answer.
    post_ret_entry_s: float | None

    # -- how big the bars were ---------------------------------------------
    #: Wilder ATR(14) in TICKS at the entry bar, per resolution. What the chart's
    #: ruler pane showed at that moment; noisy by construction (it remembers ~14
    #: bars), which is exactly why the median below sits next to it.
    vol_atr_ticks: dict[str, float | None]
    #: Median bar range in ticks over the `VOL_WIN_MIN` minutes before the entry.
    #: The robust half of the pair: one wild bar cannot move it.
    vol_med_ticks: dict[str, float | None]

    # -- the bar the fill landed in ----------------------------------------
    #: The entry bar's body in ticks, SIGNED TO THE TRADE: positive means the bar
    #: was going the trade's way when it was taken. Raw green/red is deliberately
    #: not what is stored — "green" means opposite things to a long and a short,
    #: and the sign is the whole question ("did I buy strength or weakness").
    eb_body_ticks: dict[str, float | None]
    #: Where the fill sat inside that bar's range, 0 = its low, 1 = its high.
    #: RAW, not direction-signed, for the reason `pre_loc_15m` is.
    #:
    #: NOT CLIPPED, and it escapes [0, 1] on roughly one trade in two hundred:
    #: the price here is the logical trade's *average* entry, and a scaled entry
    #: averages fills the bar holding the first one never traded. Clipping would
    #: turn "this was not one fill" into a plausible 0 or 1.
    eb_loc: dict[str, float | None]
    #: Fraction of the entry bar's ticks that had already printed at the fill.
    #: This is the honest form of "was the candle closed": measured off the tape
    #: every bar is closed, and what actually differed live is how much of it you
    #: could see — 0.1 on a 500-tick bar means you acted on 50 prints.
    eb_elapsed: dict[str, float | None]


class PricePath:
    """A session's ticks, sliceable by instant. Built once per session and shared
    by every trade on it — the load is the expensive part, the arithmetic is not.
    """

    def __init__(self, ticks: pd.DataFrame, symbol: str = ""):
        # `.as_unit('ns')` before `.astype('int64')`, never after: pandas keeps a
        # column's own resolution, so a tape stored as datetime64[us] casts to
        # *microseconds* and every window silently comes back empty — the reads
        # below compare it against `Timestamp.value`, which is always ns.
        ts = pd.to_datetime(ticks["ts_utc"], utc=True).dt.as_unit("ns")
        self.ns = ts.astype("int64").to_numpy()
        self.px = ticks["price"].to_numpy(dtype=float)
        self.symbol = symbol
        # The bar builders need the frame itself, and the vol ruler reads bar
        # RANGES — a question the price path alone cannot answer. Held rather
        # than copied: the caller already has this session in memory.
        self._ticks = ticks
        self._bars: dict[str, pd.DataFrame] = {}

    def __bool__(self) -> bool:
        return self.ns.size > 0

    @property
    def start_ns(self) -> int:
        return int(self.ns[0])

    @property
    def end_ns(self) -> int:
        return int(self.ns[-1])

    def bars(self, res: int | str) -> pd.DataFrame:
        """The session's bars at one resolution, built once and kept.

        Built from the session's FIRST tick, never from a slice around the
        trade, and that is the point: 500-tick bars chunk from wherever the
        frame starts, so a per-trade slice would give two trades on the same
        session bars that never existed on any chart. Anchoring at the session
        open makes the boundaries reproducible — the same approximation against
        ATAS that ``sim.bars.tick_bars`` already documents, and no worse.

        Cached per resolution because a day's trades share the path: the build
        is one reshape (or one groupby) over the session either way.
        """
        key = str(res)
        hit = self._bars.get(key)
        if hit is None:
            src = self._ticks
            # The builders read `size` for the volume column, which nothing here
            # uses — a tape without one (a test, an older cache) still has bars.
            if "size" not in src.columns:
                src = src.assign(size=1.0)
            hit = (barmod.tick_bars(src, res) if isinstance(res, int)
                   else barmod.time_bars(src, res))
            self._bars[key] = hit
        return hit

    def index_at(self, ns: int) -> int:
        """Position of the last tick at or before an instant; -1 before the tape."""
        return int(np.searchsorted(self.ns, ns, "right")) - 1

    def price_at(self, ns: int) -> float:
        """Last price at or before an instant. NaN before the tape starts."""
        i = self.index_at(ns)
        return float(self.px[i]) if i >= 0 else math.nan

    def window(self, a_ns: int, b_ns: int) -> np.ndarray:
        """Prices in ``[a, b]``, both inclusive. Empty when the span has no ticks."""
        if b_ns < a_ns:
            return self.px[:0]
        lo = int(np.searchsorted(self.ns, a_ns, "left"))
        hi = int(np.searchsorted(self.ns, b_ns, "right"))
        return self.px[lo:hi]

    def first_touch(self, a_ns: int, b_ns: int, level: float,
                    from_above: bool) -> int | None:
        """First instant in ``(a, b]`` where price comes back to ``level``.

        ``from_above`` says which side price starts on, so the test is a genuine
        approach rather than a tautology: asking a long that was stopped out
        below its entry when price next traded *below* the entry answers "at the
        very next tick", every time, and measures nothing.
        """
        lo = int(np.searchsorted(self.ns, a_ns, "right"))
        hi = int(np.searchsorted(self.ns, b_ns, "right"))
        if hi <= lo:
            return None
        seg = self.px[lo:hi]
        hits = np.flatnonzero(seg <= level if from_above else seg >= level)
        return int(self.ns[lo + int(hits[0])]) if hits.size else None


def _ns(ts) -> int:
    t = pd.Timestamp(ts)
    return int((t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).value)


def measure(path: PricePath, trade: Trade) -> Context | None:
    """One trade's two windows against one session's ticks.

    None when the tape cannot speak to the trade at all — no ticks, or fills the
    session does not contain. Never a partial guess: a row that exists is a row
    every field of which was measured on the right tape.
    """
    if not path:
        return None
    entry_ns, exit_ns = _ns(trade.entry_ts), _ns(trade.exit_ts)
    if exit_ns < entry_ns:
        return None
    if not trade_aligns(path, trade):
        return None
    sign = trade.sign
    entry_px, exit_px = float(trade.entry_px), float(trade.exit_px)

    pre_avail = max(0.0, (entry_ns - path.start_ns) / 1e9)
    post_avail = max(0.0, (path.end_ns - exit_ns) / 1e9)

    pre_run: dict[int, float | None] = {}
    for m in HORIZONS_MIN:
        was = path.price_at(entry_ns - m * _NS_MIN)
        pre_run[m] = round(sign * (entry_px - was), 4) if math.isfinite(was) else None

    pre_range_5, pre_loc_5 = _range_and_loc(path, entry_ns, 5, entry_px)
    pre_range_15, pre_loc_15 = _range_and_loc(path, entry_ns, 15, entry_px)
    pre_range_30, pre_loc_30 = _range_and_loc(path, entry_ns, LONG_MIN, entry_px)

    # The forward window. `price_at` walks BACK to the last tick at or before the
    # instant, so past the end of the tape it would hand back the session's
    # closing price for every horizon — a truncated window wearing a real number.
    # The horizon is refused outright instead.
    fwd_avail = max(0.0, (path.end_ns - entry_ns) / 1e9)
    fwd_pts: dict[int, float | None] = {}
    for s in FWD_HORIZONS_S:
        at = entry_ns + s * 1_000_000_000
        px = path.price_at(at) if at <= path.end_ns else math.nan
        fwd_pts[s] = round(sign * (px - entry_px), 4) if math.isfinite(px) else None

    post_mfe: dict[int, float | None] = {}
    post_mae: dict[int, float | None] = {}
    for m in HORIZONS_MIN:
        w = path.window(exit_ns, exit_ns + m * _NS_MIN)
        if w.size == 0:
            post_mfe[m] = post_mae[m] = None
            continue
        moves = sign * (w - exit_px)
        post_mfe[m] = round(float(moves.max()), 4)
        post_mae[m] = round(float(moves.min()), 4)

    to_close = path.window(exit_ns, path.end_ns)
    if to_close.size:
        close_moves = sign * (to_close - exit_px)
        mfe_close = round(float(close_moves.max()), 4)
        close_pts = round(float(close_moves[-1]), 4)
    else:
        mfe_close = close_pts = None

    rank_w = path.window(exit_ns, exit_ns + LONG_MIN * _NS_MIN)
    exit_rank = (round(float(np.mean(sign * (exit_px - rank_w) > 0)), 4)
                 if rank_w.size else None)

    # Which side of the entry the exit left price on decides the question worth
    # asking. A scratch left it on neither, and gets no answer rather than a 0.
    ret_entry_s = None
    if exit_px != entry_px:
        back = path.first_touch(exit_ns, path.end_ns, entry_px,
                                from_above=exit_px > entry_px)
        ret_entry_s = round((back - exit_ns) / 1e9, 3) if back is not None else None

    tick = tick_size_of(path.symbol or "NQ")
    vol_atr: dict[str, float | None] = {}
    vol_med: dict[str, float | None] = {}
    eb_body: dict[str, float | None] = {}
    eb_loc: dict[str, float | None] = {}
    eb_elapsed: dict[str, float | None] = {}
    for suffix, res in VOL_RES:
        r = _vol_at(path, res, entry_ns, entry_px, sign, tick)
        vol_atr[suffix], vol_med[suffix] = r["atr"], r["med"]
        eb_body[suffix], eb_loc[suffix], eb_elapsed[suffix] = (
            r["body"], r["loc"], r["elapsed"])

    return Context(
        key=trade.key,
        symbol=path.symbol,
        tick_size=tick,
        pre_avail_s=round(pre_avail, 3),
        post_avail_s=round(post_avail, 3),
        fwd_avail_s=round(fwd_avail, 3),
        pre_run_pts=pre_run,
        pre_range_pts_5m=pre_range_5,
        pre_range_pts_15m=pre_range_15,
        pre_range_pts_30m=pre_range_30,
        pre_loc_5m=pre_loc_5,
        pre_loc_15m=pre_loc_15,
        pre_loc_30m=pre_loc_30,
        fwd_pts=fwd_pts,
        post_mfe_pts=post_mfe,
        post_mae_pts=post_mae,
        post_mfe_close_pts=mfe_close,
        post_close_pts=close_pts,
        exit_rank=exit_rank,
        post_ret_entry_s=ret_entry_s,
        vol_atr_ticks=vol_atr,
        vol_med_ticks=vol_med,
        eb_body_ticks=eb_body,
        eb_loc=eb_loc,
        eb_elapsed=eb_elapsed,
    )


def _vol_at(path: PricePath, res: int | str, entry_ns: int, entry_px: float,
            sign: int, tick: float) -> dict[str, float | None]:
    """The ruler and the entry bar at one resolution.

    Everything is None where the resolution has nothing to say — a 500-tick bar
    needs 500 prints, and a thin overnight approach may not have produced one in
    half an hour. A null here means "not enough bars", never "no volatility",
    which is why nothing falls back to a coarser resolution.
    """
    out: dict[str, float | None] = {"atr": None, "med": None, "body": None,
                                    "loc": None, "elapsed": None}
    bars = path.bars(res)
    if bars.empty or tick <= 0:
        return out

    # The bar the fill landed in: the first whose last tick is at or after the
    # entry's. Bars are contiguous by tick index, so this is the containing bar
    # — except past the final one, where the trailing partial bar was dropped
    # for never having closed, and the fill therefore has no bar to sit in.
    ends = bars["end_idx"].to_numpy()
    starts = bars["start_idx"].to_numpy()
    i_tick = path.index_at(entry_ns)
    b = int(np.searchsorted(ends, i_tick, "left")) if i_tick >= 0 else -1
    if 0 <= b < len(bars):
        hi, lo = float(bars["high"].iat[b]), float(bars["low"].iat[b])
        out["body"] = round(sign * (float(bars["close"].iat[b])
                                    - float(bars["open"].iat[b])) / tick, 3)
        # A bar that never moved has no inside to be in — None, not 0.5, for the
        # reason `_range_and_loc` gives.
        out["loc"] = round((entry_px - lo) / (hi - lo), 4) if hi > lo else None
        span = int(ends[b]) - int(starts[b])
        out["elapsed"] = round((i_tick - int(starts[b])) / span, 4) if span > 0 else None

    # The ruler reads CLOSED bars only, so both lines are facts about bars that
    # had finished when the trade was taken. `b` is the forming one; everything
    # strictly before it is settled.
    closed = bars.iloc[:max(b, 0)] if b >= 0 else bars
    if closed.empty:
        return out
    rng = (closed["high"].to_numpy() - closed["low"].to_numpy()) / tick

    # --- the median, over the approach window.
    close_ns = closed["ts_utc"].values.astype("datetime64[ns]").astype("int64")
    win = rng[close_ns >= entry_ns - VOL_WIN_MIN * _NS_MIN]
    if win.size >= MIN_BARS:
        out["med"] = round(float(np.median(win)), 3)

    # --- Wilder's ATR, walked over the last `ATR_LOOKBACK_BARS` closed bars so
    # the cost per trade is bounded by the period rather than by the session.
    tail = closed.iloc[-(ATR_LOOKBACK_BARS + 1):]
    if len(tail) > ATR_PERIOD:
        high = tail["high"].to_numpy() / tick
        low = tail["low"].to_numpy() / tick
        prev = tail["close"].to_numpy()[:-1] / tick
        tr = np.empty(len(tail))
        tr[0] = high[0] - low[0]
        tr[1:] = np.maximum(high[1:], prev) - np.minimum(low[1:], prev)
        atr = float(np.mean(tr[:ATR_PERIOD]))
        for v in tr[ATR_PERIOD:]:
            atr += (float(v) - atr) / ATR_PERIOD
        out["atr"] = round(atr, 3)
    return out


def _range_and_loc(path: PricePath, entry_ns: int, minutes: int,
                   entry_px: float) -> tuple[float | None, float | None]:
    """The approach's high-low and where the fill sat in it.

    Location is None on a flat window rather than 0.5: a range of zero has no
    inside, and calling it the middle would read as "entered mid-range" on
    exactly the sessions where that means least.
    """
    w = path.window(entry_ns - minutes * _NS_MIN, entry_ns)
    if w.size == 0:
        return None, None
    lo, hi = float(w.min()), float(w.max())
    span = hi - lo
    loc = round((entry_px - lo) / span, 4) if span > 0 else None
    return round(span, 4), loc


def trade_aligns(path: PricePath, trade: Trade) -> bool:
    """Does this tape contain THIS trade's fills? The per-trade half of the guard.

    A fill before the tape starts is unanswerable rather than wrong — the price
    comes back NaN and the windows around it are already None — so it is not
    grounds for refusal here. Only a fill the tape can price, and prices
    somewhere else entirely, is.
    """
    for ts, px in ((trade.entry_ts, trade.entry_px), (trade.exit_ts, trade.exit_px)):
        tape = path.price_at(_ns(ts))
        if math.isfinite(tape) and abs(float(px) - tape) > MAX_TRADE_ALIGN_PTS:
            return False
    return True


def trades_align(path: PricePath, trades: list[Trade]) -> bool:
    """Does this tape actually contain these fills?

    The same guard ``level_tag.fills_align`` makes, for the same reason and by
    the same test: a journal row carries the front month at *export* time, so the
    roll can resolve a contract the trade never touched, and the fills then sit
    hundreds of points off a session that looks entirely healthy.
    """
    gaps = []
    for t in trades:
        for ts, px in ((t.entry_ts, t.entry_px), (t.exit_ts, t.exit_px)):
            g = abs(float(px) - path.price_at(_ns(ts)))
            if math.isfinite(g):
                gaps.append(g)
    return bool(gaps) and float(np.median(gaps)) <= MAX_ALIGN_PTS


def load_path(contract: str, day: date) -> PricePath | None:
    """One session's tick path, cache-only. None when it was never bought.

    Deliberately NOT ``session_chart.session_frame``: that builds bars, both
    profiles, the footprint, CVD, the EMAs and the RSI, and this module reads
    none of them. Since the hook runs on the finish of every sitting, the
    difference between "read two parquets" and "draw a whole session" is the
    difference between a hook you can leave on and one you can't.

    ``contract_for_cached`` is what resolves the roll, and using it rather than
    globbing the cache is the guard against measuring one contract's context
    around another's fills.
    """
    from .config import root_symbol
    from .sim import ticks as tickmod

    sym = tickmod.contract_for_cached(root_symbol(contract), day)
    if sym is None:
        return None
    parts = [tickmod.cached_overnight(sym, day), tickmod.cached_rth(sym, day),
             tickmod.cached_post(sym, day)]
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return None
    t = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    return PricePath(t, symbol=sym)


def measure_day(*, contract: str, day: date, trades: list[Trade],
                path: PricePath | None = None) -> list[Context]:
    """Every trade on one session, against that session's ticks.

    Cache-only: this runs off the back of a finished sitting, which is not a
    reason to buy tick data. Returns [] when the session isn't on disk or its
    tape disagrees with the fills — both are "nothing to say", never a guess.

    ``path`` lets a caller that already holds the session pass it in, so the
    parquet is read once however many trades came off it.
    """
    path = path or load_path(contract, day)
    if not path:
        return []
    if not trades_align(path, trades):
        return []
    return [c for c in (measure(path, t) for t in trades) if c is not None]


#: Flat column order shared by the table, the writer and the reader. The dict
#: fields are widened here — one row per trade, one column per horizon — because
#: the questions worth asking ("does follow-through at 15 minutes predict …")
#: are column comparisons, and a JSON blob would make each of them a parse.
COLUMNS: tuple[str, ...] = (
    "trade_key", "symbol", "tick_size", "method", "computed_at",
    "pre_avail_s", "post_avail_s", "fwd_avail_s",
    *(f"pre_run_pts_{m}m" for m in HORIZONS_MIN),
    "pre_range_pts_5m", "pre_range_pts_15m", "pre_range_pts_30m",
    "pre_loc_5m", "pre_loc_15m", "pre_loc_30m",
    *(f"fwd_pts_{lab}" for lab in FWD_LABELS),
    *(f"post_mfe_pts_{m}m" for m in HORIZONS_MIN),
    *(f"post_mae_pts_{m}m" for m in HORIZONS_MIN),
    "post_mfe_close_pts", "post_close_pts", "exit_rank", "post_ret_entry_s",
    *(f"vol_atr_ticks_{s}" for s, _ in VOL_RES),
    *(f"vol_med_ticks_{s}" for s, _ in VOL_RES),
    *(f"eb_body_ticks_{s}" for s, _ in VOL_RES),
    *(f"eb_loc_{s}" for s, _ in VOL_RES),
    *(f"eb_elapsed_{s}" for s, _ in VOL_RES),
)

#: The dict fields keyed by resolution rather than by horizon. Each widens onto
#: one column per `VOL_RES`, named ``{field}_{suffix}``.
_RES_FIELDS: tuple[str, ...] = ("vol_atr_ticks", "vol_med_ticks",
                                "eb_body_ticks", "eb_loc", "eb_elapsed")


def direction_edge(rows: Iterable[dict | None]) -> dict:
    """How often a set of entries pointed the right way, per forward horizon.

    The aggregate the day view asks for. Takes the *stored* rows rather than
    Contexts, because the caller is reading a day back out of the journal rather
    than measuring it — and takes them WITH their gaps: a ``None`` is a trade
    whose session was never cached, and dropping it silently before counting is
    how "3 of 4 right" gets reported over a day that had eleven trades.

    Three denominators, all of them returned, because they differ and the
    difference is the finding: ``trades`` is what was taken, ``measured`` is what
    carries a forward window at all, and each horizon's own ``n`` is what still
    had tape left that far out. A 15:57 entry is in the first two and in none of
    the third at five minutes.

    ``measured`` keys off ``fwd_avail_s`` rather than off the row existing, and
    the difference is not pedantry: a table mid-recompute holds rows written
    under an older ``METHOD`` whose forward columns were never measured, and
    counting those as measured would report "5 of 5" over a row of dashes.

    No verdict, for the reason the module docstring gives: a hit rate is a
    number, "you are bad at direction" is a threshold, and the threshold belongs
    to whoever is reading. Exact zeroes are counted apart from the wins rather
    than folded into either side — price back at the entry to the tick is not a
    call that came good.
    """
    rows = list(rows)
    measured = [r for r in rows if r and r.get("fwd_avail_s") is not None]
    horizons = []
    for sec in FWD_HORIZONS_S:
        col = f"fwd_pts_{fwd_label(sec)}"
        vals = [float(r[col]) for r in measured if r.get(col) is not None]
        n = len(vals)
        right = sum(1 for v in vals if v > 0)
        flat = sum(1 for v in vals if v == 0)
        horizons.append({
            "label": fwd_label(sec),
            "seconds": sec,
            "n": n,
            "right": right,
            "flat": flat,
            "hit_rate": round(right / n * 100, 1) if n else None,
            "median_pts": round(float(np.median(vals)), 2) if n else None,
        })
    return {"trades": len(rows), "measured": len(measured), "horizons": horizons}


def as_row(ctx: Context, *, method: str, computed_at: str) -> dict:
    """A Context flattened onto :data:`COLUMNS`."""
    d = asdict(ctx)
    row = {
        "trade_key": d.pop("key"),
        "method": method,
        "computed_at": computed_at,
    }
    for m in HORIZONS_MIN:
        row[f"pre_run_pts_{m}m"] = d["pre_run_pts"].get(m)
        row[f"post_mfe_pts_{m}m"] = d["post_mfe_pts"].get(m)
        row[f"post_mae_pts_{m}m"] = d["post_mae_pts"].get(m)
    for s in FWD_HORIZONS_S:
        row[f"fwd_pts_{fwd_label(s)}"] = d["fwd_pts"].get(s)
    for k in ("pre_run_pts", "post_mfe_pts", "post_mae_pts", "fwd_pts"):
        d.pop(k)
    for field in _RES_FIELDS:
        per_res = d.pop(field)
        for suffix, _ in VOL_RES:
            row[f"{field}_{suffix}"] = per_res.get(suffix)
    row.update(d)
    return {c: row.get(c) for c in COLUMNS}


def chips(row: dict) -> list[dict]:
    """A stored context row as read-only review chips: what the tape says about
    the moment, so the reviewer stops transcribing it.

    These replaced the context half of the free-tag vocabulary on 2026-08-31 —
    "Chopping", "Falling knife" and their relatives were hand-written copies of
    numbers this table already held, fragmenting exactly the way hand copies of
    a measurement do. The chips *describe and never gate*: the market-structure
    study showed chop KPIs predict stops but fail as gates, and a chip that
    turned red-means-don't would be that failed gate wearing a label.

    Each chip is ``{key, label, title}`` — the label is the chip, the title is
    where the derivation is admitted. Values the window could not measure
    (short pre-tape, flat range) produce no chip rather than a guess.
    """
    out: list[dict] = []

    def straightness(run, rng):
        """|net| ÷ range and its word, or None where the window had no inside."""
        if run is None or not rng:
            return None, None
        s = min(abs(float(run)) / float(rng), 1.0)
        return s, ("choppy" if s < 0.3 else "two-way" if s < 0.6 else "one-way")

    run30, range30 = row.get("pre_run_pts_30m"), row.get("pre_range_pts_30m")
    s30, word30 = straightness(run30, range30)
    if s30 is not None:
        out.append({
            "key": "approach",
            "label": f"{word30} approach · {s30:.2f}",
            "title": ("Straightness of the 30 minutes into the entry: |net move| ÷ "
                      "high-low range. 0 is pure chop, 1 is a straight line. "
                      f"Net {float(run30):+.2f} pts over a {float(range30):.2f} pt range."),
        })
    run5 = row.get("pre_run_pts_5m")
    if run5 is not None:
        # The same straightness read at 5 minutes, folded onto the drive chip:
        # the run and its shape are one moment, and "one-way" beside "+13 pts"
        # is what separates a mature drive from a last-second lunge out of chop.
        # Absent on rows measured before the 5m range existed (METHOD < v4).
        s5, word5 = straightness(run5, row.get("pre_range_pts_5m"))
        shape = f" · {word5} {s5:.2f}" if s5 is not None else ""
        out.append({
            "key": "drive",
            "label": f"last 5m {float(run5):+.1f} pts{shape}",
            "title": "Net move of the 5 minutes before the fill — the run the "
                     "entry was buying into or fading. Signed by price, not by "
                     "the trade's side."
                     + ("" if s5 is None else
                        " The straightness beside it is |net| ÷ the same 5 "
                        "minutes' high-low range, read like the approach chip's."),
        })
    loc30 = row.get("pre_loc_30m")
    if loc30 is not None:
        out.append({
            "key": "loc",
            "label": f"entry {float(loc30) * 100:.0f}% up the 30m range",
            "title": "Where the fill sat in the high-low range of the prior 30 "
                     "minutes: 0% is its low, 100% its high.",
        })
    med1 = row.get("vol_med_ticks_1m")
    if med1 is not None:
        atr1 = row.get("vol_atr_ticks_1m")
        atr = f" (ATR {float(atr1):.0f}t)" if atr1 is not None else ""
        out.append({
            "key": "vol",
            "label": f"1m bars ~{float(med1):.0f}t{atr}",
            "title": "The vol ruler at the fill: median 1-minute bar range in "
                     "ticks, with the ATR beside it. How big the weather was, "
                     "so 12 pts of follow-through reads differently on a quiet "
                     "afternoon than on a CPI open.",
        })
    return out
