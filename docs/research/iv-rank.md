# IV rank — quick reference

What the indicator is, how it is computed, where the number would come from for NQ, and what it would and would not tell this journal. Reference only — no data pulled, no code, no claim tested.

## 1. Definition

**Implied volatility rank** normalises today's implied vol against its own recent history, so a reading is comparable across instruments and across regimes. Raw IV is not: 18% is high for a bond ETF and low for a single-name biotech.

```
IV rank = (IV_now − IV_min) / (IV_max − IV_min) × 100
```

over a trailing window, conventionally 252 trading days (one year). Output is 0–100. 0 means today is the quietest implied vol of the year; 100 means the noisiest.

The input `IV_now` is **not** the IV of one option. It is a constant-maturity (usually 30-day) implied vol for the underlying as a whole — either the exchange's published vol index, or a variance-swap-style aggregate computed from the full OTM chain across the two expiries bracketing 30 days, interpolated. Reading a single at-the-money contract's IV instead makes the series jump every time the front expiry rolls.

## 2. IV rank vs IV percentile

These are routinely conflated and they are not the same statistic.

| | formula | measures |
|---|---|---|
| IV **rank** | `(x − min) / (max − min)` | position within the observed *range* |
| IV **percentile** | `count(IV_i < x) / N` | fraction of days spent *below* today |

The difference is entirely about outliers. A single volatility spike sets `IV_max` for the next twelve months, and every subsequent reading is compressed against it.

Worked case — IV sat at 14–16 all year, except one crash week that printed 60. Today IV is 17, the highest sustained level in months:

- **IV rank** = (17 − 14) / (60 − 14) × 100 = **6.5** — reads as near-record calm
- **IV percentile** ≈ **95** — reads as elevated, which matches the lived experience

Rank is the more common quote (tastytrade popularised it) and the more fragile one. Percentile is the more honest summary of the distribution. If only one is computed, percentile is the better default; the existing vol-regime code here already made that choice for realized vol.

## 3. Lookback

252 trading days is convention, not law, and it is a real parameter:

- **Shorter (60–90d)** — adapts to regime shifts fast, but the min/max are set by a small sample, so the denominator is noisy and readings whip.
- **Longer (252d+)** — stable, but a year-old crash spike anchors `IV_max` long after it stops being informative, and the indicator goes numb.

A one-year window also silently assumes vol is stationary over a year. It is not; vol regimes cluster. This is the same lag that makes IV rank read "low" through the early part of every vol expansion.

## 4. How it is conventionally used

IV rank is an **options premium** indicator, not a directional one. It says nothing about which way price goes — only whether options are expensive relative to their own history.

- **High rank (rule of thumb > 30–50)** — premium is rich; the retail convention is to sell it (credit spreads, strangles, iron condors), betting realized vol comes in under implied.
- **Low rank** — premium is cheap; buy it (debit spreads, calendars, long gamma), or express the view with the underlying instead of options.

The real signal underneath is the **variance risk premium**: implied minus subsequently-realized volatility, which is structurally positive because option sellers demand compensation for tail risk. IV rank is a crude proxy for "how fat is the VRP right now". That premium is what a realized-vol rank cannot see, and it is the only reason to prefer an implied measure over a realized one.

Two standard caveats:

- **Rank is not level.** IV rank 90 in a 10-vol year is still a low-vol tape in absolute terms; position sizing off rank alone mis-scales.
- **Term structure carries information rank discards.** Contango vs backwardation in the vol curve often matters more than where spot IV sits in its range, and the rank collapses that to one number.

## 5. Where the number comes from for NQ

There is no options data in this repo. To get a genuine IV series you take the exchange vol index for the relevant underlying rather than build the chain aggregation yourself:

| underlying | index | notes |
|---|---|---|
| Nasdaq-100 / **NQ** | **VXN** | the one that applies here |
| S&P 500 / ES | VIX | |
| Russell 2000 / RTY | RVX | |
| Crude / CL | OVX | |
| Gold / GC | GVZ | |

CBOE publishes VXN daily EOD history for free. That is enough for daily IV rank — the indicator is a daily/swing statistic and does not need intraday granularity. An intraday VXN series is a paid feed, and would be buying precision the indicator does not use.

## 6. What it would mean here

Two honest limits on applying this to an NQ intraday journal:

**It is a daily statistic and this book is intraday.** The strategies here enter and exit inside a session; IV rank moves on a scale of days to weeks. Its plausible role is a *day-level regime label* — the same shape as the existing vol-clock gate — not an entry trigger.

**The rank arithmetic is already here, on realized vol.** `src/journal/sim/vol_regime.py` computes `datr_pctl60`: where the globex day's Wilder ATR(14) sits within the trailing 60 sessions, shifted so a day never sees its own range, terciled into quiet / mid / hot. That is IV-*percentile* arithmetic applied to realized rather than implied volatility, on a 60-day rather than 252-day window.

So an IV-rank feature would be near-collinear with a label already shipped and already A/B'd. The one thing it adds that `datr_pctl60` cannot is the **implied-minus-realized spread** — whether the options market is pricing more or less movement than the tape has been delivering. If IV rank is worth anything to a directional intraday book, that spread is where it lives, not in the rank itself.

The pre-screen before building anything: pull free VXN daily history, compute rank and percentile, and correlate against `datr_pctl60` over the same sessions. High correlation means it is a rename of an existing gate and should not be built. That mirrors the oscillator pre-screen from the RSI study — if the new indicator tracks a measure already in the stack, the A/B is measurement bleed, not an edge.
