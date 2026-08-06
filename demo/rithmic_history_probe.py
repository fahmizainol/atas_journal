"""How far back does Rithmic's tick replay go, and is it the same tape? — HISTORY_PLANT probe.

The live surface starts its chart wherever you clicked Connect, because the
ticker plant delivers prints from the moment you subscribe and ``_preload`` can
only seed from what *your own recorder* already wrote (``journal.live.state``).
The one route that attacks that without an always-on host is a backfill from
Rithmic's history plant on connect — flagged in docs/live-shadow-plan.md as
"unverified scope and a second plant". This is the probe that verifies it.

Four questions, in the order that makes the later ones worth asking:

  A. login       — is HISTORY_PLANT reachable and entitled on this account?
  B. depth       — how far back does a tick replay actually return data? A ladder
                   of small windows at increasing ages, so the answer costs
                   kilobytes rather than a day of ticks per rung.
  C. session     — the real ask: prev 18:00 ET → now, in one paginated request.
                   Rows, wall time, span, and the largest interior gap.
  D. fidelity    — stream LAST_TRADE live for N seconds, then replay *that same
                   window* off the history plant and compare print for print.

D is the one that decides whether a backfill may be trusted as a substitute for
a recorded tape. A replay that returns 90% of the prints, or aggregates them, or
stamps them differently, is not the same session — and the strategies would then
be simulating a morning that never happened, which is precisely the failure class
the whole live plan is built to avoid. Its findings, in order of what would kill
the idea:

  - **one bar ≠ one print?** The request is TICK_BAR with specifier "1", so each
    bar should carry exactly one trade. ``num_trades`` says whether that holds;
    anything above 1 means the replay aggregates and the tape cannot be rebuilt
    from it.
  - **stamps.** Replay carries ``data_bar_ssboe``/``usecs`` — Rithmic's seconds
    and microseconds. The live path stamps from ``source_ssboe``/``source_nsecs``,
    the *exchange's* clock. If those disagree, a backfilled prefix and a live
    suffix are on two different clocks and the seam between them is a lie.
  - **side.** A replay bar has no ``aggressor`` field at all — only ``bid_volume``
    and ``ask_volume``. D cross-tabs the live aggressor int against that split to
    see whether the side survives the round trip, because ten gate sites and
    every profile read it.

HISTORY_PLANT and TICKER_PLANT only. ``client.connect()`` defaults to all four
and would open the ORDER plant; every connect here names its plants. That keeps
decision 2 intact — the question this asks the firm is still "may I read market
data" (docs/live-shadow-plan.md § Decisions).

Read-only. Writes nothing to data/, records nothing, and spends nothing at
Databento.

Usage:
    uv run python demo/rithmic_history_probe.py                 # A, B, C, D
    uv run python demo/rithmic_history_probe.py --probe depth
    uv run python demo/rithmic_history_probe.py --probe session --symbol NQU6
    uv run python demo/rithmic_history_probe.py --probe fidelity --seconds 120
    uv run python demo/rithmic_history_probe.py --ages 0 1 5 30 --window 30

Credentials come from .env (RITHMIC_* — see .env.example). The contract must be
RAW (``NQU6``): a root would send ``contract_for`` to probe Databento, which a
live path must not do, and the on-disk roll map ends 2026-06-30 regardless.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from async_rithmic import DataType, LastTradePresenceBits, RithmicClient, SysInfraType
except ImportError:  # pragma: no cover - dependency is Phase-5 only
    sys.exit("async_rithmic is not installed. Run: uv pip install async_rithmic")

from journal.config import ET_TZ  # noqa: E402
from journal.live.rithmic import credentials, install_redaction  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

import pandas as pd  # noqa: E402

EXCHANGE = os.getenv("RITHMIC_SMOKE_EXCHANGE", "CME")

# The rungs of probe B, in days back from the current session. Dense at the near
# end because that is where the answer decides the design: a backfill that
# reaches yesterday evening fixes the Live chart, and one that reaches a month
# back would also let a missed session be reconstructed after the fact.
DEFAULT_AGES = (0, 1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 365)

# A rung's window. Small on purpose: a minute of NQ is a few hundred prints, and
# twelve rungs of that is a probe rather than a download.
DEFAULT_WINDOW_S = 60

# Where in the session a rung's window is cut. 10:00 ET is an hour into RTH —
# liquid on any trading day, and far from both bells, so an empty answer there
# means "no data available", not "the market was shut".
RUNG_AT_ET = dtime(10, 0)


def _say(ok: bool | None, msg: str) -> None:
    mark = {True: "PASS", False: "FAIL", None: "····"}[ok]
    print(f"  [{mark}] {msg}", flush=True)


# --------------------------------------------------------------- reading a bar


def _int(v) -> int:
    """MessageToDict renders uint64 as a JSON string; int32 stays an int."""
    if v is None:
        return 0
    return int(v)


def _rows(bars: list[dict]) -> list[dict]:
    """Replay bars → the fields a tape would need, one row per bar.

    ``data_bar_ssboe``/``data_bar_usecs`` are repeated: one entry per constituent
    print. With a 1-tick bar there should be exactly one, and ``n_stamps`` is kept
    so that assumption is reported rather than trusted.
    """
    out = []
    for b in bars:
        ss = b.get("data_bar_ssboe") or []
        us = b.get("data_bar_usecs") or []
        if not ss:
            continue
        out.append({
            "ns": int(ss[0]) * 1_000_000_000 + int(us[0] if us else 0) * 1_000,
            "price": float(b.get("close_price", 0.0) or 0.0),
            "size": _int(b.get("volume")),
            "num_trades": _int(b.get("num_trades")),
            "bid_volume": _int(b.get("bid_volume")),
            "ask_volume": _int(b.get("ask_volume")),
            "n_stamps": len(ss),
        })
    out.sort(key=lambda r: r["ns"])
    return out


def _et(ns: int) -> str:
    return (pd.Timestamp(ns, unit="ns", tz="UTC")
            .tz_convert(ET_TZ).strftime("%H:%M:%S.%f")[:-3])


async def _replay(client: RithmicClient, symbol: str, start: datetime,
                  end: datetime, max_pages: int = 1_000) -> tuple[list[dict], float]:
    """One tick replay over [start, end). Returns the rows and the wall seconds.

    Both instants must be tz-aware: ``_datetime_to_index`` localises a naive
    datetime to the *host's* timezone, which on a machine set to anything but UTC
    would silently shift the window by hours.
    """
    assert start.tzinfo is not None and end.tzinfo is not None
    t0 = time.perf_counter()
    bars = await client.get_historical_tick_data(
        symbol, EXCHANGE, start, end, max_pages=max_pages)
    return _rows(bars or []), time.perf_counter() - t0


# --------------------------------------------------------------------------- A


async def probe_a_login(creds: dict) -> RithmicClient | None:
    print("\nA. HISTORY_PLANT login")
    client = RithmicClient(**creds)
    try:
        # TICKER too: probe D needs a live subscription, and probe B's contract
        # sanity check is cheaper than a failed replay. ORDER stays shut.
        await client.connect(plants=[SysInfraType.HISTORY_PLANT,
                                     SysInfraType.TICKER_PLANT])
    except Exception as exc:  # noqa: BLE001 — the probe's whole job is to report
        _say(False, f"connect failed: {type(exc).__name__}: {exc}")
        print("       → if TICKER_PLANT works in demo/rithmic_smoke.py and this does\n"
              "         not, the account is not entitled to the history plant, and the\n"
              "         backfill route is closed regardless of what B and C would say.")
        return None
    _say(True, "authenticated on HISTORY_PLANT (+ TICKER_PLANT for probe D)")
    _say(None, "ORDER_PLANT deliberately not opened")
    return client


# --------------------------------------------------------------------------- B


def _rung_window(age_days: int, window_s: int, now: datetime) -> tuple[datetime, datetime]:
    """A small, known-liquid window ``age_days`` sessions back.

    10:00 ET on a weekday. Weekends are walked back to the Friday — a Saturday
    window would return nothing and read as "no depth", which is the one way this
    ladder can lie. Holidays are not handled: a single empty rung surrounded by
    full ones is a holiday, and the table shows enough to see that.
    """
    et = now.astimezone(ET_TZ)
    day = (et - timedelta(days=age_days)).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    start = datetime.combine(day, RUNG_AT_ET, tzinfo=ET_TZ)
    if start >= et - timedelta(minutes=15):
        # Today, before 10:00 ET — take the most recent settled quarter hour
        # instead, so the rung is still in the past and still has prints in it.
        start = et - timedelta(minutes=15)
    return start, start + timedelta(seconds=window_s)


async def probe_b_depth(client: RithmicClient, symbol: str, ages: list[int],
                        window_s: int) -> int | None:
    """The ladder. Returns the deepest age (in days) that returned prints."""
    print(f"\nB. replay depth — {symbol}/{EXCHANGE}, {window_s}s windows at 10:00 ET")
    print(f"\n  {'age':>5}  {'window (ET)':<22} {'bars':>7} {'first':>13} "
          f"{'last':>13} {'took':>7}")

    deepest: int | None = None
    seen: set[datetime] = set()
    for age in ages:
        start, end = _rung_window(age, window_s, datetime.now(timezone.utc))
        # Two rungs a weekend apart land on the same Friday; asking twice would
        # print a duplicate row and read as a flaky boundary.
        if start in seen:
            continue
        seen.add(start)
        label = f"{start.astimezone(ET_TZ):%Y-%m-%d %H:%M}"
        try:
            rows, secs = await _replay(client, symbol, start, end, max_pages=20)
        except Exception as exc:  # noqa: BLE001
            print(f"  {age:>4}d  {label:<22} {type(exc).__name__}")
            continue
        if rows:
            deepest = age
            print(f"  {age:>4}d  {label:<22} {len(rows):>7,} {_et(rows[0]['ns']):>13} "
                  f"{_et(rows[-1]['ns']):>13} {secs:>6.1f}s")
        else:
            print(f"  {age:>4}d  {label:<22} {'—':>7} {'no data':>13} "
                  f"{'':>13} {secs:>6.1f}s")

    if deepest is None:
        _say(False, "no rung returned a single print — replay is not available here")
    else:
        _say(True, f"deepest rung with data: {deepest} day(s) back")
        print("       An empty rung between two full ones is a holiday, not a depth\n"
              "       limit. The limit is where the empties stop being isolated.")
    return deepest


# --------------------------------------------------------------------------- C


async def probe_c_session(client: RithmicClient, symbol: str) -> list[dict]:
    """The actual use case: everything since the session's 18:00 ET open."""
    now = pd.Timestamp.now(tz="UTC")
    day = tickmod.session_date_for(now)
    open_utc, _ = tickmod.day_bounds_utc(day)
    start = open_utc.to_pydatetime()
    end = now.to_pydatetime()
    hours = (end - start).total_seconds() / 3600

    print(f"\nC. session backfill — {symbol}, session {day.isoformat()}")
    print(f"       {start.astimezone(ET_TZ):%Y-%m-%d %H:%M} ET → "
          f"{end.astimezone(ET_TZ):%H:%M} ET  ({hours:.1f}h)")

    try:
        rows, secs = await _replay(client, symbol, start, end)
    except Exception as exc:  # noqa: BLE001
        _say(False, f"{type(exc).__name__}: {exc}")
        return []
    if not rows:
        _say(False, "no prints returned for the session so far")
        return []

    span_h = (rows[-1]["ns"] - rows[0]["ns"]) / 3.6e12
    _say(True, f"{len(rows):,} prints in {secs:.1f}s "
               f"({len(rows) / max(secs, 1e-9):,.0f}/s)")
    print(f"       first {_et(rows[0]['ns'])} ET   last {_et(rows[-1]['ns'])} ET   "
          f"span {span_h:.1f}h of {hours:.1f}h requested")

    # A replay truncated at the far end looks exactly like a quiet night, so the
    # gap is reported rather than the count alone: the interesting failure is a
    # backfill that returns the last two hours of a fourteen-hour session.
    gaps = [(rows[i + 1]["ns"] - rows[i]["ns"], rows[i]["ns"])
            for i in range(len(rows) - 1)]
    if gaps:
        worst, at = max(gaps)
        print(f"       largest interior gap {worst / 1e9:,.0f}s at {_et(at)} ET")
    lead = (rows[0]["ns"] - int(start.timestamp() * 1e9)) / 1e9
    if lead > 600:
        _say(None, f"first print is {lead / 60:.0f} min after the requested start — "
                   "the replay is truncated, not the market")

    multi = sum(1 for r in rows if r["num_trades"] > 1)
    stamped = sum(1 for r in rows if r["n_stamps"] > 1)
    _say(multi == 0, f"{len(rows) - multi:,}/{len(rows):,} bars carry exactly one trade"
                     + ("" if multi == 0 else f" — {multi:,} aggregate several"))
    if stamped:
        _say(None, f"{stamped:,} bars carry more than one timestamp")

    sided = sum(1 for r in rows if bool(r["bid_volume"]) != bool(r["ask_volume"]))
    _say(sided == len(rows),
         f"{sided:,}/{len(rows):,} bars have a one-sided bid/ask volume split "
         "(a usable `side`)")
    return rows


# --------------------------------------------------------------------------- D


async def probe_d_fidelity(client: RithmicClient, symbol: str, seconds: int) -> bool:
    """Record the live feed and the replay over the same window, then compare.

    The comparison keys on (stamp, price, size) after rounding the live stamp to
    microseconds — replay carries ``usecs`` and the live path carries nanoseconds,
    so an exact match on nanoseconds would fail on formatting rather than on
    content.
    """
    print(f"\nD. live vs replay — same {seconds}s window, print for print")

    live: list[dict] = []
    dropped: Counter[str] = Counter()

    async def on_tick(data: dict) -> None:
        if data.get("data_type") != DataType.LAST_TRADE:
            return
        if not data.get("presence_bits", 0) & LastTradePresenceBits.LAST_TRADE:
            dropped["no_trade_bit"] += 1
            return
        if data.get("is_snapshot"):
            dropped["snapshot"] += 1
            return
        if data.get("source_ssboe"):
            sub = data.get("source_nsecs")
            if sub is None:
                sub = int(data.get("source_usecs", 0) or 0) * 1_000
            ns, exch = int(data["source_ssboe"]) * 1_000_000_000 + int(sub), True
        elif data.get("ssboe"):
            ns = (int(data["ssboe"]) * 1_000_000_000
                  + int(data.get("usecs", 0) or 0) * 1_000)
            exch = False
        else:
            dropped["no_stamp"] += 1
            return
        live.append({
            "ns": ns, "exch": exch,
            "price": float(data.get("trade_price") or 0.0),
            "size": int(data.get("trade_size") or 0),
            "agg": int(data.get("aggressor", 0) or 0),
        })

    client.on_tick += on_tick
    await client.subscribe_to_market_data(symbol, EXCHANGE, DataType.LAST_TRADE)
    t_start = datetime.now(timezone.utc)
    try:
        for elapsed in range(seconds):
            await asyncio.sleep(1)
            if elapsed and elapsed % 10 == 0:
                print(f"       {elapsed:>3}s: {len(live)} prints", flush=True)
    finally:
        try:
            await client.unsubscribe_from_market_data(symbol, EXCHANGE,
                                                      DataType.LAST_TRADE)
        except Exception:  # noqa: BLE001 — we are already leaving
            pass
        client.on_tick -= on_tick
    t_end = datetime.now(timezone.utc)

    if not live:
        _say(None, "no live prints — the market is shut (NQ halts 17:00–18:00 ET, "
                   "and all weekend). Re-run while it trades.")
        return False
    _say(True, f"{len(live)} live prints"
               + (f", dropped {dict(dropped)}" if dropped else ""))

    # Replay only the interior of the window. Both edges are ragged — the
    # subscription starts mid-print and the unsubscribe lands mid-print — and an
    # edge mismatch would be charged to the replay rather than to the clock.
    lo, hi = live[0]["ns"] + 1_000_000_000, live[-1]["ns"] - 1_000_000_000
    if hi <= lo:
        _say(None, "window too short to trim its edges — re-run with --seconds 60")
        return False
    # Rithmic's replay is second-granular at the request boundary, so ask wide
    # and trim to the interior here.
    await asyncio.sleep(2.0)  # let the replay side catch up with the tape
    try:
        rows, secs = await _replay(client, symbol, t_start - timedelta(seconds=5),
                                   t_end + timedelta(seconds=5))
    except Exception as exc:  # noqa: BLE001
        _say(False, f"replay of the same window failed: {type(exc).__name__}: {exc}")
        return False

    liv = [r for r in live if lo <= r["ns"] <= hi]
    rep = [r for r in rows if lo <= r["ns"] <= hi]
    _say(None, f"comparing the interior: {len(liv)} live vs {len(rep)} replayed "
               f"(replay took {secs:.1f}s)")
    if not rep:
        _say(False, "the replay returned nothing for a window that just traded — "
                    "backfill cannot reach the current session")
        return False

    def key(r, us: bool = True) -> tuple:
        ns = r["ns"] // 1_000 * 1_000 if us else r["ns"]
        return (ns, round(r["price"], 4), r["size"])

    live_keys = Counter(key(r) for r in liv)
    rep_keys = Counter(key(r) for r in rep)
    both = sum((live_keys & rep_keys).values())
    exact = both == len(liv) == len(rep)
    _say(exact or None,
         f"{both:,} prints match on (stamp, price, size); "
         f"{len(liv) - both:,} live-only, {len(rep) - both:,} replay-only")

    if not exact:
        # Which half of the key is wrong matters, and the difference is the whole
        # decision: same prints on a slightly different clock is a fixable seam,
        # a different set of prints is a different tape.
        lv = sum(r["size"] for r in liv)
        rv = sum(r["size"] for r in rep)
        print(f"       volume: live {lv:,} vs replay {rv:,} "
              f"({100 * rv / lv if lv else float('nan'):.1f}%)")
        loose = Counter((round(r["price"], 4), r["size"]) for r in liv)
        loose_r = Counter((round(r["price"], 4), r["size"]) for r in rep)
        print(f"       ignoring stamps entirely: "
              f"{sum((loose & loose_r).values()):,} of {len(liv):,} match")

    # Both sides are one time-ordered stream of the same trades, so when the
    # counts agree the i-th print is the i-th print and the stamp difference is
    # measurable rather than a mismatch to be reported and left there. This is
    # what says whether a backfilled prefix and a live suffix are on one clock.
    ok = exact
    paired = list(zip(liv, rep)) if len(liv) == len(rep) else []
    if paired:
        content = sum(1 for a, b in paired
                      if round(a["price"], 4) == round(b["price"], 4)
                      and a["size"] == b["size"])
        _say(content == len(paired),
             f"in sequence order, {content:,}/{len(paired):,} prints agree on "
             "price and size")
        deltas = sorted(abs(b["ns"] - a["ns"]) for a, b in paired)
        med = deltas[len(deltas) // 2] / 1_000
        p90 = deltas[int(0.9 * (len(deltas) - 1))] / 1_000
        # The bar the verdict is held to is the same prints in the same order.
        # Exact stamp equality is deliberately NOT the bar: a tick tape is phased
        # by position and only *ordered* by stamp, so a clock offset that
        # preserves the order changes no bar, no profile bucket and no fill. What
        # would disqualify a backfill is a print missing, a print invented, or
        # two prints swapped — and 78/78 agreeing in sequence order is exactly
        # the statement that none of those happened.
        ok = content == len(paired)
        print(f"       stamp delta (replay − live): median {med:,.1f}µs  "
              f"p90 {p90:,.1f}µs  max {max(deltas) / 1_000:,.1f}µs")
        print("       A median in the hundreds of µs is the exchange→Rithmic hop\n"
              "       (measured 0.3-0.4ms in the access probe), which says the replay\n"
              "       carries RITHMIC's stamp where the live path carries the\n"
              "       EXCHANGE's. So a backfilled prefix and a live suffix sit on two\n"
              "       clocks a fraction of a millisecond apart. That moves no bar —\n"
              "       but it is a systematic offset Phase 6 will see against\n"
              "       Databento's ts_event, and it belongs in the manifest rather\n"
              "       than in somebody's memory.")

    exch = sum(1 for r in liv if r["exch"])
    _say(None, f"{exch:,}/{len(liv):,} live prints carried an exchange stamp "
               "(the clock a backfill has to agree with)")

    # Does the side survive the round trip? A replay bar has no aggressor field —
    # only the bid/ask volume split — so this is the only evidence that a
    # backfilled tick can be given a `side` at all. Paired by sequence for the
    # same reason as above: keying on the stamp would drop every print the
    # microsecond quantisation moved, which is most of them.
    cross: Counter[tuple] = Counter()
    for r, m in paired:
        side = ("ask" if m["ask_volume"] and not m["bid_volume"]
                else "bid" if m["bid_volume"] and not m["ask_volume"] else "both/neither")
        cross[(r["agg"], side)] += 1
    if cross:
        print("\n       aggressor (live) × bid/ask split (replay), matched prints:")
        for (agg, side), n in sorted(cross.items()):
            print(f"         aggressor={agg} → {side:<12} {n:>6,}")
        print("       A table with one cell per aggressor means a backfilled print can\n"
              "       carry the same `side` a live one does. Which cell is the finding:\n"
              "       do NOT assume ask_volume is the buy aggressor because a buy lifts\n"
              "       the offer. Read the mapping off this table, the way\n"
              "       rithmic._aggressor_map reads its enum off the schema.")
    return ok


# --------------------------------------------------------------------------- main


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", choices=["a", "depth", "session", "fidelity", "all"],
                    default="all")
    ap.add_argument("--symbol", default=os.getenv("LIVE_SYMBOL", "NQU6"),
                    help="RAW contract, e.g. NQU6 — never a root")
    ap.add_argument("--ages", type=int, nargs="+", default=list(DEFAULT_AGES),
                    help="probe B rungs, in days back")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_S,
                    help="probe B window length, seconds")
    ap.add_argument("--seconds", type=int, default=60,
                    help="probe D live capture, seconds")
    ap.add_argument("--debug", action="store_true", help="full rithmic protocol logging")
    args = ap.parse_args()

    symbol = args.symbol.strip().upper()
    if len(symbol) < 4 or symbol in {"NQ", "ES", "CL", "GC"}:
        sys.exit(f"{symbol!r} looks like a root — pin the raw contract (e.g. NQU6). "
                 "Roots resolve through Databento, which a live path must not touch.")

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("rithmic").setLevel(logging.DEBUG)

    creds = credentials()
    # Before any socket opens: a rejected login logs the request it sent, password
    # and all, at ERROR. Handler level, not logger level — see rithmic.py.
    install_redaction(creds["password"])

    print(f"gateway     {creds['url']}")
    print(f"system      {creds['system_name']}")
    print(f"user        {creds['user']}")
    print(f"contract    {symbol}/{EXCHANGE}")

    client = await probe_a_login(creds)
    if client is None:
        return 1

    verdicts: dict[str, object] = {}
    try:
        if args.probe in ("all", "depth"):
            verdicts["depth"] = await probe_b_depth(client, symbol, args.ages, args.window)
        if args.probe in ("all", "session"):
            verdicts["session"] = len(await probe_c_session(client, symbol))
        if args.probe in ("all", "fidelity"):
            verdicts["fidelity"] = await probe_d_fidelity(client, symbol, args.seconds)
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    print("\nVERDICT")
    if "depth" in verdicts:
        d = verdicts["depth"]
        print(f"  depth     {'no replay at all' if d is None else f'reaches {d} day(s) back'}")
    if "session" in verdicts:
        n = verdicts["session"]
        print(f"  session   {n:,} prints since 18:00 ET"
              if n else "  session   nothing returned for the session so far")
    if "fidelity" in verdicts:
        print(f"  fidelity  {'replay matches the live tape' if verdicts['fidelity'] else 'see D above — do NOT build the backfill on this'}")
    print("\n  A backfill is only worth building if D passes. Depth without fidelity\n"
          "  buys a chart that looks complete and a tape that is not.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
