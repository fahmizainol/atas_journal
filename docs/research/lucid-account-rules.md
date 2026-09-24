# Lucid account rules and pricing, read off the live site

**Date:** 2026-08-25
**Source:** the pricing carousel's rendered DOM, captured by the user tab by tab.
This is a **primary source** — it is what the checkout will actually charge and
what the dashboard will actually enforce — and it supersedes every third-party
write-up used earlier in
[`bracket-survival.md`](bracket-survival.md) §15–16, several of which were wrong.
**Prices are a live promotion and will move; the rules move too (Lucid changed them
in Nov 2025, Feb 2026 and Jul 2026). Re-capture before spending money.**

**Coverage:** LucidPro, LucidDaily and LucidFlex are captured in full — rules and
prices, evaluation and funded, all four sizes. **Only LucidDirect is missing.** What
the cards do *not* show for any product is the payout machinery: buffer balances,
per-payout caps and cycle mechanics appear nowhere, so those remain on third-party
authority and are flagged individually in §4 and §5.

**Cheapest evaluation per size (with coupon):** Flex at 25K ($50.30); **Daily at 50K,
100K and 150K** ($76.60 / $157.40 / $221.60).

---

## 1. LucidPro

Single-phase evaluation, EOD trailing drawdown, no consistency rule in the
evaluation, 40% in funded.

### Evaluation

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Profit target | **$1,250** | $3,000 | $6,000 | $9,000 |
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| Drawdown type | EOD | EOD | EOD | EOD |
| Daily loss limit | $600 or OFF | $1,200 or OFF | $1,800 or OFF | $2,700 or OFF |
| Max size | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |
| List | $123 | $192 | $307 | $410 |
| Sale | $90.60 | $140.40 | $225.40 | $300.50 |
| **With coupon** | **$70.60** | **$115.40** | **$180.40** | **$245.50** |
| Reset fee | $70.00 | $115.00 | $180.00 | $245.00 |
| Effective discount | 42.6% | 39.9% | 41.2% | 40.1% |

Activation fee FREE at every size; one-day pass allowed; dashboard realtime.

### Funded

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Payout profit target | $250 | $500 | $750 | $1,000 |
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| DLL (below initial trail) | $600 | $1,200 | $1,800 | $2,700 |
| **LucidScale DLL** (above trail) | 60% of peak EOD balance | ← | ← | ← |
| Consistency | 40% | 40% | 40% | 40% |
| Max size | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |
| Days to payout | 3 | 3 | 3 | 3 |
| Scaling plan | — | — | — | — |
| Payouts to live | 5 | 5 | 5 | 5 |

---

## 2. LucidDaily

Intraday trailing drawdown once funded, **always** — the eval's drawdown is a paid
choice but the funded account is intraday regardless. 50% consistency in the
evaluation, none in funded.

**Two checkout toggles, and they are not the same kind of decision.** The **eval
drawdown** (EOD or intraday) does *not* carry into funded, so it is a pure one-time
purchase of pass probability — **intraday is the cheaper side**, EOD costs more and
buys an easier evaluation. The **daily loss limit** choice *does* carry into funded,
so it is a permanent rule change. Under a −$500 self stop the DLL never binds
either way, which makes the drawdown toggle the only one worth deliberating.

### Evaluation

**The price depends on two toggles**, so these are *configured* prices, not a
like-for-like ladder — the capture had different toggles set on different cards.
The configuration each price belongs to is stated in its own row.

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Profit target | $1,250 | $3,000 | $6,000 | $9,000 |
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| Eval drawdown *(toggle)* | EOD / **Intraday** | EOD / **Intraday** | **EOD** / Intraday | **EOD** / Intraday |
| Daily loss limit *(toggle)* | ON / **OFF** | **$1,200** / OFF | **$1,800** / OFF | **$2,700** / OFF |
| Consistency | 50% | 50% | 50% | 50% |
| Max size | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |
| List | $115 | $156 | $314 | $436 |
| Sale | — | $101.60 | $202.40 | $281.60 |
| **With coupon** | **$75.00** | **$76.60** | **$157.40** | **$221.60** |
| Reset fee | $85.00 | $90.00 | $185.00 | $260.00 |
| Effective discount | 34.8% | **50.9%** | 49.9% | 49.2% |
| *priced configuration* | intraday, DLL off | intraday, DLL on | EOD, DLL on | EOD, DLL on |

### Funded

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| DLL | **NONE** | $1,200 | $1,800 | $2,700 |
| Drawdown type | Intraday | Intraday | Intraday | Intraday |
| Consistency | — | — | — | — |
| Max size | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |

Daily payouts ✓, no consistency in funded ✓, activation fee FREE.

---

## 3. LucidFlex

Single-phase evaluation, EOD trailing drawdown, **50% consistency in the evaluation
and none in funded**, and a funded **scaling plan** that gates contract size by
accumulated profit.

### Evaluation

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Profit target | $1,250 | $3,000 | $6,000 | $9,000 |
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| Drawdown type | EOD | EOD | EOD | EOD |
| Daily loss limit | $600 or OFF | $1,200 or OFF | $1,800 or OFF | $2,700 or OFF |
| Consistency | 50% | 50% | 50% | 50% |
| Max size | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |
| List | $89 | $146 | $293 | $407 |
| Sale | $65.30 | $105.20 | $215.60 | $295.40 |
| **With coupon** | **$50.30** | **$90.20** | **$170.60** | **$250.40** |
| Reset fee | $50.00 | $90.00 | $170.00 | $250.00 |
| Effective discount | 43.5% | 38.2% | 41.8% | 38.5% |

### Funded

| | 25K | 50K | 100K | 150K |
|---|---:|---:|---:|---:|
| Max loss limit | $1,000 | $2,000 | $3,000 | $4,500 |
| **DLL** | **$600** | **$1,200** | **$1,800** | **$2,700** |
| Consistency | — | — | — | — |
| Max size (ceiling) | 2 mini / 20 micro | 4 mini / 40 micro | 6 mini / 60 micro | 10 mini / 100 micro |
| **Days to payout** | **5** | **5** | **5** | **5** |
| Scaling plan | Yes | Yes | Yes | Yes |
| Payouts to live | 5 | 5 | 5 | 5 |

### The funded scaling plan

Contract size is gated by accumulated simulated profit and **starts at half the
account's ceiling**:

| profit | 25K | 50K | 100K | 150K |
|---|---|---|---|---|
| $0 – 999 | 1 mini / 10 micro | 2 mini / 20 micro | 3 mini / 30 micro | 4 mini / 40 micro |
| $1,000 – 1,999 | 2 mini / 20 micro | 3 mini / 30 micro | 4 mini / 40 micro | 5 mini / 50 micro |
| $2,000 – 2,999 | — | 4 mini / 40 micro | 5 mini / 50 micro | 6 mini / 60 micro |
| $3,000 – 4,499 | — | — | 6 mini / 60 micro | 8 mini / 80 micro |
| $4,500+ | — | — | — | 10 mini / 100 micro |

At the risk budgets this project uses (7–12% of max loss, filled in micros — 7 micros
at a 40-tick stop on a 50K) **the ladder never binds**, even at its lowest rung.

---

## 4. What this corrects in the earlier work

**The largest correction is about LucidFlex, and it goes against what this project
previously asserted.** [`bracket-survival.md`](bracket-survival.md) §15.1 states that
Flex "is not Pro with a longer cycle — it has no calendar cycle at all", and lists
five differences. The live card shows **"Days to Payout: 5"**, in exactly the field
where Pro shows **3**. So the original framing — Flex is Pro with a five-day payout
gate instead of three — is what the primary source supports, and the rebuttal was
built on a third-party write-up that has since been shown wrong about Daily's price
(by 33%) and about Flex's daily loss limit (claimed "none anywhere"; the card shows
$600–$2,700 in both eval and funded).

What survives of that §15.1 list, per the card: Flex's eval carries a **50%
consistency rule** where Pro's carries none, Flex's funded consistency is **—**
where Pro's is **40%**, and Flex has a **funded scaling plan** where Pro's field
reads "—". What is now **unverified** rather than refuted: the absent buffer, the
"five qualifying days at ≥$150", the 50%-of-cycle payout cap, and the floor-snap on
withdrawal. None of those appear on the pricing card — but neither does Pro's
buffer, so absence here is not evidence either way. They came from the same source
that got the other numbers wrong, so **treat them as unsupported until the Flex
funded-rules page is read directly.**

Beyond that, six things, three of them material.

1. **LucidDaily 50K costs $76.60, not the $115 I inferred — 33% less.** The
   inferred figure came from a reset fee and, embarrassingly, landed almost exactly
   on LucidPro 50K's real price. Daily is the **cheapest** 50K on the board, and it
   was already the best earner in
   [`bracket-survival.md`](bracket-survival.md) §16, so the recommendation there
   gets stronger, not weaker.
2. **The 25K profit target is $1,250, not $1,500** — 5% of the account, not 6%,
   on both Pro and Daily. Every 25K figure in §15.5 made the evaluation ~17% harder
   than it is and is therefore pessimistic.
3. **Daily's discount is ~50% at 50K and above**, against Pro's ~40%. The flat
   `--discount 0.40` used in §16 understates Daily's advantage.
4. **Resetting versus buying fresh now differs by product.** On **Pro they are the
   same price** ($70.00 vs $70.60, $115.00 vs $115.40) — the earlier claim that
   fresh is strictly cheaper no longer holds. On **Daily a fresh account is cheaper
   than a reset** at every size ($76.60 vs $90.00 at 50K), so the claim survives
   there. Either way there is no reason to stockpile *unstarted* evaluations.
5. **LucidDaily 25K funded has no daily loss limit at all**, and its eval DLL is
   optional. The 50K funded DLL is **$1,200**, which also corrects
   `replay_account.py`'s `lucid_daily` template, where it is $1,000. Nothing in the
   sweep depends on it — the −$500 self stop is tighter than either — but the
   template is wrong.
6. **Pro has no scaling plan** at any size (the field reads "—"). The funded
   contract ladder belongs to Flex, which remains uncaptured.

Two rules are confirmed as stated but still unmodelled anywhere: Pro's **LucidScale
DLL at 60% of peak EOD balance** once above the initial trail, and Daily's
**red-folder news breach**. Neither binds under a −$500 self stop.

---

## 5. What is still missing

- **Flex's payout mechanics.** The card gives its rules and prices, but the
  floor-snap trap, the five-qualifying-days gate and the 50%-of-cycle payout cap in
  [`bracket-survival.md`](bracket-survival.md) §15.1 come from a write-up now shown
  wrong elsewhere. The card shows no payout mechanics for *any* product, so it can
  neither confirm nor refute them — read the Flex funded-rules page directly.
- **LucidDirect**, not captured and not modelled anywhere.
- **Every product's buffer balance.** Only Daily's ($52,100 at 50K) is confirmed,
  by the help-centre page the user pasted. Pro's is third-party; Flex's supposed
  absence of one is third-party.
- **The price of each Daily toggle combination.** EOD-versus-intraday is a paid
  choice at 25K and 50K, and only one side of it was captured per card, so the cost
  of buying the easier evaluation cannot be read off this table.
- **Whether an unstarted evaluation expires**, and whether a funded account has an
  inactivity rule. Both matter to the reserve-account plan in §16 and neither is on
  the pricing page.
