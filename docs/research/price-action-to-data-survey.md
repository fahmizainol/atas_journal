# Turning "Price Action" into Data — a Survey of Encoding Methods

- **Date:** 2026-07-26
- **Research question:** What is the full landscape of methods for encoding raw price/tape behavior into machine-readable features for quantitative analysis and systematic trading — across **(A)** the quant/ML feature-engineering lineage (López de Prado *Advances in Financial Machine Learning*) and **(B)** the practitioner price-action → code lineage (candlesticks, swings, market structure, volume profile, order flow) — and how does that landscape map onto the methods this NQ-futures journal already uses versus the ones it has **not** yet tried?
- **Type:** literature survey + gap analysis, not a backtest. No engine run, no A/B. The verified factual anchors are cited to primary sources; the practitioner-side taxonomy and the journal-mapping are synthesis.
- **Sources of record:** López de Prado, *Advances in Financial Machine Learning* (Wiley 2018) — [ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf); Easley/López de Prado/O'Hara VPIN — [SSRN 1695596](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596); Andersen & Bondarenko, *VPIN and the Flash Crash* — [JFM 2014](https://www.sciencedirect.com/science/article/abs/pii/S1386418113000189); Amihud (2002) ILLIQ — [paper PDF](https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf); Tharavanij et al., candlestick profitability SET50 — [SAGE Open 2017](https://journals.sagepub.com/doi/pdf/10.1177/2158244017736799); Keogh & Lin, SAX — [UCR SAX page](https://www.cs.ucr.edu/~eamonn/SAX.htm).

---

## TL;DR

- **Two lineages, one goal.** Both worlds answer the same question — *how do you turn a wiggling price/tape stream into columns a model can filter, join, and label?* — but from opposite ends. The **quant/ML lineage** (López de Prado) re-samples the clock itself (information-driven bars), turns price *paths* into labels (triple-barrier / meta-labeling), fixes non-stationarity (fractional differentiation), and reads the tape through microstructure estimators (Kyle/Amihud/VPIN). The **practitioner lineage** hard-codes what a discretionary trader *sees* — candlestick patterns, swing pivots, BOS/CHoCH structure, order blocks / fair-value gaps, volume-profile levels, and footprint/CVD order flow.
- **The published edge asymmetry is stark.** Named-pattern and geometry reads have repeatedly *failed* peer-reviewed tests (candlestick reversal patterns are statistically indistinguishable from zero mean return over a 10-year SET50 study). Order-flow *toxicity* metrics like VPIN are contested — the estimation procedure is real and HFT-applicable, but its headline predictive claim was refuted (VPIN peaked *after* the 2010 Flash Crash, and its signal is mechanically tied to trading intensity).
- **This journal already lives on the surviving side.** Its zz20 pivots, ATR-normalized swing tiers, causal `levels_in_force` volume profile, big-lot participation / CVD / absorption-exhaustion order flow, regime classifier, momentum-favor and overlap/chop bar features, and MFE/MAE excursion labeling are the practitioner lineage done *causally and honestly*. Its own track record echoes the literature: **VP-geometry and named-pattern reads keep coming back NULL; raw participation/order-flow and regime signals survive.**
- **The biggest untried, high-prior gaps are on the quant side:** **triple-barrier + meta-labeling** (turn the existing entries into a size/quality classifier), **information-driven / imbalance bars** (re-sample the clock the engine runs on), and **fractional differentiation** (stationary-but-memory-preserving level features). Untried methods that pattern-match to the journal's *dead* families — **FVG/order-block encoding, matrix-profile/shapelet motif discovery** — carry a low prior and should be tested only as cheap null-checks, not builds.

---

## Part A — The quant/ML feature-engineering lineage (López de Prado)

*Advances in Financial Machine Learning* (Wiley, 2018) is organized so that its early chapters are, quite literally, a catalogue of price-encoding primitives. The verified chapter/section structure below is the backbone of world (A).

### A1. Information-driven bars — re-sampling the clock (Ch. 2)

**Price-action intuition it encodes.** Wall-clock bars (1-min, daily) sample time uniformly, but *information* does not arrive uniformly — most of a session's price discovery happens in bursts. Sampling by activity instead of by time produces bars with better statistical properties (closer to IID, less heteroskedastic).

**How the encoding works.** Chapter 2 (Financial Data Structures), §2.3, explicitly splits bars into **§2.3.1 Standard Bars** and **§2.3.2 Information-Driven Bars** ([ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf)). The family:
- **Tick / volume / dollar bars** — emit a new bar every *N* ticks, *N* contracts, or *$N* traded, instead of every *N* seconds.
- **Tick / volume / dollar imbalance bars** — emit a bar when the running *signed* order-flow imbalance (using the tick rule for sign) exceeds an adaptive expectation; bars close faster when informed one-sided flow dominates.
- **Run bars** — emit a bar when a *run* of same-signed flow exceeds expectation; sensitive to sequences (sweeps), not just net imbalance.

**Known failure modes / critiques.** Bar boundaries become path-dependent and non-uniform in time, which complicates joining to calendar-anchored features and to other instruments; imbalance/run bars have thresholds that are themselves a model choice (the same "the threshold is the model" trap this journal hit with swing-scale sweeps). Parameter drift across regimes means bar frequency is non-stationary.

### A2. Triple-barrier method & meta-labeling — turning paths into labels (Ch. 3)

**Intuition.** A trade outcome is not "return over a fixed horizon" — it's *which barrier the path hits first*: a profit target, a stop, or a time limit. That is exactly how a discretionary trader thinks about a position.

**How the encoding works.** Chapter 3 (**Labeling**) presents **§3.2 Fixed-Time Horizon Method**, **§3.3 Computing Dynamic Thresholds**, **§3.4 The Triple-Barrier Method**, **§3.5 Learning Side and Size**, **§3.6 Meta-Labeling**, **§3.7 How to Use Meta-Labeling** ([ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf)).
- **Triple-barrier:** set an upper (take-profit) and lower (stop) horizontal barrier plus a vertical (time) barrier; label each observation by the first barrier touched. Barriers are usually scaled by a volatility estimate so labels are regime-adaptive.
- **Meta-labeling:** a *primary* model (or rule) decides the **side**; a *secondary* ML model decides **whether to act and how large** — i.e., it labels the primary model's calls as true/false positives and sizes accordingly. It improves precision and F1 without the primary model needing to be re-fit.

**Known failure modes / critiques.** Barrier widths and the volatility scaler are hyperparameters that can be overfit; meta-labeling inherits the primary model's recall ceiling (it can only *filter*, never *add* trades the primary missed); label leakage is easy if barriers use future-scaled volatility.

### A3. Fractional differentiation — stationarity vs. memory (Ch. 5)

**Intuition.** Returns (first differences) are stationary but throw away almost all long-memory information in the price level; raw prices keep the memory but are non-stationary and break most ML/statistical assumptions. There should be a dial *between* them.

**How the encoding works.** Chapter 5 is **Fractionally Differentiated Features**, framed explicitly as **§5.2 The Stationarity vs. Memory Dilemma** and resolved by **§5.6 Stationarity with Maximum Memory Preservation** ([ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf); corroborated by the [O'Reilly Ch. 5 listing](https://www.oreilly.com/library/view/advances-in-financial/9781119482086/c05.xhtml)). Instead of differencing by an integer order *d*=1, difference by a *fractional* order (e.g. *d*=0.35) — the minimum *d* that passes an ADF stationarity test — retaining maximal memory.

**Known failure modes / critiques.** Choice of *d* and the fixed-width window / weight-threshold are tuning knobs; the transformed series is harder to interpret (no longer "returns"); still assumes a single global *d* across regimes.

### A4. Structural-break / CUSUM event sampling (Ch. 2 §2.5)

**Intuition.** Don't feed the model every bar — sample only *when something happens*. Uniform sampling drowns signal in a sea of quiet bars.

**How the encoding works.** §2.5 **Sampling Features** → **§2.5.2 Event-Based Sampling** ([ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf)). A **symmetric CUSUM filter** accumulates signed deviations and emits an event only when the cumulative sum crosses a threshold *h*, resetting after each trigger — a de-noised structural-break detector that decides *which bars become training rows*. (The journal's `overlap_10`/range-compression bars and event-log encodings are cousins of this idea.)

**Known failure modes / critiques.** Threshold *h* is a model choice; CUSUM is direction-agnostic and can fire on noise bursts; event count is regime-dependent (sample-size imbalance across periods).

### A5. Microstructural features — three generations of tape reading (Ch. 19)

Chapter 19 (**Microstructural Features**) organizes order-flow encodings into three historical generations ([ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf)):

**First generation — price sequences.** The **Tick Rule** (§19.3.1): classify each trade's aggressor side by whether it printed above (buy) or below (sell) the previous trade — the workhorse trade-sign classifier. Plus the **Roll model**, **high-low**, and **Corwin-Schultz** spread estimators — effective spread from prices alone.

**Second generation — strategic-trade models.** **Kyle's Lambda** (§19.4.1): price impact per unit signed order flow, from Kyle (1985)'s informed-trader model. **Amihud's Lambda** (§19.4.2). **Hasbrouck's Lambda** (§19.4.3).
- **Amihud's ILLIQ** (2002) is the canonical low-frequency price-impact proxy: the average daily ratio of a stock's **absolute return to its dollar volume** — "the absolute (percentage) price change per dollar of daily trading volume, or the daily price impact of the order flow" ([Amihud 2002](https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf)). It is *deliberately coarse*: Amihud himself notes "there are finer and better measures of illiquidity, such as the bid-ask spread…, transaction-by-transaction market impact or the probability of information-based trading… [but] these measures require a lot of microstructure data that are not available in many stock markets" and do not cover long periods. Coarse-by-design, built from daily price+volume only.

**Third generation — sequential-trade models.** **PIN** (§19.5.1, Easley/O'Hara) and **VPIN** (§19.5.2).
- **VPIN** (Volume-Synchronized Probability of Informed Trading) estimates order-flow **toxicity** from volume imbalance and trade intensity, updated in **volume-time** (a "volume clock") rather than clock-time, and — crucially — "does not require the intermediate estimation of non-observable parameters or the application of numerical methods," making it real-time and HFT-applicable ([SSRN 1695596](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596)). VPIN slices order flow into equal-volume **buckets** — the direct microstructure ancestor of López de Prado's later volume/information-driven bars (same co-author).

**VPIN's known failure modes — a documented, contested critique.** Andersen & Bondarenko, *VPIN and the Flash Crash* (Journal of Financial Markets, 2014) found that **VPIN is a poor predictor of short-run volatility**, that it **reached its all-time high only *after* the May 2010 Flash Crash, not before** (undermining its early-warning claim), and that **its apparent predictive content is a mechanical artifact of its correlation with trading intensity/volume** rather than a distinct toxicity signal ([JFM 2014](https://www.sciencedirect.com/science/article/abs/pii/S1386418113000189)). The original authors published a rejoinder disputing the timing (construction-dependent), so this is a *genuine academic dispute*, not settled consensus — but the estimation-procedure description in the previous paragraph stands regardless; only the *predictive-power* claim is contested. **Lesson for this journal: a signal "built from volume and absolute price moves" can look predictive purely because it co-moves with activity — exactly the "big-lot participation is mechanically tied to volume" caution already in the notes.**

### A6. Symbolic / motif time-series encoding (SAX, PAA, matrix profile, shapelets)

**Intuition.** Reduce a continuous price curve to a short **string of symbols** or a set of recurring **motifs / discriminative sub-shapes**, so you can index, cluster, and pattern-match price action at scale.

**How the encoding works.**
- **SAX** (Symbolic Aggregate approXimation, Keogh & Lin, 2002) is "the first symbolic representation for time series that allows for dimensionality reduction *and* indexing with a lower-bounding distance measure" ([UCR SAX page](https://www.cs.ucr.edu/~eamonn/SAX.htm)). Two stages: **(1) PAA** — z-normalize, split the series into *w* equal segments, replace each with its mean (dimensionality reduction); **(2) Discretization** — map each averaged segment to an alphabet letter via **Gaussian-derived breakpoint tables** (breakpoints chosen so each symbol is equiprobable under N(0,1)). Output: a machine-readable symbol string per window.
- **Matrix profile** — for every subsequence, the distance to its nearest neighbor elsewhere in the series; cheaply surfaces **motifs** (repeated shapes) and **discords** (anomalies).
- **Shapelets** — maximally class-discriminative subsequences learned for a supervised task; a shapelet distance becomes a feature.

**Known failure modes / critiques.** SAX/PAA are lossy (segment means erase intra-segment shape); the Gaussian-equiprobable assumption fits normalized returns better than raw prices; motif/shapelet discovery is prone to finding *statistically inevitable* recurring shapes in any noisy series (the same false-pattern risk that sinks candlestick backtests) and is computationally heavy at tick scale.

---

## Part B — The practitioner price-action → code lineage

This is the world of turning what a discretionary chartist *sees* into columns. The journal already lives here; the survey below names each family, its intuition, its encoding recipe, and its critique.

### B1. Candlestick-pattern detection (TA-Lib and its limits)

**Intuition.** Named OHLC shapes (doji, engulfing, hammer, morning star) signal reversals/continuations.

**Encoding.** **TA-Lib** ships ~60 `CDL*` pattern recognizers that return −100/0/+100 per bar from hard-coded body/wick/gap ratio rules. Cheap, deterministic, widely used as features.

**Critique — the strongest negative result in this survey.** A 10-year (Jul 2006–Jun 2016) study of the 50 largest-cap SET50 stocks found "the mean returns of most bullish and bearish candlestick reversal patterns are **not statistically different from zero**… even the ones with statistically significant returns do have high risks in terms of standard deviations… candlestick patterns **cannot reliably predict market directions**" ([Tharavanij et al., SAGE Open 2017](https://journals.sagepub.com/doi/pdf/10.1177/2158244017736799); consistent with Marshall et al. 2006 on Dow stocks). Additional coding pitfalls: TA-Lib's fixed thresholds are market-agnostic (a "long body" on NQ ≠ on a slow equity), definitions vary between libraries, and pattern detection with any smoothing/look-back can peek. **This is the empirical anchor for the journal's own "named-pattern reads keep coming back NULL" pattern.**

### B2. Swing / pivot detection (ZigZag, fractal pivots, ATR-normalized swings)

**Intuition.** Compress price into an alternating sequence of significant highs and lows.

**Encoding.** **ZigZag** connects reversals exceeding a % or point threshold; **fractal pivots** (Williams) mark a bar higher/lower than its *k* neighbors; **ATR-normalized swings** scale the threshold by volatility so "significant" adapts across regimes.

**Critique — repainting / look-ahead is the central hazard.** The naïve ZigZag is *non-causal*: the last leg repaints until a reversal confirms, so a pivot "at bar *t*" is only knowable in the future. Any backtest that reads a repainting pivot leaks. (This journal's `causal_zigzag` — a pivot exists at bar *t* only *after* confirmation — is the correct fix, and it is already in use.) Threshold choice is a model; too-fine settings redefine structure almost every bar.

### B3. Market-structure encoding — BOS / CHoCH / Smart Money Concepts (ICT)

**Intuition.** Trend = sequence of higher-highs/higher-lows; a **BOS** (break of structure) continues it, a **CHoCH** (change of character) flips it. SMC/ICT adds **order blocks** (last opposing candle before an impulsive move), **fair-value gaps / imbalances** (a 3-bar gap where wicks don't overlap), and **liquidity sweeps** (stop-runs beyond a prior swing).

**Encoding.** Build a swing sequence (B2), run a bias state machine emitting typed BOS/CHoCH events; detect FVGs as a geometric 3-bar wick condition; order blocks as the last down-candle before an up-impulse (and vice versa); sweeps as a wick beyond a swing that closes back inside.

**Critique.** SMC/ICT terms are popular but under-tested; definitions are non-standard across sources (many detectors repaint). This journal's own **BOS/CHoCH study** encoded the event stream cleanly (365 sessions, 24,788 breaks) and found it **forward-NULL at every swing scale** (~49–50% win, MFE≈MAE, barely beating an always-long drift baseline) — a direct, large-sample confirmation that the *encoding is good but the naïve signal is descriptive, not predictive*. FVG/order-block encodings remain **untried** here but share the same geometry-has-no-edge prior.

### B4. Supply/demand & support/resistance zone extraction

**Intuition.** Price reacts at zones where it previously reversed sharply (imbalance/base-then-rally).

**Encoding.** Cluster prior pivots/rejections into horizontal bands; score by touch count, age, freshness, volume-at-level.

**Critique.** Selection is subjective and easy to fit post-hoc. This journal's **stable-level S/R study** found stable/flat developing VP levels do **not** hold better than fresh ones (perm p=1.00; levels break through ~45/55 at every age) — another VP-geometry null.

### B5. Volume profile / Market Profile / TPO

**Intuition (Dalton, *Mind Over Markets* / Steidlmayer).** Price spends most time where the market accepts value; the **POC** (point of control) is the highest-volume price, the **value area** (VAH/VAL) holds ~70% of volume, **HVN/LVN** are high/low-volume nodes, and **developing** (intraday, still forming) vs **static** (prior-session, fixed) profiles behave differently.

**Encoding.** Bin traded volume (or TPO letters) by price to build a histogram; extract POC, VAH/VAL (70% band), and LVN/HVN as local minima/maxima. **Causality matters:** a *developing* POC/VAH read must use only volume up to the current bar (this journal's `levels_in_force` does exactly this).

**Critique.** VP levels are widely believed to be S/R but repeatedly fail forward tests (see B4; the journal's VAH-snap, LVN-retrace, prior-POC-magnet, and weekly-VWAP-context studies are all NULL). The value-area 70% is a convention, not a law; static prior-day levels are day-range-scale objects mismatched to intraday-scale moves.

### B6. Order-flow / footprint features (CVD, delta, absorption, exhaustion, aggressor classification)

**Intuition.** Beneath OHLC, the *tape* shows who is aggressive. **Delta** = aggressive-buy minus aggressive-sell volume per bar; **CVD** = cumulative delta; **absorption** = large resting size soaking up aggression without price moving; **exhaustion** = aggression drying up at an extreme; **bid/ask imbalance** = lopsided traded volume at adjacent price levels.

**Encoding — aggressor classification is the foundation.** You must first sign each trade. The **tick rule** (López de Prado §19.3.1) uses price change; **Lee-Ready (1991)** combines the quote-midpoint test with the tick rule. This journal's canonical encoding: **A = ask-lift = BUY aggressor, B = bid-hit = SELL aggressor** (`interactions.py`) — and the notes flag that an old extractor flipped it, so "buy-agg='B'" in stale notes is wrong. Big-lot participation is defined side-agnostically (≥10-lot within 60s).

**Critique.** Aggressor classification is only ~ correct (tick/Lee-Ready misclassify a meaningful minority, worse in fast/HFT tape). CVD and toxicity signals are **mechanically tied to volume/intensity** — the VPIN critique (A5) is the general warning. This journal's own order-flow studies found **absorption/exhaustion dead at every live anchor**, while **big-lot participation** was the one live entry-time signal (AUC 0.66, robust) — yet even that *failed its size-up A/B out-of-sample*. Net: raw participation/CVD survives as a *feature*, geometry-of-tape reads (absorption/exhaustion patterns) do not.

---

## Part C — Synthesis: mapping the landscape onto this journal

### C1. What the journal already does (and which lineage each belongs to)

| Journal method | Lineage / analogue | Verdict in-journal |
|---|---|---|
| `zz20` pivots, **causal** zigzag | B2 swing detection, done non-repainting | Correct encoding; used everywhere |
| ATR-normalized swing tiers | B2 (ATR-scaled swings) | Feature layer; break signal NULL (B3) |
| `levels_in_force` VP, POC/VAH/VAL developing-vs-static | B5 volume profile, causal | Encoding good; **geometry NULL** repeatedly |
| Big-lot participation / CVD / absorption-exhaustion, A=buy encoding | B6 order flow + A5 tape | Participation/CVD **survive**; absorption/exhaustion **dead** |
| Regime classifier | (no direct AFML/practitioner name) — closest to A4 structural-break/regime sampling | **Cleanest surviving detector** (ghost AUC .657) |
| Momentum-window favor features | B/A hybrid | Live in strategies |
| Overlap/chop, range-compression bar features | A4 CUSUM-style event texture; own `overlap_10` | `overlap_10` = first robust structural stop predictor (chop gate A/Bs still failed) |
| MFE/MAE excursion labeling | **A2-adjacent** — path-based outcome, one step short of full triple-barrier | Used as the scoring engine (forward MFE/MAE/net over N bars) |
| BOS/CHoCH event stream | B3 market structure | Encoding excellent; **forward-NULL at every scale** |

**The through-line:** the journal has independently rebuilt most of Part B *causally*, and its results replicate the literature — **named-pattern / VP-geometry / structure-break reads come back NULL; raw participation, order flow, and regime survive.**

### C2. Encoding methods the journal has NOT yet tried

Grouped by prior, given that track record:

**High prior — build/test candidates (quant-lineage, orthogonal to the dead families):**
1. **Triple-barrier + meta-labeling (A2).** The journal already labels with MFE/MAE over N bars — that is *90% of the way* to triple-barrier. Formalizing volatility-scaled TP/stop/time barriers and then training a **meta-label** on the existing entries (drift-fade, ORB-with-stop, ema-pullback) would turn "should I take this signal and how large" into a learned classifier — directly targeting the recurring "the edge is tail-concentrated / regime-dependent" problem. Meta-labeling *filters*, which is exactly what the surviving `reenter_after_stop_only` and regime gates already do by hand. **Highest-value untried method.**
2. **Information-driven / imbalance / run bars (A1).** The engine runs on 1-min bars; imbalance bars would re-sample the clock by signed flow — a natural fit given big-lot participation is already the one live tape signal. Cheap to prototype (re-bar the existing tick cache), and it attacks non-stationarity at the data-structure level rather than the feature level.
3. **Fractional differentiation (A3).** Level-based features (distance-to-POC, distance-to-band) are non-stationary; frac-diff would give a stationary-but-memory-preserving version for any ML layer. Low cost, orthogonal to everything tried.

**Low prior — cheap null-checks only, do NOT build first:**
4. **FVG / order-block encoding (B3 SMC).** Pattern-matches directly to the journal's *dead* geometry families (BOS/CHoCH NULL, VP-geometry NULL, stable-level NULL). Encode as a feature layer and race against a drift null exactly like the structure-events study; expect NULL. **Do not build a strategy first.**
5. **Matrix profile / shapelet motif discovery (A6).** Motif discovery on price is prone to finding statistically-inevitable shapes (the candlestick failure mode). Could be useful for *anomaly/discord* detection or regime clustering, not for a pattern-signal. Low prior for a directional edge.
6. **VPIN (A5).** Given the Andersen-Bondarenko critique — VPIN's signal is mechanically tied to intensity, and the journal *already* found big-lot participation (a purer intensity signal) fails its size-up A/B — VPIN is unlikely to add orthogonal edge. Compute it as a diagnostic if tick data is cheap, but low prior for a gate.
7. **SAX symbolic encoding (A6).** Interesting for *indexing/clustering* sessions (e.g., "find days like today"), not for a directional feature. A tooling nicety, not an edge.

### C3. The single most defensible recommendation

Given a track record where **VP-geometry and named-pattern reads keep dying while participation/order-flow and regime signals survive**, the highest-expected-value untried method is **triple-barrier + meta-labeling on the existing surviving strategies** — it is the quant-lineage formalization of what the journal already does by hand (MFE/MAE labeling + regime/re-entry filtering), it *filters rather than invents* (matching the only A/B that has ever passed: `reenter_after_stop_only`), and it sidesteps the geometry families that keep returning NULL. **Imbalance bars** and **fractional differentiation** are the two cheap, orthogonal infrastructure upgrades to pair with it. The SMC/FVG and matrix-profile/shapelet families should be entered only through the journal's standard *encode-then-race-against-a-drift-null* gate, with an explicit low prior.

---

## Caveats

- **Two evidentiary tiers.** The Part-A factual anchors (chapter/section structure, VPIN mechanics and critique, Amihud ILLIQ, candlestick null, SAX mechanism) are each verified against a **primary source** and survived 2-vote adversarial verification. The Part-B taxonomy of practitioner methods (SMC/ICT specifics, footprint definitions) and the entire Part-C journal mapping are **synthesis**, cross-checked against the journal's own MEMORY notes but not independently sourced.
- **VPIN is contested, not closed.** The refuted predictive claim (VPIN peaked *before* the Flash Crash) and the surviving refutation (it peaked *after*) are construction-dependent; the original authors' rejoinder exists. Treat VPIN's early-warning value as an *open academic dispute*, and its estimation procedure as the only uncontested part.
- **SMC/ICT and matrix-profile/shapelet families are under-cited here.** No high-trust primary quantitative test of FVG/order-block edge was surfaced in verification; the "low prior" assignment rests on *analogy* to the journal's dead geometry families, not on a cited FVG backtest.
- **Candlestick evidence is equity, not futures.** The SET50 and Dow studies are cash equities; the null generalizes plausibly to NQ but was not measured on it.
- **No engine run.** This is a survey. Every "high prior / low prior" label is a hypothesis for the journal's standard A/B gate, not a result.

---

## Open questions

1. **Does a formal triple-barrier relabel change any existing verdict?** The journal scores with MFE/MAE over a fixed *N* bars; would volatility-scaled barriers with a first-touch label move any of the borderline gate A/Bs (chop, size-up) off their flat-net peaks?
2. **Do imbalance/run bars change the regime classifier's edge?** The regime detector is the cleanest survivor on 1-min bars — does re-sampling on signed-flow bars sharpen or dissolve it?
3. **Is there any causal, non-repainting FVG/order-block definition that beats a drift null on NQ?** The journal has never run the encode-then-race test on SMC imbalances specifically.
4. **Would fractional differentiation of distance-to-level features rescue any VP-geometry signal** that died as a raw (non-stationary) level read, or does frac-diff confirm the geometry is genuinely empty?
