"""Insider transactions: BPIQ rows, optional SEC Form 4 enrichment, amendment handling and conservative matching.

BPIQ (verified 2026-09-18) returns only an acquired/disposed flag, so its rows are "Acquired/Disposed — transaction
type unknown". SEC enrichment can add the official transaction code and filing detail, but only when a BPIQ row
matches exactly one SEC transaction on every comparable field (issuer, reporting owner, transaction date,
acquired/disposed, share amount, and security type and price where both sides have them), and that SEC transaction
fits no other BPIQ row. Anything else keeps the BPIQ row unclassified; unmatched SEC transactions are listed separately.

Amendments (Form 4/A): the amendment names its original by dateOfOriginalSubmission. Amended transactions replace
the original's transactions with the same (table, security, transaction date, code, A/D) key; when that cannot be
done one-to-one the amended rows are shown but excluded from counts, so nothing is counted twice.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.domain.research import InsiderMatch, InsiderTransaction
from app.logging_setup import get_logger
from app.providers.errors import ProviderError
from app.providers.sec_edgar.client import SecEdgarClient
from app.providers.sec_edgar.form4 import Form4ParseError, ParsedFiling

log = get_logger("insider_enrichment")

UNVERIFIED_MESSAGE = (
    "BPIQ identifies shares acquired or disposed of but does not provide enough detail to classify the transaction. "
    "Filing links and subsequent holdings are unavailable from this source."
)
SEC_LOOKBACK_DAYS = 365
NAME_NOISE = frozenset({"jr", "sr", "ii", "iii", "iv", "md", "phd", "dr", "mr", "ms", "mrs"})
SHARE_TOLERANCE = 1e-3
EXCLUDED_STATUSES = ("unreconciled_amendment",)


# ---------------------------------------------------------------- amendments


def _norm_security(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(re.findall(r"[a-z0-9]+", value.lower())) or None


def _amend_key(tx: InsiderTransaction) -> tuple:
    return (tx.table, _norm_security(tx.security_type), tx.transaction_date, tx.transaction_code, tx.acquired_disposed)


def _add_note(tx: InsiderTransaction, text: str) -> None:
    tx.note = f"{tx.note} {text}" if tx.note else text


def reconcile_amendments(filings: list[ParsedFiling], *, window_start: date | None = None) -> list[InsiderTransaction]:
    """Mark superseded and unreconcilable transactions in place. Returns every transaction (none are dropped)."""
    originals = [f for f in filings if not f.filing.is_amendment]
    amendments = sorted((f for f in filings if f.filing.is_amendment), key=lambda f: (f.filing.filing_date or date.min, f.filing.accession_number))
    # Transactions still in force for each original filing, extended as amendments are applied in filing order.
    in_force: dict[str, list[InsiderTransaction]] = {f.filing.accession_number: list(f.transactions) for f in originals}

    for amend in amendments:
        ref = amend.filing
        matches = [
            o for o in originals
            if o.filing.reporting_owner_cik == ref.reporting_owner_cik
            and ref.date_of_original_submission is not None
            and o.filing.filing_date == ref.date_of_original_submission
            and (o.filing.period_of_report is None or ref.period_of_report is None or o.filing.period_of_report == ref.period_of_report)
        ]
        if len(matches) != 1:
            outside = (
                not matches and ref.date_of_original_submission is not None and window_start is not None
                and ref.date_of_original_submission < window_start
            )
            for tx in amend.transactions:
                if outside:
                    _add_note(tx, f"Amends a filing from {ref.date_of_original_submission}, outside the retrieved window.")
                else:
                    tx.amendment_status = "unreconciled_amendment"
                    _add_note(
                        tx,
                        "Amendment whose original filing could not be identified uniquely among retrieved filings; "
                        "shown but excluded from counts to avoid double counting.",
                    )
            continue

        original = matches[0]
        pool = in_force[original.filing.accession_number]
        if not amend.transactions:
            for tx in pool:
                tx.amendment_status = "amended_holdings_only"
                _add_note(tx, f"Filing amended by {ref.accession_number} (holdings only; transactions unchanged).")
            continue

        by_key: dict[tuple, list[InsiderTransaction]] = {}
        for tx in amend.transactions:
            by_key.setdefault(_amend_key(tx), []).append(tx)
        for key, amended in by_key.items():
            replaced = [tx for tx in pool if _amend_key(tx) == key]
            if not replaced:
                for tx in amended:
                    tx.amendment_status = "added_by_amendment"
                    _add_note(tx, f"Added by amendment to {original.filing.accession_number}.")
                pool.extend(amended)
            elif len(replaced) == len(amended):
                for old, new in zip(replaced, amended):
                    old.superseded_by = ref.accession_number
                    pool.remove(old)
                    _add_note(new, f"Replaces a transaction reported in {old.filing.accession_number if old.filing else 'the original filing'}.")
                pool.extend(amended)
            else:
                for tx in amended:
                    tx.amendment_status = "unreconciled_amendment"
                    _add_note(
                        tx,
                        f"The amendment reports {len(amended)} and the original {len(replaced)} transaction(s) with the same "
                        "date, code and security; not reconciled, so excluded from counts.",
                    )
    return [tx for f in filings for tx in f.transactions]


# ---------------------------------------------------------------- matching


def name_tokens(name: str | None) -> frozenset[str]:
    if not name:
        return frozenset()
    words = re.findall(r"[a-z0-9]+", name.lower().replace(".", ""))
    return frozenset(w for w in words if w not in NAME_NOISE)


def _owner_matches(bpiq_name: str | None, sec_names: str | None) -> bool:
    wanted = name_tokens(bpiq_name)
    return bool(wanted) and any(wanted == name_tokens(n) for n in (sec_names or "").split(";"))


def _compare(b: InsiderTransaction, s: InsiderTransaction, issuer_cik: int | None) -> tuple[bool, list[str], list[str]]:
    compared, skipped = [], []
    if issuer_cik is None or s.issuer_cik is None or int(s.issuer_cik) != issuer_cik:
        return False, compared, skipped
    compared.append("issuer")
    if not _owner_matches(b.insider_name, s.insider_name):
        return False, compared, skipped
    compared.append("reporting owner")
    if b.transaction_date != s.transaction_date:
        return False, compared, skipped
    compared.append("transaction date")
    if b.acquired_disposed and s.acquired_disposed:
        if b.acquired_disposed != s.acquired_disposed:
            return False, compared, skipped
        compared.append("acquired/disposed")
    else:
        skipped.append("acquired/disposed")
    if b.shares is None or s.shares is None or abs(b.shares - s.shares) > SHARE_TOLERANCE:
        return False, compared, skipped
    compared.append("shares")
    bs, ss = _norm_security(b.security_type), _norm_security(s.security_type)
    if bs and ss:
        if bs != ss:
            return False, compared, skipped
        compared.append("security type")
    else:
        skipped.append("security type")
    # BPIQ reports missing prices as 0, which the BPIQ normalizer turns into None.
    if b.price is not None and s.price is not None:
        if abs(b.price - s.price) > max(0.005, 0.0005 * abs(s.price)):
            return False, compared, skipped
        compared.append("price")
    else:
        skipped.append("price (missing on one side)")
    return True, compared, skipped


def _merge(b: InsiderTransaction, s: InsiderTransaction, match: InsiderMatch) -> InsiderTransaction:
    merged = s.model_copy(deep=True)
    merged.origin = "bpiq+sec"
    merged.source = b.source
    merged.sec_source = s.source
    merged.match = match
    agreed = {
        "issuer": (), "reporting owner": ("insider_name",), "transaction date": ("transaction_date",),
        "acquired/disposed": ("acquired_disposed",), "shares": ("shares",), "security type": ("security_type",), "price": ("price",),
    }
    for label in match.compared:
        for field in agreed.get(label, ()):
            merged.field_sources[field] = "bpiq+sec"
    merged.unavailable_fields = []
    return merged


def match_transactions(
    bpiq: list[InsiderTransaction], sec: list[InsiderTransaction], *, issuer_cik: int | None
) -> tuple[list[InsiderTransaction], list[InsiderTransaction]]:
    """Returns (rows for the BPIQ table, SEC transactions not matched to any BPIQ row)."""
    active = [s for s in sec if s.superseded_by is None]
    candidates: list[list[tuple[int, list[str], list[str]]]] = []
    claims: dict[int, set[int]] = {}
    for i, b in enumerate(bpiq):
        found = []
        if b.insider_name and b.transaction_date and b.shares is not None:
            for j, s in enumerate(active):
                ok, compared, skipped = _compare(b, s, issuer_cik)
                if ok:
                    found.append((j, compared, skipped))
                    claims.setdefault(j, set()).add(i)
        candidates.append(found)

    rows: list[InsiderTransaction] = []
    used: set[int] = set()
    for i, b in enumerate(bpiq):
        found = candidates[i]
        row = b.model_copy(deep=True)
        if not (b.insider_name and b.transaction_date and b.shares is not None):
            row.match = InsiderMatch(status="insufficient", note="BPIQ row lacks the owner, transaction date or share amount needed to match.")
        elif not found:
            row.match = InsiderMatch(status="unmatched", note="No SEC transaction agrees on owner, date, acquired/disposed, shares and security.")
        elif len(found) == 1 and claims[found[0][0]] == {i} and active[found[0][0]].amendment_status not in EXCLUDED_STATUSES:
            j, compared, skipped = found[0]
            used.add(j)
            row = _merge(b, active[j], InsiderMatch(status="matched", compared=compared, not_compared=skipped, candidates=1))
        else:
            others = len({k for j, _, _ in found for k in claims[j]}) - 1
            reason = (
                f"{len(found)} SEC transactions fit this row" if len(found) > 1
                else f"the fitting SEC transaction also fits {others} other BPIQ row(s)" if others
                else "the fitting SEC transaction is from an unreconciled amendment"
            )
            row.match = InsiderMatch(status="ambiguous", candidates=len(found), note=f"Left unclassified: {reason}.")
        rows.append(row)
    sec_only = [s for j, s in enumerate(active) if j not in used]
    return rows, sec_only


# ---------------------------------------------------------------- summary


def summarize(rows: list[InsiderTransaction], sec_only: list[InsiderTransaction]) -> dict[str, Any]:
    """Counts. Only SEC-coded rows are classified; A/D-only rows are excluded from purchase metrics."""
    counted = [r for r in rows + sec_only if r.transaction_code and r.amendment_status not in EXCLUDED_STATUSES]
    by_code: dict[str, dict[str, Any]] = {}
    for r in counted:
        entry = by_code.setdefault(r.transaction_code or "?", {"label": r.transaction_label, "count": 0, "shares": 0.0})
        entry["count"] += 1
        entry["shares"] += r.shares or 0
    purchases = [r for r in counted if r.transaction_code == "P" and r.table == "non_derivative"]
    unclassified = [r for r in rows if not r.transaction_code]
    return {
        "coded_by_code": by_code,
        "code_p_purchases": {
            "label": "Open market or private purchases (SEC code P, non-derivative)",
            "count": len(purchases),
            "shares": sum(r.shares or 0 for r in purchases),
            "note": "SEC code P does not distinguish open-market from private purchases.",
        },
        "unclassified_bpiq": {
            "acquired": sum(1 for r in unclassified if r.acquired_disposed == "A"),
            "disposed": sum(1 for r in unclassified if r.acquired_disposed == "D"),
            "note": "Acquired/disposed rows without an SEC transaction code are excluded from purchase metrics.",
        },
        "excluded_unreconciled": sum(1 for r in rows + sec_only if r.amendment_status in EXCLUDED_STATUSES),
    }


# ---------------------------------------------------------------- section builder


def _dump(rows: list[InsiderTransaction]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in rows]


async def build_insider_section(
    bpiq_section: dict[str, Any], sec: SecEdgarClient | None, ticker: str, *, today: date, live: bool
) -> dict[str, Any]:
    bpiq = [InsiderTransaction.model_validate(r) for r in bpiq_section.get("records", [])]
    sec_state: dict[str, Any] = {"state": "off", "reason": None, "records": [], "superseded": [], "issues": []}
    sec_rows: list[InsiderTransaction] = []
    issuer_cik: int | None = None

    if not live:
        sec_state["reason"] = "SEC enrichment runs only in live mode."
    elif sec is None or not sec.configured:
        sec_state["reason"] = sec.problem if sec else "SEC enrichment is not configured."
    else:
        dates = [r.transaction_date for r in bpiq if r.transaction_date]
        since = min([today - timedelta(days=SEC_LOOKBACK_DAYS), *(d - timedelta(days=7) for d in dates)])
        http = sec.http()
        try:
            issuer_cik = await sec.cik_for_ticker(http, ticker)
            if issuer_cik is None:
                sec_state.update(state="unavailable", reason=f"{ticker} is not in the SEC ticker list; SEC filings were not searched.")
            else:
                entries, index_retrieved, truncated = await sec.ownership_filings(http, issuer_cik, since=since)
                filings: list[ParsedFiling] = []
                for entry in entries:
                    try:
                        filings.append(await sec.filing(http, issuer_cik, entry))
                    except Form4ParseError as exc:
                        sec_state["issues"].append(str(exc))
                all_sec = reconcile_amendments(filings, window_start=since)
                # Filings listed under the issuer can include ones where it is the reporting owner of another issuer.
                sec_rows = [t for t in all_sec if t.issuer_cik and int(t.issuer_cik) == issuer_cik]
                sec_state.update(
                    state="ok",
                    cik=issuer_cik,
                    window_start=since.isoformat(),
                    index_retrieved_at=index_retrieved.isoformat(),
                    filings_checked=len(filings),
                    amendments=sum(1 for f in filings if f.filing.is_amendment),
                    truncated=truncated,
                    network_requests=sec.network_requests,
                )
                if truncated:
                    sec_state["issues"].append(
                        f"Only the {len(entries)} most recent Form 4/4A filings were checked (SEC_MAX_FILINGS); older BPIQ rows may stay unmatched."
                    )
        except ProviderError as exc:
            sec_state.update(state="error", reason=exc.message + (f" {exc.hint}" if exc.hint else ""))
            log.warning("SEC enrichment failed for %s: %s", ticker, exc.message)
        finally:
            await http.aclose()

    if sec_state["state"] == "ok":
        rows, sec_only = match_transactions(bpiq, sec_rows, issuer_cik=issuer_cik)
    else:
        rows = [r.model_copy(update={"match": InsiderMatch(status="not_attempted")}) for r in bpiq]
        sec_only = []
    superseded = [s for s in sec_rows if s.superseded_by]
    sec_state["records"] = _dump(sec_only)
    sec_state["superseded"] = _dump(superseded)

    matched = sum(1 for r in rows if r.match and r.match.status == "matched")
    if sec_state["state"] == "ok":
        message = (
            f"SEC enrichment checked {sec_state['filings_checked']} Form 4/4A filing(s). {matched} of {len(rows)} BPIQ row(s) matched "
            "exactly one SEC transaction and show its official code; the rest stay unclassified (acquired/disposed only). "
            "SEC transactions not matched to a BPIQ row are listed separately."
        )
    else:
        message = UNVERIFIED_MESSAGE
    return {
        "mcp": {**bpiq_section, "records": _dump(rows)},
        "sec": sec_state,
        "summary": summarize(rows, sec_only),
        "message": message,
    }
