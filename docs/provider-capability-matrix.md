# Provider capability matrix

Reviewed 2026-09-13 against:

- BPIQ REST API v1 — https://app.bpiq.com/api-documentation
- Alpaca market data plans — https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca market data FAQ — https://docs.alpaca.markets/us/docs/market-data-faq
- Alpaca historical bars — https://docs.alpaca.markets/us/reference/stockbars
- Alpaca assets — https://docs.alpaca.markets/us/reference/get-v2-assets-1

**Status legend**

| Status | Meaning |
| --- | --- |
| **Documented** | Stated explicitly in the official documentation. |
| **Inferred** | Consistent with the documentation, but not stated. Must be confirmed with an authenticated request. |
| **Unverified-live** | No authenticated request has succeeded yet. This applies to every row below until `python -m app.diagnostics` passes with real credentials. |
| **Unavailable** | The field is not available under the configured plan. |

Update 2026-09-14: read-only authenticated BPIQ REST requests were made with the configured Apex **trial** key (see "Verified 2026-09-14" below). The official BPIQ documentation pages returned HTTP 429 to automated fetches on that date, so they could not be re-read. Rows not listed as verified remain **unverified-live**. No authenticated Alpaca request has been made.

## Verified 2026-09-14 (authenticated, read-only)

| Capability | Endpoint | Observation | Status |
| --- | --- | --- | --- |
| Catalysts field set | BPIQ `GET /api/v1/info/catalysts/` | Keys and JSON types match `BpiqCatalystRecord`; `company.market_cap` integer; `catalyst_date` string. `count` was 60 for the next 30 days. | **Verified (trial)** |
| Ticker filter | BPIQ `/catalysts/?ticker=…` | All returned records matched the ticker. | **Verified (trial)** |
| Historical catalysts | BPIQ `GET /api/v1/info/historical-catalysts/` | Accessible on Apex trial. Keys include `id, company{id,ticker,name}, created_at, updated_at, ticker, drug_name, drug_indication, airtable_id, stage, catalyst_date, catalyst_source, catalyst_text, detailed_catalyst_text, catalyst_text_evidence, news_published_at, open_price_gap_percent, intra_day_price_change_percent` (the last two are decimal strings; units not documented). The `ticker` filter works. There is no id link to upcoming records. | **Verified (trial)**. Paid-plan behaviour not checked. |
| BPIQ MCP authentication | `https://bpiq-marketcompass-mcp-production.up.railway.app/mcp` (endpoint found via search result, not the docs page) | `401` with `WWW-Authenticate: Bearer … resource_metadata`. The REST key is rejected (`Token` and `Bearer`). Metadata lists OAuth authorize, token, and registration endpoints and scope `biopharmiq.read`. | **Observed**. Tools and schemas are **not yet verified** (they need OAuth consent). |
| Alpaca `adjustment=split` | Alpaca bars | Documented enum value. Used only for price-context metrics and paper fills. | Documented; unverified-live |

## Required fields

| Required field | Provider · endpoint | Response field | Type / units / nullability | Source timestamp | Subscription restriction | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Catalyst event ID | BPIQ `GET /api/v1/info/catalysts/` | `results[].id` | integer, "Unique catalyst record ID" | none | Apex Paid: full. Apex Trial: next 30 days only. | Documented |
| Ticker | BPIQ catalysts | `results[].ticker`, `results[].company.ticker` | string | none | as above | Documented |
| Company name | BPIQ catalysts | `results[].company.name` | string | none | as above | Documented (sample; listed as `company` object in schema) |
| Drug / program | BPIQ catalysts | `results[].drug_name` | string | none | as above | Documented |
| Catalyst classification | BPIQ catalysts | `results[].stage_event.stage_label`, `.event_label`, `.label`, `.id` | strings / integer. The value vocabulary is **not documented**. | none | as above | Field Documented; mapping to Phase 2 / Phase 3 results / PDUFA **Inferred** (configurable) |
| Catalyst date | BPIQ catalysts | `results[].catalyst_date` | `string \| null`. Format is not stated for catalysts; the drugs schema says "ISO date". | none | Trial: next 30 days only | Documented; `YYYY-MM-DD` **Inferred** |
| Estimated date range | BPIQ catalysts | — | No range field documented. Timing context exists only in free-text `note`. | — | — | **Unavailable** (notes are not parsed) |
| Catalyst source URL | BPIQ catalysts | `results[].catalyst_source` | `string (URL) \| null` | none | as above | In catalysts sample; schema listed only for `/drugs/`. Treated as optional. |
| Indications | BPIQ catalysts | `results[].indications[]`, `indications_text` | array / string | none | as above | In sample; treated as optional |
| Market cap | BPIQ catalysts | `results[].company.market_cap` | integer in sample. USD units **inferred**. Nullability not stated for catalysts (`number \| null` on `/all-companies/`), so treated as nullable. | **none**. The app records its retrieval time instead. | as above | Documented field; units Inferred |
| Company last price | BPIQ catalysts | `results[].company.last_price` | decimal **string** | none | as above | Documented. Display only; Alpaca close is used for eligibility. |
| Date-range filter | BPIQ catalysts | query `catalyst_date_min`, `catalyst_date_max` | Parameter names documented. Format (`YYYY-MM-DD`), inclusivity, and handling of null dates are **not documented**. | — | Trial rejects or limits beyond 30 days (behaviour not documented) | Names Documented; semantics Inferred. The app re-checks boundaries locally. |
| Market-cap filter | BPIQ catalysts | query `market_cap_min`, `market_cap_max` | Units inferred USD | — | — | Names Documented; semantics Inferred |
| Pagination | BPIQ catalysts | `count`, `next`, `previous`, `results`; query `limit`, `offset` | `next` is `string \| null` (absolute URL). Maximum `limit` not documented. | — | — | Documented |
| Rate limit | BPIQ | — | Apex Trial 10 req/min, Apex Paid 15 req/min. No 429 or rate-limit headers documented. | — | per plan | Documented limits; 429 handling Inferred |
| Errors | BPIQ | HTTP 400/401/403/404/500 | Body observed: `{"detail": "..."}` (unauthenticated probe, 2026-09-13) | — | 403 = "plan does not include this endpoint" | Documented; body shape observed without credentials |
| Auth | BPIQ | header `Authorization: Token <key>` | — | — | — | Documented; `WWW-Authenticate: Token` observed |
| **Cash / cash runway** | BPIQ `GET /api/v1/info/all-companies/` | `cash`, `ttm_burn`, `cash_runway_*` filters | `number \| null` | `finance_updated_*` filter exists; no response field documented | **API Premium only. No access under Apex.** | **Unavailable** under Apex |
| Exchange / listing | BPIQ `/all-companies/` `exchange` | — | — | — | API Premium only | **Unavailable** under Apex |
| Exchange / listing | Alpaca Trading API `GET /v2/assets/{symbol}` | `exchange`, `status`, `class`, `tradable` | enum `AMEX, ARCA, BATS, NYSE, NASDAQ, NYSEARCA, OTC, CRYPTO` | none | Trading API keys | Documented. **Pending approval** (see open questions). |
| Biotech classification | BPIQ `/all-companies/` `industry` | — | — | — | API Premium only | Unavailable under Apex. Inferred from BPIQ catalyst coverage. |
| Daily bars | Alpaca `GET https://data.alpaca.markets/v2/stocks/bars` | `bars{symbol: [ {t,o,h,l,c,v,n,vw} ]}` | `t` RFC-3339; `o,h,l,c,vw` double; `v,n` int64 | `t` per bar | Basic: SIP historical allowed only when `end` is ≥15 min old; 200 req/min. Algo Trader Plus: no restriction; 10,000 req/min. | Documented |
| Daily-bar session date | Alpaca bars | `t` | "truncated to the day (in New York)" | `t` | — | Documented (FAQ). Mapping `t` → NY calendar date Inferred. |
| Close price | Alpaca bars | `c` | double, USD | bar `t` | as above | Documented |
| Volume | Alpaca bars | `v` | int64 shares; "can be different from the 'normal' volume" | bar `t` | as above | Documented |
| VWAP | Alpaca bars | `vw` | double, USD | bar `t` | as above | Documented. Presence on every bar Inferred; missing `vw` falls back to a labelled close×volume approximation. |
| Feed selection | Alpaca bars | query `feed=sip` | enum `sip, iex, boats, otc`; default `sip` | — | SIP recency needs a subscription | Documented. The app always sends `feed=sip` explicitly and never falls back to IEX. |
| Adjustment | Alpaca bars | query `adjustment=raw` | enum `raw, split, dividend, spin-off, all` | — | — | Documented. `raw` is used so price and volume are mutually consistent; see limitations. |
| Pagination | Alpaca bars | `next_page_token`, query `page_token`, `limit` ≤ 10000 | Limit applies across all symbols; sorted by symbol, then time | — | — | Documented |
| Extended-hours inclusion in daily bars | Alpaca bars | — | Not explicitly documented | — | — | **Unknown**. Listed as a limitation. |
| Rate-limit headers | Alpaca | `X-RateLimit-Limit/Remaining/Reset` | integers | — | per plan | Documented |
| Auth | Alpaca | `APCA-API-KEY-ID`, `APCA-API-SECRET-KEY` | — | — | — | Documented |
| Exchange calendar | `exchange_calendars` (XNYS), local library | sessions, early closes | — | library version | none | Local computation, not a data provider |
