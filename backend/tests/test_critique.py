import json
from datetime import UTC, datetime

import httpx
import pytest

from app.research.critique import AiUnavailable, OpenAICritic, diff_snapshots, validate_critique
from tests.helpers import demo_settings

EVIDENCE = [{"id": "E1", "category": "supporting", "kind": "fact", "text": "x"}, {"id": "E2", "category": "against", "kind": "fact", "text": "y"}]


def test_validation_drops_uncited_and_forbidden_points():
    content = {
        "supporting": [{"point": "Catalyst in window", "evidence_ids": ["E1"]}, {"point": "Made up", "evidence_ids": ["E9"]}],
        "against": [{"point": "There is a 70% chance of approval", "evidence_ids": ["E2"]}, {"point": "Price target $40", "evidence_ids": ["E2"]}],
        "missing_or_stale": "not a list",
    }
    out = validate_critique(content, EVIDENCE)
    assert out["sections"]["supporting"] == [{"point": "Catalyst in window", "evidence_ids": ["E1"]}]
    assert out["sections"]["against"] == []
    # "Made up" cites an unknown id; the non-list section counts as one malformed entry.
    assert out["removed"] == {"uncited": 2, "forbidden": 2}
    assert out["sections"]["missing_or_stale"] == []


def test_snapshot_diff_ignores_rounding_noise():
    assert diff_snapshots({"metric.a": "0.10000", "x": "1"}, {"metric.a": "0.10001", "x": "2"}) == [{"key": "x", "previous": "1", "current": "2"}]


def critic(tmp_path, handler, spent=0.0, **overrides):
    settings = demo_settings(
        tmp_path, openai_api_key="sk-test", openai_model="test-model",
        openai_input_usd_per_1m_tokens=1.0, openai_output_usd_per_1m_tokens=4.0, **overrides,
    )
    usage = []
    c = OpenAICritic(settings, month_spend=lambda m: spent, record_usage=lambda **kw: usage.append(kw),
                     transport=httpx.MockTransport(handler), clock=lambda: datetime(2026, 9, 14, tzinfo=UTC))
    return c, usage


async def test_openai_call_records_usage_and_treats_output_as_untrusted(tmp_path):
    def handler(request):
        body = json.loads(request.content)
        assert "untrusted" in body["messages"][1]["content"]
        assert request.headers["authorization"] == "Bearer sk-test"
        content = {"supporting": [{"point": "ok", "evidence_ids": ["E1"]}], "against": [], "missing_or_stale": [], "changes": [], "invalidation_conditions": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})

    c, usage = critic(tmp_path, handler)
    out = await c.critique(ticker="AAAA", evidence=EVIDENCE)
    assert out["sections"]["supporting"][0]["point"] == "ok"
    assert usage[0]["input_tokens"] == 1000 and usage[0]["cost"] == pytest.approx((1000 * 1 + 200 * 4) / 1e6)


async def test_ask_uses_web_search_when_priced_and_strips_forbidden_sentences(tmp_path):
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        assert request.url.path.endswith("/responses")
        assert "untrusted" in body["input"] and "User question: Is it worth investing now?" in body["input"]
        text = (
            "Short answer\n- The setup has support but real risks [E1]. There is a 70% chance of approval.\n"
            "Supporting evidence\n- A news item ([example.com](https://example.com/a?utm_source=openai)) [D:catalysts]"
        )
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "web_search_call", "action": {"type": "search", "queries": ["FATE news"]}},
                    {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": [
                        {"type": "url_citation", "url": "https://example.com/a?utm_source=openai", "title": "A"}]}]},
                ],
                "usage": {"input_tokens": 10_000, "output_tokens": 300},
            },
        )

    c, usage = critic(tmp_path, handler, openai_web_search_usd_per_call=0.01)
    out = await c.ask(ticker="FATE", question="Is it worth investing now?", context={"evidence": EVIDENCE})
    assert seen["body"]["tools"] == [{"type": "web_search"}] and seen["body"]["max_tool_calls"] == 3
    assert "70%" not in out["answer"] and out["removed_sentences"] == 1
    assert "[E1]" in out["answer"] and "utm_source" not in out["answer"]
    assert out["sources"] == [{"url": "https://example.com/a", "title": "A"}]
    assert out["web_searches"] == 1 and out["search_queries"] == ["FATE news"]
    assert usage[0]["cost"] == pytest.approx((10_000 * 1 + 300 * 4) / 1e6 + 0.01)


async def test_ask_without_search_price_runs_without_web(tmp_path):
    def handler(request):
        body = json.loads(request.content)
        assert "tools" not in body
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "Short answer\n- Data only [E2]."}]}], "usage": {"input_tokens": 100, "output_tokens": 20}})

    c, _ = critic(tmp_path, handler)
    out = await c.ask(ticker="FATE", question="What are the risks?", context={})
    assert out["web_search_enabled"] is False and out["sources"] == []


def test_ask_context_includes_research_sections():
    from app.research.critique import build_ask_context

    research = {
        "overview": {"name": "X"},
        "screening": {"result": None},
        "catalysts": {"items": [{"event_id": "e1", "catalyst_date": "2026-10-01", "provider_flags": {"is_hedge_fund_pick": True}}], "primary_event_id": "e1"},
        "insiders": {"mcp": {"state": "ok", "records": [{"transaction_type": "disposed_type_unknown", "transaction_date": "2026-09-01"}]}},
        "funds": {"mcp": {"state": "ok", "records": [{"fund": "F", "period_end": "2026-06-30", "shares": 10}, {"fund": "F", "period_end": "2025-12-31"}, {"fund": "F", "period_end": "2026-03-31"}]}},
        "financials": {"mcp": {"state": "unavailable", "reason": "not connected", "records": []}},
    }
    ctx = build_ask_context(research, EVIDENCE)
    assert ctx["D:catalysts"][0]["provider_flags_true"] == ["is_hedge_fund_pick"] and ctx["D:catalysts"][0]["is_primary"]
    assert ctx["D:insiders"]["counts_by_type"] == {"disposed_type_unknown": 1}
    assert {r["period_end"] for r in ctx["D:funds"]["latest_periods"]} == {"2026-06-30", "2026-03-31"}
    assert ctx["D:financials"]["reason"] == "not connected" and len(ctx["evidence"]) == 2


async def test_budget_and_missing_configuration_refuse_calls(tmp_path):
    def never(request):
        raise AssertionError("no request expected")

    c, _ = critic(tmp_path, never, spent=5.0)
    with pytest.raises(AiUnavailable, match="budget"):
        await c.critique(ticker="AAAA", evidence=EVIDENCE)
    unconfigured = OpenAICritic(demo_settings(tmp_path), month_spend=lambda m: 0, record_usage=lambda **kw: None)
    assert unconfigured.status()["enabled"] is False
    with pytest.raises(AiUnavailable):
        await unconfigured.critique(ticker="AAAA", evidence=EVIDENCE)
