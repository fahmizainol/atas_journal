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
from .rules import (
    DriftFadeConfig, DriftFadeGlobexConfig, EmaPullbackConfig, FadeConfig,
    GlobexBounceConfig, OrbConfig,
    ProfilePullbackConfig, SimConfig, ValueRotationConfig, WeeklyTraverseConfig,
)


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
                "a grid of trail_step_ticks measured from the entry; "
                "trail_atr_mult sets that distance from the daily ATR the "
                "session opened with instead of a fixed tick count, read once "
                "at the open so it is wider on a hot day without breathing "
                "intraday. The "
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
                "value — or, in require_mirror mode, passes only the opposite "
                "shape. ny_poc_floor requires the developing session POC "
                "within reach beneath the fill (the defended node the pullback "
                "lands on), and gx_overhang stands entries down while the "
                "Globex VWAP hangs too far over the NY VWAP — rallying into "
                "the night's average inventory."
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
            # v7: added the pyramid scale-in (pyramid_tranches / pyramid_step_ticks /
            # pyramid_stop_mode) — the position may fill in equal lots, adding each
            # as price runs in its favour, instead of all at once. pyramid_tranches=1
            # (the default) is the all-in fill and simulates identically to v6, but
            # the knobs ride the base rule path, so v6 runs are quarantined.
            # v8: added the panic exit (panic_exit_delta / panic_exit_window_s) —
            # one read of the tape at the window's end after each fill: exit at
            # market if the net aggressor delta over the window ran the
            # configured contracts against the trade. 0 (the default) never
            # reads the tape and simulates identically to v7, but the exit lives
            # on the base rule path, so v7 runs are quarantined rather than
            # trusted.
            # v9: added the big-lot participation size-up (size_up_participation /
            # size_up_contracts / biglot_min_size / biglot_window_s) — at each fill
            # the entry is sized up when the trailing big-lot participation clears
            # the threshold. 0 (the default) sizes every fill at the base contracts
            # and simulates identically to v8, but the sizing rides the base rule
            # path, so v8 runs are quarantined rather than trusted.
            # v10: added reenter_after_stop_only — any exit other than a full
            # stop-out stands the session down; skipped entries ride the exit
            # rules as "reentry_halt" ghosts and a ghost stop-out lifts the
            # halt. False (the default) simulates identically to v9, but the
            # halt lives on the base rule path, so v9 runs are quarantined
            # rather than trusted.
            # v11: added reentry_rearm_window_min — a stop's re-arm may expire
            # after a set number of minutes instead of lasting until the next
            # non-stop exit. 0 (the default) keeps the open-ended re-arm and
            # simulates identically to v10, but the clock rides the base rule
            # path, so v10 runs are quarantined rather than trusted.
            # v12: added pyramid_direction — "against" walks the scale-in grid
            # the other way (resting limits below the fill: averaging down).
            # "with" (the default) simulates identically to v11, but the signed
            # step rides the base rule path, so v11 runs are quarantined rather
            # than trusted.
            # v13: added daily_loss_exit_open — the daily loss stop may now also
            # FLATTEN the open trade (once realized net plus the position marked to
            # the current print reaches the limit), not just refuse new entries.
            # False (the default) never arms and simulates identically to v12, but
            # the exit rides the base rule path, so v12 runs are quarantined rather
            # than trusted.
            # v14: added underwater_exit_after_s — flatten the open trade once it has
            # been continuously below breakeven that long. 0 (the default) never
            # evaluates and simulates identically to v13, but it is the first per-tick
            # exit trigger on the base rule path, so v13 runs are quarantined rather
            # than trusted.
            # v15: added trail_atr_mult — the trail's distance may be set from the
            # daily ATR the session opened with (read once, at the open) instead of
            # a fixed tick count. 0 (the default) keeps the fixed distance and
            # simulates identically to v14, but it moves the stop on the base rule
            # path, so v14 runs are quarantined rather than trusted.
            version="15",
            confluences=("volume_profile", "regime", "vwap_slope", "vwap_cross",
                         "upper_occupancy", "gx_rescue", "gx_floor", "on_high",
                         "gx_value", "gx_poc_shape", "ny_poc_floor",
                         "gx_overhang", "ib_in_on", "ib_width", "wk_ext",
                         "chop", "structure_clarity"),
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
            # v8: added the pyramid scale-in — see vwap-upper-band-bounce v7. It
            # rides the shared run_session, so this family inherits it; tranches=1
            # (the default) simulates identically to v7.
            # v9: added the panic exit — see vwap-upper-band-bounce v8. Rides the
            # shared run_session; 0 (the default) simulates identically to v8.
            # v10: added the big-lot participation size-up — see
            # vwap-upper-band-bounce v9. Rides the shared run_session; 0 (the
            # default) simulates identically to v9.
            # v11: added reenter_after_stop_only — see vwap-upper-band-bounce
            # v10. Rides the shared run_session; False (the default) simulates
            # identically to v10.
            # v12: added reentry_rearm_window_min — see vwap-upper-band-bounce
            # v11. Rides the shared run_session; 0 (the default) simulates
            # identically to v11.
            # v13: added daily_loss_exit_open — see vwap-upper-band-bounce v13.
            # Rides the shared run_session; False (the default) simulates
            # identically to v12.
            # v14: added underwater_exit_after_s — see vwap-upper-band-bounce
            # v14. Rides the shared run_session; 0 (the default) simulates
            # identically to v13.
            # v15: added trail_atr_mult — see vwap-upper-band-bounce v15. Rides
            # the shared run_session; 0 (the default) simulates identically to v14.
            version="15",
            config_cls=GlobexBounceConfig,
            # vwap_slope joined for the 2026-07 regime study: the invert-on
            # long's split-half-stable bleed is confined to trend-down days,
            # and ny_vwap_slope_ppm@09:45 was the only checkpoint read whose
            # post-checkpoint veto recovered it post-hoc (bbr did not).
            # Allowed-gate additions don't touch the base rule path, so no
            # version bump and existing runs stay trusted.
            confluences=("volume_profile", "vwap_slope"),
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
                "developing Globex VAL. The regime_mirror confluence stands "
                "the strategy down from its checkpoint on days whose morning "
                "did not live below both anchored VWAPs — the long's regime "
                "gate mirrored onto this side's habitat."
            ),
            # v2: added the step trail (trail_step_ticks), which moves the stop
            # on the base rule path — v1 runs are quarantined rather than trusted.
            # v3: variant A's dev1 limit fills on the crossing back to dev1, not on
            # the standing inequality — see vwap-upper-band-bounce v4.
            # v4: added the daily loss stop (daily_loss_stop) — see
            # vwap-upper-band-bounce v5.
            # v5: added entry_limit_offset_ticks — variant A's limit may rest in
            # front of dev1 instead of on it. See vwap-upper-band-bounce v6.
            # v6: added the pyramid scale-in — see vwap-upper-band-bounce v7. It
            # rides the shared run_session (via run_session_short), so this mirror
            # inherits it; tranches=1 (the default) simulates identically to v5.
            # v7: added the panic exit — see vwap-upper-band-bounce v8. Rides the
            # shared run_session (via run_session_short); on a short the shock is
            # a buy rip, the signed mirror. 0 (the default) simulates identically
            # to v6.
            # v8: added reenter_after_stop_only — see vwap-upper-band-bounce
            # v10. Rides the shared run_session (via run_session_short); False
            # (the default) simulates identically to v7.
            # v9: added reentry_rearm_window_min — see vwap-upper-band-bounce
            # v11. Rides the shared run_session (via run_session_short); 0 (the
            # default) simulates identically to v8.
            # v10: added daily_loss_exit_open — see vwap-upper-band-bounce v13.
            # Rides the shared run_session (via run_session_short); False (the
            # default) simulates identically to v9.
            # v11: added underwater_exit_after_s — see vwap-upper-band-bounce
            # v14. Rides the shared run_session (via run_session_short); on a
            # short "underwater" is the rip against the position, the signed
            # mirror. 0 (the default) simulates identically to v10.
            # v12: added trail_atr_mult — see vwap-upper-band-bounce v15. Rides
            # the shared run_session (via run_session_short); 0 (the default)
            # simulates identically to v11.
            version="12",
            confluences=("volume_profile", "on_high", "gx_value",
                         "regime_mirror"),
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
                "the fade would be selling into. vwap_flat is the balance-day "
                "gate — the caps' premise stated directly: it stands the fade "
                "down from its checkpoint unless the NY VWAP grade there is "
                "under its threshold in BOTH directions, keeping only the days "
                "that haven't picked a side."
            ),
            # v2: added arm_stretch_side — the arming stretch may run INSIDE dev1
            # (the band broken and retested) instead of beyond it. "beyond" is v1's
            # rule exactly, but the knob is part of the config hash either way.
            # v3: added daily_loss_exit_open — the daily loss stop may now also
            # flatten the open trade, not just refuse new entries. False (the
            # default) simulates identically to v2, but the exit rides the base
            # rule path, so v2 runs are quarantined rather than trusted.
            version="3",
            config_cls=FadeConfig,
            confluences=("volume_profile", "vwap_slope_cap", "vwap_flat",
                         "upper_occupancy_cap", "gx_rescue_cap", "ib_in_on",
                         "ib_width"),
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
                "above it — the ceiling this fade would be buying into. "
                "vwap_flat needs no mirror at all: it stands the fade down "
                "unless the grade is under its threshold in BOTH directions, "
                "and flat is flat on either side of the market."
            ),
            # v2: arm_stretch_side, with the short — see vwap-dev1-fade-short.
            # v3: added daily_loss_exit_open, with the short — see
            # vwap-dev1-fade-short v3.
            version="3",
            config_cls=FadeConfig,
            confluences=("volume_profile", "vwap_slope_cap", "vwap_flat",
                         "upper_occupancy_cap", "gx_rescue_cap", "ib_in_on",
                         "ib_width"),
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
            # v5: added daily_loss_exit_open — the daily loss stop may now also
            # flatten the open trade, not just refuse new entries. False (the
            # default) simulates identically to v4, but the exit rides the base
            # rule path, so v4 runs are quarantined rather than trusted.
            version="5",
            config_cls=ProfilePullbackConfig,
            run_session=engine.run_session_profile_pullback,
            # Globex session: the overnight segment feeds the Globex developing
            # profile (and the charts); the NY bands and NY profile are still
            # anchored at the bell, and trading lives in RTH only.
            session="globex",
        ),
        Strategy(
            slug="ema-pullback-long",
            name="EMA 9/20 Pullback (Upper Band)",
            description=(
                "Long the pullback-from-above onto any enabled 1-minute EMA "
                "(use_ema9/use_ema20/use_ema50/use_ema200 — each enabled line is "
                "its own candidate level) while the EMA sits in the configured "
                "region of the NY VWAP upper channel, so the line and the fill "
                "on it are both inside the bands. The EMAs are the repo's "
                "charted lines: 1-minute bars over the overnight+RTH stream, "
                "ewm(adjust=False), the same lines the chart draws — the engine "
                "trades what you see. It is the profile pullback with EMAs "
                "standing in for the developing profile levels: no acceptance "
                "candle, no arming stretch — the EMA in force is the setup. Two "
                "entries share the pullback classification: variant A rests a "
                "limit on each enabled EMA and fills on the crossing back down to "
                "it (price must first have cleared the line by rearm_ticks from "
                "above; the fill is the transition to at-or-through, never the "
                "standing inequality — a line rising up to meet a flat market "
                "disarms rather than fills); variant B waits for the first bar to "
                "close confirm_ticks back above the EMA after a touch — the bounce "
                "confirmed — and enters at market. Exits are a fixed-tick stop and "
                "an R-multiple or fixed-tick target, with the bounce's optional "
                "trail. band_region is the band condition — 'channel' (inside "
                "dev1..dev2), 'above_dev1' or 'above_dev2' (open the fill into the "
                "overextended zone above the far band), or 'off'. require_stacked "
                "demands the 9 sit at or above the 20 (the stacked-bull context — "
                "the 20 below the 9); min_ema_gap_ticks skips fills where the two "
                "lines have converged (a squeeze reads as chop the pullback has no "
                "trend to lean on); and min_band_width_ticks skips a pinched "
                "channel. open_stack_veto is the session-level regime gate from "
                "the 20/50/200 study: the 1-minute 20/50/200 EMA ordering at the "
                "close of the 09:35 bar classifies the day, and a vetoed day "
                "(bear-stacked open, or anything-but-bull under 'not_bull') takes "
                "no trades at all — whole-day on/off, no intraday re-arm chain "
                "perturbation."
            ),
            # v2: added open_stack_veto — the ema-20-50-200-behavior study's one
            # survivor (bear-stacked 09:35 open = the v1 run's loss engine,
            # −$15.4k PF 0.68 vs bull +$23.1k PF 1.91 on bc875e6a). "off" (the
            # default) takes the identical code path to v1 — the veto block and
            # the causality floor are only entered when the knob is set — so v1
            # runs remain comparable rather than quarantined.
            # v3: added use_ema50/use_ema200 — the 50/200 join the 9/20 as
            # independent candidate levels (same in-force mapping, same fill
            # discipline). Both default False, adding no code on the traded
            # path, so v2 (and v1) runs remain comparable.
            version="3",
            config_cls=EmaPullbackConfig,
            run_session=engine.run_session_ema_pullback,
            # Globex session: the overnight warms the 9/20 EMA exactly as the
            # chart overlay warms, so the traded line matches the drawn one. The
            # NY VWAP bands anchor at the bell; trading lives in RTH only.
            session="globex",
        ),
        Strategy(
            slug="value-rotation",
            name="Value Rotation",
            description=(
                "The rotation the loss studies kept finding from the other "
                "side, traded on its own: price accepted OUTSIDE the "
                "developing value area (an excursion more than "
                "arm_beyond_ticks past the edge — the VAH on the short, the "
                "VAL on the long), then re-accepted back inside by "
                "accept_inside_bars consecutive bar closes — the edge has "
                "failed — and the trade runs with the rotation toward the "
                "developing POC. 85% of the upper-band bounce's stopped "
                "losses completed exactly this rotation within 30 minutes. "
                "Entry variant A rests a limit at the failed edge and fills "
                "on the retest — only when price does the crossing; an edge "
                "that relocates across the market disarms instead of filling "
                "(profile-pullback's v3 lesson). Variant B stops into the "
                "rotation, entry_stop_offset_ticks past the edge into value. "
                "The target tracks the developing POC live (or the NY VWAP, "
                "or an R-multiple); a POC that node-flips across price books "
                "a market fill at the print, never a limit fill at a level "
                "the market wasn't at. min_room_ticks is the trivial-rotation "
                "guard — the POC must sit that far beyond the fill or the "
                "touch is missed; ~40% of the Interaction Lab's POC "
                "reversions were price already at or through the target. "
                "invalidate_outside_bars exits at market when price is "
                "re-accepted back outside the edge — the premise run "
                "backwards — with the fixed stop as the hard backstop, and "
                "nothing arms while the NY profile is younger than "
                "level_warmup_min. The vwap_flat confluence stands the "
                "strategy down from its checkpoint on days that have "
                "established a VWAP grade in either direction — the "
                "balance-day read this rotation presumed, which its first "
                "A/B refuted (see vwap_flat's honesty clause) — "
                "vwap_slope_cap only against the grade that fights the "
                "trade's own direction, and vwap_slope demands the upward "
                "grade the repo's every surviving edge leans on: on the "
                "long, the rotation is then a dip below value bought on an "
                "up-graded tape."
            ),
            # v2: added daily_loss_exit_open — the daily loss stop may now also
            # flatten the open trade, not just refuse new entries. False (the
            # default) simulates identically to v1, but the exit rides the base
            # rule path, so v1 runs are quarantined rather than trusted.
            version="2",
            config_cls=ValueRotationConfig,
            confluences=("vwap_flat", "vwap_slope_cap", "vwap_slope",
                         "ib_in_on", "ib_width"),
            run_session=engine.run_session_value_rotation,
        ),
        Strategy(
            slug="orb-breakout",
            name="Opening Range Breakout",
            description=(
                "One initiative trade off the session's opening window — the "
                "first window_minutes from the bell — per the IB/ORB research "
                "(docs/research/initial-balance-orb.md) and the Lab's IB "
                "study. Three entry modes: 'candle' is the Zarattini rule — at "
                "the window's close, enter in the window candle's direction at "
                "the first print; 'break' stops in on the first crossing of "
                "the window's high/low (+ entry_offset_ticks, Crabel's "
                "stretch); 'second_break' waits for one extreme to break and "
                "enters with the SECOND — on double-break days the close "
                "landed on the second break's side 81% of the time (IB study, "
                "n=53). One attempt per session, filled, vetoed or refused — "
                "there is no re-arm. stop_mode 'range' puts the stop at the "
                "window's opposite extreme (the paper's rule; the R-multiple "
                "is measured against the risk actually taken, which varies "
                "with the window), 'ticks' fixes it. The target is end-of-day "
                "(the paper's exit) or an R-multiple, with the bounce's "
                "optional trail. min/max_range_ticks skip the noise-floor and "
                "exhausted-tail windows. The Lab read of the candle entry "
                "WITHOUT the stop was mean R ~0 — the stop's asymmetry is the "
                "claim being tested, not a detail. ib_in_on can stand the "
                "breakout down on IB-inside-overnight days (they lean "
                "rotational), ib_width on out-of-bounds IB widths, and "
                "vwap_flat on days that established a grade."
            ),
            version="1",
            config_cls=OrbConfig,
            confluences=("ib_in_on", "ib_width", "vwap_flat"),
            run_session=engine.run_session_orb,
        ),
        Strategy(
            slug="drift-touch-fade",
            name="Drift-Touch Fade",
            description=(
                "Fade a level that price drifted into rather than approached. A "
                "drift touch is contact where, over the trailing "
                "GAP_LOOKBACK_BARS bars, price's net move toward the level plus "
                "the level's net move toward price is <= 0 (profile.gap_closer) "
                "— price was already loitering by the level and wiggled into "
                "contact, a slow re-test of a hugged zone. A fast approach is a "
                "momentum test with no edge; a drift touch means the level has "
                "already absorbed minutes of adjacent trade without breaking, so "
                "contact without impulse has nothing to carry it through. The "
                "candidate levels are developing NY and Globex POC/VAH/VAL and "
                "the static session references (ONH/ONL, the prior day's "
                "POC/VAH/VAL and close, the session open), per the config's "
                "sources. A bar touches a level within touch_tol; a re-approach "
                "is a fresh touch only after touch_gap_bars clear of the zone, "
                "and min_level_stability_min skips a drift signal on a "
                "developing level that only just relocated to its price. The "
                "fade side is which side price hugged: above the level (support) "
                "goes long, below it (resistance) short, filtered by side. Entry "
                "variant A takes a market order on the drift-touch bar's close "
                "(filled the next tick); variant B waits for a bar to close "
                "confirm_ticks beyond the touch bar's extreme on the fade side "
                "first. The stop sits stop_ticks behind the zone, measured from "
                "the LEVEL not the fill; the target fades toward value — the NY "
                "VWAP (tracked live, with the crossing discipline and the "
                "min_room_ticks trivial-rotation guard), a fixed R multiple, or "
                "a fixed distance. max_touches_per_zone caps fills to each "
                "zone's first N touches. One position at a time; simultaneous "
                "and while-in-trade drift touches ride the exit rules as "
                "in_trade ghosts in the missed rows. approach_mom_veto_min "
                "skips with-move touches (net move in the trade's direction "
                "over the trailing window positive at the fill); its A/B "
                "failed — most drift touches arrive with-move and the vetoed "
                "cohort finishes positive — so it ships off."
            ),
            version="2",
            config_cls=DriftFadeConfig,
            run_session=engine.run_session_drift_fade,
            # Globex session: the overnight feeds the Globex developing profile
            # and the ONH/ONL refs; the NY VWAP and NY profile anchor at the
            # bell, and trading lives in RTH only.
            session="globex",
            # Gates are supported single-sided only (the schema refuses a gate on
            # side="both"): each reads one signed session context. The idea must
            # first stand alone, so none is on by default.
            confluences=("regime", "vwap_slope", "vwap_slope_cap", "ib_in_on",
                         "ib_width", "wk_ext", "chop", "structure_clarity"),
        ),
        Strategy(
            slug="drift-touch-fade-entry-stop",
            name="Drift-Touch Fade (Entry Stop)",
            description=(
                "The drift-touch fade with the stop measured from the FILL, not "
                "the level. Same detection and entries as drift-touch-fade: a "
                "drift touch is contact where, over the trailing "
                "GAP_LOOKBACK_BARS bars, price's net move toward the level plus "
                "the level's net move toward price is <= 0 (profile.gap_closer) "
                "— price was already loitering by the level and wiggled into "
                "contact. The level-stop original parks stop_ticks behind the "
                "ZONE, so the risk actually taken varies with how far the "
                "variant-B confirming close landed from the level (median ~2x "
                "stop_ticks, range 1.3-8x in the Jul 2026 sweep); this variant "
                "anchors the stop to the entry print instead — every trade "
                "risks exactly stop_ticks, and the zone itself is allowed to "
                "fail without ending the trade. The premise difference is the "
                "invalidation: the original says 'the zone failing kills the "
                "idea'; this one says 'adverse excursion from my price kills "
                "it'. Everything else — sources, warmup, touch debounce, "
                "stability guard, entry variants, targets, trail, daily "
                "governor, the approach-momentum veto — reads identically to "
                "drift-touch-fade."
            ),
            version="2",
            config_cls=DriftFadeConfig,
            run_session=engine.run_session_drift_fade_entry_stop,
            # Same data needs as the original: overnight feeds the Globex
            # profile and ONH/ONL; trading lives in RTH only.
            session="globex",
            # Same single-sided-only gate support as the original.
            confluences=("regime", "vwap_slope", "vwap_slope_cap", "ib_in_on",
                         "ib_width", "wk_ext", "chop", "structure_clarity"),
        ),
        Strategy(
            slug="drift-touch-fade-globex",
            name="Drift-Touch Fade (Globex Session)",
            description=(
                "The entry-stop drift-touch fade run over the WHOLE Globex "
                "session, from the 18:00 ET open, instead of RTH alone. The "
                "event and the trade are the entry-stop sibling's: a drift "
                "touch is contact where, over the trailing GAP_LOOKBACK_BARS "
                "bars, price's net move toward the level plus the level's net "
                "move toward price is <= 0 (profile.gap_closer) — price was "
                "already loitering by the level and wiggled into contact — and "
                "the fade enters away from it with stop_ticks measured from the "
                "FILL, target toward value. What differs is when it may fire, "
                "which is the whole idea: the RTH siblings confine every signal "
                "to bars closing at or after 09:30 and use the overnight as "
                "indicator input only. Three readings had to change for the "
                "night to be tradeable rather than merely visible. The target "
                "is the Globex-anchored VWAP mid (gx_vwap), the session's own "
                "value line — there is no NY VWAP at 21:00, and the RTH engine "
                "drops any signal whose target is absent, so lifting the window "
                "alone would have traded nothing. ONH and ONL develop — the "
                "night's high and low SO FAR, settling at the bell — because "
                "the RTH path's finished-night constant is only free of "
                "lookahead if you never trade before the bell. And the Globex "
                "profile gets its own warm-up: mature by 09:30, but degenerate "
                "at 18:05, exactly the objection level_warmup_min raises for NY "
                "value. The NY levels and the session open need no special "
                "handling — they are absent before the bell either way, so they "
                "simply switch on partway through the session. Trades are NOT "
                "flattened at the bell: an overnight fill runs to its stop, its "
                "target or flat_by, and since the engine holds one position it "
                "can block the morning's signals behind it as in_trade ghosts, "
                "which the vetoed rows record. No confluence gates — every gate "
                "this family supports anchors to an RTH checkpoint or the NY "
                "value edge, which an overnight fill has no reading of."
            ),
            version="1",
            config_cls=DriftFadeGlobexConfig,
            run_session=engine.run_session_drift_fade_globex,
            # Globex session: the overnight is traded, not just read.
            session="globex",
        ),
        Strategy(
            slug="weekly-lower1-deep-traverse-long",
            name="Weekly −1σ Deep-Traverse Long",
            description=(
                "Buy the session leg that ran from the weekly mid (or higher) "
                "all the way down into the weekly −1σ band — promoted from the "
                "weekly-lower1-deep-traverse-long draft, the strongest cell of "
                "the weekly-band touch-context study and the only one that "
                "survived the next-bar race correction. The event: a 1-minute "
                "bar whose range spans the developing weekly −1σ, approached "
                "from above, with strictly fewer than max_res_below_min prior "
                "1-min closes below the band this session (no prior residence "
                "— a day living under the band is a breakdown, not a "
                "traverse), and a σ-position that reached min_origin_sigma "
                "inside the trailing origin lookback (the leg started at the "
                "mid or better). A touched band only re-arms after a full bar "
                "trades clear by rearm_sigma weekly sigmas, so a choppy hour "
                "hugging it is one touch. Entry is a market order on the tick "
                "after the signal bar closes (the draft's next-bar-open, at "
                "tick resolution); a fill already at or past either race "
                "threshold is skipped — the race was decided before the trade "
                "existed. Exits are the study's race made tradeable: the stop "
                "a σ-fraction below the level (or a fixed distance below the "
                "fill), the target a σ-fraction above it (or the weekly mid "
                "tracked live, or an R multiple), and a max-hold flatten at "
                "the study's outcome horizon. Long only by construction: the "
                "mirror cell (upper1 deep traverse) is REVERSED, so there is "
                "no side knob. The week's first session and any session "
                "without an honest weekly line (a hole in the week, no cached "
                "overnight) take no trades — absent, not approximated. "
                "Signals while a position is open ride the exit rules as "
                "in_trade ghosts in the missed rows. trail_stop_ticks turns "
                "the fixed stop into the bounce's ratchet (first click at "
                "trail_breakeven_ticks past the entry, stepping on "
                "trail_step_ticks; the R-multiple stays measured against the "
                "initial stop), and daily_loss_stop is the bounce's session "
                "governor — no new entries once the session's realized net "
                "hits the limit, and with daily_loss_exit_open the open trade "
                "is flattened against it too."
            ),
            # v2: added the step trail (trail_stop_ticks family) and the daily
            # loss stop (daily_loss_stop / daily_loss_exit_open). All off by
            # default and a config that leaves them off simulates identically
            # to v1 — but the trail moves the stop and the halt refuses fills
            # on the base rule path, so v1 runs are quarantined rather than
            # trusted.
            # v3: added underwater_exit_after_s — flatten the open trade once it
            # has been continuously below breakeven that long. 0 (the default)
            # never evaluates and simulates identically to v2, but the exit rides
            # the base rule path, so v2 runs are quarantined rather than trusted.
            version="3",
            config_cls=WeeklyTraverseConfig,
            run_session=engine.run_session_weekly_traverse,
            # Globex session: the overnight feeds the weekly anchor and the
            # study's frame is the whole session — entries may fill overnight
            # (rth_only confines them), which no other strategy does yet.
            session="globex",
        ),
    ]
}


def get(slug: str) -> Strategy:
    if slug not in STRATEGIES:
        raise KeyError(f"unknown strategy {slug!r} (available: {sorted(STRATEGIES)})")
    return STRATEGIES[slug]
