"""The strategy registry: ideas are code, tweaks are config.

Each entry is a distinct trading idea with its own engine entry point, its own
config schema, and its own run history under data/sims/<slug>/. A new idea is a
new registry entry — never a flag on an existing one. The line between the two:
a config knob may filter or tune the idea; anything that changes what/when/how
it enters or exits is a new strategy (or a coded variant inside one, like the
A/B entry variants of the VWAP bounce).

``version`` is part of every run's identity hash. Bump it whenever the engine's
*semantics* change (a rule fix, a behavior change) — not for refactors — so runs
produced by different code can never be silently compared. The UI refuses to
show a baseline delta across versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import engine
from .rules import FadeConfig, GlobexBounceConfig, ProfilePullbackConfig, SimConfig


@dataclass(frozen=True)
class Strategy:
    slug: str
    name: str
    description: str
    version: str
    config_cls: type = SimConfig
    # (cfg, day) -> (trades, vetoed, bars, bands); one session of the idea.
    run_session: Callable = engine.run_session
    # Confluence gate names (see confluences.GATE_FACTORIES) this idea supports.
    confluences: tuple[str, ...] = ()
    # Which tick segments the idea reads: "rth" (09:30–16:00 ET) or "globex"
    # (also the 18:00→09:30 overnight — e.g. a Globex-anchored VWAP). This is
    # a strategy attribute, not a config knob, because the engine entry point
    # must actually consume the extra data; a knob it ignored would let a
    # "different" config produce byte-identical results.
    session: str = "rth"


STRATEGIES: dict[str, Strategy] = {
    s.slug: s
    for s in [
        Strategy(
            slug="vwap-upper-band-bounce",
            name="VWAP Upper Band Bounce",
            description=(
                "Long the pullback to VWAP +1σ (dev1) after an acceptance candle "
                "closes above it; target +2σ (dev2) or an R-multiple, fixed-tick "
                "stop. Entry variant A rests a limit at dev1 — or "
                "entry_limit_offset_ticks above it, to fill the pullback before it "
                "reaches the band; variant B waits for "
                "a close below dev1 and buy-stops the reclaim. The volume_profile "
                "confluence additionally requires the fill to be above the "
                "developing VAH, and exit_below_vah_bars exits when price is "
                "re-accepted back inside value. trail_stop_ticks turns the fixed "
                "stop into a ratchet that follows that far behind the best price "
                "the trade has seen — its first move is to breakeven — resting on "
                "a grid of trail_step_ticks measured from the entry. The "
                "regime confluence stands the strategy down from its checkpoint "
                "(09:45 or 10:30 ET) on days whose morning lived below both "
                "anchored VWAPs; vwap_slope does the same on days whose NY VWAP "
                "has no upward grade at the checkpoint, vwap_cross on days "
                "churning back and forth across the NY VWAP, and upper_occupancy "
                "on days that have barely visited the NY upper channel. "
                "gx_rescue stands it down after 09:45 when broken session bands "
                "are not being caught by the Globex band beneath; gx_floor "
                "requires each fill to have the Globex dev1 within reach below "
                "it as a second floor. on_high requires each fill to be within "
                "reach of the overnight session high — beneath that wall, "
                "rallies sell into the night's inventory — and gx_value "
                "requires the fill beyond the developing GLOBEX value area "
                "(above its VAH), not just the session's. gx_poc_shape vetoes "
                "entries while the developing Globex POC hangs just below the "
                "Globex VWAP — a thin, low-participation rally over unfilled "
                "value."
            ),
            # v2: added the developing-value-area exit (exit_below_vah_bars) and
            # the volume_profile gate. Both are off by default and a config that
            # leaves them off simulates identically to v1 — but the exit lives on
            # the base rule path, so v1 runs are quarantined rather than trusted.
            # v3: added the step trail (trail_step_ticks). Same story — off by
            # default, but it moves the stop on the base rule path.
            # v4: variant A's dev1 limit now fills on the *crossing* back to dev1,
            # not on the standing "price is past dev1" inequality. Under v3, any
            # tick where the entry check was suspended (min_band_width_ticks not
            # yet met, pre-entry_open) let price run away from dev1 while still
            # armed — the check then woke up and booked a fill at a dev1 the market
            # had long left, sometimes from the *opposite* band.
            # v5: added the daily loss stop (daily_loss_stop) — no new entries
            # once the session's realized net P&L hits the limit. Off by default,
            # but it halts entries on the base rule path, so v4 runs are
            # quarantined rather than trusted.
            # v6: added entry_limit_offset_ticks — variant A's limit may now rest in
            # front of dev1 instead of on it. 0 (the default) rests it on dev1 and
            # simulates identically to v5, but it moves the fill price on the base
            # rule path, so v5 runs are quarantined rather than trusted.
            version="6",
            confluences=("volume_profile", "regime", "vwap_slope", "vwap_cross",
                         "upper_occupancy", "gx_rescue", "gx_floor", "on_high",
                         "gx_value", "gx_poc_shape"),
        ),
        Strategy(
            slug="vwap-globex-bounce",
            name="VWAP Globex Band Bounce",
            description=(
                "The band bounce read against a VWAP anchored at the Globex open "
                "(18:00 ET the previous evening) instead of the 09:30 bell, so the "
                "bands price the RTH session against the whole overnight "
                "distribution. side picks the direction: 'long' bounces the upper "
                "bands (acceptance above dev1 arms, buy the pullback to it, target "
                "dev2), 'short' mirrors onto the lower bands (acceptance below dev1, "
                "sell the pullback, target the lower dev2) — the engine reads every "
                "comparison off a signed frame, so the long-flavoured knob names "
                "(acceptance_require_green, invalidate_below_mid_bars, "
                "exit_below_vah_bars) mean their mirror on a short. Rules are "
                "otherwise the session strategy's: variant A rests a limit at dev1 "
                "(or entry_limit_offset_ticks in front of it) and variant B stops "
                "into the reclaim, fixed-tick stop. Overnight ticks feed the VWAP, "
                "the bars and the profile only — acceptance and the invalidations "
                "are read from RTH bar closes alone, and entries are confined to the "
                "entry window as usual. The volume_profile confluence mirrors with "
                "the band (VAH above, VAL below). invert flips the band each "
                "direction reads: off is the bounce (long→upper, short→lower, "
                "running out to dev2); on makes long buy the pullback into the "
                "LOWER band and short sell the rally into the UPPER — same dev1 "
                "entry, opposite direction, reverting toward the mid on an "
                "R-multiple target."
            ),
            # v2: added the step trail (trail_step_ticks), which moves the stop
            # on the base rule path — v1 runs are quarantined rather than trusted.
            # v3: variant A's dev1 limit fills on the crossing back to dev1, not on
            # the standing inequality — see vwap-upper-band-bounce v4.
            # v4: added the daily loss stop (daily_loss_stop) — see
            # vwap-upper-band-bounce v5.
            # v5: added entry_limit_offset_ticks — variant A's limit may rest in
            # front of dev1 instead of on it. See vwap-upper-band-bounce v6.
            # v6: the direction is now a config knob (GlobexBounceConfig.side),
            # absorbing the retired vwap-globex-lower-bounce. side="long" (the
            # default) simulates identically to v5, but the knob rides the base
            # rule path and is part of the config hash, so v5 runs are quarantined
            # rather than trusted.
            # v7: added invert (GlobexBounceConfig.invert) — long may read the
            # lower band and short the upper, reverting toward the mid. invert=False
            # (the default) reads the same band and simulates identically to v6, but
            # the knob rides the base rule path and is part of the config hash, so
            # v6 runs are quarantined rather than trusted.
            version="7",
            config_cls=GlobexBounceConfig,
            confluences=("volume_profile",),
            run_session=engine.run_session_globex,
            session="globex",
        ),
        Strategy(
            slug="vwap-lower-band-bounce",
            name="VWAP Lower Band Bounce",
            description=(
                "The upper-band bounce mirrored: short the pullback to VWAP −1σ "
                "(dev1) after an acceptance candle closes below it; target −2σ "
                "(dev2) or an R-multiple, fixed-tick stop above. Entry variant A "
                "rests a sell limit at dev1 — or entry_limit_offset_ticks BELOW it, "
                "the mirror of the long's 'in front of dev1'. Variant B waits for a "
                "close above dev1 "
                "and sell-stops the rejection. The volume_profile confluence "
                "additionally requires the fill to be below the developing VAL, and "
                "exit_below_vah_bars exits when price is re-accepted back up inside "
                "value. The config knobs keep their long-flavoured names and mean "
                "the mirror here: acceptance_require_green demands a RED candle, "
                "invalidate_below_mid_bars counts closes ABOVE the VWAP mid, and "
                "exit_below_vah_bars counts closes ABOVE the VAL. The on_high and "
                "gx_value confluences mirror onto this side too: the fill must be "
                "within reach of the overnight LOW, and beyond (below) the "
                "developing Globex VAL."
            ),
            # v2: added the step trail (trail_step_ticks), which moves the stop
            # on the base rule path — v1 runs are quarantined rather than trusted.
            # v3: variant A's dev1 limit fills on the crossing back to dev1, not on
            # the standing inequality — see vwap-upper-band-bounce v4.
            # v4: added the daily loss stop (daily_loss_stop) — see
            # vwap-upper-band-bounce v5.
            # v5: added entry_limit_offset_ticks — variant A's limit may rest in
            # front of dev1 instead of on it. See vwap-upper-band-bounce v6.
            version="5",
            confluences=("volume_profile", "on_high", "gx_value"),
            run_session=engine.run_session_short,
        ),
        # vwap-globex-lower-bounce retired: its short is now vwap-globex-bounce
        # with side="short". Its run history stays on disk under the old slug but
        # the strategy is no longer registered.
        Strategy(
            slug="vwap-dev1-fade-short",
            name="VWAP Dev1 Fade Short",
            description=(
                "The bounce's counter-trade: short the return to VWAP +1σ (dev1) "
                "after price overextended beyond it, targeting reversion to the "
                "VWAP mid (or the opposite dev1, or an R-multiple), fixed-tick "
                "stop above. The setup arms on the stretch — a print more than "
                "arm_extension_ticks past dev1 — and arming is edge-triggered: "
                "a disarm is only undone by a fresh stretch, never by the old one "
                "still standing. arm_stretch_side picks which way that stretch "
                "runs: 'beyond' is the overextension above the band, sold on the "
                "return down to it; 'inside' arms on the mirror — a rip back "
                "DOWN through dev1, into the channel — and sells the retest back "
                "UP to the band, the broken dev1 resold from underneath. Only "
                "the stretch flips; it is still a short at dev1 reverting to the "
                "mid, so the stop, the targets, the dev2 cap and the dev1 "
                "re-acceptance exit are unmoved. arm_require_mid_cross demands the approach began "
                "at the VWAP mid since the last fill; arm_cap_at_dev2 stands the "
                "setup down on a bar close beyond dev2 (a runaway, not an "
                "overextension). Entry variant A rests a sell limit at dev1 — or "
                "entry_limit_offset_ticks in front of it, toward the stretch — "
                "and fills on the crossing back to the band; variant B waits for "
                "a bar to close back inside dev1 and stops into the continuation. "
                "invalidate_beyond_dev1_bars exits at market when price is "
                "re-accepted back beyond dev1 — the bounce's acceptance, and so "
                "this trade's structural invalidation — with the fixed stop as "
                "the hard backstop behind it. The trail, the daily loss stop and "
                "the band-width filter are the bounce's, unchanged. The "
                "volume_profile confluence requires the fill to be above the "
                "developing VAH: shorting from above value, back into it, is the "
                "mean-reversion premise stated in profile terms. The cap gates "
                "are the bounce's regime stand-downs mirrored, because this "
                "trade fights the grade the bounce leans on: vwap_slope_cap "
                "stands the strategy down from its checkpoint on days whose NY "
                "VWAP has already established a steep upward grade, "
                "upper_occupancy_cap on days already camped in the NY upper "
                "channel, and gx_rescue_cap on days whose broken session bands "
                "keep getting caught by the Globex band underneath — the floor "
                "the fade would be selling into."
            ),
            # v2: added arm_stretch_side — the arming stretch may run INSIDE dev1
            # (the band broken and retested) instead of beyond it. "beyond" is v1's
            # rule exactly, but the knob is part of the config hash either way.
            version="2",
            config_cls=FadeConfig,
            confluences=("volume_profile", "vwap_slope_cap",
                         "upper_occupancy_cap", "gx_rescue_cap"),
            run_session=engine.run_session_fade_short,
        ),
        Strategy(
            slug="vwap-dev1-fade-long",
            name="VWAP Dev1 Fade Long",
            description=(
                "The dev1 fade short mirrored onto the lower bands: long the "
                "return to VWAP −1σ (dev1) after price overextended BELOW it — "
                "a print more than arm_extension_ticks under the band — "
                "targeting reversion to the VWAP mid (or the opposite dev1, or "
                "an R-multiple), fixed-tick stop below. Arming is the short's, "
                "reflected: the stretch is a rip DOWN through dev1, not up, and "
                "it is edge-triggered the same way — a disarm is only undone by "
                "a fresh stretch. arm_stretch_side reflects with it: 'beyond' is "
                "the overextension below the band, and 'inside' arms on the rip "
                "back UP through dev1 into the channel, buying the retest back "
                "DOWN to the band — the broken −1σ rebought from above. Entry "
                "variant A rests a buy limit at dev1 (or "
                "entry_limit_offset_ticks in front of it, toward the stretch) "
                "and fills on the crossing back up to the band; variant B waits "
                "for a bar to close back inside dev1 and stops into the "
                "continuation. The config knobs keep their short-flavoured names "
                "and mean the mirror here: invalidate_beyond_dev1_bars counts "
                "closes re-accepted BELOW dev1, and the volume_profile "
                "confluence requires the fill to be below the developing VAL — "
                "buying from under value, back into it, is the mean-reversion "
                "premise stated in profile terms. The cap gates mirror with the "
                "band they read: vwap_slope_cap stands the strategy down on a "
                "steep DOWNWARD NY VWAP grade, upper_occupancy_cap on days "
                "camped in the NY LOWER channel, and gx_rescue_cap on days whose "
                "broken session −1σ keeps getting caught by the Globex −1σ "
                "above it — the ceiling this fade would be buying into."
            ),
            # v2: arm_stretch_side, with the short — see vwap-dev1-fade-short.
            version="2",
            config_cls=FadeConfig,
            confluences=("volume_profile", "vwap_slope_cap",
                         "upper_occupancy_cap", "gx_rescue_cap"),
            run_session=engine.run_session_fade_long,
        ),
        Strategy(
            slug="profile-pullback-long",
            name="Profile Pullback Long",
            description=(
                "The Interactions Lab's upper-band cut, traded: long the "
                "pullback-from-above onto a developing profile level — the NY "
                "or Globex POC/VAH, per the config — while the level sits "
                "inside the NY VWAP +1σ..+2σ channel. A limit rests on each "
                "candidate level in force and fills on the crossing back down "
                "to it; price must first have cleared the level by rearm_ticks, "
                "and must clear it afresh after every touch and every exit, so "
                "a rotation sitting on a level is one touch, not many. "
                "max_touches_per_level restricts fills to each level's first N "
                "touches (the study's strongest sub-cut), and "
                "require_confluence_pts demands a second candidate level "
                "stacked within reach of the fill, and "
                "min_level_stability_min skips fills on a level that only "
                "just relocated to its price — the profile chasing the "
                "market, not a level anyone defended. NY levels are not "
                "candidates until their profile is level_warmup_min old — "
                "younger than that, POC/VAH/VAL all sit on the open print. "
                "Exits are a fixed-tick stop and an R-multiple or fixed-tick "
                "target, with the bounce's optional trail; there is no "
                "structural invalidation — the research showed the stop IS the "
                "rule (trend-down days run a median ~89 pts against this "
                "entry). Entries default to 09:45–15:00 ET: the last hour's "
                "touches scored exactly at the measured null baseline. No "
                "day-type gate is offered on purpose — no pre-known feature "
                "predicted the bounce."
            ),
            # v2: overnight crossings of a Globex level consume the arm but no
            # longer count as touches — under v1 they spent the level's first
            # touch before the bell, silently disabling every Globex level
            # under max_touches_per_level. Touch counts are RTH-only, as in the
            # study; v1 runs are quarantined rather than trusted.
            # v3: a level that VA-snaps across price disarms instead of
            # filling. Under v2 the crossing check was the standing inequality
            # over the level's own motion, so a profile rebuilding its value
            # area over the market booked a "fill" at a level the market sat
            # below — a limit no real book would have filled. Fills now require
            # the previous print above the current level (price did the
            # crossing); v2 runs are quarantined rather than trusted.
            # v4: the touch-gap rule in time. A crossing armed less than
            # min_arm_min ago is the same rotation continuing — no fill, no
            # touch counted — and a level that relocates upward under a
            # standing arm voids it (into the approach zone: disarm; still
            # clear: the dwell clock restarts). Under v3 a POC that node-
            # flipped 150 pts to land under the market filled on the next
            # downtick off a 22-minute-old arm certified against the old
            # level, and a 10-second poke re-armed it. Both rules sit on the
            # base path; v3 runs are quarantined rather than trusted.
            version="4",
            config_cls=ProfilePullbackConfig,
            run_session=engine.run_session_profile_pullback,
            # Globex session: the overnight segment feeds the Globex developing
            # profile (and the charts); the NY bands and NY profile are still
            # anchored at the bell, and trading lives in RTH only.
            session="globex",
        ),
    ]
}


def get(slug: str) -> Strategy:
    if slug not in STRATEGIES:
        raise KeyError(f"unknown strategy {slug!r} (available: {sorted(STRATEGIES)})")
    return STRATEGIES[slug]
