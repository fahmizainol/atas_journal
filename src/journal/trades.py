"""Build logical trades (flat->flat) from executions, and expose ATAS rows.

A logical trade spans from a flat position through any number of scale-in /
scale-out fills back to flat. PnL is computed from the executions using the
contract point value, signed by the realized direction of each closed lot.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from .config import point_value


def _trade_key(first_exchange_id: str, instrument: str) -> str:
    return hashlib.sha1(f"{instrument}|{first_exchange_id}".encode()).hexdigest()[:16]


def _signed_qty(direction: str, volume: float) -> float:
    return volume if direction == "Buy" else -volume


def build_logical_trades(executions: pd.DataFrame) -> pd.DataFrame:
    """Group fills per instrument (UTC time order) into flat->flat trades.

    Realized PnL uses a running average-cost on the open side; whenever the
    position reduces, the closed quantity books PnL against the average open
    price. Equivalent to ATAS per-position bookkeeping for these exports.
    """
    if executions.empty:
        return pd.DataFrame()

    cols_out: list[dict] = []
    for instrument, grp in executions.sort_values("ts_utc").groupby("instrument"):
        pv = point_value(instrument)
        pos = 0.0          # signed open position
        avg_price = 0.0    # avg price of open position
        cur: dict | None = None  # accumulator for the in-progress trade

        for _, fill in grp.iterrows():
            q = _signed_qty(fill["direction"], fill["volume"])
            price = fill["price"]

            if cur is None:
                cur = _new_trade(fill, instrument)

            cur["fills"].append({
                "exchange_id": fill["exchange_id"],
                "ts_local": fill["ts_local"],
                "ts_utc": fill["ts_utc"],
                "direction": fill["direction"],
                "price": price,
                "volume": fill["volume"],
            })
            cur["commission"] += fill["commission"]

            same_side = (pos == 0) or (pos > 0 and q > 0) or (pos < 0 and q < 0)
            if same_side:
                # opening or adding: blend average price
                new_pos = pos + q
                avg_price = (avg_price * abs(pos) + price * abs(q)) / abs(new_pos)
                pos = new_pos
            else:
                # reducing / closing (possibly flipping through zero)
                closing_qty = min(abs(q), abs(pos))
                direction_sign = 1.0 if pos > 0 else -1.0
                cur["realized_pnl"] += direction_sign * (price - avg_price) * closing_qty * pv
                remainder = abs(q) - closing_qty
                pos += q
                if remainder > 1e-9:
                    # flip: book the close, finalize this trade, open a fresh one
                    # for the overshoot quantity at this fill's price.
                    _finalize(cur, cols_out, pv)
                    cur = _new_trade(fill, instrument)
                    cur["fills"].append({
                        "exchange_id": fill["exchange_id"],
                        "ts_local": fill["ts_local"],
                        "ts_utc": fill["ts_utc"],
                        "direction": fill["direction"],
                        "price": price,
                        "volume": remainder,
                    })
                    cur["commission"] += 0.0
                    avg_price = price
                    # pos already equals the signed remainder after `pos += q`.

            if abs(pos) < 1e-9:
                pos = 0.0
                _finalize(cur, cols_out, pv)
                cur = None

        if cur is not None:
            # position left open at end of data; finalize what we have
            _finalize(cur, cols_out, pv, open_position=True)

    df = pd.DataFrame(cols_out)
    if not df.empty:
        df = df.sort_values("entry_ts_utc").reset_index(drop=True)
        df.insert(0, "trade_no", range(1, len(df) + 1))
    return df


def _new_trade(fill, instrument: str) -> dict:
    return {
        "instrument": instrument,
        "account": fill["account"],
        "fills": [],
        "realized_pnl": 0.0,
        "commission": 0.0,
        "first_exchange_id": fill["exchange_id"],
    }


def _finalize(cur: dict, out: list[dict], pv: float, open_position: bool = False) -> None:
    fills = pd.DataFrame(cur["fills"])
    # entry side = first fill's direction; entries are fills matching it
    first_dir = fills.iloc[0]["direction"]
    direction = "Long" if first_dir == "Buy" else "Short"
    entry_side = "Buy" if direction == "Long" else "Sell"
    exit_side = "Sell" if direction == "Long" else "Buy"

    entries = fills[fills["direction"] == entry_side]
    exits = fills[fills["direction"] == exit_side]

    def wavg(d: pd.DataFrame) -> float:
        if d.empty or d["volume"].sum() == 0:
            return float("nan")
        return float((d["price"] * d["volume"]).sum() / d["volume"].sum())

    avg_entry = wavg(entries)
    avg_exit = wavg(exits)
    max_contracts = float(entries["volume"].sum())

    entry_ts_utc = fills["ts_utc"].min()
    exit_ts_utc = fills["ts_utc"].max()
    entry_ts_local = fills["ts_local"].min()
    exit_ts_local = fills["ts_local"].max()
    duration_s = (exit_ts_utc - entry_ts_utc).total_seconds()

    gross_pnl = cur["realized_pnl"]
    net_pnl = gross_pnl - cur["commission"]

    out.append({
        "trade_key": _trade_key(cur["first_exchange_id"], cur["instrument"]),
        "instrument": cur["instrument"],
        "account": cur["account"],
        "direction": direction,
        "avg_entry": avg_entry,
        "avg_exit": avg_exit,
        "max_contracts": max_contracts,
        "leg_count": int(len(fills)),
        "entry_ts_utc": entry_ts_utc,
        "exit_ts_utc": exit_ts_utc,
        "entry_ts_local": entry_ts_local,
        "exit_ts_local": exit_ts_local,
        "duration_s": duration_s,
        "gross_pnl": gross_pnl,
        "commission": cur["commission"],
        "net_pnl": net_pnl,
        "open_position": open_position,
        "fills": cur["fills"],
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
