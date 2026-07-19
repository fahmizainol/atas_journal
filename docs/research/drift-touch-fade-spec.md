# Drift-Touch Fade — strategy spec

- **Date:** 2026-07-19
- **Status:** BUILT (v1, 2026-07-19) — registered as `drift-touch-fade` (`DriftFadeConfig`, `engine.run_session_drift_fade`, session `globex`). Run the baseline A/B ladder in §7 next. Two v1 boundary notes: the prior-day `pd *` refs use honest-absence off the read-only tick cache (present for every session but a window's first, which has no predecessor cached — no cross-session runner plumbing); and the §6 confluence gates are wired **single-sided only** — enabling one requires `side` = `long`/`short`, since a gate reads one signed session context (the schema refuses a gate on `side="both"`).
- **Research basis:** `interactions-v9-findings.md` §1 (the gap-closer cut) plus the 2026-07-19 monthly-robustness pass (`strategy-scouting-2026-07.md` §1). Drift touches are the first Lab lead to survive a full monthly pass: MFE/MAE ratio above null in **17/17 months** (≥1.2 in 15), reject rate above null in 17/17, no thin months, top-20 sessions carry only 39.5% of the edge, and the circularity control passes — zones with zero same-day prior rejects still show ratio 1.65 vs 1.11 for non-drift touches. Drift ∩ 1st-touch holds at 0.698 reject / 1.73 ratio.

---

## 1. The idea

A **drift touch** is contact with a level that *neither side approached*: over the trailing `GAP_LOOKBACK_BARS = 5` one-minute bars, price's net movement toward the level plus the level's net movement toward price is ≤ 0 (`interactions._gap_closer`). Price was already loitering inside the zone's tolerance and wiggled into contact — a slow re-test of a **hugged** zone. The median drift touch has price moving 3.5 pts *away* from the level over the window and touching anyway.

Why it should work: a fast approach is a momentum test (ratio ≈ null 0.99–1.10); a drift touch means the level has already absorbed several minutes of adjacent trade without breaking. Contact without impulse has nothing to carry it through. Measured: reject ~0.70 vs null 0.605, med MFE/MAE 47/26 at 30m.

The trade: **fade the level on a drift contact** — enter away from the level, structural stop behind the zone, target toward value.

Failure mode to respect: ~30% of the time the hug is **pre-breakout coiling**, not absorption (e.g. 2025-08-22 10:00, pd VAH from below — accepted, 316 pts through). The stop is the rule; no structural invalidation exists that distinguishes the two in advance.

## 2. Registry entry (proposed)

Per the registry's own rule — a new idea is a new registry entry, never a flag on an existing one:

- **slug:** `drift-touch-fade`
- **config_cls:** new `DriftFadeConfig`
- **run_session:** new `engine.run_session_drift_fade`
- **session:** `"globex"` (the Globex developing profile and session refs need the overnight segment, exactly as `profile-pullback-long` does); trading is RTH-only.
- **version:** "1"

## 3. Detection (engine translation of the Lab event)

The engine must reproduce the Lab's event live, on its own bars, using only past data:

1. **Candidate levels** (config `sources`, default all three, matching the studied population): developing NY POC/VAH/VAL, developing Globex POC/VAH/VAL, and the static session refs (ONH/ONL, pd POC/VAH/VAL, pd Close, Open). NY levels honor `level_warmup_min` (default 15, the Lab's `LEVEL_WARMUP_MIN`) — younger than that, POC/VAH/VAL all sit on the open print.
2. **Touch:** bar `low − touch_tol ≤ level ≤ high + touch_tol` with `touch_tol` default 2.0 pts (`TOUCH_TOL_PTS`). Re-approach counts as a fresh touch only after `touch_gap_bars` (default 3) bars clear of the zone — one rotation sitting on a level is one touch.
3. **Drift classification at the touch bar:** with `j = i − min(5, i)` and `toward = sign(level[j] − close[j])`: `price_closed = (close[i] − close[j]) · toward`, `level_closed = −(level[i] − level[j]) · toward`; the touch is drift iff `price_closed + level_closed ≤ 0`. Same arithmetic as `_gap_closer` — lift it into a shared helper rather than duplicating (the chart/study/engine agreement rule that `ib.chart_overlay` set).
4. **Level identity across relocation:** a developing level that node-flips to a new price is a *new* zone for touch-counting (profile-pullback v4 lesson — a relocated level is the profile chasing the market, not a level anyone defended). `min_level_stability_min` (default 5) skips drift signals on a level that relocated more than `stability_tol` within that window; a drift touch on a freshly-teleported level is a detection artifact, not a hug.

## 4. Entry

Drift contact cannot be traded with a resting limit — a limit at the level fills the price-led approaches, which are exactly the dead class (ratio 1.10 ≈ null). Entry is signal-then-order:

- **Variant A (default):** market order on the close of the drift-touch bar, direction = away from the level. The approach side defines it: price hugging **above** the level and drifting down into contact → fade = **long** (the level holds as support); price hugging **below** and drifting up into contact → fade = **short** (the level holds as resistance).
- **Variant B (confirmation):** wait for the first bar close at least `confirm_ticks` beyond the touch bar's extreme on the fade side, enter on a stop there. Later but filters the instant-acceptance failures; the profile-pullback dwell lesson (any added waiting inverted that edge) sets the prior that A beats B — build both, measure.
- `side` knob: `"long" | "short" | "both"` (default `"both"`). Drift is the repo's first near-symmetric edge (above-approach 47/24, below-approach 46/27) — but the house prior is day-with/long-only, so the A/B must read sides separately before `"both"` ships as default in a baseline.
- `entry_window` default 09:45–15:00 ET (the drop-15:xx rule survives the full window in the v9 read; 09:30–09:45 excluded by the flagship's pre-checkpoint leak lesson, revisit with a knob).
- `max_touches_per_zone` (default 0 = unlimited): acceptance decay says MFE shrinks 47→27 pts by the 7th touch, but the drift ratio held on re-tests as well as 1st touches — start unlimited, measure the nth-touch cut in the run's edges panel before restricting.
- One position at a time; skipped signals become `missed.parquet` ghosts (in-trade) as everywhere else.

## 5. Exits

- **Stop:** fixed ticks behind the zone's far side — `stop_ticks` measured from the *level*, not the fill (the zone, not the entry print, is the invalidation). Default sweep range 120–200 ticks (30–50 pts): median MAE on drift rejections is 26 pts, p75 ≈ 45.
- **Target:** `target_mode` ∈ `{r_multiple, ny_vwap, fixed_ticks}`. The natural fade-to-value target is the NY VWAP (a POC-magnet cousin); target tracking a moving reference follows value-rotation's rule — a reference that node-flips across price books a market fill at the print, never a limit at a level the market wasn't at. `min_room_ticks` trivial-rotation guard applies when targeting VWAP (skip signals where VWAP is already inside the stop distance).
- Optional trail: reuse the bounce family's `trail_stop_ticks`/`trail_step_ticks` semantics verbatim.
- EoD flat at 16:00; `daily_loss_stop` + `daily_loss_exit_open` from v13 conventions from day one.

## 6. Confluences to wire (support, all default off)

`regime`, `vwap_slope` (long side), `vwap_slope_cap`, `ib_in_on`, `ib_width`, `wk_ext`, `chop`, `structure_clarity`. No new gates in v1 — the idea must first stand alone. The known confound to watch: **drift may partly proxy trend context** (the v9 doc's circularity caveat). The regime gate is the clean instrument for that question: if drift's edge vanishes under the regime split, it was a trend proxy all along; if it survives within-regime, it's its own thing.

## 7. A/B and acceptance plan

1. **Baseline run:** full 17-month window (2025-02-01 → 2026-06-30), defaults above, sides split (`side=long` and `side=short` runs, then `both`). No gates.
2. **The bar** (house rule since wk_ext): a config change is adopted only if net, PF, maxDD and Sharpe *all* improve; a new strategy baseline is *interesting* if net > 0 with PF ≥ ~1.3 after commissions on ≥100 trades.
3. **Robustness ladder** (from `data/research/gate-robustness/`): split-half at 2025-10-15, monthly sign counts, top-N-trade concentration (the 30badf94 tail lesson — report top-20 trades as % of net), and the regime split from §6.
4. **Known translation risk:** 30m touch scores ≠ engine PnL (Globex levels scored best in v3 and lost money traded). The excursion medians (47 favorable / 26 adverse) price a ~1.5:1 structure before costs — thin enough that the stop/target geometry, not the signal, may decide the outcome. Sweep stops before concluding the signal is dead.
5. **Expected cannibalization check:** drift touches on the NY +1σ band are re-tests, not the first touch the flagship trades (v9 §6) — overlap with `vwap-upper-band-bounce` entries should be near zero; verify by joining trade timestamps on shared sessions.

## 8. Out of scope for v1

Co-snap/lone filtering (died monthly), session-ref stacking (died — concentration artifact), `closed_by=level` arming (that's the profile-pullback knob, untested monthly), any order-flow condition (three studies say the tape adds nothing at entry).
