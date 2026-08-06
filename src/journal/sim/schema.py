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
from .rules import (
    DriftFadeConfig, DriftFadeGlobexConfig, EmaPullbackConfig, FadeConfig,
    GlobexBounceConfig, OrbConfig,
    ProfilePullbackConfig, SimConfig, ValueRotationConfig, WeeklyTraverseConfig,
)


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
    Field("min_acceptance_sigma", "float", "acceptance", "Min acceptance depth (σ)",
          unit="σ", min=0, max=2, zero_means_off=True, on_default=0.15,
          help="A band-width floor on top of the tick gate: the acceptance close "
               "must also clear this fraction of the band's σ (dev2−dev1) beyond "
               "dev1. Vetoes shallow acceptance on wide-band days. 0 = off."),
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
    Field("underwater_stop_ticks", "int", "exit", "Tighten the stop if still underwater",
          unit="ticks", min=1, zero_means_off=True, on_default=80,
          help="One read when the fill is a set age: if the trade is still below "
               "breakeven then, pull the stop in to this many ticks behind the entry. "
               "It caps the loss on a fill that hasn't worked without flattening it, "
               "so a deep-heat winner still has room, and only ever tightens. Set it "
               "below the stop distance or it can never bite. Its own exit reason, "
               "'uw_stop'."),
    Field("underwater_stop_after_s", "int", "exit", "Underwater read at", unit="s", min=1,
          help="Seconds after the fill the one read is taken. The dwell study put the "
               "bleed at 60-180s, so it sits at the front of that band — late enough "
               "that the first-minute winners have resolved, early enough to still save "
               "the loss. Ignored when the tighten is off."),
    Field("underwater_exit_after_s", "int", "exit", "Cut if underwater this long",
          unit="s", min=1, zero_means_off=True, on_default=60,
          help="Flatten at market once the trade has been CONTINUOUSLY below breakeven "
               "for this long — the run resets the moment price is back at or above "
               "the entry, so it only ever fires on a position that is still red. "
               "Where the underwater tighten pulls the stop in on a one-shot read, "
               "this closes the trade outright. Unlike every other exit read here it "
               "must evaluate per tick (a duration is not knowable from one read), "
               "which is exactly what cost the panic exit $23k when it was tried that "
               "way — it also cuts the winners that start ugly and recover. Its own "
               "exit reason, 'uw_exit'."),
    Field("stop_below_mid_bars", "int", "exit", "Exit past the NY session mid",
          unit="bar closes", min=0, zero_means_off=True, on_default=3,
          help="Exit at market after this many consecutive closes past the NY session "
               "VWAP mid — the 09:30-anchored mean, not the Globex mid the bands ride. "
               "A trade entered beyond the band that closes back through the day's mean "
               "has lost its premise. Pair it with an offset — a bare touch clips the "
               "winners that dip through the mean and recover."),
    Field("stop_below_mid_ticks", "int", "exit", "Mid-exit offset", unit="ticks",
          min=0,
          help="How far past the mid a close must sit to count toward the streak. 0 "
               "counts any close on the far side; a few ticks past ignores the one-tick "
               "poke through the mean. Ignored when the mid exit is off."),

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
    Field("reenter_after_stop_only", "bool", "filters",
          "Re-enter only after a stop",
          help="Any exit other than a full stop-out stands the session down. "
               "Skipped entries are tracked as reentry_halt rows in missed "
               "trades, and one of them stopping out re-arms the day — the "
               "setup failed without you, so the next acceptance trades. A "
               "trail exit counts as an exit, not a stop, even when red."),
    Field("reentry_rearm_window_min", "int", "filters", "Re-arm window",
          unit="min", min=0, zero_means_off=True, on_default=30,
          depends_on=("reenter_after_stop_only", True),
          help="How long a stop (real or watched) keeps the day open. Off, the "
               "re-arm lasts until the next non-stop exit; set, the day stands "
               "back down if no entry fills within this many minutes of the "
               "stop print. A later watched stop starts a fresh window."),
    Field("daily_loss_stop", "float", "filters", "Daily loss stop",
          unit="$", min=0.01, zero_means_off=True, on_default=1000.0,
          help="Stand down for the rest of the session once realized net P&L "
               "(closed trades, commissions included) is this far in the red. "
               "An open position still runs to its normal exit, unless the "
               "companion exit below is on."),
    Field("daily_loss_exit_open", "bool", "filters",
          "Also flatten the open trade at the loss stop",
          help="Extends the loss stop to the position already on: once realized "
               "net P&L plus the open trade marked to the current price reaches "
               "the stop, exit it at market (reason 'daily_loss'). Off, the stop "
               "only refuses new entries — a single trade whose own stop sits "
               "wider than the whole limit still blows through it. Needs a daily "
               "loss stop set."),

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
    Field("size_up_participation", "float", "size",
          "Size up on big-lot participation", unit="fraction", min=0, max=1,
          zero_means_off=True, on_default=0.041,
          help="One read of the tape at each fill: the share of the trailing "
               "window's total volume printed in big lots (side-agnostic). When it "
               "is at least this fraction the entry is sized to the sized-up "
               "contracts instead of the base — the 30badf94 study's one entry-time "
               "separator of 3R runners, a magnitude signal so it sizes, never "
               "gates. 0 sizes every fill at the base contracts."),
    Field("size_up_contracts", "int", "size", "Sized-up contracts",
          unit="contracts", min=1, zero_means_off=True, on_default=5,
          help="The size a qualifying fill takes. Ignored when the size-up is off."),
    Field("biglot_min_size", "int", "size", "Big-lot threshold", unit="lots", min=1,
          help="A print of this many lots or more counts as a big lot — the study's "
               "institutional-size NQ print. Ignored when the size-up is off."),
    Field("biglot_window_s", "int", "size", "Participation window", unit="s", min=1,
          help="The trailing window the participation share is read over, ending at "
               "the fill. The study's split-half-robust read was 60s. Ignored when "
               "the size-up is off."),
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
    Field("pyramid_direction", "enum", "size", "Scale-in direction",
          choices=(("with", "With — add as the move confirms"),
                   ("against", "Against — average down into the pullback")),
          help="With rests each later lot one step further in the trade's favour "
               "(the classic scale-in). Against rests it one step further against "
               "— a limit below the fill on a long — so size is added into the "
               "dip and the average entry improves as the trade goes underwater. "
               "Read only with 2+ lots."),
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
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

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
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

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


# --- the value rotation's table -------------------------------------------------
# ValueRotationConfig's descriptors. No VWAP band anywhere in the setup: the
# value-area edge arms it, bar closes back inside confirm it, and the POC is
# the target — so the arming group describes the excursion outside value.

ROTATION_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "direction", "title": "Direction", "collapsed": False},
    {"key": "arming", "title": "Arming", "collapsed": False},
    {"key": "entry", "title": "Entry", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

ROTATION_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- direction ---
    Field("side", "enum", "direction", "Direction",
          choices=(("short", "Short — accepted above the VAH, sold back to the POC"),
                   ("long", "Long — accepted below the VAL, bought back to the POC")),
          help="Which value-area edge the rotation is read off. The knob names "
               "are written for the short and mean the mirror on the long: "
               "closes back inside the edge are closes back ABOVE the VAL, and "
               "the POC sits above the fill instead of below it."),

    # --- arming (accepted outside value) ---
    Field("arm_beyond_ticks", "int", "arming", "Arming excursion",
          unit="ticks", min=1,
          help="The setup arms when price prints more than this far beyond the "
               "developing value-area edge — outside value, on the trade's "
               "side. Re-arming needs a fresh excursion: price must first come "
               "back within this distance of the edge."),
    Field("level_warmup_min", "int", "arming", "NY profile warm-up",
          unit="min", min=0,
          help="An NY-anchored profile minutes old has POC=VAH=VAL on the open "
               "print; nothing arms and nothing fills until the session is "
               "this old."),
    Field("accept_inside_bars", "int", "arming", "Confirming closes inside",
          unit="bar closes", min=1,
          help="This many CONSECUTIVE bar closes back inside the edge confirm "
               "the re-acceptance — value has taken price back, and the "
               "rotation may be entered. 1 is the bare close."),

    # --- entry ---
    Field("entry_variant", "enum", "entry", "Entry variant",
          choices=(("A", "A — rest a limit at the failed edge"),
                   ("B", "B — stop into the rotation")),
          help="A rests a limit at the edge after the confirming close and "
               "fills on the retest back to it; an edge that relocates across "
               "the market disarms instead of filling. B enters as price "
               "continues away from the edge, into value."),
    Field("entry_stop_offset_ticks", "int", "entry", "Stop-entry offset",
          unit="ticks", min=0, depends_on=("entry_variant", "B"),
          help="How far past the edge, into value, the entry stop sits after "
               "the confirming close."),

    # --- exit ---
    BY_NAME["stop_ticks"],
    Field("target", "enum", "exit", "Target",
          choices=(("poc", "The developing POC"),
                   ("mid", "The NY VWAP"),
                   ("rr", "A fixed R multiple")),
          help="poc and mid are tracked live. A POC that node-flips across "
               "price books a market fill at the print, never a limit fill at "
               "a level the market wasn't at; only price crossing the level "
               "fills at the level."),
    BY_NAME["target_rr"],
    Field("min_room_ticks", "int", "exit", "Minimum room to the POC",
          unit="ticks", min=1, zero_means_off=True, on_default=40,
          help="At the would-be fill, the developing POC must sit at least "
               "this far beyond the fill in the trade's direction, or the "
               "touch is missed. The trivial-rotation guard: ~40% of the "
               "lab's POC reversions were price already at or through the "
               "target — a rotation with nothing left to pay."),
    Field("invalidate_outside_bars", "int", "exit",
          "Exit when price is re-accepted outside the edge",
          unit="bar closes", min=0, zero_means_off=True, on_default=5,
          help="Exit at market after this many consecutive closes back "
               "outside the value-area edge — the premise run backwards. The "
               "fixed stop stays behind it as the hard backstop."),
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],

    # --- filters / lifecycle ---
    Field("rearm_after_exit", "bool", "filters", "Re-arm after every exit",
          help="Any exit disarms the setup — a fresh excursion outside value "
               "and fresh confirming closes must build before the next trade."),
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this. The default matches the NY profile "
               "warm-up — an earlier edge is the open print, not a level."),
    BY_NAME["entry_close"], BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

ROTATION_BY_NAME: dict[str, Field] = {f.name: f for f in ROTATION_FIELDS}


ORB_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "setup", "title": "Opening range", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

ORB_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- setup (the opening range) ---
    Field("window_minutes", "int", "setup", "Opening window", unit="min",
          min=1, max=120,
          help="Minutes from the 09:30 bell the opening range spans. 5 is the "
               "Zarattini window (the one that carried the paper's edge); 60 "
               "is the classic Initial Balance, the natural window for the "
               "second-break mode."),
    Field("entry_mode", "enum", "setup", "Entry mode",
          choices=(("candle", "Candle — enter with the window candle's direction at its close"),
                   ("break", "Break — stop in on the first crossing of an extreme"),
                   ("second_break", "Second break — enter with the second side broken")),
          help="candle is the Zarattini rule: one market entry at the first "
               "print after the window, in its candle's direction. break stops "
               "in on the window high/low. second_break waits for one extreme "
               "to break and trades the OTHER — the close landed on the second "
               "break's side on 81% of double-break days (IB study, n=53). One "
               "attempt per session in every mode."),
    Field("direction", "enum", "setup", "Direction",
          choices=(("both", "Both — trade whichever direction sets up"),
                   ("long_only", "Long only"),
                   ("short_only", "Short only")),
          help="Which breakout directions may trade. The documented futures "
               "filter is the opening candle itself, not the gap — the "
               "one-sided settings exist for A/B, not belief."),
    Field("entry_offset_ticks", "int", "setup", "Break offset", unit="ticks",
          min=0, depends_on=("entry_mode", "break"),
          help="The entry stop rests this many ticks beyond the window "
               "extreme (Crabel's stretch, fixed rather than ATR-derived). "
               "0 triggers on the extreme itself."),

    # --- exit ---
    Field("stop_mode", "enum", "exit", "Stop",
          choices=(("range", "Range — the window's opposite extreme"),
                   ("ticks", "Fixed ticks from the fill")),
          help="range is the paper's stop: the window's other side, so risk "
               "varies day by day with the window and every R is measured "
               "against the risk actually taken. ticks divorces risk from "
               "window size."),
    Field("stop_ticks", "int", "exit", "Initial stop", unit="ticks", min=1,
          depends_on=("stop_mode", "ticks"),
          help="Fixed distance from the fill, when the stop mode is ticks."),
    Field("target", "enum", "exit", "Target",
          choices=(("eod", "End of day — hold to the session close"),
                   ("rr", "A fixed R multiple")),
          help="eod is the paper's exit: no target, the trade runs to flat_by "
               "with only the stop (and the optional trail) behind it. rr "
               "fixes a target at entry, in multiples of the actual risk."),
    BY_NAME["target_rr"],
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],

    # --- filters ---
    Field("min_range_ticks", "int", "filters", "Min window range", unit="ticks",
          min=1, zero_means_off=True, on_default=40,
          help="Skip the day when the opening range is narrower than this — "
               "the noise floor. On a range stop this is also the risk floor."),
    Field("max_range_ticks", "int", "filters", "Max window range", unit="ticks",
          min=1, zero_means_off=True, on_default=800,
          help="Skip the day when the opening range is wider than this — the "
               "window already spent the day's travel."),

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    BY_NAME["entry_open"],
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries at or after this. The candle mode enters right "
               "at the window's end regardless; this caps how late the break "
               "modes may trigger."),
    BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

ORB_BY_NAME: dict[str, Field] = {f.name: f for f in ORB_FIELDS}


# --- the drift-touch fade's table ---------------------------------------------
# DriftFadeConfig's descriptors. No acceptance, no arming stretch, no resting
# limit — the drift classification at a bar close is the setup — so the sources
# group describes what a touch may land on and the detection group how a touch
# becomes a drift signal.

DRIFT_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "sources", "title": "Sources", "collapsed": False},
    {"key": "detection", "title": "Detection", "collapsed": False},
    {"key": "entry", "title": "Entry", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

DRIFT_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- sources (what a drift touch may land on) ---
    Field("use_ny_levels", "bool", "sources", "NY developing value",
          help="Developing NY POC/VAH/VAL, anchored at the bell. Degenerate for "
               "the first minutes — see the warm-up."),
    Field("use_globex_levels", "bool", "sources", "Globex developing value",
          help="Developing Globex POC/VAH/VAL, anchored at 18:00 ET the previous "
               "evening — ~15 hours mature by the open."),
    Field("use_session_refs", "bool", "sources", "Static session references",
          help="ONH/ONL, the prior day's POC/VAH/VAL and close, and the session "
               "open — the levels every trader has drawn before the bell."),
    Field("trade_poc", "bool", "sources", "POC",
          help="Include the POC of each enabled developing profile."),
    Field("trade_vah", "bool", "sources", "VAH",
          help="Include the VAH of each enabled developing profile."),
    Field("trade_val", "bool", "sources", "VAL",
          help="Include the VAL of each enabled developing profile."),
    Field("level_warmup_min", "int", "sources", "NY level warm-up",
          unit="min", min=0,
          help="An NY-anchored profile minutes old has POC=VAH=VAL on the open "
               "print; its levels (and the session-open ref) are not candidates "
               "until the anchor is this old. Globex levels and the prior-day "
               "refs are mature and never gated by this."),

    # --- detection ---
    Field("touch_tol", "float", "detection", "Touch tolerance", unit="pts",
          min=0.0,
          help="A bar touches a level when its low−tol ≤ level ≤ high+tol."),
    Field("touch_gap_bars", "int", "detection", "Touch gap", unit="bars", min=0,
          help="A re-approach counts as a fresh touch only after this many bars "
               "clear of the zone — one rotation sitting on a level is one "
               "touch, not many."),
    Field("min_level_stability_min", "int", "detection", "Minimum level stability",
          unit="min", min=1, zero_means_off=True, on_default=5,
          help="Skip a drift signal on a developing level that relocated more "
               "than the stability tolerance within this window — a drift touch "
               "on a freshly node-flipped level is the profile chasing the "
               "market, not a hug. Static refs never move."),
    Field("stability_tol_ticks", "int", "detection", "Stability tolerance",
          unit="ticks", min=1,
          help="How far a developing level may wander over the stability window "
               "and still count as having sat in place. Read only when the "
               "stability window is on."),

    # --- entry ---
    Field("entry_variant", "enum", "entry", "Entry variant",
          choices=(("A", "A — market on the drift-touch bar's close"),
                   ("B", "B — confirm beyond the touch extreme, then enter")),
          help="A enters at market on the next tick after the drift-touch bar "
               "closes, away from the level. B waits for a bar to close at least "
               "confirm_ticks beyond the touch bar's extreme on the fade side "
               "first — later, but it filters the instant-acceptance failures."),
    Field("confirm_ticks", "int", "entry", "Confirmation distance", unit="ticks",
          min=1, depends_on=("entry_variant", "B"),
          help="Variant B only: a bar must close at least this far beyond the "
               "touch bar's extreme, on the fade side, before the entry fires."),
    Field("side", "enum", "entry", "Direction",
          choices=(("both", "Both — fade whichever side price hugged"),
                   ("long", "Long only — price hugged above, fade to support"),
                   ("short", "Short only — price hugged below, fade to resistance")),
          help="Drift is the repo's first near-symmetric edge, but the house "
               "prior is long-only, so the A/B reads the sides separately before "
               "'both' ships as a baseline default."),
    Field("max_touches_per_zone", "int", "entry", "Touches per zone",
          unit="touches", min=1, zero_means_off=True, on_default=1,
          help="Cap fills to each zone's first N touches. The drift ratio held "
               "on re-tests, so this starts off (unlimited); the nth-touch cut "
               "is measured in the edges panel first."),

    # --- exit ---
    Field("stop_ticks", "int", "exit", "Initial stop", unit="ticks", min=1,
          help="Measured from the LEVEL, not the fill — the zone is the "
               "invalidation, not the entry print. The spec's sweep is 120-200 "
               "ticks (median drift-rejection MAE ≈ 26 pts, p75 ≈ 45)."),
    Field("target_mode", "enum", "exit", "Target",
          choices=(("ny_vwap", "The NY VWAP — the fade-to-value target"),
                   ("r_multiple", "A fixed R multiple"),
                   ("fixed_ticks", "A fixed distance")),
          help="ny_vwap is tracked live: a VWAP that node-flips across price "
               "books a market fill at the print, never a limit at a level the "
               "market wasn't at. r_multiple and fixed_ticks are struck at entry."),
    # This strategy's target switch is target_mode, not the bounce's `target`, so
    # the R field depends on target_mode == "r_multiple" (else the form disables
    # it whenever r_multiple is chosen).
    replace(BY_NAME["target_rr"], depends_on=("target_mode", "r_multiple")),
    Field("target_ticks", "int", "exit", "Target distance", unit="ticks", min=1,
          depends_on=("target_mode", "fixed_ticks"),
          help="A fixed take-profit distance from the fill."),
    Field("min_room_ticks", "int", "exit", "Minimum room to the VWAP",
          unit="ticks", min=1, zero_means_off=True, on_default=40,
          help="ny_vwap target only: at the would-be fill the VWAP must sit at "
               "least this far beyond the fill in the trade's direction, or the "
               "signal is skipped — a target already inside the stop is no trade."),
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],
    Field("max_hold_min", "int", "exit", "Max hold", unit="min", min=1,
          zero_means_off=True, on_default=15,
          help="Flatten the open trade at market once it has been held this "
               "many minutes without hitting its target — the give-up-on-a-grind "
               "exit. Winners snap back fast and losers grind underwater, so a "
               "hold cap is a candidate loser filter; it also cuts the slow-but-"
               "real winners, which is what the A/B has to weigh. Off by default."),
    Field("exit_return_to_source_ticks", "int", "exit",
          "Exit on a close back through the source", unit="ticks", min=1,
          zero_means_off=True, on_default=20,
          help="The fade enters at the drift level (the source) and runs away "
               "from it toward value, so a bar that closes back THROUGH the "
               "source — at least this many ticks past it on the losing side — "
               "is the premise run backwards. Exit at market on the next tick: a "
               "close-based early stop in front of the fixed stop behind the "
               "zone. Set it below the stop distance from the source or the fixed "
               "stop bites first. Off by default."),

    # --- filters / lifecycle ---
    Field("approach_mom_veto_min", "int", "filters", "Approach-momentum veto",
          unit="min", min=1, zero_means_off=True, on_default=30,
          help="Veto a touch when the net move in the trade's direction over "
               "this trailing window is positive at the fill — a with-move "
               "touch. Side-aware, so it works on side='both' where the "
               "single-sided gates cannot; vetoed entries ride as ghosts in "
               "the vetoed rows. Its A/B FAILED on both siblings: most drift "
               "touches genuinely arrive with-move, the veto kills ~2/3 of "
               "entries and halves net while the vetoed ghosts finish "
               "positive (the study lead behind it was a lookahead artifact "
               "— see docs/research/drift-fade-market-structure.md). Leave "
               "off."),
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this. The default (09:45) matches the NY level "
               "warm-up and excludes the open's impulse — 09:30-09:45 is dropped "
               "by the flagship's pre-checkpoint leak lesson."),
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries after this; open positions are left to run. The "
               "default (15:00) drops the last hour, whose touches scored at the "
               "null baseline."),
    BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

DRIFT_BY_NAME: dict[str, Field] = {f.name: f for f in DRIFT_FIELDS}


# The Globex-scope drift fade's descriptors. Same idea and very nearly the same
# knobs as DRIFT_FIELDS — only the readings that change meaning once the night is
# tradeable get their own descriptor, and the rest are shared by reference so the
# two forms cannot drift apart.
DRIFT_GX_GROUPS: tuple[dict, ...] = DRIFT_GROUPS

_DRIFT_GX_OVERRIDES: dict[str, Field] = {f.name: f for f in (
    Field("use_session_refs", "bool", "sources", "Session references",
          help="The overnight high/low, the prior day's POC/VAH/VAL and close, "
               "and the session open. Here ONH/ONL DEVELOP — the night's "
               "extremes so far, settling at the bell — because a fill at 21:00 "
               "cannot read the high the night has not made yet."),
    Field("level_warmup_min", "int", "sources", "NY level warm-up",
          unit="min", min=0,
          help="An NY-anchored profile minutes old has POC=VAH=VAL on the open "
               "print; its levels (and the session-open ref) are not candidates "
               "until the bell is this old. Before the bell they are absent "
               "entirely, which is what makes them safe to leave on."),
    Field("globex_warmup_min", "int", "sources", "Globex level warm-up",
          unit="min", min=0,
          help="The same guard for the Globex anchor, which this strategy DOES "
               "see young: at 18:05 its profile has POC=VAH=VAL on the open "
               "print. The RTH siblings never need it — by 09:30 the Globex "
               "profile is ~15 hours mature."),
    Field("stop_ticks", "int", "exit", "Initial stop", unit="ticks", min=1,
          help="Measured from the FILL, not the level — every trade risks the "
               "same distance and the zone is allowed to fail without ending "
               "the trade (the entry-stop sibling's invalidation)."),
    Field("target_mode", "enum", "exit", "Target",
          choices=(("gx_vwap", "The Globex VWAP — the fade-to-value target"),
                   ("r_multiple", "A fixed R multiple"),
                   ("fixed_ticks", "A fixed distance")),
          help="gx_vwap is the session's own value line, anchored at the 18:00 "
               "open and defined for every tick — the NY VWAP the RTH siblings "
               "fade to does not exist before the bell. Tracked live: a VWAP "
               "that node-flips across price books a market fill at the print, "
               "never a limit at a level the market wasn't at. r_multiple and "
               "fixed_ticks are struck at entry."),
    Field("min_room_ticks", "int", "exit", "Minimum room to the VWAP",
          unit="ticks", min=1, zero_means_off=True, on_default=40,
          help="gx_vwap target only: at the would-be fill the VWAP must sit at "
               "least this far beyond the fill in the trade's direction, or the "
               "signal is skipped — a target already inside the stop is no trade."),
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this. The default (18:00) is the Globex open — "
               "the whole session trades. Set later than the close and the "
               "window wraps midnight, which is what 18:00 -> 15:00 means."),
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries after this; open positions are left to run. The "
               "default (15:00) drops the last RTH hour, whose touches scored at "
               "the null baseline for the RTH siblings."),
)}

# Substituted in place, so the form's field order is the RTH sibling's. The one
# genuinely new knob (the Globex warm-up) is slotted next to the NY warm-up it
# mirrors rather than appended after it.
DRIFT_GX_FIELDS: tuple[Field, ...] = tuple(
    f2
    for f in DRIFT_FIELDS
    for f2 in ((_DRIFT_GX_OVERRIDES.get(f.name, f),)
               + ((_DRIFT_GX_OVERRIDES["globex_warmup_min"],)
                  if f.name == "level_warmup_min" else ()))
)

DRIFT_GX_BY_NAME: dict[str, Field] = {f.name: f for f in DRIFT_GX_FIELDS}


# --- the weekly −1σ deep-traverse's table -------------------------------------
# WeeklyTraverseConfig's descriptors. No sources and no entry variants — the
# traverse event at a 1-minute bar close is the setup and the entry is a market
# order on the next tick — so the detection group is the event's own conditions
# and the exit group is the study's race, made tradeable.

WEEKLY_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "detection", "title": "Detection", "collapsed": False},
    {"key": "entry", "title": "Entry", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

WEEKLY_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- detection (the study event's own conditions) ---
    Field("rearm_sigma", "float", "detection", "Re-arm clearance", unit="σ",
          min=0.05, max=2,
          help="A touched −1σ only re-arms after a full 1-minute bar trades "
               "clear of it by this many weekly sigmas — the study's episode "
               "rule, so a choppy hour hugging the band is one touch."),
    Field("max_res_below_min", "int", "detection", "Max prior residence below",
          unit="min", min=1,
          help="Strictly fewer than this many 1-minute closes below the weekly "
               "−1σ earlier in the session — a day already living under the "
               "band is a breakdown, not a traverse. The studied cell used 5."),
    Field("origin_lookback_min", "int", "detection", "Origin lookback",
          unit="min", min=10,
          help="How far back the leg's origin is read. The σ-position must have "
               "reached the origin threshold inside this trailing window."),
    Field("min_origin_sigma", "float", "detection", "Origin threshold", unit="σ",
          min=-3, max=3,
          help="The leg must have started this high in the weekly envelope: "
               "(close − weekly mid)/σ must have reached at least this inside "
               "the lookback. 0 — the studied cell — means price touched the "
               "weekly mid or better before traversing down. A real threshold, "
               "not an off switch."),

    # --- entry ---
    Field("rth_only", "bool", "entry", "RTH entries only",
          help="The study's frame is the whole Globex session, and the "
               "overnight cohort carried more per-trade edge in the draft — so "
               "this is off by default. On, signals are confined to "
               "09:30–16:00 ET."),

    # --- exit ---
    Field("stop_mode", "enum", "exit", "Stop anchor",
          choices=(("level_sigma", "Below the level — the study's race stop"),
                   ("entry_ticks", "Below the fill — fixed risk")),
          help="level_sigma parks the stop a σ-fraction below the −1σ level, "
               "frozen at the signal — the band failing is the invalidation, "
               "and the risk varies with the band's width. entry_ticks anchors "
               "a fixed distance below the fill instead: every trade risks the "
               "same, and the band is allowed to fail without ending the trade."),
    Field("stop_sigma", "float", "exit", "Stop distance", unit="σ",
          min=0.05, max=2, depends_on=("stop_mode", "level_sigma"),
          help="How many weekly sigmas below the −1σ level the stop sits. The "
               "draft's race used 0.30."),
    Field("stop_ticks", "int", "exit", "Stop distance", unit="ticks", min=1,
          depends_on=("stop_mode", "entry_ticks"),
          help="Fixed stop distance below the fill."),
    Field("target_mode", "enum", "exit", "Target",
          choices=(("level_sigma", "Above the level — the study's race target"),
                   ("wk_mid", "The weekly mid, tracked live"),
                   ("r_multiple", "A fixed R multiple")),
          help="level_sigma is the draft's race target, frozen at the signal. "
               "wk_mid rides the reversion to the hypothesis's actual magnet — "
               "the edge grew with horizon — with the crossing discipline: "
               "price crossing the mid is a limit fill at the mid, a mid that "
               "relocates across price books a market fill at the print. "
               "r_multiple is struck at entry against the risk actually taken."),
    Field("target_sigma", "float", "exit", "Target distance", unit="σ",
          min=0.05, max=2, depends_on=("target_mode", "level_sigma"),
          help="How many weekly sigmas above the −1σ level the target sits. The "
               "draft's race used 0.30."),
    replace(BY_NAME["target_rr"], depends_on=("target_mode", "r_multiple")),
    Field("min_room_ticks", "int", "exit", "Minimum room to the mid",
          unit="ticks", min=1, zero_means_off=True, on_default=40,
          depends_on=("target_mode", "wk_mid"),
          help="wk_mid target only: at the would-be fill the weekly mid must "
               "sit at least this far above the fill, or the signal is skipped "
               "— a target already inside the stop distance is no trade."),
    BY_NAME["trail_stop_ticks"], BY_NAME["trail_step_ticks"],
    BY_NAME["trail_breakeven_ticks"], BY_NAME["trail_breakeven_only"],
    Field("max_hold_min", "int", "exit", "Max hold", unit="min", min=1,
          zero_means_off=True, on_default=60,
          help="Flatten at market once the trade has been held this many "
               "minutes, target unmet. 60 is the draft's outcome horizon; the "
               "study's edge was still growing at 120."),
    BY_NAME["underwater_exit_after_s"],

    # --- filters / lifecycle ---
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

    # --- scope / bars / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    Field("ticks_per_bar", "int", "bars", "Ticks per chart candle", min=1,
          help="The chart's candle size only. Detection runs on 1-minute bars "
               "over the full Globex session — the study's own frame — whatever "
               "the chart draws."),
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

WEEKLY_BY_NAME: dict[str, Field] = {f.name: f for f in WEEKLY_FIELDS}


# --- the 9/20 EMA pullback's table --------------------------------------------
# EmaPullbackConfig's descriptors. No acceptance and no arming — the EMA in force
# is the setup — so the setup group describes what makes a pullback a trade, and
# the levels group picks which EMA(s) a limit may rest on.

EMA_GROUPS: tuple[dict, ...] = (
    {"key": "window", "title": "Window", "collapsed": False},
    {"key": "levels", "title": "EMAs", "collapsed": False},
    {"key": "entry", "title": "Entry", "collapsed": False},
    {"key": "setup", "title": "Setup", "collapsed": False},
    {"key": "exit", "title": "Exit", "collapsed": False},
    {"key": "filters", "title": "Filters & lifecycle", "collapsed": False},
    {"key": "scope", "title": "Instrument", "collapsed": True},
    {"key": "bars", "title": "Bars", "collapsed": True},
    {"key": "session", "title": "Session times (ET)", "collapsed": True},
    {"key": "size", "title": "Size & cost", "collapsed": True},
)

EMA_FIELDS: tuple[Field, ...] = (
    # --- window ---
    BY_NAME["start_date"], BY_NAME["end_date"],

    # --- EMAs (which line a limit may rest on) ---
    Field("use_ema9", "bool", "levels", "9 EMA",
          help="The fast 1-minute EMA. Warmed over the overnight, so it is a "
               "real line from the bell."),
    Field("use_ema20", "bool", "levels", "20 EMA",
          help="The slow 1-minute EMA. Each enabled EMA is its own resting "
               "limit."),
    Field("use_ema50", "bool", "levels", "50 EMA",
          help="The intermediate 1-minute EMA. Pullbacks onto it are rarer and "
               "deeper than onto the 9/20."),
    Field("use_ema200", "bool", "levels", "200 EMA",
          help="The regime-scale 1-minute EMA — the slowest line the chart "
               "draws. Inside the upper channel it is usually far below price, "
               "so a touch is a deep pullback."),

    # --- entry ---
    Field("entry_variant", "enum", "entry", "Entry variant",
          choices=(("A", "A — rest a limit on the EMA"),
                   ("B", "B — confirmation close back above the EMA")),
          help="A fills on the pullback touch of the EMA. B waits for a bar to "
               "close the confirmation distance back above the EMA after the "
               "touch — the bounce confirmed — and enters at market on the next "
               "tick."),
    Field("confirm_ticks", "int", "entry", "Confirmation distance", unit="ticks",
          min=0, depends_on=("entry_variant", "B"),
          help="Variant B: a bar must close at least this far back above the EMA "
               "to confirm the bounce and trigger the entry."),

    # --- setup (what makes a pullback a trade) ---
    Field("band_region", "enum", "setup", "Where the EMA must sit",
          choices=(("channel", "Inside dev1..dev2 — the upper channel"),
                   ("above_dev1", "At or above dev1 — channel and above"),
                   ("above_dev2", "At or above dev2 — the far band only"),
                   ("off", "Anywhere — no band gate")),
          help="Which region of the NY VWAP upper channel the EMA (and so the "
               "fill) must sit in. 'channel' is the inside-the-upper-bands "
               "premise; 'above_dev1' opens it to the overextended zone above "
               "dev2 as well; 'above_dev2' takes only that far-band zone; 'off' "
               "trades every pullback onto the EMA (to measure the band context "
               "matters at all)."),
    Field("rearm_ticks", "int", "setup", "Re-arm distance", unit="ticks", min=0,
          help="Price must clear the EMA by this much before a new touch of it "
               "can fill — a rotation sitting on the line is one touch, not "
               "many."),

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
    Field("require_stacked", "bool", "filters", "EMAs must be stacked (9 above 20)",
          help="Take the pullback only when the fast 9 sits at or above the slow "
               "20 at the fill — the stacked-bull context. Reads both lines "
               "whichever one is traded."),
    Field("min_ema_gap_ticks", "int", "filters", "Minimum EMA separation",
          unit="ticks", min=1, zero_means_off=True, on_default=8,
          help="Skip the fill when the two EMAs are within this many ticks of "
               "each other — a convergence is the signature of chop, where a "
               "pullback has no trend slope to lean on. 0 = off."),
    Field("min_band_width_ticks", "int", "filters", "Minimum upper band width",
          unit="ticks", min=0, zero_means_off=True, on_default=20,
          help="Skip the fill when the NY VWAP +1σ..+2σ channel the EMA sits in "
               "(dev2−dev1) is tighter than this. A pinched band makes the "
               "inside-the-channel condition trivial and leaves the pullback no "
               "room to run."),
    Field("open_stack_veto", "enum", "filters", "Opening-stack session veto",
          choices=(("off", "Off — trade every day"),
                   ("bear", "Bear-stacked open — stand down for the day"),
                   ("not_bull", "Not bull-stacked — stand down for the day")),
          help="The 20/50/200 study's session gate: the 1-minute 20/50/200 EMA "
               "ordering at the close of the 09:35 bar classifies the day, and "
               "a vetoed day takes no trades at all (whole-day on/off — the "
               "intraday re-arm chains are never perturbed). 'bear' stands "
               "down only a bear-stacked open (20 < 50 < 200 — the study's "
               "loss engine); 'not_bull' also drops mixed opens. With the veto "
               "on, nothing fills before the 09:35 bar closes."),
    BY_NAME["daily_loss_stop"], BY_NAME["daily_loss_exit_open"],

    # --- scope / bars / session / size ---
    BY_NAME["instrument"], BY_NAME["contract"],
    BY_NAME["ticks_per_bar"],
    Field("entry_open", "time", "session", "Entry window opens",
          help="No entries before this. The default skips the open's impulse; "
               "the EMA is already warm, seeded over the overnight."),
    Field("entry_close", "time", "session", "Entry window closes",
          help="No new entries after this; open positions are left to run."),
    BY_NAME["flat_by"],
    BY_NAME["contracts"], BY_NAME["commission_per_side"],
)

EMA_BY_NAME: dict[str, Field] = {f.name: f for f in EMA_FIELDS}


# config class -> (groups, fields, by-name index). The registry's config_cls is
# the key, so parsing, validation and the served form blueprint all follow from
# one declaration on the strategy.
_TABLES: dict[type, tuple[tuple[dict, ...], tuple[Field, ...], dict[str, Field]]] = {
    SimConfig: (GROUPS, FIELDS, BY_NAME),
    GlobexBounceConfig: (GLOBEX_GROUPS, GLOBEX_FIELDS, GLOBEX_BY_NAME),
    FadeConfig: (FADE_GROUPS, FADE_FIELDS, FADE_BY_NAME),
    ProfilePullbackConfig: (PULLBACK_GROUPS, PULLBACK_FIELDS, PULLBACK_BY_NAME),
    ValueRotationConfig: (ROTATION_GROUPS, ROTATION_FIELDS, ROTATION_BY_NAME),
    OrbConfig: (ORB_GROUPS, ORB_FIELDS, ORB_BY_NAME),
    DriftFadeConfig: (DRIFT_GROUPS, DRIFT_FIELDS, DRIFT_BY_NAME),
    DriftFadeGlobexConfig: (DRIFT_GX_GROUPS, DRIFT_GX_FIELDS, DRIFT_GX_BY_NAME),
    EmaPullbackConfig: (EMA_GROUPS, EMA_FIELDS, EMA_BY_NAME),
    WeeklyTraverseConfig: (WEEKLY_GROUPS, WEEKLY_FIELDS, WEEKLY_BY_NAME),
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
    if getattr(cfg, "target", None) == "rr" and not cfg.target_rr:
        # Engine: `if cfg.target == "rr" and cfg.target_rr` — a null here trades
        # the live target (dev2, or the fade's mid) while claiming an R target.
        # getattr: the drift fade names its target knob target_mode (checked below).
        raise ValueError("target 'rr' needs a target_rr")
    if cfg.start_date > cfg.end_date:
        raise ValueError(
            f"start_date {cfg.start_date} is after end_date {cfg.end_date}")
    if getattr(cfg, "daily_loss_exit_open", False) and not cfg.daily_loss_stop:
        # The flatten trips against the daily loss stop's dollar figure; with no
        # stop set it has no threshold and would be a silent no-op — a config that
        # looks like it caps the open trade but never does. (getattr: OrbConfig
        # has neither knob.)
        raise ValueError(
            "daily_loss_exit_open needs a daily_loss_stop set: it flattens the "
            "open trade against that limit, and without one there is nothing to "
            "trip on")
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
        if (cfg.pyramid_tranches > 1 and cfg.pyramid_direction == "against"
                and (cfg.pyramid_tranches - 1) * cfg.pyramid_step_ticks
                >= cfg.stop_ticks):
            # The deepest add would rest at or past the stop itself: it could only
            # ever fill on the print that kills the trade, booking a lot whose
            # entire life is its own stop-out.
            raise ValueError(
                f"averaging down {cfg.pyramid_tranches} lots every "
                f"{cfg.pyramid_step_ticks} ticks puts the last add "
                f"{(cfg.pyramid_tranches - 1) * cfg.pyramid_step_ticks} ticks under "
                f"the entry, at or past the {cfg.stop_ticks}-tick stop")
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
    if isinstance(cfg, EmaPullbackConfig):
        if not (cfg.use_ema9 or cfg.use_ema20 or cfg.use_ema50 or cfg.use_ema200):
            # No candidate line: the engine would run green and take zero trades
            # forever, reading as "the idea never sets up" rather than "inert".
            raise ValueError("enable at least one EMA (9, 20, 50 or 200)")
    if isinstance(cfg, OrbConfig):
        if (cfg.min_range_ticks and cfg.max_range_ticks
                and cfg.min_range_ticks > cfg.max_range_ticks):
            # The two bounds would exclude every day; the engine would run
            # green and take zero trades forever, reading as "never sets up".
            raise ValueError(
                f"min_range_ticks ({cfg.min_range_ticks}) may not exceed "
                f"max_range_ticks ({cfg.max_range_ticks})")
    if isinstance(cfg, DriftFadeConfig):
        if cfg.target_mode == "r_multiple" and not cfg.target_rr:
            # The engine trades the live VWAP target when target_rr is null, so a
            # config claiming an R target with no multiple would silently fade to
            # value instead of the R it says.
            raise ValueError("target_mode 'r_multiple' needs a target_rr")
        if not (cfg.use_ny_levels or cfg.use_globex_levels or cfg.use_session_refs):
            raise ValueError(
                "enable at least one source (NY, Globex or session refs)")
        if ((cfg.use_ny_levels or cfg.use_globex_levels)
                and not (cfg.trade_poc or cfg.trade_vah or cfg.trade_val)):
            # A developing source with no level type selected contributes no
            # candidates; if it is the ONLY source the run takes zero trades
            # forever, reading as "the idea never sets up".
            raise ValueError(
                "a developing source (NY or Globex) needs at least one level "
                "type (POC, VAH or VAL)")
        if cfg.side == "both" and any(
                s.get("enabled") for s in (cfg.confluences or {}).values()):
            # A gate reads one signed session context (SessionCtx.side); a
            # both-sided run has no single side to hand it. Gate experiments run
            # per-side by design (the spec's A/B reads the sides separately), so
            # an ENABLED gate on a both-sided run is refused rather than
            # mis-scored. Disabled sections don't count — the run form
            # materializes one per gate whether ticked or not, and they collapse
            # to absence in canonicalize; only an armed gate needs a side.
            raise ValueError(
                "a confluence gate needs a single side: set side to 'long' or "
                "'short' (gates are scored against one signed context)")
    if isinstance(cfg, DriftFadeGlobexConfig):
        # Same three source/target invariants as the RTH sibling — a separate
        # block because this is a separate class, not a subclass (isinstance
        # dispatch here has to stay unambiguous).
        if cfg.target_mode == "r_multiple" and not cfg.target_rr:
            raise ValueError("target_mode 'r_multiple' needs a target_rr")
        if not (cfg.use_ny_levels or cfg.use_globex_levels or cfg.use_session_refs):
            raise ValueError(
                "enable at least one source (NY, Globex or session refs)")
        if ((cfg.use_ny_levels or cfg.use_globex_levels)
                and not (cfg.trade_poc or cfg.trade_vah or cfg.trade_val)):
            raise ValueError(
                "a developing source (NY or Globex) needs at least one level "
                "type (POC, VAH or VAL)")
        if cfg.entry_open == cfg.entry_close:
            # The engine reads open > close as a window wrapping midnight, so
            # equal times are the one unreachable pair: neither branch admits a
            # single minute and the run takes zero trades forever, reading as
            # "the idea never sets up".
            raise ValueError(
                "entry_open and entry_close may not be equal (the window would "
                "admit no time at all)")
        if any(s.get("enabled") for s in (cfg.confluences or {}).values()):
            # Every gate this family supports anchors to an RTH checkpoint
            # (09:45/10:30) or reads the NY-anchored value edge, neither of which
            # has a defined value for an overnight fill. The registry offers
            # none, so an enabled section here is refused rather than mis-scored.
            raise ValueError(
                "the Globex-scope drift fade supports no confluence gates: they "
                "are anchored to RTH checkpoints and the NY value edge, which an "
                "overnight fill has no reading of")
    if isinstance(cfg, WeeklyTraverseConfig):
        if cfg.target_mode == "r_multiple" and not cfg.target_rr:
            # The engine strikes the R target off the risk actually taken; a null
            # multiple would divide the trade's premise by nothing — refused
            # rather than quietly traded as some other target.
            raise ValueError("target_mode 'r_multiple' needs a target_rr")
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
