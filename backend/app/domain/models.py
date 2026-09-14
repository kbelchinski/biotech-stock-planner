"""Normalized application models used by calculations, persistence, and the UI.

Provider payloads never reach this layer directly; see app.providers.*.normalize.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DataMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class SourceRef(BaseModel):
    provider: str
    endpoint: str | None = None
    retrieved_at: datetime | None = None
    # As supplied or derived from the provider (e.g. a bar's session date); None when not provided.
    source_timestamp: str | None = None
    timestamp_note: str | None = None
    url: str | None = None
    is_mock: bool = False


# ---------------------------------------------------------------- catalysts


class CatalystType(StrEnum):
    PHASE2_RESULTS = "phase2_results"
    PHASE3_RESULTS = "phase3_results"
    PDUFA = "pdufa"


CATALYST_TYPE_LABELS: dict[CatalystType, str] = {
    CatalystType.PHASE2_RESULTS: "Phase 2 results",
    CatalystType.PHASE3_RESULTS: "Phase 3 results",
    CatalystType.PDUFA: "PDUFA decision",
}


class ClassificationStatus(StrEnum):
    MATCHED = "matched"
    NOT_QUALIFYING = "not_qualifying"
    UNRECOGNIZED = "unrecognized"


class Catalyst(BaseModel):
    event_id: str
    provider_record_id: int
    ticker: str
    company_name: str | None
    drug_name: str | None
    indications: list[str] = Field(default_factory=list)
    stage_label: str | None
    event_label: str | None
    stage_event_label: str | None
    catalyst_type: CatalystType | None
    classification_status: ClassificationStatus
    classification_reason: str
    catalyst_date: date | None
    note: str | None
    source_url: str | None
    source: SourceRef


class CompanyProfile(BaseModel):
    ticker: str
    name: str | None
    market_cap_usd: float | None
    provider_last_price: str | None
    source: SourceRef


# ---------------------------------------------------------------- market data


class DailyBar(BaseModel):
    session_date: date
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None
    vwap: float | None


class PriceHistory(BaseModel):
    ticker: str
    feed: str
    adjustment: str
    requested_start: date
    requested_end: date
    bars: list[DailyBar]
    source: SourceRef


class ListingInfo(BaseModel):
    ticker: str
    exchange: str
    status: str
    asset_class: str
    tradable: bool
    name: str | None
    source: SourceRef


class FinancialSnapshot(BaseModel):
    ticker: str
    cash_usd: float | None
    # Positive = cash consumed per month; zero or negative = not burning cash.
    monthly_net_burn_usd: float | None
    as_of: date | None
    source: SourceRef


# ---------------------------------------------------------------- screening


class CriterionKey(StrEnum):
    CATALYST = "catalyst"
    MARKET_CAP = "market_cap"
    PRICE = "price"
    RUNWAY = "runway"
    LIQUIDITY = "liquidity"
    LISTING = "listing"


class CriterionStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLIED = "not_applied"


class Eligibility(StrEnum):
    QUALIFIES = "qualifies"
    DOES_NOT_QUALIFY = "does_not_qualify"
    INSUFFICIENT_DATA = "insufficient_data"


class CriterionResult(BaseModel):
    key: CriterionKey
    label: str
    status: CriterionStatus
    observed: str | None
    threshold: str
    explanation: str
    sources: list[SourceRef] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class CatalystEvaluation(BaseModel):
    catalyst: Catalyst
    days_until: int | None
    type_status: CriterionStatus
    timing_status: CriterionStatus
    status: CriterionStatus


class WeeklyTurnover(BaseModel):
    week_start: date
    week_end: date
    sessions: list[date]
    sessions_with_bars: int
    total_usd: float
    complete: bool
    approximated_sessions: int


class PriceBarPoint(BaseModel):
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class CompanyResult(BaseModel):
    ticker: str
    name: str | None
    eligibility: Eligibility
    criteria: list[CriterionResult]
    catalysts: list[CatalystEvaluation]
    primary_catalyst_event_id: str | None
    market_cap_usd: float | None
    price: float | None
    price_date: date | None
    price_bars: list[PriceBarPoint] = Field(default_factory=list)
    runway_months: float | None
    runway_is_mock: bool
    avg_weekly_turnover_usd: float | None
    turnover_is_lower_bound: bool
    turnover_method: str | None
    exchange: str | None
    issues: list[str] = Field(default_factory=list)


class ScanCriteria(BaseModel):
    catalyst_min_days: int = Field(60, ge=0, le=730)
    catalyst_max_days: int = Field(90, ge=0, le=730)
    catalyst_types: list[CatalystType] = Field(default_factory=lambda: list(CatalystType))

    market_cap_enabled: bool = True
    market_cap_min_usd: float = Field(50_000_000, ge=0)
    market_cap_max_usd: float = Field(2_000_000_000, ge=0)

    price_enabled: bool = True
    price_above_usd: float = Field(1.0, ge=0)

    runway_enabled: bool = False
    runway_min_months: float = Field(12, ge=0)

    liquidity_enabled: bool = True
    liquidity_min_avg_weekly_usd: float = Field(2_000_000, ge=0)

    listing_enabled: bool = True

    # Query catalysts from today through the window end, so near-term events are shown as
    # timing failures instead of being invisible. When false, only the selected window is fetched.
    include_near_term_catalysts: bool = True
    # Send market_cap_min/max to BPIQ. Excluded companies then never appear as market-cap failures.
    provider_market_cap_prefilter: bool = False

    @model_validator(mode="after")
    def _check_ranges(self) -> ScanCriteria:
        if self.catalyst_min_days > self.catalyst_max_days:
            raise ValueError("catalyst_min_days must be <= catalyst_max_days")
        if self.market_cap_min_usd > self.market_cap_max_usd:
            raise ValueError("market_cap_min_usd must be <= market_cap_max_usd")
        if not self.catalyst_types:
            raise ValueError("select at least one catalyst type")
        self.catalyst_types = sorted(set(self.catalyst_types), key=list(CatalystType).index)
        return self


LIQUIDITY_WEEKS = 4
# Calendar-day lookback for the drawer chart (5D / 1M / 3M / 6M / YTD / 1Y). Liquidity still
# uses only LIQUIDITY_WEEKS; extra history is unused by the screen.
PRICE_CHART_LOOKBACK_DAYS = 400


class ScanOutcome(StrEnum):
    SUCCESS = "success"
    NO_MATCHES = "no_matches"
    INCOMPLETE = "incomplete"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SUBSCRIPTION_LIMITATION = "subscription_limitation"
    CONFIGURATION_ERROR = "configuration_error"


class ScanIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    provider: str | None
    kind: str
    message: str
    hint: str | None = None
    tickers: list[str] = Field(default_factory=list)


class ScanSummary(BaseModel):
    evaluated: int = 0
    qualifying: int = 0
    failed: int = 0
    insufficient_data: int = 0
    catalyst_records: int = 0
    undated_excluded: int = 0
    rejected_records: int = 0
    duplicate_records: int = 0


class ScanRun(BaseModel):
    id: str
    mode: DataMode
    outcome: ScanOutcome
    started_at: datetime
    finished_at: datetime
    scan_date: date
    latest_completed_session: date | None
    criteria: ScanCriteria
    summary: ScanSummary
    results: list[CompanyResult]
    issues: list[ScanIssue] = Field(default_factory=list)
    not_applied_criteria: list[CriterionKey] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    demo_scenario: str | None = None
