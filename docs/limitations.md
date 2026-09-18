# Live-integration limitations and open decisions

Status as of 2026-09-13. No authenticated request has been made, so **live compatibility is unverified**. Run `python -m app.diagnostics --labels` once credentials are in `.env`.

## Coverage gaps under the current plans

1. **Cash runway is unavailable in live mode.** BPIQ Apex catalysts contain no cash or burn fields. Those exist only on `/all-companies/`, which requires API Premium. Alpaca has no fundamentals endpoint. Live scans report runway as *Unknown / unavailable*. With runway enabled, no live company can be better than *Insufficient data*. You can disable runway per scan; the UI then labels it *Not applied*. No other provider has been added.
2. **Estimated catalyst date ranges are not provided.** BPIQ documents only `catalyst_date: string | null`. Timing context exists only in the free-text `note`, which is not parsed. Undated catalysts are excluded, as decided, and counted in the scan notes.
3. **Exchange listing** is not in BPIQ Apex responses. It is verified through Alpaca Trading API `GET /v2/assets/{symbol}`, as approved. `ALPACA_ACCOUNT_TYPE` must match the keys (paper or live host).
4. **Biotech classification** is not verifiable per company under Apex (`industry` is an API Premium field). It is inferred from BPIQ catalyst coverage.

## Behaviour inferred from documentation, to confirm with authenticated requests

| Area | Assumption in code | Mitigation |
| --- | --- | --- |
| BPIQ `stage_label` / `event_label` vocabulary | Not documented. Conservative regex rules map labels to Phase 2 results, Phase 3 results, and PDUFA. | Unrecognized labels stay *Unknown*, never *Fail*. `--labels` prints what BPIQ actually returns. |
| BPIQ `catalyst_date_min/max` | `YYYY-MM-DD`, inclusive; null-date behaviour unknown | Boundaries are re-checked locally; null dates are excluded locally. |
| BPIQ `limit` maximum | Unknown; page size defaults to 50 | `next` links are followed until null, and pages are cross-checked against `count`. |
| BPIQ 429 / rate-limit headers | Not documented | Client-side throttle (15/min paid, 10/min trial); `Retry-After` honoured if present. |
| BPIQ `market_cap` | USD; no as-of timestamp | Retrieval time is shown with a note. |
| BPIQ trial beyond 30 days | Error vs. silent truncation not documented | The scan is refused up front with a subscription-limitation message (`BPIQ_ACCESS_TIER=apex_trial`). |
| Alpaca daily bar `t` | New York midnight → session date | Bars are also validated against the requested session range. |
| Alpaca daily bars and extended hours | Not documented whether daily `c`/`v`/`vw` include extended-hours trades | A session counts as completed only 4h15m after the regular close. The close may still differ from the official closing auction price. |
| Alpaca daily volume | FAQ: may differ from "normal" consolidated volume | Turnover is volume × VWAP from the same bar. Bars missing `vw` fall back to a labelled close × volume approximation. |
| Alpaca error body | `{"message": ...}` | Only used for display. |
| Alpaca Trading API rate limit | Not reviewed | Uses the same 180/min client-side budget. |

## Screening rules that need your confirmation

These are implemented deterministically and explained in the UI, but they were not explicit in the requirements:

1. **Liquidity with missing sessions:** missing bars never count as zero. A partial total is a lower bound, so the criterion passes if that lower bound already exceeds the threshold. Otherwise it is *Unknown*. It fails only on complete history.
2. **Runway when not burning cash:** zero or negative monthly net burn passes (runway not limited by burn).
3. **Runway reference date:** runway is `cash ÷ monthly net burn` as of the financial snapshot date. It is not reduced for time elapsed since then.
4. **Combined-phase labels** ("Phase 2/3", "Phase 1/2") are *Unknown* type until a mapping is agreed.
5. **Price threshold** is strictly above $1.00, using the raw (unadjusted) close of the latest completed session. Older closes are never substituted.
6. **Query range:** by default BPIQ is queried from today through the window end, so near-term catalysts appear as timing failures. Market cap is filtered locally by default, so failures are visible. Both are toggles under *Advanced query options*.

## Research features (added 2026-09-14)

1. **BPIQ MCP tools are unverified.** OAuth consent has not been completed, so no tool list, schema, or response has been inspected. Financial, insider, and fund panels show *Unknown / unavailable* until you connect, discover tools, and confirm a mapping. Even after that, normalized values are labelled *Unverified mapping* until the field names are checked against real responses and the code is updated.
   *Exception:* the insider tool's fields were verified on 2026-09-18. It returns only an acquired/disposed flag: no transaction code, filing link or holdings. See research-features.md.
2. **Cash runway in live screening is still unavailable.** MCP financials are shown on the research page only. They do not feed the runway criterion.
3. **Apex trial horizon:** watchlist refreshes see catalysts only within 30 days. Later events stay as last stored and are never marked "no longer returned".
4. **Outcome matching is heuristic:** same drug name within 14 days, because BPIQ does not link upcoming and historical record ids.
5. **Catalyst date precision is undocumented.** Exact dates and guided estimates cannot be distinguished. Ranges are supported by the model but not supplied by any connected source.
6. **Monitoring is not continuous.** It runs only while the backend process is running, with one catch-up run after a missed schedule. There are no external notifications.
7. **Paper tracking is forward-only.** It cannot validate the strategy historically. Overlapping trades are not modelled in drawdown. Fills use daily open/close bars, so there are no intraday stops.
8. **Split-adjusted history can change** after a new split. Paper-trade entries are re-read from the same series at exit; stored price context is recalculated on each visit.
9. **Provider storage terms were not reviewed.** Scan snapshots keep normalized BPIQ records and Alpaca bars locally.
10. **AI cost is an estimate** based on the per-token prices you configure and the token usage OpenAI reports.
11. **Only the documentation pages were rate-limited (429).** MCP details come from observed server metadata and a search result. Recheck https://app.bpiq.com/mcp-documentation manually.

## Operational notes

- The exchange calendar is the local `exchange_calendars` XNYS calendar. Keep the package updated so newly announced holidays are reflected.
- Adjustment is `raw` for both price and volume, so the two are mutually consistent. Turnover in dollars is unaffected by splits.
- The app has no authentication by design. Run it on `127.0.0.1` only; do not expose it to a network.
11. **SEC insider enrichment is limited to recent Form 4/4A filings** (last 365 days, at most `SEC_MAX_FILINGS`). Forms 3 and 5 are not read. Matching is deliberately strict: BPIQ rows with differently spelled names or rounded share counts stay unclassified rather than risk a wrong match.
