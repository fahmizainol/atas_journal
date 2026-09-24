# Review revamp — every trade answers, in both modes

> **Superseded 2026-08-20 by `docs/trade-grading-plan.md`.** V14's thesis is
> deleted along with its vocabulary; what a trade owes now is a grade, the level
> it was taken off, and ≥1 tag. Everything else here — the in-tape review, the
> per-mode session split — still stands.
>
> **The forcing is gone, 2026-08-25 (user request).** The premise of this whole
> document was V1: a traded sitting *parks* until reviewed, and the account
> refuses the next one until it does. That is retired. Nothing is refused, and
> nothing opens by itself. What replaced it is one bit on the attempt,
> `review_later`: you mark a sitting when it ends (Review now / Review later on
> the recap card) or from the history page's 🚩, and the marked ones are what
> the account chip counts and the history page's Flagged filter shows. Filing a
> review clears the mark.
>
> Read as retired here: **V1** (every trade owes), the create-refusal in
> **V8**/`replay_account.refusal`, **V12** (a review owed opens the page in
> review mode), and **V6**'s drill gate — which had already been switched off on
> 2026-08-20 via `DRILL_REVIEW_REQUIRED`. What survives intact: the review's
> *content* (grade + level + tag), the in-tape panel, the per-mode keys, and the
> server's refusal to call a sitting `reviewed` until every trade is answered —
> that one is a truthfulness check on the word, not a gate on your next sitting.
>
> Why: a review you cannot decline is a form you fill in to get the dice back.
> The sittings worth going back to are not all of them.

2026-08-17. Supersedes the flag-only review shipped with the account
(`docs/terminal-redesign-plan.md` phase 10) and the optional drill review
(`docs/backtest-mode-plan.md`). The change in one sentence: **a settled rep with
trades owes a review of every trade — model + tags on the replay side, tags on
the drill side — and the style flags (fast, hole) are gone.**

Why: the flag review answered for the *worst* trades and said nothing about the
rest, so the record of what a sitting's ordinary trades were — which model they
belonged to, what confluence was actually there — never got written. That is
the data the campaign and the journal can use; "you traded fast" was a scolding.

## Decisions

- **V1 — every trade owes.** A sitting/rep that settles `finished` with ≥1
  booked trade parks there until reviewed. Zero-trade reps auto-pass to
  `reviewed` exactly as clean sittings do today — a pass is a rep, not a chore.
- **V2 — flags trimmed.** `fast` and `hole` are scrapped (they graded style,
  and the per-trade review replaces the conversation). `oversized` stays as-is
  — its mechanics get revamped later, not now. `rewind` stays: it is not a
  style judgment, it marks a rep whose result was written with the answer in
  hand. Surviving flags keep the leak/justified verdict, attached to the trade
  card they belong to (rewinds stand alone).
- **V3 — the per-trade ask.** Replay: an explicit model choice ("no model" is a
  real choice) **and ≥1 tag**; note optional. Drill: **≥1 tag**; note optional;
  the model is the drill's binding and is not re-asked; the rules-met
  checkboxes stay, still optional (an unscored trade stays unscored — forcing
  ticks would manufacture compliance data).
- **V4 — storage is the journal, not a new store.** Tags/note/model land on the
  existing `trade_notes`/`trade_model` rows via `PUT /notes/{trade_key}` — the
  same rows the Trades page reads, so a reviewed replay trade is a journaled
  trade with no copy. Flag verdicts stay on the attempt (`review.items`).
- **V5 — tags are free-form and shared.** One vocabulary across drill and
  replay (and the real journal, which already uses `tags_json`). Autocomplete
  from the union of every tag ever used; no curated master list — that
  ceremony exists for setups/confluences and is exactly what tags are not.
- **V6 — the drill gate blocks the next drill only.** An unreviewed drill
  409s `POST /replays mode=drill`; replay sittings are untouched (the account
  stays blind to drills). Enforced server-side off the latest drill's journal
  rows; the client mirrors it in `drillBlocked` so 🎲 can say why before the
  press.
- **V7 — the server gates on tags; the model choice is client-forced.** The DB
  cannot distinguish "off-model" from "never answered" (`model_id` NULL means
  both), and adding a column for it is not worth it now. So `reviewed` /
  next-drill are refused server-side when any trade lacks a tag, and the
  ReviewPanel refuses to save a replay trade without an explicit model pick.
  Accepted gap: an edited client could file model-less reviews. Noted, cheap
  to close later with a sentinel.
- **V8 — one card, both asks.** A flagged trade's verdict sits on its trade
  card, not in a second list. Flags carry the client trade id; the journal rows
  carry trade keys; the join is entry order, which is the order both sides
  already sort in. On a count mismatch (mirror behind), the panel degrades to
  the two-list layout rather than mis-joining.
- **V9 — the escape hatch is the history page.** A drill owed after a reload
  (the rep-end panel is gone) is reviewable the same way a sitting is: the
  history page's review button works for drills too and opens the in-tape
  review. Without this, one reload mid-owe locks 🎲 forever.
- **V10 — ending a sitting lands in its review** *(added after first use,
  2026-08-17)*. Shipping V1 without this made the review unreachable from the
  page that owed it: End attempt parked the sitting, the account gated the
  next create, and the recorder retried the refused create on every autosave —
  replaying the refusal cue "randomly" for as long as you kept trading. Two
  fixes, both permanent: `endAttempt` (manual or bell; not drills, not deaths,
  not zero-trade) writes the resume + review markers and reloads into review
  mode, the same path `openReview` uses; and the recorder seals after a
  refused create (`createRefusedRef`) — one no is the whole answer, cleared
  when it is pointed at a new session. `tools/browser/reviewflowcheck.mjs`
  holds both.
- **V11 — the hour gate is gone** *(user request, 2026-08-17)*. The 60-minute
  gap between sittings existed to stop one bad session becoming six; the
  forced review does that job better — the pause is spent against the tape,
  and a clean zero-trade pass costs nothing, which a flat hour never got
  right. `SITTING_GAP_S`, `next_sitting_at`, the `hour` refusal and the chip
  countdown are all removed. The 24-hour blown cooldown is untouched — a day
  is what a blown account costs.
  > **And then it wasn't** *(user request, 2026-08-24)*. The same argument
  > finished the job: mandatory reviews replaced the second timer as well.
  > `COOLDOWN_S`, the `cooldown` status, `cooldown_until` and the refusal's
  > `until` field are gone, and a blown funded account is replaceable the
  > moment its cause of death is written. **The write-up gate stays** — it is
  > now the whole of what a death costs.
- **V12 — a review owed opens the page in review mode** *(user request, same
  day)*. **Retired 2026-08-25 — see the header.** Nothing opens by itself; the
  marks below are still the mechanism, but only the history button and the
  recap card's Review now write them. Before this, a pending review left the sim page free to trade a
  session the server refused to record — the dice locked, the recorder
  sealed, and nothing on screen saying why. Now the day pick waits for the
  gate's answer and, when a review is owed, writes the same marks the history
  button writes and lands straight in the review (replay: `review_block`;
  drill: the latest rep's untagged journal rows — the client copy of the V6
  gate). The history button stays as the explicit way in; filing a drill's
  review reloads the backtest page so the next rep draws at once.
- **V13 — replay and backtest are separate sessions** *(user request, same
  day)*. They were one mounted component reconciling across a tab switch:
  same engine, same log, same blotter — and trades placed after switching to
  Backtest were booked into the still-armed *replay* attempt. The routes now
  key the component per mode (a switch is a real unmount), and the resume
  bookmark and review marker are scoped per mode
  (`sim.resume.replay`/`sim.resume.drill`, same for `sim.review`; the legacy
  unscoped keys are read once as a replay fallback). Parking one world and
  visiting the other costs neither its place — and a drill rep now resumes
  too, but only while its attempt is still `active` (resuming a settled rep
  would flip it back and withdraw its review; a resumed rep must also not
  `open` a fresh attempt — adoption wins, or the create mints a duplicate).
  The history page follows the split: Replay | Backtest tabs, a 🎲 badge on
  drill rows, KPIs per mode, and the equity line on the replay tab only.

- **V14 — the per-trade ask is a thesis and a note** *(user request,
  2026-08-18; supersedes V3 and V7)*. The model choice and the tag requirement
  are gone. They were the two questions the app answers better than a person
  can type: `trade_levels` measures which level family every fill landed on,
  against a drift-matched null, and those families are more specific than the
  model names. The confluence box was asking for a worse copy of that from
  memory — and it could not be wrong, which is the deeper problem. What each
  trade owes now is a **thesis** (`bounce` / `break` / `revert`, closed
  vocabulary) and a **non-empty note**.

  **The thesis is collected at the ticket, not here.** One optional click before
  the order goes out, stamped on `OrderRec` the way `micro` and `trail` already
  are, and carried to the journal by `_record_intent` once the mirror has given
  the trade a key. Asked at review time it would be chosen with the outcome
  known — it would grade near-perfectly and teach nothing, and grading a
  contaminated claim is worse than not grading because it manufactures evidence.
  The review still asks on trades that skipped it, and every row carries
  `captured: 'entry' | 'review'` so the two can never be pooled.

  **Never required at the ticket**, because the gestures exist to be fast and a
  mandatory field on the fast path is one that gets answered carelessly.

  **The verdict is shown only on entry-captured claims.** A card still choosing
  its thesis must not display what each option would score, or the answer
  becomes "whichever one grades well".

  This **inverts V7**: the model was client-forced only because `model_id` NULL
  cannot distinguish off-model from never-answered. A thesis row exists or does
  not and a note is empty or is not, so the server gates the whole review
  (`_unanswered`) and the tri-state is gone. It also collapses V3's replay/drill
  asymmetry — the model column was the only thing a drill hid, and there is no
  model column. The rule ticks stay optional and unscored, exactly as V3 has it.

- **V15 — the sitting gets one box of its own** *(user request, 2026-08-22)*.
  Under the cards, above File: what the session was, in your words. Everything
  else the review collects is per-trade, so the thing that is true of the
  *session* — what the day was doing, what you were doing about it — had nowhere
  to go and was being squeezed into some trade's note.

  **No new storage.** It writes the attempt's own `note`, which has existed
  since attempts did, which the history table has always rendered, and which
  `patch_replay` already mirrors onto the journal session row — so the sentence
  appears wherever that session appears (history, Trades) with nothing added
  server-side. Nothing writes that field today, which is why the column has
  always been empty.

  **Optional, and no gate reads it.** A sitting with nothing worth saying about
  it files exactly as it does now — the review's gates stay the three per-trade
  answers.

  **It goes out with File, in the same PATCH as the status.** The cards keep
  per-card saves because a card is finished one at a time; this is one box
  written once, at the end of the pass that just went through every trade. It is
  seeded from the stored note, because filing sends whatever is in it and a
  review must not erase a note the sitting already carried. Cost, accepted: an
  unfiled review loses what is typed there on a reload, the same way an unsaved
  card does.

  **Backtest's rep-end panel (`DrillReview`) does not get one.** It files no
  attempt PATCH at all — it saves cards and draws the next rep — and a drill
  reviewed through the history page reaches this box like any other sitting.

  **Where it is read.** The history table's note column and the Trades page, both
  free (the mirror already ran) — and, since the same day, Lab → Recall's
  **back**: `/recall/back/{key}` reads the sitting's row next to the trade's own
  note (`db.session_note`) and the card prints it under that note, muted and
  labelled "the session:". Two fields, two scopes — one is about this fill, the
  other about the day it happened in. **Back only, and pinned by a test**
  (`test_the_front_never_carries_the_sitting_note`): it is written with the day
  finished, so it is the same hindsight class as the grade, and a description of
  the day is a description of the tape the front is asking you to read.

  *(Amended the same day, user report: "where is it? I can't see it." It was
  below the fold, and so was **File review** — which is the older half of the
  bug. The dock scrolled as one column, so everything that is not about a
  single trade sat under however many trades the sitting took: measured on a
  6-trade review in a 900px window, the box landed at y=1311 and File at y=1405.
  A seventeen-trade sitting hid the button that ends the review.
  
  While a review is up the dock now stops scrolling and the **card list**
  scrolls inside it, so the count, the session note and File stay put at any
  trade count. Two things had to give way for it: `.sim-blotter { flex: 1 }`
  was splitting the spare height with the cards — an empty "No trades yet"
  against the thing being reviewed, which squeezed the cards to 57px, one card
  clipped mid-grade — so the blotter takes only what it needs while reviewing;
  and the standing instructions moved inside the scroll, since they are read
  once and then stand between you and what they describe. Cards get 546px of the
  same window now. `tools/browser/reviewnote.tmp.mjs` measures all of it.)*

## What does not change

- The review is in-tape: press a trade and the tape *plays* from 5 s before its
  entry at 1×. Review mode stays read-only, recorder unarmed, order paths
  refusing.

  *(The 1× half was **superseded 2026-08-21**, on the user's report that the
  speed they set kept coming back as 1. The seek now leaves the transport alone
  and plays at whatever it is on. The 1× was written for the first card, where
  it reads as helpful; a review is a dozen cards, and a control that re-sets
  itself after every press is a setting you have to keep saying. Worse, it did
  not stop at the session: `speed` is persisted through `saveSimPrefs`, so each
  seek wrote 1 over the stored preference. The panel's copy now tells you to
  slow the transport down rather than promising to do it for you. The Recall
  deck still forces 1× on flip — one gesture per card, nothing to fight.)*

  *(Amended 2026-08-17, user report: "I click seek and wait and I don't see the
  trades I placed." Five things were wrong. The
  lead was 60 s, which is a minute of nothing before the moment you asked for.
  The seek landed **paused** — `seekTo` stops the tape, which is right for a
  scrub and wrong for a request to watch something. The 1× was `setSpeed` only,
  so the frame loop, which reads `speedRef`, kept whatever the last sitting was
  set to. And the panel is forced open in review mode: unpinned it lays over the
  right 300 px of the tape, which is exactly where a seek puts the playhead — so
  the trades were drawn and behind the panel. The panel now takes its own column
  whenever a review is on, without writing the pin preference.*

  *And the fourth, found when the first three were fixed and the entry still
  never arrived: **`seekTo` truncates the order log at the new clock**, which is
  right in a sitting (going back means the part you hadn't done yet hasn't been
  done) and wrong in a review. Seeking to entry − 5 s deleted the order that was
  about to fill — and every later one with it, so one seek to an early card wiped
  the rest of the sitting off the tape. Review mode now leaves the log whole and
  records no rewinds; nothing there can write, so there is nothing to protect.*

  *And the fifth, which is why "still can't see" survived the other four: **the
  review only ever worked on its first visit.** `writeResume` stamps the
  bookmark with the recorder's attempt id; the recorder is unarmed in review
  mode, so it wrote `attemptId: null` — and that id is what the tape build's
  `usable` test compares against before it replays a stored log onto the tape.
  First load: the auto-open wrote the bookmark itself, id and all, and it worked.
  Every load after: cards on the right, empty chart, no trades at any clock. Two
  fixes, because either alone leaves an existing browser stuck. The build now
  takes the id **and the record** off the review marker rather than the bookmark,
  so an already-null bookmark recovers on sight; and `writeResume` stamps the
  reviewed id while a review is on. The build also waits for the review's own
  fetch the way it waits for the resume's — `reviewing` cannot be true until it
  lands, so a build that raced it would take the resume path and come up empty.)*
- **Every card is reachable at any time** *(2026-08-17, user request: "let me
  seek to whatever trade even though it's filled")*. The holding rule —
  `seekTo` refuses a rewind past your own entry while size is on — is about a
  sitting, where the rewind truncates the log and un-happens the trade you are
  in. A review carries no size (the position on screen is a replayed one) and
  truncates nothing, so the rule only made the cards stop working the moment the
  tape played into a fill. Skipped while reviewing, scrubber floor included.
- **A review is routed nowhere** *(2026-08-17, user report: "it's showing the
  orders as MNQ — I do have MNQ turned on on the chart")*. The mini/micro picker
  is about where the *next* order goes, and a review sends nothing: the ticket is
  gone and every order path refuses. But the chart's `routedTo`/`pointValue`
  props were fed the plain preference, so a review read with the ticket parked on
  the micro badged the canvas "→ MNQ" and priced the sitting's own NQ positions
  and orders at $2 a point — a claim about orders that cannot be sent. `Simulator`
  now derives `routedMicro` (null while reviewing) and feeds the overlays and the
  blotter's contract badge off that. The *money* was never wrong: `replaySim`
  prices every position from its own `micro` stamp and `fillCfg` carries the
  tape's figure, so only the chips lied.
- `abandoned` never owes (the stale sweep's verdict is "you left").
- Blown/cooldown outrank the review gate; the hour gate sits below it.
- Drill compliance stats: `_compliance_split` semantics untouched (V3).
- No migration: the account was reset 2026-08-17 (epoch 1, zero sittings), so
  no stored fast/hole flags gate anything. Old attempts keep their old flags
  as history.

## Phases

1. **Server.** `flags_for` drops fast+hole. `_raise_flags` auto-passes only
   when no flags *and* no booked trades. `review_block` owes on
   `finished && (flags || trades)`. `PATCH status=reviewed` additionally
   requires every mirrored trade to carry ≥1 tag (journal rows via the scope,
   `include_archived` forced). `POST mode=drill` 409s while the latest drill
   has an untagged trade. `/replays/{id}/journal` rows gain
   `tags`/`note`/`setups`/`confluences` (the save must echo the last two or it
   would blank them). `GET /notes/tags` returns the used-tag union.
2. **Frontend plumbing.** `useTradeTags`, `useSaveTradeReview` (full-body
   note save — replaces the blanking-hazard `useSaveRuleChecks`), row types.
3. **ReviewPanel** rebuilt per V3/V8: every trade a card (seek · model select
   · tag chips with autocomplete · note · save), verdict buttons on flagged
   cards, rewind cards, file gated on completeness.

   *(Amended 2026-08-17, user request: "when I press File review can it save all
   the inputs — right now I need to press Save every time I update a trade."
   The gate counted a card only once it had been **saved**, so File stayed dark
   until you had clicked Save on all seventeen. It now counts a card that is
   *answered on screen*, and File writes every dirty card — sequentially, since
   the mirror rewrites the attempt's rows on each write — before it PATCHes.
   `saveTradeAnswers` moved to `mutateAsync` so the writes can be waited on; a
   failed write stops the file rather than filing against half-written rows. The
   per-card Save stays, it is just no longer a toll.)*
4. **DrillReview** gains tags+note per trade; 🎲 gated until every trade tagged.
5. **Wiring.** Simulator `reviewing` condition includes trades; history page
   owes/review-button condition includes trades and drills.
6. **Verification.** pytest (flag tests rewritten), `tsc -b`,
   `accountcheck.mjs` + `drillcheck.mjs` with stubs updated for the new owed
   semantics.
