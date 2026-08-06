# Vol-clock — are the time-denominated findings regime artifacts?

Diagnostic re-cut of the adopted/characterized baselines by causal daily-ATR regime (`datr_pctl60` terciles from the reusable `atr-band/daily_atr.parquet`). Premise: ATR sets the clock — a fixed tick traverse completes between ~ATR-ratio (drift) and ~ATR-ratio² (chop) faster on hot days — so every wall-clock finding could be regime-dependent. Read-only; no engine changes.

- **UB** = upper-band v13 (a348d176, audited stack) — n=242 in ATR window
- **DTF** = drift-touch v2 baseline 12-15h (523f4000) — n=137 in ATR window
- **DTF_FULL** = drift-touch v1 full-window 09:45-15h (7e7a94ea) — n=170 in ATR window
- **GPOC** = drift-globex-poc v2 (0a20b6a9) — n=93 in ATR window
- **GB** = globex-bounce v14 invert-on (74e6af45) — n=643 in ATR window

Tercile median daily ATR14: quiet 361 pt, mid 438 pt, hot 506 pt (hot/quiet ratio 1.40; drift-case clock bound = that ratio, chop-case = its square).

## 1. Clock test — does hold time scale with ATR?

| run | cohort | n | ρ(ATR,dur) | p | med dur quiet | mid | hot | quiet/hot |
|---|---|---|---|---|---|---|---|---|
| UB | winners | 181 | -0.43 | 0.000 | 17m | 6m | 4m | 4.01x |
| UB | losers | 61 | -0.09 | 0.488 | 4m | 3m | 2m | 1.96x |
| DTF | winners | 122 | -0.39 | 0.000 | 15m | 8m | 4m | 3.91x |
| DTF | losers | 15 | -0.72 | 0.003 | 49m | 8m | 24m | 2.04x |
| DTF_FULL | winners | 99 | -0.32 | 0.001 | 22m | 19m | 10m | 2.18x |
| DTF_FULL | losers | 71 | -0.31 | 0.008 | 51m | 35m | 14m | 3.71x |
| GPOC | winners | 78 | -0.29 | 0.010 | 4m | 3m | 2m | 1.62x |
| GPOC | losers | 15 | -0.47 | 0.077 | 7m | 4m | 2m | 2.87x |
| GB | winners | 397 | -0.35 | 0.000 | 12m | 7m | 3m | 4.11x |
| GB | losers | 246 | -0.38 | 0.000 | 4m | 4m | 2m | 2.01x |

Excursion clocks (same test on underwater/recovery/giveback seconds):

| run | clock | n | ρ | p |
|---|---|---|---|---|
| UB | underwater_s | 242 | -0.13 | 0.041 |
| UB | recovery_s | 186 | -0.17 | 0.019 |
| UB | giveback_s | 55 | -0.00 | 0.999 |
| DTF | underwater_s | 134 | -0.35 | 0.000 |
| DTF | recovery_s | 120 | -0.32 | 0.000 |
| DTF_FULL | underwater_s | 166 | -0.30 | 0.000 |
| DTF_FULL | recovery_s | 99 | -0.30 | 0.002 |
| DTF_FULL | giveback_s | 80 | -0.11 | 0.342 |
| GPOC | underwater_s | 92 | -0.13 | 0.230 |
| GPOC | recovery_s | 77 | -0.14 | 0.215 |
| GB | underwater_s | 643 | -0.18 | 0.000 |
| GB | recovery_s | 436 | -0.13 | 0.006 |
| GB | giveback_s | 207 | -0.23 | 0.001 |

## 2. Session-boundary risk — exit mix & frequency by tercile

| run | tercile | trades | trades/sess | target+trail | stop | time-exit | time-exit avgR |
|---|---|---|---|---|---|---|---|
| UB | quiet | 85 | 0.63 | 84% | 16% | 0% | — |
| UB | mid | 61 | 0.75 | 72% | 28% | 0% | — |
| UB | hot | 96 | 0.83 | 74% | 26% | 0% | — |
| DTF | quiet | 60 | 0.45 | 82% | 13% | 5% | -0.35R(n=3) |
| DTF | mid | 33 | 0.41 | 94% | 6% | 0% | — |
| DTF | hot | 44 | 0.41 | 93% | 7% | 0% | — |
| DTF_FULL | quiet | 59 | 0.46 | 56% | 32% | 12% | -0.35R(n=7) |
| DTF_FULL | mid | 39 | 0.49 | 59% | 36% | 5% | -0.57R(n=2) |
| DTF_FULL | hot | 72 | 0.63 | 56% | 43% | 1% | +1.18R(n=1) |
| GPOC | quiet | 29 | 0.22 | 90% | 10% | 0% | — |
| GPOC | mid | 28 | 0.35 | 79% | 21% | 0% | — |
| GPOC | hot | 36 | 0.32 | 83% | 17% | 0% | — |
| GB | quiet | 213 | 1.59 | 65% | 32% | 2% | +0.13R(n=5) |
| GB | mid | 177 | 2.19 | 67% | 31% | 3% | +0.72R(n=5) |
| GB | hot | 253 | 2.22 | 67% | 32% | 1% | +0.09R(n=2) |

## 3. Where does the edge live? Expectancy by tercile

| run | tercile | n | net | win% | avgR | PF |
|---|---|---|---|---|---|---|
| UB | quiet | 85 | $97,237 | 84% | +0.53 | 4.01 |
| UB | mid | 61 | $14,116 | 72% | +0.12 | 1.36 |
| UB | hot | 96 | $36,606 | 69% | +0.19 | 1.63 |
| DTF | quiet | 60 | $8,265 | 83% | +0.07 | 1.24 |
| DTF | mid | 33 | $17,964 | 94% | +0.22 | 3.08 |
| DTF | hot | 44 | $23,952 | 93% | +0.22 | 3.13 |
| DTF_FULL | quiet | 59 | $-6,045 | 59% | -0.11 | 0.76 |
| DTF_FULL | mid | 39 | $-5,356 | 59% | -0.15 | 0.70 |
| DTF_FULL | hot | 72 | $-5,503 | 57% | -0.08 | 0.87 |
| GPOC | quiet | 29 | $22,362 | 90% | +0.31 | 3.79 |
| GPOC | mid | 28 | $7,479 | 79% | +0.12 | 1.47 |
| GPOC | hot | 36 | $16,578 | 83% | +0.19 | 2.04 |
| GB | quiet | 213 | $-6,066 | 62% | -0.02 | 0.89 |
| GB | mid | 177 | $10,463 | 63% | +0.10 | 1.25 |
| GB | hot | 253 | $-3,501 | 61% | +0.00 | 0.94 |

## 4. The wall-clock findings, re-cut by regime

### 4a. "Mornings lose" (DTF_FULL, 09:45–15:00 window)

| tercile | window | n | net | avgR | win% |
|---|---|---|---|---|---|
| quiet | morning | 28 | $-5,361 | -0.22 | 57% |
| quiet | afternoon | 31 | $-684 | -0.01 | 61% |
| mid | morning | 15 | $-549 | -0.03 | 67% |
| mid | afternoon | 24 | $-4,807 | -0.23 | 54% |
| hot | morning | 39 | $-14,244 | -0.44 | 49% |
| hot | afternoon | 33 | $8,741 | +0.35 | 67% |

### 4b. "First hour = 67% of net" (GPOC — first hour of fills, 09h local; the 01:30 window never fills before 09h)

| tercile | n | n 1st-hr | 1st-hr net | total net | 1st-hr share |
|---|---|---|---|---|---|
| quiet | 29 | 13 | $8,994 | $22,362 | +40% |
| mid | 28 | 11 | $10,098 | $7,479 | +135% |
| hot | 36 | 10 | $10,920 | $16,578 | +66% |

### 4c. "First fill of day carries all P&L" (GB)

| tercile | n first | first net | first avgR | n later | later net | later avgR |
|---|---|---|---|---|---|---|
| quiet | 76 | $8,386 | +0.17 | 137 | $-14,452 | -0.12 |
| mid | 56 | $7,577 | +0.20 | 121 | $2,886 | +0.05 |
| hot | 69 | $1,523 | +0.05 | 184 | $-5,024 | -0.02 |

## 5. Re-arm clock — stop → next signal gap (UB, booked + ghost)

Booked re-entries are structurally rare on v13 (a full 3-lot stop ≈ $2,250 trips the $1,995 daily loss stop — only partial stops can re-arm, n=4), so the clock is measured on booked + ghost signals from `missed.parquet`. The loser-study "59% regain entry ≤15 min" window is the reference wall-clock quantity.

| tercile | n stop→signal | med gap | ≤15 min | next is ghost |
|---|---|---|---|---|
| quiet | 1 | 10m | 100% | 0% |
| mid | 5 | 43m | 20% | 0% |
| hot | 5 | 16m | 20% | 0% |

Pooled ρ(ATR, gap) = +0.10 (p=0.759, n=11).

## 6. Split-half robustness (by date)

Winner clock ρ(ATR, duration) per half:

| run | half | n | ρ | p |
|---|---|---|---|---|
| UB | H1 | 90 | -0.34 | 0.001 |
| UB | H2 | 91 | -0.32 | 0.002 |
| DTF | H1 | 61 | -0.30 | 0.018 |
| DTF | H2 | 61 | -0.30 | 0.017 |
| DTF_FULL | H1 | 49 | -0.30 | 0.034 |
| DTF_FULL | H2 | 50 | -0.21 | 0.147 |
| GPOC | H1 | 39 | -0.31 | 0.057 |
| GPOC | H2 | 39 | -0.03 | 0.833 |
| GB | H1 | 198 | -0.44 | 0.000 |
| GB | H2 | 199 | -0.11 | 0.116 |

DTF_FULL hot-tercile morning vs afternoon, per half:

| half | window | n | net | avgR |
|---|---|---|---|---|
| H1 | morning | 21 | $-8,909 | -0.51 |
| H1 | afternoon | 15 | $4,301 | +0.38 |
| H2 | morning | 18 | $-5,335 | -0.35 |
| H2 | afternoon | 18 | $4,440 | +0.33 |

## 7. Monthly-sign check on the expectancy leans

| run | focus tercile(s) | vs | months focus wins | months skipped |
|---|---|---|---|---|
| UB | quiet | mid+hot | 7/8 | 8 |
| DTF | mid+hot | quiet | 5/5 | 11 |

## 8. Addendum (2026-08-02) — do scheduled event days spike the ATR?

Follow-up question: the label is lagged, so do CPI/FOMC/NFP/earnings days break it? Checked the verified event calendar (event-day-overlay §5) against `daily_atr.parquet` (`data/research/vol-clock/event_atr_check.py`).

| cohort | n | med TR (pt) | TR ÷ lagged ATR14 (med) | days >1.25× expected |
|---|---|---|---|---|
| clean | 231 | 406 | 0.90 | 25% |
| pre-macro | 65 | 354 | 0.89 | 22% |
| FOMC | 11 | 356 | 0.94 | 18% |
| CPI | 16 | 355 | 0.81 | 20% |
| NFP | 15 | 521 | 1.09 | 43% |
| mega-cap earnings reaction | 22 | 501 | 1.20 | 50% |

- **Daily range spikes only on NFP + earnings-reaction days.** FOMC/CPI days ranged *below* clean days here — the announcement move is minutes-long, often mean-reverts, and sits inside an already-400pt range (FOMC mornings are additionally compressed, the pre-FOMC-drift effect).
- **ATR14 itself never spikes** — Wilder 1/14 weighting: median one-macro-day shift −0.2%, p90 +5.9%. ATR is a regime estimator, structurally deaf to single events.
- **But label flips cluster on event days:** 35% of quiet↔hot (label vs realized-range) flips land on event days vs 21% base rate (~1.7×), driven by NFP/earnings. Those days are knowable further in advance than the ATR — a calendar overlay is the cheap label repair *if* the §3 rotation lead ever graduates. (Strategy-level event effect is already a resolved null — see event-day-overlay.)
- Caveats: n=11 FOMC, one 17-month sample; read FOMC/CPI as "no spike", not proven compression.

## 9. Rotation re-cut (2026-08-03) — the lead does NOT graduate

The §3/§7 lead (UB=quiet habitat, DTF=mid/hot) put through the pre-registered discipline before any A/B: re-cut on the pinned baselines, window-length sensitivity (40/60/90, robustness not search), and the rotation portfolio measured exactly (sessions are engine-independent — no cross-day state — so a day-level on/off is a faithful re-simulation, unlike trade-level gates). Script `data/research/vol-clock/rotation_recut.py`. Note: DTF's pin is v1 `63c78056` (`use_session_refs on`); the §3 cut used the v2 entry-reason config `523f4000` — both cut here.

Lean by window (focus-vs-rest ΔavgR; months = focus wins, ≥3 trades/side; halves by date):

| run | w | ΔavgR | p | months | halves |
|---|---|---|---|---|---|
| UB quiet | 40 | +0.26 | .067 | 6/9 | +0.49 / **−0.03** |
| UB quiet | 60 | +0.36 | .009 | 7/8 | +0.53 / +0.16 |
| UB quiet | 90 | +0.29 | .044 | 3/4 | +0.33 / +0.06 |
| DTF v1 (pinned) mid/hot | 40 | +0.12 | .18 | **3/8** | +0.05 / +0.24 |
| DTF v1 (pinned) mid/hot | 60 | +0.11 | .22 | **1/6** | +0.06 / +0.21 |
| DTF v1 (pinned) mid/hot | 90 | +0.06 | .49 | **1/4** | +0.01 / +0.62 |
| DTF v2 mid/hot | 40 | +0.16 | .12 | 3/6 | +0.08 / +0.31 |
| DTF v2 mid/hot | 60 | +0.15 | .11 | 5/5 | +0.12 / +0.28 |
| DTF v2 mid/hot | 90 | **+0.01** | .92 | 2/3 | +0.02 / +0.48 |

Rotation portfolio, exact day-filter (w60 label, DTF arm = v2):

| portfolio | net | maxDD | Sharpe | months+ | trades |
|---|---|---|---|---|---|
| A always-both | $198,139 | −$15,396 | 4.87 | 13/16 | 379 |
| B hard rotation | $139,153 | −$9,859 | 7.62 | 16/16 | 162 |
| C UB always + DTF mid/hot | $189,874 | −$14,361 | 5.20 | 14/16 | 319 |
| D UB quiet + DTF always | $147,418 | −$10,894 | 6.57 | 15/16 | 222 |

- **DTF's mid/hot lean fails the re-cut.** On the *pinned* baseline the §7 "5/5 months" collapses to 1/6 (p=.22); it was config-specific (v2's dropped session-refs) and window-specific (dies at w90 even on v2, ΔavgR +0.01). Not adoption-grade.
- **UB's quiet lean is real but decaying** — sign survives all three windows and the monthly check, but H2 is much softer everywhere (+0.16/−0.03/+0.06), so the concentration is H1-heavy.
- **Rotation is a risk-preference trade, not found money.** UB still *makes* +$50k on mid/hot days (§3) — hard rotation forfeits ~30% of net ($198k→$139k) to buy Sharpe 4.9→7.6 and maxDD −$15.4k→−$9.9k. Dropping only DTF-quiet (C) moves almost nothing ($8k).
- Window labels agree 77% across 40/60/90 (91% for 40-vs-60) — the construction isn't knife-edged; the lead's fragility is in the *edge*, not the label.

**Disposition: retired pre-A/B.** No rotation build, no gates, and §8's calendar label repair is moot (it was gated on this lead graduating). The one residual descriptive fact worth keeping: UB quiet days are its best cohort but every tercile is net-positive — there is nothing to turn off. Cost of the discipline: one read-only script vs. what would have been the 13th failed engine A/B.

## Verdict

1. **The clock is real (CONFIRMED).** Winner hold time anti-correlates with daily ATR on all five runs (ρ −0.29…−0.43, p≤0.01 pooled), and the quiet/hot median-duration ratios (1.6–4.1×) sit at or beyond the chop-case bound — wall-clock durations are regime quantities, full stop. Split-half: rock-solid on UB/DTF, direction-stable but H2-soft on GPOC/GB.

2. **Window boundaries don't need ATR-scaling — their *contents* are regime-dependent.** "Mornings lose" on drift-touch is really *hot-regime* mornings losing (−0.44R, split-half stable −0.51/−0.35); quiet mornings are only mildly negative. The adopted afternoon window earns its keep almost entirely on hot/mid days (§3, §4a). The GPOC first-hour edge is roughly regime-invariant in dollars (~$9–11k per tercile) — it is the *rest* of the day that swings with regime. GB's first-fill advantage holds in every tercile but is thinnest on hot days, and later fills bleed worst on quiet days.

3. **New lead: UB's edge concentrates on quiet days** (avgR +0.53, PF 4.0, 84% win vs +0.19/1.6/69% hot) — and the monthly-sign check in §7 says how seriously to take it. Mirror lead: DTF prefers mid/hot. Opposite-signed regime leans on the two adopted strategies would be a natural *portfolio* rotation, not a per-strategy gate — but the gate scoreboard (1 pass / 12+ fails) and the weekly-VWAP lesson (re-cut on the current baseline before building) both counsel patience.

4. **Re-arm clock: cannot be measured on this run** (n=11 stop→signal gaps — a full 3-lot stop trips the daily loss stop, truncating the sample). The loser-study "59% regain ≤15 min" was a *price-path* property, not a signal property; re-cutting it by regime needs bar data per stop. Open item, low priority.

5. **What NOT to do:** no ATR-time re-expression of window boundaries, no ATR-scaled stops (already dead per atr-band study), no new gates off this diagnostic alone.


## Percentile-window sensitivity — 60 vs 30 vs 14 sessions

Follow-up (2026-08-03): the regime label's 60-session percentile window was a
choice, not a finding. Re-cut at 30 and 14 sessions
(`pctl_window_sweep.py`, same causal ATR14, all stats on the 330 sessions
labelled by all three windows).

**Label behaviour** — shorter windows self-normalise the regime away:

| window | agree w/ 60 | day-to-day flip rate | median run | hot/quiet median ATR |
|---|---|---|---|---|
| 60 | 100% | 11% | 3.0 d | 1.40× |
| 30 | 69% | 17% | 2.0 d | 1.32× |
| 14 | 65% | 23% | 1.0 d | 1.19× |

The extremes are mostly stable (quiet→quiet 81–82%, hot→hot 73–83%) but the
mid tercile is mush (25–28% retained), and at W14 fully **15% of 60-window
hot days get labelled quiet** — the label starts inverting, because a
"hot day in a quiet fortnight" isn't hot in absolute points. A label that
flips every other day (median run 1 d at W14) is a surprise measure, not a
regime.

**The study's findings are window-robust:**

- UB quiet lean survives at every window: avgR +0.53 / +0.55 / +0.43,
  66–71% of net on ~35–44% of trades. Slightly diluted at 14 (the misfiled
  hot days drag it), never gone.
- DTF hot-morning loss (full-window run) survives and even sharpens:
  −0.44R / −0.41R / −0.71R.
- Clock ratios (median winner hold, quiet/hot) hold everywhere but compress
  as the window shrinks (GB 4.1×→2.9×, DTF_FULL 2.2×→1.6×) — mechanically,
  less ATR contrast between the terciles ⇒ less clock contrast. GPOC is the
  exception (1.6×→3.7×) on small n.
- GB tercile leans reshuffle freely (mid best at 60, hot best at 14, mid
  dies) — confirms the retired rotation lead was label-fragile on GB.

**Verdict: keep 60.** It maximises regime contrast (1.40× ATR spread),
label stability (3-day runs), and keeps all three terciles populated
(W14's mid tercile collapses to n=12–30 trades on some runs, PF=inf
degeneracy). Nothing at 30/14 surfaced a lead the 60-window cut missed;
the robust findings are driven by the raw ATR, not the window. No change
to `vol_regime.py` `PCTL_WINDOW`.

## 10. Structure, IB, and ORB across the terciles (2026-08-03)

Three follow-up cuts asked whether the regimes differ in anything other than
scale and speed. Scripts: `structure_by_regime.py`, `ib_by_regime.py`,
`orb_stop_by_regime.py` (all in `data/research/vol-clock/`).

**Market structure: shape is regime-invariant.** Joining the regime artifact's
structure KPIs (`eod_structure_v8.parquet`, 333 clean sessions) to the
terciles: chop occupancy (0.428/0.459/0.426), structure break rate, CHoCH
rate, directionality (|net|/range ≈ 0.48), close position, and up-day rate are
all statistically flat (p ≥ 0.38). The mid-tercile "churny" spike (43%) is an
H1-only artifact (56%→23%); day-class trend share leans hot (62% vs 50%,
p=.055, 2/4 months) but BOS-share leans the other way — descriptive at most.
A hot day is the same day played faster, which is *why* the clock finding is
clean on all five baselines.

**Initial balance: proportionally identical.** 60-min IB snapshot regenerated
over the full window (`NQ_20250201-20260630_v1-aada9fa2b02c`, 363 sessions).
IB fraction of day range 0.64/0.63/0.62, first-break timing ~97 min everywhere,
day-range/IB ratio, both-side break rate, break-held rate, CBOT day-type mix,
extension milestones: all flat. One stable lean: **quiet days break the IB
upward** (63% vs hot's 50%, p=.043, ~+16pt in both halves) while up-*day* rate
stays flat — quiet days drift out the top of their range, hot days pick a side
at random. Rhymes with UB-quiet being a long habitat; nothing to build alone.

**Stop-enforced 5m ORB × regime: the trend-proxy candidate FAILS.** The
unenforced Zarattini read concentrated its R on hot days (sumR +71 vs −5/−48),
which looked like the scouting queue's missing "entry-time trend proxy". With
the stop enforced (exact replication of scouting §2: n=257, +0.283R, 26.8%
win on the doc window), the concentration evaporates:

| tercile | n | avgR | months+ | halves | 2026-only avgR |
|---|---|---|---|---|---|
| quiet | 134 | +0.124 | 5/12 | +0.19/+0.00 | −0.154 |
| mid | 81 | −0.075 | 2/7 | −0.23/+0.20 | −0.053 |
| hot | 114 | +0.108 | 3/8 | **+1.18/−0.19** | **−0.245** |

The hot tercile's whole edge is 2025 — April 2025 alone runs +3.64 avgR over
7 sessions (the tariff-crash trend rips); 2026 hot is −0.245. **Every tercile
is negative in 2026** (−23.8R over 126 sessions), and the full-window pooled
avgR is +0.069 vs +0.283 on the doc window — the surviving shape itself has
decayed out of sample. Verdict: `datr_pctl60` is **not** the ORB's trend
proxy, `orb-breakout` stays parked, and the OOS decay downgrades the whole
ORB item — any future revival needs the 2026 vintage explained first.

### 10b. IB extension levels (0.5×/1×/1.5×) — no special power (2026-08-03)

Three reads (`ib_ext_check.py`), full window, by tercile:

- **No momentum cascade.** P(next milestone | this one) ≈ coin flip at every
  rung: 55% → 47% → 40% → 47%. Reaching an extension says nothing about
  reaching the next. Hot has a small deep-tail lean (1.0→1.5×: 53% vs quiet
  32%; 1.5→2.0×: 62% vs 42%) on n=30/16 — fatter hot tails, not a signal.
- **No close magnet.** Only 40–49% of days that touch a level close beyond
  it; median retention of the max extension at the close is 0.51 — half of
  any extension is given back, at every level, in every tercile.
- **The "stall at the target" is real but generic.** 30-min post-first-touch
  move (touch bar excluded) underperforms the same-day drift null at every
  level (−0.04…−0.13×IB) — but **placebo levels stall identically**
  (0.75×: −0.079; 1.25×: −0.105, both mid-curve between their lore
  neighbors). The stall is a smooth function of extension depth — the
  fresh-extreme/over-extension effect — not level-specificity. Same verdict
  class as VAH-snap resistance and stable-level S/R.

Disposition: extension guides stay a default-off chart decoration; no
targets, no fades, no gates built on them. Mid-tercile drives the biggest
stall deltas (−0.20/−0.34) — the artifact-prone small tercile again, per §10.

### 10c. IB range size (2026-08-03) — the "narrow IB → trend day" lore is a unit artifact; width is a real UB context axis

`ib_width_check.py`, 330 sessions, width = `ib_vs_adr` terciles (edges 0.46/0.67×ADR14).

- **Compression→expansion is DEAD, and the lore's mechanism with it.** IB width
  says nothing about how much new range the afternoon adds:
  corr(width, post-IB expansion in ADR units) = **+0.01** (halves −0.07/+0.14).
  The classic "narrow IB predicts a trend day" is the CBOT classifier's own
  denominator: trend/extension are measured in ×IB, so the same absolute
  afternoon range scores 0.79×IB after a narrow morning and 0.35×IB after a
  wide one (both-break 31% vs 9% — same artifact). In ADR units, expansion is
  ~0.39–0.44 everywhere.
- **The day's range is the morning's range plus a constant.** corr(width,
  day-range/ADR) = +0.61 (halves +0.60/+0.63): post-10:30 sessions add
  ≈0.4×ADR14 of new range regardless of how the morning went. Useful for the
  simulator ATR-clock: expected remaining new range after the IB completes is
  ~0.4×expected ATR, flat in morning width.
- **Width is orthogonal to the vol regime** (corr with datr_pctl60 = −0.05) and
  knowable at 10:30 — a genuinely independent, causal same-day descriptor.
- **Live-strategy cut (pinned baselines):** UB has a clean monotone width
  gradient — narrow +0.08 avgR / $10.7k, mid +0.27 / $45.9k, wide **+0.49 /
  $91.4k** (win 68→81%, wide halves +0.59/+0.40). DTF mildly mirrors (wide is
  its weakest arm, H2 −0.02). BUT every cohort is net-positive → nothing to
  veto (the rotation lesson again); the built `ib_width` gate stays off. At
  most a context/at-a-glance read: UB's best days have a wide first hour on a
  quiet-regime day — morning realized vol high, lagged daily vol low.

Caveat: IB width and morning band width both measure first-hour realized vol
(intraday ATR = band renamed, ρ .96) — the UB gradient is likely "UB likes a
wide morning channel" restated, not a new mechanism. No knob, no A/B queued.

**§10c addendum — width vs day character (structure labels):** the association
the lore claims exists, but with the sign REVERSED and no forecasting content.
By width tercile: trend-class share narrow 47%→wide 60% (both halves: 44/54,
52/65), balance/parked share narrow **29%**→wide **12%**, churny texture
38%→21% — all monotone. So **wide IB leans trend day, narrow IB leans
balance/churn** — opposite the compression lore. But this is recognition, not
prediction: post-IB expansion is width-flat (the trend day is already trending
in its first hour — the wide IB *is* the trend announcing itself), raw
directionality (|net|/range) barely moves (0.47/0.46/0.49), and narrow days
still close outside their IB 62% of the time. Coheres with everything else:
UB's wide-IB gradient = drift-harvesting on trend-up days; ORB's narrow-IB
tercile being its worst (scouting §2) = ORB starving on balance days; and
"narrow = mean-reversion day" is half-true but NOT tradeable — balance days
lose even for responsive strategies (balance-day-fade study; NQ edges are
day-with only).

**§10c addendum 2 — width × regime:** hot days do NOT produce relatively wider
IBs. In points yes (median 147/170/220 quiet→hot — everything is bigger), but
in ADR units the IB is the same size everywhere (mean ib_vs_adr
0.64/0.57/0.63, corr with datr_pctl60 = −0.05): lagged daily vol and
same-morning realized vol are independent axes. The mid-regime "narrow lean"
(46%) is an H1-only artifact (56%→28%) — the mid tercile's third artifact this
study. The UB two-way cut says the axes SUBSTITUTE rather than stack:
quiet days are good at every width (+0.47/+0.56/+0.54); on mid/hot days width
becomes the discriminator (hot: −0.20 narrow → +0.43 wide); the only negative
UB cell is **hot + narrow** (−0.20 avgR, n=28) — a hot backdrop that fails to
deliver a morning channel. Quiet+wide is NOT a super-cohort (+0.54 ≈ quiet
overall). Cells n=18–34, descriptive only. DTF shows no coherent 2-way
pattern at these cell sizes.
