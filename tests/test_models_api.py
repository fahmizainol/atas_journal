"""Models + rules CRUD, rule compliance, and the partition invariant.

The reason models replaced setups: a trade carried 0..n setups, so the per-setup
groups overlapped and their PnL didn't add up to anything. A trade has exactly
one model or none, so the groups partition the scope — which is the property
``test_per_model_pnl_partitions_the_scope`` pins down.

Run directly:  ``.venv/bin/python tests/test_models_api.py``
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

from journal import db  # noqa: E402
from api import deps  # noqa: E402
from api.routers import models as models_router  # noqa: E402
from api.routers import notes  # noqa: E402
from helpers import make_scope  # noqa: E402
from test_scope_eviction import _row  # noqa: E402


def _setup(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    deps._conn = conn
    return conn


def _with_trades(tmp: Path):
    conn = db.connect(tmp / "test.db")
    db.init_db(conn)
    db.insert_journal(conn, [
        _row("s.xlsx", "Replay", 300.0, 30),
        _row("s.xlsx", "Replay", -100.0, 40),
        _row("s.xlsx", "Replay", 50.0, 50),
    ])
    db.mark_imported(conn, "s.xlsx", file_mtime="2026-06-15T20:00:00+00:00")
    db.upsert_session(conn, "s.xlsx", "replay", "Replay")
    deps._conn = conn
    return conn


def test_model_and_rule_crud():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        created = models_router.create_model(models_router.ModelIn(name="Sweep", description="x"))
        mid = created["id"]
        assert any(m["name"] == "Sweep" for m in models_router.list_models()["models"])

        r1 = models_router.create_rule(mid, models_router.RuleIn(label="Swept a pool"))["id"]
        r2 = models_router.create_rule(mid, models_router.RuleIn(label="Reclaimed"))["id"]
        rules = models_router.list_rules(mid)["rules"]
        assert [r["label"] for r in rules] == ["Swept a pool", "Reclaimed"]  # sort_order

        models_router.update_rule(models_router.RuleUpdate(id=r2, label="Reclaimed the level"))
        assert models_router.list_rules(mid)["rules"][1]["label"] == "Reclaimed the level"

        # Retiring a rule is a soft delete: it leaves the checklist, not the DB.
        models_router.delete_rule(models_router.RuleDelete(id=r1))
        assert [r["id"] for r in models_router.list_rules(mid)["rules"]] == [r2]
        assert len(models_router.list_rules(mid, include_inactive=True)["rules"]) == 2

        # So is archiving a model.
        models_router.delete_model(models_router.ModelDelete(id=mid))
        assert not any(m["id"] == mid for m in models_router.list_models()["models"])
        assert any(
            m["id"] == mid for m in models_router.list_models(include_archived=True)["models"]
        )


def test_create_rejects_blank_and_duplicate():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        for body in (models_router.ModelIn(name="  "), models_router.ModelIn(name="Level Bounce")):
            try:
                models_router.create_model(body)
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 400


def test_trade_model_stays_single_valued():
    with tempfile.TemporaryDirectory() as d:
        conn = _with_trades(Path(d))
        a = db.create_model(conn, "A")
        b = db.create_model(conn, "B")
        key = make_scope().filtered.iloc[0]["logical_trade_key"]

        db.set_trade_model(conn, key, a)
        db.set_trade_model(conn, key, b)
        assert db.get_trade_model(conn, key) == b
        rows = conn.execute(
            "SELECT COUNT(*) FROM trade_model WHERE trade_key = ?", (key,)
        ).fetchone()[0]
        assert rows == 1


def test_switching_model_sweeps_the_old_models_rule_checks():
    with tempfile.TemporaryDirectory() as d:
        conn = _with_trades(Path(d))
        a, b = db.create_model(conn, "A"), db.create_model(conn, "B")
        ra = db.create_rule(conn, a, "A rule")
        rb = db.create_rule(conn, b, "B rule")
        key = make_scope().filtered.iloc[0]["logical_trade_key"]

        notes.put_note(key, notes.NoteIn(model_id=a, rules_met=[ra]))
        assert db.get_rule_checks(conn, key) == {ra: True}

        notes.put_note(key, notes.NoteIn(model_id=b, rules_met=[rb]))
        # A's check is gone — left behind it would inflate B's denominator.
        assert db.get_rule_checks(conn, key) == {rb: True}


def test_resaving_a_note_keeps_checks_against_a_retired_rule():
    """Retiring a rule is a soft delete. Re-journaling the trade must not quietly
    erase the score it earned under the old checklist."""
    with tempfile.TemporaryDirectory() as d:
        conn = _with_trades(Path(d))
        mid = db.create_model(conn, "M")
        r1 = db.create_rule(conn, mid, "rule 1")
        r2 = db.create_rule(conn, mid, "rule 2")
        key = make_scope().filtered.iloc[0]["logical_trade_key"]

        notes.put_note(key, notes.NoteIn(model_id=mid, rules_met=[r1, r2]))
        db.retire_rule(conn, r1)

        notes.put_note(key, notes.NoteIn(note="edited later", model_id=mid, rules_met=[r2]))
        checks = db.get_rule_checks(conn, key)
        assert checks[r1] is True, "the retired rule's check was erased"
        assert checks[r2] is True


def test_compliance_split_and_unscored_bucket():
    with tempfile.TemporaryDirectory() as d:
        conn = _with_trades(Path(d))
        mid = db.create_model(conn, "M")
        r1 = db.create_rule(conn, mid, "rule 1")
        r2 = db.create_rule(conn, mid, "rule 2")
        keys = list(make_scope().filtered["logical_trade_key"])

        notes.put_note(keys[0], notes.NoteIn(model_id=mid, rules_met=[r1, r2]))  # followed
        notes.put_note(keys[1], notes.NoteIn(model_id=mid, rules_met=[r1]))      # partial
        db.set_trade_model(conn, keys[2], mid)  # assigned, never scored

        stats = models_router.model_stats(make_scope())
        m = next(x for x in stats["models"] if x["id"] == mid)
        assert m["metrics"]["trades"] == 3
        buckets = {b["label"]: b for b in m["compliance"]["buckets"]}
        assert buckets["followed"]["trades"] == 1
        assert buckets["followed"]["net_pnl"] == 300.0
        assert buckets["partial"]["trades"] == 1
        assert buckets["partial"]["net_pnl"] == -100.0
        assert "broke" not in buckets
        assert m["compliance"]["unscored"] == 1

        # Per rule: rule 2 was met once (+300) and missed once (-100).
        by_rule = {r["id"]: r for r in m["rules"]}
        assert by_rule[r2]["met_trades"] == 1 and by_rule[r2]["met_net_pnl"] == 300.0
        assert by_rule[r2]["missed_trades"] == 1 and by_rule[r2]["missed_net_pnl"] == -100.0
        assert by_rule[r1]["met_trades"] == 2  # unscored trade contributes to neither


def test_per_model_pnl_partitions_the_scope():
    with tempfile.TemporaryDirectory() as d:
        conn = _with_trades(Path(d))
        a, b = db.create_model(conn, "A"), db.create_model(conn, "B")
        keys = list(make_scope().filtered["logical_trade_key"])
        db.set_trade_model(conn, keys[0], a)
        db.set_trade_model(conn, keys[1], b)
        # keys[2] deliberately left off-model.

        stats = models_router.model_stats(make_scope())
        per_model = sum(m["metrics"]["net_pnl"] for m in stats["models"])
        assert per_model + stats["unassigned"]["net_pnl"] == stats["total"]["net_pnl"]
        assert stats["total"]["net_pnl"] == 250.0
        assert stats["unassigned"]["trades"] == 1
        assert sum(m["metrics"]["trades"] for m in stats["models"]) == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
