# Drift-Fade Globex-POC — price action around the trades (run 0a20b6a9)

- **Date:** 2026-07-20
- **Research question:** What does the tape actually look like around the globex-POC-only drift-fade's entries — trending or reversing, extended or compressed, at S/R confluence or in empty space — and does any of it separate the 17 stops from the 85 winners?
- **Data:** run **0a20b6a9** (`drift-touch-fade-entry-stop`, v2): Globex levels only, **POC only**, `use_session_refs=false`, entry 01:30–12:00, variant B confirm 3t, entry-stop 175t, trail 20/60/BE58. 102 trades (50L/52S), 85 trail / 17 stop, **+$49,581**, PF 2.09, 83% win, Sharpe 2.61. All fills land 9:00–11:59 NY despite the 01:30 open — the POC has to mature and drift before it prints a qualifying touch.
- **Method:** the lookahead-safe structure extractor (`extract_structure_drift.py`, timestamp anchors — zero >50t fill warnings) plus a new supplemental extractor `extract_vp_sr_drift.py` for VP geometry + S/R distances: developing Globex VA (engine's own `developing_profile`/`levels_in_force` reading), ONH/ONL, RTH open, prior-day RTH high/low/close and finished POC/VAH/VAL, POC-stability, gap context. Features: `features_dtfes_0a20b6a9.parquet`, `vpsr_dtfes_0a20b6a9.parquet`. Stats: Mann-Whitney AUC + permutation p. **n=17 stops — everything below is descriptive; nothing here is adoption-grade without a re-cut and an engine A/B** (the drift-fade ledger stands at ~11 A/B fails, 1 pass).

---

## TL;DR

1. **These are with-move continuation grazes, not knife-catches.** At entry the tape is already going the trade's way: 97% of trades have the last 1 minute in trade-favor (mechanical — the 3-tick confirm), 92% the last 5 minutes, and even **72–76% the last 15–30 minutes** (median +0.4R ≈ 70 ticks of favor-move over the prior 30m). The "counter-move onto the level" barely exists: the approach leg from the last favorable zz20 pivot lasts **~1 minute** with median depth **−9 ticks** — half the entries print *above* the last pivot high. Mechanically it's often the *level* doing the approaching: 29/102 trades saw the developing POC relocate >200 ticks in the prior 30 minutes. Longs are always above the POC, shorts always below (100%/100%) — the engine fades from the hugged side, and the hugged side is the trending side.
2. **The rare true pullback is the best trade in the book.** Cutting by 15-minute momentum: the 19 trades (19%) where price actually pulled back *against* the trade direction onto the POC ran 2 stops, **+$18.6k, r̄ 0.39** — double the favor-trend cohort's r̄ 0.19 (73 trades, 12 stops, +$33.2k). Flat-15m tape is dead money (10 trades, 3 stops, −$2.2k). Same shape as the counter-overnight read below.
3. **The first RTH hour is the edge core.** 9:00–9:59 entries: 39 trades, 3 stops, **+$33.4k = 67% of net** ($855/trade). 10h and 11h thin out to ~$200–320/trade with 9/37 and 5/26 stops. The early touch — POC still fresh from the overnight distribution — is the money; late-morning touches are the leftovers.
4. **Losses fail fast and clean; the whole net lives in trades that never struggle.** Stops: median MFE 0.13R, only 3/17 ever saw +0.3R — they're wrong from the print. The 42 trades that never touched −0.25R had **zero stops and +$50.8k ≈ 102% of net**; the 46 that reached −0.4R are collectively **−$15.3k** (17 stop, 29 recover). But 43/85 winners did dip ≤−0.25R and recovered — shallow heat is normal, deep heat is net-negative. Concentration is moderate (top 10 trades = 46% of net; 17 stops on 17 distinct sessions, 12 of 17 months, max 2 consecutive) — this run is not the tail-lottery 30badf94 was.
5. **S/R confluence is absent and unneeded.** The nearest non-traded reference (ONH/ONL/open/pd high/low/close/pdPOC…) sits a median **77 ticks** away from entry; being <30t from one does *not* help (r̄ 0.19 vs 0.30 for >160t; the 80–160t bucket is the worst at −$552 — non-monotone, i.e., noise). The POC itself is similarly unanchored (median 75t from any ref). VP geometry is null too: VA width, POC position in the VA, POC position in the ON range, VA/ON coverage — all AUC ≤ 0.62 ns. **POC stability doesn't sort outcomes either**: the frozen-POC (<5t drift in 30m) cohort holds 8/17 stops; migrating-POC trades do fine.
6. **The one entry-time theme in the stops is over-extension, and it's sub-significant.** Stops skew toward tape that's already stretched in the trade's own direction: beyond the prior-day extreme in favor terms (median +293t vs +99t, AUC 0.62 p=.12), continuing a same-direction overnight move (gx_ret_r AUC 0.62), fresher favor-side highs (min_since_hi 113 vs 309 min), zz20 HH/HL state (22% stop rate vs 13–14%). The cleanest quartile cut: entries **against** the overnight move (ON moved >1.5R adverse) ran 26 trades / 2 stops / **+$25.0k / r̄ 0.38**, while the "riding a moderately-extended ON move" quartile (+0.4..+3.3R) ran 25 / 7 / +$1.5k. Middle-dip non-monotone, so a lead at best — same family as the raw `d_Open` signal (stops enter above the RTH open both directions, AUC 0.657 p=.04, but it evaporates trade-relative).
7. **Chop is a null here.** `overlap_10` ≈ 0.60 for both cohorts (AUC 0.53), `rng_compress` ≈ 0.48 both — the flagship's chop-predicts-stops finding does **not** transfer to the POC fade, consistent with the corrected 03f4c56c scan. Entries typically come out of mild compression (15m range ≈ half the prior hour) with the last 1m bar closing near its favor-side extreme (closeloc 0.74), and none of it grades outcomes.
8. **Once underwater, the only live tell is the higher-timeframe slope — agreeing with you is bad.** At −0.4R, trades whose 30-min 5m-close slope still points in *trade-favor* mostly die (AUC 0.71, p=.019, stops +6.8 t/bar vs recoverers −3.8); everything else at the underwater anchors — efficiency, overlap, push count, low cadence, retrace — is flat (AUC 0.42–0.55). Reading: recoverers are underwater inside an already-rotating tape (the dip is rotation), stops are underwater against a still-standing trend reading (the dip is an impulse break). Echoes the loser-orderflow finding that stops die of drift, not capitulation.

## The composite picture

A canonical 0a20b6a9 winner: NQ trends through the overnight into the 9:30 bell, the developing Globex POC ratchets along beneath (above) price, and in the first RTH hour price wiggles a handful of ticks back onto the level — no real counter-leg, one minute of hesitation inside mild compression — confirms 3 ticks and leaves. It never looks back (median win MAE −0.25R, 0/85 winners saw −0.4R+ and still trailed out… the deep-heat recoverers are the grind-outs that dilute r̄). The canonical stop is the same graze taken *late* (10–11am), after the move has already cleared the prior day's extreme or run an extended overnight leg — the fade of the last exhaustion wiggle of a stretched move; it goes red on the next impulse and never comes back (MFE 0.13R).

## Stop gallery (all 17)

| session | time | dir | ON move (R, favor) | 30m mom (R) | pos in ON range | zz20 | POC drift 30m (t) | nearest ref (t) | MFE (R) |
|---|---|---|---|---|---|---|---|---|---|
| 2025-02-06 | 09:42 | S | −0.3 | +0.0 | 0.54 | −1 | 0 | 10 | 0.10 |
| 2025-03-14 | 10:29 | S | −3.5 | +1.9 | 0.30 | +1 | 207 | 9 | 0.00 |
| 2025-04-08 | 10:28 | L | +11.1 | +1.9 | 1.31 | 0 | 1979 | 110 | 0.13 |
| 2025-04-16 | 11:20 | S | +4.7 | +0.3 | 0.70 | 0 | 200 | 108 | 0.02 |
| 2025-04-29 | 10:00 | S | +1.2 | −1.3 | 0.58 | 0 | 220 | 122 | 0.42 |
| 2025-05-06 | 11:54 | S | +5.0 | +1.1 | 0.57 | +1 | 0 | 375 | 0.21 |
| 2025-06-04 | 11:51 | L | +0.9 | −0.1 | 0.94 | 0 | 0 | 4 | 0.13 |
| 2025-07-24 | 09:40 | S | +0.7 | +0.3 | 0.84 | +1 | 132 | 60 | 0.18 |
| 2025-09-10 | 11:01 | L | +2.3 | +0.1 | 0.61 | −1 | 140 | 86 | 0.04 |
| 2025-10-24 | 10:40 | L | +4.2 | −0.4 | 0.98 | +1 | 0 | 24 | 0.43 |
| 2026-02-06 | 10:16 | S | −7.9 | +4.2 | 0.19 | +1 | 1000 | 32 | 0.10 |
| 2026-03-17 | 10:28 | L | +2.6 | −0.1 | 1.16 | 0 | 0 | 150 | 0.43 |
| 2026-03-18 | 10:05 | S | +2.1 | +0.3 | 0.78 | +1 | 840 | 42 | 0.24 |
| 2026-05-21 | 09:38 | L | −0.9 | +0.5 | 0.42 | +1 | 102 | 173 | 0.04 |
| 2026-05-29 | 11:37 | L | +1.2 | +1.3 | 1.20 | −1 | 0 | 133 | 0.02 |
| 2026-06-03 | 10:43 | L | +0.3 | +1.5 | 0.71 | +1 | 0 | 33 | 0.23 |
| 2026-06-10 | 10:21 | S | +5.5 | −0.7 | 0.16 | +1 | 0 | 151 | 0.02 |

14/17 after 10:00; 12/17 with the overnight moving the trade's way (≥+0.3R); three entries *beyond* the ON extreme (pos >1.0). The 2025-04-08 long is the caricature: +11R overnight rip, POC 500 points behind and sprinting (1979t/30m), entry above the ON high — a continuation graze of a vertical tape.

## Leads worth a proper test (and what not to do)

- **Time-of-day** (entry_close 10:00 vs 12:00) is the strongest, simplest cut — but it's exactly the kind of post-hoc calendar carve the weekly-VWAP lesson warns about: re-cut on the sibling runs/other configs first, then engine A/B.
- **The 15m-adverse "true pullback" preference and the counter-overnight pocket** are the same underlying idea as the FAILED `approach_mom_veto` — that veto killed ⅔ of the book and the ghosts were +$30k. Any retest must be a *soft* preference (sizing or ordering), not a hard entry veto, and must confront that the favor-trend cohort still made +$33k.
- **Extension gates (pd-extreme, ON-continuation)** are sub-significant on 17 stops; park unless they replicate on the level-stop sibling and the 03f4c56c population.

## Artifacts

- `data/research/market-structure/extract_vp_sr_drift.py` — new supplemental extractor (VP geometry, S/R distances, POC stability, gap context; timestamp-anchored, causal `levels_in_force` reads)
- `data/research/market-structure/features_dtfes_0a20b6a9.parquet`, `vpsr_dtfes_0a20b6a9.parquet`
