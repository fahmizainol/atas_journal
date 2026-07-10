"""Auto-import watcher: settle rules, folder->model classification, dedupe.

The watcher must never guess (unknown folders are skipped loudly), never eat a
half-written export (settle rule), and never spam (one warning/error per file
version, not one per tick).

Run directly:  ``.venv/bin/python tests/test_watcher.py``
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from journal import db, watcher  # noqa: E402

SETTLED = 120.0


def _conn(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    return conn


def _stub(calls: list[dict]):
    """Importer stand-in: records the call and marks the file imported, so the
    dedupe path (file_mtime comparison) behaves like the real ingest."""

    def importer(conn, path, source_tz=None, file_mtime=None, mode=None, model_id=None):
        calls.append({
            "file": path.name, "mode": mode, "model_id": model_id,
            "file_mtime": file_mtime,
        })
        db.mark_imported(conn, path.name, file_mtime=file_mtime)
        inferred = mode or "replay"
        db.upsert_session(conn, path.name, inferred, None, model_id=model_id)
        return {"executions": 1, "journal": 1, "statistics": 0}

    return importer


def _drop(directory: Path, name: str, age_s: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"xlsx-bytes")
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _dirs(tmp: Path) -> tuple[Path, Path]:
    imports = tmp / "imports"
    backtest = imports / "backtest"
    backtest.mkdir(parents=True)
    return imports, backtest


def _scan(conn, state, imports, backtest, importer):
    return watcher.scan_once(
        conn, state, imports_dir=imports, backtest_dir=backtest,
        importer=importer, settled_age_s=SETTLED,
    )


def test_settled_root_file_imports_first_tick_as_inferred():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        _drop(imports, "a.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()

        assert _scan(conn, state, imports, backtest, _stub(calls)) == 1
        assert calls[0]["mode"] is None and calls[0]["model_id"] is None
        assert state.events[-1]["kind"] == "imported"
        assert state.events[-1]["mode"] == "replay"


def test_young_file_waits_until_it_survives_a_tick_unchanged():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        path = _drop(imports, "hot.xlsx", age_s=0)
        calls: list[dict] = []
        state = watcher.WatcherState()
        stub = _stub(calls)

        assert _scan(conn, state, imports, backtest, stub) == 0, "first sight: settle"
        # Still being written: size/mtime change resets the settle clock.
        path.write_bytes(b"xlsx-bytes-longer")
        assert _scan(conn, state, imports, backtest, stub) == 0
        assert _scan(conn, state, imports, backtest, stub) == 1, "stable across a tick"
        assert len(calls) == 1


def test_backtest_folder_classifies_and_binds_the_model():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        model_id = db.create_model(conn, "Test Model")
        folder = db.get_model(conn, model_id)["folder"]
        _drop(backtest / folder, "bt.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()

        assert _scan(conn, state, imports, backtest, _stub(calls)) == 1
        assert calls[0]["mode"] == "backtest" and calls[0]["model_id"] == model_id
        sess = db.sessions_map(conn)["bt.xlsx"]
        assert sess["mode"] == "backtest" and sess["model_id"] == model_id
        assert state.events[-1]["model_name"] == "Test Model"


def test_unknown_folder_skips_and_warns_exactly_once():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        _drop(backtest / "no-such-model", "x.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()
        stub = _stub(calls)

        assert _scan(conn, state, imports, backtest, stub) == 0
        assert _scan(conn, state, imports, backtest, stub) == 0
        assert calls == []
        warnings = [e for e in state.events if e["kind"] == "unknown_folder"]
        assert len(warnings) == 1 and warnings[0]["folder"] == "no-such-model"


def test_same_version_never_reimports_but_a_new_export_does():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        path = _drop(imports, "a.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()
        stub = _stub(calls)

        assert _scan(conn, state, imports, backtest, stub) == 1
        assert _scan(conn, state, imports, backtest, stub) == 0, "same mtime: done"

        # Re-exported (new mtime, still settled): the refreshed take re-imports.
        t = time.time() - 300
        os.utime(path, (t, t))
        assert _scan(conn, state, imports, backtest, stub) == 1
        assert len(calls) == 2


def test_real_account_in_a_backtest_folder_gets_a_heads_up():
    """Backtests run on the Replay account; a real account in a model folder is
    almost certainly a mis-filed live export — imported, but flagged."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        model_id = db.create_model(conn, "Test Model")
        folder = db.get_model(conn, model_id)["folder"]
        _drop(backtest / folder, "oops.xlsx", age_s=600)
        state = watcher.WatcherState()

        def importer(conn, path, source_tz=None, file_mtime=None, mode=None, model_id=None):
            db.mark_imported(conn, path.name, file_mtime=file_mtime)
            db.upsert_session(conn, path.name, mode, "PROP-1", model_id=model_id)
            return {"executions": 1, "journal": 1, "statistics": 0}

        assert _scan(conn, state, imports, backtest, importer) == 1
        event = state.events[-1]
        assert event["kind"] == "imported"
        assert "PROP-1" in event["message"]


def test_a_failing_file_reports_once_and_retries_only_when_it_changes():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        path = _drop(imports, "bad.xlsx", age_s=600)
        state = watcher.WatcherState()
        attempts = []

        def broken(conn, path, **kwargs):
            attempts.append(path.name)
            raise ValueError("not an xlsx")

        assert _scan(conn, state, imports, backtest, broken) == 0
        assert _scan(conn, state, imports, backtest, broken) == 0
        assert len(attempts) == 1, "unchanged failure must not retry every tick"
        assert [e["kind"] for e in state.events] == ["error"]

        t = time.time() - 200
        os.utime(path, (t, t))
        assert _scan(conn, state, imports, backtest, broken) == 0
        assert len(attempts) == 2, "a changed file gets a fresh attempt"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
