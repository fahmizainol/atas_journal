# NY Upper-Band VWAP × RSI — overbought, divergence, or nothing?

- **Date:** 2026-07-27
- **Status:** **RESOLVED NULL** for an orthogonal edge. Every classic RSI read at the +1σ band (overbought-fade, bearish divergence, RSI(2) mean-reversion) is either refuted outright or collapses into the momentum/stretch axis the project has already measured and A/B-killed. On the real trade run, RSI at the decision point carries **zero** signal. No knob built, no engine A/B warranted.
- **Research question:** Does RSI — the most-quoted oscillator in the retail playbook — carry information at the NY +1σ upper band that the band geometry doesn't already have: as an *overbought fade* signal, as *bearish divergence* at higher-high retests, or as a *winner/loser separator* on the upper-band-bounce's actual entries?
- **Data:** 363 RTH sessions, NQ front-month, 2025-02-03 → 2026-06-30 — the exact grid of the ny-band-ema920 study (139,292 1-min rows, 1:1 join on `nyema_minutes.parquet`). Wilder RSI (14 / 5 / 2) on 1-minute closes, warmed over the overnight+RTH stream like the EMA was, sampled onto the RTH minute grid (`rsi_build.py` → `rsi_minutes.parquet`). Same band-touch event (2,915 fresh +1σ taps, cont→+2σ-before-mid outcome) and same trade tie-in run **`a348d176`** (`vwap-upper-band-bounce`, v13 stack, 262 trades, +$150k).
- **Method:** `rsi_events.py` re-uses the nyema event machinery and adds three angles (level, slope, touch-to-touch divergence with the higher-high condition held constant); `rsi_trades.py` joins entries at the fill minute; `rsi_honest.py` is the §5.1-style honesty pass — re-measuring at `acceptance_ts`, collinearity vs the band axes, a level-controlled divergence cut, and split-half.

---

## TL;DR

**RSI at the upper band is the momentum/stretch axis wearing an oscillator costume. The textbook reads are backwards, the one stable event-level pattern is the day-with fact we already trade, and on real entries the apparent signal is fill-minute measurement bleed that vanishes at `acceptance_ts` — the same trap, caught the same way, as the EMA study's σ-depth lead.**

1. **Overbought continues.** A band tap with RSI14 ≥ 70 goes on to tag +2σ **56.7%** of the time vs **51.4%** below 70; the tertile ladder is monotonic *upward* (43.8% → 51.3% → 58.6%). RSI(2) ≥ 90 — the Connors mean-reversion trigger — continues **57.6%** vs 49.3%, with an *identical* downside tail (median 30-min max-dn −111t vs −107t). Fading overbought at the band is trading against the house's oldest finding: NQ edges are day-**with**.

2. **Bearish divergence is not a fade signal.** On 1,542 consecutive higher-high touch pairs: divergent 50.1% cont vs confirming 58.5% (deep >5pt divergence 45.8%). But divergent cohorts never fall meaningfully *below* the 51.6% all-touch baseline — divergence just deletes the momentum bonus; it never earns the short. Controlled for current RSI level, the mid-level gap is zero (54.4 vs 53.9); what survives is "**RSI-confirming** higher-highs continue more" (63.9% at high level) — a momentum *confirmation*, i.e. the same axis again.

3. **Split-half stable, but it's a known face.** The tertile monotonicity holds in both halves (H1 46→61%, H2 41→56%) and so does the confirming>divergent ordering (Δ+3.8pp / Δ+14pp). Real, but it's a ~5–9pp continuation nudge from the same family that has now failed the engine A/B ten-plus times, and it points *with* the strategy's existing premise — there is no trade in it that the engine isn't already taking.

4. **On the real run, RSI dies at the decision point.** At the *fill* minute RSI14 looks alive: ρ=+0.112, low tertile net **−$3,257** vs mid **+$81,590**. Measured at **`acceptance_ts`** — where a filter would actually decide — ρ collapses to **−0.009** and the tertile ordering *inverts* (low becomes the best bucket, R 0.347 vs high 0.225). The fill-minute signal is pullback depth renamed: ρ(RSI14@fill, stretch9) = **+0.809**, ρ(RSI14@fill, σ-depth) = +0.403, and the partial correlation of RSI with R given σ-depth is **−0.040**. Exactly the ny-band-ema920 §5.1/§7 mechanism — the fill is a pullback, so any path-level oscillator sampled there peeks at the bounce.

**Verdict:** no RSI knob, no A/B. RSI(14/5/2) adds nothing separable to band geometry + stretch. The overbought-fade and divergence-short reads are refuted on 2,915 events; the entry-filter read is measurement bleed. This joins the ledger: geometry null, EMA null, order-flow null at entry — and now the oscillator too.

---

## 1. RSI at the touch — Angle 1 (level)

Unconditional cont→+2σ over warmed RTH minutes is **26.6%**; at a fresh +1σ tap **51.6%** (n=2,126 resolved). RSI14 at the touch sits at median 55.7 (p25 50.1 / p75 60.3) — taps are *not* typically overbought; the band is hit long before RSI70 is.

| RSI14 at touch | n (resolved) | cont→+2σ |
|---|---|---|
| < 50 | 448 | 44.0% |
| 50–60 | 1,123 | 52.0% |
| 60–70 | 465 | 57.0% |
| 70–80 | 86 | **59.3%** |
| ≥ 80 | 4 | (n too small) |

Tertiles: low (med 47) **43.8%** → mid (med 56) 51.3% → high (med 63) **58.6%**. Monotonic in the *momentum* direction — the mirror of the EMA study's stretch tertiles (44→52→58%), which is no coincidence: at the touch, RSI14 *is* stretch smoothed differently.

## 2. Angle 2 — slope

RSI14 rising into the touch (5-min delta > 0): **53.9%** cont vs falling/flat **48.7%**. The familiar ~5pp nudge, same family as EMA stacked-and-rising (54.1/48.7) — quantitatively interchangeable.

## 3. Angle 3 — bearish divergence at higher-high retests

2,405 consecutive touch pairs (≤90 min apart), 1,542 where the second touch set a **higher high** — the textbook divergence canvas, with the higher-high condition held constant so divergence isn't conflated with "made a HH".

| cohort | n | cont→+2σ |
|---|---|---|
| HH + RSI divergent | 553 | 50.1% |
| HH + RSI confirming | 533 | **58.5%** |
| HH + deep divergence (>5pt) | 271 | 45.8% |
| control: lower-high pairs | 637 | 49.6% |

Forward excursions are indistinguishable (median 30-min max-up 117t vs 112t, max-dn −103.5t vs −107t) — divergence changes the *race ordering*, not the distribution, and only by returning the cohort to baseline. Controlled for current RSI level:

| current RSI level | divergent | confirming |
|---|---|---|
| low | 45.5% (266) | 54.2% (96) |
| mid | 54.4% (169) | 53.9% (193) |
| high | 54.2% (118) | **63.9%** (244) |

The mid bucket shows nothing; the edge case is confirming-at-high-RSI — new price high *with* new RSI high — which is simply maximal momentum. There is no level×divergence cell that fades below ~46%, so there's no short and no veto: the worst divergence cohort still continues at roughly the baseline rate the strategy already survives.

## 4. RSI(2) — the Connors mean-reversion cut

RSI(2) ≥ 90 at the touch: **57.6%** cont vs 49.3% below; pinned ≥ 98: **59.0%**. Downside tails identical (median max-dn 30m −111t vs −107t) — more continuation at *no* extra violence. Short-term overbought at the band is fuel, not exhaustion.

## 5. Trade tie-in — run `a348d176`, and the honesty pass

262 entries, 0 unmatched, joined at both the **fill minute** and **`acceptance_ts`**:

| measured at | ρ vs R | tertile R (low → mid → high) | tertile net |
|---|---|---|---|
| fill minute | +0.112 | 0.002 → 0.435 → 0.387 | **−$3,257** → $81,590 → $72,106 |
| **acceptance** | **−0.009** | **0.347** → 0.249 → 0.225 | $64,969 → $45,039 → $40,431 |

At the fill the low-RSI tertile looks like dead money; at the decision point the ordering *inverts* and everything flattens into noise. The mechanism is the known one: RSI14@acceptance sits at median 63 (price accepting above the band = momentum high), the fill is a pullback, and RSI@fill measures how deep that pullback ran — which is entangled with the immediate forward path. Collinearity confirms there was never a second axis:

| pair | Spearman ρ |
|---|---|
| RSI14@fill × stretch9 | **+0.809** |
| RSI14@fill × d_px_up1_sig | +0.403 |
| RSI2@fill × d_px_up1_sig | +0.406 |
| RSI14@acc × d_px_up1_sig | +0.134 |
| **partial ρ(RSI14, R \| σ-depth)** | **−0.040** |

Slope/fast-RSI cuts at entry (rsi14 rising, rsi5 ≥ 80, rsi2 ≥ 90) are all inside noise on 262 trades, and the σ-depth axis itself already failed its engine A/B (`min_acceptance_sigma`, ny-band-ema920 §7).

## 6. Split-half

| half | tertile cont (low/mid/high) | divergent vs confirming |
|---|---|---|
| H1 (2025-02 → ~2025-10) | 46.4 / 51.5 / 61.2% | 52.8 vs 56.6% |
| H2 (~2025-10 → 2026-06) | 40.8 / 50.7 / 55.7% | 46.9 vs 60.9% |

Both event-level patterns are directionally stable — the study's facts are real facts about the band; they're just not *new* facts, and they don't transfer to the engine's trades.

## 7. Why this is the expected result

RSI is a bounded transform of the same recent close path that builds the EMA and the VWAP bands — at a band tap all three are near-collinear (ρ 0.81 with stretch). The study lands exactly where ny-band-ema920 landed, one abstraction layer up: the *indicator* adds nothing to the *geometry*, the geometry adds nothing to the *acceptance* the engine already requires, and the only live direction is day-with momentum. The retail reads (fade overbought, short divergence) are not just null here — they're **sign-reversed**.

## Artifacts

- `data/research/market-structure/rsi_build.py` → `rsi_minutes.parquet` (139k rows, RSI 14/5/2 + slope, 1:1 with `nyema_minutes.parquet`)
- `data/research/market-structure/rsi_events.py` → `rsi_events.parquet` (2,915 touches × RSI state), `rsi_divergence_pairs.parquet` (1,542 HH pairs)
- `data/research/market-structure/rsi_trades.py` → `rsi_trades.parquet` (262 entries × RSI state)
- `data/research/market-structure/rsi_honest.py` — acceptance-point re-measurement, collinearity, level-controlled divergence, split-half

## Next steps

None. If any future oscillator idea comes up (stochastics, Williams %R, MFI…), the prior from this + ny-band-ema920 is that bounded price-path transforms are collinear with stretch/σ-depth and must clear the `acceptance_ts` measurement bar *before* any knob is built — cheapest test first: ρ(indicator@fill, stretch9); if > ~0.7, don't bother.
