# Candidate #3 deep-dive — LVN-retrace continuation + big-lot aggression

- **Date:** 2026-07-20
- **What this is:** a deep-dive on candidate **#3** from `playbook-scouting-tradezella.md` — the "most novel to our stack" 🟡 needs-engine idea. Full auction logic, worked NQ examples, an explicit answer to *which volume profile the LVN is measured on*, the engine work implied — **and the causal test that resolved it.**
- **> VERDICT (2026-07-20): NULL. Do not build.** A causal, leg-anchored LVN-retrace detector has no forward edge over a random same-width pullback in the same leg (real −0.09R vs null, split-half-stable, not significant), and the big-lot aggression trigger makes it *worse*. See [§ Outcome](#outcome--resolved-null-2026-07-20). Both low-prior ingredients (VP-geometry, order-flow-as-alpha) confirmed dead by direct test.
- **Sources:** Fabio (Auction Market Theory + LVN), Carmine (LVN), Yush (order flow) — the three explicitly NQ/ES-native TradeZella playbooks. Links at the bottom.

---

## Core idea in one sentence

After price breaks *out of a balance* in an impulsive leg, it leaves a thin "no-value" gap behind it (the **LVN**). On the pullback **into** that gap, if big lots step in *with the trend*, you go with the move and target the next shelf of old value (the **prior-balance POC**). It is a **day-with continuation** trade that uses a **new location** (the thin spot) and **our one surviving order-flow signal** (75+ lot prints) as the trigger.

## The auction logic it rests on

Two states, and the setup lives on the boundary between them:

- **Balance** — a fat, bell-shaped chunk of the profile. Fair value. Price rotates inside it and, per Fabio, **reverses ~70% of the time** once it re-enters one. This is the *magnet / exit*.
- **Imbalance / LVN** — a thin sliver where price moved fast and traded almost nothing. *Not* accepted value. On a retest it tends to **reject and launch**, because there is nothing there to settle into. This is the *launchpad / entry*.

The trade is a rubber-band between the two: **enter at the LVN (rejection), exit at the next balance POC (absorption).**

## What each source contributes

| Source | Its job in the setup | Key numbers / rules |
|---|---|---|
| **Fabio — Auction/LVN "Trend Model"** | The skeleton: Market State (out of balance) → Location (LVN on the *impulse-leg* profile + prior-balance POC) → Aggression (order flow) | *"No aggression = no trade"*; exit full at prior-balance POC; **NY session only**, *"avoid the London open — too many fake breakouts"*; risk 0.25–0.5%/trade |
| **Carmine — LVN** | LVN is a **zone, not a line**; first **2–3 hours** of session; few high-quality setups, selective | Trades continuation *and* rejection — we take **only continuation**; order-flow confirmation mandatory (passive bid holding / aggressive sell failing / absorption) |
| **Yush — Order flow** | The entry-quality gate: an AOI needs **≥2 of 4 confirmations** (market level · volume-profile/LVN · big trades · delta); A+ = 3–4; 2–3 trades/day | **Big trade = 75+ lots NQ**, 200+ ES; the "trap / confirmation entry" (break → chase → return inside → reject → go the other way) |

The LVN is *simultaneously* confirmation #1 (a market-generated level) and #2 (volume-profile structure), so it is already 2 of Yush's 4 by itself. Add a 75+ lot print (#3) and positive delta (#4) and you have the A+ version.

## The setup, step by step

1. **Displacement.** Price breaks structure out of a balance — an impulse leg with momentum away from prior value.
2. **Mark the LVN.** Profile *just that impulse leg* (not the whole session) and find the thin bin(s) — the price band the move skipped through.
3. **Mark the target.** The next prior-balance POC in the trend direction (an old value shelf overhead for a long).
4. **Wait for the retrace into the LVN.**
5. **Require aggression, in the trend direction.** A big buy print (≥75 lots NQ) / footprint imbalance / delta flipping positive *as price sits in the LVN*. **Missing → stay flat.**
6. **Enter** with-trend on that confirmation. **Stop** 1–2 ticks beyond the aggressive print / swing low. **Target** the prior-balance POC, full exit (because 70% reverse from balance).

## Picture (NQ, sideways volume profile)

```
 price   │ volume traded here →
 19,055  │███████████                ┐  overhead OLD-VALUE shelf
 19,048  │██████████████  ◄ POC      ├  = prior-balance POC  →  TARGET (exit full)
 19,040  │█████████                  ┘
 19,010  │██
 18,992  │▌                          ┐
 18,984  │▌   ◄ LVN "waist"          ├  impulse leg — thin, fast, skipped
 18,976  │▌   (thin, no value)       ┘     ← ENTRY on retrace + big-lot
 18,958  │██████
 18,942  │██████████████  ◄ POC      ┐  morning balance
 18,928  │████████████               ├  (origin value)
 18,912  │████████                   ┘
```

Price path: builds the morning balance → **impulse up** through the thin 18,976–18,992 waist → pushes toward the overhead shelf → **retraces down into the LVN (~18,984)** → big buyers defend → **continue up to 19,048** and exit.

## Three worked examples

**A — the textbook long (it fires).** Morning balance 18,912–18,958, POC 18,942. At 9:35 a leg drives 18,958 → 19,010 in ~15 min, leaving 18,976–18,992 thin (the LVN). At 10:10 price pulls back to **18,984**. The footprint prints a **92-lot buy** absorbing the dip and delta flips green → **long 18,986**, stop 18,974 (12 pts), target the overhead old-value shelf POC **19,048** → +62 pts. Three of Yush's four confirmations present (LVN + big lot + delta).

**B — the veto (it *doesn't* fire — the point of the aggression rule).** Identical structure. Price retraces into 18,984 but the tape is dead — biggest print 22 lots, delta flat. **No trade.** Price then knifes straight through the LVN back into the morning balance. The LVN was not defended; without the big-lot filter you'd have caught a falling knife. This is exactly the case the trigger exists to skip — and why this can't be a plain limit-at-a-level like profile-pullback.

**C — the mirror we deliberately skip.** Same geometry but price breaks up, fails, and reclaims back *down* inside the balance, leaving an LVN in the *reclaim* leg → a **short back to POC**. Carmine trades this; Fabio calls it the Mean-Reversion Model. **We don't build it** — counter-trend + absorption, both in our graveyard (playbook G4). NQ edges are day-with only.

---

## Which volume profile is the LVN on? (the crux)

**It is neither our NY-session profile nor our globex profile in the canonical version.** The setup actually uses **two different profiles at two different anchors:**

**1. The LVN comes from an *arbitrary, structure-anchored* sub-window — the impulse leg itself.** Fabio is explicit: *"Take the impulse leg that broke the structure. Apply a Volume Profile to **that leg**. Identify Low-Volume Nodes inside that move."* That is not anchored at any clock time — it is anchored at two *structural* points: where the breakout began and where the leg topped. You only know to draw it once a leg has displaced out of balance. Carmine and Yush are looser (thin spots on "the profile" generally), but the sharp, automatable version is Fabio's: **profile the leg, not the session.**

**2. The target — the prior-balance POC — comes from an *earlier balance's* profile:** yesterday's value, the overnight balance, or an earlier intraday balance. A different window from the LVN.

### How that maps to what we already have

Checked `src/journal/sim/profile.py`: `DevelopingProfile` computes **only POC/VAH/VAL** — it builds the full per-level histogram (`hist`) internally and then **throws it away**. And it is **session/globex-anchored and cumulative** — it has no notion of "profile the ticks between structural pivot X and pivot Y."

| Piece | Source's anchor | Our closest object | Gap |
|---|---|---|---|
| **LVN** | the impulse leg (arbitrary structural window) | NY developing profile shows the thin bin **if** the leg starts near the open… | …but later rotation *fills the thin spot back in* — the cumulative profile blurs the LVN the leg-only profile would freeze; and we discard the histogram, so there is no LVN scan at all |
| **Prior-balance POC** (target) | an earlier balance | **globex POC** is a genuinely good proxy — the overnight *is* the balance the RTH-open impulse breaks out of | only clean when the impulse is the RTH-open breakout; otherwise needs a prior-day / earlier-intraday POC |

So: our **globex profile POC is a legitimate stand-in for the "prior-balance POC" target** in the common case. But **neither** of our profiles is the "profile just the leg" object the LVN needs — that is the new machinery.

### The lookahead trap in "the impulse leg"

Automating *"the leg that broke structure"* is exactly where a **lookahead leak** sneaks in — and we were just burned by this class of bug (drift-fade market-structure: the `03f4c56c` first pass was an index-base lookahead because globex engines were fed ON+RTH indices). Same failure mode here: if you define the leg's *end* as the eventual swing high, you are using future information to place the LVN. The leg's endpoint must be a **completed structural pivot known at decision time**, and the LVN only becomes tradeable *after* that pivot confirms — never at the true top.

---

## Why this is genuinely new to us (and the catch)

- **New location.** Every "VP-geometry has no edge" null we've logged (stable-level, vah-snap, pd-VAH) was measured on **POC/VAH/VAL as support/resistance**. The LVN as a *with-trend launchpad* has never been tested here.
- **New use of our one live signal.** Big-lot participation is the *only* order-flow feature that survived (AUC 0.66, split-half robust). Here it is an **entry filter at a location**, not a **size knob** — and the size-knob version is what failed its A/B, so this is a different question.

**The catch:** the setup stacks *two* ingredients that each carry a low prior in our data — VP-geometry (dead as S/R, playbook G3) and order-flow-as-alpha (dead as size/entry so far, G2). It is novel precisely because it combines them somewhere untested, but treat it as a real coin-flip, not a lead with momentum behind it.

## Engine work implied (heaviest 🟡 in the list)

1. **Impulse-leg detection + sub-window profiling** — a causal structure-break detector plus a "profile these ticks between pivots" path (`developing_profile` only accumulates forward from a session/globex anchor).
2. **LVN detection** — retain and scan the histogram for local minima / thin bins → a brand-new reference type (we've only ever exposed POC/VAH/VAL).
3. **Big-lot as an entry trigger** — the signal exists as a research feature but has never been wired as a gate/trigger in the engine.
4. **Prior-balance-POC target** — separate minor candidate #6; a `prior_poc` target mode (globex POC in the common case).

Base could clone `ProfilePullbackConfig` (same "level-in-force + pullback, no acceptance candle" shape), but the level *source* and the *trigger* are both new.

## Recommended path — cheap research-first proxy before the full build

Our own weekly-VWAP lesson: **re-cut the thesis on the baseline with cheap tools before building a knob.** Before spending the engine work on impulse-leg profiling + LVN detection:

- On an existing profile-pullback run, split pullback entries by whether they landed in a **thin** bin vs **on** POC/VAH (approximate the LVN as *thin bins in the developing profile at decision time* — contaminated, but causal and zero new machinery), and test whether the thin-bin pullbacks with a coincident 75+ lot print behave measurably differently.
- If that separation is flat → the LVN adds nothing, and we've learned it without building the clean leg-anchored version.
- If it separates → build the leg-anchored detector with a real prior.

---

## Outcome — RESOLVED NULL (2026-07-20)

Built the causal detector (the recommended path was skipped in favour of going
straight to the honest causal version) and tested it end to end. **The LVN-retrace
continuation is null on NQ.**

**Method** (`data/research/lvn-retrace/` — `lvn_causal.py` for the visual,
`lvn_outcomes.py` for the stats):
- Causal swing legs: zigzag on tick-bars, CONFIRM 22pt reversal, MIN_LEG 55pt, keep
  only **non-overlapping dominant** legs (the first cut found a 10-leg staircase).
- LVN = the thin band(s) of the leg's **own** profile (≤35% of leg-POC volume),
  **frozen when the swing high confirms** — so it exists before any retrace. Required
  to sit ≥ CONFIRM below the leg high (a real pullback target, not the final thrust).
- Entry = LVN top on the retrace; stop = LVN bottom (floored 6pt); 2R bracket,
  stop-first-within-a-bar (conservative, applied to real and null alike).
- **Null = the same legs, same-width bands at other heights** (fractions 0.15–0.75 of
  the leg), excluding the real LVN. Isolates *thinness* from *"buy any pullback."*
- 281 traded days (2025-02 → 2026), **832 real retraces vs 1,239 matched nulls.**

**Results:**

| cut | REAL R_mean | NULL R_mean | read |
|---|---|---|---|
| all | +0.108 | +0.199 | null wins |
| depth-matched, pos 0.00–0.40 (**71% of real fire here**) | +0.06 / +0.08 | +0.31 / +0.23 | real **−0.15 to −0.25** worse |
| split-half by date | +0.121 / +0.096 | +0.203 / +0.195 | null wins **both** halves |
| + big-lot participation ≥ median | **−0.041** | +0.072 | trigger *degrades* real |
| morning + big-lot (best-case cell) | **−0.111** | +0.069 | negative where hope was highest |

Significance (bootstrap, 5000): REAL−NULL = **−0.090R**, 95% CI [−0.218, +0.040],
P(real>null) = **0.085** — not distinguishable from the null, and if anything worse.

`corr(participation, R) ≈ −0.06` (both real and null): big-lot participation carries
no forward information here. The only positive signal (~+0.1R baseline, real *and*
null) is generic **"a pullback into a confirmed up-leg drifts up a bit"** — a momentum
effect the random null captures as well or better. The LVN thinness and the big-lot
trigger each add nothing; stacked, they subtract. **Confirms the standing priors:
VP-geometry has no edge; order-flow-as-alpha is dead on our tape.**

**Bug found in passing** (recorded so it doesn't bite again): the canonical tick
aggressor encoding is **`A` = ask-lift = BUY, `B` = bid-hit = SELL** (`interactions.py`
`_minute_delta`, "ask-lift minus bid-hit"). The one-off `extract_loser.py` research
script — and the loser/bigtrade memory notes — had it flipped (`B`=buy). The big-lot
**participation** signal is side-agnostic, so that study's order-flow nulls stand; but
any *directional* buy-vs-sell feature built on the flipped convention is mislabeled.

---

## Sources

- Auction Market Theory + LVN (Fabio) — https://www.tradezella.com/strategies/auction-market-strategy
- Carmine LVN Strategy — https://www.tradezella.com/strategies/low-volume-node
- Yush Order Flow Strategy — https://www.tradezella.com/strategies/order-flow-strategy
- Parent scouting pass — `playbook-scouting-tradezella.md` (candidate #3)
