# What an order takes to reach the market — and what the replay owes it

**Date:** 2026-08-13
**Data:** 27 live orders with a complete `latency` record, across 8 session-days
of `data/live/orders/*/*/orders.jsonl` (NQU6 and MNQU6, Aug 7 – Aug 13 2026).
Every send writes one `latency` line per answer, each carrying everything known
so far, so the last line for a tag is that order's whole timeline.
**Script:** `data/research/order-latency/measure.py` (numbers in `summary.json`).
**Verdict:** the wire round trip is **239 ms and remarkably stable** (sd 4.7 ms);
our own gate costs nothing (0.1 ms); the browser's leg is bimodal and is a UI
cost, not a market one. **Adopted:** a fourth knob in the fill model,
`latencyMs`, defaulting to **250 ms** — the round trip, not half of it, for the
reason in §3. Replay and the Live page's paper path both charge it.

---

## 1. The legs, and why they are not a sum

Nothing here is a difference between two machines' wall clocks. The browser
times its own press; the API times its own wire call. A press stamp shipped to
the server and subtracted from `time.time()` would report the browser/WSL skew
as latency, plausibly and silently — which is why `broker.py` refuses to do it.

| leg | n | min | p50 | mean | p90 | max | sd |
|---|---|---|---|---|---|---|---|
| `client_ms` — the browser's press → response in hand | 27 | 252.0 | 286.4 | 367.5 | 520.6 | 524.3 | 127.2 |
| `net_ms` — `client_ms − api_ms`: fetch, dev proxy, React | 27 | 5.7 | 46.5 | 128.3 | 280.2 | 283.2 | 125.4 |
| `api_ms` — the whole request handler | 27 | 231.7 | 239.1 | 239.2 | 242.0 | 255.5 | 5.1 |
| `gate_ms` — our guards, day arithmetic, journal write | 27 | 0.1 | 0.1 | 0.1 | 0.2 | 0.2 | 0.0 |
| `plant_ms` — our submit → the plant's basket id | 27 | 228.3 | 236.1 | 236.2 | 239.2 | 250.4 | 4.9 |
| `exch_ms` — the wire → the exchange's first word | 27 | 232.2 | 238.8 | 239.1 | 242.5 | 253.3 | 4.7 |

Three readings fall straight out:

- **The guardrails are free.** `gate_ms` is a tenth of a millisecond. Whatever
  an order costs, none of it is the discipline layer — worth knowing, because
  that layer keeps growing and the temptation to blame it will recur.
- **The wire is everything, and it is a constant.** `plant_ms` and `exch_ms`
  agree to within 3 ms and both have a standard deviation under 5 ms on a
  ~237 ms mean — 2% of the value. A round trip that stable is dominated by
  *transit*, not by anything the plant or the exchange is thinking about. This
  is the geography of the desk, and it will only change by moving the desk.
- **`net_ms` is bimodal and is not a market cost.** Two clusters: eleven orders
  at 270–283 ms and the rest at 6–47 ms. That is the browser and the dev proxy,
  not Chicago. It belongs in a UI budget, not in a fill model — an order does
  not fill any worse because React took 200 ms to get round to the handler, it
  just *felt* worse. (It is still worth fixing; it is the entire difference
  between a 520 ms press and a 254 ms one.)

## 2. So the felt number is ~250 ms, and it is almost all wire

The `client_ms` figure the routing panel shows — the one that prompted this —
is **252–260 ms once the browser's slow path is out of the way**, and of that,
239 ms is the wire round trip and ~10 ms is the browser's own outbound leg.

## 3. Why the replay charges the round trip, not the one-way hop

The obvious objection to putting 250 ms into a fill model: the order is at the
matching engine well before the acknowledgement gets back, so surely the sim
should charge the one-way hop — call it 120 ms — and not the round trip.

That is right about the order and wrong about the model, because **there are two
delays between the price you react to and the price you get, and the replay has
neither**:

1. the print on your screen left the exchange one hop ago — you are always
   trading a picture of the past;
2. your order takes another hop to get back there.

The replay page shows you the tape at the exact millisecond it happened and acts
on your click at the exact millisecond you made it. To reproduce the live gap
between *the print you clicked* and *the print that filled you*, it therefore
has to charge both hops — the whole round trip — even though the order itself
only travels one of them. `exch_ms` (239 ms) is a direct measurement of exactly
that quantity, and `250` is it plus the browser's own outbound leg.

## 4. What pays, and what does not

The asymmetry is the point of modelling this at all:

| lands late | lands on time |
|---|---|
| a market order | a bracket's stop |
| a resting order arriving | a bracket's target |
| a drag of a working order's levels | the trail's ratchet |
| a drag of the position's bracket | |
| a cancel | |
| a manual flatten | |

Everything in the left column is a gesture on the wire. Everything in the right
column is already resting at the exchange, so it triggers on the print that
reached it. **Your entry and your manual exit get worse; your stop does not.**
A cancel that was in flight when the level printed does not save you, and a drag
that was in flight when the stop was hit did not happen yet — both of which are
real experiences the replay could not previously produce.

One deliberate simplification: a *resting* order strictly only owes the outbound
hop, since once it is sitting at the exchange the feed delay is irrelevant to it.
It is charged the full round trip anyway, which makes it arrive later than
reality. That errs the conservative way — an order that arrives later can only
fill later or not at all — and it is the same side the queue rule already errs
on (`fill-model-verification.md` §4): the sim may be slightly harder than live,
never easier.

## 5. What this does not model

- **Playback speed.** The lag is wall-clock milliseconds applied to tape
  milliseconds. At 1× that is exact. At 4×, the tape covers four times the
  distance in a real 250 ms, so a sitting watched fast is charged a quarter of
  the market movement the same click would have cost live. The ticket stamps
  `speed` next to `latencyMs` for this reason; the two only read as one story
  together. Fixing it properly needs the playback rate stamped on each gesture,
  which the log does not carry today.
- **The distribution.** One constant, not a draw. The wire's sd of 4.7 ms says
  a constant is a good description of *this* connection; the browser's 127 ms sd
  says nothing about the market. If the wire ever becomes bimodal — a different
  route, a different city — this becomes the wrong shape rather than the wrong
  number.
- **Queue position gained or lost.** A resting order that arrives 250 ms later
  is also further back in a queue this model does not have.
- **Rejects and partials.** Still absent, still by design.

## 6. Reproducing

```
python data/research/order-latency/measure.py
```

Re-run it after any move, ISP change, or Rithmic gateway change — `latencyMs` is
the one knob in the fill model that is a fact about *this desk* rather than
about the instrument or the firm. Everything else in `DEFAULT_FILL_MODEL`
travels; this one does not.
