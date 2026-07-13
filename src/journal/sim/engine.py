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
    # That streak hit its limit: exit at market on the next tick.
    exit_on_next_tick: bool = False


def _minutes_et(ts: pd.Series) -> np.ndarray:
    et = ts.dt.tz_convert(ET_TZ)
    return (et.dt.hour * 60 + et.dt.minute).to_numpy()


def run_session(
    cfg: SimConfig, day: date, overnight: bool = False, side: str = "long"
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Simulate one session. Returns (trades, vetoed, bars, per-bar bands).

    ``side`` picks the direction the band bounce is read in: "long" bounces the
    upper bands (accept above dev1, buy the pullback, target dev2), "short"
    mirrors it exactly onto the lower bands (accept below dev1, sell the
    pullback, target dev2 beneath). See the module docstring for how the
    long-flavoured config knobs read on a short.

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
    # pay. The edge that matters is the one the trade sits *beyond*: VAH for a
    # long from above value, VAL for a short from below it.
    edge = "vah" if side == "long" else "val"
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
            profile=prof, side=side,
        )
        for g in gates:
            g.prepare(ctx)

    pv = point_value(cfg.instrument)
    risk_pts = cfg.stop_ticks * tick
    acc_min = cfg.acceptance_min_ticks * tick
    entry_off = cfg.entry_stop_offset_ticks * tick
    limit_off = cfg.entry_limit_offset_ticks * tick
    trail_pts = cfg.trail_step_ticks * tick

    price = t["price"].to_numpy(dtype="float64").tolist()
    ts = t["ts_utc"]
    # dev1/dev2 on the side being traded: the upper bands for a long, the lower
    # for a short. Everything downstream reads these two names only.
    d1 = w["upper1" if side == "long" else "lower1"].to_numpy().tolist()
    d2 = w["upper2" if side == "long" else "lower2"].to_numpy().tolist()
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
    ghosts: list[tuple[str, _Pos]] = []  # (gate name, vetoed entry being tracked)
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
        gross = pts * pv * cfg.contracts
        comm = 2 * cfg.commission_per_side * cfg.contracts
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
            "avg_entry": p_.entry_price,
            "avg_exit": exit_price,
            # The stop as entered (what was risked) and where the ratchet had
            # moved it by the exit. Without trailing the two are equal.
            "stop_price": p_.init_stop,
            "final_stop_price": p_.stop_price,
            "target_price": p_.target_price if p_.target_price is not None else d2[i],
            "exit_reason": reason,
            "points": pts,
            "r_multiple": pts / risk_pts,
            "band_width_ticks": p_.band_width_ticks,
            "acceptance_ts": p_.acceptance_ts,
            "max_contracts": cfg.contracts,
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
        if p_.exit_on_next_tick:
            # A market order sent on the previous bar's close. It fills at the
            # next print, whatever that print is — including one straight through
            # the stop. No free lunch for having decided to leave.
            # Named "vah" for both sides: the reason is one rule (price was
            # re-accepted back inside value), and renaming it per side would fork
            # every consumer that groups exits by reason.
            return ("vah", p)
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
        """Ratchet the stop on a print that extends the run. N steps in favour
        put the stop at entry + (N-1) steps, so the first step buys breakeven.

        Read off the current print rather than a stored high-water mark: the stop
        is monotone in the trade's direction, so a pullback simply computes a
        level we refuse to move back to. Same reason it is safe to run this
        *after* the tick's exit check — the ratchet only ever fires on a print
        that is beyond the stop it installs, so the new stop can never be hit by
        the print that created it, and the old stop stays in force for exactly
        the ticks it was really resting under."""
        if not trail_pts:
            return
        steps = int(s * (p - p_.entry_price) / trail_pts)
        if steps < 1:
            return
        lvl = p_.entry_price + s * (steps - 1) * trail_pts
        if s * (lvl - p_.stop_price) > 0:
            p_.stop_price = lvl

    for i in range(n):
        p = price[i]

        # Ghost positions: vetoed entries ride the same exit rules as a real
        # position (but never touch the arm/entry state) so the vetoed row
        # carries a would-be P&L, not just "an entry happened here".
        for gname, gp in ghosts[:]:
            hit = _exit(gp, i, p)
            if hit:
                vetoed.append({**_row(gp, hit[0], i, hit[1]), "gate": gname})
                ghosts.remove((gname, gp))
            else:
                _trail(gp, p)

        if pos is not None:
            hit = _exit(pos, i, p)
            if hit:
                _close(hit[0], i, hit[1])
            else:
                _trail(pos, p)

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
                # Signed so the width is the distance between the bands, positive
                # on both sides — a short's dev2 sits *below* its dev1.
                band_w = s * (d2[i] - d1[i]) / tick
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
                    )
                    veto = next((g.name for g in gates if not g.allows(i, fill)), None)
                    if veto is None:
                        pos = new_pos
                    else:
                        # Consume the setup exactly as a real entry would have —
                        # leaving it armed would re-fire the same (vetoed) entry
                        # on the very next tick.
                        ghosts.append((veto, new_pos))
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
                # Back inside value: below VAH for a long, above VAL for a short.
                inside = not np.isnan(lvl) and s * (c - lvl) < 0
                for p_ in ([pos] if pos is not None else []) + [g for _, g in ghosts]:
                    p_.inside_value = p_.inside_value + 1 if inside else 0
                    if p_.inside_value >= cfg.exit_below_vah_bars:
                        p_.exit_on_next_tick = True

            if cfg.invalidate_below_mid_bars:
                # Closes on the wrong side of the mid: below it for a long, above
                # it for a short.
                below_mid = below_mid + 1 if s * (c - md[i]) < 0 else 0
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
    cfg: SimConfig, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point (``session="globex"``): the same rules, read against a
    Globex-anchored VWAP. A separate callable rather than a config knob because a
    knob the engine could ignore would let two different-looking configs produce
    byte-identical runs."""
    return run_session(cfg, day, overnight=True)


def run_session_short(
    cfg: SimConfig, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point: the bounce mirrored onto the lower bands."""
    return run_session(cfg, day, side="short")


def run_session_globex_short(
    cfg: SimConfig, day: date
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Registry entry point: the lower-band bounce off a Globex-anchored VWAP."""
    return run_session(cfg, day, overnight=True, side="short")


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
