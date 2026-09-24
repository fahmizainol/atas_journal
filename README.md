# ATAS Trading Journal

Personal trading journal for ATAS NQ-futures `xlsx` exports. Ingests the
Statistics / Journal / Executions sheets into SQLite (deduped across imports),
recomputes performance analytics, and reconstructs a 1-minute candlestick
chart around any trade using Databento market data with your fills marked.

A Vite + React + TypeScript SPA backed by a FastAPI service that reuses the
`src/journal/` compute package. Stat charts are Recharts; the calendar is a CSS
grid; candlesticks render client-side with TradingView `lightweight-charts`.

## Setup

```bash
uv sync                       # Python API deps
cp .env.example .env          # then put your Databento key in DATABENTO_API_KEY
cd frontend && pnpm install   # frontend deps
```

The app works without a Databento key — everything except the live candlestick
chart and MAE/MFE excursion is available. AI review needs `LLM_MODEL` /
`LLM_MODELS` plus the relevant provider key.

## Run (development)

Two processes — Vite proxies `/api` to the API:

```bash
uv run uvicorn api.main:app --reload --port 8000   # API
cd frontend && pnpm dev                            # SPA on :5173
```

## Run (production, one process)

```bash
cd frontend && pnpm build           # emits frontend/dist
uv run uvicorn api.main:app --port 8000
```

FastAPI serves the built SPA (catch-all for client-side routes) with `/api/*`
taking precedence. Open http://localhost:8000.

## Run (installed on a phone)

The built SPA is a PWA. Served over HTTPS with the manifest, Chrome on Android
installs it as a WebAPK — own launcher icon, own task, no URL bar — rather than
a home-screen shortcut. HTTPS is the hard requirement; a LAN IP will never
install, so the tailnet name does the work:

```bash
tailscale serve --bg 8000    # once; persists across reboots. NOT `funnel` —
                             # that would publish it to the internet.
pnpm dev:phone               # clean build, then API with reload + build --watch
                             # (the API waits ~15s for that first build)
```

Then open the `*.ts.net` URL on the phone; the menu must read **Install app**,
not "Add to Home screen". After a rebuild the service worker serves the previous
build for one load and picks up the new one on the next, so reopen twice to see
a change. `dev:api` watches only `api/` and `src/`, so rebuilding the frontend
does not bounce the API.

While actively editing, `tailscale serve --bg 5173` instead gives HMR against
the dev server — no build step, but no service worker and no installed-app
behaviour.

Nothing here authenticates: everything on the tailnet reaches the journal *and*
the live order routes. Keep that surface to your own devices.

Drop ATAS `.xlsx` exports into `data/imports/` and click **Import from
data/imports/** in the sidebar (or use the uploader). Re-importing overlapping
files never double-counts: fills dedupe on `Exchange ID`, journal rows on a
content hash.

## Layout

```
api/                    FastAPI app (imports journal via src/)
  main.py               app, CORS, routers, static SPA mount
  deps.py               shared SQLite conn + lock
  serialize.py          NaN/inf/numpy/timestamp -> JSON sanitizer
  scope.py              resolve_scope: load -> build -> localize -> filter
  charts_data.py        candlestick payload builders (epoch-local, VWAP, rect)
  routers/              meta, filters, overview, edges, statistics, trades,
                        notes, charts, calendar, settings, ai, imports
frontend/               Vite + React + TS SPA
  src/pages/            Overview / Calendar / Edges / Trades / AiReview / CrossCheck
  src/components/       FilterBar, Sidebar, DataTable, KPI cards, charts/, ai/
  src/hooks/            TanStack Query hooks; useFilters (URL <-> state)
data/imports/           drop ATAS xlsx here
data/cache/             cached Databento 1m bars (parquet)
data/journal.db         SQLite store (created on first import)
src/journal/
  config.py             paths, KL/UTC tz, contract specs, env
  db.py                 schema + dedupe upserts
  ingest.py             parse the 3 ATAS sheets
  trades.py             logical (flat->flat) trades + ATAS-row view
  metrics.py            core performance metrics + equity/drawdown
  edges.py              time-of-day / weekday / hold-time / direction breakdowns
  excursion.py          MAE/MFE + exit efficiency from 1m bars
  databento_client.py   ohlcv-1m fetch + parquet cache
```

## Notes

- **Timezones:** Journal/Executions are Asia/Kuala_Lumpur (UTC+8); the
  Statistics sheet is UTC. The display zone (New York / Kuala Lumpur) is
  selectable in the sidebar and drives all dates and hour-of-day breakdowns;
  Databento queries use UTC.
- **Contract math:** NQ = $20/point ($5/tick). Multipliers live in `config.py`.
- **Trade views:** *Logical* groups fills flat→flat (scale-ins collapse into one
  trade); *ATAS rows* shows the Journal sheet verbatim. Toggle in the sidebar.
