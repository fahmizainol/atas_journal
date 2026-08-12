"""Does Rithmic honour a *native* trailing / breakeven bracket? — a spike.

THE QUESTION. The app's trail is a replay-practice knob today (LiveChart.tsx
sets ``trail: null`` on every live order) because on a real account Rithmic holds
the stop, not us. There are three ways to get a trail onto a funded account and
only one of them is worth building:

  A. ratchet it ourselves — call ``broker.modify()`` on a step grid. Works with
     code we already have, but it is arm-gated (broker.py:1198, 900s TTL), every
     rung costs a ``get_stop_and_target`` round trip plus a serialised leg
     modify ('Atomic order operation in progress'), and the loop dies with the
     process that owns it.
  B. ``trail_ticks`` on a plain order — async_rithmic supports it, but read
     ``plants/order.py``: the bracket branch sets ``template_id = 330`` and the
     trail branch is an ``elif`` *after* it (order.py:261). Pass ``stop_ticks``
     and your ``trail_ticks`` is silently dropped. It only ever reaches the wire
     on a bare order — no target, no fixed stop — which ``require_bracket``
     forbids anyway.
  C. a native trailing bracket. ``RequestBracketOrder`` (template 330) carries
     ``trailing_stop_trigger_ticks``, ``trailing_stop_by_last_trade_price``,
     ``break_even_ticks`` and ``break_even_trigger_ticks``, and ``BracketType``
     has non-``_STATIC`` variants. async_rithmic hardcodes ``*_STATIC`` and
     never sets any of those four fields (order.py:244, 249) — so C is a
     capability of the *protocol* that the client library does not expose,
     rather than anything Rithmic cannot do.

C is the one worth having: Rithmic holds the ratchet, so there is no arm to
lapse, no modify storm, and it survives a page reload or a dead API process.

THE RISK, AND WHY THIS IS A SPIKE AND NOT A FEATURE. Unrecognised protobuf
fields do not bounce — they arrive, get ignored, and the order works exactly as
if you had never set them. A 200 from the submit therefore proves nothing. The
only honest evidence is Rithmic *echoing the values back*, which is what every
stage below reads:

  * ``ExchangeOrderNotification`` carries ``trail_by_ticks`` and ``bracket_type``
  * ``ResponseShowBracketStops`` (template 341) carries ``stop_ticks``,
    ``bracket_trailing_field_id`` and ``trailing_stop_trigger_ticks``

If those come back as zeros and ``*_STATIC``, C is dead and the answer is A.

THE ANSWER, MEASURED 2026-08-10 ON MNQU6 (LucidPro, 1 lot, live money).
**C works, and Rithmic runs the ratchet itself.** A market buy filled at
29752.00 with ``stop_ticks=50, trailing_stop_trigger_ticks=25``:

    23:34:55  status  trail_by_ticks=50  trigger_price=29739.50   (-50t, as placed)
    23:35:29  modify                     trigger_price=29745.75   (-25t)
    23:35:33  modify                     trigger_price=29748.00   (-16t)
    23:35:37  modify                     trigger_price=29754.00   (+8t, past the fill)

Twelve modifies in 68 seconds, none of them ours. The semantics fall out of the
arithmetic: **``trailing_stop_trigger_ticks`` is when the trail wakes up** (25
ticks of profit) and **``stop_ticks`` is how far behind the high it then rides**
(50) — the first rung lands at 29745.75, which is 50 ticks under a high of
29758.25, which is the fill plus exactly the 25-tick trigger. ``trail_by_ticks``
on the stop leg reports 50, the ride distance, not the trigger.

WHERE THE RATCHET IS VISIBLE, WHICH IS NOT WHERE IT LOOKS LIKE IT SHOULD BE.
Once the entry fills, **the bracket legs become their own baskets** (the entry
was 188372279; the stop arrived as 188372282). Polling template 341 for the
entry's basket therefore returns nothing forever, which reads exactly like "the
trail is not working" and is not. The stop leg announces every rung as an
``ExchangeOrderNotification`` with ``report_type='modify'`` and a new
``trigger_price`` — push-based, unambiguous, and what ``--fill`` now reads.

Still unverified: the breakeven fields. ``ResponseShowBracketStops`` carries no
break-even column and nothing echoed one back, so whether
``break_even_trigger_ticks`` did anything on top of the trail is unknown — the
observed behaviour is fully explained by the trail alone.

THREE STAGES, EACH OPTED INTO SEPARATELY.

  --dry    (default) build the message locally and print it. No socket, no
           credentials. Proves the field names and enum values exist on the
           proto we ship, and shows the exact bytes stage 2 would send.
  --place  connect, rest a limit far from the market carrying the trailing
           bracket, read back what Rithmic echoes, then cancel it. Nothing is
           meant to fill.
  --fill   market in, qty 1, and watch the stop for --seconds to see whether the
           ratchet actually moves. Flattens in a finally. This is the only stage
           that answers the behavioural half of the question.

DEMO ACCOUNTS ONLY, AND THIS SCRIPT CANNOT CHECK THAT FOR YOU. Nothing Rithmic
sends says whether an account is funded — that is why the app makes you tag
accounts by hand — so stages 2 and 3 demand the account id spelled out and an
explicit flag rather than guessing and hoping. Size is pinned to 1 and is not an
argument.

Usage:
    uv run python demo/rithmic_trail_spike.py                       # dry, offline
    uv run python demo/rithmic_trail_spike.py --accounts            # who am I?
    uv run python demo/rithmic_trail_spike.py --place --account XXX --yes-demo
    uv run python demo/rithmic_trail_spike.py --fill  --account XXX --yes-demo

Credentials come from .env (RITHMIC_* keys, see .env.example).

ON "JUST USE THE TEST GATEWAY". You cannot, with the login you have.
``rituz00100.rithmic.com:443`` serves the system ``Rithmic Test``, which issues
its own logins — a prop-firm login on ``LucidTrading`` does not authenticate
there, and probe A of ``rithmic_smoke.py`` will say so. Changing
``RITHMIC_GATEWAY`` alone moves you to a gateway that has never heard of you.
The demo account for stages 2 and 3 therefore has to be a practice account
inside your own firm's system, or nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from async_rithmic import DataType, LastTradePresenceBits, RithmicClient, SysInfraType
    from async_rithmic.protocol_buffers.request_bracket_order_pb2 import RequestBracketOrder
    from google.protobuf.json_format import MessageToDict
except ImportError:  # pragma: no cover - dependency is Phase-5 only
    sys.exit("async_rithmic is not installed. Run: uv pip install async_rithmic")

# The credential loader and the password scrubber are the smoke test's, imported
# rather than copied: a second redaction filter is a second thing to forget to
# update, and a rejected login logs the whole request at ERROR.
from rithmic_smoke import EXCHANGE, SYMBOL, _creds, _RedactSecrets, _say  # noqa: E402

BRACKET_TEMPLATE = 330

# `_STATIC` is what async_rithmic always sends. The hypothesis this spike tests
# is that the static/dynamic pair is exactly the fixed-legs/Rithmic-manages-them
# distinction, and that the trailing and breakeven fields are only read on the
# dynamic side. If that is wrong, stage 2 says so in one line.
DYNAMIC_BRACKET = {
    RequestBracketOrder.BracketType.STOP_ONLY_STATIC: RequestBracketOrder.BracketType.STOP_ONLY,
    RequestBracketOrder.BracketType.TARGET_ONLY_STATIC: RequestBracketOrder.BracketType.TARGET_ONLY,
    RequestBracketOrder.BracketType.TARGET_AND_STOP_STATIC: RequestBracketOrder.BracketType.TARGET_AND_STOP,
}


def _dir(args) -> int:
    """+1 long, -1 short.

    Every asymmetry in this file reduces to this one number: which way is
    profit. A short's stop trails *down*, its resting limit sits *above* the
    market, and "25 ticks in front" is 25 ticks lower. Writing it once and
    multiplying keeps the sign out of the individual measurements, where a
    flipped one would read as a working mechanism doing nothing.
    """
    return 1 if args.side == "long" else -1


def _bracket_name(v) -> str:
    try:
        return RequestBracketOrder.BracketType.Name(int(v))
    except Exception:  # noqa: BLE001 — a display helper never fails a run
        return str(v)


# ------------------------------------------------------------------ the payload


def trailing_kwargs(args) -> dict:
    """The four fields async_rithmic never sets, plus the enum swap.

    Defaults are the exit grid's winners rather than round numbers: t25/be25 is
    what came out ahead across every sitting in the trail study, so if this
    lands the values are already the ones worth running.
    """
    out: dict = {}
    if args.trail_ticks:
        out["trailing_stop_trigger_ticks"] = args.trail_ticks
        # Rithmic can ratchet off the last trade or off the bid/ask. Last trade
        # is the one that matches how the replay measures it, so the paper and
        # real trails stay comparable rather than diverging by a spread.
        out["trailing_stop_by_last_trade_price"] = True
    if args.be_trigger_ticks:
        out["break_even_trigger_ticks"] = args.be_trigger_ticks
        # 0 is meaningful here — breakeven *at* the entry — so it is sent
        # whenever a trigger is, and never inferred from truthiness.
        #
        # It still will not reach the wire. These are proto3 singular scalars,
        # so a zero is indistinguishable from unset and gets dropped by the
        # serialiser: `break_even_ticks=0` asks for nothing and relies on
        # Rithmic defaulting the offset to zero anyway. Only a non-zero offset
        # is actually expressible, and if the default turns out not to be zero
        # this is where that bug will live.
        out["break_even_ticks"] = args.be_ticks
    return out


def patch_submit(plant, extra: dict, *, quiet: bool = False):
    """Fold `extra` into template 330 on its way past, and un-static the bracket.

    The same seam ``rithmic_smoke --aggregated`` uses for ``aggregated_quotes``:
    ``_build_request`` copies arbitrary kwargs onto the protobuf, so a field the
    library forgot needs no fork. It also means a typo raises KeyError here
    rather than travelling — ``_set_pb_field`` indexes ``fields_by_name``.

    Returns the sent kwargs by reference so the caller can print what actually
    went, not what it intended to send.
    """
    original = plant._send_and_collect
    sent: dict = {}

    async def _patched(template_id, **kwargs):
        if template_id == BRACKET_TEMPLATE:
            was = kwargs.get("bracket_type")
            kwargs["bracket_type"] = DYNAMIC_BRACKET.get(was, was)
            kwargs.update(extra)
            sent.update(kwargs)
            if not quiet:
                print(f"       bracket_type {_bracket_name(was)} → "
                      f"{_bracket_name(kwargs['bracket_type'])}")
                for k, v in extra.items():
                    print(f"       + {k} = {v}")
        return await original(template_id, **kwargs)

    plant._send_and_collect = _patched
    return sent


# ------------------------------------------------------------------- stage: dry


def stage_dry(args) -> int:
    """Build the message offline and print it. Nothing opens a socket."""
    print("\nDRY — the message stage 2 would send, built locally.\n")

    req = RequestBracketOrder()
    req.template_id = BRACKET_TEMPLATE
    req.symbol = args.symbol or f"{args.root}(front month)"
    req.exchange = EXCHANGE
    req.quantity = 1
    req.transaction_type = (RequestBracketOrder.TransactionType.BUY
                            if args.side == "long"
                            else RequestBracketOrder.TransactionType.SELL)
    req.duration = RequestBracketOrder.Duration.DAY
    req.price_type = RequestBracketOrder.PriceType.MARKET
    req.stop_ticks = args.stop_ticks
    req.stop_quantity = 1
    req.target_ticks = args.target_ticks
    req.target_quantity = 1
    req.bracket_type = RequestBracketOrder.BracketType.TARGET_AND_STOP

    extra = trailing_kwargs(args)
    for k, v in extra.items():
        setattr(req, k, v)

    # `preserving_proto_field_name` because the field names *are* the finding
    # here — camelCased keys would not match the proto, this script, or anything
    # a reader would grep for.
    wire = MessageToDict(req, preserving_proto_field_name=True)
    for name, value in sorted(wire.items()):
        print(f"    {name:<34} {value}")

    missing = [k for k in extra if k not in wire]
    print(
        "\n    The four fields below the fold are the ones async_rithmic never\n"
        "    sets (plants/order.py:244,249 hardcode the _STATIC bracket types):\n"
        + "".join(f"      {k}\n" for k in extra)
        + "\n    That they set cleanly here proves only that our protobuf knows the\n"
        "    field names. Whether Rithmic *reads* them is stage 2 — an ignored\n"
        "    field is accepted silently, which is the whole hazard.\n"
    )
    for name in missing:
        print(
            f"    Note: {name} is zero and so is absent from the message —\n"
            "    proto3 does not serialise a singular scalar at its default. Asking\n"
            "    for it exactly at the entry is the same bytes as not asking at all.\n"
        )
    return 0


# ------------------------------------------------------------------ live stages


async def _connect(args, *, ticker: bool) -> RithmicClient:
    creds = _creds()
    redact = _RedactSecrets(creds["password"])
    for name in ("", "rithmic"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(redact)

    plants = [SysInfraType.ORDER_PLANT]
    if ticker:
        plants.append(SysInfraType.TICKER_PLANT)

    print(f"gateway     {creds['url']}")
    print(f"system      {creds['system_name']}")
    print(f"user        {creds['user']}")

    client = RithmicClient(**creds)
    await client.connect(plants=plants)
    # Anything that throws between here and the return has to hang the socket up
    # behind it. The first version of this leaked a live ORDER-plant login on a
    # failed *print*, which is the kind of thing that leaves a session open at a
    # broker for no reason at all.
    try:
        # `SysInfraType` is a protobuf enum wrapper, so its members are bare
        # ints — `.name` is a Python-enum habit that does not apply here.
        _say(True, f"connected: {', '.join(SysInfraType.Name(p) for p in plants)}")
        return client
    except BaseException:
        await client.disconnect()
        raise


async def stage_accounts(args) -> int:
    """List the accounts this login can trade. Opens the ORDER plant, sends nothing."""
    client = await _connect(args, ticker=False)
    try:
        accounts = list(client.accounts)
        print(f"\n{len(accounts)} account(s) on this login:\n")
        for a in accounts:
            print(f"    {a.account_id:<20} {getattr(a, 'account_name', '')}")
        print(
            "\nNothing here says which of these is funded — Rithmic does not send it,\n"
            "which is why --place and --fill make you name one and say --yes-demo.\n"
        )
        return 0
    finally:
        await client.disconnect()


async def _mark(client, contract: str, timeout: float = 20.0) -> float | None:
    """One last-trade print, for pricing a limit far enough away not to fill.

    The guard is `RithmicFeed._on_tick`'s, field for field, because every clause
    of it is load-bearing and was learned the hard way:

      * the field is ``trade_price``. There is no ``price`` key, and reaching for
        one raises *inside the library's background task*, where the exception is
        logged and swallowed — so the future never resolves and the failure
        presents as a 20s hang that then reports "market shut".
      * a LAST_TRADE message can carry only derived fields (volume, vwap, net
        change) with no print in it at all. ``presence_bits`` is what says
        whether there is a trade here.
      * the opening snapshot repeats the last print with a stale stamp. Harmless
        for a mark, skipped anyway so this stays the same shape as the recorder.

    Nothing in here may raise for the same reason: a handler that throws takes
    the diagnosis with it.
    """
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_tick(data: dict) -> None:
        if got.done() or data.get("data_type") != DataType.LAST_TRADE:
            return
        if not data.get("presence_bits", 0) & LastTradePresenceBits.LAST_TRADE:
            return
        if data.get("is_snapshot"):
            return
        price = data.get("trade_price")
        if price is None:
            return
        got.set_result(float(price))

    client.on_tick += on_tick
    await client.subscribe_to_market_data(contract, EXCHANGE, DataType.LAST_TRADE)
    try:
        return await asyncio.wait_for(got, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        client.on_tick -= on_tick
        try:
            await client.unsubscribe_from_market_data(contract, EXCHANGE, DataType.LAST_TRADE)
        except Exception:  # noqa: BLE001
            pass


class _Tape:
    """A running last-trade price for the duration of a stage.

    ``_mark`` takes one print and unsubscribes, which is all a resting limit
    needs. Watching a ratchet needs the mark to keep moving — the whole question
    is whether the stop follows it — so this holds the subscription open and
    keeps the latest print in an attribute.
    """

    def __init__(self, client, contract: str) -> None:
        self.client = client
        self.contract = contract
        self.last: float | None = None

    async def __aenter__(self):
        async def on_tick(data: dict) -> None:
            if data.get("data_type") != DataType.LAST_TRADE:
                return
            if not data.get("presence_bits", 0) & LastTradePresenceBits.LAST_TRADE:
                return
            price = data.get("trade_price")
            if price is not None:
                self.last = float(price)

        self._on_tick = on_tick
        self.client.on_tick += on_tick
        await self.client.subscribe_to_market_data(
            self.contract, EXCHANGE, DataType.LAST_TRADE)
        return self

    async def __aexit__(self, *exc) -> None:
        self.client.on_tick -= self._on_tick
        try:
            await self.client.unsubscribe_from_market_data(
                self.contract, EXCHANGE, DataType.LAST_TRADE)
        except Exception:  # noqa: BLE001 — we are already leaving
            pass


def _watch(client, seen: list, entry: dict, trail: list) -> None:
    """Record every order/bracket message. The echo is the only real evidence.

    ``entry`` collects the average fill price, which is what the bracket's tick
    distances are measured from — without it a ``stop_ticks`` of 50 is a number
    with no price attached, and the ratchet cannot be read as a level.

    ``trail`` collects the ratchet itself, and it is collected *here* rather than
    polled because this is the channel that actually carries it. The first cut of
    this script polled template 341 for the entry's basket and saw nothing at all
    after the fill — Rithmic spawns the bracket legs as their own baskets the
    moment the entry fills, so the parent's id stops matching. The stop leg
    announces every rung as an ``ExchangeOrderNotification`` with
    ``report_type='modify'`` and a new ``trigger_price``, which is both push-based
    and unambiguous: a rising trigger_price on an order carrying
    ``trail_by_ticks`` IS the trail working.
    """

    async def on_exchange(m) -> None:
        avg = float(getattr(m, "avg_fill_price", 0.0) or 0.0)
        if avg and not entry.get("price"):
            entry["price"] = avg
            entry["side"] = int(getattr(m, "transaction_type", 0) or 0)
        trail_ticks = int(getattr(m, "trail_by_ticks", 0) or 0)
        trigger = float(getattr(m, "trigger_price", 0.0) or 0.0)
        # **A non-zero trigger_price is the discriminator, not trail_by_ticks.**
        # The first cut keyed on `trail_by_ticks`, which is set only when the
        # *trail* is running — so a breakeven-only order, whose stop moves once
        # and carries no trail distance, would have been recorded as never
        # moving. That is the same false negative as polling the parent basket,
        # arrived at from a different direction, and it is why this now keys on
        # the thing that distinguishes a stop leg from a target: a stop has a
        # trigger price and a limit target does not.
        if trigger:
            trail.append({
                "at": time.time(),
                "basket_id": str(getattr(m, "basket_id", "")),
                "trigger": trigger,
                "trail_ticks": trail_ticks,
                "report": str(getattr(m, "report_type", "")),
            })
        seen.append(("exchange", time.time(), {
            "basket_id": getattr(m, "basket_id", ""),
            "report_type": getattr(m, "report_type", ""),
            "status": getattr(m, "status", ""),
            "bracket_type": _bracket_name(getattr(m, "bracket_type", 0)),
            "trail_by_ticks": getattr(m, "trail_by_ticks", 0),
            "trail_by_price_id": getattr(m, "trail_by_price_id", 0),
            "avg_fill_price": avg,
            "trigger_price": getattr(m, "trigger_price", 0.0),
            "text": getattr(m, "text", ""),
        }))

    async def on_bracket(m) -> None:
        seen.append(("bracket", time.time(), {
            k: getattr(m, k, None)
            for k in ("basket_id", "stop_ticks", "target_ticks",
                      "bracket_trailing_field_id", "trailing_stop_trigger_ticks")
        }))

    client.on_exchange_order_notification += on_exchange
    client.on_bracket_update += on_bracket


async def _read_back(client, account_id: str, basket_id: str,
                     quiet: bool = False) -> dict | None:
    """Ask Rithmic what the bracket legs currently are (templates 338 / 340).

    ``ResponseShowBracketStops`` is the one message that carries
    ``bracket_trailing_field_id`` and ``trailing_stop_trigger_ticks`` — if the
    trailing fields survived the trip, this is where they show up.
    """
    try:
        stops = await client.plants["order"].list_bracket_stops(account_id=account_id)
    except Exception as e:  # noqa: BLE001 — a failed read is a result, not a crash
        _say(False, f"list_bracket_stops failed: {type(e).__name__}: {e}")
        return None

    # `stop_quantity` empty or "0" is a released/placeholder row — Rithmic sends
    # one alongside the real leg, and reading a verdict off it reports "the field
    # was ignored" while the row underneath carries the value. This is the same
    # filter `get_stop_and_target` applies for the same reason.
    mine = [s for s in stops
            if (not basket_id or s.basket_id == basket_id)
            and str(getattr(s, "stop_quantity", "")).strip() not in ("", "0")]
    if not mine:
        _say(None, f"no bracket stops returned for basket {basket_id or '(any)'}")
        _say(None, "legs may only exist once the entry fills — that is what --fill shows")
        return None

    s = mine[0]
    trailing = str(getattr(s, "trailing_stop_trigger_ticks", "") or "").strip()
    field_id = str(getattr(s, "bracket_trailing_field_id", "") or "").strip()
    out = {
        "stop_ticks": _int_or_none(getattr(s, "stop_ticks", "")),
        "trailing_ticks": _int_or_none(trailing),
        "field_id": field_id,
    }
    if not quiet:
        print(f"       stop_ticks={s.stop_ticks!r} "
              f"trailing_stop_trigger_ticks={trailing!r} "
              f"bracket_trailing_field_id={field_id!r}")
        verdict = trailing not in ("", "0")
        _say(verdict, "Rithmic echoed the trailing trigger back"
             if verdict else
             "trailing trigger came back empty — the field was accepted and ignored")
    return out


def _int_or_none(v) -> int | None:
    """Template 341 sends its numbers as strings, and blank for absent."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


async def _ratchet_watch(client, contract: str, entry: dict, trail: list,
                         args) -> None:
    """Watch the stop against a live mark. The one stage that tests *behaviour*.

    Reads the notification stream `_watch` is filling rather than polling, for
    the reason documented there: after the fill the stop is its own basket and a
    poll keyed on the entry's id returns nothing forever.

    Each rung is printed as a level and its distance from the entry, because the
    question "is this trailing" is really "has the stop passed breakeven yet" —
    and a trigger_price above the fill is the moment that answer becomes yes.
    """
    # Name the mechanism under test, because the two are only distinguishable by
    # shape and the reading of a single stop move depends on which is armed.
    if args.be_trigger_ticks and not args.trail_ticks:
        what = (f"BREAKEVEN ONLY — expect exactly one move, to "
                f"{args.be_ticks:+d} ticks from the fill, once "
                f"{args.be_trigger_ticks} ticks in profit")
    elif args.trail_ticks and not args.be_trigger_ticks:
        what = (f"TRAIL ONLY — expect repeated moves once {args.trail_ticks} "
                "ticks in profit")
    else:
        what = "TRAIL + BREAKEVEN — both armed, so a single move is ambiguous"
    print(f"\nWATCH — {args.seconds}s. {what}.")
    tick = args.tick_size
    dir_ = _dir(args)
    shown = 0
    #: The best price the trade has seen — a high when long, a low when short.
    #: Tracked through `dir_` so there is one definition of "best" rather than
    #: two branches that can disagree.
    best: float | None = None

    async with _Tape(client, contract) as tape:
        for elapsed in range(0, args.seconds, 5):
            await asyncio.sleep(5)
            mark = tape.last
            if mark is not None and (best is None or (mark - best) * dir_ > 0):
                best = mark
            px = entry.get("price")
            new = trail[shown:]
            shown = len(trail)
            if not new:
                up = ("—" if (px is None or best is None)
                      else f"{(best - px) * dir_ / tick:+.0f}t")
                print(f"    {elapsed + 5:>3}s  mark={mark or '—'}  best={up}  (no move)")
                continue
            for r in new:
                # Distance from the fill *in the trade's favour*: negative is
                # still risk on the table, positive means the stop is locking in
                # a gain. That sign flipping is the event this stage exists to
                # catch, and it has to read the same way on a short.
                off = ("—" if px is None
                       else f"{(r['trigger'] - px) * dir_ / tick:+.0f}t")
                print(f"    {elapsed + 5:>3}s  mark={mark or '—'}"
                      f"  stop→{r['trigger']:g}  ({off} vs fill)"
                      f"  [{r['report']}, trail_by_ticks={r['trail_ticks']}]")

    _ratchet_verdict(entry, trail, best, args)


def _wake_ticks(args) -> int:
    """The smallest profit that should make *something* move, over both
    mechanisms — used to tell "it did not fire" from "it was never asked to"."""
    triggers = [t for t in (args.trail_ticks, args.be_trigger_ticks) if t]
    return min(triggers) if triggers else 0


def _ratchet_verdict(entry: dict, trail: list, best: float | None, args) -> None:
    """Say what the run proved, including when it proved nothing.

    The inconclusive branch is the one that matters: a stop that never moved
    because price never cleared the trigger is not evidence against anything,
    and reporting it as a failure would retire a working feature.

    Breakeven and the trail are judged apart because they are distinguishable by
    shape. Breakeven fires **once**, onto a level that is knowable in advance —
    the fill plus ``break_even_ticks`` — while the trail moves repeatedly and
    tracks the high. Running only one of them at a time is what makes a single
    move attributable, which is why this stage is worth its own order.
    """
    print()
    px = entry.get("price")
    tick = args.tick_size
    dir_ = _dir(args)
    triggers = [r["trigger"] for r in trail]
    moves = len(set(triggers))
    be_only = bool(args.be_trigger_ticks) and not args.trail_ticks

    # The first trigger is where the stop was *placed*, not somewhere it moved
    # to, and the first level that differs from it is the mechanism's first act.
    # Isolated because a hand-drag later in the run — closing the position, say
    # — otherwise buries the one automatic move this stage came to measure.
    placed = triggers[0] if triggers else None
    first_move = next((t for t in triggers if placed is None or t != placed), None)
    if be_only and first_move is not None and px is not None:
        landed = (first_move - px) * dir_ / tick
        # Reported in the terms that decide whether this is usable, and NOT as
        # "did it match --be-ticks". The sign convention is the open question,
        # so a run probing it with a negative offset would have its *desired*
        # outcome scored as a mismatch. What matters either way is which side of
        # the fill the stop ended on.
        _say(None, f"placed at {placed:g}, first automatic move to {first_move:g}"
                   f" — {landed:+.0f} ticks from the fill.")
        if landed > 0:
            _say(True, f"the stop is IN PROFIT: {landed:.0f} ticks locked in. "
                       "This is the behaviour the replay ladder's `be` means.")
        elif landed < 0:
            _say(False, f"the stop is STILL AT RISK: {abs(landed):.0f} ticks the "
                        "wrong side of the fill. Rithmic moved it, but away from "
                        "profit — a buffer, not a lock.")
        else:
            _say(None, "the stop is exactly at the fill — breakeven gross, which "
                       "still owes the round turn.")
        # Then the mechanical reading, which is what tells us how to drive it.
        raw = (first_move - px) / tick   # unsigned by side: + is up in price
        _say(None, f"asked break_even_ticks={args.be_ticks:+d}; the stop landed "
                   f"{raw:+.0f} ticks up in raw price and {landed:+.0f} ticks in "
                   "the trade's favour. If those two disagree in sign, the field "
                   "is applied to the price and not to the direction — in which "
                   f"case a {args.side} wants "
                   f"{'+' if dir_ > 0 else '-'}N to lock N in.")
        if moves > 2:
            _say(None, f"{moves - 2} later move(s) too. Breakeven moves once, so "
                       "those are a hand-drag or another mechanism — read the "
                       "messages below before drawing anything from them.")
        return

    if moves > 1 and px is not None:
        first, last = triggers[0], triggers[-1]
        _say(True, f"the stop moved {moves - 1}× — {first:g} → {last:g} "
                   f"({(last - first) * dir_ / tick:.0f} ticks in your favour).")
        if be_only:
            # More than one move on a breakeven-only order is not what breakeven
            # means, and saying so beats filing it as a pass.
            _say(None, "breakeven was the only thing switched on, and breakeven "
                       "moves once. Something else is running — re-read the "
                       "messages below before trusting this.")
        if (last - px) * dir_ > 0:
            _say(True, f"and it passed the fill ({px:g}), locking in "
                       f"{(last - px) * dir_ / tick:.0f} ticks.")
        return

    if px is None or best is None:
        _say(None, "no fill price or no mark seen — inconclusive.")
        return
    best = (best - px) * dir_ / tick

    if moves == 1 and be_only:
        # The whole point of the run: one move, and is it where breakeven says?
        landed = (triggers[0] - px) * dir_ / tick
        if abs(landed - args.be_ticks) <= 1:
            _say(True, f"breakeven fired: one move, landing {landed:+.0f} ticks "
                       f"from the fill against break_even_ticks={args.be_ticks}. "
                       "Rithmic reads the breakeven fields.")
        else:
            _say(None, f"one move, but it landed {landed:+.0f} ticks from the "
                       f"fill and break_even_ticks asked for {args.be_ticks:+d}. "
                       "Rithmic did something — not necessarily what was asked.")
        return

    wake = _wake_ticks(args)
    # "in front" rather than "up": on a short the favourable direction is down,
    # and a verdict that says "up" while the number is negative reads as a bug
    # in the measurement rather than a quiet market.
    if best < wake:
        went = (f"only got {best:.0f} ticks in front" if best >= 0
                else f"never went in front at all ({best:.0f} ticks)")
        _say(None, f"the stop never moved, and never should have — the trade "
                   f"{went} against a {wake}-tick trigger. Inconclusive; run it "
                   "again when it is moving, or take the other side.")
    else:
        what = "breakeven" if be_only else "the trail"
        _say(False, f"the trade got {best:.0f} ticks in front, clearing the "
                    f"{wake}-tick trigger, and the stop did not move — {what} is "
                    "stored but not acted on.")


def _dump(m) -> dict:
    """Every set field on a protobuf message, under its real name."""
    try:
        return MessageToDict(m, preserving_proto_field_name=True)
    except Exception:  # noqa: BLE001 — a dump helper never fails a run
        return {k: getattr(m, k, None) for k in dir(m) if not k.startswith("_")}


async def _inspect(client, account_id: str, entry_basket: str, args) -> None:
    """Why does dragging a bracket leg answer 'placed without a stop'?

    The app sends the **leg's** basket id to ``modify_order(stop_ticks=...)``,
    which internally calls ``get_stop_and_target(basket_id=...)`` and filters
    ``list_bracket_stops`` by it. Pre-fill those rows come back under the
    *entry's* basket (measured in ``--place``), so if that is still true once the
    legs are live orders of their own, the leg id can never match and the library
    reports it as "No stop loss was set at order creation" — a sentence about a
    bracket that plainly exists.

    Two candidate fixes follow from two different facts, which is why this dumps
    rather than assumes:

      * rows keyed to the entry → send the parent basket for leg modifies
      * rows gone or zero-quantity → the bracket table is not the right channel
        at all once filled, and a leg has to be moved as the stop order it now
        is (``trigger_price`` on template 314)

    Then it tries both modifies, so one run says which the fix is.
    """
    plant = client.plants["order"]
    print("\nINSPECT — the raw tables while a bracket is live\n")

    for label, call in (
        ("list_orders", lambda: plant.list_orders(account_id=account_id)),
        ("list_brackets", lambda: plant.list_brackets(account_id=account_id)),
        ("list_bracket_stops", lambda: plant.list_bracket_stops(account_id=account_id)),
    ):
        try:
            rows = await call()
        except Exception as e:  # noqa: BLE001 — a failed read is a result
            _say(False, f"{label}: {type(e).__name__}: {e}")
            continue
        print(f"    {label} — {len(rows or [])} row(s)")
        for r in rows or []:
            d = _dump(r)
            # Trimmed to the fields this question turns on, plus anything with
            # 'basket' in the name, since the whole point is which id is which.
            keep = {k: v for k, v in d.items()
                    if "basket" in k or k in ("stop_ticks", "target_ticks",
                                              "stop_quantity", "target_quantity",
                                              "trigger_price", "price_type",
                                              "quantity", "user_tag", "status",
                                              "trailing_stop_trigger_ticks",
                                              "bracket_trailing_field_id")}
            print(f"      {keep}")
        print()

    # The decisive pair. Same requested distance, two different basket ids.
    legs = []
    try:
        stops = await plant.list_bracket_stops(account_id=account_id)
        legs = [str(s.basket_id) for s in (stops or [])]
    except Exception:  # noqa: BLE001
        pass
    orders = []
    try:
        orders = await plant.list_orders(account_id=account_id)
    except Exception:  # noqa: BLE001
        pass
    stop_leg = next((str(o.basket_id) for o in (orders or [])
                     if float(getattr(o, "trigger_price", 0) or 0)), "")

    print("    modify attempts — same distance, different basket id")
    for label, basket in (("the stop LEG's basket (what the app sends)", stop_leg),
                          ("the ENTRY's basket", entry_basket),
                          *[(f"a bracket-table basket {b}", b) for b in legs[:1]]):
        if not basket:
            _say(None, f"{label}: no id to try")
            continue
        try:
            await plant.modify_order(basket_id=basket, account_id=account_id,
                                     stop_ticks=args.stop_ticks - 4)
            _say(True, f"{label} ({basket}): ACCEPTED — this is the fix")
        except Exception as e:  # noqa: BLE001 — the failure IS the measurement
            _say(False, f"{label} ({basket}): {type(e).__name__}: {e}")


async def _cleanup(client, account_id: str) -> None:
    """Cancel everything, then flatten. Loud on failure — a spike that leaves an
    order resting on a demo account is a spike that lied about being safe."""
    print("\nCLEANUP")
    for what, call in (
        ("cancel_all_orders", lambda: client.cancel_all_orders(account_id=account_id)),
        ("exit_position", lambda: client.exit_position(
            account_id=account_id, symbol=client._spike_contract, exchange=EXCHANGE)),
    ):
        try:
            await call()
            _say(True, what)
        except Exception as e:  # noqa: BLE001 — try both regardless
            _say(False, f"{what}: {type(e).__name__}: {e} — CHECK THE PLATFORM")


async def _run_live(args, *, fill: bool) -> int:
    client = await _connect(args, ticker=True)
    account_id = args.account
    ids = [a.account_id for a in client.accounts]
    if account_id not in ids:
        _say(False, f"{account_id!r} is not on this login: {', '.join(ids)}")
        await client.disconnect()
        return 1

    # The micro by default, and only the stages that send orders default at all.
    # A spike whose purpose is to risk as little as possible while learning one
    # fact should not need a flag to be cheap — the mini is the thing you have to
    # ask for. `--root NQ` is there for the day the answer needs confirming on
    # the contract that will actually be traded.
    contract = args.symbol or await client.get_front_month_contract(args.root, EXCHANGE)
    if not contract:
        _say(False, f"no front-month contract for {args.root}/{EXCHANGE} — "
                    "is this login entitled to it?")
        await client.disconnect()
        return 1
    client._spike_contract = contract
    _say(True, f"account {account_id}, contract {contract}")

    seen: list = []
    entry: dict = {}
    trail: list = []
    _watch(client, seen, entry, trail)

    extra = trailing_kwargs(args)
    print("\nSUBMIT")
    sent = patch_submit(client.plants["order"], extra)

    basket = ""
    try:
        kwargs: dict = {
            "account_id": account_id,
            "stop_ticks": args.stop_ticks,
            "target_ticks": args.target_ticks,
        }
        from async_rithmic import OrderType, TransactionType

        if fill:
            otype = OrderType.MARKET
        else:
            mark = await _mark(client, contract)
            if mark is None:
                _say(False, "no last-trade print in 20s — market shut? cannot price a resting limit")
                await client.disconnect()
                return 1
            # Far enough away that it rests: a bid *under* the market, an offer
            # *over* it. Getting this sign wrong would rest the order straight
            # through the book and fill the stage that promised not to.
            kwargs["price"] = round(
                mark - _dir(args) * args.away_ticks * args.tick_size, 2)
            otype = OrderType.LIMIT
            _say(None, f"last {mark}, resting a "
                       f"{'buy' if args.side == 'long' else 'sell'} at "
                       f"{kwargs['price']} ({args.away_ticks} ticks "
                       f"{'below' if args.side == 'long' else 'above'})")

        res = await client.submit_order(
            order_id=f"spike-{int(time.time())}",
            symbol=contract, exchange=EXCHANGE, qty=1,
            order_type=otype,
            transaction_type=(TransactionType.BUY if args.side == "long"
                              else TransactionType.SELL),
            **kwargs)
        basket = str(getattr((res or [None])[0], "basket_id", "") or "")
        _say(True, f"accepted, basket_id={basket or '(none returned)'}")
        _say(None, "acceptance proves nothing on its own — an unread field is "
                   "accepted in exactly this way")

        print("\nECHO")
        await asyncio.sleep(2.0)
        await _read_back(client, account_id, basket)

        if fill and args.inspect:
            # The bracket has to be live for the tables to say anything, so this
            # runs after the fill and before the flatten.
            await asyncio.sleep(3.0)
            await _inspect(client, account_id, basket, args)
        elif fill:
            await _ratchet_watch(client, contract, entry, trail, args)
    finally:
        await _cleanup(client, account_id)

        print(f"\nMESSAGES ({len(seen)})")
        for kind, ts, fields in seen:
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            print(f"    {stamp} {kind:<9} {fields}")

        if sent:
            print("\nWHAT WENT OUT")
            for k in sorted(extra):
                print(f"    {k:<34} {sent.get(k)}")
            print(f"    {'bracket_type':<34} {_bracket_name(sent.get('bracket_type'))}")

        await client.disconnect()

    print(
        "\nREAD IT LIKE THIS: trail_by_ticks or trailing_stop_trigger_ticks coming\n"
        "back non-zero, or a bracket_type that stayed non-_STATIC, is option C\n"
        "working — build it. Zeros and _STATIC everywhere mean the fields were\n"
        "accepted and dropped, and the answer is option A: a server-side ratchet\n"
        "on a step grid, with the arm exemption `flatten` already has.\n"
    )
    return 0


# ------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="(default) build the message offline")
    ap.add_argument("--accounts", action="store_true", help="list tradable accounts, send nothing")
    ap.add_argument("--place", action="store_true", help="rest a limit far from market, read the echo, cancel")
    ap.add_argument("--fill", action="store_true", help="market in qty 1 and watch the stop, then flatten")
    ap.add_argument("--account", help="account id — required for --place/--fill")
    ap.add_argument("--yes-demo", action="store_true",
                    help="you have checked this account is a demo. Nothing Rithmic "
                         "sends can confirm it, so this is your word, not a check.")
    ap.add_argument("--side", choices=["long", "short"], default="long",
                    help="which way to take it. Pick the side the market is "
                         "actually moving: the mechanism only fires once the "
                         "trade is in profit, so a long into a drift down "
                         "returns 'inconclusive' and costs you the round turn.")
    ap.add_argument("--symbol", help="raw contract, e.g. MNQU6; overrides --root")
    ap.add_argument("--root", default="MNQ",
                    help="root to resolve the front month from. Defaults to the "
                         "MICRO: a tenth of the money for the same tick geometry, "
                         "which is the whole reason this is testable at all.")
    ap.add_argument("--stop-ticks", type=int, default=50)
    ap.add_argument("--target-ticks", type=int, default=120)
    ap.add_argument("--trail-ticks", type=int, default=25,
                    help="trailing_stop_trigger_ticks (0 = omit). Default is the exit grid's t25.")
    ap.add_argument("--be-ticks", type=int, default=0,
                    help="break_even_ticks — where the first rung lands. 0 is breakeven gross.")
    ap.add_argument("--be-trigger-ticks", type=int, default=25,
                    help="break_even_trigger_ticks (0 = omit). Default is be25.")
    ap.add_argument("--away-ticks", type=int, default=200, help="how far --place rests from the mark")
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--seconds", type=int, default=60, help="how long --fill watches")
    ap.add_argument("--inspect", action="store_true",
                    help="with --fill: dump the raw bracket tables while a "
                         "position is open and try the leg-vs-entry modify both "
                         "ways. Answers why dragging a stop on the chart says "
                         "'placed without a stop'. Flattens straight after.")
    ap.add_argument("--debug", action="store_true", help="full rithmic protocol logging")
    args = ap.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("rithmic").setLevel(logging.DEBUG)

    if args.place or args.fill:
        # Two separate refusals rather than one: "you forgot the account" and
        # "you have not said this is a demo" are different mistakes and the
        # second one is the expensive one.
        if not args.account:
            sys.exit("--place/--fill need --account. Run --accounts to see them.")
        if not args.yes_demo:
            risk = args.stop_ticks * args.tick_size * (2.0 if args.root == "MNQ"
                                                       else 20.0)
            sys.exit(
                f"This sends a real order to {args.account}.\n"
                "Nothing Rithmic sends says whether an account is funded, so this "
                "script cannot check for you — and the Rithmic Test gateway is no "
                "help either, since it issues its own logins that a prop-firm "
                "login does not authenticate against.\n"
                f"What --{'fill' if args.fill else 'place'} risks as configured: "
                f"{args.root} x1, a {args.stop_ticks}-tick stop = "
                f"${risk:,.0f}"
                + (" if it fills — and --place rests 200 ticks away precisely so "
                   "it does not.\n" if not args.fill else ".\n")
                + "Pass --yes-demo when you have read that and meant it.")
        if args.fill:
            print(f"\n⚠  MARKET {'BUY' if args.side == 'long' else 'SELL'}, "
                  f"qty 1 {args.root}, on {args.account}. Flattens after "
                  f"{args.seconds}s or on Ctrl-C.\n")
        return asyncio.run(_run_live(args, fill=args.fill))

    if args.accounts:
        return asyncio.run(stage_accounts(args))

    return stage_dry(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted — if a stage was mid-flight, CHECK THE PLATFORM for "
              "resting orders or an open position")
        sys.exit(130)
