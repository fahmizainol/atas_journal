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
from . import weekly as weeklymod
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


class VwapFlatGate:
    """Veto every entry after its checkpoint ET unless the day is FLAT — the NY
    VWAP grade at the checkpoint under the threshold in BOTH directions.

    The balance-day gate. The cap gates read one direction each: the slope cap
    stands a fade down when the grade runs away from the mean it reverts to,
    and is inert on a day grinding the other way. But the mean-reversion
    premise — value is where it should be, the extremes are excursions — is a
    claim about *balance*, not about the absence of one particular trend. This
    gate states it directly: |slope| at or above ``grade_max`` is a day that
    has picked a direction, and the fade stands down whichever way it points.

    Against the slope cap it is strictly tighter on one side and new on the
    other: with equal thresholds it vetoes every day the cap does, plus the
    days graded toward the fade (where the fade rarely arms, but the fills it
    does get are counter-trend entries on a directional tape).

    Honesty clause: the 1.2 pts/min default is the flattest-tercile boundary
    of |ny_vwap_slope_ppm| at 09:45 across the whole cached regime sample
    (~825 days, boundary ≈ 1.16) — a distributional cut, not an outcome-fitted
    one. At 10:30 the same boundary is ≈ 0.33 (the 30-minute window smooths
    the open's impulse away) — a 10:30 read wants its own, much tighter
    threshold, not this default.

    And the A/B verdict, Aug 2025 – Jan 2026, fade-short: the gate LOST.
    On the far-band-target baseline it vetoed ~$9k of net winners (the
    opp_dev1 traversal needs a graded tape — the fade's paying days slope
    DOWN, ~+$305/day, vs ~+$102 flat and −$248 up); and rebuilt as the
    textbook balance trade (mid target, variant A limit at the band) the
    flat-gated fade came out exactly breakeven, PF 1.00 over 249 trades.
    The gate stays because "is this a balance-day edge?" keeps being asked,
    and one enabled section answers it from the ghost ledger — not because
    a profitable setting is known. Runs efe7d5fb / cf7c42c0 / 5c654ebc.

    Same clock discipline as every checkpoint gate: entries before the
    checkpoint pass untouched, and the gate reads the artifact's checkpoint —
    never a fresher slope — so what it acts on is exactly what was knowable.

    Config section::

        {"vwap_flat": {"enabled": true, "grade_max": 1.2, "checkpoint": "09:45"}}

    ``grade_max`` is the stand-down threshold in points per minute, read as a
    magnitude: at or above it — either sign — no entries for the rest of the
    session.

    Blind days — no artifact, or a checkpoint too thin to carry a slope — are
    vetoed after the checkpoint. "No data" must not read as "confirmed".
    """

    name = "vwap_flat"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down unless the day is flat",
              default=False,
              help="After the checkpoint ET, vetoes every entry on a day whose "
                   "NY VWAP slope at the checkpoint has reached the threshold "
                   "in EITHER direction — the day has picked a grade, and the "
                   "balance premise of the fade is gone."),
        Field("grade_max", "float", name, "Max |NY VWAP slope| at the checkpoint",
              unit="pts/min", min=0.0, max=5.0, default=1.2,
              depends_on=("enabled", True),
              help="Magnitude of the NY-anchored VWAP slope into the "
                   "checkpoint, either sign. At or above this, the rest of the "
                   "session is stood down. The default is the flattest-tercile "
                   "boundary of the cached regime sample at 09:45; a 10:30 "
                   "read wants a much tighter setting (its boundary is ≈0.33)."),
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
                f"unknown vwap_flat knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("grade_max", 1.2)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 5.0:
            raise ValueError(f"grade_max must be a number in [0, 5], got {v!r}")
        self.grade_max = float(v)
        self.checkpoint = _checkpoint(section, "09:45")
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        # A magnitude read needs no band frame: flat is flat on both sides, so
        # the short and the long fade read the same number the same way.
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        slope = ((art or {}).get("checkpoints", {}).get(self.checkpoint)
                 or {}).get("ny_vwap_slope_ppm")
        if slope is not None and abs(slope) < self.grade_max:
            self._blocked = None  # the day is flat; the gate is inert today
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

        {"gx_floor": {"enabled": true, "max_ticks_below": 80, "max_ticks_above": 0}}

    ``max_ticks_above`` (default 0 — the original rule) lets the line sit that
    far on the *wrong* side of the fill and still count: the 8762c799 setup
    study's best cell filled anywhere from 50 ticks below the Globex dev1 to
    150 above it, and a strict floor cannot express the lower half of that
    window.
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
        Field("max_ticks_above", "int", name, "Max overshoot past the line",
              unit="ticks", min=0, default=0, depends_on=("enabled", True),
              help="How far the line may sit on the WRONG side of the fill "
                   "(above it on a long) and still count — a fill slightly "
                   "under the Globex dev1 is still trading against it. 0 keeps "
                   "the strict floor."),
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
        a = section.get("max_ticks_above", 0)
        if not isinstance(a, int) or isinstance(a, bool) or a < 0:
            raise ValueError(f"max_ticks_above must be a non-negative int, got {a!r}")
        self.max_ticks_below = n
        self.max_ticks_above = a
        self._line: np.ndarray | None = None
        self._max_pts = 0.0
        self._above_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._max_pts = self.max_ticks_below * tick_size(ctx.cfg.instrument)
        self._above_pts = self.max_ticks_above * tick_size(ctx.cfg.instrument)
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
        # fill, above a short's, by at most the configured distance — or on the
        # wrong side by at most the configured overshoot.
        gap = self._side * (fill - line)
        return -self._above_pts - 1e-9 <= gap <= self._max_pts + 1e-9


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

        {"gx_poc_shape": {"enabled": true, "zone_min_ticks": 25,
                          "zone_max_ticks": 100, "mode": "veto"}}

    In ``mode: "veto"`` (the default) the zone is where the veto LIVES, not a
    requirement: a POC beyond ``zone_max_ticks`` below the VWAP (a deep
    recovery day rallying over a far node) or at/above it passes. In
    ``mode: "require_mirror"`` the gate flips into the setup study's positive
    read — only fills whose Globex POC sits ``zone_min..zone_max`` ticks on
    the FAR side of the VWAP (above it, on a long) pass: the night built its
    acceptance high, and the pullback lands into accepted value. That mirror
    shape was the 8762c799 study's best cell (+$27.2k at 65% WR over 66).
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
        Field("zone_min_ticks", "int", name, "Zone starts (ticks from the VWAP)",
              unit="ticks", min=0, default=25, depends_on=("enabled", True),
              help="Inner edge of the zone, measured from the Globex VWAP. In "
                   "veto mode the zone sits below it (the thin-rally side, "
                   "mirrored on a short); in require_mirror mode the same "
                   "distances apply above it (the accepted-value side). Closer "
                   "than this, POC and VWAP agree."),
        Field("zone_max_ticks", "int", name, "Zone ends (ticks from the VWAP)",
              unit="ticks", min=0, default=100, depends_on=("enabled", True),
              help="Outer edge of the zone. A POC even further out is a "
                   "deep-recovery structure, not the shape this gate reads — "
                   "it passes a veto and fails the mirror requirement."),
        Field("mode", "enum", name, "Mode",
              choices=(("veto", "veto — block the thin-rally zone"),
                       ("require_mirror", "require mirror — only accepted-value "
                                          "shapes pass")),
              default="veto", depends_on=("enabled", True),
              help="veto blocks fills while the POC hangs in the zone below "
                   "the VWAP. require_mirror passes ONLY fills whose POC sits "
                   "in the zone on the far side (above the VWAP on a long) — "
                   "the setup study's accepted-value shape."),
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
        mode = section.get("mode", "veto")
        if mode not in ("veto", "require_mirror"):
            raise ValueError(f"mode must be veto|require_mirror, got {mode!r}")
        self.mode = mode
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
        if self.mode == "require_mirror":
            # Only the mirror shape passes: the node the same zone beyond the
            # VWAP on the traded side (above it, for an upper-band setup).
            return self._lo_pts - 1e-9 <= -gap <= self._hi_pts + 1e-9
        return not (self._lo_pts - 1e-9 <= gap <= self._hi_pts + 1e-9)


class NyPocFloorGate:
    """Veto any entry without the developing NY POC as a node just beneath it.

    The idea it enforces: a dev1 pullback that lands with the session's own
    volume node directly underneath is landing on defended ground; one whose
    POC sits far below is floating over an air pocket down to it. The 8762c799
    loss study measured both halves: fills with the NY POC 0–100 ticks below
    ran 61% WR for +$37.2k (negative in only 2 of 12 months — the stable core
    of the run), and 85% of stopped losses completed the rotation all the way
    to the POC within 30 minutes. Stacked with the widened gx_floor it was the
    study's strongest robust setup (72.5% WR, n=69, held in both halves).

    Honesty clause: post-hoc cut of one run, not a validated gate — this
    exists to A/B the setup off the ghost ledger, where the rearm and
    daily-loss-stop interactions a post-hoc cut cannot see are honest.

    Mirrored by band side: on the lower band the node must sit just *above*
    the fill. Reads the engine's own developing profile (``needs_profile``
    makes the engine build it), through profile.levels_in_force — the node
    judged at tick ``i`` is one a closed bar had already published. Ticks
    before the first bar close have no profile and are vetoed: "no data" must
    not read as "confirmed".

    Config section::

        {"ny_poc_floor": {"enabled": true, "max_ticks_below": 100}}
    """

    name = "ny_poc_floor"
    needs_profile = True

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require the NY POC just beneath the fill",
              default=False,
              help="Vetoes any entry whose fill does not have the developing "
                   "session POC within reach beneath it (above it, on a "
                   "lower-band setup) — the defended node the pullback lands "
                   "on."),
        Field("max_ticks_below", "int", name, "Max distance to the node",
              unit="ticks", min=0, default=100, depends_on=("enabled", True),
              help="How far beyond the fill the developing POC may sit and "
                   "still count. Past stop distance the node rescues nobody; "
                   "0 demands the fill sit on the node itself."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown ny_poc_floor knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("max_ticks_below", 100)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"max_ticks_below must be a non-negative int, got {n!r}")
        self.max_ticks_below = n
        self._poc: np.ndarray | None = None
        self._max_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._max_pts = self.max_ticks_below * tick_size(ctx.cfg.instrument)
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._poc = None
        if ctx.profile is None or ctx.bars.empty:
            return  # nothing to read — veto everything rather than guess
        self._poc = profmod.levels_in_force(
            ctx.profile, ctx.bars, len(ctx.ticks), edge="poc")

    def allows(self, i: int, fill: float) -> bool:
        if self._poc is None:
            return False
        poc = self._poc[i]
        if np.isnan(poc):
            return False
        # The node on the traded side of the fill, within reach: below a
        # long's fill, above a short's, by at most the configured distance.
        gap = self._side * (fill - poc)
        return 0.0 <= gap <= self._max_pts + 1e-9


class GxOverhangGate:
    """Veto any entry while the Globex VWAP hangs too far over the NY VWAP.

    The idea it enforces: when the night-anchored VWAP sits well above the
    session's, the RTH rally is climbing into the overnight's average
    inventory — every tick higher hands the night's longs a better exit, and
    the session bounce is selling pressure's guest. The 8762c799 loss study
    found the band directly: fills with the Globex VWAP 50–200 ticks above the
    NY VWAP ran 43% WR for −$10.0k, while every other reading of the spread
    was fine — including the deep (>200t) gap, which is a recovery day, not an
    overhang, so this gate caps the spread rather than requiring a sign.

    Honesty clause: post-hoc cut of one run, not a validated gate — this
    exists to A/B the spread cap off the ghost ledger (it is half of the
    study's "accepted-value" setup, with gx_poc_shape's require_mirror).

    Mirrored by band side: on the lower band the overhang is the Globex VWAP
    hanging that far *below* the NY VWAP. The NY line is the engine's own
    anchor recomputed here; the Globex line comes from the *cached* overnight
    ticks spliced onto the engine's frame (cache-only doctrine, RTH-frame
    caveat — same as gx_floor). A day with no cached overnight is vetoed
    wholesale.

    Config section::

        {"gx_overhang": {"enabled": true, "max_ticks": 50}}
    """

    name = "gx_overhang"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Cap the Globex-over-NY VWAP spread",
              default=False,
              help="Vetoes any entry while the Globex VWAP sits more than "
                   "max_ticks above the NY VWAP (below it, on a lower-band "
                   "setup) — rallying into the night's average inventory."),
        Field("max_ticks", "int", name, "Max overhang",
              unit="ticks", min=0, default=50, depends_on=("enabled", True),
              help="How far the Globex VWAP may sit on the overhead side of "
                   "the NY VWAP before entries stand down. A spread the other "
                   "way (Globex beneath) always passes."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown gx_overhang knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("max_ticks", 50)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"max_ticks must be a non-negative int, got {n!r}")
        self.max_ticks = n
        self._ny: np.ndarray | None = None
        self._gx: np.ndarray | None = None
        self._max_pts = 0.0
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._max_pts = self.max_ticks * tick_size(ctx.cfg.instrument)
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        self._ny = self._gx = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no Globex anchor — veto everything
        self._ny = vwapmod.vwap_bands(ctx.ticks)["mid"].to_numpy(dtype="float64")
        comb = pd.concat([on, ctx.ticks], ignore_index=True)
        self._gx = vwapmod.vwap_bands(comb)["mid"].to_numpy(dtype="float64")[len(on):]

    def allows(self, i: int, fill: float) -> bool:
        if self._ny is None or self._gx is None:
            return False
        ny, gx = self._ny[i], self._gx[i]
        if np.isnan(ny) or np.isnan(gx):
            return False
        # The spread on the overhead side of the setup: Globex above NY for an
        # upper-band long, below it for the mirror. Negative spread (Globex on
        # the friendly side) always passes.
        return self._side * (gx - ny) <= self._max_pts + 1e-9


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


def _ib_extremes(ctx: "SessionCtx", ib_minutes: int) -> tuple[float, float] | None:
    """The Initial Balance's high/low: RTH ticks in [09:30, 09:30+ib_minutes),
    read off the ET wall clock so the same expression is correct on an RTH frame
    and on a globex splice (overnight minutes never land inside the window).
    None when the window holds no ticks — a session with no IB has no gate."""
    et = ctx.ticks["ts_utc"].dt.tz_convert(ET_TZ)
    mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    mask = (mins >= 570) & (mins < 570 + ib_minutes)
    if not mask.any():
        return None
    px = ctx.ticks["price"].to_numpy(dtype="float64")[mask]
    return float(px.max()), float(px.min())


def _ib_minutes_knob(section: dict, name: str) -> int:
    v = section.get("ib_minutes", 60)
    if isinstance(v, bool) or not isinstance(v, int) or not 15 <= v <= 120:
        raise ValueError(f"{name} ib_minutes must be an int in [15, 120], got {v!r}")
    return v


class IbInOnGate:
    """Veto entries by whether the Initial Balance stayed inside the overnight
    range — the rotation-day read, applied from the moment the IB completes.

    The idea it enforces: an IB that never left the Globex range is a morning
    that added no information — the night's balance is still intact, and the
    day leans rotational. The IB study (NQ Feb 2025 – Jan 2026, 257 sessions)
    found IB-inside-ON days break BOTH IB sides 35.2% of the time against a
    20.6% base rate, while an IB that had already broken an ON extreme drops
    to 14–19%: containment predicts rotation, escape predicts one-sidedness.
    ``veto_inside`` stands a directional strategy down on the containment days;
    ``require_inside`` is the mirror for a rotation strategy that wants ONLY
    those days.

    Honesty clause: a distributional cut of the IB study, not an outcome-
    validated gate — both-break rate is a claim about structure, not P&L. This
    exists to A/B the idea off the ghost ledger.

    Clock discipline: the containment verdict does not exist until the IB is
    complete, so entries before 09:30+ib_minutes pass untouched — the gate
    never acts on information the session didn't have yet. Cache-only by
    doctrine: a day with no cached overnight has no ON range, and every
    post-IB entry is vetoed — "no data" must not read as "confirmed".

    Config section::

        {"ib_in_on": {"enabled": true, "mode": "veto_inside", "ib_minutes": 60}}
    """

    name = "ib_in_on"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Gate on IB-inside-overnight containment",
              default=False,
              help="From the moment the Initial Balance completes, vetoes "
                   "entries by whether the IB stayed inside the overnight "
                   "range. Containment days break both IB sides at ~1.7x the "
                   "base rate (the IB study's strongest cut) — they lean "
                   "rotational, not directional."),
        Field("mode", "enum", name, "Mode",
              choices=(("veto_inside", "Veto containment days — directional strategies"),
                       ("require_inside", "Only containment days — rotation strategies")),
              default="veto_inside", depends_on=("enabled", True),
              help="veto_inside stands the strategy down when the IB stayed "
                   "inside the overnight range; require_inside passes only "
                   "then. Entries before the IB completes always pass — the "
                   "verdict doesn't exist yet."),
        Field("ib_minutes", "int", name, "IB window", unit="min", min=15,
              max=120, default=60, depends_on=("enabled", True),
              help="Length of the Initial Balance, from the 09:30 bell. 60 is "
                   "the two-TPO convention; the study's numbers were measured "
                   "there."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown ib_in_on knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        mode = section.get("mode", "veto_inside")
        if mode not in ("veto_inside", "require_inside"):
            raise ValueError(
                f"mode must be 'veto_inside' or 'require_inside', got {mode!r}")
        self.mode = mode
        self.ib_minutes = _ib_minutes_knob(section, self.name)
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        self._blocked = None
        after_ib = _veto_from(ctx, 570 + self.ib_minutes)
        ib = _ib_extremes(ctx, self.ib_minutes)
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if ib is None or on is None or on.empty:
            self._blocked = after_ib  # blind: no IB or no overnight — veto post-IB
            return
        ib_hi, ib_lo = ib
        inside = ib_hi <= float(on["price"].max()) and ib_lo >= float(on["price"].min())
        if inside == (self.mode == "veto_inside"):
            self._blocked = after_ib

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class IbWidthGate:
    """Veto entries by the Initial Balance's width, from the moment it completes.

    The IB study's conditioner: IB width (relative to recent daily range) is
    monotonic on rotation — the narrow tercile broke both IB sides 33% of the
    time, the wide tercile 8.6% (NQ Feb 2025 – Jan 2026). A directional
    strategy may want the wide, contained days (``min_ticks``); a rotation
    strategy the narrow, breakable ones (``max_ticks``). Thresholds are
    absolute ticks — read the study's tercile boundaries (0.44x / 0.66x ADR)
    against the window's own average day range to pick them.

    Honesty clause: same as ib_in_on — a distributional cut, not an outcome-
    validated gate, and part of the narrow-IB both-break lift is mechanical
    (a narrow IB needs less absolute travel to be exceeded twice). A/B it off
    the ghost ledger.

    Same clock discipline as ib_in_on: before 09:30+ib_minutes the width isn't
    knowable and entries pass untouched. Needs no overnight — the IB is read
    off the session's own ticks.

    Config section::

        {"ib_width": {"enabled": true, "min_ticks": 0, "max_ticks": 0, "ib_minutes": 60}}
    """

    name = "ib_width"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Gate on Initial Balance width",
              default=False,
              help="From the moment the IB completes, vetoes entries when its "
                   "high-low range falls outside the bounds. Narrow IBs lean "
                   "rotational (both-break 33% vs 8.6% wide, in the IB "
                   "study's ADR terciles); wide IBs lean contained."),
        Field("min_ticks", "int", name, "Min IB range", unit="ticks", min=1,
              zero_means_off=True, on_default=400, default=0,
              depends_on=("enabled", True),
              help="0 = no lower bound. Vetoes post-IB entries on days whose "
                   "IB range is narrower than this — the rotational tail."),
        Field("max_ticks", "int", name, "Max IB range", unit="ticks", min=1,
              zero_means_off=True, on_default=800, default=0,
              depends_on=("enabled", True),
              help="0 = no upper bound. Vetoes post-IB entries on days whose "
                   "IB range is wider than this — the exhausted tail."),
        Field("ib_minutes", "int", name, "IB window", unit="min", min=15,
              max=120, default=60, depends_on=("enabled", True),
              help="Length of the Initial Balance, from the 09:30 bell."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown ib_width knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        for k in ("min_ticks", "max_ticks"):
            v = section.get(k, 0)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{k} must be a non-negative int, got {v!r}")
        self.min_ticks = section.get("min_ticks", 0)
        self.max_ticks = section.get("max_ticks", 0)
        if self.min_ticks and self.max_ticks and self.min_ticks > self.max_ticks:
            raise ValueError(
                f"min_ticks ({self.min_ticks}) may not exceed "
                f"max_ticks ({self.max_ticks})")
        self.ib_minutes = _ib_minutes_knob(section, self.name)
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        self._blocked = None
        ib = _ib_extremes(ctx, self.ib_minutes)
        if ib is None:
            self._blocked = _veto_from(ctx, 570 + self.ib_minutes)
            return
        width_t = (ib[0] - ib[1]) / tick_size(ctx.cfg.instrument)
        narrow = self.min_ticks and width_t < self.min_ticks
        wide = self.max_ticks and width_t > self.max_ticks
        if narrow or wide:
            self._blocked = _veto_from(ctx, 570 + self.ib_minutes)

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]


class WkExtGate:
    """Veto entries filling stretched beyond the weekly VWAP envelope.

    The idea it enforces: the weekly anchor (the week's first Globex open — see
    ``weekly.py``) is a slow regime measure, and the weekly-VWAP study found its
    +2σ edge is where the bounce's habitat ends. On the 398-trade v10 baseline,
    entries above weekly mid + 2σ were −$14.5k across 48 trades at a 40% stop
    rate, negative in 5 of 6 quarters; the session-level study independently
    found >+2σ opens revert (median −56 pts). So the gate vetoes any fill more
    than ``max_sigma`` weekly sigmas beyond the weekly mid on the setup's side.

    Mirrored by band side like on_high: on a lower-band setup the veto is a
    fill stretched *below* mid − max_sigma·σ.

    The week's first session is INERT, not blind — a deliberate exception to
    "no data must not read as confirmed": on that day the weekly anchor is by
    definition the session's own Globex anchor (zero seed), "a week of
    accumulated value" does not exist yet as a premise, and every live trader
    knows it is Monday. The study's first-session subset is also the run's best
    (win 74%, avg R +0.27), so vetoing it would be the gate trading on its own
    absence. A *hole* — a prior session whose ticks were never bought, or a
    session with no cached overnight — is genuinely missing data, and there the
    gate is blind and vetoes everything, exactly like on_high.

    Cache-only by doctrine (a gate must never spend at Databento). Splices the
    cached overnight itself to seed today's accumulation, so it is only correct
    on an RTH-frame strategy (same as gx_floor / on_high) — a globex frame
    already holds the night and would double-count it.

    Honesty clause: the post-hoc cut on the CURRENT v12 baseline (222 trades,
    reenter_after_stop_only on) is far weaker — the reenter knob already
    removed most of that pocket. This gate exists to A/B what remains off the
    ghost ledger, not to enshrine the v10 numbers.

    Config section::

        {"wk_ext": {"enabled": true, "max_sigma": 2.0}}
    """

    name = "wk_ext"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Cap entries at the weekly envelope",
              default=False,
              help="Vetoes any entry filling more than max_sigma weekly sigmas "
                   "beyond the weekly VWAP mid on the setup's side (below it, "
                   "on a lower-band setup). Inert on the week's first session, "
                   "where no weekly history exists yet."),
        Field("max_sigma", "float", name, "Max weekly sigmas from the mid",
              unit="σ", min=0.0, default=2.0, depends_on=("enabled", True),
              help="How far beyond the weekly mid a fill may sit, in weekly "
                   "band widths. 2.0 = the weekly dev2 edge, where the study "
                   "found the bounce's habitat ends."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown wk_ext knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        s = section.get("max_sigma", 2.0)
        if isinstance(s, bool) or not isinstance(s, (int, float)) or s < 0:
            raise ValueError(f"max_sigma must be a non-negative number, got {s!r}")
        self.max_sigma = float(s)
        self._mid: np.ndarray | None = None
        self._std: np.ndarray | None = None
        self._inert = False
        self._side = 1.0

    def prepare(self, ctx: "SessionCtx") -> None:
        self._mid = self._std = None
        self._inert = False
        self._side = 1.0 if ctx.band_side() == "upper" else -1.0
        seed = weeklymod.weekly_seed(ctx.cfg.contract, ctx.day)
        if seed is None:
            return  # blind: the week has a hole — veto everything
        if seed == (0.0, 0.0, 0.0):
            self._inert = True  # the week's first session: no premise, no gate
            return
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: today's own night is missing from the accumulation
        s_on = vwapmod.frame_sums(on)
        w = vwapmod.vwap_bands(ctx.ticks, seed=(
            seed[0] + s_on[0], seed[1] + s_on[1], seed[2] + s_on[2]))
        self._mid = w["mid"].to_numpy()
        self._std = w["std"].to_numpy()

    def allows(self, i: int, fill: float) -> bool:
        if self._inert:
            return True
        if self._mid is None:
            return False
        # How far the fill sits beyond the weekly mid on the setup's side, in
        # points; passes while within max_sigma weekly bands. A fill on the
        # mid's other side is a negative gap and always passes.
        gap = self._side * (fill - self._mid[i])
        return gap <= self.max_sigma * self._std[i] + 1e-9


def _minute_bars(on: pd.DataFrame, ticks: pd.DataFrame) -> pd.DataFrame:
    """1-min OHLC bars over the cached overnight spliced onto the RTH frame,
    with each bar's END timestamp — the moment it becomes readable. The
    market-structure gates read texture off these, not the engine's tick bars,
    because the study's features were defined on wall-clock minutes."""
    px = pd.concat([on["price"], ticks["price"]], ignore_index=True).astype(float)
    ts = pd.DatetimeIndex(pd.concat(
        [on["ts_utc"], ticks["ts_utc"]], ignore_index=True))
    s = pd.Series(px.to_numpy(), index=ts)
    bars = pd.DataFrame({
        "h": s.resample("1min").max(),
        "l": s.resample("1min").min(),
    }).dropna()
    bars["end"] = bars.index + pd.Timedelta(minutes=1)
    return bars.reset_index(drop=True)


def _last_closed_bar_at_tick(bar_end: np.ndarray, ticks: pd.DataFrame) -> np.ndarray:
    """For each tick, the index of the last 1-min bar already CLOSED at that
    tick's timestamp (-1 if none). Only closed bars are readable — a gate
    peeking into the bar a fill prints in would be reading its own future."""
    tick_ts = ticks["ts_utc"].to_numpy(dtype="datetime64[ns]")
    return np.searchsorted(bar_end, tick_ts, side="right") - 1


class ChopGate:
    """Veto entries out of overlapping, directionless tape.

    The idea it enforces: the bounce is a with-trend pullback buy, and the
    market-structure study found the one structural feature that separates
    stops from the rest at entry time — in both runs, all four cohorts, both
    years, both halves of the day — is the bar-to-bar range overlap of the ten
    1-min bars before entry (AUC 0.61–0.64, stable halves; choppiest quintile
    stop rate 33–41% vs 9–21% cleanest). When the last ten minutes are
    rotation, the "pullback" being bought has no impulse behind it. Even at
    the −0.40R matched-depth anchor, where every tape feature died, this
    entry-time number still separated stop from recover.

    The measure: over the ten most recently closed 1-min bars, each consecutive
    pair contributes (range intersection ÷ the pair's mean range), clipped to
    [0, 1]; the gate reads the mean and vetoes above ``max_overlap``. High =
    bars sitting on top of each other; low = bars marching. The window is fixed
    at ten bars — the studied definition — on purpose: a width knob would be a
    second axis to overfit.

    Texture has no side: chop is chop above or below the market, so nothing
    mirrors. The bars are wall-clock minutes spliced from the cached overnight
    onto the engine's RTH frame (the study's frame), so the window is full even
    at the bell. Cache-only by doctrine; a day with no cached overnight is
    vetoed wholesale — "no data" must not read as "confirmed" — and the splice
    means the gate is only correct on an RTH-frame strategy (same as gx_floor
    / on_high / wk_ext). A window containing zero-range pairs only is likewise
    blind, and vetoes.

    Honesty clause: the study's static counterfactual (cut the choppiest
    tercile, keep 79–95% of net) is a post-hoc cut that cannot see the
    rearm/daily-loss-stop interactions — the weekly-VWAP gate looked this good
    on paper and lost the A/B. This gate exists to run that A/B off the ghost
    ledger, not to enshrine the cut.

    Config section::

        {"chop": {"enabled": true, "max_overlap": 0.60}}
    """

    name = "chop"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Veto entries out of choppy tape",
              default=False,
              help="Vetoes any entry when the ten 1-min bars just closed "
                   "overlap each other more than max_overlap on average — "
                   "rotation, not a pullback with impulse behind it."),
        Field("max_overlap", "float", name, "Max mean bar overlap", min=0.0,
              max=1.0, default=0.60, depends_on=("enabled", True),
              help="Mean consecutive-bar range overlap (0 = bars never touch, "
                   "1 = every bar inside the last) above which entries are "
                   "vetoed. The study's tercile cut sits near 0.60; its "
                   "choppiest-quintile edge near 0.65."),
    )

    KNOBS = {f.name for f in SCHEMA}
    WINDOW = 10  # closed 1-min bars — the studied definition, not a knob

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown chop knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        v = section.get("max_overlap", 0.60)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"max_overlap must be a number in [0, 1], got {v!r}")
        self.max_overlap = float(v)
        self._overlap_at_tick: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        self._overlap_at_tick = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no full window at the bell — veto
        bars = _minute_bars(on, ctx.ticks)
        h, l = bars["h"].to_numpy(), bars["l"].to_numpy()
        # Overlap of each consecutive closed-bar pair, then the trailing mean of
        # the WINDOW-1 pairs inside each ten-bar window, stamped on the window's
        # last bar. Zero-range pairs carry no texture and drop out of the mean.
        inter = np.minimum(h[1:], h[:-1]) - np.maximum(l[1:], l[:-1])
        rng = ((h[1:] - l[1:]) + (h[:-1] - l[:-1])) / 2.0
        valid = rng > 0
        pair = np.where(valid, np.clip(
            np.divide(inter, rng, out=np.zeros_like(rng), where=valid), 0, 1), 0.0)
        w = self.WINDOW - 1
        kern = np.ones(w)
        per_bar = np.full(len(bars), np.nan)
        if len(pair) >= w:
            sums = np.convolve(pair, kern, mode="valid")
            counts = np.convolve(valid.astype(float), kern, mode="valid")
            with np.errstate(invalid="ignore"):
                means = np.where(counts > 0, sums / counts, np.nan)
            per_bar[w:] = means  # window ends at bar j -> pairs j-9..j-1
        bar_at = _last_closed_bar_at_tick(
            bars["end"].to_numpy(dtype="datetime64[ns]"), ctx.ticks)
        vals = np.full(len(bar_at), np.nan)
        ok = bar_at >= 0
        vals[ok] = per_bar[bar_at[ok]]
        self._overlap_at_tick = vals

    def allows(self, i: int, fill: float) -> bool:
        if self._overlap_at_tick is None:
            return False
        v = self._overlap_at_tick[i]
        if np.isnan(v):
            return False  # blind window must not read as confirmed
        return v <= self.max_overlap + 1e-9


class StructureClarityGate:
    """Veto entries taken while the swing structure is mixed.

    The idea it enforces: the market-structure study's second robust cut —
    trend CLARITY, not direction. Classify the causal zigzag's last confirmed
    swings at entry: higher high + higher low is a confirmed uptrend, lower
    high + lower low a confirmed downtrend, one of each is a market
    mid-transition. Both confirmed states paid on both study runs; the mixed
    state was the toxic one (−$19.2k across 115 trades on the 398-trade run,
    $3.5k across 57 on the current baseline) — and it is near-independent of
    the chop gate (r ≈ 0.09), so the two vetoes stack rather than alias.

    The zigzag is causal: a running extreme becomes a confirmed pivot only
    once price has retraced ``zz_ticks`` from it, and the gate reads only
    pivots whose confirming retrace completed inside an already-closed 1-min
    bar. No hindsight pivots — the state at tick ``i`` is the one a live
    trader watching closed bars would have drawn.

    Clarity has no side — mixed is mixed above or below the market — so
    nothing mirrors; a confirmed DOWNTREND passes a long bounce on purpose
    (the study's clear-down cell was profitable: V-day band bounces). Bars are
    wall-clock minutes spliced from the cached overnight onto the RTH frame,
    so the night's swings are on the map at the bell. Cache-only by doctrine;
    no cached overnight vetoes the day wholesale, and fewer than two confirmed
    highs and two lows also vetoes — "not enough structure to read" must not
    read as "clear". RTH-frame strategies only (same as gx_floor / on_high).

    Honesty clause: same as chop — the study's cell table is a post-hoc cut;
    this gate exists to A/B it off the ghost ledger.

    Config section::

        {"structure_clarity": {"enabled": true, "zz_ticks": 40}}
    """

    name = "structure_clarity"
    needs_profile = False

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Require a confirmed swing trend",
              default=False,
              help="Vetoes any entry while the 1-min zigzag's last swings "
                   "disagree (one higher extreme, one lower) — a market "
                   "mid-transition. Confirmed uptrends AND downtrends both "
                   "pass; ambiguity is what loses."),
        Field("zz_ticks", "int", name, "Zigzag reversal threshold", unit="ticks",
              min=1, default=40, depends_on=("enabled", True),
              help="How far price must retrace from a running extreme before "
                   "it becomes a confirmed swing pivot. 40 ticks (10 NQ "
                   "points) is the studied threshold."),
    )

    KNOBS = {f.name for f in SCHEMA}

    def __init__(self, section: dict):
        unknown = set(section) - self.KNOBS
        if unknown:
            raise ValueError(
                f"unknown structure_clarity knobs {sorted(unknown)} "
                f"(available: {sorted(self.KNOBS)})"
            )
        n = section.get("zz_ticks", 40)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"zz_ticks must be a positive int, got {n!r}")
        self.zz_ticks = n
        self._clear_at_tick: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        self._clear_at_tick = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: the night's swings are half the map — veto
        bars = _minute_bars(on, ctx.ticks)
        h, l = bars["h"].to_numpy(), bars["l"].to_numpy()
        thr = self.zz_ticks * tick_size(ctx.cfg.instrument)

        # Causal zigzag: pivots in confirmation order, each stamped with the
        # bar whose close made it official.
        pivots: list[tuple[float, str, int]] = []  # (price, kind, confirm_bar)
        direction, max_i, min_i = 0, 0, 0
        for i in range(len(h)):
            if h[i] >= h[max_i]:
                max_i = i
            if l[i] <= l[min_i]:
                min_i = i
            if direction >= 0 and h[max_i] - l[i] >= thr:
                pivots.append((h[max_i], "H", i))
                direction, min_i = -1, i
            elif direction <= 0 and h[i] - l[min_i] >= thr:
                pivots.append((l[min_i], "L", i))
                direction, max_i = 1, i

        # Per-bar clarity: walk pivots as they confirm, keep the last two of
        # each kind, and stamp every bar with whether the state they spell is
        # a confirmed trend. -1 = unreadable (not enough swings), 0 = mixed,
        # 1 = clear (HH+HL or LH+LL).
        state = np.full(len(bars), -1, dtype=np.int8)
        highs: list[float] = []
        lows: list[float] = []
        pi = 0
        cur = -1
        for j in range(len(bars)):
            while pi < len(pivots) and pivots[pi][2] <= j:
                px, kind, _ = pivots[pi]
                (highs if kind == "H" else lows).append(px)
                pi += 1
            if len(highs) >= 2 and len(lows) >= 2:
                hh = highs[-1] > highs[-2]
                hl = lows[-1] > lows[-2]
                cur = 1 if hh == hl else 0
            state[j] = cur

        bar_at = _last_closed_bar_at_tick(
            bars["end"].to_numpy(dtype="datetime64[ns]"), ctx.ticks)
        vals = np.full(len(bar_at), -1, dtype=np.int8)
        ok = bar_at >= 0
        vals[ok] = state[bar_at[ok]]
        self._clear_at_tick = vals

    def allows(self, i: int, fill: float) -> bool:
        if self._clear_at_tick is None:
            return False
        return self._clear_at_tick[i] == 1
