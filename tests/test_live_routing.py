"""Phase 7: order routing, and every place it is supposed to refuse.

This is the only code path in the repo that can reach an exchange, so what these
tests guard is almost entirely the *no* side. The happy path is four calls long
and would be obvious if it broke; the failures that matter are the ones where
something sends when it should not have, or reports flat when it does not know.

The model these are written against: **paper is an account.** It sits in the same
selector as the Rithmic ones, it is what every session starts on, and what
changes between accounts is not the capability but the ceremony — a confirm
popup by default, skippable per account with one-click trading. So the things
worth pinning are:

  - paper is the default and stays unable to send, in every lifecycle event;
  - **untagged is not demo** — the rule that outlived ``RITHMIC_ENV``, now
    enforced against a per-account label instead of a process-wide env var;
  - one-click is per account, off by default, and **cleared when an account is
    tagged live** — the accident being guarded is enabling it on practice and
    inheriting it on real money;
  - the review token: single-use, short-lived, and dropped by every change of
    situation — a disconnect, an account switch, an instrument switch, the roll;
  - reconciliation, and the difference between "nothing is working" and "we have
    not asked", which look identical on screen and only one of which is safe;
  - the kill switch working with **nothing but a connection** behind it, and
    reporting a partial failure as a failure.

There is no arm. There was one — a typed confirmation with a fifteen-minute
lease — and what it actually enforced is now ``Broker.check_routable``, checked
on every order instead of once per lease. The tests that used to pin the lease
now pin the four standing facts it sat on top of.

The broker is driven against a fake Rithmic client on a real event loop in a
real second thread, because that is the arrangement in production — the plants
live on the feed's loop and the endpoints call in from FastAPI's threadpool, and
a test that ran both on one thread would deadlock on the very hand-off
``Broker._call`` exists to make.

**The tag store is patched out for every test** (``store`` is autouse). These
tests must never write the real settings table: a stray ``live`` label on a real
account id is exactly the kind of thing that would then be believed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.live import broker as brokermod  # noqa: E402
from journal.live import routing as rt  # noqa: E402
from journal.live.broker import Broker  # noqa: E402
from journal.live.routing import PAPER  # noqa: E402

CONTRACT = "NQU6"
MICRO = "MNQU6"
DAY = date(2026, 8, 6)
SYS = "TestSystem"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Every side effect of a Broker, pointed somewhere disposable.

    Autouse and non-negotiable. Three things a Broker touches outside itself,
    and **all three default to the real ones**:

      - the settings store (account tags) — a stray ``live`` label written
        against a real account id is exactly the kind of thing that would then
        be believed;
      - the order journal on disk;
      - **``journal.db``**, because a closed round trip now books itself into
        the real trading record. That one was learned the hard way: an earlier
        version of this fixture did not patch it, and the fill-pairing tests
        below wrote eight fabricated trades into the real journal tagged
        ``mode='live'``, where they would have counted toward real statistics.

    ``db.connect`` is redirected rather than ``booking.book_trade`` stubbed, so
    the booking really runs — the tests exercise the path, they just cannot
    reach the user's data with it.
    """
    box: dict[str, dict] = {}
    # Round-tripped through JSON so the tests see what the DB would give back,
    # not the objects they put in.
    monkeypatch.setattr(rt, "_read", lambda k: json.loads(json.dumps(box.get(k, {}))))
    monkeypatch.setattr(rt, "_write",
                        lambda k, v: box.__setitem__(k, json.loads(json.dumps(v))))
    monkeypatch.setattr(brokermod, "LIVE_ORDER_DIR", tmp_path / "orders")

    from journal import db as dbmod

    real_connect = dbmod.connect
    scratch = tmp_path / "journal.db"

    def _scratch_connect(path=None):
        conn = real_connect(scratch)
        conn.executescript(dbmod.SCHEMA)
        return conn

    monkeypatch.setattr(dbmod, "connect", _scratch_connect)
    return box


def test_the_fixture_really_does_keep_the_real_journal_out_of_reach():
    """The guard on the guard. If `store` ever stops redirecting `db.connect`,
    every fill-pairing test below silently starts writing fabricated trades into
    the real journal — which is how this was found the first time."""
    from journal import db as dbmod
    from journal.config import DATA_DIR

    conn = dbmod.connect()
    try:
        (path,) = [r[2] for r in conn.execute("PRAGMA database_list")
                   if r[1] == "main"]
    finally:
        conn.close()
    assert not Path(path).is_relative_to(DATA_DIR), path


def _policy(**over) -> rt.Policy:
    """A routing policy for the gate tests. **Guardrails off unless asked for.**

    The discipline layer has its own section at the end of this file. Everything
    before it is about a *permission* — the tag, the token, the kill switch —
    and the answer to those must not depend on whether today is a good day.
    Leaving the guardrails on here would mean every order in the file needed a
    50/120 bracket in order to say anything about the gates, which is the
    coupling that makes a suite stop being read.
    """
    base = dict(enabled=True, max_qty=5, guardrails=False)
    base.update(over)
    return rt.Policy(**base)


# --- the one env gate ---------------------------------------------------------


def test_routing_is_off_until_it_is_switched_on():
    r = _policy(enabled=False).refusal()
    assert r and "LIVE_ROUTING" in r
    # And it says paper still works, because the honest reading of "routing is
    # off" is "you can still practise", not "this page is broken".
    assert "paper" in r.lower()
    assert _policy().refusal() is None


def test_the_policy_reads_the_environment_and_fails_closed(monkeypatch):
    from journal import config as cfgmod

    monkeypatch.setattr(cfgmod, "load_env", lambda: None)
    monkeypatch.delenv("LIVE_ROUTING", raising=False)
    assert rt.policy().refusal() is not None

    monkeypatch.setenv("LIVE_ROUTING", "1")
    assert rt.policy().refusal() is None


def test_the_settings_that_used_to_be_env_vars_round_trip():
    assert rt.settings().max_qty == rt.MAX_QTY_DEFAULT
    rt.save_settings(max_qty=2)
    assert rt.settings().max_qty == 2
    # A floor rather than an error: a nonsense value should not be able to leave
    # the app unable to send anything at all.
    rt.save_settings(max_qty=0)
    assert rt.settings().max_qty == 1


# --- the tags -----------------------------------------------------------------


def test_a_tag_is_scoped_to_the_login_that_owns_the_account():
    """An account id is only unique within a login. Two firms can both have an
    APEX-1234, and a tag following the id alone would label the wrong one."""
    rt.set_tag("FirmA", "1234", "demo")
    assert rt.tag_of("FirmA", "1234").kind == "demo"
    assert rt.tag_of("FirmB", "1234") is None


def test_tagging_an_account_live_clears_one_click():
    """The accident this closes: you enable one-click on practice because
    confirming every order is friction, then that tag becomes live and the fast
    path silently follows onto real money."""
    rt.set_tag(SYS, "A1", "demo")
    rt.set_one_click(SYS, "A1", True)
    assert rt.tag_of(SYS, "A1").one_click is True

    rt.set_tag(SYS, "A1", "live")
    assert rt.tag_of(SYS, "A1").one_click is False

    # Re-confirming an account that is *already* live leaves the choice alone —
    # it is only the promotion that resets it.
    rt.set_one_click(SYS, "A1", True)
    rt.set_tag(SYS, "A1", "live")
    assert rt.tag_of(SYS, "A1").one_click is True


def test_one_click_needs_a_tag_first():
    with pytest.raises(LookupError, match="tag this account"):
        rt.set_one_click(SYS, "A1", True)


def test_a_corrupt_store_reads_as_untagged_rather_than_as_demo(monkeypatch):
    monkeypatch.setattr(rt, "_read", lambda k: (_ for _ in ()).throw(AssertionError)
                        if False else {})
    assert rt.tag_of(SYS, "A1") is None
    # And a junk value for a key is dropped, not coerced.
    monkeypatch.setattr(rt, "_read", lambda k: {rt.account_key(SYS, "A1"):
                                                {"kind": "probably demo"}})
    assert rt.tag_of(SYS, "A1") is None


# --- what an order is allowed to be -----------------------------------------


def _intent(**over) -> rt.Intent:
    base = dict(side="buy", qty=1, type="market", price=None, stop_ticks=40,
                target_ticks=80, symbol=CONTRACT, exchange="CME",
                account_id="DEMO1")
    base.update(over)
    return rt.build_intent(_policy(), **base)


def test_a_slipped_digit_is_caught_before_the_review_is_even_rendered():
    with pytest.raises(ValueError, match="ceiling"):
        _intent(qty=50)
    assert _intent(qty=5).qty == 5


def test_a_resting_order_without_a_price_is_refused_rather_than_softened():
    """It would otherwise reach the exchange as something else."""
    for kind in ("limit", "stop"):
        with pytest.raises(ValueError, match="needs a price"):
            _intent(type=kind, price=None)
    assert _intent(type="limit", price=20000.0).price == 20000.0
    assert _intent(type="market", price=20000.0).price is None


def test_paper_is_not_somewhere_an_order_can_be_sent():
    with pytest.raises(ValueError, match="no real account"):
        _intent(account_id=PAPER)


def test_the_sentence_names_the_account_its_kind_and_the_absence_of_a_stop():
    s = _intent(side="sell", qty=2).sentence("demo", 0.25)
    assert "SELL 2 NQU6 at market" in s
    assert "DEMO account DEMO1" in s
    assert "stop 40 ticks (10 pts)" in s and "target 80 ticks (20 pts)" in s
    # The one an operator most needs told, and the one a fields-and-boxes ticket
    # says by showing two empty inputs.
    assert "unprotected" in _intent(stop_ticks=0, target_ticks=0).sentence("demo", 0.25)


# --- the review ---------------------------------------------------------------


def test_a_review_is_single_use_short_lived_and_cleared_by_a_change():
    c = rt.Confirms(ttl_s=30.0)
    intent = _intent()
    token, ttl = c.stage(intent)
    assert ttl == 30.0

    # Spending it twice is not a retry; it is a second order.
    assert c.consume(token) == intent
    with pytest.raises(LookupError, match="already been sent"):
        c.consume(token)

    # A price that was true a minute ago describes a different order.
    token, _ = c.stage(intent)
    with pytest.raises(LookupError):
        c.consume(token, now=time.monotonic() + 31)

    # And every change of situation empties the store outright — the account
    # switch, the instrument switch, the disconnect and the roll all call this.
    token, _ = c.stage(intent)
    c.clear()
    with pytest.raises(LookupError):
        c.consume(token)


# --- the fake plant ----------------------------------------------------------


class _Ev:
    """The `+=` half of pattern_kit's Event — all the broker uses."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


def _order(basket_id="B1", **over):
    base = dict(basket_id=basket_id, user_tag="", symbol=CONTRACT, notify_type=1,
                status="working", transaction_type=1, price_type=1, quantity=1,
                price=20000.0, trigger_price=0.0, total_fill_size=0,
                total_unfilled_size=1, avg_fill_price=0.0, text="")
    base.update(over)
    return SimpleNamespace(**base)


def _pnl(net=0, **over):
    base = dict(symbol=CONTRACT, net_quantity=net, avg_open_fill_price=20000.0,
                open_position_pnl=0.0, day_pnl=0.0)
    base.update(over)
    return SimpleNamespace(**base)


class _FakePlant:
    """The order plant, to the depth `_patch_order_plant` reaches into it.

    Real enough to be worth having: the trailing bracket is injected by wrapping
    ``_send_and_collect``, so a fake that skipped it would leave the one piece of
    the order path that async_rithmic cannot do untested.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, dict]] = []

    async def _send_and_collect(self, template_id, **kwargs):
        self.sent.append((template_id, dict(kwargs)))
        return [SimpleNamespace(basket_id=f"B{len(self.sent)}")]


class FakeClient:
    """Enough of ``RithmicClient`` for the order path, and nothing else."""

    def __init__(self, accounts=("DEMO1",), orders=(), positions=()) -> None:
        self.plants = {"order": _FakePlant()}
        self.accounts = [SimpleNamespace(account_id=a) for a in accounts]
        self.on_exchange_order_notification = _Ev()
        self.on_rithmic_order_notification = _Ev()
        self.on_instrument_pnl_update = _Ev()
        self._orders = list(orders)
        self._positions = list(positions)
        self.submitted: list[dict] = []
        self.cancelled: list[dict] = []
        self.modified: list[dict] = []
        self.cancel_alls = 0
        self.exits = 0
        self.subscribed = False
        self.fail_cancel_all = False
        self.fail_exit = False
        self.hang = False
        #: What `get_front_month_contract` answers, per root. None models a
        #: login with no micro entitlement, which must not be an outage.
        self.front_months: dict[str, str | None] = {"MNQ": MICRO}

    async def subscribe_to_pnl_updates(self):
        self.subscribed = True

    async def get_front_month_contract(self, root, exchange):
        return self.front_months.get(root)

    async def list_orders(self, **kw):
        return self._orders

    async def list_positions(self, **kw):
        return self._positions

    async def submit_order(self, **kw):
        """Mirrors `plants/order.py`'s dispatch as far as the bracket decision.

        Specifically the two things the trailing injection depends on: a bracket
        goes out as template 330 with a ``*_STATIC`` type, and ``order_id`` is
        carried on as ``user_tag`` — which is the key the broker registers each
        order's trailing fields against.
        """
        if self.hang:
            await asyncio.sleep(60)
        self.submitted.append(kw)
        from async_rithmic.protocol_buffers.request_bracket_order_pb2 import (
            RequestBracketOrder as _R,
        )

        bt = _R.BracketType
        msg = {"user_tag": kw.get("order_id"), "symbol": kw.get("symbol"),
               "quantity": kw.get("qty")}
        template = 312
        if "stop_ticks" in kw:
            template = 330
            msg["stop_ticks"] = kw["stop_ticks"]
            msg["bracket_type"] = bt.STOP_ONLY_STATIC
        if "target_ticks" in kw:
            template = 330
            msg["target_ticks"] = kw["target_ticks"]
            msg["bracket_type"] = (bt.TARGET_AND_STOP_STATIC if "stop_ticks" in kw
                                   else bt.TARGET_ONLY_STATIC)
        return await self.plants["order"]._send_and_collect(template, **msg)

    async def cancel_order(self, **kw):
        self.cancelled.append(kw)
        return [SimpleNamespace(basket_id=kw.get("basket_id"))]

    async def modify_order(self, **kw):
        self.modified.append(kw)
        return [SimpleNamespace(basket_id=kw.get("basket_id"))]

    async def cancel_all_orders(self, **kw):
        if self.fail_cancel_all:
            raise RuntimeError("rejected")
        self.cancel_alls += 1

    async def exit_position(self, **kw):
        if self.fail_exit:
            raise RuntimeError("no route")
        self.exits += 1


class _Loop:
    """An event loop in its own thread — production's arrangement, not a mock."""

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        return self

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=10)

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


def _broker(**kw) -> Broker:
    return Broker(CONTRACT, "CME", DAY, kw.pop("policy", _policy()),
                  system=SYS, **kw)


@pytest.fixture
def wired():
    """A broker attached to a fake plant, switched to a tagged demo account."""
    with _Loop() as loop:
        b = _broker()
        c = FakeClient()
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        yield b, c, loop


# --- paper is the default ----------------------------------------------------


def test_a_session_opens_on_paper_however_few_accounts_there_are():
    """Not "the only account", not "the last one" — paper, every time.

    A single-account login is the tempting case to auto-select, and it is the
    one where auto-selecting is worst: there is nothing to notice.
    """
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient(accounts=("ONLY1",))))
        assert b.account_id == PAPER and b.paper
        assert not b.ready              # paper has no broker state to be ready about
        assert [a["id"] for a in b.accounts_view()] == [PAPER, "ONLY1"]


def test_paper_cannot_send_or_be_flattened(wired):
    b, c, _ = wired
    b.use_account(PAPER)
    assert not b.routable
    with pytest.raises(ValueError, match="cannot reach a broker"):
        b.check_routable()
    with pytest.raises(PermissionError, match="cannot reach a broker"):
        b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    with pytest.raises(LookupError, match="nothing at a broker"):
        b.flatten()
    assert c.cancel_alls == 0 and c.exits == 0


def test_switching_to_paper_needs_no_round_trip(wired):
    """There is nothing at a broker to ask about, and the page's own blotter is
    waiting where it was left."""
    b, c, _ = wired
    before = len(c._orders)
    b.use_account(PAPER)
    assert b.paper and b.reconciled_at is None and len(c._orders) == before


# --- the tag gate -------------------------------------------------------------


def test_an_untagged_account_cannot_send():
    """The rule that outlived RITHMIC_ENV. Untagged is not demo."""
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient(accounts=("NEW1",))))
        b.use_account("NEW1")
        assert b.tag is None and not b.routable
        with pytest.raises(ValueError, match="not been labelled"):
            b.check_routable()
        with pytest.raises(PermissionError, match="not been labelled"):
            b.preview(side="buy", qty=1, type="market", price=None,
                      stop_ticks=0, target_ticks=0)

        rt.set_tag(SYS, "NEW1", "demo")
        assert b.routable              # and now it can


def test_an_account_that_has_not_been_read_back_cannot_send():
    """Sending against a picture this process made up is the thing `reconcile`
    exists to prevent, and it is a gate rather than a note on the panel."""
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient()))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        assert b.routable
        # What a disconnect leaves behind: still on the account, still tagged,
        # and no longer entitled to believe anything about the book.
        b.detach()
        assert b.reconciled_at is None and not b.routable
        with pytest.raises(ValueError, match="not connected"):
            b.check_routable()


def test_the_gate_is_read_on_every_order_rather_than_once(wired):
    """What replaced the arm's lease. Nothing is granted for a while: the four
    facts are re-read for each call, so losing one stops the very next order."""
    b, c, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.detach()
    with pytest.raises(PermissionError):
        b.send(p["token"])
    assert c.submitted == []


# --- sending -----------------------------------------------------------------


def test_an_order_has_to_be_reviewed_before_it_can_be_sent(wired):
    b, c, _ = wired

    with pytest.raises(LookupError):
        b.send("made-up-token")
    assert c.submitted == []

    p = b.preview(side="sell", qty=2, type="limit", price=20010.0,
                  stop_ticks=40, target_ticks=80)
    assert "SELL 2 NQU6" in p["sentence"] and "DEMO account DEMO1" in p["sentence"]
    assert c.submitted == []           # the review sends nothing

    out = b.send(p["token"])
    assert out["basket_id"] == "B1" and out["how"] == "review"
    (sent,) = c.submitted
    assert sent["qty"] == 2 and sent["price"] == 20010.0
    assert sent["stop_ticks"] == 40 and sent["target_ticks"] == 80
    assert sent["symbol"] == CONTRACT and sent["account_id"] == "DEMO1"


def test_one_click_is_refused_until_this_account_turns_it_on(wired):
    """The whole point of the flag being per account: "send an unreviewed order"
    is not a shape that exists until somebody asks for it, here."""
    b, c, _ = wired
    with pytest.raises(PermissionError, match="one-click trading is off"):
        b.send_now(side="buy", qty=1, type="market", price=None,
                   stop_ticks=0, target_ticks=0)
    assert c.submitted == []

    rt.set_one_click(SYS, "DEMO1", True)
    out = b.send_now(side="buy", qty=1, type="market", price=None,
                     stop_ticks=0, target_ticks=0)
    assert out["how"] == "one_click" and len(c.submitted) == 1


def test_one_click_still_obeys_the_gates_and_the_quantity_ceiling(wired):
    b, c, _ = wired
    rt.set_one_click(SYS, "DEMO1", True)
    # On paper: the fast path is faster, not freer.
    b.use_account(PAPER)
    with pytest.raises(PermissionError):
        b.send_now(side="buy", qty=1, type="market", price=None,
                   stop_ticks=0, target_ticks=0)
    b.use_account("DEMO1")
    with pytest.raises(ValueError, match="ceiling"):
        b.send_now(side="buy", qty=99, type="market", price=None,
                   stop_ticks=0, target_ticks=0)
    assert c.submitted == []


def test_how_an_order_went_out_is_on_disk(wired):
    """"Was this one-click" is the first question anyone asks about a fill they
    did not expect, so it cannot be a thing only the UI knew."""
    b, _, _ = wired
    rt.set_one_click(SYS, "DEMO1", True)
    b.send_now(side="buy", qty=1, type="market", price=None,
               stop_ticks=0, target_ticks=0)
    lines = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
    assert any(x["event"] == "submitted" and x["how"] == "one_click" for x in lines)


def test_switching_account_invalidates_an_order_already_staged(wired):
    """The sentence named an account. Carried across a switch it would describe
    an order on a balance nobody reviewed."""
    b, c, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.use_account(PAPER)
    b.use_account("DEMO1")
    with pytest.raises(LookupError):
        b.send(p["token"])
    assert c.submitted == []


def test_nothing_sends_from_paper(wired):
    b, c, _ = wired
    b.use_account(PAPER)
    for call in (lambda: b.preview(side="buy", qty=1, type="market", price=None,
                                   stop_ticks=0, target_ticks=0),
                 lambda: b.send("t"),
                 lambda: b.cancel("B1")):
        with pytest.raises(PermissionError):
            call()
    assert c.submitted == [] and c.cancelled == []


def test_a_plant_that_does_not_answer_says_the_outcome_is_unknown():
    """The one failure where "did it go?" is genuinely unanswerable from here,
    and the message has to say so rather than pick a side."""
    with _Loop() as loop:
        b = _broker()
        c = FakeClient()
        c.hang = True
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        p = b.preview(side="buy", qty=1, type="market", price=None,
                      stop_ticks=0, target_ticks=0)
        brokermod.CALL_TIMEOUT_S = 0.2
        try:
            with pytest.raises(TimeoutError, match="may or may not"):
                b.send(p["token"])
        finally:
            brokermod.CALL_TIMEOUT_S = 20.0


# --- attaching and reconciling ----------------------------------------------


def test_choosing_an_account_asks_what_is_working_before_anything_is_drawn():
    """The restart case, which is the whole reason `reconcile` exists.

    A process showing no orders and no position *because it had not asked* is
    indistinguishable from one showing the truth — and the difference is a live
    position nobody is watching.
    """
    with _Loop() as loop:
        b = _broker()
        c = FakeClient(orders=[_order("B7", quantity=3)], positions=[_pnl(net=-2)])
        loop.run(b.attach(c))
        assert c.subscribed
        assert b.reconciled_at is None      # on paper, nothing has been asked

        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        assert b.ready and b.reconciled_at is not None
        assert list(b.working) == ["B7"] and b.working["B7"]["qty"] == 3
        assert b.position["net"] == -2


def test_a_finished_order_found_on_reconnect_is_not_listed_as_working():
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient(orders=[
            _order("B1"),
            _order("B2", notify_type=3, status="cancelled", total_unfilled_size=0),
        ])))
        b.use_account("DEMO1")
        assert list(b.working) == ["B1"]
        assert [o["basket_id"] for o in b.recent] == ["B2"]


def test_a_login_with_no_accounts_is_an_error_not_a_quiet_paper_session():
    with _Loop() as loop:
        b = _broker()
        with pytest.raises(LookupError, match="no accounts"):
            loop.run(b.attach(FakeClient(accounts=())))


def test_switching_account_drops_the_review_and_re_reads():
    """The sentence named one balance; it cannot be sent against another."""
    with _Loop() as loop:
        b = _broker()
        c = FakeClient(accounts=("DEMO1", "DEMO2"), orders=[_order("B1")])
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        rt.set_tag(SYS, "DEMO2", "demo")

        b.use_account("DEMO1")
        assert b.ready and list(b.working) == ["B1"]
        p = b.preview(side="buy", qty=1, type="market", price=None,
                      stop_ticks=0, target_ticks=0)

        c._orders = [_order("B9")]
        b.use_account("DEMO2")

        assert b.account_id == "DEMO2"
        assert list(b.working) == ["B9"]        # the new account's, re-read
        with pytest.raises(LookupError):        # and the staged order is gone
            b.send(p["token"])
        assert c.submitted == []


def test_switching_to_an_account_this_login_does_not_have_is_refused():
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient(accounts=("DEMO1", "DEMO2"))))
        with pytest.raises(LookupError, match="not one of"):
            b.use_account("NOPE")


def test_another_accounts_orders_and_position_are_not_this_ones():
    """The bug that only exists once the account can be switched.

    `subscribe_to_pnl_updates` subscribes for **every** account on the login and
    the order stream is per-login too, so without an account filter a position
    held elsewhere lands in `self.position` and the panel calls it this one's.
    """
    with _Loop() as loop:
        b = _broker()
        c = FakeClient(accounts=("DEMO1", "DEMO2"))
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        (on_order,) = c.on_exchange_order_notification.handlers
        (on_pnl,) = c.on_instrument_pnl_update.handlers

        loop.run(on_order(_order("B2", account_id="DEMO2")))
        loop.run(on_pnl(_pnl(net=7, account_id="DEMO2")))
        assert b.working == {} and b.position is None

        loop.run(on_order(_order("B1", account_id="DEMO1")))
        loop.run(on_pnl(_pnl(net=2, account_id="DEMO1")))
        assert list(b.working) == ["B1"] and b.position["net"] == 2

        # A message that names no account is ours: some notifications omit it,
        # and dropping those would lose real fills.
        loop.run(on_order(_order("B3", account_id="")))
        assert "B3" in b.working


# --- notifications -----------------------------------------------------------


def test_the_broker_is_the_authority_on_what_is_working(wired):
    b, c, loop = wired
    (handler,) = c.on_exchange_order_notification.handlers

    loop.run(handler(_order("B9", quantity=2)))
    assert list(b.working) == ["B9"]

    # Part-filled is still working — the remainder can still be cancelled.
    loop.run(handler(_order("B9", notify_type=5, quantity=2, total_fill_size=1,
                            total_unfilled_size=1)))
    assert b.working["B9"]["filled"] == 1

    loop.run(handler(_order("B9", notify_type=5, quantity=2, total_fill_size=2,
                            total_unfilled_size=0)))
    assert b.working == {} and b.recent[0]["basket_id"] == "B9"


def test_a_rejection_leaves_nothing_working_and_keeps_the_reason(wired):
    b, c, loop = wired
    (handler,) = c.on_exchange_order_notification.handlers
    loop.run(handler(_order("B3")))
    loop.run(handler(_order("B3", notify_type=6, status="rejected",
                            total_unfilled_size=0, text="margin")))
    assert b.working == {} and b.recent[0]["text"] == "margin"


def test_the_price_that_does_not_apply_to_an_orders_kind_is_none_not_zero(wired):
    """Rithmic sends every price field on every order: a limit carries
    ``trigger_price=0.0`` and a stop carries ``price=0.0``. Passed on as the zero
    it stops being "no" and becomes a price — and a consumer coalescing the two
    (``trigger_price ?? price``) then reads a working limit as resting at zero
    and drops it off the chart. That is not hypothetical: on 2026-08-11 a live
    buy limit on MNQU6 was invisible on /live while it filled."""
    b, c, loop = wired
    (handler,) = c.on_exchange_order_notification.handlers
    loop.run(handler(_order("LMT", price_type=1, price=20000.0, trigger_price=0.0)))
    loop.run(handler(_order("STP", price_type=4, price=0.0, trigger_price=19950.0)))
    lmt, stp = b.working["LMT"], b.working["STP"]
    assert lmt["price"] == 20000.0 and lmt["trigger_price"] is None
    assert stp["trigger_price"] == 19950.0 and stp["price"] is None
    # Whichever kind, the one resting price is found by the same coalesce.
    for o in (lmt, stp):
        assert (o["trigger_price"] or o["price"]) not in (None, 0.0)


def test_a_working_order_carries_the_bracket_it_was_sent_with(wired):
    """Rithmic attaches the legs when the entry fills and says nothing about them
    before that, so without this a resting entry is drawn as a bare line — the
    chart under-reporting risk that has already been placed."""
    b, c, loop = wired
    rt.set_one_click(SYS, "DEMO1", True)
    b.send_now(side="buy", qty=1, type="limit", price=20000.0,
               stop_ticks=40, target_ticks=80)
    (sent,) = c.submitted
    (handler,) = c.on_exchange_order_notification.handlers
    loop.run(handler(_order("B1", user_tag=sent["order_id"], price=20000.0)))
    rec = b.working["B1"]
    assert rec["stop_ticks"] == 40 and rec["target_ticks"] == 80
    # An order this process did not send has no bracket to promise.
    loop.run(handler(_order("B2", price=20010.0)))
    assert b.working["B2"]["stop_ticks"] == 0


def test_the_promised_bracket_is_forgotten_once_the_order_is_done(wired):
    """Once it has filled the legs are working orders of their own. A remembered
    intention left lying about would be a second, staler answer to where they
    are."""
    b, c, loop = wired
    rt.set_one_click(SYS, "DEMO1", True)
    b.send_now(side="buy", qty=1, type="limit", price=20000.0,
               stop_ticks=40, target_ticks=0)
    tag = c.submitted[0]["order_id"]
    (handler,) = c.on_exchange_order_notification.handlers
    loop.run(handler(_order("B1", user_tag=tag, price=20000.0)))
    loop.run(handler(_order("B1", user_tag=tag, notify_type=5, total_fill_size=1,
                            total_unfilled_size=0)))
    assert b.working == {} and b.recent[0]["stop_ticks"] == 40
    # A later message on the same tag — a leg echoing it, say — inherits nothing.
    loop.run(handler(_order("B9", user_tag=tag, price=19990.0)))
    assert b.working["B9"]["stop_ticks"] == 0


def test_a_modify_is_sized_by_the_remainder_when_quantity_comes_back_zero(wired):
    """Some notifications leave `quantity` at 0 and describe the order only by
    what has filled and what has not (MNQU6, 2026-08-11). A modify sent with
    quantity 0 is a modify to nothing."""
    b, c, loop = wired
    (on_order,) = c.on_exchange_order_notification.handlers
    (on_pnl,) = c.on_instrument_pnl_update.handlers
    loop.run(on_order(_order("B1", quantity=0, total_unfilled_size=1,
                             price_type=4, price=0.0, trigger_price=19950.0)))
    loop.run(on_pnl(_pnl(net=1)))
    b.modify("B1", price=19940.0)
    (m,) = c.modified
    assert m["order"].quantity == 1


def test_the_position_is_net_quantity_and_not_the_days_gross_sides(wired):
    b, c, loop = wired
    (handler,) = c.on_instrument_pnl_update.handlers
    loop.run(handler(_pnl(net=3, buy_qty=5, sell_qty=2)))
    assert b.position["net"] == 3
    loop.run(handler(_pnl(net=0, buy_qty=5, sell_qty=5)))
    assert b.position["net"] == 0
    loop.run(handler(_pnl(net=9, symbol="ESU6")))
    assert b.position["net"] == 0


# --- drag to modify ----------------------------------------------------------


def _armed_with_position(wired, net=1, avg=20000.0, **over):
    """A working stop and a position, which is what a bracket leg looks like.

    `price_type=4` and a `trigger_price`, because that is how Rithmic describes
    the stop leg it attaches on the fill — its own basket, its own resting
    trigger, no user tag (MNQU6 2026-08-11).
    """
    b, c, loop = wired
    (on_order,) = c.on_exchange_order_notification.handlers
    (on_pnl,) = c.on_instrument_pnl_update.handlers
    kw = dict(price=0.0, trigger_price=19950.0, price_type=4,
              transaction_type=2)
    kw.update(over)
    loop.run(on_order(_order("B1", **kw)))
    loop.run(on_pnl(_pnl(net=net, avg_open_fill_price=avg)))
    return b, c, loop


def test_a_bracket_leg_is_dragged_by_its_own_price_not_by_ticks(wired):
    """The bug this whole path was rewritten for. Once the entry fills the legs
    are ordinary working orders with their own baskets, and the bracket-ticks
    call cannot reach them: template 341 answers for the parent only until the
    fill, so `modify_order(stop_ticks=...)` reads back nothing and says "No stop
    loss was set at order creation" about a stop that is working and visible.
    Six live drags died on that message before the cause was found."""
    b, c, _ = _armed_with_position(wired, avg=20000.0)
    b.modify("B1", stop=19990.0)
    (m,) = c.modified
    assert m["basket_id"] == "B1"
    assert m["trigger_price"] == 19990.0
    # Not a distance from anything, and nothing that would make Rithmic go
    # looking for a bracket row that no longer exists.
    assert "stop_ticks" not in m and "target_ticks" not in m


def test_a_stop_entry_is_dragged_by_its_trigger_and_not_its_price(wired):
    """`_validate_price_fields` keeps only the fields that apply to the order's
    type and drops the rest **without a word**, so a stop sent `price` is
    accepted, moves nothing, and reports no error. Measured on MNQU6
    2026-08-11: three drags on a resting stop entry, all accepted, trigger still
    at its original price when it was cancelled forty seconds later."""
    b, c, _ = _armed_with_position(wired)
    b.modify("B1", price=19940.0)
    (m,) = c.modified
    assert m["trigger_price"] == 19940.0 and "price" not in m
    assert m["order"] is not None and m["order"].basket_id == "B1"
    # The raw enum goes back verbatim, not our word for it. (This is also the
    # round trip `order=` skips — `modify_order` would otherwise list every
    # account's orders before the modify even starts.)
    assert m["order"].price_type == 4


def test_a_limit_order_is_still_dragged_by_its_price(wired):
    b, c, _ = _armed_with_position(wired, price=20050.0, trigger_price=0.0,
                                   price_type=1)
    b.modify("B1", target=20060.0)
    (m,) = c.modified
    assert m["price"] == 20060.0 and "trigger_price" not in m


def test_a_stop_limit_drag_keeps_the_gap_it_was_written_with(wired):
    """The distance between trigger and limit is the slippage the order was
    allowed. Collapsing the two would quietly let it fill further away than it
    was ever permitted to."""
    b, c, _ = _armed_with_position(wired, price=19947.0, trigger_price=19950.0,
                                   price_type=3)
    b.modify("B1", stop=19990.0)
    (m,) = c.modified
    assert m["trigger_price"] == 19990.0 and m["price"] == 19987.0


def test_a_trailing_stop_refuses_the_drag_instead_of_losing_it(wired):
    """Rithmic re-derives a trailing stop from the high water mark on every new
    extreme — it does not nudge the one that is there. So a drag would hold
    until the next tick of profit and then be put back *wider*: you would be
    risking more than the chart was showing, having just tightened it."""
    b, c, _ = _armed_with_position(wired, trail_by_ticks=50)
    with pytest.raises(ValueError, match="managing this bracket"):
        b.modify("B1", stop=19990.0)
    # And not one spelling away from being bypassed: `price` and `stop` reach
    # the same wire call now, so the guard is on the order, not on the word.
    with pytest.raises(ValueError, match="managing this bracket"):
        b.modify("B1", price=19990.0)
    assert c.modified == []


def test_a_breakeven_bracket_refuses_the_drag_too(wired):
    """The half the leg cannot answer. A breakeven sets no `trail_by_ticks` and
    still moves the stop out from under a drag — once, which is enough."""
    b, c, loop = wired
    _send(b, side="sell", be_trigger_ticks=25, be_ticks=3)
    (on_order,) = c.on_exchange_order_notification.handlers
    (on_pnl,) = c.on_instrument_pnl_update.handlers
    loop.run(on_order(_order("B1", price=0.0, trigger_price=19950.0,
                             price_type=4, transaction_type=2)))
    loop.run(on_pnl(_pnl(net=-1, avg_open_fill_price=20000.0)))
    with pytest.raises(ValueError, match="managing this bracket"):
        b.modify("B1", stop=19990.0)
    assert c.modified == []


def test_the_managed_bracket_is_forgotten_when_the_position_closes(wired):
    """Otherwise one trailing order poisons every drag for the rest of the day.
    Cleared on the transition to flat, not on any flat reading — an order resting
    with a trail has to keep the flag while the PnL plant repeats net=0."""
    b, c, loop = wired
    _send(b, trail_trigger_ticks=25)
    (on_order,) = c.on_exchange_order_notification.handlers
    (on_pnl,) = c.on_instrument_pnl_update.handlers
    assert b._managed_bracket              # still resting, still armed
    loop.run(on_pnl(_pnl(net=1, avg_open_fill_price=20000.0)))
    assert b._managed_bracket
    loop.run(on_pnl(_pnl(net=0)))
    assert not b._managed_bracket
    loop.run(on_order(_order("B1", price=0.0, trigger_price=19950.0,
                             price_type=4, transaction_type=2)))
    loop.run(on_pnl(_pnl(net=1, avg_open_fill_price=20000.0)))
    b.modify("B1", stop=19990.0)
    assert c.modified


def test_a_leg_is_not_moved_as_the_kind_it_is_not(wired):
    """The chart names the leg from the shape of the book it is drawing. If that
    ever disagrees with the order itself, something upstream is wrong and the
    wire is the last place to find out."""
    b, c, _ = _armed_with_position(wired)      # a stop
    with pytest.raises(ValueError, match="not a limit"):
        b.modify("B1", target=20060.0)
    assert c.modified == []


def test_a_drag_moves_one_order_to_one_price(wired):
    """The three fields are three names for the same thing now, so two at once
    is a confusion rather than a batch."""
    b, c, _ = _armed_with_position(wired)
    with pytest.raises(ValueError, match="cannot be sent together"):
        b.modify("B1", stop=19990.0, price=19940.0)
    assert c.modified == []


def test_a_drag_that_moved_nothing_is_written_down(wired):
    """"Accepted" and "moved" came apart here for months, and nothing in the
    journal said so — just cheerful `modify` lines against an order sitting
    where it started."""
    b, c, loop = _armed_with_position(wired)
    b.modify("B1", stop=19990.0)
    (on_order,) = c.on_exchange_order_notification.handlers
    loop.run(on_order(_order("B1", price=0.0, trigger_price=19950.0,
                             price_type=4, transaction_type=2, notify_type=2)))
    lines = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
    (ig,) = [x for x in lines if x["event"] == "modify_ignored"]
    assert ig["asked"] == 19990.0 and ig["landed"] == 19950.0


def test_a_drag_that_landed_is_not_reported_as_ignored(wired):
    b, c, loop = _armed_with_position(wired)
    b.modify("B1", stop=19990.0)
    (on_order,) = c.on_exchange_order_notification.handlers
    loop.run(on_order(_order("B1", price=0.0, trigger_price=19990.0,
                             price_type=4, transaction_type=2, notify_type=2)))
    lines = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
    assert not [x for x in lines if x["event"] == "modify_ignored"]


def test_modifying_an_order_that_is_gone_is_an_error_not_a_silent_no_op(wired):
    b, _, _ = wired
    with pytest.raises(LookupError, match="not working at the broker"):
        b.modify("NOPE", price=20010.0)


def test_a_modify_goes_through_the_same_gate_as_an_order(wired):
    b, c, _ = _armed_with_position(wired)
    b.use_account(PAPER)
    with pytest.raises(PermissionError):
        b.modify("B1", price=20010.0)
    assert c.modified == []


# --- pairing fills into trades -----------------------------------------------
# The broker reports executions; a *trade* is a thing the broker has to build.
# These mirror `replaySim`'s netting rules on purpose — a paper trade and a real
# one on the same chart have to mean the same thing, or the comparison the whole
# live stack exists for is between two different definitions.


def _fill(basket, side, qty, px, ss=1_700_000_000, **over):
    return _order(basket, notify_type=5, transaction_type=1 if side == "buy" else 2,
                  fill_price=px, fill_size=qty, quantity=qty,
                  total_fill_size=qty, total_unfilled_size=0, ssboe=ss, **over)


def test_a_round_trip_is_paired_out_of_two_fills(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    assert b.trades == []                       # opening a position closes nothing
    loop.run(h(_fill("X1", "sell", 1, 20010.0, ss=1_700_000_060)))

    (t,) = b.trades
    assert t["side"] == "long" and t["size"] == 1
    assert t["entry_price"] == 20000.0 and t["exit_price"] == 20010.0
    assert t["pts"] == 10.0
    assert t["pnl"] == 10.0 * 20.0              # NQ point value from the default
    assert t["entry_ms"] == 1_700_000_000_000
    assert t["exit_ms"] == 1_700_000_060_000
    assert t["r"] is None                       # no stop was working: no risk to divide by


def test_an_add_moves_the_average_and_closes_nothing(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("E2", "buy", 1, 20010.0)))
    assert b.trades == []
    loop.run(h(_fill("X1", "sell", 2, 20020.0)))
    (t,) = b.trades
    assert t["size"] == 2 and t["entry_price"] == 20005.0   # volume-weighted


def test_a_scale_out_is_one_trade_for_the_size_that_came_off(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 3, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0)))
    (t,) = b.trades
    assert t["size"] == 1 and t["reason"] == "reduce"
    # The rest is still on, at the same average.
    loop.run(h(_fill("X2", "sell", 2, 20020.0)))
    assert len(b.trades) == 2 and b.trades[1]["size"] == 2


def test_a_flip_closes_the_whole_position_and_opens_the_other_way(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("F1", "sell", 3, 20010.0)))
    (t,) = b.trades
    assert t["side"] == "long" and t["size"] == 1 and t["pts"] == 10.0
    # And two remain short, entered at the flip's price rather than the old
    # average — the remainder is a new position, not a continuation.
    loop.run(h(_fill("X1", "buy", 2, 20000.0)))
    assert b.trades[1]["side"] == "short" and b.trades[1]["entry_price"] == 20010.0
    assert b.trades[1]["pts"] == 10.0


def test_r_is_measured_against_the_stop_the_position_opened_with(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    # A working stop on the closing side, as Rithmic attaches when a bracketed
    # entry fills.
    loop.run(h(_order("S1", transaction_type=2, price_type=4,
                      trigger_price=19990.0, price=0.0)))
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20020.0)))
    (t,) = b.trades
    assert t["r"] == pytest.approx(2.0)   # 20 points made on 10 risked


def test_the_exit_reason_comes_from_the_leg_that_filled(wired):
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("S1", "sell", 1, 19990.0, price_type=4)))
    assert b.trades[0]["reason"] == "stop"

    loop.run(h(_fill("E2", "buy", 1, 20000.0)))
    loop.run(h(_fill("T1", "sell", 1, 20010.0, price_type=1)))
    assert b.trades[1]["reason"] == "target"

    loop.run(h(_fill("E3", "buy", 1, 20000.0)))
    loop.run(h(_fill("M1", "sell", 1, 20005.0, price_type=2)))
    assert b.trades[2]["reason"] == "manual"


def test_a_closed_trade_reaches_the_journal(wired, monkeypatch):
    """The broker books its own round trips — the paper side posts, this side
    writes directly, and the asymmetry is because the fill engines live in
    different places."""
    from journal.live import booking as bookmod

    booked: list[dict] = []
    monkeypatch.setattr(bookmod, "book_trade",
                        lambda conn, **kw: booked.append(kw) or True)
    monkeypatch.setattr("journal.db.connect", lambda *a, **k: _NullConn())

    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0)))

    (kw,) = booked
    assert kw["account"] == "DEMO1"
    assert kw["instrument"] == "NQU6@CME"     # the format the journal spells
    assert kw["mode"] == "live"
    assert kw["session_date"] == DAY
    assert kw["trade"]["pnl"] == 200.0


def test_a_journal_that_cannot_be_written_does_not_break_trading(wired, monkeypatch):
    """This runs on the feed's event loop, inside the notification handler —
    the same thread that would be servicing a cancel or a flatten. A journal
    that cannot be written is a bad afternoon; a journal write that stops the
    broker responding is a position you cannot close."""
    from journal.live import booking as bookmod

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(bookmod, "book_trade", boom)
    monkeypatch.setattr("journal.db.connect", lambda *a, **k: _NullConn())

    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0)))

    assert len(b.trades) == 1                 # the trade still happened
    assert b.stats_booking_errors == 1        # and the failure is counted
    assert b.snapshot()["booking_errors"] == 1
    # The durable record still has it, so a backfill could recover it.
    events = [json.loads(x)["event"] for x in b.journal.path.read_text().splitlines()]
    assert "trade" in events and "book_failed" in events
    # And the broker is still usable.
    b.flatten()
    assert c.cancel_alls == 1


class _NullConn:
    """A connection that answers close() and nothing else — the booking call
    itself is patched out, so this only has to survive the finally."""

    def close(self):
        pass


def test_trades_do_not_follow_an_account_switch(wired):
    """They belong to the account that made them. Carried across, they would
    draw one account's day on another's chart."""
    b, c, loop = wired
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0)))
    assert len(b.trades) == 1
    b.use_account(PAPER)
    assert b.trades == []


# --- the kill switch ---------------------------------------------------------


def test_flatten_is_gated_on_nothing_and_cancels_before_it_exits(wired):
    """A stop button you have to unlock is not a stop button — and the moment
    you want it most is the one where nothing else about the situation is
    right. Not even the tag or the reconciliation stand in front of it."""
    b, c, _ = wired
    rt._write(rt.ACCOUNTS_KEY, {})       # untagged: everything else would refuse
    b.reconciled_at = None
    assert not b.routable
    b.flatten()
    assert c.cancel_alls == 1 and c.exits == 1


def test_flatten_drops_anything_staged(wired):
    """Whatever made somebody hit this, a sentence written before it should not
    still be one click from the wire."""
    b, c, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.flatten()
    with pytest.raises(LookupError):
        b.send(p["token"])
    assert c.submitted == []


def test_a_half_flatten_is_reported_as_a_failure_naming_both_halves(wired):
    """Reporting the half that worked is the worst available answer."""
    b, c, _ = wired
    c.fail_exit = True
    with pytest.raises(RuntimeError, match="exit_position"):
        b.flatten()
    assert c.cancel_alls == 1        # the cancel is still attempted

    c.fail_cancel_all, c.fail_exit = True, True
    with pytest.raises(RuntimeError) as e:
        b.flatten()
    assert "cancel_all" in str(e.value) and "exit_position" in str(e.value)


# --- losing the permission ---------------------------------------------------


def test_a_disconnect_stops_the_sending_but_does_not_blank_the_position(wired):
    """Two halves of the same rule. The ability to send must not outlive the
    socket; the *last known* position must not be redrawn as flat."""
    b, c, loop = wired
    (handler,) = c.on_instrument_pnl_update.handlers
    loop.run(handler(_pnl(net=2)))

    b.detach("feed disconnected")

    assert not b.routable and not b.attached and not b.ready
    assert b.reconciled_at is None       # stale, and says so
    assert b.position["net"] == 2        # not blanked
    with pytest.raises(PermissionError):
        b.cancel("B1")


def test_the_session_roll_drops_the_review_and_leaves_the_connection(wired):
    """The feed belongs to the run and crosses 18:00; an order priced in the
    session that just ended does not."""
    b, c, _ = wired
    was = b.journal.path
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.roll_day(date(2026, 8, 7))
    with pytest.raises(LookupError):
        b.send(p["token"])
    assert c.submitted == []
    assert b.attached                    # the socket is the same socket
    assert b.journal.path != was


def test_everything_it_did_is_on_disk(wired):
    """The audit trail. Unconditional, unlike the signal journal — an order
    happened at a broker whether or not this process wrote the tape down."""
    b, _, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=10, target_ticks=20)
    b.send(p["token"])
    b.flatten()
    kinds = [json.loads(x)["event"] for x in b.journal.path.read_text().splitlines()]
    assert kinds[0] == "account"         # switching off paper is the first act
    for k in ("reconcile", "submit", "submitted", "flatten", "flattened"):
        assert k in kinds


# --- the endpoints -----------------------------------------------------------


def test_the_router_refuses_with_the_reason_rather_than_a_404(monkeypatch):
    """403 with the env var to set, not a 404 that reads as a missing feature."""
    from fastapi import HTTPException

    from api.routers import live_orders

    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy(enabled=False))
    with pytest.raises(HTTPException) as e:
        live_orders._broker()
    assert e.value.status_code == 403 and "LIVE_ROUTING" in e.value.detail


def test_a_shadow_session_is_not_a_routing_session(monkeypatch):
    from fastapi import HTTPException

    from api.routers import live_orders

    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy())
    monkeypatch.setattr(live_orders.livemod, "current",
                        lambda: SimpleNamespace(broker=None))
    with pytest.raises(HTTPException) as e:
        live_orders._broker()
    assert e.value.status_code == 404
    assert "opened at connect" in e.value.detail


def test_the_status_endpoint_answers_with_no_session_at_all(monkeypatch):
    """The panel asks before it knows there is a session."""
    from api.routers import live_orders

    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy())
    monkeypatch.setattr(live_orders.livemod, "current", lambda: None)
    body = live_orders.routing_status()
    assert body["enabled"] and body["refusal"] is None
    assert body["session"] is False and body["routing_session"] is False
    assert body["broker"] is None


def test_saved_levels_reach_the_session_that_is_running(guarded, monkeypatch):
    """A save that only reached the store is indistinguishable from one that did
    nothing, and worse than one that failed loudly.

    ``Broker`` captures its policy when the order plant opens. Before this, the
    panel's level editor wrote the numbers to the settings table and the running
    session went on enforcing the ones it started with — so the summary line
    read the old range straight back, and a stop widened to trade the day was
    still refused at the old clamp.
    """
    from api.routers import live_orders

    b, _, _ = guarded
    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy())
    monkeypatch.setattr(live_orders.livemod, "current",
                        lambda: SimpleNamespace(broker=b))
    b._day()["realized"] = -26.5

    body = live_orders.routing_settings(live_orders.SettingsIn(
        guards=live_orders.GuardsIn(stop_ticks_min=100, stop_ticks_max=120,
                                    max_risk_usd=1_000.0)))
    assert body["guards"]["stop_ticks_max"] == 120

    # What the panel draws.
    lv = b.guard_view()["levels"]
    assert (lv["stop_ticks_min"], lv["stop_ticks_max"]) == (100, 120)

    # And what the order path enforces — the half that matters. The old range is
    # now the refused one, in both directions.
    assert b.preview(side="buy", qty=1, type="market", price=None,
                     stop_ticks=110, target_ticks=200)["intent"]["stop_ticks"] == 110
    with pytest.raises(ValueError, match="noise-stopped"):
        b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=50, target_ticks=200)

    # A level is not a fill. Widening the stop at 14:00 does not change what the
    # morning cost, and must not be a way to walk back a day that has locked.
    assert b.day_realized == -26.5


def test_the_tag_endpoint_wants_the_word_and_only_a_real_account(wired, monkeypatch):
    from fastapi import HTTPException

    from api.routers import live_orders

    b, _, _ = wired
    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy())
    monkeypatch.setattr(live_orders.livemod, "current",
                        lambda: SimpleNamespace(broker=b))

    with pytest.raises(HTTPException) as e:
        live_orders.routing_tag("DEMO1", live_orders.TagIn(kind="live", confirm="demo"))
    assert e.value.status_code == 422

    with pytest.raises(HTTPException) as e:
        live_orders.routing_tag("NOPE", live_orders.TagIn(kind="demo", confirm="demo"))
    assert e.value.status_code == 404

    # Re-labelling the active account drops anything reviewed: the sentence
    # somebody read named a kind of account, and it means something else now.
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    live_orders.routing_tag("DEMO1", live_orders.TagIn(kind="live", confirm="live"))
    assert rt.tag_of(SYS, "DEMO1").kind == "live"
    with pytest.raises(LookupError):
        b.send(p["token"])


def test_the_order_endpoint_needs_a_token_or_one_click(wired, monkeypatch):
    from fastapi import HTTPException

    from api.routers import live_orders

    b, c, _ = wired
    monkeypatch.setattr(live_orders, "read_policy", lambda: _policy())
    monkeypatch.setattr(live_orders.livemod, "current",
                        lambda: SimpleNamespace(broker=b))

    with pytest.raises(HTTPException) as e:
        live_orders.routing_orders(live_orders.SendIn(side="buy"))
    assert e.value.status_code == 422
    assert "review token" in e.value.detail
    assert c.submitted == []


# --- the discipline layer ----------------------------------------------------
#
# Everything above this point guards against an *accident*: the wrong account, a
# slipped digit, an order nobody read. What follows guards against a *decision* —
# a deliberate order that the person placing it will regret and that their own
# book says loses money.
#
# The two are tested apart for the same reason they are switched apart. A
# permission and a restraint fail in opposite directions, and the tests that
# matter here are the mirror image of the ones above: not "did it refuse", but
# "did it refuse the right thing and let the exit through anyway".

GUARDED = dict(side="buy", qty=1, type="market", price=None,
               stop_ticks=50, target_ticks=120)


def _guarded_policy(**guards) -> rt.Policy:
    """Guards on. **The dollar-risk ceiling is loosened unless a test sets it.**

    Its default ($250) is one 50-tick NQ contract exactly, which would shadow
    every other rule in this section — a two-lot flip would be refused for its
    size before the day lock ever got a say, and the test would pass while
    proving nothing. It has its own tests below, where the level is explicit.
    """
    guards.setdefault("max_risk_usd", 1_000.0)
    return _policy(guardrails=True, guards=rt.Guards(**guards))


@pytest.fixture
def guarded():
    """``wired``, with the rules on and already armed."""
    with _Loop() as loop:
        b = _broker(policy=_guarded_policy())
        c = FakeClient()
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        yield b, c, loop


# --- the switch --------------------------------------------------------------


def test_the_guardrails_are_on_unless_they_are_explicitly_switched_off(monkeypatch):
    """The polarity, which is the whole design decision.

    ``LIVE_ROUTING`` unset means *cannot trade*; ``LIVE_GUARDRAILS`` unset means
    *enforced*. They point opposite ways because a permission and a restraint
    have opposite safe defaults, and both of these fail toward not losing money.
    A guardrail layer that needed a .env entry to exist would be off on every
    fresh checkout, which is exactly when nobody would notice.
    """
    from journal import config as cfgmod

    monkeypatch.setattr(cfgmod, "load_env", lambda: None)
    monkeypatch.setenv("LIVE_ROUTING", "1")

    monkeypatch.delenv("LIVE_GUARDRAILS", raising=False)
    assert rt.policy().guardrails is True
    monkeypatch.setenv("LIVE_GUARDRAILS", "")
    assert rt.policy().guardrails is True
    monkeypatch.setenv("LIVE_GUARDRAILS", "1")
    assert rt.policy().guardrails is True

    for off in ("0", "false", "no", "OFF"):
        monkeypatch.setenv("LIVE_GUARDRAILS", off)
        assert rt.policy().guardrails is False, off


def test_switching_the_layer_off_leaves_the_typo_catchers_alone():
    """max_qty and the order-shape checks are not discipline rules.

    They catch a slipped digit, and there is no session in which a naked 40-lot
    was meant — so they survive ``LIVE_GUARDRAILS=0``, while the bracket rules,
    which are fitted to one trader's book, do not.
    """
    off = _policy(guardrails=False)
    with pytest.raises(ValueError, match="ceiling"):
        rt.build_intent(off, side="buy", qty=99, type="market", price=None,
                        stop_ticks=0, target_ticks=0, symbol=CONTRACT,
                        exchange="CME", account_id="DEMO1")
    # ...and the same order with a legal quantity and no bracket at all passes,
    # which it would not with the rules on.
    assert rt.build_intent(off, side="buy", qty=1, type="market", price=None,
                           stop_ticks=0, target_ticks=0, symbol=CONTRACT,
                           exchange="CME", account_id="DEMO1").qty == 1


# --- the shape of an entry ----------------------------------------------------


def _shape(pol, **over):
    kw = dict(GUARDED, symbol=CONTRACT, exchange="CME", account_id="DEMO1")
    kw.update(over)
    return rt.build_intent(pol, **kw)


def test_an_entry_with_no_bracket_is_refused_and_the_bracket_it_wants_is_named():
    pol = _guarded_policy()
    with pytest.raises(ValueError) as e:
        _shape(pol, stop_ticks=0, target_ticks=0)
    assert "no stop" in str(e.value)
    # The refusal carries the levels, not just the rule: a refusal you have to
    # go and look something up to act on is one you override instead.
    assert "50" in str(e.value) or "40" in str(e.value)
    assert "100" in str(e.value)


def test_a_target_under_the_floor_is_refused_with_the_measurement_behind_it():
    pol = _guarded_policy()
    with pytest.raises(ValueError, match="80 ticks is net-negative"):
        _shape(pol, target_ticks=80)
    assert _shape(pol, target_ticks=100).target_ticks == 100


def test_the_stop_clamp_refuses_at_both_ends_for_different_reasons():
    """Tight and wide are not the same mistake, and the messages differ.

    Too tight is noise-stopping — the 40-tick stop is what stopped the edge
    replicating in the flat half. Too wide is fewer losses until the account is
    over, because the drawdown that ends it is fixed in dollars.
    """
    pol = _guarded_policy()
    with pytest.raises(ValueError, match="noise-stopped"):
        _shape(pol, stop_ticks=20)
    with pytest.raises(ValueError, match="take fewer contracts"):
        _shape(pol, stop_ticks=200)
    assert _shape(pol, stop_ticks=40).stop_ticks == 40
    assert _shape(pol, stop_ticks=60).stop_ticks == 60


def test_closing_size_is_not_held_to_the_bracket_rules():
    """A scale-out has no target to be too tight, and no stop to clamp."""
    pol = _guarded_policy()
    assert _shape(pol, reducing=True, stop_ticks=0, target_ticks=0).qty == 1


def test_zero_disables_one_rule_and_leaves_the_rest_standing():
    pol = _guarded_policy(min_target_ticks=0, require_bracket=False)
    assert _shape(pol, stop_ticks=50, target_ticks=4).target_ticks == 4
    assert _shape(pol, stop_ticks=0, target_ticks=0).stop_ticks == 0
    # The stop clamp is untouched by either.
    with pytest.raises(ValueError, match="noise-stopped"):
        _shape(pol, stop_ticks=1, target_ticks=0)


# --- the day ------------------------------------------------------------------


def _day(**kw):
    return rt.DayState(**kw)


def test_the_daily_stop_refuses_once_the_day_is_that_far_down():
    pol = _guarded_policy()
    assert rt.day_refusal(pol, _day(realized=-499.0), reducing=False) is None
    r = rt.day_refusal(pol, _day(realized=-500.0), reducing=False)
    assert r and "daily stop" in r


def test_the_profit_lock_is_off_until_the_funded_stage_turns_it_on():
    """Eval and funded want opposite behaviour, so this one ships at zero.

    The evaluation has no consistency rule and no minimum days, so capping a
    good day actively costs pass probability. The funded stage's 40% rule makes
    a big day worth less than it looks, and $1,000 is derivable: at 40% it needs
    $2,500 of cycle profit, which is the payout cap.
    """
    assert rt.day_refusal(_guarded_policy(), _day(realized=5000.0),
                          reducing=False) is None
    funded = _guarded_policy(daily_profit_lock=1000.0)
    r = rt.day_refusal(funded, _day(realized=1000.0), reducing=False)
    assert r and "profit lock" in r


def test_the_slow_down_rule_only_bites_below_the_threshold():
    """Not a cooldown after a loss — a cooldown indexed on the hole.

    Re-entering quickly is *fine* on this book (+$10 to +$61/trade); what is not
    fine is re-entering quickly while already down. The measurement is in the
    refusal: a bad start slowed down on costs $147/day, sped up on, $803.
    """
    pol = _guarded_policy()
    # Level on the day: pace is nobody's business.
    assert rt.day_refusal(pol, _day(realized=0.0, since_entry_s=1.0),
                          reducing=False) is None
    # In the hole and 10s after the last entry: refused, and it says how long.
    r = rt.day_refusal(pol, _day(realized=-300.0, since_entry_s=10.0),
                       reducing=False)
    assert r and "110s to wait" in r
    # Past the gap: allowed again.
    assert rt.day_refusal(pol, _day(realized=-300.0, since_entry_s=121.0),
                          reducing=False) is None
    # And the first entry of the day is never held back — there is nothing to
    # be too soon after.
    assert rt.day_refusal(pol, _day(realized=-300.0, since_entry_s=None),
                          reducing=False) is None


def test_a_lock_survives_a_recovery_back_above_the_line():
    """The whole content of the rule is that it does not reopen.

    "One more to get back to level" is the trade this exists to refuse, and a
    stop that lifted the moment a winner landed would refuse exactly the trades
    that did not need refusing.
    """
    pol = _guarded_policy()
    r = rt.day_refusal(pol, _day(realized=+900.0, locked="the daily stop"),
                       reducing=False)
    assert r and "stays over" in r


def test_getting_out_is_never_refused_by_any_of_it():
    """The first check in ``day_refusal``, and not an optimisation.

    A discipline rule that could refuse a scale-out would be, at the worst
    possible moment, a rule that keeps somebody in a trade.
    """
    pol = _guarded_policy(daily_profit_lock=1000.0)
    for st in (_day(realized=-9999.0),
               _day(realized=+9999.0),
               _day(realized=-400.0, since_entry_s=0.0),
               _day(realized=0.0, locked="the daily stop was reached")):
        assert rt.day_refusal(pol, st, reducing=True) is None


def test_switching_the_layer_off_switches_the_day_rules_off_too():
    assert rt.day_refusal(_policy(guardrails=False),
                          _day(realized=-9999.0, locked="stopped"),
                          reducing=False) is None


# --- the levels round-trip ----------------------------------------------------


def test_the_guard_levels_round_trip_and_a_partial_patch_keeps_the_rest():
    """Partial on purpose: a form that predates a guard must not reset it."""
    assert rt.settings().guards.daily_loss_stop == 500.0
    rt.save_settings(guards={"daily_loss_stop": 400.0})
    assert rt.settings().guards.daily_loss_stop == 400.0
    # Untouched by that write, and untouched again by one that names something
    # else entirely.
    assert rt.settings().guards.min_target_ticks == 100
    rt.save_settings(max_qty=3)
    assert rt.settings().guards.daily_loss_stop == 400.0
    assert rt.settings().max_qty == 3


def test_a_nonsense_level_falls_back_rather_than_disabling_the_rule(store):
    """The failure direction that matters: a corrupt level must not read as 0.

    Zero is a real value here and means "this rule is off", so a bad parse that
    landed on it would silently disable a guardrail — which is the one outcome
    this store is not allowed to produce.
    """
    store[rt.SETTINGS_KEY] = {"guards": {"daily_loss_stop": "banana",
                                         "min_target_ticks": None}}
    g = rt.settings().guards
    assert g.daily_loss_stop == 500.0 and g.min_target_ticks == 100


# --- through the broker -------------------------------------------------------


def test_the_days_total_is_what_this_process_paired_net_of_commission(guarded):
    """Not the broker's ``day_pnl``, and net of the round turns.

    The PnL plant reports what the *account* did, which includes anything traded
    elsewhere against the same login, and it arrives as a state whose timing is
    not ours. The latch has to fire on the exact trade that crossed the line, so
    it counts what this process paired — and a $500 stop that ignored $7 a
    contract would not be a $500 stop.
    """
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20000.0)))          # scratch
    assert b.day_realized == pytest.approx(-7.0)          # 2 x $3.50
    assert b.snapshot()["guard"]["trades"] == 1


def test_the_day_locks_on_the_trade_that_crosses_the_line(guarded):
    b, c, loop = guarded
    p = b.preview(**GUARDED)
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))          # -30 pts = -$600
    assert b.day_locked and "daily stop" in b.day_locked
    # Flat, so anything reviewed goes with it: "flat, nothing staged, done".
    with pytest.raises(LookupError):
        b.send(p["token"])
    with pytest.raises(PermissionError):
        b.preview(**GUARDED)


def test_a_lock_does_not_drop_a_review_while_something_is_still_held(guarded):
    """Because dropping it would take away a scale-out somebody already read.

    A rule that refuses the exit is worse than no rule. The lock still blocks
    new entries, and never a reducing one; ``flatten`` is gated on nothing.
    """
    b, c, loop = guarded
    p = b.preview(**GUARDED)
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 2, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))          # one off, one still on
    assert b.day_locked
    # Still spendable — and refused by the day rule rather than by a missing
    # token, which is the refusal that names a reason.
    with pytest.raises(PermissionError, match="the day is over"):
        b.send(p["token"])


def test_a_token_staged_before_the_stop_cannot_be_spent_after_it(guarded):
    """The reason the check is inside ``_submit`` and not only on the review.

    ``send`` spends a token minted earlier, so a guard that ran at preview time
    alone could be walked past by staging an order while still allowed and
    sending it once the day was over.
    """
    b, c, loop = guarded
    p = b.preview(**GUARDED)
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 2, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))          # locks, still holding
    assert b.day_locked                                  # the token survived
    with pytest.raises(PermissionError, match="the day is over"):
        b.send(p["token"])
    assert c.submitted == []


def test_the_way_out_still_works_after_the_day_is_over():
    """With the automatic flatten off, a locked day is still a day you can
    close out of by hand — the rule refuses entries and nothing else."""
    with _Loop() as loop:
        b = _broker(policy=_guarded_policy(auto_flatten=False))
        c = FakeClient()
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        b.use_account("DEMO1")
        _the_way_out(b, c, loop)


def _the_way_out(b, c, loop):
    (h,) = c.on_exchange_order_notification.handlers
    (hp,) = c.on_instrument_pnl_update.handlers
    loop.run(h(_fill("E1", "buy", 3, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))
    loop.run(hp(_pnl(net=2)))                             # the broker's word
    assert b.day_locked
    # Closing size: no bracket rules, no day rules, straight through.
    p = b.preview(side="sell", qty=2, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.send(p["token"])
    assert len(c.submitted) == 1
    # A *bigger* order on the same side is a flip, which is an entry however it
    # is framed — and it is the shape somebody reaches for when refused.
    with pytest.raises(PermissionError):
        b.preview(side="sell", qty=3, type="market", price=None,
                  stop_ticks=50, target_ticks=120)


def test_the_slow_down_clock_starts_when_the_entry_goes_out(guarded):
    """Not when it fills. What the measurement showed is people re-entering
    fast, and an order sitting unfilled is already that decision made."""
    b, c, _ = guarded
    assert b.snapshot()["guard"]["since_entry_s"] is None
    b.send(b.preview(**GUARDED)["token"])
    assert b.snapshot()["guard"]["since_entry_s"] == pytest.approx(0.0, abs=1.0)


def test_a_scale_out_does_not_restart_the_slow_down_clock(guarded):
    b, c, loop = guarded
    (hp,) = c.on_instrument_pnl_update.handlers
    b.send(b.preview(**GUARDED)["token"])
    first = b._days["DEMO1"]["last_entry_at"]
    loop.run(hp(_pnl(net=2)))
    b.send(b.preview(side="sell", qty=1, type="market", price=None,
                     stop_ticks=0, target_ticks=0)["token"])
    assert b._days["DEMO1"]["last_entry_at"] == first


def test_switching_account_is_not_a_way_to_unlock_a_day_that_is_over():
    """The obvious bypass, closed by keeping the day per account.

    Switching away must not carry a stop onto a fresh balance either — the
    record is per account both ways, and switching back restores the lock
    because it was never dropped.
    """
    with _Loop() as loop:
        b = _broker(policy=_guarded_policy())
        c = FakeClient(accounts=("DEMO1", "DEMO2"))
        loop.run(b.attach(c))
        rt.set_tag(SYS, "DEMO1", "demo")
        rt.set_tag(SYS, "DEMO2", "demo")
        b.use_account("DEMO1")
        (h,) = c.on_exchange_order_notification.handlers
        loop.run(h(_fill("E1", "buy", 1, 20000.0)))
        loop.run(h(_fill("X1", "sell", 1, 19970.0)))
        assert b.day_locked

        b.use_account("DEMO2")
        assert b.day_locked is None and b.day_realized == 0.0   # a clean balance
        b.use_account("DEMO1")
        assert b.day_locked                                     # and still over


def test_the_roll_is_the_only_thing_that_clears_the_day(guarded):
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))
    assert b.day_locked
    b.roll_day(date(2026, 8, 7))
    assert b.day_locked is None and b.day_realized == 0.0


def test_the_lock_is_on_disk_with_the_number_that_caused_it(guarded):
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))
    events = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
    (locked,) = [e for e in events if e["event"] == "day_locked"]
    assert locked["realized"] == pytest.approx(-607.0)
    assert locked["account"] == "DEMO1"


def test_a_refused_order_is_recorded_rather_than_only_refused(guarded):
    """An override delivers a different outcome from the rule, so the fact that
    a rule fired has to outlive the toast that said so."""
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 2, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))
    with pytest.raises(PermissionError):
        b.preview(**GUARDED)
    events = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
    assert any(e["event"] == "guard_refused" for e in events)


def test_the_snapshot_says_the_layer_is_off_rather_than_hiding_it(wired):
    """``wired`` runs guardrails off, which is the state being pinned.

    A safety layer that is silently disabled is worse than one that was never
    built, because it gets traded as though it were there — so "off" is a field
    the surface can draw, not the absence of one.
    """
    b, _, _ = wired
    g = b.snapshot()["guard"]
    assert g["on"] is False
    # And the levels are still reported, so the panel can say what *would* be
    # enforced rather than showing an empty box.
    assert g["levels"]["daily_loss_stop"] == 500.0


def test_the_snapshot_shows_both_day_numbers_and_the_gap_between_them(guarded):
    """Shown, never reconciled. A divergence means one of the two is missing
    trades, and quietly picking a winner would hide that."""
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    (hp,) = c.on_instrument_pnl_update.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20000.0)))
    loop.run(hp(_pnl(net=0, day_pnl=250.0)))
    g = b.snapshot()["guard"]
    assert g["realized"] == pytest.approx(-7.0)
    assert g["broker_day_pnl"] == 250.0
    assert g["divergence"] == pytest.approx(257.0)


def test_the_status_endpoint_says_whether_the_rules_are_on_with_no_session(monkeypatch):
    from api.routers import live_orders

    monkeypatch.setattr(live_orders, "read_policy", lambda: _guarded_policy())
    monkeypatch.setattr(live_orders.livemod, "current", lambda: None)
    out = live_orders.routing_status()
    assert out["guardrails"] is True
    assert out["guards"]["min_target_ticks"] == 100


def test_the_settings_endpoint_patches_one_level_and_reports_them_all():
    from api.routers import live_orders

    out = live_orders.routing_settings(
        live_orders.SettingsIn(guards=live_orders.GuardsIn(daily_profit_lock=1000)))
    assert out["guards"]["daily_profit_lock"] == 1000.0
    assert out["guards"]["daily_loss_stop"] == 500.0
    # There is no field on this request that turns the layer off — that is
    # `LIVE_GUARDRAILS`, so switching the rules off means leaving the chart.
    assert "guardrails" not in live_orders.SettingsIn.model_fields
    assert not any("guardrail" in f for f in live_orders.GuardsIn.model_fields)


# --- risk in dollars, which is the rule a quantity ceiling cannot be ----------


def test_the_risk_ceiling_is_measured_in_dollars_not_contracts():
    """The hole ``max_qty`` leaves open, and it is not hypothetical.

    The order path takes its symbol from whatever the feed is on. Five contracts
    on a 50-tick stop is $125 of MNQ or $1,250 of NQ — ten times apart, with the
    same number in the box and the same quantity ceiling passing both. On a
    $2,000 trailing drawdown that is sixteen losses or one and a half.
    """
    pol = _guarded_policy(max_risk_usd=250.0)
    micro, mini = 0.125, 2.50          # $ per tick: MNQ 0.25x2, NQ 0.25x20

    def build(tick_usd, qty):
        return _shape(pol, qty=qty, tick_usd=tick_usd)

    assert build(micro, 5).qty == 5                    # $31 of micros: fine
    assert build(mini, 2).qty == 2                     # $250 of minis: at the line
    with pytest.raises(ValueError, match=r"risks \$625"):
        build(mini, 5)
    # And the message says to look at the contract, not the quantity — the
    # quantity is the thing that looks right.
    with pytest.raises(ValueError, match="Check the contract before the quantity"):
        build(mini, 5)


def test_the_risk_ceiling_needs_a_tick_value_and_is_silent_without_one():
    """No dollars-per-tick, no dollar rule. Silence beats a number made up from
    a default point value that may not be this contract's."""
    pol = _guarded_policy(max_risk_usd=1.0)
    assert _shape(pol, qty=5, tick_usd=0.0).qty == 5
    assert _shape(pol, qty=5).qty == 5


def test_zero_disables_the_risk_ceiling_like_every_other_level():
    assert _shape(_guarded_policy(max_risk_usd=0.0), qty=5, tick_usd=2.50).qty == 5


def test_the_broker_measures_risk_on_the_contract_the_feed_is_actually_on():
    """End to end, because the tick value has to come from the running feed.

    The same order, the same quantity, the same stop — allowed on the micro and
    refused on the mini, decided by nothing the caller sent.
    """
    for pv, allowed in ((2.0, True), (20.0, False)):     # MNQ, NQ
        with _Loop() as loop:
            b = Broker(CONTRACT, "CME", DAY,
                       _guarded_policy(max_risk_usd=250.0),
                       tick_size=0.25, point_value=pv, system=SYS)
            c = FakeClient()
            loop.run(b.attach(c))
            rt.set_tag(SYS, "DEMO1", "demo")
            b.use_account("DEMO1")
            if allowed:
                assert b.preview(**dict(GUARDED, qty=4))["token"]
            else:
                with pytest.raises(ValueError, match="risks"):
                    b.preview(**dict(GUARDED, qty=4))


# --- the automatic flatten ----------------------------------------------------
#
# The half of the daily stop that acts rather than refuses, and the reason the
# stop had to move off realised P&L: a rule that only counts closed trades sits
# silent through an open loss that has already spent the drawdown, then refuses
# the *next* order — which was never the problem.


def _equity_broker(loop, **guards):
    b = _broker(policy=_guarded_policy(**guards))
    c = FakeClient()
    loop.run(b.attach(c))
    rt.set_tag(SYS, "DEMO1", "demo")
    b.use_account("DEMO1")
    return b, c


def test_an_open_loss_counts_toward_the_daily_stop():
    """Nothing booked, everything at risk: the case realised P&L cannot see."""
    with _Loop() as loop:
        b, c = _equity_broker(loop)
        (h,) = c.on_exchange_order_notification.handlers
        (hp,) = c.on_instrument_pnl_update.handlers
        loop.run(h(_fill("E1", "buy", 1, 20000.0)))
        loop.run(hp(_pnl(net=1, open_position_pnl=-200.0)))
        assert b.day_locked is None                 # $200 down: still trading
        assert b.snapshot()["guard"]["equity"] == pytest.approx(-200.0)

        loop.run(hp(_pnl(net=1, open_position_pnl=-600.0)))
        assert b.day_locked and "equity" in b.day_locked
        assert c.cancel_alls == 1 and c.exits == 1   # and it closed the position


def test_the_flatten_cancels_the_bracket_before_it_exits():
    """Same order as the kill switch, and the same reason: exiting under a live
    bracket can leave that bracket to open a fresh position the other way."""
    with _Loop() as loop:
        b, c = _equity_broker(loop)
        order: list[str] = []
        c.cancel_all_orders = _record(c, order, "cancel_all")
        c.exit_position = _record(c, order, "exit")
        (hp,) = c.on_instrument_pnl_update.handlers
        loop.run(hp(_pnl(net=2, open_position_pnl=-900.0)))
        assert order == ["cancel_all", "exit"]


def _record(client, sink, name):
    async def go(**kw):
        sink.append(name)
    return go


def test_the_automatic_flatten_fires_once_however_many_updates_arrive():
    """A PnL plant that keeps reporting a losing position must not queue a
    second exit behind the first, and a position re-opened afterwards must not
    arm another. The latch clears at the roll and nowhere else."""
    with _Loop() as loop:
        b, c = _equity_broker(loop)
        (hp,) = c.on_instrument_pnl_update.handlers
        for _ in range(5):
            loop.run(hp(_pnl(net=1, open_position_pnl=-900.0)))
        assert c.exits == 1
        loop.run(hp(_pnl(net=3, open_position_pnl=-4000.0)))
        assert c.exits == 1
        b.roll_day(date(2026, 8, 7))
        assert b._flattening is False


def test_a_flat_account_is_not_flattened():
    """There is nothing to close, and the realised latch already has this case.
    Sending an exit for a position nobody holds is a message to the exchange
    saying something untrue about what we think we own."""
    with _Loop() as loop:
        b, c = _equity_broker(loop)
        (hp,) = c.on_instrument_pnl_update.handlers
        loop.run(hp(_pnl(net=0, open_position_pnl=-900.0)))
        assert c.exits == 0


def test_the_automatic_flatten_can_be_switched_off_on_its_own():
    """Off, the stop still locks the day — it just stops acting. Somebody who
    wants to manage the exit by hand keeps the refusal and loses the exit."""
    with _Loop() as loop:
        b, c = _equity_broker(loop, auto_flatten=False)
        (hp,) = c.on_instrument_pnl_update.handlers
        loop.run(hp(_pnl(net=1, open_position_pnl=-900.0)))
        assert c.exits == 0 and b.day_locked is None   # realised is still clean


def test_a_flatten_that_only_half_lands_still_locks_and_still_says_so():
    """The task runs inside the event loop, where a raise would be swallowed
    with its reason. A half-landed exit has to leave the day locked and the
    failure on disk, because that is the state a person has to act on."""
    with _Loop() as loop:
        b, c = _equity_broker(loop)
        c.fail_exit = True
        (hp,) = c.on_instrument_pnl_update.handlers
        loop.run(hp(_pnl(net=1, open_position_pnl=-900.0)))
        assert b.day_locked
        events = [json.loads(x) for x in b.journal.path.read_text().splitlines()]
        (done,) = [e for e in events if e["event"] == "auto_flattened"]
        assert done["errors"] and "exit_position" in done["errors"][0]


def test_the_guardrails_being_off_switches_the_automatic_flatten_off_too(wired):
    b, c, loop = wired
    (hp,) = c.on_instrument_pnl_update.handlers
    loop.run(hp(_pnl(net=1, open_position_pnl=-9000.0)))
    assert c.exits == 0


# --- routing to the micro ----------------------------------------------------
#
# The chart is one contract and the order path is another, on purpose: a plan
# sized for micros can be practised against the mini's tape. `Guards.max_risk_usd`
# was written for exactly this ("the chart is NQ but the plan was written for
# MNQ"), so the arithmetic below is the rule it anticipated, now reachable.


def test_the_micro_is_offered_alongside_the_contract_the_feed_is_watching():
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient()))
        assert b.instruments == [CONTRACT, MICRO]
        assert b.snapshot()["feed_symbol"] == CONTRACT


def test_a_login_without_the_micro_is_not_an_outage():
    """A missing sibling leaves the session exactly as capable as before."""
    with _Loop() as loop:
        b = _broker()
        c = FakeClient()
        c.front_months = {}
        loop.run(b.attach(c))
        assert b.instruments == [CONTRACT]
        with pytest.raises(LookupError, match="not routable"):
            b.use_instrument(MICRO)


def test_switching_to_the_micro_reprices_the_risk_by_ten(wired):
    """The same 50 ticks, a tenth of the money. This is the whole point."""
    b, _, _ = wired
    assert b.point_value == 20.0
    b.use_instrument(MICRO)
    assert b.symbol == MICRO and b.point_value == 2.0
    assert b.tick_size == 0.25              # unchanged — the geometry is shared
    snap = b.snapshot()
    assert snap["symbol"] == MICRO and snap["feed_symbol"] == CONTRACT


def test_switching_instrument_drops_the_review(wired):
    """The sentence somebody read named one contract's dollars: the same 50
    ticks is $250 of NQ and $25 of MNQ."""
    b, c, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=0)
    b.use_instrument(MICRO)
    with pytest.raises(LookupError):
        b.send(p["token"])
    assert c.submitted == []


def test_the_switch_is_refused_while_an_order_is_still_working(wired):
    """`_auto_flatten` exits whichever contract routing points at, so a switch
    made with something working would aim the daily-loss stop at the wrong one."""
    b, c, loop = wired
    c._orders = [_order()]
    loop.run(b.reconcile())
    assert b.working
    with pytest.raises(PermissionError, match="still working"):
        b.use_instrument(MICRO)
    assert b.symbol == CONTRACT


def test_the_switch_is_refused_while_a_position_is_open(wired):
    b, c, loop = wired
    (hp,) = c.on_instrument_pnl_update.handlers
    loop.run(hp(_pnl(net=1)))
    with pytest.raises(PermissionError, match="open position"):
        b.use_instrument(MICRO)
    assert b.symbol == CONTRACT


def test_the_day_survives_the_switch(wired):
    """Not resetting `_days` is the load-bearing part: a daily loss stop that
    cleared when you moved to micros would be a way to unlock a day that is
    over — the same accident `use_account` refuses to allow."""
    b, _, _ = wired
    b._count_day({"pnl": -300.0, "size": 1})
    before = b.day_realized
    b.use_instrument(MICRO)
    assert b.day_realized == before


def test_the_contract_you_chose_is_the_one_the_next_session_opens_on(wired):
    """Otherwise every morning re-sizes the plan by ten unless somebody
    remembers to change it back."""
    b, _, _ = wired
    b.use_instrument(MICRO)
    assert rt.instrument_of(SYS) == "MNQ"

    with _Loop() as loop:
        nxt = _broker()
        loop.run(nxt.attach(FakeClient()))
        assert nxt.symbol == MICRO and nxt.point_value == 2.0
        # Where orders go, and nothing else: still paper, still untagged, still
        # every gate in front of the first order.
        assert nxt.paper and nxt.account_id == rt.PAPER
        assert nxt.snapshot()["feed_symbol"] == CONTRACT


def test_the_preference_is_a_root_not_a_contract_month():
    """`MNQU6` stops existing in September; "micros" does not."""
    rt.set_instrument(SYS, "MNQZ9")
    assert rt.instrument_of(SYS) == "MNQ"
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient()))
        assert b.symbol == MICRO          # this quarter's, resolved by Rithmic


def test_a_preference_the_login_cannot_route_leaves_the_feed_contract():
    """A stale entry, or an entitlement that has gone away, degrades to where
    every session started before this — never to a refusal."""
    rt.set_instrument(SYS, "MNQ")
    with _Loop() as loop:
        b = _broker()
        c = FakeClient()
        c.front_months = {}
        loop.run(b.attach(c))
        assert b.symbol == CONTRACT and b.instruments == [CONTRACT]


def test_another_login_does_not_inherit_the_preference():
    """Two logins are two sets of entitlements, like the account tags."""
    rt.set_instrument("OTHER-SYSTEM", "MNQ")
    assert rt.instrument_of(SYS) is None
    with _Loop() as loop:
        b = _broker()
        loop.run(b.attach(FakeClient()))
        assert b.symbol == CONTRACT


def test_commission_scales_to_the_contract_but_not_below_the_floor(wired):
    """$3.50 was measured on minis. Charging a micro round turn at the mini rate
    walks the day toward its stop about $6 a trade faster than reality."""
    b, _, _ = wired
    assert b.commission_per_side() == 3.50
    b.use_instrument(MICRO)
    # A tenth of $3.50 is $0.35; brokers price micros on a ticket floor, so the
    # measured $0.50 wins. Erring high is the safe direction for a loss stop.
    assert b.commission_per_side() == brokermod.MICRO_COMMISSION_FLOOR


# --- the native trailing bracket ---------------------------------------------
#
# async_rithmic cannot send one: `submit_order` hardcodes the `*_STATIC` bracket
# types and never sets the four trailing fields the protobuf carries. The broker
# folds them into template 330 on the way past. Measured against a real account
# on 2026-08-10 (demo/rithmic_trail_spike.py): sent this way, Rithmic ratcheted
# the stop twelve times in 68s and carried it past the fill into profit.


def _bt():
    from async_rithmic.protocol_buffers.request_bracket_order_pb2 import (
        RequestBracketOrder as _R,
    )

    return _R.BracketType


def _send(b, **over):
    """Through the review, which is the path an order normally takes."""
    kw = dict(side="buy", qty=1, type="market", price=None,
              stop_ticks=50, target_ticks=120)
    kw.update(over)
    return b.send(b.preview(**kw)["token"])


def test_a_plain_order_still_goes_out_static(wired):
    """The trail is opt-in at the wire, not just in the UI."""
    b, c, _ = wired
    _send(b)
    (template, msg), = c.plants["order"].sent
    assert template == 330
    assert msg["bracket_type"] == _bt().TARGET_AND_STOP_STATIC
    assert "trailing_stop_trigger_ticks" not in msg


def test_a_trailing_order_swaps_the_bracket_type_and_carries_the_fields(wired):
    b, c, _ = wired
    _send(b, trail_trigger_ticks=25)
    (template, msg), = c.plants["order"].sent
    assert template == 330
    # The static/dynamic pair is the whole mechanism: `*_STATIC` legs sit where
    # they were put, the plain ones are Rithmic's to move.
    assert msg["bracket_type"] == _bt().TARGET_AND_STOP
    assert msg["trailing_stop_trigger_ticks"] == 25
    assert msg["trailing_stop_by_last_trade_price"] is True


def test_the_trail_is_keyed_to_its_own_order(wired):
    """The reason the extras are registered per `user_tag` rather than stashed on
    the broker: with a shared slot, an order sent after a trailing one inherits
    its trail. That is a silently moving stop on an order nobody asked to trail."""
    b, c, _ = wired
    _send(b, trail_trigger_ticks=25)
    _send(b)
    first, second = c.plants["order"].sent
    assert first[1]["trailing_stop_trigger_ticks"] == 25
    assert "trailing_stop_trigger_ticks" not in second[1]
    assert second[1]["bracket_type"] == _bt().TARGET_AND_STOP_STATIC


def test_a_trail_without_a_stop_is_refused(wired):
    """Rithmic rides the trail at the stop's own distance, so a trail with no
    stop would be accepted and then do nothing — the silent no-op this whole
    path exists to avoid."""
    b, _, _ = wired
    with pytest.raises(ValueError, match="needs a stop to trail"):
        b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=0, target_ticks=120, trail_trigger_ticks=25)


def test_the_confirm_says_the_stop_will_move(wired):
    """The stop number stops meaning "where I get out" and starts meaning "where
    I start". Somebody re-reading the sentence has to be told that."""
    b, _, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=50, target_ticks=120, trail_trigger_ticks=25)
    assert "trails 50 ticks behind the high once 25 ticks in profit" in p["sentence"]
    assert "Rithmic moves it" in p["sentence"]


def test_a_trail_survives_the_review_round_trip(wired):
    """The token carries the intent, so a trail staged for review must be the
    trail that goes out — not silently dropped between preview and send."""
    b, c, _ = wired
    p = b.preview(side="buy", qty=1, type="market", price=None,
                  stop_ticks=50, target_ticks=120, trail_trigger_ticks=25)
    b.send(p["token"])
    (_, msg), = c.plants["order"].sent
    assert msg["trailing_stop_trigger_ticks"] == 25


# --- the native breakeven jump -----------------------------------------------
#
# Rithmic's `break_even_ticks` is raw price arithmetic and does not know which
# way you are facing: stop = fill + break_even_ticks x tick_size. Measured both
# ways on MNQU6 (2026-08-11) — a short sent +3 put the stop 3 ticks ABOVE the
# fill, which is 3 ticks of risk wearing the word breakeven; sent -3 it landed 3
# ticks below, locking 3 in. `Intent.be_ticks` is therefore always positive and
# always means "in the trade's favour", and `_submit` owns the negation.


def test_a_long_locks_in_by_adding_to_the_price(wired):
    b, c, _ = wired
    _send(b, side="buy", be_trigger_ticks=25, be_ticks=3)
    (_, msg), = c.plants["order"].sent
    assert msg["break_even_trigger_ticks"] == 25
    assert msg["break_even_ticks"] == 3
    assert msg["bracket_type"] == _bt().TARGET_AND_STOP


def test_a_short_locks_in_by_SUBTRACTING_from_the_price(wired):
    """The bug this test exists for: same request, same positive `be_ticks`, and
    a sign that must flip. Sent unflipped, a short's "breakeven" leaves three
    ticks of risk on the table and still reads as breakeven everywhere above."""
    b, c, _ = wired
    _send(b, side="sell", be_trigger_ticks=25, be_ticks=3)
    (_, msg), = c.plants["order"].sent
    assert msg["break_even_trigger_ticks"] == 25
    assert msg["break_even_ticks"] == -3


def test_the_lock_is_never_negative_above_the_wire(wired):
    """`Intent` speaks profit, not price. A caller cannot ask for a negative
    lock, because that would be asking for risk and calling it breakeven."""
    b, _, _ = wired
    with pytest.raises(ValueError, match="cannot be negative"):
        b.preview(side="sell", qty=1, type="market", price=None, stop_ticks=50,
                  target_ticks=120, be_trigger_ticks=25, be_ticks=-3)


def test_a_zero_tick_lock_is_refused_rather_than_silently_dropped(wired):
    """proto3 does not serialise a singular scalar at its default, so a 0 never
    reaches Rithmic and 'exactly at the fill' is not a thing that can be said.
    Refused loudly instead of sent as an undefined level."""
    b, _, _ = wired
    with pytest.raises(ValueError, match="at least 1 tick"):
        b.preview(side="buy", qty=1, type="market", price=None, stop_ticks=50,
                  target_ticks=120, be_trigger_ticks=25, be_ticks=0)


def test_a_lock_with_no_trigger_never_fires_and_is_refused(wired):
    b, _, _ = wired
    with pytest.raises(ValueError, match="never fires"):
        b.preview(side="buy", qty=1, type="market", price=None, stop_ticks=50,
                  target_ticks=120, be_trigger_ticks=0, be_ticks=3)


def test_breakeven_needs_a_stop_to_move(wired):
    b, _, _ = wired
    with pytest.raises(ValueError, match="needs a stop to move"):
        b.preview(side="buy", qty=1, type="market", price=None, stop_ticks=0,
                  target_ticks=120, be_trigger_ticks=25, be_ticks=3)


def test_the_confirm_says_the_stop_will_jump(wired):
    b, _, _ = wired
    p = b.preview(side="sell", qty=1, type="market", price=None, stop_ticks=50,
                  target_ticks=120, be_trigger_ticks=25, be_ticks=3)
    # In the trade's favour, on both sides — the sentence never shows the wire's
    # negative, because that is a fact about Rithmic and not about the order.
    assert "lock 3 ticks once 25 ticks in profit" in p["sentence"]
    assert "-3" not in p["sentence"]


def test_the_trail_and_the_breakeven_can_ride_together(wired):
    """Untested against Rithmic — measured separately on purpose, so a single
    stop move stayed attributable. Nothing in this layer stops them combining,
    and the replay ladder runs both, so the shape is at least kept honest."""
    b, c, _ = wired
    _send(b, side="buy", trail_trigger_ticks=40, be_trigger_ticks=25, be_ticks=3)
    (_, msg), = c.plants["order"].sent
    assert msg["trailing_stop_trigger_ticks"] == 40
    assert msg["break_even_trigger_ticks"] == 25 and msg["break_even_ticks"] == 3


# --- the day survives the process --------------------------------------------
#
# `roll_day` has always claimed that the 18:00 roll is the only thing that
# clears a daily stop — "not a restart, not an account switch, not a recovery
# back above the line". That was true of every path except the one people
# actually hit: the record lived in memory, so a restart mid-session brought
# the day back at zero with the whole loss stop available again, on an account
# that was already down. The rows were never lost; nothing read them back.


def _restarted(policy=None):
    """A second Broker on the same day, as a restarted process would build it.

    Nothing is carried across by hand — that is the point. It gets what a fresh
    process gets: the journal on disk, and an account somebody selects.
    """
    return _broker(policy=policy or _guarded_policy())


def test_a_restart_rebuilds_the_day_rather_than_handing_back_the_loss_stop(guarded):
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))          # -30 pts = -$600
    assert b.day_locked and b.day_realized == pytest.approx(-607.0)

    with _Loop() as loop2:
        b2 = _restarted()
        loop2.run(b2.attach(FakeClient()))
        b2.use_account("DEMO1")
        # The same money, rebuilt from the booked trade — commission and all.
        assert b2.day_realized == pytest.approx(b.day_realized)
        # And still over. A day that crossed the line before the restart comes
        # back locked, or the restart is a way to unlock it.
        assert b2.day_locked and "daily stop" in b2.day_locked
        with pytest.raises(PermissionError):
            b2.preview(**GUARDED)
        guard = b2.snapshot()["guard"]
        assert guard["trades"] == 1 and guard["restored"] == 1


def test_the_restored_day_is_said_to_be_restored(guarded):
    """A rebuilt figure is the trustworthy one, but the panel still says where
    it came from — and a live-paired trade must not claim to have been."""
    b, c, loop = guarded
    assert b.snapshot()["guard"]["restored"] == 0
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0)))
    assert b.snapshot()["guard"]["restored"] == 0


def test_the_blotter_comes_back_with_the_day(guarded):
    """The trade marks and the page's running total both read `trades`, and both
    used to come back empty from a restart while the trades sat in the journal,
    booked and correct."""
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 20010.0, ss=1_700_000_060)))

    with _Loop() as loop2:
        b2 = _restarted()
        loop2.run(b2.attach(FakeClient()))
        b2.use_account("DEMO1")
        (t,) = b2.trades
        assert t["side"] == "long" and t["size"] == 1
        assert t["entry_price"] == 20000.0 and t["exit_price"] == 20010.0
        assert t["pnl"] == pytest.approx(200.0)
        assert t["entry_ms"] == 1_700_000_000_000
        assert t["exit_ms"] == 1_700_000_060_000
        # Off disk, and saying so. `r` is not in the journal and is None rather
        # than a denominator nobody's stop justified.
        assert t["restored"] is True and t["r"] is None


def test_a_rebuilt_day_charges_each_contract_the_rate_it_was_traded_at():
    """A day that ran the mini in the morning and the micro after lunch is one
    running total made of two commission rates. Charging the lot at whatever
    routing happens to point at *now* moves the restored figure by about $6 a
    trade in whichever direction the last switch went."""
    from journal import db as dbmod
    from journal.live import booking as bookmod

    conn = dbmod.connect()
    try:
        for i, (sym, pnl) in enumerate(((CONTRACT, -100.0), (MICRO, -10.0))):
            bookmod.book_trade(
                conn, account="DEMO1", instrument=f"{sym}@CME", mode="live",
                session_date=DAY,
                trade={"side": "long", "size": 1, "entry_price": 20000.0,
                       "exit_price": 19995.0, "pts": -5.0, "pnl": pnl,
                       "entry_ms": 1_700_000_000_000 + i * 60_000,
                       "exit_ms": 1_700_000_030_000 + i * 60_000,
                       "reason": "manual"})
    finally:
        conn.close()

    b = _restarted()
    b.account_id = "DEMO1"
    micro = b.commission_per_side(MICRO)
    assert micro == brokermod.MICRO_COMMISSION_FLOOR   # not a tenth of the mini
    assert b.day_realized == pytest.approx(-100.0 - 7.0 - 10.0 - 2 * micro)
    assert b.snapshot()["guard"]["restored"] == 2


def test_the_roll_still_clears_the_day_a_restart_would_have_rebuilt(guarded):
    """The one thing that *is* a new day. The rebuild is keyed on the session
    date the roll has just advanced, so it derives the new day's total — which
    is nothing, until it isn't."""
    b, c, loop = guarded
    (h,) = c.on_exchange_order_notification.handlers
    loop.run(h(_fill("E1", "buy", 1, 20000.0)))
    loop.run(h(_fill("X1", "sell", 1, 19970.0)))
    assert b.day_locked

    b.roll_day(DAY + timedelta(days=1))
    assert b.day_locked is None and b.day_realized == 0.0


def test_a_journal_that_cannot_be_read_leaves_the_day_empty_rather_than_dead(
        monkeypatch):
    """A broker that will not come up because sqlite was busy is a worse failure
    than a guard that has to be told the number."""
    from journal import db as dbmod

    def _boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dbmod, "connect", _boom)
    b = _restarted()
    b.account_id = "DEMO1"
    assert b.day_realized == 0.0 and b.day_locked is None
    assert b.snapshot()["guard"]["restored"] == 0
