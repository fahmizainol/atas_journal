"""The aggregate-eviction and survivorship regressions.

Both were caused by ``_latest_attempt_per_day``, which kept only each calendar
day's most recently *modified* ``source_file``:

1. **Eviction** — the group key was the day alone, with no account. On a day
   holding both a live prop-account session and a replay of that same day, the
   replay export's later mtime evicted the live trades from every aggregate. In
   the real DB that silently deleted 115 of 203 live trades and zeroed out two
   accounts entirely.
2. **Survivorship** — keeping only the latest attempt of a re-done day selects
   the takes that went well. The real DB reported +27,220 replay PnL against a
   true all-attempts sum of +4,850, a 5.6x flattery.

Run directly:  ``.venv/bin/python tests/test_scope_eviction.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from helpers import make_scope  # noqa: E402

DAY = "2026-06-15"


def _row(source_file: str, account: str, pnl: float, minute: int) -> dict:
    open_local = f"{DAY}T09:{minute:02d}:00-04:00"
    close_local = f"{DAY}T09:{minute + 1:02d}:00-04:00"
    rec = {
        "account": account, "instrument": "NQ",
        "open_ts_local": open_local, "close_ts_local": close_local,
        "open_ts_utc": datetime.fromisoformat(open_local).astimezone(timezone.utc).isoformat(),
        "close_ts_utc": datetime.fromisoformat(close_local).astimezone(timezone.utc).isoformat(),
        "open_price": 100.0, "open_volume": 1.0, "close_price": 100.0 + pnl / 20,
        "close_volume": -1.0, "price_pnl": pnl / 20, "profit_ticks": pnl / 5, "pnl": pnl,
        "comment": "", "source_file": source_file,
    }
    rec["dedupe_key"] = f"{source_file}|{account}|{minute}"
    return rec


def _setup(tmp: Path, rows: list[dict], mtimes: dict[str, str]):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    db.insert_journal(conn, rows)
    for sf, mtime in mtimes.items():
        db.mark_imported(conn, sf, file_mtime=mtime)
    deps._conn = conn
    return conn


def test_live_trades_survive_a_later_replay_of_the_same_day():
    """A prop-account session and a replay of the same day both reach the
    aggregates, even though the replay export was modified later."""
    live_file, replay_file = "live.xlsx", "replay.xlsx"
    rows = [
        _row(live_file, "LTE100-9GY28W6R-TEST002", -500.0, 30),
        _row(live_file, "LTE100-9GY28W6R-TEST002", 200.0, 40),
        _row(replay_file, "Replay", 1000.0, 50),
    ]
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(
            Path(d), rows,
            # The replay was exported after the live session — the exact shape
            # that used to evict the live trades.
            {live_file: "2026-06-15T20:00:00+00:00", replay_file: "2026-06-16T09:00:00+00:00"},
        )
        db.upsert_session(conn, live_file, "live", "LTE100-9GY28W6R-TEST002")
        db.upsert_session(conn, replay_file, "replay", "Replay")

        scope = make_scope(view="atas")
        assert len(scope.filtered) == 3, "an attempt was evicted from the aggregates"

        by_account = scope.filtered.groupby("account")["net_pnl"].sum().to_dict()
        assert by_account["LTE100-9GY28W6R-TEST002"] == -300.0
        assert by_account["Replay"] == 1000.0

        # And the mode filter is what separates the money from the simulation.
        live_only = make_scope(view="atas", modes="live")
        assert len(live_only.filtered) == 2
        assert float(live_only.filtered["net_pnl"].sum()) == -300.0


def test_archived_sessions_are_excluded_by_default_and_reachable_on_demand():
    """Archive, never delete: the pre-cutover era leaves the default aggregates
    but stays browsable, and deep links still resolve via ``filtered_all``."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(
            Path(d),
            [_row("old.xlsx", "Replay", 750.0, 30), _row("new.xlsx", "Replay", 250.0, 40)],
            {"old.xlsx": "2026-01-01T00:00:00+00:00", "new.xlsx": "2026-06-16T00:00:00+00:00"},
        )
        db.upsert_session(conn, "new.xlsx", "replay", "Replay")
        db.upsert_session(conn, "old.xlsx", "replay", "Replay")
        db.update_session(conn, "old.xlsx", archived=True)

        scope = make_scope(view="atas")
        assert len(scope.filtered) == 1
        assert float(scope.filtered["net_pnl"].sum()) == 250.0
        # filtered_all ignores the archive flag, so an archived trade stays
        # reachable by direct link with the Archive toggle off.
        assert len(scope.filtered_all) == 2

        with_archive = make_scope(view="atas", include_archived=True)
        assert len(with_archive.filtered) == 2
        assert float(with_archive.filtered["net_pnl"].sum()) == 1000.0


def test_model_filter_and_effective_model_from_backtest_session():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(
            Path(d),
            [_row("bt.xlsx", "Replay", 100.0, 30), _row("rp.xlsx", "Replay", 50.0, 40)],
            {"bt.xlsx": "2026-06-15T00:00:00+00:00", "rp.xlsx": "2026-06-15T01:00:00+00:00"},
        )
        model_id = db.create_model(conn, "Spring")
        db.upsert_session(conn, "bt.xlsx", "replay", "Replay")
        db.upsert_session(conn, "rp.xlsx", "replay", "Replay")
        db.update_session(conn, "bt.xlsx", mode="backtest", model_id=model_id)

        scope = make_scope(view="atas", models=str(model_id))
        # The backtest session binds its trades session-wide, with no per-trade row.
        assert len(scope.filtered) == 1
        assert float(scope.filtered["net_pnl"].sum()) == 100.0

        # A per-trade binding beats nothing at all on the plain replay session.
        rp_key = make_scope(view="atas").filtered.query("source_file == 'rp.xlsx'").iloc[0]
        db.set_trade_model(conn, rp_key["logical_trade_key"], model_id)
        both = make_scope(view="atas", models=str(model_id))
        assert len(both.filtered) == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
