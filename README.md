# Catalyst Screener

A single-user web app that screens US-listed biotech equities for upcoming **Phase 2 / Phase 3 results** and **PDUFA decisions**, then checks market cap, price, cash runway, liquidity, and listing. Every decision is deterministic and explained with its observed value, threshold, source, and timestamp. It places no trades.

- **Demo mode** works immediately with no API keys. It runs synthetic responses in the documented BPIQ and Alpaca schemas through the same pipeline as live data.
- **Live mode** uses BPIQ Apex REST (catalysts) and Alpaca (historical SIP daily bars and asset listing) once credentials are configured. It never falls back to demo data.

See [docs/provider-capability-matrix.md](docs/provider-capability-matrix.md) and [docs/limitations.md](docs/limitations.md).

## Quick start (demo)

Requirements: Python 3.11+ and Node 20+.

```powershell
# Backend (PowerShell)
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
# Backend (macOS/Linux)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and select **Run scan**. No sign-in is required.

**Single process:** run `npm run build` in `frontend/`. The backend then also serves the UI at http://127.0.0.1:8000.

## Live mode

1. Copy `.env.example` to `.env` in the repo root. Never commit `.env`.
2. Fill in `BPIQ_API_KEY`, `BPIQ_ACCESS_TIER`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, and `ALPACA_ACCOUNT_TYPE`.
3. Check connectivity, permissions, and schemas:
   ```powershell
   cd backend
   .\.venv\Scripts\python.exe -m app.diagnostics --labels
   ```
4. Set `APP_MODE=live` and restart the backend.

Credentials stay in the backend process. They are never sent to the browser, written to logs, stored in the scan database, or included in CSV exports. Adding keys does not bypass plan limits:

- An Apex trial covers only the next 30 days, so a 60–90 day window is reported as a subscription limitation.
- If Alpaca refuses `feed=sip`, the scan reports it and does not fall back to IEX.
- Cash runway is not available from either provider (see limitations).

## Visual direction

A calm "lab instrument" dashboard:

- **Palette:** cool neutral surfaces with a single deep-teal accent.
- **Status colours:** green (pass), red (fail), amber (unknown), and dashed grey (not applied), used only for status.
- **Data:** tabular numerals.
- **Mode indicator:** a striped amber *Demo data* pill so demo results are never mistaken for live data.

The layout collapses to a single column on narrow screens. Company details open in a keyboard-accessible side sheet.

## Architecture

**Stack:** Python with FastAPI, httpx, pydantic, and SQLite, plus a React + TypeScript (Vite) UI. Typed schemas and async HTTP make it easy to validate provider payloads strictly and handle retries and pagination explicitly. SQLite needs no setup for a single user. The UI is a small SPA with no UI framework dependency beyond icons.

```
backend/app/
  providers/
    http.py                 rate limiting, timeouts, bounded retries, error mapping
    errors.py               typed provider errors (no credentials in messages)
    bpiq/schemas.py         external schema: GET /api/v1/info/catalysts/
    bpiq/normalize.py       validation → normalized Catalyst / CompanyProfile
    bpiq/client.py          filters, pagination, trial horizon, origin-pinned `next`
    alpaca/schemas.py       external schemas: bars, assets
    alpaca/normalize.py
    alpaca/market_data.py   SIP daily bars (raw), pagination
    alpaca/assets.py        listing lookup
    financials.py           live: unavailable · demo: labelled mock provider
    factory.py              live vs demo wiring (only the transport and credentials differ)
  fixtures/                 demo data, strictly separated
    bpiq/, alpaca/          provider response fixtures in documented schemas
    mock_financials.json    demo-only enrichment, never used in live mode
    transport.py            mock HTTP server + failure scenarios
  domain/models.py          normalized application models
  screening/                calendar, classification, liquidity, criteria (pure functions)
  scan/orchestrator.py      fetch → normalize → evaluate → persist; partial-failure handling
  scan/export.py            CSV
  storage/repository.py     scan inputs and results in SQLite
  main.py                   HTTP API
  diagnostics.py            live connection check
frontend/src/               React UI
```

## Screening rules

| Criterion | Rule | Source |
| --- | --- | --- |
| Catalyst | At least one catalyst of a selected type dated 60–90 calendar days from today (New York), inclusive. Undated catalysts are excluded. A Phase 2/3 stage only counts when the event indicates results. | BPIQ |
| Market cap | $50M–$2B inclusive | BPIQ `company.market_cap` |
| Price | Latest completed regular-session close strictly above $1 | Alpaca SIP daily bar |
| Cash runway | Cash ÷ monthly net burn of at least 12 months | Demo mock only; unavailable live |
| Liquidity | Average of the weekly sums of daily volume × VWAP over the last 4 completed trading weeks (holiday-shortened weeks included), above $2M | Alpaca SIP daily bars |
| Listing | Active `us_equity` on NASDAQ, NYSE, AMEX, ARCA, BATS, or NYSEARCA; not OTC | Alpaca assets |

Each criterion is **Pass**, **Fail**, **Unknown**, or **Not applied**. A company **Qualifies** only if every enabled criterion passes. It **Does not qualify** if any enabled criterion fails. Otherwise it has **Insufficient data**.

Scan outcomes distinguish success, no matches, incomplete, provider unavailable, subscription limitation, and configuration error. A failed refresh keeps the previous successful scan on screen.

## Demo scenarios

The demo dataset includes:

- Companies that pass every filter, including window, market-cap, and price boundary cases.
- One failure for each criterion.
- Missing financials, an undated catalyst, and a company with multiple catalysts.
- Short market history, a duplicate record across pages, and bars without VWAP.

Use the **Scenario** selector to simulate:

- BPIQ: rate limiting, an outage, an invalid key, trial limits, and malformed records.
- Alpaca: SIP permission denial, a data outage, and partial asset-lookup failures.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```

The tests cover:

- Inclusive boundaries and exchange-calendar weeks.
- Classification and normalization.
- BPIQ and Alpaca pagination, retries, and rate limits.
- Missing data and turnover calculations.
- Failure scenarios end to end.
- That live mode never contains mock values.
- That API responses and CSV exports never contain credentials.

## Scope

This version has no authentication, MCP, streaming, alerts, portfolio tracking, trade execution, or AI recommendations. It is meant to run on localhost. Do not deploy it publicly without adding access control.
