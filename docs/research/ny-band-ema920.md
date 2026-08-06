# NY Upper-Band VWAP × 9/20 EMA (1-min) — is the EMA an orthogonal signal?

- **Date:** 2026-07-20 (§7 σ-acceptance engine A/B added same day — FAILED)
- **Status:** null on the EMA; the one adjacent lead (σ-normalised acceptance depth) was built as a `min_acceptance_sigma` knob and **failed its engine A/B** (§7) — inert at ≤0.15σ, net-negative at 0.20σ. Knob ships **off**. Another entry in the "post-hoc leads die at the engine A/B" ledger.
- **Research question:** Does the 9/20 EMA (1-minute, the institutional day-trading convention) carry information about the NY +1σ upper band that the band geometry doesn't already have — as *confluence* (S/R), as *cross timing*, or as a *slope filter* on band touches — and does any of it separate the upper-band-bounce's winners from its losers?
- **Data:** 363 RTH sessions, NQ front-month, 2025-02-03 → 2026-06-30, 139,292 1-minute rows. NY VWAP bands are the sim-owned tick-by-tick volume-weighted bands (`vwap.py`, anchored at the 09:30-ET open); the 9/20 EMA is `ewm(span, adjust=False)` warmed over the overnight+RTH minute stream, exactly as the chart draws it (`interactions._ema_rows`). Both sampled onto one 1-minute grid. Trade tie-in uses run **`a348d176`** (`vwap-upper-band-bounce`, v13 stack, 262 trades, 76% win, +$150k — the gate-robustness scorecard baseline).
- **Method:** `data/research/market-structure/nyema_build.py` builds the per-minute feature frame (`nyema_minutes.parquet`); `nyema_events.py` defines a band-touch event (fresh tap of +1σ from below) and measures forward excursion in ticks + whether price tags the +2σ target before reverting to the mid, across the three angles; `nyema_trades.py` joins every real entry to its EMA state. Everything is sampled causally on the minute grid (the reading a trader actually has), forward outcomes measured within-session.

---

## TL;DR

**The 9/20 EMA is largely collinear with the band geometry the strategy already trades. It carries no orthogonal edge for the upper-band setup — this is another entry in the "geometry has no edge" ledger, not a new gate.**

1. **The band touch itself is the whole signal.** A fresh +1σ tap raises P(reach +2σ before falling back to the mid) from **26.6% unconditional → 51.6%**. That ~2× lift is acceptance/momentum — price at the band is already trending up. Everything the EMA adds sits *on top of* that, and it's small.

2. **Confluence (Angle 1) is marginal.** When the fast EMA hugs the band (±8t) the continuation rate is **55.0%** vs **50.2%** when the EMA is >8 ticks below it — a ~5pp wobble on n=302, well inside the noise band that has failed A/B nine times on this project.

3. **Slope/stacking (Angle 3) is marginal in the same direction.** Stacked-and-rising (ema9>ema20 and rising) → **54.1%** cont vs **48.7%** for everything else. A ~5pp nudge, not a gate.

4. **"Stretch" says momentum, not exhaustion.** Bucketing taps by how far price sits above its own 9-EMA: continuation *rises* monotonically with stretch — **44% → 52% → 58%** from low to high tertile — the opposite of a fade-the-extension read. But the high-stretch cohort's downside excursion balloons (median 30-min max-down −98t → −148t). More continuation *and* more violence. Consistent with the house finding that NQ edges are day-**with**.

5. **Crosses lag the band (Angle 2).** The median 9/20 bull cross fires **0.6σ (≈96 ticks) below** the band — the EMA catches up *after* price has already run to the band, so a cross never "leads" a band break. The apparent cont-rate gap (cross-above 79% vs cross-below 7.6%) is a mechanical distance-to-mid artifact; on the honest metric (forward max-up) cross-*below* actually runs further (median 160t vs 127t) simply because it has more room. No independent timing edge.

6. **On the real trade run, EMA state is dead.** Across the 262 `a348d176` entries, the Spearman rank-correlation of realized R with EMA slope/stacking is **≈0** (ema_gap −0.015, ema9_slope −0.018, ema20_slope −0.032). The only feature that tracks R is **price-to-band depth in σ** — a band/acceptance property, not an EMA signal. But see §5.1: measured at the fill minute it reads ρ=+0.362, and measured honestly at `acceptance_ts` it collapses to **ρ=+0.127** and loses monotonicity. Most of the apparent signal is entry-minute measurement bleed.

**Verdict:** no EMA knob. The 9/20 EMA re-expresses the band geometry with a lag and adds nothing separable. The one distinct axis — σ-normalised acceptance depth — is *real but weak* once measured at the decision point (ρ≈0.13, non-monotonic); the only defensible cut is a floor ("veto shallow/at-band acceptance"), not "prefer deeper." Low prior for an A/B.

---

## 1. Baseline and the band-touch event

Over all warmed RTH minutes (minute_idx ≥ 15, so σ isn't degenerate), the unconditional chance that price tags +2σ before the mid within 60 min is **26.6%** (n=110,172). Define a **band-touch event** as the first minute whose high reaches +1σ after the prior minute closed below it (consecutive taps deduped within 5 min): **2,915 events, ~8/session**. Of the 2,126 that resolve one way or the other inside 60 min, **51.6% tag +2σ first**. So the tap roughly doubles the odds — the setup's premise is real and it's *the* signal here.

Forward excursion at a tap (ticks): median max-up 118 / 162 / 162 at 15/30/60m against median max-down −109 / −138 at 30/60m — roughly symmetric, i.e. the tap alone doesn't tilt the *distribution*, it tilts the *ordering* (up2 before mid).

## 2. Angle 1 — confluence as S/R

`d_ema9_up1 = (ema9 − upper1)` in ticks; negative means the fast EMA sits below the band (price stretched above both).

| cohort | n | cont→+2σ | median max-up 30m |
|---|---|---|---|
| EMA9 at band (±8t) | 302 | **55.0%** | 83t |
| EMA9 >8t below band | 1408 | 50.2% | 130t |

The at-band cohort continues slightly more often but runs *less* far (83t vs 130t) — because "EMA at the band" means price hasn't stretched yet, so there's less already-spent move and a tidier, smaller continuation. A weak, ambiguous effect.

## 3. Angle 3 — slope / stacking filter, and stretch

| cohort | n | cont→+2σ |
|---|---|---|
| stacked bull (ema9>ema20) | 1515 | 52.3% |
| inverted | 611 | 49.9% |
| ema9 rising | 1313 | 53.4% |
| ema9 falling/flat | 813 | 48.7% |
| **stacked & rising** | 1152 | **54.1%** |
| not (stacked & rising) | 974 | 48.7% |

Every cut moves ~3–5pp in the intuitive direction but none clears the bar this project has set (repeated ~5pp structural nudges have failed engine A/B). **Stretch tertiles** (close − ema9) are the most monotonic slice — and they point the *unintuitive* way:

| stretch tertile | median | cont→+2σ | median max-down 30m |
|---|---|---|---|
| low | −17t | 44.1% | −98t |
| mid | +21t | 51.9% | −89t |
| high | +76t | **57.7%** | **−148t** |

More extension above the fast EMA → more continuation *and* a fatter downside tail. Momentum, not mean-reversion.

## 4. Angle 2 — 9/20 cross timing vs the band

3,106 bull crosses (~8.6/session). At the cross, price is a **median 0.6σ (−96t) below** the band (p25 −1.5σ, p75 +0.1σ) — crosses are a *lagging* confirmation, firing after price has already travelled to the band, not a lead into it. The headline cont-rate split (above-band cross 79% vs below-band 7.6%) is pure distance-to-mid: a cross far below the band is sitting on top of the mid, so it "fails" by definition. On forward max-up the ordering flips (cross-below 160t vs cross-above 127t median) — the below-band crosses simply have more room. No timing edge either way.

## 5. Trade tie-in — run `a348d176` (262 entries)

Joining every entry to its minute-of-entry EMA state (0 unmatched):

**Spearman ρ of realized R vs feature:**

| feature | ρ | read |
|---|---|---|
| ema_gap (stacking) | −0.015 | nil |
| ema9_slope | −0.018 | nil |
| ema20_slope | −0.032 | nil |
| d_ema9_up1 | +0.166 | weak, ≈ −stretch at entry |
| stretch9 | +0.087 | weak |
| **d_px_up1_sig** (price-to-band in σ) | **+0.362** | the only real one — and it's a *band* feature |

The EMA slope/stacking features have essentially zero rank-correlation with how the trades actually did. The `ema9-below-band` cut (R 0.192 vs 0.435) looks large but is just the σ-depth axis re-expressed through the EMA, and the raw EMA slope cuts underneath it are flat.

### 5.1 The σ-depth axis, measured honestly

`d_px_up1_sig` above was joined on the **entry (fill) minute** — but the fill is a pullback *to* the band, and the join reads that minute's *close*, so a fill-minute that closes well above the band partly means "the bounce already fired in the same minute." That's a mild peek at the immediate forward path, and it inflates the correlation. Moving the measurement back to `acceptance_ts` — the moment the filter would actually decide — is the honest test:

| measured at | ρ vs R | tertile R (shallow → mid → deep) |
|---|---|---|
| entry/fill minute | +0.362 | −0.14 → 0.34 → **0.62** (monotonic) |
| **acceptance** | **+0.127** | 0.16 → **0.37** → 0.30 (*non-monotonic, mid best*) |

At the decision point the signal is weak and the "deeper is better" monotonicity is gone — the mid bucket beats the deep one. What survives is only that the **shallow/negative bucket is clearly worst** (69% win, 0.16R): entries where "acceptance" was really at or below the band in σ terms. That's a floor-veto, not a "prefer deeper" edge.

It *is* a distinct axis from the existing tick gate, though: acceptance-depth-in-ticks and acceptance-depth-in-σ correlate only **0.80** (not ~1.0), because the 1σ band width itself ranges **60→284 ticks (p10→p90), a 4.8× spread** across trades. So a fixed 30-tick `acceptance_min_ticks` means very different things in σ terms session to session — normalising isn't just relabeling the gate. The payoff for normalising is just small once measured at the right instant.

## 6. Why this is the expected result

The 9/20 EMA and the +1σ VWAP band are both slow functions of the same recent price path, so at a band tap they're near-collinear and the EMA lags. This lands exactly where the project's structural studies keep landing: **VP/geometry has no orthogonal edge, losses are regime not geometry, and order flow carries no entry signal** (see `market-structure-winloss`, `upper-band-bounce-loss-study`, `vah-snap-resistance`, `stable-level-sr`). Even the one axis that flickered — σ-normalised acceptance depth — mostly evaporates when measured at the decision point (§5.1); what remains is already the spirit of the engine's own `acceptance_min_ticks`.

## 7. The σ-acceptance floor — engine A/B (FAILED)

Built the σ-normalised floor as a real engine knob (`min_acceptance_sigma`, a fraction-of-band-width floor stacked *on top of* `acceptance_min_ticks`; 0 = off) and ran the honest test — a native engine A/B on the a348d176 config (`nyema_ab.py`):

| variant | σ floor | trades | net | Δnet | PF | maxDD | Sharpe |
|---|---|---|---|---|---|---|---|
| baseline | 0.00 | 262 | $150,439 | — | 2.05 | −13,731 | 3.04 |
| sigma-0.10 | 0.10 | 262 | $150,439 | **+0** | 2.05 | −13,731 | 3.04 |
| sigma-0.15 | 0.15 | 261 | $150,418 | −22 | 2.05 | −13,731 | 3.04 |
| sigma-0.20 | 0.20 | 260 | $142,359 | **−8,080** | 1.98 | −17,067 | 2.90 |

The floor removes **zero** trades at 0.10σ, one at 0.15σ, and at 0.20σ removes 2 trades that were *winners* (net −$8k, PF and DD and Sharpe all worse). It is redundant with the existing gate until it starts hurting.

**Why the §5.1 lead vanished — measurement bleed, again.** The post-hoc sweep measured acceptance depth at the acceptance *minute* (1-min bar close, minute-floored). The engine accepts on a **500-tick bar** and this config already requires **50 ticks** past dev1. 50t is ≥0.10σ unless σ > 500 ticks — which never happens (p90 σ ≈ 284t) — so at the engine's true acceptance instant nothing is below 0.10σ; the "69 shallow trades" in the sweep were the minute-floor catching a *pullback* after the tick-bar had already accepted. The floor only bites past ~0.20σ (needs σ > 250t, the widest ~10% of days), and those wide-day trades are good ones. The engine A/B measured what the minute-grid couldn't.

**Verdict:** FAIL. `min_acceptance_sigma` ships **off** (defaults 0.0), alongside the project's other disabled-by-A/B knobs. The +0.362 correlation → +0.127 corrected → 0 at the engine was a straight line down to nothing.

## Artifacts

- `data/research/market-structure/nyema_build.py` → `nyema_minutes.parquet` (139k minute-rows, reusable)
- `data/research/market-structure/nyema_events.py` → `nyema_events.parquet` (2,915 band-touch events)
- `data/research/market-structure/nyema_trades.py` → `nyema_trades.parquet` (262 entries × EMA state)
- `data/research/market-structure/nyema_ab.py` → σ-acceptance engine A/B ladder (runs 87f3dbcc/518ee37b/facfda85/c3a2fe4d)
- Engine: `min_acceptance_sigma` knob in `rules.py` / `engine.py` / `schema.py` (ships off)

## Next steps

None. The EMA is null and the one adjacent lead failed its engine A/B. If anything ever revisits acceptance depth, it must be measured on the tick-bar at the acceptance instant (not a minute grid) — but the A/B already says there's nothing there to normalise.
