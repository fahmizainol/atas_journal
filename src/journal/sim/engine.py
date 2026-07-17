"""Event-driven tick loop.

Fills are evaluated **tick by tick**, not bar by bar. That is the whole reason
this pays for tick data: the classic bar-backtest ambiguity — "the stop and the
target are both inside this candle, which one hit first?" — cannot arise here,
because the tick stream gives the true order of events.

Bars exist only for the rules that key off a candle *close* (acceptance, the
variant-B trigger, the beyond-midline invalidation) and for the chart.

Causality: within one tick index we settle exits and fills FIRST, then process
any bar that closed on that tick. So a signal produced by a bar close can only
act from the next tick onward — the engine never trades on a close it could not
yet have seen.

The band bounce is one idea read in two directions, so the loop is written once
against a *signed* frame (``side``): the long reads dev1/dev2 off the upper
bands and VAH, the short reads the lower bands and VAL, and every comparison is
the same expression multiplied by ±1. ``side`` is a property of the strategy
(the registry picks the entry point), never a config knob — a knob the engine
could contradict would let two different-looking configs produce byte-identical
runs. The config's knob *names* stay long-flavoured for both, and mean the
mirror on a short: ``acceptance_require_green`` demands a red candle,
``invalidate_below_mid_bars`` counts closes above the mid, and
``exit_below_vah_bars`` counts closes back above VAL. In every case the rule is
"against the trade" — the name just says it in the long's words.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..config import ET_TZ, point_value, tick_size
from . import bars as barmod
from . import confluences as confmod
from . import profile as profmod
from . import ticks as tickmod
from . import vwap as vwapmod
from .rules import SimConfig

# State machine:
#   DISARMED --acceptance--> ARMED --fill--> IN_TRADE --exit--> DISARMED
# (variant B inserts AWAITING_RECLAIM between ARMED and the resting stop order)


@dataclass
class _Pos:
    entry_i: int
    entry_price: float
    # The stop in force right now. With trail_step_ticks it ratchets toward the
    # trade; init_stop keeps the distance actually risked at entry, which is what
    # the R-multiple is measured against and what the chart's stop line means.
    stop_price: float
    init_stop: float
    target_price: float | None  # None => track dev2 live
    band_width_ticks: float
    acceptance_ts: pd.Timestamp | None
    # Consecutive bar closes back inside value (below VAH for a long, above VAL
    # for a short) *since this position opened* — the streak is a property of the
    # trade, not the session, so a ghost counts its own.
    inside_value: int = 0
    # The fade's mirror streak: consecutive closes re-accepted back beyond dev1
    # against the position (invalidate_beyond_dev1_bars). Unused by the bounce.
    beyond_band: int = 0
    # A rule decided to leave: exit at market on the next tick, booked under
    # this reason ("vah" for the bounce's value re-acceptance, "dev1" for the
    # fade's, "panic" for the flow-shock exit). None = no market exit pending.
    market_exit: str | None = None
    # The panic exit's one read has been taken (see _panic — it evaluates once,
    # at the window's end, never per tick).
    panic_checked: bool = False
    # --- pyramid (scale-in) ---
    # Contracts actually filled so far — one lot at the first fill, one more on
    # each add. This, not cfg.contracts, is what the trade's P&L and commission
    # are priced on: a position whose adds never triggered exits at the size it
    # really carried. Left at 1 by every non-pyramid caller, which prices P&L on
    # cfg.contracts directly and never reads it.
    size: int = 1
    # Price of the next lot's stop trigger, or None once every lot is filled (and
    # for a non-pyramid position, which has no adds). Advances one step past the
    # first fill per lot, so it walks the grid regardless of where each add really
    # printed.
    next_add: float | None = None
    # Lots still to add.
    adds_left: int = 0


def _minutes_et(ts: pd.Series) -> np.ndarray:
    et = ts.dt.tz_convert(ET_TZ)
    return (et.dt.hour * 60 + et.dt.minute).to_numpy()


def _excursion(price: list[float], entry_i: int, exit_i: int,
               entry: float, s: float) -> tuple[float, float]:
    """The best and worst this trade was ever worth, in points, over its own life.

    Read off the same tick stream the fills came from (``price``), from the entry
    tick through the exit tick inclusive. In the trade's own direction (``s``): the
    maximum favorable excursion is the furthest price ever ran in profit before the
    trade was booked — what the exit left on the table — and the maximum adverse is
    the deepest it went against, the heat the trade sat through to get paid. MFE is
    >= 0 and MAE <= 0 except in the degenerate case where price never returned to a
    limit entry, which is itself the reading that the trade was never really live.
    """
    seg = price[entry_i:exit_i + 1]
    hi, lo = max(seg), min(seg)
    fav, adv = (hi, lo) if s > 0 else (lo, hi)
    return s * (fav - entry), s * (adv - entry)


def run_session(
    cfg: SimConfig, day: date, overnight: bool = False, side: str = "long",
    invert: bool = False,
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Simulate one session. Returns (trades, vetoed, bars, per-bar bands).

    ``side`` picks the direction the band bounce is read in: "long" bounces the
    upper bands (accept above dev1, buy the pullback, target dev2), "short"
    mirrors it exactly onto the lower bands (accept below dev1, sell the
    pullback, target dev2 beneath). See the module docstring for how the
    long-flavoured config knobs read on a short.

    ``invert`` decouples the band from the trade direction. Normally a long reads
    the upper band and a short the lower — the trade runs WITH the break, away
    from the mid, to dev2. Inverted, a long reads the LOWER band (buy the pullback
    into support) and a short the UPPER (sell the rally into resistance): the same
    entry price at dev1, the opposite direction, reverting toward the mid. dev2
    then sits behind the trade, so an inverted run must target an R-multiple, not
    dev2 (the schema enforces it). Only the *band* moves; every trade comparison
    (the stop, the R target, the entry crossing, the acceptance) keeps the trade's
    own sign, while the channel-relative reads (band width, the value-area edge
    and its exit, the mid invalidation) follow the band. With ``invert`` off the
    two signs coincide and every read is exactly what it was.

    With ``overnight`` the Globex segment (18:00 ET the previous evening) is
    spliced in front of RTH, which moves the VWAP anchor to the Globex open —
    every tick from 18:00 feeds the accumulation, so the bands price 09:30 opens
    against the whole night rather than restarting flat at the bell. The bars are
    built over the combined stream too, so one bar can straddle 09:30.

    The overnight ticks feed the *indicators only*. Acceptance, arming and the
    below-mid invalidation are evaluated exclusively on bars that close during
    RTH (see ``rth_i0``): an acceptance candle at 03:00 must not arm a setup that
    fills at the bell on a night nobody was watching. Entries remain confined to
    the config's entry window regardless.

    The bars and bands come back alongside the trades because the chart needs the
    *same* series the engine traded against — rendering a trade over differently
    computed bands would defeat the point of looking at it.

    ``vetoed`` holds entries a confluence gate rejected, tracked as ghost
    positions to their would-be exit so the run can report what the gate cost
    or saved. First-order counterfactual only: once a real fill is vetoed the
    session's subsequent arm/entry cycle can drift from the gateless run.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    # The sign that turns every long comparison into its short mirror. Read it as
    # "in the direction of the trade": s*(x - level) > 0 is "x is beyond level on
    # the side we are trading".
    s = 1.0 if side == "long" else -1.0
    # Which band the setup reads, and its own sign. Coupled to s unless inverted:
    # a long reads the upper band (bs = s = +1), a short the lower (bs = s = −1).
    # Inverting flips the band without touching the trade sign, so a long reads
    # the lower band (bs = −1, s = +1) and a short the upper (bs = +1, s = −1).
    # `bs` is used for the reads that are about the channel, not the position:
    # the band width, the value-area edge and its exit, and the mid invalidation.
    # When invert is off, bs == s and each of those is byte-identical to before.
    use_upper = (side == "long") != invert
    bs = 1.0 if use_upper else -1.0

    t = tickmod.get_day_ticks(tickmod.contract_for(cfg.contract, day), day,
                              include_overnight=overnight)
    if t is None or t.empty:
        return [], [], pd.DataFrame(), pd.DataFrame()

    # First tick of RTH: the boundary the state machine starts reading at, and —
    # when we asked for the overnight — the proof we actually got it.
    # get_day_ticks falls back to RTH-only if the Globex segment comes back empty,
    # and silently anchoring a Globex strategy's VWAP at 09:30 would produce a
    # run that looks like the idea but isn't it.
    rth_i0 = int(t["ts_utc"].searchsorted(tickmod.session_bounds_utc(day)[0], side="left"))
    if overnight and rth_i0 == 0:
        raise RuntimeError(
            f"no overnight ticks for {day} — a Globex-anchored VWAP cannot be built")

    b = barmod.tick_bars(t, cfg.ticks_per_bar)
    w = vwapmod.vwap_bands(t)
    if b.empty:
        return [], [], b, pd.DataFrame()

    tick = tick_size(cfg.instrument)

    # The developing profile is only built if a gate or the value-area exit reads
    # it — it costs a value-area scan per bar, and a run that ignores it must not
    # pay. The edge that matters is the one the trade sits *beyond*, and that is a
    # property of the band, not the trade sign: VAH on the upper band, VAL on the
    # lower, whichever direction is traded there.
    edge = "vah" if use_upper else "val"
    prof = edge_tick = edge_bar = None
    if confmod.needs_profile(cfg):
        prof = profmod.developing_profile(t, b, tick)
        # Two views of the same levels, and they are NOT interchangeable:
        #   edge_tick[i] — the last edge that had *closed* before tick i. What a
        #                  fill at i is judged against; using this bar's own edge
        #                  would judge the fill on ticks it had not yet seen.
        #   edge_bar[k]  — bar k's edge, including bar k's own ticks. Legitimate
        #                  at the close itself, which is when the exit rule reads it.
        edge_tick = profmod.levels_in_force(prof, b, len(t), edge=edge)
        edge_bar = getattr(prof, edge)

    gates = confmod.build_gates(cfg)
    if gates:
        ctx = confmod.SessionCtx(
            cfg=cfg, day=day, ticks=t, bars=b, value_edge_at_tick=edge_tick,
            profile=prof, side=side, band="upper" if use_upper else "lower",
        )
        for g in gates:
            g.prepare(ctx)

    pv = point_value(cfg.instrument)
    risk_pts = cfg.stop_ticks * tick
    acc_min = cfg.acceptance_min_ticks * tick
    entry_off = cfg.entry_stop_offset_ticks * tick
    limit_off = cfg.entry_limit_offset_ticks * tick
    # The trail's knobs, in ticks (the arithmetic below stays on the integer tick
    # grid — see _trail). A 0 step means "one click per trail distance"; a 0
    # breakeven offset puts the first click on the entry itself.
    trail_dist_t = cfg.trail_stop_ticks
    trail_step_t = cfg.trail_step_ticks or trail_dist_t
    trail_be_t = cfg.trail_breakeven_ticks
    # A breakeven stop, not a trail: the first click is the only one.
    trail_be_only = cfg.trail_breakeven_only

    # Panic exit: the signed tape (buy aggressor volume minus sell), cumulative,
    # so the delta since any fill is two lookups. Built only when the knob is on —
    # it costs a full-session scan a run that ignores it must not pay. cum_delta[i]
    # is the sum over the first i ticks, so ticks a..b inclusive are
    # cum_delta[b+1] - cum_delta[a].
    panic_delta = cfg.panic_exit_delta
    cum_delta: list | None = None
    ts_ns: list | None = None
    if panic_delta:
        sd = t["side"].to_numpy()
        if not (sd != "N").any():
            # A tape with no aggressor sides would make the panic exit a silent
            # no-op — a run that looks like the idea but isn't it. Refuse, same
            # as the missing-overnight guard above.
            raise RuntimeError(
                f"no aggressor sides in the {day} ticks — the panic exit cannot "
                f"read the tape; re-fetch the day or turn panic_exit_delta off")
        sz = t["size"].to_numpy(dtype="float64")
        signed = np.where(sd == "B", sz, np.where(sd == "A", -sz, 0.0))
        cum_delta = np.concatenate([[0.0], np.cumsum(signed)]).tolist()
        ts_ns = t["ts_utc"].astype("int64").to_numpy().tolist()
    panic_win_ns = int(cfg.panic_exit_window_s * 1_000_000_000)

    # Pyramid: split the size into pyr_n equal lots, one added each time price
    # runs pyr_step further in the trade's favour. pyr_n == 1 is the all-in fill —
    # one lot of the whole size, no adds, byte-identical to before the knob.
    pyr_n = max(1, cfg.pyramid_tranches)
    lot = cfg.contracts // pyr_n
    pyr_step = cfg.pyramid_step_ticks * tick
    pyr_blend = cfg.pyramid_stop_mode == "blend"

    price = t["price"].to_numpy(dtype="float64").tolist()
    ts = t["ts_utc"]
    # dev1/dev2 on the band being traded: the upper bands for a long (or a short
    # inverted onto them), the lower for a short (or an inverted long buying
    # support). Everything downstream reads these two names only.
    d1 = w["upper1" if use_upper else "lower1"].to_numpy().tolist()
    d2 = w["upper2" if use_upper else "lower2"].to_numpy().tolist()
    md = w["mid"].to_numpy().tolist()

    n = len(price)
    mins = _minutes_et(ts)
    open_m = cfg.entry_open.hour * 60 + cfg.entry_open.minute
    close_m = cfg.entry_close.hour * 60 + cfg.entry_close.minute
    flat_m = cfg.flat_by.hour * 60 + cfg.flat_by.minute

    # Last tick at which we may still hold. Data ends at the bell, so this is
    # normally the final tick; computing it anyway keeps the rule honest if the
    # fetch window ever widens. Searched from the RTH open, because the overnight
    # ticks between midnight and 09:30 also read as "before flat_by" on a wall
    # clock and would otherwise win the last-holdable slot.
    holdable = np.flatnonzero(mins[rth_i0:] < flat_m) + rth_i0
    force_i = int(holdable[-1]) if len(holdable) else n - 1

    # tick index -> index of the bar that closes on it (-1 if none)
    bar_end_of = np.full(n, -1, dtype="int64")
    bar_end_of[b["end_idx"].to_numpy()] = np.arange(len(b))
    bar_end_of = bar_end_of.tolist()
    b_open = b["open"].to_numpy().tolist()
    b_close = b["close"].to_numpy().tolist()

    armed = False
    awaiting_reclaim = False   # variant B: a bar has closed below dev1
    beyond = False             # variant A: price is still on the far side of dev1
    below_mid = 0
    acceptance_ts: pd.Timestamp | None = None
    pos: _Pos | None = None
    # (vetoing gate names, vetoed entry being tracked). The list holds EVERY gate
    # that rejected this fill, not just the first — the vetoed row keeps the whole
    # set so a per-confluence breakdown can score what each gate independently
    # caught, instead of only the one that happened to be checked first.
    ghosts: list[tuple[list[str], _Pos]] = []
    trades: list[dict] = []
    vetoed: list[dict] = []
    # The daily loss stop reads realized net P&L only — closed trades, in exit
    # order (which is also entry order: one position at a time). Once tripped it
    # is final for the session; nothing can move day_net back without a new
    # trade, and there are no new trades. Ghosts neither count toward it nor are
    # created after it: a halted entry is refused by the base rules, so the
    # gates are never even asked.
    day_net = 0.0
    halted = False

    def _row(p_: _Pos, reason: str, i: int, exit_price: float) -> dict:
        pts = s * (exit_price - p_.entry_price)
        # Priced on the size actually carried, not the config's: a pyramid whose
        # later lots never triggered exits smaller than one whose lots all filled.
        # With pyr_n == 1 the single lot is the whole size, so this is cfg.contracts.
        gross = pts * pv * p_.size
        comm = 2 * cfg.commission_per_side * p_.size
        mfe_pts, mae_pts = _excursion(price, p_.entry_i, i, p_.entry_price, s)
        return {
            "session": day,
            "direction": "Long" if side == "long" else "Short",
            "entry_ts_utc": ts.iloc[p_.entry_i],
            "exit_ts_utc": ts.iloc[i],
            # Tick indices, not just stamps: one aggressor sweeping N resting
            # orders emits N trade records sharing a ts_event, so a timestamp
            # does not uniquely identify a tick. The index does.
            "entry_idx": p_.entry_i,
            "exit_idx": i,
            # The volume-weighted average across every lot that filled. With one
            # lot it is that lot's price.
            "avg_entry": p_.entry_price,
            "avg_exit": exit_price,
            # The stop as entered (what was risked) and where the ratchet had
            # moved it by the exit. Without trailing the two are equal.
            "stop_price": p_.init_stop,
            "final_stop_price": p_.stop_price,
            "target_price": p_.target_price if p_.target_price is not None else d2[i],
            # The peak and trough the open trade ever showed, in points and in R.
            # MFE is what the exit left on the table; MAE the worst heat it sat
            # through. Measured off the ticks, from entry to exit inclusive.
            "mfe_points": mfe_pts,
            "mae_points": mae_pts,
            "mfe_r": mfe_pts / risk_pts,
            "mae_r": mae_pts / risk_pts,
            "exit_reason": reason,
            "points": pts,
            "r_multiple": pts / risk_pts,
            "band_width_ticks": p_.band_width_ticks,
            "acceptance_ts": p_.acceptance_ts,
            # The size the trade actually reached — its first lot plus every add
            # that triggered. The column name predates the pyramid; it has always
            # meant "the most contracts this trade held".
            "max_contracts": p_.size,
            "gross_pnl": gross,
            "commission": comm,
            "net_pnl": gross - comm,
        }

    def _close(reason: str, i: int, exit_price: float) -> None:
        nonlocal pos, armed, awaiting_reclaim, below_mid, day_net, halted
        assert pos is not None
        trades.append(_row(pos, reason, i, exit_price))
        day_net += trades[-1]["net_pnl"]
        if cfg.daily_loss_stop and day_net <= -cfg.daily_loss_stop:
            halted = True
        pos = None
        if cfg.rearm_after_exit:
            armed = False
            awaiting_reclaim = False
            below_mid = 0

    def _exit(p_: _Pos, i: int, p: float) -> tuple[str, float] | None:
        """(reason, fill) if this position is out at tick i, else None. One
        function for real and ghost positions: the vetoed rows are only a
        counterfactual if they were exited by exactly the rules the real trades
        were."""
        if p_.market_exit:
            # A market order sent when its rule tripped (a bar close for "vah",
            # a print for "panic"). It fills at the next print, whatever that
            # print is — including one straight through the stop. No free lunch
            # for having decided to leave.
            # "vah" is named for both sides: the reason is one rule (price was
            # re-accepted back inside value), and renaming it per side would fork
            # every consumer that groups exits by reason.
            return (p_.market_exit, p)
        tgt = p_.target_price if p_.target_price is not None else d2[i]
        if s * (p - p_.stop_price) <= 0:
            # Stop triggers on the first trade at or through it; fill at the
            # traded price, which is never better than the stop — i.e. never
            # better than reality.
            # A trailed stop is its own reason: a breakeven scratch is not the
            # full loss "stop" means, and lumping them would hide exactly the
            # effect the trail was added to measure.
            moved = p_.stop_price != p_.init_stop
            return ("trail" if moved else "stop", p)
        if s * (p - tgt) >= 0:
            # A resting exit limit gets its price.
            return ("target", tgt)
        if i >= force_i:
            return ("time", p)
        return None

    def _trail(p_: _Pos, p: float) -> None:
        """Ratchet the stop on a print that extends the run.

        The stop wants to sit trail_stop_ticks behind the print, but it may only
        rest on the step grid, whose origin is trail_breakeven_ticks beyond the
        entry — so it lands on that first level the moment the trade is one trail
        distance in front of it, and climbs one step at a time after that, never
        less than a trail distance behind. It is deliberately floored at that
        level rather than at (print - trail): a trail that could tighten a stop
        *below* the scratch it promises would cut winners' losses for them, which
        is a different rule than the one this measures.

        The offset is what makes the first click a real scratch and not a small
        loss: a stop on the entry is breakeven gross, and the round trip's
        commission then turns it into exactly that commission, lost.

        Counted in whole ticks, not points: every price here is on the tick grid,
        and floor() on a float distance would drop a step on the boundary print
        that earned it.

        Read off the current print rather than a stored high-water mark: the stop
        is monotone in the trade's direction, so a pullback simply computes a
        level we refuse to move back to. Same reason it is safe to run this
        *after* the tick's exit check — the ratchet only ever fires on a print
        that is beyond the stop it installs, so the new stop can never be hit by
        the print that created it, and the old stop stays in force for exactly
        the ticks it was really resting under."""
        if not trail_dist_t:
            return
        fav_t = round(s * (p - p_.entry_price) / tick)
        # Measured from the first level, not from the entry: the stop is never
        # closer than a full trail distance behind the print that installed it,
        # offset or no offset.
        if fav_t < trail_dist_t + trail_be_t:
            return
        steps = 0 if trail_be_only else (fav_t - trail_dist_t - trail_be_t) // trail_step_t
        lvl = p_.entry_price + s * (trail_be_t + steps * trail_step_t) * tick
        if s * (lvl - p_.stop_price) > 0:
            p_.stop_price = lvl

    def _add(p_: _Pos, i: int, p: float) -> None:
        """Fill pyramid lots whose stop this print has reached, and — in blend
        mode — re-strike the shared stop and target off the new average entry.

        A lot's add is a stop in the trade's favour (a buy-stop above for a long),
        so it fills at the traded price, never better than its trigger — the same
        'no free lunch' the exit stop gets. One fast print can sweep several
        triggers at once; the loop books each as a real lot at this print. next_add
        walks the grid from the *first* fill (one step per lot), not from where the
        last add happened to print, so a gap that skipped a level does not shrink
        the next lot's distance.

        Run BEFORE the tick's exit check: a print that reaches the target has
        passed the add stops beneath it on the way, so those lots really would
        have filled before the exit — scoring the exit on the larger size is the
        honest outcome, not front-running it. It can never fill a lot into its own
        stop: the adds are all in favour and the blended stop after an add sits
        stop_ticks below an average that is itself below this print, so the new
        stop stays under the price that installed it."""
        while p_.next_add is not None and s * (p - p_.next_add) >= 0:
            new_size = p_.size + lot
            p_.entry_price = (p_.entry_price * p_.size + p * lot) / new_size
            p_.size = new_size
            p_.adds_left -= 1
            p_.next_add = (p_.next_add + s * pyr_step) if p_.adds_left > 0 else None
            if pyr_blend:
                p_.stop_price = p_.entry_price - s * risk_pts
                p_.init_stop = p_.stop_price
                if p_.target_price is not None:
                    p_.target_price = p_.entry_price + s * cfg.target_rr * risk_pts

    def _panic(p_: _Pos, i: int) -> None:
        """One read of the tape at the panic window's end; arm the market exit
        if the whole window was a shock.

        Deliberately NOT a per-tick trigger. The threshold is a NET delta over
        the full window — a sustained dump the market did not absorb — and
        that is only knowable when the window closes. The per-tick variant
        (leave the moment the running delta touches the threshold) was A/B'd
        first and lost $23k of the baseline (run 07d9927e): it also fires on
        the transient spike that recovers within the minute, which is the
        signature of the winners this strategy lives on, not of the dead fill.

        Evaluated on the first tick at or past the boundary, over the ticks
        strictly inside the window; the fill leaves on the NEXT print (via
        market_exit, like every market order here). Signed with the trade: a
        dump trips a long, a rip trips a short. A position that exited before
        the window closed was simply never read.
        """
        if p_.panic_checked or ts_ns[i] - ts_ns[p_.entry_i] < panic_win_ns:
            return
        p_.panic_checked = True
        if p_.market_exit:
            return
        if s * (cum_delta[i] - cum_delta[p_.entry_i]) <= -panic_delta:
            p_.market_exit = "panic"

    for i in range(n):
        p = price[i]

        # Ghost positions: vetoed entries ride the same exit rules as a real
        # position (but never touch the arm/entry state) so the vetoed row
        # carries a would-be P&L, not just "an entry happened here". They pyramid
        # too, so the vetoed P&L stays comparable to a real pyramided trade.
        for gnames, gp in ghosts[:]:
            _add(gp, i, p)
            hit = _exit(gp, i, p)
            if hit:
                vetoed.append({**_row(gp, hit[0], i, hit[1]),
                               "gate": gnames[0], "gates": "|".join(gnames)})
                ghosts.remove((gnames, gp))
            else:
                _trail(gp, p)
                if panic_delta:
                    _panic(gp, i)

        if pos is not None:
            _add(pos, i, p)
            hit = _exit(pos, i, p)
            if hit:
                _close(hit[0], i, hit[1])
            else:
                _trail(pos, p)
                if panic_delta:
                    _panic(pos, i)

        elif armed:
            # Where variant A's limit rests: dev1, or entry_limit_offset_ticks in
            # FRONT of it (above dev1 on a long, below on a short), which fills the
            # pullback before it reaches the band. The offset can never exceed the
            # acceptance distance (schema enforces it), so the level always sits
            # between dev1 and the acceptance close — i.e. behind the market when
            # the setup arms, which is what makes it a resting limit.
            lvl = d1[i] + s * limit_off
            # Which side of that level price sits on, tracked on EVERY armed tick —
            # gates and the clock must not be able to suspend it. A resting limit
            # fills on the *crossing* back to its level, so what matters is the
            # transition beyond -> at-or-through, not the standing inequality:
            # once price is past the level and running, `s*(p - lvl) <= 0` stays
            # true all the way to the opposite band, and a check that only woke up
            # later (when a gate finally opened) would book a fill at a level the
            # market left long ago. `beyond` is what makes the touch a touch.
            at_limit = s * (p - lvl) <= 0
            touched = beyond and at_limit
            beyond = not at_limit
            if not halted and open_m <= mins[i] < close_m and i < force_i:
                # Signed by the BAND so the width is the distance between the two
                # bands, positive whichever band is traded — the lower dev2 sits
                # below its dev1. (bs, not s: an inverted trade's dev2 is behind
                # it, so the trade sign would report a negative width.)
                band_w = bs * (d2[i] - d1[i]) / tick
                fill = None
                if band_w < cfg.min_band_width_ticks:
                    pass  # too tight to be worth the trade; the touch is missed,
                          # not deferred — a limit that wasn't resting can't fill.
                elif cfg.entry_variant == "A":
                    # Limit resting on (or in front of) dev1: fills when price
                    # trades back to it — down to it on a long, up to it on a
                    # short. A resting limit gets its price, not the traded one.
                    if touched:
                        fill = lvl
                elif cfg.entry_variant == "B" and awaiting_reclaim:
                    # Stop beyond dev1: fills at market on the reclaim.
                    level = d1[i] + s * entry_off
                    if s * (p - level) >= 0:
                        fill = p
                if fill is not None:
                    tp = None
                    if cfg.target == "rr" and cfg.target_rr:
                        tp = fill + s * cfg.target_rr * risk_pts
                    stop = fill - s * risk_pts
                    new_pos = _Pos(
                        entry_i=i, entry_price=fill,
                        stop_price=stop, init_stop=stop, target_price=tp,
                        band_width_ticks=band_w, acceptance_ts=acceptance_ts,
                        # First lot down. The rest rest as stops one step apart in
                        # front of it; pyr_n == 1 leaves next_add None (no adds) and
                        # size == the whole contracts, i.e. the all-in fill.
                        size=lot, adds_left=pyr_n - 1,
                        next_add=(fill + s * pyr_step) if pyr_n > 1 else None,
                    )
                    vetoes = [g.name for g in gates if not g.allows(i, fill)]
                    if not vetoes:
                        pos = new_pos
                    else:
                        # Consume the setup exactly as a real entry would have —
                        # leaving it armed would re-fire the same (vetoed) entry
                        # on the very next tick. The ghost keeps every gate that
                        # rejected it, so overlapping vetoes stay attributable.
                        ghosts.append((vetoes, new_pos))
                        armed = False
                        awaiting_reclaim = False

        # Bars that closed overnight move the VWAP but never the state machine:
        # they are indicator input, not signals. (A bar straddling 09:30 closes in
        # RTH and does count — it is a bar the open actually printed.)
        bi = bar_end_of[i]
        if bi >= 0 and i >= rth_i0:
            c, o = b_close[bi], b_open[bi]

            # Value-area invalidation. Read this bar's own edge (it closed, so its
            # ticks are known) and arm a market exit for the next tick. Ghosts
            # count their own streaks so a vetoed entry is scored on the same rule.
            if cfg.exit_below_vah_bars and edge_bar is not None:
                lvl = edge_bar[bi]
                # Back inside value: below VAH on the upper band, above VAL on the
                # lower — a property of the band (bs), so an inverted trade reads
                # re-acceptance from the correct edge.
                inside = not np.isnan(lvl) and bs * (c - lvl) < 0
                for p_ in ([pos] if pos is not None else []) + [g for _, g in ghosts]:
                    p_.inside_value = p_.inside_value + 1 if inside else 0
                    if p_.inside_value >= cfg.exit_below_vah_bars:
                        p_.market_exit = "vah"

            if cfg.invalidate_below_mid_bars:
                # Closes on the far side of the mid from the band: below it on the
                # upper band, above it on the lower. Band-signed (bs) so an
                # inverted setup — armed in the channel between its band and the
                # mid — disarms when price closes THROUGH the mid and out the far
                # side, not merely for sitting on its own side of it.
                below_mid = below_mid + 1 if bs * (c - md[i]) < 0 else 0
                if below_mid >= cfg.invalidate_below_mid_bars:
                    armed = False
                    awaiting_reclaim = False

            if pos is None:
                ok = s * (c - d1[i]) > acc_min
                if cfg.acceptance_require_green:
                    # "Green" means with the trade: an up candle arms a long, a
                    # down candle arms a short.
                    ok = ok and s * (c - o) > 0
                if cfg.acceptance_cap_at_dev2:
                    ok = ok and s * (c - d2[i]) < 0
                if ok:
                    armed = True
                    awaiting_reclaim = False
                    # The acceptance close is beyond dev1 by MORE than acc_min, and
                    # entry_limit_offset_ticks may not exceed acc_min — so the close
                    # is strictly beyond variant A's limit however far forward it is
                    # offset. The setup is born on the far side of its own level, and
                    # the first return to it is a genuine touch.
                    beyond = True
                    below_mid = 0
                    acceptance_ts = ts.iloc[i]

            # Variant B arms its stop only after a candle actually closes back
            # through dev1 (below it on a long, above it on a short).
            if armed and cfg.entry_variant == "B" and s * (c - d1[i]) < 0:
                awaiting_reclaim = True

    bands = w.iloc[b["end_idx"].to_numpy()].reset_index(drop=True)
    return trades, vetoed, b, bands


def run_session_globex(
    cfg, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point (``session="globex"``): the same rules, read against a
    Globex-anchored VWAP, in the direction ``cfg.side`` picks (this strategy's
    config is a ``GlobexBounceConfig``, which carries that knob). A Globex-anchored
    VWAP is a separate callable rather than a config knob because a knob the engine
    could ignore would let two different-looking configs produce byte-identical
    runs; ``side`` is safe as a knob precisely because this entry point consumes
    it — long and short never share output. ``invert`` flips the band each
    direction reads (long buys the lower band, short sells the upper), which the
    engine consumes the same way."""
    return run_session(cfg, day, overnight=True, side=cfg.side, invert=cfg.invert)


def run_session_short(
    cfg: SimConfig, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point: the bounce mirrored onto the lower bands."""
    return run_session(cfg, day, side="short")


def run_session_fade(
    cfg, day: date, side: str = "short"
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Simulate one session of the band FADE (cfg is a rules.FadeConfig).

    The bounce's counter-trade: where the bounce buys the pullback to dev1
    expecting continuation to dev2, the fade sells the *return* to dev1 after
    price overextended beyond it, expecting reversion toward the mid. A "short"
    fade therefore lives on the UPPER bands — the opposite pairing from the
    bounce, which is why this loop signs band comparisons with ``u`` (the band's
    side of the market) and trade arithmetic with ``s`` (the direction), where
    the bounce's single ``s`` did both jobs.

    Its own loop rather than a mode of ``run_session`` because the two ideas
    share almost no rules — and because the bounce's loop is the replay
    authority for every stored bounce run, which a shared code path would put at
    risk with every fade change. The exit helpers are deliberately the same
    shape (stop through the traded price, resting target limit at its own
    level, the trail's tick-grid ratchet) so a fade trade and a bounce trade
    mean the same thing in every downstream table.

    The state machine (all tick-level except where a bar close is named):

      quiet --stretch past dev1 by > arm_extension_ticks-----> ARMED
      ARMED --variant A: crossing back to dev1 (+offset)-----> IN_TRADE
      ARMED --variant B: bar close back across dev1 away from
              the stretch, then the continuation through
              dev1 - entry_stop_offset----------------------> IN_TRADE
      ARMED --arm_cap_at_dev2: bar close beyond dev2---------> quiet
      IN_TRADE --stop / target / trail / time / dev1---------> quiet (rearm_after_exit)

    ``arm_stretch_side`` picks which way the arming stretch runs, and is the only
    thing in the loop it moves. "beyond" is the overextension the fade was built
    on: the stretch runs OUT of the channel (above dev1 on the short) and the
    trade sells the return down to the band. "inside" arms on the mirror — the
    stretch rips back INTO the channel (below dev1 on the short) — and the trade
    sells the retest back up to the band: the broken band, resold from
    underneath. Both are still a short at dev1 reverting to the mid, so the stop,
    the targets, the dev2 cap and the dev1 re-acceptance exit are untouched by
    the flip. Only the three rules read off the stretch itself move with it: the
    arming edge, variant A's limit offset ("in front of dev1" = toward the
    stretch), and variant B's confirming close (on the far side of dev1 from the
    stretch).

    Arming is edge-triggered: the transition into "stretched", not the standing
    state. A disarm (the dev2 cap, an exit with the price still out there) would
    otherwise be undone by the very next print of the same stretch. Re-arming
    requires price to come back within the arming distance of dev1 and push out
    again — a new stretch, not the old one still standing.

    ``arm_require_mid_cross`` remembers whether price has printed at or past the
    VWAP mid (on the channel side away from the band) since the last fill, so an
    armed stretch is one whose approach demonstrably started at the mid. The
    memory resets when a position opens: the next setup must build its own
    approach. It is read at the arming edge only — a mid touch after the stretch
    began cannot retroactively bless it.

    The targets ("mid", "opp_dev1") are tracked live like the bounce's dev2: the
    level in force at the exit is the level that fills. ``rr`` is fixed at entry.

    ``invalidate_beyond_dev1_bars`` closes re-accepted beyond dev1 against the
    position exit it at market on the next tick (reason "dev1") — re-acceptance
    out there is the bounce's entry premise, and so this trade's obituary. The
    fixed stop remains in force behind it as the hard backstop.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    # Three signs, one frame: ``s`` is the trade's direction (P&L, stops,
    # targets); ``u`` is the band's side of the market (which dev1/dev2, what
    # "beyond" means); ``a`` is the side the arming stretch runs to. The fade
    # trades against its band, so u = -s: the short fades the upper stretch, the
    # long mirror fades the lower one. And a = u for the overextension the fade
    # was built on, a = -u when it arms on the break back through the band
    # instead — the retest of dev1 from the channel side.
    s = 1.0 if side == "long" else -1.0
    u = -s
    a = u if cfg.arm_stretch_side == "beyond" else -u

    t = tickmod.get_day_ticks(tickmod.contract_for(cfg.contract, day), day)
    if t is None or t.empty:
        return [], [], pd.DataFrame(), pd.DataFrame()
    b = barmod.tick_bars(t, cfg.ticks_per_bar)
    w = vwapmod.vwap_bands(t)
    if b.empty:
        return [], [], b, pd.DataFrame()

    tick = tick_size(cfg.instrument)

    # The value edge on the setup's side: VAH above, VAL below — the fade-short
    # sits above value like the bounce-long does.
    band = "upper" if u > 0 else "lower"
    edge = "vah" if band == "upper" else "val"
    prof = edge_tick = None
    if confmod.needs_profile(cfg):
        prof = profmod.developing_profile(t, b, tick)
        edge_tick = profmod.levels_in_force(prof, b, len(t), edge=edge)

    gates = confmod.build_gates(cfg)
    if gates:
        ctx = confmod.SessionCtx(
            cfg=cfg, day=day, ticks=t, bars=b, value_edge_at_tick=edge_tick,
            profile=prof, side=side, band=band,
        )
        for g in gates:
            g.prepare(ctx)

    pv = point_value(cfg.instrument)
    risk_pts = cfg.stop_ticks * tick
    ext_pts = cfg.arm_extension_ticks * tick
    entry_off = cfg.entry_stop_offset_ticks * tick
    limit_off = cfg.entry_limit_offset_ticks * tick
    trail_dist_t = cfg.trail_stop_ticks
    trail_step_t = cfg.trail_step_ticks or trail_dist_t
    trail_be_t = cfg.trail_breakeven_ticks
    trail_be_only = cfg.trail_breakeven_only

    price = t["price"].to_numpy(dtype="float64").tolist()
    ts = t["ts_utc"]
    # The faded band and its outer sibling, plus the far dev1 the "opp_dev1"
    # target runs to. Everything downstream reads these names only.
    d1 = w["upper1" if band == "upper" else "lower1"].to_numpy().tolist()
    d2 = w["upper2" if band == "upper" else "lower2"].to_numpy().tolist()
    opp = w["lower1" if band == "upper" else "upper1"].to_numpy().tolist()
    md = w["mid"].to_numpy().tolist()

    n = len(price)
    mins = _minutes_et(ts)
    open_m = cfg.entry_open.hour * 60 + cfg.entry_open.minute
    close_m = cfg.entry_close.hour * 60 + cfg.entry_close.minute
    flat_m = cfg.flat_by.hour * 60 + cfg.flat_by.minute

    holdable = np.flatnonzero(mins < flat_m)
    force_i = int(holdable[-1]) if len(holdable) else n - 1

    bar_end_of = np.full(n, -1, dtype="int64")
    bar_end_of[b["end_idx"].to_numpy()] = np.arange(len(b))
    bar_end_of = bar_end_of.tolist()
    b_close = b["close"].to_numpy().tolist()

    armed = False
    awaiting_reclaim = False  # variant B: a bar has closed back inside dev1
    beyond = False            # variant A: price is still on the far side of the limit
    stretched = False         # the standing "beyond dev1 + extension" state
    seen_mid = False          # a print at/past the mid since the last fill
    arm_ts: pd.Timestamp | None = None
    pos: _Pos | None = None
    ghosts: list[tuple[list[str], _Pos]] = []  # (all vetoing gates, ghost entry)
    trades: list[dict] = []
    vetoed: list[dict] = []
    day_net = 0.0
    halted = False

    def _live_target(i: int) -> float:
        # The tracked target level in force at tick i; only called when
        # target_price is None, i.e. cfg.target is "mid" or "opp_dev1".
        return md[i] if cfg.target == "mid" else opp[i]

    def _row(p_: _Pos, reason: str, i: int, exit_price: float) -> dict:
        pts = s * (exit_price - p_.entry_price)
        gross = pts * pv * cfg.contracts
        comm = 2 * cfg.commission_per_side * cfg.contracts
        mfe_pts, mae_pts = _excursion(price, p_.entry_i, i, p_.entry_price, s)
        return {
            "session": day,
            "direction": "Long" if side == "long" else "Short",
            "entry_ts_utc": ts.iloc[p_.entry_i],
            "exit_ts_utc": ts.iloc[i],
            "entry_idx": p_.entry_i,
            "exit_idx": i,
            "avg_entry": p_.entry_price,
            "avg_exit": exit_price,
            "stop_price": p_.init_stop,
            "final_stop_price": p_.stop_price,
            "target_price": (p_.target_price if p_.target_price is not None
                             else _live_target(i)),
            # Peak/trough the open trade ever showed — see run_session._row.
            "mfe_points": mfe_pts,
            "mae_points": mae_pts,
            "mfe_r": mfe_pts / risk_pts,
            "mae_r": mae_pts / risk_pts,
            "exit_reason": reason,
            "points": pts,
            "r_multiple": pts / risk_pts,
            "band_width_ticks": p_.band_width_ticks,
            # The arming stretch's stamp rides in the acceptance slot: both name
            # the event that made the setup live, and every consumer (charts,
            # tables) already reads this column.
            "acceptance_ts": p_.acceptance_ts,
            "max_contracts": cfg.contracts,
            "gross_pnl": gross,
            "commission": comm,
            "net_pnl": gross - comm,
        }

    def _close(reason: str, i: int, exit_price: float) -> None:
        nonlocal pos, armed, awaiting_reclaim, day_net, halted
        assert pos is not None
        trades.append(_row(pos, reason, i, exit_price))
        day_net += trades[-1]["net_pnl"]
        if cfg.daily_loss_stop and day_net <= -cfg.daily_loss_stop:
            halted = True
        pos = None
        if cfg.rearm_after_exit:
            armed = False
            awaiting_reclaim = False

    def _exit(p_: _Pos, i: int, p: float) -> tuple[str, float] | None:
        """(reason, fill) if this position is out at tick i, else None. Shared
        by real and ghost positions, exactly as in the bounce."""
        if p_.market_exit:
            # The dev1 re-acceptance exit: a market order sent on the previous
            # bar's close, filled at the next print whatever it is.
            return (p_.market_exit, p)
        tgt = p_.target_price if p_.target_price is not None else _live_target(i)
        if s * (p - p_.stop_price) <= 0:
            moved = p_.stop_price != p_.init_stop
            return ("trail" if moved else "stop", p)
        if s * (p - tgt) >= 0:
            return ("target", tgt)
        if i >= force_i:
            return ("time", p)
        return None

    def _trail(p_: _Pos, p: float) -> None:
        # The bounce's ratchet, verbatim — see run_session._trail for why it is
        # read off the print, floored at its grid level, and run after the exit
        # check.
        if not trail_dist_t:
            return
        fav_t = round(s * (p - p_.entry_price) / tick)
        if fav_t < trail_dist_t + trail_be_t:
            return
        steps = 0 if trail_be_only else (fav_t - trail_dist_t - trail_be_t) // trail_step_t
        lvl = p_.entry_price + s * (trail_be_t + steps * trail_step_t) * tick
        if s * (lvl - p_.stop_price) > 0:
            p_.stop_price = lvl

    for i in range(n):
        p = price[i]

        for gnames, gp in ghosts[:]:
            hit = _exit(gp, i, p)
            if hit:
                vetoed.append({**_row(gp, hit[0], i, hit[1]),
                               "gate": gnames[0], "gates": "|".join(gnames)})
                ghosts.remove((gnames, gp))
            else:
                _trail(gp, p)

        if pos is not None:
            hit = _exit(pos, i, p)
            if hit:
                _close(hit[0], i, hit[1])
            else:
                _trail(pos, p)

        elif armed:
            # Variant A's limit: dev1, or entry_limit_offset_ticks in FRONT of it
            # — toward the stretch, so the return fills before the band. The
            # offset is capped at the arming stretch (schema), so the level is
            # always behind the market at the moment the setup arms, and the
            # same crossing discipline as the bounce applies: the fill is the
            # transition beyond -> at-or-through, never the standing inequality.
            # Signed with ``a``: the limit sits on the stretch's side of dev1 and
            # the return that fills it comes from there — down to the band on a
            # "beyond" arming, back up to it on an "inside" one.
            lvl = d1[i] + a * limit_off
            at_limit = a * (p - lvl) <= 0
            touched = beyond and at_limit
            beyond = not at_limit
            if not halted and open_m <= mins[i] < close_m and i < force_i:
                band_w = u * (d2[i] - d1[i]) / tick
                fill = None
                if band_w < cfg.min_band_width_ticks:
                    pass  # the touch is missed, not deferred
                elif cfg.entry_variant == "A":
                    if touched:
                        fill = lvl
                elif cfg.entry_variant == "B" and awaiting_reclaim:
                    # Stop into the continuation: past dev1, on into the channel.
                    level = d1[i] - u * entry_off
                    if u * (p - level) <= 0:
                        fill = p
                if fill is not None:
                    tp = None
                    if cfg.target == "rr" and cfg.target_rr:
                        tp = fill + s * cfg.target_rr * risk_pts
                    stop = fill - s * risk_pts
                    new_pos = _Pos(
                        entry_i=i, entry_price=fill,
                        stop_price=stop, init_stop=stop, target_price=tp,
                        band_width_ticks=band_w, acceptance_ts=arm_ts,
                    )
                    vetoes = [g.name for g in gates if not g.allows(i, fill)]
                    if not vetoes:
                        pos = new_pos
                        # The next setup must build its own approach from the mid.
                        seen_mid = False
                    else:
                        ghosts.append((vetoes, new_pos))
                        armed = False
                        awaiting_reclaim = False

        bi = bar_end_of[i]
        if bi >= 0:
            c = b_close[bi]

            # Re-acceptance beyond dev1, against the trade. Ghosts count their
            # own streaks, same as the bounce's value-area exit.
            if cfg.invalidate_beyond_dev1_bars:
                outside = u * (c - d1[i]) > 0
                for p_ in ([pos] if pos is not None else []) + [g for _, g in ghosts]:
                    p_.beyond_band = p_.beyond_band + 1 if outside else 0
                    if p_.beyond_band >= cfg.invalidate_beyond_dev1_bars:
                        p_.market_exit = "dev1"

            # The dev2 cap: an armed, unfilled setup dies on a close beyond the
            # outer band. `stretched` stays true, so re-arming needs the price
            # to come back within the arming distance and push out afresh.
            if cfg.arm_cap_at_dev2 and armed and pos is None and u * (c - d2[i]) > 0:
                armed = False
                awaiting_reclaim = False

            # Variant B's confirmation: a close back across dev1, on the far side
            # from the stretch, arms its stop — inside the band when the stretch
            # came from outside, back outside it when the stretch came from
            # within (the retest, closed above the band, is then sold on its
            # failure back down through it). The stop itself does not flip: it is
            # always entry_stop_offset_ticks into the channel, the way the trade
            # is going.
            if armed and cfg.entry_variant == "B" and a * (c - d1[i]) < 0:
                awaiting_reclaim = True

        # --- arming: a tick-level edge, evaluated last so a fresh stretch can
        # only act from the next tick onward, like any bar-close signal.
        if u * (p - md[i]) <= 0:
            seen_mid = True
        is_stretched = a * (p - d1[i]) > ext_pts
        if (is_stretched and not stretched and pos is None
                and (not cfg.arm_require_mid_cross or seen_mid)):
            armed = True
            awaiting_reclaim = False
            # Born beyond its own limit level (the offset is capped at the
            # stretch), so the first return to it is a genuine touch.
            beyond = True
            arm_ts = ts.iloc[i]
        stretched = is_stretched

    bands = w.iloc[b["end_idx"].to_numpy()].reset_index(drop=True)
    return trades, vetoed, b, bands


def run_session_fade_short(
    cfg, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point: fade the upper-band stretch from above, short."""
    return run_session_fade(cfg, day, side="short")


def run_session_fade_long(
    cfg, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point: fade the lower-band stretch from below, long."""
    return run_session_fade(cfg, day, side="long")


def run_session_profile_pullback(
    cfg, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Simulate one session of the profile pullback (cfg is a
    rules.ProfilePullbackConfig).

    The Interactions Lab's upper-band cut, traded long: rest a limit on each
    candidate developing level (NY/Globex POC/VAH, per the config), and fill
    when price pulls back onto one from above while the level sits inside the
    NY VWAP +1σ..+2σ channel. There is no arming candle and no stretch — the
    level in force *is* the setup — so this is its own loop, not a mode of the
    bounce or the fade.

    Level discipline mirrors variant A's crossing rule: a level's limit is only
    live once price has been ``rearm_ticks`` beyond it (above it — this is a
    pullback, not a breakout), and it fills on the transition to at-or-through,
    never on the standing inequality. Each crossing counts as one touch of that
    level series whether or not it filled — ``max_touches_per_level`` reads the
    series' touch count, so a first-touch config skips a level whose first
    touch happened outside the entry window rather than promoting its second
    touch to "first".

    The overnight segment is spliced in (session="globex" in the registry)
    because the Globex profile needs it; the NY VWAP bands and the NY profile
    are anchored at the bell over the RTH ticks alone, exactly as the
    Interactions study builds them. Entries and exits live in RTH only.
    """
    t = tickmod.get_day_ticks(tickmod.contract_for(cfg.contract, day), day,
                              include_overnight=True)
    if t is None or t.empty:
        return [], [], pd.DataFrame(), pd.DataFrame()

    rth_i0 = int(t["ts_utc"].searchsorted(tickmod.session_bounds_utc(day)[0], side="left"))
    if cfg.use_globex_levels and rth_i0 == 0:
        raise RuntimeError(
            f"no overnight ticks for {day} — the Globex profile cannot be built")

    b = barmod.tick_bars(t, cfg.ticks_per_bar)
    if b.empty:
        return [], [], b, pd.DataFrame()

    tick = tick_size(cfg.instrument)
    n = len(t)
    price = t["price"].to_numpy(dtype="float64").tolist()
    ts = t["ts_utc"]
    mins = _minutes_et(ts)

    # NY VWAP bands, anchored at the bell over the RTH ticks alone — NaN across
    # the overnight, where no NY band exists to be inside of.
    t_ny = t.iloc[rth_i0:].reset_index(drop=True)
    if t_ny.empty:
        return [], [], b, pd.DataFrame()
    b_ny = barmod.tick_bars(t_ny, cfg.ticks_per_bar)
    w_ny = vwapmod.vwap_bands(t_ny)
    up1 = np.full(n, np.nan)
    up2 = np.full(n, np.nan)
    up1[rth_i0:] = w_ny["upper1"].to_numpy()
    up2[rth_i0:] = w_ny["upper2"].to_numpy()
    up1 = up1.tolist()
    up2 = up2.tolist()

    # Candidate level series, one per (anchor, level type) the config trades.
    # Each is per-tick, "as known standing at that tick" (last closed bar).
    levels: list[tuple[str, list[float]]] = []
    # Each series' raw path, for the stability gate — which asks where the
    # level has BEEN, a question that reaches through the NY warmup mask: the
    # warmup gates candidacy (no limit may rest on a degenerate young profile),
    # not existence, and a VAH that has sat in place since before it became a
    # candidate really has sat there. For Globex series the two are the same.
    level_paths: list[list[float]] = []
    if cfg.use_globex_levels:
        prof_gx = profmod.developing_profile(t, b, tick)
        for edge, on in (("poc", cfg.trade_poc), ("vah", cfg.trade_vah)):
            if on:
                seg = profmod.levels_in_force(prof_gx, b, n, edge=edge).tolist()
                levels.append((f"Globex {edge.upper()}", seg))
                level_paths.append(seg)
    if cfg.use_ny_levels:
        prof_ny = profmod.developing_profile(t_ny, b_ny, tick)
        # The NY profile is degenerate while minutes old — POC/VAH collapse onto
        # the open print. Mask its levels until the anchor is warm; a limit
        # cannot rest on a level that does not meaningfully exist yet.
        warm = (mins[rth_i0:] - (mins[rth_i0] if rth_i0 < n else 0)) >= cfg.level_warmup_min
        for edge, on in (("poc", cfg.trade_poc), ("vah", cfg.trade_vah)):
            if on:
                seg = profmod.levels_in_force(prof_ny, b_ny, len(t_ny), edge=edge)
                lv = np.full(n, np.nan)
                lv[rth_i0:] = np.where(warm, seg, np.nan)
                raw = np.full(n, np.nan)
                raw[rth_i0:] = seg
                levels.append((f"NY {edge.upper()}", lv.tolist()))
                level_paths.append(raw.tolist())
    if not levels:
        raise ValueError("no candidate levels: enable at least one anchor and one level type")

    pv = point_value(cfg.instrument)
    risk_pts = cfg.stop_ticks * tick
    rearm_pts = cfg.rearm_ticks * tick
    trail_dist_t = cfg.trail_stop_ticks
    trail_step_t = cfg.trail_step_ticks or trail_dist_t
    trail_be_t = cfg.trail_breakeven_ticks
    trail_be_only = cfg.trail_breakeven_only

    open_m = cfg.entry_open.hour * 60 + cfg.entry_open.minute
    close_m = cfg.entry_close.hour * 60 + cfg.entry_close.minute
    flat_m = cfg.flat_by.hour * 60 + cfg.flat_by.minute
    holdable = np.flatnonzero(mins[rth_i0:] < flat_m) + rth_i0
    force_i = int(holdable[-1]) if len(holdable) else n - 1

    nlv = len(levels)
    armed = [False] * nlv        # price has been rearm_ticks above the level
    armed_i = [0] * nlv          # tick index of the arming print (the dwell clock)
    prev_lv = [float("nan")] * nlv
    touch_count = [0] * nlv
    ts_ns = ts.astype("int64").to_numpy().tolist()
    arm_ns = cfg.min_arm_min * 60_000_000_000
    stab_ns = cfg.min_level_stability_min * 60_000_000_000
    pos: _Pos | None = None
    pos_level = ""
    trades: list[dict] = []
    day_net = 0.0
    halted = False

    def _row(p_: _Pos, reason: str, i: int, exit_price: float) -> dict:
        pts = exit_price - p_.entry_price
        gross = pts * pv * cfg.contracts
        comm = 2 * cfg.commission_per_side * cfg.contracts
        mfe_pts, mae_pts = _excursion(price, p_.entry_i, i, p_.entry_price, 1.0)
        return {
            "session": day,
            "direction": "Long",
            "entry_ts_utc": ts.iloc[p_.entry_i],
            "exit_ts_utc": ts.iloc[i],
            "entry_idx": p_.entry_i,
            "exit_idx": i,
            "avg_entry": p_.entry_price,
            "avg_exit": exit_price,
            "stop_price": p_.init_stop,
            "final_stop_price": p_.stop_price,
            "target_price": p_.target_price,
            # Peak/trough the open trade ever showed — see run_session._row.
            "mfe_points": mfe_pts,
            "mae_points": mae_pts,
            "mfe_r": mfe_pts / risk_pts,
            "mae_r": mae_pts / risk_pts,
            "exit_reason": reason,
            "points": pts,
            "r_multiple": pts / risk_pts,
            "band_width_ticks": p_.band_width_ticks,
            # The stamp of the moment the filled level armed — when price last
            # cleared it from above, i.e. where this pullback began.
            "acceptance_ts": p_.acceptance_ts,
            "max_contracts": cfg.contracts,
            "gross_pnl": gross,
            "commission": comm,
            "net_pnl": gross - comm,
        }

    def _exit(p_: _Pos, i: int, p: float) -> tuple[str, float] | None:
        if p - p_.stop_price <= 0:
            moved = p_.stop_price != p_.init_stop
            return ("trail" if moved else "stop", p)
        if p_.target_price is not None and p - p_.target_price >= 0:
            return ("target", p_.target_price)
        if i >= force_i:
            return ("time", p)
        return None

    def _trail(p_: _Pos, p: float) -> None:
        # The bounce's ratchet, verbatim — see run_session._trail.
        if not trail_dist_t:
            return
        fav_t = round((p - p_.entry_price) / tick)
        if fav_t < trail_dist_t + trail_be_t:
            return
        steps = 0 if trail_be_only else (fav_t - trail_dist_t - trail_be_t) // trail_step_t
        lvl = p_.entry_price + (trail_be_t + steps * trail_step_t) * tick
        if lvl - p_.stop_price > 0:
            p_.stop_price = lvl

    for i in range(n):
        p = price[i]

        if pos is not None:
            hit = _exit(pos, i, p)
            if hit:
                trades.append({**_row(pos, hit[0], i, hit[1]), "level": pos_level})
                day_net += trades[-1]["net_pnl"]
                if cfg.daily_loss_stop and day_net <= -cfg.daily_loss_stop:
                    halted = True
                pos = None
                # Every exit disarms every level: arming is not tracked while a
                # position is on (this loop never reaches the level scan), so a
                # pre-entry arm surviving the trade could book a "crossing" that
                # really happened mid-hold. Price must clear a level afresh.
                armed = [False] * nlv
            else:
                _trail(pos, p)
            continue  # exits settle first; a fill can only happen from the next tick on

        # Which level series were crossed on this tick (armed -> at-or-through).
        # Every crossing consumes the series' arm and counts as a touch, whether
        # or not it may fill — eligibility must not inflate a later touch into
        # an earlier one.
        fill_k = -1
        fill_lvl = float("-inf")
        for k in range(nlv):
            lv = levels[k][1][i]
            pl = prev_lv[k]
            prev_lv[k] = lv
            if lv != lv:  # NaN — the level does not exist yet
                armed[k] = False
                continue
            # Relocation guard: the level moved up under a standing arm. The arm
            # certifies that price cleared *this* level, and a level that rose
            # since can void that. Into the approach zone (price no longer clear
            # of it by the re-arm distance) — the clearing is stale, disarm. A
            # jump that leaves price still clear re-arms against the new level:
            # the dwell clock restarts, because the pullback context it measures
            # is the new level's, not the one 150 points below.
            if armed[k] and pl == pl and lv > pl:
                if p <= lv + rearm_pts:
                    armed[k] = False
                elif lv - pl > rearm_pts:
                    armed_i[k] = i
            if armed[k] and p <= lv:
                armed[k] = False
                if i < rth_i0:
                    # An overnight crossing consumes the arm (price really did
                    # come back to the level) but is not a touch: the study
                    # counts touches within the RTH session, and letting the
                    # night spend a Globex level's first touch would disable
                    # every Globex level under a first-touch config.
                    continue
                if i == 0 or price[i - 1] <= lv:
                    # The level crossed price, not the other way around — a
                    # VA-snap, the profile's value area rebuilt over the market.
                    # No resting limit was touched: the old limit sat below a
                    # market that never came down to it, and a limit at the new
                    # level would be born marketable. The study scored these as
                    # their own event class (trend ratification, not a
                    # pullback), so the setup disarms without a fill and price
                    # must clear the level afresh.
                    continue
                if arm_ns and ts_ns[i] - ts_ns[armed_i[k]] < arm_ns:
                    # Price cleared the level only moments ago — this dip is the
                    # same rotation continuing, not a fresh pullback. The study's
                    # touch-gap rule scores it as part of the previous touch, so
                    # it neither fills nor spends a touch count.
                    continue
                touch_count[k] += 1
                ok = (not halted and open_m <= mins[i] < close_m and i < force_i
                      and (not cfg.max_touches_per_level
                           or touch_count[k] <= cfg.max_touches_per_level))
                if ok and cfg.require_upper_band:
                    ok = up1[i] <= lv <= up2[i]
                if ok and cfg.min_band_width_ticks:
                    # The upper channel must be at least this wide; a NaN band
                    # (no NY VWAP here) fails the compare and vetoes, which is
                    # correct — fills only ever happen in RTH where it exists.
                    ok = up2[i] - up1[i] >= cfg.min_band_width_ticks * tick
                if ok and cfg.require_confluence_pts:
                    ok = any(
                        m != k and levels[m][1][i] == levels[m][1][i]
                        and abs(levels[m][1][i] - lv) <= cfg.require_confluence_pts
                        for m in range(nlv)
                    )
                if ok and stab_ns:
                    # The level must have SAT here: within rearm_ticks of the
                    # fill value on every tick of the last
                    # min_level_stability_min minutes, read off the series' raw
                    # path (the warmup mask gates candidacy, not existence). A
                    # level that relocated up to price more recently than that
                    # is the profile chasing the market, and the scan back from
                    # the fill is what catches it — the standing value alone
                    # can't, because a relocated VAH looks identical to a
                    # defended one. The walk stops at the first tick old enough
                    # to certify the duration (or the first violation), so it
                    # is bounded by the window, not the day.
                    lvs = level_paths[k]
                    cut = ts_ns[i] - stab_ns
                    j = i - 1
                    while (j >= 0 and ts_ns[j] > cut
                           and lvs[j] == lvs[j] and abs(lvs[j] - lv) <= rearm_pts):
                        j -= 1
                    ok = (j >= 0 and ts_ns[j] <= cut
                          and lvs[j] == lvs[j] and abs(lvs[j] - lv) <= rearm_pts)
                # Stacked levels crossed on one tick: the highest is the first
                # limit price reached on the way down, so it is the fill.
                if ok and lv > fill_lvl:
                    fill_k, fill_lvl = k, lv
            elif p > lv + rearm_pts:
                if not armed[k]:
                    armed_i[k] = i
                armed[k] = True

        if fill_k >= 0:
            fill = fill_lvl
            tp = (fill + cfg.target_rr * risk_pts if cfg.target == "rr"
                  else fill + cfg.target_ticks * tick)
            stop = fill - risk_pts
            width = (up2[i] - up1[i]) / tick
            pos = _Pos(
                entry_i=i, entry_price=fill,
                stop_price=stop, init_stop=stop, target_price=tp,
                band_width_ticks=width if width == width else 0.0,
                acceptance_ts=ts.iloc[armed_i[fill_k]],
            )
            pos_level = levels[fill_k][0]

    bands = w_ny.iloc[b_ny["end_idx"].to_numpy()].reset_index(drop=True)
    return trades, [], b, bands


def finalize(rows: list[dict], cfg: SimConfig) -> pd.DataFrame:
    """Raw session rows -> the ordered, numbered trade frame runs are stored as."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("entry_ts_utc").reset_index(drop=True)
    df.insert(0, "trade_no", np.arange(1, len(df) + 1))
    # The contract each trade actually traded, not the config's — under a rolling
    # config those differ, and a trade that can't name its own contract can't be
    # priced or reconciled against a broker fill.
    df["instrument"] = [
        tickmod.contract_for(cfg.contract, ts.tz_convert(ET_TZ).date())
        for ts in df["entry_ts_utc"]
    ]
    df["duration_s"] = (df["exit_ts_utc"] - df["entry_ts_utc"]).dt.total_seconds()
    df["entry_ts_local"] = df["entry_ts_utc"].dt.tz_convert(ET_TZ)
    df["exit_ts_local"] = df["exit_ts_utc"].dt.tz_convert(ET_TZ)
    return df


def run(cfg: SimConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate every session in the config window -> (trades, vetoed) frames.

    Raises if a weekday returns no ticks: a sim that silently skips a session
    would report metrics over a window it never actually tested.
    """
    rows: list[dict] = []
    veto_rows: list[dict] = []
    tickmod.ensure_roll_map(cfg.contract, cfg.start_date, cfg.end_date)
    for day in tickmod.session_dates(cfg.start_date, cfg.end_date):
        if tickmod.market_closed(cfg.contract, day):
            continue  # a probe-confirmed holiday, not a skipped session
        t, v, _, _ = run_session(cfg, day)
        if not len(tickmod.get_day_ticks(tickmod.contract_for(cfg.contract, day), day)):
            raise RuntimeError(f"no ticks for {day} — cannot report metrics over this window")
        rows.extend(t)
        veto_rows.extend(v)
    return finalize(rows, cfg), finalize(veto_rows, cfg)
