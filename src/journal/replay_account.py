"""The replay account: what a practice sitting costs when it costs something.

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

What *is* stored is the one fact that cannot be derived from trading records —
where an account begins:

    data/replays/account.json    {"epochs": [{"started_at", "cause_of_death"?}]}

An **epoch** is one account's life. It ends the first time the equity walk closes
a sitting at or under the trailing floor. The death, its moment, the cooldown it
starts and the floor that caught it are all derived from that walk; the only
thing written back is the cause of death, because that one is written by a human.

The epoch list is also what keeps this feature from retroactively blowing up
months of existing practice: epoch 0 is minted the first time a sitting is
opened *after this shipped*, so every attempt already on disk falls outside every
epoch and is never counted.

**Two timestamp families live in this file and must never meet.** Replay trade
times (``entryMs``/``exitMs``) are display-zone wall clocks with the zone dropped
— see the ``journal.replays`` docstring. ``created_at``/``finished_at`` are true
UTC, always from ``replays._iso``, always the same format (which is why sorting
them as strings is correct). Anything about the *tape* uses the first family and
only ever as a difference; anything about the *account* — epochs, the hour gate,
the day the loss limit is counted against — uses the second. Nothing subtracts
one from the other.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import replays
from .config import ET_TZ

# --- the rules, from the LucidPro 50K table ---------------------------------

START_EQUITY = 50_000.0
#: Max Loss Limit. End-of-day trailing, which is the whole subtlety: an intraday
#: high does not move the floor, so the floor is constant for a whole sitting.
MAX_LOSS = 2_000.0
#: Initial Trail Balance. Once a day closes at or above this, the floor stops
#: following and locks at ``TRAIL_CAP − MAX_LOSS`` = 50,100 for good.
TRAIL_CAP = 52_100.0
#: Daily Loss Limit — soft. Locked out for the session; the account survives.
DAY_LOSS = 1_200.0
#: What passing looks like.
PROFIT_TARGET = 3_000.0
MAX_MINIS = 4
MAX_MICROS = 40

# --- the policy, which is ours rather than Lucid's --------------------------

#: Minimum wall-clock gap between the *starts* of two sittings. The point is
#: that a rep should cost an hour of your day whatever it cost the account;
#: back-to-back replays are how a bad session becomes six bad sessions.
SITTING_GAP_S = 60 * 60
#: How long a blown account stays blown, measured from the death.
COOLDOWN_S = 24 * 60 * 60
#: An ``active`` attempt this stale is treated as abandoned and settled. Without
#: it, the way to keep a losing sitting off the account is to close the tab —
#: which is precisely the behaviour the account exists to price.
STALE_ACTIVE_S = 60 * 60

ACCOUNT_FILE = "account.json"

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
    """The New York date a UTC stamp falls on — the day a prop firm counts.

    A sitting opened at 22:00 ET is on the next UTC date and the same trading
    day, so the daily loss limit has to be grouped this way or a late session
    would get a fresh $1,200 halfway through itself.
    """
    dt = _parse(ts)
    return dt.astimezone(ET_TZ).date().isoformat() if dt else ""


# --- stored state -----------------------------------------------------------
# Resolved through ``replays.REPLAYS_DIR`` on every call rather than captured at
# import: the tests repoint that module attribute at a scratch directory, and a
# constant computed here would keep writing to the real store.


def _path():
    return replays.REPLAYS_DIR / ACCOUNT_FILE


def load_state() -> dict:
    state = replays._read_json(_path(), None)
    if not isinstance(state, dict) or not isinstance(state.get("epochs"), list):
        return {"epochs": []}
    return {"epochs": [e for e in state["epochs"] if isinstance(e, dict)]}


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
    """
    now = now or _now()
    cutoff = now - timedelta(seconds=STALE_ACTIVE_S)
    closed: list[str] = []
    for row in replays.list_attempts(limit=5000, status="active"):
        seen = _parse(row.get("updated_at")) or _parse(row.get("created_at"))
        if seen and seen <= cutoff:
            try:
                replays.patch(row["id"], status="abandoned")
            except (ValueError, FileNotFoundError):
                continue
            closed.append(row["id"])
    return closed


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


def settled_attempts(*, since: str | None = None, until: str | None = None) -> list[dict]:
    """Every settled attempt in an epoch, oldest first.

    ``since``/``until`` are ``created_at`` bounds — ``since`` inclusive so an
    attempt opened in the same second the epoch was minted belongs to it,
    ``until`` exclusive so an epoch's last second is not also its successor's
    first. Sorting the stamps as strings is exact: they all come out of
    ``replays._iso``, one format, always UTC.
    """
    rows = [r for r in replays.list_attempts(limit=5000) if r.get("status") in SETTLED]
    if since:
        rows = [r for r in rows if (r.get("created_at") or "") >= since]
    if until:
        rows = [r for r in rows if (r.get("created_at") or "") < until]
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows


# --- the walk ---------------------------------------------------------------


class Walk:
    """The result of adding up one epoch, in order.

    ``counted`` stops at the death: an epoch is over when the floor catches it,
    and anything traded afterwards belongs to no account. With the create gate
    in place there is nothing afterwards, but the walk is what the gate is built
    on, so it cannot assume its own enforcement.
    """

    def __init__(self) -> None:
        self.equity = START_EQUITY
        self.peak_close = START_EQUITY
        self.floor = min(START_EQUITY, TRAIL_CAP) - MAX_LOSS
        self.death: dict | None = None
        self.counted: list[dict] = []


def walk(rows: list[dict], *, now: datetime) -> Walk:
    """Equity, the trailing floor and the death, from the attempts themselves.

    The floor follows **day closes only**. That is the actual LucidPro rule and
    it is also the one that makes the rest of this feature workable: a floor
    that moved on every new high would move underneath an open position, and
    the browser could not be handed a constant to compare live equity against.
    So the peak is taken when a *day* ends — the moment the walk sees an attempt
    on a later New York date, and once more at the end if the last attempt's day
    is already over.
    """
    w = Walk()
    day: str | None = None
    for row in rows:
        d = _et_day(row.get("created_at"))
        if day is not None and d != day:
            w.peak_close = max(w.peak_close, w.equity)
        day = d
        # Read before the attempt is added: the floor a sitting is judged
        # against is the one that was in force when it opened.
        w.floor = min(w.peak_close, TRAIL_CAP) - MAX_LOSS
        w.equity += net_usd(row)
        w.counted.append(row)
        if w.equity <= w.floor:
            w.death = {
                "at": row.get("finished_at") or row.get("updated_at") or row.get("created_at"),
                "attempt_id": row.get("id"),
                "equity": round(w.equity, 2),
                "floor": round(w.floor, 2),
            }
            return w
    if day is not None and day != _et_day(_iso(now)):
        w.peak_close = max(w.peak_close, w.equity)
    w.floor = min(w.peak_close, TRAIL_CAP) - MAX_LOSS
    return w


# --- epochs -----------------------------------------------------------------


def epoch_bounds(state: dict, index: int, *, now: datetime) -> tuple[str, str | None]:
    """One epoch's ``created_at`` window. The last epoch has no end."""
    epochs = state.get("epochs") or []
    if not epochs:
        # Nothing minted yet: the account starts *now*, which is what keeps
        # every attempt already on disk out of it.
        return _iso(now), None
    started = str(epochs[index].get("started_at") or "")
    end = None
    if index + 1 < len(epochs):
        end = str(epochs[index + 1].get("started_at") or "") or None
    return started, end


def ensure_epoch(*, now: datetime | None = None) -> dict:
    """The epoch a new sitting belongs to, minting one if it is owed.

    Owed in exactly two cases: nothing has ever been minted (first sitting after
    this shipped), or the current epoch is dead and its cooldown has run out.
    Called from the create route and nowhere else — deriving must never mint, or
    a page load would start an account.
    """
    now = now or _now()
    state = load_state()
    epochs = state.get("epochs") or []
    if not epochs:
        state["epochs"] = [{"started_at": _iso(now)}]
        save_state(state)
        return state["epochs"][-1]
    view = derive(now=now, state=state)
    if view["status"] == "can_reset":
        state["epochs"].append({"started_at": _iso(now)})
        save_state(state)
    return state["epochs"][-1]


# --- the view ---------------------------------------------------------------


def derive(*, now: datetime | None = None, state: dict | None = None) -> dict:
    """Everything the client needs to draw the account and count down to it.

    ``now`` is in the payload on purpose. Every countdown here is a deadline
    minus a clock, and a browser clock that is four minutes fast would show a
    gate as open that the server will refuse — so the client subtracts against
    this rather than against its own.
    """
    now = now or _now()
    state = load_state() if state is None else state
    epochs = state.get("epochs") or []
    index = max(0, len(epochs) - 1)
    since, until = epoch_bounds(state, index, now=now)
    rows = settled_attempts(since=since, until=until)
    w = walk(rows, now=now)

    epoch = epochs[index] if epochs else {"started_at": since}
    cause = (epoch.get("cause_of_death") or "").strip() or None

    status = "live"
    cooldown_until = None
    if w.death:
        died = _parse(w.death.get("at")) or now
        cooldown_until = _iso(died + timedelta(seconds=COOLDOWN_S))
        if not cause:
            # Blown until it has been written up. The timeout runs from the
            # death either way, so an autopsy done promptly costs nothing —
            # but an account cannot be replaced by waiting in silence.
            status = "blown"
        elif now < died + timedelta(seconds=COOLDOWN_S):
            status = "cooldown"
        else:
            status = "can_reset"

    today = _et_day(_iso(now))
    day_net = sum(net_usd(r) for r in w.counted if _et_day(r.get("created_at")) == today)

    last = w.counted[-1] if w.counted else None
    next_sitting_at = None
    if last and status == "live":
        opened = _parse(last.get("created_at"))
        if opened:
            due = opened + timedelta(seconds=SITTING_GAP_S)
            if due > now:
                next_sitting_at = _iso(due)

    return {
        "now": _iso(now),
        "equity": round(w.equity, 2),
        "floor": round(w.floor, 2),
        "peak_close": round(w.peak_close, 2),
        "status": status,
        "day_net": round(day_net, 2),
        # What is left of the soft daily limit. A green day does not bank extra
        # room, so a positive day_net leaves the full $1,200 rather than more.
        "day_loss_remaining": round(max(0.0, DAY_LOSS + min(0.0, day_net)), 2),
        "target_remaining": round(max(0.0, START_EQUITY + PROFIT_TARGET - w.equity), 2),
        "next_sitting_at": next_sitting_at,
        "cooldown_until": cooldown_until,
        "can_reset": status == "can_reset",
        "review_block": review_block(w.counted),
        "epoch": {
            "index": index,
            "started_at": epoch.get("started_at") or since,
            "sittings": len(w.counted),
            "net": round(w.equity - START_EQUITY, 2),
        },
        "last_death": last_death(state, now=now),
        "caps": {"minis": MAX_MINIS, "micros": MAX_MICROS},
    }


# --- flags ------------------------------------------------------------------


def _usd(v: float) -> str:
    return f"${abs(v):,.0f}"


def flags_for(attempt: dict, trades: list, *, guards: Any = None) -> list[dict]:
    """What in this sitting has to be answered for before the next one opens.

    Computed here rather than in the browser for one reason: the browser is the
    thing being reviewed. A flag the page could decline to send is not a gate.

    It is still not a *second engine* — nothing below re-derives a fill. It reads
    the trades the browser already booked and stored, and asks four questions of
    them, each one a finding rather than a guess:

      - **fast** — resolved inside 30 seconds (see ``FAST_TRADE_MS``);
      - **hole** — opened while the day was already past the slow-down level, the
        third of the three behavioural numbers the operating plan says to log;
      - **oversized** — lost more than a single entry is allowed to risk, which
        means either the ceiling was evaded or the stop did not hold;
      - **rewind** — a seek that un-happened booked trades. Still a real attempt,
        but one whose win rate was written with the answer in hand.

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
    by_exit = sorted(booked, key=lambda t: float(t.get("exitMs") or 0))

    def realized_before(open_ms: float) -> float:
        return sum(float(t.get("pnl") or 0) for t in by_exit if float(t.get("exitMs") or 0) <= open_ms)

    flags: list[dict] = []
    for t in booked:
        entry = float(t.get("entryMs") or 0)
        exit_ms = float(t.get("exitMs") or 0)
        pnl = float(t.get("pnl") or 0)
        reasons: list[str] = []
        if exit_ms and entry and exit_ms - entry < FAST_TRADE_MS:
            reasons.append(f"resolved in {round((exit_ms - entry) / 1000)}s")
        if g.slow_down_at and realized_before(entry) <= -g.slow_down_at:
            reasons.append(f"opened with the day already {_usd(realized_before(entry))} down")
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


def review_is_complete(flags: list, review: dict | None) -> bool:
    """Has every flag been answered? The condition for accepting ``reviewed``.

    Deliberately not "did they type something": a verdict per flag is the whole
    ask, and a partial review that unlocked the next sitting would be a gate you
    clear by scrolling.
    """
    if not flags:
        return True
    items = (review or {}).get("items") or []
    answered = {
        int(i["flag_idx"])
        for i in items
        if isinstance(i, dict)
        and isinstance(i.get("flag_idx"), (int, float))
        and i.get("verdict") in ("leak", "justified")
    }
    return answered >= set(range(len(flags)))


def review_block(view_rows: list[dict]) -> dict | None:
    """The oldest sitting still owing a review, or None.

    Oldest rather than newest: the queue is answered in the order it happened,
    which is also the order the tape reads in.
    """
    for row in view_rows:
        if row.get("status") == "finished" and (row.get("flags") or []):
            return {"attempt_id": row.get("id"), "flags": row.get("flags") or []}
    return None


def last_death(state: dict, *, now: datetime) -> dict | None:
    """The most recent account death, current epoch or the one before it.

    The previous epoch's is the one that matters most of the time: a cause of
    death is written so it can be read *through the next account*, which means
    it has to outlive the epoch that produced it. Derived rather than stored,
    like every other consequence of the walk — only the written cause is state.
    """
    epochs = state.get("epochs") or []
    for index in range(len(epochs) - 1, -1, -1):
        since, until = epoch_bounds(state, index, now=now)
        w = walk(settled_attempts(since=since, until=until), now=now)
        if w.death:
            return {
                "at": w.death["at"],
                "attempt_id": w.death["attempt_id"],
                "equity": w.death["equity"],
                "floor": w.death["floor"],
                "epoch": index,
                "cause_of_death": (epochs[index].get("cause_of_death") or "").strip() or None,
            }
    return None
