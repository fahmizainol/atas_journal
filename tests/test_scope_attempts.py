"""Two readings of a re-done day, and which surface owes which.

Every replay attempt counts *in the aggregates*: re-doing a day and keeping only
the take that went well is survivorship bias dressed up as a statistic, so
``scope.filtered`` — and everything computed off it — sums the takes.

The **calendar** is the exception, and deliberately so (2026-08-29): a cell
answers "where did this day finish", which is the take you finished on, not the
takes added together. It collapses to the single latest attempt by file, badges
how many exist, and the day explorer opens that same take.

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


def test_calendar_day_cell_reads_the_latest_attempt_alone():
    """+1200, not the 400 the two takes sum to — the day finished on take2."""
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["net_pnl"] == 1200.0
        assert cell["trades"] == 1
        # The badge still counts what exists, so a cell showing one of two says so.
        assert cell["attempts"] == 2


def test_calendar_picks_the_latest_by_modified_not_by_name():
    """take1 uploaded last does not make it the day's take; mtime decides.

    The order is ``_attempt_order``'s, shared with the day explorer, so the cell
    and the take the explorer opens by default cannot disagree."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        # take1 keeps the older "Date modified" but becomes the newest upload.
        db.mark_imported(conn, "take1.xlsx", file_mtime=f"{DAY}T20:00:00+00:00")

        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["net_pnl"] == 1200.0

        detail = calendar.day_detail(DAY, source_file=None, scope=make_scope(view="atas"))
        assert detail["source_file"] == "take2.xlsx"
        assert detail["kpis"]["net_pnl"] == cell["net_pnl"]


def test_calendar_cell_names_the_account_it_read():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["account"] == "Replay"


def test_calendar_badge_agrees_with_the_cell_it_labels():
    """The attempt count must describe the takes the cell could have shown.

    It counts in-scope takes, so archiving one drops it from both the badge and
    the pool the latest is picked from — badging "2 attempts" when only one is
    in scope would offer a take that clicking through cannot reach."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        db.update_session(conn, "take2.xlsx", archived=True)

        # The winning re-do is out of scope, so the day falls back to take1.
        out = calendar.calendar(make_scope(view="atas"))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["attempts"] == 1
        assert cell["trades"] == 1
        assert cell["net_pnl"] == -800.0

        # With the archive toggle on, take2 is back and is again the latest.
        out = calendar.calendar(make_scope(view="atas", include_archived=True))
        cell = next(c for c in out["days"] if c["date"] == DAY)
        assert cell["attempts"] == 2 and cell["trades"] == 1 and cell["net_pnl"] == 1200.0


def test_day_explorer_still_isolates_one_attempt():
    """The scope aggregate sums the takes; the day view shows exactly one."""
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
