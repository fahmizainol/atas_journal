# Weekly −1σ deep-traverse long — draft → strategy promotion

**Date:** 2026-07-28 · **Status:** BUILT — engine baseline replicates the draft; economics thin, experimental
**Lineage:** [weekly-vwap-context](weekly-vwap-context.md) study → `weekly-lower1-deep-traverse-long` draft → this promotion
**Baseline run:** `weekly-lower1-deep-traverse-long` / `20250203-20260630-v2-1910733c` (v1-77d86b1b's trade-identical v2 twin — v2 added the trail and daily-loss knobs, all off in the baseline; the 111 trades were verified byte-equal)

The strongest cell of the weekly-band touch-context study — a session leg that
starts at the weekly mid (or higher) and runs down into the weekly −1σ with no
prior residence below the band — had a draft with an all-false validation
checklist and a second-half-concentration worry. This pass ran the missing
validation and, on a qualified pass, promoted the draft to a registered engine
strategy.

## 1. Draft validation (the cheap pass)

On the materialized draft trades (111 trades, 2025-02-12 → 2026-06-25):

| check | result |
|---|---|
| split-half | **PASS** — first half +4.9R / 56.6% wr, second half +10.2R / 58.9% wr. The study table's "second-half concentration" (51.4% vs 63.9%) dissolves at trade level. |
| monthly | **qualified pass** — 10/17 months positive, best month ~29% of net, no single-month dependence. Not a 17/17. |
| pooled significance | borderline: 57.8% wr on 109 decided races vs the 50% null, one-sided p ≈ 0.052; session-cluster bootstrap P(total ≤ 0) = 0.10 |
| tail concentration | top 5 trades = 87% of net R, 177% of net points — the aggregate is thin |
| RTH vs ON | overnight cohort carries more per-trade edge (avg R 0.216, n=43) than RTH (0.086, n=68) |

Verdict: real-looking but thin lead, sign-stable across halves and months —
worth the engine stage, not worth belief.

## 2. The strategy

`weekly-lower1-deep-traverse-long` (`WeeklyTraverseConfig`,
`engine.run_session_weekly_traverse`, session="globex"). Long only by
construction — the upper1 mirror cell was REVERSED in the study, so there is no
side knob. v2 added the bounce's management knobs, all off by default: the
step trail (`trail_stop_ticks` / `trail_step_ticks` / `trail_breakeven_ticks` /
`trail_breakeven_only`; the R-multiple stays measured against the initial
stop) and the daily loss governor (`daily_loss_stop` / `daily_loss_exit_open`).

Detection reproduces the study event causally on 1-minute bars over the full
Globex session: band touch re-armed only after a full bar clears by
`rearm_sigma` (0.25σ), approach-from-above, strictly fewer than
`max_res_below_min` (5) prior closes below the band, σ-position reached
`min_origin_sigma` (0) inside the trailing `origin_lookback_min` (120). Entry
is a market order on the tick after the signal bar closes; a fill already at or
past either race threshold is skipped (the draft's "race decided at entry"
rule). Exits are the study's race made tradeable: stop `stop_sigma` (0.30σ)
below the level (or `entry_ticks` fixed risk), target `target_sigma` above it
(or the weekly mid tracked live, or an R multiple), `max_hold_min` (60) flatten.
First-session and broken-week days stand down per the weekly anchor's honesty
rules; overnight entries are allowed by default (`rth_only=false`) because the
study's frame is the whole session and the ON cohort was the stronger one.

## 3. Engine A/B vs the draft

The baseline run replicates the draft **1:1**: 111 trades, every one matching
on entry minute; exit reasons agree everywhere except the two time-outs
(naming: draft "time" = engine "maxhold"); per-trade R correlation 0.98. Mean R
0.109 vs the draft's 0.136 — the gap is tick-resolution honesty (stops fill at
the actual print through the level, entries at the true next tick).

Economics on 1 contract, commission included:

- net **+$2,892** over 17 months, 111 trades (~$26/trade expectancy)
- PF **1.10**, win rate 57.7%, Sharpe 0.32, max DD −$5,093
- median per-trade risk ~22 pts (σ-stop: risk varies with band width, p95 ~70 pts)

## 4. Verdict

The promotion is *mechanically* clean — the engine trades exactly the studied
event — but the baseline economics are thin: PF 1.10 with an equity curve that
owes most of its net to a handful of wide-band winners, exactly the draft's
tail-concentration caveat carried through. Ship as **experimental**; do not
adopt for live sizing on this evidence.

The one lead the baseline leaves on the table: the study's edge **grew with
horizon** (+0.21σ@60m → +0.29σ@120m) while this baseline exits at a fixed
0.30σ race with a 60m cap — `target_mode="wk_mid"` (the reversion magnet the
hypothesis actually names) with a longer `max_hold_min` is the obvious first
knob A/B. Second: the σ-scaled stop makes worst-case risk enormous on
wide-band days (a −158 pt stop landed in the draft); `stop_mode="entry_ticks"`
is the fixed-risk sibling read, the drift-fade entry-stop lesson.
