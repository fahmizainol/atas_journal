# Backtest mode — build plan

*Written 2026-08-15, decided in one sitting against the built terminal. Nothing
below is built yet. Branch it off `feat/terminal-redesign`, which is where the
Simulator, the pane grid and the replay account currently live — this depends on
all three.*

*Read [`docs/terminal-redesign-plan.md`](terminal-redesign-plan.md) phase 10
first if the account model is unclear. This mode is defined largely by what it
switches **off** there, and the reasons that phase gives for the account are the
reasons this one is allowed to skip it.*

---

## Why this exists

The Replay page answers "can I trade a day I chose". It does not answer the two
questions that actually decide whether a model is worth trading:

1. **How often is the setup even there?** Every number the app holds about a
   model is conditioned on trades that were taken. Nothing counts the hours the
   model was looked for and not found, so its base rate is unknown and its
   expectancy is quietly conditioned on your own recall.
2. **Can I recognise it cold?** The replay's day picker means you always know
   which day you are on, and after 77 sittings the corpus is no longer strange.
   `blind` exists for exactly this, but it hides only the date — the tape still
   starts where the session starts, so every rep is the same rep: watch the open,
   wait for your thing.

Backtest mode is a **rep generator for one model**: bind a model, get thrown at a
random hour of a random day, and either find it or don't. Both outcomes are
recorded, which is what makes a base rate exist.

It is deliberately *not* a backtester. `src/journal/sim/engine.py` already runs
strategy specs headlessly and Lab → Strategies already reports them. This is the
other half — the engine says the edge is there, this says whether you can see it
from inside the tape.

---

## The mode in one screen

```
▸ BACKTEST · Drift-touch fade                     [ End rep ]
  ▨▨▨▨ · 13:47 ET · to close 2h13m                rep 9 · +$220

  ⏵ ⏸  ▸▸ 30×  · step ›       ⏪ dimmed — "not in a drill"
```

Bind a model, press 🎲, land at 13:47 on a day you are not told the date of, with
that day's 09:30→13:47 drawn behind you. Trade it or don't. Press **End rep**, or
let it run to 16:00. The day is revealed, the review panel offers the model's
rule checklist, and 🎲 draws the next one.

---

## The decisions, and why

**D1 — the model binding is a tag, not a gate.** The ticket is untouched;
nothing is refused for being off-model, and the rule checklist is answered
*after* the rep, not before the send. The alternative — rules on the ticket,
gating the send — produces honest pre-outcome compliance data, and was declined
as too much ticket surgery for a first build.

> **The cost, stated once so it is not rediscovered as a surprise.** `rules_met`
> written here is ticked knowing the P&L. It is a training aid and a memory
> prompt; it is **not evidence**, and it must not be pooled into anything that
> gets A/B'd. Every other compliance number in this app was written the same
> way, so this changes nothing about their status — it just adds volume to a
> sample that was already self-reported.

**D2 — reps are unpriced.** No account, no MLL, no DLL, no cooldown. Phase 10
exists because "a replay rep is free" is a real problem, and this reintroduces
it knowingly: a drill is practice at *finding* the setup, and pricing it at one
rep an hour behind a 60-minute gate would cap the drill at a rate that makes a
base rate uncollectable. The stakes live on the Replay tab, which is untouched.

So `replay_account.py` must **exclude drill attempts from both the equity walk
and the create gate**. Two call sites, one filter.

**D3 — the mode is fixed when the rep opens and cannot be changed after.**
Without this, "relabel the losing sitting as a drill" is a working strategy for
hiding a loss from the account — the same hole `sweep_stale_actives` (phase 10
D7) was written to close, re-opened through a new door. `PATCH /replays/{id}`
refuses a `mode` change, full stop.

**D4 — the draw is today's `anyDay()` verbatim.** Uniform over all 601 days in
`data/cache/ticks`, with replacement, no memory of what has been sat. Considered
and rejected: drawing from unsat days first. At 601 days a chance repeat is rare,
and the set-difference bookkeeping buys little.

**D5 — the drop window is configurable, defaulting 09:30–15:00.** Two fields in
the setup panel. The default leaves every rep at least an hour of runway to the
close; narrowing it lets an afternoon-only model be drilled where it lives.

> Narrowing the window is telling yourself where the setup is, which is half of
> what the drill tests. That is your call per campaign, and the drop histogram on
> the Backtests card shows what you actually drew — so a narrow campaign cannot
> later be mistaken for a wide one.

**D6 — blind is forced on; the clock stays visible.** Date off the picker and
off the axis, exactly as `blind` does today. Time of day stays, because most
models in this journal are time-conditioned — afternoon-only, first-hour — and a
drill that hides the clock makes the bound model unexecutable. `Reveal` stays for
when you want out; the day auto-reveals at rep end.

**D7 — a rep runs from the drop to 16:00 ET, and `End rep` is always live.**
Rejected: a fixed time box. The variable rep length is the honest one — a 09:41
draw *is* a longer question than a 14:30 draw — and in practice `End rep` is what
ends most reps anyway.

**D8 — the transport is forward-only.** Play, pause, speed, step forward. No
rewind past the furthest point reached. A rewound rep is a rep that knew the
answer, and pooling it with cold ones is what the base rate cannot survive.
`attempt.rewinds` is therefore always `[]` in this mode, and phase 10's
`rewind-used` flag can never fire on a drill.

**D9 — the attempt opens at the drop, not on the first fill.** This reverses
`replays.create`'s stated contract ("a session you watched without trading leaves
nothing behind", `replays.py:152`) **for this mode only**. It has to: a rep where
you correctly sat out is the single most valuable row a blind drill produces, and
under the existing contract it leaves no trace at all.

**D10 — an abandoned rep is deleted, not counted.** With D9, "I closed the tab"
and "I sat out" are the same record, and the base rate degrades exactly as fast
as you re-roll draws you don't like. So `sweep_stale_actives` gains a drill
branch:

| stale `active` drill | what happens |
|---|---|
| zero trades | **folder deleted** |
| any trades | settled as `abandoned`, exactly as today |

and drawing again closes the currently-open empty rep immediately rather than
leaving it for the hour-long sweep.

> The cost: a rep you meant to come back to in an hour is gone. `lib/replayResume`
> already exists for coming back to a sitting, and a drill you have walked away
> from for an hour is not a cold read any more regardless.

**D11 — drills book as `sessions.mode='backtest'` with the model bound.** Reuses
the binding wholesale — `db.upsert_session` already takes `model_id`, and
`sessions.py:7-9` already defines backtest as "one model exercised exclusively,
so it binds every trade in the session", which is precisely what a drill is.

The known cost is that hand-drilled reps pool with ATAS engine exports in the
Backtests arena row, which is where "does replay/live match the backtest" is
asked. **They stay separable**: a drill's `source_file` is
`replay/<attempt id>` (`booking.py:384`) and an import's is its folder path, so
splitting the row later is a presentation change on `Backtests.tsx` and touches
no stored row.

**D12 — the review is offered, never forced.** The panel opens at rep end with
the checklist ready; 🎲 stays live whether you fill it in or not. Skipping costs
the data point and nothing else — `_compliance_split` puts a trade with no
recorded checks in **`unscored`**, explicitly not `broke`, because "counting it
as 'broke' would slander it" (`models.py:180`).

**D13 — the campaign is read on the Backtests model card.** That is where the
model's numbers already live. `ReplayHistory` lists individual drill attempts for
free; the chart page carries only a live rep counter.

---

## Architecture

### Backend

**`src/journal/replays.py`**

- `create(..., mode: str = "replay", drop_ms: int | None = None,
  window: dict | None = None)`. `mode` is `"replay"` or `"drill"`; `drop_ms` is
  the replay clock the rep started at and `window` the drawn-from bounds, both
  needed by the drop histogram and neither derivable from anything else. The
  first-fill contract stays the default; drill mode calls it at the drop.
- `patch()` refuses a `mode` change (D3).

**`src/journal/replay_account.py`** — the whole of this mode's interaction with
the account is one predicate, applied twice:

- `epoch_attempts()` filters out `mode == "drill"` — which makes the equity walk,
  the day-loss grouping, the death search and `refusal()`'s "unreviewed sitting"
  branch all drill-blind at once, since every one of them is built on that walk.
- `sweep_stale_actives()` gains the D10 branch: a stale active drill with no
  trades is deleted via `replays.delete`, not patched to `abandoned`.

> **Do the filter in `epoch_attempts`, not in `settled_attempts`.** The gate
> reads `epoch_attempts` for the hour rule and the unreviewed rule; filtering one
> layer down would leave a drill able to start the 60-minute clock on the *real*
> account, which is the bug this mode exists to not have.

**`src/journal/live/booking.py`** — `book_attempt` currently hard-codes
`db.upsert_session(conn, src, "replay", REPLAY_ACCOUNT)` at line 489 and discards
`attempt["model_id"]`. It branches: a drill upserts
`(src, "backtest", REPLAY_ACCOUNT, model_id)`. **This is the one edit that
touches existing replay booking** — the replay path must come out
byte-identical, which `tests/test_live_booking.py` already asserts.

> `upsert_session` is `INSERT OR IGNORE`, so it will not update a row that
> exists. Harmless here because the binding is fixed at rep start (D3) and each
> attempt owns its own `source_file`, but it means a bound model cannot be
> changed after the first autosave — which is the intended behaviour, arrived at
> by accident.

**`api/routers/replays.py`**

- `POST /replays` accepts `mode`, `drop_ms`, `window`; skips
  `replay_account.refusal()` entirely when `mode == "drill"`.
- `GET /replays/drills?model_id=` — the campaign aggregate: reps, traded, sat
  out, base rate, net, expectancy, and the two histograms (drawn hours vs traded
  hours). **Declared above `/replays/{attempt_id}`**, the path-swallow trap
  `backfill_journal` documents.

> The aggregate reads `data/replays` attempts, **not journal trades**. A sat-out
> rep has no trades at all, so a trades-based aggregate would report a 100% base
> rate forever and look entirely plausible doing it.

**No new endpoint for the review.** `PUT /notes/{trade_key}` already takes
`model_id` + `rules_met` and already sweeps checks belonging to another model's
rules. The review panel posts to it.

### Frontend

- **`lib/workspaces.ts`** — a third Charts tab, `/charts/backtest`, after Live.
- **`Simulator.tsx` parameterised by mode**, not a new page. It is ~3,000 lines
  of tape, engine, panes, fill model and recorder, all of which this mode wants
  unchanged. The mode gates four things: the draw (random clock), the transport's
  rewind, when `armAttempt` fires, and which prefs key the drill-only settings
  come from.
- **`lib/drillPrefs.ts`** (or a field on the existing blob) — bound model id and
  drop window. Everything else — ticket, speed, bar size, layouts, indicators,
  fill model, commissions, latency — shares `sim.prefs`, because a drill is the
  same chart with the same costs.
- **The rep counter** on the top bar, and a **`DrillPanel`** in the rail: rep N,
  today's reps/traded split, running net, the last ten reps, and 🎲.
- **`Backtests.tsx`** — the drill block on the model card (D13).

---

## The commit sequence

Each is shippable alone. Backend commits carry pytest; replay-UI commits carry
`typecheck && build` plus a browser check.

1. this doc
2. `replays.create` gains `mode`/`drop_ms`/`window`; `patch` refuses a mode
   change; tests
3. `replay_account`: the `epoch_attempts` filter and the `sweep_stale_actives`
   drill branch, with tests proving a drill moves neither equity nor the gate
4. `book_attempt` branches to `mode='backtest'` + `model_id`, replay path
   asserted unchanged
5. `POST /replays` accepts the new fields and skips the gate for drills
6. the route: third tab, `/charts/backtest`, Simulator parameterised, model
   binding in the setup panel — no draw yet, behaves as blind replay
7. the random drop: the window fields, the clock draw, `snapshotTo` at the drop,
   the identity block's dateless clock
8. forward-only transport
9. attempt-at-the-drop + End rep + the reveal + the rep counter
10. the review panel posting `PUT /notes/{trade_key}`
11. `GET /replays/drills` + the Backtests drill block
12. `tools/browser/drillcheck.mjs`

---

## Risks worth writing down

1. **The `epoch_attempts` filter is load-bearing and silent if wrong.** A drill
   leaking into the walk moves real equity and can start the 60-minute gate on
   the account you actually practise on. Test it directly: derive with a drill
   present and assert equity, floor, `next_sitting_at` and `review_block` are all
   identical to deriving without it.
2. **D9 changes when the recorder arms**, which is the code path every browser
   check in `tools/browser/` already has to stub (`panecheck` and `accountcheck`
   both answer `POST /replays` synthetically). A drill arms on load, so a check
   that merely *opens* the page now opens an attempt. Stub before writing one, or
   it writes practice nobody sat.
3. **The mirror replaces rather than appends** (`db.replace_journal`), and it
   runs on every autosave. Rule checks are keyed by `trade_key`, so review only
   after the rep is finished — a review written mid-rep is a check hanging off a
   key the next autosave may rewrite.
4. **`book_attempt`'s replay path must not move.** It is the only edit here that
   touches something already carrying data.
5. **Base rate is only as honest as D10.** If the delete branch does not land,
   every abandoned draw counts as a sat-out rep and the number reads low forever
   — plausibly, which is the worst way for it to be wrong.
6. **Pooling in the Backtests arena (D11).** Accepted, reversible via
   `source_file`. Worth splitting the row the first time you read that number for
   a real decision.

---

## Open items

- **The gated version of D1.** If the compliance numbers from this mode turn out
  to be worth trusting, the pre-trade checklist on the ticket is the upgrade, and
  it is additive — the stored shape does not change, only when it is written.
- **Per-model draw pools.** D4 draws blind to history. If a campaign ever runs
  long enough to exhaust the corpus's strangeness, unsat-first is the fix.
- **The drop histogram is the interesting output and has no home yet beyond the
  model card.** Drawn hours vs traded hours, per model, is the closest thing this
  app would have to a measured answer for "when does my setup actually happen" —
  worth a research doc once there are reps.
