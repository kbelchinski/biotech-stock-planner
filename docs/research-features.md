# Research and decision-support features

Status as of 2026-09-14. Everything here is research support. The app places no orders and makes no
predictions. Facts, calculations, your assumptions and AI interpretations are labelled separately in the UI.

## Navigation

| Page | Purpose |
| --- | --- |
| Screener | The existing scan (unchanged rules). Company details now offer **Watch** and **Open research page**. |
| Watchlist | Companies you track regardless of eligibility, with full catalyst timelines, refresh, reminders and monitoring settings. |
| Company research | The decision page: key questions, screening results, catalyst timeline, price/volume context, financial risk, insider/fund activity, evidence, changes since your last saved analysis, optional AI critique. |
| Trade plans | Calculator, planned trades, hypothetical trades and manually entered actual positions (kept separate). |
| Journal & performance | Performance by group, forward paper trades, decision journal, immutable scan history. |
| Data & integrations | Provider status, BPIQ MCP connection/discovery/mapping, AI budget, monitoring behaviour. |

## Watchlist and catalyst tracking

- A watched company stays tracked after it leaves the 60–90 day window or stops qualifying.
- Refresh queries BPIQ `GET /catalysts/?ticker=…` (all upcoming events, **including undated**) and
  `GET /historical-catalysts/?ticker=…`. The `ticker` filter was verified with an authenticated request on 2026-09-14.
- Catalysts are keyed by BPIQ record id. On each refresh the app records:
  - **Date changed** (previous and current values kept),
  - **Stage/event relabelled**, **note changed**,
  - **No longer returned**: only when the query was complete (all pages, no rejected records) and should have
    covered the event. On Apex trial, events after the 30-day horizon are never marked. This is never called a
    cancellation.
  - **Possible outcome**: a historical record with the same drug name within 14 calendar days. BPIQ documents no id
    link between upcoming and historical records, so this is a labelled heuristic.
- Dates: BPIQ supplies a single `catalyst_date`; whether it is exact or guided is not documented. The UI shows
  "Single provider date · precision not documented". The model supports ranges, but no connected source provides them.
- Earlier events: catalysts dated before the primary event (research page) or before a planned exit (trade plan) are highlighted.
- Failed refreshes keep all stored data, record the error on the company, and create a "refresh failed" notification.

### Reminders and monitoring (confirmed defaults)

- Reminders: **14 and 3 calendar days** before the provider date. Rules can use calendar days or XNYS trading
  sessions and are editable on the Watchlist page.
- Monitoring: **daily at 20:15 New York** (after the session's bars settle), plus manual refresh.
- **The scheduler runs only while the backend process is running.** When the computer is off or the backend is stopped,
  nothing is checked. On the next start, one catch-up refresh runs if a scheduled time was missed; reminders whose
  trigger date passed are then created. Notifications are in-app only (no email/Telegram).

## Price and volume context (not a filter or signal)

Bars: Alpaca historical daily bars, `feed=sip`, **`adjustment=split`** (prices and volumes split-adjusted by Alpaca;
dividends not adjusted, so these are price returns). The screener's price and liquidity criteria still use
`adjustment=raw` and are unchanged. Sessions follow the XNYS calendar up to the latest completed session
(4h15m after the close). Missing bars are never filled or replaced; affected metrics show why they are unavailable.
The window is 400 calendar days (extended back to the first-seen scan if older).

| Metric | Formula | Lookback / minimum data |
| --- | --- | --- |
| Return N sessions (N = 5, 20, 60) | close[t] / close[t−N] − 1 | Both bars required |
| Relative to benchmark (XBI, configurable) | stock return − benchmark return, same sessions (percentage points) | Both series |
| Return since first seen | close[t] / close[first-seen price session] − 1 | First saved scan where it qualified; otherwise first scan that evaluated it |
| Volume vs baseline | mean(volume, last 5 sessions) / mean(volume, the 60 sessions before) | All 5 recent bars; ≥45 of 60 baseline bars |
| Historical volatility | sample stdev of ln(close[i]/close[i−1]) × √252 | Last 20 consecutive-session pairs; ≥15 usable |
| Distance from high | close[t] / max(high) − 1 | 60 sessions (≥45 bars); 252 sessions (≥200 bars) |
| Distance from SMA | close[t] / mean(close) − 1 | 20 and 50 sessions; all bars required |
| Overnight gaps | open[t] / close[previous session] − 1, listed when \|gap\| ≥ 10% | Last 60 sessions; both bars required |

Alpaca notes its daily volume can differ from other consolidated sources. IEX-only volume is never used.

## Trade plans

Inputs: ticker, entry price, planned exit date, thesis, invalidation, loss budget, optional stop, shares or capital
allocation, optional portfolio value, decline scenarios (default 10/25/50%). Long positions only.

- Capital commitment: shares × entry, else capital allocation, else the stop-based size.
- Stop-based size: floor(loss budget ÷ (entry − stop)) whole shares; invalid if stop ≥ entry or the budget is below the per-share risk.
- Exposure: capital ÷ portfolio value. Loss scenarios: capital × decline.
- Known catalysts on or before the exit date are listed, plus undated ones ("cannot be ruled out").
- A stop is a planning estimate, not a guaranteed maximum loss (gaps, slippage, binary events).

Planned trades, hypothetical trades, actual positions and paper trades are separate record types and are never
combined. Watching a ticker never creates any of them.

## Forward paper tracking (rules confirmed 2026-09-14)

- Created after a scan finishes, for each **Qualifying** company with a dated primary catalyst. One open paper trade per company + catalyst.
- **Entry:** open of the first XNYS session whose open is after the scan finished and after the scan's price session. A scan using a session's close never enters in that session.
- **Exit:** close of the last XNYS session strictly before the catalyst date. A provider date revision moves the exit and is logged; if no session remains between entry and exit the trade is cancelled.
- **Size:** $1,000 notional each (results are reported as returns). **Costs:** 0.10% + 0.25% slippage per side.
- **Gaps:** fills at the actual open/close. **Missing price:** the trade becomes `price_unavailable`; nothing is interpolated.
- Fills use split-adjusted bars; at exit the entry is re-read from the same series if a split changed history.
- Paper trades cannot be edited or deleted.

This is forward tracking only. Today's (revised) catalyst calendar cannot validate the strategy historically; a
credible backtest would need point-in-time catalyst data, historical coverage and execution assumptions.

## Performance statistics

Per group (actual, hypothetical, paper), closed trades only:

- Net return = exit × (1 − cost − slippage) ÷ (entry × (1 + cost + slippage)) − 1.
- Win rate (net > 0), average win, average loss, three worst outcomes.
- Compounded return and maximum drawdown chain trades in exit-date order as if each used full equity (overlaps not modelled).
- Benchmark-relative: net return − (benchmark close on exit ÷ benchmark open on entry − 1), with coverage count.
- By catalyst category, sample size and observation period. Fewer than 20 trades shows a small-sample warning.
- Open positions are listed separately with a mark from the latest split-adjusted close.

## Scan snapshots

Scans are insert-only (a database trigger blocks updates). Each snapshot contains normalized BPIQ catalyst records,
company market cap values, daily bars used, the screening-rule version (`SCREENING_RULE_VERSION`), every criterion
decision with observed value, threshold, explanation and sources with timestamps, data-quality issues, and derived
calculations such as weekly turnover. A per-company index supports first-seen lookups.

**Unresolved storage question:** the BPIQ and Alpaca terms were not reviewed for local retention of normalized
records. Snapshots stay on this machine; review both providers' terms before exporting or sharing CSVs or the database.

## BPIQ MCP (optional)

Findings (2026-09-14): the documentation pages returned HTTP 429 to automated requests. The endpoint
`https://bpiq-marketcompass-mcp-production.up.railway.app/mcp` answers `401` with an OAuth `WWW-Authenticate`
challenge and rejects the REST API key. Its metadata advertises authorization-code OAuth with dynamic client
registration and scope `biopharmiq.read`.

Implementation:

1. Set `BPIQ_MCP_URL` and restart. On **Data & integrations**, select **Connect** and approve in BPIQ. Tokens are
   stored in `backend/data/bpiq_mcp_tokens.json` (gitignored) and refreshed automatically; they are never sent to the browser.
2. **Discover tools** lists actual tool names and input/output schemas (`backend/data/bpiq_mcp_tools.json`).
3. **Test a tool** shows the raw response.
4. **Capability mapping**: choose and confirm the tool for financials, insider transactions and hedge fund holdings.
   Research pages call only confirmed tools, with a ticker argument that exists in the discovered schema.

Normalization maps explicit candidate field names only and marks every value **Unverified mapping** with its raw
field name until checked against real responses. Runway is calculated only when cash, burn and a documented burn
period (ttm/quarterly/monthly field) are present; non-positive burn is reported without a runway value and never as
"safe"; reference dates older than 120 days are flagged stale; runway is not reduced for elapsed time. Shelf/ATM
values are labelled capacity, not issuance. Fund changes use the provider's value, or are calculated only against the
same fund's immediately preceding period. BPIQ pick/avoid flags are shown separately as provider labels.

Demo mode never calls MCP and has no MCP fixtures, because no schema has been verified.

### Insider transactions (BPIQ, with optional SEC Form 4 enrichment)

**What BPIQ provides (verified 2026-09-18).** `fetch_company_insider_transactions` returns exactly eight string
fields: `executive`, `executive_title`, `ticker`, `transaction_date`, `shares`, `share_price`, `security_type` and
`acquisition_or_disposal` (A/D). Checked against the tool description and live responses for VRTX and SRPT (250
rows each; 250 appears to be a row cap). The provider does **not** return a transaction code, filing date, accession
number, filing link, post-transaction holdings, direct/indirect ownership, footnotes, CIKs or amendment status.
These are *field unavailable*, not unmapped.

So BPIQ rows are shown as **Acquired — transaction type unknown** or **Disposed — transaction type unknown**. They are
never labelled purchases, awards, exercises or sales, and are excluded from any purchase metric. A `share_price` of
`0.0` is treated as no reported price. Missing filing links and holdings read **Not provided**, never zero.

**SEC enrichment (optional, live mode).** Set `SEC_USER_AGENT` to your name and contact email. The SEC's
fair-access policy requires this; requests without it are blocked. The app then:

1. Resolves the ticker to a CIK (`https://www.sec.gov/files/company_tickers.json`).
2. Lists Forms 4 and 4/A filed in the last 365 days (or since the oldest BPIQ row) from
   `https://data.sec.gov/submissions/CIK##########.json`, newest first, capped at `SEC_MAX_FILINGS` (60).
3. Parses each filing's ownership XML: issuer and reporting-owner CIKs, name and role, transaction and filing dates,
   transaction code with the official SEC label, A/D, security title, shares, price, holdings after the
   transaction, direct/indirect ownership, footnotes, accession number, link and amendment status. Both tables are
   parsed. Derivative rows are marked, because their shares and holdings count derivative securities.

Requests are rate-limited to `SEC_RATE_LIMIT_PER_SEC` (default 5; the SEC maximum is 10). They are cached in
`backend/data/sec_cache/`: filing documents indefinitely (they are immutable), the ticker map for a day, and the
submissions index for an hour. Each value keeps the date it was actually retrieved.

**Transaction codes** follow the SEC list at https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html. The raw
code is shown next to the official label. Code P is "Open market or private purchase". The source does not separate
open-market from private purchases, so the app never calls a row an open-market purchase.

**Matching is conservative.** A BPIQ row takes an SEC transaction's detail only when all of these hold:

- **Issuer:** the issuer CIK matches.
- **Owner:** the reporting-owner name matches as a token set, so "VAN, GRUNSVEN JASPER" equals "van Grunsven Jasper".
- **Date, A/D and shares:** the transaction date, acquired/disposed flag and share amount are equal.
- **Security and price:** security type and price are equal *when both sides have them*. Anything not compared is
  listed on the row.
- **Uniqueness:** exactly one SEC transaction fits, and no other BPIQ row fits that transaction.

Otherwise the BPIQ row stays unclassified, as *ambiguous*, *unmatched* or *too little detail*. SEC transactions not
matched to a BPIQ row are listed in a separate table. A ticker and date alone never match.

**Amendments.** A Form 4/A names its original through `dateOfOriginalSubmission`. The app handles them as follows:

- **Replaced rows:** amended transactions replace the original's rows that share the same table, security, date, code
  and A/D. Replaced rows are hidden and not counted.
- **Holdings-only amendments:** these leave the original's transactions unchanged.
- **Unreconciled amendments:** if the original can't be identified, or the row counts differ, the amended rows are
  shown but not counted, so nothing is double-counted.
- **Later amendments:** a second amendment replaces the first.

**Provenance.** Each value carries a mark: **B** (BPIQ), **S** (SEC filing) or **B+S** (both agree). Each row shows
the BPIQ and SEC retrieval times. Transaction and filing dates are separate columns. When SEC enrichment is off or
fails, the panel shows: "BPIQ identifies shares acquired or disposed of but does not provide enough detail to classify
the transaction. Filing links and subsequent holdings are unavailable from this source."

## Optional AI "Explain and challenge" (OpenAI)

- Off unless `OPENAI_API_KEY`, `OPENAI_MODEL`, and both per-million-token prices are set. Manual trigger only.
- Budget: **$5/month** (confirmed). Before a call, the worst-case cost (estimated input + max output tokens) must
  fit the remaining budget; actual usage is recorded after each call.
- Input is the deterministic evidence pack (supporting, against, missing/stale, changes, invalidation, context),
  passed as untrusted JSON data. Output must cite evidence ids; uncited points and points containing price targets,
  probabilities, guarantees or buy/sell language are removed and counted.
- The evidence pack and every other page work without AI or when generation fails.

### Ask (free-form question, added 2026-09-14)

- **Ask** sends your question plus a labelled data pack built by the app to the OpenAI Responses API. The pack contains the overview, screening criteria, all catalysts and revisions, historical catalysts, price metrics and gaps, MCP financials, insider summary and recent rows, the latest two fund-holding quarters, provider flags, the evidence items, and changes since your saved analysis. **Summarize info** (formerly Generate) is unchanged.
- **Web research:** the `web_search` tool is enabled only when `OPENAI_WEB_SEARCH_USD_PER_CALL` is set, and is limited to `AI_ASK_MAX_SEARCHES` calls. Without that price, Ask answers from the app's data only.
- **Budget:** the worst-case estimate includes the data pack, a 30,000-token allowance for web content, the maximum output, and the maximum searches. Actual tokens and searches are recorded after the call.
- **Answer rules:**
  - The model must answer the question directly. For "is it worth investing" it gives a reasoned assessment under uncertainty.
  - It cites `[E#]` / `[D:section]` data items and web links, and keeps data, web facts and interpretation distinct.
  - Sentences with invented probabilities or odds, price targets, or guarantees are removed and counted.
  - Company data and web pages are treated as untrusted text.
- OpenAI does not allow JSON mode together with web search (observed 2026-09-14), so answers are plain text. The UI renders headings, bullets, links and evidence tags without HTML injection.
