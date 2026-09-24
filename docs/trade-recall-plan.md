# Trade recall — build plan

> **Superseded 2026-08-20 by `docs/trade-grading-plan.md`.** The thesis question
> this deck re-asked no longer exists, so nothing here is live: the deck is now
> real SM-2 over reviewed trades, self-rated, with the front bounded at a
> jittered cut. Kept for the reasoning about the three populations, which is why
> the new deck still refuses to score anything.

*Written 2026-08-19, brainstormed and decided in one sitting. An Anki-shaped
review loop over the trades already journaled and claimed: the card is one of
your own trades frozen at the moment of entry, blind; the answer is the same
thesis the ticket asks; the reveal is what the tape did, what you did, and what
you said at the time.*

*Read [`docs/review-revamp-plan.md`](review-revamp-plan.md) first if the thesis
system is unclear — this mode is a consumer of `trade_intent` and changes
nothing about how claims are captured.*

---

## Why this exists

The review answers "what was that trade" once, days after the fact, with the
outcome on the screen. Nothing in the app ever asks the question again — so
whether the lesson *took* is unknown. Drill mode tests recognition on random
days, but random days almost never contain your setups; the one corpus that is
100% setups, by construction, is your own trade log.

Recall mode closes that loop: it re-asks the ticket's own question — *bounce,
break, or revert? and would you take it?* — on your own past entries, blind,
and compares the answer to the claim on record.

## Why this is not Anki

Anki repeats a card until recall is easy. A chart card cannot be repeated: the
second or third exposure you are no longer reading structure, you are
remembering the day ("that's the one that dumped"). So:

- **v0 has no scheduler.** Ordering is unseen-first, random within; seen cards
  sink to the bottom, least-recently-shown first. A card *can* be re-answered,
  and every answer is kept, but nothing pretends re-answering a recognized
  chart measures reading.
- The real spaced-repetition unit, if the reps prove useful, is the **setup
  family** (miss → a *different unseen* trade from the same family comes
  sooner) and possibly spawned plain-text lesson cards. Deliberately deferred:
  the prototype's job is to find out whether the reps are worth anything, not
  to get the scheduler right.

---

## The card

**Front (blind):** the trade's own session tape, drawn from the session start
to a hard freeze at the entry instant, on the same causal replay engine the
journal day-replay uses. The date is masked (`hideDates`, drill's own
mechanism), the symbol is shown as its root only (`NQ`, not `NQH5` — the month
letter dates the chart). The entry itself is shown as an open position line —
side, size, price — because the thesis is a claim *about this fill*: without
the entry the question "bounce or break?" has no referent.

**Prompt:** the ticket's vocabulary, served by `GET /intent/vocab` exactly as
the ticket serves it — bounce / break / revert (+ target family), plus
**take / skip**: knowing only what the frozen chart shows, is this a trade?

**Back (reveal):** the tape runs forward past the exit; the full trade mark
(entry→exit, P&L) replaces the position line; the date and contract unmask.
Beside the chart:

- the claim on record, labelled with its `captured` stamp (entry / review),
- the machine's grade of that claim (`intent.grade` — confirmed / refuted /
  incoherent / unlocatable / unmeasured, plus the setup axis),
- the machine's grade of **your recall answer**, same grader, same
  measurements,
- agreement: did blind-you say what the record says.

---

## The decisions, and why

**D1 — the deck is every trade with a claim, not just entry-captured ones.**
The at-ticket thesis shipped 2026-08-18; as of this writing the split is 1
entry-captured to 44 review-captured. An entry-only deck would hold one card.
Review-captured claims are admissible here because of what they are: the
thesis *chosen knowing the outcome* — i.e. the reviewed lesson, the thing the
user picked as the truth source for scoring. The reveal labels which kind the
recorded claim is, and every aggregate splits on `captured` — the never-pool
rule (`db.trade_intent` docstring) applies to this reader like every other.

**D2 — the recall answer is scored two ways, stored zero ways.** Agreement
(recall thesis == recorded thesis, and target family where it applies) is the
headline — that is "did the lesson take". The tape verdict of the recall claim
is computed with the *same* `intent.grade` over the same stored measurements
and shown as context, never as the headline: single-trade outcomes are noise,
and a recall loop that scores on them trains outcome-chasing. Neither verdict
is stored — `recall_reviews` keeps only the answer, matching the rule
`trade_intent` sets for itself: judgements are recomputed on read so
thresholds can be retuned without a table of stale verdicts.

**D3 — recall answers are a third population.** `captured` already separates
entry-time from review-time claims because pooling them launders hindsight.
Recall answers are a third thing: blind, but *after* the review, possibly
after several exposures. They live in their own table (`recall_reviews`) and
are never written into `trade_intent` — there is no code path from this page
to that table, for the same reason review mode keeps the recorder unarmed
rather than armed-but-refusing.

**D4 — no recorder, no fills, no account.** The card page never calls
`useReplayAttempt` and mounts no ticket. `DayReplayer` is the precedent: same
engine, zero write paths. The chart is read-only (`canPlaceOrders` off).

**D5 — the freeze is the entry instant.** Ticks up to and including the fill
were visible to past-you; everything after is the answer. The masked axis is
part of the same rule: a visible date is a lookup key into your own memory.
The price scale still shows the level (~a few-month era hint) — accepted,
drill accepts the same.

**D6 — Lab page, scope-free.** Placement per the brainstorm: prototype in the
Lab (`/recall`), not a fourth /charts tab. Lab pages ignore the FilterBar;
card lookup is by `trade_key` (globally unique content hash), same reasoning
as `GET /trades/context`.

---

## Build

### Backend

- **`db.py`**: `recall_reviews` table in `SCHEMA` (append-only; `id` PK,
  `trade_key`, `shown_at`, `thesis`, `target_family`, `take`). Helpers
  `add_recall_review`, `recall_seen` (per-trade last shown + count + last
  answer). New table via `CREATE TABLE IF NOT EXISTS` — no migration needed.
- **`api/routers/recall.py`**:
  - `GET /recall/deck` — trades having a `trade_intent` row joined to the
    scope frame (`filtered_all`, default scope) and a `trade_context` row
    (which carries the resolved tape contract — the ATAS-label roll trap means
    the journal's own `instrument` must never be used to fetch the tape).
    Returns per card: `trade_key`, session `date`, resolved `symbol` + root,
    entry/exit local stamps, side, size, avg prices, `net_pnl`, recorded
    `captured`, seen-state. **No grading on this path** — grading reads minute
    bars per trade and the deck must load instantly (the same rule that keeps
    `with_grade=False` on the review gate).
  - `POST /recall/answer` — body `{trade_key, thesis, target_family?, take}`,
    vocabulary-validated at the door exactly as `PUT /intent` is. Writes one
    `recall_reviews` row, then computes and returns both grades (recorded
    claim and recall claim) off one excursion read, plus agreement.
  - Mounted in `api/main.py` like every other router.

### Frontend

- `workspaces.ts`: `{ to: "/recall", label: "Recall" }` in the Lab tabs.
- `router.tsx`: lazy `Recall` page at `/recall`.
- `hooks/useRecall.ts`: deck query + answer mutation; vocab via the existing
  `/intent/vocab` endpoint.
- `pages/Recall.tsx`: deck state (pick random unseen → front → answered →
  reveal → next), the thesis/take form, the reveal panel.
- `components/charts/RecallCard.tsx`: the slim chart panel — a
  `DayReplayer`-shaped component with everything trading and playback cut:
  `useSimulatorSession` (tape is the full contract; prices identical for
  micro days), `decodeTape` + `ReplayEngine`, `setSnapshot(snapshotTo(entryMs))`
  frozen front with `setPosition` for the entry, reveal =
  `snapshotTo(min(exitMs + 30min, session_end))` + `setTrades` with the real
  mark, plus a "rest of day" snapshot button. `hideDates` until revealed.
  The journal's local stamps are tz-aware and the tape clock is display-zone
  wall time — reuse `DayReplayer`'s drop-the-offset `localMs`, not
  `Date.parse`.

### Verification

- pytest: deck shape, answer round-trip (row written, grades returned,
  vocabulary refused at the door, `trade_intent` untouched), split-on-captured
  in any stat.
- Browser harness: a card renders blind (masked axis, no date string in the
  DOM), answering flips to reveal, fills mark appears. Route stubs
  method-before-path; two stacked canvases when reading pixels.

## What v0 does not do, on purpose

- No scheduler, no intervals, no leech stats — see "Why this is not Anki".
- No family-level aggregates yet: they need grades, grades need minute-bar
  reads per trade, and the first version of that read belongs in a background
  pass, not a page load.
- No lesson cards. If disagreement rows accumulate somewhere useful, that is
  the next thing to design — the miss *is* the raw material for one.
- Success criterion, so the prototype can fail honestly: after a few weeks,
  either recall-vs-record agreement trends up, or the misses cluster in
  families that match known leaks. Flat noise across families = the reps
  aren't teaching discrimination, and the idea dies here at Lab cost.
