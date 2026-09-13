"""CSV export of a stored scan. Contains no credentials; mock values are labelled."""

from __future__ import annotations

import csv
import io

from app.domain.models import CATALYST_TYPE_LABELS, CriterionKey, DataMode, ScanRun

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> object:
    """Neutralise spreadsheet formula injection in text cells."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def scan_to_csv(run: ScanRun) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    criterion_keys = list(CriterionKey)
    writer.writerow(
        [
            "data_mode",
            "scan_id",
            "scan_date",
            "latest_completed_session",
            "ticker",
            "company",
            "eligibility",
            "primary_catalyst_type",
            "primary_catalyst_label",
            "primary_catalyst_date",
            "days_until_catalyst",
            "catalyst_count",
            "market_cap_usd",
            "price_usd",
            "price_date",
            "cash_runway_months",
            "runway_data_label",
            "avg_weekly_turnover_usd",
            "turnover_is_lower_bound",
            "turnover_method",
            "exchange",
            *[f"criterion_{k.value}" for k in criterion_keys],
            "not_applied_criteria",
        ]
    )
    not_applied = ";".join(k.value for k in run.not_applied_criteria)
    for result in run.results:
        primary = next(
            (e for e in result.catalysts if e.catalyst.event_id == result.primary_catalyst_event_id), None
        )
        statuses = {c.key: c.status.value for c in result.criteria}
        runway_criterion = next(c for c in result.criteria if c.key is CriterionKey.RUNWAY)
        if result.runway_is_mock:
            runway_label = "DEMO MOCK"
        elif run.mode is DataMode.LIVE and result.runway_months is None:
            runway_label = "unavailable from configured providers"
        else:
            runway_label = runway_criterion.status.value
        writer.writerow(
            [
                _cell(v)
                for v in [
                    run.mode.value,
                    run.id,
                    run.scan_date.isoformat(),
                    run.latest_completed_session.isoformat() if run.latest_completed_session else "",
                    result.ticker,
                    result.name or "",
                    result.eligibility.value,
                    CATALYST_TYPE_LABELS[primary.catalyst.catalyst_type]
                    if primary and primary.catalyst.catalyst_type
                    else "",
                    (primary.catalyst.stage_event_label or "") if primary else "",
                    primary.catalyst.catalyst_date.isoformat() if primary and primary.catalyst.catalyst_date else "",
                    primary.days_until if primary and primary.days_until is not None else "",
                    len(result.catalysts),
                    f"{result.market_cap_usd:.0f}" if result.market_cap_usd is not None else "",
                    f"{result.price:.4f}" if result.price is not None else "",
                    result.price_date.isoformat() if result.price_date else "",
                    f"{result.runway_months:.1f}" if result.runway_months is not None else "",
                    runway_label,
                    f"{result.avg_weekly_turnover_usd:.0f}" if result.avg_weekly_turnover_usd is not None else "",
                    str(result.turnover_is_lower_bound).lower(),
                    result.turnover_method or "",
                    result.exchange or "",
                    *[statuses.get(k, "") for k in criterion_keys],
                    not_applied,
                ]
            ]
        )
    return buffer.getvalue()
