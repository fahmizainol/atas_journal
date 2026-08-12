# Every replay sitting, re-run under different exits — trails, no trail, breakeven-only

**Date:** 2026-08-10
**Data:** every stored replay sitting with trades — 33 attempts across market
days 2024-03-08 → 2026-05-06, all recorded 2026-08-02 → 2026-08-10. 29 of 33
validate (the port reproduces the stored trade count and net **to the exact
dollar**); the 4 that don't are excluded loudly, see §5.
**Script:** `data/research/replay-trail/exit_whatif.py`, driving the engine
port in `whatif.py` (see [the trail study](replay-trail-whatif.md) for the
port itself).
**Question:** what if the same entries had been managed with different exits —
wider/tighter trails, stepped trails, breakeven-only, or no trail at all?
**Verdict:** on this record, **tighter is better and no-trail is worst**. A
25t trail on everything adds **+$6.3k** over what was actually played and is
better on 18 sittings vs worse on 4 (sign test p ≈ 0.004). Breakeven-at-+25t
alone recovers +$4.2k. Every trail ≥ 75t, and dropping the trail entirely,
loses $2.4–4.9k more. But no exit setting turns the record positive — the
best grid cell still nets −$13.9k ex-outlier. **Exits recover at most ~30% of
the losses; the rest is entries.**

---

## 1. The record being counterfactualed

29 validated sittings, 339 closed trades, net **−$40.9k as played** — of
which −$21.3k is a single 2024-03-08 sitting traded at 4/8/48-lot size (an
experiment day; every cut below is also shown without it). Typical setting
as played: market entries, stop 50t, trail 50t (a stretch of sittings ran
25t), step 0, BE +3–4t.

The exit mix as played says most of what follows: of 339 trades, **126 ended
on the full stop and only 24 ever reached a target**. 83 were trail exits,
77 manual bails.

## 2. The grid

Per sitting: re-run the stored order log (same entries, same manual closes,
same bracket drags) with every entry order's trail overridden. `tNN` = NN-tick
trail, `sNN` = ladder step; `beNN` = stop jumps to entry+3t once the trade is
NN ticks green and never moves again; `no-trail` = bracket stop/target only.
Positions still open at the sitting's end clock are marked to market, exactly
as the browser books them.

Aggregate over the 29 validated sittings (Δ vs as-played; "better/worse" =
sittings improved/hurt; Δ/ct = dollars per contract traded):

| scenario | total | Δ total | better | worse | Δ/ct |
|---|---|---|---|---|---|
| as-played | −$40,900 | — | — | — | — |
| **t25** | **−$34,642** | **+$6,258** | **18** | **4** | **+$9.3** |
| **be25** | −$36,663 | +$4,237 | 10 | 6 | +$6.3 |
| t50s25 | −$40,866 | +$34 | 6 | 5 | +$0.1 |
| t35 | −$41,069 | −$169 | 9 | 8 | −$0.3 |
| be50 | −$41,720 | −$820 | 6 | 5 | −$1.2 |
| t50s10 | −$41,966 | −$1,066 | 10 | 9 | −$1.6 |
| t50 | −$41,974 | −$1,074 | 2 | 3 | −$1.6 |
| t100 | −$43,273 | −$2,373 | 8 | 6 | −$3.5 |
| t100s25 | −$43,473 | −$2,573 | 9 | 6 | −$3.8 |
| t75 | −$44,283 | −$3,383 | 8 | 7 | −$5.0 |
| no-trail | −$45,428 | −$4,528 | 7 | 7 | −$6.7 |
| t75s25 | −$45,820 | −$4,920 | 7 | 7 | −$7.3 |

Excluding the 48-lot day changes no ordering (t25 +$5.8k on −$19.6k base).

## 3. What's actually going on

**The tight trail is a stop-out converter.** t25 turns the exit mix from
126 stops / 83 trails into **79 stops / 184 trails**: half the full −50t
losses become small scratches near breakeven. Win rate moves 42% → 49%.
Targets drop 24 → 11, but targets were barely participating anyway.

**The trail earns its keep — it's the width that doesn't.** no-trail is the
single worst cell: the instinct to trail is right on this record, and every
widening past ~35t just hands back more. Steps are noise — t50s10 ≈ t50,
and the s25 variants shuffle within a few hundred dollars of their step-0
twins.

**Breakeven-only is most of the tight trail.** be25 (arm at +25t, stop to
entry+3t, never trail further) captures two-thirds of t25's improvement.
The active ingredient is *refusing to let a once-green trade go red*, not
the ratchet.

**Wide-trail days exist but are outnumbered.** 2025-12-04 (t50 +$2.1k vs
+$0.6k), 2025-02-25 (t100s25 +$1.6k vs +$0.4k) and 2024-06-17 (t25 flips
+$930 → −$260) are real: on days with follow-through the tight trail costs
the runners. But there were ~4 of those against ~18 grind sittings, and the
cost of being tight on a paying day (−$0.1–1.2k) is smaller than the cost of
being wide on a grind day. Splitting by the day-read strip's follow-through
metric (3-min MFE median ≥ 100t) puts t25/t35/be25 on top *within both
groups* — and notably **2025-12-04 reads as a grind day (62t) yet wanted the
wide trail**, so the MFE-median day-read does *not* reliably pick the wide
days. The "read the day, then pick the width" idea from the
[3-sitting study](replay-trail-whatif.md) survives as a hypothesis, not a
rule.

**The ceiling is low.** Best cell of the whole grid: −$13.9k ex-outlier,
vs −$19.6k as played. Exit management recovers ~30% of the bleed; no setting
gets near zero. This is the same shape as the
[upper-band-bounce loss study](upper-band-bounce-loss-study.md) (losses are
regime, not geometry) and the behaviour audit (sub-30s entries are the leak):
**the money is in which trades exist, not how they end.**

## 4. If one setting had to be picked

Trail 25t, step 0, BE +3t — what the December/February sittings already ran —
or be25 if handing back open profit on every pullback grates. Both are
defensible; nothing wider than 35t is, on this evidence. The honest caveat:
counterfactuals reuse the recorded entries and manual closes verbatim, and a
different exit regime would have changed what you did next (re-entries after
a scratch, the size of the next trade). The dollar numbers are exact; the
psychology is not simulated.

## 5. Addendum: 1R, no trail — the base-hit bracket

Follow-up question: what about a plain 1R bracket — keep the placed stop,
cap the target at 1× the stop distance, no trail? Run four ways
(`data/research/replay-trail/r_whatif.py`): with the recorded stop-drags
kept (`r1`) and set-and-forget with drags dropped (`r1sf`), plus 1.5R/2R
and a 1R+BE variant. Same 29 sittings:

| scenario | total | Δ vs as-played | better | worse | win% | target hits |
|---|---|---|---|---|---|---|
| **r1be25sf** (1R + BE at +25t) | **−$30,063** | **+$10,837** | 16 | 6 | 56% | 79 |
| **r1sf** (1R set-and-forget) | −$31,744 | +$9,156 | 17 | 5 | 43% | 109 |
| r1 (drags kept) | −$33,579 | +$7,321 | 11 | 8 | 42% | 87 |
| r15sf (1.5R) | −$44,918 | −$4,018 | 15 | 7 | 37% | 58 |
| r2sf (2R) | −$49,827 | −$8,927 | 10 | 12 | 32% | 35 |

Three things worth staring at:

- **1R beats every trail in the study** (+$9.2k vs t25's +$6.3k), and 1R
  plus a breakeven jump at +25t is the best cell found anywhere
  (+$10.8k, though its 56% win rate counts BE scratches as tiny wins). As
  played, only 24 of 339 trades ever reached their 2–4R targets; at 1R the
  target lands 109 times. Your entries' edge, when there is one, is spent
  within the first ~50 ticks — wait for 1.5R and it's already gone
  (−$4.0k), 2R is the worst cell in the whole study (−$8.9k).
- **The drags cost money here too**: keeping your recorded in-trade stop
  moves (r1 vs r1sf) gives back ~$1.8k — with one big exception,
  2024-10-22, where dragging saved $3.9k. Set-and-forget is better in
  aggregate but has the fatter left tail (r1sf's worst sitting −$7.7k vs
  as-played −$4.5k there).
- **The runner days pay for the cap**: 2025-03-13 drops +$1,281 → +$298
  and 2025-02-25 gives up its $1.6k best-case — the same ~4
  follow-through days that wanted the wide trail also want more than 1R.
  Everywhere else the cap is nearly free: the grind-day disasters collapse
  (2025-10-16: −$3,138 → −$334; 2025-09-24: −$3,060 → +$68).

Same ceiling caveat as §3: even the best R-cell is −$30k on the full set,
−$9.5k ex-outlier. The base-hit bracket stops the bleeding faster than any
trail; it still doesn't make the record positive.

## 6. Exclusions and traps (for the next run)

Excluded, port would not validate: `2025-03-12` ×2 (the **two best sittings
ever recorded**, +$4,957 and +$1,590 — fill prices reproduce to the tick but
exit sequencing diverges; their trades.json was written by an engine build
that predates ~120 uncommitted lines of `replaySim.ts`), `2025-10-13`,
`2026-03-19`. The validated set therefore under-represents winner days —
another reason not to over-trust the tight-trail total.

New traps learned (both now encoded in `exit_whatif.py`):

- **open-at-clock**: the browser marks a still-open position to market into
  `trades.json`; the port must flatten the same way before comparing, or any
  sitting that ended in a position never validates.
- **stale fill prefs**: `attempt.json` prefs are creation-time, but stored
  trades re-derive under the *current* `sim.fills` (the 7→3.5 commission
  migration). Validate against candidate configs — prefs, 3.5/1/1, 0/0/0
  (engine v1), 7/1/1 — and keep whichever reproduces the stored net.
