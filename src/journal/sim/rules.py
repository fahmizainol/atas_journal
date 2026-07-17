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

    # --- filters / lifecycle ---
    min_band_width_ticks: int = 0        # 0 = off. Skip entry if dev2-dev1 is tighter.
    invalidate_below_mid_bars: int = 5   # 0 = off. N consecutive closes below VWAP mid disarms.
    rearm_after_exit: bool = True        # any exit disarms; a fresh acceptance is required
    # 0 = off. Once the session's *realized* net P&L (closed trades, commissions
    # included) is this many dollars in the red, no further entries that day. An
    # open position still runs to its normal exit — the governor halts new risk,
    # it never touches a trade already on.
    daily_loss_stop: float = 0.0

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- pyramid (scale-in) ---
    # 1 = off: the whole `contracts` size fills at once, as a single all-in touch.
    # N > 1 splits the position into N equal lots (contracts must divide by N, the
    # schema enforces it): the FIRST lot fills exactly as the base variant does
    # (variant A's limit at dev1, variant B's stop on the reclaim), and each later
    # lot rests a stop pyramid_step_ticks further in the trade's favour, off the
    # first fill's grid — so size is added only as the move confirms, never against
    # it. A lot whose trigger is never reached simply never fills: a trade that
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

    # --- size & cost ---
    contracts: int = 1
    commission_per_side: float = 7.0

    # --- confluences (veto-only gates) ---
    confluences: dict = field(default_factory=dict)
