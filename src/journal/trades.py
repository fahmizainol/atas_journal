"""Build logical trades (flat->flat) from the ATAS Journal, and expose ATAS rows.

A logical trade spans from a flat position through any number of scale-in /
scale-out lots back to flat. The ATAS Journal is the source of truth: each row
is one matched lot (an open leg and a close leg) carrying ATAS's own realized
PnL, so a logical trade's PnL is the sum of its lots' PnL and always reconciles
to the ATAS total. We group lots by walking a running net position built from
every lot's open/close events in time order; the position returning to flat
marks a trade boundary.

We deliberately do *not* reconstruct PnL from the Executions sheet: ATAS Replay
exports frequently ship a truncated Executions sheet (e.g. 7 fills backing 19
journal lots), which a flat-to-flat fill reconstruction silently mis-books.
Executions are used only to attach fill markers to a trade where they fall
inside its window; missing fills just mean fewer markers, never wrong PnL.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

# A logical trade must not span a session break. Intraday lots are seconds to a
# couple of minutes apart; the gap between trading sessions is hours. Any open
# position at a gap this large is force-closed so position drift can't merge
# unrelated sessions into one trade.
SESSION_GAP = pd.Timedelta(hours=1)

#: ``SESSION_GAP`` in the integer unit the event scan works in. Timestamps are
#: compared as microseconds since epoch rather than as Timestamps: the scan is
#: the one part of this module that touches every lot twice, and boxing 5k
#: Timestamps to compare them was most of what made it slow.
_GAP_US = SESSION_GAP // pd.Timedelta(microseconds=1)


def _trade_key(seed: str, instrument: str) -> str:
    return hashlib.sha1(f"{instrument}|{seed}".encode()).hexdigest()[:16]


def build_logical_trades(
    journal: pd.DataFrame, executions: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Group ATAS Journal lots per (account, instrument, source_file) into
    flat->flat trades.

    Each journal row contributes a signed open event at ``open_ts_utc`` and a
    signed close event at ``close_ts_utc``. Walking those events in time order,
    a trade boundary falls wherever the running net position returns to flat.
    PnL is the sum of the grouped lots' ATAS PnL, so it reconciles exactly.
    See :func:`_trade_ids` for the grouping and the session-gap rule.

    Whole-column throughout. This runs on every cold scope build — which any
    write invalidates — so the per-trade shape it replaced put a multi-second
    rebuild behind the next page load of every trade-derived surface.
    """
    if journal is None or journal.empty:
        return pd.DataFrame()

    work = journal.reset_index(drop=True)
    # Databases written before the commission column, and frames built by hand
    # in tests, simply do not have it. Absent is the same statement NULL makes
    # in it — no commission known — so it is filled that way rather than zeroed.
    if "fees" not in work:
        work = work.assign(fees=np.nan)
    # One id per flat->flat trade, ascending in (account, instrument,
    # source_file, span) order — the order the old per-group loop emitted rows
    # in. That ordering is load-bearing: the sort at the bottom is by entry
    # stamp alone and trades do share one, so the input order is what breaks
    # those ties and fixes ``trade_no``.
    uid = _trade_ids(work)

    # Lots in trade order, then by open time within the trade. This is the order
    # ``lot_keys`` and ``comment`` are written in, and its first row names the
    # trade — so it decides ``trade_key``, which notes, reviews, recall cards and
    # rule checks are all filed under.
    #
    # ``lexsort`` is **stable**, so lots sharing an open stamp keep their journal
    # order. The per-span ``sort_values`` this replaced was not: below 16 lots
    # numpy falls back to an insertion sort and is stable by accident, and above
    # it the tie order is whatever quicksort did. Since a trade's key is its first
    # lot's, that made ``trade_key`` — the id every note is filed under — depend
    # on an unspecified permutation. Nothing downstream reads ``lot_keys`` in
    # order (``lot_to_logical_map`` builds a dict from it), so making the order
    # defined costs nothing and removes that exposure.
    order = np.lexsort((_micros(work["open_ts_utc"]), uid))
    lots = work.take(order)
    uid = uid[order]

    abs_open = lots["open_volume"].abs()
    abs_close = lots["close_volume"].abs()
    lots = lots.assign(
        _uid=uid,
        _abs_open=abs_open,
        _abs_close=abs_close,
        _w_open=lots["open_price"] * abs_open,
        _w_close=lots["close_price"] * abs_close,
    )

    # One pass for everything that is a plain reduction over a trade's lots.
    g = lots.groupby("_uid", sort=False)
    df = g.agg(
        _seed=("dedupe_key", "first"),
        source_file=("source_file", "first"),
        instrument=("instrument", "first"),
        account=("account", "first"),
        _first_open_vol=("open_volume", "first"),
        _w_open=("_w_open", "sum"),
        _abs_open=("_abs_open", "sum"),
        _w_close=("_w_close", "sum"),
        _abs_close=("_abs_close", "sum"),
        leg_count=("dedupe_key", "size"),
        entry_ts_utc=("open_ts_utc", "min"),
        exit_ts_utc=("close_ts_utc", "max"),
        entry_ts_local=("open_ts_local", "min"),
        exit_ts_local=("close_ts_local", "max"),
        gross_pnl=("pnl", "sum"),
        _fees=("fees", "sum"),
    ).reset_index(drop=True)

    # A trade whose lots carry no size has no average price to quote — nan, not
    # a division blow-up. Every other column above is a sum, which needs no such
    # guard. (Grouped sums use a compensated kernel, so an average here can land
    # a ULP off what summing that trade's lots on their own gave — closer to the
    # exact value, and ~1e-12 on a price the fill matcher compares at 1e-4.)
    df["avg_entry"] = _wavg(df.pop("_w_open"), df["_abs_open"])
    df["avg_exit"] = _wavg(df.pop("_w_close"), df.pop("_abs_close"))
    df["max_contracts"] = df.pop("_abs_open")
    df["direction"] = np.where(df.pop("_first_open_vol") > 0, "Long", "Short")
    # Column-wise. The per-trade version of this was ``Timedelta.total_seconds()``
    # on one pair of stamps, whose scalar path is a ULP *less* accurate than the
    # division here — so ~1% of durations move by ~1e-14s, which is 12 orders of
    # magnitude below the nearest threshold anything compares them against.
    df["duration_s"] = (df["exit_ts_utc"] - df["entry_ts_utc"]).dt.total_seconds()
    df["leg_count"] = df["leg_count"].astype("int64")

    instruments = df["instrument"].to_numpy()
    df["trade_key"] = [_trade_key(s, i)
                       for s, i in zip(df.pop("_seed").to_numpy(), instruments)]

    # The two per-trade lists. Split once on the trade boundaries rather than
    # asking pandas for a group at a time — the frame is ~1 lot per trade, so a
    # per-group call is nearly all overhead.
    bounds = np.flatnonzero(np.r_[True, uid[1:] != uid[:-1]])[1:]
    # Every ATAS lot this logical trade absorbed. Lets ``lot_to_logical_map``
    # resolve an ATAS row back to the logical trade that owns it, so notes /
    # model / rule checks bind to the same key in either view.
    df["lot_keys"] = [list(a) for a in np.split(lots["dedupe_key"].to_numpy(), bounds)]
    df["comment"] = [
        "; ".join(dict.fromkeys([c for c in a if c]))
        for a in np.split(lots["comment"].fillna("").to_numpy(), bounds)
    ]

    # Commission is the sum of the lots' own, which is why it had to be carried
    # per lot rather than re-derived per trade: a scale-out is charged by the
    # portion, and a trade that closed in four lots pays four times. A trade
    # whose lots all report NULL sums to 0.0 — no commission known, which is
    # every imported ATAS row and is what those trades showed before the column
    # existed.
    df["commission"] = df.pop("_fees").fillna(0.0)
    df["net_pnl"] = df["gross_pnl"] - df["commission"]
    df["open_position"] = False
    df["fills"] = _window_fills(
        executions, df["account"].to_numpy(), instruments,
        _micros(df["entry_ts_utc"]), _micros(df["exit_ts_utc"]),
    )

    df = df[[
        "trade_key", "lot_keys", "source_file", "instrument", "account",
        "direction", "avg_entry", "avg_exit", "max_contracts", "leg_count",
        "entry_ts_utc", "exit_ts_utc", "entry_ts_local", "exit_ts_local",
        "duration_s", "gross_pnl", "commission", "net_pnl", "open_position",
        "fills", "comment",
    ]]
    df = df.sort_values("entry_ts_utc").reset_index(drop=True)
    df.insert(0, "trade_no", range(1, len(df) + 1))
    return df


def _micros(ts: pd.Series) -> np.ndarray:
    """A UTC timestamp column as microseconds since epoch."""
    return ts.to_numpy("datetime64[us]").astype("int64")


def _wavg(weighted: pd.Series, weight: pd.Series) -> np.ndarray:
    """Weighted mean per trade, nan where the weights sum to zero."""
    w = weight.to_numpy(dtype=float)
    return np.divide(weighted.to_numpy(dtype=float), w,
                     out=np.full(len(w), np.nan), where=w != 0)


def _trade_ids(work: pd.DataFrame) -> np.ndarray:
    """A flat->flat trade id per lot, from a running net position.

    Each lot contributes a signed open event and a signed close event; walking
    those in time order, a trade boundary falls wherever the position returns to
    flat. Events sharing a timestamp are one batch (opens before closes) and the
    flat check happens only after the batch, so a position momentarily netted to
    zero mid-instant does not split a trade spuriously.

    Grouped by ``source_file`` as well as account and instrument: each ATAS
    export is one replay attempt, and two attempts at the same
    account/instrument/replayed-date must never share a flat->flat span —
    otherwise their lots interleave by timestamp and merge into bogus combined
    trades. Isolating attempts here is what lets the day view show take-1 and
    take-2 separately instead of blended.

    **Why this can be a cumulative sum at all.** The scan looks sequential — a
    session gap force-closes an open position, which resets the running total —
    but at a gap the position is zero either way: either the previous batch left
    it flat, or the gap rule just zeroed it. So the reset points are fixed by the
    timestamps alone, and the position is a cumsum restarted at each of them
    rather than a loop that has to be walked.
    """
    n = len(work)
    # ``ngroup`` numbers groups in sorted key order, which is the order the
    # emitted trades must come out in (see the sort in the caller).
    grp = work.groupby(["account", "instrument", "source_file"],
                       sort=True).ngroup().to_numpy()
    zeros, ones = np.zeros(n, np.int8), np.ones(n, np.int8)
    ev_grp = np.concatenate([grp, grp])
    ev_ts = np.concatenate([_micros(work["open_ts_utc"]), _micros(work["close_ts_utc"])])
    ev_kind = np.concatenate([zeros, ones])
    ev_dv = np.concatenate([work["open_volume"].to_numpy(dtype=float),
                            work["close_volume"].to_numpy(dtype=float)])
    ev_row = np.concatenate([np.arange(n), np.arange(n)])
    ev_is_open = np.concatenate([np.ones(n, bool), np.zeros(n, bool)])

    # Group, then instant, then opens before closes. Stable, so events left tied
    # by all three stay in lot order.
    order = np.lexsort((ev_kind, ev_ts, ev_grp))
    e_grp, e_ts, e_dv = ev_grp[order], ev_ts[order], ev_dv[order]
    e_row, e_is_open = ev_row[order], ev_is_open[order]

    # --- collapse to batches: one instant within one group ---
    starts = np.empty(len(order), bool)
    starts[0] = True
    starts[1:] = (e_grp[1:] != e_grp[:-1]) | (e_ts[1:] != e_ts[:-1])
    batch_of_event = np.cumsum(starts) - 1
    at = np.flatnonzero(starts)
    b_grp, b_ts = e_grp[at], e_ts[at]
    b_dv = np.add.reduceat(e_dv, at)
    nb = len(at)

    # --- the running position, restarted wherever it is known to be flat ---
    first = np.zeros(nb, bool)
    first[0] = True
    new_grp = first.copy()
    new_grp[1:] = b_grp[1:] != b_grp[:-1]
    seg_break = new_grp.copy()
    seg_break[1:] |= (b_ts[1:] - b_ts[:-1]) > _GAP_US
    running = np.cumsum(b_dv)
    seg_at = np.flatnonzero(seg_break)
    # The total carried in before each segment, subtracted back off so every
    # segment counts up from zero.
    carry = np.concatenate([[0.0], running[seg_at[1:] - 1]])
    pos = running - carry[np.cumsum(seg_break) - 1]
    flat = np.abs(pos) < 1e-9

    # A trade ends when the position goes flat, and also at a session gap that
    # found it open — that gap is a force-close, which is a boundary too.
    prev_flat = np.zeros(nb, bool)
    prev_flat[1:] = flat[:-1]
    gap_closed = seg_break & ~new_grp & ~prev_flat
    uid_of_batch = np.cumsum(new_grp | gap_closed | prev_flat) - 1

    # Every lot has exactly one open event, so every lot gets exactly one id.
    out = np.empty(n, np.int64)
    out[e_row[e_is_open]] = uid_of_batch[batch_of_event[e_is_open]]
    return out


def _window_fills(
    executions: pd.DataFrame | None,
    accounts: np.ndarray, instruments: np.ndarray,
    starts: np.ndarray, ends: np.ndarray,
) -> list[list[dict] | None]:
    """Per trade, its account/instrument executions inside [start, end].

    ``starts`` and ``ends`` are microseconds since epoch, matching :func:`_micros`.
    ``None`` for a trade with no executions in the window (e.g. a truncated
    Replay export), so the chart simply omits fill markers.

    Each account/instrument's executions are sorted and turned into marker dicts
    **once**, and each trade then takes a slice of that. The obvious shape — mask
    the whole executions frame per trade — is four boolean Series, a sort and a
    row-wise walk for every trade in the journal, which cost more than the rest
    of this module put together while touching only a few hundred rows.

    Trades whose windows overlap therefore share marker dicts rather than each
    getting a copy. The trade frame is already documented read-only (see
    ``api.scope``); this makes a fill dict read-only on the same terms.
    """
    n = len(starts)
    if executions is None or executions.empty:
        return [None] * n

    by_key: dict[tuple, tuple[np.ndarray, list[dict]]] = {}
    for key, sub in executions.groupby(["account", "instrument"], sort=False):
        # Stable: fills sharing a stamp are common (one order filling in pieces),
        # and their export order is the only meaningful order they have.
        sub = sub.sort_values("ts_utc", kind="stable")
        by_key[key] = (
            _micros(sub["ts_utc"]),
            [
                {
                    "exchange_id": f["exchange_id"],
                    "ts_local": f["ts_local"],
                    "ts_utc": f["ts_utc"],
                    "direction": f["direction"],
                    "price": f["price"],
                    "volume": f["volume"],
                }
                for _, f in sub.iterrows()
            ],
        )

    out: list[list[dict] | None] = []
    for account, instrument, lo, hi in zip(accounts, instruments, starts, ends):
        found = by_key.get((account, instrument))
        if found is None:
            out.append(None)
            continue
        stamps, markers = found
        a = int(np.searchsorted(stamps, lo, "left"))
        b = int(np.searchsorted(stamps, hi, "right"))
        out.append(markers[a:b] or None)
    return out


def lot_to_logical_map(
    journal: pd.DataFrame, executions: pd.DataFrame | None = None
) -> dict[str, str]:
    """{ATAS lot ``dedupe_key``: owning logical ``trade_key``}.

    The journaling key must not depend on which view you happen to be looking at.
    A logical trade's key hashes only its *first* lot, so an ATAS row can't derive
    it — this map is the bridge. ``executions`` is accepted for signature
    symmetry with :func:`build_logical_trades` and defaults to None: fill markers
    cost real time to window and play no part in the grouping.
    """
    logical = build_logical_trades(journal, executions)
    if logical.empty:
        return {}
    return {
        lot: row["trade_key"]
        for _, row in logical.iterrows()
        for lot in row["lot_keys"]
    }


def localize(df: pd.DataFrame, tz) -> pd.DataFrame:
    """Rebuild the entry/exit *local* columns from the UTC instant in `tz`.

    The UTC timestamp is the canonical instant; switching display zones is just
    a reprojection, so day boundaries and hour-of-day follow the chosen zone.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    for utc_col, local_col in (("entry_ts_utc", "entry_ts_local"),
                               ("exit_ts_utc", "exit_ts_local")):
        s = out[utc_col]
        if s.dt.tz is None:
            s = s.dt.tz_localize("UTC")
        out[local_col] = s.dt.tz_convert(tz)
    return out


def atas_trades(journal: pd.DataFrame) -> pd.DataFrame:
    """Expose ATAS Journal rows in the same shape as logical trades."""
    if journal.empty:
        return pd.DataFrame()
    df = journal.copy()
    df["direction"] = df["open_volume"].apply(lambda v: "Short" if v < 0 else "Long")
    df["max_contracts"] = df["open_volume"].abs()
    df["avg_entry"] = df["open_price"]
    df["avg_exit"] = df["close_price"]
    df["entry_ts_utc"] = df["open_ts_utc"]
    df["exit_ts_utc"] = df["close_ts_utc"]
    df["entry_ts_local"] = df["open_ts_local"]
    df["exit_ts_local"] = df["close_ts_local"]
    df["duration_s"] = (df["exit_ts_utc"] - df["entry_ts_utc"]).dt.total_seconds()
    df["gross_pnl"] = df["pnl"]
    # Per lot here, where the column lives, rather than per trade — the lot view
    # is one row per journal row, so this is the same number the logical view
    # sums. NULL means no commission was ever reported, hence 0.0 and a net that
    # equals gross, which is what an imported ATAS row has always shown.
    df["commission"] = (df["fees"].fillna(0.0) if "fees" in df else 0.0)
    df["net_pnl"] = df["pnl"] - df["commission"]
    df["leg_count"] = 2
    df["open_position"] = False
    df["trade_key"] = df["dedupe_key"].str[:16]
    df["fills"] = None
    df = df.sort_values("entry_ts_utc").reset_index(drop=True)
    df.insert(0, "trade_no", range(1, len(df) + 1))
    # dedupe_key survives into the frame (it doesn't in the logical view) purely
    # so ``lot_to_logical_map`` can rejoin an ATAS row to its logical trade.
    keep = [
        "trade_no", "trade_key", "dedupe_key", "source_file", "instrument", "account",
        "direction",
        "avg_entry", "avg_exit", "max_contracts", "leg_count", "entry_ts_utc",
        "exit_ts_utc", "entry_ts_local", "exit_ts_local", "duration_s", "gross_pnl",
        "commission", "net_pnl", "open_position", "fills", "comment",
    ]
    return df[[c for c in keep if c in df.columns]]


def reconcile(logical: pd.DataFrame, journal: pd.DataFrame) -> dict:
    """Sanity-check computed logical PnL against ATAS Journal total PnL.

    **Both sides are net, and they have to be the same side.** ``net_pnl`` now
    subtracts the commission carried per lot, while the journal's own ``pnl``
    column is gross and always was — so comparing one against the other would
    report every live day's commission as a reconciliation failure, which is
    a real number being flagged as a bug. Subtracting the same lots' fees from
    the journal total keeps the two comparable and the difference at zero.

    Imported rows are unaffected: their ``fees`` is NULL, which sums to 0.0, so
    this is the identical arithmetic it has always done on them.
    """
    logical_pnl = float(logical["net_pnl"].sum()) if not logical.empty else 0.0
    if journal.empty:
        atas_pnl = 0.0
    else:
        fees = (journal["fees"].fillna(0.0).sum() if "fees" in journal else 0.0)
        atas_pnl = float(journal["pnl"].sum() - fees)
    return {
        "logical_net_pnl": logical_pnl,
        "atas_journal_pnl": atas_pnl,
        "difference": logical_pnl - atas_pnl,
        "logical_trades": 0 if logical.empty else len(logical),
        "atas_rows": 0 if journal.empty else len(journal),
    }
