"""Dealer-gamma levels for the replay charts (``journal.gex``).

The Live chart's regime chip reads one book and one number. This draws the
levels themselves — the zero-gamma flip, the call walls (resistance) and the put
walls (support) — from **both** books, NDX and QQQ, per session, on the futures chart's own
price axis. The two books are two different sets of traders; a level both carry
is drawn as one, and the flip is drawn as the band between the two books' flips
rather than a line, because they routinely disagree by 0.5–1.5% and a single
line would claim a precision the data does not have.

Context only. Nothing here is validated (see the GEX memory / research page),
and the Black-Scholes recompute lands 4.6% (QQQ) / 8.8% (NDX) off Cboe's own
greeks. The chart labels the layer accordingly.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from journal import gex as gexmod
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS, ET_TZ

from ..session_chart import _epoch_local
from .live import gex_anchor

router = APIRouter()

BOOKS = ("NDX", "QQQ")
MAX_SPAN_DAYS = 31
# Two books' walls within this fraction of price are one level. QQQ strikes are
# $1 apart near the money (~0.14% of price), so anything tighter would call the
# same strike two levels because of where the grid happens to fall.
AGREE_PCT = gexmod.CLUSTER_PCT


def _tz(tz: str | None):
    if tz in DISPLAY_TZS:
        return DISPLAY_TZS[tz]
    try:
        return ZoneInfo(tz or "")
    except (ZoneInfoNotFoundError, ValueError):
        return DISPLAY_TZS[DEFAULT_DISPLAY_TZ]


def _wall(day: date, t: time, disp_tz) -> int:
    """An ET wall-clock moment as the bars' own axis (epoch of the naive wall
    clock in the display zone)."""
    et = pd.Timestamp(datetime.combine(day, t), tz=ET_TZ)
    return int(_epoch_local(et.tz_convert("UTC"), disp_tz))


def book_levels(symbol: str, session: date, book: str, expiry: str = "all",
                pick: gexmod.BookRef | None = None) -> dict:
    """One book's levels for one session, in the futures contract's points —
    from ``pick`` if given (an intraday snapshot), else the book in force at the
    session's open. ``{"available": False, "reason": ...}`` on any missing input."""
    pick = pick or gexmod.book_for_session(book, session)
    if pick is None:
        return {"available": False, "reason": f"no {book} book before this session"}
    lv = gexmod.levels_at(pick, session, expiry)
    if lv is None:
        why = "unreadable" if expiry == "all" else f"has no {expiry} contracts open"
        return {"available": False, "reason": f"{book} book {pick.key} {why}"}
    anchor = gex_anchor(symbol, lv["quote_et"])
    if anchor is None:
        return {"available": False,
                "reason": f"no {symbol} print at {lv['quote_et']} ET to anchor the {book} book on"}
    px_ref, ref_day, _ = anchor

    def px(r: float | None) -> float | None:
        return round(px_ref * r, 2) if r else None

    return {
        "available": True,
        "book": book,
        "banked": pick.key,
        "stamp_utc": pick.stamp.isoformat(sep=" "),
        "quote_et": lv["quote_et"],
        "stale_days": (session - date.fromisoformat(lv["quote_et"][:10])).days,
        "px_ref": round(px_ref, 2),
        "px_ref_date": ref_day.isoformat(),
        # Futures over the underlying at the same instant — ~1.01 against NDX
        # (carry), ~41.5 against QQQ. Shipped so a wrong anchor shows up as an
        # absurd ratio rather than a plausible-looking level.
        "implied_ratio": round(px_ref / lv["ref"], 5),
        "flip_px": px(lv["flip_r"]),
        # Net GEX at the book's own spot: > 0 long gamma (dealers damp moves),
        # < 0 short gamma (dealers chase them).
        "at_ref_b": lv["at_ref_b"],
        "walls": [
            {"px": px(w["k_r"]), "strike": w["k"], "kind": kind, "rank": w["rank"],
             "weight": w["weight"], "gex_b": w["gex_b"]}
            for kind, key in (("call", "call_walls"), ("put", "put_walls"))
            for w in lv[key]
        ],
    }


def merge_walls(per_book: dict[str, dict]) -> list[dict]:
    """Walls from every available book on one list. A wall within ``AGREE_PCT``
    of another book's wall *of the same kind* (call with call, put with put) is
    folded into it and drawn once, at the NDX price (NQ tracks NDX; QQQ is the
    second opinion), carrying both names, the heavier weight and the better rank."""
    out: list[dict] = []
    for book in BOOKS:
        b = per_book.get(book)
        if not b or not b.get("available"):
            continue
        for w in b["walls"]:
            if w["px"] is None:
                continue
            twin = next((o for o in out if book not in o["books"] and o["kind"] == w["kind"]
                         and abs(o["px"] - w["px"]) <= AGREE_PCT * w["px"]), None)
            if twin:
                twin["books"].append(book)
                twin["weight"] = max(twin["weight"], w["weight"])
                twin["rank"] = min(twin["rank"], w["rank"])
                twin[f"strike_{book}"] = w["strike"]
            else:
                out.append({"px": w["px"], "kind": w["kind"], "rank": w["rank"],
                            "weight": w["weight"], "books": [book],
                            f"strike_{book}": w["strike"]})
    return sorted(out, key=lambda w: w["px"])


# Two books published within this long of each other are one update — NDX and
# QQQ come off the CDN seconds apart, and two steps a few seconds apart are one.
STEP_GROUP = timedelta(minutes=2)


def _step(symbol: str, session: date, expiry: str, picks: dict) -> dict:
    per_book = {b: book_levels(symbol, session, b, expiry, picks.get(b)) for b in BOOKS}
    flips = [per_book[b]["flip_px"] for b in BOOKS
             if per_book[b].get("available") and per_book[b]["flip_px"]]
    return {
        "books": per_book,
        "flip_lo": min(flips) if flips else None,
        "flip_hi": max(flips) if flips else None,
        "walls": merge_walls(per_book),
    }


def session_steps(symbol: str, session: date, expiry: str, disp_tz) -> list[dict]:
    """The session as a series of steps: the books in force at its open, then one
    step per update published while it ran (``journal.gex.books_during``), each
    drawn from its own publish stamp — never before, so a replay cannot see an
    update ahead of the tape. A book not republished at a step carries over."""
    start = _wall(session - timedelta(days=1), time(18, 0), disp_tz)
    end = _wall(session, time(17, 0), disp_tz)
    picks = {b: gexmod.book_for_session(b, session) for b in BOOKS}
    steps = [{"from": start, **_step(symbol, session, expiry, picks)}]

    updates = sorted((ref.stamp, b, ref) for b in BOOKS
                     for ref in gexmod.books_during(b, session))
    i = 0
    while i < len(updates):
        group = [updates[i]]
        while i + 1 < len(updates) and updates[i + 1][0] - group[0][0] <= STEP_GROUP:
            i += 1
            group.append(updates[i])
        i += 1
        for _, b, ref in group:
            picks[b] = ref
        at = group[-1][0]  # the later stamp: the step is complete only then
        steps.append({"from": int(_epoch_local(pd.Timestamp(at, tz="UTC"), disp_tz)),
                      **_step(symbol, session, expiry, picks)})
    for a, b in zip(steps, steps[1:]):
        a["to"] = b["from"]
    steps[-1]["to"] = end
    return steps


@router.get("/gex/levels")
def gex_levels(
    symbol: str = Query(..., description="The tape's futures contract, e.g. NQZ6"),
    start: str = Query(..., description="YYYY-MM-DD, first session, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, last session, inclusive"),
    tz: str | None = Query(None),
    expiry: str = Query("all", pattern="^(all|week|0dte)$",
                        description="all expiries, ≤7 days, or the session's own 0DTE"),
) -> dict:
    """Per session: where it runs on the bars' axis (Globex open → 17:00 ET), and
    its ``steps`` — each book's flip and walls in *symbol*'s points, once for the
    open and again for every intraday update. Weekends are skipped; a session
    with no book behind it is listed with its reason, not dropped, so the legend
    can say why nothing drew."""
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if d1 < d0 or (d1 - d0).days > MAX_SPAN_DAYS:
        raise HTTPException(422, f"range must be 0..{MAX_SPAN_DAYS} days")
    disp_tz = _tz(tz)

    sessions = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            sessions.append({
                "date": d.isoformat(),
                "from": _wall(d - timedelta(days=1), time(18, 0), disp_tz),
                "to": _wall(d, time(17, 0), disp_tz),
                "steps": session_steps(symbol, d, expiry, disp_tz),
            })
        d += timedelta(days=1)
    return {"symbol": symbol, "expiry": expiry, "agree_pct": AGREE_PCT, "sessions": sessions}
