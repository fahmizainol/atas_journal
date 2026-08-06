# Scouting-queue engine A/Bs — results (2026-07-27)

Three leads from `strategy-scouting-2026-07.md` / `lab-backlog.md` taken to real
engine runs in one pass. Verdicts up front:

| # | Lead | Verdict |
|---|------|---------|
| 1 | Earlier regime read (09:45 checkpoint) on the flagship | **FAILED** — net −22%, keep 10:30 |
| 2 | Lower-band mirror + `regime_mirror` habitat gate | **DEAD** — habitat cohort itself loses |
| 3 | `profile-pullback-long` 17-month promotion | **WEAK HOLD** — profitable but diluted (PF 1.25) |

All runs on cached ticks ($0 data), current working-tree engine. The 09:45 and
mirror knobs ship **off**.

---

## 1. Flagship early-regime read — FAILED

The scouting pass found 32 flagship trades slipping in before the 10:30 regime
checkpoint on days the checkpoint would have failed, netting −$15.7k — and
proposed an earlier read. The knob-expressible variant is moving the regime
gate's checkpoint to 09:45 (read *and* veto move together).

A/B on the exact v13 baseline config (`a348d176`), only
`regime.checkpoint: "10:30" → "09:45"` — run `99d8ce27`:

| metric | 10:30 (baseline) | 09:45 | Δ |
|---|---|---|---|
| net | $150,439 | $117,340 | **−$33,100 (−22%)** |
| trades | 262 | 227 | −35 |
| PF | 2.05 | 1.91 | −0.14 |
| Sharpe | 3.04 | 2.56 | −0.48 |
| maxDD | −$13,731 | −$13,665 | ~flat |

The 09:45 bbr is too noisy a read: it vetoes roughly twice as much good money
as the leak it plugs, and the blind-day doctrine (a 09:45 the artifact cannot
carry → veto) costs extra days on top. Twelfth confirmation of the house
lesson: **a static counterfactual (−$15.7k) is not an engine A/B (−$33.1k)**.

Not tested (would need a new knob, and now carries a much weaker prior): a
hybrid that vetoes 09:45→10:30 on a bad 09:45 read but lets the 10:30 read
govern the rest of the day. The −$15.7k pocket remains real but unharvested.

## 2. Lower-band mirror + regime_mirror gate — DEAD

Built the `regime_mirror` gate (this pass, `gates.py`): the long's regime gate
inverted — after the checkpoint, veto every entry on a day whose morning did
NOT live below both VWAPs. Knobs `bbr_min` (default 0.65, the scouting pass's
habitat boundary), `checkpoint`; same blind-day-vetoes doctrine. Registered on
`vwap-lower-band-bounce` only. No version bump (gate additions never touch the
base path; chop/wk_ext precedent).

Context the scouting doc predates: three ungated full-window (Feb 2025–Jun 2026)
lower-band runs completed 2026-07-19 and **all lose** — the failed-looking runs
in the folder were a contract typo (`NQZ5` over a 17-month window; use `NQ`).

| config | run | trades | net | PF | Sharpe |
|---|---|---|---|---|---|
| simple (stop 75 / rr 1) ungated | `33c7cd24` | 1,844 | −$14,426 | 0.96 | −0.71 |
| simple + regime_mirror 0.65 | `c0a23bd8` | 1,010 | −$5,995 | 0.97 | −0.39 |
| flagship-mirror (reenter on) ungated | `6194f827` | 746 | −$19,203 | 0.90 | −0.86 |
| flagship-mirror + regime_mirror 0.65 | `32bd25de` | 502 | −$29,831 | 0.80 | −1.54 |

Three findings, in order of importance:

- **The habitat cohort itself loses.** Inside the clean gated run (`c0a23bd8`),
  the post-10:30 trades — which by construction all live on bbr≥0.65 days, the
  76-session cohort the whole premise pointed at — net **−$3.9k over 314
  trades, 50.6% win**. The pre-10:30 book (all days) is also negative. The
  mirror isn't starved by the gate; its home turf doesn't pay. The day-with
  asymmetry runs deeper than regime selection: NQ's short-side band bounce has
  no habitat, full stop.
- **The gate × reenter interaction replicates the wk_ext lesson.** On the
  flagship-mirror config (`reenter_after_stop_only: true`) the same gate made
  things *worse* (−$19.2k → −$29.8k): vetoes break re-arm chains. Never A/B a
  veto gate on a reenter-enabled config and read it as the gate's verdict.
- The gate does behave as designed on the simple config (cuts losses 58%
  while removing 45% of trades) — it is a working regime detector pointed at a
  strategy with no edge to protect.

Disposition: `regime_mirror` ships available on `vwap-lower-band-bounce`,
**enabled: false**. Do not build further lower-band-short variants without a
new premise; scouting queue #2 is closed as resolved-negative.

## 3. profile-pullback-long 17-month promotion — WEAK HOLD

The 8-month baseline (`5092c2f1`, Jun 2025–Jan 2026: +$9.4k/101 trades,
PF 1.54, Sharpe 2.72) extended to Feb 2025–Jun 2026 unchanged — run
`ecf94b1c` (v5; the 101 overlapping trades are byte-identical to the v4
baseline, verified):

| metric | 8-mo baseline | 17-mo full window |
|---|---|---|
| net | $9,406 | $10,525 |
| trades | 101 | 215 |
| PF | 1.54 | 1.25 |
| Sharpe | 2.72 | 1.42 |
| maxDD | −$1,526 | −$2,548 |
| months positive | — | 12/17 |

The 9 out-of-baseline months add only **+$1.1k over 114 trades** (Feb–May 2025
−$0.8k, Feb–Jun 2026 +$1.9k). Halves balance ($6.8k / $6.7k) and drawdown
stays tiny, so this is a real strategy — but the 8-month PF 1.54 was partly
window selection; the honest full-window number is PF 1.25, ~$49/trade.

Next lever if pursued: gate support (regime / vwap_slope) does not exist on
this strategy's config at all — adding it is a build task, and the stack's
value should be judged against the PF 1.25 base, not the 8-month mirage.

---

## Run inventory

- `vwap-upper-band-bounce/20250201-20260630-v13-99d8ce27` — 09:45 checkpoint A/B
- `profile-pullback-long/20250201-20260630-v5-ecf94b1c` — 17-month promotion
- `vwap-lower-band-bounce/20250204-20260630-v10-32bd25de` — mirror gate on flagship-mirror config
- `vwap-lower-band-bounce/20250204-20260630-v10-c0a23bd8` — mirror gate on simple config

Code (working tree, uncommitted with the rest of the WIP): `RegimeMirrorGate`
in `src/journal/sim/gates.py`, registration in `confluences.py` + lower-band
`registry.py` entry, test in `tests/test_strategies.py` (66 pass).

Scoreboard update: engine A/Bs now stand at **1 pass (reenter_after_stop_only)
/ 12 fails**. The queue's remaining open items from the scouting pass: ORB
trend-proxy build (#1) and the Kelly sizing what-if (analysis-only).
