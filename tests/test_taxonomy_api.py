"""Setup/confluence CRUD endpoints + note auto-register + filters sourcing.

Calls the router functions directly with a temp DB injected into deps._conn
(same pattern as test_scan_endpoint.py).

Run directly:  ``.venv/bin/python tests/test_taxonomy_api.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from api.routers import confluences, filters, notes, setups  # noqa: E402
from api.scope import resolve_scope  # noqa: E402


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    deps._conn = conn
    return conn


def _scope():
    return resolve_scope(
        view="atas", instruments=None, accounts=None,
        start=None, end=None, tags=None, tz=None,
    )


def test_list_returns_seeded():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = setups.list_setups()
        names = [s["name"] for s in out["setups"]]
        assert "Value-Area Fade" in names
        cout = confluences.list_confluences()
        cnames = [c["name"] for c in cout["confluences"]]
        assert "VAL Sesh" in cnames and "PDC" in cnames


def test_create_update_delete_setup():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        setups.create_setup(setups.SetupIn(name="Scalp", description="quick"))
        assert "Scalp" in [s["name"] for s in setups.list_setups()["setups"]]
        setups.update_setup(setups.SetupUpdate(name="Scalp", new_name="Scalper"))
        names = [s["name"] for s in setups.list_setups()["setups"]]
        assert "Scalper" in names and "Scalp" not in names
        setups.delete_setup(setups.SetupDelete(name="Scalper"))
        assert "Scalper" not in [s["name"] for s in setups.list_setups()["setups"]]
        _ = conn


def test_create_rejects_blank():
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        try:
            setups.create_setup(setups.SetupIn(name="   "))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400


def test_put_note_auto_registers():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        notes.put_note(
            "kx1",
            notes.NoteIn(
                note="n", tags=[],
                setups=["Totally New Setup"],
                confluences=["Totally New Conf"],
            ),
        )
        assert "Totally New Setup" in [s["name"] for s in setups.list_setups()["setups"]]
        assert "Totally New Conf" in [c["name"] for c in confluences.list_confluences()["confluences"]]


def test_filters_includes_unused_master_names():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        # A setup created but never tagged on a trade must still autocomplete.
        setups.create_setup(setups.SetupIn(name="Unused Setup"))
        out = filters.filters(_scope())
        assert "Unused Setup" in out["setups"]
        assert "Value-Area Fade" in out["setups"]  # seeded, unused


def test_delete_cascades_through_endpoints():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note(
            "kx1",
            notes.NoteIn(note="keep me", setups=["Level Bounce"], confluences=["Absorption"]),
        )
        setups.delete_setup(setups.SetupDelete(name="Level Bounce"))
        note = db.get_note(conn, "kx1")
        assert note["note"] == "keep me"
        assert json.loads(note["setups_json"]) == []
        assert json.loads(note["confluences_json"]) == ["Absorption"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
