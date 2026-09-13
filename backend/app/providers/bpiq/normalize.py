"""Validate BPIQ catalyst payloads and convert them into normalized domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.models import Catalyst, CompanyProfile, SourceRef
from app.providers.bpiq.schemas import BpiqCatalystRecord, BpiqPaginatedEnvelope
from app.providers.errors import ErrorKind, ProviderError
from app.screening.catalyst_classification import classify

PROVIDER = "BPIQ Apex REST"
CATALYSTS_ENDPOINT = "GET /api/v1/info/catalysts/"
NO_TIMESTAMP_NOTE = "BPIQ does not document an as-of timestamp; the retrieval time is shown instead."
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class RejectedRecord:
    record_id: Any
    reason: str


@dataclass(frozen=True)
class NormalizedCatalyst:
    catalyst: Catalyst
    company: CompanyProfile


def summarize_validation_error(exc: ValidationError, limit: int = 3) -> str:
    """Field locations and messages only; input values are never echoed."""
    errors = exc.errors(include_input=False, include_url=False)
    parts = [f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in errors[:limit]]
    extra = len(errors) - limit
    return "; ".join(parts) + (f" (+{extra} more)" if extra > 0 else "")


def parse_catalyst_envelope(payload: Any) -> BpiqPaginatedEnvelope:
    try:
        return BpiqPaginatedEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            PROVIDER,
            ErrorKind.INVALID_RESPONSE,
            f"Catalyst response does not match the documented envelope: {summarize_validation_error(exc)}",
        ) from None


def normalize_catalyst(raw: Any, retrieved_at: datetime) -> NormalizedCatalyst | RejectedRecord:
    record_id = raw.get("id") if isinstance(raw, dict) else None
    try:
        record = BpiqCatalystRecord.model_validate(raw)
    except ValidationError as exc:
        return RejectedRecord(record_id, f"Schema validation failed: {summarize_validation_error(exc)}")

    company = record.company
    record_ticker = _clean_ticker(record.ticker)
    company_ticker = _clean_ticker(company.ticker if company else None)
    if record_ticker and company_ticker and record_ticker != company_ticker:
        return RejectedRecord(
            record.id, f"Record ticker {record_ticker} differs from linked company ticker {company_ticker}"
        )
    ticker = record_ticker or company_ticker
    if not ticker:
        return RejectedRecord(record.id, "No ticker on the record or its linked company")

    catalyst_date: date | None = None
    if record.catalyst_date is not None:
        if not _ISO_DATE.match(record.catalyst_date):
            return RejectedRecord(record.id, f"catalyst_date {record.catalyst_date!r} is not YYYY-MM-DD")
        try:
            catalyst_date = date.fromisoformat(record.catalyst_date)
        except ValueError:
            return RejectedRecord(record.id, f"catalyst_date {record.catalyst_date!r} is not a valid date")

    stage_event = record.stage_event
    stage_label = stage_event.stage_label if stage_event else None
    event_label = stage_event.event_label if stage_event else None
    classification = classify(stage_label, event_label)

    indications = [i.title for i in record.indications or [] if i.title]
    if not indications and record.indications_text:
        indications = [record.indications_text]

    catalyst = Catalyst(
        event_id=f"bpiq:catalyst:{record.id}",
        provider_record_id=record.id,
        ticker=ticker,
        company_name=company.name if company else None,
        drug_name=record.drug_name,
        indications=indications,
        stage_label=stage_label,
        event_label=event_label,
        stage_event_label=stage_event.label if stage_event else None,
        catalyst_type=classification.catalyst_type,
        classification_status=classification.status,
        classification_reason=classification.reason,
        catalyst_date=catalyst_date,
        note=record.note,
        source_url=_http_url(record.catalyst_source),
        source=SourceRef(
            provider=PROVIDER,
            endpoint=CATALYSTS_ENDPOINT,
            retrieved_at=retrieved_at,
            timestamp_note=NO_TIMESTAMP_NOTE,
            url=_http_url(record.catalyst_source),
        ),
    )

    market_cap = company.market_cap if company else None
    profile = CompanyProfile(
        ticker=ticker,
        name=company.name if company else None,
        market_cap_usd=float(market_cap) if market_cap is not None and market_cap >= 0 else None,
        provider_last_price=company.last_price if company else None,
        source=SourceRef(
            provider=PROVIDER,
            endpoint=f"{CATALYSTS_ENDPOINT} · results[].company.market_cap",
            retrieved_at=retrieved_at,
            timestamp_note=NO_TIMESTAMP_NOTE,
        ),
    )
    return NormalizedCatalyst(catalyst, profile)


def _clean_ticker(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def _http_url(value: str | None) -> str | None:
    if value and value.strip().lower().startswith(("https://", "http://")):
        return value.strip()
    return None
