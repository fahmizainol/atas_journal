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
from itertools import groupby

import pandas as pd

# A logical trade must not span a session break. Intraday lots are seconds to a
# couple of minutes apart; the gap between trading sessions is hours. Any open
# position at a gap this large is force-closed so position drift can't merge
# unrelated sessions into one trade.
SESSION_GAP = pd.Timedelta(hours=1)


def _trade_key(seed: str, instrument: str) -> str:
    return hashlib.sha1(f"{instrument}|{seed}".encode()).hexdigest()[:16]


def build_logical_trades(
    journal: pd.DataFrame, executions: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Group ATAS Journal lots per (account, instrument) into flat->flat trades.

    Each journal row contributes a signed open event at ``open_ts_utc`` and a
    signed close event at ``close_ts_utc``. Walking those events in time order,
    a trade boundary falls wherever the running net position returns to flat.
    PnL is the sum of the grouped lots' ATAS PnL, so it reconciles exactly.
    """
    if journal is None or journal.empty:
        return pd.DataFrame()

    cols_out: list[dict] = []
    for (_account, instrument), grp in journal.groupby(["account", "instrument"]):
        grp = grp.reset_index(drop=True)
        span_of = _assign_spans(grp)
        grp = grp.assign(_span=grp.index.map(span_of))
        for _span, lots in grp.groupby("_span"):
            _finalize(lots.sort_values("open_ts_utc"), instrument, cols_out, executions)

    df = pd.DataFrame(cols_out)
    if not df.empty:
        df = df.sort_values("entry_ts_utc").reset_index(drop=True)
        df.insert(0, "trade_no", range(1, len(df) + 1))
    return df


def _assign_spans(grp: pd.DataFrame) -> dict[int, int]:
    """Map each lot's row index to a flat->flat span id via running position.

    Events sharing a timestamp are applied as one batch (opens before closes)
    and the flat check happens only after the batch, so a position that is
    momentarily netted to zero mid-instant doesn't split a trade spuriously.
    """
    events: list[tuple] = []
    for i, r in grp.iterrows():
        events.append((r["open_ts_utc"], 0, r["open_volume"], i, True))
        events.append((r["close_ts_utc"], 1, r["close_volume"], i, False))
    events.sort(key=lambda e: (e[0], e[1]))

    span_of: dict[int, int] = {}
    span = 0
    pos = 0.0
    prev_ts = None
    for ts, batch in groupby(events, key=lambda e: e[0]):
        if pos != 0.0 and prev_ts is not None and ts - prev_ts > SESSION_GAP:
            span += 1
            pos = 0.0
        for _ts, _kind, dv, row_idx, is_open in batch:
            if is_open:
                span_of[row_idx] = span
            pos += dv
        prev_ts = ts
        if abs(pos) < 1e-9:
            pos = 0.0
            span += 1
    return span_of


def _window_fills(
    executions: pd.DataFrame | None, account: str, instrument: str,
    start, end,
) -> list[dict] | None:
    """Executions for this account/instrument inside [start, end], as marker dicts.

    Returns None when no executions are available in the window (e.g. a
    truncated Replay export), so the chart simply omits fill markers.
    """
    if executions is None or executions.empty:
        return None
    m = (
        (executions["account"] == account)
        & (executions["instrument"] == instrument)
        & (executions["ts_utc"] >= start)
        & (executions["ts_utc"] <= end)
    )
    sub = executions[m].sort_values("ts_utc")
    if sub.empty:
        return None
    return [
        {
            "exchange_id": f["exchange_id"],
            "ts_local": f["ts_local"],
            "ts_utc": f["ts_utc"],
            "direction": f["direction"],
            "price": f["price"],
            "volume": f["volume"],
        }
        for _, f in sub.iterrows()
    ]


def _finalize(
    lots: pd.DataFrame, instrument: str, out: list[dict],
    executions: pd.DataFrame | None,
) -> None:
    account = lots.iloc[0]["account"]
    open_vol = lots["open_volume"]
    close_vol = lots["close_volume"]
    direction = "Long" if open_vol.iloc[0] > 0 else "Short"

    abs_open = open_vol.abs()
    abs_close = close_vol.abs()

    def wavg(price: pd.Series, weight: pd.Series) -> float:
        total = weight.sum()
        return float((price * weight).sum() / total) if total else float("nan")

    avg_entry = wavg(lots["open_price"], abs_open)
    avg_exit = wavg(lots["close_price"], abs_close)
    max_contracts = float(abs_open.sum())

    entry_ts_utc = lots["open_ts_utc"].min()
    exit_ts_utc = lots["close_ts_utc"].max()
    entry_ts_local = lots["open_ts_local"].min()
    exit_ts_local = lots["close_ts_local"].max()
    duration_s = (exit_ts_utc - entry_ts_utc).total_seconds()

    gross_pnl = float(lots["pnl"].sum())
    comments = [c for c in lots["comment"].fillna("").tolist() if c]

    out.append({
        "trade_key": _trade_key(lots.iloc[0]["dedupe_key"], instrument),
        "instrument": instrument,
        "account": account,
        "direction": direction,
        "avg_entry": avg_entry,
        "avg_exit": avg_exit,
        "max_contracts": max_contracts,
        "leg_count": int(len(lots)),
        "entry_ts_utc": entry_ts_utc,
        "exit_ts_utc": exit_ts_utc,
        "entry_ts_local": entry_ts_local,
        "exit_ts_local": exit_ts_local,
        "duration_s": duration_s,
        "gross_pnl": gross_pnl,
        "commission": 0.0,
        "net_pnl": gross_pnl,
        "open_position": False,
        "fills": _window_fills(executions, account, instrument, entry_ts_utc, exit_ts_utc),
        "comment": "; ".join(dict.fromkeys(comments)),
    })


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
    df["commission"] = 0.0
    df["net_pnl"] = df["pnl"]
    df["leg_count"] = 2
    df["open_position"] = False
    df["trade_key"] = df["dedupe_key"].str[:16]
    df["fills"] = None
    df = df.sort_values("entry_ts_utc").reset_index(drop=True)
    df.insert(0, "trade_no", range(1, len(df) + 1))
    keep = [
        "trade_no", "trade_key", "instrument", "account", "direction", "avg_entry",
        "avg_exit", "max_contracts", "leg_count", "entry_ts_utc", "exit_ts_utc",
        "entry_ts_local", "exit_ts_local", "duration_s", "gross_pnl", "commission",
        "net_pnl", "open_position", "fills", "comment",
    ]
    return df[[c for c in keep if c in df.columns]]


def reconcile(logical: pd.DataFrame, journal: pd.DataFrame) -> dict:
    """Sanity-check computed logical PnL against ATAS Journal total PnL."""
    logical_pnl = float(logical["net_pnl"].sum()) if not logical.empty else 0.0
    atas_pnl = float(journal["pnl"].sum()) if not journal.empty else 0.0
    return {
        "logical_net_pnl": logical_pnl,
        "atas_journal_pnl": atas_pnl,
        "difference": logical_pnl - atas_pnl,
        "logical_trades": 0 if logical.empty else len(logical),
        "atas_rows": 0 if journal.empty else len(journal),
    }
