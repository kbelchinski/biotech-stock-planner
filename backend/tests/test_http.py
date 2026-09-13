import httpx
import pytest

from app.providers.errors import ErrorKind, ProviderError
from app.providers.http import RateLimiter
from tests.helpers import SleepRecorder, make_http


async def test_rate_limiter_waits_for_window():
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2, 60.0, clock=lambda: now[0], sleep=fake_sleep)
    await limiter.acquire()
    now[0] = 10.0
    await limiter.acquire()
    await limiter.acquire()
    assert sleeps == [50.0]


async def test_retry_after_header_is_honoured():
    calls = []
    sleep = SleepRecorder()

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "throttled"})
        return httpx.Response(200, json={"ok": True})

    http = make_http("P", handler, sleep=sleep)
    assert await http.get_json("https://example.test/x") == {"ok": True}
    assert sleep.calls == [7.0]


async def test_rate_limit_reset_header():
    sleep = SleepRecorder()
    responses = iter([httpx.Response(429, headers={"X-RateLimit-Reset": "1005"}), httpx.Response(200, json={})])
    http = make_http("P", lambda r: next(responses), sleep=sleep)
    http._clock = lambda: 1000.0
    await http.get_json("https://example.test/x")
    assert sleep.calls == [5.0]


async def test_server_errors_retry_with_bounded_backoff():
    sleep = SleepRecorder()
    http = make_http("P", lambda r: httpx.Response(503, text="down"), sleep=sleep, max_retries=3)
    with pytest.raises(ProviderError) as info:
        await http.get_json("https://example.test/x")
    assert info.value.kind is ErrorKind.UNAVAILABLE
    assert info.value.attempts == 4
    assert sleep.calls == [1.0, 2.0, 4.0]


async def test_timeouts_are_retried_then_reported():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    http = make_http("P", handler, max_retries=1)
    with pytest.raises(ProviderError) as info:
        await http.get_json("https://example.test/x")
    assert info.value.kind is ErrorKind.TIMEOUT
    assert info.value.attempts == 2


@pytest.mark.parametrize(("status", "kind"), [(400, ErrorKind.BAD_REQUEST), (401, ErrorKind.AUTH), (403, ErrorKind.PERMISSION), (404, ErrorKind.NOT_FOUND)])
async def test_client_errors_are_not_retried(status, kind):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"detail": "nope"})

    http = make_http("P", handler, hints={kind: "do this"})
    with pytest.raises(ProviderError) as info:
        await http.get_json("https://example.test/x")
    assert info.value.kind is kind
    assert info.value.hint == "do this"
    assert len(calls) == 1


async def test_non_json_success_is_invalid_response():
    http = make_http("P", lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError) as info:
        await http.get_json("https://example.test/x")
    assert info.value.kind is ErrorKind.INVALID_RESPONSE


async def test_error_messages_never_contain_credentials():
    secret = "super-secret-token-123"
    http = make_http("P", lambda r: httpx.Response(401, json={"detail": "Invalid token."}), headers={"Authorization": f"Token {secret}"})
    with pytest.raises(ProviderError) as info:
        await http.get_json("https://example.test/x")
    assert secret not in str(info.value.to_dict())
