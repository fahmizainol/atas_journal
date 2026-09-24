# SuperMemo's Algorithm Arena in the recall deck

*Built 2026-08-22. The Lab → Recall deck's scheduler, previously SM-2, is now
SuperMemo's Algorithm Arena, reached by spawning a vendored Rust binary. SM-2
stays as the fallback.*

Read [`docs/trade-grading-plan.md`](trade-grading-plan.md) G7 for what the deck
is; this doc is only about what schedules it.

---

## Build it

```sh
cargo build --release --manifest-path vendor/sm20/Cargo.toml
```

That is the whole install. No server, no Python extension, no build step in
`requirements.txt` — the shim spawns the binary and talks JSON over a pipe.
**Until you run it the deck schedules with SM-2**, silently and correctly, which
is what makes the Rust toolchain optional rather than a new dependency of the
app.

`SM20_BINARY` overrides the path if the binary lives somewhere else.

## What actually got adopted

Not SM-20. The Arena is SM-20's *production scheduler*, and what it commits is a
weighted blend of five candidate intervals:

| slot | algorithm | default weight |
|-----:|-----------|---------------:|
| 0 | SM-2  | 6%  |
| 1 | SM-15 | 14% |
| 2 | SM-19 | 45% |
| 3 | SM-20 | 25% |
| 4 | FSRS  | 10% |

SM-20 proper is a quarter of the answer and SM-19 is the plurality. On a first
Good rating the five disagree sharply — SM-2 says 1 day, SM-19 says 9, SM-20's
own kernel says 31 — and the deck commits 14. If that ratio is not what you
wanted, the thing to change is the weights, not the model.

The weights then adapt from outcomes, but slowly: the learning rate is 0.0317
and one review moves them by roughly 0.006 out of 100. Two of the five models
(M2's optimizer, M3's matrices) do not begin learning at all until the deck has
200 reps. Early on this is close to a fixed blend, and at ~45 cards this deck
will take a long time to get anywhere near 200.

## The three moving parts

- **`vendor/sm20/`** — the algorithm, vendored from Incrementum and not written
  by us. See [`vendor/sm20/PROVENANCE.md`](../vendor/sm20/PROVENANCE.md) for
  where it came from, the two edits made to it, what the tests do and do not
  prove, and the DMCA lineage caveat. `src/main.rs` is ours: one JSON object in
  on stdin, one out on stdout, one process per rating.
- **`src/journal/sm20.py`** — the shim. Stdlib only. Spawns the binary, raises
  `Sm20Unavailable` for every way that can fail.
- **`srs.advance()`** — the deck's entry point. Runs SM-2's arithmetic first,
  then hands the interval to the Arena if it can reach it. The card's `ease`,
  `reps` and `lapses` stay SM-2's throughout: they are the card's history and
  they are what the page renders, not the scheduler's working state.

## State, and why there is suddenly so much of it

SM-2 needed nothing beyond the card. The Arena keeps two things:

- **per card**, ~900 bytes of JSON in `recall_cards.sm20_state` — five models'
  item state, stability, difficulty. Deliberately *not* read by
  `all_recall_cards`, because building the deck only needs a due date and
  pulling every card's model state to compare dates would load the deck's whole
  working set to show one card.
- **per deck**, ~270 KB in the `recall_collection` singleton — the live blend
  weights, M2's optimizer, M3's 21×21×21 matrices. Read-modify-written on every
  rating inside the router's existing `db_lock`, because two ratings racing
  there would have the later write discard the earlier one's learning.

The 270 KB does **not** grow with the deck: every matrix inside it is
fixed-dimension, and the collection holds no accumulating sample lists. The cost
is a constant, not a curve.

Losing that row is not fatal and not loud — the next rating starts from default
weights and the schedule quietly gets worse. It is derived state that cannot be
rebuilt from `recall_reps`, since the models are path-dependent. That is the
argument for keeping it in `journal.db` rather than in a cache directory.

## Measured

- subprocess spawn + one review: **2.5 ms** median, 3.2 ms p90 — against a human
  who has been staring at a chart for seconds, and paid once per rating rather
  than once per card shown. `deck()` never calls the scheduler.
- binary: 1.7 MB. Build tree: 546 MB, gitignored.

## Things that will look like bugs and are not

- **A first Good schedules ~14 days out**, where SM-2 said 1. That is the blend
  above, and SM-19's 9-day first interval is doing most of it.
- **The same interval lands on different days**. SuperMemo disperses committed
  intervals stochastically to spread load — up to +99% at short intervals,
  narrowing as intervals grow. It is on by default because it is faithful; pass
  `disperse=False` to `sm20.review` to turn it off, and the tests do exactly
  that so a golden is reproducible.
- **The committed interval is a hair below the weighted blend.** 49.51 blends,
  49 commits: the Arena truncates to whole days.

## Migrating an SM-2 deck

`_migrate_recall_sm20` adds the two columns; existing cards get NULL in both and
keep their due dates. **SM-2 history is not translated and cannot be** — the two
schedulers share no state space — so a carried-over card's next rating is a
first review as far as the Arena is concerned.

## The caveat worth restating

`srs.py`'s own docstring says it: there is no objective answer to "what does
price do next", so the rating is self-assigned. A better scheduler fed
self-assigned grades is still fed self-assigned grades. The Arena schedules more
carefully than SM-2 did; it cannot make the input more honest, and the size of
the improvement is bounded by that, not by the arithmetic.
