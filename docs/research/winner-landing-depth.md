# Winner Landing Depth — where the bounce actually catches, and the averaging-down A/B

- **Date:** 2026-07-19
- **Research question:** For upper-band-bounce winners, how deep below dev1 does price actually land before the bounce — and does the answer justify tranching entries into the dip (averaging down) instead of going all-in at the first touch?
- **Data:** `vwap-upper-band-bounce` runs **cdc07ca2** (v10 adopted, 222 trades) and **30badf94** (v8 baseline, 398 trades). Entry variant A with `entry_limit_offset_ticks = 0` books the fill at dev1 exactly, so `mae_points` *is* the depth below dev1; `band_width_ticks` (dev2−dev1 = 1σ) *is* the dev1→VWAP distance. No band recomputation needed.
- **Follow-up shipped:** `pyramid_direction` knob (engine v12) + a live A/B — runs `a0512f69` / `56b67092` / `bbc61459`.

---

## TL;DR — the edge lives at the band, and every way of paying for depth loses

- **Winners land shallow.** Median winner bottoms **29% of the way from dev1 to VWAP (~34 ticks)**; 51–59% stay within 30%, 72–77% within 50%. Only 8–10% of winners ever touch VWAP. The picture-perfect kiss is rare: ≤5 ticks is ~8% of winners, to-the-tick under 2%.
- **Depth is absolute in ticks, not σ-scaled.** Winner median depth is 32–39 ticks and p90 ≈ 100 ticks in *every* band-width tercile of both runs (bands 69→260 ticks median). The bounce's grip is a fixed ~35-tick zone under dev1; wide-band days do not get proportionally deeper pullbacks.
- **Depth is a survival clock.** Win rate decays monotonically with penetration: at 50% of dev1→VWAP the trade is a coin flip with negative expectancy; through VWAP it wins 25–30%. But it is **not** an exploitable exit: cutting at a VWAP touch costs ≈ −0.73R vs the −0.48R those trades actually average (the VWAP-zone holds pay for the rest). Confirms the loss study's "early exits destroy PnL."
- **Clean landings carry the book.** Trades whose depth stays ≤15t (0.1R) are ~18% of trades but **~65% of net**; every pre-stop depth bucket is profitable at roughly flat $/trade; the entire cost of the strategy is the stop bucket.
- **Averaging down FAILED — static and live.** Static counterfactual: −26% to −52% of net. Live engine A/B (v12): **net −75% ($124.5k → $30.6k), PF 1.98 → 1.27**. The live result is worse than the static because the trail ladder re-strikes off the blended basis (winners exit earlier) and cheaper stops admit more trades past the daily loss stop (255 vs 222, extra ones skew losers).
- **Verdict: full size at the first dev1 touch.** `pyramid_direction` ships in v12, stays `"with"`. Eighth sizing/exit A/B to fail against one pass (reenter_after_stop_only).

---

## 1. Where winners land

Depth below dev1 as a fraction of the dev1→VWAP distance (= 1σ = `band_width_ticks`), winners only:

| landing depth | cdc07ca2 (n=162) | 30badf94 (n=274) |
|---|---|---|
| ≤ 2 ticks (to the tick) | 1.9% | 1.5% |
| ≤ 5 ticks | 8.6% | 8.0% |
| ≤ 10 ticks | 14.8% | 16.8% |
| ≤ 15 ticks (0.1R) | 24.1% | 23.7% |
| ≤ 30% of σ | 51% | 59% |
| ≤ 50% of σ | 72% | 77% |
| touched VWAP (≥100%) | 10% | 8% |

Winner depth in ticks (cdc07ca2): median 34, p75 73, p90 102, max 149 — i.e. the p90 winner takes ~0.67R of heat, still inside the 150-tick stop.

**Depth is absolute, not σ-scaled** — winner depth by band-width tercile:

| tercile | band med (ticks) | depth med | depth p90 |
|---|---|---|---|
| narrow | 69 / 79 | 32 / 32 | 98 / 95 |
| mid | 114 / 141 | 38 / 32 | 110 / 104 |
| wide | 220 / 261 | 34 / 39 | 98 / 104 |

(cdc07ca2 / 30badf94.) The ruler for any stop/invalidation idea is ticks-under-dev1 (~35 median / ~100 p90), not σ fractions.

## 2. Depth as a survival clock — and why it is not an exit

P(trade ends a winner | price reached depth X), cdc07ca2 / 30badf94:

| reached | win% | avgR |
|---|---|---|
| 10% of σ | 68 / 62 | +0.16 / +0.03 |
| 30% | 58 / 48 | −0.03 / −0.17 |
| 50% | 46 / 38 | −0.25 / −0.36 |
| 100% (VWAP) | 30 / 25 | −0.48 / −0.59 |
| 125% | 22 / 19 | −0.61 / −0.65 |

Monotone decay — but exiting at any of these marks loses money: an exit at the VWAP touch books ≈ −0.73R against the −0.48R those trades realize by holding (median band 110t vs the 150t stop); at the 50% mark −0.37R vs −0.25R realized. The survivors' tail pays for the heat, same as the panic-exit and underwater-stop A/Bs found. (Losers "landing past VWAP" is partly mechanical: the 150-tick stop is 1.1–1.4σ at the median band, so every stop books through VWAP by construction.)

## 3. Net by landing depth — the clean touches carry the book

cdc07ca2, all 222 trades, net $124,508:

| depth bucket | n | winners | net | avg/trade |
|---|---|---|---|---|
| ≤5t (0.03R) | 14 | 14 | +$19.7k | $1,404 |
| 5–10t | 11 | 10 | +$16.0k | $1,456 |
| 10–15t (0.1R) | 15 | 15 | +$45.4k | $3,029 |
| 15–30t (0.2R) | 29 | 29 | +$48.0k | $1,655 |
| 30–50t (0.33R) | 34 | 33 | +$45.8k | $1,348 |
| 50–100t (0.67R) | 44 | 43 | +$46.5k | $1,057 |
| 100–150t | 20 | 18 | +$29.8k | $1,490 |
| >150t (stops) | 55 | 0 | **−$126.8k** | −$2,305 |

Same shape on 30badf94. Two reads: ≤15t landings are 18% of trades and ~65% of net; and $/trade is roughly flat across every surviving depth — deep heat doesn't erode the payout, it just makes the ride uglier. (The ≤15t → 98% win stat is hindsight, not signal: a trade that never went 15t underwater mechanically can't have been stopped.)

## 4. Averaging down — static counterfactual

Same trades, same exit prices, entries tranched below dev1 (fills iff the trade's MAE reached the level; all lots stopped at the first lot's stop):

| scheme | cdc07ca2 net | PF | 30badf94 net | PF |
|---|---|---|---|---|
| baseline 3 @ entry | $124,508 | 1.98 | $122,503 | 1.46 |
| 1/1/1 @ 0/35/70t | $71,695 (−42%) | 1.73 | $68,408 | 1.33 |
| 1/1/1 @ 0/50/100t | $59,621 (−52%) | 1.70 | $54,285 | 1.30 |
| 2/1 @ 0/75t | $92,527 (−26%) | 1.87 | $92,266 | 1.41 |
| 3/2/2 @ 0/50/100t (adds on top) | $160,744 | 1.75 | $149,404 | 1.33 |

Why it fails is §1 + §3 in one sentence: tranches below dev1 mostly fill on the trades that are going to lose (depth ↔ worst conditional odds), while the shallow winners that carry the net only ever get the first tranche. The 3/2/2 scale-in "wins" on net purely as leverage — maxDD ~×2, worst trade −$4.0k, net/maxDD 10.1 → 7.0 (9.1 → 4.9 on 30badf94); flat-sizing up to 4 contracts dominates it.

## 5. Averaging down — live engine A/B (v12)

`pyramid_direction: "with" | "against"` shipped in engine v12: against-adds are resting limits one `pyramid_step_ticks` further below the fill, booked at their own levels (the entry-limit fill rule, not the stop-fill rule); the schema refuses a grid whose deepest add reaches the stop; anchor/blend stop modes keep their meanings (blend re-strikes off the falling average — a martingale, refused nothing but priced honestly). Baseline config = cdc07ca2's, anchor mode:

| run (v12) | trades | net | PF | win | maxDD | avg ctr |
|---|---|---|---|---|---|---|
| baseline `a0512f69` | 222 | **$124,508** | 1.98 | 73% | −$12,350 | 3.00 |
| avg-down 3×35t `56b67092` | 255 | $30,621 | 1.27 | 74% | −$12,842 | 2.09 |
| avg-down 3×50t `bbc61459` | 256 | $29,441 | 1.29 | 73% | −$11,313 | 1.88 |

The v12 baseline reproduces cdc07ca2 to the dollar — the knob at `"with"` simulates identically, so v11 runs are quarantined on principle, not because anything moved.

Live is far worse than static (−75% vs −42%) for two reasons the static hold-exits-fixed assumption hid:

1. **The trail ladder re-strikes off the blended basis.** Adds lower the average entry; breakeven and every trail step are struck from it, so the whole exit ladder shifts 17–35 ticks down and winners scratch out on bounces the baseline rode. Same win rate, much smaller average winner.
2. **Cheaper stops admit more bad trades.** Smaller per-stop losses trip the $1,995 daily loss stop less often, and the re-entry logic lets more attempts through: 255 trades vs 222, the extras skewing to stops (64 vs 55).

## 6. Verdict

- Full size at the first dev1 touch stands. `pyramid_direction` stays `"with"`.
- No depth-based exit, no depth-based invalidation, no averaging down. The one number worth keeping on the shelf: winners' p90 heat ≈ 100 ticks < the 150-tick stop — a ~110-tick stop clears 90% of winners' heat, but it collides with the loser study's already-low-prior wider-stop lead.
- Running tally: 8 sizing/exit A/Bs failed, 1 passed (reenter_after_stop_only).
