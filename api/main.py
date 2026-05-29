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
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import deps  # noqa: E402
from .routers import (  # noqa: E402
    charts,
    edges,
    filters,
    meta,
    notes,
    overview,
    statistics,
    trades,
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
def _startup() -> None:
    deps.init()


app.include_router(meta.router, prefix="/api")
app.include_router(filters.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(edges.router, prefix="/api")
app.include_router(statistics.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(charts.router, prefix="/api")


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
