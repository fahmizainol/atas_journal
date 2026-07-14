"""Field descriptors for SimConfig: one table, three jobs.

The knobs in rules.SimConfig are a dataclass, which says what a field is called
and what type it holds — and nothing about what values are *legal*. That gap is
where the sharp edges live: ``stop_ticks: 0`` divides by zero deep inside a
background thread, ``entry_variant: "C"`` completes a run green with zero trades,
``target: "rr"`` with no ``target_rr`` silently trades the dev2 target instead.
Every one of those is a config the engine happily accepts and then lies about.

So the descriptors here are the single source of truth for:

  1. **Canonicalization.** Every value is coerced to its declared type before it
     is hashed. This is load-bearing, not cosmetic: store.run_id() sha1s the
     serialized config, so without coercion ``7`` and ``7.0`` are *different
     runs* with identical rules — and a form that emits one while the artifact on
     disk holds the other would re-run the config it was meant to deduplicate.

  2. **Validation.** Range, choice and cross-field rules, enforced at parse time
     so a bad config is a 400 on the POST rather than a crashed run.

  3. **The UI.** The run form renders itself from these — served by
     GET /strategies/{slug} — so a new knob in SimConfig reaches the browser by
     adding it here, and can never drift out of sync with what the engine reads.

The labels are deliberately direction-neutral ("with the trade", "past the mid",
"beyond the value-area edge"). The field *names* are all long-flavoured — on a
short strategy ``acceptance_require_green`` demands a red candle and
``invalidate_below_mid_bars`` counts closes above the mid (see registry) — and a
form that printed "Require green candle" next to a short's checkbox would be
confidently wrong. The neutral phrasing is what the rule actually means; long and
short are just how the engine spells it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time

from ..config import CONTRACT_SPECS
from .rules import SimConfig


@dataclass(frozen=True)
class Field:
    name: str
    # "int" | "float" | "bool" | "enum" | "date" | "time" | "str"
    type: str
    group: str
    label: str
    help: str = ""
    unit: str = ""
    min: float | None = None
    max: float | None = None
    # (value, label) pairs for type == "enum".
    choices: tuple[tuple[str, str], ...] = ()
    # 0 is the engine's sentinel for "feature off" on these. The wire format keeps
    # the sentinel — the form just renders a checkbox over it, so unticking writes
    # 0 and ticking restores on_default rather than making the user encode "off"
    # as a magic number.
    zero_means_off: bool = False
    on_default: float | None = None
    # (field, value): this knob is only read by the engine when that field holds
    # that value. The UI disables it; the value still ships, because the engine
    # ignores it and the identity hash needs the whole config present.
    depends_on: tuple[str, object] | None = None
    # None is legal on the wire (only target_rr, when the target isn't "rr").
    nullable: bool = False
    # Only for gate knobs, which have no dataclass to take a default from. A
    # SimConfig field's default always comes from SimConfig itself.
    default: object = None


# Rendered in this order. `collapsed` groups start closed in the form: they hold
# the knobs you set once (the instrument, the commission) rather than the ones an
# experiment actually turns. The date window is its own uncollapsible group at the
# top — it is the one field that decides whether hitting Run spends Databento
# money, so it does not get to hide.
GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "acceptance", "title": "Acceptance", "collapsed": False},
    {"key": "entry", "title": "Entry", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

FIELDS: tuple[Field, ...] = (
    # --- window ---
    Field("start_date", "date", "window", "Start date",
          help="Inclusive. Weekends are skipped."),
    Field("end_date", "date", "window", "End date",
          help="Inclusive. Weekends are skipped."),

    # --- acceptance (arms the setup) ---
    Field("acceptance_min_ticks", "int", "acceptance", "Min acceptance distance",
          unit="ticks", min=0,
          help="The acceptance candle must close at least this far beyond dev1."),
    Field("acceptance_require_green", "bool", "acceptance",
          "Acceptance candle must close with the trade",
          help="Green on a long, red on a short."),
    Field("acceptance_cap_at_dev2", "bool", "acceptance",
          "Acceptance must stay short of dev2",
          help="Rejects an acceptance that has already run past the target band."),

    # --- entry ---
    Field("entry_variant", "enum", "entry", "Entry variant",
          choices=(("A", "A — rest a limit at dev1"),
                   ("B", "B — stop into the reclaim of dev1")),
          help="A fills on a touch of the band. B waits for a close through it "
               "and enters on the reclaim."),
    Field("entry_limit_offset_ticks", "int", "entry", "Limit offset in front of dev1",
          unit="ticks", min=0, depends_on=("entry_variant", "A"),
          help="Rest the limit this far in front of dev1 — above it on a long, "
               "below it on a short — so the pullback fills before it reaches the "
               "band. 0 rests the limit on dev1 itself. May not exceed the min "
               "acceptance distance, or the limit would already be through the "
               "market when the setup arms."),
    Field("entry_stop_offset_ticks", "int", "entry", "Stop-entry offset",
          unit="ticks", min=0, depends_on=("entry_variant", "B"),
          help="How far past dev1 the entry stop sits, on the reclaim."),

    # --- exit ---
    Field("stop_ticks", "int", "exit", "Initial stop", unit="ticks", min=1,
          help="Fixed distance from the fill — and the denominator of every R."),
    Field("target", "enum", "exit", "Target",
          choices=(("dev2", "dev2 — the far band"),
                   ("rr", "A fixed R multiple"))),
    Field("target_rr", "float", "exit", "Target", unit="R", min=0.1,
          nullable=True, on_default=2.0, depends_on=("target", "rr"),
          help="Measured in multiples of the initial stop."),
    Field("exit_below_vah_bars", "int", "exit",
          "Exit when price is re-accepted back inside value",
          unit="bar closes", min=0, zero_means_off=True, on_default=1,
          help="Exit at market after this many consecutive closes back inside the "
               "developing value area — the trade was taken from outside it."),
    Field("trail_stop_ticks", "int", "exit", "Trailing stop", unit="ticks", min=1,
          zero_means_off=True, on_default=75,
          help="How far behind the best price the trade has seen the stop follows. "
               "Nothing moves until the trade is this far in front, and the first "
               "move is to breakeven — the trail can never tighten a loss."),
    Field("trail_step_ticks", "int", "exit", "Trail step", unit="ticks", min=0,
          help="The grid the trailing stop moves on, measured from its first level: "
               "with a 50-tick trail and a 25-tick step it sits at the first level "
               "from +50, one step above it from +75, and so on. 0 moves it in "
               "single clicks of the full trail distance. Ignored when the trailing "
               "stop is off."),
    Field("trail_breakeven_ticks", "int", "exit", "Scratch level", unit="ticks",
          min=1, zero_means_off=True, on_default=4,
          help="Where the trail's first click lands beyond the entry. A stop on the "
               "entry itself is breakeven gross, so the round trip still books its "
               "commission as a loss — lift it far enough to pay for the trip (a "
               "tick is $5 on NQ, so $14 of commission needs 3, and a 4th covers the "
               "stop's fill-through). Ignored when the trailing stop is off."),
    Field("trail_breakeven_only", "bool", "exit", "Breakeven stop, not a trail",
          help="Take the first click and no other: the stop moves to the scratch "
               "level once the trade is the trail distance in front of it, and then "
               "stays there. The step is then irrelevant. Ignored when the trailing "
               "stop is off."),

    # --- filters / lifecycle ---
    Field("min_band_width_ticks", "int", "filters", "Minimum band width",
          unit="ticks", min=0, zero_means_off=True, on_default=20,
          help="Skip the entry when dev2−dev1 is tighter than this — too little "
               "room between the fill and the target to pay for the stop."),
    Field("invalidate_below_mid_bars", "int", "filters",
          "Invalidate past the VWAP mid", unit="bar closes", min=0,
          zero_means_off=True, on_default=5,
          help="This many consecutive closes back past the VWAP mid disarm the "
               "setup; a fresh acceptance is then required."),
    Field("rearm_after_exit", "bool", "filters", "Re-arm after every exit",
          help="Any exit disarms the setup — one trade per acceptance."),
    Field("daily_loss_stop", "float", "filters", "Daily loss stop",
          unit="$", min=0.01, zero_means_off=True, on_default=1000.0,
          help="Stand down for the rest of the session once realized net P&L "
               "(closed trades, commissions included) is this far in the red. "
               "An open position still runs to its normal exit."),

    # --- scope ---
    Field("instrument", "enum", "scope", "Instrument",
          choices=tuple((k, k) for k in CONTRACT_SPECS),
          help="Sets the tick size and point value."),
    Field("contract", "str", "scope", "Contract",
          help="A root ('NQ') rolls to the front month at each session boundary, so "
               "a window can span an expiry. An exact symbol ('NQZ5') pins one "
               "contract for the whole window and will run dry after it expires."),

    # --- bars ---
    Field("ticks_per_bar", "int", "bars", "Ticks per bar", unit="ticks", min=1,
          help="Bars are tick bars: each one closes after this many trades."),

    # --- session (ET wall clock) ---
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this — the open's impulse is not a pullback."),
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries after this; open positions are left to run."),
    Field("flat_by", "time", "session", "Flat by",
          help="Any open position is force-exited at market."),

    # --- size & cost ---
    Field("contracts", "int", "size", "Contracts", min=1),
    Field("commission_per_side", "float", "size", "Commission per side",
          unit="$ / contract", min=0,
          help="Charged on entry and on exit, per contract."),
)

BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}

# `confluences` is the one field with no descriptor: it is an open-ended dict of
# namespaced gate sections, and each gate publishes its own knobs (see
# confluences.gate_schema). Guard the invariant that everything *else* is covered
# — a knob added to SimConfig without a descriptor would be silently unreachable
# from the form and silently unvalidated on the way in.
_UNDESCRIBED = {"confluences"}
_missing = set(SimConfig().to_json()) - set(BY_NAME) - _UNDESCRIBED
if _missing:  # pragma: no cover - a developer error, caught at import
    raise RuntimeError(f"SimConfig fields with no schema.Field: {sorted(_missing)}")


# --- coercion ---------------------------------------------------------------

def coerce(f: Field, v: object) -> object:
    """One JSON value -> the exact Python type the field declares.

    Strict about bools because Python's aren't: `isinstance(True, int)` is True,
    so a stray `true` would sail into an int field and hash as 1.
    """
    if v is None:
        if f.nullable:
            return None
        raise ValueError(f"{f.name} may not be null")

    if f.type == "bool":
        if not isinstance(v, bool):
            raise ValueError(f"{f.name} must be true or false, got {v!r}")
        return v

    if isinstance(v, bool):  # every other type rejects a bool outright
        raise ValueError(f"{f.name} must be a {f.type}, got {v!r}")

    if f.type in ("int", "float"):
        if not isinstance(v, (int, float, str)):
            raise ValueError(f"{f.name} must be a number, got {v!r}")
        try:
            n = float(v)
        except ValueError:
            raise ValueError(f"{f.name} must be a number, got {v!r}") from None
        if f.type == "int":
            if n != int(n):
                raise ValueError(f"{f.name} must be a whole number, got {v!r}")
            return int(n)
        return float(n)

    if f.type == "date":
        return v if isinstance(v, date) else date.fromisoformat(str(v))

    if f.type == "time":
        return v if isinstance(v, time) else time.fromisoformat(str(v))

    # enum / str
    if not isinstance(v, str):
        raise ValueError(f"{f.name} must be a string, got {v!r}")
    if f.type == "enum":
        allowed = [c for c, _ in f.choices]
        if v not in allowed:
            raise ValueError(f"{f.name} must be one of {allowed}, got {v!r}")
    return v


def _check_range(f: Field, v: object) -> None:
    if v is None or f.type not in ("int", "float"):
        return
    # An off switch is allowed to sit below its minimum: 0 is the sentinel, and
    # min describes the value the knob takes when it is *on*.
    if f.zero_means_off and v == 0:
        return
    if f.min is not None and v < f.min:
        raise ValueError(f"{f.name} must be >= {f.min}, got {v}")
    if f.max is not None and v > f.max:
        raise ValueError(f"{f.name} must be <= {f.max}, got {v}")


def _check_cross_field(cfg: SimConfig) -> None:
    """Rules that read more than one knob. Each of these is a config the engine
    accepts and then quietly does something other than what it says."""
    if cfg.target == "rr" and not cfg.target_rr:
        # Engine: `if cfg.target == "rr" and cfg.target_rr` — a null here trades
        # the dev2 target while the config claims an R target.
        raise ValueError("target 'rr' needs a target_rr")
    if (cfg.entry_variant == "A"
            and cfg.entry_limit_offset_ticks > cfg.acceptance_min_ticks):
        # The acceptance close sits just over acceptance_min_ticks beyond dev1, so a
        # limit offset further than that is already through the market at the moment
        # the setup arms. A real broker fills a marketable limit instantly at the
        # market; the engine rests it and waits for a touch that has already
        # happened — it would quietly trade a different rule than the one on screen.
        raise ValueError(
            f"entry_limit_offset_ticks ({cfg.entry_limit_offset_ticks}) may not "
            f"exceed acceptance_min_ticks ({cfg.acceptance_min_ticks}): the limit "
            f"would sit beyond the acceptance close, on the far side of the market")
    if cfg.start_date > cfg.end_date:
        raise ValueError(
            f"start_date {cfg.start_date} is after end_date {cfg.end_date}")


def parse(raw: dict) -> SimConfig:
    """A user's JSON -> a canonical, validated SimConfig.

    Partial configs are legal: whatever is absent takes the field's default.
    Unknown keys are not — a typo that silently no-ops would masquerade as a real
    experiment (and this is the check that makes `stop_tickss: 50` a 400).
    """
    defaults = SimConfig().to_json()
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise ValueError(f"unknown config keys {unknown}")

    kw: dict = {}
    for name, default in defaults.items():
        v = raw.get(name, default)
        if name == "confluences":
            kw[name] = _parse_confluences(v)
            continue
        f = BY_NAME[name]
        v = coerce(f, v)
        _check_range(f, v)
        kw[name] = v

    if "trail_stop_ticks" not in raw and kw["trail_step_ticks"]:
        # A config written before the trail's distance and its step were separate
        # knobs: back then the single trail_step_ticks was both, so that is what it
        # still means. Every stored run's config.json is read back through here, and
        # a run must always replay to the trades it reported.
        kw["trail_stop_ticks"] = kw["trail_step_ticks"]

    cfg = SimConfig(**kw)
    _check_cross_field(cfg)
    return cfg


def _parse_confluences(v: object) -> dict:
    """Gate sections, canonicalized against each gate's own knob descriptors.

    Same reason as the scalar knobs: a section is part of the identity hash, so
    `{"min_ticks_above_vah": 0.0}` must not be a different run from
    `{"min_ticks_above_vah": 0}`. Whether the *gate* exists and whether this
    strategy supports it is confluences.validate's job — it needs the registry,
    which would be a cycle from here.
    """
    from .confluences import gate_schema

    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f"confluences must be an object, got {type(v).__name__}")

    out: dict = {}
    for key, section in v.items():
        if not isinstance(section, dict):
            raise ValueError(
                f"confluence {key!r} must be an object, got {type(section).__name__}")
        knobs = {f.name: f for f in gate_schema(key)}
        if not knobs:  # unknown gate — confluences.validate reports it properly
            out[key] = dict(section)
            continue
        clean: dict = {}
        for k, val in section.items():
            f = knobs.get(k)
            if f is None:  # unknown knob — the gate itself rejects it, with a list
                clean[k] = val
                continue
            clean[k] = coerce(f, val)
            _check_range(f, clean[k])
        out[key] = clean
    return out


def canonicalize(cfg: SimConfig) -> SimConfig:
    """Collapse a gate that is switched off to its absence.

    ``{"volume_profile": {"enabled": false}}`` and ``{}`` simulate identically —
    build_gates only ever builds the enabled ones — but they are different strings,
    and so, without this, different run_ids. The form materializes a section for
    every gate it renders, so a user who ticks a gate on and back off must land
    back on the run they started from, not on a phantom twin of it.

    Runs it in the *router*, after confluences.validate: a typo'd gate name with
    enabled=false must still be a 400, and a section dropped before validation
    would never be looked at.
    """
    live = {k: v for k, v in (cfg.confluences or {}).items() if v.get("enabled")}
    return cfg if live == cfg.confluences else replace(cfg, confluences=live)


# --- the UI's view of all this ----------------------------------------------

def _field_json(f: Field, defaults: dict) -> dict:
    """One descriptor, wire-shaped. Written out key by key rather than filtering
    __dict__ — `0 == False` in Python, so a truthiness filter would quietly drop
    every `min=0`, which is most of them."""
    d: dict = {
        "name": f.name,
        "type": f.type,
        "group": f.group,
        "label": f.label,
        "default": defaults.get(f.name, f.default),
    }
    if f.help:
        d["help"] = f.help
    if f.unit:
        d["unit"] = f.unit
    if f.min is not None:
        d["min"] = f.min
    if f.max is not None:
        d["max"] = f.max
    if f.nullable:
        d["nullable"] = True
    if f.zero_means_off:
        d["zero_means_off"] = True
        d["on_default"] = f.on_default
    if f.choices:
        d["choices"] = [{"value": c, "label": lab} for c, lab in f.choices]
    if f.depends_on:
        d["depends_on"] = {"field": f.depends_on[0], "value": f.depends_on[1]}
        if f.on_default is not None and not f.zero_means_off:
            # What the form fills in when the dependency flips this knob on.
            d["on_default"] = f.on_default
    return d


def config_schema(supported_confluences: tuple[str, ...] = ()) -> dict:
    """The form's blueprint: groups, fields, and the gates this strategy allows.

    Served alongside default_config so the browser never hard-codes the knob list
    — the descriptors above are the only place it exists.
    """
    from .confluences import gate_schema

    defaults = SimConfig().to_json()
    return {
        "groups": [dict(g) for g in GROUPS],
        "fields": [_field_json(f, defaults) for f in FIELDS],
        "confluences": [
            {
                "name": name,
                "fields": [_field_json(replace(f, group=name), {}) for f in gate_schema(name)],
            }
            for name in supported_confluences
        ],
    }
