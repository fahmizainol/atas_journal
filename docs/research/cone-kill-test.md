# Cone kill test — can a model beat the ruler at predicting how far price travels?

**Verdict: no. The ruler wins, extra model capacity makes it worse, and the level
geometry adds nothing. Build the cone as arithmetic — there is no ML in it.**

- **Date:** 2026-09-14
- Scripts: `data/research/cone/{build.py, fit.py}` → `panel.parquet`, `results.json`
- Pool: 199,065 RTH minutes over 577 sessions (2024-03-04 → 2026-06-30)

## The question

Every other pass this session asked whether look-alike chart states have look-alike
futures — per-minute k-NN, overnight twins, NY session twins, motif windows — and
answered no four times. Those are all *retrieval*: no training, no learned function.

This is the supervised version of the only target that ever looked promising:
not **which way** price goes, but **how far it travels** — the quantity a stop is
placed against, and the one thing with abundant labels.

## Method

One row per RTH minute, features read at the minute's close from closed bars only,
labels strictly in the future and measured off real intrabar highs and lows.

| family | features |
|---|---|
| **vol** | median 1-min range at 5/15/30 min (the ruler), realised vol at 15/30, range expansion, time of day |
| **geometry** | signed distance to NY/Globex VWAP and NY/Globex POC-VAH-VAL, both band half-widths, position inside band and value area — **all in ruler units**, so the geometry carries no volatility of its own |
| **arrival** | net run over 5/15 min, position in the 15-min range |

Labels: `up_h` and `dn_h`, the maximum excursion above and below the current close
over the next *h* minutes, for h = 5, 15, 30.

Three predictors, scored at three quantiles by pinball loss plus coverage:

- **ruler** — `k × ruler_30`, with *k* set on train to hit the quantile. This is
  what the bracket presets already do.
- **vol** — gradient-boosted quantile regression on the volatility family.
- **vol+geo** — the same model plus geometry and arrival.

Split is **by session and chronological** with a 5-session embargo: 427 sessions
train (2024-03-04 → 2025-11-18), 145 held out (2025-11-26 → 2026-06-30).
Neighbouring minutes share their forward window, so a random split would leak.

## Result

Change in pinball loss versus the ruler baseline, on the held-out sessions
(negative = worse than the ruler):

| target | vol | vol+geo | geometry's own contribution |
|---|---|---|---|
| dn_5 q0.80 | +0.0% | +0.5% | +0.46% |
| dn_5 q0.95 | +0.9% | +1.1% | +0.18% |
| dn_15 q0.95 | −2.4% | −5.3% | −2.91% |
| up_15 q0.95 | −5.0% | −7.8% | −2.65% |
| dn_30 q0.80 | −0.7% | −1.2% | −0.52% |
| **dn_30 q0.95** | **−3.6%** | **−8.5%** | **−4.71%** |
| **up_30 q0.95** | **−4.9%** | **−10.5%** | **−5.37%** |

**The trained model loses to a one-parameter baseline**, and loses worst at the
95th percentile — precisely where stop placement lives.

Calibration says the same thing independently. The ruler hits its target coverage
dead on (0.50 / 0.80 / 0.95–0.96). The models under-cover at the tail, reaching
**0.90** where 0.95 was asked for — a stop sized by the model would be hit twice
as often as advertised.

### The overfitting signature

The first pass ran 120 trees and the model was *marginally ahead* (+0.2% to +2.6%).
Raising it to 400 moved every cell **backwards**. More capacity, worse out-of-sample
— the textbook shape of a target with nothing left to learn once volatility is in.

### What the model reached for

Permutation importance, `dn_30` at q0.80 — drop in pinball when a feature is shuffled:

| feature | family | importance |
|---|---|---|
| `ruler_30` | vol | **8.11** |
| `ruler_15` | vol | 1.31 |
| `d_gxvwap` | geo | 0.82 |
| `gxband` | geo | 0.65 |
| `tod` | vol | 0.64 |
| `d_gxval` | geo | 0.27 |
| `rv_30`, `rv_15` | vol | 0.24, 0.23 |

The ruler is **6× more important than anything else** and 10× more important than
any geometry feature. And the two geometry features it did reach for are
`d_gxvwap` (overnight displacement) and `gxband` (band half-width) — both
volatility measures wearing geometry's clothes, exactly as
`atr-vwap-band-study` found (band = intraday ATR renamed, ρ ≈ 0.96). Whatever
the model learned from them cost it accuracy out of sample.

## What to build instead

The baseline's own coefficients. **How many rulers price travels:**

| horizon | median | 80th | 95th |
|---|---|---|---|
| **down** 5 min | 0.96 | 1.92 | 3.26 |
| down 15 min | 1.67 | 3.31 | 5.75 |
| down 30 min | 2.35 | 4.68 | 8.32 |
| **up** 5 min | 0.95 | 1.80 | 2.85 |
| up 15 min | 1.63 | 3.02 | 4.79 |
| up 30 min | 2.27 | 4.22 | 6.67 |

That table *is* the cone. Multiply the live ruler by the row you want, draw the
envelope, done — no model, no training, no inference.

**[Walk it on real sessions →](cone-visual.html)** — six held-out days, scrubbable
minute by minute, with the realised path revealable and per-session containment
scored against each band.

### Downside exceeds upside, and the gap widens

`dn > up` in **every one of the nine cells**. The ratio at the 95th percentile
grows with horizon: 1.14× at 5 minutes, 1.20× at 15, **1.25× at 30**. A long's
stop needs measurably more room than a short's — and more the longer it is held.

### The 2026-03-25 trades, in rulers

Two trades that morning, both **shorts**, both stopped, both held under 30 seconds:

| | entry (ET) | held | result | ruler | stop | adverse move, 5 min |
|---|---|---|---|---|---|---|
| 1 | 09:35 | 27.7 s | −$267 | 125.5 t | ≈53 t = **0.43 rulers** | 187 t = 1.49 rulers |
| 2 | 09:38 | 12.2 s | −$257 | 124.0 t | ≈51 t = **0.41 rulers** | 70 t = 0.56 rulers |

They were shorts, so the half that matters is the **up** side, whose median
5-minute travel is 0.955 rulers — at that morning's ruler, **120 ticks**. Both
stops sat around 52 ticks: *under half the median adverse excursion*. And the
first trade's 187-tick poke is an unremarkable wiggle — above the median, still
well inside the 80th percentile (1.80 rulers ≈ 225 ticks).

The theses were right. Price traded **217.8 points below** the second entry later
in the same session. Both trades were stopped by noise that the cone prices at a
glance.

(Measured from arbitrary minutes, and an entry is not a random minute — but not
by the factor that would rescue a 0.4-ruler stop.)

## What this closes

This is the fifth independent null on level geometry this session, and the first
where a model was actually **trained** on it rather than asked to retrieve
look-alikes. It joins `avwap-reclaim` ("VWAP-geometry-as-location has no edge"),
`vah-snap`, `stable-level-sr`, `prior-poc-magnet` and `lvn-retrace`.

The constructive half stands on its own: **spread is predictable, and the
predictor is one number you already compute.**
