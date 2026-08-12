"""The order plant — the one place in this repo that can send an order.

Everything else in ``journal.live`` watches. This module is the exception, and
it is deliberately the smallest surface that can do the job: submit, cancel,
flatten, and keep an honest picture of what the broker thinks is true.

IT RIDES ON THE FEED'S CONNECTION, AND THAT IS FORCED. Rithmic allows **one
concurrent session per login** — measured during the access probe, and again by
the harvest sweep, which is why that runs on the live client too. A broker with
its own ``RithmicClient`` would log the tick feed out the moment it connected.
So ``RithmicFeed`` opens ``ORDER_PLANT`` and ``PNL_PLANT`` alongside the ticker
when a broker is attached, and hands the connected client here. Three
consequences fall out of that, all of them wanted:

  - **No feed, no routing.** The order path cannot exist without the tape it is
    being traded against.
  - **A dropped socket drops everything actionable.** ``detach`` runs in the
    feed's ``finally``, so a reconnect comes back with nothing staged and
    nothing reconciled — it has to re-ask the broker what is true before it will
    send anything, because a picture that survived a disconnect is a picture of
    a situation that no longer exists.
  - **Routing is opt-in at connect time.** The plants are chosen once, so a
    session started without routing cannot acquire it later — the shadow-only
    connection is the same shape it always was, ORDER plant never opened
    (docs/live-shadow-plan.md decision 2).

THE BROKER IS THE AUTHORITY, NOT THIS PROCESS. Working orders and the position
come from Rithmic's own notifications and snapshots — never from what we
believed after sending something. That is the whole difference between this and
the paper blotter on the same page: the blotter is a fold over the tape and can
be re-derived, and this cannot be derived at all, only asked for. Hence
``reconcile``, which runs before the surface is allowed to draw anything and is
the reason a restarted API does not assume flat.

THE GUARDRAILS ARE ENFORCED HERE, NOT DECIDED HERE. The rules live in
``journal.live.routing`` where they can be tested without a market; what this
module owns is the one input they cannot compute — the day's realised total,
folded out of the fill stream — and the two places the check has to run. It runs
at ``preview`` so a refusal lands on the review, and again inside ``_submit``,
because a token minted while the day was still open is spent later and "was this
allowed when you staged it" is not the question. An order that *reduces* the
position skips the layer entirely: a discipline rule that could refuse a
scale-out would be, at the worst moment, a rule that keeps you in a trade.

WHAT IS NOT HERE. Strategy routing: nothing in the shadow shelf can reach this
module, by construction — ``shadow.py`` imports nothing from it and the only
callers are request handlers acting on a person's click. Manual-only is the
honest first step, and it skips the entire "did the engine mean this fill"
problem (app-backlog Charts §1).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from ..config import DATA_DIR
from .routing import (PAPER, Confirms, DayState, Intent, Policy,
                      day_refusal, tag_of)

LIVE_ORDER_DIR = DATA_DIR / "live" / "orders"

# How long a request handler waits on the feed's event loop before giving up.
# Generous for a submit (it is a round trip to Chicago and back through a loop
# that is also draining a tape), tight enough that a wedged loop is an error
# rather than a hung page.
CALL_TIMEOUT_S = 20.0
# How many finished orders to keep in memory for the panel. The journal on disk
# is the record; this is just what the page shows without a fetch.
RECENT_MAX = 50
# The floor under a scaled-down commission. $0.50/side is a $1.00 round turn,
# measured off a Lucid micro fill — brokers price micros on a per-ticket floor
# rather than pro rata, so scaling the mini rate by contract size alone lands
# too low. See `Broker.commission_per_side`.
MICRO_COMMISSION_FLOOR = 0.50
# Rithmic's bracket-order request. Its trailing fields are in the protobuf but
# not in async_rithmic's `submit_order`, so `_patch_order_plant` folds them in.
BRACKET_TEMPLATE = 330


def _dynamic_bracket(static_type):
    """`TARGET_AND_STOP_STATIC` -> `TARGET_AND_STOP`, and the other two pairs.

    `*_STATIC` means legs that sit where they were put; the plain variants are
    the ones Rithmic manages. Verified on MNQU6 2026-08-10: sent as
    TARGET_AND_STOP, the stop ratcheted twelve times in 68s and crossed the fill
    into profit without a single modify from this process. See
    demo/rithmic_trail_spike.py for that run.

    Resolved off the protobuf rather than written down as integers — the numbers
    are an implementation detail of somebody else's schema, and this file imports
    async_rithmic lazily everywhere else for the same reason.
    """
    from async_rithmic.protocol_buffers.request_bracket_order_pb2 import (
        RequestBracketOrder as _R,
    )

    bt = _R.BracketType
    return {bt.STOP_ONLY_STATIC: bt.STOP_ONLY,
            bt.TARGET_ONLY_STATIC: bt.TARGET_ONLY,
            bt.TARGET_AND_STOP_STATIC: bt.TARGET_AND_STOP}.get(static_type,
                                                               static_type)


def _s(v) -> str:
    return "" if v is None else str(v)


def _i(v) -> int:
    """Rithmic sends numbers as ints, empty strings and occasionally as text."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN-safe


def _px(v) -> float | None:
    """A price field, with Rithmic's zero read as "this does not apply".

    Every price on an order message is present whatever kind of order it is: a
    limit carries ``trigger_price=0.0`` and a stop carries ``price=0.0``, and
    the one that means nothing for that kind arrives as the zero rather than
    absent. Passed through as 0.0 it stops being a "no" and becomes a price —
    and a consumer coalescing the two fields (``trigger_price ?? price``) then
    reads a limit order as resting at zero, which is how a **live, working limit
    order went undrawn on the chart** while it was filling (2026-08-11, MNQU6).

    Nothing this app routes trades anywhere near zero, so the zero is
    unambiguous and this is the one place it has to be spent.
    """
    f = _f(v)
    return f if f else None


def _as_order(rec: dict, account_id: str, exchange: str):
    """A held order record, shaped as the object ``modify_order`` wants.

    Passing ``order=`` skips an internal ``get_order()`` — which iterates every
    account calling ``list_orders``, i.e. several round trips before the modify
    even starts. On a drag that is the difference between a line that follows
    and one that lags a second behind the mouse. Everything it needs is already
    in what the notifications told us, so the fetch is pure waste.
    """
    return SimpleNamespace(
        basket_id=rec["basket_id"],
        account_id=rec.get("account_id") or account_id,
        symbol=rec["symbol"],
        exchange=exchange,
        # Some notifications carry the order's size in `quantity`, some leave it
        # at 0 and only fill in the unfilled remainder (observed on MNQU6 working
        # orders, 2026-08-11). A modify sent with quantity 0 is a modify to
        # nothing, so the remainder is the fallback — which for a resting order
        # is its whole size anyway.
        quantity=rec.get("qty") or rec.get("unfilled") or 0,
        # The raw enum int, not our word for it: `modify_order` passes it
        # straight back as `price_type`.
        price_type=rec.get("price_type_raw", 0),
        price=rec.get("price") or 0.0,
    )


def _reprice(rec: dict, want: float) -> dict:
    """Which price field actually moves this kind of order, and to what.

    **The field is not interchangeable, and sending the wrong one fails
    silently.** ``modify_order`` runs its kwargs through
    ``_validate_price_fields``, which keeps only the fields that apply to the
    order's type and *drops the rest without a word*: a stop-market sent
    ``price`` has its price field set and its trigger left alone, so the order
    does not move and Rithmic reports no error because nothing invalid was
    asked. Measured on MNQU6 2026-08-11 — a resting stop entry was dragged three
    times, the modifies were accepted, and its ``trigger_price`` was still at the
    original 29745.75 when it was cancelled forty seconds later. The chart's line
    snapped back each time, which reads exactly like a rendering bug.

    A stop-limit carries both, and the gap between them is the slippage the order
    was written to tolerate. It is *preserved* rather than collapsed: the trigger
    goes where the drag landed and the limit follows at the same distance, so
    dragging a stop-limit cannot quietly turn it into something that fills
    further away than it was allowed to.
    """
    kind = _i(rec.get("price_type_raw", 0))
    if kind in (3, 6):                      # stop-limit, limit-if-touched
        trigger = _f(rec.get("trigger_price")) or want
        limit = _f(rec.get("price")) or want
        return {"trigger_price": want, "price": want + (limit - trigger)}
    if kind in (4, 5):                      # stop-market, market-if-touched
        return {"trigger_price": want}
    return {"price": want}


class OrderJournal:
    """Append-only record of everything this module did and was told.

    Separate from ``SignalJournal`` and deliberately unconditional: the signal
    journal follows the recording because a signal with no tape behind it has
    nothing to be checked against, and an order has no such dependency — it
    happened at a broker whether or not this process wrote the tape down. If the
    day is ever in dispute, this file is the only thing on this side of the wire
    that says what was asked for and when.
    """

    def __init__(self, symbol: str, day: date) -> None:
        self.dir = LIVE_ORDER_DIR / symbol / day.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "orders.jsonl"
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        # `event`, not `kind` — "kind" is the account's demo/live label and is a
        # field callers pass, so the two would collide on every submit.
        line = json.dumps({"at": time.time(), "event": event, **fields},
                          default=str)
        try:
            with self._lock, self.path.open("a") as fh:
                fh.write(line + "\n")
        except OSError:
            # A journal that cannot be written must never stop an order being
            # cancelled or a position being flattened. Losing the record of a
            # kill switch is bad; not being able to pull it is worse.
            pass


class Broker:
    """Live order routing for one contract, on the feed's Rithmic client."""

    def __init__(self, symbol: str, exchange: str, day: date,
                 policy: Policy, tick_size: float = 0.25,
                 point_value: float = 20.0, system: str = "") -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.day = day
        self.policy = policy
        self.tick_size = tick_size
        #: What the order path may be pointed at, the feed's contract first.
        #: The tape is one socket and one subscription, so this is *not* a list
        #: of things you can watch — it is a list of things you can send to
        #: while watching the one the feed is on. See ``use_instrument``.
        self.instruments: list[str] = [symbol]
        # For the P&L on a reconstructed round trip. The broker reports its own
        # day P&L too; this is what a *trade* made, which it does not break out.
        self.point_value = point_value
        # Which Rithmic system these credentials logged into. Only used to
        # qualify the account tags: an account id is unique within a login, so a
        # tag that followed the id alone could label somebody else's account.
        self.system = system
        self.journal = OrderJournal(symbol, day)
        self.confirms = Confirms()

        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        # The active account, and it starts on paper — every session, including
        # one that comes back from a restart or crosses the 18:00 roll. There is
        # always an account selected and by default it cannot trade, which is
        # what makes "did I leave this pointed at something real" answerable.
        self.account_id: str = PAPER
        self.accounts: list[str] = []
        # None until `reconcile` has answered. The surface must not draw a
        # position before then and nothing may be sent: "no orders yet" and "we
        # have not asked" are different claims, and only one of them is safe to
        # act on.
        self.reconciled_at: float | None = None
        self.error: str | None = None

        #: basket_id -> the broker's latest word on a working order.
        self.working: dict[str, dict] = {}
        self.recent: deque[dict] = deque(maxlen=RECENT_MAX)
        #: What the PnL plant says we are holding. Signed: + long, - short.
        self.position: dict | None = None
        self._counter = 0
        #: user_tag -> the bracket that was sent with that order, in ticks.
        #: Rithmic attaches the legs when the entry fills and says nothing about
        #: them before that, so this is the only record of what a *working*
        #: order is going to become — which is what lets the chart sketch the
        #: stop and the target in behind a resting entry instead of showing a
        #: bare line for an order that is anything but bare. Cleared with the
        #: order (see `_forget_bracket`); bounded by the same lifetime.
        self._sent_bracket: dict[str, dict[str, int]] = {}
        #: One lock per basket, so two drags on the same order cannot interleave
        #: — Rithmic answers 'Atomic order operation in progress' to that.
        self._locks: dict[str, threading.Lock] = {}
        #: Is the bracket on the position Rithmic's to move? Set when an order
        #: goes out carrying a trail or a breakeven, cleared when the position it
        #: opened goes flat. A drag against a leg the server is managing is
        #: refused, and this is the half of that answer the legs themselves do
        #: not carry — see `_server_managed`.
        self._managed_bracket = False
        #: basket_id -> the price the last drag asked for, so the notification
        #: that comes back can be checked against it. Verification only: nothing
        #: is retried or corrected from this, it is written down.
        self._asked: dict[str, tuple[str, float]] = {}
        #: Round trips paired out of the fill stream, for the chart's trade
        #: marks. The broker reports executions; a *trade* is a thing this has
        #: to construct — see `_on_fill`.
        self.trades: list[dict] = []
        #: The netting state those are folded from: net, average, when it
        #: opened, and the stop it opened with.
        self._netted: dict | None = None
        #: Trades that closed but could not be written to the journal. Surfaced
        #: on the panel: a silent count is the same as no count, and this is the
        #: one failure where the trade happened but the record of it did not.
        self.stats_booking_errors = 0

        #: --- the day, as the guardrails see it ---------------------------
        #: Per account, and that is the load-bearing part rather than tidiness:
        #: a day's running total belongs to the account that made it, so
        #: switching account must neither carry a stop onto a fresh balance nor
        #: — much worse — be the way to unlock a day that is over. Switching
        #: away and back restores the lock, because it was never dropped.
        #:
        #: Each record: realised dollars net of commission, the trade count, why
        #: the day is locked (latched — a winner that brings the total back does
        #: not reopen it), and `time.monotonic()` of the last *entry* submitted.
        #: Monotonic rather than wall clock, so an NTP step cannot grant an entry
        #: the slow-down rule was holding back.
        #: Each record also carries `restored`: how many of its trades came back
        #: off the journal rather than out of a fill this process saw.
        self._days: dict[str, dict] = {}
        #: Guards the check-and-rebuild in `_day`, and nothing else. Its own lock
        #: rather than `self._lock`: the rebuild reads the database, and holding
        #: the lock the fill path takes while sqlite answers would put a query
        #: between a fill and the netting that has to see it.
        self._day_lock = threading.Lock()
        #: Has the automatic flatten already fired today? Latches for the rest
        #: of the session day so a stream of PnL updates cannot queue several,
        #: and so a position opened after the stop cannot trigger a second one.
        self._flattening = False

    # --- lifecycle (feed thread / event loop) -------------------------------

    async def attach(self, client) -> None:
        """Take over a connected client's order and PnL plants.

        Raises rather than degrading: a routing session that came up without
        knowing its account, or without having asked what is working, is exactly
        the state that must not be reachable. The feed catches it and reports it
        on ``/live/status``; the tape keeps running either way, because losing
        market data over an order-plant problem would be the wrong trade.
        """
        client.on_exchange_order_notification += self._on_exchange
        client.on_rithmic_order_notification += self._on_rithmic
        client.on_instrument_pnl_update += self._on_pnl
        with self._lock:
            self._client = client
            self._loop = asyncio.get_running_loop()
            self.error = None
        try:
            self.accounts = [a.account_id for a in (client.accounts or [])]
            if not self.accounts:
                raise LookupError("the order plant reported no accounts")
            # Deliberately NOT selecting one, however few there are. The session
            # opens on paper and choosing a real account is an act — which is
            # the difference between "I am practising" and "I meant that".
            self.account_id = PAPER
            self._patch_order_plant(client)
            await self._find_siblings(client)
            self._restore_instrument()
            await client.subscribe_to_pnl_updates()
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.journal.write("attach_failed", error=self.error)
            raise

    def _patch_order_plant(self, client) -> None:
        """Teach the order plant to send a *trailing* bracket.

        async_rithmic cannot. ``submit_order`` hardcodes
        ``BracketType.*_STATIC`` and never sets any of the four trailing fields
        the protobuf carries (plants/order.py:244,249), so a trail has to be
        folded into template 330 on its way past. ``_build_request`` copies
        arbitrary kwargs onto the message, which is what makes that possible
        without a fork — and ``_set_pb_field`` indexes ``fields_by_name``, so a
        misspelled field raises here rather than travelling to Chicago.

        **Keyed by the order's own tag, which is why this is safe.** The obvious
        shape — stash the extras on ``self``, patch, submit, unpatch — has a
        window where a second order picks up the first one's trail. Every submit
        already carries a unique ``user_tag`` (``aj-<epoch>-<n>``) and
        ``submit_order`` passes it straight through, so the wrapper looks up
        *this* order's extras and no order can be handed another's. Installed
        once per connection rather than per call, so there is no patched state
        to leak if a submit raises.

        Registered against the plant object, not the client, because that is
        where ``_send_and_collect`` lives.
        """
        plant = client.plants["order"]
        if getattr(plant, "_aj_patched", False):
            return
        original = plant._send_and_collect
        pending = self._trailing = {}

        async def _with_trailing(template_id, **kwargs):
            if template_id == BRACKET_TEMPLATE:
                extra = pending.pop(_s(kwargs.get("user_tag", "")), None)
                if extra:
                    kwargs["bracket_type"] = _dynamic_bracket(
                        kwargs.get("bracket_type"))
                    kwargs.update(extra)
            return await original(template_id, **kwargs)

        plant._send_and_collect = _with_trailing
        plant._aj_patched = True

    async def _find_siblings(self, client) -> None:
        """Which other contracts this session may route to.

        The micro of the mini being watched, and nothing else — this is not a
        symbol browser. NQ↔MNQ and ES↔MES track the same index, so an order
        placed on one against a chart of the other is off by microstructure
        rather than by anything a plan would notice; a contract that did not
        track would make the chart a lie and is deliberately not offered.

        Resolved through Rithmic rather than assembled from the root and the
        month code. The two agree almost always, and the exception — a
        front-month roll landing on different days for the mini and the micro —
        is exactly when a hand-built symbol would be silently wrong and reach
        the exchange as a contract nobody meant to trade.

        Failure here is not fatal. The list falls back to the feed's own
        contract, which is what every session before this one could do.

        Nothing is journalled either way, deliberately. The journal is the
        record of what was *done* at a broker, and this is a capability read at
        connect — the account list is not written there for the same reason. A
        lookup that failed shows up as a one-entry ``instruments`` in the
        snapshot, which the panel draws, so it is visible without being audited.
        """
        from ..config import root_symbol

        siblings = {"NQ": "MNQ", "ES": "MES"}
        micro = siblings.get(root_symbol(self.symbol))
        if not micro:
            return
        try:
            contract = await client.get_front_month_contract(micro, self.exchange)
        except Exception:  # noqa: BLE001 — a missing sibling is not an outage
            return
        if contract and contract not in self.instruments:
            self.instruments.append(str(contract))

    def _restore_instrument(self) -> None:
        """Point routing back at the contract this login last chose.

        A session that opened on the mini every time because the *feed* is on
        the mini is a session that quietly re-sized somebody's plan by ten,
        every morning, unless they remembered to change it back. The choice is
        the trader's and it should outlive the socket that carried it.

        Three things keep this from being a way to trade something unintended.
        It is matched by **root** against the contracts this session actually
        found, so a stale or unentitled preference simply does not apply. It
        runs here, at attach — the account is ``paper``, nothing is working and
        nothing is held, which is the state ``use_instrument`` demands anyway.
        And it changes only *where* an order would go, never whether one can:
        the account is still paper, still untagged until somebody says, and
        every gate in ``check_routable`` is still in front of the first order.

        Never fatal. A preference that cannot be read leaves the session on the
        feed's own contract, which is where every session started before this.
        """
        from . import routing as _rt

        try:
            want = _rt.instrument_of(self.system)
            if not want or want == _rt.instrument_root(self.symbol):
                return
            match = next(
                (s for s in self.instruments
                 if _rt.instrument_root(s) == want), None)
            if match:
                self.use_instrument(match)
        except Exception as e:  # noqa: BLE001 — a preference is not an outage
            self.journal.write("instrument_restore_failed",
                               error=f"{type(e).__name__}: {e}")

    def detach(self, reason: str = "feed disconnected") -> None:
        """Drop the connection and everything that could be acted on with it.

        Called from the feed's ``finally``, so it runs on a clean stop, a
        reconnect and a crash alike. The client goes, ``reconciled_at`` goes
        back to None — so a reconnected session refuses to send until it has
        re-asked — and any order staged behind a review goes with them. The
        working orders and the position stay, marked stale by ``reconciled_at``,
        because they are the last thing the broker actually said and blanking
        them would read as "flat" at precisely the wrong moment.
        """
        with self._lock:
            self._client = None
            self._loop = None
            self.reconciled_at = None
        self.confirms.clear()
        self.journal.write("detach", reason=reason)

    @property
    def attached(self) -> bool:
        return self._client is not None

    @property
    def ready(self) -> bool:
        """Connected to a real account whose state has been asked for.

        False on paper, and that is not a defect: paper has no broker state to
        be ready about. The page reads ``paper`` first and only asks this of a
        real account.
        """
        return (self.attached and not self.paper
                and self.reconciled_at is not None)

    @property
    def paper(self) -> bool:
        """Is the active account the one that cannot trade?"""
        return self.account_id == PAPER

    @property
    def tag(self):
        """The active account's declared kind, or None while it is untagged.

        Read live rather than cached: a tag set in one tab must take effect in
        the other without a reconnect, and this is not on a hot path.
        """
        if self.paper:
            return None
        return tag_of(self.system, self.account_id)

    def use_account(self, account_id: str) -> dict:
        """Point routing at a different account, mid-session.

        **Everything staged behind a review goes.** The sentence names the
        account, so a token minted before the switch describes an order nobody
        asked for any more.

        The broker's state is dropped and re-asked rather than filtered, for the
        same reason ``reconcile`` exists at all: working orders and the position
        belong to an account, and showing the previous account's while the new
        one's are still in flight is the "not asked" / "nothing there" confusion
        this module keeps refusing to make. ``reconciled_at`` goes back to None
        for the moment in between, so orders are refused until the answer lands.

        Switching **to** paper needs no round trip: there is nothing at a broker
        to ask about, and the page's own blotter is waiting where it was left.

        An untagged account may be selected — you have to be able to look at one
        before you can label it — but it cannot send. See ``check_routable``.
        """
        account_id = (account_id or "").strip()
        if account_id != PAPER and account_id not in self.accounts:
            raise LookupError(
                f"{account_id!r} is not one of this login's accounts "
                f"({', '.join(self.accounts)})")
        if account_id == self.account_id:
            return self.snapshot()
        self.confirms.clear()
        with self._lock:
            self.account_id = account_id
            self.working = {}
            self.position = None
            self.recent.clear()
            # The paired trades and the netting behind them belong to the
            # account that made them. Carried across, they would draw another
            # account's day on this one's chart.
            self.trades = []
            self._netted = None
            self.reconciled_at = None
            # Both describe orders on the account being left. A managed-bracket
            # flag carried across would refuse drags on an account that never
            # sent a trail, and an outstanding expectation would be answered by
            # a notification about somebody else's order.
            self._managed_bracket = False
            self._asked.clear()
        self.journal.write("account", account=account_id)
        # Whose day this account has had, off the journal — here rather than on
        # the first fill, so the query happens on this thread while somebody is
        # waiting for a round trip anyway, and never between a fill and the
        # netting that has to see it. `_day` is idempotent: an account selected
        # twice is read back once.
        self._day()
        # And the trades behind that total, which are this account's own — the
        # blotter above was cleared for the right reason and refilled from the
        # right place. Paper is excluded because its blotter is the page's own
        # fold over the tape and the page still has it.
        if account_id != PAPER:
            self._restore_trades()
        if account_id == PAPER:
            return self.snapshot()
        return self.refresh()

    def _restore_trades(self) -> None:
        """Repaint the blotter from the journal: this account, this contract.

        The chart's trade marks and the running total behind the page's title
        both read ``self.trades``, and both used to come back empty from a
        restart or a switch — while the trades themselves sat in the journal,
        booked and correct. Scoped to one contract because that is what the
        blotter is: the same account's NQ and MNQ round trips are one day's
        money but two charts' marks.

        Display only, and deliberately so — it seeds nothing the fill path
        reads. ``_netted`` stays where ``reconcile`` puts it, because what is
        *held* is the broker's answer and never the journal's.

        Never raises, for the reason every journal touch in this file does not.
        """
        try:
            from .. import db
            from . import booking

            conn = db.connect()
            try:
                rows = booking.day_trades(conn, account=self.account_id,
                                          session_date=self.day,
                                          symbol=self.symbol)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — a blotter is not an outage
            self.journal.write("trades_restore_failed",
                               error=f"{type(e).__name__}: {e}",
                               account=self.account_id)
            return
        with self._lock:
            self.trades = rows

    def use_instrument(self, symbol: str) -> dict:
        """Point the order path at a different contract, mid-session.

        This is what lets a plan written for micros be sent as micros while the
        chart stays on the mini it was drawn from — the hazard named in
        ``routing.Guards.max_risk_usd``, closed by making the symbol a choice
        instead of an inheritance from whatever the feed happens to be on.

        **The tape does not follow, and cannot.** One Rithmic login is one
        socket and the subscription was made at connect, so this switches where
        orders *go*, not what you are watching. The two contracts track each
        other to within a tick, which is what makes that honest rather than
        merely convenient — but a limit price dragged off an NQ chart is an NQ
        price, and it is sent to MNQ as typed.

        **Refused unless the book is flat, and this is the one place that rule is
        stricter than the account switch.** Symbol-scoped state is not a display
        filter here: ``_on_pnl`` drops messages for other symbols, and — the part
        that matters — ``_auto_flatten`` exits ``self.symbol``. Switching with an
        MNQ position open would leave the daily-loss stop pointed at NQ, so the
        one automatic protection in this file would flatten the wrong contract
        while the wrong one bled. An account switch has no such hole: it re-asks
        the broker and each account's position comes back with it.

        Drops anything staged, for the reason every switch here does: the
        sentence somebody reviewed named a contract, and the same 50 ticks is
        $250 of NQ or $25 of MNQ. A review is of an amount, not of a number of
        ticks.
        """
        from ..config import contract_spec

        symbol = (symbol or "").strip().upper()
        if symbol not in self.instruments:
            raise LookupError(
                f"{symbol!r} is not routable on this session "
                f"({', '.join(self.instruments)})")
        if symbol == self.symbol:
            return self.snapshot()
        if self.working:
            raise PermissionError(
                f"{len(self.working)} order(s) still working on {self.symbol}. "
                "Cancel them first — a working order belongs to the contract it "
                "was placed on, and this switch would take its bracket out of "
                "reach of the daily-loss flatten.")
        if _i((self.position or {}).get("net") or 0):
            raise PermissionError(
                f"there is an open position on {self.symbol}. Flatten first — "
                "the automatic stop-out exits whichever contract routing is "
                "pointed at, and switching now would aim it at the other one.")

        self.confirms.clear()
        spec = contract_spec(symbol)
        with self._lock:
            self.symbol = symbol
            self.tick_size = float(spec["tick_size"])
            self.point_value = float(spec["point_value"])
            # The paired trades are this contract's, in this contract's dollars.
            # Left on screen they would draw MNQ's round trips against NQ's
            # marks — the same argument `use_account` makes about balances.
            self.trades = []
            self._netted = None
            self.recent.clear()
            self.reconciled_at = None
        # Per symbol, like the tape it annotates. `roll_day` reopens it the same
        # way when the session crosses 18:00.
        self.journal = OrderJournal(symbol, self.day)
        self.journal.write("instrument", symbol=symbol,
                           tick_size=self.tick_size,
                           point_value=self.point_value)
        # Remembered for the next session, as a root rather than this quarter's
        # contract — see `routing.set_instrument`. A preference and nothing more:
        # it cannot make anything routable, and `attach` applies it only to a
        # contract the login actually turned out to have.
        from . import routing as _rt

        _rt.set_instrument(self.system, symbol)
        # NOT the day record. `_days` is keyed by account, and a daily loss stop
        # that reset when you moved to micros would be a way to unlock a day that
        # is over — the same accident `use_account` refuses to allow.
        #
        # Paper needs no round trip and would be refused one: it has no broker
        # state to re-read. Setting the target while on paper is allowed anyway,
        # so the contract can be chosen before the account is — but paper's own
        # blotter keeps filling off the tape, at the tape's contract, and the
        # panel says so rather than letting the label imply otherwise.
        if self.paper:
            return self.snapshot()
        # The blotter *is* per contract, so it comes back for the new one —
        # cleared above for the right reason, refilled from the journal, which
        # is where this account's round trips on this contract already are.
        self._restore_trades()
        return self.refresh()

    def use_settings(self, settings) -> dict:
        """Adopt newly saved order-entry settings, mid-session.

        **The tunable half only.** ``enabled`` and ``guardrails`` are properties
        of the deployment, settled when the order plant was opened, and
        re-reading them here would let the rule layer switch off under a running
        position — the same reason ``routing`` is not one of ``set_modes``'
        switches. The levels are the opposite kind of thing: a decision about
        what the rules *are*, which is a fine thing to tune from the chart.

        Without this the panel's level editor is a write-only control.
        ``save_settings`` puts the numbers in the store, this process goes on
        enforcing the snapshot it took at connect, and the panel — which draws
        ``policy.guards`` — reads the old ones straight back, so a save that
        worked is indistinguishable from one that did nothing. "Takes effect on
        the next order" has to be true of the order path *and* of what the panel
        says the order path will do.

        The day record is untouched. A level is not a fill, and a stop widened
        at 14:00 does not change what the morning cost — nor may it reopen a day
        that has locked, which stays latched on the reason it latched for.
        """
        with self._lock:
            self.policy = replace(self.policy, max_qty=settings.max_qty,
                                  guards=settings.guards)
        g = settings.guards
        self.journal.write("settings", max_qty=settings.max_qty,
                           **{f: getattr(g, f)
                              for f in type(g).__dataclass_fields__})
        return self.snapshot()

    # --- reconciliation -----------------------------------------------------

    async def reconcile(self) -> dict:
        """Ask the broker what is working and what is held. Never assume flat.

        The one thing a restarted process must do before it draws anything. A
        surface that came back showing no orders and no position because it had
        not asked is indistinguishable from one showing the truth, and the
        difference is a live position nobody is watching.
        """
        client = self._client
        if client is None:
            raise LookupError("not connected")
        if self.paper:
            raise LookupError(
                "the paper account has no broker state — its blotter is the "
                "page's own fold over the tape")
        orders = await client.list_orders(account_id=self.account_id)
        positions = await client.list_positions(account_id=self.account_id)
        found: dict[str, dict] = {}
        for o in orders or []:
            rec = self._order_rec(o)
            if rec["working"]:
                found[rec["basket_id"]] = rec
            else:
                self.recent.appendleft(rec)
        with self._lock:
            self.working = found
            self.reconciled_at = time.time()
        for p in positions or []:
            self._apply_pnl(p)
        self.journal.write("reconcile", orders=len(found),
                           position=self.position, account=self.account_id)
        return self.snapshot()

    # --- inbound ------------------------------------------------------------

    def _on_fill(self, m, rec: dict) -> None:
        """Pair fills into round trips, so a real account's trades can be drawn.

        The chart's trade marks want an entry *and* an exit — a shape the broker
        never sends, because a fill stream reports executions, not trades. So a
        netted position is kept here and a trade is emitted whenever size comes
        off it, which is the same rule ``replaySim`` folds with. Keeping the two
        in step is the point: a paper trade and a real one on the same chart
        should mean the same thing, or the comparison the whole live stack exists
        for is between two different definitions.

        The netting rules, and each one is a case ``replaySim`` also handles:

          - **same side as the position** — an add. The entry becomes the
            volume-weighted average, and no trade is emitted.
          - **opposite side, smaller or equal** — a scale-out or a close. One
            trade for the size that came off, at the position's average.
          - **opposite side, larger** — a flip. The whole position closes as one
            trade and the remainder opens a new one the other way.

        R is measured against the stop the position was opened with, and is null
        when it carried none — the same nullable field the paper trades use, and
        the same reason: there was no risk to divide by.
        """
        px = _f(getattr(m, "fill_price", None))
        qty = _i(getattr(m, "fill_size", 0))
        if px is None or qty <= 0:
            return
        ss = _i(getattr(m, "ssboe", 0))
        ms = ss * 1000 if ss else int(time.time() * 1000)
        long = rec["side"] == "buy"
        # The fill itself, for the trade-detail chart's markers. Separate from
        # the round-trip pairing below and deliberately best-effort: a missing
        # execution costs a marker, never a number.
        self._book_fill({"fill_id": _s(getattr(m, "fill_id", "")),
                         "price": px, "size": qty, "side": rec["side"],
                         "ms": ms})
        signed = qty if long else -qty
        st = self._netted

        if st is None or st["net"] == 0:
            self._netted = {"net": signed, "avg": px, "opened_ms": ms,
                            "stop": self._stop_for(rec)}
            return
        if (st["net"] > 0) == long:
            # An add: the average moves, nothing closes.
            total = abs(st["net"]) + qty
            st["avg"] = (st["avg"] * abs(st["net"]) + px * qty) / total
            st["net"] += signed
            return

        closing = min(qty, abs(st["net"]))
        self._emit_trade(st, closing, px, ms, rec)
        left = qty - closing
        st["net"] += signed
        if left > 0:
            # A flip: what is left opens a fresh position the other way, and its
            # entry is this fill's price rather than the old average.
            self._netted = {"net": (left if long else -left), "avg": px,
                            "opened_ms": ms, "stop": self._stop_for(rec)}
        elif st["net"] == 0:
            self._netted = None
        # Last, once the netting has settled: the latch needs to know whether
        # anything is still held before it decides the day is over.
        self._latch_day()

    def _stop_for(self, rec: dict) -> float | None:
        """The stop the position opened with, for the R denominator.

        Read off the working bracket rather than the entry order: Rithmic
        attaches the legs as separate orders when the entry fills, so by the
        time this is asked the stop is a working order on the closing side.
        None when there is not one, which makes R null rather than infinite.
        """
        long = rec["side"] == "buy"
        with self._lock:
            for o in self.working.values():
                if o["basket_id"] == rec["basket_id"]:
                    continue
                if (o["side"] == "buy") == long:
                    continue
                if o["type"] in ("stop", "stop_limit"):
                    return o.get("trigger_price") or o.get("price")
        return None

    def _emit_trade(self, st: dict, size: int, exit_px: float, ms: int,
                    rec: dict) -> None:
        long = st["net"] > 0
        entry = st["avg"]
        pts = (exit_px - entry) * (1 if long else -1)
        risk = None if not st.get("stop") else abs(entry - st["stop"])
        trade = {
            "id": len(self.trades) + 1,
            "side": "long" if long else "short",
            "size": size,
            "entry_price": entry,
            "entry_ms": st["opened_ms"],
            "exit_price": exit_px,
            "exit_ms": ms,
            "pts": pts,
            "pnl": pts * self.point_value * size,
            # Stake R, the same one the paper blotter prints: points made over
            # points risked at open. Null without a stop, never zero.
            "r": None if not risk else pts / risk,
            # Which leg closed it. `reduce` is what the paper simulation calls
            # size taken off by an order on the other side, and that is exactly
            # what a partial close is here.
            "reason": self._exit_reason(rec, size, abs(st["net"])),
        }
        self.trades.append(trade)
        self.journal.write("trade", **trade)
        self._count_day(trade)
        self._book(trade)

    def _book_fill(self, fill: dict) -> None:
        """Record one fill. Same never-raise contract as ``_book``."""
        from .. import db
        from . import booking

        try:
            conn = db.connect()
            try:
                booking.book_fill(conn, account=self.account_id,
                                  instrument=f"{self.symbol}@{self.exchange}",
                                  session_date=self.day, fill=fill)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — markers are not worth a stall
            self.stats_booking_errors += 1
            print(f"[live-broker] could not record the fill: "
                  f"{type(e).__name__}: {e}", flush=True)

    def _book(self, trade: dict) -> None:
        """Put a closed round trip in the journal, where the app can see it.

        **Never allowed to raise into the fill path.** This runs inside the
        notification handler, on the feed's event loop — the same thread that is
        draining the tape and would be handling a cancel or a flatten. A journal
        that cannot be written is a bad afternoon; a journal write that stops the
        broker responding is a position you cannot close. So a failure is
        counted and printed, and nothing more: the trade is already in
        ``self.trades`` for the panel and already a line in ``orders.jsonl``,
        which is the durable record a backfill could re-read.

        Its own connection rather than the API's. ``journal.live.routing`` reads
        the settings store the same way, and it is what keeps this module free of
        an ``api.deps`` import. Safe because the DB is in WAL with a 5s busy
        timeout, and ``api.scope``'s read caches key on ``PRAGMA data_version``,
        which flips when another connection commits — so the Trades page notices
        without being told.
        """
        from .. import db
        from . import booking

        try:
            conn = db.connect()
            try:
                booking.book_trade(
                    conn, account=self.account_id,
                    instrument=f"{self.symbol}@{self.exchange}",
                    mode="live", session_date=self.day, trade=trade)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — see the docstring
            self.stats_booking_errors += 1
            self.journal.write("book_failed", error=f"{type(e).__name__}: {e}",
                               **trade)
            print(f"[live-broker] could not journal the trade: "
                  f"{type(e).__name__}: {e}", flush=True)

    # --- the discipline layer -----------------------------------------------
    #
    # The rules themselves are in `journal.live.routing` and know nothing about
    # a market. What lives here is the only part that cannot: the day's running
    # total, which has to be folded out of the fill stream.

    def _day(self) -> dict:
        """This account's day record, rebuilt from the journal on first use.

        **A restart is not a new day.** ``roll_day`` says the roll is the only
        thing that clears a daily stop — "not a restart, not an account switch,
        not a recovery back above the line" — and until this read-back existed
        that sentence was true of every path but the one people actually hit.
        The record lives in memory; the process does not outlive an afternoon of
        `--reload`, and what came back was a day that thought it was flat while
        the account was down several hundred dollars, with the whole loss stop
        available again.

        So the record is not created empty, it is *derived* — from the rows this
        broker already books (``_book``), read back through ``booking``. On
        first use of an account, which is normally ``use_account``'s doing, on
        the API thread. The lazy path here is the safety net for every other
        caller, and it is why this is not simply done at construction: an
        account nobody selects should cost no query.
        """
        rec = self._days.get(self.account_id)
        if rec is not None:
            return rec
        with self._day_lock:
            # Re-checked under the lock: two threads can reach a cold record —
            # `guard_view` on the API thread and `_count_day` on the feed's —
            # and the loser must not overwrite a rebuild the winner has already
            # started counting fills into.
            rec = self._days.get(self.account_id)
            if rec is None:
                rec = self._days[self.account_id] = self._rebuild_day()
        return rec

    def _rebuild_day(self) -> dict:
        """The active account's day, folded back out of the journal.

        Same arithmetic as ``_count_day`` and ``_latch_day``, applied to rows
        instead of fills — commission per the contract each trade was actually
        taken on (a day that mixed NQ and MNQ charges each at its own rate), and
        the lock walked forward in close order so a day that hit its stop before
        the restart comes back *locked* rather than merely negative.

        **``last_entry_at`` stays None, and that is not laziness.** It is a
        ``time.monotonic()`` reading, which means nothing across processes;
        restoring it from a wall clock would be inventing a number. The cost is
        that the slow-down rule's gap starts over after a restart — the one part
        of the day this cannot bring back, and the harmless direction to lose,
        since it delays nothing and refuses nothing that the stop does not.

        Never raises. A day that cannot be read back is reported and left empty,
        because a broker that will not come up because the journal was busy is a
        worse failure than a guard that has to be told the number.
        """
        rec = {"realized": 0.0, "trades": 0, "locked": None,
               "last_entry_at": None, "restored": 0}
        try:
            from .. import db
            from . import booking

            conn = db.connect()
            try:
                rows = booking.day_trades(conn, account=self.account_id,
                                          session_date=self.day)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — see the docstring
            self.journal.write("day_restore_failed",
                               error=f"{type(e).__name__}: {e}",
                               account=self.account_id)
            print(f"[live-broker] could not rebuild the day: "
                  f"{type(e).__name__}: {e}", flush=True)
            return rec

        for t in rows:
            rec["realized"] += (t["pnl"] - 2.0
                                * self.commission_per_side(t["symbol"])
                                * t["size"])
            rec["trades"] += 1
            rec["restored"] += 1
            if rec["locked"] is None:
                rec["locked"] = self._locked_by(rec["realized"])
        if rec["restored"]:
            self.journal.write("day_restored", account=self.account_id,
                               trades=rec["restored"],
                               realized=round(rec["realized"], 2),
                               locked=rec["locked"])
        return rec

    def reset_day(self) -> None:
        """Start the day over, for every account. Called at the 18:00 roll.

        Dropping the records is enough to *clear* them: the rebuild is keyed on
        ``self.day``, which ``roll_day`` has already advanced, so the next read
        derives the new day's total — which is nothing, until it isn't.
        """
        self._days = {}
        self._flattening = False

    @property
    def day_realized(self) -> float:
        return float(self._day()["realized"])

    @property
    def day_locked(self) -> str | None:
        return self._day()["locked"]

    def _count_day(self, trade: dict) -> None:
        """Fold one closed round trip into the day's running total.

        **Local, not the broker's ``day_pnl``, and that is deliberate.** The PnL
        plant's number is what the *account* did — it includes anything traded
        on another platform against the same login, and it arrives as a state
        whose timing this process does not control. The guard has to latch on
        the exact trade that crossed the line, so it counts what this process
        paired. The broker's figure is carried in the snapshot beside it and the
        difference is reported rather than reconciled: a divergence means one of
        the two is missing trades, and quietly picking a winner would hide that.

        Net of commission, because a $500 stop that ignores $35 of round turns
        is not a $500 stop.
        """
        rec = self._day()
        rec["realized"] += (trade["pnl"]
                            - 2.0 * self.commission_per_side() * trade["size"])
        rec["trades"] += 1

    def commission_per_side(self, symbol: str | None = None) -> float:
        """The configured rate, scaled to whatever routing is pointed at.

        The stored setting is one number and was measured on minis — $3.50,
        against 479 real fills. A micro is about a seventh of that, and since
        the day's realised total is what ``daily_loss_stop`` latches on, charging
        MNQ round turns at NQ rates would walk the day toward its stop roughly
        $6 per trade faster than reality.

        Scaled by contract size rather than stored per symbol: the ratio is a
        fact about the contracts (MNQ is a tenth of NQ, ES a tenth of MES) while
        the rate is a fact about the broker, and only one of those is the user's
        to keep up to date. Clamped below at the measured micro rate — brokers
        do not discount a micro to a tenth of a mini, and a commission estimated
        too low is the direction that lets a day run past its stop.

        ``symbol`` asks about a contract other than the live one, which the
        rebuild needs and nothing else does: a day that ran NQ in the morning
        and MNQ after lunch is one running total made of two rates, and charging
        the whole of it at whatever routing happens to point at *now* would move
        the restored figure by six dollars a trade in whichever direction the
        last switch went.
        """
        from ..config import contract_spec

        rate = float(self.policy.guards.commission_per_side)
        mini = float(contract_spec("NQ")["point_value"])
        try:
            mine = float(contract_spec(symbol or self.symbol)["point_value"])
        except Exception:  # noqa: BLE001 — an unknown contract is charged as a mini
            mine = mini
        mine = mine or mini
        if mine >= mini:
            return rate
        return max(MICRO_COMMISSION_FLOOR, rate * (mine / mini))

    def _locked_by(self, pnl: float) -> str | None:
        """Why a running total of ``pnl`` ends the day, or None.

        Split out of ``_latch_day`` so the rebuild latches on the same sentence
        the live path does. Two wordings for one rule is how a restored day and
        a live one come to disagree about what happened.
        """
        if not self.policy.guardrails:
            return None
        g = self.policy.guards
        if g.daily_loss_stop and pnl <= -g.daily_loss_stop:
            return (f"the daily stop of ${g.daily_loss_stop:,.0f} was reached "
                    f"(${pnl:,.0f} realised)")
        if g.daily_profit_lock and pnl >= g.daily_profit_lock:
            return (f"the daily profit lock of ${g.daily_profit_lock:,.0f} was "
                    f"reached (${pnl:,.0f} realised)")
        return None

    def _latch_day(self) -> None:
        """Close the day if the last fill took it past a level.

        Runs after the netting has settled, so "am I flat" is answerable — the
        staged orders are only dropped once there is nothing left to manage,
        because a scale-out somebody has already reviewed should not evaporate
        because the day locked while they were reading it. A refusal that traps
        somebody in a trade is worse than no rule at all, and the lock itself
        never refuses a reducing order (``routing.day_refusal``).
        """
        if not self.policy.guardrails:
            return
        rec = self._day()
        if rec["locked"]:
            return
        why = self._locked_by(rec["realized"])
        if why is None:
            return
        pnl = rec["realized"]
        rec["locked"] = why
        self.journal.write("day_locked", reason=why, realized=pnl,
                           trades=rec["trades"], account=self.account_id)
        flat = not self._netted or self._netted.get("net", 0) == 0
        if flat:
            # "Flat, nothing staged, done." Only when there is nothing left to
            # manage — see the docstring.
            self.confirms.clear()

    def _reducing(self, side: str | None, qty: int) -> bool:
        """Does this order take size off the position rather than put it on?

        A flip is not a reduce: an order bigger than what is held closes the
        position *and opens a fresh one*, which is an entry however it is
        framed, and it is the shape somebody reaches for when a rule has just
        refused them.

        Read off the PnL plant's net rather than this process's own netting,
        because the broker is the authority on what is held — the same rule the
        rest of this module follows.
        """
        net = _i((self.position or {}).get("net") or 0)
        if not net:
            return False
        closing = "sell" if net > 0 else "buy"
        return (side or "").strip().lower() == closing and qty <= abs(net)

    def _day_state(self) -> DayState:
        rec = self._day()
        last = rec["last_entry_at"]
        return DayState(
            realized=float(rec["realized"]),
            locked=rec["locked"],
            since_entry_s=None if last is None else time.monotonic() - last,
            trades=int(rec["trades"]),
        )

    def _guard(self, intent: Intent, how: str) -> None:
        """Refuse an order the operating plan says not to place.

        ``PermissionError``, not ``ValueError``: this is a property of the
        moment rather than of the request, which is the distinction the router
        turns into 409 versus 422. The same order is fine tomorrow, or in two
        minutes, and the message says which.

        Called from ``preview``, ``send_now`` **and** ``_submit``. The last one
        is not belt-and-braces: a token minted by ``preview`` is spent later by
        ``send``, so a check that ran only at review time could be walked past
        by staging an order while still allowed and sending it after the day
        locked.
        """
        refusal = day_refusal(self.policy, self._day_state(),
                              reducing=self._reducing(intent.side, intent.qty))
        if refusal is None:
            return
        self.journal.write("guard_refused", how=how, reason=refusal,
                           account=self.account_id, **intent.__dict__)
        raise PermissionError(refusal)

    def _exit_reason(self, rec: dict, size: int, held: int) -> str:
        if size < held:
            return "reduce"
        if rec["type"] in ("stop", "stop_limit"):
            return "stop"
        if rec["type"] == "limit":
            return "target"
        return "manual"

    def _order_rec(self, m) -> dict:
        """One order, as the broker last described it."""
        unfilled = _i(getattr(m, "total_unfilled_size", 0))
        filled = _i(getattr(m, "total_fill_size", 0))
        notify = _i(getattr(m, "notify_type", 0))
        status = _s(getattr(m, "status", ""))
        # 3 CANCEL, 6 REJECT — see ExchangeOrderNotification.NotifyType. A fill
        # is terminal only once nothing is left; a partial fill is still working.
        terminal = notify in (3, 6) or (filled > 0 and unfilled == 0)
        low = status.lower()
        if not terminal and any(w in low for w in ("cancel", "reject", "complete")):
            # Rithmic's own words, when it has bothered to say them. Belt and
            # braces on the numeric read rather than instead of it: an order
            # left in `working` after it is gone is one the panel offers to
            # cancel, and cancelling a filled order is a confusing 404 at best.
            terminal = "pending" not in low
        tag = _s(getattr(m, "user_tag", ""))
        # The bracket this order went out with, if it was ours and it is still
        # ahead of it. Dropped the moment the order is done: once it has filled
        # the legs are real working orders of their own, and a remembered
        # *intention* left lying about would be a second, staler answer to the
        # same question.
        sent = self._sent_bracket.get(tag) or {}
        if terminal:
            self._sent_bracket.pop(tag, None)
        return {
            "basket_id": _s(getattr(m, "basket_id", "")),
            "user_tag": tag,
            "symbol": _s(getattr(m, "symbol", "")),
            # Carried so a row can be checked against the account it is being
            # shown under, rather than trusted because it arrived.
            "account_id": _s(getattr(m, "account_id", "")),
            "side": {1: "buy", 2: "sell"}.get(
                _i(getattr(m, "transaction_type", 0)), "?"),
            "type": {1: "limit", 2: "market", 3: "stop_limit",
                     4: "stop", 5: "mit", 6: "lit"}.get(
                _i(getattr(m, "price_type", 0)), "?"),
            # Kept alongside our word for it because `modify_order` wants the
            # enum back verbatim — see `_as_order`.
            "price_type_raw": _i(getattr(m, "price_type", 0)),
            "qty": _i(getattr(m, "quantity", 0)),
            # `_px`, not `_f`: the field that does not apply to this kind of
            # order comes back as 0.0, and a zero that reaches a chart is a line
            # drawn off the bottom of the world. See `_px`.
            "price": _px(getattr(m, "price", None)),
            "trigger_price": _px(getattr(m, "trigger_price", None)),
            "filled": filled,
            "unfilled": unfilled,
            "avg_fill_price": _px(getattr(m, "avg_fill_price", None)),
            # What this order will become on the fill, in ticks from it — 0 for
            # an order this process did not send, or one that carries no bracket.
            # Not what Rithmic says: it says nothing until the legs exist.
            "stop_ticks": _i(sent.get("stop_ticks", 0)),
            "target_ticks": _i(sent.get("target_ticks", 0)),
            # Non-zero on a leg Rithmic is trailing: the distance it rides behind
            # the extreme, which it re-derives and re-sends itself. Carried
            # because it is the difference between a stop this app may move and
            # one it must not — `_server_managed`, and the chart greys the drag
            # on the same field rather than finding out at the wire.
            "trail_by_ticks": _i(getattr(m, "trail_by_ticks", 0)),
            "status": status,
            "notify": notify,
            "text": _s(getattr(m, "text", "")) or _s(getattr(m, "report_text", "")),
            "working": not terminal,
            "at": time.time(),
        }

    def _mine(self, m) -> bool:
        """Is this message about the contract *and* the account being routed?

        Both halves are load-bearing once the account can be switched, and the
        account half is the one that is easy to leave out. ``subscribe_to_pnl_updates``
        subscribes for **every** account on the login, and the order stream is
        per-login too — so without this, a position held on a second account
        lands in ``self.position`` and the panel reports it as this account's.
        A blank ``account_id`` on a message is treated as ours: some
        notifications omit it, and dropping those would lose real fills.
        """
        acct = _s(getattr(m, "account_id", ""))
        if acct and self.account_id and acct != self.account_id:
            return False
        return _s(getattr(m, "symbol", "")) in ("", self.symbol)

    async def _on_exchange(self, m) -> None:
        """The exchange's word on an order. The authoritative one."""
        if not self._mine(m):
            return
        rec = self._order_rec(m)
        if not rec["basket_id"]:
            return
        # 5 == FILL. Paired into round trips *before* the working set is
        # updated, because `_stop_for` reads the bracket legs and a leg that has
        # just filled must not be mistaken for the stop that was protecting the
        # position it closed.
        if rec["notify"] == 5:
            self._on_fill(m, rec)
        with self._lock:
            if rec["working"]:
                self.working[rec["basket_id"]] = rec
            else:
                self.working.pop(rec["basket_id"], None)
                self.recent.appendleft(rec)
        self.journal.write("exchange", **rec)
        self._settled(rec)

    def _settled(self, rec: dict) -> None:
        """Did the last drag on this order actually move it?

        The question is worth asking because "accepted" and "moved" came apart
        here for months: a stop-market sent the wrong price field was answered
        with a clean modify and did not move a tick (see `_reprice`). Rithmic
        never refused anything, so there was nothing to report and nothing in the
        journal but three cheerful ``modify`` lines against an order sitting
        exactly where it started.

        Read off the notification stream rather than by asking, because the
        answer arrives unprompted and a poll would be both slower and a second
        opinion on something the broker has already volunteered. Nothing is
        retried or corrected from it — a self-healing drag would be a second
        thing moving the order, which is the problem this whole change is about.
        It is written down, which is what was missing.
        """
        want = self._asked.pop(rec["basket_id"], None)
        if want is None:
            return
        leg, price, at = want
        # An expectation nothing ever answered. Left to sit, it would be resolved
        # by an unrelated notification minutes later and reported as a drag that
        # did not land, which is worse than not checking: the one line in here
        # that means "your stop is not where you put it" has to stay rare enough
        # to be believed.
        if time.monotonic() - at > 10.0:
            return
        landed = rec.get("trigger_price") or rec.get("price")
        # Half a tick, so a contract whose prices are eighths cannot fail this on
        # a rounding difference nobody asked about.
        if landed is not None and abs(float(landed) - price) <= self.tick_size / 2:
            return
        self.journal.write("modify_ignored", basket_id=rec["basket_id"],
                           leg=leg, asked=price, landed=landed)

    async def _on_rithmic(self, m) -> None:
        """Rithmic's own word — a reject before the exchange ever sees it.

        Journalled but not merged into ``working``: the two notification streams
        describe the same order and letting both write the same key is how a
        cancelled order comes back to life on an out-of-order pair. The exchange
        stream is the one that decides.
        """
        self.journal.write("rithmic", basket_id=_s(getattr(m, "basket_id", "")),
                           status=_s(getattr(m, "status", "")),
                           text=_s(getattr(m, "text", "")),
                           notify=_i(getattr(m, "notify_type", 0)))

    async def _on_pnl(self, m) -> None:
        self._apply_pnl(m)

    def _apply_pnl(self, m) -> None:
        # Same filter as the order stream, and it matters more here: the PnL
        # plant subscribes for every account on the login, so this is the one
        # place another account's position would arrive unasked.
        if not self._mine(m) or _s(getattr(m, "symbol", "")) != self.symbol:
            return
        net = _i(getattr(m, "net_quantity", 0))
        # When this position opened, so the chart can start drawing it from the
        # right bar. The PnL plant does not carry it — it reports a state, not
        # an event — so it is stamped on the transition off flat, from the
        # message's own send stamp where there is one.
        #
        # APPROXIMATE, AND KNOWINGLY: it is when *this process heard* the
        # position open, not when the fill happened, and a process that attached
        # to an already-open position never sees the transition at all. Both
        # cases are handled by leaving it None and letting the chart fall back
        # to the session start rather than inventing a bar.
        prev = self.position
        was_flat = prev is None or prev.get("net", 0) == 0
        if net == 0 and not was_flat:
            # The position that carried the server-managed bracket is closed, so
            # the next one starts unmanaged until an order says otherwise. On the
            # *transition* rather than on every flat reading: an order placed
            # with a trail while flat has to keep the flag until it fills, and
            # the PnL plant repeats net=0 the whole time it is resting.
            self._managed_bracket = False
        if net == 0:
            opened_ms = None
        elif was_flat:
            ss = _i(getattr(m, "ssboe", 0))
            opened_ms = ss * 1000 if ss else int(time.time() * 1000)
        else:
            opened_ms = (prev or {}).get("opened_ms")
        # `net_quantity` is the field to trust; buy_qty/sell_qty are the day's
        # gross sides and would read as a position long after it was closed.
        self.position = {
            "symbol": self.symbol,
            "net": net,
            "avg_price": _f(getattr(m, "avg_open_fill_price", None)),
            "open_pnl": _f(getattr(m, "open_position_pnl", None)),
            "day_pnl": _f(getattr(m, "day_pnl", None)),
            "opened_ms": opened_ms,
            "at": time.time(),
        }
        # The PnL plant is the only thing that reports an *open* loss, so this
        # is the one place the equity stop can be judged from.
        self._check_equity_stop()

    def _equity(self) -> float:
        """Realised today plus what the open position is currently down.

        The number the account's own drawdown is measured against, and the
        reason the guard cannot run on realised alone: a position held at −$800
        has already spent the drawdown whether or not it has been booked.

        The open leg is the broker's ``open_position_pnl`` rather than anything
        derived from the tape here — it is the figure the firm will act on, and
        a second opinion computed from prints would differ at exactly the moment
        it mattered.
        """
        open_pnl = _f((self.position or {}).get("open_pnl")) or 0.0
        return self.day_realized + open_pnl

    def _check_equity_stop(self) -> None:
        """Close what is open if the day has spent its loss limit. Never raises.

        Runs inside the PnL notification handler, on the feed's event loop, so
        the actual flatten is **scheduled** rather than called: ``flatten()``
        goes through ``_call``, which submits to that same loop and blocks on the
        result, and blocking the loop from inside itself is a deadlock rather
        than a slow request.

        Fires once. ``_flattening`` latches for the rest of the session day and
        is cleared only by ``reset_day`` — a position re-opened after the stop
        (which the day lock refuses anyway) must not trigger a second automatic
        exit, and a stream of PnL updates while the exit is in flight must not
        queue several.
        """
        g = self.policy.guards
        if (not self.policy.guardrails or not g.auto_flatten
                or not g.daily_loss_stop or self._flattening):
            return
        if not _i((self.position or {}).get("net")):
            return  # nothing open: the realised latch in `_latch_day` has it
        eq = self._equity()
        if eq > -g.daily_loss_stop:
            return
        why = (f"the daily stop of ${g.daily_loss_stop:,.0f} was reached on "
               f"equity (${eq:,.0f}: ${self.day_realized:,.0f} booked plus the "
               "open position)")
        self._flattening = True
        rec = self._day()
        if not rec["locked"]:
            rec["locked"] = why
        self.journal.write("auto_flatten", reason=why, equity=eq,
                           realized=self.day_realized, account=self.account_id)
        print(f"[live-broker] AUTO-FLATTEN: {why}", flush=True)
        try:
            asyncio.get_running_loop().create_task(self._auto_flatten(why))
        except RuntimeError:
            # No loop under us — a direct call from a thread rather than from
            # the notification handler. The lock still stands, which is the half
            # that matters; nothing is closed automatically.
            self.journal.write("auto_flatten_skipped", reason="no event loop")

    async def _auto_flatten(self, why: str) -> None:
        """The scheduled half. Awaits the client directly, never ``_call``.

        Same order as ``flatten`` and for the same reason: cancel the working
        orders first, because exiting under a live bracket can leave that
        bracket to open a fresh position the other way when it triggers.

        Failures are journalled and printed, never raised — there is nobody to
        raise to, and a task that dies inside the event loop takes its reason
        with it. A flatten that did not fully land leaves the day locked and the
        panel saying so, which is the state a person needs to act on.
        """
        client = self._client
        if client is None:
            self.journal.write("auto_flatten_failed", error="not connected")
            return
        errors: list[str] = []
        for what, call in (("cancel_all",
                            lambda: client.cancel_all_orders(
                                account_id=self.account_id)),
                           ("exit_position",
                            lambda: client.exit_position(
                                account_id=self.account_id, symbol=self.symbol,
                                exchange=self.exchange))):
            try:
                await call()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{what}: {type(e).__name__}: {e}")
        self.journal.write("auto_flattened", errors=errors, reason=why)
        if errors:
            print(f"[live-broker] auto-flatten did not fully land: "
                  f"{'; '.join(errors)} — check the platform", flush=True)
        # Anything staged goes either way. Whatever happens next should be a
        # decision, not a token minted before the day was over.
        self.confirms.clear()

    # --- outbound (request threads) -----------------------------------------

    def _call(self, coro_factory, what: str):
        """Run a coroutine on the feed's loop and wait for it, from any thread.

        The endpoints are sync handlers, so FastAPI runs them in its threadpool
        and this is a plain cross-thread submit. It is bounded: a wedged event
        loop must surface as a failed order rather than a request that never
        answers, because the operator's next move depends on knowing which.
        """
        with self._lock:
            loop, client = self._loop, self._client
        if loop is None or client is None:
            raise LookupError("the order plant is not connected")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(client), loop)
        try:
            return fut.result(timeout=CALL_TIMEOUT_S)
        except TimeoutError as e:
            fut.cancel()
            self.journal.write("timeout", what=what)
            raise TimeoutError(
                f"{what}: no answer from the order plant in {CALL_TIMEOUT_S:.0f}s. "
                "The order may or may not have reached the exchange — check the "
                "working list before sending anything else.") from e

    # --- may this send at all -------------------------------------------------

    def check_routable(self) -> None:
        """Raise ``ValueError`` if this account cannot send an order right now.

        Four standing facts about the situation, and each of them is a state
        somebody has to leave rather than a timer they have to beat:

        - the deployment allows routing at all (``LIVE_ROUTING``);
        - a **real** account is selected, which no session starts on;
        - a person has **labelled** it demo or live — the rule that outlived
          ``RITHMIC_ENV``, and the only one of the four the app could never work
          out for itself;
        - the broker has been **asked** what is working and what is held.

        This replaced the arm — a typed confirmation with a fifteen-minute
        lease. What the arm added over this list was a deadline, and a deadline
        is the wrong shape for the job: it lapsed while somebody was reading the
        chart and it stood open while nobody was watching, so it was ceremony at
        the moments it was meant to matter. Everything it actually enforced is
        above, and it is enforced on every order rather than once per lease.
        """
        refusal = self.policy.refusal()
        if refusal:
            raise ValueError(refusal)
        if self.paper:
            raise ValueError(
                "the paper account cannot reach a broker — select a real "
                "account to send there.")
        if not self.attached:
            raise ValueError("the order plant is not connected")
        if self.tag is None:
            # Untagged is not demo; there is no default, because the wrong
            # default is an order on a funded account.
            raise ValueError(
                f"{self.account_id} has not been labelled demo or live. Nothing "
                "Rithmic sends says which it is, so the app cannot work it out "
                "— tag it first, and it stays tagged.")
        if self.reconciled_at is None:
            raise ValueError(
                "the broker's state has not been read back yet — sending before "
                "the working orders and the position are known would mean "
                "trading against a picture this process made up")

    @property
    def routable(self) -> bool:
        """``check_routable`` as a boolean, for the snapshot the panel draws."""
        try:
            self.check_routable()
        except ValueError:
            return False
        return True

    def _routable(self) -> str:
        """The account's kind, or a ``PermissionError`` saying why it cannot send.

        The gate every order path goes through. It returns the tag's kind
        because the caller needs it anyway — the confirm sentence names what
        kind of account this is, and reading it here means there is exactly one
        place that decides "may this send" and "what is it sending on".
        """
        try:
            self.check_routable()
        except ValueError as e:
            raise PermissionError(str(e)) from e
        return self.tag.kind

    def roll_day(self, day: date) -> None:
        """Follow the 18:00 session roll: new journal, new day, nothing staged.

        Called from ``state._roll_to``, which keeps the *feed* across the roll
        (it belongs to the run) while replacing the session. The broker rides on
        the feed and so would survive too — which is right for the connection
        and wrong for a review of an order priced in the old session, so those
        are dropped here explicitly rather than by luck of where the object
        happens to live.
        """
        self.day = day
        self.journal = OrderJournal(self.symbol, day)
        # A new day is the only thing that clears a daily stop. Not a restart,
        # not an account switch, not a recovery back above the line — the roll,
        # which is also the only one of those that is actually a new day.
        self.reset_day()
        self.confirms.clear()

    # --- placing ------------------------------------------------------------

    def _intent(self, kw: dict) -> Intent:
        from .routing import build_intent

        return build_intent(self.policy, symbol=self.symbol,
                            exchange=self.exchange,
                            account_id=self.account_id,
                            # The bracket rules are about opening a position;
                            # size coming off one has no target to be too tight.
                            reducing=self._reducing(kw.get("side"),
                                                    int(kw.get("qty") or 1)),
                            # What one tick is worth on the contract routing is
                            # pointed at — which since `use_instrument` is not
                            # necessarily the one the chart is on, and is exactly
                            # why the ceiling is in dollars. Read off the broker
                            # rather than assumed, so the same 50 ticks refuses
                            # at $250 of NQ and passes at $25 of MNQ.
                            tick_usd=self.tick_size * self.point_value,
                            **kw)

    def preview(self, **kw) -> dict:
        """Validate and render an order, and mint the token that can send it.

        Nothing leaves the process here. The token is the only way to reach
        ``send``, so on an account that confirms, the review is not a UI
        convention a script could skip — an unreviewed order has no handle.
        """
        kind = self._routable()
        intent = self._intent(kw)
        # Refused here as well as at submit, so the sentence is never rendered
        # for an order that cannot go — a review that reads like permission and
        # then fails on Send is worse than an early no.
        self._guard(intent, "preview")
        token, ttl = self.confirms.stage(intent)
        return {
            "token": token,
            "expires_in_s": ttl,
            "sentence": intent.sentence(kind, self.tick_size),
            "intent": intent.__dict__,
        }

    def send(self, token: str) -> dict:
        """Spend a reviewed token: this is the call that reaches the exchange."""
        kind = self._routable()
        intent = self.confirms.consume(token)
        return self._submit(intent, kind, how="review")

    def send_now(self, **kw) -> dict:
        """Send without a review — the one-click door.

        Refused unless **this account** has one-click switched on. That is what
        keeps "send an unreviewed order" from being a request shape that merely
        exists: the flag is per-account, off by default, and cleared whenever an
        account is tagged live, so it cannot be enabled on a practice account
        and inherited by a funded one.

        The order still passes ``check_routable``, still passes the quantity
        ceiling, and is still journalled — with **how it went out**, because
        "was this one-click" is the first question anyone asks about a fill they
        did not expect.
        """
        kind = self._routable()
        tag = self.tag
        if tag is None or not tag.one_click:
            raise PermissionError(
                f"one-click trading is off for {self.account_id} — review the "
                "order, or switch it on for this account in the order-entry "
                "settings")
        return self._submit(self._intent(kw), kind, how="one_click")

    def _submit(self, intent: Intent, kind: str, how: str = "review") -> dict:
        from async_rithmic import OrderDuration, OrderType, TransactionType

        # The last gate before the wire, and the one that has to be here rather
        # than only on the review: `send` spends a token minted earlier, and
        # "still allowed when I staged it" is not the question.
        self._guard(intent, how)
        self._counter += 1
        tag = f"aj-{int(time.time())}-{self._counter}"
        kwargs: dict = {"account_id": intent.account_id,
                        "duration": OrderDuration.DAY}
        if intent.type == "market":
            otype = OrderType.MARKET
        elif intent.type == "limit":
            otype = OrderType.LIMIT
            kwargs["price"] = intent.price
        else:
            otype = OrderType.STOP_MARKET
            kwargs["trigger_price"] = intent.price
        if intent.stop_ticks:
            kwargs["stop_ticks"] = intent.stop_ticks
        if intent.target_ticks:
            kwargs["target_ticks"] = intent.target_ticks
        if intent.stop_ticks or intent.target_ticks:
            # Remembered under the same tag the trail extras use, and for the
            # same reason: it is the one identifier that is ours and unique
            # before Rithmic has said a word back. `_order_rec` hands it to the
            # chart so a resting entry can show the bracket it will get.
            self._sent_bracket[tag] = {"stop_ticks": intent.stop_ticks,
                                       "target_ticks": intent.target_ticks}
        # Registered against this order's tag for `_patch_order_plant` to pick
        # up, because async_rithmic drops unknown kwargs rather than forwarding
        # them.
        managed: dict = {}
        if intent.trail_trigger_ticks:
            # `trailing_stop_by_last_trade_price` matches how the replay
            # measures a trail, so paper and real stay comparable instead of
            # diverging by a spread.
            managed["trailing_stop_trigger_ticks"] = intent.trail_trigger_ticks
            managed["trailing_stop_by_last_trade_price"] = True
        if intent.be_trigger_ticks:
            managed["break_even_trigger_ticks"] = intent.be_trigger_ticks
            # **The only place the sign exists.** Rithmic's `break_even_ticks`
            # is raw price arithmetic and does not know which way you are
            # facing: `stop = fill + break_even_ticks x tick_size`. So locking N
            # ticks of profit is +N long and -N short, measured both ways on
            # MNQU6 (2026-08-11) — a short sent -3 put the stop 3 ticks under
            # the fill, and a short sent +3 put it 3 ticks over, which is 3
            # ticks of *risk* wearing the word breakeven.
            #
            # `Intent.be_ticks` is therefore always positive and always means
            # "in the trade's favour". Everything above this line, including the
            # confirm sentence and the ticket, speaks that language.
            managed["break_even_ticks"] = (intent.be_ticks
                                           if intent.side == "buy"
                                           else -intent.be_ticks)
        if managed:
            self._trailing[tag] = managed
            # Remembered past the send, unlike `_trailing`, which the plant patch
            # consumes on its way out. A breakeven leaves no mark on the leg it
            # will later move, so without this the drag guard cannot see it —
            # cleared when the position this order opens goes flat.
            self._managed_bracket = True

        self.journal.write("submit", tag=tag, how=how, **intent.__dict__)
        try:
            res = self._call(
                lambda c: c.submit_order(
                    order_id=tag, symbol=self.symbol, exchange=self.exchange,
                    qty=intent.qty, order_type=otype,
                    transaction_type=(TransactionType.BUY if intent.side == "buy"
                                      else TransactionType.SELL),
                    **kwargs),
                "submit")
        except Exception as e:  # noqa: BLE001 — reported, never swallowed
            self.journal.write("submit_failed", tag=tag,
                               error=f"{type(e).__name__}: {e}")
            raise
        if not self._reducing(intent.side, intent.qty):
            # The slow-down clock starts when the entry goes out, not when it
            # fills: what the measurement showed is people re-entering fast, and
            # an order sitting unfilled is already that decision made.
            self._day()["last_entry_at"] = time.monotonic()
        first = (res or [None])[0]
        basket = _s(getattr(first, "basket_id", ""))
        self.journal.write("submitted", tag=tag, how=how, basket_id=basket)
        return {"tag": tag, "basket_id": basket, "how": how,
                "sentence": intent.sentence(kind, self.tick_size)}

    def modify(self, basket_id: str, price: float | None = None,
               stop: float | None = None, target: float | None = None) -> dict:
        """Move a working order to a price. A bracket leg is one of those.

        This is what a drag on the chart lands on, and the shape of it is
        dictated by one fact about Rithmic that took a journal to see:

        **Once the entry fills, the bracket legs are ordinary working orders.**
        They get their own baskets (entry 189842650 → target 189842651, stop
        189842652, MNQU6 2026-08-11), and from that moment the bracket *as a
        bracket* is unreachable: template 341 answers for the parent only until
        the fill, and afterwards returns nothing for the parent or the legs, so
        ``modify_order``'s ``stop_ticks``/``target_ticks`` path — which reads the
        current distance back before it can send a new one — cannot work on an
        open position at all. It raises "No stop loss was set at order creation",
        which is true of nothing: the stop is right there, working, 100 ticks
        off the fill where it was asked for. Six drags died on that message
        before the cause was found, all of them on orders that carried a stop.

        So a leg is moved the way any other working order is moved: by its own
        price, on its own basket. No ticks, no entry to measure from, no
        distinction in the wire call between dragging a stop and dragging a
        resting limit — ``stop``/``target`` name which leg the *chart* thinks it
        grabbed, and are checked against what the order actually is.

        **What this cannot do is fight the server for the stop.** A trailing or
        breakeven bracket is Rithmic's to move, and it recomputes absolutely:
        for a short, ``stop = running_low + stop_ticks``, re-derived on every new
        extreme rather than nudged from where the stop currently sits (the same
        MNQU6 position: a 100-tick stop tracking the low down twelve times, ours
        for none of them). A drag against that survives exactly until the next
        tick of profit, and then reverts — *outward*, if the drag was a
        tightening, which is the direction that matters. You would be risking
        more than the chart was showing. Those drags are refused rather than
        sent; see `_server_managed`.

        **One field per call.** The three arguments are three names for one
        thing — this order's resting price — so asking for two at once is a
        confusion rather than a batch, and is refused as one. A per-basket lock
        still stands behind it, because Rithmic answers 'Atomic order operation
        in progress' to overlapping modifies on the same order.

        Nothing here is optimistic. The chart is not told the new value; the next
        poll reads it back from the broker, so a refused modify shows up as a
        line that returns to where it was. That snap *is* the error report — and
        the case where nothing is refused and nothing moves either is what
        `_reprice` and the ``modify_ignored`` journal line exist to catch.
        """
        self._routable()
        with self._lock:
            rec = self.working.get(basket_id)
        if rec is None:
            raise LookupError(
                "that order is not working at the broker any more — it filled, "
                "or was cancelled")
        asked = [(n, v) for n, v in (("stop", stop), ("target", target),
                                     ("price", price)) if v is not None]
        if len(asked) > 1:
            raise ValueError(
                "a drag moves one order to one price — "
                f"{' and '.join(n for n, _ in asked)} cannot be sent together")
        if not asked:
            return {"basket_id": basket_id, "changed": []}
        (leg, want), = asked
        want = float(want)
        kind = _s(rec.get("type"))
        # The chart names the leg from the shape of the order book it is drawing;
        # this is the same question asked of the order itself. They disagree only
        # if something is wrong upstream, and a stop moved as though it were a
        # target is the kind of wrong that should not reach the wire.
        if leg == "stop" and kind not in ("stop", "stop_limit", "mit"):
            raise ValueError(
                f"that order is a {kind or 'unknown'}, not a stop — refusing to "
                "move it as one")
        if leg == "target" and kind not in ("limit", "lit"):
            raise ValueError(
                f"that order is a {kind or 'unknown'}, not a limit — refusing to "
                "move it as a target")
        if self._server_managed(rec, leg):
            raise ValueError(
                "Rithmic is managing this bracket — it re-derives the stop from "
                "the high water mark on every new tick of profit, so a stop "
                "moved here would be silently put back, wider, the moment the "
                "trade goes your way. Flatten, or re-enter without a trail.")
        fields = _reprice(rec, want)
        lock = self._basket_lock(basket_id)
        with lock:
            self.journal.write("modify", basket_id=basket_id, leg=leg,
                               price=want, fields=sorted(fields))
            # Armed *before* the send, not after. The notification arrives on the
            # event loop's thread and does not wait for this call to return — on
            # a local plant it can and does land first, and an expectation
            # recorded afterwards would be resolved by whatever notification came
            # next instead, which is a false alarm on an order that moved fine.
            self._asked[basket_id] = (leg, want, time.monotonic())
            try:
                self._call(
                    lambda c: c.modify_order(
                        basket_id=basket_id, account_id=self.account_id,
                        order=_as_order(rec, self.account_id, self.exchange),
                        **fields),
                    f"modify {leg}")
            except Exception as e:  # noqa: BLE001
                self._asked.pop(basket_id, None)
                self.journal.write("modify_failed", basket_id=basket_id,
                                   leg=leg, error=f"{type(e).__name__}: {e}")
                raise
        return {"basket_id": basket_id, "changed": [leg], "asked": want}

    def _server_managed(self, rec: dict, leg: str) -> bool:
        """Is this order Rithmic's to move rather than ours?

        Two sources, because neither is sufficient alone, and they are not asked
        the same question.

        ``trail_by_ticks`` is on the **order itself** — the distance it rides
        behind the extreme, confirmed on MNQU6 2026-08-10. It is the answer
        straight from the broker, it survives a restart of this process, and it
        disqualifies the order however the drag was addressed: `price` and `stop`
        reach the same wire call now, so letting the leg name decide would leave
        the guard one spelling away from being bypassed.

        ``_managed_bracket`` is this process's memory that a **breakeven** went
        out, which moves the stop once and sets no field on the leg for the
        first test to find. It says nothing about *which* order, so it is spent
        only on a bracket drag — an unrelated entry resting while a managed
        position is open is still yours to move.

        Erring towards "managed" on purpose. The cost of a false yes is a drag
        that is refused and has to be done by flattening; the cost of a false no
        is a stop that appears to move, does, and is then walked back out to a
        loss the chart never showed.
        """
        if _i(rec.get("trail_by_ticks", 0)):
            return True
        return leg != "price" and self._managed_bracket

    def _basket_lock(self, basket_id: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(basket_id, threading.Lock())

    def cancel(self, basket_id: str) -> dict:
        self._routable()
        self.journal.write("cancel", basket_id=basket_id)
        res = self._call(
            lambda c: c.cancel_order(basket_id=basket_id,
                                     account_id=self.account_id),
            "cancel")
        return {"basket_id": basket_id, "ok": bool(res)}

    def flatten(self) -> dict:
        """The kill switch: cancel everything working, then exit the position.

        **Deliberately gated on nothing but the connection.** Not the tag, not
        the reconciliation, not the guardrails — a stop button you have to
        unlock is not a stop button, and the situation in which you most want
        this is the one where something is behaving in a way nobody planned.

        Cancels first and exits second, in that order: exiting while a bracket
        is still working can leave the bracket to open a fresh position in the
        opposite direction the moment it triggers.
        """
        if self.paper:
            # Not an error the caller should have to handle: the page flattens
            # paper through its own log, and this being reachable at all is a
            # stale panel rather than a mistake worth shouting about.
            raise LookupError(
                "the paper account has nothing at a broker to flatten")
        self.journal.write("flatten")
        errors: list[str] = []
        try:
            self._call(lambda c: c.cancel_all_orders(account_id=self.account_id),
                       "cancel_all")
        except Exception as e:  # noqa: BLE001 — the exit still has to be tried
            errors.append(f"cancel_all: {type(e).__name__}: {e}")
        try:
            self._call(lambda c: c.exit_position(account_id=self.account_id,
                                                 symbol=self.symbol,
                                                 exchange=self.exchange),
                       "exit_position")
        except Exception as e:  # noqa: BLE001
            errors.append(f"exit_position: {type(e).__name__}: {e}")
        self.journal.write("flattened", errors=errors)
        # Anything staged goes with it. Whatever made somebody hit this, a
        # review written before it should not still be one click from the wire.
        self.confirms.clear()
        if errors:
            raise RuntimeError(
                "; ".join(errors) + " — check the platform directly")
        return {"ok": True}

    def refresh(self) -> dict:
        """Re-ask the broker. The manual half of ``reconcile``."""
        return self._call(lambda c: self.reconcile(), "reconcile")

    # --- reading ------------------------------------------------------------

    def accounts_view(self) -> list[dict]:
        """Everything the selector draws — paper first, then the real ones.

        Paper is listed as an account rather than as a mode beside one, which is
        the whole point of this design: one selector, one mental model, and the
        thing that cannot trade sitting first in it.
        """
        out = [{"id": PAPER, "kind": PAPER, "one_click": True,
                "label": "Paper", "tagged": True}]
        for a in self.accounts:
            t = tag_of(self.system, a)
            out.append({
                "id": a,
                # None, not "demo". The badge has to be able to say "untagged",
                # because that is a state a person has to resolve rather than
                # one the app may resolve for them.
                "kind": t.kind if t else None,
                "one_click": bool(t and t.one_click),
                "label": a,
                "tagged": t is not None,
            })
        return out

    def snapshot(self) -> dict:
        tag = self.tag
        return {
            "attached": self.attached,
            "ready": self.ready,
            "account_id": self.account_id,
            "paper": self.paper,
            # The active account's declared kind, or null while it is untagged.
            # Null is one of the reasons an order refuses, so the panel reads it
            # directly rather than inferring from the absence of something else.
            "kind": tag.kind if tag else (PAPER if self.paper else None),
            # Whether this account skips the confirm popup. Always true for
            # paper — confirming a practice order is friction for nothing.
            "one_click": True if self.paper else bool(tag and tag.one_click),
            "accounts": self.accounts_view(),
            "symbol": self.symbol,
            "exchange": self.exchange,
            # Where orders may be sent, which is not what the chart is showing.
            # The panel draws the difference rather than hiding it: routing to a
            # contract the tape is not on is the point of the control, and also
            # the thing most worth saying out loud.
            "instruments": list(self.instruments),
            "feed_symbol": self.instruments[0] if self.instruments else self.symbol,
            # Both change with the instrument, and the panel's risk arithmetic
            # is drawn from them rather than assumed.
            "tick_size": self.tick_size,
            "point_value": self.point_value,
            "commission_per_side": self.commission_per_side(),
            # None means "not asked", which is a different claim from "nothing
            # working" and the whole reason the field is nullable.
            "reconciled_at": self.reconciled_at,
            # Will a gesture actually reach the exchange? The whole of
            # `check_routable` folded into one boolean, so the chart's "this is
            # live" outline is drawn from the same answer the order path gives
            # rather than from a re-derivation of it.
            "routable": self.routable,
            "max_qty": self.policy.max_qty,
            "working": sorted(self.working.values(),
                              key=lambda r: r.get("at", 0.0)),
            "recent": list(self.recent)[:12],
            # Round trips paired out of the fill stream, so a real account's
            # closed trades draw with the same marks the paper blotter uses.
            "trades": list(self.trades),
            # Non-zero means a trade happened that the journal does not know
            # about. `orders.jsonl` still has it, so it is recoverable — but
            # nothing recovers it automatically, so the number has to be visible.
            "booking_errors": self.stats_booking_errors,
            "position": self.position,
            "guard": self.guard_view(),
            "error": self.error,
        }

    def guard_view(self) -> dict:
        """The discipline layer, as the panel draws it.

        Everything here is readable at a glance for one reason: the layer being
        **off** has to be as visible as the layer refusing something. A safety
        rail that is silently disabled is worse than one that was never built,
        because it gets traded as though it were there.

        Both day figures are carried. ``realized`` is what this process paired
        and is what the rules are enforced on; ``broker_day_pnl`` is what the
        PnL plant says the account did. ``divergence`` is the difference, and it
        is shown rather than resolved — a gap means one of the two is missing
        trades, which is worth knowing about and not worth guessing at.

        ``restored`` is how many of ``realized``'s trades came back off the
        journal rather than out of a fill this process watched — non-zero after
        a restart, and worth saying rather than presenting a rebuilt figure as
        though it had been counted live. It is not a warning: the rebuilt number
        is the trustworthy one, and a zero here on an afternoon that has already
        traded is the thing to be alarmed by.
        """
        g = self.policy.guards
        st = self._day_state()
        rec = self._day()
        broker_day = (self.position or {}).get("day_pnl")
        on = self.policy.guardrails
        return {
            "on": on,
            "realized": st.realized,
            "restored": int(rec.get("restored", 0)),
            "trades": st.trades,
            "locked": st.locked,
            "since_entry_s": st.since_entry_s,
            # Past the slow-down threshold but not yet stopped: the band where
            # the rule is doing its work and the number worth having on screen.
            "slow": bool(on and g.slow_down_at
                         and st.realized <= -g.slow_down_at
                         and not st.locked),
            # Realised plus what the open position is currently down — the
            # number the account's own drawdown is measured against, and what
            # the automatic flatten fires on. Realised alone would sit silent
            # through an open loss that had already spent the limit.
            "equity": self._equity(),
            "open_pnl": _f((self.position or {}).get("open_pnl")),
            "auto_flattened": self._flattening,
            "broker_day_pnl": broker_day,
            "divergence": (None if broker_day is None
                           else broker_day - st.realized),
            "levels": {f: getattr(g, f) for f in type(g).__dataclass_fields__},
        }
