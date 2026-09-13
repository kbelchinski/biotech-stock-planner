"""External response schemas for BPIQ REST API v1, as documented at
https://app.bpiq.com/api-documentation (reviewed 2026-09-13).

These mirror the provider payload exactly: field names, nesting, and JSON types.
Values are not interpreted here; see normalize.py.

Documented envelope (list endpoints): {count, next, previous, results}.
`extra="allow"` keeps undocumented fields rather than rejecting the record.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr


class _BpiqModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class BpiqCompany(_BpiqModel):
    # Catalysts schema documents `company` as an object; the sample shows these keys.
    ticker: StrictStr | None = None
    name: StrictStr | None = None
    # Documented as a string ("14.49").
    last_price: StrictStr | None = None
    # Documented as integer in the sample; `number | null` on /all-companies/. Units not stated.
    market_cap: StrictInt | StrictFloat | None = None


class BpiqIndication(_BpiqModel):
    id: StrictInt | None = None
    title: StrictStr | None = None
    nickname: StrictStr | None = None


class BpiqStageEvent(_BpiqModel):
    id: StrictInt | None = None
    label: StrictStr | None = None
    stage_label: StrictStr | None = None
    event_label: StrictStr | None = None


class BpiqCatalystRecord(_BpiqModel):
    """One element of `results[]` from GET /api/v1/info/catalysts/."""

    id: StrictInt
    company: BpiqCompany | None = None
    ticker: StrictStr | None = None
    drug_name: StrictStr | None = None
    indications: list[BpiqIndication] | None = None
    indications_text: StrictStr | None = None
    stage_event: BpiqStageEvent | None = None
    note: StrictStr | None = None
    # `string | null`. Format not stated for catalysts ("ISO date" on /drugs/).
    catalyst_date: StrictStr | None = None
    has_catalyst: StrictBool | None = None
    catalyst_source: StrictStr | None = None
    is_big_mover: StrictBool | None = None
    is_suspected_mover: StrictBool | None = None
    is_hedge_fund_pick: StrictBool | None = None
    is_hedge_fund_avoid: StrictBool | None = None
    is_high_mgmt_interest: StrictBool | None = None


class BpiqPaginatedEnvelope(_BpiqModel):
    count: StrictInt
    next: StrictStr | None
    previous: StrictStr | None
    # Records are validated individually so one malformed record does not discard a page.
    results: list[dict[str, Any]]
