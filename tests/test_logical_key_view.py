"""Journaling binds to the logical trade in both views.

``trade_notes.trade_key`` used to be view-dependent: in logical view it hashed
the trade's first lot, in ATAS view it was the lot's own ``dedupe_key[:16]``.
Every saved note matched logical keys and none matched ATAS keys, so switching
the view made every badge disappear. ``logical_trade_key`` is the fix — an ATAS
row resolves to whichever logical trade absorbed its lot.

Run directly:  ``.venv/bin/python tests/test_logical_key_view.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from journal import db, trades  # noqa: E402
from api import deps  # noqa: E402
from api.routers import notes, trades as trades_router  # noqa: E402
from helpers import make_scope  # noqa: E402

DAY = "2026-06-15"
SRC = "session.xlsx"


def _lot(key: str, open_min: int, close_min: int, open_vol: float, pnl: float) -> dict:
    """One ATAS journal lot. ``open_vol`` +1 opens long, the close mirrors it."""
    open_local = f"{DAY}T09:{open_min:02d}:00-04:00"
    close_local = f"{DAY}T09:{close_min:02d}:00-04:00"
    return {
        "dedupe_key": key, "account": "Replay", "instrument": "NQ",
        "open_ts_local": open_local, "close_ts_local": close_local,
        "open_ts_utc": datetime.fromisoformat(open_local).astimezone(timezone.utc).isoformat(),
        "close_ts_utc": datetime.fromisoformat(close_local).astimezone(timezone.utc).isoformat(),
        "open_price": 100.0, "open_volume": open_vol,
        "close_price": 105.0, "close_volume": -open_vol,
        "price_pnl": 5.0, "profit_ticks": 20.0, "pnl": pnl,
        # Every column the inserter names, because `db._insert_ignore` does
        # `r[c] for c in cols` — a missing key is a KeyError, not a NULL, and
        # that strictness is what stops a row builder quietly dropping a column.
        "fees": None,
        "comment": "", "source_file": SRC,
    }


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    # A scale-in: two lots opened before either closes, so the running position
    # never returns to flat between them — one logical trade, two ATAS rows.
    db.insert_journal(conn, [
        _lot("lot_a", 30, 40, 1.0, 100.0),
        _lot("lot_b", 32, 38, 1.0, 60.0),
    ])
    db.mark_imported(conn, SRC, file_mtime=f"{DAY}T20:00:00+00:00")
    db.upsert_session(conn, SRC, "replay", "Replay")
    deps._conn = conn
    return conn


def test_lot_to_logical_map_covers_every_lot_of_a_scale_in():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        jr = db.load_journal(conn)
        logical = trades.build_logical_trades(jr)
        assert len(logical) == 1, "the scale-in should group into one logical trade"

        mapping = trades.lot_to_logical_map(jr)
        assert set(mapping) == {"lot_a", "lot_b"}
        assert len(set(mapping.values())) == 1
        assert mapping["lot_a"] == logical.iloc[0]["trade_key"]


def test_model_assigned_in_logical_view_resolves_in_atas_view():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        model_id = db.create_model(conn, "Test Spring")

        logical = make_scope(view="logical").filtered
        assert len(logical) == 1
        key = logical.iloc[0]["logical_trade_key"]
        assert key == logical.iloc[0]["trade_key"]  # identity in logical view

        notes.put_note(key, notes.NoteIn(note="scaled in", model_id=model_id))

        atas = make_scope(view="atas").filtered
        assert len(atas) == 2, "both lots should surface as ATAS rows"
        # Both rows resolve the same logical key, and so the same model...
        assert set(atas["logical_trade_key"]) == {key}
        assert list(atas["model_id"]) == [model_id, model_id]
        # ...while their own view-local trade_keys differ from it entirely.
        assert key not in set(atas["trade_key"])


def test_note_and_rules_survive_the_view_switch():
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        model_id = db.create_model(conn, "Test Bounce")
        rule_id = db.create_rule(conn, model_id, "Level held on the retest")

        key = make_scope(view="logical").filtered.iloc[0]["logical_trade_key"]
        notes.put_note(
            key,
            notes.NoteIn(note="clean bounce", setups=["Level Bounce"],
                         model_id=model_id, rules_met=[rule_id]),
        )

        for view in ("logical", "atas"):
            scope = make_scope(view=view)
            rows = trades_router.list_trades(scope)
            assert all(r["setups"] == ["Level Bounce"] for r in rows), f"badges lost in {view}"

            detail = trades_router.trade_detail(rows[0]["trade_no"], scope)
            # The journal form's level chips ride the detail; [] here because
            # this trade was never level-tagged, not a missing key.
            assert detail["level_candidates"] == [], f"candidates missing in {view}"
            assert detail["note"] == "clean bounce", f"note lost in {view}"
            assert detail["model_id"] == model_id, f"model lost in {view}"
            assert detail["rules_met"] == [rule_id], f"rule checks lost in {view}"


def test_a_pre_axes_note_row_does_not_500_the_list():
    """A trade_notes row written before the review-axes migration has NULL in
    the new TEXT columns. Once any *other* row holds a string there, pandas
    gives the column its str dtype — under which a NULL cell reads back as
    float NaN, which is truthy AND not valid JSON, so ``r[col] or "[]"``
    hands NaN to json.loads and the whole /trades list 500s. (An all-NULL
    column dodges it — object dtype, honest Nones — which is why a fresh test
    DB never showed this.) Every TEXT read off scope.notes goes through
    ``scope.text_cell``."""
    with tempfile.TemporaryDirectory() as d:
        conn = _setup(Path(d))
        # Two logical trades, so the notes frame has two rows to mix dtypes.
        db.insert_journal(conn, [_lot("lot_c", 45, 50, 1.0, 30.0)])
        sc = make_scope(view="logical")
        k1, k2 = list(sc.filtered["logical_trade_key"])
        notes.put_note(k1, notes.NoteIn(note="n1", tags=["x"]))
        notes.put_note(k2, notes.NoteIn(note="n2", tags=["y"]))
        conn.execute(
            "UPDATE trade_notes SET watched_levels_json=NULL, setups_json=NULL "
            "WHERE trade_key=?",
            (k2,),
        )
        conn.commit()

        nf = make_scope(view="logical").notes
        cell = nf[nf["trade_key"] == k2].iloc[0]["watched_levels_json"]
        assert not isinstance(cell, str), "repro lost: the NULL cell must not be a str"

        rows = trades_router.list_trades(make_scope(view="logical"))
        gap = next(r for r in rows if r["logical_trade_key"] == k2)
        assert gap["setups"] == [] and gap["watched_labels"] == []
        assert gap["reviewed"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
