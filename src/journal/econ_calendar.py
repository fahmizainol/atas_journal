"""USD economic calendar, scraped off ForexFactory's weekly pages.

**Why the page and not the feed.** FF publishes ``ff_calendar_thisweek.json`` but it
is this-week-only and carries no ``actual``. The calendar *page* embeds the whole
week as JSON (``calendarComponentStates``) including actual / forecast / previous /
revision, and any past week is one GET away — so history is a backfill loop.

**Time.** Every event carries ``dateline``, a true UTC epoch. The page's
``timeLabel`` ("2:00am") is in whatever zone FF guessed for the guest (UTC+8 from
this box) and is kept only to spot "All Day" / "Tentative" rows. Never parse it.

**What's banked.** Every USD row, all impacts, raw — filtering to high/medium is the
reader's job, so a looser filter later needs no re-scrape. One JSON file per FF
week, keyed by the Sunday that starts it. Adjacent weeks can overlap at the edges
(FF's week boundary is in the guest zone), so readers dedupe on ``id``.

**Finality.** Past weeks still move: actuals land at release and revisions a week
later. A week file is *settled* once it was fetched ≥ SETTLE_DAYS after the week
ended; anything else is refetched when older than STALE_S.

A context layer, not a signal: ``docs/research/event-day-overlay.md`` found no
event-day effect on any strategy.
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import CACHE_DIR

ECON_CACHE = CACHE_DIR / "econ"

URL = "https://www.forexfactory.com/calendar?week={slug}"
SETTLE_DAYS = 10         # revisions land ~a week after the print
STALE_S = 3600           # refetch an unsettled week at most hourly
KEEP = ("id", "dateline", "name", "impactName", "timeLabel", "timeMasked",
        "actual", "forecast", "previous", "revision", "notice", "soloUrl")
IMPACTS = ("high", "medium", "low", "holiday", "non-economic")

_EVENT_RX = re.compile(r'\{"id":\d+,[^{}]*?"dateline":\d+[^{}]*\}')


def week_start(d: date) -> date:
    """The Sunday that opens *d*'s FF week."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _slug(d: date) -> str:
    return f"{d.strftime('%b').lower()}{d.day}.{d.year}"


def _path(sunday: date) -> Path:
    return ECON_CACHE / f"{sunday.isoformat()}.json"


def parse(html: str) -> list[dict]:
    """USD events out of one calendar page. The state blob repeats each event, so
    dedupe on id."""
    out, seen = [], set()
    for m in _EVENT_RX.finditer(html):
        try:
            e = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if e.get("currency") != "USD" or e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append({k: e.get(k) for k in KEEP})
    return sorted(out, key=lambda e: (e["dateline"], e["id"]))


def fetch_week(sunday: date) -> dict:
    """Scrape one week and bank it. Raises on a non-200 or an empty parse — an
    empty USD week doesn't exist, so zero rows means the page changed shape."""
    from scrapling.fetchers import Fetcher  # heavy import; only the scraper pays it

    page = Fetcher.get(URL.format(slug=_slug(sunday)), impersonate="chrome",
                       stealthy_headers=True, timeout=30)
    if page.status != 200:
        raise RuntimeError(f"FF {sunday}: HTTP {page.status}")
    html = page.html_content
    events = parse(html)
    if not events:
        raise RuntimeError(f"FF {sunday}: no USD events parsed ({len(html)} bytes)")
    rec = {"week": sunday.isoformat(), "fetched_at": int(time.time()), "events": events}
    ECON_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = _path(sunday).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
    tmp.replace(_path(sunday))
    return rec


def _read(sunday: date) -> dict | None:
    p = _path(sunday)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_settled(rec: dict) -> bool:
    week_end = date.fromisoformat(rec["week"]) + timedelta(days=7)
    fetched = datetime.fromtimestamp(rec["fetched_at"], timezone.utc).date()
    return fetched >= week_end + timedelta(days=SETTLE_DAYS)


def needs_fetch(sunday: date, rec: dict | None = None) -> bool:
    rec = rec if rec is not None else _read(sunday)
    if rec is None:
        return True
    return not is_settled(rec) and time.time() - rec["fetched_at"] > STALE_S


def events(start: date, end: date, impacts: tuple[str, ...] = IMPACTS,
           refresh_limit: int = 0) -> list[dict]:
    """Banked USD events whose UTC dateline falls in [start, end] (dates, UTC days
    inclusive), filtered to *impacts*. With *refresh_limit* > 0, up to that many
    missing/stale weeks are fetched on the way (newest first); a failed fetch falls
    back to whatever is banked."""
    weeks, s = [], week_start(start - timedelta(days=1))
    while s <= end:
        weeks.append(s)
        s += timedelta(days=7)
    budget = refresh_limit
    for w in reversed(weeks):
        if budget <= 0:
            break
        if w <= date.today() + timedelta(days=7) and needs_fetch(w):
            budget -= 1
            try:
                fetch_week(w)
            except Exception as e:  # noqa: BLE001 — serve the bank, don't 500
                print(f"[econ] fetch {w} failed: {type(e).__name__}: {e}", flush=True)
    lo = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
    hi = int(datetime.combine(end + timedelta(days=1), datetime.min.time(),
                              timezone.utc).timestamp())
    out, seen = [], set()
    for w in weeks:
        rec = _read(w)
        for e in (rec or {}).get("events", []):
            if e["id"] in seen or not lo <= e["dateline"] < hi:
                continue
            if (e.get("impactName") or "").lower() not in impacts:
                continue
            seen.add(e["id"])
            out.append(e)
    return sorted(out, key=lambda e: (e["dateline"], e["id"]))


def backfill(since: date, until: date | None = None, pause_s: float = 2.0) -> None:
    """Newest week first down to *since*, skipping settled weeks. Polite: one page
    at a time with a jittered pause; failures are logged and skipped, so a rerun
    picks them up."""
    w = week_start(until or date.today() + timedelta(days=7))  # next week is scheduled already
    stop = week_start(since)
    done = skipped = failed = 0
    while w >= stop:
        if not needs_fetch(w):
            skipped += 1
        else:
            try:
                rec = fetch_week(w)
                done += 1
                print(f"[econ] {w}: {len(rec['events'])} USD events", flush=True)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[econ] {w}: FAILED {type(e).__name__}: {e}", flush=True)
            time.sleep(pause_s + random.uniform(0, pause_s))
        w -= timedelta(days=7)
    print(f"[econ] backfill: {done} fetched, {skipped} already settled, {failed} failed",
          flush=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Backfill the FF USD calendar.")
    ap.add_argument("--since", default="2024-03-03", help="oldest date (default: tape start)")
    ap.add_argument("--until", default=None)
    ap.add_argument("--pause", type=float, default=2.0)
    a = ap.parse_args()
    backfill(date.fromisoformat(a.since),
             date.fromisoformat(a.until) if a.until else None, a.pause)
