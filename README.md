# ATAS Trading Journal

Personal trading journal for ATAS NQ-futures `xlsx` exports. Ingests the
Statistics / Journal / Executions sheets into SQLite (deduped across imports),
recomputes performance analytics, and reconstructs a 1-minute candlestick
chart around any trade using Databento market data with your fills marked.

## Setup

```bash
uv sync
cp .env.example .env        # then put your Databento key in DATABENTO_API_KEY
```

The app works without a Databento key — everything except the live candlestick
chart and MAE/MFE excursion is available.

## Run

```bash
uv run streamlit run app.py
```

Drop ATAS `.xlsx` exports into `data/imports/` and click **Import from
data/imports/** in the sidebar (or use the uploader). Re-importing overlapping
files never double-counts: fills dedupe on `Exchange ID`, journal rows on a
content hash.

## Layout

```
app.py                  Streamlit UI (Overview / Calendar / Edges / Trades / Detail / Cross-check)
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
  charts.py             equity, distribution, PnL calendar, reconstruction
```

## Notes

- **Timezones:** Journal/Executions are Asia/Kuala_Lumpur (UTC+8); the
  Statistics sheet is UTC. Times display in KL; Databento queries use UTC.
- **Contract math:** NQ = $20/point ($5/tick). Multipliers live in `config.py`.
- **Trade views:** *Logical* groups fills flat→flat (scale-ins collapse into one
  trade); *ATAS rows* shows the Journal sheet verbatim. Toggle in the sidebar.
