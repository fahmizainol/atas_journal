"""Session cutover, ingest inference, and the sessions router.

The cutover archives the pre-model era in place — never deletes it. Re-importing
an export must not undo a mode or archive choice made in the UI, which is why
every write on the ingest path is INSERT OR IGNORE.

Run directly:  ``.venv/bin/python tests/test_sessions.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import HTTPException  # noqa: E402

from journal import db, ingest  # noqa: E402
from api import deps  # noqa: E402
from api.routers import sessions as sessions_router  # noqa: E402
from test_scope_eviction import _row  # noqa: E402


def _fresh(tmp: Path):
    """A DB whose journal rows predate init_db, i.e. the pre-cutover state."""
    conn = db.connect(tmp / "test.db")
    conn.executescript(db.SCHEMA)
    db.insert_journal(conn, [
        _row("replay.xlsx", "Replay", 100.0, 30),
        _row("live.xlsx", "LTE100-9GY28W6R-TEST002", -50.0, 30),
        # A file that touched both a real account and the replay account: any
        # real money in it makes the whole session live.
        _row("mixed.xlsx", "Replay", 10.0, 30),
        _row("mixed.xlsx", "LTE100-BXH602Q9-TEST001", 20.0, 40),
    ])
    db.init_db(conn)  # runs the cutover
    deps._conn = conn
    return conn


def test_cutover_infers_mode_and_archives_everything():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(Path(d))
        sess = db.sessions_map(conn)
        assert sess["replay.xlsx"]["mode"] == "replay"
        assert sess["live.xlsx"]["mode"] == "live"
        assert sess["mixed.xlsx"]["mode"] == "live"
        assert all(s["archived"] for s in sess.values()), "the old era must be archived"
        assert sess["live.xlsx"]["account"] == "LTE100-9GY28W6R-TEST002"


def test_cutover_never_re_archives_an_unarchived_session():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(Path(d))
        db.update_session(conn, "replay.xlsx", archived=False)
        db.init_db(conn)  # a second startup
        assert db.sessions_map(conn)["replay.xlsx"]["archived"] is False


def test_ingest_infers_mode_and_leaves_new_sessions_unarchived():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(Path(d))
        journal = [{"account": "Replay"}, {"account": "Replay"}]
        mode, account = ingest._infer_session(journal)
        assert (mode, account) == ("replay", "Replay")
        mode, _ = ingest._infer_session([{"account": "Replay"}, {"account": "PROP-1"}])
        assert mode == "live"

        db.upsert_session(conn, "fresh.xlsx", "replay", "Replay")
        assert db.sessions_map(conn)["fresh.xlsx"]["archived"] is False


def test_reimport_preserves_a_manual_backtest_override():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(Path(d))
        model_id = db.create_model(conn, "Test Model")
        db.update_session(conn, "replay.xlsx", mode="backtest", model_id=model_id)

        # Re-importing the same export re-runs the ingest upsert.
        db.upsert_session(conn, "replay.xlsx", "replay", "Replay")
        sess = db.sessions_map(conn)["replay.xlsx"]
        assert sess["mode"] == "backtest"
        assert sess["model_id"] == model_id


def test_patch_requires_a_model_for_backtest_and_unbinds_on_leaving():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(Path(d))
        model_id = db.create_model(conn, "Test Model")

        try:
            sessions_router.patch_session(
                "replay.xlsx", sessions_router.SessionPatch(mode="backtest")
            )
            raise AssertionError("expected a 400: a backtest must bind a model")
        except HTTPException as e:
            assert e.status_code == 400

        sessions_router.patch_session(
            "replay.xlsx", sessions_router.SessionPatch(mode="backtest", model_id=model_id)
        )
        assert db.sessions_map(conn)["replay.xlsx"]["model_id"] == model_id

        # Dropping back to replay unbinds the model rather than leaving a stale id.
        sessions_router.patch_session(
            "replay.xlsx", sessions_router.SessionPatch(mode="replay")
        )
        sess = db.sessions_map(conn)["replay.xlsx"]
        assert sess["mode"] == "replay" and sess["model_id"] is None


def test_patch_rejects_unknown_mode_and_missing_session():
    with tempfile.TemporaryDirectory() as d:
        _fresh(Path(d))
        for source_file, patch, code in (
            ("replay.xlsx", sessions_router.SessionPatch(mode="paper"), 400),
            ("nope.xlsx", sessions_router.SessionPatch(archived=True), 404),
        ):
            try:
                sessions_router.patch_session(source_file, patch)
                raise AssertionError(f"expected {code}")
            except HTTPException as e:
                assert e.status_code == code


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
