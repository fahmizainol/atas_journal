# ATR × VWAP Upper-Band Bounce — is volatility a second axis?

- **Date:** 2026-07-28
- **Status:** **RESOLVED — characterization only, nothing actionable.** ATR splits into two parts against this strategy: the intraday-cumulative part *is* the σ band renamed (ρ ≈ 0.96), and the cross-day part (daily ATR) is genuinely orthogonal but its "high-vol days do worse" lean is a 2025 artifact that is flat in 2026. No ATR gate, no ATR-scaled stop, no engine A/B warranted.
- **Research question:** What is the relationship between ATR — the standard vol yardstick — and the `vwap-upper-band-bounce` strategy? Three candidate roles: (1) is ATR *redundant* with the σ-scaled band the strategy already trades, (2) does entry-time or prior-day ATR *predict outcomes* (a vol-regime gate), (3) should stops/targets be *ATR-scaled* instead of fixed-tick?
- **Data:** 363 sessions, NQ front-month ticks, 2025-02-03 → 2026-06-30. Daily ATR(14) is Wilder-smoothed true range of the full globex day (on+rth+post segments), **shifted one session** so a trade day only ever sees vol through yesterday; intraday ATR(14) on 1-min and 5-min bars over the ON+RTH stream, sampled at the last bar that *closed* before entry (and frozen at 09:30 as a leak-resistant anchor). Median daily ATR(14): **457 pts**.
- **Runs:** the current baseline **`a348d176`** (v13 full gate stack, 262 trades, +$150,439, PF 2.05) and the pre-gate **`cdc07ca2`** (v10 reenter run, 222 trades, +$124,508, PF 1.98) — two-run robustness so gate-shaped selection can't manufacture a lean.
- **Scripts:** `data/research/atr-band/` — `build_features.py` (daily_atr.parquet + features_<run>.parquet), `analyze.py` (collinearity / outcome / geometry / terciles / session / split-half), `monthly.py`, `yearsplit.py`.

---

## TL;DR

**The σ band is already a realized-range meter — ATR's intraday content is priced in, and the part that isn't (yesterday's vol) doesn't replicate as a signal. Trade geometry stays absolute, so ATR-scaled stops have no leg to stand on either.**

1. **RTH-range-so-far ≈ band width, ρ = 0.96.** The band's σ integrates the day's dispersion, so any cumulative intraday vol measure is the band renamed. 5-min ATR sits at the redundancy line (ρ 0.66–0.71). Only *daily* ATR is orthogonal (ρ ≈ 0.25–0.28) — the one genuinely new axis ATR brings is "what kind of vol regime did we walk in from," not "how volatile is today."

2. **Band width itself carries zero outcome signal** among taken trades (AUC_stop 0.44–0.45, ρ_R ≈ 0.00 in both runs) — consistent with `min_band_width_ticks: 50` already trimming the degenerate mornings.

3. **The vol-headwind lean is real-looking and then dies.** Pooled, higher prior-day ATR → worse R in *both* runs (ρ −0.125 / −0.144, p ≈ 0.04; low-ATR tercile PF 2.79/3.04 vs high 1.74/1.81; net-per-traded-session declines monotonically 983→667 and 1,140→631). But split by calendar year: 2025 ρ −0.151/−0.172, **2026 ρ −0.013/−0.021** and the tercile ordering scrambles (2026 PF: low 1.16, mid 4.27, high 1.39). Within-month ρ is negative in only 10/16 and 10/14 months. This is the event-day-overlay / drift-fade-lead pattern again: a first-half lean that doesn't survive the year boundary.

4. **Even at face value there is no trade in it.** Every daily-ATR tercile is profitable in both runs (worst bucket still +$37–41k, PF ≥ 1.56). An ATR gate would delete positive-expectancy trades to buy a PF cosmetic — the exact shape of the 12-fail A/B scoreboard. Not built.

5. **ATR-scaled stops are contraindicated.** Winner heat does not scale with vol: ρ(|MAE|, daily ATR) = +0.05/+0.04, ρ(|MAE|, 1-min ATR) = +0.08/+0.10, all n.s.; MFE likewise ~0. This is the winner-landing-depth finding ("absolute ~34 ticks, not σ-scaled") confirmed on a second vol yardstick: the trade's geometry lives in level-structure points, not in vol units. Widening the 150t stop on high-ATR days would add risk where expectancy is already thinnest.

**Verdict:** ATR is either the band (intraday) or a non-replicating regime lean (daily). Keep the fixed-tick stop, keep the band as the vol instrument, spend nothing further here.

---

## 1. Collinearity — which ATR is just the band?

Spearman ρ vs `band_width_ticks` at entry (a348d176 / cdc07ca2):

| measure | ρ (v13) | ρ (v10) | reading |
|---|---|---|---|
| daily_atr14 (prior sessions) | +0.28 | +0.25 | orthogonal |
| datr_pctl60 | +0.21 | +0.18 | orthogonal |
| tr_prev (yesterday's range) | +0.21 | +0.22 | orthogonal |
| atr1m14 @ entry | +0.46 | +0.40 | partly shared |
| atr5m14 @ entry | **+0.71** | +0.66 | at the redundancy line |
| atr1m14 @ 09:30 | +0.40 | +0.34 | partly shared |
| globex range so far | +0.57 | +0.53 | partly shared |
| **RTH range so far** | **+0.96** | **+0.96** | **the band, renamed** |

The mechanism is arithmetic, not market structure: the NY anchor's σ is the volume-weighted dispersion since 09:30, so it *is* a running realized-range integral. Any "add ATR to the band chart" idea duplicates the y-axis. The oscillator pre-screen rule (ρ > 0.7 → don't build) fires for cumulative intraday measures before a single backtest.

## 2. Outcome — does vol predict R?

AUC of measure vs stop-out, Spearman vs r_multiple (a348d176 / cdc07ca2):

| measure | AUC_stop | ρ_R (p) v13 | ρ_R (p) v10 |
|---|---|---|---|
| daily_atr14 | 0.56 / 0.58 | −0.125 (.048) | −0.144 (.036) |
| tr_prev | 0.58 / 0.55 | −0.172 (.005) | −0.129 (.057) |
| atr1m14 @ 09:30 | 0.55 / 0.54 | −0.143 (.020) | −0.150 (.026) |
| atr1m14 @ entry | 0.55 / 0.47 | −0.133 (.032) | −0.078 (.248) |
| band width | 0.44 / 0.45 | +0.004 (.94) | −0.006 (.93) |

Sign-consistent across runs for the *daily/pre-open* measures — the entry-time 1-min ATR flips its tercile ordering between runs (low bucket best in v13, high bucket best in v10), i.e. noise. Daily-ATR terciles, pooled:

| run | tercile | n | win% | avg R | net | PF |
|---|---|---|---|---|---|---|
| v13 | low (241–409) | 84 | 79.8 | 0.39 | $69,977 | 2.79 |
| v13 | mid (410–502) | 83 | 77.1 | 0.23 | $39,532 | 1.95 |
| v13 | high (506–893) | 84 | 66.7 | 0.23 | $40,775 | 1.74 |
| v10 | low | 71 | 78.9 | 0.43 | $65,904 | 3.04 |
| v10 | mid | 70 | 74.3 | 0.17 | $23,414 | 1.56 |
| v10 | high | 71 | 66.2 | 0.25 | $37,314 | 1.81 |

Session level (v10, ungated): the strategy *fires more* on high-ATR regimes (trade-rate 0.37 → 0.48 across terciles) while earning less per traded session ($1,140 → $631) — a mild adverse-selection tilt, not a cliff.

## 3. Robustness — where the lean lives (and dies)

- **Split-half by trade count:** H1 ρ −0.168/−0.189 (p≈0.07), H2 **−0.057/−0.029** (n.s.) in v13/v10.
- **Calendar-year split:** 2025 ρ −0.151/−0.172; **2026 ρ −0.013/−0.021**. 2026 tercile PFs scramble to low 1.16 / mid 4.27 / high 1.39 (v13) — no monotone story survives.
- **Within-month ρ** (controls slow regime drift): median −0.12/−0.15, negative in only 10/16 and 10/14 months with n≥8.
- Dropping the Feb–May 2025 vol shock alone does *not* kill the pooled ρ (−0.14/−0.19) — the lean isn't purely the tariff-crash months — but everything after 2025-12-31 is flat, which is the split that matters for adoption.

Per the weekly-VWAP lesson (re-cut leads on the current baseline before building knobs): the current baseline's 2026 cohort shows nothing to harvest.

## 4. Geometry — should the stop be ATR-scaled?

Spearman of winner |MAE| (heat taken before recovering) and all-trade MFE vs vol measures:

| measure | winner MAE ρ (v13/v10) | MFE ρ (v13/v10) |
|---|---|---|
| daily_atr14 | +0.05 / +0.04 | −0.07 / −0.08 |
| atr1m14 | +0.08 / +0.10 | −0.10 / −0.02 |
| band width | +0.05 / +0.08 | +0.04 / +0.04 |

Nothing scales. Winners take the same absolute heat on a 250-pt-ATR day as on an 850-pt one — the third independent confirmation (after winner-landing-depth's dev1 finding and the σ-acceptance-depth A/B fail) that this trade's risk geometry is *absolute*, set by level structure, not by vol units. An ATR-multiple stop would systematically widen risk exactly where §2 shows expectancy is thinnest, and tighten it where the strategy is strongest.

## 5. Ledger

- No ATR gate built (lean non-replicating + all cohorts profitable → guaranteed A/B fail shape; scoreboard stands 1 pass / 12 fails).
- No ATR-scaled stop/target (geometry absolute, §4).
- No chart overlay proposed (intraday ATR duplicates the band, §1).
- Reusable: `daily_atr.parquet` (per-session globex-day OHLC + causal ATR14/percentile, 363 sessions) — a ready-made vol-regime column for any future per-session cut.
