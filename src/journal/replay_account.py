"""The replay accounts: what a practice sitting costs when it costs something.

Every discipline layer before this one rules on **one order at a time**.
``journal.live.routing`` refuses a shape and refuses a day; ``guardRules.ts``
mirrors it into the browser. Neither of them can say *"you already blew this
account on Tuesday"*, because neither of them remembers Tuesday. That is the gap
this module fills: a replay rep is otherwise free — no stakes, no witness, no
memory — so the sitting that ends $900 down ends by closing the tab, and the next
one opens an hour later from zero.

So the replay gets an account, under the real LucidPro 50K rules (see
``docs/research/lucidpro-50k-survivability.md`` §1 — the constants below are that
table, not a house version), and a sitting lifecycle around it.

**There is no stored balance.** Equity is derived, every time, by walking the
settled attempts under ``data/replays/`` in ``created_at`` order and adding up
what they netted. The attempt files are the ground truth they already were; a
balance field would be a second one, and a deleted attempt would silently
desync it. The walk is O(attempts) and the attempts are a few hundred, so the
honest thing is also the cheap one.

What *is* stored is the account list itself, and the one fact about each that
cannot be derived from trading records — where it begins:

    data/replays/account.json
        {"version": 2,
         "accounts": [{"id", "label", "template", "numbers": {...}, "archived",
                       "epochs": [{"started_at", "cause_of_death"?}]}]}

An **epoch** is one account's life, and it ends two ways. It **blows** the first
time the equity walk closes a sitting at or under the trailing floor, and it
**passes** the first time a sitting settles at or over the profit target. Either
way the walk stops there and the next sitting opens a fresh account, so the two
outcomes tally into a record of completed evals (``record``) — which is derived
from the walks like everything else here, and never counted up on disk, so a
deleted attempt or an edited limit re-answers rather than desyncs. The death, its
moment and the floor that caught it all come out of that walk; the only thing
written back is the cause of death, because that one is written by a human.

The epoch list is also what keeps this feature from retroactively blowing up
months of existing practice: epoch 0 is minted the first time a sitting is
opened *after this shipped*, so every attempt already on disk falls outside every
epoch and is never counted.

**There are as many accounts as you make, and everything above is true of each
of them separately.** There used to be exactly two, hardcoded, and the file was
``{"epochs": [...], "paper_epochs": [...]}`` — ``load_state`` still reads that
and migrates it, so nothing on disk had to be converted.

Each account is an instance of a **template**, and the split between the two is
load-bearing. A template is a rule *shape* — how the floor trails, whether a
death owes a write-up — and ships in code, because a wrong shape is an account
that teaches the wrong game and you would not find out until the real one
behaved differently. The **numbers** are the account's own and are editable from
the UI: a 25K and a 50K of one product are the same rules at different sizes.

Which account a sitting belongs to is its ``account_id``, fixed when it opens and
never patchable (``replays.account_id_of``) — and no two walks see each other's
rows, so one account's blow-up cannot touch another's equity and none of them
can be escaped by switching pages. A drill belongs to none: backtest reps are
unpriced by design (docs/backtest-mode-plan.md D2).

**Three timestamp families live in this file and only one of them is a clock.**
Replay trade times (``entryMs``/``exitMs``) are display-zone wall clocks with the
zone dropped — see the ``journal.replays`` docstring. ``created_at``/
``finished_at`` are true UTC, always from ``replays._iso``, always the same
format (which is why sorting them as strings is correct) — they order the walk
and bound the epochs. And the **tape day** (``tape_day``) is a market date: it is
what the account means by "a day", for the trailing floor and the daily loss
limit both. Nothing subtracts one family from another, and the day is never the
wall clock — that mistake is written up under ``tape_day``, because it cost the
floor its entire ability to move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import replays
from .config import ET_TZ

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# --- the rules --------------------------------------------------------------
# Two things are being kept apart here, and the split is the whole design.
#
# A **template** is a rule *shape*: how the floor trails, and what a death costs.
# It ships in code and cannot be authored from the UI, because a wrong shape is
# not a wrong number — it is an account that teaches the wrong game, and you
# would not find out until the real one behaved differently.
#
# **Numbers** are per account and editable: a 25K and a 50K of the same product
# are the same rules at different sizes, and re-deriving a shape for each would
# be a second thing to get wrong. Every number below can be changed from the
# account manager; none of them changes what ``walk`` *does*.


@dataclass(frozen=True)
class Numbers:
    """One account's figures. Editable; none of them changes the rule shape."""

    #: What the account opens at.
    start: float = 50_000.0
    #: Max Loss Limit — the distance from the peak to the floor.
    max_loss: float = 2_000.0
    #: Initial Trail Balance. Once the peak reaches this the floor stops
    #: following and locks at ``trail_cap − max_loss`` for good.
    trail_cap: float = 52_100.0
    #: Daily Loss Limit. Enforced since the day-goal work: reaching it closes
    #: whatever is open and ends the day. The account survives.
    day_loss: float = 1_200.0
    #: The day's profit goal, or None for an account that has none. Reaching it
    #: on **realised** P&L makes it the day's floor — see ``day_goal_armed``.
    day_goal: float | None = None
    #: What passing looks like.
    profit_target: float = 3_000.0
    max_minis: int = 4
    max_micros: int = 40

    def as_dict(self) -> dict:
        return {
            "start": self.start, "max_loss": self.max_loss,
            "trail_cap": self.trail_cap, "day_loss": self.day_loss,
            "day_goal": self.day_goal, "profit_target": self.profit_target,
            "max_minis": self.max_minis, "max_micros": self.max_micros,
        }

    @classmethod
    def from_dict(cls, raw: Any, *, base: "Numbers | None" = None) -> "Numbers":
        """Read stored numbers over a template's defaults.

        Anything missing or unparseable falls back to the default rather than
        failing: a hand-edited ``account.json`` with one bad field should cost
        that field, not the account. ``day_goal`` is the one that may legitimately
        be null, so it is read separately from the floats.
        """
        b = base or cls()
        d = raw if isinstance(raw, dict) else {}

        def num(key: str, default: float) -> float:
            v = d.get(key)
            return float(v) if isinstance(v, (int, float)) else default

        def whole(key: str, default: int) -> int:
            v = d.get(key)
            return int(v) if isinstance(v, (int, float)) and v > 0 else default

        goal = d.get("day_goal")
        return cls(
            start=num("start", b.start),
            max_loss=num("max_loss", b.max_loss),
            trail_cap=num("trail_cap", b.trail_cap),
            day_loss=num("day_loss", b.day_loss),
            day_goal=float(goal) if isinstance(goal, (int, float)) and goal > 0 else None,
            profit_target=num("profit_target", b.profit_target),
            max_minis=whole("max_minis", b.max_minis),
            max_micros=whole("max_micros", b.max_micros),
        )


@dataclass(frozen=True)
class Template:
    """A rule shape. Ships in code; instances of it are made from the UI."""

    key: str
    label: str
    #: How the floor follows the equity. The only field that changes what the
    #: walk *does* rather than what it compares against:
    #:
    #:   ``eod``      — the LucidPro rule. The peak is taken at day closes, so
    #:                  the floor is constant for a whole sitting, which is what
    #:                  lets the browser be handed one number to compare a live
    #:                  equity against (``guardRules.accountStop``).
    #:   ``intraday`` — the LucidDaily rule. The peak follows running equity
    #:                  *including an open position*, so the floor moves under
    #:                  the position that is moving it.
    trailing: str
    #: Whether a death has to be written up before the account can be replaced.
    needs_cause: bool
    #: Whether a sitting on this account must run at the speed the day ran at.
    #:
    #: A step, a scrub and a speed ladder are all the same gesture — skip the
    #: part you don't want to sit through — and on an account that prices a real
    #: product, sitting through it *is* the rep. So those accounts run forward
    #: at real time or they are paused, and Pause is the whole transport. Paper
    #: keeps the full set: it is the practice surface, and a backtest is a
    #: measurement whose whole point is getting through a session faster than it
    #: happened.
    #:
    #: A property of the rule shape and **never of an account's name or id**,
    #: for the same reason ``trailing`` is: this used to be a hardcoded test for
    #: the id ``funded`` on the page, so every LucidDaily account made from the
    #: registry could be fast-forwarded through the day it was meant to sit
    #: through, and nothing said so.
    real_time: bool
    defaults: Numbers
    #: Shown in the account manager, under the name.
    note: str = ""


TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            "lucid_pro", "LucidPro", "eod", True, True, Numbers(),
            note="End-of-day trailing drawdown. An intraday high does not move "
                 "the floor; a day close does, until it locks at the trail cap.",
        ),
        Template(
            "lucid_daily", "LucidDaily", "intraday", True, True,
            Numbers(day_loss=1_000.0),
            note="Intraday trailing drawdown. The floor follows the running "
                 "equity peak including an open position — money you were up "
                 "and gave back is room you do not get again.",
        ),
        Template(
            "paper", "Paper", "eod", False, False, Numbers(),
            note="The same rules with nothing at stake but the account. A death "
                 "costs no write-up; the next sitting is the reset.",
        ),
    )
}

DEFAULT_TEMPLATE = "lucid_pro"

# The built-in numbers, still reachable under the names the rest of the codebase
# knows them by. They are LucidPro's defaults and are what an unedited account
# has; anything that needs *this* account's figures must read `account.numbers`.
START_EQUITY = Numbers().start
MAX_LOSS = Numbers().max_loss
TRAIL_CAP = Numbers().trail_cap
DAY_LOSS = Numbers().day_loss
PROFIT_TARGET = Numbers().profit_target
MAX_MINIS = Numbers().max_minis
MAX_MICROS = Numbers().max_micros

# --- the policy, which is ours rather than Lucid's --------------------------

#: There used to be two clocks here and there are now none.
#:
#: ``SITTING_GAP_S`` was an hour between the starts of two sittings, to stop one
#: bad session becoming six; removed 2026-08-17. ``COOLDOWN_S`` was a day between
#: a funded death and its replacement, measured from the death; removed
#: 2026-08-24. Both went for the same reason: a timer only knows how long you
#: waited, which is why it charged a clean sitting the same as a blow-up and let
#: a blow-up be paid for by going to bed.
#:
#: What replaced them was the mandatory review — every traded sitting parked
#: until each of its trades was answered for — and on 2026-08-25 that went too,
#: at the user's request (see ``review_flagged``). So between two sittings there
#: is now nothing at all. The one gate left in the module is the write-up a
#: blown account owes before it can be replaced (``needs_cause``), which is what
#: a death costs and the only thing anything costs.
#:
#: An ``active`` attempt this stale is treated as abandoned and settled. Without
#: it, the way to keep a losing sitting off the account is to close the tab —
#: which is precisely the behaviour the account exists to price.
STALE_ACTIVE_S = 60 * 60

ACCOUNT_FILE = "account.json"


@dataclass(frozen=True)
class Account:
    """One account: which sittings it prices, and what a death on it costs.

    There used to be exactly two of these, hardcoded, sharing one set of module
    constants and differing in a single field. The argument for that sharing was
    a good one — *"a practice account with softer rules would teach a game
    nobody funds"* — and it survives, in a better form: the paper account is now
    a **template**, so what makes it cheap is stated as a rule shape rather than
    as a special case, and it still cannot be given softer numbers by accident
    because its shape is the funded one's.

    What changed is that there can be more than one funded rulebook. LucidPro
    trails end-of-day and LucidDaily trails intraday; that is a difference in
    what the walk *does*, not in what it compares against, so it lives in the
    template and nowhere else.
    """

    #: Stable id. What the API asks for, what the client caches under, and what
    #: is stamped on every sitting this account prices. Never reused.
    id: str
    label: str
    template: Template
    numbers: Numbers
    #: Hidden from the switcher, still walked. An account that has traded is
    #: archived rather than deleted — its sittings would otherwise be priced by
    #: nobody, which is the same hole `mode` immutability exists to close.
    archived: bool = False

    #: The name the rest of the codebase knew this by before accounts were a
    #: list. Kept because it reads better at the call sites that mean "which
    #: account is this" rather than "which record".
    @property
    def key(self) -> str:
        return self.id

    @property
    def needs_cause(self) -> bool:
        return self.template.needs_cause

    @property
    def trailing(self) -> str:
        return self.template.trailing

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "template": self.template.key,
            "trailing": self.template.trailing,
            "needs_cause": self.template.needs_cause,
            "real_time": self.template.real_time,
            "archived": self.archived,
            "numbers": self.numbers.as_dict(),
        }


#: The two built-ins, as seeds. A store with no accounts in it gets these, and
#: they are also the defaults the module's own functions fall back to — which is
#: only ever the unedited case, because every caller that matters resolves an
#: account out of the state file (`account_by_id`, `default_account`).
FUNDED = Account("funded", "LucidPro 50K", TEMPLATES["lucid_pro"], Numbers())
PAPER = Account("paper", "Paper", TEMPLATES["paper"], Numbers())

#: The ids of the two accounts that predate the registry, and the modes they
#: were selected by. This is the *whole* of the old `BY_MODE`, kept because
#: every sitting already on disk names its account by mode and nothing else.
LEGACY_MODE_IDS: dict[str, str] = {"replay": "funded", "paper": "paper"}


def for_mode(mode: str | None) -> Account | None:
    """The account a sitting of this mode is priced on — ``None`` for a drill.

    Only meaningful for the two built-ins: a mode was never able to name more
    than two accounts, which is why ids replaced it. Kept for the create route
    and the clients that still speak modes, and it resolves through the store so
    an edited built-in answers with its edited numbers.

    The ``or "replay"`` fallback is ``replays.mode_of``'s and matters for the
    same reason: every attempt written before modes existed is a funded replay.
    """
    key = LEGACY_MODE_IDS.get(str(mode or "replay"))
    return account_by_id(key) if key else None

#: Every status a settled attempt can have. ``active`` is the only one that is
#: not settled; the sweep exists to make sure it does not stay that way.
SETTLED = ("finished", "abandoned", "reviewed")


#: A trade that resolved inside this many milliseconds of **tape** time.
#:
#: The one habit the behavioural audit found actually costs money — about half
#: of all entries, winning 26% of the time — and an entry problem rather than an
#: exit one, which is why it is flagged for review and never refused.
#:
#: **Cross-referenced with ``FAST_TRADE_MS`` in ``frontend/src/lib/guardRules.ts``
#: and it must stay equal to it.** The strip that reports the count and the flag
#: that forces the review would otherwise disagree about the same trade, which is
#: the most confusing possible way for a threshold to drift.
FAST_TRADE_MS = 30_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return replays._iso(ts)


def _parse(ts: str | None) -> datetime | None:
    """A stored UTC stamp, or None. Never a tape time — see the module header."""
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _et_day(ts: str | None) -> str:
    """The New York date a UTC stamp falls on.

    Kept for the places that genuinely mean *when you were at the desk* — the
    stale sweep, an epoch's bounds. It is **not** how the account counts a day;
    see ``tape_day``.
    """
    dt = _parse(ts)
    return dt.astimezone(ET_TZ).date().isoformat() if dt else ""


def tape_day(row: dict) -> str:
    """The trading day a sitting **replayed** — the day this account counts.

    This is the correction to the bug that froze the trailing floor. The walk
    used to take its day boundary from ``created_at``, i.e. the wall-clock
    evening you happened to practise in, and the consequence was silent and
    total: four sittings opened on 2026-08-24 replayed the tapes of 2025-07-02,
    2026-02-17, 2024-10-08 and 2026-08-11, which is four prop days and was
    counted as one. No boundary was ever crossed, ``peak_close`` never moved,
    and the floor sat at 48,000 through a session that ran equity to 53,395 —
    unsticking only the *next* calendar day, when the end-of-walk check finally
    saw a different date.

    A replay of Tuesday **is** Tuesday. The daily loss limit is Tuesday's, the
    day closes when you leave Tuesday, and the floor takes its peak there.
    Practising four days in one evening spends four daily allowances and banks
    four day closes, which is the whole point of practising four days.

    ``date`` is written at create (``replays.create``) and is also the first
    component of the attempt id, so the fallback costs nothing and covers a row
    whose record lost the field. ``_et_day`` is the last resort and only
    reproduces the old behaviour for a row that has neither.
    """
    d = str(row.get("date") or "")
    if _DATE_RE.fullmatch(d):
        return d
    m = replays.ATTEMPT_ID_RE.match(str(row.get("id") or ""))
    return m.group(1) if m else _et_day(row.get("created_at"))


# --- stored state -----------------------------------------------------------
# Resolved through ``replays.REPLAYS_DIR`` on every call rather than captured at
# import: the tests repoint that module attribute at a scratch directory, and a
# constant computed here would keep writing to the real store.


def _path():
    return replays.REPLAYS_DIR / ACCOUNT_FILE


#: v1 was ``{"epochs": [...], "paper_epochs": [...]}`` — two hardcoded accounts,
#: each with its own top-level key. v2 is a list of account records, because
#: there can now be any number of them.
STATE_VERSION = 2

#: The v1 keys, and the account each became. Read on migration and never again.
_V1_KEYS = (("epochs", "funded"), ("paper_epochs", "paper"))


def _epochs(rows: Any) -> list[dict]:
    return [e for e in rows if isinstance(e, dict)] if isinstance(rows, list) else []


def load_state() -> dict:
    """The whole file, normalised to v2 — every account, with its epochs.

    Every account is read and written together because they share the file, and
    a save that only knew about the account it was touching would drop the
    others' history.

    **A v1 file is migrated in memory on every read**, so nothing has to be
    converted on disk before the app will start and a rollback costs nothing
    until the first write. The mapping is exact and total: ``epochs`` was the
    funded account and ``paper_epochs`` was the paper one, both under LucidPro's
    numbers, which are the defaults. A file with neither — a fresh install —
    gets both built-ins with empty epoch lists, which is what "no account has
    been opened yet" already meant.
    """
    raw = replays._read_json(_path(), None)
    raw = raw if isinstance(raw, dict) else {}

    rows = raw.get("accounts")
    if not isinstance(rows, list):
        # v1, or nothing at all.
        seeds = {a.id: a for a in (FUNDED, PAPER)}
        rows = [
            {**seeds[acct_id].as_dict(), "epochs": _epochs(raw.get(key))}
            for key, acct_id in _V1_KEYS
        ]

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        acct = _account_from(row)
        if acct is None or acct.id in seen:
            continue
        seen.add(acct.id)
        out.append({**acct.as_dict(), "epochs": _epochs(row.get("epochs"))})

    # The built-ins are never absent. Deleting `paper` from the file by hand
    # should not take the paper account away — it is where a rehearsal goes when
    # the funded one is dead, and its history is still on disk either way.
    for seed in (FUNDED, PAPER):
        if seed.id not in seen:
            out.append({**seed.as_dict(), "epochs": []})

    return {"version": STATE_VERSION, "accounts": out}


def _account_from(row: dict) -> Account | None:
    """One stored record as an ``Account``, or None if it names no id.

    The template is resolved by key and falls back to the default rather than
    failing — an unknown template means a file written by a newer version or
    edited by hand, and the safe reading of "I do not know this shape" is the
    strictest shape there is rather than no account at all.
    """
    acct_id = str(row.get("id") or "").strip()
    if not acct_id:
        return None
    tpl = TEMPLATES.get(str(row.get("template") or ""), TEMPLATES[DEFAULT_TEMPLATE])
    return Account(
        id=acct_id,
        label=str(row.get("label") or acct_id),
        template=tpl,
        numbers=Numbers.from_dict(row.get("numbers"), base=tpl.defaults),
        archived=bool(row.get("archived")),
    )


def accounts(state: dict | None = None, *, include_archived: bool = True) -> list[Account]:
    """Every account, in file order. The switcher's list."""
    state = load_state() if state is None else state
    out = [_account_from(r) for r in state.get("accounts") or []]
    return [
        a for a in out
        if a is not None and (include_archived or not a.archived)
    ]


def account_by_id(acct_id: str | None, state: dict | None = None) -> Account | None:
    """One account by id, with whatever numbers are stored for it."""
    if not acct_id:
        return None
    return next((a for a in accounts(state) if a.id == acct_id), None)


def default_account(state: dict | None = None) -> Account:
    """The account a request that named none is about.

    The funded built-in when it is there, which it always is; otherwise the
    first unarchived account, and the seed as the last resort. It exists so that
    the module's own ``account=None`` defaults resolve through the *store* — a
    constant default would answer with the seed's numbers over the stored
    account's epochs the moment anybody edited a figure.
    """
    live = accounts(state, include_archived=False)
    return next((a for a in live if a.id == FUNDED.id), live[0] if live else FUNDED)


def epochs_of(state: dict, account: Account) -> list[dict]:
    """One account's epoch list, in the shared state. Never another's.

    **The live list, not a copy.** ``ensure_epoch`` appends to it and
    ``write_cause`` writes into the dict it ends with, both followed by a
    ``save_state`` of the whole state — a defensive copy here would make both of
    those silently no-ops, which is the kind of bug that costs an account its
    life and reports success. ``load_state`` has already normalised every row,
    so what is returned is a plain list of dicts.
    """
    for row in state.get("accounts") or []:
        if isinstance(row, dict) and row.get("id") == account.id:
            rows = row.get("epochs")
            if not isinstance(rows, list):
                rows = []
                row["epochs"] = rows
            return rows
    # An account that is not in the state cannot grow an epoch by being asked
    # about. Callers that mint go through `put_account` first.
    return []


def put_account(state: dict, account: Account, *, epochs: list[dict] | None = None) -> dict:
    """Write one account's record into the state, keeping its epochs.

    Epochs are preserved rather than passed in by every caller, because they are
    the one part of an account nothing but ``ensure_epoch`` and ``write_cause``
    may touch: an edit to a daily limit must not be able to reset a life.
    """
    rows = state.setdefault("accounts", [])
    for i, row in enumerate(rows):
        if isinstance(row, dict) and row.get("id") == account.id:
            kept = _epochs(row.get("epochs")) if epochs is None else epochs
            rows[i] = {**account.as_dict(), "epochs": kept}
            return state
    rows.append({**account.as_dict(), "epochs": epochs or []})
    return state


def save_state(state: dict) -> None:
    # Temp-and-replace, for the reason ``replays._write_json`` gives: this file
    # is small but losing it means losing where the account began.
    replays._write_json(_path(), state)


# --- the attempt ledger -----------------------------------------------------


def sweep_stale_actives(*, now: datetime | None = None) -> list[str]:
    """Settle ``active`` attempts nothing has written to in an hour.

    Returns the ids that were closed. Run before deriving and before gating —
    an attempt that is still ``active`` because the tab was closed on it is a
    sitting whose result has not been counted, and leaving it that way makes
    "close the tab" a working strategy.

    **A stale drill with no trades is deleted, not settled.** Backtest mode
    opens its attempt at the drop rather than at the first fill, so that a rep
    you looked at and correctly passed on is a row — that base rate is the whole
    point of the mode. The cost is that an abandoned draw and a judged sit-out
    are the same record, and a base rate degrades exactly as fast as you re-roll
    draws you do not like. Deleting the empty ones is what keeps "28 of 40 reps
    had no setup" a number about the market rather than about your patience.

    A stale drill that *did* trade settles like anything else: its trades are
    real practice whatever the account thinks of them, and dropping them would
    be the escape hatch this sweep was written to close.
    """
    now = now or _now()
    cutoff = now - timedelta(seconds=STALE_ACTIVE_S)
    closed: list[str] = []
    for row in replays.list_attempts(limit=5000, status="active"):
        seen = _parse(row.get("updated_at")) or _parse(row.get("created_at"))
        if not (seen and seen <= cutoff):
            continue
        try:
            if replays.is_drill(row) and trade_count(row) == 0:
                replays.delete(row["id"])
                closed.append(row["id"])
                continue
            replays.patch(row["id"], status="abandoned")
        except (ValueError, FileNotFoundError, OSError):
            continue
        closed.append(row["id"])
    return closed


def trade_count(row: dict) -> int:
    """How many trades an attempt holds — the summary's number, or the file's.

    Same shape as ``net_usd`` and for the same reason: ``summary.trades`` is the
    browser's own count and the one everything else shows, but it is absent on
    an attempt that never autosaved a full summary — which includes every
    abandoned drill, since the recorder had no fill to write about.

    The distinction matters most where it is least visible: a drill campaign's
    base rate is *traded reps over reps*, so a missing count silently turns
    every rep into a sat-out one and reports a model that never appears.
    """
    n = (row.get("summary") or {}).get("trades")
    if isinstance(n, int):
        return n
    try:
        path = replays.attempt_dir(row["id"]) / "trades.json"
    except (KeyError, ValueError):
        return 0
    trades = replays._read_json(path, []) or []
    return len(trades) if isinstance(trades, list) else 0


def trade_pnls(row: dict) -> list[float]:
    """The sitting's booked P&Ls, in the order they happened.

    This is the walk's view of a sitting's *path*: the running sum of these is
    what the equity did between the open and the settle, and the floor has to be
    compared against that rather than against the settled total — a sitting that
    dived through the floor and traded back would otherwise read as alive
    (``test_a_breach_traded_back_over_is_still_a_death``).

    Empty when there is no trades file, in which case the walk falls back to
    the settled net — the same degradation ``net_usd`` makes, for the same rows.

    ``row["pnls"]`` wins when it is there, which is the row-first-then-disk shape
    ``net_usd`` and ``trade_count`` already have. Nothing written by the browser
    carries it: it is how a *counterfactual* sitting — the same fills under a
    different bracket, from ``replay_whatif`` — hands the walk its path without
    pretending to be a file on disk.
    """
    given = row.get("pnls")
    if isinstance(given, list):
        return [float(x) for x in given if isinstance(x, (int, float))]
    try:
        path = replays.attempt_dir(row["id"]) / "trades.json"
    except (KeyError, ValueError):
        return []
    trades = replays._read_json(path, []) or []
    if not isinstance(trades, list):
        return []
    return [float(t.get("pnl") or 0) for t in trades if isinstance(t, dict)]


def net_usd(row: dict) -> float:
    """What a settled attempt did to the account.

    ``summary.net_usd`` is the browser's own number and the one the history page
    shows, so it is what the account counts — one engine, no second opinion. The
    fallback down to ``trades.json`` is for attempts saved before a summary was
    written (and for anything that lost one), and it adds the same field the
    summary sums.
    """
    summary = row.get("summary") or {}
    v = summary.get("net_usd")
    if isinstance(v, (int, float)):
        return float(v)
    try:
        path = replays.attempt_dir(row["id"]) / "trades.json"
    except (KeyError, ValueError):
        return 0.0
    trades = replays._read_json(path, []) or []
    return sum(float(t.get("pnl") or 0) for t in trades if isinstance(t, dict))


def excursion(row: dict, key: str) -> float | None:
    """One of a sitting's equity-excursion figures, or None.

    Three of them are written by the browser into the summary, all measured in
    dollars **from the equity the sitting opened at** rather than absolutely —
    the account's equity can move under a sitting (an earlier attempt deleted,
    one settled behind the page's back) and an absolute figure would be wrong
    the moment it did.

      ``peak_usd``     max of realised + unrealised over the sitting, ≥ 0.
                       What an intraday-trailing floor is carried forward on.
      ``trough_usd``   min of the same, ≤ 0. An order-free death check: the
                       floor only ever rises, so equity that went under the
                       floor the sitting *opened* on went under every floor it
                       ever had, whichever order things happened in.
      ``min_room_usd`` min of (equity − the floor at the time). The browser's
                       own verdict, and the only reading that catches
                       peak-then-dip — a sitting that ran up, raised its own
                       floor, and then fell back through it.

    **Unlike ``net_usd`` there is no degradation down to ``trades.json``.** The
    money a position was up between two fills is not in that file and cannot be
    recovered from it. Absent means *this sitting has no reading* — every
    attempt written before this shipped, every one the stale sweep settled on an
    old autosave, every summary that was lost — and the walk falls back to the
    booked path there, which is a floor at or under the true one and a death at
    or later than the true one.

    That direction is the whole safety argument, and it is why these figures can
    be taken from the browser at all: **a missing reading can cost the account a
    rule, never a life.**
    """
    v = (row.get("summary") or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def equity_path(row: dict) -> list[tuple[float, float]] | None:
    """A counterfactual sitting's drawdowns, or None for a real one.

    ``[(running peak, the deepest trough after it), …]``, peaks increasing, in
    dollars from the equity the sitting opened at — the same sitting-relative
    convention ``excursion`` uses, and for the same reason.

    **Only a re-priced sitting has one.** Nothing a browser writes carries this
    key, so every attempt on disk reads None here and takes the scalar path
    below it, byte for byte as it always has. What it replaces is
    ``min_room_usd``, which is a *verdict against a floor* and therefore cannot
    exist for a bracket that was never traded — see ``replay_whatif._Path`` for
    why these pairs are a reduction of the equity path rather than a sample of
    it.
    """
    raw = row.get("equity_path")
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        hi, lo = pair
        if not isinstance(hi, (int, float)) or not isinstance(lo, (int, float)):
            return None
        out.append((float(hi), float(lo)))
    return out


def attempts_by_account(rows: list[dict] | None = None) -> dict[str, list[dict]]:
    """Every sitting on disk, split by the account that prices it, oldest first.

    One scan of the store, and the only place the split is made. It exists
    because the split is *expensive* and was being paid per account per epoch:
    ``replays.list_attempts`` opens a record and a summary for every attempt
    there is, and an account with nine lives made that scan ten times to draw one
    view. Everything below that walks more than one window takes its rows from
    here and slices them in memory, so the disk is read once however many lives
    are being counted.

    A drill maps to no account (``account_id_of`` returns None) and is dropped —
    the same attribution ``epoch_attempts`` has always made, moved up one level
    so it can be made for every account at once.
    """
    rows = replays.list_attempts(limit=5000) if rows is None else rows
    out: dict[str, list[dict]] = {}
    for row in rows:
        key = replays.account_id_of(row)
        if key:
            out.setdefault(key, []).append(row)
    for group in out.values():
        group.sort(key=lambda r: r.get("created_at") or "")
    return out


def account_attempts(account: Account) -> list[dict]:
    """Every sitting this account has ever priced, oldest first. One scan."""
    return attempts_by_account().get(account.id, [])


def epoch_attempts(
    *,
    account: Account | None = None,
    since: str | None = None,
    until: str | None = None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """Every attempt an epoch contains, whatever its status, oldest first.

    ``since``/``until`` are ``created_at`` bounds — ``since`` inclusive so an
    attempt opened in the same second the epoch was minted belongs to it,
    ``until`` exclusive so an epoch's last second is not also its successor's
    first. Sorting the stamps as strings is exact: they all come out of
    ``replays._iso``, one format, always UTC.

    ``rows`` is this account's sittings, already fetched — for a caller walking
    several of its lives in a row (``lives``), which would otherwise re-read the
    whole store per window. **They must be this account's**: the attribution is
    made in ``attempts_by_account`` when the rows are supplied, and skipping it
    here is what makes the shared fetch worth anything.
    """
    account = account or default_account()
    # **An account sees only its own sittings.** A drill matches no account and
    # is therefore priced by none — backtest mode is unpriced by design
    # (docs/backtest-mode-plan.md D2), because its reps exist to measure how
    # often a setup is even there and any gate between them makes that
    # uncollectable. Every other sitting matches exactly one account, which is
    # what keeps one account's blow-up off another's equity.
    #
    # The filter is *here*, one layer above `settled_attempts`, and that placement
    # is the load-bearing part. The account's gates read `epoch_attempts`
    # directly; filtering only the settled view would leave the wrong account's
    # rows able to move this one's state, which is exactly the coupling the
    # attribution exists to prevent.
    rows = account_attempts(account) if rows is None else rows
    if since:
        rows = [r for r in rows if (r.get("created_at") or "") >= since]
    if until:
        rows = [r for r in rows if (r.get("created_at") or "") < until]
    return sorted(rows, key=lambda r: r.get("created_at") or "")


def settled_attempts(
    *,
    account: Account | None = None,
    since: str | None = None,
    until: str | None = None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """The ones that have finished happening — what the equity walk counts."""
    rows = epoch_attempts(account=account, since=since, until=until, rows=rows)
    return [r for r in rows if r.get("status") in SETTLED]


# --- the walk ---------------------------------------------------------------


class Walk:
    """The result of adding up one epoch, in order.

    An epoch ends **two** ways and the walk stops at either: the floor catches it
    (``death``) or the equity clears the profit target (``passed``). ``counted``
    stops there too — anything traded afterwards belongs to no account. With the
    create gate in place there is nothing afterwards, but the walk is what the
    gate is built on, so it cannot assume its own enforcement.

    The two are mutually exclusive by construction, and the death is checked
    first: a sitting that dived through the floor and traded up over the target
    died on the way, and the money it passed with was money the account no longer
    had. Same argument the intra-sitting death check is built on.
    """

    def __init__(self, numbers: Numbers | None = None) -> None:
        n = numbers or Numbers()
        self.numbers = n
        self.equity = n.start
        self.peak_close = n.start
        self.floor = min(n.start, n.trail_cap) - n.max_loss
        self.death: dict | None = None
        self.passed: dict | None = None
        self.counted: list[dict] = []

    @property
    def over(self) -> bool:
        """Has this life ended, either way."""
        return self.death is not None or self.passed is not None


def walk(
    rows: list[dict],
    *,
    now: datetime | None = None,
    day: str | None = None,
    numbers: Numbers | None = None,
    trailing: str = "eod",
    seed: Walk | None = None,
) -> Walk:
    """Equity, the trailing floor and the death, from the attempts themselves.

    Two floor shapes, and the whole difference is inside the per-row block.

    Under **``eod``** the floor follows day closes only. That is the actual
    LucidPro rule and it is also the one that makes the rest of this feature
    workable: a floor that moved on every new high would move underneath an open
    position, and the browser could not be handed a constant to compare live
    equity against. So the peak is taken when a *day* ends — and a day is a
    **tape** day (``tape_day``), not the evening you replayed it in. Nothing in
    the intraday branch can run, so this is byte for byte the walk it has always
    been, present excursion fields or not.

    Under **``intraday``** the floor follows the running peak *including an open
    position*, so it moves inside the sitting that is moving it. The unrealised
    half of that path books no trade and is invisible here, so the sitting
    carries its own reading of it (``excursion``). Three readings decide the
    death and every one of them is a *lower bound* on the truth — a missing
    figure degrades to the booked path, which is a floor at or under the true
    one. **The account can be cheated of a rule, never of a life.**

    ``day`` is the tape day currently being replayed, and it is what makes the
    rule exact rather than approximately right. Every day the walk sees closes,
    **except that one**: a day you are still trading has not ended, so its peak
    is not banked and the floor under it does not move. That is what keeps the
    floor constant for a whole sitting *and* across a resume of the same day,
    which is the property ``guardRules.accountStop`` is built on. Pass ``None``
    — the history page, an autopsy, anything not sitting at a chart — and every
    day closes, which is the right reading when no day is open.

    ``now`` is no longer read. It used to decide whether the last day was over,
    back when a day meant a wall-clock date; the tape answers that now. Kept in
    the signature because every caller passes it and it costs nothing, and
    because a walk that took a clock and then quietly ignored it would be worth
    a second look — this is that second look, written down.

    ``seed`` opens the walk where an earlier one left off, instead of at
    ``numbers.start``. Exactly one caller needs it — ``account_campaign``, asking
    what a single rep in the middle of a life would have done under another
    bracket, which is only a fair question from the equity and the peak that rep
    really opened against. It is a parameter rather than a second copy of the
    loop below because the death rule is the thing most worth having in one
    place: a fix applied to a duplicate is a fix that silently stops applying.
    """
    w = Walk(numbers)
    if seed is not None:
        w.equity, w.peak_close, w.floor = seed.equity, seed.peak_close, seed.floor
    n = w.numbers
    intraday = trailing == "intraday"
    prev: str | None = None
    for row in rows:
        d = tape_day(row)
        # Under an intraday trail there are no day boundaries to bank at — the
        # peak has been following the equity all along.
        if not intraday and prev is not None and d != prev:
            w.peak_close = max(w.peak_close, w.equity)
        prev = d
        # Read before the attempt is added: the floor a sitting is judged
        # against is the one that was in force when it opened. Under `eod` that
        # is also the floor it is judged against *throughout*; under `intraday`
        # it is only the starting point, and the loop below raises it.
        opened_at = w.equity
        w.floor = min(w.peak_close, n.trail_cap) - n.max_loss
        floor_at_open = w.floor

        # The death reads the sitting's booked *path*, not just its settled sum.
        # A sitting that dived through the floor and traded back settles above
        # it, and a walk that only summed nets would call the account alive —
        # which is precisely how you test the floor and watch nothing happen.
        # The dip is where the account died; equity freezes there, because the
        # recovery was traded on money the account no longer had.
        dip: float | None = None
        caught_by = floor_at_open

        if intraday:
            # The unrealised half of the path books no trade, so it reaches this
            # process one of two ways.
            path = equity_path(row)
            if path is not None:
                # **The exact way.** A counterfactual sitting has no browser to
                # report on it, so ``replay_whatif`` hands over the drawdowns
                # themselves: (running peak, the deepest trough after it), peaks
                # increasing. That is the whole of what a moving floor can learn
                # from a path — the floor rises only at a new peak, and the room
                # is smallest at the low before the next one — so replaying it
                # here computes the same death every print would, and needs no
                # ``min_room_usd`` to see peak-then-dip.
                seen = w.peak_close
                for hi, lo in path:
                    seen = max(seen, opened_at + hi)
                    against = min(seen, n.trail_cap) - n.max_loss
                    if opened_at + lo <= against:
                        dip, caught_by = opened_at + lo, against
                        break
            else:
                # **The reported way**, for every sitting a browser wrote: three
                # scalars in the summary. Both readings below can only *add* a
                # death; a sitting with neither degrades to the booked path
                # underneath.
                trough = excursion(row, "trough_usd")
                if trough is not None and opened_at + trough <= floor_at_open:
                    # Order-free: the floor only rises, so under the opening floor
                    # is under every floor the sitting ever had.
                    dip = opened_at + trough
                room = excursion(row, "min_room_usd")
                if room is not None and room <= 0:
                    # The browser's own verdict, and the only one that sees
                    # peak-then-dip — equity that raised its own floor and then
                    # fell back through it. Its depth is the trough when we have
                    # one; without it, the floor plus the room left, which is a
                    # display figure and says so by being the weaker of the two.
                    seen = opened_at + trough if trough is not None else floor_at_open + room
                    dip = seen if dip is None else min(dip, seen)

        run = opened_at
        peak = w.peak_close
        for pnl in trade_pnls(row):
            run += pnl
            if intraday:
                # The booked half of the intraday ratchet. Computed from
                # evidence this process owns, which is why it is never removed
                # however good the browser's figures get.
                peak = max(peak, run)
                w.floor = min(peak, n.trail_cap) - n.max_loss
            if run <= w.floor:
                if dip is None or run < dip:
                    dip, caught_by = run, w.floor
                break

        if intraday:
            sat = excursion(row, "peak_usd")
            w.peak_close = max(peak, opened_at + sat) if sat is not None else peak

        w.equity += net_usd(row)
        w.counted.append(row)
        if intraday:
            # `summary.net_usd` and the sum of `trades.json` can differ on a row
            # that lost one of them, and the peak must never sit under the
            # equity it is derived from.
            w.peak_close = max(w.peak_close, w.equity)
            w.floor = min(w.peak_close, n.trail_cap) - n.max_loss

        if dip is not None or w.equity <= w.floor:
            if dip is None:
                caught_by = w.floor
            else:
                w.equity = dip
            w.death = {
                "at": row.get("finished_at") or row.get("updated_at") or row.get("created_at"),
                "attempt_id": row.get("id"),
                "equity": round(w.equity, 2),
                # The floor that *caught* it, not the one it opened under. Under
                # an intraday trail those are different numbers and the autopsy
                # is about the first.
                "floor": round(caught_by, 2),
            }
            return w

        # The other way out, and the only one that is good news.
        #
        # **The settled balance, not the booked path.** The death is read off the
        # path because a breach is instant and unrecoverable — a floor touched is
        # a floor breached, whatever the sitting did afterwards. A target is the
        # opposite: it is cleared at a day's close, and equity that touched it
        # mid-session and gave it back has not cleared anything. Reading this one
        # off the path would pass an account for a print, which is neither the
        # rule Lucid enforces nor a habit worth practising.
        #
        # Guarded on a positive target because a hand-edited ``0`` would
        # otherwise pass every account on its first settled sitting.
        target = n.start + n.profit_target
        if n.profit_target > 0 and w.equity >= target:
            w.passed = {
                "at": row.get("finished_at") or row.get("updated_at") or row.get("created_at"),
                "attempt_id": row.get("id"),
                "equity": round(w.equity, 2),
                "target": round(target, 2),
            }
            return w
    # The last day the walk saw closes too, unless it is the day still being
    # traded. Without this the floor would lag a full day behind: the sitting
    # that ends Tuesday green banks nothing until some *later* row appears, and
    # the first sitting of Wednesday would open against Monday's floor.
    if prev is not None and prev != day:
        w.peak_close = max(w.peak_close, w.equity)
    w.floor = min(w.peak_close, n.trail_cap) - n.max_loss
    return w


# --- epochs -----------------------------------------------------------------


def epoch_bounds(
    state: dict, index: int, *, account: Account | None = None, now: datetime
) -> tuple[str, str | None]:
    """One epoch's ``created_at`` window. The last epoch has no end."""
    epochs = epochs_of(state, account or default_account(state))
    if not epochs:
        # Nothing minted yet: the account starts *now*, which is what keeps
        # every attempt already on disk out of it.
        return _iso(now), None
    started = str(epochs[index].get("started_at") or "")
    end = None
    if index + 1 < len(epochs):
        end = str(epochs[index + 1].get("started_at") or "") or None
    return started, end


@dataclass(frozen=True)
class Life:
    """One epoch of one account: its window, its walk, and how it ended."""

    index: int
    started_at: str
    walk: Walk
    #: The sentence a person wrote about its death. Only ever set on a life that
    #: died, and only once somebody has written it.
    cause: str | None

    @property
    def outcome(self) -> str:
        """``blown``, ``passed``, or ``live`` for the one still being traded."""
        if self.walk.death:
            return "blown"
        if self.walk.passed:
            return "passed"
        return "live"


def lives(
    *,
    account: Account | None = None,
    state: dict | None = None,
    now: datetime | None = None,
    day: str | None = None,
    rows: list[dict] | None = None,
) -> list[Life]:
    """Every life this account has had, walked, oldest first. The last is now.

    One place knows that an account's history is *a list of windows over the same
    sittings*, and everything that used to re-derive those windows for itself —
    the view, the tally, the last death, the gate on writing a cause — reads this
    instead. The store is read once for all of them rather than once per window,
    which is what makes counting nine lives cost what counting one did.

    ``day`` is the tape day being replayed and applies to the **current** life
    alone: it names the day whose peak is not yet banked, and a life that ended
    months ago has no day still open in it. Handing it to an earlier window would
    freeze a floor inside a closed epoch.

    ``rows`` is this account's sittings, already fetched — for the registry
    route, which tallies every account and would otherwise scan the store once
    per account per life.

    An account with no epochs yet gets one synthetic life: starting now, holding
    nothing, ended by neither. That is exactly what "opened but never traded" has
    always looked like to ``derive``, and minting a real one is ``ensure_epoch``'s
    job alone — deriving must never start an account.
    """
    now = now or _now()
    state = load_state() if state is None else state
    account = account or default_account(state)
    epochs = epochs_of(state, account)
    rows = account_attempts(account) if rows is None else rows

    count = max(1, len(epochs))
    out: list[Life] = []
    for index in range(count):
        since, until = epoch_bounds(state, index, account=account, now=now)
        epoch = epochs[index] if index < len(epochs) else {}
        out.append(
            Life(
                index=index,
                started_at=str(epoch.get("started_at") or since),
                walk=walk(
                    settled_attempts(
                        account=account, since=since, until=until, rows=rows
                    ),
                    now=now,
                    day=day if index == count - 1 else None,
                    numbers=account.numbers,
                    trailing=account.template.trailing,
                ),
                cause=(epoch.get("cause_of_death") or "").strip() or None,
            )
        )
    return out


def record(history: list[Life]) -> dict:
    """How many of this account's lives ended each way.

    The whole tracker, and it stores nothing: an outcome is a property of the
    walk over an epoch's sittings, so counting them is counting walks. That is
    what keeps the tally honest through a deleted attempt or an edited limit —
    a stored counter would carry a pass the trades no longer support.

    The **current** life counts. It is finished the moment it passes or blows,
    whatever it is called until the next one is minted; a tally that waited for
    the replacement would sit one behind at exactly the moment you looked at it.
    """
    return {
        "passed": sum(1 for life in history if life.walk.passed),
        "blown": sum(1 for life in history if life.walk.death),
    }


def ensure_epoch(
    *, account: Account | None = None, now: datetime | None = None, day: str | None = None
) -> dict:
    """The epoch a new sitting belongs to, minting one if it is owed.

    Owed in exactly two cases: nothing has ever been minted (first sitting after
    this shipped), or **the current epoch is over and whatever it owed has been
    paid**. Over is either way now — a passed account is as finished as a blown
    one and the next sitting opens a fresh eval, which is what makes "how many
    times has this passed" a count of completed evals rather than of high-water
    marks. What a death owes is the write-up (nothing at all on paper); a pass
    owes nothing, so it lands on ``can_reset`` the moment it clears.

    Called from the create route and nowhere else — deriving must never mint, or
    a page load would start an account.
    """
    now = now or _now()
    state = load_state()
    account = account or default_account(state)
    # An account the state has never heard of gets its record first — an epoch
    # list has nowhere to live otherwise, and `epochs_of` is deliberately unable
    # to conjure one (mutating a list it invented would look like it worked).
    if not any(r.get("id") == account.id for r in state.get("accounts") or []):
        put_account(state, account)
    epochs = epochs_of(state, account)
    if not epochs:
        epochs.append({"started_at": _iso(now)})
        save_state(state)
        return epochs[-1]
    view = derive(account=account, now=now, state=state, day=day)
    if view["can_reset"]:
        epochs.append({"started_at": _iso(now)})
        save_state(state)
    return epochs[-1]


# --- the view ---------------------------------------------------------------


def derive(
    *,
    account: Account | None = None,
    now: datetime | None = None,
    state: dict | None = None,
    day: str | None = None,
) -> dict:
    """Everything the client needs to draw the account.

    ``day`` is the tape day being replayed, and both numbers that mean "today"
    are counted against it: the trailing floor does not bank that day's peak
    (``walk``), and ``day_net`` is the net of the sittings on it. The page knows
    which day it is on and says so; without it every number here is about the
    evening rather than about the session, which is the bug ``tape_day``
    documents.

    ``now`` is echoed in the payload because it is not incidental to the numbers
    beside it: it is the clock that decided which sittings are in the epoch and
    which day ``day_net`` is the net of. A view is a snapshot taken at an instant
    and says which instant. It used to be load-bearing for a second reason —
    the client subtracted the timeouts against it rather than against a machine
    clock that might be four minutes fast — and that reason is gone with them.
    """
    now = now or _now()
    state = load_state() if state is None else state
    account = account or default_account(state)
    n = account.numbers
    history = lives(account=account, state=state, now=now, day=day)
    current = history[-1]
    w = current.walk
    index, cause = current.index, current.cause

    status = "live"
    if w.death:
        # Two states rather than three: blown until the death has been written
        # up, and replaceable once it has. Nothing waits on a clock any more —
        # an account is not bought back with time, and the day it used to cost
        # was a day you could serve asleep. What it costs is the sentence, and
        # on paper (`needs_cause` false) not even that: a paper death lands
        # straight on `can_reset` and the next sitting is its replacement.
        status = "blown" if (account.needs_cause and not cause) else "can_reset"
    elif w.passed:
        # The other end of the same life. A pass owes no write-up — nothing died
        # and there is nothing to explain — so it is resettable from the instant
        # it clears, and the next sitting mints the next eval. It is a *state*
        # rather than a badge for the same reason `blown` is: an account that
        # kept quietly counting sittings past its target would report an eval
        # nobody could have taken.
        status = "passed"

    # The day the limits are counted against is the day being replayed, and
    # nothing else. With no day open there is no day's allowance being spent, so
    # the figures below are zero and full rather than the last day's — a history
    # page that inherited the last tape day's losses would draw a half-empty day
    # meter for a day nobody is trading. Never a wall-clock date; see `tape_day`.
    day_net = (
        sum(net_usd(r) for r in w.counted if tape_day(r) == day) if day else 0.0
    )

    return {
        "now": _iso(now),
        # Which tape day the day figures below are about. The page passed it in
        # and gets it echoed for the same reason `account` is echoed: a payload
        # that cannot name what it counted is a meter that can silently draw the
        # wrong day's allowance after a day switch.
        "day": day,
        # Which account this is. The page knows already — it asked for it — but
        # the payload says so too, because the client caches both under one
        # query family and a view that could not name itself would be a chip
        # able to draw the wrong account's equity after a mode switch.
        "account": account.key,
        "label": account.label,
        # **The rule shape, said outright.** The client has to know whether the
        # floor it was handed is a constant for the sitting (`eod`) or a number
        # it must keep re-deriving under an open position (`intraday`), and the
        # one thing it must never do is infer that from the account's name. Same
        # argument as `account` above, one level down: a payload that cannot name
        # its own rules is a floor that can be enforced under the wrong ones.
        "rules": {
            "template": account.template.key,
            "template_label": account.template.label,
            "trailing": account.template.trailing,
            # Whether the page may drive its own clock. Same argument as
            # `trailing`, and the same trap it was written for: the transport
            # used to be locked by a hardcoded test for the id `funded`, so
            # every LucidDaily account made from the registry could be
            # fast-forwarded through the rep. Read from the shape, never the id.
            "real_time": account.template.real_time,
            **n.as_dict(),
        },
        "equity": round(w.equity, 2),
        "floor": round(w.floor, 2),
        "peak_close": round(w.peak_close, 2),
        "status": status,
        "day_net": round(day_net, 2),
        # What is left of the daily limit. A green day does not bank extra room,
        # so a positive day_net leaves the whole limit rather than more.
        "day_loss_remaining": round(max(0.0, n.day_loss + min(0.0, day_net)), 2),
        "target_remaining": round(max(0.0, n.start + n.profit_target - w.equity), 2),
        # Which sittings this equity is the sum of.
        #
        # The browser adds the sitting *on screen* to `equity` to get a live
        # figure, because a sitting still being traded is not settled and so is
        # not in here. The moment it settles it is, and the two halves double-
        # count it — the page shows a floor breach that is exactly one sitting's
        # P&L deep, refuses every entry, and auto-flattens whatever is opened.
        #
        # The page cannot work this out for itself. It knows when *it* finished
        # an attempt, but `sweep_stale` settles an `active` one behind its back
        # after an hour, and a tab left open across that hour is the case where
        # nothing tells it. So the account says outright what it has counted and
        # the page adds only what is missing from that list.
        "counted_ids": [str(r.get("id")) for r in w.counted if r.get("id")],
        # Whether the next sitting opens a fresh account, which is true of a
        # paid-for death and of a pass alike — the two ways a life ends.
        "can_reset": status in ("can_reset", "passed"),
        "review_flagged": review_flagged(w.counted),
        "epoch": {
            "index": index,
            "started_at": current.started_at,
            "sittings": len(w.counted),
            "net": round(w.equity - n.start, 2),
        },
        # How this life ended, when it has. `last_death` outlives its own epoch
        # (a cause of death is written to be read *through* the next account);
        # `passed` is about the life in hand, because a pass leaves nothing
        # pinned to the next one — the tally is what carries it forward.
        "last_death": last_death(history),
        "passed": w.passed,
        # The tracker: how many of this account's lives ended each way.
        "record": record(history),
        "caps": {"minis": n.max_minis, "micros": n.max_micros},
    }


# --- flags ------------------------------------------------------------------


def _usd(v: float) -> str:
    return f"${abs(v):,.0f}"


def flags_for(attempt: dict, trades: list, *, guards: Any = None) -> list[dict]:
    """Which account rules this sitting tripped — the record it carries out.

    Computed here rather than in the browser for one reason: the browser is the
    thing being reviewed. A finding the page could decline to send is not a
    finding. It stopped being a *gate* on 2026-08-20 (``docs/trade-grading-plan.md``
    G6): "an account rule was tripped" is a different question from "how was the
    trade", the review answers the second, and a flag that had to be dismissed
    before filing was a click rather than an answer. It is still raised, still
    stored, and still shown against the sitting wherever the sitting is shown.

    It is not a *second engine* — nothing below re-derives a fill. It reads
    the trades the browser already booked and stored, and asks two questions of
    them, each one a finding rather than a guess:

      - **oversized** — lost more than a single entry is allowed to risk, which
        means either the ceiling was evaded or the stop did not hold;
      - **rewind** — a seek that un-happened booked trades. Still a real attempt,
        but one whose win rate was written with the answer in hand.

    The style findings that used to live here (**fast**, **hole**) were retired
    2026-08-17 (``docs/review-revamp-plan.md``), when every trade owed a review
    and a flag whose only content was "look at this one" stopped earning its
    verdict. They survive as the sitting's behaviour numbers in the summary
    strip (``guardRules.dayStats``), where a count is what they were.

    One flag per offending *trade*, carrying every reason it earned, so a fast
    trade taken in the hole is one thing to answer for and not two. Worst first
    — if a review is going to be abandoned halfway it should be abandoned from
    the cheap end.

    Times are tape wall clocks (``entryMs``), never account time. See the module
    header on the two families.
    """
    # Local import, and an injectable override: the account does not otherwise
    # need the broker module, and `routing.settings()` opens journal.db — which
    # would make a flag depend on this instance's configured levels rather than
    # on the trade in front of it, in a test as much as in a review.
    if guards is None:
        from .live import routing

        guards = routing.settings().guards
    g = guards

    booked = [t for t in trades if isinstance(t, dict)]

    flags: list[dict] = []
    for t in booked:
        entry = float(t.get("entryMs") or 0)
        pnl = float(t.get("pnl") or 0)
        reasons: list[str] = []
        if g.max_risk_usd and pnl < -g.max_risk_usd:
            reasons.append(f"lost {_usd(pnl)} against a {_usd(g.max_risk_usd)} ceiling")
        if reasons:
            flags.append({
                "kind": "trade",
                "trade_id": t.get("id"),
                "ms": entry,
                "pnl": round(pnl, 2),
                "label": f"{str(t.get('side') or '').upper()} {t.get('size')} — {_usd(pnl)} {'up' if pnl >= 0 else 'down'}",
                "reasons": reasons,
            })
    flags.sort(key=lambda f: f.get("pnl", 0.0))

    for i, r in enumerate(attempt.get("rewinds") or []):
        if not isinstance(r, dict):
            continue
        dropped = int(r.get("dropped") or 0)
        flags.append({
            "kind": "rewind",
            "trade_id": None,
            "ms": float(r.get("from_ms") or 0),
            "pnl": 0.0,
            "label": f"rewind #{i + 1}" + (f" — {dropped} trade{'s' if dropped != 1 else ''} un-happened" if dropped else ""),
            "reasons": ["a seek back over your own fills — the result was known when the next one was taken"],
        })
    return flags


def review_flagged(view_rows: list[dict]) -> dict | None:
    """The oldest sitting marked to review later, plus how many are, or None.

    Oldest rather than newest: the queue is answered in the order it happened,
    which is also the order the tape reads in.

    **This is a reminder, not a debt.** Until 2026-08-25 it was ``review_block``
    and it named any ``finished`` sitting that had booked a trade — every trade
    owed a review, and the account refused to open another sitting until it got
    one (``docs/review-revamp-plan.md``). That gate is gone at the user's
    request: a review you cannot decline is a form you learn to fill in, and the
    sittings worth going back to are not all of them. What replaced it is this —
    a sitting is here because *you said so*, by pressing Review later when it
    ended or by flagging it on the history page. ``refusal`` below no longer
    reads it, and nothing else refuses on it either.

    Filing the review clears the mark (``journal.replays.patch``), so a sitting
    leaves this queue by being answered and never by being waited out.

    ``flags`` rides along because the pages that render this show what the
    sitting tripped — it is not part of the condition.
    """
    marked = [
        r for r in view_rows if r.get("status") == "finished" and r.get("review_later")
    ]
    if not marked:
        return None
    # ``epoch_attempts`` hands the walk its rows oldest first, so the head of
    # this list is the one that has been waiting longest.
    oldest = marked[0]
    return {
        "attempt_id": oldest.get("id"),
        "flags": oldest.get("flags") or [],
        "count": len(marked),
    }


# --- the gate ---------------------------------------------------------------


def refusal(
    *,
    account: Account | None = None,
    now: datetime | None = None,
    view: dict | None = None,
    day: str | None = None,
) -> dict | None:
    """Why a new sitting may not open, or None. The server half of the gate.

    A mirror of ``guardRules.accountRefusal``. One clause each now that the
    review is gone from both, but they are still written as a list in the same
    order, because the thing that made the order matter — the wrong sentence at
    a dead account is any other sentence — is what will decide where the next
    clause goes.

    Two gates rather than one because they answer different questions. The
    browser's stops the gesture before it becomes a fill, which is the only way
    a refusal can be *useful* — by the time this one fires the trade has already
    been taken and only the record can be refused. This one is the one that is
    true: it cannot be got round by a stale page, a second tab, or a client that
    has been edited.

    ``can_reset`` is not a refusal. Whatever the death owed has been paid; the
    create mints the next epoch (see ``ensure_epoch``) and the sitting opens on a
    fresh account. **On the paper account that is the only state a death ever
    reaches**, and the review gate that used to sit below is gone, so paper now
    has no refusal at all — the write-up on the funded account is the last one
    left.

    **The review is no longer one of these (2026-08-25, user request).** It was:
    a ``finished`` sitting that had traded refused the next one until every trade
    carried its three answers. What it produced was a review you did to get the
    dice back rather than to learn anything. A sitting is now marked for review
    by choice (``review_flagged``) and nothing waits on it.

    Every refusal here is answered by *doing something*, never by waiting — both
    of the timed gates this module used to have (the hour between sittings, the
    day after a death) are gone, and nothing here has a deadline in it.
    """
    now = now or _now()
    v = view if view is not None else derive(account=account, now=now, day=day)
    if v["status"] == "blown":
        return {
            "code": "blown",
            "message": (
                "the account is blown — write what killed it before anything else. "
                "One sentence is the whole of what a dead account costs now, which is "
                "exactly why it is not skippable."
            ),
        }
    return None


def write_cause(
    text: str, *, account: Account | None = None, now: datetime | None = None
) -> dict:
    """Record what killed the current account. Raises if nothing has died.

    The one piece of state in this module that is written by a person rather
    than derived, which is exactly why it is the thing the blown state waits on:
    everything else about a death the walk already knows, and the timeout that
    used to sit beside this taught the timeout rather than the lesson.
    """
    now = now or _now()
    text = (text or "").strip()
    if not text:
        raise ValueError("a cause of death has to say something")
    state = load_state()
    account = account or default_account(state)
    epochs = epochs_of(state, account)
    if not epochs:
        raise ValueError("no account has been opened yet")
    # An account that *passed* is also not alive, and also has nothing to write
    # up: the sentence answers a death, so there being no death is the refusal
    # whichever way the life ended.
    if not lives(account=account, state=state, now=now)[-1].walk.death:
        raise ValueError("this account is still alive")
    epochs[len(epochs) - 1]["cause_of_death"] = text
    save_state(state)
    return derive(account=account, now=now, state=state)


def last_death(history: list[Life]) -> dict | None:
    """The most recent account death, however many lives back it was.

    Usually the *previous* epoch's, and that is the point: a cause of death is
    written so it can be read through the next account, which means it has to
    outlive the epoch that produced it. Derived rather than stored, like every
    other consequence of the walk — only the written sentence is state.

    Reversed, so a life that passed does not hide the death before it: the two
    outcomes interleave and this answers "what killed one of these last", not
    "how did the last one end".
    """
    for life in reversed(history):
        if life.walk.death:
            return {**life.walk.death, "epoch": life.index, "cause_of_death": life.cause}
    return None
