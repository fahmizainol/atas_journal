# Lucid spending — what the account has actually cost

**Date:** 2026-09-14
**Source:** the card statement, read off the user's banking app. Only rows reading
`Card transaction of <amount> USD` are Lucid purchases; the `Converted <MYR> to
<USD>` rows sitting between them are funding conversions into the card and are
**not** spending — counting them would roughly double the total.

**Total to date: $818.70 across 8 charges, 2026-05-25 → 2026-09-10.**

This is the cost side of the campaign. The equity side lives in the Accounts
page; the two have never been put in one place, so a life that "ended" there has
no price attached to it here. Pairing them is left undone deliberately — see
§ What's missing.

---

## The ledger

| Date | Amount | What it probably was |
|---|---:|---|
| 2026-05-25 | $70.00 | LucidPro 25K **reset fee** — exact match |
| 2026-06-04 | $13.00 | unknown — too small for any eval or reset |
| 2026-06-22 | $142.50 | unknown |
| 2026-06-22 | $199.50 | unknown |
| 2026-07-01 | $129.50 | unknown |
| 2026-07-07 | $111.00 | unknown |
| 2026-08-26 | $76.60 | LucidDaily 50K evaluation, with coupon — exact match |
| 2026-09-10 | $76.60 | LucidDaily 50K evaluation, with coupon — exact match |
| | **$818.70** | |

Three of eight tie to a line in
[`lucid-account-rules.md`](lucid-account-rules.md) exactly. The other five do
not, and that is expected rather than alarming: that price table was captured on
**2026-08-25**, and every unmatched charge predates it. Lucid's promotion moves —
the doc says so in its own opening — so May–July charges were bought at prices
the capture never saw. **The labels above marked "unknown" are not guesses worth
trusting; they are gaps.**

Two charges on 2026-06-22 ($142.50 and $199.50) landed the same day. That is
either two accounts bought together or a purchase plus an immediate reset.

## Shape of the spend

The two most recent charges are the cheapest evaluation Lucid sells at 50K
($76.60, LucidDaily with coupon), three weeks apart. Before that the charges ran
$111–$199.50 — meaningfully more per attempt. Whatever was bought in June and
July cost roughly **double** what the current route costs.

$818.70 is, for scale, about **1.6 of the LucidPro 50K daily loss limits**
($500 self-imposed) — i.e. the cost of the seat so far is under two bad days.
It is also 27% of one 50K profit target ($3,000).

## What's missing

- **Dates have no account attached.** The statement says what was paid and when,
  never which account id it bought. The campaign in `data/replays/account.json`
  knows when each epoch *started* — those timestamps could be matched against
  these dates to attribute cost per life, but the epochs there are seeded with
  throwaway test accounts and junk causes of death, so the join would be noise.
- **No payouts on this side.** This is outgoing only. Net cost of the campaign
  needs the incoming column too.
- **Not wired into the app.** `account_campaign.py` prices *scenarios* in P&L,
  not the account in dollars paid. Adding real spend to it is a build, not a
  doc edit.

**Append to the table above when you buy or reset.** Re-read the statement rather
than trusting memory — the two $76.60 charges are identical in every field but
the date.
