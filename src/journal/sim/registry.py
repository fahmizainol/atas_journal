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
from .rules import FadeConfig, SimConfig


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
                "it as a second floor."
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
                         "upper_occupancy", "gx_rescue", "gx_floor"),
        ),
        Strategy(
            slug="vwap-globex-bounce",
            name="VWAP Globex Upper Band Bounce",
            description=(
                "The upper-band bounce read against a VWAP anchored at the Globex "
                "open (18:00 ET the previous evening) instead of the 09:30 bell, so "
                "the bands price the RTH session against the whole overnight "
                "distribution. Rules are otherwise the session strategy's: acceptance "
                "above dev1 arms, variant A rests a limit at dev1 (or "
                "entry_limit_offset_ticks above it) and variant B "
                "buy-stops the reclaim, target dev2 or an R-multiple, fixed-tick stop. "
                "Overnight ticks feed the VWAP, the bars and the profile only — "
                "acceptance and the invalidations are read from RTH bar closes alone, "
                "and entries are confined to the entry window as usual."
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
                "exit_below_vah_bars counts closes ABOVE the VAL."
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
            confluences=("volume_profile",),
            run_session=engine.run_session_short,
        ),
        Strategy(
            slug="vwap-globex-lower-bounce",
            name="VWAP Globex Lower Band Bounce",
            description=(
                "The lower-band bounce read against a VWAP anchored at the Globex "
                "open (18:00 ET the previous evening) instead of the 09:30 bell, so "
                "the bands price the RTH session against the whole overnight "
                "distribution. Rules are otherwise the session short's: acceptance "
                "below dev1 arms, variant A rests a sell limit at dev1 (or "
                "entry_limit_offset_ticks below it) and variant B "
                "sell-stops the rejection, target dev2 or an R-multiple, fixed-tick "
                "stop. Overnight ticks feed the VWAP, the bars and the profile only — "
                "acceptance and the invalidations are read from RTH bar closes alone, "
                "and entries are confined to the entry window as usual."
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
            confluences=("volume_profile",),
            run_session=engine.run_session_globex_short,
            session="globex",
        ),
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
    ]
}


def get(slug: str) -> Strategy:
    if slug not in STRATEGIES:
        raise KeyError(f"unknown strategy {slug!r} (available: {sorted(STRATEGIES)})")
    return STRATEGIES[slug]
