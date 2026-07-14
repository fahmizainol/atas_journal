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
from . import regime as regmod
from . import ticks as tickmod
from . import vwap as vwapmod
from .schema import Field

if TYPE_CHECKING:
    from .confluences import SessionCtx


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
        self._side = 1.0 if ctx.side == "long" else -1.0

    def allows(self, i: int, fill: float) -> bool:
        assert self._edge is not None, "prepare() not called"
        edge = self._edge[i]
        if np.isnan(edge):
            return False
        # Beyond the edge, on the trade's side, by at least the margin.
        return self._side * (fill - edge) > self._margin + 1e-9


class VwapSlopeGate:
    """Veto every entry after 10:30 ET on a day whose NY VWAP has no upward
    grade at 10:30.

    The idea it enforces: the long bounce pays on days that establish an upward
    grade by mid-morning. Across Oct 2025 – Jan 2026 the 10:30 NY VWAP slope was
    the strongest day-level correlate of this strategy's net (ρ ≈ 0.42 over 60
    days, ρ ≈ 0.70 on the held-out January slice) — a better read of "bull-grade
    day" than bbr or ABR.

    Honesty clause: that correlation is mostly *recorded* by 10:30, not
    predictive from it. In the same sample, the post-10:30 entries this gate
    would veto at slope_min=0 were net POSITIVE — the slope-negative damage came
    from morning entries the gate cannot lawfully touch. This gate exists so
    that threshold variants can be A/B'd for free off one run's ghost ledger
    (vetoed.parquet), not because a profitable setting is already known.

    Same clock discipline as the regime gate: 10:30 is fixed, entries before it
    pass untouched (the number does not exist yet), and it reads the artifact's
    10:30 checkpoint — never a fresher slope — so what it acts on is exactly
    what was knowable.

    Config section::

        {"vwap_slope": {"enabled": true, "slope_min": 0.0}}

    ``slope_min`` is the stand-down threshold in points per minute (the KPI's
    native unit, over the checkpoint's 30-minute window): at or below it, no
    entries for the rest of the session. 0.0 demands any upward grade at all.

    Blind days — no artifact, or a checkpoint too thin to carry a slope — are
    vetoed after 10:30. "No data" must not read as "confirmed".
    """

    name = "vwap_slope"
    needs_profile = False

    _CHECKPOINT = "10:30"
    _CHECKPOINT_MIN = 10 * 60 + 30

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down without upward VWAP grade",
              default=False,
              help="After 10:30 ET, vetoes every entry on a day whose NY VWAP "
                   "slope at 10:30 is at or below the threshold — the day has "
                   "not established the upward grade the bounce pays on."),
        Field("slope_min", "float", name, "Min NY VWAP slope at 10:30",
              unit="pts/min", min=-5.0, max=5.0, default=0.0,
              depends_on=("enabled", True),
              help="Slope of the NY-anchored VWAP over the 30 minutes into the "
                   "10:30 checkpoint. At or below this, the rest of the session "
                   "is stood down. 0 demands any upward grade at all."),
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
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        slope = ((art or {}).get("checkpoints", {}).get(self._CHECKPOINT)
                 or {}).get("ny_vwap_slope_ppm")
        if slope is not None and slope > self.slope_min:
            self._blocked = None  # the grade is there; the gate is inert today
            return
        et = ctx.ticks["ts_utc"].dt.tz_convert(ET_TZ)
        mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
        self._blocked = mins >= self._CHECKPOINT_MIN

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
        et = ctx.ticks["ts_utc"].dt.tz_convert(ET_TZ)
        mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
        self._blocked = mins >= self._CHECKPOINT_MIN

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
        self._side = 1.0 if ctx.side == "long" else -1.0
        self._line = None
        contract = tickmod.contract_for_cached(ctx.cfg.contract, ctx.day)
        on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
        if on is None or on.empty:
            return  # blind: no overnight, no Globex anchor — veto everything
        w = vwapmod.vwap_bands(pd.concat([on, ctx.ticks], ignore_index=True))
        col = "upper1" if ctx.side == "long" else "lower1"
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


class RegimeGate:
    """Veto every entry after 10:30 ET on a day whose first RTH hour lived
    below the VWAPs.

    The idea it enforces: the band bounce needs price residing on the traded
    side of value to have anything to lean on. A session that has spent most of
    its first hour below *both* anchored VWAPs (``bbr``, the below-both ratio
    from the regime artifact's 10:30 checkpoint) is already telling you it is
    not that day — across the Oct–Dec sample, bbr at 10:30 was the strongest
    early predictor of the strategy bleeding for the rest of the session, and
    every day it flagged was a post-10:30 loser.

    10:30 is fixed, not a knob. It is the earliest checkpoint at which the NY
    anchor has enough bars to mean anything (09:45 predicted nothing), and a
    configurable read time would invite fitting the clock to the sample.
    Entries before 10:30 pass untouched — the number does not exist yet, and a
    gate acting on it earlier would be trading on hindsight.

    Config section::

        {"regime": {"enabled": true, "bbr_max": 0.6}}

    ``bbr_max`` is the stand-down threshold: at or above it, no entries for the
    rest of the session. The 0.6 default mirrors regime.classify()'s trend
    convention rather than anything tuned on strategy P&L.

    When the checkpoint cannot be read at all — no regime artifact, or a bbr of
    None because the session has no Globex anchor — the gate vetoes after
    10:30. Same doctrine as the profile gate: "no data" must not read as
    "confirmed", and a day the dual-VWAP regime cannot describe is a day this
    filter has no business waving through.
    """

    name = "regime"
    needs_profile = False

    _CHECKPOINT = "10:30"
    _CHECKPOINT_MIN = 10 * 60 + 30

    SCHEMA: tuple[Field, ...] = (
        Field("enabled", "bool", name, "Stand down on below-VWAP mornings",
              default=False,
              help="After 10:30 ET, vetoes every entry on a day whose first RTH "
                   "hour spent too long below both anchored VWAPs — the regime "
                   "in which the bounce has nothing to lean on."),
        Field("bbr_max", "float", name, "Max below-both-VWAPs ratio at 10:30",
              min=0.0, max=1.0, default=0.6, depends_on=("enabled", True),
              help="Share of the first hour spent below both VWAPs at or above "
                   "which the rest of the session is stood down. The default "
                   "mirrors classify()'s trend threshold; it was not fitted to "
                   "strategy P&L."),
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
        self._blocked: np.ndarray | None = None

    def prepare(self, ctx: "SessionCtx") -> None:
        art = regmod.get_regime(ctx.cfg.contract, ctx.day)
        bbr = ((art or {}).get("checkpoints", {}).get(self._CHECKPOINT) or {}).get("bbr")
        if bbr is not None and bbr < self.bbr_max:
            self._blocked = None  # the morning qualified; the gate is inert today
            return
        # Stood down (or blind — see class docstring): veto every tick at or
        # after the checkpoint, by ET wall clock. Overnight ticks in a globex
        # frame also read >= 10:30 on a wall clock, but entries only ever fire
        # inside the entry window, so the gate is never consulted there.
        et = ctx.ticks["ts_utc"].dt.tz_convert(ET_TZ)
        mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
        self._blocked = mins >= self._CHECKPOINT_MIN

    def allows(self, i: int, fill: float) -> bool:
        return self._blocked is None or not self._blocked[i]
