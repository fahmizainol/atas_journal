# The trail that "felt too tight" — what three replay sittings actually said

**Date:** 2026-08-10
**Data:** the three replay sittings of 2026-08-10 — 2025-03-13 (NQH5, 17 trades,
+$1,281), 2025-12-04 (NQZ5, 22 trades, +$591), 2026-02-10 (NQH6, 20 trades,
+$320) — order logs from `data/replays/`, raced against the cached tick tape.
**Script:** `data/research/replay-trail/whatif.py` — a Python port of the
browser fill engine (`frontend/src/lib/replaySim.ts`) that reproduces each
stored sitting **to the exact dollar** before any counterfactual is trusted.
**Question:** the trail felt "just nice" in the first sitting and "too tight"
in the last two — is that volatility? ATR?
**Verdict:** neither, and mostly not the setting either. **All three sittings
were traded on the same 25-tick trail** (the first sitting's saved prefs say
50t, but that is the ticket state at attempt creation — 16 of its 17 orders
snapshot 25t). ATR was flat across the days and the tape's violence was near
identical. What separated them is **follow-through**: the first day's entries
ran twice as far (median 3-min MFE 151t vs 62–78t), so the same trail banked
$303 average wins there and $70–108 on the others. The counterfactual splits
the two "tight" days: **2025-12-04 genuinely wanted a 50t trail (3.6× the
net); on 2026-02-10 the 25t trail was optimal and 50t loses $1.7k.** A fixed
wider trail is not the fix — day-type (drift vs churn) is the variable.

---

## 1. What was actually traded

All three sittings: blind replay from the 09:30 RTH open at 1×, 1 contract,
market entries, stop 50t, `step=0` (ladder rungs a full trail-distance
apart), breakeven +3t.

| sitting | trades | net | avg win | trail exits | targets hit | prefs trail | **per-order trail** |
|---|---|---|---|---|---|---|---|
| 2025-03-13 | 17 | +$1,281 | $303 | 10/17 | 2 (of 100t) | 50t | **25t × 16, 50t × 1** |
| 2025-12-04 | 22 | +$591 | $108 | 17/22 | 1 (of 150t) | 25t | 25t × 22 |
| 2026-02-10 | 20 | +$320 | $70 | 18/20 | 0 (of 200t) | 25t | 25t × 20 |

The prefs-snapshot trap: `attempt.json`'s `prefs` are captured at the first
fill; only trade #1 of the first sitting ran 50t before the ticket was dropped
to 25t, where it stayed for all ~58 remaining trades across three days. The
"nice" session and the "tight" sessions were the same setting. Per-order
`trail` snapshots in `log.json` are the truth.

## 2. The port validates, so the grids mean something

The harness replays each stored order log tick-by-tick with the browser
engine's semantics (trail ladder, stop-books-the-gap, 1t spread, 1t queue,
$3.50/side) and reproduces every sitting's stored trade count and net PnL to
the dollar before running counterfactuals. Two traps it had to know about,
both worth remembering:

- **glued-tape idx**: `OrderRec.idx` counts from the start of the glued tape
  (context days prepended by replay resume), so it overflows the single-day
  parquet — indices are re-derived from timestamps;
- **tape clock**: the browser tape's ms are the display-zone wall clock read
  as epoch-ms, not UTC.

## 3. Was it volatility? Measure four things, not one

Texture of each traded window (first fill → last exit), from the tape:

| | 03-13 "nice" | 12-04 "tight" | 02-10 "tight" |
|---|---|---|---|
| window | 28 min | 41 min | 16 min |
| box (high−low) | **950t** | 559t | 579t |
| speed (path ticks/min) | 2,980 | 2,072 | **3,129** |
| violence (25t+ reversals/min) | 10.3 | 7.5 | **11.2** |
| median swing leg | 45t | 43t | 45t |
| **drift (net ticks/min)** | **26** | 11 | 24 |
| **churn (path ÷ net)** | **114×** | 193× | 128× |
| median 3-min MFE after entry | **151t** | 62t | 78t |

- Daily ATR(14) was flat across the three days (497 / 454 / 524 pts) — and it
  is a 14-session box average, blind to everything in this table. Not ATR.
- The violence the last two days *felt* like is real — but the "nice" day was
  nearly as violent (10.3 vs 11.2 reversals/min) and had the biggest box.
  Not violence either.
- The swing anatomy is eerily constant: a move runs a **median ~45t before
  reversing 25t+**, on all three days. NQ's open texture was the same texture.
- What separated the days is the last three rows: 03-13's violence was
  stacked in one direction (drift 26 t/min, churn 114×) and its entries kept
  going (MFE 151t). Same waves everywhere; only one day had a tide.

A 25t trail sits under the universal ~45t swing, so it always exits on a
routine wiggle. On a drift day it gets carried several rungs first and
re-enters into a market still going — that is the "just nice" feel. On a
churn day the first rung is where the trade dies.

## 4. The counterfactual: same entries, different trail

Re-running each log with the trail distance/step overridden (entries frozen
at their recorded times):

**2025-12-04 — the instinct was right.** 25t was genuinely too tight:

| dist/step | n | net | note |
|---|---|---|---|
| 25t/0 (as traded) | 22 | +$591 | |
| 35t/0 | 17 | +$1,269 | |
| **50t/0** | 15 | **+$2,121** | 3.6× — best of the sweep |
| 60t/0 | 14 | +$1,848 | |
| 100t/0 | 14 | +$758 | too loose again |

**2026-02-10 — the instinct was wrong.** The tightness saved the session:

| dist/step | n | net | note |
|---|---|---|---|
| **25t/0 (as traded)** | 20 | **+$320** | best fixed-rung distance |
| 35t/0 | 18 | −$438 | |
| 50t/0 | 15 | −$1,733 | trail-outs become 50t stops |
| 75t/0 | 14 | −$2,365 | |
| 25t/**5** | 20 | +$1,075 | finer ladder; see §5 |

**2025-03-13** was nearly flat across the sweep (+$1.0k to +$1.4k everywhere
from 25t to 100t) — on a real drift day the trail distance barely mattered.

So the two "too tight" days want opposite fixes. A fixed wider trail buys
2025-12-04's improvement by donating it back (with interest) on 2026-02-10.
The variable that decides which day you are in is drift/churn, not any trail
knob — consistent with `atr-trail.md` (ATR-scaled trails failed at every
multiplier) and `vol-clock.md` (ATR sets the pace, not the geometry).

## 5. Side findings

- **The targets are decorative.** 100t → 150t → 200t targets were hit 2 / 1 /
  0 times; a 25t ladder almost never survives to them. The bracket drifted
  into internal inconsistency: target widening while the trail says "out at
  the first 25t stall".
- **`trailStepTicks` has never been turned.** All sittings ran step 0, i.e.
  rungs a full distance apart — the stop lags the high-water mark by 22–47t
  and moves in 25t jumps. A 5t step at 25t dist was +$755 on 2026-02-10,
  ~flat on 2025-12-04, −$325 on 2025-03-13. Mixed, not adopted — but at ~20
  trades a day these are directional reads, and the knob exists.
- **Replay is drilling the sub-30s leak.** Average holds were 16s / 18s / 8s.
  The manual-trade behaviour audit found sub-30s trades to be the one real
  leak in the live journal — and a 25t trail in 100t+/min noise
  *mechanically manufactures* sub-30s trades. The practice loop is rehearsing
  the leak.
- **Fees bite at this win size**: 2026-02-10 paid $140 in fees against $320
  net; per-trade expectancy fell $75 → $27 → $16 across the sittings.

## 6. Caveats

- Counterfactual entries are frozen at recorded times; with longer holds some
  later entries become flips/scale-ins (`reduce` exits in the grids), so
  wide-trail rows drift from what would actually have been traded.
- 17–22 trades per sitting: read direction, not magnitude. Nothing here is an
  A/B; nothing was adopted.
- The churn/drift numbers are descriptive of three sittings. If a "what kind
  of day is this" readout ever ships (rolling path/min + drift/min is cheap
  to compute live), it should be validated on more than three days first.

## 7. Addendum (2026-08-10): the readout shipped — as a readout

The day-type strip is now on the Simulator: `frontend/src/lib/dayRead.ts`
computes **TIDE** (10-min net drift, ticks), **SWING** (median leg before a
25t reversal) and **EXT** (median 3-min MFE of the last 5 entries, each scored
3 minutes after entry — fully causal, so blind-safe), and buckets EXT into
paying (≥100t) / grudging / dry (<60t). It renders as a strip in the ticket
panel and a colored dot on the rail, with 30s of hysteresis on the verdict.
Cross-validated against this study's Python on the 2026-02-10 tape at
minute 10: EXT matches exactly (59t), tide within a window-boundary tick.

Per the caveat above, **nothing is wired to it** — it refuses no order and
moves no default. The validation plan is the operating one: run the next
~10 blind sittings against it, note the minute-10 verdict in the attempt
note, and re-grade afterwards with `whatif.py`. The thresholds live in
`dayRead.ts` as named constants for when that sample says to move them.
