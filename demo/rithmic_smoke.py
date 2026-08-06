"""Rithmic credential smoke test — does the prop firm's login work on the API?

Answers one question in four escalating probes, stopping at the first failure:

  A. system list   — is the gateway reachable, and does it serve our system_name?
                     (unauthenticated: RequestRithmicSystemInfo, template 16)
  B. ticker login  — do the credentials authenticate on TICKER_PLANT?
  C. entitlements  — which exchanges are we entitled to, and does NQ resolve?
  D. tick flow     — do LAST_TRADE prints actually arrive?

Plus `--discover`, which needs no credentials at all: it sweeps the known
gateway hosts on :443 and prints which systems each one serves. That is the
programmatic equivalent of R|Trader Pro's System and Gateway dropdowns.

TICKER_PLANT ONLY. `client.connect()` defaults to all four plants; this script
passes `plants=[TICKER_PLANT]` so the ORDER plant is never opened. That is
decision 2 of docs/live-shadow-plan.md, and it keeps the question we are asking
the prop firm to "may I read market data", not "may I automate orders".

Read-only: subscribes, counts, unsubscribes. Writes nothing to data/.

Usage:
    uv run python demo/rithmic_smoke.py --discover  # no credentials needed
    uv run python demo/rithmic_smoke.py            # all probes, 30s of ticks
    uv run python demo/rithmic_smoke.py --probe a  # just the unauthenticated one
    uv run python demo/rithmic_smoke.py --seconds 120 --debug

Credentials come from .env (see RITHMIC_* keys in .env.example).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import ssl
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

try:
    import websockets
    from async_rithmic import DataType, LastTradePresenceBits, RithmicClient, SysInfraType
    from async_rithmic.protocol_buffers.response_list_exchange_permissions_pb2 import (
        ResponseListExchangePermissions,
    )
except ImportError:  # pragma: no cover - dependency is Phase-5 only
    sys.exit("async_rithmic is not installed. Run: uv pip install async_rithmic")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SYMBOL = os.getenv("RITHMIC_SMOKE_SYMBOL", "NQ")
EXCHANGE = os.getenv("RITHMIC_SMOKE_EXCHANGE", "CME")

# Gateway host per region, on the R|Protocol WebSocket port (443).
#
# Provenance: R|Trader Pro ships its System/Gateway dropdown contents in
# `omneconfig.tbl` (on Windows, C:\Program Files (x86)\Rithmic\Rithmic Trader Pro\).
# That table is for R|Trader Pro's own native protocol — different ports
# (64100/65000/56000/45454) — but the host names turn out to be shared: the same
# boxes answer the WebSocket API on 443. Region→host was read off that table;
# `--discover` is what verifies which of them actually answer on 443 — not all
# do, so a row failing there is a fact about that host, not a bug here.
#
# `rprotocol` is Rithmic's generic production entry point and is the safe default.
GATEWAYS: dict[str, str] = {
    "Production (generic)": "rprotocol.rithmic.com:443",
    "Chicago Area": "ritpz04063.04.rithmic.com:443",
    "Europe": "ritpz05001.rithmic.com:443",
    "Singapore": "ritpz06001.rithmic.com:443",
    "Tokyo": "ritpz15001.rithmic.com:443",
    "Sao Paolo": "ritpz18001.rithmic.com:443",
    "Hong Kong": "ritpz19001.rithmic.com:443",
    "Sydney": "ritpz20001.rithmic.com:443",
    "Mumbai": "ritpz21001.rithmic.com:443",
    "Seoul": "ritpz22001.rithmic.com:443",
    "Cape Town": "ritpz25001.rithmic.com:443",
    "Orangeburg (test)": "rituz00100.rithmic.com:443",
}


def _creds() -> dict:
    missing = [
        k
        for k in ("RITHMIC_USER", "RITHMIC_PASSWORD", "RITHMIC_SYSTEM_NAME", "RITHMIC_GATEWAY")
        if not os.getenv(k)
    ]
    if missing:
        sys.exit(
            f"Missing in .env: {', '.join(missing)}\n"
            "Run `--discover` (no credentials needed) to see every gateway and the "
            "systems it serves."
        )
    return dict(
        user=os.getenv("RITHMIC_USER"),
        password=os.getenv("RITHMIC_PASSWORD"),
        system_name=os.getenv("RITHMIC_SYSTEM_NAME"),
        app_name=os.getenv("RITHMIC_APP_NAME", "atas_journal_shadow"),
        app_version=os.getenv("RITHMIC_APP_VERSION", "0.1"),
        url=os.getenv("RITHMIC_GATEWAY"),
    )


class _RedactSecrets(logging.Filter):
    """Scrub the password out of anything the rithmic logger emits.

    Not paranoia: on a rejected login `base.py` logs the *whole* request it sent,
    password included, at ERROR — so a failed connect prints your credentials to
    the terminal and into any log file. Nothing here can stop the library
    logging; a filter on its logger is the one place that catches every path.
    """

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secret:
            return True
        if isinstance(record.msg, str) and self.secret in record.msg:
            record.msg = record.msg.replace(self.secret, "***")
        if record.args:
            record.args = tuple(
                a.replace(self.secret, "***") if isinstance(a, str) else a for a in record.args
            )
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            scrubbed = [
                a.replace(self.secret, "***") if isinstance(a, str) else a for a in exc.args
            ]
            exc.args = tuple(scrubbed)
        return True


def _say(ok: bool | None, msg: str) -> None:
    mark = {True: "PASS", False: "FAIL", None: "····"}[ok]
    print(f"  [{mark}] {msg}", flush=True)


# ----------------------------------------------------------------- system list


async def _system_names(url: str, timeout: float = 15.0) -> list[str]:
    """RequestRithmicSystemInfo (template 16) — the handshake that precedes login.

    No credentials cross the wire. Hand-rolled rather than going through
    `plant._connect()`, which raises on a system-name mismatch without ever
    showing you the list on success.
    """
    client = RithmicClient(
        user="", password="", system_name="", app_name="", app_version="", url=url
    )
    plant = client.plants["ticker"]
    plant.ws = await asyncio.wait_for(
        websockets.connect(
            client.credentials["gateway"],
            ssl=client.ssl_context,
            ping_interval=60,
            ping_timeout=50,
        ),
        timeout=timeout,
    )
    try:
        info = await asyncio.wait_for(plant.get_system_info(), timeout=timeout)
        return list(info.system_name)
    finally:
        try:
            await plant._disconnect(trigger_event=False)
        except Exception:
            pass


async def _ping_gateway(url: str, n: int = 5) -> tuple[list[float], list[float]]:
    """n fresh connect + template-16 round trips, timed separately.

    Template 16 is the cheapest request Rithmic answers and needs no login, so
    the round trip is network + server dispatch and nothing else. It is also
    strictly one per socket — Rithmic hangs up after answering, which is what
    the protocol asks clients to do anyway — so each sample reconnects.

    Both are monotonic-clock measurements and so immune to local clock skew,
    unlike the feed latency in probe D, which compares against Rithmic's stamps.
    """
    client = RithmicClient(
        user="", password="", system_name="", app_name="", app_version="", url=url
    )
    plant = client.plants["ticker"]
    connects: list[float] = []
    rtts: list[float] = []

    for _ in range(n):
        t0 = time.perf_counter()
        plant.ws = await asyncio.wait_for(
            websockets.connect(
                client.credentials["gateway"],
                ssl=client.ssl_context,
                ping_interval=60,
                ping_timeout=50,
            ),
            timeout=15,
        )
        connects.append((time.perf_counter() - t0) * 1000)

        t = time.perf_counter()
        try:
            await asyncio.wait_for(plant.get_system_info(), timeout=15)
            rtts.append((time.perf_counter() - t) * 1000)
        finally:
            try:
                await plant._disconnect(trigger_event=False)
            except Exception:
                pass
    return connects, rtts


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile. Avoids pulling numpy into a smoke test."""
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


async def ping(samples: int) -> int:
    """Round-trip time to every gateway. No credentials."""
    print(f"Pinging gateways ({samples} round trips each, unauthenticated).\n")
    width = max(len(name) for name in GATEWAYS)
    print(
        f"  {'gateway':<{width}}  {'url':<34} {'connect':>9} "
        f"{'rtt min':>9} {'median':>8} {'max':>8}"
    )

    results: list[tuple[float, str]] = []
    for name, url in GATEWAYS.items():
        try:
            connects, rtts = await _ping_gateway(url, samples)
        except Exception as exc:
            print(f"  {name:<{width}}  {url:<34} {type(exc).__name__}")
            continue
        med = _pct(rtts, 0.5)
        results.append((med, name))
        print(
            f"  {name:<{width}}  {url:<34} {_pct(connects, 0.5):>7.0f}ms "
            f"{min(rtts):>7.0f}ms {med:>7.0f}ms {max(rtts):>7.0f}ms"
        )

    if results:
        best_med, best_name = min(results)
        print(f"\nLowest median round trip: {best_name} ({best_med:.0f}ms).")
    print(
        "This is control-plane RTT, not market-data latency — for that, read the\n"
        "feed-latency block in probe D of a full run."
    )
    return 0


async def discover() -> int:
    """Sweep every known gateway and report the systems it serves. No credentials."""
    print("Sweeping known gateways on :443 — nothing is authenticated.\n")
    width = max(len(name) for name in GATEWAYS)

    # Most gateways serve an identical system list, so group by it rather than
    # reprinting twenty names per row.
    by_systems: dict[tuple[str, ...], list[str]] = {}

    for name, url in GATEWAYS.items():
        try:
            systems = await _system_names(url)
        except (OSError, asyncio.TimeoutError, ssl.SSLError, websockets.WebSocketException) as exc:
            status = f"unreachable on :443 ({type(exc).__name__})"
        except Exception as exc:
            status = f"no handshake ({type(exc).__name__})"
        else:
            key = tuple(sorted(systems))
            group = by_systems.setdefault(key, [])
            group.append(name)
            n = len(systems)
            status = f"{n} system{'s' * (n != 1)} (set #{list(by_systems).index(key) + 1})"
        print(f"  {name:<{width}}  {url:<34} {status}")

    for i, (systems, names) in enumerate(by_systems.items(), start=1):
        print(f"\nset #{i} — served by {', '.join(names)}:")
        for s in systems:
            print(f"    {s}")

    print(
        "\nPick the gateway nearest you whose set contains your firm, then set\n"
        "RITHMIC_GATEWAY and RITHMIC_SYSTEM_NAME (verbatim, spaces and all) in .env."
    )
    return 0


# --------------------------------------------------------------------------- A


async def probe_a_systems(creds: dict) -> bool:
    print("\nA. system list (no credentials sent)")

    url = creds["url"]
    try:
        systems = await _system_names(url)
    except (OSError, asyncio.TimeoutError, ssl.SSLError, websockets.WebSocketException) as exc:
        _say(False, f"cannot reach {url}: {exc!r}")
        print("       → wrong host/port, or the gateway is firewalled. Try --discover.")
        return False

    _say(True, f"gateway reachable, serves {len(systems)} system(s)")
    for s in systems:
        print(f"         - {s}")

    want = creds["system_name"]
    if want not in systems:
        _say(False, f"RITHMIC_SYSTEM_NAME={want!r} is not one of them")
        print("       → copy one of the names above verbatim, or you are on the wrong gateway.")
        return False

    _say(True, f"system_name {want!r} matches")
    return True


# --------------------------------------------------------------------------- B


async def probe_b_login(creds: dict, aggregated: bool = False) -> RithmicClient | None:
    print("\nB. TICKER_PLANT login")

    client = RithmicClient(**creds)

    if aggregated:
        # `RequestLogin` has an `aggregated_quotes` bool — the API equivalent of
        # R|Trader Pro's aggregated-quotes toggle, which routes you to Rithmic's
        # `login_agent_tp_agg_*` plant instead of `login_agent_tp_*`. async_rithmic
        # never sets it, so inject it on the way past. `_build_request` copies
        # arbitrary kwargs onto the protobuf, so this needs no fork.
        plant = client.plants["ticker"]
        login_id = plant.get_template_id("RequestLogin")
        original = plant._send_and_collect

        async def _with_aggregation(template_id, **kwargs):
            if template_id == login_id:
                kwargs["aggregated_quotes"] = True
            return await original(template_id, **kwargs)

        plant._send_and_collect = _with_aggregation
        _say(None, "requesting aggregated quotes (see --aggregated caveat below)")

    try:
        await client.connect(plants=[SysInfraType.TICKER_PLANT])
    except Exception as exc:
        _say(False, f"login rejected: {type(exc).__name__}")
        if aggregated:
            print(
                "       → --aggregated was on. `ResponseRithmicSystemInfo` advertises\n"
                "         has_aggregated_quotes=False for every system on every reachable\n"
                "         gateway, so rpCode 11 here is the server declining the flag, not\n"
                "         a credential problem. Re-run without --aggregated."
            )
        else:
            print(
                "       → the credentials reached Rithmic but were refused. Usual causes:\n"
                "         wrong password; the login is not API-enabled by the firm;\n"
                "         or app_name is not the one Rithmic conformance-certified for you."
            )
        return None

    _say(True, "authenticated on TICKER_PLANT")
    _say(None, f"ORDER_PLANT deliberately not opened (app_name={creds['app_name']!r})")
    return client


# --------------------------------------------------------------------------- C


async def probe_c_entitlements(client: RithmicClient) -> str | None:
    print("\nC. entitlements + contract resolution")

    try:
        exchanges = await client.list_exchanges()
    except Exception as exc:
        _say(False, f"list_exchanges failed: {exc!r}")
        exchanges = []

    # One response message per exchange; `exchange` and `entitlement_flag` are
    # singular, not parallel lists. level_1/level_2 are the strings that actually
    # say what you may subscribe to — LAST_TRADE and BBO are level 1.
    flag_name = ResponseListExchangePermissions.EntitlementFlag.Name
    entitled = [
        (r.exchange, flag_name(r.entitlement_flag), r.level_1_market_data, r.level_2_market_data)
        for r in exchanges
    ]
    if entitled:
        _say(True, f"{len(entitled)} exchange(s) returned")
        for ex, flag, l1, l2 in entitled:
            print(f"         - {ex}: {flag}, level_1={l1!r} level_2={l2!r}")
        if not any(ex == EXCHANGE for ex, *_ in entitled):
            _say(False, f"{EXCHANGE} is not in the entitlement list — no {SYMBOL} data")

    try:
        contract = await client.get_front_month_contract(SYMBOL, EXCHANGE)
    except Exception as exc:
        _say(False, f"front-month lookup for {SYMBOL}/{EXCHANGE} failed: {exc!r}")
        print("       → also returns empty inside the daily maintenance window.")
        return None

    _say(True, f"front month {SYMBOL}/{EXCHANGE} = {contract}")
    print(
        f"       note: the live config must pin the raw contract ({contract}), not the root —\n"
        "             contract_for() probes Databento and its roll map ends 2026-06-30."
    )
    return contract


# --------------------------------------------------------------------------- D


async def probe_d_ticks(client: RithmicClient, contract: str, seconds: int) -> bool:
    print(f"\nD. tick flow — {contract}/{EXCHANGE} for {seconds}s")

    kinds: Counter[str] = Counter()
    aggressors: Counter[str] = Counter()
    samples: list[dict] = []
    wire_ms: list[float] = []  # Rithmic's send stamp → our receive
    hop_ms: list[float] = []  # exchange's stamp → Rithmic's send stamp

    async def on_tick(data: dict) -> None:
        if data["data_type"] == DataType.LAST_TRADE:
            if not data.get("presence_bits", 0) & LastTradePresenceBits.LAST_TRADE:
                return
            now = time.time()
            kinds["trade"] += 1
            aggressors[str(data.get("aggressor"))] += 1
            if len(samples) < 5:
                samples.append(data)
            # The opening snapshot carries a stale stamp; it would swamp the stats.
            if data.get("is_snapshot") or not data.get("ssboe"):
                return
            sent = data["ssboe"] + data.get("usecs", 0) / 1e6
            wire_ms.append((now - sent) * 1000)
            if data.get("source_ssboe"):
                src = data["source_ssboe"] + data.get("source_usecs", 0) / 1e6
                hop_ms.append((sent - src) * 1000)
        else:
            kinds["bbo"] += 1

    client.on_tick += on_tick
    await client.subscribe_to_market_data(contract, EXCHANGE, DataType.LAST_TRADE)
    await client.subscribe_to_market_data(contract, EXCHANGE, DataType.BBO)

    try:
        for elapsed in range(seconds):
            await asyncio.sleep(1)
            if elapsed and elapsed % 5 == 0:
                print(f"       {elapsed:>3}s: {kinds['trade']} trades, {kinds['bbo']} quotes", flush=True)
    finally:
        for dt in (DataType.LAST_TRADE, DataType.BBO):
            try:
                await client.unsubscribe_from_market_data(contract, EXCHANGE, dt)
            except Exception:
                pass

    if not kinds["trade"] and not kinds["bbo"]:
        _say(False, "no data at all — login works but market data is not entitled")
        print("       → or the market is closed. NQ trades 18:00–17:00 ET, halt 17:00–18:00.")
        return False

    _say(kinds["trade"] > 0, f"{kinds['trade']} trades, {kinds['bbo']} quotes")

    if kinds["bbo"] and kinds["trade"]:
        ratio = kinds["bbo"] / kinds["trade"]
        print(
            f"       quotes are {ratio:.0f}× the trade volume. The engine's tick schema is\n"
            "       (ts_utc, price, size, side) — trades only — so the recorder should\n"
            "       subscribe LAST_TRADE alone and skip BBO entirely."
        )

    if wire_ms:
        print(f"\n       feed latency over {len(wire_ms)} trades:")
        if hop_ms:
            print(
                f"         exchange → Rithmic:      median {_pct(hop_ms, 0.5):7.1f}ms  "
                f"p90 {_pct(hop_ms, 0.9):7.1f}ms"
            )

        # `now - sent` compares our clock against Rithmic's, so it is one-way
        # latency plus a constant clock offset we cannot separate. Subtracting the
        # fastest observation cancels the offset and leaves delivery jitter, which
        # is the part that actually matters and is clock-independent.
        floor = min(wire_ms)
        spread = [w - floor for w in wire_ms]
        print(
            f"         arrival spread:          median {_pct(spread, 0.5):7.1f}ms  "
            f"p90 {_pct(spread, 0.9):7.1f}ms  max {max(spread):7.1f}ms"
        )
        print(
            f"       Your clock reads ~{-floor / 1000:.1f}s behind Rithmic's, so absolute\n"
            "       one-way latency is not measurable here — use --ping for that.\n"
            "       Arrival spread is an UPPER bound on network jitter, not a measurement\n"
            "       of it: bursts are timestamped as this single-threaded callback drains\n"
            "       them, so a 50-print burst charges its own processing to the tail. Take\n"
            "       it as 'nothing pathological' unless it exceeds seconds.\n"
            "       Phase 5 note: the recorder must timestamp from ssboe/usecs, never\n"
            "       from local time, or a drifting host clock corrupts the tape."
        )

    if samples:
        print("\n       first prints (raw — check field names before writing the recorder):")
        for s in samples:
            keys = {k: v for k, v in s.items() if k not in ("data_type",)}
            print(f"         {keys}")
        print(f"\n       aggressor values seen: {dict(aggressors)}")
        print(
            "       → Phase 6 note: verify this mapping empirically against the local mid.\n"
            "         Databento 'B' = BUY aggressor and sits ~0.35pt above mid. Do not\n"
            "         assume Rithmic encodes it the same way, and do not propagate the\n"
            "         known sign flip at src/journal/sim/interactions.py:266."
        )
    return kinds["trade"] > 0


# --------------------------------------------------------------------------- main


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", choices=["a", "b", "c", "d", "all"], default="all")
    ap.add_argument("--seconds", type=int, default=30, help="how long to stream in probe D")
    ap.add_argument("--debug", action="store_true", help="full rithmic protocol logging")
    ap.add_argument(
        "--discover",
        action="store_true",
        help="list every gateway and the systems it serves; needs no credentials",
    )
    ap.add_argument(
        "--ping",
        action="store_true",
        help="round-trip time to every gateway; needs no credentials",
    )
    ap.add_argument("--samples", type=int, default=5, help="round trips per gateway for --ping")
    ap.add_argument(
        "--aggregated",
        action="store_true",
        help="ask for aggregated quotes at login (every gateway currently says it has none)",
    )
    args = ap.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("rithmic").setLevel(logging.DEBUG)

    if args.discover:
        return await discover()
    if args.ping:
        return await ping(args.samples)

    creds = _creds()

    # Attach before any socket opens — a rejected login logs the request verbatim.
    # Handler-level, not logger-level: records are emitted by `rithmic.plant.*`
    # children, and a filter on an ancestor *logger* is skipped during
    # propagation — only handler filters see every record. async_rithmic installs
    # its own stdout handler on `rithmic`, so both trees need covering.
    redact = _RedactSecrets(creds["password"])
    for logger_name in ("", "rithmic"):
        for handler in logging.getLogger(logger_name).handlers:
            handler.addFilter(redact)

    print(f"gateway     {creds['url']}")
    print(f"system      {creds['system_name']}")
    print(f"user        {creds['user']}")
    print(f"app         {creds['app_name']} {creds['app_version']}")

    if not await probe_a_systems(creds):
        return 1
    if args.probe == "a":
        return 0

    client = await probe_b_login(creds, aggregated=args.aggregated)
    if client is None:
        return 1

    try:
        if args.probe == "b":
            return 0

        contract = await probe_c_entitlements(client)
        if contract is None:
            return 1
        if args.probe == "c":
            return 0

        ok = await probe_d_ticks(client, contract, args.seconds)
    finally:
        await client.disconnect()

    print("\n" + ("VERDICT: the API works with these credentials." if ok else "VERDICT: see FAILs above."))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
