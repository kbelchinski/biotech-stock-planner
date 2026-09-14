"""Deterministic criterion evaluation and overall eligibility.

Each criterion returns pass / fail / unknown / not_applied, with the observed value, threshold,
explanation, and sources. No provider access happens here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain.models import (
    CATALYST_TYPE_LABELS,
    LIQUIDITY_WEEKS,
    Catalyst,
    CatalystEvaluation,
    ClassificationStatus,
    CompanyProfile,
    CompanyResult,
    CriterionKey,
    CriterionResult,
    CriterionStatus,
    Eligibility,
    FinancialSnapshot,
    ListingInfo,
    PriceBarPoint,
    PriceHistory,
    ScanCriteria,
    SourceRef,
)
from app.screening import formatting as fmt
from app.screening.liquidity import compute_weekly_turnover
from app.screening.market_calendar import TradingWeek

US_LISTING_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "NYSEARCA"})

CRITERION_LABELS = {
    CriterionKey.CATALYST: "Catalyst type & timing",
    CriterionKey.MARKET_CAP: "Market capitalization",
    CriterionKey.PRICE: "Stock price",
    CriterionKey.RUNWAY: "Cash runway",
    CriterionKey.LIQUIDITY: "Liquidity",
    CriterionKey.LISTING: "Listing",
}


@dataclass(frozen=True)
class ScanContext:
    today: date
    criteria: ScanCriteria
    latest_session: date | None
    weeks: list[TradingWeek]


@dataclass
class CompanyInputs:
    ticker: str
    profile: CompanyProfile | None
    catalysts: list[Catalyst]
    price_history: PriceHistory | None = None
    market_data_error: str | None = None
    listing: ListingInfo | None = None
    listing_error: str | None = None
    financials: FinancialSnapshot | None = None
    financials_unavailable_reason: str | None = None
    issues: list[str] = field(default_factory=list)


def overall_eligibility(results: list[CriterionResult]) -> Eligibility:
    statuses = {r.status for r in results if r.status is not CriterionStatus.NOT_APPLIED}
    if CriterionStatus.FAIL in statuses:
        return Eligibility.DOES_NOT_QUALIFY
    if CriterionStatus.UNKNOWN in statuses:
        return Eligibility.INSUFFICIENT_DATA
    return Eligibility.QUALIFIES


def evaluate_company(inputs: CompanyInputs, ctx: ScanContext) -> CompanyResult:
    evaluations = [evaluate_catalyst(c, ctx) for c in sorted(inputs.catalysts, key=_catalyst_sort_key)]
    catalyst_result, primary = catalyst_criterion(evaluations, ctx)
    liquidity_result, liquidity_meta = liquidity_criterion(inputs, ctx)
    runway_result, runway_months = runway_criterion(inputs, ctx)
    price_result, price_value = price_criterion(inputs, ctx)

    criteria = [
        catalyst_result,
        market_cap_criterion(inputs, ctx),
        price_result,
        runway_result,
        liquidity_result,
        listing_criterion(inputs, ctx),
    ]
    return CompanyResult(
        ticker=inputs.ticker,
        name=(inputs.profile.name if inputs.profile else None)
        or (inputs.listing.name if inputs.listing else None),
        eligibility=overall_eligibility(criteria),
        criteria=criteria,
        catalysts=evaluations,
        primary_catalyst_event_id=primary.catalyst.event_id if primary else None,
        market_cap_usd=inputs.profile.market_cap_usd if inputs.profile else None,
        price=price_value,
        price_date=ctx.latest_session if price_value is not None else None,
        price_bars=[
            PriceBarPoint(
                session_date=bar.session_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in (inputs.price_history.bars if inputs.price_history else [])
        ],
        runway_months=runway_months,
        runway_is_mock=bool(inputs.financials and inputs.financials.source.is_mock),
        avg_weekly_turnover_usd=liquidity_meta.get("average_usd"),
        turnover_is_lower_bound=bool(liquidity_meta.get("is_lower_bound")),
        turnover_method=liquidity_meta.get("method"),
        exchange=inputs.listing.exchange if inputs.listing else None,
        issues=list(inputs.issues),
    )


# ------------------------------------------------------------------ catalyst


def _catalyst_sort_key(c: Catalyst) -> tuple[date, int]:
    return (c.catalyst_date or date.max, c.provider_record_id)


def evaluate_catalyst(catalyst: Catalyst, ctx: ScanContext) -> CatalystEvaluation:
    criteria = ctx.criteria
    if catalyst.catalyst_date is None:
        days = None
        timing = CriterionStatus.UNKNOWN
    else:
        days = (catalyst.catalyst_date - ctx.today).days
        timing = (
            CriterionStatus.PASS
            if criteria.catalyst_min_days <= days <= criteria.catalyst_max_days
            else CriterionStatus.FAIL
        )

    if catalyst.classification_status is ClassificationStatus.MATCHED:
        type_status = (
            CriterionStatus.PASS if catalyst.catalyst_type in criteria.catalyst_types else CriterionStatus.FAIL
        )
    elif catalyst.classification_status is ClassificationStatus.NOT_QUALIFYING:
        type_status = CriterionStatus.FAIL
    else:
        type_status = CriterionStatus.UNKNOWN

    if CriterionStatus.FAIL in (timing, type_status):
        status = CriterionStatus.FAIL
    elif timing is CriterionStatus.PASS and type_status is CriterionStatus.PASS:
        status = CriterionStatus.PASS
    else:
        status = CriterionStatus.UNKNOWN
    return CatalystEvaluation(
        catalyst=catalyst, days_until=days, type_status=type_status, timing_status=timing, status=status
    )


def catalyst_criterion(
    evaluations: list[CatalystEvaluation], ctx: ScanContext
) -> tuple[CriterionResult, CatalystEvaluation | None]:
    c = ctx.criteria
    window_start = ctx.today + timedelta(days=c.catalyst_min_days)
    window_end = ctx.today + timedelta(days=c.catalyst_max_days)
    types = ", ".join(CATALYST_TYPE_LABELS[t] for t in c.catalyst_types)
    threshold = (
        f"{types}; {c.catalyst_min_days}–{c.catalyst_max_days} calendar days inclusive "
        f"({fmt.day(window_start)} – {fmt.day(window_end)})"
    )

    passing = [e for e in evaluations if e.status is CriterionStatus.PASS]
    unknown = [e for e in evaluations if e.status is CriterionStatus.UNKNOWN]
    if passing:
        primary = passing[0]
        status = CriterionStatus.PASS
        explanation = (
            f"{len(passing)} of {len(evaluations)} catalyst(s) match both type and timing."
            if len(evaluations) > 1
            else "The catalyst matches both type and timing."
        )
    elif unknown:
        primary = unknown[0]
        status = CriterionStatus.UNKNOWN
        explanation = (
            "No catalyst definitely qualifies, but at least one could not be classified: "
            f"{primary.catalyst.classification_reason}."
        )
    else:
        primary = min(evaluations, key=lambda e: abs((e.days_until or 0) - c.catalyst_min_days), default=None)
        status = CriterionStatus.FAIL
        explanation = "No catalyst matches both the selected types and the timing window."

    return (
        CriterionResult(
            key=CriterionKey.CATALYST,
            label=CRITERION_LABELS[CriterionKey.CATALYST],
            status=status,
            observed=_describe_catalyst(primary) if primary else "No dated catalysts",
            threshold=threshold,
            explanation=explanation,
            sources=[primary.catalyst.source] if primary else [],
            details={"catalyst_count": len(evaluations)},
        ),
        primary,
    )


def _describe_catalyst(e: CatalystEvaluation) -> str:
    cat = e.catalyst
    label = (
        CATALYST_TYPE_LABELS[cat.catalyst_type]
        if cat.catalyst_type
        else (cat.stage_event_label or " ".join(filter(None, [cat.stage_label, cat.event_label])) or "Unlabelled")
    )
    when = f"{fmt.day(cat.catalyst_date)} ({e.days_until} days)" if e.days_until is not None else "undated"
    return f"{label} · {when}"


# ------------------------------------------------------------------ simple thresholds


def _not_applied(key: CriterionKey, threshold: str) -> CriterionResult:
    return CriterionResult(
        key=key,
        label=CRITERION_LABELS[key],
        status=CriterionStatus.NOT_APPLIED,
        observed=None,
        threshold=threshold,
        explanation="Disabled for this scan. 'Qualifies' does not cover this part of the strategy.",
    )


def market_cap_criterion(inputs: CompanyInputs, ctx: ScanContext) -> CriterionResult:
    c = ctx.criteria
    threshold = f"{fmt.usd(c.market_cap_min_usd)} – {fmt.usd(c.market_cap_max_usd)} inclusive"
    if not c.market_cap_enabled:
        return _not_applied(CriterionKey.MARKET_CAP, threshold)
    value = inputs.profile.market_cap_usd if inputs.profile else None
    sources = [inputs.profile.source] if inputs.profile else []
    if value is None:
        return CriterionResult(
            key=CriterionKey.MARKET_CAP,
            label=CRITERION_LABELS[CriterionKey.MARKET_CAP],
            status=CriterionStatus.UNKNOWN,
            observed=None,
            threshold=threshold,
            explanation="BPIQ returned no market capitalization for this company.",
            sources=sources,
        )
    passed = c.market_cap_min_usd <= value <= c.market_cap_max_usd
    return CriterionResult(
        key=CriterionKey.MARKET_CAP,
        label=CRITERION_LABELS[CriterionKey.MARKET_CAP],
        status=CriterionStatus.PASS if passed else CriterionStatus.FAIL,
        observed=fmt.usd(value),
        threshold=threshold,
        explanation=(
            "Within range." if passed else "Below the minimum." if value < c.market_cap_min_usd else "Above the maximum."
        )
        + " Units assumed USD (not stated in BPIQ docs).",
        sources=sources,
    )


def price_criterion(inputs: CompanyInputs, ctx: ScanContext) -> tuple[CriterionResult, float | None]:
    c = ctx.criteria
    threshold = f"Latest completed regular-session close above {fmt.price(c.price_above_usd)}"
    history = inputs.price_history
    close: float | None = None
    bar_date: date | None = None
    if history and ctx.latest_session:
        bar = next((b for b in history.bars if b.session_date == ctx.latest_session), None)
        if bar is not None:
            close, bar_date = bar.close, bar.session_date
    if not c.price_enabled:
        return _not_applied(CriterionKey.PRICE, threshold), close

    sources = [history.source.model_copy(update={"source_timestamp": bar_date.isoformat()})] if (
        history and bar_date
    ) else ([history.source] if history else [])
    if close is None:
        if inputs.market_data_error:
            reason = f"Market data unavailable: {inputs.market_data_error}"
        elif history and history.bars:
            reason = (
                f"No daily bar for the latest completed session ({fmt.day(ctx.latest_session)}); "
                f"latest available bar is {fmt.day(history.bars[-1].session_date)}. Older closes are not used."
            )
        else:
            reason = "No daily bars were returned for this symbol."
        return (
            CriterionResult(
                key=CriterionKey.PRICE,
                label=CRITERION_LABELS[CriterionKey.PRICE],
                status=CriterionStatus.UNKNOWN,
                observed=None,
                threshold=threshold,
                explanation=reason,
                sources=sources,
            ),
            None,
        )
    passed = close > c.price_above_usd
    return (
        CriterionResult(
            key=CriterionKey.PRICE,
            label=CRITERION_LABELS[CriterionKey.PRICE],
            status=CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            observed=f"{fmt.price(close)} close on {fmt.day(bar_date)}",
            threshold=threshold,
            explanation=("Above" if passed else "At or below") + " the threshold (raw, unadjusted close).",
            sources=sources,
        ),
        close,
    )


def runway_criterion(inputs: CompanyInputs, ctx: ScanContext) -> tuple[CriterionResult, float | None]:
    c = ctx.criteria
    threshold = f"At least {c.runway_min_months:g} months"
    fin = inputs.financials
    runway: float | None = None
    status = CriterionStatus.UNKNOWN
    observed: str | None = None
    explanation: str

    if fin is None:
        explanation = inputs.financials_unavailable_reason or "No financial data available for this company."
    elif fin.cash_usd is None or fin.monthly_net_burn_usd is None:
        explanation = "Financial data is incomplete (cash or monthly burn missing)."
    elif fin.monthly_net_burn_usd <= 0:
        status = CriterionStatus.PASS
        observed = f"Not burning cash (cash {fmt.usd(fin.cash_usd)})"
        explanation = f"Net monthly cash flow is non-negative as of {fmt.day(fin.as_of)}, so runway is not limited by burn."
    else:
        runway = fin.cash_usd / fin.monthly_net_burn_usd
        status = CriterionStatus.PASS if runway >= c.runway_min_months else CriterionStatus.FAIL
        observed = fmt.months(runway)
        explanation = (
            f"Cash {fmt.usd(fin.cash_usd)} ÷ monthly net burn {fmt.usd(fin.monthly_net_burn_usd)}, "
            f"as of {fmt.day(fin.as_of)}."
        )
    if fin is not None and fin.source.is_mock:
        explanation = "DEMO MOCK DATA. " + explanation

    if not c.runway_enabled:
        return _not_applied(CriterionKey.RUNWAY, threshold), runway
    return (
        CriterionResult(
            key=CriterionKey.RUNWAY,
            label=CRITERION_LABELS[CriterionKey.RUNWAY],
            status=status,
            observed=observed,
            threshold=threshold,
            explanation=explanation,
            sources=[fin.source] if fin else [],
        ),
        runway,
    )


def liquidity_criterion(inputs: CompanyInputs, ctx: ScanContext) -> tuple[CriterionResult, dict]:
    c = ctx.criteria
    threshold = (
        f"Average weekly dollar turnover above {fmt.usd(c.liquidity_min_avg_weekly_usd)} "
        f"across the last {LIQUIDITY_WEEKS} completed trading weeks"
    )
    history = inputs.price_history
    meta: dict = {}
    result: CriterionResult

    if history is None or not ctx.weeks:
        reason = (
            f"Market data unavailable: {inputs.market_data_error}"
            if inputs.market_data_error
            else "No market history available."
        )
        result = CriterionResult(
            key=CriterionKey.LIQUIDITY,
            label=CRITERION_LABELS[CriterionKey.LIQUIDITY],
            status=CriterionStatus.UNKNOWN,
            observed=None,
            threshold=threshold,
            explanation=reason,
        )
    else:
        calc = compute_weekly_turnover(history.bars, ctx.weeks)
        meta = {"average_usd": calc.average_usd, "is_lower_bound": calc.is_lower_bound, "method": calc.method}
        details = {
            "method": calc.method,
            "weeks": [w.model_dump(mode="json") for w in calc.weeks],
            "missing_sessions": [d.isoformat() for d in calc.missing_sessions],
            "approximated_sessions": calc.approximated_sessions,
        }
        sources = [history.source]
        observed = fmt.usd(calc.average_usd)
        if not calc.has_any_data:
            status = CriterionStatus.UNKNOWN
            observed = None
            meta["average_usd"] = None
            explanation = (
                f"No daily bars in the last {LIQUIDITY_WEEKS} completed weeks: insufficient history, "
                "not zero trading activity."
            )
        elif not calc.is_lower_bound:
            passed = calc.average_usd is not None and calc.average_usd > c.liquidity_min_avg_weekly_usd
            status = CriterionStatus.PASS if passed else CriterionStatus.FAIL
            explanation = f"Complete history for all {sum(len(w.sessions) for w in ctx.weeks)} sessions."
        elif calc.average_usd is not None and calc.average_usd > c.liquidity_min_avg_weekly_usd:
            status = CriterionStatus.PASS
            observed = f"≥ {observed}"
            explanation = (
                f"{len(calc.missing_sessions)} session(s) have no bar, but turnover from available sessions "
                "already exceeds the threshold. Missing sessions can only add turnover."
            )
        else:
            status = CriterionStatus.UNKNOWN
            observed = f"≥ {observed} (incomplete)"
            explanation = (
                f"{len(calc.missing_sessions)} session(s) have no bar, and available sessions alone do not "
                "exceed the threshold. Missing history is treated as insufficient data, not zero."
            )
        if calc.approximated_sessions:
            explanation += f" {calc.approximated_sessions} session(s) approximated with close × volume."
        result = CriterionResult(
            key=CriterionKey.LIQUIDITY,
            label=CRITERION_LABELS[CriterionKey.LIQUIDITY],
            status=status,
            observed=observed,
            threshold=threshold,
            explanation=explanation,
            sources=sources,
            details=details,
        )

    if not c.liquidity_enabled:
        return _not_applied(CriterionKey.LIQUIDITY, threshold), meta
    return result, meta


def listing_criterion(inputs: CompanyInputs, ctx: ScanContext) -> CriterionResult:
    threshold = "Active US exchange-listed equity (NASDAQ, NYSE, AMEX, ARCA, BATS, NYSEARCA); not OTC"
    if not ctx.criteria.listing_enabled:
        return _not_applied(CriterionKey.LISTING, threshold)
    note = " Biotech classification is inferred from BPIQ catalyst coverage, not verified per company."
    listing = inputs.listing
    if listing is None:
        explanation = (
            f"Listing could not be verified: {inputs.listing_error}"
            if inputs.listing_error
            else "Symbol was not found in Alpaca's asset master, so listing could not be verified."
        )
        return CriterionResult(
            key=CriterionKey.LISTING,
            label=CRITERION_LABELS[CriterionKey.LISTING],
            status=CriterionStatus.UNKNOWN,
            observed=None,
            threshold=threshold,
            explanation=explanation + note,
        )
    problems = []
    if listing.exchange not in US_LISTING_EXCHANGES:
        problems.append(f"exchange is {listing.exchange}")
    if listing.status != "active":
        problems.append(f"status is {listing.status}")
    if listing.asset_class != "us_equity":
        problems.append(f"asset class is {listing.asset_class}")
    return CriterionResult(
        key=CriterionKey.LISTING,
        label=CRITERION_LABELS[CriterionKey.LISTING],
        status=CriterionStatus.FAIL if problems else CriterionStatus.PASS,
        observed=f"{listing.exchange} · {listing.status}",
        threshold=threshold,
        explanation=("Not eligible: " + ", ".join(problems) + "." if problems else "Listed and active.") + note,
        sources=[listing.source],
    )


def source_list(*sources: SourceRef | None) -> list[SourceRef]:
    return [s for s in sources if s is not None]
