# Structure-Enriched Regime — BOS/CHoCH state + chop occupancy as day descriptors

- **Date:** 2026-07-27
- **Research question:** The two structure studies left a clean feature layer (BOS/CHoCH event stream, `overlap_10` chop) that is forward-NULL as a *signal*. Does it earn its keep as *regime inputs* — new columns on the artifact the one proven-REAL detector already reads — and does a texture axis (clean vs churny), orthogonal to the trend/balance class, split expectancy within class?
- **What was built (REGIME_VERSION 7 → 8):** the swing-pivot → BOS/CHoCH bias state machine and the causal zigzag ported into `regime.py` as first-class per-checkpoint KPIs, ATR-scaled per the structure-events study §7 (fine ×2 / major ×4 median-ATR14 tiers, the ATR read causally from the bars available *at the checkpoint*, RTH bars preferred once 15 exist). Eight new KPIs: `st_bias`, `st_bias_age_min`, `st_bias_share`, `st_break_rate`, `st_bos_share` (major tier), `st_choch_rate` (fine tier), `chop_occ_30m`, `chop_occ_rth`. Plus a `texture` label (`clean`/`churny`) per checkpoint and at top level, orthogonal to `class` by construction. The machine runs over ON+RTH bars (the 09:30 skeleton is the overnight's), rates/shares count RTH bars only — state is knowable at the bell, rates need a session to rate.
- **Data:** all 367 cached sessions recomputed under v8; boards evaluated on five registered baselines — `vwap-upper-band-bounce` v13 `a348d176` (201 traded days), `drift-touch-fade-entry-stop` v2 `95580b82` (199), `profile-pullback-long` v4 `5092c2f1` (81), `ema-pullback-long` v1 `73dbb43a` (171), `value-rotation` v1 `c71aefcb` (111) — through `regime_pnl.study()`'s tercile-edge + shuffled-P&L luck machinery (Bonferroni bar 0.05/38).
- **Files:** `data/research/regime-structure/` — `recompute_v8.py`, `eod_structure_v8.parquet` (one row per session), `eval_boards.py`, `eval_boards_output.txt` (the full boards).

---

## TL;DR

- **The chop KPI had to be redesigned mid-build.** A session *mean* of bar overlap is degenerate — 365 sessions all land in 0.53–0.63 (std 0.016) because averaging 390 bars washes the texture out. The KPI that shipped is an **occupancy**: share of RTH minutes whose trailing 10-bar overlap ≥ 0.60 (the winners-vs-losers study's own window and threshold family). That has real spread (0.18–0.66, std 0.084).
- **On the upper-band baseline the structure family is real but mostly redundant.** `st_bias_share` holds at 12:00 (ρ .269, +$1,729/day tercile edge, luck .000) — but it is `net_conviction` restated from a different instrument (ρ .70 between them). `st_bias` and `st_bias_age_min` hold at eod — hindsight, descriptive.
- **One semi-independent hold:** `st_choch_rate` (fine-tier character-flip rate) at 12:00 — ρ −.229, −$1,285/day, luck .000, and only ρ .32 with `ny_vwap_cross_rate`, the closest existing churn KPI. Structure whipsaw is a second, partially independent read on the same "churn kills the bounce" fact the regime gate already trades on.
- **Nothing new survives at the actionable checkpoints.** At 09:45 and 10:30 no structure/chop KPI clears the bar on either run (best near-miss: `chop_occ_rth` at 09:45, ρ −.134, luck .056). The dual-VWAP family already owns 10:30 (17 holds).
- **On drift-fade entry-stop: everything null everywhere.** No structure or chop KPI holds at any checkpoint (0 holds of 38 at four of five checkpoints). Consistent with that strategy's whole history — its edge doesn't live on the day-type axis.
- **Texture does NOT split expectancy within class** — and several cells flip the naive sign: on both baselines, churny trend_up days *out-earn* clean ones ($1,520 vs $1,367 upper-band; $886 vs $257 drift-fade), and churny balance days beat clean balance days on both. Cells are small (5–35 days); no consistent direction. **Keep texture as a descriptive axis; do not gate on it.**
- **Verdict: ship the columns, build no actuator.** The enrichment is worth having — the boards now carry a structure-vocabulary confirmation of the regime story, computed causally, for free on every run. But nothing here beats or extends the existing detector at a decision-time checkpoint, so no gate, no knob, no A/B. (The one candidate, `st_choch_rate`@12:00, appears exactly where the VWAP family is already saturated, and the checkpoint-gate shape already failed an A/B once at −22%.)

## 1. The boards — where the new KPIs landed

`vwap-upper-band-bounce` a348d176, structure/chop rows only (full boards in `eval_boards_output.txt`):

| checkpoint | best new KPI | ρ | edge $/day | luck | holds? |
|---|---|---|---|---|---|
| 09:30 | `st_bias_age_min` | .039 | +615 | .552 | no |
| 09:45 | `chop_occ_rth` | −.134 | −929 | .056 | no (nearest miss) |
| 10:30 | `st_bias_share` | .110 | +733 | .090 | no — 17 VWAP KPIs hold here |
| 12:00 | `st_bias_share` | .269 | +1,729 | .000 | **yes** (but ρ .70 with `net_conviction`) |
| 12:00 | `st_choch_rate` | −.229 | −1,285 | .000 | **yes** (ρ .32 with `ny_vwap_cross_rate` — semi-independent) |
| eod | `st_bias_age_min` | .220 | +1,530 | .000 | **yes** (hindsight) |
| eod | `st_bias` | .243 | +1,051 | .000 | **yes** (hindsight) |

`drift-touch-fade-entry-stop` 95580b82: zero structure/chop holds at every checkpoint.

## 2. Redundancy — is the structure read new information?

Rank correlations on the upper-band run's 201 traded days:

| pair | 12:00 | eod | reading |
|---|---|---|---|
| `st_bias_share` ↔ `net_conviction` | +.70 | +.72 | same fact, different instrument |
| `st_choch_rate` ↔ `ny_vwap_cross_rate` | +.32 | +.10 | mostly independent churn measure |
| `st_bias_age_min` ↔ `longest_hold_min` | +.42 | +.18 | partially independent |
| `chop_occ_rth` ↔ everything | ≈0 | ≈0 | fully orthogonal — and not predictive |

The structure layer is an *independent instrument confirming the same regime*, which is exactly what the gate-robustness audit said the regime detector deserved: its cleanest signal now has a second, uncorrelated-in-mechanism witness. That is worth something even though it changes no decision today.

## 3. Texture — the class × texture grid

Both baselines, eod labels, avg net $/day (days in parens):

| class | upper-band clean | upper-band churny | drift-fade clean | drift-fade churny |
|---|---|---|---|---|
| trend_up | 1,367 (69) | **1,520 (30)** | 257 (47) | **886 (14)** |
| mixed | 497 (35) | 87 (12) | 657 (40) | −195 (13) |
| balance | −76 (14) | **385 (9)** | −208 (21) | **820 (9)** |
| trend_down | −1,136 (10) | −1,295 (7) | 644 (25) | 165 (15) |
| parked | 201 (10) | 1,623 (5) | 614 (10) | 2,053 (5) |

No consistent direction, small cells, and the "churn is bad" prior is *contradicted* as often as confirmed (churny trend_up and churny balance are better in all four of those cells). This echoes the chop-gate A/B failures: bar-level chop is real as a *stop predictor at entry time*, but day-level chop occupancy is not a P&L axis for these strategies. The label stays on the artifact as a descriptor.

## 4. Calibration notes (for whoever touches this next)

- **Texture threshold** `TEXTURE_CHURN_OCC = 0.48` ≈ 73rd percentile of the 365-session occupancy distribution — churny is a deliberate minority class, and it is class-orthogonal (every class's mean occupancy is 0.42–0.44). It describes the sample; it was not fit to P&L.
- **Synthetic-tape boundaries** (pinned in `tests/test_regime.py`): a frictionless ramp confirms no swing highs, so the machine honestly never seeds (`st_bias` None ≠ "up"); a fixed-amplitude rotation never closes beyond its own extremes, so it produces zero breaks — expanding rotation is what flips character. Both are correct SMC semantics, worth knowing before "why is st_bias null on this day" gets filed as a bug.
- **The eod rates are diluted by design choice, not accident:** rates count RTH bars only; the machine still *runs* over the overnight so the bell has a skeleton to read.

## 5. Cross-strategy sweep — the other three baselines

The KPIs are session artifacts, so the boards run on any strategy for free. The other three registered baselines:

- **profile-pullback-long** (81 days): zero holds of any KPI at any checkpoint — the sample is too thin for the machinery, and the structure rows sit mid-board. Null.
- **ema-pullback-long** (171 days): the most interesting non-result. `st_bias` and `st_bias_share` at **10:30** — an actionable checkpoint — post ρ .242 each with luck .002/.004 against a bar of .0013: the nearest any structure KPI comes to holding early, on the one strategy in the stable that is *not* regime-gated (v1, untuned). Doesn't clear Bonferroni, and the baseline ends 2025-12; if the EMA strategy ever gets a longer baseline, re-cut this before anything else. At 12:00/eod the gx_* family holds as usual and the structure rows trail it.
- **value-rotation** (111 days): zero holds. `st_bias`@12:00 leans *negative* (ρ −.227, luck .014) — up-structure days are bad for a rotation strategy, directionally sensible, statistically unproven.

The texture grid stays inconsistent across strategies: profile-pullback and EMA-pullback pay better on *clean* trend_up days ($262 vs $98; $293 vs $159), the exact opposite of the first two runs. Five strategies, no shared direction — the descriptive-only verdict stands.

## 6. What would change the verdict

- A strategy whose edge is *not* already regime-gated (the upper-band family is) showing structure holds at 09:45/10:30.
- `chop_occ_rth`'s 09:45 near-miss (luck .056) firming up on a future, longer baseline — worth a passive re-look at the next re-run, not a build.
- Any use of `st_bias_age_min` as *context* for the re-entry knob (the one passing A/B) — that cut lives in the re-entry cohort, not on these day-level boards, and was explicitly out of scope here.
