# Initial Balance & Opening Range Breakout for Intraday Index Futures (NQ)

- **Date:** 2026-07-18
- **Research question:** What do primary and high-trust sources actually say about the Initial Balance (Market Profile) and Opening Range Breakout (ORB) for intraday index futures — definitions, documented statistics, day-type classification, trading rules, filters, and stop/target conventions — and which of the widely-quoted numbers are evidence vs. trader lore? Final section translates findings into candidate features/gates for this repo's NQ sim engine.

---

## TL;DR — what is solid, what is lore

**Solid (primary or data-grade):**

- The IB is Steidlmayer's concept, defined *functionally* ("the amount of time it takes the shorter-term trader to find an area where two-sided trade can occur"), not as a fixed 60 minutes. The CBOT's own study guide says IB parameters vary by market (1 hour in grains, 1h40m in lengthened financial sessions) and explicitly warns the parameters can change [1].
- The CBOT/Steidlmayer day-type ladder is defined in IB-extension multiples: normal day = IB is ≥85% of the day's range; normal variation = extension up to ~2× the IB; trend day = extension "considerably more than double" the IB; neutral day = extension both sides [1].
- IB break statistics on NQ specifically (two independent non-peer-reviewed but data-driven studies, ~2,500–2,800 RTH sessions each): **~96% of NQ days break at least one side of the IB by the close**; ~22–23% break both sides; ~4% break neither. Median excursion beyond the broken side ≈ 56% of IB width. Narrow IB (vs ATR) → near-certain break with larger extension; wide IB → fewer, smaller breaks [9][10].
- Zarattini & Aziz (2023, SSRN preprint): 5-min ORB on QQQ, 2016–2023, direction = first-candle sign, stop at first-candle extreme, 10R target else EoD, 1% risk, 4× leverage: total return 675% vs 169% buy-and-hold, Sharpe 1.12, annualized alpha 33% net of commissions, **win rate only 24%** (avg +0.13R/trade — the edge is payoff asymmetry, not accuracy) [4].
- Zarattini, Barbon & Aziz (2024, SSRN preprint): the *same* 5-min ORB applied to all liquid US stocks earns almost nothing (Sharpe 0.48); gating on relative volume ≥100% and taking the top-20 "stocks in play" lifts it to 1,637% total, Sharpe 2.81, alpha 35.8%/yr. **The 5-min window beat 15/30/60-min windows by a wide margin** (Sharpe 2.81 vs 1.43 / 0.21 / 0.40) [5].
- Holmberg, Lönnbark & Lundström (2013, *Finance Research Letters* — the one peer-reviewed source here): volatility-threshold ORB on crude oil futures 1983–2011 is significantly profitable, success rates 54–71% rising as the entry threshold is pushed further into the tail; but the profit is concentrated in the volatile 2001–2011 sub-period — ORB is effectively long volatility [6].
- ES/NQ ORB context study (tradingstats, 2014–2025): gap direction and ATR regime barely move ORB continuation odds; the strongest filter is **alignment of the break with the opening candle's internal direction** (+3–4pp on NQ, to ~70% continuation on 30-min ORB); Monday is the cleanest day, Wednesday the choppiest [11].

**Lore (could not be traced to a primary source — treat as hypotheses to A/B locally):**

- "The IB extends on 70–80% of days" — circulates on trading-education sites with no citation; actual NQ data says ~96% break at least one side by the close (~82% by noon), so the lore number is wrong or refers to an unstated definition [9][10][15].
- The "80% rule" (open outside value, two 30-min periods accepted back inside → 80% chance of full value-area rotation) — attributed to Steidlmayer/CBOT in forums, no primary stat found; one trader's 18-month tracking put it nearer 70% [16].
- Day-type base rates ("normal variation ~50% of days", "trend days <5%") — secondary sources conflict with each other and none cites data; Dalton's books give qualitative frequencies only ("only a few times each month" for trend days) [7][8].
- 1.5×/2× IB extension *targets* — pure platform convention (Sierra Chart, ATAS ship configurable IB extension multipliers); no efficacy evidence found anywhere [13][14].
- "High/low of day forms in the first hour ~70% of the time" — circulates uncited; it is at least *roughly* consistent with the NQ data (one IB extreme survives as the session extreme on ~3 in 4 days), but the exact number has no traceable source [9][15].

---

## 1. Definitions & parameterizations

**Initial Balance (Steidlmayer/CBOT).** The primary definition is functional, not clock-based. From the CBOT *Six-Part Study Guide to Market Profile*: the short-term trader's role is "to find a price area where two-sided trade can occur. Steidlmayer calls this an initial balance area," and — critically — "initial balance parameters can change. Therefore, the important thing is to understand the initial balance concept — the amount of time it takes the shorter-term trader to find an area where two-sided trade can occur." The guide notes it took ~1 hour in CBOT grain futures and ~1 hour 40 minutes in the lengthened financial-futures sessions [1]. The now-universal convention — IB = the first **60 minutes of RTH**, i.e. the first two 30-minute TPO periods (A: 9:30–10:00, B: 10:00–10:30 ET) — comes from the 30-minute TPO bracket structure of the profile (each letter = one half-hour bracket) [1][2][9]. Dalton's *Mind Over Markets* carries the same two-period convention and the "base of the day" metaphor: a narrow IB is an unstable base prone to being "knocked over" (range extension), a wide IB a firm one [3][7].

**Opening Range vs Initial Balance (platform convention).** Platforms are inconsistent: ATAS documents the IB as the first 60 minutes and the "Opening Range" as the first 30 minutes [14]; Sierra Chart's Initial Balance study lets the period be set arbitrarily and draws configurable extension multiples of the IB width [13]. In the ORB literature the "opening range" is anything from 5 to 60 minutes — Zarattini et al. explicitly test 5/15/30/60-minute windows [5]. Crabel's original ORB does not use a time window at all: entry is at a fixed offset (the "stretch") from the *opening print* [8].

**NQ sessions.** E-mini Nasdaq-100 futures (NQ, $20 × index, 0.25-pt tick) trade on CME Globex nearly 24h — Sunday 6:00 p.m. ET to Friday 5:00 p.m. ET with a daily 5:00–6:00 p.m. ET maintenance break; the most active period is US cash-equity hours, 9:30 a.m.–4:00 p.m. ET [12]. "RTH" in the IB/ORB context conventionally means the 9:30–16:00 ET cash session; everything outside it is the Globex/overnight session. All IB statistics cited below use IB = 9:30–10:30 ET on RTH data [9][10].

## 2. Documented statistics

### 2.1 IB break/extension frequencies on NQ and ES (data-grade, not peer-reviewed)

Two independent studies, both defining IB = 9:30–10:30 ET:

**tradingstats.net** (Jan 2015 – Dec 2025; 2,686 ES / 2,833 NQ RTH sessions, 1-min bars) [9]:

| Outcome by close | ES | NQ |
|---|---|---|
| At least one IB break | 97.8% | 96.2% |
| Single break up only | 38.3% | 40.6% |
| Single break down only | 30.9% | 33.0% |
| Both sides broken ("neutral") | 28.7% | 22.6% |
| Neither side broken | 2.2% | 3.8% |

- Median extension beyond the broken IB level: **ES 63.6%, NQ 55.6% of IB width**. A full 100% IB-width extension is reached on only ~13% (up) / ~16% (down) of NQ days.
- IB width vs ATR(14) is "the single strongest predictor": narrow IB (<0.5× ATR) breaks 98.5% of the time on NQ with median 63.8% extension; extreme IB (>1.5× ATR) breaks 76.9% with median 33.3% extension.
- Timing: ~64% of first breaks happen in C period (10:30–11:00), >80% by the end of D period (11:30).
- The IB high stands as the RTH high on ~37% of NQ days (ES ~33%) — i.e. one IB extreme survives as a session extreme on most days.

**nqstats.com** (2016–2026, 2,571 NQ sessions; breach = 1-tick wick beyond the level) [10]:

| | by 12:00 ET | by 16:00 ET |
|---|---|---|
| IB high breached | 47.0% | 62.9% |
| IB low breached | 39.8% | 54.9% |
| Either side | 82.5% | 96.1% |
| Neither side | 17.5% | 3.9% |

The two sources agree closely (96.1% vs 96.2% either-side by close; implied both-sides ~21.7% vs 22.6%), which is decent cross-validation. Neither is peer-reviewed; both are single-author websites selling stats content — but the methodology is stated and the numbers are checkable locally (see §7).

### 2.2 Academic / preprint ORB results (exact specs attached to each number)

**Zarattini & Aziz 2023, "Can Day Trading Really Be Profitable?" (SSRN 4416622; preprint, authors affiliated with Concretum Research / Peak Capital Trading / Bear Bull Traders)** [4]. Spec: QQQ, Jan 1 2016 – Feb 17 2023. If the first 5-min candle (9:30–9:35 ET) closes up, go long at the open of the second candle; if down, go short; skip dojis. Stop = the first candle's low (long) / high (short); the entry-to-stop distance is $R. Target = 10R, else liquidate at EoD. Size so a stop-out loses 1% of capital; $25,000 start; max leverage 4×; commissions $0.0005/share; **no slippage modeled**. Results:

- QQQ ORB: $25,000 → $192,806 = **675% total** (benchmark 169%); annualized return 31%; **Sharpe 1.12**; annualized alpha 33% net of commissions (p = 0.0025), beta ≈ 0; 1,795 trades, 51% long / 49% short; **win rate 24%; average +0.13R per trade**. The distribution is capped at −1R and skewed by occasional large winners — a low-accuracy, high-payoff edge.
- TQQQ (3× ETF) version: **1,485% total**, Sharpe 1.18, annualized 46% vs QQQ's 15%, alpha 47%/yr (p = 0.0013).
- Their stop/target grid (1R–10R targets × various stops): best cell = **stop at 5% of the 14-day ATR with no fixed target (EoD exit)** → +9,350%, alpha 93%/yr — which the authors themselves flag as unrealistic once slippage on a hair-width stop is considered [4].

**Zarattini, Barbon & Aziz 2024, "A Profitable Day Trading Strategy For The U.S. Equity Market" (SSRN 4729284; Swiss Finance Institute RP; preprint)** [5]. Spec: all US stocks 2016–2023 (survivorship-bias-free, 7,000+ names) with open > $5, 14-day avg volume > 1M shares, 14-day ATR > $0.50. Entry: stop order at the 5-min opening-range high (if first 5-min move was up) or low (if down); skip dojis. Stop-loss = **10% of the 14-day ATR** from entry; exit EoD (16:00) otherwise. 1% risk per position, 4× max leverage, $25,000 start, $0.0035/share commissions. Results:

| Strategy | Total return | IRR | Sharpe | Hit ratio | MDD | Alpha/yr |
|---|---|---|---|---|---|---|
| ORB base (all stocks) | 29% | 3.2% | 0.48 | 41.4% | 13% | 3.3% |
| ORB + RelVol top-20 ("stocks in play") | **1,637%** | 41.6% | **2.81** | 48.4% | 12% | 35.8% |
| S&P 500 buy-and-hold | 198% | 14.2% | 0.78 | 54.9% | 34% | — |

- Relative volume (first-5-min volume ÷ its 14-day average) is monotonically related to edge: **RelVol < 1 → −0.02R/trade; > 1 → +0.08R; > 30× → +0.38R** [5].
- Opening-range window comparison (same stocks-in-play filter): **5-min Sharpe 2.81; 15-min 1.43; 30-min 0.21; 60-min 0.40**; equal-weight combo 1.99. Authors' conjecture: shorter windows capture more of the move on trend days [5].
- Independent check: a QuantConnect replication confirmed the mechanics (Sharpe 2.40 vs SPY 0.84) but only for 2016, with community testing suggesting performance degrades materially in other periods — so treat the 8-year headline numbers as author-reported, partially replicated [17]. CXO Advisory published a critical review of the 2023 paper but it is paywalled; its content could not be verified [18].

**Holmberg, Lönnbark & Lundström 2013, "Assessing the profitability of intraday opening range breakout strategies," *Finance Research Letters* 10(1):27–33 (peer-reviewed; free working paper: Umeå Economic Studies 845)** [6]. Spec: US crude oil futures daily OHLC, Mar 30 1983 – Jan 26 2011. ORB = enter long (short) when price moves +ρ% (−ρ%) from the open, where ρ is calibrated to normal-distribution tail probabilities α ∈ {10%, 5%, 1%, 0.5%, 0.1%} of daily returns; exit at the close; no stops (they note stops would only truncate the losers, so their estimates are conservative). Full-sample results: long-side success rate 60.3–71.3% and mean return per trade +0.20% to +0.40%, rising monotonically as the threshold moves further into the tail (e.g. α = 0.1%: ρ = 2.24%, 80 trades, 71.3% success, +0.40%); short side 54–65% success. All full-sample p-values < 0.015 against the bootstrap "fair game" null. **Sub-period caveat:** 1983–1992 weak, 1992–2001 mostly insignificant, and virtually all profit sits in 2001–2011 — ORB profitability tracks volatility regimes ("ORB … is basically long volatility") [6].

**Crabel 1990 (origin of the term).** Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (Traders Press): trades are taken at a predetermined offset from the open — the "stretch" — defined as the 10-day average of the distance between each day's open and the day's closest extreme to the open. Buy stop at open + stretch, sell stop at open − stretch; first fill takes the trade, the opposite stop becomes the protective stop. Crabel conditions ORB on contraction patterns (NR4/NR7, inside days, doji) — the contraction→expansion principle later formalized by Holmberg et al. [6][8]. Note: an independent long-horizon re-test of the basic stretch ORB across 42 futures markets 1980–2011 (Oxfordstrat) rated the raw pattern poorly ("D"), i.e. the naked Crabel entry has not aged well without filters [8].

### 2.3 Related first-party result: VWAP as the day-trading anchor

Zarattini & Aziz 2023, "Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems" (SSRN 4631351): long above / short below session VWAP on QQQ, Jan 2018 – Sep 2023: 671% total, **Sharpe 2.1, MDD 9.4%** — same authors, same frictionless caveats, but it establishes VWAP-relative positioning as the strongest simple day-trade anchor in their own series of papers, which matters for stop/trail design (§6) [19].

## 3. IB and day-type classification (Steidlmayer/CBOT primary language)

From the CBOT *Six-Part Study Guide* [1] (day types are Steidlmayer's; wording below paraphrases/quotes the guide):

| Day type | Definition (in IB terms) | Control |
|---|---|---|
| **Normal day** | "On normal days, 85% or more of the range is formed in the initial balance period. Any range extension is usually slight and occurs late in the day." Market rotates between IB parameters all session. | Short-term trader (day timeframe) in control; responsive trade at the extremes works. |
| **Normal variation day** | Longer-term trader "extends the range past the initial balance area" — anywhere from a few ticks up to "roughly double the initial balance area," which is the *maximum* for this type. | Control "roughly divided." |
| **Trend day** | "Range extension is considerably more than double the initial balance area." Market moves one direction all day and "closes on the directional extreme." In the guide's example the IB is ~1/3 of the day's range. | Longer-term (initiative) trader in control; fading is the losing trade. |
| **Neutral day** | "There is range extension in both directions" — one extension cancels the other; "neutral days indicate uncertainty. Often the market uses these days to change direction." | No net longer-term influence. |

The guide's summary heuristic: no range extension → short-term control; extension ≈ 2× IB → divided; extension >> 2× IB → longer-term control [1].

Dalton (*Mind Over Markets*) keeps this ladder and adds sub-types: **double-distribution trend day** (narrow IB, quiet start, then a second distribution forms after breakout), **nontrend day** (no extension, compressed, pre-event), and splits neutral days into **neutral-center** (close in the middle — genuine balance) vs **neutral-extreme** (both sides broken but close at one extreme — a "victory" for one side late) [3][7]. Secondary sources attach frequencies (normal variation "~50%", trend "<5%" / "a few times each month") but these do not appear with data in any primary text found — treat as folklore (§ TL;DR) [7].

**Responsive vs initiative** (Dalton/CBOT vocabulary): activity is *responsive* when it fades price back toward value (buying below value area / selling above it) and *initiative* when it pushes price away from value in the direction of the move (buying above value, range-extension activity). The day-type read prescribes the trade: normal/normal-variation structure favors responsive trades at IB/range extremes; trend-day structure demands initiative (go-with) trades and forbids fading [1][3][7].

## 4. Common trading rules (as documented, with owners)

- **Crabel stretch ORB** [8]: buy open + stretch / sell open − stretch (stretch = 10-day mean of |open − nearest extreme|); opposite stop as protection; day-timeframe exit. Origin of the ORB name; raw version tests poorly in modern re-tests.
- **Zarattini 5-min ORB** [4][5]: direction = sign of the first 5-min candle; entry at second-candle open (QQQ paper) or stop order at the 5-min high/low (stocks paper); stop = first-candle opposite extreme (QQQ) or 10% of ATR(14) (stocks); target 10R or none; always flat by 16:00. The documented edge shape: win rate 24–48%, expectancy carried by the tail.
- **IB fade (responsive) rules** (convention, from the day-type logic; no independent stats found): on days classified normal/balanced, sell the IB high / buy the IB low targeting the IB midpoint or opposite extreme. This is exactly the trade the CBOT guide implies for short-term-controlled days [1][7] — but note our own repo memory: balance-day fade premises failed A/B on NQ (day-with edges only), so this family is guilty until proven innocent locally.
- **IB extension (initiative) rules** (convention): once IB high breaks, treat IB high as support and target projections at IB + 0.5×/1×/1.5×/2× IB width; platform IB studies (Sierra Chart extension multipliers, ATAS ×1/×1.5/×3 coefficients) exist to draw exactly these levels [13][14]. Data reality check: the *median* NQ extension is only ~0.56× IB and a full 1× extension happens on <16% of days — so 1.5×–2× projections are low-probability targets, not base cases [9].
- **Midpoint rules** (convention): IB mid as intraday mean-reversion magnet / stop-reference; nqstats tracks IB-mid confluence but publishes no headline edge number [10].

## 5. Filters known or claimed to improve ORB

Evidence-graded, strongest first:

1. **Relative volume ("in play")** — the single best-documented filter, but on *equities*: RelVol < 1 → negative expectancy, > 1 → +0.08R, > 30 → +0.38R per trade; top-20 RelVol turns Sharpe 0.48 into 2.81 [5]. Futures analog (session volume vs its N-day average at the same time of day) is a hypothesis worth testing, not a documented result.
2. **Opening-candle direction alignment** — trade the break only in the direction of the opening range's internal close: continuation 70.3% vs 66% baseline on NQ 30-min ORB; first-break/direction agreement 77–80% [11]. (Note: the Zarattini direction rule is the first-candle sign, i.e. the same idea at 5-min scale [4][5] — *not* gap direction, which is sometimes misquoted.)
3. **IB/OR width vs ATR (compression)** — narrow IB → ~99% break rate and bigger relative extensions; extreme-wide IB → 77% break and small extensions [9]. This is Crabel's contraction→expansion principle [8] and Holmberg's volatility framing [6] restated. Caveat from the same data: *wide* ORBs had the best continuation quality in combo setups (83.9% ES 30-min, n = 56 — small sample) — width cuts both ways depending on whether you measure break probability or follow-through [11].
4. **Volatility regime (ATR/VIX)** — Holmberg: ORB profits concentrate in high-volatility eras (2001–2011); insignificant in the quiet 1990s [6]. Within-sample daily ATR tiers, however, barely change continuation odds (±1.8pp) [11] — regime matters at the *era* scale more than day-to-day.
5. **Day of week** — Monday: fewest double-breaks (41–54%) and highest continuation (63–68%); Wednesday: most double-breaks, worst continuation; Friday trends well when it breaks [11]. Data-grade, single source, worth local validation.
6. **Gap direction/size** — mostly a dud per the only quantified source: continuation 58–65% regardless of gap direction; flat opens are choppier (more double-breaks); mild gap-fill bias (gap-down days break up first 56%) [11]. Widely *claimed* to matter in trader lore; unsupported by the one dataset found.
7. **Overnight (Globex) range vs IB** — no quantified public study found relating Globex range/position to IB extension odds; the claims circulating (e.g. "open outside overnight range → trend day") are lore. This is a genuine gap where this repo's Globex features could produce novel local evidence.
8. **Second break of a double-break day** — after both sides break, the *second* break direction "wins" the day 72.2% of the time on NQ 30-min ORB [11] — interesting for neutral-day / neutral-extreme classification logic.

## 6. Stop/target conventions

- **R-multiples off the opening range**: stop at the opposite extreme of the trigger candle/range (Crabel's opposite stretch stop [8]; Zarattini QQQ stop at first-candle extreme, targets quoted in R, 10R best among fixed targets [4]).
- **% of ATR stops**: Zarattini's grid says tighter is better on paper — 10% of ATR(14) used in the stocks paper [5], 5% of ATR(14) was the QQQ/TQQQ optimum (+9,350%) but flagged by the authors as slippage-fragile [4]. For NQ, 10% of a ~1.5–2% ATR is a very tight stop; expect the paper's frictionless assumption to be the load-bearing wall.
- **EoD exit beats fixed targets**: in both Zarattini papers the best target was *no* target (hold to 16:00) — "let profits run" empirically confirmed within their samples [4][5].
- **IB midpoint / opposite IB level**: conventional responsive-trade stops/targets (no quantified evidence found) [7][10][13].
- **IB extension multiples as targets**: platform convention only (Sierra Chart multipliers, ATAS coefficients) [13][14]; the NQ extension distribution (median 0.56× IB) says 1×+ targets fill on a minority of days [9].
- **VWAP as trailing reference**: the same authors' VWAP paper (Sharpe 2.1 long-above/short-below) supports VWAP-side as a regime/trail condition [19]; combining "ORB entry + exit on VWAP recross" is a common practitioner variant but no primary backtest of that exact combo was found — local test candidate.

## 7. Implementation notes for this codebase (design sketch)

The engine already computes session VWAP (+ anchors), value area/POC, and Globex-session features. IB/ORB slots in as one more session-scoped feature block plus a small rule/gate family.

**Features (computed once per session, 09:30–10:30 ET on the RTH stream the engine already trades):**

- `ib_high, ib_low, ib_mid, ib_range` (and `ib_range_atr = ib_range / atr14_daily`) — the ATR-normalized width is the highest-value single feature per the break statistics [9].
- `or_high_5m, or_low_5m, or_dir_5m` (sign of 09:30–09:35 candle), optionally `or_*_15m/30m` — the 5-min window is the only one with a documented large edge [5], and `or_dir` doubles as the direction filter [4][11].
- Running state after 10:30: `ib_ext_up, ib_ext_down` (booleans), `ib_ext_up_mult, ib_ext_down_mult` (max excursion beyond IB in IB-width units), `ib_first_break_side, ib_first_break_period` (C/D/E…), `ib_second_break_side` (for the 72% second-break stat [11]).
- `globex_range_vs_ib`, `open_loc_in_globex_range` — unstudied publicly (§5.7); cheap to add given existing Globex features, and any finding is novel.

**Day-type classifier (end-of-session label for edges panels, and an *intraday* running version for gates):** from `max(ib_ext_up_mult, ib_ext_down_mult)` and two-sidedness — `normal` (no/trivial extension, IB ≥ ~85% of range [1]), `normal_var` (one-sided extension ≤ ~2× IB), `trend` (one-sided > 2× IB, close near extreme), `neutral_center` / `neutral_extreme` (both sides broken; close mid vs at extreme [1][7]). First local deliverable: NQ base rates for these labels — the published ones are folklore, and the labels will immediately enrich the existing regime/edges analysis.

**Rule variants worth a parameter grid:**

- ORB entry: window ∈ {5, 15, 30 min}; trigger ∈ {stop at OR extreme, close-confirm}; direction filter ∈ {none, or_dir alignment, VWAP-side alignment}; skip-doji.
- IB breakout entry at 10:30+: trade first IB break, filtered by `ib_range_atr` tier (narrow-IB-only per [9]) and or_dir/VWAP alignment.
- Stops: {opposite OR extreme, k × ATR14 with k ∈ {0.05, 0.1, 0.2}, IB mid} — expect the 5–10% ATR cells to look great and be slippage-fragile [4]; model NQ spread/slippage explicitly before believing them.
- Exits: {EoD, 10R, VWAP recross trail, IB-mid on responsive variants}; EoD-vs-target is the cleanest documented A/B [4][5].
- Filters to A/B (in priority order): session relative volume analog, or_dir alignment, ib_range_atr tier, day-of-week, second-break reversal on neutral days. Deprioritize gap-direction (documented dud [11]).

**Claims requiring local validation before use as gates** (lore or single-source): the 70–80% extension lore (our data will simply replace it), the 80% value-area rule, 1.5×/2× extension targets, day-of-week effects, second-break stat, and *all* equity-derived numbers (RelVol thresholds, 24% win-rate shape) transplanted to NQ. Prior local finding to respect: balance/responsive premises have already failed A/B on NQ ("day-with only") — so build the responsive IB-fade variants for completeness but expect them to lose; the initiative/trend-day side (narrow IB + aligned break + EoD hold) is where the external evidence and local priors agree.

---

## Sources

Primary / peer-reviewed:

1. Chicago Board of Trade, *A Six-Part Study Guide to Market Profile* (CBOT, c. 1986–1991) — scanned PDF: https://www.profiletrading.com/cbot-a-six-part-study-guide-to-market-profile.pdf (IB definition Part I pp. 8–9; day types & range-extension multiples Part I pp. 12–14; quotes extracted from the PDF text layer).
2. J. Peter Steidlmayer & Kevin Koy, *Markets and Market Logic* (Porcupine Press, 1986) — origin text for the profile/IB framework (bibliographic; not directly consulted).
3. James F. Dalton, Eric T. Jones, Robert B. Dalton, *Mind Over Markets: Power Trading with Market-Generated Information* (updated ed., Wiley, 2013) — day-type taxonomy and IB "base" metaphor (bibliographic; content verified via secondary excerpts [7]).
4. Carlo Zarattini & Andrew Aziz, "Can Day Trading Really Be Profitable?" (Apr 2023), SSRN 4416622 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622 (full-text PDF mirror consulted: https://www.wealth-lab.com/api/discussion/download/pdf/6590-ORB-Strategy-pdf). Preprint, not peer-reviewed; authors affiliated with trading businesses; no slippage modeled.
5. Carlo Zarattini, Andrea Barbon & Andrew Aziz, "A Profitable Day Trading Strategy For The U.S. Equity Market" (Feb 2024), SSRN 4729284 / Swiss Finance Institute RP — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284 (full-text PDF mirror consulted: https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf). Same caveats as [4].
6. Ulf Holmberg, Carl Lönnbark & Christian Lundström, "Assessing the profitability of intraday opening range breakout strategies," *Finance Research Letters* 10(1), 2013, pp. 27–33 — https://www.sciencedirect.com/science/article/abs/pii/S1544612312000438 (paywalled); free working paper consulted in full: Umeå Economic Studies 845, http://www.econ.umu.se/ueslpnr/ues845.pdf.

Secondary (Dalton/day-type excerpts, conventions):

7. "Six Types of Market Days: Mind Over Markets" — https://time-price-research-astrofin.blogspot.com/2023/03/six-types-of-market-days-mind-over.html ; Marketcalls, "Market Profile: Different Types of Profile Days" — https://www.marketcalls.in/market-profile/market-profile-different-types-of-profile-days.html (both are uncited paraphrases of Dalton — used for taxonomy only, frequencies flagged as lore).
8. Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (Traders Press, 1990) — bibliographic; stretch definition and modern re-test via Oxfordstrat, "Opening Range Breakout | Trading Strategy" — https://oxfordstrat.com/trading-strategies/opening-range-breakout/.

Data-grade (single-operator, non-peer-reviewed, methodology stated):

9. "Initial Balance Breakout Statistics: ES & NQ Futures 2015–2025" — https://tradingstats.net/initial-balance-breakout-statistics/.
10. "Initial Balance Breaks — NQ Futures Analysis" (2016–2026) — https://nqstats.com/ib_breaks.html.
11. "ORB Strategy Deep Dive: Context Filters & Backtest" (ES/NQ 2014–2025) — https://tradingstats.net/orb-strategy-research/.

Exchange/platform conventions:

12. CME Group, E-mini Nasdaq-100 overview/specs — https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.html (spec fetch timed out twice; hours corroborated via NinjaTrader's NQ contract guide — https://ninjatrader.com/futures/futures-contracts/equity-index/e-mini-nasdaq/).
13. Sierra Chart, "Initial Balance" study reference (configurable period + extension multipliers) — https://www.sierrachart.com/index.php?page=doc/StudiesReference.php&ID=326.
14. ATAS, "Initial Balance Indicator" (IB = 60 min, OR = 30 min, ×1/×1.5/×3 extension coefficients) — https://atas.net/atas-possibilities/indicators/initial-balance-indicator-how-to-use-initial-balance/.

Lore circulation & verification:

15. TRADEPRO Academy, "How to Use the Initial Balance" — https://tradeproacademy.com/how-to-use-the-initial-balance/ (example of uncited 70–80%-style IB claims in circulation).
16. MyPivots forum thread, "80% Rule and Day Trading Market Profile" — https://www.mypivots.com/board/topic/6580/-1/80-rule-and-day-trading-market-profile (80% rule attribution + one trader's ~70% tracking).
17. QuantConnect, "Opening Range Breakout for Stocks in Play" (replication of [5]) — https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/.
18. CXO Advisory, "Day Trading with an Opening Range Breakout Strategy" (critical review of [4]) — https://www.cxoadvisory.com/technical-trading/day-trading-with-an-opening-range-breakout-strategy/ — **paywalled; conclusions not verifiable**.
19. Carlo Zarattini & Andrew Aziz, "Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems" (Nov 2023), SSRN 4631351 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4631351 (numbers via https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/).
