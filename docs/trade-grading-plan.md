# Trade grading — the review becomes a grade, and recall becomes a deck

2026-08-20. Supersedes `docs/review-revamp-plan.md` V14 (thesis + note) and all of
`docs/trade-recall-plan.md`. The change in one sentence: **a trade owes a grade,
the level it was traded off, and at least one tag — and the recall page becomes a
real spaced-repetition deck over those reviewed trades.**

Source of the grading idea: *"My Trade Grading System that Made Me $100M
(A,B,C,D)"* — TheOneLanceB, 2026-08-15. Its four parts are an A–D scale where D
means no-trade, one rubric per setup, grades that are subjective on purpose, and
a validation step months later that asks whether the A trades actually beat the
B trades. Decision G3 records which of those we are and are not buying.

## Decisions

- **G1 — the thesis is deleted, everywhere.** `bounce` / `break` / `revert` said
  where price was going, and long/short already says that. Out: `journal/intent.py`
  whole (the grader, its five verdicts, `anchor_level`), the `trade_intent` table,
  the ticket's `ThesisChip` and the `_record_intent` join behind it,
  `PUT /intent/{key}`, `GET /intent/vocab`, `ThesisBlock.tsx`, and the Recall page
  as built. `trade_intent` and `recall_reviews` stay on disk **unread** — 45 rows
  of answers to a question that no longer exists, dropped the same way the video
  tables were, which is to say not at all.

  One casualty worth naming because it is not covered by the reasoning above: the
  grader also computed an independent **`setup` axis** — whether the fill *faded*
  or *joined* the approach into it, off `trade_context.pre_run_pts_5m`. Direction
  does not encode that. The measurement is still stored, so it can come back
  whenever without re-measuring anything; it just stops being displayed.

- **G2 — what a trade owes is a grade, a watched level, and ≥1 tag.** The note
  becomes optional. All four fields are answered in one form and filed in one
  write.
  - **Grade** — `A|B|C|D`, closed vocabulary. `D` is a real answer, not an
    absence: the video's D means *don't take it*, so a D on a trade you took is
    the "shouldn't have been in this" verdict.
  - **Watched level** — one *or more* levels, picked from this trade's own
    measured candidates, or the exclusive `none`. A *level*, not a family: see
    G4. It took exactly one until 2026-08-22, which forced an arbitrary answer
    at exactly the moments that matter most — a fill where the globex POC and
    the weekly VWAP sat on the same price. Confluence is the ordinary reason a
    level gets traded, so the answer is a set. `none` stays exclusive: it is the
    only option that contradicts the others, and `PUT /notes` refuses the mix.
  - **Tags** — one or more, free-form, autocompleted from every tag ever used
    (`GET /notes/tags`). This is the diagnosis: `Oversized`,
    `Seller's absorption`. The vocabulary grows by being typed.
  - **Note** — optional free text, unchanged.

- **G3 — grading is review-only; the ticket asks nothing.** Recorded plainly
  because it costs something: the video's own validation step — *do my A trades
  outperform my B trades?* — **cannot ever run on these grades.** A grade
  assigned with the outcome on screen correlates with the outcome by
  construction, so the comparison would be measuring its own contamination. What
  a hindsight grade can still do is describe: which level families the D's
  cluster on, which tags ride with which grade, whether the C's were worth
  taking. If the A-vs-B question is wanted later it needs a blind capture at the
  ticket, which is additive (a `captured` column, the way `trade_intent` had one),
  not a rework.

- **G4 — the level pick is a choice, not a confirmation.** The schema note at
  `db.py:193` is the constraint: the tagger is machine-owned and the review gate
  deliberately does not read it, because auto-filling the human's side "would
  answer the question with its own guess and satisfy the review gate for free".
  Two rules keep that boundary while still offering candidates:
  - Candidates are ordered **by distance, never by the tagger's rank.** Distance
    is a fact about the chart; rank is the machine's opinion about how unusual
    that distance was. Sorting by rank puts the machine's answer at the top of
    the list.
  - Nothing is pre-selected, and `none` is always offered — so a trade whose tape
    was never cached is still answerable, and the gate can never be blocked by a
    missing measurement rather than a missing answer.

  This is deliberately **not** the `confluences` field deleted on 2026-08-18. That
  was a list of every level you remembered seeing, which could never be wrong.
  This is a single referent that can disagree with the nearest measured level —
  and that disagreement is the thing worth reading later.

  **The pick names a level, not a family (2026-08-21).** It first shipped
  family-scoped, because *ranking* is family-scoped and has to stay that way: NY,
  globex and weekly value areas run collinear, so a per-level rank splits one
  signal across three columns and calls the split a ranking. But a *pick* is not
  a rank — it is a name for the line you were watching, and "value high" cannot
  say whether that line was the globex VAH or the NY one. Since the tightest
  session level on a trade is globex or weekly about one time in three, the
  family label was silently merging three different reads.
  - `trade_levels` is keyed `(trade_key, anchor, member)` and stores **every**
    measured level, not just each family's winner — the row a pick refers to has
    to exist for it to be pickable at all, which is what "GX POC is missing"
    turned out to mean.
  - `rank` is nullable and stays **family-scoped**: it is filled on the member
    that won its family and left `NULL` on the rest. A `NULL` rank is "not
    scored", never "scored zero", and the ordering puts those last.
  - Every member carries a session-prefixed label (`GX VAH`, `NY VWAP`,
    `WK −1σ`), so the session is legible from the label alone with no legend.

- **G5 — storage is `trade_notes`, two new columns.** `grade TEXT` and
  `watched_levels_json TEXT` (a JSON array, like `tags_json` beside it; it was
  `watched_level TEXT` until 2026-08-22 and that column is migrated across then
  left unread, the way `watched_family` was), added through the
  `PRAGMA table_info` + `ALTER TABLE`
  path that column list already took twice (`db.py:502`). Not a table of its own:
  `put_intent` split itself off for two reasons, and both are now gone — the
  ticket no longer writes at a moment when there is no note to echo (G3), and the
  blanking hazard is handled by making these two fields **partial** on `NoteIn`
  (omitted means unchanged), so the journal form and the drill save never have to
  learn they exist. `NULL` = never answered; `'none'` = answered *no level*. One
  `PUT /notes/{key}` files a whole review.

- **G6 — the review panel is those four fields and nothing else.**
  - `ContextStrip` comes out — one line at `ReviewPanel.tsx:409`. `TradeDetail`
    keeps it untouched, which is where those numbers were wanted anyway.
  - The **flag verdicts come out of the review.** `oversized` and `rewind` are
    the only two flags left and they ask a different question (an account rule
    tripped), so the leak/justified buttons, the flag cards and the flag half of
    `review_is_complete` all go. Flags are still *raised* onto the attempt — they
    cost nothing and they are a fact about the sitting — they simply stop being a
    thing you must answer before filing.

    *Shipped in two halves, and the second was late.* The panel dropped the
    buttons on the day; the server kept `review_is_complete` and the
    `PATCH status=reviewed` check behind it until **2026-08-23**. In between, any
    sitting that tripped a rule could not be filed at all — the page had no way
    to send a verdict and the server would not take the status without one — so
    the 409 held, and with it the review block that gates the next sitting. The
    account was stuck. With the gate gone, `_raise_flags` also auto-passes a
    zero-trade sitting **whatever it was flagged for** (a rewind that erased
    every fill leaves nothing to grade), and `review_block` owes on booked
    trades alone.

- **G7 — Recall is rebuilt as a real SM-2 deck.**
  - **Deck** = every reviewed trade with a cached tape.
  - **Front** = the session from its open to a stored **jittered cut**: uniform in
    `[entry − 15m, entry + 10m]`, clamped to the session, chosen once and kept.
    Playable and scrubbable within `[open, cut]` and not past it. Nothing marks
    the fill — no position line, no trade mark, no direction, dates masked,
    root-only symbol. Sometimes the trade is still ahead of the cut and sometimes
    it already happened somewhere on screen, and you cannot tell which.
    *Superseded 2026-08-22:* the jitter went first (a cut minutes off the fill
    asks about a chart the setup has not formed on), and the stop-on-the-fill
    that replaced it went the same day. The front is now a fixed minute around
    the entry — `[entry − 30s, entry + 30s]`, clamped to the session — opening on
    its far side, scrubbable back through the whole session and not past that
    ceiling, with ▶ replaying the minute. The fill is still unmarked, so what the
    trade *was* stays blind; what this spends is the first half-minute of the
    answer, deliberately, because that half-minute is where a fill either goes
    with you or immediately does not.
  - **The front's payload carries no answer.** Direction, size, entry and exit,
    PnL and every review field are a *separate fetch* made on flip. Blindness
    that lives only in the UI is one devtools tab away from being no blindness at
    all.
  - **Guess** — optional free text, stored, never validated, never scored. This
    is the honour system, chosen knowingly: there is no objective right answer to
    "what does price do next", and a self-rated card is how Anki is used in
    practice.
  - **Back** = the same tape with the transport unlocked to the session end, the
    trade drawn, and the review shown — grade, watched level, tags, note.
  - **Rating** = Anki's four buttons (Again / Hard / Good / Easy) feeding
    `src/journal/srs.py` as a pure function, so the schedule is unit-testable
    without a database. *Superseded 2026-08-22:* the scheduler behind those four
    buttons is now SuperMemo's Algorithm Arena, with SM-2 kept as the fallback
    for a clone that has not built it — see
    [`docs/sm20-arena.md`](sm20-arena.md). The buttons, the cut, the blindness
    and the refusal to score anything are unchanged.
  - **The cut is stored, not re-rolled**, because the scheduler assumes a stable
    item. A window that moved every rep would ask a different question each time
    and "did you remember this card" would stop meaning anything. *Superseded
    2026-08-22:* with the jitter gone there is nothing random left to keep, so the
    cut is derived from the entry on every read — stable for the same reason, one
    fewer thing on disk to fall out of date with the trade.

- **G8 — ordering is due-first, then new.** Oldest due first, no daily cap — the
  deck is ~45 cards, and a limit that never binds is a setting that only has to
  be explained.

## What does not change

- The level tagger. `trade_levels`, `level_tag.py`, `level_store.py`, the
  drift-matched null and `METHOD` are untouched, still machine-owned, still not
  read by the gate. `_tag_levels` and `_measure_context` keep running as
  background tasks off sitting-finish; only `_record_intent` goes.
- `TradeDetail` — keeps `ContextStrip`, keeps `LevelStrip`, loses only the thesis
  panel.
- The in-tape review: pressing a trade still seeks the tape to 5 s before its
  entry and plays at 1×, recorder unarmed, order paths refusing.
  *(The 1× was superseded 2026-08-21 — the seek plays at whatever the transport
  is set to. See docs/review-revamp-plan.md for why.)*
- Drill rule checks stay optional and unscored (V3's reasoning holds: forcing
  ticks manufactures compliance data).
- The drill gate still blocks the next rep, on the new predicate.
- `abandoned` never owes; blown still outranks the review gate. *(It read
  "blown/cooldown" until 2026-08-24, when the cooldown was removed — see
  docs/review-revamp-plan.md V11.)*

## Phases

1. **Server — the deletion and the new answer.** Remove `intent.py`, the intent
   endpoints, `_record_intent`, `_grade`/`_grade_row`. Migrate `trade_notes`
   (+`grade`, +`watched_levels`); `NoteIn` gains both as partial fields;
   `db.set_trade_review`. `_unanswered` becomes grade ∧ ≥1 watched level ∧ ≥1
   tag. A `watched_levels` list *replaces* the stored set — deselecting one of
   several has to persist — so it is the one partial field where `[]` clears.
   `_journal_rows` gains the trade's level candidates (entry anchor, distance
   order, labelled) behind a `with_levels` flag so the gate path stays cheap.
   `tests/test_intent.py` and `tests/test_recall.py` deleted; new tests for the
   migration, the partial write, the gate and the candidate ordering.
2. **Review UI.** `ReviewPanel` and `DrillReview` rebuilt to the four fields —
   grade buttons, level picker, `TagInput` (un-orphaned), note. `ContextStrip`
   and the flag verdicts removed. `saveTradeAnswers`' two-write dance collapses
   to one. `ThesisBlock`, `useSaveTradeIntent`, `useIntentVocab` deleted.
3. **Recall backend.** `recall_cards` (SM-2 state + the stored cut) and
   `recall_reps` (append-only: rating, optional guess). `srs.py`.
   `GET /recall/deck` (answer-free), `GET /recall/back/{key}`,
   `POST /recall/rate`. A test asserts the deck payload contains no direction,
   no price and no PnL.
4. **Recall UI.** `RecallCard` gains the transport from `DayReplayer` —
   bounded to the cut on the front, unbounded on the back. The page becomes
   deck → front → optional guess → flip → back → rate.
5. **Verification.** pytest, `tsc --noEmit`, `recallcheck.mjs` rewritten (the
   blind assertions now cover direction and price, not just date and contract),
   `drillcheck.mjs` gate assertions updated to the new predicate.

## Addendum — 2026-08-31: the axes, and the grade moves to the recall front

Decision G3 predicted the grade could describe and never settle "do my A's
beat my B's". The 132 reviews written under that rule were joined to net P&L
and came back worse than predicted: **A 4/4 winners, B 21/21 winners, C 53%,
D 0/30** — the letter was the P&L sign restated, because a "bread and butter"
trade that *lost* was evidently regraded C/D for losing. The category the
grade exists to find — right decision, bad outcome — could never appear.

Three changes, all shipped together:

- **The grade is captured at the recall front.** Picked at the fill-freeze
  beside the guess box, locked by the flip, delivered with the rating
  (`POST /recall/rate`). The server takes only the *first* grade a trade is
  ever given; the deck's front carries `needs_grade` and nothing else new.
  Undo takes the grade back with the rating that wrote it (`recall_undo.
  wrote_grade`/`grade_before`). The panels display grades and no longer
  collect them. Blindness stays client-enforced past the flip, exactly as the
  guess's is (G7-G8's reasoning: the deck is for the person reading it).
- **The panel gate became level ∧ setup ∧ discipline.** Two small enumerated
  axes (`journal.review.SETUPS` / `DISCIPLINES`) replaced "≥1 free tag" —
  the tag box had sprawled to 70 tags across four implicit axes, and the gate
  was being satisfied with the literal tag `None`. Setup asks faded-vs-joined
  first (the one axis worth keeping from the deleted thesis review); the
  discipline calls ride almost entirely on losers in the P&L join. Tags and
  the note stay, optional. `demo/review_axes_backfill.py` carried the
  unambiguous old tags across (62 setups, 15 disciplines, 0 conflicts).
- **Context stopped being typed.** `trade_context.chips()` serves read-only
  chips (approach straightness, drive into entry, range location, the vol
  ruler) on the review payload — the "Chopping"/"POC Shift" tags were hand
  copies of numbers `trade_context` already held. Chips describe and never
  gate: chop KPIs failed as gates (market-structure study), and a chip that
  turned red-means-don't would be that failed gate wearing a label.

The old grades keep their letters; cuts that want the blind era can split on
`trade_notes.updated_at` — every pre-addendum grade was written 2026-08.
