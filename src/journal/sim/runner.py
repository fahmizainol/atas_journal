"""Run a strategy config to a stored artifact, with progress and a spend guard.

Split from the engine so the tick loop stays pure: this module owns the
lifecycle around it — preflight (how many sessions, how many need a paid
Databento pull), per-session progress into state.json, and metrics/veto
summaries at the end. Both the API's background task and the CLI go through
``execute`` so a run behaves identically however it was started.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

import pandas as pd

from .. import metrics as metricsmod
from . import confluences as confmod
from . import regime_pnl, store
from . import ticks as tickmod
from .registry import Strategy
from .rules import SimConfig

# Sessions are independent, so the simulation of a window fans out across
# processes (the tick loop is pure Python and GIL-bound — threads wouldn't help).
# SIM_WORKERS overrides the worker count; 1 (or 0) forces the serial path, which
# is exactly the old behavior — used by tests and when profiling the loop itself.
# SIM_MP_METHOD overrides the start method. Default is forkserver: the API drives
# this from a background-task thread, and a plain fork there can deadlock a child
# that inherits a lock (the import lock, a BLAS pool) held by another uvicorn
# thread at fork time. forkserver forks each worker from a clean, single-threaded
# helper instead — as fast as fork here (the stack is imported once, not per
# worker) without that hazard. Set SIM_MP_METHOD=fork to shave the last fraction
# on the single-threaded CLI, or =spawn as the most conservative fallback.
_WORKERS_ENV = "SIM_WORKERS"
_MP_METHOD_ENV = "SIM_MP_METHOD"
_MAX_CONCURRENT_ENV = "SIM_MAX_CONCURRENT"
# Below this many sessions the pool's start-up cost isn't worth it — run serially.
_MIN_PARALLEL_SESSIONS = 8


def max_concurrent_runs() -> int:
    """How many sim runs may execute at once (SIM_MAX_CONCURRENT, default 2; 1
    restores the old strictly-serial behavior). Each run's worker pool is sized
    to a 1/K share of the box (see ``_worker_count``), so K concurrent runs never
    oversubscribe the cores — which is what wedged both runs and made the orphans.
    The router's create-run gate reads this same number, so the share and the cap
    can never drift apart."""
    override = os.environ.get(_MAX_CONCURRENT_ENV)
    if override is not None:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return 2


def _mp_context():
    """The configured start method, falling back to spawn where it is absent
    (forkserver and fork are POSIX-only; spawn exists everywhere)."""
    method = os.environ.get(_MP_METHOD_ENV, "forkserver")
    try:
        return mp.get_context(method)
    except ValueError:
        return mp.get_context("spawn")


def _worker_count(n_days: int) -> int:
    override = os.environ.get(_WORKERS_ENV)
    if override is not None:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    cores = os.cpu_count() or 2
    # Divide the box across the max concurrent runs so K at once (each with its own
    # pool, fixed at start) never oversubscribe the cores. cores-1 leaves one for the
    # API/main thread; the //K share is what makes concurrent runs safe.
    budget = (cores - 1) // max_concurrent_runs()
    return max(1, min(n_days, budget, 8))


def _simulate_session(slug: str, cfg, day: date) -> tuple[list[dict], list[dict]]:
    """One session's trades + vetoes, run in a worker process.

    Only the two row lists cross the process boundary — the bars/bands frames
    ``run_session`` also returns are for the charts and are dropped here. Every
    tick this reads is already on disk: the parent's serial fetch pass guarantees
    it, so a worker never reaches Databento and never races the roll map."""
    from . import registry

    strat = registry.get(slug)
    t, v, _, _ = strat.run_session(cfg, day)
    return t, v


def preflight(cfg, session: str = "rth",
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


def _capacity_stats(missed: pd.DataFrame, trades: pd.DataFrame) -> dict:
    """What the 'in_trade' ghosts are actually worth under one-contract-at-a-time.

    ``net_pnl`` summed over the ghosts is a fiction: every ghost is born while a
    real position is open (that is what makes it 'in_trade'), and each is priced
    standalone, so the sum silently stacks positions you could never have held at
    once — on this book up to six deep. The realizable figure keeps the real book
    fixed and inserts only the ghosts that fall in its gaps (no overlap with a
    real trade or an already-inserted ghost, earliest-finish greedy). That is the
    money being one-trade-at-a-time genuinely left on the table; the rest of the
    headline sum is double-counting.  ``max_concurrent`` reports how deep the naive
    sum stacks, so the panel can say why the two numbers differ."""
    def spans(df):
        return list(zip(df["entry_ts_utc"].astype("int64"),
                        df["exit_ts_utc"].astype("int64"),
                        df["net_pnl"]))
    real = spans(trades) if not trades.empty else []
    ghost = spans(missed)

    # Half-open [entry, exit): a ghost that opens exactly as a real trade closes
    # does not conflict with it.
    def hits_real(a):
        return any(a[0] < r[1] and r[0] < a[1] for r in real)

    free = sorted((g for g in ghost if not hits_real(g)), key=lambda g: g[1])
    last_end, r_net, r_n = None, 0.0, 0
    for s, e, net in free:
        if net <= 0:
            continue  # a loser you skipped is not a cost of being in a trade
        if last_end is None or s >= last_end:
            r_net += float(net); r_n += 1; last_end = e

    # Peak concurrency if you had naively taken every real + ghost fill.
    events = []
    for s, e, _ in real + ghost:
        events.append((s, 1)); events.append((e, -1))
    events.sort()
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)

    return {"realizable_net": r_net, "realizable_count": r_n,
            "max_concurrent": peak}


def start(strategy: Strategy, cfg) -> str:
    """Validate and create the run folder in 'running' state; returns the run id
    without simulating anything. Pair with ``run_to_completion``."""
    confmod.validate(cfg, strategy.confluences)
    days = tickmod.session_dates(cfg.start_date, cfg.end_date)
    if not days:
        raise ValueError("window contains no weekday sessions")
    return store.init_run(strategy.slug, cfg, strategy.version, len(days))


def run_to_completion(strategy: Strategy, cfg, rid: str,
                      fetch_overnight: bool = True) -> None:
    """The actual work; runs in a background thread for API calls, inline for
    the CLI. All failures land in state.json — a run can never just vanish.

    Two phases. First a *serial* fetch pass pulls and caches every session's
    ticks — that is where each paid Databento download happens, one at a time, so
    the parallel pass below never issues a concurrent buy or races the roll map,
    and a broken window (a day that should have ticks and doesn't) fails before a
    single session is simulated. Then the sessions — independent by construction —
    are simulated across a process pool (serial when SIM_WORKERS<=1 or the window
    is small), which is byte-identical to the old inline loop: ``finalize`` sorts
    the rows by entry time, so completion order can't change the result.

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
        # that can't name its contracts should fail before it buys any ticks. It
        # also lands the roll map on disk, so every worker resolves contracts from
        # the file rather than re-probing Databento.
        tickmod.ensure_roll_map(cfg.contract, cfg.start_date, cfg.end_date)
        # A globex-anchored engine reads the overnight itself, so the night must be
        # cached for it whether or not the run form asked for it (an RTH engine only
        # needs it for the charts, which is what fetch_overnight is for).
        engine_overnight = strategy.session == "globex"

        # --- phase 1: serial fetch + window validation --------------------------
        work_days: list[date] = []
        closed = 0
        for day in days:
            # session_dates is every weekday; the exchange's own holidays (Christmas,
            # New Year's) land here too. When the roll probe confirmed no session
            # existed, the day is skipped as the non-event it was — anything short of
            # that confirmation still fails below, because a day that *should* have
            # ticks and doesn't is a broken window, not a holiday.
            if tickmod.market_closed(cfg.contract, day):
                closed += 1
                continue
            sym = tickmod.contract_for(cfg.contract, day)
            if fetch_overnight or engine_overnight:
                tickmod.ensure_overnight(sym, day)
            # Only reach for the ticks when the RTH segment isn't already on disk:
            # an empty pull is never cached (see ticks._get_segment), so a file that
            # exists is a file with ticks. This is the one place a cold day is bought
            # — serially — and the guard that fails a broken window before phase 2.
            # Loading a cached frame here just to count it would be wasted I/O; the
            # workers read it from disk themselves.
            if not tickmod._cache_path(sym, day, "rth").exists():
                if not len(tickmod.get_day_ticks(
                        sym, day, include_overnight=engine_overnight)):
                    raise RuntimeError(
                        f"no ticks for {day} ({sym}) — cannot report metrics over this window")
            work_days.append(day)

        # Closed days are done the moment we've skipped them — seed the bar there.
        done = closed
        store.update_progress(strategy.slug, rid, done)

        # --- phase 2: simulate (parallel where it pays) -------------------------
        workers = _worker_count(len(work_days))
        if workers <= 1 or len(work_days) < _MIN_PARALLEL_SESSIONS:
            for day in work_days:
                t, v = _simulate_session(strategy.slug, cfg, day)
                rows.extend(t)
                veto_rows.extend(v)
                done += 1
                store.update_progress(strategy.slug, rid, done)
        else:
            ex = ProcessPoolExecutor(max_workers=workers, mp_context=_mp_context())
            try:
                futs = [ex.submit(_simulate_session, strategy.slug, cfg, day)
                        for day in work_days]
                for fut in as_completed(futs):
                    t, v = fut.result()  # re-raises whatever the worker raised
                    rows.extend(t)
                    veto_rows.extend(v)
                    done += 1
                    store.update_progress(strategy.slug, rid, done)
            except BaseException:
                # Don't let the other sessions keep burning cores once the run has
                # already failed (or been cancelled).
                ex.shutdown(cancel_futures=True)
                raise
            else:
                ex.shutdown()

        trades = engine.finalize(rows, cfg)
        ghosts = engine.finalize(veto_rows, cfg)
        # Two different counterfactuals share the ghost machinery, so they are
        # split into two artifacts: entries a gate refused (vetoed.parquet — the
        # gates' book, what the /edges scopes and by_gate score) and entries the
        # engine never saw because a position was already open (missed.parquet —
        # the capacity cost of being one-trade-at-a-time). Folding the latter
        # into vetoed would silently change what every gate comparison means.
        if not ghosts.empty:
            in_trade = ghosts["gate"] == "in_trade"
            vetoed = ghosts[~in_trade].reset_index(drop=True)
            missed = ghosts[in_trade].reset_index(drop=True)
            # Each artifact numbers its own rows: finalize numbered the combined
            # frame, and carrying those gaps through would renumber every vetoed
            # row relative to a run made before the split existed.
            for f in (vetoed, missed):
                f["trade_no"] = range(1, len(f) + 1)
        else:
            vetoed = missed = ghosts
        m = metricsmod.compute_metrics(trades) if not trades.empty else {"trades": 0}
        m.update(_r_stats(trades))
        if not vetoed.empty:
            m["vetoed"] = {
                "count": int(len(vetoed)),
                "net_pnl": float(vetoed["net_pnl"].sum()),
                "by_gate": vetoed["gate"].value_counts().to_dict(),
            }
        if not missed.empty:
            m["missed"] = {
                "count": int(len(missed)),
                "net_pnl": float(missed["net_pnl"].sum()),
                **_capacity_stats(missed, trades),
            }
        store.finish_run(strategy.slug, rid, trades, vetoed, m, missed=missed)
        store.maybe_autopin_baseline(strategy.slug, rid)
        _snapshot_regime_pnl(strategy.slug, rid, cfg, trades)
    except Exception as exc:  # noqa: BLE001 — the state file IS the error channel
        store.fail_run(strategy.slug, rid, f"{type(exc).__name__}: {exc}")


def _snapshot_regime_pnl(slug: str, rid: str, cfg, trades: pd.DataFrame) -> None:
    """Write the regime-vs-P&L study next to the metrics, so it exists for anything
    that isn't the browser — a file an LLM can read beats a table only a mounted
    React component has ever computed.

    Strictly after finish_run, and strictly non-fatal: this is derived from the
    run, not part of it. A run that produced trades and metrics has succeeded, and
    a regime cache that can't answer (a window whose ticks were bought under a
    different contract, say) must not turn that into a failed run. The endpoint
    recomputes on demand anyway, so the cost of skipping here is a slow first open,
    not a missing study.
    """
    try:
        store.write_regime_pnl(
            slug, rid, regime_pnl.study(cfg.contract, cfg.start_date, cfg.end_date, trades))
    except Exception:  # noqa: BLE001 — derived artifact; never fails the run
        pass


def execute(strategy: Strategy, cfg,
            fetch_overnight: bool = True) -> str:
    """Synchronous start + run; the CLI path."""
    rid = start(strategy, cfg)
    run_to_completion(strategy, cfg, rid, fetch_overnight=fetch_overnight)
    return rid
