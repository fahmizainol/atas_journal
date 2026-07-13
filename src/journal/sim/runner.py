"""Run a strategy config to a stored artifact, with progress and a spend guard.

Split from the engine so the tick loop stays pure: this module owns the
lifecycle around it — preflight (how many sessions, how many need a paid
Databento pull), per-session progress into state.json, and metrics/veto
summaries at the end. Both the API's background task and the CLI go through
``execute`` so a run behaves identically however it was started.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .. import metrics as metricsmod
from . import confluences as confmod
from . import store
from . import ticks as tickmod
from .registry import Strategy
from .rules import SimConfig


def preflight(cfg: SimConfig, session: str = "rth",
              fetch_overnight: bool = True) -> dict:
    """What would this run touch? Sessions in the window, how many lack a tick
    cache (each of those is a Databento download that costs real money), and a
    cost estimate when the API can price it. Nothing is fetched or simulated.

    ``session`` comes from the strategy registry: "globex" strategies *need* the
    overnight segment to simulate at all. ``fetch_overnight`` is the run form's
    choice, and by default every run buys the night whether its engine reads it
    or not — the charts do (ticks.ensure_overnight). Either way a day with only
    its RTH file cached still counts as uncached, and the estimate then prices
    both segments, so the guard errs high rather than under-quoting."""
    overnight = fetch_overnight or session == "globex"
    segments = ("rth", "on") if overnight else ("rth",)
    days = tickmod.session_dates(cfg.start_date, cfg.end_date)
    try:
        tickmod.ensure_roll_map(cfg.contract, cfg.start_date, cfg.end_date)
        by_day = {d: tickmod.contract_for(cfg.contract, d) for d in days}
    except Exception:
        by_day = {}  # no key / offline — fall back to counting every day as uncached

    uncached = [
        d for d in days
        if not tickmod.market_closed(cfg.contract, d)  # a holiday has nothing to buy
        and (d not in by_day
             or not all(tickmod._cache_path(by_day[d], d, s).exists() for s in segments))
    ]

    est_cost = None
    if uncached and by_day:
        try:
            # One range query per contiguous block *of a single contract*: a range
            # that straddled the roll would be priced against the wrong symbol.
            est_cost = sum(
                tickmod.estimate_cost(sym, grp[0], grp[-1], include_overnight=overnight)
                for sym, ds in _by_contract(uncached, by_day).items()
                for grp in _contiguous(ds)
            )
        except Exception:
            est_cost = None  # no key / offline — the count still warns

    return {
        "sessions_total": len(days),
        "uncached_sessions": len(uncached),
        "uncached_days": [d.isoformat() for d in uncached],
        "est_cost_usd": est_cost,
        "contracts": sorted(set(by_day.values())),
    }


def _by_contract(days: list[date], by_day: dict[date, str]) -> dict[str, list[date]]:
    out: dict[str, list[date]] = {}
    for d in days:
        out.setdefault(by_day[d], []).append(d)
    return out


def _contiguous(days: list[date]) -> list[list[date]]:
    """Group session dates into runs of consecutive weekdays, so the cost
    estimate queries one range per gap instead of pricing cached days too."""
    groups: list[list[date]] = []
    for d in days:
        if groups and (d - groups[-1][-1]).days <= 3:  # Fri->Mon spans 3
            groups[-1].append(d)
        else:
            groups.append([d])
    return groups


def _r_stats(df: pd.DataFrame) -> dict:
    """R-multiple + band-width summary. Broken out because win-rate alone hides
    the whole story of this strategy: the target is 1 sigma wide and sigma grows
    through the session, so a 09:31 winner and a 14:00 winner are not the same
    trade at all."""
    if df.empty:
        return {}
    return {
        "r_mean": float(df["r_multiple"].mean()),
        "r_median": float(df["r_multiple"].median()),
        "r_best": float(df["r_multiple"].max()),
        "band_width_median_ticks": float(df["band_width_ticks"].median()),
        "band_width_min_ticks": float(df["band_width_ticks"].min()),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


def start(strategy: Strategy, cfg: SimConfig) -> str:
    """Validate and create the run folder in 'running' state; returns the run id
    without simulating anything. Pair with ``run_to_completion``."""
    confmod.validate(cfg, strategy.confluences)
    days = tickmod.session_dates(cfg.start_date, cfg.end_date)
    if not days:
        raise ValueError("window contains no weekday sessions")
    return store.init_run(strategy.slug, cfg, strategy.version, len(days))


def run_to_completion(strategy: Strategy, cfg: SimConfig, rid: str,
                      fetch_overnight: bool = True) -> None:
    """The actual work; runs in a background thread for API calls, inline for
    the CLI. All failures land in state.json — a run can never just vanish.

    ``fetch_overnight`` buys the Globex segment for each session even when the
    engine won't read it — that is what lets the charts draw the night. It cannot
    change a single trade: the engine's own tick pull is decided by the strategy's
    entry point, not by this."""
    from . import engine

    try:
        rows: list[dict] = []
        veto_rows: list[dict] = []
        days = tickmod.session_dates(cfg.start_date, cfg.end_date)
        # Resolve the whole window's roll up front: one cheap pull, and a window
        # that can't name its contracts should fail before it buys any ticks.
        tickmod.ensure_roll_map(cfg.contract, cfg.start_date, cfg.end_date)
        for k, day in enumerate(days):
            # session_dates is every weekday; the exchange's own holidays (Christmas,
            # New Year's) land here too. When the roll probe confirmed no session
            # existed, the day is skipped as the non-event it was — anything short of
            # that confirmation still fails below, because a day that *should* have
            # ticks and doesn't is a broken window, not a holiday.
            if tickmod.market_closed(cfg.contract, day):
                store.update_progress(strategy.slug, rid, k + 1)
                continue
            sym = tickmod.contract_for(cfg.contract, day)
            if fetch_overnight:
                tickmod.ensure_overnight(sym, day)
            t, v, _, _ = strategy.run_session(cfg, day)
            if not len(tickmod.get_day_ticks(sym, day)):
                raise RuntimeError(
                    f"no ticks for {day} ({sym}) — cannot report metrics over this window")
            rows.extend(t)
            veto_rows.extend(v)
            store.update_progress(strategy.slug, rid, k + 1)

        trades = engine.finalize(rows, cfg)
        vetoed = engine.finalize(veto_rows, cfg)
        m = metricsmod.compute_metrics(trades) if not trades.empty else {"trades": 0}
        m.update(_r_stats(trades))
        if not vetoed.empty:
            m["vetoed"] = {
                "count": int(len(vetoed)),
                "net_pnl": float(vetoed["net_pnl"].sum()),
                "by_gate": vetoed["gate"].value_counts().to_dict(),
            }
        store.finish_run(strategy.slug, rid, trades, vetoed, m)
        store.maybe_autopin_baseline(strategy.slug, rid)
    except Exception as exc:  # noqa: BLE001 — the state file IS the error channel
        store.fail_run(strategy.slug, rid, f"{type(exc).__name__}: {exc}")


def execute(strategy: Strategy, cfg: SimConfig,
            fetch_overnight: bool = True) -> str:
    """Synchronous start + run; the CLI path."""
    rid = start(strategy, cfg)
    run_to_completion(strategy, cfg, rid, fetch_overnight=fetch_overnight)
    return rid
