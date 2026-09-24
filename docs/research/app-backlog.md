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

## Charts — `/charts/replay`

### 1. Prop-firm account simulator — pick the plan, not just the size

*Added 2026-08-18.*

The replay account is currently **one hardcoded plan**: LucidPro 50K, in
`src/journal/replay_account.py` (`START_EQUITY 50_000`, `MAX_LOSS 2_000`,
`TRAIL_CAP 52_100`, lock $50,100, EOD trail). Make the plan a *choice*, the way
Live's `RoutingPanel` makes the account a choice — a selector in the replay rail,
the picked plan drawn as a badge everywhere the account appears, and the floor
computed under that plan's rule.

**Scope decision (2026-08-18): two plans, LucidPro 50K and LucidDaily 50K.** Not
the size ladder, not a generic multi-firm spec table — the two plans actually
being chosen between. Same $50K start, same $2,000 MLL, same $52,100 buffer and
$50,100 lock; **the only thing that differs is when the floor is allowed to
move** — LucidPro steps it once at the daily close, LucidDaily tracks the running
peak. Build the spec as a table anyway (one dict per plan) so a third row is data,
not a refactor.

**Switch decision (2026-08-18): switching plans mints a new epoch.** An account is
bound to its plan for its life. `data/replays/account.json` epochs gain a `plan`
field (absent ⇒ `lucidpro`, so every existing epoch reads correctly); the picker
writes a new epoch at $50K rather than re-deriving history. No retro-death when
you switch — and, deliberately, no "where would I be under the other plan"
comparison. If that comparison is wanted later it is a *read-only shadow walk*,
never a second account of record.

**Why LucidDaily is worth simulating at all** — from the plan comparison and the
measurement run in `data/research/replay-trail/intraday_dd.py` (the study has no
write-up; these numbers are the residue):

- Intraday trailing charges you for **profit you touched but never banked**. Over
  the 66 stored sittings at flat 1 NQ, the extra floor room it demands over an
  EOD trail is a **median $232, mean $320, p90 $760, max $1,232**; 18/66 days
  (27%) cost more than $500. Effective MLL under an intraday floor ≈ **$1,768 on
  a median day, $1,240 on a p90 day**, of $2,000 — an 8-loss budget becomes ~7,
  and ~5 on the bad tail.
- **The daily stop does not defend against it** ($232 → $198 median). The tax is
  manufactured by the green part of the day; a rule that only watches losses
  cannot see it. If Daily is ever armed live it needs a *new* guardrail — a
  giveback-from-peak lock — not a tighter loss stop.
- Whether the trail watches **open** trades or closed balance only moves the
  median ($232 vs $0) but barely the tail (p90 $760 vs $593, max $1,232 both).
  Lucid's own sources contradict each other on this; the measurement says the
  answer is not load-bearing.
- Against that cost Daily drops the 40% funded-consistency rule (and with it the
  `+$1,000 daily profit lock` that exists only to feed it), the 3-day payout
  cycle and the $2,000/$2,500 payout caps. Its own extra cost is the red-folder
  news rule: a **hard breach**, not a session lock.

- [ ] Lift the plan constants out of `replay_account.py` into a spec table
      (`start`, `max_loss`, `trail_cap`, `lock`, `trail: "eod" | "intraday"`,
      daily-loss rule, consistency rule). `walk()` takes the spec; everything
      that currently reads a module constant reads the spec instead. Keep
      `FAST_TRADE_MS` where it is — it is behaviour, not plan (and it is
      duplicated in `lib/guardRules.ts`; they must stay equal).
- [ ] Decide what the intraday floor marks against **before** building it. The
      server walks `trade_pnls(row)` cumulatively — a *closed-balance* path, which
      is exactly the cheap reading the measurement says is tail-equivalent.
      Marking open positions needs the tape, which the server does not have;
      the honest options are (a) closed-balance only, stated in the UI, (b) the
      recorder stamps a per-sitting peak-equity mark, (c) per-trade MFE from the
      stored excursions. **(a) first** — it is one line of the same walk, and the
      p90/max are unchanged.
- [ ] **`guardRules.accountStop` assumes the floor is constant for a sitting.**
      That assumption *is* the EOD rule, and it is the only reason the browser can
      join live equity to a server-derived floor. Under an intraday plan the floor
      climbs with every new equity high, so the client must recompute it live —
      this is the real work in this item, not the plan table.
- [ ] Simulate the floor and the daily rule; **show** the rest. Consistency
      percentages, payout cadence and caps, and the news rule are policy, not
      path — they belong in the account panel as text next to the plan badge, not
      as silent refusals. A red-folder breach in particular cannot be enforced
      without an econ calendar, and inventing one that half-works is worse than
      printing the rule.
- [ ] The account panel should read: plan badge, equity, floor, **distance to
      floor**, and (intraday only) the peak the floor is trailing. The peak is the
      number that explains a death nobody remembers earning.
- [ ] Consider writing the plan comparison up as
      `docs/research/luciddaily-vs-lucidpro.md` so it renders in Lab → Research —
      it currently exists only in a transcript and in the header comment of
      `intraday_dd.py`.

### 2. Trailing sitting-profit lock — the give-back floor

*Added 2026-08-21.*

The give-back guardrail the LucidDaily notes above call for, measured and worth
building. The rule, exactly as tested: **per sitting**, once cumulative PnL
touches **+$500**, a floor arms at **$0**; each further +$500 milestone the peak
touches ratchets the floor up $500 (peak ≥ $1,000 ⇒ floor $500, and so on). Cum
PnL at or below the floor ends the sitting. Never armed below +$500 — days that
start red belong to the ordinary daily loss rule, not to this.

**Why $500 and why at all** — measured on the full mirrored history (106
sittings, 1,060 trades, walk at trade closes): actual **−$21.3k → −$8.5k**
(+$12.9k). Fired 16 times: **12 saves +$14.6k vs 3 lockouts −$1.7k**; worst
single lockout −$873, best single save +$3.6k. The asymmetry is structural — a
lockout can only forfeit remaining upside, a give-back can run from +$1.3k to
deep red, and the reads *measured during* give-backs are degraded (46% right at
30s after a loss vs 55% after a win; this-week analysis, 2026-08-21). A $250
step recovers more (history → breakeven) but fires in a third of sittings and
its lockouts cost 4×; $500 keeps an 8.5:1 save-to-cost ratio.

- [ ] **Show before enforcing.** First cut is a read-only line — armed state,
      current floor, distance to it — on the replay account chip/panel, next to
      the plan floor. A silent refusal that fires 15% of sittings needs trust
      the number hasn't earned yet.
- [ ] Split-half the history (early vs late sittings) before wiring any
      enforcement. The +$12.9k is one August-heavy sample; the study lives in
      scratch only — port the walk (trivial: one cumsum + ratchet per sitting)
      somewhere it can re-run as sittings accrue.
- [ ] It marks **closed PnL**, same seam and same honest options (a)/(b)/(c) as
      the intraday-floor decision above — do not build a second answer to the
      same question. Closed-balance first, stated in the UI.
- [ ] Ownership: this is a *sitting* rule, not an *account* rule — it applies
      identically to paper, drill and account sittings, so it belongs beside
      `guardRules` / the sim session walk, not inside `replay_account.py`'s
      plan spec. The plan floor answers "is the account alive"; this answers
      "has this sitting stopped earning its keep".
- [ ] If enforced: end-of-sitting lock, not a trade veto — the tested rule cuts
      the *rest of the sitting*, and that is what the numbers priced. No
      trade-by-trade exceptions, or the measurement no longer applies.

### 3. Continuous-run replay — random start, then the next day

*Added 2026-09-24.*

Replay today drops you on a random cached day every sitting
(`Simulator.tsx` `anyDay`: uniform over ~600 days, with replacement, no memory).
The idea: draw the **start** day at random as now, but once that day is ended,
the next sitting opens on the **next trading day** after it, not another random
draw. You trade the era continuously — this week's regime into next week's, the
losing streak that lasts a fortnight, the Monday after a Friday blow-off —
which is what live trading actually feels like, and what a prop account's
consecutive-day floor is actually measured against.

Why it matters beyond feel: every account sim in `bracket-survival.md` (and the
2026-09 Flex/Direct/budget runs) draws days **independently**, which assumes
regimes don't cluster. They do. A run of sittings through consecutive days is
the only in-app sample of how the trader handles serially correlated days.

- [ ] A run is a first-class thing: `(symbol root, start date, cursor)`,
      persisted like the `sim.resume.*` bookmark (per mode). "End day" advances
      the cursor; a fresh draw is an explicit "start a new run", never implicit.
- [ ] Next day = next **cached** RTH session after the cursor, across the
      contract roll (resolve via `tickmod.cached_*`, not a root glob — see the
      replay-tape two-stores trap). Skip holidays/missing tapes, but surface the
      gap ("skipped 2 days — no tape") rather than jumping silently.
- [ ] Blindness: the date stays hidden until reveal, as today. Knowing it's
      "the day after" is fine; knowing the calendar date is not.
- [ ] **Show the weekday** (Mon/Tue/…) up front, even while the date is
      hidden. A live trader always knows it — Monday opens off a weekend gap,
      Friday afternoons thin out — and in a continuous run it also tells you
      when a weekend (or a holiday skip) sits between two sittings. The
      weekday alone doesn't give the date away.
- [ ] Prior-day context (`contextTicks`, composites, prior VA) must come from
      the real preceding days — it already does per day, just check it holds
      at a run boundary.
- [ ] Replay accounts: a continuous run is the natural fit for an account's
      life — consecutive tape days instead of random ones. Decide whether an
      account *is* a run (one cursor per account) or runs are independent of
      accounts. Leaning: account-bound, since the account's day already is
      the tape day (`replay-account-registry`).
- [ ] End of corpus: the cursor hits the last cached day → say so and offer a
      new random run; don't wrap.
- [ ] Drill mode keeps random drops — it's for reps, not continuity.

---

## Journal / review

### 1. Add the Modern VWAP POC anchors to the auto-derived levels — DONE (2026-08-18/19)

*Added 2026-08-18. Built the same day; the second re-arm rule added 2026-08-19.*

**The result, first: fills do not cluster on either line.** Over the backfilled
journal (995 scored fills carrying the family, 69 days):

| family | mean rank | median | at level (<0.05) |
|---|---|---|---|
| `value_low` — tightest on the board | 0.405 | 0.36 | 13.0% |
| `mv_poc_revisit` | 0.473 | 0.48 | 9.3% |
| `mv_poc_naked` | 0.483 | 0.48 | 8.0% |
| `trend_ema` — the null control | 0.501 | 0.48 | 6.9% |

Both POC anchors sit in the bottom two of the nine families, a hair inside the
control. `mv_poc_revisit` is marginally the tighter of the pair, and its entries
alone rank 0.463 / 10.1% against 0.482 / 8.0% for `mv_poc_naked` — but at ~0.01
per standard error on the mean that is a difference worth naming and not one
worth acting on, and neither line is remotely in `value_low`'s company. So both
are very nearly "a level nobody trades off", measured on my own fills. That is a
fact about where I fill, not a verdict on the indicator, and it is exactly the
answer this item was built to be able to get.

The level tagger scores every finished sitting's fills against the families in
`src/journal/level_tag.py::FAMILIES` (bands, value high/low/mid, session mean,
EMA — 7 before this item, 9 after). Add the **Modern VWAP anchored at the POC**
(`anchor: "poc"`, `rearmTicks: 50`) under **both** re-arm rules as levels it can
measure fills against: `rearmMode: "pocMove"` ("naked POC" — the POC has to
migrate) and `rearmMode: "distance"` (the chart's "price leaves the POC").

**The blocker is that Modern VWAP is frontend-only.** It lives in
`frontend/src/lib/modernVwap.ts` and is drawn through the shared `modernVwapLayer`
on `/charts` and `/lab/interactions`; there is **no Python implementation**, and
`level_tag` reads its levels off `api/session_chart.py::session_frame` slots. So
this item is mostly *port the anchor logic server-side*, and only incidentally a
family-map edit.

- [x] Port the anchor + re-arm rules to Python and expose them as `session_frame`
      slots. The rules to reproduce exactly: anchored at the developing POC; once
      fired the anchor **disarms**, and re-arms either when the POC has migrated
      ≥ `rearmTicks` from the price it last anchored at (`pocMove`), or when a
      bar closes ≥ `rearmTicks` from the POC *as of that bar* (`distance`).
      Causal — fires on bar close, no lookahead. Cross-reference the TS file in
      both directions the way `FAST_TRADE_MS` is cross-referenced.
      → `src/journal/sim/modern_vwap.py` (mid line only — no bands, no regime, no
      signals; both modes at 50 ticks, pinned in code rather than read from a chart
      knob, because a family whose definition moves with a user setting cannot be
      compared across trades; `mode` is a required argument that raises on an
      unknown value, so no caller can silently measure against the wrong line).
      Exposed as `SessionFrame.mv_poc_naked` / `mv_poc_revisit`, `{time, value}`
      rows off the **Globex** developing profile, which is the indicator's own
      `pocSource` default. The two are genuinely different lines: on real sessions
      `distance` fires 49-74 anchors to `pocMove`'s 10-12, and they separate by up
      to 83-179 points.
      **Parity is tested, not asserted:** `tests/test_modern_vwap.py` transpiles
      the TypeScript with the frontend's esbuild, runs it under node on a shared
      600-bar fixture, and compares the two mid series — it *skips* when node or
      esbuild is missing rather than passing silently.
      A session with no developing profile gets **no line**, where the frontend
      degrades to a plain session anchor: that fallback is fine for a drawing and
      false for a level (it is the session VWAP under a name claiming otherwise).
- [x] Decide **own family vs joining `session_mean`**. It is a VWAP, so
      collinearity with `vwap`/`gxvwap`/`wkvwap` is the obvious worry — but an
      anchor that jumps to a migrating POC is a different line from a session
      integral, and folding it into `session_mean` would hide it (a family's
      distance is to its *nearest* member). Default to a new family, with a
      `FAMILY_LABELS` entry, and let the rank machinery answer the question:
      `trend_ema` sitting at 0.500 is the built-in null control.
      → **Own family per re-arm rule**, `mv_poc_naked` and `mv_poc_revisit`, one
      member each. The three session means are one line asked from three start
      times; these restart wherever the day's agreed price last moved, so on a
      trending day they sit nowhere near them. Folded into `session_mean` either
      could have won the family on days the session VWAPs were far away and read
      afterwards as "traded off VWAP". And the two are kept apart from *each
      other* for the same reason at a smaller scale: pooled, a fill near either
      would score as "near the POC anchor" and neither rule could ever be shown
      to be the better one — which is the only comparison this pair supports.
- [x] Bump `METHOD` (currently `v1-drift20-pool600-n25`) — the family map moved,
      so every stored rank is stale — and re-run `demo/level_tag_backfill.py`
      over the 6,657 rows / 679 trades / 64 days.
      → `v3-drift20-pool600-n25` (v2 was the first rule alone). Backfill re-scored
      **737 logical trades / 1,474 fills / 69 days**, writing 8,955 rows (448
      mechanical exits skipped as always). 4 days skipped for no cached tape or a
      tape that disagrees with the fills (2025-01-30, 2026-06-15, 2026-08-10,
      2026-08-12).
      The v2 run left 32 rows behind under the old stamp, which is a bug the
      whole-trade replace cannot catch: those rows belong to two trade keys that
      no longer exist (a re-import re-cut them), so nothing re-scores them and
      nothing deletes them. `db.prune_trade_levels` now drops rows by stamp at the
      end of a **whole-journal** backfill only — a since-date run would delete good
      rows for trades it never looked at. `trade_levels_methods` is a single method
      again.
- [x] `LevelStrip` in `TradeDetail.tsx` renders from the families, so it picks the
      new ones up for free — check the label fits the strip at the widths the
      revamp (item 3) settles on.
      → It renders from its own `FAMILY_LABELS` copy and falls back to the raw key,
      so it needed the two entries: "naked-POC VWAP" and "revisited-POC VWAP", the
      longer of them 18 characters against the existing "value low (VAL)".
- [x] Keep them look-at-only in their claims. The POC anchor itself is
      **unvalidated** — measuring where fills land is exactly the use it was built
      for, and is not evidence the level works.
      → Still unvalidated, and now with a measured base rate that gives it no
      support either: see the result at the top of this item.

### 2. Scrap the model and confluence fields from the review — DONE (2026-08-18)

The review asked each booked trade for a **model** (tri-state select, "no model"
is an answer) and **≥1 tag** (placeholder "confluence, signs…"), plus an optional
note. Both are gone.

**The reason is that the levels are now derived, and derived beats declared.** The
tagger measures which level families a fill actually landed on, per fill, without
a self-report — and those families are more specific than the model names in use
today. The confluence field was asking a human to type, from memory, a worse
version of a number the app already computes.

> **Scope grew once, and the growth is the feature.** As filed, this item ended
> at "notes only", and accepted the loss of the claimed-vs-measured comparison as
> the price. That was the wrong trade, and the sharper rule is: **drop the
> declaration that duplicates a measurement; keep the declaration a measurement
> can score.** Confluence failed both halves — a worse copy of `trade_levels`,
> and unfalsifiable ("I saw the VAH and the 9 EMA" is never wrong). What replaced
> it is a **thesis**: `bounce`, `break` or `revert`, from a closed vocabulary,
> claiming what price will do next. That duplicates nothing and the tape settles
> it.
>
> **The hazard, and it is fatal if unhandled.** Asked at review time, a thesis is
> chosen knowing the outcome: it grades near-perfectly and teaches nothing.
> Grading a contaminated claim is *worse* than not grading, because it
> manufactures evidence. So the claim is collected **at the ticket**, one
> optional click before the order goes out, and every stored row carries
> `captured: 'entry' | 'review'`. The review still asks on trades that skipped
> it — a retrospective claim beats no record — but the two must never be pooled,
> and any accuracy quoted over a mixed set is a number the hindsight invented.
>
> **Never required at the ticket.** The chart gestures exist to be fast, and a
> mandatory field on the fast path is one that gets answered carelessly. What
> skipping it costs is one question in the review, which is where the slow
> version belongs.
>
> **What the grade actually says.** Whether the *market* did what you claimed —
> never whether the trade made money, and it does not read the P&L at all. That
> separation is the whole point, because it exposes the two disagreements a P&L
> column hides: **right thesis / bad execution** and **wrong thesis / lucky
> money**. Neither was visible before.
>
> **It needed no new measurement.** `trade_levels.dist_ticks` at the entry anchor
> locates the level, `journal.excursion` gives the hold's MAE/MFE, and
> `trade_context.pre_run_pts_5m` says whether the approach was faded or joined —
> which is what separates a bounce claim from a break claim *before* the outcome
> is consulted. `bounce` and `break` turn out to be the same geometric claim from
> opposite directions (both say "this level now holds", both die when price
> trades back through it); only the setup axis tells them apart. `revert` is
> graded on MFE reaching the named target, not on the exit — closing early is an
> execution decision the exit measurements already judge.
>
> Five verdicts, not two: `confirmed`, `refuted`, and then `incoherent` (the
> level is on the far side of the entry — the market was never asked),
> `unlocatable` (the fill was not unusually close to anything, so "the level" has
> no referent) and `unmeasured` (the background measurements have not landed, or
> the day was never cached). Collapsing those three into "refuted" would file
> mis-clicks and cache gaps as evidence that the trader reads badly.

**Gate decision (2026-08-18): a thesis and a non-empty note per booked trade.**
Every settled rep with ≥1 booked trade still parks at `finished` until each trade
has both; zero-trade reps still auto-pass. Usually the thesis is already there
from the ticket, so what is owed is the note — written with the machine's verdict
on your own claim in front of you, which is the one moment the prose is worth
typing. The forced pause between sittings survives.

- [x] This **inverts V7**. The server gated on tags precisely because `model_id`
      NULL cannot distinguish off-model from never-answered, which forced the
      model choice client-side. Neither new field has that problem — a thesis row
      exists or does not, a note is empty or is not — so the server gates the
      whole review (`_unanswered`) and the client-forced tri-state is gone.
- [x] Touch list, as built: `ReviewPanel.tsx` (model select and tag picker out,
      `ThesisRow` in — exported, because `DrillReview.tsx` renders the same
      thing), `api/routers/replays.py` (`_untagged` → `_unanswered`, both 409
      messages, `_record_intent`), `api/routers/notes.py` (`PUT /intent`,
      `GET /intent/vocab`), `db.trade_intent`, `journal/intent.py`, and the
      thesis stamped on `OrderRec` → `Position` → `Trade` in `replaySim.ts`
      exactly as `micro` and `trail` are.
- [x] **Do not delete the columns or the vocabulary.** Held: `GET /notes/tags`,
      `model_id`, `setups` and `confluences` are all still written — the review's
      save echoes every one of them untouched, because the Trades page and the
      journal read them and a form that stopped asking is not a licence to blank
      what was written elsewhere.
- [x] Restate `level_tag`'s design decision 1 for the new gate. The tagger must
      never satisfy the gate — automatic now (the machine writes no prose, and it
      cannot supply a claim either). `test_measuring_does_not_satisfy_the_review_gate`
      asserts both halves.
- [x] ~~Accept, explicitly, what is lost: the claimed-vs-measured comparison.~~
      **Not lost — rebuilt in a form that settles.** The old comparison was
      against a list of levels, which the measurement could only agree or
      disagree with; the new one is against a prediction, which the tape marks.
- [x] Harness: `tools/browser/drillcheck.mjs` unlocks 🎲 through the stateful
      stubs, now split across `/notes` (the note) and `/intent` (the claim), and
      checks the pair — a thesis alone leaves it locked, which is the assertion
      that would catch a server gating on only one of the two.

**The one thing this leaves open**, because it cannot be answered by building:
whether an entry-time thesis is a question worth answering under time pressure.
The click is cheap and skippable by design, so the failure mode is not friction —
it is a picker that sits on `bounce` all session and stamps a claim nobody made.
The chip is coloured when set, and the state is deliberately **not** persisted
across reloads, but only use will say whether that is enough.

### 3. Revamp the trade detail

*Added 2026-08-18.*

`frontend/src/components/TradeDetail.tsx` (354 lines) is now three things stacked
in the order they were built: the journal form, `LevelStrip` after it, and
`TradeRecordingPanel`. It reads as a form with attachments rather than as a view
of a trade.

- [ ] Lead with the trade, not the form. Entry/exit, side, size, R, MFE/MAE and
      the level strip are what the page is *about*; after item 2 the human input
      is one note, which no longer deserves the top of the page.
- [ ] **Wire up the context windows.** Pre-entry / post-exit price is already
      stored per trade in `journal.db` at ~96% coverage and has **no UI at all** —
      a sparkline or mini-chart of the window around the fill is the single
      highest-value thing missing from this page, and the data cost is zero.
- [ ] Decide whether this component and the review card converge. They are asking
      about the same object from two directions; after item 2 they may be the same
      card with a different header.
- [ ] Check it against the terminal-redesign dock's `overflow: hidden` — the same
      clipping that bit the ticket's Σ popovers applies to anything this page
      floats.

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
