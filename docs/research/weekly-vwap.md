# Weekly anchored VWAP — interaction study & loser filter

- **Date:** 2026-07-19
- **Research question:** Does a weekly-anchored VWAP (anchor = the week's first Globex open, Sunday 18:00 ET) carry information the daily anchors don't — as a regime feature, as fade/bounce levels, and specifically as a filter for the upper-band-bounce losers (which are regime, not geometry)?
- **Data:** NQ tick cache 2025-02-03 → 2026-06-30 (367 sessions; 363 ran, 283 "seasoned" = not the week's first session). Trade comparison against the v10 baseline `20250201-20260630-v10-0ae01934` (398 trades, net $122.5k).
- **History note:** Backlog item 2 in `lab-backlog.md`. Study module `src/journal/sim/weekly_vwap.py`, served at `/interactions/weekly-vwap`, UI panel in the Interactions Lab.

> **⚠ STATUS (2026-07-22): results pending re-run — data gap.** The weekly line here was computed on a tick cache missing the live **16:00–17:00 ET hour every weekday** (see §1 "Known gap"). The seed compounds that hole across the week (~4 hrs by Friday), so every level, band and σ below is mis-stated. Originally judged "slight"; a 2026-07-22 ATAS cross-check disproved that (on the pdl anchor it moved lower-dev2 by 16–21 pts, flipping an inside-band reading to a dev2 tag). The `wk_ext` A/B *failed* — knob left off, conservative — so the standing risk is a **false negative** (a real filter discarded on wrong numbers). **Re-run once the hour is bought before trusting any figure below.**

## TL;DR

The weekly anchor is built, drawn on every chart, and studied. Three findings:

1. **The weekly envelope's edges mean something; its middle doesn't.** Sessions opening beyond +2σ of the weekly VWAP revert hard (median −56 pts, with-side rate 37.5%, n=24). Everything between ±2σ is drift-with-the-tape (this sample: up).
2. **±1σ touches lean fade, ±2σ do not.** First touches of weekly ±1σ from the mid's side revert more than they continue (+13 to +17 pts median edge, ~1/3 reach the weekly mid within 60m). Lower −2σ touches *break through* (−23.5 pts edge) — do not fade the weekly 2σ bands.
3. **Weekly extension separates upper-band-bounce losers — the first geometry that does.** Entries taken above weekly mid +2σ: 48 trades, net **−$14.5k**, avg R −0.115, 40% stop rate. The other 350 trades: +$137k, PF 1.61. Negative in 5 of 6 quarters and in both sample halves; not outlier-driven. **Candidate gate, NOT adopted** — it must pass an engine A/B first, and 7 of the last 8 in-sample leads on this run failed theirs.

## 1. The anchor (implementation)

- `weekly.py`: each prior session collapses to (Σv, Σpv, Σp²v) cached beside the tick parquets (`*_sums.json`, keyed by which segments were summed); `vwap_bands(ticks, seed=...)` accumulates today's ticks on top. Algebraically identical to concatenating the week's ticks.
- Honesty rules, inherited from the Globex anchor: a week with a hole (a prior session whose ticks aren't cached) or a session without its overnight is **absent, not approximated**; a contract roll restarts the anchor at the roll session (never averaged across the seam). The week's first session has a zero seed — its weekly line coincides with its Globex line, which is what a weekly anchor genuinely looks like on a Monday.
- **Known gap (ESCALATED 2026-07-22):** the tick cache covers 18:00→16:00 ET, so each completed day's live 16:00–17:00 hour is not in the seed, and the seed *compounds* that hole across the week (~4 hrs by Friday). Originally judged "slight (lowest-volume hour)"; a 2026-07-22 ATAS cross-check disproved that — on the pdl anchor the missing hour moved lower-dev2 by 16–21 pts and flipped an inside-band reading into a dev2 tag. **Material for every cross-session anchor.** Cost to buy it retroactively is now scoped (~$4 for the whole cache, 16:00–17:00 only); study to be re-run after.
- Drawn on every sim/Lab chart as a third band (orange), own legend toggle, context-only (`vwap_anchor` never says "weekly").

## 2. Session-level study (283 seasoned sessions, 60m outcome window)

Open position in the weekly envelope vs how the day went:

| Open at bell | n | med drift (pts) | with-side rate |
|---|---|---|---|
| < −2σ | 16 | −5.9 | 0.50 |
| −2σ…−1σ | 40 | +33.0 | 0.45 |
| −1σ…0 | 57 | +40.0 | 0.47 |
| 0…+1σ | 63 | +29.8 | 0.52 |
| +1σ…+2σ | 83 | +34.3 | 0.59 |
| **> +2σ** | **24** | **−56.5** | **0.375** |

The interior buckets are the 2025–26 uptrend wearing different hats. The +2σ tail is the exception: extended-above-weekly opens gave it back. (The −2σ tail is flat, n=16 — no symmetric long edge.)

First touches of the weekly bands (approached from the mid's side):

| Band | n | hit-mid ≤60m | med toward (pts) | med beyond (pts) | med edge |
|---|---|---|---|---|---|
| upper2 | 71 | 7% | 47.1 | 39.7 | +1.7 |
| upper1 | 80 | 31% | 67.8 | 54.2 | +13.0 |
| lower1 | 90 | 36% | 84.4 | 58.2 | +16.9 |
| **lower2** | **69** | **6%** | 69.5 | 105.3 | **−23.5** |

Touch rates: mid 52%, ±1σ ~40–45%, ±2σ ~26–27% of eligible sessions; ±1σ touches come early (median ~25m after the open), ±2σ near the IB end (~50m). Weekday cuts show nothing (with-side 0.46–0.54 across Tue–Fri).

Read: the weekly ±1σ behaves like a value-area edge (rotational), the weekly −2σ like a breakdown level (directional). Consistent with the house finding that NQ edges are day-with, not responsive — except at the weekly +2σ extreme, where reversion finally shows up.

## 3. Upper-band-bounce comparison (v10 baseline, 398 trades)

Weekly σ-distance of the **entry price** at entry time:

| Entry vs weekly | n | net | win | avg R | stop rate |
|---|---|---|---|---|---|
| −2..−1σ | 9 | −$4.8k | 0.44 | −0.22 | 0.56 |
| −1..0σ | 48 | +$7.3k | 0.75 | +0.09 | 0.23 |
| 0..+1σ | 78 | +$30.9k | 0.71 | +0.20 | 0.27 |
| +1..+2σ | 215 | +$103.5k | 0.70 | +0.23 | 0.28 |
| **> +2σ** | **48** | **−$14.5k** | **0.58** | **−0.12** | **0.40** |

The strategy's natural habitat is +1..+2σ (it buys upper-band pullbacks on up days, which live there). Beyond +2σ the same setup is net-negative. Session-open >+2σ is the same signal a bit weaker (30 trades, −$12.5k, 27 of 30 overlap the entry cut).

Robustness of `entry ≤ +2σ` (drop 48, keep 350):
- Keep-set: net $137.0k (+12%), PF 1.61 vs 1.4x, win 70.3%, avg R +0.19.
- Dropped bucket negative in 5 of 6 quarters (2025Q3 +$2.4k the exception; 2026Q2 the worst at −$9.1k/17 trades) and in both sample halves.
- Not tail-driven: bucket median net ≈ $0, worst-3 removed still −$7.5k — it loses by stop-rate, not by one bad print.
- **Max drawdown does not improve** (baseline $13.5k → $14.6k with the entry cut): some >2σ winners were cushioning a drawdown. This is a net/PF lead, not a DD lead.
- Mondays (zero seed, weekly ≡ Globex) are the run's *best* subset (win 74%, avg R +0.27) — the gate must not fire on the week's first session, where "weekly extension" isn't measurable and the trades are good.

Mechanism reads sensible, which is why this survives the prior that geometry doesn't filter this run's losers: the loss study said losses are *regime*; weekly +2σ is a slow regime measure (a week of volume says price is stretched), not a same-day geometry knob.

## 4. Engine A/B (2026-07-19): FAIL — leave the knob off

`wk_ext` gate built (`gates.WkExtGate`: veto fills beyond weekly mid + max_sigma·σ_w on the setup's side; inert on the week's first session, blind-vetoes on a hole; cache-only, RTH-frame only). A/B at engine v12, same window, baseline config + `{"wk_ext": {"enabled": true, "max_sigma": 2.0}}`:

| | baseline `v12-a0512f69` | wk_ext `v12-07f1531f` |
|---|---|---|
| trades | 222 | 203 |
| net | $124,508 | $119,888 (**−3.7%**) |
| PF | 1.98 | 2.06 (+4%) |
| win rate | 73.0% | 73.9% |
| maxDD | $12,350 | $11,350 (−8%) |
| Sharpe | 2.68 | 2.64 (worse) |
| Sortino | 7.99 | 7.60 (worse) |
| expectancy / R̄ | $561 / 0.268 | $591 / 0.281 |

The adopted bar (set by the reenter knob: net, PF, DD, Sharpe **all** better) is not met — net and Sharpe/Sortino are worse. Halves disagree (H1 +$1.1k, H2 −$5.8k). The instructive detail: the gate's own 68 ghosts were genuinely bad (−$10.2k standalone), yet the run still netted **less** — vetoing them broke re-arm chains that produced later winners (reentry_halt vetoes fell 359→339), the same path dependence the first-touch study documented. And the post-hoc read explains why the v10 lead didn't carry: `reenter_after_stop_only` had already harvested most of the >+2σ pocket (on v12 that bucket is +$2.5k/26 trades post-hoc, not −$14.5k/48).

**Disposition: knob ships, stays off** — the 8th A/B fail against 1 pass. The weekly anchor itself (charts, Lab study, regime read) stands.

## 5. Backlog disposition

- [x] Weekly anchor in computation + charts (uniform layer, orange, honest-absence rules).
- [x] Lab study built (`/interactions/weekly-vwap`, panel in the Interactions Lab) and run on the full cache.
- **Lead → engine A/B next:** a `wk_ext_max_sigma` entry gate (veto when entry price > weekly mid + 2σ_w; inert on the week's first session). Prior is guarded — sized-up/panic-exit/depth leads all reversed OOS — but this one has an independent session-level confirmation and a 5-of-6-quarter sign. Judge on net, PF, DD, Sharpe like the reenter knob.
- Not pursued: fading weekly ±2σ as its own signal (lower2 breaks through; upper2 edge ≈ 0), weekly-band bounce entries (±1σ edge is ~15 pts against ~60-pt excursions — thin), weekday conditioning (nothing).
