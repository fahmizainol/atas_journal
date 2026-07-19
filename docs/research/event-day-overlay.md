# Event-Day Overlay — macro releases, earnings, and strategy performance

- **Date:** 2026-07-19 (first pass + same-day extension)
- **Research question:** Do scheduled macro releases (FOMC, CPI, NFP, PCE, GDP) or NQ-heavyweight earnings (NVDA/AAPL/MSFT/AMZN/META/GOOGL) line up with worse (or better) per-day performance for the sim strategies? Lab-backlog item 1.
- **Data:** current baseline runs (`data/sims/<slug>/<baseline_run_id>/trades.parquet`). Headline strategy vwap-upper-band-bounce uses the **v10 baseline `20250201-20260630-v10-0ae01934`** (398 trades, 2025-02-03 → 2026-06-30, 158 days). Event calendar verified against primary sources spans the full window — see §5.
- **History note:** a first pass ran on the v8 upper-band baseline (279 trades, window ending 2026-02-20). The baseline was switched to v10 mid-study; the Mar–Jun 2026 extension then acted as a natural out-of-sample test of the first-pass leads — and refuted them (§3). First-pass numbers are kept in §4 for the record.

---

## TL;DR — it's a null result, and a well-tested one

- **No significant macro event-day effect.** v10 upper-band-bounce: event days +0.091 R/trade vs +0.168 non-event; permutation **p = 0.58**. Win rate barely moves (69.2% vs 70.6%).
- **The in-sample "leads" died out of sample.** On the v8 window, NFP (−0.03) and GDP (−0.18) looked like soft spots and event+post-day softness reached p=0.088. Adding Mar–Jun 2026: NFP flipped to **+0.154**, CPI regressed to +0.107, event+post washed out to p=0.30. Classic tail-concentration noise (top-20 trades ≈ 100% of net makes any ~22%-of-days tag "look real" a third of the time).
- **No earnings effect either — if anything the opposite.** Post-earnings sessions (all six names report AMC, so the affected session is the *next* day): upper-band +0.208 R/trade, 76.7% win — slightly *better* than baseline. Report days (RTH before the AMC print) +0.100, fine.
- **Pre-event days are fine** (+0.116, 75% win on v10) — no "pre-event chop" story.
- **FOMC exposure barely exists.** Across the whole window the profitable strategies took only 3 FOMC-day trades, all entered before the 14:00 statement. Only the losing strategies (value-rotation, dev1-fade) ever traded post-statement.
- **Verdict: NO event gate, no event sizing, no stay-flat veto.** Backlog item 1's premise doesn't survive contact with the data at day granularity. One curiosity logged for later: value-rotation (dead money everywhere else) is positive on earnings-adjacent days (+0.51 R/trade post-earnings, n=26).

---

## 1. Method

- **Macro tags:** each session date tagged with any of {FOMC decision, CPI, NFP, PCE, GDP} from the verified calendar (§5). ~22% of trading days carry ≥1 tag. `pre` = last traded day before an event day; `post` = first traded day after; `between` = both (sandwiched); `clean` = none.
- **Earnings tags:** all six heavyweights reported AMC (16:00+ ET) on every date in the window, so `post-earnings` = first traded session after a report date (the gap/vol session), `report-day` = the RTH session before the print.
- **Join:** tags onto per-trade `session`; R and net PnL aggregated per tag.
- **Significance:** permutation tests (20k shuffles, two-sided) on mean R/trade differences.
- Thin Oct-2025-only strategies (orb-breakout, globex variants, lower-band) excluded — 4–9 days each, no power.

## 2. Results — current baselines

### Macro day-tags, vwap-upper-band-bounce v10 (n=398)

| tag | days | trades | R/trade | win% | net $ |
|---|---|---|---|---|---|
| clean | 69 | 203 | **+0.244** | 70.9 | +103,081 |
| pre | 28 | 64 | +0.116 | 75.0 | +14,074 |
| event | 30 | 65 | +0.091 | 69.2 | +10,647 |
| post | 25 | 51 | +0.052 | 66.7 | +3,776 |
| between | 6 | 15 | −0.250 | 60.0 | −9,075 |

Permutation: event-day vs rest **p = 0.579**; event+post vs rest **p = 0.304**. The clean-vs-tagged gradient exists but is exactly what tail concentration produces by chance; nothing survives testing.

### By macro event type, upper-band v10

| event | days | trades | R/trade | win% |
|---|---|---|---|---|
| FOMC | 2 | 3 | −0.310 | 66.7 |
| CPI | 10 | 17 | +0.107 | 70.6 |
| NFP | 10 | 27 | **+0.154** | 70.4 |
| PCE | 5 | 11 | +0.160 | 72.7 |
| GDP | 6 | 11 | −0.159 | 63.6 |

No type is reliably bad. GDP is the only residual negative and it's 11 trades.

### profile-pullback-long (n=101, window ends 2026-01)

clean +0.530 / pre −0.030 / event −0.112 / post +0.292 R/trade. Directionally soft on pre+event days but 14–18 trades per bucket — a single −1R day flips any of them. Not significant, not actionable.

### Earnings tags (current baselines)

| strategy | other R/trade | report-day | post-earnings |
|---|---|---|---|
| vwap-upper-band-bounce (v10) | +0.156 | +0.100 (33) | **+0.208** (30 trades, 76.7% win) |
| profile-pullback-long | +0.324 | −0.382 (4) | +0.667 (3) |
| value-rotation | −0.047 | +0.240 (15) | **+0.513** (26) |
| vwap-dev1-fade-short | −0.079 | +0.261 (7) | −0.001 (16) |

No damage anywhere; the AMC-report vol regime, if anything, suits these mean-reversion entries. NVDA specifically: 9 upper-band trades across its 4 post-earnings sessions, −0.203 R/trade — too thin to read.

### FOMC statement-time alignment

Upper-band v10 + profile-pullback have **zero** entries after 14:00 ET on FOMC days (3 + 3 trades, all pre-statement, mildly negative). Post-statement trading exists only in value-rotation (+0.36 R/trade, 12 trades) and dev1-fade (−0.37, 13). The one event that fires mid-session is one the profitable strategies structurally don't trade into — no exposure to manage.

## 3. Why the null is convincing

1. **Built-in out-of-sample test.** The first pass (v8, through Feb 2026) produced three leads: NFP soft (−0.03), GDP soft (−0.18), event+post softness at p=0.088. The Mar–Jun 2026 extension — data not seen when those hypotheses formed — flipped NFP positive, kept GDP negative-but-tiny, and washed event+post out to p=0.30. In-sample pattern, out-of-sample reversal: the same failure signature as the seven A/B'd order-flow knobs.
2. **Mechanism check fails.** If events were toxic, win rate should drop (more stops) or CPI — the biggest scheduled vol print — should be the worst bucket. Neither: win rates are flat across tags and CPI is positive in both samples.
3. **Tail concentration predicts exactly this.** With ~20 trades carrying ≈100% of net, a binary tag over ~22% of days shows a "large" R/trade gap by chance roughly a third of the time. Day-level tagging cannot beat that noise floor at n≈400.

## 4. First-pass numbers (v8 baseline, for the record)

Event vs clean, v8 window (2025-02-03 → 2026-02-20): upper-band +0.227 clean vs +0.060 event (p=0.294); profile-pullback +0.373 vs −0.112; losing strategies flat everywhere. By type: CPI +0.292, NFP −0.032, GDP −0.179, PCE +0.173, FOMC −0.310 (n=3). Pre/post cut: clean +0.291, pre +0.220, event +0.060, post +0.105, between −0.484; event+post vs rest −0.171, p=0.088. All of the negative leads regressed or reversed on the extended sample (§3).

## 5. Verified event calendar (2025-02 → 2026-06)

Cross-checked against BLS news-release archive permalinks, BEA schedule notices/embargo headers, federalreserve.gov, company IR pages, and SEC 8-Ks. The late-2025 government shutdown (Oct 1–Nov 12) delayed several releases and **canceled two outright** (no Oct-2025-reference CPI; no standalone Oct-2025 jobs report — Oct payrolls folded into the Dec 16 release). Dates below are *actual* release dates. A brief Jan 31–Feb 4 2026 lapse pushed Jan-ref prints, and the backlog created double-release days through April 2026.

- **FOMC decision days (14:00 ET):** 2025-01-29, 2025-03-19, 2025-05-07, 2025-06-18, 2025-07-30, 2025-09-17, 2025-10-29, 2025-12-10, 2026-01-28, 2026-03-18, 2026-04-29, 2026-06-17
- **CPI (08:30 ET):** 2025-02-12, 2025-03-12, 2025-04-10, 2025-05-13, 2025-06-11, 2025-07-15, 2025-08-12, 2025-09-11, 2025-10-24 (delayed from Oct 15), 2025-12-18 (delayed from Dec 10; Oct-ref canceled), 2026-01-13, 2026-02-13 (delayed from Feb 11), 2026-03-11, 2026-04-10, 2026-05-12, 2026-06-10
- **NFP / Employment Situation (08:30 ET):** 2025-02-07, 2025-03-07, 2025-04-04, 2025-05-02, 2025-06-06, 2025-07-03, 2025-08-01, 2025-09-05, 2025-11-20 (Sept-ref, delayed from Oct 3), 2025-12-16 (Nov-ref + Oct payrolls), 2026-01-09, 2026-02-11 (delayed; incl. benchmark revision), 2026-03-06, 2026-04-03, 2026-05-08 (second Friday), 2026-06-05
- **PCE (08:30 ET unless noted):** 2025-02-28, 2025-03-28, 2025-04-30, 2025-05-30, 2025-06-27, 2025-07-31, 2025-08-29, 2025-09-26, 2025-12-05 (10:00 ET), 2026-01-22 (Oct+Nov combined, 10:00 ET), 2026-02-20, 2026-03-13 (Jan-ref, delayed), 2026-04-09 (Feb-ref, delayed), 2026-04-30, 2026-05-28, 2026-06-25
- **GDP (08:30 ET):** 2025-02-27, 2025-03-27, 2025-04-30, 2025-05-29, 2025-06-26, 2025-07-30, 2025-08-28, 2025-09-25, 2025-12-23 (Q3 "initial" — replaced advance+second), 2026-01-22 (Q3 "updated"), 2026-02-20 (Q4 advance), 2026-03-13 (Q4 second), 2026-04-09 (Q4 third), 2026-04-30 (Q1'26 advance), 2026-05-28 (Q1 second), 2026-06-25 (Q1 third)
- **Earnings (all AMC — affected session is the NEXT trading day):**
  - NVDA: 2025-02-26, 2025-05-28, 2025-08-27, 2025-11-19, 2026-02-25, 2026-05-20
  - AAPL: 2025-05-01, 2025-07-31, 2025-10-30, 2026-01-29, 2026-04-30
  - MSFT: 2025-04-30, 2025-07-30, 2025-10-29, 2026-01-28, 2026-04-29
  - AMZN: 2025-02-06, 2025-05-01, 2025-07-31, 2025-10-30, 2026-02-05, 2026-04-29
  - META: 2025-04-30, 2025-07-30, 2025-10-29, 2026-01-28, 2026-04-29
  - GOOGL: 2025-02-04, 2025-04-24, 2025-07-23, 2025-10-29, 2026-02-04, 2026-04-29

Notable cluster days: 2025-10-29 (FOMC + MSFT/META/GOOGL earnings), 2026-04-29 (FOMC + MSFT/AMZN/META/GOOGL — the quadruple), 2026-04-30 (PCE + GDP + AAPL earnings), 2025-07-30 (FOMC + GDP + MSFT/META).

## 6. Disposition of backlog item 1

- [x] Event calendar assembled (macro + earnings, verified, above).
- [x] Baseline edge measured conditioned on event tags (day-of, pre, post, earnings, FOMC-time).
- [x] Decision: **neither a filter nor a signal.** No gate, no sizing knob, no Lab session-tag feature — building UI for a proven null would be clutter. The calendar in §5 is the reusable artifact if this is ever revisited.
- Revisit triggers (both low-prior): value-rotation's earnings-adjacent positivity (+0.51 R/trade, n=26) if that strategy ever comes back; GDP-day softness if it persists past ~25 trades.
