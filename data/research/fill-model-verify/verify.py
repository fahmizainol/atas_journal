"""Race the Replay fill model against the prop-firm account that actually paid.

The fill model (frontend/src/lib/fillModel.ts) charges three things a perfect
replay does not: commission per side, a tick to cross the spread, a tick of
queue on resting limits. All three are assumptions about what a funded account
experiences. The archived prop-firm journal is the one dataset that can check
them: 479 live executions with millisecond timestamps, fill prices, sizes and
(on five of six accounts) the commission actually charged — taken on the same
CME feed the tick cache holds.

Four races, each against a knob:

  1. TAPE FIDELITY — every fill here *is* a CME trade, so it should appear on
     the cached tape at that price within moments of its stamp. The match rate
     is the license for everything below; the time offset is the Rithmic-vs-
     Databento clock skew.
  2. SPREAD (slipTicks=1) — classify each fill aggressive/passive by the
     aggressor flag of its matched print. An aggressive fill's distance from
     the print before it is what crossing the book actually cost.
  3. QUEUE (queueTicks=1) — a passive fill the model would only grant after the
     tape trades a tick *through* the level. Count how many real passive fills
     had that trade-through before them, and how many filled on a touch the
     model would deny.
  4. COMMISSION ($7/side default) — what the firms actually charged, per
     contract per side, straight off the executions table.

Plus the accounting: the journal's pnl / price_pnl / profit_ticks columns,
reverse-engineered so the engine's arithmetic can be compared like for like.

    .venv/bin/python data/research/fill-model-verify/verify.py

Reads the July-11 DB backup (the live accounts were never re-imported into the
current journal.db) and the tick cache. Cache-only throughout: a session that
was never bought is reported missing, never fetched.
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from journal import tick_bars  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

DB = ROOT / "data" / "journal.db.pre-mode-folders.bak"
OUT = Path(__file__).with_name("fills.parquet")
SUMMARY = Path(__file__).with_name("summary.json")

TICK = 0.25
POINT_VALUE = 20.0
#: How far a fill's print may sit from its journal stamp and still be "its"
#: print. Wide enough for clock skew, narrow enough that a revisit of the same
#: price a minute later can't masquerade as the fill.
MATCH_S = 2.0
#: Lookbacks for the queue test: was there a trade *through* the level this
#: recently? Two horizons because "recently" is the judgement call.
THROUGH_WINDOWS_S = (10.0, 60.0)


def load_executions() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT account, instrument, ts_utc, direction, price, volume, commission "
        "FROM executions WHERE account != 'Replay' ORDER BY ts_utc",
        conn,
    )
    conn.close()
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True, format="ISO8601")
    df["dir"] = np.where(df["direction"] == "Buy", 1, -1)
    return df


def load_journal() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT account, instrument, open_ts_utc, close_ts_utc, open_price, "
        "close_price, open_volume, price_pnl, profit_ticks, pnl "
        "FROM atas_journal WHERE account != 'Replay'",
        conn,
    )
    conn.close()
    return df


#: 2026-06-16: the account traded NQU6 (ATAS stamps agree, and its price levels
#: are hundreds of points off the NQM6 tape) but the roll map — Databento's
#: volume roll — still hands that session to NQM6, and no NQU6 tape for the day
#: was ever bought. 71 fills with no tape for the contract they were struck on:
#: excluded as untestable, not counted as tape infidelity.
NO_TAPE_FOR_CONTRACT = {pd.Timestamp("2026-06-16").date()}


def race_fills(ex: pd.DataFrame) -> pd.DataFrame:
    """One row per execution: its matched print, class, slip and queue reads."""
    ex = ex.copy()
    ex["day"] = ex["ts"].map(tickmod.session_date_for)
    rows = []
    for (instrument, day), grp in ex.groupby(["instrument", "day"], sort=True):
        tape = None if day in NO_TAPE_FOR_CONTRACT else tick_bars.session_ticks(instrument, day)
        if tape is None or tape.empty:
            for _, e in grp.iterrows():
                rows.append({**base(e), "tape": False})
            continue
        tns = tape["ts_utc"].astype("int64").to_numpy()  # ns since epoch
        px = tape["price"].to_numpy()
        side = tape["side"].to_numpy()
        for _, e in grp.iterrows():
            rows.append(race_one(e, tns, px, side))
    return pd.DataFrame(rows)


def base(e: pd.Series) -> dict:
    return {
        "account": e["account"],
        "instrument": e["instrument"],
        "ts": e["ts"],
        "dir": int(e["dir"]),
        "price": float(e["price"]),
        "volume": float(e["volume"]),
        "commission": float(e["commission"]),
    }


def race_one(e: pd.Series, tns: np.ndarray, px: np.ndarray, side: np.ndarray) -> dict:
    out = {**base(e), "tape": True}
    t0 = int(e["ts"].value)
    lo = int(np.searchsorted(tns, t0 - int(MATCH_S * 1e9)))
    hi = int(np.searchsorted(tns, t0 + int(MATCH_S * 1e9)))
    at = np.flatnonzero(px[lo:hi] == e["price"]) + lo
    if at.size == 0:
        out["matched"] = False
        # How far away the nearest same-price print actually is — tells apart
        # "clock skew beyond the window" from "that price never printed".
        wide = np.flatnonzero(px == e["price"])
        if wide.size:
            out["nearest_same_px_s"] = float(np.min(np.abs(tns[wide] - t0)) / 1e9)
        return out
    j = int(at[np.argmin(np.abs(tns[at] - t0))])
    out["matched"] = True
    out["skew_ms"] = (tns[j] - t0) / 1e6
    # Aggressor read: my buy matching a buy-aggressor print means I crossed;
    # matching a sell-aggressor print means my resting bid was hit.
    mine = "B" if e["dir"] > 0 else "A"
    out["aggressive"] = bool(side[j] == mine) if side[j] in ("A", "B") else None
    # What crossing cost: distance from the print just before the fill's own,
    # signed so +1 is one tick adverse (paid), -1 a tick of improvement.
    if j > 0:
        out["slip_ticks"] = (e["price"] - px[j - 1]) * e["dir"] / TICK
    # Queue: had the tape traded a full tick THROUGH the level before this
    # fill, within each lookback? The model refuses passive fills that lack
    # one, so a real passive fill without it is a fill the replay would deny.
    lvl = e["price"] - e["dir"] * TICK  # one tick beyond, on the far side
    for w in THROUGH_WINDOWS_S:
        k = int(np.searchsorted(tns, tns[j] - int(w * 1e9)))
        seg = px[k:j]
        through = bool(((seg - lvl) * e["dir"] <= 0).any()) if seg.size else False
        out[f"through_{int(w)}s"] = through
    return out


def commission_report(ex: pd.DataFrame) -> dict:
    per = {}
    for acct, g in ex.groupby("account"):
        contracts = float(g["volume"].sum())
        comm = float(-g["commission"].sum())  # stored negative = charged
        per[acct] = {
            "executions": int(len(g)),
            "contracts": contracts,
            "commission_usd": round(comm, 2),
            "per_contract_side": round(comm / contracts, 3) if contracts else None,
        }
    return per


def accounting_report(j: pd.DataFrame) -> dict:
    """What the journal's three P&L columns actually are, tested not assumed."""
    j = j.copy()
    d = {}
    # Hypothesis A: price_pnl is points, pnl = points * $20 (gross).
    j["gross_usd"] = j["price_pnl"] * POINT_VALUE
    d["pnl_minus_gross_usd_total"] = round(float((j["pnl"] - j["gross_usd"]).sum()), 2)
    d["rows_where_pnl_equals_gross"] = int((j["pnl"] - j["gross_usd"]).abs().lt(0.01).sum())
    # Hypothesis B: profit_ticks * $5 = pnl.
    d["rows_where_pnl_equals_ticks_x5"] = int(
        (j["pnl"] - j["profit_ticks"] * 5.0).abs().lt(0.01).sum()
    )
    # Multi-lot: the rows hypothesis A misses should be pnl = points * $20 * lots.
    d["rows_where_pnl_equals_gross_x_lots"] = int(
        (j["pnl"] - j["gross_usd"] * j["open_volume"]).abs().lt(0.01).sum()
    )
    # Direct read: price_pnl vs the price difference on the row itself.
    signed = (j["close_price"] - j["open_price"])
    d["rows_where_price_pnl_is_signed_diff"] = int((j["price_pnl"].abs() - signed.abs()).abs().lt(0.01).sum())
    d["n_rows"] = int(len(j))
    return d


def main() -> None:
    ex = load_executions()
    res = race_fills(ex)
    res.to_parquet(OUT)

    on_tape = res[res["tape"]]
    matched = on_tape[on_tape["matched"] == True]  # noqa: E712
    summary: dict = {
        "executions": int(len(res)),
        "with_tape": int(len(on_tape)),
        "no_tape_by_day": {
            str(d): int(n)
            for d, n in res[~res["tape"]].groupby(res[~res["tape"]]["ts"].dt.date).size().items()
        },
        "match_rate": round(len(matched) / len(on_tape), 4) if len(on_tape) else None,
    }
    if len(matched):
        summary["skew_ms"] = {
            "median": round(float(matched["skew_ms"].median()), 1),
            "p10": round(float(matched["skew_ms"].quantile(0.1)), 1),
            "p90": round(float(matched["skew_ms"].quantile(0.9)), 1),
        }
        cls = matched[matched["aggressive"].notna()]
        agg = cls[cls["aggressive"] == True]  # noqa: E712
        psv = cls[cls["aggressive"] == False]  # noqa: E712
        summary["classified"] = {"aggressive": int(len(agg)), "passive": int(len(psv))}
        if len(agg):
            s = agg["slip_ticks"].dropna()
            summary["aggressive_slip_ticks"] = {
                "mean": round(float(s.mean()), 3),
                "dist": {str(k): int(v) for k, v in s.round().value_counts().sort_index().items()},
            }
        if len(psv):
            summary["passive_queue"] = {
                f"through_{int(w)}s_rate": round(float(psv[f"through_{int(w)}s"].astype(bool).mean()), 4)
                for w in THROUGH_WINDOWS_S
            }
            summary["passive_no_through_60s"] = int((psv["through_60s"] == False).sum())  # noqa: E712
        summary["by_account"] = {
            acct: {
                "n": int(len(g)),
                "match_rate": round(float(g["matched"].astype(bool).mean()), 3),
                "aggressive_share": round(float((g["aggressive"] == True).sum() / max(1, g["aggressive"].notna().sum())), 3),  # noqa: E712
                "agg_slip_mean": round(float(g.loc[g["aggressive"] == True, "slip_ticks"].mean()), 3)  # noqa: E712
                if (g["aggressive"] == True).any()  # noqa: E712
                else None,
            }
            for acct, g in on_tape.groupby("account")
        }
        unmatched = on_tape[on_tape["matched"] == False]  # noqa: E712
        if "nearest_same_px_s" in unmatched:
            near = unmatched["nearest_same_px_s"].dropna()
            summary["unmatched_nearest_same_px_s"] = {
                "n": int(len(unmatched)),
                "never_printed": int(len(unmatched) - len(near)),
                "median_s": round(float(near.median()), 2) if len(near) else None,
                "within_10s": int((near <= 10).sum()),
            }
    summary["commission"] = commission_report(ex)
    summary["accounting"] = accounting_report(load_journal())

    SUMMARY.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
