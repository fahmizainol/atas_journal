"""Phase 6 — did the live day actually match the backtest?

Three comparisons, and the order is not presentational. It is what makes a
residual in the last one *attributable*:

  1. **Tape fidelity.** Rithmic's recorded prints against Databento's for the
     same date. If the two tapes differ, everything downstream differs for a
     reason that has nothing to do with the strategies.
  2. **Prefix integrity.** What the runner said *during* the session (the signal
     journal) against one settled run over the same live tape. If a prefix run
     did not reproduce the prefix of the settled one, live mode was lying about
     its own tape, independently of any vendor.
  3. **Signal agreement.** The settled run over the live tape against the same
     shelf over the Databento day. Only with 1 and 2 clean does a difference
     here mean what it looks like it means.

Run out of order — or worse, run 3 alone — and a disagreement has three possible
homes and no way to choose between them. So each stage carries the verdict of
the ones before it, and stage 3 reports itself as **not attributable** when they
did not pass. That flag is the point of the module.

WEIGHTED BY P&L, NOT BY TRADE COUNT. One run's top 20 trades were 101% of its
net. A 95% match rate that misses the two trades carrying the edge is a failure
dressed as a success, so agreement is reported as the share of |net P&L| that
matched, with the count alongside as context rather than as the headline.

WHAT THIS NEEDS THAT IS NOT FREE. A Databento day for the same date. The corpus
ends 2026-06-30, so stage 1 and stage 3 need a handful of settled sessions
bought specifically for this. Stage 2 needs neither — it is live against live —
and is the one that can run the morning after a recording with nothing bought.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..sim import live_shadow
from ..sim import regime as regmod
from ..sim import ticks as tickmod
from . import journal as jourmod
from .shadow import resolve_watches

# Prints are bucketed to microseconds before they are compared. Databento stores
# nanosecond exchange stamps; Rithmic gives nanoseconds when `source_nsecs` is
# populated and microseconds otherwise, so the microsecond is the finest bucket
# the two feeds can be *held* to. Anything finer would report the two clocks'
# resolutions as a disagreement about trades.
_TS_BUCKET_NS = 1_000

# What counts as a clean stage. Deliberately strict on volume — a tape missing
# half a percent of the day's contracts is not a tape you can attribute a
# strategy difference against.
VOLUME_TOLERANCE = 0.001


def _key_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a tape to (bucketed instant, price) -> volume and print count.

    Comparing tick lists elementwise would report an ordering difference as a
    content difference: one aggressor sweeping N resting orders emits N prints
    that share an instant, and the two feeds need not emit them in the same
    order within it. Grouping is what makes the comparison about *what traded*
    rather than about the order two vendors happened to serialise it in.
    """
    ts = df["ts_utc"].values.astype("datetime64[ns]").astype("int64") // _TS_BUCKET_NS
    g = pd.DataFrame({"ts": ts, "price": df["price"].to_numpy(),
                      "size": df["size"].to_numpy(dtype="int64")})
    return g.groupby(["ts", "price"], sort=True).agg(
        size=("size", "sum"), prints=("size", "size")).reset_index()


def _tape_stats(df: pd.DataFrame | None) -> dict:
    if df is None or df.empty:
        return {"prints": 0, "volume": 0, "first": None, "last": None,
                "high": None, "low": None, "vwap": None}
    p = df["price"].to_numpy(dtype="float64")
    v = df["size"].to_numpy(dtype="float64")
    return {
        "prints": int(len(df)),
        "volume": int(v.sum()),
        "first": df["ts_utc"].iloc[0].isoformat(),
        "last": df["ts_utc"].iloc[-1].isoformat(),
        "high": float(p.max()),
        "low": float(p.min()),
        # The VWAP is here because it is what the strategies actually consume:
        # two tapes can differ by thousands of prints and agree on every number
        # a band is drawn from, or differ by one large print and not.
        "vwap": float((p * v).sum() / v.sum()) if v.sum() else None,
    }


def _live_segments(symbol: str, day: date) -> dict[str, pd.DataFrame | None]:
    return {seg: tickmod.live_segment(symbol, day, seg)
            for seg in tickmod.SEGMENTS}


def _reference_segments(symbol: str, day: date) -> dict[str, pd.DataFrame | None]:
    """The Databento windows, read *around* the live fallback.

    ``cached_rth`` and its twins fall through to the live store when the vendor
    cache has nothing — which is right everywhere else and wrong here, where
    falling through would compare a tape against itself and report perfect
    fidelity for a day that was never bought.
    """
    out: dict[str, pd.DataFrame | None] = {}
    for seg in tickmod.SEGMENTS:
        if not tickmod.have_segment(symbol, day, seg):
            out[seg] = None
            continue
        df = tickmod._read_segment_cached(symbol, day, seg)
        out[seg] = None if df.empty else df
    return out


# --- 1. tape fidelity -------------------------------------------------------


def tape_fidelity(symbol: str, day: date) -> dict:
    """Rithmic's recorded tape against Databento's, window by window."""
    live = _live_segments(symbol, day)
    ref = _reference_segments(symbol, day)
    if not any(f is not None for f in live.values()):
        return {"stage": "tape_fidelity", "status": "failed",
                "reason": f"nothing recorded for {symbol} {day}"}
    if not any(f is not None for f in ref.values()):
        return {"stage": "tape_fidelity", "status": "unavailable",
                "reason": (f"no Databento day cached for {symbol} {day} — the "
                           "corpus ends 2026-06-30, so a reconciled date has to "
                           "be bought")}

    # The headline is the WHOLE DAY, not the sum of the windows, and the
    # difference between those two is a real thing this found on its first run.
    # The cached `rth` and `post` parquets have a one-print seam — on 2025-10-13
    # the post file's first tick is at 19:59:59.9995 UTC, a hair *before* the
    # 20:00 boundary its own window declares, because `_fetch_range` asks
    # Databento for a range of ts_recv and stores ts_event. A live tape is one
    # ordered stream with no such seam, so slicing it by time puts that print in
    # RTH. Summing per-window agreement would charge that to the feeds. It is a
    # disagreement about which window a trade belongs to, not about the trade.
    day_live = _concat(live)
    day_ref = _concat(ref)
    whole = _compare(day_live, day_ref)

    segments: dict[str, dict] = {}
    window_residual = 0
    for seg in tickmod.SEGMENTS:
        row = {"live": _tape_stats(live[seg]), "databento": _tape_stats(ref[seg])}
        row.update(_compare(live[seg], ref[seg]))
        row["vwap_delta_points"] = (
            None if row["live"]["vwap"] is None or row["databento"]["vwap"] is None
            else round(row["live"]["vwap"] - row["databento"]["vwap"], 4))
        window_residual += row.get("live_only_volume", 0) + row.get("ref_only_volume", 0)
        segments[seg] = row

    share = whole["matched_volume_share"]
    status = "ok" if (1.0 - (share or 0.0)) <= VOLUME_TOLERANCE else "degraded"
    # A window recorded on one side and absent on the other is not a rounding
    # difference; it is a hole, and it makes the whole stage unusable as a
    # baseline for the two below.
    if any(bool(live[s] is None) != bool(ref[s] is None) for s in tickmod.SEGMENTS):
        status = "degraded"
    return {
        "stage": "tape_fidelity",
        "status": status,
        **whole,
        "segments": segments,
        # Prints the two stores agree traded but disagree about the window of.
        # Zero on a clean day; the known seam makes it a couple of contracts.
        "window_boundary_volume": int(window_residual) if status == "ok" else None,
        "aggressor": aggressor_crosstab(symbol, day),
    }


def _concat(segments: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    parts = [segments[s] for s in tickmod.SEGMENTS if segments.get(s) is not None]
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    return df.sort_values("ts_utc", kind="stable").reset_index(drop=True)


def _compare(lf: pd.DataFrame | None, rf: pd.DataFrame | None) -> dict:
    """Matched / live-only / reference-only volume between two tapes."""
    if lf is None or rf is None:
        return {}
    m = _key_frame(lf).merge(_key_frame(rf), on=["ts", "price"], how="outer",
                             suffixes=("_live", "_ref"))
    sl = m["size_live"].fillna(0).to_numpy()
    sr = m["size_ref"].fillna(0).to_numpy()
    common = np.minimum(sl, sr)
    matched, total = common.sum(), max(sl.sum(), sr.sum())
    return {
        "matched_volume": int(matched),
        "live_only_volume": int((sl - common).sum()),
        "ref_only_volume": int((sr - common).sum()),
        "matched_volume_share": round(float(matched / total), 6) if total else None,
    }


def aggressor_crosstab(symbol: str, day: date) -> dict:
    """Rithmic's raw aggressor int against Databento's side, on matched prints.

    This is the measurement the plan asked for and the recorder made possible.
    Rithmic's own protobuf names the enum (``BUY=1``, ``SELL=2``), so what is
    being tested is not what the ints mean but whether Rithmic's *aggressor* and
    Databento's *side* mean the same thing — the ``'B'`` = buy-aggressor finding
    was measured on Databento prints, never on these.

    A clean result is a diagonal: every raw 1 meeting a ``'B'`` and every raw 2
    meeting an ``'A'``. An anti-diagonal means the mapping in ``rithmic.py`` is
    backwards and every recorded day can be re-derived from ``agg_raw`` rather
    than re-recorded. Anything else means the two feeds disagree about aggressor
    on some prints, which is a fact worth knowing before any of it is trusted.

    Matched on (instant, price, size) triples that are unique on both sides —
    a sweep emitting several equal prints in the same microsecond cannot be
    paired one-to-one, and guessing a pairing would invent evidence.
    """
    raw = _live_raw(symbol, day)
    parts = [f for f in _reference_segments(symbol, day).values() if f is not None]
    ref = pd.concat(parts, ignore_index=True) if parts else None
    if raw is None or "agg_raw" not in raw.columns or ref is None:
        return {"status": "unavailable",
                "reason": "needs a recorded day with agg_raw and a cached Databento day"}

    def keyed(df: pd.DataFrame, col: str) -> pd.DataFrame:
        k = pd.DataFrame({
            "ts": df["ts_utc"].values.astype("datetime64[ns]").astype("int64")
                  // _TS_BUCKET_NS,
            "price": df["price"].to_numpy(),
            "size": df["size"].to_numpy(dtype="int64"),
            col: df[col].to_numpy(),
        })
        dup = k.duplicated(subset=["ts", "price", "size"], keep=False)
        return k[~dup]

    m = keyed(raw, "agg_raw").merge(keyed(ref, "side"), on=["ts", "price", "size"],
                                    how="inner")
    if m.empty:
        return {"status": "unavailable", "reason": "no uniquely pairable prints"}
    tab = m.groupby(["agg_raw", "side"]).size()
    counts = {f"{int(a)}->{s}": int(n) for (a, s), n in tab.items()}
    diag = sum(n for k, n in counts.items()
               if k in ("1->B", "2->A"))
    anti = sum(n for k, n in counts.items() if k in ("1->A", "2->B"))
    verdict = "confirmed" if diag > anti * 10 else (
        "inverted" if anti > diag * 10 else "inconclusive")
    return {"status": "ok", "pairs": int(len(m)), "counts": counts,
            "verdict": verdict,
            "note": ("`confirmed` means rithmic.py's BUY->'B' mapping agrees with "
                     "Databento; `inverted` means flip it and re-derive recorded "
                     "days from agg_raw.")}


def _live_raw(symbol: str, day: date) -> pd.DataFrame | None:
    """The recorded chunks with every column they were written with.

    ``ticks.live_day_ticks`` narrows to the engine's four columns on the way out,
    which is right for everything that simulates and wrong here: ``agg_raw`` is
    the whole point of this read.
    """
    chunks = tickmod.live_chunks(symbol, day)
    if not chunks:
        return None
    d = tickmod.live_day_dir(symbol, day)
    parts = [pd.read_parquet(d / c) for c in chunks]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else None


# --- 2. prefix integrity ----------------------------------------------------


def _trade_key(t: dict) -> tuple:
    """A trade's full identity — entry, exit, direction, both fills, the reason.

    Used where both sides read the **same tape**, which is stage 2: there, any
    difference at all is a prefix violation and there is nothing to be tolerant
    about. Deliberately excludes the excursion metrics and the timings: those are
    derived, and two runs differing only there are the same trade described
    twice.
    """
    return (_entry_key(t), str(t.get("exit_ts_utc")),
            round(float(t.get("avg_exit", 0.0)), 6), str(t.get("exit_reason")))


def _entry_key(t: dict) -> tuple:
    """Which trade this is: direction, entry instant, entry fill.

    Used where the two sides read **different tapes**, which is stage 3. There,
    keying identity on the exit as well conflates two questions that the whole
    three-stage ordering exists to keep apart. The case is not hypothetical: a
    position held into the bell is force-flattened on the tape's last tick, and
    the two stores' last ticks differ by the documented rth/post seam — one print
    at 19:59:59.9995. Both runs take the same trade, at the same entry, and exit
    at the same price for the same reason, one millisecond apart. Keyed on the
    exit those are two unmatched trades and the day reads as 98% agreement;
    keyed on the entry they are one matched trade whose exit stamp differs, which
    is what stage 1 already said and is true.

    So the exit is compared rather than matched on — see ``_exit_delta``.
    """
    return (str(t.get("direction")), str(t.get("entry_ts_utc")),
            round(float(t.get("avg_entry", 0.0)), 6))


def _exit_delta(a: dict, b: dict) -> dict | None:
    """How two runs' versions of the same trade ended differently, or None.

    P&L first: two exits a millisecond apart at the same price are a stamp
    difference, and two at the same instant for different money are not. Both are
    reported, and only the second is worth anyone's attention.
    """
    fields = {}
    for k in ("avg_exit", "net_pnl"):
        av, bv = round(float(a.get(k, 0.0)), 6), round(float(b.get(k, 0.0)), 6)
        if av != bv:
            fields[k] = [av, bv]
    for k in ("exit_reason", "exit_ts_utc"):
        av, bv = str(a.get(k)), str(b.get(k))
        if av != bv:
            fields[k] = [av, bv]
    return fields or None


def _settled_run(symbol: str, day: date, watches, frames: dict) -> dict:
    """Run the shelf to completion over one day's frames. slug -> trades.

    ``partial=False``: this is a finished session, and telling the engine
    otherwise would leave a position open at the last tick instead of flattening
    it at the bell — a different trade from the one the day actually produced.
    """
    art = frames.get("regime")
    out = {}
    for w in watches:
        frame = frames["globex"] if w.session == "globex" else frames["rth"]
        if frame is None or frame.empty:
            out[w.slug] = {"error": "no frame for this session window", "trades": []}
            continue
        try:
            trades, _vetoed, _b, _bd = live_shadow.shadow_session(
                w.slug, w.cfg, day, frame, regime=art, partial=False)
            out[w.slug] = {"error": None, "trades": trades}
        except Exception as e:  # noqa: BLE001 — one strategy's failure is its own
            out[w.slug] = {"error": f"{type(e).__name__}: {e}", "trades": []}
    return out


def _frames_from(segments: dict[str, pd.DataFrame | None], symbol: str,
                 day: date) -> dict:
    """The two windows an engine reads, plus the settled regime artifact.

    Exactly the windows ``get_day_ticks`` returns — 09:30→16:00 for an RTH
    strategy, prev 18:00→16:00 for a Globex one, and the post hour in neither.
    ``ticks.py``'s READ CONTRACT is not relaxed for a reconciliation: a frame
    carrying the overnight in front of an RTH strategy re-phases every bar, and
    the comparison would be measuring that instead of the feeds.
    """
    on, rth = segments.get("on"), segments.get("rth")
    gx = (pd.concat([on, rth], ignore_index=True)
          if on is not None and rth is not None else rth)
    art = regmod.compute_regime(symbol, day, frames=(on, rth))
    return {"rth": rth, "globex": gx, "regime": art}


def prefix_integrity(symbol: str, day: date, attributable: bool = True) -> dict:
    """Every journalled prefix against one settled run over the same live tape.

    The property under test is the one the whole feature rests on: a run over the
    day so far reproduces the *prefix* of the run over the whole day.
    ``tests/test_prefix_replay.py`` asserts it on cached sessions; this asserts it
    on the session that actually happened, against the answers that were actually
    given at the time.

    A journalled trade that is not in the settled run at the same position is the
    loudest possible failure — it means the live surface showed a trade the day
    did not contain.

    ONE ASYMMETRY WORTH NAMING. The prefix runs read a regime artifact frozen
    checkpoint by checkpoint, with the ones whose cutoff had not passed
    deliberately absent; the settled run reads the complete artifact. Those are
    not the same input, and the reason it does not matter is a property of the
    gates rather than of this function: every regime gate applies its veto only
    from its own checkpoint minute onwards, so a checkpoint the prefix run was
    missing was one no gate would have consulted yet. That is an argument, and
    arguments are what tests are for — the end-to-end case runs the real shelf
    with the real gates and expects zero divergences.
    """
    slugs = jourmod.slugs(symbol, day)
    if not slugs:
        return {"stage": "prefix_integrity", "status": "unavailable",
                "attributable": attributable,
                "reason": f"no signal journal for {symbol} {day} — was the "
                          "session recorded, or only watched?"}
    watches, skipped = resolve_watches(symbol)
    frames = _frames_from(_live_segments(symbol, day), symbol, day)
    settled = _settled_run(symbol, day, watches, frames)

    per: list[dict] = []
    breaks = 0
    for slug in slugs:
        final = [_trade_key(t) for t in settled.get(slug, {}).get("trades", [])]
        entries = jourmod.read(symbol, day, slug)
        first_bad = None
        checked = 0
        for e in entries:
            keys = [_trade_key(t) for t in e.get("trades", [])]
            checked += 1
            if keys != final[:len(keys)]:
                first_bad = {"rows": e.get("rows"), "at": e.get("at"),
                             "journalled": len(keys), "settled": len(final)}
                break
        if first_bad:
            breaks += 1
        per.append({"slug": slug, "journal_entries": len(entries),
                    "checked": checked, "settled_trades": len(final),
                    "ok": first_bad is None, "first_divergence": first_bad,
                    "settled_error": settled.get(slug, {}).get("error")})

    return {
        "stage": "prefix_integrity",
        "status": "ok" if breaks == 0 else "failed",
        "attributable": attributable,
        "strategies": len(per),
        "diverged": breaks,
        "per_strategy": per,
        "skipped": skipped,
    }


# --- 3. signal agreement ----------------------------------------------------


def _weighted(live_trades: list[dict], ref_trades: list[dict]) -> dict:
    """Match two trade lists and weight the agreement by P&L contribution.

    Count is reported, but it is not the headline. Given that one run's top 20
    trades were 101% of its net, a 95% match rate that misses the two trades
    carrying the edge is a failure dressed as a success — so the number that
    leads is the share of |net P&L| sitting on trades that matched.
    """
    lk = {_entry_key(t): t for t in live_trades}
    rk = {_entry_key(t): t for t in ref_trades}
    both = set(lk) & set(rk)

    def abs_net(ts):
        return sum(abs(float(t.get("net_pnl", 0.0))) for t in ts)

    live_abs, ref_abs = abs_net(live_trades), abs_net(ref_trades)
    matched_abs = abs_net([lk[k] for k in both])
    exits = [{"entry": k[1], **d} for k in sorted(both)
             if (d := _exit_delta(lk[k], rk[k]))]
    # Money on trades both runs took but ended differently. Separate from the
    # matched share on purpose: an unmatched trade is a strategy disagreement, a
    # divergent exit on a matched one is usually the tape's last tick.
    exit_pnl = sum(abs(float(lk[k].get("net_pnl", 0.0))
                       - float(rk[k].get("net_pnl", 0.0))) for k in both)
    return {
        "live_trades": len(live_trades),
        "databento_trades": len(ref_trades),
        "matched_trades": len(both),
        "live_only": sorted(str(k[1]) for k in set(lk) - both),
        "databento_only": sorted(str(k[1]) for k in set(rk) - both),
        "divergent_exits": exits,
        "exit_pnl_delta": round(exit_pnl, 2),
        "live_net": round(sum(float(t.get("net_pnl", 0.0)) for t in live_trades), 2),
        "databento_net": round(sum(float(t.get("net_pnl", 0.0)) for t in ref_trades), 2),
        # Two shares, because they answer different questions: how much of what
        # live claimed was real, and how much of what was real live caught.
        "pnl_share_live": round(matched_abs / live_abs, 6) if live_abs else None,
        "pnl_share_databento": round(matched_abs / ref_abs, 6) if ref_abs else None,
        "count_share": round(len(both) / max(len(lk), len(rk)), 6) if (lk or rk) else None,
    }


def signal_agreement(symbol: str, day: date, attributable: bool = True) -> dict:
    """The shelf over the live tape against the shelf over the Databento day.

    Both runs are settled runs of the same configs on the same date; the only
    thing that differs is which feed's ticks they read. That is the whole point,
    and it is also why this stage is meaningless without the two above: an
    unattributed difference here could be the feeds, the prefix property, or the
    strategies, and there is no way to tell from this number alone.
    """
    # Built once and carried by every return, including the early ones. The flag
    # is the point of the module, and a result that quietly dropped it on the
    # paths where nothing could be computed would be exactly the shape of answer
    # this stage exists to refuse to give.
    note = (None if attributable else
            "NOT ATTRIBUTABLE: an earlier stage did not pass, so a difference "
            "here cannot be assigned to the strategies rather than to the tape.")
    base = {"stage": "signal_agreement", "attributable": attributable, "note": note}

    live_segs = _live_segments(symbol, day)
    ref_segs = _reference_segments(symbol, day)
    if not any(f is not None for f in live_segs.values()):
        return {**base, "status": "failed",
                "reason": f"nothing recorded for {symbol} {day}"}
    if ref_segs.get("rth") is None:
        return {**base, "status": "unavailable",
                "reason": f"no Databento RTH cached for {symbol} {day}"}

    watches, skipped = resolve_watches(symbol)
    live_runs = _settled_run(symbol, day, watches, _frames_from(live_segs, symbol, day))
    ref_runs = _settled_run(symbol, day, watches, _frames_from(ref_segs, symbol, day))

    per, tot_match, tot_live_abs, tot_ref_abs, tot_exit = [], 0.0, 0.0, 0.0, 0.0
    for w in watches:
        lt = live_runs.get(w.slug, {}).get("trades", [])
        rt = ref_runs.get(w.slug, {}).get("trades", [])
        if not lt and not rt:
            continue
        row = _weighted(lt, rt)
        row["slug"] = w.slug
        row["errors"] = {"live": live_runs.get(w.slug, {}).get("error"),
                         "databento": ref_runs.get(w.slug, {}).get("error")}
        per.append(row)
        lk = {_entry_key(t): t for t in lt}
        rk = {_entry_key(t): t for t in rt}
        tot_match += sum(abs(float(lk[k].get("net_pnl", 0.0))) for k in set(lk) & set(rk))
        tot_live_abs += sum(abs(float(t.get("net_pnl", 0.0))) for t in lt)
        tot_ref_abs += sum(abs(float(t.get("net_pnl", 0.0))) for t in rt)
        tot_exit += row["exit_pnl_delta"]

    share = (tot_match / tot_live_abs) if tot_live_abs else None
    return {
        **base,
        "status": "ok" if per else "unavailable",
        "pnl_share_live": None if share is None else round(share, 6),
        "pnl_share_databento": (None if not tot_ref_abs
                                else round(tot_match / tot_ref_abs, 6)),
        # Trades both runs took and ended differently — reported alongside the
        # match rate rather than inside it, because they are a different failure
        # with a different usual cause.
        "divergent_exits": sum(len(r["divergent_exits"]) for r in per),
        "exit_pnl_delta": round(tot_exit, 2),
        "per_strategy": per,
        "skipped": skipped,
    }


# --- the whole thing --------------------------------------------------------


def reconcile(symbol: str, day: date) -> dict:
    """All three comparisons, in the order that makes the third one mean something."""
    fidelity = tape_fidelity(symbol, day)
    ok1 = fidelity.get("status") == "ok"
    prefix = prefix_integrity(symbol, day, attributable=ok1)
    ok2 = prefix.get("status") == "ok"
    agreement = signal_agreement(symbol, day, attributable=ok1 and ok2)
    return {"symbol": symbol, "date": day.isoformat(),
            "tape_fidelity": fidelity, "prefix_integrity": prefix,
            "signal_agreement": agreement}


def format_report(res: dict) -> str:
    """The three stages as something readable in a terminal."""
    L: list[str] = []
    L.append(f"Reconciliation — {res['symbol']} {res['date']}")
    L.append("=" * 58)

    f = res["tape_fidelity"]
    L.append(f"\n1. tape fidelity ....... {f['status'].upper()}")
    if f.get("reason"):
        L.append(f"   {f['reason']}")
    if f.get("matched_volume_share") is not None:
        L.append(f"   whole day       {f['matched_volume_share']:.4%} matched "
                 f"({f['matched_volume']:,} contracts)")
        L.append(f"   live-only {f['live_only_volume']:,}   "
                 f"databento-only {f['ref_only_volume']:,}")
        if f.get("window_boundary_volume"):
            L.append(f"   {f['window_boundary_volume']:,} contracts agreed on but "
                     "filed under different windows (the known rth/post seam)")
        for seg, row in f.get("segments", {}).items():
            lv, rv = row["live"], row["databento"]
            d = row.get("vwap_delta_points")
            L.append(f"     {seg:<4} live {lv['prints']:>9,} prints  "
                     f"databento {rv['prints']:>9,}  "
                     + ("vwap Δ n/a" if d is None else f"vwap Δ {d:+.4f} pt"))
        agg = f.get("aggressor", {})
        if agg.get("status") == "ok":
            L.append(f"   aggressor mapping: {agg['verdict'].upper()} "
                     f"on {agg['pairs']:,} pairs — {agg['counts']}")
        elif agg.get("reason"):
            L.append(f"   aggressor mapping: {agg['reason']}")

    p = res["prefix_integrity"]
    L.append(f"\n2. prefix integrity .... {p['status'].upper()}")
    if p.get("reason"):
        L.append(f"   {p['reason']}")
    for row in p.get("per_strategy", []):
        mark = "ok " if row["ok"] else "!! "
        L.append(f"   {mark}{row['slug']:<34} {row['checked']:>4} journalled "
                 f"→ {row['settled_trades']} settled")
        if row["first_divergence"]:
            L.append(f"       diverged at row {row['first_divergence']['rows']} "
                     f"({row['first_divergence']['at']})")

    a = res["signal_agreement"]
    L.append(f"\n3. signal agreement .... {a['status'].upper()}")
    if a.get("note"):
        L.append(f"   {a['note']}")
    if a.get("reason"):
        L.append(f"   {a['reason']}")
    if a.get("pnl_share_live") is not None:
        L.append(f"   P&L-weighted agreement  {a['pnl_share_live']:.4%} of live, "
                 f"{a['pnl_share_databento']:.4%} of databento")
        L.append(f"   {a['divergent_exits']} matched trade(s) ended differently, "
                 f"worth ${a['exit_pnl_delta']:,.2f}")
    for row in a.get("per_strategy", []):
        L.append(f"     {row['slug']:<34} {row['matched_trades']}/"
                 f"{row['live_trades']} live · {row['databento_trades']} databento"
                 f"   net {row['live_net']:+,.0f} vs {row['databento_net']:+,.0f}")
    return "\n".join(L)
