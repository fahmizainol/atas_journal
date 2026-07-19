"""The knob-boards.

Every tunable of a strategy lives here, so a new experiment is a new config
rather than a new branch of engine code. The point of the whole exercise is to
ask "does adding filter X improve the base rule?" — that question is only cheap
to ask if X is a field, not an edit.

One config class per *family* of ideas, and only the knobs that family's engine
actually reads: the bounce strategies share SimConfig, the fades share
FadeConfig. A knob an engine ignored would let two different-looking configs
produce byte-identical runs — the same disease the registry's ``session``
attribute exists to prevent — so an idea with different rules gets its own
class rather than a wider shared one. Which class a strategy uses is declared
on its registry entry (``config_cls``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, time


class _JsonMixin:
    def to_json(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, (date, time)):
                d[k] = v.isoformat()
        return d


@dataclass(frozen=True)
class SimConfig(_JsonMixin):
    # --- scope ---
    instrument: str = "NQ"
    # "NQ" rolls to the front month per session; "NQZ5" pins one contract for the
    # whole window. Pinning is what every run made before the roll existed did, and
    # those runs must still replay to the trades they reported — so it stays.
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 31)   # no entries before this — skip the open's impulse
    entry_close: time = time(16, 0)  # no new entries after this
    flat_by: time = time(16, 0)      # force-exit any open position

    # --- acceptance (arms the setup) ---
    acceptance_min_ticks: int = 30       # close must be > this far above dev1
    acceptance_require_green: bool = True
    acceptance_cap_at_dev2: bool = False  # if True, close must also be below dev2

    # --- entry ---
    # "A": rest a buy limit at dev1, fills on a touch.
    # "B": wait for a bar to CLOSE below dev1, then a buy stop dev1 + offset.
    entry_variant: str = "A"
    entry_stop_offset_ticks: int = 10    # variant B only
    # 0 = at dev1. Variant A only: rest the limit this many ticks in FRONT of dev1
    # (above it on a long, below it on a short), so the pullback fills before it
    # reaches the band. Capped at acceptance_min_ticks by schema — the acceptance
    # close is only that far beyond dev1, so a limit further out than that would
    # already be through the market when the setup arms, which is a marketable
    # limit, not a resting one.
    entry_limit_offset_ticks: int = 0

    # --- exit ---
    stop_ticks: int = 75
    target: str = "dev2"                 # "dev2" | "rr"
    target_rr: float | None = None       # used when target == "rr"
    # 0 = off. N consecutive bar closes back below the developing VAH exit the
    # position at market. Not a target — the trade is long from above value, so
    # VAH sits *below* the entry; this is an invalidation, the profile-side twin
    # of invalidate_below_mid_bars. Fills on the tick after the close, like any
    # market order a close could have triggered.
    exit_below_vah_bars: int = 0
    # 0 = off, and the trail's master switch. How far behind the best price the
    # trade has seen the stop wants to sit. Nothing moves until the trade is this
    # far in front: the first ratchet lands the stop on the entry, so the trail
    # can only ever buy breakeven or better, never a tightened loss.
    trail_stop_ticks: int = 0
    # The grid the trailed stop is allowed to rest on, measured from the entry.
    # The stop keeps trail_stop_ticks behind the high-water price but only moves
    # in whole steps, so with a 50-tick trail and a 25-tick step it goes to
    # breakeven at +50, +25 at +75, +50 at +100 — always 50 to 74 ticks behind.
    # 0 = one step per trail distance (the trail moves in single clicks of
    # trail_stop_ticks), which is what the trail did before the two decoupled.
    # Ignored when trail_stop_ticks is 0. The stop only ever moves toward the
    # trade — a pullback never loosens it.
    trail_step_ticks: int = 0
    # Where the trail's first click lands, in ticks beyond the entry — and so the
    # origin of the whole step grid. 0 puts it on the entry itself, which is only
    # breakeven *gross*: the round trip still owes commission, so a stop there
    # books a loss of exactly that. Set it far enough past the entry to pay the
    # round trip (on NQ a tick is $5, so a $14 round trip needs 3, and a 4th
    # covers the stop's fill-through) and a scratch is really a scratch.
    # Ignored when trail_stop_ticks is 0.
    trail_breakeven_ticks: int = 0
    # Take the first click and no other: the stop moves to the scratch level once
    # the trade is trail_stop_ticks in front of it, and then never again. That is
    # a breakeven stop rather than a trail, and it is its own rule — a trail that
    # keeps ratcheting hands back open profit on every pullback, which is exactly
    # what a breakeven stop refuses to do. The step is then irrelevant (there is
    # no second step to take). Ignored when trail_stop_ticks is 0.
    trail_breakeven_only: bool = False
    # 0 = off. Read the tape ONCE, when the fill is panic_exit_window_s old: if
    # the net aggressor delta over that window (buy volume minus sell volume,
    # signed against the trade) ran this many contracts against the position,
    # exit at market. A single read of the whole window, not a running trigger,
    # and the distinction is worth $31k on the baseline: the per-tick variant
    # (leave the moment the running delta touches the threshold) also fires on
    # the transient spike that recovers within the minute — the signature of
    # this strategy's winners, which routinely start ugly — and A/B'd $23k
    # UNDER the baseline where the one-shot read came out ahead. This is a flow
    # SHOCK detector, not a loser detector: set it far beyond a normal minute
    # of tape (the 9b7f54c6 study: every exit rule tuned to the typical loser
    # tested net-negative; only the violent, unrecovered tail marked fills that
    # were nearly always dead). Fires like any market order: on the tick after
    # the read.
    panic_exit_delta: int = 0
    # The window the one read covers, in seconds after the fill. The shock
    # decays fast — the same delta read two minutes in tested negative (the
    # price has already paid most of the stop by then) — so the window stays
    # tight. A position that exits before the window closes is never read.
    # Ignored when panic_exit_delta is 0.
    panic_exit_window_s: int = 60
    # 0 = off. One-shot read at underwater_stop_after_s seconds after the fill (the
    # same single-read design the panic exit settled on, not a per-tick trigger): if
    # the position is CURRENTLY underwater then, pull the stop in to this many ticks
    # behind the entry. It caps the loss on a trade that hasn't worked without
    # flattening it — a genuine deep-heat winner still has room to recover — and only
    # ever tightens (never loosens, never past a trail that has already moved further).
    # Targets the 60-180s dwell band the underwater study found the book bleeds in;
    # tightened-stop exits book under their own reason, "uw_stop". Set it below
    # stop_ticks or it can never bite.
    underwater_stop_ticks: int = 0
    # Seconds after the fill the one read is taken. Ignored when underwater_stop_ticks
    # is 0. The dwell study put the bleed at 60-180s, so the read wants to sit at the
    # front of that band — early enough to save the loss, late enough that the fast
    # winners (the first-minute cohort that carries the book) have already resolved.
    underwater_stop_after_s: int = 60
    # 0 = off. N consecutive bar closes at least stop_below_mid_ticks past the NY
    # session VWAP mid — anchored at the 09:30 bell, not the Globex open the bands
    # ride — exit the position at market ("mid_exit"). Below the mid on a long,
    # above on a short: a trade entered beyond the band that closes back through
    # the day's mean has lost its premise. The profile-side twin of
    # exit_below_vah_bars, and like it fills on the tick after the close. The
    # a0512f69 counterfactual wanted this CONFIRMED and OFFSET (2-3 closes, a few
    # ticks past): a bare touch of the mid clips the winners that pull back
    # through it before running; a streak past it cuts the slow bleed losers
    # without them.
    stop_below_mid_bars: int = 0
    # How far past the NY mid a close must sit to count toward the streak, in
    # ticks. 0 = any close on the far side. The offset is the winner filter — it
    # ignores the one-tick poke through the mean that recovers. Ignored when
    # stop_below_mid_bars is 0.
    stop_below_mid_ticks: int = 0

    # --- filters / lifecycle ---
    min_band_width_ticks: int = 0        # 0 = off. Skip entry if dev2-dev1 is tighter.
    invalidate_below_mid_bars: int = 5   # 0 = off. N consecutive closes below VWAP mid disarms.
    rearm_after_exit: bool = True        # any exit disarms; a fresh acceptance is required
    # Only a stop re-arms the day: any exit other than a full stop-out (trail,
    # target, time, panic, re-acceptance inside value) stands the session down.
    # The stand-down watches rather than blinds: would-be entries ride the exit
    # rules as "reentry_halt" ghosts in the missed rows, and a ghost that stops
    # out lifts the halt — the setup failed without us, and the next acceptance
    # is tradeable again. The premise: follow-ups to a booked win or scratch
    # don't earn their commission, while the re-entry after a swept stop is
    # where the outsized winners live.
    reenter_after_stop_only: bool = False
    # 0 = no clock: a stop (real or watched) re-arms the day until the next
    # non-stop exit. Set, it is how many minutes the re-arm stays open — the
    # study's re-entries decayed with the wait (≤15min avgR +0.83 vs +0.46
    # overall), so the window trades coverage for concentration. The clock
    # starts at the stop print; if no entry fills before it runs out, the day
    # stands back down (a later watched stop starts a fresh window). Only read
    # when reenter_after_stop_only is on.
    reentry_rearm_window_min: int = 0
    # 0 = off. Once the session's *realized* net P&L (closed trades, commissions
    # included) is this many dollars in the red, no further entries that day. An
    # open position still runs to its normal exit — the governor halts new risk,
    # it never touches a trade already on.
    daily_loss_stop: float = 0.0
    # False = off. Extends daily_loss_stop to the trade already ON: every tick,
    # once the day's realized net plus the open position marked to the current
    # print reaches the limit, the position leaves at market (reason
    # "daily_loss"). The bare stop only refuses new ENTRIES after a close, so a
    # single trade whose own stop sits wider than the whole daily limit blows
    # straight through it — the exact hole this closes. Reads the same
    # daily_loss_stop dollar figure (schema requires one set); a market order
    # like the panic/vah exits, filled on the next print. Off (the default) never
    # arms and rides the base rule path, so it simulates identically to a run
    # without it.
    daily_loss_exit_open: bool = False

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- pyramid (scale-in) ---
    # 1 = off: the whole `contracts` size fills at once, as a single all-in touch.
    # N > 1 splits the position into N equal lots (contracts must divide by N, the
    # schema enforces it): the FIRST lot fills exactly as the base variant does
    # (variant A's limit at dev1, variant B's stop on the reclaim), and each later
    # lot rests a stop pyramid_step_ticks further in the trade's favour, off the
    # first fill's grid — so size is added only as the move confirms (or, with
    # pyramid_direction "against", a limit one step further against it — averaging
    # down). A lot whose trigger is never reached simply never fills: a trade that
    # rejects straight off dev1 keeps only its first lot, while one that runs
    # reaches full size. That asymmetry — small losers, big winners — is the whole
    # point of scaling in over an all-in fill, and it rides the base rule path, so
    # a config that leaves it at 1 simulates identically to one without the knob.
    pyramid_tranches: int = 1
    # The favourable distance between one lot's fill grid and the next: lot 2's
    # stop sits this far beyond the first fill, lot 3 twice as far, and so on.
    # Read only when pyramid_tranches > 1.
    pyramid_step_ticks: int = 40
    # How the one shared stop and target track as lots are added:
    #   "blend"  — re-struck off the running average entry on every add, so the
    #              stop stays stop_ticks behind the average and each lot risks the
    #              same. Total dollar risk grows with size, but the R the trade is
    #              measured against is always the blended basis.
    #   "anchor" — left where the first lot set them. Later lots ride on the
    #              initial risk (they are house money once price has run to them),
    #              but a full stack that reverses to the first lot's stop books a
    #              larger adverse excursion on the lots added higher up.
    # Read only when pyramid_tranches > 1.
    pyramid_stop_mode: str = "blend"
    # Which way the later lots' grid walks off the first fill:
    #   "with"    — the scale-in above: each lot stops in one step further in the
    #               trade's favour, size is added only as the move confirms.
    #   "against" — averaging down: each lot rests a LIMIT one step further
    #               against the trade (below the fill on a long), so size is added
    #               into the pullback's depth and the blended basis improves as
    #               the trade goes underwater. The landing-depth study's static
    #               read of this (July 2026) cut net 26-52% — the shallow winners
    #               that carry the PnL never reach the adds — so the knob exists
    #               to let the engine A/B say it properly, not because it is
    #               expected to win. With "anchor" the stop stays the first lot's
    #               (later lots risk less than stop_ticks); "blend" re-strikes it
    #               off the falling average, i.e. the stop WIDENS on every add —
    #               a martingale, priced honestly but sized dangerously.
    # Read only when pyramid_tranches > 1.
    pyramid_direction: str = "with"

    # --- big-lot participation size-up (order-flow study, run 30badf94) ---
    # 0 = off. At each fill, measure big-lot PARTICIPATION over the trailing
    # biglot_window_s: the share of that window's total traded volume printed in
    # orders of biglot_min_size lots or more — side-agnostic, a composition read of
    # the tape, not a signed delta. If the share is at least this fraction the entry
    # is sized to size_up_contracts instead of the base `contracts`; below it the
    # base size stands. The 30badf94 study found this the one entry-time separator
    # of the run's 3R runners from its scratches (rank-AUC 0.66, split-half and
    # within-session robust) — and a MAGNITUDE signal, not a win/loss one, so it
    # sizes rather than gates. Rides the base rule path: 0 sizes every fill at
    # `contracts` and simulates identically to a run without the knob. Not supported
    # with the pyramid (the later lots would add at the base size) — the engine
    # refuses the combo rather than size it wrong.
    size_up_participation: float = 0.0
    # The size a qualifying fill takes, in contracts. Read only when
    # size_up_participation > 0; a positive whole number.
    size_up_contracts: int = 0
    # A print of this many lots or more counts toward the big-lot numerator — the
    # study's definition of an institutional-size NQ print. Read only when
    # size_up_participation > 0.
    biglot_min_size: int = 10
    # The trailing window the participation share is read over, in seconds ending at
    # the fill. 60 is the study's window — the one-minute read was the split-half-
    # robust one; the three- and five-minute reads leaned second-half. Read only
    # when size_up_participation > 0.
    biglot_window_s: int = 60

    # --- confluences (veto-only gates) ---
    # One namespaced section per gate, e.g. {"volume_profile": {"enabled": true, ...}}.
    # A gate may only VETO an entry the base rules would take; anything that
    # changes what/when/how we enter or exit is a new strategy, not a gate.
    confluences: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GlobexBounceConfig(SimConfig):
    """The Globex bounce, tradeable in either direction.

    Every rule is SimConfig's — this adds one knob, ``side``, the direction the
    band bounce is read in. It is its own class rather than a field on SimConfig
    because the RTH bounces fix their direction in their engine entry point
    (``run_session`` for the long, ``run_session_short`` for the short): a
    ``side`` they ignored would let two different-looking configs produce
    byte-identical runs — the disease the per-family config split and the
    registry's ``session`` attribute both exist to prevent. Here the Globex
    entry point actually consumes it, so the knob is real. The RTH upper/lower
    bounces keep their long-only confluence gates; only ``volume_profile`` (which
    mirrors by direction) rides along on this bidirectional strategy.
    """

    # "long" bounces the upper Globex bands (accept above dev1, buy the pullback
    # to it, target dev2); "short" mirrors onto the lower bands (accept below
    # dev1, sell the pullback, target the lower dev2). The engine reads every
    # comparison off a signed frame, so the long-flavoured knob names
    # (acceptance_require_green, invalidate_below_mid_bars, exit_below_vah_bars)
    # mean their mirror on a short.
    side: str = "long"
    # Invert the band each direction reads. Off: the bounce as above (long→upper,
    # short→lower), running WITH the break toward dev2. On: long reads the LOWER
    # band (buy the pullback into support), short reads the UPPER (sell the rally
    # into resistance) — the same entry price at dev1, the opposite direction,
    # reverting toward the mid. dev2 then sits behind the trade, so an inverted
    # run cannot target it: target must be "rr" and acceptance_cap_at_dev2 must be
    # off (the schema enforces both). The value-area edge, its exit and the mid
    # invalidation follow the band, not the direction, so they stay coherent.
    invert: bool = False


@dataclass(frozen=True)
class ProfilePullbackConfig(_JsonMixin):
    """The Interactions Lab's upper-band pullback cut, traded.

    Long the pullback-from-above onto a developing profile level — the NY or
    Globex POC/VAH — while price holds the NY VWAP +1σ..+2σ channel. The
    research behind it (NQ Jun 2025 – Jan 2026): that cut rejects ~68–73% at
    30m with median MFE/MAE ~38/24 on first touches, against a 60.5%/symmetric
    null; the same levels outside the channel are noise, and 15:00+ touches
    score exactly at null (hence the 15:00 entry_close default). No day-type
    filter is offered on purpose — no pre-known feature predicted the bounce,
    and the trend-day label is partly *defined by* these pullbacks holding.
    The stop is the whole risk story: trend-down days run a median ~89 pts
    against this entry, and the fixed stop is what caps them.

    Its own class because it shares no rules with the bounce or the fade: no
    acceptance candle, no arming stretch — the level itself is the setup, and
    a resting limit at the level in force is the entry.

    There is deliberately no channel re-acceptance rule (disarm when price
    falls back inside the middle band, re-arm only once it has held above +1σ
    again): measured over this run's own window, winners had LESS time above
    +1σ before the fill than losers (median 0.6 vs 1.3 min), and requiring
    even 2 minutes of standing acceptance inverted the edge (PF 1.54 -> 0.51).
    It is min_arm_min's lesson read from the price side — the profitable fill
    is the fast pullback, and any rule that demands the setup be "established"
    first selects the grinds instead.
    """

    # --- scope ---
    instrument: str = "NQ"
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 45)   # the NY levels' warm-up; earlier touches are the open, not a level
    entry_close: time = time(15, 0)  # 15:00+ touches scored exactly at the null baseline
    flat_by: time = time(16, 0)

    # --- levels (what a limit may rest on) ---
    use_ny_levels: bool = True       # the session's own developing profile
    use_globex_levels: bool = True   # the overnight profile — mature by the bell
    trade_poc: bool = True
    trade_vah: bool = True
    # An NY-anchored profile is degenerate while it is minutes old (POC=VAH=VAL
    # ≈ the open print); its levels are not candidates until the anchor is this
    # old. Globex levels are ~15h old at the bell and are never gated by this.
    level_warmup_min: int = 15

    # --- setup (what makes a touch a trade) ---
    # The cut's core condition: the level must sit inside the NY VWAP +1σ..+2σ
    # channel at the fill. Off = trade every pullback onto a level, which the
    # research showed is baseline noise — the switch exists to measure that.
    require_upper_band: bool = True
    # Price must clear the level by this much before a new touch of it can fill
    # — the sim analog of the study's touch-gap rule, so a rotation sitting on
    # the level reads as one touch, not many.
    rearm_ticks: int = 8
    # The touch-gap rule's other half, in time: a crossing may only fill (or
    # count as a touch) if price cleared the level at least this many minutes
    # ago; a level that relocates under price restarts the clock. 0 = off, and
    # the measured default: even a 1-minute requirement inverted the edge
    # in-sample (PF 1.54 -> 0.96), because the profitable fill is the FAST
    # pullback that instantly rejects — an aged arm fills a later rotation
    # where price is grinding along the level instead. The knob exists to keep
    # that measurable, not because it should be on.
    min_arm_min: int = 0
    # 0 = every touch may fill. 1 = first touch only (the study's strongest
    # sub-cut); N allows the first N touches of each level series per session.
    max_touches_per_level: int = 0
    # 0 = off. Require another active candidate level (a different series)
    # within this many points of the fill — the study's "2+ sources stacked"
    # cut, whose median MAE was the tightest of any conditioner.
    require_confluence_pts: float = 0.0

    # --- exit ---
    stop_ticks: int = 70             # 17.5 pts — sized to the cut's ~24-pt median MAE budget
    target: str = "rr"               # "rr" | "ticks"
    target_rr: float | None = 1.5
    target_ticks: int = 100          # 25 pts; used when target == "ticks"
    # The trail: same rule and same knobs as the bounce's (see SimConfig).
    trail_stop_ticks: int = 0
    trail_step_ticks: int = 0
    trail_breakeven_ticks: int = 0
    trail_breakeven_only: bool = False

    # --- filters / lifecycle ---
    # 0 = off. Skip the fill when the NY VWAP +1σ..+2σ channel the level sits in
    # (up2-up1) is tighter than this. A pinched upper band means dev1 and dev2
    # are nearly on top of each other — the "inside the channel" condition
    # require_upper_band tests is then trivially met by any level near the band,
    # and the pullback has no room to run. Independent of require_upper_band: the
    # width is the channel's, measured whether or not the inside-band gate is on.
    min_band_width_ticks: int = 0
    # 0 = off. Skip the fill unless the level has sat within rearm_ticks of its
    # fill value for at least this many minutes. A VAH that relocated up under
    # price moments before the touch is the profile chasing the market, not a
    # level anyone defended — measured on this run's window, fills on levels
    # stable under 2/3 minutes lost while the stable-level fills carried the
    # edge (PF 1.54 -> 1.73/1.97 at 2/3 min, win% 52.5 -> 55.6/58.5). The
    # tolerance is rearm_ticks on purpose: the distance that counts as price
    # being clear of the level is the distance that counts as the level having
    # moved.
    min_level_stability_min: int = 0
    daily_loss_stop: float = 0.0     # 0 = off. Same governor as the bounce's.
    # False = off. Also flatten the open trade at the daily loss stop — see
    # SimConfig.daily_loss_exit_open. Needs a daily_loss_stop set.
    daily_loss_exit_open: bool = False

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ValueRotationConfig(_JsonMixin):
    """The value rotation's knobs: re-acceptance into value, traded to the POC.

    The balance-day idea the loss studies kept pointing at from the other side:
    85% of the upper-band bounce's stopped losses complete the rotation to the
    developing POC within 30 minutes — losses are completed value rotations.
    This trades the rotation itself: price is accepted OUTSIDE value (beyond
    the developing VAH on the short, below the VAL on the long), then a bar
    close re-accepts it back inside — the edge has failed — and the trade runs
    with the rotation toward the developing POC.

    Its own class because it shares no rules with the band strategies: no VWAP
    band in the setup at all — the value-area edge arms it, bar closes inside
    value confirm it, and the POC is the target. The Interactions Lab's two
    deflation lessons are built in as knobs rather than remembered as caveats:
    ``min_room_ticks`` refuses the trivial rotation (a POC already at the edge
    — ~40% of the lab's POC "snap reversions" were price already at/through
    the target), and the crossing discipline on the live POC target books a
    node-flip across price as a market fill at the print, never as a limit
    fill at a level the market wasn't at (profile-pullback's v3 lesson).

    Knob names are written for the short off the VAH (the first direction
    registered) and mean the mirror on the long off the VAL, exactly as the
    fade's short-flavoured names do.
    """

    # --- scope ---
    instrument: str = "NQ"
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 45)   # the NY profile's warm-up; earlier "edges" are the open print
    entry_close: time = time(16, 0)
    flat_by: time = time(16, 0)

    # --- direction ---
    # "short": price accepted above the developing VAH, re-accepted back inside,
    #          sold toward the POC below.
    # "long":  the mirror off the VAL — accepted below value, re-accepted back
    #          inside, bought toward the POC above.
    # A config knob (not a per-slug entry point) because one engine loop reads a
    # signed frame and genuinely consumes it, like GlobexBounceConfig.side.
    side: str = "short"

    # --- arming (accepted outside value) ---
    # The setup arms when price prints more than this far beyond the value-area
    # edge — outside value, on the trade's side. Edge-triggered like the fade's
    # stretch: a disarm is only undone by a fresh excursion, never by the old
    # one still standing.
    arm_beyond_ticks: int = 20
    # An NY-anchored profile is degenerate while it is minutes old (POC=VAH=VAL
    # ≈ the open print); the edge cannot arm and nothing may fill until the
    # session is this old. entry_open already sits past the default; the knob
    # exists so widening the window cannot silently trade the open print.
    level_warmup_min: int = 15
    # Re-acceptance: this many CONSECUTIVE bar closes back inside the edge
    # confirm that value has taken price back — the rotation premise. 1 is the
    # bare close; more demands the re-entry hold.
    accept_inside_bars: int = 1

    # --- entry ---
    # "A": after the confirming close(s), rest a limit at the edge and fill on
    #      the retest back up to it — the failed edge, sold from inside.
    # "B": stop into the rotation: entry_stop_offset_ticks past the edge, into
    #      value, triggered as price continues away from the edge.
    entry_variant: str = "A"
    entry_stop_offset_ticks: int = 10    # variant B only

    # --- exit ---
    stop_ticks: int = 60
    # "poc" runs the rotation to the developing POC, tracked live with the
    # crossing discipline above; "mid" targets the NY VWAP; "rr" is fixed at
    # entry.
    target: str = "poc"                  # "poc" | "mid" | "rr"
    target_rr: float | None = None       # used when target == "rr"
    # The trivial-rotation guard: at the would-be fill, the developing POC must
    # sit at least this far beyond the fill in the trade's direction, or the
    # touch is missed (not deferred). 0 measures the lab's deflation instead of
    # respecting it.
    min_room_ticks: int = 40
    # 0 = off. N consecutive bar closes back OUTSIDE the edge exit the position
    # at market — price has been re-accepted outside value again, which is this
    # trade's premise run backwards. The fixed stop stays behind it as the hard
    # backstop.
    invalidate_outside_bars: int = 5
    # The trail: same rule and same knobs as the bounce's (see SimConfig).
    trail_stop_ticks: int = 0
    trail_step_ticks: int = 0
    trail_breakeven_ticks: int = 0
    trail_breakeven_only: bool = False

    # --- filters / lifecycle ---
    rearm_after_exit: bool = True        # any exit disarms; a fresh excursion is required
    daily_loss_stop: float = 0.0         # 0 = off. Same governor as the bounce's.
    # False = off. Also flatten the open trade at the daily loss stop — see
    # SimConfig.daily_loss_exit_open. Needs a daily_loss_stop set.
    daily_loss_exit_open: bool = False

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DriftFadeConfig(_JsonMixin):
    """The drift-touch fade's knobs: fade a level that price drifted into.

    A *drift touch* is contact with a level that neither side approached — over
    the trailing GAP_LOOKBACK_BARS bars, price's net move toward the level plus
    the level's net move toward price is <= 0 (profile.gap_closer). Price was
    already loitering by the level and wiggled into contact: a slow re-test of a
    hugged zone. The Interactions Lab found this the first Lab lead to survive a
    full monthly-robustness pass (docs/research/drift-touch-fade-spec.md).

    The trade fades the level on that contact: enter away from it (long when
    price hugged ABOVE and drifted down onto support, short when it hugged BELOW
    and drifted up into resistance), fixed stop behind the zone, target toward
    value. Its own class because it shares no rules with any band/profile
    strategy: no acceptance candle, no arming stretch, no resting limit — the
    drift classification at a bar close IS the setup, and the entry is a market
    order (a resting limit would fill the price-led approaches, exactly the dead
    class the drift cut removes).
    """

    # --- scope ---
    instrument: str = "NQ"
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 45)   # 09:30-09:45 excluded (the flagship's pre-checkpoint leak lesson)
    entry_close: time = time(15, 0)  # the drop-15:xx rule; 15:00+ scored at null in v9
    flat_by: time = time(16, 0)

    # --- sources (what a drift touch may land on) ---
    # The studied population: developing NY value, developing Globex value, and
    # the static session references. Each family is a switch; the developing ones
    # additionally pick which value levels (POC/VAH/VAL) are candidates.
    use_ny_levels: bool = True       # developing NY POC/VAH/VAL
    use_globex_levels: bool = True   # developing Globex POC/VAH/VAL (mature by the bell)
    use_session_refs: bool = True    # ONH/ONL, pd POC/VAH/VAL, pd Close, Open
    trade_poc: bool = True
    trade_vah: bool = True
    trade_val: bool = True
    # An NY-anchored profile is degenerate while minutes old (POC=VAH=VAL on the
    # open print); its levels are not candidates until the anchor is this old.
    # Globex levels are ~15h mature at the bell and the static refs are older
    # still, so neither is gated by this. The session Open ref anchors at the bell
    # and honors it too — a "touch" of the open at 09:31 is the open itself.
    level_warmup_min: int = 15

    # --- detection (the engine's translation of the Lab event) ---
    # A bar touches a level when low - touch_tol <= level <= high + touch_tol.
    touch_tol: float = 2.0
    # Re-approach counts as a fresh touch only after this many bars clear of the
    # zone — one rotation sitting on a level is one touch, not many (the Lab's
    # TOUCH_GAP_BARS debounce).
    touch_gap_bars: int = 3
    # 0 = off. Skip a drift signal on a level that relocated more than
    # stability_tol_ticks within the last this-many minutes: a drift touch on a
    # freshly node-flipped level is a detection artifact (the profile chasing the
    # market), not a hug. Static refs never move, so this only ever bites the
    # developing levels. Default 5 min per the spec.
    min_level_stability_min: int = 5
    # How far a developing level may wander over the stability window and still
    # count as "sat here", in ticks. Read only when min_level_stability_min > 0.
    stability_tol_ticks: int = 8

    # --- entry ---
    # "A": market order on the close of the drift-touch bar (filled on the next
    #      tick), direction = away from the level.
    # "B": wait for the first bar to close at least confirm_ticks beyond the touch
    #      bar's extreme on the fade side, then enter at market — later, but it
    #      filters the instant-acceptance failures. The profile-pullback dwell
    #      lesson sets the prior that A beats B; build both, measure.
    entry_variant: str = "A"
    confirm_ticks: int = 8               # variant B only
    # Which direction the fade may trade. Drift is the repo's first near-symmetric
    # edge, but the house prior is long-only, so the A/B reads sides separately
    # before "both" ships as a baseline default.
    side: str = "both"                   # "long" | "short" | "both"
    # 0 = unlimited. Cap fills to each zone's first N touches. Acceptance decay
    # shrinks MFE by the 7th touch, but the drift ratio held on re-tests, so this
    # starts open and the nth-touch cut is measured in the edges panel first.
    max_touches_per_zone: int = 0

    # --- exit ---
    # stop_ticks is measured from the LEVEL, not the fill — the zone is the
    # invalidation, not the entry print. Default mid of the spec's 120-200 sweep.
    stop_ticks: int = 160
    # "ny_vwap": fade to the NY VWAP (a POC-magnet cousin), tracked live with the
    #            crossing discipline (a reference that node-flips across price
    #            books a market fill at the print, never a limit at a level the
    #            market wasn't at). "r_multiple": fixed R at entry. "fixed_ticks":
    #            fixed distance at entry.
    target_mode: str = "ny_vwap"         # "ny_vwap" | "r_multiple" | "fixed_ticks"
    target_rr: float | None = 1.5        # used when target_mode == "r_multiple"
    target_ticks: int = 120              # used when target_mode == "fixed_ticks"
    # The trivial-rotation guard, ny_vwap target only: at the would-be fill the
    # VWAP must sit at least this far beyond the fill in the trade's direction, or
    # the signal is skipped (a target already inside the stop distance is no trade).
    min_room_ticks: int = 40
    # The trail: same rule and same knobs as the bounce's (see SimConfig).
    trail_stop_ticks: int = 0
    trail_step_ticks: int = 0
    trail_breakeven_ticks: int = 0
    trail_breakeven_only: bool = False
    # 0 = off. Flatten the open trade at market once it has been held this many
    # minutes, target unmet — the "give up on a grind" exit. The behavioral read:
    # winners on this strategy snap back fast (median ~4.5 min to target) while
    # losers grind underwater (median ~11 min to the stop), so a hold cap is a
    # candidate loser filter. The A/B has to weigh that against the slow-but-real
    # winners (the 15-20 min pocket ran 94% wins), which a cap cuts too.
    max_hold_min: int = 0

    # --- filters / lifecycle ---
    daily_loss_stop: float = 0.0         # 0 = off. Same governor as the bounce's.
    # False = off. Also flatten the open trade at the daily loss stop — see
    # SimConfig.daily_loss_exit_open. Needs a daily_loss_stop set.
    daily_loss_exit_open: bool = False

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OrbConfig(_JsonMixin):
    """The opening-range breakout's knobs: one initiative trade off the session's
    opening window.

    Built from the IB/ORB research (docs/research/initial-balance-orb.md) and
    the Lab's IB study. Three entry modes, because the literature and the study
    point at three distinct reads of the same window:

      - "candle"       — the Zarattini rule: at the window's close, enter in the
                         window candle's direction. The study's Lab read (NQ
                         Feb 2025 – Jan 2026): 56% follow-through at 5m, mean R
                         ~0 WITHOUT the stop — the paper's whole edge is the
                         enforced stop's asymmetry, which is exactly what this
                         engine adds over the Lab.
      - "break"        — the classic ORB: stop in on the first crossing of the
                         window's high/low (+ entry_offset_ticks).
      - "second_break" — the IB study's double-break read: on days that break
                         one side of the window and then the other, enter with
                         the SECOND break — the close landed on its side on 81%
                         of double-break days (n=53).

    One trade per session, by design — the research strategies are one-shot
    daily rules, and a re-entering variant would be a different idea. Its own
    class because it shares no rules with any band or profile strategy: no
    acceptance, no arming stretch, no level in force — the window itself is
    the setup.
    """

    # --- scope ---
    instrument: str = "NQ"
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 30)
    entry_close: time = time(16, 0)  # break modes can trigger any time before this
    flat_by: time = time(16, 0)      # the Zarattini exit: end of day

    # --- setup (the opening window) ---
    # Minutes from the 09:30 bell the window spans. 5 is the Zarattini window
    # (and the only one that carried the paper's stock edge); 60 is the classic
    # Initial Balance, the natural window for second_break.
    window_minutes: int = 5
    entry_mode: str = "candle"       # "candle" | "break" | "second_break"
    # Which breakout directions may trade. "both" is the base rule; the study's
    # documented futures filter is the opening candle itself (the candle mode),
    # not the gap, so the one-sided settings exist for A/B rather than belief.
    direction: str = "both"          # "both" | "long_only" | "short_only"
    # break mode only: the entry stop rests this many ticks beyond the window
    # extreme (Crabel's stretch, fixed rather than ATR-derived).
    entry_offset_ticks: int = 0

    # --- exit ---
    # "range": the stop sits at the window's opposite extreme — the Zarattini
    #          stop for the candle mode, the classic ORB stop for the breaks.
    # "ticks": a fixed stop_ticks stop, for divorcing risk from window size.
    stop_mode: str = "range"
    stop_ticks: int = 100            # used when stop_mode == "ticks"
    # "eod" holds to flat_by (the paper's baseline); "rr" fixes a target at
    # entry, in multiples of the actual risk (range stop -> range multiples).
    target: str = "eod"              # "eod" | "rr"
    target_rr: float | None = None   # used when target == "rr"
    # The trail: same rule and same knobs as the bounce's (see SimConfig).
    trail_stop_ticks: int = 0
    trail_step_ticks: int = 0
    trail_breakeven_ticks: int = 0
    trail_breakeven_only: bool = False

    # --- filters ---
    # 0 = off. Skip the day when the window's high-low range is narrower /
    # wider than these — the noise floor and the exhausted tail. On a range
    # stop, min_range_ticks is also the risk floor: the stop distance IS the
    # range (plus the candle's close-to-extreme geometry, in candle mode).
    min_range_ticks: int = 0
    max_range_ticks: int = 0

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FadeConfig(_JsonMixin):
    """The band fade's knobs: mean-reversion against a dev1 stretch.

    The fade is the bounce's counter-trade — where the bounce buys the pullback
    to dev1 expecting continuation to dev2, the fade sells the return to dev1
    after price overextended beyond it, expecting reversion toward the mid. Its
    own class because the two ideas share almost no rules: the fade has no
    acceptance candle, no mid invalidation of an armed setup, no value-area
    exit — and the bounce has no stretch, no mid-cross filter, no dev2 cap.

    Knob names are written for the upper-band short (the first registered fade)
    and mean the mirror on a lower-band long, exactly as SimConfig's
    long-flavoured names mean the mirror on the bounce shorts.
    """

    # --- scope ---
    instrument: str = "NQ"
    contract: str = "NQ"
    start_date: date = date(2025, 10, 13)
    end_date: date = date(2025, 10, 17)

    # --- bars ---
    ticks_per_bar: int = 500

    # --- session (ET wall clock) ---
    entry_open: time = time(9, 31)
    entry_close: time = time(16, 0)
    flat_by: time = time(16, 0)

    # --- arming (what makes the fade live) ---
    # Which side of dev1 the arming stretch runs to.
    #   "beyond": the overextension — the stretch runs OUTSIDE the band, away
    #             from the channel (up, on the short), and the fade sells the
    #             return back DOWN to dev1.
    #   "inside": the same trade armed by the break instead — the stretch runs
    #             INSIDE the band, into the channel (down, on the short), and the
    #             fade sells the retest back UP to dev1: the broken band, resold
    #             from underneath.
    # Only the stretch flips. The trade is a short off dev1 toward the mid either
    # way, so the stop, the targets, the dev2 cap and the dev1 re-acceptance exit
    # all keep the meaning (and the side) they have under "beyond".
    arm_stretch_side: str = "beyond"     # "beyond" | "inside"
    # The setup arms on the *stretch*: a print more than this many ticks past
    # dev1, on the arm_stretch_side of it. Edge-triggered — price must come back
    # within this distance of dev1 and stretch out again before the setup can
    # re-arm — so a disarm (the dev2 cap, an exit) is never undone by the very
    # next tick of a stretch that never ended.
    arm_extension_ticks: int = 50
    # The approach must have started from the VWAP mid: a print at or past the
    # mid (on the far side from the band) since the last fill is required before
    # a stretch may arm. Off = any stretch arms. Read against the band, not the
    # stretch, on both sides: under "inside" it demands a stretch that ran all
    # the way to the mid, which is a deeper break than the extension alone.
    arm_require_mid_cross: bool = False
    # A bar CLOSE beyond dev2 disarms an armed, unfilled setup — the stretch is
    # a runaway, not an overextension. A fresh stretch is then required. Never
    # touches an open position.
    arm_cap_at_dev2: bool = False

    # --- entry ---
    # "A": rest a limit at dev1 the moment the stretch arms; fills on the
    #      crossing back to the band, at the band's price.
    # "B": wait for a bar to CLOSE back across dev1, on the far side from the
    #      stretch (the rejection confirmed) — inside the band on a "beyond"
    #      arming, back outside it on an "inside" one — then stop into the
    #      continuation, entry_stop_offset_ticks past dev1 into the channel.
    #      The confirming close is the only half of B the stretch's side moves;
    #      the stop is the fade's own direction, and never flips.
    entry_variant: str = "A"
    entry_stop_offset_ticks: int = 10    # variant B only
    # 0 = at dev1. Variant A only: rest the limit this many ticks in FRONT of
    # dev1 — toward the stretch, so the return fills before it reaches the band
    # (above dev1 on a "beyond" short, below it on an "inside" one). Capped at
    # arm_extension_ticks by schema: the market is only that far past dev1 when
    # the setup arms, and a limit further out would already be through it.
    entry_limit_offset_ticks: int = 0

    # --- exit ---
    stop_ticks: int = 50
    # "mid" reverts to the VWAP mid, "opp_dev1" runs to the far band, "rr" is a
    # fixed R multiple. mid and opp_dev1 are tracked live, like the bounce's
    # dev2 target — the level the trade is judged against is the level in force
    # at the exit, not the one at entry.
    target: str = "mid"                  # "mid" | "opp_dev1" | "rr"
    target_rr: float | None = None       # used when target == "rr"
    # 0 = off. N consecutive bar closes back beyond dev1 exit the position at
    # market — price has been re-accepted outside the band, which is the
    # bounce's acceptance and therefore the fade's structural invalidation. The
    # fixed stop stays behind it as the hard backstop.
    invalidate_beyond_dev1_bars: int = 5
    # The trail: same rule and same knobs as the bounce's (see SimConfig).
    trail_stop_ticks: int = 0
    trail_step_ticks: int = 0
    trail_breakeven_ticks: int = 0
    trail_breakeven_only: bool = False

    # --- filters / lifecycle ---
    min_band_width_ticks: int = 0        # 0 = off. Skip entry if dev2-dev1 is tighter.
    rearm_after_exit: bool = True        # any exit disarms; a fresh stretch is required
    daily_loss_stop: float = 0.0         # 0 = off. Same governor as the bounce's.
    # False = off. Also flatten the open trade at the daily loss stop — see
    # SimConfig.daily_loss_exit_open. Needs a daily_loss_stop set.
    daily_loss_exit_open: bool = False

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)
