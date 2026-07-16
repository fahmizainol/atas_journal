"""Concrete confluence gates.

Each gate is a veto and nothing else (see confluences.py for why). Gates live
here rather than beside the indicators they read so that the indicator stays a
fact about the market and the gate stays a policy about trading it — the same
developing profile also feeds a base exit rule, and that rule is not a gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..config import ET_TZ, tick_size
from . import bars as barsmod
from . import profile as profmod
from . import regime as regmod
from . import ticks as tickmod
from . import vwap as vwapmod
from .schema import Field

if TYPE_CHECKING:
    from .confluences import SessionCtx


# The regime-artifact checkpoints a gate may read, and the ET wall-clock minute
# each one becomes knowable. Only listed checkpoints are legal knob values — a
# free-form read time would invite fitting the clock to the sample. 09:30 is
# absent on purpose: the NY anchor has no closed bars yet, so every NY KPI is
# None there and a gate reading it could only ever be blind.
CHECKPOINT_MINUTES: dict[str, int] = {
    "09:45": 9 * 60 + 45,
    "10:30": 10 * 60 + 30,
}
_CHECKPOINT_CHOICES = tuple((c, c) for c in CHECKPOINT_MINUTES)


def _checkpoint(section: dict, default: str) -> str:
    v = section.get("checkpoint", default)
    if v not in CHECKPOINT_MINUTES:
        raise ValueError(
            f"checkpoint must be one of {sorted(CHECKPOINT_MINUTES)}, got {v!r}")
    return v


def _veto_from(ctx: "SessionCtx", minute: int) -> np.ndarray:
    """Mask of ticks at or after the given ET wall-clock minute. Overnight ticks
    in a globex frame also read past mid-morning on a wall clock, but entries
    only ever fire inside the entry window, so a gate is never consulted there."""
    et = ctx.ticks["ts_utc"].dt.tz_convert(ET_TZ)
    mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    return mins >= minute


class VolumeProfileGate:
    """Veto any entry that does not fill beyond the developing value area.

    The idea it enforces: the VWAP-band setup is traded from *outside* value — a
    long from above it, and on the lower-band mirror, a short from below it. If
    the pullback to dev1 carries price back inside the value area, the market has
    re-accepted the prices it just left, and the "accepted outside value" premise
    of the trade is gone — however good the band structure still looks.

    So the edge the gate compares against is the one on the trade's side: VAH for
    a long, VAL for a short. The strategy's direction picks it (ctx.side); the
    config does not, because a gate that could be pointed at the wrong edge would
    silently pass every entry it was meant to stop.

    Config section::

        {"volume_profile": {"enabled": true, "min_ticks_above_vah": 0}}

    ``min_ticks_above_vah`` = 0 demands only that the fill be strictly beyond the
    edge; raise it to demand real separation from value. The knob keeps its
    long-flavoured name on both sides (see rules.SimConfig) and reads as
    "min ticks beyond the value-area edge" on a short.

    Before the session's first bar closes there is no profile yet, and the gate
    vetoes. That is deliberate: "no data" must not read as "confirmed" — a gate
    that waved trades through whenever it was blind would flatter itself in
    exactly the window (the open) where the strategy is most fragile.
    """

    name = "volume_profile"
    needs_profile = True

    # The gate owns its knobs — the run form renders them from this and the
    # config parser canonicalizes against it, exactly as it does for the scalar
    # knobs in rules.SimConfig (see schema.py). A new gate reaches the UI by
    # publishing a SCHEMA; it never edits the form.
    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require the fill to be beyond value",
              default=False,
              help="Vetoes any entry that fills back inside the developing value "
                   "area — the premise of the setup is that price was accepted "
                   "outside it."),
        Field("min_ticks_above_vah", "int", name,
              "Min distance beyond the value-area edge", unit="ticks", min=0,
              default=0, depends_on=("enabled", True),
              help="0 asks only that the fill be strictly beyond the edge (the VAH "
                   "on a long, the VAL on a short). Raise it to demand real "
                   "separation from value."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown volume_profile knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("min_ticks_above_vah", 0)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"min_ticks_above_vah must be a non-negative int, got {n!r}")
        self.min_ticks_above_vah = n
        self._edge: np.ndarray | None = None
        self._margin = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        assert ctx.value_edge_at_tick is not None, "gate declared needs_profile"
        self._edge = ctx.value_edge_at_tick
        self._margin = self.min_ticks_above_vah * tick_size(ctx.cfg.instrument)
        # Signed by the side of value the setup lives on, not the trade's
        # direction: a bounce long and a FADE SHORT both trade from above the
        # VAH, and both must fill above it.
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0

    def allows(self, i: int, fill: float) -> bool:
        assert self._edge is not None, "prepare() not called"
        edge = self._edge[i]
        if np.isnan(edge):
            return False
        # Beyond the edge, on the trade's side, by at least the margin.
        return self._side * (fill - edge) > self._margin + 1e-9


class VwapSlopeGate:
    """Veto every entry after its checkpoint ET on a day whose NY VWAP has no
    upward grade at that checkpoint.

    The idea it enforces: the long bounce pays on days that establish an upward
    grade by mid-morning. Across Oct 2025 – Jan 2026 the 10:30 NY VWAP slope was
    the strongest day-level correlate of this strategy's net (ρ ≈ 0.42 over 60
    days, ρ ≈ 0.70 on the held-out January slice) — a better read of "bull-grade
    day" than bbr or ABR.

    Honesty clause: that correlation is mostly *recorded* by 10:30, not
    predictive from it. In the same sample, the post-10:30 entries this gate
    would veto at slope_min=0 were net POSITIVE — the slope-negative damage came
    from morning entries a 10:30 read cannot lawfully touch. This gate exists so
    that threshold variants can be A/B'd for free off one run's ghost ledger
    (vetoed.parquet), not because a profitable setting is already known.

    The checkpoint is a knob (09:45 or 10:30, default 10:30). 09:45 exists
    because the Jun 2025 – Jan 2026 09:30-entry study put nearly all of this
    strategy's edge before 10:30 — the one window a 10:30 read can never
    protect — and its 09:45 board held ``ny_vwap_slope_deg`` past the
    Bonferroni bar (top tercile +$801/day at 70.6% win rate vs −$116/−$350 for
    the rest). Same clock discipline either way: entries before the checkpoint
    pass untouched (the number does not exist yet), and the gate reads the
    artifact's checkpoint — never a fresher slope — so what it acts on is
    exactly what was knowable.

    Config section::

        {"vwap_slope": {"enabled": true, "slope_min": 0.0, "checkpoint": "10:30"}}

    ``slope_min`` is the stand-down threshold in points per minute (the KPI's
    native unit, over the checkpoint's trailing 30-minute window — 15 minutes
    at 09:45): at or below it, no entries for the rest of the session. 0.0
    demands any upward grade at all.

    Blind days — no artifact, or a checkpoint too thin to carry a slope — are
    vetoed after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "vwap_slope"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down without upward VWAP grade",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "NY VWAP slope at the checkpoint is at or below the "
                   "threshold — the day has not established the upward grade "
                   "the bounce pays on."),
        Field("slope_min", "float", name, "Min NY VWAP slope at the checkpoint",
              unit="pts/min", min=-5.0, max=5.0, default=0.0,
              depends_on=("enabled", True),
              help="Slope of the NY-anchored VWAP into the checkpoint. At or "
                   "below this, the rest of the session is stood down. 0 "
                   "demands any upward grade at all."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="10:30",
              depends_on=("enabled", True),
              help="When the slope is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown vwap_slope knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("slope_min", 0.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -5.0 <= v <= 5.0:
            raise ValueError(f"slope_min must be a number in [-5, 5], got {v!r}")
        self.slope_min = float(v)
        self.checkpoint = _checkpoint(section, "10:30")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        slope = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
                 or {}).get("ny_vwap_slope_ppm")
        if slope is not None and slope > self.slope_min:
            self._blocked = None  # the grade is there; the gate is inert today
            return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class VwapSlopeCapGate:
    """Veto every entry after its checkpoint ET on a day whose NY VWAP has
    already established a steep grade AWAY from the mean, at that checkpoint.

    The slope gate's mirror, for the strategies that FIGHT the grade instead of
    leaning on it: the dev1 fade short sells the return to the upper band, and
    on a day that is trending away from the VWAP that return keeps travelling.

    The grade is read in the faded band's frame, so one threshold serves both
    sides: an upward grade stands the short (upper band) down, a downward grade
    the long (lower band). ``slope_max`` is therefore a magnitude in the fade's
    own direction — it is not a signed reading of the tape.
    Across the Aug 2025 – Jan 2026 variant-B fade study the 09:45 NY VWAP slope
    separated the bleeding cleanly on every config tried (ρ ≈ −0.26 to −0.33,
    500 permutations): the steepest tercile of days averaged −$450 to −$760/day
    while the other two terciles were flat to positive — on the far-band-target
    combos, +$150 to +$320/day.

    Honesty clause: the 1.1 pts/min default is that sample's 09:45 top-tercile
    boundary — a threshold read off the same days the board was computed on,
    not held-out evidence. And the tercile arithmetic assumes the vetoed days'
    trades simply vanish, which ghost-ledger experience says over-promises
    (entries interact through rearm) — the setting has to earn its keep on an
    actual A/B run.

    Same clock discipline as every checkpoint gate: entries before the
    checkpoint pass untouched, and the gate reads the artifact's checkpoint —
    never a fresher slope — so what it acts on is exactly what was knowable.

    Config section::

        {"vwap_slope_cap": {"enabled": true, "slope_max": 1.1,
                            "checkpoint": "09:45"}}

    ``slope_max`` is the stand-down threshold in points per minute (the KPI's
    native unit): at or above it, no entries for the rest of the session.

    Blind days — no artifact, or a checkpoint too thin to carry a slope — are
    vetoed after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "vwap_slope_cap"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down against a runaway VWAP grade",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "NY VWAP slope at the checkpoint runs away from the mean the "
                   "fade reverts to — upward on the short, downward on the long "
                   "— by at least the threshold."),
        Field("slope_max", "float", name, "Max NY VWAP slope at the checkpoint",
              unit="pts/min", min=-5.0, max=5.0, default=1.1,
              depends_on=("enabled", True),
              help="Slope of the NY-anchored VWAP into the checkpoint, read in "
                   "the fade's own direction (a long reads a −1.1 pts/min tape "
                   "as 1.1). At or above this, the rest of the session is stood "
                   "down. The default is the steepest-tercile boundary of the "
                   "Aug'25–Jan'26 fade study at 09:45."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="09:45",
              depends_on=("enabled", True),
              help="When the slope is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown vwap_slope_cap knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("slope_max", 1.1)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -5.0 <= v <= 5.0:
            raise ValueError(f"slope_max must be a number in [-5, 5], got {v!r}")
        self.slope_max = float(v)
        self.checkpoint = _checkpoint(section, "09:45")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        # The grade is read in the faded band's frame: what stands a fade down is
        # a day trending AWAY from the mean it reverts to, which is upward for the
        # short above the upper band and downward for the long beneath the lower
        # one. u flips the sign so one threshold serves both — the short's
        # behaviour is the u=+1 case, unchanged.
        u = 1.0 if ctx.band_side() == "upper" else -1.0
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        slope = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
                 or {}).get("ny_vwap_slope_ppm")
        if slope is not None and u * slope < self.slope_max:
            self._blocked = None  # no runaway grade; the gate is inert today
            return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class GxRescueGate:
    """Veto every entry after 09:45 ET on a day whose broken session bands are
    not being caught by the Globex band underneath.

    The idea it enforces: the user's own read — on some days the Globex upper
    channel wraps the session's, and a pullback that breaks the session +1σ
    bounces at the Globex +1σ instead. The regime artifact counts exactly that
    event (``gx_upper_rescue_ratio``: of closes that broke the session +1σ with
    the Globex +1σ below it, the share where the lows held the Globex line and
    price recovered). Across Aug 2025 – Jan 2026 its 09:45 reading was the
    strongest early correlate of this strategy's net found so far (ρ ≈ +0.56
    over the 40 days it was defined on, past the Bonferroni bar): days at 0
    averaged −$445 and −$309 by tercile, days at ≥1/3 averaged +$1,114.

    Honesty clause: 09:45 is 14 minutes into the entry window, so unlike the
    slope gate's 10:30 read almost all of the P&L it correlates with comes
    *after* the reading — but ghost-ledger arithmetic has over-promised before
    (entries interact through rearm), so the setting still has to earn its keep
    on an actual A/B run, not on this docstring.

    Three distinct silences, treated differently:

    - No artifact, or a ``partial`` day (no overnight, so no Globex anchor and
      no wrap to read) — blind. Vetoes after 09:45: "no data" must not read as
      "confirmed".
    - Artifact present, ratio ``None`` — the session's +1σ simply hasn't been
      broken with the wrap present yet. That is the *absence of the event*, not
      blindness; the gate stays inert rather than punishing a day for not
      having pulled back yet.
    - Ratio present but below the threshold — stood down for the rest of the
      session.

    Config section::

        {"gx_rescue": {"enabled": true, "rescue_min": 0.33}}
    """

    name = "gx_rescue"
    needs_profile = False

    _CHECKPOINT = "09:45"
    _CHECKPOINT_MIN = 9 * 60 + 45

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down when Globex isn't catching",
              default=False,
              help="After 09:45 ET, vetoes every entry on a day whose broken "
                   "session +1σ pullbacks are not being rescued by the Globex "
                   "+1σ underneath. Days with no such break yet pass untouched."),
        Field("rescue_min", "float", name, "Min rescue ratio at 09:45",
              min=0.0, max=1.0, default=0.33, depends_on=("enabled", True),
              help="Share of session-+1σ breaks the Globex +1σ must have caught "
                   "by 09:45. Below it, the rest of the session is stood down. "
                   "The 0.33 default is the top-tercile boundary of the Aug'25–"
                   "Jan'26 sample."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_rescue knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("rescue_min", 0.33)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"rescue_min must be a number in [0, 1], got {v!r}")
        self.rescue_min = float(v)
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        if art is not None and not art.get("partial"):
            ratio = (art.get("checkpoints", {}).get(self._CHECKPOINT)
                     or {}).get("gx_upper_rescue_ratio")
            if ratio is None or ratio >= self.rescue_min:
                # No break to read yet, or the rescues are there: inert today.
                self._blocked = None
                return
        self._blocked = _veto_from(ctx, self._CHECKPOINT_MIN)

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class GxRescueCapGate:
    """Veto every entry after its checkpoint ET on a day whose broken session
    bands ARE being caught by the Globex band behind them.

    The rescue gate's mirror, for the strategies the rescue works against: when
    the Globex +1σ keeps catching pullbacks that break the session +1σ, the
    market is refusing to revert — and shorting the return to that band is
    selling into the very floor doing the catching. The fade-long reads the
    mirror event (``gx_lower_rescue_ratio``): the Globex −1σ standing ABOVE the
    session −1σ and catching the rallies that break it, which is the ceiling the
    long would be buying into. Across the Aug 2025 –
    Jan 2026 variant-B fade study ``gx_upper_rescue_ratio`` at 10:30 was the
    strongest KPI on nearly every config's board (ρ ≈ −0.39 to −0.57, 500
    permutations, past the holdout on most), with the most-rescued tercile
    averaging −$790 to −$1,230/day while the other terciles were flat to
    positive.

    Honesty clause: the 0.4 default is that sample's 10:30 top-tercile
    boundary — read off the same days the board was computed on, not held-out
    evidence — and tercile arithmetic over-promises (entries interact through
    rearm); the setting has to earn its keep on an actual A/B run. The 10:30
    default checkpoint also means the open-driven morning passes unprotected:
    at 09:45 the ratio is defined on too few days to carry the default.

    Three distinct silences, same doctrine as the rescue gate:

    - No artifact, or a ``partial`` day (no Globex anchor, no wrap to read) —
      blind. Vetoes after the checkpoint: "no data" must not read as
      "confirmed".
    - Artifact present, ratio ``None`` — the session's +1σ hasn't been broken
      with the wrap present yet. The absence of the event, not blindness; the
      gate stays inert.
    - Ratio at or above the threshold — stood down for the rest of the session.

    Config section::

        {"gx_rescue_cap": {"enabled": true, "rescue_max": 0.4,
                           "checkpoint": "10:30"}}
    """

    name = "gx_rescue_cap"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down when Globex is catching",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "broken session dev1 keeps getting rescued by the Globex "
                   "dev1 behind it — the floor the short would be selling into, "
                   "the ceiling the long would be buying into. Days with no such "
                   "break yet pass untouched."),
        Field("rescue_max", "float", name, "Max rescue ratio at the checkpoint",
              min=0.0, max=1.0, default=0.4, depends_on=("enabled", True),
              help="Share of session-dev1 breaks the Globex dev1 has caught by "
                   "the checkpoint, on the faded side. At or above it, the rest "
                   "of the session is "
                   "stood down. The 0.4 default is the top-tercile boundary of "
                   "the Aug'25–Jan'26 fade study at 10:30."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="10:30",
              depends_on=("enabled", True),
              help="When the ratio is read, and from when the stand-down "
                   "applies. At 09:45 the ratio is defined on few days — most "
                   "sessions haven't broken the band yet."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_rescue_cap knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("rescue_max", 0.4)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"rescue_max must be a number in [0, 1], got {v!r}")
        self.rescue_max = float(v)
        self.checkpoint = _checkpoint(section, "10:30")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        # The rescue on the faded band's side: the Globex +1σ catching breaks of
        # the session +1σ from underneath is the floor a fade-short sells into;
        # the Globex −1σ catching breaks of the session −1σ from above is the
        # ceiling a fade-long buys into. Same event, mirrored.
        key = ("gx_upper_rescue_ratio" if ctx.band_side() == "upper"
               else "gx_lower_rescue_ratio")
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        if art is not None and not art.get("partial"):
            ratio = (art.get("checkpoints", {}).get(self.checkpoint)
                     or {}).get(key)
            if ratio is None or ratio < self.rescue_max:
                # No break to read yet, or the rescues aren't there: inert today.
                self._blocked = None
                return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class GxFloorGate:
    """Veto any entry without the Globex +1σ as a second floor just beneath it.

    The per-trade version of the wrap read: a bounce entry at the session band
    is better protected when the Globex band runs a little below the fill — a
    pullback that breaks the session +1σ can still be caught before it travels
    stop-distance. The gate requires the Globex dev1 (on the traded side) to sit
    between the fill and ``max_ticks_below`` ticks beyond it: a line above the
    fill is no floor, and one further away than the stop would rescue nobody.

    Honesty clause: the day-level cousin of this number (the mean dev1 gap)
    never cleared the study's luck bar; what did was the *event* ratio the
    gx_rescue gate reads. This gate exists to A/B the per-entry microstructure
    version of the same idea off the ghost ledger, not because a profitable
    setting is already known.

    The engine never builds the Globex bands for an RTH strategy, so the gate
    builds them itself in prepare() — from the *cached* overnight ticks spliced
    onto the engine's own tick frame, so the per-tick line is positionally
    aligned with every index ``allows`` will ever be asked about. Cache-only by
    doctrine (a gate must never spend at Databento); a day with no cached
    overnight has no Globex anchor, and the gate vetoes it wholesale — "no
    data" must not read as "confirmed".

    Config section::

        {"gx_floor": {"enabled": true, "max_ticks_below": 80}}
    """

    name = "gx_floor"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require a Globex second floor",
              default=False,
              help="Vetoes any entry whose fill does not have the Globex +1σ "
                   "(−1σ on a short) within reach beneath it — the second floor "
                   "that catches a broken session band."),
        Field("max_ticks_below", "int", name, "Max distance to the Globex line",
              unit="ticks", min=0, default=80, depends_on=("enabled", True),
              help="How far beyond the fill (below on a long, above on a short) "
                   "the Globex dev1 may sit and still count as a floor. Beyond "
                   "stop distance it would rescue nobody; 0 demands the lines "
                   "touch."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_floor knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("max_ticks_below", 80)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"max_ticks_below must be a non-negative int, got {n!r}")
        self.max_ticks_below = n
        self._line: np.ndarray | None = None
        self._max_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._max_pts = self.max_ticks_below * tick_size(ctx.cfg.instrument)
        # The Globex band on the setup's side of the market (see
        # VolumeProfileGate.prepare on why this is the band side, not the
        # trade's direction).
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._line = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no Globex anchor — veto everything
        w = vwapmod.vwap_bands(pd.concat([on, ctx.ticks], ignore_index=True))
        col = "upper1" if ctx.band_side() == "upper" else "lower1"
        self._line = w[col].to_numpy(dtype="float64")[len(on):]

    def allows(self, i: int, fill: float) -> bool:
        if self._line is None:
            return False
        line = self._line[i]
        if np.isnan(line):
            return False
        # The line on the traded side of the fill, within reach: below a long's
        # fill, above a short's, by at most the configured distance.
        gap = self._side * (fill - line)
        return 0.0 <= gap <= self._max_pts + 1e-9


class OnHighGate:
    """Veto any entry filling too far beneath the overnight session high.

    The idea it enforces: above the overnight high the session is discovering
    price with no overnight inventory overhead; beneath it, every rally is
    selling into positions the night already built. The loss study of run
    9318bc07 found the bounce's stop-outs piled up under that wall — fills at or
    above the overnight high carried 77% of the run's profit at 66% WR, while
    the bands 0–25 and 100–200 ticks beneath it were net losers — so the gate
    requires each fill to be within ``max_ticks_below`` of the high, or above it.

    Mirrored by band side, and the knob keeps its long-flavoured name (see
    rules.SimConfig): on the lower band the wall is the overnight LOW, and the
    fill must be within reach of it or below — discovering price downward.

    Honesty clause: the numbers above are a post-hoc cut of one run, not a
    validated gate. This exists to A/B the idea off the ghost ledger, where the
    rearm/daily-loss-stop interactions a post-hoc cut cannot see are honest.

    Cache-only by doctrine (a gate must never spend at Databento): a day with no
    cached overnight has no overnight high, and the gate vetoes it wholesale —
    "no data" must not read as "confirmed". Splices the cached overnight itself,
    so it is only correct on an RTH-frame strategy (same as gx_floor).

    Config section::

        {"on_high": {"enabled": true, "max_ticks_below": 100}}
    """

    name = "on_high"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require the overnight high within reach",
              default=False,
              help="Vetoes any entry filling more than max_ticks_below beneath "
                   "the overnight session high (above the overnight low, on a "
                   "lower-band setup) — beneath that wall, rallies sell into "
                   "the night's inventory."),
        Field("max_ticks_below", "int", name, "Max distance beneath the high",
              unit="ticks", min=0, default=100, depends_on=("enabled", True),
              help="How far beneath the overnight high (above the overnight low, "
                   "mirrored) a fill may sit and still pass. 0 demands the fill "
                   "be at or beyond the wall itself."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown on_high knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("max_ticks_below", 100)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"max_ticks_below must be a non-negative int, got {n!r}")
        self.max_ticks_below = n
        self._wall: float | None = None
        self._max_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._max_pts = self.max_ticks_below * tick_size(ctx.cfg.instrument)
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._wall = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no wall — veto everything
        self._wall = float(on["price"].max() if self._side > 0 else on["price"].min())

    def allows(self, i: int, fill: float) -> bool:
        if self._wall is None:
            return False
        # How far the fill sits on the *wrong* side of the wall — beneath the
        # overnight high on a long, above the overnight low on a short. A fill
        # beyond the wall is a negative gap and always passes.
        gap = self._side * (self._wall - fill)
        return gap <= self._max_pts + 1e-9


class GxValueGate:
    """Veto any entry that does not fill beyond the *Globex* developing value area.

    The volume_profile gate's premise — the setup is traded from outside value —
    read against the overnight anchor instead of the bell. The session profile an
    hour into RTH is a story about sixty minutes; the Globex profile is the whole
    night's auction, and the loss study of run 9318bc07 found it is the one that
    discriminates: fills above the Globex VAH ran 65% WR at ~3x the per-trade
    take of fills still inside overnight value, while the session VAH separated
    nothing. As there, the edge is picked by the band side, never by config: VAH
    on the upper band, VAL on the lower.

    Honesty clause: post-hoc cut of one run, not a validated gate — this exists
    to A/B the idea off the ghost ledger.

    The engine never builds the Globex profile for an RTH strategy, so the gate
    builds its own in prepare() — from the *cached* overnight ticks spliced onto
    the engine's own frame, bars cut at the run's own ticks_per_bar, so the
    level in force at tick ``i`` is one a closed bar had already published
    (profile.levels_in_force). It cannot read ctx.profile: that one is anchored
    at the bell, and a different anchor is the entire point. Cache-only by
    doctrine; a day with no cached overnight is vetoed wholesale, and the splice
    means the gate is only correct on an RTH-frame strategy (same as gx_floor).

    Config section::

        {"gx_value": {"enabled": true, "max_ticks_inside": 0}}

    ``max_ticks_inside`` = 0 demands the fill be at or beyond the edge; raise it
    to tolerate a fill that far back inside the overnight value area.
    """

    name = "gx_value"
    needs_profile = False  # builds its own — ctx.profile is the wrong anchor

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require the fill beyond Globex value",
              default=False,
              help="Vetoes any entry that fills inside the developing Globex "
                   "value area (below its VAH on a long, above its VAL on a "
                   "short) — still trading the prior night's accepted prices."),
        Field("max_ticks_inside", "int", name,
              "Max distance back inside the value area", unit="ticks", min=0,
              default=0, depends_on=("enabled", True),
              help="How far back inside the Globex value area a fill may sit and "
                   "still pass. 0 demands the fill be at or beyond the edge (the "
                   "Globex VAH on a long, its VAL on a short)."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_value knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("max_ticks_inside", 0)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"max_ticks_inside must be a non-negative int, got {n!r}")
        self.max_ticks_inside = n
        self._edge: np.ndarray | None = None
        self._margin = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._margin = self.max_ticks_inside * tick_size(ctx.cfg.instrument)
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._edge = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no Globex anchor — veto everything
        comb = pd.concat([on, ctx.ticks], ignore_index=True)
        b = barsmod.tick_bars(comb, ctx.cfg.ticks_per_bar)
        prof = profmod.developing_profile(comb, b, tick_size(ctx.cfg.instrument))
        edge = "vah" if self._side > 0 else "val"
        line = profmod.levels_in_force(prof, b, len(comb), edge=edge)
        self._edge = line[len(on):]

    def allows(self, i: int, fill: float) -> bool:
        if self._edge is None:
            return False
        edge = self._edge[i]
        if np.isnan(edge):
            return False
        # Beyond the edge on the trade's side, or at most the margin back inside.
        return self._side * (fill - edge) >= -self._margin - 1e-9


class GxPocShapeGate:
    """Veto any entry while the developing Globex POC hangs just below the
    Globex VWAP — the thin-rally profile shape.

    The idea it enforces: when the night's volume node (its POC) sits a little
    below the night-anchored VWAP while price trades above both, the rally is
    running on low participation over unfilled value — recent prices dragged
    the mean up but acceptance stayed behind. The loss study of run 8762c799
    found the pocket directly: fills with the Globex POC 25–100 ticks below the
    Globex VWAP ran −$12.9k over 53 trades and, unlike every other geometry cut
    in that study, stayed negative on up-grade and flat days alike; the mirror
    shape (POC 25–100 ticks *above* the VWAP — a pullback into accepted value)
    was the run's best cell (+$27.2k at 65% WR). Mirrored by band side: on the
    lower band the toxic shape is the POC hanging just *above* the VWAP.

    Honesty clause: post-hoc cut of one run, n=53, monthly P&L mixed (negative
    7/11) — not a validated gate. This exists to A/B the shape off the ghost
    ledger, where the rearm/daily-loss-stop interactions a post-hoc cut cannot
    see are honest.

    Both lines are built from the *cached* overnight ticks spliced onto the
    engine's frame (same doctrine and same RTH-frame caveat as gx_floor and
    gx_value): the VWAP per tick, the POC per closed bar via
    profile.levels_in_force, so the node read at tick ``i`` is one a closed bar
    had already published. Cache-only; a day with no cached overnight is vetoed
    wholesale — "no data" must not read as "confirmed".

    Config section::

        {"gx_poc_shape": {"enabled": true, "zone_min_ticks": 25, "zone_max_ticks": 100}}

    The zone is where the veto LIVES, not a requirement: a POC beyond
    ``zone_max_ticks`` below the VWAP (a deep recovery day rallying over a far
    node) or at/above it passes.
    """

    name = "gx_poc_shape"
    needs_profile = False  # builds its own — ctx.profile is the wrong anchor

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Veto the thin-rally Globex shape",
              default=False,
              help="Vetoes any entry while the developing Globex POC sits "
                   "zone_min..zone_max ticks below the Globex VWAP (above it, "
                   "on a lower-band setup) — a low-participation rally over "
                   "unfilled value."),
        Field("zone_min_ticks", "int", name, "Zone starts (ticks below the VWAP)",
              unit="ticks", min=0, default=25, depends_on=("enabled", True),
              help="Inner edge of the veto zone: a POC at least this far below "
                   "the Globex VWAP (mirrored on a short) is the toxic shape. "
                   "Closer than this, POC and VWAP agree — that passes."),
        Field("zone_max_ticks", "int", name, "Zone ends (ticks below the VWAP)",
              unit="ticks", min=0, default=100, depends_on=("enabled", True),
              help="Outer edge of the veto zone. A POC even further below the "
                   "VWAP is a deep-recovery structure, not the thin rally, and "
                   "passes."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_poc_shape knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        lo = section.get("zone_min_ticks", 25)
        hi = section.get("zone_max_ticks", 100)
        for nm, v in (("zone_min_ticks", lo), ("zone_max_ticks", hi)):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{nm} must be a non-negative int, got {v!r}")
        if lo > hi:
            raise ValueError(
                f"zone_min_ticks must not exceed zone_max_ticks, got {lo} > {hi}")
        self.zone_min_ticks = lo
        self.zone_max_ticks = hi
        self._mid: np.ndarray | None = None
        self._poc: np.ndarray | None = None
        self._lo_pts = 0.0
        self._hi_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        tick = tick_size(ctx.cfg.instrument)
        self._lo_pts = self.zone_min_ticks * tick
        self._hi_pts = self.zone_max_ticks * tick
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._mid = self._poc = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no Globex anchor — veto everything
        comb = pd.concat([on, ctx.ticks], ignore_index=True)
        b = barsmod.tick_bars(comb, ctx.cfg.ticks_per_bar)
        prof = profmod.developing_profile(comb, b, tick)
        poc = profmod.levels_in_force(prof, b, len(comb), edge="poc")
        self._mid = vwapmod.vwap_bands(comb)["mid"].to_numpy(dtype="float64")[len(on):]
        self._poc = poc[len(on):]

    def allows(self, i: int, fill: float) -> bool:
        if self._mid is None or self._poc is None:
            return False
        mid, poc = self._mid[i], self._poc[i]
        if np.isnan(mid) or np.isnan(poc):
            return False
        # How far the node hangs behind the mean, on the traded side's reading:
        # below the VWAP for an upper-band setup, above it for a lower-band one.
        gap = self._side * (mid - poc)
        return not (self._lo_pts - 1e-9 <= gap <= self._hi_pts + 1e-9)


class RegimeGate:
    """Veto every entry after its checkpoint ET on a day whose morning so far
    has lived below the VWAPs.

    The idea it enforces: the band bounce needs price residing on the traded
    side of value to have anything to lean on. A session that has spent most of
    its morning below *both* anchored VWAPs (``bbr``, the below-both ratio from
    the regime artifact's checkpoint) is already telling you it is not that day
    — across the Oct–Dec sample, bbr at 10:30 was the strongest early predictor
    of the strategy bleeding for the rest of the session, and every day it
    flagged was a post-10:30 loser.

    The checkpoint is a knob (09:45 or 10:30, default 10:30). On the original
    Oct–Dec sample 09:45 predicted nothing, which is why 10:30 was once fixed —
    but the Jun 2025 – Jan 2026 09:30-entry study put nearly all of this
    strategy's edge before 10:30, the one window a 10:30 read can never
    protect, and its 09:45 board cleared the Bonferroni bar on five KPIs. Only
    the listed checkpoints are legal — a free-form read time would invite
    fitting the clock to the sample. Entries before the checkpoint pass
    untouched — the number does not exist yet, and a gate acting on it earlier
    would be trading on hindsight.

    Config section::

        {"regime": {"enabled": true, "bbr_max": 0.6, "checkpoint": "10:30"}}

    ``bbr_max`` is the stand-down threshold: at or above it, no entries for the
    rest of the session. The 0.6 default mirrors regime.classify()'s trend
    convention rather than anything tuned on strategy P&L.

    When the checkpoint cannot be read at all — no regime artifact, or a bbr of
    None because the session has no Globex anchor — the gate vetoes after the
    checkpoint. Same doctrine as the profile gate: "no data" must not read as
    "confirmed", and a day the dual-VWAP regime cannot describe is a day this
    filter has no business waving through.
    """

    name = "regime"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down on below-VWAP mornings",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "morning spent too long below both anchored VWAPs — the "
                   "regime in which the bounce has nothing to lean on."),
        Field("bbr_max", "float", name, "Max below-both-VWAPs ratio",
              min=0.0, max=1.0, default=0.6, depends_on=("enabled", True),
              help="Share of the morning spent below both VWAPs at or above "
                   "which the rest of the session is stood down, read at the "
                   "checkpoint. The default mirrors classify()'s trend "
                   "threshold; it was not fitted to strategy P&L."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="10:30",
              depends_on=("enabled", True),
              help="When the bbr is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown regime knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("bbr_max", 0.6)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"bbr_max must be a number in [0, 1], got {v!r}")
        self.bbr_max = float(v)
        self.checkpoint = _checkpoint(section, "10:30")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        bbr = ((art or {}).get("checkpoints", {}).get(self.checkpoint) or {}).get("bbr")
        if bbr is not None and bbr < self.bbr_max:
            self._blocked = None  # the morning qualified; the gate is inert today
            return
        # Stood down (or blind — see class docstring): veto every tick at or
        # after the checkpoint, by ET wall clock.
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class VwapCrossGate:
    """Veto every entry after its checkpoint ET on a day whose price has been
    churning back and forth across the NY VWAP.

    The idea it enforces: the bounce pays on days that pick a side of the NY
    VWAP and stay there; a morning that keeps recrossing it is rotation, and
    rotation is the regime in which a +1σ pullback keeps travelling. The regime
    artifact counts exactly that (``ny_vwap_cross_rate``, crossings of the NY
    VWAP per hour up to the checkpoint). On the Jun 2025 – Jan 2026 09:30-entry
    study it held past the Bonferroni bar at both readable checkpoints (09:45:
    ρ ≈ −0.31; 10:30: ρ ≈ −0.28, 500 permutations): the calmest tercile of days
    averaged +$339/day at 09:45 while the churniest averaged −$221/day.

    Honesty clause: the 12/hr default is that sample's 09:45 top-tercile
    boundary — a threshold read off the same days the board was computed on,
    not held-out evidence. And ghost-ledger arithmetic has over-promised before
    (entries interact through rearm), so the setting still has to earn its keep
    on an actual A/B run.

    Same clock discipline as the regime gate: entries before the checkpoint
    pass untouched, and the gate reads the artifact's checkpoint — never a
    fresher count — so what it acts on is exactly what was knowable.

    Config section::

        {"vwap_cross": {"enabled": true, "cross_max": 12.0, "checkpoint": "09:45"}}

    ``cross_max`` is the stand-down threshold in crossings per hour: at or
    above it, no entries for the rest of the session.

    Blind days — no artifact, or a checkpoint too thin to carry a rate — are
    vetoed after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "vwap_cross"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down on VWAP-churn mornings",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "price has crossed the NY VWAP too often — rotation, the "
                   "regime in which a band pullback keeps travelling."),
        Field("cross_max", "float", name, "Max NY VWAP crossings",
              unit="/ hr", min=0.0, max=60.0, default=12.0,
              depends_on=("enabled", True),
              help="Crossings of the NY VWAP per hour up to the checkpoint. At "
                   "or above this, the rest of the session is stood down. The "
                   "default is the churniest-tercile boundary of the Jun'25–"
                   "Jan'26 sample at 09:45."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="09:45",
              depends_on=("enabled", True),
              help="When the rate is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown vwap_cross knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("cross_max", 12.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 60.0:
            raise ValueError(f"cross_max must be a number in [0, 60], got {v!r}")
        self.cross_max = float(v)
        self.checkpoint = _checkpoint(section, "09:45")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        rate = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
                or {}).get("ny_vwap_cross_rate")
        if rate is not None and rate < self.cross_max:
            self._blocked = None  # the morning held its side; inert today
            return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class UpperOccupancyGate:
    """Veto every entry after its checkpoint ET on a day whose price has spent
    too little of the morning in the NY upper channel.

    The idea it enforces: the upper-band bounce is a bet that the market keeps
    living between the NY +1σ and +2σ; a morning that has barely visited that
    channel has not established the residence the bounce leans on. The regime
    artifact measures exactly that (``ny_upper_channel_occupancy``, the share
    of closed bars inside the channel up to the checkpoint). On the Jun 2025 –
    Jan 2026 09:30-entry study it was the strongest 10:30 discriminator to
    clear the Bonferroni bar (ρ ≈ +0.38, 500 permutations): the lowest tercile
    of days averaged −$367/day at a 37.9% win rate, the highest +$622/day at
    67.6% — and it already held at 09:45 (ρ ≈ +0.25).

    Honesty clause: the 0.17 default is that sample's 10:30 bottom-tercile
    boundary — a threshold read off the same days the board was computed on,
    not held-out evidence. And ghost-ledger arithmetic has over-promised before
    (entries interact through rearm), so the setting still has to earn its keep
    on an actual A/B run.

    Same clock discipline as the regime gate: entries before the checkpoint
    pass untouched, and the gate reads the artifact's checkpoint — never a
    fresher share — so what it acts on is exactly what was knowable.

    Config section::

        {"upper_occupancy": {"enabled": true, "occupancy_min": 0.17,
                             "checkpoint": "10:30"}}

    ``occupancy_min`` is the stand-down threshold: at or below it, no entries
    for the rest of the session.

    Blind days — no artifact, or a checkpoint with no closed bars — are vetoed
    after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "upper_occupancy"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down outside the upper channel",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "price has spent too little of the morning between the NY "
                   "+1σ and +2σ — the residence the bounce leans on."),
        Field("occupancy_min", "float", name, "Min upper-channel occupancy",
              min=0.0, max=1.0, default=0.17, depends_on=("enabled", True),
              help="Share of the morning's bars closed inside the NY upper "
                   "channel, read at the checkpoint. At or below this, the "
                   "rest of the session is stood down. The default is the "
                   "bottom-tercile boundary of the Jun'25–Jan'26 sample at "
                   "10:30."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="10:30",
              depends_on=("enabled", True),
              help="When the occupancy is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown upper_occupancy knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("occupancy_min", 0.17)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"occupancy_min must be a number in [0, 1], got {v!r}")
        self.occupancy_min = float(v)
        self.checkpoint = _checkpoint(section, "10:30")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        occ = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
               or {}).get("ny_upper_channel_occupancy")
        if occ is not None and occ > self.occupancy_min:
            self._blocked = None  # the morning lived up there; inert today
            return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class UpperOccupancyCapGate:
    """Veto every entry after its checkpoint ET on a day whose price has spent
    too much of the morning living in the NY upper channel.

    The occupancy gate's mirror: residence between the NY +1σ and +2σ is what
    the bounce leans on, and exactly what the dev1 fade short is betting
    against — a morning already camped up there is accepting those prices, not
    overextending past them. The fade-long reads the NY lower channel instead;
    the knob keeps its long-flavoured name and means "the faded side's channel"
    on both. Across the Aug 2025 – Jan 2026 variant-B fade
    study the 09:45 reading separated on most configs (ρ ≈ −0.25 to −0.31, 500
    permutations; past the holdout at 10:30 on several): the most-occupied
    tercile averaged −$390 to −$670/day while the least-occupied was flat to
    positive.

    Honesty clause: the 0.33 default is that sample's 09:45 top-tercile
    boundary — read off the same days the board was computed on, not held-out
    evidence — and tercile arithmetic over-promises (entries interact through
    rearm); the setting has to earn its keep on an actual A/B run.

    Same clock discipline as every checkpoint gate: entries before the
    checkpoint pass untouched, and the gate reads the artifact's checkpoint —
    never a fresher share — so what it acts on is exactly what was knowable.

    Config section::

        {"upper_occupancy_cap": {"enabled": true, "occupancy_max": 0.33,
                                 "checkpoint": "09:45"}}

    ``occupancy_max`` is the stand-down threshold: at or above it, no entries
    for the rest of the session.

    Blind days — no artifact, or a checkpoint with no closed bars — are vetoed
    after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "upper_occupancy_cap"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down inside the faded channel",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "price has spent too much of the morning inside the NY "
                   "channel the fade sells into (+1σ to +2σ on the short, −1σ "
                   "to −2σ on the long) — accepted residence, not the "
                   "overextension the fade trades."),
        Field("occupancy_max", "float", name, "Max faded-channel occupancy",
              min=0.0, max=1.0, default=0.33, depends_on=("enabled", True),
              help="Share of the morning's bars closed inside the NY channel on "
                   "the faded side, read at the checkpoint. At or above this, the "
                   "rest of the session is stood down. The default is the "
                   "top-tercile boundary of the Aug'25–Jan'26 fade study at "
                   "09:45."),
        Field("checkpoint", "enum", name, "Checkpoint",
              choices=_CHECKPOINT_CHOICES, default="09:45",
              depends_on=("enabled", True),
              help="When the occupancy is read, and from when the stand-down "
                   "applies. 09:45 is the earliest read the NY anchor can "
                   "carry — the only one that reaches the open-driven morning."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown upper_occupancy_cap knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("occupancy_max", 0.33)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"occupancy_max must be a number in [0, 1], got {v!r}")
        self.occupancy_max = float(v)
        self.checkpoint = _checkpoint(section, "09:45")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        # The channel on the setup's side of the market: a fade-long lives under
        # the lower band, and the residence it is betting against is residence
        # in the NY *lower* channel. The knob keeps its long-flavoured name and
        # reads as "channel occupancy on the faded side" (see VolumeProfileGate).
        key = ("ny_upper_channel_occupancy" if ctx.band_side() == "upper"
               else "ny_lower_channel_occupancy")
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        occ = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
               or {}).get(key)
        if occ is not None and occ < self.occupancy_max:
            self._blocked = None  # the morning stayed out of the channel; inert
            return
        self._blocked = _veto_from(ctx, CHECKPOINT_MINUTES[self.checkpoint])

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]
