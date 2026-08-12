"""FastAPI entrypoint for the ATAS Journal API.

Reuses the existing ``src/journal`` compute package unchanged. In dev, Vite
(:5173) proxies ``/api`` here (:8000) and CORS allows the Vite origin. In prod,
the built ``frontend/dist`` is mounted last with a catch-all so client-side
routes resolve while ``/api/*`` still wins.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import deps  # noqa: E402
from .routers import (  # noqa: E402
    ai,
    backtests,
    calendar,
    charts,
    confluences,
    day_notes,
    drafts,
    edges,
    filters,
    imports,
    interactions,
    live,
    live_orders,
    meta,
    models,
    notes,
    overview,
    regime,
    replays,
    research,
    sessions,
    settings,
    setups,
    simulator,
    statistics,
    strategies,
    trades,
    videos,
)
from .serialize import SanitizedJSONResponse  # noqa: E402

app = FastAPI(title="ATAS Journal API", default_response_class=SanitizedJSONResponse)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    deps.init()
    # A sim run executes in an in-process background task, so a 'running' state on
    # disk at startup is an orphan — the process that owned it is gone. Clear them
    # before serving: otherwise the UI shows them frozen forever and their configs
    # stay 409-locked against a re-run.
    from journal.sim import store as sim_store

    orphans = sim_store.reconcile_orphans()
    if orphans:
        print(f"[startup] cleared {len(orphans)} orphaned sim run(s): "
              + ", ".join(orphans), flush=True)
    # Auto-import watcher: scans data/imports/ every WATCH_INTERVAL_S. Async
    # handler so the task lands on the running event loop. Disabled by default;
    # set WATCH_ENABLED=1 to run the background polling loop.
    from journal.config import WATCH_ENABLED

    if WATCH_ENABLED:
        from . import watcher

        watcher.start()
    else:
        print("[startup] auto-import watcher disabled (WATCH_ENABLED unset)",
              flush=True)
    _resume_live()


def _resume_live() -> None:
    """Pick a recorded session back up after a restart, and optionally reconnect.

    Two separate things, in order. The resume is unconditional and free: if this
    session date has ticks in the live store, the tape is rebuilt from them so
    the surface is whole up to the restart and the shelf can go on reading the
    day. Without it a process that came back at eleven would hold a tape that
    began at eleven, and every strategy would be simulating a session that opened
    two hours late — silently, with plausible numbers.

    Reconnecting the feed is opt-in (``LIVE_AUTOSTART=1`` plus ``LIVE_SYMBOL``,
    a RAW contract), because it opens a network connection and starts writing,
    and neither should happen because somebody ran the dev server.
    """
    import os

    from journal import live as livemod
    from journal.config import load_env

    try:
        # This repo does not load .env at import — `load_env()` is what does it,
        # and every consumer calls it before reading its own keys. Without this
        # LIVE_AUTOSTART is invisible however it is set in the file, and the host
        # would come up recording nothing while looking configured.
        load_env()
        symbol = os.environ.get("LIVE_SYMBOL", "").strip().upper()
        autostart = os.environ.get("LIVE_AUTOSTART", "").strip().lower() in {
            "1", "true", "yes", "on"}
        if autostart and symbol:
            live = livemod.start_rithmic(symbol, os.environ.get("LIVE_EXCHANGE", "CME"))
            print(f"[startup] live feed connected: {live.session.symbol} "
                  f"{live.session.day} (recording, sweeping earlier sessions)",
                  flush=True)
            # No sweep here: the feed runs its own behind the live stream. One
            # session per login, so a second client would log the feed out.
            return
        live = livemod.resume(symbol or None)
        if live is not None:
            print(f"[startup] resumed recorded session {live.session.symbol} "
                  f"{live.session.day} — {live.session.n} ticks, no feed attached",
                  flush=True)
        # Nothing is connected, so the history plant is ours to use: fill in the
        # sessions this machine was off for. Backgrounded — it is minutes of work
        # and the API must come up now. Needs a contract to sweep, and a raw one:
        # a root would resolve through Databento's roll map, which ends
        # 2026-06-30 and which a live path must not probe.
        if symbol and len(symbol) >= 4:
            from journal.live import harvest

            harvest.sweep_in_background(symbol, exchange=os.environ.get(
                "LIVE_EXCHANGE", "CME"))
    except Exception as e:  # noqa: BLE001 — the API must come up regardless
        print(f"[startup] live resume skipped: {type(e).__name__}: {e}", flush=True)


app.include_router(meta.router, prefix="/api")
app.include_router(filters.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(edges.router, prefix="/api")
app.include_router(statistics.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(day_notes.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(backtests.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(setups.router, prefix="/api")
app.include_router(confluences.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(videos.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(regime.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(research.router, prefix="/api")
app.include_router(drafts.router, prefix="/api")
app.include_router(simulator.router, prefix="/api")
app.include_router(live.router, prefix="/api")
# Its own module so that live.py's "nothing in this router can send an order"
# stays a fact about the file rather than a claim about a paragraph in it.
# Registered unconditionally: every route inside answers 403 with the reason
# when LIVE_ROUTING is unset, which is more use than a 404 that reads as a
# missing feature.
app.include_router(live_orders.router, prefix="/api")
app.include_router(replays.router, prefix="/api")


# --- Prod static frontend (mounted last; only if a build exists) ---------
_DIST = ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
