"""SEC EDGAR client for ownership filings, following the SEC's published access rules (checked 2026-09-18):

- A declared User-Agent with a contact email is required (https://www.sec.gov/about/webmaster-frequently-asked-questions);
  requests without one receive an "Undeclared Automated Tool" block page.
- At most 10 requests/second across all machines; this client defaults to 5/second.
- Responses are cached on disk: filing documents are immutable and cached indefinitely; the ticker map for a day;
  a company's submissions index for an hour. Every cached value keeps its original retrieval time for provenance.

Endpoints (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
- https://www.sec.gov/files/company_tickers.json            ticker -> CIK
- https://data.sec.gov/submissions/CIK##########.json       filings.recent columnar arrays
- https://www.sec.gov/Archives/edgar/data/<cik>/<accession without dashes>/<document>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.logging_setup import get_logger
from app.providers.errors import ErrorKind, ProviderError
from app.providers.http import ProviderHttpClient, RateLimiter, RetryPolicy
from app.providers.sec_edgar.form4 import PROVIDER, Form4ParseError, ParsedFiling, parse_form4

log = get_logger("sec_edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
OWNERSHIP_FORMS = ("4", "4/A")
TICKERS_TTL = timedelta(days=1)
SUBMISSIONS_TTL = timedelta(hours=1)
USER_AGENT_PATTERN = re.compile(r"\S+@\S+\.\S+")
BLOCK_PAGE_MARKER = "Undeclared Automated Tool"

SEC_HINTS = {
    ErrorKind.PERMISSION: "SEC refused the request. Set SEC_USER_AGENT to your name and contact email, e.g. \"Jane Doe jane@example.com\".",
    ErrorKind.RATE_LIMITED: "SEC fair-access limit reached. Requests resume after the rate drops for about 10 minutes.",
    ErrorKind.UNAVAILABLE: "SEC EDGAR is unavailable. BPIQ data is unaffected.",
    ErrorKind.TIMEOUT: "SEC EDGAR did not respond in time.",
}


@dataclass(frozen=True)
class Cached:
    body: str
    retrieved_at: datetime
    from_cache: bool


@dataclass(frozen=True)
class FilingIndexEntry:
    accession_number: str
    form: str
    filing_date: date | None
    report_date: date | None
    primary_document: str


def user_agent_problem(value: str | None) -> str | None:
    if not value or not value.strip():
        return "SEC enrichment is off: set SEC_USER_AGENT (your name and contact email) in .env to enable it."
    if not USER_AGENT_PATTERN.search(value):
        return "SEC enrichment is off: SEC_USER_AGENT must include a contact email, as the SEC's fair-access policy requires."
    return None


class SecEdgarClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._cache_dir: Path = settings.data_dir / "sec_cache"
        self._limiter = RateLimiter(max(1, min(settings.sec_rate_limit_per_sec, 10)), 1.0)
        self.network_requests = 0

    @property
    def problem(self) -> str | None:
        return user_agent_problem(self._settings.sec_user_agent)

    @property
    def configured(self) -> bool:
        return self.problem is None

    def http(self) -> ProviderHttpClient:
        return ProviderHttpClient(
            provider=PROVIDER,
            client=httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.http_timeout_seconds),
                headers={"User-Agent": (self._settings.sec_user_agent or "").strip(), "Accept-Encoding": "gzip, deflate"},
                follow_redirects=False,
                transport=self._transport,
            ),
            retry=RetryPolicy(max_retries=self._settings.http_max_retries),
            rate_limiter=self._limiter,
            hints=SEC_HINTS,
        )

    # ------------------------------------------------------------ cache

    def _cache_path(self, url: str) -> Path:
        return self._cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def _read_cache(self, url: str, ttl: timedelta | None) -> Cached | None:
        try:
            data = json.loads(self._cache_path(url).read_text(encoding="utf-8"))
            retrieved = datetime.fromisoformat(data["retrieved_at"])
        except (OSError, ValueError, KeyError):
            return None
        if data.get("url") != url or (ttl is not None and self._clock() - retrieved > ttl):
            return None
        return Cached(body=data["body"], retrieved_at=retrieved, from_cache=True)

    def _write_cache(self, url: str, body: str, retrieved_at: datetime) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(url)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "retrieved_at": retrieved_at.isoformat(), "body": body}), encoding="utf-8")
        os.replace(tmp, path)

    async def fetch(self, http: ProviderHttpClient, url: str, *, ttl: timedelta | None) -> Cached:
        """GET through the disk cache. ttl=None means the document is immutable and cached indefinitely."""
        if cached := self._read_cache(url, ttl):
            return cached
        self.network_requests += 1
        response = await http.send("GET", url)
        body = response.text
        if BLOCK_PAGE_MARKER in body[:2000]:
            raise ProviderError(PROVIDER, ErrorKind.PERMISSION, "SEC blocked the request as an undeclared automated tool.", hint=SEC_HINTS[ErrorKind.PERMISSION])
        retrieved = self._clock()
        self._write_cache(url, body, retrieved)
        return Cached(body=body, retrieved_at=retrieved, from_cache=False)

    async def _json(self, http: ProviderHttpClient, url: str, ttl: timedelta) -> tuple[Any, datetime]:
        cached = await self.fetch(http, url, ttl=ttl)
        try:
            return json.loads(cached.body), cached.retrieved_at
        except ValueError:
            raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, f"SEC returned non-JSON for {url}.") from None

    # ------------------------------------------------------------ lookups

    async def cik_for_ticker(self, http: ProviderHttpClient, ticker: str) -> int | None:
        data, _ = await self._json(http, TICKERS_URL, TICKERS_TTL)
        wanted = ticker.strip().upper()
        rows = data.values() if isinstance(data, dict) else []
        for row in rows:
            if isinstance(row, dict) and str(row.get("ticker", "")).upper() == wanted:
                return int(row["cik_str"])
        return None

    async def ownership_filings(self, http: ProviderHttpClient, cik: int, *, since: date) -> tuple[list[FilingIndexEntry], datetime, bool]:
        """Forms 4 and 4/A filed on or after `since`, newest first, capped at sec_max_filings.

        Returns (entries, retrieval time of the index, truncated)."""
        data, retrieved = await self._json(http, SUBMISSIONS_URL.format(cik=cik), SUBMISSIONS_TTL)
        recent = ((data or {}).get("filings") or {}).get("recent") or {}
        columns = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
        if not all(isinstance(recent.get(c), list) for c in columns):
            raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "SEC submissions index is missing expected columns.")
        entries = []
        for acc, form, filed, report, doc in zip(*(recent[c] for c in columns)):
            filed_date = _iso(filed)
            if form in OWNERSHIP_FORMS and filed_date and filed_date >= since and doc:
                entries.append(FilingIndexEntry(acc, form, filed_date, _iso(report), doc))
        entries.sort(key=lambda e: (e.filing_date or date.min, e.accession_number), reverse=True)
        limit = self._settings.sec_max_filings
        return entries[:limit], retrieved, len(entries) > limit

    async def filing(self, http: ProviderHttpClient, cik: int, entry: FilingIndexEntry) -> ParsedFiling:
        accession = entry.accession_number.replace("-", "")
        # primaryDocument points at an XSL-rendered view (e.g. "xslF345X06/doc.xml"); the raw XML drops that folder.
        raw_doc = entry.primary_document.split("/")[-1]
        xml_url = ARCHIVE_URL.format(cik=cik, accession=accession, document=raw_doc)
        view_url = ARCHIVE_URL.format(cik=cik, accession=accession, document=entry.primary_document)
        cached = await self.fetch(http, xml_url, ttl=None)
        try:
            return parse_form4(
                cached.body.encode("utf-8"),
                accession_number=entry.accession_number,
                form=entry.form,
                filing_date=entry.filing_date,
                url=view_url,
                retrieved_at=cached.retrieved_at,
            )
        except Form4ParseError:
            # A cached body that does not parse is not worth keeping.
            self._cache_path(xml_url).unlink(missing_ok=True)
            raise


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
