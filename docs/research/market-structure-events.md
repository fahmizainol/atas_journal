# Market Structure as Data — the BOS/CHoCH event stream

- **Date:** 2026-07-24
- **Research question:** How do you turn "market structure & price action" from something you read off a chart into *readable data* — columns you can filter, join to a trade, and cut by? And once encoded, does the classic swing-break vocabulary (BOS / CHoCH) carry any forward information on its own, or is it purely descriptive?
- **Approach (family #1 of the structure-as-data survey):** a **swing-pivot → bias state machine**. Reuse the prior study's non-repainting `causal_zigzag` (a pivot exists at bar *t* only after price has confirmed it — no lookahead), label each confirmed pivot vs the prior same-kind pivot (HH/HL/LH/LL), then run a bias state machine that emits two typed break events: **BOS** (close beyond the active swing *in* the current bias — continuation) and **CHoCH** (first close beyond the *opposing* swing — character flip, bias reverses). Every break is scored as if it were a trade in its signalled direction (interactions.py method: forward MFE / MAE / net over 20 one-min bars).
- **Data:** every cached **RTH** session, all NQ front months — **365 sessions, 159,301 events, 24,788 breaks**. 1-min bars. Swing threshold swept 5 / 10 / 20 / 40 pts.
- **Files:** `data/research/market-structure/structure_events.py` → `structure_events.parquet` (one row per event) + `structure_sessions.parquet` (per-session densities). Builds on `extract_structure.py` (`causal_zigzag` copied verbatim so the two studies agree bit-for-bit).
- **Visual companions:** `market-structure-events-examples.html` (`render_structure_events.py`) — four panels on real 1-min candles with the causal zigzag, HH/HL/LH/LL labels, BOS/CHoCH markers and the per-bar bias strip: (1) the labels on an opening up-staircase, (2) the null as a 15-minute whipsaw, and (3)+(4) the **same CHoCH↓ signal** preceding a textbook +246pt turn *and* a −503pt trap — the pooled null made visual. `market-structure-events-examples-atr2.html` / `-atr4.html` (`render_structure_events_atr2.py` / `_atr4.py`) — the same four examples on one **ATR×2 fine** and **ATR×4 major** adaptive rule (§7). `market-structure-swing-tiers.html` (`render_swing_tiers.py`) — minor vs **major** swings; `market-structure-atr-sweep.html` (`render_atr_sweep.py`) — the ATR-multiplier granularity dial (§7).

---

## TL;DR — the encoding is excellent, the naïve "trade the break" reading is a null at every scale

- **Structure *is* cleanly tabulatable.** The chart collapses to two products: a **per-bar state row** (`bias`, active `ref_high`/`ref_low`, `dist_hi_ticks`/`dist_lo_ticks`, `new_pivot` label) and an **event log** (`ts`, `type ∈ {pivot_H, pivot_L, BOS_up, BOS_down, CHoCH_up, CHoCH_down}`, `label`, `level`). No pixels. This is the same class of artefact as `overlap_10` — a machine-readable texture you can join to any trade's entry bar and cut by, exactly like `by_entry_reason`.
- **But the break events carry ~zero forward directional edge.** Pooled over 365 sessions, forward-20-bar net is **+0.43 pts for BOS and +0.42 pts for CHoCH**, both at **~49–50% win rate**, with **MFE ≈ MAE** (38.8 ≈ 38.3, 36.8 ≈ 36.5). A break marks a point of *maximum uncertainty*, not continuation.
- **It doesn't beat drift.** An always-long entry at an *arbitrary* bar nets **+0.38 pts** over the same window (mean |move| 35.3 pts). The events clear the coin-flip baseline by hundredths of a point — noise.
- **Coarser structure does not rescue it.** Sweeping the swing threshold 5→40 pts *reduces* event density (68→14 breaks/session) but never lifts the win rate off ~49.5%; forward net actually drifts to **−0.07 pts at 20 pts** and CHoCH net goes **negative (−0.29)** there. The symmetry (MFE ≈ MAE) holds at every scale. **The threshold is the model, and no setting of it manufactures an edge.**
- **The density itself is a finding.** At 5 pts on 1-min NQ you get **~390 pivots and ~68 breaks per session** — structure is being *redefined roughly every bar*. "CHoCH" at that scale is a label for ordinary two-sided rotation, not a regime signal (worked example below: three character-flips in 15 minutes, all near-zero forward).
- **Verdict:** consistent with — and a 365-session generalization of — the prior study's "structure breaks are a null" (which saw it only at the upper-band-bounce entry). Build the encoding as a **feature layer** (state, distances, densities, time-in-bias), **not** as a signal generator. Do *not* build a "trade the BOS/CHoCH" strategy or gate; there is nothing there at any swing scale. No A/B warranted.

---

## 1. The encoding — what "structure as data" actually looks like

Two tables come out of one pass. **Event log** (structure as rows):

| column | meaning |
|---|---|
| `ts`, `bar` | when the event became *known* (causal — pivots stamped at confirmation) |
| `type` | `pivot_H` / `pivot_L` / `BOS_up` / `BOS_down` / `CHoCH_up` / `CHoCH_down` |
| `label` | pivot only: `HH` / `HL` / `LH` / `LL` vs the prior same-kind swing |
| `level` | the price that was pivoted or broken |
| `bias_after` | the state-machine bias once this event is applied |

**Per-bar timeline** (structure as state, one row per bar): `bias`, `ref_high`/`ref_low` (the active unbroken swings), `dist_hi_ticks`/`dist_lo_ticks` (how far price sits under/over the levels it would have to break), `new_pivot`, `event`. These are the columns you'd `merge_asof` onto a trade's entry timestamp.

The bias state machine (canonical SMC form, `structure_events.py`):

```
bias == up   : close > ref_high  -> BOS_up      (continuation)
               close < ref_low   -> CHoCH_down  (flip to down)
bias == down : close < ref_low   -> BOS_down
               close > ref_high   -> CHoCH_up    (flip to up)
bias == na   : first break either way seeds the bias as a BOS
```

A broken reference is *consumed* — the machine waits for the next confirmed pivot before it can break again in that direction, so one swing can't fire twice.

## 2. Worked example — the concepts on real tape (NQH5 2025-02-03, 15:00–15:16 ET)

```
 time       type label   level bias_after  fwd_net  fwd_mfe
14:59    pivot_L    LL 21477.2       down
15:00    pivot_H    LH 21492.8       down            <- lower high: downtrend intact
15:01   CHoCH_up       21492.8         up     -4.5     10.8   <- breaks the LH: bias flips up...
15:02    pivot_H    HH 21498.8         up            <- ...confirmed by a higher high
15:04    pivot_H    HH 21506.8         up
15:06 CHoCH_down       21484.8       down     +3.0     14.5   <- 5 min later, flips back down
15:09    pivot_H    HH 21484.5       down            <- HH label but bias still down (unbroken ref)
15:15   CHoCH_up       21471.5         up     -8.8     25.5   <- third flip in 15 min
```

Everything a discretionary trader would *say* out loud — "lower high, so still down… ok character change up… no, back down" — is now three timestamped rows with prices. That is the win: the *language* is faithfully captured. The `fwd_net` column is also the loss: every one of those character-flips is worth ±a handful of points forward. The encoding is honest; the signal isn't there.

## 3. The forward-outcome null (pooled, 365 sessions)

Each break scored in its signalled direction, forward 20 one-min bars, points:

| event | n | net | win% | MFE | MAE |
|---|---|---|---|---|---|
| BOS | 13,899 | **+0.43** | 50.0 | 38.83 | 38.33 |
| CHoCH | 10,889 | **+0.42** | 49.0 | 36.83 | 36.46 |
| *baseline* (always-long, any bar) | 132,772 | +0.38 | — | mean \|move\| 35.29 | — |

MFE ≈ MAE and win% ≈ 50 is the whole story: a break sits at a spot from which price is equally likely to run either way. The +0.42/+0.43 net is indistinguishable from the +0.38 drift a coin-flip long already collects.

## 4. The threshold *is* the model — and it doesn't help

Swing reversal threshold swept; breaks pooled, forward-20-bar:

| thr (pts) | breaks/session | net | win% | MFE | MAE | CHoCH net | CHoCH win% |
|---|---|---|---|---|---|---|---|
| 5 | 67.9 | +0.43 | 49.5 | 38.0 | 37.5 | +0.42 | 49.0 |
| 10 | 57.6 | +0.36 | 49.6 | 41.1 | 40.8 | +0.16 | 48.9 |
| 20 | 35.5 | **−0.07** | 49.5 | 48.5 | 48.9 | **−0.29** | 48.9 |
| 40 | 13.9 | +0.07 | 49.3 | 58.8 | 60.0 | +0.26 | 49.8 |

Coarser swings buy fewer, "more significant" breaks — and the win rate never leaves 49.3–49.6%. If structure breaks held any continuation edge, raising the threshold (demanding a bigger, more-committed break) should surface it; instead net wanders around zero and the MFE/MAE symmetry is scale-invariant. This is the strongest form of the null: it isn't a tuning problem.

## 5. Nulls worth recording

- **BOS ≈ CHoCH.** Continuation breaks and character-flip breaks are statistically identical forward (0.43 vs 0.42, 50 vs 49%). The distinction is descriptively real and predictively empty — a break is a break.
- **No swing scale is special.** 5/10/20/40 pts all coin-flip. (Matches the VP-geometry and VWAP-geometry no-edge pattern — the *shape* rarely carries the edge; regime and texture do.)
- **This is not a "my detector is bad" result.** The detector is the prior study's own primitive, non-repainting, and the density/labels reconcile with hand-reading (§2). The information is genuinely absent, not mis-measured.

## 6. What to do with it

**Use the encoding as features, never as a signal.** The parts worth keeping, in the `overlap_10` spirit — texture and state, joined to entries, then cut by outcome:

- `bias` and **time-in-bias** at entry (how long the current up/down bias has held) — a directional-conviction proxy that's orthogonal to `overlap_10` chop.
- `dist_hi_ticks` / `dist_lo_ticks` — proximity to the level a trade would have to break, i.e. overhead/underfoot structure, as a continuous number.
- **pivot density / bias-flip count** over the last N bars — a *second* chop measure (how often the bias just whipsawed), complementary to bar-overlap.
- pivot `label` sequence as a categorical (`HH,HL` vs mixed) — reproduces the prior study's "mixed-structure tax" cut directly from this layer.

**Do not** build a strategy or gate that enters on BOS/CHoCH, and do not run an A/B — §3–4 close that door at every scale. If any of the *feature* cuts above separate winners from losers on a live run's trades, that's a future entry-gate lead, evaluated the usual way (engine A/B, not static counterfactual — weekly-VWAP lesson).

## 7. Detecting *major* swings only — the noise is a threshold, not a limit

The 5–10pt zigzag labels a pivot almost every bar (§TL;DR: ~390/session). That "internal" wiggle is a *parameter choice*, and the standard fix is a second, coarser tier — exactly how LuxAlgo's toolkit splits **internal structure** (short lookback 5–49, dashed/small) from **swing structure** (long lookback 50–100, solid/large): "internal structure are constructed from shorter term swing high/low points, while swing structure are constructed from longer term ones."

Two ways to get the major tier here:

- **Fixed larger threshold** — simple, but *wrong across sessions*. A 40pt reversal is 300 swings on a high-vol day (median ATR 52pt) and 67 on a quiet one (ATR 18pt). Same number, totally different meaning.
- **ATR-scaled threshold** (`thr = mult × session median ATR14`) — the volatility-adaptive "ATR-ZigZag". At **mult=5** it returns a stable **11–17 major swings/session** across the 17k / 21k / 28k contracts — a swing roughly every 25–40 min. This is the tier to use.

| session | contract px | ATR14 | minor 10pt | **ATR×5** | fixed 40pt |
|---|---|---|---|---|---|
| 2025-04-09 | ~17.5k | 52pt | 390 | **17** | 300 |
| 2025-02-03 | ~21.2k | 18pt | 361 | **14** | 67 |
| 2026-06-11 | ~28.7k | 37pt | 390 | **11** | 227 |

`market-structure-swing-tiers.html` shows it: the same non-repainting detector on the same April-9 session drops from 390 unreadable pivots to 17 that trace the real day (choppy morning → higher low → clean HH/HL uptrend), and the identical ATR×5 rule stays just as clean on the 28k June contract where a fixed threshold would fail.

**The multiplier is the one knob — dial it to the granularity you want.** `market-structure-atr-sweep.html` sweeps it on NQM6 2026-06-11 (median ATR14 ≈ 37pt):

| mult | threshold | swings/session | cadence | use for |
|---|---|---|---|---|
| ×2 | 75pt | 68 | ~1 / 6 min | fine / internal structure |
| ×3 | 112pt | 35 | ~1 / 11 min | intermediate |
| **×4–5** | **149–187pt** | **11** | **~1 / 35 min** | **major swing skeleton (recommended)** |
| ×6 | 224pt | 7 | ~1 / 56 min | only the day's defining legs |

Same detector, same day; only `mult` changes. ×4–5 is the sweet spot for a clean major tier; go lower for detail, higher for just the skeleton. (ATR14 window and the swing-vs-internal split are secondary knobs — the multiplier does the real work.)

`market-structure-events-examples-atr2.html` re-runs the four worked examples on **one** adaptive rule (`thr = 2 × median ATR14`) instead of the hand-picked fixed thresholds — the whole study on a single, self-consistent, volatility-normalised granularity. It confirms the null holds on the adaptive tier: a balance morning still whipsaws ~10 CHoCH near-zero (thr≈18pt), while the same CHoCH event marks a clean +186pt turn on one ordinary day and a −221pt trap on another (marquee events re-picked at this scale, ATR-normalised so they aren't tail-vol).

`market-structure-events-examples-atr4.html` does the same on the **major** tier (`thr = 4 × median ATR14`, ~11–17 swings/session — the recommended granularity). Full-RTH panels show a whole day's structure in a dozen pivots: a pure LH/LL downtrend with *zero* CHoCH (character never breaks), and a rotational day that flips major bias 6× going nowhere. The dream (+204pt, 13×ATR) and trap (−178pt) panels show the null persists even when you demand a bigger, "more significant" swing.

**Caveat — this buys readability, not edge.** §4 already showed the forward null holds at *every* threshold (the 40pt tier coin-flips just like the 5pt one). A major-swing tier gives a cleaner *feature* layer — major-tier `bias`, the major HH/HL sequence, distance to the last *major* swing — which is the right input for the outcome cuts in §6. It does not make "trade the major BOS/CHoCH" any less null.

---

*Sources for the vocabulary/method: LuxAlgo Price-Action-Concepts market-structure docs (BOS/CHoCH as rule-based pivot breaks; internal-vs-swing tiers), the MQL5 "dynamic swing architecture" write-up, and the ATR-ZigZag / PyQuantLab-MSS approach for volatility-scaled swing significance; the forward-scoring method is this repo's own interactions.py touch-scoring.*
