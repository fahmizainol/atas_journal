# Pre vs post archive — what actually decayed

**Verdict: the edge was real and it did decay, but not for the reason the
numbers first suggested. Entry selection, size and level choice are all
exonerated. What changed is that the pre-era book was banked by a stop the
trader walked up behind a running trade, and that behaviour is now absent —
zero stop fills in 230 post-era trades. Three journal faults had to be fixed
before any of this was measurable.**

- **Date:** 2026-09-17
- Sources: `data/journal.db.pre-mode-folders.bak` (the only home of the pre era),
  `data/journal.db`, `data/live/orders/*/*/orders.jsonl`, the session tick cache,
  and four surviving screen recordings under `/mnt/d/Videos/Radeon ReLive/Trades/`
- Visual: [approach-shapes.html](approach-shapes.html)
- Pool: 203 pre-era and 120 post-era **live** logical trades, de-duplicated

---

## 1. Three journal faults — read before quoting any pre-era number

### 1.1 The 12-hour double-import (+$3,475 of P&L that never happened)

ATAS exports **wall-clock local time**; the box runs **UTC+8**. Two sessions were
imported twice — once read as UTC+8 (correct) and once as `America/New_York` —
producing the *same* rows with the *same* local-time string and a different offset.
UTC+8 − (−4) = exactly 12:00:00. `dedupe_key` hashes the timestamp, so it never fired.

| | `open_ts_local` | `open_ts_utc` | ET |
|---|---|---|---|
| row 0 | `2026-05-26T22:14:09.876000+08:00` | `14:14:09Z` | 10:14:09 ✓ |
| row 13 | `2026-05-26T22:14:09.876000−04:00` | `2026-05-27T02:14:09Z` | 22:14:09 ✗ |

| source_file | rows → real | journal | truth |
|---|---|---|---|
| `..._26052026_26052026_30k_break_3kprofit.xlsx` | 12 → **6** | +$4,800 | **+$2,400** |
| `..._27052026_28052026.xlsx` | 38 → **19** | +$2,150 | **+$1,075** |

**25 phantom live trades.** Across all modes it is 219 surplus rows of 1,681 (13%),
netting −$400, so replay studies are contaminated but not directionally biased.
The live post era has **zero** duplicates.

`executions` was imported **once and is clean** — the corruption is confined to
`atas_journal`, so fills are the safe source of truth.

**Fast tell:** `sum(gross_pnl) / sum(points)`. NQ is $20/pt; 26-MAY computed to
**$41.77/pt**, i.e. 20 doubled. Run it per session before trusting a total.

**De-duplication rule:** drop rows duplicated on
`(source_file, direction, avg_entry, avg_exit, max_contracts, round(duration_s,3))`,
keeping the first. Do **not** edit the `.bak` — it is the only copy.

### 1.2 Halved lots on 2026-06-22

ATAS exported only one of two lots for trades #1198 and #1206, so the builder
halved both: +$365 should be **+$730**, −$275 should be **−$545**. The day is
**−$3,480, not −$3,580** (confirmed against the on-screen Closed PnL ladder).
That file's `executions.ts_utc` are also **second-truncated** (all `.000`), so
tick-window MFE/MAE must pad the exit by ~1.5 s.

### 1.3 A missing trade on 2026-06-23

ATAS's own Closed PnL ends at **−$1,185** for account TEST002 against the
journal's **−$1,005** — a **$180** gap, possibly an unexported trade, which would
make the day −$2,385.

### 1.4 `attempt_videos` can be wrong in substance

The row mapping the 26-MAY spreadsheet to `26-MAY-2026-01.mp4` exists and is
structurally fine, but **the video does not contain those trades**. The tape runs
09:31:00 → 10:11:25 ET (verified against `NQM6_2026-05-26_day.parquet`, not the
chart tooltip); the journal runs 10:14:09 → 10:47:53. Zero overlap — it is a
**replay warm-up** that ends three minutes before he went live. Check
tape-versus-journal time overlap before trusting any mapping.

---

## 2. Corrected era comparison

Live only, normalised to **1 NQ ($20/pt)** so the instrument change does not
confound it.

| | n | days | WR | avg W | avg L | R | $/trade | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PRE as-journaled | 228 | 17 | 50.4% | $278.77 | $203.84 | 1.37 | +$40.48 | +$9,228 |
| **PRE de-duplicated** | **203** | **16** | **47.3%** | **$292.80** | **$199.08** | **1.47** | **+$34.52** | **+$7,007** |
| POST | 120 | 13 | 44.2% | $234.57 | $253.59 | 0.93 | −$35.87 | −$4,304 |

De-duplicating moves win rate **down** (closer to post) and R **up**. The gap
survives: a ~$70/trade swing.

At the size actually traded — pre era is **203 trades of 1 NQ**, post era is
**109 of 120 in MNQ at 5–7 lots**, i.e. roughly half the exposure per trade:

| | p10 | median | p90 |
|---|---:|---:|---:|
| PRE net $/trade | −$408 | −$10 | +$549 |
| POST net $/trade | −$274 | −$20 | +$269 |
| PRE winners | +$20 | **+$282** | **+$888** |
| POST winners | +$7 | **+$103** | **+$340** |
| PRE losers | −$478 | **−$220** | −$50 |
| POST losers | −$310 | **−$170** | −$24 |

Halving exposure predicts both tails scaling by ~0.5. **Winners fell to 0.36–0.38;
losers only to 0.65–0.77.** The asymmetry survives the sizing change.

---

## 3. Where the money was — hold bands

Normalised to 1 NQ. Median **ticks** captured, and net USD at traded size:

| hold | PRE n / WR / med tick / win / loss | POST n / WR / med tick / win / loss |
|---|---|---|
| **<30s** | 113 / 26.5% / −29 / +10 / **−41.5** | 49 / 34.7% / −31 / +39 / −49.5 |
| **30–60s** | 47 / **70.2%** / +11 / **+46** / **−30** | 32 / **37.5%** / −38 / **+30** / **−46** |
| **1–2m** | 26 / 73.1% / **+51** / **+80** / −38.5 | 22 / 54.5% / **+3** / +41 / −63 |
| 2–5m | 12 / 75.0% / +22 / +54.5 / −48 | 16 / 68.8% / +29.5 / +55 / −65 |
| >5m | 5 / 100% / +102 | 1 / — / +9 |

| hold | PRE net $ | POST net $ |
|---|---:|---:|
| <30s | **−$14,295** | −$2,657 |
| 30–60s | **+$6,475** | −$2,814 |
| 1–2m | **+$8,330** | +$169 |
| 2–5m | +$1,610 | +$1,390 |
| >5m | +$4,835 | +$4 |
| **total** | **+$6,955** | **−$3,908** |

Three readings:

1. **Sub-30s is the oldest and largest leak** — 56% of all pre-era trades, bleeding
   $14,295 at one contract, a 4:1 loss-to-win size against a 27% hit rate.
2. **The 30–60s band flipped sign.** Win +46t / loss −30t became win +30t / loss
   −46t: the two numbers traded places. It is the only band that changed sign and
   it is well populated (47 vs 32).
3. **Pre era: the winner was bigger than the loser in every band over 30s. Post
   era: the loser is bigger in every band.**

The >5m and 2–5m rows are n=5 and n=12 — too thin to lean on. Two of the pre-era
>5m trades are 26-MAY positions **averaged into over several minutes**, so that
bucket is not "held still" (§7.3).

---

## 4. What the recordings show

Four tapes survive with usable coverage. Method for reading them is in
[[day-replayer-replaces-video]] (memory) — anchor on the Closed-PnL panel, never
the bookmark, and distinguish a **committed** order row (cancel box + qty chip)
from an **uncommitted drag preview** (neither).

| | 30-JUN **+$3,295** | 02-JUL **+$1,400** | 22-JUN **−$3,480** | 23-JUN **−$2,205** |
|---|---|---|---|---|
| entries resting vs market | **4/4** | **4/5** | **0/16** | 4/16 |
| naked at entry (median) | ~5 s | **6.0 s** | ~5 s | **5.6 s** |
| stop ratcheted up after commit | yes | **every winner, 2–6×** | almost never | 3 of 5 modifies |
| drags that never resolved | 0 | 0 | 2 → **−$1,100** | 4 → **−$1,115** |
| MFE capture | **86%** | 64% | — | 47% |

**What does NOT separate good days from bad: speed to place the stop.** It is
~6 s naked on the green days too, and ATAS was configured `SL/TP: Off` — every
pre-era entry was unprotected *by setting*, not by fumbling. He also drags the
preview **wider before releasing, every time**; the committed stop averages **$85
wider than his first instinct**.

**What does separate them:**

1. **The entry is a resting stop order, not a chart click.**
2. **A committed stop gets ratcheted up and effectively never widened.** Across
   22-JUN's 12 frame-read trades a committed stop was widened **zero** times;
   23-JUN had 5 committed modifies all day, 3 in his favour and 2 wider worth
   **$15 total**. Meanwhile 02-JUL's three winners trailed out with 2, ≥5 and ≥6
   upward moves each.
3. **On green days every drag resolves into a click.** The expensive failure is
   the 4–5 s indecisive drag that never commits: on 23-JUN the four trades that
   never got a stop are **64% of the day's taped loss**.

Stops that fired filled **$10–90 past** their level — proven ordinary slippage,
not override: at 8 fps, 23-JUN's T13 ran −$395 → flat → booked −$435 inside
**125 ms**, which no manual click can produce.

---

## 5. The exit inversion

Pre era, 42 exits characterised across the four tapes; post era, all 230 trades
from `orders.jsonl`:

| exit | PRE | POST |
|---|---:|---:|
| stop absorbing a loss | **40%** | **0%** |
| **trailed stop banking a profit** | **17%** | **0%** |
| manual / hand flatten | 38% | 91% |
| target fill | 2% | 9% |

**7 of 15 pre-era winners died on a stop walked into profit — his most common way
of winning.** Post era that fires zero times in 230 trades, although a bracket is
attached to every one: `stop_ticks` set on 303/303 submits (median 66t),
`target_ticks` on 303/303 (median 120t), break-even armed on 70/303, trail on 30/303.

Post-era exit economics, in points:

| reason | n | median | total |
|---|---:|---:|---:|
| reduce (hand scale-out) | 112 | +10.78 | +618 |
| manual (hand flatten) | 97 | **−9.40** | **−615** |
| target | 21 | **+21.00** | +421 |
| stop | **0** | — | — |

**Time to breakeven.** Pre era (8 timeable tape observations): median **~32 s**,
about **38%** of the way into the trade, and he *jumps past* breakeven to a real
profit stop (+$60, +$76, +$275, +$305, +$355, +$505) rather than parking on it.
On 26-MAY's best trade he held the full −$200 stop for the first **78 seconds**
while the trade went $715 onside, then ratcheted four times.

Post era (measured, all 230): reached breakeven-or-better on **38 trades (17%)**,
median **55.5 s**, and — the telling number — **83% of the way into the trade's
life**. The stop now chases and arrives just before he exits by hand anyway.

---

## 6. Regime — the ruler, and the shape that paid

30-second vol ruler at entry (`vol_med_ticks_30s`), recomputed from the tape for
both eras so the alignment guard does not bias coverage (201/203 and 111/120):

| | p10 | median | p90 |
|---|---:|---:|---:|
| PRE | 48 t | **78.5 t** | **129 t** |
| POST | 29 t | **62.5 t** | **78 t** |

**The pre era's median is the post era's p90.** Against the corpus reference
(~71t at the open, 42t across the day, 27t settled), the pre era entered at
above-open volatility.

| ruler | PRE n / WR / net / per-trade | POST n / WR / net / per-trade |
|---|---|---|
| <40t | 6 / 50% / +$465 / +$78 | 26 / 42% / **−$1,863** / **−$72** |
| **40–60t** | 47 / 55% / **+$4,290** / **+$91** | 26 / 54% / **+$1,047** / **+$40** |
| 60–80t | 52 / 48% / +$1,580 / +$30 | 51 / 45% / −$1,072 / −$21 |
| 80–110t | 60 / 42% / **−$2,375** / −$40 | 8 / 25% / −$956 / −$120 |
| >110t | 36 / 47% / **+$3,040** / **+$84** | **0 trades** |

**40–60t is the only band profitable in both eras.** The post era added 26 trades
below 40t (−$1,863) and abandoned the >110t band entirely.

### 6.1 Approach shape — the staircase

Approach efficiency = |net move over 15 min| ÷ range over the same window, with
swings counted as reversals exceeding 15% of that range:

| | PRE swings / range / net | POST swings / range / net |
|---|---|---|
| chop (<0.25) | 10.5 / 110 pt / −$740 | 9 / 90 pt / −$650 |
| **staircase (0.25–0.5)** | **7 / 137 pt / +$7,960** | 9 / 86 pt / −$115 |
| glide (0.5–0.75) | 2.5 / 137 pt / +$1,490 | **0 / 126 pt / −$1,926** |
| ramp (0.75+) | 0 / 180 pt / +$305 | 0 / 117 pt / −$58 |

Nearly the whole pre-era book came from one regime: **wide range, six to eight
real pullbacks, net progress anyway** — a trend that keeps offering a pullback to
put a buy-stop above. The clean ramp made almost nothing (+$10/trade on 30 trades);
the chop box lost.

**Honest bound.** The staircase band spans **13 separate days** at a **64%** win
rate, so the shape is not one lucky session (best day is 25% of its total). But the
**median staircase trade made $25**, and three trades supply **44%** of the $7,960.
The shape earns the win rate; only holding the winners turns it into the total —
which is §5 restated from the other direction.

Post-era approaches are measurably choppier (median efficiency 0.59 → 0.46; chop
share 17.5% → 25%), **but chop is not where the money went**. The worst post-era
band is 0.5–0.75 — decently trending tape — at −$1,926. **There is currently no
regime in which the post era is profitable.**

Note the post-era "staircase" is not one: same efficiency ratio produced by **9
swings in 86 points** instead of 7 in 137. The statistic matches; the structure
does not.

---

## 7. What is NOT the explanation

### 7.1 Momentum versus mean reversion

Signed to the trade's direction at entry:

| | PRE | POST |
|---|---:|---:|
| prior 1-minute move | +1.25 pt | −3.12 pt |
| prior 5-minute move | **+16.38 pt** | **+4.31 pt** |
| entry location in prior 15m range, own direction | **0.70** | 0.62 |

The style is **trend continuation entered on a pullback** — momentum at the
5-minute scale, flat-to-fading at the 1-minute scale. Splitting on the 1-minute
move (±3 pt), the pre-era book was a near-even 90/99 split that earned **+$39 and
+$38 per trade respectively** — neither half carried it, and both lose now
(−$23, −$27). **The momentum/reversion axis does not explain the decay.**

The post-era "flat" bucket (no 1-minute move either way) is worth flagging: 13
trades, held **66 s**, **−$97 each** — a third of the post-era loss taken when
nothing was happening, overlapping the sub-40t band.

### 7.2 Size

Exposure roughly halved (1 NQ → 5–7 MNQ). An earlier claim that bigger size
predicted worse outcomes was a **lot-frame artifact** — see
[[live-size-outcome-inversion]]; on the logical frame the correlation is −0.002
(p=0.98).

### 7.3 Averaging in — real, but small and mostly benign

**40 of 203 pre-era trades (20%) were built from more than one entry price, across
12 distinct days**, median entry spread 3.75 pt. Adding **against** the position is
only **13 trades (6%), −$2,675**; adding with it (27 trades) made +$1,575. All
multi-entry trades netted **−$1,100** against +$6,955 for the book. **Post era it
is extinct: 1 of 120.**

Consequence for §3: `max_contracts` on those rows is the **count of adds**, not a
sizing decision, and `duration_s` is first-fill-to-exit on a position still being
assembled.

### 7.4 Hold-time adaptation — a real loss, separate from everything above

Spearman, entry ruler vs hold time: **PRE ρ = −0.225, p = 0.001**; **POST
ρ = −0.030, p = 0.75**. He used to shorten holds in a louder tape, correctly. That
relationship is now statistically absent — ~33 s regardless of whether the tape is
moving 29 or 110 ticks per 30 s.

Overall holds: PRE median 27 s (winners 43 s, **losers 16 s**, ratio 2.7×); POST
median 33 s (winners 58 s, **losers 30 s**, ratio 1.9×). **The loser hold nearly
doubled.** In the <40t and 80–110t bands the asymmetry actually inverts post-era —
losers held *longer* than winners — and those two bands account for −$2,819.

---

## 8. Claims retracted during this pass

Recorded because each was stated confidently and was wrong:

1. **"He drags his stop wider three times then removes it."** Those rows were an
   *uncommitted drag preview*, not a working order. He had no stop at all.
2. **"He overrides his stops."** No — stops fill 2–4 ticks past, ordinary slippage.
3. **"Getting the stop on faster is the fix."** Naked time is ~6 s on the green
   days too; it does not discriminate.
4. **"26-MAY is the longest-hold day — he sat still."** Those were positions being
   assembled, and the video does not even contain them.
5. **"Bigger size predicted worse outcomes."** Lot-frame artifact.

---

## What's missing

- **Six of the ten live recordings are unwatched** (29-JUN, 01-JUL, 04-JUN,
  05-JUN, and the two remaining files). The four read here are two of the best
  days and two of the worst, which may exaggerate the contrast.
- **The manual-exit what-if is unpriced** — what the 97 post-era hand-flattens
  would have done left to their brackets. This is the one number that would price
  the fix, and `replay_whatif` already has the machinery.
- **The >110t pre-era band (+$3,040, n=36)** is unexplained and probably
  concentrated in few days; it has not been day-split.
- **Whether the ratchet scales past ~2-minute holds** is untested — the pre-era
  evidence for it is 8 timeable observations.
- **`archived` is still 0 on all 534 sessions** in the live DB, so the default
  aggregate continues to pool research trades with real ones.
