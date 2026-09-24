"""Bank Cboe's delayed NDX and QQQ option chains as intraday snapshots.

Run by Windows Task Scheduler every 15 minutes (see ``install-gex-task.ps1``).
Replaces the once-a-day WSL cron (``demo/gex_demo.py --collect-only``), which
died when the project moved to the Windows side — nothing was banked after
2026-09-23 12:28 UTC.

**Why every 15 minutes.** Open interest is published once a day, but traded
volume — the 0DTE flow a volume-based GEX read is built on — accumulates all
session, and the chart's gamma layer steps through each snapshot from the moment
it was published. The CDN is itself 15 minutes delayed, so polling faster than
this buys nothing.

**When it fetches.** Every run on weekdays 06:00–16:45 ET (the pre-open OCC open
interest print through the close). Outside that, only on the run nearest each
third hour, so a weekend or holiday republish is still caught without pulling
~12 MB every quarter hour for nothing.

**What it keeps.** Only the fields ``journal.gex`` reads — the underlying's quote
and, per contract, the OCC symbol, IV, open interest, volume, delta and gamma —
gzipped. A full chain pair is ~12 MB raw; this is ~a tenth of that per snapshot.

**Dedupe.** The CDN re-stamps its file on weekends and overnight without any new
data, so a snapshot is only written when its content key (the underlying's quote
time, total open interest, total volume) differs from the last one banked.

    .venv\\Scripts\\python tools\\gex_collect.py            # gated run (the task)
    .venv\\Scripts\\python tools\\gex_collect.py --force    # fetch regardless of clock
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.gex import GEX_CACHE, SNAP_DIR, books, read_book, snap_path  # noqa: E402

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
SYMBOLS = {"NDX": "_NDX", "QQQ": "QQQ"}
ET = ZoneInfo("America/New_York")
LOG = GEX_CACHE / "collect.log"

UNDERLYING_KEYS = ("symbol", "current_price", "last_trade_time", "open", "high", "low",
                   "close", "prev_day_close", "iv30")
OPTION_KEYS = ("option", "iv", "open_interest", "volume", "delta", "gamma")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def should_run(now_et: datetime) -> bool:
    if now_et.weekday() < 5 and time(6, 0) <= now_et.time() <= time(16, 45):
        return True
    return now_et.hour % 3 == 0 and now_et.minute < 15


def slim(raw: dict) -> dict:
    d = raw.get("data") or {}
    return {
        "timestamp": raw.get("timestamp"),
        "symbol": raw.get("symbol"),
        "data": {
            **{k: d.get(k) for k in UNDERLYING_KEYS},
            "options": [{k: o.get(k) for k in OPTION_KEYS} for o in d.get("options", [])],
        },
    }


def content_key(raw: dict) -> tuple:
    d = raw.get("data") or {}
    opts = d.get("options", [])
    return (
        d.get("last_trade_time"),
        sum(o.get("open_interest") or 0 for o in opts),
        sum(o.get("volume") or 0 for o in opts),
    )


def collect_one(sym: str) -> bool:
    try:
        with urllib.request.urlopen(CBOE.format(SYMBOLS[sym]), timeout=90) as r:
            raw = json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001 — a scheduled job must not die
        log(f"! {sym}: fetch failed ({exc.__class__.__name__}: {exc})")
        return False
    try:
        stamp = datetime.fromisoformat(raw["timestamp"])
    except (KeyError, TypeError, ValueError):
        log(f"! {sym}: no usable timestamp in the payload")
        return False
    path = snap_path(sym, stamp)
    if path.exists():
        log(f"  {sym}: stamp {stamp} already banked")
        return False
    last = books(sym)
    if last:
        prev = read_book(last[-1].path)
        if prev is not None and content_key(prev) == content_key(raw):
            log(f"  {sym}: unchanged since {last[-1].key} — not banked")
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(slim(raw), fh, separators=(",", ":"))
    tmp.replace(path)
    n = len(raw.get("data", {}).get("options", []))
    q = (raw.get("data") or {}).get("last_trade_time")
    log(f"  {sym}: banked {path.name}  {n:,} contracts  quote {q} ET  "
        f"{path.stat().st_size / 1e6:.2f} MB")
    return True


def main() -> int:
    now_et = datetime.now(ET)
    if "--force" not in sys.argv and not should_run(now_et):
        return 0
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    log(f"gex collect ({now_et:%a %H:%M} ET)")
    return sum(collect_one(s) for s in SYMBOLS)


if __name__ == "__main__":
    main()
    raise SystemExit(0)
