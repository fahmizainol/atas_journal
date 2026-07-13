"""Databento trade ticks with on-disk parquet caching.

Mirrors ``databento_client`` (per-symbol, per-day parquet + lru_cache over the
read) but fetches the ``trades`` schema, cached per session in two segments:

  - ``rth``  09:30 → 16:00 ET — what every strategy reads today;
  - ``on``   18:00 (prev calendar day) → 09:30 ET — the Globex overnight,
    fetched only for strategies that declare ``session="globex"``.

Segments are separate files so adding the overnight later never re-buys the RTH
data already on disk: Databento bills by the data in the requested range, and
the ranges don't overlap (get_range is end-exclusive, so on/rth meet at 09:30
with no gap and no duplicate).

Every fetch here is for a *raw* contract (``NQZ5``), never Databento's continuous
symbol — but which raw contract is decided per session, so a window may span a
roll (see ``contract_for``).

That indirection is the whole point. ``NQ.v.0`` rolls on the UTC day boundary,
19:00 ET, which lands an hour *inside* the Globex segment below: on 2025-12-16
the front month flipped mid-overnight and the price gapped 225 points. Fetching
the continuous symbol would therefore splice two contracts into one session's
ticks, and a Globex-anchored VWAP drawn across that seam is nonsense. So we take
Databento's roll *date* and quantize it to our session boundary: both segments of
a session always come from one contract, and the roll falls between sessions.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import pandas as pd

from ..config import (CACHE_DIR, CONTRACT_SPECS, DATABENTO_DATASET, ET_TZ,
                      continuous_symbol, databento_key, root_symbol)
from ..databento_client import DatabentoUnavailable

TICK_CACHE_DIR = CACHE_DIR / "ticks"

# The windows we fetch and cache. Each segment is baked into its cache
# filename, so widening a window later invalidates rather than silently serves
# a short day.
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
GLOBEX_OPEN = time(18, 0)  # previous calendar day (Sunday, for Monday sessions)

TICK_COLS = ["ts_utc", "price", "size", "side"]


def session_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """RTH open/close for an ET session date, as UTC instants (DST-aware)."""
    open_et = pd.Timestamp(datetime.combine(day, RTH_OPEN), tz=ET_TZ)
    close_et = pd.Timestamp(datetime.combine(day, RTH_CLOSE), tz=ET_TZ)
    return open_et.tz_convert("UTC"), close_et.tz_convert("UTC")


def overnight_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Globex open (18:00 ET the previous calendar day) → RTH open, as UTC
    instants. Keyed by the *session* date: Monday's overnight starts Sunday."""
    open_et = pd.Timestamp(datetime.combine(day - timedelta(days=1), GLOBEX_OPEN), tz=ET_TZ)
    return open_et.tz_convert("UTC"), session_bounds_utc(day)[0]


def _cache_path(symbol: str, day: date, segment: str = "rth"):
    return TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_{segment}.parquet"


# --- roll -------------------------------------------------------------------


def rolls(contract: str) -> bool:
    """True for a root we roll ('NQ'); False for a symbol to fetch verbatim.

    Membership of CONTRACT_SPECS is the test, rather than "does this look like a
    root?", because rolling means asking Databento for a continuous symbol — and
    the only instruments we can honestly do that for are the ones we already keep
    specs for. Everything else is taken at face value: an exact contract ('NQZ5'),
    which is what every run made before the roll existed pinned and must keep
    replaying to, and a synthetic symbol under test.
    """
    return contract in CONTRACT_SPECS


def _roll_map_path(root: str):
    return TICK_CACHE_DIR / f"roll_{root}.json"


# The map is read once per process, not once per lookup: finalize() resolves a
# contract for every trade and the charts for every request, and that shouldn't
# be a disk read each time. Invalidated wherever the map is written.
_ROLL_CACHE: dict[str, dict] = {}


def _load_roll(root: str) -> dict:
    """The roll file: {"sessions": {date: symbol}, "closed": [dates]}.

    ``closed`` is the probe's negative evidence — weekdays with no RTH bars in the
    continuous series while later sessions have them, i.e. days the exchange never
    opened. An early flat-format file (dates -> symbol only) loads as sessions with
    nothing known to be closed.
    """
    if root not in _ROLL_CACHE:
        p = _roll_map_path(root)
        raw = json.loads(p.read_text()) if p.exists() else {}
        if "sessions" not in raw:
            raw = {"sessions": raw, "closed": []}
        _ROLL_CACHE[root] = raw
    c = _ROLL_CACHE[root]
    return {"sessions": dict(c["sessions"]), "closed": list(c["closed"])}


def _load_roll_map(root: str) -> dict[str, str]:
    return _load_roll(root)["sessions"]


def _probe_front_month(root: str, start: date, end: date) -> dict[str, str]:
    """Which raw contract was front month during each session's RTH, in [start, end].

    Sampled from Databento's own continuous symbol, so we inherit its volume roll
    rather than maintaining a roll calendar by hand. Two details carry the weight:

    - Only bars *inside RTH* are counted. The continuous symbol switches at 19:00
      ET, so a sample taken anywhere in the overnight could return either side of
      the roll; RTH (09:30-16:00 ET) sits wholly within one UTC day and is always
      unambiguous.
    - The result keys a whole session, overnight included. On the roll session the
      night is therefore bought from the *new* contract, an hour before Databento's
      own switch. That hour is real data — the new contract was trading, it just
      wasn't the volume leader yet — and it keeps the session on one contract.

    This is an ohlcv-1m pull (cents for a quarter), not a tick pull.
    """
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")

    data = dbn.Historical(key).timeseries.get_range(
        dataset=DATABENTO_DATASET, schema="ohlcv-1m", stype_in="continuous",
        symbols=[continuous_symbol(root)],
        start=session_bounds_utc(start)[0].to_pydatetime(),
        end=session_bounds_utc(end)[1].to_pydatetime(),
    )
    df = data.to_df(price_type="float", pretty_ts=True)
    if df.empty:
        return {}
    df = df.reset_index()
    ts = pd.to_datetime(df["ts_event"], utc=True)
    et = ts.dt.tz_convert(ET_TZ)
    df = df[(et.dt.time >= RTH_OPEN) & (et.dt.time < RTH_CLOSE)]
    if df.empty:
        return {}
    df = df.assign(day=et.dt.date)

    # The busiest instrument_id of each RTH — one value in practice, but a mode
    # rather than a first() means a stray print can't decide a whole session.
    per_day = df.groupby("day")["instrument_id"].agg(lambda s: s.mode().iat[0])
    ids = sorted({int(v) for v in per_day})
    res = dbn.Historical(key).symbology.resolve(
        dataset=DATABENTO_DATASET, symbols=[str(i) for i in ids],
        stype_in="instrument_id", stype_out="raw_symbol",
        start_date=start.isoformat(), end_date=(end + timedelta(days=1)).isoformat(),
    )
    sym = {int(k): v[0]["s"] for k, v in res["result"].items() if v}
    return {d.isoformat(): sym[int(i)] for d, i in per_day.items() if int(i) in sym}


def ensure_roll_map(contract: str, start: date, end: date) -> dict[str, str]:
    """Resolve and cache the front month for every session in [start, end].

    Primes the map in one pull so ``contract_for`` is a dict lookup during the run
    loop. A verbatim symbol needs no map and buys nothing.
    """
    if not rolls(contract):
        return {}
    root = root_symbol(contract)
    data = _load_roll(root)
    have, closed = data["sessions"], set(data["closed"])
    want = [d for d in session_dates(start, end) if d.isoformat() not in have]
    if not want:
        return have

    have.update(_probe_front_month(root, want[0], want[-1]))
    # A weekday with no RTH bars gets no probe result. It is still recorded — as its
    # previous session's contract, else every later lookup would re-probe Databento
    # for a day that will never resolve, and charts call this per request. Whether
    # it is also *known closed* depends on where it sits: a quiet day with a later
    # session behind it is an exchange holiday (the data continues without it), but
    # a quiet day at the end of the probe could just be data not yet published, and
    # calling that "closed" would let a run silently skip a real session.
    last_seen = max(have) if have else None
    for d in want:
        k = d.isoformat()
        if k in have:
            continue
        prior = [v for kk, v in sorted(have.items()) if kk < k]
        if not prior:
            continue
        have[k] = prior[-1]
        if last_seen and k < last_seen:
            closed.add(k)

    TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _roll_map_path(root).write_text(json.dumps({
        "sessions": dict(sorted(have.items())),
        "closed": sorted(closed),
    }, indent=1))
    _ROLL_CACHE[root] = {"sessions": dict(have), "closed": sorted(closed)}
    return have


def contract_for(contract: str, day: date) -> str:
    """The raw contract a given session trades — the one call site for the roll.

    Holidays and half-days have no RTH bars and so no map entry; they carry the
    previous mapped session forward rather than re-probing, since a day with no
    RTH is a day with no trades either way.
    """
    if not rolls(contract):
        return contract
    root = root_symbol(contract)
    m = ensure_roll_map(contract, day, day)
    key = day.isoformat()
    if key in m:
        return m[key]
    prior = [v for k, v in sorted(m.items()) if k < key]
    if prior:
        return prior[-1]
    raise RuntimeError(
        f"cannot resolve the front-month contract for {root} on {day} — "
        f"no session in the roll map at or before it")


def contract_for_cached(contract: str, day: date) -> str | None:
    """``contract_for`` minus the probe: resolve from the roll map already on
    disk, or return None. This is the resolver for GET paths (the regime router)
    — a GET must never reach Databento, and a session the map has never seen has
    no cached ticks to describe anyway."""
    if not rolls(contract):
        return contract
    m = _load_roll_map(root_symbol(contract))
    key = day.isoformat()
    if key in m:
        return m[key]
    prior = [v for k, v in sorted(m.items()) if k < key]
    return prior[-1] if prior else None


def market_closed(contract: str, day: date) -> bool:
    """True when the roll probe saw no session at all that day — a full exchange
    holiday (Christmas), as opposed to a day whose ticks are merely missing.

    This is what lets the "no ticks" guard tell the two apart: an empty pull on a
    closed day is the calendar, an empty pull on any other day is a data problem
    and must still fail the run. Deliberately conservative — a quiet day at the
    probe's trailing edge is never called closed (see ensure_roll_map), and a
    pinned contract has no probe, so no day of its window is ever skippable.
    """
    if not rolls(contract):
        return False
    return day.isoformat() in _load_roll(root_symbol(contract))["closed"]


def estimate_cost(symbol: str, start: date, end: date,
                  include_overnight: bool = False) -> float:
    """USD cost of the trades pull for [start, end], before fetching anything."""
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")
    s = overnight_bounds_utc(start)[0] if include_overnight else session_bounds_utc(start)[0]
    _, e = session_bounds_utc(end)
    return dbn.Historical(key).metadata.get_cost(
        dataset=DATABENTO_DATASET, schema="trades", stype_in="raw_symbol",
        symbols=[symbol], start=s.to_pydatetime(), end=e.to_pydatetime(),
    )


def _fetch_range(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Trade ticks for a raw contract symbol over [start, end)."""
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")

    data = dbn.Historical(key).timeseries.get_range(
        dataset=DATABENTO_DATASET, schema="trades", stype_in="raw_symbol",
        symbols=[symbol], start=start.to_pydatetime(), end=end.to_pydatetime(),
    )
    df = data.to_df(price_type="float", pretty_ts=True)
    if df.empty:
        return pd.DataFrame(columns=TICK_COLS)

    df = df.reset_index()
    # ts_event is the exchange's own stamp; ts_recv is when Databento saw it.
    # Sequencing the sim off ts_event keeps fills in true exchange order.
    ts_col = "ts_event" if "ts_event" in df.columns else "ts_recv"
    df = df.rename(columns={ts_col: "ts_utc"})
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    if "side" not in df.columns:
        df["side"] = "N"
    df = df[TICK_COLS]
    return df.sort_values("ts_utc", kind="stable").reset_index(drop=True)


@lru_cache(maxsize=32)
def _read_day_parquet(symbol: str, day: date, segment: str = "rth") -> pd.DataFrame:
    """In-memory cache over the parquet read. Treat the result as READ-ONLY —
    the frame is shared with every other caller for the life of the process."""
    return pd.read_parquet(_cache_path(symbol, day, segment))


def _get_segment(symbol: str, day: date, segment: str, use_cache: bool) -> pd.DataFrame:
    cache = _cache_path(symbol, day, segment)
    if use_cache and cache.exists():
        return _read_day_parquet(symbol, day, segment)
    bounds = overnight_bounds_utc(day) if segment == "on" else session_bounds_utc(day)
    df = _fetch_range(symbol, *bounds)
    if not df.empty:
        TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
    return df


def get_day_ticks(symbol: str, day: date, use_cache: bool = True,
                  include_overnight: bool = False) -> pd.DataFrame | None:
    """Trade ticks for one session — RTH only by default; with
    ``include_overnight`` the Globex segment (prev day 18:00 ET → 09:30 ET) is
    spliced in front, each segment fetched and cached independently.

    Unlike ``databento_client.get_day_bars`` this does **not** swallow fetch
    errors: a sim that silently skips a day would report metrics over a window
    it never actually tested.
    """
    rth = _get_segment(symbol, day, "rth", use_cache)
    if not include_overnight:
        return rth
    on = _get_segment(symbol, day, "on", use_cache)
    if on.empty:
        return rth
    # Both segments are sorted and the windows meet end-exclusive at 09:30,
    # so concatenation is already globally ordered with no duplicate tick.
    return pd.concat([on, rth], ignore_index=True)


def ensure_overnight(symbol: str, day: date) -> bool:
    """Buy and cache the Globex segment for a session, whether or not the engine
    reads it. True if the night is on disk afterwards.

    An RTH strategy simulates on RTH ticks alone — that must not change — but its
    charts want the overnight in front of the session (the Globex-anchored VWAP,
    the night's candles, the full-day profile), and charts never fetch. So a run
    pays for the night up front and every chart of that run is drawn against the
    same data as a globex strategy's. Turned off by the run form's "NY session
    only" box, for when the RTH ticks are all you want to pay for.

    Failure is not fatal, which is the one way this differs from every other fetch
    here: the night is context for the charts, not an input to the rules. A run
    whose RTH ticks are all present must not land in 'error' because Databento was
    unreachable for the garnish — it simulated exactly the trades it claims. The
    session just gets drawn without its overnight, as it was before this existed.
    """
    try:
        _get_segment(symbol, day, "on", use_cache=True)
    except Exception:  # noqa: BLE001 — see above: the charts degrade, the run stands
        return False
    return _cache_path(symbol, day, "on").exists()


def cached_rth(symbol: str, day: date) -> pd.DataFrame | None:
    """The RTH segment, but only if it is already on disk — never a fetch.

    ``get_day_ticks`` buys what it doesn't find, which is right for a run and
    wrong for anything served from a GET. The regime KPIs are read straight off
    the tick cache by the charts, so they need the read half without the wallet.
    """
    if not _cache_path(symbol, day, "rth").exists():
        return None
    df = _read_day_parquet(symbol, day, "rth")
    return None if df.empty else df


def cached_overnight(symbol: str, day: date) -> pd.DataFrame | None:
    """The overnight segment, but *only* if it is already on disk — never a fetch.

    This exists for the charts. An RTH strategy's chart draws the Globex-anchored
    VWAP as context alongside the NY one it actually traded, and that needs the
    night in front of the session. But a chart is a GET: it must never be the
    thing that spends money at Databento. So a session whose overnight was never
    bought simply doesn't get the second anchor drawn, and nothing is bought to
    draw it. Runs are where ticks get paid for; charts only ever read.
    """
    if not _cache_path(symbol, day, "on").exists():
        return None
    df = _read_day_parquet(symbol, day, "on")
    return None if df.empty else df


def session_dates(start: date, end: date) -> list[date]:
    """Weekdays in [start, end]. Holidays surface as empty tick pulls."""
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days
