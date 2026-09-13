"""Typed provider errors. Messages are user-facing and must never contain credentials."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorKind(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTH = "auth"
    PERMISSION = "permission"
    SUBSCRIPTION_LIMIT = "subscription_limit"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(Exception):
    def __init__(
        self,
        provider: str,
        kind: ErrorKind,
        message: str,
        *,
        status_code: int | None = None,
        hint: str | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.hint = hint
        self.retryable = retryable
        self.retry_after = retry_after
        self.attempts = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "kind": self.kind.value,
            "message": self.message,
            "status_code": self.status_code,
            "hint": self.hint,
            "attempts": self.attempts,
        }

    def __repr__(self) -> str:
        return f"ProviderError({self.provider!r}, {self.kind.value!r}, {self.message!r})"
