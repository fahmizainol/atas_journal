# App Backlog

- **Owner:** afahmi
- **Created:** 2026-08-06
- **Purpose:** Running todo list for **app/product** work — the surfaces (`/charts/live`, `/charts/replay`, Lab) and the data plumbing behind them. Research questions live in [lab-backlog.md](lab-backlog.md); this file is for things that ship as features, not as findings.

Sibling docs worth having open: [live-shadow-plan](../live-shadow-plan.md) (the live stack, phase by phase, and its explicit scope decisions).

---

## Charts — `/charts/live`

### 1. Order entry on the live chart

Place orders from the live chart, wired to a broker. Trial Rithmic account to be
looked into first — nothing here starts before there is an account that cannot
touch a funded balance.

**Read [live-shadow-plan §"Phase 7 — routing"](../live-shadow-plan.md) before
starting.** Routing is currently out of scope *by decision, not by sequencing*:
it turns the paper blotter into an order-state machine reconciled against broker
fills, needs a kill switch and position reconciliation on restart, and the plan's
stated bar for revisiting is "when the agreement rate from Phase 6 has been
stable over a meaningful sample" — which needs the always-on host and a few
purchased Databento days first. Filing it here is a decision to revisit that,
not an instruction to ignore it.

The stated requirement — *be careful not to accidentally put an order in* — is
the design driver, not a footnote. Sketch of what that means:

- [ ] **Demo/trial credentials only, and the app must be able to tell.** A
      distinct env var from `RITHMIC_*` (or an explicit `RITHMIC_ENV=demo|live`)
      that the UI reads and displays permanently, so "which account is this"
      is never inferred from memory. Refuse to arm on a live account until
      that is deliberate.
- [ ] **Off by default, armed explicitly, disarmed automatically.** Ordering
      hidden entirely unless a config flag is set; an explicit *arm* action per
      session that expires (on disconnect, on the 18:00 roll, on idle). The
      resting state of the page is read-only.
- [ ] **No single-click path from a chart gesture to a live order.** The
      Simulator's `＋Order` tool and long-press ticket exist because the replay
      *is* a trainer — the live surface needs a confirm step that names side,
      size, price and account in words before anything is sent.
- [ ] **Kill switch + position reconciliation on restart** (the two things
      Phase 7 names). A restarted API must discover what is actually working at
      the broker before it draws anything, and never assume flat.
- [ ] **Keep shadow signals and routing separate.** Shadow mode's only purpose
      is fidelity; the plan is explicit that Phases 0–6 must not be shaped for
      routing. Whatever this becomes, it reads the same tape — it does not get
      to change how the shelf is evaluated.
- [ ] Decide manual-only vs. strategy-routed. Manual-only is the honest first
      step and skips the entire "did the engine mean this fill" problem.

### 2. Option to disable recording / shadow signals — DONE (2026-08-06)

A switch for "watch the tape, don't write anything, don't run the shelf".

**One gotcha, load-bearing:** live-shadow-plan §decision 7 — *"persistence is
not optional, but the recorder process is."* Ten gate sites read the overnight
**off disk** keyed by `(contract, day)`, not from the injected frame, and gates
**blind-fail-closed** — so a live day with nothing written behind it makes every
`gx_*` gate veto silently, which looks exactly like "no setup formed". Disabling
the *writes* while leaving shadow signals on therefore produces a plausible
wrong answer, not an obvious failure. The switch has to cut the recorder
*process* (or the shadow runner), never just the file writes.

> **Done.** Two switches, `POST /live/modes?record=&signals=` (either may be
> omitted to leave that mode alone), plus the same pair as query params on
> `/live/feed/rithmic` so a connection opens in a mode rather than being
> corrected into one. `state.check_modes` is the single place both entry points
> ask, and it refuses **two** combinations, for opposite reasons — the
> load-bearing one above, and *fake feed + recording*, which would manufacture a
> live day out of a replayed one (decisions 3-4). The fake feed may run the
> shelf with nothing recorded and that is not an exception: the day it replays
> is a cached day, so the windows the gates read are already on disk. The rule
> is about whether those reads can be answered, which is what `source` decides.
>
> Two things the scoping did not anticipate, both found in the code:
>
> - **`Live.source` was derived from `record`.** True only while the two were
>   one switch — a Rithmic session with recording off started calling itself a
>   fake feed, so the banner that exists to stop this surface being mistaken for
>   something it is not would have been the thing lying. `source` is passed
>   explicitly now, with a test pinning it.
> - **A day recorded in two halves has a hole in the middle**, and the manifest
>   is exactly the file someone consults to find out whether a day is whole.
>   Ticks that reach the tape with no recorder attached are counted
>   (`Live.unrecorded`); switching recording back on stamps the count into the
>   new recorder's `stats.unrecorded_rows`, and the banner shows it in orange for
>   the rest of the session. Re-arming *resumes* the chunk numbering, so nothing
>   already written is overwritten — but the gap is not recoverable, and now it
>   says so in the two places a reader looks.

- [x] Two independent toggles, not one: **record tape** and **run shadow
      signals**. Recording without signals is useful (harvest a day, evaluate it
      later); signals without recording is the broken combination above and is
      **refused** — 422 with the reason as the message, and the UI blocks the
      control rather than hiding it. Deliberately *not* auto-disabling the shelf
      when recording is switched off: that would be the page taking a bigger
      action than the one asked for. The connect form is the one place the two
      are still a single choice, because nothing has started yet.
- [x] Surface the current state on the page — two chips in the feed banner
      (`● recording` / `● shadow shelf`), the simulated feed showing a
      `not recorded` note with its reason on hover instead of a dead control.
      `/live/status` grew `signals`, `journalling`, `unrecorded_rows` and
      `can_record` to feed them.
- [x] Runtime toggle (endpoint + UI) vs. env-only. Runtime, as reasoned — no new
      env vars. One addition the scoping missed: **the modes survive the 18:00
      roll and a process restart.** The roll inherits them (they are a decision
      about the run, not the day — otherwise the switch undoes itself at the one
      hour nobody is watching), and `resume()` reads the shadow mark back out of
      the manifest, so a restart does not silently re-arm a shelf that was
      turned off.
- [x] Signals-off leaves the journal *absent*: the `SignalJournal` is never
      constructed, so no directory appears and Phase 6 reports `unavailable`
      rather than a clean pass over nothing. The recorder's manifest also
      carries `shadow: "on" | "off"` (via a new persistent `marks` dict, the
      same channel `harvest` uses for `source`), so the next morning a reader can
      tell a day nobody ran the shelf over from a day it found nothing on.

Verified: 12 new tests (`tests/test_live_record.py` §the two modes,
`tests/test_live.py` §the shelf's own switch) covering both refusals, the
unrecorded-hole accounting, chunk-numbering continuity across a re-arm, the roll,
the restart, and the runner's own stop/start (a `start` that did not clear the
stop event would spawn a thread that exits on its first pass — silently, and
looking exactly like a market with no setups in it). Full suite 473 pass; the
three `test_sim_charts.py` failures are the pre-existing WIP ones. Checked in a
browser against a simulated feed, both states.

### 3. Revamp `/charts/live` UI/UX to match `/charts/replay`

The Live page is 1,007 lines against the Simulator's 2,297 and has none of the
Simulator's indicator suite, setup bar, or prefs plumbing — the two halves of the
"one chart with two clocks" idea do not currently look like one chart.

- [ ] Inventory what Replay has that Live does not: setup bar, `SimPrefs`
      persistence, `IndicatorLegend`, the composite/nodes/events layers, the
      developing NY profile, the ATR/range-budget indicators, the fullscreen
      mode and mobile pointer handling.
- [ ] Decide what is genuinely shared vs. deliberately different. A live chart
      has no transport and no seek; everything else is arguably the same
      instrument. Shared components beat a second copy — but note 1b (the
      Simulator hook decomposition) is **still unfinished** in the live plan, and
      that is the work that makes sharing cheap. Sequence accordingly.
- [ ] `.sim-page` heights are measured in JS (`useFillHeight`) — any new page or
      remount path needs the hook, not a copy of the old mount-once effect. This
      already bit the Live page once (chart collapsed and grew as the signal rail
      filled).
- [ ] Keep the suite's standing rule: **context, not signals**. No indicator
      lands on the live chart that failed its A/B elsewhere.

### 4. Display backfilled days on `/charts/live`

Harvested/recorded sessions are invisible in the UI today.

**Most of the server work is already done.** `GET /live/recordings`
(`api/routers/live.py:175`) already lists every recorded session newest-first
with rows, chunks, closed flag, last tick and the recorder's `stats` — and
**nothing in `frontend/` calls it.** `src/journal/live/harvest.py` fills the days
nobody was connected for and stamps `harvest.complete` in the manifest.

- [ ] Render the recordings list on the Live page — a coverage strip or panel:
      day, rows, complete/partial, source.
- [ ] Distinguish **watched** from **harvested** in the UI, because the manifest
      does and the difference matters: a harvested day has **no signal journal**
      (nothing recorded what the shelf believed) and carries **Rithmic's clock**
      (median 287µs later than the exchange stamp on a watched day). Honest
      absence, not a blank cell.
- [ ] Show the harvest deadline where it can be acted on: Rithmic replays a
      *listed* contract back ~120 days and an **expired** one not at all — so the
      outgoing contract must be deep-harvested **before** `LIVE_SYMBOL` rolls.
      That is a date the UI can compute and warn about.
- [ ] Surface `stats.clamped` from the manifest (out-of-order exchange stamps) —
      the plan flags a non-tiny figure as a real finding, and it is currently
      only readable by opening a JSON file.

### 5. *(open slot)*

---

## Platform / data

### 1. Let the Lab use Rithmic-backfilled days to fill missing sessions

The research/sim stack reads `data/cache/ticks/` (Databento); the live stack
writes `data/live/ticks/` (Rithmic) and never mixes them — deliberately, so
Phase 6's reconciliation has an independent reference. With the **Databento
budget empty** (cache pinned ≤ 2026-06-30) the harvested store is the only
growing source of tape, so the question is how to let the Lab read it *without*
destroying that independence.

- [ ] Decide the seam. Options, cheapest first: (a) an explicit opt-in source
      flag on the Lab/sim day loader; (b) a one-way "promote a harvested day into
      the cache" step that rewrites it into the cache's segment layout
      (`_on`/`_rth`/`_post` + `_sums.json`); (c) a merged reader. **(b) is the
      one that keeps reconciliation honest** — promotion is an act, it can stamp
      provenance, and it cannot silently contaminate the reference set.
- [ ] Whichever seam: every derived artifact must carry provenance. A study
      whose window silently mixes Databento and Rithmic days, with no column
      saying which, is a result nobody can audit later.
- [ ] Resolve the open correctness questions **before** promoting anything: the
      aggressor mapping (`BUY=1/SELL=2` taken from Rithmic's protobuf enum, but
      whether Rithmic's *aggressor* and Databento's *side* mean the same thing is
      **untested** — every recorded tick keeps `agg_raw` so a wrong answer is
      re-derivable), and the clock offset (harvested days carry Rithmic's stamp,
      a systematic offset against Databento's `ts_event`). Both are exactly what
      Phase 6 stage 1 measures — so this item is **downstream of buying a few
      overlapping Databento days**, not parallel to it.
- [ ] Note the known cache seam while doing the comparison: the `rth`/`post`
      parquets have a one-print boundary overlap (2025-10-13 case in the plan),
      harmless to existing readers, not harmless to a print-for-print compare.
- [ ] Segment coverage: a Lab day needs the overnight, or the `gx_*` gates
      blind-fail-closed — same trap as the recording toggle above. A promoted day
      must be whole or be marked incomplete.

### 2. L2 / depth data

Standing gap. Several studies have died on it or been ruled untestable without
it: the MBO iceberg edge from the Pulcini podcast, LOB depth models from the
2024-26 ML survey (marked dead *"no L2"*), and the big-print digestion work ran
against trades-only tape by necessity.

- [ ] Scope what "L2" means here first — top-of-book quotes, full depth
      snapshots, or MBO (order-by-order). They are different products, different
      prices, and only MBO answers the iceberg/absorption questions that were
      parked.
- [ ] Price it against both providers (Databento MBO/MBP-10 for history,
      Rithmic depth for live) and against the **actual** parked questions, not a
      general wish for more data. The null shelf is long: absorption/exhaustion
      is dead at every live anchor on trade tape, and "more resolution" is a
      hypothesis about *why*, not evidence.
- [ ] Storage/throughput reality check before anything is bought — trades-only
      is ~3 MB/session; MBO is orders of magnitude more, and the whole cache
      layout (`_on`/`_rth`/`_post` parquets, per-session sums) assumes the small
      number.
- [ ] Decide the first question it would answer, and pre-register it. If the
      answer is "we would look around", the spend is premature.

### 3. *(open slot)*

---

### Notes

- Nothing in this file is a research claim. Anything that turns into an edge
  hypothesis moves to [lab-backlog.md](lab-backlog.md) and takes the usual
  route: Lab-first, then an engine A/B, then a gate.
- The largest standing risk on the live stack is not in this file and is not
  technical: a month of work exists only in the working tree
  (see live-shadow-plan §"What's left" item 1).
