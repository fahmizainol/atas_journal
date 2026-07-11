"""The scope cache: hit, invalidation-on-write, conn swap, view/tz isolation.

``resolve_scope`` memoizes its raw DB loads and built base frames, keyed by
``(conn identity, conn.total_changes, PRAGMA data_version)``. Nothing ever
invalidates by hand — any write through the shared connection is a miss on the
next request. These tests pin that contract.

Run directly:  ``.venv/bin/python tests/test_scope_cache.py``
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
from api import deps, scope as scope_mod  # noqa: E402
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


def _setup(tmp: Path, rows: list[dict]):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    db.insert_journal(conn, rows)
    db.mark_imported(conn, rows[0]["source_file"], file_mtime="2026-06-15T20:00:00+00:00")
    db.upsert_session(conn, rows[0]["source_file"], "replay", "Replay")
    deps._conn = conn
    return conn


class _LoadCounter:
    """Counts calls to db.load_executions while delegating to the real one."""

    def __init__(self):
        self.calls = 0
        self.real = db.load_executions

    def __call__(self, conn):
        self.calls += 1
        return self.real(conn)


def test_second_resolve_with_no_write_is_a_cache_hit():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d), [_row("a.xlsx", "Replay", 100.0, 30)])
        counter = _LoadCounter()
        scope_mod.db.load_executions = counter
        try:
            s1 = make_scope()
            s2 = make_scope()
        finally:
            scope_mod.db.load_executions = counter.real
        assert counter.calls == 1, "second resolve must not reload the DB"
        assert s1.filtered.equals(s2.filtered)
        assert s2.base is s1.base, "the built base frame is reused, not rebuilt"


def test_any_write_through_the_conn_invalidates():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d), [_row("a.xlsx", "Replay", 100.0, 30)])
        s1 = make_scope()
        assert len(s1.filtered) == 1

        # A note write (tag) must show up on the very next resolve...
        key = s1.filtered.iloc[0]["logical_trade_key"]
        db.save_note(conn, key, "note", '["A+"]')
        s2 = make_scope(tags="A+")
        assert len(s2.filtered) == 1, "tag saved after caching must be filterable"

        # ...and so must new journal rows.
        db.insert_journal(conn, [_row("a.xlsx", "Replay", -50.0, 40)])
        s3 = make_scope()
        assert len(s3.filtered) == 2, "a write must invalidate the cached frames"


def test_swapping_the_shared_connection_invalidates():
    with tempfile.TemporaryDirectory() as d:
        one = Path(d) / "one"
        one.mkdir()
        _setup(one, [_row("a.xlsx", "Replay", 100.0, 30)])
        assert float(make_scope().filtered["net_pnl"].sum()) == 100.0

        # A fresh conn restarts total_changes at 0 — the identity check, not the
        # counter, is what must catch this.
        two = Path(d) / "two"
        two.mkdir()
        _setup(two, [_row("b.xlsx", "Replay", 999.0, 30)])
        assert float(make_scope().filtered["net_pnl"].sum()) == 999.0


def test_views_and_tzs_do_not_cross_contaminate():
    rows = [
        _row("a.xlsx", "Replay", 100.0, 30),
        _row("a.xlsx", "Replay", 50.0, 31),  # adjacent lots: one logical trade
    ]
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d), rows)
        atas = make_scope(view="atas")
        logical = make_scope(view="logical")
        assert len(atas.filtered) == 2
        assert len(logical.filtered) == 1
        # Same data version, different tz: frames differ in their clock.
        ny = make_scope(tz="New York")
        kl = make_scope(tz="Kuala Lumpur")
        assert str(ny.filtered["entry_ts_local"].dt.tz) != str(kl.filtered["entry_ts_local"].dt.tz)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
