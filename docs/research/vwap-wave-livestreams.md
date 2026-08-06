# VWAP Wave live streams — trade teardown vs. cached ticks

**Status:** RESOLVED — no new mechanism, across four passes. Four sessions verified tick-by-tick; three more extracted only.
**Source:** nine Trader Drysdale live streams (**2026-04-29**, 05-26, 06-01, 2026-06-04, **06-05**, **06-11**, 06-12, 2026-07-14, 2026-07-22).
**Verified against:** `NQM6_2026-06-04/06-05/06-11/06-12_day.parquet`.
**Companion:** [vwap-wave-toolkit-video](vwap-wave-toolkit-video.md) (the paid indicator demo, same system).

> **Pass 2 (2026-08-03)** added 06-05 and 06-11 — see [Pass 2](#pass-2-the-regime-control)
> at the bottom. It closes the open question this doc left (every session sampled
> was the same day shape), builds the two anchors he quotes that we never had
> (monthly VWAP, N-day composite profile), and resolves prior-day IB extensions
> to null. Verdict unchanged.
>
> **Pass 4 (2026-08-03)** added 2026-04-29 extract-only and found the **ES handle
> used by passes 1–3 was wrong** — 65xx should be 75xx — see
> [Pass 4](#pass-4-two-more-sessions-and-a-handle-that-was-wrong-all-along).
> Verdict unchanged; the two-digit decodes were fine, only the handle moved.

Sibling of the toolkit-video pass, but a different question. That one asked *what
does the indicator do*. This one asks *what does he actually do with it, and do
the levels he calls exist*. The answer to the second is yes — which is the only
genuinely new information in four hours of video.

## Method

Two non-obvious steps make this checkable at all; both are automated in
`data/research/vwap-wave-livestreams/`.

**1. Anchor video time to ET.** Streams are partial — they start after the open —
so video time is meaningless until pinned. Presenters pin it for you: they do
trader check-ins on the half hour and say the clock aloud. A scheduled data
release is even better (the EIA nat-gas print at 10:30 ET fixed 06-04 to the
second). Two independent anchors per video agreed to under a minute:

| Session | Anchors | Offset (ET = video + x) |
|---|---|---|
| 2026-06-04 | EIA 10:30 release; "it's only 10:05" | **+10:00** |
| 2026-06-12 | "it's 10:30"; "well, it's 11:00" | **+9:47** |
| 2026-07-14 | "hitting the 10:00 hour"; "it is 10:37" | +9:56 |
| 2026-07-22 | "IB in about 6 minutes"; "coming into the 11:00 hour" | +10:17 |

**2. Decode the price shorthand.** Levels are spoken as the last 2–3 digits
("long at 268", "starter 401"). Reattaching the handle from the session's actual
range is what turns commentary into a checkable claim. *Pass 4 correction: for a
second instrument you have no data for, grep the transcripts for 4-digit tokens
first — a spoken full price beats any handle you infer, and the ES handle used
here was wrong for three passes because that step was skipped.*

Two traps worth writing down. **Upload date lags the session by a day** — the
06-12 video uploads 06-13, a Saturday, and the body says "have a wonderful
weekend, back Monday." Confirm the date from the transcript (or, better, read
`release_timestamp` — see Pass 4). And the
**Globex/ETH VWAP keeps developing through RTH**; freezing it at the open puts it
tens of points off and will make a correct call look wrong.

## Both sessions are the same day

This is the control, and it governs everything below.

| | 2026-06-04 | 2026-06-12 |
|---|---|---|
| Open → low | −117 (low 10:07) | −264 (low 09:45) |
| Low → high | **+452** | **+530** |
| Close vs open | +217 | +154 |

Morning flush, then trend up all day, twice. Every long taken on either day
worked; every short initiated after the flush failed or was scratched. Eight
"confirmations" of a system is one market condition sampled twice.

## The levels are real

Sampled checks that came out right, all recomputed from ticks with the engine's
own `vwap` / `profile` primitives:

| Call | Claimed | Computed |
|---|---|---|
| 06-04 09:34 "textbook VWAP bounce short" | at VWAP | 09:34 high **30,353.75** vs developing Globex VWAP **30,354.80** — 1 pt. Ran ~200 pts to the 10:07 low |
| 06-04 ~10:07 long 30,158 | HTF level + pivot S3 30,143 | session low **30,151 at 10:07** — bought 7 pts off the low |
| 06-04 ~10:21 long 30,268 | "reload at the point of control" | developing **POC 30,265** — 3 pts |
| 06-04 10:59 "looking 85 to 405 for the short" | "beyond IB high" | **IB high 30,364.50**; 30,401 = **+2.3σ** RTH |
| 06-12 ~10:03 long 29,475 | backtest of the VWAP break | Globex VWAP ≈ **29,477** |
| 06-12 ~10:04 reposition 29,463.25 | — | 10:04 low **29,461.00** — 2.25 pts |
| 06-12 ~10:21 "first PDC long off the RTH" | RTH upper band | RTH +1σ **29,514–29,518**; price pulled into it 10:21–10:23 |
| 06-12 ~10:43 "40 points bounce" | — | 29,408.50 → 29,449.50 |
| 06-12 ~10:44 "the break was a nice short" | RTH VWAP failed | RTH VWAP 29,436 broke that minute |

So "get in at the exact level" is not marketing. He hits them. The levels are also
all things we already compute — VWAP, σ-bands, POC/VAL, IB. Nothing exotic.

## What the teardown actually shows

**1. The scale-out costs nearly everything.** 06-12's best trade entered
29,463–29,475 with a stated runner target of 29,555; that leg topped 29,597 and
the day topped 29,760 — ~90 of 530 points. 06-04's POC long exited ~30,333 on a
day that closed 30,485 and topped 30,603 — 65 of 452. He stops trading before the
largest part of both moves. This is internally consistent with his own doctrine
("the anemic bounce is the most common scenario… that's why I scalp so
frequently"), and it means the demonstrated edge is not the level — it is exiting
before the level can fail. Consistent with [winner-landing-depth](winner-landing-depth.md).

**2. Both losers are the trades he never names.** Every winner gets a setup label
out loud, usually before the fill — "there's the PDC long right there", "we got
the return to value", "textbook VWAP bounce short". The 06-04 short at 30,401
gets a *zone* but no setup; the 06-12 loss gets only "I like this level down here
for a potential long". His own stated filter is "I generally won't take a really
good setup unless it's a textbook setup" — on both days, the two trades he
couldn't name are the two that lost.

**3. Setup family splits with the regime, not with quality.** Continuation
(PDC / return-to-value / VWAP bounce) went 9-0-1 across both days; the fade family
went 1 small win, 2 losses. On flush-then-trend days that was structural.

The one distinction sharper than anything he articulates: his 06-04 10:07 long
*was* a fade and won — but it faded a morning flush into a **higher-timeframe**
level (monthly VWAP upper band + pivot S3, 7 pts off the low). The two fades that
lost were both against the established **intraday** trend. HTF-fade vs
intraday-counter-trend-fade, not fade vs continuation.

**4. The best trade of each session happens before the stream.** 06-04 opens at
10:00; the ~200-pt Globex-VWAP short was 9:34 and is narrated in hindsight
("who in the chat caught that beautiful trade?"). 06-12 opens 9:47 with him
admitting he missed two VWAP shorts. The trades that anchor the room's belief are
disproportionately the ones nobody watched.

## Verdict

No gate, no knob, no engine A/B. The level-hitting is genuine and the null shelf
already explains why it doesn't compound: reactions at these levels are real but
shallow ([winner-landing-depth](winner-landing-depth.md)), and the directional
content is day-with only ([globex-bounce-regime](globex-bounce-regime.md); also
the balance-day-fade pass, memory-only — every responsive premise failed A/B).
A discretionary trader harvesting the shallow part and stopping is exactly what
those results predict; his outcomes and our nulls are compatible.

**Do not** re-mine this presenter's live streams for mechanisms — three passes
(toolkit video + these four) have produced zero. The residue is methodological:
the teardown workflow itself, now in
`data/research/vwap-wave-livestreams/` and the `trading-video-teardown` skill.

One thing the data would not support: 06-12 11:04 "we extended the value area, we
did not migrate value" — value extended (VAH 29,526 → 29,642), but the developing
POC also moved 29,300 → 29,550 at 10:54.

---

# Pass 2: the regime control

Pass 1 ended on a caveat it could not fix: both verified sessions were the same
day — morning flush, then trend up — so "eight confirmations of a system" was one
market condition sampled twice. These two streams were picked because one of them
is not that day.

| | 2026-06-05 (Fri) | 2026-06-11 (Thu) |
|---|---|---|
| Open → close | **−1,020** | +704 |
| High / when | 30,100.50 @ 09:32 | 29,544.25 @ 15:35 |
| Low / when | 28,974.25 @ 15:18 | 28,577.50 @ 11:01 |
| Shape | **trend down all day** | flush → trend up (again) |

**06-05 is the first non-day-with session in the sample.** Clock anchors:
06-11 `ET = video + 9:56` (EIA nat-gas countdown → 10:30:00, the 10:07 trader
check-in, and the 10:26 short below all agree); 06-05 `ET = video + 10:03`
("it is 11:00", plus two price anchors that land on the tick).

## He hits levels — four more to the tick

Pass 1's one genuinely new finding was that the level-calling is real. Pass 2
sharpens it. These are the strongest four, all recomputed from ticks:

| Session | Call | Computed |
|---|---|---|
| 06-11 10:26:48 | short **28,914**, "return to value back test" | developing **VAH = 28,914.00**; the 10:26 bar's high is 28,915.25 — he sold 1.25 pts off the minute high, and it fell 77 pts inside that minute |
| 06-11 ~11:01 | long **28,592** "fade value area extreme", "right at the band… drew you down about 14, 15 points below the band" | **Globex VWAP −1σ = 28,592.30**, flat at ~28,592 for 15 minutes. Session low **28,577.50** = **14.8 pts** below. Both the level and the stated drawdown are exact |
| 06-05 11:18 | "it's down at 57 and 3/4… 58" | **IB low = 29,758.00** exactly; the 11:18 bar straddles it (29,746.00–29,761.50) |
| 06-05 ~11:21 | "approaching yesterday's IB … at 25… the IB extension was hit to the tick" | −200% of 06-04's IB = 30,151.00 − 2(213.50) = **29,724.00**; the printed low is **29,724.00 at both 11:15 and 11:20** |

The 28,592 long is the sharpest trade in six streams: it is 14.8 points off the
low of a day that then ran 966 points. Also confirmed: the 10:28 long at 28,840
(bar range 28,791.75–28,850.00, +54 available against his stated +20 target), and
the 11:05 "IB low back test long" — IB low 28,650.75 and the **overnight-profile
POC 28,650.00** stack within a point, price tapped 28,641.00 and popped +104.

## NQ trade log

Every NQ trade in the two pass-2 streams, in session order. ES and
instrument-ambiguous fills are excluded — see the caveat in the verdict — which is
why 06-05 has exactly one row. **Bold** setups are his own words; *italic* is our
label where he named none. Machine-readable in `trades.json`.

### 2026-06-11 — low 28,577.50 @11:01 → close 29,456.75 (+704)

| ET | Side | Entry | Setup | Result | Checked against ticks |
|---|---|---|---|---|---|
| ~09:55 | long | — | **PDC** (1) | win — *not his* | Pre-stream, narrated in hindsight ("who took that PDC long on NQ?"); he says he missed it |
| ~10:12 | long | — | *none named* | **LOSS** | Long into the 10:16 flush, 28,867.75 → 28,776.75. Scaled down rather than stopped |
| 10:26:48 | short | 28,914 | **return to value back test** (3) | win | Developing VAH = **28,914.00** exactly; 10:26 bar high 28,915.25; −77 pts inside the minute |
| 10:28:39 | long | 28,840 | *rejection long* | win | Bar 28,791.75–28,850.00; 10:29 high 28,893.75. Stated +20 target, +54 available |
| ~10:34 | long | ~28,802 | **VWAP bounce** (4), first touch | win | +50/+80/+100 callouts at 10:36:02/34/59 all inside the 10:36 bar (high 28,944). Globex VWAP 51 pts lower — never touched |
| ~11:01 | long | **28,592** | **fade value area extreme** (2) | win | Globex VWAP −1σ = 28,592.30; session low 28,577.50 = **14.8 pts** below, matching his stated "14, 15 points" |
| ~11:05 | long | ~28,643 | **IB low back test** | win | IB low 28,650.75 + overnight-profile POC 28,650.00 stacked within a point; 11:07 high 28,745 (+104) |

Excluded: a ~11:25 long at 28,786.75 ("peel off one, quick four points"). That
price was last tradeable on NQ at 11:23, but the instrument is not confirmed.

### 2026-06-05 — open 30,035.75 → close 29,016.25 (−1,020)

| ET | Side | Entry | Setup | Result | Checked against ticks |
|---|---|---|---|---|---|
| ~10:03 | short | — | **PDC short "on the RTH"** (1) | win | 10:03 high 29,829.75 vs RTH −1σ 29,832.53 (2.8 pts short); fell to 29,773.50 by 10:04. Narrated at 10:05, just after the fill |

One NQ trade in 79 minutes of stream is itself the finding: on the day that was
not day-with he stepped aside — "I took two ES trades, two shorts… just kind of
enjoying a Friday, waiting for really good setups."

**The pattern from pass 1 survives into pass 2.** Every trade he assigns a setup
name to won; the single clear loser is the one he never names — and it is also the
one he scaled out of instead of stopping.

## …and then narrates the opposite direction

At **11:17 on 06-11 — sixteen minutes after printing the low he had just bought
to within 15 points** — he tells the room:

> "There really hasn't been a short setup… until we start trading down here. The
> real big opportunity short setups will take place down here."

The day closed +880 off that low. This is Pass 1's finding #1 (the scale-out costs
nearly everything) in a harder form: the entry precision and the directional
narration are not the same faculty, and it is the precision that is real.

06-05 shows the same split from the other side. His one directional lean —
"RSI 50 is holding on NQ at 29,865, if we can reclaim it, next stop is RTH VWAP"
(11:06) — never reclaimed; NQ fell to 29,695 within 17 minutes and 28,974 by
15:18. Meanwhile he barely traded it ("I took two ES trades, two shorts… just
kind of enjoying a Friday"). **The regime control mostly confirms Pass 1 rather
than overturning it**: on the day that was not day-with, the numbered entries
still landed and the narration still didn't.

## He concedes the touch-bar artifact out loud

The repo has twice found that his "retest"/"acceptance" edges are a touch-bar
scoring artifact ([weekly-vwap-context](../../MEMORY.md), toolkit-video pass).
On 06-11 he says it himself, and the co-host needles him for it twice:

> **Him:** "This would be our VWAP bounce."
> **Co-host:** "Didn't touch the line." … "But it didn't."
> **Him:** "That's a trade that I'm taking first touch, because if everybody's
> looking to buy the VWAP, guess what's not going to happen. It's probably not
> going to touch the line."

The data agrees: that bounce low was 28,802 at 10:34 with the Globex VWAP 51
points lower at 28,751 — the line was never reached. The bounce itself is real
and fast (his +50 / +80 / +100 callouts at 10:36:02 / 10:36:34 / 10:36:59 all
land inside the 10:36 bar, high 28,944). So the *setup* is "buy near the level",
not at it — which is exactly why scoring it on the touch bar manufactures an edge
that isn't there.

## Three anchors built, one null resolved

Several of his running-commentary levels match nothing we compute, so Pass 2
built the anchors he actually quotes rather than scoring him against ours:

- **Monthly VWAP** (`composite_levels.py`) — the one piece the toolkit-video pass
  flagged as unbuilt. Now exists. On 06-11 monthly −1σ ≈ 28,985 and price was
  rejected out of 29,000–29,050 at 10:00–10:09, consistent with his "rejected out
  of monthly again" — but that is one sample and not a signal.
- **N-day composite profile** — his stated setting is nine days. On 06-11 the
  composite POC sits at 30,640, ~2,000 points away and useless; every composite
  call he made on these two days was on the other instrument.
- **Prior-day IB extensions** — the only level type in six streams the repo did
  not have, and the 29,724.00 hit above is genuinely tick-perfect. **Null-checked
  and RESOLVED NULL** (`ib_extension_check.py`, 402 real touches over 307 cached
  sessions).

The IB-extension check is worth keeping for the control, not the result. The
obvious placebo — the same level shifted ±25/50 pts — showed the extensions
"holding" by ~10 points at every multiple and both sides, p=0.001. That is an
artifact: a placebo 50 points *beyond* a down-extension can only be touched on
sessions where the real level already failed, so its penetration is larger by
construction. Rebuilding the placebo as a session-drift null **matched on
distance from the open** collapses it:

| control | 1× d_pen | 1× d_rev | 2× d_pen |
|---|---|---|---|
| shifted-level placebo (broken) | −10.1 | +0.4 | −14.3 |
| session-drift, unmatched | −13.25 (p=.001) | −4.62 | −8.75 (p=.17) |
| **session-drift, distance-matched** | **−0.25 (p=.95)** | **−0.25** | **−6.25 (p=.17)** |

The whole apparent effect was distance from the open: horizontals near the open
get crossed all session, so any far-from-open level looks like it "holds". Prior-
day IB extensions are arbitrary horizontals. Consistent with the standing result
that static prior-day references are near-dead
([drift-fade-entry-reason](drift-fade-entry-reason.md)) and that levels produce
shallow reactions with no directional content
([stable-level-sr](stable-level-sr.md), [anchored-vwap-reclaim](anchored-vwap-reclaim.md)).

## Verdict (unchanged, now with the control)

Six streams, zero new mechanisms. The regime control did not rescue anything: on
a trend-down day he traded less, his one lean failed, and his precision held. The
precision is real and repeatedly tick-perfect; it is also, by his own account, a
"buy near the level and scalp out" method, which is exactly what
[winner-landing-depth](winner-landing-depth.md) and the day-with results predict
will not compound.

**Do not mine this presenter again.** Pass 2 was justified only by the regime
control, and that question is now closed.

~~One caveat on the 06-05 log: the room quotes prices as trailing digits, and a
large block of that session's trade talk is in the 7,5xx range, which matches
neither NQ (29,7xx) nor ES (≈6,4xx on the prior pass's own scale). Those cues are
excluded rather than guessed, so 06-05's ES-side trade log is thin by design.~~
**Wrong — see [Pass 4](#pass-4-two-more-sessions-and-a-handle-that-was-wrong-all-along).**
ES was never 6,4xx. Those cues are ordinary ES quotes.

---

# Pass 4: two more sessions, and a handle that was wrong all along

Extract-only, on request: two more streams (2026-05-26, already covered by pass 3,
and **2026-04-29**, new). No tick verification was run on either. The extraction
turned up one thing that invalidates part of passes 1–3.

## The ES handle was inferred, and the inference was wrong

Passes 1–3 reattached ES handles from an NQ/ES ratio and settled on **6,5xx**.
The tape says otherwise, in four sessions, unprompted:

| Session | Spoken verbatim |
|---|---|
| 2026-05-26 | "and on ES this is at **7545** level" (said 3×) |
| 2026-04-29 | "Still won't get to 167. **7167 on ES**" · "I am short on the **7170**" |
| 2026-06-05 | "yesterday's anchored … lower band was at **7535**" · "a short from **7526**" |
| 2026-06-11 | "there's a composite low volume node at **7274**" |

06-11 then hands over the shorthand convention outright — a co-host interrupts to
ask *"Are you still saying the whole price, 7274?… Just use 74"*, and gets *"I can
say 274. Would that be more helpful?"* Across six transcripts **no 6,xxx price is
ever spoken.** The ratio check that produced 6,5xx can't discriminate (NQ/ES is
tightly clustered under either handle: ≈3.95–4.03 at 7,5xx, ≈4.55–4.65 at 6,5xx)
— which is exactly why an inferred handle should have lost to a spoken one.

`trades.json` is corrected throughout: every ES price shifts by +1,000 (05-26,
06-01, 06-04), and 2026-04-29 sits at 71xx. **The two-digit decodes were all
correct; only the handle moved**, so no reasoning changes — but the 06-05
exclusion above evaporates, and with it the claim that its ES log was thin by
necessity. The recovered cues are filed under that session's `recovered_prices`,
including a presenter short at ES 7530/7532 attributed on air to a PDC off
yesterday's anchored-VWAP lower band at 7535.

Reusable rule: **a spoken full price beats any handle you can infer.** Grep every
transcript for 4-digit tokens before decoding a single 2-digit one.

## A clock anchor better than anything spoken

`yt-dlp --print "%(release_timestamp)s"` returns the **actual live-start epoch**
for a `was_live` video. That is the exact ET of video 00:00:00 — no anchor
hunting, no ±1 min. Both sessions here pinned to the second:

| Session | release_timestamp → ET | Spoken anchors agreed |
|---|---|---|
| 2026-05-26 | 10:10:21 | "it's 10:30" at 00:20:52 → 10:31:13 ✓ |
| 2026-04-29 | 10:26:59 | "it's almost 11:00" at 00:33:19 → 10:59:59 ✓ |

It also settles the date without transcript archaeology: `upload_date` lagged the
session by a day on **both** videos, `release_date` did not. Keep the spoken
anchors as the cross-check — they confirmed the offset here — but stop treating
them as the primary source.

## 2026-04-29 — FOMC day, and he sits it out

Wednesday, April FOMC decision day. NQ RTH 27,124.25–27,340.25; IB high 27,277.00
at 10:29. Stream ran 10:27–11:18 ET and ended early because a fiber installer
arrived. He stops trading at ~11:00, three hours before the statement, and says so
("I am not going to be trading FOMC, just to be clear").

The session is one position plus scraps. At ~10:29 he shorts into **"the weekly,
the IB high, and the upper band all in the same vicinity — somewhat of an A+ setup,
actually. A+ fade. And I took it."** It runs against him, he scales in twice, holds
~14 minutes and exits on "a down bar on NQ" for a stated **+$528.60** on the day in
a $10k cash account. In the middle of that hold the setup's formal version — the
Madonna, first touch of the IB after it forms — breaks through instead of
rejecting: *"Here's the actual Madonna. I don't like it. He has broke through."*
He keeps the position anyway and it works.

The one number he gives for the setup family is worth recording: **"it does a 60%
probability … four out of every 10 times it's not going to work."** A separate ES
short from 7170, targeting RSI 50 at 7167, is **scratched** — then narrated at
11:14 as *"that short from 7170 doesn't look so bad now. I scratched it, though. I
didn't hold it at all."*

Nothing here is a new mechanism, which is the expected result. The verdict stands:
**do not mine this presenter again.** Extraction remains fine — the logs feed the
chart reconstruction.

# Chart reconstruction: the NQ trades as a draft

The 25 NQ trades he actually held are laid out on our own charts as the
**Drysdale NQ livestream trades** draft (Lab → Drafts). It exists so the pass-2
verdict — *level precision real, directional narration not* — can be looked at
against our VWAP anchors, bands, profile and IB rather than read.

The build refuses to invent the parts he never spoke, and that refusal is the
whole design:

- **10 of 25 exit at a price he said** — an outright exit price, or a round
  point count ("win ~200pt", "quick four points") applied to the entry and
  placed at the first bar that traded it.
- **15 exit at their own entry**, marked `open`. He never said where he got
  out, so the chart draws a flat one-bar sliver — an entry marker, which is all
  we know. `total_points` therefore covers 10 trades and is a scale, not a P&L.
- **No R anywhere.** No stop was ever spoken; `stop_price` is pinned to entry
  rather than invented.
- **13 entries spoken, 12 taken from the open** of the minute he named — never
  the bar's extreme, which would hand him a fill he never claimed.

A first build exited unresolved trades at a flat 30-minute horizon and was
scrapped, which is worth recording because the failure was loud: the horizon
contradicted his own stated results outright — **+293 points on the trade he
called a "$200 loss"**, −210 on "win, flat quickly", −127 on "win, big". He is a
scalper narrating partial exits; any fixed hold invents a trade he did not take,
and the rect's win/loss colour then reads as a result. The draft answers *where
did he get in* and declines to answer *how did he do*.

Not reconstructible: his ES and YM trades (~12 more — the tick cache is NQ
only), one missed setup and one aborted hunt he never held, and two rows whose
instrument he never stated.

## Files

- `data/research/vwap-wave-livestreams/transcribe.py` — YouTube → timestamped transcript
- `data/research/vwap-wave-livestreams/session_levels.py` — rebuild VWAP anchors / profile / IB / day shape
- `data/research/vwap-wave-livestreams/extra_levels.py` — ETH-session profile + weekly VWAP bands
- `data/research/vwap-wave-livestreams/composite_levels.py` — monthly VWAP + N-day composite profile/LVNs
- `data/research/vwap-wave-livestreams/ib_extension_check.py` — prior-day IB extension null check
- `data/research/vwap-wave-livestreams/trades.json` — the trade log for all seven extracted sessions (four verified, three extract-only)
- `data/research/vwap-wave-livestreams/build_drysdale_draft.py` — trades.json → the NQ draft's trade table (conventions documented in its docstring)
- `data/drafts/drysdale-nq-livestream-trades.json` — the draft spec
