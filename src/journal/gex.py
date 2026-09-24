"""Dealer gamma regime, off the banked Cboe books.

The math used to live in ``demo/gex_demo.py``; it moved here when the Live chart
started asking the same question the demo page answers, so the page and the badge
cannot drift into two different definitions of the flip.

**Why a live badge needs no options feed.** Open interest is published once by the
OCC, pre-open, and then frozen for the session. So the entire net-GEX-vs-spot
curve is knowable at 08:00 and intraday is an O(1) lookup against price, not a
Black-Scholes sweep. The Live chart fetches this once per session and interpolates
per tick in the browser.

**The futures problem, and why there is no basis constant here.** The books are
cash instruments (NDX, QQQ); the Live chart trades NQ futures. NQ = NDX + basis,
and basis is carry — it drifts with rates, dividends and time to expiry, and there
is no live NDX quote in this repo to measure it against. Rather than hardcode a
number that silently rots, the mapping is done in **ratio space, each instrument
against its own prior close**:

    r        = NQ_live / NQ_prior_rth_close
    NDX_impl = ndx_ref * r

Basis cancels, because it appears in both the numerator and the reference of its
own instrument. The residual error is only the drift of basis *within* a session,
which is a rounding error next to the ±6% grid this curve spans.

That works because the collector runs pre-open (03:51 ET on the day this was
written): the cash index is shut, so the book's ``current_price`` **is** the prior
close, frozen, exact. ``is_pre_open`` on the payload says whether that held for
the book actually served; when it did not, the anchor is approximate and the
caller is told rather than left to assume.

NOTHING HERE IS VALIDATED. No historical GEX exists in this repo, so no A/B has
been run and none can be until the collector has banked ~6 months. This is a
context read, not a signal. Do not trade the walls — level-bounce geometry is
nulled four times over here (stable-level S/R, VAH snap, LVN retrace, aVWAP
reclaim), and the Black-Scholes recompute lands 4.6% (QQQ) / 8.8% (NDX) off
Cboe's own greeks, which is fine for a binary regime read and not fine for
levels.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np

from .config import CACHE_DIR

GEX_CACHE = CACHE_DIR / "gex"
_ET = ZoneInfo("America/New_York")

GRID_PCT = 0.06          # spot grid half-width, ±6%
GRID_N = 241             # points across that grid
SESSION_H = 6.5          # RTH hours, 09:30 -> 16:00 ET
MULT = 100               # contract multiplier (both QQQ and NDX options)

OCC_RX = re.compile(r"^([A-Z_]+)(\d{6})([CP])(\d{8})$")


# --- Books ----------------------------------------------------------------
def banked(sym: str) -> list[date]:
    """Every session banked for this symbol, oldest first."""
    out = []
    for p in GEX_CACHE.glob(f"{sym}_*.json"):
        try:
            out.append(date.fromisoformat(p.stem.split("_", 1)[1]))
        except ValueError:
            continue
    return sorted(out)


def load(sym: str, day: date | None = None) -> tuple[dict, date] | None:
    """The book for ``day``, or the most recent one banked. None if nothing is."""
    days = banked(sym)
    if not days:
        return None
    if day is None:
        day = days[-1]
    elif day not in days:
        earlier = [d for d in days if d <= day]
        if not earlier:
            return None
        day = earlier[-1]
    try:
        return json.loads((GEX_CACHE / f"{sym}_{day.isoformat()}.json").read_text()), day
    except (json.JSONDecodeError, OSError):
        return None


def contracts(raw: dict, asof: date) -> tuple[list, float, str]:
    """(K, dte, iv, oi, sign) rows for every LIVE contract with OI and IV.

    Expiries are filtered to DTE >= 0. The pre-open snapshot still carries
    yesterday's expired contracts with stale gamma attached, and summing them
    naively overstated net GEX by 36% the first time round.
    """
    d = raw["data"]
    spot = d["current_price"]
    rows, expired, skipped = [], 0, 0
    for o in d["options"]:
        m = OCC_RX.match(o["option"])
        if not m:
            skipped += 1
            continue
        _, ymd, cp, strike = m.groups()
        exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
        dte = (exp - asof).days
        if dte < 0:
            expired += 1
            continue
        if not o["open_interest"] or not o["iv"]:
            continue
        rows.append((int(strike) / 1000.0, dte, o["iv"], o["open_interest"],
                     1 if cp == "C" else -1))
    return rows, spot, f"{len(rows):,} live · {expired} expired dropped · {skipped} unparsed"


# --- Gamma ----------------------------------------------------------------
def _npdf(x: float) -> float:
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def _tenor(dte: int, hours_in: float) -> float:
    """Years to expiry, with the remainder of today counted in hours."""
    return max((dte + (SESSION_H - hours_in) / 24.0) / 365.0, 1e-6)


def gamma_bs(S: float, K: float, T: float, iv: float) -> float:
    v = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / v
    return _npdf(d1) / (S * v)


def _cols(rows: list, hours_in: float) -> tuple[np.ndarray, ...]:
    """The rows as columns (K, T, iv, oi, sign) — `gamma_bs` over a whole book at
    once. A book is ~16k contracts and the curve asks at 241 spots, which a
    Python loop took ~2s per book to walk; the arithmetic is the same."""
    a = np.asarray(rows, dtype=float).reshape(-1, 5)
    T = np.maximum((a[:, 1] + (SESSION_H - hours_in) / 24.0) / 365.0, 1e-6)
    return a[:, 0], T, a[:, 2], a[:, 3], a[:, 4]


def _gamma_dollars(S: np.ndarray, K, T, iv, oi) -> np.ndarray:
    """Dollar-gamma per 1% move per contract row, at each spot in ``S`` (one row
    of the result per spot)."""
    S = np.asarray(S, dtype=float).reshape(-1, 1)
    v = iv * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv * iv * T) / v
    g = np.exp(-d1 * d1 / 2.0) / math.sqrt(2.0 * math.pi) / (S * v)
    return g * oi * MULT * S * S * 0.01


def net_gex_curve(rows: list, spots: list[float], hours_in: float) -> list[float]:
    """`net_gex` at every spot in one pass."""
    if not rows:
        return [0.0] * len(spots)
    K, T, iv, oi, sgn = _cols(rows, hours_in)
    return [float(x) for x in (_gamma_dollars(spots, K, T, iv, oi) * sgn).sum(axis=1)]


def net_gex(rows: list, S: float, hours_in: float) -> float:
    """Dollar-gamma per 1% move. Calls positive, puts negative (naive convention)."""
    return net_gex_curve(rows, [S], hours_in)[0]


def by_strike(rows: list, S: float, hours_in: float) -> list[dict]:
    """Per-strike dollar-gamma, split call/put — the profile bars."""
    if not rows:
        return []
    K, T, iv, oi, sgn = _cols(rows, hours_in)
    g = _gamma_dollars([S], K, T, iv, oi)[0]
    agg: dict[float, list[float]] = {}
    for k, gi, sg in zip(K.tolist(), g.tolist(), sgn.tolist()):
        a = agg.setdefault(k, [0.0, 0.0])
        a[0 if sg > 0 else 1] += gi
    return [{"k": k, "c": v[0], "p": v[1], "net": v[0] - v[1]}
            for k, v in sorted(agg.items())]


def flip_of(curve: list[dict]) -> float | None:
    """Lowest spot where the curve crosses from negative to positive."""
    for a, b in zip(curve, curve[1:]):
        if a["g"] < 0 <= b["g"]:
            span = b["g"] - a["g"]
            t = 0.0 if span == 0 else -a["g"] / span
            return a["s"] + t * (b["s"] - a["s"])
    return None


# --- The live read --------------------------------------------------------
def regime(sym: str, *, day: date | None = None, hours_in: float = 0.0) -> dict | None:
    """The curve in **ratio space**, ready to be looked up against a futures price.

    ``curve`` is a list of ``[r, g]`` where ``r`` is spot as a fraction of the
    book's own reference close and ``g`` is net GEX in $B. Everything downstream
    — the badge, the flip, the walls — is expressed as a ratio precisely so the
    caller never needs to know what a basis is.

    Returns None when nothing is banked, so the caller can fail soft. A missing
    book must never be an error on a trading surface.
    """
    got = load(sym, day)
    if got is None:
        return None
    raw, book_day = got
    return _regime(sym, raw, book_day, book_day.isoformat(), hours_in)


def regime_at(book: "BookRef", session: date, *, hours_in: float = 0.0) -> dict | None:
    """``regime`` for one stored book, read as it stands at ``session``'s open —
    days to expiry counted from the session, not from when the book was saved."""
    raw = read_book(book.path)
    if raw is None:
        return None
    return _regime(book.sym, raw, session, book.key, hours_in)


def _regime(sym: str, raw: dict, asof: date, label: str, hours_in: float) -> dict | None:
    rows, ref, note = contracts(raw, asof)
    if not rows or not ref:
        return None

    lo, hi = ref * (1 - GRID_PCT), ref * (1 + GRID_PCT)
    grid = [lo + (hi - lo) * i / (GRID_N - 1) for i in range(GRID_N)]
    pts = [{"s": s, "g": g} for s, g in zip(grid, net_gex_curve(rows, grid, hours_in))]
    flip = flip_of(pts)

    strikes = [r for r in by_strike(rows, ref, hours_in)
               if lo * 0.97 <= r["k"] <= hi * 1.03]
    call_wall = max(strikes, key=lambda r: r["c"])["k"] if strikes else None
    put_wall = max(strikes, key=lambda r: r["p"])["k"] if strikes else None

    stamp = raw.get("timestamp") or ""
    # When `current_price` was quoted (ET, clamped to the cash session) — the
    # instant a futures anchor has to be read at. `is_pre_open` is kept for the
    # badge: true when that quote is a prior close rather than a live print.
    qt = quote_time(raw)
    pre_open = qt is not None and qt.time() == time(16, 0)

    return {
        "sym": sym,
        "book_date": label,
        "cdn_stamp": stamp,
        "quote_et": qt.isoformat() if qt else None,
        "is_pre_open": pre_open,
        "ref": round(ref, 2),
        "curve": [[round(p["s"] / ref, 5), round(p["g"] / 1e9, 4)] for p in pts],
        "at_ref": round(net_gex(rows, ref, hours_in) / 1e9, 4),
        "flip": round(flip, 2) if flip else None,
        "flip_r": round(flip / ref, 5) if flip else None,
        "call_wall": call_wall,
        "call_wall_r": round(call_wall / ref, 5) if call_wall else None,
        "put_wall": put_wall,
        "put_wall_r": round(put_wall / ref, 5) if put_wall else None,
        "n_live": len(rows),
        "note": note,
    }


def at(curve: list[list[float]], r: float) -> float | None:
    """Net GEX ($B) at ratio ``r``, linearly interpolated. None outside the grid.

    None rather than a clamped edge value: past ±6% the book has essentially no
    open interest left and the number would be a confident-looking zero.
    """
    if not curve or r < curve[0][0] or r > curve[-1][0]:
        return None
    for a, b in zip(curve, curve[1:]):
        if a[0] <= r <= b[0]:
            span = b[0] - a[0]
            t = 0.0 if span == 0 else (r - a[0]) / span
            return a[1] + t * (b[1] - a[1])
    return None


# --- Books by session, and the levels drawn on a chart ---------------------
#
# A file is named for the day the collector *banked* it, on this box's clock
# (UTC+8) — not for the session it describes. And the Cboe `timestamp` inside it
# is **UTC**, not ET: `NDX_2026-09-23.json` is stamped "2026-09-22 15:59:41",
# which is 11:59 ET, and its underlying's `last_trade_time` is 11:44:40 ET — the
# CDN is 15 minutes delayed. So the books are *midday* snapshots (the box banks
# the first one after its own midnight), carrying that morning's OCC open
# interest and an index price from mid-session.
#
# Two consequences, both load-bearing:
#   - a chart picks a book by its stamp, never its filename: the latest one
#     stamped before the session it is drawing opened (Globex, 18:00 ET the
#     evening before) — what a trader could have read before the first print;
#   - the futures anchor is the futures price *at the book's quote time*
#     (`quote_time`), not a close. Anchoring an 11:44 index price on the 16:00
#     futures close shifted every level by the afternoon's move — ~130 points
#     on 2026-09-22.

_STAMP_RX = re.compile(r'"timestamp":\s*"([^"]+)"')
_stamps: dict[Path, tuple[float, datetime | None]] = {}

WALLS_PER_SIDE = 3
CLUSTER_PCT = 0.0015     # strikes this close (fraction of price) are one wall


def book_stamp(sym: str, day: date) -> datetime | None:
    """The Cboe stamp (naive **UTC**) inside the book banked on ``day``. Read off
    the head of the file — `timestamp` is its first key — and cached per mtime."""
    p = GEX_CACHE / f"{sym}_{day.isoformat()}.json"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    hit = _stamps.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        m = _STAMP_RX.search(fh.read(256))
    try:
        st = datetime.fromisoformat(m.group(1)) if m else None
    except ValueError:
        st = None
    _stamps[p] = (mt, st)
    return st


# Intraday snapshots (tools/gex_collect.py): slim, gzipped, one file per Cboe
# publish, named by that stamp in UTC so the index never opens a file to date it.
SNAP_DIR = GEX_CACHE / "snap"


@dataclass(frozen=True)
class BookRef:
    """One stored book — a legacy daily file or an intraday snapshot."""
    sym: str
    path: Path
    stamp: datetime          # Cboe publish stamp, naive UTC

    @property
    def key(self) -> str:
        return self.path.name.split(".", 1)[0]


def snap_path(sym: str, stamp: datetime) -> Path:
    return SNAP_DIR / sym / f"{sym}_{stamp:%Y%m%dT%H%M%S}.json.gz"


def read_book(path: Path) -> dict | None:
    """A stored book, whichever format it was saved in. None if unreadable."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, EOFError):
        return None


def books(sym: str) -> list[BookRef]:
    """Every stored book for ``sym``, oldest stamp first. A stamp held twice (a
    daily file and a snapshot of the same publish) is listed once."""
    seen: dict[datetime, BookRef] = {}
    for d in banked(sym):
        st = book_stamp(sym, d)
        if st is not None:
            seen.setdefault(st, BookRef(sym, GEX_CACHE / f"{sym}_{d.isoformat()}.json", st))
    for p in (SNAP_DIR / sym).glob(f"{sym}_*.json.gz"):
        try:
            st = datetime.strptime(p.name.split("_", 1)[1].split(".", 1)[0], "%Y%m%dT%H%M%S")
        except ValueError:
            continue
        seen[st] = BookRef(sym, p, st)
    return [seen[k] for k in sorted(seen)]


def _utc(day: date, t: time) -> datetime:
    """An ET wall-clock moment as naive UTC — the stamps' own clock."""
    return (datetime.combine(day, t, tzinfo=_ET)
            .astimezone(timezone.utc).replace(tzinfo=None))


def book_for_session(sym: str, session: date) -> BookRef | None:
    """The latest book stamped before ``session``'s Globex open (18:00 ET the
    evening before) — what could have been read before the first print. None
    when the collector had nothing that early."""
    cutoff = _utc(session - timedelta(days=1), time(18, 0))
    best = None
    for b in books(sym):
        if b.stamp < cutoff:
            best = b
    return best


def books_during(sym: str, session: date) -> list[BookRef]:
    """Books published while ``session`` was running (Globex open → 17:00 ET),
    oldest first — the intraday updates a replay steps through, each drawn only
    from its own stamp on."""
    lo = _utc(session - timedelta(days=1), time(18, 0))
    hi = _utc(session, time(17, 0))
    return [b for b in books(sym) if lo <= b.stamp < hi]


def quote_time(raw: dict) -> datetime | None:
    """When the book's ``current_price`` was actually quoted, naive ET, clamped to
    a moment the cash index was printing — the instant the futures anchor must be
    read at for the ratio to be like-for-like.

    ``last_trade_time`` is the underlying's own (ET) quote time. After the close
    the index stops at its 16:00 settle while the futures keep trading, so a
    later time clamps to 16:00; before the open (or on a weekend) the index is
    still the prior close, so it walks back to the last weekday's 16:00."""
    s = (raw.get("data") or {}).get("last_trade_time")
    try:
        t = datetime.fromisoformat(s) if s else None
    except ValueError:
        return None
    if t is None:
        return None
    close = time(16, 0)
    if t.weekday() < 5 and time(9, 30) <= t.time() <= close:
        return t
    d = t.date() if (t.weekday() < 5 and t.time() > close) else t.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime.combine(d, close)


# Which expiries a level is built from, by days to expiry *from the session being
# drawn*. "0dte" is the options expiring that session — NDX and QQQ list one every
# weekday — and they carry ~20% of the gamma within 1% of price on 1–2% of the
# open interest, which is why a 0DTE-weighted read puts its walls near price.
# This is only the 0DTE *open interest* the prior session left behind: the
# same-day flow that makes 0DTE matter lives in intraday volume, which a
# once-a-day book cannot see.
EXPIRY_MAX_DTE = {"all": None, "week": 7, "0dte": 0}


@lru_cache(maxsize=1024)
def _levels(path: str, mtime: float, session: date, expiry: str) -> dict | None:
    raw = read_book(Path(path))
    if raw is None:
        return None
    # Days to expiry are counted from the *session*, not the day the book was
    # banked: a book two days old drawn under today's session would otherwise
    # call yesterday's expired contracts "0DTE".
    rows, ref, _ = contracts(raw, session)
    cap = EXPIRY_MAX_DTE[expiry]
    if cap is not None:
        rows = [r for r in rows if r[1] <= cap]
    if not rows or not ref:
        return None
    qt = quote_time(raw)
    lo, hi = ref * (1 - GRID_PCT), ref * (1 + GRID_PCT)
    grid = [lo + (hi - lo) * i / (GRID_N - 1) for i in range(GRID_N)]
    flip = flip_of([{"s": s, "g": g} for s, g in zip(grid, net_gex_curve(rows, grid, 0.0))])
    strikes = [r for r in by_strike(rows, ref, 0.0) if lo <= r["k"] <= hi]

    def pick(leg: str) -> list[dict]:
        # Call resistance (C1..) is the heaviest *call* gamma, put support (P1..)
        # the heaviest *put* gamma — the split the retail GEX tools draw, and the
        # one a scalper reads as "resistance above, support below". Heaviest
        # first; a strike within CLUSTER_PCT of one already taken is the same
        # wall (NDX lists 29475 and 29500: one hedge, not two).
        top = max((r[leg] for r in strikes), default=0.0) or 1.0
        taken: list[dict] = []
        for r in sorted(strikes, key=lambda r: r[leg], reverse=True):
            if r[leg] <= 0:
                break
            if any(abs(r["k"] - t["k"]) <= CLUSTER_PCT * r["k"] for t in taken):
                continue
            taken.append(r)
            if len(taken) == WALLS_PER_SIDE:
                break
        return [{"k": r["k"], "k_r": round(r["k"] / ref, 6), "rank": i,
                 "weight": round(r[leg] / top, 4),
                 "gex_b": round(r[leg] / 1e9, 4)} for i, r in enumerate(taken)]

    return {
        "ref": ref,
        "quote_et": qt.isoformat() if qt else None,
        "flip": flip,
        "flip_r": round(flip / ref, 6) if flip else None,
        "at_ref_b": round(net_gex(rows, ref, 0.0) / 1e9, 4),
        "call_walls": pick("c"),
        "put_walls": pick("p"),
    }


def levels(sym: str, day: date, session: date | None = None,
           expiry: str = "all") -> dict | None:
    """The flip, the call walls and the put walls for the book banked on ``day``
    as it stands at ``session``'s open (default: the banked day), in ratio space
    against its own spot. ``expiry`` narrows it to the contracts a near-dated read
    looks at (see ``EXPIRY_MAX_DTE``). Call walls rank strikes by call
    dollar-gamma at spot, put walls by put dollar-gamma."""
    st = book_stamp(sym, day)
    if st is None:
        return None
    return levels_at(BookRef(sym, GEX_CACHE / f"{sym}_{day.isoformat()}.json", st),
                     session or day, expiry)


def levels_at(book: BookRef, session: date, expiry: str = "all") -> dict | None:
    """``levels`` for any stored book (daily file or intraday snapshot)."""
    if expiry not in EXPIRY_MAX_DTE:
        raise ValueError(f"unknown expiry filter {expiry!r}")
    try:
        mt = book.path.stat().st_mtime
    except OSError:
        return None
    return _levels(str(book.path), mt, session, expiry)
