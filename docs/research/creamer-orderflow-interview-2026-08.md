# Chris Creamer (Robbins World Cup champion) — process teardown

**Video:** [Trading WORLD CHAMPION Reveals the Orderflow Strategy That Won the Robbins Cup (Step-by-Step)](https://www.youtube.com/watch?v=PL7LKUsCgIQ) — IQCapital, 58:00, published 2026-08-11. Transcript: `data/research/vwap-wave-livestreams/PL7LKUsCgIQ.txt`.

**Who:** Chris Creamer, 26. Claims the July 2026 Robbins World Cup **Micro** Day Trading Championship with a +100% monthly return. Intraday NQ trader; trades and reads order flow on **MNQ**, on 5-minute charts, in the **first 90 minutes of the NY open only**.

**Format caveat, stated up front:** this is a whiteboard interview on a prop firm's own channel, with a mid-video ad for a $9 50K challenge ([31:22](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1882s)). No trades are shown, no statements, no chart replay — every number in here is self-reported. The *micro* division also means +100% is a sizing statement as much as a skill statement. None of that makes the process wrong; it means nothing here is evidence, only hypothesis.

**Not a trade log.** There are no live trades in this video, so the usual teardown output (timestamped fills pinned to the ET clock) does not exist. The deliverable is the process and the testable residue.

---

## The process in one paragraph

Pre-open, he classifies the **environment** twice: higher-timeframe value structure (up / down / sideways on 1H–4H) and options-dealer **gamma regime** (positive = vol-dampening, breakouts fail; negative = vol-amplifying). That decides direction bias and what kind of day to expect. Then **location**: in a value-up structure he only buys *discount*, defined as a 0.705–0.886 fib retracement zone that must sit **outside** the value area. Then **confirmation** from 5-minute footprint/delta candles: aggressive sellers pile into the extreme of the candle, get absorbed, fail to produce price progression, dominance flips, and — critically — they must try **a second time and fail higher** before he enters. Stop goes under the failed sellers; the first checkpoint is whether buyers reclaim the value area; targets are swing points, trailed behind continuing aggression. Everything is gated by a hard participation floor (20k contracts per 5-min MNQ candle) and a hard 90-minute clock.

---

## Timestamped index

### The four steps

| Time | What's covered |
|---|---|
| [0:33](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=33s) | Overview: environment → location → confirmation |
| [5:02](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=302s) | **Forced participation** defined — the whole thesis in one answer |
| [8:08](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=488s) | Step 1: value-up / value-down / sideways on 1H–4H; where value is being created |
| [9:22](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=562s) | GEX introduced — why dealer hedging matters |
| [10:37](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=637s) | Positive vs negative gamma ≠ long vs short; it's a **volatility** statement |
| [12:18](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=738s) | Tanuki Trade; naive vs inferred GEX; CBOE data is SPX-only and ~$300/mo |
| [14:06](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=846s) | Call wall / put wall / gamma flip zone — **all drawn pre-open, never mid-move** |
| [14:33](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=873s) | Step 2: location = discount vs premium; never fade the HTF structure |
| [16:04](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=964s) | Prefers an **inefficient** move into the zone (fast traverse, low-volume node) |
| [17:07](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1027s) | Step 3: confirmation — "a box on the chart doesn't mean price respects my drawing" |
| [17:54](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1074s) | The two candle types: volume-profile-inside and delta-profile-inside |
| [18:38](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1118s) | Absorption at the extreme: POC + negative delta parked in the lower wick |
| [19:23](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1163s) | Dominance shift — candle closes bullish |
| [20:04](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1204s) | **The second failure** → entry; stop under the failed sellers |
| [21:58](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1318s) | First checkpoint: buyers must **reclaim into the value area** or he cuts/BEs |
| [22:41](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1361s) | Targets: swing points primarily, sometimes POC; why clustered orders there fill |
| [23:34](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1414s) | Management: trail **behind aggression**, only while effort produces progression |

### The location rule, in detail

| Time | What's covered |
|---|---|
| [25:12](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1512s) | Fib swing-low→swing-high; levels **0.705 / 0.788 / 0.886** ("golden pocket" zone) |
| [25:44](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1544s) | **The fib zone must sit outside the value area** — inside VA, he doesn't want it |
| [26:00](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1560s) | Wants internal structure (a swing point / sweep) before entering discount |
| [26:53](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1613s) | **0.886 is the hard invalidation** — through it with no dominance shift, no trade |

### The order-flow trigger, in detail

| Time | What's covered |
|---|---|
| [27:41](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1661s) | Trades 5-min (watches 1H / 15m / 5m, sometimes 1m); wants it at the **extremes** |
| [28:27](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1707s) | **"Absorption doesn't mean automatic reversal. Absorption happens constantly."** |
| [29:20](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1760s) | Why not the book: icebergs + algo orders load and pull too fast to read |
| [29:57](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1797s) | Bid×ask footprint; indicator bolds imbalances of **≥400%** |
| [30:49](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1849s) | "I wait. Doesn't mean I go long… when we try again and fail the second time, I'm long" |

### Risk, filters and frequency

| Time | What's covered |
|---|---|
| [32:47](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1967s) | Self-reported: **1.5–2R**, win rate **60–65%**, profit factor **~1.8** |
| [33:20](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2000s) | Prop math: $2,000 drawdown vs $3,000 target on a 50K **is** 1.5R — so target 1.5R |
| [34:09](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2049s) | Anti-big-R: "I want to be right, I don't want to look cool"; extra confirmation is worth a worse R |
| [36:20](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2180s) | **Order flow used to filter trades OUT, not find more.** 0–2 trades/day |
| [37:42](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2262s) | **Participation floor: <20,000 contracts per 5-min MNQ candle → don't touch it** |
| [38:26](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2306s) | Why he trades only 90 minutes: slow grinds, tapering volume, "I don't touch it" |

### Process and behaviour

| Time | What's covered |
|---|---|
| [39:22](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2362s) | How to learn it: auction market theory + an order-flow platform (ATAS, DeepCharts) on replay |
| [40:02](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2402s) | **A / B / C game sessions**, graded on execution not P&L; "back-end optimization, not front-end" |
| [41:45](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2505s) | Good loss vs bad loss — and why a bad trade that **wins** is the worst outcome |
| [44:15](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2655s) | "Don't overtrade" is not a rule — rules need an action attached and must be personal |
| [45:29](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2729s) | Diagnosing tilt: traders blame the big losing trade; that's not where it broke |
| [46:10](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2770s) | **His own trade data showed results degrade ~90 min after the open → hard shutoff time** |
| [46:50](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2810s) | **Stop after 2 losses in a row** (3 in a row ≈ 50/50 he starts making bad decisions) |
| [49:56](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2996s) | Backstory: quit job, Instagram guru, lost savings, into debt, drained crypto in a bear market |
| [52:24](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=3144s) | Turning point: **sized down to 1 micro**, removed money from the equation, graded execution only |
| [53:17](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=3197s) | If starting over: learn market mechanics first, then pick **one** strategy matched to your personality |
| [55:53](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=3353s) | Final advice: it's a behaviour problem, not an information problem |

---

## The setup, assembled

Everything below is one trade. The long case is written out; the short is the mirror (premium, buyer absorption at the highs, failure lower).

**1 — Environment** (pre-open, [8:08](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=488s))

- HTF value structure on 1H/4H: is value being created higher, lower, or sideways? Basic higher-highs/higher-lows. This sets the only direction he will trade.
- Gamma regime from **naive GEX** (QQQ/NDX proxies for NQ; CBOE-sourced inferred GEX is SPX-only and ~$300/mo, so NQ traders get naive). Positive gamma → dealers sell rips, buy dips → vol dampened → **breakouts fail**. Negative gamma → dealers buy rips, sell dips → vol amplified → faster, larger moves.
- He explicitly does **not** trade bounces off the call/put wall. GEX is a regime input only; the walls and the gamma flip zone are context lines.
- Hard rule: this is all done before the open. *"I will never do this while price is moving a million miles an hour."*

**2 — Location** ([14:33](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=873s), [25:12](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1512s))

- Trade *with* the value structure; never call the top or bottom. In a value-up structure, wait for a pullback into **discount**.
- Discount/premium are defined off the value area: below VAL is discount, above VAH is premium.
- The zone itself is a **fib retracement, swing low → swing high, at 0.705 / 0.788 / 0.886**.
- **Conjunction rule:** the fib zone must land *outside* the value area. A fib level sitting inside VA is discarded. This is the one genuinely unusual construction in the whole process — two independent location systems required to agree.
- Wants a swing point (internal structure / a sweep) forming before price enters the zone — not a straight-line drop.
- Prefers the approach to be **inefficient**: a fast traverse through a low-volume node, where little business was actually transacted.
- **0.886 is the invalidation.** Below it without a dominance shift, the trade is dead — *"if the pullback's going to happen, it should happen before we get past the 886."*

**3 — Confirmation** ([17:07](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1027s))

Two chart types side by side on the 5-minute: a candle with a **volume profile inside it**, and a candle with a **delta profile inside it**, plus bid×ask footprint with an indicator that bolds any **≥400% imbalance**. His stated reason for reading candles rather than the book: icebergs and algo orders load and pull faster than the DOM can be read.

The sequence, in order:

1. Price enters discount. Sellers get aggressive — POC and heavy **negative delta** concentrate in the **lower wick**, at the extreme of the candle.
2. They get no result. Effort without price progression = absorption.
3. **But absorption is not the signal.** He is emphatic: *"absorption doesn't mean automatic reversal — that's not how it works. Absorption happens constantly throughout the chart."* It only earns attention when the next thing happens.
4. **Dominance shift** — the candle closes bullish, ask-side imbalances start bolding (aggressive buyers lifting the offer).
5. **The second failure.** Next candle opens and immediately pulls back; sellers press again; they must fail **higher** than the first failure. *"I wait. Doesn't mean I go long, because we can be whiplashing around. I want to see us try again. And when we try again and we fail for the second time and flip again, I'm going long."*
6. **Stop** goes on the far side of the failed sellers — the trade is invalidated the moment sellers push past the level they couldn't push past the first time.

**4 — Management and targets** ([21:58](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1318s))

- **Checkpoint:** buyers must reclaim back **into the value area**. If they push aggressively and still can't get in, he moves to break-even or cuts. This is a hard early gate, before any target logic.
- **Target:** swing highs/lows primarily, sometimes POC. His reasoning for swing points: the market's job is to take swing points, and clustered orders at swing points and psychological levels tend to actually be there to get filled — unlike book orders, which get pulled.
- **Trail:** behind aggression, not behind price or ATR. While buyer effort keeps producing price progression, he trails; when aggression continues but progression stops, that's the exit signal. Trailing tightens as price approaches the call wall.
- Realized outcome, self-reported: mostly **1.5–2R**, win rate **60–65%**, PF **~1.8**.

**Hard no-trade filters**

| Filter | Rule |
|---|---|
| Participation | **<20,000 contracts per 5-min MNQ candle → no trade.** Below that, participation is dying and he's heading into lunch |
| Clock | First **90 minutes** of NY open only (occasionally Asia). Hard shutoff, derived from his own trade data |
| Location | Fib zone inside the value area → discard |
| Structure | Price through the **0.886** without a dominance shift → dead |
| Sequence | Only one seller failure, no second test → no entry |
| News | A release landing right as price enters the zone → skip |
| Behaviour | **2 consecutive losses → done for the day.** Any execution mistake (chased, entered early, anticipated) → shut it down |

Typical frequency: **0–2 trades a day.** *"When I started using order flow, either you take more trades because there's more data… or you do the opposite, which is what I did, and use it mainly to filter trades out."*

---

## Core principles distilled

1. **Trade forced participation, not balance.** Value builds while both sides are comfortable. The trade exists when one side commits at an edge and *fails* — those participants are now trapped and must transact against themselves to get out. Every element of the setup is aimed at locating that trapped inventory.
2. **The entry is the last 5–10% of the job.** Setups and candlestick patterns were his holy grail for years and were the wrong object. Context — who is participating, what they need, where they get forced — is the other 90%, and the entry only confirms or denies a thesis that already exists.
3. **Absorption is background noise until dominance shifts.** This is his sharpest technical point, and it is the opposite of how absorption is usually sold.
4. **Two independent location systems must agree.** Fib retracement zone *and* outside the value area. Either alone is discarded.
5. **Effort versus result, in both directions.** The entry reads sellers' effort failing; the exit reads buyers' effort failing. Same instrument, symmetric use.
6. **Selective participation is the edge.** *"More often than not, the best informed decision I can make is to not participate… your biggest advantage is that you have selective participation, and most traders don't take advantage of it."* Also a focus argument — he doesn't want back-to-back decisions wearing down his judgment.
7. **Optimize the back end, not the front end.** You don't control how many A+ setups appear. You control the dumb losses. Consistency comes from eliminating C-game sessions, not from adding A-game sessions.
8. **A bad trade that wins is the worst outcome** — it reinforces the behaviour that produces C-game sessions.
9. **Rules need an action attached.** "Don't overtrade" is not a rule. Find your personal line where calculated decisions become emotional ones — it may be a dollar figure, a count of break-evens, a count of losses, or *overconfidence after wins* — and attach a specific stopping action to it.
10. **Tilt starts before the big loss.** When a trader points at the trade that blew the account, that isn't where it broke; a sequence of events preceded it. Find the sequence, build the rule there.

---

## Mapping to our research

**Where he independently agrees with us:**

- **Absorption ≠ reversal.** His caveat at [28:27](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=1707s) is, almost word for word, what our own data kept saying: absorption-style tape signals nulled in [[bigprint-digestion-study]], and [[loser-orderflow-study]] found no tape signal at any loser anchor. A champion volunteering the caveat unprompted is the most credible moment in the video.
- **Trailing behind aggression, not behind a multiple.** [[atr-trailing-stop-study]] failed `trail_atr_mult` at every multiplier, and [[replay-trail-whatif-analysis]] found no-trail was the *worst* exit and t25/be25 won. Event-based trailing is the same conclusion Pulcini reached ([[pulcini-podcast-notes]]) from the other side of the market.
- **Selective participation / trade-count discipline.** [[manual-trade-behaviour-audit]] found sub-30s trades were the one real leak in the user's own journal — a trade-selection failure, not a strategy failure.

**Where he collides with what we've already measured:**

- **The 90-minute window.** [[drift-touch-fade-build]] found *afternoon-only* was the edge on that baseline, and [[drift-fade-poc-price-action]] found the first hour carries net. So "morning only" isn't a universal — it's baseline-dependent, and his is not ours.
- **Fib / VP geometry as location.** Our priors here are poor across the board: [[lvn-retrace-continuation-deepdive]] NULL, [[stable-level-sr-study]] a coin flip, [[avwap-reclaim-study]] NULL, [[vah-snap-resistance-study]] found VAH-above-price is acceptance not resistance. The fib zone itself inherits that prior. The **conjunction** (fib ∧ outside-VA) is untested here and is the part worth isolating.
- **The confirmation trigger reads the anchor bar.** His footprint read is taken from the candle forming *at* the level — precisely the construction that produced artifacts in [[weekly-vwap-context-study]] and the circular `close_al` in [[structure-orderflow-study]]. Any test of this must score from the bar *after* the second failure, never from the failure bar.
- **GEX is not testable here.** Needs an options feed (Tanuki Trade, or CBOE data at ~$300/mo). Same dead end as Pulcini's options pillar. Worth noting that two independent champion-level discretionary traders both put dealer gamma in their top two inputs, and neither can be checked from this repo.

**Where it bears on the live account:**

His prop arithmetic at [33:20](https://www.youtube.com/watch?v=PL7LKUsCgIQ&t=2000s) — $2,000 drawdown against a $3,000 target *is* a 1.5R, so a string of 1.5R trades passes by construction — is the same shape of reasoning as [[lucidpro-50k-account]]. Worth running against the actual LucidPro DD and target rather than his numbers; if the ratio differs, the implied minimum R differs with it.

---

## Testable residue

Four items, in descending order of how much new information they'd produce. Everything else in the video is either untestable (GEX), already answered (absorption, ATR trailing), or behavioural.

**1 — The participation floor. RESOLVED NULL, 2026-08-14 — see [participation-floor](participation-floor.md).**

Ran on 599 sessions. Volume-for-time-of-day predicts **how big** the next move is (2.04×) and **nothing** about how well it carries (efficiency ratio 1.02×, ρ = +0.012). It is realised volatility renamed — ρ(residual, forward path) = +0.376 — so it collapses into [[vol-clock-study]], which is already adopted. **No knob. Do not rebuild.**

His rule is probably still right in practice; his stated reason isn't. Quiet-period moves carry just as well proportionally — they are simply half the size (25.9 pts vs 52.7), which is worse against fixed costs at identical quality. That is a volatility statement, not a participation one.

Two method notes worth keeping, since both nearly went wrong:

- **The threshold couldn't port** — MNQ is 1/10 notional with a retail-heavy mix and the NQ:MNQ ratio drifts, so no divisor exists. Ported as a percentile instead.
- **ρ(log volume, minutes-from-open) = −0.529**, so the raw gate was indeed a clock in disguise. But the pre-screen as originally written here — "if ρ is high, it's a re-description, drop it" — was **too blunt and would have discarded it without learning why**. High collinearity means the raw gate is untestable and the *time-of-day residual* is what must be tested. Control, don't screen-and-discard.

**2 — The fib ∧ outside-VA conjunction.** Isolate the conjunction, not the fib. The question is narrow and answerable: conditional on a 0.705–0.886 retracement into a pullback, does the subset where the zone falls outside the value area outperform the subset where it falls inside? If it doesn't, the whole location step reduces to the VP geometry we've already nulled four times. Cheap to run, and it's a clean single-factor test.

**3 — Two behavioural rules, checkable against the user's own journal, not against tick data.**

- *Do results degrade ~90 minutes after the open?* He found this in his own trade data and built a hard shutoff from it. Directly measurable on the user's history, and the answer is personal — a general market fact isn't what's being claimed.
- *Does the trade after two consecutive losses underperform?* [[manual-trade-behaviour-audit]] already **refuted** revenge trading in this journal, so the prior is that this rule solves a problem the user doesn't have. Worth confirming rather than assuming, since it's a one-query check and the audit tested revenge, not the specific two-in-a-row sequence.

**4 — The second-failure requirement.** The distinguishing feature of his trigger versus the absorption family we've nulled is that it demands a *sequential double rejection*, with the second failing at a better level. That's a different object from single-touch absorption and hasn't been tested here. But it sits behind the anchor-bar screen and behind a poor family prior, so it's the last thing to spend effort on — and only if (1) or (2) survives.

**Not worth building:** GEX regime (no data), call/put wall levels (he doesn't trade them either), the fib zone alone (see the four nulls above), footprint imbalance thresholds (no L2/MBO; [[structure-orderflow-study]] NULL both ways).
