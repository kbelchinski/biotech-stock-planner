"""Normalized models for research features: watchlist, catalyst tracking, notifications,
price context, trade plans, trades, journal, performance, and MCP-sourced research records.

Every record carries its data mode so demo and live data never mix.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.models import CatalystType, ClassificationStatus, DataMode, SourceRef

# ---------------------------------------------------------------- catalyst tracking


class DatePrecision(StrEnum):
    # BPIQ supplies a single `catalyst_date`; whether it is an exact or a guided date is not documented.
    PROVIDER_DATE = "provider_date"
    RANGE = "range"
    UNDATED = "undated"


class TrackedStatus(StrEnum):
    ACTIVE = "active"
    # No longer returned by a complete provider query that should have covered it. Not a confirmed cancellation.
    NOT_RETURNED = "not_returned"
    DATE_PASSED = "date_passed"


class OutcomeRecord(BaseModel):
    """A BPIQ /historical-catalysts/ record matched to a tracked catalyst."""

    provider_record_id: int
    catalyst_date: date | None
    stage: str | None
    drug_name: str | None
    text: str | None
    detailed_text: str | None
    source_url: str | None
    news_published_at: str | None
    open_price_gap_percent: float | None
    intra_day_price_change_percent: float | None
    provider_created_at: str | None
    provider_updated_at: str | None
    match_basis: str
    source: SourceRef


class CatalystRevision(BaseModel):
    event_id: str
    ticker: str
    observed_at: datetime
    kind: Literal[
        "first_seen", "date_changed", "type_changed", "note_changed", "not_returned", "returned_again", "outcome_reported"
    ]
    field: str | None = None
    previous: str | None = None
    current: str | None = None


class TrackedCatalyst(BaseModel):
    mode: DataMode
    event_id: str
    provider_record_id: int
    ticker: str
    drug_name: str | None
    indications: list[str] = Field(default_factory=list)
    stage_label: str | None
    event_label: str | None
    stage_event_label: str | None
    catalyst_type: CatalystType | None
    classification_status: ClassificationStatus
    classification_reason: str
    catalyst_date: date | None
    date_precision: DatePrecision
    range_start: date | None = None
    range_end: date | None = None
    note: str | None
    source_url: str | None
    provider_flags: dict[str, bool | None] = Field(default_factory=dict)
    status: TrackedStatus
    first_seen_at: datetime
    last_seen_at: datetime
    not_returned_since: datetime | None = None
    outcome: OutcomeRecord | None = None
    source: SourceRef


class ReminderRule(BaseModel):
    offset: int = Field(ge=0, le=365)
    unit: Literal["calendar", "trading"]


class MonitoringSettings(BaseModel):
    # Confirmed 2026-09-14: 14 and 3 calendar days; daily refresh while the backend runs.
    reminder_rules: list[ReminderRule] = Field(
        default_factory=lambda: [ReminderRule(offset=14, unit="calendar"), ReminderRule(offset=3, unit="calendar")]
    )
    daily_refresh_enabled: bool = True
    # New York wall-clock time; after BAR_SETTLE_AFTER_CLOSE for a normal session.
    daily_refresh_time_ny: str = Field("20:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    benchmark_symbol: str = Field("XBI", pattern=r"^[A-Z.]{1,10}$")


class Notification(BaseModel):
    id: str
    mode: DataMode
    created_at: datetime
    ticker: str
    event_id: str | None
    kind: Literal["date_changed", "type_changed", "not_returned", "returned_again", "outcome_reported", "reminder", "refresh_failed", "paper_trade"]
    title: str
    body: str
    dedupe_key: str
    read: bool = False


class FirstSeen(BaseModel):
    basis: Literal["first_qualified", "first_evaluated"]
    scan_id: str
    scan_date: date
    price_session: date | None
    close: float | None


class WatchItem(BaseModel):
    mode: DataMode
    ticker: str
    name: str | None = None
    added_at: datetime
    note: str | None = None
    last_refresh_at: datetime | None = None
    last_refresh_ok_at: datetime | None = None
    last_refresh_error: str | None = None


# ---------------------------------------------------------------- price context


class MetricStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_HISTORY = "insufficient_history"
    MISSING_BARS = "missing_bars"
    UNAVAILABLE = "unavailable"


class Metric(BaseModel):
    key: str
    label: str
    value: float | None
    unit: Literal["pct", "ratio", "usd", "shares", "pct_points"]
    status: MetricStatus
    formula: str
    lookback: str
    detail: str | None = None


class PriceGap(BaseModel):
    session: date
    previous_session: date
    previous_close: float
    open: float
    gap_pct: float


class PriceContext(BaseModel):
    ticker: str
    benchmark_symbol: str
    feed: str
    adjustment: str
    latest_completed_session: date
    latest_bar_session: date | None
    expected_sessions: int
    present_sessions: int
    window_start: date
    retrieved_at: datetime | None
    freshness: str
    metrics: list[Metric]
    gaps: list[PriceGap]
    gap_threshold_pct: float
    first_seen: FirstSeen | None
    conventions: list[str]
    issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- plans, trades, journal


class TradeKind(StrEnum):
    PLANNED = "planned"
    HYPOTHETICAL = "hypothetical"
    ACTUAL = "actual"
    PAPER = "paper"


class TradeStatus(StrEnum):
    PLANNED = "planned"
    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    CLOSED = "closed"
    PRICE_UNAVAILABLE = "price_unavailable"
    CANCELLED = "cancelled"


class TradePlanInput(BaseModel):
    ticker: str = Field(pattern=r"^[A-Z.]{1,10}$")
    entry_price: float | None = None
    planned_exit_date: date | None = None
    thesis: str = ""
    invalidation: str = ""
    loss_budget_usd: float | None = None
    stop_price: float | None = None
    position_shares: float | None = None
    capital_allocation_usd: float | None = None
    portfolio_value_usd: float | None = None
    decline_scenarios_pct: list[float] = Field(default_factory=lambda: [10.0, 25.0, 50.0])


class LossScenario(BaseModel):
    decline_pct: float
    loss_usd: float | None
    portfolio_impact_pct: float | None


class TradePlanOutput(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    capital_commitment_usd: float | None
    capital_basis: str | None
    implied_shares: float | None
    stop_based_shares: int | None
    stop_based_capital_usd: float | None
    stop_based_note: str | None
    risk_per_share_usd: float | None
    portfolio_exposure_pct: float | None
    loss_scenarios: list[LossScenario]
    disclaimer: str


class Trade(BaseModel):
    id: str
    mode: DataMode
    kind: TradeKind
    status: TradeStatus
    ticker: str
    created_at: datetime
    updated_at: datetime
    plan: TradePlanInput | None = None
    catalyst_event_id: str | None = None
    catalyst_category: str | None = None
    source_scan_id: str | None = None
    entry_date: date | None = None
    entry_price: float | None = None
    entry_basis: str | None = None
    planned_exit_date: date | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    exit_basis: str | None = None
    shares: float | None = None
    notional_usd: float | None = None
    cost_pct_per_side: float = 0.0
    slippage_pct_per_side: float = 0.0
    thesis: str = ""
    invalidation: str = ""
    notes: str = ""
    history: list[str] = Field(default_factory=list)


class JournalEntry(BaseModel):
    id: str
    mode: DataMode
    created_at: datetime
    ticker: str
    decision: Literal["enter", "skip", "exit", "note"]
    trade_id: str | None = None
    thesis: str = ""
    expected_catalyst: str = ""
    reasons: str = ""
    result_note: str = ""


class OutcomeRow(BaseModel):
    trade_id: str
    ticker: str
    category: str
    entry_date: date
    exit_date: date
    net_return: float
    benchmark_return: float | None
    relative_return: float | None


class CategoryStats(BaseModel):
    category: str
    count: int
    win_rate: float | None
    average_net_return: float | None


class PerformanceSummary(BaseModel):
    kind: TradeKind
    closed_count: int
    open_count: int
    unresolved_count: int
    observation_start: date | None
    observation_end: date | None
    win_rate: float | None
    average_win: float | None
    average_loss: float | None
    total_compounded_return: float | None
    max_drawdown: float | None
    worst: list[OutcomeRow]
    benchmark_relative_average: float | None
    benchmark_coverage: int
    by_category: list[CategoryStats]
    open_positions: list[dict[str, Any]]
    conventions: list[str]
    warnings: list[str]


# ---------------------------------------------------------------- MCP research records


class Provenance(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    CALCULATED = "calculated"


class FinancialMeasure(BaseModel):
    key: str
    label: str
    value: float | str | None
    units: str | None
    reference_period: str | None
    reference_date: date | None
    provenance: Provenance
    mapping_verified: bool
    raw_field: str | None
    formula: str | None = None
    note: str | None = None
    source: SourceRef | None


InsiderCategory = Literal[
    # Official SEC code P: "Open market or private purchase". The code alone does not separate the two.
    "purchase_open_market_or_private",
    "sale_open_market_or_private",
    "grant_or_award",
    "derivative_exercise_or_conversion",
    "tax_or_exercise_price_withholding",
    "disposition_to_issuer",
    "gift",
    "other_coded",
    # Only an acquired (A) / disposed (D) flag is known: the transaction type is unknown.
    "acquired_type_unknown",
    "disposed_type_unknown",
    "unknown",
]


class SecFilingRef(BaseModel):
    """The Form 4 / 4/A an SEC-enriched transaction came from."""

    accession_number: str
    form: str
    filing_date: date | None
    period_of_report: date | None
    is_amendment: bool
    date_of_original_submission: date | None
    url: str
    issuer_cik: str | None
    reporting_owner_cik: str | None
    retrieved_at: datetime | None


class InsiderMatch(BaseModel):
    # matched: exactly one SEC transaction agrees on every compared field, and no other BPIQ row claims it.
    # ambiguous: more than one SEC transaction (or BPIQ row) fits; the BPIQ row stays unclassified.
    # unmatched: no SEC transaction fits. insufficient: the BPIQ row lacks owner, date or shares.
    # not_attempted: SEC enrichment is not configured or failed.
    status: Literal["matched", "ambiguous", "unmatched", "insufficient", "not_attempted"]
    compared: list[str] = []
    not_compared: list[str] = []
    candidates: int = 0
    note: str | None = None


class InsiderTransaction(BaseModel):
    origin: Literal["bpiq", "sec", "bpiq+sec"] = "bpiq"
    insider_name: str | None
    role: str | None
    # Raw SEC transaction code (P, S, A, M, F, ...). Never derived from BPIQ's A/D flag.
    transaction_code: str | None
    transaction_label: str | None = None
    transaction_type: InsiderCategory
    acquired_disposed: Literal["A", "D"] | None = None
    transaction_date: date | None
    filing_date: date | None
    shares: float | None
    price: float | None
    value_usd: float | None
    shares_owned_after: float | None
    source_url: str | None
    mapping_verified: bool
    source: SourceRef
    security_type: str | None = None
    note: str | None = None
    table: Literal["non_derivative", "derivative"] | None = None
    direct_or_indirect: Literal["D", "I"] | None = None
    nature_of_ownership: str | None = None
    footnotes: list[str] = []
    issuer_cik: str | None = None
    reporting_owner_cik: str | None = None
    filing: SecFilingRef | None = None
    amendment_status: Literal["original", "amendment", "amended_holdings_only", "added_by_amendment", "unreconciled_amendment"] | None = None
    # SEC transactions replaced by a later 4/A are kept for audit but excluded from lists and counts.
    superseded_by: str | None = None
    # Field name -> "bpiq" | "sec": which provider supplied each displayed value.
    field_sources: dict[str, str] = {}
    # Fields the provider does not return at all (as opposed to returned-but-empty).
    unavailable_fields: list[str] = []
    match: InsiderMatch | None = None
    sec_source: SourceRef | None = None


class FundHolding(BaseModel):
    fund: str | None
    period_end: date | None
    filing_date: date | None
    shares: float | None
    value_usd: float | None
    change_shares: float | None
    change_basis: str | None
    mapping_verified: bool
    source: SourceRef
