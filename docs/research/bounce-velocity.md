# Bounce Velocity — how fast the rejection off dev1 is, and whether the speed means anything

- **Date:** 2026-08-26
- **Research question:** The landing-depth study measured how *deep* the bounce off dev1 goes and never looked at the clock. So: how *fast* is the rejection — and does the speed of it predict the outcome, over and above where price simply is?
- **Data:** `vwap-upper-band-bounce`, two runs — **A** `20240303-20260630-v15-95473ae3` (the adopted baseline, 401 trades, net $152,923, PF 1.58, 2024-03→2026-06) and **B** `20250201-20260630-v13-9760707f` (436 trades, net $72,550, PF 1.23, 2025-02→2026-06, different config *and* different window — an independent replication, not a re-cut).
- **Also covered (§7):** the 13 manual trades reviewed with the **Rally fade** tag, run through the identical measurement. Descriptive only at that n.
- **Method:** new extractor `data/research/bounce-velocity/extract.py` (manual book: `extract_manual.py`; the measurement itself is shared in `windows.py` so the two sources cannot drift apart) re-reads each trade's own tick path from the fill and records, inside fixed windows of 30/60/120s, the dip, the climb-out, and the speed of it. Entry variant A with `entry_limit_offset_ticks = 0` fills at dev1 exactly, so signed distance from `avg_entry` **is** distance from the level. `analyze.py` does the reads. Reuses the tick-array rebuild + splice auto-detection from the triple-barrier study (splice verified `rth` on both runs, residual 0.09–0.13pt).

---

## TL;DR — the tape is fast, the speed is not information

- **Never calculated before.** The engine has had `recovery_s` (deepest tick → breakeven) since July 2026 and it feeds the edges panel, but it is measured over the *whole* trade, so it is NaN for every loser that dies underwater — "fast recovery wins" is a tautology in that field, not a finding. The vol-clock study correlated it against ATR (ρ = −0.17) and that is the closest anyone got. Nobody ever divided depth by time.
- **A winner's heat is taken almost immediately; a loser's never stops.** The median **winner** has made **92–100% of its entire final MAE within 30 seconds** of the fill, and takes its worst tick at **6–8% of its holding time**. The median **loser** has made only 28–43% by then and, by construction, sets its worst tick at its exit. The pooled figure (89% by 60s) is a mixture of the two and should not be quoted on its own. The asymmetry is the finding: if the trade is going to work, the pain is over almost at once.
- **The bounce, when it comes, is violent.** Median climb-out from the window's low back to the fill is **2.3–2.5 seconds**, median speed **8–9 ticks/sec**, p90 45–87 t/s. Median time to first +10 ticks is 2.4–4.2s. This is a fast-twitch event, not a grind.
- **But speed carries no information.** Holding position at the 60s mark fixed, partial Spearman of log-speed against `r_multiple` is **−0.137 (p = 0.074)** on A and **−0.118 (p = 0.108)** on B — not significant on either, and *negative*: if anything the violent snap-backs do slightly worse, the opposite of the folk claim. Uncontrolled, the correlation is ~0 (−0.006 / +0.077). Time-back and time-to-+10t are flat null.
- **Position is the whole signal, and it is mechanical.** Where price sits at 60s sorts outcomes hard (>+5t → 86%/91% win; >40t below → 38%/37%) — but that is largely the `trail_breakeven_ticks = 6` trigger reading itself back. Inside the big >+5 stratum, fast vs slow climb-out is 86% vs 86% (A) and 90% vs 90% (B), with avgR flipping sign between the two runs.
- **No cut rule survives.** Every speed- or position-based bail at 60s loses on run A, by $17k–$79k. Run B likes three of the five, and disagrees with A on the sign of every single rule. Nothing replicates.
- **Verdict: NULL.** No velocity gate, no speed-based exit. Ninth sizing/exit idea to fail. The one keeper is descriptive: the first 60 seconds is where the trade is decided.

---

## 1. When the heat is actually taken

Share of the trade's *final* MAE already made by the end of the window — **split by outcome, because pooling the two is misleading**:

| window | A winners | A losers | B winners | B losers |
|---|---|---|---|---|
| 30s | 0.92 (49% complete) | 0.28 (5%) | 1.00 (55%) | 0.40 (12%) |
| 60s | 1.00 (60%) | 0.43 (19%) | 1.00 (66%) | 0.57 (30%) |
| 120s | 1.00 (74%) | 0.79 (37%) | 1.00 (76%) | 1.00 (52%) |

And the same thing measured window-free, as *when* the worst tick lands:

| | winners: t(MAE) | as % of hold | losers: t(MAE) | as % of hold |
|---|---|---|---|---|
| A (median hold 376s) | 32.7s | 6% | 176.1s | 100% |
| B (median hold 256s) | 22.5s | 8% | 105.5s | 100% |

The losers' 100% is definitional — a trade that dies at its stop exits *at* its worst tick — so the loser column is a sanity check, not a result. The winner column is the finding: **a trade that is going to work takes all of its heat in the first ~6–8% of its life and then never revisits it.** Combined with the landing-depth result (winner median depth ~34t, p90 ~100t, absolute in ticks): price pokes ~35 ticks through dev1 within the first half-minute and, if it is a winner, that is the whole of the damage.

The tempting inference — "still making new lows after a minute ⇒ bail" — is exactly what §5 prices, and it loses.

## 2. How fast the climb-out is

Measured from the window's deepest tick to the first tick back at the fill price, trades still open at 60s:

| | A | B |
|---|---|---|
| got back to the level inside 60s | 67% | 65% |
| median time-back | 2.3s | 2.5s |
| p75 / p90 time-back | 7.2s / 15.7s | 7.1s / 14.9s |
| median speed | 8.2 t/s | 9.0 t/s |
| p10 / p90 speed | 2.6 / 44.6 t/s | 2.8 / 86.5 t/s |
| median time to first +10t | 4.2s | 2.4s |
| median time to first −20t | 9.9s | 6.5s |

The speed distribution is extremely heavy-tailed — a 40-tick dip retraced in a quarter of a second is not rare — which is why every read below uses log-speed or a median split rather than a mean.

## 3. The raw read, and why it is contaminated

Trades that dipped ≥10t and were still open at 60s:

| cohort | n (A) | got back inside 60s (A) | med depth (A) | med speed (A) | n (B) | got back (B) | med depth (B) | med speed (B) |
|---|---|---|---|---|---|---|---|---|
| winners | 200 | 70% | 33t | 7.3 t/s | 203 | 75% | 36t | 8.3 t/s |
| losers | 87 | 38% | 60t | 5.9 t/s | 97 | 36% | 64t | 5.9 t/s |

This looks like a signal and is not one. The separation is almost entirely the **binary** — did it come back at all — and coming back is most of what winning *is*. The speed column, conditional on having come back, barely moves (7.3 vs 5.9). Depth does the rest, and depth is the previous study.

## 4. The controlled read — does speed survive?

Control = signed position at the 60s mark. That variable alone sorts the book:

| position at 60s | n (A) | win% (A) | avgR (A) | n (B) | win% (B) | avgR (B) |
|---|---|---|---|---|---|---|
| < −40t | 55 | 38% | −0.29 | 60 | 37% | −0.46 |
| −40..−20t | 40 | 60% | +0.17 | 40 | 52% | −0.11 |
| −20..−5t | 45 | 71% | +0.10 | 38 | 53% | −0.23 |
| −5..+5t | 40 | 72% | +0.11 | 26 | 62% | −0.17 |
| > +5t | 185 | 86% | +0.50 | 205 | 91% | +0.60 |

Part of this is mechanical: `trail_breakeven_ticks = 6`, so a trade sitting >+5t at 60s is about to have its stop pulled to breakeven and can barely lose from there. The table is closer to a readout of the exit ladder than a discovery.

Now split each stratum by climb-out speed (median split, cohort dipped ≥10t and recovered inside the window):

| position at 60s | n | fast win% / avgR | slow win% / avgR |
|---|---|---|---|
| A, −5..+5t | 28 | 86% / +0.07 | 64% / −0.05 |
| **A, > +5t** | **117** | **86% / +0.52** | **86% / +0.56** |
| **B, > +5t** | **139** | **90% / +0.69** | **90% / +0.48** |

The only strata with usable n are the >+5t ones, and there the win rate is identical to the point and the avgR ordering **flips between runs**. The −5..+5 row on A is the one that looks like something; n = 14 per side.

Partial Spearman (both variables residualised on position at 60s — the vol-clock screen):

| x | control | n (A) | ρ (A) | p (A) | n (B) | ρ (B) | p (B) |
|---|---|---|---|---|---|---|---|
| log speed | pos@60s | 172 | −0.137 | 0.074 | 188 | −0.118 | 0.108 |
| time-back | pos@60s | 172 | −0.006 | 0.936 | 188 | +0.040 | 0.582 |
| time to +10t | pos@60s | 171 | −0.058 | 0.453 | 184 | +0.046 | 0.533 |
| log speed | *(none)* | 172 | −0.006 | 0.938 | 188 | +0.077 | 0.292 |

Both runs give the same *sign* on log-speed and neither reaches significance. Note the sign: **faster ⇒ marginally worse**. If there is anything at all here it is that a violent snap-back is a slightly worse trade than a steady one, which is the reverse of the thing people say about rejections. At ρ ≈ −0.13, n ≈ 180, it is not worth an A/B.

## 5. Cut rules at the 60s mark

The only shape a speed finding could take is a bail rule. Priced by flattening every contract at the tape price at 60s — **which ignores spread and slippage, so it is biased in favour of cutting**:

| rule fires when | n (A) | realised (A) | if cut (A) | delta (A) | n (B) | realised (B) | if cut (B) | delta (B) |
|---|---|---|---|---|---|---|---|---|
| never back at the level | 115 | −$24.3k | −$78.8k | **−$54.5k** | 112 | −$89.0k | −$84.6k | **+$4.5k** |
| below the level right now | 159 | −$10.8k | −$88.6k | **−$77.8k** | 156 | −$114.1k | −$95.1k | **+$19.0k** |
| more than 20t below | 95 | −$24.9k | −$76.4k | **−$51.5k** | 100 | −$76.8k | −$85.2k | **−$8.4k** |
| not +10t within 60s | 48 | −$17.3k | −$34.2k | **−$17.0k** | 52 | −$51.5k | −$36.4k | **+$15.1k** |
| climb-out slower than median | 125 | +$109.3k | +$30.6k | **−$78.7k** | 128 | +$76.6k | +$40.4k | **−$36.1k** |

On the adopted baseline every rule loses, several catastrophically — the same result the loss study, the panic-exit A/B, the underwater-stop A/B and the depth study all reached: **the survivors' tail pays for the heat, and any early exit sells it.** Run B likes three rules, but B is a materially worse config (PF 1.23 vs 1.58) whose losers run further, so cutting rescues more; the two runs disagree on the sign of every rule, which is the definition of not replicating.

## 6. Split-half

The >+5t fast/slow read, each run split at its median session date:

| run | half | n | fast win% / avgR | slow win% / avgR |
|---|---|---|---|---|
| A | first | 87 | 77% / +0.24 | 79% / +0.39 |
| A | second | 85 | 91% / +0.59 | 76% / +0.36 |
| B | first | 94 | 85% / +0.31 | 77% / +0.39 |
| B | second | 94 | 85% / +0.75 | 79% / +0.14 |

Slow wins the first half of both runs; fast wins the second half of both. Consistent with a common regime drift and with nothing about speed.

## 7. The manual book — trades reviewed as "Rally fade" (n = 13)

Same measurement (`windows.py` is shared), different source: logical trades from the ATAS journal filtered to the review tag, via `extract_manual.py`. **n = 13 — every number below is descriptive. No correlation, no split, no rule was tested, because at this size nothing could survive a test.** For reference, detecting the sim's own effect size (ρ ≈ 0.13) at n = 13 is hopeless; even a ρ of 0.5 would not clear p = 0.05 here.

The book: 13 trades, all **short**, all on the `replay` account, 1 contract, across 5 sessions (2025-06-13 → 2026-08-11). All entries fall between **09:35 and 10:12 ET** — this tag is an opening-hour habit. 8 wins / 5 losses, net $1,179 (ATAS PnL, gross). Median hold **42 seconds** against the engine's 376.

**Alignment.** These have no `entry_idx`, only a timestamp, so the fill is located on the tape by time. The print at the recorded instant sits a median 3 ticks (max 8) off the fill price — in a fast tape you are filled on the other side of the spread — and reading from there would open every path several ticks underwater and invent a dip that never happened. The extractor therefore snaps to the nearest print *at* the fill price; all 13 snapped, max offset **0.82s**, median 0.05s. The timestamps are sound; only the price at that instant was.

**The keeper transfers, on the same scale-free clock:**

| | median hold | winners: t(MAE) | as % of hold | median MAE |
|---|---|---|---|---|
| sim A | 376s | 32.7s | 6% | — |
| sim B | 256s | 22.5s | 8% | — |
| **manual Rally fade** | **42s** | **6.0s** | **15%** | 29t |

Different instrument of expression, same shape: the heat arrives in the first fraction of the hold. In raw seconds the manual trades look ~5× faster, but that is mostly the holding period — as a fraction of the trade they are only modestly earlier than the engine's winners.

**The bounce, at matched windows:**

| | median depth in window | median time back to entry | median speed |
|---|---|---|---|
| manual, w = 10s | 13t | 0.52s | 22.1 t/s |
| manual, w = 30s | 14t | 1.20s | 13.3 t/s |
| sim A, w = 30s | 23t | — | 11.0 t/s |

Manual rally fades dip **shallower** than the engine's dev1 entries (13–14t vs 23t) and snap back at a comparable-to-slightly-faster clip. Median time to first +10 ticks is **1.5s**, and 100% of the 13 got there. Whatever else is true, the entries are well-timed: the tape moves your way almost immediately, essentially every time.

**What actually jumps out is not velocity — it is the giveback.** Median MFE **66 ticks**, median realised **+2 ticks**. Three of 13 are scratches (|realised| ≤ 5t) whose median MFE was 66t; two of those had 89t and 66t in hand. Spot-checked against raw tape: on 2026-05-13 09:50 the trade held 89 ticks and booked +2; on 2026-08-11 09:37 it held 182 ticks and booked 115.

**But this is not a manual-trading leak** — the engine does the same thing, and worse:

| | median MFE | median realised |
|---|---|---|
| manual Rally fade | 66t | +2t |
| sim A | 125t | +6t |
| sim B | 107t | +5t |

The engine's median realised of exactly 6 ticks is its own `trail_breakeven_ticks = 6` reading back: the median engine trade is scratched at the breakeven stop having been 125 ticks in front. Giving back the median excursion is what this whole family of strategies does. It is worth knowing that the discretionary hand is *not* the outlier here, and worth not "fixing" on the manual side alone — the depth study and the loss study both found that trying to bank the excursion earlier costs more than it saves.

The win/loss split of the 13 is reported for completeness only: winners median MAE 14t / worst tick at 15% of the hold, losers 49t / 88%. That second number is the same definitional artifact as §1 — a loser exits at its worst tick.

## 8. Verdict

- **Bounce velocity is NULL.** No gate, no exit, no sizing hook. Do not propose "wait for a fast rejection" or "cut the slow ones" again — both directions were measured, on two runs, controlled and uncontrolled.
- The two keepers are descriptive and go on the shelf as ruler facts: **a winner takes all its heat in the first 6–8% of its holding time** (median 92–100% of final MAE inside 30 seconds), and **the bounce itself is a ~2.5-second, ~8 t/s event**.
- On the manual side (§7) nothing is concluded and nothing should be: 13 trades. The tag is worth keeping and re-reading once it has ~50 — the machinery now exists, so it is one command. The one thing that is *visible* rather than inferred is that these entries are well-timed (100% reach +10 ticks, median 1.5s) and the exits give the excursion back — but so does the engine, so that is a family trait, not a discretionary flaw.
- Untested residue, low prior: everything here is measured on price alone. The tape at the moment of the snap-back (who is lifting, in what size) is a different variable and was not touched — though the loser order-flow study already found no tape signal at any loser anchor, so the prior is poor.
- Running tally: 9 sizing/exit ideas failed, 1 passed (`reenter_after_stop_only`).
