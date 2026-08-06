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
from journal.sim import gate_audit, regime_pnl, registry, runner, schema, store
from journal.sim import ticks as tickmod

from .. import sim_charts
from ..serialize import records

router = APIRouter()

EDGE_COLS = ["bucket", "trades", "net_pnl", "win_rate", "expectancy", "avg_r"]
# The per-confluence veto breakdown carries the same metrics plus ``unique`` (how
# many entries a gate caught alone). ``bucket`` is the confluence name.
CONFLUENCE_COLS = ["bucket", "trades", "unique", "net_pnl", "win_rate",
                   "expectancy", "avg_r"]
# The MFE/MAE profile: one row per outcome group (All/Winners/Losers), the median
# peak and trough in R, what fraction of the peak the exit kept, and the shares
# that reached +1R in favor / sat through -1R against. ``bucket`` is the group.
EXCURSION_COLS = ["bucket", "trades", "mfe_r", "mae_r", "capture",
                  "ever_green", "reach_1r", "heat_1r"]

# The cuts a run is served in. Order is the order they are rendered in, and the
# count is the family the luck bar corrects for — adding a seventh cut here makes
# the bar for all of them stricter, which is the point of a Bonferroni.
TRADED_CUTS = ("by_hour_et", "by_weekday", "by_hold_time", "by_direction",
               "by_exit_reason", "by_band_width", "by_entry_reason")
VETOED_CUTS = TRADED_CUTS + ("by_gate",)

TRADE_COLS = [
    "trade_no", "session", "direction", "entry_ts_local", "exit_ts_local",
    "avg_entry", "avg_exit", "stop_price", "final_stop_price", "target_price", "exit_reason",
    "points", "r_multiple", "mfe_points", "mae_points", "mfe_r", "mae_r",
    "band_width_ticks", "duration_s",
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
            "meta": store.read_meta(slug, run_id), "state": state,
            # Per-trade tags (trade_no -> [tag]) plus the strategy-wide vocab that
            # feeds the tag editor's autocomplete. Both ride the run detail so the
            # by-trade table can render badges and filter by tag with no extra call.
            "trade_tags": store.read_trade_tags(slug, run_id),
            "tag_vocab": store.strategy_tag_vocab(slug)}


class ConfigIn(BaseModel):
    config: dict = {}
    # Optional name for the run, set at creation. The form suggests the diff
    # against baseline ("stop 50, trail 75"), which beats a list of hashes when
    # you are ten experiments deep. Ignored by /preflight.
    label: str | None = None
    # There used to be an `rth_only` flag here, buying the RTH window alone and
    # skipping the night. A run now always buys the whole session in one range, so
    # the flag is gone; a client that still sends it is simply ignored. (The
    # unrelated `rth_only` *rule* knob inside `config` — RTH entries only — is
    # untouched.)


@router.post("/strategies/{slug}/preflight")
def preflight(slug: str, body: ConfigIn) -> dict:
    """Spend guard: how many sessions the window needs and how many would be
    paid Databento pulls. Call before POSTing the run; nothing is fetched."""
    strat = _strategy(slug)
    cfg = _parse_config(strat, body.config)
    pf = runner.preflight(cfg)
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

    # Cap concurrent runs (SIM_MAX_CONCURRENT, default 2): each run's pool is sized
    # to a 1/K share of the box (runner._worker_count), so up to K at once never
    # oversubscribe the cores — which is what wedged both runs and made the orphans.
    # Beyond that, refuse; the caller retries when a slot frees. (This on-disk scan
    # is not atomic, so two simultaneous POSTs can briefly land K+1 runs; harmless
    # at these small K — the pools still fit the box with one core to spare.)
    limit = runner.max_concurrent_runs()
    inflight = store.running_runs()
    if len(inflight) >= limit:
        raise HTTPException(
            409, f"{len(inflight)} run(s) already in progress "
                 f"({', '.join(inflight)}); max {limit} at once — "
                 "wait for one to finish before starting another")

    try:
        rid = runner.start(strat, cfg)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.label:
        store.write_meta(slug, rid, label=body.label)
    background.add_task(runner.run_to_completion, strat, cfg, rid)
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


class TradeTagsIn(BaseModel):
    tags: list[str] = []


@router.patch("/strategies/{slug}/runs/{run_id}/trades/{trade_no}")
def patch_trade_tags(slug: str, run_id: str, trade_no: int,
                     body: TradeTagsIn) -> dict:
    """Set the tags on one trade. Mirrors patch_meta: mutates a sidecar, never the
    immutable trades.parquet, and 404s on an unknown run. Returns the run's full
    trade->tags map plus the refreshed strategy vocab so a newly-coined tag is
    immediately offered by the editor's autocomplete."""
    _strategy(slug)
    if store.read_state(slug, run_id) is None:
        raise HTTPException(404, f"No run {run_id}")
    tags = store.write_trade_tags(slug, run_id, trade_no, body.tags)
    return {"trade_tags": tags, "tag_vocab": store.strategy_tag_vocab(slug)}


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


@router.get("/strategies/{slug}/runs/{run_id}/gate-audit")
def run_gate_audit(slug: str, run_id: str) -> dict:
    """The gate-robustness scorecard for this run's confluence stack.

    Variant runs (gate deleted, parameter neighbors) are resolved by config
    hash, never by name — so the audit follows any config it is pointed at and
    reports variants that were never run as launchable configs rather than
    holes. Computed on request: the inputs are immutable parquet artifacts and
    the whole ladder is numpy over a few hundred trades. See
    docs/research/gate-robustness.md for the methodology and verdict rules.
    """
    strat = _strategy(slug)
    out = gate_audit.audit(strat, slug, run_id)
    if out is None:
        raise HTTPException(404, f"No completed run {run_id}")
    return out


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
        # Only the cuts this book can actually produce: by_entry_reason needs the
        # entry_reason column, which only the drift-fade strategies write, so on
        # every other run it drops out rather than rendering an empty section (and
        # never charges those runs' Bonferroni family for a cut it can't test).
        names = tuple(
            n for n in names
            if all(c in book.columns for c in edges.BY_NAME[n].needs))
        cuts = edges.cuts(book, names, with_luck=with_luck)
        out[scope] = {
            "trades": int(len(book)),
            "net_pnl": float(book["net_pnl"].sum()),
            # The MFE/MAE profile of this book. Empty for a run that predates the
            # engine's excursion columns; the panel reads that as "re-run to populate"
            # rather than as a book with no trades.
            "excursions": records(edges.excursions(book), EXCURSION_COLS),
            # The winner/loser distribution the bucket cuts average away — tails,
            # payoff geometry, hold-time split, streaks and drawdown. A plain dict
            # (not a cut frame): sanitize() handles its inf/nan the same way.
            "win_loss": edges.win_loss_profile(book),
            # The full R-outcome shape (stop wall + target spike), what separated
            # winners from losers at entry (permutation-scored like the cuts), and
            # the session-level concentration — the deeper winner/loser reads.
            "r_hist": edges.r_histogram(book),
            "discriminator": edges.entry_discriminator(book, with_luck=with_luck),
            "daily": edges.daily_concentration(book),
            # The losers split by how far they ever ran in favor — give-back vs
            # never-worked. Empty on runs predating mfe_r (same as excursions).
            "loser_giveback": edges.loser_giveback(book),
            # Of those green losers, how fast they collapsed back to breakeven —
            # the mirror of winner_recovery. Empty on runs predating giveback_s.
            "loser_collapse": edges.loser_collapse(book),
            # The mirror: winners split by the heat they took before working.
            "winner_heat": edges.winner_heat(book),
            # Of those underwater winners, how fast they climbed back to breakeven.
            "winner_recovery": edges.winner_recovery(book),
            # Win rate by total time underwater — does sitting red predict the loss.
            # Every trade, bucketed by dwell; empty on runs predating underwater_s.
            "underwater_survival": edges.underwater_survival(book),
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
        # The vetoed book is the only one with a gate set per row, so it is the
        # only one that gets the per-confluence (overlap-counting) breakdown. The
        # disjoint ``by_gate`` cut above sits next to it; the two answer different
        # questions (what one toggle frees vs. what each gate independently
        # catches), and both are worth showing.
        if scope == "vetoed":
            out[scope]["confluences"] = records(
                edges.confluence_breakdown(book), CONFLUENCE_COLS)
    return out


# The charts draw the engine's own tick bars by default ("tick"); the minute
# resolutions rebuild every candle as clock-time bars over the same ticks as a
# context view (sim_charts feeds the freq straight to time_bars). Anything
# unrecognised falls back to the honest default rather than erroring.
_RES = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min"}


def _resolution(res: str | None) -> str:
    return _RES.get(res or "", "tick")


# CVD-divergence swing size, in ticks: how far price must retrace to count a
# swing pivot. A per-view override of the DIV_ZZ_TICKS default — bigger = fewer,
# larger divergences; smaller = more, noisier. None leaves the server default.
_DIV_TICKS = Query(None, ge=1, le=2000)


@router.get("/strategies/{slug}/runs/{run_id}/trade-chart/{trade_no}")
def trade_chart(slug: str, run_id: str, trade_no: int, tz: str | None = Query(None),
                resolution: str | None = Query(None),
                div_ticks: int | None = _DIV_TICKS) -> dict:
    _strategy(slug)
    payload = sim_charts.sim_trade_chart(slug, run_id, trade_no, _tz(tz),
                                         _resolution(resolution), div_ticks)
    if not payload.get("available"):
        raise HTTPException(404, f"No chart for {run_id} trade #{trade_no}")
    return payload


@router.get("/strategies/{slug}/runs/{run_id}/day-chart/{day}")
def day_chart(slug: str, run_id: str, day: date, tz: str | None = Query(None),
              resolution: str | None = Query(None),
              div_ticks: int | None = _DIV_TICKS) -> dict:
    _strategy(slug)
    payload = sim_charts.sim_day_chart(slug, run_id, day, _tz(tz),
                                       _resolution(resolution), div_ticks)
    if not payload.get("available"):
        raise HTTPException(404, f"No chart for {run_id} on {day}")
    return payload
