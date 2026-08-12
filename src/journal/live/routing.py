"""Which account an order goes to, and how much ceremony it takes to send one.

Everything in this module is policy. It opens no socket, holds no order and
knows nothing about Rithmic's wire format — ``broker.py`` does all three. The
split is deliberate: the rules that decide "is this allowed" are the part that
has to be right, and they are worth testing without a market.

**PAPER IS AN ACCOUNT.** That is the organising idea, and it replaced a design
where the chart's gestures were hard-wired to a simulated blotter and only a
side panel could reach the exchange. The gestures are the point of a chart —
click the level, take the trade — and wiring them to the one thing where speed
does not matter had it backwards. So there is now a single selector holding
``paper`` alongside the real accounts, every way of placing an order works for
all of them, and what changes between them is **not the capability but the
confirmation**.

  ==========  ===============  =====================
  account     confirm popup    reaches the exchange
  ==========  ===============  =====================
  paper       never            no
  demo        on by default    yes
  live        on by default    yes
  ==========  ===============  =====================

``paper`` is also the selection every session **starts** on — a connect, a
restart, the 18:00 roll, a reconnect. There is always an active account, and by
default it cannot trade. That is what makes "did I leave this pointed at
something real" a question with a permanent answer.

**WHERE THE DECLARATION LIVES.** Rithmic does not say whether an account is
funded: ``ResponseAccountList`` carries an id, a name, an FCM and a loss limit,
and nothing about funding. So somebody has to declare it. That used to be
``RITHMIC_ENV`` — one env var for the whole login, which is both wrong (a login
can hold several accounts of different kinds) and invisible from the app. It is
now a **tag on each account**, typed once and stored in the same key-value
settings table the trading profile uses, and shown as a badge everywhere the
account appears.

What survives from the env-var design is the rule that mattered: **untagged is
not demo.** An account nobody has labelled cannot send an order. There is no
default, because the wrong default is an order on a funded account.

**THERE IS NO ARM.** There used to be: a typed confirmation that granted
permission to send for fifteen minutes and lapsed on idle. It was removed
because it was ceremony rather than a gate — everything it actually protected is
still enforced, and by something that cannot lapse at the wrong moment. What is
left is the set of standing facts about the situation: the deployment allows
routing, a real account is selected (a session starts on ``paper`` and choosing
otherwise is an act), that account has been labelled by a person, and the broker
has been asked what is working. Those are checked on every order, not once per
quarter of an hour, so the last line of defence never sits expired while
somebody is trading or granted while nobody is.

**TWO ENV VARS ARE KEPT, AND THEY POINT OPPOSITE WAYS.** ``LIVE_ROUTING`` —
without it the ORDER plant is never opened and every routing endpoint answers
403. It is the deployment-level "this machine must never trade", which the
always-on host needs and which no amount of clicking can undo. ``LIVE_GUARDRAILS``
is the discipline layer at the bottom of this module, and it is **on unless
switched off**, because the safe direction for a restraint is "enforced" the way
the safe direction for a permission is "denied". Both defaults fail toward not
losing money. Guardrails live in the environment rather than in the UI for one
reason: turning them off should require leaving the chart, not a toggle you can
reach at 09:31 with a red P&L. Everything else that used to be an env var — and
the guard *levels* themselves — is a setting with a visible control.

THE CONFIRM IS A TOKEN, NOT A CHECKBOX. ``preview()`` renders an order as a
sentence and mints a single-use token bound to that exact intent; ``consume()``
is the only way to get an intent back. One-click trading is the deliberate
exception, and it is per-account, off by default, and **reset whenever an
account is tagged live** — otherwise it gets switched on for practice and
inherited by a funded account, which is precisely the accident this exists to
prevent.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field

#: The account that is not an account. Reserved: a broker account with this id
#: would be shadowed, which is why it is a word Rithmic ids never take.
PAPER = "paper"

#: Tags an account may carry. ``None`` — untagged — is the third state and is
#: the one that refuses.
KINDS = ("demo", "live")

# How long a reviewed order stays sendable. Short: the sentence names a price,
# and a price that was true a minute ago is a different order.
PREVIEW_TTL_S = 45.0
# Ceiling on a single order, before the account's own RMS ever sees it. A
# fat-fingered quantity is the cheapest accident to make and the most expensive
# to have, and the broker's limit is not visible from here.
MAX_QTY_DEFAULT = 5

_SIDES = ("buy", "sell")
_TYPES = ("market", "limit", "stop")

#: Settings keys in the ``ai_settings`` table (a general key-value store despite
#: the name — ``trading_profile`` and ``recordings_folder`` already live there).
ACCOUNTS_KEY = "routing_accounts"
SETTINGS_KEY = "routing_settings"
INSTRUMENTS_KEY = "routing_instruments"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _flag_off(name: str) -> bool:
    """True only when somebody has explicitly switched something off.

    The mirror of ``_flag``, and the whole difference is what an *unset*
    variable means. ``_flag`` reads unset as "no", which is right for a
    permission: nothing should become able to trade because a file was missing.
    This reads unset as "not switched off", which is right for a restraint —
    the guardrails have to survive a fresh checkout, a new machine and a ``.env``
    nobody has copied yet.

    Both defaults fail toward not losing money, which is the only consistency
    worth having between two flags that point opposite ways.
    """
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


# --- the settings store -------------------------------------------------------
#
# Read and written through `journal.db`'s key-value helpers rather than a file of
# their own. One store for "things the app remembers", and it is already backed
# up with everything else.


def _read(key: str) -> dict:
    from .. import db

    conn = db.connect()
    try:
        raw = db.get_setting(conn, key)
    finally:
        conn.close()
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt settings blob must not take the order path down with it. An
        # empty dict means "untagged", which refuses — the safe direction.
        return {}
    return out if isinstance(out, dict) else {}


def _write(key: str, value: dict) -> None:
    from .. import db

    conn = db.connect()
    try:
        db.save_setting(conn, key, json.dumps(value, sort_keys=True))
    finally:
        conn.close()


def account_key(system: str, account_id: str) -> str:
    """How an account is identified in the store.

    Qualified by the Rithmic system name, because an account id is only unique
    within a login — two firms can both have an ``APEX-1234``, and a tag that
    followed the id alone would silently label somebody else's account.
    """
    return f"{(system or '').strip()}:{account_id}"


@dataclass(frozen=True)
class AccountTag:
    """What a person has declared about one account."""

    kind: str            # demo | live
    one_click: bool      # skip the confirm popup for this account
    at: str = ""         # when it was tagged, ISO

    @property
    def live(self) -> bool:
        return self.kind == "live"


def tags() -> dict[str, AccountTag]:
    out: dict[str, AccountTag] = {}
    for k, v in _read(ACCOUNTS_KEY).items():
        if not isinstance(v, dict) or v.get("kind") not in KINDS:
            continue
        out[k] = AccountTag(kind=v["kind"], one_click=bool(v.get("one_click")),
                            at=str(v.get("at") or ""))
    return out


def tag_of(system: str, account_id: str) -> AccountTag | None:
    return tags().get(account_key(system, account_id))


def set_tag(system: str, account_id: str, kind: str,
            one_click: bool | None = None) -> AccountTag:
    """Label an account, or change its label. Raises ``ValueError`` on a bad kind.

    **Re-tagging as live clears one-click.** The hazard this closes is specific
    and easy to walk into: you enable one-click on a demo account because
    confirming every practice order is friction, then that account is promoted —
    or you re-point the tag — and the fast path silently follows onto real
    money. A promotion to ``live`` is exactly the moment to make somebody ask
    for it again.
    """
    if kind not in KINDS:
        raise ValueError(f"an account is {' or '.join(KINDS)}, not {kind!r}")
    key = account_key(system, account_id)
    store = _read(ACCOUNTS_KEY)
    prev = store.get(key) if isinstance(store.get(key), dict) else {}
    if one_click is None:
        one_click = bool(prev.get("one_click"))
    if kind == "live" and (prev or {}).get("kind") != "live":
        one_click = False
    tag = AccountTag(kind=kind, one_click=bool(one_click),
                     at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    store[key] = {"kind": tag.kind, "one_click": tag.one_click, "at": tag.at}
    _write(ACCOUNTS_KEY, store)
    return tag


def set_one_click(system: str, account_id: str, on: bool) -> AccountTag:
    """Turn the confirm popup off (or back on) for one account.

    Only for an account that has been tagged: it is a decision about how much
    ceremony a *known* kind of account needs, and there is nothing coherent to
    decide about an account nobody has identified.
    """
    tag = tag_of(system, account_id)
    if tag is None:
        raise LookupError(
            "tag this account demo or live before choosing how it confirms")
    return set_tag(system, account_id, tag.kind, one_click=on)


# --- which contract orders go to ----------------------------------------------


def instrument_root(symbol: str) -> str:
    """The contract's root, which is how a choice outlives its contract month.

    Remembering ``MNQU6`` verbatim would be remembering something that stops
    existing in September. The choice being made is "micros, not minis", so that
    is what is stored, and the session matches it against whatever this
    quarter's front months turn out to be.
    """
    from ..config import root_symbol

    return root_symbol((symbol or "").strip().upper())


def instrument_of(system: str) -> str | None:
    """The root somebody last pointed routing at on this login, or None.

    Qualified by the Rithmic system for the same reason account tags are: two
    logins on one machine are two different sets of entitlements, and a micro
    preference from one has nothing to say about the other.
    """
    v = _read(INSTRUMENTS_KEY).get((system or "").strip())
    if isinstance(v, dict):
        v = v.get("root")
    v = str(v or "").strip().upper()
    return v or None


def set_instrument(system: str, symbol: str) -> str:
    """Remember which contract this login sends to. Stored as the root.

    A preference, not a permission — nothing here can make an order routable,
    and a session applies it only when the contract is one it actually found
    (see ``Broker.attach``). So a stale entry, or an entitlement that has gone
    away, degrades to the feed's own contract rather than to a refusal.
    """
    root = instrument_root(symbol)
    store = _read(INSTRUMENTS_KEY)
    store[(system or "").strip()] = {
        "root": root, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write(INSTRUMENTS_KEY, store)
    return root


# --- the discipline layer -----------------------------------------------------
#
# Everything above this point protects against an *accident*: the wrong account,
# a slipped digit, an order nobody read. What follows protects against a
# decision — a deliberate order that the person placing it will regret, and that
# their own book says loses money.
#
# The levels are not generic prop advice. They come out of
# docs/research/lucidpro-operating-plan.md, which is a fit to 2,538 of this
# trader's own round trips bootstrapped through a $2,000 trailing drawdown, and
# each default below is the number that survived a split-half.


@dataclass(frozen=True)
class Guards:
    """The discipline levels. **Zero disables that one rule**, everywhere.

    A single dataclass rather than seven loose fields because they are read
    together, defaulted together, and — the part that matters — *shown*
    together: a rule you cannot see the level of is a rule you will assume the
    wrong thing about.
    """

    #: Realised dollars down on the day at which the day is over. Latching.
    daily_loss_stop: float = 500.0
    #: Realised dollars up at which the day is over. Off in evaluation (no
    #: consistency rule to poison); the funded stage wants 1000.
    daily_profit_lock: float = 0.0
    #: Dollars down at which entries have to slow to ``min_gap_s`` apart.
    slow_down_at: float = 300.0
    #: The floor on the gap between entries, once past ``slow_down_at``.
    min_gap_s: float = 120.0
    #: Refuse a target tighter than this. Every target at or under 80 ticks is
    #: net-negative on this book, at every stop width that was tried.
    min_target_ticks: int = 100
    #: The stop clamp. 40 ticks was getting noise-stopped; 100 is four losses
    #: deep against a $2,000 drawdown.
    stop_ticks_min: int = 40
    stop_ticks_max: int = 60
    #: Refuse an entry with no stop and no target at all.
    require_bracket: bool = True
    #: When the day crosses ``daily_loss_stop``, close what is open rather than
    #: only refusing the next entry.
    #:
    #: THE STOP IS MEASURED ON EQUITY, NOT ON REALISED, AND THAT IS WHY THIS
    #: EXISTS. A rule that only counted closed trades would sit silent through
    #: an $800 open loss and then refuse the *next* order — which is the one
    #: thing that was never the problem. The account's own drawdown does not
    #: wait for you to book it, so neither does this.
    auto_flatten: bool = True
    #: The most a single entry may put at risk: stop distance x size x the
    #: contract's own dollars-per-tick.
    #:
    #: THIS IS THE RULE ``max_qty`` CANNOT BE. A quantity ceiling assumes it
    #: knows which contract the quantity is of — and 5 contracts on a 50-tick
    #: stop is $125 on MNQ and $1,250 on NQ, ten times apart, with the same
    #: number in the box. The order path takes its symbol from whatever the feed
    #: is on, so "the chart is NQ but the plan was written for MNQ" is a live
    #: hazard rather than a hypothetical. Risk in dollars is the only form of
    #: this rule that is true whatever is being traded, and it is the unit the
    #: operating plan is written in.
    max_risk_usd: float = 250.0
    #: Per side, per contract. The day's running total is measured net of it,
    #: because a $500 stop that ignores its round turns is not a $500 stop.
    #: $3.50 is the mini rate, verified against 479 real fills; a micro is about
    #: $0.50 (a $1.00 round turn, measured off a Lucid fill).
    commission_per_side: float = 3.50


def _num(raw: dict, key: str, default: float, lo: float = 0.0) -> float:
    try:
        v = float(raw.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if v == v and v >= lo else default  # NaN-safe


@dataclass(frozen=True)
class Settings:
    """The knobs that used to be env vars. Defaults are the old defaults."""

    max_qty: int = MAX_QTY_DEFAULT
    guards: Guards = field(default_factory=Guards)


def settings() -> Settings:
    raw = _read(SETTINGS_KEY)
    try:
        qty = int(raw.get("max_qty", MAX_QTY_DEFAULT))
    except (TypeError, ValueError):
        qty = MAX_QTY_DEFAULT
    g = raw.get("guards")
    g = g if isinstance(g, dict) else {}
    d = Guards()
    guards = Guards(
        daily_loss_stop=_num(g, "daily_loss_stop", d.daily_loss_stop),
        daily_profit_lock=_num(g, "daily_profit_lock", d.daily_profit_lock),
        slow_down_at=_num(g, "slow_down_at", d.slow_down_at),
        min_gap_s=_num(g, "min_gap_s", d.min_gap_s),
        min_target_ticks=int(_num(g, "min_target_ticks", d.min_target_ticks)),
        stop_ticks_min=int(_num(g, "stop_ticks_min", d.stop_ticks_min)),
        stop_ticks_max=int(_num(g, "stop_ticks_max", d.stop_ticks_max)),
        require_bracket=bool(g.get("require_bracket", d.require_bracket)),
        auto_flatten=bool(g.get("auto_flatten", d.auto_flatten)),
        max_risk_usd=_num(g, "max_risk_usd", d.max_risk_usd),
        commission_per_side=_num(g, "commission_per_side",
                                 d.commission_per_side),
    )
    return Settings(max_qty=max(1, qty), guards=guards)


def save_settings(max_qty: int | None = None,
                  guards: dict | None = None) -> Settings:
    """Write the order-entry settings. Any argument left None is kept.

    ``guards`` is a *partial* dict — the panel sends the one level being edited
    and the rest stay where they were, so a form that does not know about a
    guard added later cannot silently reset it to the default.
    """
    cur = settings()
    want = {f: getattr(cur.guards, f) for f in Guards.__dataclass_fields__}
    for k, v in (guards or {}).items():
        if k in want and v is not None:
            want[k] = v
    _write(SETTINGS_KEY, {
        "max_qty": max(1, int(cur.max_qty if max_qty is None else max_qty)),
        "guards": want,
    })
    return settings()


# --- may this process route at all -------------------------------------------


@dataclass(frozen=True)
class Policy:
    """The one deployment-level answer, plus the settings it carries around."""

    enabled: bool
    max_qty: int
    #: Is the discipline layer enforced on this machine? ``LIVE_GUARDRAILS``.
    #: Defaults to **True** — see ``_flag_off``.
    guardrails: bool = True
    guards: Guards = field(default_factory=Guards)

    def refusal(self) -> str | None:
        """Why this process may not route, or None if it may. Fails closed."""
        if not self.enabled:
            return ("order routing is switched off — set LIVE_ROUTING=1 to make "
                    "it reachable. Shadow mode does not need it, and paper "
                    "trading on the chart works without it.")
        return None


def policy() -> Policy:
    """Read the routing policy: two env vars and the stored settings.

    ``load_env()`` first, for the same reason ``rithmic.credentials`` does it:
    this repo does not load ``.env`` at import, so a module going straight to
    ``os.getenv`` finds nothing however carefully the file was filled in — and
    reports it as "not configured" rather than as "not read".
    """
    from ..config import load_env

    load_env()
    s = settings()
    return Policy(enabled=_flag("LIVE_ROUTING"),
                  max_qty=s.max_qty,
                  guardrails=not _flag_off("LIVE_GUARDRAILS"),
                  guards=s.guards)


# --- the day, as the guard sees it --------------------------------------------


@dataclass(frozen=True)
class DayState:
    """What the guard needs to know about today. A snapshot, taken by the broker.

    Deliberately a plain value rather than a reference to the ``Broker``: the
    rules below are the part that has to be right, and they are worth testing
    without a market, a socket or a thread — the same reason this whole module
    knows nothing about Rithmic's wire format.
    """

    #: Realised dollars today on this account, **net of commission**, as this
    #: process paired them. See ``Broker._count_day`` for why not the broker's.
    realized: float = 0.0
    #: Why the day is already over, latched. None while it is not.
    locked: str | None = None
    #: Seconds since the last *entry* went out, or None if none has.
    since_entry_s: float | None = None
    trades: int = 0


def _usd(v: float) -> str:
    return f"${abs(v):,.0f}"


def day_refusal(pol: Policy, day: DayState, *, reducing: bool) -> str | None:
    """Why this order may not go out right now, or None if it may.

    **A reducing order is never refused.** That is the first check and it is not
    an optimisation: a discipline rule that could stand between somebody and the
    exit would, at the worst possible moment, be a rule that keeps them in a
    trade. Closing size is always allowed, as is ``Broker.flatten``, which is
    not gated on any of this.
    """
    if reducing or not pol.guardrails:
        return None
    g = pol.guards
    if day.locked:
        return (f"the day is over — {day.locked}. It stays over even if the "
                "running total comes back, which is the entire content of the "
                "rule: 'one more to get back to level' is the trade this "
                "exists to refuse. Flatten and come back tomorrow, or turn "
                "LIVE_GUARDRAILS off in .env if you mean to trade without it.")
    if g.daily_loss_stop and day.realized <= -g.daily_loss_stop:
        return (f"down {_usd(day.realized)} today, past the "
                f"{_usd(g.daily_loss_stop)} daily stop")
    if g.daily_profit_lock and day.realized >= g.daily_profit_lock:
        return (f"up {_usd(day.realized)} today, past the "
                f"{_usd(g.daily_profit_lock)} profit lock — a bigger day is "
                "worth less than it looks once the consistency rule is applied")
    if (g.slow_down_at and g.min_gap_s
            and day.realized <= -g.slow_down_at
            and day.since_entry_s is not None
            and day.since_entry_s < g.min_gap_s):
        wait = g.min_gap_s - day.since_entry_s
        return (f"{wait:.0f}s to wait. Below {_usd(g.slow_down_at)} down, "
                f"entries go no closer than {g.min_gap_s:.0f}s apart. The "
                "measurement this comes from: a bad start slowed down on costs "
                "$147/day, and the same start sped up on costs $803. Volume is "
                "not the problem — speed is.")
    return None


# --- what an order is, before it is one --------------------------------------


@dataclass(frozen=True)
class Intent:
    """A validated order request. Not yet sent, and not yet confirmed."""

    side: str            # buy | sell
    qty: int
    type: str            # market | limit | stop
    price: float | None  # the resting price; None for market
    stop_ticks: int      # 0 for none
    target_ticks: int    # 0 for none
    symbol: str
    exchange: str
    account_id: str
    #: Ticks of profit before Rithmic starts ratcheting the stop. 0 is off.
    #:
    #: There is no second number here and that is Rithmic's model, not a
    #: simplification: the trail rides at ``stop_ticks`` behind the high, so the
    #: ride distance is the stop you already chose and the only free variable is
    #: when it wakes up. Measured on MNQU6 2026-08-10 — a 50-tick stop with a
    #: 25-tick trigger put the first rung 50 ticks under a high of fill+25.
    #: See demo/rithmic_trail_spike.py for the run this was read off.
    trail_trigger_ticks: int = 0
    #: Ticks of profit before Rithmic jumps the stop to a breakeven-plus level.
    #: 0 is off. Fires once, unlike the trail.
    be_trigger_ticks: int = 0
    #: How many ticks of profit that jump locks in. **Always positive here, and
    #: always in the trade's favour** — Rithmic's own field is raw price
    #: arithmetic and needs negating on a sell (measured 2026-08-11: a short with
    #: `break_even_ticks=-3` put the stop 3 ticks below the fill, i.e. 3 in
    #: profit). That negation lives in ``Broker._submit`` and nowhere else,
    #: because a sign convention that leaks into a UI is a sign convention that
    #: is eventually wrong on one side only.
    be_ticks: int = 0

    def sentence(self, kind: str, tick_size: float) -> str:
        """The order in words. This is the confirm popup's entire content.

        Written out rather than shown as fields because the accident this
        guards against is not misreading a number — it is not reading at all.
        A sentence with the account and its kind in it cannot be skimmed as
        "the usual".
        """
        where = "at market" if self.type == "market" else \
            f"on a {self.type} at {self.price:g}"
        parts = [f"{self.side.upper()} {self.qty} {self.symbol} {where}",
                 f"on {kind.upper()} account {self.account_id}"]
        bracket = []
        if self.stop_ticks:
            bracket.append(f"stop {self.stop_ticks} ticks "
                           f"({self.stop_ticks * tick_size:g} pts)")
        if self.target_ticks:
            bracket.append(f"target {self.target_ticks} ticks "
                           f"({self.target_ticks * tick_size:g} pts)")
        parts.append(", ".join(bracket) if bracket
                     else "no stop and no target — this order is unprotected")
        # Both are named in the sentence because they change what the stop *is*:
        # the number above stops being where you get out and becomes only where
        # you start. Somebody re-reading this confirm has to know the exit will
        # move without them.
        if self.be_trigger_ticks:
            parts.append(
                f"stop jumps to lock {self.be_ticks} ticks once "
                f"{self.be_trigger_ticks} ticks in profit")
        if self.trail_trigger_ticks:
            parts.append(
                f"stop trails {self.stop_ticks} ticks behind the high once "
                f"{self.trail_trigger_ticks} ticks in profit")
        if self.be_trigger_ticks or self.trail_trigger_ticks:
            parts.append("Rithmic moves it, not this app")
        return ", ".join(parts) + "."


def build_intent(pol: Policy, *, side: str, qty: int, type: str,
                 price: float | None, stop_ticks: int, target_ticks: int,
                 symbol: str, exchange: str, account_id: str,
                 reducing: bool = False, tick_usd: float = 0.0,
                 trail_trigger_ticks: int = 0,
                 be_trigger_ticks: int = 0, be_ticks: int = 0) -> Intent:
    """Validate a request into an ``Intent``. Raises ``ValueError`` with a reason.

    The quantity cap is checked here rather than at send time so the refusal
    lands on the review, where there is room to say why. Everything else is
    shape: a limit with no price is not a conservative order, it is a bug that
    would reach the exchange as something else.

    THE TWO GROUPS ARE DIFFERENT KINDS OF RULE. Side, type, price, quantity are
    typo-catchers and are checked whatever ``LIVE_GUARDRAILS`` says: there is no
    session in which a naked 40-lot was meant. The bracket rules below it are
    discipline, they are fitted to one trader's book, and they are skipped both
    when the guardrails are off and when the order is *reducing* — closing size
    has no target to be too tight.
    """
    side = (side or "").strip().lower()
    type = (type or "").strip().lower()
    if side not in _SIDES:
        raise ValueError(f"side must be one of {', '.join(_SIDES)}")
    if type not in _TYPES:
        raise ValueError(f"order type must be one of {', '.join(_TYPES)}")
    if qty < 1:
        raise ValueError("quantity must be at least 1")
    if qty > pol.max_qty:
        raise ValueError(
            f"quantity {qty} is over this app's ceiling of {pol.max_qty} — "
            "raise it in the order-entry settings if you meant it. The "
            "account's own risk limits are a separate and later thing; this one "
            "is here to catch a slipped digit.")
    if type == "market":
        price = None
    elif price is None or not (price == price) or price <= 0:  # NaN-safe
        raise ValueError(f"a {type} order needs a price")
    if min(stop_ticks, target_ticks, trail_trigger_ticks,
           be_trigger_ticks, be_ticks) < 0:
        raise ValueError("bracket distances are in ticks and cannot be negative")
    if be_trigger_ticks and not stop_ticks:
        raise ValueError(
            "a breakeven stop needs a stop to move: there is no leg to jump "
            "without one.")
    if be_ticks and not be_trigger_ticks:
        raise ValueError(
            "a breakeven lock with no trigger never fires — say how far in "
            "profit it should move, or set the lock back to 0.")
    if be_trigger_ticks and be_ticks < 1:
        # Not a preference either. `break_even_ticks` is a proto3 singular
        # scalar, so a zero is indistinguishable from unset and never reaches
        # the wire — asking to move the stop exactly to the fill is byte-for-byte
        # the same request as asking for no offset at all, and what Rithmic then
        # does is undefined rather than "breakeven". One tick is the nearest
        # thing that can actually be said.
        raise ValueError(
            "a breakeven lock has to be at least 1 tick. Zero cannot be sent — "
            "Rithmic's field is a proto3 scalar and a zero is dropped as though "
            "it were never set, so 'exactly at the fill' is not expressible and "
            "would leave the level undefined.")
    if trail_trigger_ticks and not stop_ticks:
        # Not a preference. The trail rides at the stop's distance, so without a
        # stop there is no distance to ride at — Rithmic would take the order
        # and trail nothing, which is the silent-no-op this whole path exists to
        # avoid.
        raise ValueError(
            "a trailing stop needs a stop to trail: Rithmic rides it at the "
            "stop's own distance behind the high, so there is nothing to "
            "measure from without one.")
    if not account_id or account_id == PAPER:
        raise ValueError("no real account to send to")
    if pol.guardrails and not reducing:
        _check_shape(pol.guards, stop_ticks, target_ticks, qty, tick_usd)
    return Intent(side=side, qty=int(qty), type=type,
                  price=None if price is None else float(price),
                  stop_ticks=int(stop_ticks), target_ticks=int(target_ticks),
                  symbol=symbol, exchange=exchange, account_id=account_id,
                  trail_trigger_ticks=int(trail_trigger_ticks),
                  be_trigger_ticks=int(be_trigger_ticks),
                  be_ticks=int(be_ticks))


def _check_shape(g: Guards, stop_ticks: int, target_ticks: int,
                 qty: int = 1, tick_usd: float = 0.0) -> None:
    """The bracket a new position is allowed to open with. Raises ``ValueError``.

    Every refusal names the measurement behind it. A rule whose reason is not on
    screen at the moment it refuses is a rule that gets switched off, and the
    honest thing to compete with the urge to override is the number.
    """
    if g.require_bracket and not (stop_ticks and target_ticks):
        missing = "stop" if not stop_ticks else "target"
        raise ValueError(
            f"this entry has no {missing}. Every entry goes out bracketed — "
            f"{g.stop_ticks_min}–{g.stop_ticks_max} tick stop, "
            f"{g.min_target_ticks}+ tick target — and an unprotected position "
            "on a trailing-drawdown account is the one mistake that ends it in "
            "a single move.")
    if g.min_target_ticks and target_ticks and target_ticks < g.min_target_ticks:
        raise ValueError(
            f"a {target_ticks}-tick target is under the {g.min_target_ticks}-"
            "tick floor. Re-simulated tick by tick on 249 real entries, every "
            "fixed target at or under 80 ticks is net-negative — 40 ticks loses "
            "$1,599, 20 loses $4,599 — and it stays negative against a 100-tick "
            "stop, so it is the absolute distance and not the ratio. 120 ticks "
            "is the number that made money.")
    if stop_ticks and g.stop_ticks_min and stop_ticks < g.stop_ticks_min:
        raise ValueError(
            f"a {stop_ticks}-tick stop is tighter than the "
            f"{g.stop_ticks_min}-tick floor. A 40-tick stop was getting "
            "noise-stopped in the flat half of the sample; widening it is what "
            "made the edge replicate across both halves rather than only the "
            "trending one.")
    if stop_ticks and g.stop_ticks_max and stop_ticks > g.stop_ticks_max:
        raise ValueError(
            f"a {stop_ticks}-tick stop is wider than the "
            f"{g.stop_ticks_max}-tick ceiling. If the setup really needs more "
            "room, take fewer contracts — the risk per trade is a dollar "
            "amount and the drawdown that ends the account is fixed in "
            "dollars, so a wider stop at the same size is simply fewer losses "
            "until it is over.")
    if g.max_risk_usd and stop_ticks and tick_usd > 0:
        risk = stop_ticks * tick_usd * qty
        if risk > g.max_risk_usd:
            raise ValueError(
                f"this order risks ${risk:,.0f} — {stop_ticks} ticks x {qty} at "
                f"${tick_usd:,.2f} a tick — against a ${g.max_risk_usd:,.0f} "
                "ceiling. Check the contract before the quantity: the order "
                "goes out on whatever the chart is on, and the same 5 on the "
                "same 50-tick stop is $125 of micros or $1,250 of minis. On a "
                "$2,000 trailing drawdown that is the difference between "
                "sixteen losses and one and a half.")


# --- the review ---------------------------------------------------------------


class Confirms:
    """Reviewed orders waiting to be sent. Single-use and short-lived.

    The store is what makes the confirm step structural on the accounts that
    want it. A send takes a token and nothing else about the order — so an
    intent that was never rendered as a sentence has no token, and a caller
    cannot construct one. One-click is the deliberate exception and takes a
    different door (``Broker.send_now``), which is refused unless the account
    has the flag set.

    **Emptied by every change of situation**, which is the only invalidation
    this needs: a disconnect, an account switch, an instrument switch and the
    18:00 roll all call ``clear``. A token describes an order on one account, on
    one contract, at a price that was true a moment ago — none of those survive
    the thing that emptied the store, so there is nothing to carry across.
    """

    def __init__(self, ttl_s: float = PREVIEW_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._staged: dict[str, tuple[Intent, float]] = {}

    def stage(self, intent: Intent, now: float | None = None) -> tuple[str, float]:
        """Mint a token for ``intent``. Returns (token, seconds valid)."""
        now = time.monotonic() if now is None else now
        self._sweep(now)
        token = secrets.token_urlsafe(12)
        self._staged[token] = (intent, now + self.ttl_s)
        return token, self.ttl_s

    def consume(self, token: str, now: float | None = None) -> Intent:
        """Spend a token. Raises ``LookupError`` with why it is not spendable."""
        now = time.monotonic() if now is None else now
        self._sweep(now)
        found = self._staged.pop(token, None)
        if found is None:
            raise LookupError(
                "that order was not reviewed, or the review has expired or "
                "already been sent. Review it again — the price will have moved.")
        return found[0]

    def clear(self) -> None:
        self._staged.clear()

    def _sweep(self, now: float) -> None:
        for k in [k for k, (_, exp) in self._staged.items() if exp <= now]:
            self._staged.pop(k, None)
