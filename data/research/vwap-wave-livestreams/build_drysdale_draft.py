"""Build the Drysdale-livestream NQ trade table for the Drafts viewer.

Source is ``trades.json`` — a hand-extraction from six live streams, not an
engine and not a study. The transcript gives far less than a trade row needs:
of the NQ trades he actually held, about half carry a spoken entry price, one
carries a spoken exit price, and none carry a stop, a size or a P&L. So the
governing rule here is: **draw only what he said, and leave the rest visibly
unfinished.**

  entry price  spoken price when he said one; otherwise the OPEN of the minute
               bar he named. Open, not the bar's extreme — using the low for a
               long would hand him a fill he never claimed.
  entry time   the stated clock time, verbatim (passthrough drafts skip the
               next-bar rule; that rule guards study events against the
               touch-bar artifact, and these are decisions, not events).
  exit         his spoken exit price, or his spoken result magnitude applied to
               the entry (see STATED_POINTS), placed at the first bar that
               trades through it. Nothing else. A trade he never sized aloud
               exits at its own entry and is marked ``open`` — on the chart
               that is a flat one-bar sliver at his entry, i.e. an entry
               marker, which is exactly how much we know.
  stop         set equal to entry, so ``r_multiple`` comes out 0 everywhere
               instead of manufacturing an R off a stop he never spoke.
  net_pnl      points x point value on ONE contract, gross. He traded unstated
               size across several accounts; this is a scale, not his P&L.

An earlier pass exited unresolved trades at a flat 30-minute horizon. It is
recorded here because the failure is instructive: the horizon contradicted his
own stated results outright — +293 points on the trade he called a "$200 loss",
-210 on one he called "win, flat quickly", -127 on "win, big". He is a scalper
narrating partial exits; any fixed hold invents a trade he did not take, and
the rect's win/loss colour then reads as a result. Hence entry-only.

The draft therefore answers "where did he get in, against which levels?" —
which is the claim the teardown actually verified (level precision real,
directional narration not) — and declines to answer "how did he do?".

Scope: NQ only — the tick cache is NQ, so his ES and YM trades cannot be drawn
at all. Rows he never actually held (a missed setup, an aborted hunt) are
dropped; rows whose instrument is unstated are dropped unless the spoken price
is only tradeable on NQ.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from journal.config import point_value
from journal.sim.drafts import _day_bars   # same ON+RTH minute frame the tape draws

ROOT = Path("/home/afahmi/repos/atas_journal")
SRC = ROOT / "data/research/vwap-wave-livestreams/trades.json"
OUT = ROOT / "data/research/vwap-wave-livestreams/drysdale_nq_trades.parquet"

SYMBOL = "NQ"
ET = "America/New_York"

# Trades to skip, keyed (date, et): he never held these.
NOT_HELD = {
    ("2026-06-04", "10:28"),   # "VWAP bounce short hunt; aborted on CVD buyers"
    ("2026-06-12", "09:48"),   # "two VWAP shorts, caught neither" — missed
}

# Unstated-instrument rows whose spoken price places them on NQ anyway.
# 28786.75 is last tradeable on NQ at 11:23 that session; no ES/YM read fits.
FORCE_NQ = {("2026-06-11", "~11:25")}

# Result magnitudes HE spoke, in points, signed in his favour. Only outright
# point counts qualify: "$200" and "$2000" are unconvertible (size unstated),
# "less than 50 points" and "win (small)" are not numbers, and a bare "win" is
# a direction without a distance. Everything absent here exits ``open``.
STATED_POINTS = {
    ("2026-04-29", "~10:29"): 10,    # "It's only 10 points here"
    ("2026-06-04", "09:34"): 200,    # "win ~200pt"
    ("2026-06-11", "10:28:39"): 20,  # "I'll fit 20 points there"
    ("2026-06-11", "~10:34"): 100,   # +50/+80/+100 callouts, last one at 10:36:59
    ("2026-06-11", "~11:25"): 4,     # "peel off one, quick four points"
    ("2026-06-12", "10:03"): 90,     # "win ~+90"
    ("2026-06-12", "10:20"): 30,     # "win +30"
    ("2026-06-12", "10:24"): 20,     # "win +20"
    ("2026-06-12", "10:43"): 40,     # "win +40"
}

SETUP_UNNAMED = "unnamed"


def is_nq(session_date: str, tr: dict) -> bool:
    ins = str(tr.get("instrument") or "")
    if (session_date, tr.get("et")) in FORCE_NQ:
        return True
    return ins.startswith("NQ")


def et_stamp(day: str, et: str) -> pd.Timestamp:
    """'~10:26', '10:26:48' -> a tz-aware ET timestamp. The tilde marks a time
    we read off surrounding narration rather than a hard cue; it shifts nothing
    about the minute, so it is stripped."""
    hms = et.lstrip("~").strip()
    if len(hms.split(":")) == 2:
        hms += ":00"
    return pd.Timestamp(f"{day} {hms}", tz=ET)


def bar_at(bars: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    """Index of the bar containing ``ts`` (bars carry minute-opening stamps)."""
    ns = bars["ts_utc"].astype("int64").to_numpy()
    i = int((ns <= ts.tz_convert("UTC").value).sum()) - 1
    return i if 0 <= i < len(bars) else None


# How far short of a spoken magnitude the tape may fall and still be treated as
# that magnitude. His point counts are round and prefixed "~"; on 2026-06-04 the
# session bottomed 1 point above the "~200pt" mark. Snapping to the best price
# actually attained keeps the exit on a real print; refusing would drop the
# day's headline trade over a rounding word.
REACH_TOL = 5.0


def reach(bars: pd.DataFrame, e: int, entry: float, sgn: int,
          dist: float) -> tuple[int, float] | None:
    """First bar after ``e`` to trade ``dist`` points in the trade's favour,
    else the best it ever got if that falls within REACH_TOL."""
    ext = (bars["high"] if sgn > 0 else bars["low"]).to_numpy()[e + 1:]
    if not len(ext):
        return None
    fav = sgn * (ext - entry)
    hit = (fav >= dist).nonzero()[0]
    if len(hit):
        j = e + 1 + int(hit[0])
        return j, entry + sgn * dist
    best = int(fav.argmax())
    if dist - fav[best] <= REACH_TOL:
        return e + 1 + best, float(ext[best])
    return None


def resolve_exit(tr: dict, bars: pd.DataFrame, e: int, entry: float,
                 sgn: int, key: tuple[str, str]) -> tuple[int, float, str]:
    """(bar index, price, reason). ``open`` collapses the trade onto its entry
    — the honest rendering when he never said where he got out."""
    dist = None
    if tr.get("exit") is not None:
        dist = sgn * (float(tr["exit"]) - entry)
    elif key in STATED_POINTS:
        dist = float(STATED_POINTS[key])

    if dist is not None and dist > 0:
        if (got := reach(bars, e, entry, sgn, dist)) is not None:
            return got[0], got[1], "stated"
        # Spoken but never printed from this entry — which happens when the
        # entry itself is a bar-open guess. Refuse to place it.

    return e, entry, "open"


def main() -> None:
    doc = json.loads(SRC.read_text())
    setups = doc["setups"]
    pval = point_value(SYMBOL)

    rows: list[dict] = []
    dropped: list[str] = []
    for sess in doc["sessions"]:
        day = sess["date"]
        cand = [t for t in sess.get("trades", [])
                if is_nq(day, t) and (day, t.get("et")) not in NOT_HELD]
        if not cand:
            continue
        bars = _day_bars(SYMBOL, pd.Timestamp(day).date())
        if bars is None:
            dropped.append(f"{day}: no cached ticks ({len(cand)} trades)")
            continue

        for tr in cand:
            key = (day, tr["et"])
            e = bar_at(bars, et_stamp(day, tr["et"]))
            if e is None:
                dropped.append(f"{day} {tr['et']}: outside the cached frame")
                continue

            sgn = 1 if tr["side"] == "long" else -1
            entry = (float(tr["entry"]) if tr.get("entry") is not None
                     else float(bars["open"].iloc[e]))
            j, exit_px, reason = resolve_exit(tr, bars, e, entry, sgn, key)
            if reason == "open" and key in STATED_POINTS:
                dropped.append(f"{day} {tr['et']}: stated +{STATED_POINTS[key]}pt "
                               f"never printed from {entry} — left open")

            entry_ts = bars["ts_utc"].iloc[e]
            exit_ts = bars["ts_utc"].iloc[j]
            points = sgn * (exit_px - entry)
            rows.append({
                "day": day,
                "direction": "Long" if sgn > 0 else "Short",
                "entry_ts_utc": entry_ts.isoformat(),
                "exit_ts_utc": exit_ts.isoformat(),
                "avg_entry": entry,
                "avg_exit": float(exit_px),
                # No stop was ever spoken: pin it to entry so R reads 0.
                "stop_price": entry,
                "target_price": float(exit_px),
                "exit_reason": reason,
                "points": float(points),
                "r_multiple": 0.0,
                "net_pnl": float(points * pval),
                "duration_s": float((exit_ts - entry_ts).total_seconds()),
                "band_width_ticks": 0.0,
                "strategy": setups.get(str(tr.get("setup"))) or SETUP_UNNAMED,
                # Provenance, carried through so the table can be audited
                # against the transcript without reopening trades.json.
                "entry_source": "spoken" if tr.get("entry") is not None else "bar open",
                "stated_result": str(tr.get("result") or "")[:120],
                "attribution": str(tr.get("attribution") or ""),
                "on_stream": str(tr.get("on_stream")),
            })

    out = pd.DataFrame(rows).sort_values("entry_ts_utc").reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)

    spoken = int((out["entry_source"] == "spoken").sum())
    stated = int((out["exit_reason"] == "stated").sum())
    print(f"wrote {len(out)} NQ trades over {out['day'].nunique()} sessions -> {OUT}")
    print(f"  entries: {spoken} spoken / {len(out) - spoken} taken from the bar open")
    print(f"  exits:   {stated} stated / {len(out) - stated} left open (entry marker only)")
    for d in dropped:
        print(f"  note: {d}")


if __name__ == "__main__":
    main()
