"""``init_db`` must be safe to run on every startup.

Each one-time step is guarded by a settings key, and every insert is INSERT OR
IGNORE, so a second run can't duplicate a seed, resurrect a deleted model, or
re-archive a session the user un-archived.

Run directly:  ``.venv/bin/python tests/test_migrations_idempotent.py``
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


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_second_init_does_not_duplicate_seeds():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "test.db")
        db.init_db(conn)
        before = (_count(conn, "models"), _count(conn, "setups"), _count(conn, "confluences"))
        assert before[0] == len(db.SEED_SETUPS)

        db.init_db(conn)
        after = (_count(conn, "models"), _count(conn, "setups"), _count(conn, "confluences"))
        assert before == after


def test_deleting_a_seeded_model_sticks():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "test.db")
        db.init_db(conn)
        conn.execute("DELETE FROM models WHERE name = 'Level Bounce'")
        conn.commit()
        db.init_db(conn)
        names = [m["name"] for m in db.list_models(conn, include_archived=True)]
        assert "Level Bounce" not in names, "the models_seeded guard was not honoured"


def test_guard_settings_are_recorded():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "test.db")
        db.init_db(conn)
        assert db.get_setting(conn, "sessions_cutover_done") == "1"
        assert db.get_setting(conn, "models_seeded") == "1"


def test_second_init_preserves_session_edits():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "test.db")
        db.init_db(conn)
        db.upsert_session(conn, "s.xlsx", "replay", "Replay")
        model_id = db.create_model(conn, "Custom")
        db.update_session(conn, "s.xlsx", mode="backtest", model_id=model_id, archived=False)

        db.init_db(conn)
        sess = db.sessions_map(conn)["s.xlsx"]
        assert sess["mode"] == "backtest"
        assert sess["model_id"] == model_id
        assert sess["archived"] is False


def test_schema_is_additive_and_reverts_by_dropping():
    """The new tables are pure additions — dropping them restores the old DB."""
    new_tables = ("sessions", "models", "model_rules", "trade_model", "trade_rule_checks")
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "test.db")
        db.init_db(conn)
        existing = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(new_tables) <= existing
        for table in new_tables:
            conn.execute(f"DROP TABLE {table}")
        conn.commit()
        # The pre-existing tables are untouched by the drop.
        assert _count(conn, "trade_notes") == 0
        assert _count(conn, "setups") == len(db.SEED_SETUPS)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
