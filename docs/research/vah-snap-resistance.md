# VAH snap-above-price in the upper band: resistance / downtrend start?

**Question** (discretionary observation): when the NY or Globex developing VAH
violently relocates upward past price while price sits inside the Globex upper
band (dev1–dev2), does that VAH act as high resistance and mark the start of a
downtrend?

**Verdict: NO as a general rule — the snap is acceptance, not rejection.** The
newly relocated VAH is broken within the next hour in the large majority of
cases, forward drift after a snap is flat-to-positive, and snap violence has
zero correlation with forward returns. One narrow cohort (violent NY-VAH snaps
in the afternoon) leans the user's way but is one nominally significant cell
out of ~30 cuts — a weak lead, not a finding.

## Design

`data/research/market-structure/vah_snap_study.py` +
`analyze_vah_snap.py`. All causal (`levels_in_force`, the engine's own
reading; Globex-anchored `vwap.vwap_bands` — the same bands the drift-fade
engine trades). 360 sessions, 2025-02-03 → 2026-06-30, 1-minute grid.

- **Event**: developing VAH relocates upward (≥1t move, guarding out
  price-falls-through-static-VAH pullbacks) and crosses from below price to
  above price, while price is inside [dev1, dev2]. Both VAH sources: Globex
  (full ON+RTH profile) and NY (RTH-anchored profile). 30-min dedup.
  1,529 events (427 gx-RTH, 554 ny-RTH, 548 gx-overnight).
- **Baseline**: every 5th in-band minute, no VAH condition (n≈26k).
- **Outcomes**: fwd 15/30/60m + EOD (ticks, signed), broke-VAH-within-60m
  (traded ≥ VAH+2t), retest-reject (came within 8t then printed ≥20t below
  without breaking).

## Results

| cohort (RTH) | n | fwd 30m | fwd 60m | broke VAH ≤60m | retest-reject |
|---|---|---|---|---|---|
| in-band baseline | 8,000 | +6.9 | +11.0 | 0.88 | 0.01 |
| gx snaps, all | 427 | +11.4 | +20.1 | 0.85 | 0.03 |
| gx snap ≥50t | 139 | +18.1 | +17.7 | 0.66 | 0.04 |
| ny snaps, all | 554 | +2.3 | +10.0 | 0.83 | 0.04 |
| ny snap5m ≥50t | 217 | −16.6 | −24.0 | 0.73 | 0.03 |
| ny violent + afternoon | 81 | −36.5 | −68.3* | — | — |

- **Resistance doesn't hold**: even the most violent snaps get broken within
  the hour 66–73% of the time (baseline 88%). Retest-reject — the actual
  "acted as resistance" shape — is 2–5% everywhere.
- **No violence dose-response**: Spearman snap1_t / snap5_t / lands-how-far
  vs fwd_60m all ρ ≈ 0 (p ≥ 0.49, n=981).
- **The snap is bullish context, not bearish**: baseline minutes where the gx
  VAH is *already above* price drift +24t/60m vs +3t when VAH is below.
  Volume being accepted at the highs is trend-day behavior — same mechanism
  as the acceptance-consumption and developing-vs-static findings.
- **The one lean**: NY-VAH snap5m ≥50t in the afternoon: −68t/60m, p=0.029,
  median −18t, both time-halves negative (−16/−32 for the broader violent
  cohort). But p=0.029 across ~30 examined cells is multiple-comparisons
  fodder; prior scorecard discipline says treat as a low-prior lead only.
- **Simpson's warning for eyeballing charts**: pooled in-band drift is
  positive (+11t/60m) but the per-session mean is deeply negative (−129t) —
  sessions that only *briefly* poke into the upper band are collapses, while
  sessions that live there are melt-ups. Anecdotal chart memory samples the
  brief-visit days and will over-remember the snap-then-dump pattern.

## Files

- `data/research/market-structure/vah_snap_study.py` — extractor
- `data/research/market-structure/analyze_vah_snap.py` — analysis
- `vah_snap_events.parquet` / `vah_snap_baseline.parquet` — data
