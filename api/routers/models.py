"""Trading models, their entry-rule checklists, and rule-compliance stats.

Supersedes the setup/confluence pivots. A trade has **exactly one** model (or
none), so the per-model groups are a strict partition of the scope: the sum of
each model's net PnL plus the unassigned bucket equals the scope total. That's
what makes these numbers readable in a way the old n:n badge groups never were.

The question the compliance split answers: *what is my expectancy when I actually
follow model X?* A trade's score is (rules met / the model's active rules); trades
with no recorded checks are "unscored" and sit outside the split.
"""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from journal import db, metrics
from journal.config import BACKTEST_DIR

from .. import deps
from ..scope import Scope, resolve_scope
from ..serialize import sanitize

router = APIRouter()


class ModelIn(BaseModel):
    name: str
    description: str = ""


class ModelUpdate(BaseModel):
    id: int
    name: str | None = None
    description: str | None = None
    archived: bool | None = None
    # Backtest sample-size goal. None = don't touch; 0 = clear the target.
    target_sample: int | None = None


class ModelDelete(BaseModel):
    id: int


class RuleIn(BaseModel):
    label: str


class RuleUpdate(BaseModel):
    id: int
    label: str | None = None
    sort_order: int | None = None
    active: bool | None = None


class RuleDelete(BaseModel):
    id: int


@router.get("/models/list")
def list_models(include_archived: bool = False) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        return {"models": db.list_models(conn, include_archived=include_archived)}


@router.post("/models/create")
def create_model(body: ModelIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    conn = deps.get_conn()
    with deps.db_lock():
        try:
            model_id = db.create_model(conn, body.name, body.description)
        except Exception as exc:  # UNIQUE(name)
            raise HTTPException(status_code=400, detail=f"model exists: {exc}") from exc
        folder = (db.get_model(conn, model_id) or {}).get("folder")
    # The model's backtest drop-box exists from birth, so "export there" is
    # never a step the user has to remember to set up.
    if folder:
        (BACKTEST_DIR / folder).mkdir(parents=True, exist_ok=True)
    return {"ok": True, "id": model_id, "folder": folder}


@router.post("/models/update")
def update_model(body: ModelUpdate) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        current = db.get_model(conn, body.id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"no model {body.id}")
        # A rename moves the drop-box with it: the folder is keyed to the model
        # id in the DB, so nothing breaks mid-rename, but keeping the slug in
        # step with the name is what makes the folder recognizable in ATAS's
        # save dialog.
        new_folder = None
        if body.name is not None and body.name.strip() != current["name"]:
            new_folder = db.unique_model_folder(conn, body.name.strip(), exclude_id=body.id)
        db.update_model(
            conn, body.id, body.name, body.description, body.archived,
            folder=new_folder,
            target_sample=body.target_sample or None,
            clear_target=body.target_sample == 0,
        )
    if new_folder and new_folder != current["folder"]:
        old_dir = BACKTEST_DIR / current["folder"] if current["folder"] else None
        new_dir = BACKTEST_DIR / new_folder
        if old_dir is not None and old_dir.is_dir() and not new_dir.exists():
            old_dir.rename(new_dir)
        else:
            new_dir.mkdir(parents=True, exist_ok=True)
        # Sessions are keyed by imports-relative path, so a moved drop-box must
        # take its already-imported sessions with it. Skipping this strands them
        # under the old path and the watcher re-imports the moved files as new,
        # empty sessions (their trades dedupe on content and never move).
        if current["folder"]:
            with deps.db_lock():
                db.rekey_source_prefix(
                    conn,
                    f"{BACKTEST_DIR.name}/{current['folder']}",
                    f"{BACKTEST_DIR.name}/{new_folder}",
                )
    return {"ok": True}


@router.post("/models/delete")
def delete_model(body: ModelDelete) -> dict:
    """Soft-archive. Trades already assigned keep resolving to it, so historical
    per-model stats don't reshuffle when a model leaves the picker."""
    conn = deps.get_conn()
    with deps.db_lock():
        db.archive_model(conn, body.id)
    return {"ok": True}


@router.get("/models/{model_id}/rules")
def list_rules(model_id: int, include_inactive: bool = False) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        return {"rules": db.list_rules(conn, model_id, include_inactive=include_inactive)}


@router.post("/models/{model_id}/rules")
def create_rule(model_id: int, body: RuleIn) -> dict:
    if not body.label.strip():
        raise HTTPException(status_code=400, detail="label is required")
    conn = deps.get_conn()
    with deps.db_lock():
        rule_id = db.create_rule(conn, model_id, body.label)
    return {"ok": True, "id": rule_id}


@router.post("/models/rules/update")
def update_rule(body: RuleUpdate) -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        db.update_rule(conn, body.id, body.label, body.sort_order, body.active)
    return {"ok": True}


@router.post("/models/rules/delete")
def delete_rule(body: RuleDelete) -> dict:
    """Soft-delete (``active = 0``): a trade scored 3/4 against the old checklist
    must keep reading 3/4, so the rule's rows survive."""
    conn = deps.get_conn()
    with deps.db_lock():
        db.retire_rule(conn, body.id)
    return {"ok": True}


def _compliance_split(
    group: pd.DataFrame, rule_ids: list[int], checks: dict[str, dict[int, bool]]
) -> dict:
    """Bucket a model's trades by how much of its checklist they satisfied.

    ``followed`` = every active rule met, ``partial`` = some, ``broke`` = none.
    A trade with no recorded checks was never reviewed against the checklist and
    lands in ``unscored`` — counting it as "broke" would slander it.
    """
    if not rule_ids:
        return {"rules": 0, "buckets": [], "unscored": len(group)}

    def score(key: str) -> float | None:
        c = checks.get(key)
        if not c:
            return None
        met = sum(1 for rid in rule_ids if c.get(rid))
        return met / len(rule_ids)

    scores = [score(k) for k in group["logical_trade_key"]]
    unscored = sum(1 for s in scores if s is None)
    named = (
        ("followed", lambda s: s == 1.0),
        ("partial", lambda s: 0.0 < s < 1.0),
        ("broke", lambda s: s == 0.0),
    )
    buckets = []
    for label, pred in named:
        mask = [s is not None and pred(s) for s in scores]
        sub = group[pd.Series(mask, index=group.index)]
        if sub.empty:
            continue
        m = metrics.compute_metrics(sub)
        buckets.append({
            "label": label,
            "trades": m["trades"],
            "win_rate": m["win_rate"],
            "expectancy": m["expectancy"],
            "net_pnl": m["net_pnl"],
        })
    return {"rules": len(rule_ids), "buckets": buckets, "unscored": unscored}


def _per_rule(
    group: pd.DataFrame, rules: list[dict], checks: dict[str, dict[int, bool]]
) -> list[dict]:
    """Per rule: expectancy on the model's trades that met it vs those that didn't.

    Only scored trades participate — an unreviewed trade says nothing either way.
    """
    out = []
    for rule in rules:
        rid = rule["id"]
        met_mask, miss_mask = [], []
        for key in group["logical_trade_key"]:
            c = checks.get(key)
            met_mask.append(bool(c) and bool(c.get(rid)))
            miss_mask.append(bool(c) and rid in c and not c.get(rid))
        met = group[pd.Series(met_mask, index=group.index)]
        miss = group[pd.Series(miss_mask, index=group.index)]
        m_met = metrics.compute_metrics(met)
        m_miss = metrics.compute_metrics(miss)
        out.append({
            "id": rid,
            "label": rule["label"],
            "met_trades": m_met["trades"],
            "met_expectancy": m_met.get("expectancy", 0.0),
            "met_win_rate": m_met.get("win_rate", 0.0),
            "met_net_pnl": m_met.get("net_pnl", 0.0),
            "missed_trades": m_miss["trades"],
            "missed_expectancy": m_miss.get("expectancy", 0.0),
            "missed_win_rate": m_miss.get("win_rate", 0.0),
            "missed_net_pnl": m_miss.get("net_pnl", 0.0),
        })
    return out


@router.get("/models/stats")
def model_stats(scope: Scope = Depends(resolve_scope)) -> dict:
    """Per-model metrics + compliance split over the current scope.

    Grouped on the row's *effective* model (own binding, else the session's model
    when it's a backtest), so ``sum(models) + unassigned == scope total``.
    """
    df = scope.filtered
    conn = deps.get_conn()
    with deps.db_lock():
        catalog = db.list_models(conn, include_archived=True)
        checks = db.all_rule_checks(conn)

    if df is None or df.empty:
        return {"models": [], "unassigned": metrics.compute_metrics(df), "total": {"trades": 0}}

    total = metrics.compute_metrics(df)
    assigned = df["model_id"].notna()
    unassigned = metrics.compute_metrics(df[~assigned])

    out = []
    for model in catalog:
        group = df[df["model_id"] == model["id"]]
        if group.empty:
            continue
        rules = model["rules"]
        out.append({
            "id": model["id"],
            "name": model["name"],
            "description": model["description"],
            "archived": model["archived"],
            "metrics": metrics.compute_metrics(group),
            "compliance": _compliance_split(group, [r["id"] for r in rules], checks),
            "rules": _per_rule(group, rules, checks),
        })
    out.sort(key=lambda m: m["metrics"].get("net_pnl", 0) or 0, reverse=True)
    return sanitize({"models": out, "unassigned": unassigned, "total": total})
