# Live shadow mode — build plan

*Written 2026-08-05, updated the same day. Phases 0–6 are now built. Phase 5's code is
complete and tested against a recorded tape; what it has never done is run against
Rithmic, because that needs the always-on host. Phase 6 is built and verified on a
re-recorded cached session; it needs purchased Databento days to run on a real one.
[What's left](#whats-left) is the short version.*

The goal is to watch a live tick feed and show where the registered strategies **would
have** signalled — shadow mode, no order routing. Routing is explicitly out of scope
(see the end of this document for why designing toward it would be harmful).

Accounts are prop-firm, routed through **Rithmic**.

---

## Status

| Phase | State |
|---|---|
| 0 — engine proven prefix-safe | **done, verified** |
| 1a — Charts workspace | **done** |
| 1c — `TapeSource` seam | **done** |
| 1b — `Simulator.tsx` decomposition | **pure half done** (`lib/simViews.ts`, `hooks/useFillHeight.ts`) + **checkpoint ladder done, verified**; hooks not done ([why](#why-1b-was-left)) |
| Rithmic access | **verified 2026-08-05** ([below](#rithmic-access--verified-2026-08-05)) |
| 2 — in-memory session + fake feed + `/live/tape` | **done** |
| 3 — `GrowableTape`, `liveSource`, append-only blotter | **done** |
| 4 — shadow signals on a cadence, frozen regime | **done, verified** |
| 5 — recorder, live-store readers, Rithmic feed, resume | **built, tested off-market** ([below](#phase-5--what-landed)); **history-plant backfill built and verified against the live plant 2026-08-05** ([below](#built-same-day)) |
| 6 — reconciliation | **built, verified on a re-recorded session** ([below](#phase-6--what-landed)) |
| 7 — manual order routing | **built 2026-08-06, rebuilt 2026-08-07 (paper as an account), never driven against a real order plant** ([below](#phase-7--routing-built-2026-08-06-beside-phases-06-rather-than-on-top-of-them)). Off unless `LIVE_ROUTING=1`; starts on paper every session; nothing autonomous can reach it |

Phases 5–6 verification: 33 tests in `tests/test_live_record.py`. The load-bearing ones
are an **end-to-end pass** — record a cached session into the live store, shadow it as it
grows with the journal on, then reconcile all three stages — which reports exact tape
fidelity, zero prefix divergences across the shelf, and 100% P&L-weighted signal
agreement; and its **negative control**, a journal claiming a trade the settled run does
not contain, which the prefix check fails on. Both fidelity directions are covered too: a
tape recorded whole matches, and one recorded with every other print dropped is caught.

**The feed has now been driven against the live plant** — 2026-08-05, NQU6, 45 trades in
18s through `RithmicFeed` with a counting route (no session, no recorder, nothing written).
Every branch did its job: 45 exchange stamps and **zero** falls back to Rithmic's send
stamp, the opening snapshot rejected, 34 LAST_TRADE messages carrying only derived fields
filtered out, no clamps needed, tape monotonic across 23 batches. The tape clock read
**1.1s ahead of the host clock**, which is the drift that makes `source_ssboe` load-bearing
rather than fastidious.

What that still does not cover is the *recorder* under a real feed, the 18:00 roll on a
real night, and a reconciliation against a Rithmic tape. All three need the always-on host,
and it is the only thing left.

**`.env` is not loaded at import, and both new readers assumed it was.**
`config.load_dotenv` sits inside `load_env()`, which every consumer calls before reading
its own keys. `rithmic.credentials()` and `api.main._resume_live` went straight to
`os.getenv` and found nothing however carefully the file was filled in — reporting the
credentials as *missing* rather than as unread, which points the debugging squarely at the
file, where nothing is wrong. Both call `load_env()` now and there are tests for it. The
error also named `RITHMIC_URL`, a variable that does not exist, because it derived the
name from the client's kwarg; the gateway is `RITHMIC_GATEWAY` and the mapping is explicit
now. The same trap waits for the next module that reads an environment variable.

Phases 2–4 verification: 13 new tests in `tests/test_live.py`, of which the
load-bearing ones are that the runner reproduces the backtest trade-for-trade over
a whole cached session (entry instant, average, exit reason and net P&L), that a
regime checkpoint does not move as the day grows, and that the freeze converges to
the settled artifact by the close. Measured cost of one full pass over all
thirteen strategies on a complete session: **4.6s wall**, against a per-strategy
cadence of ~38s at real NQ tick rates — so the shelf runs comfortably inside its
own floor.

The surface itself was checked in a browser (headless Chrome against the dev
server, a fake feed running): the chart fills the viewport, the signal rail sits
in the grid's 300px column, the regime freezes checkpoint by checkpoint as the
clock passes them, and Replay is unaffected by the shared extractions. Worth
saying because it is the one part of this work `tsc -b` cannot speak for — and the
first attempt at the layout looked correct and was not (see the third finding
below).

Full suite: 395 pass. Three failures in `tests/test_sim_charts.py` are pre-existing
and unrelated — `api/sim_charts.py` and its test are both uncommitted WIP, and they
fail identically with that file reverted to HEAD.

Phase 0 verification (377 tests at the time, before the live suite existed), of which 40
are `tests/test_prefix_replay.py`.
That suite was checked against a deliberately reverted `_force_index` and produced 13
failures — every strategy that trades — so it fails when the bug it guards is present.
Stored runs re-simulate identically on entry, exit, exit-reason and P&L. (The only stored
columns that differ are `recovery_s` and `giveback_s`, which predate those engine fields
and are already flagged as needing a re-run.)

---

<a id="whats-left"></a>
## What's left

Five things, in the order they unblock each other. The first two are code and can be done
today; the rest are no longer code at all — they are a host and a small Databento spend.

**1. Commit — and it is bigger than this feature.** HEAD is `c5b8c74`, dated **2026-07-19**.
Since then nothing has been committed: **64 untracked code files, 47 untracked research
docs, 62 modified files.** The live stack is only the newest layer of that. It sits directly
on top of other uncommitted work — `frontend/src/lib/replaySim.ts` and `replayEngine.ts`,
`pages/Simulator.tsx`, `api/routers/simulator.py`, `replays.py`, `drafts.py`,
`src/journal/replays.py`, `tests/test_prefix_replay.py` — so there is no commit that lands
shadow mode without also landing the Simulator, Replay and Drafts surfaces it is built from.

That is a separate job from this plan and probably wants its own pass: group by feature,
check each group builds, and decide what in `data/` should be gitignored rather than
committed (3,491 untracked files there, nearly all cache artifacts). Two specifics worth
knowing before starting: `api/main.py` is one file carrying four features' router
registrations plus the `WATCH_ENABLED` watcher gate, so it will need splitting by hunk
whatever the grouping; and `docs/.observations.md.swp` is a stray vim swap file, not
content.

Until then, a month of work exists only in the working tree. That is the largest risk on
this list, and it is not a technical one.

**2. Finish 1b — the hook decomposition.** `SimToolbar`/`SimTicket`/`SimBlotter`,
`hooks/useReplayPlayback.ts` (rAF loop, play/stop/seek), `hooks/useSimSession.ts` (the
bootstrap effect). `Simulator.tsx` is 2297 lines; target ~600–800 of orchestration. Wants
someone at the keyboard — see [why](#why-1b-was-left), and note that the one hook already
extracted broke in exactly the way that paragraph predicts.

**3. An always-on host.** Still wanted, and no longer the *only* thing between the code and
a whole session: the [history-plant backfill](#tick-replay-history_plant--probed-2026-08-05)
now replays the day from its 18:00 ET open on connect, so arriving mid-morning gives a
complete tape and a complete night on disk. What the host still buys is a day nobody
connected for at all — a contract that has rolled off cannot be replayed at any depth. It is
not a coding task. ~3 MB per session; a $5/mo VPS or a Pi is
ample. Set `LIVE_AUTOSTART=1` and `LIVE_SYMBOL=NQU6` (a **raw** contract — the roll map
ends 2026-06-30) and the API connects and records on startup; leave them unset anywhere
else and it still resumes a day already on disk without opening a socket.

Two things to do the first time it runs, neither of them a build:
   - read the recorder's manifest afterwards — `stats.clamped` says how often exchange
     stamps arrived out of order, and a figure that is not tiny is a real finding;
   - check the aggressor verdict once a Databento day for the same date is bought. The
     mapping is taken from Rithmic's own protobuf enum (`BUY=1`, `SELL=2`), not from the
     two ints the probe happened to see, but whether Rithmic's *aggressor* and Databento's
     *side* mean the same thing is still untested, and every recorded tick keeps `agg_raw`
     so a wrong answer is re-derivable rather than re-recordable.

**4. A handful of purchased Databento days.** Phase 6's stages 1 and 3 compare against
them; without one, `demo/live_reconcile.py` reports `unavailable` rather than a flattering
`ok`. Stage 2 (prefix integrity) needs nothing bought and runs the morning after any
recording.

**5. Loose end.** The ladder's differential harness lives in a session scratchpad because
`frontend/` has no test runner and no home for one. Fine for now; worth landing if a second
piece of pure sim logic ever needs the same treatment.

Phase 7 (routing) is out of scope by decision, not by sequencing.

---

## Decisions

Recorded here because they are the part least recoverable from the code.

1. **Shadow first, no order routing.** The app watches and reports; it never sends an
   order. Prop-firm rules diverge exactly on this line — as of 2026, Apex restricts fully
   autonomous entry *and* exit, and Topstep prohibits automation through the ProjectX API
   on live funded accounts. Read your firm's current written rules before that changes.

   **Amended 2026-08-06, widened 2026-08-07.** Manual order entry exists ([Phase
   7](#phase-7--routing-built-2026-08-06-beside-phases-06-rather-than-on-top-of-them)),
   off unless `LIVE_ROUTING=1`, and the chart's own gestures now reach it — including,
   if an account is explicitly set to one-click, with no confirmation step. What is
   unchanged is the part of this decision that was ever about the firms: **nothing
   autonomous routes.** No strategy, no gate and no shadow pass can reach the broker;
   every order originates in a human gesture, and `manual_or_auto` stays at the
   client's `MANUAL` default — a claim being made to the broker on every order rather
   than a formality.
   
   The sentence "read your firm's current written rules" is now load-bearing rather
   than precautionary, and one-click trading on a funded prop account is the specific
   thing to read them about.
2. **Rithmic, `TICKER_PLANT` only** — *by default, and unless a routing session was
   asked for.* Rithmic splits its API into independent plants (ticker / history / order /
   PnL), each its own socket **but one concurrent session per login**, which is what
   settles the design: a shadow feed opens ticker (plus history when backfilling) and
   nothing else, and a routing feed opens ORDER and PNL **on the same connection**,
   because a second client would force-log-out the first. Chosen once at connect and
   never afterwards, so a shadow session cannot acquire the ability to trade while it
   runs. The lighter conformance scope and the different question to ask a prop firm both
   still apply to every session that does not ask for routing — which is all of them by
   default.
3. **Live ticks never enter `data/cache/ticks/`.** Rithmic data stays in `data/live/`,
   permanently. The Databento corpus (606 sessions, 1.8 GB, ending 2026-06-30) remains an
   independent reference, which is what makes "do live signals match the backtest" a
   question with an answer. Recorded days therefore never grow the backtest corpus.
4. ~~**Recorded days are not replayable either.**~~ **Reversed 2026-08-11**, by the exit
   this decision named for itself: `/simulator/days` globs both stores and tags each day
   `source: "cache" | "live"`. What chafed was arithmetic — the corpus ends 2026-06-30 at
   the data budget, so by August every session of the last six weeks was recorded and
   none of it could be practised on, which is the wrong half of the year to lose.

   The stores stay disjoint on disk, and **decision 3 is untouched**: no tick moves
   between them, and `get_day_ticks` — what the *engine* loads a session with — still
   reads the Databento cache and does not fall through. A recorded day became something
   a person can replay; it did not become something a backtest can quote, which is the
   whole point of keeping the corpus an independent reference.

   Two bars a recorded day must clear to be listed, and which evidence answers which is
   the load-bearing part:

   - **settled**, from the manifest's `closed` — never from the tape. This is the trap
     `journal.live.harvest` documents: a half-day session and a session with a hole in
     it are identical from the timestamps. 2026-06-19 and 2026-07-03 are real 13:00 ET
     closes, and a "does the tape reach 16:00" rule drops both as truncated.
   - **the open is covered**, from the span. A tape that begins after the bell is a
     fragment however settled it is.

   `ends_early` then reports what is left — this tape stops before the standard close —
   without claiming to know whether that is a holiday or a short harvest, because
   nothing at this layer can tell. Across the whole corpus it fires on 23 days, and all
   23 are the US early-close calendar. `/live/recordings` remains the surface that owns
   partial recordings, and a session can appear there and not in the replay list.
5. **Nothing is recorded before Phase 5.** See [The live stack](#the-live-stack).
6. **RTH-only recording is not a partial win.** A session is 18:00 → 18:00 ET, which from
   Kuala Lumpur is around the clock. But **7 of 13 strategies declare `session="globex"`**
   (`weekly-lower1-deep-traverse-long`, all three drift-fades, `ema-pullback-long`,
   `profile-pullback-long`, `vwap-globex-bounce`), 10 gate sites read
   `tickmod.cached_overnight`, and one reads `weekly_seed`. Gates **blind-fail-closed**, so
   a missing overnight makes them veto rather than pass. Recording only desk hours would
   mean disabling the `gx_*` gates — which changes the config, and destroys the
   live-vs-backtest comparison that is the whole point. Full shadow coverage needs an
   always-on host (~3 MB per session; a $5/mo VPS or a Pi is ample).
7. **Persistence is not optional, but the recorder *process* is.** Those 10 gate sites read
   the overnight **off disk**, keyed by `(contract, day)` — not from the injected frame.
   Phase 0c put a seam in for the regime artifact and never for the tick segments, and the
   fake feed hides it because its day is a cached day, so the file it wants already exists.
   A live day has no such file, so a feed with nothing on disk behind it makes every `gx_*`
   gate veto silently. See [what can be cut](#phase-5--what-can-be-cut-and-what-cannot).

   **Built as a pair of runtime switches, 2026-08-06.** `POST /live/modes?record=&signals=`
   (and the same two query params on `/live/feed/rithmic`) turn the recording and the shadow
   shelf on and off under a running session. `journal.live.state.check_modes` is where this
   decision is enforced rather than restated: the shelf may be switched off, the recording
   may be switched off, but not the recording *alone* on a live feed — that is the one pair
   that produces a plausible wrong answer instead of a visible absence. A day recorded in
   two halves reports its hole (`stats.unrecorded_rows`), and a day watched without the
   shelf says so (`shadow: "off"` in the manifest), so neither reads as complete later.
   Scoping and findings: `docs/research/app-backlog.md` § Charts item 2.

---

## Phase 0 — Prove the engine is prefix-safe ✅

Converts "the engine looks causal" into an executable test. The strategies are marginal
enough (weekly-traverse PF 1.10; one run's top 20 trades were 101% of net) that one
silently non-prefix array would make shadow mode lie.

### 0a. `force_i` — the one known break

`force_i` is the last tick at which a position may still be held. On a complete session
the data ends at the bell, so it is either the last tick before `flat_by` or the final
tick itself — and a position still open there *should* be force-flattened, because that
is what the bell does. On a **truncated** frame the same computation lands on the newest
tick, where nothing forces a flat: `_exit` invented a `"time"` exit and the entry check
`i < force_i` blocked. That is what broke naive prefix replay.

```python
def _force_index(holdable: np.ndarray, n: int, partial: bool) -> int | float
```

Returns `math.inf` when `partial` and the frame ends before `flat_by`; otherwise exactly
what it always returned. `partial` is the discriminator, not the data — a complete RTH
frame with `flat_by` at the bell also lands on `n - 1`, and only the caller knows whether
more ticks are coming. `force_i` is only ever compared, never used as an index, so `inf`
is safe.

Applied at all 8 sites: seven identical `int(holdable[-1]) if len(holdable) else n - 1`,
plus the weekly traverse's bespoke `force_i = n - 1`.

### 0b. Frame-injection seam

All 8 entry points loaded ticks the same way. Collapsed to:

```python
def _load_ticks(cfg, day, overnight=False, frame=None) -> pd.DataFrame | None
```

`frame`, `partial` and `regime` are threaded through all 14 `run_session*` signatures;
defaults keep every existing caller byte-identical.

Note `contract_for` can probe Databento, and the on-disk roll map ends 2026-06-30 — so
any live config must pin a **raw** contract (`NQU6`), never a root.

### 0c. Regime-injection seam

`SessionCtx` gained `regime: dict | None`, and the 10 `regmod.get_regime(...)` calls in
`gates.py` now go through `gates._regime_art(ctx)`, which prefers the injected artifact
and falls back to the cached read.

A correctness cleanup in its own right — the `SessionCtx` docstring already mandated
*"Gates read; they never build their own view of the session"* — and it keeps the read
lazy, so a config with no regime gate still never triggers a compute.

### 0d. `src/journal/sim/live_shadow.py`

`shadow_session(slug, cfg, day, frame, regime=None)` resolves the registry entry and calls
its own `run_session` with `partial=True`. Deliberately a re-run, not a reimplementation:
a second `step()` per strategy would be cheaper and would be a second source of truth. The
repo has already ruled twice that a second implementation is the thing to avoid
(`journal.replays`, `api/routers/replays.py`).

### 0e. `tests/test_prefix_replay.py`

Asserts a partial run's trades and ghosts are an exact prefix of the full run's, over two
cached sessions (2025-10-13 where twelve strategies trade, 2025-10-07 where the weekly
traverse does). Cut points are both wall-clock **and derived from the full run's trades** —
cutting mid-trade guarantees the open-position case is exercised, which wall-clock cuts
alone only did for 4 of 13 strategies.

---

## Phase 1 — Charts workspace

### 1a. The workspace ✅

- `Layout.tsx` — a third workspace `{ id: "charts", … }`; Simulator removed from Lab.
  Replay leads the tab list because switching workspace lands on `tabs[0]` and Live is a
  stub; swap once Live has a feed.
- `router.tsx` — `charts/replay`, `charts/replay/history`, `charts/live`. **Both** old
  routes redirect: `workspaceForPath` falls back to `WORKSPACES[0]`, so a surviving
  `/simulator/history` would render inside the Journal shell, FilterBar and all.
- `index.css` — 47 selectors rescoped from `.ws-lab` to `:is(.ws-lab, .ws-charts)`.
  Specificity-neutral (`:is()` takes its most specific argument; both are one class), so
  the deliberate source-order tie with the trailing `.sim-page.full` block still resolves
  the same way. **Do not add a third class to the fullscreen selector.**

### 1b. Decompose `Simulator.tsx` (2363 lines) — pure half done

Behaviour-preserving, its own commit. Targets, already delimited by the file's own section
comments: ~~`lib/simViews.ts` (pure view mappers)~~ **done** — extracted while building
Live, which needs `posLine`/`tradeMark`/`orderView`/`simSig` verbatim and would otherwise
have carried a second copy of the netting rules. `hooks/useFillHeight.ts` came out with it,
for the same reason and less optionally: Live's chart does not lay out at all without it.
Still to do: `SimToolbar`/`SimTicket`/`SimBlotter`, `hooks/useReplayPlayback.ts` (rAF loop,
play/stop/seek), `hooks/useSimSession.ts` (the bootstrap effect). Target ~600-800 lines of
orchestration.

~~Also worth doing here: a **checkpoint ladder** for `runSim`.~~ **Done** — `SimLadder` in
`lib/replaySim.ts`, wired into `LiveChart`'s `rebuild()`. See [below](#the-checkpoint-ladder).

<a id="why-1b-was-left"></a>
**Why the rest was left.** 1a, 1c and the `simViews` extraction are verifiable without a
browser — `tsc -b` catches the extraction errors that matter, and pure functions moved
between modules have no other failure mode. The hook decomposition is not: moving the rAF
loop and bootstrap effect into hooks changes closure and ref identity, and nothing in the
toolchain flags a `useCallback` dependency array that silently went stale. Its verification
is a manual pass (load a session, place orders, rewind, save an attempt), so it wants
someone at the keyboard. It is also not on the critical path — the seam Live plugs into is
1c, and Live now proves that seam works.

**And the `useFillHeight` extraction is the evidence for that split.** It is the one piece
of 1b done so far that was a *hook* rather than a pure function, and it broke exactly where
the paragraph above predicts: `tsc -b` passed, the build passed, and the chart was still
collapsed, because a measurement keyed on mount never re-ran for a page that mounts late.
Nothing short of looking at it would have caught that. Treat the remaining hook targets the
same way — compiled is not verified.

<a id="the-checkpoint-ladder"></a>
**The checkpoint ladder ✅.** `LiveChart`'s `rebuild()` re-walked the tape from the first
order on every user action — O(ticks-since-the-first-order) per click on a live tape, and
growing all session. `SimLadder` (`lib/replaySim.ts`) snapshots `SimState` at 50k-tick
boundaries and resumes from the newest sound one. The "no second, optimistic code path"
contract is intact by construction: a snapshot is `stepSim` paused, and the chunked walk is
the same fold entered further along.

The whole design question is *when a snapshot is still reusable*, and both halves are
checked rather than assumed. The tape, by re-reading the last folded tick's stamp — object
identity is not enough, since `GrowableTape` reallocates its typed arrays and keeps the
object. The log, by comparing the consumed prefix element by element: every action on the
page rebuilds the `Log` and the `OrderRec` it touches and leaves the rest
reference-identical, so a cancel or a drag on an order the snapshot has already admitted
shows up as an identity change. Unconsumed entries are checked on stamp too — one appended
*behind* a snapshot's clock would be folded a tick late. Anything fails and the snapshot is
dropped along with every later one, falling back to a full rebuild. Wrong answers are not
on the menu; only how much work is saved.

Two things worth knowing. The chunked walk hands each chunk *its own* clock rather than the
caller's, because `stepSim` finishes with `admin(clock)` and the caller's would apply orders
placed long after that chunk's last print at that chunk's position. And the trailing
`admin(clock)` — what makes an order placed since the last print show up as working — runs
*after* the last snapshot, deliberately: folding it in would consume log entries at the
wrong tick.

**Verified** with a differential harness (esbuild → Node, the same trick the composite-profile
work used, since `frontend/` has no test runner): randomised logs replayed action by action
against a synthetic tape, `SimLadder.run` compared field for field against `runSim` after
every action — 12 seeds × 40 actions, plus deliberate `truncateLog` rewinds, forward-again
after a rewind, clocks past the last print, an empty log, and a tape swap under a live
ladder. All equal. The harness was then checked against a *deliberately broken* ladder (the
order-prefix identity check removed and nothing else) and produced 45 mismatches including a
phantom +$8,040 trade — so it fails when the bug it guards is present. Measured over a
million-print tape: 10.3ms for a rebuild from scratch, 0.46ms for the ladder's worst case
(a full 50k chunk), and effectively nothing in the steady state, since each run leaves
snapshots up to its own clock and the next one folds only what has arrived since.

The harness lives in the session scratchpad rather than the repo — there is nowhere in
`frontend/` for it to go. Worth landing somewhere if a second piece of pure sim logic ever
needs the same treatment.

### 1c. `lib/tapeSource.ts` ✅

Replay and Live are one chart surface; exactly three things differ, and they live here —
the clock, whether it ends, and what you may do to it.

```ts
interface TapeSource {
  mode: "replay" | "live";
  clockFor(prev, dtRealMs, speed): { clock: number; atEnd: boolean };
  stopAtEnd: boolean;
  canSeek: boolean; canRewind: boolean; canSetSpeed: boolean; canStepBar: boolean;
}
```

`replaySource(endMs)` and `liveSource(lastTickMs)` are both implemented. The rAF loop calls
`clockFor` and everything after `engine.advance(clock)` is source-agnostic. `seekTo` and
`stepBar` are guarded on `canSeek`/`canStepBar`, so the flags are load-bearing rather than
decorative.

The seam is deliberately **above** `ReplayEngine`, not inside it: `Tape.n` is already
separate from the typed-array lengths, nothing reads `.length`, and `advance()` re-reads
`t.n` every iteration off a live tape reference — so a growing tape needs no engine change.

`liveSource` clocks on the **last received tick**, not the wall clock. On a quiet market a
wall clock runs the chart past the data and hands `advance()` a clock it cannot fill;
anchoring on the last print means the chart is exactly as current as the data behind it,
and visibly stops when the feed stalls.

---

## Rithmic access — verified 2026-08-05

`demo/rithmic_smoke.py` is the probe. It runs four escalating steps — unauthenticated
system list, `TICKER_PLANT` login, entitlements, live ticks — and never opens the order
plant. `--discover` and `--ping` need no credentials at all.

**Settled facts.**

- **Gateway `ritpz06001.rithmic.com:443`, system `LucidTrading`.** Login succeeds, CME/CBOT/
  NYMEX/COMEX level 1 all `enabled` (CME level 2 too), front month resolves to `NQU6`, and
  live NQ trades arrive. Phase 5's credentials blocker is closed; only the host remains.
- **Conformance was not required.** The plan assumed production URLs come only after passing
  it. They don't, for market data: an uncertified `app_name` logs into the ticker plant fine.
  Do not spend time on a conformance request unless the order plant is ever in scope.
- **Gateway choice is worth 15×.** Singapore is 14 ms; `rprotocol.rithmic.com:443` — the
  generic entry point, physically Chicago — is 224 ms from Kuala Lumpur. Every regional
  gateway serves the same ~20 systems, so nearest wins with nothing given up. Selecting
  Chicago in R|Trader Pro really does route to Chicago; it is not silently redirected home.
- **Aggregated quotes are not available and should not be chased.** `RequestLogin` has an
  `aggregated_quotes` bool, but `ResponseRithmicSystemInfo.has_aggregated_quotes` is False
  for every system on every reachable gateway, and setting it anyway gets the login rejected
  with rpCode 11. R|Trader Pro's own logs confirm it independently: the aggregated agent
  `login_agent_tp_agg_paperc` only ever appears for *Rithmic Paper Trading*, never for
  `login_agent_tp_lucid*`. It is also not a gateway — R|Trader Pro discovers it at runtime
  ("is_there_an_aggregator") as a bare IP on a high port, so no sweep of `:443` would find it.
- **…but aggregation is a real fix for a real problem, just not this one.** From experience
  running **L2 depth in ATAS** (not currently subscribed), turning aggregated quotes on
  noticeably resolved latency. That is consistent with the mechanism: aggregation conflates
  quote/depth updates, and depth is where the message volume actually hurts. Shadow mode is
  trades-only, so it does not bite here — but if L2 ever enters scope the question reopens,
  and note Rithmic's entitlement response already reports **CME level 2 `enabled`** on this
  login, so depth may be reachable through Rithmic independently of any ATAS subscription.

**Three things the recorder must get right.**

1. **Subscribe `LAST_TRADE` only.** Measured live, quotes run 12–21× the trade count, and the
   engine's tick schema is `(ts_utc, price, size, side)` — trades only, no bid/ask anywhere.
   Skipping BBO drops ~95% of message volume and loses nothing any strategy reads. This is
   also the real answer to feed latency, and a bigger saving than aggregation would have been.
2. **Timestamp from `ssboe`/`usecs`, never from local time.** The WSL2 host clock measured
   1.7–2.8 s behind Rithmic's, and the offset moved between runs. Rithmic's own hop
   (exchange stamp → send stamp) is 0.3–0.4 ms, so its stamps are the trustworthy clock.
3. **Redact the password from logs.** `async_rithmic` logs the entire outgoing `RequestLogin`
   — password included — at ERROR on *any* rejected login. `demo/rithmic_smoke.py` installs a
   handler-level filter; anything long-running needs the same. Handler level, not logger
   level: records come from `rithmic.plant.*` children and a filter on an ancestor logger is
   skipped during propagation.

**Two API shapes worth knowing before writing Phase 5.** `client.connect()` defaults to all
four plants — pass `plants=[SysInfraType.TICKER_PLANT]` or it opens the order plant. And
`RequestRithmicSystemInfo` is one per socket; Rithmic hangs up after answering.

---

## Tick replay (HISTORY_PLANT) — probed 2026-08-05

The plan called a history-plant backfill "unverified scope and a second plant". It is now
probed, on this account, against the live tape: `demo/rithmic_history_probe.py`
(A login, B depth ladder, C whole-session backfill, D live-vs-replay print for print).
It opens `HISTORY_PLANT` + `TICKER_PLANT`, never `ORDER_PLANT`, and writes nothing.

**It works, and it is fast.** The whole session so far — 2026-08-04 18:00 ET → 07:22 ET,
13.4 hours — came back as **84,191 prints in 11.9 seconds**, starting at 18:00:00.000
exactly (no truncation at the far end; the largest interior gap is 25s, at 19:30 on a quiet
night). `bar_type=TICK_BAR` with `bar_type_specifier="1"`: **all 84,191 bars carry
`num_trades == 1`**, so the replay is prints, not aggregates.

**And it is the same tape.** Probe D streams `LAST_TRADE` live, then replays that same
window. Two runs, 78 and 114 prints in the trimmed interior: **every print agrees on price,
size and sequence order, and total volume matches exactly.** Nothing missing, nothing
invented, nothing swapped.

Three findings that will bite whoever builds it:

- **The replay is on Rithmic's clock, not the exchange's.** Stamp deltas against the live
  path: median 287µs / 280µs across the two runs, p90 ~0.6–0.9ms, max 6.7ms. That median is
  the exchange→Rithmic hop the access probe measured at 0.3–0.4ms, which identifies it — the
  live path stamps from `source_ssboe`/`source_nsecs`, and the replay carries only
  `data_bar_ssboe`/`data_bar_usecs`, which are Rithmic's. It moves no bar (a tick tape is
  phased by position and only *ordered* by stamp, and the order is preserved), but a
  backfilled prefix and a live suffix sit on two clocks, and **Phase 6 will see it as a
  systematic sub-millisecond offset against Databento's `ts_event`**. It belongs in the
  recorder's manifest, marked per row or per range — not in somebody's memory.
- **`side` survives, and the mapping is not the one you would guess.** A replay bar has no
  `aggressor` field at all, only `bid_volume`/`ask_volume`. Cross-tabbed against the live
  aggressor int on matched prints, twice, with **zero off-diagonal**: `aggressor=1` (BUY, per
  the protobuf enum) → **`bid_volume`**, `aggressor=2` (SELL) → **`ask_volume`**. That is the
  opposite of the naive reading (a buy lifts the offer, so surely `ask_volume`?). Read it off
  the table, the way `rithmic._aggressor_map` reads its enum off the schema, and do not
  propagate the sign flip at `src/journal/sim/interactions.py:266`.
- **Expired contracts are not served, at any depth.** The depth ladder on NQU6 returned data
  at every rung out to 90 days; the thin rungs (12 prints/minute at 60 and 90 days) are NQU6
  trading as the back month, not truncation. But NQM6 — the front month on 2026-06-05, and
  what the Databento cache holds for that date — returns **nothing at any age**, in 0.3s
  rather than 1.1s. So replay depth is a property of the *listed contract*, not of a
  lookback window: it covers today's session on the current front month with room to spare,
  and it cannot reconstruct a session after the contract has rolled off.

**One concurrent session per login.** Running the session probe and the fidelity probe at the
same time got a `ForcedLogout` from Rithmic and killed the first. A probe run while the app
is connected will disconnect the app, and vice versa.

### Built, same day

`RithmicFeed` now assembles the session in front of the live stream, on by default
(`start_rithmic(..., backfill=True)`, `POST /live/feed/rithmic?backfill=`). Verified against
the live plant: **90,500 prints, first tick 18:00:00.000 ET exactly, monotonic, and the same
90,500 on disk** where the `gx_*` gates read it.

It does not replace the host. Recording is still what puts the night on disk for days nobody
was connected, and a rolled-off contract cannot be rebuilt at all.

Three things the build learned that the probe could not:

- **Backfill before subscribing, not after.** Subscribing first looks safer — the
  subscription fixes the instant the live tape begins, so the replay can be cut exactly
  there. But a 13-hour replay takes **12s on a quiet event loop and 66s with a LAST_TRADE
  subscription running beside it**: the tape floods the same process the pagination waits in.
  So the bulk runs first, and `_join` afterwards replays the few seconds between the bulk and
  the first live print — a small enough request that the flood does not matter. The join is
  cut against *a print that arrived*, never the host clock, because a join placed a second
  late replays prints the subscription is about to deliver, which is the one error direction
  that puts volume on the tape twice.
- **The pieces have to be published in time order, and "resume from the tape's tail" is not
  it.** A first pass resumed from the newest recorded tick — and on a day where somebody had
  connected at 07:08, watched, and stopped, that started the backfill at 07:09 and skipped
  the entire night in front of it. Exactly the failure the feature exists to remove,
  reintroduced by the fix for it, and only a real run found it. `LiveSession.append` only
  appends, so rows replayed for 18:00 cannot go in behind rows already at 07:08: the feed now
  publishes **head → what was already recorded → tail → join**, and the recorded piece is
  routed with `record=False` so the recorder does not write it twice. Not covered: a hole
  *between* two earlier recordings. Filling it needs the covered intervals, and a gap in a
  tick tape is not distinguishable from a quiet market by looking at the tape.
- **A recorded day is no longer the glob in write order.** `recorder.py` claimed "names sort
  in write order, so concatenating the glob is already the tape". The backfill breaks that:
  connect at 07:08 and those prints are chunk 0; reconnect and the replayed night from 18:00
  lands in a later chunk. Measured on the first real run — the tape was monotonic in memory
  and **not on disk**. `_read_live_cached` now sorts (stable, once per change in the chunk
  set), and the invariant is restated where it was claimed.

The seam is held off whatever is already published by `SEAM_SLACK_NS` (10ms, against a
measured max delta of 6.7ms), biased so the join can only ever *lose* a print, never admit
one twice — a missing print is a trade absent from the profile, a duplicated one is volume
that never traded. Checked on the real tape: identical `(µs, price, size)` prints run ~20%
of rows ambiently, and the rate at the seam (129/1000) is *below* the rate deep inside the
replay (199/1000), so the join is not duplicating.

Where it reports itself: `feed_status.backfills` on `/live/status` — one entry per range,
with rows, seconds, what the seam dropped, and `error` instead of counts when a range failed
(a failed backfill costs that stretch and never the live feed). `feed_status.backfilling` is
what the banner reads to say *replaying the session so far…*, because a whole session is tens
of seconds during which the tape is empty and a blank chart under a green LIVE banner reads
as a bug.

### The harvest — days nobody was connected for

The backfill makes *this* session whole. `journal.live.harvest` is the other half:
the sessions the machine was off for. Same replay, same live store, three ways in —
`demo/rithmic_harvest.py` for the deep pull, a background sweep behind the live feed on
connect, and a background sweep at API startup when no feed is running. All three call one
function; only the connection differs, because **Rithmic allows one session per login** and a
sweep with its own client would log a running feed straight out.

- **Trailing window, not a contract start date.** `LIVE_HARVEST_DAYS` (default 30) for the
  automatic sweeps; the CLI takes an explicit `--from`. The roll is a volume migration over
  several days — NQU6 went 123 prints/minute on June 11 to 2,233 on June 16 — not a date, so
  "when did this contract begin" is a judgment that would have to be re-hardcoded quarterly.
- **A completion flag, not a coverage test.** `ticks.market_closed` only knows *full*
  exchange closures and only for contracts with a roll probe, so for a pinned raw contract it
  answers False for every day of the year. Deriving it from the tape fails the other way: a
  half-day and a day with a hole in it are identical from the timestamps, so Thanksgiving
  Friday would be re-fetched on every startup forever. A harvested day writes
  `harvest.complete` into its manifest and is skipped on that — including at zero rows, which
  is the honest answer for a day the exchange did not trade.
- **Marked `source: "harvest"`.** A harvested day is not a watched day: no signal journal
  exists for it, and it is on Rithmic's clock throughout. A reader has to be able to find
  that out without asking a person.
- **Only closed sessions.** Today-in-progress belongs to the feed; flagging it complete would
  freeze it half-recorded.
- **Refuses to run against a live feed** (the CLI checks and exits), and one sweep at a time
  process-wide.

**The finding that cost a day of data: one call is not one range.** Rithmic's replay returns
a silent **prefix** of what was asked for. The first harvest run recorded 2026-06-16 as
exactly 50,000 prints ending at 04:29 ET — on a day whose neighbours returned 313k and
487k — and flagged it complete, which meant it would never be looked at again. Nothing
raised. So `rithmic.replay_into` now drives a **cursor**: each call continues from one
nanosecond past the last print published, and the range is finished only when a call comes
back empty. `covered` says whether it ended that way or ran out of calls, and the harvest
flags a day complete only when it did.

Two details of that loop worth knowing. The +1ns lands in the **trim**, not the request — a
`datetime` carries microseconds and Rithmic indexes a replay by whole *seconds*, so every
continuation necessarily re-asks from the start of its last print's second. That is why it is
correct (the remainder of that second comes back rather than being skipped) and why
`dropped` counts one re-sent page per continuation. And each page is published as it lands,
so a long range fills the chart as it arrives rather than in one lump.

How much it was hiding: re-harvesting the same June days with the loop in place returned
**446,074** prints for 2026-06-16 against 50,000, **886,764** for the 18th against 503,034,
and **187,856** for the 19th against 95,229. Most days were short, not one.

**Two more found by running it over a month, neither reachable by reasoning:**

- **`idle_timeout=5.0` is a data-loss setting.** The library's default is a stall timer, and
  four of the busiest sessions (500k–1.1M prints) died on it while their neighbours came back
  fine. `REPLAY_IDLE_S` is 30s. A day that times out is a day left unfetched — correctly
  unflagged, so the next sweep retries it, which is how these four were noticed at all.
- **An empty answer is not evidence of an empty session.** 2026-07-06 came back with zero
  prints, no error, and was flagged complete — while a 60-second probe of that same day at
  10:00 ET returned 2,142 prints. A full holiday and a transient miss are indistinguishable
  from one call, so a zero-row day is no longer flagged. The cost is a second per real
  holiday per sweep; the cost of the other choice is a real session permanently recorded as
  having had no trades in it. The first version made the wrong trade here on the reasoning
  that zero rows is the honest answer for a holiday — it is, and that is not sufficient.
  (Re-fetched on a fresh connection, 2026-07-06 returned **414,119** prints.)

**It is not rate limiting, it is request size — and that is the fix.** The first sweep needed
three passes: timeouts on the biggest days, then empty answers with no error. The obvious
reading is a quota, and it is wrong. **40 back-to-back requests in 57 seconds all
succeeded**, ~1.3s each — a far higher request *rate* than any sweep reaches. Walking the
window size on one dense session gives the real variable:

| window | result |
|---|---|
| 15m / 60m / 180m / 360m | reliable both attempts, 0.6k–30k bars, 1–10s |
| 720m | 236,703 and 243,610 bars |
| **1440m** | **90,000 bars (truncated) on one attempt, 451,212 on the next** |

Round numbers, intermittent, silent — the same signature as the 50,000. So `replay_into`
never asks for more than `REPLAY_WINDOW_NS` (**3 hours**) at a time, which leaves margin on
the largest session seen (1.1M prints, so ~140k to a window, against a 243k single response
that came back fine). Truncation and progress became the same code path: a short window
simply leaves the cursor inside it. Validated against days already held — a windowed re-fetch
of 2026-07-15 and 2026-06-16 reproduced both to within **0.03%** (−153 and +184 prints on
~450k), same first print, same last print, monotonic. The residual is prints sharing a stamp
at a boundary, which is the same lose-rather-than-duplicate trade the seam makes.

**And an empty window is asked twice before it is believed.** Windowing makes "empty" a fact
about three hours rather than about the session — but it is not yet a fact at all, because an
empty answer looks identical whether the window was quiet or the replay just did not serve
it. 2026-08-04 was flagged complete ending at **15:48 ET** on the strength of one empty final
window; that window asked again holds **51,271 prints**. One retry costs an extra call on the
genuinely empty windows (the 17:00–18:00 halt, holidays) and nothing anywhere else.

**The completion flag is "the day has prints", not "this fetch returned prints".** A
half-day has a legitimately empty tail — Juneteenth and 3 July both close at 13:00 ET — and
keying on the fetch would leave every one of them unflagged and re-fetched on every sweep
forever.

**As harvested, 2026-06-11 → 2026-08-04:** 39 sessions, **21.3M prints, 140 MB**. Every
session starts within 60 seconds of its 18:00 ET open; every one runs to the 17:00–18:00
maintenance halt except 2026-06-19 and 2026-07-03, which stop at exactly 13:00 ET and are
the two real half-days. Median 500,788 prints per session, max 1,096,023. Spot-checked at the
point of the exercise: `cached_overnight` on 2026-07-30 returns 171,242 rows to the `gx_*`
gates, `weekly_seed` builds from that week's earlier sessions, and `day_complete`/`has_rth`
still answer False — the two stores are still disjoint.

**How this was found is the transferable part.** Every one of these — the 50,000-print
prefix, the 5s idle timeout, the empty day, the empty final window, the half-day flag — was
invisible to the tests and to reasoning, and visible immediately in a per-session row count
next to its neighbours. Harvest output is printed per day for that reason, and the coverage
audit (does the tape start at 18:00, does it reach the halt) is worth re-running after any
change to the fetch path.

**What the harvest does not give you: backtestable days.** It writes to `data/live/ticks/`
and `get_day_ticks` — what the engine loads a session with — reads the Databento cache with
no fallback. The gates and the weekly seed *do* fall through, which is the point.

---

## The live stack

*2–4 are built; 5–6 remain parked, on an always-on host and purchased Databento days.*

**Nothing is recorded before Phase 5.** Phases 2–4 run entirely in memory, in-process, off
a fake feed. Not a compromise — strictly simpler. The fake feed's source is a cached
Databento day already on disk, so persisting it writes a second copy of a file we have;
and *not* writing deletes a whole hazard class: `day_complete()` short-circuiting the
Databento backfill for that date, the unkeyed `_read_parquet_cached` /
`_read_segment_cached` LRUs going stale under a mutated file, and
`{SYM}_{DATE}_sums.json` being reused after a mid-session write.

Recording cannot be deferred past Phase 5, and the reason is structural. In-process works
for the fake feed because `--reload` killing it costs nothing — the source is static, just
replay it. With a real feed both halves break: a tick that wasn't kept is gone for good, so
the day cannot live only in a process that may die; and a separate process needs some way
for the API to see what it holds. Sealed chunk parquets answer both at once, with less
machinery than any alternative IPC.

| Phase | What | Writes? | Blocked on |
|---|---|---|---|
| 2 ✅ | In-memory live session + a **fake feed** replaying a cached Databento day at wall-clock speed (read-only) + `GET /live/tape?since=<row>&gen=<token>` reusing `simulator.py`'s delta encoding. Runs inside the API process | **no** | — |
| 3 ✅ | `GrowableTape`, `liveSource()`, append-only paper blotter — `truncateLog`/`seekTo`/`noteRewind` never wired, enforced by the `canSeek` guards | **no** | — |
| 4 ✅ | Shadow signals as **prefix re-runs of `run_session`** — one engine, so live cannot disagree with the backtest. Cadence: every closed bar + a ~5s floor. Live regime artifact computed once per checkpoint and **frozen** | **no** | — |
| 5 ✅ | Rithmic `TickSource` on `async_rithmic`, `TICKER_PLANT` only — **plus the recorder**, writing immutable sealed chunk parquets to `data/live/ticks/{SYMBOL}/{DATE}/` with a `session.json` heartbeat. Built in the trimmed form: the writes and the live-store readers, in-process, no separate recorder process ([below](#phase-5--what-landed)) | **yes** | ~~credentials~~ ✅ + always-on host to *run* it |
| 6 ✅ | Tape fidelity, prefix-integrity, then signal agreement — in that order ([below](#phase-6--what-landed)) | no | purchased Databento days for stages 1 and 3 |

<a id="phase-5--what-can-be-cut-and-what-cannot"></a>
### Phase 5 — what can be cut, and what cannot

The question that prompted this section was "can we run the real feed and skip the
recording". The answer is that the split is not where it looks like it is.

**What cannot be cut: ticks on disk.** Not for the track record — for correctness, today.
All 10 gate sites do this (`gates.py:743` and its nine twins):

```python
on = None if contract is None else tickmod.cached_overnight(contract, ctx.day)
if on is None or on.empty:
    return  # blind: no overnight, no Globex anchor — veto everything
```

That is a disk read keyed by `(contract, day)`, and nothing in the live path satisfies it.
`weekly_seed` reads the same way (already noted below). So "live feed, memory only" is not
a reduced-fidelity shadow mode; it is one where seven strategies never signal and nothing
says why. It fails the same way the missing regime artifact would have: a plausible wrong
answer, not an error.

**What cannot be cut either: the always-on host.** It is driven by the session shape, not
by recording — `frame_for(overnight=True)` cuts from prev 18:00 ET, so the connection has
to span the night whether or not anything is written down. Dropping the recorder removes
work, not the blocker. (The one route that *would* attack it: backfill the night from
Rithmic's `HISTORY_PLANT` on connect, so the process can attach mid-day. Still not the
order plant, so it stays conformance-free — but it is unverified scope and a second plant.)

**What can be cut: the process split.** Sealed chunk parquets, the `session.json`
heartbeat and the separate process all exist to survive an API restart and to let the API
see what another process holds. A trimmed Phase 5 keeps the writes and drops the
machinery:

- the feed thread appends to `data/live/ticks/{SYMBOL}/{DATE}/` alongside `session.append()`
- `cached_overnight`/`cached_rth` learn a live-day fallback to `data/live/` — decision 3
  still holds, `/simulator/days` keeps globbing only the Databento cache
- on startup, reload the day from disk instead of starting blind

What that gives up against the full design is surviving a crash mid-write. What it keeps
is the gate stack, restart tolerance, and a tape for Phase 6 — which is most of the value
for a fraction of the work. **The `cached_overnight` re-point is a Phase 5 task in its own
right**; the plan previously marked only the `weekly_seed` one, which is the same seam seen
from one caller instead of eleven.

<a id="phase-5--what-landed"></a>
### Phase 5 — what landed

The trimmed shape above, in five pieces.

- **`journal.sim.ticks` grew a live store.** `LIVE_TICK_DIR` (`data/live/ticks/`,
  deliberately *not* `config.LIVE_DIR`, which is the ATAS import drop folder), plus
  `live_chunks` / `live_day_ticks` / `live_segment` and a `session_date_for` that inverts
  `day_bounds_utc`. The three `cached_*` readers fall through to it, **Databento first**.
  That order is the load-bearing one: recording a session can never change what a backtest
  over that session says, which is what leaves the corpus an independent reference. The
  read is cached on the *chunk set*, so a day that grows invalidates its own entry —
  the failure the segment LRU and the sums file both had to be designed against.
  `weekly.session_sums` falls through the same way, keeping its sums file inside the live
  day's directory rather than beside the Databento parquets.

  Nothing else moved. `day_complete`, `has_rth` and `have_segment` still answer only about
  the vendor cache, so a recorded day cannot short-circuit a backfill or satisfy the
  runner's broken-window guard — the disjointness has a test of its own.

- **`journal.live.recorder`.** Sealed chunk parquets plus a `session.json` heartbeat, on
  three properties that between them delete the need for any coordination between reader
  and writer: a chunk is immutable once named (temp file, then rename), **the directory is
  the truth and the manifest is only a heartbeat**, and names sort in write order. The
  third seal trigger is the one that matters — a batch crossing into a new window seals the
  old one, so the night is complete on disk at the instant RTH opens, which is when the
  `gx_*` gates start reading it. A resumed recorder counts existing rows off the parquet
  footers and continues the numbering; reusing an index would overwrite ticks that are gone
  for good.

- **`journal.live.rithmic`.** Ticker plant only, `LAST_TRADE` only, timestamps from
  `source_ssboe`/`source_nsecs` (the exchange's own stamp — the same instant Databento
  stores as `ts_event`, which is what makes the Phase 6 comparison mean anything), falling
  back to Rithmic's send stamp and **never** to the host clock, which measured 1.7–2.8s off
  with a moving offset. Password filtering is installed before any socket opens. Two things
  the plan did not anticipate:

  - **The aggressor enum is in the protobuf.** `LastTrade` names its own values (`BUY=1`,
    `SELL=2`), so the mapping is read off the schema rather than guessed from the two ints
    the probe saw. What remains an assumption is that Rithmic's *aggressor* and Databento's
    *side* mean the same thing — the `'B'` = buy-aggressor measurement was made on
    Databento prints. So every recorded tick keeps the raw int in `agg_raw` beside the
    mapped `side`, and Phase 6 cross-tabs the two. A mapping shown backwards is then a
    re-derivation, not a re-recording. (`agg_raw` never reaches an engine: the tape reader
    narrows to the four schema columns on the way out.)
  - **The tape has to be forced monotonic.** Exchange stamps can arrive very slightly out
    of order and the tape is not allowed to be — `LiveSession.append` trusts its order, the
    engine searchsorts the RTH boundary, and every bar is phased by position. Clamping
    forward rather than dropping: a dropped print is a real trade missing from the profile
    and the VWAP, a clamped one is the same trade moved by microseconds. How often it
    happens is counted into the manifest, so a feed where it stops being rare is visible.

- **`journal.live.state` owns the session roll, and the feed does not.** A session is
  18:00 → 18:00 ET, so an always-on host turns the day over every night. `_Router` groups
  each batch by `session_date_for` and hands the parts to the right session, opening a new
  one — with its own recorder, shadow runner and journal — when the date advances. **The
  roll is decided by the tick clock, never the wall clock**, so a drifted host clock still
  cuts the day where the exchange does and a batch straddling 18:00 splits instead of
  landing wholly on one side. Rolling forward only.

- **Resume, and it is not optional either.** `state.resume()` rebuilds the tape from the
  recorded chunks at startup; `api/main.py` calls it unconditionally, and reconnects the
  feed only under `LIVE_AUTOSTART`. Without the resume a process that came back at eleven
  would hold a tape that *began* at eleven, and every strategy would be simulating a
  session that opened two hours late — silently, with plausible numbers. The gates would be
  fine (they read the night off disk, and it is there); the entries would be wrong.

**The surface offers both feeds.** `LiveChart`'s start screen has a Rithmic section
(contract field + "Connect & record") beside the simulated one, `useLive.startRithmicFeed`
posts to `/live/feed/rithmic`, and the running banner reads the source rather than assuming
it: green `LIVE · RITHMIC` with a recording dot, orange `SIMULATED FEED`, and orange
`RESUMED — NO FEED` for a session rebuilt from disk. That third state is why the source is
read at all — a resumed session has a whole tape and nothing behind it, and labelled
"arriving" it would read as a quiet market rather than as a feed to reconnect. Checked in a
headless browser for the start screen and the simulated-feed banner; the rithmic and
resumed banner states are pure renders off `status.source` and have not been seen.

Also: `journal.live.journal`, an append-only `.jsonl` per strategy recording what the
runner said and when — one line per pass **whose answer changed**. It exists for Phase 6's
second stage, which compares what was believed during the session against a settled run,
and a run after the close can always be redone while a belief at 10:14 cannot.

<a id="phase-6--what-landed"></a>
### Phase 6 — what landed

`journal.live.reconcile`, driven by `demo/live_reconcile.py`. The three comparisons in the
mandated order, each carrying the verdict of the ones before it, and stage 3 reporting
itself **not attributable** when they did not pass. That flag is the point of the module:
run stage 3 alone and a disagreement has three possible homes with no way to choose.

Four things worth knowing before reading a report.

- **The headline is the whole day, not the sum of the windows** — and the difference is
  real. The rth/post one-print seam noted below is a disagreement about which *window* a
  trade belongs to, not about the trade; summing per-window agreement charges it to the
  feeds. So fidelity is measured over the concatenated day, and `window_boundary_volume`
  reports the windowing residual separately. On 2025-10-13 that is 2 contracts.

- **Identity is the entry, not the whole trade — but only across tapes.** Stage 2 compares
  two runs over the *same* tape, where any difference is a prefix violation and there is
  nothing to be tolerant about, so it keys on the full trade. Stage 3 compares two runs
  over *different* tapes, and there the same seam bites again: a position held into the
  bell is force-flattened on the tape's last tick, and the two stores' last ticks differ by
  that one print. Both runs take the same trade, at the same entry, exiting at the same
  price for the same reason, one millisecond apart. Keyed on the exit those are two
  unmatched trades and a clean day reads as 98% agreement. So stage 3 keys on the entry and
  *compares* the exit, reporting `divergent_exits` and `exit_pnl_delta` alongside the match
  rate. This was found by the end-to-end test, not by reasoning.

- **Agreement is weighted by P&L, and reported from both sides.** `pnl_share_live` is how
  much of what live claimed was real; `pnl_share_databento` is how much of what was real
  live caught. Count is reported but does not lead — given that one run's top 20 trades were
  101% of its net, a 95% match rate that misses the two carrying the edge is a failure
  dressed as a success.

- **A day nobody bought reports `unavailable`, not `ok`.** The reconciliation reads the
  Databento segments *around* the live fallback — going through `cached_rth` would compare
  a tape against itself and report perfect fidelity for a day that was never bought. There
  is a test for exactly that.

### What 2–4 actually landed as

- `api/tape_codec.py` — the delta encoding, extracted from the Simulator's session
  endpoint so a live *slice* and a whole finished session are the same bytes. Every
  block is self-contained (own `t0`/`price0`, `dt[0] == dp[0] == 0`), which is what
  lets the client append rather than continue somebody else's prefix sum.
- `src/journal/live/` — `session.py` (the day so far, growing typed arrays under a
  lock, with `frame_for(overnight=...)` cutting exactly the windows `get_day_ticks`
  returns), `feed.py` (the fake feed), `shadow.py` (the runner), `state.py` (the one
  session this process watches).
- `api/routers/live.py` — `/live/status`, `/live/session`, `/live/tape`,
  `/live/signals`, and start/stop for the fake feed.
- `frontend/src/lib/growableTape.ts`, `hooks/useLive.ts`, `lib/liveTypes.ts`, and
  `pages/LiveChart.tsx` rewritten from the blocker stub into the real surface.
- `lib/simViews.ts` — the pure view mappers (`posLine`/`tradeMark`/`orderView`/
  `simSig`) lifted out of `Simulator.tsx` because Live needs them verbatim. This is
  the first of 1b's targets and the half `tsc -b` verifies outright.
- `hooks/useFillHeight.ts` — the viewport measurement that publishes
  `--sim-fill-h`, also lifted out of `Simulator.tsx`. Not a tidy-up: without it the
  Live chart collapses (third finding below).

### Three things found while building it

- **A live regime artifact must never be `None`.** `gates._regime_art` falls back to
  `get_regime` — a cached read of the **whole settled day** — when the injected
  artifact is None. The fake feed's source *is* a cached day, so that file exists:
  returning None would have handed every gate the finished day's answer at nine in
  the morning. Lookahead, silent, and flattering. `ShadowRunner._live_regime` always
  returns a dict, empty checkpoints and all, and `test_the_live_artifact_is_never_none`
  guards it. This is a sharper version of the blind-fail-closed note below — the
  failure mode is not silence, it is a plausible wrong answer.
- **The cached `rth` and `post` parquets have a one-print seam.** On 2025-10-13 the
  `post` file's first tick is at 19:59:59.9995 UTC — a hair *before* the 20:00
  boundary its own window declares — so the `rth` segment is missing one print that
  is RTH by the `[09:30, 16:00)` rule. Harmless to every existing reader (they read
  the `rth` file directly and never see it), and it does not affect the live path,
  which takes one time-ordered stream. Worth knowing before Phase 6 compares a
  Rithmic tape against a Databento one print for print.
- **`.sim-page`'s height is JS, and a page that mounts late never gets measured.**
  The chart pages fill the viewport rather than scrolling, and the height comes
  from `--sim-fill-h` — measured in JS, because the chrome above them is not a
  fixed height to subtract. Live mounts its `.sim-page` only once the status poll
  answers, so a measurement taken once on mount runs against a null ref, finds
  nothing, and never looks again. The height stays `auto`, `.sim-body`'s `flex: 1`
  has no definite height to take a share of, and the chart collapses to whatever
  its tallest sibling happens to be — *growing as the signal rail fills*, which
  reads as a chart bug and is a layout one. `useFillHeight` therefore measures in a
  `useLayoutEffect` on every render, with the state only changing when the number
  does. No feedback loop is possible: what is measured is the element's top, which
  is set by the chrome above it and cannot be moved by the height returned.
  Any future page using `.sim-page` needs this hook, not a copy of the old effect.

### Notes that will not be obvious later

- **The live regime artifact must be frozen at each checkpoint.** *(Built in Phase 4;
  see the two findings above for the sharper version of this hazard.)* Recomputing it from
  a longer prefix would let a gate's verdict change retroactively. And without one at all,
  every regime gate blind-fails-closed and the strategy goes silent after 10:30 — which
  looks exactly like "no setup formed". This is the most likely way the feature ships
  broken and stays unnoticed. As built: a checkpoint is computed the first time its ET
  cutoff has passed and never recomputed, and checkpoints whose cutoff has *not* passed are
  deliberately absent rather than computed over a short prefix. Every regime gate applies
  its veto only from its own checkpoint minute onwards, so an absent checkpoint leaves the
  gate inert until it can honestly answer.
- ~~**`/live/session` reads the Databento cache for the weekly seed.**~~ **Done in Phase 5.**
  All eleven callers went through one seam: the three `ticks.cached_*` readers fall through
  to the live store, and `weekly.session_sums` does the same for the week's earlier
  sessions. Databento first in every case. The gates were the half that failed silently and
  they now read a recorded night exactly as they read a bought one.
- **Verify Rithmic's aggressor mapping empirically.** Partly settled, and the remaining
  half is now automatic. Rithmic's `LastTrade` protobuf *names* its enum (`BUY=1`,
  `SELL=2`), so `rithmic._aggressor_map` reads the schema rather than guessing from the two
  ints the probe saw. What is still untested is whether Rithmic's *aggressor* and
  Databento's *side* mean the same thing — the `'B'` = BUY-aggressor finding (~0.35pt above
  the local mid) was measured on Databento prints. Every recorded tick therefore keeps the
  raw int in `agg_raw`, and `reconcile.aggressor_crosstab` returns `confirmed` / `inverted`
  / `inconclusive` against a bought day. On the re-recorded control session it reports
  `confirmed` with **zero** off-diagonal pairs; the real test is a Rithmic tape. Do not
  propagate the known sign flip at `src/journal/sim/interactions.py:266`.
- ~~**Phase 6's three comparisons must not be conflated.**~~ **Built that way**, with each
  stage carrying the previous verdicts and stage 3 flagging itself not attributable — see
  [what landed](#phase-6--what-landed). The conflation was not hypothetical: the first
  end-to-end run reported 98% agreement on two tapes that were the same ticks, because
  bell-flattened trades were being keyed on an exit stamp that the rth/post seam moves by a
  millisecond. That is stage 1's finding, and it was showing up as stage 3's number.
- ~~**Report agreement weighted by P&L contribution**, not trade count.~~ **Done**, and
  from both sides: `pnl_share_live` (how much of what live claimed was real) and
  `pnl_share_databento` (how much of what was real live caught).
- **Phase 6 needs purchased Databento days.** Still true, and now the only input it lacks.
  The corpus ends 2026-06-30, so stages 1 and 3 need a handful of settled sessions to
  compare against; with none, `demo/live_reconcile.py` reports `unavailable` rather than a
  flattering `ok`. Stage 2 needs nothing bought.

### The cost of this ordering

Phases 2–5 let you *watch* live signals fire; they accumulate no evidence. Shadow mode's
payoff is the track record, and that needs persistence. Watching it work is 2–5; proving it
works is 5–6.

With 0–6 built, what remains is not a cost of the ordering — it is a cost of not having a
host. The machinery is complete and checked end to end: a session records, survives a
restart, rolls at 18:00, and reconciles against the vendor tape print for print with the
three stages kept separate. What it has all been checked against is a day that already
happened, re-recorded through the same path a live day would take. Nothing has yet been
learned about a session nobody has seen, and nothing will be until something is running at
17:59 ET and still running at 16:00 the next day.

---

## Phase 7 — routing (built 2026-08-06, **beside** Phases 0–6 rather than on top of them)

Originally recorded so nobody designed toward it, and that instruction held: **nothing in
Phases 0–6 was shaped for this**, and the way to check that is that none of them changed.
The shelf cannot reach the broker (`shadow.py` imports nothing from `broker.py`), the paper
blotter is untouched, and `live.py`'s "nothing in this router can send an order" is still
true of that file — everything that can trade is in `live_orders.py`.

**Three of the four worries in the original paragraph turned out to be avoidable rather
than solved**, and the reason is the same in each case: the paper blotter was not made into
an order-state machine, because live orders were never merged into it.

- *"Would make the poll untenable"* — it does not touch the tape poll. Routing has its own
  2s status poll carrying the broker's own word.
- *"Turn the paper blotter into an order-state machine reconciled against broker fills"* —
  no. The blotter stays a re-derivable fold over the tape; the broker's working orders and
  position are a **separate** structure that is never derived, only asked for. They are
  different kinds of truth and they are kept apart.
- *"Require a kill switch plus position reconciliation on restart"* — this one was real and
  is built. `reconcile()` runs on attach and **orders are refused until it answers**;
  `reconciled_at is None` renders as "not read back", never as "nothing working".

**The seam that decided the shape: one login is one concurrent session** — the same fact
that forced the harvest sweep onto the live client. The order plant therefore rides
`RithmicFeed`'s connection, which is why a disconnect leaves the session unable to send
until it has re-read the book, why there is no routing without a tape, and why `routing` is settled at connect rather than being a runtime switch
like `record` and `signals`. Those change what is written down about a session; this changes
what the socket may do.

**Rebuilt 2026-08-07: paper is an account.** The first cut kept the chart's gestures on a
paper blotter and put real orders behind a form in a side panel, honouring "no
single-click path from a chart gesture to a live order". Using it showed the ergonomics
were inverted — the gestures exist for speed, and they were wired to the one thing where
speed does not matter. Paper now sits in the same selector as the Rithmic accounts, every
gesture works for all of them, and what varies is the **confirmation**: a popup naming the
order in words, with a per-account one-click toggle that skips it (the ATAS model).

Four gates, each failing closed, in `journal.live.routing`: `LIVE_ROUTING=1` to be
reachable at all — the one env var, and the deployment-level "this machine must never
trade"; a **per-account label** (demo/live) with no default, since Rithmic's account list
says nothing about funding and an unlabelled account cannot send; a **reconciliation**,
so nothing goes out against a picture this process made up; and, for accounts that
confirm, a server-issued review token, so the confirm is a shape of the API rather than a
habit of the UI. (A fifth gate stood here until 2026-08-11: a typed arm that lapsed on
idle, disconnect, account switch and the 18:00 roll. It was removed — a lease is the wrong
shape for a last line of defence, since it expires mid-decision and stands open while
nobody watches. The four above are read on every order instead of once per lease.) One-click is a separate door on the same endpoint, refused unless that
account carries the flag — and the flag is cleared whenever an account is labelled live,
so it cannot be enabled on practice and inherited by real money.

Every session — connect, restart, roll, reconnect — **starts on paper**. There is always an
active account and by default it cannot trade.

**Trades are journalled, 2026-08-07** (`journal.live.booking`). A closed round trip on a
real account is booked into `atas_journal` by the broker itself, as `mode='live'`; a paper
trade is POSTed from the browser and booked as the account `paper` with `mode='replay'`, so
it is visible in Trades and the Calendar without reaching the real-money statistics. The
asymmetry is forced by where the fill engines live — the broker nets its own trades on the
server, and `replaySim.ts` is the only thing that knows a paper fill happened. Booking can
never raise into the fill path: it runs inside the notification handler on the feed's event
loop, so a failure is counted (`booking_errors`) and the `orders.jsonl` line still carries
the trade. Details and the one property that looks like a bug (a scale-out reads as one
logical trade) are in `docs/research/app-backlog.md` Charts §6.

**The bar in the last sentence of this section still governs.** It was never a bar on
building the mechanism — it is a bar on trusting it with money, and the agreement rate from
Phase 6 has not been measured over a meaningful sample yet, because that needs the always-on
host and a few purchased Databento days. See `docs/research/app-backlog.md` Charts §1 for
what is left, which is the whole of the against-a-real-plant half.

### Phase 7b — the guardrails (built 2026-08-10)

A **fifth gate**, and a different kind from the four above. Those refuse an *accident* — the
wrong account, a slipped digit, an order nobody read. This one refuses a *decision*: a
deliberate order that the person placing it will regret, and that their own book says loses
money. Levels and derivation: `docs/research/lucidpro-operating-plan.md`, fitted to 2,538 real
round trips bootstrapped through a $2,000 trailing drawdown.

The rules live in `journal.live.routing` (`Guards`, `DayState`, `day_refusal`, `_check_shape`)
because that is the module with no socket in it — the part that has to be right is worth
testing without a market. `journal.live.broker` owns the one input they cannot compute: the
day's realised total, folded out of the fill stream, per account, net of commission.

Four properties, each of which is the answer to a way the thing could have been useless:

- **`LIVE_GUARDRAILS` defaults to ON.** Unset means enforced; only an explicit
  `0`/`false`/`no`/`off` disables. Opposite polarity to `LIVE_ROUTING`, deliberately — a
  permission's safe default is "denied" and a restraint's is "enforced", and both fail toward
  not losing money. It is an env var rather than a UI control because that *is* the feature:
  switching the rules off should mean leaving the chart, not reaching a toggle beside the order
  pad. The levels themselves are app settings (0 disables one rule).
- **The check runs inside `_submit`, not only in `preview`.** `send(token)` spends a token
  minted earlier, so a review-time-only check is walked past by staging an order while still
  allowed and sending it after the day locked.
- **A reducing order skips the entire layer**, shape rules included. A discipline rule that
  could refuse a scale-out would be, at the worst possible moment, a rule that keeps you in a
  trade. `flatten` was already ungated. A *flip* is not a reduce — it is an entry however it is
  framed, and it is the shape somebody reaches for once refused.
- **The daily lock latches, and only the 18:00 roll clears it.** Not a recovery back above the
  line, not a restart, and not an account switch — the day record is per account, so switching
  away and back restores the lock rather than dropping it. A reviewed order is dropped on the
  lock only when flat, for the same reason as the point above.

Visible in three places, because a safety layer that is silently off is worse than one never
built: a chip on the chart's top bar in the same always-on-screen row as the account name, the
day strip in the order panel, and `snapshot()["guard"]`, which carries both the local realised
total (what the rules run on) and the broker's own `day_pnl`, with the gap reported rather than
reconciled.

**Honest caveat, and it is in the doc too:** none of this is un-bypassable by whoever owns the
machine. It buys friction and visibility, not impossibility.
