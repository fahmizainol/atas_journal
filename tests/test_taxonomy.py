"""Setup/confluence master list: seed, backfill, CRUD, and tag cascade.

Run directly:  ``.venv/bin/python tests/test_taxonomy.py``
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


def _fresh():
    tmp = tempfile.TemporaryDirectory()
    conn = db.connect(Path(tmp.name) / "test.db")
    db.init_db(conn)
    return tmp, conn


def _names(conn, table):
    return [r["name"] for r in db.list_taxonomy(conn, table)]


def test_seed_applied_with_descriptions():
    tmp, conn = _fresh()
    with tmp:
        setups = db.list_taxonomy(conn, "setups")
        confs = db.list_taxonomy(conn, "confluences")
        assert len(setups) == len(db.SEED_SETUPS), setups
        assert len(confs) == len(db.SEED_CONFLUENCES), confs
        names = _names(conn, "setups")
        assert "Spring / Range Reclaim" in names  # a slashed name survives
        # Descriptions seeded, not blank.
        spring = next(s for s in setups if s["name"] == "Spring / Range Reclaim")
        assert "Wyckoff" in spring["description"]
        # VP split landed as separate confluences.
        cnames = _names(conn, "confluences")
        for n in ("VAL Sesh", "VAH ON", "POC Sesh", "PDH", "Big Buys"):
            assert n in cnames, n


def test_seed_runs_once_deletes_stick():
    tmp, conn = _fresh()
    with tmp:
        db.delete_taxonomy(conn, "setups", "Level Bounce")
        assert "Level Bounce" not in _names(conn, "setups")
        # Re-running init must NOT resurrect the deleted seed (guard flag).
        db.init_db(conn)
        assert "Level Bounce" not in _names(conn, "setups")


def test_backfill_from_existing_trade_tags():
    tmp, conn = _fresh()
    with tmp:
        # A trade tagged with a name that isn't in the seed.
        db.save_note(
            conn, "kx1", "n", "[]",
            json.dumps(["My Custom Setup"]),
            json.dumps(["My Custom Conf"]),
        )
        db.init_db(conn)  # re-run: backfill picks up in-use names
        assert "My Custom Setup" in _names(conn, "setups")
        assert "My Custom Conf" in _names(conn, "confluences")


def test_create_and_register():
    tmp, conn = _fresh()
    with tmp:
        db.create_taxonomy(conn, "setups", "Brand New", "desc here")
        assert "Brand New" in _names(conn, "setups")
        # Idempotent: creating again doesn't clobber the description.
        db.create_taxonomy(conn, "setups", "Brand New", "")
        row = next(s for s in db.list_taxonomy(conn, "setups") if s["name"] == "Brand New")
        assert row["description"] == "desc here"
        # register (inline auto-add) folds new names in, ignores existing.
        db.register_taxonomy(conn, "confluences", ["Fresh One", "Absorption", ""])
        assert "Fresh One" in _names(conn, "confluences")


def test_rename_cascades_to_trades():
    tmp, conn = _fresh()
    with tmp:
        db.save_note(
            conn, "kx1", "n", "[]",
            json.dumps(["Level Bounce", "Value-Area Fade"]),
            json.dumps(["Absorption"]),
        )
        db.update_taxonomy(conn, "setups", "Level Bounce", new_name="Dynamic Bounce")
        assert "Dynamic Bounce" in _names(conn, "setups")
        assert "Level Bounce" not in _names(conn, "setups")
        note = db.get_note(conn, "kx1")
        setups = json.loads(note["setups_json"])
        assert setups == ["Dynamic Bounce", "Value-Area Fade"], setups


def test_rename_into_existing_merges_and_dedupes():
    tmp, conn = _fresh()
    with tmp:
        # Trade carries BOTH names; renaming one onto the other must dedupe.
        db.save_note(
            conn, "kx1", "n", "[]",
            json.dumps(["Level Bounce", "Value-Area Fade"]),
            "[]",
        )
        db.update_taxonomy(conn, "setups", "Level Bounce", new_name="Value-Area Fade")
        names = _names(conn, "setups")
        assert names.count("Value-Area Fade") == 1
        assert "Level Bounce" not in names
        setups = json.loads(db.get_note(conn, "kx1")["setups_json"])
        assert setups == ["Value-Area Fade"], setups


def test_update_description_only():
    tmp, conn = _fresh()
    with tmp:
        db.update_taxonomy(conn, "setups", "Level Bounce", description="new blurb")
        row = next(s for s in db.list_taxonomy(conn, "setups") if s["name"] == "Level Bounce")
        assert row["description"] == "new blurb"


def test_delete_strips_tag_keeps_trade():
    tmp, conn = _fresh()
    with tmp:
        db.save_note(
            conn, "kx1", "my note", "[]",
            json.dumps(["Level Bounce", "Value-Area Fade"]),
            json.dumps(["Absorption"]),
        )
        db.delete_taxonomy(conn, "setups", "Level Bounce")
        assert "Level Bounce" not in _names(conn, "setups")
        note = db.get_note(conn, "kx1")
        assert note["note"] == "my note"  # trade note survives
        assert json.loads(note["setups_json"]) == ["Value-Area Fade"]  # only tag stripped
        assert json.loads(note["confluences_json"]) == ["Absorption"]  # other dim untouched


def test_unknown_table_rejected():
    tmp, conn = _fresh()
    with tmp:
        try:
            db.list_taxonomy(conn, "trade_notes")  # not a taxonomy table
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
