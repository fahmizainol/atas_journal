# ML-for-Trading Literature Survey (2024–2026) — What Transfers to This Journal

- **Date:** 2026-07-27
- **Research question:** What does the current (2024–2026) academic + practitioner ML-for-trading literature actually offer a journal that already trades intraday NQ futures with hypothesis-first Lab studies and strict out-of-sample discipline? Which threads are methodology upgrades worth adopting, which are confirmatory nulls, and which are low-prior directions to skip?
- **Type:** literature survey + relevance triage, not a backtest. No engine run, no A/B. Verified factual anchors are cited to primary sources; the journal-mapping and priors are synthesis. Five papers/clusters deep-read by four parallel research agents plus one hand-read paper.
- **Related:** [[price-action-to-data-survey]] (the encoding-methods survey this extends), [[triple-barrier-driftfade-study]] (the meta-labeling null this re-examines), [[gate-robustness-scorecard]] (the real-vs-luck discipline the overfitting papers formalize).

---

## TL;DR

- **The single highest-ROI takeaway is not a model — it's two nearly-free scoring upgrades to the existing A/B ledger: the Deflated Sharpe Ratio (DSR) and the Probability of Backtest Overfitting (PBO).** They formalize the journal's empirical "1 pass / 12+ fails" record into an explicit selection-bias correction, and would likely have flagged several dead gates *before* the OOS run. Pure post-hoc statistics, no engine change.
- **The near-exact-instrument paper (LSTM vs Gradient Boosting on intraday MNQ) is a clean confirmatory null.** At ~10³ sessions / 5-min bars, a sequence model gives **zero lift** over gradient boosting or even the base rate — and the honest failure mode is *under-fit*, not overfit. Don't reach for LSTMs/transformers on the order-flow pipeline until there are ~10⁵–10⁶+ samples (tick data, multi-instrument pooling, or a fine-tuned foundation model).
- **Trend-scanning labeling is a real, cheap upgrade over fixed triple-barrier — but it will not overturn the meta-labeling null.** That null (AUC .70 in-sample → .527 OOS) was an OOS-*generalization* failure; trend-scanning fixes label *misspecification* (a different disease) and may cosmetically *widen* the in-sample/OOS gap via ex-post horizon selection.
- **LOB deep-learning models (TLOB, LiT, DeepLOB) are a low-prior direction — they are built on full L2 book depth, which this journal does not have** (it works from per-second tape aggressor sums). No direct use. The one reusable artifact is the microstructural guide's *evaluation philosophy* (score the probability of a complete cost-clearing round-trip, not per-bar accuracy) — which the journal's gate-robustness discipline already embodies.
- **Cross-cutting theme, consistent across all five threads:** the literature keeps rediscovering *statistically significant ≠ economically significant*. Every serious 2024–2026 paper leads with overfitting/regime/cost caveats. This journal is already on the right side of that divide; the value here is in **sharpening the audit**, not chasing new model families.

---

## Part 1 — Backtest overfitting & OOS validation *(highest-ROI thread)*

The journal's failure mode — "most in-sample edges die OOS" — *is* the false-discovery problem these papers target. The journal already does OOS replication; what it lacks is **(a)** a distributional OOS estimate and **(b)** an explicit trials/selection penalty.

### 1a. CPCV beats walk-forward in a synthetic controlled environment
Arian, Norouzi M., Seco — *Knowledge-Based Systems* (Elsevier), 2024. Paywalled: [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110) · open preprint: [SSRN 4686376](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4686376).

Head-to-head comparison of Walk-Forward, K-Fold, Purged K-Fold, and **Combinatorial Purged Cross-Validation (CPCV)** — plus novel Bagged/Adaptive CPCV — in a *synthetic environment* where price paths come from known processes (Heston stochastic-vol, Merton jump-diffusion, drift-burst), so false discoveries can be *counted*.

- **CPCV wins:** lowest Probability of Backtest Overfitting (PBO), strongest Deflated Sharpe test statistic.
- **Walk-forward is the weakest** at preventing false discoveries — it produces only *one* chronological train/test path, so both the point estimate and its variance are noisy and regime-sensitive. CPCV averages over many purged/embargoed paths → a *distribution* of OOS Sharpes that exposes overfit configs.
- *Verification flag:* full text 403'd for the agent; findings are from the consistent indexed abstract/snippets. Numeric magnitudes of CPCV's advantage not independently confirmed.

### 1b. GT-Score — bake anti-overfitting into the objective
Sheppert — arXiv [2602.00080](https://arxiv.org/abs/2602.00080) (Jan 2026); *J. Risk Financial Management* [19(1):60](https://www.mdpi.com/1911-8074/19/1/60). *(PDF fully read.)*

Instead of correcting significance *after* the search, GT-Score embeds "significant AND consistent AND downside-controlled" into the optimizer's objective:

> **GT-Score = μ · ln(z) · r² / σ_d**
> μ = mean return/trade; z = excess-return t-stat vs buy-and-hold (**ln(z)** is a significance gate); r² = equity-curve consistency (punishes outlier-dependent edges); σ_d = downside deviation. Guardrail: ≥50 trades or the config is penalized.

- **Result:** does *not* raise raw OOS return (baselines beat it slightly, p<0.001 but d<0.1). Its win is **generalization** — it retains ~2× as much training performance OOS (generalization ratio +56% Monte-Carlo, +98% walk-forward). It explicitly trades raw return for reliability.
- **Caveats:** daily-equities single-author *Communication*; the z-term assumes ~i.i.d. Gaussian trade returns (mis-specified under intraday fat tails); the author himself calls it a heuristic gate, not a valid test.

### 1c. Reference anchors — López de Prado
- **Deflated Sharpe Ratio (DSR):** Bailey & López de Prado 2014, [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551). Probability that true Sharpe > 0 **after** correcting for (i) number of trials, (ii) non-normality (skew/kurtosis), (iii) sample length. Compares observed Sharpe to an expected-maximum benchmark SR₀ = the Sharpe you'd expect from the *best of N random trials*.
- **CPCV + PBO:** *Advances in Financial Machine Learning* (2018). Purging (drop training labels overlapping the test window) + embargo (buffer after each test block) kill serial-correlation leakage. PBO = fraction of splits where the in-sample-best config underperforms the OOS median.

### Ranked, actionable shortlist
1. **DSR + honest trial-counting** *(highest ROI, cheapest).* The journal runs many gate/knob A/Bs against one baseline — a textbook multiple-testing setup. Log N = configs ever tried on a dataset, compute SR₀ from N and the variance of candidate Sharpes, require survivors to clear it. Formalizes the "1 pass / 12 fails" ledger; pure post-hoc statistic.
2. **PBO on existing splits** *(cheap, complements #1).* Turns the walk-forward into an overfitting *probability* rather than a pass/fail anecdote. Low marginal cost given existing infrastructure.
3. **CPCV with purging + embargo** *(biggest structural upgrade, moderate effort).* The paper's actual winner. For intraday NQ the **embargo is non-negotiable** (overnight-anchor and multi-bar-label leakage is real — cf. [[ticks-1600-1700-gap]] and the drift-fade lookahead in [[drift-fade-market-structure-study]]). Cost: re-architect the runner for combinatorial purged splits. Do this *after* #1/#2 so CPCV's many paths feed a DSR/PBO that's already trustworthy.
4. **GT-Score as a custom selection metric** *(experimental, adapt don't adopt).* Conceptually aligned (reward consistency + downside control, gate on significance) but single daily-equities paper, lowers raw return by design, z-gate mis-specified for NQ. Treat as inspiration for a composite selection metric, not a DSR/PBO replacement.

---

## Part 2 — LSTM vs Gradient Boosting on intraday MNQ *(confirmatory null, near-exact instrument)*

Mesfin — *Sequential Structure in Intraday Futures Data: LSTM vs Gradient Boosting on MNQ*, arXiv [2605.17724](https://arxiv.org/abs/2605.17724) (May 2026). **A classification study, not a trading study** — it never simulates PnL.

- **Setup:** MNQ (Micro E-mini Nasdaq), RTH only, Dec 2021–Sep 2025, 72,604 five-minute bars → 944 sessions. Label = binary rest-of-session direction (close vs 10:30 open ±10pt ≈ 1 ATR, base rate 51.8%) from first-60-min features. GB = `HistGradientBoostingClassifier` (daily 29-feat / intraday 30-feat / vol-adj); LSTM = deliberately minimal (16 units, dropout 0.3) fed only the 12-bar return sequence. Expanding-window walk-forward, 3 folds, test years fully held out, permutation test for significance.
- **Result: neither model beats the other — both fail.** Combined OOS accuracy 50.0–50.9% (GB) and 50.6% (LSTM), all *below* the 51.8% base rate. Best single fold 54.76% but permutation p=0.135 (fails). LSTM p=0.515 (literally the median of the null). LSTM calibration flat (P(pos)=0.501) — it learned nothing.
- **Honest failure mode = under-fit, not overfit.** 944 samples can't support a sequence model. Author frames it as a sample-size lower bound: Kronos (the foundation-model motivation) used ~9.2M bars, ~127× more; estimated failure→success transition needs ~0.5–5M bars.
- **So-what:** On a single instrument at 5-min resolution, sequence models give **zero lift** over gradient boosting or the base rate. Don't add LSTMs/transformers to the order-flow pipeline until ~10⁵–10⁶+ samples are available (tick data, multi-instrument ES/NQ/MES/MNQ pooling, or transfer-learning from a pretrained foundation model). With the journal's OOS discipline this is a clean confirmatory null, not a lead.

---

## Part 3 — Trend-scanning & meta-labeling refinements *(cheap label upgrade; will not rescue the meta-labeling null)*

Extends [[triple-barrier-driftfade-study]] (engine-outcome meta-label = RESOLVED NULL: AUC .70 in-sample → .527 OOS, shuffle p≈.17).

### 3a. Trend-scanning labeling
For each bar, fit OLS of log-price on time over a grid of look-forward windows (5…99 bars); pick the window with **max |t-statistic of the slope|** — that horizon is the "most significant trend." Label = sign of the t-value → {−1,+1}, with a min-|t| threshold giving an organic {−1,0,+1} hold class. (López de Prado, *Machine Learning for Asset Managers*, Lecture 3.) Sources: [MQL5 Part 3](https://www.mql5.com/en/articles/19253) · [mlfinlab docs](https://random-docs.readthedocs.io/en/latest/implementations/labeling_trend_scanning.html).

- **What it fixes vs fixed triple-barrier:** removes the two arbitrary hyperparameters the prior study already distrusted (fixed horizon + fixed barrier widths). Horizon is chosen per-bar by evidence; trending regimes select long windows, consolidation yields organic "hold." This is *sympathetic* to the load-bearing wide-trailing-stop finding — labels breathe to the trend's natural length instead of clipping at a fixed barrier.
- **Cost:** low-to-moderate — a labeling function, not an engine change (loop over window lengths, OLS slope+SE per event bar; Numba/`mlfinlab` reference impl; add a volatility mask).
- **Pitfalls:** ex-post horizon *selection* uses future prices — legitimate for a label but makes labels optimistic (marks the cleanest trend that *actually occurred*, not what a model sees at decision time); boundary leakage into features; low-vol spurious trends. *The MQL5 article's performance numbers are self-admittedly miscalculated — use it for mechanism, not results.*

### 3b. Meta-labeling refinements (Hudson & Thames / JFDS, 2022–2026)
- **Framework** (Meyer et al. JFDS 2022): meta-labeling only helps when the **primary is high-recall / low-precision**. An already-precise primary + meta-layer adds noise.
- **Three components:** the meta-model must be fed **orthogonal** information (regime, vol, order-flow, time-of-day) — feeding it the same features as the primary is exactly where in-sample fit without OOS lift comes from.
- **Ensemble meta-labeling** ([UCL Discovery](https://discovery.ucl.ac.uk/id/eprint/10169066/)): the closest thing to an OOS-decay remedy — bagged/heterogeneous meta-models generalize across regimes better than a single fitted classifier. Variance reduction, not alpha creation.
- **Calibration + sizing** ([pm-research](https://www.pm-research.com/content/iijjfds/5/2/23)): Platt/isotonic calibration before sizing improves Sharpe/drawdown — but only *given a real edge*; it won't resurrect an OOS-AUC-.5 meta-model.
- **Counterweight:** [Why Meta-Labeling Is Not a Silver Bullet (QuantConnect)](https://www.quantconnect.com/forum/discussion/14706/) — for an *ML* primary, a meta-model can only help if it extracts info the primary missed; empirically it *widened* the performance range while *lowering* average Sharpe. Its value is over **discretionary/rule-based** primaries and genuine **high-recall** primaries. The journal's engine-outcome meta-label is exactly the ML-on-ML case it warns against.

### Verdict
**Implement trend-scanning as a principled *labeling* upgrade for future directional studies — but do not expect it to overturn the meta-labeling null.** That null was OOS-generalization failure; trend-scanning addresses label misspecification (different disease) and may cosmetically worsen the in-sample/OOS gap. The evidence-backed fixes for the specific .70→.527 collapse are **orthogonal meta-features + purged CV + uniqueness sample-weighting + calibration + ensembling** — not a new label. Cheapest confirmation: relabel one existing study with trend-scanning and re-run the existing OOS harness *before* building anything.

---

## Part 4 — LOB deep-learning models *(low-prior; data gap is fundamental)*

Three 2025 models, evaluated for *conceptual* transfer only — the journal has no L2 book depth, it works from per-second tape aggressor sums.

| Model | Depth required | Uses tape/aggressor? | Usable on per-second sums? |
|---|---|---|---|
| **TLOB** (dual-attention transformer, [arXiv 2502.15757](https://arxiv.org/html/2502.15757v3)) | 10 levels (L2) | No | **No** |
| **LiT** (LOB transformer, [Frontiers AI 2025](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1616485/full)) | 20 levels (L2) | No | **No** |
| **DeepLOB / microstructural guide** ([arXiv 2403.09267](https://arxiv.org/abs/2403.09267), *Quant. Finance* 2025; [LOBFrame code](https://github.com/FinancialComputingUCL/LOBFrame)) | 10 levels (L2) | No | **No** |

- **The gap is fundamental, not a downsampling problem.** These models' entire signal is the *shape and evolution of resting-order volume across price levels* (queue imbalance, depth gradients, level replenishment). Per-second aggressor sums capture *executed* order flow ("who crossed the spread") — a different, thinner axis with none of the resting-book-shape signal.
- **Horizons are wrong:** TLOB works in *events*, LiT in *300–1000 ms* — HFT territory, not intraday NQ swings.
- **Edges mostly don't survive costs.** TLOB's one cost-aware test (threshold = spread) craters F1 to ~41; LiT runs no cost/PnL analysis at all. The microstructural guide is the credible anchor: forecasting skill does **not** map to profit once you require clearing the spread on a full round-trip, and small-tick names are effectively unpredictable. **FI-2010 is confirmed optimistic** by two of the three papers — discount any headline F1 ≥ 80.
- **What genuinely transfers (conceptual only):**
  1. **TLOB's dual-axis attention** — attend over features, then over time — applies cleanly to a `[seconds × {buy_agg, sell_agg, net, big-lot-participation, CVD, …}]` matrix with no book depth. The abstraction, not the model.
  2. **The microstructural guide's operational metric** — score the *probability of a complete cost-clearing round-trip*, not per-bar accuracy. The journal's gate-robustness/A/B discipline already embodies this; the paper is a citable formalization.
- **Net:** surveyed, low-prior, no build. Fits the existing pattern of VP/VWAP-geometry and order-flow-geometry nulls ([[avwap-reclaim-study]], [[lvn-retrace-continuation-deepdive]]). Any borrowed idea gets validated the journal's way (engine A/B, split-half, ghost cohorts), not adopted on the papers' say-so.

---

## Part 5 — Interpretable hypothesis-driven walk-forward *(methodological mirror)*

*Interpretable Hypothesis-Driven Trading: A Rigorous Walk-Forward Validation Framework for Market Microstructure Signals*, arXiv [2512.12924](https://arxiv.org/html/2512.12924v1). 100 US equities, 2015–2024, daily OHLCV; five hypothesis types (institutional accumulation, flow momentum, mean reversion, breakout, range-bound value); 34 rolling walk-forward folds (252d train / 63d test), strict information-set discipline, realistic costs ($1 + 5bp).

- **Headline:** aggregate return 0.55%/yr, Sharpe 0.33, *statistically insignificant* (t=0.96, p=0.34; permutation p=0.98; only 12% statistical power, ~540 folds needed for 80%). **But regime-split is the finding:** high-vol 2020–2024 Sharpe **1.01** vs low-vol 2015–2019 Sharpe **−0.21**. "Daily microstructure signals require elevated information arrival to function."
- **Why it matters here:** it's a near-perfect mirror of the journal's own methodology (hypothesis-first microstructure signals + strict walk-forward + honest null reporting + regime-dependence + power analysis). Two adoptable habits the journal doesn't currently formalize: **(a) report statistical power** on every A/B (how many trades to detect the claimed effect), and **(b) always regime-split** the aggregate before calling a null — the aggregate p=0.34 hid a real high-vol edge. *Notably, the paper mentions but does not apply DSR/CPCV — reinforcing Part 1's shortlist as the missing piece.*

---

## Consolidated priority list

| # | Action | Effort | Prior | Thread |
|---|---|---|---|---|
| 1 | **Deflated Sharpe Ratio + trial-counting** on the A/B ledger | Low | High | Part 1 |
| 2 | **PBO** on existing OOS splits | Low | High | Part 1 |
| 3 | **Report statistical power + regime-split** on every A/B | Low | High | Parts 1, 5 |
| 4 | **CPCV (purge + embargo)** runner re-architecture | Medium | High | Part 1 |
| 5 | **Trend-scanning labels** for a *new* directional study (not to rescue meta-labeling) | Low-Med | Medium | Part 3 |
| 6 | Dual-axis attention over the aggressor matrix (only after 10⁵+ samples) | High | Low | Parts 2, 4 |
| — | LSTM/transformer on current single-instrument data | — | **Dead** | Part 2 |
| — | LOB depth models on tape-only data | — | **Dead** | Part 4 |

**One-line synthesis:** the literature's gift to this journal is a sharper *audit* (DSR, PBO, CPCV, power, regime-split), not a new model family — every serious 2024–2026 paper independently rediscovers the statistically-significant-≠-economically-significant divide the journal already lives on.
