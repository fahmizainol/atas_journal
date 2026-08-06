# Scott Pulcini podcast — insights & timestamped index

**Video:** [$15M Scalper: I Was A Victim The Market's Algorithm, So I Learned To Beat It!](https://www.youtube.com/watch?v=phLxL7Q_Usc) — Titans Of Tomorrow, 1h31m, published 2026-08-03.

**Who:** Scott Pulcini — E-mini S&P hyper-scalper 2001–2005, claims ~10% of daily world ES volume (≈50k of 500k contracts/day), $15M over 2002–2004 ($10M in 2003 alone, verified on-camera with broker statements + handwritten calendar journal). The trader Dr. Brett Steenbarger sat behind for a year while writing *Enhancing Trader Performance*. Wiped out by the rise of HFT algos + low volatility 2005–2007, spent years in medical sales, returned ~2018 trading CME MBO iceberg/stop-run data via Bookmap.

---

## The arc in one paragraph

Pulcini's original edge was pure speed + reading real resting size in a thick pre-algo order book, clicking in and out up to 3,000 lots at a time. Algos out-clicked him and the book turned fake, so the edge died in ~18 months. His reinvented edge is *location, not speed*: CME Market-By-Order data (via Bookmap) labels iceberg orders and stop runs with certainty, marking where big money is concentrated; he waits for those events at pre-drawn important areas, confirms with an ATR-scaled breakout from the event zone, and places stops/targets in ATR units on the far side of the volume event. Options-dealer gamma hedging (0DTE flow) is his second pillar. Everything else in the episode is risk discipline and "trade like an algo" psychology.

---

## Timestamped index

Click any timestamp to jump to that moment in the video.

### Act 1 — The glory days and the collapse

| Time | What's covered |
|---|---|
| [3:02](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=182s) | The calendar book: 2001–2005, ~10% of ES daily volume, day-by-day P&L log ($67k on 60k contracts, $144k days) |
| [5:05](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=305s) | 2003 numbers: $10M year, $2.9M commissions paid, $1M rebate |
| [7:36](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=456s) | Why the edge died: "that type of trading is obsolete" — algos + low vol; −$300k in 2005, ~−$1M in 2006 |
| [8:37](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=517s) | Old edge mechanics: thousand-lots were *real* size you could lean on; now size vanishes when price approaches; algos front-ran his clicks |
| [12:11](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=731s) | Firm-wide death: King Street Trading went 75 traders → 13 in two years |
| [13:42](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=822s) | Started with 1–2 lots, lost every day for two months, nearly fired; earned size increases month by month up to 3,000 |

### Act 2 — The comeback and the new edge

| Time | What's covered |
|---|---|
| [18:48](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1128s) | Steenbarger backstory (sat behind him for the book); 5 years in medical sales; 2018 call: "look at Bookmap" |
| [21:49](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1309s) | CME MBO data (introduced 2016–17): displayed book quantity beyond top levels "isn't even real"; only rhythmic + Bookmap feeds carry it |
| [22:51](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1371s) | Iceberg mechanics: big firms pay extra to display ~10%; front-running algos are *why* they hide; "a mouthful of icebergs" |
| [26:55](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1615s) | Stop runs = retail pukes, labeled with certainty by order-type in MBO data; a move made of stop runs is non-initiative → more likely to reverse |
| [30:29](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1829s) | Key nuance: an iceberg marks the *area* where big money is playing, not direction; big money is "right more than wrong" but not always |
| [25:24](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1524s) | Market composition claim: 90–95% of trade is algos (price-change/front-run types) + options-dealer hedging; big-money directional flow only ~5% |

### Act 3 — Execution rules (the mechanical core)

| Time | What's covered |
|---|---|
| [33:01](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1981s) | Learned the hard way: entering *on* the event whipsaws; needs confirmation |
| [34:01](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2041s) | Confirmation rule: price must push a **5-min ATR + 15%** away from the event zone before entry |
| [35:31](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2131s) | Stop goes on the *far side* of the volume event, another ATR+15% away — to be wrong, price must traverse the trapped-trader zone plus a full ATR |
| [36:31](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2191s) | Biggest retail mistake: fixed-point stops that ignore ATR ("risking 15 NQ points in a 180-ATR regime = stopped every time") |
| [37:31](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2251s) | Stop adjustments: nudge past nearby levels, especially GEX/gamma levels where dealers will hedge |
| [38:32](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2312s) | Current sizing: risks ~$500/trade, mostly micros; zone tool derives size from ATR distance |
| [42:36](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2556s) | Anti-fixed-R rant: "I want 3:1" and "trail to break-even" are in your head, not the market; he trails **only to new iceberg/stop events** |
| [43:07](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2587s) | Scalping around a position: price-change algo snaps fast moves back → peel off a couple lots at important areas, reload on the snap-back |
| [52:16](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3136s) | Trend-day compounding: each new event = trail stop + stack a new trade; risk is always to the most recent event |

### Act 4 — The named setups (19 strategies total)

| Time | Setup |
|---|---|
| [45:40](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2740s) | **Pick** — stop/iceberg event at the high/low of a multi-day market-profile composite (70% value area) |
| [46:10](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2770s) | **Izzy** — inflection zones drawn daily: balance-area tops/bottoms, HVNs/POC, origins of directional-conviction moves, hourly-chart tails/wicks |
| [46:41](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2801s) | **Failed balance breakout** — "the best position trade in trading": breakout fails, re-enters balance, longs trapped through the HVN → whopper move the other way (still waits for an iceberg event there) |
| [48:14](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2894s) | **Slug** — Lewig levels (Pamela Lewig, proprietary S/R, #3 on his edge ranking); trade = event *into* the level |
| [50:14](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3014s) | **Reversion trade** — market moves an ATR off the zone, comes back to retest it, fails again → scalp entry ("ATR, retest, fail") |
| [51:46](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3106s) | **Bark** — acronym: **B**lind (any location, no important area needed), **A**TR move, **R**etest, **C**onfirm; filtered by an EMA ("algo guys") in trade direction |

His edge ranking: #1 stop/iceberg events, #2 options flow + gamma levels, #3 Lewig levels ([47:43](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2863s)).

### Act 5 — Options flow / gamma (the second pillar)

| Time | What's covered |
|---|---|
| [39:33](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2373s) | 0DTE = "the newest manipulation": run out of futures bullets → buy options → dealers must hedge in futures against you |
| [40:35](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2435s) | Tools: SpotGamma (real-time "HIRO" flow) + MenthorQ (gamma levels across futures products) |
| [53:48](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3228s) | Gamma levels are where icebergs cluster — long-term GEX levels vs intraday 0DTE "trace" levels that grow in real time |
| [1:14:35](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4475s) | Case study: HIRO dropped ~$7B out of nowhere on a "quiet" webinar day and the market got killed; −$15B print on interview day = lowest he'd seen |
| [1:16:38](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4598s) | Claim: ~90% of single-stock movement is options-flow-driven (leverage preference); dealers' delta hedging transmits it to futures |

### Act 6 — Risk, psychology, career

| Time | What's covered |
|---|---|
| [16:45](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=1005s) | "The minute I turned off my P&L is when I took off" — thinking in right/wrong, not money; a $60k-feel day was actually $180k |
| [15:44](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=944s) | Size scaling mindset: "it feels the same whether I'm wrong on a 1-lot or a thousand-lot" |
| [1:09:29](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4169s) | Negative reinforcement trap: repeatedly blew through his $100k daily limit, made it back, firm let it slide… |
| [1:10:00](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4200s) | …until Feb 6 and Mar 5, 2004: **two $700k losses in minutes**, a month apart (broke screens, physically removed from his office). Lesson: broker-enforced hard limits, ~6–7% of account max per day |
| [1:11:33](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4293s) | When to size up: structure thesis + confirming event = 2–3× size; 5–10 days make most traders' entire year |
| [1:03:24](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3804s) | Intuition is dead: it *was* his whole edge scalping; now his gut is "wrong 80% of the time" — algos killed feel |
| [1:05:55](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3955s) | "Trade like an algo": algos make money because they don't decide; if you feel the short won't work, "you should put your entire house on being long — no? then put the trade on" |
| [1:13:35](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4415s) | "Headline ping-pong": last 18 months = biggest stress test; break-even through it = doing very well; unquantifiable days → hit limit, walk |
| [1:20:12](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4812s) | Closing advice: (1) quantifiable edge, back-test or latch onto a proven group; (2) when you see the edge, put it on; (3) size for your account |
| [1:21:43](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4903s) | Prop-firm setup: 15 Apex accounts, 10 copied into live accounts (backed by a friend with ~$250k); [1:25:17](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=5117s) predicts sim-prop model ends soon (CME wants its cut, Topstep bought a brokerage, live environments coming) |

### Act 7 — Manipulation stories

| Time | What's covered |
|---|---|
| [55:20](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3320s) | "Lucky guess" icebergs: 3,000-lot buy iceberg appears a minute before a news number that comes in bullish — still happens |
| [56:20](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3380s) | His theory of *why* CME sells MBO transparency: by 2015–16 retail was wiped out and it was shark-eat-shark firm-vs-firm; exchange needed retail back |
| [1:26:48](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=5208s) | The Igor story: Russian trader wash-trading (crossing his own 1,000-lots to fake buying pressure) in the 10:15–12:30 lull; Pulcini pinged the orders, videoed it, reported to CME — nothing; the guy later shopped a business plan with future spoofing fines *baked in*, was finally fined/banned ~2016 |
| [1:28:21](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=5301s) | Spoofing vs order-crossing definitions; spoofing outlawed ~2008 (he did it himself when legal) |

---

## The setups explained

He claims 19 strategies but says outright they're all one skeleton: *"my strategies are all they are are my important areas that I look at, waiting for an iceberg event"* ([42:05](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2525s)). Each named setup only changes step 1.

**The shared skeleton:**

1. **Location** — a pre-drawn "important area" (this is what each setup defines differently).
2. **Event** — wait for the MBO data to print an **iceberg** (hidden institutional order absorbing hits) or a **stop run** (retail stops puking) at that area. The event proves a concentration of committed/trapped traders now exists there. He never trades the area without it.
3. **Confirmation** — price must push **one 5-min ATR + 15% away** from the event zone before entry, in the direction of the escape ([34:01](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2041s)). Snap-back algos constantly yank price back to volume events; only a full-ATR escape means real power. The event marks the battlefield, not the winner — direction comes from the escape.
4. **Stop** — far side of the event zone, another ATR+15% away ([35:31](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2131s)). To be wrong, price must re-traverse the trapped-trader zone *plus* a full ATR. He accepts the degraded R-multiple; tight fixed-point stops are his #1 retail mistake.
5. **Management** — trail only to **new** events in his favor (never break-even, "that's in your mind"); on trend days each new event = trail + a fresh add-on trade, always risking back to the most recent event ([52:16](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=3136s)). Separately, he scalps a few lots off around the position when the price-change algo snaps price at important areas, reloading on the snap-back ([43:07](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2587s)).

**The named setups** (deliberately silly mnemonics, à la *Profiles in Courage*):

- **Pick** — location = high/low of a **multi-day market-profile composite** (edge of the merged 70% value area). Event at the composite extreme → skeleton.
- **Izzy** — "inflection zone" trade. Zones hand-drawn daily from four price-action sources: tops/bottoms of **balance areas**; **HVNs/POC** (middle of balance, choppiest spot); **origins of directional-conviction moves** (where an FOMC-type impulse or gap launched — strong reactions on revisit); **hourly-chart tails/wicks** (instant rejection).
- **Failed balance breakout** — his "best position trade in trading." Breakout fails, price re-enters balance; breakout longs are trapped, capitulate as price crosses back through the HVN, fueling a large move out the other side. Still waits for an iceberg event before entering.
- **Slug** — "Lewig level" trade: proprietary S/R levels from Pamela Lewig (parameters undisclosed — his "you don't need to disassemble the light bulb" argument). Event *into* a red/blue Lewig level → skeleton. Ranks these #3 behind icebergs and options flow.
- **Reversion trade** — his main scalp: price moves an ATR off the event zone, **comes back**, retests and **fails** → enter in the original escape direction ("ATR, retest, fail"). Quick in-and-out.
- **Bark** — acronym **B**lind / **A**TR / **R**etest / **C**onfirm: the reversion sequence but "blind" — the event can fire *anywhere*, no pre-drawn area needed, because the event itself manufactures trapped traders wherever it lands. Extra filter: an EMA ("algo guys") must agree with trade direction.
- **Dada** — name-checked by the host ([45:10](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=2710s)) but **never explained** in the episode.

**Overlays on everything:** GEX/gamma levels (SpotGamma/MenthorQ) as dealer-hedging zones — he shifts stops past nearby GEX levels rather than sit in front of forced dealer flow; sizing = flat ~$500 risk/trade on micros, size derived from the ATR stop distance, 2–3× only when a structure thesis gets event-confirmed in the same direction.

### Reading the skeleton correctly — four things that are easy to get backwards

Worked out while building the demo; each one is a mistake a careful reader actually makes.

**1. The level and the event come from different data, in that order.** The level is drawn *before* the session from ordinary price action (see the setups below) — no MBO involved. The MBO is only for step 2: whether anything actually happened at that level. A level with no event is never traded. So it is not "find levels with MBO", it is "draw levels from the chart, then use MBO to see if they matter today."

**2. He does not enter at the zone — he enters a full ATR+15% beyond it,** and only after price has already travelled there. Entering *at* the level is the trade he explicitly refuses: snap-back algos constantly yank price back to volume events, so proximity to the event carries no information. Nor does he pick a side in advance — the escape direction names it. A level drawn expecting support can hand him a short.

**3. The stop is ~2.3× ATR, and that follows from the geometry, not from a choice.** Entry sits 1.15×ATR above the zone, the stop 1.15×ATR below it, so:

```
risk = zone width + 2 × 1.15 × ATR ≈ 2.3 × ATR
```

Measured across the demo's 32 confirmed entries: median 2.33× ATR, range 2.31–2.66 (the excess is zone width). `risk = 1.15 × ATR` would put the stop *inside* the zone — stopped out by price merely returning to the event, discarding the protection the confirmation was paid for.

**4. ATR sets the entry and the stop. It does not set a target.** The episode has no ATR-multiple TP. Management is event-based trailing (step 5): trail only to *new* events in his favour, never to break-even. The one-paragraph summary at the top of this doc says "stops/targets in ATR units", which overstates it — only the stop is pinned that way in the detailed rules.

**Confirmation, and what kind it is.** His step 3 is the familiar "wait for confirmation" instinct in an unusually disciplined form. Most confirmation rules are *shape*-based — close back inside, retest, engulfing bar, LTF structure shift — all read off the bar forming at the level, which is the touch-bar artifact factory this repo keeps tripping over ([[weekly-vwap-context-study]], [[structure-orderflow-study]]). His is *distance*-based and regime-scaled: "price is 1.15×ATR away" is a fact about location with no bar to interpret. And it generates a thesis rather than confirming one — the usual version can only agree with a side you already chose. The cost is paid twice, though: entry late by an ATR, stop far by an ATR, so a 1R win needs a ~2.3-ATR move. He accepts that trade explicitly.

**Terminology — three different things share the word "sweep":**

| | timescale | defined by | claims a direction? |
|---|---|---|---|
| microstructure sweep | ~250 ms | consecutive same-side fills = one order walking the book | no — a counting unit |
| his **stop run** | seconds | MBO order-type labels | no — "marks the battlefield, not the winner" |
| retail **liquidity sweep** | minutes | price takes out a prior swing high/low, then reverses | yes — the reversal *is* the claim |

Only the third asserts anything, and it is the only one inferred from a chart rather than from order data. Our own work bears on it: swing breaks are forward-null at every scale ([[structure-events-study]]: ~49.5% win, MFE ≈ MAE, 365 sessions), and the *rejection* half is where `close_al` turned out to be circular ([[structure-orderflow-study]]) — a bar that pokes through a level and closes back inside has the answer built into the setup. Neither study was framed as a liquidity-sweep test, so the pattern is not formally resolved here; the prior is just poor. A clean test would score from the bar *after* the sweep bar.

**Sizing is per trade, not a daily limit.** The ~$500 is risked on one trade, with contract count derived from the ATR stop distance. His daily-limit content is separate and cautionary: a firm-side **$100k** limit in the ES years that he repeatedly bullied past ([1:09:29](https://www.youtube.com/watch?v=phLxL7Q_Usc&t=4169s)), hence his insistence that loss limits be externally enforced. He never states a current daily figure.

**Replication caveat:** the skeleton is fully described, but the entry trigger is gated on Bookmap + the CME MBO bundle (event labeling) plus two black boxes (Lewig levels, his zone-drawing tool). From our tick-only data, iceberg sites can be proxied — leakily — as absorption levels (repeated aggressive volume at one price without traversal), a family our studies have repeatedly nulled ([[bigprint-digestion-study]], absorption dead ×3); stop runs can't be honestly proxied at all (MBO labels them by order type; from ticks any fast sweep looks identical). Real MBO exists via Databento's GLBX.MDP3 L3 schema if ever justified — but labeled data only fixes detection, not the edge question our A/Bs keep answering no on.

**What the zone can and can't be, from ticks** (measured 2026-08-04 while building the demo):

The zone is **market-generated** — a footprint of transactions, i.e. the price extent where size actually rested and traded. Every location he names is of that kind (HVN/POC, balance edges, move origins, wicks); not one is an indicator level. That rules out substituting a *derived* band such as the Globex VWAP dev1–dev2: it is a running σ computed *from* price, with no memory of any transaction. Measured on six sessions, the 1σ band is **~3.55× the 5-min ATR** (pooled ρ 0.85, mostly a between-session effect — within a session the two can move opposite as the band integrates and ATR mean-reverts). Substituting it gives `risk ≈ 5.85 × ATR`, ~2.5× the stop, with the added term collinear with the ATR term already present — the same answer as [[atr-vwap-band-study]] (intraday ATR is the band renamed, ρ .96; geometry absolute, not σ-scaled). The band belongs at step 1 as a *location*, never in the risk formula.

His two event types are opposites in the tape and need different detectors:

- **Stop run → sweep.** Large aggressive prints, one direction, price *traverses*. Recoverable: clustered ≥50-lot sweeps (`sweeps()` in `demo/big_trades_demo.py`). Zone = the burst's price extent, median 1 pt.
- **Iceberg → absorption.** The iceberg is the *passive* side, so it appears inverted — repeated aggression into a price that does **not** move, i.e. high **volume per point traversed**. Filtering 60-second windows at vol/pt ≥ p90 with directional efficiency ≤ 0.34 isolates ~20 windows/session; the zone is then the tightest contiguous band holding 70% of that window's volume (*not* the window's high–low, which is wide *because* the event happened in it).

That second detector works only in calm regimes. Quiet day (ATR 26): 70% bands of **2.2–5.8 pt**, POC holding 8–16% — genuinely zone-shaped. Violent day (ATR 82): **13–52 pt**, POC holding 3–6% — that is not a wall, it is just fast trade. **Trade prints cannot recover a tight resting-size location precisely when volatility is high**, which is the regime where the ATR term is largest and a wide zone hurts most.

Two caveats before anyone spends effort here. First, for *this* geometry the zone is 0.8% of the stop — a better detector moves the stop by a point or two and nothing else. Second, the detector's real job is step 2 (does an event exist at all?), which carries our worst prior. The one thing keeping it from being automatically dead: he is explicit that the event does not predict direction, so the zone need only be a *measurement anchor* — a much weaker claim than the absorption-predicts-outcome family we have already nulled three times.

**Using a 5-min candle as the zone is wrong**, and measurably so: it inflates the stop by a median **+39%** (up to +103%), because the bar an event lands in is wide *because* of the event. That double-counts the volatility already captured by the ATR term — the anchor-bar trap again.

---

## Core principles distilled

1. **Location over prediction.** Don't predict whether a level holds — wait for an iceberg/stop-run event *at* the level and react. The event tells you a concentration of trapped/committed traders exists; someone must be wrong when price leaves.
2. **Stop runs are pukes, icebergs are commitment.** A rally made of stop runs is not initiative buying → fade candidate. An iceberg marks the battlefield, not the winner.
3. **Everything is denominated in ATR.** Entry confirmation = ATR+15% away from the event. Stop = far side of the event, ATR+15%. Fixed-point risk in a variable-vol market is the cardinal retail sin.
4. **Trail to events, not to feelings.** Break-even stops and fixed R-targets are mental constructs; new volume events are market facts.
5. **Dealers' hedging is the tide.** 0DTE options flow forces mechanical futures hedging that he claims drives most intraday movement; gamma levels are where reactions (and icebergs) cluster.
6. **Risk architecture beats willpower.** Externally enforced daily loss limits (broker/firm-side), because on tilt "you become a different person" — his two $700k-in-minutes days came *through* a $100k limit he'd learned to bully past.
7. **Trade like an algo.** The edge is a trigger, not a debate. Feeling that a setup won't work is not information (if it were, you'd take the opposite side).

---

## Mapping to our research (context, not endorsement)

- **MBO/iceberg claims are largely untestable here** — we have tick data only, no MBO/L2 feed (see the ML-lit survey conclusion: LOB depth models dead without L2). His central data source can't be replicated in this repo.
- **We already null-checked a video making adjacent big-print claims** ([[bigprint-digestion-study]] on MatFinOg's MBO video): the digestion hump replicated in shape but wasn't fundable, and size-ceiling/wick claims died OOS. Prior for "big prints = tradeable edge" from our own data is low; big-lot participation was our one live entry-time signal and it still failed its size-up A/B twice.
- **His ATR framing independently agrees with our vol-clock study** ([[vol-clock-study]]: ATR-sets-the-clock confirmed on 5 baselines). His "fixed-point stops ignore the regime" critique is the same finding from the other direction. His ATR+15% confirmation is structurally similar to our drift-fade confirm variant.
- **"Market reacts to the event/level" claims deserve the touch-bar screen** ([[weekly-vwap-context-study]], [[structure-orderflow-study]]): every apparent reaction we've measured at touch bars was partly or wholly a touch-bar scoring artifact. Any test of his "reversion trade" (ATR out, retest, fail) must exclude the event bar from outcomes.
- **Failed balance breakout** is his most conventional, data-testable idea — but our balance-day work ([[balance-day-fade-study]]) found flat/balance days lose even for rotation trades on NQ; the trapped-trader reversal premise would need to survive that headwind.
- **Options/GEX pillar** needs external data (SpotGamma/MenthorQ subscriptions); nothing in-repo to test it against.

## Demo — the skeleton drawn on our tape

`demo/pulcini_atr_demo.py` → **Pulcini's ATR skeleton** (`pulcini-atr.html` in this Research tab). Steps 3–5 of the skeleton — confirmation at ATR+15%, stop on the far side of the zone, size derived from that distance — laid over cached NQ sessions across a vol ladder from ATR 14 to ATR 102. Steps 1–2 aren't drawn: the location is his, and the MBO event is stood in for by a big-lot burst, deliberately leaky. The page switches lookback (5/10/14) and multiplier (×1.00/×1.15/×1.50).

It is a diagram, not a backtest — no exit rule, no sample. What it does show, mechanically:

- **The stop is ~2.3× ATR by construction,** and that is the whole story of its width: entry sits an ATR+15% above the zone, the stop an ATR+15% below it, so risk = zone width + 2 × 1.15 × ATR regardless of regime. Median stop ~38 pt on a 14-ATR day, ~264 pt on a 102-ATR day.
- **It is not an artifact of the lookback we guessed at.** He never states a period; flipping 5 → 10 → 14 moves the median stop by a couple of points at most (36.6 / 38.3 / 37.7 pt on the quiet day; 265.6 / 262.1 / 263.7 on the hot one). 5-min NQ vol is persistent enough across 25–70 minutes that the three ATRs agree.
- **His sizing rule and his stop rule collide on NQ.** On 12 of the 32 confirmed events at ×1.15, a *single micro* already risks more than $500 — so the "flat $500 risk" rule and this stop placement cannot both hold. Instrument matters here: he trades ES, which sits near a quarter of NQ's index level, so an equivalent move is ~4× fewer points. Against MES at $5/pt vs MNQ at $2/pt, the same 2.3-ATR stop costs roughly 1.6× more on the micro NQ than on the micro ES (rough — it assumes equal percentage vol, and NQ's is usually higher, so the real gap is wider).
- **His fixed-stop critique measures true here.** Carrying a 15-point stop on the same entries, the fixed stop is touched before the ATR stop on 30 of 32 confirmed events. The two survivors are a 39-pt stop on the quiet day and a 157-pt stop on a hot one — the exception is a stop wide relative to *its own* regime, not a quiet day as such.
- **The confirmation filter does bite.** 5 of 37 events never escape their zone by ATR×1.15 at all (9 of 37 at ×1.5) — exactly the snap-back he says the rule exists to sit out.

### Companion demo — the composite profile behind *Pick*

`demo/composite_profile_demo.py` → **Composite volume profiles** (`composite-profile.html`). His *Pick* location is the edge of a multi-day composite's 70% value area, and he never says how many days. The page builds four rules on the same 40 sessions — balance-anchored (accumulate while each session's value area still touches the composite-so-far's, restart on a clean break), 3-day, 10-day, 20-day — and draws the resulting distribution next to the price it came from, with POC, VAH/VAL, and prominence-scored HVN/LVN nodes.

The measured result is about **value-area width**, which is what decides whether a composite names a location at all: median **226 pt** (balance) → **466** (3-day) → **1,154** (10-day) → **1,753** (20-day). A value-area edge 1,750 points wide is describing the market's whole excursion, not a price it agreed on. Balance runs over 599 sessions are median 2 days (p90 4), so a 20-day composite on NQ merges ~8 auctions.

The page also answers the usage question: a **live view** freezes the composite at the prior close and scrubs today through it in 15-minute steps, composite profile growing right from a centre line and today's developing profile growing left, with a read-out naming the relationship (accepting inside prior value / extending through VAH / building value clear of it). The composite must be frozen intraday and rebuilt between sessions — letting today's volume feed it is circular, since the POC drifts toward wherever price sits and the level can then never be meaningfully violated. Candle timeframe (5/15/30/60m) and the node knobs (prominence 15–60%, smoothing 2–8%) are all live; the timeframe is cosmetic, since profiles, value areas and nodes are computed from raw ticks at 0.25-pt resolution and never from candles.

#### Events on the composite — the MBO half, stood in for by big trades

His step 2 is an event **at** the level, and the MBO iceberg/stop-run label is the one input we cannot reconstruct. The page now overlays two trade-print proxies that split the idea in half:

- **Sweep burst** — consecutive same-side fills glue into one order-shaped sweep; sweeps over 50 lots landing within 60 s and 5 pt of each other glue into a burst, and a burst needs 150 lots to count. This is the *aggressor's* footprint — the stop-run half.
- **Absorption** — a 15-second window whose lots-per-point-traversed runs 3× the session's own median. Size trading with nowhere to go — the iceberg half.

The threshold is relative to the session on purpose: **an absolute price band is unusable for this on NQ.** Measured across 2025–26, the median 15-second RTH range is 4.75–6.00 pt and the median *60-second* range is 11.75–26.75 pt, so a fixed "4 points of travel" window finds thousands of events on a quiet day and exactly zero on a busy one — at 60 s / ≤4 pt / ≥900 lots it fired **0 times in 3 sessions tested**. Concentration against the session's own median is what survives regime change, and it recovers ~19 events per session across both proxies.

Neither is the MBO label, and the demo says so: a sweep is the aggressor's footprint, not the resting order that refilled, and absorption cannot tell a refilling iceberg from a thick crowd of small passive orders.

**The overlay comes with a null, and the null kills the reading.** Events land where price traded, and price traded most where the composite says value is — so "the events stack at the POC" is true of *any* subset of the tape, including a random one. Scored only in the live view, where the composite was frozen before the events printed, against POC/VAH/VAL (so the number doesn't move when the node knobs do):

| | n | median distance to a level | within 10 pt | vs that session's own volume | sessions nearer |
|---|---|---|---|---|---|
| sweep bursts | 276 | 176.8 pt | 6.5% | **+20.5 pt** | 15/36 |
| absorption | 454 | 187.0 pt | 5.9% | **+22.9 pt** | 14/39 |
| volume null | — | 128.0 pt | 6.7% | — | — |

Both proxies land **further** from the frozen composite's levels than the session's own volume-weighted tape does, and only ~40% of sessions run the other way. Re-cut over 120 sessions the sign holds and the magnitude shrinks (+6.8 pt sweeps, +7.4 pt absorption; 48/111 and 48/119 sessions nearer) — consistent, and never negative. Big size is not level-seeking against a prior-frozen composite here; if anything it arrives where price has already left value, which is what you would expect if bursts are breakouts. This does not refute his edge — he is reading resting liquidity, which is a different object from executed size — but it does mean the overlay should be read as a picture of where size arrived, not as evidence that these levels attract it.

Two claims the page deliberately withdraws. **Bimodality is not evidence of merged auctions** — a single-session double distribution is a textbook trend-day shape, and ~30% of lone NQ sessions print two prominent humps even at a strict prominence threshold. And the **split-value-area** flag (deepest trough inside the VA under 35% of the tallest hump) fires under every rule here, because NQ sessions are frequently split on their own; it is shown as a property of the data, not as a discriminator.

**Bottom line for us:** the durable, transferable content is the risk architecture (ATR-denominated stops, event-based trailing, external loss limits) — which our own studies already support — not the iceberg edge, which rests on a data feed we don't have and on claims shaped like ones that have died in our A/Bs before.
