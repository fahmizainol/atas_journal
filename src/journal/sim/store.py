"""Sim run artifacts on disk.

Deliberately NOT in journal.db. That database is the user's real trading record;
synthetic trades must never be able to leak into it and contaminate live stats.
A sim run is a disposable artifact — delete the folder and it is gone.

    data/sims/<strategy_slug>/
        strategy.json                 # mutable: {"baseline_run_id": ...}
        <run_id>/
            config.json               # immutable — participates in run_id
            state.json                # lifecycle: status/progress/created_at/engine_version
            meta.json                 # mutable: label + notes, editable after the fact
            trades.parquet            # written when the run completes
            vetoed.parquet            # entries confluence gates rejected (if any)
            metrics.json
            regime_pnl.json           # derived: the regime-vs-P&L study (journal.sim.regime_pnl)

Every file above except regime_pnl.json is *primary* — it records what the engine
did. regime_pnl.json is derived, and carries the versions of the definitions it
was computed under so a stale one is recomputed rather than served. It is written
anyway (rather than left to the browser to recompute on each mount) because a
study nobody can read from outside the UI is one an LLM cannot read at all.

Runs are immutable: run_id hashes the config *and* the strategy's engine
version, so re-running an old config on newer code is a new artifact, never an
overwrite of the numbers you compared against last week. Labels and notes live
in meta.json precisely so renaming a run cannot change its identity.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timezone

import pandas as pd

from ..config import DATA_DIR
from . import regime, regime_pnl, registry, schema
from .rules import SimConfig

SIMS_DIR = DATA_DIR / "sims"


def run_id(cfg, version: str) -> str:
    """Stable id for (config, engine version). Same rules on the same code
    always land on the same folder; either changing gives a fresh one."""
    blob = json.dumps({"config": cfg.to_json(), "version": version}, sort_keys=True)
    h = hashlib.sha1(blob.encode()).hexdigest()[:8]
    return f"{cfg.start_date:%Y%m%d}-{cfg.end_date:%Y%m%d}-v{version}-{h}"


def _dir(slug: str, rid: str):
    return SIMS_DIR / slug / rid


def _json_default(o):
    if isinstance(o, (date, datetime, pd.Timestamp)):
        return o.isoformat()
    raise TypeError(type(o))


def _write(path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, default=_json_default))


def _read(path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


# --- run lifecycle ----------------------------------------------------------

def init_run(slug: str, cfg, version: str, sessions_total: int) -> str:
    """Create the run folder in 'running' state. The id is known before a single
    tick is simulated, so the UI can poll it from the moment the POST returns."""
    rid = run_id(cfg, version)
    d = _dir(slug, rid)
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "config.json", cfg.to_json())
    _write(d / "state.json", {
        "status": "running",
        "sessions_done": 0,
        "sessions_total": sessions_total,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": version,
    })
    if not (d / "meta.json").exists():
        _write(d / "meta.json", {"label": "", "notes": ""})
    return rid


def update_progress(slug: str, rid: str, sessions_done: int) -> None:
    d = _dir(slug, rid)
    st = _read(d / "state.json") or {}
    st["sessions_done"] = sessions_done
    _write(d / "state.json", st)


def finish_run(slug: str, rid: str, trades: pd.DataFrame,
               vetoed: pd.DataFrame, metrics: dict) -> None:
    d = _dir(slug, rid)
    _write(d / "metrics.json", metrics)
    trades.to_parquet(d / "trades.parquet", index=False)
    if vetoed is not None and not vetoed.empty:
        vetoed.to_parquet(d / "vetoed.parquet", index=False)
    st = _read(d / "state.json") or {}
    st["status"] = "done"
    st["sessions_done"] = st.get("sessions_total", 0)
    _write(d / "state.json", st)


def fail_run(slug: str, rid: str, error: str) -> None:
    d = _dir(slug, rid)
    st = _read(d / "state.json") or {}
    st.update(status="error", error=error)
    _write(d / "state.json", st)


def delete_run(slug: str, rid: str) -> bool:
    d = _dir(slug, rid)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    if baseline(slug) == rid:
        set_baseline(slug, None)
    return True


# --- reads ------------------------------------------------------------------

def read_run(slug: str, rid: str) -> tuple[dict, pd.DataFrame, dict] | None:
    """(config, trades, metrics) for a *completed* run, else None."""
    d = _dir(slug, rid)
    state = _read(d / "state.json")
    if state is None or state.get("status") != "done":
        return None
    cfg = _read(d / "config.json")
    metrics = _read(d / "metrics.json") or {}
    tp = d / "trades.parquet"
    trades = pd.read_parquet(tp) if tp.exists() else pd.DataFrame()
    return cfg, trades, metrics


def read_vetoed(slug: str, rid: str) -> pd.DataFrame:
    vp = _dir(slug, rid) / "vetoed.parquet"
    return pd.read_parquet(vp) if vp.exists() else pd.DataFrame()


def write_regime_pnl(slug: str, rid: str, study: dict) -> None:
    _write(_dir(slug, rid) / "regime_pnl.json", study)


def read_regime_pnl(slug: str, rid: str) -> dict | None:
    """The regime-vs-P&L study, if one was snapshotted under the *current*
    definitions.

    A snapshot written before a bump to either version is a miss, not a fallback:
    it is a table of numbers that mean something else now, and serving it would be
    worse than recomputing — the numbers would look fine.
    """
    d = _read(_dir(slug, rid) / "regime_pnl.json")
    if d is None:
        return None
    if (d.get("stats_version") != regime_pnl.STATS_VERSION
            or d.get("regime_version") != regime.REGIME_VERSION):
        return None
    return d


def read_meta(slug: str, rid: str) -> dict:
    return _read(_dir(slug, rid) / "meta.json") or {"label": "", "notes": ""}


def read_state(slug: str, rid: str) -> dict | None:
    return _read(_dir(slug, rid) / "state.json")


def write_meta(slug: str, rid: str, *, label: str | None = None,
               notes: str | None = None) -> dict:
    m = read_meta(slug, rid)
    if label is not None:
        m["label"] = label
    if notes is not None:
        m["notes"] = notes
    _write(_dir(slug, rid) / "meta.json", m)
    return m


def list_runs(slug: str) -> list[dict]:
    """Every run of a strategy — running and failed ones included, so the UI
    can show progress and errors in the same list. Newest first."""
    sd = SIMS_DIR / slug
    if not sd.exists():
        return []
    out = []
    for d in sd.iterdir():
        if not d.is_dir():
            continue
        state = _read(d / "state.json")
        cfg = _read(d / "config.json")
        if state is None or cfg is None:
            continue
        metrics = _read(d / "metrics.json") or {}
        n = metrics.get("trades")
        if n is None and (d / "trades.parquet").exists():
            n = int(len(pd.read_parquet(d / "trades.parquet")))
        vetoed = _read_vetoed_count(d)
        out.append({
            "run_id": d.name, "config": cfg, "metrics": metrics,
            "trades": int(n or 0), "vetoed": vetoed,
            "meta": _read(d / "meta.json") or {"label": "", "notes": ""},
            "state": state,
        })
    out.sort(key=lambda r: r["state"].get("created_at", ""), reverse=True)
    return out


def _read_vetoed_count(d) -> int:
    vp = d / "vetoed.parquet"
    return int(len(pd.read_parquet(vp))) if vp.exists() else 0


# --- baseline pin -----------------------------------------------------------

def baseline(slug: str) -> str | None:
    s = _read(SIMS_DIR / slug / "strategy.json") or {}
    rid = s.get("baseline_run_id")
    return rid if rid and _dir(slug, rid).is_dir() else None


def set_baseline(slug: str, rid: str | None) -> None:
    sd = SIMS_DIR / slug
    sd.mkdir(parents=True, exist_ok=True)
    _write(sd / "strategy.json", {"baseline_run_id": rid})


def maybe_autopin_baseline(slug: str, rid: str) -> None:
    """First completed run of a strategy becomes its baseline — there is
    nothing else to compare against yet. Re-pinning later is explicit."""
    if baseline(slug) is None:
        set_baseline(slug, rid)


# --- config (de)serialization ------------------------------------------------

def config_from_json(d: dict, config_cls: type = SimConfig):
    """The only door into a config from JSON — for posted configs and for
    stored config.json alike. ``config_cls`` comes from the strategy's registry
    entry; the default keeps every SimConfig caller (and the legacy migration)
    reading as before.

    It goes through schema.parse, which coerces every value to its declared type
    before anything hashes it. That matters more than it looks: run_id() sha1s the
    serialized config, so ``7`` and ``7.0`` would otherwise be two different runs
    of identical rules. Missing keys take their default, which is what lets an
    artifact written before a knob existed still load.
    """
    return schema.parse(d, config_cls)


# --- legacy migration ---------------------------------------------------------

LEGACY_STRATEGY = "vwap-upper-band-bounce"


def ensure_migrated() -> None:
    """Move pre-registry runs (data/sims/<run_id>/ with config.json directly
    inside) under the VWAP upper-band-bounce strategy they all belonged to.
    Their free-text config label becomes the run's meta label. Idempotent."""
    if not SIMS_DIR.exists():
        return
    legacy = [d for d in SIMS_DIR.iterdir()
              if d.is_dir() and (d / "config.json").exists()]
    if not legacy:
        return

    strat = registry.get(LEGACY_STRATEGY)
    for old in sorted(legacy):
        cfg_json = json.loads((old / "config.json").read_text())
        label = cfg_json.pop("label", "")
        cfg = config_from_json(cfg_json)
        rid = run_id(cfg, strat.version)
        nd = _dir(LEGACY_STRATEGY, rid)
        nd.mkdir(parents=True, exist_ok=True)
        _write(nd / "config.json", cfg.to_json())
        for f in ("trades.parquet", "metrics.json"):
            if (old / f).exists():
                shutil.move(str(old / f), str(nd / f))
        from . import ticks as tickmod
        _write(nd / "meta.json", {"label": label, "notes": ""})
        _write(nd / "state.json", {
            "status": "done",
            "sessions_done": len(tickmod.session_dates(cfg.start_date, cfg.end_date)),
            "sessions_total": len(tickmod.session_dates(cfg.start_date, cfg.end_date)),
            "error": None,
            "created_at": datetime.fromtimestamp(
                old.stat().st_mtime, tz=timezone.utc).isoformat(),
            "engine_version": strat.version,
        })
        shutil.rmtree(old)

    runs = list_runs(LEGACY_STRATEGY)
    if runs and baseline(LEGACY_STRATEGY) is None:
        # Oldest first, so "first run becomes baseline" holds for migrations too.
        set_baseline(LEGACY_STRATEGY, runs[-1]["run_id"])
