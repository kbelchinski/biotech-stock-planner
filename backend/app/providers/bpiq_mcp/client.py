"""Minimal MCP client over Streamable HTTP (JSON-RPC 2.0). Explicit tool calls; no LLM involved.

Rate limiting, timeouts and bounded retries come from ProviderHttpClient. Responses may be JSON or a
single-response SSE stream; both are parsed.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.logging_setup import get_logger
from app.providers.errors import ErrorKind, ProviderError
from app.providers.http import ProviderHttpClient

log = get_logger("bpiq_mcp.client")

PROVIDER = "BPIQ MCP"
PROTOCOL_VERSION = "2025-06-18"
MAX_TOOL_PAGES = 20

MCP_HINTS = {
    ErrorKind.AUTH: "BPIQ rejected the MCP credentials. Check BPIQ_API_KEY, or reconnect on the Integrations page.",
    ErrorKind.PERMISSION: "This BPIQ account is not permitted to use the requested MCP tool.",
    ErrorKind.RATE_LIMITED: "BPIQ MCP rate limit reached. Retry shortly.",
    ErrorKind.UNAVAILABLE: "BPIQ MCP is unavailable. Other data sources are unaffected.",
    ErrorKind.TIMEOUT: "BPIQ MCP did not respond in time.",
}


def _parse_body(response: httpx.Response, request_id: int) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                message = json.loads(payload)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "No JSON-RPC response found in the event stream.")
    try:
        message = response.json()
    except ValueError:
        raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "MCP response is not JSON.") from None
    if not isinstance(message, dict):
        raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "MCP response is not a JSON-RPC object.")
    return message


class McpSession:
    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        url: str,
        token: Callable[[], Awaitable[str]],
        scheme: str = "Bearer",
    ) -> None:
        self._http = http
        self._url = url
        self._token = token
        self._scheme = scheme
        self._ids = itertools.count(1)
        self._session_id: str | None = None
        self._protocol: str = PROTOCOL_VERSION
        self.server_info: dict[str, Any] = {}

    async def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"{self._scheme} {await self._token()}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._protocol,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        response = await self._http.send(
            "POST",
            self._url,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            headers=await self._headers(),
        )
        if sid := response.headers.get("mcp-session-id"):
            self._session_id = sid
        message = _parse_body(response, request_id)
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise ProviderError(
                PROVIDER, ErrorKind.INVALID_RESPONSE, f"MCP {method} error {error.get('code')}: {str(error.get('message'))[:200]}"
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, f"MCP {method} returned no result object.")
        return result

    async def initialize(self) -> dict[str, Any]:
        result = await self._rpc(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "catalyst-screener", "version": "0.2"}},
        )
        self._protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self.server_info = result.get("serverInfo") or {}
        await self._http.send(
            "POST", self._url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=await self._headers()
        )
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            result = await self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, f"tools/list exceeded {MAX_TOOL_PAGES} pages.")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))[:300]
            raise ProviderError(PROVIDER, ErrorKind.BAD_REQUEST, f"Tool {name} returned an error: {text or 'no detail'}")
        return result


def tool_result_payload(result: dict[str, Any]) -> Any:
    """structuredContent when present; otherwise JSON parsed from text content; otherwise the text."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
    joined = "\n".join(texts).strip()
    if not joined:
        return None
    try:
        return json.loads(joined)
    except ValueError:
        return joined
