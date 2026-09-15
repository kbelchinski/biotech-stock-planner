import json
import time
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.domain.models import SourceRef
from app.providers.bpiq_mcp.oauth import McpAuthError, McpOAuth, TokenStore
from app.providers.bpiq_mcp.research import McpResearch, normalize_financials, normalize_funds, normalize_insiders
from app.providers.errors import ErrorKind, ProviderError
from tests.helpers import demo_settings

MCP_URL = "https://mcp.example.com/mcp"
SOURCE = SourceRef(provider="BPIQ MCP")


class FakeMcp:
    def __init__(self, *, reject=False):
        self.reject = reject
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/mcp":
            return httpx.Response(404)
        auth = request.headers.get("authorization")
        if self.reject or auth not in ("Bearer good-token", "Token test-key"):
            return httpx.Response(401, json={"error": "invalid_token"})
        body = json.loads(request.content)
        self.calls.append(body["method"])
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}, "capabilities": {}}
            sse = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})}\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream", "mcp-session-id": "s1"})
        if body["method"] == "tools/list":
            tools = [
                {"name": "get_company_financials", "description": "x", "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}}}},
                {"name": "insider_trades", "inputSchema": {"type": "object", "properties": {"symbols": {"type": "array"}}}},
            ]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        if body["method"] == "tools/call":
            assert request.headers.get("mcp-session-id") == "s1"
            payload = {"cash": 120_000_000, "ttm_burn": 60_000_000, "finance_updated_at": "2026-06-30"}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})
        return httpx.Response(400)


def research(tmp_path, handler, **overrides):
    settings = demo_settings(tmp_path, bpiq_mcp_url=MCP_URL, http_max_retries=0, **overrides)
    store: dict = {}
    r = McpResearch(settings, get_value=store.get, set_value=store.__setitem__, transport=httpx.MockTransport(handler))
    return r


def connect(r):
    r.oauth._store.save({"access_token": "good-token", "client_id": "c", "token_endpoint": "https://mcp.example.com/token"})


async def test_api_key_connects_without_oauth(tmp_path):
    from pydantic import SecretStr

    fake = FakeMcp()
    r = research(tmp_path, fake, bpiq_api_key=SecretStr("test-key"))
    assert r.status()["connected"] is True and r.status()["auth_method"] == "api_key"
    await r.discover()
    assert "tools/list" in fake.calls


async def test_unconfigured_and_unconnected_are_explicit(tmp_path):
    plain = McpResearch(demo_settings(tmp_path), get_value=lambda k: None, set_value=lambda k, v: None)
    assert plain.status()["configured"] is False
    out = await plain.research("AAAA", today=date(2026, 9, 14))
    assert out["financials"]["state"] == "unavailable" and "not configured" in out["financials"]["reason"]
    r = research(tmp_path, FakeMcp())
    out = await r.research("AAAA", today=date(2026, 9, 14))
    assert "not connected" in out["insiders"]["reason"]


async def test_discovery_requires_confirmation_before_calls(tmp_path):
    fake = FakeMcp()
    r = research(tmp_path, fake)
    connect(r)
    status = (await r.discover()) and r.status()
    assert [t["name"] for t in status["tools"]] == ["get_company_financials", "insider_trades"]
    assert status["suggestions"]["financials"] == ["get_company_financials"]
    out = await r.research("AAAA", today=date(2026, 9, 14))
    assert "No MCP tool selected" in out["financials"]["reason"]
    r.save_mapping({"financials": {"tool": "get_company_financials", "confirmed": False}})
    out = await r.research("AAAA", today=date(2026, 9, 14))
    assert "selected but not confirmed" in out["financials"]["reason"]
    assert "tools/call" not in fake.calls
    with pytest.raises(ValueError):
        r.save_mapping({"financials": {"tool": "made_up", "confirmed": True}})
    r.save_mapping({"financials": {"tool": "get_company_financials", "confirmed": True}})
    out = await r.research("AAAA", today=date(2026, 9, 14))
    fin = out["financials"]
    assert fin["state"] == "ok" and fin["tool"] == "get_company_financials"
    runway = next(x for x in fin["records"] if x["key"] == "runway_calculated")
    assert runway["value"] == 24.0 and runway["provenance"] == "calculated" and runway["mapping_verified"] is False
    assert "not reduced" in runway["note"]


async def test_rejected_token_is_auth_error(tmp_path):
    r = research(tmp_path, FakeMcp(reject=True))
    connect(r)
    with pytest.raises(ProviderError) as exc:
        await r.discover()
    assert exc.value.kind is ErrorKind.AUTH


def test_financial_normalizer_never_labels_missing_as_safe():
    today = date(2026, 9, 14)
    assert normalize_financials({}, source=SOURCE, today=today) == []
    negative = normalize_financials({"cash": 10, "quarterly_burn": -5}, source=SOURCE, today=today)
    runway = next(m for m in negative if m.key == "runway_calculated")
    assert runway.value is None and "not a safety signal" in runway.note
    unknown_period = normalize_financials({"cash": 10, "burn": 5}, source=SOURCE, today=today)
    assert "burn period" in next(m for m in unknown_period if m.key == "runway_calculated").note
    stale = normalize_financials({"cash": 10, "finance_updated_at": "2025-12-31", "shelf": "S-3 $150M"}, source=SOURCE, today=today)
    assert "STALE" in next(m for m in stale if m.key == "reference_date").note
    assert "capacity" in next(m for m in stale if m.key == "shelf").note


def test_insider_types_are_not_all_purchases_and_fund_changes_use_prior_period():
    rows = normalize_insiders(
        [{"transaction_code": "P", "shares": 100, "price": 2}, {"transaction_code": "A", "shares": 5}, {"type": "Acquisition", "shares": 1}, {"shares": 2}, {}],
        source=SOURCE,
        today=date(2026, 9, 14),
    )
    assert [r.transaction_type for r in rows] == ["open_market_purchase", "award", "other", "unknown"]
    assert rows[0].value_usd == 200
    bpiq = normalize_insiders(
        [
            {
                "executive": "Jane Doe",
                "executive_title": "CEO",
                "shares": 50,
                "share_price": 4,
                "transaction_date": "2026-08-01",
                "acquisition_or_disposal": "A",
            }
        ],
        source=SOURCE,
        today=date(2026, 9, 14),
    )
    assert bpiq[0].insider_name == "Jane Doe" and bpiq[0].role == "CEO"
    # The A/D flag does not say how shares were acquired: never an open-market purchase.
    assert bpiq[0].price == 4 and bpiq[0].transaction_type == "acquisition_unspecified"
    award = normalize_insiders(
        [{"executive": "X", "shares": "100.0", "share_price": "0.0", "acquisition_or_disposal": "A", "security_type": "Common Stock"}, {"unrelated": 1}],
        source=SOURCE,
        today=date(2026, 9, 14),
    )
    assert len(award) == 1 and award[0].price is None and award[0].value_usd is None and "0" in award[0].note
    assert award[0].security_type == "Common Stock"
    quarterly = normalize_funds(
        {
            "2026Q2": [{"fund_name": "A Cap", "value": 6_731_000, "num_shares": 15_075}],
            "2026Q1": [{"fund_name": "A Cap", "value": 6_218_000, "num_shares": 13_716}, {"fund_name": "B Cap", "value": 1, "num_shares": 80_000}],
        },
        source=SOURCE,
        today=date(2026, 9, 14),
    )
    by = {(h.fund, h.period_end): h for h in quarterly}
    assert by[("A Cap", date(2026, 6, 30))].change_shares == 1_359
    gone = by[("B Cap", date(2026, 6, 30))]
    assert gone.shares is None and gone.change_shares is None and "not confirmed" in gone.change_basis
    assert "no comparable period" in by[("A Cap", date(2026, 3, 31))].change_basis
    assert normalize_financials({"id": 1, "name": "x"}, source=SOURCE, today=date(2026, 9, 14)) == []
    fin = normalize_financials({"cash": 100, "ttm_burn": 12, "qtr_burn": 4, "monthly_burn": 1}, source=SOURCE, today=date(2026, 9, 14))
    assert {m.key for m in fin} >= {"burn", "burn_quarterly", "burn_monthly"}
    assert "sign convention" in next(m for m in fin if m.key == "runway_calculated").note
    funds = normalize_funds(
        [{"fund": "F", "period_end": "2026-03-31", "shares": 100}, {"fund": "F", "period_end": "2026-06-30", "shares": 150, "filing_date": "2026-08-14"}],
        source=SOURCE,
        today=date(2026, 9, 14),
    )
    latest = max(funds, key=lambda h: h.period_end)
    assert latest.change_shares == 50 and "2026-03-31" in latest.change_basis and latest.filing_date == date(2026, 8, 14)


async def test_oauth_pkce_registration_token_and_refresh(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"authorization_servers": ["https://mcp.example.com/"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"authorization_endpoint": "https://mcp.example.com/authorize", "token_endpoint": "https://mcp.example.com/token", "registration_endpoint": "https://mcp.example.com/register"})
        if path == "/register":
            return httpx.Response(201, json={"client_id": "client-1"})
        if path == "/token":
            form = parse_qs(request.content.decode())
            seen.setdefault("grants", []).append(form["grant_type"][0])
            if form["grant_type"][0] == "authorization_code":
                assert form["code_verifier"][0]
            return httpx.Response(200, json={"access_token": f"tok-{len(seen['grants'])}", "refresh_token": "r1", "expires_in": 3600})
        return httpx.Response(404)

    store = TokenStore(tmp_path / "tokens.json")
    oauth = McpOAuth(mcp_url=MCP_URL, store=store, transport=httpx.MockTransport(handler))
    url = await oauth.start("http://127.0.0.1:8000/api/integrations/bpiq-mcp/callback")
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["client_id"] == ["client-1"]
    with pytest.raises(McpAuthError):
        await oauth.complete("code", "wrong-state")
    await oauth.complete("code", query["state"][0])
    assert await oauth.access_token() == "tok-1"
    assert "tok-1" not in json.dumps(oauth.public_state())
    data = store.load()
    data["expires_at"] = time.time() - 10
    store.save(data)
    assert await oauth.access_token() == "tok-2"
    assert seen["grants"] == ["authorization_code", "refresh_token"]
