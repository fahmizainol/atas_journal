"""Strategies: the automated strategy research workbench.

Each strategy is a coded trading idea (see journal.sim.registry); its runs are
immutable config+engine-version artifacts under data/sims/<slug>/. Not to be
confused with ``backtests.py`` (manually replayed ATAS sessions) or
``models.py`` (the *manual* trading models a real trade binds to). These runs
are machine-generated and never touch journal.db.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from journal import edges
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS
from journal.sim import regime_pnl, registry, runner, schema, store
from journal.sim import ticks as tickmod

from .. import sim_charts
from ..serialize import records

router = APIRouter()

EDGE_COLS = ["bucket", "trades", "net_pnl", "win_rate", "expectancy", "avg_r"]

# The cuts a run is served in. Order is the order they are rendered in, and the
# count is the family the luck bar corrects for — adding a seventh cut here makes
# the bar for all of them stricter, which is the point of a Bonferroni.
TRADED_CUTS = ("by_hour_et", "by_weekday", "by_hold_time", "by_direction",
               "by_exit_reason", "by_band_width")
VETOED_CUTS = TRADED_CUTS + ("by_gate",)

TRADE_COLS = [
    "trade_no", "session", "direction", "entry_ts_local", "exit_ts_local",
    "avg_entry", "avg_exit", "stop_price", "final_stop_price", "target_price", "exit_reason",
    "points", "r_multiple", "band_width_ticks", "duration_s",
    "gross_pnl", "commission", "net_pnl",
]
VETOED_COLS = TRADE_COLS + ["gate"]


def _tz(tz: str | None):
    return DISPLAY_TZS.get(tz or DEFAULT_DISPLAY_TZ, DISPLAY_TZS[DEFAULT_DISPLAY_TZ])


def _strategy(slug: str) -> registry.Strategy:
    try:
        return registry.get(slug)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _parse_config(strat: registry.Strategy, config: dict):
    """User JSON -> a canonical, validated config of the strategy's class, or a 400.

    Absent keys take their defaults; unknown keys, out-of-range values and unknown
    confluences are hard errors. Every one of those is a config the engine would
    otherwise accept and then lie about — a typo'd knob silently no-ops and
    masquerades as an experiment, and `stop_ticks: 0` divides by zero halfway
    through a background thread, which surfaces as a failed run rather than a bad
    request. The rules live in journal.sim.schema, next to the knobs themselves.
    """
    try:
        cfg = store.config_from_json(config, strat.config_cls)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"bad config: {exc}") from exc
    try:
        from journal.sim import confluences as confmod

        confmod.validate(cfg, strat.confluences)
    except ValueError as exc:
        raise HTTPException(400, f"bad config: {exc}") from exc
    # After validation, never before: a gate switched off is dropped so it hashes
    # as the absence it simulates as, but a *typo'd* gate switched off must still
    # be a 400 rather than a section quietly swept away.
    return schema.canonicalize(cfg)


def _strategy_summary(strat: registry.Strategy) -> dict:
    runs = store.list_runs(strat.slug)
    base_id = store.baseline(strat.slug)
    base = next((r for r in runs if r["run_id"] == base_id), None)
    return {
        "slug": strat.slug,
        "name": strat.name,
        "description": strat.description,
        "version": strat.version,
        "confluences": list(strat.confluences),
        # "rth" | "globex" — a globex run also pays for the overnight tick pull,
        # so the run form warns before the POST rather than after the bill.
        "session": strat.session,
        "run_count": len(runs),
        "baseline_run_id": base_id,
        "baseline_metrics": base["metrics"] if base else None,
    }


@router.get("/strategies")
def list_strategies() -> dict:
    store.ensure_migrated()
    return {"strategies": [_strategy_summary(s) for s in registry.STRATEGIES.values()]}


@router.get("/strategies/{slug}")
def strategy_detail(slug: str) -> dict:
    store.ensure_migrated()
    strat = _strategy(slug)
    return {
        **_strategy_summary(strat),
        "default_config": strat.config_cls().to_json(),
        # The run form renders itself from this — groups, widgets, bounds, and the
        # gate sections this strategy supports. Served rather than hard-coded in
        # the browser so a new knob can't reach the engine without reaching the UI.
        "config_schema": schema.config_schema(strat.confluences, strat.config_cls),
        "runs": store.list_runs(slug),
    }


@router.get("/strategies/{slug}/runs/{run_id}")
def run_detail(slug: str, run_id: str) -> dict:
    strat = _strategy(slug)
    state = store.read_state(slug, run_id)
    if state is None:
        raise HTTPException(404, f"No run {run_id}")
    if state.get("status") != "done":
        return {"run_id": run_id, "state": state,
                "meta": store.read_meta(slug, run_id)}
    cfg, trades, m = store.read_run(slug, run_id)
    rows = trades[[c for c in TRADE_COLS if c in trades.columns]].to_dict("records")
    vetoed = store.read_vetoed(slug, run_id)
    vetoed_rows = vetoed[[c for c in VETOED_COLS if c in vetoed.columns]].to_dict("records")
    # Trading days from the config window (not from the trades), so a day with
    # zero trades still gets a tab in the by-day view.
    c = store.config_from_json(cfg, strat.config_cls)
    days = [d.isoformat() for d in tickmod.session_dates(c.start_date, c.end_date)]
    return {"run_id": run_id, "config": cfg, "metrics": m, "trades": rows,
            "vetoed_trades": vetoed_rows, "session_days": days,
            "meta": store.read_meta(slug, run_id), "state": state}


class ConfigIn(BaseModel):
    config: dict = {}
    # Optional name for the run, set at creation. The form suggests the diff
    # against baseline ("stop 50, trail 75"), which beats a list of hashes when
    # you are ten experiments deep. Ignored by /preflight.
    label: str | None = None
    # Buy the RTH segment only, skipping the overnight a run otherwise pulls for
    # its charts. Deliberately NOT part of `config`: it buys data, it does not
    # change a rule, so it must not enter the run's identity hash — the same knobs
    # over the same window are the same run whether or not you also paid for the
    # night. A globex strategy ignores it; it cannot simulate without the night.
    rth_only: bool = False


def _fetch_overnight(strat: registry.Strategy, body: ConfigIn) -> bool:
    return strat.session == "globex" or not body.rth_only


@router.post("/strategies/{slug}/preflight")
def preflight(slug: str, body: ConfigIn) -> dict:
    """Spend guard: how many sessions the window needs and how many would be
    paid Databento pulls. Call before POSTing the run; nothing is fetched."""
    strat = _strategy(slug)
    cfg = _parse_config(strat, body.config)
    pf = runner.preflight(cfg, strat.session, _fetch_overnight(strat, body))
    rid = store.run_id(cfg, strat.version)
    return {**pf, "run_id": rid, "exists": store.read_state(slug, rid) is not None}


@router.post("/strategies/{slug}/runs")
def create_run(slug: str, body: ConfigIn, background: BackgroundTasks) -> dict:
    strat = _strategy(slug)
    cfg = _parse_config(strat, body.config)
    rid = store.run_id(cfg, strat.version)
    state = store.read_state(slug, rid)
    if state is not None:
        if state.get("status") == "running":
            raise HTTPException(409, f"run {rid} is already in progress")
        if state.get("status") == "done":
            # Identical config on identical code — the artifact already exists.
            return {"run_id": rid, "status": "done", "already_existed": True}
        store.delete_run(slug, rid)  # failed: clear and retry

    try:
        rid = runner.start(strat, cfg)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.label:
        store.write_meta(slug, rid, label=body.label)
    background.add_task(runner.run_to_completion, strat, cfg, rid,
                        fetch_overnight=_fetch_overnight(strat, body))
    return {"run_id": rid, "status": "running", "already_existed": False}


class MetaIn(BaseModel):
    label: str | None = None
    notes: str | None = None


@router.patch("/strategies/{slug}/runs/{run_id}")
def patch_meta(slug: str, run_id: str, body: MetaIn) -> dict:
    _strategy(slug)
    if store.read_state(slug, run_id) is None:
        raise HTTPException(404, f"No run {run_id}")
    return store.write_meta(slug, run_id, label=body.label, notes=body.notes)


@router.delete("/strategies/{slug}/runs/{run_id}")
def delete_run(slug: str, run_id: str) -> dict:
    _strategy(slug)
    state = store.read_state(slug, run_id)
    if state is None:
        raise HTTPException(404, f"No run {run_id}")
    if state.get("status") == "running":
        raise HTTPException(409, "run is still in progress")
    store.delete_run(slug, run_id)
    return {"ok": True}


class BaselineIn(BaseModel):
    run_id: str


@router.put("/strategies/{slug}/baseline")
def pin_baseline(slug: str, body: BaselineIn) -> dict:
    _strategy(slug)
    state = store.read_state(slug, body.run_id)
    if state is None:
        raise HTTPException(404, f"No run {body.run_id}")
    if state.get("status") != "done":
        raise HTTPException(409, "only a completed run can be the baseline")
    store.set_baseline(slug, body.run_id)
    return {"baseline_run_id": body.run_id}


@router.post("/strategies/{slug}/rerun-baseline")
def rerun_baseline(slug: str, background: BackgroundTasks) -> dict:
    """Re-run the baseline's exact config on the *current* engine version — the
    one-click 'is my champion still the champion after that code change' action."""
    strat = _strategy(slug)
    base_id = store.baseline(slug)
    if base_id is None:
        raise HTTPException(404, "no baseline pinned")
    r = store.read_run(slug, base_id)
    if r is None:
        raise HTTPException(409, "baseline run is not readable")
    cfg = store.config_from_json(r[0], strat.config_cls)
    return create_run(slug, ConfigIn(config=cfg.to_json()), background)


@router.get("/strategies/{slug}/runs/{run_id}/regime-pnl")
def regime_pnl_study(slug: str, run_id: str, refresh: bool = Query(False)) -> dict:
    """Which regime KPIs track this run's P&L, and which only look like they do.

    Served from the snapshot the run wrote at completion; recomputed (and
    re-snapshotted) when there isn't one, when it was written under older
    definitions, or on ``?refresh=1`` — the artifact joins against the *regime*
    cache, so a day whose ticks were bought after the run finished is a day the
    snapshot has never seen.

    Reads the tick cache only, like every other regime read: a session with no
    ticks on disk is reported as skipped, never fetched. A GET must not spend
    money at Databento.
    """
    strat = _strategy(slug)
    if not refresh and (cached := store.read_regime_pnl(slug, run_id)) is not None:
        return cached

    r = store.read_run(slug, run_id)
    if r is None:
        raise HTTPException(404, f"No completed run {run_id}")
    cfg, trades, _ = r
    c = store.config_from_json(cfg, strat.config_cls)
    study = regime_pnl.study(c.contract, c.start_date, c.end_date, trades)
    store.write_regime_pnl(slug, run_id, study)
    return study


@router.get("/strategies/{slug}/runs/{run_id}/edges")
def run_edges(slug: str, run_id: str, compare: str | None = Query(None)) -> dict:
    """The behavioral-edge cuts (/edges) run over one simulation's trades.

    The same breakdowns the journal's Edges tab computes over the real book —
    and computed by the same functions, because the engine's trade frame already
    carries the columns they read (entry_ts_utc/_local, duration_s, direction,
    net_pnl). No display-tz cut here: a sim's ``entry_ts_local`` is always ET, so
    the by-hour table would only repeat the session blocks in a coarser grid.

    Three books, not one. The trades the run took are the obvious cut; the ones
    its gates *vetoed* are the counterfactual, and they are the only way to ask
    whether a gate cut the right bucket or merely cut trades and got lucky on the
    total. ``all`` is the run the gates were never in.

    ``compare`` names another run to read the same cuts off, so each bucket can
    print what the knob change did *to that bucket* rather than only to the total.
    Defaults to the strategy's pinned baseline; pass an empty string for none.
    """
    strat = _strategy(slug)
    r = store.read_run(slug, run_id)
    if r is None:
        raise HTTPException(404, f"No completed run {run_id}")
    traded = r[1]
    vetoed = store.read_vetoed(slug, run_id)

    ref_id = store.baseline(slug) if compare is None else (compare or None)
    reference = None
    if ref_id and ref_id != run_id and (ref := store.read_run(slug, ref_id)) is not None:
        cfg, ref_cfg = (store.config_from_json(r[0], strat.config_cls),
                        store.config_from_json(ref[0], strat.config_cls))
        reference = {
            "run_id": ref_id,
            "label": store.read_meta(slug, ref_id).get("label") or "",
            "is_baseline": ref_id == store.baseline(slug),
            "start": ref_cfg.start_date.isoformat(),
            "end": ref_cfg.end_date.isoformat(),
            # Whether the two runs even saw the same sessions. If they didn't, a
            # bucket's Δ net is mostly a count of days the reference had and this
            # run didn't — the per-trade columns (avg R, win rate, expectancy) are
            # the ones that still mean something, and the panel says so rather than
            # printing an impressive number that is really a longer window.
            "same_window": (ref_cfg.start_date == cfg.start_date
                            and ref_cfg.end_date == cfg.end_date),
            # No luck column on the reference: it is a yardstick, not a claim. The
            # permutation test it would carry is about *its* trades, and printing
            # it next to this run's would invite reading a delta as significant
            # because both halves happened to be.
            "scopes": _edge_scopes(ref[1], store.read_vetoed(slug, ref_id), with_luck=False),
        }

    return {
        "run_id": run_id,
        "permutations": edges.PERMUTATIONS,
        "luck_bar": edges.luck_bar(TRADED_CUTS),
        "scopes": _edge_scopes(traded, vetoed, with_luck=True),
        "reference": reference,
    }


def _edge_scopes(traded, vetoed, with_luck: bool) -> dict:
    """The cuts of one run's three books, wire-shaped.

    A scope with no trades in it is served as null rather than as a table of
    zeros: "this run vetoed nothing" and "every vetoed bucket is empty" are
    different facts, and only the first one is true.
    """
    books = {"traded": (traded, TRADED_CUTS), "vetoed": (vetoed, VETOED_CUTS)}
    if vetoed is not None and not vetoed.empty and not traded.empty:
        # The run the gates were never in. Concatenated, not summed: a bucket's win
        # rate is a property of its trades, and averaging two of them is not one.
        books["all"] = (pd.concat([traded, vetoed], ignore_index=True), TRADED_CUTS)

    out: dict = {}
    for scope, (book, names) in books.items():
        if book is None or book.empty:
            out[scope] = None
            continue
        cuts = edges.cuts(book, names, with_luck=with_luck)
        out[scope] = {
            "trades": int(len(book)),
            "net_pnl": float(book["net_pnl"].sum()),
            "cuts": [
                {
                    "name": c["name"],
                    "label": c["label"],
                    "knowable": c["knowable"],
                    "luck": c["luck"],
                    "holds": c["holds"],
                    "rows": records(c["frame"], EDGE_COLS),
                }
                for c in cuts.values()
            ],
        }
    return out


@router.get("/strategies/{slug}/runs/{run_id}/trade-chart/{trade_no}")
def trade_chart(slug: str, run_id: str, trade_no: int, tz: str | None = Query(None)) -> dict:
    _strategy(slug)
    payload = sim_charts.sim_trade_chart(slug, run_id, trade_no, _tz(tz))
    if not payload.get("available"):
        raise HTTPException(404, f"No chart for {run_id} trade #{trade_no}")
    return payload


@router.get("/strategies/{slug}/runs/{run_id}/day-chart/{day}")
def day_chart(slug: str, run_id: str, day: date, tz: str | None = Query(None)) -> dict:
    _strategy(slug)
    payload = sim_charts.sim_day_chart(slug, run_id, day, _tz(tz))
    if not payload.get("available"):
        raise HTTPException(404, f"No chart for {run_id} on {day}")
    return payload
