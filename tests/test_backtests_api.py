"""Backtests router: per-model slice, mode comparison, session rows + notes.

A backtest session binds its model to every trade; the comparison block must
slice the *same* effective model across backtest/replay/live without letting
archived sessions leak into any of them.

Run directly:  ``.venv/bin/python tests/test_backtests_api.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from api.routers import backtests, sessions as sessions_router  # noqa: E402
from test_scope_eviction import _row  # noqa: E402


def _fresh(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    db.insert_journal(conn, [
        _row("bt1.xlsx", "Replay", 100.0, 10),
        _row("bt1.xlsx", "Replay", -40.0, 20),
        _row("bt2.xlsx", "Replay", 60.0, 30),
        _row("bt-archived.xlsx", "Replay", 999.0, 40),
        _row("replay.xlsx", "Replay", 25.0, 50),
        _row("live.xlsx", "PROP-1", -10.0, 55),
    ])
    for sf, mode in (
        ("bt1.xlsx", "replay"), ("bt2.xlsx", "replay"), ("bt-archived.xlsx", "replay"),
        ("replay.xlsx", "replay"), ("live.xlsx", "live"),
    ):
        db.upsert_session(conn, sf, mode)
    deps._conn = conn

    model_id = db.create_model(conn, "Test Model")
    for sf in ("bt1.xlsx", "bt2.xlsx", "bt-archived.xlsx"):
        db.update_session(conn, sf, mode="backtest", model_id=model_id)
    db.update_session(conn, "bt-archived.xlsx", archived=True)
    return conn, model_id


def _bind_trade(conn, source_file: str, model_id: int) -> None:
    """Give one non-backtest trade its own model binding (the trade_model path)."""
    df, *_ = backtests._frames(None)
    key = df[df["source_file"] == source_file]["logical_trade_key"].iloc[0]
    db.set_trade_model(conn, key, model_id)


def test_detail_slices_backtest_trades_and_excludes_archived():
    with tempfile.TemporaryDirectory() as d:
        conn, model_id = _fresh(Path(d))
        out = backtests.backtest_detail(model_id, tz=None)

        # bt1 (2 trades) + bt2 (1) count; the archived take's +999 must not.
        assert out["metrics"]["trades"] == 3
        assert out["metrics"]["net_pnl"] == 100.0 - 40.0 + 60.0
        assert len(out["equity"]) == 3
        assert [p["trade_no"] for p in out["equity"]] == [1, 2, 3]
        assert sorted(out["distribution"]) == [-40.0, 60.0, 100.0]


def test_comparison_tracks_the_same_model_across_modes():
    with tempfile.TemporaryDirectory() as d:
        conn, model_id = _fresh(Path(d))
        _bind_trade(conn, "replay.xlsx", model_id)
        _bind_trade(conn, "live.xlsx", model_id)

        cmp = backtests.backtest_detail(model_id, tz=None)["comparison"]
        assert cmp["backtest"]["trades"] == 3
        assert cmp["replay"]["trades"] == 1 and cmp["replay"]["net_pnl"] == 25.0
        assert cmp["live"]["trades"] == 1 and cmp["live"]["net_pnl"] == -10.0


def test_sessions_list_includes_archived_rows_and_roundtrips_notes():
    with tempfile.TemporaryDirectory() as d:
        conn, model_id = _fresh(Path(d))

        sessions_router.patch_session(
            "bt1.xlsx", sessions_router.SessionPatch(note="clean trend day sample")
        )
        out = backtests.backtest_detail(model_id, tz=None)
        rows = {s["source_file"]: s for s in out["sessions"]}
        assert set(rows) == {"bt1.xlsx", "bt2.xlsx", "bt-archived.xlsx"}
        assert rows["bt1.xlsx"]["note"] == "clean trend day sample"
        assert rows["bt-archived.xlsx"]["archived"] is True
        # The archived take still shows its own numbers on its own row.
        assert rows["bt-archived.xlsx"]["metrics"]["trades"] == 1


def test_overview_reports_sample_progress_per_model():
    with tempfile.TemporaryDirectory() as d:
        conn, model_id = _fresh(Path(d))
        db.update_model(conn, model_id, target_sample=100)

        cards = backtests.backtests_overview(tz=None)["models"]
        card = next(c for c in cards if c["id"] == model_id)
        assert card["metrics"]["trades"] == 3
        assert card["target_sample"] == 100
        assert card["sessions"] == 3
        assert card["folder"] == "test-model"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
