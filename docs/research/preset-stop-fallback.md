# The preset stop before 10:00: tonight beats yesterday

*2026-08-21 · `data/research/preset-stop/pre_open_ruler.py` + `calibrate.py` · 601 cached sessions*

> **Retired 2026-08-25 — the finding is sound, the ruler it was fitted for is
> gone.** The presets no longer read a 500-print bar inside 10:00–16:00: they
> read a bar **from the 09:30 bell onwards**, with no fallback behind it at all,
> and *which* bar is now a toggle on the panel — **500t, 30s or 15s**, defaulting
> to 30s. That change deletes the question this study answered. Before the bell —
> and until the selected bar has closed three times — the presets simply have no
> stop.
>
> The 500-print reading survives as one of the three, so this study's bucketing
> can still be read; what is gone is the fallback chain in front of it and the
> 10:00 window behind it.
>
> Everything below still holds *for a print-counted bar*, and one result is the
> reason nothing was carried over. The 1.06 correction exists because an
> overnight 500-print bar spans **more clock time** than a session one and so
> reads wide. At a fixed 30 seconds that inverts — a thin overnight bar holds a
> fraction of the prints and reads **narrow** — so the same constant would push
> an already-too-tight stop tighter. Re-fitting it was offered and declined: "no
> preset before the bell" is the one answer that cannot be wrong in the
> dangerous direction. `pre_open_ruler.py` and `calibrate.py` still run if the
> question ever comes back at the new bucketing.
>
> **The second finding is the one that survived, and it changed sign in
> practice.** The open really is wider even volume-clocked — 09:30–10:00 runs
> 1.12× the settled median on 76% of sessions — and at a *clock* bar the effect
> is far larger, because the open's 30 seconds hold several times the prints of
> the afternoon's. That was the argument for a 10:00 window when the question
> was "what is this day's character". It is the argument *against* one here:
> a bracket set at 09:32 is set against the open, so the ruler measures the
> open. Observed on NQZ4 2024-12-05 through `presetcheck`: **54t at 09:32
> against 39t at 09:37**. The stop is meant to move like that.

## The question

The bracket presets (`lib/orderPresets`) set every stop from the 500-print median
bar range inside 10:00–16:00 ET. That number does not exist before 10:00, and the
first build fell back to **yesterday's** settled median — which covers the whole
09:30–10:00 stretch, i.e. exactly when the bracket gets set.

A day-old number carried across a night is a strange thing to size a stop with.
Two questions, then:

1. **Is the fallback needed at all?** The 10:00 window is a *clock-bar* rule: the
   first half hour runs 2–4× the rest of the day and would drag a median of time
   bars. A 500-print bar is volume-clocked — a fast tape makes *more* bars, not
   bigger ones — so the open may not be wider at this bucketing, and the window
   could open at the bell.
2. **If it is needed, what beats yesterday?** The obvious candidate is on the
   same tape and hours old rather than a day: **tonight's own median**, measured
   the same way over the prints before the bell.

## What was run

Every cached session — 601 of them, NQH4 through NQU6 — bucketed into 500-print
blocks anchored at the tape's first print, exactly as `FixedTickRuler` anchors
them, and each block filed by the ET wall clock of its **first** print.

The truth per day is measured two ways, because "what should the stop be" is two
questions and they need not have the same answer:

- **`settled`** — that day's 10:00–16:00 median, the number the developing
  reading walks toward. What the ruler is a causal estimate *of*.
- **`open30`** — that day's 09:30–10:00 median. The volatility a bracket set at
  the bell actually trades in, which is the window the fallback is used in.

Every predictor is complete before the bell. Scored **out of sample**: the
sessions are split by date, each constant is fitted on one half and graded on the
other, then the halves are swapped. A scale fitted on the days it is then graded
on cannot lose, and would have made the night look better than it is.

Where two numbers appear in a `÷` label the two halves fitted different
constants, and the gap is itself the finding — a window whose constant moves
between eras is a window whose constant you cannot ship.

## Question 1: the open really is wider

| | ratio of 09:30–10:00 median to the settled median |
|---|---|
| p10 | 0.93 |
| **p50** | **1.12** |
| p90 | 1.38 |
| wider on | 76% of sessions |

The volume clock absorbs most of the open — a *time*-bar version of this would
read 2–4× — but not all of it. 1.12× is real and one-sided. **The 10:00 window
stays**, and the fallback is worth getting right rather than deleting.

## Question 2: what to fall back to

Against **`open30`**, the half hour a bell-set bracket lives in. Two columns per
metric: fitted on the older half → scored on the newer, and the reverse.

| predictor | bias | typical error | p90 error | within ±20% |
|---|---|---|---|---|
| the whole night ÷ 1.15 | 1.00 / 1.00 | **7.8% / 8.4%** | 21% / 20% | 89% / 89% |
| ⅓ yesterday + ⅔ night | 0.97 / 1.04 | 8.2% / 8.3% | 20% / 21% | 91% / 88% |
| **07:00–09:30 ÷ 1.06** | **0.99 / 1.01** | **8.4% / 9.5%** | 23% / 24% | 86% / 85% |
| 08:30–09:30 ÷ 1.01 / 0.98 | 0.97 / 1.03 | 10.0% / 11.8% | 23% / 31% | 84% / 74% |
| yesterday's settled median | **0.83 / 0.92** | **18.5% / 11.6%** | 35% / 28% | 58% / 75% |

And against **`settled`**, the day's own character, where yesterday does much
better — it is, after all, a whole session's worth of the same instrument:

| predictor | bias | typical error | within ±20% |
|---|---|---|---|
| ⅓ yesterday + ⅔ night | 1.06 / 0.95 | 10.9% / 9.5% | 71% / 85% |
| ½ yesterday + ½ night | 1.05 / 0.97 | 11.1% / 8.9% | 69% / 84% |
| the whole night ÷ 1.24 / 1.36 | 1.09 / 0.92 | 13.1% / 11.3% | 68% / 83% |
| yesterday's settled median | 0.97 / 0.98 | 15.4% / 10.5% | 66% / 78% |

## What this says

**The night was never short of information.** Uncorrected it ranks the days
better than yesterday does — Spearman 0.86 against 0.79 — and is simply *wide*,
because a 500-print bar overnight spans more clock time than one at midday. That
is a constant, not noise, and both halves of the sample agree on it.

**Yesterday's error is one-sided, in the wrong direction.** Bias 0.83–0.92
against the open means it sets the stop 8–17% *tighter* than the half hour it is
used in. Of the two ways to be wrong about a stop, that is the expensive one.

**The blend is the best hedge and the worst dependency.** ⅓ yesterday + ⅔ night
is at or near the top of both tables. But yesterday requires prior days drawn,
and on `/charts/live` with "Prior days: none" there is no yesterday at all — so
the blend silently degrades to the night alone on exactly the page where the
money is real.

## Adopted

The fallback chain in `presetStop`:

1. today's settled median, once three blocks have printed inside 10:00–16:00;
2. else **the 07:00–09:30 median ÷ 1.06** (`NIGHT_SCALE`);
3. else yesterday's settled median — a tape with no night on it at all.

**07:00–09:30 rather than the whole night**, at a cost of about 0.6pp of
accuracy, because it is a *fixed clock window*. "Everything before the bell"
spans however much tape happened to load, and the live feed's backfill has been
seen to come back short (`api/routers/live.py`, the `globex_anchor_ms` note) —
which would quietly change what the reading spans and therefore what the 1.15 in
front of it meant. A fixed window either has bars in it or it does not. Its
constant is also the more stable of the two across the split: 1.06 / 1.05 against
1.24 / 1.36 on the settled truth.

The correction lives in `lib/orderPresets` and not in the ruler: `FixedTickRuler`
reports what it measured, and the rule that turns a reading into a stop owns the
constant.

**Not part of this study:** the presets also add a flat `LEG_ROOM` (5 ticks) to
every live distance leg after the shape is worked out. That is a hand-chosen
cushion, measured against nothing here — everything above is about the *reading*,
which is what the room is added to. The panel shows both numbers (`⊥ 50t 45+5`)
so the measured part and the chosen part are never confused for one another.

## What is not claimed

Nothing here says a stop placed this way earns more. It is a claim about
*estimating today's bar size before the bell*, which is the question the fallback
was already answering badly — the same distinction [vol-sizing](vol-sizing.md)
draws between where a stop **is** and what it **earns**. The 1.5R and 1R targets
the presets hang off that stop remain untested against
[the exit study](replay-trail-whatif.md).
