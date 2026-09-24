"""What this account would have done under a different bracket.

``replay_account`` knows what a sequence of sittings does to an account.
``replay_whatif`` knows what one sitting would have done under other exits.
Neither one can answer the question this module exists for, which is the one an
account actually poses:

    *would this bracket have kept me alive?*

That is not the question the day view asks. A day view ranks brackets by what
they netted, and a net is the wrong objective for an account with a floor under
it: a bracket that makes more money and touches the floor on rep 11 has not
beaten the one that made less and did not. So every row here is re-walked —
the counterfactual P&Ls of each rep, in order, through ``replay_account.walk``
under *this* account's own numbers and its own trailing shape — and ranked by
how the account ended.

**A campaign, not an epoch.** The real epochs were minted by real deaths, so
reusing their boundaries would be anachronistic: a bracket that survives rep 11
never opens the life that really began there. Instead each row is walked
straight through every rep the account has, minting a fresh life at
``numbers.start`` each time the walk dies or passes, exactly the way a real
account is re-bought. What comes out is a record — *2 passed, 1 blown over 30
reps* — directly comparable to the one the account really has.

**Three things it is not, all of which the view has to say out loud.**

  - Entry times are held fixed. A row is the same button presses under a
    different bracket, and those presses were conditioned on what happened next.
    It is a readout on exits, not a strategy result.
  - A rep no fill model reproduces is **dropped from every row**, so a campaign
    over an account with any such rep does not reproduce that account's real
    history. The count is reported and the view states it.
  - The as-played row is a *reconstruction* and will not exactly equal the real
    record: the real one reads a live ``min_room_usd`` verdict from the browser,
    this one re-derives the path. The real record stays the account's history;
    this row is the like-for-like baseline the other rows are measured against.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import replay_account as acct
from . import replay_whatif as whatif
from . import replays

#: The two columns every scenario is priced in — ``replay_whatif``'s own names.
#: ``clicked`` keeps the manual flattens that were made; ``forget`` drops them
#: and the bracket drags with them, so the bracket does all the work. The gap
#: between one row's two answers is what the hand on the exit was worth, and it
#: is measured in lives here rather than in dollars.
COLUMNS = ("clicked", "forget")


@dataclass(frozen=True)
class Life:
    """One life of one counterfactual campaign."""

    index: int
    reps: int
    first: str | None
    last: str | None
    end_equity: float
    outcome: str            # "blown" | "passed" | "live"
    killed_by: str | None   # the attempt that ended it, either way


@dataclass(frozen=True)
class Run:
    """One bracket, in one column, over the whole account."""

    key: str
    label: str
    column: str
    lives: list[Life]
    net_usd: float
    trades: int
    wins: int
    #: This row took the other side of every entry. It is in the ladder because
    #: it is a readout on the direction read, but it is **not a bracket** — so it
    #: is ranked apart, or "the bracket that would have saved this account" ends
    #: up being "trade the opposite way", which is a different and much weaker
    #: claim than the panel is making.
    flip: bool = False

    @property
    def record(self) -> dict:
        return {
            "passed": sum(1 for x in self.lives if x.outcome == "passed"),
            "blown": sum(1 for x in self.lives if x.outcome == "blown"),
        }

    @property
    def score(self) -> int:
        """Lives won less lives lost — what orders the table.

        Not ``passed`` first. A bracket that passed once and blew three times
        bought four accounts to clear one, and sorting it above a bracket that
        ran the whole account without dying would recommend the wrong thing at
        the top of the page. Equity breaks the tie, so among brackets that cost
        the same number of accounts the one that made money wins.
        """
        r = self.record
        return r["passed"] - r["blown"]

    def as_dict(self) -> dict:
        last = self.lives[-1] if self.lives else None
        return {
            "key": self.key, "label": self.label, "column": self.column,
            "flip": self.flip, "record": self.record, "score": self.score,
            "lives": [vars(x) for x in self.lives],
            "end_equity": round(last.end_equity, 2) if last else None,
            "net_usd": round(self.net_usd, 2),
            "trades": self.trades,
            "win_rate": round(100 * self.wins / self.trades) if self.trades else None,
        }


# --- the reps a campaign runs over -------------------------------------------


def reps(account: acct.Account, *, state: dict | None = None,
         rows: list[dict] | None = None) -> list[dict]:
    """This account's in-epoch settled sittings, oldest first.

    **In-epoch only.** The union of an account's epochs is everything from the
    first one's ``started_at`` onwards, so this is one bound rather than a scan
    of windows. What it leaves out is the reps that predate the account — on the
    funded ledger that is 84 of them, kept out on purpose when epoch 0 was minted
    (``replay_account``), because they were traded with no floor under them and
    counting them here would contradict the record printed beside it.

    **Zero-trade reps stay in.** They move no equity, but a rep is a *day*, and
    an end-of-day trailing floor banks its peak when the day changes — dropping
    them would silently move the floor for every row at once.
    """
    state = acct.load_state() if state is None else state
    epochs = acct.epochs_of(state, account)
    if not epochs:
        return []
    since = str(epochs[0].get("started_at") or "")
    rows = acct.account_attempts(account) if rows is None else rows
    return [r for r in rows
            if r.get("status") in acct.SETTLED and (r.get("created_at") or "") >= since]


def grids(rows: list[dict]) -> dict[str, dict]:
    """The cached grid for each rep that has a usable one, by attempt id.

    Reads only — pricing is a write and belongs to the finish hook and the
    backfill (``demo/whatif_backfill.py``), never to a page being looked at.
    """
    out = {}
    for r in rows:
        got = whatif.cached(r["id"], r)
        if got is not None and got.get("valid"):
            out[r["id"]] = got
    return out


#: A trade held for less than this is the one behavioural leak the manual-trade
#: audit actually found (`docs/research/`), and the same threshold the account's
#: own flags use. Kept equal to ``replay_account.FAST_TRADE_MS`` by being read
#: from it rather than restated.
FAST_TRADE_MS = acct.FAST_TRADE_MS


def rep_facts(rows: list[dict], cache: dict[str, dict]) -> list[dict]:
    """Per-rep facts the account view reads, from the replay store alone.

    The stop is the interesting one and it does not come from ``prefs``: the
    ticket sizes it off the volatility ruler at *every fill*, so a sitting runs
    a range rather than a number. The as-played row of the cached grid already
    carries that range (``replay_whatif.tick_span``), measured off the initial
    bracket each position actually opened on — which a bracket drag never
    overwrites. That inconsistency is what the fixed-stop rows in the ladder
    price against, so it belongs on the same page as them.
    """
    out = []
    for r in rows:
        played = _cell(cache.get(r["id"]) or {}, "as-played", "clicked") or {}
        trades = replays._read_json(
            replays.attempt_dir(r["id"]) / "trades.json", []) or []
        holds = [float(t["exitMs"]) - float(t["entryMs"]) for t in trades
                 if isinstance(t, dict)
                 and isinstance(t.get("exitMs"), (int, float))
                 and isinstance(t.get("entryMs"), (int, float))]
        out.append({
            "id": r["id"],
            "date": r.get("date"),
            "created_at": r.get("created_at"),
            "status": r.get("status"),
            "trades": acct.trade_count(r),
            "net_usd": round(acct.net_usd(r), 2),
            "rewinds": len(r.get("rewinds") or []),
            "priced": r["id"] in cache,
            "stop_ticks": played.get("stop_ticks"),
            "fast_trades": sum(1 for h in holds if h < FAST_TRADE_MS),
            "held": len(holds),
        })
    return out


def coverage(rows: list[dict], cache: dict[str, dict]) -> dict:
    """How much of this account can be re-priced at all."""
    traded = [r for r in rows if acct.trade_count(r) > 0]
    priced = [r for r in traded if r["id"] in cache]
    return {
        "reps": len(rows),
        "traded": len(traded),
        "priced": len(priced),
        "unpriced": len(traded) - len(priced),
        "flat": len(rows) - len(traded),
    }


# --- the walk ----------------------------------------------------------------


def _cell(grid: dict, key: str, column: str) -> dict | None:
    for row in grid.get("rows") or []:
        if row.get("key") == key:
            return row.get(column)
    return None


def _counterfactual(row: dict, cell: dict) -> dict:
    """One rep as this bracket would have traded it, shaped for ``walk``.

    Everything the walk reads comes off the row: ``net_usd`` and ``trades`` from
    the summary, the booked path from ``pnls``, the unrealised one from
    ``equity_path``. ``min_room_usd`` is deliberately absent — it is a verdict
    against a floor and a bracket that was never traded has no such verdict; the
    path is what replaces it (``replay_account.equity_path``).
    """
    return {
        "id": row["id"],
        "date": row.get("date"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "summary": {
            "trades": cell["n"],
            "net_usd": cell["net"],
            "peak_usd": cell.get("peak_usd"),
            "trough_usd": cell.get("trough_usd"),
        },
        "pnls": cell.get("pnls") or [],
        "equity_path": cell.get("equity_path"),
    }


def _flat(row: dict) -> dict:
    """A rep with no trades: a day that happened and moved nothing."""
    return {
        "id": row["id"],
        "date": row.get("date"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "summary": {"trades": 0, "net_usd": 0.0},
        "pnls": [],
        "equity_path": None,
    }


def scenario_rows(rows: list[dict], cache: dict[str, dict],
                  key: str, column: str) -> list[dict] | None:
    """Every rep as this bracket would have traded it, in order.

    None when the ladder has no such row, which is how a stale cache announces
    itself rather than quietly returning a short campaign.
    """
    out = []
    for r in rows:
        if acct.trade_count(r) == 0:
            out.append(_flat(r))
            continue
        grid = cache.get(r["id"])
        if grid is None:
            continue                      # unpriceable: dropped from every row
        cell = _cell(grid, key, column)
        if cell is None:
            return None
        out.append(_counterfactual(r, cell))
    return out


def run(rows: list[dict], numbers: acct.Numbers, trailing: str,
        *, key: str, label: str, column: str, flip: bool = False) -> Run:
    """Walk one bracket straight through the account, re-buying as it dies.

    ``walk`` stops at the first ending either way and reports what it counted, so
    a life is one call and the next starts where it left off. The killing rep is
    itself counted (``Walk.counted`` is appended to before the check), which is
    what makes ``len(counted)`` the right amount to advance by — a life that
    ended on rep 11 must not hand rep 11 to its successor as well.
    """
    lives: list[Life] = []
    i = 0
    while i < len(rows):
        w = acct.walk(rows[i:], numbers=numbers, trailing=trailing)
        n = len(w.counted)
        if n == 0:
            break                          # cannot happen; never loop forever on it
        ended = w.death or w.passed or {}
        lives.append(Life(
            index=len(lives),
            reps=n,
            first=rows[i].get("date"),
            last=rows[i + n - 1].get("date"),
            end_equity=w.equity,
            outcome="blown" if w.death else ("passed" if w.passed else "live"),
            killed_by=ended.get("attempt_id"),
        ))
        if not w.over:
            break
        i += n

    pnls = [p for r in rows for p in acct.trade_pnls(r)]
    return Run(key=key, label=label, column=column, lives=lives,
               net_usd=sum(pnls), trades=len(pnls),
               wins=sum(1 for p in pnls if p > 0), flip=flip)


def report(account: acct.Account, *, state: dict | None = None,
           rows: list[dict] | None = None) -> dict:
    """Every bracket in the ladder, both columns, walked over this account."""
    state = acct.load_state() if state is None else state
    rows = reps(account, state=state, rows=rows)
    cache = grids(rows)
    out = []
    for preset in whatif.PRESETS:
        key, label = preset["key"], preset["label"]
        flip = bool(preset["spec"].get("flip"))
        for column in COLUMNS:
            scen = scenario_rows(rows, cache, key, column)
            if scen is None:
                continue
            out.append(run(scen, account.numbers, account.template.trailing,
                           key=key, label=label, column=column, flip=flip).as_dict())
    # Ordered here rather than in the view, so the answer at the top of the page
    # is one decision made in one place — see ``Run.score``.
    #
    # **Reversed rows sort last whatever they scored.** They win often, and on a
    # losing account they win by a mile — taking the other side of every entry
    # will do that. But this panel is about exits, and a table that answered
    # "which bracket would have saved this account" with "trade the opposite
    # direction" would be answering a question nobody asked with evidence that
    # does not support it. They stay, below the line, labelled.
    out.sort(key=lambda r: (r["flip"], -r["score"], -(r["end_equity"] or 0)))
    return {
        "account": account.as_dict(),
        "coverage": coverage(rows, cache),
        "reps": rep_facts(rows, cache),
        "runs": out,
        "grid_version": whatif.GRID_VERSION,
    }


# --- the rep that ended a life -----------------------------------------------


def rep_verdicts(account: acct.Account, attempt_id: str,
                 *, state: dict | None = None,
                 rows: list[dict] | None = None) -> dict:
    """Which brackets would have survived the rep that ended a life.

    The aggregate above says which bracket wins over thirty reps; this says what
    happened on the one that mattered. Every row starts from the **same** state
    the real account was in when that rep opened — the real reps before it,
    walked as they really were — so the answers differ only in the bracket, which
    is the only way the comparison means anything.
    """
    state = acct.load_state() if state is None else state
    rows = reps(account, state=state, rows=rows)
    at = next((i for i, r in enumerate(rows) if r["id"] == attempt_id), None)
    if at is None:
        raise ValueError(f"{attempt_id} is not an in-epoch rep on {account.id}")

    # The account as it stood when that rep opened, walked over the **real**
    # reps before it — re-buying as it goes, so a rep late in a much-blown
    # account opens against its own life's floor and not the first one's. A
    # fresh `Walk` is what a newly bought account looks like, which is also the
    # right answer when the rep is the first of a life (or the first of all).
    fresh = lambda: acct.Walk(account.numbers)  # noqa: E731
    before, i = fresh(), 0
    while i < at:
        w = acct.walk(rows[i:at], numbers=account.numbers,
                      trailing=account.template.trailing)
        if not w.over:
            before = w      # the life that rep belongs to is still running
            break
        i += len(w.counted)
        before = fresh()    # it ended before we got there; the next one opens new

    cache = grids([rows[at]])
    grid = cache.get(attempt_id)
    verdicts = []
    if grid is not None:
        for preset in whatif.PRESETS:
            for column in COLUMNS:
                cell = _cell(grid, preset["key"], column)
                if cell is None:
                    continue
                one = _counterfactual(rows[at], cell)
                w = acct.walk([one], numbers=account.numbers,
                              trailing=account.template.trailing, seed=before)
                verdicts.append({
                    "key": preset["key"], "label": preset["label"], "column": column,
                    "survived": w.death is None,
                    "net_usd": cell["net"],
                    "end_equity": round(w.equity, 2),
                    "floor": round(w.floor, 2),
                })
    return {
        "attempt_id": attempt_id,
        "date": rows[at].get("date"),
        "opened_at": round(before.equity, 2),
        "floor": round(before.floor, 2),
        "priced": grid is not None,
        "verdicts": verdicts,
    }


__all__ = ["COLUMNS", "Life", "Run", "reps", "grids", "coverage",
           "scenario_rows", "run", "report", "rep_verdicts"]
