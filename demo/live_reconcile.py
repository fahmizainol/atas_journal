"""Phase 6 — reconcile a recorded live session against the backtest.

Runs the three comparisons in the order that makes the last one mean anything:
tape fidelity, then prefix integrity, then signal agreement (see
``journal.live.reconcile``). Reads only: it never fetches from Databento and
never writes to either tick store.

Usage:
    uv run python demo/live_reconcile.py --list
    uv run python demo/live_reconcile.py --symbol NQU6 --date 2026-08-05
    uv run python demo/live_reconcile.py --symbol NQU6 --date 2026-08-05 --json out.json

Stage 1 and stage 3 need a Databento day for the same date — the corpus ends
2026-06-30, so a reconciled date has to be bought first. Stage 2 needs neither
and runs the morning after a recording with nothing bought at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from journal.live import reconcile as rec  # noqa: E402
from journal.live import recorder  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402


def _list() -> int:
    days = recorder.recorded_days()
    if not days:
        print("Nothing recorded yet. data/live/ticks/ is empty.")
        return 0
    print(f"{'symbol':<10} {'session':<12} {'chunks':>7} {'rows':>10}  "
          f"{'closed':<7} databento")
    for sym, day in days:
        man = recorder.read_manifest(sym, day) or {}
        bought = tickmod.have_segment(sym, day, "rth")
        print(f"{sym:<10} {day.isoformat():<12} "
              f"{len(tickmod.live_chunks(sym, day)):>7} "
              f"{man.get('rows', 0):>10,}  {str(man.get('closed')):<7} "
              + ("cached" if bought else "not bought — stages 1 and 3 unavailable"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show recorded sessions")
    ap.add_argument("--symbol", help="raw contract, e.g. NQU6")
    ap.add_argument("--date", dest="day", help="session date, YYYY-MM-DD")
    ap.add_argument("--json", dest="out", help="also write the full result here")
    args = ap.parse_args()

    if args.list or not (args.symbol and args.day):
        if not args.list:
            print("Give --symbol and --date, or --list to see what is recorded.\n")
        return _list()

    res = rec.reconcile(args.symbol.upper(), date.fromisoformat(args.day))
    print(rec.format_report(res))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, default=str))
        print(f"\nfull result -> {args.out}")

    # Exit non-zero when a stage that *could* run did not pass, so this is usable
    # from a cron on the always-on host. A stage that could not run at all
    # (nothing bought) is not a failure — it is a missing input.
    bad = [s for s in ("tape_fidelity", "prefix_integrity", "signal_agreement")
           if res[s].get("status") in ("failed", "degraded")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
