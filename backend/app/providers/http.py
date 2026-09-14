"""Shared HTTP plumbing: client-side rate limiting, timeouts, bounded retries, error mapping.

Both live requests and demo fixtures go through this client; demo mode swaps only the
httpx transport (see app.fixtures.transport).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from app.logging_setup import format_url, get_logger
from app.providers.errors import ErrorKind, ProviderError

log = get_logger("http")

Sleep = Callable[[float], Awaitable[None]]

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ERROR_DETAIL_CHARS = 300


class RateLimiter:
    """Sliding window: at most `max_calls` acquisitions per `period` seconds."""

    def __init__(
        self,
        max_calls: int,
        period: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._max_calls = max_calls
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self._period:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                await self._sleep(self._period - (now - self._calls[0]))


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    # Upper bound for server-requested waits (Retry-After / X-RateLimit-Reset).
    max_server_delay: float = 90.0
    jitter: bool = True


ErrorHints = Mapping[ErrorKind, str]


class ProviderHttpClient:
    def __init__(
        self,
        *,
        provider: str,
        client: httpx.AsyncClient,
        retry: RetryPolicy,
        rate_limiter: RateLimiter | None = None,
        hints: ErrorHints | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.provider = provider
        self._client = client
        self._retry = retry
        self._limiter = rate_limiter
        self._hints = hints or {}
        self._sleep = sleep
        self._clock = clock
        self.request_count = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        target = format_url(url, dict(params) if params else None)
        attempt = 0
        while True:
            if self._limiter is not None:
                waited_from = time.monotonic()
                await self._limiter.acquire()
                waited = time.monotonic() - waited_from
                if waited > 0.05:
                    log.info("%s rate-limit wait %.1fs before %s", self.provider, waited, target)
            self.request_count += 1
            started = time.monotonic()
            try:
                response = await self._client.get(url, params=params)
            except httpx.TimeoutException:
                elapsed_ms = (time.monotonic() - started) * 1000
                log.warning("%s GET %s timed out after %.0fms (attempt %d)", self.provider, target, elapsed_ms, attempt + 1)
                error = self._error(ErrorKind.TIMEOUT, "Request timed out.", retryable=True)
            except httpx.TransportError as exc:
                elapsed_ms = (time.monotonic() - started) * 1000
                log.warning(
                    "%s GET %s transport %s after %.0fms (attempt %d)",
                    self.provider,
                    target,
                    type(exc).__name__,
                    elapsed_ms,
                    attempt + 1,
                )
                error = self._error(
                    ErrorKind.UNAVAILABLE,
                    f"Could not reach the provider ({type(exc).__name__}).",
                    retryable=True,
                )
            else:
                elapsed_ms = (time.monotonic() - started) * 1000
                if response.status_code < 400:
                    log.info("%s GET %s -> %s in %.0fms", self.provider, target, response.status_code, elapsed_ms)
                    try:
                        return response.json()
                    except ValueError:
                        log.error("%s GET %s -> %s was not JSON", self.provider, target, response.status_code)
                        raise self._error(
                            ErrorKind.INVALID_RESPONSE,
                            "Provider returned a response that is not valid JSON.",
                            status_code=response.status_code,
                        ) from None
                log.warning(
                    "%s GET %s -> %s in %.0fms (attempt %d)",
                    self.provider,
                    target,
                    response.status_code,
                    elapsed_ms,
                    attempt + 1,
                )
                error = self._error_from_response(response)

            if not error.retryable or attempt >= self._retry.max_retries:
                error.attempts = attempt + 1
                log.error("%s giving up on %s: %s", self.provider, target, error.message)
                raise error
            delay = self._delay(attempt, error.retry_after)
            log.warning(
                "%s retry %d/%d on %s after %s; waiting %.1fs",
                self.provider,
                attempt + 1,
                self._retry.max_retries,
                target,
                error.kind.value,
                delay,
            )
            await self._sleep(delay)
            attempt += 1

    def _delay(self, attempt: int, server_delay: float | None) -> float:
        if server_delay is not None:
            return max(0.0, min(server_delay, self._retry.max_server_delay))
        delay = min(self._retry.base_delay * (2**attempt), self._retry.max_delay)
        if self._retry.jitter:
            delay *= random.uniform(0.8, 1.2)
        return delay

    def _error(self, kind: ErrorKind, message: str, **kwargs: Any) -> ProviderError:
        return ProviderError(self.provider, kind, message, hint=self._hints.get(kind), **kwargs)

    def _error_from_response(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        detail = _error_detail(response)
        suffix = f" Provider said: {detail}" if detail else ""
        if status == 401:
            return self._error(ErrorKind.AUTH, f"Authentication failed (HTTP 401).{suffix}", status_code=status)
        if status == 403:
            return self._error(
                ErrorKind.PERMISSION,
                f"Access forbidden for this account or plan (HTTP 403).{suffix}",
                status_code=status,
            )
        if status == 404:
            return self._error(ErrorKind.NOT_FOUND, f"Resource not found (HTTP 404).{suffix}", status_code=status)
        if status in (400, 422):
            return self._error(
                ErrorKind.BAD_REQUEST, f"Request rejected (HTTP {status}).{suffix}", status_code=status
            )
        if status == 429:
            return self._error(
                ErrorKind.RATE_LIMITED,
                f"Rate limit exceeded (HTTP 429).{suffix}",
                status_code=status,
                retryable=True,
                retry_after=_server_delay(response, self._clock()),
            )
        return self._error(
            ErrorKind.UNAVAILABLE,
            f"Provider error (HTTP {status}).{suffix}",
            status_code=status,
            retryable=status in RETRYABLE_STATUS,
            retry_after=_server_delay(response, self._clock()) if status == 503 else None,
        )


def _error_detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_ERROR_DETAIL_CHARS]
    return None


def _server_delay(response: httpx.Response, now: float) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    # Alpaca documents X-RateLimit-Reset (epoch seconds).
    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return max(0.0, float(reset) - now)
        except ValueError:
            pass
    return None
