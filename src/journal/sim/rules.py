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

    # --- confluences (veto-only gates) ---
    # One namespaced section per gate, e.g. {"volume_profile": {"enabled": true, ...}}.
    # A gate may only VETO an entry the base rules would take; anything that
    # changes what/when/how we enter or exit is a new strategy, not a gate.
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
