from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx
import pytest

from app.config import BpiqAccessTier
from app.domain.models import CatalystType, ClassificationStatus
from app.fixtures.transport import render_catalyst_records
from app.providers.bpiq.client import BPIQ_HINTS, BpiqClient
from app.providers.bpiq.normalize import NormalizedCatalyst, RejectedRecord, normalize_catalyst
from app.providers.errors import ErrorKind, ProviderError
from tests.helpers import SCAN_TODAY, SleepRecorder, bpiq_record, make_http

BASE = "https://api.bpiq.com/api/v1/info"
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def paged_handler(records, page_size, requests, *, count=None, next_host="api.bpiq.com"):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params.get("offset", "0"))
        page = records[offset : offset + page_size]
        more = offset + page_size < len(records)
        params = dict(request.url.params)
        params["offset"] = str(offset + page_size)
        return httpx.Response(
            200,
            json={
                "count": len(records) if count is None else count,
                "next": f"https://{next_host}/api/v1/info/catalysts/?{urlencode(params)}" if more else None,
                "previous": None,
                "results": page,
            },
        )

    return handler


def client(handler, *, tier=BpiqAccessTier.APEX_PAID, sleep=None, page_size=2):
    http = make_http("BPIQ Apex REST", handler, headers={"Authorization": "Token k"}, sleep=sleep, hints=BPIQ_HINTS)
    return BpiqClient(http, base_url=BASE, access_tier=tier, page_size=page_size, clock=lambda: NOW)


async def fetch(c, days=90):
    return await c.fetch_upcoming_catalysts(today=SCAN_TODAY, date_min=SCAN_TODAY, date_max=SCAN_TODAY + timedelta(days=days))


async def test_paginates_through_all_pages_with_documented_params():
    records = [bpiq_record(i, f"T{i}") for i in range(1, 6)]
    requests: list[httpx.Request] = []
    result = await fetch(client(paged_handler(records, 2, requests)))
    assert result.pages == 3
    assert [c.provider_record_id for c in result.catalysts] == [1, 2, 3, 4, 5]
    first = requests[0].url.params
    assert first["catalyst_date_min"] == "2026-09-14"
    assert first["catalyst_date_max"] == "2026-12-13"
    assert first["limit"] == "2" and first["offset"] == "0"
    assert "market_cap_min" not in first
    assert requests[0].headers["Authorization"] == "Token k"
    assert result.complete


async def test_market_cap_filter_params_are_integers():
    requests: list[httpx.Request] = []
    c = client(paged_handler([], 2, requests))
    await c.fetch_upcoming_catalysts(today=SCAN_TODAY, date_min=SCAN_TODAY, date_max=SCAN_TODAY, market_cap_min=50e6, market_cap_max=2e9)
    assert requests[0].url.params["market_cap_min"] == "50000000"
    assert requests[0].url.params["market_cap_max"] == "2000000000"


async def test_duplicates_undated_and_malformed_records():
    records = [
        bpiq_record(1, "AAA"),
        bpiq_record(1, "AAA"),
        bpiq_record(2, "BBB", catalyst_date=None),
        bpiq_record(3, "CCC", market_cap="412M"),
        bpiq_record(4, "DDD", catalyst_date="11/20/2026"),
    ]
    result = await fetch(client(paged_handler(records, 2, [])))
    assert [c.ticker for c in result.catalysts] == ["AAA"]
    assert result.duplicates == 1
    assert result.undated_excluded == 1
    assert {r.record_id for r in result.rejected} == {3, 4}
    assert result.received_records == 5


async def test_incomplete_pagination_is_detected():
    result = await fetch(client(paged_handler([bpiq_record(1)], 2, [], count=10)))
    assert not result.complete


async def test_refuses_to_follow_next_link_to_another_host():
    requests: list[httpx.Request] = []
    records = [bpiq_record(i, f"T{i}") for i in range(1, 4)]
    with pytest.raises(ProviderError) as info:
        await fetch(client(paged_handler(records, 2, requests, next_host="evil.example.com")))
    assert info.value.kind is ErrorKind.INVALID_RESPONSE
    assert all(r.url.host == "api.bpiq.com" for r in requests)


async def test_trial_window_limit_is_reported_before_any_request():
    requests: list[httpx.Request] = []
    c = client(paged_handler([], 2, requests), tier=BpiqAccessTier.APEX_TRIAL)
    with pytest.raises(ProviderError) as info:
        await fetch(c, days=90)
    assert info.value.kind is ErrorKind.SUBSCRIPTION_LIMIT
    assert "30 days" in info.value.message
    assert requests == []
    await fetch(c, days=30)
    assert len(requests) == 1


async def test_auth_error_has_actionable_hint():
    c = client(lambda r: httpx.Response(401, json={"detail": "Invalid token."}))
    with pytest.raises(ProviderError) as info:
        await fetch(c)
    assert info.value.kind is ErrorKind.AUTH
    assert "BPIQ_API_KEY" in info.value.hint


async def test_rate_limit_retry_then_success():
    sleep = SleepRecorder()
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "Request was throttled."}),
            httpx.Response(200, json={"count": 1, "next": None, "previous": None, "results": [bpiq_record(1)]}),
        ]
    )
    result = await fetch(client(lambda r: next(responses), sleep=sleep))
    assert len(result.catalysts) == 1
    assert sleep.calls == [2.0]


async def test_invalid_envelope_is_rejected():
    c = client(lambda r: httpx.Response(200, json={"results": []}))
    with pytest.raises(ProviderError) as info:
        await fetch(c)
    assert info.value.kind is ErrorKind.INVALID_RESPONSE


def test_normalization_preserves_identifiers_sources_and_types():
    raw = bpiq_record(272, "amrx", catalyst_date="2026-11-20", stage="NDA Filing", event="PDUFA", market_cap=4558262272, extra_field="kept")
    normalized = normalize_catalyst(raw, NOW)
    assert isinstance(normalized, NormalizedCatalyst)
    cat = normalized.catalyst
    assert cat.event_id == "bpiq:catalyst:272"
    assert cat.ticker == "AMRX"
    assert cat.catalyst_date == date(2026, 11, 20)
    assert cat.catalyst_type is CatalystType.PDUFA
    assert cat.classification_status is ClassificationStatus.MATCHED
    assert cat.source_url == "https://www.example.com/source"
    assert cat.source.retrieved_at == NOW and cat.source.timestamp_note
    assert normalized.company.market_cap_usd == 4558262272.0
    assert normalized.company.provider_last_price == "10.00"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"ticker": None, "company": None}, "No ticker"),
        ({"ticker": "AAA", "company": {"ticker": "BBB"}}, "differs"),
        ({"id": True}, "Schema validation failed"),
        ({"catalyst_date": "2026-13-40"}, "not a valid date"),
    ],
)
def test_normalization_rejections(overrides, reason):
    raw = bpiq_record(1)
    raw.update(overrides)
    result = normalize_catalyst(raw, NOW)
    assert isinstance(result, RejectedRecord)
    assert reason in result.reason


def test_rejection_reason_does_not_echo_input_values():
    result = normalize_catalyst(bpiq_record(1, market_cap="SENSITIVE-VALUE"), NOW)
    assert isinstance(result, RejectedRecord)
    assert "SENSITIVE-VALUE" not in result.reason


def test_every_demo_fixture_record_matches_documented_schema():
    records = render_catalyst_records(SCAN_TODAY)
    for raw in records:
        assert isinstance(normalize_catalyst(raw, NOW), NormalizedCatalyst), raw["id"]
    assert any(r["catalyst_date"] is None for r in records)
    assert all(isinstance(r["company"]["last_price"], str) for r in records)
    malformed = render_catalyst_records(SCAN_TODAY, "bpiq/catalysts_malformed.json")
    assert all(isinstance(normalize_catalyst(raw, NOW), RejectedRecord) for raw in malformed)
