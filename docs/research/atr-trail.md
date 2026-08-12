# The ATR-scaled trailing stop — built, A/B'd, resolved negative

**Date:** 2026-08-08
**Strategy:** `vwap-upper-band-bounce`, engine v15
**Window:** 2024-03-03 → 2026-06-30 (585 labelled sessions, 401 baseline trades)
**Verdict:** the knob ships **off**. A trail whose distance is set by the day's
volatility loses to a flat 75 ticks at every multiplier tried, and the
regime cut refutes the idea's premise with the sign reversed.

---

## 1. The idea

"Trailing ATR stop" is a real, named family — the Chandelier Exit
(`highest_high(N) − k × ATR(N)`, k≈3), Wilder's own 1978 Volatility System, and
SuperTrend are the same mechanism: a monotone ratchet whose *distance* is
volatility-scaled instead of fixed. This engine already had the ratchet
(`trail_stop_ticks` / `trail_step_ticks` / `trail_breakeven_ticks`) and it is the
dominant exit on this book — **270 of the baseline's 401 trades leave on the
trail**, so the distance is one of the highest-leverage numbers in the config.

Three prior studies argued against volatility-scaling it:

- **[ATR × upper band](atr-vwap-band.md)** — intraday ATR is the band renamed
  (ρ .96); geometry here reads absolute, "no ATR-scaled stops".
- **[Winner landing depth](winner-landing-depth.md)** — winners bottom ~34 ticks
  under dev1 in absolute ticks, not σ-scaled.
- **[Triple-barrier relabel](triple-barrier-relabel.md)** — the *wide* trailing
  stop is load-bearing; the 2σ path-tax on tightening was 62%/34% of net.

So the least-doomed shape was chosen deliberately: **ATR fixed at entry**. The
daily ATR(14) through the *prior* session (the vol clock's own number) is read
**once, at the session open**, and the distance then holds still all day. That
gives a wider trail on a hot day without ever letting the trail *tighten* on a
live trade because the afternoon went quiet — which is the failure mode a
breathing Chandelier has and the reason the fixed-at-entry variant was the one
worth spending a run on.

## 2. Calibration

The multiplier is a fraction because a daily range dwarfs an intraday trail. Over
this window NQ's daily ATR ran 210–890 points (median 390), so the baseline's
flat 75 ticks (18.75 points) is **0.048 × the median daily ATR**. `0.05` is
therefore the *vol-neutral* setting: it keeps the average distance where the
baseline has it and changes only how that distance is **allocated across days**.

| multiplier | quiet days | mid days | hot days |
|---|---|---|---|
| 0.040 | 36–78 (med **50**) | 39–98 (med 51) | 43–142 (med 75) |
| 0.050 | 46–98 (med **63**) | 48–122 (med 64) | 54–178 (med 94) |
| 0.065 | 59–127 (med **82**) | 63–159 (med 83) | 70–231 (med 122) |

The baseline trails at a flat **75** on every one of them.

## 3. The A/B

Arm 0 is the v15 twin of the pinned v14 baseline — identical knobs, re-run on the
bumped engine. It reproduces the baseline **exactly** (401 trades, $152,923, PF
1.58, DD −$28,360, exits `{trail: 270, stop: 115, target: 16}`), which is what
licenses reading the other three arms at all.

| arm | trades | net | PF | win% | maxDD | Sharpe |
|---|---|---|---|---|---|---|
| **twin (fixed 75t)** | 401 | **$152,923** | **1.58** | 70.1 | −$28,360 | **2.03** |
| atr 0.04 | 395 | $62,100 | 1.25 | 71.4 | −$43,184 | 1.00 |
| atr 0.05 (vol-neutral) | 390 | $93,356 | 1.33 | 68.2 | −$35,202 | 1.32 |
| atr 0.065 | 386 | $113,313 | 1.34 | 62.7 | −$28,750 | 1.43 |

Every arm loses: −59%, −39% and −26% of net, with Sharpe halving at the tight
end. There is no threshold to tune toward — the arms improve monotonically as the
multiplier gets *closer to reproducing the flat 75*.

## 4. Why — the regime cut

Net $ / trades / PF, split by the vol clock:

| arm | quiet | mid | hot |
|---|---|---|---|
| twin (fixed 75t) | **$115,909** / 119 / **3.29** | $3,988 / 84 / 1.07 | **$34,121** / 186 / **1.24** |
| atr 0.04 | $45,041 / 118 / 1.85 | $5,241 / 84 / 1.11 | $15,507 / 181 / 1.11 |
| atr 0.05 | $90,083 / 118 / 2.70 | $13,384 / 86 / 1.24 | **−$8,435** / 175 / 0.95 |
| atr 0.065 | $118,022 / 118 / 3.05 | −$7,290 / 83 / 0.89 | $555 / 173 / 1.00 |

Two findings, and the second is the one that kills the idea:

**Quiet days carry the book, and tightening there destroys them.** The quiet
tercile is 76% of the baseline's net at PF 3.29. Pulling the quiet trail from 75
down to a median 50 (mult 0.04) costs **−$71k** — essentially that arm's entire
loss. The 0.065 arm only looks respectable because its quiet median (82) lands
back near the flat 75. This is the triple-barrier finding reproduced by direct
engine A/B: the wide trail is load-bearing.

**Widening on hot days — the whole premise — makes hot days worse.** The
baseline's flat 75 earns $34,121 on the hot tercile. Stretch the hot median to 94
and it goes to **−$8,435**; stretch it to 122 and it is $555. The sign is
reversed: the idea's core claim is not merely absent, it is backwards. And 0.04,
whose hot median is exactly 75, still only makes $15,507 — so it is not the level
but the **dispersion** that costs: scattering the distance 43–142 around a good
number is worse than sitting on the good number.

The trailed exits tell the same story from the other side — median R on a trailed
exit falls 0.53 → 0.38 → 0.04 across the arms, i.e. the ATR trail is
systematically handing back more of each winner.

## 5. What ships

`trail_atr_mult` is built, tested and documented, defaulting to **0** on all three
strategies that ride the shared `run_session` (upper-band v15, globex-bounce v15,
lower-band v12). With it at 0 the trail is byte-identical to what it always was —
the twin arm is the proof.

Leave it off. It joins the standing scoreboard of engine A/Bs (this is another
fail against the single `reenter_after_stop_only` pass), and it is the fourth
independent confirmation that **this book's geometry is absolute, not
volatility-scaled** — measured now not by correlation or post-hoc cut but by the
one instrument that can't be fooled by a leak: the engine itself.

## 6. Reusable

- `journal.sim.vol_regime.session_atr(contract, day)` — the vol clock's daily
  ATR(14), addressable one session at a time, causal by construction (built over
  sessions *strictly before* the requested day, so it never needs a shift and is
  safe to call for a day still trading). Verified equal to the artifact's
  `daily_atr14`, including across contract rolls.
- `data/research/atr-trail/run_ab.py` — one arm per foreground invocation.
- `data/research/atr-trail/analyze.py` — the regime cut above.

**Gotcha worth keeping:** the warm-up window reaches back across contract rolls,
and resolving it to the requested day's front month silently drops every session
that traded under the previous contract — which is most of the window on the day
after a roll. The bug is invisible in the output (you get *an* ATR, just the
wrong one); it was caught only by asserting the new read equals the existing
artifact on a post-roll day.
