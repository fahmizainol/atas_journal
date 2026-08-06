"""Fetch the sessions nobody was connected for, into the live tick store.

The feed's backfill makes *this* session whole however late you connect, and the
API sweeps a trailing window on startup. This is the manual, deep version: the
one-time pull of a contract's whole life, and the pull you do **before a roll**.

THE DEADLINE THIS EXISTS FOR. Rithmic's replay serves a listed contract back
roughly 120 days and an **expired** one not at all, at any depth — measured, not
assumed (docs/live-shadow-plan.md § Tick replay). So a contract's history is free
right up to the moment it rolls, and gone immediately after. Run this against the
outgoing contract before you change ``LIVE_SYMBOL``.

ONE SESSION PER LOGIN. Rithmic force-logs-out the older session when a second
one connects. This opens its own, so **stop the live feed first** — the script
checks and refuses rather than kicking your own feed off mid-session.

Writes only to ``data/live/ticks/{SYMBOL}/{DATE}/`` (decision 3). Harvesting July
does not make July backtestable: ``get_day_ticks``, which is what the engine
loads a session with, reads the Databento cache and does not fall through to the
live store. The gates and the weekly seed do, which is the point.

Usage:
    uv run python demo/rithmic_harvest.py --symbol NQU6 --from 2026-06-11 --dry-run
    uv run python demo/rithmic_harvest.py --symbol NQU6 --from 2026-06-11
    uv run python demo/rithmic_harvest.py --symbol NQU6 --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from journal.live import harvest  # noqa: E402
from journal.live import state as statemod  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402


def _plan(symbol: str, start: date, end: date) -> list[date]:
    days = harvest.pending(symbol, start, end)
    print(f"{symbol}: {len(days)} closed session(s) with something to fetch, "
          f"{start.isoformat()} → {end.isoformat()}\n")
    if not days:
        print("Nothing to do — every session in the range is whole or already "
              "flagged.")
        return days
    for day in days:
        gaps = harvest.gaps_in(symbol, day)
        have = tickmod.live_day_ticks(symbol, day)
        span = " + ".join(
            f"{pd.Timestamp(a, tz='UTC').tz_convert('America/New_York'):%H:%M}"
            f"→{pd.Timestamp(b, tz='UTC').tz_convert('America/New_York'):%H:%M}"
            for a, b in gaps)
        print(f"  {day.isoformat()}  have {0 if have is None else len(have):>8,}  "
              f"need {span}")
    return days


async def _run(symbol: str, start: date, end: date, exchange: str) -> int:
    t0 = time.perf_counter()
    done = 0

    def report(r: dict) -> None:
        nonlocal done
        done += 1
        if r["skipped"]:
            return
        note = f" — {r['error']}" if r.get("error") else ""
        extra = f", {r['aggregated']} aggregated bars" if r.get("aggregated") else ""
        print(f"  {r['date']}  {r['rows']:>8,} prints{extra}{note}", flush=True)

    got = await harvest.sweep_standalone(symbol, start, end, exchange, on_day=report)

    rows = sum(d["rows"] for d in got)
    failed = [d for d in got if d.get("error")]
    print(f"\n{len(got)} session(s) in {time.perf_counter() - t0:.0f}s, "
          f"{rows:,} prints written")
    if failed:
        print(f"{len(failed)} session(s) failed and are NOT flagged complete — "
              "re-run to retry them:")
        for d in failed:
            print(f"  {d['date']}  {d['error']}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True, help="RAW contract, e.g. NQU6")
    ap.add_argument("--from", dest="start", help="first session date, YYYY-MM-DD")
    ap.add_argument("--days", type=int, help="or: this many days back from today")
    ap.add_argument("--to", dest="end", help="last session date (default: today)")
    ap.add_argument("--exchange", default="CME")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be fetched and stop")
    args = ap.parse_args()

    symbol = args.symbol.strip().upper()
    if len(symbol) < 4 or symbol in {"NQ", "ES", "CL", "GC"}:
        sys.exit(f"{symbol!r} looks like a root — pin the raw contract (e.g. NQU6). "
                 "Roots resolve through Databento, which a live path must not touch.")
    if not args.start and not args.days:
        sys.exit("give --from YYYY-MM-DD or --days N")

    start = (date.fromisoformat(args.start) if args.start
             else harvest.default_start(args.days))
    end = date.fromisoformat(args.end) if args.end else pd.Timestamp.now(tz="UTC").date()

    # Refuse rather than kick: a second login force-logs-out the first, and doing
    # that to a feed that is mid-session would cost the prints it is recording.
    live = statemod.current()
    if live is not None and live.feed is not None and getattr(live.feed, "running", False):
        sys.exit(f"a live feed is connected ({live.session.symbol} "
                 f"{live.session.day}). Rithmic allows one session per login, so "
                 "this would log it out — stop the feed first. (It sweeps earlier "
                 "sessions on its own connection anyway; this script is for the "
                 "deep pull.)")

    if not _plan(symbol, start, end):
        return 0
    if args.dry_run:
        print("\n--dry-run: nothing fetched.")
        return 0

    print(f"\nfetching (~12s per session, writing to "
          f"{tickmod.LIVE_TICK_DIR / symbol}):")
    return asyncio.run(_run(symbol, start, end, args.exchange))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted — sessions already written keep their flag; "
              "re-run to continue")
        sys.exit(130)
