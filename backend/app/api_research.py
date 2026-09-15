"""HTTP routes for research features. No authentication by design (localhost only). No order endpoints exist."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.domain.research import JournalEntry, MonitoringSettings, Trade, TradeKind, TradePlanInput, TradeStatus
from app.fixtures.transport import DemoScenario
from app.logging_setup import get_logger
from app.providers.bpiq_mcp.oauth import McpAuthError
from app.providers.errors import ProviderError
from app.research.critique import AiUnavailable, OpenAICritic
from app.research.monitor import Monitor
from app.research.service import ResearchService

log = get_logger("api.research")

TICKER = r"^[A-Za-z.]{1,10}$"


class WatchRequest(BaseModel):
    ticker: str = Field(pattern=TICKER)
    note: str | None = Field(None, max_length=2000)
    name: str | None = Field(None, max_length=200)


class RefreshRequest(BaseModel):
    tickers: list[str] | None = None
    demo_scenario: DemoScenario | None = None


class ReadRequest(BaseModel):
    ids: list[str] | None = None


class AnalysisRequest(BaseModel):
    note: str | None = Field(None, max_length=4000)
    demo_scenario: DemoScenario | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    demo_scenario: DemoScenario | None = None


class CritiqueRequest(BaseModel):
    invalidation: str | None = Field(None, max_length=2000)
    demo_scenario: DemoScenario | None = None


class PlanRequest(BaseModel):
    plan: TradePlanInput
    demo_scenario: DemoScenario | None = None


class TradeRequest(BaseModel):
    kind: Literal["planned", "hypothetical", "actual"]
    ticker: str = Field(pattern=TICKER)
    status: Literal["planned", "open", "closed", "cancelled"] = "planned"
    plan: TradePlanInput | None = None
    catalyst_event_id: str | None = None
    catalyst_category: str | None = Field(None, max_length=100)
    entry_date: date | None = None
    entry_price: float | None = Field(None, gt=0)
    planned_exit_date: date | None = None
    exit_date: date | None = None
    exit_price: float | None = Field(None, gt=0)
    shares: float | None = Field(None, gt=0)
    cost_pct_per_side: float = Field(0.0, ge=0, le=10)
    slippage_pct_per_side: float = Field(0.0, ge=0, le=10)
    thesis: str = Field("", max_length=4000)
    invalidation: str = Field("", max_length=4000)
    notes: str = Field("", max_length=4000)


class JournalRequest(BaseModel):
    ticker: str = Field(pattern=TICKER)
    decision: Literal["enter", "skip", "exit", "note"]
    trade_id: str | None = None
    thesis: str = Field("", max_length=4000)
    expected_catalyst: str = Field("", max_length=1000)
    reasons: str = Field("", max_length=4000)
    result_note: str = Field("", max_length=4000)


class McpToolTest(BaseModel):
    name: str = Field(max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpMapping(BaseModel):
    financials: dict[str, Any] | None = None
    insiders: dict[str, Any] | None = None
    funds: dict[str, Any] | None = None


@dataclass
class RefreshJob:
    id: str
    status: Literal["running", "completed", "failed"] = "running"
    message: str = "Starting"
    report: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "report": self.report,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
        }


def _validate_trade_request(req: TradeRequest) -> None:
    if req.kind == "planned" and req.status != "planned":
        raise HTTPException(422, "Planned trades are research plans; record a position as 'actual' or 'hypothetical'.")
    if req.kind in ("hypothetical", "actual") and req.status == "planned":
        raise HTTPException(422, "Hypothetical and actual trades must be open, closed, or cancelled.")
    if req.status in ("open", "closed") and (req.entry_date is None or req.entry_price is None):
        raise HTTPException(422, "Open and closed trades need an entry date and entry price.")
    if req.status == "closed" and (req.exit_date is None or req.exit_price is None):
        raise HTTPException(422, "Closed trades need an exit date and exit price.")
    if req.exit_date and req.entry_date and req.exit_date < req.entry_date:
        raise HTTPException(422, "Exit date cannot be before the entry date.")


def build_research_router(
    *, settings: Settings, service: ResearchService, monitor: Monitor, critic: OpenAICritic
) -> APIRouter:
    router = APIRouter(prefix="/api")
    refresh_jobs: dict[str, RefreshJob] = {}

    def bad_request(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    # ------------------------------------------------------------ watchlist

    @router.get("/watchlist")
    def watchlist() -> dict[str, Any]:
        return {"mode": service.mode.value, "items": service.watchlist(), "monitoring": monitor.status()}

    @router.post("/watchlist", status_code=201)
    def add_watch(req: WatchRequest) -> dict[str, Any]:
        return service.add_watch(req.ticker, req.note, req.name).model_dump(mode="json")

    @router.delete("/watchlist/{ticker}")
    def remove_watch(ticker: str) -> dict[str, Any]:
        if not service.remove_watch(ticker):
            raise HTTPException(404, "Ticker is not on the watchlist.")
        return {"removed": ticker.upper(), "note": "Stored catalysts and revision history are kept."}

    @router.post("/watchlist/refresh", status_code=202)
    async def refresh(req: RefreshRequest) -> dict[str, Any]:
        if service.mode.value == "live" and req.demo_scenario is not None:
            raise HTTPException(400, "Demo scenarios are only available in demo mode.")
        if service.refresh_lock.locked() or any(j.status == "running" for j in refresh_jobs.values()):
            raise HTTPException(409, "A watchlist refresh is already running.")
        job = RefreshJob(id=uuid.uuid4().hex)
        refresh_jobs[job.id] = job

        def progress(message: str) -> None:
            job.message = message

        async def execute() -> None:
            try:
                job.report = await service.refresh_watchlist(req.tickers, scenario=req.demo_scenario, progress=progress)
                job.status = "completed"
                job.message = "Refresh finished"
            except Exception:
                log.exception("watchlist refresh job %s failed", job.id[:8])
                job.status = "failed"
                job.error = "The refresh failed unexpectedly. Previously stored data is unchanged."

        job.task = asyncio.create_task(execute())
        for old in sorted((j for j in refresh_jobs.values() if j.status != "running"), key=lambda j: j.started_at)[:-10]:
            refresh_jobs.pop(old.id, None)
        return job.public()

    @router.get("/watchlist/refresh/{job_id}")
    def refresh_job(job_id: str) -> dict[str, Any]:
        job = refresh_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown refresh job.")
        return job.public()

    # ------------------------------------------------------------ notifications and monitoring

    @router.get("/notifications")
    def notifications(unread_only: bool = False, limit: int = 100) -> dict[str, Any]:
        items = service.repo.list_notifications(service.mode, limit=min(max(limit, 1), 500), unread_only=unread_only)
        return {"items": [n.model_dump(mode="json") for n in items], "unread": service.repo.unread_count(service.mode)}

    @router.post("/notifications/read")
    def mark_read(req: ReadRequest) -> dict[str, Any]:
        service.repo.mark_read(service.mode, req.ids)
        return {"unread": service.repo.unread_count(service.mode)}

    @router.get("/monitoring")
    def monitoring() -> dict[str, Any]:
        return {"settings": service.monitoring_settings().model_dump(mode="json"), "status": monitor.status()}

    @router.put("/monitoring/settings")
    def save_monitoring(value: MonitoringSettings) -> dict[str, Any]:
        service.repo.save_monitoring_settings(service.mode, value)
        return {"settings": value.model_dump(mode="json"), "status": monitor.status()}

    # ------------------------------------------------------------ company research

    @router.get("/companies/{ticker}/research")
    async def research(ticker: str, demo_scenario: DemoScenario | None = None) -> dict[str, Any]:
        try:
            return await service.company_research(ticker, scenario=demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None

    @router.post("/companies/{ticker}/analyses", status_code=201)
    async def save_analysis(ticker: str, req: AnalysisRequest) -> dict[str, Any]:
        try:
            data = await service.company_research(ticker, scenario=req.demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None
        return service.save_analysis(data, req.note)

    @router.get("/companies/{ticker}/analyses")
    def analyses(ticker: str) -> list[dict[str, Any]]:
        return service.repo.latest_analyses(service.mode, ticker.upper(), 20)

    @router.post("/companies/{ticker}/critique")
    async def critique(ticker: str, req: CritiqueRequest) -> dict[str, Any]:
        try:
            data = await service.company_research(ticker, scenario=req.demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None
        from app.research.critique import build_evidence

        evidence = build_evidence(data, req.invalidation)
        try:
            result = await critic.critique(ticker=ticker.upper(), evidence=evidence)
        except AiUnavailable as exc:
            return {"available": False, "reason": str(exc), "evidence": evidence}
        except ProviderError as exc:
            return {"available": False, "reason": f"AI generation failed: {exc.message}", "evidence": evidence}
        return {"available": True, "evidence": evidence, **result}

    # ------------------------------------------------------------ plans, trades, journal, performance

    @router.post("/companies/{ticker}/ask")
    async def ask(ticker: str, req: AskRequest) -> dict[str, Any]:
        try:
            data = await service.company_research(ticker, scenario=req.demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None
        from app.research.critique import build_ask_context, build_evidence

        context = build_ask_context(data, build_evidence(data))
        try:
            result = await critic.ask(ticker=ticker.upper(), question=req.question.strip(), context=context)
        except AiUnavailable as exc:
            return {"available": False, "reason": str(exc)}
        except ProviderError as exc:
            return {"available": False, "reason": f"AI answer failed: {exc.message}"}
        return {"available": True, **result}

    @router.post("/trade-plans/calculate")
    async def calculate(req: PlanRequest) -> dict[str, Any]:
        try:
            return await service.plan(req.plan, scenario=req.demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None

    @router.get("/trades")
    def trades(kind: TradeKind | None = None) -> list[dict[str, Any]]:
        return [t.model_dump(mode="json") for t in service.repo.list_trades(service.mode, kind)]

    @router.post("/trades", status_code=201)
    def create_trade(req: TradeRequest) -> dict[str, Any]:
        _validate_trade_request(req)
        now = datetime.now(UTC)
        trade = Trade(
            id=uuid.uuid4().hex,
            mode=service.mode,
            kind=TradeKind(req.kind),
            status=TradeStatus(req.status),
            created_at=now,
            updated_at=now,
            **req.model_dump(exclude={"kind", "status"}),
        )
        trade.ticker = trade.ticker.upper()
        return service.save_trade(trade).model_dump(mode="json")

    @router.put("/trades/{trade_id}")
    def update_trade(trade_id: str, req: TradeRequest) -> dict[str, Any]:
        existing = service.repo.get_trade(service.mode, trade_id)
        if existing is None:
            raise HTTPException(404, "Trade not found for the current data mode.")
        if existing.kind is TradeKind.PAPER:
            raise HTTPException(400, "Automated paper trades follow fixed rules and cannot be edited.")
        if existing.kind.value != req.kind:
            raise HTTPException(400, "A trade's kind cannot be changed. Create a new record instead.")
        _validate_trade_request(req)
        updated = existing.model_copy(
            update={**req.model_dump(exclude={"kind", "status"}), "status": TradeStatus(req.status), "updated_at": datetime.now(UTC), "ticker": req.ticker.upper()}
        )
        return service.save_trade(updated).model_dump(mode="json")

    @router.delete("/trades/{trade_id}")
    def delete_trade(trade_id: str) -> dict[str, Any]:
        existing = service.repo.get_trade(service.mode, trade_id)
        if existing is None:
            raise HTTPException(404, "Trade not found for the current data mode.")
        if existing.kind is TradeKind.PAPER:
            raise HTTPException(400, "Paper trades are kept for forward-tracking integrity.")
        service.repo.delete_trade(service.mode, trade_id)
        return {"deleted": trade_id}

    @router.get("/journal")
    def journal(ticker: str | None = None) -> list[dict[str, Any]]:
        return [j.model_dump(mode="json") for j in service.repo.list_journal(service.mode, ticker.upper() if ticker else None)]

    @router.post("/journal", status_code=201)
    def add_journal(req: JournalRequest) -> dict[str, Any]:
        entry = JournalEntry(id=uuid.uuid4().hex, mode=service.mode, created_at=datetime.now(UTC), **req.model_dump())
        entry.ticker = entry.ticker.upper()
        return service.add_journal(entry).model_dump(mode="json")

    @router.delete("/journal/{entry_id}")
    def delete_journal(entry_id: str) -> dict[str, Any]:
        if not service.repo.delete_journal(service.mode, entry_id):
            raise HTTPException(404, "Journal entry not found.")
        return {"deleted": entry_id}

    @router.get("/performance")
    async def performance(demo_scenario: DemoScenario | None = None) -> dict[str, Any]:
        try:
            return await service.performance(scenario=demo_scenario)
        except ValueError as exc:
            raise bad_request(exc) from None

    # ------------------------------------------------------------ integrations

    @router.get("/integrations/status")
    def integrations() -> dict[str, Any]:
        mcp_status = service.mcp.status()
        if service.mode.value == "demo":
            mcp_status["demo_note"] = "Demo mode does not call BPIQ MCP."
        return {
            "mode": service.mode.value,
            "bpiq_rest": {"configured": settings.bpiq_configured, "access_tier": settings.bpiq_access_tier.value},
            "alpaca": {"configured": settings.alpaca_configured, "plan": "Basic (free) assumed; feed=sip historical bars ≥15 min old only"},
            "bpiq_mcp": mcp_status,
            "ai": critic.status(),
            "monitoring": monitor.status(),
            "orders": "This app has no order-placement code or endpoints.",
        }

    @router.post("/integrations/bpiq-mcp/connect")
    async def mcp_connect() -> dict[str, Any]:
        if service.mcp.auth_method() == "api_key":
            raise HTTPException(400, "This app uses BPIQ_API_KEY for MCP. Click Discover tools; OAuth Connect is not required.")
        if not service.mcp.oauth:
            raise HTTPException(400, "Set BPIQ_MCP_URL in .env and restart the backend first.")
        try:
            url = await service.mcp.oauth.start(f"{settings.public_base_url.rstrip('/')}/api/integrations/bpiq-mcp/callback")
        except McpAuthError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"authorize_url": url}

    @router.get("/integrations/bpiq-mcp/callback")
    async def mcp_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
        target = f"{settings.ui_base_url.rstrip('/')}/#/integrations"
        if error or not code or not state or not service.mcp.oauth:
            return RedirectResponse(f"{target}?{urlencode({'mcp': 'error', 'message': (error or 'Authorization was not completed.')[:200]})}")
        try:
            await service.mcp.oauth.complete(code, state)
        except McpAuthError as exc:
            return RedirectResponse(f"{target}?{urlencode({'mcp': 'error', 'message': str(exc)[:200]})}")
        return RedirectResponse(f"{target}?mcp=connected")

    @router.post("/integrations/bpiq-mcp/disconnect")
    def mcp_disconnect() -> dict[str, Any]:
        if service.mcp.oauth:
            service.mcp.oauth.disconnect()
        return service.mcp.status()

    @router.post("/integrations/bpiq-mcp/discover")
    async def mcp_discover() -> dict[str, Any]:
        try:
            await service.mcp.discover()
        except McpAuthError as exc:
            raise HTTPException(400, str(exc)) from None
        except ProviderError as exc:
            raise HTTPException(502, exc.message) from None
        return service.mcp.status()

    @router.post("/integrations/bpiq-mcp/test-tool")
    async def mcp_test(req: McpToolTest) -> dict[str, Any]:
        try:
            return await service.mcp.call_raw(req.name, req.arguments)
        except (McpAuthError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        except ProviderError as exc:
            raise HTTPException(502, exc.message) from None

    @router.put("/integrations/bpiq-mcp/capabilities")
    def mcp_mapping(req: McpMapping) -> dict[str, Any]:
        try:
            service.mcp.save_mapping(req.model_dump())
        except ValueError as exc:
            raise bad_request(exc) from None
        return service.mcp.status()

    return router
