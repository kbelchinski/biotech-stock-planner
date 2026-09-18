import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.domain.models import SourceRef
from app.providers.bpiq_mcp.research import normalize_insiders
from app.providers.sec_edgar.client import SecEdgarClient
from app.providers.sec_edgar.form4 import parse_form4
from app.research.insider_enrichment import (
    UNVERIFIED_MESSAGE,
    build_insider_section,
    match_transactions,
    reconcile_amendments,
    summarize,
)
from tests.helpers import demo_settings

ISSUER_CIK = 875320
TODAY = date(2026, 9, 18)
BPIQ_SOURCE = SourceRef(provider="BPIQ MCP", endpoint="tools/call fetch_company_insider_transactions")
RETRIEVED = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
UA = "Test Runner test@example.com"


def tx_xml(
    *, code="S", ad="D", shares="1000", price="10.5", date_="2026-09-01", security="Common Stock", after="5000",
    direct="D", footnote=None, table="non_derivative",
) -> str:
    price_el = f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>" if price is not None else (
        '<transactionPricePerShare><footnoteId id="F2"/></transactionPricePerShare>'
    )
    fn = f'<footnoteId id="{footnote}"/>' if footnote else ""
    tag = "nonDerivativeTransaction" if table == "non_derivative" else "derivativeTransaction"
    extra = (
        "<conversionOrExercisePrice><value>12.00</value></conversionOrExercisePrice>"
        "<underlyingSecurity><underlyingSecurityTitle><value>Common Stock</value></underlyingSecurityTitle>"
        f"<underlyingSecurityShares><value>{shares}</value></underlyingSecurityShares></underlyingSecurity>"
        if table == "derivative" else ""
    )
    return (
        f"<{tag}><securityTitle><value>{security}</value></securityTitle>{extra}"
        f"<transactionDate><value>{date_}</value></transactionDate>"
        f"<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode>"
        "<equitySwapInvolved>0</equitySwapInvolved></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{shares}</value>{fn}</transactionShares>{price_el}"
        f"<transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode></transactionAmounts>"
        f"<postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>"
        f"<ownershipNature><directOrIndirectOwnership><value>{direct}</value></directOrIndirectOwnership>"
        + ("<natureOfOwnership><value>By trust</value></natureOfOwnership>" if direct == "I" else "")
        + f"</ownershipNature></{tag}>"
    )


def form4_xml(*txs: str, doc_type="4", owner="Doe Jane", owner_cik="0000000001", original=None, holdings=0, derivative=()) -> str:
    orig = f"<dateOfOriginalSubmission>{original}</dateOfOriginalSubmission>" if original else ""
    hold = "<nonDerivativeHolding><securityTitle><value>Common Stock</value></securityTitle></nonDerivativeHolding>" * holdings
    return (
        f"<?xml version=\"1.0\"?><ownershipDocument><schemaVersion>X0609</schemaVersion><documentType>{doc_type}</documentType>"
        f"<periodOfReport>2026-09-01</periodOfReport>{orig}"
        f"<issuer><issuerCik>{ISSUER_CIK:010d}</issuerCik><issuerName>VERTEX</issuerName><issuerTradingSymbol>VRTX</issuerTradingSymbol></issuer>"
        f"<reportingOwner><reportingOwnerId><rptOwnerCik>{owner_cik}</rptOwnerCik><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>"
        "<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle>"
        "<isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship></reportingOwner>"
        f"<nonDerivativeTable>{''.join(txs)}{hold}</nonDerivativeTable><derivativeTable>{''.join(derivative)}</derivativeTable>"
        '<footnotes><footnote id="F1">Sold under a Rule 10b5-1 plan adopted 2026-03-01.</footnote>'
        '<footnote id="F2">Price reported as a weighted average; see footnote.</footnote></footnotes></ownershipDocument>'
    )


def parse(xml: str, *, acc="0000875320-26-000001", form="4", filed=date(2026, 9, 3)):
    return parse_form4(xml, accession_number=acc, form=form, filing_date=filed, url=f"https://www.sec.gov/{acc}", retrieved_at=RETRIEVED)


def bpiq(*rows: dict):
    base = {"executive": "DOE, JANE", "executive_title": "CEO", "security_type": "Common Stock", "ticker": "VRTX"}
    return normalize_insiders([{**base, **r} for r in rows], source=BPIQ_SOURCE, today=TODAY)


def sale(**kw):
    return {"shares": "1000.0", "share_price": "10.5", "transaction_date": "2026-09-01", "acquisition_or_disposal": "D", **kw}


# ---------------------------------------------------------------- parsing


def test_parse_multiple_transactions_derivative_table_footnotes_and_missing_price():
    filing = parse(
        form4_xml(
            tx_xml(code="M", ad="A", shares="2000", price="12", after="7000"),
            tx_xml(code="S", ad="D", shares="2000", price=None, after="5000", footnote="F1"),
            tx_xml(code="P", ad="A", shares="300", price="9.99", after="300", direct="I"),
            derivative=[tx_xml(code="M", ad="D", shares="2000", price="0", security="Stock Option (Right to Buy)", after="0", table="derivative")],
        )
    )
    txs = filing.transactions
    assert [(t.table, t.transaction_code) for t in txs] == [
        ("non_derivative", "M"), ("non_derivative", "S"), ("non_derivative", "P"), ("derivative", "M"),
    ]
    exercise, sold, bought, option = txs
    # Official SEC labels, raw code kept alongside.
    assert sold.transaction_label.startswith("Open market or private sale")
    assert bought.transaction_label.startswith("Open market or private purchase") and bought.transaction_type == "purchase_open_market_or_private"
    assert exercise.transaction_type == "derivative_exercise_or_conversion"
    # Missing price: footnote only, reported as None (not zero) with no value.
    assert sold.price is None and sold.value_usd is None and "No price reported" in sold.note
    assert sold.footnotes == ["F1: Sold under a Rule 10b5-1 plan adopted 2026-03-01.", "F2: Price reported as a weighted average; see footnote."]
    assert bought.direct_or_indirect == "I" and bought.nature_of_ownership == "By trust"
    assert sold.shares_owned_after == 5000 and sold.filing_date == date(2026, 9, 3) and sold.transaction_date == date(2026, 9, 1)
    assert option.price == 0 and option.value_usd is None and "Derivative table" in option.note
    assert exercise.role == "Director, Chief Executive Officer"
    assert exercise.filing.issuer_cik == f"{ISSUER_CIK:010d}" and exercise.reporting_owner_cik == "0000000001"
    assert exercise.sec_source.retrieved_at == RETRIEVED


# ---------------------------------------------------------------- matching


def test_exact_match_merges_sec_detail_with_field_provenance():
    sec = parse(form4_xml(tx_xml())).transactions
    rows, sec_only = match_transactions(bpiq(sale()), sec, issuer_cik=ISSUER_CIK)
    row = rows[0]
    assert row.match.status == "matched" and row.origin == "bpiq+sec" and sec_only == []
    assert row.transaction_code == "S" and row.shares_owned_after == 5000 and row.source_url
    assert row.field_sources["shares"] == "bpiq+sec" and row.field_sources["shares_owned_after"] == "sec"
    assert {"issuer", "reporting owner", "transaction date", "shares", "security type", "price"} <= set(row.match.compared)
    assert row.source.provider == "BPIQ MCP" and row.sec_source.provider == "SEC EDGAR"


def test_ticker_and_date_alone_are_not_enough():
    sec = parse(form4_xml(tx_xml())).transactions
    other_owner = bpiq(sale(executive="SMITH, JOHN"))
    other_shares = bpiq(sale(shares="999.0"))
    no_shares = bpiq({"transaction_date": "2026-09-01", "acquisition_or_disposal": "D"})
    for rows_in, expected in ((other_owner, "unmatched"), (other_shares, "unmatched"), (no_shares, "insufficient")):
        rows, sec_only = match_transactions(rows_in, sec, issuer_cik=ISSUER_CIK)
        assert rows[0].match.status == expected
        assert rows[0].transaction_code is None and rows[0].transaction_type == "disposed_type_unknown"
        assert len(sec_only) == 1  # the SEC transaction is shown separately, not forced onto the row
    # Wrong issuer CIK never matches.
    rows, _ = match_transactions(bpiq(sale()), sec, issuer_cik=1)
    assert rows[0].match.status == "unmatched"


def test_ambiguous_when_two_sec_transactions_fit():
    # Same owner, date, shares and price, differing only in direct/indirect ownership: BPIQ cannot tell them apart.
    sec = parse(form4_xml(tx_xml(after="5000"), tx_xml(after="100", direct="I"))).transactions
    rows, sec_only = match_transactions(bpiq(sale()), sec, issuer_cik=ISSUER_CIK)
    assert rows[0].match.status == "ambiguous" and rows[0].match.candidates == 2
    assert rows[0].transaction_code is None and rows[0].origin == "bpiq"
    assert len(sec_only) == 2


def test_ambiguous_when_two_bpiq_rows_fit_one_sec_transaction():
    sec = parse(form4_xml(tx_xml())).transactions
    rows, sec_only = match_transactions(bpiq(sale(), sale()), sec, issuer_cik=ISSUER_CIK)
    assert [r.match.status for r in rows] == ["ambiguous", "ambiguous"]
    assert "other BPIQ row" in rows[0].match.note and len(sec_only) == 1


def test_missing_prices_do_not_block_or_fake_a_match():
    # BPIQ reports 0.0 for a filing with no price; the SEC filing gives only a footnote. Price is simply not compared.
    sec = parse(form4_xml(tx_xml(price=None))).transactions
    rows, _ = match_transactions(bpiq(sale(share_price="0.0")), sec, issuer_cik=ISSUER_CIK)
    row = rows[0]
    assert row.match.status == "matched" and "price (missing on one side)" in row.match.not_compared
    assert row.price is None and row.value_usd is None
    # A price that is present on both sides and disagrees prevents the match.
    rows, _ = match_transactions(bpiq(sale(share_price="11.0")), parse(form4_xml(tx_xml())).transactions, issuer_cik=ISSUER_CIK)
    assert rows[0].match.status == "unmatched"


def test_multiple_transactions_in_one_filing_match_one_to_one():
    sec = parse(
        form4_xml(
            tx_xml(code="M", ad="A", shares="2000", price="12"),
            tx_xml(code="S", ad="D", shares="1500", price="20"),
            tx_xml(code="F", ad="D", shares="500", price="20"),
        )
    ).transactions
    rows, sec_only = match_transactions(
        bpiq(
            sale(acquisition_or_disposal="A", shares="2000.0", share_price="12.0"),
            sale(shares="1500.0", share_price="20.0"),
            sale(shares="500.0", share_price="20.0"),
        ),
        sec,
        issuer_cik=ISSUER_CIK,
    )
    assert [r.transaction_code for r in rows] == ["M", "S", "F"] and sec_only == []
    summary = summarize(rows, sec_only)
    assert summary["coded_by_code"]["F"]["label"].startswith("Payment of exercise price or tax liability")
    assert summary["code_p_purchases"]["count"] == 0


def test_bpiq_acquisitions_are_excluded_from_purchase_metrics():
    rows, sec_only = match_transactions(
        bpiq(sale(acquisition_or_disposal="A", shares="10.0")), parse(form4_xml(tx_xml(code="P", ad="A", shares="300", price="9"))).transactions,
        issuer_cik=ISSUER_CIK,
    )
    summary = summarize(rows, sec_only)
    assert summary["unclassified_bpiq"]["acquired"] == 1
    # Only the SEC code-P row counts, and its label never claims "open market" alone.
    assert summary["code_p_purchases"]["count"] == 1 and summary["code_p_purchases"]["shares"] == 300
    assert "open market or private" in summary["code_p_purchases"]["label"].lower()


# ---------------------------------------------------------------- amendments


def test_amendment_replaces_original_transaction_without_double_counting():
    original = parse(form4_xml(tx_xml(shares="1000"), tx_xml(code="F", shares="200")), acc="A-1", filed=date(2026, 9, 3))
    amended = parse(
        form4_xml(tx_xml(shares="1100"), doc_type="4/A", original="2026-09-03"), acc="A-2", form="4/A", filed=date(2026, 9, 10)
    )
    txs = reconcile_amendments([original, amended])
    superseded = [t for t in txs if t.superseded_by]
    assert len(superseded) == 1 and superseded[0].shares == 1000 and superseded[0].superseded_by == "A-2"
    rows, sec_only = match_transactions(bpiq(sale(shares="1100.0")), txs, issuer_cik=ISSUER_CIK)
    assert rows[0].match.status == "matched" and rows[0].filing.is_amendment and rows[0].filing.accession_number == "A-2"
    # The superseded 1,000-share row is neither matchable nor counted.
    assert [t.shares for t in sec_only] == [200]
    assert summarize(rows, sec_only)["coded_by_code"]["S"] == {"label": rows[0].transaction_label, "count": 1, "shares": 1100}
    # A BPIQ row with the pre-amendment share count no longer matches anything.
    stale, _ = match_transactions(bpiq(sale(shares="1000.0")), txs, issuer_cik=ISSUER_CIK)
    assert stale[0].match.status == "unmatched"


def test_second_amendment_replaces_the_first():
    original = parse(form4_xml(tx_xml(shares="1000")), acc="A-1", filed=date(2026, 9, 3))
    first = parse(form4_xml(tx_xml(shares="1100"), doc_type="4/A", original="2026-09-03"), acc="A-2", form="4/A", filed=date(2026, 9, 5))
    second = parse(form4_xml(tx_xml(shares="1200"), doc_type="4/A", original="2026-09-03"), acc="A-3", form="4/A", filed=date(2026, 9, 9))
    txs = reconcile_amendments([second, original, first])
    assert [t.shares for t in txs if t.superseded_by is None] == [1200]


def test_holdings_only_amendment_keeps_original_transactions():
    original = parse(form4_xml(tx_xml()), acc="A-1", filed=date(2026, 9, 3))
    amended = parse(form4_xml(doc_type="4/A", original="2026-09-03", holdings=1), acc="A-2", form="4/A", filed=date(2026, 9, 10))
    txs = reconcile_amendments([original, amended])
    assert len(txs) == 1 and txs[0].superseded_by is None and txs[0].amendment_status == "amended_holdings_only"


def test_unreconciled_amendment_is_shown_but_not_counted():
    # The amendment points at an original filed inside the window that was not retrieved: counting it could double count.
    amended = parse(form4_xml(tx_xml(), doc_type="4/A", original="2026-09-02"), acc="A-2", form="4/A", filed=date(2026, 9, 10))
    txs = reconcile_amendments([amended], window_start=date(2025, 9, 1))
    assert txs[0].amendment_status == "unreconciled_amendment"
    rows, sec_only = match_transactions(bpiq(sale()), txs, issuer_cik=ISSUER_CIK)
    assert rows[0].match.status == "ambiguous" and rows[0].transaction_code is None
    assert summarize(rows, sec_only)["coded_by_code"] == {} and summarize(rows, sec_only)["excluded_unreconciled"] == 1
    # An amendment of a filing older than the retrieved window stands on its own.
    old = parse(form4_xml(tx_xml(), doc_type="4/A", original="2025-01-02"), acc="A-3", form="4/A", filed=date(2026, 9, 10))
    assert reconcile_amendments([old], window_start=date(2025, 9, 1))[0].amendment_status == "amendment"


def test_amendment_with_different_row_count_for_same_key_is_not_reconciled():
    original = parse(form4_xml(tx_xml(shares="100"), tx_xml(shares="200")), acc="A-1", filed=date(2026, 9, 3))
    amended = parse(form4_xml(tx_xml(shares="300"), doc_type="4/A", original="2026-09-03"), acc="A-2", form="4/A", filed=date(2026, 9, 10))
    txs = reconcile_amendments([original, amended])
    assert not any(t.superseded_by for t in txs)
    assert [t.amendment_status for t in txs] == ["original", "original", "unreconciled_amendment"]


# ---------------------------------------------------------------- client, caching and the section


class FakeSec:
    def __init__(self, filings: dict[str, str], *, block=False):
        self.filings = filings
        self.block = block
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.block:
            return httpx.Response(403, text="<html><title>Your Request Originates from an Undeclared Automated Tool</title></html>")
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": ISSUER_CIK, "ticker": "VRTX", "title": "Vertex"}})
        if "/submissions/" in url:
            accs = list(self.filings)
            recent = {
                "accessionNumber": accs,
                "form": ["4/A" if "4/A" in self.filings[a][:400] else "4" for a in accs],
                "filingDate": ["2026-09-03" if i == 0 else "2026-09-10" for i in range(len(accs))],
                "reportDate": ["2026-09-01"] * len(accs),
                "primaryDocument": [f"xslF345X06/{a}.xml" for a in accs],
            }
            recent["form"].append("10-Q")
            for k in ("accessionNumber", "filingDate", "reportDate", "primaryDocument"):
                recent[k].append("x")
            return httpx.Response(200, json={"cik": str(ISSUER_CIK), "filings": {"recent": recent, "files": []}})
        for acc, xml in self.filings.items():
            if url.endswith(f"/{acc.replace('-', '')}/{acc}.xml"):
                return httpx.Response(200, text=xml)
        return httpx.Response(404)


def sec_client(tmp_path, fake, *, ua=UA, clock=lambda: RETRIEVED):
    settings = demo_settings(tmp_path, sec_user_agent=ua, http_max_retries=0)
    return SecEdgarClient(settings, transport=httpx.MockTransport(fake), clock=clock)


def bpiq_section(rows):
    return {"state": "ok", "records": [r.model_dump(mode="json") for r in rows]}


@pytest.mark.asyncio
async def test_section_without_user_agent_makes_no_requests_and_uses_unverified_message(tmp_path):
    fake = FakeSec({})
    for ua in (None, "no contact here"):
        section = await build_insider_section(bpiq_section(bpiq(sale())), sec_client(tmp_path, fake, ua=ua), "VRTX", today=TODAY, live=True)
        assert section["sec"]["state"] == "off" and "SEC_USER_AGENT" in section["sec"]["reason"]
        assert section["message"] == UNVERIFIED_MESSAGE
        assert section["mcp"]["records"][0]["match"]["status"] == "not_attempted"
    assert fake.requests == []
    demo = await build_insider_section(bpiq_section(bpiq(sale())), sec_client(tmp_path, fake), "VRTX", today=TODAY, live=False)
    assert demo["sec"]["state"] == "off" and fake.requests == []


@pytest.mark.asyncio
async def test_section_end_to_end_with_amendment_cache_and_user_agent(tmp_path):
    fake = FakeSec({
        "0000875320-26-000001": form4_xml(tx_xml(shares="1000"), tx_xml(code="F", shares="200")),
        "0000875320-26-000002": form4_xml(tx_xml(shares="1100"), doc_type="4/A", original="2026-09-03"),
    })
    client = sec_client(tmp_path, fake)
    section = await build_insider_section(bpiq_section(bpiq(sale(shares="1100.0"))), client, "VRTX", today=TODAY, live=True)
    assert section["sec"]["state"] == "ok" and section["sec"]["filings_checked"] == 2 and section["sec"]["amendments"] == 1
    row = section["mcp"]["records"][0]
    assert row["match"]["status"] == "matched" and row["transaction_code"] == "S" and row["filing"]["form"] == "4/A"
    assert row["source_url"].endswith("/xslF345X06/0000875320-26-000002.xml")  # human-readable view
    assert [r["shares"] for r in section["sec"]["records"]] == [200]
    assert [r["shares"] for r in section["sec"]["superseded"]] == [1000]
    assert "1 of 1 BPIQ row(s) matched" in section["message"]
    assert all(r.headers["user-agent"] == UA for r in fake.requests)
    # The raw XML is fetched without the XSL folder.
    assert any(str(r.url).endswith("/000087532026000001/0000875320-26-000001.xml") for r in fake.requests)
    first_requests = len(fake.requests)

    # Second research request: everything comes from the cache, with the original retrieval time kept.
    later = sec_client(tmp_path, fake, clock=lambda: RETRIEVED + timedelta(minutes=30))
    again = await build_insider_section(bpiq_section(bpiq(sale(shares="1100.0"))), later, "VRTX", today=TODAY, live=True)
    assert len(fake.requests) == first_requests and later.network_requests == 0
    assert again["mcp"]["records"][0]["sec_source"]["retrieved_at"].startswith("2026-09-18T12:00")
    # After the submissions TTL only the index is refetched; filing documents are immutable.
    much_later = sec_client(tmp_path, fake, clock=lambda: RETRIEVED + timedelta(hours=2))
    await build_insider_section(bpiq_section([]), much_later, "VRTX", today=TODAY, live=True)
    refetched = [str(r.url) for r in fake.requests[first_requests:]]
    assert len(refetched) == 1 and "/submissions/" in refetched[0]


@pytest.mark.asyncio
async def test_sec_block_page_is_reported_and_bpiq_rows_stay_unclassified(tmp_path):
    section = await build_insider_section(bpiq_section(bpiq(sale())), sec_client(tmp_path, FakeSec({}, block=True)), "VRTX", today=TODAY, live=True)
    assert section["sec"]["state"] == "error" and "SEC_USER_AGENT" in section["sec"]["reason"]
    assert section["message"] == UNVERIFIED_MESSAGE
    assert section["mcp"]["records"][0]["transaction_type"] == "disposed_type_unknown"
    assert not list((tmp_path / "sec_cache").glob("*.json"))  # error bodies are never cached


@pytest.mark.asyncio
async def test_unknown_ticker_is_reported(tmp_path):
    section = await build_insider_section(bpiq_section([]), sec_client(tmp_path, FakeSec({})), "ZZZZ", today=TODAY, live=True)
    assert section["sec"]["state"] == "unavailable" and "ZZZZ" in section["sec"]["reason"]
