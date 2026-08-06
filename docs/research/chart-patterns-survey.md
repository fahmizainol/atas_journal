# Classic Chart Patterns as Data — Encoding & Empirical-Edge Survey

- **Date:** 2026-07-27
- **Research question:** For the *classic multi-swing chart-pattern family* — head-and-shoulders, double tops/bottoms, triangles, wedges, flags, pennants, rectangles, cup-and-handle, broadening formations (distinct from single-bar *candlestick* patterns, which the [price-action-to-data survey](price-action-to-data-survey.md) already covered) — **(1)** how do you encode these fuzzy geometric shapes into causal, non-repainting columns, and **(2)** do peer-reviewed and high-trust practitioner sources find they predict returns? Deliverable: an honest prior on whether this family is any different from the geometry reads (BOS/CHoCH, VP-levels, candlesticks) that keep coming back NULL in this journal.
- **Type:** literature survey + honest-prior gap analysis. No engine run, no A/B. Every Part-A/B factual anchor is cited to a **primary peer-reviewed source**, fetched and text-verified under 3-vote adversarial verification (23 of 25 candidate claims confirmed 3-0; 2 killed). Part-C mapping onto this journal is synthesis.
- **Method note:** produced by the deep-research harness — 5 search angles, 17 sources fetched, 67 claims extracted, top 25 verified. Two evidentiary tiers below: **[VERIFIED]** = 3-0 against a primary PDF; **[SYNTHESIS]** = my reasoning over the verified facts.

---

## TL;DR

- **The encoding problem is solved and has a canonical answer.** Lo, Mamaysky & Wang (2000), *Foundations of Technical Analysis* (Journal of Finance) is the reference algorithm: fit a **Nadaraya-Watson kernel-regression smoother** to price, find local maxima/minima on the *smoothed* curve, then define each pattern as a set of geometric relations among those extrema. It formalizes exactly **10 patterns** (H&S + inverse, double top/bottom, triangle top/bottom, rectangle top/bottom, broadening top/bottom). Two refinement lineages exist: **swing/ZigZag pivot geometry** with volatility-scaled thresholds (Chang-Osler; Savin-Weller-Zvingelis; Chong-Poon) and, separately, **render-to-image + CNN** (Jiang-Kelly-Xiu). Wedges, flags, pennants, cup-and-handle are **not** in the rigorous literature at all.
- **The empirical edge is weak, hedged, and structurally identical to the NULLs this journal keeps finding.** LMW's own conclusion is "incremental *information*, concentrated in Nasdaq… does *not* necessarily imply excess trading profits." Every rigorous *standalone-profitability* test of head-and-shoulders finds it unprofitable (Savin-Weller-Zvingelis, US equities) or profitable in only **2 of 6** currencies and **dominated by far simpler MA/momentum rules** (Chang-Osler, FX).
- **Data-snooping is the killer, and it hits mature liquid markets hardest.** Once you correct for the size of the rule search space (White's Reality Check / Hansen's SPA), Hsu-Kuan find **no** significantly profitable chart-pattern rule survives for the **DJIA or S&P 500** — edges persist only in "young" markets (Nasdaq Composite, Russell 2000). Sullivan-Timmermann-White show the best in-sample rule going insignificant out-of-sample (snooping-adjusted p = 0.99 vs unadjusted ≈ 0.000). **A liquid, mature, efficient market is exactly where the pattern edge vanishes — and intraday NQ is the closest analogue to that regime.**
- **The modern image-CNN lineage is real but marginal and off-domain.** Jiang-Kelly-Xiu report OOS directional accuracy ~53% (their own words: a "1%–2%" edge) and headline weekly Sharpes the authors *explicitly flag as non-tradable demonstrations*. It's US **monthly cross-sectional equities**, not intraday single-instrument futures — external validity to NQ is unestablished.
- **Honest prior for this journal:** the classic chart-pattern family is **not** materially different from the geometry reads already found NULL here. If anything on the list clears the bar, it's **head-and-shoulders** (the only pattern with a repeated *in-sample information* result) and **double bottoms** (LMW's other named-informative pattern) — but even H&S was dominated by simpler momentum rules and dies in mature markets. Recommendation: **do not build a pattern strategy.** Treat the whole family as low-prior *encode-then-race-against-a-drift-null* null-checks, and only H&S / double-bottom are worth even that.

---

## Part A — How the shapes get encoded (verified)

### A1. Lo-Mamaysky-Wang (2000) — the canonical kernel-regression encoding **[VERIFIED, 3-0]**

The most-cited algorithmic-TA paper. A three-step pipeline ([NBER w7613 full PDF](https://www.nber.org/system/files/working_papers/w7613/w7613.pdf); [Penn mirror](https://www.cis.upenn.edu/~mkearns/teaching/cis700/lo.pdf)):

1. **Define** each pattern by its geometric properties — a required *sequence of local maxima and minima* (e.g. H&S = five extrema E1…E5 with the middle peak highest and the two outer peaks roughly level).
2. **Smooth** the raw price series with a **nonparametric Nadaraya-Watson kernel regression** so extrema can be found numerically instead of on noisy raw ticks. (Rolling 35-day window, completion lag 3.)
3. **Scan** the smoothed curve for occurrences of each pattern's extrema template.

It formalizes **exactly 10 patterns**: head-and-shoulders (HS) + inverse (IHS), broadening top/bottom (BTOP/BBOT), triangle top/bottom (TTOP/TBOT), rectangle top/bottom (RTOP/RBOT), double top/bottom (DTOP/DBOT). **Wedges, flags, pennants, and cup-and-handle are not among them** — a gap that persists through the entire rigorous literature.

*Repainting hazard:* the smoother's most recent extrema are unstable until enough future bars arrive (the completion lag exists precisely for this). Any detector that reads a kernel extremum "at bar *t*" before the lag has elapsed **leaks** — the same non-causal trap as naïve ZigZag (see [price-action-to-data-survey §B2](price-action-to-data-survey.md)).

### A2. Swing/ZigZag pivot geometry with volatility-scaled thresholds — Chang-Osler **[VERIFIED, 3-0]**

The canonical worked example of a **causal, non-repainting** detector. Chang & Osler (1999), *Methodical Madness*, Economic Journal ([primary PDF](http://reversal-patterns.technicalanalysis.org.uk/ChOs99.pdf)):

- Trace a **ZigZag of local extrema** — a peak is a local max at least *x%* above the prior trough.
- **Scan the data 10 times** at volatility-scaled cutoffs: {1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5} × the daily-return σ of that instrument.
- **Enter only on confirmed neckline penetration**, with an explicit look-ahead safeguard: because extrema are only identified *after* they occur, entry waits until the cutoff is reached so a position never benefits from future information.

This is the reference pattern for turning a fuzzy shape into a column that respects causality — and it names the specific repainting hazard: **right-shoulder / neckline confirmation lag** means the "pattern" is only knowable some bars after its apparent completion. (Directly analogous to this journal's `causal_zigzag`.)

### A3. Kernel smoothing + swing-geometry rules + a context filter — Chong-Poon / Savin-Weller-Zvingelis **[VERIFIED, 3-0]**

A refinement lineage that keeps A1's kernel smoothing but adds magnitude/asymmetry rules and — critically — a **location filter**. Chong & Poon (2014), *A new recognition algorithm for head-and-shoulders* ([ResearchGate / MPRA 60825](https://www.researchgate.net/publication/319191114_A_new_recognition_algorithm_for_head-and-shoulders_price_patterns)):

- Nadaraya-Watson smoothing over a **63-day** rolling window (bandwidth = LOOCV-optimal × {1.5, 2, 2.5}).
- Detect 5–7 shoulder/head/neckline extrema (E1–E6) with LMW/SWZ magnitude & asymmetry rules (R1–R10).
- **Novel contribution — a trend-position filter (R10a-c, 150/250-day SMA):** discard H&S patterns sitting in the *wrong location of the trend*, because the earlier LMW/SWZ algorithms "might wrongly identify such patterns at the bottom of the market."

**Encoding lesson worth keeping:** context/location is *load-bearing*, not just shape. The same H&S geometry means opposite things at a top vs a bottom; an encoder that scores geometry alone manufactures false positives. (This mirrors the journal's own repeated finding that *developing-vs-static* context and regime split outcomes where raw geometry does not.)

### A4. Render-to-image + CNN — Jiang-Kelly-Xiu **[VERIFIED, 3-0]**

A separate modern lineage that **doesn't hand-craft patterns at all**. *(Re-)Imag(in)ing Price Trends*, Journal of Finance 78(6), 2023 ([primary PDF](https://www.aidf.nus.edu.sg/wp-content/uploads/2022/02/Xiu-Re-Imagining-Price-Trends.pdf)):

- Render OHLC + volume + a moving-average line into **black-and-white images** over 5/20/60-day windows.
- Train a **CNN** to classify the direction of future returns — the model *learns* predictive shapes instead of testing pre-specified templates.

Empirics in Part B4. The GAF/Gramian-imaging and YOLO-style chart-pattern object-detection methods named in the research brief were **not** corroborated by any verified source here — only this grayscale-CNN approach is documented. Treat GAF/YOLO as plausible-but-uncited.

---

## Part B — Does any of it predict returns? (verified)

### B1. LMW's own verdict: incremental *information*, not tradable *profit* **[VERIFIED, 3-0]**

LMW test by comparing the **unconditional** distribution of daily US-stock returns (1962–1996) against the distribution **conditioned on a pattern occurrence** (Kolmogorov-Smirnov + goodness-of-fit). Verbatim from the abstract: "several technical indicators do provide incremental information and may have some practical value," *"especially for Nasdaq stocks. While this does not necessarily imply that technical analysis can be used to generate excess trading profits…"* And in the body: *"patterns that are optimal for detecting statistical anomalies need not be optimal for indicating trading profits."*

So the founding affirmative result is **real but hedged**: statistical information content, concentrated in the least-efficient (Nasdaq) names, explicitly *not* a claim of a net-of-cost tradable edge.

### B2. Head-and-shoulders, tested rigorously, standalone-unprofitable **[VERIFIED, 3-0]**

- **US equities — Savin-Weller-Zvingelis (2007)**, J. Financial Econometrics 5(2), S&P 500 + Russell 2000, 1990-1999 ([abstract](https://academic.oup.com/jfec/article-abstract/5/2/243/785044)): "little or no support for the profitability of a **stand-alone** trading strategy," *yet* "strong evidence that the pattern had power to predict excess returns" (a *conditioned* strategy earned ~5-7%/yr risk-adjusted). The recurring **"informative-but-not-tradable" split** — and it's in-sample using the LMW algorithm, so data-snooping/OOS exposure remains.
- **FX — Chang-Osler (1999)** ([PDF](http://reversal-patterns.technicalanalysis.org.uk/ChOs99.pdf)): H&S profitable and significant (vs a 10,000-run random-walk bootstrap) for **only 2 of 6** dollar rates (mark, yen); the other four were insignificant, with the **pound and Canadian dollar actually negative**. Even where profitable, H&S was **dominated by far simpler MA-oscillator and momentum rules** (significant at 1% in 45 of 48 cases; H&S Sharpe ≈ 0.32 vs simpler rules 0.49-0.95). A 2-of-6 hit rate is itself what multiple-comparisons noise looks like.

### B3. The data-snooping guillotine — and it falls hardest on mature markets **[VERIFIED, 3-0]**

- **Park & Irwin (2007)**, the canonical TA-profitability survey ([J. Economic Surveys 21(4)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x)): of 95 modern studies, 56 positive / 20 negative / 19 mixed — *but* the authors immediately attribute the majority-positive tally to **data snooping, ex-post selection of rules/search technologies, and poor risk/cost treatment**, and conclude the literature does **not** provide conclusive evidence of profitability. **The raw majority-positive count is explicitly not a reliable prior.**
- **Sullivan-Timmermann-White (1999)**, J. Finance 54(5) ([DOI](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163)): the best rule selected as of 1986 is **insignificant in genuine 1987-1996 OOS** data, and the snooping correction is enormous — for the S&P Sharpe criterion the **snooping-adjusted p-value is 0.99 vs an unadjusted single-rule p ≈ 0.000**. Ignoring the search-space size grossly overstates significance. *This is the cleanest cautionary template for any encode-and-race-against-null work.*
- **Hsu-Kuan (2005)**, J. Financial Econometrics 3(4) ([PDF](https://homepage.ntu.edu.tw/~ckuan/pdf/snoop01.pdf)): machine-encoded the classic patterns as explicit rule classes in a **39,832-rule universe** — H&S (1,200 rules), triangles (720), rectangles (2,160), double tops/bottoms (2,160), broadening (720). After White's Reality Check + Hansen's SPA correction, **no significantly profitable rule survives for the DJIA or S&P 500**; profitable rules persist only for the "young" markets Nasdaq Composite and Russell 2000. **The efficient, liquid, mature market — the closest analogue to NQ — is precisely where the edge disappears.**

### B4. The modern image-CNN result — marginal and off-domain **[VERIFIED, 3-0]**

Jiang-Kelly-Xiu ([PDF](https://www.aidf.nus.edu.sg/wp-content/uploads/2022/02/Xiu-Re-Imagining-Price-Trends.pdf)): OOS directional accuracy **exceeds 53%** for one-month returns — a statistically significant but **marginal** improvement over momentum/short-term-reversal (the authors themselves peg the edge at "1% to 2%"). Image-based decile long-short spreads earn high reported OOS Sharpes that **decay sharply** with holding period and value-weighting: up to 2.4 EW / 0.5 VW monthly, up to 7.2 EW / 1.5 VW weekly — and **the authors explicitly flag the weekly figures as a demonstration of predictive strength, NOT practically achievable returns.** Everything is US **monthly cross-sectional equities**; the intraday single-instrument-futures analogue is unestablished.

---

## Part C — Synthesis: mapping onto this journal

### C1. Where each pattern-family method lands vs the journal's own track record **[SYNTHESIS]**

| Chart-pattern method | Journal analogue already tested | Literature verdict | Prior for NQ |
|---|---|---|---|
| LMW kernel-extrema patterns (H&S, DT/DB, triangle, rect, broadening) | BOS/CHoCH swing-structure encoding (forward-NULL at every scale) | Incremental *info*, not profit; dies in mature mkts | **Low** |
| ZigZag/swing trendline geometry | `causal_zigzag`, ATR swing tiers (encoding good, break signal NULL) | Chang-Osler: dominated by simpler momentum | **Low** |
| Kernel + **context/location filter** (Chong-Poon) | developing-vs-static refs, regime classifier (the surviving detectors) | Filter is load-bearing; shape alone = false positives | context matters, shape doesn't |
| Image → CNN (Jiang-Kelly-Xiu) | (untried) | ~53% acc, non-tradable weekly Sharpes, equities-monthly | **Low for intraday** |
| Bulkowski Encyclopedia win-rates | (practitioner tables, unaudited) | **No vetted rigor** — see caveat | practitioner claim only |

**The through-line is the same one already carved into this journal's memory:** *raw geometry has no directional edge in liquid markets; context/regime/participation does.* The classic chart-pattern family is the single most-tested corner of that geometry world, and it fails in exactly the market regime (mature, liquid, efficient) that intraday NQ most resembles. Nothing here contradicts the journal's BOS/CHoCH-NULL, VP-geometry-NULL, candlestick-NULL results — it **replicates** them at book scale.

### C2. Ranked prior — what, if anything, is worth encode-and-race **[SYNTHESIS]**

1. **Head-and-shoulders + inverse H&S** — the *only* pattern with a repeated in-sample *information* result (LMW, SWZ, and the profitable-in-2-currencies Chang-Osler). Still: standalone-unprofitable, dominated by momentum, dead in mature markets. **Worth a single cheap null-check, not a build.** Encode via the Chang-Osler causal ZigZag + volatility-scaled cutoff (non-repainting by construction) and race the neckline-break entry against a drift-long null, exactly like the [structure-events study](market-structure-events.md).
2. **Double bottom** (and to a lesser extent double top) — LMW's other named-informative pattern; cheapest to encode (two lows within a tolerance band + intervening peak). Same treatment: one null-check, low prior.
3. **Triangles / rectangles / broadening** — carried the *least* information in LMW and were swept up in the Hsu-Kuan mature-market null. **Skip** unless a null-check on H&S/DB surprises to the upside.
4. **Wedges, flags, pennants, cup-and-handle** — **no rigorous evidence either way**; absent from LMW/Hsu-Kuan/SWZ entirely. Purely practitioner lore. Lowest prior; do not encode first.
5. **Image-CNN** — interesting infrastructure, but the honest read is it's a *monthly cross-sectional equity* result with a 1-2% edge; porting it to intraday single-instrument NQ is a research programme, not a null-check. Park it behind the triple-barrier/meta-labeling work already ranked higher in the [price-action-to-data survey](price-action-to-data-survey.md).

**Single most defensible recommendation:** do **not** build a chart-pattern strategy. If curiosity demands a test, run **one** encode-then-race-against-a-drift-null on **head-and-shoulders** using a Chang-Osler-style causal detector, with an explicit low prior and the mature-market null (DJIA/S&P) as the base rate. Expect NULL. The journal's finite research budget is better spent on the quant-lineage gaps (triple-barrier + meta-labeling, imbalance bars, frac-diff) that are *orthogonal* to the geometry families rather than yet another member of the family that keeps dying.

---

## Caveats

- **Bulkowski did not survive verification.** The *Encyclopedia of Chart Patterns* win-rate/target statistics — the practitioner benchmark the brief asked for — produced **no vetted claim**. Treat any Bulkowski figure as a **practitioner claim of unknown rigor** with known selection-bias problems: hand-identified patterns, no OOS holdout, survivorship in the illustrative examples. Not a verified fact.
- **GAF/Gramian imaging and YOLO chart-object-detection are uncited here.** Only the Jiang-Kelly-Xiu grayscale-CNN approach was corroborated. The other image methods named in the brief are plausible but unverified in this pass.
- **Wedges, flags, pennants, cup-and-handle are evidence-free**, not evidence-against. The rigorous literature simply never encoded them. "Low prior" for these rests on *analogy* to the tested patterns, not a direct null.
- **Every profitability result is equities or FX; none is intraday index futures.** All external validity to NQ is inference. The mature-market null (DJIA/S&P 500) is the closest analogue and points skeptical, but liquid intraday futures were not tested by any source here.
- **The affirmative "information" findings inherit data-snooping exposure** the standalone-profitability nulls do not — LMW and SWZ are in-sample and use the same kernel algorithm.
- **Two claims were killed in verification** (flagged for honesty, do not lean on either): (a) that young-market rules *also* beat buy-and-hold net-of-cost in a reserved OOS window — **refuted 0-3**; (b) that Chong-Poon's filter improvements are economically tiny/inside-noise — **1-2, unresolved**.

---

## Open questions

1. Does a rigorous, non-repainting, snooping-corrected test of these patterns on **intraday index futures** (the actual NQ use-case) match the mature-market equity null, or does intraday microstructure change the answer? The entire rigorous literature is daily equities/FX.
2. Do the image/CNN results survive at **intraday horizons and realistic futures costs**? The authors' own weekly Sharpes are non-tradable and everything is monthly cross-sectional equities.
3. Is even **H&S** worth the build given it was dominated by simpler MA/momentum rules and vanishes in mature markets — i.e., would a causal H&S detector beat the drift-long null this journal already races everything against? The honest prior says no.

---

## Primary sources

- Lo, Mamaysky & Wang (2000), *Foundations of Technical Analysis*, J. Finance 55:1705-1765 — [NBER w7613](https://www.nber.org/system/files/working_papers/w7613/w7613.pdf) · [Penn mirror](https://www.cis.upenn.edu/~mkearns/teaching/cis700/lo.pdf)
- Chang & Osler (1999), *Methodical Madness: Technical Analysis and the Irrationality of Exchange-Rate Forecasts*, Economic Journal — [PDF](http://reversal-patterns.technicalanalysis.org.uk/ChOs99.pdf)
- Savin, Weller & Zvingelis (2007), *The Predictive Power of "Head-and-Shoulders" Price Patterns*, J. Financial Econometrics 5(2):243-265 — [abstract](https://academic.oup.com/jfec/article-abstract/5/2/243/785044)
- Chong & Poon (2014/2017), *A new recognition algorithm for head-and-shoulders price patterns* — [MPRA 60825 / ResearchGate](https://www.researchgate.net/publication/319191114_A_new_recognition_algorithm_for_head-and-shoulders_price_patterns)
- Park & Irwin (2007), *What Do We Know About the Profitability of Technical Analysis?*, J. Economic Surveys 21(4):786-826 — [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x)
- Sullivan, Timmermann & White (1999), *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, J. Finance 54(5) — [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163)
- Hsu & Kuan (2005), *Reexamining the Profitability of Technical Analysis with Data Snooping Checks*, J. Financial Econometrics 3(4):606-628 — [PDF](https://homepage.ntu.edu.tw/~ckuan/pdf/snoop01.pdf)
- Jiang, Kelly & Xiu (2023), *(Re-)Imag(in)ing Price Trends*, J. Finance 78(6) — [PDF](https://www.aidf.nus.edu.sg/wp-content/uploads/2022/02/Xiu-Re-Imagining-Price-Trends.pdf)
