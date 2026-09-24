"""Dealer gamma regime — the GEX curve, drawn against price.

One HTML page that answers the only question Creamer actually asks of gamma
exposure: *what kind of day am I walking into, and how far is the boundary?*

Two panels share a price axis:

  * left  — dollar-gamma per strike, drawn like a volume profile. Green above
            zero, red below. This is where the call/put walls live.
  * right — net GEX as a function of spot. The zero crossing IS the gamma flip.
            The curve is the part the three-lines-on-a-chart view hides: how
            fast the regime deteriorates if price slides.

Drag the spot marker and everything recomputes. That is the point of the page —
net GEX is not a property of the day, it is a property of *where price is*, and
the regime can inverte mid-session without a single position changing hands.

Data is the free Cboe delayed-quote CDN (no key, no signup), which ships gamma
and open_interest per contract:

    https://cdn.cboe.com/api/global/delayed_quotes/options/QQQ.json
    https://cdn.cboe.com/api/global/delayed_quotes/options/_NDX.json

Three things that are easy to get wrong, all handled here:

  1. **Stale expiries.** The pre-open snapshot still carries yesterday's
     expired contracts, with stale gamma attached. Summing them naively
     overstated net GEX by 36% the first time round. Everything is filtered to
     DTE >= 0.
  2. **Open interest is frozen intraday.** OCC publishes it once, pre-open. So
     the whole curve is knowable at 08:00 and intraday is a lookup, not a
     Black-Scholes sweep. That is why this page can precompute and why a live
     badge needs no options feed at all.
  3. **The clock barely matters.** Holding OI and spot fixed, six hours of
     decay moves the flip by 0.12%. Spot dominates by ~10x. The time slider is
     here to show that, not because it is load-bearing.

Gamma is recomputed with Black-Scholes (r=0, each contract's own IV held fixed
as spot moves — sticky-strike) rather than read from the feed, because the feed
only gives gamma at the current spot and the whole page is about other spots.
The recompute lands within ~6% of Cboe's own greeks at spot, which is fine for
a binary regime read and NOT fine for trading the wall levels precisely.

NOTHING HERE IS VALIDATED. There is no historical GEX in this repo, so no A/B
has been run and none can be until a collector has banked ~6 months. The page
is a context overlay and a probe, not a signal. Do not trade the walls —
Creamer doesn't either, and level-bounce geometry is nulled four times over
here (stable-level S/R, VAH snap, LVN retrace, aVWAP reclaim).

Writes ``docs/research/gex-regime.html``, which the Lab's Research tab lists and
serves; no external assets, so the page also opens straight off disk.

    uv run python demo/gex_demo.py                # cached pull if fresh today
    uv run python demo/gex_demo.py --collect-only # bank the book only (cron)
    uv run python demo/gex_demo.py --refetch    # force a new pull
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent
HTML_OUT = ROOT / "docs" / "research" / "gex-regime.html"

sys.path.insert(0, str(ROOT / "src"))

# The gamma math lives in the package, not here — the Live chart's regime badge
# reads the same books through the same functions, and two definitions of the
# flip on two surfaces is exactly the drift this repo keeps getting bitten by.
from journal.gex import (GEX_CACHE as CACHE, GRID_N, GRID_PCT,  # noqa: E402
                         MULT, OCC_RX, SESSION_H, by_strike, contracts,
                         flip_of, net_gex)

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
SYMBOLS = {"QQQ": "QQQ", "NDX": "_NDX"}

TIME_SLICES = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 6.4]


# --- Data -----------------------------------------------------------------
def fetch(sym: str, *, refetch: bool = False) -> dict:
    """Cboe's delayed chain, cached per symbol per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sym}_{date.today().isoformat()}.json"
    if path.exists() and not refetch:
        print(f"  {sym}: cached  {path.name}")
        return json.loads(path.read_text())
    url = CBOE.format(SYMBOLS[sym])
    print(f"  {sym}: fetching {url}")
    with urllib.request.urlopen(url, timeout=60) as r:
        raw = json.loads(r.read().decode())
    path.write_text(json.dumps(raw))
    return raw


def _latest_stamp(sym: str) -> str | None:
    """CDN publish stamp of the newest snapshot already banked for this symbol."""
    files = sorted(CACHE.glob(f"{sym}_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text()).get("timestamp")
    except (json.JSONDecodeError, OSError):
        return None


def collect() -> int:
    """Bank today's chains and exit. The cron entry point.

    Deliberately *not* time-critical. Open interest is published once by the OCC
    and then frozen for the session, so any pull during the day returns the same
    book — which means this can run hourly and simply catch whichever hour the
    machine happens to be awake for. That matters more than precision: a missed
    day cannot be backfilled at any price, and this box reboots unpredictably.

    Dedupes on the CDN's own publish stamp rather than the calendar, so weekends
    and market holidays (where the CDN keeps serving the last session) do not
    litter the history with duplicate books.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    banked = 0
    for sym in SYMBOLS:
        path = CACHE / f"{sym}_{today}.json"
        if path.exists():
            print(f"  {sym}: already banked {path.name}")
            continue
        prev = _latest_stamp(sym)
        try:
            with urllib.request.urlopen(CBOE.format(SYMBOLS[sym]), timeout=60) as r:
                raw = json.loads(r.read().decode())
        except Exception as exc:                      # noqa: BLE001 — cron must not die
            print(f"! {sym}: fetch failed ({exc.__class__.__name__}: {exc})")
            continue
        stamp = raw.get("timestamp")
        if prev and stamp == prev:
            print(f"  {sym}: unchanged book ({stamp}) — weekend/holiday, not banked")
            continue
        path.write_text(json.dumps(raw))
        n = len(raw.get("data", {}).get("options", []))
        print(f"  {sym}: banked {path.name}  {n:,} contracts  stamp {stamp}")
        banked += 1
    return banked


def build(sym: str, raw: dict, asof: date) -> dict:
    rows, spot, note = contracts(raw, asof)
    print(f"  {sym}: {note}  spot {spot:,.2f}")

    lo, hi = spot * (1 - GRID_PCT), spot * (1 + GRID_PCT)
    grid = [lo + (hi - lo) * i / (GRID_N - 1) for i in range(GRID_N)]

    slices = []
    for h in TIME_SLICES:
        curve = [{"s": s, "g": net_gex(rows, s, h)} for s in grid]
        f = flip_of(curve)
        slices.append({
            "h": h,
            "label": _clock(h),
            "curve": [round(c["g"] / 1e9, 4) for c in curve],
            "flip": round(f, 2) if f else None,
            "at_spot": round(net_gex(rows, spot, h) / 1e9, 4),
        })

    strikes = [r for r in by_strike(rows, spot, 0.0) if lo * 0.97 <= r["k"] <= hi * 1.03]
    call_wall = max(strikes, key=lambda r: r["c"])["k"] if strikes else None
    put_wall = max(strikes, key=lambda r: r["p"])["k"] if strikes else None

    # what the feed's own greeks say, as a cross-check on the recompute
    feed = 0.0
    for o in raw["data"]["options"]:
        m = OCC_RX.match(o["option"])
        if not m:
            continue
        _, ymd, cp, _k = m.groups()
        exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
        if (exp - asof).days < 0 or not o["gamma"] or not o["open_interest"]:
            continue
        feed += (1 if cp == "C" else -1) * o["gamma"] * o["open_interest"] * MULT * spot * spot * 0.01

    return {
        "sym": sym,
        "spot": round(spot, 2),
        "grid": [round(s, 2) for s in grid],
        "slices": slices,
        "strikes": [{"k": r["k"], "c": round(r["c"] / 1e9, 5), "p": round(r["p"] / 1e9, 5),
                     "net": round(r["net"] / 1e9, 5)} for r in strikes],
        "call_wall": call_wall,
        "put_wall": put_wall,
        "n_live": len(rows),
        "note": note,
        "feed_gex": round(feed / 1e9, 3),
        "recompute_gex": round(net_gex(rows, spot, 0.0) / 1e9, 3),
        "timestamp": raw.get("timestamp", "?"),
    }


def _clock(h: float) -> str:
    mins = int(round(9 * 60 + 30 + h * 60))
    return f"{mins // 60:02d}:{mins % 60:02d}"


# --- Output ---------------------------------------------------------------
def _write_html(payload: dict) -> None:
    template = (OUT_DIR / "_gex_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(template.replace("__GEX_JSON__", json.dumps(payload)))


def main() -> None:
    if "--collect-only" in sys.argv:
        print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] gex collect")
        raise SystemExit(0 if collect() >= 0 else 1)

    refetch = "--refetch" in sys.argv
    asof = date.today()
    print(f"Cboe delayed chains, as of {asof}:")

    books = {}
    for sym in SYMBOLS:
        books[sym] = build(sym, fetch(sym, refetch=refetch), asof)

    payload = {
        "asof": asof.isoformat(),
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "books": books,
        "session_h": SESSION_H,
    }
    _write_html(payload)

    print("\nregime read:")
    for sym, b in books.items():
        g = b["slices"][0]["at_spot"]
        regime = "POSITIVE (dampening)" if g > 0 else "NEGATIVE (amplifying)"
        flip = b["slices"][0]["flip"]
        dist = f"{100 * (flip / b['spot'] - 1):+.2f}%" if flip else "  n/a"
        print(f"  {sym:>4}  spot {b['spot']:>10,.2f}   net GEX {g:>+7.2f}B  {regime}")
        print(f"        flip {flip:>10,.2f} ({dist})   call wall {b['call_wall']:,}   "
              f"put wall {b['put_wall']:,}")
        print(f"        recompute {b['recompute_gex']:+.2f}B vs feed greeks "
              f"{b['feed_gex']:+.2f}B  ({abs(b['recompute_gex'] / b['feed_gex'] - 1) * 100:.1f}% apart)")

    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


if __name__ == "__main__":
    main()
