"""BPIQ MCP research adapter: connection status, tool discovery, confirmed tool mapping, normalization.

Status on 2026-09-14: no MCP tool schema or response has been verified (OAuth consent not yet
completed). Therefore:
- Tools are discovered at runtime and stored with their input/output schemas.
- A capability (financials, insiders, funds) is only called after the user confirms which tool
  serves it, having inspected the schema and a test response on the Integrations page.
- Normalizers map only exact field names listed below. Every normalized value is marked
  mapping_verified=False and shows its raw field name, until the mapping is checked against real
  responses and this module is updated.
- Anything not found is reported as unknown; absence of data is never treated as absence of risk.

Exception, 2026-09-18: fetch_company_insider_transactions was checked against its tool description and live
responses. Its 8 fields are mapped exactly (see BPIQ_INSIDER_FIELDS); everything else is "field unavailable".
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.domain.research import FinancialMeasure, FundHolding, InsiderTransaction, Provenance
from app.domain.models import SourceRef
from app.logging_setup import get_logger
from app.providers.bpiq_mcp.client import MCP_HINTS, PROVIDER, McpSession, tool_result_payload
from app.providers.bpiq_mcp.oauth import McpAuthError, McpOAuth, TokenStore
from app.providers.errors import ProviderError
from app.providers.http import ProviderHttpClient, RateLimiter, RetryPolicy

log = get_logger("bpiq_mcp.research")

CAPABILITIES = ("financials", "insiders", "funds")
TOOLS_FILE = "bpiq_mcp_tools.json"
TOKENS_FILE = "bpiq_mcp_tokens.json"
MAPPING_KEY = "bpiq_mcp_capabilities"
STALE_FINANCIALS_DAYS = 120
TICKER_ARGUMENT_NAMES = ("ticker", "symbol", "tickers", "symbols")

# Candidate raw field names (exact, case-insensitive). UNVERIFIED against live MCP responses.
FIN_FIELDS: dict[str, tuple[str, str | None, tuple[str, ...]]] = {
    "cash": ("Cash balance", "USD (assumed; not verified)", ("cash", "cash_and_equivalents", "cash_and_cash_equivalents", "cash_usd")),
    # fetch_company_info (observed 2026-09-14) returns cash, ttm_burn, qtr_burn, monthly_burn, finance_updated_at.
    # The burn sign convention is NOT documented (a profitable company showed a positive ttm_burn).
    "burn": ("Cash burn, trailing 12 months (as reported)", "USD, sign convention not documented", ("ttm_burn", "burn", "cash_burn")),
    "burn_quarterly": ("Cash burn, quarter (as reported)", "USD, sign convention not documented", ("qtr_burn", "quarterly_burn")),
    "burn_monthly": ("Cash burn, monthly (as reported)", "USD, sign convention not documented", ("monthly_burn",)),
    "runway": ("Cash runway (provider-reported)", "as reported (not verified)", ("cash_runway", "runway", "runway_months", "cash_runway_months")),
    "reference_date": ("Financial reference date", None, ("finance_updated_at", "financials_updated_at", "period_end", "report_date", "as_of", "fiscal_period_end")),
    "financing": ("Announced financing", None, ("financing", "offering", "recent_offering", "announced_financing")),
    "shelf": ("Shelf registration", None, ("shelf", "shelf_registration", "s3_shelf")),
    "atm": ("At-the-market (ATM) program", None, ("atm", "atm_program", "at_the_market")),
}
BURN_PERIOD_MONTHS = {"ttm_burn": 12, "qtr_burn": 3, "quarterly_burn": 3, "monthly_burn": 1}
QUARTER_KEY = re.compile(r"^(\d{4})Q([1-4])$")


class McpResearch:
    def __init__(
        self,
        settings: Settings,
        *,
        get_value: Callable[[str], Any],
        set_value: Callable[[str, Any], None],
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._get_value = get_value
        self._set_value = set_value
        self._transport = transport
        self._clock = clock
        self._lock = asyncio.Lock()
        data_dir: Path = settings.data_dir
        self._tools_path = data_dir / TOOLS_FILE
        self.oauth = McpOAuth(mcp_url=settings.bpiq_mcp_url, store=TokenStore(data_dir / TOKENS_FILE), transport=transport) if settings.bpiq_mcp_url else None

    # ------------------------------------------------------------ status and discovery

    @property
    def configured(self) -> bool:
        return self.oauth is not None

    def _api_key(self) -> str | None:
        key = self._settings.bpiq_api_key
        if key and (value := key.get_secret_value().strip()):
            return value
        return None

    def connected(self) -> bool:
        return bool(self._api_key()) or bool(self.oauth and self.oauth.connected())

    def auth_method(self) -> str | None:
        if self._api_key():
            return "api_key"
        if self.oauth and self.oauth.connected():
            return "oauth"
        return None

    def discovered(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._tools_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def mapping(self) -> dict[str, dict[str, Any]]:
        stored = self._get_value(MAPPING_KEY) or {}
        return {cap: stored.get(cap) or {"tool": None, "confirmed": False} for cap in CAPABILITIES}

    def save_mapping(self, mapping: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        tools = {t["name"] for t in (self.discovered() or {}).get("tools", [])}
        clean: dict[str, dict[str, Any]] = {}
        for cap in CAPABILITIES:
            entry = mapping.get(cap) or {}
            tool = entry.get("tool")
            if tool is not None and tool not in tools:
                raise ValueError(f"Tool {tool!r} is not in the discovered tool list.")
            clean[cap] = {"tool": tool, "confirmed": bool(entry.get("confirmed") and tool), "argument": entry.get("argument")}
        self._set_value(MAPPING_KEY, clean)
        return clean

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "connected": False,
                "message": "BPIQ MCP is optional and not configured. Set BPIQ_MCP_URL in .env to enable the connection flow.",
            }
        discovered = self.discovered()
        method = self.auth_method()
        return {
            "configured": True,
            "url": self._settings.bpiq_mcp_url,
            **self.oauth.public_state(),  # type: ignore[union-attr]
            "connected": self.connected(),
            "auth_method": method,
            "message": (
                "Using your BPIQ API key. Click Discover tools — OAuth Connect is not needed for this app."
                if method == "api_key"
                else None
            ),
            "discovered_at": discovered.get("discovered_at") if discovered else None,
            "server_info": discovered.get("server_info") if discovered else None,
            "tools": discovered.get("tools", []) if discovered else [],
            "suggestions": self.suggestions(discovered),
            "mapping": self.mapping(),
            "verification_note": (
                "Tool names and schemas are shown exactly as discovered. Field mappings are unverified until "
                "checked against a real response."
            ),
        }

    @staticmethod
    def suggestions(discovered: dict[str, Any] | None) -> dict[str, list[str]]:
        """Name-based hints only. Nothing is called until the user confirms a tool."""
        names = [t["name"] for t in (discovered or {}).get("tools", [])]
        words = {
            "financials": ("financ", "cash", "runway", "company_info", "analysis"),
            "insiders": ("insider",),
            "funds": ("hedge", "fund", "13f", "holding"),
        }
        return {cap: [n for n in names if any(w in n.lower() for w in ws)] for cap, ws in words.items()}

    def _http(self) -> ProviderHttpClient:
        return ProviderHttpClient(
            provider=PROVIDER,
            client=httpx.AsyncClient(timeout=httpx.Timeout(self._settings.http_timeout_seconds), follow_redirects=False, transport=self._transport),
            retry=RetryPolicy(max_retries=self._settings.http_max_retries),
            rate_limiter=RateLimiter(self._settings.bpiq_mcp_rate_limit_per_min, 60.0),
            hints=MCP_HINTS,
        )

    async def _credential(self) -> str:
        if key := self._api_key():
            return key
        if self.oauth:
            return await self.oauth.access_token()
        raise McpAuthError("BPIQ MCP is not configured.")

    async def _session(self) -> tuple[McpSession, ProviderHttpClient]:
        if not self.configured:
            raise McpAuthError("BPIQ MCP is not configured.")
        if not self.connected():
            raise McpAuthError("BPIQ MCP is configured but not connected.")
        http = self._http()
        session = McpSession(
            http,
            url=self._settings.bpiq_mcp_url,  # type: ignore[arg-type]
            token=self._credential,
            scheme="Token" if self._api_key() else "Bearer",
        )
        try:
            await session.initialize()
        except BaseException:
            await http.aclose()
            raise
        return session, http

    async def discover(self) -> dict[str, Any]:
        async with self._lock:
            session, http = await self._session()
            try:
                tools = await session.list_tools()
            finally:
                await http.aclose()
            data = {
                "discovered_at": self._clock().isoformat(),
                "server_info": session.server_info,
                "tools": [
                    {
                        "name": t["name"],
                        "title": t.get("title"),
                        "description": t.get("description"),
                        "input_schema": t.get("inputSchema"),
                        "output_schema": t.get("outputSchema"),
                    }
                    for t in tools
                ],
            }
            self._tools_path.parent.mkdir(parents=True, exist_ok=True)
            self._tools_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            log.info("BPIQ MCP discovery: %s tools", len(tools))
            return data

    async def call_raw(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tools = {t["name"]: t for t in (self.discovered() or {}).get("tools", [])}
        if name not in tools:
            raise ValueError("Run discovery first; the tool is not in the discovered list.")
        async with self._lock:
            session, http = await self._session()
            try:
                result = await session.call_tool(name, arguments)
            finally:
                await http.aclose()
        return {"tool": name, "arguments": arguments, "retrieved_at": self._clock().isoformat(), "payload": tool_result_payload(result)}

    # ------------------------------------------------------------ capabilities

    def _ticker_arguments(self, tool: dict[str, Any], ticker: str, override: str | None) -> dict[str, Any] | None:
        props = (tool.get("input_schema") or {}).get("properties") or {}
        name = override if override in props else next((n for n in TICKER_ARGUMENT_NAMES if n in props), None)
        if name is None:
            return None
        kind = (props.get(name) or {}).get("type")
        return {name: [ticker] if kind == "array" else ticker}

    async def research(self, ticker: str, *, today: date) -> dict[str, Any]:
        """Run every confirmed capability for one ticker. Failures are isolated per capability."""
        out: dict[str, Any] = {cap: {"state": "unavailable", "reason": None, "records": []} for cap in CAPABILITIES}
        if not self.configured:
            for cap in CAPABILITIES:
                out[cap]["reason"] = "BPIQ MCP is not configured."
            return out
        if not self.connected():
            for cap in CAPABILITIES:
                out[cap]["reason"] = "BPIQ MCP is configured but not connected."
            return out
        tools = {t["name"]: t for t in (self.discovered() or {}).get("tools", [])}
        for cap, entry in self.mapping().items():
            tool = tools.get(entry.get("tool") or "")
            if entry.get("tool") and tool is None:
                out[cap]["reason"] = f"Mapped tool {entry['tool']} is not in the latest discovered tool list. Run Discover tools again."
                continue
            if tool is None:
                out[cap]["reason"] = "No MCP tool selected for this capability yet (Data & integrations → Capability mapping)."
                continue
            if not entry.get("confirmed"):
                out[cap]["reason"] = (
                    f"Tool {tool['name']} is selected but not confirmed. On Data & integrations, tick "
                    "“I inspected the schema and a test response” and select Save mapping."
                )
                continue
            args = self._ticker_arguments(tool, ticker, entry.get("argument"))
            if args is None:
                out[cap]["reason"] = f"Tool {tool['name']} has no ticker/symbol argument in its discovered schema."
                continue
            try:
                raw = await self.call_raw(tool["name"], args)
            except (ProviderError, McpAuthError, ValueError) as exc:
                out[cap] = {"state": "error", "reason": getattr(exc, "message", str(exc)), "records": []}
                continue
            source = SourceRef(
                provider=PROVIDER,
                endpoint=f"tools/call {tool['name']}",
                retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
                timestamp_note=(
                    "Insider fields verified 2026-09-18 against the tool description and live responses."
                    if cap == "insiders"
                    else "Unverified MCP field mapping; see the raw field name on each value."
                ),
            )
            normalizer = {"financials": normalize_financials, "insiders": normalize_insiders, "funds": normalize_funds}[cap]
            records = normalizer(raw["payload"], source=source, today=today)
            out[cap] = {
                "state": "ok" if records else "empty",
                "reason": None if records else "The tool returned no recognizable records. Missing data is not evidence of low risk.",
                "tool": tool["name"],
                "records": [r.model_dump(mode="json") for r in records],
                "raw_preview": json.dumps(raw["payload"], default=str)[:4000],
            }
            if cap == "insiders" and len(records) >= BPIQ_OBSERVED_ROW_CAP:
                out[cap]["notes"] = [BPIQ_ROW_CAP_NOTE.format(n=len(records))]
        return out


# ---------------------------------------------------------------- normalizers (unverified mappings)


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "records", "transactions", "holdings", "insider_transactions", "insiders"):
            if isinstance(payload.get(key), list):
                return [p for p in payload[key] if isinstance(p, dict)]
        return [payload]
    return []


def _find(record: dict[str, Any], names: tuple[str, ...]) -> tuple[str | None, Any]:
    lower = {k.lower(): k for k in record}
    for name in names:
        if name in lower and record[lower[name]] not in (None, ""):
            return lower[name], record[lower[name]]
    return None, None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("$", ""))
        except ValueError:
            return None
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def normalize_financials(payload: Any, *, source: SourceRef, today: date) -> list[FinancialMeasure]:
    records = _records(payload)
    if not records:
        return []
    record = records[0]
    measures: list[FinancialMeasure] = []
    ref_field, ref_raw = _find(record, FIN_FIELDS["reference_date"][2])
    ref_date = _date(ref_raw)
    found: dict[str, tuple[str, Any]] = {}
    for key, (label, units, names) in FIN_FIELDS.items():
        raw_field, value = _find(record, names)
        if raw_field is None:
            continue
        found[key] = (raw_field, value)
        numeric = _number(value)
        note = None
        if key == "reference_date" and ref_date and (today - ref_date).days > STALE_FINANCIALS_DAYS:
            note = f"STALE: reference date is more than {STALE_FINANCIALS_DAYS} days old."
        if key in ("shelf", "atm"):
            note = "Financing capacity, not an announced issuance."
        if key == "financing":
            note = "Reported financing text/value as provided; not the same as shelf or ATM capacity."
        measures.append(
            FinancialMeasure(
                key=key,
                label=label,
                value=numeric if numeric is not None and key not in ("reference_date", "financing", "shelf", "atm") else (str(value) if not isinstance(value, (int, float)) else value),
                units=units,
                reference_period=None,
                reference_date=ref_date,
                provenance=Provenance.PROVIDER_REPORTED,
                mapping_verified=False,
                raw_field=raw_field,
                note=note,
                source=source,
            )
        )
    if not found:
        # Nothing recognizable: report unknown rather than an "ok" panel containing only a blank runway row.
        return []

    cash = _number(found.get("cash", (None, None))[1])
    # Prefer trailing-12-month burn; fall back to quarterly, then monthly.
    burn_field, burn_raw = next(
        (found[k] for k in ("burn", "burn_quarterly", "burn_monthly") if k in found), (None, None)
    )
    burn = _number(burn_raw)
    months = BURN_PERIOD_MONTHS.get((burn_field or "").lower())
    runway = FinancialMeasure(
        key="runway_calculated",
        label="Cash runway (calculated)",
        value=None,
        units="months",
        reference_period=f"{months}-month burn period" if months else None,
        reference_date=ref_date,
        provenance=Provenance.CALCULATED,
        mapping_verified=False,
        raw_field=None,
        formula="cash ÷ (burn over period ÷ months in period); assumes positive burn means cash consumed — BPIQ does not document this",
        source=source,
    )
    if cash is None or burn is None:
        runway.note = "Not calculated: cash or burn is unavailable from connected sources."
    elif months is None:
        runway.note = f"Not calculated: the burn period of field {burn_field!r} is not documented."
    elif burn <= 0:
        runway.note = "Not calculated: reported burn is zero or negative (no cash consumption in that period). This is not a safety signal."
    else:
        runway.value = cash / (burn / months)
        if ref_date is None:
            runway.note = "No reference date; runway is not reduced for time elapsed."
        else:
            elapsed = (today - ref_date).days / 30.44
            runway.note = f"As of {ref_date}; not reduced for the {elapsed:.1f} months elapsed since then."
        runway.note += (
            " UNVERIFIED sign convention: if the provider reports burn as a positive number for cash-generating "
            "companies, this runway is meaningless. Check against the company's filings."
        )
    measures.append(runway)
    return measures


# fetch_company_insider_transactions: VERIFIED 2026-09-18 against the tool description and live responses (VRTX, SRPT;
# 250 rows each). Every row has exactly these 8 string fields and nothing else:
BPIQ_INSIDER_FIELDS = (
    "shares", "ticker", "executive", "share_price", "security_type", "executive_title", "transaction_date", "acquisition_or_disposal",
)
# Information the provider does NOT return (field unavailable, not an unverified mapping).
BPIQ_INSIDER_UNAVAILABLE = (
    "transaction_code", "filing_date", "accession_number", "source_url", "shares_owned_after", "direct_or_indirect", "footnotes",
    "reporting_owner_cik", "issuer_cik", "amendment_status",
)
BPIQ_ROW_CAP_NOTE = (
    "BPIQ returned {n} rows, which matches the row count observed as a limit on 2026-09-18; older transactions in the "
    "provider's four-quarter window may be missing."
)
BPIQ_OBSERVED_ROW_CAP = 250


def _insider_rows(payload: Any) -> list[dict[str, Any]]:
    rows = _records(payload)
    if len(rows) == 1 and _find(rows[0], ("shares", "executive", "transaction_date", "share_price"))[0] is None:
        nested: list[dict[str, Any]] = []
        for value in rows[0].values():
            if isinstance(value, list):
                nested.extend(_records(value))
        if nested:
            return nested
    return rows


def normalize_insiders(payload: Any, *, source: SourceRef, today: date) -> list[InsiderTransaction]:
    """Map the verified BPIQ fields only. The A/D flag is never turned into a purchase, award, exercise or sale."""
    out = []
    for r in _insider_rows(payload):
        name = _str(_find(r, ("executive",))[1])
        tx_date = _date(_find(r, ("transaction_date",))[1])
        shares = _number(_find(r, ("shares",))[1])
        if name is None and tx_date is None and shares is None:
            continue  # not a recognizable transaction row
        flag = (_str(_find(r, ("acquisition_or_disposal",))[1]) or "").upper()[:1]
        acquired_disposed = flag if flag in ("A", "D") else None
        kind = {"A": "acquired_type_unknown", "D": "disposed_type_unknown"}.get(flag, "unknown")
        label = {
            "A": "Acquired — transaction type unknown",
            "D": "Disposed — transaction type unknown",
        }.get(flag, "Transaction type unknown")
        price = _number(_find(r, ("share_price",))[1])
        note = None
        if price is not None and price <= 0:
            # 0.0 is how BPIQ reports rows whose filing has no cash price (e.g. awards, gifts). Not a real price of zero.
            note = "BPIQ reports a price of 0; treated as no reported price."
            price = None
        fields = {k: "bpiq" for k, v in (
            ("insider_name", name), ("role", _find(r, ("executive_title",))[1]), ("transaction_date", tx_date), ("shares", shares),
            ("price", price), ("security_type", _find(r, ("security_type",))[1]), ("acquired_disposed", acquired_disposed),
        ) if v is not None}
        out.append(
            InsiderTransaction(
                origin="bpiq",
                insider_name=name,
                role=_str(_find(r, ("executive_title",))[1]),
                transaction_code=None,
                transaction_label=label,
                transaction_type=kind,  # type: ignore[arg-type]
                acquired_disposed=acquired_disposed,  # type: ignore[arg-type]
                transaction_date=tx_date,
                security_type=_str(_find(r, ("security_type",))[1]),
                note=note,
                filing_date=None,
                shares=shares,
                price=price,
                value_usd=shares * price if shares is not None and price is not None else None,
                shares_owned_after=None,
                source_url=None,
                mapping_verified=True,
                source=source,
                field_sources=fields,
                unavailable_fields=list(BPIQ_INSIDER_UNAVAILABLE),
            )
        )
    return out


def _quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 30 if quarter in (2, 3) else 31)


def _normalize_quarterly_funds(payload: dict[str, Any], *, source: SourceRef) -> list[FundHolding]:
    """fetch_company_hedge_fund_holdings (observed 2026-09-14): {"2026Q2": [{fund_name, value, num_shares}], ...}.

    Changes are calculated only against the immediately preceding calendar quarter, and only when that quarter is
    present in the response. A fund listed in the prior quarter but not the current one is reported as "not listed",
    never as a confirmed exit. `value` appears to be USD (value ÷ shares matched the share price); not documented.
    """
    quarters: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for key, rows in payload.items():
        match = QUARTER_KEY.match(str(key))
        if match and isinstance(rows, list):
            quarters[(int(match.group(1)), int(match.group(2)))] = [r for r in rows if isinstance(r, dict)]
    out: list[FundHolding] = []
    for (year, q) in sorted(quarters, reverse=True):
        prev_key = (year, q - 1) if q > 1 else (year - 1, 4)
        prev_rows = quarters.get(prev_key)
        prev_by_fund = {_str(r.get("fund_name")): r for r in prev_rows or []}
        label = f"{prev_key[0]}Q{prev_key[1]}"
        current_funds = set()
        for r in quarters[(year, q)]:
            fund = _str(_find(r, ("fund_name", "fund", "name"))[1])
            current_funds.add(fund)
            shares = _number(_find(r, ("num_shares", "shares"))[1])
            prev = prev_by_fund.get(fund)
            prev_shares = _number(_find(prev, ("num_shares", "shares"))[1]) if prev else None
            if prev_rows is None:
                change, basis = None, f"{label} not in response; no comparable period"
            elif prev is None:
                change, basis = None, f"Not listed in {label} (new position or not previously reported)"
            elif shares is not None and prev_shares is not None:
                change, basis = shares - prev_shares, f"Calculated vs {label} ({prev_shares:,.0f} sh)"
            else:
                change, basis = None, f"Share count missing in {label} or current quarter"
            out.append(
                FundHolding(
                    fund=fund,
                    period_end=_quarter_end(year, q),
                    filing_date=None,
                    shares=shares,
                    value_usd=_number(_find(r, ("value", "market_value"))[1]),
                    change_shares=change,
                    change_basis=basis,
                    mapping_verified=False,
                    source=source,
                )
            )
        for fund, prev in prev_by_fund.items():
            if fund not in current_funds:
                out.append(
                    FundHolding(
                        fund=fund,
                        period_end=_quarter_end(year, q),
                        filing_date=None,
                        shares=None,
                        value_usd=None,
                        change_shares=None,
                        change_basis=(
                            f"Listed in {label} ({_number(prev.get('num_shares')) or 0:,.0f} sh) but not in {year}Q{q}; "
                            "may have exited or not reported — not confirmed"
                        ),
                        mapping_verified=False,
                        source=source,
                    )
                )
    return out


def normalize_funds(payload: Any, *, source: SourceRef, today: date) -> list[FundHolding]:
    if isinstance(payload, dict) and payload and any(QUARTER_KEY.match(str(k)) for k in payload):
        return _normalize_quarterly_funds(payload, source=source)
    rows = []
    for r in _records(payload):
        if _find(r, ("fund", "fund_name", "hedge_fund", "manager", "name"))[0] is None:
            continue  # not a recognizable holding row
        rows.append(
            FundHolding(
                fund=_str(_find(r, ("fund", "fund_name", "hedge_fund", "manager", "name"))[1]),
                period_end=_date(_find(r, ("period_end", "report_period", "period", "quarter_end", "as_of"))[1]),
                filing_date=_date(_find(r, ("filing_date", "filed_at", "filed"))[1]),
                shares=_number(_find(r, ("shares", "position_shares", "quantity"))[1]),
                value_usd=_number(_find(r, ("value", "market_value", "position_value"))[1]),
                change_shares=_number(_find(r, ("change_shares", "share_change", "shares_change"))[1]),
                change_basis="As reported by the provider (comparison period not verified)"
                if _find(r, ("change_shares", "share_change", "shares_change"))[0]
                else None,
                mapping_verified=False,
                source=source,
            )
        )
    # Where the provider gave no change, compute it only against the immediately preceding period of the same fund.
    by_fund: dict[str | None, list[FundHolding]] = {}
    for h in rows:
        by_fund.setdefault(h.fund, []).append(h)
    for holdings in by_fund.values():
        dated = sorted((h for h in holdings if h.period_end), key=lambda h: h.period_end)  # type: ignore[arg-type,return-value]
        for prev, cur in zip(dated, dated[1:]):
            if cur.change_shares is None and cur.shares is not None and prev.shares is not None:
                cur.change_shares = cur.shares - prev.shares
                cur.change_basis = f"Calculated vs period ending {prev.period_end}"
    return rows


def _str(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None
