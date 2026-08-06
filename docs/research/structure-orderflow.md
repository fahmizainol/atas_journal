# Structure × S/R × order flow (resolved null)

**Question.** The last untested cross between the two feature families: does
order flow **at** a price-action event — a BOS/CHoCH structure break, or a
touch of a basic S/R level — predict *that event's* outcome (follow-through vs
fail, accept vs reject)? Big-lot participation is the one tape signal that has
ever survived a robustness pass ([bigtrade study](bigtrade-orderflow-30badf94.html)),
and both parents are individually null — structure breaks carry no forward
edge at any swing scale ([market-structure-events](market-structure-events.md)),
touch-fading has no edge at any horizon (interactions v9). The practitioner
story (absorption at a level → the level holds; big lots with the break → it
runs) lives exactly in this cross, so it got a direct test. **It is a
characterization, not a build** — no knob, no gate, no strategy.

## Method

- **Anchors** (363 RTH sessions, 125,282 rows, every anchor located by tick
  timestamp / `minute_bars` `end_idx` — never a raw index base):
  - *break* — BOS/CHoCH close-break bars from the `structure_events.py` state
    machine re-run bit-for-bit at swing thresholds 5 / 10 / 20 pt (57.4k).
  - *touch* — S/R touches on 1-min bars: static session refs (Open, ONH, ONL,
    pdHigh, pdLow, pdClose) + retests of confirmed-but-unbroken 10-pt swing
    pivots, with the interactions-bench constants (2-pt touch tolerance,
    re-arm only after 3 pts away, 3-pt reject / 2-pt accept margin) (57.1k;
    5.8k static, 51.3k pivot).
  - *null* — 30 time-matched random bars per session, direction = sign of the
    trailing 3-bar move. The "flow predicts short drift anywhere" control
    every event class has to beat.
- **Tape features**, trailing 60s / 300s tick windows ending at the anchor
  bar's last print (canonical A=buy / B=sell sign): volume rate, CVD per-vol
  imbalance, ≥10-lot participation, big-lot CVD, max print, session CVD;
  direction-aware **absorption** (approach-side lots per tick of net progress)
  and **exhaustion** (20s vs 40s approach-side rate) from the loser-orderflow
  definitions. `*_al` variants signed by event direction.
- **Outcome**: house race — net/MFE/MAE over bars t+1..t+20 in event
  direction, anchor bar excluded; touches also get the accept/reject/chop
  call. Stats: Mann-Whitney AUC, 1,000 within-session permutations,
  odd/even session split-half.

Extractor / analysis: `data/research/structure-orderflow/extract_sof.py`,
`analyze_sof.py`; rows in `sof_events.parquet`, table in `aucs_sof.csv`.

## Result 1: flow at a structure break says nothing about follow-through

Every feature, every threshold, BOS and CHoCH alike: AUC vs `fwd_net>0` sits
in **0.492–0.510**, no permutation p survives even a glance at multiple
comparisons, and the event-minus-null AUC delta never exceeds **+0.012**. A
break "confirmed" by aligned CVD, big-lot prints, or a volume surge travels no
farther than an unconfirmed one — and no farther than flow "predicts" at a
random momentum-signed bar. The quartile read is non-monotonic noise (thr-5
breaks in the *top* aligned-CVD quartile actually net −0.33 pts).

## Result 2: the apparent absorption edge at S/R is price action in a costume

The first pass looked like a finding — the practitioner story, confirmed with
p = .000 and near-identical split halves:

| accept-vs-reject target | absorp AUC | reject q1→q4 | w60 aligned-CVD AUC |
|---|---|---|---|
| static refs | 0.439 | 57.6% → 69.9% | 0.457 |
| pivot retests | 0.452 | 63.3% → 73.5% | 0.469 |

Then the artifact screen (the [RSI@fill = stretch9 lesson](ny-band-rsi.md)):

- The strongest "predictor" of accept-vs-reject is **`close_al`** — where the
  touch bar itself closed relative to the level — at AUC **0.601 / 0.566** on
  its own. Pure price, zero tape.
- Absorption is built from approach-side volume ÷ progress, and the progress
  term is that same price action: ρ(absorp, prog_ticks) = **−0.64 / −0.71**,
  ρ(absorp, close_al) ≈ −0.5.
- **Stratified within touches whose anchor bar closed within 1 pt of the
  level** — where intrabar price can't be doing the work — every flow feature
  collapses: absorp 0.497–0.499, its flow-only numerator (`appr_vol60`)
  0.494–0.495, aligned CVD 0.508–0.516 with p ≥ .32, big-lot CVD ~0.50. Both
  families, both halves, n = 6.7k.

"Absorption at the level" was the touch bar closing back away from the level,
renamed. The tape added nothing beyond what the bar's own close already said —
and the bar's close "predicting" reject is just the reject beginning inside
the anchor bar.

## Residue (honest, small)

`w300_cvdpv_al` at pivot retests vs follow-through: AUC 0.512, perm p < .001,
halves 0.520 / 0.505 — 5 minutes of aligned imbalance carries a whisper of
continuation. It is the size of rounding error, decays toward the null-anchor
read (0.500), and is exactly the "flow momentum everywhere" background the
null class exists to expose. Not a lead.

## Verdict

**Resolved null, both directions of the cross.** Order flow does not grade
price-action events (breaks or touches), and price-action context does not
make order flow informative (event-minus-null ≈ 0 on every feature). This
closes the loop started by the bigtrade study: big-lot participation predicts
*engine-trade* outcomes in one strategy's habitat, but not generic structural
events. Consistent with absorption/exhaustion being dead at every anchor the
loser study tried, and with stops dying of drift, not capitulation.

**Reusable:**
- The anchor-window tape extractor (`_Flow` in `extract_sof.py`) — prefix-sum
  CVD/big-lot/absorption features at arbitrary timestamped anchors.
- **Pre-screen rule, generalized from the oscillator one:** any feature
  computed on the anchor bar that correlates |ρ| ≳ 0.5 with the bar's own
  close-vs-level (or close-vs-prior-close) is price action renamed — run the
  `close_al` control and the pinned-close stratification *before* believing
  an anchor-bar tape signal.
- The momentum-signed null-anchor class as a standing control for any future
  "flow at event X" claim.
