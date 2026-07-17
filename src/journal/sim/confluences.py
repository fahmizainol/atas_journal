"""Veto-only confluence gates.

A gate answers one question at the moment the base rules produce a fill:
"is this entry allowed?". It can never move an entry, change an exit, or size a
position — that discipline is what keeps a run with a gate comparable to the
same run without it. Anything that would change *how* the strategy trades is a
new strategy (or a coded variant inside one), not a gate.

Entries a gate rejects are not discarded: the engine tracks them as ghost
positions to their would-be exit, so a single run can report "this confluence
filtered N trades worth $X" instead of forcing an A/B run comparison.

A gate lives in config as a namespaced section keyed by its registry name:

    {"confluences": {"volume_profile": {"enabled": true, ...gate params}}}

Sections with ``enabled: false`` are inert but still part of the run's identity
hash — flipping the flag is a different run, as it should be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Protocol

import pandas as pd

from .profile import DevelopingProfile
from .rules import SimConfig


@dataclass(frozen=True)
class SessionCtx:
    """Everything the engine computed for one session, handed to a gate once,
    before the tick loop. Gates read; they never build their own view of the
    session — a gate that recomputed the profile could gate on levels the engine
    never traded against."""

    cfg: SimConfig
    day: date
    ticks: pd.DataFrame
    bars: pd.DataFrame
    # Per-tick value-area edge in force on the side being traded (see
    # profile.levels_in_force): VAH for a long, VAL for a short. Present iff some
    # gate or rule asked for it; a gate declaring needs_profile may assume it is
    # here. Gates read this rather than picking a level themselves — the edge the
    # engine judged the trade against is the only one they may judge the entry on.
    value_edge_at_tick: pd.Series | None
    profile: DevelopingProfile | None
    # "long" | "short" — the direction the strategy trades the setup in.
    side: str = "long"
    # "upper" | "lower" — which side of the market the setup lives on, and so
    # which value edge and which band a gate must compare against. Distinct from
    # ``side`` because the two only coincide on the bounce (a long bounce reads
    # the upper bands): the fade trades AGAINST its band, so a short fade lives
    # on the upper side. None derives it from ``side`` the bounce's way, which
    # keeps every pre-fade construction meaning what it always did.
    band: str | None = None

    def band_side(self) -> str:
        return self.band or ("upper" if self.side == "long" else "lower")


class Gate(Protocol):
    name: str
    # Computing the developing profile costs real time per session, so the engine
    # only builds it when something reads it. Set True and ctx.profile is filled.
    needs_profile: bool

    def prepare(self, ctx: SessionCtx) -> None:
        """Called once per session before the tick loop. Build whatever
        per-session state you need."""

    def allows(self, i: int, fill: float) -> bool:
        """May the entry at tick index ``i``, filling at ``fill``, proceed?

        Pure veto — no side effects. ``fill`` is the price the position would
        actually open at, which is not the traded price at ``i``: a resting limit
        gets its own level. A gate comparing against the tape instead of the fill
        would answer a question nobody asked.
        """


# name -> factory(config_section) -> Gate. Concrete gates (volume profile, big
# trades, ...) register here as they are built; each strategy declares which of
# these names it supports.
GATE_FACTORIES: dict[str, Callable[[dict], Gate]] = {}


def validate(cfg: SimConfig, supported: tuple[str, ...]) -> None:
    """Reject configs naming gates that don't exist or that this strategy
    doesn't support — a typo'd section silently doing nothing would masquerade
    as a real experiment."""
    for key, section in (cfg.confluences or {}).items():
        if key not in GATE_FACTORIES:
            raise ValueError(f"unknown confluence {key!r} (available: {sorted(GATE_FACTORIES)})")
        if key not in supported:
            raise ValueError(f"confluence {key!r} is not supported by this strategy")
        if not isinstance(section, dict):
            raise ValueError(f"confluence {key!r} must be an object, got {type(section).__name__}")
        # Build it: a gate rejects its own bad knobs, and a typo caught here is a
        # 400 on the POST rather than a crashed run in a background thread.
        GATE_FACTORIES[key](section)


def gate_schema(name: str) -> tuple:
    """A gate's knob descriptors (schema.Field), or () if the gate is unknown.

    The run form renders a gate's section from these, and the config parser
    canonicalizes its values against them — so a gate's knobs are declared once,
    on the gate, and the form has nothing to learn about it.
    """
    factory = GATE_FACTORIES.get(name)
    return tuple(getattr(factory, "SCHEMA", ()))


def build_gates(cfg: SimConfig) -> list[Gate]:
    gates = []
    for key, section in (cfg.confluences or {}).items():
        if section.get("enabled", False):
            gates.append(GATE_FACTORIES[key](section))
    return gates


def needs_profile(cfg) -> bool:
    """Would anything read the developing profile this run? Building it costs a
    value-area scan per bar, so the engine asks before paying. getattr because
    only the bounce's config has the value-area exit; on any other class the
    gates alone decide."""
    if getattr(cfg, "exit_below_vah_bars", 0):
        return True
    return any(getattr(g, "needs_profile", False) for g in build_gates(cfg))


# Registered at the bottom so the concrete gates can import the Gate contract
# above without a cycle. Every entry point that resolves a config goes through
# this module, so importing it is what makes the gates exist.
from .gates import (  # noqa: E402
    GxFloorGate, GxOverhangGate, GxPocShapeGate, GxRescueCapGate, GxRescueGate,
    GxValueGate, NyPocFloorGate, OnHighGate, RegimeGate, UpperOccupancyCapGate,
    UpperOccupancyGate, VolumeProfileGate, VwapCrossGate, VwapSlopeCapGate,
    VwapSlopeGate,
)

GATE_FACTORIES[VolumeProfileGate.name] = VolumeProfileGate
GATE_FACTORIES[RegimeGate.name] = RegimeGate
GATE_FACTORIES[VwapSlopeGate.name] = VwapSlopeGate
GATE_FACTORIES[VwapSlopeCapGate.name] = VwapSlopeCapGate
GATE_FACTORIES[VwapCrossGate.name] = VwapCrossGate
GATE_FACTORIES[UpperOccupancyGate.name] = UpperOccupancyGate
GATE_FACTORIES[UpperOccupancyCapGate.name] = UpperOccupancyCapGate
GATE_FACTORIES[GxRescueGate.name] = GxRescueGate
GATE_FACTORIES[GxRescueCapGate.name] = GxRescueCapGate
GATE_FACTORIES[GxFloorGate.name] = GxFloorGate
GATE_FACTORIES[OnHighGate.name] = OnHighGate
GATE_FACTORIES[GxValueGate.name] = GxValueGate
GATE_FACTORIES[GxPocShapeGate.name] = GxPocShapeGate
GATE_FACTORIES[NyPocFloorGate.name] = NyPocFloorGate
GATE_FACTORIES[GxOverhangGate.name] = GxOverhangGate
