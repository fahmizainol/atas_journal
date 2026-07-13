"""The knob-board.

Every tunable of the strategy lives here, so a new experiment is a new config
rather than a new branch of engine code. The point of the whole exercise is to
ask "does adding filter X improve the base rule?" — that question is only cheap
to ask if X is a field, not an edit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, time


@dataclass(frozen=True)
class SimConfig:
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
    # 0 = off. A step ratchet: once price has run N * trail_step_ticks in the
    # trade's favour, the stop moves to entry + (N-1) steps. So the first step
    # buys breakeven, the second locks one step of profit, and so on. The stop
    # only ever moves toward the trade — a pullback never loosens it.
    trail_step_ticks: int = 0

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

    def to_json(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, (date, time)):
                d[k] = v.isoformat()
        return d
