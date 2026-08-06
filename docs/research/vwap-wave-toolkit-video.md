# VWAP Wave System Toolkit masterclass — transcript + claim mapping

**Source.** [youtube.com/watch?v=JiLLSehLntg](https://www.youtube.com/watch?v=JiLLSehLntg)
— "VWAP Wave System Toolkit Masterclass | Full Walkthrough", Trader Drysdale,
1h19m, uploaded 2025-08-30. Full cleaned transcript with timestamps at
`data/research/vwap-wave-toolkit/transcript.txt` (auto-caption track via
yt-dlp, 1,730 caption lines → 92 timestamped paragraphs).

**What it actually is.** A product-launch walkthrough of a paid TradingView
indicator (built by Flux Charts around the presenter's book), not a research
talk. ~50 min of feature demo + ~28 min of Q&A. Zero backtests, zero sample
sizes, zero out-of-sample claims — the presenter is explicit about this
("this isn't a signal indicator, buy here sell there… you need to develop the
discretion of an experienced trader", 00:57:43). Treat every mechanism below as
a *hypothesis to race*, never as evidence.

**Headline.** **No new mechanisms.** Every feature in the toolkit maps onto
something this repo has already built or already resolved null. The video's
value is confirmatory: it's a clean snapshot of the mainstream practitioner
VWAP-and-volume-profile toolkit, and our null shelf already covers most of it.
Two micro-gaps worth noting (IB mid not drawn; no monthly anchor), one
methodological catch (his "retest" signal is our touch-bar artifact by
definition), and one untested-but-low-prior variant (composite multi-session
LVNs).

---

## 1. Claim inventory → repo status

| # | Claim / feature (timestamp) | Repo status | Verdict |
|---|---|---|---|
| 1 | **Initial Balance** = first 60 min RTH high/low; 50%/100%/200% extensions + IB mid as measured moves; all four core setups play out on IB levels (00:06:29–00:12:56) | **Built** — `sim/ib.py`, drawn on every sim/Lab chart (backlog §4). `EXT_MILESTONES = (0.5, 1.0, 1.5, 2.0)`; chart guides draw 1×/1.5×/2× | Same construct. Edge already tested: **IB-breakout dead**, stop-enforced 5m ORB is the survivor ([initial-balance-orb](initial-balance-orb.md), [strategy-scouting-2026-07](strategy-scouting-2026-07.md) §2) |
| 2 | **IB size vs trailing 20 sessions** → big/medium/small, forecasts today's range/volatility (00:08:48) | We compute `ib_vs_adr` (IB range ÷ ADR14) and `ib_pct_of_day` — same idea, different normalizer | Covered. The narrow-IB filter **reversed locally** in the scouting pass; don't re-open on a demo's say-so |
| 3 | **Candle coloring by σ band**: inside ±1σ = balance/chop, >+1σ = price discovery, >+2σ = extreme (00:13:53–00:18:29) | This *is* the repo's framework (upper-band-bounce family, regime classifier) | Confirms the taxonomy is mainstream. Nothing to build. Note [atr-vwap-band](atr-vwap-band.md): intraday ATR is the band renamed (ρ .96) |
| 4 | **Multi-TF VWAP** — intraday + weekly + **monthly**, plus a dashboard reading bull/bear/balanced per anchor (00:19:18–00:24:40) | Weekly **built** ([weekly-vwap](weekly-vwap.md)); dashboard ≈ our regime classifier. **Monthly anchor does not exist** | The one genuinely unbuilt item. Prior is bad — see §2 |
| 5 | **Confluence stacking**: "monthly VWAP + weekly lower band in the same spot… excellent level to go long from" (00:21:28, 00:26:23) | Directly tested shape | **Refuted.** Stacked-ref died in the v9 monthly-robustness pass; retest/acceptance/origin context all null in [weekly-vwap-context](weekly-vwap-context.md) |
| 6 | **Composite (N-session) volume profile → LVN levels**; thicker where several sessions' LVNs coincide; "price trades level to level" between them (00:28:06–00:40:30) | VP geometry is **0-for-5** here: [lvn-retrace-continuation](lvn-retrace-continuation.md), [structure-node-precheck](structure-node-precheck.md), [stable-level-sr](stable-level-sr.md), prior-POC magnet, [vah-snap-resistance](vah-snap-resistance.md) | Composite/multi-session LVNs are the one untested cell (ours were session/leg-anchored). **Low prior** — see §3 |
| 7 | **Retest / break signal definition**: "retest" = price crossed the level but the bar *closed back*; "break" = bar closed beyond (00:09:30, 00:52:29) | We have a standing screen for exactly this | **Methodological catch** — see §4 |
| 8 | **Volume climax candles** (">well over 125% of average volume") = short-term reversal, and each one **anchors a VWAP** that shows who's in control until the next climax (00:40:30–00:44:47, 01:06:57) | Event-anchored VWAP resolved null ([anchored-vwap-reclaim](anchored-vwap-reclaim.md)); absorption/exhaustion dead ([bigtrade-orderflow](bigtrade-orderflow-30badf94.html), loser order-flow) | Predicted null, and the threshold is weak — see §5 |
| 9 | Auction-theory framing: LVN ≈ "fair value gap", price rotates between HVNs until it materially leaves value (00:30:35) | Same theory the repo already encodes | No research content |
| 10 | **Volume profile "Row Size" setting** exists in the TV indicator (00:28:06) | Backlog §6 open item | Video adds nothing §6 doesn't already state in more detail |
| 11 | Misc: TV futures IB must be set 8:30–9:30 (CME session offset quirk); VWAP source = OHLC4; doesn't work on range/Renko; monthly VWAP identical across timeframes (00:07:12, 01:00:40, 01:05:54) | Our VWAP is computed from **tick prices** (`sim/vwap.py`, `ticks["price"]`), not bar typical price | We're strictly more accurate than OHLC4-on-bars; `vwap.py`'s docstring already flags that the two produce different numbers. No action |

---

## 2. The monthly anchor — the only unbuilt piece, and why I'd skip it

The toolkit's one construct we don't have is a **monthly-anchored VWAP** (plus a
monthly band). Adding it is cheap: `weekly.py` already solved seeded
accumulation off per-session sums, honest-absence, and the chart layer, so a
monthly sibling is mostly a re-anchor.

Three independent results argue it won't pay:

1. **The weekly anchor's own gate failed its A/B** (`wk_ext`, net −3.7%, Sharpe
   worse). A slower anchor is unlikely to do better than the faster one that
   already failed.
2. **Static/slow reference levels were near-dead OOS.** The drift-fade
   entry-reason study found that *dropping* the static refs
   (`use_session_refs=false` — Open/ONH/ONL/pd\*) **replicated out of sample** and
   improved PF/DD/expectancy. A monthly VWAP is the most static ref available.
3. **Stacked-ref confluence — the exact use he demos — already died** in the
   monthly-robustness pass.

**Recommendation:** don't build it as a signal. If it goes in at all, it goes in
as a *chart-parity* layer only (matching what ATAS/TV users see), explicitly
with no gate and no efficacy claim — the same footing as the default-off IB
extension guides.

## 3. Composite LVNs — the one untested cell

Our LVN work used **session-scoped and causal leg-anchored** profiles. The video
uses a **composite over the last 5–10 sessions**, and treats levels where
multiple sessions' LVNs coincide as higher-conviction. That specific variant has
not been raced here.

Prior is low: five separate VP-geometry tests have come back null, and the
closest analogue (`structure_node_precheck.py`) found node percentile
*collinear with retest count* and the sign **refuted** — LVN breaks netted
*worse*. Note also that a composite profile is a day-range-scale object, which
is what killed the prior-POC magnet hypothesis (day-scale level vs intraday-scale
move).

If it's ever run, the cheap form is: extend `data/research/lvn-retrace/lvn_causal.py`
to build an N-session composite, reuse the **random-pullback null** already
written there, and require the composite version to beat the null the
session-scoped one failed to beat. One afternoon, not a project. It sits below
the ORB entry-time trend proxy in the queue.

## 4. Methodological catch — his "retest" is our touch-bar artifact

His retest/break signal is defined as *"the close price traveled across the
level but the close price is now back below it"* (00:54:01). That is,
verbatim, the construct [structure-orderflow](structure-orderflow.md) identified
as manufacturing a fake edge: **"absorption → reject" was a touch-bar
close-back artifact**, and [weekly-vwap-context](weekly-vwap-context.md)
reached the same conclusion independently (scoring the touch bar manufactured
retest/acceptance/origin edges that vanished once the touch bar was excluded).

A "retest" label of this shape is *guaranteed* to correlate with a wick-reject
appearance, because it's selected on the close-back. Anyone racing it must
**exclude the touch bar from the outcome window**, or the result is
pre-determined. This is the standing anchor-bar screen — worth restating because
the video sells the signal as a discovery.

## 5. Volume climax → anchored VWAP

Two claims, both predicted null by existing work:

- **"Climax bar = short-term reversal."** Our order-flow studies killed
  absorption and exhaustion at every live anchor, on both winners and losers
  (`bigtrade-orderflow`, `loser-orderflow`): winners don't make a low to fight
  at, and stops **die of drift, not capitulation**. Also, the stated threshold —
  *"well over 125% of the average volume"* (01:07:52) — is a weak filter, not a
  rare event; a bar at 1.25× average volume is common, so "capitulation" here
  is far more frequent than the word implies.
- **"Anchor a VWAP at each climax."** [anchored-vwap-reclaim](anchored-vwap-reclaim.md)
  tested event-anchored VWAP (pdl + first-swing, ~2,900 reclaims) and never beat
  a random-long null — and crucially found the **cross-null edge flips sign with
  the anchor choice**. A third anchor is a third draw from a distribution
  centered on zero. VWAP geometry joined VP geometry on the no-edge shelf.

The climax-bar reversal claim is the cheapest thing in the video to falsify
(the `_Flow` tape extractor and big-lot machinery already exist), but it's a
low-prior confirm-the-null, not a lead.

---

## 6. Actionable residue

Small and honest:

- **Draw the IB mid.** `session_row` already computes `ib_mid`; `chart_overlay`
  doesn't emit it. The presenter treats IB mid as a VWAP-equivalent bounce level
  (00:12:02) and it's a platform convention. Free to add, default-off toggle, no
  efficacy claim — same footing as the extension guides. *(Not done here — flagged
  as a candidate, not silently built.)*
- **Monthly VWAP** — parity-only if wanted, never as a gate (§2).
- **Nothing else.** No new study, no knob, no strategy.

**Queue impact:** none. Backlog §6 (VP row binning) is unaffected; the video
confirms TradingView exposes a Row Size setting but doesn't state the default
convention any more precisely than §6 already does.
