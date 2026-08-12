# App Backlog

- **Owner:** afahmi
- **Created:** 2026-08-06
- **Purpose:** Running todo list for **app/product** work — the surfaces (`/charts/live`, `/charts/replay`, Lab) and the data plumbing behind them. Research questions live in [lab-backlog.md](lab-backlog.md); this file is for things that ship as features, not as findings.

Sibling docs worth having open: [live-shadow-plan](../live-shadow-plan.md) (the live stack, phase by phase, and its explicit scope decisions).

---

## Charts — `/charts/live`

### 1. Order entry on the live chart — BUILT (2026-08-07), READ PATH PROVEN, WRITE PATH NOT

Place orders from the live chart, wired to a broker.

**Read [live-shadow-plan §"Phase 7 — routing"](../live-shadow-plan.md) before
touching this.** Routing was out of scope *by decision, not by sequencing*, and
the plan's bar for revisiting it — "when the agreement rate from Phase 6 has
been stable over a meaningful sample" — **has not been met**. What exists is the
mechanism, built so that the resting state of a checkout cannot trade; the bar
above still governs whether it is ever pointed at a funded account.

> **Built twice, and the second design is the one to read.** The first cut
> (2026-08-06) honoured a rule from this item's original scoping — *no
> single-click path from a chart gesture to a live order* — by keeping the two
> apart: chart gestures filled a paper blotter, and a separate panel with its
> own form was the only thing that could reach the exchange.
>
> **Using it showed the ergonomics were inverted.** The whole point of the chart
> gestures is speed and muscle memory, and they were wired to the one thing
> where speed does not matter; real orders were typed into a form, *including
> the price*, so you could not click the level you wanted. The rule was
> protecting the wrong thing.
>
> **The rebuild (2026-08-07) makes paper an account.** It sits in the same
> selector as the Rithmic ones, every way of placing an order works for all of
> them, and what varies is **not the capability but the confirmation** — the
> ATAS model: a popup naming the order in words, with a per-account "one-click
> trading" toggle that skips it.
>
> |  | confirm popup | reaches the exchange |
> |---|---|---|
> | 📝 Paper | never | no |
> | `[demo]` | on by default, toggleable | yes |
> | `[live]` | on by default, toggleable | yes |
>
> **The arm was removed (2026-08-11).** The middle column used to read "arm
> required", and both real rows said yes: a typed confirmation that granted
> fifteen minutes of sendability and lapsed on idle. It went because the
> deadline was the wrong shape for the job — it lapsed mid-decision and stood
> open while nobody was watching, so it was ceremony at exactly the moments it
> was meant to matter. What it actually enforced is now
> `Broker.check_routable`, four standing facts read on **every** order rather
> than once per lease: routing is switched on, a real account is selected, a
> person has labelled it, and the broker has been read back.
>
> **The seam that decided the shape: one Rithmic login is one concurrent
> session.** Measured twice already in this stack (the access probe, and the
> harvest sweep, which runs on the live client for the same reason). The order
> plant therefore rides `RithmicFeed`'s connection — so a disconnect leaves the
> session unable to send until it has re-read the book, there is no routing
> without a tape, and `routing` is settled at connect rather
> than being a runtime switch like `record` and `signals`. Those change what is
> written down about a session; this changes what the socket may do.
>
> Files: `src/journal/live/routing.py` (policy, tags, the review token),
> `src/journal/live/broker.py` (the order plant, the gate, fill pairing),
> `api/routers/live_orders.py`, `frontend/src/hooks/useOrderIntent.ts` (the one
> funnel every gesture ends in), `frontend/src/lib/brokerViews.ts`,
> `frontend/src/components/{RoutingPanel,OrderConfirm}.tsx`.

**Four things the scoping did not anticipate, all found in the code:**

- **The confirm had to be a server-issued token, not a modal.** "A confirm step
  that names side, size, price and account in words" is satisfiable by a dialog
  the client could simply not render. `POST /preview` returns the sentence *and*
  a single-use token; the reviewed door on `POST /orders` takes the token **and
  no other field**, so there is no request shape that describes an unreviewed
  order. One-click is a separate door on the same endpoint, refused unless
  *that account* has the flag — so "send without review" does not exist as a
  shape until somebody asks for it, per account.
- **The app can corroborate "demo" and can never corroborate "live".** The first
  `observe()` returned demo/live from the gateway and system name. That is a
  confident wrong answer waiting to happen: a name can *show* an account is a
  test one, but the absence of the word "test" is evidence of nothing. Rithmic's
  `ResponseAccountList` carries an id, a name, an FCM and a loss limit — nothing
  about funding. So the label is a person's declaration, and the rebuild moved
  it from an env var to a per-account tag that is **visible as a badge** wherever
  the account appears. The rule that survived intact: **untagged is not demo**,
  and an untagged account cannot send anything.
- **One-click had to reset on promotion to live.** Enable it on a demo account
  because confirming every practice order is friction, re-label that account
  live, and the fast path silently follows onto real money. `routing.set_tag`
  clears it on the demo→live transition.
- **A real account that is selected but cannot yet send must refuse, not fall
  through to paper.** (Untagged, or not read back.) The first wiring let the
  gesture make a paper trade. That is a
  surprise in both directions — you either think you are in a trade you are not,
  or you have made a practice trade you did not ask for. Paper is one click away
  in the selector if that is what you wanted.

- [x] **Demo/trial credentials only, and the app must be able to tell.**
      Re-scoped: the app *cannot* tell, and now says so honestly. Per-account
      labels with a badge, no default, untagged refuses to send.
- [x] **Off by default, and pointed at something real only deliberately.**
      `LIVE_ROUTING=1` or the endpoints 403 and the ORDER plant is never opened.
      Every session starts on **Paper**, including after a restart or the 18:00
      roll, so choosing a real account is an act. ~~The arm is typed and lapses
      on idle.~~ **Removed 2026-08-11** — see the note above; the four gates it
      sat on are now read on every order, and a reviewed order is dropped by a
      disconnect, an account switch, an instrument switch, the roll and a
      flatten.
- [x] ~~**No single-click path from a chart gesture to a live order.**~~
      **Deliberately reversed**, on the reasoning above. What replaces it: the
      confirm popup by default, one-click as an explicit per-account opt-in that
      resets on promotion to live, and a permanent account chip in the top bar
      so "am I about to trade real money" never depends on which panel is open.
- [x] **Kill switch + position reconciliation on restart.** `reconcile()` runs
      on selecting a real account and **orders are refused until it answers** —
      `reconciled_at is None` renders as "not read back", never as "nothing
      working". Flatten cancels before it exits (exiting under a live bracket
      can leave the bracket to open a fresh position the other way), is **gated
      on nothing but the connection**, and reports a partial failure naming both
      halves.
- [x] **Keep shadow signals and routing separate.** `shadow.py` imports nothing
      from `broker.py`; the shelf has no reference to reach it with.
- [x] Manual-only. Nothing routes a strategy signal.

Verified: 124 tests in `tests/test_live_routing.py`, the broker driven against a
fake plant on a real event loop **in a second thread** — production's
arrangement, since a single-threaded test would deadlock on the very hand-off
`Broker._call` exists to make. The tag store is patched out for every test, so
none can write a real label. Suite 544 pass; 12 fail, of which 3 are the known
`test_sim_charts.py` WIP and 9 are Databento gateway 504s. Frontend typechecks
and builds.

**The read path has now met a real plant.** `data/live/orders/NQU6/2026-08-07/`
records it: the ORDER plant opened on the live LucidTrading login, the account
list came through with two accounts, and switching between them ran
`list_orders` + `list_positions` against each and got flat, zero-order answers
back. So `attach`, `accounts_view`, `use_account` and `reconcile` are proven, not
inferred.

That same file also records the **old** code failing there first — nineteen
`attach_failed` lines in a reconnect loop, because the pre-revamp
`_pick_account` raised on a login with two accounts and took the whole feed down
with it. That is exactly the failure the rebuild removed by opening on paper
instead, and it happened for real rather than hypothetically.

**The write path is still unproven**, and it is the whole of what is left.
No order has been placed, so everything past a read is built from Rithmic's
protobuf schema:

- [ ] Place one order on the TEST account: does it come back through
      `on_exchange_order_notification` with the fields `_order_rec` reads
      (`basket_id`, `notify_type`, `total_unfilled_size`, `account_id`), and do
      the bracket kwargs (`stop_ticks`/`target_ticks`, template 330) produce the
      bracket the confirm sentence described?
- [ ] Check `account_id` **is populated** on the exchange and PnL notifications.
      `Broker._mine` treats a blank one as ours — right for a single account,
      where dropping unlabelled messages would lose real fills, but a plant that
      never sets the field silently defeats the multi-account filter.
- [ ] Confirm the bracket-leg inference in `brokerViews.bracketOf`. The legs are
      matched by side, type and position relative to the entry rather than by
      `linked_basket_ids`, which is only populated on some notification paths.
      A wrong match would put a drag on the wrong leg.
- [ ] Check `_on_fill`'s pairing against the broker's own `day_pnl` over a
      session with a scale-out and a flip in it — the netting mirrors
      `replaySim`, and where the two disagree the answer should be written down
      rather than reconciled away.
- [ ] Confirm the prop firm's written rules cover it. Decision 1 is explicit
      that this is the line firms diverge on, and `manual_or_auto` is left at
      the client's `MANUAL` default — a claim being made to the broker on every
      order, not a formality. Faster manual entry is still manual entry, but
      one-click trading on a funded prop account is worth reading the rules over.

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

### 3. Revamp `/charts/live` UI/UX to match `/charts/replay` — DONE (2026-08-06)

The Live page was 1,007 lines against the Simulator's 2,297 and had none of the
Simulator's indicator suite, setup bar, or prefs plumbing — the two halves of the
"one chart with two clocks" idea did not look like one chart.

> **Done**, on branch `chart-maximal-ui` (`375c2bc` … `ceb5b75`). The gap closed
> mostly by *deletion* rather than by porting: both pages lost their shell chrome
> to a single ~36px `ChartTopBar`, and what Live was missing turned out to be
> reachable by handing the same components the live tape instead of a finished
> one. Live is 1,379 lines now, and the growth is the feed and the shadow rail —
> not a second copy of the chart.
>
> What it shares outright: `ReplayChart` whole (so the developing NY profile, the
> viewport profile, the VWAP bands, the IB boxes, `IndicatorLegend`, the ⚓/ruler
> tools, the order primitives, the long-press ticket and the mobile pointer
> handling all arrive with it), `SimIndicators` for the day-scale ATR/range-budget
> strip, `TimeframeControl` with the same four-primary/⋯ split, the `sim-quick`
> market buttons, and the `sim-rail` panel-and-pin. The setup drawer opens off the
> title exactly as the Simulator's session setup does — the rule that fell out of
> it is *anything you touch while watching lives in the bar; anything you set once
> lives behind the title*.
>
> Deliberately different, and each one traceable to a property of the clock rather
> than to unfinished work:
>
> - **No transport.** `liveSource` reports `canSeek`/`canRewind`/`canSetSpeed`/
>   `canStepBar` false, and `truncateLog` is *never imported* on this page. A live
>   fill happened; un-happening it would be a lie about the session.
> - **No `SimPrefs`.** Replay persists a day, a start time and a speed. Live has
>   none of those — its "session" is whichever feed is running, which is server
>   state and already on `/live/status`.
> - **No composite / context days.** Live plays one growing session with no prior
>   days glued to its left, so the composite has nothing to be built over. ~~Absent
>   by construction, not by omission.~~ **Wrong reason — corrected in item 5.**
>   Nothing about a live clock forbids prior days to the *left* of the current
>   one; what forbids it is that no endpoint serves a *recorded* day as tape.
>   The absence is a missing reader, not a property of the session.
>
> Two things the scoping got wrong:
>
> - **`useFillHeight` no longer exists.** The third bullet warned about keeping a
>   JS-measured height in sync; the chart-maximal layout removed the chrome that
>   made measuring necessary, and `.sim-page` is now `100dvh` in CSS. The hazard
>   was deleted rather than handled. (One stale comment in `LiveChart.tsx` still
>   claimed `--sim-fill-h` was load-bearing — corrected.)
> - **Sharing did not have to wait on the hook decomposition.** The second bullet
>   said live-plan item 1b was the work that makes sharing cheap, and to sequence
>   after it. It is still unfinished, and sharing happened anyway: the seam that
>   mattered was `lib/tapeSource` (where the clock comes from, whether the tape
>   ends, what you may do to it), which already existed. The hook split is still
>   worth doing; it was not the blocker.

- [x] Inventory what Replay has that Live does not — done, and most of it came
      free with `ReplayChart`. The genuine absences are the three above, all
      properties of a live clock rather than gaps.
- [x] Decide what is genuinely shared vs. deliberately different — shared by
      default, with the transport as the one hard line. See the `lib/tapeSource`
      note above on why this did not have to wait on the hook decomposition.
- [x] ~~`.sim-page` heights are measured in JS (`useFillHeight`)~~ — obsolete.
      The hook is gone repo-wide; height is `100dvh`.
- [x] Keep the suite's standing rule: **context, not signals**. Held. Everything
      that landed is drawn context — profiles, bands, IB, the ATR/range budget —
      and the one panel that carries strategy output is the shadow rail, which
      reports what the shelf *believed* and cannot route anything.

Verified: three follow-up commits (`c06c3e1`, `cd856ee`, `ceb5b75`) are
browser-found layout faults on the first cut — mobile, the pinned panel taking a
grid row, and three CSS rules stranded inside a media query. Note the revamp cost
Replay ~70px: it was already fullscreen-on-mount, so the top bar is chrome it did
not have before, while Live gained everything.

### 4. Inventory the backfilled days — DONE (2026-08-06)

Harvested/recorded sessions were invisible in the UI.

> **Scope correction (2026-08-06, after the fact.)** This item was filed as
> "display backfilled days" and built as an **inventory** — what tape is on
> disk, where the holes are, what expires when. That was not the ask. The ask
> was to *draw* those days on the chart, so the live page is not stranded with
> only the current session's bars. That is now **item 5**, and it is open. What
> shipped below is still worth having and stays done; it answers "what have I
> got", not "show it to me".

**Most of the server work was already done.** `GET /live/recordings` already
listed every recorded session newest-first with rows, chunks, closed flag, last
tick and the recorder's `stats` — and **nothing in `frontend/` called it.**
`src/journal/live/harvest.py` fills the days nobody was connected for and stamps
`harvest.complete` in the manifest.

> **Done.** `frontend/src/components/TapeCoverage.tsx`, mounted twice off one
> component: as a rail panel beside the running chart, and full-width on the
> no-session setup screen — which is arguably its more important home, since
> *"what have I got, and what is about to become unfetchable"* is a question you
> ask **before** connecting. The rail holds one panel with two views rather than
> two panels: a second `.sim-panel` would stack in the unpinned overlay and fight
> for the same column when pinned, and coverage is something you consult, not
> something you watch.
>
> The endpoint grew what the UI could not honestly derive: `kind`, `signals`
> (the journalled slugs), `shadow`, `clamped` and `unrecorded_rows` lifted out of
> `stats`, plus a `contracts` block carrying the deadline. Reads are a directory
> walk, a manifest and a glob — no tick file is opened — because a page polls it:
> **12–14ms for 40 recorded days** against the real store.
>
> Three things the scoping did not anticipate:
>
> - **`source` is the last writer, not the day's provenance — and the sweep was
>   destroying the evidence.** `heartbeat` rewrites `session.json` whole, so a
>   day that was watched and later gap-filled came back stamped
>   `source: "harvest"` with its `shadow` mark gone: indistinguishable from a day
>   nobody was ever connected for. **2026-08-05 in the real store is exactly this
>   day.** Fixed at the writer (`harvest_day` now carries the prior manifest's
>   marks forward), and the classifier reads the evidence in order of how much it
>   can be trusted — a signal journal first, since it survives any number of
>   manifest rewrites. Four answers, not two: `watched`, `filled` (watched then
>   repaired), `harvest`, and **`unknown`** for days recorded before the fix,
>   where guessing "harvested" would put a clock claim on a day that has not
>   earned one.
> - **The deadline is two ceilings, not one, and only one of them is a date.**
>   The 120-day floor *slides forward daily*, so a session ages out on a rolling
>   basis long before the contract rolls; expiry is the cliff behind which
>   nothing is recoverable at any depth. The panel states both, and the warning
>   names the count that dies on the specific date.
> - **Contract expiry needed a whitelist, not a formula.** Third-Friday is the
>   CME *equity-index* rule; the energy and metal roots settle nowhere near it.
>   An unknown root gets **no** expiry, which the panel says — a plausible wrong
>   date on a deadline nobody can re-check after it passes is worse than none.
>
> What it turned up on the real store, immediately: **46 of the 86 reachable
> sessions have nothing recorded**, in one contiguous block from 2026-04-08 to
> 2026-06-10 (the deep harvest only ever went back to 06-11), and **NQU6 expires
> in 43 days**. That is the item working as intended on its first run.

- [x] Render the recordings list on the Live page — day, rows, kind, partial
      flag, plus a per-session coverage strip drawn over the **reachable window**
      rather than over what exists, because the holes are the point. The strip's
      calendar comes down from the server (`missing_dates`) rather than being
      recomputed in TSX: the weekday/holiday reasoning is subtle enough to have
      in one place.
- [x] Distinguish **watched** from **harvested** — see the `_kind_of` note above.
      The journalled slugs are listed in a tooltip, and an empty list on a
      harvested day reads as the honest absence it is.
- [x] Show the harvest deadline where it can be acted on — per contract, with
      days-to-expiry going orange at 30 days and red at 7, and an explicit
      "deep-harvest before it rolls" line naming how many sessions die on that
      date. Computed server-side (`harvest.replay_window`): it is arithmetic over
      a *measured* property of the service, not a display choice.
- [x] Surface `stats.clamped` — its own field on every row, gold, with the "tiny
      is ordinary, large is a finding" reading in the tooltip. `unrecorded_rows`
      came along for the ride since it sits in the same dict and is the one hole
      that **cannot** be repaired by fetching again.

Verified: 10 new tests in `tests/test_live_record.py` §coverage — the contract
parse (including the root the feed's own guard rejects), third-Friday expiry and
its whitelist, the window arithmetic (floor, days-left, today-is-not-a-hole,
negative days after expiry said plainly rather than clamped), all four `_kind_of`
cases, the gap-fill mark carry-forward, and the endpoint's provenance/deadline
fields. Suite: 486 pass, the same three pre-existing `test_sim_charts.py` WIP
failures. Endpoint checked over HTTP against the real 40-day store; frontend
typechecks and builds. **Not yet opened in a browser** — no headless browser on
this host, so the layout of the rail panel at rail width is unverified.

### 5. Draw the recorded days on the live chart — DONE (2026-08-06)

**The original intent behind item 4.** The live chart holds exactly one
session — today's, growing. Scrolling left runs out of tape at the Globex open.
Every prior recorded day is sitting in `data/live/ticks/`, and none of it is on
screen.

> **Done.** A week by default (`HISTORY_DAYS_DEFAULT = 5`, selectable 0–10 in the
> setup drawer). Two endpoints and a seed; no new chart.
>
> **The seam turned out to be the growable tape, not `concatTapes`.** `Tape.n` is
> already independent of the typed arrays' length, so prior days are copied in as
> a **prefix at construction** and the live rows append behind them —
> `createGrowableTape(tickSize, pointValue, context)`. `ReplayEngine` needed no
> change at all: it already binary-searches its session start against whatever
> tape it is handed and draws everything before it as context bars, which is the
> path the Simulator's `concatTapes` feeds.
>
> **Why seeding rather than splicing, and it is load-bearing:** an order's `idx`
> and the ladder's snapshots are *positions in that array*. Context that arrived
> later and shifted everything right would silently renumber every fill already
> recorded. So context is a **precondition of starting the tape** — `useLiveTape`
> is gated on the history having settled — not something added to a tape already
> growing. The same fact has a UI consequence that the scoping missed: changing
> "Prior days" re-seeds, and `onReset` clears the blotter with it. The control
> **locks once the blotter has anything in it** rather than discarding paper
> trades as a side effect of a reading choice.
>
> Three things worth keeping:
>
> - **The cache-first rule does real work, immediately.** `_history_source` tries
>   the Databento cache then the live store, per day, mirroring
>   `journal.sim.weekly.session_sums`. A week behind 2026-07-06 comes back as two
>   cached days and three recorded ones. It is not a hypothetical either: the
>   **fake feed replays a cached day**, so a simulated session's context is
>   entirely in the cache while a Rithmic session's is entirely in the live store.
> - **`missing` is reported, not skipped.** A test written for this caught the
>   sharper case: asking for 2 days when the day *immediately* behind the session
>   is unrecorded still reports that hole, because what gets drawn is then not
>   contiguous with the live tape. Satisfying the count is not a reason to go
>   quiet about a gap.
> - **The composite became reachable rather than allowed.** Item 3 listed it as
>   deliberately absent; it was absent because it is built over context days.
>   It now switches on with them (frozen at the prior close, as ever) and off when
>   there are none.
>
> Cost, measured against the real 40-day store: the index is **14ms**; a day is
> **~1.65s and 4.1MB** for 510k prints, so a cold week is ~20MB and ~8s before the
> session's own tape starts. That is why it is a control and not a constant, and
> why the loader caches decoded tapes across changes.

- [x] Add the reader endpoint — `GET /live/history/session`, same payload shape
      as `/simulator/session` (minus `default_start_ms`: a context day is drawn,
      never played), plus a `source` field naming which store answered.
- [x] Decide which store answers — cache-first, per day, as reasoned above.
- [x] A skipped day is visible as a skip — `GET /live/history/days` returns
      `missing` alongside the days, and the setup drawer shows
      "N unrecorded" (orange) separately from "N unread" (red). The two look
      identical on the chart — a shorter chart — and the difference matters:
      one is a hole in the store, the other is a request to retry.
- [x] Default lookback is a preference, not a constant — 0/1/2/3/5/10.
- [x] The composite follows, built from prior sessions only.
- [x] Context, not signals. Held: prior days are drawn tape and nothing reads
      the shelf off them.

Verified: 5 new tests in `tests/test_live_record.py` §the days behind the live
one — the cache-first resolution through all three states, the weekend/hole walk,
the early stop and its gap, the encoded day, and the 404. Suite 491 pass (the
same three pre-existing `test_sim_charts.py` WIP failures). Both endpoints
exercised over HTTP against the real 40-day store; frontend typechecks and
builds. **Not yet opened in a browser** — same gap as item 4, no headless browser
on this host.

### 6. Journal the trades taken on the live chart — DONE (2026-08-07)

Until this, the one surface in the app where you actually trade was the one
surface whose trades it never saw. Paper trades lived in the browser and died on
a reload; the broker's round trips lived in memory and in an append-only
`orders.jsonl` that nothing read back.

> **Done.** `src/journal/live/booking.py` is the single place that knows the
> row shape; the broker books its own round trips, and paper trades are POSTed.
>
> **The feature is short because there is no `trades` table.** A journaled trade
> is *derived at read time* from `atas_journal` (matched lots) by
> `trades.build_logical_trades`, funnelled through `api/scope.py`. Write a
> correct row and the Trades page, Calendar, Statistics, AI review, notes,
> setups, models and video bookmarks all work with **nothing else changed**.
>
> **Two writers, and the asymmetry is forced.** The fill engines are in
> different places: a real trade is netted by `Broker` on the server, so it
> books itself; a paper trade is computed by `replaySim.ts` in the browser, and
> `api/routers/replays.py` is explicit that the server must never recompute one
> ("one engine, so a stored attempt can't disagree with the replay that produced
> it"). So paper arrives by `POST /live/journal/paper`.
>
> **Paper is an account, tagged `replay`.** It appears in Trades and the
> Calendar and filters by account like any other, and `sessions.mode='replay'`
> — the same mechanism ATAS's own `Replay` account already uses — keeps it out
> of real-money statistics unless asked for. Not behind `LIVE_ROUTING` or an
> gates: paper reaches no broker, so none of the routing gates are about it.

- [x] `source_file` is the sitting: `live/<account>/<date>`, under a prefix that
      cannot collide with a root-level `Export_*.xlsx` or `backtest/<model>/`.
      `db.delete_attempt` then deletes a live day with no new code.
- [x] The session row is written **before** the trade. `api/scope.py`'s
      `DEFAULT_SESSION` makes an unregistered `source_file` read as
      `mode='replay'`, so a real trade booked without one would quietly leave
      the real-money statistics — invisibly. There is a test that demonstrates
      that failure rather than just asserting against it.
- [x] `dedupe_key` reuses `ingest._journal_key` verbatim, so re-posting is free
      and the paper client can be careless about retries. Prices are rounded
      once, in the builder, because the key hashes them as strings.
- [x] Booking can never raise into the fill path. It runs inside the
      notification handler on the feed's event loop — the thread that would be
      servicing a cancel or a flatten — so a failure is counted
      (`booking_errors` on the panel) and the `orders.jsonl` line still carries
      the trade for a later backfill.
- [x] Fills go to `executions` for the trade-detail chart's markers, keyed on
      Rithmic's `fill_id`. Best-effort: an unkeyable fill is dropped rather than
      given a synthetic id, and the read path already degrades to "no markers"
      rather than a wrong number.

**One property worth knowing, because it looks like a bug and is not:** the
broker emits a round trip each time size comes off, so a scaled-out position
produces two of them sharing an entry. Written as two lots, `build_logical_trades`
nets them back into a single logical trade of the full size — which is what the
position was, and how an ATAS export of the same scale-out reads. The lots stay
separate underneath. There is a test pinning it.

Verified: 22 tests in `tests/test_live_booking.py`, every assertion made through
`api/scope.py` rather than a SELECT — the row is not the product, what the Trades
page shows is, and a lot of derivation sits between them.

> **A mistake worth recording.** The first version of the routing suite's autouse
> fixture did not redirect `journal.db.connect`, so the fill-pairing tests wrote
> **eight fabricated trades into the real journal**, tagged `mode='live'`, where
> they would have counted toward real statistics. Found by checking the real DB
> after a green run, removed with `db.delete_attempt`. The fixture now redirects
> the connection — booking still really runs, it just cannot reach real data —
> and there is a test asserting the redirect is in place, because if it ever
> silently stops working every test below it starts writing to the real journal
> again.

- [ ] Confirm `fill_id` against a real plant. If it is blank or not unique per
      fill, `book_fill` returns None and the only cost is missing markers — but
      the mapping is currently schema-derived, like the rest of the write path.
- [ ] Decide what happens if an ATAS export covering a day traded here is ever
      imported. By decision (2026-08-07) the two sources are disjoint, so there
      is no reconciliation — both copies would land and statistics would
      double-count. A warning on the Trades page is the cheap mitigation.

### 7. *(open slot)*

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
