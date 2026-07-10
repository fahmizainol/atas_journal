"""Every replay attempt counts — no collapsing to the latest take.

Re-doing a day and keeping only the take that went well is survivorship bias
dressed up as an aggregate. The scope sums all attempts; the day explorer is
where you look at one take in isolation.

Run directly:  ``.venv/bin/python tests/test_scope_attempts.py``
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
from journal import metrics  # noqa: E402
from api import deps  # noqa: E402
from api.routers import calendar  # noqa: E402
from helpers import make_scope  # noqa: E402
from test_scope_eviction import DAY, _row  # noqa: E402


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    # Two attempts of the same day: a losing first take, a winning re-do.
    db.insert_journal(conn, [
        _row("take1.xlsx", "Replay", -800.0, 30),
        _row("take2.xlsx", "Replay", 1200.0, 30),
    ])
    db.mark_imported(conn, "take1.xlsx", file_mtime=f"{DAY}T20:00:00+00:00")
    db.mark_imported(conn, "take2.xlsx", file_mtime=f"{DAY}T22:00:00+00:00")
    db.upsert_session(conn, "take1.xlsx", "replay", "Replay")
    db.upsert_session(conn, "take2.xlsx", "replay", "Replay")
    deps._conn = conn
    return conn


def test_both_attempts_reach_the_aggregates():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        scope = make_scope(view="atas")
        assert len(scope.filtered) == 2
        # The all-attempts truth, not the flattering +1200 of the latest take.
        assert float(scope.filtered["net_pnl"].sum()) == 400.0
        assert metrics.compute_metrics(scope.filtered)["net_pnl"] == 400.0


def test_calendar_day_cell_sums_every_attempt():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["net_pnl"] == 400.0
        assert cell["trades"] == 2
        assert cell["attempts"] == 2


def test_calendar_badge_agrees_with_the_cell_it_labels():
    """The attempt count must describe the same trades the cell's PnL sums —
    badging "2 attempts" over a total covering one is a contradiction."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.update_session(conn, "take1.xlsx", archived=True)

        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["attempts"] == 1
        assert cell["trades"] == 1
        assert cell["net_pnl"] == 1200.0

        # With the archive toggle on, both takes are back in the cell and badge.
        out = calendar.calendar(make_scope(view="atas", include_archived=True))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["attempts"] == 2 and cell["trades"] == 2 and cell["net_pnl"] == 400.0


def test_day_explorer_still_isolates_one_attempt():
    """The aggregate sums the takes; the day view shows exactly one."""
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        scope = make_scope(view="atas")
        detail = calendar.day_detail(DAY, source_file="take1.xlsx", scope=scope)
        assert detail["source_file"] == "take1.xlsx"
        assert len(detail["trades"]) == 1
        assert detail["kpis"]["net_pnl"] == -800.0
        assert len(detail["attempts"]) == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
