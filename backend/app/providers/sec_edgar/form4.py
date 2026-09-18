"""Parse EDGAR ownership XML (Forms 4 and 4/A) into insider transactions.

Element names follow the EDGAR Ownership XML Technical Specification and were checked on 2026-09-18 against live
filings (schemaVersion X0609): ownershipDocument/documentType, periodOfReport, dateOfOriginalSubmission (4/A only),
issuer/issuerCik, reportingOwner/reportingOwnerId/rptOwnerCik|rptOwnerName, reportingOwnerRelationship,
nonDerivativeTable/nonDerivativeTransaction, derivativeTable/derivativeTransaction, footnotes/footnote[@id].

Every value element may carry only a footnoteId and no <value>; that is reported as missing, never as zero.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.models import SourceRef
from app.domain.research import InsiderTransaction, SecFilingRef
from app.providers.sec_edgar.codes import describe

PROVIDER = "SEC EDGAR"
SEC_FIELDS = (
    "insider_name", "role", "transaction_code", "acquired_disposed", "transaction_date", "filing_date", "security_type",
    "shares", "price", "shares_owned_after", "direct_or_indirect", "footnotes", "source_url",
)


@dataclass
class ParsedFiling:
    filing: SecFilingRef
    issuer_symbol: str | None
    owner_names: list[str]
    transactions: list[InsiderTransaction] = field(default_factory=list)
    holdings_count: int = 0


class Form4ParseError(ValueError):
    pass


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _value(el: ET.Element, path: str) -> str | None:
    return _text(el, f"{path}/value")


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _date(text: str | None) -> date | None:
    if not text or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _flag(text: str | None) -> bool:
    return (text or "").strip().lower() in ("1", "true")


def _role(rel: ET.Element | None) -> str | None:
    if rel is None:
        return None
    parts = []
    if _flag(_text(rel, "isDirector")):
        parts.append("Director")
    if _flag(_text(rel, "isOfficer")):
        parts.append(_text(rel, "officerTitle") or "Officer")
    if _flag(_text(rel, "isTenPercentOwner")):
        parts.append("10% owner")
    if _flag(_text(rel, "isOther")):
        parts.append(_text(rel, "otherText") or "Other")
    return ", ".join(parts) or None


def _footnotes(el: ET.Element, texts: dict[str, str]) -> list[str]:
    ids: list[str] = []
    for ref in el.iter("footnoteId"):
        fid = ref.get("id")
        if fid and fid not in ids:
            ids.append(fid)
    return [f"{fid}: {texts[fid]}" if fid in texts else f"{fid}: (footnote text not in filing)" for fid in ids]


def parse_form4(
    xml: bytes | str,
    *,
    accession_number: str,
    form: str,
    filing_date: date | None,
    url: str,
    retrieved_at: datetime | None,
) -> ParsedFiling:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Form4ParseError(f"Filing {accession_number} is not valid ownership XML: {exc}") from None
    if root.tag != "ownershipDocument":
        raise Form4ParseError(f"Filing {accession_number}: unexpected root element {root.tag!r}.")

    document_type = _text(root, "documentType") or form
    is_amendment = document_type.upper().endswith("/A")
    issuer = root.find("issuer")
    owners = root.findall("reportingOwner")
    owner_names = [n for o in owners if (n := _text(o, "reportingOwnerId/rptOwnerName"))]
    owner_ciks = [c for o in owners if (c := _text(o, "reportingOwnerId/rptOwnerCik"))]
    roles = [r for o in owners if (r := _role(o.find("reportingOwnerRelationship")))]
    ref = SecFilingRef(
        accession_number=accession_number,
        form=document_type,
        filing_date=filing_date,
        period_of_report=_date(_text(root, "periodOfReport")),
        is_amendment=is_amendment,
        date_of_original_submission=_date(_text(root, "dateOfOriginalSubmission")),
        url=url,
        issuer_cik=_text(issuer, "issuerCik"),
        reporting_owner_cik="; ".join(owner_ciks) or None,
        retrieved_at=retrieved_at,
    )
    source = SourceRef(provider=PROVIDER, endpoint=f"Form {document_type} {accession_number}", retrieved_at=retrieved_at, url=url)
    footnote_texts = {fn.get("id", ""): " ".join((fn.text or "").split()) for fn in root.iter("footnote")}
    parsed = ParsedFiling(filing=ref, issuer_symbol=_text(issuer, "issuerTradingSymbol"), owner_names=owner_names)
    parsed.holdings_count = len(root.findall("nonDerivativeTable/nonDerivativeHolding")) + len(root.findall("derivativeTable/derivativeHolding"))

    for table, path in (("non_derivative", "nonDerivativeTable/nonDerivativeTransaction"), ("derivative", "derivativeTable/derivativeTransaction")):
        for tx in root.findall(path):
            code = _text(tx, "transactionCoding/transactionCode")
            label, category = describe(code)
            ad = (_value(tx, "transactionAmounts/transactionAcquiredDisposedCode") or "").upper()[:1] or None
            shares = _number(_value(tx, "transactionAmounts/transactionShares"))
            price = _number(_value(tx, "transactionAmounts/transactionPricePerShare"))
            notes = []
            if price is None:
                notes.append("No price reported in the filing.")
            elif price == 0:
                notes.append("Filing reports a price of $0 (no cash price); no transaction value calculated.")
            if table == "derivative":
                underlying = _value(tx, "underlyingSecurity/underlyingSecurityTitle")
                underlying_shares = _number(_value(tx, "underlyingSecurity/underlyingSecurityShares"))
                exercise = _number(_value(tx, "conversionOrExercisePrice"))
                detail = ", ".join(
                    p for p in (
                        f"underlying {underlying}" if underlying else None,
                        f"{underlying_shares:,.0f} underlying shares" if underlying_shares is not None else None,
                        f"exercise/conversion price ${exercise:,.2f}" if exercise is not None else None,
                    ) if p
                )
                notes.append("Derivative table: shares and holdings count derivative securities" + (f" ({detail})." if detail else "."))
            if _flag(_text(tx, "transactionCoding/equitySwapInvolved")):
                notes.append("Equity swap involved.")
            direct = (_value(tx, "ownershipNature/directOrIndirectOwnership") or "").upper()[:1] or None
            parsed.transactions.append(
                InsiderTransaction(
                    origin="sec",
                    insider_name="; ".join(owner_names) or None,
                    role="; ".join(roles) or None,
                    transaction_code=code.upper() if code else None,
                    transaction_label=label,
                    transaction_type=category,  # type: ignore[arg-type]
                    acquired_disposed=ad if ad in ("A", "D") else None,  # type: ignore[arg-type]
                    transaction_date=_date(_value(tx, "transactionDate")),
                    filing_date=filing_date,
                    shares=shares,
                    price=price,
                    value_usd=shares * price if shares is not None and price else None,
                    shares_owned_after=_number(_value(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                    source_url=url,
                    mapping_verified=True,
                    source=source,
                    security_type=_value(tx, "securityTitle"),
                    note=" ".join(notes) or None,
                    table=table,  # type: ignore[arg-type]
                    direct_or_indirect=direct if direct in ("D", "I") else None,  # type: ignore[arg-type]
                    nature_of_ownership=_value(tx, "ownershipNature/natureOfOwnership"),
                    footnotes=_footnotes(tx, footnote_texts),
                    issuer_cik=ref.issuer_cik,
                    reporting_owner_cik=ref.reporting_owner_cik,
                    filing=ref,
                    amendment_status="amendment" if is_amendment else "original",
                    field_sources={f: "sec" for f in SEC_FIELDS},
                    sec_source=source,
                )
            )
    return parsed
