"""Order routing — the only endpoints in this API that can reach an exchange.

Kept in its own file rather than added to ``live.py``, and that is structural
rather than tidy. ``live.py``'s docstring ends "NO ORDER ROUTING. Nothing in
this router can send an order", and that sentence is still true: the shadow
reads, the feed switches and the tape history live there and none of them have
grown a side effect. Everything that can trade is here, where it can be read in
one sitting.

The reasoning behind each refusal is in ``journal.live.routing`` (the four gates)
and ``journal.live.broker`` (why the order plant rides on the tick feed's own
connection). What this layer adds is the mapping from those refusals onto status
codes that mean something to a page:

  - **403** the environment does not allow routing at all — ``LIVE_ROUTING`` is
    unset, ``RITHMIC_ENV`` is unset or names a live account without
    ``RITHMIC_ALLOW_LIVE``. A property of the deployment, not of the moment.
  - **404** there is no session, or the running one is not a routing session.
  - **409** the active account is paper or untagged, the broker has not been
    read back yet, or a **guardrail** refused — the day is over, or the entry is
    too soon after the last one. A property of the moment: try again after doing
    something, or tomorrow.
  - **422** the order itself is wrong — a limit with no price, a quantity over
    the ceiling, a confirmation that does not name the environment.
  - **504** the order plant did not answer in time, which is the one failure
    where the answer to "did it go?" is genuinely *unknown* and the message
    says so rather than guessing.

TWO STEPS TO SEND, ENFORCED HERE AND NOT IN THE CLIENT. ``/preview`` renders the
order as an English sentence and returns a single-use token; ``/send`` takes the
token and nothing else. There is no field on ``/send`` that describes an order,
so a caller that skipped the review has nothing to put in the request — the
"no single-click path to a live order" rule is a shape of the API rather than a
habit of the UI.

THE KILL SWITCH IS GATED ON NOTHING BUT THE CONNECTION. ``/flatten`` needs a
socket and nothing more — not the tag, not the reconciliation, not the
guardrails. The moment you most want it is the moment the page was reloaded, or
something is behaving in a way nobody planned.

THERE IS NO ARM. There used to be ``/arm`` and ``/disarm`` — a typed
confirmation with a fifteen-minute lease on the ability to send. What actually
gated an order is now checked on every one of them
(``Broker.check_routable``): routing is switched on, a real account is selected,
a person has labelled it, and the broker has been read back. A lease added a
deadline on top of that, and a deadline is the wrong shape for the job — it
lapsed while somebody was reading the chart and stood open while nobody was
watching.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from journal import live as livemod
from journal.live import booking as bookmod
from journal.live import routing as rtmod
from journal.live.routing import policy as read_policy

from .. import deps

router = APIRouter()


def _broker():
    """The running session's broker, or an HTTPException saying why there isn't.

    Ordered so the answer is the most actionable one available: a deployment
    that cannot route at all should say so even with no session running, because
    "start a feed and try again" would be wasted advice.
    """
    pol = read_policy()
    refusal = pol.refusal()
    if refusal:
        raise HTTPException(403, refusal)
    live = livemod.current()
    if live is None:
        raise HTTPException(404, "no live session is running")
    broker = live.broker
    if broker is None:
        raise HTTPException(
            404,
            "this session was not started with routing. The ORDER plant is "
            "opened at connect and never afterwards, so a shadow session cannot "
            "acquire the ability to trade while you watch — stop the feed and "
            "reconnect with routing on.")
    return broker


@router.get("/live/routing")
def routing_status() -> dict:
    """What routing can do here, whether or not anything is running.

    Never 404s. The panel asks this before it knows there is a session, and the
    two things it most needs — which environment these credentials are, and
    whether routing is switched on for this deployment at all — are answerable
    with no feed and no broker.

    ``broker.accounts`` is the selector's contents — paper first, then every
    real account with the label somebody gave it, or ``kind: null`` for one
    nobody has. That null is load-bearing: an untagged account cannot send,
    because nothing Rithmic sends says whether it is funded and the app must not
    guess.
    """
    pol = read_policy()
    live = livemod.current()
    broker = live.broker if live is not None else None
    return {
        "enabled": pol.enabled,
        "max_qty": pol.max_qty,
        # Answerable with no feed and no broker, like `enabled`, and for the
        # same reason: "are the rules on" is a property of the deployment, and
        # the panel has to be able to say it is off before anything is running.
        "guardrails": pol.guardrails,
        "guards": {f: getattr(pol.guards, f)
                   for f in type(pol.guards).__dataclass_fields__},
        "refusal": pol.refusal(),
        "session": live is not None,
        # A session running without routing is the ordinary case, and it is a
        # different thing from routing being unavailable — the panel says which.
        "routing_session": broker is not None,
        "broker": broker.snapshot() if broker is not None else None,
    }


class TagIn(BaseModel):
    """Label an account demo or live. ``confirm`` must repeat the kind.

    Typed rather than clicked, and it is now the only typed confirmation left on
    this router: it makes the person state what they believe the account is.
    Once per account rather than once per session — the tag is what replaced
    ``RITHMIC_ENV``, and it sticks.
    """

    kind: str = Field(..., description='"demo" or "live"')
    confirm: str = Field(..., description="repeat the kind to confirm")


@router.put("/live/routing/accounts/{account_id}")
def routing_tag(account_id: str, body: TagIn) -> dict:
    """Declare what an account is.

    Nothing Rithmic sends says whether an account is funded — the account list
    carries an id, a name, an FCM and a loss limit, and nothing else. So this is
    a person's declaration, stored where it can be seen instead of in an env var
    nobody can check. Anything staged behind a review is dropped: the sentence
    somebody read named a kind of account, and re-labelling changes what it
    said.
    """
    broker = _broker()
    kind = (body.kind or "").strip().lower()
    if (body.confirm or "").strip().lower() != kind:
        raise HTTPException(422, f"type {kind!r} to confirm the label")
    if account_id not in broker.accounts:
        raise HTTPException(
            404, f"{account_id!r} is not one of this login's accounts")
    try:
        rtmod.set_tag(broker.system, account_id, kind)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if broker.account_id == account_id:
        broker.confirms.clear()
    return broker.snapshot()


class OneClickIn(BaseModel):
    on: bool


@router.put("/live/routing/accounts/{account_id}/one_click")
def routing_one_click(account_id: str, body: OneClickIn) -> dict:
    """Turn the confirm popup off (or back on) for one account.

    Per account, never global. Tagging an account ``live`` clears it
    (``routing.set_tag``), so the fast path cannot be switched on for practice
    and then inherited by a funded account.
    """
    broker = _broker()
    if account_id not in broker.accounts:
        raise HTTPException(
            404, f"{account_id!r} is not one of this login's accounts")
    try:
        rtmod.set_one_click(broker.system, account_id, body.on)
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    return broker.snapshot()


class GuardsIn(BaseModel):
    """The discipline levels. **Every field is optional and null means "leave it"**.

    A partial patch rather than a whole object, so a form that predates a guard
    added later cannot silently reset it. ``0`` is a real value and disables that
    one rule — which is why the floor is 0 rather than a positive minimum.

    There is no field here for turning the layer *off*. That is ``LIVE_GUARDRAILS``
    in the environment, on purpose: switching the rules off should mean leaving
    the chart, not reaching a control that sits next to the order pad.
    """

    daily_loss_stop: float | None = Field(None, ge=0)
    daily_profit_lock: float | None = Field(None, ge=0)
    slow_down_at: float | None = Field(None, ge=0)
    min_gap_s: float | None = Field(None, ge=0)
    min_target_ticks: int | None = Field(None, ge=0)
    stop_ticks_min: int | None = Field(None, ge=0)
    stop_ticks_max: int | None = Field(None, ge=0)
    require_bracket: bool | None = None
    auto_flatten: bool | None = None
    max_risk_usd: float | None = Field(None, ge=0)
    commission_per_side: float | None = Field(None, ge=0)


class SettingsIn(BaseModel):
    max_qty: int | None = Field(None, ge=1)
    guards: GuardsIn | None = None


@router.put("/live/routing/settings")
def routing_settings(body: SettingsIn) -> dict:
    """The knobs that used to be env vars, plus the guardrail levels.

    ``max_qty`` catches a slipped digit before the account's own risk limits are
    involved. Everything here takes effect on the **next order** rather than
    retroactively, which needs no explaining now that there is no lease for it
    to be measured against: an order is checked against the rules as they stand
    when it is sent.

    A running session is handed the new settings rather than left to find them.
    ``Broker`` captures its policy when the order plant opens, so a save that
    only reached the store would be enforced by tomorrow's session and by
    nothing today — and the panel, which draws the *broker's* levels, would show
    the old numbers back and read as a save that failed.
    """
    s = rtmod.save_settings(
        max_qty=body.max_qty,
        guards=body.guards.model_dump(exclude_none=True) if body.guards else None)
    live = livemod.current()
    broker = live.broker if live is not None else None
    if broker is not None:
        broker.use_settings(s)
    return {"max_qty": s.max_qty,
            "guards": {f: getattr(s.guards, f)
                       for f in type(s.guards).__dataclass_fields__}}


class PaperTrade(BaseModel):
    """One closed round trip as ``replaySim`` computed it.

    Deliberately the browser's shape, not a re-derivation. The fill engine for
    paper trades is ``frontend/src/lib/replaySim.ts`` and there is only one of
    it — the same rule ``api.routers.replays`` states for stored attempts, and
    for the same reason: a server that recomputed the trade could disagree with
    the chart that produced it, and then neither is trustworthy.
    """

    side: str                       # long | short
    size: float
    entry_price: float
    entry_ms: float
    exit_price: float
    exit_ms: float
    pnl: float
    pts: float | None = None
    reason: str = "manual"


class PaperTradesIn(BaseModel):
    symbol: str
    exchange: str = "CME"
    #: The session date the sitting belongs to — the client's, because the
    #: session it is watching is the authority on which day these happened in,
    #: and a server clock six hours away would file them under the wrong one.
    date: str
    trades: list[PaperTrade]


@router.post("/live/journal/paper")
def journal_paper_trades(body: PaperTradesIn) -> dict:
    """Book paper trades taken on the live chart into the journal.

    **Not behind ``LIVE_ROUTING``, and behind none of the order gates.** Paper
    trades reach no broker, so none of them are about it — a checkout with
    routing switched off still practises on the live chart, and those trades
    should still be recorded. This endpoint is why ``_broker()`` is not called
    here.

    They land as account ``paper`` with ``sessions.mode='replay'``, which is how
    ATAS's own ``Replay`` account is already handled: visible in Trades, on the
    Calendar and filterable by account, and out of the real-money statistics
    unless the mode filter asks for them.

    Re-posting is expected rather than exceptional — the page posts what it has
    and lets the content hash sort it out, so a reload or a slow reply can never
    lose a trade or double one.
    """
    try:
        day = date.fromisoformat(body.date)
    except ValueError as e:
        raise HTTPException(422, f"bad session date: {body.date!r}") from e
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(422, "which contract these were taken on")

    conn = deps.get_conn()
    with deps.db_lock():
        written = bookmod.book_trades(
            conn,
            account=bookmod.PAPER_ACCOUNT,
            instrument=f"{symbol}@{body.exchange.strip().upper()}",
            mode="replay",
            session_date=day,
            trades=[t.model_dump() for t in body.trades],
        )
    return {
        "written": written,
        "received": len(body.trades),
        "account": bookmod.PAPER_ACCOUNT,
        "source_file": bookmod.source_file_for(bookmod.PAPER_ACCOUNT, day),
    }


class AccountIn(BaseModel):
    account_id: str


@router.post("/live/routing/account")
def routing_account(body: AccountIn) -> dict:
    """Point routing at a different account on this login, mid-session.

    **Anything staged behind a review goes**, since the sentence named the old
    account. The new account's working orders and position are then re-read, and
    orders are refused in the moment between — the same rule as connecting.

    409 when the deployment pinned an account with ``LIVE_ORDER_ACCOUNT``: that
    is a configuration decision, and quietly overriding it from a page would be
    the app deciding the pin was advisory.
    """
    broker = _broker()
    try:
        return broker.use_account(body.account_id)
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e


class InstrumentIn(BaseModel):
    instrument: str


@router.post("/live/routing/instrument")
def routing_instrument(body: InstrumentIn) -> dict:
    """Point routing at a different contract — the mini's micro, or back.

    The tape does not follow: one login is one socket and the subscription was
    made at connect, so this changes where orders go and not what is on screen.
    That is the feature rather than a limitation of it — a plan sized for micros
    can be sent as micros against the mini chart it was drawn on, which is the
    hazard ``routing.Guards.max_risk_usd`` exists to survive.

    **409 unless the book is flat.** Working orders and an open position belong
    to the contract they were placed on, and the automatic daily-loss flatten
    exits whichever contract routing is pointed at — so a switch made with
    something open would aim the one automatic protection here at the wrong
    instrument. Cancel and flatten first; the refusal says which.

    Drops anything staged, like every other switch on this router: the same 50
    ticks is $250 of NQ or $25 of MNQ, and a sentence somebody reviewed named an
    amount.
    """
    broker = _broker()
    try:
        return broker.use_instrument(body.instrument)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e


class OrderIn(BaseModel):
    """One order, before it has been reviewed. Never sent from this shape."""

    side: str = Field(..., description="buy | sell")
    qty: int = Field(1, ge=1)
    type: str = Field("market", description="market | limit | stop")
    price: float | None = Field(None, description="resting price; ignored for market")
    stop_ticks: int = Field(0, ge=0)
    target_ticks: int = Field(0, ge=0)
    trail_trigger_ticks: int = Field(
        0, ge=0,
        description="Ticks of profit before Rithmic starts ratcheting the stop "
                    "up behind the high. 0 is off. There is no separate trail "
                    "distance: it rides at stop_ticks, so the only free "
                    "variable is when it wakes up. Refused without a stop.")
    be_trigger_ticks: int = Field(
        0, ge=0,
        description="Ticks of profit before Rithmic jumps the stop to a "
                    "breakeven-plus level. 0 is off. Fires once, unlike the "
                    "trail. Refused without a stop.")
    be_ticks: int = Field(
        0, ge=0,
        description="How many ticks of profit that jump locks in — always "
                    "positive, always in the trade's favour; the API negates it "
                    "for a sell. Must be at least 1 when be_trigger_ticks is "
                    "set: Rithmic's field is a proto3 scalar, so a zero never "
                    "reaches the wire and 'exactly at the fill' cannot be said.")


@router.post("/live/routing/preview")
def routing_preview(body: OrderIn) -> dict:
    """Validate an order and render it in words. Nothing leaves the process.

    Returns a single-use token that expires in well under a minute, because the
    sentence names a price and a price that was true a minute ago describes a
    different order.
    """
    broker = _broker()
    try:
        return broker.preview(side=body.side, qty=body.qty, type=body.type,
                              price=body.price, stop_ticks=body.stop_ticks,
                              target_ticks=body.target_ticks,
                              trail_trigger_ticks=body.trail_trigger_ticks,
                              be_trigger_ticks=body.be_trigger_ticks,
                              be_ticks=body.be_ticks)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class SendIn(BaseModel):
    """Two doors, one endpoint, and which one you came through is recorded.

    **The reviewed door** carries ``token`` and nothing else — no field on it
    describes an order, so an order that was never rendered as a sentence cannot
    be sent through it.

    **The one-click door** carries the order outright, and the server refuses it
    unless *this account* has one-click switched on. So "send an unreviewed
    order" is not a shape that exists until somebody turns it on, per account,
    and tagging an account live turns it back off.
    """

    token: str | None = None
    one_click: bool = False
    side: str | None = None
    qty: int = 1
    type: str = "market"
    price: float | None = None
    stop_ticks: int = 0
    target_ticks: int = 0
    trail_trigger_ticks: int = 0
    be_trigger_ticks: int = 0
    be_ticks: int = 0


@router.post("/live/routing/orders")
def routing_orders(body: SendIn) -> dict:
    """Send an order. The only endpoint that reaches an exchange."""
    broker = _broker()
    try:
        if body.one_click:
            return broker.send_now(side=body.side, qty=body.qty, type=body.type,
                                   price=body.price, stop_ticks=body.stop_ticks,
                                   target_ticks=body.target_ticks,
                                   trail_trigger_ticks=body.trail_trigger_ticks,
                                   be_trigger_ticks=body.be_trigger_ticks,
                                   be_ticks=body.be_ticks)
        if not body.token:
            raise HTTPException(
                422, "an order needs either a review token or one-click "
                     "trading switched on for this account")
        return broker.send(body.token)
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    except TimeoutError as e:
        # The one case where "did it go?" is genuinely unanswerable from here.
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001 — a broker refusal, reported verbatim
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e


class ModifyIn(BaseModel):
    """A drag, landed. Exactly one price, on the order that was dragged.

    ``stop`` and ``target`` do not name legs of some *other* order: once an entry
    fills, Rithmic's bracket legs are working orders with their own baskets, and
    ``basket_id`` is the leg's own. The three fields differ only in what the
    chart believed it grabbed, which the broker checks against what the order
    actually is.
    """

    basket_id: str
    price: float | None = None
    stop: float | None = None
    target: float | None = None


@router.post("/live/routing/modify")
def routing_modify(body: ModifyIn) -> dict:
    """Move a working order — a bracket leg included — to a price.

    422 carries the refusal worth reading in full: **a bracket Rithmic is
    trailing cannot be dragged.** It re-derives the stop from the high water mark
    on every new tick of profit, so a stop moved here would be put back, wider,
    without a word. Refused rather than sent, because the failure it prevents is
    believing you tightened a stop that then loosened itself.

    Nothing is echoed back optimistically. The next routing poll reads the value
    from the broker, so a refused drag shows up as a line returning to where it
    was, which is a more honest error report than a toast.
    """
    broker = _broker()
    try:
        return broker.modify(body.basket_id, price=body.price,
                             stop=body.stop, target=body.target)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e


class CancelIn(BaseModel):
    basket_id: str


@router.post("/live/routing/cancel")
def routing_cancel(body: CancelIn) -> dict:
    broker = _broker()
    try:
        return broker.cancel(body.basket_id)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e


@router.post("/live/routing/flatten")
def routing_flatten() -> dict:
    """Cancel everything working, then exit the position.

    Gated on nothing but the connection — see the module docstring. Cancels
    before it exits,
    because exiting under a working bracket can leave the bracket to open a
    fresh position in the opposite direction when it triggers.

    A partial failure is a 502 with both halves named. It is not retried
    automatically and it does not report success on the half that worked: the
    only useful thing this can tell somebody whose flatten did not fully land is
    exactly which part did not, so they can go to the platform.
    """
    broker = _broker()
    try:
        return broker.flatten()
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@router.post("/live/routing/refresh")
def routing_refresh() -> dict:
    """Re-ask the broker what is working and what is held.

    The manual half of the reconciliation that runs on connect. Available on any
    account, like the kill switch: reading the truth is never the dangerous
    operation.
    """
    broker = _broker()
    try:
        return broker.refresh()
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}") from e
