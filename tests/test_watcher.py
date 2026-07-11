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

from journal import db, ingest, watcher  # noqa: E402

SETTLED = 120.0


def _conn(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    return conn


def _stub(calls: list[dict], imports: Path):
    """Importer stand-in: records the call and marks the file imported, so the
    dedupe path (file_mtime comparison) behaves like the real ingest — which
    keys the DB by the path relative to the imports dir, not the bare name."""

    def importer(conn, path, source_tz=None, file_mtime=None, mode=None, model_id=None,
                 lock=None):
        source = ingest.source_key(path, imports)
        calls.append({
            "file": path.name, "mode": mode, "model_id": model_id,
            "file_mtime": file_mtime,
        })
        db.mark_imported(conn, source, file_mtime=file_mtime)
        inferred = mode or "replay"
        db.upsert_session(conn, source, inferred, None, model_id=model_id)
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

        assert _scan(conn, state, imports, backtest, _stub(calls, imports)) == 1
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
        stub = _stub(calls, imports)

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

        assert _scan(conn, state, imports, backtest, _stub(calls, imports)) == 1
        assert calls[0]["mode"] == "backtest" and calls[0]["model_id"] == model_id
        # Keyed by the imports-relative path: a backtest export can never
        # collide with a same-named live/replay export in the root.
        sess = db.sessions_map(conn)[f"backtest/{folder}/bt.xlsx"]
        assert sess["mode"] == "backtest" and sess["model_id"] == model_id
        assert state.events[-1]["model_name"] == "Test Model"


def test_same_filename_in_root_and_model_folder_are_distinct_sessions():
    """ATAS names exports by date range, so a backtest of a day that was also
    traded live produces the same filename in both places. Path-relative keys
    keep them from fighting over one DB row."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        model_id = db.create_model(conn, "Test Model")
        folder = db.get_model(conn, model_id)["folder"]
        _drop(imports, "same.xlsx", age_s=600)
        _drop(backtest / folder, "same.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()
        stub = _stub(calls, imports)

        assert _scan(conn, state, imports, backtest, stub) == 2
        sessions = db.sessions_map(conn)
        assert sessions["same.xlsx"]["mode"] == "replay"
        assert sessions[f"backtest/{folder}/same.xlsx"]["mode"] == "backtest"
        assert _scan(conn, state, imports, backtest, stub) == 0, "both settled: done"


def test_unknown_folder_skips_and_warns_exactly_once():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        _drop(backtest / "no-such-model", "x.xlsx", age_s=600)
        calls: list[dict] = []
        state = watcher.WatcherState()
        stub = _stub(calls, imports)

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
        stub = _stub(calls, imports)

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

        def importer(conn, path, source_tz=None, file_mtime=None, mode=None, model_id=None,
                     lock=None):
            source = ingest.source_key(path, imports)
            db.mark_imported(conn, source, file_mtime=file_mtime)
            db.upsert_session(conn, source, mode, "PROP-1", model_id=model_id)
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


def test_parse_runs_unlocked_and_store_runs_locked():
    """The whole point of the lock plumbing: openpyxl parsing must never hold
    the shared DB lock (it stalls every API request), while the DB writes must."""
    from journal import ingest

    class SpyLock:
        def __init__(self):
            self.held = False

        def __enter__(self):
            assert not self.held, "lock is not reentrant"
            self.held = True
            return self

        def __exit__(self, *exc):
            self.held = False
            return False

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = _conn(tmp)
        imports, backtest = _dirs(tmp)
        _drop(imports, "a.xlsx", age_s=600)
        state = watcher.WatcherState()
        spy = SpyLock()
        seen = {"parse_locked": None, "store_locked": None}

        real_parse, real_store = ingest.parse_file, ingest.store_parsed

        def fake_parse(path, source_tz=None):
            seen["parse_locked"] = spy.held
            return {"executions": [], "journal": [], "statistics": []}

        def fake_store(conn, source_name, parsed, **kwargs):
            seen["store_locked"] = spy.held
            return real_store(conn, source_name, parsed, **kwargs)

        ingest.parse_file, ingest.store_parsed = fake_parse, fake_store
        try:
            imported = watcher.scan_once(
                conn, state, imports_dir=imports, backtest_dir=backtest,
                settled_age_s=SETTLED, lock=spy,
            )
        finally:
            ingest.parse_file, ingest.store_parsed = real_parse, real_store

        assert imported == 1
        assert seen["parse_locked"] is False, "parse must run outside the lock"
        assert seen["store_locked"] is True, "store must run inside the lock"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
