"""OAuth 2.1 authorization-code + PKCE flow for the BPIQ MCP server.

Observed 2026-09-14 (unauthenticated metadata requests):
- The MCP endpoint answers 401 with `WWW-Authenticate: Bearer ... resource_metadata=...` and does not
  accept the BPIQ REST API key.
- /.well-known/oauth-protected-resource lists the authorization server and scope `biopharmiq.read`.
- /.well-known/oauth-authorization-server lists authorize, token and dynamic-registration endpoints.

Tokens are stored server-side in a JSON file under backend/data (gitignored). They are never returned
by the API, logged, or written to the scan database.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.logging_setup import get_logger

log = get_logger("bpiq_mcp.oauth")

STATE_TTL_SECONDS = 600
REFRESH_MARGIN_SECONDS = 120
SCOPE = "biopharmiq.read"


class McpAuthError(Exception):
    pass


@dataclass
class PendingAuthorization:
    verifier: str
    redirect_uri: str
    created: float


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def clear(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass


class McpOAuth:
    def __init__(self, *, mcp_url: str, store: TokenStore, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._mcp_url = mcp_url
        self._store = store
        self._timeout = timeout
        self._transport = transport
        self._pending: dict[str, PendingAuthorization] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, follow_redirects=False, transport=self._transport)

    @property
    def origin(self) -> str:
        parts = urlsplit(self._mcp_url)
        return f"{parts.scheme}://{parts.netloc}"

    def connected(self) -> bool:
        data = self._store.load()
        return bool(data and data.get("access_token"))

    def public_state(self) -> dict[str, Any]:
        data = self._store.load() or {}
        return {
            "connected": bool(data.get("access_token")),
            "obtained_at": data.get("obtained_at"),
            "expires_at": data.get("expires_at"),
            "has_refresh_token": bool(data.get("refresh_token")),
            "scope": data.get("scope"),
        }

    async def _metadata(self, client: httpx.AsyncClient) -> dict[str, Any]:
        resource = await client.get(f"{self.origin}/.well-known/oauth-protected-resource")
        resource.raise_for_status()
        servers = resource.json().get("authorization_servers") or [self.origin + "/"]
        issuer = servers[0].rstrip("/")
        meta = await client.get(f"{issuer}/.well-known/oauth-authorization-server")
        meta.raise_for_status()
        data = meta.json()
        for key in ("authorization_endpoint", "token_endpoint"):
            if not str(data.get(key, "")).startswith("https://"):
                raise McpAuthError(f"Authorization server metadata has no HTTPS {key}.")
        return data

    async def start(self, redirect_uri: str) -> str:
        """Register (once per redirect URI) and return the browser authorization URL."""
        async with self._client() as client:
            try:
                meta = await self._metadata(client)
                stored = self._store.load() or {}
                if stored.get("client_id") and stored.get("redirect_uri") == redirect_uri:
                    client_id = stored["client_id"]
                else:
                    if not meta.get("registration_endpoint"):
                        raise McpAuthError("Server does not support dynamic client registration.")
                    reg = await client.post(
                        meta["registration_endpoint"],
                        json={
                            "client_name": "Catalyst Screener (local)",
                            "redirect_uris": [redirect_uri],
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"],
                            "token_endpoint_auth_method": "none",
                            "scope": SCOPE,
                        },
                    )
                    if reg.status_code >= 400:
                        raise McpAuthError(f"Client registration failed (HTTP {reg.status_code}).")
                    client_id = reg.json()["client_id"]
                    self._store.save({"client_id": client_id, "redirect_uri": redirect_uri, "token_endpoint": meta["token_endpoint"]})
            except httpx.HTTPError as exc:
                raise McpAuthError(f"Could not reach the MCP authorization server ({type(exc).__name__}).") from None

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        self._prune()
        self._pending[state] = PendingAuthorization(verifier=verifier, redirect_uri=redirect_uri, created=time.time())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": self._mcp_url,
            }
        )
        return f"{meta['authorization_endpoint']}?{query}"

    async def complete(self, code: str, state: str) -> None:
        self._prune()
        pending = self._pending.pop(state, None)
        if pending is None:
            raise McpAuthError("Unknown or expired authorization state. Start the connection again.")
        stored = self._store.load() or {}
        if not stored.get("client_id") or not stored.get("token_endpoint"):
            raise McpAuthError("Client registration is missing. Start the connection again.")
        await self._token_request(
            stored,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "client_id": stored["client_id"],
                "code_verifier": pending.verifier,
                "resource": self._mcp_url,
            },
        )

    async def access_token(self) -> str:
        stored = self._store.load() or {}
        token = stored.get("access_token")
        if not token:
            raise McpAuthError("BPIQ MCP is not connected.")
        expires_at = stored.get("expires_at")
        if expires_at and time.time() > float(expires_at) - REFRESH_MARGIN_SECONDS:
            if not stored.get("refresh_token"):
                raise McpAuthError("The BPIQ MCP token expired. Reconnect.")
            await self._token_request(
                stored,
                {"grant_type": "refresh_token", "refresh_token": stored["refresh_token"], "client_id": stored["client_id"], "resource": self._mcp_url},
            )
            token = (self._store.load() or {}).get("access_token")
        return str(token)

    async def _token_request(self, stored: dict[str, Any], form: dict[str, str]) -> None:
        async with self._client() as client:
            try:
                resp = await client.post(stored["token_endpoint"], data=form, headers={"Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise McpAuthError(f"Token endpoint unreachable ({type(exc).__name__}).") from None
        if resp.status_code >= 400:
            raise McpAuthError(f"Token request failed (HTTP {resp.status_code}). Reconnect BPIQ MCP.")
        body = resp.json()
        if not body.get("access_token"):
            raise McpAuthError("Token response contained no access token.")
        now = time.time()
        self._store.save(
            {
                **stored,
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token") or stored.get("refresh_token"),
                "expires_at": now + float(body["expires_in"]) if body.get("expires_in") else None,
                "scope": body.get("scope"),
                "obtained_at": now,
            }
        )
        log.info("BPIQ MCP token stored (grant=%s)", form["grant_type"])

    def disconnect(self) -> None:
        self._store.clear()

    def _prune(self) -> None:
        cutoff = time.time() - STATE_TTL_SECONDS
        for key in [k for k, v in self._pending.items() if v.created < cutoff]:
            self._pending.pop(key, None)
