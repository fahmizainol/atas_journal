"""Field descriptors for the config classes: one table per class, three jobs.

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
from .rules import FadeConfig, GlobexBounceConfig, ProfilePullbackConfig, SimConfig


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
    Field("panic_exit_delta", "int", "exit", "Panic exit on a flow shock",
          unit="contracts", min=1, zero_means_off=True, on_default=300,
          help="One read of the tape when the fill is a panic-window old: exit at "
               "market if the net aggressor delta over that window ran this many "
               "contracts against the trade. A shock detector, not a loser "
               "detector — set it far beyond a normal minute of tape, or it reads "
               "the winners that start ugly and recover as shocks."),
    Field("panic_exit_window_s", "int", "exit", "Panic window", unit="s", min=1,
          help="The window the read covers, in seconds after the fill. The "
               "shock's edge decays within a couple of minutes — by then the "
               "price has paid most of the stop — so it stays tight. A position "
               "that exits before the window closes is never read. Ignored when "
               "the panic exit is off."),

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
    Field("pyramid_tranches", "int", "size", "Scale-in lots", unit="lots", min=1,
          help="1 fills the whole size at once. N>1 splits it into N equal lots "
               "(Contracts must divide by N): the first fills at dev1, each later "
               "lot stops in one step further in the trade's favour, so size is "
               "added only as the move confirms. A lot whose trigger is never "
               "reached never fills — losers stay small, winners reach full size."),
    Field("pyramid_step_ticks", "int", "size", "Scale-in step", unit="ticks", min=1,
          help="The favourable distance between one scale-in lot's stop and the "
               "next, measured from the first fill. Read only with 2+ lots."),
    Field("pyramid_stop_mode", "enum", "size", "Scale-in stop",
          choices=(("blend", "Blend — re-strike the stop off the average entry"),
                   ("anchor", "Anchor — keep the first lot's stop")),
          help="Blend keeps each lot risking the same distance off the running "
               "average (total risk grows with size). Anchor leaves the stop and "
               "target where the first lot set them — later lots ride the initial "
               "risk. Read only with 2+ lots."),
)

BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}


# --- the Globex bounce's table ------------------------------------------------
# GlobexBounceConfig is SimConfig plus one knob, `side`, so its form is the
# bounce's with a Direction group bolted on the front. Every other descriptor is
# reused outright.

GLOBEX_GROUPS: tuple[dict, ...] = (
    GROUPS[0],  # window stays first — it is the one that spends Databento money
    {"key": "direction", "title": "Direction", "collapsed": False},
    *GROUPS[1:],
)

GLOBEX_FIELDS: tuple[Field, ...] = (
    Field("side", "enum", "direction", "Direction",
          choices=(("long", "Long — buy the pullback"),
                   ("short", "Short — sell the pullback")),
          help="The trade direction. Which band it reads depends on Invert: by "
               "default long bounces the upper band and short the lower. The "
               "long-flavoured knob names (green acceptance candle, invalidation "
               "past the mid, re-acceptance inside value) all mean their mirror on "
               "a short, and the volume_profile confluence flips with it too."),
    Field("invert", "bool", "direction", "Invert the band",
          help="Off: the bounce — long reads the upper band, short the lower, "
               "each running with the break out to dev2. On: long reads the LOWER "
               "band (buy the pullback into support) and short the UPPER (sell the "
               "rally into resistance) — same entry at dev1, opposite direction, "
               "reverting toward the mid. dev2 then sits behind the trade, so an "
               "inverted run must target an R-multiple and cannot cap acceptance "
               "at dev2."),
    *FIELDS,
)

GLOBEX_BY_NAME: dict[str, Field] = {f.name: f for f in GLOBEX_FIELDS}


# --- the fade's table ---------------------------------------------------------
# FadeConfig's descriptors. A knob whose name, meaning and bounds are the same
# on both classes reuses the bounce's Field outright (the default the form shows
# comes from the config class, not the descriptor, so a shared Field can still
# carry a different default per class). A knob whose rule differs gets its own.

FADE_GROUPS: tuple[dict, ...] = tuple(
    {"key": "arming", "title": "Arming", "collapsed": False}
    if g["key"] == "acceptance" else g
    for g in GROUPS
)

FADE_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- arming (what makes the fade live) ---
    Field("arm_stretch_side", "enum", "arming", "The arming stretch runs",
          choices=(("beyond", "Beyond dev1 — the overextension, faded"),
                   ("inside", "Inside dev1 — the broken band, retested")),
          help="Which side of dev1 the stretch that arms the setup runs to. "
               "Beyond: price overextends out of the channel and the fade sells "
               "the return down to the band. Inside: price rips back through the "
               "band into the channel and the fade sells the retest back up to "
               "it — the same short at dev1, armed by the break instead of the "
               "overextension. Only the stretch flips: the stop, the targets, "
               "the dev2 cap and the dev1 re-acceptance exit are unmoved."),
    Field("arm_extension_ticks", "int", "arming", "Arming stretch",
          unit="ticks", min=1,
          help="The setup arms when price prints more than this far past dev1, "
               "on the side the arming stretch runs to. Re-arming needs a fresh "
               "stretch: price must first come back within this distance of the "
               "band."),
    Field("arm_require_mid_cross", "bool", "arming",
          "Approach must start from the VWAP mid",
          help="Only arm a stretch whose move began at the mid: price must have "
               "printed at or past the mid since the last fill."),
    Field("arm_cap_at_dev2", "bool", "arming", "Stand down past dev2",
          help="A bar close beyond dev2 disarms the armed setup — that stretch "
               "is a runaway, not an overextension — and a fresh stretch is then "
               "required. An open position is never touched."),

    # --- entry ---
    Field("entry_variant", "enum", "entry", "Entry variant",
          choices=(("A", "A — rest a limit at dev1"),
                   ("B", "B — stop into the continuation of the rejection")),
          help="A fills on the return to the band, no confirmation. B waits for "
               "a bar to close back inside dev1 and enters as price continues "
               "away from the band."),
    Field("entry_limit_offset_ticks", "int", "entry", "Limit offset in front of dev1",
          unit="ticks", min=0, depends_on=("entry_variant", "A"),
          help="Rest the limit this far in front of dev1 — toward the stretch, "
               "whichever side it ran to — so the return fills before it reaches "
               "the band. 0 rests the limit on dev1 itself. May not exceed the "
               "arming stretch, or the limit would already be through the "
               "market when the setup arms."),
    Field("entry_stop_offset_ticks", "int", "entry", "Stop-entry offset",
          unit="ticks", min=0, depends_on=("entry_variant", "B"),
          help="How far past dev1, into the channel, the entry stop sits after "
               "the confirming close."),

    # --- exit ---
    BY_NAME["stop_ticks"],
    Field("target", "enum", "exit", "Target",
          choices=(("mid", "The VWAP mid"),
                   ("opp_dev1", "The opposite dev1"),
                   ("rr", "A fixed R multiple")),
          help="mid and the opposite dev1 are tracked live, like the bounce's "
               "dev2 — the level in force at the exit is the one that fills."),
    BY_NAME["target_rr"],
    Field("invalidate_beyond_dev1_bars", "int", "exit",
          "Exit when price is re-accepted beyond dev1",
          unit="bar closes", min=0, zero_means_off=True, on_default=5,
          help="Exit at market after this many consecutive closes back beyond "
               "dev1 — that is the bounce's acceptance, and so the fade's "
               "structural invalidation. The fixed stop stays behind it as the "
               "hard backstop."),
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],

    # --- filters / lifecycle ---
    BY_NAME["min_band_width_ticks"],
    Field("rearm_after_exit", "bool", "filters", "Re-arm after every exit",
          help="Any exit disarms the setup — a fresh stretch (and a fresh mid "
               "touch, when required) must build before the next trade."),
    BY_NAME["daily_loss_stop"],

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    BY_NAME["entry_open"], BY_NAME["entry_close"], BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

FADE_BY_NAME: dict[str, Field] = {f.name: f for f in FADE_FIELDS}


# --- the profile pullback's table ----------------------------------------------
# ProfilePullbackConfig's descriptors. No acceptance and no arming — the level
# is the setup — so the setup group describes what makes a touch a trade.

PULLBACK_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "levels", "title": "Levels", "collapsed": False},
    {"key": "setup", "title": "Setup", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

PULLBACK_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- levels (what a limit may rest on) ---
    Field("use_ny_levels", "bool", "levels", "NY session profile levels",
          help="The session's own developing profile, anchored at the bell. "
               "Degenerate for the first minutes — see the warm-up."),
    Field("use_globex_levels", "bool", "levels", "Globex profile levels",
          help="The overnight developing profile, anchored at 18:00 ET the "
               "previous evening — ~15 hours mature by the open."),
    Field("trade_poc", "bool", "levels", "POC"),
    Field("trade_vah", "bool", "levels", "VAH"),
    Field("level_warmup_min", "int", "levels", "NY level warm-up",
          unit="min", min=0,
          help="An NY-anchored profile minutes old has POC=VAH=VAL on the open "
               "print; its levels are not candidates until the anchor is this "
               "old. Globex levels are never gated by this."),

    # --- setup (what makes a touch a trade) ---
    Field("require_upper_band", "bool", "setup",
          "Level must sit inside NY VWAP +1σ..+2σ",
          help="The cut's core condition. The same levels outside the channel "
               "scored at the null baseline — switch this off only to measure "
               "that."),
    Field("rearm_ticks", "int", "setup", "Re-arm distance", unit="ticks", min=0,
          help="Price must clear the level by this much before a new touch of "
               "it can fill — a rotation sitting on the level is one touch, "
               "not many."),
    Field("min_arm_min", "int", "setup", "Minimum pullback age",
          unit="min", min=1, zero_means_off=True, on_default=3,
          help="A touch may only fill (or count) if price cleared the level at "
               "least this long ago; a level that relocates under price "
               "restarts the clock. Measured in-sample: even 1 minute inverted "
               "the edge (the profitable fill is the fast pullback that "
               "instantly rejects) — this exists to keep that measurable, not "
               "as a recommendation."),
    Field("max_touches_per_level", "int", "setup", "Touches per level",
          unit="touches", min=1, zero_means_off=True, on_default=1,
          help="Only the first N touches of each level series may fill; the "
               "first touch was the study's strongest sub-cut. Off = every "
               "touch may fill."),
    Field("require_confluence_pts", "float", "setup", "Require a stacked level",
          unit="pts", min=0.01, zero_means_off=True, on_default=10.0,
          help="Another candidate level must sit within this many points of "
               "the fill — the study's 2+ sources cut, whose median adverse "
               "excursion was the tightest of any conditioner."),

    # --- exit ---
    BY_NAME["stop_ticks"],
    Field("target", "enum", "exit", "Target",
          choices=(("rr", "A fixed R multiple"),
                   ("ticks", "A fixed distance"))),
    BY_NAME["target_rr"],
    Field("target_ticks", "int", "exit", "Target distance", unit="ticks", min=1,
          depends_on=("target", "ticks"),
          help="A fixed take-profit distance from the fill."),
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],

    # --- filters / lifecycle ---
    Field("min_band_width_ticks", "int", "filters", "Minimum upper band width",
          unit="ticks", min=0, zero_means_off=True, on_default=20,
          help="Skip the fill when the NY VWAP +1σ..+2σ channel the level sits "
               "in (dev2−dev1) is tighter than this. A pinched upper band makes "
               "the inside-the-channel condition trivial and leaves the pullback "
               "no room to run."),
    Field("min_level_stability_min", "int", "filters", "Minimum level stability",
          unit="min", min=1, zero_means_off=True, on_default=2,
          help="Skip the fill unless the level has sat within the re-arm "
               "distance of its fill value for this long. A VAH that relocated "
               "up under price moments before the touch is the profile chasing "
               "the market, not a level anyone defended — measured in-sample, "
               "fills on just-relocated levels lost while stable-level fills "
               "carried the edge."),
    BY_NAME["daily_loss_stop"],

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this. The default matches the NY level "
               "warm-up — an earlier touch is the open's impulse, not a level."),
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries after this; open positions are left to run. The "
               "default excludes the last hour, whose touches scored exactly "
               "at the null baseline."),
    BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

PULLBACK_BY_NAME: dict[str, Field] = {f.name: f for f in PULLBACK_FIELDS}


# config class -> (groups, fields, by-name index). The registry's config_cls is
# the key, so parsing, validation and the served form blueprint all follow from
# one declaration on the strategy.
_TABLES: dict[type, tuple[tuple[dict, ...], tuple[Field, ...], dict[str, Field]]] = {
    SimConfig: (GROUPS, FIELDS, BY_NAME),
    GlobexBounceConfig: (GLOBEX_GROUPS, GLOBEX_FIELDS, GLOBEX_BY_NAME),
    FadeConfig: (FADE_GROUPS, FADE_FIELDS, FADE_BY_NAME),
    ProfilePullbackConfig: (PULLBACK_GROUPS, PULLBACK_FIELDS, PULLBACK_BY_NAME),
}

# `confluences` is the one field with no descriptor: it is an open-ended dict of
# namespaced gate sections, and each gate publishes its own knobs (see
# confluences.gate_schema). Guard the invariant that everything *else* is covered
# both ways — a knob without a descriptor would be silently unreachable from the
# form and unvalidated on the way in; a descriptor without a knob would render a
# widget whose value the parser then rejects as an unknown key.
_UNDESCRIBED = {"confluences"}
for _cls, (_, _, _by) in _TABLES.items():
    _missing = set(_cls().to_json()) - set(_by) - _UNDESCRIBED
    _extra = set(_by) - set(_cls().to_json())
    if _missing or _extra:  # pragma: no cover - a developer error, caught at import
        raise RuntimeError(
            f"{_cls.__name__} fields with no schema.Field: {sorted(_missing)}; "
            f"schema.Fields with no {_cls.__name__} field: {sorted(_extra)}")


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


def _check_cross_field(cfg) -> None:
    """Rules that read more than one knob. Each of these is a config the engine
    accepts and then quietly does something other than what it says."""
    if cfg.target == "rr" and not cfg.target_rr:
        # Engine: `if cfg.target == "rr" and cfg.target_rr` — a null here trades
        # the live target (dev2, or the fade's mid) while claiming an R target.
        raise ValueError("target 'rr' needs a target_rr")
    if cfg.start_date > cfg.end_date:
        raise ValueError(
            f"start_date {cfg.start_date} is after end_date {cfg.end_date}")
    if isinstance(cfg, SimConfig):
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
        if cfg.pyramid_tranches > 1 and cfg.contracts % cfg.pyramid_tranches:
            # Each scale-in lot is an equal whole slice of the size. A remainder
            # would either drop contracts on the floor or fill an uneven last lot —
            # the engine's `contracts // pyramid_tranches` silently does the former,
            # trading a smaller position than the config says.
            raise ValueError(
                f"contracts ({cfg.contracts}) must divide evenly by pyramid_tranches "
                f"({cfg.pyramid_tranches}): each scale-in lot is an equal whole slice")
    if isinstance(cfg, GlobexBounceConfig) and cfg.invert:
        # Inverting reverts toward the mid, so dev2 sits BEHIND the entry — a
        # dev2 target fills instantly at a loss, and an acceptance capped at dev2
        # can never arm. Both would run green and silently trade nothing like
        # what they say, so they are refused rather than quietly mis-simulated.
        if cfg.target != "rr":
            raise ValueError(
                "invert needs target 'rr': dev2 sits behind an inverted trade "
                "(it reverts toward the mid), so it cannot be the target")
        if cfg.acceptance_cap_at_dev2:
            raise ValueError(
                "invert cannot use acceptance_cap_at_dev2: the acceptance close is "
                "beyond dev1 toward the channel, never past the far dev2")
    if isinstance(cfg, ProfilePullbackConfig):
        if not (cfg.use_ny_levels or cfg.use_globex_levels):
            raise ValueError("enable at least one level anchor (NY or Globex)")
        if not (cfg.trade_poc or cfg.trade_vah):
            raise ValueError("enable at least one level type (POC or VAH)")
        if cfg.require_confluence_pts and sum(
                [cfg.use_ny_levels, cfg.use_globex_levels]) * sum(
                [cfg.trade_poc, cfg.trade_vah]) < 2:
            # One candidate series can never have "another level" beside it: the
            # engine would run green and take zero trades, forever, which reads
            # as "the idea never sets up" rather than "this config is inert".
            raise ValueError(
                "require_confluence_pts needs at least two candidate level series")
    if isinstance(cfg, FadeConfig):
        if (cfg.entry_variant == "A"
                and cfg.entry_limit_offset_ticks > cfg.arm_extension_ticks):
            # Same rule, read against the fade's arming: the stretch print is only
            # arm_extension_ticks past dev1 (strictly more, in fact), so a limit
            # offset beyond that is already through the market when the setup arms.
            # Side-agnostic — the limit and the stretch are on the same side of
            # dev1 by construction, whichever side arm_stretch_side names.
            raise ValueError(
                f"entry_limit_offset_ticks ({cfg.entry_limit_offset_ticks}) may not "
                f"exceed arm_extension_ticks ({cfg.arm_extension_ticks}): the limit "
                f"would sit beyond the arming stretch, on the far side of the market")


def parse(raw: dict, config_cls: type = SimConfig):
    """A user's JSON -> a canonical, validated config of the strategy's class.

    Partial configs are legal: whatever is absent takes the field's default.
    Unknown keys are not — a typo that silently no-ops would masquerade as a real
    experiment (and this is the check that makes `stop_tickss: 50` a 400). A
    bounce knob posted at a fade strategy is exactly such a key.
    """
    _, _, by_name = _TABLES[config_cls]
    defaults = config_cls().to_json()
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise ValueError(f"unknown config keys {unknown}")

    kw: dict = {}
    for name, default in defaults.items():
        v = raw.get(name, default)
        if name == "confluences":
            kw[name] = _parse_confluences(v)
            continue
        f = by_name[name]
        v = coerce(f, v)
        _check_range(f, v)
        kw[name] = v

    if config_cls is SimConfig and "trail_stop_ticks" not in raw and kw["trail_step_ticks"]:
        # A config written before the trail's distance and its step were separate
        # knobs: back then the single trail_step_ticks was both, so that is what it
        # still means. Every stored run's config.json is read back through here, and
        # a run must always replay to the trades it reported. (SimConfig only: no
        # fade run predates the split.)
        kw["trail_stop_ticks"] = kw["trail_step_ticks"]

    cfg = config_cls(**kw)
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


def canonicalize(cfg):
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


def config_schema(supported_confluences: tuple[str, ...] = (),
                  config_cls: type = SimConfig) -> dict:
    """The form's blueprint: groups, fields, and the gates this strategy allows.

    Served alongside default_config so the browser never hard-codes the knob list
    — the descriptors above are the only place it exists.
    """
    from .confluences import gate_schema

    groups, fields, _ = _TABLES[config_cls]
    defaults = config_cls().to_json()
    return {
        "groups": [dict(g) for g in groups],
        "fields": [_field_json(f, defaults) for f in fields],
        "confluences": [
            {
                "name": name,
                "fields": [_field_json(replace(f, group=name), {}) for f in gate_schema(name)],
            }
            for name in supported_confluences
        ],
    }
