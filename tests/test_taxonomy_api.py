"""Legacy setup/confluence read endpoints + filters sourcing.

Setups/confluences are read-only since the model cutover: the CRUD endpoints and
the note-save auto-registration are gone (that auto-registration is what let any
typed badge become a permanent master entry). What remains is rendering the
archived pre-cutover era's badges, which is what these cover.

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
sys.path.insert(0, str(ROOT / "tests"))

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from api.routers import confluences, filters, notes, setups  # noqa: E402
from helpers import make_scope  # noqa: E402


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    deps._conn = conn
    return conn


def _scope():
    return make_scope(view="atas")


def test_list_returns_seeded():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = setups.list_setups()
        names = [s["name"] for s in out["setups"]]
        assert "Value-Area Fade" in names
        cout = confluences.list_confluences()
        cnames = [c["name"] for c in cout["confluences"]]
        assert "VAL Sesh" in cnames and "PDC" in cnames


def test_taxonomy_crud_endpoints_are_gone():
    """The sprawl in the old taxonomy was structural: every badge typed into the
    trade form registered a permanent name. Removing the write path is the fix."""
    gone = [
        (setups, "create_setup"), (setups, "update_setup"), (setups, "delete_setup"),
        (confluences, "create_confluence"), (confluences, "update_confluence"),
        (confluences, "delete_confluence"),
    ]
    for mod, name in gone:
        assert not hasattr(mod, name), f"{mod.__name__}.{name} still exists"


def test_put_note_does_not_register_new_names():
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
        assert "Totally New Setup" not in [s["name"] for s in setups.list_setups()["setups"]]
        assert "Totally New Conf" not in [
            c["name"] for c in confluences.list_confluences()["confluences"]
        ]
        # The badge still saves on the trade — only the master list is protected.
        assert notes.get_note("kx1")["setups"] == ["Totally New Setup"]


def test_filters_includes_master_names():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = filters.filters(_scope())
        assert "Value-Area Fade" in out["setups"]  # seeded, unused
        assert "Absorption" in out["confluences"]


def test_filters_exposes_modes_and_models():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        out = filters.filters(_scope())
        # No sessions imported yet, so the mode options fall back to the full set.
        assert set(out["modes"]) == {"live", "replay", "backtest"}
        names = [m["name"] for m in out["models"]]
        assert "Value-Area Fade" in names  # seeded as a starter model


def test_note_survives_taxonomy_read():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        notes.put_note(
            "kx1",
            notes.NoteIn(note="keep me", setups=["Level Bounce"], confluences=["Absorption"]),
        )
        note = db.get_note(conn, "kx1")
        assert note["note"] == "keep me"
        assert json.loads(note["setups_json"]) == ["Level Bounce"]
        assert json.loads(note["confluences_json"]) == ["Absorption"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
