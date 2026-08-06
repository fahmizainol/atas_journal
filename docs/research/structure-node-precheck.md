# Structure breaks × volume nodes — pre-check (resolved null)

**Question.** The one untested cell of the market-structure × HVN/LVN combo:
does a BOS/CHoCH whose break level sits in a *thin* (LVN) region of the
developing intraday profile follow through farther than one breaking through a
*heavy* (HVN) shelf? ("Thin = no resistance, the break should run.")

Both parents were individually null going in — structure breaks carry no
forward edge at any swing scale ([market-structure-events](market-structure-events.md)),
and VP geometry has lost every direct test
([lvn-retrace-continuation](lvn-retrace-continuation.md),
[stable-level-sr](stable-level-sr.md), prior-POC magnet, VAH snap). This scan
raced the interaction. **It is a pre-check, not a build** — no knob, no gate,
no strategy.

## Method

- Events: `structure_events.py` machine re-run per session at swing thresholds
  **5 / 10 / 20 pt** on the same 1-min RTH bars (363 sessions, ~59k break rows;
  22.6k / 18.9k / 11.2k after maturity filter).
- Node reading (causal): cumulative tick histogram **as of the end of bar
  t−1** — the break bar's own volume never colours its node. Break level's
  smoothed volume (±2-tick primary, ±6-tick robustness) ranked as a percentile
  of all levels in the visited span. Q1 (≤20th pctl) = LVN, Q5 (≥80th) = HVN.
- Guardrails: maturity filter (bar ≥ 30), outcomes are the parquet's
  t+1..t+20 window (break bar excluded), primary stat raced against **2,000
  within-session permutations** of node percentile, split-half by calendar,
  retest-count covariate, direction-mix check.

Extractor / analysis: `data/research/market-structure/structure_node_precheck.py`,
`structure_node_analyze.py`; rows in `structure_node_events.parquet`.

## Result: null — and the sign points the *wrong way*

| thr | LVN net | HVN net | LVN−HVN gap | perm p | alt-smooth p | win% range |
|----:|--------:|--------:|------------:|-------:|-------------:|-----------:|
|  5  | −2.12   | +0.69   | **−2.75**   | .043   | .118         | 48.4–50.7  |
| 10  | −1.57   | +0.30   | −1.82       | .308   | .398         | 47.7–50.3  |
| 20  | −3.50   | +0.61   | −4.04       | .128   | .317         | 48.6–50.8  |

- **The hypothesis is refuted, not just unsupported.** LVN breaks net *worse*
  than HVN breaks at every threshold, consistently in both split-halves, both
  BOS and CHoCH, both break directions (direction mix is 50–52% in both cells,
  so it's not a drift artifact).
- **"Travels farther" is real but symmetric.** LVN breaks do post bigger MFE
  (+9.2 pts, perm p < .0005 at thr 5) — and bigger MAE by just as much
  (43.4 vs 31.2). Thin zones are simply higher-volatility territory; the move
  runs farther in *both* directions and nets ≈ nothing. Win rate is a coin
  flip (48–51%) in every cell.
- **The node reading is near-collinear with retest count** — the anticipated
  confound. Touches 0–2 is essentially all-LVN, touches 16+ essentially
  all-HVN; the only overlapping bucket (7–15 touches) shrinks the gap
  (−1.4 / −0.0 / −5.1). "LVN at break" mostly means "fresh, barely revisited
  level," so even the small negative gap can't be attributed to volume
  structure per se.
- The modest negative net gap itself doesn't clear the bar: 2–4 pts against
  35–50 pt excursions, significant only at one threshold with one smoothing
  (p = .043 → .118 under the ±6-tick reading).

## Verdict

**Do not build.** No break strategy, no node gate, no HVN/LVN filter on
structure events. The combo family joins its parents: the only real content is
"thin profile ⇒ more volatility both ways," which is a volatility descriptor,
not an edge. This also mechanically explains why the LVN-retrace continuation
study found big-lot triggers made things *worse* — thin zones amplify
excursion without biasing direction.

Reusable: the causal cumulative-histogram node reader in
`structure_node_precheck.py` (percentile-vs-visited-span, two smoothings,
end-of-prior-bar cutoff) is a clean pattern for any future
"what node is price at" feature.
